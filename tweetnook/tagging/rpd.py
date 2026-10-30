"""Persistent Gemini requests-per-day quota accounting."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from tweetnook.storage import ArchiveStore

_PACIFIC = ZoneInfo("America/Los_Angeles")
_STATE_VERSION = 1
_METADATA_KEY_PREFIX = "gemini_rpd"


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


def get_rpd_status(
    store: ArchiveStore,
    *,
    model: str,
    limit: int,
    now: datetime | None = None,
) -> RpdStatus:
    """Read a model's current quota status without changing persistent state."""
    _validate_limit(limit)
    normalized_model = normalize_model_name(model)
    quota_day, reset_at = _quota_window(now or datetime.now(UTC))
    used = _used_for_current_day(
        store.metadata_value(f"{_METADATA_KEY_PREFIX}:{normalized_model}"),
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
    store: ArchiveStore,
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

    def decide(raw_value: str | None) -> tuple[str | None, RpdStatus]:
        used = _used_for_current_day(raw_value, quota_day=quota_day, model=normalized_model)
        allowed = used < limit
        if allowed:
            used += 1
        value = (
            json.dumps(
                {
                    "day": quota_day.isoformat(),
                    "model": normalized_model,
                    "used": used,
                    "version": _STATE_VERSION,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            if allowed
            else None
        )
        return value, _status(
            allowed=allowed,
            used=used,
            limit=limit,
            quota_day=quota_day,
            reset_at=reset_at,
        )

    return store.reserve_metadata_counter(
        f"{_METADATA_KEY_PREFIX}:{normalized_model}",
        decide,
        updated_at=effective_now.astimezone(UTC).isoformat(),
    )
