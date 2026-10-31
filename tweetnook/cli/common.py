"""Console, archive and pipeline helpers shared by the command modules."""

from __future__ import annotations

import os
from typing import Annotated

import typer
from rich.console import Console

from tweetnook.config import AppConfig, SyncConfig
from tweetnook.config import initialize_config as load_config
from tweetnook.core.collection_names import normalize_collection_name
from tweetnook.jobs.context import open_job_store
from tweetnook.jobs.pipeline import PipelineReporter


def web_pipeline(console: Console, title: str, paths) -> PipelineReporter:
    """Publish CLI lifecycle state for the always-installed Web activity drawer."""

    return PipelineReporter(
        console,
        title,
        state_path=paths.activity_status_file,
    )


SYNC_MAX_LINKED_DEPTH_OPTION = Annotated[
    int | None,
    typer.Option(
        "--max-linked-depth",
        min=0,
        help=(
            "Safety cap on quote/linked-status hops followed from saved tweets and their "
            "author's own thread (0 disables linked discovery; default 50)."
        ),
    ),
]
SYNC_REPLY_LINKED_DEPTH_OPTION = Annotated[
    int | None,
    typer.Option(
        "--reply-linked-depth",
        min=0,
        max=1,
        help=(
            "Follow quotes/linked statuses from other people's replies in saved "
            "conversations: 1 for one hop, 0 to skip (default 0)."
        ),
    ),
]


def apply_sync_depth_options(
    config: AppConfig, max_linked_depth: int | None, reply_linked_depth: int | None
) -> None:
    updates = {
        name: value
        for name, value in (
            ("max_linked_depth", max_linked_depth),
            ("reply_linked_depth", reply_linked_depth),
        )
        if value is not None
    }
    if updates:
        config.sync = SyncConfig.model_validate(config.sync.model_dump() | updates)


def configure_logging() -> Console:
    service_mode = bool(os.environ.get("INVOCATION_ID") or os.environ.get("JOURNAL_STREAM"))
    return Console(
        stderr=True,
        force_terminal=False if service_mode else None,
        color_system=None if service_mode else "auto",
    )


def normalize_collection_or_exit(collection: str, console: Console) -> str:
    try:
        return normalize_collection_name(collection)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


def open_store_for_read(console: Console):
    config, paths = load_config()
    store = open_job_store(paths, create=False, config=config, console=console)
    if store is None:
        console.print("[red]No local archive found.[/red]")
        raise typer.Exit(1)
    return store, paths
