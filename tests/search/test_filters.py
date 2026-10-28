from __future__ import annotations

import json
from typing import Any

import pytest

from tests.search.conftest import _real_store, _row
from tweetnook.storage.backend import ArchiveStore
from tweetnook.storage.search_query import (
    count_posts,
    search_posts,
)


def _facts_store(tmp_path) -> ArchiveStore:
    raw_rows = {
        "1": {
            "source": "<a>Twitter for iPhone</a>",
            "card": {"name": "summary"},
            "core": {"user_results": {"result": {"is_blue_verified": True}}},
            "legacy": {
                "in_reply_to_status_id_str": "9",
                "in_reply_to_screen_name": "Alice",
                "is_quote_status": True,
                "quoted_status_id_str": "77",
                "favorite_count": 1500,
                "retweet_count": "12",
                "reply_count": 3,
                "entities": {
                    "user_mentions": [{"screen_name": "Bob"}, {"screen_name": "carol"}],
                    "hashtags": [{"text": "Python"}],
                },
            },
        },
        "2": None,
        "3": {},
        "4": {
            "source": "Twitter Web App",
            "core": {"user_results": {"result": {"legacy": {"verified": True}}}},
            "legacy": {
                "in_reply_to_status_id_str": "",
                "retweeted_status_id_str": "5",
                "is_quote_status": False,
                "favorite_count": 0,
                "entities": {
                    "user_mentions": [],
                    "hashtags": [{"text": "python"}, {"text": "Rust"}],
                },
            },
        },
        "5": {
            "legacy": {
                "in_reply_to_screen_name": "someone",
                "entities": {
                    "user_mentions": [{"id": 1}],
                    "hashtags": [{"text": "a"}, {"text": "b"}],
                },
            }
        },
    }
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    store._merge_records(
        [
            store._record(
                row_key=f"tweet:like::{tweet_id}",
                record_type="tweet",
                tweet_id=tweet_id,
                collection_type="like",
                author_username="alice",
                created_at_ts=int(tweet_id),
                text="facts",
                raw_json=None if raw is None else json.dumps(raw),
            )
            for tweet_id, raw in raw_rows.items()
        ]
    )
    return store


# The JSON expressions each filter evaluated before archive_tweet_facts existed.
_JSON_FILTER_SEMANTICS = {
    "is:reply": "NULLIF(json_extract(raw_json, '$.legacy.in_reply_to_status_id_str'), '') "
    "IS NOT NULL",
    "is:quote": "COALESCE(json_extract(raw_json, '$.legacy.is_quote_status'), 0) != 0",
    "is:retweet": "NULLIF(json_extract(raw_json, '$.legacy.retweeted_status_id_str'), '') "
    "IS NOT NULL",
    "is:thread": "NULLIF(json_extract(raw_json, '$.legacy.in_reply_to_screen_name'), '') "
    "IS NOT NULL AND LOWER(json_extract(raw_json, '$.legacy.in_reply_to_screen_name')) = "
    "LOWER(COALESCE(author_username, ''))",
    "is:verified": "COALESCE(json_extract(raw_json, "
    "'$.core.user_results.result.is_blue_verified'), json_extract(raw_json, "
    "'$.core.user_results.result.legacy.verified'), 0) != 0",
    "min_faves:0": "CAST(COALESCE(json_extract(raw_json, '$.legacy.favorite_count'), 0) "
    "AS INTEGER) >= 0",
    "min_faves:1000": "CAST(COALESCE(json_extract(raw_json, '$.legacy.favorite_count'), 0) "
    "AS INTEGER) >= 1000",
    "min_retweets:12": "CAST(COALESCE(json_extract(raw_json, '$.legacy.retweet_count'), 0) "
    "AS INTEGER) >= 12",
    "min_replies:3": "CAST(COALESCE(json_extract(raw_json, '$.legacy.reply_count'), 0) "
    "AS INTEGER) >= 3",
    "quoted_tweet_id:77": "json_extract(raw_json, '$.legacy.quoted_status_id_str') = '77'",
    "source:iphone": "LOWER(COALESCE(json_extract(raw_json, '$.source'), '')) LIKE '%iphone%'",
    "source:twitter_web": "LOWER(COALESCE(json_extract(raw_json, '$.source'), '')) "
    "LIKE '%twitter web%'",
    "card_name:summary": "json_extract(raw_json, '$.card.name') = 'summary'",
    "to:alice": "LOWER(COALESCE(json_extract(raw_json, "
    "'$.legacy.in_reply_to_screen_name'), '')) = 'alice'",
    "to:@": "LOWER(COALESCE(json_extract(raw_json, '$.legacy.in_reply_to_screen_name'), '')) = ''",
    **{
        query: "EXISTS (SELECT 1 FROM json_each(CASE WHEN json_valid(raw_json) THEN raw_json "
        f"ELSE '{{}}' END, '$.legacy.entities.{path}') AS item WHERE "
        f"LOWER(COALESCE(json_extract(item.value, '$.{field}'), '')) = '{value}')"
        for query, path, field, value in [
            ("mentions:bob", "user_mentions", "screen_name", "bob"),
            ("mentions:@", "user_mentions", "screen_name", ""),
            ("#python", "hashtags", "text", "python"),
            ("hashtag:rust", "hashtags", "text", "rust"),
            ('hashtag:"a b"', "hashtags", "text", "a b"),
        ]
    },
}


@pytest.mark.parametrize("query", sorted(_JSON_FILTER_SEMANTICS))
@pytest.mark.parametrize("negated", [False, True])
def test_fact_filters_match_their_json_semantics(tmp_path, query: str, negated: bool) -> None:
    store = _facts_store(tmp_path)
    try:
        expression = _JSON_FILTER_SEMANTICS[query]
        if negated:
            expression = f"NOT (COALESCE(({expression}), 0))"
        expected = {
            row[0]
            for row in store.conn.execute(
                f"SELECT tweet_id FROM archive WHERE record_type = 'tweet' AND ({expression})"
            )
        }
        actual = search_posts(store, ("-" if negated else "") + query, limit=20)

        assert {row["tweet_id"] for row in actual.rows} == expected
        assert actual.total == len(expected)
    finally:
        store.close()


def test_hidden_tweets_are_left_out_unless_the_query_names_is_hidden(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    store._merge_records(
        [
            store._record(
                row_key=f"tweet:{collection}::{tweet_id}",
                record_type="tweet",
                tweet_id=tweet_id,
                collection_type=collection,
                text=f"needle {tweet_id}",
                author_id="alice",
                author_username="alice",
                created_at=f"2026-01-0{tweet_id}T00:00:00Z",
                created_at_ts=int(tweet_id),
                sort_index=tweet_id,
                raw_json=json.dumps({"legacy": {}}),
            )
            for tweet_id in ("1", "2", "3")
            for collection in ("bookmark", "like")
        ]
    )

    def ids(query: str | None, **options: Any) -> list[str]:
        return [row["tweet_id"] for row in search_posts(store, query, **options).rows]

    store.set_tweet_hidden("2", True)
    store.set_tweet_hidden("2", True)  # Hiding twice keeps one marker.

    assert ids(None) == ["3", "1"]
    assert search_posts(store, None).total == 2
    assert search_posts(store, None, collections={"bookmark"}).total == 2
    assert ids(None, sort="random", random_seed=3, collections={"bookmark", "like"}) != []
    assert "2" not in ids(None, sort="random", random_seed=3, collections={"bookmark", "like"})
    assert ids(None, collections={"like"}, sort="liked_latest") == ["3", "1"]
    assert ids("needle", sort="newest") == ["3", "1"]
    assert ids("from:alice", sort="newest") == ["3", "1"]
    assert ids("is:hidden") == ["2"]
    assert search_posts(store, "is:hidden").total == 1
    assert ids("needle is:hidden") == ["2"]
    assert ids("-is:hidden", sort="newest") == ["3", "1"]
    assert ids("is:hidden OR needle", sort="newest") == ["3", "2", "1"]

    store.set_tweet_hidden("2", False)

    assert ids(None) == ["3", "2", "1"]
    assert search_posts(store, None).total == 3
    assert ids("is:hidden") == []
    store.close()


def test_note_filter_includes_saved_notes_history_and_direct_quote_targets(tmp_path):
    rows = [_row(str(index), f"needle {index}") for index in range(1, 10)]
    rows[0]["raw_json"]["birdwatch_pivot"] = {"subtitle": {"text": "Captured note"}}
    rows[8]["raw_json"]["birdwatch_pivot"] = {"shorttitle": "No actual note"}
    store = _real_store(tmp_path, rows)
    first_member = store._query(expr="row_key = 'tweet:bookmark::1'")[0]
    store._merge_records(
        [
            {**first_member, "row_key": "tweet:like::1", "collection_type": "like"},
            {
                **first_member,
                "row_key": "tweet_object:1",
                "record_type": "tweet_object",
                "collection_type": None,
            },
            store._record(
                row_key="tweet_object:original",
                record_type="tweet_object",
                tweet_id="original",
                text="Quoted context outside every collection",
                raw_json=json.dumps({"birdwatch_pivot": {"subtitle": {"text": "Quote note"}}}),
            ),
            *[
                store._record(
                    row_key=f"tweet_relation:{source}:{kind}:{target}",
                    record_type="tweet_relation",
                    tweet_id=source,
                    relation_type=kind,
                    target_tweet_id=target,
                )
                for source, kind, target in (
                    ("4", "quote_of", "original"),
                    ("5", "quote_of", "past-original"),
                    ("7", "quote_of", "4"),
                    ("8", "retweet_of", "original"),
                )
            ],
        ]
    )
    store.record_community_note("2", {"text": "Current note"})
    store.record_community_note("3", {"text": "Removed note"})
    store.record_community_note("3", None)
    store.record_community_note("past-original", {"text": "Removed quote note"})
    store.record_community_note("past-original", None)

    def ids(query, **options):
        return {row["tweet_id"] for row in search_posts(store, query, **options).rows}

    expected = {"1", "2", "3", "4", "5"}
    assert ids("has:note") == expected
    assert ids("HAS:NOTE", collections={"bookmarks"}) == expected
    assert ids("needle has:note") == expected
    assert ids("has:note OR from:nobody") == expected
    assert ids("from:alice has:note") == expected
    assert ids("-has:note") == {"6", "7", "8", "9"}
    assert count_posts(store, "has:note") == 5
    pages = [search_posts(store, "has:note", page=page, limit=2) for page in (1, 2, 3)]
    assert [row["tweet_id"] for page in pages for row in page.rows] == ["5", "4", "3", "2", "1"]
    assert [page.has_more for page in pages] == [True, True, False]
    # A captured membership and its canonical object still occupy one result slot.
    assert pages[0].total == 5
    store.record_community_note("6", {"text": "New note"})
    assert count_posts(store, "has:note") == 6
    store.close()


@pytest.mark.parametrize(
    "sort", ["newest", "oldest", "random", "most_likes", "liked_latest", "liked_earliest"]
)
def test_uncaptured_entries_are_hidden_by_default_in_every_search_order(tmp_path, sort):
    rows = [_row(str(index), f"needle {index}") for index in range(1, 10)]
    for index in (1, 4, 7):
        rows[index].update(text="", raw_json=None if index != 7 else {"rest_id": "8", "legacy": {}})
    rows[2].update(
        text="This Post is from a suspended account. {learnmore}",
        raw_json={"__typename": "TweetTombstone"},
    )
    rows[3]["raw_json"] = {"legacy": {"full_text": "needle 4"}}
    rows[5].update(text="", raw_json=None)
    rows[8]["raw_json"] = None  # Text-only official archive content is still captured.
    store = _real_store(tmp_path, rows)
    records = []
    for row in store._query(expr="record_type = 'tweet'"):
        records.append(
            {**row, "row_key": f"tweet:like::{row['tweet_id']}", "collection_type": "like"}
        )
    records.extend(
        [
            store._record(
                row_key="tweet_object:4",
                record_type="tweet_object",
                tweet_id="4",
                enrichment_state="terminal_unavailable",
                enrichment_reason="deleted_by_author",
            ),
            store._record(
                row_key="tweet_object:6",
                record_type="tweet_object",
                tweet_id="6",
                text="Recovered content",
                raw_json=json.dumps({"legacy": {"full_text": "Recovered content"}}),
                enrichment_state="done",
            ),
        ]
    )
    store._merge_records(records)
    store.set_tweet_hidden("2", True)  # Both exclusions must count this only once.
    options = {"sort": sort, "collections": {"like"}, "random_seed": 3}

    def ids(query):
        return {row["tweet_id"] for row in search_posts(store, query, **options).rows}

    assert ids(None) == {"1", "4", "6", "7", "9"}
    assert search_posts(store, None, **options).total == 5
    assert count_posts(store, None, collections={"like"}) == 5
    assert ids("from:alice") == {"1", "4", "6", "7", "9"}
    assert ids("needle") == {"1", "4", "7", "9"}
    assert ids("is:uncaptured") == {"3", "5", "8"}
    assert ids("-is:uncaptured") == {"1", "4", "6", "7", "9"}
    assert ids("NOT is:uncaptured") == {"1", "4", "6", "7", "9"}
    assert ids("is:uncaptured OR needle") == {"1", "3", "4", "5", "7", "8", "9"}
    assert ids("is:uncaptured is:hidden") == {"2"}
    assert count_posts(store, "is:uncaptured") == 3
    assert count_posts(store, "is:uncaptured is:hidden") == 1
    store.close()


# Placeholder texts as X returned them and they were saved, without post JSON.
LIVE_PLACEHOLDER_TEXTS = (
    "You\u2019re unable to view this Post because this account owner limits who can view "
    "their Posts. {learnmore}",
    "This Post from @CSlowqmemes has been withheld in all countries (copyright reasons) "
    "in response to a legal demand. {learnmore}",
    "This Post is from a suspended account. {learnmore}",
)


def test_live_placeholder_texts_are_uncaptured_and_saved_text_is_not(tmp_path):
    texts = (*LIVE_PLACEHOLDER_TEXTS, "This took me 4 months to find https://t.co/4r60bChBtm")
    rows = [_row(str(index), text) for index, text in enumerate(texts, start=1)]
    for row in rows:
        row["raw_json"] = None
    suspended = "This account is suspended. {learnmore}"
    rows.append(_row("5", suspended))
    rows[-1]["raw_json"] = {"legacy": {"full_text": suspended}}
    store = _real_store(tmp_path, rows)
    store._merge_records(
        [
            store._record(
                row_key="tweet_object:4",
                record_type="tweet_object",
                tweet_id="4",
                enrichment_state="terminal_unavailable",
                enrichment_reason="suspended_account",
            )
        ]
    )

    assert [row["tweet_id"] for row in search_posts(store, None).rows] == ["4"]
    assert count_posts(store, None) == 1
    assert {row["tweet_id"] for row in search_posts(store, "is:uncaptured").rows} == {
        "1",
        "2",
        "3",
        "5",
    }
    store.close()


def test_uncaptured_default_preserves_feed_cursor_paging_and_updates(tmp_path):
    store = _real_store(tmp_path, [_row(str(index), f"post {index}") for index in range(1, 7)])
    with store.conn:
        store.conn.execute(
            "UPDATE archive SET text = '', raw_json = NULL WHERE tweet_id IN ('2', '4')"
        )
    seen = []
    cursor = ""
    while cursor is not None:
        page = search_posts(store, None, limit=1, cursor=cursor)
        assert page.total == 4
        seen.extend(row["tweet_id"] for row in page.rows)
        cursor = page.next_cursor
    assert seen == ["6", "5", "3", "1"]
    assert count_posts(store, None) == 4
    with store.conn:
        store.conn.execute("UPDATE archive SET text = 'newly captured' WHERE tweet_id = '4'")
    assert count_posts(store, None) == 5
    assert [row["tweet_id"] for row in search_posts(store, "is:uncaptured").rows] == ["2"]
    store.close()


def test_description_filter_matches_media_tag_descriptions_only(tmp_path) -> None:
    store = _real_store(
        tmp_path,
        [
            _row("1", "Weekend photo"),
            _row("2", "Pet picture"),
            _row("3", "A red car I saw"),
            _row("4", "Quoting the photo"),
        ],
    )
    store._merge_records(
        [
            store._record(
                row_key="tweet_relation:4:quote_of:1",
                record_type="tweet_relation",
                tweet_id="4",
                relation_type="quote_of",
                target_tweet_id="1",
            )
        ]
    )
    store.update_media_tags("1", ["cars"], description="A red car parked by the sea")
    store.update_media_tags("2", ["pets"], description="Cat asleep on a sofa")

    def ids(query: str) -> list[str]:
        return sorted(row["tweet_id"] for row in search_posts(store, query).rows)

    assert ids("description:car") == ["1", "4"]
    assert ids('description:"Sea RED"') == ["1", "4"]
    assert ids('description:"car sofa"') == []
    assert ids('description:"car OR sofa"') == []
    assert ids("description:ca*") == ["1", "2", "4"]
    assert ids("-description:car") == ["2", "3"]
    assert ids("description:sofa OR from:nobody") == ["2"]
    assert ids("red") == ["3"]

    store.update_media_tags("2", ["pets"], description="Dog on a rug")
    assert ids("description:sofa") == []
    assert ids("description:dog") == ["2"]
    store.update_media_tags("2", ["pets"])
    assert ids("description:dog") == ["2"]
    store.delete_media_tag("2")
    assert ids("description:dog") == []
    assert store.conn.execute("SELECT COUNT(*) FROM archive_description_fts").fetchone()[0] == 1
