"""Archive table layout, derived search tables, and upgrades from released schemas."""

from __future__ import annotations

import json
import math
import shutil
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from tweetnook.core.exceptions import ArchiveUpgradeError
from tweetnook.core.unavailability import PLACEHOLDER_TEXT_MARKER, PLACEHOLDER_TEXT_PREFIXES

from .common import SOURCE_RANK, numeric_media_id, parse_created_at
from .db import ConnectionMixin
from .record_types import RecordType


@dataclass(slots=True)
class MigrationReport:
    from_version: int
    to_version: int


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
    "last_head_tweet_id",
    "backfill_cursor",
    "backfill_incomplete",
    "updated_at",
    "key",
    "value",
]

# Fields a merged media row takes together, keyed by the first: one file's metadata, or a blank
# that the other source's copy can fill.
_MEDIA_MERGE_GROUPS = (
    ("local_path", "sha256", "byte_size", "content_type"),
    (
        "thumbnail_local_path",
        "thumbnail_sha256",
        "thumbnail_byte_size",
        "thumbnail_content_type",
    ),
    ("source",),
    ("article_id",),
    ("position",),
    ("media_type",),
    ("media_url",),
    ("thumbnail_url",),
    ("width",),
    ("height",),
    ("duration_millis",),
    ("variants_json",),
    ("added_at",),
    ("last_seen_at",),
    ("synced_at",),
)


def _fill_media_payload(winner: Any, donor: Any) -> Any:
    if isinstance(winner, dict) and isinstance(donor, dict):
        return {
            **donor,
            **{key: _fill_media_payload(value, donor.get(key)) for key, value in winner.items()},
        }
    return donor if winner in (None, "", [], {}) else winner


SCHEMA_VERSION = 14
# An upgrade rewrites every archive row in one transaction. The write-ahead log and SQLite's
# temporary files together peak near twice the database size.
MIGRATION_SPACE_FACTOR = 2
# From schema 13 an upgrade rewrites only derived tables and indexes, a few percent of
# the archive, and none of its rows.
DERIVED_MIGRATION_SPACE_FACTOR = 0.1
# Indexes whose definition changed after a release. CREATE INDEX IF NOT EXISTS keeps an old
# definition, so upgrades drop these before recreating the derived structures.
REDEFINED_INDEXES = ("idx_archive_search_attachment", "idx_archive_membership")
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


def _media_tag_description(raw: str) -> str:
    """The text description stored in a media_tag payload, or ''."""
    return (
        f"COALESCE(CASE WHEN json_valid({raw}) THEN CASE "
        f"WHEN json_type({raw}, '$.description') = 'text' "
        f"THEN json_extract({raw}, '$.description') END END, '')"
    )


def _json_each_texts(raw: str, path: str, field: str) -> str:
    """Lowercased field values of a JSON array, joined and wrapped in char(31)."""
    return (
        "(SELECT char(31) || group_concat(LOWER(COALESCE(CASE WHEN item.type = 'object' "
        f"THEN json_extract(item.value, '$.{field}') END, '')), char(31)) || char(31) "
        f"FROM json_each({raw}, '{path}') AS item)"
    )


# Values that structured search filters read from a membership row's tweet JSON.
# Triggers keep archive_tweet_facts in step with raw_json, so a filtered search
# reads one small row per membership instead of parsing every tweet's JSON.
TWEET_FACT_EXPRESSIONS = {
    "reply_to": "LOWER(json_extract({raw}, '$.legacy.in_reply_to_screen_name'))",
    "is_reply": (
        "NULLIF(json_extract({raw}, '$.legacy.in_reply_to_status_id_str'), '') IS NOT NULL"
    ),
    "is_quote": "COALESCE(json_extract({raw}, '$.legacy.is_quote_status'), 0) != 0",
    "is_retweet": (
        "NULLIF(json_extract({raw}, '$.legacy.retweeted_status_id_str'), '') IS NOT NULL"
    ),
    "is_verified": (
        "COALESCE(json_extract({raw}, '$.core.user_results.result.is_blue_verified'), "
        "json_extract({raw}, '$.core.user_results.result.legacy.verified'), 0) != 0"
    ),
    "retweet_count": "CAST(COALESCE(json_extract({raw}, '$.legacy.retweet_count'), 0) AS INTEGER)",
    "favorite_count": (
        "CAST(COALESCE(json_extract({raw}, '$.legacy.favorite_count'), 0) AS INTEGER)"
    ),
    "reply_count": "CAST(COALESCE(json_extract({raw}, '$.legacy.reply_count'), 0) AS INTEGER)",
    "quoted_status_id": "json_extract({raw}, '$.legacy.quoted_status_id_str')",
    "source": "LOWER(COALESCE(json_extract({raw}, '$.source'), ''))",
    "card_name": "json_extract({raw}, '$.card.name')",
    "mentions": _json_each_texts("{raw}", "$.legacy.entities.user_mentions", "screen_name"),
    "hashtags": _json_each_texts("{raw}", "$.legacy.entities.hashtags", "text"),
}


def _tweet_fact_values(raw: str) -> str:
    # NULL JSON evaluates like an absent key. Malformed JSON yields NULL facts
    # instead of failing the write that stored it.
    return ", ".join(
        f"CASE WHEN {raw} IS NULL OR json_valid({raw}) THEN {expression.format(raw=raw)} END"
        for expression in TWEET_FACT_EXPRESSIONS.values()
    )


def _saved_post_text(value: str) -> str:
    """SQL for non-empty text that is not X's placeholder, close to is_placeholder_text.

    This runs in triggers, which must work for direct SQL writes, so it can't call Python. It
    treats tab, newline, carriage return and no-break space as spaces, and curly apostrophes as
    straight ones; LIKE ignores ASCII case. It doesn't collapse repeated spaces, other Unicode
    spaces or non-ASCII case, so a placeholder written with those still counts as content.
    """
    spaced = f"COALESCE({value}, '')"
    for code in (9, 10, 13, 160):
        spaced = f"REPLACE({spaced}, char({code}), ' ')"
    text = f"TRIM(REPLACE({spaced}, '\u2019', ''''))"
    patterns = (f"%{PLACEHOLDER_TEXT_MARKER}%", *(f"{p}%" for p in PLACEHOLDER_TEXT_PREFIXES))
    placeholder = " OR ".join(
        f"{text} LIKE '{pattern.replace(chr(39), chr(39) * 2)}'" for pattern in patterns
    )
    return f"({text} != '' AND NOT ({placeholder}))"


def _tweet_content_values(row: str) -> str:
    """Content and note presence, evaluated on writes rather than while browsing."""
    raw = f"(CASE WHEN json_valid({row}.raw_json) THEN {row}.raw_json ELSE '{{}}' END)"
    tombstone = (
        f"COALESCE(json_extract({raw}, '$.__typename'), "
        f"json_extract({raw}, '$.__typename__'), '') IN ('TweetTombstone', 'TweetUnavailable') "
        f"OR COALESCE(json_extract({raw}, '$.__tombstone__'), 0) = 1"
    )
    full_text = f"json_extract({raw}, '$.legacy.full_text')"
    captured = (
        f"NOT ({tombstone}) AND ("
        f"{_saved_post_text(full_text)} OR "
        f"COALESCE(json_extract({raw}, '$.note_tweet.note_tweet_results.result.text'), '') "
        "!= '' OR "
        f"COALESCE(json_array_length({raw}, '$.legacy.extended_entities.media'), 0) > 0 OR "
        f"{_saved_post_text(f'{row}.text')})"
    )
    note = f"COALESCE(json_extract({raw}, '$.birdwatch_pivot.subtitle.text'), '') != ''"
    return f"{row}.tweet_id, ({captured}), ({note})"


def _gigabytes(size: float) -> str:
    # Rounds up, so following the advice in a message always leaves enough room.
    return f"{math.ceil(size / 100_000_000) / 10:.1f} GB"


def _failed_upgrade(error: Exception) -> ArchiveUpgradeError:
    return ArchiveUpgradeError(
        f"The archive upgrade did not finish, and your archive is unchanged: {error}. "
        "Check the server log, then restart TweetNook."
    )


class SchemaMixin(ConnectionMixin):
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

        # Releases shipped schema 5 (0.0.9 and 0.1.0), schema 8 (0.1.1 to 0.1.6),
        # schema 9 (0.1.7 and 0.1.8), schema 10 (0.2.0 to 1.1.0), schema 12 (1.2.0) and
        # schema 13 (1.3.0 and 1.3.1). Schema 11 was only on development builds before 1.2.0.
        if current_version == 13:
            self._run_migration(
                self._migrate_search_facts,
                current_version,
                space_factor=DERIVED_MIGRATION_SPACE_FACTOR,
            )
            return
        if current_version in {8, 9, 10, 11, 12}:
            self._run_migration(self._migrate_search_facts, current_version)
            return
        if current_version != 5:
            raise RuntimeError(
                f"Archive schema version {current_version} comes from a pre-release build "
                "and cannot be upgraded."
            )
        self._run_migration(self._migrate_search_index, current_version)

    def _run_migration(
        self,
        migrate: Callable[[int], None],
        current_version: int,
        *,
        space_factor: float = MIGRATION_SPACE_FACTOR,
    ) -> None:
        required = space_factor * self.db_path.stat().st_size
        available = shutil.disk_usage(self.db_path.parent).free
        if available < required:
            # Refuse here: an attempt fills the disk for everything else on it, then rolls back.
            raise ArchiveUpgradeError(
                f"Upgrading the archive needs about {_gigabytes(required)} of free disk space, "
                f"and {self.db_path.parent} has {_gigabytes(available)}. Your archive is "
                "unchanged. Free up space, then restart TweetNook."
            )
        self._announce_migration(current_version)
        try:
            migrate(current_version)
        except sqlite3.OperationalError as error:
            code = (getattr(error, "sqlite_errorcode", 0) or 0) & 0xFF
            # Another process holding the database says nothing about the upgrade itself.
            if code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                raise
            if code == sqlite3.SQLITE_FULL:
                raise ArchiveUpgradeError(
                    "The disk filled up while upgrading the archive, which needs about "
                    f"{_gigabytes(required)} of free space. Your archive is unchanged. "
                    "Free up space, then restart TweetNook."
                ) from error
            raise _failed_upgrade(error) from error
        except Exception as error:
            raise _failed_upgrade(error) from error

    def _announce_migration(self, current_version: int) -> None:
        # The Web server doesn't listen until this finishes, and a large archive takes minutes.
        # Without a message that looks like a failed start, and stopping it wastes the work.
        print(
            f"Upgrading the archive from schema v{current_version} to v{SCHEMA_VERSION}. "
            "This can take several minutes on a large archive; don't stop TweetNook.",
            file=sys.stderr,
            flush=True,
        )

    def _create_latest_schema(self) -> None:
        col_def = ", ".join(f"{name} {COLUMN_TYPES[name]}" for name in ARCHIVE_COLUMNS)
        with self.transaction():
            self.conn.execute(f"CREATE TABLE archive ({col_def})")
            self._create_canonical_tables()
            self._create_fts_schema()
            self._create_derived_structures()
            self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.conn.execute("PRAGMA optimize=0x10002")

    def _migrate_search_index(self, current_version: int) -> None:
        """Repair derived FTS documents/triggers without copying canonical archive data."""
        with self.transaction():
            self._drop_unread_followup_columns()
            self._merge_duplicate_media()
            self._create_canonical_tables()
            self._rebuild_fts_schema()
            self._drop_redefined_indexes()
            self._create_derived_structures()
            expected = self.conn.execute(
                f"SELECT COUNT(*) FROM archive WHERE record_type = '{RecordType.TWEET}'"
            ).fetchone()[0]
            actual = self.conn.execute("SELECT COUNT(*) FROM archive_fts").fetchone()[0]
            if actual != expected:
                raise RuntimeError(
                    "Search-index migration produced an unexpected row count: "
                    f"expected={expected}, actual={actual}"
                )
            self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

        self.migration_report = MigrationReport(
            from_version=current_version, to_version=SCHEMA_VERSION
        )

    def _migrate_search_facts(self, current_version: int) -> None:
        """Add missing derived search tables and drop the columns nothing reads.

        v8 lacks the search-filter facts; v8 and v9 lack the description index.
        v10 lacks the content/note flags used to filter uncaptured tweets and notes;
        v11 computed those flags from an incomplete list of placeholder texts. v13 and
        older lack the covering membership and attachment indexes.
        """
        with self.transaction():
            if current_version < 13:
                self._drop_unread_followup_columns()
            self._merge_duplicate_media()
            self._create_canonical_tables()
            self._drop_redefined_indexes()
            self._create_derived_structures()
            expected = self.conn.execute(
                f"SELECT COUNT(*) FROM archive WHERE record_type = '{RecordType.TWEET}' AND "
                "row_key IS NOT NULL"
            ).fetchone()[0]
            actual = self.conn.execute("SELECT COUNT(*) FROM archive_tweet_facts").fetchone()[0]
            if actual != expected:
                raise RuntimeError(
                    "Search-filter migration produced an unexpected row count: "
                    f"expected={expected}, actual={actual}"
                )
            description = _media_tag_description("raw_json")
            expected = self.conn.execute(
                f"SELECT COUNT(*) FROM archive WHERE record_type = '{RecordType.MEDIA_TAG}' "
                f"AND {description} != ''"
            ).fetchone()[0]
            actual = self.conn.execute("SELECT COUNT(*) FROM archive_description_fts").fetchone()[0]
            if actual != expected:
                raise RuntimeError(
                    "Description-index migration produced an unexpected row count: "
                    f"expected={expected}, actual={actual}"
                )
            self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

        self.migration_report = MigrationReport(
            from_version=current_version, to_version=SCHEMA_VERSION
        )

    def _drop_redefined_indexes(self) -> None:
        for name in REDEFINED_INDEXES:
            self.conn.execute(f"DROP INDEX IF EXISTS {name}")

    def _merge_duplicate_media(self) -> None:
        """Merge the rows sources created for one media item before they shared a row.

        The higher-ranked source's row survives with its key and filenames unchanged, filling
        blanks from the other copies. A file only the other row saved moves over with its metadata,
        so nothing is downloaded again; a file both saved stays on disk, unreferenced.
        """
        groups: dict[tuple[str, str], list[str]] = {}
        for row in self.conn.execute(
            f"SELECT row_key, tweet_id, media_key FROM archive "
            f"WHERE record_type = '{RecordType.MEDIA}'"
        ):
            media_id = numeric_media_id(row["media_key"])
            if media_id is not None:
                groups.setdefault((row["tweet_id"], media_id), []).append(row["row_key"])
        for row_keys in groups.values():
            if len(row_keys) < 2:
                continue
            placeholders = ", ".join("?" for _key in row_keys)
            rows = [
                dict(row)
                for row in self.conn.execute(
                    f"SELECT rowid AS archive_rowid, * FROM archive "
                    f"WHERE row_key IN ({placeholders})",
                    row_keys,
                )
            ]
            winner, *others = sorted(
                rows,
                key=lambda row: (
                    -SOURCE_RANK.get(row["provenance_source"], 0),
                    not row["local_path"],
                    row["archive_rowid"],
                ),
            )
            changes: dict[str, Any] = {}
            for fields in _MEDIA_MERGE_GROUPS:
                donor = next(
                    (other for other in others if not winner[fields[0]] and other[fields[0]]),
                    None,
                )
                if donor is not None:
                    changes.update({field: donor[field] for field in fields})
            payload = winner["raw_json"]
            for other in others:
                if not payload:
                    payload = other["raw_json"]
                elif other["raw_json"]:
                    try:
                        retained = json.loads(payload)
                        combined = _fill_media_payload(retained, json.loads(other["raw_json"]))
                    except (TypeError, ValueError):
                        continue
                    if combined != retained:
                        payload = json.dumps(combined)
            if payload != winner["raw_json"]:
                changes["raw_json"] = payload
            merged = {**winner, **changes}
            if merged["local_path"] and (
                merged["media_type"] not in {"video", "animated_gif"}
                or merged["thumbnail_local_path"]
            ):
                changes["download_state"] = "done"
                changes["download_error"] = None
            elif merged["download_state"] == "done":
                # Restoring a missing video type can reveal an unsaved poster.
                changes["download_state"] = "pending"
                changes["download_error"] = None
            changes["downloaded_at"] = next(
                (row["downloaded_at"] for row in (winner, *others) if row["downloaded_at"]), None
            )
            changes["first_seen_at"] = min(
                (row["first_seen_at"] for row in rows if row["first_seen_at"]), default=None
            )
            assignments = ", ".join(f"{field} = ?" for field in changes)
            self.conn.execute(
                f"UPDATE archive SET {assignments} WHERE row_key = ?",
                (*changes.values(), winner["row_key"]),
            )
            self.conn.executemany(
                "DELETE FROM archive WHERE row_key = ?", [(other["row_key"],) for other in others]
            )

    def _drop_unread_followup_columns(self) -> None:
        """Remove the manifest's follow-up status, which schemas before 13 hold and nothing reads.

        SQLite rewrites every row to do this, so the first open after an upgrade takes longer
        on a large archive, and the transaction undoes it if the disk fills.
        """
        for column in ("enrichment_followup_status", "enrichment_aborted_reason"):
            self.conn.execute(f"ALTER TABLE archive DROP COLUMN {column}")

    def _archive_table_exists(self) -> bool:
        return (
            self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'archive'"
            ).fetchone()
            is not None
        )

    def _get_schema_version(self) -> int:
        return int(self.conn.execute("PRAGMA user_version").fetchone()[0])

    def _backfill_created_at_timestamps(self) -> None:
        remaining = self.conn.execute(
            "SELECT row_key, created_at FROM archive "
            "WHERE created_at IS NOT NULL AND created_at_ts IS NULL"
        ).fetchall()
        updates = []
        for row in remaining:
            created_at = parse_created_at(row[1])
            if created_at is not None:
                updates.append((int(created_at.timestamp()), row[0]))
        if updates:
            self.conn.executemany("UPDATE archive SET created_at_ts = ? WHERE row_key = ?", updates)

    def _create_canonical_tables(self) -> None:
        """Saved data beside the archive table, which no rebuild of derived state may touch."""
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS community_notes ("
            "tweet_id TEXT NOT NULL, text_hash TEXT NOT NULL, text TEXT NOT NULL, "
            "raw_json TEXT, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, "
            "removed_at TEXT, PRIMARY KEY (tweet_id, text_hash))"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS tweet_checks ("
            "tweet_id TEXT PRIMARY KEY, checked_at TEXT NOT NULL, "
            "outcome TEXT NOT NULL, http_status INTEGER, scope TEXT, reason TEXT)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tweet_checks_checked_at ON tweet_checks (checked_at)"
        )
        # One row per change in a tweet's check result, not one per night.
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS tweet_check_history ("
            "tweet_id TEXT NOT NULL, checked_at TEXT NOT NULL, outcome TEXT NOT NULL, "
            "http_status INTEGER, scope TEXT, reason TEXT, "
            "PRIMARY KEY (tweet_id, checked_at))"
        )

    def _create_fts_schema(self, *, populate: bool = False) -> None:
        self.conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS archive_fts USING fts5(
                author_username, author_display_name, text, note_tweet_text,
                content=''
            )
        """)
        if populate:
            self.conn.execute(f"""
                INSERT INTO archive_fts(
                    rowid, author_username, author_display_name, text, note_tweet_text
                )
                SELECT rowid, author_username, author_display_name, text, note_tweet_text
                FROM archive
                WHERE record_type = '{RecordType.TWEET}'
            """)

    def _create_fts_triggers(self) -> None:
        self.conn.execute(f"""
        CREATE TRIGGER archive_ad AFTER DELETE ON archive
        WHEN old.record_type = '{RecordType.TWEET}' BEGIN
          INSERT INTO archive_fts(
            archive_fts, rowid, author_username, author_display_name, text, note_tweet_text
          ) VALUES(
            'delete', old.rowid, old.author_username, old.author_display_name,
            old.text, old.note_tweet_text
          );
        END;
        """)
        self.conn.execute(f"""
        CREATE TRIGGER archive_ai AFTER INSERT ON archive
        WHEN new.record_type = '{RecordType.TWEET}' BEGIN
          INSERT INTO archive_fts(
            rowid, author_username, author_display_name, text, note_tweet_text
          ) VALUES(
            new.rowid, new.author_username, new.author_display_name,
            new.text, new.note_tweet_text
          );
        END;
        """)
        self.conn.execute(f"""
        CREATE TRIGGER archive_au AFTER UPDATE ON archive
        WHEN (old.record_type = '{RecordType.TWEET}' OR new.record_type = '{RecordType.TWEET}')
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
          WHERE old.record_type = '{RecordType.TWEET}';
          INSERT INTO archive_fts(
            rowid, author_username, author_display_name, text, note_tweet_text
          ) SELECT
            new.rowid, new.author_username, new.author_display_name,
            new.text, new.note_tweet_text
          WHERE new.record_type = '{RecordType.TWEET}';
        END;
        """)

    def _rebuild_fts_schema(self) -> None:
        for trigger in ("archive_ad", "archive_ai", "archive_au"):
            self.conn.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        self.conn.execute("DROP TABLE IF EXISTS archive_fts")
        self._create_fts_schema(populate=True)
        self._create_fts_triggers()

    def _drop_derived_triggers(self) -> None:
        """Drop every trigger on archive, all of which _create_derived_structures recreates.

        Creating a trigger only if it's missing would keep an older body on an upgraded archive,
        so a change to a trigger's definition wouldn't reach the archives that need it.
        """
        for row in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'archive'"
        ).fetchall():
            self.conn.execute(f'DROP TRIGGER "{row["name"]}"')

    def _create_derived_structures(self) -> None:
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
        self._create_search_indexes()
        self.conn.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_archive_media_tag_lookup
            ON archive(tweet_id, raw_json)
            WHERE record_type = '{RecordType.MEDIA_TAG}'
        """)
        self.conn.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_archive_search_author
            ON archive(LOWER(COALESCE(author_username, '')), created_at_ts DESC, tweet_id)
            WHERE record_type = '{RecordType.TWEET}'
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_archive_profile_author
            ON archive(author_id)
            WHERE author_id IS NOT NULL AND author_id != ''
        """)
        self._drop_derived_triggers()
        self._create_fts_triggers()
        self._create_tweet_facts()
        self._create_tweet_content()
        self._create_description_fts()
        self._create_cache_revisions()

    def _create_search_indexes(self) -> None:
        """Indexes that hold every column search reads from memberships and attachments.

        Search touches every membership and attachment its filters consider. Read from
        the table, each one costs a page of a large row, which made the first search after
        a start take seconds; these partial indexes are small enough to stay cached.
        record_type is a column although the WHERE fixes it: SQLite only answers a query
        from an index alone when the index holds every column the query names.
        """
        self.conn.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_archive_search_attachment
            ON archive(record_type, tweet_id, media_type, source)
            WHERE record_type IN ('{RecordType.MEDIA}', '{RecordType.URL_REF}')
        """)
        self.conn.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_archive_membership
            ON archive(tweet_id, row_key, collection_type, created_at_ts, sort_index, record_type)
            WHERE record_type = '{RecordType.TWEET}'
        """)

    def _create_tweet_content(self) -> None:
        """Index captured content and notes in memberships and context objects."""
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS archive_tweet_content "
            "(row_key TEXT PRIMARY KEY, tweet_id TEXT, has_content INTEGER NOT NULL, "
            "has_note INTEGER NOT NULL) WITHOUT ROWID"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tweet_content_captured "
            "ON archive_tweet_content (has_content, tweet_id)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tweet_content_note "
            "ON archive_tweet_content (has_note, tweet_id)"
        )
        columns = "row_key, tweet_id, has_content, has_note"
        types = f"('{RecordType.TWEET}', '{RecordType.TWEET_OBJECT}')"
        upsert = (
            f"INSERT OR REPLACE INTO archive_tweet_content ({columns}) "
            f"SELECT new.row_key, {_tweet_content_values('new')} "
            f"WHERE new.record_type IN {types} AND new.row_key IS NOT NULL;"
        )
        self.conn.execute(
            "CREATE TRIGGER archive_content_ai AFTER INSERT ON archive "
            f"WHEN new.record_type IN {types} AND new.row_key IS NOT NULL "
            f"BEGIN {upsert} END"
        )
        self.conn.execute(
            "CREATE TRIGGER archive_content_au "
            "AFTER UPDATE OF row_key, record_type, tweet_id, text, raw_json ON archive "
            f"WHEN old.record_type IN {types} OR new.record_type IN {types} BEGIN "
            "DELETE FROM archive_tweet_content WHERE row_key = old.row_key; "
            f"{upsert} END"
        )
        self.conn.execute(
            "CREATE TRIGGER archive_content_ad AFTER DELETE ON archive "
            f"WHEN old.record_type IN {types} BEGIN "
            "DELETE FROM archive_tweet_content WHERE row_key = old.row_key; END"
        )
        self.conn.execute(
            f"INSERT OR REPLACE INTO archive_tweet_content ({columns}) "
            f"SELECT content.row_key, {_tweet_content_values('content')} FROM archive AS content "
            f"WHERE content.record_type IN {types} AND content.row_key IS NOT NULL"
        )

    def _create_tweet_facts(self) -> None:
        """Create, backfill, and maintain the derived search-filter values.

        Membership writes always carry a row key; keyless rows (only possible through
        direct SQL) get no facts instead of failing the write.
        """
        columns = ", ".join(TWEET_FACT_EXPRESSIONS)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS archive_tweet_facts "
            f"(row_key TEXT PRIMARY KEY, {columns}) WITHOUT ROWID"
        )

        def upsert(row: str) -> str:
            return (
                f"INSERT OR REPLACE INTO archive_tweet_facts (row_key, {columns}) "
                f"SELECT {row}.row_key, {_tweet_fact_values(row + '.raw_json')}"
            )

        self.conn.execute(
            "CREATE TRIGGER archive_facts_ai AFTER INSERT ON archive "
            f"WHEN new.record_type = '{RecordType.TWEET}' AND new.row_key IS NOT NULL "
            f"BEGIN {upsert('new')}; END"
        )
        self.conn.execute(
            "CREATE TRIGGER archive_facts_au "
            "AFTER UPDATE OF row_key, record_type, raw_json ON archive "
            f"WHEN old.record_type = '{RecordType.TWEET}' OR new.record_type = "
            f"'{RecordType.TWEET}' BEGIN "
            "DELETE FROM archive_tweet_facts WHERE row_key = old.row_key; "
            f"{upsert('new')} WHERE new.record_type = '{RecordType.TWEET}' AND new.row_key IS NOT "
            "NULL; END"
        )
        self.conn.execute(
            "CREATE TRIGGER archive_facts_ad AFTER DELETE ON archive "
            f"WHEN old.record_type = '{RecordType.TWEET}' BEGIN "
            "DELETE FROM archive_tweet_facts WHERE row_key = old.row_key; END"
        )
        self.conn.execute(
            f"INSERT OR REPLACE INTO archive_tweet_facts (row_key, {columns}) "
            f"SELECT row_key, {_tweet_fact_values('raw_json')} "
            f"FROM archive WHERE record_type = '{RecordType.TWEET}' AND row_key IS NOT NULL"
        )

    def _create_description_fts(self) -> None:
        """Create, backfill, and maintain the full-text index of media-tag descriptions.

        Rows share the media_tag archive row's rowid. The index stores its own copy
        of each description, so triggers delete by rowid without the old text.
        """
        exists = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'archive_description_fts'"
        ).fetchone()
        self.conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS archive_description_fts USING fts5(description)"
        )

        def insert(row: str) -> str:
            description = _media_tag_description(f"{row}.raw_json")
            return (
                "INSERT INTO archive_description_fts (rowid, description) "
                f"SELECT {row}.rowid, {description} "
                f"WHERE {row}.record_type = '{RecordType.MEDIA_TAG}' AND {description} != ''"
            )

        self.conn.execute(
            "CREATE TRIGGER archive_description_ai AFTER INSERT ON archive "
            f"WHEN new.record_type = '{RecordType.MEDIA_TAG}' BEGIN {insert('new')}; END"
        )
        self.conn.execute(
            "CREATE TRIGGER archive_description_au "
            "AFTER UPDATE OF record_type, raw_json ON archive "
            f"WHEN old.record_type = '{RecordType.MEDIA_TAG}' OR new.record_type = "
            f"'{RecordType.MEDIA_TAG}' BEGIN "
            "DELETE FROM archive_description_fts WHERE rowid = old.rowid; "
            f"{insert('new')}; END"
        )
        self.conn.execute(
            "CREATE TRIGGER archive_description_ad AFTER DELETE ON archive "
            f"WHEN old.record_type = '{RecordType.MEDIA_TAG}' BEGIN "
            "DELETE FROM archive_description_fts WHERE rowid = old.rowid; END"
        )
        if not exists:
            description = _media_tag_description("raw_json")
            self.conn.execute(
                "INSERT INTO archive_description_fts (rowid, description) "
                f"SELECT rowid, {description} FROM archive "
                f"WHERE record_type = '{RecordType.MEDIA_TAG}' AND {description} != ''"
            )

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
        # What the like order reads from a membership; its text and raw payload don't affect it.
        like_membership_columns = (
            "record_type",
            "tweet_id",
            "collection_type",
            "sort_index",
            "source",
        )
        scopes = {
            "membership": (
                lambda row: f"{row}.record_type = '{RecordType.TWEET}'",
                ("record_type", "tweet_id", "collection_type"),
            ),
            "like_order": (
                lambda row: (
                    f"({row}.record_type = '{RecordType.TWEET}' AND {row}.collection_type = "
                    "'like') OR "
                    f"({row}.record_type = '{RecordType.RAW_CAPTURE}' AND {row}.operation IN "
                    "('Likes', 'XArchiveLikes', 'XArchiveManifest')) OR "
                    f"{row}.record_type = '{RecordType.IMPORT_MANIFEST}'"
                ),
                (
                    *like_membership_columns,
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
                condition = " OR ".join(f"({relevant(row)})" for row in rows)
                if event == "UPDATE":
                    changed = " OR ".join(f"old.{col} IS NOT new.{col}" for col in columns)
                    if scope == "like_order":
                        membership_changed = " OR ".join(
                            f"old.{col} IS NOT new.{col}" for col in like_membership_columns
                        )
                        changed = (
                            f"((old.record_type = '{RecordType.TWEET}' AND new.record_type = "
                            f"'{RecordType.TWEET}' "
                            f"AND ({membership_changed})) OR "
                            f"((old.record_type != '{RecordType.TWEET}' OR new.record_type != "
                            f"'{RecordType.TWEET}') "
                            f"AND ({changed})))"
                        )
                    condition = f"({condition}) AND ({changed})"
                self.conn.execute(
                    f"CREATE TRIGGER archive_revision_{scope}_{event.lower()} "
                    f"AFTER {event} ON archive WHEN {condition} BEGIN "
                    "UPDATE archive_revisions SET generation = generation + 1 "
                    f"WHERE scope = '{scope}'; END"
                )

    def rebuild_search_index(self) -> None:
        """Finalize timestamp and full-text indexes after an explicit bulk import."""
        with self.transaction():
            self._backfill_created_at_timestamps()
            self._rebuild_fts_schema()
