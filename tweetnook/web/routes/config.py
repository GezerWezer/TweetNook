"""Configuration management endpoints."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from tweetnook.web.deps import verify_credentials

router = APIRouter()
MASKED_SECRET = "********"
SECRET_FIELDS = (
    ("auth", "auth_token"),
    ("auth", "ct0"),
    ("web", "password_hash"),
    ("tagging", "api_key"),
)


class ConfigUpdateRequest(BaseModel):
    changes: dict[str, Any]


def _mask_secrets(data: dict) -> dict:
    for section, field in SECRET_FIELDS:
        section_data = data.get(section)
        if isinstance(section_data, dict) and section_data.get(field):
            section_data[field] = MASKED_SECRET
    return data


@router.get("/api/config")
def api_get_config(_auth: Annotated[bool, Depends(verify_credentials)]):
    try:
        from tweetnook.config import get_explicit_config_fields, load_config

        config, paths = load_config()
        return {
            "values": _mask_secrets(config.model_dump()),
            "explicit": get_explicit_config_fields(paths),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/config/defaults")
def api_get_config_defaults(_auth: Annotated[bool, Depends(verify_credentials)]):
    try:
        from tweetnook.config import AppConfig

        return AppConfig().model_dump()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/config/schema")
def api_get_config_schema(_auth: Annotated[bool, Depends(verify_credentials)]):
    try:
        from tweetnook.config import get_config_ui_schema

        return get_config_ui_schema()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/config")
def api_post_config(
    req: ConfigUpdateRequest,
    _auth: Annotated[bool, Depends(verify_credentials)],
):
    try:
        from tweetnook.config import (
            get_explicit_config_fields,
            load_config,
            update_config_values,
        )

        _, paths = load_config()
        if "web.password_hash" in req.changes:
            raise ValueError("web.password_hash cannot be changed through this endpoint")
        secret_paths = {f"{section}.{field}" for section, field in SECRET_FIELDS}
        if any(
            path in secret_paths and value == MASKED_SECRET for path, value in req.changes.items()
        ):
            raise ValueError("Masked secret placeholders cannot be saved")

        update_config_values(paths, req.changes)
        config, refreshed_paths = load_config()
        return {
            "values": _mask_secrets(config.model_dump()),
            "explicit": get_explicit_config_fields(refreshed_paths),
        }
    except Exception as e:
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=400, detail=str(e)) from e
