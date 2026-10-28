"""SQL fragments, source names and parsers shared by the storage modules."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime
from typing import Any, NamedTuple

from .record_types import RecordType


def folder_key(folder_id: str | None) -> str:
    return folder_id or ""


class Clause(NamedTuple):
    """A WHERE fragment and the values bound to its ? placeholders, in order."""

    sql: str
    params: tuple[Any, ...] = ()


# Past this many values an IN list binds one JSON array instead of a placeholder per value,
# which keeps large ID sets under SQLite's bound-variable limit.
_MAX_IN_PLACEHOLDERS = 500


def clause_in(field: str, values: Iterable[Any], *, negate: bool = False) -> Clause:
    operator = "NOT IN" if negate else "IN"
    # Sorted so equal sets build equal SQL, which keeps statement and count caches warm.
    ordered = tuple(sorted(values))
    if len(ordered) > _MAX_IN_PLACEHOLDERS:
        return Clause(
            f"{field} {operator} (SELECT value FROM json_each(?))", (json.dumps(ordered),)
        )
    return Clause(f"{field} {operator} ({', '.join('?' for _value in ordered)})", ordered)


def _pending_state_expr(field: str) -> str:
    return f"({field} IS NULL OR {field} = '' OR {field} = 'pending')"


def state_filter(field: str, states: set[str]) -> Clause:
    clauses: list[Clause] = []
    explicit_states = {state for state in states if state != "pending"}
    if explicit_states:
        clauses.append(clause_in(field, explicit_states))
    if "pending" in states:
        clauses.append(Clause(_pending_state_expr(field)))
    if not clauses:
        raise ValueError("state filter requires at least one state")
    if len(clauses) == 1:
        return clauses[0]
    joined = join_clauses(" OR ", clauses)
    return Clause(f"({joined.sql})", joined.params)


def join_clauses(separator: str, clauses: Iterable[Clause | str]) -> Clause:
    """Join fragments, skipping empty ones, and keep their parameters in placeholder order."""
    parts = [Clause(clause) if isinstance(clause, str) else clause for clause in clauses]
    parts = [part for part in parts if part.sql]
    return Clause(
        separator.join(part.sql for part in parts),
        tuple(param for part in parts for param in part.params),
    )


def and_clauses(*clauses: Clause | str) -> Clause:
    return join_clauses(" AND ", clauses)


def parse_created_at(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y")
    except (TypeError, ValueError):
        return None


LIVE_SOURCE = "live_graphql"
ARCHIVE_SOURCE = "x_archive"
FX_SOURCE = "fxtwitter"
# Higher ranks replace lower ones: a live GraphQL response replaces an FxTwitter
# placeholder, which replaces a sparse official-archive row. Unknown sources rank 0.
SOURCE_RANK = {ARCHIVE_SOURCE: 0, FX_SOURCE: 1, LIVE_SOURCE: 2}
AVAILABLE_SOURCES = frozenset({LIVE_SOURCE, FX_SOURCE})
SEARCH_COLLECTION_ORDER = ("bookmark", "like", "tweet")
# Tweets hidden from Web lists and searches; `is:hidden` still finds them.
HIDDEN_TWEET_IDS_SQL = (
    "SELECT hidden.tweet_id FROM archive AS hidden "
    f"WHERE hidden.record_type = '{RecordType.TWEET_HIDDEN}' AND hidden.tweet_id IS NOT NULL"
)
UNCAPTURED_TWEET_IDS_SQL = (
    "SELECT DISTINCT content.tweet_id FROM archive_tweet_content AS content "
    "WHERE content.has_content = 0 AND content.tweet_id IS NOT NULL "
    "AND NOT EXISTS (SELECT 1 FROM archive_tweet_content AS captured "
    "WHERE captured.tweet_id = content.tweet_id AND captured.has_content = 1) "
    # An official archive or partial capture can store useful secondary content
    # without post text/JSON. Use indexed IDs and scalar columns, not raw JSON.
    "AND NOT EXISTS (SELECT 1 FROM archive AS saved "
    "WHERE saved.tweet_id = content.tweet_id AND ("
    f"(saved.record_type = '{RecordType.MEDIA}' AND ("
    "COALESCE(saved.media_url, '') != '' OR COALESCE(saved.local_path, '') != '' OR "
    "COALESCE(saved.thumbnail_url, '') != '' OR "
    "COALESCE(saved.thumbnail_local_path, '') != '')) OR "
    f"(saved.record_type = '{RecordType.ARTICLE}' AND ("
    "COALESCE(saved.title, '') != '' OR COALESCE(saved.summary_text, '') != '' OR "
    "COALESCE(saved.content_text, '') != ''))))"
)
COMMUNITY_NOTE_TWEET_IDS_SQL = (
    "SELECT tweet_id FROM archive_tweet_content WHERE has_note = 1 "
    "UNION SELECT tweet_id FROM community_notes"
)
