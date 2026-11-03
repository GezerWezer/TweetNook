"""Archive row construction, page buffering and source-precedence merging."""

from __future__ import annotations

import json
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from tweetnook.core import fastjson
from tweetnook.core.extractor import (
    are_different_articles,
    link_card_lacks_image,
    merge_article_payloads,
    restore_link_card_image,
    retain_rich_features,
)
from tweetnook.core.utils import utc_now

from .common import AVAILABLE_SOURCES, LIVE_SOURCE, SOURCE_RANK, clause_in, folder_key
from .db import ConnectionMixin
from .record_types import RecordType
from .schema import ARCHIVE_COLUMNS

_RETAINED_FEATURE_MARKERS = ('"image_value"', '"note_tweet"', '"article')


@dataclass(slots=True)
class PageBuffer:
    records: dict[str, dict[str, Any]] = field(default_factory=dict)
    existing_rows: dict[str, dict[str, Any] | None] = field(default_factory=dict)
    # tweet id -> numeric media id -> row key, including rows queued in this batch.
    media_rows: dict[str, dict[str, str]] = field(default_factory=dict)

    def checkpoint(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, str]]]:
        return (
            self.records.copy(),
            self.existing_rows.copy(),
            {tweet_id: rows.copy() for tweet_id, rows in self.media_rows.items()},
        )

    def restore(
        self, checkpoint: tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, str]]]
    ) -> None:
        records, existing_rows, media_rows = checkpoint
        self.clear()
        self.records.update(records)
        self.existing_rows.update(existing_rows)
        self.media_rows.update(media_rows)

    def clear(self) -> None:
        self.records.clear()
        self.existing_rows.clear()
        self.media_rows.clear()


@dataclass(slots=True)
class RecordContext:
    existing: dict[str, Any] | None
    now: str
    first_seen_at: str
    added_at: str


class RecordsMixin(ConnectionMixin):
    def _record(self, **overrides: Any) -> dict[str, Any]:
        record = {field: None for field in ARCHIVE_COLUMNS}
        record.update(overrides)
        return record

    def _coalesce_value(self, *values: Any) -> Any:
        for value in values:
            if value is None:
                continue
            if isinstance(value, str) and not value:
                continue
            return value
        return None

    def _parse_bool(self, val: Any) -> bool:
        if val is None:
            return False
        if isinstance(val, bool):
            return val
        if isinstance(val, int | float):
            return bool(val)
        if isinstance(val, str):
            return val.lower() in ("1", "true", "yes", "t", "y")
        return bool(val)

    def _json_value(self, value: Any) -> str | None:
        if value is None:
            return None
        return json.dumps(value, sort_keys=True)

    def _merge_records(self, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        cols = list(ARCHIVE_COLUMNS)
        placeholders = ", ".join(["?"] * len(cols))
        col_names = ", ".join(cols)
        updates = ", ".join(f"{col} = excluded.{col}" for col in cols if col != "row_key")
        sql = (
            f"INSERT INTO archive ({col_names}) VALUES ({placeholders}) "
            f"ON CONFLICT(row_key) DO UPDATE SET {updates}"
        )
        params = []
        for record in records:
            row = []
            for col in cols:
                row.append(record.get(col))
            params.append(row)
        with self.transaction():
            self.conn.executemany(sql, params)

    def merge_rows(self, rows: list[dict[str, Any]]) -> None:
        """Replace complete archive records; omitted columns become NULL."""
        self._merge_records(rows)

    def flush(self, buffer: PageBuffer) -> int:
        """Commit a batch without an enclosing transaction, then clear it."""
        if not buffer.records:
            return 0
        pending = len(buffer.records)
        with self.transaction(allow_nested=False):
            self._merge_records(list(buffer.records.values()))
        buffer.clear()
        return pending

    def _row_key_for_tweet(
        self, tweet_id: str, collection_type: str, folder_id: str | None = None
    ) -> str:
        return f"tweet:{collection_type}:{folder_key(folder_id)}:{tweet_id}"

    def _row_key_for_sync_state(self, collection_type: str, folder_id: str | None = None) -> str:
        return f"sync_state:{collection_type}:{folder_key(folder_id)}"

    def _row_key_for_metadata(self, key: str) -> str:
        return f"metadata:{key}"

    def _row_key_for_import_manifest(self, archive_digest: str) -> str:
        return f"import_manifest:{archive_digest}"

    def _row_key_for_tweet_object(self, tweet_id: str) -> str:
        return f"tweet_object:{tweet_id}"

    def _row_key_for_tweet_relation(
        self, source_tweet_id: str, relation_type: str, target_tweet_id: str
    ) -> str:
        return f"tweet_relation:{source_tweet_id}:{relation_type}:{target_tweet_id}"

    def _row_key_for_media(self, tweet_id: str, media_key: str) -> str:
        return f"media:{tweet_id}:{media_key}"

    def _row_key_for_url(self, url_hash: str) -> str:
        return f"url:{url_hash}"

    def _row_key_for_url_ref(self, tweet_id: str, position: int) -> str:
        return f"url_ref:{tweet_id}:{position}"

    def _row_key_for_article(self, tweet_id: str) -> str:
        return f"article:{tweet_id}"

    def _get_row(self, row_key: str) -> dict[str, Any] | None:
        rows = self._query("row_key = ?", (row_key,), limit=1)
        return rows[0] if rows else None

    def _rows_for_values(
        self,
        record_type: RecordType,
        field_name: str,
        values: set[str] | list[str] | tuple[str, ...],
        *,
        columns: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if field_name not in ARCHIVE_COLUMNS:
            raise ValueError(f"Unknown archive field: {field_name}")
        unique_values = [value for value in dict.fromkeys(values) if value]
        if not unique_values:
            return []
        if record_type == RecordType.URL and field_name == "url_hash":
            unique_values = [self._row_key_for_url(value) for value in unique_values]
            field_name = "row_key"
        rows: list[dict[str, Any]] = []
        chunk_size = 100
        for start in range(0, len(unique_values), chunk_size):
            chunk = unique_values[start : start + chunk_size]
            value_index = {
                "tweet_id": "idx_archive_tweet_id",
                "target_tweet_id": "idx_archive_target_tweet_id",
                "row_key": "sqlite_autoindex_archive_1",
            }.get(field_name)
            source = f"archive INDEXED BY {value_index}" if value_index else "archive"
            selected = ", ".join(columns) if columns else "*"
            placeholders = ", ".join("?" for _value in chunk)
            sql = (
                f"SELECT {selected} FROM {source} WHERE record_type = ? "
                f"AND {field_name} IN ({placeholders})"
            )
            rows.extend(dict(row) for row in self.conn.execute(sql, (record_type, *chunk)))
        return rows

    def _lookup_row(
        self, row_key: str, *, cursor: PageBuffer | None = None
    ) -> dict[str, Any] | None:
        if cursor is None:
            return self._get_row(row_key)
        if row_key in cursor.records:
            return cursor.records[row_key]
        if row_key not in cursor.existing_rows:
            cursor.existing_rows[row_key] = self._get_row(row_key)
        return cursor.existing_rows[row_key]

    def prefetch_rows(self, row_keys: list[str], *, cursor: PageBuffer) -> None:
        missing = [
            row_key
            for row_key in dict.fromkeys(row_keys)
            if row_key and row_key not in cursor.records and row_key not in cursor.existing_rows
        ]
        if not missing:
            return
        found: dict[str, dict[str, Any]] = {}
        chunk_size = 200
        for start in range(0, len(missing), chunk_size):
            chunk = missing[start : start + chunk_size]
            where = clause_in("row_key", set(chunk))
            for row in self._query(where.sql, where.params):
                row_key = row.get("row_key")
                if isinstance(row_key, str) and row_key:
                    found[row_key] = row
        for row_key in missing:
            cursor.existing_rows[row_key] = found.get(row_key)

    def _queue_record(self, record: dict[str, Any], *, cursor: PageBuffer | None = None) -> None:
        if cursor is None:
            self._merge_records([record])
            return
        cursor.records[record["row_key"]] = record

    def _row_timestamps(
        self, row_key: str, *, cursor: PageBuffer | None = None, now: str | None = None
    ) -> tuple[dict[str, Any] | None, str, str]:
        existing = self._lookup_row(row_key, cursor=cursor)
        stamp = now or utc_now()
        first_seen_at = existing["first_seen_at"] if existing else stamp
        added_at = existing["added_at"] if existing else stamp
        return existing, first_seen_at, added_at

    def _record_context(
        self,
        row_key: str,
        *,
        cursor: PageBuffer | None = None,
    ) -> RecordContext:
        now = utc_now()
        existing, first_seen_at, added_at = self._row_timestamps(row_key, cursor=cursor, now=now)
        return RecordContext(
            existing=existing,
            now=now,
            first_seen_at=first_seen_at,
            added_at=added_at,
        )

    @staticmethod
    def _existing_value(context: RecordContext, field_name: str) -> Any:
        if context.existing is None:
            return None
        return context.existing.get(field_name)

    def _record_with_context(
        self,
        context: RecordContext,
        **overrides: Any,
    ) -> dict[str, Any]:
        return self._record(
            **overrides,
            first_seen_at=context.first_seen_at,
            last_seen_at=context.now,
            added_at=context.added_at,
            synced_at=context.now,
        )

    def _normalized_source(self, value: str | None) -> str:
        return value or LIVE_SOURCE

    def _prefer_incoming_source(
        self,
        context: RecordContext,
        incoming_source: str,
        *,
        source_field: str = "source",
        deleted_at: str | None = None,
    ) -> bool:
        if context.existing is None:
            return True
        existing_source = self._normalized_source(self._existing_value(context, source_field))
        if incoming_source == existing_source:
            return True
        return SOURCE_RANK.get(incoming_source, 0) > SOURCE_RANK.get(existing_source, 0)

    def _merge_by_source_precedence(
        self,
        context: RecordContext,
        field_name: str,
        incoming: Any,
        *,
        prefer_incoming: bool,
    ) -> Any:
        existing = self._existing_value(context, field_name)
        if prefer_incoming:
            return self._coalesce_value(incoming, existing)
        return self._coalesce_value(existing, incoming)

    def _merged_tweet_text(
        self,
        context: RecordContext,
        text: str | None,
        note_tweet_text: str | None,
        *,
        prefer_incoming: bool,
    ) -> tuple[str | None, str | None]:
        """A post's text and note as one choice: the note that survives supplies the text."""
        note = self._merge_by_source_precedence(
            context, "note_tweet_text", note_tweet_text, prefer_incoming=prefer_incoming
        )
        return note or self._merge_by_source_precedence(
            context, "text", text, prefer_incoming=prefer_incoming
        ), note

    def _merged_tweet_raw_json(
        self,
        context: RecordContext,
        raw: Any,
        *,
        prefer_incoming: bool,
        fallback_key: str | None = None,
        cursor: PageBuffer | None = None,
    ) -> Any:
        incoming = self._json_value(raw)
        merged = self._merge_by_source_precedence(
            context, "raw_json", incoming, prefer_incoming=prefer_incoming
        )
        if not isinstance(merged, str):
            return merged
        # Whichever copy wins, keep what the losing copy has and it lacks: a note, an Article's
        # state, and the image bindings timelines sometimes drop from a summary card. Collection
        # rows can also take card images from the shared tweet object, but not its note, which
        # would disagree with the note column these rows merge separately.
        loser = self._existing_value(context, "raw_json") if merged is incoming else incoming
        fallback = None
        if fallback_key is not None and '"card"' in merged:
            fallback_row = self._lookup_row(fallback_key, cursor=cursor)
            fallback = fallback_row.get("raw_json") if fallback_row else None
        donor = (
            loser
            if isinstance(loser, str)
            and loser != merged
            and any(marker in loser for marker in _RETAINED_FEATURE_MARKERS)
            else None
        )
        card_fallback = (
            fallback if isinstance(fallback, str) and '"image_value"' in fallback else None
        )
        if donor is None and card_fallback is None:
            return merged
        try:
            winner = fastjson.loads(merged)
        except (TypeError, ValueError):
            return merged
        if not isinstance(winner, dict):
            return merged
        changed = False
        if donor is not None:
            with suppress(TypeError, ValueError):
                changed = retain_rich_features(winner, fastjson.loads(donor))
        if card_fallback is not None and link_card_lacks_image(winner):
            with suppress(TypeError, ValueError):
                changed = restore_link_card_image(winner, fastjson.loads(card_fallback)) or changed
        return self._json_value(winner) if changed else merged

    def _names_another_article(self, context: RecordContext, raw: Any) -> bool:
        """Compares the payloads' own ids, as the id column falls back to the tweet's."""
        saved = self._existing_value(context, "raw_json")
        if not isinstance(raw, dict) or not isinstance(saved, str):
            return False
        try:
            payload = fastjson.loads(saved)
        except (TypeError, ValueError):
            return False
        return isinstance(payload, dict) and are_different_articles(raw, payload)

    def _merged_article_raw_json(
        self, context: RecordContext, raw: Any, *, prefer_incoming: bool
    ) -> Any:
        """Keep an Article's structured state beside the body text that survives from it."""
        incoming = self._json_value(raw)
        merged = self._merge_by_source_precedence(
            context, "raw_json", incoming, prefer_incoming=prefer_incoming
        )
        loser = self._existing_value(context, "raw_json") if merged is incoming else incoming
        if not isinstance(merged, str) or not isinstance(loser, str) or loser == merged:
            return merged
        try:
            winner, donor = fastjson.loads(merged), fastjson.loads(loser)
        except (TypeError, ValueError):
            return merged
        if not isinstance(winner, dict):
            return merged
        combined = merge_article_payloads(winner, donor)
        return merged if combined is winner else self._json_value(combined)

    def _merged_source_value(
        self,
        context: RecordContext,
        incoming_source: str,
        *,
        prefer_incoming: bool,
        field_name: str = "source",
    ) -> str:
        if prefer_incoming:
            return incoming_source
        return self._normalized_source(self._existing_value(context, field_name))

    def _merged_deleted_at(
        self,
        context: RecordContext,
        incoming_deleted_at: str | None,
        *,
        prefer_incoming: bool,
        incoming_source: str,
    ) -> str | None:
        if prefer_incoming and incoming_source in AVAILABLE_SOURCES:
            return incoming_deleted_at
        return self._coalesce_value(
            incoming_deleted_at, self._existing_value(context, "deleted_at")
        )
