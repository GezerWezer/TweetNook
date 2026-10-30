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
    def _directional_relations(
        self,
        tweet_id: str,
        *,
        source_types: tuple[str, ...] = (),
        target_types: tuple[str, ...] = (),
        limit: int,
    ):
        """Load both sides of a relation without leaving an index choice to SQLite."""
        rows = []
        seen = set()
        lookups = (
            ("tweet_id", source_types, "idx_archive_tweet_id"),
            ("target_tweet_id", target_types, "idx_archive_target_tweet_id"),
        )
        for field, relation_types, index_name in lookups:
            if not relation_types or len(rows) >= limit:
                continue
            types = clause_in("relation_type", relation_types)
            candidates = self._query(
                f"record_type = '{RecordType.TWEET_RELATION}' AND {field} = ? AND {types.sql}",
                (tweet_id, *types.params),
                cols=["tweet_id", "target_tweet_id", "relation_type"],
                limit=limit - len(rows),
                indexed_by=index_name,
            )
            for row in candidates:
                key = (row.get("tweet_id"), row.get("target_tweet_id"), row.get("relation_type"))
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)
                if len(rows) >= limit:
                    break
        return rows

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
                    FROM archive AS relation INDEXED BY idx_archive_tweet_id
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
        """Resolve one chain in directional lookup order, excluding cycles and the focal ID."""
        parents: list[str] = []
        current = tweet_id
        seen = {tweet_id}
        for _ in range(50):
            rows = self._directional_relations(
                current,
                source_types=("reply_to", "thread_parent"),
                target_types=("thread_child",),
                limit=10,
            )
            parent = None
            for row in rows:
                if row.get("relation_type") in ("reply_to", "thread_parent"):
                    parent = row.get("target_tweet_id")
                elif row.get("relation_type") == "thread_child":
                    parent = row.get("tweet_id")
                if parent:
                    break
            if not parent or parent in seen:
                break
            parents.append(parent)
            seen.add(parent)
            current = parent
        return parents

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
            indexed_by="idx_archive_tweet_id",
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
            limit=related_count * 10,
            indexed_by="idx_archive_tweet_id",
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
                "raw_json",
            ],
            limit=related_count * 3,
            indexed_by="idx_archive_tweet_id",
        )
        tag_rows = self._query(
            f"record_type = '{RecordType.MEDIA_TAG}' AND {related.sql}",
            related.params,
            cols=["tweet_id", "raw_json"],
            limit=related_count,
            indexed_by="idx_archive_tweet_id",
        )

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
        paginated_ids = [r.get("tweet_id") for r in rows if r.get("tweet_id")]
        return paginated_ids

    def stored_tweet_json(self, tweet_id: str) -> str | None:
        rows = self._query(
            f"record_type = '{RecordType.TWEET_OBJECT}' AND tweet_id = ?",
            (tweet_id,),
            cols=["raw_json"],
            limit=1,
            indexed_by="idx_archive_tweet_id",
        )
        return rows[0].get("raw_json") if rows else None
