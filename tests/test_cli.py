from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
import typer
from rich.console import Console
from typer.testing import CliRunner

import tweetnook.cli as cli
from tweetnook.client.timelines import TimelineTweet
from tweetnook.config import AppConfig, AuthConfig
from tweetnook.storage import open_archive_store

runner = CliRunner()


def _tweet(tweet_id: str, *, text: str) -> TimelineTweet:
    return TimelineTweet(
        tweet_id=tweet_id,
        text=text,
        author_id="1",
        author_username="user1",
        author_display_name="User 1",
        created_at="Sat Mar 14 00:00:00 +0000 2026",
        sort_index="10",
        raw_json={"tweet": tweet_id},
    )


def _seed_archive(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[_tweet("1", text="bookmark tweet")],
        last_head_tweet_id="1",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.persist_page(
        operation="Likes",
        collection_type="like",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[_tweet("2", text="like tweet")],
        last_head_tweet_id="2",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.persist_page(
        operation="UserTweets",
        collection_type="tweet",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[_tweet("3", text="authored tweet")],
        last_head_tweet_id="3",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.close()


def _capture_console(monkeypatch, buffer: StringIO) -> None:
    monkeypatch.setattr(
        cli,
        "_configure_logging",
        lambda: Console(file=buffer, force_terminal=False, color_system=None),
    )


def test_version_option_prints_version_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli,
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

    console = cli._configure_logging()

    assert console.is_terminal is False
    assert console.color_system is None


def test_version_text_falls_back_to_semver_without_git(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_find_git_repo_root", lambda: None)

    assert cli._version_text() == f"tweetnook {cli.__version__}"


def test_version_text_includes_git_revision_and_dirty_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = Path("/repo")
    monkeypatch.setattr(cli, "_find_git_repo_root", lambda: repo_root)

    def fake_git_command_output(root: Path, *args: str) -> str | None:
        assert root == repo_root
        if args == ("rev-parse", "--short", "HEAD"):
            return "abc1234"
        if args == ("status", "--short", "--untracked-files=no"):
            return " M tweetnook/cli.py"
        raise AssertionError(args)

    monkeypatch.setattr(cli, "_git_command_output", fake_git_command_output)

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
    assert "Sync bookmarks and likes" in result.stdout
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


def test_auth_check_help_has_only_explicit_auth_flow() -> None:
    result = runner.invoke(cli.app, ["auth", "check", "--help"])

    assert result.exit_code == 0
    assert "Validate local auth" in result.stdout
    assert "--browser" not in result.stdout
    assert "--profile" not in result.stdout
    assert "--interactive" not in result.stdout


def test_export_help_only_lists_json() -> None:
    result = runner.invoke(cli.app, ["export", "--help"])

    assert result.exit_code == 0
    assert "json" in result.stdout
    assert "Export the archive as JSON." in result.stdout
    assert "html" not in result.stdout.lower()


def test_import_x_archive_help_describes_offline_mode() -> None:
    result = runner.invoke(cli.app, ["import", "x-archive", "--help"], terminal_width=140)
    assert result.exit_code == 0
    assert "--offline" in result.output
    assert "Twitter/X requests" in result.output


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


def test_import_enrich_help_has_no_default_limit() -> None:
    result = runner.invoke(
        cli.app,
        ["import", "enrich", "--help"],
        env={"COLUMNS": "160"},
    )

    assert result.exit_code == 0
    assert "eligible when the command starts" in " ".join(result.stdout.split())
    assert "--limit" in result.stdout
    assert "Omit to process every currently eligible row" in " ".join(result.stdout.split())
    assert "[default: 200]" not in result.stdout


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
    assert "Maximum number of pending media rows" in result.stdout
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


def test_view_bookmarks_prints_rows(paths, monkeypatch) -> None:
    _seed_archive(paths)
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    monkeypatch.setattr(cli, "_format_created_at", lambda raw: "LOCAL-TIME")

    cli.view_bookmarks(limit=5)

    output = " ".join(buffer.getvalue().split())
    assert "bookmark tweet" in output
    assert "bookmarks archive" in output
    assert "LOCAL-TIME" in output
    assert "https://x.com/user1/status/1" in output
    assert "like tweet" not in output


def test_view_tweets_prints_rows(paths, monkeypatch) -> None:
    _seed_archive(paths)
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))

    cli.view_tweets(limit=5)

    output = " ".join(buffer.getvalue().split())
    assert "authored tweet" in output
    assert "tweets archive" in output
    assert "bookmark tweet" not in output


def test_highlight_search_matches_marks_query_terms() -> None:
    rendered = cli._highlight_search_matches(
        "Machine learning beats keyword search for search-heavy tasks.",
        "search learning",
    )

    assert rendered.plain == "Machine learning beats keyword search for search-heavy tasks."
    spans = {(span.start, span.end, span.style) for span in rendered.spans}
    assert (8, 16, "black on yellow") in spans
    assert (31, 37, "black on yellow") in spans
    assert (42, 48, "black on yellow") in spans


def test_search_uses_shared_tweet_list_rendering(monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "_format_created_at", lambda raw: "LOCAL-TIME")
    monkeypatch.setattr(cli, "_with_auto_optimize", lambda store, paths, console, fn: fn(store))

    class _FakeStore:
        def close(self) -> None:
            return None

    def fake_search_posts(store, query, *, collections, sort, limit):
        assert isinstance(store, _FakeStore)
        assert query == "bookmark"
        assert limit == 5
        assert sort == "relevance"
        assert collections == {"bookmark"}
        return SimpleNamespace(
            rows=[
                {
                    "tweet_id": "1",
                    "type": "post",
                    "collections": ["bookmark"],
                    "author": {"username": "user1", "id": "1"},
                    "created_at": "Sat Mar 14 00:00:00 +0000 2026",
                    "text": "bookmark tweet",
                    "match_score": 0.75,
                }
            ],
            total=1,
            truncated=False,
        )

    monkeypatch.setattr(cli, "_open_store_for_read", lambda console: (_FakeStore(), object()))
    monkeypatch.setattr(cli, "search_posts", fake_search_posts)

    cli.search_archive(
        "bookmark",
        limit=5,
        collection_filter="bookmarks",
    )

    output = buffer.getvalue()
    assert "search: bookmark" in output
    assert "showing 1 of 1 search results" in output
    assert "LOCAL-TIME" in output
    assert "/status/1" in output
    assert "0.750" in output
    assert "post · bookmark" in output
    assert "bookmark tweet" in output


def test_sort_search_results_reorders_newest_then_oldest() -> None:
    rows = [
        {
            "tweet_id": "1",
            "created_at": "Tue Oct 09 21:39:26 +0000 2012",
            "match_score": 0.95,
        },
        {
            "tweet_id": "2",
            "created_at": "Thu Apr 11 03:55:13 +0000 2024",
            "match_score": 0.90,
        },
        {
            "tweet_id": "3",
            "created_at": None,
            "match_score": 1.0,
        },
    ]

    newest = cli._sort_search_results(rows, sort="newest")
    oldest = cli._sort_search_results(rows, sort="oldest")

    assert [row["tweet_id"] for row in newest] == ["2", "1", "3"]
    assert [row["tweet_id"] for row in oldest] == ["1", "2", "3"]


def test_export_json_accepts_plural_collection_name(paths, monkeypatch, tmp_path: Path) -> None:
    _seed_archive(paths)
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    out_path = tmp_path / "bookmarks.json"

    cli.export_json(collection="bookmarks", out=out_path)

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert [row["tweet_id"] for row in payload] == ["1"]
    assert "exported bookmarks archive" in buffer.getvalue()


def test_export_json_accepts_tweets_collection_name(paths, monkeypatch, tmp_path: Path) -> None:
    _seed_archive(paths)
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    out_path = tmp_path / "tweets.json"

    cli.export_json(collection="tweets", out=out_path)

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert [row["tweet_id"] for row in payload] == ["3"]
    assert "exported tweets archive" in buffer.getvalue()


def test_auth_check_uses_explicit_credentials(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(
        cli,
        "load_config",
        lambda: (
            AppConfig(auth=AuthConfig(auth_token="config-token", ct0="config-ct0")),
            paths,
        ),
    )
    selected = {}
    monkeypatch.setattr(
        cli,
        "resolve_auth_bundle",
        lambda config, env=None, status=None: SimpleNamespace(
            auth_token="config-token",
            ct0="config-ct0",
            user_id="42",
            auth_token_source="config",
            ct0_source="config",
            user_id_source="config",
        ),
    )

    async def fake_run_preflight(*, config, paths, collections, auth_bundle=None):
        selected["auth_bundle"] = auth_bundle
        return SimpleNamespace(
            auth=auth_bundle,
            probes={
                "bookmarks": SimpleNamespace(ready=True, detail="Remote probe succeeded."),
                "likes": SimpleNamespace(ready=True, detail="Remote probe succeeded."),
                "tweets": SimpleNamespace(ready=True, detail="Remote probe succeeded."),
            },
            has_local_error=False,
            has_remote_error=False,
        )

    monkeypatch.setattr(cli, "run_preflight", fake_run_preflight)

    cli.auth_check()

    assert selected["auth_bundle"].auth_token == "config-token"
    output = buffer.getvalue()
    assert "local auth: auth_token=config" in output
    assert "bookmarks: ready" in output
    assert "tweets: ready" in output


def test_sync_bookmarks_forwards_article_backfill(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    monkeypatch.setattr(
        cli,
        "resolve_auth_bundle",
        lambda config: SimpleNamespace(auth_token="t"),
    )
    forwarded = {}

    async def fake_sync_collection(
        collection,
        *,
        full,
        backfill=False,
        article_backfill=False,
        head_only=False,
        limit=None,
        config=None,
        auth_bundle=None,
        console=None,
        followups=None,
    ):
        forwarded.update(
            {
                "collection": collection,
                "full": full,
                "backfill": backfill,
                "article_backfill": article_backfill,
                "head_only": head_only,
                "limit": limit,
                "followups": followups,
            }
        )
        return SimpleNamespace(pages_fetched=2, tweets_seen=3, stop_reason="empty")

    monkeypatch.setattr(cli, "sync_collection", fake_sync_collection)

    cli.sync_bookmarks(article_backfill=True)

    assert forwarded == {
        "collection": "bookmarks",
        "full": False,
        "backfill": False,
        "article_backfill": True,
        "head_only": False,
        "limit": None,
        "followups": cli.SyncFollowupPlan(),
    }
    assert "bookmarks: 2 pages, 3 tweets, empty" in buffer.getvalue()


def test_sync_likes_forwards_article_backfill(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    monkeypatch.setattr(
        cli,
        "resolve_auth_bundle",
        lambda config: SimpleNamespace(auth_token="t"),
    )
    forwarded = {}

    async def fake_sync_collection(
        collection,
        *,
        full,
        backfill=False,
        article_backfill=False,
        head_only=False,
        limit=None,
        config=None,
        auth_bundle=None,
        console=None,
        followups=None,
    ):
        forwarded.update(
            {
                "collection": collection,
                "full": full,
                "backfill": backfill,
                "article_backfill": article_backfill,
                "head_only": head_only,
                "limit": limit,
                "followups": followups,
            }
        )
        return SimpleNamespace(pages_fetched=2, tweets_seen=3, stop_reason="empty")

    monkeypatch.setattr(cli, "sync_collection", fake_sync_collection)

    cli.sync_likes(article_backfill=True)

    assert forwarded == {
        "collection": "likes",
        "full": False,
        "backfill": False,
        "article_backfill": True,
        "head_only": False,
        "limit": None,
        "followups": cli.SyncFollowupPlan(),
    }
    assert "likes: 2 pages, 3 tweets, empty" in buffer.getvalue()


def test_sync_tweets_forwards_article_backfill(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    monkeypatch.setattr(
        cli,
        "resolve_auth_bundle",
        lambda config: SimpleNamespace(auth_token="t"),
    )
    forwarded = {}

    async def fake_sync_collection(
        collection,
        *,
        full,
        backfill=False,
        article_backfill=False,
        head_only=False,
        limit=None,
        config=None,
        auth_bundle=None,
        console=None,
        followups=None,
    ):
        forwarded.update(
            {
                "collection": collection,
                "full": full,
                "backfill": backfill,
                "article_backfill": article_backfill,
                "head_only": head_only,
                "limit": limit,
                "followups": followups,
            }
        )
        return SimpleNamespace(pages_fetched=2, tweets_seen=3, stop_reason="empty")

    monkeypatch.setattr(cli, "sync_collection", fake_sync_collection)

    cli.sync_tweets(article_backfill=True)

    assert forwarded == {
        "collection": "tweets",
        "full": False,
        "backfill": False,
        "article_backfill": True,
        "head_only": False,
        "limit": None,
        "followups": cli.SyncFollowupPlan(),
    }
    assert "tweets: 2 pages, 3 tweets, empty" in buffer.getvalue()


def test_sync_all_forwards_article_backfill(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    monkeypatch.setattr(
        cli,
        "resolve_auth_bundle",
        lambda config: SimpleNamespace(auth_token="t"),
    )
    forwarded = {}

    async def fake_sync_all(
        *,
        full,
        backfill=False,
        article_backfill=False,
        head_only=False,
        limit=None,
        config=None,
        auth_bundle=None,
        console=None,
        followups=None,
    ):
        forwarded.update(
            {
                "full": full,
                "backfill": backfill,
                "article_backfill": article_backfill,
                "head_only": head_only,
                "limit": limit,
                "followups": followups,
            }
        )
        return SimpleNamespace(
            results=[SimpleNamespace(collection="bookmarks", pages_fetched=2, tweets_seen=3)],
            errors={"likes": "boom"},
            exit_code=2,
        )

    monkeypatch.setattr(cli, "sync_all", fake_sync_all)

    with pytest.raises(typer.Exit) as excinfo:
        cli.sync_everything(article_backfill=True)

    assert excinfo.value.exit_code == 2
    assert forwarded == {
        "full": False,
        "backfill": False,
        "article_backfill": True,
        "head_only": False,
        "limit": None,
        "followups": cli.SyncFollowupPlan(),
    }
    output = buffer.getvalue()
    assert "bookmarks: 2 pages, 3 tweets" in output
    assert "Likes sync failed: boom" in output


def test_sync_default_runs_sync_all_with_full_followups(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    monkeypatch.setattr(
        cli,
        "resolve_auth_bundle",
        lambda config: SimpleNamespace(auth_token="t"),
    )
    forwarded = {}

    async def fake_sync_all(
        *,
        full,
        backfill=False,
        article_backfill=False,
        head_only=False,
        limit=None,
        config=None,
        auth_bundle=None,
        console=None,
        followups=None,
    ):
        forwarded.update(
            {
                "full": full,
                "backfill": backfill,
                "article_backfill": article_backfill,
                "head_only": head_only,
                "limit": limit,
                "followups": followups,
            }
        )
        return SimpleNamespace(
            results=[
                SimpleNamespace(collection="bookmarks", pages_fetched=2, tweets_seen=3),
                SimpleNamespace(collection="likes", pages_fetched=1, tweets_seen=2),
            ],
            errors={},
            exit_code=0,
        )

    monkeypatch.setattr(cli, "sync_all", fake_sync_all)

    result = runner.invoke(cli.app, ["sync"])

    assert result.exit_code == 0
    assert forwarded == {
        "full": False,
        "backfill": False,
        "article_backfill": False,
        "head_only": False,
        "limit": None,
        "followups": cli.SyncFollowupPlan(),
    }
    output = buffer.getvalue()
    assert "bookmarks: 2 pages, 3 tweets" in output
    assert "likes: 1 pages, 2 tweets" in output


def test_sync_default_skip_flags_disable_selected_followups(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    monkeypatch.setattr(
        cli,
        "resolve_auth_bundle",
        lambda config: SimpleNamespace(auth_token="t"),
    )
    forwarded = {}

    async def fake_sync_all(
        *,
        full,
        backfill=False,
        article_backfill=False,
        head_only=False,
        limit=None,
        config=None,
        auth_bundle=None,
        console=None,
        followups=None,
    ):
        forwarded["followups"] = followups
        return SimpleNamespace(results=[], errors={}, exit_code=0)

    monkeypatch.setattr(cli, "sync_all", fake_sync_all)

    result = runner.invoke(
        cli.app, ["sync", "--skip-resurrection", "--skip-media", "--skip-threads"]
    )

    assert result.exit_code == 0
    assert forwarded["followups"] == cli.SyncFollowupPlan(
        resurrection=False,
        media=False,
        threads=False,
    )


def test_sync_likes_forwards_head_only(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    monkeypatch.setattr(
        cli,
        "resolve_auth_bundle",
        lambda config: SimpleNamespace(auth_token="t"),
    )
    forwarded = {}

    async def fake_sync_collection(
        collection,
        *,
        full,
        backfill=False,
        article_backfill=False,
        head_only=False,
        limit=None,
        config=None,
        auth_bundle=None,
        console=None,
        followups=None,
    ):
        forwarded.update(
            {
                "collection": collection,
                "full": full,
                "backfill": backfill,
                "article_backfill": article_backfill,
                "head_only": head_only,
                "limit": limit,
                "followups": followups,
            }
        )
        return SimpleNamespace(pages_fetched=1, tweets_seen=20, stop_reason="duplicate")

    monkeypatch.setattr(cli, "sync_collection", fake_sync_collection)

    cli.sync_likes(head_only=True)

    assert forwarded == {
        "collection": "likes",
        "full": False,
        "backfill": False,
        "article_backfill": False,
        "head_only": True,
        "limit": None,
        "followups": cli.SyncFollowupPlan(),
    }
    assert "likes: 1 pages, 20 tweets, duplicate" in buffer.getvalue()


def test_media_download_reports_runner_result(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))

    async def fake_download_media(**kwargs):
        return SimpleNamespace(processed=3, downloaded=2, skipped=1, failed=0)

    monkeypatch.setattr(cli, "download_media", fake_download_media)

    cli.media_download(limit=5, photos_only=True, retry_failed=True)

    output = buffer.getvalue()
    assert "media: 3 processed, 2 downloaded, 1 skipped, 0 failed" in output


def test_unfurl_archive_reports_runner_result(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))

    async def fake_unfurl_urls(**kwargs):
        return SimpleNamespace(processed=2, updated=2, failed=0)

    monkeypatch.setattr(cli, "unfurl_urls", fake_unfurl_urls)

    cli.unfurl_archive(limit=10, retry_failed=True)

    output = buffer.getvalue()
    assert "unfurl: 2 processed, 2 updated, 0 failed" in output


def test_refresh_archived_articles_reports_runner_result(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    monkeypatch.setattr(
        cli,
        "resolve_auth_bundle",
        lambda config: SimpleNamespace(auth_token="token", ct0="ct0"),
    )

    captured = {}

    async def fake_refresh_articles(**kwargs):
        assert kwargs["targets"] == ["https://x.com/example/status/2026531440414925307"]
        assert kwargs["preview_only"] is True
        captured["detail_delay"] = kwargs["config"].sync.detail_delay
        return SimpleNamespace(processed=1, updated=1, failed=0)

    monkeypatch.setattr(cli, "refresh_articles", fake_refresh_articles)

    cli.refresh_archived_articles(["https://x.com/example/status/2026531440414925307"])

    output = buffer.getvalue()
    assert captured == {"detail_delay": 0}
    assert "articles: 1 processed, 1 refreshed, 0 failed" in output


def test_expand_archive_threads_reports_runner_result(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    monkeypatch.setattr(
        cli,
        "resolve_auth_bundle",
        lambda config: SimpleNamespace(auth_token="token", ct0="ct0"),
    )

    async def fake_expand_threads(**kwargs):
        assert kwargs["targets"] == ["https://x.com/example/status/2026531440414925307"]
        assert kwargs["limit"] == 5
        assert kwargs["refresh"] is False
        return SimpleNamespace(processed=2, expanded=2, skipped=1, failed=0)

    monkeypatch.setattr(cli, "expand_threads", fake_expand_threads)

    cli.expand_archive_threads(
        ["https://x.com/example/status/2026531440414925307"],
        limit=5,
    )

    output = buffer.getvalue()
    assert "threads: 2 processed, 2 expanded, 1 skipped, 0 failed" in output


def test_expand_archive_threads_forwards_refresh_flag(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    monkeypatch.setattr(
        cli,
        "resolve_auth_bundle",
        lambda config: SimpleNamespace(auth_token="token", ct0="ct0"),
    )

    async def fake_expand_threads(**kwargs):
        assert kwargs["targets"] == ["100"]
        assert kwargs["refresh"] is True
        assert kwargs["config"].sync.detail_delay == 0
        return SimpleNamespace(processed=1, expanded=1, skipped=0, failed=0)

    monkeypatch.setattr(cli, "expand_threads", fake_expand_threads)

    cli.expand_archive_threads(["100"], refresh=True)

    assert "threads: 1 processed, 1 expanded, 0 skipped, 0 failed" in buffer.getvalue()


def test_import_x_archive_reports_runner_result(paths, monkeypatch, tmp_path: Path) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    captured = {}

    async def fake_import_x_archive(
        archive,
        *,
        offline=False,
        regen=False,
        enrich=False,
        detail_lookups=0,
        sample_limit=None,
        debug=False,
        config=None,
        paths=None,
        auth_bundle=None,
        console=None,
    ):
        captured.update(
            {
                "archive": archive,
                "regen": regen,
                "enrich": enrich,
                "detail_lookups": detail_lookups,
                "sample_limit": sample_limit,
                "debug": debug,
                "detail_delay": config.sync.detail_delay if config is not None else None,
                "auth_bundle": auth_bundle,
            }
        )
        return SimpleNamespace(
            skipped=False,
            followup_performed=True,
            counts={
                "authored_tweets": 2,
                "deleted_authored_tweets": 1,
                "likes": 3,
                "media_files_copied": 4,
            },
            warnings=["archive does not contain a bookmark dataset"],
            reconciled_collections=["tweets", "likes"],
            detail_lookups=5,
            detail_terminal_unavailable=1,
            detail_transient_failures=2,
            pending_enrichment=9,
        )

    monkeypatch.setattr(cli, "import_x_archive", fake_import_x_archive)
    archive_path = tmp_path / "archive.zip"
    archive_path.write_bytes(b"placeholder")

    cli.import_x_archive_command(
        archive_path,
        regen=True,
        detail_lookups=25,
        sample_limit=100,
        debug=True,
    )

    assert captured == {
        "archive": archive_path,
        "regen": True,
        "enrich": True,
        "detail_lookups": 25,
        "sample_limit": 100,
        "debug": True,
        "detail_delay": 0,
        "auth_bundle": None,
    }
    output = " ".join(buffer.getvalue().split())
    assert "archive import: 2 authored, 1 deleted authored, 3 likes, 4 media files copied" in output
    assert "live reconciliation: tweets, likes" in output
    assert (
        "detail enrichment: 5 refreshed, 1 terminal, 2 transient failures, "
        "9 pending untouched, 0 transient due, 0 transient delayed"
    ) in output
    assert "archive does not contain a bookmark dataset" in output


def test_import_x_archive_interrupt_reports_completed_import_and_continuation(
    paths, monkeypatch, tmp_path: Path
) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))

    async def fake_import_x_archive(*_args, **_kwargs):
        raise cli.ArchiveEnrichmentInterrupted(12_481)

    monkeypatch.setattr(cli, "import_x_archive", fake_import_x_archive)
    archive_path = tmp_path / "archive.zip"
    archive_path.write_bytes(b"placeholder")

    with pytest.raises(typer.Exit) as excinfo:
        cli.import_x_archive_command(archive_path)

    assert excinfo.value.exit_code == 130
    output = buffer.getvalue()
    assert "Archive import is complete." in output
    assert "Archive enrichment was interrupted." in output
    assert "12,481 sparse archive tweets remain incomplete." in output
    assert "tweetnook import enrich" in output


def test_archive_followup_explains_when_only_delayed_transient_rows_remain() -> None:
    result = SimpleNamespace(
        reconciled_collections=[],
        selected=0,
        detail_lookups=0,
        detail_terminal_unavailable=0,
        detail_transient_failures=0,
        pending_enrichment=2_827,
        pending_untouched=0,
        transient_due=0,
        transient_delayed=2_827,
        warnings=[],
    )

    output = cli._archive_followup_summary(result)

    assert "0 pending untouched" in output
    assert "2,827 transient delayed" in output


def test_import_x_archive_enrich_reuses_existing_import(paths, monkeypatch, tmp_path: Path) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    captured = {}

    async def fake_import_x_archive(
        archive,
        *,
        offline=False,
        regen=False,
        enrich=False,
        detail_lookups=0,
        sample_limit=None,
        debug=False,
        config=None,
        paths=None,
        auth_bundle=None,
        console=None,
    ):
        captured.update(
            {
                "archive": archive,
                "regen": regen,
                "enrich": enrich,
                "detail_lookups": detail_lookups,
                "sample_limit": sample_limit,
                "debug": debug,
            }
        )
        return SimpleNamespace(
            skipped=True,
            followup_performed=True,
            counts={
                "authored_tweets": 2,
                "deleted_authored_tweets": 1,
                "likes": 3,
                "media_files_copied": 4,
            },
            warnings=[],
            reconciled_collections=["likes"],
            detail_lookups=7,
            detail_terminal_unavailable=0,
            detail_transient_failures=1,
            pending_enrichment=5,
        )

    monkeypatch.setattr(cli, "import_x_archive", fake_import_x_archive)
    archive_path = tmp_path / "archive.zip"
    archive_path.write_bytes(b"placeholder")

    cli.import_x_archive_command(archive_path, enrich=True)

    assert captured == {
        "archive": archive_path,
        "regen": False,
        "enrich": True,
        "detail_lookups": 0,
        "sample_limit": None,
        "debug": False,
    }
    output = " ".join(buffer.getvalue().split())
    assert "already present; keeping existing imported data and running" in output
    assert "follow-up enrichment" in output
    assert "live reconciliation: likes" in output
    assert "detail enrichment: 7 refreshed, 0 terminal, 1 transient failures" in output
    assert "5 pending untouched" in output


def test_import_enrich_runs_followup_for_existing_imports(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    monkeypatch.setattr(
        cli,
        "resolve_auth_bundle",
        lambda config: SimpleNamespace(auth_token="token", ct0="ct0"),
    )
    captured = {}

    async def fake_enrich_imported_archive(
        *,
        limit=None,
        config=None,
        paths=None,
        auth_bundle=None,
        transport=None,
        console=None,
    ):
        captured.update(
            {
                "limit": limit,
                "auth_bundle": auth_bundle,
                "detail_delay": config.sync.detail_delay if config is not None else None,
            }
        )
        return SimpleNamespace(
            warnings=["detail enrichment failed: upstream 429"],
            reconciled_collections=["tweets", "likes"],
            detail_lookups=9,
            detail_terminal_unavailable=2,
            detail_transient_failures=1,
            pending_enrichment=4,
        )

    monkeypatch.setattr(cli, "enrich_imported_archive", fake_enrich_imported_archive)

    cli.import_archive_enrich(limit=50)

    assert captured["limit"] == 50
    assert captured["auth_bundle"].auth_token == "token"
    assert captured["detail_delay"] == 0
    output = " ".join(buffer.getvalue().split())
    assert "archive enrich: existing imported archive data" in output
    assert "live reconciliation: tweets, likes" in output
    assert "detail enrichment: 9 refreshed, 2 terminal, 1 transient failures" in output
    assert "4 pending untouched" in output
    assert "detail enrichment failed: upstream 429" in output


@pytest.mark.parametrize(
    ("raised", "exit_code", "expected"),
    [
        (
            cli.ArchiveEnrichmentInterrupted(12_481),
            130,
            "Archive enrichment was interrupted.",
        ),
        (
            cli.ArchiveEnrichmentAborted(17, TypeError("parser bug")),
            2,
            "Archive enrichment stopped after TypeError: parser bug",
        ),
    ],
)
def test_import_enrich_reports_interruption_or_systemic_abort(
    paths, monkeypatch, raised, exit_code: int, expected: str
) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    monkeypatch.setattr(
        cli,
        "resolve_auth_bundle",
        lambda config: SimpleNamespace(auth_token="token", ct0="ct0"),
    )

    async def fail_enrichment(**_kwargs):
        raise raised

    monkeypatch.setattr(cli, "enrich_imported_archive", fail_enrichment)

    with pytest.raises(typer.Exit) as excinfo:
        cli.import_archive_enrich()

    assert excinfo.value.exit_code == exit_code
    output = buffer.getvalue()
    assert expected in output
    assert "tweetnook import enrich" in output
    assert ("12,481" if exit_code == 130 else "17") in output


def test_expand_archive_threads_refresh_requires_targets(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    monkeypatch.setattr(
        cli,
        "resolve_auth_bundle",
        lambda config: SimpleNamespace(auth_token="token", ct0="ct0"),
    )

    with pytest.raises(typer.Exit) as excinfo:
        cli.expand_archive_threads(refresh=True)

    assert excinfo.value.exit_code == 1
    assert "--refresh requires one or more explicit thread targets." in buffer.getvalue()


def test_optimize_archive_uses_write_lock(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    lock_calls: list[Path] = []

    class FakeStore:
        def __init__(self) -> None:
            self.optimized = False
            self.closed = False

        def optimize(self) -> None:
            self.optimized = True

        def close(self) -> None:
            self.closed = True

    store = FakeStore()
    monkeypatch.setattr(cli, "open_archive_store", lambda _paths, create=False, config=None: store)
    monkeypatch.setattr(
        cli,
        "_with_archive_write_lock",
        lambda lock_paths, fn: (lock_calls.append(lock_paths.lock_file), fn())[1],
    )

    cli.optimize_archive()

    assert lock_calls == [paths.lock_file]
    assert store.optimized is True
    assert store.closed is True
    assert "vacuuming database..." in buffer.getvalue()
    assert "vacuum complete." in buffer.getvalue()


@pytest.mark.parametrize(
    ("full", "expected_pragma"),
    [(False, "quick_check"), (True, "integrity_check")],
)
def test_database_check_runs_requested_diagnostic_and_closes_store(
    paths,
    monkeypatch: pytest.MonkeyPatch,
    full: bool,
    expected_pragma: str,
) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)

    class FakeStore:
        def __init__(self) -> None:
            self.full_values: list[bool] = []
            self.closed = False

        def check_integrity(self, *, full: bool = False) -> list[str]:
            self.full_values.append(full)
            return ["ok"]

        def close(self) -> None:
            self.closed = True

    store = FakeStore()
    monkeypatch.setattr(cli, "_open_store_for_read", lambda _console: (store, paths))

    cli.check_database(full=full)

    assert store.full_values == [full]
    assert store.closed is True
    assert f"database {expected_pragma}: ok" in buffer.getvalue()


def test_database_check_reports_integrity_problems(
    paths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)

    class FakeStore:
        def __init__(self) -> None:
            self.closed = False

        def check_integrity(self, *, full: bool = False) -> list[str]:
            assert full is False
            return ["row 10 missing from index"]

        def close(self) -> None:
            self.closed = True

    store = FakeStore()
    monkeypatch.setattr(cli, "_open_store_for_read", lambda _console: (store, paths))

    with pytest.raises(typer.Exit) as excinfo:
        cli.check_database()

    assert excinfo.value.exit_code == 2
    assert store.closed is True
    assert "row 10 missing from index" in buffer.getvalue()


def test_stats_archive_renders_summary_tables(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    _seed_archive(paths)

    cli.stats_archive()

    output = buffer.getvalue()
    normalized = " ".join(output.split())
    assert "TweetNook statistics" in normalized
    assert "View summary" in normalized
    assert "Archive" in normalized
    assert "Unique tweets" in normalized
    assert "Collection" in normalized
    assert "memberships" in normalized
    assert "Collections" in normalized
    assert "Storage" in normalized
    assert "Database & Indexes" in normalized
    assert "Archive health" in normalized
    assert "Tagging & search" not in normalized
    assert "Local rehydrate gaps" in normalized
    assert "Thread memberships pending" in normalized
    assert "related pending" in normalized
    assert "Versions" not in normalized
    assert "Optimize" not in normalized
    assert "Follow-up" not in normalized


def test_stats_archive_builds_a_fresh_report_on_every_invocation(paths, monkeypatch) -> None:
    class Store:
        def close(self) -> None:
            pass

    store = Store()
    reports: list[object] = []
    rendered: list[object] = []
    monkeypatch.setattr(cli, "_configure_logging", lambda: object())
    monkeypatch.setattr(cli, "_open_store_for_read", lambda _console: (store, paths))

    def build(store_arg):
        assert store_arg is store
        report = object()
        reports.append(report)
        return report

    monkeypatch.setattr(cli, "build_stats_report", build)
    monkeypatch.setattr(
        cli,
        "render_stats_report",
        lambda _console, report, *, detailed: rendered.append(report),
    )

    cli.stats_archive()
    cli.stats_archive()

    assert len(reports) == 2
    assert reports[0] is not reports[1]
    assert rendered == reports


def test_stats_archive_detailed_shows_full_storage_and_hidden_rows(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    _seed_archive(paths)

    cli.stats_archive(detailed=True)

    normalized = " ".join(buffer.getvalue().split())
    assert "TweetNook statistics" in normalized
    assert "View detailed" in normalized
    assert "Core Tweet Database" in normalized
    assert "Protected account" in normalized
    assert "Enrichment pending" in normalized


def test_rehydrate_archive_uses_write_lock(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    lock_calls: list[Path] = []

    class FakeTqdm:
        def __init__(self, *args, **kwargs) -> None:
            self.updates: list[int] = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def update(self, count: int) -> None:
            self.updates.append(count)

    class FakeStore:
        def __init__(self) -> None:
            self.table = SimpleNamespace(count_rows=lambda expr: 2)
            self.optimized = False
            self.closed = False

        def _count(self, filter_expr: str | None = None) -> int:
            return 2

        def rehydrate_from_raw_json(self, *, progress=None):
            if progress is not None:
                progress(2)
            return SimpleNamespace(tweets_updated=2, secondary_records=5)

        def optimize(self) -> None:
            self.optimized = True

        def close(self) -> None:
            self.closed = True

    store = FakeStore()
    monkeypatch.setattr(cli, "open_archive_store", lambda _paths, create=False, config=None: store)
    monkeypatch.setattr(
        cli,
        "_with_archive_write_lock",
        lambda lock_paths, fn: (lock_calls.append(lock_paths.lock_file), fn())[1],
    )
    monkeypatch.setitem(sys.modules, "tqdm", SimpleNamespace(tqdm=FakeTqdm))

    cli.rehydrate_archive()

    assert lock_calls == [paths.lock_file]
    assert store.closed is True
    assert "rehydrated 2 tweet rows and rebuilt 5 secondary rows" in buffer.getvalue()
