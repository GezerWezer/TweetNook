"""Recoverable, root-contained file cleanup after prune commits its row deletion."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from tweetnook.core.exceptions import ConfigError
from tweetnook.core.fileio import atomic_write_json
from tweetnook.storage import ArchiveStore


def pending_file(database_path: Path) -> Path:
    return database_path.with_name("prune-pending.json")


def _owned_path(data_dir: Path, relative: str) -> Path | None:
    parts = Path(relative).parts
    if len(parts) < 2 or parts[0] != "media" or ".." in parts:
        return None
    path = (data_dir / "media").resolve()
    for part in parts[1:]:
        path /= part
        if path.is_symlink():
            return None
    return path


def pending_paths(data_dir: Path, database_path: Path) -> dict[str, Path]:
    intent = pending_file(database_path)
    invalid = ConfigError(
        f"Invalid prune cleanup file: {intent}. Delete it to continue; "
        "media files it listed may then stay on disk."
    )
    if intent.is_symlink():
        raise invalid
    try:
        payload = json.loads(intent.read_text("utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError as error:
        raise invalid from error
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise invalid
    paths = payload.get("paths")
    if not isinstance(paths, list) or any(not isinstance(path, str) for path in paths):
        raise invalid
    return {
        relative: path
        for relative in paths
        if (path := _owned_path(data_dir, relative)) is not None
    }


def referenced_media_files(store: ArchiveStore, data_dir: Path) -> set[Path]:
    return {(data_dir / relative).resolve() for relative in store.referenced_media_paths()}


def record_pending_files(data_dir: Path, database_path: Path, paths: list[Path]) -> None:
    pending = set(pending_paths(data_dir, database_path))
    media_root = (data_dir / "media").resolve()
    for path in paths:
        if path.is_relative_to(media_root):
            pending.add(str(Path("media") / path.relative_to(media_root)))
    if pending:
        atomic_write_json(pending_file(database_path), {"version": 1, "paths": sorted(pending)})


def cleanup_pending_files(
    store: ArchiveStore,
    data_dir: Path,
    database_path: Path,
    log: Callable[[str], None],
) -> tuple[int, int, int]:
    # A retry may follow a new reference to a formerly doomed file. Recheck saved
    # references after SQL commits, rather than trusting the old plan or intent.
    protected = referenced_media_files(store, data_dir)
    remaining: list[str] = []
    deleted = deleted_bytes = 0
    parents: set[Path] = set()
    for relative, path in sorted(pending_paths(data_dir, database_path).items()):
        if path in protected:
            continue
        try:
            size = path.stat().st_size
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError as error:
            remaining.append(relative)
            log(f"could not remove {relative}: {error}")
            continue
        deleted += 1
        deleted_bytes += size
        parents.add(path.parent)
    media_root = (data_dir / "media").resolve()
    for parent in sorted(parents, key=lambda value: len(value.parts), reverse=True):
        while parent != media_root and parent.is_relative_to(media_root):
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
    intent = pending_file(database_path)
    if remaining:
        atomic_write_json(intent, {"version": 1, "paths": remaining})
    else:
        intent.unlink(missing_ok=True)
    return deleted, deleted_bytes, len(remaining)
