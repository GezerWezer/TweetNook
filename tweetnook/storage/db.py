"""Queries, cache revisions, read warming and maintenance on the archive connection."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from .record_types import RecordType

if TYPE_CHECKING:
    import threading
    from pathlib import Path

    from tweetnook.config import AppConfig

    from .schema import MigrationReport


# Pages read by browsing and search: the contentless FTS index blobs, membership
# rows, and the canonical objects that availability overlays onto them. LENGTH()
# loads TEXT values, including overflow pages; FTS blobs are fetched instead.
_WARM_READ_QUERIES = (
    "SELECT block FROM archive_fts_data",
    "SELECT SUM(LENGTH(raw_json)) FROM archive INDEXED BY idx_archive_membership "
    f"WHERE record_type = '{RecordType.TWEET}'",
    "SELECT SUM(LENGTH(object.raw_json)) "
    "FROM archive AS membership INDEXED BY idx_archive_membership "
    "JOIN archive AS object INDEXED BY idx_archive_tweet_id "
    "ON object.tweet_id = membership.tweet_id "
    f"WHERE membership.record_type = '{RecordType.TWEET}' AND object.record_type = "
    f"'{RecordType.TWEET_OBJECT}'",
)


class ConnectionMixin:
    # Set by ArchiveStore.__init__; declared here so the other storage mixins type-check.
    conn: sqlite3.Connection
    db_path: Path
    config: AppConfig | None
    migration_report: MigrationReport | None
    _closed: bool
    _warm_cancelled: bool
    _like_order_lock: threading.RLock
    _count_cache: dict[str, tuple[int, int]]
    _search_count_cache: dict[tuple, tuple[tuple[int, int], int]]

    def cache_revision(self, scope: str) -> int:
        return int(
            self.conn.execute(
                "SELECT generation FROM archive_revisions WHERE scope = ?", (scope,)
            ).fetchone()[0]
        )

    def check_integrity(self, *, full: bool = False) -> list[str]:
        """Run an explicit SQLite integrity diagnostic and return every result row."""
        pragma = "integrity_check" if full else "quick_check"
        return [str(row[0]) for row in self.conn.execute(f"PRAGMA {pragma}").fetchall()]

    def _query(
        self,
        expr: str,
        cols: list[str] | None = None,
        limit: int | None = None,
        is_fts: bool = False,
        query: str | None = None,
        order_by: str | None = None,
        offset: int | None = None,
        indexed_by: str | None = None,
    ) -> list[dict[str, Any]]:
        allowed_indexes = {
            "idx_archive_tweet_id",
            "idx_archive_target_tweet_id",
        }
        if indexed_by is not None and indexed_by not in allowed_indexes:
            raise ValueError(f"Unsupported archive query index: {indexed_by}")
        if indexed_by is not None and is_fts:
            raise ValueError("Archive index hints cannot be combined with FTS queries")

        c = ", ".join(cols) if cols else "*"
        archive_source = "archive"
        if indexed_by is not None:
            archive_source += f" INDEXED BY {indexed_by}"
        q = f"SELECT {c} FROM {archive_source}"
        params = []
        if is_fts and query:
            if c != "*":
                c += ", archive_fts.rank AS rank"
            else:
                c = "*, archive_fts.rank AS rank"
            q = (
                f"SELECT {c} FROM archive "
                "JOIN archive_fts ON archive.rowid = archive_fts.rowid "
                "WHERE archive_fts MATCH ?"
            )
            params.append(query)

        if expr:
            if "WHERE" in q:
                q += f" AND ({expr})"
            else:
                q += f" WHERE {expr}"

        if is_fts and query:
            q += " ORDER BY archive_fts.rank"
        elif order_by:
            q += f" ORDER BY {order_by}"

        if limit is not None:
            q += " LIMIT ?"
            params.append(limit)
            if offset is not None:
                q += " OFFSET ?"
                params.append(offset)

        return [dict(row) for row in self.conn.execute(q, params).fetchall()]

    def _count(self, filter_expr: str | None = None) -> int:
        if not filter_expr:
            return self.conn.execute("SELECT COUNT(*) FROM archive").fetchone()[0]
        return self.conn.execute(f"SELECT COUNT(*) FROM archive WHERE {filter_expr}").fetchone()[0]

    def _delete(self, filter_expr: str) -> None:
        with self.conn:
            self.conn.execute(f"DELETE FROM archive WHERE {filter_expr}")

    def close(self) -> None:
        if self._closed:
            return
        self.interrupt_warm_reads()
        try:
            # Read-only/current-schema opens stay cheap. Writers refresh planner
            # statistics at the end of their job, including direct SQL tag writes.
            if self.conn.total_changes and not self.conn.in_transaction:
                self.conn.execute("PRAGMA optimize")
        finally:
            self._closed = True
            self.conn.close()

    def apply_config(self, config: AppConfig) -> None:
        """Apply connection tuning and use this snapshot for future private readers."""
        if self.config is None or self.config.database != config.database:
            self.conn.execute(f"PRAGMA cache_size = -{config.database.cache_size_kb}")
            self.conn.execute(f"PRAGMA mmap_size = {config.database.mmap_size_bytes}")
        self.config = config

    def warm_read_cache(self) -> None:
        """Read the pages Web browsing and search use so the OS page cache holds them.

        Uses a separate read-only connection, so it never queues behind or holds the
        shared connection. interrupt_warm_reads() and close() stop it mid-query.
        """
        if self._warm_cancelled:
            return
        conn = sqlite3.connect(f"file:{quote(str(self.db_path))}?mode=ro", uri=True)
        # Polled while queries run, so a stop requested at any moment takes effect.
        conn.set_progress_handler(lambda: self._warm_cancelled, 10_000)
        try:
            for sql in _WARM_READ_QUERIES:
                for _row in conn.execute(sql):
                    pass
        except sqlite3.OperationalError:
            pass  # Stopped, or the archive is busy; warming is best-effort.
        finally:
            conn.close()

    def interrupt_warm_reads(self) -> None:
        """Stop current and future warm_read_cache() and open_reader() queries promptly."""
        self._warm_cancelled = True

    def version_count(self) -> int:
        return 1

    def optimize(self, *, cleanup: bool = True) -> None:
        self.conn.execute("VACUUM")
        self.conn.execute("PRAGMA optimize=0x10002")
        with self.conn:
            self.conn.execute("INSERT INTO archive_fts(archive_fts) VALUES ('optimize')")
            self.conn.execute(
                "INSERT INTO archive_description_fts(archive_description_fts) VALUES ('optimize')"
            )
