"""Cross-process locks used by archive and command lifecycles."""

from __future__ import annotations

import fcntl
import threading
from pathlib import Path
from typing import Any, ClassVar

from tweetnook.exceptions import ProcessLockError


class ProcessLock:
    """Non-blocking filesystem lock with optional same-process reentrancy."""

    _registry_guard = threading.Lock()
    _registry: ClassVar[dict[str, tuple[Any, int]]] = {}

    def __init__(self, path: Path, *, conflict_message: str | None = None):
        self.path = path
        self.conflict_message = conflict_message or (
            "Another tweetnook archive job is already running."
        )
        self._handle: Any | None = None
        self._registry_key: str | None = None

    def acquire(self, *, reentrant: bool = False) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        key = str(self.path.resolve())
        with self._registry_guard:
            held = self._registry.get(key)
            if held is not None:
                if not reentrant:
                    raise ProcessLockError(self.conflict_message)
                handle, count = held
                self._registry[key] = (handle, count + 1)
                self._handle = handle
                self._registry_key = key
                return
        handle = self.path.open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise ProcessLockError(self.conflict_message) from exc
        self._handle = handle
        self._registry_key = key
        with self._registry_guard:
            self._registry[key] = (handle, 1)

    def release(self) -> None:
        if self._handle is None or self._registry_key is None:
            return
        handle = self._handle
        key = self._registry_key
        with self._registry_guard:
            held_handle, count = self._registry[key]
            if count > 1:
                self._registry[key] = (held_handle, count - 1)
                self._handle = None
                self._registry_key = None
                return
            del self._registry[key]
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
        self._handle = None
        self._registry_key = None
