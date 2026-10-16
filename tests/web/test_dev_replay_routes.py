from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import make_bookmarks_response, make_tweet_detail_response, make_tweet_result
from tweetnook import config as config_module
from tweetnook.client.timelines import (
    FocalResultKind,
    TimelineTweet,
    parse_timeline_response,
    parse_tweet_detail_response,
)
from tweetnook.config import XDGPaths, ensure_paths
from tweetnook.media import download_media
from tweetnook.storage import open_archive_store
from tweetnook.sync import sync_collection
from tweetnook.web.deps import server_state
from tweetnook.web.replay_placeholders import placeholder_bytes
from tweetnook.web.replay_source import FAILURE_HEADER, MEDIA_HEADER, RESULT_HEADER
from tweetnook.web.routes import dev_replay as dev_replay_routes
from tweetnook.web.server import _compressible_path

PASSWORD = "source-pass"
AUTH = ("tester", PASSWORD)
GRAPHQL = "/api/dev/replay/x/x.com/i/api/graphql/qid"


def _paths(root: Path) -> XDGPaths:
    return ensure_paths(
        XDGPaths(config_dir=root / "config", data_dir=root / "data", cache_dir=root / "cache")
    )


def _tweet(tweet_id: str) -> TimelineTweet:
    raw = make_tweet_result(tweet_id, f"tweet {tweet_id}")
    return TimelineTweet(
        tweet_id=tweet_id,
        text=f"tweet {tweet_id}",
        author_id="100",
        author_username="user100",
        author_display_name="User 100",
        created_at=None,
        sort_index=tweet_id,
        raw_json=raw,
    )


def _persist_page(store, ids: list[str], cursor_in: str | None, cursor_out: str | None) -> None:
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=cursor_in,
        cursor_out=cursor_out,
        http_status=200,
        raw_json=make_bookmarks_response(ids, cursor=cursor_out),
        tweets=[_tweet(tweet_id) for tweet_id in ids],
        last_head_tweet_id=ids[0],
        backfill_cursor=None,
        backfill_incomplete=False,
    )


def _seed_source(paths: XDGPaths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    try:
        _persist_page(store, ["11", "12"], None, "c1")
        _persist_page(store, ["13"], "c1", "c2")
        # A later, shorter head-only chain must not hide the longer saved chain.
        _persist_page(store, ["11"], None, "c-short")
        detail = make_tweet_detail_response(
            [make_tweet_result("50", "root"), make_tweet_result("51", "reply")]
        )
        store.persist_thread_detail(
            focal_tweet_id="50", tweets=[_tweet("50"), _tweet("51")], raw_json=detail
        )
    finally:
        store.close()
    media_file = paths.data_dir / "media" / "11" / "3_a.jpg"
    media_file.parent.mkdir(parents=True)
    media_file.write_bytes(b"\xff\xd8\xffreal-jpeg")
    connection = sqlite3.connect(paths.database_path)
    with connection:
        connection.execute(
            "INSERT INTO archive (row_key, record_type, tweet_id, media_type, media_url, "
            "local_path, content_type, download_state) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "media:11:3_a",
                "media",
                "11",
                "photo",
                "https://pbs.twimg.com/media/a.jpg",
                "media/11/3_a.jpg",
                "image/jpeg",
                "done",
            ),
        )
    connection.close()


def _source_app(monkeypatch, paths: XDGPaths, *, enabled: bool = True, password: bool = True):
    monkeypatch.setattr(dev_replay_routes, "_replay_source_enabled", lambda: enabled)
    server_state["paths"] = paths
    if password:
        server_state["password_hash"] = hashlib.sha256(PASSWORD.encode()).hexdigest()
    app = FastAPI()
    app.include_router(dev_replay_routes.router)
    return app


@pytest.fixture
def source_paths(tmp_path: Path) -> XDGPaths:
    paths = _paths(tmp_path / "source")
    _seed_source(paths)
    return paths


def test_replay_endpoints_are_404_when_disabled(monkeypatch, source_paths) -> None:
    client = TestClient(_source_app(monkeypatch, source_paths, enabled=False))
    assert client.get("/api/dev/replay/status", auth=AUTH).status_code == 404
    assert client.get(f"{GRAPHQL}/Bookmarks", auth=AUTH).status_code == 404


def test_replay_refuses_without_web_password(monkeypatch, source_paths) -> None:
    client = TestClient(_source_app(monkeypatch, source_paths, password=False))
    response = client.get("/api/dev/replay/status")
    assert response.status_code == 403
    assert "Web password" in response.json()["detail"]


def test_replay_requires_source_password(monkeypatch, source_paths) -> None:
    client = TestClient(_source_app(monkeypatch, source_paths))
    assert client.get("/api/dev/replay/status").status_code == 401
    assert client.get("/api/dev/replay/status", auth=("x", "wrong")).status_code == 401


def test_config_refuses_enabling_replay_source_without_password(make_web_client) -> None:
    from tweetnook.web.routes import config as config_routes

    client = make_web_client(config_routes.router)
    response = client.post("/api/config", json={"changes": {"web.dev_replay_source": True}})
    assert response.status_code == 400
    assert "Web password" in response.json()["detail"]
    schema = config_module.get_config_ui_schema()
    assert "web.dev_replay_source" not in schema["whitelist"]
    assert "web.dev_replay_source" not in schema["blacklist"]
    assert "web.dev_replay_source" in schema["descriptions"]


def test_status_reports_available_replay_data(monkeypatch, source_paths) -> None:
    client = TestClient(_source_app(monkeypatch, source_paths))
    status = client.get("/api/dev/replay/status", auth=AUTH).json()
    assert status["bookmarks_pages"] == 3
    assert status["bookmarks_chain_pages"] == 2
    assert status["likes_pages"] == 0
    assert status["thread_captures"] == 1
    assert status["downloaded_media"] == 1
    assert status["placeholder_media"] is True


def _variables(**values) -> dict[str, str]:
    return {"variables": json.dumps(values)}


def test_bookmarks_chain_by_cursor_and_end_with_empty_page(monkeypatch, source_paths) -> None:
    client = TestClient(_source_app(monkeypatch, source_paths))

    first = client.get(f"{GRAPHQL}/Bookmarks", params=_variables(count=20), auth=AUTH)
    assert first.status_code == 200
    assert first.headers[RESULT_HEADER] == "hit"
    tweets, cursor = parse_timeline_response(first.json(), "Bookmarks")
    assert [tweet.tweet_id for tweet in tweets] == ["11", "12"]
    assert cursor == "c1"

    second = client.get(f"{GRAPHQL}/Bookmarks", params=_variables(cursor="c1"), auth=AUTH)
    tweets, cursor = parse_timeline_response(second.json(), "Bookmarks")
    assert [tweet.tweet_id for tweet in tweets] == ["13"]
    assert cursor == "c2"

    end = client.get(f"{GRAPHQL}/Bookmarks", params=_variables(cursor="c2"), auth=AUTH)
    assert end.status_code == 200
    assert end.headers[RESULT_HEADER] == "empty"
    payload = end.json()
    assert "bookmark_timeline_v2" in payload["data"]
    assert parse_timeline_response(payload, "Bookmarks") == ([], None)

    likes = client.get(f"{GRAPHQL}/Likes", params=_variables(userId="1"), auth=AUTH)
    assert parse_timeline_response(likes.json(), "Likes") == ([], None)
    user_tweets = client.get(f"{GRAPHQL}/UserTweets", auth=AUTH)
    assert parse_timeline_response(user_tweets.json(), "UserTweets") == ([], None)


def test_tweet_detail_found_in_thread_capture_or_unavailable(monkeypatch, source_paths) -> None:
    client = TestClient(_source_app(monkeypatch, source_paths))
    for focal in ("50", "51", "13"):
        response = client.get(
            f"{GRAPHQL}/TweetDetail", params=_variables(focalTweetId=focal), auth=AUTH
        )
        assert response.status_code == 200
        result = parse_tweet_detail_response(response.json(), focal)
        assert result.kind == FocalResultKind.AVAILABLE, focal

    missing = client.get(f"{GRAPHQL}/TweetDetail", params=_variables(focalTweetId="999"), auth=AUTH)
    assert missing.headers[RESULT_HEADER] == "miss"
    result = parse_tweet_detail_response(missing.json(), "999")
    assert result.kind == FocalResultKind.EXPLICIT_UNAVAILABLE


def test_unknown_requests_miss_and_discovery_falls_back(monkeypatch, source_paths) -> None:
    client = TestClient(_source_app(monkeypatch, source_paths))
    miss = client.get(f"{GRAPHQL}/SomethingElse", auth=AUTH)
    assert miss.status_code == 404
    assert miss.headers[RESULT_HEADER] == "miss"
    assert miss.json()["operation"] == "SomethingElse"
    bundle = client.get(
        "/api/dev/replay/x/abs.twimg.com/responsive-web/client-web/main.abc.js", auth=AUTH
    )
    assert bundle.status_code == 404
    discovery = client.get("/api/dev/replay/x/x.com/", params={"lang": "en"}, auth=AUTH)
    assert discovery.status_code == 200
    assert "abs.twimg.com" not in discovery.text


def test_fake_failures(monkeypatch, source_paths) -> None:
    client = TestClient(_source_app(monkeypatch, source_paths))
    failed = client.get(f"{GRAPHQL}/Bookmarks", headers={FAILURE_HEADER: "fail"}, auth=AUTH)
    assert failed.status_code == 401
    assert "www-authenticate" not in failed.headers
    limited = client.get(f"{GRAPHQL}/Bookmarks", headers={FAILURE_HEADER: "ratelimit"}, auth=AUTH)
    assert limited.status_code == 429
    assert limited.headers["x-rate-limit-remaining"] == "0"
    assert int(limited.headers["x-rate-limit-reset"]) > 0


def test_media_real_and_placeholder(monkeypatch, source_paths) -> None:
    client = TestClient(_source_app(monkeypatch, source_paths))
    base = "/api/dev/replay/x/pbs.twimg.com/media"
    real = client.get(f"{base}/a.jpg", params={"name": "orig"}, auth=AUTH)
    assert real.status_code == 200
    assert real.content == b"\xff\xd8\xffreal-jpeg"
    real_by_format = client.get(f"{base}/a", params={"format": "jpg", "name": "small"}, auth=AUTH)
    assert real_by_format.content == b"\xff\xd8\xffreal-jpeg"
    missing = client.get(f"{base}/missing.jpg", auth=AUTH)
    assert missing.status_code == 404
    assert missing.headers[RESULT_HEADER] == "miss"

    headers = {MEDIA_HEADER: "placeholder"}
    for url, media_type in (
        (f"{base}/a.jpg", "image/jpeg"),
        (f"{base}/missing?format=png&name=orig", "image/png"),
        (f"{base}/missing?format=webp", "image/webp"),
        ("/api/dev/replay/x/pbs.twimg.com/profile_images/1/p_normal.jpg", "image/jpeg"),
        ("/api/dev/replay/x/video.twimg.com/ext_tw_video/1/vid.mp4?tag=12", "video/mp4"),
    ):
        response = client.get(url, headers=headers, auth=AUTH)
        assert response.status_code == 200, url
        assert response.headers["content-type"] == media_type
        assert response.headers[RESULT_HEADER] == "placeholder"
        assert response.content == placeholder_bytes(media_type)
        assert len(response.content) < 4096
    assert not _compressible_path("/api/dev/replay/x/pbs.twimg.com/media/a.jpg")


def _test_paths(tmp_path: Path) -> XDGPaths:
    return _paths(tmp_path / "test-install")


class ReplayClientTransport(httpx.AsyncBaseTransport):
    """Minimal stand-in for a replaying client, following the wire contract."""

    def __init__(self, app, *, placeholder_media: bool = True) -> None:
        self.inner = httpx.ASGITransport(app=app)
        self.placeholder_media = placeholder_media

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raw_path = f"/api/dev/replay/x/{request.url.host}".encode() + request.url.raw_path
        token = base64.b64encode(f"tester:{PASSWORD}".encode()).decode()
        headers = {"authorization": f"Basic {token}"}
        if self.placeholder_media:
            headers[MEDIA_HEADER] = "placeholder"
        replay = httpx.Request(
            request.method,
            httpx.URL("http://source.test").copy_with(raw_path=raw_path),
            headers=headers,
        )
        return await self.inner.handle_async_request(replay)


def _replay_transport(app, *, placeholder_media: bool = True) -> ReplayClientTransport:
    return ReplayClientTransport(app, placeholder_media=placeholder_media)


async def test_test_install_sync_replays_saved_chain(
    monkeypatch, source_paths, tmp_path, config
) -> None:
    app = _source_app(monkeypatch, source_paths)
    paths = _test_paths(tmp_path)

    async def no_sleep(_seconds: float) -> None:
        return None

    result = await sync_collection(
        "bookmarks",
        full=False,
        config=config,
        paths=paths,
        transport=_replay_transport(app),
        sleep=no_sleep,
    )

    assert result.pages_fetched == 3
    assert result.tweets_seen == 3
    assert result.stop_reason == "empty"


async def test_placeholder_media_is_accepted_by_media_pipeline(
    monkeypatch, source_paths, tmp_path, config
) -> None:
    from tests.test_media import _seed_archive

    app = _source_app(monkeypatch, source_paths)
    paths = _test_paths(tmp_path)
    _seed_archive(paths)

    result = await download_media(config=config, paths=paths, transport=_replay_transport(app))

    assert result.downloaded == 3
    assert result.failed == 0
    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        rows = store.list_media_rows()
    finally:
        store.close()
    assert {row["download_state"] for row in rows} == {"done"}
    video = next(row for row in rows if row["media_type"] == "video")
    assert video["content_type"] == "video/mp4"
    assert (paths.data_dir / video["local_path"]).read_bytes() == placeholder_bytes("video/mp4")
    assert video["thumbnail_content_type"] == "image/jpeg"
    photo = next(row for row in rows if row["media_type"] == "photo")
    assert (paths.data_dir / photo["local_path"]).read_bytes() == placeholder_bytes("image/jpeg")
