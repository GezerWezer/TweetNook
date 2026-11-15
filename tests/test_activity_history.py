from __future__ import annotations

import json
from io import StringIO

import pytest
from rich.console import Console

from tweetnook.jobs.activity_history import (
    list_runs,
    read_full_run_log,
    read_run,
    read_run_log,
)
from tweetnook.jobs.pipeline import PipelineReporter


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

    import tweetnook.jobs.activity_history as history

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

    import tweetnook.jobs.activity_history as history

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
    from tweetnook.jobs.activity_history import mark_run_process_result

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


def _killed_mid_step(tmp_path, *, console_log: bool):
    import tweetnook.jobs.activity_history as history

    writer = history.ActivityRunWriter(tmp_path / "activity-status.json", "tweetnook sync")
    writer.start(pid=2**31 - 1, started_at=1)
    if console_log:
        (writer.run_dir / "console.log").write_text("2026-01-01T00:00:00Z earlier line\n")
    writer.snapshot(
        {
            "running": True,
            "active_step": "urls",
            "steps": [
                {
                    "key": "urls",
                    "title": "Link previews",
                    "state": "active",
                    "activity": "Fetching media.example.net",
                }
            ],
        }
    )
    return history, writer


def test_a_killed_worker_leaves_its_signal_and_last_activity_in_the_log(tmp_path):
    history, writer = _killed_mid_step(tmp_path, console_log=True)

    history.mark_run_process_result(tmp_path, writer.run_id, exit_code=-15)

    lines = (writer.run_dir / "console.log").read_text().splitlines()
    assert lines[0] == "2026-01-01T00:00:00Z earlier line"
    assert len(lines) == 2
    assert lines[1].split(" ", 1)[1] == (
        "Worker ended by SIGTERM (exit status -15) before recording completion"
        " · the signal didn't come from TweetNook"
        " · last step: Link previews · Fetching media.example.net"
    )
    assert read_run_log(tmp_path, writer.run_id).endswith("Fetching media.example.net")


def test_a_stop_that_needed_a_signal_is_logged_as_a_stop(tmp_path):
    history, writer = _killed_mid_step(tmp_path, console_log=False)

    history.mark_run_process_result(tmp_path, writer.run_id, exit_code=-15, stopped=True)

    last = (writer.run_dir / "output.log").read_text().splitlines()[-1]
    assert last.split(" ", 1)[1] == (
        "Worker stopped by request · SIGTERM (exit status -15)"
        " · last step: Link previews · Fetching media.example.net"
    )


def test_a_worker_that_recorded_its_end_gets_no_extra_log_line(tmp_path):
    history, writer = _killed_mid_step(tmp_path, console_log=True)
    writer.snapshot({"running": False, "steps": [], "summary": "done"})

    history.mark_run_process_result(tmp_path, writer.run_id, exit_code=0)

    assert (writer.run_dir / "console.log").read_text() == "2026-01-01T00:00:00Z earlier line\n"


def test_the_last_line_says_how_long_the_worker_had_been_quiet(tmp_path, monkeypatch):
    history, writer = _killed_mid_step(tmp_path, console_log=True)
    snapshot = json.loads((writer.run_dir / "snapshot.json").read_text())
    writer.snapshot({**snapshot, "updated_at": 1_000.0})
    monkeypatch.setattr(history.time, "time", lambda: 1_000.0 + 4 * 60 + 10)

    history.mark_run_process_result(tmp_path, writer.run_id, exit_code=-9)

    last = (writer.run_dir / "console.log").read_text().splitlines()[-1]
    assert last.endswith(
        " · last step: Link previews · Fetching media.example.net · last update 4m 10s earlier"
    )


def test_a_worker_gone_without_a_supervisor_still_ends_its_log(tmp_path):
    history, writer = _killed_mid_step(tmp_path, console_log=True)

    # The pid recorded by _killed_mid_step belongs to no process.
    assert history.read_run(tmp_path, writer.run_id)["status"] == "interrupted"
    history._TERMINAL_RESULTS.clear()
    history.read_run(tmp_path, writer.run_id)

    lines = (writer.run_dir / "console.log").read_text().splitlines()
    assert len(lines) == 2
    assert lines[1].split(" ", 1)[1] == (
        "Worker is gone and its exit status is unknown; completion was not recorded"
        " · last step: Link previews · Fetching media.example.net"
    )


def _run(tmp_path, body, *, interactive=False):
    output = StringIO()
    with PipelineReporter(
        Console(file=output, width=400),
        "tweetnook sync",
        interactive=interactive,
        state_path=tmp_path / "activity-status.json",
    ) as pipeline:
        body(pipeline)
    return pipeline.run_id, output.getvalue()


def test_the_run_log_starts_with_the_version_and_command(tmp_path, monkeypatch):
    import tweetnook

    monkeypatch.setattr(
        "sys.argv", ["/venv/lib/tweetnook/__main__.py", "sync", "--auth-token", "secret", "likes"]
    )

    run_id, _ = _run(tmp_path, lambda pipeline: None)

    lines = [line.split(" ", 1)[1] for line in read_run_log(tmp_path, run_id).splitlines()]
    assert lines[0].startswith(f"tweetnook sync: started · TweetNook {tweetnook.__version__} · ")
    assert " · cli · pid " in lines[0]
    assert lines[1] == "tweetnook sync: command · tweetnook sync --auth-token '********' likes"


def test_an_x_error_puts_xs_answer_in_the_run_log_and_its_traceback_in_the_full_log(tmp_path):
    from tweetnook.core.exceptions import FeatureFlagDriftError

    def fetch_bookmarks():
        try:
            raise ConnectionError("socket detail")
        except ConnectionError as exc:
            raise FeatureFlagDriftError(
                "Twitter/X returned 400.",
                status_code=400,
                operation="Bookmarks",
                response_excerpt='{"errors":[{"message":"rweb_lists cannot be null"}]}',
            ) from exc

    def body(pipeline):
        pipeline.add_step("sync", "Bookmarks", total=1, unit="pages")
        pipeline.start_step("sync", activity="Fetching bookmarks")
        fetch_bookmarks()

    with pytest.raises(FeatureFlagDriftError):
        _run(tmp_path, body)
    (run_id,) = [run["run_id"] for run in list_runs(tmp_path)]

    log = read_run_log(tmp_path, run_id)
    assert (
        'X response: detail · HTTP 400 · Bookmarks · {"errors":[{"message":"rweb_lists cannot'
        in log
    )
    assert "Traceback" not in log

    full = read_full_run_log(tmp_path, run_id)
    assert "Traceback for FeatureFlagDriftError:" in full
    assert "in fetch_bookmarks" in full
    assert "ConnectionError: socket detail" in full
    assert "direct cause of the following exception" in full
    # The traceback comes before the failure that ends the log.
    assert full.index("Traceback for") < full.index("tweetnook sync: failed")


def test_the_full_log_keeps_every_occurrence_the_run_log_samples(tmp_path):
    def body(pipeline):
        for index in range(1, 31):
            pipeline.issue(f"URL host{index}.example: ReadTimeout", dedupe_key="urls:failure")

    run_id, _ = _run(tmp_path, body)

    sampled = [line for line in read_run_log(tmp_path, run_id).splitlines() if "WARNING" in line]
    assert len(sampled) == 5  # occurrences 1, 2, 3, 10 and 25
    full = [line for line in read_full_run_log(tmp_path, run_id).splitlines() if "WARNING" in line]
    assert [line.split("WARNING: URL ", 1)[1].split(".", 1)[0] for line in full] == [
        f"host{index}" for index in range(1, 31)
    ]


def test_the_full_log_stops_recording_an_issue_after_its_limit(tmp_path, monkeypatch):
    import tweetnook.jobs.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "FULL_LOG_ISSUE_LIMIT", 5)

    def body(pipeline):
        for index in range(1, 9):
            pipeline.issue(f"Media {index}: HTTP 403", dedupe_key="media:failure")

    run_id, _ = _run(tmp_path, body)

    full = [
        line.split(" ", 1)[1]
        for line in read_full_run_log(tmp_path, run_id).splitlines()
        if "WARNING" in line
    ]
    assert full == [
        "WARNING: Media 1: HTTP 403",
        "WARNING: Media 2: HTTP 403 · occurrences=2",
        "WARNING: Media 3: HTTP 403 · occurrences=3",
        "WARNING: Media 4: HTTP 403 · occurrences=4",
        "WARNING: Media 5: HTTP 403 · occurrences=5",
        "WARNING: later occurrences are only counted · Media 6: HTTP 403 · occurrences=6",
    ]


def test_the_full_log_merges_by_time_and_keeps_a_stack_dump_with_its_line(tmp_path):
    import tweetnook.jobs.activity_history as history

    writer = history.ActivityRunWriter(tmp_path / "activity-status.json", "tweetnook sync")
    (writer.run_dir / "console.log").write_text(
        "2026-01-01T00:00:01Z first\n"
        "2026-01-01T00:00:03Z Fatal Python error: Terminated\n"
        'File "fetch.py", line 9 in stuck\n'
        "2026-01-01T00:00:05Z last\n"
    )
    events = [
        {"timestamp": 1767225602.0, "line": "WARNING: between first and dump", "full": True},
        {"timestamp": 1767225603.5, "line": "not full, already in the console log"},
        {"timestamp": 1767225604.0, "line": "WARNING: after the dump", "full": True},
    ]
    (writer.run_dir / "events.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events) + '{"partial'
    )

    assert history.read_full_run_log(tmp_path, writer.run_id).splitlines() == [
        "2026-01-01T00:00:01Z first",
        "2026-01-01T00:00:02Z WARNING: between first and dump",
        "2026-01-01T00:00:03Z Fatal Python error: Terminated",
        'File "fetch.py", line 9 in stuck',
        "2026-01-01T00:00:04Z WARNING: after the dump",
        "2026-01-01T00:00:05Z last",
    ]
