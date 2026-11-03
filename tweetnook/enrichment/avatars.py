"""Profile pictures: format checks, source URLs, cache publication, and the core avatars the
media step keeps for saved posts and the posts they quote."""

from __future__ import annotations

import json
import os
import re
import struct
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx

from tweetnook.core.fileio import atomic_write_bytes, atomic_write_text
from tweetnook.storage.backend import ArchiveStore

AVATAR_CANDIDATE_LIMIT = 8
AVATAR_FETCH_BUDGET_SECONDS = 20.0
AVATAR_SUFFIXES = (".jpg", ".png", ".gif", ".webp")
# Core avatars live below the on-demand cache. Its size cleanup only scans the top level,
# so these neither count toward the limit nor get evicted.
CORE_AVATAR_DIRNAME = "core"


def is_placeholder_avatar(content: bytes) -> bool:
    """Recognize one-pixel PNG fallbacks without rejecting real alpha images."""
    return (
        content.startswith(b"\x89PNG\r\n\x1a\n")
        and len(content) >= 24
        and content[12:16] == b"IHDR"
        and struct.unpack(">II", content[16:24]) == (1, 1)
    )


def avatar_format(content: bytes) -> tuple[str, str] | None:
    """Identify supported image headers without decoding or flattening alpha."""
    if is_placeholder_avatar(content):
        return None
    if (
        len(content) >= 33
        and content.startswith(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
        and all(struct.unpack(">II", content[16:24]))
    ):
        return ".png", "image/png"
    if len(content) >= 12 and content.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if (
        len(content) >= 13
        and content[:6] in (b"GIF87a", b"GIF89a")
        and all(struct.unpack("<HH", content[6:10]))
    ):
        return ".gif", "image/gif"
    if (
        len(content) >= 20
        and content[:4] == b"RIFF"
        and content[8:12] == b"WEBP"
        and content[12:16] in (b"VP8 ", b"VP8L", b"VP8X")
    ):
        return ".webp", "image/webp"
    return None


def cache_avatar(path: Path, content: bytes) -> None:
    """Publish complete files so concurrent requests cannot see a partial write."""
    atomic_write_bytes(path, content, fsync=False, follow_umask=True)


def avatar_variant_urls(url: str) -> list[str]:
    """Prefer the large variant, then the captured size and original image."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return []
    suffix = re.search(r"_(normal|400x400)(?=\.[^./]+$)", parts.path)
    if suffix is None:
        return [url]
    paths = [
        parts.path[: suffix.start()] + "_400x400" + parts.path[suffix.end() :],
        parts.path,
        parts.path[: suffix.start()] + parts.path[suffix.end() :],
    ]
    return list(dict.fromkeys(urlunsplit(parts._replace(path=path)) for path in paths))


def avatar_cache_paths(directory: Path, user_id: str) -> list[Path]:
    return [directory / f"{user_id}{suffix}" for suffix in AVATAR_SUFFIXES]


def core_avatar_dir(avatars_dir: Path) -> Path:
    return avatars_dir / CORE_AVATAR_DIRNAME


@dataclass(slots=True)
class CoreAvatarTarget:
    author_id: str
    source_urls: list[str]


@dataclass(slots=True)
class CoreAvatarOutcome:
    content_bytes: int = 0
    # Every candidate answered without a usable image, so retrying the same URLs is pointless.
    # A network error leaves this false and the author is tried again next run.
    unavailable: bool = False

    @property
    def saved(self) -> bool:
        return self.content_bytes > 0


@dataclass(slots=True)
class CoreAvatarState:
    """What the media step remembers about core avatars between runs.

    `saved` maps an author to the newest captured URL when their core file was saved. X gives
    a changed picture a new URL, so a newer capture means the file is out of date.
    `unavailable` maps an author to a newest URL that answered without a usable image; it
    isn't tried again until a newer capture appears.
    """

    saved: dict[str, str] = field(default_factory=dict)
    unavailable: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, state_file: Path) -> CoreAvatarState:
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(state, dict):
            return cls()
        return cls(
            saved=_url_map(state.get("saved")),
            unavailable=_url_map(state.get("unavailable")),
        )

    def save(self, state_file: Path) -> None:
        atomic_write_text(
            state_file,
            json.dumps(
                {"saved": self.saved, "unavailable": self.unavailable},
                separators=(",", ":"),
                sort_keys=True,
            ),
            fsync=False,
            follow_umask=True,
        )


@dataclass(slots=True)
class CoreAvatarPlan:
    targets: list[CoreAvatarTarget] = field(default_factory=list)
    adopted: int = 0
    released: int = 0


def _url_map(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(author_id): url for author_id, url in value.items() if isinstance(url, str)}


def _valid_avatar_file(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return avatar_format(handle.read(64)) is not None
    except OSError:
        return False


def _saved_avatar(directory: Path, user_id: str) -> Path | None:
    return next(
        (path for path in avatar_cache_paths(directory, user_id) if _valid_avatar_file(path)),
        None,
    )


def _safe_author_id(author_id: str) -> bool:
    return "/" not in author_id and "\\" not in author_id and author_id not in {".", ".."}


def prepare_core_avatars(
    store: ArchiveStore, avatars_dir: Path, state: CoreAvatarState
) -> CoreAvatarPlan:
    """Bring the core directory in line with the archive and list the downloads it needs.

    Pictures of authors who are no longer core return to the on-demand cache, where size
    cleanup can evict them. A picture that cache already holds for a core author moves in
    instead of being downloaded again. A core author needs a download when their file is
    missing or a newer URL was captured since it was saved, unless that URL already proved
    unavailable. Updates `state` in place.
    """
    core_dir = core_avatar_dir(avatars_dir)
    # One listing instead of a probe per author: the route deletes a corrupt file when it
    # meets one, and the next run downloads it again.
    files: dict[str, list[Path]] = {}
    try:
        for path in core_dir.iterdir():
            if path.suffix in AVATAR_SUFFIXES:
                files.setdefault(path.stem, []).append(path)
    except FileNotFoundError:
        pass
    core_ids = [author_id for author_id in store.core_author_ids() if _safe_author_id(author_id)]
    plan = CoreAvatarPlan()
    core_set = set(core_ids)
    for author_id, paths in files.items():
        if author_id in core_set:
            continue
        for path in paths:
            try:
                os.replace(path, avatars_dir / path.name)
            except OSError:
                continue
            plan.released += 1
    state.saved = {
        author_id: url
        for author_id, url in state.saved.items()
        if author_id in core_set and author_id in files
    }
    state.unavailable = {
        author_id: url for author_id, url in state.unavailable.items() if author_id in core_set
    }
    for author_id in core_ids:
        urls = store.avatar_source_urls(author_id, limit=AVATAR_CANDIDATE_LIMIT)
        newest = urls[0] if urls else None
        if author_id in files:
            known = state.saved.get(author_id)
            if newest is None or known == newest:
                continue
            if known is None:
                # Adopted from the cache or saved before sources were recorded; the newest
                # capture is the best guess for what it shows.
                state.saved[author_id] = newest
                continue
        else:
            cached = _saved_avatar(avatars_dir, author_id)
            if cached is not None:
                core_dir.mkdir(parents=True, exist_ok=True)
                try:
                    os.replace(cached, core_dir / cached.name)
                except OSError:
                    pass
                else:
                    plan.adopted += 1
                    if newest is not None:
                        state.saved[author_id] = newest
                    continue
        if newest is None or state.unavailable.get(author_id) == newest:
            continue
        # A refresh only wants the new picture; older URLs would fetch the one already saved.
        plan.targets.append(CoreAvatarTarget(author_id, urls[:1] if author_id in files else urls))
    return plan


async def download_core_avatar(
    client: httpx.AsyncClient, avatars_dir: Path, target: CoreAvatarTarget
) -> CoreAvatarOutcome:
    """Fetch one author's picture into the core directory, trying the same variants as the route.

    A failed refresh keeps the existing file. A local write failure raises: it isn't about
    this author and would repeat for the next.
    """
    core_dir = core_avatar_dir(avatars_dir)
    attempted: set[str] = set()
    deadline = time.monotonic() + AVATAR_FETCH_BUDGET_SECONDS
    network_failure = False
    for source_url in target.source_urls:
        for url in avatar_variant_urls(source_url):
            if url in attempted:
                continue
            attempted.add(url)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return CoreAvatarOutcome()
            try:
                response = await client.get(url, timeout=min(10.0, remaining))
            except (httpx.HTTPError, ValueError):
                network_failure = True
                continue
            image_format = avatar_format(response.content)
            if response.status_code != 200 or image_format is None:
                continue
            path = core_dir / f"{target.author_id}{image_format[0]}"
            cache_avatar(path, response.content)
            # A refreshed picture can change format, and the route would serve the old file
            # first if its suffix sorts earlier.
            for other in avatar_cache_paths(core_dir, target.author_id):
                if other != path:
                    with suppress(OSError):
                        other.unlink(missing_ok=True)
            return CoreAvatarOutcome(content_bytes=len(response.content))
    return CoreAvatarOutcome(unavailable=not network_failure)
