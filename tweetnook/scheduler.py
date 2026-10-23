"""Built-in interval scheduler owned by the always-on Web service."""

from __future__ import annotations

import calendar
import json
import os
import random
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from threading import Thread
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tweetnook.client.fxtwitter import fx_enabled
from tweetnook.config import AppConfig, CheckConfig, ScheduleConfig, XDGPaths, load_config
from tweetnook.job_supervisor import JobConflictError, JobSupervisor

CHECK_CONFLICT_RETRY_SECONDS = 15 * 60


def _timezone(value: str) -> tzinfo:
    if value == "local":
        try:
            resolved = str(Path("/etc/localtime").resolve())
            marker = "/zoneinfo/"
            if marker in resolved:
                return ZoneInfo(resolved.split(marker, 1)[1])
        except (OSError, ZoneInfoNotFoundError):
            pass
        return datetime.now().astimezone().tzinfo or UTC
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {value}") from exc


def _clock(config: ScheduleConfig) -> tuple[int, int]:
    hour, minute = config.time.split(":", 1)
    return int(hour), int(minute)


def _next_nominal_run(config: ScheduleConfig, after: datetime) -> datetime:
    zone = _timezone(config.timezone)
    local = after.astimezone(zone)
    if config.cadence == "hours":
        return after + timedelta(hours=config.every_hours)

    hour, minute = _clock(config)
    if config.cadence == "daily":
        candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= local:
            candidate += timedelta(days=1)
        return candidate.astimezone(UTC)

    if config.cadence == "weekly":
        days = (config.weekday - local.weekday()) % 7
        candidate = (local + timedelta(days=days)).replace(
            hour=hour,
            minute=minute,
            second=0,
            microsecond=0,
        )
        if candidate <= local:
            candidate += timedelta(days=7)
        return candidate.astimezone(UTC)

    year, month = local.year, local.month
    while True:
        day = min(config.day_of_month, calendar.monthrange(year, month)[1])
        candidate = datetime(year, month, day, hour, minute, tzinfo=zone)
        if candidate > local:
            return candidate.astimezone(UTC)
        month += 1
        if month == 13:
            year += 1
            month = 1


def next_run_after(
    config: ScheduleConfig,
    after: datetime,
    *,
    random_offset: float | None = None,
    occurrence_consumed: bool = False,
) -> datetime:
    if config.cadence == "hours" or not config.randomize_time:
        return _next_nominal_run(config, after)
    if random_offset is not None and random_offset < 0:
        raise ValueError("Random added delay must not be negative")

    if occurrence_consumed:
        nominal = _next_nominal_run(config, after)
        offset = (
            random.uniform(
                config.random_offset_min_hours,
                config.random_offset_max_hours,
            )
            if random_offset is None
            else random_offset
        )
        return nominal + timedelta(hours=offset)

    if random_offset is None:
        nominal = _next_nominal_run(
            config,
            after - timedelta(hours=config.random_offset_max_hours),
        )
        seconds_after_nominal = (after - nominal).total_seconds()
        minimum_future_offset = seconds_after_nominal / 3600 + 1e-9
        lower_bound = max(config.random_offset_min_hours, minimum_future_offset)
        if lower_bound > config.random_offset_max_hours:
            nominal = _next_nominal_run(config, nominal)
            lower_bound = config.random_offset_min_hours
        offset = random.uniform(lower_bound, config.random_offset_max_hours)
        return nominal + timedelta(hours=offset)

    nominal_after = after
    while True:
        nominal = _next_nominal_run(config, nominal_after)
        candidate = nominal + timedelta(hours=random_offset)
        if candidate > after:
            return candidate
        nominal_after = nominal


_WEEKDAY_PLURALS = (
    "Mondays",
    "Tuesdays",
    "Wednesdays",
    "Thursdays",
    "Fridays",
    "Saturdays",
    "Sundays",
)


def _ordinal(day: int) -> str:
    suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def schedule_label(config: ScheduleConfig) -> str:
    """A short label with the cadence and time, e.g. "Daily at 3:00 AM"; the random delay
    is left to the schedule settings."""

    if config.cadence == "hours":
        return "Every hour" if config.every_hours == 1 else f"Every {config.every_hours} hours"
    hour, minute = (int(part) for part in config.time.split(":"))
    clock = f"{hour % 12 or 12}:{minute:02d} {'PM' if hour >= 12 else 'AM'}"
    if config.cadence == "daily":
        return f"Daily at {clock}"
    if config.cadence == "weekly":
        return f"{_WEEKDAY_PLURALS[config.weekday]} at {clock}"
    return f"Monthly on the {_ordinal(config.day_of_month)} at {clock}"


def schedule_description(config: ScheduleConfig) -> str:
    if config.cadence == "hours":
        suffix = "hour" if config.every_hours == 1 else "hours"
        return f"Every {config.every_hours} {suffix}"
    if config.cadence == "daily":
        description = "Every day"
    elif config.cadence == "weekly":
        description = "Every week"
    else:
        description = "Every month"
    if config.randomize_time:
        description += " · randomized delay"
    return description


def _relative_time(timestamp: float, now: float | None = None) -> str:
    seconds = max(round(timestamp - (time.time() if now is None else now)), 0)
    if seconds < 60:
        return "in less than a minute"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"in {minutes} minute{'s' if minutes != 1 else ''}"
    hours = round(seconds / 3600)
    if hours < 48:
        return f"in {hours} hour{'s' if hours != 1 else ''}"
    days = round(seconds / 86400)
    return f"in {days} day{'s' if days != 1 else ''}"


class ScheduleManager:
    def __init__(
        self,
        paths: XDGPaths,
        config: AppConfig,
        supervisor: JobSupervisor,
        *,
        on_job_conflict: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.paths = paths
        self.config = config
        self.supervisor = supervisor
        self.on_job_conflict = on_job_conflict
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Thread | None = None
        self._state = self._load_state()
        self._ensure_next_run(reset=False)
        self._ensure_next_check(reset=False)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(
            target=self._loop,
            daemon=True,
            name="tweetnook-scheduler",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._thread = None

    def reload(self, *, reset: bool = True) -> None:
        config, _ = load_config()
        with self._lock:
            self.config = config
            self.supervisor.config = config
            self._ensure_next_run(reset=reset)
            self._ensure_next_check(reset=reset)

    def status(self) -> dict[str, Any]:
        with self._lock:
            schedule = self.config.schedule
            next_at = self._state.get("next_run_at") if schedule.enabled else None
            if next_at is None:
                return {
                    "configured": False,
                    "enabled": schedule.enabled,
                    "relative": "Not configured" if not schedule.enabled else "Calculating",
                    "date": "Set up scheduling in Settings",
                    "next_run_at": None,
                    "description": schedule_description(schedule),
                    "label": schedule_label(schedule),
                    "config": schedule.model_dump(),
                }
            zone = _timezone(schedule.timezone)
            next_dt = datetime.fromtimestamp(float(next_at), UTC).astimezone(zone)
            display_date = (
                next_dt.strftime("%a, %b ")
                + str(next_dt.day)
                + next_dt.strftime(" at %I:%M %p").replace(" at 0", " at ")
            )
            return {
                "configured": True,
                "enabled": True,
                "relative": _relative_time(float(next_at)),
                "date": display_date,
                "next_run_at": next_at,
                "description": schedule_description(schedule),
                "label": schedule_label(schedule),
                "last_run_at": self._state.get("last_run_at"),
                "last_result": self._state.get("last_result"),
                "config": schedule.model_dump(),
            }

    def _loop(self) -> None:
        while not self._stop.wait(5):
            try:
                self.tick()
            except Exception:
                # Scheduling must never bring down the archive Web service.
                continue

    def tick(self, *, now: float | None = None) -> None:
        timestamp = time.time() if now is None else now
        with self._lock:
            self._tick_sync(timestamp)
            self._tick_check(timestamp)

    def _tick_sync(self, timestamp: float) -> None:
        schedule = self.config.schedule
        if not schedule.enabled:
            return
        next_at = self._state.get("next_run_at")
        if next_at is None:
            self._ensure_next_run(reset=True, now=timestamp)
            return
        if float(next_at) > timestamp:
            return
        try:
            result = self.supervisor.start(
                kind="sync",
                cli_args=["sync"],
                origin="schedule",
                title="tweetnook sync",
            )
            self._state["last_result"] = "started"
            self._state["last_run_id"] = result["run_id"]
        except JobConflictError:
            if self.supervisor.active_kind == "check":
                # The check runs at most once a day, so a sync due during it waits
                # for it instead of losing its slot.
                self._state["last_result"] = "deferred: waiting for the availability check"
                self._state["next_run_at"] = timestamp + CHECK_CONFLICT_RETRY_SECONDS
                self._save_state()
                return
            self._state["last_result"] = "skipped: another command was running"
            if self.on_job_conflict is not None:
                context = {
                    "scheduled_at": float(next_at),
                    "active_run_id": self.supervisor.active_run_id,
                    "active_kind": self.supervisor.active_kind,
                }
                try:
                    self.on_job_conflict(context)
                except Exception:
                    # A notification failure must not interrupt schedule advancement.
                    pass
        self._state["last_run_at"] = timestamp
        self._state["next_run_at"] = next_run_after(
            schedule,
            datetime.fromtimestamp(timestamp, UTC),
            occurrence_consumed=True,
        ).timestamp()
        self._state["fingerprint"] = self._fingerprint(schedule)
        self._save_state()

    def check_status(self) -> dict[str, Any]:
        """The check's schedule for the Web jobs list."""

        with self._lock:
            next_at = self._state.get("check_next_run_at") if self._check_on() else None
            last_result = str(self._state.get("check_last_result") or "")
            return {
                "on": self._check_on(),
                "next_run_at": next_at,
                # A due check that found another command running retries every 15 minutes.
                "deferred": bool(next_at) and last_result.startswith("deferred"),
                "last_run_at": self._state.get("check_last_run_at"),
            }

    def sync_deferred(self) -> bool:
        """Whether a due scheduled sync is waiting for the availability check."""

        with self._lock:
            return str(self._state.get("last_result") or "").startswith(
                "deferred: waiting for the availability check"
            )

    def skip_deferred_check(self, *, now: float | None = None) -> bool:
        """Drop the deferred check; the next one runs at its usual time."""

        with self._lock:
            if not self.check_status()["deferred"]:
                return False
            timestamp = time.time() if now is None else now
            self._state["check_next_run_at"] = next_run_after(
                self._check_schedule(self.config.check),
                datetime.fromtimestamp(timestamp, UTC),
            ).timestamp()
            self._state["check_last_result"] = "skipped: removed from the queue"
            self._save_state()
            return True

    def _check_on(self) -> bool:
        # The check is FxTwitter lookups; with FxTwitter off, sync owns the retries.
        return self.config.check.enabled and fx_enabled(self.config)

    def _check_schedule(self, check: CheckConfig) -> ScheduleConfig:
        # Checks have their own cadence/time, in the sync schedule's time zone.
        return ScheduleConfig(
            cadence=check.cadence,
            weekday=check.weekday,
            day_of_month=check.day_of_month,
            time=check.time,
            randomize_time=False,
            timezone=self.config.schedule.timezone,
        )

    def _check_fingerprint(self) -> str:
        return json.dumps(
            {
                "check": self.config.check.model_dump(),
                "fxtwitter": fx_enabled(self.config),
                "timezone": self.config.schedule.timezone,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    def _ensure_next_check(self, *, reset: bool, now: float | None = None) -> None:
        check = self.config.check
        fingerprint = self._check_fingerprint()
        if not self._check_on():
            self._state["check_next_run_at"] = None
            self._state["check_fingerprint"] = fingerprint
            self._save_state()
            return
        if (
            reset
            or self._state.get("check_fingerprint") != fingerprint
            or not self._state.get("check_next_run_at")
        ):
            timestamp = time.time() if now is None else now
            self._state["check_next_run_at"] = next_run_after(
                self._check_schedule(check),
                datetime.fromtimestamp(timestamp, UTC),
            ).timestamp()
            self._state["check_fingerprint"] = fingerprint
            self._save_state()

    def _tick_check(self, timestamp: float) -> None:
        check = self.config.check
        if not self._check_on():
            return
        next_at = self._state.get("check_next_run_at")
        if next_at is None:
            self._ensure_next_check(reset=True, now=timestamp)
            return
        if float(next_at) > timestamp:
            return
        try:
            result = self.supervisor.start(
                kind="check",
                cli_args=["check"],
                origin="schedule",
                title="tweetnook check",
            )
        except JobConflictError:
            # Unlike a missed sync, a due check waits for the running command.
            self._state["check_last_result"] = "deferred: another command was running"
            self._state["check_next_run_at"] = timestamp + CHECK_CONFLICT_RETRY_SECONDS
        else:
            self._state["check_last_result"] = "started"
            self._state["check_last_run_id"] = result["run_id"]
            self._state["check_last_run_at"] = timestamp
            self._state["check_next_run_at"] = next_run_after(
                self._check_schedule(check),
                datetime.fromtimestamp(timestamp, UTC),
            ).timestamp()
        self._state["check_fingerprint"] = self._check_fingerprint()
        self._save_state()

    def _ensure_next_run(self, *, reset: bool, now: float | None = None) -> None:
        schedule = self.config.schedule
        fingerprint = self._fingerprint(schedule)
        if not schedule.enabled:
            self._state["next_run_at"] = None
            self._state["fingerprint"] = fingerprint
            self._save_state()
            return
        if (
            reset
            or self._state.get("fingerprint") != fingerprint
            or not self._state.get("next_run_at")
        ):
            timestamp = time.time() if now is None else now
            self._state["next_run_at"] = next_run_after(
                schedule,
                datetime.fromtimestamp(timestamp, UTC),
            ).timestamp()
            self._state["fingerprint"] = fingerprint
            self._save_state()

    @staticmethod
    def _fingerprint(config: ScheduleConfig) -> str:
        return json.dumps(config.model_dump(), sort_keys=True, separators=(",", ":"))

    def _load_state(self) -> dict[str, Any]:
        try:
            state = json.loads(self.paths.schedule_state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return state if isinstance(state, dict) else {}

    def _save_state(self) -> None:
        path = self.paths.schedule_state_file
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(self._state, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.replace(path)
        except OSError:
            temporary.unlink(missing_ok=True)
