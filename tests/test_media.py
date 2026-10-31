from __future__ import annotations

from io import StringIO
from unittest.mock import MagicMock

import httpx
import pytest
from rich.console import Console

from tests.conftest import (
    make_article_result,
    make_link_card,
    make_photo_media,
    make_tweet_result,
    make_url_entity,
    make_video_media,
)
from tweetnook.core.timeline_parsing import TimelineTweet
from tweetnook.enrichment import media as media_module
from tweetnook.enrichment.media import download_media
from tweetnook.jobs.pipeline import PipelineReporter
from tweetnook.storage import open_archive_store
from tweetnook.storage.backend import ArchiveStore


@pytest.mark.asyncio
async def test_saved_link_cards_backfill_download_and_retry_without_refetch(paths, config):
    _seed_archive(paths)
    store = open_archive_store(paths, create=False)
    raw = make_tweet_result("300", "an older saved link")
    card_url = "https://pbs.twimg.com/card_img/story?format=jpg&name=small"
    raw["card"] = make_link_card(card_url)
    # Simulate a pre-upgrade archive: saved raw tweet, no extracted image row.
    store.merge_rows(
        [
            store._record(
                row_key="tweet_object:300",
                record_type="tweet_object",
                tweet_id="300",
                source="live",
                raw_json=store._json_value(raw),
            ),
        ]
    )
    store.close()
    requested = []

    def handler(request):
        requested.append(str(request.url))
        if "/card_img/" in str(request.url):
            return httpx.Response(503)
        return httpx.Response(200, content=b"image", headers={"content-type": "image/jpeg"})

    failed = await download_media(
        config=config, paths=paths, photos_only=True, transport=httpx.MockTransport(handler)
    )
    assert failed.failed == 1
    assert card_url in requested
    requested.clear()
    skipped = await download_media(
        config=config, paths=paths, transport=httpx.MockTransport(handler)
    )
    assert skipped.failed == 0
    assert not any("/card_img/" in url for url in requested)

    def image(request):
        assert str(request.url) == card_url
        return httpx.Response(200, content=b"card-image", headers={"content-type": "image/png"})

    retried = await download_media(
        config=config, paths=paths, retry_failed=True, limit=1, transport=httpx.MockTransport(image)
    )
    assert retried.downloaded == 1
    store = open_archive_store(paths, create=False)
    try:
        row = next(row for row in store.list_media_rows() if row["source"] == "link_card")
        assert row["download_state"] == "done"
        assert (paths.data_dir / row["local_path"]).read_bytes() == b"card-image"
        assert row["local_path"].endswith(".png")
        assert store.backfill_link_card_media() == 0
        assert store._get_row("tweet_object:300")["raw_json"] == store._json_value(raw)
    finally:
        store.close()


def _complex_tweet(tweet_id: str = "100") -> TimelineTweet:
    quoted = make_tweet_result(
        "200",
        "quoted short",
        user_id="2000",
        urls=[
            make_url_entity(
                "https://t.co/quoted",
                "https://quoted.example.com/post",
                display_url="quoted.example.com/post",
            )
        ],
        media=[
            make_video_media(
                "7_quoted",
                "https://pbs.twimg.com/ext_tw_video_thumb/quoted.jpg",
                bitrate_url="https://video.twimg.com/ext_tw_video/quoted.mp4",
            )
        ],
    )
    raw = make_tweet_result(
        tweet_id,
        "root short",
        user_id="1000",
        urls=[
            make_url_entity(
                "https://t.co/root",
                "https://example.com/story?utm_source=x&keep=1",
                display_url="example.com/story",
            )
        ],
        media=[make_photo_media("3_root", "https://pbs.twimg.com/media/root.jpg")],
        quoted_tweet=quoted,
        article=make_article_result(
            "article-1",
            title="Article title",
            preview_text="Article preview",
            plain_text="Article body",
            url="https://x.com/i/article/123",
        ),
    )
    return TimelineTweet(
        tweet_id=tweet_id,
        text="root short",
        author_id="1000",
        author_username="user1000",
        author_display_name="User 1000",
        created_at="Sat Mar 14 00:00:00 +0000 2026",
        sort_index="10",
        raw_json=raw,
    )


def _seed_archive(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[_complex_tweet()],
        last_head_tweet_id="100",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.close()


@pytest.mark.asyncio
async def test_media_limit_and_progress_count_video_with_poster_as_one_item(paths, config):
    store = open_archive_store(paths, create=True)
    tweet = _complex_tweet()
    tweet.raw_json = make_tweet_result(
        "100",
        "video",
        media=[
            make_video_media(
                "7_video",
                "https://pbs.twimg.com/poster.jpg",
                bitrate_url="https://video.twimg.com/video.mp4",
            )
        ],
    )
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={},
        tweets=[tweet],
        last_head_tweet_id="100",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.close()
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(
            200,
            content=b"media",
            headers={
                "content-type": "video/mp4" if request.url.path.endswith(".mp4") else "image/jpeg"
            },
        )

    reporter = PipelineReporter(Console(file=StringIO()), "media", interactive=False)
    with reporter:
        result = await download_media(
            limit=1, config=config, paths=paths, transport=httpx.MockTransport(handler)
        )

    assert result.processed == result.downloaded == 1
    assert len(requests) == 2
    assert len([p for p in paths.media_dir.rglob("*") if p.is_file()]) == 2
    step = reporter._step_by_key["media"]
    assert step.unit == "items"
    assert step.total == step.completed == 1
    assert step.rate_unit == "items/s"


@pytest.mark.asyncio
async def test_download_media_updates_rows_and_files(paths, config) -> None:
    _seed_archive(paths)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith(".mp4"):
            return httpx.Response(
                200, content=b"video-bytes", headers={"content-type": "video/mp4"}
            )
        return httpx.Response(200, content=b"image-bytes", headers={"content-type": "image/jpeg"})

    result = await download_media(
        config=config,
        paths=paths,
        transport=httpx.MockTransport(handler),
    )

    assert result.processed == 3
    assert result.downloaded == 3
    assert result.failed == 0

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        rows = store.list_media_rows()
        assert all(row["download_state"] == "done" for row in rows)
        assert all(
            (paths.data_dir / row["local_path"]).exists() for row in rows if row["local_path"]
        )
        video_row = next(row for row in rows if row["media_type"] == "video")
        assert video_row["thumbnail_local_path"] is not None
        assert (paths.data_dir / video_row["thumbnail_local_path"]).exists()
    finally:
        store.close()

    reporter = PipelineReporter(
        Console(file=StringIO(), force_terminal=False, color_system=None),
        "media",
        interactive=False,
    )
    with reporter:
        repeat = await download_media(
            config=config,
            paths=paths,
            transport=httpx.MockTransport(handler),
        )
    assert repeat.processed == 0
    assert repeat.downloaded == 0
    assert reporter._step_by_key["media"].state == "skipped"
    assert "no media items" in reporter._step_by_key["media"].summary


@pytest.mark.asyncio
async def test_download_media_batches_row_merges(
    paths,
    config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_archive(paths)
    batch_sizes: list[int] = []
    original = ArchiveStore.merge_rows

    def counting_merge_rows(self, rows):
        batch_sizes.append(len(rows))
        return original(self, rows)

    monkeypatch.setattr(ArchiveStore, "merge_rows", counting_merge_rows)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith(".mp4"):
            return httpx.Response(
                200,
                content=b"video-bytes",
                headers={"content-type": "video/mp4"},
                request=request,
            )
        return httpx.Response(
            200,
            content=b"image-bytes",
            headers={"content-type": "image/jpeg"},
            request=request,
        )

    result = await download_media(
        config=config,
        paths=paths,
        transport=httpx.MockTransport(handler),
    )

    assert result.downloaded == 3
    assert batch_sizes == [3]


@pytest.mark.asyncio
async def test_download_media_retries_failed_rows_and_respects_limit(paths, config) -> None:
    _seed_archive(paths)

    def failing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="nope", request=request)

    failed = await download_media(
        config=config,
        paths=paths,
        photos_only=True,
        transport=httpx.MockTransport(failing_handler),
    )

    assert failed.processed == 2
    assert failed.downloaded == 0
    assert failed.failed == 2

    skipped = await download_media(
        config=config,
        paths=paths,
        photos_only=True,
        transport=httpx.MockTransport(failing_handler),
    )
    assert skipped.processed == 0
    assert skipped.downloaded == 0
    assert skipped.failed == 0

    def success_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"image-bytes",
            headers={"content-type": "image/jpeg"},
            request=request,
        )

    retried = await download_media(
        config=config,
        paths=paths,
        photos_only=True,
        retry_failed=True,
        limit=1,
        transport=httpx.MockTransport(success_handler),
    )

    assert retried.processed == 1
    assert retried.downloaded == 1
    assert retried.failed == 0

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        done_photos = store.list_media_rows(states={"done"}, media_types={"photo"})
        failed_photos = store.list_media_rows(states={"failed"}, media_types={"photo"})
        pending_video = store.list_media_rows(states={"pending"}, media_types={"video"})
        assert len(done_photos) == 1
        assert len(failed_photos) == 1
        assert len(pending_video) == 1
    finally:
        store.close()


@pytest.mark.asyncio
async def test_download_media_interactive_reports_status(paths, config, monkeypatch) -> None:
    _seed_archive(paths)
    buffer = StringIO()
    console = Console(file=buffer, force_terminal=True, color_system=None)
    progress = MagicMock()
    on_progress = MagicMock()
    progress.return_value.__enter__.return_value = on_progress
    monkeypatch.setattr("tweetnook.enrichment.media.progress_callback", progress)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith(".mp4"):
            return httpx.Response(
                200,
                content=b"video-bytes",
                headers={"content-type": "video/mp4"},
                request=request,
            )
        return httpx.Response(
            200,
            content=b"image-bytes",
            headers={"content-type": "image/jpeg"},
            request=request,
        )

    result = await download_media(
        config=config,
        paths=paths,
        transport=httpx.MockTransport(handler),
        console=console,
    )

    assert result.downloaded == 3
    progress.assert_called_once_with(
        console,
        label="media download",
        total=3,
        unit="items",
    )
    assert on_progress.call_args_list == [((1, 3),), ((2, 3),), ((3, 3),)]
    output = buffer.getvalue()
    assert "media download: loading pending media rows" in output
    assert "media download: downloading 3 media items" in output


@pytest.mark.asyncio
async def test_full_disk_aborts_download_without_marking_remaining_rows_failed(
    paths, config, monkeypatch
):
    import errno

    from tweetnook.jobs.resource_failure import StorageExhaustedError

    _seed_archive(paths)
    calls = []

    async def full(*args, **kwargs):
        calls.append(kwargs["url"])
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr("tweetnook.enrichment.media._download_file", full)
    with pytest.raises(StorageExhaustedError):
        await download_media(config=config, paths=paths, console=Console(file=StringIO()))
    assert len(calls) == 1
    store = open_archive_store(paths, create=False)
    try:
        rows = store.list_media_rows()
        assert rows
        assert all(row["download_state"] == "pending" for row in rows)
    finally:
        store.close()


def _card_tweet(tweet_id: str, card: dict[str, object]) -> TimelineTweet:
    raw = make_tweet_result(tweet_id, "a linked story")
    raw["card"] = card
    return TimelineTweet(
        tweet_id=tweet_id,
        text="a linked story",
        author_id="1",
        author_username="user1",
        author_display_name="User 1",
        created_at="Sat Mar 14 00:00:00 +0000 2026",
        sort_index="10",
        raw_json=raw,
    )


def _stripped_card(card_url: str = "https://example.com/story") -> dict[str, object]:
    # X's Likes timeline sometimes sends a summary card without any image bindings.
    return {
        "legacy": {
            "name": "summary_large_image",
            "binding_values": [
                {"key": "title", "value": {"string_value": "A saved story"}},
                {"key": "card_url", "value": {"string_value": card_url}},
            ],
        }
    }


def _card_image(store: ArchiveStore, row_key: str) -> str | None:
    import json

    raw = json.loads(store._get_row(row_key)["raw_json"])
    for item in raw["card"]["legacy"]["binding_values"]:
        if item["key"] == "thumbnail_image_original":
            return item["value"]["image_value"]["url"]
    return None


def _persist_likes(store: ArchiveStore, tweet: TimelineTweet) -> None:
    store.persist_page(
        operation="Likes",
        collection_type="like",
        cursor_in=None,
        cursor_out="cursor-1",
        http_status=200,
        raw_json={"page": tweet.tweet_id},
        tweets=[tweet],
        last_head_tweet_id=tweet.tweet_id,
        backfill_cursor="cursor-1",
        backfill_incomplete=False,
    )


def test_later_timeline_copy_keeps_the_card_image_of_the_same_card(paths):
    store = open_archive_store(paths, create=True)
    card_image = "https://pbs.twimg.com/card_img/story.jpg"
    try:
        detail = _card_tweet("400", make_link_card(card_image))
        store.persist_tweet_detail(tweet=detail, raw_json={"detail": "400"})
        _persist_likes(store, _card_tweet("400", _stripped_card()))
        assert _card_image(store, "tweet_object:400") == card_image
        assert _card_image(store, "tweet:like::400") == card_image

        # A different card (another link) replaces the old one as before.
        _persist_likes(store, _card_tweet("400", _stripped_card("https://example.com/other")))
        assert _card_image(store, "tweet_object:400") is None
    finally:
        store.close()


@pytest.mark.parametrize("operation", ["TweetDetail", "Likes"])
def test_stripped_link_cards_are_restored_once_from_saved_captures(paths, operation):
    store = open_archive_store(paths, create=True)
    card_image = "https://pbs.twimg.com/card_img/story.jpg"
    try:
        _persist_likes(store, _card_tweet("500", _stripped_card()))
        _persist_likes(store, _card_tweet("501", _stripped_card()))  # no capture has its image
        full = make_tweet_result("500", "a linked story")
        full["card"] = make_link_card(card_image)
        older = make_tweet_result("500", "a linked story")
        older["card"] = make_link_card("https://pbs.twimg.com/card_img/older.jpg")
        cursor_in = "500" if operation == "TweetDetail" else "page-cursor"
        store.append_raw_capture(operation, cursor_in, None, 200, {"data": {"tweet": older}})
        store.append_raw_capture(operation, cursor_in, None, 200, {"data": {"tweet": full}})
        assert _card_image(store, "tweet_object:500") is None

        assert store.restore_link_card_images() == 1
        assert _card_image(store, "tweet_object:500") == card_image
        assert _card_image(store, "tweet:like::500") == card_image
        assert _card_image(store, "tweet_object:501") is None
        media = [row for row in store.list_media_rows() if row["source"] == "link_card"]
        assert [(row["tweet_id"], row["media_url"]) for row in media] == [("500", card_image)]
        assert store.restore_link_card_images() == 0
    finally:
        store.close()


def test_card_restore_retries_media_after_insert_failure(paths):
    import sqlite3

    store = open_archive_store(paths, create=True)
    card_image = "https://pbs.twimg.com/card_img/story.jpg"
    try:
        assert store.backfill_link_card_media() == 0
        _persist_likes(store, _card_tweet("500", _stripped_card()))
        full = make_tweet_result("500", "a linked story")
        full["card"] = make_link_card(card_image)
        store.append_raw_capture("TweetDetail", "500", None, 200, {"data": {"tweet": full}})
        store.conn.execute(
            "CREATE TEMP TRIGGER reject_card_media BEFORE INSERT ON archive "
            "WHEN new.record_type = 'media' BEGIN SELECT RAISE(ABORT, 'media insert'); END"
        )
        with pytest.raises(sqlite3.IntegrityError, match="media insert"):
            store.restore_link_card_images()
    finally:
        store.close()
    store = open_archive_store(paths, create=False)
    try:
        assert _card_image(store, "tweet_object:500") is None
        assert store.restore_link_card_images() == 1
        assert _card_image(store, "tweet_object:500") == card_image
        assert [(row["tweet_id"], row["media_url"]) for row in store.list_media_rows()] == [
            ("500", card_image)
        ]
    finally:
        store.close()


_VIDEO_URL_SUFFIX = ".mp4"


class _AssetServer:
    """Serve each asset kind from a switchable answer and record what was requested."""

    def __init__(self) -> None:
        self.video_status = 200
        self.poster_status = 200
        self.requested: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        is_video = request.url.path.endswith(_VIDEO_URL_SUFFIX)
        self.requested.append("video" if is_video else "poster")
        status = self.video_status if is_video else self.poster_status
        if status != 200:
            return httpx.Response(status, text="nope", request=request)
        return httpx.Response(
            200,
            content=b"video-bytes" if is_video else b"poster",
            headers={"content-type": "video/mp4" if is_video else "image/jpeg"},
            request=request,
        )


async def _download_videos(paths, config, server: _AssetServer, *, retry_failed: bool = False):
    server.requested = []
    return await download_media(
        config=config,
        paths=paths,
        retry_failed=retry_failed,
        transport=httpx.MockTransport(server),
    )


def _video_row(paths) -> dict[str, object]:
    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        return next(row for row in store.list_media_rows() if row["media_type"] == "video")
    finally:
        store.close()


def _only_video(paths) -> None:
    """Leave the seeded video as the only row awaiting download."""
    _seed_archive(paths)
    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        store.merge_rows(
            [
                store.build_media_download_update(
                    row,
                    download_state="done",
                    local_path=None,
                    sha256=None,
                    byte_size=None,
                    content_type=None,
                    thumbnail_local_path=None,
                    thumbnail_sha256=None,
                    thumbnail_byte_size=None,
                    thumbnail_content_type=None,
                    downloaded_at=None,
                    download_error=None,
                )
                for row in store.list_media_rows()
                if row["media_type"] != "video"
            ]
        )
    finally:
        store.close()


@pytest.mark.asyncio
async def test_retry_fetches_only_the_poster_after_the_video_was_saved(paths, config) -> None:
    _only_video(paths)
    server = _AssetServer()
    server.poster_status = 503

    first = await _download_videos(paths, config, server)

    assert (first.downloaded, first.failed) == (0, 1)
    assert server.requested == ["video", "poster"]
    saved = _video_row(paths)
    assert saved["download_state"] == "failed"
    assert int(str(saved["byte_size"])) == len(b"video-bytes")

    # The video URL has since expired, which must not block the poster.
    server.poster_status = 200
    server.video_status = 503
    second = await _download_videos(paths, config, server, retry_failed=True)

    assert server.requested == ["poster"]
    assert (second.downloaded, second.failed) == (1, 0)
    assert second.downloaded_bytes == len(b"poster")
    done = _video_row(paths)
    assert done["download_state"] == "done"
    assert done["local_path"] == saved["local_path"]
    assert done["sha256"] == saved["sha256"]
    assert (paths.data_dir / str(done["thumbnail_local_path"])).read_bytes() == b"poster"


@pytest.mark.asyncio
async def test_a_video_imported_without_its_poster_only_needs_the_poster(paths, config) -> None:
    _only_video(paths)
    imported = paths.data_dir / "imported" / "video.mp4"
    imported.parent.mkdir(parents=True)
    imported.write_bytes(b"from the export")
    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        row = next(row for row in store.list_media_rows() if row["media_type"] == "video")
        store.merge_rows(
            [
                store.build_media_download_update(
                    row,
                    download_state="pending",
                    local_path="imported/video.mp4",
                    sha256="imported-hash",
                    byte_size=15,
                    content_type="video/mp4",
                    thumbnail_local_path=None,
                    thumbnail_sha256=None,
                    thumbnail_byte_size=None,
                    thumbnail_content_type=None,
                    downloaded_at=None,
                    download_error=None,
                )
            ]
        )
    finally:
        store.close()
    server = _AssetServer()

    result = await _download_videos(paths, config, server)

    assert server.requested == ["poster"]
    assert result.downloaded == 1
    done = _video_row(paths)
    assert (done["local_path"], done["sha256"]) == ("imported/video.mp4", "imported-hash")
    assert imported.read_bytes() == b"from the export"


@pytest.mark.asyncio
async def test_a_main_file_missing_from_disk_is_downloaded_again(paths, config) -> None:
    _only_video(paths)
    server = _AssetServer()
    server.poster_status = 503
    await _download_videos(paths, config, server)
    (paths.data_dir / str(_video_row(paths)["local_path"])).unlink()
    server.poster_status = 200

    result = await _download_videos(paths, config, server, retry_failed=True)

    assert server.requested == ["video", "poster"]
    assert result.downloaded == 1
    assert (paths.data_dir / str(_video_row(paths)["local_path"])).read_bytes() == b"video-bytes"


@pytest.mark.asyncio
async def test_a_failed_video_still_saves_its_poster_for_the_retry(paths, config) -> None:
    _only_video(paths)
    server = _AssetServer()
    server.video_status = 503

    first = await _download_videos(paths, config, server)

    assert server.requested == ["video", "poster"]
    assert first.failed == 1
    saved = _video_row(paths)
    assert saved["download_state"] == "failed"
    assert saved["thumbnail_local_path"] is not None

    server.video_status = 200
    second = await _download_videos(paths, config, server, retry_failed=True)

    assert server.requested == ["video"]
    assert second.downloaded == 1
    assert _video_row(paths)["download_state"] == "done"


@pytest.mark.parametrize(
    "error",
    [RuntimeError("local bug"), PermissionError("read-only"), ValueError("local bug")],
)
@pytest.mark.asyncio
async def test_a_local_error_stops_the_job_instead_of_failing_the_row(
    paths, config, monkeypatch, error
) -> None:
    _only_video(paths)

    async def broken(*_args, **_kwargs):
        raise error

    monkeypatch.setattr("tweetnook.enrichment.media._download_file", broken)

    with pytest.raises(type(error)):
        await download_media(
            config=config, paths=paths, transport=httpx.MockTransport(_AssetServer())
        )
    assert _video_row(paths)["download_state"] == "pending"


_UNPARSEABLE_URL = "http://[broken"
_VALID_PHOTO_URL = "https://pbs.twimg.com/media/valid.jpg"


def _media_tweet(tweet_id: str, media: dict[str, object]) -> TimelineTweet:
    return TimelineTweet(
        tweet_id=tweet_id,
        text="post",
        author_id="1000",
        author_username="user1000",
        author_display_name="User 1000",
        created_at="Sat Mar 14 00:00:00 +0000 2026",
        sort_index=tweet_id,
        raw_json=make_tweet_result(tweet_id, "post", user_id="1000", media=[media]),
    )


def _seed_media(paths, *media: dict[str, object]) -> None:
    """Save one post per media item through the real ingestion path, then a valid photo."""
    tweets = [_media_tweet(str(100 + index), item) for index, item in enumerate(media)]
    tweets.append(_media_tweet("900", make_photo_media("3_valid", _VALID_PHOTO_URL)))
    store = open_archive_store(paths, create=True)
    assert store is not None
    try:
        store.persist_page(
            operation="Bookmarks",
            collection_type="bookmark",
            cursor_in=None,
            cursor_out=None,
            http_status=200,
            raw_json={"ok": True},
            tweets=tweets,
            last_head_tweet_id=tweets[0].tweet_id,
            backfill_cursor=None,
            backfill_incomplete=False,
        )
    finally:
        store.close()


def _rows_by_post(paths) -> dict[str, dict[str, object]]:
    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        return {str(row["tweet_id"]): row for row in store.list_media_rows()}
    finally:
        store.close()


def _serve_assets(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200, content=b"asset", headers={"content-type": "image/jpeg"}, request=request
    )


@pytest.mark.parametrize(
    "media",
    [
        pytest.param(make_photo_media("3_bad", _UNPARSEABLE_URL), id="photo"),
        pytest.param(
            make_photo_media("3_bad", "https://exa\N{FULLWIDTH SOLIDUS}mple.com/photo.jpg"),
            id="photo-with-rejected-host",
        ),
        pytest.param(
            make_video_media(
                "7_bad", "https://pbs.twimg.com/poster.jpg", bitrate_url=_UNPARSEABLE_URL
            ),
            id="video",
        ),
    ],
)
@pytest.mark.asyncio
async def test_an_unparseable_main_url_fails_only_its_row(paths, config, media) -> None:
    _seed_media(paths, media)

    result = await download_media(
        config=config, paths=paths, transport=httpx.MockTransport(_serve_assets)
    )

    assert (result.downloaded, result.failed) == (1, 1)
    rows = _rows_by_post(paths)
    assert rows["100"]["download_state"] == "failed"
    assert rows["100"]["local_path"] is None
    assert rows["900"]["download_state"] == "done"
    # A video's poster is a separate file, and its URL is fine.
    assert (rows["100"]["thumbnail_local_path"] is not None) == (media["type"] == "video")


@pytest.mark.asyncio
async def test_an_unparseable_poster_url_keeps_the_saved_video(paths, config) -> None:
    _seed_media(
        paths,
        make_video_media("7_bad", _UNPARSEABLE_URL, bitrate_url="https://video.twimg.com/v.mp4"),
    )

    result = await download_media(
        config=config, paths=paths, transport=httpx.MockTransport(_serve_assets)
    )

    assert (result.downloaded, result.failed) == (1, 1)
    rows = _rows_by_post(paths)
    assert rows["100"]["download_state"] == "failed"
    assert (paths.data_dir / str(rows["100"]["local_path"])).read_bytes() == b"asset"
    assert rows["100"]["thumbnail_local_path"] is None
    assert rows["900"]["download_state"] == "done"


@pytest.mark.asyncio
async def test_a_url_with_a_null_byte_fails_only_its_row(paths, config) -> None:
    # It parses, and its suffix would reach the file name before the request rejects it.
    _seed_media(paths, make_photo_media("3_bad", "https://example.com/photo.jp\x00g"))

    result = await download_media(
        config=config, paths=paths, transport=httpx.MockTransport(_serve_assets)
    )

    assert (result.downloaded, result.failed) == (1, 1)
    rows = _rows_by_post(paths)
    assert rows["100"]["download_state"] == "failed"
    assert rows["900"]["download_state"] == "done"


@pytest.mark.asyncio
async def test_a_url_suffix_too_long_for_a_file_name_still_downloads(paths, config) -> None:
    _seed_media(paths, make_photo_media("3_long", f"https://example.com/photo.{'j' * 300}"))

    result = await download_media(
        config=config, paths=paths, transport=httpx.MockTransport(_serve_assets)
    )

    assert (result.downloaded, result.failed) == (2, 0)
    assert _rows_by_post(paths)["100"]["local_path"] == "media/100/3_long.jpg"


@pytest.mark.asyncio
async def test_a_local_error_keeps_the_rows_finished_before_it(paths, config, monkeypatch) -> None:
    _seed_media(paths, make_photo_media("3_first", "https://pbs.twimg.com/media/first.jpg"))
    download_file = media_module._download_file
    calls = 0

    async def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise PermissionError("read-only")
        return await download_file(*args, **kwargs)

    monkeypatch.setattr("tweetnook.enrichment.media._download_file", fail_second)

    with pytest.raises(PermissionError):
        await download_media(
            config=config, paths=paths, transport=httpx.MockTransport(_serve_assets)
        )

    states = sorted(str(row["download_state"]) for row in _rows_by_post(paths).values())
    assert states == ["done", "pending"]


@pytest.mark.asyncio
async def test_saving_finished_rows_cannot_replace_the_error_that_stopped_the_job(
    paths, config, monkeypatch
) -> None:
    _seed_media(paths, make_photo_media("3_first", "https://pbs.twimg.com/media/first.jpg"))
    download_file = media_module._download_file
    calls = 0

    async def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise PermissionError("read-only")
        return await download_file(*args, **kwargs)

    def broken_merge(self, rows):
        raise RuntimeError("store failed too")

    monkeypatch.setattr("tweetnook.enrichment.media._download_file", fail_second)
    monkeypatch.setattr(ArchiveStore, "merge_rows", broken_merge)

    with pytest.raises(PermissionError):
        await download_media(
            config=config, paths=paths, transport=httpx.MockTransport(_serve_assets)
        )
