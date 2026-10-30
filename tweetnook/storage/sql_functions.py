"""Connection-wide pure SQL callbacks for random search and legacy export order."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from .common import parse_created_at


def register_export_functions(conn: sqlite3.Connection) -> None:
    def legacy_timestamp(value):
        parsed = parse_created_at(value)
        return parsed.timestamp() if parsed is not None else None

    def export_integer_key(value):
        # Keep Python int() semantics (invalid values become zero), including
        # indices beyond SQLite's signed integer range. Length-prefixed digits
        # sort lexically as integers; complement negative keys to reverse them.
        try:
            number = int(value or 0)
        except (TypeError, ValueError):
            number = 0
        digits = str(abs(number))
        key = f"{len(digits):020d}{digits}"
        if number < 0:
            return "0" + key.translate(str.maketrans("0123456789", "9876543210"))
        return "1" + key

    conn.create_function("tweetnook_export_timestamp", 1, legacy_timestamp, deterministic=True)
    conn.create_function("tweetnook_export_integer", 1, export_integer_key, deterministic=True)


def register_random_function(conn: sqlite3.Connection) -> None:
    def stable_random_key(tweet_id: Any, supplied_seed: Any) -> int:
        digest = hashlib.blake2b(
            f"{int(supplied_seed)}:{tweet_id}".encode(), digest_size=8
        ).digest()
        return int.from_bytes(digest, "big") & ((1 << 63) - 1)

    conn.create_function("tweetnook_random_key", 2, stable_random_key, deterministic=True)
