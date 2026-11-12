"""Stored Web feed cards: each post's finished list-card JSON, valid for one archive generation."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .db import ConnectionMixin

# Every table a feed card reads from. Any write to one of them starts a new generation,
# which retires every stored card at once.
_CARD_SOURCES = ("archive", "community_notes", "tweet_checks")


class WebCardsMixin(ConnectionMixin):
    def _ensure_web_card_table(self) -> None:
        """Create the card table and the triggers that retire its rows.

        The triggers only bump a counter, so they never call a Python function and
        work on any connection, including the sqlite3 shell and the legacy importer.
        """
        with self.transaction():
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS tweetnook_web_cards (tweet_id TEXT NOT NULL, "
                "scope TEXT NOT NULL, generation INTEGER NOT NULL, card BLOB NOT NULL, "
                "PRIMARY KEY (scope, tweet_id)) WITHOUT ROWID"
            )
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS tweetnook_web_cards_state "
                "(id INTEGER PRIMARY KEY CHECK (id = 1), generation INTEGER NOT NULL)"
            )
            self.conn.execute("INSERT OR IGNORE INTO tweetnook_web_cards_state VALUES (1, 0)")
            for table in _CARD_SOURCES:
                for event in ("INSERT", "UPDATE", "DELETE"):
                    name = f"tweetnook_web_cards_{table}_{event.lower()}"
                    self.conn.execute(f"DROP TRIGGER IF EXISTS {name}")
                    self.conn.execute(
                        f"CREATE TRIGGER {name} AFTER {event} ON {table} BEGIN "
                        "UPDATE tweetnook_web_cards_state SET generation = generation + 1 "
                        "WHERE id = 1; END"
                    )

    def web_cards(self, tweet_ids: list[str], scope: str) -> tuple[int, dict[str, bytes]]:
        """Return the current generation and the stored cards that belong to it."""
        generation = int(
            self.conn.execute(
                "SELECT generation FROM tweetnook_web_cards_state WHERE id = 1"
            ).fetchone()[0]
        )
        if not tweet_ids:
            return generation, {}
        placeholders = ", ".join("?" for _ in tweet_ids)
        rows = self.conn.execute(
            "SELECT tweet_id, card FROM tweetnook_web_cards WHERE scope = ? AND generation = ? "
            f"AND tweet_id IN ({placeholders})",
            (scope, generation, *tweet_ids),
        ).fetchall()
        return generation, {str(row[0]): bytes(row[1]) for row in rows}


_swept_generation: int | None = None


def write_web_cards(db_path: Path, scope: str, generation: int, cards: dict[str, bytes]) -> None:
    """Store cards built at ``generation``; best effort, on a connection of its own.

    A card built before a concurrent write keeps its older generation and is never
    served. A busy archive skips the write rather than waiting for it.
    """
    global _swept_generation
    conn = sqlite3.connect(db_path, timeout=0.2)
    try:
        with conn:
            current = conn.execute(
                "SELECT generation FROM tweetnook_web_cards_state WHERE id = 1"
            ).fetchone()
            if current is None or int(current[0]) != generation:
                return
            conn.executemany(
                "INSERT OR REPLACE INTO tweetnook_web_cards VALUES (?, ?, ?, ?)",
                [(tweet_id, scope, generation, card) for tweet_id, card in cards.items()],
            )
            if _swept_generation != generation:
                # Cards from older generations are dead weight; sweep once per generation.
                conn.execute("DELETE FROM tweetnook_web_cards WHERE generation != ?", (generation,))
                _swept_generation = generation
    except sqlite3.Error:
        pass
    finally:
        conn.close()
