"""Enrichment queues: media downloads, URL unfurls, link cards and resurrection."""

from __future__ import annotations

from typing import Any

from tweetnook.core import fastjson
from tweetnook.core.extractor import (
    extract_secondary_objects,
    link_card_has_image,
    link_card_lacks_image,
    restore_link_card_image,
)
from tweetnook.core.utils import utc_now

from .checks import ChecksMixin
from .common import (
    RESURRECTION_ELIGIBLE_SQL,
    RESURRECTION_PERMANENT_SQL,
    Clause,
    and_clauses,
    clause_in,
    is_resurrection_candidate,
    state_filter,
)
from .objects import ObjectsMixin
from .record_types import RecordType
from .records import PageBuffer

_DETAIL_OPERATIONS = ("ThreadExpandDetail", "TweetDetail", "TweetDetailResurrection")


def _captured_link_card_tweets(raw_json: Any, wanted: set[str]) -> dict[str, dict[str, Any]]:
    """Tweets in one raw capture, among ``wanted``, whose summary card has an image."""
    if not isinstance(raw_json, str) or '"image_value"' not in raw_json:
        return {}
    try:
        payload = fastjson.loads(raw_json)
    except (TypeError, ValueError):
        return {}
    found: dict[str, dict[str, Any]] = {}
    stack = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            rest_id = node.get("rest_id")
            if rest_id in wanted and link_card_has_image(node):
                found[rest_id] = node
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return found


class EnrichmentMixin(ChecksMixin, ObjectsMixin):
    def backfill_link_card_media(self) -> int:
        """Queue saved card images once, without refetching tweets or rehydrating content."""
        key = "link_card_media_v1"
        marker = self._row_key_for_metadata(key)
        if self._get_row(marker) is not None:
            return 0
        added = 0
        after = 0
        while True:
            rows = self.conn.execute(
                "SELECT rowid, raw_json, source FROM archive "
                f"WHERE record_type IN ('{RecordType.TWEET_OBJECT}', '{RecordType.TWEET}') AND "
                "rowid > ? "
                "AND raw_json LIKE '%\"card\"%' ORDER BY rowid LIMIT 500",
                (after,),
            ).fetchall()
            if not rows:
                break
            buffer = PageBuffer()
            for row in rows:
                after = row["rowid"]
                try:
                    raw = fastjson.loads(row["raw_json"])
                except (TypeError, ValueError):
                    continue
                if not isinstance(raw, dict):
                    continue
                graph = extract_secondary_objects(raw)
                for media in graph.media.values():
                    if media.source != "link_card":
                        continue
                    row_key = self._row_key_for_media(media.tweet_id, media.media_key)
                    if row_key in buffer.records or self._get_row(row_key) is not None:
                        continue
                    self._queue_record(
                        self._media_record(
                            media,
                            provenance_source=self._normalized_source(row["source"]),
                            cursor=buffer,
                        ),
                        cursor=buffer,
                    )
                    added += 1
            self._merge_records(list(buffer.records.values()))
        self._queue_record(
            self._record(
                row_key=marker,
                record_type=RecordType.METADATA,
                key=key,
                value="done",
                updated_at=utc_now(),
            )
        )
        return added

    def restore_link_card_images(self) -> int:
        """Put back card images a later timeline copy dropped, once per archive.

        Detail and timeline captures keep each response as X sent it, so a card
        stored without its image bindings is restored from the newest capture
        of the same tweet that has them. Returns the card images queued.
        """
        key = "link_card_restore_v1"
        marker = self._row_key_for_metadata(key)
        if self._get_row(marker) is not None:
            return 0
        stripped: set[str] = set()
        after = 0
        while True:
            rows = self.conn.execute(
                "SELECT rowid, tweet_id, raw_json FROM archive "
                f"WHERE record_type IN ('{RecordType.TWEET_OBJECT}', '{RecordType.TWEET}') AND "
                "rowid > ? "
                "AND raw_json LIKE '%\"card\"%' ORDER BY rowid LIMIT 500",
                (after,),
            ).fetchall()
            if not rows:
                break
            for row in rows:
                after = row["rowid"]
                try:
                    raw = fastjson.loads(row["raw_json"])
                except (TypeError, ValueError):
                    continue
                if row["tweet_id"] and link_card_lacks_image(raw):
                    stripped.add(str(row["tweet_id"]))
        donors: dict[str, dict[str, Any]] = {}
        for tweet_id in stripped:
            # Newest first: a tweet's own detail captures are indexed by its id.
            for capture in reversed(self.list_target_captures(tweet_id, _DETAIL_OPERATIONS)):
                found = _captured_link_card_tweets(capture["raw_json"], {tweet_id})
                if tweet_id in found:
                    donors[tweet_id] = found[tweet_id]
                    break
        missing = stripped - donors.keys()
        if missing:
            # Timeline pages hold many tweets; read them once, newest last.
            rows = self.conn.execute(
                f"SELECT raw_json FROM archive WHERE record_type = '{RecordType.RAW_CAPTURE}' "
                "AND operation IN ('Likes', 'Bookmarks', 'UserTweets') "
                "AND raw_json LIKE '%image_value%' ORDER BY captured_at"
            ).fetchall()
            for row in rows:
                donors.update(_captured_link_card_tweets(row["raw_json"], missing))
        queued = 0
        stamp = utc_now()
        buffer = PageBuffer()
        with self.transaction():
            for tweet_id, donor in donors.items():
                rows = self.conn.execute(
                    "SELECT row_key, raw_json, source FROM archive "
                    f"WHERE tweet_id = ? AND record_type IN ('{RecordType.TWEET_OBJECT}', "
                    f"'{RecordType.TWEET}')",
                    (tweet_id,),
                ).fetchall()
                for row in rows:
                    try:
                        raw = fastjson.loads(row["raw_json"])
                    except (TypeError, ValueError):
                        continue
                    if not isinstance(raw, dict) or not restore_link_card_image(raw, donor):
                        continue
                    self.conn.execute(
                        "UPDATE archive SET raw_json = ?, updated_at = ? WHERE row_key = ?",
                        (self._json_value(raw), stamp, row["row_key"]),
                    )
                    for media in extract_secondary_objects(raw).media.values():
                        if media.source != "link_card":
                            continue
                        row_key = self._row_key_for_media(media.tweet_id, media.media_key)
                        if row_key in buffer.records or self._get_row(row_key) is not None:
                            continue
                        self._queue_record(
                            self._media_record(
                                media,
                                provenance_source=self._normalized_source(row["source"]),
                                cursor=buffer,
                            ),
                            cursor=buffer,
                        )
                        queued += 1
            self._merge_records(list(buffer.records.values()))
            self._queue_record(
                self._record(
                    row_key=marker,
                    record_type=RecordType.METADATA,
                    key=key,
                    value="done",
                    updated_at=utc_now(),
                )
            )
        return queued

    def list_media_rows(
        self,
        *,
        states: set[str] | None = None,
        media_types: set[str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        if states is not None and not states:
            return []
        if media_types is not None and not media_types:
            return []
        where = and_clauses(
            f"record_type = '{RecordType.MEDIA}'",
            state_filter("download_state", states) if states is not None else "",
            clause_in("media_type", media_types) if media_types is not None else "",
        )
        cols = [
            "row_key",
            "tweet_id",
            "position",
            "media_key",
            "media_type",
            "media_url",
            "thumbnail_url",
            "download_state",
            "local_path",
            "thumbnail_local_path",
            "sha256",
            "byte_size",
            "content_type",
            "thumbnail_sha256",
            "thumbnail_byte_size",
            "thumbnail_content_type",
            "variants_json",
            "source",
        ]
        return self._query(
            where.sql,
            where.params,
            cols=cols,
            order_by="COALESCE(tweet_id, ''), COALESCE(position, 1000000), rowid",
            limit=limit,
        )

    def build_media_download_update(
        self,
        row: dict[str, Any],
        *,
        download_state: str,
        local_path: str | None,
        sha256: str | None,
        byte_size: int | None,
        content_type: str | None,
        thumbnail_local_path: str | None,
        thumbnail_sha256: str | None,
        thumbnail_byte_size: int | None,
        thumbnail_content_type: str | None,
        downloaded_at: str | None,
        download_error: str | None,
    ) -> dict[str, Any]:
        full_row = self._get_row(row["row_key"])
        if full_row is None:
            raise KeyError(f"Media row not found: {row['row_key']}")
        updated = dict(full_row)
        updated.update(
            {
                "download_state": download_state,
                "local_path": local_path,
                "sha256": sha256,
                "byte_size": byte_size,
                "content_type": content_type,
                "thumbnail_local_path": thumbnail_local_path,
                "thumbnail_sha256": thumbnail_sha256,
                "thumbnail_byte_size": thumbnail_byte_size,
                "thumbnail_content_type": thumbnail_content_type,
                "downloaded_at": downloaded_at,
                "download_error": download_error,
                "updated_at": utc_now(),
            }
        )
        return updated

    def list_url_rows(
        self,
        *,
        states: set[str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        if states is not None and not states:
            return []
        where = and_clauses(
            f"record_type = '{RecordType.URL}'",
            state_filter("unfurl_state", states) if states is not None else "",
        )
        cols = [
            "row_key",
            "url_hash",
            "canonical_url",
            "url",
            "expanded_url",
            "final_url",
            "http_status",
            "title",
            "description",
            "site_name",
            "content_type",
            "unfurl_state",
        ]
        return self._query(
            where.sql,
            where.params,
            cols=cols,
            order_by="COALESCE(NULLIF(canonical_url, ''), url, ''), rowid",
            limit=limit,
        )

    def build_url_unfurl_update(
        self,
        row: dict[str, Any],
        *,
        http_status: int | None,
        final_url: str | None,
        canonical_url: str | None,
        title: str | None,
        description: str | None,
        site_name: str | None,
        content_type: str | None,
        unfurl_state: str,
        last_fetched_at: str | None,
        download_error: str | None,
    ) -> dict[str, Any]:
        full_row = self._get_row(row["row_key"])
        if full_row is None:
            raise KeyError(f"URL row not found: {row['row_key']}")
        updated = dict(full_row)
        updated.update(
            {
                "http_status": http_status,
                "final_url": final_url,
                "canonical_url": canonical_url,
                "url": canonical_url or updated.get("url"),
                "title": title,
                "description": description,
                "site_name": site_name,
                "content_type": content_type,
                "unfurl_state": unfurl_state,
                "last_fetched_at": last_fetched_at,
                "download_error": download_error,
                "updated_at": utc_now(),
            }
        )
        return updated

    def get_article_tweet_ids(
        self,
        *,
        preview_only: bool = False,
        limit: int | None = None,
    ) -> list[str]:
        where = and_clauses(
            f"record_type = '{RecordType.ARTICLE}'",
            "(status IS NULL OR status != 'body_present')" if preview_only else "",
            "tweet_id IS NOT NULL AND tweet_id != ''",
        )
        rows = self._query(
            where.sql,
            cols=["tweet_id"],
            order_by="tweet_id, rowid",
            limit=limit,
        )
        return [row["tweet_id"] for row in rows]

    def list_tweet_objects_for_enrichment(
        self, *, limit: int | None = None, now: str | None = None
    ) -> list[dict[str, Any]]:
        now = now or utc_now()
        return self._query(
            f"record_type = '{RecordType.TWEET_OBJECT}' "
            "AND (enrichment_state = 'pending' OR ("
            "enrichment_state = 'transient_failure' AND ("
            "enrichment_next_retry_at IS NULL OR enrichment_next_retry_at <= ?)))",
            (now,),
            cols=[
                "tweet_id",
                "enrichment_checked_at",
                "enrichment_next_retry_at",
                "enrichment_retry_count",
            ],
            order_by=(
                "COALESCE(enrichment_next_retry_at, '') ASC, "
                "COALESCE(enrichment_checked_at, '') ASC, tweet_id ASC"
            ),
            limit=limit,
        )

    def count_tweet_objects_for_enrichment(self) -> int:
        return self.count_incomplete_initial_enrichment()

    def count_pending_initial_enrichment(self) -> int:
        return self._count(
            f"record_type = '{RecordType.TWEET_OBJECT}' AND enrichment_state = 'pending'"
        )

    def count_due_transient_enrichment(self, now: str | None = None) -> int:
        now = now or utc_now()
        return self._count(
            f"record_type = '{RecordType.TWEET_OBJECT}' AND enrichment_state = 'transient_failure' "
            "AND (enrichment_next_retry_at IS NULL OR enrichment_next_retry_at <= ?)",
            (now,),
        )

    def count_delayed_transient_enrichment(self, now: str | None = None) -> int:
        now = now or utc_now()
        return self._count(
            f"record_type = '{RecordType.TWEET_OBJECT}' AND enrichment_state = 'transient_failure' "
            "AND enrichment_next_retry_at IS NOT NULL AND enrichment_next_retry_at > ?",
            (now,),
        )

    def count_incomplete_initial_enrichment(self) -> int:
        return self._count(
            f"record_type = '{RecordType.TWEET_OBJECT}' "
            "AND (enrichment_state = 'pending' OR enrichment_state = 'transient_failure')"
        )

    def enrichment_status_counts(self) -> dict[str, int]:
        return {
            "pending_initial_enrichment": self.count_pending_initial_enrichment(),
            "transient_initial_enrichment": self._count(
                f"record_type = '{RecordType.TWEET_OBJECT}' AND enrichment_state = "
                "'transient_failure'"
            ),
            "terminal_unavailable": self._count(
                f"record_type = '{RecordType.TWEET_OBJECT}' AND enrichment_state = "
                "'terminal_unavailable'"
            ),
            "retryable_unavailable": self._count(
                f"record_type = '{RecordType.TWEET_OBJECT}' AND enrichment_state = "
                "'terminal_unavailable' "
                f"AND {RESURRECTION_ELIGIBLE_SQL}"
            ),
            "permanent_unavailable": self._count(
                f"record_type = '{RecordType.TWEET_OBJECT}' AND enrichment_state = "
                "'terminal_unavailable' "
                f"AND {RESURRECTION_PERMANENT_SQL}"
            ),
            "done": self._count(
                f"record_type = '{RecordType.TWEET_OBJECT}' AND enrichment_state = 'done'"
            ),
            "resurrected": self._count(
                f"record_type = '{RecordType.TWEET_OBJECT}' AND enrichment_state = 'resurrected'"
            ),
        }

    def list_due_resurrection_tweets(
        self,
        *,
        reasons: set[str] | None = None,
        exclude_tweet_ids: set[str] | None = None,
        limit: int | None = None,
        now: str | None = None,
    ) -> list[dict[str, Any]]:
        now = now or utc_now()
        clauses = [
            f"record_type = '{RecordType.TWEET_OBJECT}'",
            "enrichment_state = 'terminal_unavailable'",
            RESURRECTION_ELIGIBLE_SQL,
            Clause("(enrichment_next_retry_at IS NULL OR enrichment_next_retry_at <= ?)", (now,)),
        ]
        if reasons:
            clauses.append(clause_in("enrichment_reason", reasons))
        if exclude_tweet_ids:
            clauses.append(clause_in("tweet_id", exclude_tweet_ids, negate=True))
        where = and_clauses(*clauses)
        return self._query(
            where.sql,
            where.params,
            cols=[
                "tweet_id",
                "author_id",
                "enrichment_state",
                "enrichment_reason",
                "enrichment_detail",
                "enrichment_checked_at",
                "enrichment_retry_count",
                "enrichment_next_retry_at",
            ],
            order_by=(
                "COALESCE(enrichment_next_retry_at, '') ASC, "
                "COALESCE(enrichment_checked_at, '') ASC, tweet_id ASC"
            ),
            limit=limit,
        )

    def current_resurrection_candidates(
        self, tweet_ids: list[str], *, cursor: PageBuffer | None = None
    ) -> dict[str, dict[str, Any]]:
        """The current rows of these posts that still await restoration, whenever they're due.

        Reads writes staged in ``cursor``, so a post restored earlier in a batch drops out.
        """

        return {
            tweet_id: row
            for tweet_id, row in self.current_objects(tweet_ids, cursor=cursor).items()
            if is_resurrection_candidate(row)
        }

    def list_same_author_resurrection_tweets(
        self,
        author_id: str,
        *,
        exclude_tweet_ids: set[str] | None = None,
        limit: int = 5,
        due_only: bool = False,
        now: str | None = None,
    ) -> list[dict[str, Any]]:
        now = now or utc_now()
        clauses = [
            f"record_type = '{RecordType.TWEET_OBJECT}'",
            "enrichment_state = 'terminal_unavailable'",
            RESURRECTION_ELIGIBLE_SQL,
            Clause("author_id = ?", (author_id,)),
        ]
        if due_only:
            clauses.append(
                Clause(
                    "(enrichment_next_retry_at IS NULL OR enrichment_next_retry_at <= ?)", (now,)
                )
            )
        if exclude_tweet_ids:
            clauses.append(clause_in("tweet_id", exclude_tweet_ids, negate=True))
        where = and_clauses(*clauses)
        return self._query(
            where.sql,
            where.params,
            cols=[
                "tweet_id",
                "author_id",
                "enrichment_state",
                "enrichment_reason",
                "enrichment_detail",
                "enrichment_checked_at",
                "enrichment_retry_count",
                "enrichment_next_retry_at",
            ],
            order_by=(
                "COALESCE(enrichment_next_retry_at, '') ASC, "
                "COALESCE(enrichment_checked_at, '') ASC, tweet_id ASC"
            ),
            limit=limit,
        )

    def mark_author_resurrection_due(
        self, author_id: str, *, exclude_tweet_ids: set[str] | None = None
    ) -> int:
        clauses = [
            f"record_type = '{RecordType.TWEET_OBJECT}'",
            "enrichment_state = 'terminal_unavailable'",
            RESURRECTION_ELIGIBLE_SQL,
            Clause("author_id = ?", (author_id,)),
        ]
        if exclude_tweet_ids:
            clauses.append(clause_in("tweet_id", exclude_tweet_ids, negate=True))
        where = and_clauses(*clauses)
        with self.transaction():
            cursor = self.conn.execute(
                "UPDATE archive SET enrichment_next_retry_at = ?, updated_at = ? "
                f"WHERE {where.sql}",
                (utc_now(), utc_now(), *where.params),
            )
        return cursor.rowcount

    def mark_tweets_resurrection_due(
        self,
        tweet_ids: set[str],
        *,
        due_at: str | None = None,
    ) -> int:
        if not tweet_ids:
            return 0
        now = due_at or utc_now()
        clauses = [
            f"record_type = '{RecordType.TWEET_OBJECT}'",
            "enrichment_state = 'terminal_unavailable'",
            RESURRECTION_ELIGIBLE_SQL,
            clause_in("tweet_id", tweet_ids),
        ]
        where = and_clauses(*clauses)
        with self.transaction():
            cursor = self.conn.execute(
                "UPDATE archive SET enrichment_next_retry_at = ?, updated_at = ? "
                f"WHERE {where.sql}",
                (now, now, *where.params),
            )
        return cursor.rowcount

    def count_due_resurrection_tweets(self, now: str | None = None) -> int:
        now = now or utc_now()
        return self._count(
            f"record_type = '{RecordType.TWEET_OBJECT}' "
            "AND enrichment_state = 'terminal_unavailable' "
            f"AND {RESURRECTION_ELIGIBLE_SQL} "
            "AND (enrichment_next_retry_at IS NULL OR enrichment_next_retry_at <= ?)",
            (now,),
        )
