from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest

import tweetnook.importer.archive_enrich as archive_enrich
from tests.archive_import.conftest import (
    _auth_bundle,
    _console,
    _detail_entry_payload,
    _empty_detail_entry_payload,
)
from tests.conftest import make_tweet_detail_response, make_tweet_result, request_details
from tweetnook.config import AppConfig
from tweetnook.core.exceptions import (
    RepeatedFocalAbsenceError,
)
from tweetnook.storage import open_archive_store


def test_enrich_pending_rows_aborts_after_three_absences_and_flushes_prior_rows(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store._merge_records(
        [
            store._record(
                row_key=f"tweet_object:{tweet_id}",
                record_type="tweet_object",
                tweet_id=tweet_id,
                enrichment_state="pending",
            )
            for tweet_id in ("1", "2", "3", "4", "5")
        ]
    )
    store.close()
    attempts: list[str] = []

    @asynccontextmanager
    async def fake_locked_archive_job(*, config=None, paths=None, console=None):
        opened = open_archive_store(paths, create=False)
        assert opened is not None
        try:
            yield SimpleNamespace(store=opened, mark_dirty=lambda **_kwargs: None)
        finally:
            opened.close()

    async def fake_resolve_query_ids(*_args, **_kwargs):
        return {"TweetDetail": "detail-query-id"}

    async def fake_fetch_page(*args, **_kwargs):
        _operation, variables = request_details(args[1])
        tweet_id = variables["focalTweetId"]
        attempts.append(tweet_id)
        if tweet_id == "1":
            payload = make_tweet_detail_response(
                [make_tweet_result("1", "completed before breaker", user_id="42")]
            )
        else:
            payload = make_tweet_detail_response([make_tweet_result("999", "unrelated")])
        return httpx.Response(200, json=payload, request=httpx.Request("GET", args[1]))

    class DummyClient:
        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(archive_enrich, "locked_archive_job", fake_locked_archive_job)
    monkeypatch.setattr(archive_enrich, "resolve_query_ids", fake_resolve_query_ids)
    monkeypatch.setattr(
        archive_enrich, "build_async_client", lambda *_args, **_kwargs: DummyClient()
    )
    monkeypatch.setattr(archive_enrich, "fetch_page", fake_fetch_page)

    with pytest.raises(RepeatedFocalAbsenceError):
        asyncio.run(
            archive_enrich._enrich_pending_rows(
                limit=None,
                config=AppConfig(),
                paths=paths,
                auth_bundle=_auth_bundle(),
                transport=None,
                console=_console(),
            )
        )

    assert attempts == ["1", "2", "3", "4"]
    store = open_archive_store(paths, create=False)
    assert store is not None
    assert store._get_row("tweet_object:1")["enrichment_state"] == "done"
    for tweet_id in ("2", "3", "4"):
        assert store._get_row(f"tweet_object:{tweet_id}")["enrichment_state"] == (
            "transient_failure"
        )
    assert store._get_row("tweet_object:5")["enrichment_state"] == "pending"
    store.close()


def test_enrich_focal_absence_counter_resets_on_explicit_empty_and_available_results(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    tweet_ids = ("1", "2", "3", "4", "5", "6", "7")
    store = open_archive_store(paths, create=True)
    assert store is not None
    store._merge_records(
        [
            store._record(
                row_key=f"tweet_object:{tweet_id}",
                record_type="tweet_object",
                tweet_id=tweet_id,
                enrichment_state="pending",
            )
            for tweet_id in tweet_ids
        ]
    )
    store.close()

    @asynccontextmanager
    async def fake_locked_archive_job(*, config=None, paths=None, console=None):
        opened = open_archive_store(paths, create=False)
        assert opened is not None
        try:
            yield SimpleNamespace(store=opened, mark_dirty=lambda **_kwargs: None)
        finally:
            opened.close()

    async def fake_resolve_query_ids(*_args, **_kwargs):
        return {"TweetDetail": "detail-query-id"}

    async def fake_fetch_page(*args, **_kwargs):
        _operation, variables = request_details(args[1])
        tweet_id = variables["focalTweetId"]
        if tweet_id == "2":
            payload = _detail_entry_payload(
                f"conversationthread-999-tweet-{tweet_id}",
                {
                    "__typename": "TweetUnavailable",
                    "reason": "This account is suspended.",
                },
            )
        elif tweet_id == "4":
            payload = make_tweet_detail_response(
                [make_tweet_result(tweet_id, "available reset", user_id="42")]
            )
        elif tweet_id == "6":
            payload = _empty_detail_entry_payload(f"tweet-{tweet_id}")
        else:
            payload = make_tweet_detail_response([make_tweet_result("999", "unrelated")])
        return httpx.Response(200, json=payload, request=httpx.Request("GET", args[1]))

    class DummyClient:
        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(archive_enrich, "locked_archive_job", fake_locked_archive_job)
    monkeypatch.setattr(archive_enrich, "resolve_query_ids", fake_resolve_query_ids)
    monkeypatch.setattr(
        archive_enrich, "build_async_client", lambda *_args, **_kwargs: DummyClient()
    )
    monkeypatch.setattr(archive_enrich, "fetch_page", fake_fetch_page)

    result = asyncio.run(
        archive_enrich._enrich_pending_rows(
            limit=None,
            config=AppConfig(),
            paths=paths,
            auth_bundle=_auth_bundle(),
            transport=None,
            console=_console(),
        )
    )

    assert result.selected == 7
    assert result.classified_unavailable == 2
    assert result.completed == 1
    assert result.transient_failures == 4


def test_enrich_pending_rows_flushes_when_client_close_fails(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store._merge_records(
        [
            store._record(
                row_key="tweet_object:1",
                record_type="tweet_object",
                tweet_id="1",
                enrichment_state="pending",
            )
        ]
    )
    store.close()

    @asynccontextmanager
    async def fake_locked_archive_job(*, config=None, paths=None, console=None):
        opened = open_archive_store(paths, create=False)
        assert opened is not None
        try:
            yield SimpleNamespace(store=opened, mark_dirty=lambda **_kwargs: None)
        finally:
            opened.close()

    async def fake_resolve_query_ids(*_args, **_kwargs):
        return {"TweetDetail": "detail-query-id"}

    async def fake_fetch_page(*args, **_kwargs):
        payload = make_tweet_detail_response([make_tweet_result("1", "complete")])
        return httpx.Response(200, json=payload, request=httpx.Request("GET", args[1]))

    class FailingCloseClient:
        async def aclose(self) -> None:
            raise RuntimeError("close failed")

    monkeypatch.setattr(archive_enrich, "locked_archive_job", fake_locked_archive_job)
    monkeypatch.setattr(archive_enrich, "resolve_query_ids", fake_resolve_query_ids)
    monkeypatch.setattr(
        archive_enrich,
        "build_async_client",
        lambda *_args, **_kwargs: FailingCloseClient(),
    )
    monkeypatch.setattr(archive_enrich, "fetch_page", fake_fetch_page)

    with pytest.raises(RuntimeError, match="close failed"):
        asyncio.run(
            archive_enrich._enrich_pending_rows(
                limit=None,
                config=AppConfig(),
                paths=paths,
                auth_bundle=_auth_bundle(),
                transport=None,
                console=_console(),
            )
        )

    store = open_archive_store(paths, create=False)
    assert store is not None
    assert store._get_row("tweet_object:1")["enrichment_state"] == "done"
    store.close()


def test_enrich_pending_rows_flushes_completed_writes_on_interrupt(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store._merge_records(
        [
            store._record(
                row_key=f"tweet_object:{tweet_id}",
                record_type="tweet_object",
                tweet_id=tweet_id,
                enrichment_state="pending",
            )
            for tweet_id in ("1", "2")
        ]
    )
    store.close()

    @asynccontextmanager
    async def fake_locked_archive_job(*, config=None, paths=None, console=None):
        store = open_archive_store(paths, create=False)
        assert store is not None
        try:
            yield SimpleNamespace(store=store, mark_dirty=lambda **_kwargs: None)
        finally:
            store.close()

    async def fake_resolve_query_ids(*_args, **_kwargs):
        return {"TweetDetail": "detail-query-id"}

    attempts = 0

    async def fake_fetch_page(*args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise KeyboardInterrupt
        payload = make_tweet_detail_response([make_tweet_result("1", "complete", user_id="42")])
        return httpx.Response(200, json=payload, request=httpx.Request("GET", args[1]))

    class DummyClient:
        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(archive_enrich, "locked_archive_job", fake_locked_archive_job)
    monkeypatch.setattr(archive_enrich, "resolve_query_ids", fake_resolve_query_ids)
    monkeypatch.setattr(
        archive_enrich, "build_async_client", lambda *_args, **_kwargs: DummyClient()
    )
    monkeypatch.setattr(archive_enrich, "fetch_page", fake_fetch_page)

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(
            archive_enrich._enrich_pending_rows(
                limit=None,
                config=AppConfig(),
                paths=paths,
                auth_bundle=_auth_bundle(),
                transport=None,
                console=_console(),
            )
        )

    store = open_archive_store(paths, create=False)
    assert store is not None
    assert store._get_row("tweet_object:1")["enrichment_state"] == "done"
    assert store._get_row("tweet_object:2")["enrichment_state"] == "pending"
    store.close()


def test_enrich_pending_rows_aborts_on_unexpected_parser_error_without_mutating_rows(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store._merge_records(
        [
            store._record(
                row_key=f"tweet_object:{tweet_id}",
                record_type="tweet_object",
                tweet_id=tweet_id,
                enrichment_state="pending",
            )
            for tweet_id in ("1", "2")
        ]
    )
    store.close()

    @asynccontextmanager
    async def fake_locked_archive_job(*, config=None, paths=None, console=None):
        store = open_archive_store(paths, create=False)
        assert store is not None
        try:
            yield SimpleNamespace(store=store, mark_dirty=lambda **_kwargs: None)
        finally:
            store.close()

    async def fake_resolve_query_ids(*_args, **_kwargs):
        return {"TweetDetail": "detail-query-id"}

    attempts = 0

    async def fake_fetch_page(*args, **_kwargs):
        nonlocal attempts
        attempts += 1
        payload = make_tweet_detail_response(
            [make_tweet_result("1", "would be complete", user_id="42")]
        )
        return httpx.Response(200, json=payload, request=httpx.Request("GET", args[1]))

    class DummyClient:
        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(archive_enrich, "locked_archive_job", fake_locked_archive_job)
    monkeypatch.setattr(archive_enrich, "resolve_query_ids", fake_resolve_query_ids)
    monkeypatch.setattr(
        archive_enrich, "build_async_client", lambda *_args, **_kwargs: DummyClient()
    )
    monkeypatch.setattr(archive_enrich, "fetch_page", fake_fetch_page)
    monkeypatch.setattr(
        archive_enrich,
        "parse_tweet_detail_response",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TypeError("parser bug")),
    )

    with pytest.raises(TypeError, match="parser bug"):
        asyncio.run(
            archive_enrich._enrich_pending_rows(
                limit=None,
                config=AppConfig(),
                paths=paths,
                auth_bundle=_auth_bundle(),
                transport=None,
                console=_console(),
            )
        )

    assert attempts == 1
    store = open_archive_store(paths, create=False)
    assert store is not None
    assert store._get_row("tweet_object:1")["enrichment_state"] == "pending"
    assert store._get_row("tweet_object:2")["enrichment_state"] == "pending"
    store.close()


def test_enrich_pending_rows_keeps_known_transport_errors_per_row_retryable(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store._merge_records(
        [
            store._record(
                row_key="tweet_object:1",
                record_type="tweet_object",
                tweet_id="1",
                enrichment_state="pending",
            )
        ]
    )
    store.close()

    @asynccontextmanager
    async def fake_locked_archive_job(*, config=None, paths=None, console=None):
        store = open_archive_store(paths, create=False)
        assert store is not None
        try:
            yield SimpleNamespace(store=store, mark_dirty=lambda **_kwargs: None)
        finally:
            store.close()

    async def fake_resolve_query_ids(*_args, **_kwargs):
        return {"TweetDetail": "detail-query-id"}

    async def fake_fetch_page(*args, **_kwargs):
        request = httpx.Request("GET", args[1])
        raise httpx.ConnectError("offline", request=request)

    class DummyClient:
        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(archive_enrich, "locked_archive_job", fake_locked_archive_job)
    monkeypatch.setattr(archive_enrich, "resolve_query_ids", fake_resolve_query_ids)
    monkeypatch.setattr(
        archive_enrich, "build_async_client", lambda *_args, **_kwargs: DummyClient()
    )
    monkeypatch.setattr(archive_enrich, "fetch_page", fake_fetch_page)

    result = asyncio.run(
        archive_enrich._enrich_pending_rows(
            limit=None,
            config=AppConfig(),
            paths=paths,
            auth_bundle=_auth_bundle(),
            transport=None,
            console=_console(),
        )
    )

    assert result.transient_failures == 1
    store = open_archive_store(paths, create=False)
    assert store is not None
    row = store._get_row("tweet_object:1")
    assert row["enrichment_state"] == "transient_failure"
    assert row["enrichment_reason"] is None
    assert row["enrichment_retry_count"] == 1
    assert row["enrichment_next_retry_at"] is not None
    store.close()
