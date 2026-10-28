"""Helpers and fixtures shared by the cli tests."""

from __future__ import annotations

from io import StringIO

from rich.console import Console
from typer.testing import CliRunner

from tests.conftest import patch_cli
from tweetnook.core.timeline_parsing import TimelineTweet
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
    patch_cli(
        monkeypatch,
        "configure_logging",
        lambda: Console(file=buffer, force_terminal=False, color_system=None),
    )
