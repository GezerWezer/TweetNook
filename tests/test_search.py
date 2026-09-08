from __future__ import annotations

import json
from typing import Any

import pytest

from tweetnook.search import SearchQueryError, _filter_matches, parse_search_query, search_posts
from tweetnook.storage.backend import ArchiveStore


def _row(
    tweet_id: str,
    text: str,
    *,
    username: str = "alice",
    media: list[dict[str, Any]] | None = None,
    article: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "tweet_id": tweet_id,
        "text": text,
        "author": {"id": username, "username": username, "display_name": username.title()},
        "created_at": f"2026-01-0{tweet_id}T00:00:00Z",
        "media": media or [],
        "urls": [],
        "article": article,
        "raw_json": {"legacy": {}},
    }


def _real_store(tmp_path, rows: list[dict[str, Any]]) -> ArchiveStore:
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    records = []
    for row in rows:
        tweet_id = row["tweet_id"]
        author = row["author"]
        records.append(
            store._record(
                row_key=f"tweet:bookmark::{tweet_id}",
                record_type="tweet",
                tweet_id=tweet_id,
                collection_type="bookmark",
                text=row["text"],
                author_id=author["id"],
                author_username=author["username"],
                author_display_name=author["display_name"],
                created_at=row["created_at"],
                created_at_ts=int(tweet_id),
                sort_index=tweet_id,
                raw_json=json.dumps(row["raw_json"]),
            )
        )
        for index, media in enumerate(row["media"]):
            records.append(
                store._record(
                    row_key=f"media:{tweet_id}:{index}",
                    record_type="media",
                    tweet_id=tweet_id,
                    media_key=str(index),
                    media_type=media["type"],
                )
            )
    store._merge_records(records)
    return store


def test_parser_uses_implicit_and_and_adjacent_or_groups() -> None:
    parsed = parse_search_query("from:alice cats OR dogs has:image")

    assert [[clause.value for clause in group] for group in parsed.groups] == [
        ["alice"],
        ["cats", "dogs"],
        ["image"],
    ]
    assert parsed.has_or is True


def test_parser_accepts_explicit_and_but_keeps_lowercase_words_searchable() -> None:
    explicit = parse_search_query("cats AND dogs")
    lowercase = parse_search_query("cats and dogs or birds")

    assert [[clause.value for clause in group] for group in explicit.groups] == [
        ["cats"],
        ["dogs"],
    ]
    assert [group[0].value for group in lowercase.groups] == ["cats", "and", "dogs", "or", "birds"]


def test_parser_preserves_text_and_filter_negation() -> None:
    parsed = parse_search_query('cats -dogs -filter:videos "night sky"')
    clauses = [clause for group in parsed.groups for clause in group]

    assert [(clause.kind, clause.value, clause.negated) for clause in clauses] == [
        ("text", "cats", False),
        ("text", "dogs", True),
        ("filter", "videos", True),
        ("text", '"night sky"', False),
    ]


@pytest.mark.parametrize("query", ["OR cats", "cats OR", "cats AND", "unknown:value"])
def test_parser_rejects_malformed_or_unknown_operators(query: str) -> None:
    with pytest.raises(SearchQueryError):
        parse_search_query(query)


def test_grouped_search_requires_all_groups_and_any_or_alternative(tmp_path) -> None:
    photo = [{"type": "photo"}]
    store = _real_store(
        tmp_path,
        [
            _row("1", "cats", media=photo),
            _row("2", "dogs", media=photo),
            _row("3", "dogs", username="bob", media=photo),
            _row("4", "cats"),
        ],
    )

    result = search_posts(store, "from:alice cats OR dogs has:image", limit=20)

    assert {row["tweet_id"] for row in result.rows} == {"1", "2"}
    assert result.total is None
    store.close()


def test_explicit_or_makes_repeated_filters_alternatives(tmp_path) -> None:
    store = _real_store(
        tmp_path,
        [
            _row("1", "one"),
            _row("2", "two", username="bob"),
            _row("3", "three", username="carol"),
        ],
    )

    result = search_posts(store, "from:alice OR from:bob", limit=20)

    assert {row["tweet_id"] for row in result.rows} == {"1", "2"}
    assert result.total == 2
    store.close()


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
                enrichment_state="done",
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
    assert "json_extract(raw_json, '$.legacy.in_reply_to_status_id_str')" in traced_sql

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


def test_thread_filter_is_null_safe_for_incomplete_authors() -> None:
    row = _row("1", "incomplete")
    row["author"] = {"username": None}
    row["raw_json"] = {"legacy": {"in_reply_to_screen_name": "alice"}}

    assert _filter_matches(row, "is", "thread") is False
    assert _filter_matches(row, "filter", "threads") is False


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


def test_unified_search_paginates_complete_large_result_sets(tmp_path) -> None:
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    records = []
    for tweet_id in range(1, 1601):
        text = "foo shared term" if tweet_id <= 1300 else "bar alternate term"
        if tweet_id > 1500:
            text = "clean archive entry"
        legacy: dict[str, Any] = {}
        if tweet_id <= 1300 and tweet_id % 5 == 0:
            legacy = {
                "in_reply_to_status_id_str": str(tweet_id - 1),
                "in_reply_to_screen_name": "alice",
            }
        collections = ("bookmark", "like") if tweet_id <= 1300 else ("bookmark",)
        for collection in collections:
            records.append(
                store._record(
                    row_key=f"tweet:{collection}::{tweet_id}",
                    record_type="tweet",
                    tweet_id=str(tweet_id),
                    collection_type=collection,
                    text=text,
                    author_id="alice",
                    author_username="alice",
                    author_display_name="Alice",
                    created_at_ts=tweet_id,
                    sort_index=str(tweet_id),
                    raw_json=json.dumps({"legacy": legacy}),
                )
            )
        if tweet_id % 4 == 0:
            records.append(
                store._record(
                    row_key=f"media:{tweet_id}:video",
                    record_type="media",
                    tweet_id=str(tweet_id),
                    media_key="video",
                    media_type="video",
                )
            )
        if tweet_id <= 1300 and tweet_id % 6 == 0:
            records.append(
                store._record(
                    row_key=f"media_tag:{tweet_id}",
                    record_type="media_tag",
                    tweet_id=str(tweet_id),
                    raw_json=json.dumps({"tags": ["example"]}),
                )
            )
    store._merge_records(records)

    page_50 = search_posts(store, "foo", page=50, limit=20)
    page_51 = search_posts(store, "foo", page=51, limit=20)
    assert len(page_51.rows) == 20
    assert page_51.total is page_51.pages is None
    assert page_51.has_more is True
    assert {row["tweet_id"] for row in page_50.rows}.isdisjoint(
        row["tweet_id"] for row in page_51.rows
    )

    or_page = search_posts(store, "foo OR bar", page=66, limit=20)
    assert len(or_page.rows) == 20
    assert or_page.has_more is True

    negative = search_posts(store, "NOT foo", sort="oldest", page=1, limit=100)
    assert len(negative.rows) == 100
    assert all("foo" not in row["text"] for row in negative.rows)

    mixed = search_posts(store, "foo OR has:video", sort="newest", page=1, limit=100)
    assert [row["tweet_id"] for row in mixed.rows[:3]] == ["1600", "1596", "1592"]
    assert len(mixed.rows) == 100

    videos = search_posts(store, "foo has:video", page=6, limit=20)
    replies = search_posts(store, "foo is:reply", page=6, limit=20)
    tags = search_posts(store, "foo tag:example", page=6, limit=20)
    assert len(videos.rows) == len(replies.rows) == len(tags.rows) == 20
    assert all(row["media"] for row in videos.rows)
    assert all(row["raw_json"]["legacy"].get("in_reply_to_status_id_str") for row in replies.rows)
    assert all(row["media_tags"] == {"tags": ["example"]} for row in tags.rows)

    newest = search_posts(store, "foo", sort="newest", limit=3)
    oldest = search_posts(store, "foo", sort="oldest", limit=3)
    assert [row["tweet_id"] for row in newest.rows] == ["1300", "1299", "1298"]
    assert [row["tweet_id"] for row in oldest.rows] == ["1", "2", "3"]

    all_foo = search_posts(store, "foo", page=65, limit=20)
    like_foo = search_posts(store, "foo", collections={"like"}, page=65, limit=20)
    assert len(all_foo.rows) == len(like_foo.rows) == 20
    assert len({row["tweet_id"] for row in all_foo.rows}) == 20
    assert {row["tweet_id"] for row in all_foo.rows} == {row["tweet_id"] for row in like_foo.rows}

    liked_latest = search_posts(
        store,
        "foo",
        collections={"like"},
        sort="liked_latest",
        page=51,
        limit=20,
    )
    liked_earliest = search_posts(
        store,
        "foo",
        collections={"like"},
        sort="liked_earliest",
        limit=3,
    )
    assert [row["tweet_id"] for row in liked_latest.rows[:3]] == ["300", "299", "298"]
    assert [row["tweet_id"] for row in liked_earliest.rows] == ["1", "2", "3"]

    random_first = search_posts(store, None, sort="random", limit=20, random_seed=17)
    random_second = search_posts(store, None, sort="random", page=2, limit=20, random_seed=17)
    random_repeat = search_posts(store, None, sort="random", limit=20, random_seed=17)
    random_new_seed = search_posts(store, None, sort="random", limit=20, random_seed=18)
    assert [row["tweet_id"] for row in random_first.rows] == [
        row["tweet_id"] for row in random_repeat.rows
    ]
    assert {row["tweet_id"] for row in random_first.rows}.isdisjoint(
        row["tweet_id"] for row in random_second.rows
    )
    assert [row["tweet_id"] for row in random_first.rows] != [
        row["tweet_id"] for row in random_new_seed.rows
    ]
    assert random_first.total == 1600

    plan_shapes = {
        "simple": ("foo", {}),
        "collection": ("foo", {"collections": {"bookmark"}}),
        "filter": ("foo has:video", {}),
        "or": ("foo OR bar", {}),
        "negative": ("NOT foo", {}),
        "newest": ("foo", {"sort": "newest"}),
    }
    plans = {}
    for name, (query, options) in plan_shapes.items():
        statements: list[str] = []
        store.conn.set_trace_callback(statements.append)
        search_posts(store, query, **options)
        store.conn.set_trace_callback(None)
        page_sql = next(
            statement
            for statement in statements
            if "SELECT matches.tweet_id" in statement and "ORDER BY" in statement
        )
        plans[name] = " ".join(
            row["detail"] for row in store.conn.execute(f"EXPLAIN QUERY PLAN {page_sql}")
        )
    assert all("archive_fts VIRTUAL TABLE INDEX" in plan for plan in plans.values())
    assert "idx_archive_tweet_id" in plans["simple"]
    assert "idx_archive_tweet_id" in plans["collection"]
    assert "idx_archive_search_attachment" in plans["filter"]
    assert "idx_archive_tweet_id" in plans["or"]
    assert "idx_archive_record_page" in plans["negative"]
    assert "idx_archive_tweet_id" in plans["newest"]
    store.close()


def test_real_store_keeps_grouped_search_semantics(tmp_path) -> None:
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    store._merge_records(
        [
            store._record(
                row_key=f"tweet:bookmark::{tweet_id}",
                record_type="tweet",
                tweet_id=tweet_id,
                collection_type="bookmark",
                text=text,
                author_id=author,
                author_username=author,
                author_display_name=author.title(),
                created_at_ts=int(tweet_id),
                sort_index=tweet_id,
                raw_json=json.dumps({"legacy": {}}),
            )
            for tweet_id, text, author in [("1", "cats", "alice"), ("2", "dogs", "bob")]
        ]
    )

    alternatives = search_posts(store, "cats OR dogs", limit=20)
    required_authors = search_posts(store, "from:alice from:bob", limit=20)
    alternative_authors = search_posts(store, "from:alice OR from:bob", limit=20)

    assert {row["tweet_id"] for row in alternatives.rows} == {"1", "2"}
    assert required_authors.rows == []
    assert {row["tweet_id"] for row in alternative_authors.rows} == {"1", "2"}
    store.close()


def test_tag_search_matches_tags_on_a_quoted_original(tmp_path) -> None:
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    store._merge_records(
        [
            store._record(
                row_key="tweet:like::quote",
                record_type="tweet",
                tweet_id="quote",
                collection_type="like",
                text="Commentary around a quoted post",
                author_id="alice",
                author_username="alice",
                author_display_name="Alice",
                created_at_ts=2,
                sort_index="2",
                raw_json=json.dumps({"legacy": {}}),
            ),
            store._record(
                row_key="tweet_relation:quote:quote_of:original",
                record_type="tweet_relation",
                tweet_id="quote",
                relation_type="quote_of",
                target_tweet_id="original",
            ),
            store._record(
                row_key="media_tag:original",
                record_type="media_tag",
                tweet_id="original",
                raw_json=json.dumps({"tags": ["Quoted Topic", "Specific Subject"]}),
            ),
            store._record(
                row_key="tweet:like::near-match",
                record_type="tweet",
                tweet_id="near-match",
                collection_type="like",
                text="Similar but differently tagged post",
                author_id="bob",
                author_username="bob",
                author_display_name="Bob",
                created_at_ts=1,
                sort_index="1",
                raw_json=json.dumps({"legacy": {}}),
            ),
            store._record(
                row_key="media_tag:near-match",
                record_type="media_tag",
                tweet_id="near-match",
                raw_json=json.dumps({"tags": ["Quoted Topic Extended"]}),
            ),
        ]
    )

    statements: list[str] = []
    store.conn.set_trace_callback(statements.append)
    direct = search_posts(store, 'tag:"Quoted Topic"')
    store.conn.set_trace_callback(None)
    grouped = search_posts(store, 'tag:"Quoted Topic" OR from:nobody')

    assert [row["tweet_id"] for row in direct.rows] == ["quote"]
    assert direct.rows[0]["qt_media_tags"] == {"tags": ["Quoted Topic", "Specific Subject"]}
    assert [row["tweet_id"] for row in grouped.rows] == ["quote"]
    count_sql = next(
        statement
        for statement in statements
        if "SELECT COUNT(*) FROM matches" in statement and "matching_tags" in statement
    )
    plan = " ".join(row["detail"] for row in store.conn.execute(f"EXPLAIN QUERY PLAN {count_sql}"))
    assert "idx_archive_media_tag_lookup" in count_sql
    assert "CROSS JOIN archive relation INDEXED BY idx_archive_target_tweet_id" in count_sql
    assert "SEARCH relation USING INDEX idx_archive_target_tweet_id (target_tweet_id=?)" in plan
    assert "SCAN relation USING INDEX idx_archive_tweet_id" not in plan
    store.close()
