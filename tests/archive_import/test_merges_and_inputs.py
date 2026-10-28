from __future__ import annotations

import asyncio
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import tweetnook.importer.archive_enrich as archive_enrich
import tweetnook.importer.archive_input as archive_input
from tests.archive_import.conftest import (
    _auth_bundle,
    _console,
    _disable_live_reconciliation,
    _live_tweet,
    _write_archive_dir,
)
from tweetnook.config import AppConfig
from tweetnook.core.exceptions import (
    ArchiveOwnerMismatchError,
    ConfigError,
)
from tweetnook.importer.archive import import_x_archive
from tweetnook.storage import open_archive_store


def test_archive_live_reconciliation_skips_resuming_saved_backfills(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[dict[str, object]] = []

    async def fake_sync_collection(*args, **kwargs):
        captured.append({"collection": args[0], **kwargs})
        return SimpleNamespace(pages_fetched=1, tweets_seen=1, stop_reason="duplicate")

    monkeypatch.setattr(archive_enrich, "sync_collection", fake_sync_collection)

    reconciled, warnings, resolved_auth = asyncio.run(
        archive_enrich._run_live_reconciliation(
            collections=["likes", "tweets"],
            config=AppConfig(),
            paths=paths,
            auth_bundle=_auth_bundle(),
            transport=None,
            console=_console(),
        )
    )

    assert reconciled == ["likes", "tweets"]
    assert warnings == []
    assert resolved_auth is not None
    assert [kwargs["collection"] for kwargs in captured] == ["likes", "tweets"]
    assert all(kwargs["resume_backfill"] is False for kwargs in captured)


def test_archive_import_does_not_downgrade_existing_live_tweet_object(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path, like_tweet_id="300")
    store = open_archive_store(paths, create=True)
    assert store is not None
    live_tweet = _live_tweet("300", text="live bookmark tweet")
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[live_tweet],
        last_head_tweet_id="300",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.close()
    _disable_live_reconciliation(monkeypatch)

    asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    store = open_archive_store(paths, create=False)
    assert store is not None
    tweet_object = store._query(expr="row_key = 'tweet_object:300'", limit=1)[0]
    assert tweet_object["source"] == "live_graphql"
    assert tweet_object["text"] == "live bookmark tweet"
    assert tweet_object["enrichment_state"] == "done"
    like_row = store._query(expr="row_key = 'tweet:like::300'", limit=1)[0]
    assert like_row["source"] == "x_archive"
    store.close()


def test_live_sync_can_upgrade_archive_like_placeholder_after_import(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path, like_tweet_id="300")
    _disable_live_reconciliation(monkeypatch)

    asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    store = open_archive_store(paths, create=False)
    assert store is not None
    store.persist_page(
        operation="Likes",
        collection_type="like",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[_live_tweet("300", text="live liked tweet")],
        last_head_tweet_id="300",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    tweet_object = store._query(expr="row_key = 'tweet_object:300'", limit=1)[0]
    assert tweet_object["source"] == "live_graphql"
    assert tweet_object["text"] == "live liked tweet"
    assert tweet_object["enrichment_state"] == "done"
    store.close()


def test_import_x_archive_rejects_missing_manifest(paths, tmp_path: Path) -> None:
    broken_dir = tmp_path / "broken-archive"
    broken_dir.mkdir()

    with pytest.raises(ConfigError, match="missing manifest.js"):
        asyncio.run(
            import_x_archive(
                broken_dir,
                config=AppConfig(),
                paths=paths,
                console=_console(),
            )
        )


def test_archive_input_closes_zip_when_manifest_load_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_zip = tmp_path / "broken-archive.zip"
    with zipfile.ZipFile(archive_zip, "w") as handle:
        handle.writestr("data/manifest.js", "window.__THAR_CONFIG = {}\n")

    closed: list[str] = []
    original_close = zipfile.ZipFile.close

    def tracking_close(self: zipfile.ZipFile) -> None:
        if self.fp is not None:
            closed.append(str(self.filename))
        original_close(self)

    def raise_bad_manifest(_self: archive_input.ArchiveInput) -> dict[str, object]:
        raise ConfigError("bad manifest")

    monkeypatch.setattr(zipfile.ZipFile, "close", tracking_close)
    monkeypatch.setattr(archive_input.ArchiveInput, "_load_manifest", raise_bad_manifest)

    with pytest.raises(ConfigError, match="bad manifest"):
        archive_input.ArchiveInput(archive_zip)

    assert closed == [str(archive_zip)]


def test_import_x_archive_rejects_owner_mismatch(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    store = open_archive_store(paths, create=True)
    assert store is not None
    store.ensure_archive_owner_id("84")
    store.close()
    _disable_live_reconciliation(monkeypatch)

    with pytest.raises(ArchiveOwnerMismatchError):
        asyncio.run(
            import_x_archive(
                archive_dir,
                config=AppConfig(),
                paths=paths,
                console=_console(),
            )
        )


def test_import_x_archive_rejects_parent_segments_in_manifest_paths(paths, tmp_path: Path) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    manifest_path = archive_dir / "data" / "manifest.js"
    manifest = json.loads(
        manifest_path.read_text(encoding="utf-8").removeprefix("window.__THAR_CONFIG = ")
    )
    manifest["dataTypes"]["tweets"]["files"][0]["fileName"] = "data/../../../etc/passwd"
    manifest_path.write_text(
        f"window.__THAR_CONFIG = {json.dumps(manifest, indent=2)}\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="must stay within the archive data/ directory"):
        asyncio.run(
            import_x_archive(
                archive_dir,
                config=AppConfig(),
                paths=paths,
                console=_console(),
            )
        )


def test_import_x_archive_parse_errors_include_filename(paths, tmp_path: Path) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    (archive_dir / "data" / "like.js").write_text(
        "window.YTD.like.part0 = not-json\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=r"like\.js"):
        asyncio.run(
            import_x_archive(
                archive_dir,
                config=AppConfig(),
                paths=paths,
                console=_console(),
            )
        )


def test_import_x_archive_reuses_existing_thumbnail_destination(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path, media_kind="video")
    (paths.data_dir / "media" / "100").mkdir(parents=True, exist_ok=True)
    (paths.data_dir / "media" / "100" / "7_500-poster.jpg").write_bytes(b"poster")
    _disable_live_reconciliation(monkeypatch)

    result = asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    assert result.counts["media_files_copied"] == 0
    store = open_archive_store(paths, create=False)
    assert store is not None
    media_row = store._query(expr="record_type = 'media'", limit=1)[0]
    assert media_row["download_state"] == "pending"
    assert media_row["local_path"] is None
    assert media_row["thumbnail_local_path"] == "media/100/7_500-poster.jpg"
    assert media_row["thumbnail_sha256"] is None
    assert media_row["thumbnail_byte_size"] is None
    assert store.list_media_rows(states={"pending"})[0]["row_key"] == media_row["row_key"]
    store.close()


def test_import_x_archive_preserves_main_and_thumbnail_updates_for_video_media(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(
        tmp_path,
        media_kind="video",
        include_video_main_asset=True,
    )
    _disable_live_reconciliation(monkeypatch)

    result = asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    assert result.counts["media_files_copied"] == 2
    store = open_archive_store(paths, create=False)
    assert store is not None
    media_row = store._query(expr="record_type = 'media'", limit=1)[0]
    assert media_row["local_path"] == "media/100/7_500.mp4"
    assert media_row["thumbnail_local_path"] == "media/100/7_500-poster.jpg"
    assert media_row["download_state"] == "done"
    assert (paths.data_dir / "media" / "100" / "7_500.mp4").exists()
    assert (paths.data_dir / "media" / "100" / "7_500-poster.jpg").exists()
    store.close()


def test_archive_deleted_tweet_preserves_existing_live_fields(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    store = open_archive_store(paths, create=True)
    assert store is not None
    live_tweet = _live_tweet("200", text="live deleted tweet")
    store.persist_page(
        operation="UserTweets",
        collection_type="tweet",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[live_tweet],
        last_head_tweet_id="200",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.close()
    _disable_live_reconciliation(monkeypatch)

    asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    store = open_archive_store(paths, create=False)
    assert store is not None
    tweet_row = store._query(expr="row_key = 'tweet:tweet::200'", limit=1)[0]
    assert tweet_row["source"] == "live_graphql"
    assert tweet_row["text"] == "live deleted tweet"
    assert tweet_row["deleted_at"] == "Mon Mar 16 00:00:00 +0000 2026"
    tweet_object = store._query(expr="row_key = 'tweet_object:200'", limit=1)[0]
    assert tweet_object["source"] == "live_graphql"
    assert tweet_object["text"] == "live deleted tweet"
    assert tweet_object["deleted_at"] == "Mon Mar 16 00:00:00 +0000 2026"
    store.close()
