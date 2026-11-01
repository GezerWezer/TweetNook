"""Which writer may change each field family, one source pair at a time."""

from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import (
    make_tweet_detail_response,
    make_tweet_result,
    make_url_entity,
    make_video_media,
)
from tweetnook.core.extractor import (
    ExtractedTweetGraph,
    UrlData,
    extract_author_fields,
    extract_canonical_text,
    extract_secondary_objects,
)
from tweetnook.core.timeline_parsing import TimelineTweet
from tweetnook.enrichment.availability import record_unavailable
from tweetnook.storage import open_archive_store
from tweetnook.storage.ownership import worker_changes
from tweetnook.storage.record_types import RecordType
from tweetnook.storage.records import PageBuffer
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


def _two_videos(first: str, second: str) -> dict[str, Any]:
    return make_tweet_result(
        "100",
        "two videos",
        media=[
            make_video_media(
                first,
                f"https://pbs.twimg.com/{first}.jpg",
                bitrate_url="https://video.twimg.com/a.mp4",
            ),
            make_video_media(
                second,
                f"https://pbs.twimg.com/{second}.jpg",
                bitrate_url="https://video.twimg.com/b.mp4",
            ),
        ],
    )


def _media_positions(store: Any) -> dict[str, tuple[str, str]]:
    return {
        row["media_key"]: (str(row["position"]), row["provenance_source"])
        for row in store.conn.execute(
            "SELECT media_key, position, provenance_source FROM archive WHERE record_type = 'media'"
        )
    }


@pytest.mark.parametrize(
    ("first_source", "second_source", "expected"),
    [
        # A weaker source can't reorder what a stronger one saved.
        ("live_graphql", "x_archive", {"7_1": "0", "7_2": "1"}),
        ("live_graphql", "fxtwitter", {"7_1": "0", "7_2": "1"}),
        ("fxtwitter", "x_archive", {"7_1": "0", "7_2": "1"}),
        # A stronger or equal source's order wins.
        ("x_archive", "live_graphql", {"7_1": "1", "7_2": "0"}),
        ("live_graphql", "live_graphql", {"7_1": "1", "7_2": "0"}),
    ],
)
def test_media_position_follows_the_winning_source(
    paths, first_source: str, second_source: str, expected: dict[str, str]
) -> None:
    store = open_archive_store(paths, create=True)
    for raw, source in (
        (_two_videos("7_1", "7_2"), first_source),
        (_two_videos("7_2", "7_1"), second_source),
    ):
        buffer = PageBuffer()
        store._buffer_secondary_graph(extract_secondary_objects(raw), source=source, cursor=buffer)
        store.flush(buffer)

    positions = _media_positions(store)
    assert {key: value[0] for key, value in positions.items()} == expected
    store.close()


def test_export_keeps_each_video_with_its_own_geometry_after_a_weaker_copy(paths) -> None:
    def video(key: str, width: int, height: int) -> dict[str, Any]:
        media = make_video_media(
            key,
            f"https://pbs.twimg.com/{key}.jpg",
            bitrate_url=f"https://video.twimg.com/{key}.mp4",
        )
        media["video_info"]["aspect_ratio"] = [width, height]
        return media

    store = open_archive_store(paths, create=True)
    landscape, portrait = video("7_1", 16, 9), video("7_2", 9, 16)
    persist_like_page(store, make_tweet_result("100", "two videos", media=[landscape, portrait]))
    buffer = PageBuffer()
    store._buffer_secondary_graph(
        extract_secondary_objects(
            make_tweet_result("100", "two videos", media=[portrait, landscape])
        ),
        source="x_archive",
        cursor=buffer,
    )
    store.flush(buffer)

    media = store.export_rows("all")[0]["media"]
    assert [item["media_key"] for item in media] == ["7_1", "7_2"]
    assert [item["aspect_ratio"] for item in media] == [[16, 9], [9, 16]]
    store.close()


URL_ENTITY = make_url_entity(
    "https://t.co/a", "https://example.com/story", display_url="example.com/story"
)


def _url_row(store: Any) -> dict[str, Any]:
    return store.list_url_rows()[0]


def _store_with_unfurl_result(paths, *, failed: bool) -> Any:
    store = open_archive_store(paths, create=True)
    persist_like_page(store, make_tweet_result("100", "linked", urls=[URL_ENTITY]))
    store.merge_rows(
        [
            store.build_url_unfurl_update(
                _url_row(store),
                http_status=503 if failed else 200,
                final_url=None if failed else "https://www.example.com/final",
                canonical_url=_url_row(store)["canonical_url"]
                if failed
                else "https://www.example.com/clean",
                title=None if failed else "Fetched title",
                description=None if failed else "Fetched description",
                site_name=None if failed else "Fetched site",
                content_type=None if failed else "text/html",
                unfurl_state="failed" if failed else "done",
                last_fetched_at="2026-05-01T00:00:00+00:00",
                download_error="HTTP 503" if failed else None,
            )
        ]
    )
    return store


def _reingest(store: Any, source: str) -> None:
    buffer = PageBuffer()
    store._buffer_secondary_graph(
        extract_secondary_objects(make_tweet_result("100", "linked", urls=[URL_ENTITY])),
        source=source,
        cursor=buffer,
    )
    store.flush(buffer)


UNFURL_FIELDS = (
    "url",
    "final_url",
    "canonical_url",
    "http_status",
    "title",
    "description",
    "site_name",
    "content_type",
    "unfurl_state",
)


@pytest.mark.parametrize("source", ["live_graphql", "fxtwitter", "x_archive"])
def test_reingesting_a_post_keeps_a_finished_unfurl(paths, source: str) -> None:
    store = _store_with_unfurl_result(paths, failed=False)
    expected = {field: _url_row(store)[field] for field in UNFURL_FIELDS}
    assert expected["final_url"] == "https://www.example.com/final"
    assert str(expected["http_status"]) == "200"
    url_hash = _url_row(store)["url_hash"]

    _reingest(store, source)

    row = _url_row(store)
    assert {field: row[field] for field in UNFURL_FIELDS} == expected
    assert row["url_hash"] == url_hash
    store.close()


@pytest.mark.parametrize("source", ["live_graphql", "x_archive"])
def test_reingesting_a_post_keeps_a_failed_unfurls_evidence(paths, source: str) -> None:
    store = _store_with_unfurl_result(paths, failed=True)

    _reingest(store, source)

    row = store.conn.execute(
        "SELECT unfurl_state, http_status, download_error FROM archive WHERE record_type = 'url'"
    ).fetchone()
    assert (row["unfurl_state"], str(row["http_status"]), row["download_error"]) == (
        "failed",
        "503",
        "HTTP 503",
    )
    store.close()


def test_entity_hints_update_only_a_url_the_worker_has_not_fetched(paths) -> None:
    store = open_archive_store(paths, create=True)
    persist_like_page(store, make_tweet_result("100", "linked", urls=[URL_ENTITY]))
    row = _url_row(store)

    def hint(title: str) -> None:
        graph = ExtractedTweetGraph()
        graph.add_url(
            UrlData(
                url_hash=row["url_hash"],
                canonical_url=row["canonical_url"],
                expanded_url=None,
                host=None,
                raw_json={},
                title=title,
            )
        )
        buffer = PageBuffer()
        store._buffer_secondary_graph(graph, cursor=buffer)
        store.flush(buffer)

    hint("First hint")
    hint("Second hint")
    assert _url_row(store)["title"] == "Second hint"

    store.merge_rows(
        [
            store.build_url_unfurl_update(
                _url_row(store),
                http_status=200,
                final_url=row["canonical_url"],
                canonical_url=row["canonical_url"],
                title="Fetched title",
                description=None,
                site_name=None,
                content_type="text/html",
                unfurl_state="done",
                last_fetched_at="2026-05-01T00:00:00+00:00",
                download_error=None,
            )
        ]
    )
    hint("Later hint")
    assert _url_row(store)["title"] == "Fetched title"
    store.close()
