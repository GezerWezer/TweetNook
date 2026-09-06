"""Collect the canonical archive statistics report."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

from tweetnook.stats.models import (
    StatItem,
    StatsReport,
    StatsSection,
    StatsSectionSpec,
    TableColumn,
)
from tweetnook.storage import ArchiveStore
from tweetnook.storage.backend import ArchiveStats

UNAVAILABLE_REASON_LABELS = (
    ("protected_account", "Protected account"),
    ("suspended_account", "Suspended account"),
    ("account_missing", "Missing account"),
    ("withheld", "Withheld"),
    ("not_found", "Not found"),
    ("unavailable_unknown", "Unknown availability"),
    ("deleted_by_author", "Deleted by author"),
    ("archive_deleted", "Deleted in archive"),
)

# The Web UI loads its legacy statistics endpoints in parallel. They share one
# ArchiveStore/SQLite connection, so serialize multi-query snapshots rather than
# interleaving cursor use across FastAPI worker threads. RLock is required because
# the archive-status collector also uses the lightweight enrichment aggregate.
_STATS_LOCK = RLock()


@dataclass(slots=True)
class _StatsContext:
    store: ArchiveStore
    _archive_stats: ArchiveStats | None = None

    @property
    def archive_stats(self) -> ArchiveStats:
        if self._archive_stats is None:
            self._archive_stats = self.store.archive_stats()
        return self._archive_stats


def _percentage(count: int, total: int) -> float:
    return round((count / total) * 100, 1) if total else 0.0


def format_bytes(size: float) -> str:
    """Format storage bytes for the Web compatibility payload."""
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if size < 1024.0 or unit == "PB":
            return f"{size:.2f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{size:.2f} PB"


def get_enrichment_incomplete_count(store: ArchiveStore) -> int:
    """Return the lightweight banner count without collecting the full report."""
    with _STATS_LOCK:
        row = store.conn.execute(
            """
            SELECT count(*) FROM archive
            WHERE record_type = 'tweet_object'
              AND enrichment_state IN ('pending', 'transient_failure')
            """
        ).fetchone()
    return int(row[0] or 0) if row else 0


def get_latest_sync_at(store: ArchiveStore) -> str | None:
    """Return the latest sync timestamp without collecting archive statistics."""
    with _STATS_LOCK:
        row = store.conn.execute(
            "SELECT max(updated_at) FROM archive WHERE record_type = 'sync_state'"
        ).fetchone()
    if row and isinstance(row[0], str) and row[0]:
        return row[0]
    return None


def _backfill_status(cursor: str | None, incomplete: bool) -> str:
    if incomplete and cursor:
        return "resume older"
    if incomplete:
        return "incomplete"
    if cursor:
        return "saved cursor"
    return "none saved"


def _archive_availability_counts(conn: Any) -> tuple[int, int]:
    row = conn.execute(
        """
        SELECT
            count(*),
            count(*) FILTER (WHERE enrichment_state = 'terminal_unavailable')
        FROM archive
        WHERE record_type = 'tweet_object'
        """
    ).fetchone()
    return (int(row[0] or 0), int(row[1] or 0)) if row else (0, 0)


def _unavailable_breakdown(
    conn: Any,
    *,
    archive_total: int,
    unavailable_total: int,
) -> dict[str, Any]:
    now = datetime.now(tz=UTC).isoformat()
    rows = conn.execute(
        """
        SELECT
            COALESCE(NULLIF(enrichment_reason, ''), 'unavailable_unknown') AS reason,
            count(*) AS count,
            sum(CASE
                WHEN deleted_at IS NOT NULL
                  OR enrichment_retry_eligible = 0
                  OR enrichment_reason IN ('archive_deleted', 'deleted_by_author')
                THEN 1 ELSE 0
            END) AS permanent,
            sum(CASE
                WHEN deleted_at IS NULL
                  AND enrichment_retry_eligible = 1
                  AND COALESCE(enrichment_reason, '') NOT IN (
                      'archive_deleted', 'deleted_by_author'
                  )
                THEN 1 ELSE 0
            END) AS retryable,
            sum(CASE
                WHEN deleted_at IS NULL
                  AND enrichment_retry_eligible = 1
                  AND COALESCE(enrichment_reason, '') NOT IN (
                      'archive_deleted', 'deleted_by_author'
                  )
                  AND (
                      enrichment_next_retry_at IS NULL
                      OR enrichment_next_retry_at <= ?
                  )
                THEN 1 ELSE 0
            END) AS due,
            sum(CASE
                WHEN deleted_at IS NULL
                  AND enrichment_retry_eligible = 1
                  AND COALESCE(enrichment_reason, '') NOT IN (
                      'archive_deleted', 'deleted_by_author'
                  )
                  AND enrichment_next_retry_at > ?
                THEN 1 ELSE 0
            END) AS delayed
        FROM archive
        WHERE record_type = 'tweet_object'
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
    labels = dict(UNAVAILABLE_REASON_LABELS)
    ordered_reasons = [reason for reason, _label in UNAVAILABLE_REASON_LABELS]
    ordered_reasons.extend(sorted(set(counts_by_reason) - set(ordered_reasons)))
    reasons = []
    for reason in ordered_reasons:
        counts = counts_by_reason.get(
            reason,
            {"count": 0, "permanent": 0, "retryable": 0, "due": 0, "delayed": 0},
        )
        count = counts["count"]
        reasons.append(
            {
                "reason": reason,
                "label": labels.get(reason, reason.replace("_", " ").title()),
                "count": count,
                "percent_of_missing": _percentage(count, unavailable_total),
                "percent_of_archive": _percentage(count, archive_total),
                "retryable": counts["retryable"],
                "due": counts["due"],
                "delayed": counts["delayed"],
                "permanent": counts["permanent"],
            }
        )
    return {
        "total": unavailable_total,
        "percent_of_archive": _percentage(unavailable_total, archive_total),
        "retryable": sum(item["retryable"] for item in reasons),
        "due": sum(item["due"] for item in reasons),
        "delayed": sum(item["delayed"] for item in reasons),
        "permanent": sum(item["permanent"] for item in reasons),
        "reasons": reasons,
    }


def _collect_overview(context: _StatsContext) -> StatsSection:
    conn = context.store.conn

    def count(sql: str) -> int:
        return int(conn.execute(sql).fetchone()[0] or 0)

    unique_posts = count("SELECT count(DISTINCT tweet_id) FROM archive WHERE record_type = 'tweet'")
    collection_memberships = count("SELECT count(*) FROM archive WHERE record_type = 'tweet'")
    articles = count("SELECT count(*) FROM archive WHERE record_type = 'article'")
    raw_captures = count("SELECT count(*) FROM archive WHERE record_type = 'raw_capture'")
    media_rows = count("SELECT count(*) FROM archive WHERE record_type = 'media'")
    urls = count("SELECT count(*) FROM archive WHERE record_type = 'url'")
    profiles = count(
        """
        SELECT count(DISTINCT author_id)
        FROM archive
        WHERE author_id IS NOT NULL AND author_id != ''
        """
    )
    oldest_row = conn.execute(
        """
        SELECT created_at FROM archive
        WHERE record_type = 'tweet' AND created_at IS NOT NULL AND created_at != ''
        ORDER BY created_at_ts ASC LIMIT 1
        """
    ).fetchone()
    newest_row = conn.execute(
        """
        SELECT created_at FROM archive
        WHERE record_type = 'tweet' AND created_at IS NOT NULL AND created_at != ''
        ORDER BY created_at_ts DESC LIMIT 1
        """
    ).fetchone()
    latest_capture_row = conn.execute(
        "SELECT max(captured_at) FROM archive WHERE record_type = 'raw_capture'"
    ).fetchone()
    latest_sync_row = conn.execute(
        "SELECT max(updated_at) FROM archive WHERE record_type = 'sync_state'"
    ).fetchone()
    owner_row = conn.execute(
        """
        SELECT value FROM archive
        WHERE record_type = 'metadata' AND key = 'owner_user_id'
        ORDER BY updated_at DESC LIMIT 1
        """
    ).fetchone()
    owner = owner_row[0] if owner_row and owner_row[0] else "Local Vault"
    oldest_post = oldest_row[0] if oldest_row else None
    newest_post = newest_row[0] if newest_row else None
    latest_capture = latest_capture_row[0] if latest_capture_row else None
    latest_sync = latest_sync_row[0] if latest_sync_row else None
    archive_tweets, missing_archive_tweets = _archive_availability_counts(conn)
    missing_pct = _percentage(missing_archive_tweets, archive_tweets)
    data = {
        "owner_user_id": owner,
        "unique_posts": unique_posts,
        "articles": articles,
        "collection_memberships": collection_memberships,
        "raw_captures": raw_captures,
        "media_rows": media_rows,
        "urls": urls,
        "profiles": profiles,
        "archive_tweets": archive_tweets,
        "missing_archive_tweets": missing_archive_tweets,
        "missing_archive_pct": missing_pct,
        "oldest_post": oldest_post,
        "newest_post": newest_post,
        "latest_capture": latest_capture,
        "latest_sync": latest_sync,
    }
    return StatsSection(
        id="overview",
        title="Overview",
        kind="cards",
        items=[
            StatItem("unique_posts", "Unique tweets", unique_posts),
            StatItem(
                "collection_memberships",
                "Collection memberships",
                collection_memberships,
            ),
            StatItem("media_rows", "Media", media_rows, description="Stored media records"),
            StatItem("articles", "Articles", articles),
            StatItem("urls", "URLs", urls),
            StatItem("profiles", "Authors", profiles, description="Distinct archived authors"),
            StatItem("raw_captures", "Raw captures", raw_captures),
            StatItem(
                "missing_archive_tweets",
                "Missing archive tweets",
                missing_archive_tweets,
                subtitle=f"{missing_pct:.1f}% of {archive_tweets:,} imported tweets",
                description=(
                    "Imported archive tweets that Twitter/X currently reports as unavailable"
                ),
            ),
            StatItem("oldest_post", "First tweet", oldest_post, format="datetime"),
            StatItem("newest_post", "Latest tweet", newest_post, format="datetime"),
            StatItem("latest_capture", "Latest capture", latest_capture, format="datetime"),
            StatItem("latest_sync", "Last sync", latest_sync, format="datetime"),
        ],
        data=data,
    )


def _collect_collections(context: _StatsContext) -> StatsSection:
    rows: list[dict[str, Any]] = []
    names = {"bookmark": "Bookmarks", "like": "Likes", "tweet": "Authored Tweets"}
    for collection in context.archive_stats.collections:
        rows.append(
            {
                "collection": names.get(
                    collection.collection_type, collection.collection_type.title()
                ),
                "raw_type": collection.collection_type,
                "count": collection.post_count,
                "oldest": collection.oldest_created_at,
                "newest": collection.newest_created_at,
                "last_synced": collection.last_synced_at,
                "backfill_status": _backfill_status(
                    collection.backfill_cursor,
                    collection.backfill_incomplete,
                ),
            }
        )
    return StatsSection(
        id="collections",
        title="Collections",
        kind="table",
        columns=[
            TableColumn("collection", "Collection"),
            TableColumn("count", "Tweets", format="integer", align="right"),
            TableColumn("oldest", "First", format="datetime"),
            TableColumn("newest", "Last", format="datetime"),
            TableColumn("last_synced", "Last sync", format="datetime"),
            TableColumn("backfill_status", "Backfill"),
        ],
        rows=rows,
        data={"collections": rows},
    )


def _collect_archive_status(context: _StatsContext) -> StatsSection:
    stats = context.archive_stats
    archive_tweets, unavailable_total = _archive_availability_counts(context.store.conn)
    unavailable = _unavailable_breakdown(
        context.store.conn,
        archive_total=archive_tweets,
        unavailable_total=unavailable_total,
    )
    incomplete = get_enrichment_incomplete_count(context.store)
    available = stats.done_enrichment_count + stats.resurrected_enrichment_count
    data = {
        "enrichment": {
            "available": available,
            "done": stats.done_enrichment_count,
            "resurrected": stats.resurrected_enrichment_count,
            "pending": stats.pending_enrichment_count,
            "transient": stats.transient_enrichment_failure_count,
            "transient_due": stats.transient_enrichment_due_count,
            "transient_delayed": stats.transient_enrichment_delayed_count,
            "incomplete": incomplete,
            "terminal": stats.terminal_enrichment_count,
            "unavailable": unavailable,
        },
        "threads_expanded": stats.expanded_thread_target_count,
        "preview_articles": stats.preview_article_count,
        "missing_tweet_objects": stats.missing_tweet_object_count,
        "pending_thread_memberships": stats.pending_thread_membership_count,
        "pending_linked_statuses": stats.pending_thread_linked_status_count,
    }
    return StatsSection(
        id="archive_status",
        title="Archive status",
        kind="archive_status",
        items=[
            StatItem("enriched", "Enriched", stats.done_enrichment_count),
            StatItem(
                "threads_expanded",
                "Threads",
                stats.expanded_thread_target_count,
                subtitle="TweetDetail targets expanded",
            ),
            StatItem(
                "missing_enrichment",
                "Missing enrichment",
                incomplete,
                subtitle=(
                    f"{stats.pending_enrichment_count:,} pending · "
                    f"{stats.transient_enrichment_failure_count:,} retrying"
                ),
            ),
            StatItem("resurrected", "Resurrected", stats.resurrected_enrichment_count),
            StatItem(
                "unavailable",
                "Unavailable tweets",
                unavailable_total,
                subtitle=f"{unavailable['percent_of_archive']:.1f}% of imported archive tweets",
            ),
            StatItem("preview_articles", "Preview-only articles", stats.preview_article_count),
            StatItem("rehydrate_gaps", "Rehydrate gaps", stats.missing_tweet_object_count),
        ],
        data=data,
    )


def _collect_tags(context: _StatsContext) -> StatsSection:
    conn = context.store.conn
    eligible_tweets, tagged_tweets = context.store.get_tagging_coverage_counts()
    unique_tags = int(
        conn.execute(
            """
            SELECT count(DISTINCT LOWER(t.value))
            FROM (
                SELECT raw_json FROM archive
                WHERE record_type = 'media_tag'
                  AND (enrichment_state = 'done' OR enrichment_state IS NULL)
                  AND json_valid(raw_json)
            ) a, json_each(a.raw_json, '$.tags') AS t
            """
        ).fetchone()[0]
        or 0
    )
    total_tag_instances = int(
        conn.execute(
            """
            SELECT count(t.value)
            FROM (
                SELECT raw_json FROM archive
                WHERE record_type = 'media_tag'
                  AND (enrichment_state = 'done' OR enrichment_state IS NULL)
                  AND json_valid(raw_json)
            ) a, json_each(a.raw_json, '$.tags') AS t
            """
        ).fetchone()[0]
        or 0
    )
    top_tags_rows = conn.execute(
        """
        SELECT LOWER(t.value) AS tag, count(*) AS count
        FROM (
            SELECT raw_json FROM archive
            WHERE record_type = 'media_tag'
              AND (enrichment_state = 'done' OR enrichment_state IS NULL)
              AND json_valid(raw_json)
        ) a, json_each(a.raw_json, '$.tags') AS t
        GROUP BY LOWER(t.value)
        ORDER BY count DESC, tag ASC
        LIMIT 20
        """
    ).fetchall()
    top_tags = [{"tag": row[0], "count": row[1]} for row in top_tags_rows]
    coverage_pct = _percentage(tagged_tweets, eligible_tweets)
    avg_tags = round(total_tag_instances / tagged_tweets, 1) if tagged_tweets else 0.0
    data = {
        "eligible_tweets": eligible_tweets,
        "tagged_tweets": tagged_tweets,
        "untagged_eligible": max(0, eligible_tweets - tagged_tweets),
        "unique_tags": unique_tags,
        "total_tag_instances": total_tag_instances,
        "coverage_pct": coverage_pct,
        "avg_tags_per_tweet": avg_tags,
        "top_tags": top_tags,
    }
    return StatsSection(
        id="tagging",
        title="Tagging & search",
        kind="tags",
        items=[
            StatItem(
                "coverage",
                "Coverage",
                coverage_pct,
                format="percent",
                subtitle=f"{tagged_tweets:,} / {eligible_tweets:,} eligible",
            ),
            StatItem("unique_tags", "Unique tags", unique_tags),
            StatItem("tagged_tweets", "Tagged tweets", tagged_tweets),
            StatItem("average_tags", "Average tags", avg_tags, format="decimal"),
        ],
        data=data,
    )


def _collect_storage(context: _StatsContext) -> StatsSection:
    store = context.store
    segments: list[dict[str, Any]] = []
    payload_sample_limit = 4096

    def get_db_stats(record_type: str, payload_expression: str) -> tuple[int, int]:
        count = int(
            store.conn.execute(
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
        sample_count, sample_bytes = store.conn.execute(
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
        for row in store.conn.execute(
            """
            SELECT tweet_id
            FROM archive INDEXED BY idx_archive_record_page
            WHERE record_type = 'tweet' AND tweet_id IS NOT NULL
            """
        )
    }
    root_tweet_ids = sorted(core_tweet_ids)
    for start in range(0, len(root_tweet_ids), 500):
        chunk = root_tweet_ids[start : start + 500]
        placeholders = ", ".join("?" for _tweet_id in chunk)
        related_rows = store.conn.execute(
            f"""
            SELECT target_tweet_id
            FROM archive INDEXED BY idx_archive_tweet_id
            WHERE tweet_id IN ({placeholders})
              AND record_type = 'tweet_relation'
              AND relation_type IN ('quote_of', 'quoted', 'links_to_status')
              AND target_tweet_id IS NOT NULL
            """,
            chunk,
        ).fetchall()
        core_tweet_ids.update(str(row[0]) for row in related_rows)

    media_rows = store.conn.execute(
        """
        SELECT tweet_id, local_path, thumbnail_local_path, media_type
        FROM archive INDEXED BY idx_archive_record_page
        WHERE record_type = 'media'
        """
    ).fetchall()
    core_media_rows = [row for row in media_rows if row[0] in core_tweet_ids]
    context_media_rows = [row for row in media_rows if row[0] not in core_tweet_ids]
    core_primary_types = {Path(row[1]).name: row[3] for row in core_media_rows if row[1]}
    context_primary_types = {Path(row[1]).name: row[3] for row in context_media_rows if row[1]}
    supplementary_file_names = {Path(row[2]).name for row in media_rows if row[2]}

    def is_supplementary(path: Path) -> bool:
        stem = path.stem.lower()
        return path.name in supplementary_file_names or stem.endswith(
            ("-poster", "_poster", "-thumbnail", "_thumbnail", "-thumb", "_thumb")
        )

    media_dir = store.db_path.parent / "media"
    avatars_dir = media_dir / "avatars"
    core_photos = core_videos = core_bytes = 0
    context_photos = context_videos = context_bytes = 0
    supplementary_count = supplementary_bytes = 0
    avatars_count = avatars_file_bytes = 0
    if media_dir.exists():
        for path in media_dir.rglob("*"):
            if not path.is_file():
                continue
            if avatars_dir in path.parents or path.parent == avatars_dir:
                avatars_count += 1
                avatars_file_bytes += path.stat().st_size
                continue
            size = path.stat().st_size
            if is_supplementary(path):
                supplementary_count += 1
                supplementary_bytes += size
                continue
            if path.name in core_primary_types:
                is_video = core_primary_types[path.name] in {"video", "animated_gif"}
                core_bytes += size
                core_videos += int(is_video)
                core_photos += int(not is_video)
            else:
                media_type = context_primary_types.get(path.name)
                is_video = media_type in {"video", "animated_gif"} or (
                    media_type is None
                    and path.suffix.lower() in {".mp4", ".mov", ".m3u8", ".ts", ".webm"}
                )
                context_bytes += size
                context_videos += int(is_video)
                context_photos += int(not is_video)
    if core_photos == 0 and core_videos == 0 and core_media_rows:
        for row in core_media_rows:
            core_videos += int(row[3] in {"video", "animated_gif"})
            core_photos += int(row[3] not in {"video", "animated_gif"})
    if context_photos == 0 and context_videos == 0 and context_media_rows:
        for row in context_media_rows:
            context_videos += int(row[3] in {"video", "animated_gif"})
            context_photos += int(row[3] not in {"video", "animated_gif"})
    if supplementary_count == 0 and supplementary_file_names:
        supplementary_count = len(supplementary_file_names)

    core_total_files = core_photos + core_videos
    context_total_files = context_photos + context_videos
    segments.extend(
        [
            {
                "id": "core_media",
                "group": "media",
                "name": "Core Media (Bookmarked & Quoted)",
                "bytes": core_bytes,
                "count": core_total_files,
                "unit": "files",
                "formatted_count": f"{core_photos:,} photos · {core_videos:,} videos/gifs",
                "description": "Downloaded media attached to saved and quoted tweets.",
            },
            {
                "id": "context_media",
                "group": "media",
                "name": "Context Media (Thread Extensions)",
                "bytes": context_bytes,
                "count": context_total_files,
                "unit": "files",
                "formatted_count": f"{context_photos:,} photos · {context_videos:,} videos/gifs",
                "description": "Downloaded media from surrounding thread context.",
            },
            {
                "id": "supplementary_media",
                "group": "media",
                "name": "Supplementary Media Files",
                "bytes": supplementary_bytes,
                "count": supplementary_count,
                "unit": "files",
                "formatted_count": f"{supplementary_count:,} supporting files",
                "description": "Video posters, thumbnails, and other derived supporting files.",
            },
        ]
    )
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
        store.conn.execute(
            """
            SELECT count(DISTINCT author_id)
            FROM archive
            WHERE author_id IS NOT NULL AND author_id != ''
            """
        ).fetchone()[0]
        or 0
    )
    segments.extend(
        [
            {
                "id": "core_db",
                "group": "database",
                "name": "Core Tweet Database",
                "bytes": core_db_bytes,
                "count": core_db_count,
                "unit": "tweets",
                "description": "Estimated payload size for saved tweet records and raw data.",
            },
            {
                "id": "threads",
                "group": "database",
                "name": "Thread Extensions & Context",
                "bytes": threads_bytes,
                "count": threads_count,
                "unit": "objects",
                "description": "Estimated payload size for fetched conversation context.",
            },
            {
                "id": "articles",
                "group": "database",
                "name": "Article Content & Cards",
                "bytes": articles_bytes,
                "count": articles_count,
                "unit": "articles",
                "description": "Estimated payload size for article bodies, previews, and cards.",
            },
            {
                "id": "tags",
                "group": "database",
                "name": "Tagging & Topic Metadata",
                "bytes": tags_bytes,
                "count": tags_count,
                "unit": "tags",
                "description": "Estimated payload size for search tags and topic metadata.",
            },
            {
                "id": "user_profiles",
                "group": "database",
                "name": "User Profiles & Handles",
                "bytes": profiles_bytes,
                "count": profiles_count,
                "unit": "profiles",
                "description": "Estimated payload size for stored author names and handles.",
            },
            {
                "id": "avatars",
                "group": "media",
                "name": "Avatar Image Cache",
                "bytes": avatars_file_bytes,
                "count": avatars_count,
                "unit": "avatars",
                "description": "Locally cached author profile images.",
            },
        ]
    )
    total_db_bytes = store.db_path.stat().st_size
    for suffix in ("-wal", "-shm"):
        sidecar = store.db_path.with_name(store.db_path.name + suffix)
        if sidecar.exists():
            total_db_bytes += sidecar.stat().st_size
    sum_db_segments = core_db_bytes + threads_bytes + articles_bytes + tags_bytes + profiles_bytes
    segments.append(
        {
            "id": "search_index",
            "group": "database",
            "name": "Search Index & DB Overhead",
            "bytes": max(0, total_db_bytes - sum_db_segments),
            "count": 1,
            "unit": "index",
            "description": "SQLite indexes, FTS data, structural overhead, and active sidecars.",
        }
    )
    total_bytes = (
        total_db_bytes + core_bytes + context_bytes + supplementary_bytes + avatars_file_bytes
    )
    segments = [segment for segment in segments if segment["bytes"] > 0 or segment["count"] > 0]
    segments.sort(key=lambda segment: segment["bytes"], reverse=True)
    for segment in segments:
        segment["formatted_size"] = format_bytes(segment["bytes"])
        segment["percent"] = round(segment["bytes"] / total_bytes * 100, 2) if total_bytes else 0.0
    media_bytes = core_bytes + context_bytes + supplementary_bytes + avatars_file_bytes
    simplified_segments: list[dict[str, Any]] = []
    if total_db_bytes > 0:
        simplified_segments.append(
            {
                "id": "database",
                "name": "Database & Indexes",
                "bytes": total_db_bytes,
                "count": core_db_count,
                "unit": "records",
                "formatted_count": "SQLite Archive & Full-Text Search Indexes",
                "formatted_size": format_bytes(total_db_bytes),
                "percent": round(total_db_bytes / total_bytes * 100, 2) if total_bytes else 0.0,
                "description": "Primary SQLite archive, full-text indexes, and active sidecars.",
            }
        )
    media_count = core_total_files + context_total_files + supplementary_count + avatars_count
    if media_bytes > 0 or media_count > 0:
        simplified_segments.append(
            {
                "id": "media",
                "name": "Media Files & Avatars",
                "bytes": media_bytes,
                "count": media_count,
                "unit": "files",
                "formatted_count": (
                    f"{core_photos + context_photos:,} photos · "
                    f"{core_videos + context_videos:,} videos · "
                    f"{supplementary_count:,} supplementary · {avatars_count:,} avatars"
                ),
                "formatted_size": format_bytes(media_bytes),
                "percent": round(media_bytes / total_bytes * 100, 2) if total_bytes else 0.0,
                "description": "Downloaded tweet media, thread attachments, and cached avatars.",
            }
        )
    simplified_segments.sort(key=lambda segment: segment["bytes"], reverse=True)
    data = {
        "total_bytes": total_bytes,
        "formatted_total": format_bytes(total_bytes),
        "database_component_bytes_estimated": True,
        "segments": segments,
        "simplified_segments": simplified_segments,
    }
    return StatsSection(
        id="storage",
        title="Storage",
        kind="storage",
        items=[
            StatItem("total", "Total", total_bytes, format="bytes"),
        ],
        data=data,
    )


STATS_SECTION_SPECS = (
    StatsSectionSpec("overview", "Overview", "cards"),
    StatsSectionSpec("collections", "Collections", "table"),
    StatsSectionSpec("archive_status", "Archive status", "archive_status"),
    StatsSectionSpec("storage", "Storage", "storage"),
    StatsSectionSpec("tagging", "Tagging & search", "tags"),
)

_COLLECTORS = {
    "overview": _collect_overview,
    "collections": _collect_collections,
    "archive_status": _collect_archive_status,
    "storage": _collect_storage,
    "tagging": _collect_tags,
}


def _collect_registered_section(
    context: _StatsContext,
    spec: StatsSectionSpec,
) -> StatsSection:
    section = _COLLECTORS[spec.id](context)
    identity = (section.id, section.title, section.kind)
    expected = (spec.id, spec.title, spec.kind)
    if identity != expected:
        raise RuntimeError(
            f"statistics collector {spec.id!r} returned {identity!r}; expected {expected!r}"
        )
    return section


def build_stats_section(store: ArchiveStore, section_id: str) -> StatsSection:
    """Collect one registered section without loading unrelated expensive sections."""
    spec = next((spec for spec in STATS_SECTION_SPECS if spec.id == section_id), None)
    if spec is None:
        raise KeyError(f"unknown statistics section: {section_id}")
    with _STATS_LOCK:
        return _collect_registered_section(_StatsContext(store), spec)


def build_stats_report(
    store: ArchiveStore,
    *,
    include: set[str] | None = None,
) -> StatsReport:
    """Collect an ordered report from the shared section registry."""
    selected = {spec.id for spec in STATS_SECTION_SPECS} if include is None else include
    unknown = selected - {spec.id for spec in STATS_SECTION_SPECS}
    if unknown:
        raise KeyError(f"unknown statistics sections: {', '.join(sorted(unknown))}")
    with _STATS_LOCK:
        context = _StatsContext(store)
        sections = [
            _collect_registered_section(context, spec)
            for spec in STATS_SECTION_SPECS
            if spec.id in selected
        ]
        overview = next((section for section in sections if section.id == "overview"), None)
        owner = (
            str(overview.data["owner_user_id"])
            if overview is not None
            else (context.archive_stats.owner_user_id or "Local Vault")
        )
        return StatsReport(
            archive_path=str(store.db_path),
            owner=owner,
            generated_at=datetime.now(tz=UTC).isoformat(),
            sections=sections,
        )
