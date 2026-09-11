from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from tweetnook.config import AppConfig, XDGPaths
from tweetnook.web import server
from tweetnook.web.deps import (
    get_server_state,
    get_store,
    server_state,
    verify_credentials,
)


def _auth_app() -> FastAPI:
    app = FastAPI()

    @app.get("/protected")
    def protected(_auth: bool = Depends(verify_credentials)):
        return {"ok": True}

    return app


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
    app = FastAPI(lifespan=server.lifespan)

    with TestClient(app):
        assert server_state["store"] is store
        assert events == ["scalar", "thread", "fts"]

    assert events == ["scalar", "thread", "fts", "close"]


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

    with pytest.raises(RuntimeError, match="application failed"):
        async with server.lifespan(FastAPI()):
            raise RuntimeError("application failed")

    assert events == ["scalar", "fts", "close"]


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
