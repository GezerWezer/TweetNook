from __future__ import annotations

import asyncio

import httpx
import pytest

from tests.collection_sync.conftest import (
    _bookmarks_response_from_results,
    _console,
    _op_and_variables,
    _save_query_ids,
)
from tests.conftest import (
    make_article_result,
    make_bookmarks_response,
    make_likes_response,
    make_tweet_result,
    make_user_tweets_response,
)
from tweetnook.config import AppConfig
from tweetnook.core.timeline_parsing import TimelineTweet
from tweetnook.storage import open_archive_store
from tweetnook.sync.collection import sync_collection


@pytest.mark.asyncio
async def test_collection_scoped_duplicate_detection(paths, config: AppConfig, auth_bundle) -> None:
    _save_query_ids(paths)
    store = open_archive_store(paths, create=True)
    assert store is not None
    store.persist_page(
        operation="Likes",
        collection_type="like",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"seed": True},
        tweets=[
            TimelineTweet(
                tweet_id="1",
                text="preexisting",
                author_id="1",
                author_username="user1",
                author_display_name="User 1",
                created_at="date",
                sort_index="10",
                raw_json={"tweet": "1"},
            )
        ],
        last_head_tweet_id="1",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.close()

    async def handler(request: httpx.Request) -> httpx.Response:
        operation, variables = _op_and_variables(request)
        cursor = variables.get("cursor")
        count = int(variables["count"])
        if count == 1:
            return httpx.Response(200, json=make_bookmarks_response(["1"]), request=request)
        if cursor is None:
            return httpx.Response(
                200, json=make_bookmarks_response(["1"], cursor="c1"), request=request
            )
        if cursor == "c1":
            return httpx.Response(200, json=make_bookmarks_response(["2"]), request=request)
        raise AssertionError(f"unexpected request {operation} {cursor}")

    result = await sync_collection(
        "bookmarks",
        full=False,
        limit=None,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        query_ids={"Bookmarks": "qid-bookmarks"},
        transport=httpx.MockTransport(handler),
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
    )
    assert result.pages_fetched == 2


@pytest.mark.parametrize("collection", ["likes", "tweets"])
@pytest.mark.parametrize("prior_head", [None, "98", "97"])
async def test_imported_membership_is_not_a_live_sync_boundary(
    paths, config, auth_bundle, collection, prior_head
) -> None:
    _save_query_ids(paths)
    storage_type = {"likes": "like", "tweets": "tweet"}[collection]
    operation = {"likes": "Likes", "tweets": "UserTweets"}[collection]
    response_for = {"likes": make_likes_response, "tweets": make_user_tweets_response}[collection]
    store = open_archive_store(paths, create=True)
    imported = TimelineTweet(
        tweet_id="100",
        text="imported",
        author_id="42",
        author_username="owner",
        author_display_name="Owner",
        created_at="date",
        sort_index="-1",
        raw_json=make_tweet_result("100", "imported"),
    )
    store.merge_rows([store._tweet_record(imported, storage_type, source="x_archive")])
    store.persist_tweet_detail(tweet=imported, raw_json={"enriched": True})
    assert store.get_collection_tweet_ids(storage_type) == {"100"}
    if prior_head:
        store.set_sync_state(storage_type, last_head_tweet_id=prior_head)
    store.close()
    pages = []

    async def handler(request):
        _, variables = _op_and_variables(request)
        cursor = variables.get("cursor")
        if variables["count"] == 1:
            return httpx.Response(200, json=response_for(["100"]), request=request)
        pages.append(cursor)
        if cursor is None:
            payload = response_for(["101", "100"], cursor="c1")
        elif cursor == "c1":
            payload = response_for(["99"], cursor="c2")
        else:
            assert cursor == "c2"
            payload = response_for(["98"])
        return httpx.Response(200, json=payload, request=request)

    result = await sync_collection(
        collection,
        full=False,
        limit=None,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        query_ids={operation: "query"},
        transport=httpx.MockTransport(handler),
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
    )
    assert result.pages_fetched == 3
    assert pages == [None, "c1", "c2"]
    store = open_archive_store(paths, create=False)
    try:
        assert store.get_collection_tweet_ids(storage_type) == {"101", "100", "99", "98"}
    finally:
        store.close()


@pytest.mark.asyncio
async def test_duplicate_detection_stops_sync(paths, config: AppConfig, auth_bundle) -> None:
    """After a full sync, a normal re-sync should stop on the first page (all dupes)."""
    _save_query_ids(paths)
    store = open_archive_store(paths, create=True)
    assert store is not None
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out="c1",
        http_status=200,
        raw_json={"seed": True},
        tweets=[
            TimelineTweet(
                tweet_id="1",
                text="already stored",
                author_id="a1",
                author_username="user1",
                author_display_name="User 1",
                created_at="date",
                sort_index="10",
                raw_json={"tweet": "1"},
            ),
            TimelineTweet(
                tweet_id="2",
                text="also stored",
                author_id="a1",
                author_username="user1",
                author_display_name="User 1",
                created_at="date",
                sort_index="9",
                raw_json={"tweet": "2"},
            ),
        ],
        last_head_tweet_id="1",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.close()

    pages_requested: list[str | None] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        operation, variables = _op_and_variables(request)
        cursor = variables.get("cursor")
        count = int(variables["count"])
        if count == 1:
            return httpx.Response(200, json=make_bookmarks_response(["1"]), request=request)
        pages_requested.append(cursor if isinstance(cursor, str) else None)
        if cursor is None:
            return httpx.Response(
                200, json=make_bookmarks_response(["1", "2"], cursor="c1"), request=request
            )
        raise AssertionError(f"should have stopped before cursor {cursor}")

    result = await sync_collection(
        "bookmarks",
        full=False,
        limit=None,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        query_ids={"Bookmarks": "qid-bookmarks"},
        transport=httpx.MockTransport(handler),
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
    )
    assert result.stop_reason == "duplicate"
    assert result.pages_fetched == 1
    assert pages_requested == [None]


@pytest.mark.asyncio
async def test_duplicate_detection_stops_user_tweets_sync(
    paths, config: AppConfig, auth_bundle
) -> None:
    _save_query_ids(paths)
    store = open_archive_store(paths, create=True)
    assert store is not None
    store.persist_page(
        operation="UserTweets",
        collection_type="tweet",
        cursor_in=None,
        cursor_out="c1",
        http_status=200,
        raw_json={"seed": True},
        tweets=[
            TimelineTweet(
                tweet_id="10",
                text="already stored",
                author_id="a1",
                author_username="user1",
                author_display_name="User 1",
                created_at="date",
                sort_index="10",
                raw_json={"tweet": "10"},
            ),
            TimelineTweet(
                tweet_id="11",
                text="also stored",
                author_id="a1",
                author_username="user1",
                author_display_name="User 1",
                created_at="date",
                sort_index="9",
                raw_json={"tweet": "11"},
            ),
        ],
        last_head_tweet_id="10",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.close()

    pages_requested: list[str | None] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        operation, variables = _op_and_variables(request)
        cursor = variables.get("cursor")
        count = int(variables["count"])
        if count == 1:
            return httpx.Response(200, json=make_user_tweets_response(["10"]), request=request)
        pages_requested.append(cursor if isinstance(cursor, str) else None)
        if cursor is None:
            return httpx.Response(
                200,
                json=make_user_tweets_response(["10", "11"], cursor="c1"),
                request=request,
            )
        raise AssertionError(f"should have stopped before cursor {cursor} for {operation}")

    result = await sync_collection(
        "tweets",
        full=False,
        limit=None,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        query_ids={"UserTweets": "qid-user-tweets"},
        transport=httpx.MockTransport(handler),
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
    )
    assert result.stop_reason == "duplicate"
    assert result.pages_fetched == 1
    assert pages_requested == [None]


@pytest.mark.asyncio
async def test_backfill_flag_skips_duplicate_stop(paths, config: AppConfig, auth_bundle) -> None:
    """--backfill should continue past duplicates without resetting state."""
    _save_query_ids(paths)
    store = open_archive_store(paths, create=True)
    assert store is not None
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"seed": True},
        tweets=[
            TimelineTweet(
                tweet_id="1",
                text="existing",
                author_id="a1",
                author_username="user1",
                author_display_name="User 1",
                created_at="date",
                sort_index="10",
                raw_json={"tweet": "1"},
            ),
        ],
        last_head_tweet_id="1",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.close()

    async def handler(request: httpx.Request) -> httpx.Response:
        operation, variables = _op_and_variables(request)
        cursor = variables.get("cursor")
        count = int(variables["count"])
        if count == 1:
            return httpx.Response(200, json=make_bookmarks_response(["1"]), request=request)
        if cursor is None:
            return httpx.Response(
                200, json=make_bookmarks_response(["1"], cursor="c1"), request=request
            )
        if cursor == "c1":
            return httpx.Response(200, json=make_bookmarks_response(["2"]), request=request)
        raise AssertionError(f"unexpected cursor {cursor}")

    result = await sync_collection(
        "bookmarks",
        full=False,
        backfill=True,
        limit=None,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        query_ids={"Bookmarks": "qid-bookmarks"},
        transport=httpx.MockTransport(handler),
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
    )
    assert result.pages_fetched == 2
    assert result.stop_reason != "duplicate"


@pytest.mark.asyncio
async def test_article_backfill_refreshes_older_duplicate_pages(
    paths, config: AppConfig, auth_bundle
) -> None:
    _save_query_ids(paths)
    store = open_archive_store(paths, create=True)
    assert store is not None
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"seed": True},
        tweets=[
            TimelineTweet(
                tweet_id="1",
                text="existing one",
                author_id="a1",
                author_username="user1",
                author_display_name="User 1",
                created_at="date",
                sort_index="10",
                raw_json=make_tweet_result("1", "existing one", user_id="101"),
            ),
            TimelineTweet(
                tweet_id="2",
                text="existing two",
                author_id="a2",
                author_username="user2",
                author_display_name="User 2",
                created_at="date",
                sort_index="9",
                raw_json=make_tweet_result("2", "existing two", user_id="102"),
            ),
        ],
        last_head_tweet_id="1",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.close()

    article_tweet = make_tweet_result(
        "2",
        "existing two",
        user_id="102",
        article=make_article_result(
            "article-2",
            title="Older article",
            preview_text="Older article preview",
            plain_text="Older article body",
            url="https://x.com/i/article/2",
        ),
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        operation, variables = _op_and_variables(request)
        cursor = variables.get("cursor")
        count = int(variables["count"])
        if count == 1:
            return httpx.Response(
                200,
                json=_bookmarks_response_from_results([make_tweet_result("1", "existing one")]),
                request=request,
            )
        if operation == "Bookmarks" and cursor is None:
            return httpx.Response(
                200,
                json=_bookmarks_response_from_results(
                    [make_tweet_result("1", "existing one")],
                    cursor="c1",
                ),
                request=request,
            )
        if cursor == "c1":
            return httpx.Response(
                200,
                json=_bookmarks_response_from_results([article_tweet]),
                request=request,
            )
        raise AssertionError(f"unexpected cursor {cursor}")

    result = await sync_collection(
        "bookmarks",
        full=False,
        article_backfill=True,
        limit=None,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        query_ids={"Bookmarks": "qid-bookmarks"},
        transport=httpx.MockTransport(handler),
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
    )

    assert result.pages_fetched == 2
    assert result.stop_reason != "duplicate"

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        article_rows = store._query(expr="record_type = 'article'")
        assert len(article_rows) == 1
        assert article_rows[0]["tweet_id"] == "2"
        assert article_rows[0]["title"] == "Older article"
    finally:
        store.close()
