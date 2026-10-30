"""Chronological logical-post keys shared by feed, search and mixed results.

The first membership row key in the requested collections supplies the timestamp
and numeric timeline key. Unknown timestamps follow dated posts in either direction;
timeline keys and text IDs follow the requested direction, with SQLite's native
NULL ordering for timeline keys. Export has a separate legacy-date contract.
"""

from __future__ import annotations

from typing import Any

from .common import Clause, clause_in


def representative_membership(tweet_id: str, collections: set[str] | None) -> Clause:
    scope = clause_in("member.collection_type", collections) if collections else Clause("")
    return Clause(
        "(SELECT member.row_key FROM archive AS member INDEXED BY idx_archive_membership "
        f"WHERE member.record_type = 'tweet' AND member.tweet_id = {tweet_id} "
        f"{'AND ' + scope.sql if scope.sql else ''} ORDER BY member.row_key LIMIT 1)",
        scope.params,
    )


def chronological_columns(alias: str) -> list[str]:
    return [f"{alias}.created_at_ts", f"CAST({alias}.sort_index AS INTEGER)", f"{alias}.tweet_id"]


def chronological_order(alias: str, sort: str, *, unknown_last: bool = True) -> str:
    direction = "ASC" if sort == "oldest" else "DESC"
    columns = chronological_columns(alias)
    return ", ".join(
        ([f"{columns[0]} IS NULL"] if unknown_last else [])
        + [f"{column} {direction}" for column in columns]
    )


def sort_chronologically(
    rows: list[dict[str, Any]], keys: dict[str, dict[str, Any]], *, sort: str
) -> list[dict[str, Any]]:
    def key(row):
        tweet_id = str(row.get("tweet_id") or "")
        ordering = keys.get(tweet_id, {})
        timestamp, index = ordering.get("created_at_ts"), ordering.get("sort_index")
        return (timestamp or 0, index is not None, index or 0, tweet_id)

    ordered = sorted(rows, key=key, reverse=sort == "newest")
    # Stable partition keeps the native tie-key order within the unknown tail.
    return sorted(
        ordered, key=lambda row: keys.get(row["tweet_id"], {}).get("created_at_ts") is None
    )
