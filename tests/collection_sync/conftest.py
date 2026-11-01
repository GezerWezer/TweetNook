"""Helpers and fixtures shared by the collection sync tests."""

from __future__ import annotations

import json
from io import StringIO
from urllib.parse import parse_qs, urlparse

import httpx
from rich.console import Console

from tweetnook.core.timeline_parsing import TimelineTweet
from tweetnook.importer.archive_manifests import record_archive_capture
from tweetnook.query_ids import QueryIdStore
from tweetnook.storage import ArchiveStore
from tweetnook.storage.ingestion import ImportObservation


def _seed_like_import(
    store: ArchiveStore,
    ids: list[str],
    *,
    digest: str = "export",
    status: str = "completed",
    partial: bool = False,
) -> None:
    store.set_import_manifest(digest, archive_generation_date=None, status=status)
    record_archive_capture(
        store,
        "XArchiveManifest",
        "data/manifest.js",
        {
            "archiveInfo": {"isPartialArchive": partial},
            "dataTypes": {
                "like": {"files": [{"fileName": "data/like.js", "count": str(len(ids))}]}
            },
        },
        archive_digest=digest,
    )
    record_archive_capture(
        store,
        "XArchiveLikes",
        "data/like.js",
        [{"like": {"tweetId": tweet_id}} for tweet_id in ids],
        archive_digest=digest,
    )
    for tweet_id in ids:
        tweet = TimelineTweet(
            tweet_id=tweet_id,
            text="imported",
            author_id=None,
            author_username=None,
            author_display_name=None,
            created_at=None,
            sort_index="-1",
            raw_json={"like": {"tweetId": tweet_id}},
        )
        store.ingest_import_batch([ImportObservation(tweet)], membership="like")


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
