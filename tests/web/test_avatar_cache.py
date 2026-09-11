from __future__ import annotations

import json
import os
from pathlib import Path

from tweetnook.config import AppConfig, WebConfig, XDGPaths
from tweetnook.web.avatar_cache import (
    AVATAR_ACCESS_TOUCH_INTERVAL_SECONDS,
    AVATAR_CLEANUP_INTERVAL_SECONDS,
    AvatarCacheManager,
    cleanup_avatar_cache,
    mark_avatar_accessed,
)


def _paths(tmp_path: Path) -> XDGPaths:
    return XDGPaths(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
    )


def _avatar(directory: Path, name: str, size: int, mtime: float) -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"a" * size)
    os.utime(path, (mtime, mtime))
    return path


def test_mark_avatar_accessed_refreshes_mtime_at_most_daily(tmp_path: Path) -> None:
    avatar = _avatar(tmp_path, "42.jpg", 4, 1_000.0)

    assert (
        mark_avatar_accessed(
            avatar,
            now=1_000.0 + AVATAR_ACCESS_TOUCH_INTERVAL_SECONDS - 1,
        )
        is False
    )
    assert avatar.stat().st_mtime == 1_000.0

    refreshed_at = 1_000.0 + AVATAR_ACCESS_TOUCH_INTERVAL_SECONDS
    assert mark_avatar_accessed(avatar, now=refreshed_at) is True
    assert avatar.stat().st_mtime == refreshed_at
    assert mark_avatar_accessed(avatar, now=refreshed_at + 10) is False
    assert avatar.stat().st_mtime == refreshed_at


def test_mark_avatar_accessed_ignores_disappearing_file(tmp_path: Path) -> None:
    assert mark_avatar_accessed(tmp_path / "missing.jpg", now=1_000.0) is False


def test_cleanup_removes_oldest_avatars_until_cache_fits(tmp_path: Path) -> None:
    avatars_dir = tmp_path / "avatars"
    oldest = _avatar(avatars_dir, "old.png", 400_000, 100.0)
    middle = _avatar(avatars_dir, "middle.jpg", 400_000, 200.0)
    newest = _avatar(avatars_dir, "new.webp", 400_000, 300.0)
    unrelated = _avatar(avatars_dir, ".DS_Store", 900_000, 1.0)

    result = cleanup_avatar_cache(avatars_dir, 800_000)

    assert result.deleted_files == 1
    assert result.deleted_bytes == 400_000
    assert result.remaining_files == 2
    assert result.remaining_bytes == 800_000
    assert result.errors == 0
    assert not oldest.exists()
    assert middle.exists()
    assert newest.exists()
    assert unrelated.exists()


def test_cleanup_uses_name_as_stable_tiebreaker(tmp_path: Path) -> None:
    avatars_dir = tmp_path / "avatars"
    first = _avatar(avatars_dir, "a.jpg", 2, 100.0)
    second = _avatar(avatars_dir, "b.jpg", 2, 100.0)

    result = cleanup_avatar_cache(avatars_dir, 2)

    assert result.deleted_files == 1
    assert not first.exists()
    assert second.exists()


def test_recently_served_legacy_avatar_survives_older_untouched_file(tmp_path: Path) -> None:
    avatars_dir = tmp_path / "avatars"
    served = _avatar(avatars_dir, "served.jpg", 600_000, 100.0)
    untouched = _avatar(avatars_dir, "untouched.jpg", 600_000, 200.0)

    assert mark_avatar_accessed(served, now=1_000_000.0) is True
    result = cleanup_avatar_cache(avatars_dir, 1_048_576)

    assert result.deleted_files == 1
    assert served.exists()
    assert not untouched.exists()


def test_cleanup_missing_directory_is_empty(tmp_path: Path) -> None:
    result = cleanup_avatar_cache(tmp_path / "missing", 1)

    assert result.deleted_files == 0
    assert result.remaining_files == 0
    assert result.remaining_bytes == 0
    assert result.errors == 0


def test_manager_runs_immediately_then_once_per_week(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    avatars_dir = paths.media_dir / "avatars"
    oldest = _avatar(avatars_dir, "old.jpg", 700_000, 100.0)
    newest = _avatar(avatars_dir, "new.jpg", 700_000, 200.0)
    manager = AvatarCacheManager(
        paths,
        AppConfig(web=WebConfig(avatar_cache_limit_mb=1)),
    )

    first = manager.tick(now=1_000.0)

    assert first is not None
    assert first.deleted_files == 1
    assert not oldest.exists()
    assert newest.exists()
    state = json.loads(paths.avatar_cache_state_file.read_text(encoding="utf-8"))
    assert state["last_run_at"] == 1_000.0
    assert state["next_run_at"] == 1_000.0 + AVATAR_CLEANUP_INTERVAL_SECONDS
    assert manager.tick(now=1_000.0 + AVATAR_CLEANUP_INTERVAL_SECONDS - 1) is None
    restarted = AvatarCacheManager(paths, manager.config)
    assert restarted.tick(now=1_000.0 + AVATAR_CLEANUP_INTERVAL_SECONDS - 1) is None

    _avatar(avatars_dir, "later.jpg", 700_000, 300.0)
    second = manager.tick(now=1_000.0 + AVATAR_CLEANUP_INTERVAL_SECONDS)

    assert second is not None
    assert second.deleted_files == 1
    assert not newest.exists()


def test_manager_preserves_over_limit_cache_when_disabled(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    avatar = _avatar(paths.media_dir / "avatars", "old.jpg", 2_000_000, 100.0)
    manager = AvatarCacheManager(
        paths,
        AppConfig(web=WebConfig(avatar_cache_limit_enabled=False, avatar_cache_limit_mb=1)),
    )

    assert manager.tick(now=1_000.0) is None
    assert avatar.exists()
    assert not paths.avatar_cache_state_file.exists()
