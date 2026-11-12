from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tweetnook.web import server
from tweetnook.web.page import assemble_page, bundle_assets

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


def _bundle_root(tmp_path: Path) -> Path:
    web_root = tmp_path / "web"
    (web_root / "static" / "js").mkdir(parents=True)
    (web_root / "static" / "css").mkdir()
    (web_root / "index.html").write_text(
        "<head>\n"
        "    <!-- bundle -->\n"
        '    <link rel="stylesheet" href="/static/css/a.css">\n'
        '    <script defer src="/static/vendor/alpine.min.js"></script>\n'
        '    <script src="/static/js/one.js"></script>\n'
        '    <script src="/static/js/two.js"></script>\n'
        "    <!-- /bundle -->\n"
        "</head>\n"
    )
    for name, text in (("js/one.js", "1"), ("js/two.js", "2"), ("css/a.css", "a{}")):
        (web_root / "static" / name).write_text(text)

    def digest(*files: tuple[str, str]) -> str:
        hash_ = hashlib.sha256()
        for name, text in files:
            hash_.update(f"{name}\0{text}\0".encode())
        return hash_.hexdigest()

    manifest = {
        "js": digest(("js/one.js", "1"), ("js/two.js", "2")),
        "css": digest(("css/a.css", "a{}")),
    }
    (web_root / "static" / "bundle.json").write_text(json.dumps(manifest))
    return web_root


def test_current_bundle_replaces_the_listed_assets_and_keeps_deferred_scripts(
    tmp_path: Path,
) -> None:
    web_root = _bundle_root(tmp_path)
    page, sources = assemble_page(web_root)

    served = bundle_assets(page, web_root, sources)

    assert served == (
        "<head>\n"
        '    <link rel="stylesheet" href="/static/css/bundle.min.css">\n'
        '    <script defer src="/static/vendor/alpine.min.js"></script>\n'
        '    <script src="/static/js/bundle.min.js"></script>\n'
        "</head>\n"
    )
    assert {path.name for path, _ in sources} >= {"bundle.json", "one.js", "two.js", "a.css"}


@pytest.mark.parametrize("change", ["edit_source", "drop_manifest", "corrupt_manifest"])
def test_stale_or_missing_bundle_serves_the_listed_assets(tmp_path: Path, change: str) -> None:
    web_root = _bundle_root(tmp_path)
    if change == "edit_source":
        (web_root / "static" / "js" / "two.js").write_text("2;")
    elif change == "drop_manifest":
        (web_root / "static" / "bundle.json").unlink()
    else:
        (web_root / "static" / "bundle.json").write_text("{")
    page, sources = assemble_page(web_root)

    served = bundle_assets(page, web_root, sources)

    assert "<!-- bundle -->" not in served and "bundle.min" not in served
    assert served.count("<script") == 3 and '/static/css/a.css"' in served
