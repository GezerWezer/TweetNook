"""Archive counts and the statistics summary."""

from __future__ import annotations

from dataclasses import dataclass, field

from tweetnook.core.extractor import extract_status_id_from_url
from tweetnook.core.utils import utc_now

from .common import RESURRECTION_ELIGIBLE_SQL, SEARCH_COLLECTION_ORDER, valid_date_bounds
from .record_types import RecordType
from .relations import RelationsMixin
from .sync_state import SyncStateMixin


@dataclass(slots=True)
class ArchiveCollectionStats:
    collection_type: str
    post_count: int = 0
    oldest_created_at: str | None = None
    newest_created_at: str | None = None
    last_synced_at: str | None = None
    backfill_cursor: str | None = None
    backfill_incomplete: bool = False


@dataclass(slots=True)
class ArchiveStats:
    owner_user_id: str | None = None
    unique_post_count: int = 0
    collection_membership_count: int = 0
    article_count: int = 0
    raw_capture_count: int = 0
    media_count: int = 0
    url_count: int = 0
    oldest_created_at: str | None = None
    newest_created_at: str | None = None
    latest_capture_at: str | None = None
    latest_sync_at: str | None = None
    collections: list[ArchiveCollectionStats] = field(default_factory=list)
    pending_enrichment_count: int = 0
    transient_enrichment_failure_count: int = 0
    transient_enrichment_due_count: int = 0
    transient_enrichment_delayed_count: int = 0
    terminal_enrichment_count: int = 0
    resurrected_enrichment_count: int = 0
    done_enrichment_count: int = 0
    retryable_unavailable_count: int = 0
    permanent_unavailable_count: int = 0
    due_resurrection_count: int = 0
    preview_article_count: int = 0
    missing_tweet_object_count: int = 0
    expanded_thread_target_count: int = 0
    pending_thread_membership_count: int = 0
    pending_thread_linked_status_count: int = 0


class StatsMixin(SyncStateMixin, RelationsMixin):
    def counts(self) -> dict[str, int]:
        tweet_rows = self._count(f"record_type = '{RecordType.TWEET}'")
        return {
            "raw_captures": self._count(f"record_type = '{RecordType.RAW_CAPTURE}'"),
            "tweets": tweet_rows,
            "collections": tweet_rows,
            "tweet_objects": self._count(f"record_type = '{RecordType.TWEET_OBJECT}'"),
            "tweet_relations": self._count(f"record_type = '{RecordType.TWEET_RELATION}'"),
            "media": self._count(f"record_type = '{RecordType.MEDIA}'"),
            "urls": self._count(f"record_type = '{RecordType.URL}'"),
            "url_refs": self._count(f"record_type = '{RecordType.URL_REF}'"),
            "articles": self._count(f"record_type = '{RecordType.ARTICLE}'"),
            "import_manifests": self._count(f"record_type = '{RecordType.IMPORT_MANIFEST}'"),
            "sync_state": self._count(f"record_type = '{RecordType.SYNC_STATE}'"),
        }

    def archive_stats(self, max_linked_depth: int = 1) -> ArchiveStats:
        counts = self.counts()
        tweet_rows = [
            dict(row)
            for row in self.conn.execute(
                f"""
                SELECT tweet_id, collection_type, created_at
                FROM archive INDEXED BY idx_archive_record_page
                WHERE record_type = '{RecordType.TWEET}'
                """
            ).fetchall()
        ]
        sync_rows = self._query(
            expr=f"record_type = '{RecordType.SYNC_STATE}'",
            cols=[
                "collection_type",
                "updated_at",
                "last_head_tweet_id",
                "backfill_cursor",
                "backfill_incomplete",
            ],
        )
        enrichment_counts = {
            row[0]: int(row[1])
            for row in self.conn.execute(
                f"""
                SELECT enrichment_state, count(*)
                FROM archive INDEXED BY idx_archive_enrichment_due
                WHERE record_type = '{RecordType.TWEET_OBJECT}'
                GROUP BY enrichment_state
                """
            ).fetchall()
            if isinstance(row[0], str)
        }
        scheduler_rows = [
            dict(row)
            for row in self.conn.execute(
                f"""
                SELECT deleted_at, enrichment_state, enrichment_reason,
                       enrichment_retry_eligible, enrichment_next_retry_at,
                       {RESURRECTION_ELIGIBLE_SQL} AS can_retry
                FROM archive INDEXED BY idx_archive_enrichment_due
                WHERE record_type = '{RecordType.TWEET_OBJECT}'
                  AND enrichment_state IN ('transient_failure', 'terminal_unavailable')
                """
            ).fetchall()
        ]
        url_ref_rows = [
            dict(row)
            for row in self.conn.execute(
                f"""
                SELECT tweet_id, canonical_url, expanded_url, url
                FROM archive INDEXED BY idx_archive_record_page
                WHERE record_type = '{RecordType.URL_REF}'
                """
            ).fetchall()
        ]

        oldest_created_at: str | None = None
        newest_created_at: str | None = None
        unique_post_ids: set[str] = set()
        expanded_thread_targets = set(self.list_expanded_thread_target_ids())
        collection_stats = {
            collection: ArchiveCollectionStats(collection_type=collection)
            for collection in SEARCH_COLLECTION_ORDER
        }

        def update_created_bounds(
            raw: str | None,
            *,
            collection: ArchiveCollectionStats | None = None,
        ) -> None:
            nonlocal oldest_created_at, newest_created_at
            oldest_created_at, _ = valid_date_bounds((oldest_created_at, raw))
            _, newest_created_at = valid_date_bounds((newest_created_at, raw))
            if collection is not None:
                collection.oldest_created_at, _ = valid_date_bounds(
                    (collection.oldest_created_at, raw)
                )
                _, collection.newest_created_at = valid_date_bounds(
                    (collection.newest_created_at, raw)
                )

        for row in tweet_rows:
            tweet_id = row.get("tweet_id")
            if isinstance(tweet_id, str) and tweet_id:
                unique_post_ids.add(tweet_id)
            collection_type = row.get("collection_type")
            collection = None
            if isinstance(collection_type, str) and collection_type:
                collection = collection_stats.setdefault(
                    collection_type,
                    ArchiveCollectionStats(collection_type=collection_type),
                )
                collection.post_count += 1
            update_created_bounds(row.get("created_at"), collection=collection)

        pending_enrichment_count = enrichment_counts.get("pending", 0)
        transient_enrichment_failure_count = enrichment_counts.get("transient_failure", 0)
        transient_enrichment_due_count = 0
        transient_enrichment_delayed_count = 0
        terminal_enrichment_count = enrichment_counts.get("terminal_unavailable", 0)
        resurrected_enrichment_count = enrichment_counts.get("resurrected", 0)
        done_enrichment_count = enrichment_counts.get("done", 0)
        retryable_unavailable_count = 0
        permanent_unavailable_count = 0
        due_resurrection_count = 0
        stats_now = utc_now()
        for row in scheduler_rows:
            enrichment_state = row.get("enrichment_state")
            if enrichment_state == "transient_failure":
                next_retry_at = row.get("enrichment_next_retry_at")
                if not next_retry_at or next_retry_at <= stats_now:
                    transient_enrichment_due_count += 1
                else:
                    transient_enrichment_delayed_count += 1
            elif enrichment_state == "terminal_unavailable":
                permanently_unavailable = not row["can_retry"]
                if permanently_unavailable:
                    permanent_unavailable_count += 1
                else:
                    retryable_unavailable_count += 1
                    next_retry_at = row.get("enrichment_next_retry_at")
                    if not next_retry_at or next_retry_at <= stats_now:
                        due_resurrection_count += 1
        preview_article_count = int(
            self.conn.execute(
                f"""
                SELECT count(*)
                FROM archive INDEXED BY idx_archive_record_page
                WHERE record_type = '{RecordType.ARTICLE}'
                  AND (status IS NULL OR status != 'body_present')
                """
            ).fetchone()[0]
            or 0
        )
        missing_tweet_object_count = int(
            self.conn.execute(
                f"""
                SELECT count(DISTINCT tweet.tweet_id)
                FROM archive tweet INDEXED BY idx_archive_record_page
                WHERE tweet.record_type = '{RecordType.TWEET}'
                  AND tweet.tweet_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM archive object INDEXED BY idx_archive_tweet_id
                      WHERE object.tweet_id = tweet.tweet_id
                        AND object.record_type = '{RecordType.TWEET_OBJECT}'
                  )
                """
            ).fetchone()[0]
            or 0
        )

        latest_capture_row = self.conn.execute(
            f"""
            SELECT max(captured_at)
            FROM archive INDEXED BY idx_archive_capture_target
            WHERE record_type = '{RecordType.RAW_CAPTURE}'
            """
        ).fetchone()
        latest_capture_at = latest_capture_row[0] if latest_capture_row else None

        latest_sync_at: str | None = None
        for row in sync_rows:
            collection_type = row.get("collection_type")
            if not isinstance(collection_type, str) or not collection_type:
                continue
            collection = collection_stats.setdefault(
                collection_type,
                ArchiveCollectionStats(collection_type=collection_type),
            )
            updated_at = row.get("updated_at")
            if (
                isinstance(updated_at, str)
                and updated_at
                and (collection.last_synced_at is None or updated_at > collection.last_synced_at)
            ):
                collection.last_synced_at = updated_at
                backfill_cursor = row.get("backfill_cursor")
                collection.backfill_cursor = (
                    backfill_cursor
                    if isinstance(backfill_cursor, str) and backfill_cursor
                    else None
                )
                collection.backfill_incomplete = self._parse_bool(row.get("backfill_incomplete"))
            if (
                isinstance(updated_at, str)
                and updated_at
                and (latest_sync_at is None or updated_at > latest_sync_at)
            ):
                latest_sync_at = updated_at

        ordered_collections = [
            collection_stats[name] for name in SEARCH_COLLECTION_ORDER if name in collection_stats
        ]
        ordered_collections.extend(
            collection_stats[name]
            for name in sorted(collection_stats)
            if name not in SEARCH_COLLECTION_ORDER
        )
        pending_thread_membership_count = len(unique_post_ids - expanded_thread_targets)
        pending_linked_status_targets: set[str] = set()

        url_edges: dict[str, list[str]] = {}
        for row in url_ref_rows:
            target_id = None
            for field_name in ("canonical_url", "expanded_url", "url"):
                candidate = row.get(field_name)
                if isinstance(candidate, str):
                    target_id = extract_status_id_from_url(candidate)
                    if target_id:
                        break
            src = row.get("tweet_id")
            if src and target_id:
                url_edges.setdefault(src, []).append(target_id)

        depths = {tid: 0 for tid in unique_post_ids if tid}
        discovery_kinds: dict[str, str] = {}
        quote_reachable_targets: set[str] = set()
        for depth in range(1, max_linked_depth + 1):
            frontier = {
                tweet_id for tweet_id, known_depth in depths.items() if known_depth == depth - 1
            }
            quote_edges: dict[str, list[str]] = {}
            for row in self.list_quote_relation_rows(frontier):
                source_id = row.get("tweet_id")
                target_id = row.get("target_tweet_id")
                if isinstance(source_id, str) and isinstance(target_id, str) and target_id:
                    quote_edges.setdefault(source_id, []).append(target_id)

            for source_id in sorted(frontier):
                candidates = [
                    *(("quote", target_id) for target_id in quote_edges.get(source_id, [])),
                    *(("linked", target_id) for target_id in url_edges.get(source_id, [])),
                ]
                for kind, target_id in candidates:
                    if not target_id or target_id == source_id:
                        continue
                    if kind == "quote":
                        quote_reachable_targets.add(target_id)
                    if target_id in depths:
                        continue
                    depths[target_id] = depth
                    discovery_kinds[target_id] = kind

        linked_candidates = {
            target_id
            for target_id, depth in depths.items()
            if depth > 0
            and target_id not in quote_reachable_targets
            and discovery_kinds[target_id] == "linked"
        }
        known_linked_targets = set(unique_post_ids & linked_candidates)
        linked_candidate_ids = sorted(linked_candidates - known_linked_targets)
        for start in range(0, len(linked_candidate_ids), 500):
            chunk = linked_candidate_ids[start : start + 500]
            placeholders = ", ".join("?" for _tweet_id in chunk)
            known_linked_targets.update(
                str(row[0])
                for row in self.conn.execute(
                    f"""
                    SELECT DISTINCT tweet_id
                    FROM archive INDEXED BY idx_archive_tweet_id
                    WHERE tweet_id IN ({placeholders})
                      AND record_type = '{RecordType.TWEET_OBJECT}'
                    """,
                    chunk,
                ).fetchall()
            )

        for target_id, depth in depths.items():
            if depth == 0 or target_id in expanded_thread_targets:
                continue
            if (
                target_id not in quote_reachable_targets
                and discovery_kinds[target_id] == "linked"
                and target_id in known_linked_targets
            ):
                continue
            pending_linked_status_targets.add(target_id)
        return ArchiveStats(
            owner_user_id=self.get_archive_owner_id(),
            unique_post_count=len(unique_post_ids),
            collection_membership_count=counts["tweets"],
            article_count=counts["articles"],
            raw_capture_count=counts["raw_captures"],
            media_count=counts["media"],
            url_count=counts["urls"],
            oldest_created_at=oldest_created_at,
            newest_created_at=newest_created_at,
            latest_capture_at=latest_capture_at,
            latest_sync_at=latest_sync_at,
            collections=ordered_collections,
            pending_enrichment_count=pending_enrichment_count,
            transient_enrichment_failure_count=transient_enrichment_failure_count,
            transient_enrichment_due_count=transient_enrichment_due_count,
            transient_enrichment_delayed_count=transient_enrichment_delayed_count,
            terminal_enrichment_count=terminal_enrichment_count,
            resurrected_enrichment_count=resurrected_enrichment_count,
            done_enrichment_count=done_enrichment_count,
            retryable_unavailable_count=retryable_unavailable_count,
            permanent_unavailable_count=permanent_unavailable_count,
            due_resurrection_count=due_resurrection_count,
            preview_article_count=preview_article_count,
            missing_tweet_object_count=missing_tweet_object_count,
            expanded_thread_target_count=len(expanded_thread_targets),
            pending_thread_membership_count=pending_thread_membership_count,
            pending_thread_linked_status_count=len(pending_linked_status_targets),
        )
