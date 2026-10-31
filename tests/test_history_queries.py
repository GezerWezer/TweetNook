from __future__ import annotations

import json
import os

import pytest

from tweetnook.config import AppConfig
from tweetnook.jobs.activity_history import latest_finished_snapshot, list_runs
from tweetnook.web.jobs import jobs_payload


def _write_run(data_dir, index, title, started_at, *, status="completed"):
    run_id = f"20261030T000000Z-{index:012x}"
    run_dir = data_dir / "activity" / "runs" / run_id
    run_dir.mkdir(parents=True)
    record = {
        "run_id": run_id,
        "title": title,
        "started_at": started_at,
        "completed_at": started_at + 1,
        "status": status,
        "success": status == "completed",
        "pid": os.getpid(),
    }
    (run_dir / "metadata.json").write_text(json.dumps(record))
    if status not in {"starting", "running"}:
        (run_dir / "final.json").write_text(json.dumps(record | {"running": False}))
    return run_id


@pytest.mark.parametrize(
    ("filters", "expected_title"),
    [
        ({"title": "tweetnook import enrich"}, "tweetnook import enrich"),
        ({"titles": {"tweetnook import enrich", "tweetnook sync"}}, "tweetnook sync"),
        ({"title": "tweetnook missing"}, None),
    ],
)
def test_latest_matching_snapshot_searches_all_retained_runs(tmp_path, filters, expected_title):
    _write_run(tmp_path, 0, "tweetnook import enrich", 100, status="failed")
    _write_run(tmp_path, 1, "tweetnook sync", 101)
    for index in range(2, 29):
        _write_run(tmp_path, index, "tweetnook check", 100 + index)
    _write_run(tmp_path, 29, "tweetnook import enrich", 129, status="starting")
    _write_run(tmp_path, 30, "tweetnook sync", 130, status="running")
    result = latest_finished_snapshot(tmp_path, **filters)
    if expected_title is None:
        assert result is None
    else:
        assert result["title"] == expected_title
        assert result["started_at"] == (101 if expected_title == "tweetnook sync" else 100)
    assert len(list_runs(tmp_path, limit=25)) == 25
    assert [run["started_at"] for run in list_runs(tmp_path, limit=2, offset=25)] == [105, 104]


def test_job_catalog_uses_all_retained_history_for_latest_result_and_weekly_count(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("tweetnook.web.jobs.time.time", lambda: 1800000000)
    _write_run(tmp_path, 0, "tweetnook media download", 1799999000, status="failed")
    for index in range(1, 202):
        _write_run(tmp_path, index, "tweetnook check", 1799999000 + index)
    _write_run(tmp_path, 202, "tweetnook media download", 1799999300, status="running")
    _write_run(tmp_path, 203, "tweetnook sync", 1700000000)
    result = jobs_payload(
        config=AppConfig(),
        data_dir=tmp_path,
        active=None,
        queue=[],
        schedule={},
        check={},
        sync_deferred=False,
    )
    media = next(job for job in result["jobs"] if job["key"] == "media")
    assert media["state"] == "failed"
    assert media["last"]["started_at"] == 1799999000
    assert media["last_snapshot"]["success"] is False
    assert result["runs_this_week"] == 203
