from __future__ import annotations

import httpx
import pytest

from tweetnook import dev_replay
from tweetnook.client.base import build_async_client
from tweetnook.config import ReplayConfig, load_config, update_config_values
from tweetnook.dev_replay import (
    FAILURE_HEADER,
    MEDIA_HEADER,
    ReplayBlockedError,
    ReplaySettings,
    ReplayTransport,
    client_transport,
    configure_replay,
)


class Recorder(httpx.AsyncBaseTransport, httpx.BaseTransport):
    def __init__(self, response: httpx.Response | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.response = response or httpx.Response(200, json={"ok": True})

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.response

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.response


def _transport(recorder: Recorder, **settings) -> ReplayTransport:
    return ReplayTransport(
        ReplaySettings(url="http://source.test:8000", password="pw", **settings),
        async_transport=recorder,
        sync_transport=recorder,
    )


async def test_transport_rewrites_x_requests_and_strips_credentials(auth_bundle) -> None:
    recorder = Recorder()
    client = build_async_client(auth_bundle, timeout=5, transport=_transport(recorder))
    try:
        response = await client.get(
            "https://x.com/i/api/graphql/abc/Bookmarks?variables=%7B%22count%22%3A20%7D"
        )
    finally:
        await client.aclose()

    assert response.status_code == 200
    (sent,) = recorder.requests
    assert str(sent.url) == (
        "http://source.test:8000/api/dev/replay/x/x.com/i/api/graphql/abc/Bookmarks"
        "?variables=%7B%22count%22%3A20%7D"
    )
    lowered = {name.lower() for name in sent.headers}
    assert "cookie" not in lowered
    assert "x-csrf-token" not in lowered
    assert FAILURE_HEADER.lower() not in lowered
    assert sent.headers["authorization"].startswith("Basic ")
    assert "Bearer" not in sent.headers["authorization"]
    assert all("ct0" not in value for value in sent.headers.values())
    assert all(auth_bundle.auth_token not in value for value in sent.headers.values())
    # The placeholder header only goes with media requests.
    assert MEDIA_HEADER.lower() not in lowered


def test_transport_requests_placeholder_media_and_drops_source_cookies() -> None:
    recorder = Recorder(
        httpx.Response(200, content=b"img", headers={"set-cookie": "tweetnook_session=abc"})
    )
    with httpx.Client(transport=_transport(recorder)) as client:
        response = client.get(
            "https://pbs.twimg.com/media/a.jpg?format=jpg&name=large",
            headers={"Cookie": "auth_token=real-secret; ct0=csrf", "x-csrf-token": "csrf"},
        )
        assert "set-cookie" not in response.headers
        assert not client.cookies
    (sent,) = recorder.requests
    assert sent.url.raw_path == b"/api/dev/replay/x/pbs.twimg.com/media/a.jpg?format=jpg&name=large"
    assert sent.headers[MEDIA_HEADER] == "placeholder"
    assert {name.lower() for name in sent.headers}.isdisjoint({"cookie", "x-csrf-token"})
    assert all("real-secret" not in value for value in sent.headers.values())


@pytest.mark.parametrize("keyword", ["fail", "ratelimit"])
async def test_transport_forwards_only_fake_failure_keyword(auth_bundle, keyword) -> None:
    recorder = Recorder()
    bundle = auth_bundle.model_copy(update={"auth_token": keyword})
    client = build_async_client(bundle, timeout=5, transport=_transport(recorder))
    try:
        await client.get("https://x.com/i/api/graphql/abc/Likes")
    finally:
        await client.aclose()

    (sent,) = recorder.requests
    assert sent.headers[FAILURE_HEADER] == keyword
    assert "cookie" not in {name.lower() for name in sent.headers}


@pytest.mark.parametrize("token", ["Fail", "FAIL", "failing", "offline-ish", "real-token"])
async def test_transport_sends_no_failure_header_unless_token_is_exact(auth_bundle, token) -> None:
    recorder = Recorder()
    bundle = auth_bundle.model_copy(update={"auth_token": token})
    client = build_async_client(bundle, timeout=5, transport=_transport(recorder))
    try:
        await client.get("https://x.com/i/api/graphql/abc/Likes")
    finally:
        await client.aclose()

    (sent,) = recorder.requests
    assert FAILURE_HEADER.lower() not in {name.lower() for name in sent.headers}


async def test_transport_simulates_offline_and_blocks_other_hosts(auth_bundle) -> None:
    recorder = Recorder()
    bundle = auth_bundle.model_copy(update={"auth_token": "offline"})
    client = build_async_client(bundle, timeout=5, transport=_transport(recorder))
    try:
        with pytest.raises(httpx.ConnectError, match="offline"):
            await client.get("https://x.com/i/api/graphql/abc/Likes")
    finally:
        await client.aclose()

    async with httpx.AsyncClient(transport=_transport(recorder)) as plain:
        with pytest.raises(ReplayBlockedError, match="example.com"):
            await plain.get("https://example.com/article")
    with httpx.Client(transport=_transport(recorder)) as sync_client:
        with pytest.raises(ReplayBlockedError):
            sync_client.get("https://news.example.org/")
        assert sync_client.get("https://pbs.twimg.com/media/a.jpg").status_code == 200
    assert [request.url.host for request in recorder.requests] == ["source.test"]


async def test_transport_reports_rejected_source_password() -> None:
    recorder = Recorder(httpx.Response(401, headers={"www-authenticate": "Basic"}))
    async with httpx.AsyncClient(transport=_transport(recorder)) as client:
        with pytest.raises(ReplayBlockedError, match="password"):
            await client.get("https://x.com/i/api/graphql/abc/Likes")


def test_real_media_preference_omits_placeholder_header() -> None:
    recorder = Recorder()
    with httpx.Client(transport=_transport(recorder, placeholder_media=False)) as client:
        client.get("https://video.twimg.com/a.mp4")
    assert MEDIA_HEADER.lower() not in {name.lower() for name in recorder.requests[0].headers}


def test_client_transport_prefers_explicit_then_replay() -> None:
    explicit = Recorder()
    configure_replay(None)
    assert client_transport() is None
    configure_replay(ReplayConfig(url="http://source.test", password="pw"))
    try:
        assert isinstance(client_transport(), ReplayTransport)
        assert client_transport(explicit) is explicit
    finally:
        configure_replay(None)


def test_load_config_activates_and_clears_replay(tmp_path) -> None:
    env = {
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
    }
    _config, paths = load_config(env)
    assert dev_replay.active_replay() is None
    update_config_values(
        paths,
        {
            "replay.url": "http://source.test:8000",
            "replay.password": "pw",
            "replay.placeholder_media": False,
        },
    )
    try:
        config, _ = load_config(env)
        settings = dev_replay.active_replay()
        assert settings == ReplaySettings("http://source.test:8000", "pw", False)
        assert config.replay.placeholder_media is False
        update_config_values(paths, {"replay.url": None, "replay.password": None})
        load_config(env)
        assert dev_replay.active_replay() is None
    finally:
        configure_replay(None)


def test_normalize_replay_url() -> None:
    assert dev_replay.normalize_replay_url(" 10.0.0.5:8000/ ") == "http://10.0.0.5:8000"
    assert dev_replay.normalize_replay_url("https://s.test/tn/") == "https://s.test/tn"
    for bad in ("ftp://s.test", "http://u:p@s.test", "http://s.test/?a=1", "http://"):
        with pytest.raises(ValueError):
            dev_replay.normalize_replay_url(bad)


def test_transport_keeps_a_source_path_prefix() -> None:
    recorder = Recorder()
    settings = ReplaySettings(url="http://source.test/tn", password="pw")
    with httpx.Client(transport=ReplayTransport(settings, sync_transport=recorder)) as client:
        client.get("https://twitter.com/")
    assert str(recorder.requests[0].url) == "http://source.test/tn/api/dev/replay/x/twitter.com/"


def test_default_client_never_reaches_the_network_in_replay(monkeypatch) -> None:
    """With replay active and no explicit transport, nothing goes to the real host."""

    sent: list[httpx.Request] = []

    def fake_handle(self, request):
        sent.append(request)
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", fake_handle)
    configure_replay(ReplayConfig(url="http://source.test:8000", password="pw"))
    try:
        with httpx.Client(transport=client_transport()) as client:
            client.get("https://x.com/")
            with pytest.raises(ReplayBlockedError):
                client.get("https://example.com/")
    finally:
        configure_replay(None)
    assert [request.url.host for request in sent] == ["source.test"]


def test_dev_tools_env_flag() -> None:
    assert dev_replay.dev_tools_enabled({"TWEETNOOK_DEV_TOOLS": "1"})
    assert not dev_replay.dev_tools_enabled({})


def _route_network(monkeypatch) -> list[httpx.Request]:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, text="<html></html>")

    monkeypatch.setattr(dev_replay, "_sync_network_transport", lambda: httpx.MockTransport(handler))
    monkeypatch.setattr(
        dev_replay, "_async_network_transport", lambda: httpx.MockTransport(handler)
    )
    configure_replay(ReplayConfig(url="http://source.test:8000", password="pw"))
    return sent


def test_web_avatar_fetch_goes_to_the_replay_source(monkeypatch) -> None:
    from tweetnook.web.routes import avatars

    sent = _route_network(monkeypatch)
    try:
        avatars._fetch_avatar("https://pbs.twimg.com/profile_images/1/a_normal.jpg", timeout=5)
    finally:
        configure_replay(None)
    (request,) = sent
    assert str(request.url) == (
        "http://source.test:8000/api/dev/replay/x/pbs.twimg.com/profile_images/1/a_normal.jpg"
    )
    assert request.headers[MEDIA_HEADER] == "placeholder"


async def test_query_id_discovery_uses_the_replay_source_and_blocks_others(
    monkeypatch, tmp_path
) -> None:
    from tweetnook.query_ids import QueryIdStore
    from tweetnook.utils import resolve_query_ids

    sent = _route_network(monkeypatch)
    env = {
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
    }
    try:
        _config, paths = load_config(env)
        configure_replay(ReplayConfig(url="http://source.test:8000", password="pw"))
        ids = await resolve_query_ids(
            QueryIdStore(paths), ["Bookmarks"], force_refresh=True, transport=None
        )
        async with httpx.AsyncClient(transport=client_transport()) as client:
            with pytest.raises(ReplayBlockedError):
                await client.get("https://example.com/story")
    finally:
        configure_replay(None)
    assert ids["Bookmarks"]
    assert sent
    assert {request.url.host for request in sent} == {"source.test"}
    assert sent[0].url.raw_path.startswith(b"/api/dev/replay/x/x.com/")
