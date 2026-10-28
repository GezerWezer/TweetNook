from __future__ import annotations

import json
from io import StringIO

import httpx
import pytest
from rich.console import Console

from tests.conftest import (
    list_raw_capture_target_ids,
    make_tweet_detail_response,
    make_tweet_result,
)
from tests.threads.conftest import (
    _detail_handler,
    _seed_quote_archive,
    _seed_thread_archive,
    _status_url,
)
from tweetnook.core.timeline_parsing import TimelineTweet
from tweetnook.enrichment.threads import expand_threads
from tweetnook.query_ids import QueryIdStore
from tweetnook.storage import ArchiveStore, open_archive_store


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
        assert list_raw_capture_target_ids(store, "ThreadExpandDetail") == ["100", "300"]
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
async def test_skip_quotes_keeps_threads_and_linked_expansion_and_can_be_reenabled(
    paths, config, auth_bundle
) -> None:
    root = _seed_thread_archive(paths, with_quote=True)
    QueryIdStore(paths).save({"TweetDetail": "detail-qid"})
    requested = []
    output = StringIO()

    def handler(request):
        target = json.loads(request.url.params["variables"])["focalTweetId"]
        requested.append(target)
        payload = root if target == "100" else make_tweet_result(target, "context")
        return httpx.Response(200, json=make_tweet_detail_response([payload]), request=request)

    result = await expand_threads(
        skip_quotes=True,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(handler),
        console=Console(file=output, force_terminal=False),
    )
    assert requested == ["100", "300"]
    assert result.expanded == 2
    assert "quoted tweet pass skipped by replay sync setting (1 candidates)" in output.getvalue()
    requested.clear()
    result = await expand_threads(
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(handler),
    )
    assert requested == ["400"]
    assert result.expanded == 1


@pytest.mark.asyncio
async def test_expand_threads_refetches_context_after_a_later_resurrection(
    paths,
    config,
    auth_bundle,
) -> None:
    root_raw = _seed_thread_archive(paths)
    parent_raw = make_tweet_result("200", "parent tweet", user_id="2000")
    store = open_archive_store(paths, create=False)
    assert store is not None
    store._merge_records(
        [
            store._record(
                row_key="raw_capture:stale-thread-100",
                record_type="raw_capture",
                operation="ThreadExpandDetail",
                cursor_in="100",
                captured_at="2026-08-01T00:00:00+00:00",
            )
        ]
    )
    store.update_tweet_object_enrichment(
        "100",
        enrichment_state="resurrected",
        enrichment_checked_at="2026-08-02T00:00:00+00:00",
        enrichment_http_status=200,
        enrichment_reason=None,
    )
    assert store.list_expanded_thread_target_ids() == []
    store.close()
    QueryIdStore(paths).save({"TweetDetail": "detail-qid"})
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.params["variables"])
        return httpx.Response(
            200,
            json=make_tweet_detail_response([root_raw, parent_raw], module=True),
            request=request,
        )

    result = await expand_threads(
        targets=["100"],
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(handler),
    )

    assert result.processed == 1
    assert result.expanded == 1
    assert len(requests) == 1
    store = open_archive_store(paths, create=False)
    assert store is not None
    assert store.list_expanded_thread_target_ids() == ["100"]
    assert store._get_row("tweet_object:200")["text"] == "parent tweet"
    store.close()


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
        assert list_raw_capture_target_ids(store, "ThreadExpandDetail") == ["100", "200", "300"]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_expand_threads_default_depth_keeps_following_quote_chain(
    paths,
    config,
    auth_bundle,
) -> None:
    assert config.sync.max_linked_depth == 50
    wrapper_raw, quoted_raw, deepest_raw = _seed_quote_archive(paths)
    QueryIdStore(paths).save({"TweetDetail": "detail-qid"})
    requests: list[str] = []
    handler = _detail_handler(
        {"100": [wrapper_raw], "200": [quoted_raw], "300": [deepest_raw]}, requests
    )

    # Nested quotes surface as relations only once each quoted tweet is fetched, so the
    # chain grows a hop per run; the default depth keeps following it without a cap of 1.
    for _ in range(3):
        await expand_threads(
            config=config,
            paths=paths,
            auth_bundle=auth_bundle,
            transport=httpx.MockTransport(handler),
        )

    assert requests == ["100", "200", "300"]


@pytest.mark.asyncio
async def test_expand_threads_seeds_self_thread_without_fetching_it(
    paths,
    config,
    auth_bundle,
    monkeypatch,
) -> None:
    # The saved tweet quotes its author's own reply in the same conversation; that
    # self-thread tweet is a depth-0 seed, so its link is followed but it is never
    # fetched as a quote target itself.
    self_reply = make_tweet_result(
        "101",
        "self thread",
        user_id="1000",
        conversation_id="100",
        in_reply_to_status_id="100",
        urls=[_status_url("500")],
    )
    saved_raw = make_tweet_result("100", "saved", user_id="1000", quoted_tweet=self_reply)
    store = open_archive_store(paths, create=True)
    assert store is not None
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[
            TimelineTweet(
                tweet_id="100",
                text="saved",
                author_id="1000",
                author_username="user1000",
                author_display_name="User 1000",
                created_at="Sat Mar 14 00:00:00 +0000 2026",
                sort_index="10",
                raw_json=saved_raw,
            )
        ],
        last_head_tweet_id="100",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    assert store.list_saved_conversation_tweet_ids() == ({"100", "101"}, set())
    store.close()
    QueryIdStore(paths).save({"TweetDetail": "detail-qid"})
    quote_lookups: list[set[str]] = []
    original_lookup = ArchiveStore.list_quote_relation_rows

    def counting_lookup(self, source_tweet_ids):
        quote_lookups.append(set(source_tweet_ids))
        return original_lookup(self, source_tweet_ids)

    monkeypatch.setattr(ArchiveStore, "list_quote_relation_rows", counting_lookup)
    requests: list[str] = []
    output = StringIO()
    handler = _detail_handler(
        {
            "100": [saved_raw],
            "500": [make_tweet_result("500", "linked", user_id="5000")],
        },
        requests,
    )

    result = await expand_threads(
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(handler),
        console=Console(file=output, force_terminal=False, color_system=None, width=400),
    )

    assert requests == ["100", "500"]
    assert result.expanded == 2
    # Depth 1 from the seeds, depth 2 from 500, then the empty frontier stops the loop.
    assert quote_lookups == [{"100", "101"}, {"500"}]
    assert "max depth 50; 1 self-thread seeds; 0 replies not followed" in output.getvalue()
