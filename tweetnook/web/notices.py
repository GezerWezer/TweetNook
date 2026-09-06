"""Small durable notice inbox, available before an archive exists."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle)
            handle.flush()
            os.fsync(handle.fileno())
        Path(name).replace(path)
    finally:
        Path(name).unlink(missing_ok=True)


class NoticeStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        try:
            items = json.loads(path.read_text())
            self.items = items if isinstance(items, list) else []
        except (OSError, ValueError):
            self.items = []

    def add(self, notice_id, title, message, *, severity="error", action="activity"):
        with self.lock:
            if any(item["id"] == notice_id for item in self.items):
                return
            self.items.append(
                dict(
                    id=notice_id,
                    severity=severity,
                    title=title,
                    message=message,
                    created_at=datetime.now(UTC).isoformat(),
                    dismissed=False,
                    action=action,
                )
            )
            self.items = self.items[-100:]
            atomic_json(self.path, self.items)

    def list(self):
        with self.lock:
            return [dict(item) for item in reversed(self.items)]

    def dismiss(self, notice_id):
        with self.lock:
            for item in self.items:
                if item["id"] == notice_id:
                    item["dismissed"] = True
            atomic_json(self.path, self.items)
