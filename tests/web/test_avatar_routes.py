from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx

from tweetnook.config import AppConfig, XDGPaths
from tweetnook.web.deps import server_state
from tweetnook.web.routes import avatars


class AvatarStore:
    def __init__(self, tweet_rows=None, object_rows=None):
        self.tweet_rows = tweet_rows or []
        self.object_rows = object_rows or []
        self.expressions: list[str] = []

    def _query(self, *, expr: str, limit: int, **_kwargs):
        assert limit == avatars.AVATAR_CANDIDATE_LIMIT
        self.expressions.append(expr)
        return [*self.tweet_rows, *self.object_rows]


def _paths(tmp_path: Path) -> XDGPaths:
    return XDGPaths(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
    )


def _raw_avatar(url: str, *, modern: bool = False) -> str:
    result = (
        {"avatar": {"image_url": url}} if modern else {"legacy": {"profile_image_url_https": url}}
    )
    return json.dumps({"core": {"user_results": {"result": result}}})


def _raw_without_avatar() -> str:
    return json.dumps({"core": {"user_results": {"result": {}}}})


def test_cached_avatar_is_returned_without_store_or_network(
    monkeypatch, make_web_client, tmp_path: Path
) -> None:
    paths = _paths(tmp_path)
    avatar_path = paths.media_dir / "avatars" / "42.jpg"
    avatar_path.parent.mkdir(parents=True)
    avatar_path.write_bytes(b"cached-jpeg")
    server_state.update({"paths": paths, "config": AppConfig()})

    class Store:
        def _query(self, **_kwargs):
            raise AssertionError("cache hit should not query")

    monkeypatch.setattr(
        avatars.httpx,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cache hit should not fetch")
        ),
    )
    client = make_web_client(avatars.router, store=Store())

    response = client.get("/api/avatar/42")

    assert response.status_code == 200
    assert response.content == b"cached-jpeg"
    assert response.headers["content-type"] == "image/jpeg"


def test_avatar_fetches_high_resolution_url_and_caches_response(
    monkeypatch, make_web_client, tmp_path: Path
) -> None:
    paths = _paths(tmp_path)
    config = AppConfig()
    store = AvatarStore(
        tweet_rows=[
            {
                "raw_json": _raw_avatar(
                    "https://pbs.twimg.com/profile_images/id_normal.jpg",
                    modern=True,
                )
            }
        ]
    )
    calls: list[tuple[str, float]] = []
    monkeypatch.setattr(
        avatars.httpx,
        "get",
        lambda url, timeout: (
            calls.append((url, timeout)) or SimpleNamespace(status_code=200, content=b"downloaded")
        ),
    )
    server_state.update({"paths": paths, "config": config})
    client = make_web_client(avatars.router, store=store)

    response = client.get("/api/avatar/42")

    assert response.status_code == 200
    assert response.content == b"downloaded"
    assert calls == [("https://pbs.twimg.com/profile_images/id_400x400.jpg", 10.0)]
    assert (paths.media_dir / "avatars" / "42.jpg").read_bytes() == b"downloaded"


def test_stale_transparent_fallback_does_not_block_download(
    monkeypatch, make_web_client, tmp_path: Path
) -> None:
    paths = _paths(tmp_path)
    fallback_path = paths.media_dir / "avatars" / "42.png"
    fallback_path.parent.mkdir(parents=True)
    fallback_path.write_bytes(avatars.TRANSPARENT_PNG)
    store = AvatarStore(
        tweet_rows=[{"raw_json": _raw_avatar("https://pbs.twimg.com/profile_images/id_normal.jpg")}]
    )
    monkeypatch.setattr(
        avatars.httpx,
        "get",
        lambda url, timeout: SimpleNamespace(status_code=200, content=b"recovered"),
    )
    server_state.update({"paths": paths, "config": AppConfig()})
    client = make_web_client(avatars.router, store=store)

    response = client.get("/api/avatar/42")

    assert response.content == b"recovered"
    assert (paths.media_dir / "avatars" / "42.jpg").read_bytes() == b"recovered"
    assert not fallback_path.exists()


def test_avatar_skips_avatarless_rows_and_uses_richer_candidate(
    monkeypatch, make_web_client, tmp_path: Path
) -> None:
    paths = _paths(tmp_path)
    store = AvatarStore(
        tweet_rows=[
            {"raw_json": _raw_without_avatar()},
            {"raw_json": _raw_avatar("https://pbs.twimg.com/profile_images/id_normal.png")},
        ]
    )
    monkeypatch.setattr(
        avatars.httpx,
        "get",
        lambda url, timeout: SimpleNamespace(status_code=200, content=b"legacy"),
    )
    server_state.update({"paths": paths, "config": AppConfig()})
    client = make_web_client(avatars.router, store=store)

    response = client.get("/api/avatar/77")

    assert response.content == b"legacy"
    assert len(store.expressions) == 1
    assert "record_type IN ('tweet', 'tweet_object')" in store.expressions[0]


def test_avatar_tries_next_candidate_after_fetch_failure(
    monkeypatch, make_web_client, tmp_path: Path
) -> None:
    paths = _paths(tmp_path)
    store = AvatarStore(
        tweet_rows=[
            {"raw_json": _raw_avatar("https://pbs.twimg.com/profile_images/old_normal.jpg")},
            {"raw_json": _raw_avatar("https://pbs.twimg.com/profile_images/new_normal.jpg")},
        ]
    )
    responses = iter(
        [
            SimpleNamespace(status_code=503, content=b"busy"),
            SimpleNamespace(status_code=200, content=b"downloaded"),
        ]
    )
    calls: list[str] = []

    def fetch(url, timeout):
        calls.append(url)
        return next(responses)

    monkeypatch.setattr(avatars.httpx, "get", fetch)
    server_state.update({"paths": paths, "config": AppConfig()})
    client = make_web_client(avatars.router, store=store)

    response = client.get("/api/avatar/42")

    assert response.content == b"downloaded"
    assert calls == [
        "https://pbs.twimg.com/profile_images/old_400x400.jpg",
        "https://pbs.twimg.com/profile_images/new_400x400.jpg",
    ]


def test_disabled_avatar_fetch_returns_nonpersistent_transparent_png(
    monkeypatch, make_web_client, tmp_path: Path
) -> None:
    paths = _paths(tmp_path)
    config = AppConfig()
    config.web.fetch_avatars = False
    store = AvatarStore(
        tweet_rows=[{"raw_json": _raw_avatar("https://pbs.twimg.com/profile_images/id_normal.jpg")}]
    )
    monkeypatch.setattr(
        avatars.httpx,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("fetching is disabled")),
    )
    server_state.update({"paths": paths, "config": config})
    client = make_web_client(avatars.router, store=store)

    first = client.get("/api/avatar/42")
    second = client.get("/api/avatar/42")

    assert first.content == second.content == avatars.TRANSPARENT_PNG
    assert first.headers["content-type"] == "image/png"
    assert second.headers["content-type"] == "image/png"
    assert first.headers["cache-control"] == "no-store, max-age=0"
    assert not (paths.media_dir / "avatars" / "42.png").exists()


def test_missing_malformed_or_failed_avatar_uses_transparent_fallback(
    monkeypatch, make_web_client, tmp_path: Path
) -> None:
    paths = _paths(tmp_path)
    responses = iter(
        [
            AvatarStore(),
            AvatarStore(tweet_rows=[{"raw_json": "not-json"}]),
            AvatarStore(
                tweet_rows=[
                    {"raw_json": _raw_avatar("https://pbs.twimg.com/profile_images/id_normal.jpg")}
                ]
            ),
        ]
    )
    monkeypatch.setattr(
        avatars.httpx,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(httpx.ConnectError("offline")),
    )
    server_state.update({"paths": paths, "config": AppConfig()})

    for user_id in ("missing", "malformed", "offline"):
        client = make_web_client(avatars.router, store=next(responses))
        response = client.get(f"/api/avatar/{user_id}")
        assert response.status_code == 200
        assert response.content == avatars.TRANSPARENT_PNG
        assert response.headers["content-type"] == "image/png"
        assert response.headers["cache-control"] == "no-store, max-age=0"
        assert not (paths.media_dir / "avatars" / f"{user_id}.png").exists()


def test_avatar_escapes_user_id_in_store_expression(make_web_client, tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    store = AvatarStore()
    server_state.update({"paths": paths, "config": AppConfig()})
    client = make_web_client(avatars.router, store=store)

    response = client.get("/api/avatar/o%27reilly")

    assert response.status_code == 200
    assert len(store.expressions) == 1
    assert store.expressions[0].startswith(
        "author_id = 'o''reilly' AND record_type IN ('tweet', 'tweet_object')"
    )


def test_avatar_route_requires_authentication(make_web_client, tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    server_state.update({"paths": paths, "config": AppConfig()})
    client = make_web_client(avatars.router, store=AvatarStore(), password="secret")

    response = client.get("/api/avatar/42")

    assert response.status_code == 401
