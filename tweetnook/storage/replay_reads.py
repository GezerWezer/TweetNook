"""Read-only archive queries a replay source answers from.

The replay source opens a short-lived read-only connection per lookup instead of an
``ArchiveStore``, so it never migrates, writes or holds the archive. These are its
queries, kept with the schema they read.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from .db import connect_read_only
from .record_types import RecordType

_OK_CAPTURE = f"record_type = '{RecordType.RAW_CAPTURE}' AND CAST(http_status AS INTEGER) = 200"
_DOWNLOADED = "local_path IS NOT NULL AND local_path != ''"


@contextmanager
def replay_connection(db_path: Path) -> Iterator[sqlite3.Connection]:
    if not db_path.is_file():
        raise FileNotFoundError(f"No archive database at {db_path}")
    connection = connect_read_only(db_path)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


def _placeholders(values: Sequence[object]) -> str:
    return ",".join("?" for _ in values)


def capture(conn: sqlite3.Connection, row_key: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT raw_json, http_status FROM archive WHERE row_key = ?", (row_key,)
    ).fetchone()


def timeline_captures(conn: sqlite3.Connection, operation: str) -> list[sqlite3.Row]:
    """Successful captures of one timeline operation, oldest first."""
    return conn.execute(
        f"SELECT row_key, cursor_in, cursor_out FROM archive WHERE {_OK_CAPTURE} "
        "AND operation = ? ORDER BY captured_at ASC",
        (operation,),
    ).fetchall()


def detail_captures(conn: sqlite3.Connection, operations: Sequence[str]) -> Iterator[sqlite3.Row]:
    """Successful TweetDetail captures (row_key, raw_json), oldest first."""
    return conn.execute(
        f"SELECT row_key, raw_json FROM archive WHERE {_OK_CAPTURE} "
        f"AND operation IN ({_placeholders(operations)}) ORDER BY captured_at ASC",
        tuple(operations),
    )


def latest_detail_capture(
    conn: sqlite3.Connection, tweet_id: str, operations: Sequence[str]
) -> sqlite3.Row | None:
    """The newest TweetDetail capture requested for ``tweet_id``, whatever its status."""
    return conn.execute(
        f"SELECT raw_json, http_status FROM archive WHERE record_type = '{RecordType.RAW_CAPTURE}' "
        f"AND cursor_in = ? AND operation IN ({_placeholders(operations)}) "
        "ORDER BY captured_at DESC LIMIT 1",
        (tweet_id, *operations),
    ).fetchone()


def tweet_object_payloads(conn: sqlite3.Connection, tweet_id: str) -> list[str | None]:
    return [
        row["raw_json"]
        for row in conn.execute(
            f"SELECT raw_json FROM archive WHERE record_type = '{RecordType.TWEET_OBJECT}' "
            "AND tweet_id = ?",
            (tweet_id,),
        )
    ]


def downloaded_media(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Media rows with a downloaded file or poster."""
    return conn.execute(
        f"""
        SELECT media_url, variants_json, local_path, content_type,
               thumbnail_url, thumbnail_local_path, thumbnail_content_type
        FROM archive
        WHERE record_type = '{RecordType.MEDIA}'
          AND ({_DOWNLOADED} OR (thumbnail_local_path IS NOT NULL AND thumbnail_local_path != ''))
        """
    ).fetchall()


def capture_counts(conn: sqlite3.Connection, operations: Sequence[str]) -> dict[str, int]:
    """Successful captures per operation."""
    return {
        row["operation"]: row["count"]
        for row in conn.execute(
            f"SELECT operation, COUNT(*) AS count FROM archive WHERE {_OK_CAPTURE} "
            f"AND operation IN ({_placeholders(operations)}) GROUP BY operation",
            tuple(operations),
        )
    }


def downloaded_media_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        f"SELECT COUNT(*) FROM archive WHERE record_type = '{RecordType.MEDIA}' AND {_DOWNLOADED}"
    ).fetchone()
    return int(row[0]) if row else 0


def owner_user_id(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT value FROM archive WHERE row_key = ? LIMIT 1", ("metadata:owner_user_id",)
    ).fetchone()
    return row["value"] if row else None
