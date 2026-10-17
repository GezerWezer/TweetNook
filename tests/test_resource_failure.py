from __future__ import annotations

import errno
import sqlite3

import pytest

from tweetnook.resource_failure import StorageExhaustedError, raise_if_storage_exhausted


@pytest.mark.parametrize(
    "error",
    [
        OSError(errno.ENOSPC, "No space left on device"),
        OSError(errno.EDQUOT, "Disk quota exceeded"),
        sqlite3.OperationalError("database or disk is full"),
    ],
)
def test_storage_failure_is_fatal_even_when_wrapped(error):
    try:
        raise RuntimeError("follow-up failed") from error
    except RuntimeError as wrapped:
        with pytest.raises(StorageExhaustedError, match="Free or add disk space"):
            raise_if_storage_exhausted(wrapped)


def test_unrelated_io_and_database_errors_are_not_storage_exhaustion():
    for error in [PermissionError("denied"), sqlite3.OperationalError("database is locked")]:
        raise_if_storage_exhausted(error)
