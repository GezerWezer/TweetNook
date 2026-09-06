"""Bounded, reason-aware TweetDetail resurrection scheduling."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from rich.console import Console

from tweetnook.auth import ResolvedAuthBundle, resolve_auth_bundle
from tweetnook.client.base import AdaptiveRequestPacer, build_async_client
from tweetnook.client.timelines import (
    MAX_CONSECUTIVE_FOCAL_ABSENCES,
    FocalResultKind,
    build_tweet_detail_url,
    fetch_page,
    parse_tweet_detail_response,
)
from tweetnook.config import AppConfig, XDGPaths
from tweetnook.exceptions import (
    APIResponseError,
    AuthExpiredError,
    FeatureFlagDriftError,
    RateLimitExhaustedError,
    RepeatedFocalAbsenceError,
    StaleQueryIdError,
)
from tweetnook.jobs import locked_archive_job, resolve_job_context
from tweetnook.pipeline import current_pipeline
from tweetnook.query_ids import QueryIdStore, refresh_query_ids
from tweetnook.storage.backend import ArchiveStore, _PageBuffer
from tweetnook.utils import resolve_query_ids, utc_now

DEFAULT_RESURRECTION_BUDGET = 200
ACCOUNT_PROBE_LIMIT = 5
ACCOUNT_PROBE_SUCCESS_THRESHOLD = 3
ACCOUNT_BOOST_LIMIT = 15
DETAIL_WRITE_BATCH = 100

PERMANENT_REASONS = frozenset({"archive_deleted", "deleted_by_author"})
ACCOUNT_LEVEL_REASONS = frozenset({"protected_account", "suspended_account", "account_missing"})
AMBIGUOUS_REASONS = frozenset({"withheld", "not_found", "unavailable_unknown"})
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


@dataclass(slots=True)
class ResurrectionResult:
    attempted: int = 0
    resurrected: int = 0
    still_unavailable: int = 0
    transient_failures: int = 0
    remaining_due: int = 0
    account_probes: int = 0
    account_boosts: int = 0
    account_rows_prioritized: int = 0
    warnings: list[str] = field(default_factory=list)


def _as_utc_datetime(value: str | None = None) -> datetime:
    if value:
        try:
            parsed = datetime.fromisoformat(value)
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime.now(tz=UTC)


def _after(*, now: str | None, days: int = 0, hours: int = 0) -> str:
    return (_as_utc_datetime(now) + timedelta(days=days, hours=hours)).isoformat()


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


def _append_absence_diagnostic(detail: str | None, diagnostic: str | None) -> str | None:
    if not diagnostic:
        return detail
    if not detail:
        return diagnostic
    if diagnostic in detail:
        return detail
    return f"{detail}\nRetry diagnostic: {diagnostic}"


def select_weighted_resurrection_candidates(
    store: ArchiveStore,
    *,
    budget: int,
    now: str | None = None,
) -> list[dict[str, Any]]:
    if budget <= 0:
        return []
    quotas = (
        (frozenset({"protected_account"}), int(budget * 0.60)),
        (
            frozenset({"suspended_account", "account_missing"}),
            int(budget * 0.25),
        ),
    )
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    for reasons, quota in quotas:
        for row in store.list_due_resurrection_tweets(
            reasons=set(reasons),
            exclude_tweet_ids=selected_ids,
            limit=quota,
            now=now,
        ):
            tweet_id = row.get("tweet_id")
            if isinstance(tweet_id, str) and tweet_id not in selected_ids:
                selected.append(row)
                selected_ids.add(tweet_id)
    ambiguous_quota = max(budget - sum(quota for _, quota in quotas), 0)
    for row in store.list_due_resurrection_tweets(
        reasons=set(AMBIGUOUS_REASONS),
        exclude_tweet_ids=selected_ids,
        limit=ambiguous_quota,
        now=now,
    ):
        tweet_id = row.get("tweet_id")
        if isinstance(tweet_id, str) and tweet_id not in selected_ids:
            selected.append(row)
            selected_ids.add(tweet_id)
    if len(selected) < budget:
        for row in store.list_due_resurrection_tweets(
            exclude_tweet_ids=selected_ids,
            limit=budget - len(selected),
            now=now,
        ):
            tweet_id = row.get("tweet_id")
            if not isinstance(tweet_id, str) or tweet_id in selected_ids:
                continue
            selected.append(row)
            selected_ids.add(tweet_id)
            if len(selected) >= budget:
                break
    return selected[:budget]


def _flush_buffer(store: ArchiveStore, buffer: _PageBuffer) -> int:
    if not buffer.records:
        return 0
    count = len(buffer.records)
    store.merge_rows(list(buffer.records.values()))
    buffer.records.clear()
    buffer.pending_tweets.clear()
    buffer.existing_rows.clear()
    return count


async def resurrect_due_tweets(
    *,
    budget: int = DEFAULT_RESURRECTION_BUDGET,
    config: AppConfig,
    paths: XDGPaths,
    auth_bundle: ResolvedAuthBundle | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    console: Console | None = None,
    status: Callable[[str], None] | None = None,
    sleep: Callable[[float], Any] = asyncio.sleep,
) -> ResurrectionResult:
    config, paths = resolve_job_context(config=config, paths=paths)
    console = console or Console(stderr=True)
    pipeline = current_pipeline()
    result = ResurrectionResult()
    step_key = "resurrection"
    if pipeline is not None:
        pipeline.add_step(
            step_key,
            "Resurrection",
            total=1,
            unit="tweets",
            detail=f"due unavailable tweets · {budget}-request ceiling",
            rate_unit="tweets/s",
        )
    if budget <= 0:
        async with locked_archive_job(config=config, paths=paths, console=console) as job:
            result.remaining_due = job.store.count_due_resurrection_tweets()
        if pipeline is not None:
            pipeline.skip_step(step_key, "the request budget being zero")
        return result

    async with locked_archive_job(config=config, paths=paths, console=console) as job:
        store = job.store
        candidates = select_weighted_resurrection_candidates(store, budget=budget)
        if not candidates:
            result.remaining_due = store.count_due_resurrection_tweets()
            if pipeline is not None:
                pipeline.skip_step(step_key, "no due retryable unavailable tweets")
            return result

        if pipeline is not None:
            pipeline.add_step(
                step_key,
                "Resurrection",
                total=len(candidates),
                unit="tweets",
                detail=(f"{len(candidates)} due selected"),
                rate_unit="tweets/s",
            )
            pipeline.start_step(
                step_key,
                activity=(
                    "Resolving Twitter/X authentication"
                    if auth_bundle is None
                    else "Resolving the TweetDetail operation ID"
                ),
                counters=(
                    f"{len(candidates)} due selected · 0 checked · 0 returned · "
                    "0 unavailable · 0 transient"
                ),
            )

        if auth_bundle is None:
            auth_bundle = resolve_auth_bundle(config)
            if pipeline is not None:
                pipeline.status(step_key, "Resolving the TweetDetail operation ID")

        query_store = QueryIdStore(paths)
        query_ids = await resolve_query_ids(
            query_store,
            ["TweetDetail"],
            force_refresh=not query_store.is_fresh(),
            transport=transport,
        )
        if pipeline is not None:
            pipeline.status(
                step_key,
                f"Rechecking unavailable tweet {candidates[0]['tweet_id']}",
            )
        client = build_async_client(auth_bundle, timeout=config.sync.timeout, transport=transport)
        queue = deque(candidates)
        queued_ids = {row["tweet_id"] for row in candidates if isinstance(row.get("tweet_id"), str)}
        processed_ids: set[str] = set()
        probe_ids: dict[str, set[str]] = {}
        probe_successes: dict[str, int] = {}
        boosted_authors: set[str] = set()
        probed_authors: set[str] = set()
        pacer = AdaptiveRequestPacer(config.sync.detail_delay)
        buffer = _PageBuffer()
        buffered_attempts = 0
        consecutive_focal_absences = 0

        def prepend(rows: list[dict[str, Any]]) -> list[str]:
            added: list[dict[str, Any]] = []
            for row in rows:
                tweet_id = row.get("tweet_id")
                if (
                    not isinstance(tweet_id, str)
                    or tweet_id in queued_ids
                    or tweet_id in processed_ids
                ):
                    continue
                queued_ids.add(tweet_id)
                added.append(row)
            for row in reversed(added):
                queue.appendleft(row)
            return [row["tweet_id"] for row in added]

        def flush() -> None:
            nonlocal buffered_attempts
            written = _flush_buffer(store, buffer)
            if written:
                job.mark_dirty(rows=written, batches=1)
            buffered_attempts = 0

        try:
            while queue and result.attempted < budget:
                row = queue.popleft()
                tweet_id = str(row["tweet_id"])
                queued_ids.discard(tweet_id)
                if tweet_id in processed_ids:
                    continue
                processed_ids.add(tweet_id)
                result.attempted += 1
                buffered_attempts += 1
                if pipeline is not None:
                    planned_total = min(budget, result.attempted + len(queue))
                    pipeline.update_step(
                        step_key,
                        completed=result.attempted - 1,
                        total=max(planned_total, result.attempted),
                        activity=f"Rechecking unavailable tweet {tweet_id}",
                        counters=(
                            f"{result.attempted - 1} checked · {result.resurrected} returned · "
                            f"{result.still_unavailable} unavailable · "
                            f"{result.transient_failures} transient"
                        ),
                    )
                await pacer.wait(attempted=result.attempted - 1, sleep=sleep)

                previous_reason = str(row.get("enrichment_reason") or "unavailable_unknown")
                previous_detail = (
                    str(row["enrichment_detail"])
                    if row.get("enrichment_detail") is not None
                    else None
                )
                previous_retry_count = int(row.get("enrichment_retry_count") or 0)
                availability_retry_count = previous_retry_count + 1

                async def refresh_once(tweet_id: str = tweet_id) -> str:
                    refreshed = await refresh_query_ids(
                        query_store, operations=["TweetDetail"], client=client
                    )
                    query_ids.update(refreshed)
                    return build_tweet_detail_url(query_ids["TweetDetail"], tweet_id)

                available_author_id: str | None = None
                absence_error: RepeatedFocalAbsenceError | None = None
                write_checkpoint = buffer.checkpoint()
                try:
                    response = await fetch_page(
                        client,
                        build_tweet_detail_url(query_ids["TweetDetail"], tweet_id),
                        config.sync,
                        max_retries=config.sync.detail_max_retries,
                        backoff_base=config.sync.detail_backoff_base,
                        refresh_once=refresh_once,
                        status=(
                            (
                                lambda message, tweet_id=tweet_id: pipeline.status(
                                    step_key,
                                    f"Resurrection tweet {tweet_id}: {message}",
                                    important=True,
                                )
                            )
                            if pipeline is not None
                            else status
                        ),
                        sleep=sleep,
                    )
                    pacer.observe(
                        response,
                        status=(
                            status
                            if status is not None
                            else (
                                lambda message: pipeline.status(step_key, message, important=True)
                                if pipeline is not None
                                else None
                            )
                        ),
                    )
                    payload = response.json()
                    focal = parse_tweet_detail_response(payload, tweet_id)
                    if focal.kind == FocalResultKind.AVAILABLE and focal.tweet is not None:
                        consecutive_focal_absences = 0
                        available_author_id = focal.tweet.author_id
                        store.persist_tweet_detail(
                            tweet=focal.tweet,
                            raw_json=payload,
                            http_status=response.status_code,
                            cursor=buffer,
                        )
                        result.resurrected += 1
                    elif focal.kind == FocalResultKind.EXPLICIT_UNAVAILABLE:
                        consecutive_focal_absences = 0
                        assert focal.unavailable is not None
                        reason, detail = preserve_reason_confidence(
                            previous_reason,
                            previous_detail,
                            focal.unavailable.reason,
                            focal.unavailable.detail,
                        )
                        eligible, next_retry = resurrection_retry_schedule(
                            reason, availability_retry_count
                        )
                        store.persist_unavailable_tweet(
                            tweet_id=tweet_id,
                            operation="TweetDetailResurrection",
                            raw_json=payload,
                            http_status=response.status_code,
                            reason=reason,
                            detail=detail,
                            retry_eligible=eligible,
                            next_retry_at=next_retry,
                            retry_count=availability_retry_count,
                            cursor=buffer,
                        )
                        result.still_unavailable += 1
                    else:
                        assert focal.kind == FocalResultKind.ABSENT
                        assert focal.unavailable is not None
                        buffer.restore(write_checkpoint)
                        store.append_raw_capture(
                            "TweetDetailResurrection",
                            tweet_id,
                            None,
                            response.status_code,
                            payload,
                            cursor=buffer,
                        )
                        store.update_tweet_object_enrichment(
                            tweet_id,
                            enrichment_state="terminal_unavailable",
                            enrichment_checked_at=utc_now(),
                            enrichment_http_status=response.status_code,
                            enrichment_reason=previous_reason,
                            enrichment_detail=_append_absence_diagnostic(
                                previous_detail,
                                focal.unavailable.detail,
                            ),
                            enrichment_retry_count=previous_retry_count,
                            enrichment_next_retry_at=transient_retry_at(1),
                            enrichment_retry_eligible=True,
                            cursor=buffer,
                        )
                        result.transient_failures += 1
                        consecutive_focal_absences += 1
                        if pipeline is not None:
                            pipeline.issue(
                                f"Resurrection tweet {tweet_id}: focal tweet absent; "
                                "retry scheduled",
                                dedupe_key="resurrection:focal-absence",
                            )
                        if consecutive_focal_absences >= MAX_CONSECUTIVE_FOCAL_ABSENCES:
                            absence_error = RepeatedFocalAbsenceError(
                                f"TweetDetail omitted its requested focal tweet for "
                                f"{consecutive_focal_absences} consecutive responses; "
                                f"last target was {tweet_id}."
                            )
                except APIResponseError as exc:
                    if isinstance(
                        exc,
                        StaleQueryIdError
                        | AuthExpiredError
                        | FeatureFlagDriftError
                        | RateLimitExhaustedError,
                    ):
                        raise
                    if exc.status_code == 410:
                        reason, detail = preserve_reason_confidence(
                            previous_reason,
                            previous_detail,
                            "not_found",
                            str(exc),
                        )
                        eligible, next_retry = resurrection_retry_schedule(
                            reason, availability_retry_count
                        )
                        store.persist_unavailable_tweet(
                            tweet_id=tweet_id,
                            operation="TweetDetailResurrection",
                            raw_json={"http_status": 410, "error": str(exc)},
                            http_status=410,
                            reason=reason,
                            detail=detail,
                            retry_eligible=eligible,
                            next_retry_at=next_retry,
                            retry_count=availability_retry_count,
                            cursor=buffer,
                        )
                        result.still_unavailable += 1
                    else:
                        store.update_tweet_object_enrichment(
                            tweet_id,
                            enrichment_state="terminal_unavailable",
                            enrichment_checked_at=utc_now(),
                            enrichment_http_status=exc.status_code,
                            enrichment_reason=previous_reason,
                            enrichment_retry_count=previous_retry_count,
                            enrichment_next_retry_at=transient_retry_at(1),
                            enrichment_retry_eligible=True,
                            cursor=buffer,
                        )
                        result.transient_failures += 1
                        if pipeline is not None:
                            pipeline.issue(
                                f"Resurrection tweet {tweet_id}: {exc}",
                                dedupe_key="resurrection:api-failure",
                            )
                except httpx.TransportError as exc:
                    store.update_tweet_object_enrichment(
                        tweet_id,
                        enrichment_state="terminal_unavailable",
                        enrichment_checked_at=utc_now(),
                        enrichment_http_status=None,
                        enrichment_reason=previous_reason,
                        enrichment_retry_count=previous_retry_count,
                        enrichment_next_retry_at=transient_retry_at(1),
                        enrichment_retry_eligible=True,
                        cursor=buffer,
                    )
                    result.transient_failures += 1
                    if pipeline is not None:
                        pipeline.issue(
                            f"Resurrection tweet {tweet_id}: {exc.__class__.__name__}: {exc}",
                            dedupe_key="resurrection:transport-failure",
                        )
                except (KeyboardInterrupt, asyncio.CancelledError):
                    raise
                except Exception:
                    buffer.restore(write_checkpoint)
                    raise

                if absence_error is not None:
                    flush()
                    raise absence_error

                probe_author = next(
                    (author for author, ids in probe_ids.items() if tweet_id in ids), None
                )
                if probe_author is not None:
                    if available_author_id == probe_author:
                        probe_successes[probe_author] = probe_successes.get(probe_author, 0) + 1
                    if (
                        probe_successes.get(probe_author, 0) >= ACCOUNT_PROBE_SUCCESS_THRESHOLD
                        and probe_author not in boosted_authors
                    ):
                        boosted_authors.add(probe_author)
                        boosted = store.list_same_author_resurrection_tweets(
                            probe_author,
                            exclude_tweet_ids=processed_ids | queued_ids,
                            limit=ACCOUNT_BOOST_LIMIT,
                            due_only=False,
                        )
                        added = prepend(boosted)
                        persisted_due = store.mark_author_resurrection_due(
                            probe_author,
                            exclude_tweet_ids=processed_ids,
                        )
                        if persisted_due:
                            job.mark_dirty(rows=persisted_due, batches=1)
                        if added or persisted_due:
                            result.account_boosts += 1
                        result.account_rows_prioritized += len(added)
                        if pipeline is not None and added:
                            pipeline.status(
                                step_key,
                                f"Prioritized {len(added)} same-author recovery candidates",
                                important=True,
                            )

                if (
                    available_author_id is not None
                    and available_author_id.isdigit()
                    and previous_reason in ACCOUNT_LEVEL_REASONS
                    and available_author_id not in probed_authors
                ):
                    probed_authors.add(available_author_id)
                    probes = store.list_same_author_resurrection_tweets(
                        available_author_id,
                        exclude_tweet_ids=processed_ids | queued_ids,
                        limit=ACCOUNT_PROBE_LIMIT,
                        due_only=False,
                    )
                    added = prepend(probes)
                    probe_ids[available_author_id] = set(added)
                    probes_marked_due = store.mark_tweets_resurrection_due(set(added))
                    if probes_marked_due:
                        job.mark_dirty(rows=probes_marked_due, batches=1)
                    result.account_probes += len(added)

                if pipeline is not None:
                    planned_total = min(budget, result.attempted + len(queue))
                    pipeline.update_step(
                        step_key,
                        completed=result.attempted,
                        total=max(planned_total, result.attempted),
                        counters=(
                            f"{result.attempted} checked · {result.resurrected} returned · "
                            f"{result.still_unavailable} unavailable · "
                            f"{result.transient_failures} transient · "
                            f"{result.account_probes} account probes"
                        ),
                    )

                if buffered_attempts >= DETAIL_WRITE_BATCH:
                    flush()
        finally:
            try:
                await client.aclose()
            finally:
                flush()

        result.remaining_due = store.count_due_resurrection_tweets()
        if pipeline is not None:
            pipeline.complete_step(
                step_key,
                f"{result.attempted} checked · {result.resurrected} returned · "
                f"{result.still_unavailable} unavailable · "
                f"{result.transient_failures} transient · {result.remaining_due} still due",
            )
    return result
