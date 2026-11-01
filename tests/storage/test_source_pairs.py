"""Every field family, for every ordered pair of sources: who owns the saved value afterwards."""

from __future__ import annotations

import json
from itertools import permutations
from typing import Any

import pytest

from tests.conftest import make_photo_media, make_tweet_detail_response, make_tweet_result
from tests.storage.test_field_ownership import (
    lifecycle,
    mark_not_found,
    persist_from,
    persist_like_page,
)
from tweetnook.storage import open_archive_store

# The stronger source of each pair, written out rather than read from the code's own ranks.
STRONGER = {
    frozenset({"live_graphql", "fxtwitter"}): "live_graphql",
    frozenset({"live_graphql", "x_archive"}): "live_graphql",
    frozenset({"fxtwitter", "x_archive"}): "fxtwitter",
}
PAIRS = list(permutations(["live_graphql", "fxtwitter", "x_archive"], 2))
URL = "https://t.co/link"


def _post(source: str) -> dict[str, Any]:
    # Each source reports its own text and URL hint, and lists the two photos in its own order.
    photos = [
        make_photo_media("3_1", f"https://pbs.twimg.com/{source}-1.jpg"),
        make_photo_media("3_2", f"https://pbs.twimg.com/{source}-2.jpg"),
    ]
    return make_tweet_result(
        "100",
        f"text from {source}",
        media=photos if source == "live_graphql" else photos[::-1],
        urls=[
            {
                "url": URL,
                "expanded_url": "https://dest.example/story",
                "display_url": f"{source}.example",
            }
        ],
    )


def _row(store: Any, record_type: str, **match: str) -> dict[str, Any]:
    conditions = " AND ".join(f"{field} = ?" for field in match)
    row = store.conn.execute(
        f"SELECT * FROM archive WHERE record_type = ? AND {conditions}",
        (record_type, *match.values()),
    ).fetchone()
    return dict(row)


@pytest.fixture(params=PAIRS, ids=lambda pair: f"{pair[0]}-then-{pair[1]}")
def saved(request, paths):
    first, second = request.param
    store = open_archive_store(paths, create=True)
    persist_from(store, first, _post(first))
    worker_row = next(row for row in store.list_media_rows() if row["media_key"] == "3_1")
    store.merge_rows(
        [
            store.build_media_download_update(
                worker_row,
                download_state="done",
                local_path="media/100/3_1.jpg",
                sha256="saved-sha",
                byte_size=5,
                content_type="image/jpeg",
                thumbnail_local_path=None,
                thumbnail_sha256=None,
                thumbnail_byte_size=None,
                thumbnail_content_type=None,
                downloaded_at="2026-01-02T03:04:05+00:00",
                download_error=None,
            )
        ]
    )
    persist_from(store, second, _post(second))
    yield store, STRONGER[frozenset(request.param)]
    store.close()


def test_content_comes_from_the_stronger_source(saved) -> None:
    store, stronger = saved
    assert _row(store, "tweet_object", tweet_id="100")["text"] == f"text from {stronger}"


def test_the_payload_comes_from_the_stronger_source(saved) -> None:
    store, stronger = saved
    raw = json.loads(_row(store, "tweet_object", tweet_id="100")["raw_json"])
    assert raw["legacy"]["full_text"] == f"text from {stronger}"


def test_provenance_names_the_stronger_source(saved) -> None:
    store, stronger = saved
    assert _row(store, "tweet_object", tweet_id="100")["source"] == stronger
    assert _row(store, "media", media_key="3_2")["provenance_source"] == stronger


def test_media_content_comes_from_the_stronger_source(saved) -> None:
    store, stronger = saved
    assert _row(store, "media", media_key="3_2")["media_url"].endswith(f"/{stronger}-2.jpg")


def test_position_comes_from_the_stronger_source(saved) -> None:
    store, stronger = saved
    expected = {"3_1": "0", "3_2": "1"} if stronger == "live_graphql" else {"3_1": "1", "3_2": "0"}
    assert {
        key: str(_row(store, "media", media_key=key)["position"]) for key in ("3_1", "3_2")
    } == expected


def test_worker_results_outlast_either_source(saved) -> None:
    store, _stronger = saved
    row = _row(store, "media", media_key="3_1")
    assert (row["download_state"], row["local_path"], row["sha256"], row["downloaded_at"]) == (
        "done",
        "media/100/3_1.jpg",
        "saved-sha",
        "2026-01-02T03:04:05+00:00",
    )


def test_lifecycle_is_unchanged_by_replaying_what_either_source_said(saved) -> None:
    store, _stronger = saved
    # Rehydration starts from memberships, so it needs one to reach the saved captures.
    persist_like_page(store, make_tweet_result("900", "unrelated"))
    raw = make_tweet_result("100", "saved text")
    store.append_raw_capture("TweetDetail", "100", None, 200, make_tweet_detail_response([raw]))
    mark_not_found(store, "100")
    expected = lifecycle(store, "100")
    assert expected["enrichment_state"] == "terminal_unavailable"

    store.rehydrate_from_raw_json()

    assert lifecycle(store, "100") == expected
