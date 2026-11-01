"""Small durable notice inbox, available before an archive exists."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path

from tweetnook.core.fileio import atomic_write_json


class NoticeStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        try:
            items = json.loads(path.read_text())
            self.items = items if isinstance(items, list) else []
        except (OSError, ValueError):
            self.items = []

    def _save(self):
        try:
            atomic_write_json(self.path, self.items)
        except OSError:
            # Keep notices readable while a full/read-only volume prevents persistence.
            pass

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
            self._save()

    def remove(self, notice_id):
        with self.lock:
            kept = [item for item in self.items if item["id"] != notice_id]
            if len(kept) != len(self.items):
                self.items = kept
                self._save()

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
                self._save()
            return bool(changed)

    def dismiss_all(self):
        with self.lock:
            changed = [item for item in self.items if not item["dismissed"]]
            for item in changed:
                item["dismissed"] = True
            if changed:
                self._save()
            return len(changed)

    def clear_dismissed(self):
        """Drop dismissed history; unread notices stay."""
        with self.lock:
            kept = [item for item in self.items if not item["dismissed"]]
            removed = len(self.items) - len(kept)
            if removed:
                self.items = kept
                self._save()
            return removed
