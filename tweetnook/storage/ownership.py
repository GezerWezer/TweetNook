"""Which writer owns each archive field, and what each kind of write may change.

Every write is one of three modes. A provider observation is a response just received. A
saved-evidence replay rebuilds rows from a capture already on disk, so it says nothing about
now and leaves lifecycle alone. A worker result is the outcome of follow-up work on a row and
changes only that worker's fields. docs/development/storage.md gives the rules per family.
"""

from __future__ import annotations

from enum import Enum

from .record_types import RecordType


class MergeMode(Enum):
    PROVIDER_OBSERVATION = "provider_observation"
    EVIDENCE_REPLAY = "evidence_replay"
    WORKER_RESULT = "worker_result"


LIFECYCLE_FIELDS = frozenset(
    {
        "deleted_at",
        "enrichment_state",
        "enrichment_checked_at",
        "enrichment_http_status",
        "enrichment_reason",
        "enrichment_detail",
        "enrichment_retry_count",
        "enrichment_next_retry_at",
        "enrichment_first_unavailable_at",
        "enrichment_retry_eligible",
    }
)
MEDIA_WORKER_FIELDS = frozenset(
    {
        "download_state",
        "local_path",
        "sha256",
        "byte_size",
        "content_type",
        "thumbnail_local_path",
        "thumbnail_sha256",
        "thumbnail_byte_size",
        "thumbnail_content_type",
        "downloaded_at",
        "download_error",
    }
)
URL_WORKER_FIELDS = frozenset(
    {
        "http_status",
        "final_url",
        "canonical_url",
        "url",
        "title",
        "description",
        "site_name",
        "content_type",
        "unfurl_state",
        "last_fetched_at",
        "download_error",
    }
)
_WORKER_FIELDS = {RecordType.MEDIA: MEDIA_WORKER_FIELDS, RecordType.URL: URL_WORKER_FIELDS}


def writes_lifecycle(mode: MergeMode) -> bool:
    """Only a response just received can say whether a post is available now."""
    return mode is MergeMode.PROVIDER_OBSERVATION


def worker_changes(record_type: RecordType, changes: dict[str, object]) -> dict[str, object]:
    """Check that a worker result stays within the fields its worker owns."""
    owned = _WORKER_FIELDS.get(record_type, frozenset())
    foreign = changes.keys() - owned
    if foreign:
        raise ValueError(f"{record_type} worker cannot write {sorted(foreign)}")
    return changes
