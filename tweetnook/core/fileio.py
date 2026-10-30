"""Owned, same-directory temporary files and atomic publication."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any


@contextmanager
def owned_temp(path: Path) -> Iterator[Path]:
    """Keep a temporary owned until publication, including interrupts and close failures."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
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
    path: Path, data: bytes, *, mode: int | None = None, fsync: bool = True
) -> None:
    with owned_temp(path) as temporary:
        temporary.write_bytes(data)
        publish_file(temporary, path, mode=mode, fsync=fsync)


def atomic_write_text(
    path: Path, data: str, *, mode: int | None = None, fsync: bool = True
) -> None:
    atomic_write_bytes(path, data.encode("utf-8"), mode=mode, fsync=fsync)


def atomic_write_json(
    path: Path, data: Any, *, mode: int | None = None, fsync: bool = True
) -> None:
    atomic_write_text(path, json.dumps(data, indent=2) + "\n", mode=mode, fsync=fsync)
