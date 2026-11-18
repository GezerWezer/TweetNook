"""The Web jobs list: every job the CLI can run, with its state, last and next run."""

from __future__ import annotations

import time
from typing import Any

from tweetnook.config import AppConfig
from tweetnook.jobs.activity_history import list_runs, read_run
from tweetnook.jobs.catalog import JOBS, JOBS_BY_TITLE, option_defaults


def _run_record(run: dict[str, Any]) -> dict[str, Any]:
    return {
        key: run.get(key)
        for key in (
            "run_id",
            "title",
            "origin",
            "status",
            "success",
            "started_at",
            "completed_at",
            "summary",
        )
    }


def jobs_payload(
    *,
    config: AppConfig,
    data_dir: Any,
    active: dict[str, Any] | None,
    queue: list[dict[str, Any]],
    schedule: dict[str, Any],
    check: dict[str, Any],
    sync_deferred: bool,
    setup_queue: list[str] | None = None,
) -> dict[str, Any]:
    """Every job with its state, last run and next run, for the Web jobs list.

    ``setup_queue`` holds jobs Setup starts on its own after the current one.
    """

    last: dict[str, dict[str, Any]] = {}
    runs = list_runs(data_dir, limit=None) if data_dir is not None else []
    week_ago = time.time() - 7 * 86400
    for run in runs:
        job = JOBS_BY_TITLE.get(str(run.get("title") or ""))
        if job is None or job.key in last or run.get("status") in {"starting", "running"}:
            continue
        last[job.key] = _run_record(run)
    running_key = JOBS_BY_TITLE.get(str((active or {}).get("title") or ""))
    running_key = running_key.key if running_key else None
    active_steps = {
        step.get("key"): step.get("state") for step in (active or {}).get("steps") or []
    }
    queued = {item["kind"]: index for index, item in enumerate(queue)}
    now = time.time()
    jobs = []
    for job in JOBS:
        record = last.get(job.key)
        state = "never" if record is None else "ok"
        if (
            record is not None
            and record.get("success") is False
            and record.get("status") != "stopped"
        ):
            state = "failed"
        queue_info = None
        if job.key in queued:
            state = "queued"
            queue_info = {"position": queued[job.key] + 1, "retry_at": None, "removable": True}
        elif job.key in (setup_queue or []):
            state = "queued"
            queue_info = {"position": None, "retry_at": None, "removable": False}
        if job.key == "check" and check.get("deferred"):
            state = "queued"
            queue_info = {"position": None, "retry_at": check.get("next_run_at"), "removable": True}
        if job.key == "sync" and sync_deferred and schedule.get("next_run_at"):
            state = "queued"
            queue_info = {
                "position": None,
                "retry_at": schedule.get("next_run_at"),
                "removable": False,
            }
        if running_key == job.key:
            state = "running"
        elif (
            running_key == "sync" and job.sync_step and active_steps.get(job.sync_step) == "active"
        ):
            state = "in_sync"
        if job.key == "sync":
            next_at = schedule.get("next_run_at") if schedule.get("enabled") else None
            next_info = {"at": next_at, "label": None if next_at else "Off"}
        elif job.key == "check":
            next_at = check.get("next_run_at")
            next_info = {"at": next_at, "label": None if next_at else "Off"}
        elif job.sync_step and (
            (job.key == "tagging" and config.tagging.enabled)
            or (job.key == "smart-search" and config.smart_search.enabled)
            or job.key not in {"tagging", "smart-search"}
        ):
            next_info = {"at": None, "label": "With sync"}
        else:
            next_info = {"at": None, "label": None}
        available, reason = True, None
        if job.key == "check" and not config.fx_enabled:
            available, reason = False, "Needs FxTwitter"
        snapshot = None
        if record is not None and data_dir is not None:
            try:
                snapshot = (read_run(data_dir, str(record["run_id"])) or {}).get("snapshot")
            except ValueError:
                snapshot = None
        jobs.append(
            {
                "key": job.key,
                "name": job.name,
                "description": job.description,
                "command": job.title,
                "scheduled": job.scheduled,
                "state": state,
                "available": available,
                "unavailable_reason": reason,
                "last": record,
                "last_snapshot": snapshot,
                "next": next_info,
                "queue": queue_info,
                "options": [option.as_dict() for option in job.options],
                "defaults": option_defaults(job, config),
            }
        )
    return {
        "jobs": jobs,
        "running": (
            {
                "key": running_key,
                "run_id": active.get("run_id"),
                "origin": active.get("origin"),
                "started_at": active.get("started_at"),
            }
            if active and running_key
            else None
        ),
        "runs_this_week": sum(1 for run in runs if (run.get("started_at") or 0) >= week_ago),
        "now": now,
    }
