"""Run the Web server and update the managed installation."""

from __future__ import annotations

import os
from typing import Annotated

import typer
from rich.console import Console

from tweetnook.cli_service import update_managed_installation
from tweetnook.config import initialize_config as load_config
from tweetnook.web.address import display_web_url


def update_command() -> None:
    """Update TweetNook and restart its managed system service; requests sudo if needed."""
    update_managed_installation()


def serve_daemon_internal(
    dev_tools: Annotated[
        bool,
        typer.Option(
            "--dev-tools",
            help=(
                "Developer tools: let Setup connect this install to a replay source (another "
                "TweetNook server) so it replays that archive instead of contacting Twitter/X. "
                "Same as TWEETNOOK_DEV_TOOLS=1."
            ),
        ),
    ] = False,
) -> None:
    """Internal command used by 'web start' to run the server process."""
    if dev_tools:
        from tweetnook.core.dev_replay import DEV_TOOLS_ENV

        os.environ[DEV_TOOLS_ENV] = "1"
    try:
        from tweetnook.web.server import run_server
    except ImportError as exc:
        raise typer.Exit(1) from exc

    config, paths = load_config()

    web = config.web
    Console().print(f"Open TweetNook at {display_web_url(web.host, web.port)}")
    run_server(config, paths, web.host, web.port, web.password_hash)
