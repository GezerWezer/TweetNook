from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_core_startup_and_manual_tag_modules_do_not_import_optional_dependencies() -> None:
    root = Path(__file__).parents[1]
    code = f"""
import builtins, sys
sys.path.insert(0, {str(root)!r})
original = builtins.__import__
def reject(name, *args, **kwargs):
    if name == 'google' or name.startswith('google.') or name == 'PIL' or name.startswith('PIL.'):
        raise AssertionError('optional dependency imported: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = reject
import tweetnook.cli
import tweetnook.sync
import tweetnook.web.server
import tweetnook.web.routes.tags
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
