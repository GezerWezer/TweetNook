"""Assemble the web app's page from its shell and per-feature fragments.

``index.html`` is the page shell. A line holding only ``<!-- include NAME.html -->``
is replaced by the text of ``fragments/NAME.html``, which may include fragments in
turn. Each fragment keeps its own indentation and final newline, so the assembled
page is byte for byte what a single file holding the same markup would be.

``tests/js/page_scripts.cjs`` assembles the page the same way for the browser
harnesses; ``tests/test_web_page.py`` checks that the two agree.
"""

from __future__ import annotations

import re
from pathlib import Path

_INCLUDE = re.compile(r"^[ \t]*<!-- include ([\w/-]+\.html) -->\n", re.MULTILINE)


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
