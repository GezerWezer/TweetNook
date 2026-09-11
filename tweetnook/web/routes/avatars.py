"""Avatar proxy endpoints."""

import json
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

    avatar_path = avatars_dir / f"{user_id}.jpg"
    fallback_path = avatars_dir / f"{user_id}.png"
    if avatar_path.exists():
        config = server_state.get("config")
        if config and config.web.avatar_cache_limit_enabled:
            mark_avatar_accessed(avatar_path)
        return FileResponse(avatar_path, headers=AVATAR_CACHE_HEADERS)

    def return_transparent():
        # A failed fetch must not become a persistent negative cache entry. The
        # source URL or archive metadata may become available later.
        return Response(
            content=TRANSPARENT_PNG,
            media_type="image/png",
            headers=TRANSPARENT_CACHE_HEADERS,
        )

    config = server_state.get("config")
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
            if resp.status_code == 200 and resp.content:
                avatar_path.write_bytes(resp.content)
                try:
                    fallback_path.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    # A stale fallback must never prevent serving a newly
                    # downloaded avatar.
                    pass
                return FileResponse(avatar_path, headers=AVATAR_CACHE_HEADERS)
        except Exception:
            # One stale URL or malformed candidate must not prevent trying
            # the next stored representation for this author.
            continue

    return return_transparent()
