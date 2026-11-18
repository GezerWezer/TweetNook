"""Helpers for parsing `smart:` prefix queries and caching the web encoder."""

from __future__ import annotations

import re
import threading
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any

_ENCODER_LOCK = threading.Lock()
_ENCODER_CACHE: tuple[tuple[str, str], Any] | None = None
_TEXT_VEC_CACHE: OrderedDict[tuple[int, str, str, str], bytes] = OrderedDict()
_TEXT_VEC_CACHE_MAX = 32
_SMART_TOKEN_RE = re.compile(r'(?i)(?:^|\s)smart:(?:"([^"]*)"|([^\s]+))')
_OTHER_FILTER_RE = re.compile(r'(?:^|\s)-?[a-z_]+:(?:"[^"]*"|\S+)', re.IGNORECASE)


def split_smart_query(query: str | None) -> tuple[str | None, str | None]:
    """Split *query* into `(smart_text, remaining_filter_query)`.

    Supports both leading `smart:sunset over ocean` (all words as semantic text when no
    other `key:value` filters appear) and mixed queries like `until:2020-12-31 smart:cat`
    or `smart:"red car" from:alice`.
    """
    if not query or not query.strip():
        return None, None
    stripped = query.strip()
    if stripped[:6].casefold() == "smart:":
        rest = stripped[6:].strip()
        if not _OTHER_FILTER_RE.search(rest):
            if len(rest) >= 2 and rest[0] == '"' and rest[-1] == '"':
                rest = rest[1:-1].strip()
            return rest, None

    smart_parts: list[str] = []

    def _collect(match: re.Match[str]) -> str:
        val = match.group(1) if match.group(1) is not None else (match.group(2) or "")
        if val.strip():
            smart_parts.append(val.strip())
        return " "

    remainder = _SMART_TOKEN_RE.sub(_collect, stripped)
    # Also handle bare `smart:` with no value so it triggers Smart Search rather than FTS error.
    if not smart_parts and re.search(r"(?i)(?:^|\s)smart:(?:\s|$)", stripped):
        remainder = re.sub(r"(?i)(?:^|\s)smart:(?=\s|$)", " ", stripped)
        return "", " ".join(remainder.split()) or None
    if not smart_parts:
        return None, stripped
    cleaned_remainder = " ".join(remainder.split()) or None
    return " ".join(smart_parts), cleaned_remainder


def parse_smart_query(query: str | None) -> str | None:
    """Return the semantic image query when *query* uses the `smart:` prefix."""
    smart_text, _ = split_smart_query(query)
    return smart_text


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


def encode_smart_query(
    model_id: str,
    models_cache_dir: Path,
    query: str,
    *,
    encoder_fn: Callable[[str, Path], Any] = get_cached_encoder,
) -> bytes:
    """Encode *query* to normalized float32 bytes, caching the last 32 text vectors."""
    key = (id(encoder_fn), model_id, str(models_cache_dir), query)
    with _ENCODER_LOCK:
        cached = _TEXT_VEC_CACHE.get(key)
        if cached is not None:
            _TEXT_VEC_CACHE.move_to_end(key)
            return cached
    encoder = encoder_fn(model_id, models_cache_dir)
    vec_bytes = encoder.encode_text(query).tobytes()
    with _ENCODER_LOCK:
        _TEXT_VEC_CACHE[key] = vec_bytes
        _TEXT_VEC_CACHE.move_to_end(key)
        while len(_TEXT_VEC_CACHE) > _TEXT_VEC_CACHE_MAX:
            _TEXT_VEC_CACHE.popitem(last=False)
    return vec_bytes
