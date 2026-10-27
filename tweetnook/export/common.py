"""Shared export/view helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from tweetnook.core.collection_names import display_collection_name


def default_export_path(base_dir: Path, collection: str, *, extension: str) -> Path:
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    label = display_collection_name(collection)
    return base_dir / f"export-{label}-{stamp}.{extension}"
