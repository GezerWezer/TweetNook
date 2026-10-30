"""Database facts for requested statistics sections, independent of presentation and files."""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import UTC, datetime
from typing import Any

from .common import RESURRECTION_ELIGIBLE_SQL, SEARCH_COLLECTION_ORDER, valid_date_bounds
from .record_types import RecordType
from .stats import ArchiveCollectionStats, StatsMixin
from .tags import TagsMixin


@dataclass(slots=True)
class OverviewFacts:
    unique_posts: int
    collection_memberships: int
    articles: int
    raw_captures: int
    media_rows: int
    urls: int
    profiles: int
    oldest_post: str | None
    newest_post: str | None
    latest_capture: str | None
    latest_sync: str | None
    archive_tweets: int
    missing_archive_tweets: int


@dataclass(slots=True)
class TaggingFacts:
    eligible_tweets: int
    tagged_tweets: int
    unique_tags: int
    total_tag_instances: int
    top_tags: list[dict[str, Any]]
    usage_tagged_tweets: int = 0


@dataclass(slots=True)
class StorageFacts:
    core_tweet_ids: set[str]
    media_rows: list[Any]
    payloads: dict[str, tuple[int, int]]
    profiles_count: int


@dataclass(slots=True)
class HealthCounts:
    done_enrichment_count: int
    resurrected_enrichment_count: int
    pending_enrichment_count: int
    transient_enrichment_failure_count: int
    transient_enrichment_due_count: int
    transient_enrichment_delayed_count: int
    terminal_enrichment_count: int
    expanded_thread_target_count: int
    preview_article_count: int
    missing_tweet_object_count: int
    pending_thread_membership_count: int
    pending_thread_linked_status_count: int


@dataclass(slots=True)
class HealthFacts:
    stats: HealthCounts
    archive_total: int
    unavailable_total: int
    incomplete: int
    reason_counts: dict[str, dict[str, int]]


@dataclass(slots=True)
class StatisticsFacts:
    owner: str | None
    overview: OverviewFacts | None = None
    collections: list[ArchiveCollectionStats] | None = None
    health: HealthFacts | None = None
    tagging: TaggingFacts | None = None
    storage: StorageFacts | None = None


class ReportFactsMixin(StatsMixin, TagsMixin):
    def statistics_facts(self, include: set[str]) -> StatisticsFacts:
        return StatisticsFacts(
            owner=self.get_archive_owner_id(),
            overview=self.overview_facts() if "overview" in include else None,
            collections=self.collection_facts() if "collections" in include else None,
            health=self.health_facts() if "archive_status" in include else None,
            tagging=self.tagging_coverage() if "tagging" in include else None,
            storage=self.storage_facts() if "storage" in include else None,
        )

    def enrichment_incomplete_count(self) -> int:
        row = self.conn.execute(
            f"""
            SELECT count(*) FROM archive
            WHERE record_type = '{RecordType.TWEET_OBJECT}'
              AND enrichment_state IN ('pending', 'transient_failure')
            """
        ).fetchone()
        return int(row[0] or 0) if row else 0

    def latest_sync_at(self) -> str | None:
        row = self.conn.execute(
            f"SELECT max(updated_at) FROM archive WHERE record_type = '{RecordType.SYNC_STATE}'"
        ).fetchone()
        if row and isinstance(row[0], str) and row[0]:
            return row[0]
        return None

    def availability_counts(self) -> tuple[int, int]:
        row = self.conn.execute(
            f"""
            SELECT
                count(*),
                count(*) FILTER (WHERE enrichment_state = 'terminal_unavailable')
            FROM archive
            WHERE record_type = '{RecordType.TWEET_OBJECT}'
            """
        ).fetchone()
        return (int(row[0] or 0), int(row[1] or 0)) if row else (0, 0)

    def overview_facts(self) -> OverviewFacts:
        conn = self.conn

        def count(sql: str) -> int:
            return int(conn.execute(sql).fetchone()[0] or 0)

        unique_posts = count(
            f"SELECT count(DISTINCT tweet_id) FROM archive WHERE record_type = '{RecordType.TWEET}'"
        )
        collection_memberships = count(
            f"SELECT count(*) FROM archive WHERE record_type = '{RecordType.TWEET}'"
        )
        articles = count(f"SELECT count(*) FROM archive WHERE record_type = '{RecordType.ARTICLE}'")
        raw_captures = count(
            f"SELECT count(*) FROM archive WHERE record_type = '{RecordType.RAW_CAPTURE}'"
        )
        media_rows = count(f"SELECT count(*) FROM archive WHERE record_type = '{RecordType.MEDIA}'")
        urls = count(f"SELECT count(*) FROM archive WHERE record_type = '{RecordType.URL}'")
        profiles = count(
            """
            SELECT count(DISTINCT author_id)
            FROM archive
            WHERE author_id IS NOT NULL AND author_id != ''
            """
        )
        oldest_post, newest_post = valid_date_bounds(
            row[0]
            for row in conn.execute(
                f"SELECT created_at FROM archive WHERE record_type = '{RecordType.TWEET}'"
            )
        )
        latest_capture_row = conn.execute(
            f"SELECT max(captured_at) FROM archive WHERE record_type = '{RecordType.RAW_CAPTURE}'"
        ).fetchone()
        latest_sync_row = conn.execute(
            f"SELECT max(updated_at) FROM archive WHERE record_type = '{RecordType.SYNC_STATE}'"
        ).fetchone()
        latest_capture = latest_capture_row[0] if latest_capture_row else None
        latest_sync = latest_sync_row[0] if latest_sync_row else None
        archive_tweets, missing_archive_tweets = self.availability_counts()
        return OverviewFacts(
            unique_posts,
            collection_memberships,
            articles,
            raw_captures,
            media_rows,
            urls,
            profiles,
            oldest_post,
            newest_post,
            latest_capture,
            latest_sync,
            archive_tweets,
            missing_archive_tweets,
        )

    def collection_facts(self) -> list[ArchiveCollectionStats]:
        rows = self._query(
            f"record_type = '{RecordType.TWEET}'", cols=["collection_type", "created_at"]
        )
        collections = {name: ArchiveCollectionStats(name) for name in SEARCH_COLLECTION_ORDER}
        dates: dict[str, list[str | None]] = {}
        for row in rows:
            name = row.get("collection_type")
            if not isinstance(name, str) or not name:
                continue
            collection = collections.setdefault(name, ArchiveCollectionStats(name))
            collection.post_count += 1
            dates.setdefault(name, []).append(row.get("created_at"))
        for name, values in dates.items():
            collection = collections[name]
            collection.oldest_created_at, collection.newest_created_at = valid_date_bounds(values)
        for row in self._query(
            f"record_type = '{RecordType.SYNC_STATE}'",
            cols=["collection_type", "updated_at", "backfill_cursor", "backfill_incomplete"],
        ):
            name = row.get("collection_type")
            if not isinstance(name, str) or not name:
                continue
            collection = collections.setdefault(name, ArchiveCollectionStats(name))
            updated = row.get("updated_at")
            if (
                isinstance(updated, str)
                and updated
                and (collection.last_synced_at is None or updated > collection.last_synced_at)
            ):
                collection.last_synced_at = updated
                collection.backfill_cursor = row.get("backfill_cursor") or None
                collection.backfill_incomplete = self._parse_bool(row.get("backfill_incomplete"))
        names = [*SEARCH_COLLECTION_ORDER, *sorted(set(collections) - set(SEARCH_COLLECTION_ORDER))]
        return [collections[name] for name in names]

    def health_facts(self) -> HealthFacts:
        now = datetime.now(tz=UTC).isoformat()
        rows = self.conn.execute(
            f"""
            SELECT
                COALESCE(NULLIF(enrichment_reason, ''), 'unavailable_unknown') AS reason,
                count(*) AS count,
                sum(CASE WHEN NOT {RESURRECTION_ELIGIBLE_SQL} THEN 1 ELSE 0 END) AS permanent,
                sum(CASE WHEN {RESURRECTION_ELIGIBLE_SQL} THEN 1 ELSE 0 END) AS retryable,
                sum(CASE WHEN {RESURRECTION_ELIGIBLE_SQL}
                    AND (enrichment_next_retry_at IS NULL OR enrichment_next_retry_at <= ?)
                    THEN 1 ELSE 0 END) AS due,
                sum(CASE WHEN {RESURRECTION_ELIGIBLE_SQL} AND enrichment_next_retry_at > ?
                    THEN 1 ELSE 0 END) AS delayed
            FROM archive
            WHERE record_type = '{RecordType.TWEET_OBJECT}'
              AND enrichment_state = 'terminal_unavailable'
            GROUP BY COALESCE(NULLIF(enrichment_reason, ''), 'unavailable_unknown')
            """,
            (now, now),
        ).fetchall()
        counts_by_reason = {
            str(row[0]): {
                "count": int(row[1] or 0),
                "permanent": int(row[2] or 0),
                "retryable": int(row[3] or 0),
                "due": int(row[4] or 0),
                "delayed": int(row[5] or 0),
            }
            for row in rows
        }
        archive_total, unavailable_total = self.availability_counts()
        stats = self.archive_stats()
        return HealthFacts(
            HealthCounts(
                **{field.name: getattr(stats, field.name) for field in fields(HealthCounts)}
            ),
            archive_total,
            unavailable_total,
            self.enrichment_incomplete_count(),
            counts_by_reason,
        )

    def tagging_coverage(self) -> TaggingFacts:
        conn = self.conn
        eligible_tweets, tagged_tweets = self.get_tagging_coverage_counts()
        unique_tags = int(
            conn.execute(
                f"""
                SELECT count(DISTINCT LOWER(t.value))
                FROM (
                    SELECT raw_json FROM archive
                    WHERE record_type = '{RecordType.MEDIA_TAG}'
                      AND (enrichment_state = 'done' OR enrichment_state IS NULL)
                      AND json_valid(raw_json)
                ) a, json_each(a.raw_json, '$.tags') AS t
                """
            ).fetchone()[0]
            or 0
        )
        total_tag_instances = int(
            conn.execute(
                f"""
                SELECT count(t.value)
                FROM (
                    SELECT raw_json FROM archive
                    WHERE record_type = '{RecordType.MEDIA_TAG}'
                      AND (enrichment_state = 'done' OR enrichment_state IS NULL)
                      AND json_valid(raw_json)
                ) a, json_each(a.raw_json, '$.tags') AS t
                """
            ).fetchone()[0]
            or 0
        )
        top_tags_rows = conn.execute(
            f"""
            SELECT LOWER(t.value) AS tag, count(*) AS count
            FROM (
                SELECT raw_json FROM archive
                WHERE record_type = '{RecordType.MEDIA_TAG}'
                  AND (enrichment_state = 'done' OR enrichment_state IS NULL)
                  AND json_valid(raw_json)
            ) a, json_each(a.raw_json, '$.tags') AS t
            GROUP BY LOWER(t.value)
            ORDER BY count DESC, tag ASC
            LIMIT 20
            """
        ).fetchall()
        top_tags = [{"tag": row[0], "count": row[1]} for row in top_tags_rows]
        usage_tagged_tweets = int(
            self.conn.execute(
                f"SELECT COUNT(DISTINCT a.tweet_id) FROM archive AS a, "
                "json_each(CASE WHEN json_valid(a.raw_json) THEN a.raw_json ELSE '{}' END, "
                "'$.tags') t "
                f"WHERE a.record_type = '{RecordType.MEDIA_TAG}' "
                "AND (a.enrichment_state = 'done' OR a.enrichment_state IS NULL) "
                "AND t.value IS NOT NULL"
            ).fetchone()[0]
        )
        return TaggingFacts(
            eligible_tweets,
            tagged_tweets,
            unique_tags,
            total_tag_instances,
            top_tags,
            usage_tagged_tweets,
        )

    def storage_facts(self) -> StorageFacts:
        payload_sample_limit = 4096

        def get_db_stats(record_type: str, payload_expression: str) -> tuple[int, int]:
            count = int(
                self.conn.execute(
                    """
                    SELECT count(*)
                    FROM archive INDEXED BY idx_archive_record_page
                    WHERE record_type = ?
                    """,
                    (record_type,),
                ).fetchone()[0]
                or 0
            )
            if count == 0:
                return 0, 0
            sample_count, sample_bytes = self.conn.execute(
                f"""
                SELECT count(*), sum(payload_bytes)
                FROM (
                    SELECT {payload_expression} AS payload_bytes
                    FROM archive INDEXED BY idx_archive_record_page
                    WHERE record_type = ?
                    LIMIT ?
                )
                """,
                (record_type, payload_sample_limit),
            ).fetchone()
            estimated_bytes = round((int(sample_bytes or 0) / int(sample_count or 1)) * count)
            return count, estimated_bytes

        core_tweet_ids = {
            str(row[0])
            for row in self.conn.execute(
                f"""
                SELECT tweet_id
                FROM archive INDEXED BY idx_archive_record_page
                WHERE record_type = '{RecordType.TWEET}' AND tweet_id IS NOT NULL
                """
            )
        }
        root_tweet_ids = sorted(core_tweet_ids)
        for start in range(0, len(root_tweet_ids), 500):
            chunk = root_tweet_ids[start : start + 500]
            placeholders = ", ".join("?" for _tweet_id in chunk)
            related_rows = self.conn.execute(
                f"""
                SELECT target_tweet_id
                FROM archive INDEXED BY idx_archive_tweet_id
                WHERE tweet_id IN ({placeholders})
                  AND record_type = '{RecordType.TWEET_RELATION}'
                  AND relation_type IN ('quote_of', 'quoted', 'links_to_status')
                  AND target_tweet_id IS NOT NULL
                """,
                chunk,
            ).fetchall()
            core_tweet_ids.update(str(row[0]) for row in related_rows)

        media_rows = self.conn.execute(
            f"""
            SELECT tweet_id, local_path, thumbnail_local_path, media_type
            FROM archive INDEXED BY idx_archive_record_page
            WHERE record_type = '{RecordType.MEDIA}'
            """
        ).fetchall()
        core_db_count, core_db_bytes = get_db_stats(
            "tweet",
            "ifnull(length(raw_json), 0) + ifnull(length(text), 0)",
        )
        threads_count, threads_bytes = get_db_stats(
            "tweet_object",
            "ifnull(length(raw_json), 0)",
        )
        articles_count, articles_bytes = get_db_stats(
            "article",
            "ifnull(length(content_text), 0) + ifnull(length(summary_text), 0)",
        )
        tags_count, tags_bytes = get_db_stats(
            "media_tag",
            "ifnull(length(raw_json), 0)",
        )
        _tweet_count, profiles_bytes = get_db_stats(
            "tweet",
            "ifnull(length(author_username), 0) + ifnull(length(author_display_name), 0)",
        )
        profiles_count = int(
            self.conn.execute(
                """
                SELECT count(DISTINCT author_id)
                FROM archive
                WHERE author_id IS NOT NULL AND author_id != ''
                """
            ).fetchone()[0]
            or 0
        )
        return StorageFacts(
            core_tweet_ids,
            list(media_rows),
            {
                "core": (core_db_count, core_db_bytes),
                "threads": (threads_count, threads_bytes),
                "articles": (articles_count, articles_bytes),
                "tags": (tags_count, tags_bytes),
                "profiles": (_tweet_count, profiles_bytes),
            },
            profiles_count,
        )
