from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tweetnook.web.page import assemble_page

ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "tweetnook" / "web"
NODE_TESTS = sorted((ROOT / "tests" / "js").glob("test_web_*.cjs"))


@pytest.mark.parametrize("node_test", NODE_TESTS, ids=lambda path: path.stem)
def test_browser_javascript_unit_suite(node_test: Path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the deterministic browser-asset unit harness")

    result = subprocess.run(
        [node, str(node_test)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "browser asset tests passed" in result.stdout


def test_index_loads_local_assets_in_dependency_order():
    html = assemble_page(WEB_DIR)[0]
    themes = html.index('<script src="/static/js/themes.js"></script>')
    autocomplete = html.index('<script src="/static/js/autocomplete.js"></script>')
    completion = html.index('<script src="/static/js/setup-completion.js"></script>')
    app = html.index('<script src="/static/js/app.js"></script>')

    assert themes < autocomplete < completion < app
    assert '<link rel="stylesheet" href="/static/css/styles.css">' in html
    assert 'x-data="tweetApp()"' in html
    assert 'x-data="searchAutocomplete()"' in html
    assert '<meta name="theme-color" content="#000000">' in html


@pytest.mark.parametrize(
    "relative_path",
    [
        "static/js/themes.js",
        "static/js/autocomplete.js",
        "static/js/app.js",
        "static/js/setup-completion.js",
        "static/css/styles.css",
    ],
)
def test_index_referenced_assets_are_packaged(relative_path: str):
    asset = WEB_DIR / relative_path
    assert asset.is_file()
    assert asset.stat().st_size > 0
