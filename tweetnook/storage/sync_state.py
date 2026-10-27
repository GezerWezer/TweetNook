"""Sync cursors and archive metadata: the owner account and import manifests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tweetnook.exceptions import ArchiveOwnerMismatchError
from tweetnook.utils import utc_now

from .common import _expr_quote, _folder_key
from .records import RecordsMixin, _PageBuffer


@dataclass(slots=True)
class SyncState:
    collection_type: str
    last_head_tweet_id: str | None = None
    backfill_cursor: str | None = None
    backfill_incomplete: bool = False
    updated_at: str | None = None


class SyncStateMixin(RecordsMixin):
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
            record_type="sync_state",
            collection_type=collection_type,
            folder_id=_folder_key(folder_id),
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
        cursor: _PageBuffer | None = None,
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
        self._delete(
            f"row_key = {_expr_quote(self._row_key_for_sync_state(collection_type, folder_id))}"
        )

    def get_collection_tweet_ids(self, collection_type: str) -> set[str]:
        filter_expr = f"record_type = 'tweet' AND collection_type = {_expr_quote(collection_type)}"
        rows = self._query(expr=filter_expr, cols=["tweet_id"])
        return {row["tweet_id"] for row in rows}

    def get_archive_owner_id(self) -> str | None:
        row = self._get_row(self._row_key_for_metadata("owner_user_id"))
        return row["value"] if row else None

    def set_archive_owner_id(self, user_id: str, *, cursor: _PageBuffer | None = None) -> None:
        record = self._record(
            row_key=self._row_key_for_metadata("owner_user_id"),
            record_type="metadata",
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

    def has_completed_archive_import(self) -> bool:
        """Return whether at least one official archive import completed successfully."""

        return (
            self._count("record_type = 'import_manifest' AND status IN ('completed', 'sampled')")
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
        enrichment_followup_status: str | None = None,
        enrichment_aborted_reason: str | None = None,
    ) -> None:
        existing = self.get_import_manifest(archive_digest)
        self.merge_rows(
            [
                self._record(
                    row_key=self._row_key_for_import_manifest(archive_digest),
                    record_type="import_manifest",
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
                    enrichment_followup_status=enrichment_followup_status,
                    enrichment_aborted_reason=enrichment_aborted_reason,
                    updated_at=utc_now(),
                )
            ]
        )
