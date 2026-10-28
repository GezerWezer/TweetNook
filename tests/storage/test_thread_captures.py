from __future__ import annotations

from tests.conftest import (
    make_tweet_detail_response,
    make_tweet_result,
    make_url_entity,
)
from tests.storage.conftest import _complex_tweet
from tweetnook.core.timeline_parsing import TimelineTweet
from tweetnook.storage import open_archive_store


def test_persist_thread_detail_keeps_context_tweets_out_of_memberships(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    root_raw = make_tweet_result(
        "100",
        "reply tweet",
        user_id="1000",
        in_reply_to_status_id="200",
        conversation_id="200",
        urls=[
            make_url_entity(
                "https://t.co/thread",
                "https://x.com/example/status/300?s=20",
                display_url="x.com/example/status/300",
            )
        ],
    )
    root_tweet = TimelineTweet(
        tweet_id="100",
        text="reply tweet",
        author_id="1000",
        author_username="user1000",
        author_display_name="User 1000",
        created_at="Sat Mar 14 00:00:00 +0000 2026",
        sort_index="10",
        raw_json=root_raw,
    )
    parent_raw = make_tweet_result("200", "parent tweet", user_id="2000")
    parent_tweet = TimelineTweet(
        tweet_id="200",
        text="parent tweet",
        author_id="2000",
        author_username="user2000",
        author_display_name="User 2000",
        created_at="Sat Mar 14 00:00:00 +0000 2026",
        sort_index="9",
        raw_json=parent_raw,
    )

    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[root_tweet],
        last_head_tweet_id="100",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.persist_thread_detail(
        focal_tweet_id="100",
        tweets=[root_tweet, parent_tweet],
        raw_json=make_tweet_detail_response([root_raw, parent_raw]),
    )

    membership_rows = store._query(expr="record_type = 'tweet'")
    assert [row["tweet_id"] for row in membership_rows] == ["100"]

    tweet_object_rows = store._query(expr="record_type = 'tweet_object'")
    assert {row["tweet_id"] for row in tweet_object_rows} == {"100", "200"}
    relation_rows = store._query(expr="record_type = 'tweet_relation'")
    relations = {
        (row["tweet_id"], row["relation_type"], row["target_tweet_id"]) for row in relation_rows
    }
    assert ("100", "reply_to", "200") in relations
    assert ("100", "thread_parent", "200") in relations
    assert ("200", "thread_child", "100") in relations
    assert ("100", "links_to_status", "300") in relations

    raw_capture_rows = store._query(expr="record_type = 'raw_capture'")
    assert any(row["operation"] == "ThreadExpandDetail" for row in raw_capture_rows)
    store.close()


def test_rehydrate_from_raw_json_rebuilds_thread_capture_secondary_rows(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    root_raw = make_tweet_result(
        "100",
        "reply tweet",
        user_id="1000",
        in_reply_to_status_id="200",
        conversation_id="200",
        urls=[
            make_url_entity(
                "https://t.co/thread",
                "https://x.com/example/status/300?s=20",
                display_url="x.com/example/status/300",
            )
        ],
    )
    parent_raw = make_tweet_result("200", "parent tweet", user_id="2000")
    root_tweet = TimelineTweet(
        tweet_id="100",
        text="reply tweet",
        author_id="1000",
        author_username="user1000",
        author_display_name="User 1000",
        created_at="Sat Mar 14 00:00:00 +0000 2026",
        sort_index="10",
        raw_json=root_raw,
    )
    parent_tweet = TimelineTweet(
        tweet_id="200",
        text="parent tweet",
        author_id="2000",
        author_username="user2000",
        author_display_name="User 2000",
        created_at="Sat Mar 14 00:00:00 +0000 2026",
        sort_index="9",
        raw_json=parent_raw,
    )

    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[root_tweet],
        last_head_tweet_id="100",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.persist_thread_detail(
        focal_tweet_id="100",
        tweets=[root_tweet, parent_tweet],
        raw_json=make_tweet_detail_response([root_raw, parent_raw]),
    )
    store._delete(
        "record_type IN ('tweet_object', 'tweet_relation', 'media', 'url', 'url_ref', 'article')"
    )

    result = store.rehydrate_from_raw_json()

    assert result.secondary_records > 0
    tweet_object_rows = store._query(expr="record_type = 'tweet_object'")
    assert {row["tweet_id"] for row in tweet_object_rows} == {"100", "200"}
    relation_rows = store._query(expr="record_type = 'tweet_relation'")
    relations = {
        (row["tweet_id"], row["relation_type"], row["target_tweet_id"]) for row in relation_rows
    }
    assert ("100", "reply_to", "200") in relations
    assert ("100", "thread_parent", "200") in relations
    assert ("200", "thread_child", "100") in relations
    assert ("100", "links_to_status", "300") in relations
    store.close()


def test_successful_live_refresh_preserves_resurrected_state(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    tweet = _complex_tweet()

    def persist_page() -> None:
        store.persist_page(
            operation="Bookmarks",
            collection_type="bookmark",
            cursor_in=None,
            cursor_out=None,
            http_status=200,
            raw_json={"ok": True},
            tweets=[tweet],
            last_head_tweet_id=tweet.tweet_id,
            backfill_cursor=None,
            backfill_incomplete=False,
        )

    persist_page()
    store.update_tweet_object_enrichment(
        tweet.tweet_id,
        enrichment_state="terminal_unavailable",
        enrichment_checked_at=None,
        enrichment_http_status=404,
        enrichment_reason="deleted_by_author",
    )

    persist_page()
    assert store._get_row(f"tweet_object:{tweet.tweet_id}")["enrichment_state"] == "resurrected"

    persist_page()
    store.persist_tweet_detail(tweet=tweet, raw_json=make_tweet_detail_response([tweet.raw_json]))
    store.persist_thread_detail(
        focal_tweet_id=tweet.tweet_id,
        tweets=[tweet],
        raw_json=make_tweet_detail_response([tweet.raw_json]),
    )

    assert store._get_row(f"tweet_object:{tweet.tweet_id}")["enrichment_state"] == "resurrected"
    store.close()


def test_export_rows_include_secondary_objects(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    tweet = _complex_tweet()
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[tweet],
        last_head_tweet_id=tweet.tweet_id,
        backfill_cursor=None,
        backfill_incomplete=False,
    )

    exported = store.export_rows("bookmark")

    assert len(exported) == 1
    row = exported[0]
    assert row["article"]["title"] == "Article title"
    assert row["article"]["media"][0]["source"] == "article_cover"
    assert any(item["source"] == "tweet_media" for item in row["media"])
    assert row["urls"][0]["resolved"]["canonical_url"] == "https://example.com/story?keep=1"
    store.close()
