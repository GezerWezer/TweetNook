import io
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tweetnook.core.fileio import atomic_write_text
from tweetnook.importer.archive_media import _copy_file_from_archive
from tweetnook.jobs.activity_history import _atomic_json
from tweetnook.query_ids.store import QueryIdStore
from tweetnook.tagging.accounting import AIUsageLedger


@pytest.mark.parametrize("writer", ["query_ids", "import", "ledger"])
def test_replacement_failure_preserves_destination_and_cleans_temporary(
    paths, tmp_path, monkeypatch, writer
):
    if writer == "query_ids":
        store = QueryIdStore(paths)
        path = store.path
        store.save({})  # The save lock is a lasting sidecar, not a temporary.

        def publish():
            return store.save({"TweetDetail": "new"})
    elif writer == "ledger":
        ledger = AIUsageLedger(tmp_path)
        path = ledger.summary_path

        def publish():
            return ledger._atomic_summary({"new": True})
    else:
        path = tmp_path / "media" / "asset.jpg"
        source = SimpleNamespace(open_binary=lambda _: io.BytesIO(b"new asset"))

        def publish():
            return _copy_file_from_archive(source, "asset.jpg", path)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("previous")
    before = set(path.parent.iterdir())

    def fail_replace(temporary, destination):
        assert temporary.read_bytes()
        raise OSError("publication refused")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="publication refused"):
        publish()
    assert path.read_text() == "previous"
    assert set(path.parent.iterdir()) == before


def test_import_interrupt_after_actual_write_cleans_temporary(tmp_path):
    class InterruptedInput(io.BytesIO):
        def read(self, size):
            if self.tell():
                raise KeyboardInterrupt
            return super().read(size)

    source = SimpleNamespace(open_binary=lambda _: InterruptedInput(b"partial"))
    destination = tmp_path / "asset.jpg"
    with pytest.raises(KeyboardInterrupt):
        _copy_file_from_archive(source, "asset.jpg", destination)
    assert list(tmp_path.iterdir()) == []


def test_rebuildable_high_frequency_files_skip_the_flush(paths, tmp_path, monkeypatch):
    flushed = []
    monkeypatch.setattr(os, "fsync", lambda fd: flushed.append(fd))
    source = SimpleNamespace(open_binary=lambda _: io.BytesIO(b"asset"))
    _copy_file_from_archive(source, "asset.jpg", tmp_path / "asset.jpg")
    _atomic_json(tmp_path / "snapshot.json", {"running": True})
    QueryIdStore(paths).save({"TweetDetail": "new"})
    assert flushed == []


def test_durable_publication_still_flushes(tmp_path, monkeypatch):
    flushed = []
    monkeypatch.setattr(os, "fsync", lambda fd: flushed.append(fd))
    atomic_write_text(tmp_path / "config.toml", "kept")
    assert len(flushed) == 1
