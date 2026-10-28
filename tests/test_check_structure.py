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
    tree("tweetnook/core/utils.py", "import tweetnook.sync\nfrom tweetnook import config\n")
    tree(
        "tweetnook/storage/backend.py",
        "from tweetnook import config, sync\nfrom ..search import x\nfrom . import migrate\n",
    )
    tree("tweetnook/sync/collection.py", "from tweetnook.storage.backend import ArchiveStore\n")
    tree(
        "tweetnook/web/server.py",
        "from tweetnook.sync.collection import run\nfrom tweetnook.cli import app\n",
    )

    assert check_structure.upward_imports() == [
        ("tweetnook.core.utils", "tweetnook.sync"),
        ("tweetnook.storage.backend", "tweetnook.search"),
        ("tweetnook.storage.backend", "tweetnook.sync"),
    ]


def test_strict_fails_only_when_something_is_reported(tree, capsys) -> None:
    tree("tweetnook/sync/collection.py", "from tweetnook.storage import backend\n")
    assert check_structure.main(["--strict"]) == 0

    tree("tweetnook/storage/backend.py", "from tweetnook.web import server\n")
    assert check_structure.main([]) == 0
    assert check_structure.main(["--strict"]) == 1
    assert (
        "tweetnook.storage.backend (storage) -> tweetnook.web (interface)"
        in capsys.readouterr().out
    )


def test_strict_fails_on_files_over_the_limit(tree) -> None:
    tree("tweetnook/large.py", "x = 1\n" * 1200)
    assert check_structure.main(["--strict"]) == 0

    tree("tweetnook/large.py", "x = 1\n" * 1201)
    assert check_structure.main([]) == 0
    assert check_structure.main(["--strict"]) == 1


def test_exempt_file_fails_only_past_its_ceiling(tree, monkeypatch, capsys) -> None:
    monkeypatch.setattr(check_structure, "SIZE_EXEMPT", {"tweetnook/large.py": 1500})
    tree("tweetnook/large.py", "x = 1\n" * 1500)
    assert check_structure.main(["--strict"]) == 0
    assert "exempt   1500  tweetnook/large.py" in capsys.readouterr().out

    tree("tweetnook/large.py", "x = 1\n" * 1501)
    assert check_structure.main(["--strict"]) == 1
    assert "LIMIT    1501  tweetnook/large.py" in capsys.readouterr().out


def test_reports_private_names_imported_from_another_module(tree) -> None:
    tree("tweetnook/storage/common.py", "def _quote(x): ...\ndef shared(x): ...\n")
    tree(
        "tweetnook/storage/reads.py",
        "from .common import _quote, shared\nfrom tweetnook.storage.common import __all__\n",
    )
    tree(
        "tweetnook/web/server.py",
        "from tweetnook.storage.common import _quote\nfrom os import _exit\n",
    )

    assert check_structure.private_imports() == [
        ("tweetnook.storage.reads", "tweetnook.storage.common._quote"),
        ("tweetnook.web.server", "tweetnook.storage.common._quote"),
    ]


def test_private_imports_never_fail_strict(tree) -> None:
    tree("tweetnook/storage/common.py", "def _quote(x): ...\n")
    tree("tweetnook/storage/reads.py", "from .common import _quote\n")
    assert check_structure.main(["--strict"]) == 0
