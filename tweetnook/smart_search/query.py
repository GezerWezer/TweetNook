"""Helpers for parsing `smart:` prefix queries and caching the web encoder."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

_ENCODER_LOCK = threading.Lock()
_ENCODER_CACHE: tuple[tuple[str, str], Any] | None = None


def parse_smart_query(query: str | None) -> str | None:
    """Return the semantic image query when *query* uses the `smart:` prefix."""
    if not query:
        return None
    stripped = query.strip()
    if len(stripped) < 6 or stripped[:6].casefold() != "smart:":
        return None
    body = stripped[6:].strip()
    if len(body) >= 2 and body[0] == '"' and body[-1] == '"':
        body = body[1:-1].strip()
    return body


def get_cached_encoder(model_id: str, models_cache_dir: Path) -> Any:
    """Return a cached `ClipOnnxEncoder` for `(model_id, models_cache_dir)`."""
    global _ENCODER_CACHE
    from .encoder import ClipOnnxEncoder

    cache_key = (model_id, str(models_cache_dir))
    with _ENCODER_LOCK:
        if _ENCODER_CACHE is not None and _ENCODER_CACHE[0] == cache_key:
            return _ENCODER_CACHE[1]
        encoder = ClipOnnxEncoder.from_cache(model_id, models_cache_dir)
        _ENCODER_CACHE = (cache_key, encoder)
        return encoder
