"""Dedicated Settings API for the optional direct-Gemini tagger."""

from __future__ import annotations

from io import StringIO
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from rich.console import Console

from tweetnook.automated_tagging import (
    automated_tagging_installed,
    list_models,
    model_is_compatible,
)
from tweetnook.config import TaggingConfig
from tweetnook.extractor import extract_status_id_from_url
from tweetnook.gemini_accounting import AIUsageLedger
from tweetnook.search import SearchQueryError, search_posts
from tweetnook.web.deps import require_store, server_state, verify_credentials

router = APIRouter()
MASKED_SECRET = "********"


class AutomatedTaggingUpdate(BaseModel):
    values: dict[str, Any]


class ModelCatalogRequest(BaseModel):
    api_key: str | None = None
    api_mode: Literal["free", "paid"] = "free"
    processing_tier: Literal["standard", "flex"] = "standard"


class AutomatedTaggingTestRequest(BaseModel):
    tweet_id: str = Field(min_length=1, max_length=30, pattern=r"^\d+$")
    values: dict[str, Any]


def _public_values(config: TaggingConfig) -> dict[str, Any]:
    values = config.model_dump()
    if values.get("api_key"):
        values["api_key"] = MASKED_SECRET
    return values


def _load():
    from tweetnook.config import load_config

    return load_config()


def _validated_values(values: dict[str, Any], current: TaggingConfig) -> TaggingConfig:
    allowed = set(TaggingConfig.model_fields)
    unknown = set(values) - allowed
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown automated-tagging fields: {', '.join(sorted(unknown))}",
        )
    submitted = dict(values)
    if submitted.get("api_key") == MASKED_SECRET:
        submitted["api_key"] = current.api_key
    try:
        return TaggingConfig.model_validate({**current.model_dump(), **submitted})
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/api/automated-tagging")
def get_automated_tagging(_auth: Annotated[bool, Depends(verify_credentials)]):
    config, paths = _load()
    accounting = AIUsageLedger(paths.ai_usage_dir).statistics()
    return {
        "installed": automated_tagging_installed(),
        "values": _public_values(config.tagging),
        "accounting": accounting,
    }


@router.put("/api/automated-tagging")
def put_automated_tagging(
    request: AutomatedTaggingUpdate,
    _auth: Annotated[bool, Depends(verify_credentials)],
):
    if not automated_tagging_installed():
        raise HTTPException(status_code=404, detail="Automated tagging is not installed")
    config, paths = _load()
    validated = _validated_values(request.values, config.tagging)
    try:
        from tweetnook.config import update_config_values

        update_config_values(
            paths,
            {f"tagging.{key}": value for key, value in validated.model_dump().items()},
        )
        refreshed, _ = _load()
        server_state["config"] = refreshed
        return {
            "installed": True,
            "values": _public_values(refreshed.tagging),
            "accounting": AIUsageLedger(paths.ai_usage_dir).statistics(),
        }
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/api/automated-tagging/tweets")
def get_automated_tagging_tweets(
    _auth: Annotated[bool, Depends(verify_credentials)],
    q: str = Query("", max_length=500),
    store=Depends(require_store),  # noqa: B008
):
    """Return compact tweet choices for the Settings dry-run picker."""
    if store is None:
        raise HTTPException(status_code=503, detail="Archive is not available")
    query = q.strip()
    target_id = extract_status_id_from_url(query) or (query if query.isdigit() else None)
    if target_id:
        row = store.conn.execute(
            "SELECT tweet_id, author_display_name, author_username, text "
            "FROM archive INDEXED BY idx_archive_tweet_id "
            "WHERE record_type = 'tweet_object' AND tweet_id = ? LIMIT 1",
            (target_id,),
        ).fetchone()
        rows = [dict(row)] if row is not None else []
    else:
        try:
            rows = search_posts(store, query or None, page=1, limit=8).rows
        except SearchQueryError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    tweet_ids = [str(row.get("tweet_id")) for row in rows if row.get("tweet_id")]
    media_ids: set[str] = set()
    if tweet_ids:
        placeholders = ", ".join("?" for _tweet_id in tweet_ids)
        media_ids = {
            str(row["tweet_id"])
            for row in store.conn.execute(
                f"SELECT DISTINCT tweet_id FROM archive INDEXED BY idx_archive_tweet_id "
                f"WHERE record_type = 'media' AND tweet_id IN ({placeholders})",
                tweet_ids,
            ).fetchall()
        }
    return {
        "tweets": [
            {
                "tweet_id": str(row.get("tweet_id") or ""),
                "author_display_name": str(row.get("author_display_name") or ""),
                "author_username": str(row.get("author_username") or ""),
                "text": str(row.get("text") or ""),
                "has_media": str(row.get("tweet_id") or "") in media_ids,
            }
            for row in rows
            if row.get("tweet_id")
        ]
    }


@router.post("/api/automated-tagging/test")
async def post_automated_tagging_test(
    request: AutomatedTaggingTestRequest,
    _auth: Annotated[bool, Depends(verify_credentials)],
    store=Depends(require_store),  # noqa: B008
):
    """Run the CLI-equivalent test mode using unsaved form settings."""
    if not automated_tagging_installed():
        raise HTTPException(status_code=404, detail="Automated tagging is not installed")
    if store is None:
        raise HTTPException(status_code=503, detail="Archive is not available")
    config, paths = _load()
    values = dict(request.values)
    values["enabled"] = True
    validated = _validated_values(values, config.tagging)
    if not validated.api_key:
        raise HTTPException(status_code=400, detail="Enter a Gemini API key first")

    from tweetnook.tagging import TaggingPreview, tag_media_tweets

    test_config = config.model_copy(update={"tagging": validated})
    previews: list[TaggingPreview] = []
    output = StringIO()
    console = Console(file=output, color_system=None, width=120)
    generated = await tag_media_tweets(
        store=store,
        config=test_config,
        paths=paths,
        console=console,
        tweet_ids=[request.tweet_id],
        dry_run=True,
        preview_results=previews,
    )
    return {
        "success": generated == 1 and bool(previews),
        "result": previews[0].model_dump() if previews else None,
        "output": output.getvalue().strip(),
        "accounting": AIUsageLedger(paths.ai_usage_dir).statistics(),
        "notice": (
            "Settings and generated tags were not saved. The real Gemini request(s) may still "
            "consume quota or paid spend, which TweetNook accounts normally."
        ),
    }


@router.post("/api/automated-tagging/models")
def post_automated_tagging_models(
    request: ModelCatalogRequest,
    _auth: Annotated[bool, Depends(verify_credentials)],
):
    if not automated_tagging_installed():
        raise HTTPException(status_code=404, detail="Automated tagging is not installed")
    config, _paths = _load()
    api_key = (
        config.tagging.api_key if request.api_key in {None, MASKED_SECRET} else request.api_key
    )
    if not api_key:
        raise HTTPException(status_code=400, detail="Enter a Gemini API key first")
    try:
        catalog = list_models(api_key)
    except Exception as error:
        raise HTTPException(
            status_code=502, detail=f"Could not load Gemini models: {error}"
        ) from error

    return {
        "models": [
            model.model_dump()
            for model in catalog
            if model_is_compatible(
                model,
                api_mode=request.api_mode,
                processing_tier=request.processing_tier,
            )
        ]
    }
