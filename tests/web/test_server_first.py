from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tweetnook.activity_history import mark_run_process_result, new_run_id, reserve_run
from tweetnook.config import AppConfig, ensure_paths, resolve_paths
from tweetnook.job_supervisor import JobConflictError
from tweetnook.storage import open_archive_store
from tweetnook.web import server
from tweetnook.web.deps import SESSION_COOKIE, server_state
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

    def finish(self, code=0, *, stopped=False, steps=None):
        run_id = self.active_run_id
        self.active = False
        self.active_run_id = None
        self.active_kind = None
        if code == 0:
            run_dir = self.paths.activity_runs_dir / run_id
            metadata = json.loads((run_dir / "metadata.json").read_text())
            (run_dir / "final.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "title": metadata["title"],
                        "started_at": metadata["started_at"],
                        "running": False,
                        "success": True,
                        "completed_at": time.time(),
                        "steps": steps or [],
                    }
                )
            )
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
        root = client.get("/")
        for path in ("/post/123", "/post/123/quotes"):
            detail = client.get(path)
            assert detail.status_code == 200
            assert detail.content == root.content
            assert "text/html" in detail.headers["content-type"]
        assert client.get("/api/activity/status").status_code == 200
        assert client.get("/api/tweets").json()["detail"]["code"] == "setup_required"
        assert client.get("/post/not-a-number").json()["detail"]["code"] == "setup_required"
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
        assert response.json() == {"completed": True, "reload": False}
        assert "HttpOnly" in response.headers["set-cookie"]
        assert server_state["schedule_manager"] is not None
        assert client.get("/api/setup").status_code == 200
        assert client.get("/api/tweets").status_code == 200
        client.cookies.clear()
        assert client.get("/api/setup").status_code == 401
        assert client.get("/api/tweets", auth=("any", "test-secret")).status_code == 200
        before = first_run.setup_state_file.read_bytes()
        assert client.get("/api/setup", auth=("any", "test-secret")).json()["completed"]
        assert first_run.setup_state_file.read_bytes() == before


def test_setup_can_finish_without_a_password_and_later_remove_one(first_run):
    with TestClient(server.app) as client:
        assert client.get("/api/setup").json()["web"] == {"password_configured": False}
        assert client.post("/api/setup/archive/empty").status_code == 200
        both = {"skip_auth": True, "no_password": True, "password": "x", "confirm_password": "x"}
        assert client.post("/api/setup/complete", json=both).status_code == 422

        response = client.post("/api/setup/complete", json={"skip_auth": True, "no_password": True})

        assert response.status_code == 200
        assert response.json() == {"completed": True, "reload": False}
        assert "set-cookie" not in response.headers
        assert client.get("/api/tweets").status_code == 200
        assert client.get("/api/setup").json()["web"]["password_configured"] is False

        # Rerunning Setup can add a password, then remove it again.
        added = {"password": "test-secret", "confirm_password": "test-secret"}
        assert client.post("/api/setup/complete", json=added).json()["reload"] is False
        assert client.get("/api/tweets").status_code == 200
        client.cookies.clear()
        assert client.get("/api/tweets").status_code == 401
        removed = client.post(
            "/api/setup/complete", json={"no_password": True}, auth=("any", "test-secret")
        )
        assert removed.json() == {"completed": True, "reload": False}
        assert client.get("/api/tweets").status_code == 200
        assert "password_hash" not in first_run.config_file.read_text(encoding="utf-8")


@pytest.mark.parametrize("endpoint", ["/api/setup/complete", "/api/setup/password"])
def test_setup_password_change_keeps_only_the_current_browser_authenticated(first_run, endpoint):
    with TestClient(server.app, base_url="https://testserver") as client:
        submit = client.put if endpoint.endswith("/password") else client.post
        assert client.post("/api/setup/archive/empty").status_code == 200
        body = {"skip_auth": True, "password": "first", "confirm_password": "first"}
        assert submit(endpoint, json=body).status_code == 200
        previous_session = client.cookies[SESSION_COOKIE]

        body.update(password="second", confirm_password="second")
        changed = submit(endpoint, json=body)
        assert changed.json() == {"completed": True, "reload": False}
        assert "; Secure" in changed.headers["set-cookie"]
        assert client.cookies[SESSION_COOKIE] != previous_session
        assert client.get("/api/setup").status_code == 200
        assert client.get("/api/tweets").status_code == 200

        # A failed change must not replace the session or weaken authentication.
        body.update(password="third", confirm_password="mismatch")
        failed = submit(endpoint, json=body)
        assert failed.status_code == 422
        assert "set-cookie" not in failed.headers
        assert client.get("/api/tweets").status_code == 200
        client.cookies.clear()
        client.cookies.set(SESSION_COOKIE, previous_session)
        assert client.get("/api/setup").status_code == 401
        assert submit(endpoint, json=body).status_code == 401


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


def test_attached_archive_warms_at_start_and_after_each_job(first_run, monkeypatch):
    events = []

    class Warmer:
        def __init__(self, _store):
            events.append("init")

        def request(self):
            events.append("request")

        def stop(self):
            events.append("stop")

    monkeypatch.setattr(server, "ArchiveWarmer", Warmer)
    open_archive_store(first_run, create=True).close()
    with TestClient(server.app):
        assert events == ["init", "request"]
        supervisor = server_state["job_supervisor"]
        supervisor.start(kind="sync", title="Sync", cli_args=["sync"], origin="web")
        supervisor.finish()
        assert events == ["init", "request", "request"]

    assert events == ["init", "request", "request", "stop"]


def test_attached_archive_keeps_its_daily_analytics_report_after_jobs(first_run, monkeypatch):
    events = []

    class Refresher:
        def __init__(self, _store, refresh_time):
            events.append(f"init {refresh_time}")

        def start(self):
            events.append("start")

        def stop(self):
            events.append("stop")

    monkeypatch.setattr(server, "DailyStatsRefresher", Refresher)
    monkeypatch.setattr(server.web_stats_cache, "clear", lambda: events.append("clear"))
    monkeypatch.setattr(
        server.web_stats_cache, "start_refresh", lambda _store: events.append("refresh")
    )
    open_archive_store(first_run, create=True).close()
    with TestClient(server.app):
        assert events == ["clear", "init 05:00", "start"]
        supervisor = server_state["job_supervisor"]
        supervisor.start(kind="sync", title="Sync", cli_args=["sync"], origin="web")
        supervisor.finish()
        assert events == ["clear", "init 05:00", "start"]

    assert events[3] == "stop"


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
    assert jobs.calls[0]["cli_args"][-1] == "--defer-followup"
    server_state["config"].auth.auth_token = "candidate"
    server_state["config"].auth.ct0 = "candidate-csrf"
    if auth_first:
        coord.auth_was_verified()
        assert len(jobs.calls) == 1
    coord.paths.database_path.touch()
    jobs.finish()
    # The import downloaded media as its last step, so no separate media job follows.
    assert coord.status()["archive"]["media_state"] == "complete"
    if not auth_first:
        assert len(jobs.calls) == 1
        coord.auth_was_verified()
    assert len(jobs.calls) == 1  # Connecting must not snapshot default collections.
    coord.complete_walkthrough()
    coord.reconcile()
    assert jobs.calls[-1]["cli_args"] == ["import", "enrich"]
    for _ in range(3):
        coord.reconcile()
    assert len(jobs.calls) == 2
    jobs.finish(1)
    coord.reconcile()
    assert len(jobs.calls) == 2
    assert coord.notices.list()


@pytest.mark.parametrize("empty_field", ["auth_token", "ct0"])
def test_replay_import_waits_for_both_cookies_then_starts_first_sync(coordinator, empty_field):
    coord, jobs, _ = coordinator
    config = server_state["config"]
    config.replay.url = "http://source.test"
    config.auth.auth_token = "anything"
    config.auth.ct0 = "anything"
    coord.begin("import")
    coord.paths.database_path.touch()
    jobs.finish()
    assert coord.state.auth_verified is True
    assert len(jobs.calls) == 1

    setattr(config.auth, empty_field, "")
    coord.reconcile()
    assert coord.state.auth_verified is False
    coord.complete_walkthrough(skip_auth=True)
    assert coord.status()["archive"]["followup_state"] == "waiting_for_auth"
    assert coord.pending_jobs() == []
    assert len(jobs.calls) == 1

    setattr(config.auth, empty_field, "anything")
    coord.reconcile()
    assert coord.state.auth_verified is True
    assert coord.state.auth_skipped is False
    assert jobs.calls[-1]["cli_args"] == ["import", "enrich"]
    coord.reconcile()
    assert len(jobs.calls) == 2


def test_restart_completed_import_waits_for_verified_auth(coordinator):
    coord, jobs, _ = coordinator
    coord.begin("import")
    jobs.callbacks.clear()
    coord.paths.database_path.touch()
    jobs.finish()
    coord.state.auth_verified = True
    coord.state.completed = True
    coord.save()
    server_state["config"].auth.auth_token = "candidate"
    server_state["config"].auth.ct0 = "candidate-csrf"
    restarted = SetupCoordinator(
        coord.paths, server_state, coord.attach, lambda: None, coord.notices
    )
    restarted.reconcile()
    assert jobs.calls[-1]["kind"] == "enrich"


def test_enrichment_exit_waits_for_monitor_before_raising_notice(coordinator):
    coord, jobs, _ = coordinator
    jobs.start(kind="enrich", cli_args=["import", "enrich"], origin="web", title="Enrich")
    coord.state.auto_enrich_run_id = jobs.active_run_id
    jobs.active = False  # Process exited, but monitor has not recorded its result.
    coord.reconcile()
    assert coord.notices.list() == []
    finished_id = jobs.finish()
    assert coord.state.auto_enrich_run_id == finished_id
    assert coord.notices.list() == []
    coord.reconcile()
    assert coord.notices.list() == []


def test_successful_enrichment_dismisses_prior_spurious_notice(coordinator):
    coord, jobs, _ = coordinator
    jobs.start(kind="enrich", cli_args=["import", "enrich"], origin="web", title="Enrich")
    coord.state.auto_enrich_run_id = jobs.active_run_id
    coord.notices.add(f"enrich-{jobs.active_run_id}", "Enrichment needs attention", "Stale warning")
    finished_id = jobs.finish()
    assert coord.state.auto_enrich_run_id == finished_id
    assert all(notice["dismissed"] for notice in coord.notices.list())


def test_failed_followups_remain_visible_when_imported_details_are_complete(coordinator):
    coord, jobs, store = coordinator
    server_state["store"] = store
    store.count_incomplete_initial_enrichment = lambda: 0
    coord.state.initialization_mode = "import"
    coord.state.initialization_result = "complete"
    coord.state.completed = True
    jobs.start(
        kind="enrich", cli_args=["import", "enrich"], origin="web", title="tweetnook import enrich"
    )
    coord.state.auto_enrich_run_id = jobs.active_run_id
    coord.save()
    failed_id = jobs.finish(2)

    status = coord.status()["archive"]
    assert status["browsable"] is True
    assert status["details_complete"] is True
    assert status["enriched"] is False
    assert status["enrichment_state"] == "failed"
    assert status["followup_state"] == "failed"
    assert status["all_followups_complete"] is False
    assert status["warnings"]
    restarted = SetupCoordinator(
        coord.paths, server_state, coord.attach, lambda: None, coord.notices
    )
    assert restarted.state.auto_enrich_run_id == failed_id
    assert restarted.status()["archive"]["followup_state"] == "failed"


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


def test_zero_pending_details_still_waits_for_confirmed_first_sync(coordinator):
    coord, jobs, store = coordinator
    store.count_incomplete_initial_enrichment = lambda: 0
    coord.begin("import")
    coord.paths.database_path.touch()
    jobs.finish()
    assert coord.state.auto_enrich_requested
    assert coord.status()["archive"]["enrichment_state"] == "complete"
    server_state["config"].auth.auth_token = "candidate"
    server_state["config"].auth.ct0 = "candidate-csrf"
    coord.auth_was_verified()
    assert len(jobs.calls) == 1
    coord.complete_walkthrough()
    coord.reconcile()
    assert jobs.calls[-1]["kind"] == "enrich"


def test_migration_never_arms_enrichment(coordinator):
    coord, jobs, _ = coordinator
    coord.begin("migration")
    coord.paths.database_path.touch()
    jobs.finish()
    assert server_state["store"] is not None
    assert not coord.state.auto_enrich_requested
    assert "media_requested" not in coord.state.model_dump()
    assert coord.status()["archive"]["media_state"] == "not_applicable"
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
    # The worker is a separate process, so keep its enrichment off the real FxTwitter API.
    first_run.config_file.write_text('[fxtwitter]\nbase_url = "http://127.0.0.1:9"\n')
    with TestClient(server.app) as client:
        assert client.put("/api/setup/archive", content=archive.read_bytes()).status_code == 200
        result = client.post("/api/setup/archive/import")
        assert result.status_code == 202
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            status = client.get("/api/setup").json()
            if status["archive"]["ready"] and status["archive"]["media_state"] == "complete":
                break
            time.sleep(0.05)
        assert status["archive"]["ready"], status
        assert status["archive"]["imported"]
        # FxTwitter couldn't answer, so the tweet waits for the connected first sync.
        assert status["archive"]["enrichment_state"] == "waiting_for_setup"
        assert status["archive"]["pending_enrichment"] == 1
        assert status["archive"]["media_state"] == "complete", status
        assert status["archive"]["followup_state"] == "waiting_for_setup"
        # Import, enrichment and media are one run.
        (run_dir,) = first_run.activity_runs_dir.iterdir()
        steps = json.loads((run_dir / "final.json").read_text())["steps"]
        keys = [step["key"] for step in steps]
        assert keys[-3:] == ["archive-auth", "archive-enrich", "media"]
        assert steps[-1]["state"] in {"complete", "skipped"}  # Skipped: files came in the ZIP.
        assert first_run.media_dir.exists()


def test_first_sync_uses_final_collection_choice(coordinator):
    coord, jobs, _ = coordinator
    coord.begin("import")
    coord.paths.database_path.touch()
    jobs.finish()
    server_state["config"].auth.auth_token = "synthetic"
    server_state["config"].auth.ct0 = "synthetic"
    coord.auth_was_verified()
    assert len(jobs.calls) == 1
    server_state["config"].sync.collections = ["bookmarks"]
    coord.reconcile()
    assert len(jobs.calls) == 1
    coord.complete_walkthrough()
    coord.reconcile()
    assert len(jobs.calls) == 2
    assert server_state["config"].sync.collections == ["bookmarks"]
    assert jobs.calls[-1]["kind"] == "enrich"
    assert not server_state["config"].schedule.enabled


def test_real_import_media_disk_full_keeps_archive_ready_without_retry(
    first_run, tmp_path, monkeypatch
):
    import errno

    from typer.testing import CliRunner

    import tweetnook.cli as cli
    from tests.test_archive_import import _write_archive_dir, _write_archive_zip
    from tweetnook.activity_history import read_run
    from tweetnook.resource_failure import STORAGE_RECOVERY

    source = _write_archive_dir(tmp_path / "source", media_kind="video")
    archive = _write_archive_zip(source, tmp_path / "fixture.zip")
    downloads = []

    async def full(_client, *, url, destination):
        downloads.append(url)
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr("tweetnook.media._download_file", full)
    with TestClient(server.app) as client:
        coord = server_state["setup_coordinator"]
        jobs = server_state["job_supervisor"]
        config = server_state["config"]
        config.fxtwitter.enabled = False
        monkeypatch.setattr(cli, "load_config", lambda: (config, first_run))
        assert client.put("/api/setup/archive", content=archive.read_bytes()).status_code == 200
        assert client.post("/api/setup/archive/import").status_code == 202
        run_id = jobs.active_run_id
        with monkeypatch.context() as worker:
            worker.setenv("TWEETNOOK_ACTIVITY_RUN_ID", run_id)
            worker.setenv("TWEETNOOK_ACTIVITY_ORIGIN", "web")
            result = CliRunner().invoke(cli.app, jobs.calls[0]["cli_args"])
        assert result.exit_code == 2, result.output
        jobs.finish(result.exit_code)
        status = client.get("/api/setup").json()["archive"]
        assert status["browsable"] and status["imported"]
        assert status["initialization_state"] == "complete"
        assert status["media_state"] == "failed"
        assert status["followup_state"] == "failed"
        assert status["warnings"] == [STORAGE_RECOVERY]
        saved = read_run(first_run.data_dir, run_id)
        assert saved["status"] == "failed"
        media = next(step for step in saved["snapshot"]["steps"] if step["key"] == "media")
        assert media["state"] == "failed"
        assert STORAGE_RECOVERY in media["reason"]
        assert server_state["store"].list_media_rows(states={"pending"})
        assert len(downloads) == 1
        for _ in range(3):
            coord.reconcile()
        assert len(jobs.calls) == 1
        assert coord.pending_jobs() == []


def _stop_import_during(
    jobs,
    paths,
    step_key,
    *,
    code=130,
    stopped=True,
    summary="Stopped by user",
    enrich_skipped=False,
):
    """Finish the active import as a worker stopped in ``step_key`` would leave it."""

    order = ["archive-inspect", "archive-source", "archive-media", "archive-enrich", "media"]
    stopped_at = order.index(step_key)
    steps = [
        {
            "key": key,
            "state": "complete" if i < stopped_at else "failed" if i == stopped_at else "skipped",
        }
        for i, key in enumerate(order)
    ]
    if enrich_skipped:
        next(step for step in steps if step["key"] == "archive-enrich")["state"] = "skipped"
    filename = "snapshot.json" if stopped else "final.json"
    (paths.activity_runs_dir / jobs.active_run_id / filename).write_text(
        json.dumps({"running": False, "success": False, "summary": summary, "steps": steps})
    )
    jobs.finish(code, stopped=stopped)


@pytest.mark.parametrize("step_key", ["archive-enrich", "media"])
def test_stopping_after_the_local_import_waits_for_manual_resume(coordinator, step_key):
    coord, jobs, _ = coordinator
    coord.begin("import")
    coord.paths.database_path.touch()
    _stop_import_during(jobs, coord.paths, step_key)
    assert coord.state.initialization_result == "complete"
    assert server_state["store"] is not None
    assert len(jobs.calls) == 1
    assert not coord.state.auto_enrich_requested
    status = coord.status()["archive"]
    assert status["media_state"] == ("stopped" if step_key == "media" else "not_started")
    assert status["followup_state"] == "failed"
    assert status["warnings"][0].startswith("Stopped by user")


def test_stopping_the_local_import_needs_setup_attention(coordinator):
    coord, jobs, _ = coordinator
    coord.begin("import")
    coord.paths.database_path.touch()
    _stop_import_during(jobs, coord.paths, "archive-source")
    assert coord.state.initialization_result == "failed"
    assert len(jobs.calls) == 1
    assert coord.pending_jobs() == []


@pytest.mark.parametrize("connected", [False, True])
@pytest.mark.parametrize("enrich_skipped", [False, True])
def test_disk_full_import_does_not_start_media_or_first_sync(
    coordinator, connected, enrich_skipped
):
    from tweetnook.resource_failure import STORAGE_RECOVERY

    coord, jobs, _ = coordinator
    coord.begin("import")
    coord.paths.database_path.touch()
    if connected:
        server_state["config"].auth.auth_token = "synthetic"
        server_state["config"].auth.ct0 = "synthetic"
        coord.auth_was_verified()
        coord.complete_walkthrough()
    import_id = jobs.active_run_id
    _stop_import_during(
        jobs,
        coord.paths,
        "media",
        code=2,
        stopped=False,
        summary=STORAGE_RECOVERY,
        enrich_skipped=enrich_skipped,
    )
    assert len(jobs.calls) == 1
    assert not jobs.active
    assert coord.state.initialization_result == "complete"
    assert not coord.state.auto_enrich_requested
    status = coord.status()["archive"]
    assert status["browsable"]
    assert status["media_state"] == "failed"
    assert status["followup_state"] == "failed"
    assert status["warnings"] == [STORAGE_RECOVERY]
    assert [notice["run_id"] for notice in coord.notices.list()] == [import_id]
    # Neither reconnecting, finishing Setup nor restarting should retry the failed work.
    server_state["config"].auth.auth_token = "synthetic"
    server_state["config"].auth.ct0 = "synthetic"
    coord.auth_was_verified()
    coord.complete_walkthrough()
    restarted = SetupCoordinator(
        coord.paths, server_state, coord.attach, lambda: None, coord.notices
    )
    for _ in range(3):
        restarted.reconcile()
    assert len(jobs.calls) == 1
    assert restarted.status()["archive"]["media_state"] == "failed"
    assert restarted.pending_jobs() == []


@pytest.mark.parametrize("recorded", [False, True])
def test_old_saved_media_intent_is_ignored_after_failed_import(coordinator, recorded):
    coord, jobs, _ = coordinator
    coord.begin("import")
    coord.paths.database_path.touch()
    if not recorded:
        jobs.callbacks.clear()  # Server exited before its completion callback recorded the result.
    _stop_import_during(jobs, coord.paths, "media", code=2, stopped=False)
    saved = coord.state.model_dump()
    saved.update(media_requested=True, media_run_id=None, auto_enrich_requested=True)
    coord.paths.setup_state_file.write_text(json.dumps(saved))
    restarted = SetupCoordinator(
        coord.paths, server_state, coord.attach, lambda: None, coord.notices
    )
    server_state["config"].auth.auth_token = "synthetic"
    server_state["config"].auth.ct0 = "synthetic"
    restarted.auth_was_verified()
    restarted.complete_walkthrough()
    restarted.reconcile()
    assert len(jobs.calls) == 1
    assert not restarted.state.auto_enrich_requested
    assert restarted.status()["archive"]["media_state"] == "failed"
    assert restarted.pending_jobs() == []


def test_manual_sync_recovers_after_failed_import_media(coordinator):
    coord, jobs, _ = coordinator
    coord.begin("import")
    coord.paths.database_path.touch()
    _stop_import_during(jobs, coord.paths, "media", code=2, stopped=False)
    jobs.start(kind="sync", cli_args=["sync"], origin="web", title="tweetnook sync")
    assert coord.status()["archive"]["followup_state"] == "running"
    jobs.finish(steps=[{"key": "media", "state": "complete"}])
    status = coord.status()["archive"]
    assert status["followup_state"] == "complete"
    assert status["media_state"] == "complete"
    assert status["warnings"] == []
    assert all(notice["dismissed"] for notice in coord.notices.list())
    assert len(jobs.calls) == 2


def test_jobs_list_shows_what_setup_starts_next(coordinator, monkeypatch):
    from tweetnook.web.jobs import jobs_payload

    coord, jobs, _ = coordinator
    coord.begin("import")
    assert coord.pending_jobs() == []
    coord.paths.database_path.touch()
    server_state["config"].auth.auth_token = "synthetic"
    server_state["config"].auth.ct0 = "synthetic"
    coord.auth_was_verified()
    coord.complete_walkthrough()

    def busy(**kwargs):
        raise JobConflictError("Another command is running.")

    monkeypatch.setattr(jobs, "start", busy)
    jobs.finish()
    assert coord.status()["archive"]["media_state"] == "complete"
    assert coord.pending_jobs() == ["enrich"]
    payload = jobs_payload(
        config=AppConfig(),
        data_dir=None,
        active=None,
        queue=[],
        schedule={},
        check={},
        sync_deferred=False,
        setup_queue=coord.pending_jobs(),
    )
    by_key = {job["key"]: job for job in payload["jobs"]}
    assert by_key["enrich"]["state"] == "queued"
    assert by_key["enrich"]["queue"]["removable"] is False
    assert by_key["media"]["state"] != "queued"
