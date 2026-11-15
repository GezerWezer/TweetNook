"""Timestamp the merged Python console output of managed CLI workers."""

from __future__ import annotations

import faulthandler
import io
import os
import signal
import sys
import threading
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import TextIO

from tweetnook.jobs.activity_history import ACTIVITY_ORIGIN_ENV, ACTIVITY_RUN_ID_ENV


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
    _enable_stack_dumps(sys.stderr)
    stream = TimestampedWorkerStream(sys.stdout)
    sys.stdout = stream
    sys.stderr = stream


def _enable_stack_dumps(log: TextIO) -> None:
    """Write every thread's stack to console.log when the worker crashes or is terminated.

    The supervisor sends SIGTERM only after SIGINT went unanswered, so a worker that gets it
    is usually stuck, and its stack shows where. The dump is chained, so the signal still
    ends the worker. SIGUSR1 dumps on request and leaves the worker running. Nothing can
    catch SIGKILL or the OOM killer.
    """

    try:
        faulthandler.enable(file=log, all_threads=True)
        if hasattr(faulthandler, "register"):
            faulthandler.register(signal.SIGTERM, file=log, all_threads=True, chain=True)
            faulthandler.register(signal.SIGUSR1, file=log, all_threads=True)
    except (OSError, ValueError, AttributeError, io.UnsupportedOperation):
        # A stream without a file descriptor can't take a dump; logging still works.
        pass
