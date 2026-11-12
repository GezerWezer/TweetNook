"""Shared dependencies and server state for the TweetNook Web server."""

import hashlib
import hmac
import logging
import os
import re
import secrets
import threading
import time
import tomllib
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tweetnook.config import XDGPaths, config_lock, load_config_at
from tweetnook.core.passwords import RecentPasswordCheck, hash_password, password_needs_rehash

logger = logging.getLogger(__name__)

server_state: dict[str, Any] = {}
security = HTTPBasic(auto_error=False)
_password_check = RecentPasswordCheck()

# A successful password login is remembered in a signed cookie. Home Screen web
# apps on iOS keep cookies between launches, but not HTTP Basic credentials, so
# without it they ask for the password every time they open.
SESSION_COOKIE = "tweetnook_session"
SESSION_MAX_AGE = 400 * 24 * 60 * 60  # Browsers cap cookie lifetimes at 400 days.
SESSION_RENEW_AGE = 7 * 24 * 60 * 60
# Stands in for a saved secret in API responses. Every route that gets it back keeps the
# saved value, so a form can resubmit a secret it was never shown.
# web/fragments/setup.html compares against the same literal.
MASKED_SECRET = "********"
# Config fields no response returns in clear text, by config section.
SECRET_FIELDS: dict[str, tuple[str, ...]] = {
    "auth": ("auth_token", "ct0"),
    "web": ("password_hash",),
    "tagging": ("api_key",),
    "replay": ("password",),
}
SECRET_PATHS = frozenset(
    f"{section}.{field}" for section, fields in SECRET_FIELDS.items() for field in fields
)


def mask_secrets(section: str, values: dict[str, Any]) -> dict[str, Any]:
    """``values`` (one config section's fields) with each saved secret masked, in place."""
    for field in SECRET_FIELDS[section]:
        if values.get(field):
            values[field] = MASKED_SECRET
    return values


def without_masked(changes: dict[str, Any]) -> dict[str, Any]:
    """Dotted config ``changes`` minus secrets that came back masked, which stay as saved."""
    return {
        path: value
        for path, value in changes.items()
        if not (path in SECRET_PATHS and value == MASKED_SECRET)
    }


def unmask(value: Any, saved: Any) -> Any:
    """The submitted value, or the saved one when the mask came back."""
    return saved if value == MASKED_SECRET else value


_ISSUE_SESSION = "issue_web_session"
_PASSWORD_FILE_STAMP = "password_file_stamp"
_PASSWORD_WARNED_STAMP = "password_warned_stamp"
_session_key_lock = threading.Lock()


def _session_key() -> bytes:
    """Return the cookie signing key, kept beside the archive so sessions survive restarts."""
    with _session_key_lock:
        if key := server_state.get("session_key"):
            return key
        key = secrets.token_bytes(32)
        paths = server_state.get("paths")
        if paths is not None:
            path = paths.web_session_key_file
            try:
                stored = path.read_bytes()
                if len(stored) >= 32:
                    key = stored
                else:
                    path.write_bytes(key)
            except FileNotFoundError:
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(descriptor, "wb") as handle:
                        handle.write(key)
                except OSError:
                    pass  # Sessions then last until the server restarts.
            except OSError:
                pass
        server_state["session_key"] = key
        return key


def _session_signature(issued: int, password_hash: str) -> str:
    # Signing the password hash too signs every session out when it changes.
    message = f"{issued}:{password_hash}".encode()
    return hmac.new(_session_key(), message, hashlib.sha256).hexdigest()


def create_session_token(password_hash: str) -> str:
    issued = int(time.time())
    return f"{issued}.{_session_signature(issued, password_hash)}"


def session_age(token: str | None, password_hash: str) -> int | None:
    """Return a valid session token's age in seconds, or None."""
    # Cookies are untrusted: Unicode digits and non-ASCII signatures can raise before
    # password authentication gets its chance to recover the request.
    if not token or re.fullmatch(r"[0-9]{1,20}\.[0-9a-f]{64}", token) is None:
        return None
    issued_text, _, signature = token.partition(".")
    issued = int(issued_text)
    age = int(time.time()) - issued
    if not 0 <= age < SESSION_MAX_AGE:
        return None
    if not hmac.compare_digest(signature, _session_signature(issued, password_hash)):
        return None
    return age


def _rehash_saved_password(paths: XDGPaths, expected_hash: str, password: str) -> None:
    from tweetnook.web.runtime_config import save_server_config

    try:
        current, _ = load_config_at(paths)
        if current.web.password_hash == expected_hash:
            upgraded = save_server_config(paths, {"web.password_hash": hash_password(password)})
            server_state["password_hash"] = upgraded.web.password_hash
    except (OSError, ValueError) as error:
        # The password was correct. A read-only or full disk only delays the upgrade to a later
        # login; it must not lock the owner out of their archive.
        logger.warning("Could not upgrade the saved password hash: %s", error.__class__.__name__)


def _config_stamp(paths: XDGPaths) -> tuple[int, int, int] | None:
    try:
        saved = paths.config_file.stat()
    except OSError:
        return None
    # Config writes replace the file, so the inode changes even within one mtime tick.
    return (saved.st_ino, saved.st_mtime_ns, saved.st_size)


def watch_saved_password(paths: XDGPaths) -> None:
    """Apply password changes another process saves to ``paths``' config.toml."""
    # The startup hash was read before this point, so a save in between would match a stamp
    # taken now. A stamp no file has makes the first request re-read the password.
    server_state[_PASSWORD_FILE_STAMP] = ()


def _saved_password_hash(paths: XDGPaths) -> str | None:
    # Read directly rather than through load_config_at, which treats a missing file as an
    # empty configuration and so as "no password".
    with paths.config_file.open("rb") as handle:
        web = tomllib.load(handle).get("web", {})
    if not isinstance(web, dict):
        raise ValueError("web is not a table")
    value = web.get("password_hash")
    if value is not None and not isinstance(value, str):
        raise ValueError("web.password_hash is not a string")
    return value


def _follow_saved_password() -> None:
    # `tweetnook set-password` saves from its own process, and the server only learns
    # of it here. A missing or unreadable file keeps the current password: failing to read
    # the config must never turn authentication off.
    if _PASSWORD_FILE_STAMP not in server_state or not (paths := server_state.get("paths")):
        return
    stamp = _config_stamp(paths)
    if stamp is None or stamp == server_state[_PASSWORD_FILE_STAMP]:
        return
    with config_lock:
        if stamp == server_state[_PASSWORD_FILE_STAMP]:
            return
        try:
            server_state["password_hash"] = _saved_password_hash(paths)
        except (OSError, ValueError) as error:
            # Retried on every request: fixing a permission leaves the stamp unchanged.
            # The warning is once per version of the file.
            if server_state.get(_PASSWORD_WARNED_STAMP) != stamp:
                server_state[_PASSWORD_WARNED_STAMP] = stamp
                logger.warning(
                    "Kept the current web password; config.toml could not be read: %s",
                    error.__class__.__name__,
                )
            return
        server_state[_PASSWORD_FILE_STAMP] = stamp


def verify_credentials(
    request: Request,
    credentials: Annotated[HTTPBasicCredentials | None, Depends(security)],
) -> bool:
    _follow_saved_password()
    expected_hash = server_state.get("password_hash")
    if not expected_hash:
        return True

    age = session_age(request.cookies.get(SESSION_COOKIE), expected_hash)
    if age is not None:
        if age >= SESSION_RENEW_AGE:
            setattr(request.state, _ISSUE_SESSION, expected_hash)
        return True

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect password",
            headers={"WWW-Authenticate": "Basic"},
        )

    is_password_correct = _password_check.verify(credentials.password, expected_hash)

    if not is_password_correct:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect password",
            headers={"WWW-Authenticate": "Basic"},
        )
    with config_lock:
        # A password change during the slow check must not authenticate the old password,
        # including one saved by another process that no request has picked up yet.
        _follow_saved_password()
        if server_state.get("password_hash") != expected_hash:
            raise HTTPException(status_code=401, detail="Incorrect password")
        if password_needs_rehash(expected_hash) and (paths := server_state.get("paths")):
            _rehash_saved_password(paths, expected_hash, credentials.password)
        setattr(request.state, _ISSUE_SESSION, server_state.get("password_hash"))
    return True


async def verify_credentials_async(
    request: Request,
    credentials: Annotated[HTTPBasicCredentials | None, Depends(security)],
) -> bool:
    """verify_credentials for async handlers that serve many small requests.

    A session cookie that needs no renewal is checked inline, so only the password
    check, which is slow by design, costs a threadpool hop.
    """
    _follow_saved_password()
    expected_hash = server_state.get("password_hash")
    if not expected_hash:
        return True
    age = session_age(request.cookies.get(SESSION_COOKIE), expected_hash)
    if age is not None and age < SESSION_RENEW_AGE:
        return True
    return await run_in_threadpool(verify_credentials, request, credentials)


def remember_web_session(request: Request) -> None:
    """Issue a login cookie after an authenticated password change or initial setup."""
    with config_lock:
        setattr(request.state, _ISSUE_SESSION, server_state.get("password_hash"))


def _session_cookie(password_hash: str, scheme: str | None) -> bytes:
    cookie = (
        f"{SESSION_COOKIE}={create_session_token(password_hash)}; Max-Age={SESSION_MAX_AGE}; "
        "Path=/; HttpOnly; SameSite=Lax"
    )
    return (cookie + ("; Secure" if scheme == "https" else "")).encode("latin-1")


class WebSessionMiddleware:
    """Set the session cookie on responses to requests that logged in with the password."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # Request.state reads and writes this dict, so create it before any handler does.
        state = scope.setdefault("state", {})

        async def send_with_session(message: Message) -> None:
            if message["type"] == "http.response.start":
                # A pending response may outlive password rotation. Only the request that
                # committed the change may issue a cookie for the new generation.
                with config_lock:
                    password_hash = state.get(_ISSUE_SESSION)
                    current = server_state.get("password_hash")
                    if password_hash and password_hash == current:
                        cookie = _session_cookie(password_hash, scope.get("scheme"))
                        headers = [*message.get("headers", []), (b"set-cookie", cookie)]
                        message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_session)


def get_store():
    return server_state.get("store")


def require_store(store: Annotated[Any, Depends(get_store)]):
    if store is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "archive_not_ready",
                "message": "The archive is still being initialized.",
            },
        )
    return store
