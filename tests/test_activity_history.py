from __future__ import annotations

import json
from io import StringIO

from rich.console import Console

from tweetnook.activity_history import list_runs, read_run, read_run_log
from tweetnook.pipeline import PipelineReporter


def test_pipeline_persists_structured_history_and_plain_log(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TWEETNOOK_ACTIVITY_ORIGIN", "cli")
    state_path = tmp_path / "activity-status.json"
    with PipelineReporter(
        Console(file=StringIO()),
        "tweetnook sync",
        interactive=True,
        state_path=state_path,
    ) as pipeline:
        pipeline.add_step("bookmarks", "Bookmarks", total=2, unit="pages")
        pipeline.start_step("bookmarks", activity="Fetching bookmarks")
        pipeline.update_step("bookmarks", completed=1, counters="12 tweets")
        pipeline.complete_step("bookmarks", "2 pages · 20 tweets")
        pipeline.final_note("sync complete")

    runs = list_runs(tmp_path)
    assert len(runs) == 1
    assert runs[0]["origin"] == "cli"
    assert runs[0]["status"] == "completed"
    run = read_run(tmp_path, runs[0]["run_id"])
    assert run is not None
    assert run["snapshot"]["success"] is True
    assert run["snapshot"]["steps"][0]["summary"] == "2 pages · 20 tweets"
    log = read_run_log(tmp_path, runs[0]["run_id"])
    assert "tweetnook sync: started" in log
    assert "Bookmarks: complete" in log


def test_pipeline_history_redacts_secret_command_arguments(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        ["tweetnook", "sync", "--auth-token", "secret", "--ct0=also-secret"],
    )
    with PipelineReporter(
        Console(file=StringIO()),
        "tweetnook sync",
        interactive=False,
        state_path=tmp_path / "activity-status.json",
    ):
        pass

    metadata = json.loads(
        next((tmp_path / "activity" / "runs").glob("*/metadata.json")).read_text()
    )
    command = " ".join(metadata["command"])
    assert "secret" not in command
    assert command.count("********") == 2


def test_full_volume_releases_reserved_space_before_terminal_snapshot(tmp_path, monkeypatch):
    import errno
    import sqlite3

    import pytest

    import tweetnook.activity_history as history

    state_path = tmp_path / "activity-status.json"
    reporter = PipelineReporter(
        Console(file=StringIO()),
        "tweetnook import enrich",
        interactive=False,
        state_path=state_path,
    )
    original_atomic = history._atomic_json

    with pytest.raises(sqlite3.OperationalError, match="database or disk is full"):
        with reporter as pipeline:
            run_dir = history.get_run_dir(tmp_path, pipeline.run_id)
            assert (run_dir / "final.reserve").stat().st_size >= 256 * 1024

            def full_until_reserve_released(path, payload):
                if (run_dir / "final.reserve").exists():
                    raise OSError(errno.ENOSPC, "No space left on device")
                original_atomic(path, payload)

            monkeypatch.setattr(history, "_atomic_json", full_until_reserve_released)
            pipeline.add_step("media", "Media", total=4, unit="files")
            pipeline.add_step("urls", "Link previews", total=2, unit="URLs")
            pipeline.start_step("media", activity="Downloading media")
            # Exercise SQLite's actual SQLITE_FULL result without filling the host disk.
            connection = sqlite3.connect(tmp_path / "limited.db")
            try:
                connection.execute("PRAGMA max_page_count=2")
                connection.execute("CREATE TABLE payloads (value BLOB)")
                connection.execute("INSERT INTO payloads VALUES (zeroblob(100000))")
            finally:
                connection.close()

    history._TERMINAL_RESULTS.clear()  # Reload solely from disk, as after server restart.
    run = history.read_run(tmp_path, reporter.run_id)
    assert run["status"] == "failed"
    assert run["snapshot"]["success"] is False
    assert "Free or add disk space" in run["summary"]
    assert [step["state"] for step in run["snapshot"]["steps"]] == ["failed", "skipped"]
    assert not (run_dir / "final.reserve").exists()


def test_history_retains_fallback_when_all_writes_fail_and_repairs_after_restart(
    tmp_path, monkeypatch
):
    import errno

    import tweetnook.activity_history as history

    writer = history.ActivityRunWriter(tmp_path / "activity-status.json", "tweetnook import enrich")
    writer.start(pid=2**31 - 1, started_at=1)
    writer.snapshot(
        {
            "running": True,
            "steps": [{"key": "media", "state": "active"}],
            "title": "tweetnook import enrich",
        }
    )

    def full(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    with monkeypatch.context() as full_disk:
        full_disk.setattr(history, "_atomic_json", full)
        result = history.mark_run_process_result(tmp_path, writer.run_id, exit_code=2)
        assert history.read_run(tmp_path, writer.run_id)["status"] == "failed"
        assert "could not be saved" in result["snapshot"]["history_warning"]

    history._TERMINAL_RESULTS.clear()
    repaired = history.read_run(tmp_path, writer.run_id)
    assert repaired["status"] == "interrupted"
    assert repaired["snapshot"]["success"] is None
    assert repaired["snapshot"]["running"] is False
    assert repaired["snapshot"]["steps"][0]["state"] == "failed"
    assert history.list_runs(tmp_path)[0]["status"] == "interrupted"


def test_process_exit_cannot_turn_failed_followup_into_success(tmp_path):
    from tweetnook.activity_history import mark_run_process_result

    with PipelineReporter(
        Console(file=StringIO()),
        "tweetnook sync",
        interactive=False,
        state_path=tmp_path / "activity-status.json",
    ) as pipeline:
        pipeline.add_step("media", "Media", total=2, unit="files")
        pipeline.fail_step("media", "Download failed")
    result = mark_run_process_result(tmp_path, pipeline.run_id, exit_code=0)
    assert result["status"] == "failed"
    assert result["success"] is False
    assert result["snapshot"]["success"] is False
