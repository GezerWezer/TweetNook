"""Avatar proxy endpoints."""

import json
import struct
import tempfile
from pathlib import Path
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, Response

from tweetnook.config import XDGPaths
from tweetnook.web.avatar_cache import mark_avatar_accessed
from tweetnook.web.deps import get_server_state, require_store, verify_credentials

router = APIRouter()
AVATAR_CANDIDATE_LIMIT = 8
AVATAR_CACHE_HEADERS = {"Cache-Control": "no-cache"}
TRANSPARENT_CACHE_HEADERS = {"Cache-Control": "no-store, max-age=0"}

TRANSPARENT_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _is_placeholder(content: bytes) -> bool:
    """Recognize one-pixel PNG fallbacks without rejecting real alpha images."""
    return (
        content.startswith(b"\x89PNG\r\n\x1a\n")
        and len(content) >= 24
        and content[12:16] == b"IHDR"
        and struct.unpack(">II", content[16:24]) == (1, 1)
    )


def _avatar_format(content: bytes) -> tuple[str, str] | None:
    """Identify supported image headers without decoding or flattening alpha."""
    if _is_placeholder(content):
        return None
    if (
        len(content) >= 33
        and content.startswith(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
        and all(struct.unpack(">II", content[16:24]))
    ):
        return ".png", "image/png"
    if len(content) >= 12 and content.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if (
        len(content) >= 13
        and content[:6] in (b"GIF87a", b"GIF89a")
        and all(struct.unpack("<HH", content[6:10]))
    ):
        return ".gif", "image/gif"
    if (
        len(content) >= 20
        and content[:4] == b"RIFF"
        and content[8:12] == b"WEBP"
        and content[12:16] in (b"VP8 ", b"VP8L", b"VP8X")
    ):
        return ".webp", "image/webp"
    return None


def _cache_avatar(path: Path, content: bytes) -> None:
    """Publish complete files so concurrent requests cannot see a partial write."""
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".avatar-", delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write(content)
            file.flush()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


@router.get("/api/avatar/{user_id}")
def get_avatar(
    user_id: str,
    store: Annotated[Any, Depends(require_store)],
    _auth: Annotated[bool, Depends(verify_credentials)],
):
    server_state = get_server_state()
    paths: XDGPaths = server_state["paths"]
    avatars_dir = paths.media_dir / "avatars"
    avatars_dir.mkdir(parents=True, exist_ok=True)

    config = server_state.get("config")
    cache_paths = [
        avatars_dir / f"{user_id}{suffix}" for suffix in (".jpg", ".png", ".gif", ".webp")
    ]
    for avatar_path in cache_paths:
        try:
            with avatar_path.open("rb") as cached:
                image_format = _avatar_format(cached.read(33))
        except FileNotFoundError:
            continue
        except OSError:
            continue
        if image_format is None:
            try:
                avatar_path.unlink(missing_ok=True)
            except OSError:
                pass
        else:
            if config and config.web.avatar_cache_limit_enabled:
                mark_avatar_accessed(avatar_path)
            return FileResponse(
                avatar_path, media_type=image_format[1], headers=AVATAR_CACHE_HEADERS
            )

    def return_transparent():
        # A failed fetch must not become a persistent negative cache entry. The
        # source URL or archive metadata may become available later.
        return Response(
            content=TRANSPARENT_PNG,
            media_type="image/png",
            headers=TRANSPARENT_CACHE_HEADERS,
        )

    if not config or not config.web.fetch_avatars:
        return return_transparent()

    safe_user_id = user_id.replace("'", "''")
    rows = store._query(
        expr=(
            # Match the partial author index predicate explicitly: SQLite cannot
            # infer the nonempty condition from the author equality below.
            "author_id IS NOT NULL AND author_id != '' AND "
            f"author_id = '{safe_user_id}' AND record_type IN ('tweet', 'tweet_object') "
            "AND raw_json IS NOT NULL AND json_valid(raw_json) "
            "AND ("
            "CASE WHEN json_valid(raw_json) THEN "
            "json_extract(raw_json, '$.core.user_results.result.avatar.image_url') END IS NOT NULL "
            "OR CASE WHEN json_valid(raw_json) THEN "
            "json_extract(raw_json, '$.core.user_results.result.legacy.profile_image_url_https') "
            "END IS NOT NULL)"
        ),
        cols=["raw_json"],
        limit=AVATAR_CANDIDATE_LIMIT,
        order_by="last_seen_at DESC",
    )

    attempted_urls: set[str] = set()
    for row in rows:
        raw_json = row.get("raw_json")
        if not raw_json:
            continue

        try:
            raw = json.loads(raw_json)
            user_res = raw.get("core", {}).get("user_results", {}).get("result", {})
            if not isinstance(user_res, dict):
                continue

            avatar = user_res.get("avatar") or {}
            legacy = user_res.get("legacy") or {}
            url = avatar.get("image_url") or legacy.get("profile_image_url_https")
            if not isinstance(url, str) or not url:
                continue

            url = url.replace("_normal", "_400x400")
            if url in attempted_urls:
                continue
            attempted_urls.add(url)
            resp = httpx.get(url, timeout=10.0)
            image_format = _avatar_format(resp.content)
            if resp.status_code == 200 and image_format is not None:
                suffix, media_type = image_format
                avatar_path = avatars_dir / f"{user_id}{suffix}"
                _cache_avatar(avatar_path, resp.content)
                return FileResponse(
                    avatar_path, media_type=media_type, headers=AVATAR_CACHE_HEADERS
                )
        except Exception:
            # One stale URL or malformed candidate must not prevent trying
            # the next stored representation for this author.
            continue

    return return_transparent()
