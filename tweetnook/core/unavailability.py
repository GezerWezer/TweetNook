"""What X's placeholders and tombstones say about a post it will not show."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# The GraphQL result types X returns in place of a post it won't show.
TOMBSTONE_TYPENAMES = frozenset({"TweetTombstone", "TweetUnavailable"})


def is_tombstone(result: Any) -> bool:
    return isinstance(result, dict) and result.get("__typename") in TOMBSTONE_TYPENAMES


@dataclass(frozen=True, slots=True)
class UnavailableReason:
    key: str
    label: str
    message: str


# Every reason classify_tweet_unavailability or the archive import can record, in the
# order reports list them.
UNAVAILABLE_REASONS = (
    UnavailableReason(
        "protected_account", "Protected account", "This tweet is from a protected account."
    ),
    UnavailableReason(
        "suspended_account", "Suspended account", "This tweet is from a suspended account."
    ),
    UnavailableReason(
        "account_missing",
        "Missing account",
        "This tweet is from an account that no longer exists.",
    ),
    UnavailableReason("withheld", "Withheld", "This tweet is unavailable in your location."),
    UnavailableReason("not_found", "Not found", "This tweet could not be found."),
    UnavailableReason("unavailable_unknown", "Unknown availability", "This tweet is unavailable."),
    UnavailableReason(
        "deleted_by_author", "Deleted by author", "This tweet was deleted by its author."
    ),
    UnavailableReason(
        "archive_deleted", "Deleted in archive", "This tweet was deleted from Twitter/X."
    ),
)
# A deletion is final, so these are never retried.
PERMANENT_REASONS = frozenset({"archive_deleted", "deleted_by_author"})


def _normalize_system_text(text: str) -> str:
    # Only the right single quote is folded: the SQL capture flags fold the same one, and
    # placeholder detection must agree with them.
    return " ".join(text.casefold().replace("\u2019", "'").split())


def extract_tweet_unavailability_detail(result: dict[str, Any]) -> str | None:
    tombstone = result.get("tombstone")
    if isinstance(tombstone, dict):
        tombstone_text = tombstone.get("text")
        if isinstance(tombstone_text, str) and tombstone_text:
            return tombstone_text
        if isinstance(tombstone_text, dict):
            text = tombstone_text.get("text")
            if isinstance(text, str) and text:
                return text
    for key in ("reason", "message", "text"):
        value = result.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict):
            text = value.get("text")
            if isinstance(text, str) and text:
                return text
    return None


# X's system text for posts it will not show. Prefixes are casefolded with straight
# apostrophes; the storage capture flags compile the same list to SQL.
PLACEHOLDER_TEXT_MARKER = "{learnmore}"
PLACEHOLDER_TEXT_PREFIXES = (
    "this post is from ",
    "this tweet is from ",
    "this post from ",
    "this tweet from ",
    "this post was deleted ",
    "this tweet was deleted ",
    "this post is unavailable",
    "this tweet is unavailable",
    "this post is not available ",
    "this tweet is not available ",
    "this post has been withheld",
    "this tweet has been withheld",
    "you're unable to view this post",
    "you're unable to view this tweet",
)


def is_placeholder_text(text: str | None) -> bool:
    """Whether saved text is X's system placeholder rather than the post itself."""
    if not text:
        return False
    normalized = _normalize_system_text(text)
    return PLACEHOLDER_TEXT_MARKER in normalized or normalized.startswith(PLACEHOLDER_TEXT_PREFIXES)


def classify_tweet_unavailability(typename: str, text: str | None) -> str:
    # Drop X's {learnmore}-style link markers, which split phrases apart.
    stripped = re.sub(r"\{[^{}]*\}", " ", text or "").replace("\u2018", "'")
    normalized = _normalize_system_text(stripped)
    if any(
        phrase in normalized
        for phrase in (
            "protected account",
            "private account",
            "account is protected",
            "posts are protected",
            "tweets are protected",
            "confirmed followers",
            "not authorized to see",
            "account owner limits who can view",
            "limits who can view their posts",
            "unable to view this post because this account",
        )
    ):
        return "protected_account"
    if any(
        phrase in normalized
        for phrase in (
            "account suspended",
            "account is suspended",
            "account has been suspended",
            "account that authored this post has been suspended",
            "suspended account",
        )
    ):
        return "suspended_account"
    if any(
        phrase in normalized
        for phrase in (
            "account doesn't exist",
            "account does not exist",
            "account no longer exists",
            "account that no longer exists",
            "post is from an account that no longer exists",
            "user not found",
        )
    ):
        return "account_missing"
    if any(
        phrase in normalized
        for phrase in (
            "post was deleted by its author",
            "post was deleted by the post author",
            "deleted by the post author",
            "deleted by the tweet author",
            "author deleted this post",
            "author deleted",
        )
    ):
        return "deleted_by_author"
    if any(
        phrase in normalized
        for phrase in ("withheld", "not available in your country", "legal demand")
    ):
        return "withheld"
    if any(
        phrase in normalized for phrase in ("post not found", "tweet not found", "status not found")
    ):
        return "not_found"
    return "unavailable_unknown"
