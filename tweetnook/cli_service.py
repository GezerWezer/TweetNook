"""Explicit Linux systemd integration; pip installation never modifies the host."""

from __future__ import annotations

import http.client
import json
import os
import pwd
import re
import shlex
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from typing import Annotated

import typer

from tweetnook import __version__
from tweetnook.config import WebConfig, resolve_paths
from tweetnook.core.exceptions import ARCHIVE_UPGRADE_NOTICE
from tweetnook.core.fileio import atomic_write_text
from tweetnook.jobs.supervisor import SERVICE_STOP_TIMEOUT

service_app = typer.Typer(
    no_args_is_help=True, help="Install and manage the Linux systemd service."
)
UNIT_PATH = Path("/etc/systemd/system/tweetnook.service")
UNIT_NAME = "tweetnook.service"
OWNERSHIP_MARKER = "# Managed by tweetnook service install\n"
SERVICE_STARTUP_SETTLE_SECONDS = 2.0
# The server doesn't listen until an archive upgrade finishes, which takes minutes on a large one.
WEB_STARTUP_TIMEOUT = 1800


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


def _sudo_if_needed(*arguments: str) -> None:
    """Hand off once to the same installed CLI, preserving interactive sudo I/O."""
    if os.geteuid() == 0:
        return
    sudo = shutil.which("sudo")
    if sudo is None:
        raise ValueError("sudo is not installed. Install sudo or run this command as root.")
    typer.echo("Administrator access is required; requesting sudo...")
    result = subprocess.run(
        [sudo, sys.executable, "-I", "-m", "tweetnook", *arguments], check=False
    )
    # Returning to the caller would perform the operation twice, once without root.
    code = result.returncode
    raise typer.Exit(code if code >= 0 else 128 - code)


def _service_environment() -> dict[str, str]:
    environment: dict[str, str] = {}
    for line in UNIT_PATH.read_text().splitlines():
        if not line.startswith("Environment="):
            continue
        try:
            assignments = shlex.split(line.removeprefix("Environment="))
        except ValueError:
            continue
        for assignment in assignments:
            key, separator, value = assignment.partition("=")
            if separator and key:
                # render_unit doubles percent signs to escape systemd specifiers.
                environment[key] = value.replace("%%", "%")
    return environment


def _is_tweetnook_serve_process(pid: int) -> bool:
    try:
        arguments = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return False
    return any(
        arguments[index : index + 3] == [b"-m", b"tweetnook", b"serve"]
        for index in range(max(0, len(arguments) - 2))
    )


def _manual_web_pid() -> int | None:
    data_home = _service_environment().get("XDG_DATA_HOME")
    if not data_home:
        return None
    pid_file = Path(data_home) / "tweetnook" / ".web.pid"
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return pid if pid > 0 and _is_tweetnook_serve_process(pid) else None


def _service_runtime_state() -> tuple[str, str, int]:
    result = subprocess.run(
        [
            "systemctl",
            "show",
            UNIT_NAME,
            "--property=ActiveState",
            "--property=SubState",
            "--property=MainPID",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    fields = {
        key: value
        for line in result.stdout.splitlines()
        for key, separator, value in [line.partition("=")]
        if separator
    }
    try:
        main_pid = int(fields.get("MainPID", "0"))
    except ValueError:
        main_pid = 0
    return fields.get("ActiveState", "unknown"), fields.get("SubState", "unknown"), main_pid


def _verify_service_running() -> None:
    time.sleep(SERVICE_STARTUP_SETTLE_SECONDS)
    active_state, sub_state, main_pid = _service_runtime_state()
    if active_state == "active" and sub_state == "running" and main_pid > 0:
        return
    pid_label = str(main_pid) if main_pid > 0 else "none"
    raise ValueError(
        f"{UNIT_NAME} did not remain running after startup "
        f"(state: {active_state}/{sub_state}, main PID: {pid_label}). "
        "Another process may own the configured Web port. Run "
        f"`systemctl status {UNIT_NAME} --no-pager` and stop any manual "
        "`tweetnook web` server before retrying."
    )


def _owned_unit():
    if UNIT_PATH.is_symlink() or (
        UNIT_PATH.exists() and not UNIT_PATH.read_text().startswith(OWNERSHIP_MARKER)
    ):
        raise ValueError("Refusing to replace or remove a service unit not managed by TweetNook.")


def _service_python() -> str:
    if not UNIT_PATH.exists():
        raise ValueError("TweetNook's managed service is not installed.")

    exec_start = [
        line.removeprefix("ExecStart=")
        for line in UNIT_PATH.read_text().splitlines()
        if line.startswith("ExecStart=")
    ]
    if len(exec_start) != 1:
        raise ValueError("The managed TweetNook service has an invalid ExecStart command.")
    try:
        command = shlex.split(exec_start[0])
    except ValueError as exc:
        raise ValueError("The managed TweetNook service has an invalid ExecStart command.") from exc
    if len(command) != 4 or command[1:] != ["-m", "tweetnook", "serve"]:
        raise ValueError("The managed TweetNook service has an invalid ExecStart command.")

    # Undo render_unit's systemd escaping before using the path outside systemd.
    python = command[0].replace("%%", "%").replace("$$", "$")
    if not Path(python).is_file():
        raise ValueError(f"The service Python executable does not exist: {python}")
    return python


def _service_version(python: str) -> str:
    result = subprocess.run(
        [python, "-c", "import tweetnook; print(tweetnook.__version__)"],
        check=True,
        capture_output=True,
        text=True,
    )
    version = result.stdout.strip()
    if not version:
        raise ValueError("The service Python environment did not report a TweetNook version.")
    return version


def _supports_finish_update(python: str) -> bool:
    """Whether the installed version has finish-update; a pinned older one doesn't."""
    command = [python, "-I", "-m", "tweetnook", "service", "finish-update", "--help"]
    try:
        return subprocess.run(command, check=False, capture_output=True).returncode == 0
    except OSError:
        return False


def _wait_for_service_web(host: str, port: int, *, alive) -> None:
    """Check the local application, without HTTP proxies or redirects."""
    host = host.strip()
    probe_host = {"0.0.0.0": "127.0.0.1", "::": "::1", "[::]": "::1"}.get(host, host)
    probe_host = probe_host.removeprefix("[").removesuffix("]")
    deadline = time.monotonic() + WEB_STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        connection = http.client.HTTPConnection(probe_host, port, timeout=1)
        try:
            connection.request("GET", "/")
            # The login challenge also comes from TweetNook, and it only answers after startup.
            if connection.getresponse().status in {200, 401}:
                return
        except (OSError, http.client.HTTPException):
            pass
        finally:
            connection.close()
        if not alive():
            raise ValueError("The TweetNook service stopped or restarted while starting up.")
        time.sleep(0.25)
    raise ValueError(
        f"The TweetNook web app did not respond on port {port} within "
        f"{WEB_STARTUP_TIMEOUT} seconds."
    )


def _archive_upgrade_failure(data_dir: Path) -> str | None:
    try:
        notices = json.loads((data_dir / "notices.json").read_text())
    except (OSError, ValueError):
        return None
    for notice in notices if isinstance(notices, list) else []:
        if isinstance(notice, dict) and notice.get("id") == ARCHIVE_UPGRADE_NOTICE:
            return notice.get("message")
    return None


def update_managed_installation() -> None:
    try:
        _systemd()
        _owned_unit()
        python = _service_python()
        if manual_pid := _manual_web_pid():
            raise ValueError(
                f"A manual TweetNook Web server is still running (PID: {manual_pid}). "
                "Stop it as the service user with `tweetnook web stop`, then rerun "
                "`tweetnook update`; otherwise it can keep old Python code loaded "
                "and block the managed service port."
            )
        _sudo_if_needed("update")
        subprocess.run(
            [python, "-m", "pip", "--version"],
            check=True,
            capture_output=True,
            text=True,
        )
        old_version = _service_version(python)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        typer.echo(f"TweetNook update preflight failed: {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo("Stopping TweetNook...")
    try:
        _systemctl("stop", UNIT_NAME)
    except (OSError, subprocess.CalledProcessError) as exc:
        typer.echo(f"Could not stop the TweetNook service: {exc}", err=True)
        raise typer.Exit(1) from exc

    try:
        subprocess.run(
            [python, "-m", "pip", "install", "--no-cache-dir", "--upgrade", "tweetnook"],
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        typer.echo(f"TweetNook update failed: {exc}", err=True)
        try:
            _systemctl("start", UNIT_NAME)
            _verify_service_running()
        except (ValueError, OSError, subprocess.CalledProcessError) as restart_exc:
            typer.echo(f"The TweetNook service could not be restarted: {restart_exc}", err=True)
        else:
            typer.echo("TweetNook service restarted.")
        raise typer.Exit(1) from exc

    if _supports_finish_update(python):
        # This process still runs the old code, so the new version reports its own outcome.
        finish = [python, "-I", "-m", "tweetnook", "service", "finish-update"]
        code = subprocess.run([*finish, "--from-version", old_version], check=False).returncode
        if code:
            raise typer.Exit(code)
        return

    try:
        _systemctl("start", UNIT_NAME)
        _verify_service_running()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        typer.echo(
            f"TweetNook was updated, but the managed service did not stay running: {exc}",
            err=True,
        )
        raise typer.Exit(1) from exc

    try:
        new_version = _service_version(python)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        typer.echo(
            f"TweetNook service restarted, but its installed version could not be read: {exc}",
            err=True,
        )
        raise typer.Exit(1) from exc

    if old_version == new_version:
        typer.echo(f"TweetNook is already up to date ({new_version}).")
    else:
        typer.echo(f"TweetNook updated: {old_version} → {new_version}")
        typer.echo(
            "If this version upgrades the archive, the web app returns when the upgrade finishes, "
            "which can take several minutes on a large archive."
        )
    typer.echo("TweetNook service restarted.")


@service_app.command(
    "finish-update",
    hidden=True,
    help="Start the service after `tweetnook update` installed this version, and report the "
    "outcome. The previous version's updater runs this; keep its name and options stable.",
)
def finish_update(
    from_version: Annotated[
        str, typer.Option(help="The version that was installed before this update.")
    ],
) -> None:
    try:
        _systemd(admin=True)
        paths = resolve_paths(_service_environment())
        _systemctl("start", UNIT_NAME)
        _verify_service_running()
        if from_version != __version__:
            typer.echo(
                "Waiting for TweetNook to start. Upgrading a large archive can take several "
                "minutes; don't stop it."
            )
        try:
            with paths.config_file.open("rb") as handle:
                config = WebConfig.model_validate(tomllib.load(handle).get("web", {}))
        except FileNotFoundError:
            config = WebConfig()
        initial = _service_runtime_state()
        _wait_for_service_web(
            config.host, config.port, alive=lambda: _service_runtime_state() == initial
        )
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        typer.echo(
            f"TweetNook {__version__} is installed, but the service did not start: {exc}\n"
            "Check the service log with `journalctl -u tweetnook -n 50 --no-pager`.",
            err=True,
        )
        raise typer.Exit(1) from exc
    if failure := _archive_upgrade_failure(paths.data_dir):
        typer.echo(f"TweetNook {__version__} is installed, but: {failure}", err=True)
        raise typer.Exit(1)
    if from_version == __version__:
        typer.echo(f"TweetNook is already up to date ({__version__}).")
    else:
        typer.echo(f"TweetNook updated: {from_version} → {__version__}")
    typer.echo("TweetNook service restarted.")


@service_app.command(
    "install",
    help="Write and enable the service unit, then restart to load installed code; requires sudo.",
)
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
        atomic_write_text(UNIT_PATH, unit, mode=0o644)
        _systemctl("daemon-reload")
        _systemctl("enable", UNIT_NAME)
        # enable --now only starts an inactive unit. An existing Python process
        # would retain old routes while StaticFiles serves newly installed JS.
        # restart also starts an inactive unit, without starting it twice.
        _systemctl("restart", UNIT_NAME)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(
        "TweetNook service enabled and restarted. Open this host's Web address to complete Setup."
    )


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
            _systemd(admin=action not in {"status", "restart"})
            if action == "restart":
                _owned_unit()
                if not UNIT_PATH.is_file():
                    raise ValueError("TweetNook's managed service is not installed.")
                _sudo_if_needed("service", "restart")
            _systemctl(action, UNIT_NAME)
        except (ValueError, OSError, subprocess.CalledProcessError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc

    service_app.command(action, help=help_text)(command)


for _action, _help in {
    "start": "Start the installed service.",
    "stop": "Stop the service and its managed jobs gracefully.",
    "restart": (
        "Restart the installed service, stopping active jobs first; requests sudo if needed."
    ),
    "status": "Show systemd service status.",
}.items():
    _register_action(_action, _help)
