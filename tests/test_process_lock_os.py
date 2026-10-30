"""The process lock excludes other processes, not only other holders in this interpreter.

Same-interpreter conflict tests stop at ProcessLock's registry before flock is reached, so
they would pass with the OS lock removed. Each check here asks an independent interpreter
to take the lock file with flock itself.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import tweetnook.storage.backend as backend
from tweetnook.core.locking import ProcessLock
from tweetnook.jobs.context import locked_archive_job
from tweetnook.storage import ArchiveStore
from tweetnook.storage.schema import SCHEMA_VERSION

# Uses only the standard library, so the probe doesn't share any code with the lock.
_PROBE = """
import fcntl, sys
with open(sys.argv[1], "a+") as handle:
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("held")
    else:
        print("free")
"""


def other_process_sees(path: Path) -> str:
    result = subprocess.run(
        [sys.executable, "-I", "-c", _PROBE, str(path)],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return result.stdout.strip()


def test_held_lock_excludes_another_process_until_release(tmp_path) -> None:
    lock = ProcessLock(tmp_path / "sync.lock")
    lock.acquire()
    try:
        assert other_process_sees(lock.path) == "held"
    finally:
        lock.release()
    assert other_process_sees(lock.path) == "free"


@pytest.mark.parametrize("first_released", ["outer", "inner"])
def test_reentrant_holders_keep_the_lock_until_the_last_releases(
    tmp_path, first_released: str
) -> None:
    path = tmp_path / "sync.lock"
    outer = ProcessLock(path)
    inner = ProcessLock(path)
    outer.acquire()
    inner.acquire(reentrant=True)
    first, last = (outer, inner) if first_released == "outer" else (inner, outer)
    try:
        first.release()
        assert other_process_sees(path) == "held"
    finally:
        last.release()
    assert other_process_sees(path) == "free"


async def test_archive_job_holds_the_lock_and_releases_it_after_a_failure(paths, config) -> None:
    ArchiveStore(paths.database_path, create=True, config=config).close()

    with pytest.raises(RuntimeError, match="job failed"):
        async with locked_archive_job(config=config, paths=paths):
            assert other_process_sees(paths.lock_file) == "held"
            raise RuntimeError("job failed")

    assert other_process_sees(paths.lock_file) == "free"


async def test_failed_nested_schema_open_keeps_the_outer_job_lock(
    paths, config, monkeypatch
) -> None:
    ArchiveStore(paths.database_path, create=True, config=config).close()
    async with locked_archive_job(config=config, paths=paths):
        # An older user_version makes the next open take the lock reentrantly to migrate.
        with sqlite3.connect(paths.database_path) as connection:
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION - 1}")
        connection.close()

        def failing_store(*args, **kwargs):
            raise sqlite3.OperationalError("migration failed")

        monkeypatch.setattr(backend, "ArchiveStore", failing_store)
        with pytest.raises(sqlite3.OperationalError, match="migration failed"):
            backend.open_archive_store(paths, create=False, config=config)
        assert other_process_sees(paths.lock_file) == "held"

    assert other_process_sees(paths.lock_file) == "free"
