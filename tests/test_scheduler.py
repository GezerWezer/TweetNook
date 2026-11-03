from __future__ import annotations

import errno
import logging
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from tweetnook.config import AppConfig, ScheduleConfig, XDGPaths
from tweetnook.jobs.scheduler import (
    ScheduleManager,
    next_run_after,
    schedule_description,
    schedule_label,
)
from tweetnook.jobs.supervisor import JobConflictError


@pytest.mark.parametrize(
    ("config", "start", "expected"),
    [
        (
            ScheduleConfig(cadence="hours", every_hours=6),
            datetime(2026, 8, 12, 10, 30, tzinfo=UTC),
            datetime(2026, 8, 12, 16, 30, tzinfo=UTC),
        ),
        (
            ScheduleConfig(cadence="daily", time="03:00", timezone="UTC", randomize_time=False),
            datetime(2026, 8, 12, 10, 30, tzinfo=UTC),
            datetime(2026, 8, 13, 3, 0, tzinfo=UTC),
        ),
        (
            ScheduleConfig(
                cadence="weekly",
                weekday=0,
                time="09:15",
                timezone="UTC",
                randomize_time=False,
            ),
            datetime(2026, 8, 12, 10, 30, tzinfo=UTC),
            datetime(2026, 8, 17, 9, 15, tzinfo=UTC),
        ),
        (
            ScheduleConfig(
                cadence="monthly",
                day_of_month=31,
                time="04:00",
                timezone="UTC",
                randomize_time=False,
            ),
            datetime(2026, 9, 1, 10, 30, tzinfo=UTC),
            datetime(2026, 9, 30, 4, 0, tzinfo=UTC),
        ),
    ],
)
def test_next_run_supports_all_schedule_cadences(config, start, expected) -> None:
    assert next_run_after(config, start) == expected


def test_schedule_defaults_to_randomized_two_hour_delay() -> None:
    config = ScheduleConfig()

    assert config.randomize_time is True
    assert config.random_offset_min_hours == 0
    assert config.random_offset_max_hours == 2


def test_next_run_applies_random_offset_to_fixed_time_cadences() -> None:
    config = ScheduleConfig(
        cadence="daily",
        time="10:00",
        timezone="UTC",
        randomize_time=True,
        random_offset_min_hours=0,
        random_offset_max_hours=2,
    )

    assert next_run_after(
        config,
        datetime(2026, 8, 12, 8, 0, tzinfo=UTC),
        random_offset=1.5,
    ) == datetime(2026, 8, 12, 11, 30, tzinfo=UTC)
    assert schedule_description(config) == "Every day · randomized delay"


def test_randomized_time_skips_a_window_that_has_already_passed() -> None:
    config = ScheduleConfig(
        cadence="daily",
        time="10:00",
        timezone="UTC",
        randomize_time=True,
        random_offset_min_hours=0,
        random_offset_max_hours=0,
    )

    assert next_run_after(
        config,
        datetime(2026, 8, 12, 10, 30, tzinfo=UTC),
        random_offset=0,
    ) == datetime(2026, 8, 13, 10, 0, tzinfo=UTC)


def test_randomized_time_uses_future_part_of_current_window() -> None:
    config = ScheduleConfig(
        cadence="daily",
        time="10:00",
        timezone="UTC",
        randomize_time=True,
        random_offset_min_hours=0,
        random_offset_max_hours=2,
    )

    with patch("tweetnook.jobs.scheduler.random.uniform", return_value=1.25) as uniform:
        result = next_run_after(config, datetime(2026, 8, 12, 10, 30, tzinfo=UTC))

    assert result == datetime(2026, 8, 12, 11, 15, tzinfo=UTC)
    lower_bound, upper_bound = uniform.call_args.args
    assert 0.5 < lower_bound < 0.500001
    assert upper_bound == 2


@pytest.mark.parametrize(
    ("config", "after", "expected"),
    [
        (
            ScheduleConfig(
                cadence="daily",
                time="03:00",
                timezone="UTC",
                randomize_time=True,
                random_offset_min_hours=0,
                random_offset_max_hours=2,
            ),
            datetime(2026, 8, 12, 3, 16, tzinfo=UTC),
            datetime(2026, 8, 13, 4, 0, tzinfo=UTC),
        ),
        (
            ScheduleConfig(
                cadence="weekly",
                weekday=2,
                time="09:00",
                timezone="UTC",
                randomize_time=True,
                random_offset_min_hours=0,
                random_offset_max_hours=2,
            ),
            datetime(2026, 8, 12, 9, 16, tzinfo=UTC),
            datetime(2026, 8, 19, 10, 0, tzinfo=UTC),
        ),
        (
            ScheduleConfig(
                cadence="monthly",
                day_of_month=31,
                time="04:00",
                timezone="UTC",
                randomize_time=True,
                random_offset_min_hours=0,
                random_offset_max_hours=2,
            ),
            datetime(2026, 8, 31, 4, 16, tzinfo=UTC),
            datetime(2026, 9, 30, 5, 0, tzinfo=UTC),
        ),
    ],
)
def test_consumed_randomized_occurrence_advances_to_next_nominal_run(
    config, after, expected
) -> None:
    with patch("tweetnook.jobs.scheduler.random.uniform", return_value=1.0):
        assert next_run_after(config, after, occurrence_consumed=True) == expected


def test_hourly_schedule_ignores_fixed_time_randomization() -> None:
    config = ScheduleConfig(
        cadence="hours",
        every_hours=4,
        randomize_time=True,
        random_offset_min_hours=0,
        random_offset_max_hours=2,
    )
    start = datetime(2026, 8, 12, 9, 30, tzinfo=UTC)

    assert next_run_after(config, start, random_offset=2) == datetime(
        2026, 8, 12, 13, 30, tzinfo=UTC
    )
    assert schedule_description(config) == "Every 4 hours"


def test_schedule_rejects_inverted_random_offset_window() -> None:
    with pytest.raises(ValueError, match="minimum must not exceed the maximum"):
        ScheduleConfig(
            randomize_time=True,
            random_offset_min_hours=2,
            random_offset_max_hours=1,
        )

    ScheduleConfig(
        cadence="hours",
        randomize_time=True,
        random_offset_min_hours=2,
        random_offset_max_hours=1,
    )
    ScheduleConfig(
        cadence="daily",
        randomize_time=False,
        random_offset_min_hours=2,
        random_offset_max_hours=1,
    )


def test_scheduler_launches_due_sync_and_advances_next_run(tmp_path) -> None:
    paths = XDGPaths(
        config_dir=tmp_path / "config", data_dir=tmp_path, cache_dir=tmp_path / "cache"
    )
    config = AppConfig(
        schedule=ScheduleConfig(enabled=True, cadence="hours", every_hours=2, timezone="UTC")
    )

    class Supervisor:
        def __init__(self):
            self.config = config

        def start(self, **kwargs):
            assert kwargs["cli_args"] == ["sync"]
            assert kwargs["origin"] == "schedule"
            return {"run_id": "scheduled-run"}

    manager = ScheduleManager(
        paths,
        config,
        Supervisor(),
        on_job_conflict=lambda _context: pytest.fail(
            "successful launches must not report a conflict"
        ),
    )
    manager._state["next_run_at"] = 1_000.0
    manager.tick(now=1_001.0)

    assert manager._state["last_result"] == "started"
    assert manager._state["last_run_id"] == "scheduled-run"
    assert manager._state["next_run_at"] == 1_001.0 + 2 * 3600


def test_scheduler_consumes_randomized_daily_occurrence_once(tmp_path) -> None:
    paths = XDGPaths(
        config_dir=tmp_path / "config", data_dir=tmp_path, cache_dir=tmp_path / "cache"
    )
    config = AppConfig(
        schedule=ScheduleConfig(
            enabled=True,
            cadence="daily",
            time="03:00",
            timezone="UTC",
            randomize_time=True,
            random_offset_min_hours=0,
            random_offset_max_hours=2,
        )
    )
    started = []

    class Supervisor:
        def __init__(self):
            self.config = config

        def start(self, **_kwargs):
            started.append(True)
            return {"run_id": "scheduled-run"}

    manager = ScheduleManager(paths, config, Supervisor())
    occurrence = datetime(2026, 8, 12, 3, 16, tzinfo=UTC)
    manager._state["next_run_at"] = occurrence.timestamp()

    with patch("tweetnook.jobs.scheduler.random.uniform", return_value=1.0):
        manager.tick(now=occurrence.timestamp())

    next_run = datetime.fromtimestamp(manager._state["next_run_at"], UTC)
    assert next_run == datetime(2026, 8, 13, 4, 0, tzinfo=UTC)

    manager.tick(now=datetime(2026, 8, 12, 4, 30, tzinfo=UTC).timestamp())
    assert started == [True]


def test_scheduler_resumes_after_an_overdue_randomized_occurrence(tmp_path) -> None:
    paths = XDGPaths(
        config_dir=tmp_path / "config", data_dir=tmp_path, cache_dir=tmp_path / "cache"
    )
    config = AppConfig(
        schedule=ScheduleConfig(
            enabled=True,
            cadence="daily",
            time="03:00",
            timezone="UTC",
            randomize_time=True,
            random_offset_min_hours=0,
            random_offset_max_hours=2,
        )
    )

    class Supervisor:
        def __init__(self):
            self.config = config

        def start(self, **_kwargs):
            return {"run_id": "scheduled-run"}

    manager = ScheduleManager(paths, config, Supervisor())
    manager._state["next_run_at"] = datetime(2026, 8, 10, 3, 16, tzinfo=UTC).timestamp()

    with patch("tweetnook.jobs.scheduler.random.uniform", return_value=1.0):
        manager.tick(now=datetime(2026, 8, 12, 12, 0, tzinfo=UTC).timestamp())

    assert datetime.fromtimestamp(manager._state["next_run_at"], UTC) == datetime(
        2026, 8, 13, 4, 0, tzinfo=UTC
    )


def test_scheduler_records_conflicting_run_as_skipped(tmp_path) -> None:
    paths = XDGPaths(
        config_dir=tmp_path / "config", data_dir=tmp_path, cache_dir=tmp_path / "cache"
    )
    config = AppConfig(schedule=ScheduleConfig(enabled=True, cadence="daily", timezone="UTC"))

    class Supervisor:
        def __init__(self):
            self.config = config
            self.active_run_id = "blocking-import"
            self.active_kind = "import"

        def start(self, **_kwargs):
            raise JobConflictError("busy")

    conflicts = []
    manager = ScheduleManager(
        paths,
        config,
        Supervisor(),
        on_job_conflict=conflicts.append,
    )
    manager._state["next_run_at"] = 1_000.0
    manager.tick(now=1_001.0)

    assert manager._state["last_result"] == "skipped: another command was running"
    assert manager._state["last_run_at"] == 1_001.0
    assert manager._state["next_run_at"] > 1_001.0
    assert conflicts == [
        {
            "scheduled_at": 1_000.0,
            "active_run_id": "blocking-import",
            "active_kind": "import",
        }
    ]


def test_sync_due_during_the_check_waits_for_it_then_runs(tmp_path) -> None:
    paths = XDGPaths(
        config_dir=tmp_path / "config", data_dir=tmp_path, cache_dir=tmp_path / "cache"
    )
    config = AppConfig(schedule=ScheduleConfig(enabled=True, cadence="daily", timezone="UTC"))

    class Supervisor:
        def __init__(self):
            self.config = config
            self.active_run_id = "check-run"
            self.active_kind = "check"

        def start(self, **_kwargs):
            if self.active_kind is not None:
                raise JobConflictError("busy")
            return {"run_id": "scheduled-run"}

    supervisor = Supervisor()
    manager = ScheduleManager(
        paths,
        config,
        supervisor,
        on_job_conflict=lambda _context: pytest.fail("waiting for the check is not a conflict"),
    )
    manager._state["next_run_at"] = 1_000.0
    manager.tick(now=1_001.0)

    assert manager.sync_deferred() is True
    assert manager._state["next_run_at"] == 1_001.0 + 15 * 60

    supervisor.active_kind = None
    manager.tick(now=1_001.0 + 15 * 60)

    assert manager._state["last_run_id"] == "scheduled-run"
    assert manager.sync_deferred() is False


def test_a_new_schedule_replaces_waiting_sync_and_check_retries(tmp_path) -> None:
    paths = XDGPaths(
        config_dir=tmp_path / "config", data_dir=tmp_path, cache_dir=tmp_path / "cache"
    )
    config = AppConfig.model_validate(
        {
            "schedule": {"enabled": True, "cadence": "daily", "timezone": "UTC"},
            "check": {"enabled": True},
            "fxtwitter": {"enabled": True},
        }
    )

    class Supervisor:
        def __init__(self):
            self.config = config
            self.active_run_id = "check-run"
            self.active_kind = "check"

        def start(self, **_kwargs):
            raise JobConflictError("busy")

    manager = ScheduleManager(paths, config, Supervisor())
    manager._state["next_run_at"] = 1_000.0
    manager._state["check_next_run_at"] = 1_000.0
    manager.tick(now=1_001.0)
    assert manager.sync_deferred() is True
    assert manager.check_status()["deferred"] is True

    # Saving the schedule picks fresh run times, so neither retry is pending anymore.
    manager.reload(config=config)

    assert manager.sync_deferred() is False
    assert manager.check_status()["deferred"] is False


def test_scheduler_conflict_consumes_randomized_daily_occurrence(tmp_path) -> None:
    paths = XDGPaths(
        config_dir=tmp_path / "config", data_dir=tmp_path, cache_dir=tmp_path / "cache"
    )
    config = AppConfig(
        schedule=ScheduleConfig(
            enabled=True,
            cadence="daily",
            time="03:00",
            timezone="UTC",
            randomize_time=True,
            random_offset_min_hours=0,
            random_offset_max_hours=2,
        )
    )

    class Supervisor:
        active_run_id = "blocking-import"
        active_kind = "import"

        def __init__(self):
            self.config = config

        def start(self, **_kwargs):
            raise JobConflictError("busy")

    manager = ScheduleManager(paths, config, Supervisor())
    occurrence = datetime(2026, 8, 12, 3, 16, tzinfo=UTC)
    manager._state["next_run_at"] = occurrence.timestamp()

    with patch("tweetnook.jobs.scheduler.random.uniform", return_value=0.5):
        manager.tick(now=occurrence.timestamp())

    assert datetime.fromtimestamp(manager._state["next_run_at"], UTC) == datetime(
        2026, 8, 13, 3, 30, tzinfo=UTC
    )


def test_scheduler_ignores_conflict_notification_failure(tmp_path) -> None:
    paths = XDGPaths(
        config_dir=tmp_path / "config", data_dir=tmp_path, cache_dir=tmp_path / "cache"
    )
    config = AppConfig(schedule=ScheduleConfig(enabled=True, cadence="daily", timezone="UTC"))

    class Supervisor:
        active_run_id = None
        active_kind = None

        def __init__(self):
            self.config = config

        def start(self, **_kwargs):
            raise JobConflictError("busy")

    def fail(_context):
        raise RuntimeError("notice storage unavailable")

    manager = ScheduleManager(paths, config, Supervisor(), on_job_conflict=fail)
    manager._state["next_run_at"] = 1_000.0

    manager.tick(now=1_001.0)

    assert manager._state["last_result"] == "skipped: another command was running"
    assert manager._state["next_run_at"] > 1_001.0


@pytest.mark.parametrize(
    ("kwargs", "label"),
    [
        ({"cadence": "hours", "every_hours": 1}, "Every hour"),
        ({"cadence": "hours", "every_hours": 6}, "Every 6 hours"),
        ({"cadence": "daily", "time": "03:00"}, "Daily at 3:00 AM"),
        ({"cadence": "daily", "time": "15:05"}, "Daily at 3:05 PM"),
        ({"cadence": "weekly", "weekday": 0, "time": "00:30"}, "Mondays at 12:30 AM"),
        (
            {"cadence": "monthly", "day_of_month": 22, "time": "12:00"},
            "Monthly on the 22nd at 12:00 PM",
        ),
        (
            {"cadence": "monthly", "day_of_month": 11, "time": "09:00"},
            "Monthly on the 11th at 9:00 AM",
        ),
    ],
)
def test_schedule_label_is_short_and_includes_the_time(kwargs, label) -> None:
    assert schedule_label(ScheduleConfig(**kwargs)) == label


def test_failed_sync_launch_is_logged_retried_later_and_leaves_the_check_due(
    tmp_path, caplog
) -> None:
    paths = XDGPaths(
        config_dir=tmp_path / "config", data_dir=tmp_path, cache_dir=tmp_path / "cache"
    )
    config = AppConfig.model_validate(
        {
            "schedule": {"enabled": True, "randomize_time": False},
            "check": {"enabled": True},
            "fxtwitter": {"enabled": True},
        }
    )
    started: list[str] = []

    class Supervisor:
        active_kind = None
        active_run_id = None

        def start(self, *, kind, **_kwargs):
            started.append(kind)
            if kind == "sync":
                raise OSError(errno.ENOSPC, "No space left on device")
            return {"run_id": "check-run"}

    manager = ScheduleManager(paths, config, Supervisor())
    now = 10_000.0
    manager._state["next_run_at"] = now - 1
    manager._state["check_next_run_at"] = now - 1

    with caplog.at_level(logging.ERROR, logger="tweetnook.jobs.scheduler"):
        manager.tick(now=now)

    assert started == ["sync", "check"]
    assert "Could not start the scheduled sync" in caplog.text
    assert manager._state["next_run_at"] == now + 15 * 60
    assert manager._state["last_result"].startswith("failed to start")
    assert manager._state["check_last_result"] == "started"

    # The retry waits for its time rather than repeating on every tick.
    manager.tick(now=now + 5)
    assert started == ["sync", "check"]
    manager.tick(now=now + 15 * 60)
    assert started == ["sync", "check", "sync"]


def test_unexpected_stage_failure_is_logged_and_the_loop_keeps_going(tmp_path, caplog) -> None:
    paths = XDGPaths(
        config_dir=tmp_path / "config", data_dir=tmp_path, cache_dir=tmp_path / "cache"
    )
    manager = ScheduleManager(paths, AppConfig(), object())
    ran = []

    def explode(_timestamp):
        raise RuntimeError("boom")

    manager._tick_sync = explode
    manager._tick_check = ran.append
    with caplog.at_level(logging.ERROR, logger="tweetnook.jobs.scheduler"):
        manager.tick(now=1.0)
    assert ran == [1.0]
    assert "Scheduled sync could not run" in caplog.text


NEW_YORK = {"timezone": "America/New_York", "randomize_time": False}


@pytest.mark.parametrize(
    ("cadence", "after", "expected"),
    [
        # 2026-11-01: New York repeats 01:00-02:00, at 05:00-06:00Z then 06:00-07:00Z.
        pytest.param({"cadence": "weekly", "weekday": 6}, "05:15", "06:30", id="weekly-first-pass"),
        pytest.param(
            {"cadence": "weekly", "weekday": 6}, "06:15", "06:30", id="weekly-second-pass"
        ),
        pytest.param({"cadence": "monthly", "day_of_month": 1}, "05:15", "06:30", id="month-first"),
        pytest.param(
            {"cadence": "monthly", "day_of_month": 1}, "06:15", "06:30", id="month-second"
        ),
        pytest.param({"cadence": "daily"}, "05:15", "06:30", id="daily-first-pass"),
        pytest.param({"cadence": "daily"}, "06:15", "06:30", id="daily-second-pass"),
    ],
)
def test_repeated_hour_resolves_to_its_later_occurrence(cadence, after, expected) -> None:
    config = ScheduleConfig(**NEW_YORK, time="01:30", **cadence)
    hour, minute = map(int, after.split(":"))
    reference = datetime(2026, 11, 1, hour, minute, tzinfo=UTC)
    hour, minute = map(int, expected.split(":"))
    assert next_run_after(config, reference) == datetime(2026, 11, 1, hour, minute, tzinfo=UTC)


@pytest.mark.parametrize(
    ("cadence", "expected"),
    [
        ({"cadence": "daily"}, datetime(2026, 11, 2, 6, 30, tzinfo=UTC)),
        ({"cadence": "weekly", "weekday": 6}, datetime(2026, 11, 8, 6, 30, tzinfo=UTC)),
        ({"cadence": "monthly", "day_of_month": 1}, datetime(2026, 12, 1, 6, 30, tzinfo=UTC)),
    ],
)
def test_exact_occurrence_is_not_returned_again(cadence, expected) -> None:
    config = ScheduleConfig(**NEW_YORK, time="01:30", **cadence)
    assert next_run_after(config, datetime(2026, 11, 1, 6, 30, tzinfo=UTC)) == expected


def test_skipped_hour_runs_just_after_the_change() -> None:
    # 2026-03-08: New York skips 02:00-03:00, so 02:30 EST is 07:30Z, shown as 03:30 EDT.
    config = ScheduleConfig(**NEW_YORK, time="02:30", cadence="daily")
    reference = datetime(2026, 3, 8, 6, 0, tzinfo=UTC)
    assert next_run_after(config, reference) == datetime(2026, 3, 8, 7, 30, tzinfo=UTC)


def test_repeated_hour_check_starts_once(tmp_path) -> None:
    paths = XDGPaths(
        config_dir=tmp_path / "config", data_dir=tmp_path, cache_dir=tmp_path / "cache"
    )
    config = AppConfig.model_validate(
        {
            "schedule": {"timezone": "America/New_York"},
            "check": {"enabled": True, "cadence": "weekly", "weekday": 6, "time": "01:30"},
            "fxtwitter": {"enabled": True},
        }
    )
    started: list[str] = []

    class Supervisor:
        active_kind = None
        active_run_id = None

        def start(self, *, kind, **_kwargs):
            started.append(kind)
            return {"run_id": "check-run"}

    manager = ScheduleManager(paths, config, Supervisor())
    second_pass = datetime(2026, 11, 1, 6, 15, tzinfo=UTC).timestamp()
    manager._state["check_next_run_at"] = second_pass
    for elapsed in (0, 5, 10):
        manager.tick(now=second_pass + elapsed)
    assert started == ["check"]
    assert manager._state["check_next_run_at"] > second_pass + 10
