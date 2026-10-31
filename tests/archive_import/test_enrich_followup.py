from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import tweetnook.importer.archive_enrich as archive_enrich
from tests.archive_import.conftest import (
    _auth_bundle,
    _console,
    _disable_live_reconciliation,
    _write_archive_dir,
)
from tweetnook.config import AppConfig
from tweetnook.core.exceptions import (
    ConfigError,
)
from tweetnook.importer.archive import import_x_archive
from tweetnook.importer.archive_enrich import enrich_imported_archive
from tweetnook.storage import open_archive_store


def test_repeated_import_can_reuse_existing_archive_for_enrich_followup(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    _disable_live_reconciliation(monkeypatch)

    first = asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    async def fake_reconciliation(**_kwargs):
        return ["likes"], [], _auth_bundle()

    async def fake_enrich_pending_rows(**kwargs):
        assert kwargs["limit"] is None
        return archive_enrich.ArchiveEnrichResult(
            detail_lookups=3,
            detail_terminal_unavailable=1,
            detail_transient_failures=2,
            pending_enrichment=4,
            pending_untouched=4,
        )

    monkeypatch.setattr(archive_enrich, "_run_live_reconciliation", fake_reconciliation)
    monkeypatch.setattr(archive_enrich, "_enrich_pending_rows", fake_enrich_pending_rows)

    second = asyncio.run(
        import_x_archive(
            archive_dir,
            enrich=True,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    assert first.skipped is False
    assert second.skipped is True
    assert second.followup_performed is True
    assert second.reconciled_collections == ["likes"]
    assert second.counts["authored_tweets"] == 1
    assert second.counts["deleted_authored_tweets"] == 1
    assert second.counts["likes"] == 1
    assert second.detail_lookups == 3
    assert second.detail_terminal_unavailable == 1
    assert second.detail_transient_failures == 2
    assert second.pending_enrichment == 4

    store = open_archive_store(paths, create=False)
    assert store is not None
    manifest_rows = store._query(expr="record_type = 'import_manifest'")
    manifest_counts = json.loads(manifest_rows[0]["counts_json"])
    assert manifest_counts["detail_lookups"] == 3
    # The saved queue is read from the rows, whatever this stubbed enrichment reported.
    assert manifest_counts["pending_enrichment"] == store.count_incomplete_initial_enrichment()
    store.close()


def test_repeated_import_keeps_ingest_warnings_and_reports_this_runs_followup(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    _disable_live_reconciliation(monkeypatch)

    first = asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    async def fake_followup(**_kwargs):
        return archive_enrich.ArchiveEnrichResult(
            warnings=["detail enrichment failed: upstream 429"],
            pending_enrichment=first.pending_enrichment,
        )

    monkeypatch.setattr(archive_enrich, "_run_archive_followup", fake_followup)

    second = asyncio.run(
        import_x_archive(
            archive_dir,
            enrich=True,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    expected_warning = (
        "archive does not contain a bookmark dataset (expected for current official "
        "Twitter/X archives)"
    )
    assert expected_warning in second.warnings
    assert "detail enrichment failed: upstream 429" in second.warnings

    store = open_archive_store(paths, create=False)
    assert store is not None
    manifest_row = store.completed_manifests()[0]
    assert json.loads(manifest_row["warnings_json"]) == [expected_warning]
    store.close()


def test_followup_reruns_do_not_grow_the_stored_warnings(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    no_auth = "live reconciliation skipped: no auth configured"
    bookmark = (
        "archive does not contain a bookmark dataset (expected for current official "
        "Twitter/X archives)"
    )

    async def fake_followup(**_kwargs):
        return archive_enrich.ArchiveEnrichResult(warnings=[no_auth])

    monkeypatch.setattr(archive_enrich, "_run_archive_followup", fake_followup)

    results = [
        asyncio.run(
            import_x_archive(archive_dir, config=AppConfig(), paths=paths, console=_console())
        )
        for _ in range(4)
    ]

    assert [result.warnings.count(no_auth) for result in results] == [1, 1, 1, 1]
    store = open_archive_store(paths, create=False)
    assert store is not None
    manifest_row = store.completed_manifests()[0]
    assert json.loads(manifest_row["warnings_json"]) == [bookmark]
    store.close()


def test_warnings_an_earlier_release_accumulated_collapse_on_the_next_followup(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)

    async def fake_followup(**_kwargs):
        return archive_enrich.ArchiveEnrichResult()

    monkeypatch.setattr(archive_enrich, "_run_archive_followup", fake_followup)
    asyncio.run(import_x_archive(archive_dir, config=AppConfig(), paths=paths, console=_console()))
    store = open_archive_store(paths, create=False)
    assert store is not None
    row = store.completed_manifests()[0]
    store.set_import_manifest(
        row["archive_digest"],
        archive_generation_date=None,
        status="completed",
        warnings=["old warning", "old warning", "old warning"],
        counts=json.loads(row["counts_json"]),
    )
    store.close()

    asyncio.run(enrich_imported_archive(config=AppConfig(), paths=paths, console=_console()))

    store = open_archive_store(paths, create=False)
    assert store is not None
    row = store.completed_manifests()[0]
    assert json.loads(row["warnings_json"]) == ["old warning"]
    store.close()


def test_import_and_standalone_enrich_save_the_same_counts(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_followup(**_kwargs):
        return archive_enrich.ArchiveEnrichResult(detail_lookups=3)

    monkeypatch.setattr(archive_enrich, "_run_archive_followup", fake_followup)

    def saved_counts() -> dict[str, int]:
        store = open_archive_store(paths, create=False)
        assert store is not None
        row = store.completed_manifests()[0]
        store.close()
        return json.loads(row["counts_json"])

    imported = asyncio.run(
        import_x_archive(
            _write_archive_dir(tmp_path), config=AppConfig(), paths=paths, console=_console()
        )
    )
    after_import = saved_counts()
    assert imported.counts == after_import
    assert after_import["detail_lookups"] == 3
    assert {"transient_due", "transient_delayed"} <= after_import.keys()

    asyncio.run(enrich_imported_archive(config=AppConfig(), paths=paths, console=_console()))

    assert saved_counts() == after_import


def test_enrich_imported_archive_requires_completed_import(paths) -> None:
    with pytest.raises(ConfigError, match="No completed Twitter/X archive import found"):
        asyncio.run(
            enrich_imported_archive(
                config=AppConfig(),
                paths=paths,
                console=_console(),
            )
        )


def test_enrich_imported_archive_reuses_existing_import_state(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    _disable_live_reconciliation(monkeypatch)

    asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    captured: dict[str, object] = {}
    enrichment_started_at = "2099-01-01T00:00:00+00:00"

    async def fake_reconciliation(**kwargs):
        captured["collections"] = kwargs["collections"]
        return ["tweets", "likes"], ["bulk reconciliation warning"], _auth_bundle()

    async def fake_enrich_pending_rows(**kwargs):
        captured["limit"] = kwargs["limit"]
        captured["started_at"] = kwargs["started_at"]
        return archive_enrich.ArchiveEnrichResult(
            detail_lookups=4,
            detail_terminal_unavailable=1,
            detail_transient_failures=2,
            pending_enrichment=3,
            pending_untouched=3,
        )

    monkeypatch.setattr(archive_enrich, "_run_live_reconciliation", fake_reconciliation)
    monkeypatch.setattr(archive_enrich, "_enrich_pending_rows", fake_enrich_pending_rows)
    monkeypatch.setattr(archive_enrich, "utc_now", lambda: enrichment_started_at)

    result = asyncio.run(
        enrich_imported_archive(
            limit=25,
            reconcile_live=True,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    assert captured == {
        "collections": ["tweets", "likes"],
        "limit": 25,
        "started_at": enrichment_started_at,
    }
    assert result.reconciled_collections == ["tweets", "likes"]
    assert result.warnings == ["bulk reconciliation warning"]
    assert result.detail_lookups == 4
    assert result.detail_terminal_unavailable == 1
    assert result.detail_transient_failures == 2
    assert result.pending_enrichment == 3


def test_enrich_imported_archive_can_skip_live_reconciliation(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    _disable_live_reconciliation(monkeypatch)

    asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    provided_auth = _auth_bundle()
    captured: dict[str, object] = {}

    async def fail_reconciliation(**_kwargs):
        raise AssertionError("live reconciliation should be skipped")

    async def fake_enrich_pending_rows(**kwargs):
        captured["limit"] = kwargs["limit"]
        captured["auth_token"] = kwargs["auth_bundle"].auth_token
        captured["user_id"] = kwargs["auth_bundle"].user_id
        return archive_enrich.ArchiveEnrichResult(
            detail_lookups=5,
            detail_terminal_unavailable=0,
            detail_transient_failures=1,
            pending_enrichment=2,
            pending_untouched=2,
        )

    monkeypatch.setattr(archive_enrich, "_run_live_reconciliation", fail_reconciliation)
    monkeypatch.setattr(archive_enrich, "_enrich_pending_rows", fake_enrich_pending_rows)

    result = asyncio.run(
        enrich_imported_archive(
            limit=7,
            reconcile_live=False,
            config=AppConfig(),
            paths=paths,
            auth_bundle=provided_auth,
            console=_console(),
        )
    )

    assert captured == {"limit": 7, "auth_token": "auth", "user_id": "42"}
    assert result.reconciled_collections == []
    assert result.warnings == []
    assert result.detail_lookups == 5
    assert result.detail_terminal_unavailable == 0
    assert result.detail_transient_failures == 1
    assert result.pending_enrichment == 2


def test_unlimited_archive_followup_selects_one_snapshot_without_limit(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    limits: list[int | None] = []
    trackers: list[object] = []

    async def fake_enrich_pending_rows(**kwargs):
        limits.append(kwargs["limit"])
        trackers.append(kwargs["absence_tracker"])
        return archive_enrich.ArchiveEnrichResult(
            detail_lookups=1_200,
            pending_enrichment=0,
        )

    monkeypatch.setattr(archive_enrich, "_enrich_pending_rows", fake_enrich_pending_rows)

    result = asyncio.run(
        archive_enrich._run_archive_followup(
            collections=[],
            detail_limit=None,
            reconcile_live=False,
            config=AppConfig(),
            paths=paths,
            auth_bundle=_auth_bundle(),
            transport=None,
            console=_console(),
        )
    )

    assert limits == [None]
    assert len(trackers) == 1
    assert result.detail_lookups == 1_200
