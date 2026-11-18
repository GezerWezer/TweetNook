"""Offline unit tests for Smart Search SDK, query parsing, indexer, and CLI."""

from __future__ import annotations

import array
from pathlib import Path
from types import SimpleNamespace

from rich.console import Console
from typer.testing import CliRunner

from tweetnook.cli import app
from tweetnook.config import AppConfig, XDGPaths
from tweetnook.jobs.pipeline import PipelineReporter
from tweetnook.smart_search import sdk
from tweetnook.smart_search.indexer import index_media_embeddings
from tweetnook.smart_search.query import parse_smart_query
from tweetnook.storage.backend import ArchiveStore
from tweetnook.storage.record_types import RecordType


class _FakeVector:
    def __init__(self, values: list[float]) -> None:
        self._bytes = array.array("f", values).tobytes()

    def tobytes(self) -> bytes:
        return self._bytes


class _FakeEncoder:
    spec = SimpleNamespace(dims=3)

    def encode_images(self, paths: list[Path]) -> list[_FakeVector]:
        out = []
        for path in paths:
            if path.name == "corrupt.jpg":
                raise OSError("corrupt image")
            out.append(_FakeVector([1.0, 0.0, 0.0]))
        return out


def test_parse_smart_query() -> None:
    assert parse_smart_query(None) is None
    assert parse_smart_query("sunset") is None
    assert parse_smart_query("smart:sunset over ocean") == "sunset over ocean"
    assert parse_smart_query('SMART:"red car"') == "red car"
    assert parse_smart_query("smart:") == ""


def test_smart_search_installed_flag(monkeypatch) -> None:
    monkeypatch.setattr(sdk.importlib.util, "find_spec", lambda name: None)
    assert sdk.smart_search_installed() is False

    monkeypatch.setattr(sdk.importlib.util, "find_spec", lambda name: object())
    assert sdk.smart_search_installed() is True


def test_index_media_embeddings_batches_and_skips_bad_files(tmp_path: Path) -> None:
    paths = XDGPaths(
        config_dir=tmp_path / "cfg",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
    )
    media_dir = paths.data_dir / "media" / "photos"
    media_dir.mkdir(parents=True)
    (media_dir / "good.jpg").write_bytes(b"fake-jpg")
    (media_dir / "corrupt.jpg").write_bytes(b"bad-jpg")

    config = AppConfig()
    config.smart_search.batch_size = 4

    store = ArchiveStore(paths.data_dir / "archive.db", create=True, config=config)
    try:
        with store.transaction():
            items = [
                ("1", "media/photos/good.jpg"),
                ("2", "media/photos/corrupt.jpg"),
                ("3", "media/photos/missing.jpg"),
            ]
            for tid, fname in items:
                store.conn.execute(
                    f"INSERT INTO archive (row_key, record_type, tweet_id, collection_type, text, "
                    f"created_at_ts, sort_index) VALUES "
                    f"(?, '{RecordType.TWEET}', ?, 'likes', 'hi', 100, ?)",
                    (f"t:{tid}", tid, tid),
                )
                store.conn.execute(
                    f"INSERT INTO archive (row_key, record_type, tweet_id, media_key, media_type, "
                    f"local_path, download_state, position) "
                    f"VALUES (?, '{RecordType.MEDIA}', ?, ?, 'photo', ?, 'done', 0)",
                    (f"m:{tid}", tid, f"3_{tid}", fname),
                )

        pipeline = PipelineReporter(
            console=Console(quiet=True),
            title="tweetnook smart-search index",
            interactive=False,
            state_path=tmp_path / "status.json",
        )
        result = index_media_embeddings(
            store,
            paths,
            config,
            pipeline=pipeline,
            encoder=_FakeEncoder(),  # type: ignore[arg-type]
        )
        assert result.indexed == 1
        assert result.skipped == 2
        assert result.total_pending == 3
        assert store.embedding_stats(config.smart_search.model)["indexed"] == 1
    finally:
        store.close()


def test_smart_search_cli_help_and_status(tmp_path: Path, monkeypatch) -> None:
    runner = CliRunner()
    for args in [
        ["smart-search", "--help"],
        ["smart-search", "index", "--help"],
        ["smart-search", "status", "--help"],
        ["smart-search", "search", "--help"],
    ]:
        res = runner.invoke(app, args)
        assert res.exit_code == 0, res.output

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    res = runner.invoke(app, ["smart-search", "status"])
    assert res.exit_code == 0
    assert "Model:" in res.output
