"""Explicit Linux systemd integration; pip installation never modifies the host."""

from __future__ import annotations

import os
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Annotated

import typer

from tweetnook.job_supervisor import SERVICE_STOP_TIMEOUT

service_app = typer.Typer(
    no_args_is_help=True, help="Install and manage the Linux systemd service."
)
UNIT_PATH = Path("/etc/systemd/system/tweetnook.service")
UNIT_NAME = "tweetnook.service"
OWNERSHIP_MARKER = "# Managed by tweetnook service install\n"


def _quote(value: str, *, expand_variables: bool = False) -> str:
    if any(ord(char) < 32 for char in value):
        raise ValueError("Service values must not contain control characters.")
    # Environment= does not expand dollars; ExecStart does. Both expand specifiers.
    if expand_variables:
        value = value.replace("$", "$$")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def service_user(name: str | None):
    name = name or os.environ.get("SUDO_USER")
    if not name and os.geteuid() != 0:
        name = pwd.getpwuid(os.getuid()).pw_name
    if not name:
        raise ValueError("Specify --user when installing directly as root.")
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_.-]*[$]?", name):
        raise ValueError("Invalid service user name.")
    try:
        return pwd.getpwnam(name)
    except KeyError as exc:
        raise ValueError(f"Unknown service user: {name}") from exc


def render_unit(
    user,
    *,
    python: str,
    data_home: Path | None = None,
    config_home: Path | None = None,
    cache_home: Path | None = None,
) -> str:
    home = Path(user.pw_dir)
    environment = {
        "HOME": home,
        "XDG_DATA_HOME": data_home or home / ".local/share",
        "XDG_CONFIG_HOME": config_home or home / ".config",
        "XDG_CACHE_HOME": cache_home or home / ".cache",
    }
    for path in environment.values():
        if not path.is_absolute():
            raise ValueError("Service home paths must be absolute.")
    return OWNERSHIP_MARKER + "\n".join(
        [
            "[Unit]",
            "Description=TweetNook",
            "After=network-online.target",
            "Wants=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            f"User={user.pw_name}",
            *[f"Environment={_quote(f'{key}={value}')}" for key, value in environment.items()],
            f"ExecStart={_quote(python, expand_variables=True)} -m tweetnook serve",
            "Restart=on-failure",
            "RestartSec=5",
            "KillMode=control-group",
            # systemd sends the same cooperative signal to the workers in its cgroup.
            "KillSignal=SIGINT",
            f"TimeoutStopSec={SERVICE_STOP_TIMEOUT}",
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ]
    )


def _systemd(*, admin=False):
    if (
        sys.platform != "linux"
        or not shutil.which("systemctl")
        or not Path("/run/systemd/system").is_dir()
    ):
        raise ValueError(
            "Linux with a running systemd installation is required. "
            "Use tweetnook serve on other systems."
        )
    if admin and os.geteuid() != 0:
        raise ValueError("Run this command with sudo using this environment's Python executable.")


def _systemctl(*args):
    subprocess.run(["systemctl", *args], check=True)


def _owned_unit():
    if UNIT_PATH.is_symlink() or (
        UNIT_PATH.exists() and not UNIT_PATH.read_text().startswith(OWNERSHIP_MARKER)
    ):
        raise ValueError("Refusing to replace or remove a service unit not managed by TweetNook.")


@service_app.command("install", help="Write the service unit and enable/start it; requires sudo.")
def install(
    user: Annotated[
        str | None, typer.Option(help="Unix service user; defaults to SUDO_USER.")
    ] = None,
    data_home: Annotated[
        Path | None, typer.Option(help="Absolute XDG data home (contains tweetnook/).")
    ] = None,
    config_home: Annotated[
        Path | None, typer.Option(help="Absolute XDG config home (contains tweetnook/).")
    ] = None,
    cache_home: Annotated[
        Path | None, typer.Option(help="Absolute XDG cache home (contains tweetnook/).")
    ] = None,
) -> None:
    try:
        _systemd(admin=True)
        account = service_user(user)
        unit = render_unit(
            account,
            python=sys.executable,
            data_home=data_home,
            config_home=config_home,
            cache_home=cache_home,
        )
        _owned_unit()
        fd, name = tempfile.mkstemp(prefix=".tweetnook-", dir=UNIT_PATH.parent)
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(unit)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(name, 0o644)
            Path(name).replace(UNIT_PATH)
        finally:
            Path(name).unlink(missing_ok=True)
        _systemctl("daemon-reload")
        _systemctl("enable", "--now", UNIT_NAME)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo("TweetNook service enabled. Open this host's Web address to complete Setup.")


@service_app.command(
    "uninstall", help="Stop and remove only the managed unit; preserve data and configuration."
)
def uninstall():
    try:
        _systemd(admin=True)
        _owned_unit()
        if UNIT_PATH.exists():
            _systemctl("disable", "--now", UNIT_NAME)
            UNIT_PATH.unlink()
            _systemctl("daemon-reload")
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


def _register_action(action, help_text):
    def command():
        try:
            _systemd(admin=action != "status")
            _systemctl(action, UNIT_NAME)
        except (ValueError, OSError, subprocess.CalledProcessError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc

    service_app.command(action, help=help_text)(command)


for _action, _help in {
    "start": "Start the installed service.",
    "stop": "Stop the service and its managed jobs gracefully.",
    "restart": "Restart the installed service, stopping active jobs first.",
    "status": "Show systemd service status.",
}.items():
    _register_action(_action, _help)
