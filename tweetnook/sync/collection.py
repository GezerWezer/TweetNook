"""Sync orchestration."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, NamedTuple

import httpx
from rich.console import Console

from tweetnook.auth import ResolvedAuthBundle, resolve_auth_bundle
from tweetnook.client.base import build_async_client
from tweetnook.client.timelines import (
    build_bookmarks_url,
    build_likes_url,
    build_user_tweets_url,
    fetch_page,
)
from tweetnook.config import AppConfig, XDGPaths, ensure_paths, load_config
from tweetnook.core.exceptions import (
    APIResponseError,
    AuthResolutionError,
    ConfigError,
    TweetNookError,
)
from tweetnook.core.timeline_parsing import TimelineTweet, parse_timeline_response
from tweetnook.jobs.context import locked_archive_job, open_job_store
from tweetnook.jobs.pipeline import current_pipeline
from tweetnook.jobs.resource_failure import raise_if_storage_exhausted
from tweetnook.query_ids import QueryIdStore, refresh_query_ids, resolve_query_ids
from tweetnook.storage import ArchiveStore
from tweetnook.sync.followups import SyncFollowupPlan, run_auto_followups
from tweetnook.sync.steps import (
    add_backfill_step,
    add_head_step,
    add_preflight_step,
    backfill_step_key,
    head_step_key,
    history_shares_head_step,
    preflight_step_key,
)


class SyncMode(StrEnum):
    INCREMENTAL = "incremental"
    HEAD_ONLY = "head_only"
    BACKFILL = "backfill"
    FULL = "full"


def parse_sync_mode(*, full: bool, backfill: bool, head_only: bool, limit: int | None) -> SyncMode:
    """Refuse an impossible request before preflight or any write acts on it."""
    if head_only and (full or backfill):
        raise ConfigError(
            "--head-only cannot be combined with --full, --backfill, or --article-backfill."
        )
    if limit is not None and limit < 0:
        raise ConfigError("--limit cannot be negative.")
    if full:
        return SyncMode.FULL
    if backfill:
        return SyncMode.BACKFILL
    return SyncMode.HEAD_ONLY if head_only else SyncMode.INCREMENTAL


COLLECTION_TO_OPERATION = {
    "bookmarks": "Bookmarks",
    "likes": "Likes",
    "tweets": "UserTweets",
}
COLLECTION_TO_STORAGE = {
    "bookmarks": "bookmark",
    "likes": "like",
    "tweets": "tweet",
}


class LocalPreflightError(ConfigError):
    """Raised for local preflight failures."""


class RemotePreflightError(TweetNookError):
    """Raised for remote/API preflight failures."""


@dataclass(slots=True)
class ProbeResult:
    collection: str
    ready: bool
    detail: str
    local_error: bool = False


@dataclass(slots=True)
class PreflightResult:
    auth: ResolvedAuthBundle
    query_ids: dict[str, str]
    probes: dict[str, ProbeResult]

    @property
    def has_local_error(self) -> bool:
        return any(probe.local_error for probe in self.probes.values())

    @property
    def has_remote_error(self) -> bool:
        return any(not probe.ready and not probe.local_error for probe in self.probes.values())

    def is_ready_for(self, collections: Sequence[str]) -> bool:
        return all(self.probes[collection].ready for collection in collections)


@dataclass(slots=True)
class SyncResult:
    collection: str
    pages_fetched: int
    tweets_seen: int
    stop_reason: str


@dataclass(slots=True)
class SyncAllResult:
    exit_code: int
    results: list[SyncResult]
    errors: dict[str, str]


def _build_url(
    collection: str, query_id: str, auth: ResolvedAuthBundle, cursor: str | None, count: int
) -> str:
    if collection == "bookmarks":
        return build_bookmarks_url(query_id, cursor=cursor, count=count)
    if collection == "likes":
        return build_likes_url(query_id, auth.user_id or "", cursor=cursor, count=count)
    return build_user_tweets_url(query_id, auth.user_id or "", cursor=cursor, count=count)


async def run_preflight(
    *,
    config: AppConfig,
    paths: XDGPaths,
    collections: Sequence[str],
    auth_bundle: ResolvedAuthBundle | None = None,
    query_ids: dict[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    console: Console | None = None,
) -> PreflightResult:
    pipeline = current_pipeline()
    step_key = preflight_step_key(collections)
    add_preflight_step(pipeline, collections)
    pipeline.start_step(step_key, activity="Connecting to Twitter/X")
    console = console or Console(stderr=True)
    try:
        auth_bundle = auth_bundle or resolve_auth_bundle(config)
        pipeline.update_step(
            step_key,
            completed=1,
            activity="Checking archive ownership",
            counters="Signed in",
        )
        existing_store = open_job_store(paths, create=False, config=config, console=console)
        if existing_store is not None:
            try:
                existing_owner = existing_store.get_archive_owner_id()
                if existing_owner and auth_bundle.user_id and existing_owner != auth_bundle.user_id:
                    raise LocalPreflightError(
                        f"Local archive belongs to Twitter/X user {existing_owner}, but current "
                        "auth resolved "
                        f"{auth_bundle.user_id}."
                    )
            finally:
                existing_store.close()
        pipeline.update_step(
            step_key,
            completed=2,
            activity="Preparing Twitter/X requests",
            counters="Signed in · archive owner confirmed",
        )
        operation_names = [COLLECTION_TO_OPERATION[collection] for collection in collections]
        query_store = QueryIdStore(paths)
        # Copied, because a refresh below writes into it and the dict belongs to the caller.
        query_ids = (
            dict(query_ids)
            if query_ids
            else await resolve_query_ids(
                query_store,
                operation_names,
                force_refresh=not query_store.is_fresh(operations=operation_names),
                transport=transport,
            )
        )
        pipeline.update_step(
            step_key,
            completed=3,
            activity=f"Checking {collections[0].title()}",
            counters=f"{len(query_ids)} request types ready",
        )
        probes: dict[str, ProbeResult] = {}
        client = build_async_client(auth_bundle, timeout=config.sync.timeout, transport=transport)
        try:
            for collection in collections:
                operation = COLLECTION_TO_OPERATION[collection]
                try:
                    auth_bundle.validate_for_collection(collection)
                except AuthResolutionError as exc:
                    probes[collection] = ProbeResult(
                        collection=collection,
                        ready=False,
                        detail=str(exc),
                        local_error=True,
                    )
                    continue

                async def refresh_once(
                    operation_name: str = operation,
                    collection_name: str = collection,
                ) -> str:
                    refreshed = await refresh_query_ids(
                        query_store,
                        operations=[operation_name],
                        client=client,
                    )
                    query_ids.update(refreshed)
                    return _build_url(
                        collection_name,
                        query_ids[operation_name],
                        auth_bundle,
                        None,
                        1,
                    )

                try:
                    pipeline.status(step_key, f"Checking {collection.title()}")
                    await fetch_page(
                        client,
                        _build_url(collection, query_ids[operation], auth_bundle, None, 1),
                        config.sync,
                        refresh_once=refresh_once,
                    )
                except APIResponseError as exc:
                    probes[collection] = ProbeResult(
                        collection=collection, ready=False, detail=str(exc)
                    )
                    continue
                except httpx.TransportError as exc:
                    probes[collection] = ProbeResult(
                        collection=collection,
                        ready=False,
                        detail=f"{type(exc).__name__}: {exc}",
                    )
                    continue

                probes[collection] = ProbeResult(
                    collection=collection, ready=True, detail="Remote probe succeeded."
                )
                ready_count = sum(probe.ready for probe in probes.values())
                pipeline.update_step(
                    step_key,
                    completed=3 + len(probes),
                    counters=f"{ready_count}/{len(collections)} collections ready",
                )
        finally:
            await client.aclose()
        ready_count = sum(probe.ready for probe in probes.values())
        if ready_count == len(collections):
            pipeline.complete_step(
                step_key,
                f"Connected · {ready_count}/{len(collections)} collections ready",
            )
        else:
            pipeline.fail_step(
                step_key,
                f"{ready_count}/{len(collections)} collections ready",
            )
        return PreflightResult(auth=auth_bundle, query_ids=query_ids, probes=probes)
    except Exception as exc:
        # Left active, this step would refuse the next collection's Prepare step and hide
        # the real failure behind that complaint. An interrupt is the reporter's to resolve.
        pipeline.fail_step(step_key, str(exc) or type(exc).__name__)
        raise


async def _fetch_and_parse_page(
    *,
    collection: str,
    cursor: str | None,
    count: int,
    config: AppConfig,
    auth: ResolvedAuthBundle,
    query_store: QueryIdStore,
    query_ids: dict[str, str],
    client: httpx.AsyncClient,
    status: Callable[[str], None] | None = None,
) -> tuple[httpx.Response, dict[str, Any], list[TimelineTweet], str | None]:
    operation = COLLECTION_TO_OPERATION[collection]

    async def refresh_once() -> str:
        refreshed = await refresh_query_ids(query_store, operations=[operation], client=client)
        query_ids.update(refreshed)
        return _build_url(collection, query_ids[operation], auth, cursor, count)

    url = _build_url(collection, query_ids[operation], auth, cursor, count)
    response = await fetch_page(
        client,
        url,
        config.sync,
        refresh_once=refresh_once,
        status=status,
    )
    payload = response.json()
    tweets, next_cursor = parse_timeline_response(payload)
    return response, payload, tweets, next_cursor


def _store_state_for_page(
    *,
    mode: SyncMode,
    prior_backfill_cursor: str | None,
    prior_backfill_incomplete: bool,
    next_cursor: str | None,
    stop_reason: str,
    is_head_pass: bool,
) -> tuple[str | None, bool]:
    if mode is SyncMode.HEAD_ONLY:
        return None, False
    if not is_head_pass:
        if stop_reason in {"empty", "backfill-complete"}:
            return None, False
        return next_cursor, bool(next_cursor)
    if prior_backfill_incomplete:
        return prior_backfill_cursor, True
    if stop_reason in {"duplicate", "head-complete"}:
        return None, False
    return next_cursor, bool(next_cursor)


def _pass_label(*, is_head_pass: bool) -> str:
    return "head" if is_head_pass else "backfill"


def _rate_limit_context(response: httpx.Response) -> str:
    remaining = response.headers.get("x-rate-limit-remaining")
    limit = response.headers.get("x-rate-limit-limit")
    reset = response.headers.get("x-rate-limit-reset")
    parts: list[str] = []
    if remaining and limit:
        parts.append(f"API {remaining}/{limit}")
    if reset and reset.isdigit():
        minutes = max(round((int(reset) - time.time()) / 60), 0)
        parts.append(f"reset in {minutes}m")
    return " · ".join(parts)


def _stop_summary(reason: str) -> str:
    return {
        "duplicate": "archive is up to date",
        "empty": "Twitter/X returned no tweets",
        "head-complete": "reached the end of the collection",
        "backfill-complete": "older history is complete",
        "limit": "reached the page limit",
        "continue": "more pages available",
    }.get(reason, reason)


class PassResult(NamedTuple):
    pages: int
    tweets: int
    stop_reason: str
    head_id: str | None


async def _run_pass(
    *,
    mode: SyncMode,
    collection: str,
    start_cursor: str | None,
    config: AppConfig,
    auth: ResolvedAuthBundle,
    query_store: QueryIdStore,
    query_ids: dict[str, str],
    store: ArchiveStore,
    count_limit: int | None,
    stop_on_duplicate: bool,
    head_id: str | None,
    prior_backfill_cursor: str | None,
    prior_backfill_incomplete: bool,
    initial_seen_ids: set[str],
    is_head_pass: bool,
    console: Console,
    sleep: Callable[[float], Awaitable[None]],
    client: httpx.AsyncClient,
    pipeline_step_key: str | None = None,
) -> PassResult:
    pages_fetched = 0
    tweets_seen = 0
    cursor = start_cursor
    latest_head_id = head_id
    stop_reason = "empty"
    seen_ids = initial_seen_ids
    pipeline = current_pipeline()

    while True:
        if count_limit is not None and pages_fetched >= count_limit:
            stop_reason = "limit"
            break

        page_number = pages_fetched + 1
        pass_name = _pass_label(is_head_pass=is_head_pass)
        if pipeline_step_key is not None:
            pipeline.update_step(
                pipeline_step_key,
                completed=0,
                total=1,
                activity=(
                    f"Fetching page {page_number}"
                    if is_head_pass
                    else f"Fetching older page {page_number}"
                ),
                counters=f"{pages_fetched} pages · {tweets_seen} tweets saved",
                detail=f"{pass_name} pass · each page is committed with its resume cursor",
            )
        response, payload, tweets, next_cursor = await _fetch_and_parse_page(
            collection=collection,
            cursor=cursor,
            count=20,
            config=config,
            auth=auth,
            query_store=query_store,
            query_ids=query_ids,
            client=client,
            status=(
                (
                    lambda message, page_number=page_number: pipeline.status(
                        pipeline_step_key,
                        f"{collection.title()} page {page_number}: {message}",
                        important=True,
                    )
                )
                if pipeline.active and pipeline_step_key is not None
                else None
            ),
        )
        duplicate_seen = False
        if is_head_pass and stop_on_duplicate:
            # Imported/detail-enriched members do not prove timeline coverage.
            # The previous timeline head is the incremental sync boundary.
            duplicate_seen = any(
                tweet.tweet_id not in seen_ids and tweet.tweet_id == head_id for tweet in tweets
            )

        if is_head_pass and tweets and pages_fetched == 0:
            latest_head_id = tweets[0].tweet_id

        if not tweets:
            stop_reason = "empty"
        elif duplicate_seen:
            stop_reason = "duplicate"
        elif next_cursor is None:
            stop_reason = "head-complete" if is_head_pass else "backfill-complete"
        elif count_limit is not None and pages_fetched + 1 >= count_limit:
            stop_reason = "limit"
        else:
            stop_reason = "continue"

        backfill_cursor, backfill_incomplete = _store_state_for_page(
            mode=mode,
            prior_backfill_cursor=prior_backfill_cursor,
            prior_backfill_incomplete=prior_backfill_incomplete,
            next_cursor=next_cursor,
            stop_reason=stop_reason,
            is_head_pass=is_head_pass,
        )
        if pipeline_step_key is not None:
            pipeline.status(
                pipeline_step_key,
                f"Saving {len(tweets):,} tweets from page {page_number}",
            )
        store.persist_page(
            operation=COLLECTION_TO_OPERATION[collection],
            collection_type=COLLECTION_TO_STORAGE[collection],
            cursor_in=cursor,
            cursor_out=next_cursor,
            http_status=response.status_code,
            raw_json=payload,
            tweets=tweets,
            last_head_tweet_id=latest_head_id,
            backfill_cursor=backfill_cursor,
            backfill_incomplete=backfill_incomplete,
        )
        for tweet in tweets:
            seen_ids.add(tweet.tweet_id)

        pages_fetched += 1
        tweets_seen += len(tweets)
        if pipeline.active and pipeline_step_key is not None:
            rate_context = _rate_limit_context(response)
            detail = f"{pass_name} pass · durable through page {pages_fetched}"
            if rate_context:
                detail += f" · {rate_context}"
            pipeline.update_step(
                pipeline_step_key,
                completed=1,
                total=1,
                activity=(
                    f"Page {pages_fetched} saved"
                    if stop_reason == "continue"
                    else _stop_summary(stop_reason).capitalize()
                ),
                counters=f"{pages_fetched} pages · {tweets_seen} tweets saved",
                detail=detail,
                important=True,
            )
        else:
            console.print(
                f"{collection} {_pass_label(is_head_pass=is_head_pass)}: "
                f"page {pages_fetched}, "
                f"page_tweets {len(tweets)}, "
                f"total_tweets {tweets_seen}, "
                f"stop={stop_reason}",
                highlight=False,
            )

        if stop_reason != "continue":
            break

        cursor = next_cursor
        await sleep(config.sync.page_delay)

    return PassResult(pages_fetched, tweets_seen, stop_reason, latest_head_id)


async def sync_collection(
    collection: str,
    *,
    full: bool,
    backfill: bool = False,
    head_only: bool = False,
    resume_backfill: bool = True,
    limit: int | None = None,
    config: AppConfig | None = None,
    paths: XDGPaths | None = None,
    auth_bundle: ResolvedAuthBundle | None = None,
    query_ids: dict[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    console: Console | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    followups: SyncFollowupPlan | None = None,
) -> SyncResult:
    if config is None or paths is None:
        loaded_config, loaded_paths = load_config()
        config = config or loaded_config
        paths = paths or loaded_paths
    paths = ensure_paths(paths)
    console = console or Console(stderr=True)
    mode = parse_sync_mode(full=full, backfill=backfill, head_only=head_only, limit=limit)

    preflight = await run_preflight(
        config=config,
        paths=paths,
        collections=[collection],
        auth_bundle=auth_bundle,
        query_ids=query_ids,
        transport=transport,
        console=console,
    )
    result = await _sync_collection_ready(
        collection=collection,
        mode=mode,
        resume_backfill=resume_backfill,
        limit=limit,
        config=config,
        paths=paths,
        preflight=preflight,
        transport=transport,
        console=console,
        sleep=sleep,
    )
    if followups is not None:
        await run_auto_followups(
            plan=followups,
            config=config,
            paths=paths,
            auth_bundle=preflight.auth,
            transport=transport,
            console=console,
            sleep=sleep,
        )
    return result


async def _sync_collection_ready(
    *,
    collection: str,
    mode: SyncMode,
    resume_backfill: bool = True,
    limit: int | None,
    config: AppConfig,
    paths: XDGPaths,
    preflight: PreflightResult,
    transport: httpx.AsyncBaseTransport | None,
    console: Console,
    sleep: Callable[[float], Awaitable[None]],
) -> SyncResult:
    pipeline = current_pipeline()
    probe = preflight.probes[collection]
    if not probe.ready:
        if probe.local_error:
            raise LocalPreflightError(probe.detail)
        raise RemotePreflightError(probe.detail)

    async with locked_archive_job(config=config, paths=paths, console=console, create=True) as job:
        store = job.store
        store.ensure_archive_owner_id(preflight.auth.user_id)
        initial_collection_count = store.count_export_rows(COLLECTION_TO_STORAGE[collection])

        if mode is SyncMode.FULL:
            store.reset_sync_state(COLLECTION_TO_STORAGE[collection])

        previous_state = store.get_sync_state(COLLECTION_TO_STORAGE[collection])
        prior_backfill_cursor = (
            previous_state.backfill_cursor if previous_state.backfill_incomplete else None
        )
        prior_backfill_incomplete = previous_state.backfill_incomplete
        seen_ids: set[str] = set()
        stop_on_dup = mode in {SyncMode.INCREMENTAL, SyncMode.HEAD_ONLY}
        pages_total = 0
        client = build_async_client(
            preflight.auth, timeout=config.sync.timeout, transport=transport
        )
        try:
            head_step = head_step_key(collection)
            combine_pipeline_passes = pipeline.active and history_shares_head_step(collection)
            if pipeline.active:
                add_head_step(pipeline, collection)
                pipeline.start_step(
                    head_step,
                    activity="Fetching page 1",
                    counters="0 pages · 0 tweets saved",
                )
            else:
                console.print(f"{collection}: starting head pass", highlight=False)
            head = await _run_pass(
                mode=mode,
                collection=collection,
                start_cursor=None,
                config=config,
                auth=preflight.auth,
                query_store=QueryIdStore(paths),
                query_ids=dict(preflight.query_ids),
                store=store,
                count_limit=limit,
                stop_on_duplicate=stop_on_dup,
                head_id=previous_state.last_head_tweet_id,
                prior_backfill_cursor=prior_backfill_cursor,
                prior_backfill_incomplete=prior_backfill_incomplete,
                initial_seen_ids=seen_ids,
                is_head_pass=True,
                console=console,
                sleep=sleep,
                client=client,
                pipeline_step_key=head_step,
            )
            if not combine_pipeline_passes:
                pipeline.complete_step(
                    head_step,
                    f"{head.pages:,} pages · {head.tweets:,} tweets · "
                    f"{_stop_summary(head.stop_reason)}",
                )
            pages_total = head.pages
            tweets_total = head.tweets
            stop_reason = head.stop_reason
            remaining = None if limit is None else max(limit - head.pages, 0)

            resume_saved_backfill = (
                resume_backfill
                and mode is not SyncMode.HEAD_ONLY
                and prior_backfill_incomplete
                and remaining != 0
            )
            if resume_saved_backfill:
                if pipeline.active:
                    if combine_pipeline_passes:
                        pipeline.update_step(
                            head_step,
                            completed=0,
                            total=1,
                            activity="Fetching older page 1",
                            counters="0 pages · 0 tweets saved",
                            detail=(
                                "older-history pass · each page is committed with its resume cursor"
                            ),
                            important=True,
                        )
                        backfill_step = head_step
                    else:
                        backfill_step = backfill_step_key(collection)
                        add_backfill_step(pipeline, collection)
                        pipeline.start_step(
                            backfill_step,
                            activity="Fetching older page 1",
                            counters="0 pages · 0 tweets saved",
                        )
                else:
                    backfill_step = None
                    console.print(f"{collection}: resuming saved backfill pass", highlight=False)
                older = await _run_pass(
                    mode=mode,
                    collection=collection,
                    start_cursor=prior_backfill_cursor,
                    config=config,
                    auth=preflight.auth,
                    query_store=QueryIdStore(paths),
                    query_ids=dict(preflight.query_ids),
                    store=store,
                    count_limit=remaining,
                    stop_on_duplicate=False,
                    head_id=head.head_id,
                    prior_backfill_cursor=prior_backfill_cursor,
                    prior_backfill_incomplete=prior_backfill_incomplete,
                    initial_seen_ids=seen_ids,
                    is_head_pass=False,
                    console=console,
                    sleep=sleep,
                    client=client,
                    pipeline_step_key=backfill_step,
                )
                pages_total += older.pages
                tweets_total += older.tweets
                stop_reason = older.stop_reason
                if not combine_pipeline_passes:
                    pipeline.complete_step(
                        backfill_step,
                        f"{older.pages:,} pages · {older.tweets:,} tweets · "
                        f"{_stop_summary(older.stop_reason)}",
                    )
            if pipeline.active and combine_pipeline_passes:
                metrics = {
                    "new_tweets": max(
                        store.count_export_rows(COLLECTION_TO_STORAGE[collection])
                        - initial_collection_count,
                        0,
                    ),
                    "tweets_seen": tweets_total,
                    "pages": pages_total,
                }
                if resume_saved_backfill:
                    pipeline.complete_step(
                        head_step,
                        f"{head.pages:,} head pages · {head.tweets:,} tweets · "
                        f"{older.pages:,} older pages · {older.tweets:,} tweets · "
                        f"{_stop_summary(older.stop_reason)}",
                        metrics=metrics,
                    )
                else:
                    pipeline.complete_step(
                        head_step,
                        f"{head.pages:,} pages · {head.tweets:,} tweets · "
                        f"{_stop_summary(head.stop_reason)}",
                        metrics=metrics,
                    )
            elif pipeline.has_step(backfill_step_key(collection)) and not resume_saved_backfill:
                if not prior_backfill_incomplete:
                    reason = "no saved older-history cursor"
                elif remaining == 0:
                    reason = "the page limit being consumed by the head pass"
                else:
                    reason = "older-history resume being disabled for this run"
                pipeline.skip_step(backfill_step_key(collection), reason)
        finally:
            await client.aclose()

    return SyncResult(
        collection=collection,
        pages_fetched=pages_total,
        tweets_seen=tweets_total,
        stop_reason=stop_reason,
    )


async def sync_all(
    *,
    full: bool,
    backfill: bool = False,
    head_only: bool = False,
    limit: int | None,
    config: AppConfig | None = None,
    paths: XDGPaths | None = None,
    auth_bundle: ResolvedAuthBundle | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    console: Console | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    followups: SyncFollowupPlan | None = None,
) -> SyncAllResult:
    if config is None or paths is None:
        loaded_config, loaded_paths = load_config()
        config = config or loaded_config
        paths = paths or loaded_paths
    paths = ensure_paths(paths)
    console = console or Console(stderr=True)
    mode = parse_sync_mode(full=full, backfill=backfill, head_only=head_only, limit=limit)
    selected = list(config.sync.collections)
    preflight = await run_preflight(
        config=config,
        paths=paths,
        collections=selected,
        auth_bundle=auth_bundle,
        transport=transport,
        console=console,
    )
    if not preflight.is_ready_for(selected):
        if preflight.has_local_error:
            raise LocalPreflightError("sync all preflight failed on local auth/config.")
        raise RemotePreflightError("sync all preflight failed on a remote probe.")

    results: list[SyncResult] = []
    errors: dict[str, str] = {}
    exit_code = 0
    for collection in selected:
        try:
            result = await _sync_collection_ready(
                collection=collection,
                mode=mode,
                limit=limit,
                config=config,
                paths=paths,
                preflight=preflight,
                transport=transport,
                console=console,
                sleep=sleep,
            )
            results.append(result)
        except TweetNookError as exc:
            raise_if_storage_exhausted(exc)
            exit_code = 2
            errors[collection] = str(exc)
            pipeline = current_pipeline()
            if pipeline.active:
                pipeline.issue(
                    f"{collection.title()} sync failed: {exc}",
                    level="error",
                    dedupe_key=f"sync:{collection}:failure",
                )
            else:
                console.print(f"{collection}: failed ({exc})")
            break
    if exit_code == 0 and followups is not None:
        await run_auto_followups(
            plan=followups,
            config=config,
            paths=paths,
            auth_bundle=preflight.auth,
            transport=transport,
            console=console,
            sleep=sleep,
        )
    return SyncAllResult(exit_code=exit_code, results=results, errors=errors)
