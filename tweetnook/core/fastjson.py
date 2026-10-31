"""Fast JSON parsing and serialization for read paths, with standard-library fallbacks."""

from __future__ import annotations

import json
import math
import re
from typing import Any

import orjson

# A number token of 19 digits or more, the shortest that can exceed 64 bits. Bounding it by JSON
# punctuation leaves out the digit runs in ID strings and status URLs, which nearly every saved
# post has. Text inside a string can still look like this; that only costs a slower parse.
_WIDE_INTEGER = r"(?:^|[:,\[\s])-?\d{19,}(?=[,\]}\s]|$)"
_WIDE_INTEGER_TEXT = re.compile(_WIDE_INTEGER)
_WIDE_INTEGER_BYTES = re.compile(_WIDE_INTEGER.encode())


def _may_hold_wide_integer(value: Any) -> bool:
    if isinstance(value, str):
        return _WIDE_INTEGER_TEXT.search(value) is not None
    if isinstance(value, bytes | bytearray | memoryview):
        return _WIDE_INTEGER_BYTES.search(value) is not None
    return False


def loads(value: str | bytes) -> Any:
    """Parse JSON with orjson, keeping json.loads semantics for what orjson rejects or rounds.

    orjson refuses NaN/Infinity literals, which the standard library accepts, and it returns an
    integer beyond 64 bits as a rounded float rather than refusing it. A saved document decoded
    here can be written back, so both kinds of document go to json.loads. Invalid JSON still
    raises json.JSONDecodeError (orjson's error subclasses it), and a non-string value still
    raises TypeError.
    """
    if _may_hold_wide_integer(value):
        return json.loads(value)
    try:
        return orjson.loads(value)
    except orjson.JSONDecodeError:
        return json.loads(value)


def _finite_values(value: Any, active: set[int]) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if not isinstance(value, dict | list | tuple):
        return value
    identity = id(value)
    if identity in active:
        raise ValueError("Circular reference detected")
    active.add(identity)
    try:
        if isinstance(value, dict):
            return {key: _finite_values(item, active) for key, item in value.items()}
        return [_finite_values(item, active) for item in value]
    finally:
        active.remove(identity)


def dumps(value: Any) -> bytes:
    """Serialize to compact UTF-8 JSON, as Starlette's JSONResponse renders it.

    Values orjson cannot encode (integers beyond 64 bits, non-string keys) fall
    back to the standard library. Unlike JSONResponse, NaN and infinities become
    null instead of failing the response.
    """
    try:
        return orjson.dumps(value)
    except (orjson.JSONEncodeError, TypeError):
        # An unrelated wide integer or key can select this path for a payload
        # whose nonfinite values the fast encoder would otherwise turn into null.
        text = json.dumps(
            _finite_values(value, set()),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        return text.encode("utf-8")
