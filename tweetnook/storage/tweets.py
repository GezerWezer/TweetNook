"""Timeline page writes: raw captures, collection memberships and sync cursors together."""

from __future__ import annotations

import json
import uuid
from typing import Any

from tweetnook.core.extractor import extract_note_tweet_text
from tweetnook.core.timeline_parsing import TimelineTweet
from tweetnook.core.utils import utc_now

from .common import LIVE_SOURCE, folder_key, parse_created_at
from .objects import ObjectsMixin
from .records import PageBuffer
from .sync_state import SyncStateMixin


class TweetsMixin(SyncStateMixin, ObjectsMixin):
    def _capture_record(
        self,
        operation: str,
        cursor_in: str | None,
        cursor_out: str | None,
        http_status: int,
        raw_json: Any,
        *,
        source: str,
        capture_key: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        capture_id = capture_key or str(uuid.uuid4())
        return capture_id, self._record(
            row_key=f"raw_capture:{capture_id}",
            record_type="raw_capture",
            operation=operation,
            cursor_in=cursor_in,
            cursor_out=cursor_out,
            captured_at=utc_now(),
            http_status=http_status,
            source=source,
            raw_json=json.dumps(raw_json, sort_keys=True),
        )

    def append_raw_capture(
        self,
        operation: str,
        cursor_in: str | None,
        cursor_out: str | None,
        http_status: int,
        raw_json: Any,
        *,
        source: str = LIVE_SOURCE,
        capture_key: str | None = None,
        cursor: PageBuffer | None = None,
    ) -> str:
        capture_id, record = self._capture_record(
            operation,
            cursor_in,
            cursor_out,
            http_status,
            raw_json,
            source=source,
            capture_key=capture_key,
        )
        self._queue_record(record, cursor=cursor)
        return capture_id

    def upsert_tweet(self, tweet: TimelineTweet, *, cursor: PageBuffer | None = None) -> None:
        if cursor is None:
            raise RuntimeError(
                "ArchiveStore.upsert_tweet() is only supported inside page buffering "
                "for the SQLite backend."
            )
        cursor.pending_tweets[tweet.tweet_id] = tweet

    def _tweet_record(
        self,
        tweet: TimelineTweet,
        collection_type: str,
        *,
        source: str = LIVE_SOURCE,
        deleted_at: str | None = None,
        sort_index: str | None = None,
        folder_id: str | None = None,
        cursor: PageBuffer | None = None,
    ) -> dict[str, Any]:
        row_key = self._row_key_for_tweet(tweet.tweet_id, collection_type, folder_id)
        legacy = tweet.raw_json.get("legacy") or {}
        context = self._record_context(row_key, cursor=cursor)
        prefer_incoming = self._prefer_incoming_source(
            context,
            source,
            deleted_at=deleted_at,
        )
        return self._record_with_context(
            context,
            row_key=row_key,
            record_type="tweet",
            tweet_id=tweet.tweet_id,
            collection_type=collection_type,
            folder_id=folder_key(folder_id),
            sort_index=self._merge_by_source_precedence(
                context, "sort_index", sort_index, prefer_incoming=prefer_incoming
            ),
            source=self._merged_source_value(context, source, prefer_incoming=prefer_incoming),
            text=self._merge_by_source_precedence(
                context,
                "text",
                tweet.text,
                prefer_incoming=prefer_incoming,
            ),
            author_id=self._merge_by_source_precedence(
                context,
                "author_id",
                tweet.author_id,
                prefer_incoming=prefer_incoming,
            ),
            author_username=self._merge_by_source_precedence(
                context,
                "author_username",
                tweet.author_username,
                prefer_incoming=prefer_incoming,
            ),
            author_display_name=self._merge_by_source_precedence(
                context,
                "author_display_name",
                tweet.author_display_name,
                prefer_incoming=prefer_incoming,
            ),
            created_at=(
                merged_created_at := self._merge_by_source_precedence(
                    context,
                    "created_at",
                    tweet.created_at,
                    prefer_incoming=prefer_incoming,
                )
            ),
            created_at_ts=int(parse_created_at(merged_created_at).timestamp())
            if parse_created_at(merged_created_at)
            else None,
            deleted_at=self._merged_deleted_at(
                context,
                deleted_at,
                prefer_incoming=prefer_incoming,
                incoming_source=source,
            ),
            conversation_id=self._merge_by_source_precedence(
                context,
                "conversation_id",
                legacy.get("conversation_id_str"),
                prefer_incoming=prefer_incoming,
            ),
            lang=self._merge_by_source_precedence(
                context,
                "lang",
                legacy.get("lang"),
                prefer_incoming=prefer_incoming,
            ),
            note_tweet_text=self._merge_by_source_precedence(
                context,
                "note_tweet_text",
                extract_note_tweet_text(tweet.raw_json),
                prefer_incoming=prefer_incoming,
            ),
            raw_json=self._merged_tweet_raw_json(
                context,
                tweet.raw_json,
                prefer_incoming=prefer_incoming,
                fallback_key=self._row_key_for_tweet_object(tweet.tweet_id),
                cursor=cursor,
            ),
        )

    def upsert_membership(
        self,
        tweet_id: str,
        collection_type: str,
        *,
        source: str = LIVE_SOURCE,
        deleted_at: str | None = None,
        sort_index: str | None = None,
        folder_id: str | None = None,
        cursor: PageBuffer | None = None,
    ) -> None:
        if cursor is None:
            raise RuntimeError(
                "ArchiveStore.upsert_membership() is only supported inside page buffering "
                "for the SQLite backend."
            )
        try:
            tweet = cursor.pending_tweets[tweet_id]
        except KeyError as exc:
            raise RuntimeError(
                f"Missing pending tweet {tweet_id} for collection {collection_type}."
            ) from exc
        self._queue_record(
            self._tweet_record(
                tweet,
                collection_type,
                source=source,
                deleted_at=deleted_at,
                sort_index=sort_index,
                folder_id=folder_id,
                cursor=cursor,
            ),
            cursor=cursor,
        )

    def persist_page(
        self,
        *,
        operation: str,
        collection_type: str,
        cursor_in: str | None,
        cursor_out: str | None,
        http_status: int,
        raw_json: dict[str, Any],
        tweets: list[TimelineTweet],
        last_head_tweet_id: str | None,
        backfill_cursor: str | None,
        backfill_incomplete: bool,
    ) -> None:
        buffer = PageBuffer()
        self.append_raw_capture(
            operation,
            cursor_in,
            cursor_out,
            http_status,
            raw_json,
            source=LIVE_SOURCE,
            cursor=buffer,
        )
        for tweet in tweets:
            self.upsert_tweet(tweet, cursor=buffer)
            self.upsert_membership(
                tweet.tweet_id,
                collection_type,
                source=LIVE_SOURCE,
                sort_index=tweet.sort_index,
                cursor=buffer,
            )
        self._buffer_secondary_objects(tweets, source=LIVE_SOURCE, cursor=buffer)
        self.set_sync_state(
            collection_type,
            last_head_tweet_id=last_head_tweet_id,
            backfill_cursor=backfill_cursor,
            backfill_incomplete=backfill_incomplete,
            cursor=buffer,
        )
        self._merge_records(list(buffer.records.values()))
