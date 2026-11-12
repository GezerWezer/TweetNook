"""Native SQLite archive storage backend."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING

from tweetnook.config import DatabaseConfig, XDGPaths

from .authors import AuthorsMixin
from .checks import ChecksMixin
from .connection import SerializedConnection
from .conversations import ConversationsMixin
from .db import ConnectionMixin, connect_read_only
from .details import DetailsMixin
from .enrichment import EnrichmentMixin
from .imports import ImportsMixin
from .ingestion import IngestionMixin
from .objects import ObjectsMixin
from .presentation import PresentationMixin
from .prune import PruneMixin
from .reads import ReadsMixin
from .record_types import RecordType
from .records import RecordsMixin
from .relations import RelationsMixin
from .report_facts import ReportFactsMixin
from .schema import SCHEMA_VERSION, MigrationReport, SchemaMixin
from .search import SearchMixin
from .sql_functions import (
    register_export_functions,
    register_identity_functions,
)
from .stats import StatsMixin
from .sync_state import SyncStateMixin
from .tags import TagsMixin
from .tweets import TweetsMixin

if TYPE_CHECKING:
    from tweetnook.config import AppConfig


# Each mixin inherits the mixins it calls, so type checkers see the whole store. The
# bases here and in each mixin follow one order, which Python needs for a consistent
# method resolution order: a mixin comes before the mixins it builds on.
class ArchiveStore(
    DetailsMixin,
    IngestionMixin,
    ReportFactsMixin,
    TweetsMixin,
    StatsMixin,
    SearchMixin,
    PresentationMixin,
    EnrichmentMixin,
    ChecksMixin,
    SyncStateMixin,
    SchemaMixin,
    ImportsMixin,
    ReadsMixin,
    ObjectsMixin,
    RecordsMixin,
    AuthorsMixin,
    RelationsMixin,
    TagsMixin,
    ConversationsMixin,
    PruneMixin,
    ConnectionMixin,
):
    def __init__(
        self,
        db_path: Path,
        *,
        create: bool,
        config: AppConfig | None = None,
        read_only: bool = False,
    ) -> None:
        self.db_path = db_path
        self.config = config
        self._closed = False
        self._transaction_lock = threading.RLock()
        self._like_order_cache = None
        self._like_capture_pages = {}
        self._indexed_like_order = None
        self._transaction_depth = 0
        self._transaction_aborted = False
        self._read_only = read_only
        self._has_written = False
        self._warm_cancelled = False
        self._count_cache: dict[str, tuple[int, int]] = {}
        self._search_count_cache: dict[tuple, tuple[tuple[int, int], int]] = {}
        self._search_page_cache: dict[tuple, tuple[tuple[int, int], float, list]] = {}
        self._reader_pool = threading.local()
        self._readers: list[ArchiveStore] = []
        self.migration_report: MigrationReport | None = None
        if create:
            db_path.parent.mkdir(parents=True, exist_ok=True)

        db_file = str(db_path)
        if read_only:
            # A private reader for one thread; the writer that opened the
            # archive already chose WAL and migrated the schema.
            self.conn = connect_read_only(db_path)
        else:
            # Shared Web readers can race CPython's prepared-statement cache even
            # with serialized SQLite (python/cpython#118172). Keep the database page
            # cache, but prepare independent statements for concurrent cursors.
            self.conn = sqlite3.connect(
                db_file,
                check_same_thread=False,
                cached_statements=0,
                factory=SerializedConnection,
            )
        try:
            self.conn.row_factory = sqlite3.Row
            register_export_functions(self.conn)
            register_identity_functions(self.conn)
            if not read_only:
                self.conn.execute("PRAGMA journal_mode=WAL")
                self.conn.execute("PRAGMA recursive_triggers=ON")

            self._apply_tuning(config.database if config is not None else DatabaseConfig())

            if not read_only and (create or db_path.exists()):
                self._migrate_schema()
                # Hydration looks up one record type for a batch of tweet ids; with the type in
                # the key it visits only those rows instead of every row sharing the id. It leads
                # with tweet_id: led by record_type, the planner took it for feed candidate scans
                # and sorted every post instead of walking the date-ordered page indexes.
                self.conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_archive_tweet_record "
                    "ON archive(tweet_id, record_type)"
                )
                # The derived like sequence persists, keyed by the like-order revision it
                # was built from, so a restart doesn't rebuild it from every capture.
                self.conn.execute(
                    "CREATE TABLE IF NOT EXISTS tweetnook_like_order "
                    "(tweet_id TEXT PRIMARY KEY, latest_position INTEGER UNIQUE, "
                    "earliest_position INTEGER UNIQUE)"
                )
                self.conn.execute(
                    "CREATE TABLE IF NOT EXISTS tweetnook_like_order_state "
                    "(id INTEGER PRIMARY KEY CHECK (id = 1), signature INTEGER NOT NULL)"
                )
                # Sorting by like count walks this index instead of every membership's facts.
                self.conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_facts_favorite_count "
                    "ON archive_tweet_facts(favorite_count DESC, row_key)"
                )
                self._ensure_tag_table()
                self._tag_table_ready = True
                self._ensure_authors_table()
        except BaseException:
            with suppress(Exception):
                self.conn.close()
            raise

    def _ensure_tag_table(self) -> None:
        """One row per saved tag, so tag searches and autocomplete skip parsing JSON.

        The triggers only queue the tweet ids whose tags changed: a trigger calling the
        Python tag-key function would make every write to the archive fail on a connection
        that hasn't registered it, such as the sqlite3 shell or the legacy importer. This
        store recomputes queued tweets before each write commits and when it opens.
        """
        exists = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'tweetnook_tags'"
        ).fetchone()
        media_tag = f"'{RecordType.MEDIA_TAG}'"
        with self.transaction():
            if not exists:
                self.conn.execute(
                    "CREATE TABLE tweetnook_tags (tweet_id TEXT NOT NULL, position INTEGER "
                    "NOT NULL, tag TEXT NOT NULL, tag_key TEXT NOT NULL, counted INTEGER "
                    "NOT NULL, PRIMARY KEY (tweet_id, position)) WITHOUT ROWID"
                )
                self.conn.execute(
                    "CREATE INDEX tweetnook_tags_key ON tweetnook_tags (tag_key, tweet_id)"
                )
                self.conn.execute(
                    "CREATE INDEX tweetnook_tags_counted ON tweetnook_tags (counted, tag_key, tag)"
                )
                self.conn.execute(f"INSERT OR IGNORE INTO tweetnook_tags {self._tag_rows_sql()}")
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS tweetnook_tags_pending "
                "(tweet_id TEXT PRIMARY KEY) WITHOUT ROWID"
            )
            for event in ("insert", "delete", "update"):
                self.conn.execute(f"DROP TRIGGER IF EXISTS tweetnook_tags_{event}")
            for event, row in (("INSERT", "new"), ("DELETE", "old")):
                self.conn.execute(
                    f"CREATE TRIGGER tweetnook_tags_{event.lower()} AFTER {event} "
                    f"ON archive WHEN {row}.record_type = {media_tag} BEGIN "
                    f"INSERT OR IGNORE INTO tweetnook_tags_pending VALUES ({row}.tweet_id); END"
                )
            self.conn.execute(
                "CREATE TRIGGER tweetnook_tags_update AFTER UPDATE ON archive "
                f"WHEN old.record_type = {media_tag} OR new.record_type = {media_tag} BEGIN "
                "INSERT OR IGNORE INTO tweetnook_tags_pending VALUES (old.tweet_id); "
                "INSERT OR IGNORE INTO tweetnook_tags_pending VALUES (new.tweet_id); END"
            )
            self._sync_tag_table()

    @staticmethod
    def _tag_rows_sql(where: str = "") -> str:
        return (
            "SELECT tagged.tweet_id, CAST(item.key AS INTEGER), item.value, "
            "tweetnook_tag_key(item.value), "
            "(tagged.enrichment_state = 'done' OR tagged.enrichment_state IS NULL) "
            "FROM archive AS tagged INDEXED BY idx_archive_media_tag_lookup "
            "CROSS JOIN json_each(CASE WHEN json_valid(tagged.raw_json) "
            "THEN tagged.raw_json ELSE '{}' END, '$.tags') AS item "
            f"WHERE tagged.record_type = '{RecordType.MEDIA_TAG}' AND item.type = 'text'{where}"
        )

    def _sync_tag_table(self) -> None:
        # Runs on every write commit: each statement is an index probe when nothing is queued.
        pending = "SELECT tweet_id FROM tweetnook_tags_pending"
        self.conn.execute(f"DELETE FROM tweetnook_tags WHERE tweet_id IN ({pending})")
        self.conn.execute(
            "INSERT OR IGNORE INTO tweetnook_tags "
            + self._tag_rows_sql(f" AND tagged.tweet_id IN ({pending})")
        )
        self.conn.execute("DELETE FROM tweetnook_tags_pending")

    def _before_commit(self) -> None:
        # Opening runs migrations before the tag table exists.
        if getattr(self, "_tag_table_ready", False):
            self._sync_tag_table()
    def _ensure_authors_table(self) -> None:
        """Each handle's latest saved row, kept current by triggers on memberships."""
        exists = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'tweetnook_authors'"
        ).fetchone()
        author_filter = "author_username IS NOT NULL AND author_id IS NOT NULL AND author_id != ''"
        latest = (
            "SELECT LOWER(COALESCE(author_username, '')), author_id, author_username, "
            "author_display_name, LOWER(author_username), LOWER(author_display_name) "
            # No INDEXED BY here: a trigger naming an index fails whenever a migration drops
            # or rebuilds it. The handle expression matches idx_archive_search_author anyway.
            f"FROM archive WHERE record_type = '{RecordType.TWEET}' AND {author_filter} "
            "AND LOWER(COALESCE(author_username, '')) = {key} "
            "ORDER BY created_at_ts DESC LIMIT 1"
        )

        def refresh(row: str) -> str:
            key = f"LOWER(COALESCE({row}.author_username, ''))"
            return (
                f"DELETE FROM tweetnook_authors WHERE name_key = {key}; "
                f"INSERT OR REPLACE INTO tweetnook_authors {latest.format(key=key)}; "
            )

        tweet = f"'{RecordType.TWEET}'"
        with self.transaction():
            if not exists:
                self.conn.execute(
                    "CREATE TABLE tweetnook_authors (name_key TEXT PRIMARY KEY, author_id TEXT, "
                    "author_username TEXT, author_display_name TEXT, username_key TEXT, "
                    "display_key TEXT) WITHOUT ROWID"
                )
                self.conn.execute(
                    "INSERT OR REPLACE INTO tweetnook_authors "
                    "SELECT handles.name_key, saved.author_id, saved.author_username, "
                    "saved.author_display_name, LOWER(saved.author_username), "
                    "LOWER(saved.author_display_name) "
                    "FROM (SELECT DISTINCT LOWER(COALESCE(author_username, '')) AS name_key "
                    f"FROM archive WHERE record_type = {tweet} AND {author_filter}) AS handles "
                    "JOIN archive AS saved ON saved.row_key = ("
                    "SELECT row_key FROM archive INDEXED BY idx_archive_search_author "
                    f"WHERE record_type = {tweet} AND {author_filter} "
                    "AND LOWER(COALESCE(author_username, '')) = handles.name_key "
                    "ORDER BY created_at_ts DESC LIMIT 1)"
                )
            self.conn.execute(
                "CREATE TRIGGER IF NOT EXISTS tweetnook_authors_insert AFTER INSERT ON archive "
                f"WHEN new.record_type = {tweet} BEGIN {refresh('new')}END"
            )
            self.conn.execute(
                "CREATE TRIGGER IF NOT EXISTS tweetnook_authors_delete AFTER DELETE ON archive "
                f"WHEN old.record_type = {tweet} BEGIN {refresh('old')}END"
            )
            self.conn.execute(
                "CREATE TRIGGER IF NOT EXISTS tweetnook_authors_update AFTER UPDATE OF "
                "record_type, author_id, author_username, author_display_name, created_at_ts "
                f"ON archive WHEN old.record_type = {tweet} OR new.record_type = {tweet} "
                f"BEGIN {refresh('old')}{refresh('new')}END"
            )

    # --- availability checks and community-note history ------------------

    def open_reader(self) -> ArchiveStore:
        """Open a read-only store on its own connection for long multi-query reads.

        SQLite holds a connection's mutex while each statement steps, so a long
        aggregate on the shared connection stalls every other Web request. The
        caller owns the reader and must close it.
        """
        reader = ArchiveStore(self.db_path, create=False, config=self.config, read_only=True)
        # Closing this archive stops reader queries too, so shutdown never waits on them.
        reader.conn.set_progress_handler(lambda: self._warm_cancelled, 10_000)
        return reader

    def pooled_reader(self) -> ArchiveStore:
        """This thread's long-lived read-only store, kept open between requests.

        Opening a reader registers its SQL functions and starts with a cold page cache;
        reusing one per worker thread pays that once.
        """
        if self._read_only:
            return self
        reader = getattr(self._reader_pool, "reader", None)
        if reader is None or reader._closed:
            reader = self.open_reader()
            self._reader_pool.reader = reader
            with self._transaction_lock:
                self._readers.append(reader)
        return reader

    def close(self) -> None:
        with self._transaction_lock:
            readers, self._readers = self._readers, []
        for reader in readers:
            with suppress(Exception):
                reader.close()
        super().close()

    @contextmanager
    def read_snapshot(self) -> Iterator[ArchiveStore]:
        """Use a private reader, borrowing a caller's uncommitted transaction unchanged."""
        with self._transaction_lock:
            if self.conn.in_transaction:
                yield self
                return
            reader = self.open_reader()
        try:
            with reader.transaction(write=False):
                yield reader
        finally:
            reader.close()


def open_archive_store(
    paths: XDGPaths, *, create: bool, config: AppConfig | None = None
) -> ArchiveStore | None:
    if not create and not paths.database_path.exists():
        return None
    migration_lock = None
    if paths.database_path.exists() and _database_requires_schema_migration(paths.database_path):
        from tweetnook.core.locking import ProcessLock

        migration_lock = ProcessLock(paths.lock_file)
        migration_lock.acquire(reentrant=True)
    try:
        return ArchiveStore(paths.database_path, create=create, config=config)
    except FileNotFoundError:
        return None
    finally:
        if migration_lock is not None:
            migration_lock.release()


def _database_requires_schema_migration(db_path: Path) -> bool:
    connection = sqlite3.connect(db_path)
    try:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        return version != SCHEMA_VERSION
    finally:
        connection.close()
