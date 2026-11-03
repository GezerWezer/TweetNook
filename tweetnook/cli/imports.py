"""`tweetnook import`: official X archive import and enrichment."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from tweetnook.auth import resolve_auth_bundle
from tweetnook.cli.common import configure_logging, web_pipeline
from tweetnook.config import initialize_config as load_config
from tweetnook.core.exceptions import ConfigError, TweetNookError
from tweetnook.enrichment.media import download_media
from tweetnook.importer.archive import import_x_archive
from tweetnook.importer.archive_enrich import (
    ArchiveEnrichmentAborted,
    ArchiveEnrichmentInterrupted,
    enrich_imported_archive,
    remaining_enrichment_text,
)
from tweetnook.importer.archive_steps import plan_archive_import
from tweetnook.sync.collection import SyncMode, sync_all
from tweetnook.sync.followups import SyncFollowupPlan, plan_sync_pipeline

import_app = typer.Typer(
    no_args_is_help=True, help="Import and enrich official Twitter/X archives."
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
            plan_archive_import(
                pipeline,
                regen=regen,
                enrich=enrich,
                detail_lookups=detail_lookups,
                sample_limit=sample_limit,
                offline=offline,
                defer_followup=defer_followup,
            )
            asyncio.run(
                import_x_archive(
                    archive,
                    offline=offline,
                    defer_followup=defer_followup,
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
            if defer_followup and not offline and sample_limit is None:
                # Media URLs are public, so Setup downloads them before any account is needed.
                asyncio.run(download_media(config=config, paths=paths, console=console))
    except ArchiveEnrichmentInterrupted as exc:
        console.print("Archive import is complete.", highlight=False)
        console.print("\nArchive enrichment was interrupted.", highlight=False)
        console.print(
            remaining_enrichment_text(exc.remaining, "sparse archive tweets"),
            highlight=False,
        )
        console.print("\nContinue later with:\n  tweetnook import enrich", highlight=False)
        raise typer.Exit(130) from exc
    except ArchiveEnrichmentAborted as exc:
        console.print("Archive import is complete.", highlight=False)
        console.print("\nAutomatic archive enrichment stopped:", highlight=False)
        console.print(f"[red]{exc.cause}[/red]")
        console.print(remaining_enrichment_text(exc.remaining, "sparse archive tweets"))
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
        "then catch up imported Likes and backfill the other configured collections."
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
                await enrich_imported_archive(
                    limit=limit,
                    config=config,
                    paths=paths,
                    auth_bundle=auth_bundle,
                    console=console,
                )
                # A complete Likes export supplies older history; other collections
                # still walk past known posts to fill gaps.
                return await sync_all(
                    mode=SyncMode.CATCH_UP,
                    limit=None,
                    config=config,
                    paths=paths,
                    auth_bundle=auth_bundle,
                    console=console,
                    followups=followups,
                )

            synced = asyncio.run(enrich_then_sync())
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
        console.print(remaining_enrichment_text(exc.remaining, "tweets"), highlight=False)
        console.print("Run `tweetnook import enrich` to continue.", highlight=False)
        raise typer.Exit(130) from exc
    except ArchiveEnrichmentAborted as exc:
        console.print(f"[red]{exc}[/red]")
        console.print(remaining_enrichment_text(exc.remaining, "tweets"), highlight=False)
        console.print("Run `tweetnook import enrich` to continue.", highlight=False)
        raise typer.Exit(2) from exc
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except TweetNookError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
