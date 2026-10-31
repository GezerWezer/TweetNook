import json
import signal
import subprocess
import sys
import threading
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from tweetnook.config import AppConfig
from tweetnook.core.exceptions import ProcessLockError
from tweetnook.jobs import pipeline as reporting
from tweetnook.jobs import supervisor as supervision


def test_reporter_enter_failure_releases_history_context_and_os_lock(paths, monkeypatch):
    reporter = reporting.PipelineReporter(
        Console(file=StringIO()),
        "failed startup",
        interactive=True,
        state_path=paths.activity_status_file,
    )

    def fail_start(*args, **kwargs):
        raise RuntimeError("live startup failed")

    monkeypatch.setattr(reporting.Live, "start", fail_start)
    monkeypatch.setattr(
        reporting.Live, "stop", lambda *a: (_ for _ in ()).throw(OSError("stop failed"))
    )
    with pytest.raises(RuntimeError, match="live startup failed"):
        with reporter:
            pytest.fail("body must not run")
    assert not reporting.current_pipeline().active
    metadata = json.loads((paths.activity_runs_dir / reporter.run_id / "metadata.json").read_text())
    assert metadata["status"] == "failed"
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            'import fcntl,sys; h=open(sys.argv[1], "a+"); '
            "fcntl.flock(h, fcntl.LOCK_EX|fcntl.LOCK_NB)",
            str(paths.command_lock_file),
        ],
        capture_output=True,
        timeout=5,
    )
    assert probe.returncode == 0, probe.stderr


@pytest.mark.parametrize("failure", ["console", "monitor"])
def test_supervisor_failed_acquisition_rolls_back_its_resources(paths, monkeypatch, failure):
    handles, signals, waits = [], [], []
    original_open = Path.open

    class Process:
        pid = 43210
        exited = False

        def poll(self):
            return 127 if self.exited else None

        def wait(self, timeout=None):
            waits.append(timeout)
            self.exited = True
            return 127

    process = Process()

    def open_path(path, *args, **kwargs):
        if path.name == "console.log":
            if failure == "console":
                raise PermissionError("console denied")
            handle = original_open(path, *args, **kwargs)
            handles.append(handle)
            return handle
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_path)
    monkeypatch.setattr(supervision.subprocess, "Popen", lambda *a, **kw: process)
    monkeypatch.setattr(supervision.os, "killpg", lambda pid, sig: signals.append((pid, sig)))
    monkeypatch.setattr(
        supervision.threading.Thread,
        "start",
        lambda _: (_ for _ in ()).throw(RuntimeError("monitor failed")),
    )
    supervisor = supervision.JobSupervisor(paths, AppConfig())
    with pytest.raises((PermissionError, RuntimeError)):
        supervisor.start(kind="sync", cli_args=["sync"], origin="web", title="Sync")
    assert not supervisor.active
    assert supervisor.active_run_id is None
    assert all(handle.closed for handle in handles)
    assert signals == ([(43210, signal.SIGTERM)] if failure == "monitor" else [])
    assert waits == ([supervision.WORKER_TERMINATE_TIMEOUT] if failure == "monitor" else [])
    runs = list(paths.activity_runs_dir.iterdir())
    assert len(runs) == 1
    assert json.loads((runs[0] / "metadata.json").read_text())["status"] == "failed"
    assert not (runs[0] / "final.reserve").exists()
    # A subsequent attempt reaches acquisition again rather than failing admission.
    with pytest.raises((PermissionError, RuntimeError)):
        supervisor.start(kind="sync", cli_args=["sync"], origin="web", title="Sync")


def test_shutdown_joins_old_callback_after_new_worker_finishes(paths, monkeypatch):
    old_callback, release_old, second_done, shutdown_done = (threading.Event() for _ in range(4))
    finish_first, finish_second = threading.Event(), threading.Event()
    processes = []

    class Process:
        def __init__(self, event):
            self.pid = 20000 + len(processes)
            self.event = event
            processes.append(self)

        def poll(self):
            return 0 if self.event.is_set() else None

        def wait(self, timeout=None):
            assert self.event.wait(5)
            return 0

    supplied = iter([Process(finish_first), Process(finish_second)])
    monkeypatch.setattr(supervision.subprocess, "Popen", lambda *a, **kw: next(supplied))

    def complete(run_id, code):
        if not old_callback.is_set():
            old_callback.set()
            assert release_old.wait(5)
        else:
            second_done.set()

    supervisor = supervision.JobSupervisor(paths, AppConfig(), on_complete=complete)
    supervisor.start(kind="sync", cli_args=["sync"], origin="web", title="Sync")
    finish_first.set()
    assert old_callback.wait(2)
    supervisor.start(kind="sync", cli_args=["sync"], origin="web", title="Sync")
    finish_second.set()
    assert second_done.wait(2)
    shutdown = threading.Thread(target=lambda: (supervisor.shutdown(), shutdown_done.set()))
    shutdown.start()
    try:
        assert not shutdown_done.wait(0.05)
    finally:
        release_old.set()
        shutdown.join(3)
    assert shutdown_done.is_set()


def test_reporter_that_loses_the_command_lock_leaves_the_running_status_alone(paths):
    running = reporting.PipelineReporter(
        Console(file=StringIO()),
        "running",
        interactive=False,
        state_path=paths.activity_status_file,
    )
    with running:
        before = b'{"title": "running"}'
        paths.activity_status_file.write_bytes(before)
        loser = reporting.PipelineReporter(
            Console(file=StringIO()),
            "second",
            interactive=False,
            state_path=paths.activity_status_file,
        )
        with pytest.raises(ProcessLockError):
            with loser:
                pytest.fail("body must not run")
        assert paths.activity_status_file.read_bytes() == before
        assert reporting.current_pipeline() is running
