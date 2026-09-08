"""Native SQLite archive storage backend."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tweetnook.client.timelines import TimelineTweet, parse_tweet_detail_tweets
from tweetnook.config import DatabaseConfig, XDGPaths
from tweetnook.exceptions import ArchiveOwnerMismatchError
from tweetnook.extractor import (
    ExtractedTweetGraph,
    extract_author_fields,
    extract_canonical_text,
    extract_note_tweet_text,
    extract_secondary_objects,
    extract_status_id_from_url,
    extract_thread_objects,
)
from tweetnook.utils import utc_now

if TYPE_CHECKING:
    from tweetnook.config import AppConfig


def _folder_key(folder_id: str | None) -> str:
    return folder_id or ""


def _expr_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _expr_in(field: str, values: set[str]) -> str:
    return f"{field} IN ({', '.join(_expr_quote(value) for value in sorted(values))})"


def _pending_state_expr(field: str) -> str:
    return f"({field} IS NULL OR {field} = '' OR {field} = 'pending')"


def _state_filter_expr(field: str, states: set[str]) -> str:
    clauses: list[str] = []
    explicit_states = {state for state in states if state != "pending"}
    if explicit_states:
        clauses.append(_expr_in(field, explicit_states))
    if "pending" in states:
        clauses.append(_pending_state_expr(field))
    if not clauses:
        raise ValueError("state filter requires at least one state")
    return clauses[0] if len(clauses) == 1 else f"({' OR '.join(clauses)})"


def _and_expr(*clauses: str) -> str:
    return " AND ".join(clause for clause in clauses if clause)


def _parse_created_at(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y")
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class SyncState:
    collection_type: str
    last_head_tweet_id: str | None = None
    backfill_cursor: str | None = None
    backfill_incomplete: bool = False
    updated_at: str | None = None


@dataclass(slots=True)
class _PageBuffer:
    records: dict[str, dict[str, Any]] = field(default_factory=dict)
    pending_tweets: dict[str, TimelineTweet] = field(default_factory=dict)
    existing_rows: dict[str, dict[str, Any] | None] = field(default_factory=dict)

    def checkpoint(
        self,
    ) -> tuple[
        dict[str, dict[str, Any]],
        dict[str, TimelineTweet],
        dict[str, dict[str, Any] | None],
    ]:
        return self.records.copy(), self.pending_tweets.copy(), self.existing_rows.copy()

    def restore(
        self,
        checkpoint: tuple[
            dict[str, dict[str, Any]],
            dict[str, TimelineTweet],
            dict[str, dict[str, Any] | None],
        ],
    ) -> None:
        records, pending_tweets, existing_rows = checkpoint
        self.records.clear()
        self.records.update(records)
        self.pending_tweets.clear()
        self.pending_tweets.update(pending_tweets)
        self.existing_rows.clear()
        self.existing_rows.update(existing_rows)


@dataclass(slots=True)
class _RecordContext:
    existing: dict[str, Any] | None
    now: str
    first_seen_at: str
    added_at: str


@dataclass(slots=True)
class RehydrateResult:
    tweets_updated: int = 0
    secondary_records: int = 0


@dataclass(slots=True)
class MigrationReport:
    from_version: int
    to_version: int
    backup_path: Path | None
    search_index_rebuilt: bool = False
    legacy_terminal_rows_scanned: int = 0
    content_rows_repaired: int = 0
    author_rows_repaired: int = 0
    rows_still_missing_author: int = 0
    rows_without_richer_source: int = 0


@dataclass(slots=True)
class ArchiveCollectionStats:
    collection_type: str
    post_count: int = 0
    oldest_created_at: str | None = None
    newest_created_at: str | None = None
    last_synced_at: str | None = None
    backfill_cursor: str | None = None
    backfill_incomplete: bool = False


@dataclass(slots=True)
class ArchiveStats:
    owner_user_id: str | None = None
    unique_post_count: int = 0
    collection_membership_count: int = 0
    article_count: int = 0
    raw_capture_count: int = 0
    media_count: int = 0
    url_count: int = 0
    oldest_created_at: str | None = None
    newest_created_at: str | None = None
    latest_capture_at: str | None = None
    latest_sync_at: str | None = None
    version_count: int = 0
    collections: list[ArchiveCollectionStats] = field(default_factory=list)
    pending_enrichment_count: int = 0
    transient_enrichment_failure_count: int = 0
    transient_enrichment_due_count: int = 0
    transient_enrichment_delayed_count: int = 0
    terminal_enrichment_count: int = 0
    resurrected_enrichment_count: int = 0
    done_enrichment_count: int = 0
    retryable_unavailable_count: int = 0
    permanent_unavailable_count: int = 0
    due_resurrection_count: int = 0
    preview_article_count: int = 0
    missing_tweet_object_count: int = 0
    expanded_thread_target_count: int = 0
    pending_thread_membership_count: int = 0
    pending_thread_linked_status_count: int = 0


ARCHIVE_COLUMNS = [
    "row_key",
    "record_type",
    "tweet_id",
    "collection_type",
    "folder_id",
    "sort_index",
    "operation",
    "cursor_in",
    "cursor_out",
    "captured_at",
    "http_status",
    "source",
    "text",
    "author_id",
    "author_username",
    "author_display_name",
    "created_at",
    "created_at_ts",
    "deleted_at",
    "conversation_id",
    "lang",
    "note_tweet_text",
    "enrichment_state",
    "enrichment_checked_at",
    "enrichment_http_status",
    "enrichment_reason",
    "enrichment_detail",
    "enrichment_retry_count",
    "enrichment_next_retry_at",
    "enrichment_first_unavailable_at",
    "enrichment_retry_eligible",
    "raw_json",
    "first_seen_at",
    "last_seen_at",
    "added_at",
    "synced_at",
    "relation_type",
    "target_tweet_id",
    "position",
    "media_key",
    "media_type",
    "media_url",
    "thumbnail_url",
    "width",
    "height",
    "duration_millis",
    "variants_json",
    "download_state",
    "local_path",
    "provenance_source",
    "sha256",
    "byte_size",
    "content_type",
    "thumbnail_local_path",
    "thumbnail_sha256",
    "thumbnail_byte_size",
    "thumbnail_content_type",
    "downloaded_at",
    "download_error",
    "url_hash",
    "url",
    "expanded_url",
    "final_url",
    "canonical_url",
    "display_url",
    "url_host",
    "description",
    "site_name",
    "unfurl_state",
    "last_fetched_at",
    "article_id",
    "title",
    "summary_text",
    "content_text",
    "published_at",
    "status",
    "archive_digest",
    "archive_generation_date",
    "import_started_at",
    "import_completed_at",
    "warnings_json",
    "counts_json",
    "enrichment_followup_status",
    "enrichment_aborted_reason",
    "last_head_tweet_id",
    "backfill_cursor",
    "backfill_incomplete",
    "updated_at",
    "key",
    "value",
]

SCHEMA_VERSION = 8
COLUMN_TYPES = {
    field: (
        "TEXT PRIMARY KEY"
        if field == "row_key"
        else "INTEGER"
        if field
        in {
            "created_at_ts",
            "enrichment_retry_count",
            "enrichment_retry_eligible",
        }
        else "TEXT"
    )
    for field in ARCHIVE_COLUMNS
}
ENRICHMENT_INDEXES = {
    "idx_archive_enrichment_due": (
        "record_type, enrichment_state, enrichment_retry_eligible, enrichment_next_retry_at"
    ),
    "idx_archive_dead_author": (
        "record_type, enrichment_state, author_id, enrichment_retry_eligible, "
        "enrichment_next_retry_at"
    ),
    "idx_archive_resurrection_reason_due": (
        "record_type, enrichment_state, enrichment_retry_eligible, enrichment_reason, "
        "enrichment_next_retry_at"
    ),
    "idx_archive_capture_target": "record_type, cursor_in, captured_at DESC",
}

SECONDARY_RECORD_TYPES = ("tweet_object", "tweet_relation", "media", "url", "url_ref", "article")
LIVE_SOURCE = "live_graphql"
ARCHIVE_SOURCE = "x_archive"
AVAILABLE_ENRICHMENT_STATES = ("done", "resurrected")
SEARCH_KIND_POST = "post"
SEARCH_KIND_ARTICLE = "article"
SEARCH_COLLECTION_ORDER = ("bookmark", "like", "tweet")
SEARCH_TEXT_FIELD = "text"
_UNSET = object()


class ArchiveStore:
    TABLE_NAME = "archive"

    def __init__(self, db_path: Path, *, create: bool, config: AppConfig | None = None) -> None:
        self.db_path = db_path
        self._closed = False
        self._like_order_lock = threading.RLock()
        self._count_cache: dict[str, tuple[int, int]] = {}
        self._search_count_cache: dict[tuple, tuple[tuple[int, int], int]] = {}
        self.migration_report: MigrationReport | None = None
        if create:
            db_path.parent.mkdir(parents=True, exist_ok=True)

        db_file = str(db_path)
        self.conn = sqlite3.connect(db_file, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA recursive_triggers=ON")

        database_config = config.database if config is not None else DatabaseConfig()
        self.conn.execute(
            f"PRAGMA cache_size = -{database_config.cache_size_kb}"
        )  # Negative for kibibytes
        self.conn.execute(f"PRAGMA mmap_size = {database_config.mmap_size_bytes}")

        if create or db_path.exists():
            try:
                self._migrate_schema()
            except BaseException:
                self.conn.close()
                raise

    def _migrate_schema(self) -> None:
        current_version = self._get_schema_version()
        if current_version > SCHEMA_VERSION:
            raise RuntimeError(
                f"Archive schema version {current_version} is newer than this build supports "
                f"({SCHEMA_VERSION})."
            )
        if current_version == SCHEMA_VERSION:
            return

        if not self._archive_table_exists():
            self._create_latest_schema()
            return

        if current_version in {3, 4, 5}:
            self._migrate_search_index(current_version)
            return

        if current_version in {6, 7}:
            self._migrate_search_support_indexes(current_version)
            return

        self._migrate_legacy_database(current_version)

    def _create_latest_schema(self) -> None:
        col_def = ", ".join(f"{name} {COLUMN_TYPES[name]}" for name in ARCHIVE_COLUMNS)
        with self.conn:
            self.conn.execute(f"CREATE TABLE archive ({col_def})")
            self._create_fts_schema()
            self._create_archive_indexes()
            self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.conn.execute("PRAGMA optimize=0x10002")

    def _migrate_legacy_database(self, current_version: int) -> None:
        self._require_quick_check("before migration")
        before_counts = self._row_counts_by_type()
        sample_keys = [
            row[0]
            for row in self.conn.execute(
                "SELECT row_key FROM archive ORDER BY row_key LIMIT 5"
            ).fetchall()
        ]
        backup_path = self._backup_before_migration(SCHEMA_VERSION)

        with self.conn:
            self._add_missing_archive_columns()
            self._backfill_created_at_timestamps()
            self._rebuild_fts_schema()
            self._backfill_enrichment_scheduler()
            self._clear_available_enrichment_scheduler()
            self._create_archive_indexes()
            repair_counts = self._repair_legacy_terminal_rows()
            self._validate_migration(before_counts, sample_keys)
            self._require_quick_check("after migration")
            self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

        self.migration_report = MigrationReport(
            from_version=current_version,
            to_version=SCHEMA_VERSION,
            backup_path=backup_path,
            **repair_counts,
        )

    def _migrate_search_index(self, current_version: int) -> None:
        """Repair derived FTS documents/triggers without copying canonical archive data."""
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            self._rebuild_fts_schema()
            self._create_archive_indexes()
            expected = self.conn.execute(
                "SELECT COUNT(*) FROM archive WHERE record_type = 'tweet'"
            ).fetchone()[0]
            actual = self.conn.execute("SELECT COUNT(*) FROM archive_fts").fetchone()[0]
            if actual != expected:
                raise RuntimeError(
                    "Search-index migration produced an unexpected row count: "
                    f"expected={expected}, actual={actual}"
                )
            self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

        self.migration_report = MigrationReport(
            from_version=current_version,
            to_version=SCHEMA_VERSION,
            backup_path=None,
            search_index_rebuilt=True,
        )

    def _migrate_search_support_indexes(self, current_version: int) -> None:
        """Add derived search indexes without copying canonical archive data."""
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            self._create_archive_indexes()
            self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.conn.execute("PRAGMA optimize=0x10002")

        self.migration_report = MigrationReport(
            from_version=current_version,
            to_version=SCHEMA_VERSION,
            backup_path=None,
        )

    def _archive_table_exists(self) -> bool:
        return (
            self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'archive'"
            ).fetchone()
            is not None
        )

    def _get_schema_version(self) -> int:
        return int(self.conn.execute("PRAGMA user_version").fetchone()[0])

    def _archive_column_names(self) -> set[str]:
        if not self._archive_table_exists():
            return set()
        return {row[1] for row in self.conn.execute("PRAGMA table_info(archive)").fetchall()}

    def _add_missing_archive_columns(self) -> None:
        existing = self._archive_column_names()
        for column in ARCHIVE_COLUMNS:
            if column in existing:
                continue
            column_type = COLUMN_TYPES.get(column)
            if column_type is None:
                raise ValueError(f"Unknown archive column: {column}")
            if column == "row_key":
                raise RuntimeError("Existing archive table is missing its row_key primary key.")
            self.conn.execute(f"ALTER TABLE archive ADD COLUMN {column} {column_type}")

    def _require_quick_check(self, stage: str) -> None:
        result = self.conn.execute("PRAGMA quick_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"SQLite quick_check failed {stage}: {result}")

    def _row_counts_by_type(self) -> dict[str | None, int]:
        return {
            row[0]: int(row[1])
            for row in self.conn.execute(
                "SELECT record_type, COUNT(*) FROM archive GROUP BY record_type"
            ).fetchall()
        }

    def _backup_before_migration(self, target_version: int) -> Path:
        timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        backup_path = self.db_path.with_name(
            f"{self.db_path.name}.pre-schema-v{target_version}.{timestamp}.bak"
        )
        suffix = 1
        temporary_path = backup_path.with_suffix(f"{backup_path.suffix}.tmp")
        while backup_path.exists() or temporary_path.exists():
            backup_path = self.db_path.with_name(
                f"{self.db_path.name}.pre-schema-v{target_version}.{timestamp}.{suffix}.bak"
            )
            temporary_path = backup_path.with_suffix(f"{backup_path.suffix}.tmp")
            suffix += 1
        destination: sqlite3.Connection | None = None
        try:
            destination = sqlite3.connect(temporary_path)
            self.conn.backup(destination)
            destination.close()
            destination = None
            validation = sqlite3.connect(temporary_path)
            try:
                check = validation.execute("PRAGMA quick_check").fetchone()[0]
            finally:
                validation.close()
            if check != "ok":
                raise RuntimeError(f"SQLite backup quick_check failed: {check}")
            temporary_path.replace(backup_path)
        except Exception as exc:
            if temporary_path.exists():
                temporary_path.unlink()
            raise RuntimeError(
                f"Failed to create validated pre-migration backup at {backup_path}."
            ) from exc
        finally:
            if destination is not None:
                try:
                    destination.close()
                except sqlite3.Error:
                    pass
        return backup_path

    def _backfill_created_at_timestamps(self) -> None:
        remaining = self.conn.execute(
            "SELECT row_key, created_at FROM archive "
            "WHERE created_at IS NOT NULL AND created_at_ts IS NULL"
        ).fetchall()
        updates = []
        for row in remaining:
            created_at = _parse_created_at(row[1])
            if created_at is not None:
                updates.append((int(created_at.timestamp()), row[0]))
        if updates:
            self.conn.executemany("UPDATE archive SET created_at_ts = ? WHERE row_key = ?", updates)

    def _create_fts_schema(self, *, populate: bool = False) -> None:
        self.conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS archive_fts USING fts5(
                author_username, author_display_name, text, note_tweet_text,
                content=''
            )
        """)
        self.conn.execute("""
        CREATE TRIGGER IF NOT EXISTS archive_ad AFTER DELETE ON archive
        WHEN old.record_type = 'tweet' BEGIN
          INSERT INTO archive_fts(
            archive_fts, rowid, author_username, author_display_name, text, note_tweet_text
          ) VALUES(
            'delete', old.rowid, old.author_username, old.author_display_name,
            old.text, old.note_tweet_text
          );
        END;
        """)
        self.conn.execute("""
        CREATE TRIGGER IF NOT EXISTS archive_ai AFTER INSERT ON archive
        WHEN new.record_type = 'tweet' BEGIN
          INSERT INTO archive_fts(
            rowid, author_username, author_display_name, text, note_tweet_text
          ) VALUES(
            new.rowid, new.author_username, new.author_display_name,
            new.text, new.note_tweet_text
          );
        END;
        """)
        self.conn.execute("""
        CREATE TRIGGER IF NOT EXISTS archive_au AFTER UPDATE ON archive
        WHEN (old.record_type = 'tweet' OR new.record_type = 'tweet')
          AND (old.record_type IS NOT new.record_type
            OR old.author_username IS NOT new.author_username
            OR old.author_display_name IS NOT new.author_display_name
            OR old.text IS NOT new.text
            OR old.note_tweet_text IS NOT new.note_tweet_text)
        BEGIN
          INSERT INTO archive_fts(
            archive_fts, rowid, author_username, author_display_name, text, note_tweet_text
          ) SELECT
            'delete', old.rowid, old.author_username, old.author_display_name,
            old.text, old.note_tweet_text
          WHERE old.record_type = 'tweet';
          INSERT INTO archive_fts(
            rowid, author_username, author_display_name, text, note_tweet_text
          ) SELECT
            new.rowid, new.author_username, new.author_display_name,
            new.text, new.note_tweet_text
          WHERE new.record_type = 'tweet';
        END;
        """)
        if populate:
            self.conn.execute("""
                INSERT INTO archive_fts(
                    rowid, author_username, author_display_name, text, note_tweet_text
                )
                SELECT rowid, author_username, author_display_name, text, note_tweet_text
                FROM archive
                WHERE record_type = 'tweet'
            """)

    def _rebuild_fts_schema(self) -> None:
        for trigger in ("archive_ad", "archive_ai", "archive_au"):
            self.conn.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        self.conn.execute("DROP TABLE IF EXISTS archive_fts")
        self._create_fts_schema(populate=True)

    def _create_archive_indexes(self) -> None:
        index_sql = {
            "idx_archive_tweet_id": "tweet_id",
            "idx_archive_target_tweet_id": "target_tweet_id",
            "idx_archive_sort": (
                "collection_type, created_at_ts DESC, CAST(sort_index AS INTEGER) DESC"
            ),
            "idx_archive_record_sort": (
                "record_type, collection_type, created_at_ts DESC, tweet_id DESC"
            ),
            "idx_archive_record_page": (
                "record_type, created_at_ts DESC, CAST(sort_index AS INTEGER) DESC, tweet_id DESC"
            ),
            "idx_archive_record_collection_page": (
                "record_type, collection_type, created_at_ts DESC, "
                "CAST(sort_index AS INTEGER) DESC, tweet_id DESC"
            ),
            **ENRICHMENT_INDEXES,
        }
        for name, columns in index_sql.items():
            self.conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON archive({columns})")
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_archive_search_attachment
            ON archive(record_type, tweet_id, media_type)
            WHERE record_type IN ('media', 'url_ref')
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_archive_media_tag_lookup
            ON archive(tweet_id, raw_json)
            WHERE record_type = 'media_tag'
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_archive_membership
            ON archive(tweet_id, row_key, collection_type)
            WHERE record_type = 'tweet'
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_archive_search_author
            ON archive(LOWER(COALESCE(author_username, '')), created_at_ts DESC, tweet_id)
            WHERE record_type = 'tweet'
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_archive_profile_author
            ON archive(author_id)
            WHERE author_id IS NOT NULL AND author_id != ''
        """)
        self._create_cache_revisions()

    def _create_cache_revisions(self) -> None:
        """Track derived-data inputs across processes, including direct SQL writes."""
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS archive_revisions "
            "(scope TEXT PRIMARY KEY, generation INTEGER NOT NULL)"
        )
        self.conn.executemany(
            "INSERT OR IGNORE INTO archive_revisions VALUES (?, 0)",
            [("membership",), ("like_order",)],
        )
        scopes = {
            "membership": (
                "{row}.record_type = 'tweet'",
                ("record_type", "tweet_id", "collection_type"),
            ),
            "like_order": (
                "({row}.record_type = 'tweet' AND {row}.collection_type = 'like') OR "
                "({row}.record_type = 'raw_capture' AND {row}.operation IN "
                "('Likes', 'XArchiveLikes', 'XArchiveManifest')) OR "
                "{row}.record_type = 'import_manifest'",
                (
                    "record_type",
                    "tweet_id",
                    "collection_type",
                    "sort_index",
                    "source",
                    "operation",
                    "raw_json",
                    "cursor_in",
                    "cursor_out",
                    "http_status",
                    "captured_at",
                    "archive_digest",
                    "archive_generation_date",
                    "import_started_at",
                    "row_key",
                ),
            ),
        }
        for scope, (relevant, columns) in scopes.items():
            for event in ("INSERT", "DELETE", "UPDATE"):
                rows = (
                    ("new", "old")
                    if event == "UPDATE"
                    else ("new" if event == "INSERT" else "old",)
                )
                condition = " OR ".join(f"({relevant.format(row=row)})" for row in rows)
                if event == "UPDATE":
                    changed = " OR ".join(f"old.{col} IS NOT new.{col}" for col in columns)
                    # Membership content/raw payload updates do not change like order.
                    if scope == "like_order":
                        membership_changed = " OR ".join(
                            f"old.{col} IS NOT new.{col}" for col in columns[:5]
                        )
                        changed = (
                            f"((old.record_type = 'tweet' AND new.record_type = 'tweet' "
                            f"AND ({membership_changed})) OR "
                            f"((old.record_type != 'tweet' OR new.record_type != 'tweet') "
                            f"AND ({changed})))"
                        )
                    condition = f"({condition}) AND ({changed})"
                self.conn.execute(
                    f"CREATE TRIGGER IF NOT EXISTS archive_revision_{scope}_{event.lower()} "
                    f"AFTER {event} ON archive WHEN {condition} BEGIN "
                    "UPDATE archive_revisions SET generation = generation + 1 "
                    f"WHERE scope = '{scope}'; END"
                )

    def cache_revision(self, scope: str) -> int:
        return int(
            self.conn.execute(
                "SELECT generation FROM archive_revisions WHERE scope = ?", (scope,)
            ).fetchone()[0]
        )

    def _backfill_enrichment_scheduler(self) -> None:
        now = utc_now()
        self._preserve_unknown_enrichment_reasons()
        self.conn.execute(
            "UPDATE archive SET enrichment_reason = 'archive_deleted', "
            "enrichment_retry_eligible = 0, enrichment_next_retry_at = NULL, "
            "enrichment_retry_count = COALESCE(enrichment_retry_count, 0), "
            "enrichment_first_unavailable_at = COALESCE("
            "enrichment_first_unavailable_at, enrichment_checked_at, ?) "
            "WHERE record_type = 'tweet_object' AND enrichment_state = 'terminal_unavailable' "
            "AND deleted_at IS NOT NULL",
            (now,),
        )
        self.conn.execute(
            "UPDATE archive SET enrichment_reason = 'archive_deleted', "
            "enrichment_retry_eligible = 0, enrichment_next_retry_at = NULL, "
            "enrichment_retry_count = COALESCE(enrichment_retry_count, 0), "
            "enrichment_first_unavailable_at = COALESCE("
            "enrichment_first_unavailable_at, enrichment_checked_at, ?) "
            "WHERE record_type = 'tweet_object' AND enrichment_state = 'terminal_unavailable' "
            "AND source = ? AND LOWER(COALESCE(enrichment_reason, '')) = 'deleted'",
            (now, ARCHIVE_SOURCE),
        )
        self.conn.execute(
            "UPDATE archive SET enrichment_reason = 'deleted_by_author', "
            "enrichment_retry_eligible = 0, enrichment_next_retry_at = NULL, "
            "enrichment_retry_count = COALESCE(enrichment_retry_count, 0), "
            "enrichment_first_unavailable_at = COALESCE("
            "enrichment_first_unavailable_at, enrichment_checked_at, ?) "
            "WHERE record_type = 'tweet_object' AND enrichment_state = 'terminal_unavailable' "
            "AND LOWER(COALESCE(enrichment_reason, '')) IN "
            "('deleted_by_author', 'deleted by author')",
            (now,),
        )
        permanent = "('archive_deleted', 'deleted_by_author')"
        normalized = (
            "('archive_deleted', 'deleted_by_author', 'protected_account', "
            "'suspended_account', 'account_missing', 'withheld', 'not_found', "
            "'unavailable_unknown')"
        )
        self.conn.execute(
            "UPDATE archive SET "
            f"enrichment_reason = CASE WHEN enrichment_reason IN {normalized} "
            "THEN enrichment_reason ELSE 'unavailable_unknown' END, "
            "enrichment_retry_count = COALESCE(enrichment_retry_count, 0), "
            "enrichment_first_unavailable_at = COALESCE("
            "enrichment_first_unavailable_at, enrichment_checked_at, ?), "
            f"enrichment_retry_eligible = CASE WHEN enrichment_reason IN {permanent} "
            "THEN 0 ELSE 1 END, "
            f"enrichment_next_retry_at = CASE WHEN enrichment_reason IN {permanent} "
            "THEN NULL ELSE COALESCE(enrichment_next_retry_at, enrichment_checked_at, ?) END "
            "WHERE record_type = 'tweet_object' AND enrichment_state = 'terminal_unavailable'",
            (now, now),
        )
        self.conn.execute(
            "UPDATE archive SET enrichment_retry_count = COALESCE(enrichment_retry_count, 0), "
            "enrichment_next_retry_at = COALESCE(enrichment_next_retry_at, ?) "
            "WHERE record_type = 'tweet_object' AND enrichment_state = 'transient_failure'",
            (now,),
        )

    def _preserve_unknown_enrichment_reasons(self) -> None:
        normalized = {
            "archive_deleted",
            "deleted",
            "deleted by author",
            "deleted_by_author",
            "protected_account",
            "suspended_account",
            "account_missing",
            "withheld",
            "not_found",
            "unavailable_unknown",
        }
        rows = self.conn.execute(
            "SELECT row_key, enrichment_reason, enrichment_detail FROM archive "
            "WHERE record_type = 'tweet_object' "
            "AND enrichment_state = 'terminal_unavailable' "
            "AND enrichment_reason IS NOT NULL AND enrichment_reason != ''"
        ).fetchall()
        updates: list[tuple[str, str]] = []
        for row_key, reason, detail in rows:
            if str(reason).casefold() in normalized:
                continue
            diagnostic = f"Legacy enrichment reason: {reason}"
            preserved_detail = str(detail) if detail else ""
            if diagnostic not in preserved_detail:
                preserved_detail = (
                    f"{preserved_detail}\n{diagnostic}" if preserved_detail else diagnostic
                )
            updates.append((preserved_detail, row_key))
        if updates:
            self.conn.executemany(
                "UPDATE archive SET enrichment_detail = ? WHERE row_key = ?",
                updates,
            )

    def _clear_available_enrichment_scheduler(self) -> None:
        available_states = ", ".join(_expr_quote(state) for state in AVAILABLE_ENRICHMENT_STATES)
        self.conn.execute(
            "UPDATE archive SET enrichment_reason = NULL, enrichment_detail = NULL, "
            "enrichment_retry_count = 0, enrichment_next_retry_at = NULL, "
            "enrichment_first_unavailable_at = NULL, enrichment_retry_eligible = 0 "
            "WHERE record_type = 'tweet_object' "
            f"AND enrichment_state IN ({available_states})"
        )

    def _legacy_recovery_quality(self, values: tuple[Any, ...]) -> tuple[int, int]:
        text, author_id, username, display_name, created_at = values[:5]
        conversation_id, lang, note_text, raw_json, source = (
            values[6],
            values[7],
            values[8],
            values[9],
            values[10],
        )
        content_score = sum(
            (
                50 if author_id else 0,
                20 if text else 0,
                8 if username else 0,
                5 if display_name else 0,
                4 if created_at else 0,
                3 if conversation_id else 0,
                2 if lang else 0,
                6 if note_text else 0,
                10 if raw_json and "__tombstone__" not in str(raw_json) else 0,
            )
        )
        return content_score, 1 if source == LIVE_SOURCE else 0

    def _tweet_recovery_values(
        self,
        tweet: TimelineTweet,
        *,
        source: str,
    ) -> tuple[Any, ...]:
        legacy = tweet.raw_json.get("legacy") or {}
        created_at = _parse_created_at(tweet.created_at)
        return (
            tweet.text,
            tweet.author_id,
            tweet.author_username,
            tweet.author_display_name,
            tweet.created_at,
            int(created_at.timestamp()) if created_at else None,
            legacy.get("conversation_id_str"),
            legacy.get("lang"),
            extract_note_tweet_text(tweet.raw_json),
            self._json_value(tweet.raw_json),
            source,
        )

    def _repair_legacy_terminal_rows(
        self,
    ) -> dict[str, int]:
        query = (
            "SELECT row_key, tweet_id FROM archive "
            "WHERE record_type = 'tweet_object' AND enrichment_state = 'terminal_unavailable' "
            "AND COALESCE(text, '') = '' AND author_id IS NULL "
            "AND raw_json LIKE '%__tombstone__%' ORDER BY tweet_id"
        )
        suspicious = self.conn.execute(query).fetchall()

        recovered_rows = 0
        content_repaired = 0
        authors_repaired = 0
        for row_key, tweet_id in suspicious:
            memberships = self.conn.execute(
                "SELECT text, author_id, author_username, author_display_name, created_at, "
                "created_at_ts, conversation_id, lang, note_tweet_text, raw_json, source "
                "FROM archive WHERE record_type = 'tweet' AND tweet_id = ? ",
                (tweet_id,),
            ).fetchall()
            membership_values = [tuple(candidate) for candidate in memberships]

            capture_values: list[tuple[Any, ...]] = []
            captures = self.conn.execute(
                "SELECT raw_json, source FROM archive WHERE record_type = 'raw_capture' "
                "AND cursor_in = ? AND raw_json IS NOT NULL ORDER BY captured_at DESC",
                (tweet_id,),
            ).fetchall()
            for capture in captures:
                try:
                    payload = json.loads(capture[0])
                except (TypeError, json.JSONDecodeError):
                    continue
                detail = next(
                    (
                        tweet
                        for tweet in parse_tweet_detail_tweets(payload)
                        if tweet.tweet_id == tweet_id
                    ),
                    None,
                )
                if detail is None:
                    continue
                capture_values.append(
                    self._tweet_recovery_values(
                        detail,
                        source=str(capture[1] or LIVE_SOURCE),
                    )
                )

            candidates = membership_values + capture_values
            if not candidates:
                continue
            values = max(candidates, key=self._legacy_recovery_quality)
            meaningful = bool(values[0] or values[1] or any(values[2:9]))
            if not meaningful:
                continue
            self.conn.execute(
                "UPDATE archive SET text = COALESCE(?, text), "
                "author_id = COALESCE(?, author_id), "
                "author_username = COALESCE(?, author_username), "
                "author_display_name = COALESCE(?, author_display_name), "
                "created_at = COALESCE(?, created_at), "
                "created_at_ts = COALESCE(?, created_at_ts), "
                "conversation_id = COALESCE(?, conversation_id), "
                "lang = COALESCE(?, lang), "
                "note_tweet_text = COALESCE(?, note_tweet_text), "
                "raw_json = COALESCE(?, raw_json) "
                "WHERE row_key = ?",
                (*values[:10], row_key),
            )
            recovered_rows += 1
            if values[0] or any(values[2:9]):
                content_repaired += 1
            if values[1]:
                authors_repaired += 1

        still_lacking_author = self.conn.execute(
            "SELECT COUNT(*) FROM archive "
            "WHERE record_type = 'tweet_object' "
            "AND enrichment_state = 'terminal_unavailable' "
            "AND author_id IS NULL AND raw_json LIKE '%__tombstone__%'"
        ).fetchone()[0]
        return {
            "legacy_terminal_rows_scanned": len(suspicious),
            "content_rows_repaired": content_repaired,
            "author_rows_repaired": authors_repaired,
            "rows_still_missing_author": int(still_lacking_author),
            "rows_without_richer_source": len(suspicious) - recovered_rows,
        }

    def rebuild_search_index(self) -> None:
        """Finalize timestamp and full-text indexes after an explicit bulk import."""
        with self.conn:
            self._backfill_created_at_timestamps()
            self._rebuild_fts_schema()

    def check_integrity(self, *, full: bool = False) -> list[str]:
        """Run an explicit SQLite integrity diagnostic and return every result row."""
        pragma = "integrity_check" if full else "quick_check"
        return [str(row[0]) for row in self.conn.execute(f"PRAGMA {pragma}").fetchall()]

    def _validate_migration(
        self,
        before_counts: dict[str | None, int],
        sample_keys: list[str],
    ) -> None:
        after_counts = self._row_counts_by_type()
        if before_counts != after_counts:
            raise RuntimeError(
                "Archive row counts changed during schema migration: "
                f"before={before_counts!r}, after={after_counts!r}"
            )
        missing = set(ARCHIVE_COLUMNS) - self._archive_column_names()
        if missing:
            raise RuntimeError(f"Archive migration left missing columns: {sorted(missing)}")
        indexes = {
            row[0]
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
        missing_indexes = set(ENRICHMENT_INDEXES) - indexes
        if missing_indexes:
            raise RuntimeError(f"Archive migration left missing indexes: {sorted(missing_indexes)}")
        for row_key in sample_keys:
            if (
                self.conn.execute("SELECT 1 FROM archive WHERE row_key = ?", (row_key,)).fetchone()
                is None
            ):
                raise RuntimeError(f"Archive migration lost sampled row {row_key!r}.")

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

    def _count_distinct(self, field: str, filter_expr: str | None = None) -> int:
        if field not in ARCHIVE_COLUMNS:
            raise ValueError(f"Unknown archive field: {field}")
        sql = f"SELECT COUNT(DISTINCT {field}) FROM archive"
        if filter_expr:
            sql += f" WHERE {filter_expr}"
        return self.conn.execute(sql).fetchone()[0]

    def _delete(self, filter_expr: str) -> None:
        with self.conn:
            self.conn.execute(f"DELETE FROM archive WHERE {filter_expr}")

    def close(self) -> None:
        if self._closed:
            return
        try:
            # Read-only/current-schema opens stay cheap. Writers refresh planner
            # statistics at the end of their job, including direct SQL tag writes.
            if self.conn.total_changes and not self.conn.in_transaction:
                self.conn.execute("PRAGMA optimize")
        finally:
            self._closed = True
            self.conn.close()

    def _record(self, **overrides: Any) -> dict[str, Any]:
        record = {field: None for field in ARCHIVE_COLUMNS}
        record.update(overrides)
        return record

    def _coalesce_value(self, *values: Any) -> Any:
        for value in values:
            if value is None:
                continue
            if isinstance(value, str) and not value:
                continue
            return value
        return None

    def _parse_bool(self, val: Any) -> bool:
        if val is None:
            return False
        if isinstance(val, bool):
            return val
        if isinstance(val, int | float):
            return bool(val)
        if isinstance(val, str):
            return val.lower() in ("1", "true", "yes", "t", "y")
        return bool(val)

    def _json_value(self, value: Any) -> str | None:
        if value is None:
            return None
        return json.dumps(value, sort_keys=True)

    def _merge_records(self, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        cols = list(ARCHIVE_COLUMNS)
        placeholders = ", ".join(["?"] * len(cols))
        col_names = ", ".join(cols)
        updates = ", ".join(f"{col} = excluded.{col}" for col in cols if col != "row_key")
        sql = (
            f"INSERT INTO archive ({col_names}) VALUES ({placeholders}) "
            f"ON CONFLICT(row_key) DO UPDATE SET {updates}"
        )
        params = []
        for record in records:
            row = []
            for col in cols:
                row.append(record.get(col))
            params.append(row)
        with self.conn:
            self.conn.executemany(sql, params)

    def merge_rows(self, rows: list[dict[str, Any]]) -> None:
        self._merge_records(rows)

    def _row_key_for_tweet(
        self, tweet_id: str, collection_type: str, folder_id: str | None = None
    ) -> str:
        return f"tweet:{collection_type}:{_folder_key(folder_id)}:{tweet_id}"

    def _row_key_for_sync_state(self, collection_type: str, folder_id: str | None = None) -> str:
        return f"sync_state:{collection_type}:{_folder_key(folder_id)}"

    def _row_key_for_metadata(self, key: str) -> str:
        return f"metadata:{key}"

    def _row_key_for_import_manifest(self, archive_digest: str) -> str:
        return f"import_manifest:{archive_digest}"

    def _row_key_for_tweet_object(self, tweet_id: str) -> str:
        return f"tweet_object:{tweet_id}"

    def _row_key_for_tweet_relation(
        self, source_tweet_id: str, relation_type: str, target_tweet_id: str
    ) -> str:
        return f"tweet_relation:{source_tweet_id}:{relation_type}:{target_tweet_id}"

    def _row_key_for_media(self, tweet_id: str, media_key: str) -> str:
        return f"media:{tweet_id}:{media_key}"

    def _row_key_for_url(self, url_hash: str) -> str:
        return f"url:{url_hash}"

    def _row_key_for_url_ref(self, tweet_id: str, position: int) -> str:
        return f"url_ref:{tweet_id}:{position}"

    def _row_key_for_article(self, tweet_id: str) -> str:
        return f"article:{tweet_id}"

    def _get_row(self, row_key: str) -> dict[str, Any] | None:
        rows = self._query(expr=f"row_key = {_expr_quote(row_key)}", limit=1)
        return rows[0] if rows else None

    def _rows_for_values(
        self,
        record_type: str,
        field_name: str,
        values: set[str] | list[str] | tuple[str, ...],
        *,
        columns: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if field_name not in ARCHIVE_COLUMNS:
            raise ValueError(f"Unknown archive field: {field_name}")
        unique_values = [value for value in dict.fromkeys(values) if value]
        if not unique_values:
            return []
        if record_type == "url" and field_name == "url_hash":
            unique_values = [self._row_key_for_url(value) for value in unique_values]
            field_name = "row_key"
        rows: list[dict[str, Any]] = []
        chunk_size = 100
        for start in range(0, len(unique_values), chunk_size):
            chunk = unique_values[start : start + chunk_size]
            value_index = {
                "tweet_id": "idx_archive_tweet_id",
                "target_tweet_id": "idx_archive_target_tweet_id",
                "row_key": "sqlite_autoindex_archive_1",
            }.get(field_name)
            source = f"archive INDEXED BY {value_index}" if value_index else "archive"
            selected = ", ".join(columns) if columns else "*"
            placeholders = ", ".join("?" for _value in chunk)
            sql = (
                f"SELECT {selected} FROM {source} WHERE record_type = ? "
                f"AND {field_name} IN ({placeholders})"
            )
            rows.extend(dict(row) for row in self.conn.execute(sql, (record_type, *chunk)))
        return rows

    def _lookup_row(
        self, row_key: str, *, cursor: _PageBuffer | None = None
    ) -> dict[str, Any] | None:
        if cursor is None:
            return self._get_row(row_key)
        if row_key in cursor.records:
            return cursor.records[row_key]
        if row_key not in cursor.existing_rows:
            cursor.existing_rows[row_key] = self._get_row(row_key)
        return cursor.existing_rows[row_key]

    def prefetch_rows(self, row_keys: list[str], *, cursor: _PageBuffer) -> None:
        missing = [
            row_key
            for row_key in dict.fromkeys(row_keys)
            if row_key and row_key not in cursor.records and row_key not in cursor.existing_rows
        ]
        if not missing:
            return
        found: dict[str, dict[str, Any]] = {}
        chunk_size = 200
        for start in range(0, len(missing), chunk_size):
            chunk = missing[start : start + chunk_size]
            expr = _expr_in("row_key", set(chunk))
            for row in self._query(expr=expr):
                row_key = row.get("row_key")
                if isinstance(row_key, str) and row_key:
                    found[row_key] = row
        for row_key in missing:
            cursor.existing_rows[row_key] = found.get(row_key)

    def _queue_record(self, record: dict[str, Any], *, cursor: _PageBuffer | None = None) -> None:
        if cursor is None:
            self._merge_records([record])
            return
        cursor.records[record["row_key"]] = record

    def _row_timestamps(
        self, row_key: str, *, cursor: _PageBuffer | None = None, now: str | None = None
    ) -> tuple[dict[str, Any] | None, str, str]:
        existing = self._lookup_row(row_key, cursor=cursor)
        stamp = now or utc_now()
        first_seen_at = existing["first_seen_at"] if existing else stamp
        added_at = existing["added_at"] if existing else stamp
        return existing, first_seen_at, added_at

    def _record_context(
        self,
        row_key: str,
        *,
        cursor: _PageBuffer | None = None,
    ) -> _RecordContext:
        now = utc_now()
        existing, first_seen_at, added_at = self._row_timestamps(row_key, cursor=cursor, now=now)
        return _RecordContext(
            existing=existing,
            now=now,
            first_seen_at=first_seen_at,
            added_at=added_at,
        )

    @staticmethod
    def _existing_value(context: _RecordContext, field_name: str) -> Any:
        if context.existing is None:
            return None
        return context.existing.get(field_name)

    def _coalesce_existing(
        self,
        context: _RecordContext,
        field_name: str,
        *values: Any,
    ) -> Any:
        return self._coalesce_value(*values, self._existing_value(context, field_name))

    def _record_with_context(
        self,
        context: _RecordContext,
        **overrides: Any,
    ) -> dict[str, Any]:
        return self._record(
            **overrides,
            first_seen_at=context.first_seen_at,
            last_seen_at=context.now,
            added_at=context.added_at,
            synced_at=context.now,
        )

    def _normalized_source(self, value: str | None) -> str:
        return value or LIVE_SOURCE

    def _prefer_incoming_source(
        self,
        context: _RecordContext,
        incoming_source: str,
        *,
        source_field: str = "source",
        deleted_at: str | None = None,
    ) -> bool:
        if context.existing is None:
            return True
        existing_source = self._normalized_source(self._existing_value(context, source_field))
        if incoming_source == existing_source:
            return True
        return incoming_source == LIVE_SOURCE and existing_source != LIVE_SOURCE

    def _merge_by_source_precedence(
        self,
        context: _RecordContext,
        field_name: str,
        incoming: Any,
        *,
        prefer_incoming: bool,
    ) -> Any:
        existing = self._existing_value(context, field_name)
        if prefer_incoming:
            return self._coalesce_value(incoming, existing)
        return self._coalesce_value(existing, incoming)

    def _merged_source_value(
        self,
        context: _RecordContext,
        incoming_source: str,
        *,
        prefer_incoming: bool,
        field_name: str = "source",
    ) -> str:
        if prefer_incoming:
            return incoming_source
        return self._normalized_source(self._existing_value(context, field_name))

    def _merged_deleted_at(
        self,
        context: _RecordContext,
        incoming_deleted_at: str | None,
        *,
        prefer_incoming: bool,
        incoming_source: str,
    ) -> str | None:
        if prefer_incoming and incoming_source == LIVE_SOURCE:
            return incoming_deleted_at
        return self._coalesce_value(
            incoming_deleted_at, self._existing_value(context, "deleted_at")
        )

    def _capture_record(
        self,
        operation: str,
        cursor_in: str | None,
        cursor_out: str | None,
        http_status: int,
        raw_json: Any,
        *,
        source: str,
        capture_key: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        capture_id = capture_key or str(uuid.uuid4())
        return capture_id, self._record(
            row_key=f"raw_capture:{capture_id}",
            record_type="raw_capture",
            operation=operation,
            cursor_in=cursor_in,
            cursor_out=cursor_out,
            captured_at=utc_now(),
            http_status=http_status,
            source=source,
            raw_json=json.dumps(raw_json, sort_keys=True),
        )

    def append_raw_capture(
        self,
        operation: str,
        cursor_in: str | None,
        cursor_out: str | None,
        http_status: int,
        raw_json: Any,
        *,
        source: str = LIVE_SOURCE,
        capture_key: str | None = None,
        cursor: _PageBuffer | None = None,
    ) -> str:
        capture_id, record = self._capture_record(
            operation,
            cursor_in,
            cursor_out,
            http_status,
            raw_json,
            source=source,
            capture_key=capture_key,
        )
        self._queue_record(record, cursor=cursor)
        return capture_id

    def upsert_tweet(self, tweet: TimelineTweet, *, cursor: _PageBuffer | None = None) -> None:
        if cursor is None:
            raise RuntimeError(
                "ArchiveStore.upsert_tweet() is only supported inside page buffering "
                "for the SQLite backend."
            )
        cursor.pending_tweets[tweet.tweet_id] = tweet

    def _tweet_record(
        self,
        tweet: TimelineTweet,
        collection_type: str,
        *,
        source: str = LIVE_SOURCE,
        deleted_at: str | None = None,
        sort_index: str | None = None,
        folder_id: str | None = None,
        cursor: _PageBuffer | None = None,
    ) -> dict[str, Any]:
        row_key = self._row_key_for_tweet(tweet.tweet_id, collection_type, folder_id)
        legacy = tweet.raw_json.get("legacy") or {}
        context = self._record_context(row_key, cursor=cursor)
        prefer_incoming = self._prefer_incoming_source(
            context,
            source,
            deleted_at=deleted_at,
        )
        return self._record_with_context(
            context,
            row_key=row_key,
            record_type="tweet",
            tweet_id=tweet.tweet_id,
            collection_type=collection_type,
            folder_id=_folder_key(folder_id),
            sort_index=self._merge_by_source_precedence(
                context, "sort_index", sort_index, prefer_incoming=prefer_incoming
            ),
            source=self._merged_source_value(context, source, prefer_incoming=prefer_incoming),
            text=self._merge_by_source_precedence(
                context,
                "text",
                tweet.text,
                prefer_incoming=prefer_incoming,
            ),
            author_id=self._merge_by_source_precedence(
                context,
                "author_id",
                tweet.author_id,
                prefer_incoming=prefer_incoming,
            ),
            author_username=self._merge_by_source_precedence(
                context,
                "author_username",
                tweet.author_username,
                prefer_incoming=prefer_incoming,
            ),
            author_display_name=self._merge_by_source_precedence(
                context,
                "author_display_name",
                tweet.author_display_name,
                prefer_incoming=prefer_incoming,
            ),
            created_at=(
                merged_created_at := self._merge_by_source_precedence(
                    context,
                    "created_at",
                    tweet.created_at,
                    prefer_incoming=prefer_incoming,
                )
            ),
            created_at_ts=int(_parse_created_at(merged_created_at).timestamp())
            if _parse_created_at(merged_created_at)
            else None,
            deleted_at=self._merged_deleted_at(
                context,
                deleted_at,
                prefer_incoming=prefer_incoming,
                incoming_source=source,
            ),
            conversation_id=self._merge_by_source_precedence(
                context,
                "conversation_id",
                legacy.get("conversation_id_str"),
                prefer_incoming=prefer_incoming,
            ),
            lang=self._merge_by_source_precedence(
                context,
                "lang",
                legacy.get("lang"),
                prefer_incoming=prefer_incoming,
            ),
            note_tweet_text=self._merge_by_source_precedence(
                context,
                "note_tweet_text",
                extract_note_tweet_text(tweet.raw_json),
                prefer_incoming=prefer_incoming,
            ),
            raw_json=self._merge_by_source_precedence(
                context,
                "raw_json",
                self._json_value(tweet.raw_json),
                prefer_incoming=prefer_incoming,
            ),
        )

    def upsert_membership(
        self,
        tweet_id: str,
        collection_type: str,
        *,
        source: str = LIVE_SOURCE,
        deleted_at: str | None = None,
        sort_index: str | None = None,
        folder_id: str | None = None,
        cursor: _PageBuffer | None = None,
    ) -> None:
        if cursor is None:
            raise RuntimeError(
                "ArchiveStore.upsert_membership() is only supported inside page buffering "
                "for the SQLite backend."
            )
        try:
            tweet = cursor.pending_tweets[tweet_id]
        except KeyError as exc:
            raise RuntimeError(
                f"Missing pending tweet {tweet_id} for collection {collection_type}."
            ) from exc
        self._queue_record(
            self._tweet_record(
                tweet,
                collection_type,
                source=source,
                deleted_at=deleted_at,
                sort_index=sort_index,
                folder_id=folder_id,
                cursor=cursor,
            ),
            cursor=cursor,
        )

    def get_sync_state(self, collection_type: str, folder_id: str | None = None) -> SyncState:
        row = self._get_row(self._row_key_for_sync_state(collection_type, folder_id))
        if row is None:
            return SyncState(collection_type=collection_type)
        return SyncState(
            collection_type=collection_type,
            last_head_tweet_id=row["last_head_tweet_id"],
            backfill_cursor=row["backfill_cursor"],
            backfill_incomplete=self._parse_bool(row["backfill_incomplete"]),
            updated_at=row["updated_at"],
        )

    def _sync_state_record(
        self,
        collection_type: str,
        *,
        last_head_tweet_id: str | None = None,
        backfill_cursor: str | None = None,
        backfill_incomplete: bool = False,
        folder_id: str | None = None,
    ) -> dict[str, Any]:
        return self._record(
            row_key=self._row_key_for_sync_state(collection_type, folder_id),
            record_type="sync_state",
            collection_type=collection_type,
            folder_id=_folder_key(folder_id),
            last_head_tweet_id=last_head_tweet_id,
            backfill_cursor=backfill_cursor,
            backfill_incomplete=backfill_incomplete,
            updated_at=utc_now(),
        )

    def set_sync_state(
        self,
        collection_type: str,
        *,
        last_head_tweet_id: str | None = None,
        backfill_cursor: str | None = None,
        backfill_incomplete: bool = False,
        folder_id: str | None = None,
        cursor: _PageBuffer | None = None,
    ) -> None:
        record = self._sync_state_record(
            collection_type,
            last_head_tweet_id=last_head_tweet_id,
            backfill_cursor=backfill_cursor,
            backfill_incomplete=backfill_incomplete,
            folder_id=folder_id,
        )
        self._queue_record(record, cursor=cursor)

    def reset_sync_state(self, collection_type: str, folder_id: str | None = None) -> None:
        self._delete(
            f"row_key = {_expr_quote(self._row_key_for_sync_state(collection_type, folder_id))}"
        )

    def has_membership(
        self, tweet_id: str, collection_type: str, folder_id: str | None = None
    ) -> bool:
        row_key = self._row_key_for_tweet(tweet_id, collection_type, folder_id)
        return self._get_row(row_key) is not None

    def get_collection_tweet_ids(self, collection_type: str) -> set[str]:
        filter_expr = f"record_type = 'tweet' AND collection_type = {_expr_quote(collection_type)}"
        rows = self._query(expr=filter_expr, cols=["tweet_id"])
        return {row["tweet_id"] for row in rows}

    def get_archive_owner_id(self) -> str | None:
        row = self._get_row(self._row_key_for_metadata("owner_user_id"))
        return row["value"] if row else None

    def set_archive_owner_id(self, user_id: str, *, cursor: _PageBuffer | None = None) -> None:
        record = self._record(
            row_key=self._row_key_for_metadata("owner_user_id"),
            record_type="metadata",
            key="owner_user_id",
            value=user_id,
            updated_at=utc_now(),
        )
        self._queue_record(record, cursor=cursor)

    def ensure_archive_owner_id(self, user_id: str | None) -> None:
        if not user_id:
            return
        existing = self.get_archive_owner_id()
        if existing and existing != user_id:
            raise ArchiveOwnerMismatchError(
                f"Local archive belongs to Twitter/X user {existing}, but current auth "
                f"resolved {user_id}."
            )
        if existing is None:
            self.set_archive_owner_id(user_id)

    def get_import_manifest(self, archive_digest: str) -> dict[str, Any] | None:
        return self._get_row(self._row_key_for_import_manifest(archive_digest))

    def has_completed_archive_import(self) -> bool:
        """Return whether at least one official archive import completed successfully."""

        return (
            self._count("record_type = 'import_manifest' AND status IN ('completed', 'sampled')")
            > 0
        )

    def set_import_manifest(
        self,
        archive_digest: str,
        *,
        archive_generation_date: str | None,
        status: str,
        import_started_at: str | None = None,
        import_completed_at: str | None = None,
        warnings: list[str] | None = None,
        counts: dict[str, Any] | None = None,
        enrichment_followup_status: str | None = None,
        enrichment_aborted_reason: str | None = None,
    ) -> None:
        existing = self.get_import_manifest(archive_digest)
        self.merge_rows(
            [
                self._record(
                    row_key=self._row_key_for_import_manifest(archive_digest),
                    record_type="import_manifest",
                    archive_digest=archive_digest,
                    archive_generation_date=archive_generation_date
                    or (existing.get("archive_generation_date") if existing else None),
                    import_started_at=import_started_at
                    or (existing.get("import_started_at") if existing else None)
                    or utc_now(),
                    import_completed_at=import_completed_at,
                    status=status,
                    warnings_json=self._json_value(warnings or []),
                    counts_json=self._json_value(counts or {}),
                    enrichment_followup_status=enrichment_followup_status,
                    enrichment_aborted_reason=enrichment_aborted_reason,
                    updated_at=utc_now(),
                )
            ]
        )

    def _tweet_object_record(
        self,
        tweet: Any,
        *,
        source: str = LIVE_SOURCE,
        deleted_at: str | None = None,
        enrichment_state: str | None = None,
        enrichment_checked_at: str | None = None,
        enrichment_http_status: int | None = None,
        enrichment_reason: str | None = None,
        enrichment_detail: str | None = None,
        enrichment_retry_count: int | None = None,
        enrichment_next_retry_at: str | None = None,
        enrichment_first_unavailable_at: str | None = None,
        enrichment_retry_eligible: bool | None = None,
        cursor: _PageBuffer | None = None,
    ) -> dict[str, Any]:
        row_key = self._row_key_for_tweet_object(tweet.tweet_id)
        context = self._record_context(row_key, cursor=cursor)
        prefer_incoming = self._prefer_incoming_source(
            context,
            source,
            deleted_at=deleted_at,
        )
        if source == LIVE_SOURCE:
            if enrichment_state is None:
                prior_state = self._existing_value(context, "enrichment_state")
                enrichment_state = (
                    "resurrected"
                    if prior_state in {"terminal_unavailable", "resurrected"}
                    else "done"
                )
            enrichment_checked_at = enrichment_checked_at or context.now
            enrichment_http_status = (
                200 if enrichment_http_status is None else enrichment_http_status
            )
            enrichment_reason = None
            enrichment_detail = None
            enrichment_retry_count = 0
            enrichment_next_retry_at = None
            enrichment_first_unavailable_at = None
            enrichment_retry_eligible = False
        elif deleted_at and enrichment_state is None:
            enrichment_state = "terminal_unavailable"
            enrichment_checked_at = enrichment_checked_at or context.now
            enrichment_reason = enrichment_reason or "archive_deleted"
            enrichment_first_unavailable_at = enrichment_first_unavailable_at or context.now
            enrichment_retry_count = enrichment_retry_count or 0
            enrichment_retry_eligible = False

        def merge_lifecycle(field_name: str, incoming: Any) -> Any:
            if source == LIVE_SOURCE:
                return incoming
            return self._merge_by_source_precedence(
                context,
                field_name,
                incoming,
                prefer_incoming=prefer_incoming,
            )

        return self._record_with_context(
            context,
            row_key=row_key,
            record_type="tweet_object",
            tweet_id=tweet.tweet_id,
            source=self._merged_source_value(context, source, prefer_incoming=prefer_incoming),
            text=self._merge_by_source_precedence(
                context,
                "text",
                tweet.text,
                prefer_incoming=prefer_incoming,
            )
            or "",
            author_id=self._merge_by_source_precedence(
                context,
                "author_id",
                tweet.author_id,
                prefer_incoming=prefer_incoming,
            ),
            author_username=self._merge_by_source_precedence(
                context,
                "author_username",
                tweet.author_username,
                prefer_incoming=prefer_incoming,
            ),
            author_display_name=self._merge_by_source_precedence(
                context,
                "author_display_name",
                tweet.author_display_name,
                prefer_incoming=prefer_incoming,
            ),
            created_at=(
                merged_created_at := self._merge_by_source_precedence(
                    context,
                    "created_at",
                    tweet.created_at,
                    prefer_incoming=prefer_incoming,
                )
            ),
            created_at_ts=int(_parse_created_at(merged_created_at).timestamp())
            if _parse_created_at(merged_created_at)
            else None,
            deleted_at=self._merged_deleted_at(
                context,
                deleted_at,
                prefer_incoming=prefer_incoming,
                incoming_source=source,
            ),
            conversation_id=self._merge_by_source_precedence(
                context,
                "conversation_id",
                tweet.conversation_id,
                prefer_incoming=prefer_incoming,
            ),
            lang=self._merge_by_source_precedence(
                context,
                "lang",
                tweet.lang,
                prefer_incoming=prefer_incoming,
            ),
            note_tweet_text=self._merge_by_source_precedence(
                context,
                "note_tweet_text",
                tweet.note_tweet_text,
                prefer_incoming=prefer_incoming,
            ),
            enrichment_state=merge_lifecycle("enrichment_state", enrichment_state),
            enrichment_checked_at=merge_lifecycle("enrichment_checked_at", enrichment_checked_at),
            enrichment_http_status=merge_lifecycle(
                "enrichment_http_status", enrichment_http_status
            ),
            enrichment_reason=merge_lifecycle("enrichment_reason", enrichment_reason),
            enrichment_detail=merge_lifecycle("enrichment_detail", enrichment_detail),
            enrichment_retry_count=merge_lifecycle(
                "enrichment_retry_count", enrichment_retry_count
            ),
            enrichment_next_retry_at=merge_lifecycle(
                "enrichment_next_retry_at", enrichment_next_retry_at
            ),
            enrichment_first_unavailable_at=merge_lifecycle(
                "enrichment_first_unavailable_at", enrichment_first_unavailable_at
            ),
            enrichment_retry_eligible=merge_lifecycle(
                "enrichment_retry_eligible",
                int(enrichment_retry_eligible) if enrichment_retry_eligible is not None else None,
            ),
            raw_json=self._merge_by_source_precedence(
                context,
                "raw_json",
                self._json_value(tweet.raw_json),
                prefer_incoming=prefer_incoming,
            ),
        )

    def _tweet_relation_record(
        self,
        relation: Any,
        *,
        source: str = LIVE_SOURCE,
        cursor: _PageBuffer | None = None,
    ) -> dict[str, Any]:
        row_key = self._row_key_for_tweet_relation(
            relation.source_tweet_id,
            relation.relation_type,
            relation.target_tweet_id,
        )
        context = self._record_context(row_key, cursor=cursor)
        prefer_incoming = self._prefer_incoming_source(context, source)
        return self._record_with_context(
            context,
            row_key=row_key,
            record_type="tweet_relation",
            tweet_id=relation.source_tweet_id,
            relation_type=relation.relation_type,
            target_tweet_id=relation.target_tweet_id,
            source=self._merged_source_value(context, source, prefer_incoming=prefer_incoming),
            raw_json=self._merge_by_source_precedence(
                context,
                "raw_json",
                self._json_value(relation.raw_json),
                prefer_incoming=prefer_incoming,
            ),
        )

    def _media_record(
        self,
        media: Any,
        *,
        provenance_source: str = LIVE_SOURCE,
        cursor: _PageBuffer | None = None,
    ) -> dict[str, Any]:
        row_key = self._row_key_for_media(media.tweet_id, media.media_key)
        context = self._record_context(row_key, cursor=cursor)
        prefer_incoming = self._prefer_incoming_source(
            context,
            provenance_source,
            source_field="provenance_source",
        )
        return self._record_with_context(
            context,
            row_key=row_key,
            record_type="media",
            tweet_id=media.tweet_id,
            source=self._merge_by_source_precedence(
                context,
                "source",
                media.source,
                prefer_incoming=prefer_incoming,
            ),
            provenance_source=self._merged_source_value(
                context,
                provenance_source,
                prefer_incoming=prefer_incoming,
                field_name="provenance_source",
            ),
            article_id=self._merge_by_source_precedence(
                context,
                "article_id",
                media.article_id,
                prefer_incoming=prefer_incoming,
            ),
            position=media.position,
            media_key=media.media_key,
            media_type=self._merge_by_source_precedence(
                context,
                "media_type",
                media.media_type,
                prefer_incoming=prefer_incoming,
            ),
            media_url=self._merge_by_source_precedence(
                context,
                "media_url",
                media.media_url,
                prefer_incoming=prefer_incoming,
            ),
            thumbnail_url=self._merge_by_source_precedence(
                context,
                "thumbnail_url",
                media.thumbnail_url,
                prefer_incoming=prefer_incoming,
            ),
            width=self._merge_by_source_precedence(
                context,
                "width",
                media.width,
                prefer_incoming=prefer_incoming,
            ),
            height=self._merge_by_source_precedence(
                context,
                "height",
                media.height,
                prefer_incoming=prefer_incoming,
            ),
            duration_millis=self._merge_by_source_precedence(
                context,
                "duration_millis",
                media.duration_millis,
                prefer_incoming=prefer_incoming,
            ),
            variants_json=self._merge_by_source_precedence(
                context,
                "variants_json",
                self._json_value(media.variants) if media.variants else None,
                prefer_incoming=prefer_incoming,
            ),
            download_state=self._coalesce_value(
                self._existing_value(context, "download_state"),
                "pending",
            ),
            local_path=self._existing_value(context, "local_path"),
            sha256=self._existing_value(context, "sha256"),
            byte_size=self._existing_value(context, "byte_size"),
            content_type=self._existing_value(context, "content_type"),
            thumbnail_local_path=self._existing_value(context, "thumbnail_local_path"),
            thumbnail_sha256=self._existing_value(context, "thumbnail_sha256"),
            thumbnail_byte_size=self._existing_value(context, "thumbnail_byte_size"),
            thumbnail_content_type=self._existing_value(context, "thumbnail_content_type"),
            downloaded_at=self._existing_value(context, "downloaded_at"),
            download_error=self._existing_value(context, "download_error"),
            raw_json=self._merge_by_source_precedence(
                context,
                "raw_json",
                self._json_value(media.raw_json),
                prefer_incoming=prefer_incoming,
            ),
        )

    def _url_record(
        self,
        url: Any,
        *,
        source: str = LIVE_SOURCE,
        cursor: _PageBuffer | None = None,
    ) -> dict[str, Any]:
        row_key = self._row_key_for_url(url.url_hash)
        context = self._record_context(row_key, cursor=cursor)
        prefer_incoming = self._prefer_incoming_source(context, source)
        return self._record_with_context(
            context,
            row_key=row_key,
            record_type="url",
            url_hash=url.url_hash,
            url=url.canonical_url,
            source=self._merged_source_value(context, source, prefer_incoming=prefer_incoming),
            expanded_url=self._merge_by_source_precedence(
                context,
                "expanded_url",
                url.expanded_url,
                prefer_incoming=prefer_incoming,
            ),
            final_url=self._merge_by_source_precedence(
                context,
                "final_url",
                url.final_url,
                prefer_incoming=prefer_incoming,
            ),
            canonical_url=url.canonical_url,
            url_host=self._merge_by_source_precedence(
                context,
                "url_host",
                url.host,
                prefer_incoming=prefer_incoming,
            ),
            title=self._merge_by_source_precedence(
                context,
                "title",
                url.title,
                prefer_incoming=prefer_incoming,
            ),
            description=self._merge_by_source_precedence(
                context,
                "description",
                url.description,
                prefer_incoming=prefer_incoming,
            ),
            site_name=self._merge_by_source_precedence(
                context,
                "site_name",
                url.site_name,
                prefer_incoming=prefer_incoming,
            ),
            unfurl_state=self._coalesce_value(
                self._existing_value(context, "unfurl_state"),
                "pending",
            ),
            last_fetched_at=self._existing_value(context, "last_fetched_at"),
            raw_json=self._merge_by_source_precedence(
                context,
                "raw_json",
                self._json_value(url.raw_json),
                prefer_incoming=prefer_incoming,
            ),
        )

    def _url_ref_record(
        self,
        url_ref: Any,
        *,
        source: str = LIVE_SOURCE,
        cursor: _PageBuffer | None = None,
    ) -> dict[str, Any]:
        row_key = self._row_key_for_url_ref(url_ref.tweet_id, url_ref.position)
        context = self._record_context(row_key, cursor=cursor)
        prefer_incoming = self._prefer_incoming_source(context, source)
        return self._record_with_context(
            context,
            row_key=row_key,
            record_type="url_ref",
            tweet_id=url_ref.tweet_id,
            position=url_ref.position,
            source=self._merged_source_value(context, source, prefer_incoming=prefer_incoming),
            url_hash=self._merge_by_source_precedence(
                context,
                "url_hash",
                url_ref.url_hash,
                prefer_incoming=prefer_incoming,
            ),
            url=self._merge_by_source_precedence(
                context,
                "url",
                url_ref.short_url,
                prefer_incoming=prefer_incoming,
            ),
            expanded_url=self._merge_by_source_precedence(
                context,
                "expanded_url",
                url_ref.expanded_url,
                prefer_incoming=prefer_incoming,
            ),
            canonical_url=self._merge_by_source_precedence(
                context,
                "canonical_url",
                url_ref.canonical_url,
                prefer_incoming=prefer_incoming,
            ),
            display_url=self._merge_by_source_precedence(
                context,
                "display_url",
                url_ref.display_url,
                prefer_incoming=prefer_incoming,
            ),
            raw_json=self._merge_by_source_precedence(
                context,
                "raw_json",
                self._json_value(url_ref.raw_json),
                prefer_incoming=prefer_incoming,
            ),
        )

    def _article_record(
        self,
        article: Any,
        *,
        source: str = LIVE_SOURCE,
        cursor: _PageBuffer | None = None,
    ) -> dict[str, Any]:
        row_key = self._row_key_for_article(article.tweet_id)
        context = self._record_context(row_key, cursor=cursor)
        prefer_incoming = self._prefer_incoming_source(context, source)
        content_text = self._merge_by_source_precedence(
            context,
            "content_text",
            article.content_text,
            prefer_incoming=prefer_incoming,
        )
        status = (
            "body_present"
            if content_text
            else self._coalesce_value(
                article.status,
                self._existing_value(context, "status"),
                "preview_only",
            )
        )
        return self._record_with_context(
            context,
            row_key=row_key,
            record_type="article",
            tweet_id=article.tweet_id,
            source=self._merged_source_value(context, source, prefer_incoming=prefer_incoming),
            article_id=self._merge_by_source_precedence(
                context,
                "article_id",
                article.article_id,
                prefer_incoming=prefer_incoming,
            ),
            title=self._merge_by_source_precedence(
                context,
                "title",
                article.title,
                prefer_incoming=prefer_incoming,
            ),
            summary_text=self._merge_by_source_precedence(
                context,
                "summary_text",
                article.summary_text,
                prefer_incoming=prefer_incoming,
            ),
            content_text=content_text,
            canonical_url=self._merge_by_source_precedence(
                context,
                "canonical_url",
                article.canonical_url,
                prefer_incoming=prefer_incoming,
            ),
            published_at=self._merge_by_source_precedence(
                context,
                "published_at",
                article.published_at,
                prefer_incoming=prefer_incoming,
            ),
            status=status,
            raw_json=self._merge_by_source_precedence(
                context,
                "raw_json",
                self._json_value(article.raw_json),
                prefer_incoming=prefer_incoming,
            ),
        )

    def _buffer_secondary_graph(
        self,
        graph: ExtractedTweetGraph,
        *,
        source: str = LIVE_SOURCE,
        cursor: _PageBuffer,
    ) -> None:
        keys = [
            self._row_key_for_tweet_object(item.tweet_id) for item in graph.tweet_objects.values()
        ]
        keys.extend(
            self._row_key_for_tweet_relation(
                item.source_tweet_id, item.relation_type, item.target_tweet_id
            )
            for item in graph.relations.values()
        )
        keys.extend(
            self._row_key_for_media(item.tweet_id, item.media_key) for item in graph.media.values()
        )
        keys.extend(self._row_key_for_url(item.url_hash) for item in graph.urls.values())
        keys.extend(
            self._row_key_for_url_ref(item.tweet_id, item.position)
            for item in graph.url_refs.values()
        )
        keys.extend(self._row_key_for_article(item.tweet_id) for item in graph.articles.values())
        self.prefetch_rows(keys, cursor=cursor)
        for item in graph.tweet_objects.values():
            self._queue_record(
                self._tweet_object_record(item, source=source, cursor=cursor),
                cursor=cursor,
            )
        for item in graph.relations.values():
            self._queue_record(
                self._tweet_relation_record(item, source=source, cursor=cursor),
                cursor=cursor,
            )
        for item in graph.media.values():
            self._queue_record(
                self._media_record(item, provenance_source=source, cursor=cursor),
                cursor=cursor,
            )
        for item in graph.urls.values():
            self._queue_record(self._url_record(item, source=source, cursor=cursor), cursor=cursor)
        for item in graph.url_refs.values():
            self._queue_record(
                self._url_ref_record(item, source=source, cursor=cursor),
                cursor=cursor,
            )
        for item in graph.articles.values():
            self._queue_record(
                self._article_record(item, source=source, cursor=cursor),
                cursor=cursor,
            )

    def _buffer_secondary_objects(
        self,
        tweets: list[TimelineTweet],
        *,
        source: str = LIVE_SOURCE,
        cursor: _PageBuffer,
    ) -> None:
        graph = ExtractedTweetGraph()
        for tweet in tweets:
            graph.merge(extract_secondary_objects(tweet.raw_json))
        self._buffer_secondary_graph(graph, source=source, cursor=cursor)

    def persist_page(
        self,
        *,
        operation: str,
        collection_type: str,
        cursor_in: str | None,
        cursor_out: str | None,
        http_status: int,
        raw_json: dict[str, Any],
        tweets: list[TimelineTweet],
        last_head_tweet_id: str | None,
        backfill_cursor: str | None,
        backfill_incomplete: bool,
    ) -> None:
        buffer = _PageBuffer()
        self.append_raw_capture(
            operation,
            cursor_in,
            cursor_out,
            http_status,
            raw_json,
            source=LIVE_SOURCE,
            cursor=buffer,
        )
        for tweet in tweets:
            self.upsert_tweet(tweet, cursor=buffer)
            self.upsert_membership(
                tweet.tweet_id,
                collection_type,
                source=LIVE_SOURCE,
                sort_index=tweet.sort_index,
                cursor=buffer,
            )
        self._buffer_secondary_objects(tweets, source=LIVE_SOURCE, cursor=buffer)
        self.set_sync_state(
            collection_type,
            last_head_tweet_id=last_head_tweet_id,
            backfill_cursor=backfill_cursor,
            backfill_incomplete=backfill_incomplete,
            cursor=buffer,
        )
        self._merge_records(list(buffer.records.values()))

    def list_media_rows(
        self,
        *,
        states: set[str] | None = None,
        media_types: set[str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        if states is not None and not states:
            return []
        if media_types is not None and not media_types:
            return []
        where_expr = _and_expr(
            "record_type = 'media'",
            _state_filter_expr("download_state", states) if states is not None else "",
            _expr_in("media_type", media_types) if media_types is not None else "",
        )
        cols = [
            "row_key",
            "tweet_id",
            "position",
            "media_key",
            "media_type",
            "media_url",
            "thumbnail_url",
            "download_state",
            "local_path",
            "thumbnail_local_path",
            "sha256",
            "byte_size",
            "content_type",
            "thumbnail_sha256",
            "thumbnail_byte_size",
            "thumbnail_content_type",
            "variants_json",
            "source",
        ]
        return self._query(
            expr=where_expr,
            cols=cols,
            order_by="COALESCE(tweet_id, ''), COALESCE(position, 1000000), rowid",
            limit=limit,
        )

    def update_media_download(
        self,
        row_key: str,
        *,
        download_state: str,
        local_path: str | None,
        sha256: str | None,
        byte_size: int | None,
        content_type: str | None,
        thumbnail_local_path: str | None,
        thumbnail_sha256: str | None,
        thumbnail_byte_size: int | None,
        thumbnail_content_type: str | None,
        downloaded_at: str | None,
        download_error: str | None,
    ) -> None:
        row = self._get_row(row_key)
        if row is None:
            raise KeyError(f"Media row not found: {row_key}")
        self.merge_rows(
            [
                self.build_media_download_update(
                    row,
                    download_state=download_state,
                    local_path=local_path,
                    sha256=sha256,
                    byte_size=byte_size,
                    content_type=content_type,
                    thumbnail_local_path=thumbnail_local_path,
                    thumbnail_sha256=thumbnail_sha256,
                    thumbnail_byte_size=thumbnail_byte_size,
                    thumbnail_content_type=thumbnail_content_type,
                    downloaded_at=downloaded_at,
                    download_error=download_error,
                )
            ]
        )

    def build_media_download_update(
        self,
        row: dict[str, Any],
        *,
        download_state: str,
        local_path: str | None,
        sha256: str | None,
        byte_size: int | None,
        content_type: str | None,
        thumbnail_local_path: str | None,
        thumbnail_sha256: str | None,
        thumbnail_byte_size: int | None,
        thumbnail_content_type: str | None,
        downloaded_at: str | None,
        download_error: str | None,
    ) -> dict[str, Any]:
        full_row = self._get_row(row["row_key"])
        if full_row is None:
            raise KeyError(f"Media row not found: {row['row_key']}")
        updated = dict(full_row)
        updated.update(
            {
                "download_state": download_state,
                "local_path": local_path,
                "sha256": sha256,
                "byte_size": byte_size,
                "content_type": content_type,
                "thumbnail_local_path": thumbnail_local_path,
                "thumbnail_sha256": thumbnail_sha256,
                "thumbnail_byte_size": thumbnail_byte_size,
                "thumbnail_content_type": thumbnail_content_type,
                "downloaded_at": downloaded_at,
                "download_error": download_error,
                "updated_at": utc_now(),
            }
        )
        return updated

    def list_url_rows(
        self,
        *,
        states: set[str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        if states is not None and not states:
            return []
        where_expr = _and_expr(
            "record_type = 'url'",
            _state_filter_expr("unfurl_state", states) if states is not None else "",
        )
        cols = [
            "row_key",
            "url_hash",
            "canonical_url",
            "url",
            "expanded_url",
            "final_url",
            "http_status",
            "title",
            "description",
            "site_name",
            "content_type",
            "unfurl_state",
        ]
        return self._query(
            expr=where_expr,
            cols=cols,
            order_by="COALESCE(NULLIF(canonical_url, ''), url, ''), rowid",
            limit=limit,
        )

    def update_url_unfurl(
        self,
        row_key: str,
        *,
        http_status: int | None,
        final_url: str | None,
        canonical_url: str | None,
        title: str | None,
        description: str | None,
        site_name: str | None,
        content_type: str | None,
        unfurl_state: str,
        last_fetched_at: str | None,
        download_error: str | None,
    ) -> None:
        row = self._get_row(row_key)
        if row is None:
            raise KeyError(f"URL row not found: {row_key}")
        self.merge_rows(
            [
                self.build_url_unfurl_update(
                    row,
                    http_status=http_status,
                    final_url=final_url,
                    canonical_url=canonical_url,
                    title=title,
                    description=description,
                    site_name=site_name,
                    content_type=content_type,
                    unfurl_state=unfurl_state,
                    last_fetched_at=last_fetched_at,
                    download_error=download_error,
                )
            ]
        )

    def build_url_unfurl_update(
        self,
        row: dict[str, Any],
        *,
        http_status: int | None,
        final_url: str | None,
        canonical_url: str | None,
        title: str | None,
        description: str | None,
        site_name: str | None,
        content_type: str | None,
        unfurl_state: str,
        last_fetched_at: str | None,
        download_error: str | None,
    ) -> dict[str, Any]:
        full_row = self._get_row(row["row_key"])
        if full_row is None:
            raise KeyError(f"URL row not found: {row['row_key']}")
        updated = dict(full_row)
        updated.update(
            {
                "http_status": http_status,
                "final_url": final_url,
                "canonical_url": canonical_url,
                "url": canonical_url or updated.get("url"),
                "title": title,
                "description": description,
                "site_name": site_name,
                "content_type": content_type,
                "unfurl_state": unfurl_state,
                "last_fetched_at": last_fetched_at,
                "download_error": download_error,
                "updated_at": utc_now(),
            }
        )
        return updated

    def list_article_rows(
        self,
        *,
        preview_only: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        where_expr = _and_expr(
            "record_type = 'article'",
            "(status IS NULL OR status != 'body_present')" if preview_only else "",
        )
        return self._query(expr=where_expr, order_by="COALESCE(tweet_id, ''), rowid", limit=limit)

    def get_article_tweet_ids(
        self,
        *,
        preview_only: bool = False,
        limit: int | None = None,
    ) -> list[str]:
        where_expr = _and_expr(
            "record_type = 'article'",
            "(status IS NULL OR status != 'body_present')" if preview_only else "",
        )
        rows = self._query(
            expr=_and_expr(where_expr, "tweet_id IS NOT NULL AND tweet_id != ''"),
            cols=["tweet_id"],
            order_by="tweet_id, rowid",
            limit=limit,
        )
        return [row["tweet_id"] for row in rows]

    def list_tweet_objects_for_enrichment(
        self, *, limit: int | None = None, now: str | None = None
    ) -> list[dict[str, Any]]:
        now = now or utc_now()
        return self._query(
            expr="record_type = 'tweet_object' "
            "AND (enrichment_state = 'pending' OR ("
            "enrichment_state = 'transient_failure' AND ("
            "enrichment_next_retry_at IS NULL OR "
            f"enrichment_next_retry_at <= {_expr_quote(now)})))",
            cols=[
                "tweet_id",
                "enrichment_checked_at",
                "enrichment_next_retry_at",
                "enrichment_retry_count",
            ],
            order_by=(
                "COALESCE(enrichment_next_retry_at, '') ASC, "
                "COALESCE(enrichment_checked_at, '') ASC, tweet_id ASC"
            ),
            limit=limit,
        )

    def count_tweet_objects_for_enrichment(self) -> int:
        return self.count_incomplete_initial_enrichment()

    def count_pending_initial_enrichment(self) -> int:
        return self._count("record_type = 'tweet_object' AND enrichment_state = 'pending'")

    def count_due_transient_enrichment(self, now: str | None = None) -> int:
        now = now or utc_now()
        return self._count(
            "record_type = 'tweet_object' AND enrichment_state = 'transient_failure' "
            "AND (enrichment_next_retry_at IS NULL OR "
            f"enrichment_next_retry_at <= {_expr_quote(now)})"
        )

    def count_delayed_transient_enrichment(self, now: str | None = None) -> int:
        now = now or utc_now()
        return self._count(
            "record_type = 'tweet_object' AND enrichment_state = 'transient_failure' "
            "AND enrichment_next_retry_at IS NOT NULL AND "
            f"enrichment_next_retry_at > {_expr_quote(now)}"
        )

    def count_incomplete_initial_enrichment(self) -> int:
        return self._count(
            "record_type = 'tweet_object' "
            "AND (enrichment_state = 'pending' OR enrichment_state = 'transient_failure')"
        )

    def enrichment_status_counts(self) -> dict[str, int]:
        return {
            "pending_initial_enrichment": self.count_pending_initial_enrichment(),
            "transient_initial_enrichment": self._count(
                "record_type = 'tweet_object' AND enrichment_state = 'transient_failure'"
            ),
            "terminal_unavailable": self._count(
                "record_type = 'tweet_object' AND enrichment_state = 'terminal_unavailable'"
            ),
            "retryable_unavailable": self._count(
                "record_type = 'tweet_object' AND enrichment_state = 'terminal_unavailable' "
                "AND enrichment_retry_eligible = 1 AND deleted_at IS NULL "
                "AND enrichment_reason NOT IN ('archive_deleted', 'deleted_by_author')"
            ),
            "permanent_unavailable": self._count(
                "record_type = 'tweet_object' AND enrichment_state = 'terminal_unavailable' "
                "AND (deleted_at IS NOT NULL OR enrichment_retry_eligible = 0 OR "
                "enrichment_reason IN ('archive_deleted', 'deleted_by_author'))"
            ),
            "done": self._count("record_type = 'tweet_object' AND enrichment_state = 'done'"),
            "resurrected": self._count(
                "record_type = 'tweet_object' AND enrichment_state = 'resurrected'"
            ),
        }

    def list_due_resurrection_tweets(
        self,
        *,
        reasons: set[str] | None = None,
        exclude_tweet_ids: set[str] | None = None,
        limit: int | None = None,
        now: str | None = None,
    ) -> list[dict[str, Any]]:
        now = now or utc_now()
        clauses = [
            "record_type = 'tweet_object'",
            "enrichment_state = 'terminal_unavailable'",
            "enrichment_retry_eligible = 1",
            "deleted_at IS NULL",
            "enrichment_reason NOT IN ('archive_deleted', 'deleted_by_author')",
            f"(enrichment_next_retry_at IS NULL OR enrichment_next_retry_at <= {_expr_quote(now)})",
        ]
        if reasons:
            clauses.append(_expr_in("enrichment_reason", reasons))
        if exclude_tweet_ids:
            clauses.append(f"NOT {_expr_in('tweet_id', exclude_tweet_ids)}")
        return self._query(
            expr=_and_expr(*clauses),
            cols=[
                "tweet_id",
                "author_id",
                "enrichment_reason",
                "enrichment_detail",
                "enrichment_checked_at",
                "enrichment_retry_count",
                "enrichment_next_retry_at",
            ],
            order_by=(
                "COALESCE(enrichment_next_retry_at, '') ASC, "
                "COALESCE(enrichment_checked_at, '') ASC, tweet_id ASC"
            ),
            limit=limit,
        )

    def list_same_author_resurrection_tweets(
        self,
        author_id: str,
        *,
        exclude_tweet_ids: set[str] | None = None,
        limit: int = 5,
        due_only: bool = False,
        now: str | None = None,
    ) -> list[dict[str, Any]]:
        now = now or utc_now()
        clauses = [
            "record_type = 'tweet_object'",
            "enrichment_state = 'terminal_unavailable'",
            "enrichment_retry_eligible = 1",
            "deleted_at IS NULL",
            f"author_id = {_expr_quote(author_id)}",
            "enrichment_reason NOT IN ('archive_deleted', 'deleted_by_author')",
        ]
        if due_only:
            clauses.append(
                "(enrichment_next_retry_at IS NULL OR "
                f"enrichment_next_retry_at <= {_expr_quote(now)})"
            )
        if exclude_tweet_ids:
            clauses.append(f"NOT {_expr_in('tweet_id', exclude_tweet_ids)}")
        return self._query(
            expr=_and_expr(*clauses),
            cols=[
                "tweet_id",
                "author_id",
                "enrichment_reason",
                "enrichment_detail",
                "enrichment_checked_at",
                "enrichment_retry_count",
                "enrichment_next_retry_at",
            ],
            order_by=(
                "COALESCE(enrichment_next_retry_at, '') ASC, "
                "COALESCE(enrichment_checked_at, '') ASC, tweet_id ASC"
            ),
            limit=limit,
        )

    def mark_author_resurrection_due(
        self, author_id: str, *, exclude_tweet_ids: set[str] | None = None
    ) -> int:
        clauses = [
            "record_type = 'tweet_object'",
            "enrichment_state = 'terminal_unavailable'",
            "enrichment_retry_eligible = 1",
            "deleted_at IS NULL",
            "enrichment_reason NOT IN ('archive_deleted', 'deleted_by_author')",
            f"author_id = {_expr_quote(author_id)}",
        ]
        if exclude_tweet_ids:
            clauses.append(f"NOT {_expr_in('tweet_id', exclude_tweet_ids)}")
        with self.conn:
            cursor = self.conn.execute(
                f"UPDATE archive SET enrichment_next_retry_at = ?, updated_at = ? "
                f"WHERE {_and_expr(*clauses)}",
                (utc_now(), utc_now()),
            )
        return cursor.rowcount

    def mark_tweets_resurrection_due(
        self,
        tweet_ids: set[str],
        *,
        due_at: str | None = None,
    ) -> int:
        if not tweet_ids:
            return 0
        now = due_at or utc_now()
        clauses = [
            "record_type = 'tweet_object'",
            "enrichment_state = 'terminal_unavailable'",
            "enrichment_retry_eligible = 1",
            "deleted_at IS NULL",
            "enrichment_reason NOT IN ('archive_deleted', 'deleted_by_author')",
            _expr_in("tweet_id", tweet_ids),
        ]
        with self.conn:
            cursor = self.conn.execute(
                f"UPDATE archive SET enrichment_next_retry_at = ?, updated_at = ? "
                f"WHERE {_and_expr(*clauses)}",
                (now, now),
            )
        return cursor.rowcount

    def count_due_resurrection_tweets(self, now: str | None = None) -> int:
        now = now or utc_now()
        return self._count(
            "record_type = 'tweet_object' "
            "AND enrichment_state = 'terminal_unavailable' "
            "AND enrichment_retry_eligible = 1 AND deleted_at IS NULL "
            "AND enrichment_reason NOT IN ('archive_deleted', 'deleted_by_author') "
            "AND (enrichment_next_retry_at IS NULL OR "
            f"enrichment_next_retry_at <= {_expr_quote(now)})"
        )

    def list_dead_tweets_for_resurrection(
        self, *, limit: int | None = None
    ) -> list[dict[str, Any]]:
        return self.list_due_resurrection_tweets(limit=limit)

    def count_dead_tweets_for_resurrection(self) -> int:
        return self.count_due_resurrection_tweets()

    def get_eligible_tagging_candidates(
        self,
        *,
        limit: int = 20,
        exclude_tweet_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return one homogeneous batch of untagged saved or directly quoted tweets.

        Keep candidate discovery streaming.  A materialized CTE for the complete
        eligible population is particularly expensive here: large archives make
        SQLite sort that population into temporary B-trees before it can return
        even a one-tweet batch.  Walking the existing record/page index lets us
        stop as soon as the next homogeneous batch is full and keeps temporary
        storage independent of archive size.
        """
        if limit < 1:
            return []

        state_placeholders = ", ".join("?" for _state in AVAILABLE_ENRICHMENT_STATES)
        valid_tag_sql = """
            NOT EXISTS (
                SELECT 1
                FROM archive tag INDEXED BY idx_archive_tweet_id
                WHERE tag.tweet_id = {tweet_id}
                  AND tag.record_type = 'media_tag'
                  AND (tag.enrichment_state = 'done' OR tag.enrichment_state IS NULL)
                  AND json_valid(tag.raw_json)
                  AND json_type(tag.raw_json, '$.tags') = 'array'
                  AND json_array_length(tag.raw_json, '$.tags') > 0
            )
        """
        saved_query = f"""
            SELECT t.tweet_id, t.created_at_ts AS sort_ts
            FROM archive t INDEXED BY idx_archive_record_page
            WHERE t.record_type = 'tweet'
              AND EXISTS (
                  SELECT 1
                  FROM archive o INDEXED BY idx_archive_tweet_id
                  WHERE o.tweet_id = t.tweet_id
                    AND o.record_type = 'tweet_object'
                    AND o.enrichment_state IN ({state_placeholders})
              )
              AND {valid_tag_sql.format(tweet_id="t.tweet_id")}
            ORDER BY t.created_at_ts DESC,
                     CAST(t.sort_index AS INTEGER) DESC,
                     t.tweet_id DESC
        """
        quoted_query = f"""
            SELECT o.tweet_id, o.created_at_ts AS sort_ts
            FROM archive o INDEXED BY idx_archive_record_page
            WHERE o.record_type = 'tweet_object'
              AND o.enrichment_state IN ({state_placeholders})
              AND {valid_tag_sql.format(tweet_id="o.tweet_id")}
              AND EXISTS (
                  SELECT 1
                  FROM archive relation INDEXED BY idx_archive_target_tweet_id
                  WHERE relation.target_tweet_id = o.tweet_id
                    AND relation.record_type = 'tweet_relation'
                    AND relation.relation_type = 'quote_of'
                    AND EXISTS (
                        SELECT 1
                        FROM archive saved INDEXED BY idx_archive_tweet_id
                        WHERE saved.tweet_id = relation.tweet_id
                          AND saved.record_type = 'tweet'
                          AND EXISTS (
                              SELECT 1
                              FROM archive source_object INDEXED BY idx_archive_tweet_id
                              WHERE source_object.tweet_id = saved.tweet_id
                                AND source_object.record_type = 'tweet_object'
                                AND source_object.enrichment_state IN ({state_placeholders})
                          )
                    )
              )
            ORDER BY o.created_at_ts DESC,
                     CAST(o.sort_index AS INTEGER) DESC,
                     o.tweet_id DESC
        """
        classification_query = """
            SELECT
                (
                    SELECT MIN(relation.target_tweet_id)
                    FROM archive relation INDEXED BY idx_archive_tweet_id
                    WHERE relation.tweet_id = ?
                      AND relation.record_type = 'tweet_relation'
                      AND relation.relation_type = 'quote_of'
                ) AS quoted_tweet_id,
                CASE WHEN
                    EXISTS (
                        SELECT 1
                        FROM archive media INDEXED BY idx_archive_tweet_id
                        WHERE media.tweet_id = ?
                          AND media.record_type = 'media'
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM archive relation INDEXED BY idx_archive_tweet_id
                        JOIN archive quoted_media INDEXED BY idx_archive_tweet_id
                          ON quoted_media.tweet_id = relation.target_tweet_id
                         AND quoted_media.record_type = 'media'
                        WHERE relation.tweet_id = ?
                          AND relation.record_type = 'tweet_relation'
                          AND relation.relation_type = 'quote_of'
                    )
                    THEN 'media' ELSE 'text'
                END AS content_type
        """

        excluded = exclude_tweet_ids or set()
        seen: set[str] = set()
        selected_type: str | None = None
        selected: list[dict[str, Any]] = []

        def ordered_unique_rows(rows: sqlite3.Cursor) -> Iterator[sqlite3.Row]:
            """Restore tweet-ID tie ordering without an SQLite temp sort."""
            group_ts: int | None = None
            group: dict[str, sqlite3.Row] = {}
            for row in rows:
                sort_ts = int(row["sort_ts"] or 0)
                if group_ts is not None and sort_ts != group_ts:
                    for tweet_id in sorted(group, reverse=True):
                        yield group[tweet_id]
                    group.clear()
                group_ts = sort_ts
                tweet_id = str(row["tweet_id"])
                group.setdefault(tweet_id, row)
            for tweet_id in sorted(group, reverse=True):
                yield group[tweet_id]

        def next_row(rows: Iterator[sqlite3.Row]) -> sqlite3.Row | None:
            return next(rows, None)

        def row_key(row: sqlite3.Row) -> tuple[int, str]:
            return int(row["sort_ts"] or 0), str(row["tweet_id"])

        saved_rows = self.conn.execute(saved_query, AVAILABLE_ENRICHMENT_STATES)
        quoted_rows = self.conn.execute(
            quoted_query,
            (*AVAILABLE_ENRICHMENT_STATES, *AVAILABLE_ENRICHMENT_STATES),
        )
        try:
            saved_stream = ordered_unique_rows(saved_rows)
            quoted_stream = ordered_unique_rows(quoted_rows)
            saved_row = next_row(saved_stream)
            quoted_row = next_row(quoted_stream)
            while saved_row is not None or quoted_row is not None:
                if quoted_row is None or (
                    saved_row is not None and row_key(saved_row) >= row_key(quoted_row)
                ):
                    row = saved_row
                    saved_row = next_row(saved_stream)
                else:
                    row = quoted_row
                    quoted_row = next_row(quoted_stream)
                if row is None:
                    continue
                tweet_id = str(row["tweet_id"])
                if tweet_id in seen:
                    continue
                seen.add(tweet_id)
                if tweet_id in excluded:
                    continue
                classification = self.conn.execute(
                    classification_query,
                    (tweet_id, tweet_id, tweet_id),
                ).fetchone()
                if classification is None:
                    continue
                content_type = str(classification["content_type"])
                if selected_type is None:
                    selected_type = content_type
                if content_type != selected_type:
                    continue
                selected.append(
                    {
                        "tweet_id": tweet_id,
                        "quoted_tweet_id": classification["quoted_tweet_id"],
                        "content_type": content_type,
                        "sort_ts": int(row["sort_ts"] or 0),
                    }
                )
                if len(selected) >= limit:
                    break
        finally:
            saved_rows.close()
            quoted_rows.close()
        return selected

    def get_eligible_tweets_for_tagging(self, *, limit: int = 20) -> list[str]:
        """Compatibility wrapper returning IDs from the next homogeneous tag batch."""
        return [row["tweet_id"] for row in self.get_eligible_tagging_candidates(limit=limit)]

    def get_tagging_coverage_counts(self) -> tuple[int, int]:
        """Return eligible and validly tagged tweet counts for coverage reporting."""
        state_placeholders = ", ".join("?" for _state in AVAILABLE_ENRICHMENT_STATES)
        query = f"""
            WITH saved AS (
                SELECT DISTINCT t.tweet_id
                FROM archive t INDEXED BY idx_archive_record_page
                WHERE t.record_type = 'tweet'
                  AND EXISTS (
                      SELECT 1
                      FROM archive o INDEXED BY idx_archive_tweet_id
                      WHERE o.tweet_id = t.tweet_id
                        AND o.record_type = 'tweet_object'
                        AND o.enrichment_state IN ({state_placeholders})
                  )
            ),
            quoted AS (
                SELECT DISTINCT relation.target_tweet_id AS tweet_id
                FROM saved
                CROSS JOIN archive relation INDEXED BY idx_archive_tweet_id
                WHERE relation.tweet_id = saved.tweet_id
                  AND relation.record_type = 'tweet_relation'
                  AND relation.relation_type = 'quote_of'
                  AND EXISTS (
                      SELECT 1
                      FROM archive quoted_object INDEXED BY idx_archive_tweet_id
                      WHERE quoted_object.tweet_id = relation.target_tweet_id
                        AND quoted_object.record_type = 'tweet_object'
                        AND quoted_object.enrichment_state IN ({state_placeholders})
                  )
            ),
            eligible_tweets AS (
                SELECT tweet_id FROM saved
                UNION
                SELECT tweet_id FROM quoted
            )
            SELECT
                COUNT(*) AS eligible_tweets,
                COALESCE(SUM(EXISTS (
                    SELECT 1
                    FROM archive tg INDEXED BY idx_archive_tweet_id
                    WHERE tg.tweet_id = eligible_tweets.tweet_id
                      AND tg.record_type = 'media_tag'
                      AND (tg.enrichment_state = 'done' OR tg.enrichment_state IS NULL)
                      AND json_valid(tg.raw_json)
                      AND json_type(tg.raw_json, '$.tags') = 'array'
                      AND json_array_length(tg.raw_json, '$.tags') > 0
                )), 0) AS tagged_tweets
            FROM eligible_tweets
        """
        row = self.conn.execute(
            query,
            (*AVAILABLE_ENRICHMENT_STATES, *AVAILABLE_ENRICHMENT_STATES),
        ).fetchone()
        if row is None:
            return 0, 0
        return int(row["eligible_tweets"]), int(row["tagged_tweets"])

    def delete_media_tag(self, tweet_id: str) -> None:
        self.conn.execute(
            "DELETE FROM archive WHERE record_type = 'media_tag' AND tweet_id = ?", (tweet_id,)
        )
        self.conn.commit()

    def update_media_tags(
        self,
        tweet_id: str,
        tags: list[str],
        *,
        description: str | None = None,
    ) -> None:
        """Overwrite tags and optionally the description for a specific tweet."""
        normalized_tags: list[str] = []
        seen: set[str] = set()
        for tag in tags:
            cleaned = tag.strip()
            folded = cleaned.casefold()
            if not cleaned or folded in seen:
                continue
            seen.add(folded)
            normalized_tags.append(cleaned)

        row = self.conn.execute(
            "SELECT raw_json FROM archive WHERE record_type = 'media_tag' AND tweet_id = ?",
            (tweet_id,),
        ).fetchone()

        cleaned_description = description.strip() if description is not None else None
        if not normalized_tags and (description is None or not cleaned_description):
            self.delete_media_tag(tweet_id)
            return

        now_ts = str(time.time())
        if row and row["raw_json"]:
            try:
                data = json.loads(row["raw_json"])
            except json.JSONDecodeError:
                data = {}
            if not isinstance(data, dict):
                data = {}
            data["tags"] = normalized_tags
        else:
            data = {"description": "", "tags": normalized_tags}

        if cleaned_description is not None:
            data["description"] = cleaned_description

        payload = json.dumps(data)

        self.conn.execute(
            "INSERT OR REPLACE INTO archive "
            "(row_key, record_type, tweet_id, raw_json, enrichment_state, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (f"media_tag:{tweet_id}", "media_tag", tweet_id, payload, "done", now_ts),
        )
        self.conn.commit()

    def delete_global_tag(self, tag: str) -> None:
        """Delete a tag globally across all tweets."""
        sql = """
        SELECT a.tweet_id, a.raw_json
        FROM archive a,
             json_each(
               CASE WHEN json_valid(a.raw_json) THEN a.raw_json ELSE '{"tags":[]}' END,
               '$.tags'
             ) as t
        WHERE a.record_type = 'media_tag' AND LOWER(t.value) = LOWER(?)
        """
        rows = self.conn.execute(sql, (tag,)).fetchall()

        now_ts = str(time.time())
        for row in rows:
            tid = row["tweet_id"]
            if not row["raw_json"]:
                continue
            try:
                data = json.loads(row["raw_json"])
                if "tags" in data:
                    data["tags"] = [t for t in data["tags"] if t.lower() != tag.lower()]
                    if not data["tags"]:
                        self.conn.execute(
                            "DELETE FROM archive WHERE record_type = 'media_tag' AND tweet_id = ?",
                            (tid,),
                        )
                    else:
                        self.conn.execute(
                            "UPDATE archive SET raw_json = ?, updated_at = ? "
                            "WHERE record_type = 'media_tag' AND tweet_id = ?",
                            (json.dumps(data), now_ts, tid),
                        )
            except json.JSONDecodeError:
                pass
        self.conn.commit()

    def merge_global_tags(self, primary_tag: str, merge_tags: list[str]) -> None:
        """Merge a list of tags into a primary tag globally."""
        primary_tag = primary_tag.strip()
        normalized_merge_tags = list(
            dict.fromkeys(
                tag.strip().casefold()
                for tag in merge_tags
                if tag.strip() and tag.strip().casefold() != primary_tag.casefold()
            )
        )
        if not primary_tag or not normalized_merge_tags:
            return

        merge_tags_lower = set(normalized_merge_tags)
        placeholders = ",".join("?" for _ in normalized_merge_tags)

        sql = f"""
        SELECT DISTINCT a.tweet_id, a.raw_json
        FROM archive a,
             json_each(
               CASE WHEN json_valid(a.raw_json) THEN a.raw_json ELSE '{{"tags":[]}}' END,
               '$.tags'
             ) as t
        WHERE a.record_type = 'media_tag' AND LOWER(t.value) IN ({placeholders})
        """

        rows = self.conn.execute(sql, tuple(normalized_merge_tags)).fetchall()
        now_ts = str(time.time())

        for row in rows:
            tid = row["tweet_id"]
            if not row["raw_json"]:
                continue
            try:
                data = json.loads(row["raw_json"])
                if "tags" in data:
                    existing_tags = data["tags"]
                    new_tags: list[str] = []
                    seen_tags: set[str] = set()
                    has_primary = False

                    for t in existing_tags:
                        if not isinstance(t, str):
                            continue
                        t_low = t.casefold()
                        if t_low == primary_tag.casefold():
                            has_primary = True
                            if t_low not in seen_tags:
                                new_tags.append(t)
                                seen_tags.add(t_low)
                        elif t_low not in merge_tags_lower and t_low not in seen_tags:
                            new_tags.append(t)
                            seen_tags.add(t_low)

                    if not has_primary:
                        new_tags.append(primary_tag)

                    data["tags"] = new_tags

                    self.conn.execute(
                        "UPDATE archive SET raw_json = ?, updated_at = ? "
                        "WHERE record_type = 'media_tag' AND tweet_id = ?",
                        (json.dumps(data), now_ts, tid),
                    )
            except json.JSONDecodeError:
                pass
        self.conn.commit()

    def update_tweet_object_enrichment(
        self,
        tweet_id: str,
        *,
        enrichment_state: str,
        enrichment_checked_at: str | None,
        enrichment_http_status: int | None,
        enrichment_reason: str | None,
        enrichment_detail: str | None | object = _UNSET,
        enrichment_retry_count: int | None | object = _UNSET,
        enrichment_next_retry_at: str | None | object = _UNSET,
        enrichment_first_unavailable_at: str | None | object = _UNSET,
        enrichment_retry_eligible: bool | int | None | object = _UNSET,
        cursor: _PageBuffer | None = None,
    ) -> None:
        changes = {
            "enrichment_state": enrichment_state,
            "enrichment_checked_at": enrichment_checked_at,
            "enrichment_http_status": enrichment_http_status,
            "enrichment_reason": enrichment_reason,
            "updated_at": utc_now(),
        }
        optional_updates = {
            "enrichment_detail": enrichment_detail,
            "enrichment_retry_count": enrichment_retry_count,
            "enrichment_next_retry_at": enrichment_next_retry_at,
            "enrichment_first_unavailable_at": enrichment_first_unavailable_at,
            "enrichment_retry_eligible": enrichment_retry_eligible,
        }
        for field_name, value in optional_updates.items():
            if value is _UNSET:
                continue
            if field_name == "enrichment_retry_eligible" and value is not None:
                value = int(bool(value))
            changes[field_name] = value
        row_key = self._row_key_for_tweet_object(tweet_id)
        if cursor is None:
            with self.conn:
                result = self.conn.execute(
                    f"UPDATE archive SET {', '.join(f'{field} = ?' for field in changes)} "
                    "WHERE row_key = ?",
                    (*changes.values(), row_key),
                )
                if not result.rowcount:
                    raise KeyError(f"Tweet object row not found: {tweet_id}")
            return
        row = self._lookup_row(row_key, cursor=cursor)
        if row is None:
            raise KeyError(f"Tweet object row not found: {tweet_id}")
        updated = {**row, **changes}
        self._queue_record(updated, cursor=cursor)

    def _refresh_tweet_records_for_detail(
        self,
        tweet: TimelineTweet,
        *,
        cursor: _PageBuffer,
    ) -> None:
        legacy = tweet.raw_json.get("legacy") or {}
        rows = self._rows_for_values("tweet", "tweet_id", [tweet.tweet_id])
        now = utc_now()
        for row in rows:
            updated = dict(row)
            updated["text"] = self._coalesce_value(tweet.text, row.get("text"))
            updated["author_id"] = self._coalesce_value(tweet.author_id, row.get("author_id"))
            updated["author_username"] = self._coalesce_value(
                tweet.author_username,
                row.get("author_username"),
            )
            updated["author_display_name"] = self._coalesce_value(
                tweet.author_display_name,
                row.get("author_display_name"),
            )
            updated["created_at"] = self._coalesce_value(tweet.created_at, row.get("created_at"))
            updated["created_at_ts"] = (
                int(_parse_created_at(updated["created_at"]).timestamp())
                if _parse_created_at(updated["created_at"])
                else None
            )
            updated["conversation_id"] = self._coalesce_value(
                legacy.get("conversation_id_str"),
                row.get("conversation_id"),
            )
            updated["lang"] = self._coalesce_value(legacy.get("lang"), row.get("lang"))
            updated["note_tweet_text"] = self._coalesce_value(
                extract_note_tweet_text(tweet.raw_json),
                row.get("note_tweet_text"),
            )
            updated["source"] = LIVE_SOURCE
            updated["deleted_at"] = None
            updated["raw_json"] = self._json_value(tweet.raw_json)
            updated["last_seen_at"] = now
            updated["synced_at"] = now

            if row.get("enrichment_state") == "terminal_unavailable":
                updated["enrichment_state"] = "resurrected"

            cursor.records[updated["row_key"]] = updated

    def _refresh_tweet_records_for_details(
        self,
        tweets: list[TimelineTweet],
        *,
        cursor: _PageBuffer,
    ) -> None:
        for tweet in tweets:
            self._refresh_tweet_records_for_detail(tweet, cursor=cursor)

    def persist_tweet_detail(
        self,
        *,
        tweet: TimelineTweet,
        raw_json: dict[str, Any],
        http_status: int = 200,
        cursor: _PageBuffer | None = None,
    ) -> None:
        owns_buffer = cursor is None
        buffer = cursor or _PageBuffer()
        existing = self._lookup_row(self._row_key_for_tweet_object(tweet.tweet_id), cursor=buffer)
        prior_state = existing.get("enrichment_state") if existing is not None else None
        self.append_raw_capture(
            "TweetDetail",
            tweet.tweet_id,
            None,
            http_status,
            raw_json,
            source=LIVE_SOURCE,
            cursor=buffer,
        )
        self._refresh_tweet_records_for_details([tweet], cursor=buffer)

        self._buffer_secondary_graph(
            extract_secondary_objects(tweet.raw_json),
            source=LIVE_SOURCE,
            cursor=buffer,
        )

        self.update_tweet_object_enrichment(
            tweet.tweet_id,
            enrichment_state=(
                "resurrected" if prior_state in {"terminal_unavailable", "resurrected"} else "done"
            ),
            enrichment_checked_at=utc_now(),
            enrichment_http_status=http_status,
            enrichment_reason=None,
            enrichment_detail=None,
            enrichment_retry_count=0,
            enrichment_next_retry_at=None,
            enrichment_first_unavailable_at=None,
            enrichment_retry_eligible=False,
            cursor=buffer,
        )
        if owns_buffer:
            self._merge_records(list(buffer.records.values()))

    def persist_thread_detail(
        self,
        *,
        focal_tweet_id: str,
        tweets: list[TimelineTweet],
        raw_json: dict[str, Any],
        http_status: int = 200,
        cursor: _PageBuffer | None = None,
    ) -> None:
        owns_buffer = cursor is None
        buffer = cursor or _PageBuffer()
        self.append_raw_capture(
            "ThreadExpandDetail",
            focal_tweet_id,
            None,
            http_status,
            raw_json,
            source=LIVE_SOURCE,
            cursor=buffer,
        )
        self._refresh_tweet_records_for_details(tweets, cursor=buffer)
        self._buffer_secondary_graph(
            extract_thread_objects([tweet.raw_json for tweet in tweets]),
            source=LIVE_SOURCE,
            cursor=buffer,
        )
        if owns_buffer:
            self._merge_records(list(buffer.records.values()))

    def persist_unavailable_tweet(
        self,
        *,
        tweet_id: str,
        operation: str,
        raw_json: dict[str, Any],
        http_status: int | None,
        reason: str,
        detail: str | None,
        retry_eligible: bool,
        next_retry_at: str | None,
        retry_count: int | None = None,
        checked_at: str | None = None,
        cursor: _PageBuffer | None = None,
    ) -> None:
        owns_buffer = cursor is None
        buffer = cursor or _PageBuffer()
        checked_at = checked_at or utc_now()
        self.append_raw_capture(
            operation,
            tweet_id,
            None,
            http_status or 0,
            raw_json,
            source=LIVE_SOURCE,
            cursor=buffer,
        )
        row_key = self._row_key_for_tweet_object(tweet_id)
        existing = self._lookup_row(row_key, cursor=buffer)
        if existing is None:
            existing = self._record(
                record_type="tweet_object",
                row_key=row_key,
                tweet_id=tweet_id,
                source=LIVE_SOURCE,
                raw_json=self._json_value(raw_json),
                first_seen_at=checked_at,
                added_at=checked_at,
            )
        updated = dict(existing)
        current_retry_count = int(updated.get("enrichment_retry_count") or 0)
        updated.update(
            {
                "enrichment_state": "terminal_unavailable",
                "enrichment_checked_at": checked_at,
                "enrichment_http_status": http_status,
                "enrichment_reason": reason,
                "enrichment_detail": detail,
                "enrichment_retry_count": (
                    current_retry_count if retry_count is None else retry_count
                ),
                "enrichment_next_retry_at": next_retry_at,
                "enrichment_first_unavailable_at": (
                    updated.get("enrichment_first_unavailable_at") or checked_at
                ),
                "enrichment_retry_eligible": int(retry_eligible),
                "updated_at": checked_at,
            }
        )
        self._queue_record(updated, cursor=buffer)
        if owns_buffer:
            self._merge_records(list(buffer.records.values()))

    def persist_terminal_unavailable_target(self, focal_tweet_id: str, operation: str) -> None:
        from tweetnook.resurrection import resurrection_retry_schedule

        retry_eligible, next_retry_at = resurrection_retry_schedule("unavailable_unknown", 0)
        self.persist_unavailable_tweet(
            tweet_id=focal_tweet_id,
            operation=operation,
            raw_json={"__typename__": "TweetUnavailable"},
            http_status=404,
            reason="unavailable_unknown",
            detail=None,
            retry_eligible=retry_eligible,
            next_retry_at=next_retry_at,
        )

    def list_membership_tweet_ids(self, *, limit: int | None = None) -> list[str]:
        rows = self._query(expr="record_type = 'tweet'", cols=["tweet_id", "added_at"])
        rows.sort(key=lambda row: (row.get("added_at") or "", row.get("tweet_id") or ""))
        tweet_ids = [row["tweet_id"] for row in rows if row.get("tweet_id")]
        unique = list(dict.fromkeys(tweet_ids))
        return unique[:limit] if limit is not None else unique

    def list_known_tweet_ids(self) -> set[str]:
        tweet_rows = self._query(expr="record_type = 'tweet'", cols=["tweet_id"])
        tweet_object_rows = self._query(expr="record_type = 'tweet_object'", cols=["tweet_id"])
        tweet_ids = {
            row["tweet_id"]
            for row in tweet_rows + tweet_object_rows
            if isinstance(row.get("tweet_id"), str) and row["tweet_id"]
        }
        return tweet_ids

    def list_raw_capture_target_ids(
        self,
        operation: str,
        *,
        limit: int | None = None,
    ) -> list[str]:
        expr = (
            "record_type = 'raw_capture' "
            f"AND operation = {_expr_quote(operation)} "
            "AND cursor_in IS NOT NULL"
        )
        rows = self._query(expr=expr, cols=["captured_at", "cursor_in"])
        rows.sort(key=lambda row: (row.get("captured_at") or "", row.get("cursor_in") or ""))
        targets = [row["cursor_in"] for row in rows if isinstance(row.get("cursor_in"), str)]
        unique = list(dict.fromkeys(targets))
        return unique[:limit] if limit is not None else unique

    def list_url_ref_rows(self) -> list[dict[str, Any]]:
        rows = self._query(
            expr="record_type = 'url_ref'",
            cols=["tweet_id", "position", "canonical_url", "expanded_url", "url"],
        )
        rows.sort(
            key=lambda row: (
                row.get("tweet_id") or "",
                row.get("position") if row.get("position") is not None else 1_000_000,
            )
        )
        return rows

    def list_quote_relation_rows(self, source_tweet_ids: set[str]) -> list[dict[str, Any]]:
        if not source_tweet_ids:
            return []
        rows: list[dict[str, Any]] = []
        source_ids = sorted(source_tweet_ids)
        chunk_size = 500
        for start in range(0, len(source_ids), chunk_size):
            chunk = source_ids[start : start + chunk_size]
            placeholders = ", ".join("?" for _ in chunk)
            query = f"""
                SELECT tweet_id, target_tweet_id
                FROM archive INDEXED BY idx_archive_tweet_id
                WHERE record_type = 'tweet_relation'
                  AND relation_type = 'quote_of'
                  AND tweet_id IN ({placeholders})
                ORDER BY tweet_id, target_tweet_id
            """
            rows.extend(dict(row) for row in self.conn.execute(query, chunk).fetchall())
        return rows

    def _serialize_media_row(self, row: dict[str, Any]) -> dict[str, Any]:
        variants = json.loads(row["variants_json"]) if row.get("variants_json") else []
        return {
            "media_key": row.get("media_key"),
            "type": row.get("media_type"),
            "source": row.get("source"),
            "article_id": row.get("article_id"),
            "position": row.get("position"),
            "url": row.get("media_url"),
            "thumbnail_url": row.get("thumbnail_url"),
            "width": row.get("width"),
            "height": row.get("height"),
            "duration_millis": row.get("duration_millis"),
            "variants": variants,
            "download": {
                "state": row.get("download_state") or "pending",
                "local_path": row.get("local_path"),
                "sha256": row.get("sha256"),
                "byte_size": row.get("byte_size"),
                "content_type": row.get("content_type"),
                "thumbnail_local_path": row.get("thumbnail_local_path"),
                "thumbnail_sha256": row.get("thumbnail_sha256"),
                "thumbnail_byte_size": row.get("thumbnail_byte_size"),
                "thumbnail_content_type": row.get("thumbnail_content_type"),
                "downloaded_at": row.get("downloaded_at"),
                "error": row.get("download_error"),
            },
        }

    def _serialize_article_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "article_id": row.get("article_id"),
            "title": row.get("title"),
            "summary_text": row.get("summary_text"),
            "content_text": row.get("content_text"),
            "canonical_url": row.get("canonical_url"),
            "published_at": row.get("published_at"),
            "status": row.get("status"),
        }

    def _serialize_url_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "url_hash": row.get("url_hash"),
            "canonical_url": row.get("canonical_url"),
            "expanded_url": row.get("expanded_url"),
            "final_url": row.get("final_url"),
            "host": row.get("url_host"),
            "title": row.get("title"),
            "description": row.get("description"),
            "site_name": row.get("site_name"),
            "content_type": row.get("content_type"),
            "http_status": row.get("http_status"),
            "unfurl_state": row.get("unfurl_state") or "pending",
            "last_fetched_at": row.get("last_fetched_at"),
            "error": row.get("download_error"),
        }

    def count_export_rows(self, collection: str) -> int:
        revision = self.cache_revision("membership")
        cached = self._count_cache.get(collection)
        if not self.conn.in_transaction and cached is not None and cached[0] == revision:
            return cached[1]
        sql = (
            "SELECT COUNT(DISTINCT tweet_id) FROM archive INDEXED BY idx_archive_membership "
            "WHERE record_type = 'tweet'"
        )
        params: tuple[str, ...] = ()
        if collection != "all":
            sql += " AND collection_type = ?"
            params = (collection,)
        count = int(self.conn.execute(sql, params).fetchone()[0])
        if not self.conn.in_transaction:
            if len(self._count_cache) >= 32:
                self._count_cache.clear()
            self._count_cache[collection] = (revision, count)
        else:
            self._count_cache.clear()
        return count

    def get_like_order(self):
        """Return the derived order independently of tweet content/source updates."""
        from tweetnook.like_order import load_like_order

        with self._like_order_lock:
            return load_like_order(self)

    def _ensure_like_order_table(self) -> None:
        """Index the immutable derived sequence once per input revision/connection.

        Call under _like_order_lock. A savepoint preserves any caller transaction;
        temporary rows never become part of the on-disk archive or backups.
        """
        order = self.get_like_order()
        if getattr(self, "_indexed_like_order", None) is order and not self.conn.in_transaction:
            return
        self.conn.execute("SAVEPOINT like_order_index")
        try:
            self.conn.execute(
                "CREATE TEMP TABLE IF NOT EXISTS tweetnook_like_order "
                "(tweet_id TEXT PRIMARY KEY, latest_position INTEGER UNIQUE, "
                "earliest_position INTEGER UNIQUE)"
            )
            self.conn.execute("DELETE FROM temp.tweetnook_like_order")
            earliest = {
                tweet_id: i for i, tweet_id in enumerate(order.ordered_ids("liked_earliest"))
            }
            self.conn.executemany(
                "INSERT INTO temp.tweetnook_like_order VALUES (?, ?, ?)",
                ((tweet_id, i, earliest[tweet_id]) for i, tweet_id in enumerate(order.ids)),
            )
        except BaseException:
            self.conn.execute("ROLLBACK TO like_order_index")
            raise
        finally:
            self.conn.execute("RELEASE like_order_index")
        self._indexed_like_order = order if not self.conn.in_transaction else None

    def query_like_order_ids(
        self,
        filter_expr: str,
        *,
        sort: str,
        limit: int,
        offset: int,
        after_position: int | None = None,
    ) -> list[dict[str, Any]]:
        """Filter before pagination without hydrating every liked tweet.

        filter_expr comes from the internal search planner, never directly from
        an HTTP parameter. Position ranges use the temporary sequence index.
        """
        position = "earliest_position" if sort == "liked_earliest" else "latest_position"
        with self._like_order_lock:
            self._ensure_like_order_table()
            rows = self.conn.execute(
                f"SELECT ordering.tweet_id, ordering.{position} AS like_position "
                "FROM temp.tweetnook_like_order AS ordering "
                f"WHERE ordering.{position} > ? AND EXISTS "
                "(SELECT 1 FROM archive INDEXED BY idx_archive_tweet_id "
                f"WHERE archive.tweet_id = ordering.tweet_id AND ({filter_expr})) "
                f"ORDER BY ordering.{position} LIMIT ? OFFSET ?",
                (-1 if after_position is None else after_position, limit, offset),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_paginated_tweet_ids(
        self, collection: str, limit: int, offset: int, sort: str = "newest"
    ) -> list[str]:
        return [
            row["tweet_id"]
            for row in self.get_paginated_tweet_rows(collection, limit, offset, sort)
        ]

    def get_paginated_tweet_rows(
        self,
        collection: str,
        limit: int,
        offset: int,
        sort: str = "newest",
        *,
        after: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if sort not in {"newest", "oldest", "random"}:
            raise ValueError(f"Unsupported sort order: {sort}")
        if limit < 0:
            raise ValueError("limit must be non-negative")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        where = ["candidate.record_type = 'tweet'"]
        duplicate_where = [
            "duplicate.record_type = 'tweet'",
            "duplicate.tweet_id = candidate.tweet_id",
            "duplicate.row_key < candidate.row_key",
        ]
        params: list[Any] = []
        if collection != "all":
            where.append("candidate.collection_type = ?")
            duplicate_where.append("duplicate.collection_type = ?")
            params.extend([collection, collection])

        order_by = (
            "candidate.created_at_ts DESC, "
            "CAST(candidate.sort_index AS INTEGER) DESC, candidate.tweet_id DESC"
        )
        if sort == "oldest":
            order_by = (
                "candidate.created_at_ts ASC, "
                "CAST(candidate.sort_index AS INTEGER) ASC, candidate.tweet_id ASC"
            )
        elif sort == "random":
            order_by = "RANDOM()"

        sql = (
            "SELECT candidate.tweet_id, candidate.created_at_ts, "
            "CAST(candidate.sort_index AS INTEGER) AS sort_index FROM archive AS candidate "
            f"WHERE {' AND '.join(where)} "
            "AND NOT EXISTS ("
            "SELECT 1 FROM archive AS duplicate INDEXED BY idx_archive_membership "
            f"WHERE {' AND '.join(duplicate_where)}"
            ") "
        )
        if after is None:
            return [
                dict(row)
                for row in self.conn.execute(
                    f"{sql} ORDER BY {order_by} LIMIT ? OFFSET ?", [*params, limit, offset]
                )
                if row["tweet_id"]
            ]
        # Disjoint, ordered ranges preserve SQLite's NULL/tie semantics and let
        # every continuation seek into the composite index. A single OR across
        # nullable keys can instead scan all preceding pages.
        columns = [
            "candidate.created_at_ts",
            "CAST(candidate.sort_index AS INTEGER)",
            "candidate.tweet_id",
        ]
        values = [after["created_at_ts"], after["sort_index"], after["tweet_id"]]
        result = []
        direction = "DESC" if sort == "newest" else "ASC"
        for condition, bounds, order_start in self._page_cursor_ranges(
            columns, values, sort == "newest"
        ):
            range_order = ", ".join(f"{column} {direction}" for column in columns[order_start:])
            result.extend(
                dict(row)
                for row in self.conn.execute(
                    f"{sql} AND ({condition}) ORDER BY {range_order} LIMIT ?",
                    [*params, *bounds, limit - len(result)],
                )
                if row["tweet_id"]
            )
            if len(result) >= limit:
                break
        return result

    @staticmethod
    def _page_cursor_ranges(columns, values, descending):
        """Yield lexicographic continuation ranges in native SQLite NULL order."""
        for index in range(len(columns) - 1, -1, -1):
            prefix = [f"{column} IS ?" for column in columns[:index]]
            value = values[index]
            column = columns[index]
            if value is not None:
                comparison = "<" if descending else ">"
                yield (
                    " AND ".join([*prefix, f"{column} {comparison} ?"]),
                    [*values[:index], value],
                    index,
                )
                if descending and index < len(columns) - 1:
                    yield " AND ".join([*prefix, f"{column} IS NULL"]), values[:index], index + 1
            elif not descending:
                yield " AND ".join([*prefix, f"{column} IS NOT NULL"]), values[:index], index

    def _hydrate_exported_rows(
        self,
        sorted_rows: list[dict[str, Any]],
        include_raw_json: bool,
        defer_raw_json: bool,
    ) -> list[dict[str, Any]]:
        tweet_ids = [row["tweet_id"] for row in sorted_rows if row.get("tweet_id")]

        if defer_raw_json and tweet_ids:
            raw_rows = self._rows_for_values(
                "tweet", "tweet_id", tweet_ids, columns=["tweet_id", "raw_json"]
            )
            raw_map = {r.get("tweet_id"): r.get("raw_json") for r in raw_rows}
            for row in sorted_rows:
                if row.get("tweet_id") in raw_map:
                    row["raw_json"] = raw_map[row["tweet_id"]]

        media_cols = [
            "tweet_id",
            "media_key",
            "media_type",
            "source",
            "article_id",
            "position",
            "media_url",
            "thumbnail_url",
            "width",
            "height",
            "duration_millis",
            "variants_json",
            "download_state",
            "local_path",
            "sha256",
            "byte_size",
            "content_type",
            "thumbnail_local_path",
            "thumbnail_sha256",
            "thumbnail_byte_size",
            "thumbnail_content_type",
            "downloaded_at",
            "download_error",
        ]
        article_cols = [
            "tweet_id",
            "article_id",
            "title",
            "summary_text",
            "content_text",
            "canonical_url",
            "published_at",
            "status",
        ]
        url_ref_cols = [
            "tweet_id",
            "url_hash",
            "url",
            "expanded_url",
            "display_url",
            "canonical_url",
            "position",
        ]
        url_cols = [
            "url_hash",
            "canonical_url",
            "expanded_url",
            "final_url",
            "url_host",
            "title",
            "description",
            "site_name",
            "content_type",
            "http_status",
            "unfurl_state",
            "last_fetched_at",
            "download_error",
        ]

        media_rows = self._rows_for_values("media", "tweet_id", tweet_ids, columns=media_cols)
        article_rows = self._rows_for_values("article", "tweet_id", tweet_ids, columns=article_cols)
        url_ref_rows = self._rows_for_values("url_ref", "tweet_id", tweet_ids, columns=url_ref_cols)
        url_hashes = [row["url_hash"] for row in url_ref_rows if row.get("url_hash")]
        url_rows = self._rows_for_values("url", "url_hash", url_hashes, columns=url_cols)

        media_by_tweet: dict[str, list[dict[str, Any]]] = {}
        for row in sorted(
            media_rows,
            key=lambda item: (
                item.get("tweet_id") or "",
                item.get("position") if item.get("position") is not None else 1_000_000,
            ),
        ):
            media_by_tweet.setdefault(row["tweet_id"], []).append(self._serialize_media_row(row))

        articles_by_tweet = {
            row["tweet_id"]: self._serialize_article_row(row)
            for row in article_rows
            if row.get("tweet_id")
        }
        urls_by_hash = {
            row["url_hash"]: self._serialize_url_row(row) for row in url_rows if row.get("url_hash")
        }
        url_refs_by_tweet: dict[str, list[dict[str, Any]]] = {}
        for row in sorted(
            url_ref_rows,
            key=lambda item: (
                item.get("tweet_id") or "",
                item.get("position") if item.get("position") is not None else 1_000_000,
            ),
        ):
            resolved = urls_by_hash.get(row.get("url_hash"))
            url_refs_by_tweet.setdefault(row["tweet_id"], []).append(
                {
                    "position": row.get("position"),
                    "url_hash": row.get("url_hash"),
                    "short_url": row.get("url"),
                    "expanded_url": row.get("expanded_url"),
                    "display_url": row.get("display_url"),
                    "canonical_url": row.get("canonical_url"),
                    "resolved": resolved,
                }
            )

        quote_relations = self.list_quote_relation_rows(set(tweet_ids))
        quoted_tweet_by_source = {
            row["tweet_id"]: row["target_tweet_id"]
            for row in quote_relations
            if row.get("tweet_id") and row.get("target_tweet_id")
        }
        tag_tweet_ids = list(dict.fromkeys([*tweet_ids, *quoted_tweet_by_source.values()]))
        media_tag_rows = self._rows_for_values(
            "media_tag", "tweet_id", tag_tweet_ids, columns=["tweet_id", "raw_json"]
        )
        tags_by_tweet: dict[str, dict[str, Any]] = {}
        for row in media_tag_rows:
            raw_tag_json = row.get("raw_json")
            if not isinstance(raw_tag_json, str) or not raw_tag_json:
                continue
            try:
                tag_payload = json.loads(raw_tag_json)
            except json.JSONDecodeError:
                continue
            if isinstance(tag_payload, dict):
                tags_by_tweet[row["tweet_id"]] = tag_payload

        exported = []
        for row in sorted_rows:
            tweet_media = media_by_tweet.get(row["tweet_id"], [])
            article = articles_by_tweet.get(row["tweet_id"])
            if article is not None:
                article["media"] = [
                    item
                    for item in tweet_media
                    if item.get("article_id") == article.get("article_id")
                    or str(item.get("source") or "").startswith("article_")
                ]
            exported.append(
                {
                    "tweet_id": row["tweet_id"],
                    "text": row["text"],
                    "author": {
                        "id": row["author_id"],
                        "username": row["author_username"],
                        "display_name": row["author_display_name"],
                    },
                    "created_at": row["created_at"],
                    "collection": {
                        "type": row["collection_type"],
                        "folder_id": row["folder_id"] or None,
                        "sort_index": row["sort_index"],
                        "added_at": row["added_at"],
                        "synced_at": row["synced_at"],
                    },
                    "media": tweet_media,
                    "urls": url_refs_by_tweet.get(row["tweet_id"], []),
                    "article": article,
                    "media_tags": tags_by_tweet.get(row["tweet_id"]),
                    "qt_media_tags": tags_by_tweet.get(
                        quoted_tweet_by_source.get(row["tweet_id"], "")
                    ),
                    "raw_json": json.loads(row["raw_json"])
                    if include_raw_json and row.get("raw_json")
                    else None,
                }
            )
        return exported

    def fetch_tweets_by_ids(self, tweet_ids: list[str]) -> list[dict[str, Any]]:
        if not tweet_ids:
            return []

        tweet_columns = [
            "tweet_id",
            "text",
            "author_id",
            "author_username",
            "author_display_name",
            "created_at",
            "collection_type",
            "folder_id",
            "sort_index",
            "added_at",
            "synced_at",
            "raw_json",
        ]

        rows = self._rows_for_values("tweet", "tweet_id", tweet_ids, columns=tweet_columns)

        id_order = {tid: i for i, tid in enumerate(tweet_ids)}
        rows.sort(
            key=lambda row: (
                id_order.get(row.get("tweet_id"), 999999),
                SEARCH_COLLECTION_ORDER.index(row["collection_type"])
                if row.get("collection_type") in SEARCH_COLLECTION_ORDER
                else len(SEARCH_COLLECTION_ORDER),
                row.get("folder_id") or "",
            )
        )
        unique_rows: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for row in rows:
            tweet_id = row.get("tweet_id")
            if not isinstance(tweet_id, str) or tweet_id in seen_ids:
                continue
            seen_ids.add(tweet_id)
            unique_rows.append(row)

        return self._hydrate_exported_rows(
            unique_rows,
            include_raw_json=True,
            defer_raw_json=False,
        )

    def export_rows(
        self,
        collection: str,
        *,
        sort: str = "newest",
        offset: int = 0,
        limit: int | None = None,
        include_raw_json: bool = True,
    ) -> list[dict[str, Any]]:
        if sort not in {"newest", "oldest"}:
            raise ValueError(f"Unsupported sort order: {sort}")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        if limit is not None and limit < 0:
            raise ValueError("limit must be non-negative")
        filter_expr = "record_type = 'tweet'"
        if collection != "all":
            filter_expr += f" AND collection_type = {_expr_quote(collection)}"
        tweet_columns = [
            "tweet_id",
            "text",
            "author_id",
            "author_username",
            "author_display_name",
            "created_at",
            "collection_type",
            "folder_id",
            "sort_index",
            "added_at",
            "synced_at",
        ]

        if include_raw_json:
            tweet_columns.append("raw_json")

        # Rank lightweight membership keys in SQLite; materialize text, JSON, and
        # secondary objects only for the selected logical tweets. Legacy dates
        # without a timestamp retain the export parser's unknown-last semantics.
        def legacy_timestamp(value):
            parsed = _parse_created_at(value)
            return parsed.timestamp() if parsed is not None else None

        def export_integer_key(value):
            # Keep Python int() semantics (invalid values become zero), including
            # indices beyond SQLite's signed integer range. Length-prefixed digits
            # sort lexically as integers; complement negative keys to reverse them.
            try:
                number = int(value or 0)
            except (TypeError, ValueError):
                number = 0
            digits = str(abs(number))
            key = f"{len(digits):020d}{digits}"
            if number < 0:
                return "0" + key.translate(str.maketrans("0123456789", "9876543210"))
            return "1" + key

        self.conn.create_function(
            "tweetnook_export_timestamp", 1, legacy_timestamp, deterministic=True
        )
        self.conn.create_function(
            "tweetnook_export_integer", 1, export_integer_key, deterministic=True
        )
        direction = "ASC" if sort == "oldest" else "DESC"
        order = (
            f"export_timestamp IS NULL, export_timestamp {direction}, "
            f"export_sort {direction}, tweet_id ASC, source_rowid ASC"
        )
        keys = self.conn.execute(
            "WITH candidates AS MATERIALIZED ("
            "SELECT row_key, rowid AS source_rowid, tweet_id, "
            "COALESCE(created_at_ts, tweetnook_export_timestamp(created_at)) AS export_timestamp, "
            "tweetnook_export_integer(sort_index) AS export_sort "
            f"FROM archive WHERE {filter_expr} AND tweet_id IS NOT NULL), "
            "ranked AS (SELECT *, ROW_NUMBER() OVER ("
            f"PARTITION BY tweet_id ORDER BY {order}) AS duplicate_rank FROM candidates) "
            "SELECT row_key FROM ranked WHERE duplicate_rank = 1 "
            f"ORDER BY {order} LIMIT ? OFFSET ?",
            (-1 if limit is None else limit, offset),
        ).fetchall()
        selected = self._rows_for_values(
            "tweet",
            "row_key",
            [row["row_key"] for row in keys],
            columns=["row_key", *tweet_columns],
        )
        by_key = {row["row_key"]: row for row in selected}
        sorted_rows = [by_key[row["row_key"]] for row in keys if row["row_key"] in by_key]
        return self._hydrate_exported_rows(sorted_rows, include_raw_json, False)

    def get_tag_counts(self, query: str = "", limit: int = 50) -> list[dict[str, Any]]:
        sql = """
        SELECT MIN(json_each.value) as tag, count(*) as count
        FROM archive,
             json_each(
               CASE WHEN json_valid(archive.raw_json)
                    THEN archive.raw_json ELSE '{"tags":[]}' END,
               '$.tags'
             )
        WHERE record_type = 'media_tag'
          AND (enrichment_state = 'done' OR enrichment_state IS NULL)
          AND json_each.type = 'text'
        """
        params = []
        if query:
            sql += " AND LOWER(json_each.value) LIKE ?"
            params.append(f"%{query.lower()}%")

        sql += " GROUP BY LOWER(json_each.value) ORDER BY count DESC LIMIT ?"
        params.append(limit)

        rows = self.conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]

    def counts(self) -> dict[str, int]:
        tweet_rows = self._count("record_type = 'tweet'")
        return {
            "raw_captures": self._count("record_type = 'raw_capture'"),
            "tweets": tweet_rows,
            "collections": tweet_rows,
            "tweet_objects": self._count("record_type = 'tweet_object'"),
            "tweet_relations": self._count("record_type = 'tweet_relation'"),
            "media": self._count("record_type = 'media'"),
            "urls": self._count("record_type = 'url'"),
            "url_refs": self._count("record_type = 'url_ref'"),
            "articles": self._count("record_type = 'article'"),
            "import_manifests": self._count("record_type = 'import_manifest'"),
            "sync_state": self._count("record_type = 'sync_state'"),
        }

    def archive_stats(self, max_linked_depth: int = 1) -> ArchiveStats:
        counts = self.counts()
        tweet_rows = [
            dict(row)
            for row in self.conn.execute(
                """
                SELECT tweet_id, collection_type, created_at
                FROM archive INDEXED BY idx_archive_record_page
                WHERE record_type = 'tweet'
                """
            ).fetchall()
        ]
        sync_rows = self._query(
            expr="record_type = 'sync_state'",
            cols=[
                "collection_type",
                "updated_at",
                "last_head_tweet_id",
                "backfill_cursor",
                "backfill_incomplete",
            ],
        )
        enrichment_counts = {
            row[0]: int(row[1])
            for row in self.conn.execute(
                """
                SELECT enrichment_state, count(*)
                FROM archive INDEXED BY idx_archive_enrichment_due
                WHERE record_type = 'tweet_object'
                GROUP BY enrichment_state
                """
            ).fetchall()
            if isinstance(row[0], str)
        }
        scheduler_rows = [
            dict(row)
            for row in self.conn.execute(
                """
                SELECT deleted_at, enrichment_state, enrichment_reason,
                       enrichment_retry_eligible, enrichment_next_retry_at
                FROM archive INDEXED BY idx_archive_enrichment_due
                WHERE record_type = 'tweet_object'
                  AND enrichment_state IN ('transient_failure', 'terminal_unavailable')
                """
            ).fetchall()
        ]
        url_ref_rows = [
            dict(row)
            for row in self.conn.execute(
                """
                SELECT tweet_id, canonical_url, expanded_url, url
                FROM archive INDEXED BY idx_archive_record_page
                WHERE record_type = 'url_ref'
                """
            ).fetchall()
        ]

        oldest_created_dt: datetime | None = None
        newest_created_dt: datetime | None = None
        oldest_created_at: str | None = None
        newest_created_at: str | None = None
        unique_post_ids: set[str] = set()
        expanded_thread_targets = {
            str(row[0])
            for row in self.conn.execute(
                """
                SELECT cursor_in
                FROM archive INDEXED BY idx_archive_capture_target
                WHERE record_type = 'raw_capture'
                  AND operation = 'ThreadExpandDetail'
                  AND cursor_in IS NOT NULL AND cursor_in != ''
                """
            ).fetchall()
        }
        collection_stats = {
            collection: ArchiveCollectionStats(collection_type=collection)
            for collection in SEARCH_COLLECTION_ORDER
        }

        def update_created_bounds(
            raw: str | None,
            *,
            collection: ArchiveCollectionStats | None = None,
        ) -> None:
            nonlocal oldest_created_dt, newest_created_dt, oldest_created_at, newest_created_at

            created_at = _parse_created_at(raw)
            if created_at is None:
                return
            if oldest_created_dt is None or created_at < oldest_created_dt:
                oldest_created_dt = created_at
                oldest_created_at = raw
            if newest_created_dt is None or created_at > newest_created_dt:
                newest_created_dt = created_at
                newest_created_at = raw
            if collection is None:
                return

            collection_oldest_dt = _parse_created_at(collection.oldest_created_at)
            if collection_oldest_dt is None or created_at < collection_oldest_dt:
                collection.oldest_created_at = raw
            collection_newest_dt = _parse_created_at(collection.newest_created_at)
            if collection_newest_dt is None or created_at > collection_newest_dt:
                collection.newest_created_at = raw

        for row in tweet_rows:
            tweet_id = row.get("tweet_id")
            if isinstance(tweet_id, str) and tweet_id:
                unique_post_ids.add(tweet_id)
            collection_type = row.get("collection_type")
            collection = None
            if isinstance(collection_type, str) and collection_type:
                collection = collection_stats.setdefault(
                    collection_type,
                    ArchiveCollectionStats(collection_type=collection_type),
                )
                collection.post_count += 1
            update_created_bounds(row.get("created_at"), collection=collection)

        pending_enrichment_count = enrichment_counts.get("pending", 0)
        transient_enrichment_failure_count = enrichment_counts.get("transient_failure", 0)
        transient_enrichment_due_count = 0
        transient_enrichment_delayed_count = 0
        terminal_enrichment_count = enrichment_counts.get("terminal_unavailable", 0)
        resurrected_enrichment_count = enrichment_counts.get("resurrected", 0)
        done_enrichment_count = enrichment_counts.get("done", 0)
        retryable_unavailable_count = 0
        permanent_unavailable_count = 0
        due_resurrection_count = 0
        stats_now = utc_now()
        for row in scheduler_rows:
            enrichment_state = row.get("enrichment_state")
            if enrichment_state == "transient_failure":
                next_retry_at = row.get("enrichment_next_retry_at")
                if not next_retry_at or next_retry_at <= stats_now:
                    transient_enrichment_due_count += 1
                else:
                    transient_enrichment_delayed_count += 1
            elif enrichment_state == "terminal_unavailable":
                reason = row.get("enrichment_reason")
                deleted_at = row.get("deleted_at")
                retry_eligible = self._parse_bool(row.get("enrichment_retry_eligible"))
                permanently_unavailable = (
                    bool(deleted_at)
                    or not retry_eligible
                    or reason
                    in {
                        "archive_deleted",
                        "deleted_by_author",
                    }
                )
                if permanently_unavailable:
                    permanent_unavailable_count += 1
                else:
                    retryable_unavailable_count += 1
                    next_retry_at = row.get("enrichment_next_retry_at")
                    if not next_retry_at or next_retry_at <= stats_now:
                        due_resurrection_count += 1
        preview_article_count = int(
            self.conn.execute(
                """
                SELECT count(*)
                FROM archive INDEXED BY idx_archive_record_page
                WHERE record_type = 'article'
                  AND (status IS NULL OR status != 'body_present')
                """
            ).fetchone()[0]
            or 0
        )
        missing_tweet_object_count = int(
            self.conn.execute(
                """
                SELECT count(DISTINCT tweet.tweet_id)
                FROM archive tweet INDEXED BY idx_archive_record_page
                WHERE tweet.record_type = 'tweet'
                  AND tweet.tweet_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM archive object INDEXED BY idx_archive_tweet_id
                      WHERE object.tweet_id = tweet.tweet_id
                        AND object.record_type = 'tweet_object'
                  )
                """
            ).fetchone()[0]
            or 0
        )

        latest_capture_row = self.conn.execute(
            """
            SELECT max(captured_at)
            FROM archive INDEXED BY idx_archive_capture_target
            WHERE record_type = 'raw_capture'
            """
        ).fetchone()
        latest_capture_at = latest_capture_row[0] if latest_capture_row else None

        latest_sync_at: str | None = None
        for row in sync_rows:
            collection_type = row.get("collection_type")
            if not isinstance(collection_type, str) or not collection_type:
                continue
            collection = collection_stats.setdefault(
                collection_type,
                ArchiveCollectionStats(collection_type=collection_type),
            )
            updated_at = row.get("updated_at")
            if (
                isinstance(updated_at, str)
                and updated_at
                and (collection.last_synced_at is None or updated_at > collection.last_synced_at)
            ):
                collection.last_synced_at = updated_at
                backfill_cursor = row.get("backfill_cursor")
                collection.backfill_cursor = (
                    backfill_cursor
                    if isinstance(backfill_cursor, str) and backfill_cursor
                    else None
                )
                collection.backfill_incomplete = self._parse_bool(row.get("backfill_incomplete"))
            if (
                isinstance(updated_at, str)
                and updated_at
                and (latest_sync_at is None or updated_at > latest_sync_at)
            ):
                latest_sync_at = updated_at

        ordered_collections = [
            collection_stats[name] for name in SEARCH_COLLECTION_ORDER if name in collection_stats
        ]
        ordered_collections.extend(
            collection_stats[name]
            for name in sorted(collection_stats)
            if name not in SEARCH_COLLECTION_ORDER
        )
        pending_thread_membership_count = len(unique_post_ids - expanded_thread_targets)
        pending_linked_status_targets: set[str] = set()

        url_edges: dict[str, list[str]] = {}
        for row in url_ref_rows:
            target_id = None
            for field_name in ("canonical_url", "expanded_url", "url"):
                candidate = row.get(field_name)
                if isinstance(candidate, str):
                    target_id = extract_status_id_from_url(candidate)
                    if target_id:
                        break
            src = row.get("tweet_id")
            if src and target_id:
                url_edges.setdefault(src, []).append(target_id)

        depths = {tid: 0 for tid in unique_post_ids if tid}
        discovery_kinds: dict[str, str] = {}
        quote_reachable_targets: set[str] = set()
        for depth in range(1, max_linked_depth + 1):
            frontier = {
                tweet_id for tweet_id, known_depth in depths.items() if known_depth == depth - 1
            }
            quote_edges: dict[str, list[str]] = {}
            for row in self.list_quote_relation_rows(frontier):
                source_id = row.get("tweet_id")
                target_id = row.get("target_tweet_id")
                if isinstance(source_id, str) and isinstance(target_id, str) and target_id:
                    quote_edges.setdefault(source_id, []).append(target_id)

            for source_id in sorted(frontier):
                candidates = [
                    *(("quote", target_id) for target_id in quote_edges.get(source_id, [])),
                    *(("linked", target_id) for target_id in url_edges.get(source_id, [])),
                ]
                for kind, target_id in candidates:
                    if not target_id or target_id == source_id:
                        continue
                    if kind == "quote":
                        quote_reachable_targets.add(target_id)
                    if target_id in depths:
                        continue
                    depths[target_id] = depth
                    discovery_kinds[target_id] = kind

        linked_candidates = {
            target_id
            for target_id, depth in depths.items()
            if depth > 0
            and target_id not in quote_reachable_targets
            and discovery_kinds[target_id] == "linked"
        }
        known_linked_targets = set(unique_post_ids & linked_candidates)
        linked_candidate_ids = sorted(linked_candidates - known_linked_targets)
        for start in range(0, len(linked_candidate_ids), 500):
            chunk = linked_candidate_ids[start : start + 500]
            placeholders = ", ".join("?" for _tweet_id in chunk)
            known_linked_targets.update(
                str(row[0])
                for row in self.conn.execute(
                    f"""
                    SELECT DISTINCT tweet_id
                    FROM archive INDEXED BY idx_archive_tweet_id
                    WHERE tweet_id IN ({placeholders})
                      AND record_type = 'tweet_object'
                    """,
                    chunk,
                ).fetchall()
            )

        for target_id, depth in depths.items():
            if depth == 0 or target_id in expanded_thread_targets:
                continue
            if (
                target_id not in quote_reachable_targets
                and discovery_kinds[target_id] == "linked"
                and target_id in known_linked_targets
            ):
                continue
            pending_linked_status_targets.add(target_id)
        return ArchiveStats(
            owner_user_id=self.get_archive_owner_id(),
            unique_post_count=len(unique_post_ids),
            collection_membership_count=counts["tweets"],
            article_count=counts["articles"],
            raw_capture_count=counts["raw_captures"],
            media_count=counts["media"],
            url_count=counts["urls"],
            oldest_created_at=oldest_created_at,
            newest_created_at=newest_created_at,
            latest_capture_at=latest_capture_at,
            latest_sync_at=latest_sync_at,
            version_count=self.version_count(),
            collections=ordered_collections,
            pending_enrichment_count=pending_enrichment_count,
            transient_enrichment_failure_count=transient_enrichment_failure_count,
            transient_enrichment_due_count=transient_enrichment_due_count,
            transient_enrichment_delayed_count=transient_enrichment_delayed_count,
            terminal_enrichment_count=terminal_enrichment_count,
            resurrected_enrichment_count=resurrected_enrichment_count,
            done_enrichment_count=done_enrichment_count,
            retryable_unavailable_count=retryable_unavailable_count,
            permanent_unavailable_count=permanent_unavailable_count,
            due_resurrection_count=due_resurrection_count,
            preview_article_count=preview_article_count,
            missing_tweet_object_count=missing_tweet_object_count,
            expanded_thread_target_count=len(expanded_thread_targets),
            pending_thread_membership_count=pending_thread_membership_count,
            pending_thread_linked_status_count=len(pending_linked_status_targets),
        )

    def list_archive_import_media_paths(self) -> list[str]:
        rows = self._query(
            expr="record_type = 'media' AND provenance_source = 'x_archive'",
            cols=["local_path", "thumbnail_local_path"],
        )
        relative_paths = {
            path
            for row in rows
            for path in (row.get("local_path"), row.get("thumbnail_local_path"))
            if isinstance(path, str) and path
        }
        return sorted(relative_paths)

    def clear_archive_import_data(self) -> dict[str, int]:
        deletions: dict[str, int] = {}
        filters = {
            "raw_captures": "record_type = 'raw_capture' AND source = 'x_archive'",
            "tweets": "record_type = 'tweet' AND source = 'x_archive'",
            "tweet_objects": "record_type = 'tweet_object' AND source = 'x_archive'",
            "tweet_relations": "record_type = 'tweet_relation' AND source = 'x_archive'",
            "media": "record_type = 'media' AND provenance_source = 'x_archive'",
            "urls": "record_type = 'url' AND source = 'x_archive'",
            "url_refs": "record_type = 'url_ref' AND source = 'x_archive'",
            "articles": "record_type = 'article' AND source = 'x_archive'",
            "import_manifests": "record_type = 'import_manifest'",
        }
        for key, expr in filters.items():
            count = self._count(expr)
            if count:
                self._delete(expr)
            deletions[key] = count
        return deletions

    def _flush_rehydrate_buffer(self, buffer: _PageBuffer) -> int:
        if not buffer.records:
            return 0
        secondary_records = sum(
            1
            for record in buffer.records.values()
            if record["record_type"] in SECONDARY_RECORD_TYPES
        )
        self._merge_records(list(buffer.records.values()))
        buffer.records.clear()
        buffer.existing_rows.clear()
        return secondary_records

    def rehydrate_from_raw_json(
        self, *, progress: Callable[[int], None] | None = None
    ) -> RehydrateResult:
        """Rebuild normalized tweet fields and secondary rows from stored raw_json."""
        rows = self._query(expr="record_type = 'tweet'")
        if not rows:
            return RehydrateResult()
        buffer = _PageBuffer()
        batch_size = 500
        result = RehydrateResult()
        for row in rows:
            raw = json.loads(row["raw_json"])
            legacy = raw.get("legacy") or {}
            author_id, username, display_name = extract_author_fields(raw)
            canonical_text = extract_canonical_text(raw)
            updated_row = dict(row)
            changed = False
            desired_updates = {
                "text": self._coalesce_value(canonical_text, updated_row.get("text")),
                "author_id": self._coalesce_value(author_id, updated_row.get("author_id")),
                "author_username": self._coalesce_value(
                    username, updated_row.get("author_username")
                ),
                "author_display_name": self._coalesce_value(
                    display_name,
                    updated_row.get("author_display_name"),
                ),
                "created_at": self._coalesce_value(
                    legacy.get("created_at"), updated_row.get("created_at")
                ),
                "conversation_id": self._coalesce_value(
                    legacy.get("conversation_id_str"),
                    updated_row.get("conversation_id"),
                ),
                "lang": self._coalesce_value(legacy.get("lang"), updated_row.get("lang")),
                "source": updated_row.get("source") or LIVE_SOURCE,
            }
            desired_updates["note_tweet_text"] = self._coalesce_value(
                extract_note_tweet_text(raw),
                updated_row.get("note_tweet_text"),
            )
            for key, value in desired_updates.items():
                if updated_row.get(key) != value:
                    updated_row[key] = value
                    changed = True
            if changed:
                buffer.records[updated_row["row_key"]] = updated_row
                result.tweets_updated += 1
            self._buffer_secondary_graph(
                extract_secondary_objects(raw),
                source=self._normalized_source(row.get("source")),
                cursor=buffer,
            )
            if progress:
                progress(1)
            if len(buffer.records) >= batch_size:
                result.secondary_records += self._flush_rehydrate_buffer(buffer)
        detail_rows = self._query(
            expr="record_type = 'raw_capture' "
            "AND (operation = 'TweetDetail' OR operation = 'ThreadExpandDetail')"
        )
        detail_rows.sort(key=lambda row: (row.get("captured_at") or "", row.get("cursor_in") or ""))
        for row in detail_rows:
            raw_json = row.get("raw_json")
            if not isinstance(raw_json, str) or not raw_json:
                continue
            detail_payload = json.loads(raw_json)
            detail_tweets = parse_tweet_detail_tweets(detail_payload)
            if not detail_tweets:
                continue
            self._buffer_secondary_graph(
                extract_thread_objects([tweet.raw_json for tweet in detail_tweets]),
                source=self._normalized_source(row.get("source")),
                cursor=buffer,
            )
            if len(buffer.records) >= batch_size:
                result.secondary_records += self._flush_rehydrate_buffer(buffer)
        result.secondary_records += self._flush_rehydrate_buffer(buffer)
        return result

    def rehydrate_authors(self, *, progress: Callable[[int], None] | None = None) -> int:
        return self.rehydrate_from_raw_json(progress=progress).tweets_updated

    def ensure_scalar_indexes(self) -> None:
        pass

    def ensure_fts_index(self) -> None:
        pass

    def _dedupe_search_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen_tweet_ids: set[str] = set()
        for row in rows:
            tweet_id = row.get("tweet_id")
            if not isinstance(tweet_id, str) or not tweet_id or tweet_id in seen_tweet_ids:
                continue
            seen_tweet_ids.add(tweet_id)
            deduped.append(row)
        return deduped

    def _collect_search_context(
        self, tweet_ids: list[str]
    ) -> tuple[dict[str, list[str]], dict[str, dict[str, Any]]]:
        tweet_rows = self._rows_for_values("tweet", "tweet_id", tweet_ids)
        tweet_object_rows = self._rows_for_values("tweet_object", "tweet_id", tweet_ids)
        collections_by_tweet_id: dict[str, set[str]] = {}
        metadata_by_tweet_id: dict[str, dict[str, Any]] = {}

        def merge_metadata(row: dict[str, Any]) -> None:
            tweet_id = row.get("tweet_id")
            if not isinstance(tweet_id, str) or not tweet_id:
                return
            metadata = metadata_by_tweet_id.setdefault(tweet_id, {})
            for metadata_field in (
                "author_id",
                "author_username",
                "author_display_name",
                "created_at",
                "text",
                "note_tweet_text",
            ):
                if metadata.get(metadata_field) not in (None, ""):
                    continue
                value = row.get(metadata_field)
                if value not in (None, ""):
                    metadata[metadata_field] = value

        for row in tweet_object_rows:
            merge_metadata(row)
        for row in tweet_rows:
            tweet_id = row.get("tweet_id")
            collection_type = row.get("collection_type")
            if isinstance(tweet_id, str) and tweet_id and isinstance(collection_type, str):
                collections_by_tweet_id.setdefault(tweet_id, set()).add(collection_type)
            merge_metadata(row)

        ordered_collections = {
            tweet_id: self._ordered_search_collections(values)
            for tweet_id, values in collections_by_tweet_id.items()
        }
        return ordered_collections, metadata_by_tweet_id

    def _ordered_search_collections(self, collections: set[str]) -> list[str]:
        order = {k: i for i, k in enumerate(SEARCH_COLLECTION_ORDER)}
        return sorted(list(collections), key=lambda c: (order.get(c, 99), c))

    def _apply_sort(self, search_results: list[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
        def sort_index_value(row: dict[str, Any]) -> int:
            raw = row.get("sort_index")
            if not raw:
                return 0
            try:
                return int(raw)
            except (TypeError, ValueError):
                return 0

        def oldest_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
            created_at = _parse_created_at(row.get("created_at"))
            if created_at is not None:
                return (0, created_at.timestamp(), sort_index_value(row), row.get("tweet_id") or "")
            return (1, datetime.max.timestamp(), sort_index_value(row), row.get("tweet_id") or "")

        def newest_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
            created_at = _parse_created_at(row.get("created_at"))
            if created_at is not None:
                return (
                    0,
                    -created_at.timestamp(),
                    -sort_index_value(row),
                    row.get("tweet_id") or "",
                )
            return (1, 0.0, -sort_index_value(row), row.get("tweet_id") or "")

        sort_key_fn = oldest_sort_key if sort == "oldest" else newest_sort_key
        return sorted(search_results, key=sort_key_fn)

    def _search_collection_expr(self, collections: set[str] | None) -> str:
        if not collections:
            return ""
        if len(collections) == 1:
            return f"collection_type = {_expr_quote(next(iter(collections)))}"
        formatted = ", ".join(_expr_quote(c) for c in collections)
        return f"collection_type IN ({formatted})"

    def _prepare_fts_query(self, query: str) -> str:
        import re

        raw_parts = re.findall(r'"[^"]*"|\S+', query.strip())
        if not raw_parts:
            return ""

        operators = {"AND", "OR", "NOT"}
        prepared: list[str] = []
        for index, part in enumerate(raw_parts):
            upper = part.upper()
            previous_is_term = index > 0 and raw_parts[index - 1].upper() not in operators
            next_is_term = (
                index + 1 < len(raw_parts) and raw_parts[index + 1].upper() not in operators
            )
            if upper in operators and previous_is_term and next_is_term:
                prepared.append(upper)
                continue

            if len(part) >= 2 and part.startswith('"') and part.endswith('"'):
                inner = part[1:-1].replace('"', '""')
                if inner:
                    prepared.append(f'"{inner}"')
                continue
            if re.fullmatch(r"[\w]+(?:\*)?", part) and upper not in operators | {"NEAR"}:
                prepared.append(part)
                continue
            safe_part = part.replace('"', '""')
            if safe_part:
                prepared.append(f'"{safe_part}"')
        return " ".join(prepared)

    def _search_score(self, row: dict[str, Any]) -> float:
        rank = row.get("rank")
        if rank is not None:
            return float(-rank)
        score = row.get("match_score")
        if score is not None:
            return float(score)
        return float("-inf")

    def _search_post_rows_fts(
        self,
        query: str,
        *,
        limit: int,
        collections: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        where_expr = _and_expr("record_type = 'tweet'", self._search_collection_expr(collections))
        return self._query(
            expr=where_expr,
            limit=limit,
            is_fts=True,
            query=query,
        )

    def _search_article_rows_fts(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        import re

        parts = re.findall(r'"[^"]*"|\S+', query)
        groups: list[tuple[list[str], list[str]]] = [([], [])]
        negate_next = False
        for part in parts:
            operator = part.upper()
            if operator == "OR":
                groups.append(([], []))
                negate_next = False
            elif operator == "AND":
                negate_next = False
            elif operator == "NOT":
                negate_next = True
            else:
                target = groups[-1][1] if negate_next else groups[-1][0]
                target.append(part)
                negate_next = False
        if not any(positive or negative for positive, negative in groups):
            return []

        def term_parts(term: str) -> list[str]:
            if term.startswith('"') and term.endswith('"'):
                term = term[1:-1]
            return re.findall(r"\w+", term.casefold())

        def matches_term(term: str, words: list[str], normalized: str) -> bool:
            pieces = term_parts(term)
            if not pieces:
                return False
            if term.endswith("*") and len(pieces) == 1:
                return any(word.startswith(pieces[0]) for word in words)
            if len(pieces) > 1 or (term.startswith('"') and term.endswith('"')):
                return " ".join(pieces) in normalized
            return pieces[0] in words

        rows = self._query(expr="record_type = 'article'")
        matches: list[dict[str, Any]] = []
        for row in rows:
            haystack = " ".join(
                part
                for part in (
                    row.get("title"),
                    row.get("summary_text"),
                    row.get("content_text"),
                )
                if isinstance(part, str) and part
            )
            folded = haystack.casefold()
            words = re.findall(r"\w+", folded)
            normalized = " ".join(words)
            matching_groups = [
                positive
                for positive, negative in groups
                if all(matches_term(term, words, normalized) for term in positive)
                and not any(matches_term(term, words, normalized) for term in negative)
            ]
            if not matching_groups:
                continue
            matched = dict(row)
            matched["match_score"] = float(
                max(
                    sum(normalized.count(" ".join(term_parts(term))) for term in positive)
                    for positive in matching_groups
                )
            )
            matches.append(matched)
        matches.sort(
            key=lambda row: (
                row.get("match_score") if row.get("match_score") is not None else float("-inf"),
                len(row.get("content_text") or row.get("summary_text") or row.get("title") or ""),
            ),
            reverse=True,
        )
        return matches[:limit]

    def _project_post_search_results(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        tweet_ids = [
            tweet_id
            for row in rows
            if isinstance((tweet_id := row.get("tweet_id")), str) and tweet_id
        ]
        collections_by_tweet_id, metadata_by_tweet_id = self._collect_search_context(tweet_ids)
        results: list[dict[str, Any]] = []
        for row in rows:
            tweet_id = row.get("tweet_id")
            if not isinstance(tweet_id, str) or not tweet_id:
                continue
            metadata = metadata_by_tweet_id.get(tweet_id, {})
            collections = collections_by_tweet_id.get(tweet_id)
            if not collections:
                fallback = row.get("collection_type")
                collections = [fallback] if isinstance(fallback, str) and fallback else []
            results.append(
                {
                    "tweet_id": tweet_id,
                    "type": SEARCH_KIND_POST,
                    "collections": collections,
                    "author_id": row.get("author_id") or metadata.get("author_id"),
                    "author_username": row.get("author_username")
                    or metadata.get("author_username"),
                    "created_at": row.get("created_at") or metadata.get("created_at"),
                    "text": self._coalesce_value(
                        row.get("note_tweet_text"),
                        row.get("text"),
                        metadata.get("note_tweet_text"),
                        metadata.get("text"),
                    ),
                    "match_score": self._search_score(row),
                }
            )
        return results

    def _compose_article_search_text(
        self,
        row: dict[str, Any],
        metadata: dict[str, Any],
    ) -> str | None:
        summary_or_body = self._coalesce_value(row.get("summary_text"), row.get("content_text"))
        title = row.get("title")
        if title and summary_or_body:
            return f"{title}\\n\\n{summary_or_body}"
        return self._coalesce_value(
            title,
            summary_or_body,
            metadata.get("note_tweet_text"),
            metadata.get("text"),
        )

    def _project_article_search_results(
        self,
        rows: list[dict[str, Any]],
        *,
        collections: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        tweet_ids = [
            tweet_id
            for row in rows
            if isinstance((tweet_id := row.get("tweet_id")), str) and tweet_id
        ]
        collections_by_tweet_id, metadata_by_tweet_id = self._collect_search_context(tweet_ids)
        results: list[dict[str, Any]] = []
        for row in rows:
            tweet_id = row.get("tweet_id")
            if not isinstance(tweet_id, str) or not tweet_id:
                continue
            result_collections = collections_by_tweet_id.get(tweet_id, [])
            if collections is not None and not set(result_collections).intersection(collections):
                continue
            metadata = metadata_by_tweet_id.get(tweet_id, {})
            results.append(
                {
                    "tweet_id": tweet_id,
                    "type": SEARCH_KIND_ARTICLE,
                    "collections": result_collections,
                    "author_id": metadata.get("author_id"),
                    "author_username": metadata.get("author_username"),
                    "created_at": metadata.get("created_at"),
                    "text": self._compose_article_search_text(row, metadata),
                    "match_score": self._search_score(row),
                }
            )
        return results

    def search_authors(self, query: str, limit: int = 10) -> list[dict[str, str]]:
        """Search for authors by username or display name."""
        if not query:
            return []

        query = query.lstrip("@")
        if not query:
            return []

        sql = """
            SELECT MIN(author_id) AS author_id,
                   author_username,
                   MIN(author_display_name) AS author_display_name
            FROM archive
            WHERE record_type = 'tweet' 
              AND author_username IS NOT NULL
              AND (LOWER(author_username) LIKE LOWER(?) OR LOWER(author_display_name) LIKE LOWER(?))
            GROUP BY LOWER(author_username)
            ORDER BY author_username ASC
            LIMIT ?
        """
        pattern = f"%{query}%"
        try:
            rows = self.conn.execute(sql, (pattern, pattern, limit)).fetchall()
            return [
                {
                    "id": r["author_id"],
                    "username": r["author_username"],
                    "display_name": r["author_display_name"],
                }
                for r in rows
            ]
        except Exception as e:
            if "no such column" in str(e).lower() or "author_display_name" in str(e).lower():
                fallback_sql = """
                    SELECT MIN(author_id) AS author_id, author_username
                    FROM archive
                    WHERE record_type = 'tweet' 
                      AND author_username IS NOT NULL
                      AND LOWER(author_username) LIKE LOWER(?)
                    GROUP BY LOWER(author_username)
                    ORDER BY author_username ASC
                    LIMIT ?
                """
                rows = self.conn.execute(fallback_sql, (pattern, limit)).fetchall()
                return [
                    {
                        "id": r["author_id"],
                        "username": r["author_username"],
                        "display_name": r["author_username"],
                    }
                    for r in rows
                ]
            raise

    def search_fts(
        self,
        query: str,
        *,
        limit: int = 20,
        types: set[str] | None = None,
        collections: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Full-text search over exposed search result types."""
        self.ensure_fts_index()
        query = self._prepare_fts_query(query)
        if not query:
            return []

        fetch_limit = max(limit, 1)
        max_fetch_limit = max(limit * 8, 50)
        results: list[dict[str, Any]] = []
        while True:
            post_raw_rows: list[dict[str, Any]] = []
            article_raw_rows: list[dict[str, Any]] = []
            post_rows: list[dict[str, Any]] = []
            article_rows: list[dict[str, Any]] = []
            if types is None or SEARCH_KIND_POST in types:
                post_raw_rows = self._search_post_rows_fts(
                    query,
                    limit=fetch_limit,
                    collections=collections,
                )
                post_rows = self._dedupe_search_rows(post_raw_rows)
            if types is None or SEARCH_KIND_ARTICLE in types:
                article_raw_rows = self._search_article_rows_fts(query, limit=fetch_limit)
                article_rows = self._dedupe_search_rows(article_raw_rows)
            results = self._project_post_search_results(post_rows)
            results.extend(
                self._project_article_search_results(article_rows, collections=collections)
            )
            results.sort(
                key=lambda row: (
                    row["match_score"] if row.get("match_score") is not None else float("-inf")
                ),
                reverse=True,
            )
            exhausted = True
            if types is None or SEARCH_KIND_POST in types:
                exhausted = exhausted and len(post_raw_rows) < fetch_limit
            if types is None or SEARCH_KIND_ARTICLE in types:
                exhausted = exhausted and len(article_raw_rows) < fetch_limit
            if len(results) >= limit or fetch_limit >= max_fetch_limit or exhausted:
                return results[:limit]
            fetch_limit = min(fetch_limit * 2, max_fetch_limit)

    def search_tweet_ids(
        self,
        query_groups: tuple[tuple[dict[str, Any], ...], ...],
        *,
        collections: set[str] | None,
        sort: str,
        limit: int,
        offset: int,
        random_seed: int | None = None,
        include_total: bool = False,
        after: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], int | None]:
        """Evaluate the Web tweet query in SQLite and return one lightweight page.

        Text clauses are independent FTS relations. The supplied filter expressions
        are produced by the canonical query compiler, never by HTTP input directly.
        """
        self.ensure_fts_index()
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if offset < 0:
            raise ValueError("offset must be non-negative")

        # Preserve the index-ordered ordinary-feed path. It already deduplicates
        # logical tweets before LIMIT/OFFSET and avoids building a search CTE when
        # there is no query to evaluate.
        if not query_groups and sort != "random" and (not collections or len(collections) == 1):
            collection = next(iter(collections)) if collections else "all"
            filter_expr = "record_type = 'tweet'"
            if collection != "all":
                filter_expr += f" AND collection_type = {_expr_quote(collection)}"
            total = self.count_export_rows(collection) if include_total else None
            if sort in {"liked_latest", "liked_earliest"}:
                rows = self.query_like_order_ids(
                    filter_expr,
                    sort=sort,
                    limit=limit + 1,
                    offset=offset,
                    after_position=after["like_position"] if after else None,
                )
                return rows, total
            rows = self.get_paginated_tweet_rows(
                collection,
                limit=limit + 1,
                offset=offset,
                sort=sort,
                after=after,
            )
            return rows, total

        collection_expr = self._search_collection_expr(collections)
        ctes: list[str] = []
        params: list[Any] = []
        author_groups = [
            group
            for group in query_groups
            if group and all(clause.get("index_driver") for clause in group)
        ]
        narrow_ids = None
        if author_groups and any(
            clause["kind"] == "text" for group in query_groups for clause in group
        ):
            author_where = ["archive.record_type = 'tweet'"]
            if collection_expr:
                author_where.append(collection_expr)
            author_where.extend(
                "(" + " OR ".join(f"({clause['expression']})" for clause in group) + ")"
                for group in author_groups
            )
            # A bounded selectivity probe chooses a per-row FTS lookup only for
            # small author sets. Larger sets use the complete FTS-led executor;
            # this threshold never caps the returned search population.
            probe = self.conn.execute(
                "SELECT DISTINCT archive.tweet_id FROM archive "
                f"WHERE {' AND '.join(author_where)} LIMIT 201"
            ).fetchall()
            if len(probe) <= 200:
                narrow_ids = [row[0] for row in probe]
                if not narrow_ids:
                    return [], 0 if include_total else None
                ctes.append(
                    "structured_candidates(tweet_id) AS MATERIALIZED "
                    "(SELECT value FROM json_each(?))"
                )
                params.append(json.dumps(narrow_ids))
        compiled_groups: list[list[dict[str, Any]]] = []
        text_index = 0
        for group in query_groups:
            compiled_group: list[dict[str, Any]] = []
            for clause in group:
                compiled = dict(clause)
                if compiled["kind"] == "text":
                    prepared = self._prepare_fts_query(str(compiled["value"]))
                    alias = f"text_match_{text_index}"
                    text_index += 1
                    compiled["alias"] = alias
                    where = "archive.record_type = 'tweet'"
                    if collection_expr:
                        where += f" AND {collection_expr}"
                    rank = "MIN(archive_fts.rank)" if sort == "relevance" else "0.0"
                    text_from = "archive_fts JOIN archive ON archive.rowid = archive_fts.rowid"
                    if narrow_ids is not None:
                        # Include every membership of the selected logical IDs:
                        # text and author predicates may match different rows.
                        text_from = (
                            "structured_candidates CROSS JOIN archive "
                            "INDEXED BY idx_archive_tweet_id "
                            "ON archive.tweet_id = structured_candidates.tweet_id "
                            "CROSS JOIN archive_fts ON archive_fts.rowid = archive.rowid"
                        )
                        if sort == "relevance":
                            # BM25 initialization per row is costly for common
                            # terms. Scan FTS once, intersect before aggregation,
                            # and rank only matching authors' membership rows.
                            text_from = (
                                "archive_fts CROSS JOIN archive "
                                "ON archive.rowid = archive_fts.rowid"
                            )
                            where += (
                                " AND archive.tweet_id IN "
                                "(SELECT tweet_id FROM structured_candidates)"
                            )
                    ctes.append(
                        f"{alias}(tweet_id, rank) AS MATERIALIZED ("
                        f"SELECT archive.tweet_id, {rank} "
                        f"FROM {text_from} "
                        f"WHERE archive_fts MATCH ? AND {where} "
                        "GROUP BY archive.tweet_id)"
                    )
                    params.append(prepared)
                compiled_group.append(compiled)
            compiled_groups.append(compiled_group)

        # Any all-positive-text AND group is a complete candidate driver. Using one
        # keeps ordinary text searches FTS-led while mixed OR and negative-only
        # queries correctly evaluate against the complete logical tweet population.
        driver_group = next(
            (
                group
                for group in compiled_groups
                if group
                and all(
                    clause["kind"] == "text" and not clause.get("negated", False)
                    for clause in group
                )
            ),
            None,
        )
        driver_alias = None
        if driver_group is not None and len(driver_group) == 1:
            # Reuse the already-deduplicated text relation directly, avoiding a
            # second copy of the same candidate IDs and a redundant self-join.
            driver_alias = driver_group[0]["alias"]
            from_sql = (
                f"{driver_alias} JOIN archive AS archive INDEXED BY idx_archive_tweet_id "
                f"ON archive.tweet_id = {driver_alias}.tweet_id"
            )
        elif driver_group is not None:
            union = " UNION ".join(
                f"SELECT tweet_id FROM {clause['alias']}" for clause in driver_group
            )
            ctes.append(f"candidate_ids(tweet_id) AS MATERIALIZED ({union})")
            from_sql = (
                "candidate_ids JOIN archive AS archive INDEXED BY idx_archive_tweet_id "
                "ON archive.tweet_id = candidate_ids.tweet_id"
            )
        else:
            from_sql = "archive AS archive"

        joins: list[str] = []
        group_conditions: list[str] = []
        group_scores: list[str] = []
        for group in compiled_groups:
            alternatives: list[str] = []
            positive_scores: list[str] = []
            for clause in group:
                if clause["kind"] == "text":
                    alias = str(clause["alias"])
                    if alias != driver_alias:
                        joins.append(f"LEFT JOIN {alias} ON {alias}.tweet_id = archive.tweet_id")
                    if clause.get("negated", False):
                        alternatives.append(f"{alias}.tweet_id IS NULL")
                    else:
                        alternatives.append(f"{alias}.tweet_id IS NOT NULL")
                        positive_scores.append(f"COALESCE(-{alias}.rank, 0.0)")
                else:
                    alternatives.append(f"({clause['expression']})")
            if alternatives:
                group_conditions.append(f"({' OR '.join(alternatives)})")
            if positive_scores:
                group_scores.append(
                    positive_scores[0]
                    if len(positive_scores) == 1
                    else f"MAX({', '.join(positive_scores)})"
                )

        where = ["archive.record_type = 'tweet'"]
        if collection_expr:
            where.append(collection_expr)
        where.extend(group_conditions)
        score_expr = " + ".join(group_scores) if group_scores else "0.0"
        ctes.append(
            "matches(tweet_id, match_score, created_at_ts, sort_index) AS NOT MATERIALIZED ("
            "SELECT archive.tweet_id, "
            f"MAX({score_expr}), MAX(archive.created_at_ts), "
            "MAX(CAST(archive.sort_index AS INTEGER)) "
            f"FROM {from_sql} {' '.join(dict.fromkeys(joins))} "
            f"WHERE {' AND '.join(where)} GROUP BY archive.tweet_id)"
        )
        with_sql = "WITH " + ", ".join(ctes)

        page_from = "matches"
        page_params = list(params)
        page_where = ""
        page_extra = ""
        if sort in {"liked_latest", "liked_earliest"}:
            position = "earliest_position" if sort == "liked_earliest" else "latest_position"
            page_from = (
                "matches JOIN temp.tweetnook_like_order AS like_order "
                "ON like_order.tweet_id = matches.tweet_id"
            )
            order_by = f"like_order.{position}, matches.tweet_id"
            page_extra = f", like_order.{position} AS like_position"
            if after:
                page_where = f"WHERE like_order.{position} > ?"
                page_params.append(after["like_position"])
        elif sort == "oldest":
            order_by = (
                "matches.created_at_ts IS NULL, matches.created_at_ts ASC, "
                "matches.sort_index ASC, matches.tweet_id ASC"
            )
        elif sort == "newest":
            order_by = (
                "matches.created_at_ts IS NULL, matches.created_at_ts DESC, "
                "matches.sort_index DESC, matches.tweet_id DESC"
            )
        elif sort == "random":
            seed = int(random_seed or 0)

            def stable_random_key(tweet_id: Any, supplied_seed: Any) -> int:
                digest = hashlib.blake2b(
                    f"{int(supplied_seed)}:{tweet_id}".encode(), digest_size=8
                ).digest()
                return int.from_bytes(digest, "big") & ((1 << 63) - 1)

            self.conn.create_function(
                "tweetnook_random_key", 2, stable_random_key, deterministic=True
            )
            order_by = "tweetnook_random_key(matches.tweet_id, ?), matches.tweet_id"
            page_params.append(seed)
        else:
            order_by = "matches.match_score DESC, matches.tweet_id DESC"

        page_sql = (
            f"{with_sql} SELECT matches.tweet_id, matches.match_score, "
            f"matches.created_at_ts, matches.sort_index{page_extra} "
            f"FROM {page_from} {page_where} ORDER BY {order_by} LIMIT ? OFFSET ?"
        )
        page_params.extend([limit + 1, offset])
        like_sort = sort in {"liked_latest", "liked_earliest"}
        with self._like_order_lock if like_sort else nullcontext():
            if like_sort:
                self._ensure_like_order_table()
            rows = [dict(row) for row in self.conn.execute(page_sql, page_params).fetchall()]

        total = None
        if include_total:
            signature = (
                self.conn.total_changes,
                self.conn.execute("PRAGMA data_version").fetchone()[0],
            )
            cache_key = (with_sql, tuple(params))
            cached = self._search_count_cache.get(cache_key)
            if not self.conn.in_transaction and cached is not None and cached[0] == signature:
                total = cached[1]
            else:
                total = int(
                    self.conn.execute(
                        f"{with_sql} SELECT COUNT(*) FROM matches", params
                    ).fetchone()[0]
                )
                if len(self._search_count_cache) >= 64 or self.conn.in_transaction:
                    self._search_count_cache.clear()
                if not self.conn.in_transaction:
                    self._search_count_cache[cache_key] = (signature, total)
        return rows, total

    def version_count(self) -> int:
        return 1

    def optimize(self, *, cleanup: bool = True) -> None:
        self.conn.execute("VACUUM")
        self.conn.execute("PRAGMA optimize=0x10002")
        with self.conn:
            self.conn.execute("INSERT INTO archive_fts(archive_fts) VALUES ('optimize')")


def open_archive_store(
    paths: XDGPaths, *, create: bool, config: AppConfig | None = None
) -> ArchiveStore | None:
    if not create and not paths.database_path.exists():
        return None
    migration_lock = None
    if paths.database_path.exists() and _database_requires_schema_migration(paths.database_path):
        from tweetnook.sync import ProcessLock

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
