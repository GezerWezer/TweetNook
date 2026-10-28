from __future__ import annotations

import asyncio
from io import StringIO

import httpx
import pytest
from rich.console import Console

from tests.collection_sync.conftest import (
    _bookmarks_response_from_results,
    _console,
    _op_and_variables,
    _save_query_ids,
)
from tests.conftest import (
    make_bookmarks_response,
    make_user_tweets_response,
)
from tweetnook.config import AppConfig
from tweetnook.core.exceptions import ConfigError
from tweetnook.jobs.pipeline import PipelineReporter
from tweetnook.storage import open_archive_store
from tweetnook.sync.collection import sync_collection


@pytest.mark.asyncio
async def test_sync_collection_logs_head_and_backfill_progress(
    paths, config: AppConfig, auth_bundle
) -> None:
    _save_query_ids(paths)

    async def first_handler(request: httpx.Request) -> httpx.Response:
        _operation, variables = _op_and_variables(request)
        cursor = variables.get("cursor")
        count = int(variables["count"])
        if count == 1:
            return httpx.Response(
                200, json=make_bookmarks_response(["1"], cursor="probe-cursor"), request=request
            )
        if cursor is None:
            return httpx.Response(
                200, json=make_bookmarks_response(["1"], cursor="c1"), request=request
            )
        raise AssertionError(f"unexpected request cursor {cursor}")

    first = await sync_collection(
        "bookmarks",
        full=False,
        limit=1,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        query_ids={"Bookmarks": "qid-bookmarks"},
        transport=httpx.MockTransport(first_handler),
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
    )
    assert first.pages_fetched == 1

    console_buffer = StringIO()
    console = Console(file=console_buffer, force_terminal=False, color_system=None)

    async def second_handler(request: httpx.Request) -> httpx.Response:
        _operation, variables = _op_and_variables(request)
        cursor = variables.get("cursor")
        count = int(variables["count"])
        if count == 1:
            return httpx.Response(
                200, json=make_bookmarks_response(["new"], cursor="probe-cursor"), request=request
            )
        if cursor is None:
            return httpx.Response(
                200, json=make_bookmarks_response(["new", "1"], cursor="ignored"), request=request
            )
        if cursor == "c1":
            return httpx.Response(
                200, json=make_bookmarks_response(["2"], cursor="c2"), request=request
            )
        if cursor == "c2":
            return httpx.Response(200, json=make_bookmarks_response(["3"]), request=request)
        raise AssertionError(f"unexpected request cursor {cursor}")

    second = await sync_collection(
        "bookmarks",
        full=False,
        limit=None,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        query_ids={"Bookmarks": "qid-bookmarks"},
        transport=httpx.MockTransport(second_handler),
        console=console,
        sleep=lambda _: asyncio.sleep(0),
    )

    assert second.pages_fetched == 3
    output = console_buffer.getvalue()
    assert "bookmarks: starting head pass" in output
    assert "bookmarks head: page 1, page_tweets 2, total_tweets 2, stop=duplicate" in output
    assert "bookmarks: resuming saved backfill pass" in output
    assert "bookmarks backfill: page 1, page_tweets 1, total_tweets 1, stop=continue" in output


@pytest.mark.asyncio
async def test_sync_collection_head_only_clears_saved_backfill_state(
    paths, config: AppConfig, auth_bundle
) -> None:
    _save_query_ids(paths)

    async def first_handler(request: httpx.Request) -> httpx.Response:
        _operation, variables = _op_and_variables(request)
        cursor = variables.get("cursor")
        count = int(variables["count"])
        if count == 1:
            return httpx.Response(
                200, json=make_bookmarks_response(["1"], cursor="probe-cursor"), request=request
            )
        if cursor is None:
            return httpx.Response(
                200, json=make_bookmarks_response(["1"], cursor="c1"), request=request
            )
        raise AssertionError(f"unexpected request cursor {cursor}")

    first = await sync_collection(
        "bookmarks",
        full=False,
        limit=1,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        query_ids={"Bookmarks": "qid-bookmarks"},
        transport=httpx.MockTransport(first_handler),
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
    )
    assert first.pages_fetched == 1

    console_buffer = StringIO()
    console = Console(file=console_buffer, force_terminal=False, color_system=None)

    async def second_handler(request: httpx.Request) -> httpx.Response:
        _operation, variables = _op_and_variables(request)
        cursor = variables.get("cursor")
        count = int(variables["count"])
        if count == 1:
            return httpx.Response(
                200, json=make_bookmarks_response(["new"], cursor="probe-cursor"), request=request
            )
        if cursor is None:
            return httpx.Response(
                200, json=make_bookmarks_response(["new", "1"], cursor="ignored"), request=request
            )
        raise AssertionError(f"unexpected request cursor {cursor}")

    reporter = PipelineReporter(console, "sync", interactive=False)
    with reporter:
        second = await sync_collection(
            "bookmarks",
            full=False,
            head_only=True,
            limit=None,
            config=config,
            paths=paths,
            auth_bundle=auth_bundle,
            query_ids={"Bookmarks": "qid-bookmarks"},
            transport=httpx.MockTransport(second_handler),
            console=console,
            sleep=lambda _: asyncio.sleep(0),
        )

    assert second.pages_fetched == 1
    assert reporter.has_step("sync:bookmarks:head")
    assert not reporter.has_step("sync:bookmarks:backfill")
    assert reporter._step_by_key["sync:bookmarks:head"].metrics == {
        "new_tweets": 1,
        "tweets_seen": 2,
        "pages": 1,
    }

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        state = store.get_sync_state("bookmark")
        assert state.backfill_incomplete is False
        assert state.backfill_cursor is None
        assert state.last_head_tweet_id == "new"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_sync_collection_clears_saved_backfill_after_empty_backfill_page(
    paths, config: AppConfig, auth_bundle
) -> None:
    _save_query_ids(paths)

    async def first_handler(request: httpx.Request) -> httpx.Response:
        _operation, variables = _op_and_variables(request)
        cursor = variables.get("cursor")
        count = int(variables["count"])
        if count == 1:
            return httpx.Response(
                200, json=make_bookmarks_response(["1"], cursor="probe-cursor"), request=request
            )
        if cursor is None:
            return httpx.Response(
                200, json=make_bookmarks_response(["1"], cursor="c1"), request=request
            )
        raise AssertionError(f"unexpected request cursor {cursor}")

    first = await sync_collection(
        "bookmarks",
        full=False,
        limit=1,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        query_ids={"Bookmarks": "qid-bookmarks"},
        transport=httpx.MockTransport(first_handler),
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
    )
    assert first.pages_fetched == 1

    console_buffer = StringIO()
    console = Console(file=console_buffer, force_terminal=False, color_system=None)

    async def second_handler(request: httpx.Request) -> httpx.Response:
        _operation, variables = _op_and_variables(request)
        cursor = variables.get("cursor")
        count = int(variables["count"])
        if count == 1:
            return httpx.Response(
                200, json=make_bookmarks_response(["new"], cursor="probe-cursor"), request=request
            )
        if cursor is None:
            return httpx.Response(
                200, json=make_bookmarks_response(["new", "1"], cursor="ignored"), request=request
            )
        if cursor == "c1":
            return httpx.Response(
                200,
                json=_bookmarks_response_from_results([], cursor="c1"),
                request=request,
            )
        raise AssertionError(f"unexpected request cursor {cursor}")

    reporter = PipelineReporter(console, "sync", interactive=False)
    with reporter:
        second = await sync_collection(
            "bookmarks",
            full=False,
            limit=None,
            config=config,
            paths=paths,
            auth_bundle=auth_bundle,
            query_ids={"Bookmarks": "qid-bookmarks"},
            transport=httpx.MockTransport(second_handler),
            console=console,
            sleep=lambda _: asyncio.sleep(0),
        )

    assert second.pages_fetched == 2
    assert reporter.has_step("sync:bookmarks:head")
    assert not reporter.has_step("sync:bookmarks:backfill")
    assert reporter._step_by_key["sync:bookmarks:head"].summary.endswith(
        "Twitter/X returned no tweets"
    )

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        state = store.get_sync_state("bookmark")
        assert state.backfill_incomplete is False
        assert state.backfill_cursor is None
        assert state.last_head_tweet_id == "new"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_head_only_cannot_be_combined_with_backfill_modes(
    paths, config: AppConfig, auth_bundle
) -> None:
    _save_query_ids(paths)

    with pytest.raises(ConfigError, match="--head-only cannot be combined"):
        await sync_collection(
            "bookmarks",
            full=False,
            backfill=True,
            head_only=True,
            limit=None,
            config=config,
            paths=paths,
            auth_bundle=auth_bundle,
            query_ids={"Bookmarks": "qid-bookmarks"},
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, json=make_bookmarks_response(["1"]), request=request
                )
            ),
            console=_console(),
            sleep=lambda _: asyncio.sleep(0),
        )


@pytest.mark.asyncio
async def test_sync_user_tweets_end_to_end_and_resume(
    paths, config: AppConfig, auth_bundle
) -> None:
    _save_query_ids(paths)
    seen: list[tuple[str, str | None, int]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        operation, variables = _op_and_variables(request)
        cursor = variables.get("cursor")
        count = int(variables["count"])
        seen.append((operation, cursor if isinstance(cursor, str) else None, count))

        if count == 1:
            return httpx.Response(
                200,
                json=make_user_tweets_response(["10"], cursor="probe-cursor"),
                request=request,
            )
        if cursor is None:
            return httpx.Response(
                200,
                json=make_user_tweets_response(["10"], cursor="c1"),
                request=request,
            )
        if cursor == "c1":
            return httpx.Response(
                200,
                json=make_user_tweets_response(["11"], cursor="c2"),
                request=request,
            )
        if cursor == "c2":
            return httpx.Response(200, json=make_user_tweets_response(["12"]), request=request)
        raise AssertionError(f"unexpected request cursor {cursor}")

    first = await sync_collection(
        "tweets",
        full=False,
        limit=1,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        query_ids={"UserTweets": "qid-user-tweets"},
        transport=httpx.MockTransport(handler),
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
    )
    assert first.pages_fetched == 1

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        state = store.get_sync_state("tweet")
        assert state.backfill_incomplete is True
        assert state.backfill_cursor == "c1"
    finally:
        store.close()

    seen.clear()

    async def second_handler(request: httpx.Request) -> httpx.Response:
        operation, variables = _op_and_variables(request)
        cursor = variables.get("cursor")
        count = int(variables["count"])
        seen.append((operation, cursor if isinstance(cursor, str) else None, count))
        if count == 1:
            return httpx.Response(
                200,
                json=make_user_tweets_response(["new"], cursor="probe-cursor"),
                request=request,
            )
        if cursor is None:
            return httpx.Response(
                200,
                json=make_user_tweets_response(["new", "10"], cursor="ignored"),
                request=request,
            )
        if cursor == "c1":
            return httpx.Response(
                200,
                json=make_user_tweets_response(["11"], cursor="c2"),
                request=request,
            )
        if cursor == "c2":
            return httpx.Response(200, json=make_user_tweets_response(["12"]), request=request)
        raise AssertionError(f"unexpected request cursor {cursor}")

    second = await sync_collection(
        "tweets",
        full=False,
        limit=None,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        query_ids={"UserTweets": "qid-user-tweets"},
        transport=httpx.MockTransport(second_handler),
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
    )

    assert second.pages_fetched == 3
    assert [item for item in seen if item[2] == 20][:2] == [
        ("UserTweets", None, 20),
        ("UserTweets", "c1", 20),
    ]

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        counts = store.counts()
        assert counts["raw_captures"] == 4
        assert counts["tweets"] == 4
        assert counts["collections"] == 4
        state = store.get_sync_state("tweet")
        assert state.backfill_incomplete is False
        assert state.last_head_tweet_id == "new"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_full_sync_leaves_resumable_backfill_state(
    paths, config: AppConfig, auth_bundle
) -> None:
    _save_query_ids(paths)

    async def first_handler(request: httpx.Request) -> httpx.Response:
        operation, variables = _op_and_variables(request)
        cursor = variables.get("cursor")
        count = int(variables["count"])
        if count == 1:
            return httpx.Response(
                200,
                json=make_bookmarks_response(["1"], cursor="probe-cursor"),
                request=request,
            )
        if operation == "Bookmarks" and cursor is None:
            return httpx.Response(
                200, json=make_bookmarks_response(["1"], cursor="c1"), request=request
            )
        if cursor == "c1":
            return httpx.Response(200, json=make_bookmarks_response(["2"]), request=request)
        raise AssertionError(f"unexpected cursor {cursor}")

    first = await sync_collection(
        "bookmarks",
        full=True,
        limit=1,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        query_ids={"Bookmarks": "qid-bookmarks"},
        transport=httpx.MockTransport(first_handler),
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
    )
    assert first.pages_fetched == 1

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        state = store.get_sync_state("bookmark")
        assert state.backfill_incomplete is True
        assert state.backfill_cursor == "c1"
    finally:
        store.close()

    async def second_handler(request: httpx.Request) -> httpx.Response:
        operation, variables = _op_and_variables(request)
        cursor = variables.get("cursor")
        count = int(variables["count"])
        if count == 1:
            return httpx.Response(200, json=make_bookmarks_response(["3"]), request=request)
        if operation == "Bookmarks" and cursor is None:
            return httpx.Response(
                200,
                json=make_bookmarks_response(["3", "1"], cursor="ignored"),
                request=request,
            )
        if cursor == "c1":
            return httpx.Response(200, json=make_bookmarks_response(["2"]), request=request)
        raise AssertionError(f"unexpected cursor {cursor}")

    second = await sync_collection(
        "bookmarks",
        full=False,
        limit=None,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        query_ids={"Bookmarks": "qid-bookmarks"},
        transport=httpx.MockTransport(second_handler),
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
    )
    assert second.pages_fetched == 2

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        assert store.counts()["tweets"] == 3
    finally:
        store.close()
