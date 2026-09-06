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
