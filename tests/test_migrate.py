from __future__ import annotations

import sqlite3
import sys
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from rich.console import Console
from typer.testing import CliRunner

from tweetnook import cli
from tweetnook.config import AppConfig
from tweetnook.pipeline import PipelineReporter, current_pipeline
from tweetnook.storage import migrate, open_archive_store
from tweetnook.storage.backend import SCHEMA_VERSION, ArchiveStore


class FakeLegacyTable:
    def __init__(self, total_rows: int) -> None:
        self.total_rows = total_rows

    def count_rows(self) -> int:
        return self.total_rows


@pytest.mark.parametrize(
    "migration_status,code",
    [
        ("complete", 0),
        ("partial", 0),
        ("source_missing", 2),
        ("dependency_missing", 2),
        ("table_missing", 2),
        ("destination_failed", 2),
        ("aborted", 2),
    ],
)
def test_migrate_cli_activates_the_shared_pipeline(
    monkeypatch: pytest.MonkeyPatch, paths, migration_status, code
) -> None:
    observed: list[str] = []

    def fake_run_migration(*, console: Console) -> migrate.MigrationResult:
        pipeline = current_pipeline()
        assert pipeline is not None
        observed.append(pipeline.title)
        return migrate.MigrationResult(status=migration_status)

    monkeypatch.setattr(migrate, "run_migration", fake_run_migration)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))

    result = CliRunner().invoke(cli.app, ["migrate"])

    assert result.exit_code == code, result.output
    assert observed == ["tweetnook migrate"]


class FakeLegacyDatabase:
    def __init__(
        self,
        table: FakeLegacyTable | None = None,
        *,
        open_error: BaseException | None = None,
    ) -> None:
        self.table = table
        self.open_error = open_error
        self.opened_tables: list[str] = []

    def open_table(self, name: str) -> FakeLegacyTable:
        self.opened_tables.append(name)
        if self.open_error:
            raise self.open_error
        assert self.table is not None
        return self.table


class FakeLanceDB:
    def __init__(
        self,
        database: FakeLegacyDatabase | None = None,
        *,
        connect_error: BaseException | None = None,
    ) -> None:
        self.database = database
        self.connect_error = connect_error
        self.connected_paths: list[Path] = []

    def connect(self, path: Path) -> FakeLegacyDatabase:
        self.connected_paths.append(path)
        if self.connect_error:
            raise self.connect_error
        assert self.database is not None
        return self.database


class FakeConnection:
    def __init__(self, *, rebuild_error: BaseException | None = None) -> None:
        self.rebuild_error = rebuild_error
        self.executed: list[str] = []
        self.commits = 0

    def execute(self, sql: str) -> None:
        self.executed.append(sql)
        if self.rebuild_error:
            raise self.rebuild_error

    def commit(self) -> None:
        self.commits += 1


class FakeStore:
    def __init__(
        self,
        events: list[object],
        *,
        connection: FakeConnection | None = None,
        label: str = "store",
    ) -> None:
        self.events = events
        self.conn = connection or FakeConnection()
        self.label = label
        self.closed = False

    def rebuild_search_index(self) -> None:
        self.conn.execute("INSERT INTO archive_fts(archive_fts) VALUES('rebuild')")
        self.conn.commit()

    def close(self) -> None:
        self.closed = True
        self.events.append(("close", self.label))


class FakeProgress:
    def __init__(self, total: int) -> None:
        self.total = total
        self.n = 0
        self.updates: list[int] = []
        self.messages: list[str] = []
        self.closed = False

    def update(self, amount: int) -> None:
        self.updates.append(amount)
        self.n += amount

    def write(self, message: str) -> None:
        self.messages.append(message)

    def close(self) -> None:
        self.closed = True


def worker_result(return_code: int) -> SimpleNamespace:
    return SimpleNamespace(returncode=return_code, stdout=b"", stderr=b"")


def install_legacy_source(
    monkeypatch: pytest.MonkeyPatch,
    paths,
    *,
    total_rows: int,
) -> tuple[AppConfig, Path, FakeLanceDB]:
    config = AppConfig()
    lance_path = paths.data_dir / "archive.lancedb"
    lance_path.mkdir(parents=True)
    legacy = FakeLanceDB(FakeLegacyDatabase(FakeLegacyTable(total_rows)))
    monkeypatch.setattr(migrate, "load_config", lambda: (config, paths))
    monkeypatch.setattr(migrate, "_import_lancedb", lambda: legacy)
    return config, lance_path, legacy


def install_fake_stores(
    monkeypatch: pytest.MonkeyPatch,
    *,
    events: list[object],
    final_connection: FakeConnection | None = None,
) -> tuple[list[tuple[bool, Any, Any]], list[FakeStore]]:
    calls: list[tuple[bool, Any, Any]] = []
    stores: list[FakeStore] = []

    def fake_open(paths, *, create: bool, config) -> FakeStore:
        calls.append((create, paths, config))
        label = "schema" if not stores else "fts"
        connection = final_connection if stores and final_connection else FakeConnection()
        store = FakeStore(events, connection=connection, label=label)
        stores.append(store)
        events.append(("open", label, create))
        return store

    monkeypatch.setattr(migrate, "open_archive_store", fake_open)
    return calls, stores


def install_worker_sequence(
    monkeypatch: pytest.MonkeyPatch,
    return_codes: list[int],
    *,
    events: list[object] | None = None,
) -> list[tuple[Path, Path, int, int]]:
    calls: list[tuple[Path, Path, int, int]] = []
    remaining = list(return_codes)

    def fake_worker(
        lance_path: Path,
        database_path: Path,
        batch_size: int,
        offset: int,
    ) -> SimpleNamespace:
        calls.append((lance_path, database_path, batch_size, offset))
        if events is not None:
            events.append(("worker", offset))
        return worker_result(remaining.pop(0))

    monkeypatch.setattr(migrate, "_run_worker", fake_worker)
    return calls


def test_worker_code_executes_batch_and_preserves_existing_rows(
    monkeypatch: pytest.MonkeyPatch,
    paths,
) -> None:
    config = AppConfig()
    store = open_archive_store(paths, create=True, config=config)
    assert store is not None
    store.conn.execute(
        """
        INSERT INTO archive (row_key, record_type, tweet_id, text)
        VALUES ('tweet_object:1', 'tweet_object', '1', 'newer SQLite value')
        """
    )
    store.conn.commit()
    store.close()

    class FakeSearch:
        def __init__(self) -> None:
            self.limit_value: int | None = None
            self.offset_value: int | None = None

        def limit(self, value: int) -> FakeSearch:
            self.limit_value = value
            return self

        def offset(self, value: int) -> FakeSearch:
            self.offset_value = value
            return self

        def to_list(self) -> list[dict[str, object]]:
            assert self.limit_value == 25
            assert self.offset_value == 75
            return [
                {
                    "row_key": "tweet_object:1",
                    "record_type": "tweet_object",
                    "tweet_id": "1",
                    "text": "legacy value must be ignored",
                },
                {
                    "row_key": "tweet_object:2",
                    "record_type": "tweet_object",
                    "tweet_id": "2",
                    "text": "migrated value",
                    "unknown_legacy_field": "ignored",
                },
            ]

    search = FakeSearch()
    table = SimpleNamespace(search=lambda: search)
    database = SimpleNamespace(
        open_table=lambda name: table
        if name == "archive"
        else pytest.fail(f"unexpected table: {name}")
    )
    connected_paths: list[str] = []
    fake_lancedb = SimpleNamespace(connect=lambda path: connected_paths.append(path) or database)
    monkeypatch.setitem(sys.modules, "lancedb", fake_lancedb)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "-c",
            str(paths.data_dir / "archive.lancedb"),
            str(paths.database_path),
            "25",
            "75",
        ],
    )

    with pytest.raises(SystemExit) as exit_info:
        exec(compile(migrate.WORKER_CODE, "<migration-worker>", "exec"), {"__name__": "__main__"})

    assert exit_info.value.code == 0
    assert connected_paths == [str(paths.data_dir / "archive.lancedb")]
    connection = sqlite3.connect(paths.database_path)
    try:
        rows = connection.execute(
            """
            SELECT row_key, text
            FROM archive
            WHERE row_key IN ('tweet_object:1', 'tweet_object:2')
            ORDER BY row_key
            """
        ).fetchall()
    finally:
        connection.close()
    assert rows == [
        ("tweet_object:1", "newer SQLite value"),
        ("tweet_object:2", "migrated value"),
    ]


def test_worker_code_empty_batch_exits_without_opening_destination(
    monkeypatch: pytest.MonkeyPatch,
    paths,
) -> None:
    search = SimpleNamespace(
        limit=lambda value: SimpleNamespace(
            offset=lambda offset: SimpleNamespace(to_list=lambda: [])
        )
    )
    fake_lancedb = SimpleNamespace(
        connect=lambda path: SimpleNamespace(
            open_table=lambda name: SimpleNamespace(search=lambda: search)
        )
    )
    monkeypatch.setitem(sys.modules, "lancedb", fake_lancedb)
    database_path = paths.data_dir / "must-not-be-created.sqlite"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "-c",
            str(paths.data_dir / "archive.lancedb"),
            str(database_path),
            "5000",
            "0",
        ],
    )

    with pytest.raises(SystemExit) as exit_info:
        exec(compile(migrate.WORKER_CODE, "<migration-worker>", "exec"), {"__name__": "__main__"})

    assert exit_info.value.code == migrate.WORKER_END_OF_TABLE
    assert not database_path.exists()


def test_worker_code_classifies_lancedb_read_failure(
    monkeypatch: pytest.MonkeyPatch,
    paths,
) -> None:
    fake_lancedb = SimpleNamespace(
        connect=lambda path: (_ for _ in ()).throw(RuntimeError("broken fragment"))
    )
    monkeypatch.setitem(sys.modules, "lancedb", fake_lancedb)
    monkeypatch.setattr(
        sys,
        "argv",
        ["-c", str(paths.data_dir / "archive.lancedb"), str(paths.database_path), "8", "2"],
    )

    with pytest.raises(SystemExit) as exit_info:
        exec(compile(migrate.WORKER_CODE, "<migration-worker>", "exec"), {"__name__": "__main__"})

    assert exit_info.value.code == migrate.WORKER_SOURCE_READ_FAILED


def test_worker_code_classifies_sqlite_write_failure(
    monkeypatch: pytest.MonkeyPatch,
    paths,
) -> None:
    rows = [{"row_key": "tweet_object:1", "record_type": "tweet_object"}]
    search = SimpleNamespace(
        limit=lambda value: SimpleNamespace(
            offset=lambda offset: SimpleNamespace(to_list=lambda: rows)
        )
    )
    fake_lancedb = SimpleNamespace(
        connect=lambda path: SimpleNamespace(
            open_table=lambda name: SimpleNamespace(search=lambda: search)
        )
    )
    fake_sqlite = SimpleNamespace(
        Row=object,
        connect=lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    monkeypatch.setitem(sys.modules, "lancedb", fake_lancedb)
    monkeypatch.setitem(sys.modules, "sqlite3", fake_sqlite)
    monkeypatch.setattr(
        sys,
        "argv",
        ["-c", str(paths.data_dir / "archive.lancedb"), str(paths.database_path), "8", "2"],
    )

    with pytest.raises(SystemExit) as exit_info:
        exec(compile(migrate.WORKER_CODE, "<migration-worker>", "exec"), {"__name__": "__main__"})

    assert exit_info.value.code == migrate.WORKER_DESTINATION_WRITE_FAILED


def test_module_does_not_require_lancedb_until_migration_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_import(name: str) -> None:
        assert name == "lancedb"
        raise ImportError("not installed")

    monkeypatch.setattr(migrate.importlib, "import_module", missing_import)

    assert migrate._import_lancedb() is None
    compile(migrate.WORKER_CODE, "<migration-worker>", "exec")


def test_missing_legacy_archive_returns_without_loading_lancedb(
    monkeypatch: pytest.MonkeyPatch,
    paths,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(migrate, "load_config", lambda: (AppConfig(), paths))
    monkeypatch.setattr(
        migrate,
        "_import_lancedb",
        lambda: pytest.fail("legacy dependency loaded without a source archive"),
    )

    result = migrate.run_migration()

    assert result.status == "source_missing"
    assert "No old LanceDB archive found" in capsys.readouterr().out


def test_migration_uses_pipeline_and_resolves_all_steps_when_source_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    paths,
) -> None:
    monkeypatch.setattr(migrate, "load_config", lambda: (AppConfig(), paths))
    output = StringIO()
    reporter = PipelineReporter(
        Console(file=output, force_terminal=False, color_system=None),
        "tweetnook migrate",
        interactive=False,
    )

    with reporter:
        result = migrate.run_migration()

    assert result.status == "source_missing"
    assert [step.key for step in reporter.steps] == [
        "migration-inspect",
        "migration-copy",
        "migration-index",
    ]
    assert all(step.state == "skipped" for step in reporter.steps)
    assert "No legacy LanceDB archive found" in reporter._final_summary


def test_missing_optional_dependency_returns_result_instead_of_exiting(
    monkeypatch: pytest.MonkeyPatch,
    paths,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = AppConfig()
    (paths.data_dir / "archive.lancedb").mkdir()
    monkeypatch.setattr(migrate, "load_config", lambda: (config, paths))
    monkeypatch.setattr(migrate, "_import_lancedb", lambda: None)
    monkeypatch.setattr(
        migrate,
        "open_archive_store",
        lambda *args, **kwargs: pytest.fail("destination opened without lancedb"),
    )

    result = migrate.run_migration()

    assert result.status == "dependency_missing"
    output = capsys.readouterr().out
    assert "legacy-migration" in output
    assert "tweetnook[legacy-migration]" in output


@pytest.mark.parametrize(
    "legacy",
    [
        FakeLanceDB(connect_error=RuntimeError("cannot connect")),
        FakeLanceDB(
            FakeLegacyDatabase(open_error=KeyError("archive")),
        ),
    ],
)
def test_missing_or_unreadable_archive_table_is_nonfatal(
    monkeypatch: pytest.MonkeyPatch,
    paths,
    capsys: pytest.CaptureFixture[str],
    legacy: FakeLanceDB,
) -> None:
    (paths.data_dir / "archive.lancedb").mkdir()
    monkeypatch.setattr(migrate, "load_config", lambda: (AppConfig(), paths))
    monkeypatch.setattr(migrate, "_import_lancedb", lambda: legacy)
    monkeypatch.setattr(
        migrate,
        "open_archive_store",
        lambda *args, **kwargs: pytest.fail("destination opened without a legacy table"),
    )

    result = migrate.run_migration()

    assert result.status == "table_missing"
    assert "not found or unreadable" in capsys.readouterr().out


def test_destination_schema_is_created_and_closed_before_first_worker(
    monkeypatch: pytest.MonkeyPatch,
    paths,
) -> None:
    config, _, _ = install_legacy_source(monkeypatch, paths, total_rows=1)
    events: list[object] = []
    store_calls, stores = install_fake_stores(monkeypatch, events=events)
    worker_calls = install_worker_sequence(
        monkeypatch,
        [0, migrate.WORKER_END_OF_TABLE],
        events=events,
    )
    monkeypatch.setattr(migrate, "_create_progress", lambda total: None)

    result = migrate.run_migration()

    assert result.status == "complete"
    assert events[:3] == [
        ("open", "schema", True),
        ("close", "schema"),
        ("worker", 0),
    ]
    assert store_calls[0] == (True, paths, config)
    assert stores[0].closed is True
    assert worker_calls[0][1] == paths.database_path


@pytest.mark.parametrize(
    ("total_rows", "return_codes", "expected_offsets", "expected_updates"),
    [
        (3, [0, migrate.WORKER_END_OF_TABLE], [0, 5000], [3]),
        (
            12_000,
            [0, 0, 0, migrate.WORKER_END_OF_TABLE],
            [0, 5000, 10_000, 15_000],
            [5000, 5000, 2000],
        ),
    ],
)
def test_single_and_multi_batch_offsets_and_progress(
    monkeypatch: pytest.MonkeyPatch,
    paths,
    total_rows: int,
    return_codes: list[int],
    expected_offsets: list[int],
    expected_updates: list[int],
) -> None:
    install_legacy_source(monkeypatch, paths, total_rows=total_rows)
    events: list[object] = []
    install_fake_stores(monkeypatch, events=events)
    worker_calls = install_worker_sequence(monkeypatch, return_codes)
    progress = FakeProgress(total_rows)
    monkeypatch.setattr(migrate, "_create_progress", lambda total: progress)

    result = migrate.run_migration()

    assert result.status == "complete"
    assert result.total_rows == total_rows
    assert result.migrated_rows == total_rows
    assert result.skipped_rows == 0
    assert result.final_offset == total_rows
    assert [call[3] for call in worker_calls] == expected_offsets
    assert progress.updates == expected_updates
    assert progress.n == total_rows
    assert progress.closed is True


def test_worker_end_code_stops_immediately_without_advancing_progress(
    monkeypatch: pytest.MonkeyPatch,
    paths,
) -> None:
    install_legacy_source(monkeypatch, paths, total_rows=0)
    install_fake_stores(monkeypatch, events=[])
    worker_calls = install_worker_sequence(
        monkeypatch,
        [migrate.WORKER_END_OF_TABLE],
    )
    progress = FakeProgress(0)
    monkeypatch.setattr(migrate, "_create_progress", lambda total: progress)

    result = migrate.run_migration()

    assert result.worker_calls == 1
    assert result.final_offset == 0
    assert result.migrated_rows == 0
    assert worker_calls[0][3] == 0
    assert progress.updates == []
    assert progress.closed is True


def test_corrupted_chunk_is_narrowed_to_one_unreadable_row(
    monkeypatch: pytest.MonkeyPatch,
    paths,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_legacy_source(monkeypatch, paths, total_rows=4)
    install_fake_stores(monkeypatch, events=[])
    worker_calls = install_worker_sequence(
        monkeypatch,
        [
            migrate.WORKER_SOURCE_READ_FAILED,
            0,
            migrate.WORKER_SOURCE_READ_FAILED,
            migrate.WORKER_SOURCE_READ_FAILED,
            0,
            migrate.WORKER_END_OF_TABLE,
        ],
    )
    progress = FakeProgress(4)
    monkeypatch.setattr(migrate, "_create_progress", lambda total: progress)

    result = migrate.run_migration(batch_size=4)

    assert result.status == "partial"
    assert result.skipped_rows == 1
    assert result.migrated_rows == 3
    assert [(call[3], call[2]) for call in worker_calls] == [
        (0, 4),
        (0, 2),
        (2, 2),
        (2, 1),
        (3, 1),
        (4, 4),
    ]
    assert progress.updates == [2, 1, 1]
    assert progress.n == 4
    assert any("Corrupted row at offset 2" in message for message in progress.messages)
    output = capsys.readouterr().out
    assert "Migration partially complete: 3 rows copied and 1 unreadable row skipped" in output
    assert "Keep the original" in output


def test_native_worker_crash_is_recovered_by_splitting(
    monkeypatch: pytest.MonkeyPatch,
    paths,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_legacy_source(monkeypatch, paths, total_rows=2)
    install_fake_stores(monkeypatch, events=[])
    worker_calls = install_worker_sequence(
        monkeypatch,
        [-11, 0, 0, migrate.WORKER_END_OF_TABLE],
    )
    monkeypatch.setattr(migrate, "_create_progress", lambda total: None)

    result = migrate.run_migration(batch_size=2)

    assert result.status == "complete"
    assert result.skipped_rows == 0
    assert result.migrated_rows == 2
    assert [(call[3], call[2]) for call in worker_calls] == [
        (0, 2),
        (0, 1),
        (1, 1),
        (2, 2),
    ]
    assert "retrying smaller ranges" in capsys.readouterr().out


def test_destination_worker_failure_aborts_without_splitting(
    monkeypatch: pytest.MonkeyPatch,
    paths,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_legacy_source(monkeypatch, paths, total_rows=5000)
    install_fake_stores(monkeypatch, events=[])
    worker_calls = install_worker_sequence(
        monkeypatch,
        [migrate.WORKER_DESTINATION_WRITE_FAILED],
    )
    monkeypatch.setattr(migrate, "_create_progress", lambda total: None)

    result = migrate.run_migration()

    assert result.status == "aborted"
    assert result.worker_calls == 1
    assert len(worker_calls) == 1
    assert result.final_offset == 0
    assert result.skipped_rows == 0
    output = capsys.readouterr().out
    assert f"exit code {migrate.WORKER_DESTINATION_WRITE_FAILED}" in output
    assert "Migration stopped before all readable chunks were processed" in output
    assert "Migration complete!" not in output


def test_worker_launch_exception_aborts_without_skipping_rows(
    monkeypatch: pytest.MonkeyPatch,
    paths,
) -> None:
    install_legacy_source(monkeypatch, paths, total_rows=5000)
    install_fake_stores(monkeypatch, events=[])

    def failed_worker(*args: Any, **kwargs: Any) -> SimpleNamespace:
        raise OSError("cannot spawn")

    monkeypatch.setattr(migrate, "_run_worker", failed_worker)
    progress = FakeProgress(5000)
    monkeypatch.setattr(migrate, "_create_progress", lambda total: progress)

    result = migrate.run_migration()

    assert result.status == "aborted"
    assert result.skipped_rows == 0
    assert result.worker_calls == 1
    assert len(progress.messages) == 1
    assert "cannot spawn" in progress.messages[0]


def test_fts_rebuild_commits_and_closes_destination(
    monkeypatch: pytest.MonkeyPatch,
    paths,
) -> None:
    install_legacy_source(monkeypatch, paths, total_rows=0)
    events: list[object] = []
    final_connection = FakeConnection()
    store_calls, stores = install_fake_stores(
        monkeypatch,
        events=events,
        final_connection=final_connection,
    )
    install_worker_sequence(monkeypatch, [migrate.WORKER_END_OF_TABLE])
    monkeypatch.setattr(migrate, "_create_progress", lambda total: None)

    result = migrate.run_migration()

    assert result.fts_rebuilt is True
    assert final_connection.executed == ["INSERT INTO archive_fts(archive_fts) VALUES('rebuild')"]
    assert final_connection.commits == 1
    assert store_calls[-1][0] is True
    assert stores[-1].closed is True


def test_fts_rebuild_failure_warns_and_still_closes_store(
    monkeypatch: pytest.MonkeyPatch,
    paths,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_legacy_source(monkeypatch, paths, total_rows=0)
    events: list[object] = []
    final_connection = FakeConnection(rebuild_error=sqlite3.OperationalError("no fts5"))
    _, stores = install_fake_stores(
        monkeypatch,
        events=events,
        final_connection=final_connection,
    )
    install_worker_sequence(monkeypatch, [migrate.WORKER_END_OF_TABLE])
    monkeypatch.setattr(migrate, "_create_progress", lambda total: None)

    result = migrate.run_migration()

    assert result.status == "complete"
    assert result.fts_rebuilt is False
    assert stores[-1].closed is True
    assert "Failed to rebuild FTS index: no fts5" in capsys.readouterr().out


@pytest.mark.parametrize("open_behavior", ["none", "error"])
def test_destination_initialization_failure_prevents_workers(
    monkeypatch: pytest.MonkeyPatch,
    paths,
    capsys: pytest.CaptureFixture[str],
    open_behavior: str,
) -> None:
    install_legacy_source(monkeypatch, paths, total_rows=10)

    def failed_open(*args: Any, **kwargs: Any) -> None:
        if open_behavior == "error":
            raise sqlite3.OperationalError("disk full")
        return None

    monkeypatch.setattr(migrate, "open_archive_store", failed_open)
    monkeypatch.setattr(
        migrate,
        "_run_worker",
        lambda *args, **kwargs: pytest.fail("worker started without destination schema"),
    )

    result = migrate.run_migration()

    assert result.status == "destination_failed"
    assert "Failed to" in capsys.readouterr().out


def test_lancedb_migration_creates_latest_schema_without_legacy_upgrade(
    monkeypatch: pytest.MonkeyPatch,
    paths,
) -> None:
    install_legacy_source(monkeypatch, paths, total_rows=0)
    install_worker_sequence(monkeypatch, [migrate.WORKER_END_OF_TABLE])
    monkeypatch.setattr(migrate, "_create_progress", lambda _total: None)

    def unexpected(*_args, **_kwargs):
        pytest.fail("new LanceDB destination entered the legacy SQLite migration path")

    monkeypatch.setattr(ArchiveStore, "_migrate_legacy_database", unexpected)
    monkeypatch.setattr(ArchiveStore, "_backup_before_migration", unexpected)

    result = migrate.run_migration()

    assert result.status == "complete"
    assert result.fts_rebuilt is True
    connection = sqlite3.connect(paths.database_path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    finally:
        connection.close()
    assert list(paths.data_dir.glob("archive.db.pre-schema-v*.bak")) == []


def test_realistic_rerun_preserves_existing_rows_and_rebuilds_tweet_only_search(
    monkeypatch: pytest.MonkeyPatch,
    paths,
) -> None:
    config, _, _ = install_legacy_source(monkeypatch, paths, total_rows=2)
    monkeypatch.setattr(migrate, "_create_progress", lambda total: None)

    existing_store = open_archive_store(paths, create=True, config=config)
    assert existing_store is not None
    existing_store.conn.execute(
        """
        INSERT INTO archive (row_key, record_type, tweet_id, text, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            "tweet_object:1",
            "tweet_object",
            "1",
            "preserve newer SQLite text",
            "Sat Mar 14 00:00:00 +0000 2026",
        ),
    )
    existing_store.conn.commit()
    existing_store.close()

    offsets: list[int] = []

    def sqlite_worker(
        lance_path: Path,
        database_path: Path,
        batch_size: int,
        offset: int,
    ) -> SimpleNamespace:
        offsets.append(offset)
        if offset:
            return worker_result(migrate.WORKER_END_OF_TABLE)
        connection = sqlite3.connect(database_path)
        with connection:
            connection.executemany(
                """
                INSERT OR IGNORE INTO archive
                    (row_key, record_type, tweet_id, text, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        "tweet_object:1",
                        "tweet_object",
                        "1",
                        "legacy text must not overwrite",
                        "Sat Mar 14 00:00:00 +0000 2026",
                    ),
                    (
                        "tweet_object:2",
                        "tweet_object",
                        "2",
                        "newly migrated searchable phrase",
                        "Sun Mar 15 00:00:00 +0000 2026",
                    ),
                ],
            )
        connection.close()
        return worker_result(0)

    monkeypatch.setattr(migrate, "_run_worker", sqlite_worker)

    first = migrate.run_migration()
    second = migrate.run_migration()

    assert first.status == second.status == "complete"
    assert first.fts_rebuilt is second.fts_rebuilt is True
    assert offsets == [0, 5000, 0, 5000]
    assert "INSERT OR IGNORE INTO archive" in migrate.WORKER_CODE

    final_store = open_archive_store(paths, create=False, config=config)
    assert final_store is not None
    try:
        rows = final_store.conn.execute(
            """
            SELECT row_key, text, created_at_ts
            FROM archive
            WHERE row_key IN ('tweet_object:1', 'tweet_object:2')
            ORDER BY row_key
            """
        ).fetchall()
        assert [(row["row_key"], row["text"]) for row in rows] == [
            ("tweet_object:1", "preserve newer SQLite text"),
            ("tweet_object:2", "newly migrated searchable phrase"),
        ]
        assert all(row["created_at_ts"] is not None for row in rows)
        fts_matches = final_store.conn.execute(
            """
            SELECT archive.row_key
            FROM archive_fts
            JOIN archive ON archive.rowid = archive_fts.rowid
            WHERE archive_fts MATCH 'searchable'
            """
        ).fetchall()
        assert [row["row_key"] for row in fts_matches] == []
    finally:
        final_store.close()
