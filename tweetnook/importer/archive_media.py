"""Copying an export's media files into the archive's managed media directory."""

from __future__ import annotations

import hashlib
import mimetypes
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from tweetnook.config import XDGPaths
from tweetnook.core.utils import utc_now
from tweetnook.importer.archive_input import _ArchiveInput
from tweetnook.jobs.context import (
    ArchiveWriteTracker,
)
from tweetnook.storage import ArchiveStore


def _url_basename(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    name = Path(urlsplit(value).path).name
    return name or None


def _copy_file_from_archive(
    source: _ArchiveInput, relative_path: str, destination: Path
) -> tuple[str, int]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256()
    byte_size = 0
    with tempfile.NamedTemporaryFile(
        "wb",
        dir=destination.parent,
        delete=False,
        prefix=f"{destination.name}.",
        suffix=".tmp",
    ) as handle:
        temp_path = Path(handle.name)
        try:
            with source.open_binary(relative_path) as input_handle:
                for chunk in iter(lambda: input_handle.read(1024 * 1024), b""):
                    handle.write(chunk)
                    sha.update(chunk)
                    byte_size += len(chunk)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
    temp_path.replace(destination)
    return sha.hexdigest(), byte_size


def _resolve_media_path(base_dir: Path, relative_path: Any) -> Path | None:
    if not isinstance(relative_path, str) or not relative_path:
        return None
    return base_dir / relative_path


def _media_download_complete(base_dir: Path, row: dict[str, Any]) -> bool:
    local_path = _resolve_media_path(base_dir, row.get("local_path"))
    if local_path is None or not local_path.exists():
        return False
    if row.get("media_type") not in {"video", "animated_gif"}:
        return True
    poster_path = _resolve_media_path(base_dir, row.get("thumbnail_local_path"))
    return poster_path is not None and poster_path.exists()


def _copy_exported_media(
    source: _ArchiveInput,
    store: ArchiveStore,
    paths: XDGPaths,
    media_directory: str | None,
    *,
    counts: dict[str, int],
    warnings: list[str],
    progress: Callable[[int, int], None] | None = None,
    item_status: Callable[[str, int, int], None] | None = None,
    limit: int | None = None,
    write_tracker: ArchiveWriteTracker | None = None,
) -> None:
    if not media_directory:
        return
    files = _slice_for_sample(source.iter_files(media_directory), limit=limit)
    if not files:
        return

    rows = store.list_media_rows()
    row_by_asset: dict[tuple[str, str], tuple[dict[str, Any], bool]] = {}
    for row in rows:
        tweet_id = row.get("tweet_id")
        if not isinstance(tweet_id, str) or not tweet_id:
            continue
        media_name = _url_basename(row.get("media_url"))
        if media_name:
            row_by_asset[(tweet_id, media_name)] = (row, False)
        thumbnail_name = _url_basename(row.get("thumbnail_url"))
        if thumbnail_name and thumbnail_name != media_name:
            row_by_asset[(tweet_id, thumbnail_name)] = (row, True)

    pending_updates: dict[str, dict[str, Any]] = {}
    unmatched = 0
    total = len(files)
    for index, relative_path in enumerate(files, start=1):
        if item_status is not None:
            item_status(relative_path, index - 1, total)
        filename = Path(relative_path).name
        if "-" not in filename:
            unmatched += 1
        else:
            tweet_id, asset_name = filename.split("-", 1)
            target = row_by_asset.get((tweet_id, asset_name))
            if target is None:
                unmatched += 1
            else:
                row, is_thumbnail = target
                suffix = "-poster" if is_thumbnail else ""
                media_key = str(row.get("media_key") or "media")
                extension = Path(asset_name).suffix.lower() or ".bin"
                relative_dest = Path("media") / tweet_id / f"{media_key}{suffix}{extension}"
                destination = paths.data_dir / relative_dest
                if not destination.exists():
                    sha256, byte_size = _copy_file_from_archive(source, relative_path, destination)
                    counts["media_files_copied"] += 1
                else:
                    sha256 = str(
                        (row.get("thumbnail_sha256") if is_thumbnail else row.get("sha256")) or ""
                    )
                    byte_size = int(
                        (row.get("thumbnail_byte_size") if is_thumbnail else row.get("byte_size"))
                        or 0
                    )
                content_type = mimetypes.guess_type(destination.name)[0]
                base_row = pending_updates.get(str(row["row_key"]), row)
                local_path = (
                    base_row.get("local_path") if is_thumbnail else relative_dest.as_posix()
                )
                thumbnail_local_path = (
                    relative_dest.as_posix()
                    if is_thumbnail
                    else base_row.get("thumbnail_local_path")
                )
                updated_row = dict(base_row)
                updated_row.update(
                    {
                        "local_path": local_path,
                        "sha256": base_row.get("sha256") if is_thumbnail else sha256 or None,
                        "byte_size": (
                            base_row.get("byte_size") if is_thumbnail else byte_size or None
                        ),
                        "content_type": (
                            base_row.get("content_type") if is_thumbnail else content_type
                        ),
                        "thumbnail_local_path": thumbnail_local_path,
                        "thumbnail_sha256": (sha256 or None)
                        if is_thumbnail
                        else base_row.get("thumbnail_sha256"),
                        "thumbnail_byte_size": (byte_size or None)
                        if is_thumbnail
                        else base_row.get("thumbnail_byte_size"),
                        "thumbnail_content_type": content_type
                        if is_thumbnail
                        else base_row.get("thumbnail_content_type"),
                    }
                )
                complete = _media_download_complete(paths.data_dir, updated_row)
                pending_updates[str(row["row_key"])] = store.build_media_download_update(
                    base_row,
                    download_state="done" if complete else "pending",
                    local_path=local_path,
                    sha256=updated_row.get("sha256"),
                    byte_size=updated_row.get("byte_size"),
                    content_type=updated_row.get("content_type"),
                    thumbnail_local_path=thumbnail_local_path,
                    thumbnail_sha256=updated_row.get("thumbnail_sha256"),
                    thumbnail_byte_size=updated_row.get("thumbnail_byte_size"),
                    thumbnail_content_type=updated_row.get("thumbnail_content_type"),
                    downloaded_at=(
                        utc_now() if complete else (base_row.get("downloaded_at") or None)
                    ),
                    download_error=None,
                )
        if len(pending_updates) >= 100:
            updates = list(pending_updates.values())
            store.merge_rows(updates)
            if write_tracker is not None:
                write_tracker.mark_dirty(rows=len(updates), batches=1)
            pending_updates.clear()
        if progress:
            progress(index, total)
    if pending_updates:
        updates = list(pending_updates.values())
        store.merge_rows(updates)
        if write_tracker is not None:
            write_tracker.mark_dirty(rows=len(updates), batches=1)
    if unmatched:
        warnings.append(f"{unmatched} archive media files did not match normalized media rows.")


def _slice_for_sample(items: list[Any], *, limit: int | None) -> list[Any]:
    if limit is None:
        return items
    return items[:limit]
