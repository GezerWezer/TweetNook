"""Typer CLI entrypoint."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Annotated

import typer

from tweetnook import __version__
from tweetnook.cli.auth import auth_app
from tweetnook.cli.enrichment import (
    CHECK_HELP,
    article_app,
    check_command,
    media_app,
    tag_archive,
    thread_app,
    unfurl_archive,
)
from tweetnook.cli.imports import import_app
from tweetnook.cli.maintenance import (
    db_app,
    migrate,
    optimize_archive,
    prune_archive,
    rehydrate_archive,
)
from tweetnook.cli.server import serve_daemon_internal, update_command
from tweetnook.cli.sync import SYNC_GROUP_HELP, sync_app
from tweetnook.cli.view import export_app, search_archive, stats_archive, view_app
from tweetnook.cli.web import web_app
from tweetnook.cli_service import service_app

app = typer.Typer(no_args_is_help=True)
app.add_typer(article_app, name="articles", help="Refresh archived article bodies.")
app.add_typer(auth_app, name="auth", help="Check auth and refresh query IDs.")
app.add_typer(db_app, name="db", help="Inspect the local SQLite archive database.")
app.add_typer(export_app, name="export", help="Export the local archive.")
app.add_typer(import_app, name="import", help="Import and enrich official Twitter/X archives.")
app.add_typer(media_app, name="media", help="Download archived tweet media.")
app.add_typer(
    sync_app,
    name="sync",
    help=SYNC_GROUP_HELP,
)
app.add_typer(thread_app, name="threads", help="Expand archived tweet threads.")
app.add_typer(view_app, name="view", help="Render archived tweets in the terminal.")
app.add_typer(service_app, name="service")
app.add_typer(web_app, name="web", help="Manage the background web UI server.")


def _find_git_repo_root() -> Path | None:
    current = Path(__file__).resolve().parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _git_command_output(repo_root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    output = result.stdout.strip()
    return output or None


def _version_text() -> str:
    version = f"tweetnook {__version__}"
    repo_root = _find_git_repo_root()
    if repo_root is None:
        return version
    revision = _git_command_output(repo_root, "rev-parse", "--short", "HEAD")
    if revision is None:
        return version
    status = _git_command_output(repo_root, "status", "--short", "--untracked-files=no")
    suffix = f" ({revision}"
    if status:
        suffix += ", dirty"
    suffix += ")"
    return version + suffix


def _version_callback(
    ctx: typer.Context,
    _param: typer.CallbackParam,
    value: bool,
) -> None:
    if not value or ctx.resilient_parsing:
        return
    typer.echo(_version_text())
    raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Show version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """TweetNook CLI."""
    del version


# Root commands come from several modules, and help lists them in registration order,
# so they are registered here rather than with decorators in each module.
app.command("update")(update_command)
app.command("check", help=CHECK_HELP)(check_command)
app.command("unfurl", help="Fetch canonical URL metadata for saved links.")(unfurl_archive)
app.command("tag", help="Use Gemini to generate search tags for archived and quoted tweets.")(
    tag_archive
)
app.command("optimize")(optimize_archive)
app.command("prune")(prune_archive)
app.command("stats")(stats_archive)
app.command("rehydrate")(rehydrate_archive)
app.command("search")(search_archive)
app.command("serve-daemon", hidden=True)(serve_daemon_internal)
app.command(
    "serve",
    help="Run the Web server in the foreground; complete Setup in your browser.",
)(serve_daemon_internal)
app.command()(migrate)
