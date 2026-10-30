"""Archive maintenance: optimize, prune, db check, rehydrate and migrate."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

import typer
from rich.console import Console

from tweetnook.cli.common import (
    configure_logging,
    open_store_for_read,
    web_pipeline,
)
from tweetnook.config import initialize_config as load_config
from tweetnook.core.exceptions import ConfigError, TweetNookError
from tweetnook.jobs.context import archive_job
from tweetnook.storage import ArchiveStore

db_app = typer.Typer(no_args_is_help=True, help="Inspect the local SQLite archive database.")

# These commands can stop after destructive work has already committed, so the generic promise
# that saved data is untouched and Sync resumes everything doesn't hold for them.
MAINTENANCE_STORAGE_RECOVERY = (
    "Storage is full. Free or add disk space, then run `tweetnook db check` and rerun this "
    "command. It may have finished part of its work before stopping."
)


@contextmanager
def _maintenance_store(
    console: Console, config, paths, *, hold_lock: bool = True
) -> Iterator[ArchiveStore]:
    try:
        with archive_job(
            config=config,
            paths=paths,
            console=console,
            hold_lock=hold_lock,
            storage_recovery=MAINTENANCE_STORAGE_RECOVERY,
        ) as job:
            yield job.store
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except TweetNookError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc


def optimize_archive() -> None:
    """Reclaim SQLite space and optimize query planning and the full-text index."""
    console = configure_logging()
    config, paths = load_config()
    with _maintenance_store(console, config, paths) as store:
        console.print("optimizing database and search index...")
        store.optimize()
        console.print("database optimization complete.")


def prune_archive(
    apply: Annotated[
        bool,
        typer.Option(
            "--apply",
            help=(
                "Delete the out-of-scope rows and media files, then VACUUM. "
                "Without it, only report what would be deleted."
            ),
        ),
    ] = False,
    max_linked_depth: Annotated[
        int | None,
        typer.Option(
            "--max-linked-depth",
            min=0,
            help=(
                "Quote/linked-status hops from saved tweets to keep "
                "(default: the sync max_linked_depth setting)."
            ),
        ),
    ] = None,
    reply_linked_depth: Annotated[
        int | None,
        typer.Option(
            "--reply-linked-depth",
            min=0,
            max=1,
            help=(
                "1 keeps threads linked from other people's replies, 0 prunes them "
                "(default: the sync reply_linked_depth setting)."
            ),
        ),
    ] = None,
) -> None:
    """Remove archived tweets, threads and media that thread expansion would not fetch.

    Keeps saved tweets, their conversations, quote/linked-status threads on the save
    path under the current sync settings, and tweets quoted or retweeted by kept
    tweets. Everything else is out of scope, including threads expanded by hand
    with `threads expand` that are not on the save path. Back up archive.db before
    using --apply.
    """
    from tweetnook.enrichment.prune import (
        apply_prune,
        describe_prune_plan,
        describe_prune_result,
        plan_prune,
    )

    console = configure_logging()
    config, paths = load_config()
    depth = config.sync.max_linked_depth if max_linked_depth is None else max_linked_depth
    reply_depth = (
        config.sync.reply_linked_depth if reply_linked_depth is None else reply_linked_depth
    )

    def log(message: str) -> None:
        console.print(f"prune: {message}", highlight=False)

    with _maintenance_store(console, config, paths, hold_lock=apply) as store:
        plan = plan_prune(
            store,
            paths.data_dir,
            max_linked_depth=depth,
            reply_linked_depth=reply_depth,
            log=log,
        )
        for line in describe_prune_plan(plan):
            console.print(line, highlight=False)
        if not plan.row_total and not plan.media_files and not plan.cleanup_pending:
            return
        if not apply:
            console.print(
                "Dry run: nothing was deleted. Back up archive.db, then re-run with --apply.",
                highlight=False,
            )
            return
        result = apply_prune(store, plan, paths.data_dir, paths.database_path, log=log)
        for line in describe_prune_result(result):
            console.print(line, highlight=False)
        if result.files_pending:
            raise typer.Exit(1)


@db_app.command("check", help="Run an explicit SQLite database integrity check.")
def check_database(
    full: Annotated[
        bool,
        typer.Option(
            "--full",
            help="Run SQLite integrity_check instead of the faster quick_check.",
        ),
    ] = False,
) -> None:
    console = configure_logging()
    store, paths = open_store_for_read(console)
    pragma = "integrity_check" if full else "quick_check"
    try:
        results = store.check_integrity(full=full)
    except (sqlite3.Error, OSError) as exc:
        console.print(f"[red]database {pragma} failed: {exc}[/red]")
        raise typer.Exit(2) from exc
    finally:
        store.close()

    if results == ["ok"]:
        console.print(f"database {pragma}: ok ({paths.database_path})", highlight=False)
        return

    console.print(f"[red]database {pragma} reported problems:[/red]")
    for result in results:
        console.print(result, highlight=False)
    raise typer.Exit(2)


def rehydrate_archive() -> None:
    """Rebuild normalized tweet fields and secondary rows from stored raw_json."""
    from tqdm import tqdm

    console = configure_logging()
    config, paths = load_config()
    with _maintenance_store(console, config, paths) as store:
        total = store.rehydration_work_total()
        if total == 0:
            console.print("archive has no tweet rows")
            return
        with tqdm(total=total, desc="rehydrating", unit="tweets") as pbar:
            result = store.rehydrate_from_raw_json(progress=pbar.update)
        console.print(
            f"rehydrated {result.tweets_updated} tweet rows and rebuilt "
            f"{result.secondary_records} secondary rows"
        )


def migrate() -> None:
    """Migrate data from older LanceDB storage to native SQLite storage."""
    from tweetnook.importer.legacy_migration import run_migration

    console = configure_logging()
    try:
        _, paths = load_config()
        with web_pipeline(console, "tweetnook migrate", paths):
            result = run_migration(console=console)
            if result.status not in {"complete", "partial"}:
                raise TweetNookError(f"Migration did not complete ({result.status}).")
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except TweetNookError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
