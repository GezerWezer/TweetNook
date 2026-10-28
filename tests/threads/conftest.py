"""Helpers and fixtures shared by the threads tests."""

from __future__ import annotations

import json

import httpx

from tests.conftest import (
    make_tweet_detail_response,
    make_tweet_result,
    make_url_entity,
)
from tweetnook.core.timeline_parsing import TimelineTweet
from tweetnook.storage import open_archive_store


def _seed_thread_archive(paths, *, with_quote: bool = False) -> dict[str, object]:
    root_raw = make_tweet_result(
        "100",
        "reply tweet",
        user_id="1000",
        in_reply_to_status_id="200",
        conversation_id="200",
        quoted_tweet=make_tweet_result("400", "quoted tweet") if with_quote else None,
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
    store = open_archive_store(paths, create=True)
    assert store is not None
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
    store.close()
    return root_raw


def _seed_quote_archive(
    paths,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    deepest_raw = make_tweet_result("300", "deepest quoted tweet", user_id="3000")
    quoted_raw = make_tweet_result(
        "200",
        "quoted tweet",
        user_id="2000",
        quoted_tweet=deepest_raw,
    )
    wrapper_raw = make_tweet_result(
        "100",
        "quote wrapper",
        user_id="1000",
        quoted_tweet=quoted_raw,
    )
    wrapper = TimelineTweet(
        tweet_id="100",
        text="quote wrapper",
        author_id="1000",
        author_username="user1000",
        author_display_name="User 1000",
        created_at="Sat Mar 14 00:00:00 +0000 2026",
        sort_index="10",
        raw_json=wrapper_raw,
    )
    store = open_archive_store(paths, create=True)
    assert store is not None
    store.persist_page(
        operation="Likes",
        collection_type="like",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[wrapper],
        last_head_tweet_id="100",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.close()
    return wrapper_raw, quoted_raw, deepest_raw


def _status_url(tweet_id: str) -> dict[str, object]:
    return make_url_entity(
        f"https://t.co/s{tweet_id}",
        f"https://x.com/example/status/{tweet_id}",
        display_url=f"x.com/example/status/{tweet_id}",
    )


def _detail_handler(responses: dict[str, list[dict[str, object]]], requests: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        focal = json.loads(request.url.params["variables"])["focalTweetId"]
        if focal not in responses:
            raise AssertionError(f"unexpected request for {focal}")
        requests.append(focal)
        return httpx.Response(
            200,
            json=make_tweet_detail_response(responses[focal], module=True),
            request=request,
        )

    return handler
