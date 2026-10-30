"""JSON export."""

from __future__ import annotations

import json
from pathlib import Path

from tweetnook.core.collection_names import normalize_collection_name
from tweetnook.core.fileio import owned_temp, publish_file
from tweetnook.storage import ArchiveStore


def export_json_archive(store: ArchiveStore, *, collection: str, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = store.export_rows(normalize_collection_name(collection))
    with owned_temp(out_path) as temporary:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        publish_file(temporary, out_path)
    return out_path
