"""Import manifests: captured counts and warnings, enrichment status, and the files an
import owns."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from tweetnook.core.utils import utc_now
from tweetnook.storage import ArchiveStore
from tweetnook.storage.common import ARCHIVE_SOURCE
from tweetnook.storage.record_types import RecordType


def manifest_generation_date(manifest: dict[str, Any]) -> str | None:
    archive_info = manifest.get("archiveInfo") or {}
    generation = archive_info.get("generationDate")
    return generation if isinstance(generation, str) and generation else None


def record_archive_capture(
    store: ArchiveStore,
    operation: str,
    filename: str,
    payload: Any,
    *,
    archive_digest: str,
) -> None:
    capture_key = hashlib.sha256(f"{archive_digest}\0{operation}\0{filename}".encode()).hexdigest()
    store.append_raw_capture(
        operation,
        filename,
        None,
        200,
        payload,
        source=ARCHIVE_SOURCE,
        capture_key=capture_key,
    )


def _safe_relative_data_path(relative_path: str) -> Path | None:
    relative = Path(relative_path)
    if relative.is_absolute():
        return None
    if any(part == ".." for part in relative.parts):
        return None
    return relative


def remove_archive_owned_files(base_dir: Path, relative_paths: list[str]) -> int:
    removed = 0
    for relative_path in sorted(set(relative_paths)):
        relative = _safe_relative_data_path(relative_path)
        if relative is None or relative.parts[:1] != ("media",):
            continue
        destination = base_dir / relative
        if not destination.exists() or not destination.is_file():
            continue
        destination.unlink()
        removed += 1
        parent = destination.parent
        while parent != base_dir:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
    return removed


def initial_counts() -> dict[str, int]:
    return {
        "authored_tweets": 0,
        "deleted_authored_tweets": 0,
        "likes": 0,
        "media_files_copied": 0,
    }


def manifest_counts(manifest_row: dict[str, Any] | None) -> dict[str, int]:
    if not manifest_row:
        return initial_counts()
    raw = manifest_row.get("counts_json")
    if not isinstance(raw, str) or not raw:
        return initial_counts()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return initial_counts()
    if not isinstance(parsed, dict):
        return initial_counts()
    counts = initial_counts()
    for key in counts:
        value = parsed.get(key)
        if isinstance(value, int):
            counts[key] = value
    return counts


def _manifest_all_counts(manifest_row: dict[str, Any] | None) -> dict[str, Any]:
    if not manifest_row:
        return initial_counts()
    raw = manifest_row.get("counts_json")
    if not isinstance(raw, str) or not raw:
        return initial_counts()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return initial_counts()
    return parsed if isinstance(parsed, dict) else initial_counts()


def manifest_warnings(manifest_row: dict[str, Any] | None) -> list[str]:
    if not manifest_row:
        return []
    raw = manifest_row.get("warnings_json")
    if not isinstance(raw, str) or not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, str) and item]


def list_import_manifest_rows(store: ArchiveStore) -> list[dict[str, Any]]:
    return store._query(f"record_type = '{RecordType.IMPORT_MANIFEST}' AND status = 'completed'")


def finalize_import_enrichment_status(
    store: ArchiveStore,
    manifest_row: dict[str, Any],
    *,
    followup_status: str,
    aborted_reason: str | None = None,
    count_updates: dict[str, Any] | None = None,
) -> None:
    digest = manifest_row.get("archive_digest")
    if not isinstance(digest, str) or not digest:
        return
    counts = _manifest_all_counts(manifest_row)
    if count_updates:
        counts.update(count_updates)
    counts["pending_enrichment"] = store.count_incomplete_initial_enrichment()
    counts["transient_due"] = store.count_due_transient_enrichment()
    counts["transient_delayed"] = store.count_delayed_transient_enrichment()
    counts.update(store.enrichment_status_counts())
    store.set_import_manifest(
        digest,
        archive_generation_date=manifest_row.get("archive_generation_date"),
        status=str(manifest_row.get("status") or "completed"),
        import_started_at=manifest_row.get("import_started_at"),
        import_completed_at=utc_now(),
        warnings=manifest_warnings(manifest_row),
        counts=counts,
        enrichment_followup_status=followup_status,
        enrichment_aborted_reason=aborted_reason,
    )


def aggregate_import_counts(manifest_rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = initial_counts()
    for row in manifest_rows:
        parsed = manifest_counts(row)
        for key, value in parsed.items():
            counts[key] += value
    return counts
