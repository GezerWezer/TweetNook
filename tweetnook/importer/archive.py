"""Official Twitter/X archive import helpers."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter

import httpx
from rich.console import Console

from tweetnook.auth import ResolvedAuthBundle
from tweetnook.config import AppConfig, XDGPaths
from tweetnook.core.exceptions import ConfigError
from tweetnook.core.utils import utc_now
from tweetnook.importer.archive_enrich import ArchiveEnrichResult, run_import_followup
from tweetnook.importer.archive_input import ArchiveInput
from tweetnook.importer.archive_manifests import manifest_counts, manifest_warnings
from tweetnook.importer.archive_progress import archive_status_printer, runner_console_for
from tweetnook.importer.archive_stages import (
    ImportRun,
    claim_archive_owner,
    clear_previous_import,
    ingest_archive,
    inspect_archive,
)
from tweetnook.importer.archive_steps import INGEST_STEPS
from tweetnook.jobs.context import (
    locked_archive_job,
    resolve_job_context,
)
from tweetnook.jobs.interactive import emit_status
from tweetnook.jobs.pipeline import current_pipeline
from tweetnook.storage import ArchiveStore
from tweetnook.sync.steps import head_step_key, preflight_step_key

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ArchiveImportResult:
    skipped: bool = False
    followup_performed: bool = False
    counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    reconciled_collections: list[str] = field(default_factory=list)
    detail_lookups: int = 0
    detail_terminal_unavailable: int = 0
    detail_transient_failures: int = 0
    pending_enrichment: int = 0
    pending_untouched: int = 0
    transient_due: int = 0
    transient_delayed: int = 0


def _record_failed_import(
    store: ArchiveStore,
    digest: str,
    *,
    archive_generation_date: str | None,
    import_started_at: str,
    warnings: list[str],
    counts: dict[str, int],
) -> None:
    """Mark the manifest failed without letting this write replace the import's own error.

    A full disk is the likely reason an import fails, and then this write fails too.
    """
    try:
        store.set_import_manifest(
            digest,
            archive_generation_date=archive_generation_date,
            status="failed",
            import_started_at=import_started_at,
            import_completed_at=utc_now(),
            warnings=warnings,
            counts=counts,
        )
    except Exception:
        logger.exception("Could not record the failed archive import")


def _ingest_unless_duplicate(run: ImportRun, store: ArchiveStore, *, regen: bool) -> None:
    """Import the archive, unless this digest already completed: then adopt what it recorded."""
    try:
        claim_archive_owner(run, store)
        if regen:
            clear_previous_import(run, store)
        existing_manifest = store.get_import_manifest(run.digest)
        if existing_manifest and existing_manifest.get("status") == "completed":
            run.duplicate = True
            run.counts = manifest_counts(existing_manifest)
            run.warnings = manifest_warnings(existing_manifest)
            run.pending_after_import = store.count_tweet_objects_for_enrichment()
            for step in INGEST_STEPS:
                if run.pipeline.has_step(step.key):
                    run.pipeline.skip_step(step.key, "this archive already being imported")
        else:
            ingest_archive(run, store, final_status="sampled" if run.sampled else "completed")
    except BaseException:
        if run.imported:
            _record_failed_import(
                store,
                run.digest,
                archive_generation_date=run.generation_date,
                import_started_at=run.import_started_at,
                warnings=run.warnings,
                counts=run.counts,
            )
        raise


def _raise_warning(run: ImportRun, warning: str) -> None:
    run.pipeline.issue(warning, dedupe_key=f"archive-import:{warning.split(':', 1)[0]}")


def _skip_duplicate_followup_steps(run: ImportRun) -> None:
    for step_key in (
        "archive-auth",
        preflight_step_key(["tweets"]),
        head_step_key("tweets"),
        preflight_step_key(["likes"]),
        head_step_key("likes"),
    ):
        if run.pipeline.has_step(step_key):
            run.pipeline.skip_step(step_key, "the duplicate archive having no requested follow-up")


def _finish_run(
    run: ImportRun, note: str, *, followup: ArchiveEnrichResult | None = None
) -> ArchiveImportResult:
    """Close the run the same way on every exit: only the note and the follow-up differ."""
    if run.debug:
        emit_status(run.status, "debug summary:")
        for summary in run.timings:
            emit_status(run.status, f"  {summary}")
    run.pipeline.final_note(note)
    if followup is None:
        return ArchiveImportResult(
            skipped=run.duplicate,
            counts={**run.counts, "pending_enrichment": run.pending_after_import},
            warnings=run.warnings,
            pending_enrichment=run.pending_after_import,
        )
    return ArchiveImportResult(
        skipped=run.duplicate,
        followup_performed=True,
        counts=followup.saved_counts,
        warnings=[*run.warnings, *followup.warnings],
        reconciled_collections=followup.reconciled_collections,
        detail_lookups=followup.detail_lookups,
        detail_terminal_unavailable=followup.detail_terminal_unavailable,
        detail_transient_failures=followup.detail_transient_failures,
        pending_enrichment=followup.pending_enrichment,
        pending_untouched=followup.pending_untouched,
        transient_due=followup.transient_due,
        transient_delayed=followup.transient_delayed,
    )


async def import_x_archive(
    archive_path: Path,
    *,
    detail_lookups: int = 0,
    enrich: bool = True,
    offline: bool = False,
    defer_followup: bool = False,
    regen: bool = False,
    sample_limit: int | None = None,
    debug: bool = False,
    config: AppConfig | None = None,
    paths: XDGPaths | None = None,
    auth_bundle: ResolvedAuthBundle | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    console: Console | None = None,
) -> ArchiveImportResult:
    # Setup defers only the live sync; enrichment still runs, with or without an account.
    config, paths = resolve_job_context(config=config, paths=paths)
    console = console or Console(stderr=True)
    status = archive_status_printer(console, "archive import", force=debug)
    runner_console = runner_console_for(console, force=debug)
    if sample_limit is not None and sample_limit <= 0:
        raise ConfigError("--sample-limit must be greater than zero.")
    emit_status(status, f"opening {archive_path}")
    with ArchiveInput(archive_path) as source:
        run = ImportRun(
            source=source,
            paths=paths,
            console=console,
            pipeline=current_pipeline(),
            status=status,
            debug=debug,
            sample_limit=sample_limit,
        )
        inspect_archive(run, archive_path)
        run.import_started_at = utc_now()
        # A sample never runs follow-up, so a sampled rerun of a completed archive has none to do.
        followup_requested = not offline and not run.sampled and (enrich or detail_lookups > 0)

        async with locked_archive_job(
            config=config, paths=paths, console=console, create=True
        ) as job:
            _ingest_unless_duplicate(run, job.store, regen=regen)

        if run.imported:
            # Raised now, so a follow-up that stops the run can't lose what the ingest found.
            for warning in run.warnings:
                _raise_warning(run, warning)
        if run.duplicate and not followup_requested:
            _skip_duplicate_followup_steps(run)
            run.warnings = ["archive already imported; skipping duplicate import"]
            return _finish_run(run, "Archive already imported; no follow-up work requested.")
        if run.sampled:
            skipped_followup = (
                "sampled import skipped automatic live reconciliation and detail enrichment; "
                "rerun without --sample-limit for normal follow-up"
            )
            run.warnings.append(skipped_followup)
            _raise_warning(run, skipped_followup)
            return _finish_run(
                run,
                f"Sample imported: {run.counts['authored_tweets']:,} authored · "
                f"{run.counts['deleted_authored_tweets']:,} deleted · "
                f"{run.counts['likes']:,} likes · "
                f"{run.counts['media_files_copied']:,} media copied.",
            )
        if offline or (defer_followup and not followup_requested):
            return _finish_run(
                run,
                "Local archive import complete; enrichment skipped and live sync deferred "
                "to the separate Setup first-sync task."
                if defer_followup
                else "Local archive import complete; live follow-up skipped (offline).",
            )

        # Bulk archive writes are complete at this point. The follow-up sync/enrichment helpers
        # reacquire the same process lock around their own writes, so we intentionally do not hold
        # the outer lock across potentially long network I/O.
        emit_status(status, "running follow-up reconciliation and enrichment...")
        followup_started = perf_counter()
        followup = await run_import_followup(
            counts=run.counts,
            manifest_digest=run.digest,
            detail_limit=(detail_lookups if detail_lookups > 0 else None if enrich else 0),
            reconcile_live=not defer_followup,
            config=config,
            paths=paths,
            auth_bundle=auth_bundle,
            transport=transport,
            console=runner_console,
            status=status,
            # Setup leaves rows nobody can enrich yet (no account, and no FxTwitter
            # under replay) pending for the first sync instead of failing the import.
            account_optional=defer_followup,
        )
        run.record_timing(
            "archive follow-up",
            followup_started,
            processed=followup.detail_lookups,
            unit="details",
        )
        saved = followup.saved_counts
        return _finish_run(
            run,
            f"Imported totals: {saved.get('authored_tweets', 0):,} authored · "
            f"{saved.get('deleted_authored_tweets', 0):,} deleted · "
            f"{saved.get('likes', 0):,} likes · "
            f"{saved.get('media_files_copied', 0):,} media copied. "
            f"Enrichment queue: {followup.pending_untouched:,} pending untouched · "
            f"{followup.transient_due:,} due · {followup.transient_delayed:,} delayed."
            + (
                " Live sync deferred to the separate Setup first-sync task."
                if defer_followup
                else ""
            ),
            followup=followup,
        )
