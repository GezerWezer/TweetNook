from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from tweetnook.config import AppConfig, ScheduleConfig, XDGPaths
from tweetnook.job_supervisor import JobConflictError
from tweetnook.scheduler import ScheduleManager, next_run_after, schedule_description


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

    with patch("tweetnook.scheduler.random.uniform", return_value=1.25) as uniform:
        result = next_run_after(config, datetime(2026, 8, 12, 10, 30, tzinfo=UTC))

    assert result == datetime(2026, 8, 12, 11, 15, tzinfo=UTC)
    lower_bound, upper_bound = uniform.call_args.args
    assert 0.5 < lower_bound < 0.500001
    assert upper_bound == 2


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
