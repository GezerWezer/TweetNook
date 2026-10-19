from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_media_playback_browser_suite():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the browser media playback harness")
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [node, str(root / "tests/js/test_media_playback.cjs")],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
