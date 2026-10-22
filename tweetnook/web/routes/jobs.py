"""Every job the CLI can run: status for the Web jobs list, runs with options, the queue."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from tweetnook.config import AppConfig
from tweetnook.job_supervisor import JobConflictError
from tweetnook.web.deps import server_state, verify_credentials
from tweetnook.web.jobs import JOBS_BY_KEY, JobOptionsError, job_cli_args, jobs_payload
from tweetnook.web.routes.activity import _read_active_snapshot

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class RunRequest(BaseModel):
    options: dict[str, Any] = Field(default_factory=dict)


def _config() -> AppConfig:
    return server_state.get("config") or AppConfig()


@router.get("")
def list_jobs(_auth: Annotated[bool, Depends(verify_credentials)]) -> dict[str, Any]:
    paths = server_state.get("paths")
    supervisor = server_state.get("job_supervisor")
    manager = server_state.get("schedule_manager")
    payload = jobs_payload(
        config=_config(),
        data_dir=getattr(paths, "data_dir", None),
        active=_read_active_snapshot(),
        queue=getattr(supervisor, "queue", []) if supervisor is not None else [],
        schedule=manager.status() if manager is not None else {},
        check=manager.check_status() if manager is not None else {},
        sync_deferred=bool(manager and manager.sync_deferred()),
    )
    # The archive Setup staged for import (uploaded through /api/setup/archive).
    staged = getattr(paths, "staged_archive_file", None)
    uploaded = bool(staged and staged.is_file())
    payload["archive"] = {
        "uploaded": uploaded,
        "filename": staged.name if uploaded else None,
        "size": staged.stat().st_size if uploaded else None,
    }
    return payload


@router.post("/{key}/run", status_code=status.HTTP_202_ACCEPTED)
def run_job(
    key: str,
    request: RunRequest,
    _auth: Annotated[bool, Depends(verify_credentials)],
) -> dict[str, Any]:
    """Start a job now, or queue it behind the one that's running."""

    job = JOBS_BY_KEY.get(key)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job.")
    supervisor = server_state.get("job_supervisor")
    paths = server_state.get("paths")
    if supervisor is None or paths is None:
        raise HTTPException(status_code=503, detail="The job service is not initialized.")
    staged = paths.staged_archive_file
    try:
        args = job_cli_args(
            job,
            request.options,
            _config(),
            staged_archive=str(staged) if staged.is_file() else None,
        )
    except JobOptionsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        result = supervisor.enqueue(kind=job.key, cli_args=args, origin="web", title=job.title)
    except JobConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail=f"Could not start the job: {exc}") from exc
    return {**result, "command": " ".join(("tweetnook", *args))}


@router.delete("/{key}/queue")
def unqueue_job(
    key: str,
    _auth: Annotated[bool, Depends(verify_credentials)],
) -> dict[str, Any]:
    """Take a waiting job out of the queue; a deferred check skips tonight's run."""

    if key not in JOBS_BY_KEY:
        raise HTTPException(status_code=404, detail="Unknown job.")
    supervisor = server_state.get("job_supervisor")
    manager = server_state.get("schedule_manager")
    removed = bool(supervisor and supervisor.dequeue(key))
    if not removed and key == "check" and manager is not None:
        removed = manager.skip_deferred_check()
    if not removed:
        raise HTTPException(status_code=409, detail="That job isn't waiting.")
    return {"removed": True, "key": key}
