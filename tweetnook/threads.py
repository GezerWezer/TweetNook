"""Thread/context expansion helpers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx
from rich.console import Console

from tweetnook.auth import ResolvedAuthBundle, resolve_auth_bundle
from tweetnook.client.base import AdaptiveRequestPacer, build_async_client
from tweetnook.client.timelines import (
    MAX_CONSECUTIVE_FOCAL_ABSENCES,
    FocalResultKind,
    FocalTweetDetailResult,
    TimelineTweet,
    build_tweet_detail_url,
    fetch_page,
    parse_tweet_detail_response,
    parse_tweet_detail_tweets,
)
from tweetnook.config import AppConfig, XDGPaths
from tweetnook.exceptions import (
    APIResponseError,
    AuthExpiredError,
    ConfigError,
    FeatureFlagDriftError,
    QueryIdRefreshError,
    RateLimitExhaustedError,
    RepeatedFocalAbsenceError,
    StaleQueryIdError,
)
from tweetnook.extractor import extract_status_id_from_url
from tweetnook.jobs import locked_archive_job, resolve_job_context
from tweetnook.pipeline import current_pipeline
from tweetnook.query_ids import QueryIdStore, refresh_query_ids
from tweetnook.storage import ArchiveStore
from tweetnook.utils import resolve_query_ids

_SCAN_PROGRESS_EVERY = 100


@dataclass(slots=True)
class ThreadExpandResult:
    processed: int = 0
    expanded: int = 0
    skipped: int = 0
    failed: int = 0


@dataclass(slots=True)
class _FocalAbsenceTracker:
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


def normalize_thread_target(value: str) -> str:
    candidate = value.strip()
    if candidate.isdigit():
        return candidate
    tweet_id = extract_status_id_from_url(candidate)
    if tweet_id:
        return tweet_id
    raise ConfigError(
        f"Unsupported thread target '{value}'. Use a tweet ID or Twitter/X status URL."
    )


def _dedupe_targets(values: list[str]) -> tuple[list[str], int]:
    unique = list(dict.fromkeys(values))
    return unique, len(values) - len(unique)


def _log_thread_status(console: Console, tweet_id: str, message: str) -> None:
    pipeline = current_pipeline()
    if pipeline is not None:
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
    if pipeline is not None:
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
    if current_pipeline() is not None:
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


async def _fetch_detail(
    *,
    tweet_id: str,
    query_ids: dict[str, str],
    query_store: QueryIdStore,
    client: httpx.AsyncClient,
    config: AppConfig,
    console: Console,
    pacer: AdaptiveRequestPacer | None = None,
) -> tuple[dict[str, object], list[TimelineTweet], FocalTweetDetailResult, int]:
    async def refresh_once() -> str:
        refreshed = await refresh_query_ids(
            query_store,
            operations=["TweetDetail"],
            client=client,
        )
        query_ids.update(refreshed)
        return build_tweet_detail_url(query_ids["TweetDetail"], tweet_id)

    response = await fetch_page(
        client,
        build_tweet_detail_url(query_ids["TweetDetail"], tweet_id),
        config.sync,
        max_retries=config.sync.detail_max_retries,
        backoff_base=config.sync.detail_backoff_base,
        refresh_once=refresh_once,
        status=lambda message: _log_thread_status(console, tweet_id, message),
    )
    if pacer is not None:
        pacer.observe(response)
    payload = response.json()
    tweets = parse_tweet_detail_tweets(payload)
    focal = parse_tweet_detail_response(payload, tweet_id)
    return payload, tweets, focal, response.status_code


async def _expand_target(
    *,
    tweet_id: str,
    store: ArchiveStore,
    query_ids: dict[str, str],
    query_store: QueryIdStore,
    client: httpx.AsyncClient,
    config: AppConfig,
    console: Console,
    pacer: AdaptiveRequestPacer | None = None,
    mark_dirty: Callable[[int, int], None] | None = None,
) -> tuple[list[str], FocalResultKind]:
    payload, tweets, focal, http_status = await _fetch_detail(
        tweet_id=tweet_id,
        query_ids=query_ids,
        query_store=query_store,
        client=client,
        config=config,
        console=console,
        pacer=pacer,
    )
    if focal.kind == FocalResultKind.AVAILABLE:
        store.persist_thread_detail(
            focal_tweet_id=tweet_id,
            tweets=tweets,
            raw_json=payload,
            http_status=http_status,
        )
    elif focal.kind == FocalResultKind.EXPLICIT_UNAVAILABLE:
        assert focal.unavailable is not None
        from tweetnook.resurrection import resurrection_retry_schedule

        retry_eligible, next_retry_at = resurrection_retry_schedule(
            focal.unavailable.reason,
            0,
        )
        store.persist_unavailable_tweet(
            tweet_id=tweet_id,
            operation="ThreadExpandDetail",
            raw_json=payload,
            http_status=http_status,
            reason=focal.unavailable.reason,
            detail=focal.unavailable.detail,
            retry_eligible=retry_eligible,
            next_retry_at=next_retry_at,
            retry_count=0,
        )
    if mark_dirty is not None and focal.kind != FocalResultKind.ABSENT:
        mark_dirty(1, 1)
    return [tweet.tweet_id for tweet in tweets], focal.kind


async def _try_expand_target(
    *,
    tweet_id: str,
    store: ArchiveStore,
    query_ids: dict[str, str],
    query_store: QueryIdStore,
    client: httpx.AsyncClient,
    config: AppConfig,
    attempted_targets: set[str],
    expanded_targets: set[str],
    known_tweet_ids: set[str],
    result: ThreadExpandResult,
    console: Console,
    absence_tracker: _FocalAbsenceTracker,
    pacer: AdaptiveRequestPacer | None = None,
    mark_dirty: Callable[[int, int], None] | None = None,
) -> None:
    result.processed += 1
    attempted_targets.add(tweet_id)
    try:
        discovered_ids, focal_kind = await _expand_target(
            tweet_id=tweet_id,
            store=store,
            query_ids=query_ids,
            query_store=query_store,
            client=client,
            config=config,
            console=console,
            pacer=pacer,
            mark_dirty=mark_dirty,
        )
    except (
        AuthExpiredError,
        FeatureFlagDriftError,
        QueryIdRefreshError,
        RateLimitExhaustedError,
        RepeatedFocalAbsenceError,
        StaleQueryIdError,
    ):
        raise
    except (APIResponseError, httpx.TransportError) as exc:
        result.failed += 1
        _log_thread_status(console, tweet_id, f"failed ({exc})")
        return
    except Exception:
        raise

    absence_tracker.observe(focal_kind, tweet_id)
    if focal_kind == FocalResultKind.ABSENT:
        result.failed += 1
        _log_thread_status(console, tweet_id, "ambiguous focal absence (deferred)")
        return
    expanded_targets.add(tweet_id)
    if focal_kind == FocalResultKind.EXPLICIT_UNAVAILABLE:
        result.failed += 1
        _log_thread_status(console, tweet_id, "terminal unavailable (classified for retry)")
        return
    known_tweet_ids.update(discovered_ids)
    result.expanded += 1


async def expand_threads(
    *,
    targets: list[str] | None = None,
    limit: int | None = None,
    refresh: bool = False,
    config: AppConfig | None = None,
    paths: XDGPaths | None = None,
    auth_bundle: ResolvedAuthBundle | None = None,
    auth_status: Callable[[str], None] | None = None,
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
    if pipeline is not None:
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
            activity="Selecting thread candidates from local archive state",
            counters="loading memberships, prior expansions, and saved status links",
        )

    async with locked_archive_job(config=config, paths=paths, console=console) as job:
        store = job.store
        result = ThreadExpandResult()
        if limit is not None and limit <= 0:
            if pipeline is not None:
                pipeline.skip_step(step_key, "the requested limit allowing no candidates")
            return result
        _log_threads(console, "loading archived thread expansion state...")
        expanded_targets = set(store.list_raw_capture_target_ids("ThreadExpandDetail"))
        _log_threads(
            console,
            f"loaded {len(expanded_targets)} previously expanded thread targets",
        )
        known_tweet_ids: set[str] = set()
        pending_membership_ids: list[str] = []
        pending_quote_ids: list[str] = []
        pending_linked_ids: list[str] = []

        if targets:
            requested, duplicate_count = _dedupe_targets(
                [normalize_thread_target(target) for target in targets]
            )
            result.skipped += duplicate_count
            target_ids = requested[:limit] if limit is not None else requested
            pending_membership_ids = [
                tweet_id for tweet_id in target_ids if refresh or tweet_id not in expanded_targets
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
                tweet_id for tweet_id in membership_ids if tweet_id not in expanded_targets
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
            _log_threads(console, "loading archived url refs...")
            url_ref_rows = store.list_url_ref_rows()
            max_linked_depth = config.sync.max_linked_depth
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

            depths = {tweet_id: 0 for tweet_id in membership_ids if tweet_id}
            discovery_kinds: dict[str, str] = {}
            quote_reachable_targets: set[str] = set()
            reachable_quote_count = 0
            reachable_link_count = 0
            for depth in range(1, max_linked_depth + 1):
                frontier = [
                    tweet_id for tweet_id, known_depth in depths.items() if known_depth == depth - 1
                ]
                quote_edges: dict[str, list[str]] = {}
                for row in store.list_quote_relation_rows(set(frontier)):
                    source_id = row.get("tweet_id")
                    target_id = row.get("target_tweet_id")
                    if isinstance(source_id, str) and isinstance(target_id, str) and target_id:
                        quote_edges.setdefault(source_id, []).append(target_id)

                for source_id in frontier:
                    candidates = [
                        *(("quote", target_id) for target_id in quote_edges.get(source_id, [])),
                        *(("linked", target_id) for target_id in url_edges.get(source_id, [])),
                    ]
                    for kind, target_id in candidates:
                        if kind == "quote":
                            reachable_quote_count += 1
                            quote_reachable_targets.add(target_id)
                        else:
                            reachable_link_count += 1
                        if target_id == source_id:
                            result.skipped += 1
                            continue
                        if target_id in depths:
                            result.skipped += 1
                            continue
                        depths[target_id] = depth
                        discovery_kinds[target_id] = kind

            membership_id_set = set(membership_ids)
            for target_id, depth in depths.items():
                if depth == 0:
                    continue
                kind = (
                    "quote" if target_id in quote_reachable_targets else discovery_kinds[target_id]
                )
                if target_id in membership_id_set or target_id in expanded_targets:
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
                f"{reachable_quote_count} quote relations and "
                f"{reachable_link_count} reachable url refs "
                f"(from {len(url_ref_rows)} total url refs, max depth {max_linked_depth})",
            )

        pending_total = (
            len(pending_membership_ids) + len(pending_quote_ids) + len(pending_linked_ids)
        )
        if pending_total == 0:
            if pipeline is not None:
                pipeline.skip_step(
                    step_key, "all thread candidates already being expanded or known"
                )
            return result

        if pipeline is not None:
            pipeline.update_step(
                step_key,
                completed=0,
                total=(min(pending_total, limit) if limit is not None else pending_total),
                activity=(
                    "Resolving Twitter/X authentication"
                    if auth_bundle is None
                    else "Resolving the TweetDetail operation ID"
                ),
                counters=(
                    f"{len(pending_membership_ids)} membership · "
                    f"{len(pending_quote_ids)} quoted · "
                    f"{len(pending_linked_ids)} linked · {result.skipped} already known"
                ),
                detail=(
                    f"{len(pending_membership_ids)} membership candidates · "
                    f"{len(pending_quote_ids)} quoted-status candidates · "
                    f"{len(pending_linked_ids)} reachable linked-status candidates"
                ),
            )

        if auth_bundle is None:
            _log_threads(console, "resolving auth bundle")
            auth_bundle = resolve_auth_bundle(config, status=auth_status)
        _log_threads(console, "resolving TweetDetail query ID")
        query_store = QueryIdStore(paths)
        query_ids = await resolve_query_ids(
            query_store,
            ["TweetDetail"],
            force_refresh=not query_store.is_fresh(),
            transport=transport,
        )
        attempted_targets: set[str] = set()
        pacer = AdaptiveRequestPacer(config.sync.detail_delay)
        absence_tracker = _FocalAbsenceTracker()
        client = build_async_client(auth_bundle, timeout=config.sync.timeout, transport=transport)
        selected_total = min(pending_total, limit) if limit is not None else pending_total
        scanned = 0
        try:
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
                    if pipeline is not None:
                        pipeline.update_step(
                            step_key,
                            completed=min(scanned, selected_total),
                            activity=f"Fetching {phase} context for {tweet_id}",
                            counters=(
                                f"{result.processed} fetched · {result.expanded} expanded · "
                                f"{result.skipped} already known · {result.failed} failed"
                            ),
                        )
                    await pacer.wait(attempted=result.processed, sleep=sleep)
                    await _try_expand_target(
                        tweet_id=tweet_id,
                        store=store,
                        query_ids=query_ids,
                        query_store=query_store,
                        client=client,
                        config=config,
                        attempted_targets=attempted_targets,
                        expanded_targets=expanded_targets,
                        known_tweet_ids=known_tweet_ids,
                        result=result,
                        console=console,
                        absence_tracker=absence_tracker,
                        pacer=pacer,
                        mark_dirty=job.mark_dirty,
                    )
                    scanned += 1
                    if pipeline is not None:
                        pipeline.update_step(
                            step_key,
                            completed=min(scanned, selected_total),
                            counters=(
                                f"{result.processed} fetched · {result.expanded} expanded · "
                                f"{result.skipped} already known · {result.failed} failed"
                            ),
                        )
                    _log_scan_progress(
                        console,
                        phase=phase,
                        scanned=scanned,
                        total=selected_total,
                        result=result,
                    )
                    job.maybe_optimize_mid_job(console=console)
        finally:
            await client.aclose()

        if pipeline is not None:
            pipeline.update_step(
                step_key,
                completed=max(scanned, 1),
                total=max(scanned, 1),
            )
            pipeline.complete_step(
                step_key,
                f"{result.processed} fetched · {result.expanded} expanded · "
                f"{result.skipped} already known · {result.failed} failed",
            )
        return result
