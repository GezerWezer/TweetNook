"""Shared dependencies and server state for the TweetNook Web server."""

import hashlib
import hmac
import os
import secrets
import threading
import time
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette.types import ASGIApp, Message, Receive, Scope, Send

server_state: dict[str, Any] = {}
security = HTTPBasic(auto_error=False)

# A successful password login is remembered in a signed cookie. Home Screen web
# apps on iOS keep cookies between launches, but not HTTP Basic credentials, so
# without it they ask for the password every time they open.
SESSION_COOKIE = "tweetnook_session"
SESSION_MAX_AGE = 400 * 24 * 60 * 60  # Browsers cap cookie lifetimes at 400 days.
SESSION_RENEW_AGE = 7 * 24 * 60 * 60
_ISSUE_SESSION = "issue_web_session"
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
    issued_text, _, signature = (token or "").partition(".")
    if not issued_text.isdigit():
        return None
    issued = int(issued_text)
    age = int(time.time()) - issued
    if not 0 <= age < SESSION_MAX_AGE:
        return None
    if not hmac.compare_digest(signature, _session_signature(issued, password_hash)):
        return None
    return age


def verify_credentials(
    request: Request,
    credentials: Annotated[HTTPBasicCredentials | None, Depends(security)],
) -> bool:
    expected_hash = server_state.get("password_hash")
    if not expected_hash:
        return True

    age = session_age(request.cookies.get(SESSION_COOKIE), expected_hash)
    if age is not None:
        if age >= SESSION_RENEW_AGE:
            setattr(request.state, _ISSUE_SESSION, True)
        return True

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect password",
            headers={"WWW-Authenticate": "Basic"},
        )

    input_hash = hashlib.sha256(credentials.password.encode("utf8")).hexdigest()
    is_password_correct = secrets.compare_digest(input_hash, expected_hash)

    if not is_password_correct:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect password",
            headers={"WWW-Authenticate": "Basic"},
        )
    setattr(request.state, _ISSUE_SESSION, True)
    return True


def remember_web_session(request: Request) -> None:
    """Issue a login cookie after an authenticated password change or initial setup."""
    setattr(request.state, _ISSUE_SESSION, True)


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
            password_hash = server_state.get("password_hash")
            if message["type"] == "http.response.start" and state.get(_ISSUE_SESSION):
                if password_hash:
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


def get_server_state():
    return server_state
