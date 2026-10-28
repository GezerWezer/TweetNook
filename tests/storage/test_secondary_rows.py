from __future__ import annotations

import pytest

from tests.conftest import (
    list_article_rows,
    make_article_result,
    make_tweet_result,
    make_url_entity,
)
from tests.storage.conftest import _complex_tweet, _counts, _tweet, _tweet_with_created_at
from tweetnook.core.timeline_parsing import TimelineTweet
from tweetnook.storage import open_archive_store


def test_export_rows_only_returns_tweet_records(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    store.set_archive_owner_id("42")
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out="cursor-1",
        http_status=200,
        raw_json={"ok": True},
        tweets=[_tweet("1")],
        last_head_tweet_id="1",
        backfill_cursor="cursor-1",
        backfill_incomplete=True,
    )

    exported = store.export_rows("all")

    assert len(exported) == 1
    assert exported[0]["tweet_id"] == "1"
    assert exported[0]["collection"]["type"] == "bookmark"
    store.close()


def test_export_rows_limit_only_fetches_secondary_rows_for_selected_tweets(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    older = _complex_tweet("100")
    newer = _complex_tweet("200")
    newer.created_at = "Sun Mar 15 00:00:00 +0000 2026"
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[older, newer],
        last_head_tweet_id="200",
        backfill_cursor=None,
        backfill_incomplete=False,
    )

    seen: list[tuple[str, tuple[str, ...]]] = []
    original = store._rows_for_values

    def wrapped(record_type: str, field_name: str, values, *, columns=None):
        if field_name == "tweet_id":
            seen.append((record_type, tuple(values)))
        return original(record_type, field_name, values, columns=columns)

    monkeypatch.setattr(store, "_rows_for_values", wrapped)

    exported = store.export_rows("bookmark", sort="newest", limit=1, include_raw_json=False)

    assert [row["tweet_id"] for row in exported] == ["200"]
    assert exported[0]["raw_json"] is None
    assert seen
    for record_type, values in seen:
        assert values == ("200",), f"{record_type} fetched secondary rows for unexpected tweets"
    store.close()


def test_export_rows_sorts_by_created_at_and_puts_unknown_dates_last(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    tweets = [
        _tweet_with_created_at(
            "older",
            created_at="Tue Oct 09 21:39:26 +0000 2012",
            sort_index="999",
        ),
        _tweet_with_created_at(
            "newer",
            created_at="Thu Apr 11 03:55:13 +0000 2024",
            sort_index="-999",
        ),
        _tweet_with_created_at(
            "unknown",
            created_at=None,
            sort_index="-1000",
        ),
    ]
    store.persist_page(
        operation="Likes",
        collection_type="like",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=tweets,
        last_head_tweet_id="newer",
        backfill_cursor=None,
        backfill_incomplete=False,
    )

    oldest = store.export_rows("like", sort="oldest")
    newest = store.export_rows("like", sort="newest")

    assert [row["tweet_id"] for row in oldest] == ["older", "newer", "unknown"]
    assert [row["tweet_id"] for row in newest] == ["newer", "older", "unknown"]
    store.close()


def test_search_fts_projects_posts_and_articles_with_aggregated_collections(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    tweet = _complex_tweet("100")
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[tweet],
        last_head_tweet_id="100",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.persist_page(
        operation="Likes",
        collection_type="like",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[tweet],
        last_head_tweet_id="100",
        backfill_cursor=None,
        backfill_incomplete=False,
    )

    post_results = store.search_fts("root", limit=5)
    article_results = store.search_fts("article", limit=5)
    article_only_posts = store.search_fts("article", limit=5, types={"post"})
    bookmark_articles = store.search_fts("article", limit=5, collections={"bookmark"})
    tweet_articles = store.search_fts("article", limit=5, collections={"tweet"})

    assert [(row["type"], row["tweet_id"], row["collections"]) for row in post_results] == [
        ("post", "100", ["bookmark", "like"])
    ]
    assert post_results[0]["author_username"] == "user1000"
    assert post_results[0]["text"] == "root longform text"

    assert [(row["type"], row["tweet_id"], row["collections"]) for row in article_results] == [
        ("article", "100", ["bookmark", "like"])
    ]
    assert str(article_results[0]["text"]).startswith("Article title")

    assert article_only_posts == []
    assert [(row["type"], row["tweet_id"]) for row in bookmark_articles] == [("article", "100")]
    assert tweet_articles == []
    store.close()


def test_persist_page_extracts_secondary_objects_once_across_collections(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    tweet = _complex_tweet()
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out="cursor-1",
        http_status=200,
        raw_json={"ok": True},
        tweets=[tweet],
        last_head_tweet_id=tweet.tweet_id,
        backfill_cursor="cursor-1",
        backfill_incomplete=True,
    )
    store.persist_page(
        operation="Likes",
        collection_type="like",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[tweet],
        last_head_tweet_id=tweet.tweet_id,
        backfill_cursor=None,
        backfill_incomplete=False,
    )

    counts = store.counts()
    assert counts == _counts(
        raw_captures=2,
        tweets=2,
        collections=2,
        tweet_objects=2,
        tweet_relations=1,
        media=3,
        urls=2,
        url_refs=2,
        articles=1,
        sync_state=2,
    )

    article_rows = store._query(expr="record_type = 'article'")
    assert article_rows[0]["title"] == "Article title"
    assert article_rows[0]["content_text"] == "Article body"
    media_rows = store._query(expr="record_type = 'media'")
    assert any(row["source"] == "article_cover" for row in media_rows)
    relation_rows = store._query(expr="record_type = 'tweet_relation'")
    assert relation_rows[0]["relation_type"] == "quote_of"
    assert relation_rows[0]["target_tweet_id"] == "200"
    store.close()


def test_persist_page_extracts_secondary_objects_once_for_authored_tweets(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    tweet = _complex_tweet()
    store.persist_page(
        operation="UserTweets",
        collection_type="tweet",
        cursor_in=None,
        cursor_out="cursor-1",
        http_status=200,
        raw_json={"ok": True},
        tweets=[tweet],
        last_head_tweet_id=tweet.tweet_id,
        backfill_cursor="cursor-1",
        backfill_incomplete=True,
    )
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

    counts = store.counts()
    assert counts == _counts(
        raw_captures=2,
        tweets=2,
        collections=2,
        tweet_objects=2,
        tweet_relations=1,
        media=3,
        urls=2,
        url_refs=2,
        articles=1,
        sync_state=2,
    )

    exported = store.export_rows("tweet")
    assert [row["tweet_id"] for row in exported] == [tweet.tweet_id]
    assert exported[0]["collection"]["type"] == "tweet"
    store.close()


def test_rehydrate_from_raw_json_rebuilds_secondary_rows(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    tweet = _complex_tweet()
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out="cursor-1",
        http_status=200,
        raw_json={"ok": True},
        tweets=[tweet],
        last_head_tweet_id=tweet.tweet_id,
        backfill_cursor="cursor-1",
        backfill_incomplete=True,
    )
    store._delete(
        "record_type IN ('tweet_object', 'tweet_relation', 'media', 'url', 'url_ref', 'article')"
    )
    tweet_row = store._query(expr="record_type = 'tweet'", limit=1)[0]
    tweet_row["text"] = "root short"
    tweet_row["author_username"] = None
    tweet_row["author_display_name"] = None
    store._merge_records([tweet_row])

    result = store.rehydrate_from_raw_json()

    assert result.tweets_updated == 1
    assert result.secondary_records == 11
    counts = store.counts()
    assert counts["tweet_objects"] == 2
    assert counts["tweet_relations"] == 1
    assert counts["media"] == 3
    assert counts["urls"] == 2
    assert counts["url_refs"] == 2
    assert counts["articles"] == 1
    rebuilt_row = store._query(expr="record_type = 'tweet'", limit=1)[0]
    assert rebuilt_row["text"] == "root longform text"
    assert rebuilt_row["author_username"] == "user1000"
    assert rebuilt_row["note_tweet_text"] == "root longform text"
    store.close()


def test_persist_page_preserves_richer_secondary_values_from_existing_rows(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    rich_tweet = _complex_tweet()
    thin_quoted = make_tweet_result(
        "200",
        "quoted short",
        user_id="2000",
        media=[{"media_key": "7_quoted", "type": "video"}],
    )
    thin_raw = make_tweet_result(
        "100",
        "root short",
        user_id="1000",
        urls=[
            make_url_entity(
                "https://t.co/root",
                "https://example.com/story",
                display_url="example.com/story",
            )
        ],
        media=[{"media_key": "3_root", "type": "photo"}],
        quoted_tweet=thin_quoted,
        article=make_article_result(
            "article-1",
            title="Article title",
            preview_text="Article preview",
            plain_text=None,
            url=None,
        ),
    )
    thin_tweet = TimelineTweet(
        tweet_id="100",
        text="root short",
        author_id="1000",
        author_username="user1000",
        author_display_name="User 1000",
        created_at="Sat Mar 14 00:00:00 +0000 2026",
        sort_index="10",
        raw_json=thin_raw,
    )

    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[rich_tweet],
        last_head_tweet_id="100",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[thin_tweet],
        last_head_tweet_id="100",
        backfill_cursor=None,
        backfill_incomplete=False,
    )

    article_row = store._query(expr="record_type = 'article'", limit=1)[0]
    assert article_row["content_text"] == "Article body"
    assert article_row["canonical_url"] == "https://x.com/i/article/123"
    assert article_row["status"] == "body_present"

    media_rows = store._query(expr="record_type = 'media'")
    media_by_key = {row["media_key"]: row for row in media_rows}
    assert media_by_key["3_root"]["media_url"] == "https://pbs.twimg.com/media/root.jpg"
    assert (
        media_by_key["7_quoted"]["media_url"]
        == "https://video.twimg.com/ext_tw_video/quoted-hd.mp4"
    )
    assert media_by_key["7_quoted"]["variants_json"] is not None
    store.close()


def test_secondary_row_listings_filter_through_sqlite(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    body_tweet = _complex_tweet()
    preview_raw = make_tweet_result(
        "101",
        "preview short",
        user_id="1001",
        urls=[
            make_url_entity(
                "https://t.co/preview",
                "https://preview.example.com/post",
                display_url="preview.example.com/post",
            )
        ],
        article=make_article_result(
            "article-2",
            title="Preview article",
            preview_text="Preview only",
            plain_text=None,
            url="https://x.com/i/article/456",
        ),
    )
    preview_tweet = TimelineTweet(
        tweet_id="101",
        text="preview short",
        author_id="1001",
        author_username="user1001",
        author_display_name="User 1001",
        created_at="Sat Mar 14 00:00:00 +0000 2026",
        sort_index="11",
        raw_json=preview_raw,
    )

    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[body_tweet, preview_tweet],
        last_head_tweet_id="101",
        backfill_cursor=None,
        backfill_incomplete=False,
    )

    root_photo = next(row for row in store.list_media_rows() if row["media_key"] == "3_root")
    store.merge_rows(
        [
            store.build_media_download_update(
                root_photo,
                download_state="done",
                local_path="media/root.jpg",
                sha256="root-sha",
                byte_size=123,
                content_type="image/jpeg",
                thumbnail_local_path=None,
                thumbnail_sha256=None,
                thumbnail_byte_size=None,
                thumbnail_content_type=None,
                downloaded_at="2026-03-16T00:00:00Z",
                download_error=None,
            )
        ]
    )

    root_url = next(
        row
        for row in store.list_url_rows()
        if row["canonical_url"] == "https://example.com/story?keep=1"
    )
    store.merge_rows(
        [
            store.build_url_unfurl_update(
                root_url,
                http_status=200,
                final_url="https://example.com/story?keep=1",
                canonical_url="https://example.com/story?keep=1",
                title="Root title",
                description="Root description",
                site_name="Example",
                content_type="text/html",
                unfurl_state="done",
                last_fetched_at="2026-03-16T00:00:00Z",
                download_error=None,
            )
        ]
    )

    pending_video_rows = store.list_media_rows(states={"pending"}, media_types={"video"})
    assert [(row["tweet_id"], row["media_key"]) for row in pending_video_rows] == [
        ("200", "7_quoted")
    ]

    done_photo_rows = store.list_media_rows(states={"done"}, media_types={"photo"})
    assert [(row["tweet_id"], row["media_key"]) for row in done_photo_rows] == [("100", "3_root")]

    pending_url_rows = store.list_url_rows(states={"pending"})
    assert [row["canonical_url"] for row in pending_url_rows] == [
        "https://preview.example.com/post",
        "https://quoted.example.com/post",
    ]

    done_url_rows = store.list_url_rows(states={"done"})
    assert [row["canonical_url"] for row in done_url_rows] == ["https://example.com/story?keep=1"]

    preview_rows = list_article_rows(store, preview_only=True)
    assert [row["tweet_id"] for row in preview_rows] == ["101"]
    assert preview_rows[0]["status"] == "preview_only"
    store.close()
