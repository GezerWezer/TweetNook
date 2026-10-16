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

    def add(self, notice_id, title, message, *, severity="error", action="activity", run_id=None):
        with self.lock:
            if any(item["id"] == notice_id for item in self.items):
                return
            item = dict(
                id=notice_id,
                severity=severity,
                title=title,
                message=message,
                created_at=datetime.now(UTC).isoformat(),
                dismissed=False,
                action=action,
            )
            if run_id:
                # Lets the inbox open the log of the run behind the notice.
                item["run_id"] = run_id
            self.items.append(item)
            self.items = self.items[-100:]
            atomic_json(self.path, self.items)

    def list(self):
        with self.lock:
            return [dict(item) for item in reversed(self.items)]

    def dismiss(self, notice_id):
        with self.lock:
            changed = [
                item for item in self.items if item["id"] == notice_id and not item["dismissed"]
            ]
            for item in changed:
                item["dismissed"] = True
            if changed:
                atomic_json(self.path, self.items)
            return bool(changed)

    def dismiss_all(self):
        with self.lock:
            changed = [item for item in self.items if not item["dismissed"]]
            for item in changed:
                item["dismissed"] = True
            if changed:
                atomic_json(self.path, self.items)
            return len(changed)

    def clear_dismissed(self):
        """Drop dismissed history; unread notices stay."""
        with self.lock:
            kept = [item for item in self.items if not item["dismissed"]]
            removed = len(self.items) - len(kept)
            if removed:
                self.items = kept
                atomic_json(self.path, self.items)
            return removed
