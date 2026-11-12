"""Connection-wide pure SQL callbacks for random search, export order and text identity."""

from __future__ import annotations

import sqlite3
from typing import Any

from .common import parse_created_at, tag_key


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


def register_identity_functions(conn: sqlite3.Connection) -> None:
    # SQLite's LOWER only folds ASCII, so "Straße" and "STRASSE" or "École" and "école" would stay
    # different in SQL while Python treats them as one.
    def tag_key_or_null(value: Any) -> str | None:
        return tag_key(value) if isinstance(value, str) else None

    # Fact lists keep their char(31) delimiters, which stripping would remove from the ends.
    def casefold_or_null(value: Any) -> str | None:
        return value.casefold() if isinstance(value, str) else None

    conn.create_function("tweetnook_tag_key", 1, tag_key_or_null, deterministic=True)
    conn.create_function("tweetnook_casefold", 1, casefold_or_null, deterministic=True)
