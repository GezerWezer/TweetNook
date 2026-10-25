"""Focused first-run authentication and Twitter/X archive setup endpoints."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from tweetnook.config import (
    SYNC_COLLECTION_ORDER,
    AppConfig,
    AuthConfig,
    ReplayConfig,
    ScheduleConfig,
    SyncCollection,
    XDGPaths,
    ensure_paths,
    load_config,
    resolve_paths,
    update_config_values,
)
from tweetnook.dev_replay import (
    REPLAY_STATUS_PATH,
    dev_tools_enabled,
    normalize_replay_url,
)
from tweetnook.exceptions import ProcessLockError
from tweetnook.job_supervisor import JobConflictError
from tweetnook.web.deps import remember_web_session, server_state, verify_credentials
from tweetnook.web.routes.activity import _read_active_snapshot
from tweetnook.web.runtime_config import reload_server_config

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
    configured = all((value or "").strip() for value in (config.auth_token, config.ct0))
    return {"configured": configured, "verified": verified, "values": values}


def _archive_auth(config: AuthConfig) -> AuthConfig:
    """Use the imported account identity only as a fallback for an unset ID."""
    if config.user_id:
        return config
    store = server_state.get("store")
    owner = (
        store.get_archive_owner_id() if store and hasattr(store, "get_archive_owner_id") else None
    )
    if owner is None:
        paths = server_state.get("paths")
        replay = _read_replay_status(paths)
        owner = replay.get("owner_user_id") if replay else None
    owner = str(owner or "")
    return config.model_copy(update={"user_id": owner}) if owner.isdecimal() else config


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


def _replay_config_changes(replay: ReplayConfig) -> dict[str, object]:
    return {
        "replay.url": replay.url,
        "replay.password": replay.password,
        "replay.placeholder_media": replay.placeholder_media,
        "replay.skip_quote_expansion": replay.skip_quote_expansion,
        "replay.skip_link_previews": replay.skip_link_previews,
    }


def _test_auth_candidate(
    candidate: AuthConfig, replay: ReplayConfig | None = None
) -> subprocess.CompletedProcess[str]:
    if replay is None:
        replay = load_config()[0].replay
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
                # A test install probes its replay source, never Twitter/X.
                **_replay_config_changes(replay),
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
    auth = _auth_payload(
        _archive_auth(config.auth),
        verified=bool(coordinator and coordinator.state.auth_verified),
    )
    if config.replay.url:
        auth["verified"] = auth["configured"]
    return {
        **payload,
        "auth": auth,
        "dev": _dev_payload(config),
        "schedule": {"enabled": config.schedule.enabled, "collections": config.sync.collections},
    }


REPLAY_STATUS_FIELDS = (
    "bookmarks_pages",
    "bookmarks_chain_pages",
    "likes_pages",
    "likes_chain_pages",
    "thread_captures",
    "downloaded_media",
    "placeholder_media",
    "owner_user_id",
)


def _replay_status_file(paths: XDGPaths) -> Path:
    return paths.cache_dir / "replay-source-status.json"


def _read_replay_status(paths: XDGPaths | None) -> dict[str, object] | None:
    if paths is None:
        return None
    try:
        data = json.loads(_replay_status_file(paths).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_replay_status(paths: XDGPaths, status_summary: dict[str, object] | None) -> None:
    target = _replay_status_file(paths)
    try:
        if status_summary is None:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(status_summary), encoding="utf-8")
    except OSError:
        pass


def _dev_payload(config: AppConfig) -> dict[str, object]:
    """Developer replay state for Setup. Never includes the source password."""

    replay = config.replay
    connected = bool(replay.url)
    return {
        "tools": dev_tools_enabled(),
        "replay": {
            "connected": connected,
            "url": replay.url,
            "password": MASKED_SECRET if replay.password else None,
            "placeholder_media": replay.placeholder_media,
            "skip_quote_expansion": replay.skip_quote_expansion,
            "skip_link_previews": replay.skip_link_previews,
            "status": _read_replay_status(server_state.get("paths")) if connected else None,
        },
    }


class ReplaySourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    password: str | None = None
    placeholder_media: bool | None = None
    skip_quote_expansion: bool | None = None
    skip_link_previews: bool | None = None


class ReplayMediaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    placeholder_media: bool


class ReplayQuoteExpansionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skip_quote_expansion: bool


class ReplayLinkPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skip_link_previews: bool


def _require_dev_tools() -> XDGPaths:
    if not dev_tools_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    paths = server_state.get("paths")
    if paths is None:
        raise HTTPException(status_code=503, detail="Setup storage is not initialized.")
    return paths


def _source_client() -> httpx.Client:
    # Deliberately not ``client_transport()``: this request goes to the source itself.
    return httpx.Client(timeout=15.0, follow_redirects=False)


def _probe_replay_source(url: str, password: str | None) -> dict[str, object]:
    """Call the source's replay status endpoint directly (never via the replay transport)."""

    try:
        with _source_client() as client:
            response = client.get(f"{url}{REPLAY_STATUS_PATH}", auth=("tweetnook", password or ""))
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=422, detail=f"Could not reach the replay source at {url}: {exc}"
        ) from exc
    if response.status_code == 401:
        raise HTTPException(
            status_code=422, detail="The replay source rejected the password (its Web password)."
        )
    if response.status_code == 404:
        raise HTTPException(
            status_code=422,
            detail=(
                "That server is not serving a replay source. On it, turn on Settings > "
                "Configuration > Developer > Serve as replay source."
            ),
        )
    try:
        data = response.json()
    except ValueError:
        data = None
    if response.status_code != 200 or not isinstance(data, dict) or not data.get("replay_source"):
        detail = data.get("detail") if isinstance(data, dict) else None
        raise HTTPException(
            status_code=422,
            detail=str(detail or f"The replay source answered with HTTP {response.status_code}."),
        )
    return {field: data.get(field) for field in REPLAY_STATUS_FIELDS}


def _apply_replay_config(paths: XDGPaths, changes: dict[str, object]) -> AppConfig:
    try:
        update_config_values(paths, changes)
        config, _ = load_config()
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    reload_server_config(config)
    return config


@router.put("/replay")
def put_replay_source(
    request: ReplaySourceRequest,
    _auth: Annotated[bool, Depends(verify_credentials)],
) -> dict[str, object]:
    """Test a replay source and save it only when its status endpoint answers."""

    paths = _require_dev_tools()
    current = load_config()[0].replay
    try:
        url = normalize_replay_url(request.url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    password = request.password.strip() if request.password else None
    if password == MASKED_SECRET or password is None:
        # The saved password is only ever sent back to the source it was saved for.
        password = current.password if current.url == url else None
    if not password:
        raise HTTPException(status_code=422, detail="Enter the replay source's Web password.")
    placeholder_media = (
        current.placeholder_media
        if request.placeholder_media is None
        else request.placeholder_media
    )
    source_status = _probe_replay_source(url, password)
    _write_replay_status(paths, {**source_status, "checked_at": int(time.time())})
    config = _apply_replay_config(
        paths,
        _replay_config_changes(
            ReplayConfig(
                url=url,
                password=password,
                placeholder_media=placeholder_media,
                skip_quote_expansion=(
                    current.skip_quote_expansion
                    if request.skip_quote_expansion is None
                    else request.skip_quote_expansion
                ),
                skip_link_previews=(
                    current.skip_link_previews
                    if request.skip_link_previews is None
                    else request.skip_link_previews
                ),
            )
        ),
    )
    return _dev_payload(config)


@router.put("/replay/placeholder-media")
def put_replay_placeholder_media(
    request: ReplayMediaRequest,
    _auth: Annotated[bool, Depends(verify_credentials)],
) -> dict[str, object]:
    paths = _require_dev_tools()
    if not load_config()[0].replay.url:
        raise HTTPException(status_code=409, detail="No replay source is connected.")
    config = _apply_replay_config(paths, {"replay.placeholder_media": request.placeholder_media})
    return _dev_payload(config)


@router.put("/replay/skip-quote-expansion")
def put_replay_skip_quote_expansion(
    request: ReplayQuoteExpansionRequest,
    _auth: Annotated[bool, Depends(verify_credentials)],
) -> dict[str, object]:
    paths = _require_dev_tools()
    if not load_config()[0].replay.url:
        raise HTTPException(status_code=409, detail="No replay source is connected.")
    config = _apply_replay_config(
        paths, {"replay.skip_quote_expansion": request.skip_quote_expansion}
    )
    return _dev_payload(config)


@router.put("/replay/skip-link-previews")
def put_replay_skip_link_previews(
    request: ReplayLinkPreviewRequest,
    _auth: Annotated[bool, Depends(verify_credentials)],
) -> dict[str, object]:
    paths = _require_dev_tools()
    if not load_config()[0].replay.url:
        raise HTTPException(status_code=409, detail="No replay source is connected.")
    config = _apply_replay_config(paths, {"replay.skip_link_previews": request.skip_link_previews})
    return _dev_payload(config)


@router.delete("/replay")
def delete_replay_source(_auth: Annotated[bool, Depends(verify_credentials)]) -> dict[str, object]:
    paths = _require_dev_tools()
    config = _apply_replay_config(paths, _replay_config_changes(ReplayConfig()))
    _write_replay_status(paths, None)
    return _dev_payload(config)


@router.put("/auth")
def put_auth(
    request: AuthUpdateRequest,
    _auth: Annotated[bool, Depends(verify_credentials)],
) -> dict[str, object]:
    paths = server_state.get("paths")
    if paths is None:
        raise HTTPException(status_code=503, detail="Setup storage is not initialized.")
    current, _ = load_config()
    candidate = _archive_auth(_candidate_auth(request, current.auth))
    if candidate.user_id and not candidate.user_id.isdecimal():
        raise HTTPException(status_code=422, detail="X user ID must contain only digits.")
    connected = bool(candidate.auth_token and candidate.ct0)
    if not connected and not current.replay.url:
        raise HTTPException(
            status_code=422,
            detail="Authentication failed: auth_token and ct0 are required.",
        )
    if not current.replay.url:
        try:
            result = _test_auth_candidate(candidate)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HTTPException(
                status_code=503, detail=f"Authentication test failed: {exc}"
            ) from exc
        output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
        if result.returncode != 0:
            detail = output[-8000:] or "The Twitter/X authentication probe did not succeed."
            raise HTTPException(status_code=422, detail=detail)

    changes: dict[str, object] = {}
    for field, value in request.model_dump(exclude_unset=True).items():
        if field in {"auth_token", "ct0"} and value == MASKED_SECRET:
            continue
        changes[f"auth.{field}"] = (value.strip() or None) if isinstance(value, str) else value
    if candidate.user_id:
        changes["auth.user_id"] = candidate.user_id
    try:
        update_config_values(paths, changes)
        config, _ = load_config()
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    reload_server_config(config)
    coordinator = server_state.get("setup_coordinator")
    if coordinator:
        if current.replay.url:
            coordinator.reconcile()
        else:
            coordinator.auth_was_verified()
    return _auth_payload(
        config.auth,
        verified=connected
        if current.replay.url
        else bool(coordinator and coordinator.state.auth_verified),
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


class ScheduleSetupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schedule: ScheduleConfig
    collections: list[SyncCollection] = Field(min_length=1)


def _schedule_payload() -> dict[str, object]:
    """Scheduled syncs and what they collect; the scheduler itself starts after Setup."""
    config, _ = load_config()
    manager = server_state.get("schedule_manager")
    status_payload = manager.status() if manager else {"config": config.schedule.model_dump()}
    return {**status_payload, "collections": list(config.sync.collections)}


@router.get("/schedule")
def get_setup_schedule(_auth: Annotated[bool, Depends(verify_credentials)]) -> dict[str, object]:
    return _schedule_payload()


@router.put("/schedule")
def put_setup_schedule(
    request: ScheduleSetupRequest,
    _auth: Annotated[bool, Depends(verify_credentials)],
) -> dict[str, object]:
    """Save the Schedule step while the Activity schedule API is still gated by Setup."""
    paths = server_state.get("paths")
    if paths is None:
        raise HTTPException(status_code=503, detail="Setup storage is not initialized.")
    current, _ = load_config()
    auth = _archive_auth(current.auth)
    coordinator = server_state.get("setup_coordinator")
    connected = bool(coordinator and coordinator.state.auth_verified)
    if (
        connected
        and {"likes", "tweets"}.intersection(request.collections)
        and not (auth.user_id and auth.user_id.isdecimal())
    ):
        raise HTTPException(
            status_code=422,
            detail="Likes and your own tweets require your numeric X user ID. "
            "Return to Connect to enter it, import your official archive, "
            "or select only Bookmarks.",
        )
    changes: dict[str, object] = {
        f"schedule.{key}": value for key, value in request.schedule.model_dump().items()
    }
    changes["sync.collections"] = [c for c in SYNC_COLLECTION_ORDER if c in request.collections]
    if auth.user_id and not current.auth.user_id:
        changes["auth.user_id"] = auth.user_id
    try:
        update_config_values(paths, changes)
        config, _ = load_config()
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    reload_server_config(config, reset_schedule=True)
    return _schedule_payload()


class CompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str | None = None
    confirm_password: str | None = None
    skip_auth: bool = False
    # An explicit choice to open TweetNook without a password; any stored one is removed.
    no_password: bool = False


def _finish_setup(request: CompleteRequest, http_request: Request):
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
        if request.no_password and request.password is not None:
            raise HTTPException(
                status_code=422, detail="Choose a password or no password, not both."
            )
        if request.password is not None:
            if not request.password or request.password != request.confirm_password:
                raise HTTPException(status_code=422, detail="Enter matching, nonempty passwords.")
            password_hash = hashlib.sha256(request.password.encode("utf-8")).hexdigest()
        if request.no_password:
            password_hash = None
        elif not password_hash:
            raise HTTPException(
                status_code=422, detail="Create a Web password or choose no password."
            )
        password_changed = request.password is not None or (
            request.no_password and server_state.get("password_hash")
        )
        if password_changed:
            update_config_values(coordinator.paths, {"web.password_hash": password_hash})
            config, _ = load_config()
            reload_server_config(config)
        coordinator.complete_walkthrough(skip_auth=request.skip_auth)
        server_state["password_hash"] = password_hash
        coordinator.reconcile()
        if request.password is not None:
            remember_web_session(http_request)
    return {"completed": True, "reload": False}


@router.put("/password")
def setup_password(
    request: CompleteRequest,
    http_request: Request,
    _auth: Annotated[bool, Depends(verify_credentials)],
):
    """Set a password as the final first-run action, or change it after authentication."""
    return _finish_setup(request, http_request)


@router.post("/complete")
def complete_setup(
    request: CompleteRequest,
    http_request: Request,
    _auth: Annotated[bool, Depends(verify_credentials)],
):
    return _finish_setup(request, http_request)
