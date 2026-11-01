"""Copying an export's media files onto saved rows, across flushes and partial exports."""

from __future__ import annotations

import hashlib
import io
from collections import Counter
from typing import Any

from tests.conftest import make_tweet_result, make_video_media
from tweetnook.core.timeline_parsing import TimelineTweet
from tweetnook.importer.archive_media import copy_exported_media
from tweetnook.storage import open_archive_store
from tweetnook.storage.ingestion import ImportObservation


class ExportFiles:
    """An export's media directory, listing files in the order given."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files

    def iter_files(self, _directory: str) -> list[str]:
        return list(self.files)

    def open_binary(self, relative_path: str) -> io.BytesIO:
        return io.BytesIO(self.files[relative_path])


def _seed_videos(store: Any, count: int) -> None:
    observations = []
    for index in range(count):
        tweet_id = str(1000 + index)
        raw = make_tweet_result(
            tweet_id,
            "video",
            media=[
                make_video_media(
                    f"7_{index}",
                    f"https://pbs.twimg.com/poster{index}.jpg",
                    bitrate_url=f"https://video.twimg.com/main{index}.mp4",
                )
            ],
        )
        observations.append(
            ImportObservation(
                timeline_tweet=TimelineTweet(
                    tweet_id=tweet_id,
                    text="video",
                    author_id="100",
                    author_username="user100",
                    author_display_name="User 100",
                    created_at=raw["legacy"]["created_at"],
                    sort_index=None,
                    raw_json=raw,
                )
            )
        )
    store.ingest_import_batch(observations, membership="tweet")


def _copy(store: Any, paths: Any, files: dict[str, bytes]) -> None:
    copy_exported_media(
        ExportFiles(files),
        store,
        paths,
        "media",
        exported_names={},
        counts=Counter(),
        warnings=[],
    )


def test_a_video_keeps_its_first_asset_when_a_flush_separates_it_from_its_poster(paths) -> None:
    store = open_archive_store(paths, create=True)
    _seed_videos(store, 100)
    main_bytes = {index: f"main-{index}".encode() for index in range(100)}
    poster_bytes = {index: f"poster-{index}".encode() for index in range(100)}
    # Every video's main file comes first, so the hundredth row flushes before any poster.
    files = {
        f"media/{1000 + index}-main{index}-hd.mp4": main_bytes[index] for index in range(100)
    } | {f"media/{1000 + index}-poster{index}.jpg": poster_bytes[index] for index in range(100)}

    _copy(store, paths, files)

    rows = sorted(store.list_media_rows(), key=lambda row: row["tweet_id"])
    assert len(rows) == 100
    for index, row in enumerate(rows):
        assert row["download_state"] == "done"
        assert row["local_path"] == f"media/{1000 + index}/7_{index}.mp4"
        assert row["sha256"] == hashlib.sha256(main_bytes[index]).hexdigest()
        assert row["thumbnail_local_path"] == f"media/{1000 + index}/7_{index}-poster.jpg"
        assert row["thumbnail_sha256"] == hashlib.sha256(poster_bytes[index]).hexdigest()
    assert store.list_media_rows(states={"pending"}) == []
    store.close()


def test_a_partial_import_keeps_a_saved_download_time(paths) -> None:
    store = open_archive_store(paths, create=True)
    _seed_videos(store, 1)
    saved_at = "2026-01-02T03:04:05+00:00"
    store.merge_rows(
        [
            store.build_media_download_update(
                store.list_media_rows()[0],
                download_state="pending",
                local_path=None,
                sha256=None,
                byte_size=None,
                content_type=None,
                thumbnail_local_path=None,
                thumbnail_sha256=None,
                thumbnail_byte_size=None,
                thumbnail_content_type=None,
                downloaded_at=saved_at,
                download_error=None,
            )
        ]
    )

    _copy(store, paths, {"media/1000-poster0.jpg": b"poster"})

    row = store.list_media_rows()[0]
    assert row["thumbnail_local_path"] == "media/1000/7_0-poster.jpg"
    assert row["download_state"] == "pending"
    assert row["downloaded_at"] == saved_at
    store.close()
