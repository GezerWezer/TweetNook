"""Shared dependencies and server state for the TweetNook Web server."""

import hashlib
import secrets
from typing import Annotated, Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

server_state: dict[str, Any] = {}
security = HTTPBasic(auto_error=False)


def verify_credentials(
    credentials: Annotated[HTTPBasicCredentials | None, Depends(security)],
) -> bool:
    expected_hash = server_state.get("password_hash")
    if not expected_hash:
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
    return True


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
