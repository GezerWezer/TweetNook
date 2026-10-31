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
from tests.threads.conftest import _seed_thread_archive
from tweetnook.core.exceptions import (
    AuthExpiredError,
    RepeatedFocalAbsenceError,
)
from tweetnook.enrichment.resurrection import resurrect_due_tweets
from tweetnook.enrichment.threads import expand_threads, normalize_thread_target
from tweetnook.jobs.pipeline import PipelineReporter
from tweetnook.query_ids import QueryIdStore
from tweetnook.storage import open_archive_store


async def _no_sleep(_seconds: float) -> None:
    return None


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
    assert list_raw_capture_target_ids(store, "ThreadExpandDetail") == []
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
async def test_a_repeated_target_that_failed_is_reported_as_skipped_not_known(
    paths, config, auth_bundle
) -> None:
    _seed_thread_archive(paths)
    QueryIdStore(paths).save({"TweetDetail": "detail-qid"})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, request=request)

    reporter = PipelineReporter(
        Console(file=StringIO(), force_terminal=False, color_system=None),
        "threads",
        interactive=False,
    )
    with reporter:
        result = await expand_threads(
            targets=["100", "100"],
            config=config,
            paths=paths,
            auth_bundle=auth_bundle,
            transport=httpx.MockTransport(handler),
            sleep=_no_sleep,
        )

    assert (result.failed, result.skipped) == (1, 1)
    summary = reporter._step_by_key["threads"].summary
    assert "1 skipped" in summary
    assert "known" not in summary
