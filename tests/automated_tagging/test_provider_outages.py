from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace

import httpx
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
    paid_queue_store,
    paid_response,
    provider_failure,
)
from tweetnook.jobs.pipeline import PipelineReporter
from tweetnook.tagging import tagger as tagging
from tweetnook.tagging.accounting import (
    AIUsageLedger,
)


def overload_failure() -> Exception:
    error = RuntimeError("Gemini is currently experiencing high demand")
    error.status_code = 503
    error.response = httpx.Response(
        503, json={"error": {"code": "service_unavailable", "message": str(error)}}
    )
    return error


@pytest.mark.asyncio
async def test_paid_intermittent_overload_keeps_tags_and_reserves_without_daily_stop(
    monkeypatch, paths
):
    store = paid_queue_store(4)
    interactions = FakeInteractions(
        [
            item
            for _ in range(4)
            for item in (overload_failure(), paid_response({"tags": ["One", "Two"]}))
        ]
        + [paid_response({"tags": ["Three", "Four"]})]
    )
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=interactions)
    install_paid_pricing(monkeypatch)
    delays = []

    async def no_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(tagging.asyncio, "sleep", no_sleep)
    settings = config(api_mode="paid", daily_spend_limit_usd=1)
    sink, _ = console()
    reporter = PipelineReporter(sink, "sync", interactive=False)
    with reporter:
        result = await tagging.tag_pending_media_tweets(store, settings, paths, sink)
    assert result == tagging.TaggingRunResult(processed=4, tagged=4, batches=8)
    assert reporter._step_by_key["tagging"].state == "complete"
    assert all(store.media_tag(str(i)) is not None for i in range(1, 5))
    assert len(delays) == 4 and all(12 <= delay <= 18 for delay in delays)
    ledger = AIUsageLedger(paths.ai_usage_dir)
    failures = [row for row in ledger_records(ledger) if row["status"] == "billing_unknown"]
    assert len(failures) == 4
    assert all(row["failure_kind"] == "capacity" for row in failures)
    assert all(row["actual"]["cost_usd"] is None for row in failures)
    before = ledger.daily_budget_usage()
    assert before["unknown"] == before["capacity_unknown"] == 4
    assert before["reserved"] == Decimal("0.08")
    ledger.rebuild()
    assert AIUsageLedger(paths.ai_usage_dir).daily_budget_usage() == before
    # A fresh run also bypasses the daily count cap, without refunding reserves.
    store.add_tweet("5")
    assert (
        await tagging.tag_media_tweets(
            store, settings, paths, console()[0], ["5"], content_type="text"
        )
        == 1
    )
    assert ledger.daily_budget_usage()["reserved"] == Decimal("0.08")


@pytest.mark.asyncio
async def test_paid_five_overloads_pause_with_jitter_and_leave_candidate_pending(
    monkeypatch, paths
):
    store = paid_queue_store(100)
    interactions = FakeInteractions(
        [overload_failure() for _ in range(5)] + [paid_response({"tags": ["One", "Two"]})]
    )
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=interactions)
    install_paid_pricing(monkeypatch)
    factors = iter([0.8, 1.2, 0.8, 1.2])
    monkeypatch.setattr(tagging.random, "uniform", lambda low, high: next(factors))
    delays = []

    async def no_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(tagging.asyncio, "sleep", no_sleep)
    settings = config(api_mode="paid", daily_spend_limit_usd=1)
    sink, _ = console()
    reporter = PipelineReporter(sink, "sync", interactive=False)
    before = store.conn.total_changes
    with reporter:
        result = await tagging.tag_pending_media_tweets(store, settings, paths, sink)
    assert result == tagging.TaggingRunResult(processed=1, tagged=0, batches=5)
    assert len(interactions.calls) == 5
    assert delays == [12, 36, 48, 144]
    assert store.conn.total_changes == before
    assert "five consecutive request failures" in reporter._step_by_key["tagging"].summary
    assert AIUsageLedger(paths.ai_usage_dir).daily_budget_usage()["reserved"] == Decimal("0.10")
    assert await tagging.tag_pending_media_tweets(
        store, settings, paths, console()[0], batch_limit=1
    ) == tagging.TaggingRunResult(processed=1, tagged=1, batches=1)
    assert store.media_tag("1") is not None
    assert store.media_tag("2") is None
    assert len(interactions.calls) == 6


@pytest.mark.asyncio
@pytest.mark.parametrize("limit,policy", [(0.03, "reserve"), (1, "stop")])
async def test_paid_overload_preserves_spending_and_strict_policy_guards(
    monkeypatch, paths, limit, policy
):
    store = Store()
    store.add_tweet("1")
    interactions = FakeInteractions([overload_failure(), paid_response({"tags": ["One", "Two"]})])
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=interactions)
    install_paid_pricing(monkeypatch)

    async def no_sleep(_):
        pass

    monkeypatch.setattr(tagging.asyncio, "sleep", no_sleep)
    state = tagging._PaidRunState()
    assert (
        await tagging.tag_media_tweets(
            store,
            config(api_mode="paid", daily_spend_limit_usd=limit, unknown_billing_policy=policy),
            paths,
            console()[0],
            ["1"],
            content_type="text",
            _paid_state=state,
        )
        == 0
    )
    assert len(interactions.calls) == 1
    assert state.stop_reason
    assert "daily limit on billing-unknown" not in state.stop_reason
    assert AIUsageLedger(paths.ai_usage_dir).daily_budget_usage()["reserved"] == Decimal("0.02")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [provider_failure(503), TimeoutError("timed out")])
async def test_paid_ambiguous_failures_still_stop_after_recovered_overload(
    monkeypatch, paths, failure
):
    store = paid_queue_store(2)
    interactions = FakeInteractions(
        [
            overload_failure(),
            paid_response({"tags": ["One", "Two"]}),
            failure,
            paid_response({"tags": ["Unused", "Response"]}),
        ]
    )
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=interactions)
    install_paid_pricing(monkeypatch)

    async def no_sleep(_):
        pass

    monkeypatch.setattr(tagging.asyncio, "sleep", no_sleep)
    sink, _ = console()
    reporter = PipelineReporter(sink, "sync", interactive=False)
    with reporter:
        result = await tagging.tag_pending_media_tweets(
            store,
            config(api_mode="paid", daily_spend_limit_usd=1, max_unknown_requests_per_day=1),
            paths,
            sink,
        )
    assert result == tagging.TaggingRunResult(processed=2, tagged=1, batches=3)
    assert len(interactions.calls) == 3
    assert "daily limit on billing-unknown" in reporter._step_by_key["tagging"].summary
    budget = AIUsageLedger(paths.ai_usage_dir).daily_budget_usage()
    assert budget["unknown"] == 2 and budget["capacity_unknown"] == 1
    assert budget["reserved"] == Decimal("0.04")
    assert store.media_tag("1") is not None and store.media_tag("2") is None


@pytest.mark.parametrize(
    "evidence", [{"usage": {}}, {"id": "interaction-1"}, {"output": []}, {"steps": []}]
)
def test_paid_overload_with_generation_evidence_remains_ambiguous(evidence):
    body = {"error": {"code": "service_unavailable"}, **evidence}
    assert not tagging._is_capacity_response(503, body, "currently experiencing high demand")


def test_paid_overload_requires_explicit_provider_status():
    body = {"error": {"code": "service_unavailable"}}
    assert tagging._is_capacity_response(503, body, "provider unavailable")
    assert not tagging._is_capacity_response(None, body, "currently experiencing high demand")
    assert not tagging._is_capacity_response(500, body, "currently experiencing high demand")
    assert not tagging._is_capacity_response(503, {}, "generic server failure")


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [429, 503, None, "http_timeout"])
@pytest.mark.parametrize("unlimited", [False, True])
async def test_paid_provider_outage_stops_entire_queue_and_allows_a_new_run(
    monkeypatch, paths, code, unlimited
):
    store = paid_queue_store(100)
    interactions = FakeInteractions(
        [provider_failure(code) for _ in range(5)] + [paid_response({"tags": ["One", "Two"]})]
    )
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=interactions)
    install_paid_pricing(monkeypatch)
    delays = []

    async def no_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(tagging.asyncio, "sleep", no_sleep)
    settings = config(
        api_mode="paid",
        unlimited_spend=unlimited,
        daily_spend_limit_usd=5,
        max_unknown_requests_per_day=100,
    )
    sink, output = console()
    reporter = PipelineReporter(sink, "sync", interactive=False)
    before = store.conn.total_changes
    with reporter:
        result = await tagging.tag_pending_media_tweets(store, settings, paths, sink)

    assert result == tagging.TaggingRunResult(processed=1, tagged=0, batches=5)
    assert len(interactions.calls) == 5
    assert delays == [15, 30, 60, 120]
    assert store.conn.total_changes == before
    step = reporter._step_by_key["tagging"]
    assert step.state == "failed"
    assert "five consecutive request failures" in step.summary
    assert step.metrics == {"tagged": 0, "requests": 5}
    assert "retry later" in output.getvalue()
    records = ledger_records(AIUsageLedger(paths.ai_usage_dir))
    assert [r["attempt"] for r in records] == [1, 2, 3, 4, 5]
    assert len({r["request_id"] for r in records}) == 1
    assert all(r["tweet_ids"] == ["1"] for r in records)
    assert all(r["status"] == ("failed" if code == 429 else "billing_unknown") for r in records)
    assert all(r["actual"]["cost_usd"] == ("0" if code == 429 else None) for r in records)

    # The availability pause is scoped to this run; valid tweets remain eligible.
    assert await tagging.tag_pending_media_tweets(
        store, settings, paths, console()[0], batch_limit=1
    ) == tagging.TaggingRunResult(processed=1, tagged=1, batches=1)
    assert json.loads(store.media_tag("1")[0])["tags"] == ["One", "Two"]
    assert store.media_tag("2") is None
    assert len(interactions.calls) == 6


@pytest.mark.asyncio
async def test_paid_provider_recovery_resets_failure_streak(monkeypatch, paths):
    store = paid_queue_store(2)
    interactions = FakeInteractions(
        [provider_failure(429) for _ in range(4)]
        + [paid_response({"tags": ["One", "Two"]})]
        + [provider_failure(503) for _ in range(4)]
        + [paid_response({"tags": ["Three", "Four"]})]
    )
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=interactions)
    install_paid_pricing(monkeypatch)

    async def no_sleep(_):
        pass

    monkeypatch.setattr(tagging.asyncio, "sleep", no_sleep)
    sink, _ = console()
    reporter = PipelineReporter(sink, "sync", interactive=False)
    with reporter:
        result = await tagging.tag_pending_media_tweets(
            store, config(api_mode="paid", unlimited_spend=True), paths, sink
        )
    assert result == tagging.TaggingRunResult(processed=2, tagged=2, batches=10)
    assert reporter._step_by_key["tagging"].state == "complete"
    assert len(interactions.calls) == 10


@pytest.mark.asyncio
async def test_paid_provider_failures_are_shared_across_candidates(monkeypatch, paths):
    store = paid_queue_store()
    interactions = FakeInteractions(
        [provider_failure(429) for _ in range(3)]
        + [provider_failure(400)]
        + [provider_failure(503) for _ in range(2)]
        + [paid_response({"tags": ["Unused", "Response"]})]
    )
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=interactions)
    install_paid_pricing(monkeypatch)

    async def no_sleep(_):
        pass

    monkeypatch.setattr(tagging.asyncio, "sleep", no_sleep)
    sink, _ = console()
    reporter = PipelineReporter(sink, "sync", interactive=False)
    with reporter:
        result = await tagging.tag_pending_media_tweets(
            store, config(api_mode="paid", unlimited_spend=True), paths, sink
        )
    assert result == tagging.TaggingRunResult(processed=2, tagged=0, batches=6)
    assert len(interactions.calls) == 6
    assert "five consecutive request failures" in reporter._step_by_key["tagging"].summary
    # Only the actual validation rejection defers an item, once.
    deferred = store.conn.execute(
        "SELECT row_key, value FROM archive WHERE row_key LIKE 'tagging_retry:%'"
    ).fetchall()
    assert len(deferred) == 1
    assert deferred[0]["row_key"] == "tagging_retry:1"
    assert json.loads(deferred[0]["value"])["attempts"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("slow_failure", [429, None])
async def test_paid_slow_provider_failure_limits_retry_time(monkeypatch, paths, slow_failure):
    store = paid_queue_store()
    interactions = FakeInteractions(
        [provider_failure(slow_failure), paid_response({"tags": ["Unused", "Response"]})]
    )
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=interactions)
    install_paid_pricing(monkeypatch)
    clock = [0.0]
    monkeypatch.setattr(
        tagging,
        "time",
        SimpleNamespace(monotonic=lambda: clock[0], time=tagging.time.time),
    )
    create = interactions.create

    async def slow_create(**kwargs):
        clock[0] += 900 if slow_failure is None else 890
        return await create(**kwargs)

    monkeypatch.setattr(interactions, "create", slow_create)
    delays = []

    async def simulated_sleep(delay):
        delays.append(delay)
        clock[0] += delay

    monkeypatch.setattr(tagging.asyncio, "sleep", simulated_sleep)
    sink, _ = console()
    reporter = PipelineReporter(sink, "sync", interactive=False)
    with reporter:
        result = await tagging.tag_pending_media_tweets(
            store, config(api_mode="paid", daily_spend_limit_usd=1), paths, sink
        )
    assert result == tagging.TaggingRunResult(processed=1, tagged=0, batches=1)
    assert len(interactions.calls) == 1
    assert delays == ([] if slow_failure is None else [10])
    assert clock[0] == 900
    assert "unavailable for 15 minutes" in reporter._step_by_key["tagging"].summary
    record = ledger_records(AIUsageLedger(paths.ai_usage_dir))[0]
    assert record["status"] == ("billing_unknown" if slow_failure is None else "failed")
    assert record["actual"]["cost_usd"] == (None if slow_failure is None else "0")
    assert record["reserved_cost_usd"] == "0.02"
