from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from tweetnook.storage.backend import ArchiveStore


@pytest.mark.parametrize("operation", ["merge", "delete"])
def test_failed_global_tag_edit_cannot_be_committed_by_another_edit(tmp_path, operation):
    path = tmp_path / "archive.db"
    store = ArchiveStore(path, create=True)
    for tweet_id in ("1", "2"):
        store.update_media_tags(tweet_id, ["Old", "Keep"])
    store.conn.execute(
        "CREATE TEMP TRIGGER reject_second_tag BEFORE UPDATE ON archive "
        "WHEN old.row_key = 'media_tag:2' BEGIN SELECT RAISE(ABORT, 'second write'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="second write"):
        if operation == "merge":
            store.merge_global_tags("New", ["Old"])
        else:
            store.delete_global_tag("Old")
    store.set_tweet_hidden("unrelated", True)
    store.close()
    reopened = ArchiveStore(path, create=False)
    try:
        rows = reopened.conn.execute(
            "SELECT raw_json FROM archive WHERE record_type = 'media_tag' ORDER BY tweet_id"
        ).fetchall()
        assert [json.loads(row[0])["tags"] for row in rows] == [["Old", "Keep"]] * 2
    finally:
        reopened.close()


def test_transaction_refuses_foreign_work_without_rolling_it_back(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    try:
        store.conn.execute(
            "INSERT INTO archive (row_key, record_type) VALUES ('foreign', 'metadata')"
        )
        with pytest.raises(RuntimeError, match="foreign transaction"):
            with store.transaction():
                pytest.fail("entered a foreign transaction")
        assert store.conn.in_transaction
        assert store.conn.execute("SELECT 1 FROM archive WHERE row_key = 'foreign'").fetchone()
        store.conn.rollback()
    finally:
        store.close()


def test_regeneration_cleanup_rolls_back_earlier_deletes_and_manifest(tmp_path):
    path = tmp_path / "archive.db"
    store = ArchiveStore(path, create=True)
    with store.transaction():
        store.conn.executemany(
            "INSERT INTO archive (row_key, record_type, source) VALUES (?, ?, 'x_archive')",
            [
                ("capture", "raw_capture"),
                ("member", "tweet"),
                ("object", "tweet_object"),
                ("manifest", "import_manifest"),
            ],
        )
    store.conn.execute(
        "CREATE TEMP TRIGGER reject_object BEFORE DELETE ON archive "
        "WHEN old.row_key = 'object' BEGIN SELECT RAISE(ABORT, 'after earlier deletes'); END"
    )
    try:
        with pytest.raises(sqlite3.IntegrityError, match="after earlier deletes"):
            store.clear_archive_import_data()
        store.set_tweet_hidden("unrelated", True)
    finally:
        store.close()
    store = ArchiveStore(path, create=False)
    try:
        assert (
            store.conn.execute(
                "SELECT COUNT(*) FROM archive WHERE source = 'x_archive'"
            ).fetchone()[0]
            == 4
        )
        assert store.clear_archive_import_data() == {
            "raw_captures": 1,
            "tweets": 1,
            "tweet_objects": 1,
            "tweet_relations": 0,
            "media": 0,
            "urls": 0,
            "url_refs": 0,
            "articles": 0,
            "import_manifests": 1,
        }
    finally:
        store.close()


def test_nested_helpers_rollback_only_their_savepoint(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    try:
        with store.transaction():
            store.update_media_tags("kept", ["Keep"])
            with pytest.raises(ValueError):
                with store.transaction():
                    store.update_media_tags("rolled-back", ["Lose"])
                    raise ValueError("nested failure")
            assert store.conn.in_transaction
        assert store.get_tag_counts() == [{"tag": "Keep", "count": 1}]
        with pytest.raises(ValueError):
            with store.transaction():
                store.update_media_tags("outer-rollback", ["Lose"])
                raise ValueError("outer failure")
        assert store.get_tag_counts() == [{"tag": "Keep", "count": 1}]
    finally:
        store.close()


@pytest.mark.parametrize("retry_write", [False, True])
def test_sqlite_rollback_aborts_the_outer_scope_until_it_exits(tmp_path, retry_write):
    path = tmp_path / "archive.db"
    store = ArchiveStore(path, create=True)
    store.conn.execute(
        "CREATE TEMP TRIGGER abort_transaction BEFORE INSERT ON archive "
        "WHEN new.row_key = 'tweet_hidden:abort' AND EXISTS "
        "(SELECT 1 FROM archive WHERE row_key = 'media_tag:before') "
        "BEGIN SELECT RAISE(ROLLBACK, 'whole transaction'); END"
    )
    try:
        with pytest.raises(RuntimeError, match="transaction.*aborted"):
            with store.transaction():
                store.update_media_tags("before", ["Lose"])
                with pytest.raises(sqlite3.IntegrityError, match="whole transaction"):
                    store.set_tweet_hidden("abort", True)
                if retry_write:
                    with pytest.raises(RuntimeError, match="transaction.*aborted"):
                        store.update_media_tags("after", ["Lose"])
        assert store.get_tag_counts() == []
        store.update_media_tags("fresh", ["Keep"])
    finally:
        store.close()
    reopened = ArchiveStore(path, create=False)
    try:
        assert reopened.get_tag_counts() == [{"tag": "Keep", "count": 1}]
    finally:
        reopened.close()


def test_failed_savepoint_rollback_cannot_commit_the_failed_helper(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)

    def reject_savepoint_rollback(action, operation, _name, _database, _source):
        if action == sqlite3.SQLITE_SAVEPOINT and operation == "ROLLBACK":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    store.conn.set_authorizer(reject_savepoint_rollback)
    try:
        with pytest.raises(RuntimeError, match="transaction.*aborted"):
            with store.transaction():
                store.update_media_tags("before", ["Lose"])
                with pytest.raises(ValueError, match="original failure"):
                    with store.transaction():
                        store.update_media_tags("failed", ["Lose"])
                        raise ValueError("original failure")
                with pytest.raises(RuntimeError, match="transaction.*aborted"):
                    store.update_media_tags("after", ["Lose"])
        assert store.get_tag_counts() == []
        store.update_media_tags("fresh", ["Keep"])
        assert store.get_tag_counts() == [{"tag": "Keep", "count": 1}]
    finally:
        store.close()


def test_global_tag_selection_and_write_share_the_transaction_lock(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    selected, release, edited, started = Event(), Event(), Event(), Event()
    store.update_media_tags("1", ["Old"], description="before")

    connection = store.conn

    class SelectedCursor:
        def __init__(self, cursor):
            self.cursor = cursor

        def fetchall(self):
            rows = self.cursor.fetchall()
            selected.set()
            assert release.wait(5)
            return rows

    class Connection:
        def __getattr__(self, name):
            return getattr(connection, name)

        def execute(self, statement, *args):
            cursor = connection.execute(statement, *args)
            if "SELECT DISTINCT a.tweet_id, a.raw_json" in statement:
                return SelectedCursor(cursor)
            return cursor

    def edit():
        started.set()
        store.update_media_tags("1", ["Manual"], description="after")
        edited.set()

    store.conn = Connection()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            merge = pool.submit(store.merge_global_tags, "New", ["Old"])
            assert selected.wait(5)
            manual = pool.submit(edit)
            assert started.wait(5)
            assert not edited.wait(0.05)
            release.set()
            merge.result(timeout=5)
            manual.result(timeout=5)
        row = store.conn.execute(
            "SELECT raw_json FROM archive WHERE row_key = 'media_tag:1'"
        ).fetchone()
        assert json.loads(row[0]) == {"tags": ["Manual"], "description": "after"}
    finally:
        release.set()
        store.close()
