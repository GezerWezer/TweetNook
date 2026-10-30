"""Collect the canonical archive statistics report."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tweetnook.stats.models import (
    StatsReport,
    StatsSection,
    StatsSectionSpec,
    TableColumn,
)
from tweetnook.storage import ArchiveStore
from tweetnook.storage.report_facts import HealthFacts, StatisticsFacts

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


@dataclass(slots=True)
class _StatsContext:
    store: ArchiveStore
    facts: StatisticsFacts


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
    return store.enrichment_incomplete_count()


def get_latest_sync_at(store: ArchiveStore) -> str | None:
    """Return the latest sync timestamp without collecting archive statistics."""
    return store.latest_sync_at()


def _backfill_status(cursor: str | None, incomplete: bool) -> str:
    if incomplete and cursor:
        return "resume older"
    if incomplete:
        return "incomplete"
    if cursor:
        return "saved cursor"
    return "none saved"


def _unavailable_breakdown(
    facts: HealthFacts,
) -> dict[str, Any]:
    counts_by_reason = facts.reason_counts
    archive_total, unavailable_total = facts.archive_total, facts.unavailable_total
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


def _collect_overview(context: _StatsContext, spec: StatsSectionSpec) -> StatsSection:
    facts = context.facts.overview
    assert facts is not None
    unique_posts = facts.unique_posts
    collection_memberships = facts.collection_memberships
    articles, raw_captures = facts.articles, facts.raw_captures
    media_rows, urls, profiles = facts.media_rows, facts.urls, facts.profiles
    oldest_post, newest_post = facts.oldest_post, facts.newest_post
    latest_capture, latest_sync = facts.latest_capture, facts.latest_sync
    archive_tweets, missing_archive_tweets = facts.archive_tweets, facts.missing_archive_tweets
    owner = context.facts.owner or "Local Vault"
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
        id=spec.id,
        title=spec.title,
        data=data,
    )


def _collect_collections(context: _StatsContext, spec: StatsSectionSpec) -> StatsSection:
    rows: list[dict[str, Any]] = []
    names = {"bookmark": "Bookmarks", "like": "Likes", "tweet": "Authored Tweets"}
    for collection in context.facts.collections or []:
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
        id=spec.id,
        title=spec.title,
        columns=[
            TableColumn("collection", "Collection"),
            TableColumn("count", "Tweets", format="integer", align="right"),
            TableColumn("oldest", "First", format="datetime"),
            TableColumn("newest", "Last", format="datetime"),
            TableColumn("last_synced", "Last sync", format="datetime"),
            TableColumn("backfill_status", "Backfill"),
        ],
        rows=rows,
    )


def _collect_archive_status(context: _StatsContext, spec: StatsSectionSpec) -> StatsSection:
    facts = context.facts.health
    assert facts is not None
    stats = facts.stats
    unavailable = _unavailable_breakdown(facts)
    incomplete = facts.incomplete
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
        id=spec.id,
        title=spec.title,
        data=data,
    )


def _collect_tags(context: _StatsContext, spec: StatsSectionSpec) -> StatsSection:
    facts = context.facts.tagging
    assert facts is not None
    eligible_tweets, tagged_tweets = facts.eligible_tweets, facts.tagged_tweets
    unique_tags, total_tag_instances, top_tags = (
        facts.unique_tags,
        facts.total_tag_instances,
        facts.top_tags,
    )
    coverage_pct = _percentage(tagged_tweets, eligible_tweets)
    avg_tags = (
        round(total_tag_instances / facts.usage_tagged_tweets, 1)
        if facts.usage_tagged_tweets
        else 0.0
    )
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
        id=spec.id,
        title=spec.title,
        data=data,
    )


def _collect_storage(context: _StatsContext, spec: StatsSectionSpec) -> StatsSection:
    store = context.store
    segments: list[dict[str, Any]] = []
    facts = context.facts.storage
    assert facts is not None
    core_tweet_ids, media_rows = facts.core_tweet_ids, facts.media_rows
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
    core_db_count, core_db_bytes = facts.payloads["core"]
    threads_count, threads_bytes = facts.payloads["threads"]
    articles_count, articles_bytes = facts.payloads["articles"]
    tags_count, tags_bytes = facts.payloads["tags"]
    _tweet_count, profiles_bytes = facts.payloads["profiles"]
    profiles_count = facts.profiles_count
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
        id=spec.id,
        title=spec.title,
        data=data,
    )


STATS_SECTION_SPECS = (
    StatsSectionSpec("overview", "Overview"),
    StatsSectionSpec("collections", "Collections"),
    StatsSectionSpec("archive_status", "Archive status"),
    StatsSectionSpec("storage", "Storage"),
    StatsSectionSpec("tagging", "Tagging & search"),
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
    return _COLLECTORS[spec.id](context, spec)


def build_stats_section(store: ArchiveStore, section_id: str) -> StatsSection:
    """Collect one registered section without loading unrelated expensive sections."""
    spec = next((spec for spec in STATS_SECTION_SPECS if spec.id == section_id), None)
    if spec is None:
        raise KeyError(f"unknown statistics section: {section_id}")
    with store.read_snapshot() as reader:
        context = _StatsContext(reader, reader.statistics_facts({section_id}))
    return _collect_registered_section(context, spec)


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
    with store.read_snapshot() as reader:
        context = _StatsContext(reader, reader.statistics_facts(selected))
    sections = [
        _collect_registered_section(context, spec)
        for spec in STATS_SECTION_SPECS
        if spec.id in selected
    ]
    return StatsReport(
        archive_path=str(store.db_path),
        owner=context.facts.owner or "Local Vault",
        generated_at=datetime.now(tz=UTC).isoformat(),
        sections=sections,
    )
