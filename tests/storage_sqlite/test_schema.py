from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import tweetnook.storage.backend as storage_backend
from tests.storage_sqlite.conftest import CREATED_2012, _membership
from tweetnook.storage import open_archive_store
from tweetnook.storage.backend import ArchiveStore
from tweetnook.storage.schema import ARCHIVE_COLUMNS, ENRICHMENT_INDEXES, SCHEMA_VERSION


def test_schema_creates_fts_triggers_and_page_indexes(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    objects = {
        (row["type"], row["name"])
        for row in store.conn.execute(
            "SELECT type, name FROM sqlite_master WHERE name LIKE 'archive%' "
            "OR name LIKE 'idx_archive%'"
        )
    }

    assert ("table", "archive") in objects
    assert ("table", "archive_fts") in objects
    fts_sql = store.conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'archive_fts'"
    ).fetchone()[0]
    assert "content=''" in fts_sql
    assert {
        ("trigger", "archive_ai"),
        ("trigger", "archive_au"),
        ("trigger", "archive_ad"),
        ("table", "archive_description_fts"),
        ("trigger", "archive_description_ai"),
        ("trigger", "archive_description_au"),
        ("trigger", "archive_description_ad"),
    } <= objects
    assert {
        ("index", "idx_archive_record_page"),
        ("index", "idx_archive_record_collection_page"),
        ("index", "idx_archive_tweet_id"),
        ("index", "idx_archive_media_tag_lookup"),
        ("index", "idx_archive_search_attachment"),
    } <= objects
    tag_index_sql = store.conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'index' "
        "AND name = 'idx_archive_media_tag_lookup'"
    ).fetchone()[0]
    assert "ON archive(tweet_id, raw_json)" in tag_index_sql
    assert "WHERE record_type = 'media_tag'" in tag_index_sql
    store.close()


def test_archive_queries_can_force_safe_id_indexes(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store._merge_records(
        [
            _membership(
                store,
                "1",
                collection="bookmark",
                text="indexed lookup",
                created_at=CREATED_2012,
                created_at_ts=1,
                sort_index="1",
            )
        ]
    )
    statements: list[str] = []
    store.conn.set_trace_callback(statements.append)

    rows = store._rows_for_values("tweet", "tweet_id", ["1"], columns=["tweet_id"])

    store.conn.set_trace_callback(None)
    assert rows == [{"tweet_id": "1"}]
    assert any("INDEXED BY idx_archive_tweet_id" in statement for statement in statements)
    with pytest.raises(ValueError, match="Unsupported archive query index"):
        store._query("tweet_id = '1'", indexed_by="idx_archive_record_page")
    store.close()


def test_fts_triggers_follow_insert_update_and_delete(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    row = _membership(
        store,
        "1",
        collection="bookmark",
        text="initial searchable phrase",
        created_at=CREATED_2012,
        created_at_ts=1,
        sort_index="1",
    )

    store._merge_records([row])
    assert [result["tweet_id"] for result in store.search_fts("initial")] == ["1"]

    store._merge_records(
        [
            store._record(
                row_key="tweet_object:2",
                record_type="tweet_object",
                tweet_id="2",
                text="secondary object must not be searchable",
            )
        ]
    )
    assert store.search_fts("secondary") == []
    assert store.conn.execute("SELECT COUNT(*) FROM archive_fts").fetchone()[0] == 1

    row["text"] = "replacement searchable phrase"
    original_rowid = store.conn.execute(
        "SELECT rowid FROM archive WHERE row_key = ?", (row["row_key"],)
    ).fetchone()[0]
    store._merge_records([row])
    assert store.search_fts("initial") == []
    assert [result["tweet_id"] for result in store.search_fts("replacement")] == ["1"]
    assert store.conn.execute("SELECT COUNT(*) FROM archive_fts").fetchone()[0] == 1
    assert (
        store.conn.execute(
            "SELECT rowid FROM archive WHERE row_key = ?", (row["row_key"],)
        ).fetchone()[0]
        == original_rowid
    )

    # Updating capture metadata must not rewrite unchanged indexed text.
    statements = []
    store.conn.set_trace_callback(statements.append)
    row["synced_at"] = "2026-09-08"
    store._merge_records([row])
    store.conn.set_trace_callback(None)
    assert not any("archive_fts_data" in sql or "archive_fts_docsize" in sql for sql in statements)

    store._delete("row_key = 'tweet:bookmark::1'")
    assert store.search_fts("replacement") == []
    assert store.conn.execute("SELECT COUNT(*) FROM archive_fts").fetchone()[0] == 0
    store.close()


def test_schema_v5_repairs_orphaned_fts_documents_atomically(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "archive.db"
    initial = ArchiveStore(db_path, create=True)
    row = initial._record(
        row_key="tweet:like::1",
        record_type="tweet",
        tweet_id="1",
        collection_type="like",
        text="oldword",
    )
    initial._merge_records([row])
    initial.conn.execute("PRAGMA recursive_triggers=OFF")
    with initial.conn:
        initial.conn.execute(
            "INSERT OR REPLACE INTO archive "
            "(row_key,record_type,tweet_id,collection_type,text) "
            "VALUES ('tweet:like::1','tweet','1','like','newword')"
        )
        initial.conn.execute("PRAGMA user_version=5")
    assert initial.conn.execute("SELECT COUNT(*) FROM archive_fts").fetchone()[0] == 2
    initial.close()

    original = ArchiveStore._create_archive_indexes

    def fail(_store):
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr(ArchiveStore, "_create_archive_indexes", fail)
    with pytest.raises(RuntimeError, match="injected"):
        ArchiveStore(db_path, create=False)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
        assert conn.execute("SELECT COUNT(*) FROM archive_fts").fetchone()[0] == 2
    monkeypatch.setattr(ArchiveStore, "_create_archive_indexes", original)
    repaired = ArchiveStore(db_path, create=False)
    assert repaired.migration_report.from_version == 5
    assert repaired.conn.execute("SELECT COUNT(*) FROM archive_fts").fetchone()[0] == 1
    assert (
        repaired.conn.execute(
            "SELECT COUNT(*) FROM archive_fts WHERE archive_fts MATCH 'oldword'"
        ).fetchone()[0]
        == 0
    )
    assert (
        repaired.conn.execute(
            "SELECT COUNT(*) FROM archive_fts WHERE archive_fts MATCH 'newword'"
        ).fetchone()[0]
        == 1
    )
    repaired.close()


def test_enrichment_state_update_does_not_read_or_replace_content(paths) -> None:
    store = open_archive_store(paths, create=True)
    store._merge_records(
        [
            store._record(
                row_key="tweet_object:1",
                record_type="tweet_object",
                tweet_id="1",
                text="preserved",
                raw_json='{"payload":"preserved"}',
            )
        ]
    )
    statements = []
    store.conn.set_trace_callback(statements.append)
    store.update_tweet_object_enrichment(
        "1",
        enrichment_state="done",
        enrichment_checked_at="2026-09-08",
        enrichment_http_status=200,
        enrichment_reason=None,
    )
    store.conn.set_trace_callback(None)
    assert not any(
        sql.startswith("SELECT") or sql.startswith("INSERT INTO archive ") for sql in statements
    )
    row = store._get_row("tweet_object:1")
    assert row["raw_json"] == '{"payload":"preserved"}'
    assert row["text"] == "preserved"
    assert row["enrichment_state"] == "done"
    store.close()


def test_current_schema_open_skips_checks_repairs_and_schema_maintenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "archive.db"
    initial = ArchiveStore(db_path, create=True)
    initial.close()

    def unexpected(*_args, **_kwargs):
        pytest.fail("current-schema open performed migration maintenance")

    for method_name in (
        "_archive_table_exists",
        "_backfill_created_at_timestamps",
        "_create_fts_schema",
        "_create_archive_indexes",
        "_migrate_search_index",
    ):
        monkeypatch.setattr(ArchiveStore, method_name, unexpected)

    current = ArchiveStore(db_path, create=True)

    assert current.migration_report is None
    assert current.conn.total_changes == 0
    current.close()


def test_current_schema_lock_probe_reads_only_user_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "archive.db"
    initial = ArchiveStore(db_path, create=True)
    initial.close()
    statements: list[str] = []
    original_connect = sqlite3.connect

    def tracing_connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(storage_backend.sqlite3, "connect", tracing_connect)

    assert storage_backend._database_requires_schema_migration(db_path) is False
    assert statements == ["PRAGMA user_version"]


def test_schema_v5_upgrade_adds_later_indexes_and_reports_migration(tmp_path: Path) -> None:
    db_path = tmp_path / "archive.db"
    initial = ArchiveStore(db_path, create=True)
    initial._merge_records(
        [
            _membership(
                initial,
                "1",
                collection="bookmark",
                text="search index sentinel",
                created_at=CREATED_2012,
                created_at_ts=1,
                sort_index="1",
            ),
        ]
    )
    # Indexes added after the schema-5 releases (0.0.9 and 0.1.0).
    later_indexes = (
        "idx_archive_membership",
        "idx_archive_profile_author",
        "idx_archive_search_author",
    )
    for index in later_indexes:
        initial.conn.execute(f"DROP INDEX {index}")
    initial.conn.execute("PRAGMA user_version = 5")
    initial.conn.commit()
    initial.close()

    migrated = ArchiveStore(db_path, create=True)

    assert migrated.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert migrated.migration_report is not None
    assert migrated.migration_report.from_version == 5
    assert migrated.migration_report.to_version == SCHEMA_VERSION
    assert [row["tweet_id"] for row in migrated.search_fts("sentinel")] == ["1"]
    indexes = {
        row["name"]
        for row in migrated.conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
    }
    assert set(later_indexes) <= indexes
    migrated.close()


@pytest.mark.parametrize("version", [2, 4, 6, 7])
def test_pre_release_schema_is_rejected_without_changes(tmp_path: Path, version: int) -> None:
    db_path = tmp_path / "archive.db"
    initial = ArchiveStore(db_path, create=True)
    initial.conn.execute(f"PRAGMA user_version = {version}")
    initial.conn.commit()
    initial.close()

    with pytest.raises(RuntimeError, match="pre-release build"):
        ArchiveStore(db_path, create=True)

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == version


def test_new_database_creates_latest_schema_without_migration_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "archive.db"

    def unexpected(*_args, **_kwargs):
        pytest.fail("new database entered the migration path")

    monkeypatch.setattr(ArchiveStore, "_migrate_search_index", unexpected)

    store = ArchiveStore(db_path, create=True)

    assert store.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert set(ARCHIVE_COLUMNS) == {
        row["name"] for row in store.conn.execute("PRAGMA table_info(archive)")
    }
    assert (
        store.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'archive_fts'"
        ).fetchone()
        is not None
    )
    assert set(ENRICHMENT_INDEXES).issubset(
        {
            row["name"]
            for row in store.conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
        }
    )
    store.close()


def test_explicit_integrity_checks_select_quick_or_full_pragma(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    statements: list[str] = []
    store.conn.set_trace_callback(statements.append)

    assert store.check_integrity() == ["ok"]
    assert store.check_integrity(full=True) == ["ok"]

    store.conn.set_trace_callback(None)
    assert "PRAGMA quick_check" in statements
    assert "PRAGMA integrity_check" in statements
    store.close()
