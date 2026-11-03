"""Profile-picture recognition, source URLs and cache publication shared by every avatar fetch."""

from __future__ import annotations

import re
import struct
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from tweetnook.core.fileio import atomic_write_bytes

AVATAR_CANDIDATE_LIMIT = 8


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
