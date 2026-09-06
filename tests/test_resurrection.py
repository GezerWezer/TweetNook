from __future__ import annotations

from contextlib import asynccontextmanager
from io import StringIO
from types import SimpleNamespace

import httpx
import pytest
from rich.console import Console

import tweetnook.resurrection as resurrection
from tests.conftest import make_tweet_detail_response, make_tweet_result, request_details
from tweetnook.client.timelines import TimelineTweet
from tweetnook.pipeline import PipelineReporter
from tweetnook.query_ids import QueryIdStore
from tweetnook.storage import open_archive_store


@pytest.mark.asyncio
async def test_resurrection_marks_planned_step_skipped_when_due_queue_is_empty(
    paths,
    config,
    auth_bundle,
) -> None:
    store = open_archive_store(paths, create=True, config=config)
    assert store is not None
    store.close()
    console = Console(file=StringIO(), force_terminal=False, color_system=None)
    reporter = PipelineReporter(console, "resurrection", interactive=False)

    with reporter:
        result = await resurrection.resurrect_due_tweets(
            config=config,
            paths=paths,
            auth_bundle=auth_bundle,
            console=console,
        )

    assert result.attempted == 0
    assert reporter._step_by_key["resurrection"].state == "skipped"
    assert "no due retryable" in reporter._step_by_key["resurrection"].summary


def _terminal_row(
    store,
    tweet_id: str,
    reason: str,
    *,
    author_id: str | None = "42",
    retry_eligible: int = 1,
    next_retry_at: str | None = "2026-01-01T00:00:00+00:00",
    deleted_at: str | None = None,
):
    return store._record(
        row_key=f"tweet_object:{tweet_id}",
        record_type="tweet_object",
        tweet_id=tweet_id,
        author_id=author_id,
        enrichment_state="terminal_unavailable",
        enrichment_reason=reason,
        enrichment_retry_eligible=retry_eligible,
        enrichment_retry_count=0,
        enrichment_next_retry_at=next_retry_at,
        enrichment_checked_at="2025-01-01T00:00:00+00:00",
        deleted_at=deleted_at,
    )


def test_resurrection_retry_policy_is_reason_aware() -> None:
    assert resurrection.resurrection_retry_schedule("archive_deleted", 0) == (False, None)
    assert resurrection.resurrection_retry_schedule("deleted_by_author", 0) == (False, None)
    protected = resurrection.resurrection_retry_schedule(
        "protected_account", 0, now="2026-01-01T00:00:00+00:00"
    )
    unknown_first = resurrection.resurrection_retry_schedule(
        "unavailable_unknown", 0, now="2026-01-01T00:00:00+00:00"
    )
    unknown_later = resurrection.resurrection_retry_schedule(
        "unavailable_unknown", 3, now="2026-01-01T00:00:00+00:00"
    )
    assert protected == (True, "2026-01-08T00:00:00+00:00")
    assert unknown_first == (True, "2026-01-08T00:00:00+00:00")
    assert unknown_later == (True, "2026-06-30T00:00:00+00:00")


def test_resurrection_storage_excludes_permanent_future_and_deleted_rows(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store._merge_records(
        [
            _terminal_row(store, "archive", "archive_deleted", retry_eligible=0),
            _terminal_row(store, "author-delete", "deleted_by_author", retry_eligible=0),
            _terminal_row(store, "private", "protected_account"),
            _terminal_row(store, "suspended", "suspended_account"),
            _terminal_row(store, "missing", "account_missing"),
            _terminal_row(store, "unknown", "unavailable_unknown"),
            _terminal_row(
                store,
                "future",
                "protected_account",
                next_retry_at="2030-01-01T00:00:00+00:00",
            ),
            _terminal_row(
                store,
                "deleted-at",
                "unavailable_unknown",
                deleted_at="2025-01-01T00:00:00+00:00",
            ),
        ]
    )

    due = store.list_due_resurrection_tweets(now="2026-08-01T00:00:00+00:00")

    assert [row["tweet_id"] for row in due] == [
        "missing",
        "private",
        "suspended",
        "unknown",
    ]
    store.close()


def test_weighted_selection_uses_120_50_30_quota_and_spills(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    rows = []
    for prefix, reason, count in (
        ("p", "protected_account", 150),
        ("s", "suspended_account", 80),
        ("u", "unavailable_unknown", 60),
    ):
        rows.extend(_terminal_row(store, f"{prefix}{index:03d}", reason) for index in range(count))
    store._merge_records(rows)

    selected = resurrection.select_weighted_resurrection_candidates(
        store, budget=200, now="2026-08-01T00:00:00+00:00"
    )
    reasons = [row["enrichment_reason"] for row in selected]

    assert len(selected) == 200
    assert reasons.count("protected_account") == 120
    assert reasons.count("suspended_account") == 50
    assert reasons.count("unavailable_unknown") == 30
    assert len({row["tweet_id"] for row in selected}) == 200

    store._delete("record_type = 'tweet_object' AND enrichment_reason = 'suspended_account'")
    spilled = resurrection.select_weighted_resurrection_candidates(
        store, budget=200, now="2026-08-01T00:00:00+00:00"
    )
    assert len(spilled) == 200
    assert sum(row["enrichment_reason"] == "unavailable_unknown" for row in spilled) > 30
    store.close()


def test_persist_unavailable_tweet_preserves_rich_existing_fields(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    original_raw = '{"live":"payload"}'
    store._merge_records(
        [
            store._record(
                row_key="tweet_object:1",
                record_type="tweet_object",
                tweet_id="1",
                text="rich text",
                author_id="42",
                author_username="alice",
                created_at="Sat Mar 14 00:00:00 +0000 2026",
                raw_json=original_raw,
                enrichment_state="done",
                last_seen_at="2026-01-01T00:00:00+00:00",
            )
        ]
    )

    store.persist_unavailable_tweet(
        tweet_id="1",
        operation="TweetDetail",
        raw_json={"__typename": "TweetTombstone", "reason": "protected"},
        http_status=200,
        reason="protected_account",
        detail="These posts are protected.",
        retry_eligible=True,
        next_retry_at="2026-08-08T00:00:00+00:00",
    )

    row = store._get_row("tweet_object:1")
    assert row is not None
    assert row["text"] == "rich text"
    assert row["author_id"] == "42"
    assert row["author_username"] == "alice"
    assert row["raw_json"] == original_raw
    assert row["enrichment_reason"] == "protected_account"
    assert row["enrichment_detail"] == "These posts are protected."
    assert row["last_seen_at"] == "2026-01-01T00:00:00+00:00"

    store.persist_unavailable_tweet(
        tweet_id="missing",
        operation="TweetDetail",
        raw_json={"__typename": "TweetUnavailable"},
        http_status=200,
        reason="unavailable_unknown",
        detail=None,
        retry_eligible=True,
        next_retry_at="2026-08-08T00:00:00+00:00",
    )
    minimal = store._get_row("tweet_object:missing")
    assert minimal is not None
    assert minimal["enrichment_state"] == "terminal_unavailable"
    assert "TweetUnavailable" in minimal["raw_json"]
    store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("previous_reason", "response_message", "expected_reason", "is_absent"),
    [
        ("protected_account", None, "protected_account", True),
        (
            "suspended_account",
            "Dieses Posting ist nicht verfügbar.",
            "suspended_account",
            False,
        ),
        (
            "unavailable_unknown",
            "These posts are protected.",
            "protected_account",
            False,
        ),
        (
            "protected_account",
            "This post was deleted by its author.",
            "deleted_by_author",
            False,
        ),
    ],
)
async def test_resurrection_preserves_or_upgrades_reason_confidence(
    paths,
    config,
    auth_bundle,
    previous_reason: str,
    response_message: str | None,
    expected_reason: str,
    is_absent: bool,
) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    row = _terminal_row(store, "1", previous_reason)
    row["enrichment_detail"] = "original strong detail"
    store._merge_records([row])
    store.close()
    QueryIdStore(paths).save({"TweetDetail": "qid"})

    if is_absent:
        payload = make_tweet_detail_response([make_tweet_result("999", "unrelated")])
    else:
        payload = make_tweet_detail_response(
            [
                {
                    "__typename": "TweetUnavailable",
                    "rest_id": "1",
                    "reason": response_message,
                }
            ]
        )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    result = await resurrection.resurrect_due_tweets(
        budget=1,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(handler),
        sleep=lambda _delay: SimpleNamespace(__await__=lambda self: iter(())),
    )

    store = open_archive_store(paths, create=False)
    assert store is not None
    current = store._get_row("tweet_object:1")
    assert current["enrichment_reason"] == expected_reason
    if is_absent:
        assert result.transient_failures == 1
        assert result.still_unavailable == 0
        assert current["enrichment_retry_count"] == 0
        assert "original strong detail" in current["enrichment_detail"]
        assert "identifiable focal result" in current["enrichment_detail"]
    else:
        assert result.still_unavailable == 1
    store.close()


@pytest.mark.asyncio
async def test_account_probe_persists_due_across_budget_boundary(
    paths, config, auth_bundle
) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store._merge_records(
        [
            _terminal_row(store, "1", "protected_account", author_id="42"),
            _terminal_row(
                store,
                "2",
                "protected_account",
                author_id="42",
                next_retry_at="2030-01-01T00:00:00+00:00",
            ),
        ]
    )
    store.close()
    QueryIdStore(paths).save({"TweetDetail": "qid"})
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        _operation, variables = request_details(str(request.url))
        tweet_id = variables["focalTweetId"]
        attempts.append(tweet_id)
        payload = make_tweet_detail_response(
            [make_tweet_result(tweet_id, f"returned {tweet_id}", user_id="42")]
        )
        return httpx.Response(200, json=payload, request=request)

    first = await resurrection.resurrect_due_tweets(
        budget=1,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(handler),
        sleep=lambda _delay: SimpleNamespace(__await__=lambda self: iter(())),
    )

    assert first.attempted == 1
    assert first.account_probes == 1
    store = open_archive_store(paths, create=False)
    assert store is not None
    assert [row["tweet_id"] for row in store.list_due_resurrection_tweets()] == ["2"]
    store.close()

    second = await resurrection.resurrect_due_tweets(
        budget=1,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(handler),
        sleep=lambda _delay: SimpleNamespace(__await__=lambda self: iter(())),
    )

    assert second.attempted == 1
    assert attempts == ["1", "2"]


@pytest.mark.asyncio
async def test_resurrection_flushes_buffered_work_when_client_close_fails(
    paths, config, auth_bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store._merge_records([_terminal_row(store, "1", "protected_account")])
    store.close()
    QueryIdStore(paths).save({"TweetDetail": "qid"})

    class FailingCloseClient:
        async def aclose(self) -> None:
            raise RuntimeError("close failed")

    async def fake_fetch_page(*args, **_kwargs):
        payload = make_tweet_detail_response([make_tweet_result("1", "returned", user_id="42")])
        return httpx.Response(200, json=payload, request=httpx.Request("GET", args[1]))

    monkeypatch.setattr(
        resurrection,
        "build_async_client",
        lambda *_args, **_kwargs: FailingCloseClient(),
    )
    monkeypatch.setattr(resurrection, "fetch_page", fake_fetch_page)

    with pytest.raises(RuntimeError, match="close failed"):
        await resurrection.resurrect_due_tweets(
            budget=1,
            config=config,
            paths=paths,
            auth_bundle=auth_bundle,
        )

    store = open_archive_store(paths, create=False)
    assert store is not None
    assert store._get_row("tweet_object:1")["enrichment_state"] == "resurrected"
    store.close()


def test_successful_detail_clears_unavailable_scheduler_fields(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store._merge_records(
        [
            {
                **_terminal_row(store, "1", "protected_account"),
                "last_seen_at": "2025-01-01T00:00:00+00:00",
            },
        ]
    )
    store.update_tweet_object_enrichment(
        "1",
        enrichment_state="terminal_unavailable",
        enrichment_checked_at="2026-08-01T00:00:00+00:00",
        enrichment_http_status=200,
        enrichment_reason="protected_account",
        enrichment_detail="These posts are protected.",
        enrichment_retry_count=4,
        enrichment_first_unavailable_at="2026-01-01T00:00:00+00:00",
        enrichment_retry_eligible=True,
    )
    raw_tweet = make_tweet_result("1", "returned", user_id="42")
    store.persist_tweet_detail(
        tweet=TimelineTweet(
            tweet_id="1",
            text="returned",
            author_id="42",
            author_username="user42",
            author_display_name="User 42",
            created_at=None,
            sort_index=None,
            raw_json=raw_tweet,
        ),
        raw_json=make_tweet_detail_response([raw_tweet]),
    )

    row = store._get_row("tweet_object:1")
    assert row is not None
    assert row["enrichment_state"] == "resurrected"
    assert row["enrichment_reason"] is None
    assert row["enrichment_detail"] is None
    assert row["enrichment_retry_count"] == 0
    assert row["enrichment_next_retry_at"] is None
    assert row["enrichment_first_unavailable_at"] is None
    assert row["enrichment_retry_eligible"] == 0
    assert row["last_seen_at"] != "2025-01-01T00:00:00+00:00"
    assert store._count("record_type = 'raw_capture' AND operation = 'TweetDetail'") == 1
    store.close()


@pytest.mark.asyncio
async def test_account_recovery_boost_stays_inside_global_budget(
    paths, config, auth_bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = {
        "root": {
            "tweet_id": "root",
            "author_id": "42",
            "enrichment_reason": "protected_account",
            "enrichment_retry_count": 0,
        },
        **{
            f"probe-{index}": {
                "tweet_id": f"probe-{index}",
                "author_id": "42",
                "enrichment_reason": "protected_account",
                "enrichment_retry_count": 0,
            }
            for index in range(5)
        },
        **{
            f"boost-{index}": {
                "tweet_id": f"boost-{index}",
                "author_id": "42",
                "enrichment_reason": "protected_account",
                "enrichment_retry_count": 0,
            }
            for index in range(15)
        },
    }

    class FakeStore:
        def __init__(self):
            self.persisted: list[str] = []
            self.marked_due = 0

        def list_due_resurrection_tweets(
            self, *, reasons=None, exclude_tweet_ids=None, limit=None, now=None
        ):
            candidates = [rows["root"]]
            if reasons is not None:
                candidates = [row for row in candidates if row["enrichment_reason"] in reasons]
            if exclude_tweet_ids:
                candidates = [row for row in candidates if row["tweet_id"] not in exclude_tweet_ids]
            return candidates[:limit] if limit is not None else candidates

        def list_same_author_resurrection_tweets(
            self, author_id, *, exclude_tweet_ids=None, limit=5, due_only=False, now=None
        ):
            excluded = exclude_tweet_ids or set()
            candidates = [
                row
                for tweet_id, row in rows.items()
                if tweet_id != "root" and tweet_id not in excluded and row["author_id"] == author_id
            ]
            return candidates[:limit]

        def persist_tweet_detail(self, *, tweet, raw_json, http_status, cursor):
            self.persisted.append(tweet.tweet_id)

        def persist_unavailable_tweet(self, **kwargs):
            raise AssertionError(f"unexpected unavailable result: {kwargs}")

        def update_tweet_object_enrichment(self, *_args, **_kwargs):
            raise AssertionError("unexpected transient failure")

        def mark_author_resurrection_due(self, author_id, *, exclude_tweet_ids=None):
            self.marked_due += 1
            return 1

        def mark_tweets_resurrection_due(self, tweet_ids, *, due_at=None):
            self.marked_due += int(bool(tweet_ids))
            return len(tweet_ids)

        def count_due_resurrection_tweets(self):
            return 0

        def merge_rows(self, _rows):
            return None

    fake_store = FakeStore()
    job = SimpleNamespace(
        store=fake_store,
        mark_dirty=lambda **_kwargs: None,
    )

    @asynccontextmanager
    async def fake_locked_archive_job(**_kwargs):
        yield job

    class FakeQueryStore:
        def __init__(self, _paths):
            pass

        def is_fresh(self):
            return True

    class FakeClient:
        async def aclose(self):
            return None

    async def fake_resolve_query_ids(*_args, **_kwargs):
        return {"TweetDetail": "qid"}

    async def fake_fetch_page(*args, **_kwargs):
        _operation, variables = request_details(args[1])
        tweet_id = variables["focalTweetId"]
        payload = make_tweet_detail_response(
            [make_tweet_result(tweet_id, f"returned {tweet_id}", user_id="42")]
        )
        return httpx.Response(200, json=payload, request=httpx.Request("GET", args[1]))

    monkeypatch.setattr(resurrection, "locked_archive_job", fake_locked_archive_job)
    monkeypatch.setattr(resurrection, "QueryIdStore", FakeQueryStore)
    monkeypatch.setattr(resurrection, "resolve_query_ids", fake_resolve_query_ids)
    monkeypatch.setattr(resurrection, "build_async_client", lambda *_args, **_kwargs: FakeClient())
    monkeypatch.setattr(resurrection, "fetch_page", fake_fetch_page)

    result = await resurrection.resurrect_due_tweets(
        budget=10,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        console=Console(file=StringIO(), force_terminal=False),
        sleep=lambda _delay: SimpleNamespace(__await__=lambda self: iter(())),
    )

    assert result.attempted == 10
    assert result.resurrected == 10
    assert result.account_probes == 5
    assert result.account_boosts == 1
    assert result.account_rows_prioritized == 15
    assert fake_store.marked_due == 2
    assert len(fake_store.persisted) == 10


@pytest.mark.asyncio
async def test_unexpected_resurrection_parser_error_aborts_without_mutating_rows(
    paths, config, auth_bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store._merge_records(
        [
            _terminal_row(store, "1", "protected_account"),
            _terminal_row(store, "2", "protected_account"),
        ]
    )
    store.close()
    QueryIdStore(paths).save({"TweetDetail": "qid"})
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        _operation, variables = request_details(str(request.url))
        attempts.append(variables["focalTweetId"])
        payload = make_tweet_detail_response(
            [make_tweet_result(variables["focalTweetId"], "available", user_id="42")]
        )
        return httpx.Response(200, json=payload, request=request)

    def fail_parser(*_args, **_kwargs):
        raise RuntimeError("response-shape regression")

    monkeypatch.setattr(resurrection, "parse_tweet_detail_response", fail_parser)

    with pytest.raises(RuntimeError, match="response-shape regression"):
        await resurrection.resurrect_due_tweets(
            budget=200,
            config=config,
            paths=paths,
            auth_bundle=auth_bundle,
            transport=httpx.MockTransport(handler),
            sleep=lambda _delay: SimpleNamespace(__await__=lambda self: iter(())),
        )

    assert attempts == ["1"]
    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        for tweet_id in ("1", "2"):
            row = store._get_row(f"tweet_object:{tweet_id}")
            assert row["enrichment_state"] == "terminal_unavailable"
            assert row["enrichment_reason"] == "protected_account"
            assert row["enrichment_retry_count"] == 0
    finally:
        store.close()


@pytest.mark.asyncio
async def test_transport_failures_do_not_advance_availability_retry_count(
    paths, config, auth_bundle
) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    row = _terminal_row(store, "1", "unavailable_unknown")
    row["enrichment_detail"] = "original tombstone detail"
    store._merge_records([row])
    store.close()
    QueryIdStore(paths).save({"TweetDetail": "qid"})

    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    for _attempt in range(3):
        result = await resurrection.resurrect_due_tweets(
            budget=1,
            config=config,
            paths=paths,
            auth_bundle=auth_bundle,
            transport=httpx.MockTransport(offline),
            sleep=lambda _delay: SimpleNamespace(__await__=lambda self: iter(())),
        )
        assert result.transient_failures == 1
        store = open_archive_store(paths, create=False)
        assert store is not None
        current = store._get_row("tweet_object:1")
        assert current["enrichment_retry_count"] == 0
        assert current["enrichment_reason"] == "unavailable_unknown"
        assert current["enrichment_detail"] == "original tombstone detail"
        store.update_tweet_object_enrichment(
            "1",
            enrichment_state="terminal_unavailable",
            enrichment_checked_at=current["enrichment_checked_at"],
            enrichment_http_status=current["enrichment_http_status"],
            enrichment_reason="unavailable_unknown",
            enrichment_next_retry_at="2020-01-01T00:00:00+00:00",
            enrichment_retry_count=0,
            enrichment_retry_eligible=True,
        )
        store.close()

    unavailable_payload = make_tweet_detail_response(
        [
            {
                "__typename": "TweetUnavailable",
                "rest_id": "1",
                "reason": "Dieses Posting ist nicht verfügbar.",
            }
        ]
    )

    def still_unavailable(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=unavailable_payload, request=request)

    result = await resurrection.resurrect_due_tweets(
        budget=1,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(still_unavailable),
        sleep=lambda _delay: SimpleNamespace(__await__=lambda self: iter(())),
    )

    assert result.still_unavailable == 1
    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        current = store._get_row("tweet_object:1")
        assert current["enrichment_retry_count"] == 1
        assert current["enrichment_reason"] == "unavailable_unknown"
    finally:
        store.close()
