"""Indexed conversation traversal, thread observations and reverse quote pages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .common import clause_in
from .db import ConnectionMixin
from .record_types import RecordType


@dataclass(slots=True)
class ThreadObservations:
    objects: list[dict[str, Any]]
    media: list[dict[str, Any]]
    memberships: list[dict[str, Any]]
    tags: list[dict[str, Any]]


class ConversationsMixin(ConnectionMixin):
    def reply_subtree(
        self,
        tweet_id: str,
        *,
        limit: int = 250,
    ) -> tuple[list[dict], bool]:
        """Load a bounded reply subtree with indexed, globally deduplicated frontiers."""
        query_limit = limit + 1
        seen = {tweet_id}
        frontier = [tweet_id]
        normalized: list[dict] = []
        depth = 1

        while frontier and len(normalized) < query_limit:
            placeholders = ", ".join("?" for _ in frontier)
            # A grouped child can appear only once per directional lookup. Allow room for
            # every already-seen ID so cycles cannot crowd unseen children out of the cap.
            candidate_limit = query_limit + len(seen)
            rows = self.conn.execute(
                f"""
                /* tweetnook_reply_frontier */
                SELECT child_id, MIN(parent_id) AS parent_id
                FROM (
                    SELECT relation.tweet_id AS child_id,
                           relation.target_tweet_id AS parent_id
                    FROM archive AS relation INDEXED BY idx_archive_target_tweet_id
                    WHERE relation.record_type = '{RecordType.TWEET_RELATION}'
                      AND relation.relation_type IN ('reply_to', 'thread_parent')
                      AND relation.target_tweet_id IN ({placeholders})
                    UNION ALL
                    SELECT relation.target_tweet_id AS child_id,
                           relation.tweet_id AS parent_id
                    FROM archive AS relation INDEXED BY idx_archive_tweet_record
                    WHERE relation.record_type = '{RecordType.TWEET_RELATION}'
                      AND relation.relation_type = 'thread_child'
                      AND relation.tweet_id IN ({placeholders})
                )
                WHERE child_id IS NOT NULL AND parent_id IS NOT NULL
                GROUP BY child_id
                ORDER BY child_id
                LIMIT ?
                """,
                (*frontier, *frontier, candidate_limit),
            ).fetchall()

            next_frontier = []
            for row in rows:
                child_id = str(row[0])
                parent_id = str(row[1])
                if child_id in seen:
                    continue
                seen.add(child_id)
                next_frontier.append(child_id)
                normalized.append(
                    {
                        "tweet_id": child_id,
                        "target_tweet_id": parent_id,
                        "relation_type": "reply_to",
                        "depth": depth,
                    }
                )
                if len(normalized) >= query_limit:
                    break

            frontier = next_frontier
            depth += 1

        return normalized[:limit], len(normalized) > limit

    def ancestor_chain(self, tweet_id: str) -> list[str]:
        """Resolve one chain in directional lookup order, excluding cycles and the focal ID.

        One recursive statement walks up to 50 parents; each step prefers the tweet's own
        reply_to/thread_parent relation, then a thread_child relation pointing at it.
        """
        relation = f"'{RecordType.TWEET_RELATION}'"

        def parent_of(child: str) -> str:
            return f"""COALESCE(
                (SELECT relation.target_tweet_id
                 FROM archive AS relation INDEXED BY idx_archive_tweet_record
                 WHERE relation.record_type = {relation} AND relation.tweet_id = {child}
                   AND relation.relation_type IN ('reply_to', 'thread_parent')
                   AND COALESCE(relation.target_tweet_id, '') != '' LIMIT 1),
                (SELECT relation.tweet_id
                 FROM archive AS relation INDEXED BY idx_archive_target_tweet_id
                 WHERE relation.record_type = {relation} AND relation.target_tweet_id = {child}
                   AND relation.relation_type = 'thread_child'
                   AND COALESCE(relation.tweet_id, '') != '' LIMIT 1))"""

        rows = self.conn.execute(
            f"""
            WITH RECURSIVE chain(id, depth, path, parent) AS (
                SELECT ?1, 0, ',' || ?1 || ',', {parent_of("?1")}
                UNION ALL
                SELECT chain.parent, chain.depth + 1, chain.path || chain.parent || ',',
                       {parent_of("chain.parent")}
                FROM chain
                WHERE chain.parent IS NOT NULL AND chain.depth < 50
                  AND instr(chain.path, ',' || chain.parent || ',') = 0
            )
            SELECT id FROM chain WHERE depth > 0 ORDER BY depth
            """,
            (tweet_id,),
        ).fetchall()
        return [str(row[0]) for row in rows]

    def thread_observations(self, related_ids: set[str]) -> ThreadObservations:
        related = clause_in("tweet_id", related_ids)
        related_count = len(related_ids)
        objs = self._query(
            f"record_type = '{RecordType.TWEET_OBJECT}' AND {related.sql}",
            related.params,
            cols=[
                "tweet_id",
                "text",
                "author_id",
                "author_username",
                "author_display_name",
                "created_at",
                "synced_at",
                "raw_json",
            ],
            limit=related_count,
            indexed_by="idx_archive_tweet_record",
        )
        media = self._query(
            f"record_type = '{RecordType.MEDIA}' AND {related.sql}",
            related.params,
            cols=[
                "source",
                "tweet_id",
                "position",
                "media_type",
                "media_url",
                "thumbnail_url",
                "width",
                "height",
                "duration_millis",
                "local_path",
                "thumbnail_local_path",
            ],
            # No row cap: a post can carry up to twenty media items.
            indexed_by="idx_archive_tweet_record",
        )
        col_rows = self._query(
            f"record_type = '{RecordType.TWEET}' AND {related.sql}",
            related.params,
            cols=[
                "tweet_id",
                "collection_type",
                "text",
                "author_id",
                "author_username",
                "author_display_name",
                "created_at",
                "synced_at",
                "row_key",
            ],
            limit=related_count * 3,
            indexed_by="idx_archive_tweet_record",
        )
        tag_rows = self._query(
            f"record_type = '{RecordType.MEDIA_TAG}' AND {related.sql}",
            related.params,
            cols=["tweet_id", "raw_json"],
            limit=related_count,
            indexed_by="idx_archive_tweet_record",
        )

        # A membership's payload is shown only for a post without a stored object, so the
        # large raw_json column is read for those memberships alone.
        object_ids = {row.get("tweet_id") for row in objs}
        needed = [row["row_key"] for row in col_rows if row.get("tweet_id") not in object_ids]
        payloads: dict[str, Any] = {}
        for start in range(0, len(needed), 200):
            keys = clause_in("row_key", set(needed[start : start + 200]))
            for row in self._query(keys.sql, keys.params, cols=["row_key", "raw_json"]):
                payloads[row["row_key"]] = row.get("raw_json")
        for row in col_rows:
            row["raw_json"] = payloads.get(row.pop("row_key"))

        return ThreadObservations(objs, media, col_rows, tag_rows)

    def quote_count(self, tweet_id: str) -> int:
        return self.conn.execute(
            f"""
                SELECT COUNT(DISTINCT tweet_id)
                FROM archive INDEXED BY idx_archive_target_tweet_id
                WHERE record_type = '{RecordType.TWEET_RELATION}'
                  AND relation_type = 'quote_of'
                  AND target_tweet_id = ?
                """,
            (tweet_id,),
        ).fetchone()[0]

    def quote_page(self, tweet_id: str, *, offset: int, limit: int) -> list[str]:
        rows = self._query(
            f"record_type = '{RecordType.TWEET_RELATION}' AND relation_type = 'quote_of' "
            "AND target_tweet_id = ?",
            (tweet_id,),
            cols=["DISTINCT tweet_id"],
            order_by="tweet_id DESC",
            limit=limit,
            offset=offset,
            indexed_by="idx_archive_target_tweet_id",
        )
        return [row_id for r in rows if (row_id := r.get("tweet_id"))]

    def stored_tweet_json(self, tweet_id: str) -> str | None:
        rows = self._query(
            f"record_type = '{RecordType.TWEET_OBJECT}' AND tweet_id = ?",
            (tweet_id,),
            cols=["raw_json"],
            limit=1,
            indexed_by="idx_archive_tweet_record",
        )
        return rows[0].get("raw_json") if rows else None
