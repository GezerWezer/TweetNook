"""Production subprocess supervisor for Web and scheduled maintenance jobs."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from typing import Any

from tweetnook.activity_history import (
    ACTIVITY_ORIGIN_ENV,
    ACTIVITY_RUN_ID_ENV,
    cleanup_runs,
    mark_run_process_result,
    new_run_id,
    reserve_run,
    update_run_metadata,
)
from tweetnook.config import AppConfig, XDGPaths
from tweetnook.exceptions import ProcessLockError
from tweetnook.locking import ProcessLock

WORKER_INTERRUPT_TIMEOUT = 45
WORKER_TERMINATE_TIMEOUT = 10
WORKER_KILL_TIMEOUT = 5
WEB_STOP_TIMEOUT = 70
SERVICE_STOP_TIMEOUT = 90


class JobConflictError(RuntimeError):
    """Raised when another archive command is already active."""


class JobSupervisor:
    """Launch one guarded tweetnook worker process at a time."""

    def __init__(
        self,
        paths: XDGPaths,
        config: AppConfig,
        *,
        on_complete: Callable[[str, int], None] | None = None,
        on_start: Callable[[str, int], None] | None = None,
    ) -> None:
        self.paths = paths
        self.config = config
        self.on_complete = on_complete
        self.on_start = on_start
        self._lock = threading.RLock()
        self._process: subprocess.Popen[bytes] | None = None
        self._run_id: str | None = None
        self._kind: str | None = None
        self._origin: str | None = None
        self._stopping = False
        self._console_handle: Any | None = None
        self._monitor_thread: threading.Thread | None = None
        self._shutdown = False

    @property
    def active(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    @property
    def active_run_id(self) -> str | None:
        with self._lock:
            return self._run_id

    @property
    def active_pid(self) -> int | None:
        with self._lock:
            return self._process.pid if self.active and self._process is not None else None

    @property
    def active_kind(self) -> str | None:
        with self._lock:
            return self._kind if self.active else None

    @property
    def active_origin(self) -> str | None:
        with self._lock:
            return self._origin if self.active else None

    def start(
        self,
        *,
        kind: str,
        cli_args: list[str],
        origin: str,
        title: str,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if self._shutdown:
                raise JobConflictError("The server is stopping.")
            if self._run_id is not None or self._external_activity_is_running():
                raise JobConflictError("Another Web or scheduled command is already running.")
            run_id = run_id or new_run_id()
            command = [sys.executable, "-m", "tweetnook", *cli_args]
            run_dir = reserve_run(
                self.paths.data_dir,
                run_id=run_id,
                title=title,
                command=command,
                origin=origin,
            )
            console_handle = (run_dir / "console.log").open("ab")
            env = os.environ.copy()
            env[ACTIVITY_RUN_ID_ENV] = run_id
            env[ACTIVITY_ORIGIN_ENV] = origin
            env["PYTHONUNBUFFERED"] = "1"
            try:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=console_handle,
                    stderr=subprocess.STDOUT,
                    env=env,
                    start_new_session=True,
                )
            except OSError:
                console_handle.close()
                mark_run_process_result(self.paths.data_dir, run_id, exit_code=127)
                raise
            self._process = process
            self._run_id = run_id
            self._kind = kind
            self._origin = origin
            self._stopping = False
            self._console_handle = console_handle
            update_run_metadata(
                self.paths.data_dir,
                run_id,
                status="starting",
                pid=process.pid,
                process_group=process.pid,
            )
            self._monitor_thread = threading.Thread(
                target=self._monitor,
                args=(process, run_id),
                daemon=True,
                name=f"tweetnook-job-{kind}",
            )
            self._monitor_thread.start()
            if self.on_start is not None:
                self.on_start(run_id, process.pid)
            return {"started": True, "kind": kind, "run_id": run_id, "pid": process.pid}

    def _external_activity_is_running(self) -> bool:
        try:
            snapshot = json.loads(self.paths.activity_status_file.read_text(encoding="utf-8"))
            pid = snapshot.get("pid")
            if snapshot.get("running") and isinstance(pid, int) and pid > 0:
                os.kill(pid, 0)
                return True
        except (OSError, ValueError, json.JSONDecodeError, AttributeError):
            pass

        probe = ProcessLock(self.paths.command_lock_file)
        try:
            probe.acquire()
        except ProcessLockError:
            return True
        else:
            probe.release()
            return False

    def stop(self) -> dict[str, Any]:
        with self._lock:
            process = self._process
            if process is None or process.poll() is not None:
                raise JobConflictError("No Web or scheduled task is running.")
            self._stopping = True
            try:
                os.killpg(process.pid, signal.SIGINT)
            except ProcessLookupError:
                pass
            return {"stopping": True, "run_id": self._run_id}

    def shutdown(self, *, timeout: float = WORKER_INTERRUPT_TIMEOUT) -> None:
        """Stop managed workers cooperatively, escalating only after bounded waits."""
        with self._lock:
            self._shutdown = True
            process = self._process
            monitor = self._monitor_thread
            if process is not None and process.poll() is None:
                self._stopping = True
        if process is not None:
            for sig, grace in (
                (signal.SIGINT, timeout),
                (signal.SIGTERM, WORKER_TERMINATE_TIMEOUT),
                (signal.SIGKILL, WORKER_KILL_TIMEOUT),
            ):
                if process.poll() is not None:
                    break
                try:
                    os.killpg(process.pid, sig)
                except ProcessLookupError:
                    break
                try:
                    process.wait(timeout=grace)
                    break
                except subprocess.TimeoutExpired:
                    continue
        # The monitor closes logs, records stopped status and delivers the callback.
        # Never hold the supervisor lock while joining it.
        if monitor is not None and monitor is not threading.current_thread():
            monitor.join(timeout=WORKER_KILL_TIMEOUT)

    def _monitor(self, process: subprocess.Popen[bytes], run_id: str) -> None:
        exit_code = process.wait()
        with self._lock:
            stopped = (self._stopping and self._run_id == run_id) or exit_code in {
                130,
                -signal.SIGINT,
            }
            mark_run_process_result(
                self.paths.data_dir,
                run_id,
                exit_code=exit_code,
                stopped=stopped,
            )
            handle = self._console_handle if self._run_id == run_id else None
            if self._run_id == run_id:
                self._process = None
                self._run_id = None
                self._kind = None
                self._origin = None
                self._stopping = False
                self._console_handle = None
        if handle is not None:
            handle.close()
        cleanup_runs(
            self.paths.data_dir,
            max_runs=self.config.activity.max_runs,
            retention_days=self.config.activity.retention_days,
        )
        if self.on_complete is not None:
            self.on_complete(run_id, exit_code)
