"""Availability check through FxTwitter, with TweetDetail as the fallback.

Each run: every saved tweet, optionally a rolling share of context tweets (quoted, thread, and
linked tweets), and every retryable unavailable tweet are looked up through FxTwitter.
Each saved tweet's response also carries the post it quotes, so quoted posts are checked
along with it. Results:

- An unavailable tweet that FxTwitter returns is restored from FxTwitter's data as a
  placeholder; its next thread expansion during sync replaces it with live data.
- A sparse (not yet enriched) tweet that FxTwitter returns is enriched from it.
- A tweet that turns up suspended or deleted is marked unavailable. A bare 404 for a
  tweet that was available only marks it suspect; a second 404 on the next check marks
  it unavailable and keeps it due for a login lookup until one confirms it. Those marks
  wait for the end of the run: when many available tweets 404, FxTwitter is assumed
  broken and none are marked.
- Community notes are versioned; like and view counts are not kept over time.

Protected accounts answer 401 on FxTwitter, so they are left to the TweetDetail retry
schedule, which runs last within ``check.detail_budget`` lookups. With FxTwitter off the
check doesn't run; sync's resurrection step keeps retrying unavailable tweets instead.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from contextlib import aclosing
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import httpx
from rich.console import Console

from tweetnook.client import fxtwitter
from tweetnook.client.fxtwitter import (
    FxLookup,
    fx_enabled,
    fx_status_to_graphql,
    fx_timeline_tweet,
    iter_fx_statuses,
)
from tweetnook.config import AppConfig, XDGPaths
from tweetnook.extractor import classify_tweet_unavailability
from tweetnook.jobs import locked_archive_job, resolve_job_context
from tweetnook.pipeline import current_pipeline
from tweetnook.resurrection import (
    ResurrectionResult,
    _as_utc_datetime,
    preserve_reason_confidence,
    resurrection_retry_schedule,
)
from tweetnook.storage.backend import ArchiveStore, _PageBuffer
from tweetnook.utils import utc_now

CHECK_CHUNK_SIZE = 100
# Progress is published as each lookup returns, at most this often (the Web activity
# stream samples the status file every 0.25s).
PROGRESS_INTERVAL_SECONDS = 0.25
STEP_BUCKETS = ("still on X", "restored", "unavailable", "to recheck", "protected", "failed")
FX_NOT_FOUND_DETAIL = "FxTwitter returned 404."
# More bare 404s than this share of tweets that were available, once enough have been
# checked, means FxTwitter is failing rather than that many posts vanished overnight.
MASS_404_MIN_CHECKED = 50
MASS_404_MAX_SHARE = 0.05
# This many failed lookups in a row means FxTwitter is down; the rest waits for tomorrow.
MAX_CONSECUTIVE_ERRORS = fxtwitter.FX_OUTAGE_ERRORS
FX_TOMBSTONE_REASONS = {
    "suspended": "suspended_account",
    "private": "protected_account",
    "deleted": "not_found",
    "blocked": "unavailable_unknown",
    "unavailable": "unavailable_unknown",
}


@dataclass(slots=True)
class CheckResult:
    saves_checked: int = 0
    context_checked: int = 0
    unavailable_checked: int = 0
    quotes_checked: int = 0
    restored: int = 0
    enriched: int = 0
    became_unavailable: int = 0
    still_unavailable: int = 0
    protected: int = 0
    errors: int = 0
    suspect: int = 0
    notes_added: int = 0
    notes_changed: int = 0
    notes_removed: int = 0
    detail: ResurrectionResult | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def checked(self) -> int:
        return self.saves_checked + self.context_checked + self.unavailable_checked


def _step_counters(buckets: Counter[str]) -> str:
    # The activity drawer reads these as a breakdown of the tweets checked so far and
    # shows the rest of the step's total as remaining.
    return " · ".join(f"{buckets[bucket]} {bucket}" for bucket in STEP_BUCKETS)


def fx_unavailable_reason(lookup: FxLookup) -> str:
    """Map an FxTwitter tombstone or 404 onto TweetNook's unavailability reasons."""

    if lookup.kind == "not_found":
        return "unavailable_unknown"
    if lookup.kind == "protected":
        return "protected_account"
    from_message = classify_tweet_unavailability("TweetTombstone", lookup.detail)
    if from_message != "unavailable_unknown":
        return from_message
    return FX_TOMBSTONE_REASONS.get(lookup.reason or "", "unavailable_unknown")


class _CheckApplier:
    """Apply FxTwitter outcomes to the archive inside one write buffer."""

    def __init__(self, store: ArchiveStore, result: CheckResult, now: str) -> None:
        self.store = store
        self.result = result
        self.now = now
        self.buffer = _PageBuffer()
        self.checks: list[tuple[str, str, int | None, str, str | None]] = []
        # Tweets looked up directly this run, and every tweet applied this run: an embedded
        # quote of either is skipped so each tweet gets one result per run.
        self.direct_ids: set[str] = set()
        self.applied: set[str] = set()
        # Latest check outcome per tweet in the chunk being applied.
        self.prior_outcomes: dict[str, str] = {}
        self.available_seen = 0
        self.available_404s = 0
        self.mass_404 = False
        # Second 404s in a row, marked unavailable at the end of the run unless the run
        # shows FxTwitter 404ing everything; with the step that counted each one.
        self.pending_404s: list[tuple[FxLookup, str, str]] = []
        self.step_buckets: dict[str, Counter[str]] = {}

    def flush(self) -> int:
        written = len(self.buffer.records)
        if written:
            self.store.merge_rows(list(self.buffer.records.values()))
            self.buffer = _PageBuffer()
        self.store.record_tweet_checks(self.checks, checked_at=self.now)
        self.checks = []
        return written

    def _current_row(self, tweet_id: str) -> dict[str, Any] | None:
        # Rows written earlier in this buffer (a quote restored with the post quoting it)
        # are newer than what the database returns.
        return self.store._lookup_row(
            self.store._row_key_for_tweet_object(tweet_id), cursor=self.buffer
        )

    def apply(
        self, lookup: FxLookup, row: dict[str, Any] | None, scope: str, step: str = ""
    ) -> str:
        """Apply one lookup; return its bucket for the step's progress counters."""

        self.applied.add(lookup.tweet_id)
        row_key = self.store._row_key_for_tweet_object(lookup.tweet_id)
        row = self.buffer.records.get(row_key, row)
        state = (row or {}).get("enrichment_state")
        unavailable = state == "terminal_unavailable"
        sparse = state in {"pending", "transient_failure"}
        if row is not None and not unavailable and lookup.kind in {"available", "not_found"}:
            self.available_seen += 1
        outcome = lookup.kind
        reason = (
            fx_unavailable_reason(lookup)
            if lookup.kind in {"tombstone", "not_found", "protected"}
            else None
        )
        if lookup.kind == "available":
            assert lookup.status is not None
            if unavailable or sparse:
                self.store.persist_fx_status(
                    tweet=fx_timeline_tweet(fx_status_to_graphql(lookup.status)),
                    payload=lookup.payload,
                    http_status=lookup.http_status or 200,
                    cursor=self.buffer,
                )
                if unavailable:
                    self.result.restored += 1
                    outcome = "restored"
                else:
                    self.result.enriched += 1
                    outcome = "enriched"
            if scope != "quote":
                # An embedded quote's note can lag behind its own response's.
                self._record_note(lookup)
                self._apply_quote(lookup, step)
        elif lookup.kind in {"tombstone", "not_found"} and row is not None:
            if not self._apply_unavailable(lookup, row, scope, step, unavailable=unavailable):
                outcome = "suspect"
        elif lookup.kind == "protected":
            self.result.protected += 1
        elif lookup.kind == "error":
            self.result.errors += 1
        self.checks.append((lookup.tweet_id, outcome, lookup.http_status, scope, reason))
        if outcome == "restored":
            return "restored"
        if outcome == "suspect":
            return "to recheck"
        if lookup.kind == "available":
            return "still on X"
        if lookup.kind == "error":
            return "failed"
        if lookup.kind == "protected" or (reason == "protected_account" and not unavailable):
            return "protected"
        return "unavailable"

    def _record_note(self, lookup: FxLookup) -> None:
        assert lookup.status is not None
        note = lookup.status.get("community_note")
        # On a tweet's first check, the note saved with it is the version to compare with,
        # so one removed or replaced before then still shows as removed.
        baseline = (
            None
            if lookup.tweet_id in self.prior_outcomes
            else self.store.stored_community_note(lookup.tweet_id)
        )
        change = self.store.record_community_note(
            lookup.tweet_id,
            note if isinstance(note, dict) else None,
            seen_at=self.now,
            baseline=baseline if baseline and baseline[0] else None,
        )
        if change == "added":
            self.result.notes_added += 1
        elif change == "changed":
            self.result.notes_changed += 1
        elif change == "removed":
            self.result.notes_removed += 1

    def _apply_quote(self, lookup: FxLookup, step: str) -> None:
        """Check the post a tweet quotes from the copy embedded in its response."""

        assert lookup.status is not None
        quote = lookup.status.get("quote")
        if not isinstance(quote, dict) or not quote.get("id"):
            return
        quote_id = str(quote["id"])
        if quote_id in self.direct_ids or quote_id in self.applied:
            return
        row = self._current_row(quote_id)
        if row is None:
            return
        if quote.get("type") == "status":
            embedded = FxLookup(
                quote_id,
                "available",
                lookup.http_status,
                status=quote,
                payload={"code": 200, "status": quote, "quoted_by": lookup.tweet_id},
            )
        elif quote.get("type") == "tombstone":
            embedded = FxLookup(
                quote_id,
                "tombstone",
                lookup.http_status,
                status=quote,
                reason=str(quote.get("reason") or "unavailable"),
                detail=quote.get("message"),
                payload={"code": 200, "status": quote, "quoted_by": lookup.tweet_id},
            )
        else:
            return
        self.result.quotes_checked += 1
        self.apply(embedded, row, "quote", step)

    def settle_404s(self) -> Counter[str]:
        """Mark the run's second 404s unavailable unless FxTwitter looked broken.

        Returns how many were marked per step.
        """

        if (
            not self.mass_404
            and self.available_seen >= MASS_404_MIN_CHECKED
            and self.available_404s / self.available_seen > MASS_404_MAX_SHARE
        ):
            self.mass_404 = True
            self.result.warnings.append(
                f"FxTwitter returned 404 for {self.available_404s} of {self.available_seen} "
                "tweets that were available; none were marked missing."
            )
        pending, self.pending_404s = self.pending_404s, []
        marked: Counter[str] = Counter()
        if self.mass_404:
            self.result.suspect += len(pending)
            return marked
        for lookup, scope, step in pending:
            # A second 404 in a row. The fixed detail (not FxTwitter's message) marks it
            # as unconfirmed, which keeps it due for a login lookup.
            self.store.persist_unavailable_tweet(
                tweet_id=lookup.tweet_id,
                operation="FxTwitterStatus",
                raw_json=lookup.payload if isinstance(lookup.payload, dict) else {},
                http_status=lookup.http_status,
                reason="unavailable_unknown",
                detail=FX_NOT_FOUND_DETAIL,
                retry_eligible=True,
                next_retry_at=self.now,
                retry_count=0,
                checked_at=self.now,
                cursor=self.buffer,
            )
            self.result.became_unavailable += 1
            # Recorded after the provisional "suspect", so this one is kept.
            self.checks.append(
                (lookup.tweet_id, "not_found", lookup.http_status, scope, "unavailable_unknown")
            )
            marked[step] += 1
        return marked

    def _apply_unavailable(
        self,
        lookup: FxLookup,
        row: dict[str, Any],
        scope: str,
        step: str,
        *,
        unavailable: bool,
    ) -> bool:
        """Apply a tombstone or 404; return False when a 404 only left the tweet suspect."""

        reason = fx_unavailable_reason(lookup)
        detail = lookup.detail or (FX_NOT_FOUND_DETAIL if lookup.kind == "not_found" else None)
        if unavailable:
            # Already unavailable. FxTwitter agreeing never moves the login schedule (the
            # retry count and date belong to login lookups); a tombstone may only sharpen
            # the reason. A bare 404 adds nothing, and protected accounts are for the login.
            self.result.still_unavailable += 1
            previous_reason = str(row.get("enrichment_reason") or "unavailable_unknown")
            if previous_reason == "protected_account" or lookup.kind == "not_found":
                return True
            reason, detail = preserve_reason_confidence(
                previous_reason, row.get("enrichment_detail"), reason, detail
            )
            if reason == previous_reason:
                return True
            retry_count = int(row.get("enrichment_retry_count") or 0)
            eligible, scheduled = resurrection_retry_schedule(reason, retry_count, now=self.now)
            next_retry = (row.get("enrichment_next_retry_at") or scheduled) if eligible else None
            self.store.update_tweet_object_enrichment(
                lookup.tweet_id,
                enrichment_state="terminal_unavailable",
                enrichment_checked_at=row.get("enrichment_checked_at"),
                enrichment_http_status=lookup.http_status,
                enrichment_reason=reason,
                enrichment_detail=detail,
                enrichment_retry_count=retry_count,
                enrichment_next_retry_at=next_retry,
                enrichment_retry_eligible=eligible,
                cursor=self.buffer,
            )
            return True
        if reason == "protected_account":
            # FxTwitter can't see protected accounts the user may follow.
            self.result.protected += 1
            return True
        if lookup.kind == "not_found":
            # A first 404 only makes the tweet suspect; a second in a row waits for
            # settle_404s, which skips it when FxTwitter is 404ing everything.
            self.available_404s += 1
            if self.prior_outcomes.get(lookup.tweet_id) == "suspect":
                self.pending_404s.append((lookup, scope, step))
            else:
                self.result.suspect += 1
            return False
        eligible, next_retry = resurrection_retry_schedule(reason, 0, now=self.now)
        self.store.persist_unavailable_tweet(
            tweet_id=lookup.tweet_id,
            operation="FxTwitterStatus",
            raw_json=lookup.payload if isinstance(lookup.payload, dict) else {},
            http_status=lookup.http_status,
            reason=reason,
            detail=detail,
            retry_eligible=eligible,
            next_retry_at=next_retry,
            retry_count=0,
            checked_at=self.now,
            cursor=self.buffer,
        )
        self.result.became_unavailable += 1
        return True


async def run_check(
    *,
    config: AppConfig,
    paths: XDGPaths,
    saves: bool = True,
    context: bool | None = None,
    unavailable: bool = True,
    detail_budget: int | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    fx_transport: httpx.AsyncBaseTransport | None = None,
    console: Console | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> CheckResult:
    config, paths = resolve_job_context(config=config, paths=paths)
    console = console or Console(stderr=True)
    pipeline = current_pipeline()
    result = CheckResult()
    context = config.check.context_enabled if context is None else context
    budget = config.check.detail_budget if detail_budget is None else detail_budget
    now = utc_now()

    if not fx_enabled(config):
        # Without FxTwitter, sync's resurrection step owns the login retries.
        result.warnings.append("FxTwitter is off, so the check didn't run.")
        return result
    scopes = [
        (scope, step, title)
        for enabled, scope, step, title in (
            (saves, "save", "check:saves", "Saved tweets"),
            (unavailable, "unavailable", "check:unavailable", "Unavailable tweets"),
            (context, "context", "check:context", "Context tweets"),
        )
        if enabled
    ]
    if pipeline is not None:
        for _, step, title in scopes:
            pipeline.add_step(step, title, total=1, unit="tweets", rate_unit="tweets/s")
        if budget > 0:
            pipeline.add_step(
                "resurrection",
                "Unavailable tweets",
                total=1,
                unit="tweets",
                detail=f"due unavailable tweets · {budget}-request ceiling",
                rate_unit="tweets/s",
            )
    if scopes:
        async with locked_archive_job(config=config, paths=paths, console=console) as job:
            store = job.store
            store.ensure_check_tables()
            saved_ids = store.list_membership_tweet_ids() if saves else []
            unavailable_ids = (
                [row["tweet_id"] for row in store.list_retryable_unavailable_rows()]
                if unavailable
                else []
            )

            def context_ids() -> list[str]:
                # Chosen after the saves pass, so posts it already checked through the
                # saved tweets quoting them count as checked this run.
                if not context:
                    return []
                # A weekly/monthly check must cover more of the cycle than a daily one.
                # Use the longest month so a short month never leaves the cycle undersized.
                interval_days = {"daily": 1, "weekly": 7, "monthly": 31}[config.check.cadence]
                per_run = math.ceil(
                    store.count_context_tweets() * interval_days / config.check.context_days
                )
                checked_before = (
                    _as_utc_datetime(now) - timedelta(days=max(config.check.context_days - 1, 0))
                ).isoformat()
                return store.list_context_check_tweet_ids(
                    limit=per_run, checked_before=checked_before
                )

            applier = _CheckApplier(store, result, now)
            applier.direct_ids.update(saved_ids, unavailable_ids)
            client = fxtwitter.build_fx_client(config, transport=fx_transport)
            try:
                seen: set[str] = set()
                selectors = {
                    "save": lambda: saved_ids,
                    "unavailable": lambda: unavailable_ids,
                    "context": context_ids,
                }
                for index, (scope, step, title) in enumerate(scopes):
                    ids = [tweet_id for tweet_id in selectors[scope]() if tweet_id not in seen]
                    seen.update(ids)
                    applier.direct_ids.update(ids)
                    reachable = await _check_scope(
                        applier,
                        client,
                        config=config,
                        ids=ids,
                        scope=scope,
                        step=step,
                        title=title,
                        pipeline=pipeline,
                        job=job,
                        sleep=sleep,
                    )
                    if not reachable:
                        if pipeline is not None:
                            for _, remaining_step, _ in scopes[index + 1 :]:
                                pipeline.skip_step(remaining_step, "FxTwitter stopped answering")
                        break
                for step, count in applier.settle_404s().items():
                    buckets = applier.step_buckets[step]
                    buckets["to recheck"] -= count
                    buckets["unavailable"] += count
                    if pipeline is not None:
                        pipeline.revise_step(step, _step_counters(buckets))
            finally:
                await client.aclose()
                written = applier.flush()
                if written:
                    job.mark_dirty(rows=written, batches=1)

    if budget > 0:
        result.detail = await _run_detail_fallback(
            config=config,
            paths=paths,
            budget=budget,
            transport=transport,
            console=console,
            sleep=sleep,
            result=result,
        )
    return result


async def _check_scope(
    applier: _CheckApplier,
    client: httpx.AsyncClient,
    *,
    config: AppConfig,
    ids: list[str],
    scope: str,
    step: str,
    title: str,
    pipeline: Any,
    job: Any,
    sleep: Callable[[float], Awaitable[None]],
) -> bool:
    """Check one scope; return False when FxTwitter stopped answering."""

    result = applier.result
    buckets: Counter[str] = Counter()
    applier.step_buckets[step] = buckets
    if pipeline is not None:
        pipeline.add_step(step, title, total=max(len(ids), 1), unit="tweets", rate_unit="tweets/s")
        if not ids:
            pipeline.skip_step(step, "nothing due")
            return True
        pipeline.start_step(step, activity="Checking through FxTwitter")
    done = 0
    last_progress = 0.0
    consecutive_errors = 0
    for start in range(0, len(ids), CHECK_CHUNK_SIZE):
        chunk = ids[start : start + CHECK_CHUNK_SIZE]
        rows = applier.store.get_tweet_object_rows(chunk)
        applier.prior_outcomes = applier.store.get_tweet_check_outcomes(chunk)
        checked = 0
        lookups = iter_fx_statuses(
            client, chunk, concurrency=config.fxtwitter.concurrency, sleep=sleep
        )
        async with aclosing(lookups):
            async for lookup in lookups:
                buckets[applier.apply(lookup, rows.get(lookup.tweet_id), scope, step)] += 1
                checked += 1
                done += 1
                consecutive_errors = consecutive_errors + 1 if lookup.kind == "error" else 0
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    break
                now = time.monotonic()
                if pipeline is not None and now - last_progress >= PROGRESS_INTERVAL_SECONDS:
                    last_progress = now
                    pipeline.update_step(step, completed=done, counters=_step_counters(buckets))
        if scope == "save":
            result.saves_checked += checked
        elif scope == "context":
            result.context_checked += checked
        else:
            result.unavailable_checked += checked
        written = applier.flush()
        if written:
            job.mark_dirty(rows=written, batches=1)
        if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
            message = (
                f"FxTwitter failed {consecutive_errors} lookups in a row; "
                "the rest of the check waits for the next run."
            )
            result.warnings.append(message)
            if pipeline is not None:
                pipeline.fail_step(step, message)
            return False
        if pipeline is not None:
            pipeline.update_step(step, completed=done, counters=_step_counters(buckets))
    if pipeline is not None:
        pipeline.complete_step(step, _step_counters(buckets))
    return True


async def _run_detail_fallback(
    *,
    config: AppConfig,
    paths: XDGPaths,
    budget: int,
    transport: httpx.AsyncBaseTransport | None,
    console: Console,
    sleep: Callable[[float], Awaitable[None]],
    result: CheckResult,
) -> ResurrectionResult | None:
    """Check what FxTwitter couldn't settle (protected accounts, bare 404s) with the login."""

    from tweetnook.auth import resolve_auth_bundle
    from tweetnook.exceptions import AuthExpiredError, AuthResolutionError
    from tweetnook.resurrection import resurrect_due_tweets

    pipeline = current_pipeline()
    try:
        auth_bundle = resolve_auth_bundle(config)
    except AuthResolutionError as exc:
        result.warnings.append(f"Skipped login lookups: {exc}")
        if pipeline is not None:
            pipeline.skip_step("resurrection", "login credentials being unavailable")
        return None
    try:
        return await resurrect_due_tweets(
            budget=budget,
            config=config,
            paths=paths,
            auth_bundle=auth_bundle,
            transport=transport,
            console=console,
            sleep=sleep,
        )
    except AuthExpiredError as exc:
        # FxTwitter results are already saved; an expired login only skips this part.
        result.warnings.append(f"Skipped login lookups: {exc}")
        if pipeline is not None:
            pipeline.skip_step("resurrection", "the login having expired")
        return None
