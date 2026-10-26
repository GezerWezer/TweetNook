from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_template_names_resolve():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the template name checker")
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [node, str(root / "tests/js/test_template_names.cjs")],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
