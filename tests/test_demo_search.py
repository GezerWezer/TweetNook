"""Exercise the static adapter and SQLite search with the same synthetic archive."""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from tweetnook.core.collection_names import normalize_collection_name
from tweetnook.storage.backend import ArchiveStore
from tweetnook.storage.search_query import SearchQueryError, count_posts, search_posts

ROOT = Path(__file__).resolve().parents[1]


def search_fixture():
    fixture = json.loads((ROOT / "tests/fixtures/demo-synthetic.json").read_text())
    template = fixture["tweets"][0]
    authors = fixture["authors"]
    authors[0]["username"], authors[0]["display_name"] = "alice", "Alice Smith"
    authors[1]["username"], authors[1]["display_name"] = "bob", "Bob Jones"
    fixture["tweets"] = []
    texts = [
        "cats café co-operate foo_bar red bicycles cafe\u0301 ộ Straße Σίσυφος \u13a0 \u10e7 😀",
        "dogs dogs dogs",
        "quoted original",
        "cats and dogs in a red bike workshop",
        None,
        "cats hidden",
        "red bike missing",
        "dogs returned",
        "cats without date",
        "link card",
        "cats",
    ]
    for index, text in enumerate(texts, 1):
        tweet = copy.deepcopy(template)
        tweet.update(
            tweet_id=str(index),
            author_id=authors[index % 2]["id"],
            text=text,
            created_at=f"2026-01-{index:02d}T00:00:00Z",
            collections=["like"],
            liked_order=100 - index,
            engagement={"likes": index * 10, "retweets": index, "replies": 0},
            hashtags=[],
            mentions=[],
            urls=[],
            media=[],
            tags=[],
            description="",
            quoted_tweet_id=None,
            reply_to_id=None,
            reply_to_username=None,
            conversation_id=str(index),
            source="Twitter Web App",
            card_name=None,
        )
        fixture["tweets"].append(tweet)
    (
        first,
        second,
        quoted,
        reply,
        uncaptured,
        hidden,
        missing,
        resurrected,
        undated,
        card,
        multiple,
    ) = fixture["tweets"]
    first.update(
        tags=["Straße", "onlytag", "\u13a0", "ΐ"],
        description="A café with red bicycles",
        hashtags=["Straße"],
        urls=[
            {"expanded_url": "https://example.test/onlyurl", "display_url": "example.test/onlyurl"}
        ],
    )
    second.update(quoted_tweet_id="3", mentions=["alice"], collections=["like", "bookmark"])
    quoted.update(
        collections=[], tags=["quotedtag"], description="a purple bicycle", note_history=True
    )
    reply.update(
        reply_to_id="1",
        reply_to_username="bob",
        conversation_id="1",
        media=[{"type": "video", "url": "demo/media/test.mp4"}],
    )
    uncaptured.update(uncaptured=True)
    hidden.update(hidden=True)
    missing.update(enrichment_state="terminal_unavailable")
    resurrected.update(enrichment_state="resurrected")
    undated.update(created_at=None)
    card.update(
        media=[{"type": "photo", "source": "link_card", "url": "demo/media/test.jpg"}],
        card_name="summary",
    )
    multiple.update(collections=["bookmark", "like"], created_at=first["created_at"])
    return fixture


def sqlite_fixture(tmp_path, fixture):
    store = ArchiveStore(tmp_path / "search.db", create=True)
    authors = {author["id"]: author for author in fixture["authors"]}
    records = []
    for tweet in fixture["tweets"]:
        tid = tweet["tweet_id"]
        author = authors[tweet["author_id"]]
        raw = {
            "source": tweet["source"],
            "legacy": {
                "full_text": tweet["text"],
                "conversation_id_str": tweet["conversation_id"],
                "in_reply_to_status_id_str": tweet["reply_to_id"],
                "in_reply_to_screen_name": tweet["reply_to_username"],
                "is_quote_status": bool(tweet["quoted_tweet_id"]),
                "quoted_status_id_str": tweet["quoted_tweet_id"],
                "favorite_count": tweet["engagement"]["likes"],
                "retweet_count": tweet["engagement"]["retweets"],
                "reply_count": tweet["engagement"]["replies"],
                "entities": {
                    "hashtags": [{"text": tag} for tag in tweet["hashtags"]],
                    "user_mentions": [{"screen_name": name} for name in tweet["mentions"]],
                },
            },
            "card": {"name": tweet["card_name"]} if tweet["card_name"] else None,
            "core": {"user_results": {"result": {"is_blue_verified": author["verified"]}}},
        }
        if tweet.get("community_note"):
            raw["birdwatch_pivot"] = {"subtitle": {"text": tweet["community_note"]}}
        values = dict(
            tweet_id=tid,
            text=tweet["text"],
            author_id=author["id"],
            author_username=author["username"],
            author_display_name=author["display_name"],
            created_at=tweet["created_at"],
            created_at_ts=int(datetime.fromisoformat(tweet["created_at"]).timestamp())
            if tweet["created_at"]
            else None,
            sort_index=str(tweet["liked_order"] or tid),
            raw_json=json.dumps(raw),
            conversation_id=tweet["conversation_id"],
        )
        for collection in tweet["collections"]:
            records.append(
                store._record(
                    row_key=f"tweet:{collection}::{tid}",
                    record_type="tweet",
                    collection_type=collection,
                    **values,
                )
            )
        records.append(
            store._record(
                row_key=f"tweet_object:{tid}",
                record_type="tweet_object",
                enrichment_state=tweet.get("enrichment_state", "done"),
                **values,
            )
        )
        if tweet["tags"] or tweet["description"]:
            records.append(
                store._record(
                    row_key=f"media_tag:{tid}",
                    record_type="media_tag",
                    tweet_id=tid,
                    raw_json=json.dumps(
                        {"tags": tweet["tags"], "description": tweet["description"]}
                    ),
                )
            )
        if tweet["quoted_tweet_id"]:
            records.append(
                store._record(
                    row_key=f"quote:{tid}",
                    record_type="tweet_relation",
                    tweet_id=tid,
                    target_tweet_id=tweet["quoted_tweet_id"],
                    relation_type="quote_of",
                )
            )
        for index, media in enumerate(tweet["media"]):
            records.append(
                store._record(
                    row_key=f"media:{tid}:{index}",
                    record_type="media",
                    tweet_id=tid,
                    media_key=str(index),
                    media_type=media["type"],
                    source=media.get("source"),
                )
            )
        for index, url in enumerate(tweet["urls"]):
            records.append(
                store._record(
                    row_key=f"url:{tid}:{index}",
                    record_type="url_ref",
                    tweet_id=tid,
                    expanded_url=url["expanded_url"],
                    display_url=url["display_url"],
                )
            )
    store._merge_records(records)
    for tweet in fixture["tweets"]:
        if tweet.get("hidden"):
            store.set_tweet_hidden(tweet["tweet_id"], True)
        if tweet.get("note_history"):
            store.conn.execute(
                "INSERT INTO community_notes"
                "(tweet_id, text, text_hash, first_seen_at, last_seen_at) "
                "VALUES (?, 'past note', 'hash', '1', '1')",
                (tweet["tweet_id"],),
            )
            store.conn.commit()
    store.conn.commit()
    return store


def demo_results(fixture, requests):
    node = shutil.which("node")
    assert node, "Node is required for static demo search parity checks"
    result = subprocess.run(
        [node, str(ROOT / "tests/js/demo_search_runner.cjs")],
        input=json.dumps({"fixture": fixture, "requests": requests}),
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    return json.loads(result.stdout)


def test_demo_search_matches_sqlite(tmp_path):
    fixture = search_fixture()
    queries = [
        "",
        "cats",
        "cats OR dogs from:alice",
        "cats OR dogs from:bob",
        "cats dogs OR missing",
        "cats AND dogs",
        "cats NOT dogs",
        "cats -dogs",
        "NOT cats",
        '-"red bike"',
        "cafe",
        "café",
        "cafe\u0301",
        "ộ",
        "o",
        "Straße",
        "ΣΊΣΥΦΟΣ",
        "\u13a0",
        "\u10e7",
        "😀",
        "tag:\u13a0",
        "tag:\u0399̈́",
        "co-operate",
        "foo_bar",
        "foo",
        "bar",
        '"red bike"',
        '"red bicy"',
        "bicy*",
        "bicycles",
        "onlytag",
        "onlyurl",
        '"Smith cats"',
        "cats or dogs",
        "tag:onlytag",
        "tag:quotedtag",
        "-tag:quotedtag",
        "tag:STRASSE",
        "#STRASSE",
        "description:cafe",
        'description:"red bicy*"',
        "description:purple",
        "-description:purple",
        "has:note",
        "-has:note",
        "has:image",
        "has:media",
        "has:video",
        "has:links",
        "filter:images",
        "filter:media",
        "filter:videos",
        "filter:links",
        "is:quote",
        "is:reply",
        "is:thread",
        "is:verified",
        "is:retweet",
        "is:missing",
        "is:resurrected",
        "is:hidden",
        "-is:hidden",
        "cats OR is:hidden",
        "is:uncaptured",
        "-has:media",
        "-is:uncaptured",
        "cats OR is:uncaptured",
        "from:@alice",
        "to:bob",
        "mentions:alice",
        "min_faves:50",
        "min_faves:5_0",
        "min_retweets:3",
        "min_replies:1",
        "since:2026-01-02",
        "until:2026-01-05",
        "-since:2026-01-02",
        "since:2026-01-02_12:00:00",
        "since_time:1767312000.5",
        "since_time:1_767_312_000.5",
        "until_time:1767312000.5",
        "since_id:3",
        "max_id:3",
        "since_id:9223372036854775807",
        "max_id:-9223372036854775808",
        "conversation_id:1",
        "quoted_tweet_id:3",
        "source:Web_App",
        "card_name:summary",
        "url:example.test",
        "url:example%onlyurl",
        "url:example.test/only_rl",
    ]
    requests = [
        {"q": query, "sort": sort, "limit": "100", "random_seed": "42"}
        for query in queries
        for sort in ("default", "relevance", "oldest", "newest", "random", "most_likes")
    ]
    for collection in (
        "all",
        "like",
        "likes",
        "bookmark",
        "bookmarks",
        "tweet",
        "tweets",
        "invalid",
    ):
        requests.append(
            {"q": "cats OR dogs", "collection": collection, "limit": "100", "random_seed": "42"}
        )
    for sort in ("liked_latest", "liked_earliest"):
        for query in ("", "cats OR dogs", "has:note"):
            requests.append(
                {
                    "q": query,
                    "collection": "likes",
                    "sort": sort,
                    "limit": "100",
                    "random_seed": "42",
                }
            )
    store = sqlite_fixture(tmp_path, fixture)
    try:
        results = demo_results(fixture, requests)
        for params, actual in zip(requests, results, strict=True):
            try:
                collection = normalize_collection_name(params.get("collection", "all"))
            except ValueError:
                collection = "all"
            collections = {collection} if collection != "all" else None
            expected = search_posts(
                store,
                params["q"],
                collections=collections,
                sort=params.get("sort", "default"),
                limit=100,
                random_seed=42,
            )
            assert actual["status"] == 200, (params, actual)
            assert actual["body"]["tweets"] == [row["tweet_id"] for row in expected.rows], params
            assert actual["body"]["total"] == expected.total, params
            assert actual["body"]["pages"] == expected.pages, params
            assert actual["body"]["has_more"] == expected.has_more, params
            assert actual["count"]["total"] == count_posts(
                store, params["q"], collections=collections
            ), params
    finally:
        store.close()


def test_demo_rejects_invalid_searches_like_sqlite(tmp_path):
    fixture = search_fixture()
    invalid = [
        "OR cats",
        "cats OR",
        "cats AND",
        "NOT",
        "NOT NOT cats",
        "cats OR AND dogs",
        '""',
        "unknown:value",
        "has:bogus",
        "is:bogus",
        "filter:bogus",
        "since:2026-02-30",
        "since:yesterday",
        "description:***",
        'tag:""',
        "min_faves:nope",
        "min_replies:1.5",
        "since_id:9223372036854775808",
        "max_id:-9223372036854775809",
        "since_time:nan",
        "since_time:0x10",
        "until_time:inf",
    ]
    results = demo_results(fixture, [{"q": query} for query in invalid])
    store = sqlite_fixture(tmp_path, fixture)
    try:
        for query, actual in zip(invalid, results, strict=True):
            with pytest.raises(SearchQueryError):
                search_posts(store, query)
            assert actual["status"] == 400, (query, actual)
            assert actual["count_status"] == 400, (query, actual)
    finally:
        store.close()
