from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from tweetnook.config import AppConfig, XDGPaths
from tweetnook.web import server
from tweetnook.web import deps
from tweetnook.web.deps import (
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    SESSION_RENEW_AGE,
    WebSessionMiddleware,
    get_server_state,
    get_store,
    server_state,
    verify_credentials,
)


def _auth_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(WebSessionMiddleware)

    @app.get("/protected")
    def protected(_auth: bool = Depends(verify_credentials)):
        return {"ok": True}

    return app


def _recording_warmer(events: list[str]):
    class Warmer:
        def __init__(self, _store):
            pass

        def request(self):
            events.append("warm")

        def stop(self):
            events.append("warm-stop")

    return Warmer


def _recording_refresher(events: list[str]):
    class Refresher:
        def __init__(self, _store, refresh_time):
            assert refresh_time == AppConfig().web.analytics_refresh_time

        def start(self):
            events.append("stats")

        def stop(self):
            events.append("stats-stop")

    return Refresher


def _paths(tmp_path: Path) -> XDGPaths:
    paths = XDGPaths(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
    )
    paths.media_dir.mkdir(parents=True)
    return paths


def test_auth_is_optional_when_no_password_is_configured() -> None:
    with TestClient(_auth_app()) as client:
        response = client.get("/protected")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.parametrize("password", ["wrong", ""])
def test_auth_rejects_missing_or_incorrect_password(password: str) -> None:
    server_state["password_hash"] = hashlib.sha256(b"correct").hexdigest()

    with TestClient(_auth_app()) as client:
        response = (
            client.get("/protected", auth=("user", password))
            if password
            else client.get("/protected")
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "Incorrect password"}
    assert response.headers["www-authenticate"] == "Basic"


def test_auth_accepts_correct_password_independent_of_username() -> None:
    server_state["password_hash"] = hashlib.sha256(b"correct").hexdigest()

    with TestClient(_auth_app()) as client:
        response = client.get("/protected", auth=("any-user", "correct"))

    assert response.status_code == 200


def test_password_login_is_remembered_by_a_session_cookie() -> None:
    server_state["password_hash"] = hashlib.sha256(b"correct").hexdigest()

    with TestClient(_auth_app()) as client:
        login = client.get("/protected", auth=("any-user", "correct"))
        token = client.cookies[SESSION_COOKIE]

    assert login.status_code == 200
    cookie = login.headers["set-cookie"]
    assert f"Max-Age={SESSION_MAX_AGE}" in cookie
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Path=/" in cookie
    assert "Secure" not in cookie  # A plain-HTTP server can't use a Secure cookie.
    # A Home Screen web app relaunches with only the cookie, and isn't asked again.
    with TestClient(_auth_app(), cookies={SESSION_COOKIE: token}) as client:
        remembered = client.get("/protected")
    assert remembered.status_code == 200
    assert "set-cookie" not in remembered.headers  # Fresh sessions aren't reissued.

    issued, _, signature = token.partition(".")
    forged = f"{int(issued) + 1}.{signature}"
    with TestClient(_auth_app(), cookies={SESSION_COOKIE: forged}) as client:
        assert client.get("/protected").status_code == 401
    server_state["password_hash"] = hashlib.sha256(b"changed").hexdigest()
    with TestClient(_auth_app(), cookies={SESSION_COOKIE: token}) as client:
        assert client.get("/protected").status_code == 401  # Changing the password signs out.


def test_session_cookie_is_secure_over_https_and_absent_without_a_password() -> None:
    server_state["password_hash"] = hashlib.sha256(b"correct").hexdigest()
    with TestClient(_auth_app(), base_url="https://testserver") as client:
        response = client.get("/protected", auth=("user", "correct"))
    assert "; Secure" in response.headers["set-cookie"]

    server_state.pop("password_hash")
    with TestClient(_auth_app()) as client:
        assert "set-cookie" not in client.get("/protected").headers


def test_session_renews_after_a_week_and_expires_after_its_lifetime(monkeypatch) -> None:
    server_state["password_hash"] = hashlib.sha256(b"correct").hexdigest()
    now = [1_800_000_000]
    monkeypatch.setattr(deps.time, "time", lambda: now[0])
    token = deps.create_session_token(server_state["password_hash"])

    now[0] += SESSION_RENEW_AGE
    with TestClient(_auth_app(), cookies={SESSION_COOKIE: token}) as client:
        renewed = client.get("/protected")
    assert renewed.status_code == 200
    assert renewed.headers["set-cookie"].startswith(f"{SESSION_COOKIE}={now[0]}.")

    now[0] += SESSION_MAX_AGE
    with TestClient(_auth_app(), cookies={SESSION_COOKIE: token}) as client:
        assert client.get("/protected").status_code == 401


def test_session_key_is_private_and_survives_restarts(tmp_path: Path) -> None:
    server_state["paths"] = _paths(tmp_path)
    server_state["password_hash"] = hashlib.sha256(b"correct").hexdigest()
    token = deps.create_session_token(server_state["password_hash"])
    key_file = server_state["paths"].web_session_key_file

    assert key_file.stat().st_mode & 0o777 == 0o600
    server_state.pop("session_key")  # A restarted server reads the same key.
    assert deps.session_age(token, server_state["password_hash"]) is not None
    assert deps.session_age("not-a-session", server_state["password_hash"]) is None


def test_state_dependencies_return_shared_objects() -> None:
    store = object()
    server_state.update({"store": store, "marker": "value"})

    assert get_store() is store
    assert get_server_state() is server_state


def test_build_fts_background_suppresses_optional_index_failure() -> None:
    class Store:
        def ensure_fts_index(self):
            raise RuntimeError("FTS unavailable")

    server._build_fts_in_background(Store())


def test_lifespan_opens_indexes_and_closes_store(monkeypatch, tmp_path: Path) -> None:
    events: list[str] = []

    class Store:
        def count_incomplete_initial_enrichment(self):
            return 0

        def ensure_scalar_indexes(self):
            events.append("scalar")

        def ensure_fts_index(self):
            events.append("fts")

        def close(self):
            events.append("close")

    class ImmediateThread:
        def __init__(self, *, target, args, daemon):
            assert daemon is True
            self.target = target
            self.args = args
            events.append("thread")

        def start(self):
            self.target(*self.args)

    store = Store()
    paths = _paths(tmp_path)
    config = AppConfig()
    server_state.update({"paths": paths, "config": config})

    def open_store(actual_paths, *, create, config):
        assert actual_paths is paths
        assert create is False
        assert config is server_state["config"]
        return store

    monkeypatch.setattr(
        server,
        "open_archive_store",
        open_store,
    )
    monkeypatch.setattr(server.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(server, "ArchiveWarmer", _recording_warmer(events))
    monkeypatch.setattr(server, "DailyStatsRefresher", _recording_refresher(events))
    app = FastAPI(lifespan=server.lifespan)

    with TestClient(app):
        assert server_state["store"] is store
        assert events == ["scalar", "thread", "fts", "warm", "stats"]

    assert events == [
        "scalar",
        "thread",
        "fts",
        "warm",
        "stats",
        "stats-stop",
        "warm-stop",
        "close",
    ]


def test_lifespan_without_paths_does_not_open_a_store(monkeypatch) -> None:
    monkeypatch.setattr(
        server,
        "open_archive_store",
        lambda *_args, **_kwargs: pytest.fail("store should not be opened"),
    )
    app = FastAPI(lifespan=server.lifespan)

    with TestClient(app):
        assert "store" not in server_state


def test_lifespan_starts_and_stops_avatar_cache_manager(monkeypatch, tmp_path: Path) -> None:
    events: list[str] = []
    paths = _paths(tmp_path)
    config = AppConfig()
    server_state.update({"paths": paths, "config": config})

    class Manager:
        def __init__(self, actual_paths, actual_config):
            assert actual_paths is paths
            assert actual_config is config
            events.append("init")

        def start(self):
            events.append("start")

        def stop(self):
            events.append("stop")

    monkeypatch.setattr(server, "AvatarCacheManager", Manager)
    monkeypatch.setattr(server, "open_archive_store", lambda *_args, **_kwargs: None)
    app = FastAPI(lifespan=server.lifespan)

    with TestClient(app):
        assert events == ["init", "start"]
        assert server_state["avatar_cache_manager"].__class__ is Manager

    assert events == ["init", "start", "stop"]
    assert "avatar_cache_manager" not in server_state


def test_lifespan_allows_missing_archive(monkeypatch, tmp_path: Path) -> None:
    server_state["paths"] = _paths(tmp_path)
    monkeypatch.setattr(server, "open_archive_store", lambda *_args, **_kwargs: None)
    app = FastAPI(lifespan=server.lifespan)

    with TestClient(app):
        assert server_state.get("store") is None
        assert server_state["job_supervisor"] is not None
        assert server_state.get("schedule_manager") is None
        assert not server_state["paths"].database_path.exists()

    assert "store" not in server_state


@pytest.mark.asyncio
async def test_lifespan_closes_store_when_context_exits_with_error(
    monkeypatch, tmp_path: Path
) -> None:
    events: list[str] = []

    class Store:
        def count_incomplete_initial_enrichment(self):
            return 0

        def ensure_scalar_indexes(self):
            events.append("scalar")

        def ensure_fts_index(self):
            events.append("fts")

        def close(self):
            events.append("close")

    class ImmediateThread:
        def __init__(self, *, target, args, daemon):
            self.target = target
            self.args = args

        def start(self):
            self.target(*self.args)

    store = Store()
    server_state["paths"] = _paths(tmp_path)
    monkeypatch.setattr(server, "open_archive_store", lambda *_args, **_kwargs: store)
    monkeypatch.setattr(server.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(server, "ArchiveWarmer", _recording_warmer(events))
    monkeypatch.setattr(server, "DailyStatsRefresher", _recording_refresher(events))

    with pytest.raises(RuntimeError, match="application failed"):
        async with server.lifespan(FastAPI()):
            raise RuntimeError("application failed")

    assert events == ["scalar", "fts", "warm", "stats", "stats-stop", "warm-stop", "close"]


def test_packaged_root_and_static_assets_are_served() -> None:
    with TestClient(server.app) as client:
        root = client.get("/")
        post = client.get("/post/12345678901234567890")
        quotes = client.get("/post/12345678901234567890/quotes")
        static = client.get("/static/js/themes.js")

    assert root.status_code == 200
    assert "text/html" in root.headers["content-type"]
    assert "tweetnook" in root.text.lower()
    for detail in (post, quotes):
        assert detail.status_code == 200
        assert detail.headers["content-type"] == root.headers["content-type"]
        assert detail.content == root.content
    assert static.status_code == 200
    assert "javascript" in static.headers["content-type"]


@pytest.mark.parametrize("path", ["/post/not-a-number", "/post/123/other"])
def test_malformed_document_routes_are_not_swallowed(path: str) -> None:
    with TestClient(server.app) as client:
        response = client.get(path)

    assert response.status_code in {404, 422}


def test_packaged_root_is_password_protected() -> None:
    server_state["password_hash"] = hashlib.sha256(b"secret").hexdigest()

    with TestClient(server.app) as client:
        paths = ["/", "/post/123", "/post/123/quotes"]
        unauthorized = [client.get(path) for path in paths]
        authorized = [client.get(path, auth=("tweetnook", "secret")) for path in paths]

    assert [response.status_code for response in unauthorized] == [401, 401, 401]
    assert [response.status_code for response in authorized] == [200, 200, 200]


def test_media_files_are_authenticated_and_path_contained(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    media_file = paths.media_dir / "nested" / "photo.jpg"
    media_file.parent.mkdir()
    media_file.write_bytes(b"private-media")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    (paths.media_dir / "escape").symlink_to(outside)
    server_state.update(
        {
            "paths": paths,
            "password_hash": hashlib.sha256(b"secret").hexdigest(),
        }
    )
    client = TestClient(server.app)

    try:
        unauthorized = client.get("/media/nested/photo.jpg")
        authorized = client.get(
            "/media/nested/photo.jpg",
            auth=("tweetnook", "secret"),
        )
        escaped = client.get("/media/escape", auth=("tweetnook", "secret"))
        missing = client.get("/media/missing.jpg", auth=("tweetnook", "secret"))
    finally:
        client.close()

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert authorized.content == b"private-media"
    assert authorized.headers["content-type"] == "image/jpeg"
    assert authorized.headers["cache-control"] == "private, max-age=2592000"
    assert escaped.status_code == 404
    assert missing.status_code == 404


def test_run_server_sets_state_and_invokes_uvicorn(monkeypatch, tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    config = AppConfig()
    run_calls: list[tuple[object, dict[str, object]]] = []

    monkeypatch.setattr(
        "uvicorn.run",
        lambda app, **kwargs: run_calls.append((app, kwargs)),
    )

    server.run_server(config, paths, "127.0.0.2", 9123, "hash")

    assert server_state == {
        "config": config,
        "paths": paths,
        "password_hash": "hash",
    }
    assert run_calls == [
        (
            server.app,
            {"host": "127.0.0.2", "port": 9123, "log_level": "info"},
        )
    ]


def test_document_versions_assets_and_revalidates_with_etag() -> None:
    with TestClient(server.app) as client:
        root = client.get("/")
        version = re.search(r'src="/static/js/app\.js\?v=([0-9a-f]{16})"', root.text)
        assert version is not None
        current = client.get(f"/static/js/app.js?v={version.group(1)}")
        unversioned = client.get("/static/js/app.js")
        stale = client.get("/static/js/app.js?v=0000000000000000")
        revalidated = client.get("/", headers={"If-None-Match": root.headers["etag"]})

    assert root.headers["cache-control"] == "no-cache"
    assert 'href="/static/css/styles.css?v=' in root.text
    assert current.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert unversioned.headers["cache-control"] == "no-cache"
    assert stale.headers["cache-control"] == "no-cache"
    assert current.content == unversioned.content
    assert revalidated.status_code == 304
    assert revalidated.content == b""


def test_versioned_document_follows_asset_changes(monkeypatch, tmp_path: Path) -> None:
    web_root = tmp_path / "web"
    (web_root / "static").mkdir(parents=True)
    (web_root / "index.html").write_text(
        '<script src="/static/app.js"></script><img src="/static/missing.png">'
    )
    asset = web_root / "static" / "app.js"
    asset.write_text("one")
    monkeypatch.setattr(server, "WEB_ROOT", web_root)
    monkeypatch.setattr(server, "static_dir", web_root / "static")
    monkeypatch.setattr(server, "_index_cache", None)

    first, first_etag, versions = server._versioned_index()
    asset.write_text("two")
    os.utime(asset, ns=(1, 1))
    second, second_etag, _ = server._versioned_index()

    assert b'src="/static/missing.png"' in first
    assert set(versions) == {"app.js"}
    assert first != second and first_etag != second_etag


@pytest.mark.parametrize(
    ("path", "compressed"),
    [
        ("/api/payload", True),
        ("/static/js/app.js", True),
        ("/media/photo.jpg", False),
        ("/api/avatar/1", False),
        ("/static/font.woff2", False),
    ],
)
def test_text_gzip_compresses_only_text_routes(path: str, compressed: bool) -> None:
    async def body(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"x" * 4096})

    app = FastAPI()
    app.add_middleware(server.TextGZipMiddleware)
    app.mount("/", body)

    with TestClient(app) as client:
        response = client.get(path, headers={"Accept-Encoding": "gzip"})

    assert (response.headers.get("content-encoding") == "gzip") is compressed
    assert response.content == b"x" * 4096
