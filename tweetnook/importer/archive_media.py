"""Copying an export's media files into the archive's managed media directory."""

from __future__ import annotations

import hashlib
import mimetypes
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from tweetnook.config import XDGPaths
from tweetnook.core.fileio import owned_temp, publish_file
from tweetnook.core.utils import utc_now
from tweetnook.importer.archive_input import ArchiveInput
from tweetnook.storage import ArchiveStore
from tweetnook.storage.common import numeric_media_id


def _url_basename(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    name = Path(urlsplit(value).path).name
    return name or None


def exported_media_names(tweets: Iterable[Any]) -> dict[tuple[str, str], tuple[str, bool]]:
    """Each file an export's posts name: (post id, file name) to (media id, whether it's a poster).

    A saved row may keep a stronger source's URLs, whose file names differ from the export's, so
    the export's own payloads are what say which media a file belongs to.
    """
    names: dict[tuple[str, str], tuple[str, bool]] = {}
    for item in tweets:
        tweet = item.get("tweet") if isinstance(item, dict) else None
        if not isinstance(tweet, dict):
            continue
        tweet_id = str(tweet.get("id_str") or tweet.get("id") or "")
        for block in (tweet.get("extended_entities"), tweet.get("entities")):
            media_items = block.get("media") if isinstance(block, dict) else None
            for media in media_items if isinstance(media_items, list) else ():
                if not isinstance(media, dict):
                    continue
                media_id = media.get("id_str") or media.get("id")
                if not media_id:
                    continue
                variants = (media.get("video_info") or {}).get("variants") or []
                for variant in variants:
                    name = _url_basename(variant.get("url")) if isinstance(variant, dict) else None
                    if name:
                        names[tweet_id, name] = (str(media_id), False)
                still = _url_basename(media.get("media_url_https") or media.get("media_url"))
                if still:
                    names[tweet_id, still] = (str(media_id), bool(variants))
    return names


def _copy_file_from_archive(
    source: ArchiveInput, relative_path: str, destination: Path
) -> tuple[str, int]:
    sha = hashlib.sha256()
    byte_size = 0
    with owned_temp(destination, follow_umask=True) as temporary:
        with temporary.open("wb") as handle, source.open_binary(relative_path) as input_handle:
            for chunk in iter(lambda: input_handle.read(1024 * 1024), b""):
                handle.write(chunk)
                sha.update(chunk)
                byte_size += len(chunk)
        # Flushing per file slows large imports badly, and the export can always be imported again.
        publish_file(temporary, destination, fsync=False)
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


def copy_exported_media(
    source: ArchiveInput,
    store: ArchiveStore,
    paths: XDGPaths,
    media_directory: str | None,
    *,
    exported_names: dict[tuple[str, str], tuple[str, bool]],
    counts: dict[str, int],
    warnings: list[str],
    progress: Callable[[int, int], None] | None = None,
    item_status: Callable[[str, int, int], None] | None = None,
    limit: int | None = None,
) -> None:
    if not media_directory:
        return
    files = slice_for_sample(source.iter_files(media_directory), limit=limit)
    if not files:
        return

    rows = store.list_media_rows()
    row_by_asset: dict[tuple[str, str], tuple[dict[str, Any], bool]] = {}
    row_by_media_id: dict[tuple[str, str], dict[str, Any]] = {}
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
        if media_id := numeric_media_id(row.get("media_key")):
            row_by_media_id[tweet_id, media_id] = row
    for (tweet_id, name), (media_id, is_thumbnail) in exported_names.items():
        row = row_by_media_id.get((tweet_id, media_id))
        if row is not None:
            row_by_asset.setdefault((tweet_id, name), (row, is_thumbnail))

    # A video's two files can arrive either side of a flush, so each row's current state outlives
    # the batch that wrote it.
    current_rows: dict[str, dict[str, Any]] = {}
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
                base_row = current_rows.get(str(row["row_key"]), row)
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
                update = store.build_media_download_update(
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
                current_rows[str(row["row_key"])] = update
                pending_updates[str(row["row_key"])] = update
        if len(pending_updates) >= 100:
            updates = list(pending_updates.values())
            store.merge_rows(updates)
            pending_updates.clear()
        if progress:
            progress(index, total)
    if pending_updates:
        updates = list(pending_updates.values())
        store.merge_rows(updates)
    if unmatched:
        warnings.append(f"{unmatched} archive media files did not match normalized media rows.")


def slice_for_sample(items: list[Any], *, limit: int | None) -> list[Any]:
    if limit is None:
        return items
    return items[:limit]
