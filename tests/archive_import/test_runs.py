from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import tweetnook.importer.archive as archive_import
import tweetnook.importer.archive_enrich as archive_enrich
import tweetnook.importer.archive_manifests as archive_manifests
import tweetnook.importer.archive_media as archive_media
from tests.archive_import.conftest import (
    _auth_bundle,
    _console,
    _disable_live_reconciliation,
    _live_tweet,
    _write_archive_dir,
)
from tweetnook.config import AppConfig
from tweetnook.core.exceptions import (
    APIResponseError,
    StaleQueryIdError,
)
from tweetnook.importer.archive import import_x_archive
from tweetnook.jobs.pipeline import PipelineReporter
from tweetnook.storage import open_archive_store


def test_import_x_archive_detail_api_errors_become_transient_failures(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)

    async def fake_reconciliation(**_kwargs):
        return [], [], _auth_bundle()

    async def fake_resolve_query_ids(*_args, **_kwargs):
        return {"TweetDetail": "detail-query-id"}

    async def fake_fetch_page(*_args, **_kwargs):
        raise APIResponseError("server error", status_code=500)

    class DummyClient:
        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(archive_enrich, "_run_live_reconciliation", fake_reconciliation)
    monkeypatch.setattr(archive_enrich, "resolve_query_ids", fake_resolve_query_ids)
    monkeypatch.setattr(
        archive_enrich, "build_async_client", lambda *_args, **_kwargs: DummyClient()
    )
    monkeypatch.setattr(archive_enrich, "fetch_page", fake_fetch_page)

    result = asyncio.run(
        import_x_archive(
            archive_dir,
            detail_lookups=1,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    assert result.detail_lookups == 0
    assert result.detail_transient_failures == 1
    assert result.pending_enrichment == 1

    store = open_archive_store(paths, create=False)
    assert store is not None
    tweet_object = store._query(expr="row_key = 'tweet_object:300'", limit=1)[0]
    assert tweet_object["enrichment_state"] == "transient_failure"
    assert tweet_object["enrichment_http_status"] == "500"
    manifest_row = store._query(expr="record_type = 'import_manifest'", limit=1)[0]
    manifest_counts = json.loads(manifest_row["counts_json"])
    assert manifest_counts["detail_transient_failures"] == 1
    store.close()


def test_import_x_archive_detail_stale_query_id_aborts_and_leaves_row_untouched(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)

    async def fake_reconciliation(**_kwargs):
        return [], [], _auth_bundle()

    async def fake_resolve_query_ids(*_args, **_kwargs):
        return {"TweetDetail": "detail-query-id"}

    async def fake_fetch_page(*_args, **_kwargs):
        raise StaleQueryIdError("stale query id", status_code=404)

    class DummyClient:
        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(archive_enrich, "_run_live_reconciliation", fake_reconciliation)
    monkeypatch.setattr(archive_enrich, "resolve_query_ids", fake_resolve_query_ids)
    monkeypatch.setattr(
        archive_enrich, "build_async_client", lambda *_args, **_kwargs: DummyClient()
    )
    monkeypatch.setattr(archive_enrich, "fetch_page", fake_fetch_page)

    with pytest.raises(archive_enrich.ArchiveEnrichmentAborted, match="stale query id") as exc:
        asyncio.run(
            import_x_archive(
                archive_dir,
                detail_lookups=1,
                config=AppConfig(),
                paths=paths,
                console=_console(),
            )
        )
    assert isinstance(exc.value.cause, StaleQueryIdError)

    store = open_archive_store(paths, create=False)
    assert store is not None
    tweet_object = store._query(expr="row_key = 'tweet_object:300'", limit=1)[0]
    assert tweet_object["enrichment_state"] == "pending"
    assert tweet_object["enrichment_http_status"] is None
    manifest_row = store._query(expr="record_type = 'import_manifest'", limit=1)[0]
    assert manifest_row["status"] == "completed"
    assert manifest_row["enrichment_followup_status"] == "enrichment_aborted"
    assert "stale query id" in manifest_row["enrichment_aborted_reason"]
    store.close()


def test_import_x_archive_finalizes_manifest_when_followup_is_interrupted(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)

    async def interrupt_followup(**_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(archive_enrich, "_run_archive_followup", interrupt_followup)

    with pytest.raises(archive_enrich.ArchiveEnrichmentInterrupted):
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
    manifest = store._query(expr="record_type = 'import_manifest'", limit=1)[0]
    counts = json.loads(manifest["counts_json"])
    assert manifest["status"] == "completed"
    assert manifest["enrichment_followup_status"] == "enrichment_interrupted"
    assert manifest["enrichment_aborted_reason"] == "KeyboardInterrupt"
    assert counts["pending_enrichment"] == store.count_incomplete_initial_enrichment()
    assert "transient_due" in counts
    assert "transient_delayed" in counts
    store.close()


def test_import_x_archive_preserves_attempt_start_time(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    _disable_live_reconciliation(monkeypatch)
    timestamps = iter(
        [
            "2026-03-17T00:00:00Z",
            "2026-03-17T00:00:01Z",
            "2026-03-17T00:00:02Z",
            "2026-03-17T00:00:03Z",
        ]
    )
    # The import, its media copy and its manifest updates read the clock in turn.
    for module in (archive_import, archive_media, archive_manifests):
        monkeypatch.setattr(module, "utc_now", lambda: next(timestamps))

    asyncio.run(
        import_x_archive(
            archive_dir,
            # Without FxTwitter or an account, enrichment is skipped and takes no timestamps.
            config=AppConfig(fxtwitter={"enabled": False}),
            paths=paths,
            console=_console(),
        )
    )

    store = open_archive_store(paths, create=False)
    assert store is not None
    manifest_row = store._query(expr="record_type = 'import_manifest'", limit=1)[0]
    assert manifest_row["import_started_at"] == "2026-03-17T00:00:00Z"
    assert manifest_row["import_completed_at"] == "2026-03-17T00:00:03Z"
    store.close()


def test_import_x_archive_sample_limit_does_not_require_debug(paths, tmp_path: Path) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    console = _console()
    reporter = PipelineReporter(console, "archive import", interactive=False)

    with reporter:
        result = asyncio.run(
            import_x_archive(
                archive_dir,
                sample_limit=1,
                config=AppConfig(),
                paths=paths,
                console=console,
            )
        )

    assert result.skipped is False
    assert result.followup_performed is False
    assert any("sampled import" in warning for warning in result.warnings)
    assert {step.key for step in reporter.steps} == {
        "archive-inspect",
        "archive-source",
        "archive-tweets",
        "archive-deleted",
        "archive-likes",
        "archive-media",
    }
    assert not reporter.has_step("archive-enrich")


def test_sampled_debug_import_stays_non_completed_and_full_import_can_rerun(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    _disable_live_reconciliation(monkeypatch)

    sampled = asyncio.run(
        import_x_archive(
            archive_dir,
            sample_limit=1,
            debug=True,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    assert sampled.skipped is False
    assert sampled.followup_performed is False
    assert any("sampled import" in warning for warning in sampled.warnings)

    store = open_archive_store(paths, create=False)
    assert store is not None
    manifest_row = store._query(expr="record_type = 'import_manifest'", limit=1)[0]
    assert manifest_row["status"] == "sampled"
    store.close()

    full = asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    assert full.skipped is False
    store = open_archive_store(paths, create=False)
    assert store is not None
    manifest_row = store._query(expr="record_type = 'import_manifest'", limit=1)[0]
    assert manifest_row["status"] == "completed"
    store.close()


def test_interrupted_import_marks_manifest_failed_and_rerun_reuses_archive_captures(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    _disable_live_reconciliation(monkeypatch)
    original_import_authored_tweets = archive_import.import_authored_tweets
    optimize_calls = {"count": 0}

    def abort_import(*_args, **_kwargs) -> None:
        raise KeyboardInterrupt()

    def fake_optimize(self) -> None:
        optimize_calls["count"] += 1

    monkeypatch.setattr(archive_import, "import_authored_tweets", abort_import)
    monkeypatch.setattr(archive_import.ArchiveStore, "optimize", fake_optimize)

    with pytest.raises(KeyboardInterrupt):
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
    manifest_row = store._query(expr="record_type = 'import_manifest'", limit=1)[0]
    assert manifest_row["status"] == "failed"
    raw_capture_count = store.counts()["raw_captures"]
    store.close()

    assert optimize_calls["count"] == 0

    monkeypatch.setattr(archive_import, "import_authored_tweets", original_import_authored_tweets)

    rerun = asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    assert rerun.skipped is False
    store = open_archive_store(paths, create=False)
    assert store is not None
    manifest_row = store._query(expr="record_type = 'import_manifest'", limit=1)[0]
    assert manifest_row["status"] == "completed"
    assert store.counts()["raw_captures"] == raw_capture_count
    store.close()


def test_import_x_archive_regen_clears_archive_rows_but_keeps_live_rows(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    _disable_live_reconciliation(monkeypatch)

    store = open_archive_store(paths, create=True)
    assert store is not None
    live_tweet = _live_tweet("900", text="existing live bookmark")
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[live_tweet],
        last_head_tweet_id="900",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.close()

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
    first_raw_capture_count = store.counts()["raw_captures"]
    media_row = store._query(expr="record_type = 'media'", limit=1)[0]
    original_media_path = paths.data_dir / str(media_row["local_path"])
    stale_media_path = paths.data_dir / "media" / "100" / "stale.jpg"
    stale_media_path.parent.mkdir(parents=True, exist_ok=True)
    original_media_path.rename(stale_media_path)
    updated_media_row = dict(media_row)
    updated_media_row["local_path"] = "media/100/stale.jpg"
    store.merge_rows([updated_media_row])
    store.close()

    rerun = asyncio.run(
        import_x_archive(
            archive_dir,
            regen=True,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    assert rerun.skipped is False
    assert not stale_media_path.exists()
    assert (paths.data_dir / "media" / "100" / "3_500.jpg").exists()

    store = open_archive_store(paths, create=False)
    assert store is not None
    assert store.counts()["raw_captures"] == first_raw_capture_count
    live_row = store._query(expr="row_key = 'tweet:bookmark::900'", limit=1)[0]
    assert live_row["source"] == "live_graphql"
    assert store.counts()["import_manifests"] == 1
    store.close()


def test_remove_archive_owned_files_only_removes_media_subtree(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    media_path = data_dir / "media" / "100" / "asset.jpg"
    notes_path = data_dir / "notes" / "asset.jpg"
    media_path.parent.mkdir(parents=True, exist_ok=True)
    notes_path.parent.mkdir(parents=True, exist_ok=True)
    media_path.write_bytes(b"media")
    notes_path.write_bytes(b"notes")

    removed = archive_manifests.remove_archive_owned_files(
        data_dir,
        ["media/100/asset.jpg", "notes/asset.jpg", "../escape.jpg", "/tmp/escape.jpg"],
    )

    assert removed == 1
    assert not media_path.exists()
    assert notes_path.exists()
