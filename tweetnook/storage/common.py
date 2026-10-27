"""SQL fragments, source names and parsers shared by the storage modules."""

from __future__ import annotations

from datetime import datetime


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
    "WHERE hidden.record_type = 'tweet_hidden' AND hidden.tweet_id IS NOT NULL"
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
    "(saved.record_type = 'media' AND ("
    "COALESCE(saved.media_url, '') != '' OR COALESCE(saved.local_path, '') != '' OR "
    "COALESCE(saved.thumbnail_url, '') != '' OR "
    "COALESCE(saved.thumbnail_local_path, '') != '')) OR "
    "(saved.record_type = 'article' AND ("
    "COALESCE(saved.title, '') != '' OR COALESCE(saved.summary_text, '') != '' OR "
    "COALESCE(saved.content_text, '') != ''))))"
)
COMMUNITY_NOTE_TWEET_IDS_SQL = (
    "SELECT tweet_id FROM archive_tweet_content WHERE has_note = 1 "
    "UNION SELECT tweet_id FROM community_notes"
)
