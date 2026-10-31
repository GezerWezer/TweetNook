from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tweetnook.storage.backend import ArchiveStore
from tweetnook.tagging.rpd import get_rpd_status, normalize_model_name, reserve_rpd_request


def _archive_store(path: Path | str = ":memory:"):
    return ArchiveStore(Path(path), create=True)


def _reserve(
    store: ArchiveStore,
    *,
    model: str = "gemini-3.5-flash",
    limit: int = 2,
    now: datetime = datetime(2026, 7, 31, 12, tzinfo=UTC),
):
    return reserve_rpd_request(store, model=model, limit=limit, now=now)


def test_reservation_enforces_limit_and_returns_immutable_status() -> None:
    store = _archive_store()

    first = _reserve(store)
    second = _reserve(store)
    denied = _reserve(store)

    assert (first.allowed, first.used, first.remaining) == (True, 1, 1)
    assert (second.allowed, second.used, second.remaining) == (True, 2, 0)
    assert (denied.allowed, denied.used, denied.remaining) == (False, 2, 0)
    assert denied.limit == 2
    assert denied.day.isoformat() == "2026-07-31"
    assert denied.reset_at.isoformat() == "2026-08-01T00:00:00-07:00"
    with pytest.raises(AttributeError):
        denied.used = 0  # type: ignore[misc]


def test_read_only_status_does_not_create_or_repair_state() -> None:
    store = _archive_store()
    missing = get_rpd_status(
        store,
        model="gemini-3.5-flash",
        limit=2,
        now=datetime(2026, 7, 31, 12, tzinfo=UTC),
    )
    assert (missing.allowed, missing.used, missing.remaining) == (True, 0, 2)
    assert store.conn.execute("SELECT COUNT(*) FROM archive").fetchone()[0] == 0

    store.conn.execute(
        "INSERT INTO archive (row_key, record_type, key, value) VALUES (?, ?, ?, ?)",
        (
            "metadata:gemini_rpd:gemini-3.5-flash",
            "metadata",
            "gemini_rpd:gemini-3.5-flash",
            "malformed",
        ),
    )
    store.conn.commit()
    malformed = get_rpd_status(
        store,
        model="gemini-3.5-flash",
        limit=2,
        now=datetime(2026, 7, 31, 12, tzinfo=UTC),
    )

    assert (malformed.allowed, malformed.used, malformed.remaining) == (True, 0, 2)
    assert store.conn.execute("SELECT value FROM archive").fetchone()[0] == "malformed"


def test_read_only_status_reports_exhausted_counter() -> None:
    store = _archive_store()
    assert _reserve(store, limit=1).allowed

    status = get_rpd_status(
        store,
        model="gemini-3.5-flash",
        limit=1,
        now=datetime(2026, 7, 31, 12, tzinfo=UTC),
    )

    assert (status.allowed, status.used, status.remaining) == (False, 1, 0)


def test_counter_persists_across_connections_and_normalizes_model(tmp_path: Path) -> None:
    db_path = tmp_path / "archive.db"
    first_store = _archive_store(db_path)
    assert _reserve(first_store, model=" models/GEMINI-3.5-FLASH ", limit=1).allowed
    first_store.close()

    second_store = _archive_store(db_path)
    denied = _reserve(second_store, model="gemini-3.5-flash", limit=1)

    assert normalize_model_name(" models/GEMINI-3.5-FLASH ") == "gemini-3.5-flash"
    assert not denied.allowed
    assert denied.used == 1


def test_real_archive_reopens_existing_quota_state_without_a_migration(tmp_path: Path) -> None:
    db_path = tmp_path / "archive.db"
    now = datetime(2026, 7, 31, 12, tzinfo=UTC)
    store = ArchiveStore(db_path, create=True)
    assert reserve_rpd_request(
        store,
        model="gemini-3.5-flash",
        limit=1,
        now=now,
    ).allowed
    store.close()

    reopened = ArchiveStore(db_path, create=False)
    try:
        status = get_rpd_status(
            reopened,
            model="gemini-3.5-flash",
            limit=1,
            now=now,
        )
        assert (status.allowed, status.used, status.remaining) == (False, 1, 0)
    finally:
        reopened.close()


@pytest.mark.parametrize("stored_value", [None, "not-json", "[]", '{"version":99}'])
def test_missing_or_malformed_state_starts_at_zero(stored_value: str | None) -> None:
    store = _archive_store()
    if stored_value is not None:
        store.conn.execute(
            "INSERT INTO archive (row_key, record_type, key, value) VALUES (?, ?, ?, ?)",
            (
                "metadata:gemini_rpd:gemini-3.5-flash",
                "metadata",
                "gemini_rpd:gemini-3.5-flash",
                stored_value,
            ),
        )
        store.conn.commit()

    status = _reserve(store, limit=1)

    assert status.allowed
    assert status.used == 1


def test_old_pacific_day_resets_without_a_migration() -> None:
    store = _archive_store()
    store.conn.execute(
        "INSERT INTO archive (row_key, record_type, key, value) VALUES (?, ?, ?, ?)",
        (
            "metadata:gemini_rpd:gemini-3.5-flash",
            "metadata",
            "gemini_rpd:gemini-3.5-flash",
            json.dumps(
                {
                    "day": "2026-07-30",
                    "model": "gemini-3.5-flash",
                    "used": 20,
                    "version": 1,
                }
            ),
        ),
    )
    store.conn.commit()

    status = _reserve(store, limit=1)

    assert status.allowed
    assert status.used == 1


def test_lowered_and_raised_limit_use_current_configuration() -> None:
    store = _archive_store()
    assert _reserve(store, limit=3).allowed
    assert _reserve(store, limit=3).allowed

    lowered = _reserve(store, limit=1)
    raised = _reserve(store, limit=4)

    assert (lowered.allowed, lowered.used, lowered.limit) == (False, 2, 1)
    assert (raised.allowed, raised.used, raised.limit) == (True, 3, 4)


def test_models_have_independent_counters() -> None:
    store = _archive_store()

    first_model = _reserve(store, model="gemini-3.5-flash", limit=1)
    second_model = _reserve(store, model="gemini-3.5-pro", limit=1)

    assert first_model.allowed
    assert second_model.allowed
    rows = store.conn.execute(
        "SELECT row_key FROM archive WHERE record_type = 'metadata' ORDER BY row_key"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("metadata:gemini_rpd:gemini-3.5-flash",),
        ("metadata:gemini_rpd:gemini-3.5-pro",),
    ]


@pytest.mark.parametrize(
    ("now", "expected_day", "expected_reset"),
    [
        (
            datetime(2026, 7, 31, 6, 59, 59, tzinfo=UTC),
            "2026-07-30",
            "2026-07-31T00:00:00-07:00",
        ),
        (
            datetime(2026, 7, 31, 7, 0, tzinfo=UTC),
            "2026-07-31",
            "2026-08-01T00:00:00-07:00",
        ),
        (
            datetime(2026, 3, 8, 7, 59, tzinfo=UTC),
            "2026-03-07",
            "2026-03-08T00:00:00-08:00",
        ),
        (
            datetime(2026, 3, 8, 8, 0, tzinfo=UTC),
            "2026-03-08",
            "2026-03-09T00:00:00-07:00",
        ),
        (
            datetime(2026, 11, 1, 7, 0, tzinfo=UTC),
            "2026-11-01",
            "2026-11-02T00:00:00-08:00",
        ),
    ],
)
def test_pacific_day_and_next_reset_are_dst_correct(
    now: datetime,
    expected_day: str,
    expected_reset: str,
) -> None:
    status = _reserve(_archive_store(), limit=1, now=now)

    assert status.day.isoformat() == expected_day
    assert status.reset_at.isoformat() == expected_reset


def test_two_connections_cannot_both_reserve_last_request(tmp_path: Path) -> None:
    db_path = tmp_path / "archive.db"
    first_store = _archive_store(db_path)
    second_store = _archive_store(db_path)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(_reserve, store, limit=1) for store in (first_store, second_store)
        ]
        statuses = [future.result(timeout=5) for future in futures]

    assert sorted(status.allowed for status in statuses) == [False, True]
    assert sorted(status.used for status in statuses) == [1, 1]


def test_database_error_rolls_back_and_is_reraised() -> None:
    store = _archive_store()
    store.conn.execute("DROP TABLE archive")

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        _reserve(store)

    assert not store.conn.in_transaction


def test_reservation_rejects_foreign_transaction_without_losing_its_work(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    try:
        store.conn.execute(
            "INSERT INTO archive (row_key, record_type) VALUES ('caller', 'metadata')"
        )
        with pytest.raises(RuntimeError, match="foreign transaction"):
            reserve_rpd_request(store, model="gemini-3.5-flash", limit=1)
        assert store.conn.in_transaction
        assert store.conn.execute("SELECT 1 FROM archive WHERE row_key='caller'").fetchone()
        store.conn.rollback()
    finally:
        store.close()


@pytest.mark.parametrize("write", [False, True])
def test_reservation_rejects_owned_transactions_and_commits_before_return(tmp_path, write):
    path = tmp_path / "archive.db"
    store = _archive_store(path)
    try:
        with store.transaction(write=write):
            store.update_media_tags("unrelated", ["Keep"])
            with pytest.raises(RuntimeError, match="outermost transaction"):
                _reserve(store, limit=1)
        assert store.get_tag_counts() == [{"tag": "Keep", "count": 1}]
        assert _reserve(store, limit=1).allowed
        reader = _archive_store(path)
        try:
            status = get_rpd_status(
                reader,
                model="gemini-3.5-flash",
                limit=1,
                now=datetime(2026, 7, 31, 12, tzinfo=UTC),
            )
            assert (status.used, status.remaining) == (1, 0)
            assert not _reserve(reader, limit=1).allowed
        finally:
            reader.close()
        with pytest.raises(ValueError, match="later failure"):
            with store.transaction():
                store.update_media_tags("failed", ["Lose"])
                raise ValueError("later failure")
    finally:
        store.close()
    reopened = _archive_store(path)
    try:
        assert not _reserve(reopened, limit=1).allowed
        assert reopened.get_tag_counts() == [{"tag": "Keep", "count": 1}]
    finally:
        reopened.close()
