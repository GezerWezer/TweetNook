"""The kinds of row the shared `archive` table holds, told apart by its record_type column."""

from __future__ import annotations

from enum import StrEnum


class RecordType(StrEnum):
    # A post's membership in one collection (like, bookmark or authored tweet).
    TWEET = "tweet"
    # The canonical content of a post, shared by all its memberships.
    TWEET_OBJECT = "tweet_object"
    TWEET_RELATION = "tweet_relation"
    TWEET_HIDDEN = "tweet_hidden"
    MEDIA = "media"
    MEDIA_TAG = "media_tag"
    URL = "url"
    URL_REF = "url_ref"
    ARTICLE = "article"
    RAW_CAPTURE = "raw_capture"
    IMPORT_MANIFEST = "import_manifest"
    SYNC_STATE = "sync_state"
    METADATA = "metadata"
