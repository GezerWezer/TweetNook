"""Archive statistics endpoints backed by the shared report registry."""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends

from tweetnook.stats import (
    build_stats_report,
    build_stats_section,
    get_enrichment_incomplete_count,
    get_latest_sync_at,
)
from tweetnook.storage import ArchiveStore
from tweetnook.web.deps import require_store, verify_credentials
from tweetnook.web.stats_cache import CachedStatsReport, web_stats_cache

router = APIRouter(
    prefix="/api/stats",
    tags=["stats"],
    dependencies=[Depends(verify_credentials)],
)


def _format_ts(ts: int | float | str | None) -> str | None:
    if not ts:
        return None
    if isinstance(ts, str):
        try:
            parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            from tweetnook.storage.backend import _parse_created_at

            parsed = _parse_created_at(ts)
            if parsed is None:
                return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
    else:
        parsed = datetime.fromtimestamp(ts, tz=UTC)
    return parsed.astimezone(UTC).strftime("%b %d, %Y")


def _format_stats_timestamp(raw: str | None) -> str:
    if not raw:
        return "-"
    try:
        from tweetnook.storage.backend import _parse_created_at

        parsed = _parse_created_at(raw)
        if parsed is None:
            parsed = datetime.fromisoformat(raw)
        local_dt = parsed.astimezone() if parsed.tzinfo is not None else parsed
        date_part = local_dt.strftime("%b %d, %Y")
        time_part = local_dt.strftime("%I:%M %p").lower().lstrip("0")
        return f"{date_part} {time_part}"
    except Exception:
        return raw


def _legacy_summary(data: dict[str, Any]) -> dict[str, Any]:
    result = dict(data)
    for key in ("oldest_post", "newest_post", "latest_sync"):
        result[key] = _format_ts(result[key])
    result.pop("collection_memberships", None)
    result.pop("raw_captures", None)
    result.pop("latest_capture", None)
    return result


def _legacy_collections(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            **row,
            "oldest": _format_stats_timestamp(row["oldest"]),
            "newest": _format_stats_timestamp(row["newest"]),
            "last_synced": _format_stats_timestamp(row["last_synced"]),
        }
        for row in rows
    ]


def _legacy_health(data: dict[str, Any]) -> dict[str, Any]:
    result = dict(data)
    result.pop("missing_tweet_objects", None)
    result.pop("pending_thread_memberships", None)
    result.pop("pending_linked_statuses", None)
    result["enrichment"] = dict(result["enrichment"])
    result["enrichment"].pop("transient_due", None)
    result["enrichment"].pop("transient_delayed", None)
    return result


def _cached_snapshot_payload(cached: CachedStatsReport) -> dict[str, Any]:
    report = cached.report
    return {
        "generated_at": report.generated_at,
        "age_seconds": round(cached.age_seconds, 1),
        "stale": cached.stale,
        "refreshing": cached.refreshing,
        "refresh_failed": cached.refresh_failed,
        "summary": _legacy_summary(report.section("overview").data),
        "collections": _legacy_collections(report.section("collections").rows),
        "health": _legacy_health(report.section("archive_status").data),
        "storage": report.section("storage").data,
        "tags": report.section("tagging").data,
    }


def _cached_status_payload(cached: CachedStatsReport) -> dict[str, Any]:
    """Return refresh metadata without serializing the cached report again."""
    return {
        "generated_at": cached.report.generated_at,
        "age_seconds": round(cached.age_seconds, 1),
        "stale": cached.stale,
        "refreshing": cached.refreshing,
        "refresh_failed": cached.refresh_failed,
    }


@router.get("/report")
def get_stats_report(
    store: ArchiveStore = Depends(require_store),  # noqa: B008 - FastAPI dependency
) -> dict[str, Any]:
    """Return the complete ordered report consumed by both interfaces."""
    return build_stats_report(store).as_dict()


@router.get("/snapshot")
def get_cached_stats_snapshot(
    revalidate: bool = True,
    store: ArchiveStore = Depends(require_store),  # noqa: B008 - FastAPI dependency
) -> dict[str, Any]:
    """Return the Web modal snapshot, revalidating stale data in the background."""
    return _cached_snapshot_payload(web_stats_cache.get(store, revalidate=revalidate))


@router.get("/status")
def get_cached_stats_status(
    store: ArchiveStore = Depends(require_store),  # noqa: B008 - FastAPI dependency
) -> dict[str, Any]:
    """Return only cache state for inexpensive browser refresh polling."""
    return _cached_status_payload(web_stats_cache.get(store, revalidate=False))


@router.post("/refresh")
def refresh_cached_stats(
    store: ArchiveStore = Depends(require_store),  # noqa: B008 - FastAPI dependency
) -> dict[str, Any]:
    """Start a manual refresh while retaining the currently displayed snapshot."""
    return _cached_status_payload(web_stats_cache.refresh(store))


@router.get("/enrichment-incomplete")
def get_enrichment_incomplete(
    store: ArchiveStore = Depends(require_store),  # noqa: B008 - FastAPI dependency
) -> dict[str, int]:
    """Return the inexpensive status-banner count."""
    return {"incomplete": get_enrichment_incomplete_count(store)}


@router.get("/latest-sync")
def get_latest_sync(
    store: ArchiveStore = Depends(require_store),  # noqa: B008 - FastAPI dependency
) -> dict[str, str | None]:
    """Return only the inexpensive page-header sync timestamp."""
    return {"latest_sync": _format_ts(get_latest_sync_at(store))}


@router.get("/summary")
def get_stats_summary(
    store: ArchiveStore = Depends(require_store),  # noqa: B008 - FastAPI dependency
) -> dict[str, Any]:
    """Return the legacy overview payload."""
    return _legacy_summary(build_stats_section(store, "overview").data)


@router.get("/collections")
def get_stats_collections(
    store: ArchiveStore = Depends(require_store),  # noqa: B008 - FastAPI dependency
) -> list[dict[str, Any]]:
    """Return the legacy collections payload."""
    return _legacy_collections(build_stats_section(store, "collections").rows)


@router.get("/health")
def get_stats_health(
    store: ArchiveStore = Depends(require_store),  # noqa: B008 - FastAPI dependency
) -> dict[str, Any]:
    """Return the legacy archive-status payload."""
    return _legacy_health(build_stats_section(store, "archive_status").data)


@router.get("/tags")
def get_stats_tags(
    store: ArchiveStore = Depends(require_store),  # noqa: B008 - FastAPI dependency
) -> dict[str, Any]:
    """Return the legacy tagging payload."""
    return build_stats_section(store, "tagging").data
