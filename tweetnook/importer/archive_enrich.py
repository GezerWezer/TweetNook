"""Completing imported posts after ingest: live collection reconciliation and detail
enrichment through X or FxTwitter, for a new import or one already in the archive."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from rich.console import Console

from tweetnook.auth import ResolvedAuthBundle, resolve_auth_bundle
from tweetnook.client import fxtwitter
from tweetnook.client.base import AdaptiveRequestPacer, build_async_client
from tweetnook.client.detail import TweetDetailRequester, resolve_detail_query_ids
from tweetnook.config import AppConfig, XDGPaths
from tweetnook.core.exceptions import (
    ArchiveNotFoundError,
    ConfigError,
    TweetNookError,
)
from tweetnook.core.fxtwitter_payloads import fx_status_to_graphql, fx_timeline_tweet
from tweetnook.core.utils import utc_now
from tweetnook.enrichment.availability import record_unavailable
from tweetnook.enrichment.detail import (
    DetailAvailable,
    DetailExecutor,
    DetailOutcome,
    DetailUnavailable,
    detail_reporters,
    record_transient_outcome,
)
from tweetnook.importer.archive_manifests import (
    aggregate_import_counts,
    record_followup_counts,
)
from tweetnook.importer.archive_progress import (
    archive_status_printer,
    runner_console_for,
)
from tweetnook.jobs.context import (
    locked_archive_job,
    resolve_job_context,
)
from tweetnook.jobs.interactive import emit_status, progress_callback
from tweetnook.jobs.pipeline import current_pipeline
from tweetnook.jobs.resource_failure import raise_if_storage_exhausted
from tweetnook.storage import ArchiveStore
from tweetnook.storage.records import PageBuffer
from tweetnook.sync.collection import sync_collection
from tweetnook.sync.steps import head_step_key, preflight_step_key

logger = logging.getLogger(__name__)

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
    saved_counts: dict[str, Any] = field(default_factory=dict)


def remaining_enrichment_text(remaining: int | None, noun: str) -> str:
    """Say how much is left, or that the count couldn't be read when the stop was recorded."""
    if remaining is None:
        return f"Some {noun} may remain incomplete."
    return f"{remaining:,} {noun} remain incomplete."


class ArchiveEnrichmentInterrupted(KeyboardInterrupt):
    def __init__(self, remaining: int | None):
        super().__init__("Archive enrichment interrupted.")
        self.remaining = remaining


def _abort_message(cause: Exception) -> str:
    return f"Archive enrichment stopped after {cause.__class__.__name__}: {cause}"


class ArchiveEnrichmentAborted(TweetNookError):
    def __init__(self, remaining: int | None, cause: Exception):
        super().__init__(_abort_message(cause))
        self.remaining = remaining
        self.cause = cause


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
    if auth_bundle is not None and pipeline.step_state(step_key) == "complete":
        return auth_bundle
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
            pipeline.skip_step(step_key, "no Twitter/X account being connected")
            return None
        pipeline.fail_step(step_key, str(exc))
        raise
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
    if pipeline.active:
        for collection in ("tweets", "likes"):
            if collection in collections:
                continue
            for step_key in (preflight_step_key([collection]), head_step_key(collection)):
                if pipeline.has_step(step_key):
                    pipeline.skip_step(
                        step_key,
                        f"the archive containing no {collection} dataset rows",
                    )
    try:
        resolved_auth = _resolve_archive_followup_auth(config, auth_bundle)
    except ConfigError as exc:
        warnings.append(f"live reconciliation skipped: {exc}")
        for collection in collections:
            for step_key in (preflight_step_key([collection]), head_step_key(collection)):
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
                catch_up=collection == "likes",
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
        enrichment_result = await _enrich_pending_rows(
            limit=detail_limit,
            config=config,
            paths=paths,
            auth_bundle=resolved_auth,
            transport=transport,
            console=console,
            status=status,
            started_at=enrichment_started_at,
        )
    else:
        async with locked_archive_job(config=config, paths=paths) as job:
            enrichment_result = _archive_enrich_counts(job.store)
        pipeline = current_pipeline()
        if pipeline.has_step("archive-enrich"):
            pipeline.skip_step("archive-enrich", "Twitter/X authentication being unavailable")
    enrichment_result.warnings = warnings
    enrichment_result.reconciled_collections = reconciled_collections
    pipeline = current_pipeline()
    if pipeline.active:
        for warning in warnings:
            pipeline.issue(
                warning,
                dedupe_key=f"archive-followup:{warning.split(':', 1)[0]}",
            )
    return enrichment_result


def _followup_manifest_rows(store: ArchiveStore, digest: str | None) -> list[dict[str, Any]]:
    if digest is None:
        return store.completed_manifests()
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
    """Reconcile and enrich the collections an import's counts cover, and save the result.

    However it ends, the enrichment counts are saved on the import manifest it belongs to
    (manifest_digest, or every completed import when None). A stopped follow-up saves them
    before the error propagates, and that write never replaces the error.
    """

    async def record_stop() -> int | None:
        # The disk may be why the run stopped, so this write must not replace that error.
        remaining: int | None = None
        try:
            async with locked_archive_job(config=config, paths=paths) as job:
                remaining = job.store.count_incomplete_initial_enrichment()
                for manifest_row in _followup_manifest_rows(job.store, manifest_digest):
                    record_followup_counts(job.store, manifest_row)
        except Exception:
            logger.exception("Could not record the archive follow-up's stop")
        return remaining

    try:
        result = await _run_archive_followup(
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
        remaining = await record_stop()
        raise ArchiveEnrichmentInterrupted(remaining) from exc
    except Exception as exc:
        raise_if_storage_exhausted(exc)
        remaining = await record_stop()
        raise ArchiveEnrichmentAborted(remaining, exc) from exc
    async with locked_archive_job(config=config, paths=paths, console=console) as job:
        count_updates = {
            "detail_lookups": result.detail_lookups,
            "detail_terminal_unavailable": result.detail_terminal_unavailable,
            "detail_transient_failures": result.detail_transient_failures,
        }
        for manifest_row in _followup_manifest_rows(job.store, manifest_digest):
            result.saved_counts = record_followup_counts(
                job.store, manifest_row, count_updates=count_updates
            )
    return result


def _archive_enrich_counts(store: ArchiveStore, *, now: str | None = None) -> ArchiveEnrichResult:
    return ArchiveEnrichResult(
        pending_enrichment=store.count_incomplete_initial_enrichment(),
        pending_untouched=store.count_pending_initial_enrichment(),
        transient_due=store.count_due_transient_enrichment(now),
        transient_delayed=store.count_delayed_transient_enrichment(now),
    )


@dataclass(slots=True)
class _EnrichRun:
    """One enrichment pass: its rows, write buffer, counters and provider fan-out."""

    store: ArchiveStore
    rows: list[dict[str, Any]]
    config: AppConfig
    console: Console
    signed_in: bool
    fx_client: httpx.AsyncClient | None
    step_key: str = "archive-enrich"
    buffer: PageBuffer = field(default_factory=PageBuffer)
    buffered_writes: int = 0
    # Rows finished so far, in whatever order FxTwitter answers, so progress never jumps.
    done: int = 0
    succeeded: int = 0
    terminal: int = 0
    transient: int = 0
    deferred: int = 0
    fx_served: int = 0
    progress: Callable[[int, int], None] | None = None

    def flush(self) -> None:
        if self.buffered_writes <= 0:
            return
        self.store.flush(self.buffer)
        self.buffered_writes = 0

    def report_progress(self, activity: str | None = None) -> None:
        if self.progress:
            self.progress(self.done, len(self.rows))
        pipeline = current_pipeline()
        if not pipeline.active:
            return
        counters = (
            f"{self.done} checked · {self.succeeded} enriched · "
            f"{self.terminal} unavailable · {self.transient} transient"
        )
        if not self.signed_in:
            counters += f" · {self.deferred} need account"
        pipeline.update_step(
            self.step_key,
            completed=self.done,
            counters=counters,
            activity=activity or None,
        )

    def _save_fx_lookup(self, lookup: fxtwitter.FxLookup) -> None:
        if lookup.kind != "available":
            return
        assert lookup.status is not None
        self.store.persist_fx_status(
            tweet=fx_timeline_tweet(fx_status_to_graphql(lookup.status)),
            payload=lookup.payload,
            http_status=lookup.http_status or 200,
            cursor=self.buffer,
        )
        self.succeeded += 1
        self.fx_served += 1
        self.done += 1
        self.buffered_writes += 1
        if self.buffered_writes >= _DETAIL_ENRICH_WRITE_BATCH:
            self.flush()
        self.report_progress()

    async def _prefetch_fx(self, tweet_ids: list[str]) -> dict[str, fxtwitter.FxLookup]:
        assert self.fx_client is not None
        self.report_progress("Looking up posts through FxTwitter")
        results, fx_down = await fxtwitter.fetch_fx_statuses(
            self.fx_client,
            tweet_ids,
            concurrency=self.config.fxtwitter.concurrency,
            on_lookup=self._save_fx_lookup,
        )
        if fx_down:
            # FxTwitter is down; TweetDetail handles the rest of this run.
            await self.fx_client.aclose()
            self.fx_client = None
            self.console.print(
                "[yellow]FxTwitter isn't answering; using TweetDetail "
                "for the rest of this run.[/yellow]"
            )
        return results

    async def select_targets(self) -> AsyncIterator[dict[str, Any]]:
        """Yield the rows FxTwitter didn't answer that TweetDetail can still try."""

        fx_results: dict[str, fxtwitter.FxLookup] = {}

        def fx_answered(row: dict[str, Any]) -> bool:
            lookup = fx_results.get(row["tweet_id"])
            return lookup is not None and lookup.kind == "available"

        for index, row in enumerate(self.rows):
            if self.fx_client is not None and index % _FX_ENRICH_PREFETCH == 0:
                fx_results = await self._prefetch_fx(
                    [str(item["tweet_id"]) for item in self.rows[index:][:_FX_ENRICH_PREFETCH]]
                )
            if fx_answered(row):
                continue  # Saved and counted as FxTwitter answered.
            if not self.signed_in:
                # Left as-is so the next authenticated run picks it up.
                if self.fx_client is None:
                    remaining = [item for item in self.rows[index:] if not fx_answered(item)]
                    self.deferred += len(remaining)
                    self.done += len(remaining)
                    self.report_progress()
                    break
                self.deferred += 1
                self.done += 1
                self.report_progress()
                continue
            self.report_progress(f"Fetching live details for tweet {row['tweet_id']}")
            yield row

    def record(self, row: dict[str, Any], outcome: DetailOutcome) -> None:
        if isinstance(outcome, DetailAvailable):
            self.store.persist_tweet_detail(
                tweet=outcome.tweet,
                raw_json=outcome.payload,
                http_status=outcome.http_status,
                cursor=self.buffer,
            )
            self.succeeded += 1
        elif isinstance(outcome, DetailUnavailable):
            record_unavailable(
                self.store,
                tweet_id=outcome.tweet_id,
                operation="TweetDetail",
                payload=outcome.payload,
                http_status=outcome.http_status,
                reason=outcome.reason,
                detail=outcome.detail,
                cursor=self.buffer,
            )
            self.terminal += 1
        else:
            record_transient_outcome(
                self.store,
                row,
                outcome,
                label="Enrichment",
                issue_key="archive-enrich",
                cursor=self.buffer,
            )
            self.transient += 1

    def after_record(self, _row: dict[str, Any], _outcome: DetailOutcome) -> None:
        self.buffered_writes += 1
        if self.buffered_writes >= _DETAIL_ENRICH_WRITE_BATCH:
            self.flush()
            current_pipeline().status(
                self.step_key,
                f"Committed {self.done + 1}/{len(self.rows)} enrichment attempts",
                important=True,
            )
        self.done += 1
        self.report_progress()


async def _run_enrichment(run: _EnrichRun, executor: DetailExecutor | None) -> None:
    with progress_callback(
        run.console,
        label="archive enrich detail",
        total=len(run.rows),
        unit="tweets",
        leave=False,
    ) as progress:
        run.progress = progress
        if executor is None:
            # Without an account the selection does all the work and yields nothing.
            async for _row in run.select_targets():
                raise AssertionError("an unauthenticated run has no TweetDetail targets")
            return
        await executor.run(
            run.select_targets(),
            tweet_id_of=lambda row: str(row["tweet_id"]),
            record=run.record,
            after=run.after_record,
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
    started_at: str | None = None,
    fx_transport: httpx.AsyncBaseTransport | None = None,
) -> ArchiveEnrichResult:
    pipeline = current_pipeline()
    step_key = "archive-enrich"
    if limit is not None and limit <= 0:
        async with locked_archive_job(config=config, paths=paths, console=console) as job:
            result = _archive_enrich_counts(job.store)
        if pipeline.has_step(step_key):
            pipeline.skip_step(step_key, "detail enrichment being disabled for this run")
        return result

    async with locked_archive_job(config=config, paths=paths, console=console) as job:
        store = job.store
        selection_started_at = started_at or utc_now()
        rows = store.list_tweet_objects_for_enrichment(limit=limit, now=selection_started_at)
        if not rows:
            result = _archive_enrich_counts(store, now=selection_started_at)
            emit_status(status, "no archive enrichment rows are currently due")
            if result.transient_delayed:
                emit_status(
                    status,
                    f"{result.transient_delayed:,} transient failures remain scheduled "
                    "for later retry",
                )
            if pipeline.has_step(step_key):
                pipeline.skip_step(
                    step_key,
                    "no sparse archive tweets being eligible for enrichment",
                )
            return result
        queue_counts = _archive_enrich_counts(store, now=selection_started_at)
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
        emit_status(
            status, f"detail enrichment over {len(rows)} eligible sparse tweets{limit_suffix}"
        )
        # Without an account only FxTwitter runs; rows it can't answer wait for TweetDetail.
        client = None
        executor = None
        run = _EnrichRun(
            store=store,
            rows=rows,
            config=config,
            console=console,
            signed_in=auth_bundle is not None,
            # FxTwitter answers most lookups without the login; TweetDetail handles the rest.
            fx_client=(
                fxtwitter.build_fx_client(config, transport=fx_transport)
                if fxtwitter.fx_enabled(config)
                else None
            ),
        )
        try:
            if auth_bundle is not None:
                query_store, query_ids = await resolve_detail_query_ids(paths, transport=transport)
                client = build_async_client(
                    auth_bundle, timeout=config.sync.timeout, transport=transport
                )
                retry_status, pacing_status = detail_reporters(
                    step_key, label="Enrichment", status=status
                )
                executor = DetailExecutor(
                    requester=TweetDetailRequester(client, query_store, query_ids, config.sync),
                    pacer=AdaptiveRequestPacer(config.sync.detail_delay),
                    gone_is_unavailable=True,
                    buffer=run.buffer,
                    retry_status=retry_status,
                    pacing_status=pacing_status,
                )
            await _run_enrichment(run, executor)
        finally:
            try:
                if client is not None:
                    await client.aclose()
                if run.fx_client is not None:
                    await run.fx_client.aclose()
            finally:
                run.flush()
        result = _archive_enrich_counts(store)
        result.detail_lookups = run.succeeded
        result.detail_terminal_unavailable = run.terminal
        result.detail_transient_failures = run.transient
        pipeline.complete_step(
            step_key,
            f"{run.succeeded} enriched ({run.fx_served} via FxTwitter) · "
            f"{run.terminal} unavailable · {run.transient} transient · "
            + (f"{run.deferred} need account · " if not run.signed_in else "")
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
    status = archive_status_printer(console, "archive enrich")
    runner_console = runner_console_for(console)
    emit_status(status, "loading completed archive imports...")
    try:
        async with locked_archive_job(config=config, paths=paths) as job:
            manifest_rows = job.store.completed_manifests()
            if not manifest_rows:
                raise ConfigError(
                    "No completed Twitter/X archive import found. Run "
                    "'tweetnook import x-archive' first."
                )
            counts = aggregate_import_counts(manifest_rows)
    except ArchiveNotFoundError as exc:
        raise ConfigError(
            "No completed Twitter/X archive import found. Run 'tweetnook import x-archive' first."
        ) from exc
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
    pipeline = current_pipeline()
    pipeline.final_note(
        f"Archive enrichment queue: {result.pending_untouched:,} pending untouched · "
        f"{result.transient_due:,} transient due · "
        f"{result.transient_delayed:,} transient delayed."
    )
    return result
