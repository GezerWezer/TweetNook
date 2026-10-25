from __future__ import annotations

from io import StringIO
from types import SimpleNamespace

import pytest
from rich.console import Console

from tweetnook.config import AppConfig, CheckConfig, ScheduleConfig, XDGPaths
from tweetnook.job_supervisor import JobConflictError, JobSupervisor
from tweetnook.pipeline import PipelineReporter
from tweetnook.scheduler import ScheduleManager
from tweetnook.web.deps import server_state
from tweetnook.web.jobs import JOBS_BY_KEY, JobOptionsError, job_cli_args
from tweetnook.web.routes import jobs


def _paths(tmp_path) -> XDGPaths:
    return XDGPaths(config_dir=tmp_path / "config", data_dir=tmp_path, cache_dir=tmp_path / "c")


def test_run_options_become_the_same_cli_flags() -> None:
    config = AppConfig()
    threads = JOBS_BY_KEY["threads"]

    assert job_cli_args(threads, {}, config) == ["threads", "expand"]
    # Defaults (taken from config) are left out; changed values become flags.
    assert job_cli_args(
        threads,
        {"refresh": True, "reply_linked_depth": True, "max_linked_depth": 50, "limit": "200"},
        config,
    ) == ["threads", "expand", "--refresh", "--reply-linked-depth", "1", "--limit", "200"]
    assert job_cli_args(JOBS_BY_KEY["tagging"], {"target": "123", "test": True}, config) == [
        "tag",
        "--test",
        "123",
    ]
    imported = job_cli_args(
        JOBS_BY_KEY["import"],
        {"enrich": False, "detail_lookups": 10, "regen": True},
        config,
        staged_archive="/data/archive.zip",
    )
    # Detail lookups don't apply once enrichment is off.
    assert imported == ["import", "x-archive", "--no-enrich", "--regen", "/data/archive.zip"]
    for bad in ({"limit": 0}, {"limit": "many"}, {"refresh": "yes"}, {"nope": True}):
        with pytest.raises(JobOptionsError):
            job_cli_args(threads, bad, config)
    with pytest.raises(JobOptionsError):
        job_cli_args(JOBS_BY_KEY["tagging"], {"model": "--debug"}, config)
    with pytest.raises(JobOptionsError, match="Choose an archive"):
        job_cli_args(JOBS_BY_KEY["import"], {}, config)


def test_jobs_wait_in_a_queue_behind_the_running_one(tmp_path) -> None:
    supervisor = JobSupervisor(_paths(tmp_path), AppConfig())
    started: list[str] = []
    busy = {"value": True}

    def start(*, kind, cli_args, origin, title):
        if busy["value"]:
            raise JobConflictError("busy")
        started.append(kind)
        return {"started": True, "kind": kind, "run_id": f"run-{kind}"}

    supervisor.start = start  # type: ignore[method-assign]
    try:
        first = supervisor.enqueue(
            kind="media", cli_args=["media", "download"], origin="web", title="m"
        )
        supervisor.enqueue(kind="urls", cli_args=["unfurl"], origin="web", title="u")
        with pytest.raises(JobConflictError):
            supervisor.enqueue(kind="urls", cli_args=["unfurl"], origin="web", title="u")

        assert first == {"started": False, "queued": True, "kind": "media", "position": 1}
        assert [item["kind"] for item in supervisor.queue] == ["media", "urls"]
        assert "cli_args" not in supervisor.queue[0]

        busy["value"] = False
        assert supervisor._start_next()["kind"] == "media"
        assert supervisor.dequeue("urls") is True
        assert supervisor._start_next() is None
        assert started == ["media"]
        assert supervisor.queue == []

        # With nothing running, a job starts right away.
        assert supervisor.enqueue(kind="check", cli_args=["check"], origin="web", title="c")[
            "started"
        ]
    finally:
        supervisor.shutdown(timeout=0)


def test_a_deferred_check_shows_as_waiting_and_can_be_skipped(tmp_path) -> None:
    config = AppConfig(
        schedule=ScheduleConfig(timezone="UTC"), check=CheckConfig(enabled=True, cadence="daily")
    )

    class Busy:
        active_kind = "sync"
        active_run_id = "run"

        def start(self, **_kwargs):
            raise JobConflictError("busy")

    manager = ScheduleManager(_paths(tmp_path), config, Busy())
    due = 4 * 3600.0  # 04:00 UTC on day 0, when tonight's check was due
    manager._state["check_next_run_at"] = due
    manager.tick(now=due + 60)

    status = manager.check_status()
    assert status["deferred"] is True
    assert manager.skip_deferred_check(now=due + 60) is True
    assert manager.check_status()["deferred"] is False
    # The next check is tomorrow's, at its usual time.
    assert manager._state["check_next_run_at"] == due + 86_400


def test_jobs_endpoint_lists_state_last_and_next_runs(tmp_path, make_web_client) -> None:
    paths = _paths(tmp_path)
    server_state["paths"] = paths
    server_state["config"] = AppConfig()
    with PipelineReporter(
        Console(file=StringIO()),
        "tweetnook media download",
        interactive=False,
        state_path=paths.activity_status_file,
    ) as pipeline:
        pipeline.final_note("412 files downloaded")
    server_state["job_supervisor"] = SimpleNamespace(
        queue=[{"kind": "urls", "title": "tweetnook unfurl", "origin": "web", "queued_at": 1.0}],
        active=False,
    )
    server_state["schedule_manager"] = SimpleNamespace(
        status=lambda: {"enabled": True, "next_run_at": 5_000.0},
        check_status=lambda: {"on": False, "next_run_at": None, "deferred": False},
        sync_deferred=lambda: False,
    )
    client = make_web_client(jobs.router)

    data = client.get("/api/jobs").json()
    by_key = {job["key"]: job for job in data["jobs"]}

    assert list(by_key) == [
        "sync",
        "check",
        "threads",
        "articles",
        "media",
        "urls",
        "tagging",
        "import",
        "enrich",
    ]
    assert by_key["sync"]["next"] == {"at": 5_000.0, "label": None}
    assert by_key["check"]["next"] == {"at": None, "label": "Off"}
    assert by_key["media"]["state"] == "ok"
    assert by_key["media"]["last"]["summary"] == "412 files downloaded"
    assert by_key["media"]["next"]["label"] == "With sync"
    assert by_key["urls"]["state"] == "queued"
    assert by_key["urls"]["queue"]["position"] == 1
    assert by_key["import"]["next"]["label"] is None
    assert by_key["threads"]["defaults"]["max_linked_depth"] == 50
    assert {option["key"] for option in by_key["media"]["options"]} == {
        "photos_only",
        "retry_failed",
        "limit",
    }
    assert data["runs_this_week"] == 1
    media_limit = next(option for option in by_key["media"]["options"] if option["key"] == "limit")
    assert media_limit["unit"] == "items"
    assert "poster files" in media_limit["help"]
    assert data["archive"] == {"uploaded": False, "filename": None, "size": None}


def test_run_endpoint_validates_and_queues(tmp_path, make_web_client) -> None:
    server_state["paths"] = _paths(tmp_path)
    server_state["config"] = AppConfig()
    calls: list[dict] = []

    class Supervisor:
        def enqueue(self, **kwargs):
            calls.append(kwargs)
            return {"started": False, "queued": True, "kind": kwargs["kind"], "position": 1}

        def dequeue(self, kind):
            return kind == "media"

    server_state["job_supervisor"] = Supervisor()
    server_state["schedule_manager"] = None
    client = make_web_client(jobs.router)

    response = client.post("/api/jobs/media/run", json={"options": {"photos_only": True}})

    assert response.status_code == 202
    assert response.json()["command"] == "tweetnook media download --photos-only"
    assert calls == [
        {
            "kind": "media",
            "cli_args": ["media", "download", "--photos-only"],
            "origin": "web",
            "title": "tweetnook media download",
        }
    ]
    assert client.post("/api/jobs/media/run", json={"options": {"limit": -1}}).status_code == 422
    assert client.post("/api/jobs/import/run", json={}).status_code == 422
    assert client.post("/api/jobs/nope/run", json={}).status_code == 404
    assert client.delete("/api/jobs/media/queue").json() == {"removed": True, "key": "media"}
    assert client.delete("/api/jobs/urls/queue").status_code == 409


@pytest.mark.parametrize("key", ["import", "enrich"])
@pytest.mark.parametrize("failed", [False, True])
def test_setup_jobs_retain_steps_and_errors_after_finishing(
    tmp_path, make_web_client, key, failed
) -> None:
    paths = _paths(tmp_path)
    server_state["paths"] = paths
    server_state["config"] = AppConfig()
    job = JOBS_BY_KEY[key]
    with PipelineReporter(
        Console(file=StringIO()),
        job.title,
        interactive=False,
        state_path=paths.activity_status_file,
    ) as pipeline:
        pipeline.add_step("archive-enrich", "Enrichment", total=12, unit="posts")
        pipeline.start_step("archive-enrich", activity="Looking up posts")
        if failed:
            pipeline.fail_step("archive-enrich", "Replay source unavailable")
        else:
            pipeline.complete_step("archive-enrich", "12 posts enriched")
    # A later job replaces the current activity file, as Setup's media job does.
    with PipelineReporter(
        Console(file=StringIO()),
        "tweetnook media download",
        interactive=False,
        state_path=paths.activity_status_file,
    ):
        pass
    client = make_web_client(jobs.router)

    for _ in range(2):  # New requests recover the run from durable history.
        data = client.get("/api/jobs").json()
        retained = next(item for item in data["jobs"] if item["key"] == key)
        assert retained["state"] == ("failed" if failed else "ok")
        snapshot = retained["last_snapshot"]
        assert snapshot["run_id"] == retained["last"]["run_id"]
        assert snapshot["title"] == job.title
        assert snapshot["success"] is not failed
        assert snapshot["steps"][0]["state"] == ("failed" if failed else "complete")
        if failed:
            assert "Replay source unavailable" in snapshot["steps"][0]["reason"]
