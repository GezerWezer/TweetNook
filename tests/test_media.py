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
from tweetnook.client.timelines import TimelineTweet
from tweetnook.media import download_media
from tweetnook.pipeline import PipelineReporter
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
    assert repeat.skipped == 0
    assert reporter._step_by_key["media"].state == "skipped"
    assert "no media files" in reporter._step_by_key["media"].summary


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
    monkeypatch.setattr("tweetnook.media.progress_callback", progress)

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
        unit="rows",
    )
    assert on_progress.call_args_list == [((1, 3),), ((2, 3),), ((3, 3),)]
    output = buffer.getvalue()
    assert "media download: loading pending media rows" in output
    assert "media download: downloading 3 media rows" in output


@pytest.mark.asyncio
async def test_full_disk_aborts_download_without_marking_remaining_rows_failed(
    paths, config, monkeypatch
):
    import errno

    from tweetnook.resource_failure import StorageExhaustedError

    _seed_archive(paths)
    calls = []

    async def full(*args, **kwargs):
        calls.append(kwargs["url"])
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr("tweetnook.media._download_file", full)
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
