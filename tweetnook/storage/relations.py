"""Tweet ID and relation lookups used by thread expansion and quote restoring."""

from __future__ import annotations

from typing import Any

from .db import ConnectionMixin


class RelationsMixin(ConnectionMixin):
    def list_membership_tweet_ids(self, *, limit: int | None = None) -> list[str]:
        rows = self._query(expr="record_type = 'tweet'", cols=["tweet_id", "added_at"])
        rows.sort(key=lambda row: (row.get("added_at") or "", row.get("tweet_id") or ""))
        tweet_ids = [row["tweet_id"] for row in rows if row.get("tweet_id")]
        unique = list(dict.fromkeys(tweet_ids))
        return unique[:limit] if limit is not None else unique

    def list_known_tweet_ids(self) -> set[str]:
        tweet_rows = self._query(expr="record_type = 'tweet'", cols=["tweet_id"])
        tweet_object_rows = self._query(expr="record_type = 'tweet_object'", cols=["tweet_id"])
        tweet_ids = {
            row["tweet_id"]
            for row in tweet_rows + tweet_object_rows
            if isinstance(row.get("tweet_id"), str) and row["tweet_id"]
        }
        return tweet_ids

    def list_expanded_thread_target_ids(self, *, limit: int | None = None) -> list[str]:
        """Return targets whose latest expansion marker does not predate recovery."""

        rows = self.conn.execute(
            """
            WITH latest_expansions AS (
                SELECT cursor_in AS tweet_id, MAX(captured_at) AS captured_at
                FROM archive INDEXED BY idx_archive_capture_target
                WHERE record_type = 'raw_capture'
                  AND operation = 'ThreadExpandDetail'
                  AND cursor_in IS NOT NULL AND cursor_in != ''
                GROUP BY cursor_in
            )
            SELECT expansion.tweet_id, expansion.captured_at
            FROM latest_expansions AS expansion
            LEFT JOIN archive AS object INDEXED BY idx_archive_tweet_id
              ON object.record_type = 'tweet_object'
             AND object.tweet_id = expansion.tweet_id
            WHERE object.enrichment_state IS NULL
               OR object.enrichment_state != 'resurrected'
               OR object.enrichment_checked_at IS NULL
               OR expansion.captured_at >= object.enrichment_checked_at
            ORDER BY expansion.captured_at, expansion.tweet_id
            """
        ).fetchall()
        targets = [str(row[0]) for row in rows]
        return targets[:limit] if limit is not None else targets

    def list_url_ref_rows(self) -> list[dict[str, Any]]:
        rows = self._query(
            expr="record_type = 'url_ref'",
            cols=["tweet_id", "position", "canonical_url", "expanded_url", "url"],
        )
        rows.sort(
            key=lambda row: (
                row.get("tweet_id") or "",
                row.get("position") if row.get("position") is not None else 1_000_000,
            )
        )
        return rows

    def list_quote_relation_rows(self, source_tweet_ids: set[str]) -> list[dict[str, Any]]:
        if not source_tweet_ids:
            return []
        rows: list[dict[str, Any]] = []
        source_ids = sorted(source_tweet_ids)
        chunk_size = 500
        for start in range(0, len(source_ids), chunk_size):
            chunk = source_ids[start : start + chunk_size]
            placeholders = ", ".join("?" for _ in chunk)
            query = f"""
                SELECT tweet_id, target_tweet_id
                FROM archive INDEXED BY idx_archive_tweet_id
                WHERE record_type = 'tweet_relation'
                  AND relation_type = 'quote_of'
                  AND tweet_id IN ({placeholders})
                ORDER BY tweet_id, target_tweet_id
            """
            rows.extend(dict(row) for row in self.conn.execute(query, chunk).fetchall())
        return rows

    def list_saved_conversation_tweet_ids(self) -> tuple[set[str], set[str]]:
        """Split archived tweets in saved conversations into self-thread and reply ids.

        Returns ``(self_thread_ids, reply_ids)``: archived tweet objects sharing a
        conversation with a saved tweet, split by whether they are authored by a saved
        tweet's author in that conversation. Saved tweets themselves are included and are
        left to the caller to exclude. A saved tweet's own object supplies its
        conversation and author, falling back to the membership row.

        One pass over tweet objects; the IN subqueries are materialized once as
        ephemeral indexes, so the cost stays linear on large archives.
        """
        rows = self.conn.execute(
            """
            WITH saved AS MATERIALIZED (
                SELECT DISTINCT
                    COALESCE(NULLIF(object.conversation_id, ''), membership.conversation_id)
                        AS conversation_id,
                    COALESCE(NULLIF(object.author_id, ''), membership.author_id) AS author_id
                FROM archive AS membership INDEXED BY idx_archive_membership
                LEFT JOIN archive AS object INDEXED BY idx_archive_tweet_id
                  ON object.tweet_id = membership.tweet_id
                 AND object.record_type = 'tweet_object'
                WHERE membership.record_type = 'tweet'
            )
            SELECT peer.tweet_id,
                   MAX(
                       (peer.conversation_id, peer.author_id)
                       IN (SELECT conversation_id, author_id FROM saved)
                   ) AS is_self_thread
            FROM archive AS peer
            WHERE peer.record_type = 'tweet_object'
              AND peer.tweet_id IS NOT NULL AND peer.tweet_id != ''
              AND peer.conversation_id IN (
                  SELECT conversation_id FROM saved
                  WHERE conversation_id IS NOT NULL AND conversation_id != ''
              )
            GROUP BY peer.tweet_id
            """
        ).fetchall()
        self_thread_ids = {str(row[0]) for row in rows if row[1]}
        reply_ids = {str(row[0]) for row in rows if not row[1]}
        return self_thread_ids, reply_ids
