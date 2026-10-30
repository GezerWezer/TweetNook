"""Context graph projections and scoped prune row ownership."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from .db import ConnectionMixin
from .record_types import RecordType

# Rows owned by a single tweet, keyed by its tweet_id.
TWEET_RECORD_TYPES = (
    RecordType.TWEET_OBJECT,
    RecordType.TWEET_RELATION,
    RecordType.MEDIA,
    RecordType.URL_REF,
    RecordType.ARTICLE,
    RecordType.MEDIA_TAG,
    RecordType.TWEET_HIDDEN,
)
# Raw captures whose cursor_in is the tweet they fetched.
TWEET_CAPTURE_OPERATIONS = (
    "ThreadExpandDetail",
    "TweetDetail",
    "TweetDetailResurrection",
    "FxTwitterStatus",
)
EMBEDDED_RELATIONS = ("quote_of", "retweet_of")


@dataclass(slots=True)
class PruneSelection:
    row_counts: Counter[str]
    thread_fetch_count: int
    media_paths: set[str]


def _load_prune_rows(store: ConnectionMixin, pruned_tweet_ids: Iterable[str]) -> None:
    """Fill temp tables with the out-of-scope tweet ids and every row to delete."""
    conn = store.conn
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS prune_ids (tweet_id TEXT PRIMARY KEY)")
    conn.execute(
        "CREATE TEMP TABLE IF NOT EXISTS prune_rows "
        "(rid INTEGER PRIMARY KEY, record_type TEXT, tweet_id TEXT, operation TEXT)"
    )
    conn.execute("DELETE FROM temp.prune_ids")
    conn.execute("DELETE FROM temp.prune_rows")
    conn.executemany(
        "INSERT OR IGNORE INTO temp.prune_ids VALUES (?)",
        ((tweet_id,) for tweet_id in pruned_tweet_ids),
    )
    record_types = ", ".join(f"'{name}'" for name in TWEET_RECORD_TYPES)
    conn.execute(
        f"""
        INSERT OR IGNORE INTO temp.prune_rows
        SELECT owned.rowid, owned.record_type, owned.tweet_id, NULL
        FROM temp.prune_ids AS pruned
        JOIN archive AS owned INDEXED BY idx_archive_tweet_id ON owned.tweet_id = pruned.tweet_id
        WHERE owned.record_type IN ({record_types})
        """
    )
    operations = ", ".join(f"'{name}'" for name in TWEET_CAPTURE_OPERATIONS)
    conn.execute(
        f"""
        INSERT OR IGNORE INTO temp.prune_rows
        SELECT capture.rowid, '{RecordType.RAW_CAPTURE}', capture.cursor_in, capture.operation
        FROM temp.prune_ids AS pruned
        JOIN archive AS capture INDEXED BY idx_archive_capture_target
          ON capture.record_type = '{RecordType.RAW_CAPTURE}'
            AND capture.cursor_in = pruned.tweet_id
        WHERE capture.operation IN ({operations})
        """
    )
    # Unfurled links are shared by url_hash; drop only those no surviving ref uses.
    conn.execute(
        f"""
        INSERT OR IGNORE INTO temp.prune_rows
        SELECT url.rowid, '{RecordType.URL}', NULL, NULL
        FROM archive AS url
        WHERE url.row_key IN (
            SELECT 'url:' || ref.url_hash
            FROM temp.prune_rows AS doomed
            JOIN archive AS ref ON ref.rowid = doomed.rid
            WHERE doomed.record_type = '{RecordType.URL_REF}' AND ref.url_hash IS NOT NULL
            EXCEPT
            SELECT 'url:' || url_hash
            FROM archive
            WHERE record_type = '{RecordType.URL_REF}' AND url_hash IS NOT NULL
              AND rowid NOT IN (SELECT rid FROM temp.prune_rows)
        )
        AND url.record_type = '{RecordType.URL}'
        """
    )


def _media_paths(store: ConnectionMixin, *, pruned: bool) -> set[str]:
    membership = "IN" if pruned else "NOT IN"
    rows = store.conn.execute(
        f"""
        SELECT local_path, thumbnail_local_path FROM archive
        WHERE record_type = '{RecordType.MEDIA}'
          AND rowid {membership} (
            SELECT rid FROM temp.prune_rows WHERE record_type = '{RecordType.MEDIA}'
          )
          AND (local_path IS NOT NULL OR thumbnail_local_path IS NOT NULL)
        """
    )
    return {value for row in rows for value in row if value}


class PruneMixin(ConnectionMixin):
    def referenced_media_paths(self) -> set[str]:
        rows = self.conn.execute(
            "SELECT local_path, thumbnail_local_path FROM archive "
            f"WHERE record_type = '{RecordType.MEDIA}'"
        )
        return {value for row in rows for value in row if value}

    def prune_graph(self) -> tuple[set[str], dict[str, str], dict[str, list[str]], set[str]]:
        conversation_of: dict[str, str] = {}
        object_ids: set[str] = set()
        for tweet_id, record_type, conversation_id in self.conn.execute(
            "SELECT tweet_id, record_type, conversation_id FROM archive "
            f"WHERE record_type IN ('{RecordType.TWEET}', '{RecordType.TWEET_OBJECT}') "
            "AND tweet_id IS NOT NULL AND tweet_id != ''"
        ):
            if record_type == RecordType.TWEET_OBJECT:
                object_ids.add(tweet_id)
            if conversation_id:
                conversation_of[tweet_id] = conversation_id

        relation_types = ", ".join(f"'{name}'" for name in EMBEDDED_RELATIONS)
        embedded: dict[str, list[str]] = {}
        for source_id, target_id in self.conn.execute(
            "SELECT tweet_id, target_tweet_id FROM archive "
            f"WHERE record_type = '{RecordType.TWEET_RELATION}' AND relation_type IN "
            f"({relation_types}) "
            "AND target_tweet_id IS NOT NULL AND target_tweet_id != ''"
        ):
            embedded.setdefault(source_id, []).append(target_id)
        capture_targets = {
            str(row[0])
            for row in self.conn.execute(
                "SELECT DISTINCT cursor_in FROM archive INDEXED BY idx_archive_capture_target "
                f"WHERE record_type = '{RecordType.RAW_CAPTURE}' "
                "AND operation = 'ThreadExpandDetail' "
                "AND cursor_in IS NOT NULL AND cursor_in != ''"
            )
        }
        return object_ids, conversation_of, embedded, capture_targets

    def prune_plan(self, pruned_tweet_ids: Iterable[str]) -> PruneSelection:
        with self._prune_scope(pruned_tweet_ids, write=False):
            row_counts = Counter(
                dict(
                    self.conn.execute(
                        "SELECT record_type, COUNT(*) FROM temp.prune_rows GROUP BY record_type"
                    ).fetchall()
                )
            )
            thread_fetch_count = self.conn.execute(
                "SELECT COUNT(DISTINCT tweet_id) FROM temp.prune_rows "
                f"WHERE record_type = '{RecordType.RAW_CAPTURE}' "
                "AND operation = 'ThreadExpandDetail'"
            ).fetchone()[0]

            return PruneSelection(
                row_counts,
                thread_fetch_count,
                _media_paths(self, pruned=True) - _media_paths(self, pruned=False),
            )

    def apply_prune(self, pruned_tweet_ids: Iterable[str]) -> int:
        with self._prune_scope(pruned_tweet_ids, write=True):
            cursor = self.conn.execute(
                "DELETE FROM archive WHERE rowid IN (SELECT rid FROM temp.prune_rows)"
            )
            rows_deleted = cursor.rowcount

        return rows_deleted

    @contextmanager
    def _prune_scope(self, tweet_ids: Iterable[str], *, write: bool) -> Iterator[None]:
        with self.transaction(write=write):
            try:
                _load_prune_rows(self, tweet_ids)
                yield
            finally:
                self.conn.execute("DROP TABLE IF EXISTS temp.prune_rows")
                self.conn.execute("DROP TABLE IF EXISTS temp.prune_ids")
