"""Writes from tweet detail, thread, unavailable-tweet and FxTwitter responses."""

from __future__ import annotations

from typing import Any

from tweetnook.core.extractor import (
    extract_note_tweet_text,
    extract_secondary_objects,
    extract_thread_objects,
)
from tweetnook.core.timeline_parsing import TimelineTweet
from tweetnook.core.utils import utc_now

from .common import FX_SOURCE, LIVE_SOURCE, SOURCE_RANK, created_at_timestamp
from .record_types import RecordType
from .records import PageBuffer
from .tweets import TweetsMixin

_UNSET = object()


class DetailsMixin(TweetsMixin):
    def update_tweet_object_enrichment(
        self,
        tweet_id: str,
        *,
        enrichment_state: str,
        enrichment_checked_at: str | None,
        enrichment_http_status: int | None,
        enrichment_reason: str | None,
        enrichment_detail: str | None | object = _UNSET,
        enrichment_retry_count: int | None | object = _UNSET,
        enrichment_next_retry_at: str | None | object = _UNSET,
        enrichment_first_unavailable_at: str | None | object = _UNSET,
        enrichment_retry_eligible: bool | int | None | object = _UNSET,
        cursor: PageBuffer | None = None,
    ) -> None:
        changes = {
            "enrichment_state": enrichment_state,
            "enrichment_checked_at": enrichment_checked_at,
            "enrichment_http_status": enrichment_http_status,
            "enrichment_reason": enrichment_reason,
            "updated_at": utc_now(),
        }
        optional_updates = {
            "enrichment_detail": enrichment_detail,
            "enrichment_retry_count": enrichment_retry_count,
            "enrichment_next_retry_at": enrichment_next_retry_at,
            "enrichment_first_unavailable_at": enrichment_first_unavailable_at,
            "enrichment_retry_eligible": enrichment_retry_eligible,
        }
        for field_name, value in optional_updates.items():
            if value is _UNSET:
                continue
            if field_name == "enrichment_retry_eligible" and value is not None:
                value = int(bool(value))
            changes[field_name] = value
        row_key = self._row_key_for_tweet_object(tweet_id)
        if cursor is None:
            with self.transaction():
                result = self.conn.execute(
                    f"UPDATE archive SET {', '.join(f'{field} = ?' for field in changes)} "
                    "WHERE row_key = ?",
                    (*changes.values(), row_key),
                )
                if not result.rowcount:
                    raise KeyError(f"Tweet object row not found: {tweet_id}")
            return
        row = self._lookup_row(row_key, cursor=cursor)
        if row is None:
            raise KeyError(f"Tweet object row not found: {tweet_id}")
        updated = {**row, **changes}
        self._queue_record(updated, cursor=cursor)

    def _refresh_tweet_records_for_detail(
        self,
        tweet: TimelineTweet,
        *,
        cursor: PageBuffer,
        source: str = LIVE_SOURCE,
    ) -> None:
        legacy = tweet.raw_json.get("legacy") or {}
        rows = self._rows_for_values(RecordType.TWEET, "tweet_id", [tweet.tweet_id])
        now = utc_now()
        for row in rows:
            updated = dict(row)
            existing_source = self._normalized_source(row.get("source"))
            if source != existing_source and SOURCE_RANK.get(source, 0) < SOURCE_RANK.get(
                existing_source, 0
            ):
                # A lower-ranked lookup only proves the tweet is back; keep richer content.
                updated["deleted_at"] = None
                updated["last_seen_at"] = now
                if row.get("enrichment_state") == "terminal_unavailable":
                    updated["enrichment_state"] = "resurrected"
                cursor.records[updated["row_key"]] = updated
                continue
            updated["text"] = self._coalesce_value(tweet.text, row.get("text"))
            updated["author_id"] = self._coalesce_value(tweet.author_id, row.get("author_id"))
            updated["author_username"] = self._coalesce_value(
                tweet.author_username,
                row.get("author_username"),
            )
            updated["author_display_name"] = self._coalesce_value(
                tweet.author_display_name,
                row.get("author_display_name"),
            )
            updated["created_at"] = self._coalesce_value(tweet.created_at, row.get("created_at"))
            updated["created_at_ts"] = created_at_timestamp(updated["created_at"])
            updated["conversation_id"] = self._coalesce_value(
                legacy.get("conversation_id_str"),
                row.get("conversation_id"),
            )
            updated["lang"] = self._coalesce_value(legacy.get("lang"), row.get("lang"))
            updated["note_tweet_text"] = self._coalesce_value(
                extract_note_tweet_text(tweet.raw_json),
                row.get("note_tweet_text"),
            )
            updated["source"] = source
            updated["deleted_at"] = None
            updated["raw_json"] = self._json_value(tweet.raw_json)
            updated["last_seen_at"] = now
            updated["synced_at"] = now

            if row.get("enrichment_state") == "terminal_unavailable":
                updated["enrichment_state"] = "resurrected"

            cursor.records[updated["row_key"]] = updated

    def _refresh_tweet_records_for_details(
        self,
        tweets: list[TimelineTweet],
        *,
        cursor: PageBuffer,
        source: str = LIVE_SOURCE,
    ) -> None:
        for tweet in tweets:
            self._refresh_tweet_records_for_detail(tweet, cursor=cursor, source=source)

    def persist_tweet_detail(
        self,
        *,
        tweet: TimelineTweet,
        raw_json: dict[str, Any],
        http_status: int = 200,
        cursor: PageBuffer | None = None,
    ) -> None:
        owns_buffer = cursor is None
        buffer = cursor or PageBuffer()
        existing = self._lookup_row(self._row_key_for_tweet_object(tweet.tweet_id), cursor=buffer)
        prior_state = existing.get("enrichment_state") if existing is not None else None
        self.append_raw_capture(
            "TweetDetail",
            tweet.tweet_id,
            None,
            http_status,
            raw_json,
            source=LIVE_SOURCE,
            cursor=buffer,
        )
        self._refresh_tweet_records_for_details([tweet], cursor=buffer)

        self._buffer_secondary_graph(
            extract_secondary_objects(tweet.raw_json),
            source=LIVE_SOURCE,
            cursor=buffer,
        )

        self.update_tweet_object_enrichment(
            tweet.tweet_id,
            enrichment_state=(
                "resurrected" if prior_state in {"terminal_unavailable", "resurrected"} else "done"
            ),
            enrichment_checked_at=utc_now(),
            enrichment_http_status=http_status,
            enrichment_reason=None,
            enrichment_detail=None,
            enrichment_retry_count=0,
            enrichment_next_retry_at=None,
            enrichment_first_unavailable_at=None,
            enrichment_retry_eligible=False,
            cursor=buffer,
        )
        if owns_buffer:
            self._merge_records(list(buffer.records.values()))

    def persist_thread_detail(
        self,
        *,
        focal_tweet_id: str,
        tweets: list[TimelineTweet],
        raw_json: dict[str, Any],
        http_status: int = 200,
        cursor: PageBuffer | None = None,
    ) -> None:
        owns_buffer = cursor is None
        buffer = cursor or PageBuffer()
        self._refresh_tweet_records_for_details(tweets, cursor=buffer)
        self._buffer_secondary_graph(
            extract_thread_objects([tweet.raw_json for tweet in tweets]),
            source=LIVE_SOURCE,
            cursor=buffer,
        )
        # Write the completion marker after the canonical rows. A resurrected
        # object whose enrichment timestamp is newer than its last marker needs
        # one fresh expansion; a marker written here proves that refresh happened.
        self.append_raw_capture(
            "ThreadExpandDetail",
            focal_tweet_id,
            None,
            http_status,
            raw_json,
            source=LIVE_SOURCE,
            cursor=buffer,
        )
        if owns_buffer:
            self._merge_records(list(buffer.records.values()))

    def persist_unavailable_tweet(
        self,
        *,
        tweet_id: str,
        operation: str,
        raw_json: dict[str, Any],
        http_status: int | None,
        reason: str,
        detail: str | None,
        retry_eligible: bool,
        next_retry_at: str | None,
        retry_count: int | None = None,
        checked_at: str | None = None,
        capture_source: str = LIVE_SOURCE,
        cursor: PageBuffer | None = None,
    ) -> None:
        """Record an unavailable post. ``capture_source`` names the provider that answered,
        which is a different fact from the provenance of the saved body."""
        owns_buffer = cursor is None
        buffer = cursor or PageBuffer()
        checked_at = checked_at or utc_now()
        self.append_raw_capture(
            operation,
            tweet_id,
            None,
            http_status or 0,
            raw_json,
            source=capture_source,
            cursor=buffer,
        )
        row_key = self._row_key_for_tweet_object(tweet_id)
        existing = self._lookup_row(row_key, cursor=buffer)
        if existing is None:
            existing = self._record(
                record_type=RecordType.TWEET_OBJECT,
                row_key=row_key,
                tweet_id=tweet_id,
                source=LIVE_SOURCE,
                raw_json=self._json_value(raw_json),
                first_seen_at=checked_at,
                added_at=checked_at,
            )
        updated = dict(existing)
        current_retry_count = int(updated.get("enrichment_retry_count") or 0)
        updated.update(
            {
                "enrichment_state": "terminal_unavailable",
                "enrichment_checked_at": checked_at,
                "enrichment_http_status": http_status,
                "enrichment_reason": reason,
                "enrichment_detail": detail,
                "enrichment_retry_count": (
                    current_retry_count if retry_count is None else retry_count
                ),
                "enrichment_next_retry_at": next_retry_at,
                "enrichment_first_unavailable_at": (
                    updated.get("enrichment_first_unavailable_at") or checked_at
                ),
                "enrichment_retry_eligible": int(retry_eligible),
                "updated_at": checked_at,
            }
        )
        self._queue_record(updated, cursor=buffer)
        if owns_buffer:
            self._merge_records(list(buffer.records.values()))

    def persist_fx_status(
        self,
        *,
        tweet: TimelineTweet,
        payload: Any,
        http_status: int = 200,
        cursor: PageBuffer | None = None,
    ) -> None:
        """Store an FxTwitter lookup as a placeholder that live GraphQL data later replaces."""

        owns_buffer = cursor is None
        buffer = cursor or PageBuffer()
        existing = self._lookup_row(self._row_key_for_tweet_object(tweet.tweet_id), cursor=buffer)
        prior_state = existing.get("enrichment_state") if existing is not None else None
        self.append_raw_capture(
            "FxTwitterStatus",
            tweet.tweet_id,
            None,
            http_status,
            payload,
            source=FX_SOURCE,
            cursor=buffer,
        )
        self._refresh_tweet_records_for_details([tweet], cursor=buffer, source=FX_SOURCE)
        self._buffer_secondary_graph(
            extract_secondary_objects(tweet.raw_json),
            source=FX_SOURCE,
            cursor=buffer,
        )
        self.update_tweet_object_enrichment(
            tweet.tweet_id,
            enrichment_state=(
                "resurrected" if prior_state in {"terminal_unavailable", "resurrected"} else "done"
            ),
            enrichment_checked_at=utc_now(),
            enrichment_http_status=http_status,
            enrichment_reason=None,
            enrichment_detail=None,
            enrichment_retry_count=0,
            enrichment_next_retry_at=None,
            enrichment_first_unavailable_at=None,
            enrichment_retry_eligible=False,
            cursor=buffer,
        )
        if owns_buffer:
            self._merge_records(list(buffer.records.values()))
