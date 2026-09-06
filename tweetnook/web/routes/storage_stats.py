"""Compatibility endpoint for the shared storage statistics section."""

from typing import Any

from fastapi import APIRouter, Depends

from tweetnook.stats import build_stats_section
from tweetnook.stats import format_bytes as _format_bytes
from tweetnook.storage import ArchiveStore
from tweetnook.web.deps import require_store, verify_credentials

router = APIRouter(
    prefix="/api/storage",
    tags=["storage"],
    dependencies=[Depends(verify_credentials)],
)


def format_bytes(size: float) -> str:
    """Retain the existing formatting helper for API callers and tests."""
    return _format_bytes(size)


@router.get("/breakdown")
def get_storage_breakdown(
    store: ArchiveStore = Depends(require_store),  # noqa: B008 - FastAPI dependency
) -> dict[str, Any]:
    """Return the legacy storage payload from the shared report collector."""
    return build_stats_section(store, "storage").data
