"""Resurrection stops after repeated TweetDetail responses that omit the requested post.

Thread expansion and archive enrichment have their own copies of this breaker and their
own tests; resurrection keeps an inline counter, so it needs one too.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

import tweetnook.enrichment.resurrection as resurrection
from tests.conftest import make_tweet_detail_response, make_tweet_result, request_details
from tweetnook.core.exceptions import RepeatedFocalAbsenceError
from tweetnook.query_ids import QueryIdStore
from tweetnook.storage import open_archive_store

CHECKED_AT = "2025-01-01T00:00:00+00:00"
DUE_AT = "2026-01-01T00:00:00+00:00"
IDS = ("1", "2", "3", "4", "5")


def no_sleep(_delay: float):
    return SimpleNamespace(__await__=lambda self: iter(()))


def seed_due_rows(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    for tweet_id in IDS:
        store.persist_unavailable_tweet(
            tweet_id=tweet_id,
            operation="TweetDetail",
            raw_json={"seed": tweet_id},
            http_status=200,
            reason="unavailable_unknown",
            detail="original tombstone",
            retry_eligible=True,
            next_retry_at=DUE_AT,
            retry_count=0,
        )
        store.update_tweet_object_enrichment(
            tweet_id,
            enrichment_state="terminal_unavailable",
            enrichment_checked_at=CHECKED_AT,
            enrichment_http_status=200,
            enrichment_reason="unavailable_unknown",
            enrichment_detail="original tombstone",
            enrichment_retry_count=0,
            enrichment_next_retry_at=DUE_AT,
            enrichment_retry_eligible=True,
        )
    store.close()
    QueryIdStore(paths).save({"TweetDetail": "qid"})


def responder(kinds: list[str], requested: list[str]):
    """Answer the nth request with the nth kind: available, absent or unavailable."""

    def handler(request: httpx.Request) -> httpx.Response:
        _operation, variables = request_details(str(request.url))
        tweet_id = str(variables["focalTweetId"])
        kind = kinds[len(requested)]
        requested.append(tweet_id)
        if kind == "available":
            payload = make_tweet_detail_response(
                [make_tweet_result(tweet_id, "back", user_id="42")]
            )
        elif kind == "absent":
            payload = make_tweet_detail_response([make_tweet_result("999", "unrelated")])
        else:
            payload = make_tweet_detail_response(
                [{"__typename": "TweetUnavailable", "rest_id": tweet_id, "reason": "Gone."}]
            )
        return httpx.Response(200, json=payload, request=request)

    return handler


def saved_rows(paths) -> dict[str, dict]:
    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        return {
            tweet_id: dict(
                store.conn.execute(
                    "SELECT enrichment_state, enrichment_detail, enrichment_checked_at,"
                    " enrichment_retry_count, enrichment_next_retry_at"
                    " FROM archive WHERE row_key = ?",
                    (f"tweet_object:{tweet_id}",),
                ).fetchone()
            )
            for tweet_id in IDS
        }
    finally:
        store.close()


async def test_three_absences_stop_the_pass_and_keep_earlier_results(
    paths, config, auth_bundle
) -> None:
    seed_due_rows(paths)
    requested: list[str] = []
    handler = responder(["available", "absent", "absent", "absent", "available"], requested)

    with pytest.raises(RepeatedFocalAbsenceError, match="3 consecutive responses"):
        await resurrection.resurrect_due_tweets(
            budget=len(IDS),
            config=config,
            paths=paths,
            auth_bundle=auth_bundle,
            transport=httpx.MockTransport(handler),
            sleep=no_sleep,
        )

    assert len(requested) == 4
    rows = saved_rows(paths)
    returned, *absent = requested
    assert rows[returned]["enrichment_state"] == "resurrected"
    for tweet_id in absent:
        row = rows[tweet_id]
        assert row["enrichment_state"] == "terminal_unavailable"
        assert row["enrichment_detail"].startswith("original tombstone\nRetry diagnostic: ")
        assert row["enrichment_retry_count"] == 0
        assert row["enrichment_checked_at"] != CHECKED_AT
    (untouched,) = set(IDS) - set(requested)
    assert rows[untouched] == {
        "enrichment_state": "terminal_unavailable",
        "enrichment_detail": "original tombstone",
        "enrichment_checked_at": CHECKED_AT,
        "enrichment_retry_count": 0,
        "enrichment_next_retry_at": DUE_AT,
    }


@pytest.mark.parametrize("reset", ["available", "unavailable"])
async def test_a_focal_result_between_absences_resets_the_count(
    paths, config, auth_bundle, reset: str
) -> None:
    seed_due_rows(paths)
    requested: list[str] = []
    handler = responder(["absent", "absent", reset, "absent", "absent"], requested)

    result = await resurrection.resurrect_due_tweets(
        budget=len(IDS),
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(handler),
        sleep=no_sleep,
    )

    assert sorted(requested) == sorted(IDS)
    assert result.attempted == 5
    assert result.transient_failures == 4
