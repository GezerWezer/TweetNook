from __future__ import annotations

from io import StringIO

import httpx
import pytest
from rich.console import Console

from tests.conftest import (
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
from tweetnook.jobs.pipeline import PipelineReporter
from tweetnook.query_ids import QueryIdStore
from tweetnook.storage import open_archive_store


@pytest.mark.asyncio
async def test_expand_threads_follows_other_replies_one_hop_only_when_enabled(
    paths,
    config,
    auth_bundle,
) -> None:
    root_raw = make_tweet_result("100", "saved root", user_id="1000")
    self_reply = make_tweet_result(
        "101",
        "self thread",
        user_id="1000",
        conversation_id="100",
        in_reply_to_status_id="100",
        urls=[_status_url("500")],
    )
    other_reply = make_tweet_result(
        "102",
        "other reply",
        user_id="2000",
        conversation_id="100",
        in_reply_to_status_id="100",
        urls=[_status_url("700")],
    )
    reply_target = make_tweet_result(
        "700", "reply target", user_id="3000", urls=[_status_url("800")]
    )
    reply_target_reply = make_tweet_result(
        "701",
        "reply to reply target",
        user_id="4000",
        conversation_id="700",
        in_reply_to_status_id="700",
        urls=[_status_url("900")],
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
        tweets=[
            TimelineTweet(
                tweet_id="100",
                text="saved root",
                author_id="1000",
                author_username="user1000",
                author_display_name="User 1000",
                created_at="Sat Mar 14 00:00:00 +0000 2026",
                sort_index="10",
                raw_json=root_raw,
            )
        ],
        last_head_tweet_id="100",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.close()
    QueryIdStore(paths).save({"TweetDetail": "detail-qid"})
    requests: list[str] = []
    handler = httpx.MockTransport(
        _detail_handler(
            {
                "100": [root_raw, self_reply, other_reply],
                "500": [make_tweet_result("500", "self-thread target", user_id="5000")],
                "700": [reply_target, reply_target_reply],
            },
            requests,
        )
    )

    # The first pass archives the saved conversation; the next default pass follows the
    # saved author's own reply but not the other user's reply.
    await expand_threads(config=config, paths=paths, auth_bundle=auth_bundle, transport=handler)
    assert requests == ["100"]
    store = open_archive_store(paths, create=False)
    assert store is not None
    assert store.list_saved_conversation_tweet_ids() == ({"100", "101"}, {"102"})
    store.close()
    output = StringIO()
    await expand_threads(
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=handler,
        console=Console(file=output, force_terminal=False, color_system=None, width=400),
    )
    assert requests == ["100", "500"]
    assert "1 self-thread seeds; 1 replies not followed" in output.getvalue()

    reply_hop = config.model_copy(
        update={"sync": config.sync.model_copy(update={"reply_linked_depth": 1})}
    )
    output = StringIO()
    third = await expand_threads(
        config=reply_hop,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=handler,
        console=Console(file=output, force_terminal=False, color_system=None, width=400),
    )
    assert third.expanded == 1
    assert requests == ["100", "500", "700"]
    assert "reply hop from 1 replies reached 1 new" in output.getvalue()

    # 700 was reached only through the reply hop: neither its own link (800) nor its
    # replies' links (900 via 701) are followed further.
    fourth = await expand_threads(
        config=reply_hop, paths=paths, auth_bundle=auth_bundle, transport=handler
    )
    assert fourth.processed == 0
    assert requests == ["100", "500", "700"]


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
        "tweetnook.enrichment.threads.resolve_query_ids",
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
