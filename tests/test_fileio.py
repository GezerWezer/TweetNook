from pathlib import Path

import pytest

from tweetnook.core.fileio import atomic_write_text, owned_temp


def test_atomic_write_permissions_and_replacement(tmp_path):
    path = tmp_path / "nested" / "config"
    atomic_write_text(path, "old", mode=0o600)
    atomic_write_text(path, "new", mode=0o600)
    assert path.read_text() == "new"
    assert path.stat().st_mode & 0o777 == 0o600
    assert list(path.parent.iterdir()) == [path]


def test_owned_temp_preserves_interrupt_when_cleanup_fails(tmp_path, monkeypatch):
    def fail_unlink(self, **kwargs):
        raise OSError("cleanup")

    with pytest.raises(KeyboardInterrupt):
        with owned_temp(tmp_path / "output") as temporary:
            temporary.write_bytes(b"partial")
            with monkeypatch.context() as patch:
                patch.setattr(Path, "unlink", fail_unlink)
                raise KeyboardInterrupt
