"""Thread/context expansion helpers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field

import httpx
from rich.console import Console

from tweetnook.auth import ResolvedAuthBundle, resolve_auth_bundle
from tweetnook.client.base import AdaptiveRequestPacer, build_async_client
from tweetnook.client.detail import TweetDetailRequester, resolve_detail_query_ids
from tweetnook.config import AppConfig, XDGPaths
from tweetnook.core.exceptions import ConfigError
from tweetnook.core.extractor import extract_status_id_from_url
from tweetnook.core.timeline_parsing import parse_tweet_detail_tweets
from tweetnook.enrichment.availability import record_unavailable
from tweetnook.enrichment.detail import (
    DetailAbsent,
    DetailAvailable,
    DetailExecutor,
    DetailOutcome,
    DetailUnavailable,
    normalize_status_target,
)
from tweetnook.jobs.context import locked_archive_job, resolve_job_context
from tweetnook.jobs.pipeline import current_pipeline
from tweetnook.storage import ArchiveStore

_SCAN_PROGRESS_EVERY = 100


@dataclass(slots=True)
class ThreadExpandResult:
    processed: int = 0
    expanded: int = 0
    skipped: int = 0
    failed: int = 0


def _pipeline_outcomes(result: ThreadExpandResult, *, final: bool = False) -> str:
    parts: list[str] = []
    if final or result.expanded:
        parts.append(f"{result.expanded} expanded")
    if result.skipped:
        parts.append(f"{result.skipped} skipped")
    if result.failed:
        parts.append(f"{result.failed} failed")
    return " · ".join(parts)


def normalize_thread_target(value: str) -> str:
    return normalize_status_target(value, "thread")


def _dedupe_targets(values: list[str]) -> tuple[list[str], int]:
    unique = list(dict.fromkeys(values))
    return unique, len(values) - len(unique)


def _log_thread_status(console: Console, tweet_id: str, message: str) -> None:
    pipeline = current_pipeline()
    if pipeline.active:
        active = pipeline.active_step
        if active is None:
            return
        rendered = f"Tweet {tweet_id}: {message}"
        if any(marker in message for marker in ("failed (", "ambiguous", "unavailable")):
            category = "unavailable" if "unavailable" in message else "fetch-failure"
            pipeline.issue(rendered, dedupe_key=f"threads:{category}")
        else:
            pipeline.status(active.key, rendered, important=True)
        return
    console.print(f"thread {tweet_id}: {message}", highlight=False)


def _log_threads(console: Console, message: str) -> None:
    pipeline = current_pipeline()
    if pipeline.active:
        active = pipeline.active_step
        if active is not None:
            pipeline.status(active.key, message, important=True)
        return
    console.print(f"threads: {message}", highlight=False)


def _log_scan_progress(
    console: Console,
    *,
    phase: str,
    scanned: int,
    total: int,
    result: ThreadExpandResult,
) -> None:
    if current_pipeline().active:
        return
    if scanned % _SCAN_PROGRESS_EVERY != 0 and scanned != total:
        return
    _log_threads(
        console,
        f"{phase} {scanned}/{total} scanned, "
        f"{result.processed} processed, "
        f"{result.expanded} expanded, "
        f"{result.skipped} skipped, "
        f"{result.failed} failed",
    )


def _record_thread_outcome(
    store: ArchiveStore,
    outcome: DetailOutcome,
    *,
    known_tweet_ids: set[str],
    result: ThreadExpandResult,
    console: Console,
) -> None:
    tweet_id = outcome.tweet_id
    if isinstance(outcome, DetailAvailable):
        tweets = parse_tweet_detail_tweets(outcome.payload)
        store.persist_thread_detail(
            focal_tweet_id=tweet_id,
            tweets=tweets,
            raw_json=outcome.payload,
            http_status=outcome.http_status,
        )
        known_tweet_ids.update(tweet.tweet_id for tweet in tweets)
        result.expanded += 1
        return
    result.failed += 1
    if isinstance(outcome, DetailUnavailable):
        record_unavailable(
            store,
            tweet_id=tweet_id,
            operation="ThreadExpandDetail",
            payload=outcome.payload,
            http_status=outcome.http_status,
            reason=outcome.reason,
            detail=outcome.detail,
        )
        _log_thread_status(console, tweet_id, "terminal unavailable (classified for retry)")
    elif isinstance(outcome, DetailAbsent):
        _log_thread_status(console, tweet_id, "ambiguous focal absence (deferred)")
    else:
        _log_thread_status(console, tweet_id, f"failed ({outcome.error})")


@dataclass(slots=True)
class LinkedStatusWalk:
    """Tweets reachable from saves along quote/linked-status edges, keyed by hop depth.

    Depth 0 holds saved tweets and the saved authors' own tweets in saved
    conversations; deeper entries are thread-expansion candidates.
    """

    depths: dict[str, int]
    discovery_kinds: dict[str, str]
    quote_reachable_targets: set[str]
    reachable_quote_count: int = 0
    reachable_link_count: int = 0
    edges_skipped: int = 0
    url_ref_count: int = 0
    self_thread_ids: list[str] = field(default_factory=list)
    reply_ids: list[str] = field(default_factory=list)
    reply_target_ids: set[str] = field(default_factory=set)
    reply_hop_count: int = 0


def walk_linked_statuses(
    store: ArchiveStore,
    membership_ids: list[str],
    *,
    max_linked_depth: int,
    reply_linked_depth: int,
    log: Callable[[str], None] | None = None,
) -> LinkedStatusWalk:
    """Walk the save path the way thread expansion selects linked-status targets."""
    log = log or (lambda _message: None)
    log("loading archived url refs...")
    url_ref_rows = store.list_url_ref_rows()
    url_edges: dict[str, list[str]] = {}
    for row in url_ref_rows:
        target_id = next(
            (
                found
                for field_name in ("canonical_url", "expanded_url", "url")
                if isinstance((candidate := row.get(field_name)), str)
                and (found := extract_status_id_from_url(candidate))
            ),
            None,
        )
        source_id = row.get("tweet_id")
        if isinstance(source_id, str) and target_id:
            url_edges.setdefault(source_id, []).append(target_id)

    walk = LinkedStatusWalk(
        depths={tweet_id: 0 for tweet_id in membership_ids if tweet_id},
        discovery_kinds={},
        quote_reachable_targets=set(),
        url_ref_count=len(url_ref_rows),
    )
    depths = walk.depths

    def follow_edges(sources: list[str], depth: int, *, deterministic: bool = False) -> list[str]:
        """Record unseen quote/link targets of ``sources`` at ``depth``."""
        quote_edges: dict[str, list[str]] = {}
        for row in store.list_quote_relation_rows(set(sources)):
            source_id = row.get("tweet_id")
            target_id = row.get("target_tweet_id")
            if isinstance(source_id, str) and isinstance(target_id, str) and target_id:
                quote_edges.setdefault(source_id, []).append(target_id)

        reached: list[str] = []
        for source_id in sources:
            candidates = [
                *(("quote", target_id) for target_id in quote_edges.get(source_id, [])),
                *(("linked", target_id) for target_id in url_edges.get(source_id, [])),
            ]
            if deterministic:
                candidates.sort(key=lambda item: (item[0] != "quote", item[1]))
            for kind, target_id in candidates:
                if kind == "quote":
                    walk.reachable_quote_count += 1
                    walk.quote_reachable_targets.add(target_id)
                else:
                    walk.reachable_link_count += 1
                if target_id == source_id or target_id in depths:
                    walk.edges_skipped += 1
                    continue
                depths[target_id] = depth
                walk.discovery_kinds[target_id] = kind
                reached.append(target_id)
        return reached

    # The saved author's own continuation of a saved tweet's conversation counts
    # as saved (depth 0): its quotes/links are followed, but the tweets are
    # already archived, so depth-0 entries are never queued for fetching.
    if max_linked_depth > 0:
        log("loading saved-conversation self-threads and replies...")
        self_thread_set, reply_set = store.list_saved_conversation_tweet_ids()
        walk.self_thread_ids = sorted(self_thread_set - depths.keys())
        walk.reply_ids = sorted(reply_set - depths.keys())
        depths.update(dict.fromkeys(walk.self_thread_ids, 0))

    frontier = list(depths)
    for depth in range(1, max_linked_depth + 1):
        if not frontier:
            break
        frontier = follow_edges(frontier, depth)

    # Other people's tweets in saved conversations get at most one hop, run after
    # the save path so its depths win. Reached targets are fetched but never join
    # a frontier, so neither their replies nor their own links are followed.
    if max_linked_depth > 0 and reply_linked_depth > 0 and walk.reply_ids:
        walk.reply_target_ids.update(follow_edges(walk.reply_ids, 1, deterministic=True))
        walk.reply_hop_count = len(walk.reply_target_ids)
    return walk


async def expand_threads(
    *,
    targets: list[str] | None = None,
    limit: int | None = None,
    refresh: bool = False,
    skip_quotes: bool = False,
    config: AppConfig | None = None,
    paths: XDGPaths | None = None,
    auth_bundle: ResolvedAuthBundle | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    console: Console | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> ThreadExpandResult:
    config, paths = resolve_job_context(config=config, paths=paths)
    console = console or Console(stderr=True)
    pipeline = current_pipeline()
    _log_threads(console, "preparing archive expansion job")
    if refresh and not targets:
        raise ConfigError("--refresh requires one or more explicit thread targets.")
    step_key = "threads"
    pipeline.add_step(
        step_key,
        "Threads",
        total=1,
        unit="candidates",
        detail="membership and reachable linked-status candidates",
        rate_unit="candidates/s",
    )
    pipeline.start_step(
        step_key,
        activity="Selecting candidates",
    )

    async with locked_archive_job(config=config, paths=paths, console=console) as job:
        store = job.store
        result = ThreadExpandResult()
        if limit is not None and limit <= 0:
            pipeline.skip_step(step_key, "the requested limit allowing no candidates")
            return result
        _log_threads(console, "loading archived thread expansion state...")
        expanded_before_run = set(store.list_expanded_thread_target_ids())
        _log_threads(
            console,
            f"loaded {len(expanded_before_run)} previously expanded thread targets",
        )
        known_tweet_ids: set[str] = set()
        pending_membership_ids: list[str] = []
        pending_quote_ids: list[str] = []
        pending_linked_ids: list[str] = []
        reply_target_ids: set[str] = set()

        if targets:
            requested, duplicate_count = _dedupe_targets(
                [normalize_thread_target(target) for target in targets]
            )
            result.skipped += duplicate_count
            target_ids = requested[:limit] if limit is not None else requested
            pending_membership_ids = [
                tweet_id
                for tweet_id in target_ids
                if refresh or tweet_id not in expanded_before_run
            ]
            result.skipped += len(target_ids) - len(pending_membership_ids)
            refresh_suffix = " (refresh)" if refresh else ""
            _log_threads(
                console,
                f"explicit target pass over {len(target_ids)} targets{refresh_suffix}",
            )
        else:
            _log_threads(console, "loading archived membership tweets...")
            membership_ids = store.list_membership_tweet_ids()
            pending_membership_ids = [
                tweet_id for tweet_id in membership_ids if tweet_id not in expanded_before_run
            ]
            result.skipped += len(membership_ids) - len(pending_membership_ids)
            _log_threads(
                console,
                "membership pass over "
                f"{len(membership_ids)} archived tweets "
                f"({len(membership_ids) - len(pending_membership_ids)} already expanded)",
            )
            _log_threads(console, "loading known tweet ids for linked-status pass...")
            known_tweet_ids = store.list_known_tweet_ids()
            _log_threads(
                console,
                f"loaded {len(known_tweet_ids)} known tweet ids for linked-status dedupe",
            )
            walk = walk_linked_statuses(
                store,
                membership_ids,
                max_linked_depth=config.sync.max_linked_depth,
                reply_linked_depth=config.sync.reply_linked_depth,
                log=lambda message: _log_threads(console, message),
            )
            result.skipped += walk.edges_skipped

            membership_id_set = set(membership_ids)
            reply_target_ids = walk.reply_target_ids
            for target_id, depth in walk.depths.items():
                if depth == 0:
                    continue
                kind = (
                    "quote"
                    if target_id in walk.quote_reachable_targets
                    else walk.discovery_kinds[target_id]
                )
                if target_id in membership_id_set or target_id in expanded_before_run:
                    result.skipped += 1
                    continue
                if kind == "linked" and target_id in known_tweet_ids:
                    result.skipped += 1
                    continue
                if kind == "quote":
                    pending_quote_ids.append(target_id)
                else:
                    pending_linked_ids.append(target_id)
            _log_threads(
                console,
                "related-status pass over "
                f"{walk.reachable_quote_count} quote relations and "
                f"{walk.reachable_link_count} reachable url refs "
                f"(from {walk.url_ref_count} total url refs, "
                f"max depth {config.sync.max_linked_depth}; "
                f"{len(walk.self_thread_ids)} self-thread seeds; "
                + (
                    f"reply hop from {len(walk.reply_ids)} replies "
                    f"reached {walk.reply_hop_count} new)"
                    if config.sync.reply_linked_depth > 0
                    else f"{len(walk.reply_ids)} replies not followed)"
                ),
            )

        if skip_quotes and not targets:
            _log_threads(
                console,
                "quoted tweet pass skipped by replay sync setting "
                f"({len(pending_quote_ids)} candidates)",
            )
            pending_quote_ids = []

        pending_total = (
            len(pending_membership_ids) + len(pending_quote_ids) + len(pending_linked_ids)
        )
        if pending_total == 0:
            pipeline.skip_step(
                step_key,
                "quote expansion being disabled and no other thread candidates remaining"
                if skip_quotes and not targets
                else "all thread candidates already being expanded or known",
            )
            return result

        pipeline.update_step(
            step_key,
            completed=0,
            total=(min(pending_total, limit) if limit is not None else pending_total),
            activity=(
                "Connecting to Twitter/X"
                if auth_bundle is None
                else "Preparing conversation lookups"
            ),
            counters=(
                f"{len(pending_membership_ids)} membership · "
                f"{len(pending_quote_ids)} quoted · "
                f"{len(pending_linked_ids)} linked · {result.skipped} skipped"
            ),
            detail=(
                f"{len(pending_membership_ids)} membership candidates · "
                f"{len(pending_quote_ids)} quoted-status candidates · "
                f"{len(pending_linked_ids)} reachable linked-status candidates"
            ),
        )

        if auth_bundle is None:
            _log_threads(console, "resolving auth bundle")
            auth_bundle = resolve_auth_bundle(config)
        _log_threads(console, "resolving TweetDetail query ID")
        query_store, query_ids = await resolve_detail_query_ids(paths, transport=transport)
        attempted_targets: set[str] = set()
        attempted_reply_count = 0
        client = build_async_client(auth_bundle, timeout=config.sync.timeout, transport=transport)
        selected_total = min(pending_total, limit) if limit is not None else pending_total
        scanned = 0

        async def select_targets() -> AsyncIterator[tuple[str, str]]:
            nonlocal attempted_reply_count
            for phase, target_ids in (
                ("thread", pending_membership_ids),
                ("quoted tweet", pending_quote_ids),
                ("linked tweet", pending_linked_ids),
            ):
                for tweet_id in target_ids:
                    if limit is not None and result.processed >= limit:
                        break
                    if tweet_id in attempted_targets or (
                        phase == "linked tweet" and tweet_id in known_tweet_ids
                    ):
                        result.skipped += 1
                        continue
                    if tweet_id in reply_target_ids:
                        if attempted_reply_count >= config.sync.reply_linked_limit:
                            continue
                        attempted_reply_count += 1
                    pipeline.update_step(
                        step_key,
                        completed=min(scanned, selected_total),
                        activity=f"Fetching {phase} context",
                        counters=_pipeline_outcomes(result),
                    )
                    result.processed += 1
                    attempted_targets.add(tweet_id)
                    yield tweet_id, phase

        def record(target: tuple[str, str], outcome: DetailOutcome) -> None:
            nonlocal scanned
            _record_thread_outcome(
                store,
                outcome,
                known_tweet_ids=known_tweet_ids,
                result=result,
                console=console,
            )
            scanned += 1
            pipeline.update_step(
                step_key,
                completed=min(scanned, selected_total),
                counters=_pipeline_outcomes(result),
            )
            _log_scan_progress(
                console,
                phase=target[1],
                scanned=scanned,
                total=selected_total,
                result=result,
            )

        try:
            await DetailExecutor(
                requester=TweetDetailRequester(
                    client, query_store, query_ids, config.sync, sleep=sleep
                ),
                pacer=AdaptiveRequestPacer(config.sync.detail_delay),
                retry_status=lambda tweet_id, message: _log_thread_status(
                    console, tweet_id, message
                ),
                sleep=sleep,
            ).run(select_targets(), tweet_id_of=lambda target: target[0], record=record)
        finally:
            await client.aclose()

        pipeline.update_step(
            step_key,
            completed=max(scanned, 1),
            total=max(scanned, 1),
        )
        pipeline.complete_step(
            step_key,
            _pipeline_outcomes(result, final=True),
            metrics={
                "expanded": result.expanded,
                "already_known": result.skipped,
                "failed": result.failed,
            },
        )
        return result
