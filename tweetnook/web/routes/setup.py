"""Focused first-run authentication and Twitter/X archive setup endpoints."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from tweetnook.config import (
    AuthConfig,
    ensure_paths,
    load_config,
    resolve_paths,
    update_config_values,
)
from tweetnook.exceptions import ProcessLockError
from tweetnook.job_supervisor import JobConflictError
from tweetnook.web.deps import server_state, verify_credentials
from tweetnook.web.routes.activity import _read_active_snapshot

router = APIRouter(prefix="/api/setup", tags=["setup"])
MASKED_SECRET = "********"
MAX_ARCHIVE_BYTES = 50 * 1024**3


class AuthUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auth_token: str | None = None
    ct0: str | None = None
    user_id: str | None = None


def _auth_payload(config: AuthConfig, *, verified: bool = False) -> dict[str, object]:
    values = config.model_dump()
    for field in ("auth_token", "ct0"):
        if values.get(field):
            values[field] = MASKED_SECRET
    configured = bool(config.auth_token and config.ct0)
    return {"configured": configured, "verified": verified, "values": values}


def _candidate_auth(request: AuthUpdateRequest, current: AuthConfig) -> AuthConfig:
    values: dict[str, str | None] = {}
    for field, value in request.model_dump().items():
        if field not in request.model_fields_set:
            value = getattr(current, field)
        if field in {"auth_token", "ct0"} and value == MASKED_SECRET:
            value = getattr(current, field)
        if isinstance(value, str):
            value = value.strip() or None
        values[field] = value
    return AuthConfig.model_validate(values)


def _test_auth_candidate(candidate: AuthConfig) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for name in ("TWEETNOOK_AUTH_TOKEN", "TWEETNOOK_CT0", "TWEETNOOK_USER_ID"):
        env.pop(name, None)
    with tempfile.TemporaryDirectory(prefix="tweetnook-auth-") as config_root:
        env["XDG_CONFIG_HOME"] = config_root
        env["XDG_DATA_HOME"] = os.path.join(config_root, "data")
        env["XDG_CACHE_HOME"] = os.path.join(config_root, "cache")
        temporary_paths = ensure_paths(resolve_paths(env))
        update_config_values(
            temporary_paths,
            {
                "auth.auth_token": candidate.auth_token,
                "auth.ct0": candidate.ct0,
                "auth.user_id": candidate.user_id,
            },
        )
        return subprocess.run(
            [sys.executable, "-m", "tweetnook", "auth", "check"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
            check=False,
        )


def _archive_payload() -> dict[str, object]:
    paths = server_state.get("paths")
    if paths is None:
        raise HTTPException(status_code=503, detail="Setup service is not initialized.")
    if coordinator := server_state.get("setup_coordinator"):
        return coordinator.status()["archive"]
    # Keep the route useful in embedded/test applications without the server lifespan.
    store = server_state.get("store")
    archive = paths.staged_archive_file
    imported = bool(store and store.has_completed_archive_import())
    pending = store.count_incomplete_initial_enrichment() if imported else 0
    return {
        "ready": store is not None,
        "uploaded": archive.is_file(),
        "filename": archive.name if archive.is_file() else None,
        "size": archive.stat().st_size if archive.is_file() else None,
        "imported": imported,
        "enriched": imported and not pending,
        "pending_enrichment": pending,
        "warnings": []
        if imported and not pending
        else ["Archive import or enrichment is pending."],
    }


@router.get("")
def get_setup(_auth: Annotated[bool, Depends(verify_credentials)]) -> dict[str, object]:
    config, _ = load_config()
    coordinator = server_state.get("setup_coordinator")
    payload = coordinator.status() if coordinator else {"archive": _archive_payload()}
    return {
        **payload,
        "auth": _auth_payload(
            config.auth,
            verified=bool(coordinator and coordinator.state.auth_verified),
        ),
    }


@router.put("/auth")
def put_auth(
    request: AuthUpdateRequest,
    _auth: Annotated[bool, Depends(verify_credentials)],
) -> dict[str, object]:
    paths = server_state.get("paths")
    if paths is None:
        raise HTTPException(status_code=503, detail="Setup storage is not initialized.")
    current, _ = load_config()
    candidate = _candidate_auth(request, current.auth)
    if not candidate.auth_token or not candidate.ct0:
        raise HTTPException(
            status_code=422,
            detail="Authentication failed: auth_token and ct0 are required.",
        )
    try:
        result = _test_auth_candidate(candidate)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(status_code=503, detail=f"Authentication test failed: {exc}") from exc
    output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    if result.returncode != 0:
        detail = output[-8000:] or "The Twitter/X authentication probe did not succeed."
        raise HTTPException(status_code=422, detail=detail)

    changes: dict[str, object] = {}
    for field, value in request.model_dump(exclude_unset=True).items():
        if field in {"auth_token", "ct0"} and value == MASKED_SECRET:
            continue
        changes[f"auth.{field}"] = value.strip() if isinstance(value, str) else value
    try:
        update_config_values(paths, changes)
        config, _ = load_config()
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    server_state["config"] = config
    if supervisor := server_state.get("job_supervisor"):
        supervisor.config = config
    coordinator = server_state.get("setup_coordinator")
    if coordinator:
        coordinator.auth_was_verified()
    return _auth_payload(
        config.auth,
        verified=bool(coordinator and coordinator.state.auth_verified),
    )


@router.put("/archive")
async def upload_archive(
    request: Request,
    _auth: Annotated[bool, Depends(verify_credentials)],
) -> dict[str, object]:
    paths = server_state.get("paths")
    if paths is None:
        raise HTTPException(status_code=503, detail="Setup storage is not initialized.")
    if _read_active_snapshot() is not None:
        raise HTTPException(status_code=409, detail="Wait for the active command to finish.")
    archive = paths.staged_archive_file
    archive.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=".archive-", suffix=".upload", dir=archive.parent)
    os.close(fd)
    temporary = archive.parent / Path(temporary_name).name
    size = 0
    try:
        with temporary.open("wb") as handle:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_ARCHIVE_BYTES:
                    raise HTTPException(status_code=413, detail="Archive exceeds the 50 GiB limit.")
                handle.write(chunk)
        if size == 0 or not zipfile.is_zipfile(temporary):
            raise HTTPException(status_code=422, detail="Upload must be a valid archive.zip file.")
        if _read_active_snapshot() is not None:
            raise HTTPException(
                status_code=409, detail="A command started during upload. Retry after it finishes."
            )
        temporary.replace(archive)
    finally:
        temporary.unlink(missing_ok=True)
    return _archive_payload()


@router.delete("/archive")
def clear_archive(_auth: Annotated[bool, Depends(verify_credentials)]) -> dict[str, object]:
    paths = server_state.get("paths")
    if paths is None:
        raise HTTPException(status_code=503, detail="Setup storage is not initialized.")
    if _read_active_snapshot() is not None:
        raise HTTPException(status_code=409, detail="Wait for the active command to finish.")
    paths.staged_archive_file.unlink(missing_ok=True)
    return _archive_payload()


def _coordinator():
    coordinator = server_state.get("setup_coordinator")
    if coordinator is None:
        raise HTTPException(status_code=503, detail="Setup service is not initialized.")
    return coordinator


def _initialize(mode):
    coordinator = _coordinator()
    try:
        if mode == "empty":
            coordinator.create_empty_archive()
            return coordinator.status()
        return coordinator.begin(mode)
    except (JobConflictError, ProcessLockError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (OSError, sqlite3.Error) as exc:
        coordinator.notices.add(
            "initialization-start",
            "Archive could not be initialized",
            "Check available storage and retry Setup.",
            action="setup",
        )
        raise HTTPException(status_code=503, detail="Could not initialize the archive.") from exc


@router.post("/archive/import", status_code=status.HTTP_202_ACCEPTED)
def import_archive(_auth: Annotated[bool, Depends(verify_credentials)]) -> dict[str, object]:
    paths = server_state.get("paths")
    if paths is None or not paths.staged_archive_file.is_file():
        raise HTTPException(status_code=409, detail="Upload archive.zip before importing it.")
    return _initialize("import")


@router.post("/archive/empty")
def empty_archive(_auth: Annotated[bool, Depends(verify_credentials)]):
    return _initialize("empty")


@router.post("/migrate", status_code=202)
def migrate_archive(_auth: Annotated[bool, Depends(verify_credentials)]):
    coordinator = _coordinator()
    if not (coordinator.paths.data_dir / "archive.lancedb").exists():
        raise HTTPException(status_code=409, detail="No upstream LanceDB archive was found.")
    return _initialize("migration")


class CompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str | None = None
    confirm_password: str | None = None
    skip_auth: bool = False


def _finish_setup(request):
    coordinator = _coordinator()
    with coordinator.lock:
        initial = not coordinator.state.completed
        if initial and not coordinator.state.initialization_mode:
            raise HTTPException(
                status_code=409, detail="Choose an archive initialization option first."
            )
        if initial and not coordinator.state.auth_verified and not request.skip_auth:
            raise HTTPException(
                status_code=409, detail="Connect Twitter/X or explicitly skip connecting."
            )
        password_hash = server_state.get("password_hash")
        if request.password is not None:
            if not request.password or request.password != request.confirm_password:
                raise HTTPException(status_code=422, detail="Enter matching, nonempty passwords.")
            password_hash = hashlib.sha256(request.password.encode("utf-8")).hexdigest()
        if not password_hash:
            raise HTTPException(status_code=422, detail="Create a Web password to finish Setup.")
        if request.password is not None:
            update_config_values(coordinator.paths, {"web.password_hash": password_hash})
            config, _ = load_config()
            server_state["config"] = config
            server_state["job_supervisor"].config = config
        coordinator.complete_walkthrough(skip_auth=request.skip_auth)
        server_state["password_hash"] = password_hash
        coordinator.reconcile()
    return {"completed": True, "reload": initial or request.password is not None}


@router.put("/password")
def setup_password(request: CompleteRequest, _auth: Annotated[bool, Depends(verify_credentials)]):
    """Set a password as the final first-run action, or change it after authentication."""
    return _finish_setup(request)


@router.post("/complete")
def complete_setup(request: CompleteRequest, _auth: Annotated[bool, Depends(verify_credentials)]):
    return _finish_setup(request)
