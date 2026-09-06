"""Live command status, production job controls, schedules, and retained logs."""

from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from tweetnook.activity_history import (
    latest_finished_snapshot,
    list_runs,
    read_run,
    read_run_log,
)
from tweetnook.config import ScheduleConfig, update_config_values
from tweetnook.job_supervisor import JobConflictError
from tweetnook.web.deps import server_state, verify_credentials

router = APIRouter(prefix="/api/activity", tags=["activity"])


class ImportRequest(BaseModel):
    archive: Path
    regen: bool = False
    enrich: bool = True
    detail_lookups: int = Field(default=0, ge=0)
    sample_limit: int | None = Field(default=None, ge=1)


def _process_is_running(pid: Any) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError):
        return False
    return True


def _read_snapshot() -> dict[str, Any] | None:
    paths = server_state.get("paths")
    if paths is None or not paths.activity_status_file.is_file():
        return None
    try:
        snapshot = json.loads(paths.activity_status_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return snapshot if isinstance(snapshot, dict) else None


def _read_active_snapshot() -> dict[str, Any] | None:
    snapshot = _read_snapshot()
    if (
        snapshot is not None
        and snapshot.get("running")
        and _process_is_running(snapshot.get("pid"))
    ):
        return snapshot

    supervisor = server_state.get("job_supervisor")
    if supervisor is None or not supervisor.active:
        return None
    started_at = server_state.get("activity_worker_started_at", time.time())
    kind = supervisor.active_kind or "sync"
    return {
        "version": 1,
        "run_id": supervisor.active_run_id,
        "origin": supervisor.active_origin or "web",
        "pid": supervisor.active_pid,
        "title": f"tweetnook {kind}",
        "running": True,
        "success": None,
        "summary": "Starting worker process",
        "started_at": started_at,
        "updated_at": time.time(),
        "elapsed_seconds": max(time.time() - started_at, 0),
        "active_step": None,
        "steps": [],
        "issues": [],
    }


def _schedule_status() -> dict[str, Any]:
    schedule_manager = server_state.get("schedule_manager")
    if schedule_manager is not None:
        return schedule_manager.status()
    return {
        "configured": False,
        "enabled": False,
        "relative": "Not configured",
        "date": "Set up scheduling in Settings",
    }


@router.get("/status")
def get_activity_status(
    _auth: Annotated[bool, Depends(verify_credentials)],
) -> dict[str, Any]:
    snapshot = _read_active_snapshot()
    paths = server_state.get("paths")
    data_dir = getattr(paths, "data_dir", None)
    last_snapshot = (
        latest_finished_snapshot(data_dir, title="tweetnook sync") if data_dir is not None else None
    )
    if last_snapshot is None:
        candidate = _read_snapshot()
        if (
            candidate is not None
            and not candidate.get("running")
            and candidate.get("title") == "tweetnook sync"
        ):
            last_snapshot = candidate
    if snapshot is not None:
        last_snapshot = None
    return {
        "active": snapshot is not None,
        "snapshot": snapshot,
        "last_snapshot": last_snapshot,
        "schedule": _schedule_status(),
    }


@router.post("/stop", status_code=status.HTTP_202_ACCEPTED)
def stop_activity(
    _auth: Annotated[bool, Depends(verify_credentials)],
) -> dict[str, Any]:
    supervisor = server_state.get("job_supervisor")
    if supervisor is not None:
        try:
            return supervisor.stop()
        except JobConflictError:
            pass

    snapshot = _read_active_snapshot()
    if snapshot is None or snapshot.get("origin") not in {"web", "schedule", "cli"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No Web or scheduled task is running.",
        )
    try:
        pid = int(snapshot["pid"])
        if snapshot.get("origin") in {"web", "schedule"} and hasattr(os, "killpg"):
            os.killpg(pid, signal.SIGINT)
        else:
            os.kill(pid, signal.SIGINT)
    except (KeyError, TypeError, ValueError, ProcessLookupError, PermissionError, OSError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The task could not be stopped.",
        ) from exc
    return {"stopping": True, "run_id": snapshot.get("run_id")}


def _start_activity(kind: str, cli_args: list[str], title: str) -> dict[str, Any]:
    active = _read_active_snapshot()
    if active is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{active.get('title', 'Another command')} is already running.",
        )
    supervisor = server_state.get("job_supervisor")
    if supervisor is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The activity service is not initialized.",
        )
    try:
        return supervisor.start(kind=kind, cli_args=cli_args, origin="web", title=title)
    except JobConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not start the worker: {exc}",
        ) from exc


@router.post("/sync", status_code=status.HTTP_202_ACCEPTED)
def start_sync(_auth: Annotated[bool, Depends(verify_credentials)]) -> dict[str, Any]:
    return _start_activity("sync", ["sync"], "tweetnook sync")


@router.post("/enrich", status_code=status.HTTP_202_ACCEPTED)
def start_enrich(_auth: Annotated[bool, Depends(verify_credentials)]) -> dict[str, Any]:
    store = server_state.get("store")
    if store is not None and store.count_incomplete_initial_enrichment() <= 0:
        return {
            "started": False,
            "kind": "enrich",
            "reason": "no_pending_enrichment",
        }
    return _start_activity("enrich", ["import", "enrich"], "tweetnook import enrich")


@router.post("/import", status_code=status.HTTP_202_ACCEPTED)
def start_import(
    request: ImportRequest,
    _auth: Annotated[bool, Depends(verify_credentials)],
) -> dict[str, Any]:
    archive = request.archive.expanduser().resolve()
    if not archive.exists() or (not archive.is_dir() and archive.suffix.lower() != ".zip"):
        raise HTTPException(status_code=422, detail="Archive must be an existing ZIP or directory.")
    args = ["import", "x-archive", str(archive)]
    if request.regen:
        args.append("--regen")
    if not request.enrich:
        args.append("--no-enrich")
    if request.detail_lookups:
        args.extend(("--detail-lookups", str(request.detail_lookups)))
    if request.sample_limit is not None:
        args.extend(("--sample-limit", str(request.sample_limit)))
    return _start_activity("import", args, "tweetnook import x-archive")


@router.get("/runs")
def activity_runs(
    _auth: Annotated[bool, Depends(verify_credentials)],
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    paths = server_state.get("paths")
    if paths is None:
        return {"runs": []}
    return {
        "runs": list_runs(
            paths.data_dir,
            limit=min(max(limit, 1), 200),
            offset=max(offset, 0),
        )
    }


@router.get("/runs/{run_id}")
def activity_run(
    run_id: str,
    _auth: Annotated[bool, Depends(verify_credentials)],
) -> dict[str, Any]:
    paths = server_state.get("paths")
    try:
        run = read_run(paths.data_dir, run_id) if paths is not None else None
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Run not found.") from exc
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    return run


@router.get("/runs/{run_id}/log", response_class=PlainTextResponse)
def activity_run_log(
    run_id: str,
    _auth: Annotated[bool, Depends(verify_credentials)],
) -> str:
    paths = server_state.get("paths")
    try:
        if paths is None or read_run(paths.data_dir, run_id) is None:
            raise ValueError
        return read_run_log(paths.data_dir, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Run not found.") from exc


@router.get("/schedule")
def get_schedule(_auth: Annotated[bool, Depends(verify_credentials)]) -> dict[str, Any]:
    manager = server_state.get("schedule_manager")
    if manager is None:
        raise HTTPException(status_code=503, detail="The scheduler is not initialized.")
    return manager.status()


@router.put("/schedule")
def put_schedule(
    request: ScheduleConfig,
    _auth: Annotated[bool, Depends(verify_credentials)],
) -> dict[str, Any]:
    manager = server_state.get("schedule_manager")
    paths = server_state.get("paths")
    if manager is None or paths is None:
        raise HTTPException(status_code=503, detail="The scheduler is not initialized.")
    try:
        update_config_values(
            paths,
            {f"schedule.{key}": value for key, value in request.model_dump().items()},
        )
        manager.reload(reset=True)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return manager.status()
