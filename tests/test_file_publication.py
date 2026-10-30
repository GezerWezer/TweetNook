import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from tweetnook.importer.archive_media import _copy_file_from_archive
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
