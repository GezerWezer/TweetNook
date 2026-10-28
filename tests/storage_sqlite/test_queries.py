from __future__ import annotations

import json
import sqlite3
import threading
import time

import pytest

from tests.storage_sqlite.conftest import CREATED_2026, _membership, _seed_search_rows
from tweetnook.config import AppConfig, DatabaseConfig
from tweetnook.storage import open_archive_store


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
