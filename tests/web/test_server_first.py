from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tweetnook.activity_history import mark_run_process_result, new_run_id, reserve_run
from tweetnook.config import AppConfig, ensure_paths, resolve_paths
from tweetnook.storage import open_archive_store
from tweetnook.web import server
from tweetnook.web.deps import server_state
from tweetnook.web.notices import NoticeStore
from tweetnook.web.setup_coordinator import SetupCoordinator


class Supervisor:
    active = False
    active_run_id = None
    active_kind = None
    active_pid = None
    active_origin = "web"

    def __init__(self, paths, config, **callbacks):
        self.paths, self.config, self.callbacks = paths, config, callbacks
        self.calls = []

    def start(self, **kwargs):
        assert not self.active
        self.calls.append(kwargs)
        self.active = True
        self.active_run_id = kwargs.get("run_id") or new_run_id()
        self.active_kind = kwargs["kind"]
        reserve_run(
            self.paths.data_dir,
            run_id=self.active_run_id,
            title=kwargs["title"],
            command=kwargs["cli_args"],
            origin=kwargs["origin"],
        )
        return {"started": True, "run_id": self.active_run_id, "kind": self.active_kind}

    def _external_activity_is_running(self):
        return self.active

    def finish(self, code=0, *, stopped=False):
        run_id = self.active_run_id
        self.active = False
        self.active_run_id = None
        self.active_kind = None
        mark_run_process_result(self.paths.data_dir, run_id, exit_code=code, stopped=stopped)
        if callback := self.callbacks.get("on_complete"):
            callback(run_id, code)
        return run_id

    def shutdown(self):
        if self.active:
            self.finish(130, stopped=True)


@pytest.fixture
def first_run(tmp_path, monkeypatch):
    for key, folder in [
        ("XDG_DATA_HOME", "data"),
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_CACHE_HOME", "cache"),
    ]:
        monkeypatch.setenv(key, str(tmp_path / folder))
    for key in ("TWEETNOOK_AUTH_TOKEN", "TWEETNOOK_CT0", "TWEETNOOK_USER_ID"):
        monkeypatch.delenv(key, raising=False)
    paths = ensure_paths(resolve_paths())
    server_state.update(paths=paths, config=AppConfig())
    monkeypatch.setattr(server, "JobSupervisor", Supervisor)
    return paths


def test_fresh_server_empty_choice_password_and_live_attachment(first_run):
    with TestClient(server.app) as client:
        assert not first_run.database_path.exists()
        status = client.get("/api/setup").json()
        assert status["required"] and not status["archive"]["ready"]
        assert client.get("/api/activity/status").status_code == 200
        assert client.get("/api/tweets").json()["detail"]["code"] == "setup_required"
        assert client.post("/api/setup/complete", json={"skip_auth": True}).status_code == 409
        assert client.post("/api/setup/archive/empty").status_code == 200
        assert first_run.database_path.exists()
        assert "schedule_manager" not in server_state
        assert client.post("/api/setup/complete", json={"skip_auth": True}).status_code == 422
        response = client.post(
            "/api/setup/complete",
            json={"skip_auth": True, "password": "test-secret", "confirm_password": "test-secret"},
        )
        assert response.status_code == 200
        assert server_state["schedule_manager"] is not None
        assert client.get("/api/setup").status_code == 401
        assert client.get("/api/tweets", auth=("any", "test-secret")).status_code == 200
        before = first_run.setup_state_file.read_bytes()
        assert client.get("/api/setup", auth=("any", "test-secret")).json()["completed"]
        assert first_run.setup_state_file.read_bytes() == before


def test_upgrade_recognizes_existing_sqlite(first_run):
    store = open_archive_store(first_run, create=True)
    store.close()
    with TestClient(server.app) as client:
        status = client.get("/api/setup").json()
        assert status["completed"] and not status["required"]
        assert status["archive"]["ready"]
        scheduler = server_state["schedule_manager"]
        server.start_scheduler_if_ready()
        assert server_state["schedule_manager"] is scheduler


@pytest.fixture
def coordinator(first_run, monkeypatch):
    supervisor = Supervisor(first_run, server_state["config"])
    server_state["job_supervisor"] = supervisor
    store = SimpleNamespace(
        has_completed_archive_import=lambda: True,
        count_incomplete_initial_enrichment=lambda: 3,
        close=lambda: None,
    )

    def attach():
        if first_run.database_path.exists():
            server_state["store"] = store

    monkeypatch.setattr("tweetnook.web.setup_coordinator.open_archive_store", lambda *a, **k: store)
    notices = NoticeStore(first_run.notices_file)
    coordinator = SetupCoordinator(first_run, server_state, attach, lambda: None, notices)
    supervisor.callbacks["on_complete"] = coordinator.job_completed
    return coordinator, supervisor, store


@pytest.mark.parametrize("auth_first", [True, False])
def test_import_auth_order_enriches_exactly_once(coordinator, auth_first):
    coord, jobs, store = coordinator
    coord.begin("import")
    assert jobs.calls[0]["cli_args"][-1] == "--offline"
    server_state["config"].auth.auth_token = "candidate"
    server_state["config"].auth.ct0 = "candidate-csrf"
    if auth_first:
        coord.auth_was_verified()
        assert len(jobs.calls) == 1
    coord.paths.database_path.touch()
    jobs.finish()
    if not auth_first:
        assert len(jobs.calls) == 1
        coord.auth_was_verified()
    assert jobs.calls[-1]["cli_args"] == ["import", "enrich"]
    for _ in range(3):
        coord.reconcile()
    assert len(jobs.calls) == 2
    jobs.finish(1)
    coord.reconcile()
    assert len(jobs.calls) == 2
    assert coord.notices.list()


def test_restart_completed_import_waits_for_verified_auth(coordinator):
    coord, jobs, _ = coordinator
    coord.begin("import")
    jobs.callbacks.clear()
    coord.paths.database_path.touch()
    jobs.finish()
    coord.state.auth_verified = True
    coord.save()
    server_state["config"].auth.auth_token = "candidate"
    server_state["config"].auth.ct0 = "candidate-csrf"
    restarted = SetupCoordinator(
        coord.paths, server_state, coord.attach, lambda: None, coord.notices
    )
    restarted.reconcile()
    assert jobs.calls[-1]["kind"] == "enrich"


def test_interrupted_import_does_not_attach_partial_database(coordinator):
    coord, jobs, _ = coordinator
    coord.begin("import")
    coord.paths.database_path.touch()
    jobs.active = False
    jobs.active_run_id = None
    coord.reconcile()
    assert coord.state.initialization_result == "interrupted"
    assert server_state.get("store") is None
    assert len(jobs.calls) == 1


def test_zero_pending_work_does_not_spawn_enrichment(coordinator):
    coord, jobs, store = coordinator
    store.count_incomplete_initial_enrichment = lambda: 0
    coord.begin("import")
    coord.paths.database_path.touch()
    jobs.finish()
    assert not coord.state.auto_enrich_requested
    assert len(jobs.calls) == 1


def test_migration_never_arms_enrichment(coordinator):
    coord, jobs, _ = coordinator
    coord.begin("migration")
    coord.paths.database_path.touch()
    jobs.finish()
    assert server_state["store"] is not None
    assert not coord.state.auto_enrich_requested
    assert len(jobs.calls) == 1


def test_notice_dismissal_survives_restart(first_run):
    notices = NoticeStore(first_run.notices_file)
    notices.add("one", "Import failed", "Retry from Setup", action="setup")
    notices.dismiss("one")
    restarted = NoticeStore(first_run.notices_file)
    restarted.add("one", "Duplicate", "Ignored")
    assert len(restarted.list()) == 1
    assert restarted.list()[0]["dismissed"]


def test_scheduled_skip_callback_writes_one_warning_per_blocking_run(first_run):
    notices = NoticeStore(first_run.notices_file)
    server_state["notices"] = notices
    context = {
        "scheduled_at": 1_000.0,
        "active_run_id": "long-import",
        "active_kind": "import",
    }

    server._record_schedule_skip(context)
    server._record_schedule_skip({**context, "scheduled_at": 2_000.0})
    server._record_schedule_skip(
        {"scheduled_at": 3_000.0, "active_run_id": "long-enrich", "active_kind": "enrich"}
    )

    items = notices.list()
    assert len(items) == 2
    assert items[0]["id"] == "schedule-skipped-long-enrich"
    assert items[1]["id"] == "schedule-skipped-long-import"
    assert items[1]["title"] == "Scheduled sync skipped"
    assert items[1]["severity"] == "warning"
    assert items[1]["action"] == "schedule"


def test_setup_can_finish_while_import_runs_and_scheduler_waits(first_run):
    with TestClient(server.app) as client:
        coord = server_state["setup_coordinator"]
        coord.begin("import")
        result = client.post(
            "/api/setup/complete",
            json={"skip_auth": True, "password": "secret", "confirm_password": "secret"},
        )
        assert result.status_code == 200
        assert "schedule_manager" not in server_state
        assert client.get("/api/stats/summary", auth=("x", "secret")).status_code == 503
        store = open_archive_store(first_run, create=True)
        store.set_import_manifest("test", archive_generation_date=None, status="completed")
        store.close()
        server_state["job_supervisor"].finish()
        assert server_state["store"] is not None
        assert server_state["schedule_manager"] is not None
        assert client.get("/api/tweets", auth=("x", "secret")).status_code == 200


def test_success_exit_without_import_manifest_is_not_ready(coordinator):
    coord, jobs, store = coordinator
    store.has_completed_archive_import = lambda: False
    coord.begin("import")
    coord.paths.database_path.touch()
    jobs.finish()
    assert coord.state.initialization_result == "failed"
    assert server_state.get("store") is None


def test_auth_endpoint_works_while_import_is_active(first_run, monkeypatch):
    import subprocess

    from tweetnook.web.routes import setup

    monkeypatch.setattr(
        setup,
        "_test_auth_candidate",
        lambda candidate: subprocess.CompletedProcess([], 0, "OK", ""),
    )
    with TestClient(server.app) as client:
        coordinator = server_state["setup_coordinator"]
        coordinator.begin("import")
        jobs = server_state["job_supervisor"]
        result = client.put("/api/setup/auth", json={"auth_token": "fixture", "ct0": "fixture"})
        assert result.status_code == 200
        assert result.json()["values"]["auth_token"] == "********"
        assert jobs.active and len(jobs.calls) == 1
        assert coordinator.state.auth_verified


def test_real_offline_import_worker_attaches_archive(first_run, tmp_path, monkeypatch):
    import time

    from tests.test_archive_import import _write_archive_dir, _write_archive_zip
    from tweetnook.job_supervisor import JobSupervisor

    source = _write_archive_dir(tmp_path / "source")
    archive = _write_archive_zip(source, tmp_path / "fixture.zip")
    monkeypatch.setattr(server, "JobSupervisor", JobSupervisor)
    with TestClient(server.app) as client:
        assert client.put("/api/setup/archive", content=archive.read_bytes()).status_code == 200
        result = client.post("/api/setup/archive/import")
        assert result.status_code == 202
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            status = client.get("/api/setup").json()
            if status["archive"]["ready"]:
                break
            time.sleep(0.05)
        assert status["archive"]["ready"], status
        assert status["archive"]["imported"]
        assert status["archive"]["enrichment_state"] == "waiting_for_auth"
        assert len(list(first_run.activity_runs_dir.iterdir())) == 1
        assert first_run.media_dir.exists()
