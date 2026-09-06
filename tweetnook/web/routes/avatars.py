"""Avatar proxy endpoints."""

import json
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from tweetnook.config import XDGPaths
from tweetnook.web.deps import get_server_state, require_store, verify_credentials

router = APIRouter()

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
        return FileResponse(avatar_path)
    if fallback_path.exists():
        return FileResponse(fallback_path, media_type="image/png")

    def save_and_return_transparent():
        fallback_path.write_bytes(TRANSPARENT_PNG)
        return FileResponse(fallback_path, media_type="image/png")

    safe_user_id = user_id.replace("'", "''")
    rows = store._query(
        expr=f"author_id = '{safe_user_id}' AND record_type = 'tweet'",
        limit=1,
    )
    if not rows:
        rows = store._query(
            expr=f"author_id = '{safe_user_id}' AND record_type = 'tweet_object'",
            limit=1,
        )

    if rows and rows[0].get("raw_json"):
        config = server_state.get("config")
        if not config or not config.web.fetch_avatars:
            return save_and_return_transparent()

        try:
            raw = json.loads(rows[0]["raw_json"])
            user_res = raw.get("core", {}).get("user_results", {}).get("result", {})

            url = user_res.get("avatar", {}).get("image_url")
            if not url:
                url = user_res.get("legacy", {}).get("profile_image_url_https")

            if url:
                url = url.replace("_normal", "_400x400")
                resp = httpx.get(url, timeout=10.0)
                if resp.status_code == 200:
                    avatar_path.write_bytes(resp.content)
                    return FileResponse(avatar_path)
        except Exception:
            pass

    return save_and_return_transparent()
