"""Configuration management endpoints."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from tweetnook.config import config_error_message
from tweetnook.web.deps import SECRET_FIELDS, mask_secrets, verify_credentials, without_masked
from tweetnook.web.runtime_config import save_server_config

router = APIRouter()


class ConfigUpdateRequest(BaseModel):
    changes: dict[str, Any]


def _mask_secrets(data: dict) -> dict:
    for section in SECRET_FIELDS:
        if isinstance(data.get(section), dict):
            mask_secrets(section, data[section])
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

        saved, paths = load_config()
        changes = without_masked(req.changes)
        if (
            "replay.url" in changes
            and changes["replay.url"] != saved.replay.url
            and "replay.password" not in changes
        ):
            # The saved password is only ever sent back to the source it was saved for.
            changes["replay.password"] = None
        if "web.password_hash" in changes:
            raise ValueError("web.password_hash cannot be changed through this endpoint")
        if changes.get("web.dev_replay_source") is True:
            from tweetnook.web.deps import server_state

            if not server_state.get("password_hash"):
                raise ValueError(
                    "Set a Web password before serving this archive as a replay source."
                )

        config = save_server_config(paths, changes)
        return {
            "values": _mask_secrets(config.model_dump()),
            "explicit": get_explicit_config_fields(paths),
        }
    # Invalid values (including Pydantic errors) and an unwritable config file are both
    # something the person saving can fix, so Settings shows them.
    except (ValueError, OSError) as e:
        raise HTTPException(status_code=400, detail=config_error_message(e)) from e
