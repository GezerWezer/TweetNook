"""X's Chirp typeface, the default font of the themes, fetched from X by each install.

Chirp is licensed to X, so TweetNook can't ship it. Instead the server downloads the
web fonts X serves to every browser into its cache once, and serves them from there,
so browsers never contact X for them. Until they arrive, or if X stops serving them,
the themes fall back to the system font.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import httpx

from tweetnook.config import XDGPaths
from tweetnook.core.dev_replay import client_transport
from tweetnook.core.fileio import atomic_write_bytes

logger = logging.getLogger(__name__)

CHIRP_WEIGHTS = ("regular", "medium", "bold", "heavy")
CHIRP_URL = "https://abs.twimg.com/fonts/chirp-{weight}-web.woff"
# Each weight is about 50 KB; anything far larger is not the font.
MAX_FONT_BYTES = 1_000_000
WOFF_SIGNATURE = b"wOFF"
FETCH_TIMEOUT_SECONDS = 30.0


def chirp_font_path(paths: XDGPaths, weight: str) -> Path:
    return paths.cache_dir / "fonts" / f"chirp-{weight}.woff"


def build_font_client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    # Replay mode answers twimg.com from the replay source, as it does for avatars.
    return httpx.Client(
        transport=client_transport(transport),
        timeout=FETCH_TIMEOUT_SECONDS,
        follow_redirects=True,
    )


def _fetch_font(client: httpx.Client, weight: str) -> bytes:
    with client.stream("GET", CHIRP_URL.format(weight=weight)) as response:
        response.raise_for_status()
        body = bytearray()
        for chunk in response.iter_bytes():
            body += chunk
            if len(body) > MAX_FONT_BYTES:
                raise ValueError(f"chirp-{weight} is larger than {MAX_FONT_BYTES} bytes")
    # A captive portal or error page can answer 200; only a WOFF file is kept.
    if not body.startswith(WOFF_SIGNATURE):
        raise ValueError(f"chirp-{weight} is not a WOFF font")
    return bytes(body)


def download_chirp_fonts(paths: XDGPaths, *, transport: httpx.BaseTransport | None = None) -> int:
    """Save each missing weight and return how many were saved.

    A failure stops the run and is only logged: the weights saved so far stay, and the
    rest are tried again on the next start.
    """
    missing = [weight for weight in CHIRP_WEIGHTS if not chirp_font_path(paths, weight).is_file()]
    if not missing:
        return 0
    saved = 0
    try:
        with build_font_client(transport) as client:
            for weight in missing:
                body = _fetch_font(client, weight)
                path = chirp_font_path(paths, weight)
                path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_bytes(path, body)
                saved += 1
    except (httpx.HTTPError, ValueError, OSError) as error:
        logger.info("Could not download the Chirp font; themes use the system font: %s", error)
    return saved


def start_chirp_download(paths: XDGPaths) -> threading.Thread | None:
    """Fetch missing weights off the startup path; None when all are already cached."""
    if all(chirp_font_path(paths, weight).is_file() for weight in CHIRP_WEIGHTS):
        return None
    thread = threading.Thread(
        target=download_chirp_fonts, args=(paths,), name="tweetnook-chirp-font", daemon=True
    )
    thread.start()
    return thread
