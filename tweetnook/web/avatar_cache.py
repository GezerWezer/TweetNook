"""Avatar cache access tracking and weekly size enforcement."""

from __future__ import annotations

import json
import os
import stat as stat_module
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Thread

from tweetnook.config import AppConfig, XDGPaths

AVATAR_ACCESS_TOUCH_INTERVAL_SECONDS = 24 * 60 * 60
AVATAR_CLEANUP_INTERVAL_SECONDS = 7 * 24 * 60 * 60
AVATAR_CLEANUP_POLL_SECONDS = 60 * 60
AVATAR_SUFFIXES = frozenset({".gif", ".jpeg", ".jpg", ".png", ".webp"})

_cache_lock = threading.RLock()


@dataclass(frozen=True, slots=True)
class AvatarCleanupResult:
    deleted_files: int
    deleted_bytes: int
    remaining_files: int
    remaining_bytes: int
    errors: int


def mark_avatar_accessed(
    path: Path,
    *,
    now: float | None = None,
    minimum_interval: float = AVATAR_ACCESS_TOUCH_INTERVAL_SECONDS,
) -> bool:
    """Refresh an avatar's mtime when its recorded access is sufficiently old."""
    timestamp = time.time() if now is None else now
    with _cache_lock:
        try:
            stat = path.stat()
            if timestamp - stat.st_mtime < minimum_interval:
                return False
            os.utime(path, (stat.st_atime, timestamp))
        except OSError:
            # Access tracking must never prevent an otherwise valid cache hit.
            return False
    return True


def cleanup_avatar_cache(avatars_dir: Path, limit_bytes: int) -> AvatarCleanupResult:
    """Remove least-recently-used avatar files until the cache fits the limit."""
    candidates: list[tuple[int, str, Path, int]] = []
    errors = 0

    with _cache_lock:
        try:
            entries = list(avatars_dir.iterdir())
        except FileNotFoundError:
            entries = []
        except OSError:
            return AvatarCleanupResult(0, 0, 0, 0, 1)

        for path in entries:
            if path.suffix.casefold() not in AVATAR_SUFFIXES or path.is_symlink():
                continue
            try:
                stat = path.stat()
            except OSError:
                errors += 1
                continue
            if not stat_module.S_ISREG(stat.st_mode):
                continue
            candidates.append((stat.st_mtime_ns, path.name, path, stat.st_size))

        total_bytes = sum(item[3] for item in candidates)
        remaining_files = len(candidates)
        deleted_files = 0
        deleted_bytes = 0

        if total_bytes > limit_bytes:
            for _mtime_ns, _name, path, size in sorted(candidates):
                if total_bytes <= limit_bytes:
                    break
                try:
                    path.unlink()
                except OSError:
                    errors += 1
                    continue
                total_bytes -= size
                remaining_files -= 1
                deleted_files += 1
                deleted_bytes += size

    return AvatarCleanupResult(
        deleted_files=deleted_files,
        deleted_bytes=deleted_bytes,
        remaining_files=remaining_files,
        remaining_bytes=total_bytes,
        errors=errors,
    )


class AvatarCacheManager:
    """Run avatar-cache size enforcement independently of archive syncs."""

    def __init__(self, paths: XDGPaths, config: AppConfig) -> None:
        self.paths = paths
        self.config = config
        self._stop = threading.Event()
        self._thread: Thread | None = None
        self._state = self._load_state()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(
            target=self._loop,
            daemon=True,
            name="tweetnook-avatar-cache-cleanup",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._thread = None

    def tick(self, *, now: float | None = None) -> AvatarCleanupResult | None:
        """Run one due cleanup and return its result; return None when not due or disabled."""
        if not self.config.web.avatar_cache_limit_enabled:
            return None

        timestamp = time.time() if now is None else now
        last_run_at = self._state.get("last_run_at")
        if last_run_at is not None:
            try:
                if timestamp - float(last_run_at) < AVATAR_CLEANUP_INTERVAL_SECONDS:
                    return None
            except (TypeError, ValueError):
                pass

        limit_bytes = self.config.web.avatar_cache_limit_mb * 1024 * 1024
        result = cleanup_avatar_cache(self.paths.media_dir / "avatars", limit_bytes)
        self._state = {
            "last_run_at": timestamp,
            "next_run_at": timestamp + AVATAR_CLEANUP_INTERVAL_SECONDS,
            "last_result": asdict(result),
        }
        self._save_state()
        return result

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                # Cache maintenance must never bring down the Web service.
                pass
            self._stop.wait(AVATAR_CLEANUP_POLL_SECONDS)

    def _load_state(self) -> dict[str, object]:
        try:
            state = json.loads(self.paths.avatar_cache_state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return state if isinstance(state, dict) else {}

    def _save_state(self) -> None:
        path = self.paths.avatar_cache_state_file
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(self._state, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.replace(path)
        except OSError:
            temporary.unlink(missing_ok=True)
