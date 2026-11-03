"""Fast JSON parsing and serialization for read paths, with standard-library fallbacks."""

from __future__ import annotations

import json
import math
from typing import Any

import orjson

# orjson parses integers within the signed or unsigned 64-bit range exactly. Anything wider has at
# least 20 digits, or 19 after a minus sign. Mapping every ASCII digit to "0" turns that test into
# two substring searches, which run at C speed; a regular expression for digit runs costs several
# times the parse itself on a saved post. Digit runs inside strings can match too, which only
# costs a slower parse; the 19-digit post IDs in ID strings and status URLs don't.
_DIGITS_AS_ZERO = bytes.maketrans(b"0123456789", b"0000000000")
_WIDE_POSITIVE = b"0" * 20
_WIDE_NEGATIVE = b"-" + b"0" * 19


def _may_hold_wide_integer(value: Any) -> bool:
    if isinstance(value, str):
        value = value.encode("utf-8", "surrogatepass")
    elif isinstance(value, memoryview):
        value = value.tobytes()
    elif not isinstance(value, bytes | bytearray):
        return False
    digits = value.translate(_DIGITS_AS_ZERO)
    return _WIDE_POSITIVE in digits or _WIDE_NEGATIVE in digits


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
