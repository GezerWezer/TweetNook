"""Timestamp the merged Python console output of managed CLI workers."""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import TextIO

from tweetnook.activity_history import ACTIVITY_ORIGIN_ENV, ACTIVITY_RUN_ID_ENV


class TimestampedWorkerStream:
    """Prefix each nonempty physical line, preserving partial writes and flushes."""

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream
        self._line_start = True
        self._lock = threading.RLock()

    def write(self, text: str) -> int:
        with self._lock:
            parts = []
            line_start = self._line_start
            for index, part in enumerate(text.split("\n")):
                if index:
                    parts.append("\n")
                    line_start = True
                if part:
                    if line_start:
                        stamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
                        parts.append(f"{stamp} ")
                    parts.append(part)
                    line_start = False
            self._stream.write("".join(parts))
            self._line_start = line_start
        return len(text)

    def writelines(self, lines: Iterable[str]) -> None:
        for line in lines:
            self.write(line)

    def flush(self) -> None:
        with self._lock:
            self._stream.flush()

    def __getattr__(self, name: str):
        return getattr(self._stream, name)


def configure_worker_output() -> None:
    """Managed workers already merge stderr into stdout in console.log."""

    if not os.environ.get(ACTIVITY_RUN_ID_ENV) or os.environ.get(ACTIVITY_ORIGIN_ENV) not in {
        "web",
        "schedule",
    }:
        return
    if isinstance(sys.stdout, TimestampedWorkerStream):
        return
    stream = TimestampedWorkerStream(sys.stdout)
    sys.stdout = stream
    sys.stderr = stream
