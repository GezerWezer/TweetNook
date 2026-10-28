"""Helpers and fixtures shared by the collection sync tests."""

from __future__ import annotations

import json
from io import StringIO
from urllib.parse import parse_qs, urlparse

import httpx
from rich.console import Console

from tweetnook.query_ids import QueryIdStore


def _op_and_variables(request: httpx.Request) -> tuple[str, dict[str, object]]:
    parsed = urlparse(str(request.url))
    operation = parsed.path.split("/")[-1]
    variables = json.loads(parse_qs(parsed.query)["variables"][0])
    return operation, variables


def _save_query_ids(paths) -> None:
    QueryIdStore(paths).save(
        {
            "Bookmarks": "qid-bookmarks",
            "Likes": "qid-likes",
            "UserTweets": "qid-user-tweets",
        }
    )


def _console() -> Console:
    return Console(file=StringIO(), force_terminal=False, color_system=None)


def _bookmarks_response_from_results(
    results: list[dict[str, object]],
    *,
    cursor: str | None = None,
) -> dict[str, object]:
    entries: list[dict[str, object]] = []
    for index, result in enumerate(results):
        tweet_id = result["rest_id"]
        entries.append(
            {
                "entryId": f"tweet-{tweet_id}",
                "sortIndex": str(500 - index),
                "content": {
                    "entryType": "TimelineTimelineItem",
                    "itemContent": {
                        "itemType": "TimelineTweet",
                        "tweet_results": {"result": result},
                    },
                },
            }
        )
    if cursor is not None:
        entries.append(
            {
                "entryId": "cursor-bottom-1",
                "content": {"cursorType": "Bottom", "value": cursor},
            }
        )
    return {
        "data": {
            "bookmark_timeline_v2": {
                "timeline": {
                    "instructions": [{"type": "TimelineAddEntries", "entries": entries}],
                }
            }
        }
    }
