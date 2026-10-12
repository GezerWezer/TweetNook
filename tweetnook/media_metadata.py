"""Small helpers for metadata presented from captured tweet media."""

from __future__ import annotations

import math
from collections.abc import Mapping
from numbers import Real
from typing import Any


def raw_tweet_media(raw: Any) -> list[Any]:
    """Return captured legacy media objects from a raw tweet payload."""
    if not isinstance(raw, Mapping):
        return []
    legacy = raw.get("legacy")
    if not isinstance(legacy, Mapping):
        return []
    extended = legacy.get("extended_entities")
    entities = legacy.get("entities")
    media = (extended.get("media") if isinstance(extended, Mapping) else None) or (
        entities.get("media") if isinstance(entities, Mapping) else None
    )
    if not isinstance(media, list):
        return []
    return media


def validated_aspect_ratio(value: Any) -> list[int | float] | None:
    """Keep only a positive finite two-number aspect ratio."""
    if not isinstance(value, list | tuple) or len(value) != 2:
        return None
    ratio: list[int | float] = []
    for component in value:
        if isinstance(component, bool) or not isinstance(component, Real):
            return None
        if not math.isfinite(component) or component <= 0:
            return None
        ratio.append(component)
    return ratio


def aspect_ratio_for_media(raw: Any, position: Any, media_type: Any) -> list[int | float] | None:
    """Read validated playback geometry for one indexed video or GIF."""
    if media_type not in {"video", "animated_gif"}:
        return None
    if isinstance(position, str) and position.isdecimal():
        position = int(position)
    if isinstance(position, bool) or not isinstance(position, int) or position < 0:
        return None
    media = raw_tweet_media(raw)
    if position >= len(media):
        return None
    item = media[position]
    if not isinstance(item, Mapping):
        return None
    video_info = item.get("video_info")
    if not isinstance(video_info, Mapping):
        return None
    return validated_aspect_ratio(video_info.get("aspect_ratio"))
