"""What a finished job achieved, as one value every layer reads the same way."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any


class Outcome(StrEnum):
    SUCCEEDED = "succeeded"
    # Nothing was due: every selected step was skipped.
    NO_WORK = "no_work"
    # Stopped at a limit it will resume from, such as a daily request quota.
    PAUSED = "paused"
    # Failed after finishing at least one step, so some of its work is saved.
    PARTIAL = "partial"
    FAILED = "failed"
    STOPPED = "stopped"

    @property
    def ok(self) -> bool:
        return self in {Outcome.SUCCEEDED, Outcome.NO_WORK, Outcome.PAUSED}

    @property
    def run_status(self) -> str:
        if self is Outcome.STOPPED:
            return "stopped"
        return "completed" if self.ok else "failed"


def recorded_outcome(record: Mapping[str, Any]) -> Outcome | None:
    """Read a saved run or snapshot; None means its result was never verified."""
    value = record.get("outcome")
    if value in set(Outcome):
        return Outcome(value)
    # Activity history from before outcomes were recorded is still shown and still resumed from.
    if record.get("stopped") or record.get("status") == "stopped":
        return Outcome.STOPPED
    success = record.get("success")
    if success is None:
        return None
    return Outcome.SUCCEEDED if success else Outcome.FAILED


def finished_cleanly(exit_code: int | None, outcome: Outcome | None) -> bool:
    """A nonzero exit always wins. Publishing a result is best-effort, so a clean exit with no
    saved result counts as clean, while a saved failure still counts when the exit was zero.
    """
    if exit_code not in {None, 0}:
        return False
    if outcome is not None:
        return outcome.ok
    return exit_code == 0
