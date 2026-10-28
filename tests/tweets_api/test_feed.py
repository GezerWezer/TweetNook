from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi")

from tests.tweets_api.conftest import ListingStore, _list_tweets  # noqa: E402
from tweetnook.storage.backend import ArchiveStore  # noqa: E402


def _row(**overrides: Any) -> dict[str, Any]:
    row = {
        "tweet_id": "200",
        "text": "A post",
        "author": {"id": "u1", "username": "alice", "display_name": "Alice"},
        "created_at": "2026-01-02T00:00:00Z",
        "created_at_ts": 1_767_312_000,
        "sort_index": "20",
        "media": [
            {"type": "photo", "download": {"local_path": "media/photo.jpg"}},
            {"type": "video", "download": {"local_path": "media/video.mp4"}},
        ],
        "urls": [
            {
                "expanded_url": "https://example.com/Story",
                "display_url": "example.com/Story",
            }
        ],
        "media_tags": {"tags": ["Landscape", "Night"]},
        "enrichment_state": "resurrected",
        "raw_json": {
            "source": '<a href="https://example.test">Twitter Web App</a>',
            "card": {"name": "summary"},
            "core": {
                "user_results": {
                    "result": {"is_blue_verified": True, "legacy": {"verified": False}}
                }
            },
            "legacy": {
                "conversation_id_str": "conversation-1",
                "quoted_status_id_str": "quoted-1",
                "in_reply_to_screen_name": "alice",
                "in_reply_to_status_id_str": "100",
                "is_quote_status": True,
                "retweeted_status_id_str": "50",
                "retweet_count": 4,
                "favorite_count": 8,
                "reply_count": 2,
                "entities": {
                    "hashtags": [{"text": "Python"}],
                    "user_mentions": [{"screen_name": "Bob"}],
                },
            },
        },
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize(
    ("sort", "effective_sort"),
    [
        ("default", "newest"),
        ("newest", "newest"),
        ("oldest", "oldest"),
        ("random", "random"),
        ("unexpected", "newest"),
    ],
)
def test_api_tweets_fast_path_uses_bounded_pagination_and_sort(sort: str, effective_sort: str):
    store = ListingStore([_row(tweet_id="1"), _row(tweet_id="2"), _row(tweet_id="3")])

    result = _list_tweets(store, collection="likes", sort=sort, page=2, limit=1)

    assert result["page"] == 2
    assert result["pages"] == 3
    assert result["has_more"] is True
    assert [tweet["tweet_id"] for tweet in result["tweets"]] == ["2"]
    query = next(data for name, data in store.calls if name == "search_tweet_ids")
    assert query["offset"] == 1
    assert query["limit"] == 1
    assert query["sort"] == effective_sort
    assert query["collections"] == {"like"}
    assert query["include_total"] is True


def test_api_tweets_invalid_collection_falls_back_to_all():
    store = ListingStore([_row()])
    _list_tweets(store, collection="not-real")
    query = next(data for name, data in store.calls if name == "search_tweet_ids")
    assert query["collections"] is None


def test_api_tweets_rejects_unknown_search_filters():
    store = ListingStore([_row()])

    with pytest.raises(fastapi.HTTPException) as exc:
        _list_tweets(store, q="unknown:value")

    assert exc.value.status_code == 400
    assert "Unsupported search filter" in exc.value.detail


def test_api_tweets_pushdown_quotes_untrusted_author_and_conversation():
    store = ListingStore([])
    payload = "alice' or 1=1 --"

    _list_tweets(store, q=f'from:"{payload}" conversation_id:"{payload}"')

    query = next(data for name, data in store.calls if name == "search_tweet_ids")
    expressions = " ".join(
        clause["expression"] for group in query["query_groups"] for clause in group
    )
    assert "alice'' or 1=1 --" in expressions
    assert "alice' or 1=1 --'" not in expressions


def test_api_tweets_fts_path_sorts_and_paginates_hydrated_results():
    rows = [
        _row(
            tweet_id="old",
            created_at="Sat Jan 01 00:00:00 +0000 2022",
            created_at_ts=None,
        ),
        _row(
            tweet_id="new",
            created_at="Sat Jan 01 00:00:00 +0000 2026",
            created_at_ts=None,
        ),
    ]
    store = ListingStore(rows, search_rows=list(reversed(rows)))

    result = _list_tweets(store, q="needle", sort="oldest", page=1, limit=1)

    assert [tweet["tweet_id"] for tweet in result["tweets"]] == ["old"]
    assert result["total"] is None
    assert result["pages"] is None
    search = next(data for name, data in store.calls if name == "search_tweet_ids")
    assert search["limit"] == 1
    assert search["offset"] == 0
    assert search["sort"] == "oldest"
    assert search["include_total"] is False


def test_api_tweets_text_search_browses_past_one_thousand(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    store._merge_records(
        [
            store._record(
                row_key=f"tweet:bookmark::{tweet_id}",
                record_type="tweet",
                tweet_id=str(tweet_id),
                collection_type="bookmark",
                text="broad searchable token",
                created_at_ts=tweet_id,
                sort_index=str(tweet_id),
                raw_json=json.dumps({"legacy": {}}),
            )
            for tweet_id in range(1, 1206)
        ]
    )

    result = _list_tweets(store, q="searchable", page=51, limit=20)

    assert len(result["tweets"]) == 20
    assert result["total"] is None
    assert result["pages"] is None
    assert result["has_more"] is True
    assert result["truncated"] is False
    store.close()


def test_api_tweets_post_filter_path_preserves_relevance_order_without_export():
    rows = [_row(tweet_id="1"), _row(tweet_id="2", author={"username": "bob"})]
    search_rows = [{"tweet_id": "2"}, {"tweet_id": "1"}]
    store = ListingStore(rows, search_rows=search_rows)

    result = _list_tweets(store, q="needle -from:nobody", sort="relevance")

    assert [tweet["tweet_id"] for tweet in result["tweets"]] == ["2", "1"]
    assert not any(name == "export" for name, _ in store.calls)
    fetch = next(data for name, data in store.calls if name == "fetch")
    assert fetch["ids"] == ["2", "1"]


def test_api_tweets_has_media_uses_sql_pagination_without_export():
    store = ListingStore([_row(tweet_id="1"), _row(tweet_id="2")])

    result = _list_tweets(store, q="has:media", limit=1)

    assert len(result["tweets"]) == 1
    assert not any(name == "export" for name, _ in store.calls)
    query = next(data for name, data in store.calls if name == "search_tweet_ids")
    expression = query["query_groups"][0][0]["expression"]
    assert "EXISTS (SELECT 1 FROM archive AS related" in expression
    assert "related.record_type = 'media'" in expression


def test_api_tweets_includes_unavailable_tombstones_as_placeholders():
    unavailable = _row(text="This Post is from a suspended account. {learnmore}", media=[])
    store = ListingStore([unavailable, _row(tweet_id="201")])
    result = _list_tweets(store, sort="oldest", limit=1)
    assert [tweet["tweet_id"] for tweet in result["tweets"]] == ["200"]
    assert result["tweets"][0]["availability"] == {
        "state": "unavailable",
        "enrichment_state": None,
        "reason": "suspended_account",
        "message": "This tweet is from a suspended account.",
        "placeholder": True,
        "confirmed": False,
        "retryable": None,
        "checked_at": None,
    }
    assert result["total"] == 2
    assert result["pages"] == 2


def test_api_tweets_uses_canonical_state_instead_of_membership_text():
    terminal = _row(
        tweet_id="terminal",
        media=[],
        text="You\u2019re unable to view this Post because this account owner limits who "
        "can view their Posts. {learnmore}",
    )
    available = _row(
        tweet_id="available",
        text="This Post is from a suspended account. {learnmore}",
    )
    store = ListingStore(
        [terminal, available],
        object_rows=[
            {
                "tweet_id": "terminal",
                "enrichment_state": "terminal_unavailable",
                "enrichment_reason": "protected_account",
                "enrichment_retry_eligible": 1,
                "enrichment_checked_at": "2026-08-10T00:00:00+00:00",
            },
            {
                "tweet_id": "available",
                "text": "canonical recovered text",
                "author_id": "u2",
                "author_username": "recovered",
                "author_display_name": "Recovered",
                "raw_json": {"rest_id": "available", "legacy": {}},
                "enrichment_state": "resurrected",
                "enrichment_reason": None,
                "enrichment_retry_eligible": 0,
                "enrichment_checked_at": "2026-08-10T01:00:00+00:00",
            },
        ],
    )

    result = _list_tweets(store)
    by_id = {tweet["tweet_id"]: tweet for tweet in result["tweets"]}

    assert by_id["terminal"]["availability"]["reason"] == "protected_account"
    assert by_id["terminal"]["availability"]["confirmed"] is True
    assert by_id["terminal"]["raw_json"] is None
    assert by_id["terminal"]["media"] == []
    assert by_id["available"]["availability"]["state"] == "available"
    assert by_id["available"]["availability"]["placeholder"] is False
    assert by_id["available"]["text"] == "canonical recovered text"


def test_api_tweets_real_store_deduplicates_memberships_and_filters_before_paging(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)

    def membership(tweet_id: str, collection: str, created_at: str, text: str) -> dict[str, Any]:
        return store._record(
            row_key=f"tweet:{collection}::{tweet_id}",
            record_type="tweet",
            tweet_id=tweet_id,
            collection_type=collection,
            text=text,
            author_id="a1",
            author_username="alice",
            author_display_name="Alice",
            created_at=created_at,
            created_at_ts=int(datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y").timestamp()),
            sort_index=tweet_id,
            raw_json=json.dumps({"legacy": {}}),
        )

    store._merge_records(
        [
            membership("1", "bookmark", "Thu Jan 01 00:00:00 +0000 2026", "needle one"),
            membership("1", "like", "Thu Jan 01 00:00:00 +0000 2026", "needle one"),
            membership("2", "bookmark", "Fri Jan 02 00:00:00 +0000 2026", "needle two"),
            membership(
                "3",
                "bookmark",
                "Sat Jan 03 00:00:00 +0000 2026",
                "This Post is from a suspended account. {learnmore}",
            ),
        ]
    )

    first = _list_tweets(store, limit=1)
    second = _list_tweets(store, page=2, limit=1)
    filtered = _list_tweets(store, q="needle -from:nobody since:2026-01-01")

    assert first["total"] == first["pages"] == 2
    assert [tweet["tweet_id"] for tweet in first["tweets"]] == ["2"]
    assert [tweet["tweet_id"] for tweet in second["tweets"]] == ["1"]
    uncaptured = _list_tweets(store, q="is:uncaptured")
    assert uncaptured["total"] == 1
    assert [tweet["tweet_id"] for tweet in uncaptured["tweets"]] == ["3"]
    assert uncaptured["tweets"][0]["availability"]["reason"] == "suspended_account"
    assert {tweet["tweet_id"] for tweet in filtered["tweets"]} == {"1", "2"}
    store.close()


def test_api_tweets_hydrates_wrapped_quote_media_and_limits_to_ten():
    quote = {
        "__typename": "TweetWithVisibilityResults",
        "tweet": {"rest_id": "quoted"},
    }
    store = ListingStore([_row(raw_json={"quoted_status_result": {"result": quote}})])
    store.media_rows = [
        {
            "tweet_id": "quoted",
            "media_type": "photo",
            "width": 100,
            "height": 50,
            "local_path": f"media/{index}.jpg",
        }
        for index in range(12)
    ]

    result = _list_tweets(store)

    assert len(result["tweets"][0]["qt_media"]) == 10
    assert result["tweets"][0]["qt_media"][0]["download"]["local_path"] == "media/0.jpg"
