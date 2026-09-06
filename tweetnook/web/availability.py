"""Canonical Web presentation for tweet availability and attached tweets."""

from __future__ import annotations

import json
from typing import Any

from tweetnook.extractor import (
    classify_tweet_unavailability,
    extract_author_fields,
    extract_canonical_text,
    extract_tweet_unavailability_detail,
    unwrap_tweet_result,
)

UNAVAILABLE_MESSAGES = {
    "protected_account": "This tweet is from a protected account.",
    "suspended_account": "This tweet is from a suspended account.",
    "account_missing": "This tweet is from an account that no longer exists.",
    "deleted_by_author": "This tweet was deleted by its author.",
    "archive_deleted": "This tweet was deleted from Twitter/X.",
    "withheld": "This tweet is unavailable in your location.",
    "not_found": "This tweet could not be found.",
    "unavailable_unknown": "This tweet is unavailable.",
}

_OBJECT_COLUMNS = [
    "tweet_id",
    "text",
    "author_id",
    "author_username",
    "author_display_name",
    "created_at",
    "synced_at",
    "raw_json",
    "enrichment_state",
    "enrichment_checked_at",
    "enrichment_reason",
    "enrichment_retry_eligible",
]


def _json_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _direct_tombstone(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if not raw:
        return None
    typename = raw.get("__typename") or raw.get("__typename__")
    if typename in {"TweetTombstone", "TweetUnavailable"} or raw.get("__tombstone__") is True:
        return raw
    return None


def _fallback_unavailable_reason(tweet: dict[str, Any]) -> str | None:
    raw = _json_dict(tweet.get("raw_json"))
    tombstone = _direct_tombstone(raw)
    if tombstone is not None:
        typename = str(tombstone.get("__typename") or tombstone.get("__typename__") or "")
        detail = extract_tweet_unavailability_detail(tombstone)
        return classify_tweet_unavailability(typename, detail)

    text = tweet.get("text")
    if not isinstance(text, str) or not text:
        return None
    normalized = " ".join(text.casefold().split())
    looks_like_system_placeholder = (
        "{learnmore}" in normalized
        or normalized.startswith("this post is from ")
        or normalized.startswith("this tweet is from ")
        or normalized.startswith("this post was deleted ")
        or normalized.startswith("this tweet was deleted ")
        or normalized.startswith("this post is unavailable")
        or normalized.startswith("this tweet is unavailable")
        or normalized.startswith("this post is not available ")
        or normalized.startswith("this tweet is not available ")
    )
    if not looks_like_system_placeholder:
        return None
    reason = classify_tweet_unavailability("", text)
    if reason != "unavailable_unknown":
        return reason
    if "post is unavailable" in normalized or "tweet is unavailable" in normalized:
        return "unavailable_unknown"
    return None


def availability_for_tweet(
    tweet: dict[str, Any],
    object_row: dict[str, Any] | None,
    *,
    relation_only: bool = False,
) -> dict[str, Any]:
    """Return a stable availability contract without trusting display text as primary state."""
    enrichment_state = object_row.get("enrichment_state") if object_row else None
    if enrichment_state == "terminal_unavailable":
        reason = str(object_row.get("enrichment_reason") or "unavailable_unknown")
        if reason not in UNAVAILABLE_MESSAGES:
            reason = "unavailable_unknown"
        return {
            "state": "unavailable",
            "enrichment_state": enrichment_state,
            "reason": reason,
            "message": UNAVAILABLE_MESSAGES[reason],
            "placeholder": True,
            "confirmed": True,
            "retryable": bool(object_row.get("enrichment_retry_eligible")),
            "checked_at": object_row.get("enrichment_checked_at"),
        }

    if enrichment_state in {"done", "resurrected"}:
        return {
            "state": "available",
            "enrichment_state": enrichment_state,
            "reason": None,
            "message": None,
            "placeholder": False,
            "confirmed": True,
            "retryable": False,
            "checked_at": object_row.get("enrichment_checked_at"),
        }

    inferred_reason = _fallback_unavailable_reason(tweet)
    if inferred_reason:
        return {
            "state": "unavailable",
            "enrichment_state": enrichment_state,
            "reason": inferred_reason,
            "message": UNAVAILABLE_MESSAGES[inferred_reason],
            "placeholder": True,
            "confirmed": False,
            "retryable": None,
            "checked_at": object_row.get("enrichment_checked_at") if object_row else None,
        }

    if relation_only:
        return {
            "state": "not_archived",
            "enrichment_state": None,
            "reason": "not_archived",
            "message": "This tweet was not captured in the local archive.",
            "placeholder": True,
            "confirmed": False,
            "retryable": None,
            "checked_at": None,
        }

    if enrichment_state in {"pending", "transient_failure"} or object_row is None:
        has_content = bool(tweet.get("text") or tweet.get("raw_json"))
        return {
            "state": "incomplete",
            "enrichment_state": enrichment_state,
            "reason": None if has_content else "details_not_archived",
            "message": None if has_content else "Tweet details have not been archived yet.",
            "placeholder": not has_content,
            "confirmed": False,
            "retryable": None,
            "checked_at": object_row.get("enrichment_checked_at") if object_row else None,
        }

    return {
        "state": "available",
        "enrichment_state": enrichment_state,
        "reason": None,
        "message": None,
        "placeholder": False,
        "confirmed": False,
        "retryable": False,
        "checked_at": object_row.get("enrichment_checked_at") if object_row else None,
    }


def missing_web_tweet(tweet_id: str) -> dict[str, Any]:
    """Create a relation-backed placeholder without claiming remote unavailability."""
    tweet = {
        "tweet_id": tweet_id,
        "text": "",
        "author": {"id": None, "username": None, "display_name": None},
        "created_at": None,
        "synced_at": None,
        "raw_json": None,
        "media": [],
        "qt_media": [],
        "media_tags": None,
        "qt_media_tags": None,
        "_relation_only": True,
    }
    tweet["availability"] = availability_for_tweet(tweet, None, relation_only=True)
    return tweet


def _embedded_result(raw: dict[str, Any] | None, relation_type: str) -> dict[str, Any] | None:
    if not raw:
        return None
    if relation_type == "quote_of":
        result = (raw.get("quoted_status_result") or {}).get("result")
        if result is None:
            result = raw.get("quoted_status")
    else:
        legacy = raw.get("legacy") or {}
        result = (legacy.get("retweeted_status_result") or {}).get("result")
        if result is None:
            result = raw.get("retweeted_status")
    return unwrap_tweet_result(result) if isinstance(result, dict) else None


def _legacy_target_id(raw: dict[str, Any] | None, relation_type: str) -> str | None:
    if not raw:
        return None
    legacy = raw.get("legacy") or {}
    key = "quoted_status_id_str" if relation_type == "quote_of" else "retweeted_status_id_str"
    value = legacy.get(key)
    return str(value) if value else None


def _media_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": row.get("media_type"),
        "url": row.get("media_url"),
        "thumbnail_url": row.get("thumbnail_url"),
        "width": row.get("width"),
        "height": row.get("height"),
        "duration_millis": row.get("duration_millis"),
        "download": {
            "local_path": row.get("local_path"),
            "thumbnail_local_path": row.get("thumbnail_local_path"),
        },
    }


def _raw_media_payloads(raw: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not raw:
        return []
    legacy = raw.get("legacy") or {}
    extended = legacy.get("extended_entities") or {}
    entities = legacy.get("entities") or {}
    raw_media = extended.get("media") or entities.get("media") or []
    if not isinstance(raw_media, list):
        return []

    media: list[dict[str, Any]] = []
    for item in raw_media:
        if not isinstance(item, dict):
            continue
        original = item.get("original_info") or {}
        large = (item.get("sizes") or {}).get("large") or {}
        video_info = item.get("video_info") or {}
        media.append(
            {
                "type": item.get("type"),
                "width": original.get("width") or large.get("w"),
                "height": original.get("height") or large.get("h"),
                "duration_millis": video_info.get("duration_millis"),
                "download": {
                    "local_path": None,
                    "thumbnail_local_path": None,
                },
            }
        )
    return media


def _normalized_embedded_tweet(raw: dict[str, Any], tweet_id: str | None) -> dict[str, Any]:
    author_id, username, display_name = extract_author_fields(raw)
    legacy = raw.get("legacy") or {}
    return {
        "tweet_id": tweet_id or raw.get("rest_id"),
        "text": extract_canonical_text(raw),
        "author": {"id": author_id, "username": username, "display_name": display_name},
        "created_at": legacy.get("created_at"),
        "synced_at": None,
        "raw_json": raw,
        "media": _raw_media_payloads(raw),
        "media_tags": None,
    }


def _normalized_object_tweet(row: dict[str, Any]) -> dict[str, Any]:
    raw = _json_dict(row.get("raw_json"))
    return {
        "tweet_id": row.get("tweet_id"),
        "text": row.get("text") or "",
        "author": {
            "id": row.get("author_id"),
            "username": row.get("author_username"),
            "display_name": row.get("author_display_name"),
        },
        "created_at": row.get("created_at"),
        "synced_at": row.get("synced_at"),
        "raw_json": raw,
        "media": _raw_media_payloads(raw),
        "media_tags": None,
    }


def _use_canonical_object_content(object_row: dict[str, Any] | None, tweet: dict[str, Any]) -> bool:
    if object_row is None:
        return False
    if object_row.get("enrichment_state") in {"done", "resurrected"}:
        return True
    return not bool(tweet.get("text") or tweet.get("raw_json"))


def _overlay_object_content(tweet: dict[str, Any], object_row: dict[str, Any]) -> None:
    canonical = _normalized_object_tweet(object_row)
    for key in ("text", "author", "created_at", "synced_at", "raw_json"):
        tweet[key] = canonical[key]


def annotate_web_tweets(store: Any, tweets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach canonical availability and normalized quote/retweet targets in bounded batches."""
    tweet_ids = [str(tweet["tweet_id"]) for tweet in tweets if tweet.get("tweet_id")]
    object_rows = store._rows_for_values(
        "tweet_object", "tweet_id", tweet_ids, columns=_OBJECT_COLUMNS
    )
    objects_by_id = {str(row["tweet_id"]): row for row in object_rows if row.get("tweet_id")}

    for tweet in tweets:
        tweet_id = str(tweet.get("tweet_id") or "")
        object_row = objects_by_id.get(tweet_id)
        if not _use_canonical_object_content(object_row, tweet):
            continue
        _overlay_object_content(tweet, object_row)
        tweet["_relation_only"] = False

    relation_rows = store._rows_for_values(
        "tweet_relation",
        "tweet_id",
        tweet_ids,
        columns=["tweet_id", "target_tweet_id", "relation_type"],
    )
    relations: dict[tuple[str, str], str] = {}
    for row in relation_rows:
        relation_type = row.get("relation_type")
        if relation_type not in {"quote_of", "retweet_of"}:
            continue
        source_id = row.get("tweet_id")
        target_id = row.get("target_tweet_id")
        if source_id and target_id:
            relations[(str(source_id), str(relation_type))] = str(target_id)

    attached_specs: dict[tuple[str, str], tuple[str | None, dict[str, Any] | None]] = {}
    attached_ids: list[str] = []
    for tweet in tweets:
        source_id = str(tweet.get("tweet_id") or "")
        raw = _json_dict(tweet.get("raw_json"))
        for relation_type in ("quote_of", "retweet_of"):
            embedded = _embedded_result(raw, relation_type)
            legacy = (raw or {}).get("legacy") or {}
            target_id = (
                relations.get((source_id, relation_type))
                or _legacy_target_id(raw, relation_type)
                or (str(embedded.get("rest_id")) if embedded and embedded.get("rest_id") else None)
            )
            has_quote_marker = relation_type == "quote_of" and bool(
                legacy.get("is_quote_status") or legacy.get("quoted_status_permalink")
            )
            if target_id or embedded or has_quote_marker:
                attached_specs[(source_id, relation_type)] = (target_id, embedded)
            if target_id:
                attached_ids.append(target_id)

    attached_object_rows = store._rows_for_values(
        "tweet_object", "tweet_id", attached_ids, columns=_OBJECT_COLUMNS
    )
    attached_objects = {
        str(row["tweet_id"]): row for row in attached_object_rows if row.get("tweet_id")
    }
    media_rows = store._rows_for_values(
        "media",
        "tweet_id",
        attached_ids,
        columns=[
            "tweet_id",
            "media_type",
            "media_url",
            "thumbnail_url",
            "width",
            "height",
            "duration_millis",
            "local_path",
            "thumbnail_local_path",
        ],
    )
    media_by_id: dict[str, list[dict[str, Any]]] = {}
    for row in media_rows:
        if row.get("tweet_id"):
            media_by_id.setdefault(str(row["tweet_id"]), []).append(_media_payload(row))
    tag_rows = store._rows_for_values(
        "media_tag", "tweet_id", attached_ids, columns=["tweet_id", "raw_json"]
    )
    tags_by_id: dict[str, dict[str, Any]] = {}
    for row in tag_rows:
        payload = _json_dict(row.get("raw_json"))
        if row.get("tweet_id") and payload is not None:
            tags_by_id[str(row["tweet_id"])] = payload

    for tweet in tweets:
        tweet_id = str(tweet.get("tweet_id") or "")
        object_row = objects_by_id.get(tweet_id)
        relation_only = bool(tweet.pop("_relation_only", False))
        tweet["availability"] = availability_for_tweet(
            tweet, object_row, relation_only=relation_only
        )
        if tweet["availability"]["placeholder"]:
            tweet["media"] = []
            tweet["qt_media"] = []
            tweet["raw_json"] = None
        elif not tweet.get("media"):
            tweet["media"] = _raw_media_payloads(_json_dict(tweet.get("raw_json")))

        for relation_type, field_name in (
            ("quote_of", "quoted_tweet"),
            ("retweet_of", "retweeted_tweet"),
        ):
            spec = attached_specs.get((tweet_id, relation_type))
            if spec is None or tweet["availability"]["placeholder"]:
                tweet[field_name] = None
                continue
            target_id, embedded = spec
            target_object = attached_objects.get(target_id or "")
            embedded_tweet = (
                _normalized_embedded_tweet(embedded, target_id) if embedded is not None else None
            )
            if _use_canonical_object_content(target_object, embedded_tweet or {}):
                attached = _normalized_object_tweet(target_object)
            elif embedded_tweet is not None:
                attached = embedded_tweet
            elif target_object is not None:
                attached = _normalized_object_tweet(target_object)
            elif target_id:
                attached = missing_web_tweet(target_id)
            elif relation_type == "quote_of":
                attached = missing_web_tweet("")
            else:
                attached = None
            if attached is None:
                tweet[field_name] = None
                continue
            attached["availability"] = availability_for_tweet(
                attached,
                target_object,
                relation_only=target_object is None and embedded is None,
            )
            if attached["availability"]["placeholder"]:
                attached["raw_json"] = None
                attached["media"] = []
            elif target_id and media_by_id.get(target_id):
                attached["media"] = media_by_id[target_id][:10]
            if target_id:
                attached["media_tags"] = tags_by_id.get(target_id)
            tweet[field_name] = attached

        quote = tweet.get("quoted_tweet")
        tweet["qt_media"] = quote.get("media", []) if isinstance(quote, dict) else []
        tweet["qt_media_tags"] = quote.get("media_tags") if isinstance(quote, dict) else None
    return tweets
