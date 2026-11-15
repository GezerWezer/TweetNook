from __future__ import annotations

import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from tweetnook.config import AppConfig, ensure_paths, resolve_paths
from tweetnook.web import chirp_font, server
from tweetnook.web.deps import server_state
from tweetnook.web.routes import fonts

WEIGHTS = ("regular", "medium", "bold", "heavy")


def _woff(weight: str) -> bytes:
    return b"wOFF" + weight.encode() * 100


def _font_transport(requested: list[str], *, answer=None) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        weight = request.url.path.removeprefix("/fonts/chirp-").removesuffix("-web.woff")
        if answer is not None:
            return answer(weight)
        return httpx.Response(200, content=_woff(weight))

    return httpx.MockTransport(handle)


def test_download_saves_every_weight_from_x(paths):
    requested: list[str] = []

    assert chirp_font.download_chirp_fonts(paths, transport=_font_transport(requested)) == 4

    assert requested == [f"https://abs.twimg.com/fonts/chirp-{w}-web.woff" for w in WEIGHTS]
    for weight in WEIGHTS:
        assert (paths.cache_dir / "fonts" / f"chirp-{weight}.woff").read_bytes() == _woff(weight)
    # Everything is cached, so a later start neither downloads nor starts a thread.
    assert chirp_font.download_chirp_fonts(paths, transport=_font_transport(requested)) == 0
    assert chirp_font.start_chirp_download(paths) is None
    assert len(requested) == 4


def test_failure_keeps_saved_weights_and_retries_only_the_rest(paths):
    requested: list[str] = []

    def bold_fails(weight):
        if weight == "bold":
            return httpx.Response(503)
        return httpx.Response(200, content=_woff(weight))

    transport = _font_transport(requested, answer=bold_fails)
    assert chirp_font.download_chirp_fonts(paths, transport=transport) == 2
    fonts_dir = paths.cache_dir / "fonts"
    saved = sorted(p.name for p in fonts_dir.iterdir())
    assert saved == ["chirp-medium.woff", "chirp-regular.woff"]

    requested.clear()
    assert chirp_font.download_chirp_fonts(paths, transport=_font_transport(requested)) == 2
    assert [url.rsplit("/", 1)[1] for url in requested] == [
        "chirp-bold-web.woff",
        "chirp-heavy-web.woff",
    ]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"<html>Sign in to this network</html>"),
        httpx.Response(200, content=b"wOFF" + b"\0" * (chirp_font.MAX_FONT_BYTES + 1)),
    ],
    ids=["not-woff", "too-large"],
)
def test_download_never_saves_what_is_not_the_font(paths, response):
    transport = _font_transport([], answer=lambda _weight: response)

    assert chirp_font.download_chirp_fonts(paths, transport=transport) == 0
    fonts_dir = paths.cache_dir / "fonts"
    assert not fonts_dir.exists() or not any(fonts_dir.iterdir())


def test_route_serves_cached_weights_and_404s_until_they_exist(paths, make_web_client):
    server_state["paths"] = paths
    client = make_web_client(fonts.router)

    missing = client.get("/fonts/chirp-regular.woff")
    assert missing.status_code == 404
    assert missing.headers["cache-control"] == "no-store"

    chirp_font.download_chirp_fonts(paths, transport=_font_transport([]))
    found = client.get("/fonts/chirp-regular.woff")
    assert found.status_code == 200
    assert found.content == _woff("regular")
    assert found.headers["content-type"] == "font/woff"
    assert "max-age" in found.headers["cache-control"]

    assert client.get("/fonts/chirp-light.woff").status_code == 404
    assert client.get("/fonts/chirp-..%2Fsecret.woff").status_code == 404


def test_fresh_server_downloads_on_start_and_serves_before_setup(tmp_path, monkeypatch):
    for key, folder in [
        ("XDG_DATA_HOME", "data"),
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_CACHE_HOME", "cache"),
    ]:
        monkeypatch.setenv(key, str(tmp_path / folder))
    paths = ensure_paths(resolve_paths())
    server_state.update(paths=paths, config=AppConfig())
    requested: list[str] = []
    real_build = chirp_font.build_font_client
    monkeypatch.setattr(
        chirp_font,
        "build_font_client",
        lambda transport=None: real_build(_font_transport(requested)),
    )
    started: list[threading.Thread] = []
    real_start = server.start_chirp_download

    def recording_start(start_paths):
        thread = real_start(start_paths)
        started.append(thread)
        return thread

    monkeypatch.setattr(server, "start_chirp_download", recording_start)

    with TestClient(server.app) as client:
        assert len(started) == 1
        started[0].join(timeout=5)
        assert len(requested) == 4
        # Setup isn't complete, and the font is still served (and gzip is skipped).
        response = client.get("/fonts/chirp-bold.woff", headers={"Accept-Encoding": "gzip"})
        assert response.status_code == 200
        assert response.content == _woff("bold")
        assert "content-encoding" not in response.headers
