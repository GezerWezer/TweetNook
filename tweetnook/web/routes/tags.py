"""Tag management endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from tweetnook.storage import ArchiveStore
from tweetnook.web.deps import require_store, verify_credentials

router = APIRouter()


class TagUpdateRequest(BaseModel):
    tags: list[str]
    description: str | None = None


class TagMergeRequest(BaseModel):
    primary_tag: str
    merge_tags: list[str]


@router.delete("/api/tags/{tweet_id}")
def api_delete_tag(
    tweet_id: str,
    store: ArchiveStore = Depends(require_store),  # noqa: B008 - FastAPI dependency
    _auth: bool = Depends(verify_credentials),
) -> dict[str, bool]:
    try:
        store.delete_media_tag(tweet_id)
        return {"success": True}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.put("/api/tags/{tweet_id}")
def api_update_tag(
    tweet_id: str,
    req: TagUpdateRequest,
    store: ArchiveStore = Depends(require_store),  # noqa: B008 - FastAPI dependency
    _auth: bool = Depends(verify_credentials),
) -> dict[str, bool]:
    try:
        store.update_media_tags(tweet_id, req.tags, description=req.description)
        return {"success": True}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/api/tags/global/{tag}")
def api_delete_global_tag(
    tag: str,
    store: ArchiveStore = Depends(require_store),  # noqa: B008 - FastAPI dependency
    _auth: bool = Depends(verify_credentials),
) -> dict[str, bool]:
    try:
        store.delete_global_tag(tag)
        return {"success": True}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/tags/merge")
def api_merge_tags(
    req: TagMergeRequest,
    store: ArchiveStore = Depends(require_store),  # noqa: B008 - FastAPI dependency
    _auth: bool = Depends(verify_credentials),
) -> dict[str, bool]:
    try:
        store.merge_global_tags(req.primary_tag, req.merge_tags)
        return {"success": True}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/api/tags/autocomplete")
def api_tags_autocomplete(
    q: str = "",
    store: ArchiveStore = Depends(require_store),  # noqa: B008 - FastAPI dependency
    _auth: bool = Depends(verify_credentials),
) -> dict[str, list[dict]]:
    try:
        results = store.get_tag_counts(query=q)
        return {"tags": results}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/api/tags/stats")
def api_tags_stats(
    store: ArchiveStore = Depends(require_store),  # noqa: B008 - FastAPI dependency
    _auth: bool = Depends(verify_credentials),
) -> dict[str, list[dict]]:
    try:
        results = store.get_tag_counts(limit=-1)
        return {"tags": results}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
