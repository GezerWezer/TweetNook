"""`tweetnook import`: official X archive import and enrichment."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Any

import typer

from tweetnook.auth import resolve_auth_bundle
from tweetnook.cli.common import configure_logging, web_pipeline
from tweetnook.config import load_config
from tweetnook.core.exceptions import ConfigError, TweetNookError
from tweetnook.enrichment.media import download_media
from tweetnook.importer.archive import import_x_archive
from tweetnook.importer.archive_enrich import (
    ArchiveEnrichmentAborted,
    ArchiveEnrichmentInterrupted,
    enrich_imported_archive,
)
from tweetnook.jobs.pipeline import PipelineReporter
from tweetnook.sync.collection import sync_all
from tweetnook.sync.followups import SyncFollowupPlan, plan_sync_pipeline

import_app = typer.Typer(
    no_args_is_help=True, help="Import and enrich official Twitter/X archives."
)


def _plan_archive_import_pipeline(
    pipeline: PipelineReporter,
    *,
    regen: bool,
    enrich: bool,
    detail_lookups: int,
    sample_limit: int | None,
    offline: bool = False,
    defer_followup: bool = False,
) -> None:
    pipeline.add_step(
        "archive-inspect",
        "Inspect",
        total=1,
        unit="bytes",
        detail="archive content hash and owner validation",
        rate_unit="bytes/s",
    )
    if regen:
        pipeline.add_step(
            "archive-regen",
            "Regenerate",
            total=1,
            unit="cleanup",
            detail="archive-import-owned rows and managed media only",
            show_rate=False,
            show_eta=False,
        )
    for key, title, unit, detail in (
        ("archive-source", "Archive source", "operations", "dataset parsing and raw capture"),
        ("archive-tweets", "Tweets", "tweets", "authored archive rows"),
        ("archive-deleted", "Deleted tweets", "tweets", "deleted authored archive rows"),
        ("archive-likes", "Likes", "likes", "archive like memberships"),
        ("archive-media", "Archive media", "files", "exported media files"),
    ):
        pipeline.add_step(key, title, total=1, unit=unit, detail=detail)

    if sample_limit is not None or offline:
        return
    pipeline.add_step(
        "archive-auth",
        "Authentication",
        total=1,
        unit="session",
        detail=(
            "optional Twitter/X session; enrichment uses FxTwitter without one"
            if defer_followup
            else "configured Twitter/X session for live archive follow-up"
        ),
        show_rate=False,
        show_eta=False,
    )
    if not defer_followup:
        for collection in ("tweets", "likes"):
            pipeline.add_step(
                f"preflight:{collection}",
                f"Prepare {collection}",
                total=4,
                unit="checks",
                detail="authentication · archive owner · remote endpoint probe",
                rate_unit="checks/s",
            )
            pipeline.add_step(
                f"sync:{collection}:head",
                f"Reconcile {collection}",
                total=1,
                unit="current page",
                detail="live Twitter/X timeline reconciled into imported archive rows",
                show_rate=False,
                show_eta=False,
                show_progress=False,
            )
    if enrich or detail_lookups > 0:
        pipeline.add_step(
            "archive-enrich",
            "Enrich",
            total=1,
            unit="tweets",
            detail="stable snapshot of eligible sparse archive tweets",
            rate_unit="tweets/s",
        )
    if defer_followup:
        pipeline.add_step(
            "media",
            "Media",
            total=1,
            unit="items",
            detail="pending archived media selected for local download",
            rate_unit="items/s",
        )


def _archive_followup_summary(result: Any) -> str:
    reconciled = ", ".join(getattr(result, "reconciled_collections", [])) or "none"
    pending_untouched = getattr(result, "pending_untouched", result.pending_enrichment)
    transient_due = getattr(result, "transient_due", 0)
    transient_delayed = getattr(result, "transient_delayed", 0)
    return (
        f"live reconciliation: {reconciled}. detail enrichment: "
        f"{result.detail_lookups:,} enriched, "
        f"{result.detail_terminal_unavailable:,} terminal, "
        f"{result.detail_transient_failures:,} transient failures, "
        f"{pending_untouched:,} pending untouched, "
        f"{transient_due:,} transient due, "
        f"{transient_delayed:,} transient delayed"
    )


def _archive_import_summary(result: Any) -> str:
    counts = result.counts
    prefix = "archive import"
    if result.skipped and result.followup_performed:
        prefix = (
            "archive import: already present; keeping existing imported data and running "
            "follow-up enrichment. Stored totals"
        )
    elif result.skipped:
        prefix = "archive import skipped: already imported. Stored totals"
    return (
        f"{prefix}: {counts.get('authored_tweets', 0)} authored, "
        f"{counts.get('deleted_authored_tweets', 0)} deleted authored, "
        f"{counts.get('likes', 0)} likes, "
        f"{counts.get('media_files_copied', 0)} media files copied. "
        f"{_archive_followup_summary(result)}"
    )


@import_app.command(
    "x-archive", help="Import an official Twitter/X archive ZIP or extracted directory."
)
def import_x_archive_command(
    archive: Annotated[
        Path, typer.Argument(help="Path to a Twitter/X archive zip or extracted directory.")
    ],
    offline: Annotated[
        bool,
        typer.Option(
            "--offline",
            help=(
                "Import all local archive data and media without Twitter/X requests; "
                "skip all live reconciliation and enrichment."
            ),
        ),
    ] = False,
    defer_followup: Annotated[
        bool,
        typer.Option(
            "--defer-followup",
            help=(
                "Import, enrich, and download media now without live sync; Setup launches "
                "the first sync separately."
            ),
        ),
    ] = False,
    regen: Annotated[
        bool,
        typer.Option(
            "--regen",
            help=(
                "Clear archive-import-owned rows, manifests, and imported media files before "
                "reimporting. Live-synced rows are kept."
            ),
        ),
    ] = False,
    enrich: Annotated[
        bool,
        typer.Option(
            "--enrich/--no-enrich",
            help=(
                "Automatically complete sparse archive TweetDetail enrichment after import. "
                "Bulk live reconciliation still runs with --no-enrich."
            ),
        ),
    ] = True,
    detail_lookups: Annotated[
        int,
        typer.Option(
            "--detail-lookups",
            min=0,
            help=(
                "Bound automatic TweetDetail enrichment to this many sparse tweets for this "
                "import invocation."
            ),
        ),
    ] = 0,
    sample_limit: Annotated[
        int | None,
        typer.Option(
            "--sample-limit",
            min=1,
            help=(
                "Sample mode: import at most N authored tweets, deleted tweets, likes, and "
                "media files after full dataset load. This stores a sampled manifest instead "
                "of a completed import."
            ),
        ),
    ] = None,
    debug: Annotated[
        bool,
        typer.Option(
            "--debug",
            help=(
                "Print detailed archive-import timing diagnostics. Interactive TTY runs already "
                "show the unified pipeline by default."
            ),
        ),
    ] = False,
) -> None:
    console = configure_logging()
    try:
        config, paths = load_config()
        with web_pipeline(console, "tweetnook import x-archive", paths) as pipeline:
            _plan_archive_import_pipeline(
                pipeline,
                regen=regen,
                enrich=enrich,
                detail_lookups=detail_lookups,
                sample_limit=sample_limit,
                offline=offline,
                defer_followup=defer_followup,
            )
            result = asyncio.run(
                import_x_archive(
                    archive,
                    offline=offline,
                    **({"defer_followup": True} if defer_followup else {}),
                    regen=regen,
                    enrich=enrich,
                    detail_lookups=detail_lookups,
                    sample_limit=sample_limit,
                    debug=debug,
                    config=config,
                    paths=paths,
                    auth_bundle=None,
                    console=console,
                )
            )
            if pipeline.step_state("archive-inspect") == "pending" and not pipeline.has_final_note:
                pipeline.final_note(_archive_import_summary(result))
                for warning in result.warnings:
                    pipeline.issue(
                        warning,
                        dedupe_key=f"archive-import:{warning.split(':', 1)[0]}",
                    )
            if defer_followup and not offline and sample_limit is None:
                # Media URLs are public, so Setup downloads them before any account is needed.
                asyncio.run(download_media(config=config, paths=paths, console=console))
    except ArchiveEnrichmentInterrupted as exc:
        console.print("Archive import is complete.", highlight=False)
        console.print("\nArchive enrichment was interrupted.", highlight=False)
        console.print(
            f"{exc.remaining:,} sparse archive tweets remain incomplete.",
            highlight=False,
        )
        console.print("\nContinue later with:\n  tweetnook import enrich", highlight=False)
        raise typer.Exit(130) from exc
    except ArchiveEnrichmentAborted as exc:
        console.print("Archive import is complete.", highlight=False)
        console.print("\nAutomatic archive enrichment stopped:", highlight=False)
        console.print(f"[red]{exc.cause}[/red]")
        console.print(f"{exc.remaining:,} sparse archive tweets remain incomplete.")
        console.print("\nContinue later with:\n  tweetnook import enrich", highlight=False)
        raise typer.Exit(2) from exc
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except TweetNookError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc


@import_app.command(
    "enrich",
    help=(
        "Process every archive TweetDetail row eligible when the command starts, "
        "then sync the configured collections past saved tweets to fill timeline gaps."
    ),
)
def import_archive_enrich(
    limit: Annotated[
        int | None,
        typer.Option(
            "--limit",
            min=1,
            help=(
                "Maximum sparse tweets to process in this continuation run. "
                "Omit to process every currently eligible row."
            ),
        ),
    ] = None,
) -> None:
    console = configure_logging()
    try:
        config, paths = load_config()
        with web_pipeline(console, "tweetnook import enrich", paths) as pipeline:
            pipeline.add_step(
                "archive-auth",
                "Authentication",
                total=1,
                unit="session",
                detail="configured Twitter/X session for archive enrichment",
                show_rate=False,
                show_eta=False,
            )
            pipeline.add_step(
                "archive-enrich",
                "Enrich",
                total=1,
                unit="tweets",
                detail="stable command-start snapshot of eligible sparse archive tweets",
                rate_unit="tweets/s",
            )
            followups = SyncFollowupPlan()
            plan_sync_pipeline(
                pipeline,
                config=config,
                collections=config.sync.collections,
                followups=followups,
                head_only=False,
            )
            pipeline.start_step("archive-auth", activity="Resolving Twitter/X authentication")
            auth_bundle = resolve_auth_bundle(config)
            pipeline.complete_step("archive-auth", "Twitter/X authentication resolved")

            async def enrich_then_sync():
                result = await enrich_imported_archive(
                    limit=limit,
                    config=config,
                    paths=paths,
                    auth_bundle=auth_bundle,
                    console=console,
                )
                # Walk through saved tweets to repair gaps left by archive import
                # or an earlier short sync, without resetting archived data.
                synced = await sync_all(
                    full=False,
                    backfill=True,
                    limit=None,
                    config=config,
                    paths=paths,
                    auth_bundle=auth_bundle,
                    console=console,
                    followups=followups,
                )
                return result, synced

            result, synced = asyncio.run(enrich_then_sync())
            if pipeline.step_state("archive-enrich") == "pending" and not pipeline.has_final_note:
                pipeline.final_note(
                    "archive enrich: existing imported archive data. "
                    + _archive_followup_summary(result)
                )
                for warning in result.warnings:
                    pipeline.issue(
                        warning,
                        dedupe_key=f"archive-enrich:{warning.split(':', 1)[0]}",
                    )
            if synced.exit_code:
                pipeline.finish(
                    "Archive enrichment saved; automatic sync did not finish. "
                    + "; ".join(f"{name}: {error}" for name, error in synced.errors.items()),
                    success=False,
                )
        if synced.exit_code:
            raise typer.Exit(synced.exit_code)
    except ArchiveEnrichmentInterrupted as exc:
        console.print("Archive enrichment was interrupted.", highlight=False)
        console.print(f"{exc.remaining:,} tweets remain incomplete.", highlight=False)
        console.print("Run `tweetnook import enrich` to continue.", highlight=False)
        raise typer.Exit(130) from exc
    except ArchiveEnrichmentAborted as exc:
        console.print(f"[red]{exc}[/red]")
        console.print(f"{exc.remaining:,} tweets remain incomplete.", highlight=False)
        console.print("Run `tweetnook import enrich` to continue.", highlight=False)
        raise typer.Exit(2) from exc
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except TweetNookError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
