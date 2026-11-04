from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.storage_sqlite.conftest import (
    CREATED_2024,
    CREATED_2026,
    _membership,
    add_released_followup_columns,
)
from tweetnook.core.exceptions import ArchiveUpgradeError
from tweetnook.core.unavailability import is_placeholder_text
from tweetnook.storage import open_archive_store
from tweetnook.storage import schema as schema_module
from tweetnook.storage.backend import ArchiveStore
from tweetnook.storage.schema import SCHEMA_VERSION
from tweetnook.storage.search_query import search_posts


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
    add_released_followup_columns(store)
    store.conn.execute("PRAGMA user_version = 10")
    store.conn.commit()
    store.close()
    return original, notes


def test_upgrade_announces_itself_before_the_work_starts(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "archive.db"
    _v10_content_archive(db_path)
    announced = []
    migrate = ArchiveStore._migrate_search_facts

    def record_then_migrate(self, current_version):
        announced.append(capsys.readouterr().err)
        migrate(self, current_version)

    monkeypatch.setattr(ArchiveStore, "_migrate_search_facts", record_then_migrate)
    ArchiveStore(db_path, create=False).close()
    assert f"from schema v10 to v{SCHEMA_VERSION}" in announced[0]


def _archive_contents(db_path: Path) -> tuple[int, list[tuple]]:
    connection = sqlite3.connect(db_path)
    try:
        return (
            connection.execute("PRAGMA user_version").fetchone()[0],
            [tuple(row) for row in connection.execute("SELECT * FROM archive ORDER BY row_key")],
        )
    finally:
        connection.close()


def test_upgrade_refuses_without_writing_when_the_disk_is_too_small(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "archive.db"
    _v10_content_archive(db_path)
    before = _archive_contents(db_path)
    needed = 2 * db_path.stat().st_size
    monkeypatch.setattr(
        schema_module.shutil, "disk_usage", lambda _path: SimpleNamespace(free=needed - 1)
    )
    with pytest.raises(ArchiveUpgradeError, match="Free up space") as error:
        ArchiveStore(db_path, create=False)
    assert str(tmp_path) in str(error.value)
    # Nothing announced: the upgrade never started.
    assert "Upgrading the archive from" not in capsys.readouterr().err
    assert _archive_contents(db_path) == before


def test_upgrade_starts_with_exactly_the_required_space(tmp_path, monkeypatch):
    db_path = tmp_path / "archive.db"
    _v10_content_archive(db_path)
    needed = 2 * db_path.stat().st_size
    monkeypatch.setattr(
        schema_module.shutil, "disk_usage", lambda _path: SimpleNamespace(free=needed)
    )
    store = ArchiveStore(db_path, create=False)
    assert store.migration_report.from_version == 10
    store.close()


def test_disk_filling_during_an_upgrade_leaves_the_archive_unchanged(tmp_path, monkeypatch):
    db_path = tmp_path / "archive.db"
    _v10_content_archive(db_path)
    before = _archive_contents(db_path)
    drop_columns = ArchiveStore._drop_unread_followup_columns

    def fill_disk_inside_the_transaction(self):
        # SQLite reports a real SQLITE_FULL once the file can't grow past this page count.
        pages = self.conn.execute("PRAGMA page_count").fetchone()[0]
        self.conn.execute(f"PRAGMA max_page_count = {pages}")
        drop_columns(self)
        self.conn.executemany(
            "INSERT INTO archive (row_key, record_type, raw_json) VALUES (?, 'filler', ?)",
            ((f"filler-{index}", "x" * 4096) for index in range(pages + 10)),
        )

    monkeypatch.setattr(
        ArchiveStore, "_drop_unread_followup_columns", fill_disk_inside_the_transaction
    )
    with pytest.raises(ArchiveUpgradeError, match="disk filled up") as error:
        ArchiveStore(db_path, create=False)
    assert isinstance(error.value.__cause__, sqlite3.OperationalError)
    assert _archive_contents(db_path) == before


def test_upgrade_reports_an_unexpected_failure_as_an_upgrade_failure(tmp_path, monkeypatch):
    db_path = tmp_path / "archive.db"
    _v10_content_archive(db_path)

    def fail(self):
        raise RuntimeError("unexpected row count")

    monkeypatch.setattr(ArchiveStore, "_merge_duplicate_media", fail)
    with pytest.raises(ArchiveUpgradeError, match="unexpected row count"):
        ArchiveStore(db_path, create=False)


def test_a_locked_database_is_not_reported_as_a_failed_upgrade(tmp_path, monkeypatch):
    db_path = tmp_path / "archive.db"
    _v10_content_archive(db_path)
    locked = sqlite3.OperationalError("database is locked")
    locked.sqlite_errorcode = sqlite3.SQLITE_BUSY

    def fail(self, _version):
        raise locked

    monkeypatch.setattr(ArchiveStore, "_migrate_search_facts", fail)
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        ArchiveStore(db_path, create=False)


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
    add_released_followup_columns(store)
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
    add_released_followup_columns(initial)
    initial.conn.execute("PRAGMA user_version = 8")
    initial.conn.commit()
    initial.close()


def test_schema_v8_upgrade_backfills_search_facts_and_reports(tmp_path: Path) -> None:
    db_path = tmp_path / "archive.db"
    _v8_archive(db_path)

    migrated = ArchiveStore(db_path, create=False)

    assert migrated.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 14
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
    add_released_followup_columns(initial)
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


# (text, is it X's placeholder rather than a post?), which both sides must agree on.
_PLACEHOLDER_CASES = [
    ("A real post", False),
    ("This post is about being unavailable", False),
    ("This post is unavailable.", True),
    ("THIS POST IS UNAVAILABLE.", True),
    ("\nThis post is unavailable.", True),
    ("\tThis post is unavailable.", True),
    ("\r\nThis post is unavailable.", True),
    ("\u00a0This post is unavailable.", True),
    ("This post\nis unavailable.", True),
    ("This post\u00a0is unavailable.", True),
    ("You\u2019re unable to view this Post", True),
    ("You're unable to view this Post", True),
    ("Learn more {learnmore}", True),
]
_BLANK_CASES = ["", " ", "\n", "\t\r\n", "\u00a0", " \u00a0\n "]
# What the triggers can't see without Python, which reads as content to them alone.
_ACCEPTED_DIFFERENCES = [
    "This post  is unavailable.",
    "\u2003This post is unavailable.",
]


def _has_content(paths, text: str) -> int:
    store = open_archive_store(paths, create=True)
    try:
        store.conn.execute(
            "INSERT OR REPLACE INTO archive (row_key, record_type, tweet_id, collection_type, text)"
            " VALUES ('tweet:like::1', 'tweet', '1', 'like', ?)",
            (text,),
        )
        return store.conn.execute(
            "SELECT has_content FROM archive_tweet_content WHERE row_key = 'tweet:like::1'"
        ).fetchone()[0]
    finally:
        store.close()


@pytest.mark.parametrize(("text", "placeholder"), _PLACEHOLDER_CASES)
def test_capture_flag_agrees_with_the_placeholder_rule(paths, text: str, placeholder: bool) -> None:
    assert is_placeholder_text(text) is placeholder
    assert _has_content(paths, text) == (0 if placeholder else 1)


@pytest.mark.parametrize("text", _BLANK_CASES)
def test_blank_text_is_not_captured_content(paths, text: str) -> None:
    assert _has_content(paths, text) == 0


@pytest.mark.parametrize("text", _ACCEPTED_DIFFERENCES)
def test_recorded_differences_from_the_placeholder_rule(paths, text: str) -> None:
    assert is_placeholder_text(text) is True
    assert _has_content(paths, text) == 1
