"""Sync cursors and archive metadata: the owner account and import manifests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from tweetnook.core.exceptions import ArchiveOwnerMismatchError
from tweetnook.core.utils import utc_now

from .common import folder_key
from .record_types import RecordType
from .records import PageBuffer, RecordsMixin

T = TypeVar("T")


@dataclass(slots=True)
class SyncState:
    collection_type: str
    last_head_tweet_id: str | None = None
    backfill_cursor: str | None = None
    backfill_incomplete: bool = False
    updated_at: str | None = None


class SyncStateMixin(RecordsMixin):
    def metadata_value(self, key: str) -> str | None:
        row = self._get_row(self._row_key_for_metadata(key))
        return row["value"] if row else None

    def reserve_metadata_counter(
        self,
        key: str,
        decide: Callable[[str | None], tuple[str | None, T]],
        *,
        updated_at: str | None = None,
    ) -> T:
        """Commit a conditional counter replacement without an enclosing transaction."""
        with self.transaction(allow_nested=False):
            value, result = decide(self.metadata_value(key))
            if value is not None:
                self.conn.execute(
                    "INSERT INTO archive (row_key, record_type, key, value, updated_at) "
                    "VALUES (?, ?, ?, ?, ?) ON CONFLICT(row_key) DO UPDATE SET "
                    "record_type = excluded.record_type, key = excluded.key, "
                    "value = excluded.value, updated_at = excluded.updated_at",
                    (
                        self._row_key_for_metadata(key),
                        RecordType.METADATA,
                        key,
                        value,
                        updated_at or utc_now(),
                    ),
                )
            return result

    def get_sync_state(self, collection_type: str, folder_id: str | None = None) -> SyncState:
        row = self._get_row(self._row_key_for_sync_state(collection_type, folder_id))
        if row is None:
            return SyncState(collection_type=collection_type)
        return SyncState(
            collection_type=collection_type,
            last_head_tweet_id=row["last_head_tweet_id"],
            backfill_cursor=row["backfill_cursor"],
            backfill_incomplete=self._parse_bool(row["backfill_incomplete"]),
            updated_at=row["updated_at"],
        )

    def _sync_state_record(
        self,
        collection_type: str,
        *,
        last_head_tweet_id: str | None = None,
        backfill_cursor: str | None = None,
        backfill_incomplete: bool = False,
        folder_id: str | None = None,
    ) -> dict[str, Any]:
        return self._record(
            row_key=self._row_key_for_sync_state(collection_type, folder_id),
            record_type=RecordType.SYNC_STATE,
            collection_type=collection_type,
            folder_id=folder_key(folder_id),
            last_head_tweet_id=last_head_tweet_id,
            backfill_cursor=backfill_cursor,
            backfill_incomplete=backfill_incomplete,
            updated_at=utc_now(),
        )

    def set_sync_state(
        self,
        collection_type: str,
        *,
        last_head_tweet_id: str | None = None,
        backfill_cursor: str | None = None,
        backfill_incomplete: bool = False,
        folder_id: str | None = None,
        cursor: PageBuffer | None = None,
    ) -> None:
        record = self._sync_state_record(
            collection_type,
            last_head_tweet_id=last_head_tweet_id,
            backfill_cursor=backfill_cursor,
            backfill_incomplete=backfill_incomplete,
            folder_id=folder_id,
        )
        self._queue_record(record, cursor=cursor)

    def reset_sync_state(self, collection_type: str, folder_id: str | None = None) -> None:
        self._delete("row_key = ?", (self._row_key_for_sync_state(collection_type, folder_id),))

    def get_collection_tweet_ids(self, collection_type: str) -> set[str]:
        rows = self._query(
            f"record_type = '{RecordType.TWEET}' AND collection_type = ?",
            (collection_type,),
            cols=["tweet_id"],
        )
        return {row["tweet_id"] for row in rows}

    def get_archive_owner_id(self) -> str | None:
        row = self._get_row(self._row_key_for_metadata("owner_user_id"))
        return row["value"] if row else None

    def set_archive_owner_id(self, user_id: str, *, cursor: PageBuffer | None = None) -> None:
        record = self._record(
            row_key=self._row_key_for_metadata("owner_user_id"),
            record_type=RecordType.METADATA,
            key="owner_user_id",
            value=user_id,
            updated_at=utc_now(),
        )
        self._queue_record(record, cursor=cursor)

    def ensure_archive_owner_id(self, user_id: str | None) -> None:
        if not user_id:
            return
        existing = self.get_archive_owner_id()
        if existing and existing != user_id:
            raise ArchiveOwnerMismatchError(
                f"Local archive belongs to Twitter/X user {existing}, but current auth "
                f"resolved {user_id}."
            )
        if existing is None:
            self.set_archive_owner_id(user_id)

    def get_import_manifest(self, archive_digest: str) -> dict[str, Any] | None:
        return self._get_row(self._row_key_for_import_manifest(archive_digest))

    def completed_manifests(self) -> list[dict[str, Any]]:
        """Completed official imports eligible for deferred enrichment."""
        return self._query(f"record_type = '{RecordType.IMPORT_MANIFEST}' AND status = 'completed'")

    def has_completed_archive_import(self) -> bool:
        """Return whether at least one official archive import completed successfully."""

        return (
            self._count(
                f"record_type = '{RecordType.IMPORT_MANIFEST}' AND status IN ('completed', "
                "'sampled')"
            )
            > 0
        )

    def set_import_manifest(
        self,
        archive_digest: str,
        *,
        archive_generation_date: str | None,
        status: str,
        import_started_at: str | None = None,
        import_completed_at: str | None = None,
        warnings: list[str] | None = None,
        counts: dict[str, Any] | None = None,
    ) -> None:
        existing = self.get_import_manifest(archive_digest)
        self.merge_rows(
            [
                self._record(
                    row_key=self._row_key_for_import_manifest(archive_digest),
                    record_type=RecordType.IMPORT_MANIFEST,
                    archive_digest=archive_digest,
                    archive_generation_date=archive_generation_date
                    or (existing.get("archive_generation_date") if existing else None),
                    import_started_at=import_started_at
                    or (existing.get("import_started_at") if existing else None)
                    or utc_now(),
                    import_completed_at=import_completed_at,
                    status=status,
                    warnings_json=self._json_value(warnings or []),
                    counts_json=self._json_value(counts or {}),
                    updated_at=utc_now(),
                )
            ]
        )
