"""Owned, same-directory temporary files and atomic publication."""

from __future__ import annotations

import json
import os
import secrets
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any


def _create_temp(path: Path, *, follow_umask: bool) -> tuple[int, str]:
    if not follow_umask:
        return tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    # mkstemp always creates 0600, and reading the umask would mean changing it process-wide.
    # Creating the file lets the kernel apply it.
    name = str(path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp"))
    return os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666), name


@contextmanager
def owned_temp(path: Path, *, follow_umask: bool = False) -> Iterator[Path]:
    """Keep a temporary owned until publication, including interrupts and close failures.

    The file is private unless follow_umask is set, and publication keeps that mode.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = _create_temp(path, follow_umask=follow_umask)
    temporary = Path(name)
    try:
        os.close(fd)
        yield temporary
    finally:
        # Cleanup must never replace a serialization, write, close or publication error.
        with suppress(OSError):
            temporary.unlink(missing_ok=True)


def publish_file(
    temporary: Path, path: Path, *, mode: int | None = None, fsync: bool = True
) -> None:
    """Publish a closed owned temporary on the same filesystem."""
    with temporary.open("rb") as handle:
        if mode is not None:
            os.fchmod(handle.fileno(), mode)
        if fsync:
            os.fsync(handle.fileno())
    temporary.replace(path)


def atomic_write_bytes(
    path: Path,
    data: bytes,
    *,
    mode: int | None = None,
    fsync: bool = True,
    follow_umask: bool = False,
) -> None:
    with owned_temp(path, follow_umask=follow_umask) as temporary:
        temporary.write_bytes(data)
        publish_file(temporary, path, mode=mode, fsync=fsync)


def atomic_write_text(
    path: Path,
    data: str,
    *,
    mode: int | None = None,
    fsync: bool = True,
    follow_umask: bool = False,
) -> None:
    atomic_write_bytes(
        path, data.encode("utf-8"), mode=mode, fsync=fsync, follow_umask=follow_umask
    )


def atomic_write_json(
    path: Path, data: Any, *, mode: int | None = None, fsync: bool = True
) -> None:
    atomic_write_text(path, json.dumps(data, indent=2) + "\n", mode=mode, fsync=fsync)
