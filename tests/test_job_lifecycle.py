"""locked_archive_job owns the lock, open, notice, body and close, and their failures."""

from __future__ import annotations

import asyncio
import errno
import json
import sqlite3
import time
from io import StringIO
from pathlib import Path

import httpx
import pytest
from rich.console import Console
from typer.testing import CliRunner

import tweetnook.cli as cli
import tweetnook.importer.archive as archive_import
import tweetnook.importer.archive_enrich as archive_enrich
import tweetnook.importer.archive_stages as archive_stages
from tests.archive_import.conftest import _write_archive_dir
from tests.collection_sync.conftest import _bookmarks_response_from_results, _save_query_ids
from tests.conftest import make_tweet_result, patch_cli
from tests.storage_sqlite.test_release_upgrades import released_archive
from tests.test_process_lock_os import other_process_sees
from tweetnook.config import AppConfig
from tweetnook.core.exceptions import ArchiveNotFoundError, ConfigError, StaleQueryIdError
from tweetnook.jobs import activity_history
from tweetnook.jobs import context as job_context
from tweetnook.jobs.activity_history import (
    cleanup_runs,
    list_runs,
    mark_run_process_result,
    new_run_id,
    read_run,
    read_run_log,
    reserve_run,
    runs_root,
    update_run_metadata,
)
from tweetnook.jobs.outcome import Outcome, finished_cleanly, recorded_outcome
from tweetnook.jobs.pipeline import PipelineReporter
from tweetnook.jobs.resource_failure import STORAGE_RECOVERY, StorageExhaustedError
from tweetnook.storage import ArchiveStore, open_archive_store
from tweetnook.storage.schema import SchemaMixin
from tweetnook.sync.collection import (
    LocalPreflightError,
    RemotePreflightError,
    run_preflight,
    sync_all,
    sync_collection,
)
from tweetnook.sync.followups import plan_sync_pipeline
from tweetnook.sync.steps import add_backfill_step, add_head_step, add_preflight_step

FULL_DISK = OSError(errno.ENOSPC, "No space left on device")
FULL_DATABASE = sqlite3.OperationalError("database or disk is full")


@pytest.fixture
def archive(paths, config):
    open_archive_store(paths, create=True, config=config).close()
    return paths


@pytest.fixture
def opened(monkeypatch):
    """Keep every store the job opens, so a test can see whether it was closed."""
    stores: list[ArchiveStore] = []
    original = job_context.open_archive_store

    def open_store(*args, **kwargs):
        store = original(*args, **kwargs)
        stores.append(store)
        return store

    monkeypatch.setattr(job_context, "open_archive_store", open_store)
    return stores


def _is_closed(store: ArchiveStore) -> bool:
    try:
        store.conn.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        return True
    return False


def _fail_close(monkeypatch, error: BaseException) -> None:
    original = ArchiveStore.close

    def close(self) -> None:
        original(self)
        raise error

    monkeypatch.setattr(ArchiveStore, "close", close)


@pytest.mark.parametrize("error_number", [errno.ENOSPC, errno.EDQUOT])
@pytest.mark.parametrize(("method", "target"), [("mkdir", "data_dir"), ("open", "lock_file")])
def test_job_preparation_classifies_storage_failures(
    paths, config, monkeypatch, error_number, method, target
):
    original = getattr(Path, method)
    failed_path = getattr(paths, target)

    def fail(path, *args, **kwargs):
        if path == failed_path:
            raise OSError(error_number, "Storage exhausted")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, method, fail)
    recovery = "Storage is full; free space and retry this job."
    with pytest.raises(StorageExhaustedError, match=recovery):
        with job_context.archive_job(
            config=config, paths=paths, create=True, storage_recovery=recovery
        ):
            pytest.fail("the body must not run")
    assert not paths.database_path.exists()
    assert other_process_sees(paths.lock_file) == "free"


@pytest.mark.asyncio
async def test_notice_failure_closes_the_store_and_reports_storage_full(
    archive, config, opened, monkeypatch
):
    def fail(_console, _store):
        raise FULL_DISK

    monkeypatch.setattr(job_context, "print_archive_migration_report", fail)
    with pytest.raises(StorageExhaustedError, match="Free or add disk space"):
        async with job_context.locked_archive_job(config=config, paths=archive):
            pytest.fail("the body must not run")
    assert _is_closed(opened[0])
    assert other_process_sees(archive.lock_file) == "free"


@pytest.mark.asyncio
async def test_open_failure_reports_storage_full_and_releases_the_lock(
    archive, config, monkeypatch
):
    def fail(*_args, **_kwargs):
        raise FULL_DATABASE

    monkeypatch.setattr(job_context, "open_archive_store", fail)
    with pytest.raises(StorageExhaustedError):
        async with job_context.locked_archive_job(config=config, paths=archive):
            pytest.fail("the body must not run")
    assert other_process_sees(archive.lock_file) == "free"


@pytest.mark.asyncio
async def test_close_failure_after_a_clean_body_is_reported(archive, config, monkeypatch):
    _fail_close(monkeypatch, FULL_DISK)
    with pytest.raises(StorageExhaustedError):
        async with job_context.locked_archive_job(config=config, paths=archive):
            pass
    assert other_process_sees(archive.lock_file) == "free"


@pytest.mark.parametrize(
    ("primary", "expected"),
    [
        (KeyboardInterrupt(), KeyboardInterrupt),
        (ConfigError("the real problem"), ConfigError),
    ],
)
@pytest.mark.asyncio
async def test_close_failure_never_replaces_the_primary_error(
    archive, config, monkeypatch, primary, expected
):
    _fail_close(monkeypatch, FULL_DISK)
    with pytest.raises(expected) as caught:
        async with job_context.locked_archive_job(config=config, paths=archive):
            raise primary
    assert caught.value is primary
    assert other_process_sees(archive.lock_file) == "free"


@pytest.mark.asyncio
async def test_lock_is_held_for_the_close(archive, config, monkeypatch):
    seen: list[str] = []
    original = ArchiveStore.close

    def close(self) -> None:
        seen.append(other_process_sees(archive.lock_file))
        original(self)

    monkeypatch.setattr(ArchiveStore, "close", close)
    async with job_context.locked_archive_job(config=config, paths=archive):
        pass
    assert seen == ["held"]


@pytest.mark.asyncio
async def test_interrupt_during_a_full_disk_stays_an_interrupt(archive, config):
    with pytest.raises(KeyboardInterrupt):
        async with job_context.locked_archive_job(config=config, paths=archive):
            try:
                raise FULL_DISK
            except OSError as exc:
                raise KeyboardInterrupt() from exc


@pytest.mark.asyncio
async def test_create_opens_a_new_archive_and_the_default_requires_one(paths, config):
    with pytest.raises(ArchiveNotFoundError):
        async with job_context.locked_archive_job(config=config, paths=paths):
            pytest.fail("the body must not run")
    assert not paths.database_path.exists()
    async with job_context.locked_archive_job(config=config, paths=paths, create=True):
        pass
    assert paths.database_path.exists()


def _downgrade(paths) -> None:
    with sqlite3.connect(paths.database_path) as connection:
        # Schema 5 still had these; the upgrade drops them.
        for column in ("enrichment_followup_status", "enrichment_aborted_reason"):
            connection.execute(f"ALTER TABLE archive ADD COLUMN {column} TEXT")
        connection.execute("PRAGMA user_version = 5")


@pytest.mark.asyncio
async def test_job_reports_an_upgrade_it_performed(archive, config):
    _downgrade(archive)
    output = StringIO()
    console = Console(file=output, width=200)
    async with job_context.locked_archive_job(config=config, paths=archive, console=console):
        pass
    assert "Archive schema migrated from v5" in output.getvalue()


@pytest.mark.parametrize(
    "args",
    [["optimize"], ["rehydrate"], ["prune"], ["prune", "--apply"]],
)
def test_maintenance_reports_full_disk_with_its_own_recovery(archive, config, monkeypatch, args):
    import tweetnook.enrichment.prune as prune

    def fail(*_args, **_kwargs):
        raise FULL_DATABASE

    buffer = StringIO()
    patch_cli(
        monkeypatch,
        "configure_logging",
        lambda: Console(file=buffer, force_terminal=False, color_system=None, width=400),
    )
    patch_cli(monkeypatch, "load_config", lambda: (AppConfig(), archive))
    monkeypatch.setattr(ArchiveStore, "optimize", fail)
    monkeypatch.setattr(ArchiveStore, "rehydration_work_total", fail)
    monkeypatch.setattr(prune, "plan_prune", fail)

    result = CliRunner().invoke(cli.app, args)

    assert result.exit_code == 2, result.output
    output = buffer.getvalue()
    assert "may have finished part of its work" in output
    assert STORAGE_RECOVERY not in output
    assert other_process_sees(archive.lock_file) == "free"


@pytest.mark.parametrize("command", [["optimize"], ["rehydrate"], ["prune"]])
def test_maintenance_reports_the_upgrade_it_performed(archive, monkeypatch, command):
    _downgrade(archive)
    buffer = StringIO()
    patch_cli(
        monkeypatch,
        "configure_logging",
        lambda: Console(file=buffer, force_terminal=False, color_system=None, width=400),
    )
    patch_cli(monkeypatch, "load_config", lambda: (AppConfig(), archive))

    CliRunner().invoke(cli.app, command)

    assert buffer.getvalue().count("Archive schema migrated from v5") == 1


def test_maintenance_without_an_archive_exits_one(paths, monkeypatch):
    buffer = StringIO()
    patch_cli(
        monkeypatch,
        "configure_logging",
        lambda: Console(file=buffer, force_terminal=False, color_system=None, width=400),
    )
    patch_cli(monkeypatch, "load_config", lambda: (AppConfig(), paths))

    result = CliRunner().invoke(cli.app, ["optimize"])

    assert result.exit_code == 1
    assert "No local archive found." in buffer.getvalue()


# ---- import ----


def _capture_cli(monkeypatch, paths, config) -> StringIO:
    buffer = StringIO()
    patch_cli(
        monkeypatch,
        "configure_logging",
        lambda: Console(file=buffer, force_terminal=False, color_system=None, width=400),
    )
    patch_cli(monkeypatch, "load_config", lambda: (config, paths))
    return buffer


def _manifest_statuses(paths, config) -> list[str]:
    with sqlite3.connect(paths.database_path) as connection:
        rows = connection.execute(
            "SELECT status FROM archive WHERE record_type = 'import_manifest'"
        ).fetchall()
    return [row[0] for row in rows]


def test_full_disk_during_import_reaches_the_cli_as_storage_full(
    paths, config, tmp_path, monkeypatch
):
    original = archive_stages.record_archive_capture
    calls = 0

    def fail_after_first(*args, **kwargs):
        nonlocal calls
        calls += 1
        original(*args, **kwargs)
        if calls == 1:
            raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(archive_stages, "record_archive_capture", fail_after_first)
    buffer = _capture_cli(monkeypatch, paths, config)
    archive_dir = _write_archive_dir(tmp_path)

    result = CliRunner().invoke(cli.app, ["import", "x-archive", str(archive_dir), "--offline"])

    assert result.exit_code == 2, result.output
    assert not isinstance(result.exception, OSError)
    assert "Free or add disk space" in buffer.getvalue()
    assert _manifest_statuses(paths, config) == ["failed"]
    assert other_process_sees(paths.lock_file) == "free"


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (KeyboardInterrupt(), KeyboardInterrupt),
        (ValueError("bad row"), ValueError),
    ],
)
@pytest.mark.asyncio
async def test_failed_manifest_write_never_replaces_the_import_error(
    paths, config, tmp_path, monkeypatch, raised, expected
):
    def fail_import(*_args, **_kwargs):
        raise raised

    monkeypatch.setattr(archive_stages, "import_likes", fail_import)
    original = ArchiveStore.set_import_manifest

    def fail_failed_write(self, digest, **kwargs):
        if kwargs.get("status") == "failed":
            raise sqlite3.OperationalError("database or disk is full")
        original(self, digest, **kwargs)

    monkeypatch.setattr(ArchiveStore, "set_import_manifest", fail_failed_write)

    with pytest.raises(expected) as caught:
        await archive_import.import_x_archive(
            _write_archive_dir(tmp_path),
            offline=True,
            config=config,
            paths=paths,
            console=Console(file=StringIO()),
        )

    assert caught.value is raised
    assert _manifest_statuses(paths, config) == ["in_progress"]
    assert other_process_sees(paths.lock_file) == "free"


# ---- enrichment follow-up ----


def test_import_followup_interrupt_saves_a_stopped_cli_run(paths, config, tmp_path, monkeypatch):
    _capture_cli(monkeypatch, paths, config)

    async def interrupted(**_kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(archive_enrich, "_run_archive_followup", interrupted)
    archive_dir = _write_archive_dir(tmp_path)
    result = CliRunner().invoke(cli.app, ["import", "x-archive", str(archive_dir)])

    assert result.exit_code == 130
    direct = json.loads(paths.activity_status_file.read_text())
    assert direct["outcome"] == "stopped"
    assert direct["stopped"] is True
    assert direct["success"] is False
    saved = read_run(paths.data_dir, direct["run_id"])
    assert saved["status"] == "stopped"
    assert saved["outcome"] == "stopped"
    supervised = mark_run_process_result(
        paths.data_dir, direct["run_id"], exit_code=130, stopped=True
    )
    assert supervised["snapshot"]["outcome"] == direct["outcome"]
    assert supervised["snapshot"]["stopped"] is direct["stopped"]


def _full_status_write(*_args, **_kwargs):
    raise sqlite3.OperationalError("database or disk is full")


@pytest.mark.asyncio
async def test_interrupted_followup_survives_its_status_write_failing(paths, config, monkeypatch):
    open_archive_store(paths, create=True, config=config).close()

    async def interrupted(**_kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(archive_enrich, "_run_archive_followup", interrupted)
    monkeypatch.setattr(archive_enrich, "_followup_manifest_rows", lambda *_: [{}])
    monkeypatch.setattr(archive_enrich, "record_followup_counts", _full_status_write)

    with pytest.raises(archive_enrich.ArchiveEnrichmentInterrupted) as caught:
        await archive_enrich.run_import_followup(
            counts={},
            manifest_digest="digest",
            detail_limit=None,
            reconcile_live=False,
            config=config,
            paths=paths,
            auth_bundle=None,
            transport=None,
            console=Console(file=StringIO()),
        )
    assert caught.value.remaining == 0
    assert other_process_sees(paths.lock_file) == "free"


@pytest.mark.asyncio
async def test_aborted_followup_survives_its_status_write_failing(paths, config, monkeypatch):
    open_archive_store(paths, create=True, config=config).close()
    cause = StaleQueryIdError("query id went stale", status_code=404)

    async def aborted(**_kwargs):
        raise cause

    monkeypatch.setattr(archive_enrich, "_run_archive_followup", aborted)
    monkeypatch.setattr(archive_enrich, "_followup_manifest_rows", lambda *_: [{}])
    monkeypatch.setattr(archive_enrich, "record_followup_counts", _full_status_write)

    with pytest.raises(archive_enrich.ArchiveEnrichmentAborted) as caught:
        await archive_enrich.run_import_followup(
            counts={},
            manifest_digest="digest",
            detail_limit=None,
            reconcile_live=False,
            config=config,
            paths=paths,
            auth_bundle=None,
            transport=None,
            console=Console(file=StringIO()),
        )
    assert caught.value.cause is cause


def test_remaining_text_admits_an_unreadable_count():
    assert archive_enrich.remaining_enrichment_text(None, "tweets") == (
        "Some tweets may remain incomplete."
    )
    assert archive_enrich.remaining_enrichment_text(1200, "tweets") == (
        "1,200 tweets remain incomplete."
    )


# ---- sync ----


def _empty_timeline(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=_bookmarks_response_from_results([]))


def test_sync_preflight_upgrade_full_disk_reaches_the_cli_as_a_typed_error(
    paths, config, monkeypatch
):
    released_archive(paths.database_path, 12)
    buffer = _capture_cli(monkeypatch, paths, config)
    monkeypatch.setattr("tweetnook.sync.collection.load_config", lambda: (config, paths))
    writes = []

    def fail_after_first_write(self):
        self.conn.execute("ALTER TABLE archive DROP COLUMN enrichment_followup_status")
        writes.append(True)
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(SchemaMixin, "_drop_unread_followup_columns", fail_after_first_write)
    result = CliRunner().invoke(cli.app, ["sync", "bookmarks"])

    assert writes == [True]
    assert result.exit_code == 2, result.output
    assert not isinstance(result.exception, sqlite3.Error)
    assert "Free or add disk space" in buffer.getvalue()
    with sqlite3.connect(paths.database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 12
        columns = {row[1] for row in connection.execute("PRAGMA table_info(archive)")}
        assert {"enrichment_followup_status", "enrichment_aborted_reason"} <= columns
        assert connection.execute("SELECT COUNT(*) FROM archive").fetchone()[0] == 3
    assert other_process_sees(paths.lock_file) == "free"


@pytest.mark.asyncio
async def test_sync_preflight_classifies_a_close_only_storage_failure(
    archive, config, auth_bundle, opened, monkeypatch
):
    _fail_close(monkeypatch, FULL_DISK)
    with pytest.raises(StorageExhaustedError):
        await run_preflight(
            config=config,
            paths=archive,
            collections=["bookmarks"],
            auth_bundle=auth_bundle,
            transport=httpx.MockTransport(_empty_timeline),
            console=Console(file=StringIO()),
        )
    assert len(opened) == 1
    assert _is_closed(opened[0])


@pytest.mark.asyncio
async def test_sync_preflight_preserves_owner_error_when_close_also_fails(
    archive, config, auth_bundle, opened, monkeypatch
):
    store = open_archive_store(archive, create=False, config=config)
    store.ensure_archive_owner_id("99")
    store.close()
    opened.clear()
    _fail_close(monkeypatch, FULL_DISK)
    with pytest.raises(LocalPreflightError, match="belongs to Twitter/X user 99"):
        await run_preflight(
            config=config,
            paths=archive,
            collections=["bookmarks"],
            auth_bundle=auth_bundle,
            transport=httpx.MockTransport(_empty_timeline),
            console=Console(file=StringIO()),
        )
    assert len(opened) == 1
    assert _is_closed(opened[0])


@pytest.mark.asyncio
async def test_full_disk_in_sync_setup_is_classified_and_closes_the_store(
    archive, config, auth_bundle, opened, monkeypatch
):
    _save_query_ids(archive)
    monkeypatch.setattr(ArchiveStore, "reset_sync_state", _full_status_write)

    with pytest.raises(StorageExhaustedError):
        await sync_collection(
            "bookmarks",
            full=True,
            config=config,
            paths=archive,
            auth_bundle=auth_bundle,
            transport=httpx.MockTransport(_empty_timeline),
            console=Console(file=StringIO()),
        )
    assert all(_is_closed(store) for store in opened)
    assert other_process_sees(archive.lock_file) == "free"


@pytest.mark.asyncio
async def test_sync_reports_an_upgrade_exactly_once(archive, config, auth_bundle):
    _save_query_ids(archive)
    _downgrade(archive)
    output = StringIO()

    await sync_collection(
        "bookmarks",
        full=False,
        config=config,
        paths=archive,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(_empty_timeline),
        console=Console(file=output, width=200),
    )

    assert output.getvalue().count("Archive schema migrated from v5") == 1


@pytest.mark.asyncio
async def test_failed_preflight_resolves_its_step_for_the_next_collection(
    paths, config, auth_bundle
):
    _save_query_ids(paths)

    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    errors: list[str] = []
    with PipelineReporter(Console(file=StringIO()), "sync", interactive=False) as pipeline:
        for collection in ("bookmarks", "likes"):
            with pytest.raises(RemotePreflightError) as caught:
                await sync_collection(
                    collection,
                    full=False,
                    config=config,
                    paths=paths,
                    auth_bundle=auth_bundle,
                    transport=httpx.MockTransport(offline),
                    console=Console(file=StringIO()),
                )
            errors.append(str(caught.value))
        states = {step.key: step.state for step in pipeline.steps}
        pipeline.finish("done", success=False)

    assert errors == ["ConnectError: offline", "ConnectError: offline"]
    assert states == {"preflight:bookmarks": "failed", "preflight:likes": "failed"}


@pytest.mark.asyncio
async def test_preflight_keeps_the_callers_query_ids_unchanged(paths, config, auth_bundle):
    given = {"Bookmarks": "qid"}
    await run_preflight(
        config=config,
        paths=paths,
        collections=["bookmarks"],
        auth_bundle=auth_bundle,
        query_ids=given,
        transport=httpx.MockTransport(_empty_timeline),
        console=Console(file=StringIO()),
    )
    assert given == {"Bookmarks": "qid"}


def _finished_snapshot(tmp_path, build) -> dict:
    state_path = tmp_path / "activity-status.json"
    console = Console(file=StringIO(), force_terminal=False)
    try:
        with PipelineReporter(console, "Job", state_path=state_path) as pipeline:
            build(pipeline)
    except (KeyboardInterrupt, RuntimeError):
        pass
    return json.loads(state_path.read_text())


def _two_steps(pipeline) -> None:
    pipeline.add_step("one", "One", total=2, unit="items")
    pipeline.add_step("two", "Two", total=2, unit="items")


def _finish_first(pipeline) -> None:
    _two_steps(pipeline)
    pipeline.start_step("one", activity="working")
    pipeline.complete_step("one", "done")


def _raise(error):
    raise error


@pytest.mark.parametrize(
    ("build", "outcome", "success"),
    [
        pytest.param(
            lambda p: (_finish_first(p), p.skip_step("two", "nothing due")),
            "succeeded",
            True,
            id="succeeded",
        ),
        pytest.param(_two_steps, "no_work", True, id="every-step-skipped"),
        pytest.param(
            lambda p: (
                _finish_first(p),
                p.start_step("two", activity="working"),
                p.fail_step("two", "limit", paused=True),
            ),
            "paused",
            True,
            id="paused",
        ),
        pytest.param(
            lambda p: (_finish_first(p), _raise(RuntimeError("boom"))),
            "partial",
            False,
            id="failed-after-finishing-a-step",
        ),
        pytest.param(
            lambda p: (
                _two_steps(p),
                p.start_step("one", activity="working"),
                _raise(RuntimeError("boom")),
            ),
            "failed",
            False,
            id="failed-before-finishing-a-step",
        ),
        pytest.param(
            lambda p: (_finish_first(p), _raise(KeyboardInterrupt())),
            "stopped",
            False,
            id="interrupted",
        ),
    ],
)
def test_reporter_decides_one_outcome(tmp_path, build, outcome, success):
    snapshot = _finished_snapshot(tmp_path, build)
    assert snapshot["outcome"] == outcome
    assert snapshot["success"] is success
    assert snapshot["stopped"] is (outcome == "stopped")
    assert not snapshot["running"]
    assert snapshot["active_step"] is None
    assert {step["state"] for step in snapshot["steps"]} <= {"complete", "skipped", "failed"}


def test_finish_keeps_an_open_steps_progress(tmp_path):
    def build(pipeline):
        _two_steps(pipeline)
        pipeline.start_step("one", activity="working")
        pipeline.update_step("one", completed=1)

    steps = {step["key"]: step for step in _finished_snapshot(tmp_path, build)["steps"]}
    assert steps["one"]["state"] == "complete"
    assert steps["one"]["completed"] == 1
    assert steps["two"]["state"] == "skipped"


def test_summary_wording_does_not_decide_a_stop(tmp_path):
    run_id = _finished_snapshot(
        tmp_path, lambda _: _raise(RuntimeError("Stopped responding: server gone"))
    )["run_id"]
    assert read_run(tmp_path, run_id)["status"] == "failed"
    assert read_run(tmp_path, run_id)["outcome"] == "failed"


def test_direct_and_supervised_interrupts_save_the_same_result(tmp_path):
    direct = _finished_snapshot(tmp_path, lambda p: (_finish_first(p), _raise(KeyboardInterrupt())))
    run_id = direct["run_id"]
    assert read_run(tmp_path, run_id)["status"] == "stopped"

    supervised = mark_run_process_result(tmp_path, run_id, exit_code=130, stopped=True)
    assert supervised["status"] == "stopped"
    assert supervised["snapshot"]["outcome"] == direct["outcome"] == "stopped"
    assert supervised["snapshot"]["stopped"] is direct["stopped"] is True


@pytest.mark.parametrize(
    ("reported", "exit_code", "stopped", "status", "outcome", "clean"),
    [
        ("succeeded", 0, False, "completed", "succeeded", True),
        ("no_work", 0, False, "completed", "no_work", True),
        # A worker can save a failure and still exit zero; the saved failure counts.
        ("failed", 0, False, "failed", "failed", False),
        ("partial", 2, False, "failed", "partial", False),
        # A nonzero exit outranks a worker that published success first.
        ("succeeded", 2, False, "failed", "failed", False),
        ("succeeded", 130, True, "stopped", "stopped", False),
        # Publication is best-effort: a clean exit with nothing saved isn't a failure...
        (None, 0, False, "interrupted", None, True),
        # ...and a failed exit with nothing saved is.
        (None, 2, False, "failed", "failed", False),
    ],
)
def test_process_result_settles_the_reported_outcome(
    tmp_path, reported, exit_code, stopped, status, outcome, clean
):
    run_id = new_run_id()
    reserve_run(tmp_path, run_id=run_id, title="Job", command=["job"], origin="web")
    if reported is not None:
        (runs_root(tmp_path) / run_id / "final.json").write_text(
            json.dumps(
                {
                    "running": False,
                    "outcome": reported,
                    "success": Outcome(reported).ok,
                    "steps": [],
                }
            )
        )
    run = mark_run_process_result(tmp_path, run_id, exit_code=exit_code, stopped=stopped)
    assert run["status"] == status
    assert run["outcome"] == outcome
    assert run["snapshot"]["outcome"] == outcome
    assert finished_cleanly(exit_code, recorded_outcome(run["snapshot"])) is clean


def test_history_saved_before_outcomes_still_reads(tmp_path):
    assert recorded_outcome({"status": "stopped", "success": False}) is Outcome.STOPPED
    assert recorded_outcome({"stopped": True, "success": False}) is Outcome.STOPPED
    assert recorded_outcome({"success": True}) is Outcome.SUCCEEDED
    assert recorded_outcome({"success": False}) is Outcome.FAILED
    assert recorded_outcome({"success": None}) is None


def test_one_collection_failing_is_one_issue_and_a_partial_run(
    archive, config, auth_bundle, monkeypatch
):
    _save_query_ids(archive)
    config.auth.user_id = "42"
    _capture_cli(monkeypatch, archive, config)
    likes_requests = []

    def likes_fail_after_preflight(request: httpx.Request) -> httpx.Response:
        if "Likes" in request.url.path:
            likes_requests.append(request)
            if len(likes_requests) > 1:
                return httpx.Response(500, json={"errors": [{"message": "boom"}]})
        return _empty_timeline(request)

    real_sync_all = sync_all

    def sync_all_offline(**kwargs):
        kwargs.update(
            config=config,
            paths=archive,
            auth_bundle=auth_bundle,
            transport=httpx.MockTransport(likes_fail_after_preflight),
            sleep=lambda _seconds: asyncio.sleep(0),
        )
        return real_sync_all(**kwargs)

    patch_cli(monkeypatch, "sync_all", sync_all_offline)
    patch_cli(monkeypatch, "resolve_auth_bundle", lambda _config: auth_bundle)

    result = CliRunner().invoke(cli.app, ["sync"])

    assert result.exit_code == 2, result.output
    status = json.loads(archive.activity_status_file.read_text())
    assert status["outcome"] == "partial"
    assert status["success"] is False
    failures = [issue for issue in status["issues"] if "sync failed" in issue["message"]]
    assert [issue["count"] for issue in failures] == [1]
    assert {step["state"] for step in status["steps"]} <= {"complete", "skipped", "failed"}


def _timeline_with_cursor(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json=_bookmarks_response_from_results(
            [make_tweet_result("101", "first")], cursor="cursor-page-2"
        ),
    )


@pytest.mark.asyncio
async def test_interrupted_head_only_sync_leaves_no_backfill_cursor(archive, config, auth_bundle):
    _save_query_ids(archive)
    store = open_archive_store(archive, create=False, config=config)
    store.set_sync_state(
        "bookmark", last_head_tweet_id=None, backfill_cursor="older", backfill_incomplete=True
    )
    store.close()

    class Interrupted(Exception):
        pass

    async def interrupt_after_first_page(_seconds):
        raise Interrupted

    with pytest.raises(Interrupted):
        await sync_collection(
            "bookmarks",
            full=False,
            head_only=True,
            config=config,
            paths=archive,
            auth_bundle=auth_bundle,
            transport=httpx.MockTransport(_timeline_with_cursor),
            console=Console(file=StringIO()),
            sleep=interrupt_after_first_page,
        )

    store = open_archive_store(archive, create=False, config=config)
    state = store.get_sync_state("bookmark")
    store.close()
    assert state.backfill_cursor is None
    assert state.backfill_incomplete is False
    assert state.last_head_tweet_id == "101"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "flags",
    [
        {"full": True, "head_only": True},
        {"full": False, "backfill": True, "head_only": True},
        {"full": True, "limit": -2},
    ],
)
async def test_impossible_sync_requests_stop_before_preflight_and_writes(
    archive, config, auth_bundle, flags
):
    store = open_archive_store(archive, create=False, config=config)
    store.set_sync_state(
        "bookmark", last_head_tweet_id="head", backfill_cursor="older", backfill_incomplete=True
    )
    store.close()

    def no_requests(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"preflight reached {request.url}")

    with pytest.raises(ConfigError):
        await sync_collection(
            "bookmarks",
            config=config,
            paths=archive,
            auth_bundle=auth_bundle,
            transport=httpx.MockTransport(no_requests),
            console=Console(file=StringIO()),
            **flags,
        )
    store = open_archive_store(archive, create=False, config=config)
    state = store.get_sync_state("bookmark")
    store.close()
    assert (state.last_head_tweet_id, state.backfill_cursor) == ("head", "older")


def test_planned_and_run_steps_are_the_same_steps(archive, config, auth_bundle):
    pipeline = PipelineReporter(Console(file=StringIO()), "sync", interactive=False)
    with pipeline:
        plan_sync_pipeline(
            pipeline,
            config=config,
            collections=["bookmarks", "tweets"],
            followups=None,
            head_only=False,
        )
        planned = [step.key for step in pipeline.steps]
        add_preflight_step(pipeline, ["bookmarks", "tweets"])
        for collection in ("bookmarks", "tweets"):
            add_head_step(pipeline, collection)
        add_backfill_step(pipeline, "tweets")
        assert [step.key for step in pipeline.steps] == planned
    assert planned == [
        "preflight:bookmarks,tweets",
        "sync:bookmarks:head",
        "sync:tweets:head",
        "sync:tweets:backfill",
    ]


def test_sync_limit_refuses_a_negative_value_before_any_request(
    archive, config, auth_bundle, monkeypatch
):
    output = _capture_cli(monkeypatch, archive, config)
    patch_cli(monkeypatch, "resolve_auth_bundle", lambda _config: auth_bundle)
    result = CliRunner().invoke(cli.app, ["sync", "bookmarks", "--limit", "-1"])
    assert result.exit_code == 1
    assert "--limit cannot be negative." in output.getvalue()


def _finished_run(data_dir) -> str:
    run_id = new_run_id()
    reserve_run(data_dir, run_id=run_id, title="Job", command=["job"], origin="web")
    mark_run_process_result(data_dir, run_id, exit_code=0)
    return run_id


def test_history_cleanup_removes_a_run_from_every_lookup(tmp_path):
    removed = _finished_run(tmp_path)
    update_run_metadata(tmp_path, removed, started_at=1.0, completed_at=2.0)
    kept = _finished_run(tmp_path)
    update_run_metadata(tmp_path, kept, completed_at=time.time())

    cleanup_runs(tmp_path, max_runs=1, retention_days=30)

    assert [run["run_id"] for run in list_runs(tmp_path)] == [kept]
    assert read_run(tmp_path, removed) is None
    assert read_run_log(tmp_path, removed) == ""
    assert read_run(tmp_path, kept) is not None


def test_cached_result_stays_while_its_run_still_exists(tmp_path, monkeypatch):
    """The cache exists for a run whose final result couldn't be written to disk."""
    run_id = new_run_id()
    reserve_run(tmp_path, run_id=run_id, title="Job", command=["job"], origin="web")

    def full(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(activity_history, "_atomic_json", full)
    mark_run_process_result(tmp_path, run_id, exit_code=2)
    monkeypatch.undo()

    run = read_run(tmp_path, run_id)
    assert run["status"] == "failed"
    assert "final result could not be saved" in run["snapshot"]["history_warning"]
    cleanup_runs(tmp_path, max_runs=10, retention_days=30)
    assert read_run(tmp_path, run_id)["status"] == "failed"
