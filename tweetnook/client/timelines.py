"""Timeline and TweetDetail request builders."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

from tweetnook.client.features import (
    build_field_toggles,
    build_timeline_features,
)

API_BASE_URL = "https://x.com/i/api/graphql"


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
    params = _timeline_params(variables, features=build_timeline_features())
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
    params = _timeline_params(variables, features=build_timeline_features())
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
    params = _timeline_params(variables, features=build_timeline_features())
    return f"{API_BASE_URL}/{query_id}/UserTweets?{params}"


def build_tweet_detail_url(query_id: str, tweet_id: str) -> str:
    variables: dict[str, Any] = {
        "focalTweetId": tweet_id,
        "withCommunity": True,
        "withVoice": True,
        "withBirdwatchNotes": True,
        "includePromotedContent": True,
    }
    params = _timeline_params(variables, features=build_timeline_features())
    return f"{API_BASE_URL}/{query_id}/TweetDetail?{params}"
