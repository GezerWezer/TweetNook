"""Pure adaptation of embedded and legacy tweet payloads for normalized presentation."""

from __future__ import annotations

from typing import Any

from .extractor import extract_author_fields, extract_canonical_text
from .media_metadata import aspect_ratio_for_media


def legacy_target_id(raw: dict[str, Any] | None, relation_type: str) -> str | None:
    if not raw:
        return None
    legacy = raw.get("legacy") or {}
    key = "quoted_status_id_str" if relation_type == "quote_of" else "retweeted_status_id_str"
    value = legacy.get(key)
    return str(value) if value else None


def raw_media_payloads(raw: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not raw:
        return []
    legacy = raw.get("legacy") or {}
    extended = legacy.get("extended_entities") or {}
    entities = legacy.get("entities") or {}
    raw_media = extended.get("media") or entities.get("media") or []
    if not isinstance(raw_media, list):
        return []

    media: list[dict[str, Any]] = []
    for position, item in enumerate(raw_media):
        if not isinstance(item, dict):
            continue
        original = item.get("original_info") or {}
        large = (item.get("sizes") or {}).get("large") or {}
        video_info = item.get("video_info") or {}
        payload = {
            "type": item.get("type"),
            "width": original.get("width") or large.get("w"),
            "height": original.get("height") or large.get("h"),
            "duration_millis": video_info.get("duration_millis"),
            "download": {
                "local_path": None,
                "thumbnail_local_path": None,
            },
        }
        aspect_ratio = aspect_ratio_for_media(raw, position, item.get("type"))
        if aspect_ratio is not None:
            payload["aspect_ratio"] = aspect_ratio
        media.append(payload)
    return media


def normalized_embedded_tweet(raw: dict[str, Any], tweet_id: str | None) -> dict[str, Any]:
    author_id, username, display_name = extract_author_fields(raw)
    legacy = raw.get("legacy") or {}
    return {
        "tweet_id": tweet_id or raw.get("rest_id"),
        "text": extract_canonical_text(raw),
        "author": {"id": author_id, "username": username, "display_name": display_name},
        "created_at": legacy.get("created_at"),
        "synced_at": None,
        "raw_json": raw,
        "media": raw_media_payloads(raw),
        "media_tags": None,
    }
