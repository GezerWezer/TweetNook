"""Developer replay mode: route a test install's Twitter/X traffic to a replay source.

A test install with a saved replay source (``[replay] url``) never contacts
Twitter/X. Every httpx client the app creates asks :func:`client_transport` for
its transport; while replay is active that is a :class:`ReplayTransport`, which
rewrites requests for Twitter/X hosts to the source's ``/api/dev/replay/x/...``
endpoint and refuses every other host. Session cookies and auth headers are
never forwarded; the only cookie-derived value that leaves the process is a
fake-failure keyword (``fail``/``ratelimit``) typed as the auth_token;
``offline`` is simulated here as a connection error and never sent.

The replay source half lives in ``tweetnook.web.replay_source``; the wire
constants below are shared by both halves.
"""

from __future__ import annotations

import base64
import os
import threading
from dataclasses import dataclass
from http.cookies import CookieError, SimpleCookie
from typing import Any

import httpx

DEV_TOOLS_ENV = "TWEETNOOK_DEV_TOOLS"
REPLAY_PATH_PREFIX = "/api/dev/replay"
REPLAY_PROXY_PREFIX = f"{REPLAY_PATH_PREFIX}/x"
REPLAY_STATUS_PATH = f"{REPLAY_PATH_PREFIX}/status"
FAILURE_HEADER = "X-TweetNook-Replay-Fail"
MEDIA_HEADER = "X-TweetNook-Replay-Media"
RESULT_HEADER = "X-TweetNook-Replay"
PLACEHOLDER_MEDIA = "placeholder"
FAKE_FAILURES = frozenset({"fail", "ratelimit", "offline"})
REPLAY_HOST_SUFFIXES = ("x.com", "twitter.com", "twimg.com")
# Only content-negotiation headers survive; cookies, authorization, CSRF and
# every other request header are dropped before anything reaches the source.
_FORWARDED_HEADERS = ("accept", "range")
_TRUE_VALUES = {"1", "true", "yes", "on"}


class ReplayBlockedError(httpx.ConnectError):
    """Raised instead of reaching a real network host while replay is active."""


@dataclass(frozen=True, slots=True)
class ReplaySettings:
    url: str
    password: str | None = None
    placeholder_media: bool = True

    @property
    def base_url(self) -> str:
        return self.url.rstrip("/")


_lock = threading.Lock()
_active: ReplaySettings | None = None


def dev_tools_enabled(env: Any = None) -> bool:
    env = os.environ if env is None else env
    return str(env.get(DEV_TOOLS_ENV, "")).strip().lower() in _TRUE_VALUES


def normalize_replay_url(value: str) -> str:
    url = value.strip()
    if url and "://" not in url:
        url = f"http://{url}"
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL as exc:
        raise ValueError(f"Invalid replay source address: {exc}") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.host:
        raise ValueError("Enter the replay source address, for example http://192.168.1.20:8000")
    if parsed.userinfo or parsed.query or parsed.fragment:
        raise ValueError(
            "Enter only the replay source address (no user name, password, query or fragment)."
        )
    return url.rstrip("/")


def configure_replay(replay_config: Any) -> ReplaySettings | None:
    """Activate (or clear) replay for this process from a ``ReplayConfig``."""

    global _active
    url = getattr(replay_config, "url", None) if replay_config is not None else None
    settings = (
        ReplaySettings(
            url=url,
            password=getattr(replay_config, "password", None),
            placeholder_media=bool(getattr(replay_config, "placeholder_media", True)),
        )
        if url
        else None
    )
    with _lock:
        _active = settings
    return settings


def active_replay() -> ReplaySettings | None:
    return _active


def is_replay_host(host: str | None) -> bool:
    if not host:
        return False
    host = host.lower().rstrip(".")
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in REPLAY_HOST_SUFFIXES)


def is_media_host(host: str | None) -> bool:
    host = (host or "").lower().rstrip(".")
    return host == "twimg.com" or host.endswith(".twimg.com")


def source_auth_header(password: str | None) -> str:
    token = base64.b64encode(f"tweetnook:{password or ''}".encode()).decode("ascii")
    return f"Basic {token}"


def fake_failure_keyword(request: httpx.Request) -> str | None:
    """Return the fake-failure keyword typed as auth_token, never the cookie itself."""

    for raw in request.headers.get_list("cookie"):
        try:
            jar = SimpleCookie()
            jar.load(raw)
        except CookieError:
            continue
        morsel = jar.get("auth_token")
        if morsel is not None and morsel.value in FAKE_FAILURES:
            return morsel.value
    return None


def _async_network_transport() -> httpx.AsyncBaseTransport:
    """The real connection to the replay source (patched in end-to-end tests)."""

    return httpx.AsyncHTTPTransport()


def _sync_network_transport() -> httpx.BaseTransport:
    return httpx.HTTPTransport()


class ReplayTransport(httpx.AsyncBaseTransport, httpx.BaseTransport):
    """Send Twitter/X requests to a replay source and block everything else."""

    def __init__(
        self,
        settings: ReplaySettings,
        *,
        async_transport: httpx.AsyncBaseTransport | None = None,
        sync_transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._async_transport = async_transport
        self._sync_transport = sync_transport

    def _replay_request(self, request: httpx.Request) -> httpx.Request:
        host = request.url.host
        if not is_replay_host(host):
            raise ReplayBlockedError(
                f"Replay mode blocked a request to {host or request.url}: only Twitter/X "
                "hosts are answered by the replay source, and nothing else is contacted.",
                request=request,
            )
        failure = fake_failure_keyword(request)
        if failure == "offline":
            # Same exception type a real outage raises; nothing is sent to the source.
            raise httpx.ConnectError(
                "Replay mode simulated a network failure (auth_token=offline).", request=request
            )
        base = httpx.URL(self.settings.base_url)
        prefix = base.raw_path.split(b"?", 1)[0].rstrip(b"/")
        raw_path = prefix + f"{REPLAY_PROXY_PREFIX}/{host.lower()}".encode() + request.url.raw_path
        headers = {
            name: request.headers[name] for name in _FORWARDED_HEADERS if name in request.headers
        }
        headers["authorization"] = source_auth_header(self.settings.password)
        if failure is not None:
            headers[FAILURE_HEADER] = failure
        if self.settings.placeholder_media and is_media_host(host):
            headers[MEDIA_HEADER] = PLACEHOLDER_MEDIA
        return httpx.Request(
            request.method,
            base.copy_with(raw_path=raw_path),
            headers=headers,
            extensions={"timeout": request.extensions.get("timeout", {})},
        )

    @staticmethod
    def _check_source_auth(response: httpx.Response, request: httpx.Request) -> None:
        if (
            response.status_code == 401
            and "basic" in response.headers.get("www-authenticate", "").lower()
        ):
            raise ReplayBlockedError(
                "The replay source rejected the saved password. Reconnect it in Setup.",
                request=request,
            )

    @staticmethod
    def _drop_source_cookies(response: httpx.Response) -> httpx.Response:
        # The source may issue its own Web session cookie; it must not land in the
        # cookie jar of a Twitter/X client.
        if "set-cookie" in response.headers:
            del response.headers["set-cookie"]
        return response

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        replay = self._replay_request(request)
        if self._async_transport is None:
            self._async_transport = _async_network_transport()
        response = await self._async_transport.handle_async_request(replay)
        if response.status_code == 401:
            try:
                self._check_source_auth(response, request)
            except ReplayBlockedError:
                await response.aclose()
                raise
        return self._drop_source_cookies(response)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        replay = self._replay_request(request)
        if self._sync_transport is None:
            self._sync_transport = _sync_network_transport()
        response = self._sync_transport.handle_request(replay)
        if response.status_code == 401:
            try:
                self._check_source_auth(response, request)
            except ReplayBlockedError:
                response.close()
                raise
        return self._drop_source_cookies(response)

    async def aclose(self) -> None:
        if self._async_transport is not None:
            await self._async_transport.aclose()

    def close(self) -> None:
        if self._sync_transport is not None:
            self._sync_transport.close()


def client_transport(transport: Any = None) -> Any:
    """Transport for a new httpx client: explicit > replay source > default network.

    Every httpx client TweetNook creates passes its optional ``transport``
    through this helper so replay mode can never fall through to the network.
    """

    if transport is not None:
        return transport
    settings = _active
    if settings is None:
        return None
    return ReplayTransport(settings)
