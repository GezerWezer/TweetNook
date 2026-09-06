from __future__ import annotations

import json
import signal
import subprocess
import threading
import time

import pytest

from tweetnook.config import AppConfig, XDGPaths
from tweetnook.job_supervisor import JobConflictError, JobSupervisor
from tweetnook.locking import ProcessLock


def _paths(tmp_path):
    return XDGPaths(config_dir=tmp_path / "config", data_dir=tmp_path, cache_dir=tmp_path / "cache")


def test_supervisor_launches_isolated_cli_worker_and_records_exit(tmp_path, monkeypatch) -> None:
    paths = _paths(tmp_path)
    finished = threading.Event()
    captured = {}

    class Process:
        pid = 4321

        def poll(self):
            return 0 if finished.is_set() else None

        def wait(self, timeout=None):
            assert timeout is None
            finished.wait(2)
            return 0

    def popen(command, **kwargs):
        captured.update(command=command, kwargs=kwargs)
        return Process()

    monkeypatch.setattr("tweetnook.job_supervisor.subprocess.Popen", popen)
    supervisor = JobSupervisor(paths, AppConfig())

    result = supervisor.start(
        kind="sync",
        cli_args=["sync"],
        origin="web",
        title="tweetnook sync",
    )

    assert captured["command"][-1] == "sync"
    assert captured["kwargs"]["start_new_session"] is True
    assert captured["kwargs"]["env"]["TWEETNOOK_ACTIVITY_ORIGIN"] == "web"
    assert supervisor.active is True
    finished.set()
    metadata_path = paths.activity_runs_dir / result["run_id"] / "metadata.json"
    for _ in range(100):
        metadata = json.loads(metadata_path.read_text())
        if metadata["status"] == "completed":
            break
        time.sleep(0.01)
    metadata = json.loads(metadata_path.read_text())
    assert metadata["status"] == "completed"
    assert metadata["exit_code"] == 0


def test_supervisor_stops_the_worker_process_group(tmp_path, monkeypatch) -> None:
    paths = _paths(tmp_path)
    finished = threading.Event()
    signals = []

    class Process:
        pid = 6789

        def poll(self):
            return None

        def wait(self, timeout=None):
            finished.wait(2)
            return 130

    monkeypatch.setattr("tweetnook.job_supervisor.subprocess.Popen", lambda *_a, **_k: Process())
    monkeypatch.setattr(
        "tweetnook.job_supervisor.os.killpg",
        lambda pid, sent_signal: signals.append((pid, sent_signal)),
    )
    supervisor = JobSupervisor(paths, AppConfig())
    result = supervisor.start(kind="sync", cli_args=["sync"], origin="web", title="tweetnook sync")

    response = supervisor.stop()

    assert response["stopping"] is True
    assert signals == [(6789, signal.SIGINT)]
    finished.set()
    metadata_path = paths.activity_runs_dir / result["run_id"] / "metadata.json"
    for _ in range(100):
        metadata = json.loads(metadata_path.read_text())
        if metadata["status"] == "stopped":
            break
        time.sleep(0.01)
    assert metadata["status"] == "stopped"


def test_supervisor_rejects_a_live_external_pipeline(tmp_path, monkeypatch) -> None:
    paths = _paths(tmp_path)
    paths.activity_status_file.write_text(
        json.dumps({"running": True, "pid": 2468}),
        encoding="utf-8",
    )
    monkeypatch.setattr("tweetnook.job_supervisor.os.kill", lambda _pid, _signal: None)
    monkeypatch.setattr(
        "tweetnook.job_supervisor.subprocess.Popen",
        lambda *_args, **_kwargs: pytest.fail("worker should not launch"),
    )
    supervisor = JobSupervisor(paths, AppConfig())

    with pytest.raises(JobConflictError, match="already running"):
        supervisor.start(
            kind="sync",
            cli_args=["sync"],
            origin="schedule",
            title="tweetnook sync",
        )


def test_supervisor_rejects_lifecycle_lock_without_relying_on_snapshot(
    tmp_path, monkeypatch
) -> None:
    paths = _paths(tmp_path)
    lock = ProcessLock(paths.command_lock_file)
    lock.acquire()
    monkeypatch.setattr(
        "tweetnook.job_supervisor.subprocess.Popen",
        lambda *_args, **_kwargs: pytest.fail("worker should not launch"),
    )
    supervisor = JobSupervisor(paths, AppConfig())
    try:
        with pytest.raises(JobConflictError, match="already running"):
            supervisor.start(
                kind="sync",
                cli_args=["sync"],
                origin="schedule",
                title="tweetnook sync",
            )
    finally:
        lock.release()


@pytest.mark.parametrize("exit_on", [signal.SIGINT, signal.SIGTERM, signal.SIGKILL])
def test_shutdown_escalates_only_after_grace_and_records_stop(tmp_path, monkeypatch, exit_on):
    paths = _paths(tmp_path)
    finished = threading.Event()
    signals, waits, callbacks = [], [], []

    class Process:
        pid = 54321

        def poll(self):
            return 130 if finished.is_set() else None

        def wait(self, timeout=None):
            if timeout is None:
                assert finished.wait(3)
            else:
                waits.append(timeout)
                if not finished.is_set():
                    raise subprocess.TimeoutExpired("worker", timeout)
            return 130

    def killpg(pid, sig):
        signals.append(sig)
        if sig == exit_on:
            finished.set()

    monkeypatch.setattr("tweetnook.job_supervisor.subprocess.Popen", lambda *a, **k: Process())
    monkeypatch.setattr("tweetnook.job_supervisor.os.killpg", killpg)
    supervisor = JobSupervisor(paths, AppConfig(), on_complete=lambda *args: callbacks.append(args))
    result = supervisor.start(kind="import", cli_args=["import"], origin="web", title="Import")
    supervisor.shutdown(timeout=0.01)
    assert signals == [signal.SIGINT, signal.SIGTERM, signal.SIGKILL][: signals.index(exit_on) + 1]
    assert waits[0] == 0.01
    assert callbacks == [(result["run_id"], 130)]
    metadata = json.loads(
        (paths.activity_runs_dir / result["run_id"] / "metadata.json").read_text()
    )
    assert metadata["status"] == "stopped"
    assert supervisor._console_handle is None
    with pytest.raises(JobConflictError, match="stopping"):
        supervisor.start(kind="sync", cli_args=["sync"], origin="web", title="Sync")


def test_shutdown_does_not_signal_finished_worker(tmp_path, monkeypatch):
    supervisor = JobSupervisor(_paths(tmp_path), AppConfig())
    monkeypatch.setattr(
        "tweetnook.job_supervisor.os.killpg", lambda *args: pytest.fail("no active worker")
    )
    supervisor.shutdown()
