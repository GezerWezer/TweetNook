from __future__ import annotations

import os
import runpy
import subprocess
import sys
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import ANY

import pytest
from rich.console import Console
from typer.testing import CliRunner

import tweetnook.cli as cli
import tweetnook.jobs as jobs
import tweetnook.tagging as tagging
from tweetnook.config import AppConfig, TaggingConfig
from tweetnook.exceptions import ConfigError, TweetNookError

runner = CliRunner()


class FakeTagStore:
    pass


class FakeLockedJob:
    def __init__(
        self,
        store: FakeTagStore,
        events: list[str],
        *,
        enter_error: BaseException | None = None,
    ) -> None:
        self.store = store
        self.events = events
        self.enter_error = enter_error

    async def __aenter__(self) -> FakeLockedJob:
        self.events.append("enter")
        if self.enter_error:
            raise self.enter_error
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: Any,
    ) -> None:
        self.events.append(f"exit:{exc_type.__name__ if exc_type else 'clean'}")


def capture_console(monkeypatch: pytest.MonkeyPatch) -> StringIO:
    output = StringIO()
    monkeypatch.setattr(
        cli,
        "_configure_logging",
        lambda: Console(
            file=output,
            force_terminal=False,
            color_system=None,
            width=240,
        ),
    )
    return output


def configure_tag_command(
    monkeypatch: pytest.MonkeyPatch,
    paths,
    *,
    pending_result: tagging.TaggingRunResult | None = None,
    direct_tagged: int = 1,
    enter_error: BaseException | None = None,
    pending_error: BaseException | None = None,
) -> tuple[
    AppConfig,
    FakeTagStore,
    list[str],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    config = AppConfig(
        tagging=TaggingConfig(
            enabled=True,
            api_key="test-key",
        )
    )
    store = FakeTagStore()
    lifecycle: list[str] = []
    lock_calls: list[dict[str, Any]] = []
    direct_calls: list[dict[str, Any]] = []
    pending_calls: list[dict[str, Any]] = []

    monkeypatch.setattr(cli, "load_config", lambda: (config, paths))

    def fake_locked_archive_job(**kwargs: Any) -> FakeLockedJob:
        lock_calls.append(kwargs)
        return FakeLockedJob(store, lifecycle, enter_error=enter_error)

    async def fake_tag_media_tweets(**kwargs: Any) -> int:
        lifecycle.append("direct")
        direct_calls.append(kwargs)
        return direct_tagged

    async def fake_tag_pending_media_tweets(**kwargs: Any) -> tagging.TaggingRunResult:
        lifecycle.append("pending")
        pending_calls.append(kwargs)
        if pending_error is not None:
            raise pending_error
        return pending_result or tagging.TaggingRunResult(processed=3, tagged=3, batches=2)

    monkeypatch.setattr(jobs, "locked_archive_job", fake_locked_archive_job)
    monkeypatch.setattr(tagging, "tag_media_tweets", fake_tag_media_tweets)
    monkeypatch.setattr(tagging, "tag_pending_media_tweets", fake_tag_pending_media_tweets)
    return config, store, lifecycle, lock_calls, direct_calls, pending_calls


def test_root_help_exposes_installed_non_sync_additions() -> None:
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert "tag" in result.stdout
    assert "Use Gemini to generate search tags" in result.stdout
    assert "migrate" in result.stdout
    assert "Migrate data from older LanceDB storage" in result.stdout
    assert "web" in result.stdout
    assert "Manage the background web UI server" in result.stdout
    assert "serve-daemon" not in result.stdout


def test_tag_help_documents_target_and_all_options() -> None:
    result = runner.invoke(cli.app, ["tag", "--help"])
    help_text = " ".join(result.stdout.replace("│", " ").split())

    assert result.exit_code == 0
    assert "Tweet ID or Twitter/X status URL to tag" in help_text
    assert "--limit" in help_text
    assert "Maximum number of independent Gemini requests" in help_text
    assert "--test" in help_text
    assert "without saving them" in help_text
    assert "--batch" in help_text
    assert "Override the Free-mode tweets-per-request batch size" in help_text
    assert "Paid requests remain sequential" in help_text
    assert "--model" in help_text
    assert "Override the Gemini model specified in config.toml" in help_text


def test_tag_command_delegates_default_run_to_pending_runner_inside_locked_job(
    monkeypatch: pytest.MonkeyPatch,
    paths,
) -> None:
    output = capture_console(monkeypatch)
    config, store, lifecycle, lock_calls, direct_calls, pending_calls = configure_tag_command(
        monkeypatch, paths
    )

    result = runner.invoke(cli.app, ["tag"])

    assert result.exit_code == 0, result.output
    assert "tag: 3 processed, 3 tagged" in output.getvalue()
    assert lifecycle == ["enter", "pending", "exit:clean"]
    assert lock_calls == [{"config": config, "paths": paths, "console": ANY}]
    assert direct_calls == []
    assert pending_calls == [
        {
            "store": store,
            "config": config,
            "paths": paths,
            "console": ANY,
            "batch_limit": None,
            "batch_size": None,
            "model_override": None,
            "dry_run": False,
        }
    ]


def test_tag_command_forwards_limit_batch_test_and_model_to_pending_runner(
    monkeypatch: pytest.MonkeyPatch,
    paths,
) -> None:
    output = capture_console(monkeypatch)
    preview_result = tagging.TaggingRunResult(processed=1, tagged=1, batches=1)
    config, store, lifecycle, lock_calls, direct_calls, pending_calls = configure_tag_command(
        monkeypatch, paths, pending_result=preview_result
    )

    result = runner.invoke(
        cli.app,
        [
            "tag",
            "--limit",
            "7",
            "--batch",
            "20",
            "--test",
            "--model",
            "gemini-explicit",
        ],
    )

    assert result.exit_code == 0, result.output
    assert lifecycle == ["enter", "pending", "exit:clean"]
    assert lock_calls[0]["config"] is config
    assert lock_calls[0]["paths"] is paths
    assert lock_calls[0]["console"] is pending_calls[0]["console"]
    assert direct_calls == []
    assert pending_calls == [
        {
            "store": store,
            "config": config,
            "paths": paths,
            "console": ANY,
            "batch_limit": 7,
            "batch_size": 20,
            "model_override": "gemini-explicit",
            "dry_run": True,
        }
    ]
    assert "tag: 1 processed" not in output.getvalue()


@pytest.mark.parametrize(
    ("target", "tweet_id"),
    [
        ("2026531440414925307", "2026531440414925307"),
        (
            "https://x.com/example/status/2026531440414925307?s=20",
            "2026531440414925307",
        ),
        (
            "https://twitter.com/example/status/2026531440414925307/video/1",
            "2026531440414925307",
        ),
    ],
)
def test_tag_command_normalizes_explicit_target_and_bypasses_pending_runner(
    monkeypatch: pytest.MonkeyPatch,
    paths,
    target: str,
    tweet_id: str,
) -> None:
    output = capture_console(monkeypatch)
    config, store, lifecycle, _, direct_calls, pending_calls = configure_tag_command(
        monkeypatch, paths, direct_tagged=1
    )

    result = runner.invoke(cli.app, ["tag", target, "--model", "gemini-explicit"])

    assert result.exit_code == 0, result.output
    assert lifecycle == ["enter", "direct", "exit:clean"]
    assert pending_calls == []
    assert direct_calls == [
        {
            "store": store,
            "config": config,
            "paths": paths,
            "console": ANY,
            "tweet_ids": [tweet_id],
            "model_override": "gemini-explicit",
            "dry_run": False,
        }
    ]
    assert "Automated tags: complete · 1 tagged" in output.getvalue()


def test_tag_command_forwards_test_mode_for_explicit_target_without_summary(
    monkeypatch: pytest.MonkeyPatch,
    paths,
) -> None:
    output = capture_console(monkeypatch)
    _, _, lifecycle, _, direct_calls, pending_calls = configure_tag_command(
        monkeypatch, paths, direct_tagged=1
    )

    result = runner.invoke(cli.app, ["tag", "41", "--test"])

    assert result.exit_code == 0, result.output
    assert lifecycle == ["enter", "direct", "exit:clean"]
    assert pending_calls == []
    assert direct_calls[0]["tweet_ids"] == ["41"]
    assert direct_calls[0]["dry_run"] is True
    assert "tag: 1 processed" not in output.getvalue()


@pytest.mark.parametrize(
    "target",
    [
        "not-a-tweet",
        "https://example.com/person/status/2026531440414925307",
        "https://x.com/example/status/not-numeric",
    ],
)
def test_tag_command_rejects_invalid_explicit_target_before_locking_archive(
    monkeypatch: pytest.MonkeyPatch,
    paths,
    target: str,
) -> None:
    output = capture_console(monkeypatch)
    _, _, lifecycle, lock_calls, direct_calls, pending_calls = configure_tag_command(
        monkeypatch, paths
    )

    result = runner.invoke(cli.app, ["tag", target])

    assert result.exit_code == 1
    assert lifecycle == []
    assert lock_calls == []
    assert direct_calls == []
    assert pending_calls == []
    assert "Unsupported tag target. Use a tweet ID or Twitter/X status URL." in output.getvalue()


@pytest.mark.parametrize("raw_limit", ["0", "-1"])
def test_tag_command_rejects_nonpositive_limit(
    raw_limit: str,
) -> None:
    result = runner.invoke(cli.app, ["tag", "--limit", raw_limit])

    assert result.exit_code == 2
    assert "Invalid value for '--limit'" in result.output


@pytest.mark.parametrize("raw_batch", ["0", "-1"])
def test_tag_command_rejects_nonpositive_batch_size(raw_batch: str) -> None:
    result = runner.invoke(cli.app, ["tag", "--batch", raw_batch])

    assert result.exit_code == 2
    assert "Invalid value for '--batch'" in result.output


def test_tag_command_requires_batch_size() -> None:
    result = runner.invoke(cli.app, ["tag", "--batch"])

    assert result.exit_code == 2
    assert "Option '--batch' requires an argument" in result.output


def test_tag_command_reports_no_eligible_rows_and_still_closes_job(
    monkeypatch: pytest.MonkeyPatch,
    paths,
) -> None:
    output = capture_console(monkeypatch)
    no_work = tagging.TaggingRunResult()
    _, _, lifecycle, _, direct_calls, pending_calls = configure_tag_command(
        monkeypatch,
        paths,
        pending_result=no_work,
    )

    result = runner.invoke(cli.app, ["tag"])

    assert result.exit_code == 0, result.output
    assert direct_calls == []
    assert len(pending_calls) == 1
    assert lifecycle == ["enter", "pending", "exit:clean"]
    assert "No eligible untagged tweets found." in output.getvalue()


def test_tag_command_maps_config_error_to_exit_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = capture_console(monkeypatch)
    monkeypatch.setattr(
        cli,
        "load_config",
        lambda: (_ for _ in ()).throw(ConfigError("invalid tagging config")),
    )

    result = runner.invoke(cli.app, ["tag"])

    assert result.exit_code == 1
    assert "invalid tagging config" in output.getvalue()


def test_tag_command_maps_lock_entry_error_to_exit_two(
    monkeypatch: pytest.MonkeyPatch,
    paths,
) -> None:
    output = capture_console(monkeypatch)
    _, _, lifecycle, _, _, _ = configure_tag_command(
        monkeypatch,
        paths,
        enter_error=TweetNookError("archive is unavailable"),
    )

    result = runner.invoke(cli.app, ["tag"])

    assert result.exit_code == 2
    assert lifecycle == ["enter"]
    assert "archive is unavailable" in output.getvalue()


def test_tag_command_maps_runner_domain_error_to_exit_two_and_closes_job(
    monkeypatch: pytest.MonkeyPatch,
    paths,
) -> None:
    output = capture_console(monkeypatch)
    _, _, lifecycle, _, _, _ = configure_tag_command(
        monkeypatch,
        paths,
        pending_error=TweetNookError("tagging is unavailable"),
    )

    result = runner.invoke(cli.app, ["tag"])

    assert result.exit_code == 2
    assert lifecycle == ["enter", "pending", "exit:TweetNookError"]
    assert "tagging is unavailable" in output.getvalue()


def test_python_module_help_dispatches_real_cli() -> None:
    environment = os.environ.copy()
    environment["NO_COLOR"] = "1"

    result = subprocess.run(
        [sys.executable, "-m", "tweetnook", "--help"],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "TweetNook CLI" in result.stdout
    assert "tag" in result.stdout
    assert "migrate" in result.stdout
    assert "web" in result.stdout
    assert "serve-daemon" not in result.stdout


def test_python_module_dispatches_hidden_serve_daemon_via_main(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatched_arguments: list[list[str]] = []

    def fake_app() -> None:
        dispatched_arguments.append(sys.argv[1:])

    monkeypatch.setattr(cli, "app", fake_app)
    monkeypatch.setattr(sys, "argv", ["tweetnook", "serve-daemon"])

    runpy.run_module("tweetnook", run_name="__main__")

    assert dispatched_arguments == [["serve-daemon"]]


def test_hidden_serve_daemon_defers_archive_lifecycle_to_server(
    monkeypatch: pytest.MonkeyPatch,
    paths,
) -> None:
    events: list[object] = []
    config = AppConfig()

    class FakeArchive:
        def close(self) -> None:
            events.append("close")

    def fake_run_server(*args: Any) -> None:
        events.append(("run_server", args))

    monkeypatch.setattr(cli, "load_config", lambda: (config, paths))
    monkeypatch.setattr(
        cli,
        "open_archive_store",
        lambda loaded_paths, *, create, config: (
            events.append(("open", loaded_paths, create, config)) or FakeArchive()
        ),
    )
    monkeypatch.setattr("tweetnook.web.server.run_server", fake_run_server)

    cli.serve_daemon_internal()

    assert events == [
        ("run_server", (config, paths, config.web.host, config.web.port, config.web.password_hash))
    ]
