"""Reading an official X export: the ZIP or extracted directory, its manifest and its
window.YTD data files."""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from tweetnook.core.exceptions import (
    ConfigError,
)

_YTD_ASSIGNMENT_RE = re.compile(r"^\s*window\.YTD\.[A-Za-z0-9_.]+\s*=\s*", re.DOTALL)


_MANIFEST_ASSIGNMENT_RE = re.compile(r"^\s*window\.__THAR_CONFIG\s*=\s*", re.DOTALL)


@dataclass(slots=True)
class _ArchiveIdentity:
    account_id: str | None
    username: str | None
    display_name: str


class _ArchiveInput:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._zip: zipfile.ZipFile | None = None
        self._data_dir: Path | None = None
        if path.is_dir():
            if (path / "data" / "manifest.js").exists():
                self._data_dir = path / "data"
            elif (path / "manifest.js").exists():
                self._data_dir = path
            else:
                raise ConfigError("Archive directory is missing manifest.js.")
        elif path.is_file() and zipfile.is_zipfile(path):
            self._zip = zipfile.ZipFile(path)
        else:
            raise ConfigError(
                "Archive input must be a .zip file or an extracted archive directory."
            )
        try:
            self.manifest = self._load_manifest()
        except Exception:
            self.close()
            raise

    def __enter__(self) -> _ArchiveInput:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()

    def _normalize_name(self, relative_path: str) -> str:
        relative = relative_path.replace("\\", "/").lstrip("/")
        normalized = relative if relative.startswith("data/") else f"data/{relative}"
        parts = [part for part in PurePosixPath(normalized).parts if part not in {"", "."}]
        if any(part == ".." for part in parts):
            raise ConfigError("Archive paths must stay within the archive data/ directory.")
        return "/".join(parts)

    def _directory_path(self, relative_path: str) -> Path:
        assert self._data_dir is not None
        relative = self._normalize_name(relative_path)
        return self._data_dir / relative.removeprefix("data/")

    def read_text(self, relative_path: str) -> str:
        if self._zip is not None:
            return self._zip.read(self._normalize_name(relative_path)).decode("utf-8")
        return self._directory_path(relative_path).read_text(encoding="utf-8")

    @contextmanager
    def open_binary(self, relative_path: str):
        if self._zip is not None:
            handle = self._zip.open(self._normalize_name(relative_path), "r")
            try:
                yield handle
            finally:
                handle.close()
            return
        with self._directory_path(relative_path).open("rb") as handle:
            yield handle

    def iter_files(self, relative_dir: str) -> list[str]:
        prefix = self._normalize_name(relative_dir).rstrip("/") + "/"
        if self._zip is not None:
            return [
                name
                for name in sorted(self._zip.namelist())
                if name.startswith(prefix) and name != prefix
            ]
        directory = self._directory_path(relative_dir)
        if not directory.exists():
            return []
        return [f"{prefix}{path.name}" for path in sorted(directory.iterdir()) if path.is_file()]

    def _load_manifest(self) -> dict[str, Any]:
        raw = self.read_text("data/manifest.js")
        return _parse_assigned_json(raw, _MANIFEST_ASSIGNMENT_RE, "manifest.js")

    def dataset_files(self, key: str) -> list[str]:
        dataset = ((self.manifest.get("dataTypes") or {}).get(key)) or {}
        files = dataset.get("files") or []
        return [
            item["fileName"]
            for item in files
            if isinstance(item, dict) and isinstance(item.get("fileName"), str)
        ]

    def load_dataset(
        self,
        key: str,
        *,
        progress: Callable[[int, int], None] | None = None,
        item_status: Callable[[str], None] | None = None,
    ) -> tuple[list[Any], list[tuple[str, Any]]]:
        items: list[Any] = []
        parts: list[tuple[str, Any]] = []
        files = self.dataset_files(key)
        for index, filename in enumerate(files, start=1):
            if item_status is not None:
                item_status(filename)
            parsed = parse_ytd_js(self.read_text(filename), label=filename)
            parts.append((filename, parsed))
            if isinstance(parsed, list):
                items.extend(parsed)
            if progress is not None:
                progress(index, len(files))
        return items, parts

    def _digest_paths(self) -> list[str]:
        relevant: set[str] = {"data/manifest.js"}
        for dataset_key in (
            "account",
            "profile",
            "tweets",
            "tweetHeaders",
            "deletedTweets",
            "deletedTweetHeaders",
            "like",
        ):
            relevant.update(self.dataset_files(dataset_key))
        tweets_info = ((self.manifest.get("dataTypes") or {}).get("tweets")) or {}
        media_directory = tweets_info.get("mediaDirectory")
        if isinstance(media_directory, str):
            relevant.update(self.iter_files(media_directory))
        return sorted(relevant)

    def _file_size(self, relative_path: str) -> int:
        normalized = self._normalize_name(relative_path)
        if self._zip is not None:
            return self._zip.getinfo(normalized).file_size
        return self._directory_path(normalized).stat().st_size

    def digest_total_bytes(self) -> int:
        return sum(self._file_size(relative_path) for relative_path in self._digest_paths())

    def digest(
        self,
        *,
        progress: Callable[[int, int], None] | None = None,
        item_status: Callable[[str], None] | None = None,
        total_bytes: int | None = None,
    ) -> str:
        digest = hashlib.sha256()
        total_bytes = (
            total_bytes
            if progress is not None and total_bytes is not None
            else (self.digest_total_bytes() if progress is not None else 0)
        )
        processed_bytes = 0
        for relative_path in self._digest_paths():
            if item_status is not None:
                item_status(relative_path)
            digest.update(relative_path.encode("utf-8"))
            with self.open_binary(relative_path) as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
                    processed_bytes += len(chunk)
                    if progress is not None:
                        progress(processed_bytes, total_bytes)
        return digest.hexdigest()


def _parse_assigned_json(raw: str, pattern: re.Pattern[str], label: str) -> Any:
    stripped = pattern.sub("", raw, count=1).strip()
    if stripped.endswith(";"):
        stripped = stripped[:-1].rstrip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Failed to parse {label}.") from exc


def parse_ytd_js(raw: str, *, label: str = "YTD data") -> Any:
    return _parse_assigned_json(raw, _YTD_ASSIGNMENT_RE, label)


def _archive_identity(manifest: dict[str, Any], account_items: list[Any]) -> _ArchiveIdentity:
    account_entry = account_items[0] if account_items and isinstance(account_items[0], dict) else {}
    account_block = account_entry.get("account") or {}
    user_info = manifest.get("userInfo") or {}
    account_id = str(user_info.get("accountId") or account_block.get("accountId") or "").strip()
    if account_id.lower() == "unknown":
        account_id = ""
    username = str(user_info.get("userName") or account_block.get("username") or "").strip()
    if not account_id:
        raise ConfigError("Archive account.js/manifest.js does not include an account id.")
    if not username:
        raise ConfigError("Archive account.js/manifest.js does not include a username.")
    display_name = str(
        user_info.get("displayName") or account_block.get("accountDisplayName") or username
    ).strip()
    return _ArchiveIdentity(
        account_id=account_id or None,
        username=username or None,
        display_name=display_name or username or "Unknown User",
    )
