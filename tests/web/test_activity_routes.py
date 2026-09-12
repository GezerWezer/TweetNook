from __future__ import annotations

import json
import os
from io import StringIO
from types import SimpleNamespace

import pytest
from rich.console import Console

from tweetnook.config import XDGPaths
from tweetnook.pipeline import PipelineReporter
from tweetnook.web.deps import server_state
from tweetnook.web.routes import activity


def _write_snapshot(path, *, pid: int, running: bool = True, title: str = "tweetnook sync"):
    path.write_text(
        json.dumps({"pid": pid, "running": running, "title": title, "steps": []}),
        encoding="utf-8",
    )


def test_activity_status_returns_active_pipeline(tmp_path, make_web_client, monkeypatch) -> None:
    status_file = tmp_path / "activity-status.json"
    _write_snapshot(status_file, pid=os.getpid(), title="tweetnook import enrich")
    server_state["paths"] = SimpleNamespace(activity_status_file=status_file, data_dir=tmp_path)
    monkeypatch.setattr(
        activity,
        "latest_finished_snapshot",
        lambda *_args, **_kwargs: pytest.fail("active status must not scan finished-run history"),
    )
    client = make_web_client(activity.router)

    response = client.get("/api/activity/status")

    assert response.status_code == 200
    assert response.json()["active"] is True
    assert response.json()["snapshot"]["title"] == "tweetnook import enrich"
    assert response.json()["last_snapshot"] is None


def test_activity_status_ignores_finished_or_orphaned_snapshots(tmp_path, make_web_client) -> None:
    status_file = tmp_path / "activity-status.json"
    server_state["paths"] = SimpleNamespace(activity_status_file=status_file, data_dir=tmp_path)
    client = make_web_client(activity.router)

    _write_snapshot(status_file, pid=os.getpid(), running=False)
    finished = client.get("/api/activity/status").json()
    assert finished["active"] is False
    assert finished["snapshot"] is None
    assert finished["last_snapshot"]["running"] is False
    assert finished["schedule"] == {
        "configured": False,
        "enabled": False,
        "relative": "Not configured",
        "date": "Set up scheduling in Settings",
    }

    _write_snapshot(status_file, pid=2**31 - 1)
    orphaned = client.get("/api/activity/status").json()
    assert orphaned["active"] is False
    assert orphaned["last_snapshot"] is None


def test_start_activity_launches_a_production_worker(tmp_path, make_web_client) -> None:
    status_file = tmp_path / "activity-status.json"
    server_state["paths"] = SimpleNamespace(activity_status_file=status_file, data_dir=tmp_path)

    class Supervisor:
        active = False

        @staticmethod
        def start(**kwargs):
            assert kwargs == {
                "kind": "enrich",
                "cli_args": ["import", "enrich"],
                "origin": "web",
                "title": "tweetnook import enrich",
            }
            return {"started": True, "kind": "enrich", "run_id": "run", "pid": 42}

    server_state["job_supervisor"] = Supervisor()
    client = make_web_client(activity.router)

    response = client.post("/api/activity/enrich")

    assert response.status_code == 202
    assert response.json() == {"started": True, "kind": "enrich", "run_id": "run", "pid": 42}


def test_start_enrichment_is_a_controlled_noop_when_nothing_is_pending(
    tmp_path, make_web_client
) -> None:
    status_file = tmp_path / "activity-status.json"
    server_state["paths"] = SimpleNamespace(activity_status_file=status_file, data_dir=tmp_path)
    server_state["store"] = SimpleNamespace(count_incomplete_initial_enrichment=lambda: 0)
    server_state["job_supervisor"] = SimpleNamespace(
        start=lambda **_kwargs: pytest.fail("empty enrichment must not launch")
    )
    client = make_web_client(activity.router)

    response = client.post("/api/activity/enrich")

    assert response.status_code == 202
    assert response.json() == {
        "started": False,
        "kind": "enrich",
        "reason": "no_pending_enrichment",
    }


def test_start_activity_rejects_when_another_pipeline_is_active(tmp_path, make_web_client) -> None:
    status_file = tmp_path / "activity-status.json"
    _write_snapshot(status_file, pid=os.getpid(), title="tweetnook import x-archive")
    server_state["paths"] = SimpleNamespace(activity_status_file=status_file, data_dir=tmp_path)
    client = make_web_client(activity.router)

    response = client.post("/api/activity/import", json={"archive": str(tmp_path)})

    assert response.status_code == 409
    assert "import x-archive is already running" in response.json()["detail"]


def test_start_activity_rejects_unknown_simulation(tmp_path, make_web_client) -> None:
    server_state["paths"] = SimpleNamespace(activity_status_file=tmp_path / "activity.json")
    client = make_web_client(activity.router)

    response = client.post("/api/activity/delete-everything")

    assert response.status_code == 404


def test_stop_activity_signals_the_running_worker(make_web_client) -> None:
    class Supervisor:
        @staticmethod
        def stop():
            return {"stopping": True, "run_id": "run"}

    server_state["job_supervisor"] = Supervisor()
    client = make_web_client(activity.router)

    response = client.post("/api/activity/stop")

    assert response.status_code == 202
    assert response.json() == {"stopping": True, "run_id": "run"}


def test_activity_history_endpoints_return_manual_cli_logs(tmp_path, make_web_client) -> None:
    paths = XDGPaths(
        config_dir=tmp_path / "config", data_dir=tmp_path, cache_dir=tmp_path / "cache"
    )
    server_state["paths"] = paths
    with PipelineReporter(
        Console(file=StringIO()),
        "tweetnook sync",
        interactive=False,
        state_path=paths.activity_status_file,
    ) as pipeline:
        pipeline.final_note("manual sync complete")
    client = make_web_client(activity.router)

    listing = client.get("/api/activity/runs").json()["runs"]
    assert len(listing) == 1
    run_id = listing[0]["run_id"]
    assert listing[0]["origin"] == "cli"
    assert client.get(f"/api/activity/runs/{run_id}").json()["summary"] == "manual sync complete"
    assert "tweetnook sync: started" in client.get(f"/api/activity/runs/{run_id}/log").text
    assert client.get("/api/activity/runs/not-a-run/log").status_code == 404


def test_schedule_endpoints_persist_validated_settings(tmp_path, make_web_client) -> None:
    paths = XDGPaths(
        config_dir=tmp_path / "config", data_dir=tmp_path, cache_dir=tmp_path / "cache"
    )

    class Manager:
        reloaded = False

        def status(self):
            return {
                "configured": self.reloaded,
                "description": "Every 4 hours",
                "relative": "in 4 hours",
                "date": "today",
            }

        def reload(self, *, reset):
            assert reset is True
            self.reloaded = True

    manager = Manager()
    server_state.update({"paths": paths, "schedule_manager": manager})
    client = make_web_client(activity.router)

    response = client.put(
        "/api/activity/schedule",
        json={
            "enabled": True,
            "cadence": "daily",
            "time": "18:45",
            "randomize_time": False,
            "random_offset_min_hours": 0.5,
            "random_offset_max_hours": 2.5,
            "timezone": "UTC",
        },
    )

    assert response.status_code == 200
    assert response.json()["configured"] is True
    saved = paths.config_file.read_text(encoding="utf-8")
    assert "enabled = true" in saved
    assert 'time = "18:45"' in saved
    assert "randomize_time = false" in saved
    assert "random_offset_min_hours = 0.5" in saved
    assert "random_offset_max_hours = 2.5" in saved
    assert 'timezone = "UTC"' in saved
    assert (
        client.put(
            "/api/activity/schedule", json={"cadence": "hours", "every_hours": 0}
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/api/activity/schedule",
            json={
                "randomize_time": True,
                "random_offset_min_hours": 2,
                "random_offset_max_hours": -1,
            },
        ).status_code
        == 422
    )
