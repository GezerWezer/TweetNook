from __future__ import annotations

import pytest

import tweetnook.cli as cli
from tests.cli.conftest import runner
from tests.conftest import patch_cli
from tests.storage_sqlite.conftest import CREATED_2026, _membership
from tweetnook.storage.backend import ArchiveStore
from tweetnook.storage.search_query import search_posts


@pytest.fixture
def ordered_archive(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    # The like keys deliberately disagree with bookmark order. Timestamp and
    # timeline key must come from one representative membership, never two MAXs.
    rows = [
        _membership(
            store,
            tweet_id,
            collection=collection,
            text="coffee",
            created_at=CREATED_2026,
            created_at_ts=200 if collection == "like" and tweet_id == "1" else timestamp,
            sort_index=index,
        )
        for tweet_id, timestamp, bookmark, like in [
            ("1", 100, "1", "100"),
            ("2", 100, "2", "3"),
            ("3", 100, "2", "3"),
            ("4", None, None, "200"),
            ("5", None, "1", None),
            ("6", 50, None, None),
        ]
        for collection, index in [("bookmark", bookmark), ("like", like)]
    ]
    store.merge_rows(rows)
    yield store
    store.close()


@pytest.mark.parametrize("query", ["", "from:alice", "coffee", "coffee from:alice"])
@pytest.mark.parametrize("collections", [None, {"bookmark"}, {"like"}, {"bookmark", "like"}])
@pytest.mark.parametrize("sort", ["newest", "oldest"])
def test_chronology_agrees_across_executors_and_continuations(
    ordered_archive, query, collections, sort
):
    if collections == {"like"}:
        expected = (
            ["1", "3", "2", "6", "4", "5"] if sort == "newest" else ["6", "2", "3", "1", "5", "4"]
        )
    else:
        expected = (
            ["3", "2", "1", "6", "5", "4"] if sort == "newest" else ["6", "1", "2", "3", "4", "5"]
        )
    store = ordered_archive
    whole = search_posts(store, query, collections=collections, sort=sort, limit=20)
    assert [row["tweet_id"] for row in whole.rows] == expected
    for size in (1, 2, 3, 4):
        ids, cursor = [], ""
        while cursor is not None:
            page = search_posts(
                store, query, collections=collections, sort=sort, limit=size, cursor=cursor
            )
            ids.extend(row["tweet_id"] for row in page.rows)
            cursor = page.next_cursor
        assert ids == expected
        offset_ids = [
            row["tweet_id"]
            for number in range(1, 1 + (len(expected) + size - 1) // size)
            for row in search_posts(
                store, query, collections=collections, sort=sort, limit=size, page=number
            ).rows
        ]
        assert offset_ids == expected
    if collections == {"like"}:
        assert [row["collection"]["sort_index"] for row in whole.rows] == [
            {"1": "100", "2": "3", "3": "3", "4": "200", "5": None, "6": None}[id]
            for id in expected
        ]


@pytest.mark.parametrize("sort", ["newest", "oldest"])
def test_shared_projection_retains_logical_tie_keys(ordered_archive, sort):
    store = ordered_archive
    expected = (
        ["3", "2", "1", "6", "5", "4"] if sort == "newest" else ["6", "1", "2", "3", "4", "5"]
    )
    results = store.search_fts("coffee", types={"post"}, sort=sort, limit=20)
    assert [row["tweet_id"] for row in results] == expected
    # Article and post projections use identical keys without publishing them.
    article_rows = [dict(row, type="article", match_score=100) for row in reversed(results)]
    merged = store.order_search_results(results + article_rows, sort=sort)
    assert [row["tweet_id"] for row in merged] == [id for id in expected for _ in range(2)]


@pytest.mark.parametrize("sort", ["newest", "oldest"])
def test_post_only_cli_preserves_sql_ties_and_count(ordered_archive, monkeypatch, sort):
    captured = {}
    patch_cli(monkeypatch, "open_store_for_read", lambda console: (ordered_archive, object()))
    patch_cli(monkeypatch, "_render_tweet_list", lambda console, **kwargs: captured.update(kwargs))
    result = runner.invoke(cli.app, ["search", "from:alice", "--sort", sort, "--limit", "3"])
    assert result.exit_code == 0, result.output
    expected = ["3", "2", "1"] if sort == "newest" else ["6", "1", "2"]
    assert [row.tweet_id for row in captured["rows"]] == expected
    assert captured["count_line"] == "showing 3 of 6 search results"


@pytest.mark.parametrize("types", ["article", "post,article"])
def test_cli_article_queries_keep_structured_filter_restriction(
    ordered_archive, monkeypatch, types
):
    patch_cli(monkeypatch, "open_store_for_read", lambda console: (ordered_archive, object()))
    result = runner.invoke(cli.app, ["search", "from:alice", "--type", types])
    assert result.exit_code == 1
    assert "Structured filters apply to tweets" in result.output
