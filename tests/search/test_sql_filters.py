from __future__ import annotations

import json
from typing import Any

import pytest

from tweetnook.storage.backend import ArchiveStore
from tweetnook.storage.search_query import (
    _parse_twitter_date,
    search_posts,
)


def test_real_store_pushes_normalized_filters_into_sql(tmp_path) -> None:
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    store._merge_records(
        [
            store._record(
                row_key="tweet:bookmark::1",
                record_type="tweet",
                tweet_id="1",
                collection_type="bookmark",
                text="needle cats in sqlite",
                author_id="alice",
                author_username="alice",
                author_display_name="Alice",
                created_at="Thu Jan 01 00:00:00 +0000 2026",
                created_at_ts=1,
                sort_index="1",
                raw_json=json.dumps(
                    {
                        "legacy": {
                            "favorite_count": 20,
                            "in_reply_to_status_id_str": "parent",
                            "in_reply_to_screen_name": "alice",
                            "is_quote_status": True,
                            "quoted_status_id_str": "quoted",
                            "entities": {
                                "hashtags": [{"text": "Python"}],
                                "user_mentions": [{"screen_name": "Bob"}],
                            },
                        },
                        "source": "Twitter Web App",
                        "card": {"name": "summary"},
                    }
                ),
            ),
            store._record(
                row_key="tweet:bookmark::2",
                record_type="tweet",
                tweet_id="2",
                collection_type="bookmark",
                text="needle dogs in sqlite",
                author_id="bob",
                author_username="bob",
                author_display_name="Bob",
                created_at="Fri Jan 02 00:00:00 +0000 2026",
                created_at_ts=2,
                sort_index="2",
                raw_json=json.dumps(
                    {
                        "legacy": {},
                        "core": {"user_results": {"result": {"is_blue_verified": True}}},
                    }
                ),
            ),
            store._record(
                row_key="tweet:like::3",
                record_type="tweet",
                tweet_id="3",
                collection_type="like",
                text="unrelated post",
                author_id="carol",
                author_username="carol",
                author_display_name="Carol",
                created_at="Sat Jan 03 00:00:00 +0000 2026",
                created_at_ts=3,
                sort_index="3",
                raw_json=json.dumps({"legacy": {}}),
            ),
            store._record(
                row_key="media:1:photo",
                record_type="media",
                tweet_id="1",
                media_key="photo",
                media_type="photo",
            ),
            store._record(
                row_key="media:2:video",
                record_type="media",
                tweet_id="2",
                media_key="video",
                media_type="video",
            ),
            store._record(
                row_key="url_ref:2:0",
                record_type="url_ref",
                tweet_id="2",
                position=0,
                expanded_url="https://example.test/story",
                display_url="example.test/story",
            ),
            store._record(
                row_key="article:1",
                record_type="article",
                tweet_id="1",
                title="Attached article",
                content_text="Long form text",
            ),
            store._record(
                row_key="tweet_object:1",
                record_type="tweet_object",
                tweet_id="1",
                enrichment_state="resurrected",
            ),
            store._record(
                row_key="tweet_object:2",
                record_type="tweet_object",
                tweet_id="2",
                enrichment_state="terminal_unavailable",
            ),
        ]
    )

    assert {row["tweet_id"] for row in search_posts(store, "has:media").rows} == {"1", "2"}
    assert [row["tweet_id"] for row in search_posts(store, "has:image").rows] == ["1"]
    assert [row["tweet_id"] for row in search_posts(store, "filter:images").rows] == ["1"]
    assert [row["tweet_id"] for row in search_posts(store, "has:video").rows] == ["2"]
    assert [row["tweet_id"] for row in search_posts(store, "filter:videos").rows] == ["2"]
    assert [row["tweet_id"] for row in search_posts(store, "filter:native_video").rows] == ["2"]
    assert [row["tweet_id"] for row in search_posts(store, "has:links").rows] == ["2"]
    assert [row["tweet_id"] for row in search_posts(store, "filter:links").rows] == ["2"]
    assert [row["tweet_id"] for row in search_posts(store, "filter:articles").rows] == ["1"]
    assert [row["tweet_id"] for row in search_posts(store, "has:article").rows] == ["1"]
    assert [row["tweet_id"] for row in search_posts(store, "is:resurrected").rows] == ["1"]
    assert [row["tweet_id"] for row in search_posts(store, "is:missing").rows] == ["2"]
    assert {row["tweet_id"] for row in search_posts(store, "-is:missing").rows} == {"1", "3"}
    assert {row["tweet_id"] for row in search_posts(store, "is:resurrected OR has:video").rows} == {
        "1",
        "2",
    }

    store.export_rows = lambda *_args, **_kwargs: pytest.fail("export_rows must not be called")

    assert {row["tweet_id"] for row in search_posts(store, "-filter:videos").rows} == {"1", "3"}
    assert [row["tweet_id"] for row in search_posts(store, "is:reply").rows] == ["1"]
    assert [row["tweet_id"] for row in search_posts(store, "is:quote").rows] == ["1"]
    assert [row["tweet_id"] for row in search_posts(store, "is:thread").rows] == ["1"]
    assert [row["tweet_id"] for row in search_posts(store, "is:verified").rows] == ["2"]
    assert [row["tweet_id"] for row in search_posts(store, "to:alice").rows] == ["1"]
    assert [row["tweet_id"] for row in search_posts(store, "mentions:bob").rows] == ["1"]
    assert [row["tweet_id"] for row in search_posts(store, "#python").rows] == ["1"]
    assert [row["tweet_id"] for row in search_posts(store, "source:twitter_web").rows] == ["1"]
    assert [row["tweet_id"] for row in search_posts(store, "card_name:summary").rows] == ["1"]
    assert [row["tweet_id"] for row in search_posts(store, "quoted_tweet_id:quoted").rows] == ["1"]
    assert {row["tweet_id"] for row in search_posts(store, "-quoted_tweet_id:quoted").rows} == {
        "2",
        "3",
    }
    assert [row["tweet_id"] for row in search_posts(store, "url:example.test/story").rows] == ["2"]
    assert {row["tweet_id"] for row in search_posts(store, "needle OR unrelated").rows} == {
        "1",
        "2",
        "3",
    }
    assert [row["tweet_id"] for row in search_posts(store, "-needle").rows] == ["3"]

    statements: list[str] = []
    store.conn.set_trace_callback(statements.append)
    search_posts(store, "has:image is:reply")
    store.conn.set_trace_callback(None)
    traced_sql = " ".join(statements)
    assert "related INDEXED BY idx_archive_search_attachment" in traced_sql
    assert "SELECT facts.is_reply FROM archive_tweet_facts" in traced_sql
    assert "json_extract" not in traced_sql.split("SELECT archive.tweet_id, MAX(")[1]

    text_media = search_posts(store, "needle has:media")
    assert {row["tweet_id"] for row in text_media.rows} == {"1", "2"}
    assert [row["tweet_id"] for row in text_media.rows] == [
        row["tweet_id"] for row in search_posts(store, "needle").rows
    ]

    hydrated_ids: list[list[str]] = []
    original_fetch = store.fetch_tweets_by_ids

    def track_fetch(tweet_ids: list[str]) -> list[dict[str, Any]]:
        hydrated_ids.append(tweet_ids)
        return original_fetch(tweet_ids)

    store.fetch_tweets_by_ids = track_fetch
    popular = search_posts(store, "needle min_faves:10")
    assert [row["tweet_id"] for row in popular.rows] == ["1"]
    assert hydrated_ids[-1] == ["1"]
    store.close()


def _operator_store(tmp_path) -> ArchiveStore:
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    rows = [
        (
            "1",
            "alice",
            1_767_225_600,  # 2026-01-01T00:00:00Z
            {
                "legacy": {
                    "retweet_count": 5,
                    "reply_count": 1,
                    "in_reply_to_status_id_str": "parent",
                    "in_reply_to_screen_name": "alice",
                    "is_quote_status": True,
                }
            },
            "conv-1",
        ),
        (
            "2",
            "bob",
            1_767_355_200,  # 2026-01-02T12:00:00Z
            {
                "legacy": {"retweeted_status_id_str": "original", "reply_count": 3},
                "core": {"user_results": {"result": {"legacy": {"verified": True}}}},
            },
            "conv-2",
        ),
        (
            "3",
            None,
            1_767_398_400,  # 2026-01-03T00:00:00Z
            {"legacy": {"in_reply_to_status_id_str": "other", "in_reply_to_screen_name": "alice"}},
            "conv-3",
        ),
    ]
    store._merge_records(
        [
            store._record(
                row_key=f"tweet:bookmark::{tweet_id}",
                record_type="tweet",
                tweet_id=tweet_id,
                collection_type="bookmark",
                text=f"post {tweet_id}",
                author_id=username or "",
                author_username=username,
                created_at_ts=created_at_ts,
                sort_index=tweet_id,
                conversation_id=conversation_id,
                raw_json=json.dumps(raw),
            )
            for tweet_id, username, created_at_ts, raw, conversation_id in rows
        ]
    )
    return store


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("since:2026-01-02", {"2", "3"}),
        ("until:2026-01-02", {"1"}),
        ("since_time:1767355200", {"2", "3"}),
        ("until_time:1767355200", {"1"}),
        ("since_id:1", {"2", "3"}),
        ("max_id:2", {"1", "2"}),
        ("min_retweets:5", {"1"}),
        ("min_replies:2", {"2"}),
        ("conversation_id:conv-1", {"1"}),
        ("is:retweet", {"2"}),
        ("filter:nativeretweets", {"2"}),
        ("filter:quote", {"1"}),
        ("filter:replies", {"1", "3"}),
        # Tweet 3 replies to alice but has no author username, so it is not a self-thread.
        ("is:thread", {"1"}),
        ("filter:threads", {"1"}),
        ("filter:verified", {"2"}),
    ],
)
def test_real_store_applies_each_remaining_operator_in_sql(
    tmp_path, query: str, expected: set[str]
) -> None:
    store = _operator_store(tmp_path)
    try:
        assert {row["tweet_id"] for row in search_posts(store, query).rows} == expected
    finally:
        store.close()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-01-02", 1_767_312_000),
        ("2026-01-02_03:04:05", 1_767_323_045),
        ("", None),
        ("2026-99-99", None),
        ("not-a-date", None),
    ],
)
def test_parse_twitter_date(value: str, expected: float | None) -> None:
    assert _parse_twitter_date(value) == expected


def test_real_store_hydrates_only_the_requested_fts_page(tmp_path) -> None:
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    store._merge_records(
        [
            store._record(
                row_key=f"tweet:bookmark::{tweet_id}",
                record_type="tweet",
                tweet_id=str(tweet_id),
                collection_type="bookmark",
                text="shared candidate token",
                created_at_ts=tweet_id,
                sort_index=str(tweet_id),
                raw_json=json.dumps({"legacy": {}}),
            )
            for tweet_id in range(1, 31)
        ]
    )
    hydrated_ids: list[list[str]] = []
    original_fetch = store.fetch_tweets_by_ids

    def track_fetch(tweet_ids: list[str]) -> list[dict[str, Any]]:
        hydrated_ids.append(tweet_ids)
        return original_fetch(tweet_ids)

    store.fetch_tweets_by_ids = track_fetch

    page = search_posts(store, "candidate", limit=5)

    assert len(page.rows) == 5
    assert page.total is None
    assert page.has_more is True
    assert len(hydrated_ids) == 1
    assert len(hydrated_ids[0]) == 5
    store.close()
