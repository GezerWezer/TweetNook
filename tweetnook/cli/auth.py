"""`tweetnook auth`: credential checks and query ID refresh."""

from __future__ import annotations

import asyncio

import typer

from tweetnook.auth import resolve_auth_bundle
from tweetnook.cli.common import configure_logging
from tweetnook.config import ensure_paths, load_config
from tweetnook.core.exceptions import ConfigError, TweetNookError
from tweetnook.query_ids import QueryIdStore, refresh_query_ids
from tweetnook.sync.collection import (
    run_preflight,
)

auth_app = typer.Typer(no_args_is_help=True, help="Check auth and refresh query IDs.")


@auth_app.command("check", help="Validate local auth and probe remote timeline readiness.")
def auth_check() -> None:
    console = configure_logging()
    config, paths = load_config()
    try:
        auth_bundle = resolve_auth_bundle(config)
        result = asyncio.run(
            run_preflight(
                config=config,
                paths=paths,
                collections=["bookmarks", "likes", "tweets"],
                auth_bundle=auth_bundle,
            )
        )
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except TweetNookError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc

    console.print(
        f"local auth: auth_token={result.auth.auth_token_source}, ct0={result.auth.ct0_source}, "
        f"user_id={result.auth.user_id_source or 'missing'}"
    )
    for collection, probe in result.probes.items():
        status = "ready" if probe.ready else "not ready"
        console.print(f"{collection}: {status} ({probe.detail})")
    if result.has_local_error:
        raise typer.Exit(1)
    if result.has_remote_error:
        raise typer.Exit(2)


@auth_app.command(
    "refresh-ids", help="Force-refresh GraphQL query IDs from Twitter/X's JS bundles."
)
def auth_refresh_ids() -> None:
    console = configure_logging()
    _, paths = load_config()
    ensure_paths(paths)
    store = QueryIdStore(paths)

    async def _refresh() -> None:
        await refresh_query_ids(store)

    try:
        asyncio.run(_refresh())
    except TweetNookError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    cache = store.load()
    console.print(f"refreshed {len(cache.ids)} query IDs into {store.path}")
