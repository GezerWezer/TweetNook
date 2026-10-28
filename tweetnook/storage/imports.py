"""Official-archive import cleanup and rehydrating rows from raw captures."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from tweetnook.core.extractor import (
    extract_author_fields,
    extract_canonical_text,
    extract_note_tweet_text,
    extract_secondary_objects,
    extract_thread_objects,
)
from tweetnook.core.timeline_parsing import parse_tweet_detail_tweets

from .common import FX_SOURCE, LIVE_SOURCE
from .objects import ObjectsMixin
from .records import PageBuffer


@dataclass(slots=True)
class RehydrateResult:
    tweets_updated: int = 0
    secondary_records: int = 0


SECONDARY_RECORD_TYPES = ("tweet_object", "tweet_relation", "media", "url", "url_ref", "article")


class ImportsMixin(ObjectsMixin):
    def list_archive_import_media_paths(self) -> list[str]:
        rows = self._query(
            expr="record_type = 'media' AND provenance_source = 'x_archive'",
            cols=["local_path", "thumbnail_local_path"],
        )
        relative_paths = {
            path
            for row in rows
            for path in (row.get("local_path"), row.get("thumbnail_local_path"))
            if isinstance(path, str) and path
        }
        return sorted(relative_paths)

    def clear_archive_import_data(self) -> dict[str, int]:
        deletions: dict[str, int] = {}
        filters = {
            "raw_captures": "record_type = 'raw_capture' AND source = 'x_archive'",
            "tweets": "record_type = 'tweet' AND source = 'x_archive'",
            "tweet_objects": "record_type = 'tweet_object' AND source = 'x_archive'",
            "tweet_relations": "record_type = 'tweet_relation' AND source = 'x_archive'",
            "media": "record_type = 'media' AND provenance_source = 'x_archive'",
            "urls": "record_type = 'url' AND source = 'x_archive'",
            "url_refs": "record_type = 'url_ref' AND source = 'x_archive'",
            "articles": "record_type = 'article' AND source = 'x_archive'",
            "import_manifests": "record_type = 'import_manifest'",
        }
        for key, expr in filters.items():
            count = self._count(expr)
            if count:
                self._delete(expr)
            deletions[key] = count
        return deletions

    def _flush_rehydrate_buffer(self, buffer: PageBuffer) -> int:
        if not buffer.records:
            return 0
        secondary_records = sum(
            1
            for record in buffer.records.values()
            if record["record_type"] in SECONDARY_RECORD_TYPES
        )
        self._merge_records(list(buffer.records.values()))
        buffer.records.clear()
        buffer.existing_rows.clear()
        return secondary_records

    def rehydrate_from_raw_json(
        self, *, progress: Callable[[int], None] | None = None
    ) -> RehydrateResult:
        """Rebuild normalized tweet fields and secondary rows from stored raw_json."""
        rows = self._query(expr="record_type = 'tweet'")
        if not rows:
            return RehydrateResult()
        buffer = PageBuffer()
        batch_size = 500
        result = RehydrateResult()
        for row in rows:
            raw = json.loads(row["raw_json"])
            legacy = raw.get("legacy") or {}
            author_id, username, display_name = extract_author_fields(raw)
            canonical_text = extract_canonical_text(raw)
            updated_row = dict(row)
            changed = False
            desired_updates = {
                "text": self._coalesce_value(canonical_text, updated_row.get("text")),
                "author_id": self._coalesce_value(author_id, updated_row.get("author_id")),
                "author_username": self._coalesce_value(
                    username, updated_row.get("author_username")
                ),
                "author_display_name": self._coalesce_value(
                    display_name,
                    updated_row.get("author_display_name"),
                ),
                "created_at": self._coalesce_value(
                    legacy.get("created_at"), updated_row.get("created_at")
                ),
                "conversation_id": self._coalesce_value(
                    legacy.get("conversation_id_str"),
                    updated_row.get("conversation_id"),
                ),
                "lang": self._coalesce_value(legacy.get("lang"), updated_row.get("lang")),
                "source": updated_row.get("source") or LIVE_SOURCE,
            }
            desired_updates["note_tweet_text"] = self._coalesce_value(
                extract_note_tweet_text(raw),
                updated_row.get("note_tweet_text"),
            )
            for key, value in desired_updates.items():
                if updated_row.get(key) != value:
                    updated_row[key] = value
                    changed = True
            if changed:
                buffer.records[updated_row["row_key"]] = updated_row
                result.tweets_updated += 1
            self._buffer_secondary_graph(
                extract_secondary_objects(raw),
                source=self._normalized_source(row.get("source")),
                cursor=buffer,
            )
            if progress:
                progress(1)
            if len(buffer.records) >= batch_size:
                result.secondary_records += self._flush_rehydrate_buffer(buffer)
        detail_rows = self._query(
            expr="record_type = 'raw_capture' "
            "AND (operation = 'TweetDetail' OR operation = 'ThreadExpandDetail')"
        )
        detail_rows.sort(key=lambda row: (row.get("captured_at") or "", row.get("cursor_in") or ""))
        for row in detail_rows:
            raw_json = row.get("raw_json")
            if not isinstance(raw_json, str) or not raw_json:
                continue
            detail_payload = json.loads(raw_json)
            detail_tweets = parse_tweet_detail_tweets(detail_payload)
            if not detail_tweets:
                continue
            self._buffer_secondary_graph(
                extract_thread_objects([tweet.raw_json for tweet in detail_tweets]),
                source=self._normalized_source(row.get("source")),
                cursor=buffer,
            )
            if len(buffer.records) >= batch_size:
                result.secondary_records += self._flush_rehydrate_buffer(buffer)
        from tweetnook.core.fxtwitter_payloads import fx_status_to_graphql

        fx_rows = self._query(
            expr="record_type = 'raw_capture' AND operation = 'FxTwitterStatus'",
            cols=["raw_json", "captured_at", "cursor_in"],
        )
        fx_rows.sort(key=lambda row: (row.get("captured_at") or "", row.get("cursor_in") or ""))
        for row in fx_rows:
            raw_json = row.get("raw_json")
            payload = json.loads(raw_json) if isinstance(raw_json, str) and raw_json else None
            status = payload.get("status") if isinstance(payload, dict) else None
            if not isinstance(status, dict) or status.get("type") != "status":
                continue
            # Lower-ranked than live captures, so this only fills gaps they left; whether
            # the tweet is still available is up to later checks, not this old copy.
            self._buffer_secondary_graph(
                extract_secondary_objects(fx_status_to_graphql(status)),
                source=FX_SOURCE,
                preserve_lifecycle=True,
                cursor=buffer,
            )
            if len(buffer.records) >= batch_size:
                result.secondary_records += self._flush_rehydrate_buffer(buffer)
        result.secondary_records += self._flush_rehydrate_buffer(buffer)
        return result
