"""Persistent Gemini requests-per-day quota accounting."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

_PACIFIC = ZoneInfo("America/Los_Angeles")
_STATE_VERSION = 1
_METADATA_KEY_PREFIX = "gemini_rpd"


class _ArchiveStore(Protocol):
    conn: sqlite3.Connection


@dataclass(frozen=True, slots=True)
class RpdStatus:
    """Result of attempting to reserve one Gemini request."""

    allowed: bool
    used: int
    limit: int
    remaining: int
    day: date
    reset_at: datetime


def normalize_model_name(model: str) -> str:
    """Return a stable Gemini model name for quota accounting."""
    normalized = model.strip().casefold()
    if normalized.startswith("models/"):
        normalized = normalized.removeprefix("models/")
    if not normalized:
        raise ValueError("Gemini model name must not be empty")
    return normalized


def _quota_window(now: datetime) -> tuple[date, datetime]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("RPD timestamps must be timezone-aware")
    pacific_now = now.astimezone(_PACIFIC)
    quota_day = pacific_now.date()
    reset_at = datetime.combine(quota_day + timedelta(days=1), time.min, tzinfo=_PACIFIC)
    return quota_day, reset_at


def _used_for_current_day(raw_value: object, *, quota_day: date, model: str) -> int:
    if not isinstance(raw_value, str):
        return 0
    try:
        state = json.loads(raw_value)
    except (json.JSONDecodeError, TypeError):
        return 0
    if not isinstance(state, dict):
        return 0
    used = state.get("used")
    if (
        state.get("version") != _STATE_VERSION
        or state.get("day") != quota_day.isoformat()
        or state.get("model") != model
        or isinstance(used, bool)
        or not isinstance(used, int)
        or used < 0
    ):
        return 0
    return used


def _status(
    *,
    allowed: bool,
    used: int,
    limit: int,
    quota_day: date,
    reset_at: datetime,
) -> RpdStatus:
    return RpdStatus(
        allowed=allowed,
        used=used,
        limit=limit,
        remaining=max(limit - used, 0),
        day=quota_day,
        reset_at=reset_at,
    )


def _validate_limit(limit: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("RPD limit must be a positive integer")


def _state_location(model: str) -> tuple[str, str]:
    metadata_key = f"{_METADATA_KEY_PREFIX}:{model}"
    return f"metadata:{metadata_key}", metadata_key


def get_rpd_status(
    store: _ArchiveStore,
    *,
    model: str,
    limit: int,
    now: datetime | None = None,
) -> RpdStatus:
    """Read a model's current quota status without changing persistent state."""
    _validate_limit(limit)
    normalized_model = normalize_model_name(model)
    quota_day, reset_at = _quota_window(now or datetime.now(UTC))
    row_key, _metadata_key = _state_location(normalized_model)
    row = store.conn.execute(
        "SELECT value FROM archive WHERE row_key = ?",
        (row_key,),
    ).fetchone()
    used = _used_for_current_day(
        row[0] if row is not None else None,
        quota_day=quota_day,
        model=normalized_model,
    )
    return _status(
        allowed=used < limit,
        used=used,
        limit=limit,
        quota_day=quota_day,
        reset_at=reset_at,
    )


def reserve_rpd_request(
    store: _ArchiveStore,
    *,
    model: str,
    limit: int,
    now: datetime | None = None,
) -> RpdStatus:
    """Atomically reserve one request from a model's current Pacific quota day.

    Reservations are committed before the caller dispatches the request and are never
    refunded. This deliberately favors a conservative overcount if the process exits
    between reserving and sending the request.
    """
    _validate_limit(limit)
    normalized_model = normalize_model_name(model)
    effective_now = now or datetime.now(UTC)
    quota_day, reset_at = _quota_window(effective_now)
    row_key, metadata_key = _state_location(normalized_model)
    conn = store.conn

    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT value FROM archive WHERE row_key = ?",
            (row_key,),
        ).fetchone()
        used = _used_for_current_day(
            row[0] if row is not None else None,
            quota_day=quota_day,
            model=normalized_model,
        )
        if used >= limit:
            conn.commit()
            return _status(
                allowed=False,
                used=used,
                limit=limit,
                quota_day=quota_day,
                reset_at=reset_at,
            )

        used += 1
        value = json.dumps(
            {
                "day": quota_day.isoformat(),
                "model": normalized_model,
                "used": used,
                "version": _STATE_VERSION,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        conn.execute(
            """
            INSERT INTO archive (row_key, record_type, key, value, updated_at)
            VALUES (?, 'metadata', ?, ?, ?)
            ON CONFLICT(row_key) DO UPDATE SET
                record_type = excluded.record_type,
                key = excluded.key,
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (
                row_key,
                metadata_key,
                value,
                effective_now.astimezone(UTC).isoformat(),
            ),
        )
        conn.commit()
        return _status(
            allowed=True,
            used=used,
            limit=limit,
            quota_day=quota_day,
            reset_at=reset_at,
        )
    except BaseException:
        conn.rollback()
        raise
