"""Background preparation of derived data and pages the Web UI reads first."""

from __future__ import annotations

import threading
from typing import Any


class ArchiveWarmer:
    """Rebuild the like order and read hot pages off the request path.

    Requests that arrive while a pass runs coalesce into one follow-up pass, so
    several finished jobs never stack warm-ups. stop() interrupts page reads and waits
    for the current pass.
    """

    def __init__(self, store: Any) -> None:
        self._store = store
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._pending = False
        self._stopped = False

    def request(self) -> None:
        with self._lock:
            if self._stopped:
                return
            if self._thread is not None:
                self._pending = True
                return
            self._thread = threading.Thread(
                target=self._run, name="tweetnook-archive-warmup", daemon=True
            )
            self._thread.start()

    def _run(self) -> None:
        while True:
            for warm in (self._store.warm_like_order, self._store.warm_read_cache):
                if self._stopped:
                    break
                try:
                    warm()
                except Exception:
                    pass  # Warming is an optimization; requests rebuild on demand.
            with self._lock:
                if self._stopped or not self._pending:
                    self._thread = None
                    return
                self._pending = False

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            thread = self._thread
        self._store.interrupt_warm_reads()
        if thread is not None:
            thread.join()
