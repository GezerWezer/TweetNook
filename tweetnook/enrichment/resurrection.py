"""Bounded, reason-aware TweetDetail resurrection scheduling."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from rich.console import Console

from tweetnook.auth import ResolvedAuthBundle, resolve_auth_bundle
from tweetnook.client.base import AdaptiveRequestPacer, build_async_client
from tweetnook.client.detail import TweetDetailRequester, resolve_detail_query_ids
from tweetnook.config import AppConfig, XDGPaths
from tweetnook.core.timeline_parsing import parse_tweet_detail_tweets
from tweetnook.enrichment.availability import (
    preserve_reason_confidence,
    record_unavailable,
)
from tweetnook.enrichment.detail import (
    DetailAbsent,
    DetailAvailable,
    DetailExecutor,
    DetailOutcome,
    DetailUnavailable,
    detail_reporters,
    record_transient_outcome,
)
from tweetnook.jobs.context import locked_archive_job, resolve_job_context
from tweetnook.jobs.pipeline import current_pipeline
from tweetnook.storage.backend import ArchiveStore
from tweetnook.storage.records import PageBuffer

DEFAULT_RESURRECTION_BUDGET = 200
ACCOUNT_PROBE_LIMIT = 5
ACCOUNT_PROBE_SUCCESS_THRESHOLD = 3
ACCOUNT_BOOST_LIMIT = 15
DETAIL_WRITE_BATCH = 100
_DETAIL_OPERATION = "TweetDetailResurrection"

ACCOUNT_LEVEL_REASONS = frozenset({"protected_account", "suspended_account", "account_missing"})
AMBIGUOUS_REASONS = frozenset({"withheld", "not_found", "unavailable_unknown"})


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


def _pipeline_outcomes(result: ResurrectionResult, *, final: bool = False) -> str:
    parts: list[str] = []
    if final or result.resurrected:
        parts.append(f"{result.resurrected} restored")
    if result.still_unavailable:
        parts.append(f"{result.still_unavailable} still unavailable")
    if result.transient_failures:
        parts.append(f"{result.transient_failures} retry later")
    if result.account_probes:
        parts.append(f"{result.account_probes} account probes")
    if final and result.remaining_due:
        parts.append(f"{result.remaining_due} still due")
    return " · ".join(parts)


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


@dataclass(slots=True)
class _ResurrectionRun:
    """One restoration pass: its queue, write buffer and same-author probes."""

    store: ArchiveStore
    budget: int
    queue: deque[dict[str, Any]]
    result: ResurrectionResult
    step_key: str = "resurrection"
    buffer: PageBuffer = field(default_factory=PageBuffer)
    buffered_attempts: int = 0
    queued_ids: set[str] = field(default_factory=set)
    processed_ids: set[str] = field(default_factory=set)
    probe_ids: dict[str, set[str]] = field(default_factory=dict)
    probe_successes: dict[str, int] = field(default_factory=dict)
    boosted_authors: set[str] = field(default_factory=set)
    probed_authors: set[str] = field(default_factory=set)

    def flush(self) -> None:
        self.store.flush(self.buffer)
        self.buffered_attempts = 0

    def _publish_progress(self, completed: int, **fields: Any) -> None:
        pipeline = current_pipeline()
        if not pipeline.active:
            return
        planned_total = min(self.budget, self.result.attempted + len(self.queue))
        pipeline.update_step(
            self.step_key,
            completed=completed,
            total=max(planned_total, self.result.attempted),
            counters=_pipeline_outcomes(self.result),
            **fields,
        )

    async def select_targets(self) -> AsyncIterator[dict[str, Any]]:
        result = self.result
        while self.queue and result.attempted < self.budget:
            row = self.queue.popleft()
            tweet_id = str(row["tweet_id"])
            self.queued_ids.discard(tweet_id)
            if tweet_id in self.processed_ids:
                continue
            self.processed_ids.add(tweet_id)
            # An earlier response in this run may have returned this post as thread context.
            # Selection's copy is stale then, and acting on it would undo the restoration.
            current = self.store.current_resurrection_candidates(
                [tweet_id], cursor=self.buffer
            ).get(tweet_id)
            if current is None:
                continue
            result.attempted += 1
            self.buffered_attempts += 1
            self._publish_progress(result.attempted - 1, activity="Checking an unavailable tweet")
            yield current

    def record(self, row: dict[str, Any], outcome: DetailOutcome) -> None:
        tweet_id = outcome.tweet_id
        if isinstance(outcome, DetailAvailable):
            self.store.persist_thread_detail(
                focal_tweet_id=tweet_id,
                tweets=parse_tweet_detail_tweets(outcome.payload),
                raw_json=outcome.payload,
                http_status=outcome.http_status,
                cursor=self.buffer,
            )
            self.result.resurrected += 1
        elif isinstance(outcome, DetailUnavailable):
            reason, detail = preserve_reason_confidence(
                str(row.get("enrichment_reason") or "unavailable_unknown"),
                row.get("enrichment_detail"),
                outcome.reason,
                outcome.detail,
            )
            record_unavailable(
                self.store,
                tweet_id=tweet_id,
                operation=_DETAIL_OPERATION,
                payload=outcome.payload,
                http_status=outcome.http_status,
                reason=reason,
                detail=detail,
                retry_count=int(row.get("enrichment_retry_count") or 0) + 1,
                cursor=self.buffer,
            )
            self.result.still_unavailable += 1
        else:
            if isinstance(outcome, DetailAbsent):
                self.store.append_raw_capture(
                    _DETAIL_OPERATION,
                    tweet_id,
                    None,
                    outcome.http_status,
                    outcome.payload,
                    cursor=self.buffer,
                )
            record_transient_outcome(
                self.store,
                row,
                outcome,
                label="Resurrection",
                issue_key="resurrection",
                cursor=self.buffer,
            )
            self.result.transient_failures += 1

    def _prepend(self, rows: list[dict[str, Any]]) -> list[str]:
        """Queue related posts ahead of the rest, unless this run already restored them."""

        ids = [row["tweet_id"] for row in rows if isinstance(row.get("tweet_id"), str)]
        candidates = self.store.current_resurrection_candidates(ids, cursor=self.buffer)
        added: list[dict[str, Any]] = []
        for row in rows:
            tweet_id = row.get("tweet_id")
            if (
                tweet_id not in candidates
                or tweet_id in self.queued_ids
                or tweet_id in self.processed_ids
            ):
                continue
            self.queued_ids.add(tweet_id)
            added.append(row)
        for row in reversed(added):
            self.queue.appendleft(row)
        return [row["tweet_id"] for row in added]

    def _boost_probed_author(self, author_id: str) -> None:
        """Three restored probes mean the account is back: prioritize its other posts."""

        self.boosted_authors.add(author_id)
        boosted = self.store.list_same_author_resurrection_tweets(
            author_id,
            exclude_tweet_ids=self.processed_ids | self.queued_ids,
            limit=ACCOUNT_BOOST_LIMIT,
            due_only=False,
        )
        added = self._prepend(boosted)
        persisted_due = self.store.mark_author_resurrection_due(
            author_id,
            exclude_tweet_ids=self.processed_ids,
        )
        if added or persisted_due:
            self.result.account_boosts += 1
        self.result.account_rows_prioritized += len(added)
        if added:
            current_pipeline().status(
                self.step_key,
                f"Prioritized {len(added)} same-author recovery candidates",
                important=True,
            )

    def _probe_author(self, author_id: str) -> None:
        """A restored post from an account-level loss is a reason to try that account again."""

        self.probed_authors.add(author_id)
        probes = self.store.list_same_author_resurrection_tweets(
            author_id,
            exclude_tweet_ids=self.processed_ids | self.queued_ids,
            limit=ACCOUNT_PROBE_LIMIT,
            due_only=False,
        )
        added = self._prepend(probes)
        self.probe_ids[author_id] = set(added)
        self.store.mark_tweets_resurrection_due(set(added))
        self.result.account_probes += len(added)

    def after_record(self, row: dict[str, Any], outcome: DetailOutcome) -> None:
        tweet_id = outcome.tweet_id
        author_id = outcome.tweet.author_id if isinstance(outcome, DetailAvailable) else None
        probe_author = next(
            (author for author, ids in self.probe_ids.items() if tweet_id in ids), None
        )
        if probe_author is not None:
            if author_id == probe_author:
                self.probe_successes[probe_author] = self.probe_successes.get(probe_author, 0) + 1
            if (
                self.probe_successes.get(probe_author, 0) >= ACCOUNT_PROBE_SUCCESS_THRESHOLD
                and probe_author not in self.boosted_authors
            ):
                self._boost_probed_author(probe_author)
        if (
            author_id is not None
            and author_id.isdigit()
            and row.get("enrichment_reason") in ACCOUNT_LEVEL_REASONS
            and author_id not in self.probed_authors
        ):
            self._probe_author(author_id)
        self._publish_progress(self.result.attempted)
        if self.buffered_attempts >= DETAIL_WRITE_BATCH:
            self.flush()


async def resurrect_due_tweets(
    *,
    budget: int = DEFAULT_RESURRECTION_BUDGET,
    config: AppConfig,
    paths: XDGPaths,
    auth_bundle: ResolvedAuthBundle | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    console: Console | None = None,
    sleep: Callable[[float], Any] = asyncio.sleep,
) -> ResurrectionResult:
    config, paths = resolve_job_context(config=config, paths=paths)
    console = console or Console(stderr=True)
    pipeline = current_pipeline()
    result = ResurrectionResult()
    step_key = "resurrection"
    pipeline.add_step(
        step_key,
        "Unavailable tweets",
        total=1,
        unit="tweets",
        detail=f"due unavailable tweets · {budget}-request ceiling",
        rate_unit="tweets/s",
    )
    if budget <= 0:
        async with locked_archive_job(config=config, paths=paths, console=console) as job:
            result.remaining_due = job.store.count_due_resurrection_tweets()
        pipeline.skip_step(step_key, "the request budget being zero")
        return result

    async with locked_archive_job(config=config, paths=paths, console=console) as job:
        store = job.store
        candidates = select_weighted_resurrection_candidates(store, budget=budget)
        if not candidates:
            result.remaining_due = store.count_due_resurrection_tweets()
            pipeline.skip_step(step_key, "no due retryable unavailable tweets")
            return result

        pipeline.add_step(
            step_key,
            "Unavailable tweets",
            total=len(candidates),
            unit="tweets",
            detail=(f"{len(candidates)} due selected"),
            rate_unit="tweets/s",
        )
        pipeline.start_step(
            step_key,
            activity=(
                "Connecting to Twitter/X" if auth_bundle is None else "Preparing recovery checks"
            ),
        )

        if auth_bundle is None:
            auth_bundle = resolve_auth_bundle(config)
            pipeline.status(step_key, "Preparing recovery checks")

        query_store, query_ids = await resolve_detail_query_ids(paths, transport=transport)
        pipeline.status(
            step_key,
            "Checking an unavailable tweet",
        )
        client = build_async_client(auth_bundle, timeout=config.sync.timeout, transport=transport)
        run = _ResurrectionRun(
            store=store,
            budget=budget,
            queue=deque(candidates),
            result=result,
            queued_ids={
                row["tweet_id"] for row in candidates if isinstance(row.get("tweet_id"), str)
            },
        )
        retry_status, pacing_status = detail_reporters(step_key, label="Resurrection")
        try:
            await DetailExecutor(
                requester=TweetDetailRequester(
                    client, query_store, query_ids, config.sync, sleep=sleep
                ),
                pacer=AdaptiveRequestPacer(config.sync.detail_delay),
                gone_is_unavailable=True,
                buffer=run.buffer,
                retry_status=retry_status,
                pacing_status=pacing_status,
                sleep=sleep,
            ).run(
                run.select_targets(),
                tweet_id_of=lambda row: str(row["tweet_id"]),
                record=run.record,
                after=run.after_record,
            )
        finally:
            try:
                await client.aclose()
            finally:
                run.flush()

        result.remaining_due = store.count_due_resurrection_tweets()
        pipeline.complete_step(
            step_key,
            _pipeline_outcomes(result, final=True),
            metrics={
                "restored": result.resurrected,
                "still_unavailable": result.still_unavailable,
                "retry_later": result.transient_failures,
                "remaining_due": result.remaining_due,
            },
        )
    return result
