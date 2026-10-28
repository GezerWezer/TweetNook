from __future__ import annotations

from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
import typer

from tests.cli.conftest import _capture_console
from tests.conftest import patch_cli
from tweetnook.cli import enrichment as cli_enrichment
from tweetnook.cli import imports as cli_imports
from tweetnook.config import AppConfig


@pytest.mark.parametrize(
    ("runner", "command", "kwargs", "result", "summary"),
    [
        (
            "download_media",
            "media_download",
            {"limit": 5, "photos_only": True, "retry_failed": True},
            {"processed": 3, "downloaded": 2, "skipped": 1, "failed": 0},
            "media: 3 processed, 2 downloaded, 1 skipped, 0 failed",
        ),
        (
            "unfurl_urls",
            "unfurl_archive",
            {"limit": 10, "retry_failed": True},
            {"processed": 2, "updated": 2, "failed": 0},
            "unfurl: 2 processed, 2 updated, 0 failed",
        ),
    ],
)
def test_maintenance_commands_report_runner_result(
    paths, monkeypatch, runner: str, command: str, kwargs: dict, result: dict, summary: str
) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    patch_cli(monkeypatch, "load_config", lambda: (AppConfig(), paths))

    async def fake_runner(**_kwargs):
        return SimpleNamespace(**result)

    patch_cli(monkeypatch, runner, fake_runner)

    getattr(cli_enrichment, command)(**kwargs)

    assert summary in buffer.getvalue()


def test_refresh_archived_articles_reports_runner_result(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    patch_cli(monkeypatch, "load_config", lambda: (AppConfig(), paths))
    patch_cli(
        monkeypatch,
        "resolve_auth_bundle",
        lambda config: SimpleNamespace(auth_token="token", ct0="ct0"),
    )

    captured = {}

    async def fake_refresh_articles(**kwargs):
        assert kwargs["targets"] == ["https://x.com/example/status/2026531440414925307"]
        assert kwargs["preview_only"] is True
        captured["detail_delay"] = kwargs["config"].sync.detail_delay
        return SimpleNamespace(processed=1, updated=1, failed=0)

    patch_cli(monkeypatch, "refresh_articles", fake_refresh_articles)

    cli_enrichment.refresh_archived_articles(["https://x.com/example/status/2026531440414925307"])

    output = buffer.getvalue()
    assert captured == {"detail_delay": 0}
    assert "articles: 1 processed, 1 refreshed, 0 failed" in output


def test_expand_archive_threads_reports_runner_result(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    patch_cli(monkeypatch, "load_config", lambda: (AppConfig(), paths))
    patch_cli(
        monkeypatch,
        "resolve_auth_bundle",
        lambda config: SimpleNamespace(auth_token="token", ct0="ct0"),
    )

    async def fake_expand_threads(**kwargs):
        assert kwargs["targets"] == ["https://x.com/example/status/2026531440414925307"]
        assert kwargs["limit"] == 5
        assert kwargs["refresh"] is False
        return SimpleNamespace(processed=2, expanded=2, skipped=1, failed=0)

    patch_cli(monkeypatch, "expand_threads", fake_expand_threads)

    cli_enrichment.expand_archive_threads(
        ["https://x.com/example/status/2026531440414925307"],
        limit=5,
    )

    output = buffer.getvalue()
    assert "threads: 2 processed, 2 expanded, 1 skipped, 0 failed" in output


def test_expand_archive_threads_forwards_refresh_flag(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    patch_cli(monkeypatch, "load_config", lambda: (AppConfig(), paths))
    patch_cli(
        monkeypatch,
        "resolve_auth_bundle",
        lambda config: SimpleNamespace(auth_token="token", ct0="ct0"),
    )

    async def fake_expand_threads(**kwargs):
        assert kwargs["targets"] == ["100"]
        assert kwargs["refresh"] is True
        assert kwargs["config"].sync.detail_delay == 0
        return SimpleNamespace(processed=1, expanded=1, skipped=0, failed=0)

    patch_cli(monkeypatch, "expand_threads", fake_expand_threads)

    cli_enrichment.expand_archive_threads(["100"], refresh=True)

    assert "threads: 1 processed, 1 expanded, 0 skipped, 0 failed" in buffer.getvalue()


def test_expand_archive_threads_forwards_linked_depth_overrides(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    patch_cli(monkeypatch, "load_config", lambda: (AppConfig(), paths))
    patch_cli(
        monkeypatch,
        "resolve_auth_bundle",
        lambda config: SimpleNamespace(auth_token="token", ct0="ct0"),
    )
    seen: dict[str, int] = {}

    async def fake_expand_threads(**kwargs):
        seen["max"] = kwargs["config"].sync.max_linked_depth
        seen["reply"] = kwargs["config"].sync.reply_linked_depth
        return SimpleNamespace(processed=0, expanded=0, skipped=0, failed=0)

    patch_cli(monkeypatch, "expand_threads", fake_expand_threads)

    cli_enrichment.expand_archive_threads(None, max_linked_depth=3, reply_linked_depth=1)

    assert seen == {"max": 3, "reply": 1}


def test_import_x_archive_reports_runner_result(paths, monkeypatch, tmp_path: Path) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    patch_cli(monkeypatch, "load_config", lambda: (AppConfig(), paths))
    captured = {}

    async def fake_import_x_archive(
        archive,
        *,
        offline=False,
        regen=False,
        enrich=False,
        detail_lookups=0,
        sample_limit=None,
        debug=False,
        config=None,
        paths=None,
        auth_bundle=None,
        console=None,
    ):
        captured.update(
            {
                "archive": archive,
                "regen": regen,
                "enrich": enrich,
                "detail_lookups": detail_lookups,
                "sample_limit": sample_limit,
                "debug": debug,
                "detail_delay": config.sync.detail_delay if config is not None else None,
                "auth_bundle": auth_bundle,
            }
        )
        return SimpleNamespace(
            skipped=False,
            followup_performed=True,
            counts={
                "authored_tweets": 2,
                "deleted_authored_tweets": 1,
                "likes": 3,
                "media_files_copied": 4,
            },
            warnings=["archive does not contain a bookmark dataset"],
            reconciled_collections=["tweets", "likes"],
            detail_lookups=5,
            detail_terminal_unavailable=1,
            detail_transient_failures=2,
            pending_enrichment=9,
        )

    patch_cli(monkeypatch, "import_x_archive", fake_import_x_archive)
    archive_path = tmp_path / "archive.zip"
    archive_path.write_bytes(b"placeholder")

    cli_imports.import_x_archive_command(
        archive_path,
        regen=True,
        detail_lookups=25,
        sample_limit=100,
        debug=True,
    )

    assert captured == {
        "archive": archive_path,
        "regen": True,
        "enrich": True,
        "detail_lookups": 25,
        "sample_limit": 100,
        "debug": True,
        "detail_delay": 0,
        "auth_bundle": None,
    }
    output = " ".join(buffer.getvalue().split())
    assert "archive import: 2 authored, 1 deleted authored, 3 likes, 4 media files copied" in output
    assert "live reconciliation: tweets, likes" in output
    assert (
        "detail enrichment: 5 enriched, 1 terminal, 2 transient failures, "
        "9 pending untouched, 0 transient due, 0 transient delayed"
    ) in output
    assert "archive does not contain a bookmark dataset" in output


def test_import_x_archive_interrupt_reports_completed_import_and_continuation(
    paths, monkeypatch, tmp_path: Path
) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    patch_cli(monkeypatch, "load_config", lambda: (AppConfig(), paths))

    async def fake_import_x_archive(*_args, **_kwargs):
        raise cli_imports.ArchiveEnrichmentInterrupted(12_481)

    patch_cli(monkeypatch, "import_x_archive", fake_import_x_archive)
    archive_path = tmp_path / "archive.zip"
    archive_path.write_bytes(b"placeholder")

    with pytest.raises(typer.Exit) as excinfo:
        cli_imports.import_x_archive_command(archive_path)

    assert excinfo.value.exit_code == 130
    output = buffer.getvalue()
    assert "Archive import is complete." in output
    assert "Archive enrichment was interrupted." in output
    assert "12,481 sparse archive tweets remain incomplete." in output
    assert "tweetnook import enrich" in output


def test_archive_followup_explains_when_only_delayed_transient_rows_remain() -> None:
    result = SimpleNamespace(
        reconciled_collections=[],
        selected=0,
        detail_lookups=0,
        detail_terminal_unavailable=0,
        detail_transient_failures=0,
        pending_enrichment=2_827,
        pending_untouched=0,
        transient_due=0,
        transient_delayed=2_827,
        warnings=[],
    )

    output = cli_imports._archive_followup_summary(result)

    assert "0 pending untouched" in output
    assert "2,827 transient delayed" in output


def test_import_x_archive_enrich_reuses_existing_import(paths, monkeypatch, tmp_path: Path) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    patch_cli(monkeypatch, "load_config", lambda: (AppConfig(), paths))
    captured = {}

    async def fake_import_x_archive(
        archive,
        *,
        offline=False,
        regen=False,
        enrich=False,
        detail_lookups=0,
        sample_limit=None,
        debug=False,
        config=None,
        paths=None,
        auth_bundle=None,
        console=None,
    ):
        captured.update(
            {
                "archive": archive,
                "regen": regen,
                "enrich": enrich,
                "detail_lookups": detail_lookups,
                "sample_limit": sample_limit,
                "debug": debug,
            }
        )
        return SimpleNamespace(
            skipped=True,
            followup_performed=True,
            counts={
                "authored_tweets": 2,
                "deleted_authored_tweets": 1,
                "likes": 3,
                "media_files_copied": 4,
            },
            warnings=[],
            reconciled_collections=["likes"],
            detail_lookups=7,
            detail_terminal_unavailable=0,
            detail_transient_failures=1,
            pending_enrichment=5,
        )

    patch_cli(monkeypatch, "import_x_archive", fake_import_x_archive)
    archive_path = tmp_path / "archive.zip"
    archive_path.write_bytes(b"placeholder")

    cli_imports.import_x_archive_command(archive_path, enrich=True)

    assert captured == {
        "archive": archive_path,
        "regen": False,
        "enrich": True,
        "detail_lookups": 0,
        "sample_limit": None,
        "debug": False,
    }
    output = " ".join(buffer.getvalue().split())
    assert "already present; keeping existing imported data and running" in output
    assert "follow-up enrichment" in output
    assert "live reconciliation: likes" in output
    assert "detail enrichment: 7 enriched, 0 terminal, 1 transient failures" in output
    assert "5 pending untouched" in output


@pytest.mark.parametrize("collections", [["bookmarks", "likes"], ["tweets"]])
def test_import_enrich_runs_followup_for_existing_imports(paths, monkeypatch, collections) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    config = AppConfig()
    config.sync.collections = collections
    patch_cli(monkeypatch, "load_config", lambda: (config, paths))
    patch_cli(
        monkeypatch,
        "resolve_auth_bundle",
        lambda config: SimpleNamespace(auth_token="token", ct0="ct0"),
    )
    captured = {}

    async def fake_enrich_imported_archive(
        *,
        limit=None,
        config=None,
        paths=None,
        auth_bundle=None,
        transport=None,
        console=None,
    ):
        from tweetnook.jobs.pipeline import current_pipeline

        assert current_pipeline().step_state("archive-auth") == "complete"
        captured.update(
            {
                "limit": limit,
                "auth_bundle": auth_bundle,
                "detail_delay": config.sync.detail_delay if config is not None else None,
            }
        )
        return SimpleNamespace(
            warnings=["detail enrichment failed: upstream 429"],
            reconciled_collections=["tweets", "likes"],
            detail_lookups=9,
            detail_terminal_unavailable=2,
            detail_transient_failures=1,
            pending_enrichment=4,
        )

    patch_cli(monkeypatch, "enrich_imported_archive", fake_enrich_imported_archive)

    async def fake_sync_all(**kwargs):
        assert captured["limit"] == 50
        assert kwargs["backfill"] is True
        assert kwargs["full"] is False
        assert kwargs["limit"] is None
        assert kwargs["config"].sync.collections == collections
        assert kwargs["followups"].threads
        captured["syncs"] = captured.get("syncs", 0) + 1
        return SimpleNamespace(exit_code=0, errors={})

    patch_cli(monkeypatch, "sync_all", fake_sync_all)

    cli_imports.import_archive_enrich(limit=50)

    assert captured["limit"] == 50
    assert captured["auth_bundle"].auth_token == "token"
    assert captured["detail_delay"] == 0
    assert captured["syncs"] == 1
    output = " ".join(buffer.getvalue().split())
    assert "archive enrich: existing imported archive data" in output
    assert "live reconciliation: tweets, likes" in output
    assert "detail enrichment: 9 enriched, 2 terminal, 1 transient failures" in output
    assert "4 pending untouched" in output
    assert "detail enrichment failed: upstream 429" in output


def test_import_enrich_reports_a_failed_automatic_sync_separately(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    patch_cli(monkeypatch, "load_config", lambda: (AppConfig(), paths))
    patch_cli(monkeypatch, "resolve_auth_bundle", lambda config: object())
    events = []

    async def enrich(**kwargs):
        events.append("enriched")
        return SimpleNamespace(
            warnings=[],
            reconciled_collections=[],
            detail_lookups=5,
            detail_terminal_unavailable=0,
            detail_transient_failures=0,
            pending_enrichment=0,
        )

    async def sync(**kwargs):
        events.append("sync")
        return SimpleNamespace(exit_code=2, errors={"likes": "Connection expired"})

    patch_cli(monkeypatch, "enrich_imported_archive", enrich)
    patch_cli(monkeypatch, "sync_all", sync)
    with pytest.raises(typer.Exit) as raised:
        cli_imports.import_archive_enrich()
    assert raised.value.exit_code == 2
    assert events == ["enriched", "sync"]
    assert "Archive enrichment saved; automatic sync did not finish" in buffer.getvalue()


@pytest.mark.parametrize(
    ("raised", "exit_code", "expected"),
    [
        (
            cli_imports.ArchiveEnrichmentInterrupted(12_481),
            130,
            "Archive enrichment was interrupted.",
        ),
        (
            cli_imports.ArchiveEnrichmentAborted(17, TypeError("parser bug")),
            2,
            "Archive enrichment stopped after TypeError: parser bug",
        ),
    ],
)
def test_import_enrich_reports_interruption_or_systemic_abort(
    paths, monkeypatch, raised, exit_code: int, expected: str
) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    patch_cli(monkeypatch, "load_config", lambda: (AppConfig(), paths))
    patch_cli(
        monkeypatch,
        "resolve_auth_bundle",
        lambda config: SimpleNamespace(auth_token="token", ct0="ct0"),
    )

    async def fail_enrichment(**_kwargs):
        raise raised

    patch_cli(monkeypatch, "enrich_imported_archive", fail_enrichment)
    patch_cli(
        monkeypatch,
        "sync_all",
        lambda **kwargs: pytest.fail("aborted enrichment must not start sync"),
    )

    with pytest.raises(typer.Exit) as excinfo:
        cli_imports.import_archive_enrich()

    assert excinfo.value.exit_code == exit_code
    output = buffer.getvalue()
    assert expected in output
    assert "tweetnook import enrich" in output
    assert ("12,481" if exit_code == 130 else "17") in output
