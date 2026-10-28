from __future__ import annotations

from pathlib import Path

import pytest

import tweetnook.cli as cli
from tests.cli.conftest import runner
from tests.conftest import patch_cli
from tweetnook.cli import common as cli_common


def test_version_option_prints_version_text(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_cli(
        monkeypatch,
        "_version_text",
        lambda: f"tweetnook {cli.__version__} (abc1234, dirty)",
    )
    result = runner.invoke(cli.app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == f"tweetnook {cli.__version__} (abc1234, dirty)"


def test_configure_logging_forces_plain_output_for_systemd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INVOCATION_ID", "service-run-id")

    console = cli_common.configure_logging()

    assert console.is_terminal is False
    assert console.color_system is None


def test_version_text_falls_back_to_semver_without_git(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_cli(monkeypatch, "_find_git_repo_root", lambda: None)

    assert cli._version_text() == f"tweetnook {cli.__version__}"


def test_version_text_includes_git_revision_and_dirty_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = Path("/repo")
    patch_cli(monkeypatch, "_find_git_repo_root", lambda: repo_root)

    def fake_git_command_output(root: Path, *args: str) -> str | None:
        assert root == repo_root
        if args == ("rev-parse", "--short", "HEAD"):
            return "abc1234"
        if args == ("status", "--short", "--untracked-files=no"):
            return " M tweetnook/cli.py"
        raise AssertionError(args)

    patch_cli(monkeypatch, "_git_command_output", fake_git_command_output)

    assert cli._version_text() == f"tweetnook {cli.__version__} (abc1234, dirty)"


def test_sync_help_lists_subcommand_descriptions() -> None:
    result = runner.invoke(cli.app, ["sync", "--help"])

    assert result.exit_code == 0
    assert "Run the normal sync pass." in result.stdout
    assert "Without a subcommand" in result.stdout
    assert "archive enrich" not in result.stdout
    assert "resurrection checks" in result.stdout
    assert "--skip-resurrection" in result.stdout
    assert "--skip-threads" in result.stdout
    assert "bookmarks" in result.stdout
    assert "Sync bookmarked tweets." in result.stdout
    assert "likes" in result.stdout
    assert "Sync liked tweets." in result.stdout
    assert "tweets" in result.stdout
    assert "Sync authored tweets." in result.stdout
    assert "all" in result.stdout
    assert "media download" in result.stdout
    assert "configured AI tagging" in result.stdout
    assert "--browser" not in result.stdout
    assert "--profile" not in result.stdout


def test_linked_depth_options_are_documented_on_sync_and_threads() -> None:
    for command in (["sync"], ["sync", "all"], ["threads", "expand"]):
        result = runner.invoke(cli.app, [*command, "--help"])

        assert result.exit_code == 0
        normalized = " ".join(result.stdout.replace("│", " ").split())
        assert "--max-linked-depth" in normalized
        assert "default 50" in normalized
        assert "--reply-linked-depth" in normalized
        assert "other people's replies" in normalized


def test_reply_linked_depth_rejects_values_above_one() -> None:
    result = runner.invoke(cli.app, ["threads", "expand", "--reply-linked-depth", "2"])

    assert result.exit_code == 2


def test_stats_help_describes_detailed_view() -> None:
    result = runner.invoke(cli.app, ["stats", "--help"])

    assert result.exit_code == 0
    assert "--detailed" in result.stdout
    normalized = " ".join(result.stdout.split())
    assert "full storage breakdown" in normalized
    assert "unavailable" in normalized
    assert "zero-count maintenance queues" in normalized


def test_sync_all_help_describes_default_followups() -> None:
    result = runner.invoke(cli.app, ["sync", "all", "--help"])

    assert result.exit_code == 0
    assert "Sync the collections chosen in sync.collections" in " ".join(result.stdout.split())
    assert "resurrection checks" in result.stdout
    assert "--skip-media" in result.stdout
    assert "--skip-unfurl" in result.stdout
    assert "configured AI tagging" in result.stdout


def test_sync_likes_help_describes_flags() -> None:
    result = runner.invoke(cli.app, ["sync", "likes", "--help"])

    assert result.exit_code == 0
    assert "Reset saved sync state" in result.stdout
    assert "Continue older history past duplicates" in result.stdout
    assert "Clear any saved backfill cursor" in result.stdout
    assert "Maximum number of pages to fetch" in result.stdout


def test_auth_check_help_describes_local_validation() -> None:
    result = runner.invoke(cli.app, ["auth", "check", "--help"])

    assert result.exit_code == 0
    assert "Validate local auth" in result.stdout


def test_export_help_describes_json_export() -> None:
    result = runner.invoke(cli.app, ["export", "--help"])

    assert result.exit_code == 0
    assert "json" in result.stdout
    assert "Export the archive as JSON." in result.stdout


def test_import_x_archive_help_describes_offline_mode() -> None:
    result = runner.invoke(cli.app, ["import", "x-archive", "--help"], terminal_width=140)
    assert result.exit_code == 0
    assert "--offline" in result.output
    assert "Twitter/X requests" in result.output
    assert "--defer-followup" in result.output
    assert "without live" in result.output
    normalized = " ".join(result.output.replace("│", "").split())
    assert "Import, enrich, and download media now" in normalized
    assert "the first sync separately" in normalized


def test_import_x_archive_help_describes_sample_limit() -> None:
    result = runner.invoke(cli.app, ["import", "x-archive", "--help"])

    assert result.exit_code == 0
    assert "--sample-limit" in result.stdout
    normalized = " ".join(result.stdout.split())
    assert "sampled manifest" in normalized
    assert "--detail-lookups" in result.stdout
    assert "--no-enrich" in result.stdout
    assert "[default: enrich]" in result.stdout
    assert "unified pipeline" in normalized


def test_import_enrich_help_describes_unbounded_limit() -> None:
    result = runner.invoke(
        cli.app,
        ["import", "enrich", "--help"],
        env={"COLUMNS": "160"},
    )

    assert result.exit_code == 0
    assert "eligible when the command starts" in " ".join(result.stdout.split())
    assert "--limit" in result.stdout
    assert "Omit to process every currently eligible row" in " ".join(result.stdout.split())
    assert "then sync the configured collections past saved tweets" in " ".join(
        result.stdout.split()
    )


def test_database_check_help_exposes_full_integrity_option() -> None:
    result = runner.invoke(cli.app, ["db", "check", "--help"])

    assert result.exit_code == 0
    assert "--full" in result.stdout
    assert "integrity_check" in result.stdout
    assert "quick_check" in result.stdout


def test_root_help_lists_group_descriptions() -> None:
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert "sync" in result.stdout
    assert "Run the normal sync pass." in result.stdout
    assert "auth" in result.stdout
    assert "Check auth and refresh query IDs." in result.stdout
    assert "db" in result.stdout
    assert "Inspect the local SQLite archive database." in result.stdout
    assert "articles" in result.stdout
    assert "Refresh archived article bodies." in result.stdout
    assert "export" in result.stdout
    assert "Export the local archive." in result.stdout
    assert "import" in result.stdout
    assert "Import and enrich official Twitter/X archives." in result.stdout
    assert "media" in result.stdout
    assert "Download archived tweet media." in result.stdout
    assert "Repair recoverable legacy archive rows." not in result.stdout
    assert "threads" in result.stdout
    assert "Expand archived tweet threads." in result.stdout
    assert "view" in result.stdout
    assert "Render archived tweets in the terminal." in result.stdout


def test_view_bookmarks_help_describes_flags() -> None:
    result = runner.invoke(cli.app, ["view", "bookmarks", "--help"])

    assert result.exit_code == 0
    assert "View bookmarked tweets." in result.stdout
    assert "Maximum number of rows to display." in result.stdout
    assert "Display order: newest or oldest." in result.stdout


def test_media_download_help_describes_flags() -> None:
    result = runner.invoke(cli.app, ["media", "download", "--help"])

    assert result.exit_code == 0
    assert "Download archived tweet media files." in result.stdout
    assert "Maximum number of media items" in result.stdout
    assert "poster file" in result.stdout
    assert "Only download photo rows and skip video or" in result.stdout
    assert "Retry rows that previously failed" in result.stdout


def test_search_help_describes_flags() -> None:
    result = runner.invoke(cli.app, ["search", "--help"])

    assert result.exit_code == 0
    assert "Full-text search archived tweets and articles." in result.stdout
    assert "Search query text." in result.stdout
    assert "Maximum number of results to" in result.stdout
    assert "--mode" not in result.stdout
    assert "Comma-delimited search result" in result.stdout
    assert "post (default)" in " ".join(result.stdout.split())
    assert "Comma-delimited collections:" in result.stdout
