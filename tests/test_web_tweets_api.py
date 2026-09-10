from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from tweetnook.storage.backend import ArchiveStore  # noqa: E402
from tweetnook.web.deps import get_store, server_state, verify_credentials  # noqa: E402
from tweetnook.web.routes.tweets import (  # noqa: E402
    _apply_advanced_filters,
    _extract_advanced_filters,
    _parse_twitter_date,
    _recursive_reply_rows,
    api_tweet_quotes,
    api_tweet_thread,
    api_tweets,
    router,
)


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


def test_extract_advanced_filters_preserves_text_phrases_and_repeated_filters():
    filters, text = _extract_advanced_filters(
        'from:Alice from:"Bob Smith" -filter:replies "exact phrase" #Python loose'
    )

    assert filters == {
        "from": ["alice", "bob smith"],
        "-filter": ["replies"],
        "hashtag": ["python"],
    }
    assert text == '"exact phrase" loose'


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
def test_parse_twitter_date(value: str, expected: float | None):
    assert _parse_twitter_date(value) == expected


@pytest.mark.parametrize(
    ("filter_name", "value"),
    [
        ("from", "@alice"),
        ("to", "@alice"),
        ("mentions", "@bob"),
        ("since", "2026-01-01"),
        ("until", "2026-01-03"),
        ("since_time", "1767311000"),
        ("until_time", "1767313000"),
        ("since_id", "199"),
        ("max_id", "200"),
        ("has", "media"),
        ("has", "image"),
        ("has", "video"),
        ("has", "links"),
        ("is", "reply"),
        ("is", "quote"),
        ("is", "retweet"),
        ("is", "thread"),
        ("is", "verified"),
        ("is", "resurrected"),
        ("filter", "replies"),
        ("filter", "quote"),
        ("filter", "nativeretweets"),
        ("filter", "threads"),
        ("filter", "media"),
        ("filter", "images"),
        ("filter", "videos"),
        ("filter", "native_video"),
        ("filter", "links"),
        ("filter", "verified"),
        ("min_retweets", "4"),
        ("min_faves", "8"),
        ("min_replies", "2"),
        ("conversation_id", "conversation-1"),
        ("quoted_tweet_id", "quoted-1"),
        ("url", "example.com/story"),
        ("source", "twitter_web"),
        ("card_name", "summary"),
        ("tag", "landscape"),
        ("hashtag", "#python"),
    ],
)
def test_apply_advanced_filters_supports_each_search_operator(filter_name: str, value: str):
    row = _row()
    assert _apply_advanced_filters([row], {filter_name: [value]}) == [row]
    assert _apply_advanced_filters([row], {f"-{filter_name}": [value]}) == []


def test_apply_advanced_filters_distinguishes_image_and_video_media():
    photo = _row(media=[{"type": "photo"}])
    video = _row(media=[{"type": "animated_gif"}])

    assert _apply_advanced_filters([photo, video], {"has": ["image"]}) == [photo]
    assert _apply_advanced_filters([photo, video], {"has": ["video"]}) == [video]


def test_apply_advanced_filters_supports_attached_articles():
    article = _row(article={"title": "Long read"})
    ordinary = _row(tweet_id="201", article=None)

    assert _apply_advanced_filters([article, ordinary], {"filter": ["articles"]}) == [article]


def test_apply_advanced_filters_rejects_invalid_values_without_crashing():
    row = _row(created_at="invalid", tweet_id="not-numeric")
    filters = {
        "since": ["bad"],
        "since_time": ["bad"],
        "since_id": ["bad"],
        "min_faves": ["bad"],
    }
    assert _apply_advanced_filters([row], filters) == []


def test_apply_advanced_filters_supports_real_twitter_timestamps():
    row = _row(created_at="Fri Jan 02 00:00:00 +0000 2026", created_at_ts=None)

    assert _apply_advanced_filters([row], {"since": ["2026-01-01"]}) == [row]
    assert _apply_advanced_filters([row], {"until": ["2026-01-03"]}) == [row]


class ListingStore:
    def __init__(
        self,
        rows: list[dict[str, Any]],
        *,
        search_rows: list[dict[str, Any]] | None = None,
        object_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self.rows = rows
        self.search_rows = search_rows if search_rows is not None else rows
        self.object_rows = object_rows or []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.media_rows: list[dict[str, Any]] = []

    def _count(self, expr: str) -> int:
        self.calls.append(("count", {"expr": expr}))
        return len(self.rows)

    def _count_distinct(self, field: str, expr: str) -> int:
        self.calls.append(("count_distinct", {"field": field, "expr": expr}))
        return len({row["tweet_id"] for row in self.rows})

    def _query(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("query", kwargs))
        rows = self.rows
        if kwargs.get("cols") == ["DISTINCT tweet_id"]:
            rows = list({row["tweet_id"]: row for row in rows}.values())
        offset = kwargs.get("offset", 0)
        limit = kwargs.get("limit", len(rows))
        return [{"tweet_id": row["tweet_id"]} for row in rows[offset : offset + limit]]

    def fetch_tweets_by_ids(self, ids: list[str]) -> list[dict[str, Any]]:
        self.calls.append(("fetch", {"ids": ids}))
        by_id = {row["tweet_id"]: row for row in self.rows}
        return [by_id[tweet_id] for tweet_id in ids if tweet_id in by_id]

    def search_fts(
        self,
        query: str,
        *,
        limit: int,
        types: set[str] | None = None,
        collections: set[str] | None,
    ) -> list[dict[str, Any]]:
        self.calls.append(
            (
                "search",
                {
                    "query": query,
                    "limit": limit,
                    "types": types,
                    "collections": collections,
                },
            )
        )
        return [dict(row) for row in self.search_rows]

    def search_tweet_ids(
        self,
        query_groups,
        *,
        collections,
        sort,
        limit,
        offset,
        random_seed=None,
        include_total=False,
    ):
        self.calls.append(
            (
                "search_tweet_ids",
                {
                    "query_groups": query_groups,
                    "collections": collections,
                    "sort": sort,
                    "limit": limit,
                    "offset": offset,
                    "random_seed": random_seed,
                    "include_total": include_total,
                },
            )
        )
        has_text = any(clause.get("kind") == "text" for group in query_groups for clause in group)
        source = self.search_rows if has_text else self.rows

        def timestamp(row):
            full_row = next(
                (candidate for candidate in self.rows if candidate["tweet_id"] == row["tweet_id"]),
                row,
            )
            if full_row.get("created_at_ts") is not None:
                return full_row["created_at_ts"]
            created_at = full_row.get("created_at", "")
            if "T" in created_at:
                return datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp()
            return datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y").timestamp()

        rows = [
            {
                "tweet_id": row["tweet_id"],
                "match_score": row.get("match_score", 0.0),
                "created_at_ts": timestamp(row),
                "sort_index": row.get("sort_index"),
            }
            for row in source
        ]
        if sort == "oldest":
            rows.sort(key=lambda row: (row.get("created_at_ts") or 0, row["tweet_id"]))
        elif sort == "newest":
            rows.sort(
                key=lambda row: (row.get("created_at_ts") or 0, row["tweet_id"]), reverse=True
            )
        return rows[offset : offset + limit + 1], len(rows) if include_total else None

    def export_rows(
        self, collection: str, *, sort: str, include_raw_json: bool
    ) -> list[dict[str, Any]]:
        self.calls.append(
            (
                "export",
                {
                    "collection": collection,
                    "sort": sort,
                    "include_raw_json": include_raw_json,
                },
            )
        )
        return [dict(row) for row in self.rows]

    def _rows_for_values(
        self,
        record_type: str,
        field: str,
        values: list[str],
        *,
        columns: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        self.calls.append(
            (
                "rows_for_values",
                {
                    "record_type": record_type,
                    "field": field,
                    "values": values,
                    "columns": columns,
                },
            )
        )
        if record_type == "tweet_object":
            return [row for row in self.object_rows if row.get(field) in values]
        if record_type == "media":
            return [row for row in self.media_rows if row.get(field) in values]
        return []


def _list_tweets(
    store: ListingStore,
    *,
    q: str | None = None,
    collection: str = "all",
    sort: str = "default",
    page: int = 1,
    limit: int = 20,
) -> dict[str, Any]:
    return api_tweets(
        q=q,
        collection=collection,
        sort=sort,
        page=page,
        limit=limit,
        random_seed=None,
        store=store,
        _auth=True,
    )


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
    unavailable = _row(text="This Post is from a suspended account. {learnmore}")
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
    terminal = _row(tweet_id="terminal", text="ordinary archived text")
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

    assert first["total"] == first["pages"] == 3
    assert [tweet["tweet_id"] for tweet in first["tweets"]] == ["3"]
    assert first["tweets"][0]["availability"]["reason"] == "suspended_account"
    assert [tweet["tweet_id"] for tweet in second["tweets"]] == ["2"]
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


def _thread_object(
    tweet_id: str,
    *,
    author_id: str = "u1",
    likes: int = 0,
    quote_id: str | None = None,
) -> dict[str, Any]:
    raw: dict[str, Any] = {"legacy": {"favorite_count": likes}}
    if quote_id:
        raw["quoted_status_result"] = {
            "result": {
                "__typename": "TweetWithVisibilityResults",
                "tweet": {"rest_id": quote_id},
            }
        }
    return {
        "tweet_id": tweet_id,
        "text": f"post {tweet_id}",
        "author_id": author_id,
        "author_username": f"user-{author_id}",
        "author_display_name": f"User {author_id}",
        "created_at": f"2026-01-0{min(len(tweet_id), 9)}T00:00:00Z",
        "synced_at": "2026-01-10T00:00:00Z",
        "raw_json": json.dumps(raw),
    }


class ThreadStore:
    def __init__(self) -> None:
        self.relation_call = 0
        self.expressions: list[str] = []
        self.query_columns: list[tuple[str, list[str] | None]] = []
        self.query_indexes: list[tuple[str, str | None]] = []
        self.quote_count_call: tuple[str, tuple[object, ...]] | None = None
        self.reply_tree_call: tuple[str, tuple[object, ...]] | None = None
        self.result_rows: list[tuple[object, ...]] = []
        self.conn = self

    def _query(
        self,
        *,
        expr: str,
        cols: list[str] | None = None,
        limit: int | None = None,
        indexed_by: str | None = None,
        **_: Any,
    ) -> list[dict[str, Any]]:
        self.expressions.append(expr)
        self.query_columns.append((expr, cols))
        self.query_indexes.append((expr, indexed_by))
        if "record_type = 'tweet_relation'" in expr and "quote_of" not in expr:
            self.relation_call += 1
            if " AND tweet_id = 'main'" in expr and "'reply_to'" in expr:
                return [
                    {
                        "tweet_id": "main",
                        "target_tweet_id": "parent",
                        "relation_type": "reply_to",
                    }
                ]
            return []
        if "record_type = 'tweet_object'" in expr:
            return [
                _thread_object("main", quote_id="quoted"),
                _thread_object("parent"),
                _thread_object("child", author_id="u2", likes=1),
                _thread_object("popular", author_id="u2", likes=20),
                _thread_object("grandchild", likes=0),
                _thread_object("non-op-grandchild", author_id="u3", likes=0),
                _thread_object("great-grandchild", author_id="u4", likes=0),
            ]
        if "record_type = 'media'" in expr:
            return [
                {
                    "tweet_id": "main",
                    "media_type": "photo",
                    "width": 80,
                    "height": 40,
                    "local_path": "media/main.jpg",
                }
            ]
        if "record_type = 'tweet' AND" in expr:
            return [
                {"tweet_id": "main", "collection_type": "bookmark"},
                {"tweet_id": "main", "collection_type": "like"},
            ]
        if "record_type = 'media_tag'" in expr:
            return [
                {"tweet_id": "main", "raw_json": '{"tags":["Night"]}'},
                {"tweet_id": "child", "raw_json": "not-json"},
            ]
        return []

    def execute(self, sql: str, params: tuple[object, ...]):
        if "WITH RECURSIVE reply_tree" in sql:
            self.reply_tree_call = (sql, params)
            self.result_rows = [
                ("child", "main", 1),
                ("popular", "main", 1),
                ("grandchild", "child", 2),
                ("non-op-grandchild", "child", 2),
                ("great-grandchild", "grandchild", 3),
            ]
            return self
        self.quote_count_call = (sql, params)
        self.result_rows = []
        return self

    def fetchone(self) -> tuple[int]:
        return (2,)

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.result_rows

    def _rows_for_values(
        self,
        record_type: str,
        field: str,
        values: list[str],
        *,
        columns: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        assert field == "tweet_id"
        if record_type == "tweet_object":
            return []
        if record_type == "tweet_relation":
            return []
        if record_type == "media_tag":
            assert columns == ["tweet_id", "raw_json"]
            return [
                {
                    "tweet_id": "quoted",
                    "raw_json": '{"tags":["Quoted Topic"]}',
                }
            ]
        assert record_type == "media"
        assert columns == [
            "tweet_id",
            "media_type",
            "media_url",
            "thumbnail_url",
            "width",
            "height",
            "duration_millis",
            "local_path",
            "thumbnail_local_path",
        ]
        return [
            {
                "tweet_id": "quoted",
                "media_type": "video",
                "duration_millis": 12,
                "local_path": "media/quote.mp4",
            }
        ]


def test_api_tweet_thread_builds_parents_children_nested_replies_quotes_media_and_tags():
    store = ThreadStore()

    result = api_tweet_thread("main", store=store, _auth=True)

    assert result["main"]["collections"] == ["bookmark", "like"]
    assert result["main"]["local_quote_count"] == 2
    assert result["main"]["media_tags"] == {"tags": ["Night"]}
    assert result["main"]["qt_media_tags"] == {"tags": ["Quoted Topic"]}
    assert result["main"]["qt_media"][0]["download"]["local_path"] == "media/quote.mp4"
    assert [tweet["tweet_id"] for tweet in result["parents"]] == ["parent"]
    assert [tweet["tweet_id"] for tweet in result["children"]] == ["child", "popular"]
    assert [reply["tweet_id"] for reply in result["children"][0]["op_replies"]] == [
        "grandchild",
        "great-grandchild",
        "non-op-grandchild",
    ]
    assert [reply["thread_depth"] for reply in result["children"][0]["op_replies"]] == [
        2,
        3,
        2,
    ]
    assert [reply["parent_tweet_id"] for reply in result["children"][0]["op_replies"]] == [
        "child",
        "grandchild",
        "child",
    ]
    assert result["children"][0]["op_replies"][2]["author"]["id"] == "u3"
    assert result["reply_tree_truncated"] is False
    assert store.reply_tree_call is not None
    reply_tree_sql, reply_tree_params = store.reply_tree_call
    assert reply_tree_sql.count("INDEXED BY idx_archive_target_tweet_id") == 2
    assert reply_tree_sql.count("INDEXED BY idx_archive_tweet_id") == 2
    assert reply_tree_params[:4] == ("main",) * 4
    assert reply_tree_params[-3:] == (251, 251, 251)
    assert store.quote_count_call is not None
    assert store.quote_count_call[1] == ("main",)
    assert "INDEXED BY idx_archive_target_tweet_id" in store.quote_count_call[0]
    relation_columns = {
        tuple(columns or [])
        for expr, columns in store.query_columns
        if "record_type = 'tweet_relation'" in expr
    }
    assert relation_columns == {("tweet_id", "target_tweet_id", "relation_type")}
    relation_queries = [
        (expr, index)
        for expr, index in store.query_indexes
        if "record_type = 'tweet_relation'" in expr
    ]
    assert relation_queries
    assert all(" OR " not in expr for expr, _ in relation_queries)
    for expr, index in relation_queries:
        expected = (
            "idx_archive_target_tweet_id"
            if " AND target_tweet_id" in expr
            else "idx_archive_tweet_id"
        )
        assert index == expected
    expected_columns = {
        "tweet_object": {
            "tweet_id",
            "text",
            "author_id",
            "author_username",
            "author_display_name",
            "created_at",
            "synced_at",
            "raw_json",
        },
        "media": {
            "tweet_id",
            "media_type",
            "media_url",
            "thumbnail_url",
            "width",
            "height",
            "duration_millis",
            "local_path",
            "thumbnail_local_path",
        },
        "media_tag": {"tweet_id", "raw_json"},
    }
    for record_type, columns in expected_columns.items():
        actual = next(
            selected
            for expr, selected in store.query_columns
            if f"record_type = '{record_type}'" in expr
        )
        assert set(actual or []) == columns
    for expr, index in store.query_indexes:
        if any(f"record_type = '{record_type}'" in expr for record_type in expected_columns):
            assert index == "idx_archive_tweet_id"


def test_api_tweet_thread_recursively_loads_and_deduplicates_saved_reply_chain(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    records = []
    for tweet_id, author_id in (("100", "u1"), ("200", "u2"), ("300", "u3"), ("400", "u4")):
        tweet = _thread_object(tweet_id, author_id=author_id)
        records.append(
            store._record(
                row_key=f"tweet_object:{tweet_id}",
                record_type="tweet_object",
                tweet_id=tweet_id,
                text=tweet["text"],
                author_id=tweet["author_id"],
                author_username=tweet["author_username"],
                author_display_name=tweet["author_display_name"],
                created_at=tweet["created_at"],
                raw_json=tweet["raw_json"],
                enrichment_state="done",
            )
        )
    for child_id, parent_id in (("200", "100"), ("300", "200"), ("400", "300")):
        records.extend(
            [
                store._record(
                    row_key=f"tweet_relation:{child_id}:reply_to:{parent_id}",
                    record_type="tweet_relation",
                    tweet_id=child_id,
                    relation_type="reply_to",
                    target_tweet_id=parent_id,
                ),
                store._record(
                    row_key=f"tweet_relation:{child_id}:thread_parent:{parent_id}",
                    record_type="tweet_relation",
                    tweet_id=child_id,
                    relation_type="thread_parent",
                    target_tweet_id=parent_id,
                ),
                store._record(
                    row_key=f"tweet_relation:{parent_id}:thread_child:{child_id}",
                    record_type="tweet_relation",
                    tweet_id=parent_id,
                    relation_type="thread_child",
                    target_tweet_id=child_id,
                ),
            ]
        )
    store._merge_records(records)

    limited_rows, limited = _recursive_reply_rows(store, "100", limit=2)
    assert [row["tweet_id"] for row in limited_rows] == ["200", "300"]
    assert limited is True

    traced_sql: list[str] = []
    store.conn.set_trace_callback(traced_sql.append)
    result = api_tweet_thread("100", store=store, _auth=True)
    store.conn.set_trace_callback(None)

    assert [child["tweet_id"] for child in result["children"]] == ["200"]
    assert result["children"][0]["thread_depth"] == 1
    assert [reply["tweet_id"] for reply in result["children"][0]["op_replies"]] == [
        "300",
        "400",
    ]
    assert [reply["thread_depth"] for reply in result["children"][0]["op_replies"]] == [
        2,
        3,
    ]
    assert result["reply_tree_truncated"] is False
    recursive_queries = [sql for sql in traced_sql if "WITH RECURSIVE reply_tree" in sql]
    assert len(recursive_queries) == 1
    assert "INDEXED BY idx_archive_target_tweet_id" in recursive_queries[0]
    assert "INDEXED BY idx_archive_tweet_id" in recursive_queries[0]
    store.close()


def test_api_tweet_thread_parses_each_tweet_json_once_and_tolerates_malformed(monkeypatch):
    class MalformedThreadStore(ThreadStore):
        def _query(self, **kwargs: Any) -> list[dict[str, Any]]:
            rows = super()._query(**kwargs)
            if "record_type = 'tweet_object'" in kwargs["expr"]:
                rows[0]["raw_json"] = "not-json"
            return rows

    original_loads = json.loads
    parsed_values: list[object] = []

    def track_loads(value: object, *args: Any, **kwargs: Any) -> Any:
        parsed_values.append(value)
        return original_loads(value, *args, **kwargs)

    monkeypatch.setattr("tweetnook.web.routes.tweets.json.loads", track_loads)
    result = api_tweet_thread("main", store=MalformedThreadStore(), _auth=True)

    assert result["main"]["raw_json"] is None
    assert len(parsed_values) == 10  # seven tweet objects and three direct/quoted media-tag rows


class CycleStore(ThreadStore):
    def _query(self, *, expr: str, limit: int | None = None, **kwargs: Any) -> list[dict[str, Any]]:
        if "record_type = 'tweet_relation'" in expr and "quote_of" not in expr:
            self.expressions.append(expr)
            self.relation_call += 1
            if self.relation_call == 1:
                return [
                    {
                        "tweet_id": "main",
                        "target_tweet_id": "parent",
                        "relation_type": "reply_to",
                    }
                ]
            if self.relation_call == 2:
                return [
                    {
                        "tweet_id": "parent",
                        "target_tweet_id": "main",
                        "relation_type": "reply_to",
                    }
                ]
            return []
        return super()._query(expr=expr, limit=limit, **kwargs)


def test_api_tweet_thread_stops_parent_cycles_before_depth_cap():
    store = CycleStore()
    result = api_tweet_thread("main", store=store, _auth=True)
    assert result["main"]["tweet_id"] == "main"
    assert store.relation_call < 10


def test_api_tweet_thread_preserves_relation_only_placeholders(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    store._merge_records(
        [
            store._record(
                row_key="tweet_object:main",
                record_type="tweet_object",
                tweet_id="main",
                text="main post",
                author_id="u1",
                author_username="alice",
                author_display_name="Alice",
                created_at="2026-08-10T00:00:00+00:00",
                raw_json=json.dumps({"rest_id": "main", "legacy": {}}),
                enrichment_state="done",
            ),
            store._record(
                row_key="tweet_relation:main:reply_to:missing-parent",
                record_type="tweet_relation",
                tweet_id="main",
                relation_type="reply_to",
                target_tweet_id="missing-parent",
            ),
        ]
    )

    result = api_tweet_thread("main", store=store, _auth=True)
    missing_main = api_tweet_thread("missing-parent", store=store, _auth=True)

    assert [parent["tweet_id"] for parent in result["parents"]] == ["missing-parent"]
    assert result["parents"][0]["availability"] == {
        "state": "not_archived",
        "enrichment_state": None,
        "reason": "not_archived",
        "message": "This tweet was not captured in the local archive.",
        "placeholder": True,
        "confirmed": False,
        "retryable": None,
        "checked_at": None,
    }
    assert missing_main["main"]["availability"]["state"] == "not_archived"
    assert [child["tweet_id"] for child in missing_main["children"]] == ["main"]
    with pytest.raises(fastapi.HTTPException) as exc:
        api_tweet_thread("unknown", store=store, _auth=True)
    assert exc.value.status_code == 404
    store.close()


def test_api_tweet_thread_quotes_untrusted_path_id_in_store_expressions():
    payload = "main' OR 1=1 --"

    class MissingStore:
        def __init__(self) -> None:
            self.expressions: list[str] = []
            self.conn = self

        def _query(self, *, expr: str, **_: Any) -> list[dict[str, Any]]:
            self.expressions.append(expr)
            return []

        def execute(self, _sql: str, _params: tuple[object, ...]):
            return self

        def fetchall(self) -> list[tuple[object, ...]]:
            return []

    store = MissingStore()
    with pytest.raises(fastapi.HTTPException) as exc:
        api_tweet_thread(payload, store=store, _auth=True)
    assert exc.value.status_code == 404
    assert all("main'' OR 1=1 --" in expr for expr in store.expressions)
    assert all("main' OR 1=1 --'" not in expr for expr in store.expressions)


class QuotesConnection:
    def __init__(self) -> None:
        self.call: tuple[str, tuple[str, ...]] | None = None

    def execute(self, sql: str, params: tuple[str, ...]):
        self.call = (sql, params)
        return self

    def fetchone(self) -> tuple[int]:
        return (3,)


class QuotesStore:
    def __init__(self) -> None:
        self.conn = QuotesConnection()
        self.query: dict[str, Any] | None = None

    def _query(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.query = kwargs
        return [{"tweet_id": "q3"}, {"tweet_id": "q2"}]

    def fetch_tweets_by_ids(self, ids: list[str]) -> list[dict[str, Any]]:
        return [{"tweet_id": value} for value in ids]

    def _rows_for_values(
        self,
        _record_type: str,
        _field: str,
        _values: list[str],
        *,
        columns: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        return []


def test_api_tweet_quotes_parameterizes_count_and_paginates_distinct_ids():
    store = QuotesStore()
    payload = "target' OR 1=1 --"

    result = api_tweet_quotes(payload, page=2, limit=2, store=store, _auth=True)

    assert [tweet["tweet_id"] for tweet in result["tweets"]] == ["q3", "q2"]
    assert all(tweet["availability"]["state"] == "incomplete" for tweet in result["tweets"])
    assert result["total"] == 3
    assert result["page"] == 2
    assert result["limit"] == 2
    assert store.conn.call is not None
    sql, params = store.conn.call
    assert payload not in sql
    assert params == (payload,)
    assert "INDEXED BY idx_archive_target_tweet_id" in sql
    assert store.query is not None
    assert "target'' OR 1=1 --" in store.query["expr"]
    assert store.query["cols"] == ["DISTINCT tweet_id"]
    assert store.query["offset"] == 2
    assert store.query["indexed_by"] == "idx_archive_target_tweet_id"


def _api_client(store: object) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[verify_credentials] = lambda: True
    return TestClient(app)


@pytest.mark.parametrize(
    "url",
    [
        "/api/tweets?page=0",
        "/api/tweets?limit=0",
        "/api/tweets?limit=101",
        "/api/tweets?random_seed=-1",
        "/api/tweets?random_seed=2147483648",
        "/api/tweets/1/quotes?page=0",
        "/api/tweets/1/quotes?limit=101",
    ],
)
def test_api_query_validation_rejects_out_of_bounds_pagination(url: str):
    response = _api_client(ListingStore([])).get(url)
    assert response.status_code == 422


def test_authors_search_endpoint_forwards_query_and_limit():
    class AuthorStore:
        def search_authors(self, query: str, *, limit: int) -> list[dict[str, str]]:
            assert query == "ali"
            assert limit == 10
            return [{"id": "1", "username": "alice", "display_name": "Alice"}]

    response = _api_client(AuthorStore()).get("/api/authors/search?q=ali")
    assert response.status_code == 200
    assert response.json()["authors"][0]["username"] == "alice"


@pytest.mark.parametrize(
    "url",
    [
        "/api/tweets",
        "/api/tweets/1",
        "/api/tweets/1/quotes",
        "/api/authors/search",
    ],
)
def test_tweet_routes_require_authentication(url: str):
    previous_state = dict(server_state)
    server_state.clear()
    server_state["password_hash"] = hashlib.sha256(b"secret").hexdigest()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_store] = lambda: ListingStore([])
    try:
        with TestClient(app) as client:
            response = client.get(url)
    finally:
        server_state.clear()
        server_state.update(previous_state)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Basic"


@pytest.mark.parametrize(
    ("method", "expected_status"),
    [
        (
            lambda: api_tweets(
                q=None,
                collection="all",
                sort="newest",
                page=1,
                limit=20,
                store=type("BadStore", (), {"_count": lambda *_: 1 / 0})(),
                _auth=True,
            ),
            500,
        ),
        (
            lambda: api_tweet_thread(
                "1",
                store=type("BadStore", (), {"_query": lambda *_a, **_k: 1 / 0})(),
                _auth=True,
            ),
            500,
        ),
    ],
)
def test_api_failures_are_mapped_to_http_500(method: Callable[[], object], expected_status: int):
    with pytest.raises(fastapi.HTTPException) as exc:
        method()
    assert exc.value.status_code == expected_status
