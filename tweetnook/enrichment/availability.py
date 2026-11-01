"""When unavailable posts are retried, and which unavailability reason wins."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from tweetnook.core.utils import utc_now
from tweetnook.storage.backend import ArchiveStore
from tweetnook.storage.common import FX_SOURCE, LIVE_SOURCE
from tweetnook.storage.records import PageBuffer

PERMANENT_REASONS = frozenset({"archive_deleted", "deleted_by_author"})
REASON_CONFIDENCE = {
    "unavailable_unknown": 0,
    "not_found": 20,
    "withheld": 40,
    "protected_account": 60,
    "suspended_account": 60,
    "account_missing": 60,
    "archive_deleted": 100,
    "deleted_by_author": 100,
}

RETRY_INTERVAL_DAYS = {
    "protected_account": (7,),
    "suspended_account": (30,),
    "account_missing": (90,),
    "withheld": (90,),
    "not_found": (180,),
    "unavailable_unknown": (7, 30, 90, 180),
}
TRANSPORT_RETRY_HOURS = (1, 6, 24, 72)
# A failed lookup of a post already unavailable waits this long, however often it has failed.
# Its retry count schedules availability checks, so failures must not advance it.
UNAVAILABLE_TRANSIENT_RETRY_COUNT = 1
FOCAL_ABSENT_REASON = "focal_tweet_absent"


def as_utc_datetime(value: str | None = None) -> datetime:
    if value:
        try:
            parsed = datetime.fromisoformat(value)
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime.now(tz=UTC)


def _after(*, now: str | None, days: int = 0, hours: int = 0) -> str:
    return (as_utc_datetime(now) + timedelta(days=days, hours=hours)).isoformat()


def resurrection_retry_schedule(
    reason: str,
    retry_count: int,
    *,
    now: str | None = None,
) -> tuple[bool, str | None]:
    if reason in PERMANENT_REASONS:
        return False, None
    intervals = RETRY_INTERVAL_DAYS.get(reason, RETRY_INTERVAL_DAYS["unavailable_unknown"])
    interval = intervals[min(max(retry_count, 0), len(intervals) - 1)]
    return True, _after(now=now, days=interval)


def transient_retry_at(retry_count: int, *, now: str | None = None) -> str:
    hours = TRANSPORT_RETRY_HOURS[min(max(retry_count - 1, 0), len(TRANSPORT_RETRY_HOURS) - 1)]
    return _after(now=now, hours=hours)


def preserve_reason_confidence(
    previous_reason: str,
    previous_detail: str | None,
    new_reason: str,
    new_detail: str | None,
) -> tuple[str, str | None]:
    if (
        new_reason in PERMANENT_REASONS
        or previous_reason == "unavailable_unknown"
        or REASON_CONFIDENCE.get(new_reason, 0) >= REASON_CONFIDENCE.get(previous_reason, 0)
    ):
        return new_reason, new_detail
    return previous_reason, previous_detail


def append_absence_diagnostic(detail: str | None, diagnostic: str | None) -> str | None:
    if not diagnostic:
        return detail
    if not detail:
        return diagnostic
    if diagnostic in detail:
        return detail
    return f"{detail}\nRetry diagnostic: {diagnostic}"


def record_unavailable(
    store: ArchiveStore,
    *,
    tweet_id: str,
    operation: str,
    payload: dict[str, Any],
    http_status: int | None,
    reason: str,
    detail: str | None,
    retry_count: int = 0,
    schedule: tuple[bool, str | None] | None = None,
    now: str | None = None,
    cursor: PageBuffer | None = None,
) -> None:
    """Save an unavailable post with its evidence, scheduled by reason unless told otherwise."""

    eligible, next_retry = schedule or resurrection_retry_schedule(reason, retry_count, now=now)
    store.persist_unavailable_tweet(
        tweet_id=tweet_id,
        operation=operation,
        raw_json=payload,
        http_status=http_status,
        reason=reason,
        detail=detail,
        retry_eligible=eligible,
        next_retry_at=next_retry,
        retry_count=retry_count,
        checked_at=now,
        capture_source=FX_SOURCE if operation == "FxTwitterStatus" else LIVE_SOURCE,
        cursor=cursor,
    )


def record_transient(
    store: ArchiveStore,
    row: dict[str, Any],
    *,
    http_status: int | None,
    reason: str | None,
    detail: str | None,
    cursor: PageBuffer | None = None,
) -> None:
    """Schedule another look at a post whose lookup settled nothing.

    A post not yet enriched retries sooner each time the lookup fails, up to the last delay.
    A post already unavailable stays unavailable with its reason and retry count: a failed
    lookup isn't evidence about the post, so only an absent focal result leaves a diagnostic.
    """

    tweet_id = str(row["tweet_id"])
    retry_count = int(row.get("enrichment_retry_count") or 0)
    if row.get("enrichment_state") == "terminal_unavailable":
        diagnostic: dict[str, Any] = {}
        if reason == FOCAL_ABSENT_REASON:
            diagnostic["enrichment_detail"] = append_absence_diagnostic(
                row.get("enrichment_detail"), detail
            )
        store.update_tweet_object_enrichment(
            tweet_id,
            enrichment_state="terminal_unavailable",
            enrichment_checked_at=utc_now(),
            enrichment_http_status=http_status,
            enrichment_reason=str(row.get("enrichment_reason") or "unavailable_unknown"),
            enrichment_retry_count=retry_count,
            enrichment_next_retry_at=transient_retry_at(UNAVAILABLE_TRANSIENT_RETRY_COUNT),
            enrichment_retry_eligible=True,
            cursor=cursor,
            **diagnostic,
        )
        return
    retry_count += 1
    store.update_tweet_object_enrichment(
        tweet_id,
        enrichment_state="transient_failure",
        enrichment_checked_at=utc_now(),
        enrichment_http_status=http_status,
        enrichment_reason=reason,
        enrichment_detail=detail,
        enrichment_retry_count=retry_count,
        enrichment_next_retry_at=transient_retry_at(retry_count),
        enrichment_retry_eligible=None,
        cursor=cursor,
    )
