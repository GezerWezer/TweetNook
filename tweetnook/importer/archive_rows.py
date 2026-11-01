"""Archive rows for authored posts and likes: payloads adapted to the GraphQL shape, like
placeholders, secondary graph records and the batched writes that store them."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, TypeVar

from tweetnook.core.exceptions import (
    ConfigError,
)
from tweetnook.core.extractor import extract_secondary_objects
from tweetnook.core.timeline_parsing import (
    TimelineTweet,
)
from tweetnook.importer.archive_input import ArchiveIdentity
from tweetnook.storage import ArchiveStore
from tweetnook.storage.ingestion import ImportObservation

_AUTHORED_IMPORT_PREFETCH_CHUNK = 50


_LIKE_IMPORT_PREFETCH_CHUNK = 200


_T = TypeVar("_T")


def _media_key_for_archive_item(item: dict[str, Any], *, position: int) -> str:
    media_id = item.get("id_str") or item.get("id")
    media_type = item.get("type")
    # X keys GIFs 16_ and videos 7_, so an imported row starts out with the key a live one has.
    prefix = {"photo": "3", "animated_gif": "16"}.get(media_type, "7")
    if media_id:
        return f"{prefix}_{media_id}"
    return f"{prefix}_archive_{position}"


def _deepcopy_json(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _adapt_archive_tweet_payload(
    tweet_payload: dict[str, Any], identity: ArchiveIdentity
) -> dict[str, Any]:
    legacy = _deepcopy_json(tweet_payload)
    tweet_id = str(legacy.get("id_str") or legacy.get("id") or "").strip()
    if not tweet_id:
        raise ConfigError("Archive tweet payload is missing id_str.")
    legacy["id_str"] = tweet_id
    legacy["conversation_id_str"] = str(legacy.get("conversation_id_str") or tweet_id)
    for key in ("entities", "extended_entities"):
        block = legacy.get(key) or {}
        media_items = block.get("media")
        if not isinstance(media_items, list):
            continue
        for position, item in enumerate(media_items):
            if isinstance(item, dict) and not item.get("media_key"):
                item["media_key"] = _media_key_for_archive_item(item, position=position)
    user_result: dict[str, Any] = {"__typename": "User"}
    if identity.account_id:
        user_result["rest_id"] = identity.account_id
    user_legacy: dict[str, Any] = {}
    if identity.username:
        user_legacy["screen_name"] = identity.username
    if identity.display_name:
        user_legacy["name"] = identity.display_name
    if user_legacy:
        user_result["legacy"] = user_legacy
    return {
        "__typename": "Tweet",
        "rest_id": tweet_id,
        "legacy": legacy,
        "core": {"user_results": {"result": user_result}},
    }


def _timeline_tweet_from_archive(
    tweet_payload: dict[str, Any],
    identity: ArchiveIdentity,
    *,
    sort_index: str | None,
) -> TimelineTweet:
    raw_json = _adapt_archive_tweet_payload(tweet_payload, identity)
    legacy = raw_json.get("legacy") or {}
    return TimelineTweet(
        tweet_id=raw_json["rest_id"],
        text=str(legacy.get("full_text") or ""),
        author_id=identity.account_id,
        author_username=identity.username,
        author_display_name=identity.display_name,
        created_at=legacy.get("created_at"),
        sort_index=sort_index,
        raw_json=raw_json,
    )


def deleted_headers_map(items: list[Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in items:
        tweet = item.get("tweet") if isinstance(item, dict) else {}
        tweet = tweet or {}
        tweet_id = tweet.get("tweet_id")
        deleted_at = tweet.get("deleted_at")
        if isinstance(tweet_id, str) and tweet_id and isinstance(deleted_at, str) and deleted_at:
            result[tweet_id] = deleted_at
    return result


def _chunked(items: list[_T], size: int):
    for index in range(0, len(items), size):
        yield items[index : index + size]


def import_authored_tweets(
    store: ArchiveStore,
    tweets: list[Any],
    identity: ArchiveIdentity,
    *,
    deleted_headers: dict[str, str],
    counts: dict[str, int],
    force_deleted: bool = False,
    progress: Callable[[int, int], None] | None = None,
    item_status: Callable[[str, int, int], None] | None = None,
) -> None:
    valid_items = [
        item for item in tweets if isinstance(item, dict) and isinstance(item.get("tweet"), dict)
    ]
    total = len(valid_items)
    processed = 0
    for chunk_items in _chunked(valid_items, _AUTHORED_IMPORT_PREFETCH_CHUNK):
        prepared: list[ImportObservation] = []
        for item in chunk_items:
            payload = item["tweet"]
            timeline_tweet = _timeline_tweet_from_archive(
                payload,
                identity,
                sort_index=str(payload.get("id_str") or payload.get("id") or ""),
            )
            deleted_at = deleted_headers.get(timeline_tweet.tweet_id) or payload.get("deleted_at")
            prepared.append(
                ImportObservation(
                    timeline_tweet=timeline_tweet,
                    deleted_at=deleted_at if isinstance(deleted_at, str) else None,
                    graph=extract_secondary_objects(timeline_tweet.raw_json),
                )
            )
        if not prepared:
            continue

        def before(item: ImportObservation) -> None:
            if item_status is not None:
                item_status(item.timeline_tweet.tweet_id, processed, total)

        def after(item: ImportObservation) -> None:
            nonlocal processed
            if force_deleted or item.deleted_at:
                counts["deleted_authored_tweets"] += 1
            else:
                counts["authored_tweets"] += 1
            processed += 1
            if progress:
                progress(processed, total)

        store.ingest_import_batch(
            prepared,
            membership="tweet",
            force_deleted=force_deleted,
            before_item=before,
            after_item=after,
        )


def import_likes(
    store: ArchiveStore,
    likes: list[Any],
    *,
    counts: dict[str, int],
    progress: Callable[[int, int], None] | None = None,
    item_status: Callable[[str, int, int], None] | None = None,
) -> None:
    prepared: list[ImportObservation] = []
    for index, item in enumerate(likes, start=1):
        payload = item.get("like") if isinstance(item, dict) else None
        if not isinstance(payload, dict):
            continue
        tweet_id = str(payload.get("tweetId") or "").strip()
        if not tweet_id:
            continue
        prepared.append(
            ImportObservation(
                timeline_tweet=TimelineTweet(
                    tweet_id=tweet_id,
                    text=str(payload.get("fullText") or ""),
                    author_id=None,
                    author_username=None,
                    author_display_name=None,
                    created_at=None,
                    # Existing list/view code sorts these numerically descending, so -1 stays
                    # ahead of -2 and preserves the original archive file order without a
                    # special-case code path.
                    sort_index=str(-index),
                    raw_json={"like": payload},
                ),
            )
        )
    total = len(prepared)
    processed = 0
    for chunk in _chunked(prepared, _LIKE_IMPORT_PREFETCH_CHUNK):

        def before(item: ImportObservation) -> None:
            if item_status is not None:
                item_status(item.timeline_tweet.tweet_id, processed, total)

        def after(item: ImportObservation) -> None:
            nonlocal processed
            counts["likes"] += 1
            processed += 1
            if progress:
                progress(processed, total)

        store.ingest_import_batch(chunk, membership="like", before_item=before, after_item=after)
