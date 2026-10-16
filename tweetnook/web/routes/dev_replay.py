"""Replay source endpoints: serve this archive's saved Twitter/X responses to a test install."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, Response
from fastapi.security import HTTPBasicCredentials

from tweetnook.config import load_config
from tweetnook.web.deps import security, server_state, verify_credentials
from tweetnook.web.replay_source import REPLAY_PATH_PREFIX, replay_archive, replay_request

router = APIRouter(prefix=REPLAY_PATH_PREFIX, tags=["dev-replay"])


def _replay_source_enabled() -> bool:
    config, _ = load_config()
    return bool(config.web.dev_replay_source)


def require_replay_source(
    credentials: Annotated[HTTPBasicCredentials | None, Depends(security)],
) -> bool:
    if not _replay_source_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    if not server_state.get("password_hash"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Replay source is disabled until this server has a Web password.",
        )
    return verify_credentials(credentials)


def _archive():
    paths = server_state.get("paths")
    if paths is None:
        raise HTTPException(status_code=503, detail="Replay source is not initialized.")
    archive = replay_archive(paths.data_dir)
    if not archive.db_path.is_file():
        raise HTTPException(status_code=503, detail="This server has no archive to replay.")
    return archive


@router.get("/status")
def replay_status(_auth: Annotated[bool, Depends(require_replay_source)]) -> dict[str, object]:
    return {"replay_source": True, **_archive().status()}


@router.api_route("/x/{host}/{path:path}", methods=["GET", "HEAD", "POST"])
def replay_proxy(
    host: str,
    path: str,
    request: Request,
    _auth: Annotated[bool, Depends(require_replay_source)],
) -> Response:
    result = replay_request(
        _archive(),
        host=host,
        path=f"/{path}",
        query=request.url.query,
        headers=request.headers,
    )
    headers = result.response_headers()
    if result.file is not None:
        return FileResponse(result.file, media_type=result.media_type, headers=headers)
    return Response(
        content=result.body,
        status_code=result.status_code,
        media_type=result.media_type,
        headers=headers,
    )
