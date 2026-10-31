from __future__ import annotations

import errno
from io import StringIO
from unittest.mock import MagicMock

import httpx
import pytest
from rich.console import Console

from tests.conftest import (
    make_article_result,
    make_photo_media,
    make_tweet_result,
    make_url_entity,
    make_video_media,
)
from tweetnook.core.timeline_parsing import TimelineTweet
from tweetnook.enrichment.unfurl import unfurl_urls
from tweetnook.jobs.pipeline import PipelineReporter
from tweetnook.jobs.resource_failure import StorageExhaustedError
from tweetnook.storage import open_archive_store
from tweetnook.storage.backend import ArchiveStore


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
async def test_unfurl_urls_persists_html_metadata(paths, config) -> None:
    _seed_archive(paths)

    def handler(request: httpx.Request) -> httpx.Response:
        if "quoted.example.com" in str(request.url):
            html = """
            <html><head>
            <title>Quoted Story</title>
            <meta property="og:description" content="Quoted description">
            </head><body></body></html>
            """
            return httpx.Response(200, text=html, headers={"content-type": "text/html"})
        html = """
        <html><head>
        <title>Root Story</title>
        <link rel="canonical" href="https://example.com/story?keep=1">
        <meta name="description" content="Root description">
        <meta property="og:site_name" content="Example">
        </head><body></body></html>
        """
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})

    result = await unfurl_urls(
        config=config,
        paths=paths,
        transport=httpx.MockTransport(handler),
    )

    assert result.processed == 2
    assert result.updated == 2
    assert result.failed == 0

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        rows = store.list_url_rows(states={"done"})
        assert len(rows) == 2
        root = next(
            row for row in rows if row["canonical_url"] == "https://example.com/story?keep=1"
        )
        assert root["title"] == "Root Story"
        assert root["description"] == "Root description"
        assert root["site_name"] == "Example"
        quoted = next(
            row for row in rows if row["canonical_url"] == "https://quoted.example.com/post"
        )
        assert quoted["title"] == "Quoted Story"
        assert quoted["description"] == "Quoted description"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_unfurl_urls_batches_row_merges(
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
        html = """
        <html><head>
        <title>Story</title>
        </head><body></body></html>
        """
        return httpx.Response(
            200,
            text=html,
            headers={"content-type": "text/html"},
            request=request,
        )

    result = await unfurl_urls(
        config=config,
        paths=paths,
        transport=httpx.MockTransport(handler),
    )

    assert result.updated == 2
    assert batch_sizes == [2]


@pytest.mark.asyncio
async def test_unfurl_urls_retries_failed_rows_and_respects_limit(paths, config) -> None:
    _seed_archive(paths)

    def failing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="nope", request=request)

    failed = await unfurl_urls(
        config=config,
        paths=paths,
        transport=httpx.MockTransport(failing_handler),
    )

    assert failed.processed == 2
    assert failed.updated == 0
    assert failed.failed == 2

    reporter = PipelineReporter(
        Console(file=StringIO(), force_terminal=False, color_system=None),
        "unfurl",
        interactive=False,
    )
    with reporter:
        skipped = await unfurl_urls(
            config=config,
            paths=paths,
            transport=httpx.MockTransport(failing_handler),
        )
    assert skipped.processed == 0
    assert skipped.updated == 0
    assert skipped.failed == 0
    assert reporter._step_by_key["urls"].state == "skipped"
    assert "no saved URLs" in reporter._step_by_key["urls"].summary

    def success_handler(request: httpx.Request) -> httpx.Response:
        html = """
        <html><head>
        <title>Recovered</title>
        </head><body></body></html>
        """
        return httpx.Response(
            200,
            text=html,
            headers={"content-type": "text/html"},
            request=request,
        )

    retried = await unfurl_urls(
        config=config,
        paths=paths,
        retry_failed=True,
        limit=1,
        transport=httpx.MockTransport(success_handler),
    )

    assert retried.processed == 1
    assert retried.updated == 1
    assert retried.failed == 0

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        done_rows = store.list_url_rows(states={"done"})
        failed_rows = store.list_url_rows(states={"failed"})
        assert len(done_rows) == 1
        assert len(failed_rows) == 1
    finally:
        store.close()


@pytest.mark.asyncio
async def test_unfurl_urls_accepts_non_html_responses(paths, config) -> None:
    _seed_archive(paths)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"ok": True},
            headers={"content-type": "application/json"},
            request=request,
        )

    result = await unfurl_urls(
        config=config,
        paths=paths,
        limit=1,
        transport=httpx.MockTransport(handler),
    )

    assert result.processed == 1
    assert result.updated == 1
    assert result.failed == 0

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        done_rows = store.list_url_rows(states={"done"})
        pending_rows = store.list_url_rows(states={"pending"})
        assert len(done_rows) == 1
        assert len(pending_rows) == 1
        row = done_rows[0]
        assert row["content_type"] == "application/json"
        assert row["title"] is None
        assert row["description"] is None
    finally:
        store.close()


@pytest.mark.asyncio
async def test_unfurl_urls_interactive_reports_status(paths, config, monkeypatch) -> None:
    _seed_archive(paths)
    buffer = StringIO()
    console = Console(file=buffer, force_terminal=True, color_system=None)
    progress = MagicMock()
    on_progress = MagicMock()
    progress.return_value.__enter__.return_value = on_progress
    monkeypatch.setattr("tweetnook.enrichment.unfurl.progress_callback", progress)

    def handler(request: httpx.Request) -> httpx.Response:
        html = """
        <html><head>
        <title>Story</title>
        </head><body></body></html>
        """
        return httpx.Response(
            200,
            text=html,
            headers={"content-type": "text/html"},
            request=request,
        )

    result = await unfurl_urls(
        config=config,
        paths=paths,
        transport=httpx.MockTransport(handler),
        console=console,
    )

    assert result.updated == 2
    progress.assert_called_once_with(
        console,
        label="unfurl",
        total=2,
        unit="urls",
    )
    assert on_progress.call_args_list == [((1, 2),), ((2, 2),)]
    output = buffer.getvalue()
    assert "unfurl: loading saved URL rows" in output
    assert "unfurl: fetching metadata for 2 saved URLs" in output


async def _unfurl_one(paths, config, handler):
    return await unfurl_urls(
        config=config,
        paths=paths,
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_a_local_update_error_is_not_saved_as_a_failed_url(
    paths, config, monkeypatch
) -> None:
    _seed_archive(paths)

    real_update = ArchiveStore.build_url_unfurl_update

    def broken_success_update(self, row, **fields):
        # Only the success write breaks; the failed-row write works, so a catch-all around the
        # update would save this as a bad URL.
        if fields["unfurl_state"] == "done":
            raise RuntimeError("local bug")
        return real_update(self, row, **fields)

    monkeypatch.setattr(ArchiveStore, "build_url_unfurl_update", broken_success_update)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text="ok", headers={"content-type": "text/plain"}, request=request
        )

    with pytest.raises(RuntimeError, match="local bug"):
        await _unfurl_one(paths, config, handler)

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        assert store.list_url_rows(states={"failed"}) == []
    finally:
        store.close()


@pytest.mark.asyncio
async def test_a_full_disk_while_saving_urls_stops_with_the_storage_error(
    paths, config, monkeypatch
) -> None:
    _seed_archive(paths)

    def full(self, *_args, **_kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(ArchiveStore, "build_url_unfurl_update", full)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text="ok", headers={"content-type": "text/plain"}, request=request
        )

    with pytest.raises(StorageExhaustedError):
        await _unfurl_one(paths, config, handler)


@pytest.mark.parametrize(
    ("answer", "http_status"),
    [
        (lambda request: httpx.Response(503, text="down", request=request), "503"),
        (
            lambda request: (_ for _ in ()).throw(httpx.ConnectError("offline", request=request)),
            None,
        ),
    ],
    ids=["server-error", "transport"],
)
@pytest.mark.asyncio
async def test_remote_failures_stay_on_their_url(paths, config, answer, http_status):
    _seed_archive(paths)

    result = await _unfurl_one(paths, config, answer)

    assert result.failed == result.processed >= 1
    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        rows = store.list_url_rows(states={"failed"})
        assert len(rows) == result.failed
        assert all(row["http_status"] == http_status for row in rows)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_a_malformed_canonical_link_falls_back_to_the_final_url(paths, config):
    _seed_archive(paths)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text='<title>Saved</title><link rel="canonical" href="http://[broken">',
            headers={"content-type": "text/html"},
            request=request,
        )

    result = await _unfurl_one(paths, config, handler)

    assert result.failed == 0
    assert result.updated == result.processed >= 1


@pytest.mark.asyncio
async def test_a_malformed_saved_url_is_that_rows_failure_under_a_pipeline(
    paths, config, monkeypatch
):
    _seed_archive(paths)
    real_rows = ArchiveStore.list_url_rows

    def malformed_rows(self, **kwargs):
        return [{**row, "final_url": "https://[broken"} for row in real_rows(self, **kwargs)]

    monkeypatch.setattr(ArchiveStore, "list_url_rows", malformed_rows)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unresolvable", request=request)

    console = Console(file=StringIO(), force_terminal=False, color_system=None)
    with PipelineReporter(console, "unfurl", interactive=False):
        result = await _unfurl_one(paths, config, handler)

    assert result.failed == result.processed >= 1
