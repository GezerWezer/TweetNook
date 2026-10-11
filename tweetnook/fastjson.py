"""Fast JSON parsing and serialization for read paths, with standard-library fallbacks."""

from __future__ import annotations

import json
from typing import Any

import orjson


def loads(value: str | bytes) -> Any:
    """Parse JSON with orjson, keeping json.loads semantics for what orjson rejects.

    orjson refuses NaN/Infinity literals and integers beyond 64 bits, which the
    standard library accepts; those documents are retried with json.loads. Invalid
    JSON still raises json.JSONDecodeError (orjson's error subclasses it), and a
    non-string value still raises TypeError.
    """
    try:
        return orjson.loads(value)
    except orjson.JSONDecodeError:
        return json.loads(value)


def dumps(value: Any) -> bytes:
    """Serialize to compact UTF-8 JSON, as Starlette's JSONResponse renders it.

    Values orjson cannot encode (integers beyond 64 bits, non-string keys) fall
    back to the standard library. Unlike JSONResponse, NaN and infinities become
    null instead of failing the response.
    """
    try:
        return orjson.dumps(value)
    except (orjson.JSONEncodeError, TypeError):
        text = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        return text.encode("utf-8")
