"""Feed and export reads: pagination, like order and row hydration."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from tweetnook.core import fastjson
from tweetnook.core.media_metadata import aspect_ratio_for_media

from .common import (
    HIDDEN_TWEET_IDS_SQL,
    SEARCH_COLLECTION_ORDER,
    UNCAPTURED_TWEET_IDS_SQL,
)
from .record_types import RecordType
from .records import RecordsMixin
from .relations import RelationsMixin

if TYPE_CHECKING:
    from .like_order import LikeOrder


class ReadsMixin(RecordsMixin, RelationsMixin):
    def _serialize_media_row(
        self, row: dict[str, Any], raw_tweet: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        variants = fastjson.loads(row["variants_json"]) if row.get("variants_json") else []
        payload = {
            "media_key": row.get("media_key"),
            "type": row.get("media_type"),
            "source": row.get("source"),
            "article_id": row.get("article_id"),
            "position": row.get("position"),
            "url": row.get("media_url"),
            "thumbnail_url": row.get("thumbnail_url"),
            "width": row.get("width"),
            "height": row.get("height"),
            "duration_millis": row.get("duration_millis"),
            "variants": variants,
            "download": {
                "state": row.get("download_state") or "pending",
                "local_path": row.get("local_path"),
                "sha256": row.get("sha256"),
                "byte_size": row.get("byte_size"),
                "content_type": row.get("content_type"),
                "thumbnail_local_path": row.get("thumbnail_local_path"),
                "thumbnail_sha256": row.get("thumbnail_sha256"),
                "thumbnail_byte_size": row.get("thumbnail_byte_size"),
                "thumbnail_content_type": row.get("thumbnail_content_type"),
                "downloaded_at": row.get("downloaded_at"),
                "error": row.get("download_error"),
            },
        }
        aspect_ratio = aspect_ratio_for_media(raw_tweet, row.get("position"), row.get("media_type"))
        if aspect_ratio is not None:
            payload["aspect_ratio"] = aspect_ratio
        return payload

    def _serialize_article_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "article_id": row.get("article_id"),
            "title": row.get("title"),
            "summary_text": row.get("summary_text"),
            "content_text": row.get("content_text"),
            "canonical_url": row.get("canonical_url"),
            "published_at": row.get("published_at"),
            "status": row.get("status"),
        }

    def _serialize_url_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "url_hash": row.get("url_hash"),
            "canonical_url": row.get("canonical_url"),
            "expanded_url": row.get("expanded_url"),
            "final_url": row.get("final_url"),
            "host": row.get("url_host"),
            "title": row.get("title"),
            "description": row.get("description"),
            "site_name": row.get("site_name"),
            "content_type": row.get("content_type"),
            "http_status": row.get("http_status"),
            "unfurl_state": row.get("unfurl_state") or "pending",
            "last_fetched_at": row.get("last_fetched_at"),
            "error": row.get("download_error"),
        }

    def count_export_rows(self, collection: str) -> int:
        revision = self.cache_revision("membership")
        cached = self._count_cache.get(collection)
        if not self.conn.in_transaction and cached is not None and cached[0] == revision:
            return cached[1]
        sql = (
            "SELECT COUNT(DISTINCT tweet_id) FROM archive INDEXED BY idx_archive_membership "
            f"WHERE record_type = '{RecordType.TWEET}'"
        )
        params: tuple[str, ...] = ()
        if collection != "all":
            sql += " AND collection_type = ?"
            params = (collection,)
        count = int(self.conn.execute(sql, params).fetchone()[0])
        if not self.conn.in_transaction:
            if len(self._count_cache) >= 32:
                self._count_cache.clear()
            self._count_cache[collection] = (revision, count)
        else:
            self._count_cache.clear()
        return count

    def get_like_order(self) -> LikeOrder:
        """Return the derived order independently of tweet content/source updates."""
        from tweetnook.storage.like_order import load_like_order

        with self._like_order_lock:
            return load_like_order(self)

    def warm_like_order(self) -> None:
        """Rebuild the derived like sequence and its paging table before a request needs it."""
        with self._like_order_lock:
            self._ensure_like_order_table()

    def _ensure_like_order_table(self) -> None:
        """Index the immutable derived sequence once per input revision/connection.

        Call under _like_order_lock. A savepoint preserves any caller transaction;
        temporary rows never become part of the on-disk archive or backups.
        """
        order = self.get_like_order()
        if self._indexed_like_order is order and not self.conn.in_transaction:
            return
        self.conn.execute("SAVEPOINT like_order_index")
        try:
            self.conn.execute(
                "CREATE TEMP TABLE IF NOT EXISTS tweetnook_like_order "
                "(tweet_id TEXT PRIMARY KEY, latest_position INTEGER UNIQUE, "
                "earliest_position INTEGER UNIQUE)"
            )
            self.conn.execute("DELETE FROM temp.tweetnook_like_order")
            earliest = {
                tweet_id: i for i, tweet_id in enumerate(order.ordered_ids("liked_earliest"))
            }
            self.conn.executemany(
                "INSERT INTO temp.tweetnook_like_order VALUES (?, ?, ?)",
                ((tweet_id, i, earliest[tweet_id]) for i, tweet_id in enumerate(order.ids)),
            )
        except BaseException:
            self.conn.execute("ROLLBACK TO like_order_index")
            raise
        finally:
            self.conn.execute("RELEASE like_order_index")
        self._indexed_like_order = order if not self.conn.in_transaction else None

    def query_like_order_ids(
        self,
        filter_expr: str,
        params: Sequence[Any] = (),
        *,
        sort: str,
        limit: int,
        offset: int,
        after_position: int | None = None,
    ) -> list[dict[str, Any]]:
        """Filter before pagination without hydrating every liked tweet.

        filter_expr comes from the internal search planner, never directly from
        an HTTP parameter. Position ranges use the temporary sequence index.
        """
        position = "earliest_position" if sort == "liked_earliest" else "latest_position"
        with self._like_order_lock:
            self._ensure_like_order_table()
            rows = self.conn.execute(
                f"SELECT ordering.tweet_id, ordering.{position} AS like_position "
                "FROM temp.tweetnook_like_order AS ordering "
                f"WHERE ordering.{position} > ? AND EXISTS "
                "(SELECT 1 FROM archive INDEXED BY idx_archive_tweet_id "
                f"WHERE archive.tweet_id = ordering.tweet_id AND ({filter_expr})) "
                f"ORDER BY ordering.{position} LIMIT ? OFFSET ?",
                (-1 if after_position is None else after_position, *params, limit, offset),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_paginated_tweet_rows(
        self,
        collection: str,
        limit: int,
        offset: int,
        sort: str = "newest",
        *,
        after: dict[str, Any] | None = None,
        exclude_hidden: bool = False,
        exclude_uncaptured: bool = False,
    ) -> list[dict[str, Any]]:
        if sort not in {"newest", "oldest", "random"}:
            raise ValueError(f"Unsupported sort order: {sort}")
        if limit < 0:
            raise ValueError("limit must be non-negative")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        where = [f"candidate.record_type = '{RecordType.TWEET}'"]
        duplicate_where = [
            f"duplicate.record_type = '{RecordType.TWEET}'",
            "duplicate.tweet_id = candidate.tweet_id",
            "duplicate.row_key < candidate.row_key",
        ]
        params: list[Any] = []
        if collection != "all":
            where.append("candidate.collection_type = ?")
            duplicate_where.append("duplicate.collection_type = ?")
            params.extend([collection, collection])
        if exclude_hidden:
            where.append(f"candidate.tweet_id NOT IN ({HIDDEN_TWEET_IDS_SQL})")
        if exclude_uncaptured:
            where.append(f"candidate.tweet_id NOT IN ({UNCAPTURED_TWEET_IDS_SQL})")

        order_by = (
            "candidate.created_at_ts DESC, "
            "CAST(candidate.sort_index AS INTEGER) DESC, candidate.tweet_id DESC"
        )
        if sort == "oldest":
            order_by = (
                "candidate.created_at_ts ASC, "
                "CAST(candidate.sort_index AS INTEGER) ASC, candidate.tweet_id ASC"
            )
        elif sort == "random":
            order_by = "RANDOM()"

        sql = (
            "SELECT candidate.tweet_id, candidate.created_at_ts, "
            "CAST(candidate.sort_index AS INTEGER) AS sort_index FROM archive AS candidate "
            f"WHERE {' AND '.join(where)} "
            "AND NOT EXISTS ("
            "SELECT 1 FROM archive AS duplicate INDEXED BY idx_archive_membership "
            f"WHERE {' AND '.join(duplicate_where)}"
            ") "
        )
        if after is None:
            return [
                dict(row)
                for row in self.conn.execute(
                    f"{sql} ORDER BY {order_by} LIMIT ? OFFSET ?", [*params, limit, offset]
                )
                if row["tweet_id"]
            ]
        # Disjoint, ordered ranges preserve SQLite's NULL/tie semantics and let
        # every continuation seek into the composite index. A single OR across
        # nullable keys can instead scan all preceding pages.
        columns = [
            "candidate.created_at_ts",
            "CAST(candidate.sort_index AS INTEGER)",
            "candidate.tweet_id",
        ]
        values = [after["created_at_ts"], after["sort_index"], after["tweet_id"]]
        result = []
        direction = "DESC" if sort == "newest" else "ASC"
        for condition, bounds, order_start in self._page_cursor_ranges(
            columns, values, sort == "newest"
        ):
            range_order = ", ".join(f"{column} {direction}" for column in columns[order_start:])
            result.extend(
                dict(row)
                for row in self.conn.execute(
                    f"{sql} AND ({condition}) ORDER BY {range_order} LIMIT ?",
                    [*params, *bounds, limit - len(result)],
                )
                if row["tweet_id"]
            )
            if len(result) >= limit:
                break
        return result

    @staticmethod
    def _page_cursor_ranges(columns, values, descending):
        """Yield lexicographic continuation ranges in native SQLite NULL order."""
        for index in range(len(columns) - 1, -1, -1):
            prefix = [f"{column} IS ?" for column in columns[:index]]
            value = values[index]
            column = columns[index]
            if value is not None:
                comparison = "<" if descending else ">"
                yield (
                    " AND ".join([*prefix, f"{column} {comparison} ?"]),
                    [*values[:index], value],
                    index,
                )
                if descending and index < len(columns) - 1:
                    yield " AND ".join([*prefix, f"{column} IS NULL"]), values[:index], index + 1
            elif not descending:
                yield " AND ".join([*prefix, f"{column} IS NOT NULL"]), values[:index], index

    def _hydrate_exported_rows(
        self,
        sorted_rows: list[dict[str, Any]],
        include_raw_json: bool,
    ) -> list[dict[str, Any]]:
        tweet_ids = [row["tweet_id"] for row in sorted_rows if row.get("tweet_id")]

        media_cols = [
            "tweet_id",
            "media_key",
            "media_type",
            "source",
            "article_id",
            "position",
            "media_url",
            "thumbnail_url",
            "width",
            "height",
            "duration_millis",
            "variants_json",
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
        ]
        article_cols = [
            "tweet_id",
            "article_id",
            "title",
            "summary_text",
            "content_text",
            "canonical_url",
            "published_at",
            "status",
        ]
        url_ref_cols = [
            "tweet_id",
            "url_hash",
            "url",
            "expanded_url",
            "display_url",
            "canonical_url",
            "position",
        ]
        url_cols = [
            "url_hash",
            "canonical_url",
            "expanded_url",
            "final_url",
            "url_host",
            "title",
            "description",
            "site_name",
            "content_type",
            "http_status",
            "unfurl_state",
            "last_fetched_at",
            "download_error",
        ]

        media_rows = self._rows_for_values(
            RecordType.MEDIA, "tweet_id", tweet_ids, columns=media_cols
        )
        article_rows = self._rows_for_values(
            RecordType.ARTICLE, "tweet_id", tweet_ids, columns=article_cols
        )
        url_ref_rows = self._rows_for_values(
            RecordType.URL_REF, "tweet_id", tweet_ids, columns=url_ref_cols
        )
        url_hashes = [row["url_hash"] for row in url_ref_rows if row.get("url_hash")]
        url_rows = self._rows_for_values(RecordType.URL, "url_hash", url_hashes, columns=url_cols)

        media_by_tweet: dict[str, list[dict[str, Any]]] = {}
        raw_tweet_by_id: dict[str, dict[str, Any]] = {}
        for row in sorted_rows:
            raw_json = row.get("raw_json")
            if not isinstance(raw_json, str) or not raw_json:
                continue
            try:
                raw_tweet = fastjson.loads(raw_json)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(raw_tweet, dict) and row.get("tweet_id"):
                raw_tweet_by_id[row["tweet_id"]] = raw_tweet
        for row in sorted(
            media_rows,
            key=lambda item: (
                item.get("tweet_id") or "",
                item.get("position") if item.get("position") is not None else 1_000_000,
            ),
        ):
            media_by_tweet.setdefault(row["tweet_id"], []).append(
                self._serialize_media_row(row, raw_tweet_by_id.get(row["tweet_id"]))
            )

        articles_by_tweet = {
            row["tweet_id"]: self._serialize_article_row(row)
            for row in article_rows
            if row.get("tweet_id")
        }
        urls_by_hash = {
            row["url_hash"]: self._serialize_url_row(row) for row in url_rows if row.get("url_hash")
        }
        url_refs_by_tweet: dict[str, list[dict[str, Any]]] = {}
        for row in sorted(
            url_ref_rows,
            key=lambda item: (
                item.get("tweet_id") or "",
                item.get("position") if item.get("position") is not None else 1_000_000,
            ),
        ):
            resolved = urls_by_hash.get(row.get("url_hash"))
            url_refs_by_tweet.setdefault(row["tweet_id"], []).append(
                {
                    "position": row.get("position"),
                    "url_hash": row.get("url_hash"),
                    "short_url": row.get("url"),
                    "expanded_url": row.get("expanded_url"),
                    "display_url": row.get("display_url"),
                    "canonical_url": row.get("canonical_url"),
                    "resolved": resolved,
                }
            )

        quote_relations = self.list_quote_relation_rows(set(tweet_ids))
        quoted_tweet_by_source = {
            row["tweet_id"]: row["target_tweet_id"]
            for row in quote_relations
            if row.get("tweet_id") and row.get("target_tweet_id")
        }
        tag_tweet_ids = list(dict.fromkeys([*tweet_ids, *quoted_tweet_by_source.values()]))
        media_tag_rows = self._rows_for_values(
            RecordType.MEDIA_TAG, "tweet_id", tag_tweet_ids, columns=["tweet_id", "raw_json"]
        )
        tags_by_tweet: dict[str, dict[str, Any]] = {}
        for row in media_tag_rows:
            raw_tag_json = row.get("raw_json")
            if not isinstance(raw_tag_json, str) or not raw_tag_json:
                continue
            try:
                tag_payload = fastjson.loads(raw_tag_json)
            except json.JSONDecodeError:
                continue
            if isinstance(tag_payload, dict):
                tags_by_tweet[row["tweet_id"]] = tag_payload

        exported = []
        for row in sorted_rows:
            tweet_media = media_by_tweet.get(row["tweet_id"], [])
            article = articles_by_tweet.get(row["tweet_id"])
            if article is not None:
                article["media"] = [
                    item
                    for item in tweet_media
                    if item.get("article_id") == article.get("article_id")
                    or str(item.get("source") or "").startswith("article_")
                ]
            exported.append(
                {
                    "tweet_id": row["tweet_id"],
                    "text": row["text"],
                    "author": {
                        "id": row["author_id"],
                        "username": row["author_username"],
                        "display_name": row["author_display_name"],
                    },
                    "created_at": row["created_at"],
                    "collection": {
                        "type": row["collection_type"],
                        "folder_id": row["folder_id"] or None,
                        "sort_index": row["sort_index"],
                        "added_at": row["added_at"],
                        "synced_at": row["synced_at"],
                    },
                    "media": tweet_media,
                    "urls": url_refs_by_tweet.get(row["tweet_id"], []),
                    "article": article,
                    "media_tags": tags_by_tweet.get(row["tweet_id"]),
                    "qt_media_tags": tags_by_tweet.get(
                        quoted_tweet_by_source.get(row["tweet_id"], "")
                    ),
                    "raw_json": fastjson.loads(row["raw_json"])
                    if include_raw_json and row.get("raw_json")
                    else None,
                }
            )
        return exported

    def fetch_tweets_by_ids(
        self, tweet_ids: list[str], *, include_objects: bool = False
    ) -> list[dict[str, Any]]:
        """Hydrate collection tweets in the requested order, skipping unknown IDs.

        With include_objects, IDs outside every collection fall back to their
        stored tweet object; those tweets have a null ``collection`` and a
        top-level ``synced_at``.
        """
        if not tweet_ids:
            return []

        tweet_columns = [
            "tweet_id",
            "text",
            "author_id",
            "author_username",
            "author_display_name",
            "created_at",
            "collection_type",
            "folder_id",
            "sort_index",
            "added_at",
            "synced_at",
            "raw_json",
        ]

        rows = self._rows_for_values(RecordType.TWEET, "tweet_id", tweet_ids, columns=tweet_columns)
        object_ids: set[str] = set()
        if include_objects:
            member_ids = {row.get("tweet_id") for row in rows}
            object_rows = self._rows_for_values(
                RecordType.TWEET_OBJECT,
                "tweet_id",
                [tid for tid in tweet_ids if tid not in member_ids],
                columns=tweet_columns,
            )
            object_ids = {row["tweet_id"] for row in object_rows if row.get("tweet_id")}
            rows.extend(object_rows)

        id_order = {tid: i for i, tid in enumerate(tweet_ids)}
        rows.sort(
            key=lambda row: (
                id_order.get(row.get("tweet_id"), 999999),
                SEARCH_COLLECTION_ORDER.index(row["collection_type"])
                if row.get("collection_type") in SEARCH_COLLECTION_ORDER
                else len(SEARCH_COLLECTION_ORDER),
                row.get("folder_id") or "",
            )
        )
        unique_rows: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for row in rows:
            tweet_id = row.get("tweet_id")
            if not isinstance(tweet_id, str) or tweet_id in seen_ids:
                continue
            seen_ids.add(tweet_id)
            unique_rows.append(row)

        tweets = self._hydrate_exported_rows(
            unique_rows,
            include_raw_json=True,
        )
        for tweet in tweets:
            if tweet["tweet_id"] in object_ids:
                tweet["synced_at"] = tweet["collection"]["synced_at"]
                tweet["collection"] = None
        return tweets

    def export_rows(
        self,
        collection: str,
        *,
        sort: str = "newest",
        offset: int = 0,
        limit: int | None = None,
        include_raw_json: bool = True,
    ) -> list[dict[str, Any]]:
        if sort not in {"newest", "oldest"}:
            raise ValueError(f"Unsupported sort order: {sort}")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        if limit is not None and limit < 0:
            raise ValueError("limit must be non-negative")
        filter_expr = f"record_type = '{RecordType.TWEET}'"
        filter_params: tuple[str, ...] = ()
        if collection != "all":
            filter_expr += " AND collection_type = ?"
            filter_params = (collection,)
        tweet_columns = [
            "tweet_id",
            "text",
            "author_id",
            "author_username",
            "author_display_name",
            "created_at",
            "collection_type",
            "folder_id",
            "sort_index",
            "added_at",
            "synced_at",
        ]

        if include_raw_json:
            tweet_columns.append("raw_json")

        # Rank lightweight membership keys in SQLite; materialize text, JSON, and
        # secondary objects only for the selected logical tweets. Legacy dates
        # without a timestamp retain the export parser's unknown-last semantics.
        direction = "ASC" if sort == "oldest" else "DESC"
        order = (
            f"export_timestamp IS NULL, export_timestamp {direction}, "
            f"export_sort {direction}, tweet_id ASC, source_rowid ASC"
        )
        keys = self.conn.execute(
            "WITH candidates AS MATERIALIZED ("
            "SELECT row_key, rowid AS source_rowid, tweet_id, "
            "COALESCE(created_at_ts, tweetnook_export_timestamp(created_at)) AS export_timestamp, "
            "tweetnook_export_integer(sort_index) AS export_sort "
            f"FROM archive WHERE {filter_expr} AND tweet_id IS NOT NULL), "
            "ranked AS (SELECT *, ROW_NUMBER() OVER ("
            f"PARTITION BY tweet_id ORDER BY {order}) AS duplicate_rank FROM candidates) "
            "SELECT row_key FROM ranked WHERE duplicate_rank = 1 "
            f"ORDER BY {order} LIMIT ? OFFSET ?",
            (*filter_params, -1 if limit is None else limit, offset),
        ).fetchall()
        selected = self._rows_for_values(
            RecordType.TWEET,
            "row_key",
            [row["row_key"] for row in keys],
            columns=["row_key", *tweet_columns],
        )
        by_key = {row["row_key"]: row for row in selected}
        sorted_rows = [by_key[row["row_key"]] for row in keys if row["row_key"] in by_key]
        return self._hydrate_exported_rows(sorted_rows, include_raw_json)
