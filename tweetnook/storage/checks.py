"""Availability check results, context checks and community notes."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from tweetnook.core.utils import utc_now

from .common import RESURRECTION_ELIGIBLE_SQL
from .record_types import RecordType
from .records import RecordsMixin
from .schema import SchemaMixin


class ChecksMixin(SchemaMixin, RecordsMixin):
    def ensure_check_tables(self) -> None:
        with self.transaction():
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS tweet_checks ("
                "tweet_id TEXT PRIMARY KEY, checked_at TEXT NOT NULL, "
                "outcome TEXT NOT NULL, http_status INTEGER, scope TEXT, reason TEXT)"
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tweet_checks_checked_at "
                "ON tweet_checks (checked_at)"
            )
            # One row per change in a tweet's check result, not one per night.
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS tweet_check_history ("
                "tweet_id TEXT NOT NULL, checked_at TEXT NOT NULL, outcome TEXT NOT NULL, "
                "http_status INTEGER, scope TEXT, reason TEXT, "
                "PRIMARY KEY (tweet_id, checked_at))"
            )
            self._create_community_notes_table()

    def _table_exists(self, name: str) -> bool:
        return (
            self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
            ).fetchone()
            is not None
        )

    @staticmethod
    def _check_state(outcome: str | None) -> str | None:
        if outcome in {"available", "restored", "enriched"}:
            return "available"
        if outcome in {"tombstone", "not_found"}:
            return "missing"
        return outcome

    def record_tweet_checks(
        self,
        checks: list[tuple[str, str, int | None, str, str | None]],
        *,
        checked_at: str | None = None,
    ) -> None:
        """Upsert ``(tweet_id, outcome, http_status, scope, reason)`` per checked tweet.

        A history row is added the first time a tweet is checked and whenever its
        result changes from the last recorded one (restores and enrichments always
        count). Failed lookups aren't recorded, so the tweet stays due; a ``suspect``
        result (a first bare 404) only updates the latest check.
        """

        checks = [check for check in checks if check[1] != "error"]
        if not checks:
            return
        stamp = checked_at or utc_now()
        previous: dict[str, tuple[str, str | None]] = {}
        ids = list(dict.fromkeys(check[0] for check in checks))
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            placeholders = ", ".join("?" for _ in chunk)
            for row in self.conn.execute(
                f"SELECT tweet_id, outcome, reason, MAX(checked_at) FROM tweet_check_history "
                f"WHERE tweet_id IN ({placeholders}) GROUP BY tweet_id",
                chunk,
            ):
                previous[row[0]] = (row[1], row[2])
        history = []
        for tweet_id, outcome, http_status, scope, reason in checks:
            if outcome == "suspect":
                continue
            prior = previous.get(tweet_id)
            changed = (
                prior is None
                or outcome in {"restored", "enriched"}
                or self._check_state(prior[0]) != self._check_state(outcome)
                or (self._check_state(outcome) == "missing" and prior[1] != reason)
            )
            if changed:
                history.append((tweet_id, stamp, outcome, http_status, scope, reason))
            previous[tweet_id] = (outcome, reason)
        with self.transaction():
            self.conn.executemany(
                "INSERT INTO tweet_checks "
                "(tweet_id, checked_at, outcome, http_status, scope, reason) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(tweet_id) DO UPDATE SET "
                "checked_at = excluded.checked_at, outcome = excluded.outcome, "
                "http_status = excluded.http_status, scope = excluded.scope, "
                "reason = excluded.reason",
                [
                    (tweet_id, stamp, outcome, http_status, scope, reason)
                    for tweet_id, outcome, http_status, scope, reason in checks
                ],
            )
            self.conn.executemany(
                "INSERT OR REPLACE INTO tweet_check_history "
                "(tweet_id, checked_at, outcome, http_status, scope, reason) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                history,
            )

    def get_tweet_check_outcomes(self, tweet_ids: list[str]) -> dict[str, str]:
        """The latest recorded check outcome per tweet."""

        if not tweet_ids or not self._table_exists("tweet_checks"):
            return {}
        outcomes: dict[str, str] = {}
        for start in range(0, len(tweet_ids), 500):
            chunk = tweet_ids[start : start + 500]
            placeholders = ", ".join("?" for _ in chunk)
            for row in self.conn.execute(
                f"SELECT tweet_id, outcome FROM tweet_checks WHERE tweet_id IN ({placeholders})",
                chunk,
            ):
                outcomes[row[0]] = row[1]
        return outcomes

    def get_tweet_check(self, tweet_id: str) -> dict[str, Any] | None:
        if not self._table_exists("tweet_checks"):
            return None
        row = self.conn.execute(
            "SELECT * FROM tweet_checks WHERE tweet_id = ?", (tweet_id,)
        ).fetchone()
        return dict(row) if row is not None else None

    def list_tweet_check_history(self, tweet_id: str) -> list[dict[str, Any]]:
        if not self._table_exists("tweet_check_history"):
            return []
        rows = self.conn.execute(
            "SELECT * FROM tweet_check_history WHERE tweet_id = ? ORDER BY checked_at",
            (tweet_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_target_captures(
        self, tweet_id: str, operations: tuple[str, ...]
    ) -> list[dict[str, Any]]:
        """Raw captures made for one tweet (its id is the capture's ``cursor_in``)."""

        placeholders = ", ".join("?" for _ in operations)
        rows = self.conn.execute(
            f"SELECT operation, captured_at, http_status, source, raw_json "
            f"FROM archive INDEXED BY idx_archive_capture_target "
            f"WHERE record_type = '{RecordType.RAW_CAPTURE}' AND cursor_in = ? "
            f"AND operation IN ({placeholders}) ORDER BY captured_at",
            (tweet_id, *operations),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_tweet_object_rows(self, tweet_ids: list[str]) -> dict[str, dict[str, Any]]:
        rows = self._rows_for_values(
            RecordType.TWEET_OBJECT,
            "tweet_id",
            tweet_ids,
            columns=[
                "tweet_id",
                "author_id",
                "source",
                "deleted_at",
                "enrichment_state",
                "enrichment_reason",
                "enrichment_detail",
                "enrichment_checked_at",
                "enrichment_retry_count",
                "enrichment_next_retry_at",
                "enrichment_retry_eligible",
            ],
        )
        return {row["tweet_id"]: row for row in rows}

    def count_context_tweets(self) -> int:
        """Archived tweet objects that are not saves and not already unavailable."""

        return int(
            self.conn.execute(
                f"""
                SELECT COUNT(*) FROM archive AS object
                WHERE object.record_type = '{RecordType.TWEET_OBJECT}'
                  AND COALESCE(object.enrichment_state, '') != 'terminal_unavailable'
                  AND NOT EXISTS (
                      SELECT 1 FROM archive AS saved INDEXED BY idx_archive_membership
                      WHERE saved.record_type = '{RecordType.TWEET}'
                        AND saved.tweet_id = object.tweet_id
                  )
                """
            ).fetchone()[0]
        )

    def list_context_check_tweet_ids(self, *, limit: int, checked_before: str) -> list[str]:
        """Context tweets due for a rolling check.

        Tweets a 404 left suspect come first (a second 404 confirms them), then
        never-checked and oldest-checked ones.
        """

        if limit <= 0:
            return []
        self.ensure_check_tables()
        rows = self.conn.execute(
            f"""
            SELECT object.tweet_id FROM archive AS object
            LEFT JOIN tweet_checks AS checks ON checks.tweet_id = object.tweet_id
            WHERE object.record_type = '{RecordType.TWEET_OBJECT}'
              AND COALESCE(object.enrichment_state, '') != 'terminal_unavailable'
              AND (
                  checks.checked_at IS NULL
                  OR checks.checked_at < ?
                  OR checks.outcome = 'suspect'
              )
              AND NOT EXISTS (
                  SELECT 1 FROM archive AS saved INDEXED BY idx_archive_membership
                  WHERE saved.record_type = '{RecordType.TWEET}'
                    AND saved.tweet_id = object.tweet_id
              )
            ORDER BY COALESCE(checks.outcome, '') != 'suspect',
                checks.checked_at IS NOT NULL, checks.checked_at, object.tweet_id
            LIMIT ?
            """,
            (checked_before, limit),
        ).fetchall()
        return [str(row[0]) for row in rows if row[0]]

    def list_retryable_unavailable_rows(self) -> list[dict[str, Any]]:
        """Every unavailable tweet that may come back, regardless of its retry date."""

        return self._query(
            f"record_type = '{RecordType.TWEET_OBJECT}' "
            "AND enrichment_state = 'terminal_unavailable' "
            f"AND {RESURRECTION_ELIGIBLE_SQL}",
            cols=[
                "tweet_id",
                "author_id",
                "enrichment_reason",
                "enrichment_detail",
                "enrichment_checked_at",
                "enrichment_retry_count",
                "enrichment_next_retry_at",
            ],
            order_by="tweet_id ASC",
        )

    def record_community_note(
        self,
        tweet_id: str,
        note: dict[str, Any] | None,
        *,
        seen_at: str | None = None,
        baseline: tuple[str, str | None] | None = None,
    ) -> str | None:
        """Keep every version of a tweet's community note.

        Returns ``"added"``, ``"changed"``, ``"removed"``, or ``None`` when nothing changed.
        A version is keyed by a hash of its text; a note that disappears gets ``removed_at``
        and comes back as the same version if its text returns unchanged. ``baseline``
        (text, saved at) is the note saved with the tweet, recorded first when the tweet
        has no versions yet.
        """

        self.ensure_check_tables()
        stamp = seen_at or utc_now()
        text = note.get("text") if isinstance(note, dict) else None
        if baseline is not None:
            base_text, base_at = baseline
            has_versions = self.conn.execute(
                "SELECT 1 FROM community_notes WHERE tweet_id = ? LIMIT 1", (tweet_id,)
            ).fetchone()
            if has_versions is None:
                with self.transaction():
                    self.conn.execute(
                        "INSERT INTO community_notes (tweet_id, text_hash, text, raw_json, "
                        "first_seen_at, last_seen_at, removed_at) VALUES (?, ?, ?, ?, ?, ?, NULL)",
                        (
                            tweet_id,
                            hashlib.sha256(base_text.encode("utf-8")).hexdigest()[:32],
                            base_text,
                            json.dumps({"text": base_text}),
                            base_at or stamp,
                            base_at or stamp,
                        ),
                    )
        active = self.conn.execute(
            "SELECT text_hash FROM community_notes WHERE tweet_id = ? AND removed_at IS NULL",
            (tweet_id,),
        ).fetchall()
        active_hashes = {row[0] for row in active}
        with self.transaction():
            if not text:
                if not active_hashes:
                    return None
                self.conn.execute(
                    "UPDATE community_notes SET removed_at = ? "
                    "WHERE tweet_id = ? AND removed_at IS NULL",
                    (stamp, tweet_id),
                )
                return "removed"
            text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
            if text_hash in active_hashes and len(active_hashes) == 1:
                self.conn.execute(
                    "UPDATE community_notes SET last_seen_at = ? "
                    "WHERE tweet_id = ? AND text_hash = ?",
                    (stamp, tweet_id, text_hash),
                )
                return None
            self.conn.execute(
                "UPDATE community_notes SET removed_at = ? "
                "WHERE tweet_id = ? AND removed_at IS NULL AND text_hash != ?",
                (stamp, tweet_id, text_hash),
            )
            self.conn.execute(
                "INSERT INTO community_notes "
                "(tweet_id, text_hash, text, raw_json, first_seen_at, last_seen_at, removed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL) ON CONFLICT(tweet_id, text_hash) DO UPDATE SET "
                "last_seen_at = excluded.last_seen_at, raw_json = excluded.raw_json, "
                "removed_at = NULL",
                (tweet_id, text_hash, text, json.dumps(note, sort_keys=True), stamp, stamp),
            )
            return "changed" if active_hashes else "added"

    def stored_community_note(self, tweet_id: str) -> tuple[str | None, str | None]:
        """The note in the tweet's stored X payload, with when that copy was first saved."""

        for record_type in (RecordType.TWEET, RecordType.TWEET_OBJECT):
            for row in self._rows_for_values(
                record_type, "tweet_id", [tweet_id], columns=["raw_json", "first_seen_at"]
            ):
                try:
                    raw = json.loads(row.get("raw_json") or "null")
                except json.JSONDecodeError:
                    continue
                pivot = raw.get("birdwatch_pivot") if isinstance(raw, dict) else None
                text = ((pivot or {}).get("subtitle") or {}).get("text")
                if text:
                    return text, row.get("first_seen_at")
        return None, None

    def list_community_notes(self, tweet_id: str) -> list[dict[str, Any]]:
        if not self._table_exists("community_notes"):
            return []
        rows = self.conn.execute(
            "SELECT tweet_id, text_hash, text, raw_json, first_seen_at, last_seen_at, removed_at "
            "FROM community_notes WHERE tweet_id = ? ORDER BY first_seen_at, text_hash",
            (tweet_id,),
        ).fetchall()
        return [dict(row) for row in rows]
