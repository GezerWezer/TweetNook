"""Sync orchestration."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

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
    ArchiveOwnerMismatchError,
    AuthResolutionError,
    ConfigError,
    TweetNookError,
)
from tweetnook.core.locking import ProcessLock
from tweetnook.core.timeline_parsing import TimelineTweet, parse_timeline_response
from tweetnook.jobs.pipeline import current_pipeline
from tweetnook.jobs.resource_failure import raise_if_storage_exhausted
from tweetnook.query_ids import QueryIdStore, refresh_query_ids, resolve_query_ids
from tweetnook.storage import ArchiveStore, SyncState, open_archive_store
from tweetnook.sync.followups import SyncFollowupPlan, run_auto_followups

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
) -> PreflightResult:
    pipeline = current_pipeline()
    step_key = "preflight:" + ",".join(collections)
    if pipeline is not None:
        pipeline.add_step(
            step_key,
            "Prepare",
            total=3 + len(collections),
            unit="checks",
            detail=f"sign-in · archive owner · {len(collections)} collection checks",
            rate_unit="checks/s",
        )
        pipeline.start_step(step_key, activity="Connecting to Twitter/X")
    auth_bundle = auth_bundle or resolve_auth_bundle(config)
    if pipeline is not None:
        pipeline.update_step(
            step_key,
            completed=1,
            activity="Checking archive ownership",
            counters="Signed in",
        )
    existing_store = open_archive_store(paths, create=False, config=config)
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
    if pipeline is not None:
        pipeline.update_step(
            step_key,
            completed=2,
            activity="Preparing Twitter/X requests",
            counters="Signed in · archive owner confirmed",
        )
    operation_names = [COLLECTION_TO_OPERATION[collection] for collection in collections]
    query_store = QueryIdStore(paths)
    query_ids = query_ids or await resolve_query_ids(
        query_store,
        operation_names,
        force_refresh=not query_store.is_fresh(operations=operation_names),
        transport=transport,
    )
    if pipeline is not None:
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
                if pipeline is not None:
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

            probes[collection] = ProbeResult(
                collection=collection, ready=True, detail="Remote probe succeeded."
            )
            if pipeline is not None:
                ready_count = sum(probe.ready for probe in probes.values())
                pipeline.update_step(
                    step_key,
                    completed=3 + len(probes),
                    counters=f"{ready_count}/{len(collections)} collections ready",
                )
    finally:
        await client.aclose()
    if pipeline is not None:
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
    prior_backfill_cursor: str | None,
    prior_backfill_incomplete: bool,
    next_cursor: str | None,
    stop_reason: str,
    is_head_pass: bool,
) -> tuple[str | None, bool]:
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


async def _run_pass(
    *,
    collection: str,
    start_cursor: str | None,
    config: AppConfig,
    auth: ResolvedAuthBundle,
    query_store: QueryIdStore,
    query_ids: dict[str, str],
    store: ArchiveStore,
    count_limit: int | None,
    stop_on_duplicate: bool,
    previous_state: SyncState,
    prior_backfill_cursor: str | None,
    prior_backfill_incomplete: bool,
    initial_seen_ids: set[str],
    is_head_pass: bool,
    console: Console,
    sleep: Callable[[float], Awaitable[None]],
    client: httpx.AsyncClient,
    pipeline_step_key: str | None = None,
) -> tuple[int, int, str]:
    pages_fetched = 0
    tweets_seen = 0
    cursor = start_cursor
    latest_head_id = previous_state.last_head_tweet_id
    stop_reason = "empty"
    seen_ids = initial_seen_ids
    pipeline = current_pipeline()

    while True:
        if count_limit is not None and pages_fetched >= count_limit:
            stop_reason = "limit"
            break

        page_number = pages_fetched + 1
        pass_name = _pass_label(is_head_pass=is_head_pass)
        if pipeline is not None and pipeline_step_key is not None:
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
                if pipeline is not None and pipeline_step_key is not None
                else None
            ),
        )
        duplicate_seen = False
        if is_head_pass and stop_on_duplicate:
            # Imported/detail-enriched members do not prove timeline coverage.
            # The previous timeline head is the incremental sync boundary.
            duplicate_seen = any(
                tweet.tweet_id not in seen_ids
                and tweet.tweet_id == previous_state.last_head_tweet_id
                for tweet in tweets
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
            prior_backfill_cursor=prior_backfill_cursor,
            prior_backfill_incomplete=prior_backfill_incomplete,
            next_cursor=next_cursor,
            stop_reason=stop_reason,
            is_head_pass=is_head_pass,
        )
        if pipeline is not None and pipeline_step_key is not None:
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
        if pipeline is not None and pipeline_step_key is not None:
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
            return pages_fetched, tweets_seen, stop_reason

        cursor = next_cursor
        await sleep(config.sync.page_delay)

    return pages_fetched, tweets_seen, stop_reason


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

    preflight = await run_preflight(
        config=config,
        paths=paths,
        collections=[collection],
        auth_bundle=auth_bundle,
        query_ids=query_ids,
        transport=transport,
    )
    result = await _sync_collection_ready(
        collection=collection,
        full=full,
        backfill=backfill,
        head_only=head_only,
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
    full: bool,
    backfill: bool = False,
    head_only: bool = False,
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

    lock = ProcessLock(paths.lock_file)
    lock.acquire()
    try:
        store = open_archive_store(paths, create=True, config=config)
        assert store is not None
        from tweetnook.jobs.reminders import print_archive_migration_report

        print_archive_migration_report(console, store)
        try:
            store.ensure_archive_owner_id(preflight.auth.user_id)
        except ArchiveOwnerMismatchError:
            store.close()
            raise
        initial_collection_count = store.count_export_rows(COLLECTION_TO_STORAGE[collection])

        if head_only and (full or backfill):
            raise ConfigError(
                "--head-only cannot be combined with --full, --backfill, or --article-backfill."
            )

        if full:
            store.reset_sync_state(COLLECTION_TO_STORAGE[collection])

        previous_state = store.get_sync_state(COLLECTION_TO_STORAGE[collection])
        if head_only and previous_state.backfill_incomplete:
            store.set_sync_state(
                COLLECTION_TO_STORAGE[collection],
                last_head_tweet_id=previous_state.last_head_tweet_id,
                backfill_cursor=None,
                backfill_incomplete=False,
            )
            previous_state = store.get_sync_state(COLLECTION_TO_STORAGE[collection])
        prior_backfill_cursor = (
            previous_state.backfill_cursor if previous_state.backfill_incomplete else None
        )
        prior_backfill_incomplete = previous_state.backfill_incomplete
        seen_ids: set[str] = set()
        stop_on_dup = not full and not backfill
        pages_total = 0
        client = build_async_client(
            preflight.auth, timeout=config.sync.timeout, transport=transport
        )
        try:
            try:
                head_step_key = f"sync:{collection}:head"
                combine_pipeline_passes = pipeline is not None and collection in {
                    "bookmarks",
                    "likes",
                }
                if pipeline is not None:
                    stop_policy = (
                        "continue through saved duplicates"
                        if full or backfill
                        else "stop at saved archive history"
                    )
                    pipeline.add_step(
                        head_step_key,
                        collection.title(),
                        total=1,
                        unit="current page",
                        detail=f"head pass · {stop_policy}",
                        show_rate=False,
                        show_eta=False,
                        show_progress=False,
                    )
                    pipeline.start_step(
                        head_step_key,
                        activity="Fetching page 1",
                        counters="0 pages · 0 tweets saved",
                    )
                else:
                    console.print(f"{collection}: starting head pass", highlight=False)
                head_pages, head_tweets, head_reason = await _run_pass(
                    collection=collection,
                    start_cursor=None,
                    config=config,
                    auth=preflight.auth,
                    query_store=QueryIdStore(paths),
                    query_ids=dict(preflight.query_ids),
                    store=store,
                    count_limit=limit,
                    stop_on_duplicate=stop_on_dup,
                    previous_state=previous_state,
                    prior_backfill_cursor=prior_backfill_cursor,
                    prior_backfill_incomplete=prior_backfill_incomplete,
                    initial_seen_ids=seen_ids,
                    is_head_pass=True,
                    console=console,
                    sleep=sleep,
                    client=client,
                    pipeline_step_key=head_step_key,
                )
                if pipeline is not None and not combine_pipeline_passes:
                    pipeline.complete_step(
                        head_step_key,
                        f"{head_pages:,} pages · {head_tweets:,} tweets · "
                        f"{_stop_summary(head_reason)}",
                    )
                pages_total = head_pages
                tweets_total = head_tweets
                stop_reason = head_reason
                remaining = None if limit is None else max(limit - head_pages, 0)

                if head_only:
                    refreshed_state = store.get_sync_state(COLLECTION_TO_STORAGE[collection])
                    store.set_sync_state(
                        COLLECTION_TO_STORAGE[collection],
                        last_head_tweet_id=refreshed_state.last_head_tweet_id
                        or previous_state.last_head_tweet_id,
                        backfill_cursor=None,
                        backfill_incomplete=False,
                    )

                resume_saved_backfill = (
                    resume_backfill
                    and not head_only
                    and prior_backfill_incomplete
                    and remaining != 0
                )
                if resume_saved_backfill:
                    if pipeline is not None:
                        if combine_pipeline_passes:
                            pipeline.update_step(
                                head_step_key,
                                completed=0,
                                total=1,
                                activity="Fetching older page 1",
                                counters="0 pages · 0 tweets saved",
                                detail=(
                                    "older-history pass · each page is committed with its "
                                    "resume cursor"
                                ),
                                important=True,
                            )
                            backfill_step_key = head_step_key
                        else:
                            backfill_step_key = f"sync:{collection}:backfill"
                            pipeline.add_step(
                                backfill_step_key,
                                f"{collection.title()} history",
                                total=1,
                                unit="current page",
                                detail="saved backfill cursor · older archive history",
                                show_rate=False,
                                show_eta=False,
                                show_progress=False,
                            )
                            pipeline.start_step(
                                backfill_step_key,
                                activity="Fetching older page 1",
                                counters="0 pages · 0 tweets saved",
                            )
                    else:
                        backfill_step_key = None
                        console.print(
                            f"{collection}: resuming saved backfill pass", highlight=False
                        )
                    refreshed_state = store.get_sync_state(COLLECTION_TO_STORAGE[collection])
                    backfill_pages, backfill_tweets, backfill_reason = await _run_pass(
                        collection=collection,
                        start_cursor=prior_backfill_cursor,
                        config=config,
                        auth=preflight.auth,
                        query_store=QueryIdStore(paths),
                        query_ids=dict(preflight.query_ids),
                        store=store,
                        count_limit=remaining,
                        stop_on_duplicate=False,
                        previous_state=refreshed_state,
                        prior_backfill_cursor=prior_backfill_cursor,
                        prior_backfill_incomplete=prior_backfill_incomplete,
                        initial_seen_ids=seen_ids,
                        is_head_pass=False,
                        console=console,
                        sleep=sleep,
                        client=client,
                        pipeline_step_key=backfill_step_key if pipeline is not None else None,
                    )
                    pages_total += backfill_pages
                    tweets_total += backfill_tweets
                    stop_reason = backfill_reason
                    if pipeline is not None and not combine_pipeline_passes:
                        pipeline.complete_step(
                            backfill_step_key,
                            f"{backfill_pages:,} pages · {backfill_tweets:,} tweets · "
                            f"{_stop_summary(backfill_reason)}",
                        )
                if pipeline is not None and combine_pipeline_passes:
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
                            head_step_key,
                            f"{head_pages:,} head pages · {head_tweets:,} tweets · "
                            f"{backfill_pages:,} older pages · {backfill_tweets:,} tweets · "
                            f"{_stop_summary(backfill_reason)}",
                            metrics=metrics,
                        )
                    else:
                        pipeline.complete_step(
                            head_step_key,
                            f"{head_pages:,} pages · {head_tweets:,} tweets · "
                            f"{_stop_summary(head_reason)}",
                            metrics=metrics,
                        )
                elif (
                    pipeline is not None
                    and pipeline.has_step(f"sync:{collection}:backfill")
                    and not resume_saved_backfill
                ):
                    if not prior_backfill_incomplete:
                        reason = "no saved older-history cursor"
                    elif remaining == 0:
                        reason = "the page limit being consumed by the head pass"
                    else:
                        reason = "older-history resume being disabled for this run"
                    pipeline.skip_step(f"sync:{collection}:backfill", reason)
            except BaseException as exc:
                raise_if_storage_exhausted(exc)
                raise
        finally:
            try:
                await client.aclose()
            finally:
                store.close()
    finally:
        lock.release()

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
    selected = list(config.sync.collections)
    preflight = await run_preflight(
        config=config,
        paths=paths,
        collections=selected,
        auth_bundle=auth_bundle,
        transport=transport,
    )
    if not preflight.is_ready_for(selected):
        if preflight.has_local_error:
            raise LocalPreflightError("sync all preflight failed on local auth/config.")
        raise RemotePreflightError("sync all preflight failed on a remote probe.")

    pipeline = current_pipeline()
    if pipeline is not None:
        stop_policy = (
            "continue through saved duplicates"
            if full or backfill
            else "stop at saved archive history"
        )
        for collection in selected:
            pipeline.add_step(
                f"sync:{collection}:head",
                collection.title(),
                total=1,
                unit="current page",
                detail=f"head pass · {stop_policy}",
                show_rate=False,
                show_eta=False,
                show_progress=False,
            )

    results: list[SyncResult] = []
    errors: dict[str, str] = {}
    exit_code = 0
    for collection in selected:
        try:
            result = await _sync_collection_ready(
                collection=collection,
                full=full,
                backfill=backfill,
                head_only=head_only,
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
            if pipeline is not None:
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
