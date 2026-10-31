from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from pydantic import ValidationError
from rich.console import Console
from rich.text import Text

from tweetnook.config import AppConfig, XDGPaths
from tweetnook.jobs.activity_history import new_run_id
from tweetnook.jobs.outcome import Outcome
from tweetnook.jobs.pipeline import current_pipeline
from tweetnook.jobs.resource_failure import raise_if_storage_exhausted
from tweetnook.storage.backend import ArchiveStore
from tweetnook.tagging.accounting import (
    AIUsageLedger,
    failed_usage_record,
    new_request_id,
    usage_from_generate_content,
)
from tweetnook.tagging.failures import (
    is_rejected_free_payload,
    is_retryable_free_error,
    provider_error_code,
    retry_delay_seconds,
    safe_error,
)
from tweetnook.tagging.paid import PaidRunState, SearchPolicy, tag_paid_tweet
from tweetnook.tagging.preparation import MediaLoader, PreparedBatch, prepare_batch
from tweetnook.tagging.results import MediaTagResult, TextTagResult, parse_result_list
from tweetnook.tagging.rpd import RpdStatus, get_rpd_status, reserve_rpd_request
from tweetnook.tagging.run import (
    TaggingOutcome,
    TaggingRun,
    TagRequest,
    announce_batch,
    persist,
    system_prompt,
)
from tweetnook.tagging.sdk import (
    AutomatedTaggingUnavailable,
    close_tagging_resources,
    load_gemini,
)

try:  # Optional extra: importing this module must remain safe in base installations.
    from google import genai
    from google.genai import types
    from PIL import Image
except ImportError:  # pragma: no cover - exercised in a dependency-isolated subprocess test
    genai = None
    types = None
    Image = None


class _FreeMediaTagResult(MediaTagResult):
    id: str


class _FreeTextTagResult(TextTagResult):
    id: str


# Keep the wave-oriented execution path so a future backfill mode can raise
# this internal ceiling without changing tagging orchestration.  It is not a
# configuration setting and normal Paid processing is sequential for now.
PAID_CONCURRENCY = 1


@dataclass(frozen=True, slots=True)
class TaggingRunResult:
    processed: int = 0
    tagged: int = 0
    # Provider attempts actually sent, including retries and split children.
    batches: int = 0
    outcome: Outcome = field(default=Outcome.NO_WORK, compare=False)


_THINKING_BUDGETS = {
    "none": 0,
    "low": 1024,
    "medium": 4096,
    "high": 8192,
}
_FREE_REQUEST_TIMES: dict[str, deque[float]] = defaultdict(deque)


async def _wait_for_free_rpm(model: str, requests_per_minute: int) -> None:
    timestamps = _FREE_REQUEST_TIMES[model.casefold()]
    while True:
        now = time.monotonic()
        while timestamps and now - timestamps[0] >= 60:
            timestamps.popleft()
        if len(timestamps) < requests_per_minute:
            timestamps.append(now)
            return
        await asyncio.sleep(max(60 - (now - timestamps[0]), 0.01))


def _ensure_optional_dependencies() -> bool:
    global Image, genai, types
    if genai is not None and types is not None and Image is not None:
        return True
    try:
        genai, types, Image = load_gemini()
    except AutomatedTaggingUnavailable:
        return False
    return True


def _thinking_config(level: str) -> Any | None:
    normalized = level.strip().lower()
    fields = getattr(types.ThinkingConfig, "model_fields", {})
    if "thinking_level" in fields:
        return types.ThinkingConfig(thinking_level=normalized)
    if "thinking_budget" in fields:
        return types.ThinkingConfig(
            thinking_budget=_THINKING_BUDGETS.get(normalized, _THINKING_BUDGETS["high"])
        )
    return None


def _print_rpd_exhausted(run: TaggingRun, status: RpdStatus) -> None:
    model = run.model_name
    reset_at = status.reset_at.strftime("%Y-%m-%d %H:%M %Z")
    run.console.print(
        Text(
            f"Gemini daily request limit reached for {model} "
            f"({status.used}/{status.limit}). No further requests will be sent until "
            f"{reset_at}.",
            style="yellow",
        )
    )


def _print_rpd_storage_error(run: TaggingRun, error: BaseException) -> None:
    run.console.print(
        Text(
            "Could not update Gemini daily request usage; request not sent: "
            f"{safe_error(error, run.tag_config.api_key)}",
            style="red",
        )
    )


def _quote_targets(store: ArchiveStore, tweet_ids: list[str]) -> dict[str, str]:
    try:
        return store.tagging_quote_targets(tweet_ids)
    except Exception:
        return {}


def _top_archive_tags(store: ArchiveStore, limit: int = 50) -> list[str]:
    try:
        return [str(row["tag"]) for row in store.get_tag_counts(limit=limit) if row.get("tag")]
    except Exception:
        return []


def _run_id() -> str:
    pipeline = current_pipeline()
    return str(pipeline.run_id or new_run_id())


def _new_run(
    store: ArchiveStore,
    config: AppConfig,
    paths: XDGPaths,
    console: Console,
    request: TagRequest,
    *,
    ledger: AIUsageLedger | None = None,
    search_policy: SearchPolicy | None = None,
    paid_state: PaidRunState | None = None,
) -> TaggingRun:
    tag_config = config.tagging
    ledger = ledger or AIUsageLedger(paths.ai_usage_dir)
    return TaggingRun(
        store=store,
        tag_config=tag_config,
        paths=paths,
        console=console,
        model_name=request.model_override or tag_config.model,
        ledger=ledger,
        run_identifier=_run_id(),
        search_policy=search_policy
        or SearchPolicy(
            ledger,
            tag_config.search_safety_reserve,
            tag_config.api_mode == "paid" and tag_config.google_search,
            unknown_policy=tag_config.search_unknown_policy,
            max_unknown_attempts=tag_config.max_unknown_search_requests,
        ),
        paid_state=paid_state or PaidRunState(),
        dry_run=request.dry_run,
        previews=request.preview_results,
    )


def _free_generation_config(run: TaggingRun, batch: PreparedBatch) -> Any:
    result_schema = _FreeMediaTagResult if batch.content_type == "media" else _FreeTextTagResult
    config_args: dict[str, Any] = {
        "http_options": types.HttpOptions(
            timeout=900_000,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
        "response_mime_type": "application/json",
        "max_output_tokens": 8192,
        "response_schema": list[result_schema],
    }
    thinking_config = _thinking_config(run.tag_config.thinking_level)
    if thinking_config is not None:
        config_args["thinking_config"] = thinking_config
    return types.GenerateContentConfig(**config_args)


@dataclass(slots=True)
class _FreeSend:
    """How one Free batch's provider attempts ended."""

    response: Any | None = None
    rejected: bool = False
    stop: TaggingOutcome | None = None


def _free_quota_stop(run: TaggingRun, check: Callable[..., RpdStatus]) -> TaggingOutcome | None:
    """Return why no Free request may be sent: spent allowance, or usage that can't be counted."""
    try:
        status = check(run.store, model=run.model_name, limit=run.tag_config.free_rpd)
    except Exception as error:
        raise_if_storage_exhausted(error)
        _print_rpd_storage_error(run, error)
        return TaggingOutcome.failed("Gemini daily request usage could not be updated", halted=True)
    if status.allowed:
        return None
    _print_rpd_exhausted(run, status)
    return TaggingOutcome.paused(f"the Gemini daily request limit was reached for {run.model_name}")


async def _send_free_request(
    run: TaggingRun,
    client: Any,
    batch: PreparedBatch,
    existing_tags: Sequence[str],
) -> _FreeSend:
    tag_config = run.tag_config
    request_id = new_request_id()
    contents = batch.free_parts(system_prompt(run, batch, existing_tags, search=False))
    metadata = batch.request_metadata(existing_tag_count=len(existing_tags), tag_config=tag_config)
    generation_config = _free_generation_config(run, batch)
    for attempt in range(5):
        stop = _free_quota_stop(run, reserve_rpd_request)
        if stop is not None:
            return _FreeSend(stop=stop)
        await _wait_for_free_rpm(run.model_name, tag_config.free_rpm)
        started = time.monotonic()
        run.free_attempts += 1
        try:
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=run.model_name,
                contents=contents,
                config=generation_config,
            )
        except Exception as error:
            _record_free_failure(run, batch, request_id, started, attempt + 1, metadata)
            code = provider_error_code(error)
            if is_retryable_free_error(error, code) and attempt < 4:
                delay = retry_delay_seconds(attempt)
                run.console.print(
                    f"[yellow]Gemini API busy. Retrying in {delay} seconds "
                    f"(Attempt {attempt + 1}/5)...[/yellow]"
                )
                await asyncio.sleep(delay)
                continue
            if is_rejected_free_payload(error, code):
                return _FreeSend(rejected=True)
            message = safe_error(error, tag_config.api_key)
            run.console.print(f"[red]Gemini Tagging Failed: {message}[/red]")
            return _FreeSend(stop=TaggingOutcome.failed(message))
        _record_free_success(run, batch, response, request_id, started, attempt + 1, metadata)
        return _FreeSend(response=response)
    raise AssertionError("the final attempt always returns")


def _record_free_success(
    run: TaggingRun,
    batch: PreparedBatch,
    response: Any,
    request_id: str,
    started: float,
    attempt_number: int,
    metadata: dict[str, Any],
) -> None:
    try:
        run.ledger.append(
            usage_from_generate_content(
                response,
                run_id=run.run_identifier,
                request_id=request_id,
                requested_model=run.model_name,
                content_type=batch.content_type,
                tweet_ids=batch.tweet_ids,
                latency_ms=round((time.monotonic() - started) * 1000),
                attempt=attempt_number,
                thinking_level=run.tag_config.thinking_level,
                request_metadata=metadata,
                reserved_cost_usd="0",
            )
        )
    except Exception as error:
        raise_if_storage_exhausted(error)
        run.console.print(
            "[yellow]Gemini Free usage could not be written to the AI ledger: "
            f"{safe_error(error, run.tag_config.api_key)}[/yellow]"
        )


def _record_free_failure(
    run: TaggingRun,
    batch: PreparedBatch,
    request_id: str,
    started: float,
    attempt_number: int,
    metadata: dict[str, Any],
) -> None:
    try:
        run.ledger.append(
            failed_usage_record(
                run_id=run.run_identifier,
                request_id=request_id,
                api_mode="free",
                model=run.model_name,
                service_tier="free",
                content_type=batch.content_type,
                tweet_ids=batch.tweet_ids,
                latency_ms=round((time.monotonic() - started) * 1000),
                search_enabled=False,
                attempt=attempt_number,
                thinking_level=run.tag_config.thinking_level,
                request_metadata=metadata,
                reserved_cost_usd="0",
            )
        )
    except Exception as error:
        raise_if_storage_exhausted(error)


async def _split_free_batch(
    run: TaggingRun, client: Any, batch: PreparedBatch, reason: str
) -> TaggingOutcome:
    first, second = batch.halves()
    run.console.print(
        f"[yellow]{reason} for a Free batch of {len(batch.tweets)}. Splitting into "
        f"batches of {len(first.tweets)} and {len(second.tweets)}...[/yellow]"
    )
    total = TaggingOutcome()
    for child in (first, second):
        # Each child reads the tags its sibling just saved, and reuses the prepared media.
        total = total.plus(await _tag_free_batch(run, client, child, _top_archive_tags(run.store)))
        if total.halted:
            break
    return total


def _free_results(run: TaggingRun, batch: PreparedBatch, raw_results: list[Any]) -> list[Any]:
    model = _FreeMediaTagResult if batch.content_type == "media" else _FreeTextTagResult
    parsed: list[tuple[str, MediaTagResult | TextTagResult]] = []
    for raw in raw_results:
        try:
            result = model.model_validate(raw)
            parsed.append((result.id, result))
        except (ValidationError, TypeError, ValueError):
            run.console.print(
                "[yellow]Skipping a Gemini result that did not match the batch schema.[/yellow]"
            )
    return parsed


async def _tag_free_batch(
    run: TaggingRun, client: Any, batch: PreparedBatch, existing_tags: Sequence[str]
) -> TaggingOutcome:
    announce_batch(run, batch)
    can_split = len(batch.tweets) > 1
    send = await _send_free_request(run, client, batch, existing_tags)
    if send.stop is not None:
        return send.stop
    if send.rejected:
        if can_split:
            return await _split_free_batch(run, client, batch, "Gemini rejected the payload")
        if not run.dry_run:
            run.store.mark_tagging_failed(batch.tweet_ids)
        return TaggingOutcome.failed("Gemini rejected the payload")
    response_text = getattr(send.response, "text", None)
    if not response_text:
        if can_split:
            return await _split_free_batch(run, client, batch, "Gemini returned an empty response")
        run.console.print("[red]Gemini returned an empty response.[/red]")
        if not run.dry_run:
            run.store.mark_tagging_failed(batch.tweet_ids)
        return TaggingOutcome.failed("Gemini returned an empty response")
    try:
        raw_results = parse_result_list(response_text)
    except (TypeError, ValueError):
        reason = "Gemini returned invalid structured output"
        if can_split:
            return await _split_free_batch(run, client, batch, reason)
        run.console.print(f"[red]{reason}.[/red]")
        return TaggingOutcome.failed(reason)
    return TaggingOutcome.from_results(
        persist(run, batch, _free_results(run, batch, raw_results), existing_tags)
    )


async def _prepare_and_execute(
    run: TaggingRun,
    client: Any,
    tweet_ids: list[str],
    content_type: Literal["media", "text"],
    quote_targets: dict[str, str],
    existing_tags: Sequence[str],
    resources: tuple[list[Any], list[Any]],
) -> TaggingOutcome:
    tag_config = run.tag_config
    images, uploads = resources
    context_ids = list(dict.fromkeys([*tweet_ids, *quote_targets.values()]))
    all_media, tweet_objs = run.store.tagging_context(context_ids)
    try:
        batch = await prepare_batch(
            tweet_ids,
            content_type,
            quote_targets=quote_targets,
            all_media=all_media,
            tweet_objs=tweet_objs,
            loader=MediaLoader(
                client=client,
                data_dir=run.paths.data_dir,
                max_media_size_mb=tag_config.max_media_size_mb,
                upload_all=run.paid,
                image_module=Image,
                images=images,
                uploads=uploads,
                console=run.console,
                api_key=tag_config.api_key,
            ),
            with_ids=not run.paid,
        )
        if not any(tweet_id in tweet_objs for tweet_id in tweet_ids):
            run.console.print(
                "[yellow]No stored tweet objects found for the selected IDs.[/yellow]"
            )
            return TaggingOutcome()
        if not batch.tweets:
            run.console.print("[yellow]No loadable media found for the selected tweets.[/yellow]")
            if not run.dry_run and run.paid:
                run.store.defer_tagging_failure(tweet_ids)
            return TaggingOutcome()
        execute = tag_paid_tweet if run.paid else _tag_free_batch
        return await execute(run, client, batch, existing_tags)
    except Exception as error:
        # A full disk is the run's failure, not this batch's. Report it as what it is.
        raise_if_storage_exhausted(error)
        message = safe_error(error, tag_config.api_key)
        run.console.print(f"[red]Gemini Tagging Failed: {message}[/red]")
        return TaggingOutcome.failed(message)


async def _tag_scope(
    run: TaggingRun,
    tweet_ids: list[str],
    content_type: Literal["media", "text"],
    quote_targets: dict[str, str],
    existing_tags: Sequence[str],
) -> TaggingOutcome:
    """Own one client and everything loaded through it, for one request and its children."""
    tag_config = run.tag_config
    if run.paid_state.stop_reason:
        return TaggingOutcome.paused(run.paid_state.stop_reason)
    if not run.paid:
        stop = _free_quota_stop(run, get_rpd_status)
        if stop is not None:
            return stop
    try:
        client = genai.Client(
            api_key=tag_config.api_key,
            http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1)),
        )
    except Exception as error:
        message = safe_error(error, tag_config.api_key)
        run.console.print(f"[red]Could not initialize Gemini: {message}[/red]")
        return TaggingOutcome.failed(message, halted=True)

    images: list[Any] = []
    uploads: list[Any] = []
    attempts_before = run.attempts_sent
    try:
        outcome = await _prepare_and_execute(
            run, client, tweet_ids, content_type, quote_targets, existing_tags, (images, uploads)
        )
    finally:
        await close_tagging_resources(client, images, uploads)
    return replace(outcome, attempts=run.attempts_sent - attempts_before)


async def _tag_group(
    run: TaggingRun,
    tweet_ids: list[str],
    content_type: Literal["media", "text"],
    quote_targets: dict[str, str],
    existing_tags: Sequence[str],
) -> TaggingOutcome:
    if not run.paid or len(tweet_ids) == 1:
        return await _tag_scope(run, tweet_ids, content_type, quote_targets, existing_tags)
    # Keep the generic wave path, but Paid processing is intentionally sequential until a
    # future feature opts into a higher internal cap. Each tweet is its own request, and
    # prepares nothing until its turn, so a pause doesn't waste uploads.
    semaphore = asyncio.Semaphore(PAID_CONCURRENCY)

    async def paid_one(tweet_id: str) -> TaggingOutcome:
        async with semaphore:
            targets = {tweet_id: quote_targets[tweet_id]} if tweet_id in quote_targets else {}
            return await _tag_scope(run, [tweet_id], content_type, targets, existing_tags)

    total = TaggingOutcome()
    for outcome in await asyncio.gather(*(paid_one(tweet_id) for tweet_id in tweet_ids)):
        total = total.plus(outcome)
    return total


async def tag_tweets(
    store: ArchiveStore,
    config: AppConfig,
    paths: XDGPaths,
    console: Console,
    request: TagRequest,
    *,
    ledger: AIUsageLedger | None = None,
    search_policy: SearchPolicy | None = None,
    paid_state: PaidRunState | None = None,
) -> TaggingOutcome:
    """Tag specific tweets: plan their requests, then run each through its stages."""
    pipeline = current_pipeline()
    if pipeline.has_step("tagging") and not request.dry_run:
        console = pipeline.capture_console("tagging")  # type: ignore[assignment]
    tag_config = config.tagging
    if not tag_config.enabled or not tag_config.api_key:
        console.print("[yellow]Tagging is disabled or missing API key.[/yellow]")
        return TaggingOutcome()
    if not _ensure_optional_dependencies():
        console.print(
            "[yellow]Automated tagging is not installed; sync will continue. Install "
            "tweetnook[automated-tagging] to enable it.[/yellow]"
        )
        return TaggingOutcome()
    tweet_ids = request.tweet_ids
    if not tweet_ids:
        return TaggingOutcome()
    if request.dry_run and len(tweet_ids) != 1:
        raise ValueError("Test tag generation requires exactly one tweet")
    run = _new_run(
        store,
        config,
        paths,
        console,
        request,
        ledger=ledger,
        search_policy=search_policy,
        paid_state=paid_state,
    )
    if run.paid_state.stop_reason:
        return TaggingOutcome.paused(run.paid_state.stop_reason)
    quote_targets = (
        _quote_targets(store, tweet_ids)
        if request.quoted_tweet_ids is None
        else request.quoted_tweet_ids
    )
    total = TaggingOutcome()
    for content_type, ids in _plan_groups(store, request, quote_targets):
        targets = {
            tweet_id: quote_targets[tweet_id] for tweet_id in ids if tweet_id in quote_targets
        }
        # Each group reads the tags the previous one just saved.
        existing_tags = tuple(_top_archive_tags(store))
        total = total.plus(await _tag_group(run, ids, content_type, targets, existing_tags))
        if total.halted:
            break
    return total


def _plan_groups(
    store: ArchiveStore, request: TagRequest, quote_targets: dict[str, str]
) -> list[tuple[Literal["media", "text"], list[str]]]:
    """A request sends one content type, so a mixed selection becomes homogeneous groups."""
    if request.content_type is not None:
        return [(request.content_type, request.tweet_ids)]
    content_types = store.tagging_content_types(request.tweet_ids, quote_targets)
    groups: list[tuple[Literal["media", "text"], list[str]]] = []
    for kind in ("media", "text"):
        ids = [tweet_id for tweet_id in request.tweet_ids if content_types[tweet_id] == kind]
        if ids:
            groups.append((kind, ids))
    return groups


async def tag_media_tweets(
    store: ArchiveStore,
    config: AppConfig,
    paths: XDGPaths,
    console: Console,
    tweet_ids: list[str],
    model_override: str | None = None,
    *,
    _search_policy: SearchPolicy | None = None,
    _ledger: AIUsageLedger | None = None,
    _paid_state: PaidRunState | None = None,
    **options: Any,
) -> int:
    """Tag specific tweets for a caller that only needs the number tagged."""
    outcome = await tag_tweets(
        store,
        config,
        paths,
        console,
        TagRequest(tweet_ids, model_override, **options),
        ledger=_ledger,
        search_policy=_search_policy,
        paid_state=_paid_state,
    )
    return outcome.tagged


def finish_tagging_step(
    pipeline: Any, outcome: TaggingOutcome, *, processed: int, note: str = ""
) -> None:
    """Report one outcome the same way for every caller.

    Tagging is an optional follow-up, so a failure stays recoverable here: a sync that saved
    its posts didn't fail because Gemini did. A command that only tags decides for itself.
    """
    summary = f"{outcome.tagged} tagged · {outcome.attempts} requests{note}"
    metrics = {"tagged": outcome.tagged, "requests": outcome.attempts}
    reason = outcome.reason or "tagging could not finish"
    if outcome.status is Outcome.PAUSED:
        pipeline.update_step("tagging", completed=processed)
        pipeline.fail_step(
            "tagging", f"Paused: {reason}", metrics=metrics, reason=reason, paused=True
        )
    elif outcome.status is Outcome.FAILED:
        pipeline.update_step("tagging", completed=processed)
        pipeline.fail_step(
            "tagging", f"No tags saved: {reason}", metrics=metrics, reason=reason, recoverable=True
        )
    elif processed == 0:
        pipeline.skip_step("tagging", "no eligible tweets or available quota/spend")
    elif outcome.status is Outcome.PARTIAL:
        pipeline.complete_step("tagging", f"{summary} · some requests failed", metrics=metrics)
    else:
        pipeline.complete_step("tagging", summary, metrics=metrics)


def _step_counters(tagged: int, requests: int) -> str:
    return " · ".join(
        part
        for value, part in ((tagged, f"{tagged} tagged"), (requests, f"{requests} requests"))
        if value
    )


async def tag_pending_media_tweets(
    store: ArchiveStore,
    config: AppConfig,
    paths: XDGPaths,
    console: Console,
    *,
    batch_limit: int | None = None,
    batch_size: int | None = None,
    model_override: str | None = None,
    dry_run: bool = False,
) -> TaggingRunResult:
    """Run homogeneous Free batches or one-request Paid waves."""
    if batch_limit is not None and batch_limit < 1:
        raise ValueError("Tagging request limit must be a positive integer")
    if batch_size is not None and batch_size < 1:
        raise ValueError("Free tagging batch override must be a positive integer")

    pipeline = current_pipeline()
    tag_config = config.tagging
    if not tag_config.enabled or not tag_config.api_key:
        if pipeline.has_step("tagging"):
            pipeline.skip_step(
                "tagging",
                "tagging being disabled in configuration"
                if not tag_config.enabled
                else "no Gemini API key being configured",
            )
        return TaggingRunResult()
    if not _ensure_optional_dependencies():
        if pipeline.has_step("tagging"):
            pipeline.skip_step("tagging", "the automated-tagging extra not being installed")
        else:
            console.print(
                "[yellow]Automated tagging is not installed; continuing without it.[/yellow]"
            )
        return TaggingRunResult()

    max_requests = 1 if dry_run else batch_limit
    free_batch_size = min(batch_size or tag_config.free_batch_size, 20)
    model_name = model_override or tag_config.model
    if pipeline.active:
        mode_detail = f"{model_name} · " + (
            f"Free · up to {free_batch_size} tweets/request · {tag_config.free_rpd} requests/day"
            if tag_config.api_mode == "free"
            else (f"Paid · {tag_config.processing_tier.title()} · sequential processing")
        )
        pipeline.add_step(
            "tagging",
            "Automated tags",
            total=max_requests
            or (free_batch_size if tag_config.api_mode == "free" else PAID_CONCURRENCY),
            unit="tweets",
            detail=mode_detail,
            show_rate=False,
            show_eta=False,
        )
        pipeline.start_step(
            "tagging",
            activity="Selecting tweets",
        )
    if pipeline.has_step("tagging") and not dry_run:
        console = pipeline.capture_console("tagging")  # type: ignore[assignment]

    run = _new_run(store, config, paths, console, TagRequest([], model_override, dry_run=dry_run))
    processed = requests = 0
    total = TaggingOutcome()
    attempted_ids: set[str] = set()

    while max_requests is None or requests < max_requests:
        if run.paid:
            remaining = PAID_CONCURRENCY
            if max_requests is not None:
                remaining = min(remaining, max_requests - requests)
            selection_limit = max(remaining, 1)
        else:
            selection_limit = 1 if dry_run else free_batch_size
        candidates = store.get_eligible_tagging_candidates(
            limit=selection_limit,
            exclude_tweet_ids=attempted_ids,
        )
        if not candidates:
            break
        tweet_ids = [candidate["tweet_id"] for candidate in candidates]
        content_type = candidates[0]["content_type"]
        quote_targets = {
            candidate["tweet_id"]: candidate["quoted_tweet_id"]
            for candidate in candidates
            if candidate["quoted_tweet_id"]
        }

        pipeline.update_step(
            "tagging",
            completed=processed,
            total=max(max_requests or processed + len(tweet_ids), 1),
            activity=(
                f"Generating {content_type} tags for {len(tweet_ids)} tweet"
                f"{'s' if len(tweet_ids) != 1 else ''}"
            ),
            counters=_step_counters(total.tagged, total.attempts),
        )

        # Refresh after the previous request has committed its tags. One snapshot per Free
        # batch or Paid wave: completion order must not change the prompts within it.
        existing_tags = tuple(_top_archive_tags(store))
        outcome = await _tag_group(run, tweet_ids, content_type, quote_targets, existing_tags)
        total = total.plus(outcome)

        # A stop before anything was sent leaves these tweets as they were found.
        dispatched_ids = [] if outcome.halted and not outcome.attempts else tweet_ids
        processed += len(dispatched_ids)
        # The limit counts logical requests; retries and split children are attempts within one.
        # Tweets with nothing to send aren't a request, so they can't use up the limit before
        # the next eligible tweet is reached.
        if outcome.status is not Outcome.NO_WORK:
            requests += len(dispatched_ids) if run.paid else 1
        attempted_ids.update(dispatched_ids)
        pipeline.update_step(
            "tagging",
            completed=processed,
            total=max(max_requests or processed, processed, 1),
            counters=_step_counters(total.tagged, total.attempts),
            important=True,
        )
        if dry_run or outcome.halted:
            break

    finish_tagging_step(pipeline, total, processed=processed)
    return TaggingRunResult(
        processed=processed, tagged=total.tagged, batches=total.attempts, outcome=total.status
    )
