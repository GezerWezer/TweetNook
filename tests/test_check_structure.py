"""The size budget and import-direction report catch what they claim to."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_structure", ROOT / "scripts" / "check_structure.py"
)
assert SPEC and SPEC.loader
check_structure = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_structure)


@pytest.fixture
def tree(tmp_path, monkeypatch):
    monkeypatch.setattr(check_structure, "ROOT", tmp_path)

    def write(relative: str, text: str) -> None:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    for package in ("tweetnook", "tweetnook/storage", "tweetnook/web"):
        write(f"{package}/__init__.py", "")
    return write


def test_reports_files_over_the_budget_but_not_vendored_ones(tree) -> None:
    tree("tweetnook/small.py", "x = 1\n" * 800)
    tree("tweetnook/large.py", "x = 1\n" * 801)
    tree("tweetnook/web/static/js/app.js", "x;\n" * 1201)
    tree("tweetnook/web/static/vendor/alpine.min.js", "x;\n" * 5000)

    assert check_structure.oversized_files() == [
        (1201, "tweetnook/web/static/js/app.js"),
        (801, "tweetnook/large.py"),
    ]


def test_reports_only_imports_that_point_up_the_stack(tree) -> None:
    tree("tweetnook/utils.py", "import tweetnook.sync\n")
    tree(
        "tweetnook/storage/backend.py",
        "from tweetnook import utils, sync\nfrom ..search import x\nfrom . import migrate\n",
    )
    tree("tweetnook/sync.py", "from tweetnook.storage.backend import ArchiveStore\n")
    tree(
        "tweetnook/web/server.py", "from tweetnook.sync import run\nfrom tweetnook.cli import app\n"
    )

    assert check_structure.upward_imports() == [
        ("tweetnook.storage.backend", "tweetnook.search"),
        ("tweetnook.storage.backend", "tweetnook.sync"),
        ("tweetnook.utils", "tweetnook.sync"),
    ]


def test_strict_fails_only_when_something_is_reported(tree, capsys) -> None:
    tree("tweetnook/sync.py", "from tweetnook.storage import backend\n")
    assert check_structure.main(["--strict"]) == 0

    tree("tweetnook/storage/backend.py", "from tweetnook.web import server\n")
    assert check_structure.main([]) == 0
    assert check_structure.main(["--strict"]) == 1
    assert (
        "tweetnook.storage.backend (storage) -> tweetnook.web (interface)"
        in capsys.readouterr().out
    )
