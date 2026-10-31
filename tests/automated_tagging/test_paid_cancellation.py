"""A cancelled Paid request keeps its uncertainty when it may already have been sent."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest

from tests.automated_tagging.conftest import (
    FakeInteractions,
    FakeModels,
    Store,
    config,
    console,
    install_fake_client,
    install_paid_pricing,
    ledger_records,
    paid_response,
)
from tweetnook.tagging import paid
from tweetnook.tagging import tagger as tagging
from tweetnook.tagging.accounting import AIUsageLedger

STRICT = {"api_mode": "paid", "daily_spend_limit_usd": 0.03, "google_search": True}


class BlockedInteractions:
    """Enters the provider call, then waits until the test cancels it."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        self.entered.set()
        await asyncio.Event().wait()


def one_tweet() -> Store:
    store = Store()
    store.add_tweet("1")
    return store


def start(store, paths, policy=None, **overrides):
    settings = {**STRICT, **overrides}
    return asyncio.create_task(
        tagging.tag_media_tweets(
            store,
            config(**settings),
            paths,
            console()[0],
            ["1"],
            content_type="text",
            _search_policy=policy,
        )
    )


def policy_for(paths) -> paid.SearchPolicy:
    return paid.SearchPolicy(AIUsageLedger(paths.ai_usage_dir), 100, True)


async def test_cancelled_dispatch_records_unknown_billing_and_search(monkeypatch, paths):
    interactions = BlockedInteractions()
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=interactions)
    install_paid_pricing(monkeypatch)
    policy = policy_for(paths)
    task = start(one_tweet(), paths, policy)
    await interactions.entered.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    ledger = AIUsageLedger(paths.ai_usage_dir)
    records = ledger_records(ledger)
    assert len(records) == 1
    assert records[0]["status"] == "billing_unknown"
    assert records[0]["error_type"] == "CancelledError"
    assert records[0]["actual"]["cost_usd"] is None
    assert records[0]["search_reservation_consumed"] == paid.SEARCH_ATTEMPT_SAFETY_RESERVATION
    budget = ledger.daily_budget_usage()
    assert budget["unknown"] == 1
    assert budget["reserved"] == Decimal("0.02")
    assert ledger.monthly_search_count() == paid.SEARCH_ATTEMPT_SAFETY_RESERVATION
    assert policy.in_flight == 0
    assert policy.unknown_attempts == 1


async def test_recorded_uncertainty_stops_the_next_run_under_a_strict_budget(monkeypatch, paths):
    interactions = BlockedInteractions()
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=interactions)
    install_paid_pricing(monkeypatch)
    store = one_tweet()
    settings = {"unknown_billing_policy": "stop"}
    task = start(store, paths, **settings)
    await interactions.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    recovery = FakeInteractions([paid_response({"tags": ["One", "Two"]})])
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=recovery)
    assert await start(store, paths, **settings) == 0
    assert recovery.calls == []


async def test_cancellation_through_the_sync_sdk_fallback_settles_too(monkeypatch, paths):
    release = asyncio.Event()
    entered = asyncio.Event()
    loop = asyncio.get_running_loop()

    def blocking_create(**_kwargs):
        loop.call_soon_threadsafe(entered.set)
        asyncio.run_coroutine_threadsafe(release.wait(), loop).result(5)
        return paid_response({"tags": ["Late", "Result"]})

    client, _ = install_fake_client(
        monkeypatch, models=FakeModels([]), interactions=FakeInteractions([])
    )
    client.aio = SimpleNamespace(aclose=client.aio.aclose)
    client.interactions = SimpleNamespace(create=blocking_create)
    install_paid_pricing(monkeypatch)
    store = one_tweet()
    task = start(store, paths, policy_for(paths))
    await entered.wait()

    task.cancel()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    records = ledger_records(AIUsageLedger(paths.ai_usage_dir))
    assert [record["status"] for record in records] == ["billing_unknown"]
    assert store.media_tag("1") is None


async def test_cancellation_before_dispatch_records_nothing(monkeypatch, paths):
    entered = asyncio.Event()

    async def blocked_tokens(*_args, **_kwargs):
        entered.set()
        await asyncio.Event().wait()

    interactions = FakeInteractions([])
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=interactions)
    install_paid_pricing(monkeypatch)
    monkeypatch.setattr(paid, "count_request_tokens", blocked_tokens)
    policy = policy_for(paths)
    task = start(one_tweet(), paths, policy)
    await entered.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert interactions.calls == []
    assert not list(paths.ai_usage_dir.glob("????-??.jsonl"))
    assert policy.in_flight == 0
    assert policy.unknown_attempts == 0


async def test_cancellation_during_a_retry_wait_does_not_settle_twice(monkeypatch, paths):
    failure = RuntimeError("Provider busy")
    failure.status_code = 503
    interactions = FakeInteractions([failure])
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=interactions)
    install_paid_pricing(monkeypatch)
    waiting = asyncio.Event()

    async def blocked_sleep(_delay):
        waiting.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(tagging.asyncio, "sleep", blocked_sleep)
    task = start(one_tweet(), paths, policy_for(paths), daily_spend_limit_usd=1)
    await waiting.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    records = ledger_records(AIUsageLedger(paths.ai_usage_dir))
    assert len(records) == 1
    assert records[0]["error_type"] == "RuntimeError"


async def test_a_failed_ledger_write_does_not_replace_the_cancellation(monkeypatch, paths):
    interactions = BlockedInteractions()
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=interactions)
    install_paid_pricing(monkeypatch)
    policy = policy_for(paths)
    task = start(one_tweet(), paths, policy)
    await interactions.entered.wait()

    def refuse(self, _record):
        raise OSError("ledger refused")

    monkeypatch.setattr(AIUsageLedger, "append", refuse)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert policy.in_flight == 0
    assert policy.unknown_attempts == 1
