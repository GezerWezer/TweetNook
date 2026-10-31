"""The loop every TweetDetail pass shares: select, request, classify, record, circuit-break.

Each pass supplies its targets and how to record an outcome. Target pacing, the request
policy, classification and the focal-absence stop live here, so the passes can't drift apart.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

import httpx

from tweetnook.client.base import AdaptiveRequestPacer
from tweetnook.client.detail import TweetDetailRequester
from tweetnook.core.exceptions import (
    APIResponseError,
    AuthExpiredError,
    ConfigError,
    FeatureFlagDriftError,
    RateLimitExhaustedError,
    RepeatedFocalAbsenceError,
    StaleQueryIdError,
)
from tweetnook.core.extractor import extract_status_id_from_url
from tweetnook.core.timeline_parsing import (
    FocalResultKind,
    TimelineTweet,
    parse_tweet_detail_response,
)
from tweetnook.enrichment.availability import FOCAL_ABSENT_REASON, record_transient
from tweetnook.jobs.pipeline import PipelineReporter, current_pipeline
from tweetnook.storage.backend import ArchiveStore
from tweetnook.storage.records import PageBuffer

MAX_CONSECUTIVE_FOCAL_ABSENCES = 3
FOCAL_ABSENCE_DETAIL = "TweetDetail response did not contain an identifiable focal result."

# These say the whole run can't succeed, so no pass treats one as a failed item.
_RUN_STOP_ERRORS = (
    AuthExpiredError,
    FeatureFlagDriftError,
    RateLimitExhaustedError,
    StaleQueryIdError,
)

Target = TypeVar("Target")


def normalize_status_target(value: str, kind: str) -> str:
    """Read a tweet ID or Twitter/X status URL that a person gave a pass as its target."""

    candidate = value.strip()
    if candidate.isdigit():
        return candidate
    tweet_id = extract_status_id_from_url(candidate)
    if tweet_id:
        return tweet_id
    raise ConfigError(
        f"Unsupported {kind} target '{value}'. Use a tweet ID or Twitter/X status URL."
    )


@dataclass(frozen=True, slots=True)
class DetailAvailable:
    tweet_id: str
    tweet: TimelineTweet
    payload: dict[str, Any]
    http_status: int


@dataclass(frozen=True, slots=True)
class DetailUnavailable:
    """X said the post can't be shown: a tombstone, an empty focal entry, or an HTTP 410."""

    tweet_id: str
    reason: str
    detail: str | None
    payload: dict[str, Any]
    http_status: int


@dataclass(frozen=True, slots=True)
class DetailAbsent:
    """The response omitted the requested post, which says nothing about the post."""

    tweet_id: str
    payload: dict[str, Any]
    http_status: int


@dataclass(frozen=True, slots=True)
class DetailFailed:
    """The request failed in a way that affects this post, not the run."""

    tweet_id: str
    failure: Literal["api", "transport"]
    http_status: int | None
    error: Exception

    @property
    def detail(self) -> str:
        if self.failure == "transport":
            return f"{self.error.__class__.__name__}: {self.error}"
        return str(self.error)


DetailOutcome = DetailAvailable | DetailUnavailable | DetailAbsent | DetailFailed


@dataclass(slots=True)
class FocalAbsenceTracker:
    """Stop a run once responses keep omitting the post they were asked for.

    Only a parsed response moves the count. A failed request says nothing about the focal
    result, so it neither counts nor resets.
    """

    consecutive: int = 0

    def observe(self, kind: FocalResultKind, tweet_id: str) -> None:
        if kind != FocalResultKind.ABSENT:
            self.consecutive = 0
            return
        self.consecutive += 1
        if self.consecutive >= MAX_CONSECUTIVE_FOCAL_ABSENCES:
            raise RepeatedFocalAbsenceError(
                f"TweetDetail omitted its requested focal tweet for "
                f"{self.consecutive} consecutive responses; last target was {tweet_id}."
            )


def record_transient_outcome(
    store: ArchiveStore,
    row: dict[str, Any],
    outcome: DetailAbsent | DetailFailed,
    *,
    label: str,
    issue_key: str,
    cursor: PageBuffer | None = None,
) -> None:
    """Save a lookup that settled nothing and tell the run about it."""

    tweet_id = outcome.tweet_id
    absent = isinstance(outcome, DetailAbsent)
    record_transient(
        store,
        row,
        http_status=outcome.http_status,
        reason=FOCAL_ABSENT_REASON if absent else None,
        detail=FOCAL_ABSENCE_DETAIL if absent else outcome.detail,
        cursor=cursor,
    )
    if absent:
        message, kind = "focal tweet absent; retry scheduled", "focal-absence"
    else:
        message, kind = outcome.detail, f"{outcome.failure}-failure"
    current_pipeline().issue(
        f"{label} tweet {tweet_id}: {message}", dedupe_key=f"{issue_key}:{kind}"
    )


def detail_reporters(
    step_key: str,
    *,
    label: str,
    status: Callable[[str], None] | None = None,
) -> tuple[Callable[[str, str], None], Callable[[str], None]]:
    """Build a pass's retry and pacing messages: to its status line, or else its step."""

    pipeline: PipelineReporter = current_pipeline()

    def report(message: str) -> None:
        if status is not None:
            status(message)
        elif pipeline.active:
            pipeline.status(step_key, message, important=True)

    def retry_status(tweet_id: str, message: str) -> None:
        prefix = "detail" if status is not None else f"{label} tweet"
        report(f"{prefix} {tweet_id}: {message}")

    return retry_status, report


def _observed_kind(outcome: DetailOutcome) -> FocalResultKind | None:
    # HTTP 410 is the one DetailUnavailable with no parsed response behind it.
    if isinstance(outcome, DetailAvailable):
        return FocalResultKind.AVAILABLE
    if isinstance(outcome, DetailAbsent):
        return FocalResultKind.ABSENT
    if isinstance(outcome, DetailUnavailable) and outcome.http_status != 410:
        return FocalResultKind.EXPLICIT_UNAVAILABLE
    return None


@dataclass(slots=True)
class DetailExecutor:
    """Runs one pass's targets through the shared stages.

    ``gone_is_unavailable`` is for passes that record availability: they read HTTP 410 as
    an unavailable post, where the others treat it as a failed request.
    """

    requester: TweetDetailRequester
    pacer: AdaptiveRequestPacer
    gone_is_unavailable: bool = False
    buffer: PageBuffer | None = None
    retry_status: Callable[[str, str], None] | None = None
    pacing_status: Callable[[str], None] | None = None
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    tracker: FocalAbsenceTracker = field(default_factory=FocalAbsenceTracker)
    attempts: int = 0

    async def fetch(self, tweet_id: str) -> DetailOutcome:
        """Pace, request and classify one post. Only run-stopping errors escape."""

        await self.pacer.wait(attempted=self.attempts, sleep=self.sleep)
        self.attempts += 1
        retry_status = self.retry_status
        try:
            response = await self.requester.fetch(
                tweet_id,
                status=(
                    (lambda message: retry_status(tweet_id, message))
                    if retry_status is not None
                    else None
                ),
            )
        except _RUN_STOP_ERRORS:
            raise
        except APIResponseError as exc:
            if self.gone_is_unavailable and exc.status_code == 410:
                return DetailUnavailable(
                    tweet_id,
                    "not_found",
                    str(exc),
                    {"http_status": 410, "error": str(exc)},
                    410,
                )
            return DetailFailed(tweet_id, "api", exc.status_code, exc)
        except httpx.TransportError as exc:
            return DetailFailed(tweet_id, "transport", None, exc)
        self.pacer.observe(response, status=self.pacing_status)
        payload = response.json()
        focal = parse_tweet_detail_response(payload, tweet_id)
        if focal.tweet is not None:
            return DetailAvailable(tweet_id, focal.tweet, payload, response.status_code)
        if focal.unavailable is not None:
            return DetailUnavailable(
                tweet_id,
                focal.unavailable.reason,
                focal.unavailable.detail,
                payload,
                response.status_code,
            )
        return DetailAbsent(tweet_id, payload, response.status_code)

    def _record(
        self,
        record: Callable[[Target, DetailOutcome], None],
        target: Target,
        outcome: DetailOutcome,
    ) -> None:
        if self.buffer is None:
            record(target, outcome)
            return
        checkpoint = self.buffer.checkpoint()
        try:
            record(target, outcome)
        except Exception:
            # A half-recorded post must not reach the flush that closes the run.
            self.buffer.restore(checkpoint)
            raise

    async def run(
        self,
        targets: AsyncIterable[Target],
        *,
        tweet_id_of: Callable[[Target], str],
        record: Callable[[Target, DetailOutcome], None],
        after: Callable[[Target, DetailOutcome], None] | None = None,
    ) -> None:
        """Run every target. The absence stop comes after the outcome is recorded, so the
        response that trips it is kept, and the pass's own cleanup flushes it.

        ``after`` runs outside the recording checkpoint, for work that must not undo a post
        already recorded: flushing a batch, publishing progress, queueing related posts.
        """

        async for target in targets:
            tweet_id = tweet_id_of(target)
            outcome = await self.fetch(tweet_id)
            self._record(record, target, outcome)
            if after is not None:
                after(target, outcome)
            kind = _observed_kind(outcome)
            if kind is not None:
                self.tracker.observe(kind, tweet_id)
