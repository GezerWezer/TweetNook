from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from tests.conftest import (
    make_bookmarks_response,
    make_likes_response,
    make_tweet_detail_response,
    make_tweet_result,
    make_user_tweets_response,
    request_details,
)
from tweetnook.client.base import (
    RateLimitExhaustedError,
    is_auth_error,
    is_feature_flag_error,
    is_rate_limit,
    is_stale_query_id,
)
from tweetnook.client.timelines import (
    FocalResultKind,
    _entry_id_targets_tweet,
    build_bookmarks_url,
    build_likes_url,
    build_tweet_detail_url,
    build_user_tweets_url,
    fetch_page,
    parse_timeline_response,
    parse_tweet_detail_response,
    parse_tweet_detail_tweets,
)
from tweetnook.config import SyncConfig


def _detail_payload(entries: list[dict[str, object]]) -> dict[str, object]:
    return {
        "data": {
            "threaded_conversation_with_injections_v2": {
                "instructions": [{"type": "TimelineAddEntries", "entries": entries}]
            }
        }
    }


def _detail_entry(entry_id: str, result: dict[str, object]) -> dict[str, object]:
    return {
        "entryId": entry_id,
        "content": {"itemContent": {"tweet_results": {"result": result}}},
    }


def _empty_detail_entry(entry_id: str) -> dict[str, Any]:
    return {
        "entryId": entry_id,
        "content": {
            "__typename": "TimelineTimelineItem",
            "entryType": "TimelineTimelineItem",
            "itemContent": {
                "__typename": "TimelineTweet",
                "itemType": "TimelineTweet",
                "tweet_results": {},
            },
        },
    }


def test_build_timeline_urls() -> None:
    bookmarks_url = build_bookmarks_url("bookmark-qid", cursor="abc")
    likes_url = build_likes_url("likes-qid", "42", cursor="def")
    tweets_url = build_user_tweets_url("tweets-qid", "42", cursor="ghi")
    detail_url = build_tweet_detail_url("detail-qid", "2026531440414925307")
    operation, variables = request_details(bookmarks_url)
    assert operation == "Bookmarks"
    assert variables["cursor"] == "abc"
    bookmarks_query = parse_qs(urlparse(bookmarks_url).query)
    assert '"withArticlePlainText":true' in bookmarks_query["fieldToggles"][0]
    assert '"withArticleSummaryText":true' in bookmarks_query["fieldToggles"][0]
    operation, variables = request_details(likes_url)
    assert operation == "Likes"
    assert variables["userId"] == "42"
    assert variables["cursor"] == "def"
    operation, variables = request_details(tweets_url)
    assert operation == "UserTweets"
    assert variables["userId"] == "42"
    assert variables["cursor"] == "ghi"
    operation, variables = request_details(detail_url)
    assert operation == "TweetDetail"
    assert variables["focalTweetId"] == "2026531440414925307"


def test_parse_tweet_detail_response_real_article_fixture() -> None:
    fixture = Path(__file__).parent / "fixtures" / "dimitris_article_tweet_detail.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))

    focal = parse_tweet_detail_response(payload, "2026531440414925307")

    assert focal.is_available
    assert focal.kind == FocalResultKind.AVAILABLE
    tweet = focal.tweet
    assert tweet is not None
    assert tweet.tweet_id == "2026531440414925307"
    article = ((tweet.raw_json.get("article") or {}).get("article_results") or {}).get(
        "result"
    ) or {}
    assert article["title"] == "You Don't Need to Run Every Eval"
    assert len(article["plain_text"]) == 17308


def test_parse_tweet_detail_tweets_collects_all_context_tweets() -> None:
    payload = make_tweet_detail_response(
        [
            make_tweet_result("100", "root", user_id="1000"),
            make_tweet_result(
                "200",
                "parent",
                user_id="2000",
                in_reply_to_status_id="150",
            ),
        ],
        module=True,
    )

    tweets = parse_tweet_detail_tweets(payload)

    assert [tweet.tweet_id for tweet in tweets] == ["100", "200"]
    assert parse_tweet_detail_response(payload, "200").is_available


def test_parse_tweet_detail_response_preserves_unavailable_focal_payload() -> None:
    unavailable = {
        "__typename": "TweetTombstone",
        "tombstone": {"text": {"text": "These posts are protected."}},
        "reason": {"text": "raw reason metadata"},
    }
    payload = {
        "data": {
            "threaded_conversation_with_injections_v2": {
                "instructions": [
                    {
                        "entries": [
                            {
                                "entryId": "tweet-900",
                                "content": {
                                    "itemContent": {"tweet_results": {"result": unavailable}}
                                },
                            }
                        ]
                    }
                ]
            }
        }
    }

    focal = parse_tweet_detail_response(payload, "900")

    assert not focal.is_available
    assert focal.is_explicitly_unavailable
    assert focal.unavailable is not None
    assert focal.unavailable.reason == "protected_account"
    assert focal.unavailable.detail == "These posts are protected."
    assert focal.unavailable.raw_result is unavailable


@pytest.mark.parametrize(
    "message",
    [
        "This Post was deleted by the Post author.",
        "You're unable to view this Post because this account owner limits who can view "
        "their Posts.",
    ],
)
def test_parse_tweet_detail_does_not_assign_unrelated_tombstone_to_focal(
    message: str,
) -> None:
    unrelated = {
        "__typename": "TweetTombstone",
        "rest_id": "999",
        "tombstone": {"text": {"text": message}},
    }

    focal = parse_tweet_detail_response(
        _detail_payload([_detail_entry("tweet-999", unrelated)]),
        "123",
    )

    assert not focal.is_available
    assert focal.is_absent
    assert focal.unavailable is not None
    assert focal.unavailable.typename == "FocalTweetAbsent"
    assert focal.unavailable.reason == "unavailable_unknown"
    assert focal.unavailable.raw_result == {}


def test_parse_tweet_detail_matches_focal_tombstone_by_exact_entry_id() -> None:
    unavailable = {
        "__typename": "TweetTombstone",
        "tombstone": {"text": {"text": "This account is suspended."}},
    }

    focal = parse_tweet_detail_response(
        _detail_payload([_detail_entry("tweet-123", unavailable)]),
        "123",
    )

    assert focal.unavailable is not None
    assert focal.is_explicitly_unavailable
    assert focal.unavailable.reason == "suspended_account"
    assert focal.unavailable.raw_result is unavailable


@pytest.mark.parametrize("nested", [False, True])
def test_parse_tweet_detail_matches_empty_focal_result_sentinel(nested: bool) -> None:
    empty_results: dict[str, object] = {}
    if nested:
        entry: dict[str, Any] = {
            "entryId": "conversationthread-999",
            "content": {
                "items": [
                    {
                        "entryId": "conversationthread-999-tweet-123",
                        "item": {
                            "itemContent": {
                                "__typename": "TimelineTweet",
                                "itemType": "TimelineTweet",
                                "tweet_results": empty_results,
                            }
                        },
                    }
                ]
            },
        }
    else:
        entry = _empty_detail_entry("tweet-123")
        empty_results = entry["content"]["itemContent"]["tweet_results"]

    focal = parse_tweet_detail_response(_detail_payload([entry]), "123")

    assert focal.kind == FocalResultKind.EXPLICIT_UNAVAILABLE
    assert focal.unavailable is not None
    assert focal.unavailable.tweet_id == "123"
    assert focal.unavailable.typename == "TweetResultEmpty"
    assert focal.unavailable.reason == "unavailable_unknown"
    assert focal.unavailable.detail == (
        "TweetDetail returned an empty tweet_results object for the requested focal entry."
    )
    assert focal.unavailable.raw_result is empty_results


@pytest.mark.parametrize(
    "entry_id",
    ["tweet-1234", "conversationthread-999-tweet-1234", "tweet-999"],
)
def test_parse_tweet_detail_rejects_unrelated_empty_result_sentinel(entry_id: str) -> None:
    focal = parse_tweet_detail_response(
        _detail_payload([_empty_detail_entry(entry_id)]),
        "123",
    )

    assert focal.kind == FocalResultKind.ABSENT


def test_parse_tweet_detail_keeps_nonempty_malformed_focal_result_absent() -> None:
    entry = _empty_detail_entry("tweet-123")
    entry["content"]["itemContent"]["tweet_results"] = {"unexpected": True}

    focal = parse_tweet_detail_response(_detail_payload([entry]), "123")

    assert focal.kind == FocalResultKind.ABSENT


def test_parse_tweet_detail_keeps_non_timeline_tweet_empty_result_absent() -> None:
    entry = _empty_detail_entry("tweet-123")
    entry["content"]["itemContent"]["itemType"] = "TimelineUser"

    focal = parse_tweet_detail_response(_detail_payload([entry]), "123")

    assert focal.kind == FocalResultKind.ABSENT


def test_parse_tweet_detail_prefers_available_focal_over_empty_sentinel() -> None:
    focal = parse_tweet_detail_response(
        _detail_payload(
            [
                _empty_detail_entry("tweet-123"),
                _detail_entry("tweet-123", make_tweet_result("123", "available focal")),
            ]
        ),
        "123",
    )

    assert focal.kind == FocalResultKind.AVAILABLE
    assert focal.tweet is not None
    assert focal.tweet.tweet_id == "123"


@pytest.mark.parametrize(
    ("entry_id", "expected"),
    [
        (None, False),
        ("tweet-123", True),
        ("conversationthread-123-tweet-123", True),
        ("conversationthread-999-tweet-123", True),
        ("some-other-module-tweet-123", True),
        ("tweet-1234", False),
        ("conversationthread-123-tweet-1234", False),
        ("conversationthread-123-tweet-999", False),
        ("not-a-tweet-123-extra", False),
    ],
)
def test_entry_id_targets_tweet_uses_exact_suffix_boundaries(
    entry_id: str | None,
    expected: bool,
) -> None:
    assert _entry_id_targets_tweet(entry_id, "123") is expected


@pytest.mark.parametrize(
    ("entry_id", "message", "expected_reason"),
    [
        ("conversationthread-123-tweet-123", "These posts are protected.", "protected_account"),
        (
            "conversationthread-999-tweet-123",
            "This account is suspended.",
            "suspended_account",
        ),
        (
            "some-other-module-tweet-123",
            "This account doesn't exist.",
            "account_missing",
        ),
        (
            "conversationthread-123-tweet-123",
            "This Post was deleted by the Post author.",
            "deleted_by_author",
        ),
        (
            "conversationthread-123-tweet-123",
            "Dieses Posting ist nicht verfügbar.",
            "unavailable_unknown",
        ),
    ],
)
def test_parse_tweet_detail_matches_nested_focal_tombstones_and_preserves_details(
    entry_id: str,
    message: str,
    expected_reason: str,
) -> None:
    unavailable = {
        "__typename": "TweetTombstone",
        "tombstone": {"text": {"text": message}},
    }

    focal = parse_tweet_detail_response(
        _detail_payload([_detail_entry(entry_id, unavailable)]),
        "123",
    )

    assert focal.kind == FocalResultKind.EXPLICIT_UNAVAILABLE
    assert focal.unavailable is not None
    assert focal.unavailable.tweet_id == "123"
    assert focal.unavailable.typename == "TweetTombstone"
    assert focal.unavailable.reason == expected_reason
    assert focal.unavailable.detail == message
    assert focal.unavailable.raw_result is unavailable


@pytest.mark.parametrize(
    "entry_id",
    [
        "conversationthread-123-tweet-999",
        "conversationthread-1-tweet-1234",
    ],
)
def test_parse_tweet_detail_rejects_unrelated_nested_tombstones(entry_id: str) -> None:
    unavailable = {
        "__typename": "TweetTombstone",
        "tombstone": {"text": {"text": "This account is suspended."}},
    }

    focal = parse_tweet_detail_response(
        _detail_payload([_detail_entry(entry_id, unavailable)]),
        "123",
    )

    assert focal.kind == FocalResultKind.ABSENT


def test_parse_tweet_detail_explicit_nonmatching_id_wins_over_nested_entry_match() -> None:
    unavailable = {
        "__typename": "TweetTombstone",
        "rest_id": "999",
        "tombstone": {"text": {"text": "This account is suspended."}},
    }

    focal = parse_tweet_detail_response(
        _detail_payload([_detail_entry("conversationthread-1-tweet-123", unavailable)]),
        "123",
    )

    assert focal.kind == FocalResultKind.ABSENT


def test_parse_tweet_detail_prefers_available_focal_over_unrelated_tombstones() -> None:
    unrelated = {
        "__typename": "TweetTombstone",
        "rest_id": "999",
        "tombstone": {"text": {"text": "This Post was deleted by the Post author."}},
    }
    available = make_tweet_result("123", "available focal")

    focal = parse_tweet_detail_response(
        _detail_payload(
            [
                _detail_entry("tweet-999", unrelated),
                _detail_entry("tweet-123", available),
            ]
        ),
        "123",
    )

    assert focal.is_available
    assert focal.tweet is not None
    assert focal.tweet.tweet_id == "123"


def test_parse_tweet_detail_multiple_unmatched_tombstones_remain_unknown() -> None:
    entries = [
        _detail_entry(
            f"tweet-{tweet_id}",
            {
                "__typename": "TweetUnavailable",
                "rest_id": tweet_id,
                "reason": message,
            },
        )
        for tweet_id, message in (
            ("998", "This account is suspended."),
            ("999", "This Post was deleted by the Post author."),
        )
    ]

    focal = parse_tweet_detail_response(_detail_payload(entries), "123")

    assert focal.unavailable is not None
    assert focal.unavailable.typename == "FocalTweetAbsent"
    assert focal.unavailable.reason == "unavailable_unknown"


def test_parse_timeline_response_bookmarks_shape() -> None:
    tweets, cursor = parse_timeline_response(
        make_bookmarks_response(["1", "2"], cursor="next"), "Bookmarks"
    )
    assert [tweet.tweet_id for tweet in tweets] == ["1", "2"]
    assert cursor == "next"


def test_parse_timeline_response_likes_module_shape() -> None:
    tweets, cursor = parse_timeline_response(
        make_likes_response(["10", "11"], cursor="older", module=True),
        "Likes",
    )
    assert [tweet.tweet_id for tweet in tweets] == ["10", "11"]
    assert cursor == "older"


def test_parse_timeline_response_user_tweets_shape() -> None:
    tweets, cursor = parse_timeline_response(
        make_user_tweets_response(["20", "21"], cursor="later"),
        "UserTweets",
    )
    assert [tweet.tweet_id for tweet in tweets] == ["20", "21"]
    assert cursor == "later"


def test_response_classification_helpers() -> None:
    request = httpx.Request("GET", "https://example.com")
    assert is_rate_limit(httpx.Response(429, request=request))
    assert is_auth_error(httpx.Response(401, request=request))
    assert is_feature_flag_error(httpx.Response(400, request=request))
    assert is_stale_query_id(httpx.Response(404, request=request))


@pytest.mark.asyncio
async def test_fetch_page_refreshes_once_on_404() -> None:
    responses = deque(
        [
            httpx.Response(404, request=httpx.Request("GET", "https://example.com/one")),
            httpx.Response(
                200, json={"ok": True}, request=httpx.Request("GET", "https://example.com/two")
            ),
        ]
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return responses.popleft()

    refreshed = {"count": 0}
    messages: list[str] = []

    async def refresh_once() -> str:
        refreshed["count"] += 1
        return "https://example.com/two"

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        response = await fetch_page(
            client,
            "https://example.com/one",
            SyncConfig(),
            refresh_once=refresh_once,
            status=messages.append,
        )
    finally:
        await client.aclose()

    assert response.status_code == 200
    assert refreshed["count"] == 1
    assert messages == ["query ID stale (HTTP 404), refreshing once"]


@pytest.mark.asyncio
async def test_fetch_page_raises_after_repeated_429() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    delays: list[float] = []
    messages: list[str] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    try:
        with pytest.raises(RateLimitExhaustedError):
            await fetch_page(
                client,
                "https://example.com",
                SyncConfig(
                    max_retries=1, backoff_base=0.1, cooldown_threshold=1, cooldown_duration=0.2
                ),
                status=messages.append,
                sleep=fake_sleep,
            )
    finally:
        await client.aclose()

    assert delays == [0.1, 0.2, 0.1]
    assert messages == [
        "rate limited (HTTP 429), retry 1/1 in 0.1s",
        "rate limited repeatedly, cooling down for 0.2s",
        "rate limited (HTTP 429), retry 1/1 in 0.1s",
    ]


@pytest.mark.asyncio
async def test_fetch_page_honors_rate_limit_overrides() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    delays: list[float] = []
    messages: list[str] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    try:
        with pytest.raises(RateLimitExhaustedError):
            await fetch_page(
                client,
                "https://example.com",
                SyncConfig(
                    max_retries=5,
                    backoff_base=9.0,
                    cooldown_threshold=3,
                    cooldown_duration=0.2,
                ),
                max_retries=2,
                backoff_base=0.1,
                status=messages.append,
                sleep=fake_sleep,
            )
    finally:
        await client.aclose()

    assert delays == [0.1, 0.2, 0.2, 0.1, 0.2]
    assert messages == [
        "rate limited (HTTP 429), retry 1/2 in 0.1s",
        "rate limited (HTTP 429), retry 2/2 in 0.2s",
        "rate limited repeatedly, cooling down for 0.2s",
        "rate limited (HTTP 429), retry 1/2 in 0.1s",
        "rate limited (HTTP 429), retry 2/2 in 0.2s",
    ]


@pytest.mark.asyncio
async def test_fetch_page_honors_retry_after_header() -> None:
    responses = deque(
        [
            httpx.Response(429, headers={"retry-after": "7"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        response = responses.popleft()
        response.request = request
        return response

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    delays: list[float] = []
    messages: list[str] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    try:
        response = await fetch_page(
            client,
            "https://example.com",
            SyncConfig(),
            status=messages.append,
            sleep=fake_sleep,
        )
    finally:
        await client.aclose()

    assert response.status_code == 200
    assert delays == [7.0]
    assert messages == ["rate limited (HTTP 429), waiting 7.0s before retry (retry-after 7.0s)"]
