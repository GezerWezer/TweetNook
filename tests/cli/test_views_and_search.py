from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

import tweetnook.cli as cli
from tests.cli.conftest import _capture_console, _seed_archive, runner
from tests.conftest import patch_cli
from tweetnook.cli import view as cli_view
from tweetnook.config import AppConfig
from tweetnook.storage import open_archive_store


def test_view_bookmarks_prints_rows(paths, monkeypatch) -> None:
    _seed_archive(paths)
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    patch_cli(monkeypatch, "load_config", lambda: (AppConfig(), paths))
    patch_cli(monkeypatch, "_format_created_at", lambda raw: "LOCAL-TIME")

    cli_view.view_bookmarks(limit=5)

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
    patch_cli(monkeypatch, "load_config", lambda: (AppConfig(), paths))

    cli_view.view_tweets(limit=5)

    output = " ".join(buffer.getvalue().split())
    assert "authored tweet" in output
    assert "tweets archive" in output
    assert "bookmark tweet" not in output


def test_highlight_search_matches_marks_query_terms() -> None:
    rendered = cli_view._highlight_search_matches(
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
    patch_cli(monkeypatch, "_format_created_at", lambda raw: "LOCAL-TIME")
    patch_cli(monkeypatch, "_with_auto_optimize", lambda store, paths, console, fn: fn(store))

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

    patch_cli(monkeypatch, "_open_store_for_read", lambda console: (_FakeStore(), object()))
    patch_cli(monkeypatch, "search_posts", fake_search_posts)

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


@pytest.mark.parametrize("types", ["article", "post,article"])
@pytest.mark.parametrize("query", ["cats -coffee", "cats OR dogs -coffee"])
def test_article_cli_uses_shared_query_and_hides_saved_posts(paths, monkeypatch, types, query):
    store = open_archive_store(paths, create=True)
    assert store is not None
    rows = []
    for tweet_id, text in [("900001", "cats"), ("900002", "cats coffee"), ("900003", "cats")]:
        rows.extend(
            [
                store._record(
                    row_key=f"tweet:bookmark::{tweet_id}",
                    record_type="tweet",
                    tweet_id=tweet_id,
                    collection_type="bookmark",
                    text=text,
                ),
                store._record(
                    row_key=f"article:{tweet_id}",
                    record_type="article",
                    tweet_id=tweet_id,
                    title=text,
                ),
            ]
        )
    store.merge_rows(rows)
    store.set_tweet_hidden("900003", True)
    patch_cli(monkeypatch, "_open_store_for_read", lambda console: (store, paths))
    patch_cli(monkeypatch, "_with_auto_optimize", lambda store, paths, console, fn: fn(store))

    result = runner.invoke(cli.app, ["search", query, "--type", types])

    assert result.exit_code == 0, result.output
    assert "900001" in result.output
    assert "900002" not in result.output
    assert "900003" not in result.output
    assert "article" in result.output


@pytest.mark.parametrize("types", ["article", "post,article"])
@pytest.mark.parametrize(("sort", "expected"), [("oldest", "910001"), ("newest", "910003")])
def test_article_cli_sorts_all_matches_before_limiting(paths, monkeypatch, types, sort, expected):
    store = open_archive_store(paths, create=True)
    rows = []
    for tweet_id, year, text in [
        ("910001", 2024, "coffee"),
        ("910002", 2025, "coffee " * 10),
        ("910003", 2026, "coffee"),
        ("910004", None, "coffee " * 20),
    ]:
        rows.extend(
            [
                store._record(
                    row_key=f"tweet:bookmark::{tweet_id}",
                    record_type="tweet",
                    tweet_id=tweet_id,
                    collection_type="bookmark",
                    text="saved post",
                    created_at=f"Sat Mar 14 00:00:00 +0000 {year}" if year else None,
                ),
                store._record(
                    row_key=f"article:{tweet_id}",
                    record_type="article",
                    tweet_id=tweet_id,
                    title=text,
                ),
            ]
        )
    store.merge_rows(rows)
    patch_cli(monkeypatch, "_open_store_for_read", lambda console: (store, paths))
    patch_cli(monkeypatch, "_with_auto_optimize", lambda store, paths, console, fn: fn(store))

    result = runner.invoke(
        cli.app, ["search", "coffee", "--type", types, "--sort", sort, "--limit", "1"]
    )

    assert result.exit_code == 0, result.output
    assert expected in result.output
    for tweet_id in {"910001", "910002", "910003", "910004"} - {expected}:
        assert tweet_id not in result.output


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

    newest = cli_view._sort_search_results(rows, sort="newest")
    oldest = cli_view._sort_search_results(rows, sort="oldest")

    assert [row["tweet_id"] for row in newest] == ["2", "1", "3"]
    assert [row["tweet_id"] for row in oldest] == ["1", "2", "3"]


def test_export_json_accepts_plural_collection_name(paths, monkeypatch, tmp_path: Path) -> None:
    _seed_archive(paths)
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    patch_cli(monkeypatch, "load_config", lambda: (AppConfig(), paths))
    out_path = tmp_path / "bookmarks.json"

    cli_view.export_json(collection="bookmarks", out=out_path)

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert [row["tweet_id"] for row in payload] == ["1"]
    assert "exported bookmarks archive" in buffer.getvalue()


def test_export_json_accepts_tweets_collection_name(paths, monkeypatch, tmp_path: Path) -> None:
    _seed_archive(paths)
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    patch_cli(monkeypatch, "load_config", lambda: (AppConfig(), paths))
    out_path = tmp_path / "tweets.json"

    cli_view.export_json(collection="tweets", out=out_path)

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert [row["tweet_id"] for row in payload] == ["3"]
    assert "exported tweets archive" in buffer.getvalue()
