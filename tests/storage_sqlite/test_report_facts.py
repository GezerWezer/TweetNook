from __future__ import annotations

from tweetnook.core.timeline_parsing import TimelineTweet
from tweetnook.stats import build_stats_report, build_stats_section
from tweetnook.storage import ArchiveStore


def _save_members(store, dates):
    store.persist_page(
        operation="Likes",
        collection_type="like",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"synthetic": True},
        tweets=[
            TimelineTweet(
                tweet_id=tweet_id,
                text="saved",
                author_id="author",
                author_username="alice",
                author_display_name="Alice",
                created_at=created_at,
                sort_index=tweet_id,
                raw_json={
                    "rest_id": tweet_id,
                    "legacy": {"full_text": "saved", "created_at": created_at},
                },
            )
            for tweet_id, created_at in dates
        ],
        last_head_tweet_id=dates[-1][0],
        backfill_cursor=None,
        backfill_incomplete=False,
    )


def test_report_keeps_one_snapshot_when_another_writer_commits_between_sections(
    tmp_path, monkeypatch
):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    _save_members(store, [("1", "Sat Mar 14 00:00:00 +0000 2026")])
    original = ArchiveStore.overview_facts
    inserted = False

    def interleaved(reader):
        nonlocal inserted
        facts = original(reader)
        if not inserted:
            inserted = True
            _save_members(store, [("2", "Sun Mar 15 00:00:00 +0000 2026")])
        return facts

    monkeypatch.setattr(ArchiveStore, "overview_facts", interleaved)
    try:
        report = build_stats_report(store, include={"overview", "collections"})
        assert report.section("overview").data["unique_posts"] == 1
        like = next(row for row in report.section("collections").rows if row["raw_type"] == "like")
        assert like["count"] == 1
        assert build_stats_section(store, "overview").data["unique_posts"] == 2
    finally:
        store.close()


def test_collection_and_owner_only_reports_avoid_the_health_graph(tmp_path, monkeypatch):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    _save_members(store, [("1", "Sat Mar 14 00:00:00 +0000 2026")])
    store.set_archive_owner_id("owner")

    def unexpected(*args, **kwargs):
        raise AssertionError("unrequested health graph")

    monkeypatch.setattr(ArchiveStore, "archive_stats", unexpected)
    try:
        collections = build_stats_section(store, "collections")
        assert [row["count"] for row in collections.rows] == [0, 1, 0]
        assert build_stats_report(store, include={"tagging"}).owner == "owner"
    finally:
        store.close()


def test_overview_and_collections_share_valid_date_bounds(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    valid = "Sat Mar 14 00:00:00 +0000 2026"
    _save_members(store, [("1", valid), ("2", "not a date")])
    try:
        report = build_stats_report(store, include={"overview", "collections"})
        overview = report.section("overview").data
        like = next(row for row in report.section("collections").rows if row["raw_type"] == "like")
        assert (overview["oldest_post"], overview["newest_post"]) == (valid, valid)
        assert (like["oldest"], like["newest"]) == (valid, valid)
    finally:
        store.close()


def test_tag_average_uses_the_same_archive_wide_population_as_tag_instances(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    store.merge_rows(
        [
            {
                "row_key": "tweet:like::1",
                "record_type": "tweet",
                "tweet_id": "1",
                "collection_type": "like",
            },
            {
                "row_key": "tweet_object:1",
                "record_type": "tweet_object",
                "tweet_id": "1",
                "enrichment_state": "done",
            },
            {
                "row_key": "tweet_object:3",
                "record_type": "tweet_object",
                "tweet_id": "3",
                "enrichment_state": "terminal_unavailable",
            },
        ]
    )
    store.update_media_tags("1", ["A", "B"])
    store.update_media_tags("2", ["C", "D", "E", "F"])
    store.update_media_tags("3", ["G", "H", "I"])
    try:
        data = build_stats_section(store, "tagging").data
        assert (data["eligible_tweets"], data["tagged_tweets"]) == (1, 1)
        assert data["total_tag_instances"] == 9
        assert data["avg_tags_per_tweet"] == 3.0
        with store.transaction():
            store.conn.execute("DELETE FROM archive WHERE record_type='tweet'")
        data = build_stats_section(store, "tagging").data
        assert data["eligible_tweets"] == 0
        assert data["avg_tags_per_tweet"] == 3.0
    finally:
        store.close()
