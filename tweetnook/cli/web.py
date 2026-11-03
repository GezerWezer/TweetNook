"""Web CLI commands for TweetNook."""

import os
import signal
import subprocess
import sys
import time
from contextlib import suppress
from importlib import import_module
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from rich.console import Console

from tweetnook.config import initialize_config as load_config
from tweetnook.config import update_config_values
from tweetnook.core.passwords import hash_password
from tweetnook.core.process_identity import ProcessIdentity, process_has_exited, process_identity
from tweetnook.jobs.supervisor import WEB_STOP_TIMEOUT, WORKER_KILL_TIMEOUT, terminate_owned_process
from tweetnook.web.address import display_web_url
from tweetnook.web.processes import (
    find_web_process,
    forget_web_process,
    record_web_process,
    recorded_web_process,
)

web_app = typer.Typer(no_args_is_help=True, help="Manage the background web UI server.")


def _get_pid_file(data_dir: Path) -> Path:
    return data_dir / ".web.pid"


def _read_pid(pid_file: Path) -> int | None:
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return pid if pid > 0 else None


def _is_running(pid: int) -> bool:
    if pid <= 0 or process_has_exited(pid):
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _positive_pids(output: str) -> list[int]:
    return [int(value) for value in output.split() if value.isdigit() and int(value) > 0]


def _find_pid_by_port(port: int) -> int | None:
    # A plain `lsof -i:<port>` also returns clients with an active connection.
    # Only a listening TCP socket owns the port for start/stop decisions.
    commands = (
        ["lsof", "-t", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
        ["fuser", f"{port}/tcp"],
    )
    for command in commands:
        try:
            output = subprocess.check_output(
                command,
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if pids := _positive_pids(output):
            return pids[0]
    return None


def _require_web_dependencies(console: Console) -> None:
    try:
        import_module("tweetnook.web.server")
        import_module("uvicorn")
    except ImportError as exc:
        console.print("[red]Required Web dependencies are missing. Reinstall tweetnook.[/red]")
        raise typer.Exit(1) from exc


def _running_pid(pid_file: Path, port: int) -> int | None:
    pid = _read_pid(pid_file)
    if pid is not None:
        identity = recorded_web_process(pid_file.parent, pid)
        if identity is not None and process_identity(pid) == identity:
            return pid
    identity = find_web_process(pid_file.parent, port)
    return identity.pid if identity is not None else None


def _unverified_process(pid_file: Path, port: int) -> int | None:
    for pid in (_read_pid(pid_file), _find_pid_by_port(port)):
        if pid is not None and _is_running(pid):
            return pid
    return None


def _refuse_unverified_process(console: Console, pid: int) -> NoReturn:
    console.print(
        f"Cannot verify the server identity (PID: {pid}). Stop it from its original "
        "terminal or service manager, then start this version.",
        markup=False,
    )
    raise typer.Exit(1)


def _wait_for_exit(identity: ProcessIdentity, timeout: float) -> bool:
    for _ in range(max(1, int(timeout * 10))):
        current = process_identity(identity.pid)
        if current is not None and current.start != identity.start:
            return True
        if current is None:
            if not _is_running(identity.pid):
                return True
            raise PermissionError("Cannot verify whether the server has stopped.")
        time.sleep(0.1)
    return False


DEV_TOOLS_OPTION = typer.Option(
    "--dev-tools",
    help=(
        "Developer tools: let Setup connect this install to a replay source (another "
        "TweetNook server) so it replays that archive instead of contacting Twitter/X. "
        "Same as TWEETNOOK_DEV_TOOLS=1."
    ),
)


@web_app.command("start", help="Start the background web server.")
def start_web(dev_tools: Annotated[bool, DEV_TOOLS_OPTION] = False) -> None:
    console = Console()
    _require_web_dependencies(console)
    config, paths = load_config()

    pid_file = _get_pid_file(paths.data_dir)
    recorded_pid = _running_pid(pid_file, config.web.port)
    if recorded_pid is not None:
        console.print(f"[yellow]Web server is already running (PID: {recorded_pid}).[/yellow]")
        raise typer.Exit(0)

    if (pid := _read_pid(pid_file)) is not None and _is_running(pid):
        _refuse_unverified_process(console, pid)

    port_pid = _find_pid_by_port(config.web.port)
    if port_pid is not None and _is_running(port_pid):
        console.print(
            f"[red]Cannot start web server: port {config.web.port} is already in use "
            f"(PID: {port_pid}).[/red]"
        )
        raise typer.Exit(1)

    web_config = config.web
    console.print(
        f"Starting web server on {display_web_url(web_config.host, web_config.port)} ...",
        markup=False,
    )

    command = [sys.executable, "-m", "tweetnook", "serve"]
    if dev_tools:
        command.append("--dev-tools")
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        console.print(f"[red]Failed to start web server: {exc}[/red]")
        raise typer.Exit(1) from exc

    identity: ProcessIdentity | None = None
    try:
        identity = process_identity(process.pid)
        if identity is None:
            raise OSError("Cannot read the new server's process identity.")
        record_web_process(paths.data_dir, identity, config.web.port)
        pid_file.write_text(str(process.pid), encoding="utf-8")
    except OSError as exc:
        try:
            terminate_owned_process(process)
        except OSError:
            pass
        if identity is not None:
            with suppress(OSError):
                forget_web_process(paths.data_dir, identity)
        console.print(f"[red]Failed to record web server PID: {exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"[green]Started background server (PID: {process.pid}).[/green]")


@web_app.command("stop", help="Stop the background web server.")
def stop_web() -> None:
    console = Console()
    config, paths = load_config()
    pid_file = _get_pid_file(paths.data_dir)
    pid = _running_pid(pid_file, config.web.port)

    if pid is None:
        if unverified := _unverified_process(pid_file, config.web.port):
            _refuse_unverified_process(console, unverified)
        console.print("[yellow]Web server is not running.[/yellow]")
        pid_file.unlink(missing_ok=True)
        return

    identity = recorded_web_process(paths.data_dir, pid)
    if identity is None:
        _refuse_unverified_process(console, pid)
    try:
        console.print(f"Stopping web server (PID: {pid})...")
        identity.send_signal(signal.SIGTERM)
        if not _wait_for_exit(identity, WEB_STOP_TIMEOUT):
            identity.send_signal(signal.SIGKILL)
            if not _wait_for_exit(identity, WORKER_KILL_TIMEOUT):
                raise TimeoutError("The server did not stop after SIGKILL.")
        console.print("[green]Server stopped.[/green]")
    except ProcessLookupError:
        console.print("[yellow]Web server is not running.[/yellow]")
    except Exception as exc:
        console.print(f"[red]Error stopping server: {exc}[/red]")
        raise typer.Exit(1) from exc
    forget_web_process(paths.data_dir, identity)


@web_app.command("restart", help="Restart the background web server.")
def restart_web(dev_tools: Annotated[bool, DEV_TOOLS_OPTION] = False) -> None:
    console = Console()
    _require_web_dependencies(console)
    _, paths = load_config()
    stop_web()
    start_web(dev_tools=dev_tools)


@web_app.command("status", help="Check if the background web server is running.")
def status_web() -> None:
    console = Console()
    config, paths = load_config()
    pid_file = _get_pid_file(paths.data_dir)
    web_config = config.web
    url = display_web_url(web_config.host, web_config.port)
    pid = _running_pid(pid_file, web_config.port)

    if pid is not None:
        console.print(f"Web server is [green]running[/green] (PID: {pid}).")
        console.print(f"URL: {url}")
        return

    if unverified := _unverified_process(pid_file, web_config.port):
        _refuse_unverified_process(console, unverified)
    console.print("Web server is [red]stopped[/red].")
    console.print(f"Configured URL: {url}")
    pid_file.unlink(missing_ok=True)
    raise typer.Exit(1)


@web_app.command("set-password", help="Set the password for the web server.")
def set_password() -> None:
    console = Console()
    config, paths = load_config()

    password = typer.prompt(
        "Enter new web password",
        hide_input=True,
        confirmation_prompt=True,
    )
    if not password:
        console.print("[red]Password cannot be empty.[/red]")
        raise typer.Exit(1)

    password_hash = hash_password(password)
    try:
        update_config_values(paths, {"web.password_hash": password_hash})
    except OSError as exc:
        console.print(f"[red]Failed to save password: {exc}[/red]")
        raise typer.Exit(1) from exc
    console.print("[green]Password updated in config.toml.[/green]")
    console.print(
        "Note: If the server is currently running, you must restart it for the new "
        "password to take effect."
    )
    console.print("Run `tweetnook web stop` and `tweetnook web start`.")
