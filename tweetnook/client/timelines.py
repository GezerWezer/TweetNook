"""Timeline and TweetDetail request builders."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
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
