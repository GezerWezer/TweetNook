"""Avatar proxy endpoints."""

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import Response

from tweetnook.config import XDGPaths
from tweetnook.core.dev_replay import client_transport
from tweetnook.enrichment.avatars import (
    AVATAR_CANDIDATE_LIMIT,
    AVATAR_FETCH_BUDGET_SECONDS,
    avatar_cache_paths,
    avatar_format,
    avatar_variant_urls,
    cache_avatar,
    core_avatar_dir,
)
from tweetnook.web.avatar_cache import mark_avatar_accessed
from tweetnook.web.deps import get_server_state, require_store, verify_credentials

router = APIRouter()
logger = logging.getLogger(__name__)
# Avatars rarely change; reuse them for a day, then refresh in the background.
AVATAR_CACHE_HEADERS = {"Cache-Control": "private, max-age=86400, stale-while-revalidate=604800"}
TRANSPARENT_CACHE_HEADERS = {"Cache-Control": "no-store, max-age=0"}
# With downloads off, a missing avatar stays missing until the setting changes, so the
# browser may reuse the placeholder briefly instead of asking on every page load. The
# Web UI changes avatar URLs when it saves that setting.
DISABLED_CACHE_HEADERS = {"Cache-Control": "private, max-age=300"}
# Remote avatar fetches can take seconds. Running avatar work on its own small
# pool keeps a page of new authors from occupying the shared request threads
# that feed, search, and thread requests need.
_AVATAR_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="tweetnook-avatar")

TRANSPARENT_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _fetch_avatar(url: str, *, timeout: float) -> httpx.Response:
    transport = client_transport()
    if transport is None:
        return httpx.get(url, timeout=timeout, follow_redirects=True)
    # Replay mode: avatars come from the replay source, never from Twitter/X.
    with httpx.Client(transport=transport) as client:
        return client.get(url, timeout=timeout, follow_redirects=True)


@router.get("/api/avatar/{user_id}")
async def get_avatar(
    user_id: str,
    store: Annotated[Any, Depends(require_store)],
    _auth: Annotated[bool, Depends(verify_credentials)],
):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_AVATAR_EXECUTOR, _avatar_response, user_id, store)


def _avatar_response(user_id: str, store: Any):
    server_state = get_server_state()
    paths: XDGPaths = server_state["paths"]
    avatars_dir = paths.media_dir / "avatars"
    config = server_state.get("config")
    fetch = bool(config and config.web.fetch_avatars)
    avatar = load_avatar(avatars_dir, user_id, store, fetch=fetch)
    if avatar is None:
        headers = TRANSPARENT_CACHE_HEADERS if fetch else DISABLED_CACHE_HEADERS
        return Response(content=TRANSPARENT_PNG, media_type="image/png", headers=headers)
    if avatar.cache_path is not None:
        # Only the on-demand cache is evicted by last use; core avatars are kept regardless.
        if (
            config
            and config.web.avatar_cache_limit_enabled
            and avatar.cache_path.parent == avatars_dir
        ):
            mark_avatar_accessed(avatar.cache_path)
        headers = AVATAR_CACHE_HEADERS
    else:
        headers = TRANSPARENT_CACHE_HEADERS
    return Response(content=avatar.content, media_type=avatar.media_type, headers=headers)


@dataclass(frozen=True, slots=True)
class AvatarImage:
    content: bytes
    media_type: str
    cache_path: Path | None


def load_avatar(avatars_dir: Path, user_id: str, store: Any, *, fetch: bool) -> AvatarImage | None:
    """Own the image bytes independently of cache eviction or publication failure."""
    if "\x00" in user_id or "/" in user_id or "\\" in user_id or user_id in {"", ".", ".."}:
        return None
    # Core avatars, saved by sync for posts in collections, win over the on-demand cache.
    cache_paths = avatar_cache_paths(core_avatar_dir(avatars_dir), user_id) + avatar_cache_paths(
        avatars_dir, user_id
    )
    for avatar_path in cache_paths:
        try:
            with avatar_path.open("rb") as cached:
                content = cached.read()
            image_format = avatar_format(content)
        except (OSError, ValueError):
            continue
        if image_format is None:
            try:
                avatar_path.unlink(missing_ok=True)
            except OSError:
                pass
        else:
            return AvatarImage(content, image_format[1], avatar_path)

    if not fetch:
        return None

    urls = store.avatar_source_urls(user_id, limit=AVATAR_CANDIDATE_LIMIT)
    attempted_urls: set[str] = set()
    deadline = time.monotonic() + AVATAR_FETCH_BUDGET_SECONDS
    for source_url in urls:
        for url in avatar_variant_urls(source_url):
            if url in attempted_urls:
                continue
            attempted_urls.add(url)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                resp = _fetch_avatar(url, timeout=min(10.0, remaining))
            except (httpx.HTTPError, ValueError):
                # Stale variants and network errors must not block other sources.
                continue
            image_format = avatar_format(resp.content)
            if resp.status_code != 200 or image_format is None:
                continue
            suffix, media_type = image_format
            avatar_path = avatars_dir / f"{user_id}{suffix}"
            try:
                cache_avatar(avatar_path, resp.content)
            except OSError as exc:
                logger.warning("Could not cache avatar: %s", exc)
                return AvatarImage(resp.content, media_type, None)
            return AvatarImage(resp.content, media_type, avatar_path)

    return None
