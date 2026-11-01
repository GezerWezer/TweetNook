"""What X's placeholders and tombstones say about a post it will not show."""

from __future__ import annotations

import re
from typing import Any


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
    normalized = " ".join(text.casefold().replace("\u2019", "'").split())
    return PLACEHOLDER_TEXT_MARKER in normalized or normalized.startswith(PLACEHOLDER_TEXT_PREFIXES)


def classify_tweet_unavailability(typename: str, text: str | None) -> str:
    normalized = (text or "").casefold().replace("\u2019", "'").replace("\u2018", "'")
    normalized = re.sub(r"\{[^{}]*\}", " ", normalized)
    normalized = " ".join(normalized.split())
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
