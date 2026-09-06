"""Timeline request builders and parsers."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import urlencode

import httpx

from tweetnook.client.base import request_with_backoff
from tweetnook.client.features import (
    build_bookmarks_features,
    build_field_toggles,
    build_likes_features,
    build_tweet_detail_features,
    build_user_tweets_features,
)
from tweetnook.config import API_BASE_URL, SyncConfig
from tweetnook.extractor import (
    classify_tweet_unavailability,
    extract_author_fields,
    extract_canonical_text,
    extract_tweet_unavailability_detail,
    unwrap_tweet_result,
)


@dataclass(slots=True)
class TimelineTweet:
    tweet_id: str
    text: str
    author_id: str | None
    author_username: str | None
    author_display_name: str | None
    created_at: str | None
    sort_index: str | None
    raw_json: dict[str, Any]


@dataclass(slots=True)
class TweetUnavailableInfo:
    tweet_id: str
    typename: str
    reason: str
    detail: str | None
    raw_result: dict[str, Any]


class FocalResultKind(StrEnum):
    AVAILABLE = "available"
    EXPLICIT_UNAVAILABLE = "explicit_unavailable"
    ABSENT = "absent"


MAX_CONSECUTIVE_FOCAL_ABSENCES = 3


@dataclass(slots=True)
class FocalTweetDetailResult:
    kind: FocalResultKind
    tweet: TimelineTweet | None = None
    unavailable: TweetUnavailableInfo | None = None

    @property
    def is_available(self) -> bool:
        return self.kind == FocalResultKind.AVAILABLE

    @property
    def is_explicitly_unavailable(self) -> bool:
        return self.kind == FocalResultKind.EXPLICIT_UNAVAILABLE

    @property
    def is_absent(self) -> bool:
        return self.kind == FocalResultKind.ABSENT

    @property
    def tweet_id(self) -> str:
        if self.tweet is not None:
            return self.tweet.tweet_id
        assert self.unavailable is not None
        return self.unavailable.tweet_id

    @property
    def raw_json(self) -> dict[str, Any]:
        if self.tweet is not None:
            return self.tweet.raw_json
        assert self.unavailable is not None
        return self.unavailable.raw_result


def _encode_param(value: dict[str, Any]) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _timeline_params(variables: dict[str, Any], *, features: dict[str, bool]) -> str:
    params = {
        "variables": _encode_param(variables),
        "features": _encode_param(features),
        "fieldToggles": _encode_param(build_field_toggles()),
    }
    return urlencode(params)


def build_bookmarks_url(query_id: str, cursor: str | None = None, *, count: int = 20) -> str:
    variables: dict[str, Any] = {
        "count": count,
        "includePromotedContent": False,
        "withBirdwatchNotes": False,
        "withClientEventToken": False,
        "withVoice": True,
        "withV2Timeline": True,
    }
    if cursor:
        variables["cursor"] = cursor
    params = _timeline_params(variables, features=build_bookmarks_features())
    return f"{API_BASE_URL}/{query_id}/Bookmarks?{params}"


def build_likes_url(
    query_id: str, user_id: str, cursor: str | None = None, *, count: int = 20
) -> str:
    variables: dict[str, Any] = {
        "count": count,
        "includePromotedContent": False,
        "userId": user_id,
        "withBirdwatchNotes": False,
        "withClientEventToken": False,
        "withVoice": True,
        "withV2Timeline": True,
    }
    if cursor:
        variables["cursor"] = cursor
    params = _timeline_params(variables, features=build_likes_features())
    return f"{API_BASE_URL}/{query_id}/Likes?{params}"


def build_user_tweets_url(
    query_id: str, user_id: str, cursor: str | None = None, *, count: int = 20
) -> str:
    variables: dict[str, Any] = {
        "count": count,
        "includePromotedContent": True,
        "userId": user_id,
        "withQuickPromoteEligibilityTweetFields": True,
        "withVoice": True,
        "withV2Timeline": True,
    }
    if cursor:
        variables["cursor"] = cursor
    params = _timeline_params(variables, features=build_user_tweets_features())
    return f"{API_BASE_URL}/{query_id}/UserTweets?{params}"


def build_tweet_detail_url(query_id: str, tweet_id: str) -> str:
    variables: dict[str, Any] = {
        "focalTweetId": tweet_id,
        "withCommunity": True,
        "withVoice": True,
        "withBirdwatchNotes": True,
        "includePromotedContent": True,
    }
    params = _timeline_params(variables, features=build_tweet_detail_features())
    return f"{API_BASE_URL}/{query_id}/TweetDetail?{params}"


async def fetch_page(
    client: httpx.AsyncClient,
    url: str,
    sync_config: SyncConfig,
    *,
    max_retries: int | None = None,
    backoff_base: float | None = None,
    refresh_once: Callable[[], Awaitable[str]] | None = None,
    status: Callable[[str], None] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> httpx.Response:
    return await request_with_backoff(
        client,
        url,
        sync_config,
        max_retries=max_retries,
        backoff_base=backoff_base,
        refresh_once=refresh_once,
        status=status,
        sleep=sleep,
    )


def _extract_tweet_result_slots(
    content: dict[str, Any],
    entry_id: str | None,
) -> list[tuple[dict[str, Any], str | None, dict[str, Any]]]:
    slots: list[tuple[dict[str, Any], str | None, dict[str, Any]]] = []
    item_content = content.get("itemContent") or content.get("content", {}).get("itemContent")
    if isinstance(item_content, dict):
        tweet_results = item_content.get("tweet_results")
        if isinstance(tweet_results, dict):
            slots.append((tweet_results, entry_id, item_content))

    for item in content.get("items", []):
        if not isinstance(item, dict):
            continue
        nested_item = item.get("item", {})
        nested_content = nested_item.get("itemContent")
        if isinstance(nested_content, dict):
            tweet_results = nested_content.get("tweet_results")
            if isinstance(tweet_results, dict):
                nested_entry_id = item.get("entryId") or nested_item.get("entryId") or entry_id
                slots.append(
                    (
                        tweet_results,
                        str(nested_entry_id) if nested_entry_id is not None else None,
                        nested_content,
                    )
                )
    return slots


def _extract_raw_tweet_result_entries(
    content: dict[str, Any],
    entry_id: str | None,
) -> list[tuple[dict[str, Any], str | None]]:
    results: list[tuple[dict[str, Any], str | None]] = []
    for tweet_results, result_entry_id, _item_content in _extract_tweet_result_slots(
        content, entry_id
    ):
        result = tweet_results.get("result")
        if isinstance(result, dict):
            results.append((result, result_entry_id))
    return results


def _extract_raw_tweet_results_from_content(content: dict[str, Any]) -> list[dict[str, Any]]:
    return [result for result, _entry_id in _extract_raw_tweet_result_entries(content, None)]


def _entry_id_targets_tweet(entry_id: str | None, tweet_id: str) -> bool:
    """Return whether a timeline entry identifies the requested tweet."""
    if not entry_id:
        return False
    direct_entry_id = f"tweet-{tweet_id}"
    return entry_id == direct_entry_id or entry_id.endswith(f"-{direct_entry_id}")


def _extract_tweet_results_from_content(content: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        unwrapped
        for raw_result in _extract_raw_tweet_results_from_content(content)
        if (unwrapped := unwrap_tweet_result(raw_result)) is not None
    ]


def _iter_entries(node: Any) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if "entryId" in node and "content" in node:
            entries.append(node)
        for value in node.values():
            entries.extend(_iter_entries(value))
    elif isinstance(node, list):
        for item in node:
            entries.extend(_iter_entries(item))
    return entries


def _extract_cursor(entry: dict[str, Any]) -> str | None:
    entry_id = entry.get("entryId", "")
    content = entry.get("content", {})
    if entry_id.startswith("cursor-bottom-"):
        return content.get("value")
    if content.get("cursorType") == "Bottom":
        return content.get("value")
    return None


def _tweet_from_result(result: dict[str, Any], *, sort_index: str | None) -> TimelineTweet | None:
    if result.get("__typename") in {"TweetTombstone", "TweetUnavailable"}:
        return None
    legacy = result.get("legacy") or {}
    tweet_id = result.get("rest_id")
    if not tweet_id:
        return None
    author_id, author_username, author_display_name = extract_author_fields(result)
    return TimelineTweet(
        tweet_id=tweet_id,
        text=extract_canonical_text(result),
        author_id=author_id,
        author_username=author_username,
        author_display_name=author_display_name,
        created_at=legacy.get("created_at"),
        sort_index=sort_index,
        raw_json=result,
    )


def parse_timeline_response(
    data: dict[str, Any], operation: str
) -> tuple[list[TimelineTweet], str | None]:
    tweets: list[TimelineTweet] = []
    seen_ids: set[str] = set()
    bottom_cursor: str | None = None

    for entry in _iter_entries(data):
        bottom_cursor = bottom_cursor or _extract_cursor(entry)
        sort_index = entry.get("sortIndex")
        for result in _extract_tweet_results_from_content(entry.get("content", {})):
            tweet = _tweet_from_result(result, sort_index=sort_index)
            if tweet and tweet.tweet_id not in seen_ids:
                seen_ids.add(tweet.tweet_id)
                tweets.append(tweet)

    if operation not in {"Bookmarks", "Likes", "UserTweets"}:
        raise ValueError(f"Unsupported timeline operation: {operation}")
    return tweets, bottom_cursor


def parse_tweet_detail_tweets(data: dict[str, Any]) -> list[TimelineTweet]:
    tweets: list[TimelineTweet] = []
    seen_ids: set[str] = set()
    for entry in _iter_entries(data):
        sort_index = entry.get("sortIndex")
        for result in _extract_tweet_results_from_content(entry.get("content", {})):
            tweet = _tweet_from_result(result, sort_index=sort_index)
            if tweet and tweet.tweet_id not in seen_ids:
                seen_ids.add(tweet.tweet_id)
                tweets.append(tweet)
    return tweets


def parse_tweet_detail_response(
    data: dict[str, Any],
    focal_tweet_id: str,
) -> FocalTweetDetailResult:
    empty_focal_result: dict[str, Any] | None = None
    for entry in _iter_entries(data):
        entry_id = str(entry.get("entryId") or "")
        sort_index = entry.get("sortIndex")
        result_slots = _extract_tweet_result_slots(entry.get("content", {}), entry_id)
        for tweet_results, result_entry_id, item_content in result_slots:
            raw_result = tweet_results.get("result")
            if not isinstance(raw_result, dict):
                if (
                    tweet_results == {}
                    and item_content.get("__typename") == "TimelineTweet"
                    and item_content.get("itemType") == "TimelineTweet"
                    and _entry_id_targets_tweet(result_entry_id, focal_tweet_id)
                ):
                    empty_focal_result = tweet_results
                continue
            result = unwrap_tweet_result(raw_result)
            if result is None:
                continue
            tweet = _tweet_from_result(result, sort_index=sort_index)
            if tweet is not None:
                if tweet.tweet_id == focal_tweet_id:
                    return FocalTweetDetailResult(
                        kind=FocalResultKind.AVAILABLE,
                        tweet=tweet,
                    )
                continue
            typename = str(result.get("__typename") or raw_result.get("__typename") or "")
            if typename not in {"TweetTombstone", "TweetUnavailable"}:
                continue
            detail = extract_tweet_unavailability_detail(result)
            unavailable = TweetUnavailableInfo(
                tweet_id=focal_tweet_id,
                typename=typename,
                reason=classify_tweet_unavailability(typename, detail),
                detail=detail,
                raw_result=result,
            )
            result_id = str(result.get("rest_id") or raw_result.get("rest_id") or "")
            entry_matches = _entry_id_targets_tweet(result_entry_id, focal_tweet_id)
            if result_id == focal_tweet_id or (not result_id and entry_matches):
                return FocalTweetDetailResult(
                    kind=FocalResultKind.EXPLICIT_UNAVAILABLE,
                    unavailable=unavailable,
                )
    if empty_focal_result is not None:
        return FocalTweetDetailResult(
            kind=FocalResultKind.EXPLICIT_UNAVAILABLE,
            unavailable=TweetUnavailableInfo(
                tweet_id=focal_tweet_id,
                typename="TweetResultEmpty",
                reason="unavailable_unknown",
                detail=(
                    "TweetDetail returned an empty tweet_results object for the requested "
                    "focal entry."
                ),
                raw_result=empty_focal_result,
            ),
        )
    return FocalTweetDetailResult(
        kind=FocalResultKind.ABSENT,
        unavailable=TweetUnavailableInfo(
            tweet_id=focal_tweet_id,
            typename="FocalTweetAbsent",
            reason="unavailable_unknown",
            detail="TweetDetail response did not contain an identifiable focal result.",
            raw_result={},
        ),
    )
