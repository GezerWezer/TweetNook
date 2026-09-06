from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import tweetnook.storage.backend as storage_backend
from tests.conftest import make_tweet_detail_response, make_tweet_result
from tweetnook.config import AppConfig, DatabaseConfig
from tweetnook.storage import open_archive_store
from tweetnook.storage.backend import (
    ARCHIVE_COLUMNS,
    COLUMN_TYPES,
    ENRICHMENT_INDEXES,
    SCHEMA_VERSION,
    ArchiveStore,
)

CREATED_2012 = "Tue Oct 09 21:39:26 +0000 2012"
CREATED_2024 = "Thu Apr 11 03:55:13 +0000 2024"
CREATED_2026 = "Sat Mar 14 00:00:00 +0000 2026"


def _membership(
    store: ArchiveStore,
    tweet_id: str,
    *,
    collection: str,
    text: str,
    created_at: str,
    created_at_ts: int,
    sort_index: str,
    folder_id: str | None = None,
    author_username: str = "alice",
    author_display_name: str = "Alice Example",
) -> dict[str, object]:
    return store._record(
        row_key=f"tweet:{collection}:{folder_id or ''}:{tweet_id}",
        record_type="tweet",
        tweet_id=tweet_id,
        collection_type=collection,
        folder_id=folder_id or "",
        text=text,
        author_id=f"author-{tweet_id}",
        author_username=author_username,
        author_display_name=author_display_name,
        created_at=created_at,
        created_at_ts=created_at_ts,
        sort_index=sort_index,
        raw_json=f'{{"id":"{tweet_id}"}}',
    )


def _seed_search_rows(store: ArchiveStore) -> None:
    rows = [
        _membership(
            store,
            "1",
            collection="bookmark",
            text="alpha beta hello-world",
            created_at=CREATED_2012,
            created_at_ts=1,
            sort_index="10",
        ),
        _membership(
            store,
            "1",
            collection="like",
            text="alpha beta hello-world",
            created_at=CREATED_2012,
            created_at_ts=1,
            sort_index="9",
        ),
        _membership(
            store,
            "2",
            collection="like",
            text="alpha gamma",
            created_at=CREATED_2024,
            created_at_ts=2,
            sort_index="20",
            author_username="bob",
            author_display_name="Bob Builder",
        ),
        _membership(
            store,
            "3",
            collection="bookmark",
            text="article membership",
            created_at=CREATED_2026,
            created_at_ts=3,
            sort_index="30",
        ),
        store._record(
            row_key="article:3",
            record_type="article",
            tweet_id="3",
            title="Alpha Analysis",
            summary_text="A focused beta report",
            content_text="Long form article content",
        ),
    ]
    store._merge_records(rows)


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
    with pytest.raises(ValueError, match="cannot be combined with FTS"):
        store._query(
            "record_type = 'tweet'",
            is_fts=True,
            query="indexed",
            indexed_by="idx_archive_tweet_id",
        )
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
    store._merge_records([row])
    assert store.search_fts("initial") == []
    assert [result["tweet_id"] for result in store.search_fts("replacement")] == ["1"]

    store._delete("row_key = 'tweet:bookmark::1'")
    assert store.search_fts("replacement") == []
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
        "_archive_column_names",
        "_require_quick_check",
        "_backup_before_migration",
        "_backfill_created_at_timestamps",
        "_create_fts_schema",
        "_create_archive_indexes",
        "_repair_legacy_terminal_rows",
    ):
        monkeypatch.setattr(ArchiveStore, method_name, unexpected)

    current = ArchiveStore(db_path, create=True)

    assert current.migration_report is None
    assert current.conn.total_changes == 0
    assert list(tmp_path.glob("*.bak")) == []
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


def test_schema_v3_rebuilds_only_derived_search_index_without_backup(tmp_path: Path) -> None:
    db_path = tmp_path / "archive.db"
    initial = ArchiveStore(db_path, create=True)
    initial._merge_records(
        [
            _membership(
                initial,
                "1",
                collection="bookmark",
                text="searchable membership sentinel",
                created_at=CREATED_2012,
                created_at_ts=1,
                sort_index="1",
            ),
            initial._record(
                row_key="tweet_object:2",
                record_type="tweet_object",
                tweet_id="2",
                text="legacy secondary sentinel",
            ),
        ]
    )
    initial.close()

    legacy = sqlite3.connect(db_path)
    for trigger in ("archive_ad", "archive_ai", "archive_au"):
        legacy.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    legacy.execute("DROP TABLE archive_fts")
    legacy.execute("""
        CREATE VIRTUAL TABLE archive_fts USING fts5(
            author_username, author_display_name, text, note_tweet_text,
            content='archive', content_rowid='rowid'
        )
    """)
    legacy.execute("INSERT INTO archive_fts(archive_fts) VALUES('rebuild')")
    legacy.execute("PRAGMA user_version = 3")
    legacy.commit()
    assert (
        legacy.execute(
            "SELECT COUNT(*) FROM archive_fts WHERE archive_fts MATCH 'secondary'"
        ).fetchone()[0]
        == 1
    )
    legacy.close()

    migrated = ArchiveStore(db_path, create=True)

    assert migrated.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert migrated.migration_report is not None
    assert migrated.migration_report.from_version == 3
    assert migrated.migration_report.to_version == SCHEMA_VERSION
    assert migrated.migration_report.backup_path is None
    assert migrated.migration_report.search_index_rebuilt is True
    assert [row["tweet_id"] for row in migrated.search_fts("membership")] == ["1"]
    assert migrated.search_fts("secondary") == []
    assert migrated.conn.execute("SELECT COUNT(*) FROM archive_fts").fetchone()[0] == 1
    assert list(tmp_path.glob("*.bak")) == []
    migrated.close()


def test_schema_v4_adds_only_tag_search_index_without_backup(tmp_path: Path) -> None:
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
            initial._record(
                row_key="media_tag:1",
                record_type="media_tag",
                tweet_id="1",
                raw_json=json.dumps({"tags": ["Nature"]}),
            ),
        ]
    )
    initial.conn.execute("DROP INDEX idx_archive_media_tag_lookup")
    initial.conn.execute("PRAGMA user_version = 4")
    initial.conn.commit()
    initial.close()

    migrated = ArchiveStore(db_path, create=True)

    assert migrated.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert migrated.migration_report is not None
    assert migrated.migration_report.from_version == 4
    assert migrated.migration_report.to_version == SCHEMA_VERSION
    assert migrated.migration_report.backup_path is None
    assert migrated.migration_report.search_index_rebuilt is False
    assert [row["tweet_id"] for row in migrated.search_fts("sentinel")] == ["1"]
    assert (
        migrated.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' "
            "AND name = 'idx_archive_media_tag_lookup'"
        ).fetchone()
        is not None
    )
    assert list(tmp_path.glob("*.bak")) == []
    migrated.close()


def test_new_database_creates_latest_schema_without_migration_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "archive.db"

    def unexpected(*_args, **_kwargs):
        pytest.fail("new database entered the legacy migration path")

    monkeypatch.setattr(ArchiveStore, "_migrate_legacy_database", unexpected)
    monkeypatch.setattr(ArchiveStore, "_require_quick_check", unexpected)
    monkeypatch.setattr(ArchiveStore, "_backup_before_migration", unexpected)
    monkeypatch.setattr(ArchiveStore, "_repair_legacy_terminal_rows", unexpected)

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
    assert list(tmp_path.glob("*.bak")) == []
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


def test_created_at_ts_migration_backfills_and_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "archive.db"
    columns = []
    for name in ARCHIVE_COLUMNS:
        if name == "created_at_ts":
            continue
        columns.append(f"{name} {'TEXT PRIMARY KEY' if name == 'row_key' else 'TEXT'}")
    connection = sqlite3.connect(db_path)
    connection.execute(f"CREATE TABLE archive ({', '.join(columns)})")
    connection.execute(
        "INSERT INTO archive(row_key, record_type, tweet_id, created_at) VALUES (?, ?, ?, ?)",
        ("tweet:bookmark::1", "tweet", "1", CREATED_2012),
    )
    connection.commit()
    connection.close()

    first = ArchiveStore(db_path, create=True)
    first_value = first.conn.execute(
        "SELECT created_at_ts FROM archive WHERE tweet_id = '1'"
    ).fetchone()[0]
    first.close()
    second = ArchiveStore(db_path, create=True)
    second_value = second.conn.execute(
        "SELECT created_at_ts FROM archive WHERE tweet_id = '1'"
    ).fetchone()[0]
    created_at_columns = [
        row["name"] for row in second.conn.execute("PRAGMA table_info(archive)")
    ].count("created_at_ts")

    assert first_value == second_value == 1_349_818_766
    assert created_at_columns == 1
    second.close()


def test_enrichment_scheduler_migration_is_backed_up_additive_and_idempotent(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "archive.db"
    scheduler_columns = {
        "enrichment_detail",
        "enrichment_retry_count",
        "enrichment_next_retry_at",
        "enrichment_first_unavailable_at",
        "enrichment_retry_eligible",
    }
    legacy_columns = [name for name in ARCHIVE_COLUMNS if name not in scheduler_columns]
    definitions = []
    for name in legacy_columns:
        column_type = (
            "TEXT PRIMARY KEY"
            if name == "row_key"
            else "INTEGER"
            if name == "created_at_ts"
            else "TEXT"
        )
        definitions.append(f"{name} {column_type}")
    connection = sqlite3.connect(db_path)
    connection.execute(f"CREATE TABLE archive ({', '.join(definitions)})")
    connection.executemany(
        """
        INSERT INTO archive(
            row_key, record_type, tweet_id, source, text, author_id,
            enrichment_state, enrichment_checked_at, enrichment_reason,
            deleted_at, raw_json, media_url, url, counts_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "tweet:bookmark::1",
                "tweet",
                "1",
                "live_graphql",
                "searchable migration sentinel",
                "101",
                None,
                None,
                None,
                None,
                '{"id":"1","text":"unchanged"}',
                None,
                None,
                None,
            ),
            (
                "tweet_object:2",
                "tweet_object",
                "2",
                "x_archive",
                "",
                None,
                "pending",
                None,
                None,
                None,
                '{"id":"2"}',
                None,
                None,
                None,
            ),
            (
                "tweet_object:3",
                "tweet_object",
                "3",
                "x_archive",
                "",
                None,
                "transient_failure",
                "2026-01-01T00:00:00+00:00",
                "network",
                None,
                '{"id":"3"}',
                None,
                None,
                None,
            ),
            (
                "tweet_object:4",
                "tweet_object",
                "4",
                "x_archive",
                "deleted archive post",
                "104",
                "terminal_unavailable",
                "2026-01-02T00:00:00+00:00",
                "deleted",
                "2026-01-02T00:00:00+00:00",
                '{"id":"4"}',
                None,
                None,
                None,
            ),
            (
                "tweet_object:5",
                "tweet_object",
                "5",
                "live_graphql",
                "private post",
                "105",
                "terminal_unavailable",
                "2026-01-03T00:00:00+00:00",
                "TerminalUnavailableError",
                None,
                '{"id":"5"}',
                None,
                None,
                None,
            ),
            (
                "raw_capture:1",
                "raw_capture",
                None,
                "live_graphql",
                None,
                None,
                None,
                None,
                None,
                None,
                '{"raw":true}',
                None,
                None,
                None,
            ),
            (
                "media:1:m",
                "media",
                "1",
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                '{"media":true}',
                "https://example.test/image.jpg",
                None,
                None,
            ),
            (
                "url:u",
                "url",
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                '{"url":true}',
                None,
                "https://example.test",
                None,
            ),
            (
                "import_manifest:d",
                "import_manifest",
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                '{"likes":1}',
            ),
        ],
    )
    connection.commit()
    connection.close()

    first = ArchiveStore(db_path, create=True)
    assert first.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert first.conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    assert first._count() == 9
    assert first.search_fts("sentinel")[0]["tweet_id"] == "1"
    rich = first._get_row("tweet:bookmark::1")
    assert rich is not None
    assert rich["text"] == "searchable migration sentinel"
    assert rich["author_id"] == "101"
    assert rich["raw_json"] == '{"id":"1","text":"unchanged"}'
    deleted = first._get_row("tweet_object:4")
    assert deleted is not None
    assert deleted["enrichment_reason"] == "archive_deleted"
    assert deleted["enrichment_retry_eligible"] == 0
    ambiguous = first._get_row("tweet_object:5")
    assert ambiguous is not None
    assert ambiguous["enrichment_reason"] == "unavailable_unknown"
    assert ambiguous["enrichment_retry_eligible"] == 1
    assert ambiguous["author_id"] == "105"
    transient = first._get_row("tweet_object:3")
    assert transient is not None
    assert transient["enrichment_retry_count"] == 0
    assert transient["enrichment_next_retry_at"] is not None
    first.close()

    backups = list(tmp_path.glob(f"archive.db.pre-schema-v{SCHEMA_VERSION}.*.bak"))
    assert len(backups) == 1
    second = ArchiveStore(db_path, create=True)
    assert second._count() == 9
    second.close()
    assert list(tmp_path.glob(f"archive.db.pre-schema-v{SCHEMA_VERSION}.*.bak")) == backups


def test_legacy_terminal_repair_prefers_rich_indexed_capture_and_reports_deferral(
    paths, capsys
) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    rich_payload = make_tweet_detail_response(
        [make_tweet_result("6", "recovered detail text", user_id="606")]
    )
    store._merge_records(
        [
            store._record(
                row_key="tweet_object:6",
                record_type="tweet_object",
                tweet_id="6",
                enrichment_state="terminal_unavailable",
                raw_json='{"__tombstone__":true}',
            ),
            store._record(
                row_key="tweet:like::6",
                record_type="tweet",
                tweet_id="6",
                collection_type="like",
                text="sparse membership text",
                author_id=None,
                raw_json='{"tweetId":"6"}',
            ),
            store._record(
                row_key="raw_capture:detail-6",
                record_type="raw_capture",
                operation="TweetDetail",
                cursor_in="6",
                captured_at="2026-01-01T00:00:00+00:00",
                raw_json=json.dumps(rich_payload),
            ),
            store._record(
                row_key="tweet_object:7",
                record_type="tweet_object",
                tweet_id="7",
                enrichment_state="terminal_unavailable",
                raw_json='{"__tombstone__":true}',
            ),
        ]
    )

    report = store._repair_legacy_terminal_rows()

    repaired = store._get_row("tweet_object:6")
    assert repaired is not None
    assert repaired["text"] == "recovered detail text"
    assert repaired["author_id"] == "606"
    assert report == {
        "legacy_terminal_rows_scanned": 2,
        "content_rows_repaired": 1,
        "author_rows_repaired": 1,
        "rows_still_missing_author": 1,
        "rows_without_richer_source": 1,
    }
    assert capsys.readouterr().out == ""
    store.close()


def test_schema_v2_migrates_to_latest_with_backup_reason_preservation_and_repairs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "archive.db"
    definitions = []
    for name in ARCHIVE_COLUMNS:
        if name in {"enrichment_followup_status", "enrichment_aborted_reason"}:
            continue
        definitions.append(f"{name} {COLUMN_TYPES[name]}")
    connection = sqlite3.connect(db_path)
    connection.execute(f"CREATE TABLE archive ({', '.join(definitions)})")
    connection.execute("PRAGMA user_version = 2")
    detail_payload = make_tweet_detail_response(
        [make_tweet_result("1", "recovered from capture", user_id="101")]
    )
    connection.executemany(
        "INSERT INTO archive ("
        "row_key, record_type, tweet_id, operation, cursor_in, captured_at, source, "
        "text, author_id, enrichment_state, enrichment_reason, enrichment_detail, "
        "enrichment_retry_count, enrichment_next_retry_at, "
        "enrichment_first_unavailable_at, enrichment_retry_eligible, raw_json"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                "tweet_object:1",
                "tweet_object",
                "1",
                None,
                None,
                None,
                "live_graphql",
                None,
                None,
                "terminal_unavailable",
                "LegacyMysteryReason",
                "original detail",
                2,
                "2026-01-01T00:00:00+00:00",
                "2025-01-01T00:00:00+00:00",
                1,
                '{"__tombstone__":true}',
            ),
            (
                "raw_capture:detail-1",
                "raw_capture",
                None,
                "TweetDetail",
                "1",
                "2026-01-02T00:00:00+00:00",
                "live_graphql",
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                json.dumps(detail_payload),
            ),
            (
                "tweet_object:2",
                "tweet_object",
                "2",
                None,
                None,
                None,
                "live_graphql",
                "available",
                "202",
                "done",
                "protected_account",
                "stale scheduler detail",
                4,
                "2030-01-01T00:00:00+00:00",
                "2025-01-01T00:00:00+00:00",
                1,
                '{"rest_id":"2"}',
            ),
        ],
    )
    connection.commit()
    connection.close()

    quick_check_stages: list[str] = []
    backup_calls = 0
    repair_calls = 0
    original_quick_check = ArchiveStore._require_quick_check
    original_backup = ArchiveStore._backup_before_migration
    original_repair = ArchiveStore._repair_legacy_terminal_rows

    def tracked_quick_check(self: ArchiveStore, stage: str) -> None:
        quick_check_stages.append(stage)
        original_quick_check(self, stage)

    def tracked_backup(self: ArchiveStore, target_version: int) -> Path:
        nonlocal backup_calls
        backup_calls += 1
        return original_backup(self, target_version)

    def tracked_repair(self: ArchiveStore, **kwargs) -> dict[str, int]:
        nonlocal repair_calls
        repair_calls += 1
        return original_repair(self, **kwargs)

    monkeypatch.setattr(ArchiveStore, "_require_quick_check", tracked_quick_check)
    monkeypatch.setattr(ArchiveStore, "_backup_before_migration", tracked_backup)
    monkeypatch.setattr(ArchiveStore, "_repair_legacy_terminal_rows", tracked_repair)

    first = ArchiveStore(db_path, create=True)
    assert first.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert first.migration_report is not None
    assert first.migration_report.from_version == 2
    assert first.migration_report.to_version == SCHEMA_VERSION
    terminal = first._get_row("tweet_object:1")
    assert terminal["text"] == "recovered from capture"
    assert terminal["author_id"] == "101"
    assert terminal["enrichment_reason"] == "unavailable_unknown"
    assert "Legacy enrichment reason: LegacyMysteryReason" in terminal["enrichment_detail"]
    available = first._get_row("tweet_object:2")
    assert available["enrichment_reason"] is None
    assert available["enrichment_detail"] is None
    assert available["enrichment_retry_count"] == 0
    assert available["enrichment_next_retry_at"] is None
    assert available["enrichment_first_unavailable_at"] is None
    assert available["enrichment_retry_eligible"] == 0
    first.close()

    backups = list(tmp_path.glob(f"archive.db.pre-schema-v{SCHEMA_VERSION}.*.bak"))
    assert len(backups) == 1
    backup = sqlite3.connect(backups[0])
    assert backup.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    assert backup.execute("PRAGMA user_version").fetchone()[0] == 2
    backup.close()

    second = ArchiveStore(db_path, create=True)
    assert second.migration_report is None
    second.close()
    assert list(tmp_path.glob(f"archive.db.pre-schema-v{SCHEMA_VERSION}.*.bak")) == backups
    assert quick_check_stages == ["before migration", "after migration"]
    assert backup_calls == 1
    assert repair_calls == 1


def test_migration_backup_failure_leaves_original_unchanged_and_no_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "archive.db"
    definitions = ", ".join(f"{name} {COLUMN_TYPES[name]}" for name in ARCHIVE_COLUMNS)
    connection = sqlite3.connect(db_path)
    connection.execute(f"CREATE TABLE archive ({definitions})")
    connection.execute("PRAGMA user_version = 2")
    connection.execute(
        "INSERT INTO archive (row_key, record_type, tweet_id) "
        "VALUES ('tweet_object:1', 'tweet_object', '1')"
    )
    connection.commit()
    connection.close()
    original_connect = sqlite3.connect

    class InvalidBackupConnection:
        def close(self) -> None:
            return None

    def failing_connect(path, *args, **kwargs):
        if str(path).endswith(".bak.tmp"):
            Path(path).touch()
            return InvalidBackupConnection()
        return original_connect(path, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", failing_connect)

    with pytest.raises(RuntimeError, match="validated pre-migration backup"):
        ArchiveStore(db_path, create=True)

    check = original_connect(db_path)
    assert check.execute("PRAGMA user_version").fetchone()[0] == 2
    assert check.execute("SELECT COUNT(*) FROM archive").fetchone()[0] == 1
    check.close()
    assert list(tmp_path.glob("*.bak")) == []
    assert list(tmp_path.glob("*.bak.tmp")) == []


def test_enrichment_selector_omits_sql_limit_when_unbounded(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    with store.conn:
        store.conn.executemany(
            "INSERT INTO archive (row_key, record_type, tweet_id, enrichment_state) "
            "VALUES (?, 'tweet_object', ?, 'pending')",
            [(f"tweet_object:{tweet_id}", str(tweet_id)) for tweet_id in range(1, 5)],
        )
    statements: list[str] = []
    store.conn.set_trace_callback(statements.append)

    unbounded = store.list_tweet_objects_for_enrichment(
        limit=None,
        now="2026-01-01T00:00:00+00:00",
    )
    limited = store.list_tweet_objects_for_enrichment(
        limit=2,
        now="2026-01-01T00:00:00+00:00",
    )

    store.conn.set_trace_callback(None)
    selects = [
        statement
        for statement in statements
        if statement.startswith("SELECT") and "enrichment_state" in statement
    ]
    assert len(unbounded) == 4
    assert len(limited) == 2
    assert "LIMIT" not in selects[0]
    assert "LIMIT 2" in selects[1]
    store.close()


def test_due_resurrection_query_orders_and_limits_in_sql(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    with store.conn:
        store.conn.executemany(
            "INSERT INTO archive ("
            "row_key, record_type, tweet_id, enrichment_state, enrichment_reason, "
            "enrichment_retry_eligible, enrichment_next_retry_at"
            ") VALUES (?, 'tweet_object', ?, 'terminal_unavailable', "
            "'protected_account', 1, '2020-01-01T00:00:00+00:00')",
            [(f"tweet_object:{index}", str(index)) for index in range(10_000)],
        )
    statements: list[str] = []
    store.conn.set_trace_callback(statements.append)

    rows = store.list_due_resurrection_tweets(
        reasons={"protected_account"},
        limit=37,
        now="2026-01-01T00:00:00+00:00",
    )

    store.conn.set_trace_callback(None)
    assert len(rows) == 37
    select = next(statement for statement in statements if statement.startswith("SELECT"))
    assert "ORDER BY" in select
    assert "LIMIT 37" in select
    store.close()


def test_database_pragmas_use_configured_values(paths) -> None:
    config = AppConfig(database=DatabaseConfig(cache_size_kb=321, mmap_size_bytes=4096))
    store = open_archive_store(paths, create=True, config=config)
    assert store is not None

    assert store.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert store.conn.execute("PRAGMA cache_size").fetchone()[0] == -321
    assert store.conn.execute("PRAGMA mmap_size").fetchone()[0] == 4096
    store.close()


def test_database_pragmas_use_application_defaults(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    assert store.conn.execute("PRAGMA cache_size").fetchone()[0] == -524288
    effective_mmap = store.conn.execute("PRAGMA mmap_size").fetchone()[0]
    assert effective_mmap > 0
    assert effective_mmap <= 1073741824
    store.close()


def test_archive_store_without_app_config_uses_database_defaults(paths) -> None:
    from tweetnook.storage.backend import ArchiveStore

    store = ArchiveStore(paths.database_path, create=True)

    assert store.conn.execute("PRAGMA cache_size").fetchone()[0] == -524288
    effective_mmap = store.conn.execute("PRAGMA mmap_size").fetchone()[0]
    assert 0 < effective_mmap <= 1073741824
    store.close()


def test_query_supports_projection_order_limit_and_offset(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    _seed_search_rows(store)

    rows = store._query(
        expr="record_type = 'tweet'",
        cols=["tweet_id", "created_at_ts"],
        order_by="created_at_ts DESC, tweet_id DESC",
        limit=2,
        offset=1,
    )

    assert rows == [
        {"tweet_id": "2", "created_at_ts": 2},
        {"tweet_id": "1", "created_at_ts": 1},
    ]
    store.close()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, False),
        (False, False),
        (True, True),
        (0, False),
        (2, True),
        ("0", False),
        ("false", False),
        ("YES", True),
        ("unexpected", False),
    ],
)
def test_parse_bool_handles_sqlite_text_values(paths, raw: object, expected: bool) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    assert store._parse_bool(raw) is expected
    store.close()


def test_paginated_ids_support_collection_order_and_random(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    _seed_search_rows(store)

    assert store.get_paginated_tweet_ids("bookmark", 10, 0, "newest") == ["3", "1"]
    assert store.get_paginated_tweet_ids("bookmark", 10, 0, "oldest") == ["1", "3"]
    assert store.get_paginated_tweet_ids("bookmark", 1, 1, "newest") == ["1"]
    assert set(store.get_paginated_tweet_ids("bookmark", 10, 0, "random")) == {"1", "3"}
    assert store.get_paginated_tweet_ids("all", 10, 0, "newest") == ["3", "2", "1"]
    assert store.count_export_rows("all") == 3
    assert store.count_export_rows("like") == 2
    assert store.get_paginated_tweet_ids("bookmark", 0, 0) == []
    store.close()


@pytest.mark.parametrize(
    ("limit", "offset", "sort"),
    [(-1, 0, "newest"), (1, -1, "newest"), (1, 0, "sideways")],
)
def test_paginated_ids_reject_invalid_controls(
    paths,
    limit: int,
    offset: int,
    sort: str,
) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    with pytest.raises(ValueError):
        store.get_paginated_tweet_ids("all", limit, offset, sort)
    store.close()


def test_fetch_tweets_preserves_requested_order_and_hydrates_tags(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    _seed_search_rows(store)
    store.update_media_tags("1", ["Nature", "Sky"])
    store.update_media_tags("2", ["City"])

    rows = store.fetch_tweets_by_ids(["2", "missing", "1"])

    assert [row["tweet_id"] for row in rows] == ["2", "1"]
    assert rows[0]["media_tags"]["tags"] == ["City"]
    assert rows[1]["media_tags"]["tags"] == ["Nature", "Sky"]
    store.close()


@pytest.mark.parametrize(
    ("query", "expected_ids"),
    [
        ('"alpha beta"', ["1"]),
        ("hello-world", ["1"]),
        ("alpha AND gamma", ["2"]),
        ("alpha OR gamma", ["1", "2"]),
        ("", []),
        ("   ", []),
    ],
)
def test_fts_handles_phrases_punctuation_booleans_and_blank_queries(
    paths,
    query: str,
    expected_ids: list[str],
) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    _seed_search_rows(store)

    results = store.search_fts(query, limit=20, types={"post"})

    assert sorted({row["tweet_id"] for row in results}) == sorted(expected_ids)
    store.close()


def test_fts_filters_types_and_collections_and_hydrates_memberships(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    _seed_search_rows(store)

    posts = store.search_fts("alpha", types={"post"}, collections={"bookmark"})
    articles = store.search_fts("alpha", types={"article"}, collections={"bookmark"})
    excluded = store.search_fts("alpha", types={"article"}, collections={"like"})

    assert [row["tweet_id"] for row in posts] == ["1"]
    assert posts[0]["collections"] == ["bookmark", "like"]
    assert [row["tweet_id"] for row in articles] == ["3"]
    assert articles[0]["type"] == "article"
    assert excluded == []
    store.close()


@pytest.mark.parametrize(
    ("query", "expected_ids"),
    [
        ("alpha AND beta", ["3"]),
        ("alpha OR missing", ["3"]),
        ("alpha NOT missing", ["3"]),
        ("alpha NOT beta", []),
        ('"focused beta"', ["3"]),
        ("focused-beta", ["3"]),
    ],
)
def test_article_search_honors_boolean_query_semantics(
    paths,
    query: str,
    expected_ids: list[str],
) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    _seed_search_rows(store)

    results = store.search_fts(query, limit=20, types={"article"})

    assert [row["tweet_id"] for row in results] == expected_ids
    store.close()


def test_fts_overfetches_past_duplicate_memberships(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    duplicate_rows = [
        _membership(
            store,
            "1",
            collection="bookmark",
            folder_id=f"folder-{index}",
            text="shared overfetch token",
            created_at=CREATED_2012,
            created_at_ts=1,
            sort_index=str(100 - index),
        )
        for index in range(12)
    ]
    duplicate_rows.append(
        _membership(
            store,
            "2",
            collection="like",
            text="shared overfetch token",
            created_at=CREATED_2024,
            created_at_ts=2,
            sort_index="1",
        )
    )
    store._merge_records(duplicate_rows)

    results = store.search_fts("overfetch", limit=2, types={"post"})

    assert {row["tweet_id"] for row in results} == {"1", "2"}
    store.close()


def test_search_authors_matches_username_display_name_and_at_prefix(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    _seed_search_rows(store)

    assert [row["username"] for row in store.search_authors("@ali")] == ["alice"]
    assert [row["username"] for row in store.search_authors("Builder")] == ["bob"]
    assert store.search_authors("@") == []
    assert store.search_authors("") == []
    store.close()


def test_search_authors_falls_back_for_legacy_missing_display_name_column(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    _seed_search_rows(store)
    connection = store.conn

    class LegacyAuthorConnection:
        def execute(self, sql, parameters=()):
            if "MIN(author_display_name)" in sql:
                raise sqlite3.OperationalError("no such column: author_display_name")
            return connection.execute(sql, parameters)

        def close(self) -> None:
            connection.close()

    store.conn = LegacyAuthorConnection()

    assert store.search_authors("@ali") == [
        {
            "id": "author-1",
            "username": "alice",
            "display_name": "alice",
        }
    ]
    store.close()


def test_page_queries_use_covering_indexes_without_temporary_sort(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    all_plan = store.conn.execute(
        "EXPLAIN QUERY PLAN SELECT tweet_id FROM archive "
        "WHERE record_type = 'tweet' "
        "ORDER BY created_at_ts DESC, CAST(sort_index AS INTEGER) DESC, tweet_id DESC "
        "LIMIT 20"
    ).fetchall()
    collection_plan = store.conn.execute(
        "EXPLAIN QUERY PLAN SELECT tweet_id FROM archive "
        "WHERE record_type = 'tweet' AND collection_type = 'bookmark' "
        "ORDER BY created_at_ts DESC, CAST(sort_index AS INTEGER) DESC, tweet_id DESC "
        "LIMIT 20"
    ).fetchall()
    details = " ".join(row["detail"] for row in all_plan + collection_plan)

    assert "idx_archive_record_page" in details
    assert "idx_archive_record_collection_page" in details
    assert "USE TEMP B-TREE" not in details
    store.close()


def test_fts_native_rank_plan_does_not_materialize_a_temporary_sort(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store._merge_records(
        [
            _membership(
                store,
                "1",
                collection="bookmark",
                text="native rank sentinel",
                created_at=CREATED_2012,
                created_at_ts=1,
                sort_index="1",
            )
        ]
    )

    plan = store.conn.execute(
        "EXPLAIN QUERY PLAN "
        "SELECT archive.tweet_id, archive_fts.rank FROM archive "
        "JOIN archive_fts ON archive.rowid = archive_fts.rowid "
        "WHERE archive_fts MATCH 'sentinel' AND archive.record_type = 'tweet' "
        "ORDER BY archive_fts.rank LIMIT 20"
    ).fetchall()
    details = " ".join(row["detail"] for row in plan)

    assert "archive_fts" in details
    assert "USE TEMP B-TREE" not in details
    store.close()
