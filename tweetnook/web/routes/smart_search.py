"""Dedicated Settings API for local CLIP/SigLIP Smart Search."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from tweetnook.config import SmartSearchConfig, config_error_message, load_config
from tweetnook.smart_search.models import list_supported_models
from tweetnook.smart_search.sdk import smart_search_installed
from tweetnook.web.deps import require_store, verify_credentials
from tweetnook.web.runtime_config import save_server_config

router = APIRouter()


class SmartSearchUpdate(BaseModel):
    values: dict[str, Any]


def _validated_values(values: dict[str, Any], current: SmartSearchConfig) -> SmartSearchConfig:
    allowed = set(SmartSearchConfig.model_fields)
    unknown = set(values) - allowed
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown smart-search fields: {', '.join(sorted(unknown))}",
        )
    try:
        return SmartSearchConfig.model_validate({**current.model_dump(), **values})
    except Exception as error:
        raise HTTPException(status_code=400, detail=config_error_message(error)) from error


@router.get("/api/smart-search")
def get_smart_search(
    _auth: Annotated[bool, Depends(verify_credentials)] = True,
):
    config, _paths = load_config()
    return {
        "installed": smart_search_installed(),
        "values": config.smart_search.model_dump(),
        "models": list_supported_models(),
    }


@router.get("/api/smart-search/stats")
def get_smart_search_stats(
    store=Depends(require_store),  # noqa: B008
    _auth: Annotated[bool, Depends(verify_credentials)] = True,
):
    config, _paths = load_config()
    stats = store.embedding_stats(
        config.smart_search.model,
        include_video_thumbnails=config.smart_search.include_video_thumbnails,
        include_quoted_media=config.smart_search.include_quoted_media,
        include_thread_media=config.smart_search.include_thread_media,
    )
    return {"stats": stats}


@router.put("/api/smart-search")
def put_smart_search(
    request: SmartSearchUpdate,
    _auth: Annotated[bool, Depends(verify_credentials)] = True,
):
    config, paths = load_config()
    validated = _validated_values(request.values, config.smart_search)
    try:
        refreshed = save_server_config(
            paths,
            {f"smart_search.{key}": getattr(validated, key) for key in request.values},
        )
    except Exception as error:
        raise HTTPException(status_code=400, detail=config_error_message(error)) from error
    return {
        "installed": smart_search_installed(),
        "values": refreshed.smart_search.model_dump(),
        "models": list_supported_models(),
    }
