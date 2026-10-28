"""Media tags, global tag edits, hidden tweets and automated-tagging candidates."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from typing import Any

from .db import ConnectionMixin
from .record_types import RecordType

AVAILABLE_ENRICHMENT_STATES = ("done", "resurrected")


class TagsMixin(ConnectionMixin):
    def get_eligible_tagging_candidates(
        self,
        *,
        limit: int = 20,
        exclude_tweet_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return one homogeneous batch of untagged saved or directly quoted tweets.

        Keep candidate discovery streaming.  A materialized CTE for the complete
        eligible population is particularly expensive here: large archives make
        SQLite sort that population into temporary B-trees before it can return
        even a one-tweet batch.  Walking the existing record/page index lets us
        stop as soon as the next homogeneous batch is full and keeps temporary
        storage independent of archive size.
        """
        if limit < 1:
            return []

        state_placeholders = ", ".join("?" for _state in AVAILABLE_ENRICHMENT_STATES)
        valid_tag_sql = f"""
            NOT EXISTS (
                SELECT 1 FROM archive retry
                WHERE retry.row_key = 'tagging_retry:' || {{tweet_id}}
                  AND CASE WHEN json_valid(retry.value)
                      THEN CAST(json_extract(retry.value, '$.next_retry_at') AS REAL)
                      ELSE 0 END > CAST(strftime('%s', 'now') AS REAL)
            ) AND
            NOT EXISTS (
                SELECT 1
                FROM archive tag INDEXED BY idx_archive_tweet_id
                WHERE tag.tweet_id = {{tweet_id}}
                  AND tag.record_type = '{RecordType.MEDIA_TAG}'
                  AND (tag.enrichment_state = 'done' OR tag.enrichment_state IS NULL)
                  AND json_valid(tag.raw_json)
                  AND json_type(tag.raw_json, '$.tags') = 'array'
                  AND json_array_length(tag.raw_json, '$.tags') > 0
            )
        """
        saved_query = f"""
            SELECT t.tweet_id, t.created_at_ts AS sort_ts
            FROM archive t INDEXED BY idx_archive_record_page
            WHERE t.record_type = '{RecordType.TWEET}'
              AND EXISTS (
                  SELECT 1
                  FROM archive o INDEXED BY idx_archive_tweet_id
                  WHERE o.tweet_id = t.tweet_id
                    AND o.record_type = '{RecordType.TWEET_OBJECT}'
                    AND o.enrichment_state IN ({state_placeholders})
              )
              AND {valid_tag_sql.format(tweet_id="t.tweet_id")}
            ORDER BY t.created_at_ts DESC,
                     CAST(t.sort_index AS INTEGER) DESC,
                     t.tweet_id DESC
        """
        quoted_query = f"""
            SELECT o.tweet_id, o.created_at_ts AS sort_ts
            FROM archive o INDEXED BY idx_archive_record_page
            WHERE o.record_type = '{RecordType.TWEET_OBJECT}'
              AND o.enrichment_state IN ({state_placeholders})
              AND {valid_tag_sql.format(tweet_id="o.tweet_id")}
              AND EXISTS (
                  SELECT 1
                  FROM archive relation INDEXED BY idx_archive_target_tweet_id
                  WHERE relation.target_tweet_id = o.tweet_id
                    AND relation.record_type = '{RecordType.TWEET_RELATION}'
                    AND relation.relation_type = 'quote_of'
                    AND EXISTS (
                        SELECT 1
                        FROM archive saved INDEXED BY idx_archive_tweet_id
                        WHERE saved.tweet_id = relation.tweet_id
                          AND saved.record_type = '{RecordType.TWEET}'
                          AND EXISTS (
                              SELECT 1
                              FROM archive source_object INDEXED BY idx_archive_tweet_id
                              WHERE source_object.tweet_id = saved.tweet_id
                                AND source_object.record_type = '{RecordType.TWEET_OBJECT}'
                                AND source_object.enrichment_state IN ({state_placeholders})
                          )
                    )
              )
            ORDER BY o.created_at_ts DESC,
                     CAST(o.sort_index AS INTEGER) DESC,
                     o.tweet_id DESC
        """
        classification_query = f"""
            SELECT
                (
                    SELECT MIN(relation.target_tweet_id)
                    FROM archive relation INDEXED BY idx_archive_tweet_id
                    WHERE relation.tweet_id = ?
                      AND relation.record_type = '{RecordType.TWEET_RELATION}'
                      AND relation.relation_type = 'quote_of'
                ) AS quoted_tweet_id,
                CASE WHEN
                    EXISTS (
                        SELECT 1
                        FROM archive media INDEXED BY idx_archive_tweet_id
                        WHERE media.tweet_id = ?
                          AND media.record_type = '{RecordType.MEDIA}'
                          AND COALESCE(media.source, '') != 'link_card'
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM archive relation INDEXED BY idx_archive_tweet_id
                        JOIN archive quoted_media INDEXED BY idx_archive_tweet_id
                          ON quoted_media.tweet_id = relation.target_tweet_id
                         AND quoted_media.record_type = '{RecordType.MEDIA}'
                         AND COALESCE(quoted_media.source, '') != 'link_card'
                        WHERE relation.tweet_id = ?
                          AND relation.record_type = '{RecordType.TWEET_RELATION}'
                          AND relation.relation_type = 'quote_of'
                    )
                    THEN 'media' ELSE 'text'
                END AS content_type
        """

        excluded = exclude_tweet_ids or set()
        seen: set[str] = set()
        selected_type: str | None = None
        selected: list[dict[str, Any]] = []

        def ordered_unique_rows(rows: sqlite3.Cursor) -> Iterator[sqlite3.Row]:
            """Restore tweet-ID tie ordering without an SQLite temp sort."""
            group_ts: int | None = None
            group: dict[str, sqlite3.Row] = {}
            for row in rows:
                sort_ts = int(row["sort_ts"] or 0)
                if group_ts is not None and sort_ts != group_ts:
                    for tweet_id in sorted(group, reverse=True):
                        yield group[tweet_id]
                    group.clear()
                group_ts = sort_ts
                tweet_id = str(row["tweet_id"])
                group.setdefault(tweet_id, row)
            for tweet_id in sorted(group, reverse=True):
                yield group[tweet_id]

        def next_row(rows: Iterator[sqlite3.Row]) -> sqlite3.Row | None:
            return next(rows, None)

        def row_key(row: sqlite3.Row) -> tuple[int, str]:
            return int(row["sort_ts"] or 0), str(row["tweet_id"])

        saved_rows = self.conn.execute(saved_query, AVAILABLE_ENRICHMENT_STATES)
        quoted_rows = self.conn.execute(
            quoted_query,
            (*AVAILABLE_ENRICHMENT_STATES, *AVAILABLE_ENRICHMENT_STATES),
        )
        try:
            saved_stream = ordered_unique_rows(saved_rows)
            quoted_stream = ordered_unique_rows(quoted_rows)
            saved_row = next_row(saved_stream)
            quoted_row = next_row(quoted_stream)
            while saved_row is not None or quoted_row is not None:
                if quoted_row is None or (
                    saved_row is not None and row_key(saved_row) >= row_key(quoted_row)
                ):
                    row = saved_row
                    saved_row = next_row(saved_stream)
                else:
                    row = quoted_row
                    quoted_row = next_row(quoted_stream)
                if row is None:
                    continue
                tweet_id = str(row["tweet_id"])
                if tweet_id in seen:
                    continue
                seen.add(tweet_id)
                if tweet_id in excluded:
                    continue
                classification = self.conn.execute(
                    classification_query,
                    (tweet_id, tweet_id, tweet_id),
                ).fetchone()
                if classification is None:
                    continue
                content_type = str(classification["content_type"])
                if selected_type is None:
                    selected_type = content_type
                if content_type != selected_type:
                    continue
                selected.append(
                    {
                        "tweet_id": tweet_id,
                        "quoted_tweet_id": classification["quoted_tweet_id"],
                        "content_type": content_type,
                        "sort_ts": int(row["sort_ts"] or 0),
                    }
                )
                if len(selected) >= limit:
                    break
        finally:
            saved_rows.close()
            quoted_rows.close()
        return selected

    def get_eligible_tweets_for_tagging(self, *, limit: int = 20) -> list[str]:
        """Compatibility wrapper returning IDs from the next homogeneous tag batch."""
        return [row["tweet_id"] for row in self.get_eligible_tagging_candidates(limit=limit)]

    def get_tagging_coverage_counts(self) -> tuple[int, int]:
        """Return eligible and validly tagged tweet counts for coverage reporting."""
        state_placeholders = ", ".join("?" for _state in AVAILABLE_ENRICHMENT_STATES)
        query = f"""
            WITH saved AS (
                SELECT DISTINCT t.tweet_id
                FROM archive t INDEXED BY idx_archive_record_page
                WHERE t.record_type = '{RecordType.TWEET}'
                  AND EXISTS (
                      SELECT 1
                      FROM archive o INDEXED BY idx_archive_tweet_id
                      WHERE o.tweet_id = t.tweet_id
                        AND o.record_type = '{RecordType.TWEET_OBJECT}'
                        AND o.enrichment_state IN ({state_placeholders})
                  )
            ),
            quoted AS (
                SELECT DISTINCT relation.target_tweet_id AS tweet_id
                FROM saved
                CROSS JOIN archive relation INDEXED BY idx_archive_tweet_id
                WHERE relation.tweet_id = saved.tweet_id
                  AND relation.record_type = '{RecordType.TWEET_RELATION}'
                  AND relation.relation_type = 'quote_of'
                  AND EXISTS (
                      SELECT 1
                      FROM archive quoted_object INDEXED BY idx_archive_tweet_id
                      WHERE quoted_object.tweet_id = relation.target_tweet_id
                        AND quoted_object.record_type = '{RecordType.TWEET_OBJECT}'
                        AND quoted_object.enrichment_state IN ({state_placeholders})
                  )
            ),
            eligible_tweets AS (
                SELECT tweet_id FROM saved
                UNION
                SELECT tweet_id FROM quoted
            )
            SELECT
                COUNT(*) AS eligible_tweets,
                COALESCE(SUM(EXISTS (
                    SELECT 1
                    FROM archive tg INDEXED BY idx_archive_tweet_id
                    WHERE tg.tweet_id = eligible_tweets.tweet_id
                      AND tg.record_type = '{RecordType.MEDIA_TAG}'
                      AND (tg.enrichment_state = 'done' OR tg.enrichment_state IS NULL)
                      AND json_valid(tg.raw_json)
                      AND json_type(tg.raw_json, '$.tags') = 'array'
                      AND json_array_length(tg.raw_json, '$.tags') > 0
                )), 0) AS tagged_tweets
            FROM eligible_tweets
        """
        row = self.conn.execute(
            query,
            (*AVAILABLE_ENRICHMENT_STATES, *AVAILABLE_ENRICHMENT_STATES),
        ).fetchone()
        if row is None:
            return 0, 0
        return int(row["eligible_tweets"]), int(row["tagged_tweets"])

    def defer_tagging_failure(self, tweet_ids: list[str]) -> None:
        """Persist backoff separately so explicit retries preserve existing tags."""
        now = time.time()
        for tweet_id in tweet_ids:
            key = f"tagging_retry:{tweet_id}"
            row = self.conn.execute(
                "SELECT value FROM archive WHERE row_key = ?", (key,)
            ).fetchone()
            try:
                previous = json.loads(row[0]) if row else {}
                attempts = max(int(previous.get("attempts", 0)), 0) + 1
            except (TypeError, ValueError, AttributeError):
                attempts = 1
            delay = min(3600 * 2 ** min(attempts - 1, 8), 7 * 86400)
            self.conn.execute(
                "INSERT OR REPLACE INTO archive "
                "(row_key, record_type, key, value, updated_at) VALUES (?, ?, ?, ?, ?)",
                (
                    key,
                    RecordType.METADATA,
                    key,
                    json.dumps({"attempts": attempts, "next_retry_at": now + delay}),
                    str(now),
                ),
            )
        self.conn.commit()

    def delete_media_tag(self, tweet_id: str) -> None:
        self.conn.execute(
            f"DELETE FROM archive WHERE record_type = '{RecordType.MEDIA_TAG}' AND tweet_id = ?",
            (tweet_id,),
        )
        self.conn.commit()

    def update_media_tags(
        self,
        tweet_id: str,
        tags: list[str],
        *,
        description: str | None = None,
    ) -> None:
        """Overwrite tags and optionally the description for a specific tweet."""
        normalized_tags: list[str] = []
        seen: set[str] = set()
        for tag in tags:
            cleaned = tag.strip()
            folded = cleaned.casefold()
            if not cleaned or folded in seen:
                continue
            seen.add(folded)
            normalized_tags.append(cleaned)

        row = self.conn.execute(
            f"SELECT raw_json FROM archive WHERE record_type = '{RecordType.MEDIA_TAG}' AND "
            "tweet_id = ?",
            (tweet_id,),
        ).fetchone()

        cleaned_description = description.strip() if description is not None else None
        if not normalized_tags and (description is None or not cleaned_description):
            self.delete_media_tag(tweet_id)
            return

        now_ts = str(time.time())
        if row and row["raw_json"]:
            try:
                data = json.loads(row["raw_json"])
            except json.JSONDecodeError:
                data = {}
            if not isinstance(data, dict):
                data = {}
            data["tags"] = normalized_tags
        else:
            data = {"description": "", "tags": normalized_tags}

        if cleaned_description is not None:
            data["description"] = cleaned_description

        payload = json.dumps(data)

        self.conn.execute(
            "INSERT OR REPLACE INTO archive "
            "(row_key, record_type, tweet_id, raw_json, enrichment_state, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (f"media_tag:{tweet_id}", "media_tag", tweet_id, payload, "done", now_ts),
        )
        self.conn.commit()

    def set_tweet_hidden(self, tweet_id: str, hidden: bool) -> None:
        """Hide a tweet from lists and searches, or show it again."""
        if hidden:
            self.conn.execute(
                "INSERT OR IGNORE INTO archive (row_key, record_type, tweet_id, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (f"tweet_hidden:{tweet_id}", RecordType.TWEET_HIDDEN, tweet_id, str(time.time())),
            )
        else:
            self.conn.execute(
                f"DELETE FROM archive WHERE record_type = '{RecordType.TWEET_HIDDEN}' AND "
                "tweet_id = ?",
                (tweet_id,),
            )
        self.conn.commit()

    def _excluded_member_count(self, collection: str, excluded_ids: str) -> int:
        """Count excluded IDs that belong to this collection, deduplicating overlap."""
        membership = (
            f"member.record_type = '{RecordType.TWEET}' AND member.tweet_id = excluded.tweet_id"
        )
        params: tuple[str, ...] = ()
        if collection != "all":
            membership += " AND member.collection_type = ?"
            params = (collection,)
        return int(
            self.conn.execute(
                f"SELECT COUNT(*) FROM ({excluded_ids}) AS excluded "
                "WHERE EXISTS (SELECT 1 FROM archive AS member "
                f"INDEXED BY idx_archive_membership WHERE {membership})",
                params,
            ).fetchone()[0]
        )

    def delete_global_tag(self, tag: str) -> None:
        """Delete a tag globally across all tweets."""
        sql = f"""
        SELECT a.tweet_id, a.raw_json
        FROM archive a,
             json_each(
               CASE WHEN json_valid(a.raw_json) THEN a.raw_json ELSE '{{"tags":[]}}' END,
               '$.tags'
             ) as t
        WHERE a.record_type = '{RecordType.MEDIA_TAG}' AND LOWER(t.value) = LOWER(?)
        """
        rows = self.conn.execute(sql, (tag,)).fetchall()

        now_ts = str(time.time())
        for row in rows:
            tid = row["tweet_id"]
            if not row["raw_json"]:
                continue
            try:
                data = json.loads(row["raw_json"])
                if "tags" in data:
                    data["tags"] = [t for t in data["tags"] if t.lower() != tag.lower()]
                    if not data["tags"]:
                        self.conn.execute(
                            f"DELETE FROM archive WHERE record_type = '{RecordType.MEDIA_TAG}' "
                            "AND tweet_id = ?",
                            (tid,),
                        )
                    else:
                        self.conn.execute(
                            "UPDATE archive SET raw_json = ?, updated_at = ? "
                            f"WHERE record_type = '{RecordType.MEDIA_TAG}' AND tweet_id = ?",
                            (json.dumps(data), now_ts, tid),
                        )
            except json.JSONDecodeError:
                pass
        self.conn.commit()

    def merge_global_tags(self, primary_tag: str, merge_tags: list[str]) -> None:
        """Merge a list of tags into a primary tag globally."""
        primary_tag = primary_tag.strip()
        normalized_merge_tags = list(
            dict.fromkeys(
                tag.strip().casefold()
                for tag in merge_tags
                if tag.strip() and tag.strip().casefold() != primary_tag.casefold()
            )
        )
        if not primary_tag or not normalized_merge_tags:
            return

        merge_tags_lower = set(normalized_merge_tags)
        placeholders = ",".join("?" for _ in normalized_merge_tags)

        sql = f"""
        SELECT DISTINCT a.tweet_id, a.raw_json
        FROM archive a,
             json_each(
               CASE WHEN json_valid(a.raw_json) THEN a.raw_json ELSE '{{"tags":[]}}' END,
               '$.tags'
             ) as t
        WHERE a.record_type = '{RecordType.MEDIA_TAG}' AND LOWER(t.value) IN ({placeholders})
        """

        rows = self.conn.execute(sql, tuple(normalized_merge_tags)).fetchall()
        now_ts = str(time.time())

        for row in rows:
            tid = row["tweet_id"]
            if not row["raw_json"]:
                continue
            try:
                data = json.loads(row["raw_json"])
                if "tags" in data:
                    existing_tags = data["tags"]
                    new_tags: list[str] = []
                    seen_tags: set[str] = set()
                    has_primary = False

                    for t in existing_tags:
                        if not isinstance(t, str):
                            continue
                        t_low = t.casefold()
                        if t_low == primary_tag.casefold():
                            has_primary = True
                            if t_low not in seen_tags:
                                new_tags.append(t)
                                seen_tags.add(t_low)
                        elif t_low not in merge_tags_lower and t_low not in seen_tags:
                            new_tags.append(t)
                            seen_tags.add(t_low)

                    if not has_primary:
                        new_tags.append(primary_tag)

                    data["tags"] = new_tags

                    self.conn.execute(
                        "UPDATE archive SET raw_json = ?, updated_at = ? "
                        f"WHERE record_type = '{RecordType.MEDIA_TAG}' AND tweet_id = ?",
                        (json.dumps(data), now_ts, tid),
                    )
            except json.JSONDecodeError:
                pass
        self.conn.commit()

    def get_tag_counts(self, query: str = "", limit: int = 50) -> list[dict[str, Any]]:
        sql = f"""
        SELECT MIN(json_each.value) as tag, count(*) as count
        FROM archive,
             json_each(
               CASE WHEN json_valid(archive.raw_json)
                    THEN archive.raw_json ELSE '{{"tags":[]}}' END,
               '$.tags'
             )
        WHERE record_type = '{RecordType.MEDIA_TAG}'
          AND (enrichment_state = 'done' OR enrichment_state IS NULL)
          AND json_each.type = 'text'
        """
        params = []
        # Each fragment must occur in the same tag; punctuation between them
        # does not prevent a match. instr keeps %, _ and other input literal.
        for fragment in query.lower().split():
            sql += " AND instr(LOWER(json_each.value), ?) > 0"
            params.append(fragment)

        sql += " GROUP BY LOWER(json_each.value) ORDER BY count DESC LIMIT ?"
        params.append(limit)

        rows = self.conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]
