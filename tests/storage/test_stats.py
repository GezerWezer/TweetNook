from __future__ import annotations

from tests.conftest import (
    list_raw_capture_target_ids,
    make_tweet_detail_response,
    make_tweet_result,
    make_url_entity,
)
from tests.storage.conftest import _complex_tweet, _tweet, _tweet_with_created_at
from tweetnook.core.timeline_parsing import TimelineTweet
from tweetnook.storage import open_archive_store


def test_archive_stats_summarizes_collections_and_bounds(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    store.set_archive_owner_id("42")
    newer = _complex_tweet("100")
    older = _tweet_with_created_at(
        "200",
        created_at="Tue Oct 09 21:39:26 +0000 2012",
        sort_index="20",
    )
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out="cursor-1",
        http_status=200,
        raw_json={"ok": True},
        tweets=[newer],
        last_head_tweet_id="100",
        backfill_cursor="cursor-1",
        backfill_incomplete=True,
    )
    store.persist_page(
        operation="Likes",
        collection_type="like",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[newer],
        last_head_tweet_id="100",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.persist_page(
        operation="UserTweets",
        collection_type="tweet",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[older],
        last_head_tweet_id="200",
        backfill_cursor=None,
        backfill_incomplete=False,
    )

    stats = store.archive_stats()
    collections = {row.collection_type: row for row in stats.collections}

    assert stats.owner_user_id == "42"
    assert stats.unique_post_count == 2
    assert stats.collection_membership_count == 3
    assert stats.article_count == 1
    assert stats.raw_capture_count == 3
    assert stats.media_count == 3
    assert stats.url_count == 2
    assert stats.oldest_created_at == "Tue Oct 09 21:39:26 +0000 2012"
    assert stats.newest_created_at == "Sat Mar 14 00:00:00 +0000 2026"
    assert stats.latest_capture_at is not None
    assert stats.latest_sync_at is not None

    assert collections["bookmark"].post_count == 1
    assert collections["bookmark"].oldest_created_at == "Sat Mar 14 00:00:00 +0000 2026"
    assert collections["bookmark"].newest_created_at == "Sat Mar 14 00:00:00 +0000 2026"
    assert collections["bookmark"].last_synced_at is not None
    assert collections["bookmark"].backfill_cursor == "cursor-1"
    assert collections["bookmark"].backfill_incomplete is True

    assert collections["like"].post_count == 1
    assert collections["tweet"].post_count == 1
    assert collections["tweet"].oldest_created_at == "Tue Oct 09 21:39:26 +0000 2012"
    assert collections["tweet"].newest_created_at == "Tue Oct 09 21:39:26 +0000 2012"
    store.close()


def test_archive_stats_reports_followup_work(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    primary = _complex_tweet("100")
    linked_raw = make_tweet_result(
        "400",
        "linked status source",
        urls=[
            make_url_entity(
                "https://t.co/status",
                "https://x.com/other/status/999",
                display_url="x.com/other/status/999",
            )
        ],
    )
    linked = TimelineTweet(
        tweet_id="400",
        text="linked status source",
        author_id="100",
        author_username="user100",
        author_display_name="User 100",
        created_at="Sat Mar 14 00:00:00 +0000 2026",
        sort_index="20",
        raw_json=linked_raw,
    )
    extra = _tweet("300")

    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[primary, linked, extra],
        last_head_tweet_id="300",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.persist_thread_detail(
        focal_tweet_id="100",
        tweets=[primary],
        raw_json={"ok": True},
    )
    store.update_tweet_object_enrichment(
        "100",
        enrichment_state="transient_failure",
        enrichment_checked_at="2026-03-21T00:00:00+00:00",
        enrichment_http_status=503,
        enrichment_reason="rate_limit",
    )
    store.update_tweet_object_enrichment(
        "400",
        enrichment_state="pending",
        enrichment_checked_at=None,
        enrichment_http_status=None,
        enrichment_reason=None,
    )
    store._merge_records(
        [
            store._record(
                row_key="tweet_object:500",
                record_type="tweet_object",
                tweet_id="500",
                enrichment_state="transient_failure",
                enrichment_next_retry_at="9999-12-31T23:59:59+00:00",
            )
        ]
    )
    store._delete("row_key = 'tweet_object:300'")

    statements: list[str] = []
    store.conn.set_trace_callback(statements.append)
    stats = store.archive_stats()

    assert stats.pending_enrichment_count == 1
    assert stats.transient_enrichment_failure_count == 2
    assert stats.transient_enrichment_due_count == 1
    assert stats.transient_enrichment_delayed_count == 1
    assert stats.terminal_enrichment_count == 0
    assert stats.done_enrichment_count == 1
    assert stats.missing_tweet_object_count == 1
    assert stats.expanded_thread_target_count == 1
    assert stats.pending_thread_membership_count == 2
    assert stats.pending_thread_linked_status_count == 2
    enrichment_aggregates = [
        statement for statement in statements if "SELECT enrichment_state, count(*)" in statement
    ]
    assert len(enrichment_aggregates) == 1
    assert "INDEXED BY idx_archive_enrichment_due" in enrichment_aggregates[0]
    assert not any(
        "SELECT tweet_id, deleted_at, enrichment_state" in statement for statement in statements
    )
    store.close()


def test_live_timeline_ingestion_resurrects_and_clears_terminal_scheduler_metadata(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    tweet = _complex_tweet("100")
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"pass": 1},
        tweets=[tweet],
        last_head_tweet_id="100",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.persist_unavailable_tweet(
        tweet_id="100",
        operation="TweetDetail",
        raw_json={"__typename": "TweetTombstone"},
        http_status=200,
        reason="protected_account",
        detail="private",
        retry_eligible=True,
        next_retry_at="2030-01-01T00:00:00+00:00",
        retry_count=3,
        checked_at="2025-01-01T00:00:00+00:00",
    )

    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"pass": 2},
        tweets=[tweet],
        last_head_tweet_id="100",
        backfill_cursor=None,
        backfill_incomplete=False,
    )

    row = store._get_row("tweet_object:100")
    assert row is not None
    assert row["enrichment_state"] == "resurrected"
    assert row["enrichment_reason"] is None
    assert row["enrichment_detail"] is None
    assert row["enrichment_retry_count"] == 0
    assert row["enrichment_next_retry_at"] is None
    assert row["enrichment_first_unavailable_at"] is None
    assert row["enrichment_retry_eligible"] == 0
    store.close()


def test_resurrection_after_thread_failure_requeues_context_expansion(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    tweet = _complex_tweet("100")
    unavailable_at = "2026-08-01T00:00:00+00:00"
    resurrected_at = "2026-08-02T00:00:00+00:00"
    store._merge_records(
        [
            store._record(
                row_key="tweet_object:100",
                record_type="tweet_object",
                tweet_id="100",
                enrichment_state="resurrected",
                enrichment_checked_at=resurrected_at,
            ),
            store._record(
                row_key="raw_capture:unavailable-thread-100",
                record_type="raw_capture",
                operation="ThreadExpandDetail",
                cursor_in="100",
                captured_at=unavailable_at,
            ),
        ]
    )

    assert list_raw_capture_target_ids(store, "ThreadExpandDetail") == ["100"]
    assert store.list_expanded_thread_target_ids() == []
    assert store.archive_stats().pending_thread_membership_count == 0

    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[tweet],
        last_head_tweet_id="100",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    assert store.archive_stats().pending_thread_membership_count == 1

    store.persist_thread_detail(
        focal_tweet_id="100",
        tweets=[tweet],
        raw_json=make_tweet_detail_response([tweet.raw_json]),
    )

    assert store.list_expanded_thread_target_ids() == ["100"]
    assert store.archive_stats().pending_thread_membership_count == 0
    store.close()


def test_archive_stats_bfs_depth(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    # Depth 0 (Root)
    store._merge_records(
        [
            store._record(
                record_type="tweet",
                row_key="tweet:100",
                tweet_id="100",
            )
        ]
    )

    # Depth 0 -> Depth 1 (100 -> 200)
    store._merge_records(
        [
            store._record(
                record_type="url_ref",
                row_key="url_ref:100:0",
                tweet_id="100",
                expanded_url="https://x.com/user/status/200",
            )
        ]
    )

    # Depth 1 -> Depth 2 (200 -> 300)
    store._merge_records(
        [
            store._record(
                record_type="url_ref",
                row_key="url_ref:200:0",
                tweet_id="200",
                expanded_url="https://x.com/user/status/300",
            )
        ]
    )

    # Depth 2 -> Depth 3 (300 -> 400)
    store._merge_records(
        [
            store._record(
                record_type="url_ref",
                row_key="url_ref:300:0",
                tweet_id="300",
                expanded_url="https://x.com/user/status/400",
            )
        ]
    )

    assert store.archive_stats(max_linked_depth=0).pending_thread_linked_status_count == 0
    assert store.archive_stats(max_linked_depth=1).pending_thread_linked_status_count == 1  # 200
    assert (
        store.archive_stats(max_linked_depth=2).pending_thread_linked_status_count == 2
    )  # 200, 300
    assert (
        store.archive_stats(max_linked_depth=3).pending_thread_linked_status_count == 3
    )  # 200, 300, 400

    store.close()


def test_archive_stats_bfs_depth_combines_quote_and_url_edges(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store._merge_records(
        [
            store._record(
                record_type="tweet",
                row_key="tweet:100",
                tweet_id="100",
            ),
            store._record(
                record_type="tweet_object",
                row_key="tweet_object:200",
                tweet_id="200",
            ),
            store._record(
                record_type="tweet_object",
                row_key="tweet_object:400",
                tweet_id="400",
            ),
            store._record(
                record_type="tweet_relation",
                row_key="tweet_relation:100:quote_of:200",
                tweet_id="100",
                relation_type="quote_of",
                target_tweet_id="200",
            ),
            store._record(
                record_type="url_ref",
                row_key="url_ref:200:0",
                tweet_id="200",
                expanded_url="https://x.com/user/status/300",
            ),
            store._record(
                record_type="tweet_relation",
                row_key="tweet_relation:300:quote_of:400",
                tweet_id="300",
                relation_type="quote_of",
                target_tweet_id="400",
            ),
        ]
    )

    assert store.archive_stats(max_linked_depth=0).pending_thread_linked_status_count == 0
    assert store.archive_stats(max_linked_depth=1).pending_thread_linked_status_count == 1
    assert store.archive_stats(max_linked_depth=2).pending_thread_linked_status_count == 2
    assert store.archive_stats(max_linked_depth=3).pending_thread_linked_status_count == 3

    store.close()


def test_archive_stats_parses_each_date_once_and_only_when_requested(paths, monkeypatch) -> None:
    import tweetnook.storage.stats as stats_module

    store = open_archive_store(paths, create=True)
    assert store is not None
    store.merge_rows(
        [
            {
                "row_key": f"tweet:like:_:{tweet_id}",
                "record_type": "tweet",
                "tweet_id": tweet_id,
                "collection_type": "like",
                "created_at": created_at,
            }
            for tweet_id, created_at in (
                ("1", "Tue Oct 09 21:39:26 +0000 2012"),
                ("2", "Sat Mar 14 00:00:00 +0000 2026"),
            )
        ]
    )
    calls = []
    parse = stats_module.parse_created_at
    monkeypatch.setattr(
        stats_module, "parse_created_at", lambda raw: (calls.append(raw), parse(raw))[1]
    )
    try:
        stats = store.archive_stats()
        assert len(calls) == 2
        assert stats.oldest_created_at == "Tue Oct 09 21:39:26 +0000 2012"
        assert stats.newest_created_at == "Sat Mar 14 00:00:00 +0000 2026"

        calls.clear()
        assert store.archive_stats(include_dates=False).oldest_created_at is None
        assert calls == []
    finally:
        store.close()
