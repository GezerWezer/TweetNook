from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_web_tweets_api import _api_client
from tweetnook.search import search_posts
from tweetnook.storage.backend import ArchiveStore


def test_most_likes_browser_and_demo_suite():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the Most likes browser harness")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [node, str(root / "tests/js/test_most_likes.cjs")],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture
def store(tmp_path):
    archive = ArchiveStore(tmp_path / "archive.db", create=True)
    rows = []
    # Deliberately disagree with date/ID order; ties, zero, missing and duplicate
    # memberships exercise real SQLite facts and logical-ID aggregation.
    for tweet_id, likes in [("1", 90), ("2", 10), ("3", 90), ("4", None), ("5", 0)]:
        for collection in ["bookmark", "like"]:
            raw = {"legacy": {"favorite_count": likes}} if likes is not None else {}
            rows.append(
                archive._record(
                    row_key=f"tweet:{collection}::{tweet_id}",
                    record_type="tweet",
                    tweet_id=tweet_id,
                    collection_type=collection,
                    text="needle sample",
                    author_username="alice",
                    author_id="a1",
                    sort_index=tweet_id,
                    created_at_ts=100,
                    created_at="Sat Mar 14 00:00:00 +0000 2026",
                    raw_json=json.dumps(raw),
                )
            )
    archive._merge_records(rows)
    yield archive
    archive.close()


def ids(page):
    return [row["tweet_id"] for row in page.rows]


@pytest.mark.parametrize(
    "query",
    [None, "needle", "from:alice", "needle from:alice", "needle OR from:alice", "-from:nobody"],
)
@pytest.mark.parametrize("collections", [None, {"like"}, {"like", "bookmark"}])
def test_most_likes_orders_complete_query_results_and_stable_pages(store, query, collections):
    expected = ["3", "1", "2", "5", "4"]
    options = dict(sort="most_likes", limit=2, collections=collections)
    first = search_posts(store, query, cursor="", **options)
    second = search_posts(store, query, cursor=first.next_cursor, **options)
    third = search_posts(store, query, cursor=second.next_cursor, **options)
    assert ids(first) + ids(second) + ids(third) == expected
    assert ids(search_posts(store, query, page=2, **options)) == expected[2:4]
    assert first.has_more and second.has_more and not third.has_more
    assert third.next_cursor is None
    assert first.total == (None if query and "needle" in query else 5)


def test_most_likes_hides_hidden_tweets_and_honors_numeric_filters(store):
    store.set_tweet_hidden("3", True)
    assert ids(search_posts(store, None, sort="most_likes")) == ["1", "2", "5", "4"]
    assert ids(search_posts(store, "min_faves:50", sort="most_likes")) == ["1"]
    assert ids(search_posts(store, "is:hidden", sort="most_likes")) == ["3"]


def test_most_likes_count_updates_use_trigger_facts(store):
    store.conn.execute(
        "UPDATE archive SET raw_json = ? WHERE tweet_id = '2'",
        (json.dumps({"legacy": {"favorite_count": 1000}}),),
    )
    assert ids(search_posts(store, "needle", sort="most_likes"))[0] == "2"


def test_most_likes_api_and_cursor_context(store):
    with _api_client(store) as client:
        first = client.get("/api/tweets", params={"sort": "most_likes", "limit": 2, "cursor": ""})
        assert first.status_code == 200
        payload = first.json()
        assert [row["tweet_id"] for row in payload["tweets"]] == ["3", "1"]
        assert payload["total"] == 5
        second = client.get(
            "/api/tweets",
            params={"sort": "most_likes", "limit": 2, "cursor": payload["next_cursor"]},
        )
        assert [row["tweet_id"] for row in second.json()["tweets"]] == ["2", "5"]
        assert (
            client.get(
                "/api/tweets",
                params={"sort": "newest", "limit": 2, "cursor": payload["next_cursor"]},
            ).status_code
            == 400
        )
