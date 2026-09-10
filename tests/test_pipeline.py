from __future__ import annotations

import json
import random
import time
from io import StringIO

import pytest
from rich.console import Console
from rich.text import Text

from tweetnook.exceptions import ProcessLockError
from tweetnook.pipeline import (
    SPINNER_FRAMES,
    TWITTER_BLUE,
    PipelineReporter,
    current_pipeline,
)


def _console(output: StringIO, *, width: int = 160) -> Console:
    return Console(
        file=output,
        force_terminal=False,
        color_system=None,
        width=width,
    )


def test_reporter_chooses_one_spinner_for_the_command_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chosen: list[tuple[str, ...]] = []

    def choose(names: tuple[str, ...]) -> str:
        chosen.append(names)
        return "cascade"

    monkeypatch.setattr(random, "choice", choose)
    reporter = PipelineReporter(_console(StringIO()), "sync", interactive=False)

    with reporter:
        reporter.add_step("bookmarks", "Bookmarks", total=10, unit="tweets")
        reporter.start_step("bookmarks", activity="Fetching bookmark timeline page 1")
        reporter.complete_step("bookmarks", "10 tweets stored")
        reporter.add_step("threads", "Threads", total=2, unit="tweets")
        reporter.start_step("threads", activity="Expanding thread for tweet 123")
        reporter.complete_step("threads", "2 threads expanded")

    assert chosen == [tuple(SPINNER_FRAMES)]
    assert reporter.spinner_name == "cascade"


def test_finished_pipeline_keeps_and_skips_planned_steps_with_no_work() -> None:
    reporter = PipelineReporter(_console(StringIO()), "sync", interactive=False)

    with reporter:
        reporter.add_step("bookmarks", "Bookmarks", total=1, unit="pages")
        reporter.start_step("bookmarks", activity="Fetching bookmark timeline page 1")
        reporter.complete_step("bookmarks", "1 page fetched")
        reporter.add_step("media", "Media", total=1, unit="files")

    assert [step.key for step in reporter.steps] == ["bookmarks", "media"]
    assert reporter._step_by_key["media"].state == "skipped"
    assert reporter._step_by_key["media"].summary == (
        "skipped due to no work being found for this run"
    )


def test_render_uses_determinate_twitter_blue_progress_with_useful_metadata() -> None:
    output = StringIO()
    console = _console(output)
    reporter = PipelineReporter(console, "tweetnook sync", interactive=False)
    reporter.add_step(
        "threads",
        "Threads",
        total=100,
        unit="tweets",
        detail="23 expanded · 1 unavailable · 0 retrying",
        rate_unit="tweets/s",
    )
    reporter.start_step(
        "threads",
        activity="Fetching conversation context for tweet 189223",
        counters="24 inspected · 23 expanded · 1 unavailable",
    )
    reporter.update_step("threads", completed=25)
    reporter._step_by_key["threads"].started_at = time.monotonic() - 5
    reporter.issue(
        "Could not fetch thread context for tweet 189222; continuing.",
        dedupe_key="thread-context",
    )

    console.print(reporter)
    rendered = output.getvalue()

    assert "Fetching conversation context for tweet 189223" in rendered
    assert "24 inspected · 23 expanded · 1 unavailable" in rendered
    assert "25/100 tweets · 25.0%" in rendered
    assert "tweets/s" in rendered
    assert "ETA ~" in rendered
    assert "Could not fetch thread context for tweet 189222" in rendered
    assert "elapsed 00:00" in rendered.lower()
    assert "overall eta" not in rendered.lower()
    assert TWITTER_BLUE == "#1DA1F2"


def test_open_ended_step_omits_fabricated_progress_but_keeps_status(tmp_path) -> None:
    output = StringIO()
    state_path = tmp_path / "activity-status.json"
    reporter = PipelineReporter(
        _console(output),
        "tweetnook sync",
        interactive=False,
        state_path=state_path,
    )

    with reporter:
        reporter.add_step(
            "bookmarks",
            "Bookmarks",
            total=1,
            unit="current page",
            detail="head pass",
            show_rate=False,
            show_eta=False,
            show_progress=False,
        )
        reporter.start_step(
            "bookmarks",
            activity="Fetching page 1",
            counters="0 pages · 0 tweets saved",
        )
        reporter.update_step(
            "bookmarks",
            completed=1,
            activity="Archive is up to date",
            counters="1 page · 20 tweets saved",
            important=True,
        )

        snapshot = json.loads(state_path.read_text(encoding="utf-8"))
        assert snapshot["steps"][0]["show_progress"] is False
        reporter.console.print(reporter)

    rendered = output.getvalue()
    assert "Archive is up to date" in rendered
    assert "1 page · 20 tweets saved" in rendered
    assert "0/1 current page" not in rendered
    assert "1/1 current page" not in rendered


def test_live_render_uses_twitter_blue_truecolor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    output = StringIO()
    console = Console(
        file=output,
        force_terminal=True,
        color_system="truecolor",
        width=140,
    )
    reporter = PipelineReporter(
        console,
        "sync",
        spinner_name="wave",
        interactive=False,
    )
    reporter.add_step("media", "Media", total=4, unit="files")
    reporter.start_step("media", activity="Downloading photo for tweet 123")
    reporter.update_step("media", completed=2)

    console.print(reporter)

    assert "\x1b[38;2;29;161;242m" in output.getvalue()


def test_captured_worker_output_only_adds_actual_warnings_and_errors_to_issues() -> None:
    reporter = PipelineReporter(_console(StringIO()), "sync", interactive=False)
    reporter.add_step("tagging", "Tagging", total=2, unit="tweets")
    reporter.start_step("tagging", activity="Preparing Gemini batch")
    captured = reporter.capture_console("tagging")

    captured.print("Generating tags for tweet 123")
    captured.print("[yellow]Gemini API busy; retrying[/yellow]")
    captured.print(Text("Gemini rejected tweet 124", style="red"))

    assert reporter._step_by_key["tagging"].activity == "Generating tags for tweet 123"
    assert [issue.level for issue in reporter.issues] == ["warning", "error"]
    assert all("Generating tags" not in issue.message for issue in reporter.issues)


def test_system_output_is_plain_detailed_and_bounded() -> None:
    output = StringIO()
    reporter = PipelineReporter(
        _console(output),
        "tweetnook sync",
        interactive=False,
        log_interval_seconds=3600,
    )

    with reporter:
        reporter.add_step(
            "threads",
            "Threads",
            total=1000,
            unit="tweets",
            detail="conversation membership and linked context",
        )
        reporter.start_step(
            "threads",
            activity="Expanding thread for tweet 1",
            counters="0 expanded · 0 unavailable",
        )
        for index in range(1, 1001):
            reporter.update_step(
                "threads",
                completed=index,
                activity=f"Expanding thread for tweet {index}",
                counters=f"{index} inspected · {index - 1} expanded",
            )
            reporter.issue(
                f"Could not fetch linked context for tweet {index}",
                dedupe_key="linked-context",
            )
        reporter.complete_step("threads", "999 expanded · 1 unavailable")
        reporter.final_note("Sync completed with one recoverable issue.")

    rendered = output.getvalue()
    lines = rendered.splitlines()
    progress_lines = [line for line in lines if line.startswith("Threads: progress · ")]
    issue_lines = [line for line in lines if line.startswith("WARNING: ")]

    assert "tweetnook sync: started" in rendered
    assert "conversation membership and linked context" in rendered
    assert "Threads: complete · 999 expanded · 1 unavailable · elapsed" in rendered
    assert "tweetnook sync: complete · elapsed" in rendered
    assert "Sync completed with one recoverable issue" in rendered
    assert 10 <= len(progress_lines) <= 12
    assert len(issue_lines) < 50
    assert "occurrences=1000" in rendered
    assert "\x1b" not in rendered
    assert "\r" not in rendered
    assert not any(frame.strip() and frame in rendered for frame in SPINNER_FRAMES["cascade"])


def test_systemd_environment_disables_live_rendering_even_with_a_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INVOCATION_ID", "service-run-id")
    reporter = PipelineReporter(
        Console(file=StringIO(), force_terminal=True, color_system="truecolor"),
        "sync",
    )

    assert reporter.interactive is False


def test_explicit_diagnostics_are_structured_but_not_added_as_issues() -> None:
    output = StringIO()
    reporter = PipelineReporter(_console(output), "archive import", interactive=False)

    with reporter:
        reporter.detail("debug", "archive hash: 1.24s for 8,192 bytes")

    assert "debug: detail · archive hash: 1.24s for 8,192 bytes" in output.getvalue()
    assert reporter.issues == []


def test_reporter_cleans_up_context_and_records_failure() -> None:
    output = StringIO()
    reporter = PipelineReporter(_console(output), "sync", interactive=False)

    with pytest.raises(RuntimeError, match="network failed"):
        with reporter:
            assert current_pipeline() is reporter
            reporter.add_step("bookmarks", "Bookmarks", total=1, unit="pages")
            reporter.start_step("bookmarks", activity="Fetching page 1")
            raise RuntimeError("network failed")

    assert current_pipeline() is None
    assert reporter._step_by_key["bookmarks"].state == "failed"
    assert [(issue.level, issue.message) for issue in reporter.issues] == [
        ("error", "network failed")
    ]
    assert "sync: failed · elapsed 00:00 · network failed" in output.getvalue()


def test_completed_steps_render_their_elapsed_time_at_the_right() -> None:
    output = StringIO()
    reporter = PipelineReporter(_console(output), "sync", interactive=False)
    reporter._started_at = time.monotonic() - 12
    reporter.add_step("threads", "Threads", total=1, unit="tweet")
    reporter.start_step("threads", activity="Fetching thread context")
    reporter._step_by_key["threads"].started_at = time.monotonic() - 7
    reporter.complete_step("threads", "1 expanded")

    reporter.console.print(reporter)

    rendered = output.getvalue()
    assert "elapsed 00:12" in rendered
    assert "1 expanded" in rendered
    assert "00:07" in rendered


def test_reporter_atomically_publishes_web_lifecycle_snapshot(tmp_path) -> None:
    state_path = tmp_path / "activity-status.json"
    reporter = PipelineReporter(
        _console(StringIO()),
        "tweetnook import enrich",
        interactive=False,
        state_path=state_path,
    )

    with reporter:
        reporter.add_step("enrich", "Enrich", total=20, unit="tweets")
        reporter.start_step("enrich", activity="Fetching tweet 10")
        reporter.update_step(
            "enrich",
            completed=10,
            counters="9 refreshed · 1 unavailable",
        )
        reporter.issue("X rate limit reached", dedupe_key="rate-limit")

        snapshot = json.loads(state_path.read_text(encoding="utf-8"))
        assert snapshot["running"] is True
        assert snapshot["title"] == "tweetnook import enrich"
        assert snapshot["active_step"] == "enrich"
        assert snapshot["steps"][0]["completed"] == 10
        assert snapshot["steps"][0]["counters"] == "9 refreshed · 1 unavailable"
        assert snapshot["issues"][0]["message"] == "X rate limit reached"

        reporter.complete_step(
            "enrich",
            "9 refreshed · 1 unavailable",
            metrics={"refreshed": 9, "unavailable": 1},
        )
        completed_step = json.loads(state_path.read_text(encoding="utf-8"))["steps"][0]
        assert completed_step["metrics"] == {"refreshed": 9, "unavailable": 1}

    finished = json.loads(state_path.read_text(encoding="utf-8"))
    assert finished["running"] is False
    assert finished["success"] is True
    assert list(tmp_path.glob(".*.tmp")) == []


def test_stopped_pipeline_snapshot_preserves_the_active_steps_progress(tmp_path) -> None:
    state_path = tmp_path / "activity-status.json"
    reporter = PipelineReporter(
        _console(StringIO()),
        "tweetnook sync",
        interactive=False,
        state_path=state_path,
    )

    with pytest.raises(KeyboardInterrupt):
        with reporter:
            reporter.add_step("threads", "Threads", total=6, unit="candidates")
            reporter.start_step("threads", activity="Fetching thread context")
            reporter.update_step("threads", completed=2)
            raise KeyboardInterrupt

    stopped = json.loads(state_path.read_text(encoding="utf-8"))
    assert stopped["success"] is False
    assert stopped["steps"][0]["state"] == "failed"
    assert stopped["steps"][0]["completed"] == 2
    assert stopped["steps"][0]["summary"] == "Stopped by user"


def test_reporter_lifecycle_lock_serializes_commands_and_releases_after_failure(tmp_path) -> None:
    state_path = tmp_path / "activity-status.json"
    first = PipelineReporter(
        _console(StringIO()),
        "tweetnook import x-archive",
        interactive=False,
        state_path=state_path,
    )
    second = PipelineReporter(
        _console(StringIO()),
        "tweetnook sync",
        interactive=False,
        state_path=state_path,
    )

    with pytest.raises(RuntimeError, match="import failed"):
        with first:
            with pytest.raises(ProcessLockError, match="Another tweetnook command"):
                second.__enter__()
            raise RuntimeError("import failed")

    with second:
        second.final_note("sync can start after import releases its lifecycle lock")
