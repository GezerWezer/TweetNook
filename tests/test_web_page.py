from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tweetnook.web import server
from tweetnook.web.page import assemble_page

ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "tweetnook" / "web"


def _web_root(tmp_path: Path, shell: str, fragments: dict[str, str]) -> Path:
    web_root = tmp_path / "web"
    (web_root / "fragments").mkdir(parents=True)
    (web_root / "index.html").write_text(shell, encoding="utf-8")
    for name, text in fragments.items():
        path = web_root / "fragments" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return web_root


def test_include_lines_are_replaced_by_their_fragments_verbatim(tmp_path: Path) -> None:
    web_root = _web_root(
        tmp_path,
        "<body>\n    <!-- include dialog.html -->\n</body>",
        {
            "dialog.html": "    <div>\n        <!-- include dialog/page.html -->\n    </div>\n",
            "dialog/page.html": "        <p x-text='a'></p>\n",
        },
    )

    page, sources = assemble_page(web_root)

    assert page == "<body>\n    <div>\n        <p x-text='a'></p>\n    </div>\n</body>"
    assert [path.relative_to(web_root.resolve()) for path, _ in sources[1:]] == [
        Path("fragments/dialog.html"),
        Path("fragments/dialog/page.html"),
    ]


def test_an_include_must_be_alone_on_its_line(tmp_path: Path) -> None:
    web_root = _web_root(tmp_path, "<p><!-- include a.html --></p>\n", {"a.html": "x\n"})

    assert assemble_page(web_root)[0] == "<p><!-- include a.html --></p>\n"


@pytest.mark.parametrize(
    ("fragments", "message"),
    [
        ({"a.html": "<!-- include a.html -->\n"}, "a.html includes itself"),
        ({"a.html": "no newline"}, "a.html must end with a newline"),
    ],
)
def test_malformed_fragments_are_refused(
    tmp_path: Path, fragments: dict[str, str], message: str
) -> None:
    web_root = _web_root(tmp_path, "<!-- include a.html -->\n", fragments)

    with pytest.raises(ValueError, match=message):
        assemble_page(web_root)


def test_includes_cannot_leave_the_fragments_directory(tmp_path: Path) -> None:
    web_root = _web_root(tmp_path, f"<!-- include {tmp_path}/secret.html -->\n", {})
    (tmp_path / "secret.html").write_text("secret\n", encoding="utf-8")

    with pytest.raises(ValueError, match="outside"):
        assemble_page(web_root)


def test_served_page_follows_fragment_edits(monkeypatch, tmp_path: Path) -> None:
    web_root = _web_root(tmp_path, "<!-- include a.html -->\n", {"a.html": "<p>one</p>\n"})
    (web_root / "static").mkdir()
    monkeypatch.setattr(server, "WEB_ROOT", web_root)
    monkeypatch.setattr(server, "static_dir", web_root / "static")
    monkeypatch.setattr(server, "_index_cache", None)

    first, first_etag, _ = server._versioned_index()
    fragment = web_root / "fragments" / "a.html"
    fragment.write_text("<p>two</p>\n", encoding="utf-8")
    os.utime(fragment, ns=(1, 1))
    second, second_etag, _ = server._versioned_index()

    assert (first, second) == (b"<p>one</p>\n", b"<p>two</p>\n")
    assert first_etag != second_etag


def test_browser_harnesses_assemble_the_same_page() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to run the harnesses' page assembler")
    script = (
        "const { pageHtml } = require('./tests/js/page_scripts.cjs');"
        "process.stdout.write(JSON.stringify(pageHtml()));"
    )
    result = subprocess.run(
        [node, "-e", script], cwd=ROOT, check=True, capture_output=True, text=True, timeout=30
    )

    assert json.loads(result.stdout) == assemble_page(WEB_ROOT)[0]
