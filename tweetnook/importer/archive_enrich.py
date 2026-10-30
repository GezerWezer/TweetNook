"""Completing imported posts after ingest: live collection reconciliation and detail
enrichment through X or FxTwitter, for a new import or one already in the archive."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from rich.console import Console

from tweetnook.auth import ResolvedAuthBundle, resolve_auth_bundle
from tweetnook.client import fxtwitter
from tweetnook.client.base import AdaptiveRequestPacer, build_async_client
from tweetnook.client.timelines import build_tweet_detail_url, fetch_page
from tweetnook.config import AppConfig, XDGPaths
from tweetnook.core.exceptions import (
    APIResponseError,
    AuthExpiredError,
    ConfigError,
    FeatureFlagDriftError,
    RateLimitExhaustedError,
    RepeatedFocalAbsenceError,
    StaleQueryIdError,
    TweetNookError,
)
from tweetnook.core.fxtwitter_payloads import fx_status_to_graphql, fx_timeline_tweet
from tweetnook.core.timeline_parsing import (
    FOCAL_ABSENCE_DETAIL,
    MAX_CONSECUTIVE_FOCAL_ABSENCES,
    FocalResultKind,
    parse_tweet_detail_response,
)
from tweetnook.core.utils import utc_now
from tweetnook.enrichment.resurrection import resurrection_retry_schedule, transient_retry_at
from tweetnook.importer.archive_manifests import (
    aggregate_import_counts,
    finalize_import_enrichment_status,
    list_import_manifest_rows,
)
from tweetnook.importer.archive_progress import (
    _progress_callback,
    _status_printer,
    runner_console_for,
)
from tweetnook.importer.archive_rows import flush_buffer
from tweetnook.jobs.context import (
    locked_archive_job,
    resolve_job_context,
)
from tweetnook.jobs.interactive import emit_status
from tweetnook.jobs.pipeline import current_pipeline
from tweetnook.jobs.resource_failure import raise_if_storage_exhausted
from tweetnook.query_ids import QueryIdStore, refresh_query_ids, resolve_query_ids
from tweetnook.storage import ArchiveStore
from tweetnook.storage.records import PageBuffer
from tweetnook.sync.collection import sync_collection

_DETAIL_ENRICH_WRITE_BATCH = 100


_FX_ENRICH_PREFETCH = 100


@dataclass(slots=True)
class ArchiveEnrichResult:
    warnings: list[str] = field(default_factory=list)
    reconciled_collections: list[str] = field(default_factory=list)
    detail_lookups: int = 0
    detail_terminal_unavailable: int = 0
    detail_transient_failures: int = 0
    pending_enrichment: int = 0
    pending_untouched: int = 0
    transient_due: int = 0
    transient_delayed: int = 0


class ArchiveEnrichmentInterrupted(KeyboardInterrupt):
    def __init__(self, remaining: int):
        super().__init__("Archive enrichment interrupted.")
        self.remaining = remaining


class ArchiveEnrichmentAborted(TweetNookError):
    def __init__(self, remaining: int, cause: Exception):
        super().__init__(f"Archive enrichment stopped after {cause.__class__.__name__}: {cause}")
        self.remaining = remaining
        self.cause = cause


@dataclass(slots=True)
class _FocalAbsenceTracker:
    consecutive: int = 0

    def observe(self, kind: FocalResultKind, tweet_id: str) -> None:
        if kind == FocalResultKind.ABSENT:
            self.consecutive += 1
            if self.consecutive >= MAX_CONSECUTIVE_FOCAL_ABSENCES:
                raise RepeatedFocalAbsenceError(
                    f"TweetDetail omitted its requested focal tweet for "
                    f"{self.consecutive} consecutive responses; last target was {tweet_id}."
                )
        else:
            self.consecutive = 0


def _followup_collections_from_counts(counts: dict[str, int]) -> list[str]:
    collections: list[str] = []
    if counts.get("authored_tweets") or counts.get("deleted_authored_tweets"):
        collections.append("tweets")
    if counts.get("likes"):
        collections.append("likes")
    return collections


def _resolve_archive_followup_auth(
    config: AppConfig,
    auth_bundle: ResolvedAuthBundle | None,
    *,
    optional: bool = False,
) -> ResolvedAuthBundle | None:
    pipeline = current_pipeline()
    step_key = "archive-auth"
    if pipeline is not None:
        pipeline.add_step(
            step_key,
            "Authentication",
            total=1,
            unit="session",
            detail="resolve the configured Twitter/X session values for live follow-up",
            show_rate=False,
            show_eta=False,
        )
        pipeline.start_step(
            step_key,
            activity="Resolving Twitter/X authentication for archive follow-up",
        )
    try:
        resolved = auth_bundle if auth_bundle is not None else resolve_auth_bundle(config)
    except ConfigError as exc:
        if optional:
            # FxTwitter answers most enrichment lookups without an account.
            if pipeline is not None:
                pipeline.skip_step(step_key, "no Twitter/X account being connected")
            return None
        if pipeline is not None:
            pipeline.fail_step(step_key, str(exc))
        raise
    if pipeline is not None:
        pipeline.complete_step(step_key, "Twitter/X authentication resolved")
    return resolved


async def _run_live_reconciliation(
    *,
    collections: list[str],
    config: AppConfig,
    paths: XDGPaths,
    auth_bundle: ResolvedAuthBundle | None,
    transport: httpx.AsyncBaseTransport | None,
    console: Console,
    status: Callable[[str], None] | None = None,
) -> tuple[list[str], list[str], ResolvedAuthBundle | None]:
    warnings: list[str] = []
    pipeline = current_pipeline()
    if pipeline is not None:
        for collection in ("tweets", "likes"):
            if collection in collections:
                continue
            for step_key in (f"preflight:{collection}", f"sync:{collection}:head"):
                if pipeline.has_step(step_key):
                    pipeline.skip_step(
                        step_key,
                        f"the archive containing no {collection} dataset rows",
                    )
    try:
        resolved_auth = _resolve_archive_followup_auth(config, auth_bundle)
    except ConfigError as exc:
        warnings.append(f"live reconciliation skipped: {exc}")
        if pipeline is not None:
            for collection in collections:
                for step_key in (f"preflight:{collection}", f"sync:{collection}:head"):
                    if pipeline.has_step(step_key):
                        pipeline.skip_step(step_key, "Twitter/X authentication being unavailable")
        return [], warnings, None

    completed: list[str] = []
    for collection in collections:
        try:
            emit_status(status, f"running live {collection} reconciliation...")
            await sync_collection(
                collection,
                full=False,
                resume_backfill=False,
                limit=None,
                config=config,
                paths=paths,
                auth_bundle=resolved_auth,
                transport=transport,
                console=console,
            )
            completed.append(collection)
        except Exception as exc:
            raise_if_storage_exhausted(exc)
            warnings.append(f"{collection} reconciliation failed: {exc}")
    return completed, warnings, resolved_auth


async def _run_archive_followup(
    *,
    collections: list[str],
    detail_limit: int | None,
    reconcile_live: bool,
    config: AppConfig,
    paths: XDGPaths,
    auth_bundle: ResolvedAuthBundle | None,
    transport: httpx.AsyncBaseTransport | None,
    console: Console,
    status: Callable[[str], None] | None = None,
    enrichment_started_at: str | None = None,
    account_optional: bool = False,
) -> ArchiveEnrichResult:
    warnings: list[str] = []
    if reconcile_live:
        reconciled_collections, reconcile_warnings, resolved_auth = await _run_live_reconciliation(
            collections=collections,
            config=config,
            paths=paths,
            auth_bundle=auth_bundle,
            transport=transport,
            console=console,
            status=status,
        )
        warnings.extend(reconcile_warnings)
    else:
        reconciled_collections = []
        try:
            resolved_auth = _resolve_archive_followup_auth(
                config,
                auth_bundle,
                optional=account_optional
                or fxtwitter.fx_enabled(config)
                or bool(config.replay.url),
            )
        except ConfigError as exc:
            warnings.append(f"detail enrichment skipped: {exc}")
            resolved_auth = None
    if resolved_auth is None and config.replay.url:
        # The replay source answers detail lookups without an account: no cookie is ever
        # forwarded to it, so placeholder values stand in for the missing session.
        resolved_auth = ResolvedAuthBundle(
            auth_token="replay",
            ct0="replay",
            auth_token_source="replay",
            ct0_source="replay",
        )
    enrichment_result = ArchiveEnrichResult()
    if resolved_auth is not None or fxtwitter.fx_enabled(config):
        try:
            absence_tracker = _FocalAbsenceTracker()
            enrichment_result = await _enrich_pending_rows(
                limit=detail_limit,
                config=config,
                paths=paths,
                auth_bundle=resolved_auth,
                transport=transport,
                console=console,
                status=status,
                absence_tracker=absence_tracker,
                started_at=enrichment_started_at,
            )
        except Exception as exc:
            raise_if_storage_exhausted(exc)
            async with locked_archive_job(config=config, paths=paths) as job:
                remaining = job.store.count_incomplete_initial_enrichment()
            raise ArchiveEnrichmentAborted(remaining, exc) from exc
    else:
        async with locked_archive_job(config=config, paths=paths) as job:
            enrichment_result = _archive_enrich_counts(job.store)
        pipeline = current_pipeline()
        if pipeline is not None and pipeline.has_step("archive-enrich"):
            pipeline.skip_step("archive-enrich", "Twitter/X authentication being unavailable")
    enrichment_result.warnings = warnings
    enrichment_result.reconciled_collections = reconciled_collections
    pipeline = current_pipeline()
    if pipeline is not None:
        for warning in warnings:
            pipeline.issue(
                warning,
                dedupe_key=f"archive-followup:{warning.split(':', 1)[0]}",
            )
    return enrichment_result


def _followup_manifest_rows(store: ArchiveStore, digest: str | None) -> list[dict[str, Any]]:
    if digest is None:
        return list_import_manifest_rows(store)
    manifest_row = store.get_import_manifest(digest)
    return [manifest_row] if manifest_row is not None else []


async def run_import_followup(
    *,
    counts: dict[str, int],
    manifest_digest: str | None,
    detail_limit: int | None,
    reconcile_live: bool,
    config: AppConfig,
    paths: XDGPaths,
    auth_bundle: ResolvedAuthBundle | None,
    transport: httpx.AsyncBaseTransport | None,
    console: Console,
    status: Callable[[str], None] | None = None,
    enrichment_started_at: str | None = None,
    account_optional: bool = False,
) -> ArchiveEnrichResult:
    """Reconcile and enrich the collections an import's counts cover.

    An interrupted or failed follow-up is recorded on the import manifest it belongs to
    (manifest_digest, or every completed import when None) before the error propagates, so
    the next run and the UI know enrichment stopped part way. Recording success is left to
    the caller, which knows what counts to write.
    """
    try:
        return await _run_archive_followup(
            collections=_followup_collections_from_counts(counts),
            detail_limit=detail_limit,
            reconcile_live=reconcile_live,
            config=config,
            paths=paths,
            auth_bundle=auth_bundle,
            transport=transport,
            console=console,
            status=status,
            enrichment_started_at=enrichment_started_at,
            account_optional=account_optional,
        )
    except (KeyboardInterrupt, asyncio.CancelledError) as exc:
        async with locked_archive_job(config=config, paths=paths) as job:
            remaining = job.store.count_incomplete_initial_enrichment()
            for manifest_row in _followup_manifest_rows(job.store, manifest_digest):
                finalize_import_enrichment_status(
                    job.store,
                    manifest_row,
                    followup_status="enrichment_interrupted",
                    aborted_reason=str(exc) or exc.__class__.__name__,
                )
        raise ArchiveEnrichmentInterrupted(remaining) from exc
    except Exception as exc:
        raise_if_storage_exhausted(exc)
        async with locked_archive_job(config=config, paths=paths) as job:
            for manifest_row in _followup_manifest_rows(job.store, manifest_digest):
                finalize_import_enrichment_status(
                    job.store,
                    manifest_row,
                    followup_status="enrichment_aborted",
                    aborted_reason=str(exc),
                )
        raise


def _archive_enrich_counts(store: ArchiveStore, *, now: str | None = None) -> ArchiveEnrichResult:
    return ArchiveEnrichResult(
        pending_enrichment=store.count_incomplete_initial_enrichment(),
        pending_untouched=store.count_pending_initial_enrichment(),
        transient_due=store.count_due_transient_enrichment(now),
        transient_delayed=store.count_delayed_transient_enrichment(now),
    )


async def _enrich_pending_rows(
    *,
    limit: int | None,
    config: AppConfig,
    paths: XDGPaths,
    auth_bundle: ResolvedAuthBundle | None,
    transport: httpx.AsyncBaseTransport | None,
    console: Console,
    status: Callable[[str], None] | None = None,
    absence_tracker: _FocalAbsenceTracker | None = None,
    started_at: str | None = None,
) -> ArchiveEnrichResult:
    pipeline = current_pipeline()
    if limit is not None and limit <= 0:
        async with locked_archive_job(config=config, paths=paths, console=console) as job:
            result = _archive_enrich_counts(job.store)
        if pipeline is not None and pipeline.has_step("archive-enrich"):
            pipeline.skip_step("archive-enrich", "detail enrichment being disabled for this run")
        return result

    async with locked_archive_job(config=config, paths=paths, console=console) as job:
        store = job.store
        selection_started_at = started_at or utc_now()
        rows = store.list_tweet_objects_for_enrichment(limit=limit, now=selection_started_at)
        desc_type = "eligible sparse tweets"
        if not rows:
            result = _archive_enrich_counts(store, now=selection_started_at)
            emit_status(status, "no archive enrichment rows are currently due")
            if result.transient_delayed:
                emit_status(
                    status,
                    f"{result.transient_delayed:,} transient failures remain scheduled "
                    "for later retry",
                )
            if pipeline is not None and pipeline.has_step("archive-enrich"):
                pipeline.skip_step(
                    "archive-enrich",
                    "no sparse archive tweets being eligible for enrichment",
                )
            return result
        queue_counts = _archive_enrich_counts(store, now=selection_started_at)
        step_key = "archive-enrich"
        if pipeline is not None:
            pipeline.add_step(
                step_key,
                "Enrich",
                total=len(rows),
                unit="tweets",
                detail=(
                    f"stable command-start snapshot · {queue_counts.transient_delayed} "
                    f"delayed outside this run · {_DETAIL_ENRICH_WRITE_BATCH}-attempt checkpoints"
                ),
                rate_unit="tweets/s",
            )
            pipeline.start_step(
                step_key,
                activity=f"Fetching live details for tweet {rows[0]['tweet_id']}",
                counters="0 checked · 0 enriched · 0 unavailable · 0 transient",
            )
        limit_suffix = "" if limit is None else f" (limit {limit})"
        emit_status(status, f"detail enrichment over {len(rows)} {desc_type}{limit_suffix}")
        query_store = QueryIdStore(paths)
        # Without an account only FxTwitter runs; rows it can't answer wait for TweetDetail.
        client = None
        query_ids: dict[str, str] = {}
        if auth_bundle is not None:
            query_ids = await resolve_query_ids(
                query_store,
                ["TweetDetail"],
                force_refresh=not query_store.is_fresh(),
                transport=transport,
            )
            client = build_async_client(
                auth_bundle, timeout=config.sync.timeout, transport=transport
            )
        succeeded = 0
        terminal = 0
        transient = 0
        deferred = 0
        pacer = AdaptiveRequestPacer(config.sync.detail_delay)
        write_buffer = PageBuffer()
        buffered_writes = 0
        absence_tracker = absence_tracker or _FocalAbsenceTracker()
        # FxTwitter answers most lookups without the login; TweetDetail handles the rest.
        fx_client = fxtwitter.build_fx_client(config) if fxtwitter.fx_enabled(config) else None
        fx_results: dict[str, fxtwitter.FxLookup] = {}
        fx_served = 0
        detail_attempts = 0

        def flush_detail_writes() -> None:
            nonlocal buffered_writes
            if buffered_writes <= 0:
                return
            flush_buffer(store, write_buffer)
            buffered_writes = 0

        # Rows finished so far, in whatever order FxTwitter answers, so progress never jumps.
        done = 0
        detail_progress = None

        def report_progress(activity: str | None = None) -> None:
            if detail_progress:
                detail_progress(done, len(rows))
            if pipeline is None:
                return
            counters = (
                f"{done} checked · {succeeded} enriched · "
                f"{terminal} unavailable · {transient} transient"
            )
            if client is None:
                counters += f" · {deferred} need account"
            pipeline.update_step(
                step_key,
                completed=done,
                counters=counters,
                **({"activity": activity} if activity else {}),
            )

        def save_fx_lookup(lookup: fxtwitter.FxLookup) -> None:
            nonlocal succeeded, fx_served, buffered_writes, done
            if lookup.kind != "available":
                return
            assert lookup.status is not None
            store.persist_fx_status(
                tweet=fx_timeline_tweet(fx_status_to_graphql(lookup.status)),
                payload=lookup.payload,
                http_status=lookup.http_status or 200,
                cursor=write_buffer,
            )
            succeeded += 1
            fx_served += 1
            done += 1
            buffered_writes += 1
            if buffered_writes >= _DETAIL_ENRICH_WRITE_BATCH:
                flush_detail_writes()
            report_progress()

        try:
            with _progress_callback(
                console,
                label="archive enrich detail",
                total=len(rows),
                unit="tweets",
                leave=False,
            ) as detail_progress:
                for index, row in enumerate(rows, start=1):
                    tweet_id = row["tweet_id"]
                    wrote_row = False
                    absence_error: RepeatedFocalAbsenceError | None = None
                    if fx_client is not None and (index - 1) % _FX_ENRICH_PREFETCH == 0:
                        report_progress("Looking up posts through FxTwitter")
                        fx_results, fx_down = await fxtwitter.fetch_fx_statuses(
                            fx_client,
                            [
                                str(item["tweet_id"])
                                for item in rows[index - 1 : index - 1 + _FX_ENRICH_PREFETCH]
                            ],
                            concurrency=config.fxtwitter.concurrency,
                            on_lookup=save_fx_lookup,
                        )
                        if fx_down:
                            # FxTwitter is down; TweetDetail handles the rest of this run.
                            await fx_client.aclose()
                            fx_client = None
                            console.print(
                                "[yellow]FxTwitter isn't answering; using TweetDetail "
                                "for the rest of this run.[/yellow]"
                            )
                    fx_lookup = fx_results.get(tweet_id)
                    if fx_lookup is not None and fx_lookup.kind == "available":
                        continue  # Saved and counted as FxTwitter answered.
                    if client is None:
                        # Left as-is so the next authenticated run picks it up.
                        if fx_client is None:
                            remaining = [
                                item
                                for item in rows[index - 1 :]
                                if getattr(fx_results.get(item["tweet_id"]), "kind", None)
                                != "available"
                            ]
                            deferred += len(remaining)
                            done += len(remaining)
                            report_progress()
                            break
                        deferred += 1
                        done += 1
                        report_progress()
                        continue
                    report_progress(f"Fetching live details for tweet {tweet_id}")
                    await pacer.wait(attempted=detail_attempts)
                    detail_attempts += 1

                    async def refresh_once(tweet_id: str = tweet_id) -> str:
                        refreshed = await refresh_query_ids(
                            query_store,
                            operations=["TweetDetail"],
                            client=client,
                        )
                        query_ids.update(refreshed)
                        return build_tweet_detail_url(query_ids["TweetDetail"], tweet_id)

                    write_checkpoint = write_buffer.checkpoint()
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
                                    lambda message, tweet_id=tweet_id: emit_status(
                                        status, f"detail {tweet_id}: {message}"
                                    )
                                )
                                if status is not None
                                else (
                                    lambda message, tweet_id=tweet_id: pipeline.status(
                                        step_key,
                                        f"Enrichment tweet {tweet_id}: {message}",
                                        important=True,
                                    )
                                    if pipeline is not None
                                    else None
                                )
                            ),
                        )
                        pacer.observe(
                            response,
                            status=(
                                status
                                if status is not None
                                else (
                                    lambda message: pipeline.status(
                                        step_key, message, important=True
                                    )
                                    if pipeline is not None
                                    else None
                                )
                            ),
                        )
                        payload = response.json()
                        focal = parse_tweet_detail_response(payload, tweet_id)
                        if focal.kind == FocalResultKind.AVAILABLE:
                            assert focal.tweet is not None
                            absence_tracker.observe(focal.kind, tweet_id)
                            store.persist_tweet_detail(
                                tweet=focal.tweet,
                                raw_json=payload,
                                http_status=response.status_code,
                                cursor=write_buffer,
                            )
                            succeeded += 1
                        elif focal.kind == FocalResultKind.EXPLICIT_UNAVAILABLE:
                            absence_tracker.observe(focal.kind, tweet_id)
                            assert focal.unavailable is not None
                            eligible, next_retry = resurrection_retry_schedule(
                                focal.unavailable.reason, 0
                            )
                            store.persist_unavailable_tweet(
                                tweet_id=tweet_id,
                                operation="TweetDetail",
                                raw_json=payload,
                                http_status=response.status_code,
                                reason=focal.unavailable.reason,
                                detail=focal.unavailable.detail,
                                retry_eligible=eligible,
                                next_retry_at=next_retry,
                                retry_count=0,
                                cursor=write_buffer,
                            )
                            terminal += 1
                        else:
                            assert focal.kind == FocalResultKind.ABSENT
                            write_buffer.restore(write_checkpoint)
                            retry_count = int(row.get("enrichment_retry_count") or 0) + 1
                            store.update_tweet_object_enrichment(
                                tweet_id,
                                enrichment_state="transient_failure",
                                enrichment_checked_at=utc_now(),
                                enrichment_http_status=response.status_code,
                                enrichment_reason="focal_tweet_absent",
                                enrichment_detail=FOCAL_ABSENCE_DETAIL,
                                enrichment_retry_count=retry_count,
                                enrichment_next_retry_at=transient_retry_at(retry_count),
                                enrichment_retry_eligible=None,
                                cursor=write_buffer,
                            )
                            transient += 1
                            if pipeline is not None:
                                pipeline.issue(
                                    f"Enrichment tweet {tweet_id}: focal tweet absent; "
                                    "retry scheduled",
                                    dedupe_key="archive-enrich:focal-absence",
                                )
                        wrote_row = True
                        if focal.kind == FocalResultKind.ABSENT:
                            try:
                                absence_tracker.observe(focal.kind, tweet_id)
                            except RepeatedFocalAbsenceError as exc:
                                absence_error = exc
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
                            eligible, next_retry = resurrection_retry_schedule("not_found", 0)
                            store.persist_unavailable_tweet(
                                tweet_id=tweet_id,
                                operation="TweetDetail",
                                raw_json={"http_status": 410, "error": str(exc)},
                                http_status=exc.status_code,
                                reason="not_found",
                                detail=str(exc),
                                retry_eligible=eligible,
                                next_retry_at=next_retry,
                                retry_count=0,
                                cursor=write_buffer,
                            )
                            terminal += 1
                            wrote_row = True
                        else:
                            retry_count = int(row.get("enrichment_retry_count") or 0) + 1
                            store.update_tweet_object_enrichment(
                                tweet_id,
                                enrichment_state="transient_failure",
                                enrichment_checked_at=utc_now(),
                                enrichment_http_status=exc.status_code,
                                enrichment_reason=None,
                                enrichment_detail=str(exc),
                                enrichment_retry_count=retry_count,
                                enrichment_next_retry_at=transient_retry_at(retry_count),
                                enrichment_retry_eligible=None,
                                cursor=write_buffer,
                            )
                            transient += 1
                            wrote_row = True
                            if pipeline is not None:
                                pipeline.issue(
                                    f"Enrichment tweet {tweet_id}: {exc}",
                                    dedupe_key="archive-enrich:api-failure",
                                )
                    except httpx.TransportError as exc:
                        retry_count = int(row.get("enrichment_retry_count") or 0) + 1
                        store.update_tweet_object_enrichment(
                            tweet_id,
                            enrichment_state="transient_failure",
                            enrichment_checked_at=utc_now(),
                            enrichment_http_status=None,
                            enrichment_reason=None,
                            enrichment_detail=f"{exc.__class__.__name__}: {exc}",
                            enrichment_retry_count=retry_count,
                            enrichment_next_retry_at=transient_retry_at(retry_count),
                            enrichment_retry_eligible=None,
                            cursor=write_buffer,
                        )
                        transient += 1
                        wrote_row = True
                        if pipeline is not None:
                            pipeline.issue(
                                f"Enrichment tweet {tweet_id}: {exc.__class__.__name__}: {exc}",
                                dedupe_key="archive-enrich:transport-failure",
                            )
                    except Exception:
                        write_buffer.restore(write_checkpoint)
                        raise
                    if wrote_row:
                        buffered_writes += 1
                        if buffered_writes >= _DETAIL_ENRICH_WRITE_BATCH:
                            flush_detail_writes()
                            if pipeline is not None:
                                pipeline.status(
                                    step_key,
                                    f"Committed {done + 1}/{len(rows)} enrichment attempts",
                                    important=True,
                                )
                    if absence_error is not None:
                        flush_detail_writes()
                        raise absence_error
                    done += 1
                    report_progress()
        finally:
            try:
                if client is not None:
                    await client.aclose()
                if fx_client is not None:
                    await fx_client.aclose()
            finally:
                flush_detail_writes()
        result = _archive_enrich_counts(store)
        result.detail_lookups = succeeded
        result.detail_terminal_unavailable = terminal
        result.detail_transient_failures = transient
        if pipeline is not None:
            pipeline.complete_step(
                step_key,
                f"{succeeded} enriched ({fx_served} via FxTwitter) · "
                f"{terminal} unavailable · {transient} transient · "
                + (f"{deferred} need account · " if client is None else "")
                + f"{result.pending_untouched} pending untouched · "
                f"{result.transient_due} due · {result.transient_delayed} delayed",
            )
    return result


async def enrich_imported_archive(
    *,
    limit: int | None = None,
    reconcile_live: bool = False,
    config: AppConfig | None = None,
    paths: XDGPaths | None = None,
    auth_bundle: ResolvedAuthBundle | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    console: Console | None = None,
) -> ArchiveEnrichResult:
    config, paths = resolve_job_context(config=config, paths=paths)
    enrichment_started_at = utc_now()
    console = console or Console(stderr=True)
    status = _status_printer(console, "archive enrich")
    runner_console = runner_console_for(console)
    emit_status(status, "loading completed archive imports...")
    try:
        async with locked_archive_job(config=config, paths=paths) as job:
            manifest_rows = list_import_manifest_rows(job.store)
            if not manifest_rows:
                raise ConfigError(
                    "No completed Twitter/X archive import found. Run "
                    "'tweetnook import x-archive' first."
                )
            counts = aggregate_import_counts(manifest_rows)
    except ConfigError as exc:
        if str(exc) == "No local archive found.":
            raise ConfigError(
                "No completed Twitter/X archive import found. Run "
                "'tweetnook import x-archive' first."
            ) from exc
        raise
    result = await run_import_followup(
        counts=counts,
        manifest_digest=None,
        detail_limit=limit,
        reconcile_live=reconcile_live,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=transport,
        console=runner_console,
        status=status,
        enrichment_started_at=enrichment_started_at,
    )
    async with locked_archive_job(config=config, paths=paths) as job:
        count_updates = {
            "detail_lookups": result.detail_lookups,
            "detail_terminal_unavailable": result.detail_terminal_unavailable,
            "detail_transient_failures": result.detail_transient_failures,
        }
        for manifest_row in list_import_manifest_rows(job.store):
            finalize_import_enrichment_status(
                job.store,
                manifest_row,
                followup_status="enrichment_complete",
                count_updates=count_updates,
            )
    pipeline = current_pipeline()
    if pipeline is not None:
        pipeline.final_note(
            f"Archive enrichment queue: {result.pending_untouched:,} pending untouched · "
            f"{result.transient_due:,} transient due · "
            f"{result.transient_delayed:,} transient delayed."
        )
    return result
