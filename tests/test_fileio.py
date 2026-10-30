import os
from io import BytesIO, StringIO
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from rich.console import Console

from tweetnook.core.fileio import atomic_write_text, owned_temp
from tweetnook.enrichment.media import _download_file
from tweetnook.importer.archive_media import _copy_file_from_archive
from tweetnook.jobs.activity_history import _atomic_json
from tweetnook.jobs.pipeline import PipelineReporter
from tweetnook.tagging.accounting import AIUsageLedger
from tweetnook.web.routes.avatars import _cache_avatar


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


@pytest.mark.parametrize("umask, expected", [(0o022, 0o644), (0o077, 0o600), (0o002, 0o664)])
def test_follow_umask_publishes_the_mode_the_kernel_applied(tmp_path, umask, expected):
    path = tmp_path / "state.json"
    previous = os.umask(umask)
    try:
        atomic_write_text(path, "state", follow_umask=True)
    finally:
        os.umask(previous)
    assert path.stat().st_mode & 0o777 == expected
    assert list(tmp_path.iterdir()) == [path]


def test_writer_is_private_by_default_and_explicit_mode_wins(tmp_path):
    previous = os.umask(0o022)
    try:
        atomic_write_text(tmp_path / "private", "secret")
        atomic_write_text(tmp_path / "unit", "text", mode=0o644)
        atomic_write_text(tmp_path / "explicit", "secret", mode=0o600, follow_umask=True)
    finally:
        os.umask(previous)
    assert (tmp_path / "private").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "unit").stat().st_mode & 0o777 == 0o644
    assert (tmp_path / "explicit").stat().st_mode & 0o777 == 0o600


def test_status_and_state_files_follow_the_umask(tmp_path):
    previous = os.umask(0o022)
    try:
        activity = tmp_path / "activity.json"
        _atomic_json(activity, {"running": True})
        ledger = AIUsageLedger(tmp_path)
        ledger._atomic_summary({"new": True})
        reporter = PipelineReporter(
            Console(file=StringIO()), "status", interactive=False, state_path=tmp_path / "status"
        )
        with reporter:
            reporter._write_state_snapshot({"running": True})
            assert (tmp_path / "status").stat().st_mode & 0o777 == 0o644
    finally:
        os.umask(previous)
    assert activity.stat().st_mode & 0o777 == 0o644
    assert ledger.summary_path.stat().st_mode & 0o777 == 0o644


async def test_media_files_follow_the_umask(tmp_path):
    previous = os.umask(0o022)
    try:
        source = SimpleNamespace(open_binary=lambda _: BytesIO(b"asset"))
        imported = tmp_path / "imported.jpg"
        _copy_file_from_archive(source, "asset.jpg", imported)

        avatar = tmp_path / "avatar.jpg"
        _cache_avatar(avatar, b"avatar")

        transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"media"))
        async with httpx.AsyncClient(transport=transport) as client:
            downloaded, *_ = await _download_file(
                client, url="https://example.test/a.jpg", destination=tmp_path / "downloaded.jpg"
            )
    finally:
        os.umask(previous)
    for path in (imported, avatar, downloaded):
        assert path.stat().st_mode & 0o777 == 0o644
    assert not [path for path in tmp_path.iterdir() if path.suffix == ".tmp"]
