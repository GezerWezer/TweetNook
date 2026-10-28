"""Archive rows for authored posts and likes: payloads adapted to the GraphQL shape, like
placeholders, secondary graph records and the batched writes that store them."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from tweetnook.core.exceptions import (
    ConfigError,
)
from tweetnook.core.extractor import ExtractedTweetGraph, extract_secondary_objects
from tweetnook.core.timeline_parsing import (
    TimelineTweet,
)
from tweetnook.importer.archive_input import _ArchiveIdentity
from tweetnook.jobs.context import (
    ArchiveWriteTracker,
)
from tweetnook.storage import ArchiveStore
from tweetnook.storage.common import ARCHIVE_SOURCE, LIVE_SOURCE
from tweetnook.storage.records import _PageBuffer

_AUTHORED_IMPORT_PREFETCH_CHUNK = 50


_LIKE_IMPORT_PREFETCH_CHUNK = 200


_T = TypeVar("_T")


@dataclass(slots=True)
class _PlaceholderTweetObject:
    tweet_id: str
    text: str
    author_id: str | None = None
    author_username: str | None = None
    author_display_name: str | None = None
    created_at: str | None = None
    conversation_id: str | None = None
    lang: str | None = None
    note_tweet_text: str | None = None
    raw_json: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class _PreparedAuthoredImport:
    timeline_tweet: TimelineTweet
    deleted_at: str | None
    graph: ExtractedTweetGraph


@dataclass(slots=True)
class _PreparedLikeImport:
    tweet_id: str
    timeline_tweet: TimelineTweet
    payload: dict[str, Any]


def _media_key_for_archive_item(item: dict[str, Any], *, position: int) -> str:
    media_id = item.get("id_str") or item.get("id")
    media_type = item.get("type")
    prefix = "3" if media_type == "photo" else "7"
    if media_id:
        return f"{prefix}_{media_id}"
    return f"{prefix}_archive_{position}"


def _deepcopy_json(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _adapt_archive_tweet_payload(
    tweet_payload: dict[str, Any], identity: _ArchiveIdentity
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
    identity: _ArchiveIdentity,
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


def _placeholder_tweet_object(like_payload: dict[str, Any]) -> _PlaceholderTweetObject:
    tweet_id = str(like_payload.get("tweetId") or "").strip()
    if not tweet_id:
        raise ConfigError("Archive like row is missing tweetId.")
    return _PlaceholderTweetObject(
        tweet_id=tweet_id,
        text=str(like_payload.get("fullText") or ""),
        raw_json={"like": like_payload},
    )


def _queue_secondary_graph(
    store: ArchiveStore,
    buffer: _PageBuffer,
    graph: ExtractedTweetGraph,
    *,
    source: str,
    deleted_at_by_tweet_id: dict[str, str] | None = None,
    force_deleted_tweet_id: str | None = None,
) -> None:
    deleted_at_by_tweet_id = deleted_at_by_tweet_id or {}
    for item in graph.tweet_objects.values():
        force_deleted = item.tweet_id == force_deleted_tweet_id
        store._queue_record(
            store._tweet_object_record(
                item,
                source=source,
                deleted_at=deleted_at_by_tweet_id.get(item.tweet_id),
                enrichment_state="terminal_unavailable" if force_deleted else None,
                enrichment_reason="archive_deleted" if force_deleted else None,
                enrichment_retry_count=0 if force_deleted else None,
                enrichment_retry_eligible=False if force_deleted else None,
                cursor=buffer,
            ),
            cursor=buffer,
        )
    for item in graph.relations.values():
        store._queue_record(
            store._tweet_relation_record(item, source=source, cursor=buffer),
            cursor=buffer,
        )
    for item in graph.media.values():
        store._queue_record(
            store._media_record(item, provenance_source=source, cursor=buffer),
            cursor=buffer,
        )
    for item in graph.urls.values():
        store._queue_record(store._url_record(item, source=source, cursor=buffer), cursor=buffer)
    for item in graph.url_refs.values():
        store._queue_record(
            store._url_ref_record(item, source=source, cursor=buffer),
            cursor=buffer,
        )
    for item in graph.articles.values():
        store._queue_record(
            store._article_record(item, source=source, cursor=buffer),
            cursor=buffer,
        )


def _flush_buffer(store: ArchiveStore, buffer: _PageBuffer) -> int:
    if not buffer.records:
        return 0
    pending = len(buffer.records)
    store.merge_rows(list(buffer.records.values()))
    buffer.records.clear()
    buffer.pending_tweets.clear()
    buffer.existing_rows.clear()
    return pending


def _deleted_headers_map(items: list[Any]) -> dict[str, str]:
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


def _secondary_graph_row_keys(store: ArchiveStore, graph: ExtractedTweetGraph) -> list[str]:
    row_keys: list[str] = []
    row_keys.extend(
        store._row_key_for_tweet_object(item.tweet_id) for item in graph.tweet_objects.values()
    )
    row_keys.extend(
        store._row_key_for_tweet_relation(
            item.source_tweet_id,
            item.relation_type,
            item.target_tweet_id,
        )
        for item in graph.relations.values()
    )
    row_keys.extend(
        store._row_key_for_media(item.tweet_id, item.media_key) for item in graph.media.values()
    )
    row_keys.extend(store._row_key_for_url(item.url_hash) for item in graph.urls.values())
    row_keys.extend(
        store._row_key_for_url_ref(item.tweet_id, item.position) for item in graph.url_refs.values()
    )
    row_keys.extend(store._row_key_for_article(item.tweet_id) for item in graph.articles.values())
    return row_keys


def _import_authored_tweets(
    store: ArchiveStore,
    tweets: list[Any],
    identity: _ArchiveIdentity,
    *,
    deleted_headers: dict[str, str],
    counts: dict[str, int],
    force_deleted: bool = False,
    progress: Callable[[int, int], None] | None = None,
    item_status: Callable[[str, int, int], None] | None = None,
    write_tracker: ArchiveWriteTracker | None = None,
) -> None:
    buffer = _PageBuffer()
    valid_items = [
        item for item in tweets if isinstance(item, dict) and isinstance(item.get("tweet"), dict)
    ]
    total = len(valid_items)
    processed = 0
    for chunk_items in _chunked(valid_items, _AUTHORED_IMPORT_PREFETCH_CHUNK):
        prepared: list[_PreparedAuthoredImport] = []
        for item in chunk_items:
            payload = item["tweet"]
            timeline_tweet = _timeline_tweet_from_archive(
                payload,
                identity,
                sort_index=str(payload.get("id_str") or payload.get("id") or ""),
            )
            deleted_at = deleted_headers.get(timeline_tweet.tweet_id) or payload.get("deleted_at")
            prepared.append(
                _PreparedAuthoredImport(
                    timeline_tweet=timeline_tweet,
                    deleted_at=deleted_at if isinstance(deleted_at, str) else None,
                    graph=extract_secondary_objects(timeline_tweet.raw_json),
                )
            )
        if not prepared:
            continue
        row_keys: list[str] = []
        for item in prepared:
            row_keys.append(store._row_key_for_tweet(item.timeline_tweet.tweet_id, "tweet"))
            row_keys.extend(_secondary_graph_row_keys(store, item.graph))
        store.prefetch_rows(row_keys, cursor=buffer)
        for item in prepared:
            timeline_tweet = item.timeline_tweet
            if item_status is not None:
                item_status(timeline_tweet.tweet_id, processed, total)
            store.upsert_tweet(timeline_tweet, cursor=buffer)
            store.upsert_membership(
                timeline_tweet.tweet_id,
                "tweet",
                source=ARCHIVE_SOURCE,
                deleted_at=item.deleted_at,
                sort_index=timeline_tweet.sort_index,
                cursor=buffer,
            )
            _queue_secondary_graph(
                store,
                buffer,
                item.graph,
                source=ARCHIVE_SOURCE,
                deleted_at_by_tweet_id=(
                    {timeline_tweet.tweet_id: item.deleted_at} if item.deleted_at else {}
                ),
                force_deleted_tweet_id=timeline_tweet.tweet_id if force_deleted else None,
            )
            if force_deleted or item.deleted_at:
                counts["deleted_authored_tweets"] += 1
            else:
                counts["authored_tweets"] += 1
            processed += 1
            if progress:
                progress(processed, total)
        flushed = _flush_buffer(store, buffer)
        if write_tracker is not None and flushed > 0:
            write_tracker.mark_dirty(rows=flushed, batches=1)


def _should_seed_like_placeholder(store: ArchiveStore, buffer: _PageBuffer, tweet_id: str) -> bool:
    row = store._lookup_row(store._row_key_for_tweet_object(tweet_id), cursor=buffer)
    if row is None:
        return True
    if row.get("source") == LIVE_SOURCE:
        return False
    return not any(
        row.get(field_name)
        for field_name in ("author_id", "author_username", "created_at", "conversation_id", "lang")
    )


def _import_likes(
    store: ArchiveStore,
    likes: list[Any],
    *,
    counts: dict[str, int],
    progress: Callable[[int, int], None] | None = None,
    item_status: Callable[[str, int, int], None] | None = None,
    write_tracker: ArchiveWriteTracker | None = None,
) -> None:
    buffer = _PageBuffer()
    prepared: list[_PreparedLikeImport] = []
    for index, item in enumerate(likes, start=1):
        payload = item.get("like") if isinstance(item, dict) else None
        if not isinstance(payload, dict):
            continue
        tweet_id = str(payload.get("tweetId") or "").strip()
        if not tweet_id:
            continue
        prepared.append(
            _PreparedLikeImport(
                tweet_id=tweet_id,
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
                payload=payload,
            )
        )
    total = len(prepared)
    processed = 0
    for chunk in _chunked(prepared, _LIKE_IMPORT_PREFETCH_CHUNK):
        row_keys: list[str] = []
        for item in chunk:
            row_keys.append(store._row_key_for_tweet(item.tweet_id, "like"))
            row_keys.append(store._row_key_for_tweet_object(item.tweet_id))
        store.prefetch_rows(row_keys, cursor=buffer)
        for item in chunk:
            if item_status is not None:
                item_status(item.tweet_id, processed, total)
            store.upsert_tweet(item.timeline_tweet, cursor=buffer)
            store.upsert_membership(
                item.tweet_id,
                "like",
                source=ARCHIVE_SOURCE,
                sort_index=item.timeline_tweet.sort_index,
                cursor=buffer,
            )
            if _should_seed_like_placeholder(store, buffer, item.tweet_id):
                store._queue_record(
                    store._tweet_object_record(
                        _placeholder_tweet_object(item.payload),
                        source=ARCHIVE_SOURCE,
                        enrichment_state="pending",
                        cursor=buffer,
                    ),
                    cursor=buffer,
                )
            counts["likes"] += 1
            processed += 1
            if progress:
                progress(processed, total)
        flushed = _flush_buffer(store, buffer)
        if write_tracker is not None and flushed > 0:
            write_tracker.mark_dirty(rows=flushed, batches=1)
