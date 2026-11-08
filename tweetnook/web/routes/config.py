"""Configuration management endpoints."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from tweetnook.config import config_error_message
from tweetnook.web.deps import MASKED_SECRET, verify_credentials
from tweetnook.web.runtime_config import save_server_config

router = APIRouter()
SECRET_FIELDS = (
    ("auth", "auth_token"),
    ("auth", "ct0"),
    ("web", "password_hash"),
    ("tagging", "api_key"),
    ("replay", "password"),
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
    from tweetnook.config import get_explicit_config_fields, load_config

    config, paths = load_config()
    return {
        "values": _mask_secrets(config.model_dump()),
        "explicit": get_explicit_config_fields(paths),
    }


@router.get("/api/config/defaults")
def api_get_config_defaults(_auth: Annotated[bool, Depends(verify_credentials)]):
    from tweetnook.config import AppConfig

    return AppConfig().model_dump()


@router.get("/api/config/schema")
def api_get_config_schema(_auth: Annotated[bool, Depends(verify_credentials)]):
    from tweetnook.web.config_schema import get_config_ui_schema

    return get_config_ui_schema()


@router.post("/api/config")
def api_post_config(
    req: ConfigUpdateRequest,
    _auth: Annotated[bool, Depends(verify_credentials)],
):
    try:
        from tweetnook.config import (
            get_explicit_config_fields,
            load_config,
        )

        _, paths = load_config()
        if "web.password_hash" in req.changes:
            raise ValueError("web.password_hash cannot be changed through this endpoint")
        secret_paths = {f"{section}.{field}" for section, field in SECRET_FIELDS}
        if any(
            path in secret_paths and value == MASKED_SECRET for path, value in req.changes.items()
        ):
            raise ValueError("Masked secret placeholders cannot be saved")
        if req.changes.get("web.dev_replay_source") is True:
            from tweetnook.web.deps import server_state

            if not server_state.get("password_hash"):
                raise ValueError(
                    "Set a Web password before serving this archive as a replay source."
                )

        config = save_server_config(paths, req.changes)
        return {
            "values": _mask_secrets(config.model_dump()),
            "explicit": get_explicit_config_fields(paths),
        }
    # Invalid values (including Pydantic errors) and an unwritable config file are both
    # something the person saving can fix, so Settings shows them.
    except (ValueError, OSError) as e:
        raise HTTPException(status_code=400, detail=config_error_message(e)) from e
