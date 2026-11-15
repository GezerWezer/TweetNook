"""Serve the Chirp font files the server downloaded into its cache."""

from fastapi import APIRouter
from fastapi.responses import FileResponse, Response

from tweetnook.web.chirp_font import CHIRP_WEIGHTS, chirp_font_path
from tweetnook.web.deps import server_state

router = APIRouter()
# Like the static assets, the font is public and served before login and Setup.
FONT_CACHE_HEADERS = {"Cache-Control": "public, max-age=2592000"}
# A first install is still downloading, so the browser asks again on the next load.
MISSING_HEADERS = {"Cache-Control": "no-store"}


@router.get("/fonts/chirp-{weight}.woff")
def get_chirp_font(weight: str):
    paths = server_state.get("paths")
    if weight not in CHIRP_WEIGHTS or paths is None:
        return Response(status_code=404, headers=MISSING_HEADERS)
    path = chirp_font_path(paths, weight)
    if not path.is_file():
        return Response(status_code=404, headers=MISSING_HEADERS)
    return FileResponse(path, media_type="font/woff", headers=FONT_CACHE_HEADERS)
