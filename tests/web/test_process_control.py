"""Unowned listeners and stale process records cannot become stop targets."""

from __future__ import annotations

import json
import os
import signal
from io import StringIO
from types import SimpleNamespace

import pytest
from rich.console import Console
from typer.testing import CliRunner

from tweetnook.cli import web
from tweetnook.config import AppConfig
from tweetnook.core import process_identity as process_ids
from tweetnook.core.process_identity import ProcessIdentity
from tweetnook.jobs.pipeline import PipelineReporter
from tweetnook.web import processes, server
from tweetnook.web.deps import server_state
from tweetnook.web.routes import activity


@pytest.mark.parametrize("command", ["stop", "restart", "status", "start"])
@pytest.mark.parametrize("recorded", [False, True])
def test_personal_web_refuses_unverified_processes(monkeypatch, paths, command, recorded):
    monkeypatch.setattr(web, "load_config", lambda: (AppConfig(), paths))
    monkeypatch.setattr(web, "_require_web_dependencies", lambda _console: None)
    monkeypatch.setattr(web, "_find_pid_by_port", lambda _port: 77)
    monkeypatch.setattr(web, "_is_running", lambda pid: pid == 77)
    monkeypatch.setattr(web, "process_identity", lambda pid: ProcessIdentity(pid, "new", pid))
    if recorded:
        (paths.data_dir / ".web.pid").write_text("77")
        processes.record_web_process(paths.data_dir, ProcessIdentity(77, "old", 77), 8000)
    monkeypatch.setattr(processes, "process_identity", web.process_identity)
    monkeypatch.setattr(os, "kill", lambda *_: pytest.fail("unowned process signalled"))
    monkeypatch.setattr(os, "killpg", lambda *_: pytest.fail("unowned process group signalled"))
    monkeypatch.setattr(web.subprocess, "Popen", lambda *_a, **_k: pytest.fail("must not start"))

    result = CliRunner().invoke(web.web_app, [command])

    assert result.exit_code == 1
    assert "Cannot verify" in result.stdout or "already in use" in result.stdout
    if recorded:
        assert (paths.data_dir / ".web.pid").read_text() == "77"
        assert processes.recorded_web_process(paths.data_dir, 77) is not None


def test_copied_process_record_does_not_identify_another_archive(paths, tmp_path, monkeypatch):
    identity = ProcessIdentity(77, "birth", 77)
    other = tmp_path / "other"
    processes.record_web_process(other, identity, 8000)
    (paths.data_dir / ".web-process-77.json").write_bytes(
        (other / ".web-process-77.json").read_bytes()
    )
    monkeypatch.setattr(processes, "process_identity", lambda _pid: identity)
    assert processes.find_web_process(paths.data_dir, 8000) is None
    assert processes.recorded_web_process(paths.data_dir, 77) is None


def test_stop_timeout_preserves_records_for_retry(paths, monkeypatch):
    identity = ProcessIdentity(77, "birth", 77)
    processes.record_web_process(paths.data_dir, identity, 8000)
    (paths.data_dir / ".web.pid").write_text("77")
    monkeypatch.setattr(web, "load_config", lambda: (AppConfig(), paths))
    monkeypatch.setattr(web, "process_identity", lambda _pid: identity)
    monkeypatch.setattr(web, "_wait_for_exit", lambda *_args: False)
    signals = []
    monkeypatch.setattr(ProcessIdentity, "send_signal", lambda _self, sig: signals.append(sig))

    result = CliRunner().invoke(web.web_app, ["stop"])

    assert result.exit_code == 1
    assert signals == [signal.SIGTERM, signal.SIGKILL]
    assert "did not stop" in result.stdout
    assert (paths.data_dir / ".web.pid").read_text() == "77"
    assert processes.recorded_web_process(paths.data_dir, 77) == identity


def test_cleanup_keeps_a_new_owner_that_reuses_the_pid(paths):
    replacement = ProcessIdentity(77, "replacement", 77)
    processes.record_web_process(paths.data_dir, replacement, 8000)
    (paths.data_dir / ".web.pid").write_text("77")
    processes.forget_web_process(paths.data_dir, ProcessIdentity(77, "original", 77))
    assert processes.recorded_web_process(paths.data_dir, 77) == replacement
    assert (paths.data_dir / ".web.pid").read_text() == "77"


def test_stop_does_not_escalate_to_a_reused_pid(paths, monkeypatch):
    identity = ProcessIdentity(77, "birth", 77)
    processes.record_web_process(paths.data_dir, identity, 8000)
    (paths.data_dir / ".web.pid").write_text("77")
    monkeypatch.setattr(web, "load_config", lambda: (AppConfig(), paths))
    current = [identity]
    monkeypatch.setattr(web, "process_identity", lambda _pid: current[0])
    signals = []

    def stop(_self, sig):
        signals.append(sig)
        current[0] = ProcessIdentity(77, "replacement", 77)

    monkeypatch.setattr(ProcessIdentity, "send_signal", stop)
    result = CliRunner().invoke(web.web_app, ["stop"])
    assert result.exit_code == 0
    assert signals == [signal.SIGTERM]
    assert not (paths.data_dir / ".web.pid").exists()


@pytest.mark.parametrize("origin", ["cli", "web", "schedule"])
@pytest.mark.parametrize("identity_kind", ["missing", "reused", "current", "wrong-group"])
def test_activity_fallback_requires_the_recorded_lifetime(
    paths, monkeypatch, make_web_client, origin, identity_kind
):
    identity = ProcessIdentity(77, "birth", 78 if identity_kind == "wrong-group" else 77)
    snapshot = {"pid": 77, "origin": origin, "running": True, "run_id": "run"}
    if identity_kind != "missing":
        snapshot["process_identity"] = identity.record()
    paths.activity_status_file.write_text(json.dumps(snapshot))
    server_state["paths"] = paths
    monkeypatch.setattr(activity, "_process_is_running", lambda _pid: True)
    monkeypatch.setattr(process_ids, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(
        process_ids,
        "process_identity",
        lambda _pid: ProcessIdentity(77, "replacement", 77)
        if identity_kind == "reused"
        else identity,
    )
    calls = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: calls.append(("pid", pid, sig)))
    monkeypatch.setattr(os, "killpg", lambda pid, sig: calls.append(("group", pid, sig)))

    response = make_web_client(activity.router).post("/api/activity/stop")

    allowed = identity_kind == "current" or (identity_kind == "wrong-group" and origin == "cli")
    assert response.status_code == (202 if allowed else 409)
    assert calls == (
        [("pid" if origin == "cli" else "group", 77, signal.SIGINT)] if allowed else []
    )


def test_foreground_server_records_and_cleans_its_own_identity(paths, monkeypatch):
    import uvicorn

    def run(*_args, **_kwargs):
        recorded = processes.find_web_process(paths.data_dir, 8123)
        assert recorded is not None
        assert recorded.pid == os.getpid()
        raise RuntimeError("listener failure")

    monkeypatch.setattr(uvicorn, "run", run)
    with pytest.raises(RuntimeError, match="listener failure"):
        server.run_server(AppConfig(), paths, "127.0.0.1", 8123, None)
    assert list(paths.data_dir.glob(".web-process-*.json")) == []


def test_pipeline_records_stop_identity_without_exposing_it_in_status(paths, make_web_client):
    server_state["paths"] = paths
    client = make_web_client(activity.router)
    with PipelineReporter(
        Console(file=StringIO()), "tweetnook sync", state_path=paths.activity_status_file
    ) as pipeline:
        pipeline.add_step("sync", "Sync", total=1, unit="pages")
        pipeline.start_step("sync", activity="Reading")
        record = json.loads(paths.activity_status_file.read_text())["process_identity"]
        assert record["pid"] == os.getpid()
        assert record["group"] == os.getpgrp()
        assert record["start"]
        payload = client.get("/api/activity/status").json()
        assert payload["active"] is True
        assert "process_identity" not in payload["snapshot"]
    payload = client.get("/api/activity/status").json()
    assert payload["active"] is False
    assert "process_identity" not in payload["last_snapshot"]
