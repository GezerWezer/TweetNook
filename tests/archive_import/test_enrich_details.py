from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from types import SimpleNamespace

import httpx
import pytest

import tweetnook.importer.archive as archive_import
import tweetnook.importer.archive_enrich as archive_enrich
import tweetnook.importer.archive_rows as archive_rows
from tests.archive_import.conftest import (
    _auth_bundle,
    _console,
    _detail_entry_payload,
    _empty_detail_entry_payload,
)
from tests.conftest import make_tweet_detail_response, make_tweet_result, request_details
from tweetnook.config import AppConfig
from tweetnook.storage import open_archive_store


@pytest.mark.parametrize(
    ("limit", "expected_attempts"),
    [(None, 1_200), (200, 200)],
)
def test_enrich_pending_rows_uses_full_snapshot_or_explicit_limit(
    paths,
    monkeypatch: pytest.MonkeyPatch,
    limit: int | None,
    expected_attempts: int,
) -> None:
    total_rows = 1_200
    store = open_archive_store(paths, create=True)
    assert store is not None
    store._merge_records(
        [
            store._record(
                row_key=f"tweet_object:{tweet_id}",
                record_type="tweet_object",
                tweet_id=str(tweet_id),
                enrichment_state="pending",
                source="x_archive",
            )
            for tweet_id in range(1, total_rows + 1)
        ]
    )
    store.close()
    attempts: list[str] = []
    dirty_calls: list[tuple[int, int]] = []
    progress_totals: list[int] = []
    progress_updates: list[tuple[int, int]] = []

    @asynccontextmanager
    async def fake_locked_archive_job(*, config=None, paths=None, console=None):
        opened = open_archive_store(paths, create=False)
        assert opened is not None

        def mark_dirty(*, rows: int, batches: int) -> None:
            dirty_calls.append((rows, batches))

        try:
            yield SimpleNamespace(store=opened, mark_dirty=mark_dirty)
        finally:
            opened.close()

    @contextmanager
    def fake_progress_callback(_console, *, total: int, **_kwargs):
        progress_totals.append(total)
        yield lambda current, callback_total: progress_updates.append((current, callback_total))

    async def fake_resolve_query_ids(*_args, **_kwargs):
        return {"TweetDetail": "detail-query-id"}

    async def fake_fetch_page(*args, **_kwargs):
        _operation, variables = request_details(args[1])
        tweet_id = variables["focalTweetId"]
        attempts.append(tweet_id)
        payload = make_tweet_detail_response(
            [make_tweet_result(tweet_id, f"enriched {tweet_id}", user_id="777")]
        )
        return httpx.Response(200, json=payload, request=httpx.Request("GET", args[1]))

    class DummyClient:
        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(archive_enrich, "locked_archive_job", fake_locked_archive_job)
    monkeypatch.setattr(archive_enrich, "_progress_callback", fake_progress_callback)
    monkeypatch.setattr(archive_enrich, "resolve_query_ids", fake_resolve_query_ids)
    monkeypatch.setattr(
        archive_enrich, "build_async_client", lambda *_args, **_kwargs: DummyClient()
    )
    monkeypatch.setattr(archive_enrich, "fetch_page", fake_fetch_page)

    result = asyncio.run(
        archive_enrich._enrich_pending_rows(
            limit=limit,
            config=AppConfig(),
            paths=paths,
            auth_bundle=_auth_bundle(),
            transport=None,
            console=_console(),
        )
    )

    assert result.selected == expected_attempts
    assert result.completed == expected_attempts
    assert len(attempts) == expected_attempts
    assert len(set(attempts)) == expected_attempts
    assert progress_totals == [expected_attempts]
    assert progress_updates[-1] == (expected_attempts, expected_attempts)
    assert sum(rows for rows, _batches in dirty_calls) == expected_attempts
    store = open_archive_store(paths, create=False)
    assert store is not None
    assert store._count("record_type = 'tweet_object' AND enrichment_state = 'done'") == (
        expected_attempts
    )
    assert store._count("record_type = 'tweet_object' AND enrichment_state = 'pending'") == (
        total_rows - expected_attempts
    )
    store.close()


def test_enrich_pending_rows_uses_command_start_time_for_stable_snapshot(
    paths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started_at = "2099-01-01T00:00:00+00:00"
    store = open_archive_store(paths, create=True)
    assert store is not None
    store._merge_records(
        [
            store._record(
                row_key="tweet_object:1",
                record_type="tweet_object",
                tweet_id="1",
                enrichment_state="transient_failure",
                enrichment_next_retry_at=started_at,
            ),
            store._record(
                row_key="tweet_object:2",
                record_type="tweet_object",
                tweet_id="2",
                enrichment_state="transient_failure",
                enrichment_next_retry_at="2099-01-01T00:01:00+00:00",
            ),
        ]
    )
    store.close()
    selection_calls: list[tuple[int | None, str | None]] = []
    attempts: list[str] = []
    original_selector = archive_import.ArchiveStore.list_tweet_objects_for_enrichment

    def tracked_selector(self, *, limit=None, now=None):
        selection_calls.append((limit, now))
        return original_selector(self, limit=limit, now=now)

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
        payload = make_tweet_detail_response([make_tweet_result(tweet_id, "available")])
        return httpx.Response(200, json=payload, request=httpx.Request("GET", args[1]))

    class DummyClient:
        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        archive_import.ArchiveStore,
        "list_tweet_objects_for_enrichment",
        tracked_selector,
    )
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
            started_at=started_at,
        )
    )

    assert result.selected == 1
    assert attempts == ["1"]
    assert selection_calls == [(None, started_at)]
    store = open_archive_store(paths, create=False)
    assert store is not None
    assert store._get_row("tweet_object:1")["enrichment_state"] == "done"
    delayed = store._get_row("tweet_object:2")
    assert delayed["enrichment_state"] == "transient_failure"
    assert delayed["enrichment_next_retry_at"] == "2099-01-01T00:01:00+00:00"
    store.close()


def test_enrich_pending_rows_batches_detail_writes(paths, monkeypatch: pytest.MonkeyPatch) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    buffer = archive_rows._PageBuffer()
    for index in range(12):
        tweet_id = str(1000 + index)
        store._queue_record(
            store._tweet_object_record(
                archive_rows._PlaceholderTweetObject(
                    tweet_id=tweet_id,
                    text=f"placeholder {tweet_id}",
                ),
                source=archive_rows.ARCHIVE_SOURCE,
                enrichment_state="pending",
                cursor=buffer,
            ),
            cursor=buffer,
        )
    archive_rows._flush_buffer(store, buffer)
    store.close()
    dirty_calls: list[tuple[int, int]] = []

    @asynccontextmanager
    async def fake_locked_archive_job(*, config=None, paths=None, console=None):
        store = open_archive_store(paths, create=False)
        assert store is not None

        class _Job:
            def __init__(self, store):
                self.store = store

            def mark_dirty(self, rows: int = 1, batches: int = 1) -> None:
                dirty_calls.append((rows, batches))

        try:
            yield _Job(store)
        finally:
            store.close()

    async def fake_resolve_query_ids(*_args, **_kwargs):
        return {"TweetDetail": "detail-query-id"}

    async def fake_fetch_page(*args, **_kwargs):
        detail_url = args[1]
        _operation, variables = request_details(detail_url)
        tweet_id = variables["focalTweetId"]
        payload = make_tweet_detail_response(
            [make_tweet_result(tweet_id, f"enriched {tweet_id}", user_id="777")]
        )
        return httpx.Response(
            200,
            json=payload,
            request=httpx.Request("GET", detail_url),
        )

    class DummyClient:
        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(archive_enrich, "_DETAIL_ENRICH_WRITE_BATCH", 5)
    monkeypatch.setattr(archive_enrich, "locked_archive_job", fake_locked_archive_job)
    monkeypatch.setattr(archive_enrich, "resolve_query_ids", fake_resolve_query_ids)
    monkeypatch.setattr(
        archive_enrich, "build_async_client", lambda *_args, **_kwargs: DummyClient()
    )
    monkeypatch.setattr(archive_enrich, "fetch_page", fake_fetch_page)

    refreshed, terminal, transient, pending = asyncio.run(
        archive_enrich._enrich_pending_rows(
            limit=None,
            config=AppConfig(),
            paths=paths,
            auth_bundle=_auth_bundle(),
            transport=None,
            console=_console(),
        )
    )

    assert (refreshed, terminal, transient, pending) == (12, 0, 0, 0)
    assert dirty_calls == [(5, 1), (5, 1), (2, 1)]

    store = open_archive_store(paths, create=False)
    assert store is not None
    tweet_object_rows = store.list_tweet_objects_for_enrichment()
    store.close()
    assert tweet_object_rows == []


@pytest.mark.parametrize(
    ("message", "expected_reason", "retry_eligible", "expected_detail"),
    [
        ("These posts are protected.", "protected_account", 1, "These posts are protected."),
        ("This account is suspended.", "suspended_account", 1, "This account is suspended."),
        ("This account doesn't exist.", "account_missing", 1, "This account doesn't exist."),
        (
            "This Post was deleted by the Post author.",
            "deleted_by_author",
            0,
            "This Post was deleted by the Post author.",
        ),
        (
            "Dieses Posting ist nicht verfügbar.",
            "unavailable_unknown",
            1,
            "Dieses Posting ist nicht verfügbar.",
        ),
        (
            None,
            "unavailable_unknown",
            1,
            "TweetDetail returned an empty tweet_results object for the requested focal entry.",
        ),
    ],
)
def test_enrich_pending_rows_classifies_explicit_unavailable_shapes(
    paths,
    monkeypatch: pytest.MonkeyPatch,
    message: str | None,
    expected_reason: str,
    retry_eligible: int,
    expected_detail: str,
) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store._merge_records(
        [
            store._record(
                row_key="tweet_object:900",
                record_type="tweet_object",
                tweet_id="900",
                enrichment_state="pending",
                source="x_archive",
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
        if message is None:
            payload = _empty_detail_entry_payload("tweet-900")
        else:
            payload = _detail_entry_payload(
                "conversationthread-777-tweet-900",
                {
                    "__typename": "TweetTombstone",
                    "tombstone": {"text": {"text": message}},
                },
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

    assert result.selected == 1
    assert result.classified_unavailable == 1
    assert result.pending_enrichment == 0
    store = open_archive_store(paths, create=False)
    assert store is not None
    row = store._get_row("tweet_object:900")
    assert row is not None
    assert row["enrichment_state"] == "terminal_unavailable"
    assert row["enrichment_reason"] == expected_reason
    assert row["enrichment_detail"] == expected_detail
    assert row["enrichment_retry_eligible"] == retry_eligible
    store.close()


def test_enrich_pending_rows_defers_one_ambiguous_focal_absence(
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

    assert result.transient_failures == 1
    assert result.classified_unavailable == 0
    store = open_archive_store(paths, create=False)
    assert store is not None
    row = store._get_row("tweet_object:1")
    assert row["enrichment_state"] == "transient_failure"
    assert row["enrichment_reason"] == "focal_tweet_absent"
    assert "identifiable focal result" in row["enrichment_detail"]
    assert row["enrichment_next_retry_at"] is not None
    store.close()
