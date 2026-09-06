from __future__ import annotations

import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from tweetnook.stats import STATS_SECTION_SPECS, build_stats_report
from tweetnook.storage.backend import ArchiveCollectionStats, ArchiveStats, ArchiveStore
from tweetnook.web.routes import stats as stats_routes
from tweetnook.web.stats_cache import WebStatsCache


def _stats_store(tmp_path: Path) -> ArchiveStore:
    return ArchiveStore(tmp_path / "archive.db", create=True)


def _seed_tag_stats_tweet(
    store: ArchiveStore,
    tweet_id: str,
    *,
    membership: bool = True,
    enrichment_state: str = "done",
    media: bool = True,
) -> None:
    rows = [
        store._record(
            row_key=f"tweet_object:{tweet_id}",
            record_type="tweet_object",
            tweet_id=tweet_id,
            enrichment_state=enrichment_state,
        )
    ]
    if membership:
        rows.append(
            store._record(
                row_key=f"tweet:like::{tweet_id}",
                record_type="tweet",
                tweet_id=tweet_id,
                collection_type="like",
            )
        )
    if media:
        rows.append(
            store._record(
                row_key=f"media:{tweet_id}:photo",
                record_type="media",
                tweet_id=tweet_id,
                media_key="photo",
            )
        )
    store._merge_records(rows)


def test_summary_reports_counts_ranges_owner_and_latest_sync(tmp_path, make_web_client) -> None:
    store = _stats_store(tmp_path)
    store.conn.executemany(
        """
        INSERT INTO archive (
            record_type, tweet_id, author_id, created_at, created_at_ts, updated_at, key, value
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "tweet",
                "t1",
                "a1",
                "Tue Nov 14 22:13:20 +0000 2023",
                1_700_000_000,
                None,
                None,
                None,
            ),
            (
                "tweet",
                "t2",
                "a2",
                "Fri Jan 15 08:00:00 +0000 2027",
                1_800_000_000,
                None,
                None,
                None,
            ),
            ("article", None, None, None, None, None, None, None),
            ("media", "t1", "a1", None, None, None, None, None),
            ("url", "t1", None, None, None, None, None, None),
            ("sync_state", None, None, None, None, "2025-06-15T00:00:00Z", None, None),
            ("metadata", None, None, None, None, None, "owner_user_id", "owner-42"),
        ],
    )
    store.conn.executemany(
        "INSERT INTO archive (record_type, tweet_id, enrichment_state) VALUES (?, ?, ?)",
        [
            ("tweet_object", "t1", "done"),
            ("tweet_object", "t2", "terminal_unavailable"),
        ],
    )
    store.set_archive_owner_id("owner-42")
    store.conn.execute(
        "UPDATE archive SET collection_type = 'bookmark' WHERE record_type = 'sync_state'"
    )
    client = make_web_client(stats_routes.router, store=store)

    response = client.get("/api/stats/summary")

    assert response.status_code == 200
    assert response.json() == {
        "owner_user_id": "owner-42",
        "unique_posts": 2,
        "articles": 1,
        "media_rows": 1,
        "urls": 1,
        "profiles": 2,
        "archive_tweets": 2,
        "missing_archive_tweets": 1,
        "missing_archive_pct": 50.0,
        "oldest_post": "Nov 14, 2023",
        "newest_post": "Jan 15, 2027",
        "latest_sync": "Jun 15, 2025",
    }


def test_summary_uses_empty_defaults(tmp_path, make_web_client) -> None:
    store = _stats_store(tmp_path)
    client = make_web_client(stats_routes.router, store=store)

    data = client.get("/api/stats/summary").json()

    assert data == {
        "owner_user_id": "Local Vault",
        "unique_posts": 0,
        "articles": 0,
        "media_rows": 0,
        "urls": 0,
        "profiles": 0,
        "archive_tweets": 0,
        "missing_archive_tweets": 0,
        "missing_archive_pct": 0.0,
        "oldest_post": None,
        "newest_post": None,
        "latest_sync": None,
    }


def test_report_exposes_the_shared_ordered_section_registry(tmp_path, make_web_client) -> None:
    store = _stats_store(tmp_path)
    client = make_web_client(stats_routes.router, store=store)

    response = client.get("/api/stats/report")

    assert response.status_code == 200
    data = response.json()
    assert [section["id"] for section in data["sections"]] == [
        spec.id for spec in STATS_SECTION_SPECS
    ]
    assert [section["title"] for section in data["sections"]] == [
        spec.title for spec in STATS_SECTION_SPECS
    ]
    assert data["archive_path"] == str(store.db_path)


def test_cached_snapshot_serves_old_data_during_manual_refresh(
    tmp_path,
    make_web_client,
    monkeypatch,
) -> None:
    store = _stats_store(tmp_path)
    _seed_tag_stats_tweet(store, "t1")
    refresh_started = Event()
    allow_refresh = Event()
    calls = 0

    def build(store_arg):
        nonlocal calls
        calls += 1
        if calls == 2:
            refresh_started.set()
            assert allow_refresh.wait(timeout=2)
        return build_stats_report(store_arg)

    cache = WebStatsCache(builder=build)
    monkeypatch.setattr(stats_routes, "web_stats_cache", cache)
    client = make_web_client(stats_routes.router, store=store)

    initial = client.get("/api/stats/snapshot?revalidate=false").json()
    _seed_tag_stats_tweet(store, "t2")
    refreshing = client.post("/api/stats/refresh").json()

    assert refresh_started.wait(timeout=2)
    assert initial["summary"]["unique_posts"] == 1
    assert refreshing["refreshing"] is True
    assert refreshing["generated_at"] == initial["generated_at"]
    assert set(refreshing) == {
        "generated_at",
        "age_seconds",
        "stale",
        "refreshing",
        "refresh_failed",
    }

    allow_refresh.set()
    cache.wait_for_refreshes()
    refreshed = client.get("/api/stats/snapshot?revalidate=false").json()

    assert refreshed["summary"]["unique_posts"] == 2
    assert refreshed["refreshing"] is False
    assert refreshed["refresh_failed"] is False
    assert calls == 2


def test_cached_status_returns_refresh_metadata_without_report_sections(
    tmp_path,
    make_web_client,
    monkeypatch,
) -> None:
    store = _stats_store(tmp_path)
    _seed_tag_stats_tweet(store, "t1")
    cache = WebStatsCache()
    monkeypatch.setattr(stats_routes, "web_stats_cache", cache)
    client = make_web_client(stats_routes.router, store=store)
    initial = client.get("/api/stats/snapshot?revalidate=false").json()

    response = client.get("/api/stats/status")

    assert response.status_code == 200
    assert response.json() == {
        "generated_at": initial["generated_at"],
        "age_seconds": pytest.approx(0, abs=0.1),
        "stale": False,
        "refreshing": False,
        "refresh_failed": False,
    }


def test_enrichment_banner_uses_the_lightweight_shared_count(tmp_path, make_web_client) -> None:
    store = _stats_store(tmp_path)
    store.conn.executemany(
        "INSERT INTO archive (record_type, enrichment_state) VALUES ('tweet_object', ?)",
        [("pending",), ("transient_failure",), ("done",)],
    )
    client = make_web_client(stats_routes.router, store=store)

    response = client.get("/api/stats/enrichment-incomplete")

    assert response.status_code == 200
    assert response.json() == {"incomplete": 2}


def test_latest_sync_endpoint_avoids_full_statistics_collection(
    tmp_path,
    make_web_client,
    monkeypatch,
) -> None:
    store = _stats_store(tmp_path)
    store.conn.execute(
        """
        INSERT INTO archive (record_type, collection_type, updated_at)
        VALUES ('sync_state', 'bookmark', '2026-08-10T14:30:00Z')
        """
    )
    monkeypatch.setattr(
        stats_routes,
        "build_stats_section",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("full stats called")),
    )
    client = make_web_client(stats_routes.router, store=store)

    response = client.get("/api/stats/latest-sync")

    assert response.status_code == 200
    assert response.json() == {"latest_sync": "Aug 10, 2026"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, "-"),
        ("", "-"),
        ("2026-03-14T01:02:00", "Mar 14, 2026 1:02 am"),
        ("not-a-date", "not-a-date"),
    ],
)
def test_format_stats_timestamp(raw: str | None, expected: str) -> None:
    assert stats_routes._format_stats_timestamp(raw) == expected


def test_collections_formats_names_and_backfill_states(make_web_client) -> None:
    archive_stats = ArchiveStats(
        collections=[
            ArchiveCollectionStats(
                collection_type="bookmark",
                post_count=3,
                backfill_incomplete=True,
                backfill_cursor="cursor",
            ),
            ArchiveCollectionStats(
                collection_type="like",
                post_count=2,
                backfill_incomplete=True,
            ),
            ArchiveCollectionStats(
                collection_type="tweet",
                post_count=1,
                backfill_cursor="saved",
            ),
            ArchiveCollectionStats(collection_type="custom", post_count=4),
        ]
    )
    store = SimpleNamespace(archive_stats=lambda: archive_stats)
    client = make_web_client(stats_routes.router, store=store)

    response = client.get("/api/stats/collections")

    assert response.status_code == 200
    rows = response.json()
    assert [(row["collection"], row["count"], row["backfill_status"]) for row in rows] == [
        ("Bookmarks", 3, "resume older"),
        ("Likes", 2, "incomplete"),
        ("Authored Tweets", 1, "saved cursor"),
        ("Custom", 4, "none saved"),
    ]
    assert all(row["oldest"] == row["newest"] == row["last_synced"] == "-" for row in rows)


def test_health_reports_pipeline_counts(tmp_path, make_web_client) -> None:
    store = _stats_store(tmp_path)
    store.conn.executemany(
        """
        INSERT INTO archive (
            record_type, enrichment_state, enrichment_reason,
            enrichment_retry_eligible, enrichment_next_retry_at,
            conversation_id, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("tweet_object", "done", None, None, None, None, None),
            ("tweet_object", "done", None, None, None, None, None),
            ("tweet_object", "resurrected", None, None, None, None, None),
            ("tweet_object", "pending", None, None, None, None, None),
            ("tweet_object", "transient_failure", None, None, None, None, None),
            (
                "tweet_object",
                "terminal_unavailable",
                "protected_account",
                1,
                None,
                None,
                None,
            ),
            ("tweet", None, None, None, None, "c1", None),
            ("tweet", None, None, None, None, "c1", None),
            ("tweet", None, None, None, None, "c2", None),
            ("article", None, None, None, None, None, "preview_only"),
            ("article", None, None, None, None, None, "body_present"),
        ],
    )
    store.conn.executemany(
        """
        INSERT INTO archive (record_type, operation, cursor_in)
        VALUES ('raw_capture', 'ThreadExpandDetail', ?)
        """,
        [("t1",), ("t2",)],
    )
    client = make_web_client(stats_routes.router, store=store)

    response = client.get("/api/stats/health")

    assert response.status_code == 200
    assert response.json() == {
        "enrichment": {
            "available": 3,
            "done": 2,
            "resurrected": 1,
            "pending": 1,
            "transient": 1,
            "incomplete": 2,
            "terminal": 1,
            "unavailable": {
                "total": 1,
                "percent_of_archive": 16.7,
                "retryable": 1,
                "due": 1,
                "delayed": 0,
                "permanent": 0,
                "reasons": response.json()["enrichment"]["unavailable"]["reasons"],
            },
        },
        "threads_expanded": 2,
        "preview_articles": 1,
    }
    reasons = {
        item["reason"]: item for item in response.json()["enrichment"]["unavailable"]["reasons"]
    }
    assert list(reasons) == [
        "protected_account",
        "suspended_account",
        "account_missing",
        "withheld",
        "not_found",
        "unavailable_unknown",
        "deleted_by_author",
        "archive_deleted",
    ]
    assert reasons["protected_account"] == {
        "reason": "protected_account",
        "label": "Protected account",
        "count": 1,
        "percent_of_missing": 100.0,
        "percent_of_archive": 16.7,
        "retryable": 1,
        "due": 1,
        "delayed": 0,
        "permanent": 0,
    }
    assert reasons["unavailable_unknown"]["count"] == 0
    assert reasons["deleted_by_author"]["permanent"] == 0


def test_health_breaks_unavailable_tweets_down_by_reason_and_retry_state(
    tmp_path,
    make_web_client,
) -> None:
    store = _stats_store(tmp_path)
    store.conn.executemany(
        """
        INSERT INTO archive (
            record_type, enrichment_state, enrichment_reason,
            enrichment_retry_eligible, enrichment_next_retry_at, deleted_at
        ) VALUES ('tweet_object', 'terminal_unavailable', ?, ?, ?, ?)
        """,
        [
            ("unavailable_unknown", 1, "2999-01-01T00:00:00+00:00", None),
            ("unavailable_unknown", 1, None, None),
            ("suspended_account", 1, "2999-01-01T00:00:00+00:00", None),
            ("deleted_by_author", 0, None, "2026-08-01T00:00:00+00:00"),
        ],
    )
    store.conn.execute(
        "INSERT INTO archive (record_type, enrichment_state) VALUES ('tweet_object', 'done')"
    )
    client = make_web_client(stats_routes.router, store=store)

    data = client.get("/api/stats/health").json()["enrichment"]["unavailable"]

    assert data == {
        "total": 4,
        "percent_of_archive": 80.0,
        "retryable": 3,
        "due": 1,
        "delayed": 2,
        "permanent": 1,
        "reasons": data["reasons"],
    }
    reasons = {item["reason"]: item for item in data["reasons"]}
    assert reasons["unavailable_unknown"] == {
        "reason": "unavailable_unknown",
        "label": "Unknown availability",
        "count": 2,
        "percent_of_missing": 50.0,
        "percent_of_archive": 40.0,
        "retryable": 2,
        "due": 1,
        "delayed": 1,
        "permanent": 0,
    }
    assert reasons["suspended_account"]["delayed"] == 1
    assert reasons["deleted_by_author"]["permanent"] == 1
    assert reasons["archive_deleted"]["count"] == 0


def test_tag_stats_reports_case_insensitive_usage_and_coverage(tmp_path, make_web_client) -> None:
    store = _stats_store(tmp_path)
    for tweet_id in ("t1", "t2", "t3"):
        _seed_tag_stats_tweet(store, tweet_id)
    store._merge_records(
        [
            store._record(
                row_key="tweet:bookmark::t1",
                record_type="tweet",
                tweet_id="t1",
                collection_type="bookmark",
            )
        ]
    )
    _seed_tag_stats_tweet(store, "thread-only", membership=False)
    _seed_tag_stats_tweet(store, "pending", enrichment_state="pending")
    _seed_tag_stats_tweet(store, "text-only", media=False)
    store.conn.executemany(
        "INSERT INTO archive (row_key, record_type, tweet_id, raw_json) VALUES (?, ?, ?, ?)",
        [
            ("media:t1:second", "media", "t1", None),
            ("media_tag:t1", "media_tag", "t1", json.dumps({"tags": ["Nature", "Bird"]})),
            ("media_tag:t2", "media_tag", "t2", json.dumps({"tags": ["nature", "Sky"]})),
        ],
    )
    client = make_web_client(stats_routes.router, store=store)

    response = client.get("/api/stats/tags")

    assert response.status_code == 200
    data = response.json()
    assert data == {
        "eligible_tweets": 4,
        "tagged_tweets": 2,
        "untagged_eligible": 2,
        "unique_tags": 3,
        "total_tag_instances": 4,
        "coverage_pct": 50.0,
        "avg_tags_per_tweet": 2.0,
        "top_tags": data["top_tags"],
    }
    assert {item["tag"]: item["count"] for item in data["top_tags"]} == {
        "nature": 2,
        "bird": 1,
        "sky": 1,
    }


def test_tag_stats_handles_empty_archive(tmp_path, make_web_client) -> None:
    store = _stats_store(tmp_path)
    client = make_web_client(stats_routes.router, store=store)

    data = client.get("/api/stats/tags").json()

    assert data["coverage_pct"] == 0.0
    assert data["avg_tags_per_tweet"] == 0.0
    assert data["untagged_eligible"] == 0
    assert data["top_tags"] == []


def test_tag_stats_ignore_malformed_tag_json(tmp_path, make_web_client) -> None:
    store = _stats_store(tmp_path)
    _seed_tag_stats_tweet(store, "t1")
    _seed_tag_stats_tweet(store, "t2")
    store.conn.executemany(
        "INSERT INTO archive (row_key, record_type, tweet_id, raw_json) VALUES (?, ?, ?, ?)",
        [
            ("media_tag:t1", "media_tag", "t1", "not-json"),
            ("media_tag:t2", "media_tag", "t2", json.dumps({"tags": ["Bird"]})),
        ],
    )
    client = make_web_client(stats_routes.router, store=store)

    response = client.get("/api/stats/tags")

    assert response.status_code == 200
    assert response.json()["tagged_tweets"] == 1
    assert response.json()["unique_tags"] == 1
    assert response.json()["top_tags"] == [{"tag": "bird", "count": 1}]


def test_tag_stats_require_a_nonempty_tags_array(tmp_path, make_web_client) -> None:
    store = _stats_store(tmp_path)
    for tweet_id in ("t1", "t2", "t3"):
        _seed_tag_stats_tweet(store, tweet_id)
    store.conn.executemany(
        "INSERT INTO archive (row_key, record_type, tweet_id, raw_json) VALUES (?, ?, ?, ?)",
        [
            ("media_tag:t1", "media_tag", "t1", "{}"),
            ("media_tag:t2", "media_tag", "t2", '{"tags":[]}'),
            ("media_tag:t3", "media_tag", "t3", '{"tags":["Bird"]}'),
        ],
    )
    client = make_web_client(stats_routes.router, store=store)

    data = client.get("/api/stats/tags").json()

    assert data["tagged_tweets"] == 1
    assert data["untagged_eligible"] == 2
    assert data["coverage_pct"] == 33.3


def test_summary_and_health_follow_real_archive_schema(tmp_path, make_web_client) -> None:
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    store._merge_records(
        [
            store._record(
                row_key="tweet:bookmark::1",
                record_type="tweet",
                tweet_id="1",
                collection_type="bookmark",
                author_id="author-1",
                created_at_ts=1_700_000_000,
            ),
            store._record(
                row_key="tweet:like::1",
                record_type="tweet",
                tweet_id="1",
                collection_type="like",
                author_id="author-1",
                created_at_ts=1_700_000_000,
            ),
            store._record(
                row_key="sync_state:bookmark:",
                record_type="sync_state",
                collection_type="bookmark",
                updated_at="2025-06-15T00:00:00Z",
            ),
            store._record(
                row_key="article:1",
                record_type="article",
                tweet_id="1",
                status="preview_only",
            ),
        ]
    )
    client = make_web_client(stats_routes.router, store=store)

    summary = client.get("/api/stats/summary").json()
    health = client.get("/api/stats/health").json()

    assert summary["unique_posts"] == 1
    assert summary["latest_sync"] == "Jun 15, 2025"
    assert health["preview_articles"] == 1
    store.close()


def test_stats_routes_require_authentication(tmp_path, make_web_client) -> None:
    client = make_web_client(
        stats_routes.router,
        store=_stats_store(tmp_path),
        password="secret",
    )

    response = client.get("/api/stats/summary")

    assert response.status_code == 401
