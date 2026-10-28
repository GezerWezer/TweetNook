from __future__ import annotations

import asyncio
import json
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

import tweetnook.importer.archive_enrich as archive_enrich
from tests.archive_import.conftest import (
    _console,
    _disable_live_reconciliation,
    _wrap_ytd,
    _write_archive_dir,
    _write_archive_zip,
)
from tweetnook.auth import ResolvedAuthBundle
from tweetnook.config import AppConfig
from tweetnook.importer.archive import import_x_archive
from tweetnook.jobs.pipeline import PipelineReporter
from tweetnook.storage import open_archive_store


def _write_root_layout_archive_dir(base: Path, **kwargs: object) -> Path:
    root = _write_archive_dir(base, **kwargs)
    data_dir = root / "data"
    for path in sorted(data_dir.iterdir()):
        path.rename(root / path.name)
    data_dir.rmdir()
    return root


def test_offline_import_copies_full_archive_without_live_followup(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)

    async def unexpected_followup(*args, **kwargs):
        pytest.fail("Offline import must not start live follow-up")

    monkeypatch.setattr(archive_enrich, "_run_archive_followup", unexpected_followup)
    for repeated in (False, True):
        result = asyncio.run(
            import_x_archive(
                archive_dir,
                config=AppConfig(),
                paths=paths,
                console=_console(),
                offline=True,
                enrich=True,
                detail_lookups=100,
            )
        )
        assert result.skipped is repeated
        assert not result.followup_performed
        assert result.counts["authored_tweets"] == 1
        assert result.counts["likes"] == 1
        assert result.counts["media_files_copied"] == 1
        assert (paths.data_dir / "media/100/3_500.jpg").is_file()

    store = open_archive_store(paths, create=False)
    assert store is not None
    assert store._query(expr="record_type = 'import_manifest'")[0]["status"] == "completed"
    store.close()


@pytest.mark.parametrize("deferred", [False, True])
def test_setup_deferred_import_enriches_without_live_sync(paths, tmp_path, monkeypatch, deferred):
    archive_dir = _write_archive_dir(tmp_path)
    _disable_live_reconciliation(monkeypatch)

    async def unexpected_reconciliation(**_kwargs):
        pytest.fail("Setup's import must leave live sync to the first-sync task")

    monkeypatch.setattr(archive_enrich, "_run_live_reconciliation", unexpected_reconciliation)
    console = _console()
    reporter = PipelineReporter(console, "archive import", interactive=False)
    with reporter:
        asyncio.run(
            import_x_archive(
                archive_dir,
                config=AppConfig(),
                paths=paths,
                console=console,
                offline=not deferred,
                defer_followup=deferred,
            )
        )
    summary = reporter._state_snapshot()["summary"]
    assert ("Live sync deferred to the separate Setup" in summary) is deferred
    assert ("skipped (offline)" in summary) is not deferred
    # Without an account, enrichment still ran through FxTwitter (offline in tests).
    assert reporter.has_step("archive-enrich") is deferred
    assert reporter.step_state("archive-auth") == ("skipped" if deferred else None)


@pytest.mark.parametrize("deferred", [False, True])
def test_import_without_account_under_replay_enriches_through_replay_source(
    paths, tmp_path, monkeypatch, deferred
):
    archive_dir = _write_archive_dir(tmp_path)
    _disable_live_reconciliation(monkeypatch)
    for name in ("TWEETNOOK_AUTH_TOKEN", "TWEETNOOK_CT0"):
        monkeypatch.delenv(name, raising=False)
    bundles: list[ResolvedAuthBundle | None] = []

    async def capture_enrichment(*, auth_bundle, **_kwargs):
        bundles.append(auth_bundle)
        return archive_enrich.ArchiveEnrichResult()

    monkeypatch.setattr(archive_enrich, "_enrich_pending_rows", capture_enrichment)
    # Replay switches FxTwitter off; its source answers TweetDetail without cookies.
    config = AppConfig.model_validate({"replay": {"url": "http://127.0.0.1:9"}})
    console = _console()
    reporter = PipelineReporter(console, "archive import", interactive=False)
    with reporter:
        result = asyncio.run(
            import_x_archive(
                archive_dir,
                config=config,
                paths=paths,
                console=console,
                enrich=True,
                defer_followup=deferred,
            )
        )
    assert len(bundles) == 1 and bundles[0] is not None
    assert bundles[0].auth_token_source == "replay"
    assert not any("enrichment skipped" in warning for warning in result.warnings)
    if deferred:
        assert reporter.step_state("archive-auth") == "skipped"


def test_existing_archive_auth_completes_declared_step(monkeypatch):
    bundle = ResolvedAuthBundle(
        auth_token="synthetic", ct0="synthetic", auth_token_source="test", ct0_source="test"
    )
    reporter = PipelineReporter(_console(), "archive enrich", interactive=False)
    monkeypatch.setattr(
        archive_enrich, "resolve_auth_bundle", lambda _: pytest.fail("Already resolved")
    )
    with reporter:
        reporter.add_step("archive-auth", "Authentication", total=1, unit="session")
        assert archive_enrich._resolve_archive_followup_auth(AppConfig(), bundle) is bundle
        assert reporter.step_state("archive-auth") == "complete"


def test_import_x_archive_directory_populates_archive_and_copies_media(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    _disable_live_reconciliation(monkeypatch)

    result = asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    assert result.skipped is False
    assert result.counts["authored_tweets"] == 1
    assert result.counts["deleted_authored_tweets"] == 1
    assert result.counts["likes"] == 1
    assert result.counts["media_files_copied"] == 1
    assert result.pending_enrichment == 1
    assert any("bookmark dataset" in warning for warning in result.warnings)
    assert any("live reconciliation skipped" in warning for warning in result.warnings)

    store = open_archive_store(paths, create=False)
    assert store is not None
    assert store.get_archive_owner_id() == "42"
    assert store.counts()["import_manifests"] == 1

    tweet_rows = store._query(expr="record_type = 'tweet'")
    row_by_key = {(row["collection_type"], row["tweet_id"]): row for row in tweet_rows}
    assert row_by_key[("tweet", "100")]["source"] == "x_archive"
    assert row_by_key[("tweet", "200")]["deleted_at"] == "Mon Mar 16 00:00:00 +0000 2026"
    assert row_by_key[("like", "300")]["sort_index"] == "-1"

    tweet_objects = {
        row["tweet_id"]: row for row in store._query(expr="record_type = 'tweet_object'")
    }
    assert tweet_objects["100"]["source"] == "x_archive"
    assert tweet_objects["200"]["enrichment_state"] == "terminal_unavailable"
    assert tweet_objects["200"]["enrichment_reason"] == "archive_deleted"
    assert tweet_objects["200"]["enrichment_retry_eligible"] == 0
    assert tweet_objects["300"]["enrichment_state"] == "pending"

    media_rows = store._query(expr="record_type = 'media'")
    assert len(media_rows) == 1
    assert media_rows[0]["download_state"] == "done"
    assert media_rows[0]["local_path"] == "media/100/3_500.jpg"
    assert (paths.data_dir / media_rows[0]["local_path"]).exists()

    manifest_rows = store._query(expr="record_type = 'import_manifest'")
    manifest_counts = json.loads(manifest_rows[0]["counts_json"])
    assert manifest_rows[0]["enrichment_followup_status"] == "enrichment_complete"
    assert manifest_rows[0]["enrichment_aborted_reason"] is None
    assert manifest_counts["pending_enrichment"] == 1
    store.close()


def test_deleted_tweets_without_deleted_header_remain_deleted(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    (archive_dir / "data" / "deleted-tweet-headers.js").write_text(
        _wrap_ytd("YTD.deleted_tweet_headers.part0", []),
        encoding="utf-8",
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

    assert result.counts["authored_tweets"] == 1
    assert result.counts["deleted_authored_tweets"] == 1

    store = open_archive_store(paths, create=False)
    assert store is not None
    tweet_row = store._query(expr="row_key = 'tweet:tweet::200'", limit=1)[0]
    assert tweet_row["deleted_at"] is None
    tweet_object = store._query(expr="row_key = 'tweet_object:200'", limit=1)[0]
    assert tweet_object["deleted_at"] is None
    assert tweet_object["enrichment_state"] == "terminal_unavailable"
    assert tweet_object["enrichment_reason"] == "archive_deleted"
    assert tweet_object["enrichment_retry_eligible"] == 0
    store.close()


def test_import_x_archive_logs_progress_on_tty(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    _disable_live_reconciliation(monkeypatch)
    buffer = StringIO()
    console = Console(file=buffer, force_terminal=True, color_system=None)

    asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=console,
            debug=True,
        )
    )

    output = buffer.getvalue()
    assert "archive import: opening" in output
    assert "archive import: hashing archive contents for idempotence check..." in output
    assert "archive import: loading archive datasets..." in output
    assert "archive import hash" in output
    assert "archive import likes" in output
    assert "archive import media" in output
    assert "archive import: running follow-up reconciliation and enrichment..." in output
    assert "archive import: debug summary:" in output


def test_import_x_archive_zip_populates_archive_and_copies_media(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    archive_zip = _write_archive_zip(archive_dir, tmp_path / "archive.zip")
    _disable_live_reconciliation(monkeypatch)

    result = asyncio.run(
        import_x_archive(
            archive_zip,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    assert result.skipped is False
    assert result.counts["authored_tweets"] == 1
    assert result.counts["deleted_authored_tweets"] == 1
    assert result.counts["likes"] == 1
    assert result.counts["media_files_copied"] == 1
    assert (paths.data_dir / "media" / "100" / "3_500.jpg").exists()


def test_import_x_archive_supports_root_manifest_layout(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_root_layout_archive_dir(tmp_path)
    _disable_live_reconciliation(monkeypatch)

    result = asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    assert result.skipped is False
    assert result.counts["authored_tweets"] == 1
    assert result.counts["deleted_authored_tweets"] == 1
    assert result.counts["likes"] == 1
    assert result.counts["media_files_copied"] == 1
    assert (paths.data_dir / "media" / "100" / "3_500.jpg").exists()


def test_repeated_import_short_circuits_across_directory_and_zip_inputs(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    archive_zip = _write_archive_zip(archive_dir, tmp_path / "archive.zip")
    _disable_live_reconciliation(monkeypatch)

    first = asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )
    console = _console()
    reporter = PipelineReporter(console, "archive import", interactive=False)
    with reporter:
        second = asyncio.run(
            import_x_archive(
                archive_zip,
                enrich=False,
                config=AppConfig(),
                paths=paths,
                console=console,
            )
        )

    assert first.skipped is False
    assert second.skipped is True
    assert [step.key for step in reporter.steps] == ["archive-inspect"]
    store = open_archive_store(paths, create=False)
    assert store is not None
    assert store.counts()["import_manifests"] == 1
    store.close()
