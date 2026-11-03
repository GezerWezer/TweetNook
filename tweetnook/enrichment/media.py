"""Media download helpers."""

from __future__ import annotations

import asyncio
import mimetypes
import re
from contextlib import suppress
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from rich.console import Console

from tweetnook.config import DEFAULT_USER_AGENT, AppConfig, XDGPaths
from tweetnook.core.dev_replay import client_transport
from tweetnook.core.fileio import owned_temp, publish_file
from tweetnook.core.utils import utc_now
from tweetnook.enrichment.avatars import (
    CoreAvatarState,
    download_core_avatar,
    prepare_core_avatars,
)
from tweetnook.jobs.context import locked_archive_job
from tweetnook.jobs.interactive import emit_status, progress_callback, status_printer
from tweetnook.jobs.pipeline import current_pipeline
from tweetnook.storage.backend import ArchiveStore

_CONTENT_TYPE_EXTENSIONS = {
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
}
_UPDATE_BATCH_SIZE = 100
_REMOTE_FAILURES = (httpx.HTTPError, httpx.InvalidURL)
_URL_EXTENSION = re.compile(r"\.[a-z0-9]{1,16}")
_AVATAR_ACTIVITY = "Downloading avatars"


@dataclass(slots=True)
class MediaDownloadResult:
    processed: int = 0
    downloaded: int = 0
    failed: int = 0
    downloaded_bytes: int = 0
    avatars_downloaded: int = 0
    avatars_failed: int = 0


def _pipeline_outcomes(result: MediaDownloadResult, *, final: bool = False) -> str:
    parts: list[str] = []
    if final or result.downloaded:
        parts.append(f"{result.downloaded} downloaded")
    if result.failed:
        parts.append(f"{result.failed} failed")
    if result.avatars_downloaded:
        parts.append(f"{result.avatars_downloaded} avatars")
    if result.avatars_failed:
        parts.append(f"{result.avatars_failed} avatars unavailable")
    if result.downloaded_bytes:
        parts.append(f"{result.downloaded_bytes / (1024 * 1024):.1f} MiB")
    return " · ".join(parts)


def _media_activity(row: dict[str, object]) -> str:
    media_type = str(row.get("media_type") or "media").replace("_", " ")
    return f"Downloading {media_type}"


def _safe_media_stem(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def _content_extension(content_type: str | None, url: str) -> str:
    if content_type:
        base_type = content_type.split(";", 1)[0].strip().lower()
        if base_type in _CONTENT_TYPE_EXTENSIONS:
            return _CONTENT_TYPE_EXTENSIONS[base_type]
        guessed = mimetypes.guess_extension(base_type)
        if guessed:
            return ".jpg" if guessed == ".jpe" else guessed
    # The URL's suffix becomes part of a file name, and a saved URL can hold anything.
    suffix = Path(urlsplit(url).path).suffix.lower()
    return suffix if _URL_EXTENSION.fullmatch(suffix) else ".bin"


def _photo_download_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.netloc != "pbs.twimg.com":
        return url
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["name"] = "orig"
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


def _asset_url(row: dict[str, object]) -> str | None:
    media_url = row.get("media_url")
    return media_url if isinstance(media_url, str) and media_url else None


def _request_url(row: dict[str, object], url: str) -> str:
    if row.get("media_type") == "photo" and row.get("source") != "link_card":
        return _photo_download_url(url)
    return url


def _poster_url(row: dict[str, object]) -> str | None:
    media_type = row.get("media_type")
    if media_type not in {"video", "animated_gif"}:
        return None
    thumbnail_url = row.get("thumbnail_url")
    return thumbnail_url if isinstance(thumbnail_url, str) and thumbnail_url else None


def _target_path(
    base_dir: Path, row: dict[str, object], *, url: str, suffix: str
) -> tuple[str, Path]:
    tweet_id = str(row.get("tweet_id") or "unknown")
    media_key = _safe_media_stem(str(row.get("media_key") or "media"))
    extension = _content_extension(None, url)
    relative = Path("media") / tweet_id / f"{media_key}{suffix}{extension}"
    return relative.as_posix(), base_dir / relative


async def _download_file(
    client: httpx.AsyncClient,
    *,
    url: str,
    destination: Path,
) -> tuple[Path, str, int, str | None]:
    content_type: str | None = None
    file_hash = sha256()
    byte_size = 0
    with owned_temp(destination, follow_umask=True) as temporary:
        with temporary.open("wb") as handle:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type")
                async for chunk in response.aiter_bytes():
                    handle.write(chunk)
                    file_hash.update(chunk)
                    byte_size += len(chunk)
        final_destination = destination
        if content_type:
            extension = _content_extension(content_type, url)
            if destination.suffix.lower() != extension:
                final_destination = destination.with_suffix(extension)
        await asyncio.to_thread(publish_file, temporary, final_destination)
        return final_destination, file_hash.hexdigest(), byte_size, content_type


def _asset_saved(base_dir: Path, relative_path: object) -> bool:
    return (
        isinstance(relative_path, str)
        and bool(relative_path)
        and (base_dir / relative_path).is_file()
    )


async def _obtain_missing_assets(
    client: httpx.AsyncClient,
    row: dict[str, object],
    base_dir: Path,
) -> tuple[dict[str, object], int, str | None]:
    """Download whichever of a row's main file and poster aren't saved yet.

    The two files fail independently: a provider that has stopped serving one must not block
    the other or discard it, so each is attempted and kept on its own. Returns the row's asset
    state, the bytes transferred, and the first failure. Only a failed transfer or a saved URL
    that can't be parsed is the row's failure; a local error isn't about this row and stops
    the job.
    """

    state: dict[str, object] = {
        "local_path": row.get("local_path"),
        "sha256": row.get("sha256"),
        "byte_size": row.get("byte_size"),
        "content_type": row.get("content_type"),
        "thumbnail_local_path": row.get("thumbnail_local_path"),
        "thumbnail_sha256": row.get("thumbnail_sha256"),
        "thumbnail_byte_size": row.get("thumbnail_byte_size"),
        "thumbnail_content_type": row.get("thumbnail_content_type"),
    }
    transferred = 0
    error: str | None = None

    async def obtain(saved_url: str, suffix: str, field_prefix: str, path_field: str) -> None:
        nonlocal transferred, error
        # This block only parses the URL, so its ValueError can't hide a local failure. The
        # row stays selectable until it is recorded, so raising would stop every later run here.
        try:
            url = _request_url(row, saved_url)
            _, destination = _target_path(base_dir, row, url=url, suffix=suffix)
        except ValueError as exc:
            error = error or f"invalid URL: {exc}"
            return
        try:
            final_path, file_hash, byte_size, content_type = await _download_file(
                client, url=url, destination=destination
            )
        except _REMOTE_FAILURES as exc:
            error = error or str(exc)
            return
        state[path_field] = final_path.relative_to(base_dir).as_posix()
        state[f"{field_prefix}sha256"] = file_hash
        state[f"{field_prefix}byte_size"] = byte_size
        state[f"{field_prefix}content_type"] = content_type
        transferred += byte_size

    if not _asset_saved(base_dir, row.get("local_path")):
        asset_url = _asset_url(row)
        if asset_url is None:
            error = "missing media download URL"
        else:
            await obtain(asset_url, "", "", "local_path")
    poster_url = _poster_url(row)
    if poster_url and not _asset_saved(base_dir, row.get("thumbnail_local_path")):
        await obtain(poster_url, "-poster", "thumbnail_", "thumbnail_local_path")
    return state, transferred, error


def _row_update(
    store: ArchiveStore, row: dict[str, object], state: dict[str, object], error: str | None
) -> dict[str, object]:
    return store.build_media_download_update(
        row,
        download_state="failed" if error else "done",
        downloaded_at=(row.get("downloaded_at") or None) if error else utc_now(),
        download_error=error,
        **state,
    )


async def download_media(
    *,
    limit: int | None = None,
    photos_only: bool = False,
    retry_failed: bool = False,
    config: AppConfig | None = None,
    paths: XDGPaths | None = None,
    console: Console | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> MediaDownloadResult:
    console = console or Console(stderr=True)
    pipeline = current_pipeline()
    status = None if pipeline.active else status_printer(console, "media download")
    step_key = "media"
    pipeline.add_step(
        step_key,
        "Media",
        total=1,
        unit="items",
        detail="pending archived media selected for local download",
        rate_unit="items/s",
    )
    async with locked_archive_job(config=config, paths=paths, console=console) as job:
        config = job.config
        paths = job.paths
        store = job.store
        emit_status(status, "checking saved link-card images")
        queued = store.restore_link_card_images() + store.backfill_link_card_media()
        if queued:
            emit_status(status, f"queued {queued} saved link-card images")
        states = {"pending"}
        if retry_failed:
            states.add("failed")
        media_types = {"photo"} if photos_only else None
        emit_status(status, "loading pending media rows")
        rows = store.list_media_rows(states=states, media_types=media_types, limit=limit)
        # Core avatars are part of the media step whatever web.fetch_avatars says; that
        # setting governs only the route's on-demand downloads.
        avatars_dir = paths.media_dir / "avatars"
        emit_status(status, "checking avatars for saved and quoted posts")
        avatar_state = CoreAvatarState.load(paths.core_avatar_state_file)
        avatar_plan = prepare_core_avatars(store, avatars_dir, avatar_state)
        avatar_targets = avatar_plan.targets
        if avatar_plan.adopted:
            emit_status(status, f"kept {avatar_plan.adopted} cached avatars")
        if avatar_plan.released:
            emit_status(status, f"returned {avatar_plan.released} avatars to the cache")
        total = len(rows) + len(avatar_targets)
        result = MediaDownloadResult()
        if not total:
            with suppress(OSError):
                avatar_state.save(paths.core_avatar_state_file)
            emit_status(status, "no media items pending download")
            pipeline.skip_step(step_key, "no media items requiring download")
            return result
        filters: list[str] = []
        if photos_only:
            filters.append("photos only")
        if retry_failed:
            filters.append("including failed")
        filter_suffix = f" ({', '.join(filters)})" if filters else ""
        limit_suffix = "" if limit is None else f" (limit {limit})"
        emit_status(status, f"downloading {len(rows)} media items{filter_suffix}{limit_suffix}")
        if avatar_targets:
            emit_status(status, f"downloading {len(avatar_targets)} avatars")
        if pipeline.active:
            scope = "pending media items"
            if filters:
                scope += f" · {', '.join(filters)}"
            if avatar_targets:
                scope += " and avatars"
            pipeline.add_step(
                step_key,
                "Media",
                total=total,
                unit="items",
                detail=scope,
                rate_unit="items/s",
            )
            pipeline.start_step(
                step_key,
                activity=_media_activity(rows[0]) if rows else _AVATAR_ACTIVITY,
            )

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=max(config.sync.timeout, 60.0),
            transport=client_transport(transport),
            headers={"User-Agent": DEFAULT_USER_AGENT},
        ) as client:
            pending_updates: list[dict[str, object]] = []

            def flush_updates() -> None:
                if not pending_updates:
                    return
                updates = pending_updates.copy()
                store.merge_rows(updates)
                pending_updates.clear()

            try:
                with progress_callback(
                    console,
                    label="media download",
                    total=total,
                    unit="items",
                ) as progress:
                    for index, row in enumerate(rows, start=1):
                        try:
                            result.processed += 1
                            pipeline.update_step(
                                step_key,
                                completed=index - 1,
                                activity=_media_activity(row),
                                counters=_pipeline_outcomes(result),
                            )
                            state, transferred, error = await _obtain_missing_assets(
                                client, row, paths.data_dir
                            )
                            result.downloaded_bytes += transferred
                            pending_updates.append(_row_update(store, row, state, error))
                            if error is None:
                                result.downloaded += 1
                            else:
                                result.failed += 1
                                if pipeline.active:
                                    pipeline.issue(
                                        f"Media {row['row_key']}: {error}",
                                        dedupe_key="media:download-failure",
                                    )
                                elif console:
                                    console.print(
                                        f"media {row['row_key']}: failed ({error})",
                                        highlight=False,
                                    )
                            if len(pending_updates) >= _UPDATE_BATCH_SIZE:
                                flush_updates()
                        finally:
                            pipeline.update_step(
                                step_key,
                                completed=index,
                                counters=_pipeline_outcomes(result),
                            )
                            if progress is not None:
                                progress(index, total)

                    flush_updates()
                    for index, target in enumerate(avatar_targets, start=len(rows) + 1):
                        try:
                            result.processed += 1
                            pipeline.update_step(
                                step_key,
                                completed=index - 1,
                                activity=_AVATAR_ACTIVITY,
                                counters=_pipeline_outcomes(result),
                            )
                            outcome = await download_core_avatar(client, avatars_dir, target)
                            if outcome.saved:
                                result.avatars_downloaded += 1
                                result.downloaded_bytes += outcome.content_bytes
                                avatar_state.saved[target.author_id] = target.source_urls[0]
                                avatar_state.unavailable.pop(target.author_id, None)
                            else:
                                # Deleted and suspended accounts are expected here, so a
                                # missing avatar is counted rather than raised as an issue.
                                result.avatars_failed += 1
                                if outcome.unavailable:
                                    avatar_state.unavailable[target.author_id] = target.source_urls[
                                        0
                                    ]
                        finally:
                            pipeline.update_step(
                                step_key,
                                completed=index,
                                counters=_pipeline_outcomes(result),
                            )
                            if progress is not None:
                                progress(index, total)
            finally:
                # Rows already finished stay true when a later one stops the job. The store
                # may be what failed, and that must not replace the real error.
                with suppress(Exception):
                    flush_updates()
                with suppress(OSError):
                    avatar_state.save(paths.core_avatar_state_file)
        pipeline.complete_step(
            step_key,
            _pipeline_outcomes(result, final=True),
            metrics={
                "downloaded": result.downloaded,
                "downloaded_bytes": result.downloaded_bytes,
                "failed": result.failed,
                "avatars_downloaded": result.avatars_downloaded,
                "avatars_failed": result.avatars_failed,
            },
        )
        return result
