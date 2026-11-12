"""Assemble the web app's page from its shell and per-feature fragments.

``index.html`` is the page shell. A line holding only ``<!-- include NAME.html -->``
is replaced by the text of ``fragments/NAME.html``, which may include fragments in
turn. Each fragment keeps its own indentation and final newline, so the assembled
page is byte for byte what a single file holding the same markup would be.

``tests/js/page_scripts.cjs`` assembles the page the same way for the browser
harnesses; ``tests/test_web_page.py`` checks that the two agree.

The shell lists every script and stylesheet between ``<!-- bundle -->`` and
``<!-- /bundle -->``. ``dev/web/bundle.cjs`` concatenates them into one minified
script and one stylesheet and records a digest of their sources; ``bundle_assets``
serves those two files instead of the list while the digest still matches, so an
edited source is served as is until the bundle is rebuilt.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

_INCLUDE = re.compile(r"^[ \t]*<!-- include ([\w/-]+\.html) -->\n", re.MULTILINE)
_BUNDLE = re.compile(r"^([ \t]*)<!-- bundle -->\n(.*?)^[ \t]*<!-- /bundle -->\n", re.M | re.S)
# Blocking scripts and stylesheets are bundled; deferred vendor scripts keep their own tags.
_BUNDLED_SCRIPT = re.compile(r'^[ \t]*<script src="/static/(js/[^"]+)"></script>\n', re.M)
_BUNDLED_STYLE = re.compile(r'^[ \t]*<link rel="stylesheet" href="/static/(css/[^"]+)">\n', re.M)


def assemble_page(web_root: Path) -> tuple[str, list[tuple[Path, int]]]:
    """Return the assembled page and the files it was built from, shell first.

    Each file comes with its modification time from before it was read, so a
    caller that caches the page sees any edit made while it was being assembled.
    """
    fragments = (web_root / "fragments").resolve()
    sources: list[tuple[Path, int]] = []

    def expand(path: Path, including: tuple[Path, ...]) -> str:
        if path in including:
            raise ValueError(f"{path.name} includes itself")
        sources.append((path, path.stat().st_mtime_ns))
        text = path.read_text(encoding="utf-8")
        if including and not text.endswith("\n"):
            raise ValueError(f"{path.name} must end with a newline")

        def include(match: re.Match[str]) -> str:
            target = (fragments / match[1]).resolve()
            if not target.is_relative_to(fragments):
                raise ValueError(f"{match[1]} is outside {fragments}")
            return expand(target, (*including, path))

        return _INCLUDE.sub(include, text)

    return expand(web_root / "index.html", ()), sources


def sources_digest(static_root: Path, names: list[str]) -> str:
    """Digest of the named static files, in order; ``dev/web/bundle.cjs`` computes the same."""
    digest = hashlib.sha256()
    for name in names:
        digest.update(name.encode() + b"\0" + (static_root / name).read_bytes() + b"\0")
    return digest.hexdigest()


def bundle_assets(page: str, web_root: Path, sources: list[tuple[Path, int]]) -> str:
    """Serve the bundled script and stylesheet in place of the listed ones while current.

    Every file the decision reads is appended to ``sources``, so a cached page is
    rebuilt when a listed asset, a bundle or its manifest changes.
    """
    match = _BUNDLE.search(page)
    if match is None:
        return page
    indent, block = match[1], match[2]
    static_root = web_root / "static"
    scripts = _BUNDLED_SCRIPT.findall(block)
    styles = _BUNDLED_STYLE.findall(block)
    manifest = static_root / "bundle.json"
    try:
        for name in ("bundle.json", *scripts, *styles):
            sources.append((static_root / name, (static_root / name).stat().st_mtime_ns))
        recorded = json.loads(manifest.read_text(encoding="utf-8"))
        current = recorded == {
            "js": sources_digest(static_root, scripts),
            "css": sources_digest(static_root, styles),
        }
    except (OSError, ValueError):
        current = False
    if not current:
        return page[: match.start()] + block + page[match.end() :]
    kept = _BUNDLED_STYLE.sub("", _BUNDLED_SCRIPT.sub("", block))
    bundled = (
        f'{indent}<link rel="stylesheet" href="/static/css/bundle.min.css">\n'
        f"{kept}"
        f'{indent}<script src="/static/js/bundle.min.js"></script>\n'
    )
    return page[: match.start()] + bundled + page[match.end() :]
