"""Database facts for requested statistics sections, independent of presentation and files."""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import UTC, datetime
from typing import Any

from .common import (
    RESURRECTION_ELIGIBLE_SQL,
    RESURRECTION_PERMANENT_SQL,
    SEARCH_COLLECTION_ORDER,
)
from .insight_facts import InsightFacts, InsightFactsMixin
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
    # Post objects kept only as conversation context: parents, replies and quotes.
    context_posts: int


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
    insights: InsightFacts | None = None


def _iso_date(timestamp: int | None) -> str | None:
    # created_at_ts is the parsed created_at, so the ISO form names the same instant.
    if timestamp is None:
        return None
    return datetime.fromtimestamp(int(timestamp), tz=UTC).isoformat()


class ReportFactsMixin(InsightFactsMixin, StatsMixin, TagsMixin):
    def statistics_facts(self, include: set[str]) -> StatisticsFacts:
        return StatisticsFacts(
            owner=self.get_archive_owner_id(),
            overview=self.overview_facts() if "overview" in include else None,
            collections=self.collection_facts() if "collections" in include else None,
            health=self.health_facts() if "archive_status" in include else None,
            tagging=self.tagging_coverage() if "tagging" in include else None,
            storage=self.storage_facts() if "storage" in include else None,
            insights=self.insight_facts() if "insights" in include else None,
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
        # created_at_ts holds the parsed created_at, so its bounds are the valid date bounds.
        bounds = conn.execute(
            f"""
            SELECT min(created_at_ts), max(created_at_ts)
            FROM archive INDEXED BY idx_archive_record_page
            WHERE record_type = '{RecordType.TWEET}' AND created_at_ts IS NOT NULL
            """
        ).fetchone()
        oldest_post, newest_post = _iso_date(bounds[0]), _iso_date(bounds[1])
        latest_capture_row = conn.execute(
            f"SELECT max(captured_at) FROM archive WHERE record_type = '{RecordType.RAW_CAPTURE}'"
        ).fetchone()
        latest_sync_row = conn.execute(
            f"SELECT max(updated_at) FROM archive WHERE record_type = '{RecordType.SYNC_STATE}'"
        ).fetchone()
        latest_capture = latest_capture_row[0] if latest_capture_row else None
        latest_sync = latest_sync_row[0] if latest_sync_row else None
        archive_tweets, missing_archive_tweets = self.availability_counts()
        context_posts = count(
            f"""
            SELECT count(*) FROM archive
            WHERE record_type = '{RecordType.TWEET_OBJECT}'
              AND tweet_id NOT IN (
                  SELECT tweet_id FROM archive INDEXED BY idx_archive_membership
                  WHERE record_type = '{RecordType.TWEET}'
              )
            """
        )
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
            context_posts,
        )

    def collection_facts(self) -> list[ArchiveCollectionStats]:
        collections = {name: ArchiveCollectionStats(name) for name in SEARCH_COLLECTION_ORDER}
        for name, count, oldest, newest in self.conn.execute(
            f"""
            SELECT collection_type, count(*), min(created_at_ts), max(created_at_ts)
            FROM archive INDEXED BY idx_archive_record_collection_page
            WHERE record_type = '{RecordType.TWEET}'
            GROUP BY collection_type
            """
        ):
            if not isinstance(name, str) or not name:
                continue
            collection = collections.setdefault(name, ArchiveCollectionStats(name))
            collection.post_count = int(count)
            collection.oldest_created_at = _iso_date(oldest)
            collection.newest_created_at = _iso_date(newest)
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
                sum(CASE WHEN {RESURRECTION_PERMANENT_SQL} THEN 1 ELSE 0 END) AS permanent,
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
        stats = self.archive_stats(include_dates=False)
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
        # One pass over the tag arrays yields the distinct count, the total and the top tags.
        tag_counts = conn.execute(
            f"""
            SELECT tweetnook_tag_key(t.value) AS tag, count(*) AS count
            FROM (
                SELECT raw_json FROM archive
                WHERE record_type = '{RecordType.MEDIA_TAG}'
                  AND (enrichment_state = 'done' OR enrichment_state IS NULL)
                  AND json_valid(raw_json)
            ) a, json_each(a.raw_json, '$.tags') AS t
            WHERE t.value IS NOT NULL
            GROUP BY tweetnook_tag_key(t.value)
            """
        ).fetchall()
        unique_tags = sum(1 for row in tag_counts if row[0] is not None)
        total_tag_instances = sum(int(row[1]) for row in tag_counts)
        top_tags_rows = sorted(
            (row for row in tag_counts if row[0] is not None),
            key=lambda row: (-int(row[1]), str(row[0])),
        )[:20]
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
        def get_db_stats(record_type: str, *columns: str) -> tuple[int, int]:
            # Exact UTF-8 byte totals: length() of a blob counts bytes, where length() of
            # text counts characters.
            payload = " + ".join(f"ifnull(length(CAST({column} AS BLOB)), 0)" for column in columns)
            count, payload_bytes = self.conn.execute(
                f"""
                SELECT count(*), sum({payload})
                FROM archive INDEXED BY idx_archive_record_page
                WHERE record_type = ?
                """,
                (record_type,),
            ).fetchone()
            return int(count or 0), int(payload_bytes or 0)

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
        core_db_count, core_db_bytes = get_db_stats("tweet", "raw_json", "text")
        threads_count, threads_bytes = get_db_stats("tweet_object", "raw_json")
        articles_count, articles_bytes = get_db_stats("article", "content_text", "summary_text")
        tags_count, tags_bytes = get_db_stats("media_tag", "raw_json")
        _tweet_count, profiles_bytes = get_db_stats(
            "tweet", "author_username", "author_display_name"
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
