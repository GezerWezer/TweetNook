from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from tests.storage.conftest import _complex_tweet, _counts, _tweet
from tweetnook.core.exceptions import ArchiveOwnerMismatchError
from tweetnook.storage import open_archive_store
from tweetnook.storage.records import PageBuffer


def test_store_persist_page_is_atomic(paths, monkeypatch: pytest.MonkeyPatch) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    original = store.upsert_membership

    def fail_once(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(store, "upsert_membership", fail_once)

    with pytest.raises(RuntimeError):
        store.persist_page(
            operation="Bookmarks",
            collection_type="bookmark",
            cursor_in=None,
            cursor_out="cursor-1",
            http_status=200,
            raw_json={"ok": True},
            tweets=[_tweet("1")],
            last_head_tweet_id="1",
            backfill_cursor="cursor-1",
            backfill_incomplete=True,
        )

    assert store.counts() == _counts()
    monkeypatch.setattr(store, "upsert_membership", original)
    store.close()


def test_store_persist_page_is_atomic_on_secondary_object_failure(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    def fail_once(*args, **kwargs):
        raise RuntimeError("secondary boom")

    monkeypatch.setattr(store, "_buffer_secondary_objects", fail_once)

    with pytest.raises(RuntimeError):
        store.persist_page(
            operation="Bookmarks",
            collection_type="bookmark",
            cursor_in=None,
            cursor_out="cursor-1",
            http_status=200,
            raw_json={"ok": True},
            tweets=[_complex_tweet()],
            last_head_tweet_id="100",
            backfill_cursor="cursor-1",
            backfill_incomplete=True,
        )

    assert store.counts() == _counts()
    store.close()


def _database_rows(db_path: Path) -> dict[str, list[tuple]]:
    """Every stored row, FTS shadow tables included, read through a fresh connection.

    sqlite_stat* hold planner statistics that closing the store refreshes, not content.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        tables = [
            name
            for (name,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND sql NOT LIKE 'CREATE VIRTUAL%' AND name NOT LIKE 'sqlite_stat%'"
            )
        ]
        return {
            name: conn.execute(f'SELECT * FROM "{name}" ORDER BY 1').fetchall() for name in tables
        }
    finally:
        conn.close()


def test_store_persist_page_rolls_back_rows_already_written(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    page = dict(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        http_status=200,
        backfill_incomplete=True,
    )
    store.persist_page(
        **page,
        cursor_out="cursor-1",
        raw_json={"page": 1},
        tweets=[_tweet("1")],
        last_head_tweet_id="1",
        backfill_cursor="cursor-1",
    )
    db_path = store.db_path
    before = _database_rows(db_path)

    # The sync-state row is the page's last write, so by the time this fires the raw
    # capture, both tweets, their memberships and FTS entries are already in the
    # transaction. ABORT only undoes the failing row; the rest must be rolled back too.
    store.conn.execute(
        "CREATE TEMP TRIGGER fail_page_cursor BEFORE INSERT ON archive "
        "WHEN new.record_type = 'sync_state' "
        "BEGIN SELECT RAISE(ABORT, 'injected cursor failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="injected cursor failure"):
        store.persist_page(
            **page,
            cursor_out="cursor-2",
            raw_json={"page": 2},
            tweets=[replace(_tweet("1"), text="rewritten zebra"), _tweet("2")],
            last_head_tweet_id="2",
            backfill_cursor="cursor-2",
        )
    store.close()

    assert _database_rows(db_path) == before
    reopened = open_archive_store(paths, create=False)
    assert reopened is not None
    try:
        assert reopened.counts() == _counts(raw_captures=1, tweets=1, collections=1, sync_state=1)
        state = reopened.get_sync_state("bookmark")
        assert (state.last_head_tweet_id, state.backfill_cursor) == ("1", "cursor-1")
        assert (
            reopened.conn.execute(
                "SELECT text FROM archive WHERE record_type = 'tweet' AND tweet_id = '1'"
            ).fetchone()[0]
            == "tweet 1"
        )
        assert (
            reopened.conn.execute(
                "SELECT count(*) FROM archive_fts WHERE archive_fts MATCH 'zebra'"
            ).fetchone()[0]
            == 0
        )
    finally:
        reopened.close()


def test_owner_guardrail(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store.set_archive_owner_id("42")
    with pytest.raises(ArchiveOwnerMismatchError):
        store.ensure_archive_owner_id("84")
    store.close()


def test_prefetch_rows_populates_buffer_cache(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out="cursor-1",
        http_status=200,
        raw_json={"ok": True},
        tweets=[_complex_tweet()],
        last_head_tweet_id="100",
        backfill_cursor="cursor-1",
        backfill_incomplete=True,
    )

    buffer = PageBuffer()
    store.prefetch_rows(
        [
            "tweet:bookmark::100",
            "tweet_object:100",
            "tweet_object:missing",
        ],
        cursor=buffer,
    )

    assert buffer.existing_rows["tweet:bookmark::100"]["tweet_id"] == "100"
    assert buffer.existing_rows["tweet_object:100"]["tweet_id"] == "100"
    assert buffer.existing_rows["tweet_object:missing"] is None
    store.close()
