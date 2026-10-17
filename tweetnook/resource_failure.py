"""Recognize storage exhaustion before retryable item errors hide it."""

from __future__ import annotations

import errno
import sqlite3

from tweetnook.exceptions import TweetNookError

STORAGE_RECOVERY = (
    "Storage is full. Free or add disk space, then run Sync now to resume. "
    "Saved archive data is preserved; remaining media and link previews are pending."
)


class StorageExhaustedError(TweetNookError):
    """A run-wide storage failure that must stop subsequent writes."""


def is_storage_exhausted(error: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, StorageExhaustedError):
            return True
        if isinstance(current, OSError) and current.errno in {errno.ENOSPC, errno.EDQUOT}:
            return True
        if isinstance(current, sqlite3.Error) and (
            getattr(current, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL
            or "database or disk is full" in str(current).lower()
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


def raise_if_storage_exhausted(error: BaseException) -> None:
    if isinstance(error, StorageExhaustedError):
        raise error
    if is_storage_exhausted(error):
        raise StorageExhaustedError(STORAGE_RECOVERY) from error
