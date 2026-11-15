from __future__ import annotations

import asyncio
import errno
import time
from io import StringIO
from unittest.mock import MagicMock
from urllib.parse import urlsplit

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
from tweetnook.enrichment import unfurl
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
async def test_each_url_is_saved_before_the_next_is_fetched(paths, config) -> None:
    """A worker killed mid-run runs no cleanup, so a finished URL must already be committed."""
    _seed_archive(paths)
    done_when_fetched: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        # A separate connection sees only committed rows.
        reader = open_archive_store(paths, create=False)
        assert reader is not None
        try:
            done_when_fetched.append(len(reader.list_url_rows(states={"done"})))
        finally:
            reader.close()
        return httpx.Response(
            200, text="<title>Story</title>", headers={"content-type": "text/html"}
        )

    result = await unfurl_urls(
        config=config,
        paths=paths,
        transport=httpx.MockTransport(handler),
    )

    assert result.updated == 2
    assert done_when_fetched == [0, 1]


class _CountingStream(httpx.AsyncByteStream):
    """An endless body that counts the chunks a reader pulled from it."""

    def __init__(self, chunk: bytes, *, delay: float = 0.0, first: bytes = b"") -> None:
        self.chunk = chunk
        self.delay = delay
        self.first = first
        self.pulled = 0

    async def __aiter__(self):
        if self.first:
            self.pulled += 1
            yield self.first
        while True:
            if self.delay:
                await asyncio.sleep(self.delay)
            self.pulled += 1
            yield self.chunk


@pytest.mark.asyncio
async def test_a_non_html_answer_is_recorded_without_reading_its_body(paths, config) -> None:
    _seed_archive(paths)
    streams: list[_CountingStream] = []

    def handler(request: httpx.Request) -> httpx.Response:
        stream = _CountingStream(b"\0" * 65536)
        streams.append(stream)
        return httpx.Response(200, headers={"content-type": "video/mp4"}, stream=stream)

    result = await unfurl_urls(config=config, paths=paths, transport=httpx.MockTransport(handler))

    assert result.updated == result.processed == 2
    assert [stream.pulled for stream in streams] == [0, 0]
    rows = _done_rows(paths)
    assert {row["content_type"] for row in rows} == {"video/mp4"}


@pytest.mark.asyncio
async def test_an_endless_html_page_is_read_only_up_to_the_metadata_limit(paths, config) -> None:
    _seed_archive(paths)
    streams: list[_CountingStream] = []
    chunk = b"<p>filler</p>" * 1000

    def handler(request: httpx.Request) -> httpx.Response:
        stream = _CountingStream(chunk, first=b"<title>Huge page</title>")
        streams.append(stream)
        return httpx.Response(200, headers={"content-type": "text/html"}, stream=stream)

    result = await unfurl_urls(config=config, paths=paths, transport=httpx.MockTransport(handler))

    assert result.updated == result.processed == 2
    # 500,000 bytes after a short first chunk takes 39 chunks of 13,000 bytes.
    assert [stream.pulled for stream in streams] == [1 + 39, 1 + 39]
    assert {row["title"] for row in _done_rows(paths)} == {"Huge page"}


@pytest.mark.asyncio
async def test_a_page_that_never_finishes_fails_at_the_deadline(paths, config, monkeypatch) -> None:
    _seed_archive(paths)
    # The client timeout is 30s; this makes each URL's deadline 0.3s.
    monkeypatch.setattr(unfurl, "_DEADLINE_TIMEOUTS", 0.01)

    def handler(request: httpx.Request) -> httpx.Response:
        # Small chunks keep arriving, so no single read ever waits long.
        stream = _CountingStream(b"<p>.</p>", delay=0.01)
        return httpx.Response(200, headers={"content-type": "text/html"}, stream=stream)

    started = time.monotonic()
    result = await unfurl_urls(config=config, paths=paths, transport=httpx.MockTransport(handler))
    elapsed = time.monotonic() - started

    assert result.failed == result.processed == 2
    assert elapsed < 5
    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        errors = [
            row["download_error"]
            for row in store.conn.execute(
                "SELECT download_error FROM archive WHERE row_key LIKE 'url:%'"
            )
        ]
    finally:
        store.close()
    assert errors == ["gave up after 0.3s without a complete answer"] * 2


def _done_rows(paths) -> list[dict]:
    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        return store.list_url_rows(states={"done"})
    finally:
        store.close()


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


def _html_response(markup: str):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text=markup, headers={"content-type": "text/html"}, request=request
        )

    return handler


def _saved_url_row(paths) -> dict:
    store = open_archive_store(paths, create=True)
    try:
        return next(row for row in store.list_url_rows(limit=10) if row.get("unfurl_state"))
    finally:
        store.close()


@pytest.mark.parametrize(
    "markup",
    [
        (
            "<title>Clean &amp; tidy</title>"
            '<link rel="canonical" href="https://dest.example/clean">'
            '<meta name="description" content="About &amp; more">'
            '<meta property="og:site_name" content="Dest">'
        ),
        (
            "<TITLE>Clean &amp;\n  tidy</TITLE>"
            "<link href='https://dest.example/clean' data-x='1' rel='canonical'>"
            "<META content='About &amp; more' name='description'>"
            "<meta content=Dest property=og:site_name>"
        ),
        (
            '<script>"<title>Not this</title>"</script><title>Clean &amp; tidy</title>'
            '<link href="/clean" rel="canonical">'
            '<meta content="About &amp; more" property="og:description">'
            '<meta content="Dest" name="application-name">'
        ),
    ],
)
@pytest.mark.asyncio
async def test_metadata_is_read_in_any_spelling_of_the_markup(paths, config, markup: str):
    _seed_archive(paths)

    result = await _unfurl_one(paths, config, _html_response(markup))

    assert result.failed == 0
    row = _saved_url_row(paths)
    assert row["title"] == "Clean & tidy"
    assert row["description"] == "About & more"
    assert row["site_name"] == "Dest"
    assert urlsplit(row["canonical_url"]).path == "/clean"


@pytest.mark.asyncio
async def test_a_relative_canonical_resolves_against_the_final_url(paths, config):
    _seed_archive(paths)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host != "final.example":
            return httpx.Response(302, headers={"location": "https://final.example/a/b"})
        return httpx.Response(
            200,
            text='<link rel="canonical" href="../clean?utm_source=x">',
            headers={"content-type": "text/html"},
            request=request,
        )

    await _unfurl_one(paths, config, handler)

    assert _saved_url_row(paths)["canonical_url"] == "https://final.example/clean"


@pytest.mark.asyncio
async def test_markup_html_parser_refuses_keeps_what_came_before_it(paths, config):
    _seed_archive(paths)

    result = await _unfurl_one(paths, config, _html_response("<title>Kept</title><![bogus[ x ]]>"))

    assert result.failed == 0
    assert _saved_url_row(paths)["title"] == "Kept"


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
