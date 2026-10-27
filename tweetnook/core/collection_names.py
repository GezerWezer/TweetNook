"""Collection names accepted on the command line and in the Web API."""

from __future__ import annotations

COLLECTION_ALIASES = {
    "all": "all",
    "bookmark": "bookmark",
    "bookmarks": "bookmark",
    "like": "like",
    "likes": "like",
    "tweet": "tweet",
    "tweets": "tweet",
}

COLLECTION_LABELS = {
    "all": "all",
    "bookmark": "bookmarks",
    "like": "likes",
    "tweet": "tweets",
}


def normalize_collection_name(collection: str) -> str:
    normalized = COLLECTION_ALIASES.get(collection.strip().lower())
    if normalized is None:
        allowed = ", ".join(sorted(COLLECTION_ALIASES))
        raise ValueError(f"Unsupported collection '{collection}'. Expected one of: {allowed}.")
    return normalized


def display_collection_name(collection: str) -> str:
    return COLLECTION_LABELS[normalize_collection_name(collection)]
