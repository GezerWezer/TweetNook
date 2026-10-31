from __future__ import annotations

import errno
import json
import signal
import subprocess
import threading
import time

import pytest

from tweetnook.config import AppConfig, XDGPaths
from tweetnook.core.locking import ProcessLock
from tweetnook.jobs.activity_history import new_run_id, read_run
from tweetnook.jobs.outcome import Outcome
from tweetnook.jobs.supervisor import JobCompletion, JobConflictError, JobSupervisor


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

    monkeypatch.setattr("tweetnook.jobs.supervisor.subprocess.Popen", popen)
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
        if metadata["status"] == "interrupted":
            break
        time.sleep(0.01)
    metadata = json.loads(metadata_path.read_text())
    assert metadata["status"] == "interrupted"
    assert metadata["exit_code"] == 0
    assert metadata["success"] is None


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

    monkeypatch.setattr("tweetnook.jobs.supervisor.subprocess.Popen", lambda *_a, **_k: Process())
    monkeypatch.setattr(
        "tweetnook.jobs.supervisor.os.killpg",
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


@pytest.mark.parametrize(
    ("first_outcome", "first_exit", "second_outcome", "second_exit"),
    [("succeeded", 0, "failed", 2), ("failed", 2, "succeeded", 0)],
)
def test_overlapping_completions_keep_their_own_outcomes(
    tmp_path, monkeypatch, first_outcome, first_exit, second_outcome, second_exit
):
    import tweetnook.jobs.supervisor as supervision

    paths = _paths(tmp_path)
    first_id, second_id = new_run_id(), new_run_id()
    first_at_cleanup, release_first, second_callback = (threading.Event() for _ in range(3))
    callbacks = []

    class Process:
        def __init__(self, pid, exit_code):
            self.pid = pid
            self.exit_code = exit_code
            self.done = threading.Event()

        def wait(self, timeout=None):
            assert self.done.wait(5)
            return self.exit_code

    first_process = Process(20001, first_exit)
    second_process = Process(20002, second_exit)
    supplied = iter([first_process, second_process])
    monkeypatch.setattr(supervision.subprocess, "Popen", lambda *a, **kw: next(supplied))
    original_cleanup = supervision.cleanup_runs

    def cleanup(*args, **kwargs):
        if threading.current_thread().name == "tweetnook-job-first":
            first_at_cleanup.set()
            assert release_first.wait(5)
        return original_cleanup(*args, **kwargs)

    monkeypatch.setattr(supervision, "cleanup_runs", cleanup)

    def complete(completion):
        callbacks.append(completion)
        if completion.run_id != first_id:
            second_callback.set()

    supervisor = JobSupervisor(paths, AppConfig(), on_complete=complete)
    monitors = []
    try:
        for kind, run_id, outcome, process in (
            ("first", first_id, first_outcome, first_process),
            ("second", second_id, second_outcome, second_process),
        ):
            supervisor.start(kind=kind, cli_args=[kind], origin="web", title=kind, run_id=run_id)
            monitors.append(supervisor._monitor_thread)
            (paths.activity_runs_dir / run_id / "final.json").write_text(
                json.dumps({"outcome": outcome, "success": outcome == "succeeded", "steps": []})
            )
            process.done.set()
            if kind == "first":
                assert first_at_cleanup.wait(2)
                assert not supervisor.active
            else:
                assert second_callback.wait(2)
    finally:
        release_first.set()
        first_process.done.set()
        second_process.done.set()
        for monitor in monitors:
            monitor.join(5)
            assert not monitor.is_alive()

    assert callbacks == [
        JobCompletion(second_id, second_exit, Outcome(second_outcome)),
        JobCompletion(first_id, first_exit, Outcome(first_outcome)),
    ]
    assert read_run(paths.data_dir, first_id)["snapshot"]["outcome"] == first_outcome
    assert supervisor.last_result["run_id"] == second_id


def test_supervisor_rejects_a_live_external_pipeline(tmp_path, monkeypatch) -> None:
    paths = _paths(tmp_path)
    paths.activity_status_file.write_text(
        json.dumps({"running": True, "pid": 2468}),
        encoding="utf-8",
    )
    monkeypatch.setattr("tweetnook.jobs.supervisor.os.kill", lambda _pid, _signal: None)
    monkeypatch.setattr(
        "tweetnook.jobs.supervisor.subprocess.Popen",
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
        "tweetnook.jobs.supervisor.subprocess.Popen",
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

    monkeypatch.setattr("tweetnook.jobs.supervisor.subprocess.Popen", lambda *a, **k: Process())
    monkeypatch.setattr("tweetnook.jobs.supervisor.os.killpg", killpg)
    supervisor = JobSupervisor(paths, AppConfig(), on_complete=callbacks.append)
    result = supervisor.start(kind="import", cli_args=["import"], origin="web", title="Import")
    supervisor.shutdown(timeout=0.01)
    assert signals == [signal.SIGINT, signal.SIGTERM, signal.SIGKILL][: signals.index(exit_on) + 1]
    assert waits[0] == 0.01
    assert callbacks == [JobCompletion(result["run_id"], 130, Outcome.STOPPED)]
    assert not callbacks[0].clean
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
        "tweetnook.jobs.supervisor.os.killpg", lambda *args: pytest.fail("no active worker")
    )
    supervisor.shutdown()


def test_monitor_always_cleans_up_and_calls_back_when_result_persistence_fails(
    tmp_path, monkeypatch
):
    import errno
    from types import SimpleNamespace
    from unittest.mock import Mock

    callbacks = []
    supervisor = JobSupervisor(_paths(tmp_path), AppConfig(), on_complete=callbacks.append)
    process = SimpleNamespace(wait=lambda: 2, poll=lambda: 2, pid=4321)
    handle = Mock()
    handle.close.side_effect = OSError(errno.ENOSPC, "No space left on device")
    supervisor._process = process
    supervisor._run_id = "test-run"
    supervisor._kind = "sync"
    supervisor._origin = "web"
    supervisor._console_handle = handle

    def full(*args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr("tweetnook.jobs.supervisor.mark_run_process_result", full)
    monkeypatch.setattr("tweetnook.jobs.supervisor.cleanup_runs", full)
    supervisor._monitor(process, "test-run")

    assert supervisor.active is False
    assert supervisor.active_run_id is None
    assert supervisor._console_handle is None
    handle.close.assert_called_once()
    assert callbacks == [JobCompletion("test-run", 2, Outcome.FAILED)]
    assert supervisor.last_result["snapshot"]["success"] is False
    # Persistence failure must not leave the supervisor refusing the next start.
    monkeypatch.setattr(supervisor, "external_activity_is_running", lambda: False)
    monkeypatch.setattr("tweetnook.jobs.supervisor.reserve_run", full)
    with pytest.raises(OSError):
        supervisor.start(kind="sync", cli_args=["sync"], origin="web", title="Sync")


class _QueueWorld:
    """A real supervisor where Popen is scripted per command and workers finish on request."""

    def __init__(self, tmp_path, monkeypatch, *, failing: set[str]):
        self.failures: list[tuple[str, str]] = []
        self.finish_events: list[threading.Event] = []
        self.launched: list[str] = []
        world = self

        class Process:
            def __init__(self):
                self.pid = 4000 + len(world.launched)
                self.done = threading.Event()
                world.finish_events.append(self.done)

            def poll(self):
                return 0 if self.done.is_set() else None

            def wait(self, timeout=None):
                self.done.wait(5)
                return 0

        def popen(command, **_kwargs):
            if command[-1] in failing:
                raise FileNotFoundError(errno.ENOENT, "No such file", command[0])
            world.launched.append(command[-1])
            return Process()

        monkeypatch.setattr("tweetnook.jobs.supervisor.subprocess.Popen", popen)
        self.supervisor = JobSupervisor(
            _paths(tmp_path),
            AppConfig(),
            on_launch_failure=lambda run_id, title, _exc: self.failures.append((run_id, title)),
        )
        monkeypatch.setattr(self.supervisor, "external_activity_is_running", lambda: False)

    def enqueue(self, kind):
        return self.supervisor.enqueue(kind=kind, cli_args=[kind], origin="web", title=kind)

    def wait_for(self, kind):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.launched and self.launched[-1] == kind and self.supervisor.active:
                return
            time.sleep(0.01)
        pytest.fail(
            f"{kind} never started; launched {self.launched}, queue {self.supervisor.queue}"
        )


def test_deferred_launch_failure_is_reported_and_the_next_job_starts(tmp_path, monkeypatch):
    world = _QueueWorld(tmp_path, monkeypatch, failing={"broken"})
    assert world.enqueue("first")["started"] is True
    assert world.enqueue("broken") == {
        "started": False,
        "queued": True,
        "kind": "broken",
        "position": 1,
    }
    assert world.enqueue("after")["position"] == 2

    world.finish_events[0].set()
    world.wait_for("after")

    assert [title for _run_id, title in world.failures] == ["broken"]
    run = read_run(tmp_path, world.failures[0][0])
    assert run["exit_code"] == 127
    assert run["status"] == "failed"
    assert world.supervisor.queue == []
    world.finish_events[1].set()


def test_requested_job_that_cannot_launch_fails_the_request(tmp_path, monkeypatch):
    world = _QueueWorld(tmp_path, monkeypatch, failing={"broken"})
    with pytest.raises(OSError):
        world.enqueue("broken")
    assert world.supervisor.queue == []
    assert world.failures == []


def test_queue_conflict_keeps_the_job_and_retries(tmp_path, monkeypatch):
    world = _QueueWorld(tmp_path, monkeypatch, failing=set())
    monkeypatch.setattr(world.supervisor, "external_activity_is_running", lambda: True)
    assert world.enqueue("later")["position"] == 1
    assert world.supervisor._retry_timer is not None
    world.supervisor._retry_timer.cancel()
    monkeypatch.setattr(world.supervisor, "external_activity_is_running", lambda: False)
    world.supervisor._start_next()
    world.wait_for("later")
    world.finish_events[0].set()
