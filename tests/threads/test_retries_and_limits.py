from __future__ import annotations

from io import StringIO

import httpx
import pytest
from rich.console import Console

from tests.conftest import (
    list_raw_capture_target_ids,
    make_tweet_detail_response,
    make_tweet_result,
    make_url_entity,
)
from tests.threads.conftest import _seed_thread_archive
from tweetnook.core.exceptions import (
    RateLimitExhaustedError,
)
from tweetnook.core.timeline_parsing import TimelineTweet
from tweetnook.enrichment.threads import expand_threads
from tweetnook.query_ids import QueryIdStore
from tweetnook.storage import open_archive_store


@pytest.mark.asyncio
async def test_expand_threads_retries_failed_linked_status_once_per_run(
    paths,
    config,
    auth_bundle,
) -> None:
    link_url = make_url_entity(
        "https://t.co/shared",
        "https://x.com/example/status/300?s=20",
        display_url="x.com/example/status/300",
    )
    first_raw = make_tweet_result("100", "first root", user_id="1000", urls=[link_url])
    second_raw = make_tweet_result("101", "second root", user_id="1001", urls=[link_url])
    first_tweet = TimelineTweet(
        tweet_id="100",
        text="first root",
        author_id="1000",
        author_username="user1000",
        author_display_name="User 1000",
        created_at="Sat Mar 14 00:00:00 +0000 2026",
        sort_index="10",
        raw_json=first_raw,
    )
    second_tweet = TimelineTweet(
        tweet_id="101",
        text="second root",
        author_id="1001",
        author_username="user1001",
        author_display_name="User 1001",
        created_at="Sat Mar 14 00:00:00 +0000 2026",
        sort_index="9",
        raw_json=second_raw,
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
        tweets=[first_tweet, second_tweet],
        last_head_tweet_id="100",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.close()
    QueryIdStore(paths).save({"TweetDetail": "detail-qid"})
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        focal = request.url.params["variables"]
        if '"focalTweetId":"100"' in focal:
            requests.append("100")
            return httpx.Response(
                200,
                json=make_tweet_detail_response([first_raw]),
                request=request,
            )
        if '"focalTweetId":"101"' in focal:
            requests.append("101")
            return httpx.Response(
                200,
                json=make_tweet_detail_response([second_raw]),
                request=request,
            )
        if '"focalTweetId":"300"' in focal:
            requests.append("300")
            return httpx.Response(500, request=request)
        raise AssertionError(f"unexpected request {request.url}")

    result = await expand_threads(
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(handler),
    )

    assert result.processed == 3
    assert result.expanded == 2
    assert result.failed == 1
    assert result.skipped == 1
    assert requests == ["100", "101", "300"]


@pytest.mark.asyncio
async def test_expand_threads_respects_limit_before_linked_status_pass(
    paths,
    config,
    auth_bundle,
) -> None:
    root_raw = _seed_thread_archive(paths)
    parent_raw = make_tweet_result("200", "parent tweet", user_id="2000")
    QueryIdStore(paths).save({"TweetDetail": "detail-qid"})
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        focal = request.url.params["variables"]
        if '"focalTweetId":"100"' in focal:
            requests.append("100")
            return httpx.Response(
                200,
                json=make_tweet_detail_response([root_raw, parent_raw], module=True),
                request=request,
            )
        raise AssertionError(f"unexpected request {request.url}")

    result = await expand_threads(
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        limit=1,
        transport=httpx.MockTransport(handler),
    )

    assert result.processed == 1
    assert result.expanded == 1
    assert result.failed == 0
    assert result.skipped == 0
    assert requests == ["100"]

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        assert list_raw_capture_target_ids(store, "ThreadExpandDetail") == ["100"]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_expand_threads_logs_rate_limit_progress(
    paths,
    config,
    auth_bundle,
) -> None:
    _seed_thread_archive(paths)
    QueryIdStore(paths).save({"TweetDetail": "detail-qid"})
    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None)
    limited_config = config.model_copy(
        update={
            "sync": config.sync.model_copy(
                update={
                    "max_retries": 1,
                    "backoff_base": 0.1,
                    "detail_max_retries": 1,
                    "detail_backoff_base": 0.1,
                    "cooldown_threshold": 1,
                    "cooldown_duration": 0.0,
                }
            )
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, request=request)

    with pytest.raises(RateLimitExhaustedError):
        await expand_threads(
            targets=["100"],
            config=limited_config,
            paths=paths,
            auth_bundle=auth_bundle,
            transport=httpx.MockTransport(handler),
            console=console,
        )
    text = output.getvalue()
    assert "threads: preparing archive expansion job" in text
    assert "threads: resolving TweetDetail query ID" in text
    assert "threads: loading archived thread expansion state..." in text
    assert "threads: explicit target pass over 1 targets" in text
    assert "thread 100: rate limited (HTTP 429), retry 1/1 in 0.1s" in text
    assert "thread 100: rate limited repeatedly, cooling down for 0.0s" in text
    assert "thread 100: failed (Rate limit persisted after retries and cooldown.)" not in text
