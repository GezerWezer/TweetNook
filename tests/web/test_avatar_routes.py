from __future__ import annotations

import json
import os
import struct
import zlib
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from tweetnook.config import AppConfig, XDGPaths
from tweetnook.storage.backend import ArchiveStore
from tweetnook.web.deps import server_state
from tweetnook.web.routes import avatars

JPEG = (Path(__file__).resolve().parents[2] / "demo/demo media/avatars/1.jpg").read_bytes()


def _alpha_png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    header = struct.pack(">IIBBBBB", 2, 2, 8, 6, 0, 0, 0)
    pixels = b"\x00\xff\x00\x00\xff\x00\x00\x00\x00" * 2
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(pixels))
        + chunk(b"IEND", b"")
    )


class AvatarStore:
    def __init__(self, tweet_rows=None, object_rows=None):
        self.tweet_rows = tweet_rows or []
        self.object_rows = object_rows or []
        self.author_ids: list[str] = []

    def avatar_source_urls(self, author_id: str, *, limit: int):
        assert limit == avatars.AVATAR_CANDIDATE_LIMIT
        self.author_ids.append(author_id)
        urls = []
        for row in [*self.tweet_rows, *self.object_rows]:
            try:
                user = json.loads(row["raw_json"])["core"]["user_results"]["result"]
                url = (user.get("avatar") or {}).get("image_url") or (user.get("legacy") or {}).get(
                    "profile_image_url_https"
                )
                if isinstance(url, str) and url and url not in urls:
                    urls.append(url)
            except (ValueError, KeyError, AttributeError, TypeError):
                continue
        return urls[:limit]


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
    avatar_path.write_bytes(JPEG)
    os.utime(avatar_path, (1_000.0, 1_000.0))
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
    assert response.content == JPEG
    assert response.headers["content-type"] == "image/jpeg"
    assert avatar_path.stat().st_mtime > 1_000.0


def test_cached_avatar_does_not_refresh_mtime_when_limit_is_disabled(
    monkeypatch, make_web_client, tmp_path: Path
) -> None:
    paths = _paths(tmp_path)
    avatar_path = paths.media_dir / "avatars" / "42.jpg"
    avatar_path.parent.mkdir(parents=True)
    avatar_path.write_bytes(JPEG)
    os.utime(avatar_path, (1_000.0, 1_000.0))
    config = AppConfig()
    config.web.avatar_cache_limit_enabled = False
    server_state.update({"paths": paths, "config": config})
    monkeypatch.setattr(
        avatars,
        "mark_avatar_accessed",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("disabled limits should not track access")
        ),
    )
    client = make_web_client(avatars.router, store=AvatarStore())

    response = client.get("/api/avatar/42")

    assert response.status_code == 200
    assert avatar_path.stat().st_mtime == 1_000.0


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
        lambda url, timeout, follow_redirects: (
            calls.append((url, timeout)) or SimpleNamespace(status_code=200, content=JPEG)
        ),
    )
    server_state.update({"paths": paths, "config": config})
    client = make_web_client(avatars.router, store=store)

    response = client.get("/api/avatar/42")

    assert response.status_code == 200
    assert response.content == JPEG
    assert calls == [("https://pbs.twimg.com/profile_images/id_400x400.jpg", 10.0)]
    assert (paths.media_dir / "avatars" / "42.jpg").read_bytes() == JPEG


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
        lambda url, timeout, follow_redirects: SimpleNamespace(status_code=200, content=JPEG),
    )
    server_state.update({"paths": paths, "config": AppConfig()})
    client = make_web_client(avatars.router, store=store)

    response = client.get("/api/avatar/42")

    assert response.content == JPEG
    assert (paths.media_dir / "avatars" / "42.jpg").read_bytes() == JPEG
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
        lambda url, timeout, follow_redirects: SimpleNamespace(status_code=200, content=JPEG),
    )
    server_state.update({"paths": paths, "config": AppConfig()})
    client = make_web_client(avatars.router, store=store)

    response = client.get("/api/avatar/77")

    assert response.content == JPEG
    assert store.author_ids == ["77"]


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
    calls: list[str] = []

    def fetch(url, timeout, follow_redirects):
        calls.append(url)
        assert follow_redirects is True
        return (
            SimpleNamespace(status_code=503, content=b"busy")
            if "/old" in url
            else SimpleNamespace(status_code=200, content=JPEG)
        )

    monkeypatch.setattr(avatars.httpx, "get", fetch)
    server_state.update({"paths": paths, "config": AppConfig()})
    client = make_web_client(avatars.router, store=store)

    response = client.get("/api/avatar/42")

    assert response.content == JPEG
    assert calls == [
        "https://pbs.twimg.com/profile_images/old_400x400.jpg",
        "https://pbs.twimg.com/profile_images/old_normal.jpg",
        "https://pbs.twimg.com/profile_images/old.jpg",
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
    assert store.author_ids == []
    assert not (paths.media_dir / "avatars" / "42.png").exists()


def test_avatar_lookup_uses_author_index_and_preserves_candidate_order(
    monkeypatch, make_web_client, tmp_path: Path
) -> None:
    paths = _paths(tmp_path)
    server_state.update({"paths": paths, "config": AppConfig()})
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    records = [
        store._record(
            row_key=f"tweet_object:{index}",
            record_type="tweet_object",
            tweet_id=str(index),
            author_id=str(index),
            raw_json=_raw_avatar(f"https://pbs.twimg.com/{index}_normal.jpg"),
        )
        for index in range(100)
    ]
    records.extend(
        store._record(
            row_key=f"tweet_object:target-{index}",
            record_type="tweet_object",
            tweet_id=f"target-{index}",
            author_id="target",
            last_seen_at=index,
            raw_json=raw,
        )
        for index, raw in enumerate(
            [
                _raw_avatar("https://pbs.twimg.com/older_normal.jpg"),
                _raw_avatar("https://pbs.twimg.com/newer_normal.jpg", modern=True),
                _raw_without_avatar(),
                "malformed-json",
            ]
        )
    )
    store._merge_records(records)
    calls = []

    def fetch(url, timeout, follow_redirects):
        calls.append(url)
        return SimpleNamespace(status_code=200, content=JPEG)

    monkeypatch.setattr(avatars.httpx, "get", fetch)
    client = make_web_client(avatars.router, store=store)
    statements = []
    store.conn.set_trace_callback(statements.append)
    try:
        response = client.get("/api/avatar/target")
    finally:
        store.conn.set_trace_callback(None)
    try:
        assert response.status_code == 200
        assert calls == ["https://pbs.twimg.com/newer_400x400.jpg"]
        queries = [sql for sql in statements if "SELECT avatar_url" in sql]
        assert len(queries) == 1
        plan = " ".join(row[3] for row in store.conn.execute("EXPLAIN QUERY PLAN " + queries[0]))
        assert "idx_archive_profile_author (author_id=?)" in plan
        assert "idx_archive_capture_target" not in plan
    finally:
        store.close()


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


def test_avatar_passes_user_id_as_a_value(make_web_client, tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    store = AvatarStore()
    server_state.update({"paths": paths, "config": AppConfig()})
    client = make_web_client(avatars.router, store=store)

    response = client.get("/api/avatar/o%27reilly")

    assert response.status_code == 200
    assert store.author_ids == ["o'reilly"]


def test_avatar_route_requires_authentication(make_web_client, tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    server_state.update({"paths": paths, "config": AppConfig()})
    client = make_web_client(avatars.router, store=AvatarStore(), password="secret")

    response = client.get("/api/avatar/42")

    assert response.status_code == 401


@pytest.mark.parametrize("fetch_enabled", [True, False])
def test_cached_jpg_placeholder_is_invalidated(
    monkeypatch, make_web_client, tmp_path: Path, fetch_enabled: bool
) -> None:
    paths = _paths(tmp_path)
    path = paths.media_dir / "avatars" / "42.jpg"
    path.parent.mkdir(parents=True)
    path.write_bytes(avatars.TRANSPARENT_PNG)
    config = AppConfig()
    config.web.fetch_avatars = fetch_enabled
    server_state.update({"paths": paths, "config": config})
    calls = []

    def fetch(url, timeout, follow_redirects):
        calls.append(url)
        return SimpleNamespace(status_code=200, content=JPEG)

    monkeypatch.setattr(avatars.httpx, "get", fetch)
    store = AvatarStore(tweet_rows=[{"raw_json": _raw_avatar("https://pbs.twimg.com/new.jpg")}])
    response = make_web_client(avatars.router, store=store).get("/api/avatar/42")

    assert response.content == (JPEG if fetch_enabled else avatars.TRANSPARENT_PNG)
    assert calls == (["https://pbs.twimg.com/new.jpg"] if fetch_enabled else [])
    assert path.exists() is fetch_enabled
    if fetch_enabled:
        assert path.read_bytes() == JPEG
    else:
        assert response.headers["cache-control"] == "no-store, max-age=0"


def test_downloaded_placeholder_does_not_block_next_source(
    monkeypatch, make_web_client, tmp_path: Path
) -> None:
    paths = _paths(tmp_path)
    server_state.update({"paths": paths, "config": AppConfig()})
    store = AvatarStore(
        tweet_rows=[{"raw_json": _raw_avatar(f"https://pbs.twimg.com/{i}.png")} for i in range(2)]
    )
    replies = iter([avatars.TRANSPARENT_PNG, JPEG])
    monkeypatch.setattr(
        avatars.httpx,
        "get",
        lambda *_args, **_kwargs: SimpleNamespace(status_code=200, content=next(replies)),
    )
    response = make_web_client(avatars.router, store=store).get("/api/avatar/42")

    assert response.content == JPEG
    assert (paths.media_dir / "avatars" / "42.jpg").read_bytes() == JPEG


@pytest.mark.parametrize("suffix", [".jpg", ".png"])
def test_cached_transparent_png_keeps_alpha_and_uses_png_mime(
    monkeypatch, make_web_client, tmp_path: Path, suffix: str
) -> None:
    paths = _paths(tmp_path)
    path = paths.media_dir / "avatars" / f"42{suffix}"
    path.parent.mkdir(parents=True)
    png = _alpha_png()
    path.write_bytes(png)
    config = AppConfig()
    config.web.fetch_avatars = False
    server_state.update({"paths": paths, "config": config})
    monkeypatch.setattr(
        avatars.httpx,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("cache should win")),
    )
    response = make_web_client(avatars.router, store=AvatarStore()).get("/api/avatar/42")

    assert response.content == png
    assert response.headers["content-type"] == "image/png"
    assert path.read_bytes() == png


def test_downloaded_png_uses_png_extension_and_mime(monkeypatch, make_web_client, tmp_path):
    paths = _paths(tmp_path)
    png = _alpha_png()
    server_state.update({"paths": paths, "config": AppConfig()})
    store = AvatarStore(tweet_rows=[{"raw_json": _raw_avatar("https://pbs.twimg.com/alpha.png")}])
    monkeypatch.setattr(
        avatars.httpx,
        "get",
        lambda *_args, **_kwargs: SimpleNamespace(status_code=200, content=png),
    )
    response = make_web_client(avatars.router, store=store).get("/api/avatar/42")

    assert response.content == png
    assert response.headers["content-type"] == "image/png"
    assert (paths.media_dir / "avatars" / "42.png").read_bytes() == png
    assert not (paths.media_dir / "avatars" / "42.jpg").exists()
    assert not list((paths.media_dir / "avatars").glob(".avatar-*"))


@pytest.mark.parametrize("bad_content", [b"", b"<html>not an image</html>", b"\x89PNG\r\n\x1a\n"])
def test_nonimage_cache_and_download_are_rejected(
    monkeypatch, make_web_client, tmp_path, bad_content
):
    paths = _paths(tmp_path)
    path = paths.media_dir / "avatars" / "42.jpg"
    path.parent.mkdir(parents=True)
    path.write_bytes(bad_content)
    server_state.update({"paths": paths, "config": AppConfig()})
    store = AvatarStore(tweet_rows=[{"raw_json": _raw_avatar("https://pbs.twimg.com/bad.jpg")}])
    monkeypatch.setattr(
        avatars.httpx,
        "get",
        lambda *_args, **_kwargs: SimpleNamespace(status_code=200, content=bad_content),
    )
    response = make_web_client(avatars.router, store=store).get("/api/avatar/42")

    assert response.content == avatars.TRANSPARENT_PNG
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert not path.exists()


def test_cache_publication_is_atomic(monkeypatch, tmp_path):
    path = tmp_path / "42.png"
    content = _alpha_png()
    original_replace = Path.replace
    replacements = []

    def replace(temporary, target):
        assert not target.exists()
        assert temporary.read_bytes() == content
        replacements.append(target)
        return original_replace(temporary, target)

    monkeypatch.setattr(Path, "replace", replace)
    avatars._cache_avatar(path, content)

    assert replacements == [path]
    assert path.read_bytes() == content
    assert list(tmp_path.iterdir()) == [path]


def test_distinct_source_limit_does_not_hide_older_working_url(
    monkeypatch, make_web_client, tmp_path
):
    paths = _paths(tmp_path)
    server_state.update({"paths": paths, "config": AppConfig()})
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    records = [
        store._record(
            row_key=f"tweet_object:{i}",
            record_type="tweet_object",
            tweet_id=str(i),
            author_id="42",
            last_seen_at=i,
            raw_json=_raw_avatar("https://pbs.twimg.com/dead.jpg"),
        )
        for i in range(12)
    ]
    records.append(
        store._record(
            row_key="tweet:old",
            record_type="tweet",
            tweet_id="old",
            author_id="42",
            last_seen_at=-1,
            raw_json=_raw_avatar("https://pbs.twimg.com/working.jpg"),
        )
    )
    store._merge_records(records)
    calls = []

    def fetch(url, **kwargs):
        calls.append(url)
        return (
            SimpleNamespace(status_code=404, content=b"")
            if "/dead" in url
            else SimpleNamespace(status_code=200, content=JPEG)
        )

    monkeypatch.setattr(avatars.httpx, "get", fetch)
    try:
        response = make_web_client(avatars.router, store=store).get("/api/avatar/42")
        assert response.content == JPEG
        assert calls == ["https://pbs.twimg.com/dead.jpg", "https://pbs.twimg.com/working.jpg"]
        assert store.avatar_source_urls("42' OR 1=1 --") == []
    finally:
        store.close()


@pytest.mark.parametrize("bad_large", [b"", avatars.TRANSPARENT_PNG, b"<html>error</html>"])
@pytest.mark.parametrize("use_original", [False, True])
def test_resize_falls_back_to_captured_or_original_url(
    monkeypatch, make_web_client, tmp_path, bad_large, use_original
):
    paths = _paths(tmp_path)
    server_state.update({"paths": paths, "config": AppConfig()})
    url = "https://pbs.twimg.com/alpha_normal.png?name=_normal"
    store = AvatarStore(tweet_rows=[{"raw_json": _raw_avatar(url)}])
    calls = []
    png = _alpha_png()
    working_url = url.replace("alpha_normal.png", "alpha.png") if use_original else url

    def fetch(candidate, **kwargs):
        calls.append(candidate)
        assert kwargs["follow_redirects"] is True
        return SimpleNamespace(
            status_code=200, content=png if candidate == working_url else bad_large
        )

    monkeypatch.setattr(avatars.httpx, "get", fetch)
    response = make_web_client(avatars.router, store=store).get("/api/avatar/42")

    assert response.content == png
    assert calls == ["https://pbs.twimg.com/alpha_400x400.png?name=_normal", url] + (
        [working_url] if use_original else []
    )


def test_malformed_url_does_not_prevent_another_source(monkeypatch, make_web_client, tmp_path):
    paths = _paths(tmp_path)
    server_state.update({"paths": paths, "config": AppConfig()})
    store = AvatarStore(
        tweet_rows=[
            {"raw_json": _raw_avatar("https://[broken")},
            {"raw_json": _raw_avatar("https://pbs.twimg.com/valid.jpg")},
        ]
    )
    monkeypatch.setattr(
        avatars.httpx,
        "get",
        lambda *_args, **_kwargs: SimpleNamespace(status_code=200, content=JPEG),
    )
    response = make_web_client(avatars.router, store=store).get("/api/avatar/42")
    assert response.content == JPEG


def test_redirected_source_is_followed(monkeypatch, make_web_client, tmp_path):
    paths = _paths(tmp_path)
    server_state.update({"paths": paths, "config": AppConfig()})
    store = AvatarStore(tweet_rows=[{"raw_json": _raw_avatar("https://images.example/start")}])
    calls = []

    def transport(request):
        calls.append(str(request.url))
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/image"})
        return httpx.Response(200, content=_alpha_png())

    def fetch(url, **kwargs):
        with httpx.Client(transport=httpx.MockTransport(transport)) as client:
            return client.get(url, **kwargs)

    monkeypatch.setattr(avatars.httpx, "get", fetch)
    response = make_web_client(avatars.router, store=store).get("/api/avatar/42")

    assert response.content == _alpha_png()
    assert calls == ["https://images.example/start", "https://images.example/image"]


def test_exhausted_fetch_budget_stops_attempts(monkeypatch, make_web_client, tmp_path):
    paths = _paths(tmp_path)
    server_state.update({"paths": paths, "config": AppConfig()})
    store = AvatarStore(
        tweet_rows=[{"raw_json": _raw_avatar("https://pbs.twimg.com/alpha_normal.png")}]
    )
    now = [100.0]
    calls = []
    monkeypatch.setattr(avatars.time, "monotonic", lambda: now[0])

    def fetch(url, **kwargs):
        calls.append(url)
        now[0] += avatars.AVATAR_FETCH_BUDGET_SECONDS
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(avatars.httpx, "get", fetch)
    response = make_web_client(avatars.router, store=store).get("/api/avatar/42")

    assert response.content == avatars.TRANSPARENT_PNG
    assert len(calls) == 1
