from __future__ import annotations

import json
from io import StringIO

import httpx
import pytest
from rich.console import Console

from tests.conftest import make_tweet_detail_response, make_tweet_result, make_url_entity
from tweetnook.client.timelines import TimelineTweet
from tweetnook.exceptions import (
    AuthExpiredError,
    RateLimitExhaustedError,
    RepeatedFocalAbsenceError,
)
from tweetnook.pipeline import PipelineReporter
from tweetnook.query_ids import QueryIdStore
from tweetnook.resurrection import resurrect_due_tweets
from tweetnook.storage import open_archive_store
from tweetnook.threads import expand_threads, normalize_thread_target


def _seed_thread_archive(paths) -> dict[str, object]:
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


def test_normalize_thread_target_accepts_ids_and_urls() -> None:
    assert normalize_thread_target("2026531440414925307") == "2026531440414925307"
    assert (
        normalize_thread_target("https://x.com/dimitrispapail/status/2026531440414925307")
        == "2026531440414925307"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "expected_reason", "retry_eligible"),
    [
        (
            "You're unable to view this Post because this account owner limits who can view "
            "their Posts.",
            "protected_account",
            1,
        ),
        ("This Post was deleted by the Post author.", "deleted_by_author", 0),
    ],
)
async def test_thread_unavailable_result_is_preserved_and_not_resurrected_same_sync(
    paths,
    config,
    auth_bundle,
    message: str,
    expected_reason: str,
    retry_eligible: int,
) -> None:
    _seed_thread_archive(paths)
    QueryIdStore(paths).save({"TweetDetail": "detail-qid"})
    requests: list[str] = []
    tombstone = {
        "__typename": "TweetTombstone",
        "tombstone": {"text": {"text": message}, "entities": [{"type": "Hashtag"}]},
    }
    payload = {
        "data": {
            "threaded_conversation_with_injections_v2": {
                "instructions": [
                    {
                        "entries": [
                            {
                                "entryId": "tweet-100",
                                "content": {
                                    "itemContent": {"tweet_results": {"result": tombstone}}
                                },
                            }
                        ]
                    }
                ]
            }
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.params["variables"])
        return httpx.Response(200, json=payload, request=request)

    expanded = await expand_threads(
        targets=["100"],
        limit=1,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(handler),
    )

    assert expanded.processed == 1
    assert expanded.failed == 1
    assert len(requests) == 1
    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        row = store._get_row("tweet_object:100")
        assert row is not None
        assert row["text"] == "reply tweet"
        assert row["author_id"] == "1000"
        assert row["enrichment_reason"] == expected_reason
        assert row["enrichment_detail"] == message
        assert row["enrichment_retry_eligible"] == retry_eligible
        assert (row["enrichment_next_retry_at"] is not None) is bool(retry_eligible)
        captures = store._query(
            expr="record_type = 'raw_capture' AND operation = 'ThreadExpandDetail'"
        )
        assert len(captures) == 1
        assert json.loads(captures[0]["raw_json"]) == payload
        assert store.count_due_resurrection_tweets() == 0
    finally:
        store.close()

    resurrected = await resurrect_due_tweets(
        budget=200,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(
            lambda request: pytest.fail(f"unexpected same-sync resurrection: {request.url}")
        ),
    )

    assert resurrected.attempted == 0
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_thread_focal_absence_does_not_create_terminal_state(
    paths, config, auth_bundle
) -> None:
    _seed_thread_archive(paths)
    QueryIdStore(paths).save({"TweetDetail": "detail-qid"})
    payload = make_tweet_detail_response([make_tweet_result("999", "unrelated")])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    result = await expand_threads(
        targets=["100"],
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(handler),
    )

    assert result.processed == 1
    assert result.failed == 1
    store = open_archive_store(paths, create=False)
    assert store is not None
    row = store._get_row("tweet_object:100")
    assert row["enrichment_state"] == "done"
    assert row["enrichment_reason"] is None
    assert store.list_raw_capture_target_ids("ThreadExpandDetail") == []
    store.close()


@pytest.mark.asyncio
async def test_thread_focal_absence_circuit_breaker_stops_remaining_targets(
    paths, config, auth_bundle
) -> None:
    _seed_thread_archive(paths)
    QueryIdStore(paths).save({"TweetDetail": "detail-qid"})
    attempts = 0
    payload = make_tweet_detail_response([make_tweet_result("999", "unrelated")])

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, json=payload, request=request)

    with pytest.raises(RepeatedFocalAbsenceError):
        await expand_threads(
            targets=["100", "101", "102", "103"],
            config=config,
            paths=paths,
            auth_bundle=auth_bundle,
            transport=httpx.MockTransport(handler),
        )

    assert attempts == 3


@pytest.mark.asyncio
async def test_thread_auth_failure_aborts_before_remaining_targets(
    paths, config, auth_bundle
) -> None:
    _seed_thread_archive(paths)
    QueryIdStore(paths).save({"TweetDetail": "detail-qid"})
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(401, request=request)

    with pytest.raises(AuthExpiredError):
        await expand_threads(
            targets=["100", "101"],
            config=config,
            paths=paths,
            auth_bundle=auth_bundle,
            transport=httpx.MockTransport(handler),
        )

    assert attempts == 1


@pytest.mark.asyncio
async def test_expand_threads_fetches_membership_and_linked_status(
    paths,
    config,
    auth_bundle,
) -> None:
    root_raw = _seed_thread_archive(paths)
    parent_raw = make_tweet_result("200", "parent tweet", user_id="2000")
    linked_raw = make_tweet_result("300", "linked tweet", user_id="3000")
    QueryIdStore(paths).save({"TweetDetail": "detail-qid"})
    requests: list[str] = []
    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None)

    def handler(request: httpx.Request) -> httpx.Response:
        focal = request.url.params["variables"]
        if '"focalTweetId":"100"' in focal:
            requests.append("100")
            return httpx.Response(
                200,
                json=make_tweet_detail_response([root_raw, parent_raw], module=True),
                request=request,
            )
        if '"focalTweetId":"300"' in focal:
            requests.append("300")
            return httpx.Response(
                200,
                json=make_tweet_detail_response([linked_raw]),
                request=request,
            )
        raise AssertionError(f"unexpected request {request.url}")

    result = await expand_threads(
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(handler),
        console=console,
    )

    assert result.processed == 2
    assert result.expanded == 2
    assert result.failed == 0
    assert requests == ["100", "300"]
    text = output.getvalue()
    assert "threads: preparing archive expansion job" in text
    assert "threads: loading archived thread expansion state..." in text
    assert "threads: loading archived membership tweets..." in text
    assert "threads: loading known tweet ids for linked-status pass..." in text
    assert "threads: loading archived url refs..." in text

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        membership_rows = store._query(expr="record_type = 'tweet'")
        assert [row["tweet_id"] for row in membership_rows] == ["100"]

        tweet_object_rows = store._query(expr="record_type = 'tweet_object'")
        assert {row["tweet_id"] for row in tweet_object_rows} == {"100", "200", "300"}

        relation_rows = store._query(expr="record_type = 'tweet_relation'")
        relations = {
            (row["tweet_id"], row["relation_type"], row["target_tweet_id"]) for row in relation_rows
        }
        assert ("100", "reply_to", "200") in relations
        assert ("100", "thread_parent", "200") in relations
        assert ("200", "thread_child", "100") in relations
        assert ("100", "links_to_status", "300") in relations
        assert store.list_raw_capture_target_ids("ThreadExpandDetail") == ["100", "300"]
    finally:
        store.close()

    def unexpected_request(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"should not refetch thread targets: {request.url}")

    second = await expand_threads(
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(unexpected_request),
    )

    assert second.processed == 0
    assert second.expanded == 0
    assert second.failed == 0
    assert second.skipped >= 1


@pytest.mark.asyncio
async def test_expand_threads_follows_quote_edges_at_configured_depth(
    paths,
    config,
    auth_bundle,
) -> None:
    wrapper_raw, quoted_raw, deepest_raw = _seed_quote_archive(paths)
    wrapper_reply = make_tweet_result(
        "101",
        "wrapper reply",
        user_id="1001",
        conversation_id="100",
        in_reply_to_status_id="100",
    )
    quoted_reply = make_tweet_result(
        "201",
        "quoted reply",
        user_id="2001",
        conversation_id="200",
        in_reply_to_status_id="200",
    )
    deepest_reply = make_tweet_result(
        "301",
        "deepest reply",
        user_id="3001",
        conversation_id="300",
        in_reply_to_status_id="300",
    )
    QueryIdStore(paths).save({"TweetDetail": "detail-qid"})
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        focal = request.url.params["variables"]
        if '"focalTweetId":"100"' in focal:
            requests.append("100")
            results = [wrapper_raw, wrapper_reply]
        elif '"focalTweetId":"200"' in focal:
            requests.append("200")
            results = [quoted_raw, quoted_reply]
        elif '"focalTweetId":"300"' in focal:
            requests.append("300")
            results = [deepest_raw, deepest_reply]
        else:
            raise AssertionError(f"unexpected request {request.url}")
        return httpx.Response(
            200,
            json=make_tweet_detail_response(results, module=True),
            request=request,
        )

    depth_one = config.model_copy(
        update={"sync": config.sync.model_copy(update={"max_linked_depth": 1})}
    )
    first = await expand_threads(
        config=depth_one,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(handler),
    )

    assert first.processed == 2
    assert first.expanded == 2
    assert requests == ["100", "200"]

    second = await expand_threads(
        config=depth_one,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(handler),
    )

    assert second.processed == 0
    assert requests == ["100", "200"]

    depth_two = config.model_copy(
        update={"sync": config.sync.model_copy(update={"max_linked_depth": 2})}
    )
    third = await expand_threads(
        config=depth_two,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(handler),
    )

    assert third.processed == 1
    assert third.expanded == 1
    assert requests == ["100", "200", "300"]

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        relation_rows = store._query(expr="record_type = 'tweet_relation'")
        relations = {
            (row["tweet_id"], row["relation_type"], row["target_tweet_id"]) for row in relation_rows
        }
        assert ("101", "reply_to", "100") in relations
        assert ("201", "reply_to", "200") in relations
        assert ("301", "reply_to", "300") in relations
        assert store.list_raw_capture_target_ids("ThreadExpandDetail") == ["100", "200", "300"]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_expand_threads_deduplicates_quoted_original_with_membership(
    paths,
    config,
    auth_bundle,
) -> None:
    wrapper_raw, quoted_raw, _deepest_raw = _seed_quote_archive(paths)
    quoted_raw.pop("quoted_status_result")
    quoted = TimelineTweet(
        tweet_id="200",
        text="quoted tweet",
        author_id="2000",
        author_username="user2000",
        author_display_name="User 2000",
        created_at="Sat Mar 14 00:00:00 +0000 2026",
        sort_index="9",
        raw_json=quoted_raw,
    )
    store = open_archive_store(paths, create=False)
    assert store is not None
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[quoted],
        last_head_tweet_id="200",
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
            result = wrapper_raw
        elif '"focalTweetId":"200"' in focal:
            requests.append("200")
            result = quoted_raw
        else:
            raise AssertionError(f"unexpected request {request.url}")
        return httpx.Response(
            200,
            json=make_tweet_detail_response([result]),
            request=request,
        )

    result = await expand_threads(
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(handler),
    )

    assert result.processed == 2
    assert result.expanded == 2
    assert requests == ["100", "200"]


@pytest.mark.asyncio
async def test_expand_threads_explicit_targets_preserve_duplicate_and_failure_counts(
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
        if '"focalTweetId":"200"' in focal:
            requests.append("200")
            return httpx.Response(
                200,
                json=make_tweet_detail_response([root_raw]),
                request=request,
            )
        raise AssertionError(f"unexpected request {request.url}")

    result = await expand_threads(
        targets=[
            "https://x.com/example/status/100",
            "100",
            "200",
        ],
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(handler),
    )

    assert result.processed == 2
    assert result.expanded == 1
    assert result.failed == 1
    assert result.skipped == 1
    assert requests == ["100", "200"]


@pytest.mark.asyncio
async def test_expand_threads_explicit_targets_skip_already_expanded_by_default(
    paths,
    config,
    auth_bundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root_raw = _seed_thread_archive(paths)
    parent_raw = make_tweet_result("200", "parent tweet", user_id="2000")
    QueryIdStore(paths).save({"TweetDetail": "detail-qid"})

    def handler(request: httpx.Request) -> httpx.Response:
        focal = request.url.params["variables"]
        if '"focalTweetId":"100"' in focal:
            return httpx.Response(
                200,
                json=make_tweet_detail_response([root_raw, parent_raw], module=True),
                request=request,
            )
        raise AssertionError(f"unexpected request {request.url}")

    first = await expand_threads(
        targets=["100"],
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(handler),
    )

    assert first.processed == 1
    assert first.expanded == 1

    def unexpected_request(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"should not refetch explicit target without --refresh: {request.url}")

    monkeypatch.setattr(
        "tweetnook.threads.resolve_query_ids",
        lambda *_args, **_kwargs: pytest.fail(
            "TweetDetail metadata should not resolve for an empty thread queue"
        ),
    )

    reporter = PipelineReporter(
        Console(file=StringIO(), force_terminal=False, color_system=None),
        "threads",
        interactive=False,
    )
    with reporter:
        second = await expand_threads(
            targets=["100"],
            config=config,
            paths=paths,
            auth_bundle=auth_bundle,
            transport=httpx.MockTransport(unexpected_request),
        )

    assert second.processed == 0
    assert second.expanded == 0
    assert second.failed == 0
    assert second.skipped == 1
    assert reporter._step_by_key["threads"].state == "skipped"
    assert "already being expanded or known" in reporter._step_by_key["threads"].summary


@pytest.mark.asyncio
async def test_expand_threads_explicit_targets_refresh_refetches_expanded_target(
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

    first = await expand_threads(
        targets=["100"],
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(handler),
    )
    assert first.expanded == 1

    second = await expand_threads(
        targets=["100"],
        refresh=True,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(handler),
    )

    assert second.processed == 1
    assert second.expanded == 1
    assert second.skipped == 0
    assert requests == ["100", "100"]


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
        assert store.list_raw_capture_target_ids("ThreadExpandDetail") == ["100"]
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
