"""Which writer may change each field family, one source pair at a time."""

from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import make_tweet_detail_response, make_tweet_result
from tweetnook.core.extractor import extract_author_fields, extract_canonical_text
from tweetnook.core.timeline_parsing import TimelineTweet
from tweetnook.enrichment.availability import record_unavailable
from tweetnook.storage import open_archive_store
from tweetnook.storage.ownership import worker_changes
from tweetnook.storage.record_types import RecordType
from tweetnook.storage.search_query import search_posts

LIFECYCLE = (
    "enrichment_state",
    "enrichment_checked_at",
    "enrichment_http_status",
    "enrichment_reason",
    "enrichment_detail",
    "enrichment_retry_count",
    "enrichment_next_retry_at",
    "enrichment_first_unavailable_at",
    "enrichment_retry_eligible",
)


def timeline_tweet(raw: dict[str, Any], *, sort_index: str = "10") -> TimelineTweet:
    author_id, username, display_name = extract_author_fields(raw)
    return TimelineTweet(
        tweet_id=str(raw["rest_id"]),
        text=extract_canonical_text(raw),
        author_id=author_id,
        author_username=username,
        author_display_name=display_name,
        created_at=raw["legacy"].get("created_at"),
        sort_index=sort_index,
        raw_json=raw,
    )


def persist_like_page(store: Any, *raws: dict[str, Any]) -> None:
    store.persist_page(
        operation="Likes",
        collection_type="like",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[timeline_tweet(raw) for raw in raws],
        last_head_tweet_id=None,
        backfill_cursor=None,
        backfill_incomplete=False,
    )


def mark_not_found(store: Any, tweet_id: str) -> None:
    store.persist_unavailable_tweet(
        tweet_id=tweet_id,
        operation="TweetDetail",
        raw_json={"errors": [{"message": "not found"}]},
        http_status=404,
        reason="not_found",
        detail="gone",
        retry_eligible=True,
        next_retry_at="2099-01-01T00:00:00+00:00",
        retry_count=2,
        checked_at="2026-05-01T00:00:00+00:00",
    )


def lifecycle(store: Any, tweet_id: str) -> dict[str, Any]:
    row = store.current_objects([tweet_id])[tweet_id]
    return {field: row[field] for field in LIFECYCLE}


@pytest.mark.parametrize("replayed_capture", ["membership", "saved_thread_detail"])
def test_replay_keeps_a_newer_unavailable_observation(paths, replayed_capture: str) -> None:
    store = open_archive_store(paths, create=True)
    if replayed_capture == "membership":
        persist_like_page(store, make_tweet_result("100", "saved text"))
    else:
        # Rehydration starts from memberships, so it needs one to reach the saved details.
        persist_like_page(store, make_tweet_result("900", "unrelated"))
        raw = make_tweet_result("100", "saved text")
        store.persist_thread_detail(
            focal_tweet_id="100",
            tweets=[timeline_tweet(raw)],
            raw_json=make_tweet_detail_response([raw]),
        )
    mark_not_found(store, "100")
    expected = lifecycle(store, "100")
    assert expected["enrichment_state"] == "terminal_unavailable"
    assert str(expected["enrichment_http_status"]) == "404"

    store.rehydrate_from_raw_json()

    assert lifecycle(store, "100") == expected
    store.close()


def test_a_real_response_still_ends_an_unavailable_state(paths) -> None:
    store = open_archive_store(paths, create=True)
    persist_like_page(store, make_tweet_result("100", "saved text"))
    mark_not_found(store, "100")

    persist_like_page(store, make_tweet_result("100", "saved text"))

    state = lifecycle(store, "100")
    assert state["enrichment_state"] == "resurrected"
    assert str(state["enrichment_http_status"]) == "200"
    assert state["enrichment_reason"] is None
    store.close()


def test_a_worker_result_cannot_write_another_owners_field() -> None:
    assert worker_changes(RecordType.MEDIA, {"local_path": "a.jpg"}) == {"local_path": "a.jpg"}
    with pytest.raises(ValueError, match="media_url"):
        worker_changes(RecordType.MEDIA, {"media_url": "https://example.com/a.jpg"})
    with pytest.raises(ValueError, match="local_path"):
        worker_changes(RecordType.URL, {"local_path": "a.jpg"})


@pytest.mark.parametrize("stale_timestamp", [None, 0])
def test_rehydration_repairs_a_date_and_its_timestamp_together(paths, stale_timestamp) -> None:
    store = open_archive_store(paths, create=True)
    persist_like_page(store, make_tweet_result("100", "saved text"))
    row = store.conn.execute("SELECT * FROM archive WHERE row_key = 'tweet:like::100'").fetchone()
    store.merge_rows([{**dict(row), "created_at": None, "created_at_ts": stale_timestamp}])

    assert store.rehydrate_from_raw_json().tweets_updated == 1

    repaired = store.conn.execute(
        "SELECT created_at, created_at_ts FROM archive WHERE row_key = 'tweet:like::100'"
    ).fetchone()
    # 2026-03-14T00:00:00Z, the date make_tweet_result writes.
    assert tuple(repaired) == ("Sat Mar 14 00:00:00 +0000 2026", 1773446400)
    assert [row["tweet_id"] for row in search_posts(store, "since:2026-03-01").rows] == ["100"]
    store.close()


@pytest.mark.parametrize(
    ("operation", "expected_source"),
    [("FxTwitterStatus", "fxtwitter"), ("TweetDetailResurrection", "live_graphql")],
)
def test_an_unavailable_capture_names_the_provider_that_answered(
    paths, operation: str, expected_source: str
) -> None:
    store = open_archive_store(paths, create=True)
    persist_like_page(store, make_tweet_result("100", "saved text"))

    record_unavailable(
        store,
        tweet_id="100",
        operation=operation,
        payload={"code": 404},
        http_status=404,
        reason="not_found",
        detail=None,
    )

    captures = store.list_target_captures("100", (operation,))
    assert [row["source"] for row in captures] == [expected_source]
    # The body still came from the authenticated page.
    assert store.current_objects(["100"])["100"]["source"] == "live_graphql"
    store.close()
