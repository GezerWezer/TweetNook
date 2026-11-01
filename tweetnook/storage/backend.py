"""Native SQLite archive storage backend."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

from tweetnook.config import DatabaseConfig, XDGPaths

from .authors import AuthorsMixin
from .checks import ChecksMixin
from .conversations import ConversationsMixin
from .db import ConnectionMixin
from .details import DetailsMixin
from .enrichment import EnrichmentMixin
from .imports import ImportsMixin
from .ingestion import IngestionMixin
from .objects import ObjectsMixin
from .presentation import PresentationMixin
from .prune import PruneMixin
from .reads import ReadsMixin
from .records import RecordsMixin
from .relations import RelationsMixin
from .report_facts import ReportFactsMixin
from .schema import SCHEMA_VERSION, MigrationReport, SchemaMixin
from .search import SearchMixin
from .sql_functions import (
    register_export_functions,
    register_identity_functions,
    register_random_function,
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
    TABLE_NAME = "archive"

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
        # TEMP like paging shares transaction state with archive mutations.
        self._like_order_lock = self._transaction_lock
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
        self.migration_report: MigrationReport | None = None
        if create:
            db_path.parent.mkdir(parents=True, exist_ok=True)

        db_file = str(db_path)
        if read_only:
            # A private reader for one thread; the writer that opened the
            # archive already chose WAL and migrated the schema.
            self.conn = sqlite3.connect(f"file:{quote(db_file)}?mode=ro", uri=True)
        else:
            # Shared Web readers can race CPython's prepared-statement cache even
            # with serialized SQLite (python/cpython#118172). Keep the database page
            # cache, but prepare independent statements for concurrent cursors.
            self.conn = sqlite3.connect(db_file, check_same_thread=False, cached_statements=0)
        try:
            self.conn.row_factory = sqlite3.Row
            register_export_functions(self.conn)
            register_random_function(self.conn)
            register_identity_functions(self.conn)
            if not read_only:
                self.conn.execute("PRAGMA journal_mode=WAL")
                self.conn.execute("PRAGMA recursive_triggers=ON")

            database_config = config.database if config is not None else DatabaseConfig()
            self.conn.execute(
                f"PRAGMA cache_size = -{database_config.cache_size_kb}"
            )  # Negative for kibibytes
            self.conn.execute(f"PRAGMA mmap_size = {database_config.mmap_size_bytes}")

            if not read_only and (create or db_path.exists()):
                self._migrate_schema()
        except BaseException:
            with suppress(Exception):
                self.conn.close()
            raise

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
