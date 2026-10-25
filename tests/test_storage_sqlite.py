from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest

import tweetnook.storage.backend as storage_backend
from tweetnook.config import AppConfig, DatabaseConfig
from tweetnook.search import search_posts
from tweetnook.storage import open_archive_store
from tweetnook.storage.backend import (
    ARCHIVE_COLUMNS,
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


def test_reader_reads_committed_rows_on_its_own_read_only_connection(paths) -> None:
    config = AppConfig(database=DatabaseConfig(cache_size_kb=321, mmap_size_bytes=4096))
    store = open_archive_store(paths, create=True, config=config)
    assert store is not None
    store._merge_records(
        [
            _membership(
                store,
                "1",
                collection="bookmark",
                text="committed",
                created_at=CREATED_2026,
                created_at_ts=1,
                sort_index="1",
            )
        ]
    )

    reader = store.open_reader()
    try:
        assert reader.conn is not store.conn
        assert reader.conn.execute("PRAGMA cache_size").fetchone()[0] == -321
        assert reader._count("record_type = 'tweet'") == 1
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader.conn.execute("DELETE FROM archive")
    finally:
        reader.close()
    assert store._count("record_type = 'tweet'") == 1
    store.close()


def test_interrupting_the_archive_stops_running_reader_queries(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    endless = (
        "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT COUNT(*) FROM n"
    )
    errors: list[Exception] = []

    def run() -> None:
        reader = store.open_reader()
        try:
            reader.conn.execute(endless).fetchone()
        except sqlite3.OperationalError as error:
            errors.append(error)
        finally:
            reader.close()

    thread = threading.Thread(target=run)
    thread.start()
    time.sleep(0.05)
    store.interrupt_warm_reads()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert len(errors) == 1 and "interrupt" in str(errors[0])
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


def test_fetch_tweets_serializes_valid_playback_aspect_ratio(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    raw = {
        "legacy": {
            "extended_entities": {
                "media": [
                    {"type": "photo", "original_info": {"width": 900, "height": 1600}},
                    {
                        "type": "video",
                        "original_info": {"width": 1600, "height": 900},
                        "video_info": {"aspect_ratio": [3, 4]},
                    },
                    {
                        "type": "video",
                        "original_info": {"width": 640, "height": 360},
                        "video_info": {"aspect_ratio": [3, 4]},
                    },
                    {"type": "animated_gif", "video_info": {"aspect_ratio": [0, 4]}},
                ]
            }
        }
    }
    store._merge_records(
        [
            store._record(
                row_key="tweet:bookmark::aspect",
                record_type="tweet",
                tweet_id="aspect",
                collection_type="bookmark",
                text="media",
                raw_json=json.dumps(raw),
            ),
            *[
                store._record(
                    row_key=f"media:aspect:{key}",
                    record_type="media",
                    tweet_id="aspect",
                    media_key=key,
                    media_type=media_type,
                    position=position,
                    width=width,
                    height=height,
                )
                for key, media_type, position, width, height in [
                    ("photo", "photo", 0, 900, 1600),
                    ("video", "video", 1, 1600, 900),
                    ("no-dimensions", "video", 2, None, None),
                    ("malformed", "animated_gif", 3, None, None),
                ]
            ],
        ]
    )

    media = store.fetch_tweets_by_ids(["aspect"])[0]["media"]

    assert "aspect_ratio" not in media[0]
    assert (media[1]["width"], media[1]["height"]) == ("1600", "900")
    assert media[1]["aspect_ratio"] == [3, 4]
    assert media[2]["width"] is None and media[2]["height"] is None
    assert media[2]["aspect_ratio"] == [3, 4]
    assert "aspect_ratio" not in media[3]
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
        ("alpha -beta", []),
        ("alpha -missing", ["3"]),
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


@pytest.mark.parametrize(
    ("query", "expected_ids"),
    [
        ("cats -coffee", {"1", "5"}),
        ("cats NOT coffee", {"1", "5"}),
        ("cats OR dogs coffee", {"2", "3"}),
        ("cats OR dogs -coffee", {"1", "5"}),
        ("cats OR -coffee", {"1", "3", "4", "5"}),
        ("-coffee", {"1", "4", "5"}),
        ("NOT cats", {"2", "4"}),
        ("cats not", {"5"}),
        ('"cats coffee"', {"3"}),
        ('cats -"cold brew"', {"1", "3"}),
        ("coff*", {"2", "3"}),
    ],
)
def test_article_queries_share_post_clause_semantics(paths, query, expected_ids) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    texts = {
        "1": "cats",
        "2": "dogs coffee",
        "3": "cats coffee",
        "4": "birds",
        "5": "cats not cold brew",
    }
    store.merge_rows(
        [
            row
            for tweet_id, text in texts.items()
            for row in (
                _membership(
                    store,
                    tweet_id,
                    collection="bookmark",
                    text=text,
                    created_at=CREATED_2026,
                    created_at_ts=1,
                    sort_index=tweet_id,
                ),
                store._record(
                    row_key=f"article:{tweet_id}",
                    record_type="article",
                    tweet_id=tweet_id,
                    content_text=text,
                ),
            )
        ]
    )
    try:
        posts = search_posts(store, query, limit=20).rows
        articles = store.search_fts(query, limit=20, types={"article"})
        combined = store.search_fts(query, limit=20, types={"post", "article"})
        assert {row["tweet_id"] for row in posts} == expected_ids
        assert {row["tweet_id"] for row in articles} == expected_ids
        for kind in ("post", "article"):
            assert {row["tweet_id"] for row in combined if row["type"] == kind} == expected_ids
    finally:
        store.close()


def test_article_search_filters_hidden_before_limiting_and_can_unhide(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store.merge_rows(
        [
            store._record(
                row_key=f"article:{i}",
                record_type="article",
                tweet_id=str(i),
                content_text="coffee " * (20 - i),
            )
            for i in range(12)
        ]
    )
    for i in range(10):
        store.set_tweet_hidden(str(i), True)
    try:
        visible = store.search_fts("coffee", limit=2, types={"article"})
        assert [row["tweet_id"] for row in visible] == ["10", "11"]
        store.set_tweet_hidden("0", False)
        assert store.search_fts("coffee", limit=1, types={"article"})[0]["tweet_id"] == "0"
    finally:
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


def _facts(store: ArchiveStore) -> dict[str, tuple]:
    return {
        row["row_key"]: (row["is_reply"], row["favorite_count"], row["hashtags"])
        for row in store.conn.execute("SELECT * FROM archive_tweet_facts")
    }


def test_tweet_facts_follow_insert_update_and_delete(paths) -> None:
    store = open_archive_store(paths, create=True)
    reply = json.dumps(
        {
            "legacy": {
                "in_reply_to_status_id_str": "9",
                "favorite_count": 5,
                "entities": {"hashtags": [{"text": "Py"}]},
            }
        }
    )
    row = _membership(
        store,
        "1",
        collection="like",
        text="first",
        created_at=CREATED_2024,
        created_at_ts=1,
        sort_index="1",
    )
    store._merge_records([{**row, "raw_json": reply}])
    key = row["row_key"]
    assert _facts(store) == {key: (1, 5, "\x1fpy\x1f")}

    store._merge_records([{**row, "raw_json": '{"legacy":{"favorite_count":"7"}}'}])
    assert _facts(store) == {key: (0, 7, None)}

    with store.conn:
        store.conn.execute("UPDATE archive SET raw_json = 'not json' WHERE row_key = ?", (key,))
    assert _facts(store) == {key: (None, None, None)}

    with store.conn:
        store.conn.execute(
            "UPDATE archive SET record_type = 'tweet_object', raw_json = ? WHERE row_key = ?",
            (reply, key),
        )
    assert _facts(store) == {}

    store._merge_records([{**row, "row_key": "tweet:bookmark::1", "raw_json": None}])
    assert _facts(store) == {"tweet:bookmark::1": (0, 0, None)}
    with store.conn:
        store.conn.execute("DELETE FROM archive WHERE record_type = 'tweet'")
    assert _facts(store) == {}
    store.close()


def test_content_flags_follow_all_write_paths_and_ignore_placeholder_text(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    samples = [
        ("empty", "", None, 0, 0),
        ("metadata", "", '{"rest_id":"1","legacy":{}}', 0, 0),
        ("text", "Official archive text", None, 1, 0),
        ("malformed", "", "not json", 0, 0),
        ("raw", "", '{"legacy":{"full_text":"Captured"}}', 1, 0),
        ("long", "", '{"note_tweet":{"note_tweet_results":{"result":{"text":"Long text"}}}}', 1, 0),
        ("media", "", '{"legacy":{"extended_entities":{"media":[{"type":"photo"}]}}}', 1, 0),
        ("tombstone", "Unavailable", '{"__typename":"TweetTombstone"}', 0, 0),
        ("legacy", "This Post is from a private account. {learnmore}", None, 0, 0),
        ("note", "Captured", '{"birdwatch_pivot":{"subtitle":{"text":"Context"}}}', 1, 1),
    ]
    store._merge_records(
        [
            store._record(
                row_key=f"tweet_object:{key}",
                record_type="tweet_object",
                tweet_id=key,
                text=text,
                raw_json=raw,
            )
            for key, text, raw, _, _ in samples
        ]
    )
    flags = {
        row["tweet_id"]: (row["has_content"], row["has_note"])
        for row in store.conn.execute("SELECT * FROM archive_tweet_content")
    }
    assert flags == {key: (captured, note) for key, _, _, captured, note in samples}
    with store.conn:
        store.conn.execute("UPDATE archive SET text = 'Captured now' WHERE tweet_id = 'empty'")
        store.conn.execute(
            "UPDATE archive SET row_key = 'renamed', tweet_id = 'new-id' WHERE tweet_id = 'note'"
        )
        store.conn.execute("UPDATE archive SET record_type = 'media_tag' WHERE tweet_id = 'raw'")
        store.conn.execute("DELETE FROM archive WHERE tweet_id = 'media'")
    flags = {
        row["tweet_id"]: (row["has_content"], row["has_note"])
        for row in store.conn.execute("SELECT * FROM archive_tweet_content")
    }
    assert flags["empty"] == (1, 0)
    assert flags["new-id"] == (1, 1)
    assert not {"note", "raw", "media"} & flags.keys()
    store.close()


def _v10_content_archive(db_path):
    store = ArchiveStore(db_path, create=True)
    store._merge_records(
        [
            store._record(
                row_key="tweet:like::1",
                record_type="tweet",
                tweet_id="1",
                collection_type="like",
                text="Saved content",
                raw_json='{"birdwatch_pivot":{"subtitle":{"text":"Saved note"}}}',
            ),
            store._record(
                row_key="tweet:like::2",
                record_type="tweet",
                tweet_id="2",
                collection_type="like",
                text="",
                raw_json=None,
            ),
            store._record(
                row_key="tweet_object:3",
                record_type="tweet_object",
                tweet_id="3",
                text="Context",
                raw_json='{"legacy":{"full_text":"Context"}}',
            ),
        ]
    )
    store.record_community_note("1", {"text": "Earlier note"})
    store.record_community_note("1", None)
    original = [tuple(row) for row in store.conn.execute("SELECT * FROM archive ORDER BY row_key")]
    notes = store.list_community_notes("1")
    for suffix in ("ai", "au", "ad"):
        store.conn.execute(f"DROP TRIGGER archive_content_{suffix}")
    store.conn.execute("DROP TABLE archive_tweet_content")
    store.conn.execute("PRAGMA user_version = 10")
    store.conn.commit()
    store.close()
    return original, notes


def test_v10_upgrade_backfills_content_flags_without_changing_archive_or_notes(tmp_path):
    db_path = tmp_path / "archive.db"
    original, notes = _v10_content_archive(db_path)
    store = ArchiveStore(db_path, create=False)
    assert store.migration_report.from_version == 10
    assert store.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert [
        tuple(row) for row in store.conn.execute("SELECT * FROM archive ORDER BY row_key")
    ] == original
    assert store.list_community_notes("1") == notes
    assert [row["tweet_id"] for row in search_posts(store, None).rows] == ["1"]
    assert [row["tweet_id"] for row in search_posts(store, "has:note").rows] == ["1"]
    assert [row["tweet_id"] for row in search_posts(store, "is:uncaptured").rows] == ["2"]
    assert store.conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    store.close()


def test_v11_upgrade_recomputes_content_flags_and_triggers_for_placeholder_text(tmp_path):
    db_path = tmp_path / "archive.db"
    protected = (
        "You\u2019re unable to view this Post because this account owner limits who can view "
        "their Posts. {learnmore}"
    )
    store = ArchiveStore(db_path, create=True)
    store._merge_records(
        [
            store._record(
                row_key=f"tweet:like::{tweet_id}",
                record_type="tweet",
                tweet_id=tweet_id,
                collection_type="like",
                text=text,
            )
            for tweet_id, text in (("1", "Saved content"), ("2", protected))
        ]
    )
    # v11 flagged this placeholder as content, and its triggers kept doing so.
    store.conn.execute("UPDATE archive_tweet_content SET has_content = 1")
    store.conn.execute("DROP TRIGGER archive_content_au")
    store.conn.execute(
        "CREATE TRIGGER archive_content_au AFTER UPDATE OF text ON archive BEGIN "
        "UPDATE archive_tweet_content SET has_content = 1 WHERE row_key = new.row_key; END"
    )
    store.conn.execute("PRAGMA user_version = 11")
    store.conn.commit()
    store.close()

    migrated = ArchiveStore(db_path, create=False)
    assert migrated.migration_report.from_version == 11
    assert migrated.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert [row["tweet_id"] for row in search_posts(migrated, None).rows] == ["1"]
    assert [row["tweet_id"] for row in search_posts(migrated, "is:uncaptured").rows] == ["2"]
    with migrated.conn:
        migrated.conn.execute(
            "UPDATE archive SET text = ? WHERE row_key = 'tweet:like::1'", (protected,)
        )
    assert search_posts(migrated, None).rows == []
    migrated.close()


def test_failed_v10_content_upgrade_rolls_back_all_derived_changes(tmp_path, monkeypatch):
    db_path = tmp_path / "archive.db"
    _v10_content_archive(db_path)
    create_content = ArchiveStore._create_tweet_content

    def fail(store):
        create_content(store)
        raise RuntimeError("injected content failure")

    monkeypatch.setattr(ArchiveStore, "_create_tweet_content", fail)
    with pytest.raises(RuntimeError, match="injected content failure"):
        ArchiveStore(db_path, create=False)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 10
        assert (
            conn.execute(
                "SELECT name FROM sqlite_master WHERE name = 'archive_tweet_content'"
            ).fetchone()
            is None
        )
        assert conn.execute("SELECT COUNT(*) FROM archive").fetchone()[0] == 3


def _v8_archive(db_path: Path) -> None:
    initial = ArchiveStore(db_path, create=True)
    initial._merge_records(
        [
            {
                **_membership(
                    initial,
                    tweet_id,
                    collection="like",
                    text="facts sentinel",
                    created_at=CREATED_2024,
                    created_at_ts=int(tweet_id),
                    sort_index=tweet_id,
                ),
                "raw_json": raw,
            }
            for tweet_id, raw in [
                ("1", '{"legacy":{"in_reply_to_status_id_str":"9"}}'),
                ("2", '{"legacy":{}}'),
            ]
        ]
    )
    for trigger in ("archive_facts_ai", "archive_facts_au", "archive_facts_ad"):
        initial.conn.execute(f"DROP TRIGGER {trigger}")
    initial.conn.execute("DROP TABLE archive_tweet_facts")
    initial.conn.execute("PRAGMA user_version = 8")
    initial.conn.commit()
    initial.close()


def test_schema_v8_upgrade_backfills_search_facts_and_reports(tmp_path: Path) -> None:
    db_path = tmp_path / "archive.db"
    _v8_archive(db_path)

    migrated = ArchiveStore(db_path, create=False)

    assert migrated.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 12
    assert migrated.migration_report.from_version == 8
    assert [row["tweet_id"] for row in search_posts(migrated, "is:reply").rows] == ["1"]
    assert [row["tweet_id"] for row in search_posts(migrated, "-is:reply").rows] == ["2"]
    migrated._merge_records(
        [
            _membership(
                migrated,
                "3",
                collection="like",
                text="after upgrade",
                created_at=CREATED_2026,
                created_at_ts=3,
                sort_index="3",
            )
        ]
    )
    assert len(_facts(migrated)) == 3
    migrated.close()


def test_failed_schema_v8_upgrade_leaves_the_archive_at_v8(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "archive.db"
    _v8_archive(db_path)

    def fail(_store):
        raise RuntimeError("injected facts failure")

    monkeypatch.setattr(ArchiveStore, "_create_tweet_facts", fail)
    with pytest.raises(RuntimeError, match="injected"):
        ArchiveStore(db_path, create=False)

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 8
        assert (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'archive_tweet_facts'"
            ).fetchone()
            is None
        )


def test_schema_v9_upgrade_backfills_the_description_index(tmp_path: Path) -> None:
    db_path = tmp_path / "archive.db"
    initial = ArchiveStore(db_path, create=True)
    initial._merge_records(
        [
            _membership(
                initial,
                tweet_id,
                collection="like",
                text="tweet text",
                created_at=CREATED_2024,
                created_at_ts=int(tweet_id),
                sort_index=tweet_id,
            )
            for tweet_id in ("1", "2", "3")
        ]
    )
    initial.update_media_tags("1", ["boats"], description="Sailboat at dusk")
    initial.update_media_tags("2", ["untitled"])
    initial.conn.execute(
        "INSERT INTO archive (row_key, record_type, tweet_id, raw_json) "
        "VALUES ('media_tag:3', 'media_tag', '3', 'not json')"
    )
    for trigger in ("archive_description_ai", "archive_description_au", "archive_description_ad"):
        initial.conn.execute(f"DROP TRIGGER {trigger}")
    initial.conn.execute("DROP TABLE archive_description_fts")
    initial.conn.execute("PRAGMA user_version = 9")
    initial.conn.commit()
    initial.close()

    migrated = ArchiveStore(db_path, create=False)

    assert migrated.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert migrated.migration_report.from_version == 9
    assert [row["tweet_id"] for row in search_posts(migrated, "description:sailboat").rows] == ["1"]
    migrated.update_media_tags("2", ["untitled"], description="Harbor sailboat")
    assert sorted(
        row["tweet_id"] for row in search_posts(migrated, "description:sailboat").rows
    ) == ["1", "2"]
    migrated.close()
