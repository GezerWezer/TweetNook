"""Generate the in-app cookie instructions from docs/getting-started.md.

The Setup modal shows these steps without a second hand-written copy. Run this
after editing the guide; tests/test_setup_help.py fails while the two differ.

    uv run python scripts/build_setup_help.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "getting-started.md"
OUTPUT = ROOT / "tweetnook" / "web" / "static" / "setup-help.json"

_SUMMARY = re.compile(r"<summary><strong>(.+?)</strong></summary>")
_STEP = re.compile(r"^###\s+\d+\.\s+(.+)$")
_INLINE = re.compile(r"\*\*(.+?)\*\*|`([^`]+)`")


def _inline(text: str) -> list[dict[str, str]]:
    """Split markdown text into plain, strong and code segments."""
    segments: list[dict[str, str]] = []
    position = 0
    for match in _INLINE.finditer(text):
        if match.start() > position:
            segments.append({"t": "text", "v": text[position : match.start()]})
        if match.group(1) is not None:
            segments.append({"t": "strong", "v": match.group(1)})
        else:
            segments.append({"t": "code", "v": match.group(2)})
        position = match.end()
    if position < len(text):
        segments.append({"t": "text", "v": text[position:]})
    return segments


def _blocks(lines: list[str]) -> list[dict[str, object]]:
    """Group lines into paragraphs and fenced code blocks."""
    blocks: list[dict[str, object]] = []
    paragraph: list[str] = []
    code: list[str] | None = None

    def flush() -> None:
        if paragraph:
            blocks.append({"type": "p", "inline": _inline(" ".join(paragraph))})
            paragraph.clear()

    for raw in lines:
        line = raw.rstrip()
        if line.startswith("```"):
            if code is None:
                flush()
                code = []
            else:
                blocks.append({"type": "code", "text": "\n".join(code)})
                code = None
        elif code is not None:
            code.append(line)
        elif line.strip():
            paragraph.append(line.strip())
        else:
            flush()
    if code is not None:
        raise ValueError("Unclosed code fence in the cookie guide.")
    flush()
    return blocks


def build(markdown: str) -> dict[str, object]:
    caution = re.search(r"> \[!CAUTION\]\n> (.+)", markdown)
    if not caution:
        raise ValueError("The cookie guide has no CAUTION callout.")
    browsers = []
    for section in re.findall(r"<details>(.*?)</details>", markdown, re.S):
        summary = _SUMMARY.search(section)
        if not summary:
            raise ValueError("A browser section has no <summary><strong> title.")
        body = section[summary.end() :].splitlines()
        intro: list[str] = []
        steps: list[dict[str, object]] = []
        current: list[str] = intro
        for line in body:
            step = _STEP.match(line)
            if step:
                current = []
                steps.append({"title": _inline(step.group(1)), "lines": current})
            else:
                current.append(line)
        if not steps:
            raise ValueError(f"The {summary.group(1)} section has no numbered steps.")
        browsers.append(
            {
                "name": summary.group(1),
                "intro": _blocks(intro),
                "steps": [
                    {"title": step["title"], "blocks": _blocks(step["lines"])}  # type: ignore[arg-type]
                    for step in steps
                ],
            }
        )
    if not browsers:
        raise ValueError("The cookie guide has no browser sections.")
    return {
        "source": SOURCE.relative_to(ROOT).as_posix(),
        "caution": _inline(caution.group(1)),
        "browsers": browsers,
    }


def render(markdown: str) -> str:
    return json.dumps(build(markdown), ensure_ascii=False, indent=1) + "\n"


def main() -> int:
    OUTPUT.write_text(render(SOURCE.read_text(encoding="utf-8")), encoding="utf-8")
    print(f"Wrote {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
