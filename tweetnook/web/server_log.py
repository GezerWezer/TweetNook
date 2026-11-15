"""The Web server's own log: startup, scheduling, job launches and handled errors.

Run logs only cover workers. What the server process itself does (starting, scheduling a
sync, launching or failing to launch a job, a Setup step that failed) used to reach only the
terminal, which a background server discards. It is also written to a rotating file in the
data directory, which Settings > Logs shows.
"""

from __future__ import annotations

import logging
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

SERVER_LOG_MAX_BYTES = 5_000_000
SERVER_LOG_BACKUPS = 2
# The Logs view shows this much of the end of the current file.
SERVER_LOG_VIEW_BYTES = 1_000_000


class ServerLogFormatter(logging.Formatter):
    """Lines in the run log's shape: a UTC timestamp, then ``WARNING:`` or ``ERROR:`` for
    those levels, then where the line came from. The Logs view colors and counts lines by
    that prefix."""

    converter = time.gmtime

    def format(self, record: logging.LogRecord) -> str:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", self.converter(record.created))
        source = record.name.removeprefix("tweetnook.")
        # Uvicorn logs startup and shutdown on "uvicorn.error", a name kept for history;
        # shown as is, every routine line would read as an error.
        if source == "uvicorn.error":
            source = "uvicorn"
        prefix = f"{record.levelname}: " if record.levelno >= logging.WARNING else ""
        text = f"{stamp}.{int(record.msecs):03d}Z {prefix}{source}: {record.getMessage()}"
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            text += "\n" + record.exc_text
        if record.stack_info:
            text += "\n" + self.formatStack(record.stack_info)
        return text


def server_logging_config(log_file: Path) -> dict[str, Any]:
    """Uvicorn's logging setup, with the server log file added.

    It is passed to Uvicorn rather than attached afterwards, because Uvicorn applies its own
    configuration on start and that removes handlers from the loggers it names. TweetNook's
    loggers also keep writing to the terminal: once they have a handler, Python no longer
    prints their warnings through its fallback. The per-request access log stays out of the
    file; it is mostly the page's own polling, and request URLs carry search terms.
    """

    from uvicorn.config import LOGGING_CONFIG

    log_file.parent.mkdir(parents=True, exist_ok=True)
    config = deepcopy(LOGGING_CONFIG)
    config["formatters"]["server_file"] = {"()": f"{__name__}.ServerLogFormatter"}
    config["handlers"]["server_file"] = {
        "class": "logging.handlers.RotatingFileHandler",
        "formatter": "server_file",
        "filename": str(log_file),
        "maxBytes": SERVER_LOG_MAX_BYTES,
        "backupCount": SERVER_LOG_BACKUPS,
        "encoding": "utf-8",
    }
    config["loggers"]["uvicorn"]["handlers"] = [
        *config["loggers"]["uvicorn"]["handlers"],
        "server_file",
    ]
    config["loggers"]["tweetnook"] = {
        "handlers": ["default", "server_file"],
        "level": "INFO",
        "propagate": False,
    }
    return config


def _log_files(log_file: Path) -> list[Path]:
    """The current file and its backups, oldest first."""
    backups = [log_file.with_name(f"{log_file.name}.{n}") for n in range(SERVER_LOG_BACKUPS, 0, -1)]
    return [path for path in [*backups, log_file] if path.is_file()]


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def read_server_log(log_file: Path, *, max_bytes: int = SERVER_LOG_VIEW_BYTES) -> str:
    """The end of the current log file, starting at a whole line."""
    try:
        with log_file.open("rb") as handle:
            size = handle.seek(0, 2)
            start = max(0, size - max_bytes)
            handle.seek(start)
            data = handle.read()
    except OSError:
        return ""
    if start:
        newline = data.find(b"\n")
        data = data[newline + 1 :] if newline >= 0 else b""
    return _decode(data)


def read_full_server_log(log_file: Path) -> str:
    """The rotated backups and the current file, in the order they were written."""
    parts: list[str] = []
    for path in _log_files(log_file):
        try:
            text = _decode(path.read_bytes())
        except OSError:
            continue
        if text:
            parts.append(text if text.endswith("\n") else text + "\n")
    return "".join(parts)
