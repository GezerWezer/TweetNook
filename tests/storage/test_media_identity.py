"""One media item keeps one row, whichever sources saw it and in whatever order."""

from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import make_tweet_result, make_video_media
from tests.storage.test_field_ownership import persist_from, persist_like_page
from tweetnook.core.extractor import extract_secondary_objects
from tweetnook.core.fxtwitter_payloads import fx_status_to_graphql
from tweetnook.importer.archive_rows import _media_key_for_archive_item
from tweetnook.storage import open_archive_store

VIDEO_URL = "https://video.twimg.com/ext_tw_video/555"
POSTER_URL = "https://pbs.twimg.com/ext_tw_video_thumb/555.jpg"

# Every prefix a source assigns to the same video: X, FxTwitter, an archive import.
KEY_FROM = {"live_graphql": "7_555", "fxtwitter": "13_555", "x_archive": "7_555"}
RANK_ORDER = ["x_archive", "fxtwitter", "live_graphql"]


def _video_post(source: str, *, key: str | None = None) -> dict[str, Any]:
    return make_tweet_result(
        "100",
        "a video",
        media=[
            make_video_media(
                key or KEY_FROM[source],
                POSTER_URL,
                bitrate_url=f"{VIDEO_URL}/{source}.mp4",
            )
        ],
    )


def _served_media(store: Any) -> list[dict[str, Any]]:
    return store.fetch_tweets_by_ids(["100"], include_objects=True)[0]["media"]


def _media_rows(store: Any) -> list[dict[str, Any]]:
    rows = store.conn.execute("SELECT * FROM archive WHERE record_type = 'media'").fetchall()
    return [dict(row) for row in rows]


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (first, second)
        for first in KEY_FROM
        for second in KEY_FROM
        if first != second and KEY_FROM[first] != KEY_FROM[second]
    ],
)
def test_one_video_from_two_sources_is_served_once(paths, first: str, second: str) -> None:
    store = open_archive_store(paths, create=True)

    persist_from(store, first, _video_post(first))
    persist_from(store, second, _video_post(second))

    rows = _media_rows(store)
    assert len(rows) == 1
    # The first source's key stays, because saved filenames are built from it.
    assert rows[0]["media_key"] == KEY_FROM[first]
    winner = max(first, second, key=RANK_ORDER.index)
    assert rows[0]["provenance_source"] == winner
    served = _served_media(store)
    assert len(served) == 1
    assert served[0]["url"].startswith(f"{VIDEO_URL}/{winner}")
    store.close()


def test_an_imported_gif_and_a_live_one_share_a_row(paths) -> None:
    def gif_post(key: str) -> dict[str, Any]:
        media = make_video_media(key, POSTER_URL, bitrate_url=f"{VIDEO_URL}/gif.mp4")
        media["type"] = "animated_gif"
        return make_tweet_result("100", "a gif", media=[media])

    store = open_archive_store(paths, create=True)
    # An older import keyed the GIF as a video.
    persist_from(store, "x_archive", gif_post("7_555"))
    persist_from(store, "live_graphql", gif_post("16_555"))

    assert [row["provenance_source"] for row in _media_rows(store)] == ["live_graphql"]
    assert len(_served_media(store)) == 1
    store.close()


def test_new_imports_key_a_gif_the_way_x_does() -> None:
    gif = {"id_str": "555", "type": "animated_gif"}
    assert _media_key_for_archive_item(gif, position=0) == "16_555"
    assert _media_key_for_archive_item({**gif, "type": "video"}, position=0) == "7_555"
    assert _media_key_for_archive_item({**gif, "type": "photo"}, position=0) == "3_555"


def test_a_saved_download_survives_a_higher_ranked_copy(paths) -> None:
    store = open_archive_store(paths, create=True)
    persist_from(store, "fxtwitter", _video_post("fxtwitter"))
    store.merge_rows(
        [
            store.build_media_download_update(
                store.list_media_rows()[0],
                download_state="done",
                local_path="media/100/13_555.mp4",
                sha256="main",
                byte_size=5,
                content_type="video/mp4",
                thumbnail_local_path="media/100/13_555-poster.jpg",
                thumbnail_sha256="poster",
                thumbnail_byte_size=3,
                thumbnail_content_type="image/jpeg",
                downloaded_at="2026-01-02T03:04:05+00:00",
                download_error=None,
            )
        ]
    )

    persist_from(store, "live_graphql", _video_post("live_graphql"))

    [row] = _media_rows(store)
    assert (row["local_path"], row["thumbnail_local_path"]) == (
        "media/100/13_555.mp4",
        "media/100/13_555-poster.jpg",
    )
    assert row["download_state"] == "done"
    store.close()


def test_replaying_saved_fx_captures_does_not_bring_a_duplicate_back(paths) -> None:
    status = {
        "type": "status",
        "id": "100",
        "text": "a video",
        "author": {"id": "1", "screen_name": "a", "name": "A"},
        "media": {
            "all": [
                {
                    "id": "555",
                    "type": "video",
                    "url": f"{VIDEO_URL}/fx.mp4",
                    "thumbnail_url": POSTER_URL,
                    "formats": [{"url": f"{VIDEO_URL}/fx.mp4", "bitrate": 1}],
                }
            ]
        },
    }
    # The capture really does describe this video under FxTwitter's own key.
    assert [
        item.media_key
        for item in extract_secondary_objects(fx_status_to_graphql(status)).media.values()
    ] == ["13_555"]
    store = open_archive_store(paths, create=True)
    persist_from(store, "live_graphql", _video_post("live_graphql"))
    # Rehydrating an archive without a collection row stops before it reaches the captures.
    persist_like_page(store, make_tweet_result("900", "an unrelated like"))
    store.append_raw_capture(
        "FxTwitterStatus", "100", None, 200, {"status": status}, source="fxtwitter"
    )

    store.rehydrate_from_raw_json()

    [row] = _media_rows(store)
    assert (row["media_key"], row["provenance_source"]) == ("7_555", "live_graphql")
    store.close()
