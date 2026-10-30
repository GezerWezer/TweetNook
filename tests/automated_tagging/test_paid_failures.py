from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from tests.automated_tagging.conftest import (
    FakeInteractions,
    FakeModels,
    QueueStore,
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
    failed_usage_record,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [400, 429, 503, None])
async def test_paid_finite_failure_classification_reserves_and_logs(monkeypatch, paths, code):
    store = Store()
    store.add_tweet("1")
    failure = RuntimeError("UNAVAILABLE secret [bold]provider message[/bold]")
    failure.status_code = code
    interactions = FakeInteractions([failure, paid_response({"tags": ["One", "Two"]})])
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=interactions)
    install_paid_pricing(monkeypatch)
    original_sleep = asyncio.sleep
    monkeypatch.setattr(tagging.asyncio, "sleep", lambda _: original_sleep(0))
    sink, output = console()
    state = tagging._PaidRunState()
    result = await tagging.tag_media_tweets(
        store,
        config(api_mode="paid", daily_spend_limit_usd=1),
        paths,
        sink,
        ["1"],
        content_type="text",
        _paid_state=state,
    )
    records = ledger_records(AIUsageLedger(paths.ai_usage_dir))
    first = records[0]
    assert first["error_code"] == code
    assert first["error_type"] == "RuntimeError"
    assert first["failure_phase"] == "interaction"
    assert "secret" not in first["error_message"]
    assert "secret" not in output.getvalue()
    assert "[bold]provider message[/bold]" in output.getvalue()
    assert first["status"] == ("failed" if code in {400, 429} else "billing_unknown")
    assert first["actual"]["cost_usd"] == ("0" if code in {400, 429} else None)
    assert Decimal(first["reserved_cost_usd"]) == Decimal("0.02")
    # Validation rejection is not retried, even if its text says UNAVAILABLE.
    if code == 400:
        assert result == 0
        assert state.attempts == 1
    else:
        assert result == 1
        assert state.attempts == 2
        assert records[0]["request_id"] == records[1]["request_id"]
        assert [r["attempt"] for r in records] == [1, 2]
        assert (
            store.conn.execute("SELECT 1 FROM archive WHERE row_key='tagging_retry:1'").fetchone()
            is None
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "limit,maximum,policy", [(0.03, 3, "reserve"), (1, 1, "reserve"), (1, 3, "stop")]
)
async def test_paid_uncertainty_stops_at_budget_or_policy_without_another_dispatch(
    monkeypatch, paths, limit, maximum, policy
):
    store = Store()
    store.add_tweet("1")
    interactions = FakeInteractions(
        [TimeoutError("timed out"), paid_response({"tags": ["One", "Two"]})]
    )
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=interactions)
    install_paid_pricing(monkeypatch)
    original_sleep = asyncio.sleep
    monkeypatch.setattr(tagging.asyncio, "sleep", lambda _: original_sleep(0))
    state = tagging._PaidRunState()
    result = await tagging.tag_media_tweets(
        store,
        config(
            api_mode="paid",
            daily_spend_limit_usd=limit,
            max_unknown_requests_per_day=maximum,
            unknown_billing_policy=policy,
        ),
        paths,
        console()[0],
        ["1"],
        content_type="text",
        _paid_state=state,
    )
    assert result == 0
    assert state.stop_reason
    assert state.attempts == len(interactions.calls) == 1
    budget = AIUsageLedger(paths.ai_usage_dir).daily_budget_usage()
    assert budget["spend"] == 0
    assert budget["reserved"] == Decimal("0.02")
    assert budget["unknown"] == 1
    assert budget["unreserved"] == 0
    assert (
        store.conn.execute("SELECT value FROM archive WHERE row_key='tagging_retry:1'").fetchone()
        is None
    )


@pytest.mark.asyncio
async def test_paid_reserved_unknown_survives_a_new_run_and_summary_rebuild(monkeypatch, paths):
    ledger = AIUsageLedger(paths.ai_usage_dir)
    from dataclasses import replace

    ledger.append(
        replace(
            failed_usage_record(
                run_id="old",
                request_id="old",
                api_mode="paid",
                model="gemini-3.6-flash",
                service_tier="flex",
                content_type="text",
                tweet_ids=["old"],
                latency_ms=1,
                search_enabled=False,
                status="billing_unknown",
            ),
            reserved_cost_usd="0.02",
        )
    )
    old_summary = json.loads(ledger.summary_path.read_text())
    old_summary["version"] = 2
    ledger.summary_path.write_text(json.dumps(old_summary))
    store = Store()
    store.add_tweet("1")
    interactions = FakeInteractions([paid_response({"tags": ["One", "Two"]})])
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=interactions)
    install_paid_pricing(monkeypatch)
    assert (
        await tagging.tag_media_tweets(
            store,
            config(api_mode="paid", daily_spend_limit_usd=0.1),
            paths,
            console()[0],
            ["1"],
            content_type="text",
            dry_run=True,
        )
        == 1
    )
    assert ledger.daily_budget_usage()["reserved"] == Decimal("0.02")
    assert ledger.statistics()["today_unknown_cost_requests"] == 1
    assert ledger.statistics()["today_unreserved_unknown_cost_requests"] == 0


@pytest.mark.asyncio
async def test_paid_unknown_billing_keeps_useful_output_and_dry_run_has_no_archive_writes(
    monkeypatch, paths
):
    store = Store()
    store.add_tweet("1")
    response = paid_response({"tags": ["One", "Two"]})
    response.usage = None
    interactions = FakeInteractions([response])
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=interactions)
    install_paid_pricing(monkeypatch)
    before = store.conn.total_changes
    assert (
        await tagging.tag_media_tweets(
            store,
            config(api_mode="paid", daily_spend_limit_usd=1, unknown_billing_policy="stop"),
            paths,
            console()[0],
            ["1"],
            content_type="text",
            dry_run=True,
        )
        == 1
    )
    assert store.conn.total_changes == before
    assert ledger_records(AIUsageLedger(paths.ai_usage_dir))[0]["actual"]["cost_usd"] is None


@pytest.mark.asyncio
async def test_search_tolerates_only_configured_number_of_unknown_attempts(monkeypatch, paths):
    store = Store()
    store.add_tweet("1")
    interactions = FakeInteractions(
        [paid_response({"tags": ["One", "Two"]}, searches=None) for _ in range(4)]
    )
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=interactions)
    ledger = AIUsageLedger(paths.ai_usage_dir)
    policy = tagging._SearchPolicy(
        ledger, 100, True, unknown_policy="reserve", max_unknown_attempts=2
    )
    for _ in range(4):
        assert (
            await tagging.tag_media_tweets(
                store,
                config(api_mode="paid", unlimited_spend=True, google_search=True),
                paths,
                console()[0],
                ["1"],
                content_type="text",
                dry_run=True,
                _search_policy=policy,
            )
            == 1
        )
    assert ["tools" in c for c in interactions.calls] == [True, True, False, False]
    assert policy.in_flight == 0
    assert ledger.monthly_search_count() == 10
    assert [r["google_search_count"] for r in ledger_records(ledger)] == [None, None, 0, 0]


@pytest.mark.asyncio
async def test_paid_queue_pause_records_dispatched_attempt_in_pipeline(monkeypatch, paths):
    store = Store()
    store.add_tweet("1")
    store.get_eligible_tagging_candidates = lambda **_: [
        {"tweet_id": "1", "content_type": "text", "quoted_tweet_id": None, "sort_ts": 0}
    ]
    interactions = FakeInteractions([TimeoutError("timed out")])
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=interactions)
    install_paid_pricing(monkeypatch)
    sink, output = console()
    reporter = PipelineReporter(sink, "sync", interactive=False)
    with reporter:
        result = await tagging.tag_pending_media_tweets(
            store,
            config(api_mode="paid", daily_spend_limit_usd=1, unknown_billing_policy="stop"),
            paths,
            sink,
        )
    step = reporter._step_by_key["tagging"]
    assert result == tagging.TaggingRunResult(processed=1, tagged=0, batches=1)
    assert step.state == "failed"
    assert step.summary.startswith("Paused:")
    assert step.paused is True
    assert step.reason and not step.reason.startswith("Paused")
    assert step.metrics == {"tagged": 0, "requests": 1}
    assert "skipped due to no eligible" not in output.getvalue()


@pytest.mark.asyncio
async def test_paid_sdk_retries_are_disabled_without_sending_an_extra_body_field(
    monkeypatch, paths
):
    interactions = FakeInteractions([paid_response({"tags": ["One", "Two"]})])
    interactions.sdk_configuration = SimpleNamespace(retry_config="SDK default")
    client, _ = install_fake_client(monkeypatch, models=FakeModels([]), interactions=interactions)
    await tagging._create_interaction(client, model="gemini-3.6-flash", input="test")
    assert interactions.sdk_configuration.retry_config is None
    assert "retries" not in interactions.calls[0]


@pytest.mark.asyncio
async def test_token_preflight_failure_is_visible_and_secret_is_masked(monkeypatch):
    def broken_counter(**_):
        raise RuntimeError("count failed secret")

    sink, output = console()
    assert await tagging._count_request_tokens(
        SimpleNamespace(models=SimpleNamespace(count_tokens=broken_counter)),
        model="gemini-3.6-flash",
        system_prompt="prompt",
        interaction_input=[],
        console=sink,
        api_key="secret",
    ) == (None, False)
    assert "Token preflight unavailable" in output.getvalue()
    assert "secret" not in output.getvalue()


@pytest.mark.asyncio
async def test_paid_queue_continues_after_a_rejected_tweet(monkeypatch, paths):
    store = Store()
    store.add_tweet("1")
    store.add_tweet("2")
    queue = QueueStore(
        [
            {"tweet_id": str(i), "content_type": "text", "quoted_tweet_id": None, "sort_ts": 0}
            for i in [1, 2]
        ]
    )
    store.get_eligible_tagging_candidates = queue.get_eligible_tagging_candidates
    error = RuntimeError("Invalid media")
    error.status_code = 400
    interactions = FakeInteractions([error, paid_response({"tags": ["One", "Two"]})])
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=interactions)
    install_paid_pricing(monkeypatch)
    assert await tagging.tag_pending_media_tweets(
        store,
        config(api_mode="paid", daily_spend_limit_usd=1),
        paths,
        console()[0],
    ) == tagging.TaggingRunResult(processed=2, tagged=1, batches=2)
    assert store.media_tag("1") is None
    assert json.loads(store.media_tag("2")[0])["tags"] == ["One", "Two"]
    assert store.conn.execute("SELECT 1 FROM archive WHERE row_key='tagging_retry:1'").fetchone()


@pytest.mark.asyncio
async def test_paid_ledger_write_failure_stops_queue_without_a_second_request(monkeypatch, paths):
    store = Store()
    store.add_tweet("1")
    interactions = FakeInteractions([paid_response({"tags": ["One", "Two"]})])
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=interactions)
    install_paid_pricing(monkeypatch)
    ledger = AIUsageLedger(paths.ai_usage_dir)

    def cannot_append(_record):
        raise OSError("disk write failed")

    monkeypatch.setattr(ledger, "append", cannot_append)
    state = tagging._PaidRunState()
    assert (
        await tagging.tag_media_tweets(
            store,
            config(api_mode="paid", daily_spend_limit_usd=1),
            paths,
            console()[0],
            ["1"],
            content_type="text",
            _ledger=ledger,
            _paid_state=state,
        )
        == 0
    )
    assert state.stop_reason == "the usage ledger could not be saved"
    assert len(interactions.calls) == 1
    assert store.media_tag("1") is None


@pytest.mark.asyncio
async def test_paid_preflight_increases_reserve_for_expensive_input(monkeypatch, paths):
    store = Store()
    store.add_tweet("1")
    models = FakeModels([])
    models.count_tokens = lambda **_: SimpleNamespace(total_tokens=200_000)
    interactions = FakeInteractions([paid_response({"tags": ["One", "Two"]})])
    install_fake_client(monkeypatch, models=models, interactions=interactions)
    install_paid_pricing(monkeypatch)
    state = tagging._PaidRunState()
    assert (
        await tagging.tag_media_tweets(
            store,
            config(api_mode="paid", daily_spend_limit_usd=0.05),
            paths,
            console()[0],
            ["1"],
            content_type="text",
            _paid_state=state,
        )
        == 0
    )
    assert state.stop_reason
    assert state.attempts == 0
    assert interactions.calls == []


@pytest.mark.asyncio
async def test_provider_error_with_partial_usage_is_never_assumed_unbilled(monkeypatch, paths):
    store = Store()
    store.add_tweet("1")
    error = RuntimeError("400 interrupted generation")
    error.response = SimpleNamespace(
        status_code=400,
        json=lambda: {"usage": {"total_input_tokens": 100}},
    )
    interactions = FakeInteractions([error])
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=interactions)
    install_paid_pricing(monkeypatch)
    assert (
        await tagging.tag_media_tweets(
            store,
            config(api_mode="paid", daily_spend_limit_usd=1),
            paths,
            console()[0],
            ["1"],
            content_type="text",
        )
        == 0
    )
    record = ledger_records(AIUsageLedger(paths.ai_usage_dir))[0]
    assert record["status"] == "billing_unknown"
    assert record["actual"]["cost_usd"] is None
    assert record["reserved_cost_usd"] == "0.02"


@pytest.mark.asyncio
async def test_paid_provider_retry_window_is_checked_after_preflight(monkeypatch, paths):
    store = paid_queue_store()
    interactions = FakeInteractions(
        [provider_failure(429), paid_response({"tags": ["Unused", "Response"]})]
    )
    models = FakeModels([])
    install_fake_client(monkeypatch, models=models, interactions=interactions)
    install_paid_pricing(monkeypatch)
    clock = [0.0]
    monkeypatch.setattr(
        tagging,
        "time",
        SimpleNamespace(monotonic=lambda: clock[0], time=tagging.time.time),
    )
    count = models.count_tokens

    def slow_count(**kwargs):
        if models.count_calls:
            clock[0] += 885
        return count(**kwargs)

    monkeypatch.setattr(models, "count_tokens", slow_count)

    async def simulated_sleep(delay):
        clock[0] += delay

    monkeypatch.setattr(tagging.asyncio, "sleep", simulated_sleep)
    sink, _ = console()
    reporter = PipelineReporter(sink, "sync", interactive=False)
    with reporter:
        result = await tagging.tag_pending_media_tweets(
            store, config(api_mode="paid", unlimited_spend=True), paths, sink
        )
    assert result == tagging.TaggingRunResult(processed=1, tagged=0, batches=1)
    assert len(interactions.calls) == 1
    assert len(models.count_calls) == 2
    assert clock[0] == 900
    assert "unavailable for 15 minutes" in reporter._step_by_key["tagging"].summary
    assert len(ledger_records(AIUsageLedger(paths.ai_usage_dir))) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("dry_run", [False, True])
async def test_paid_unusable_normalized_tags_defer_only_outside_dry_run(
    monkeypatch, paths, dry_run
):
    store = Store()
    store.add_tweet("1")
    interactions = FakeInteractions([paid_response({"tags": ["One", "One"]})])
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=interactions)
    install_paid_pricing(monkeypatch)
    before = store.conn.total_changes
    assert (
        await tagging.tag_media_tweets(
            store,
            config(api_mode="paid", daily_spend_limit_usd=1),
            paths,
            console()[0],
            ["1"],
            content_type="text",
            dry_run=dry_run,
        )
        == 0
    )
    deferred = store.conn.execute(
        "SELECT 1 FROM archive WHERE row_key='tagging_retry:1'"
    ).fetchone()
    assert bool(deferred) is not dry_run
    assert store.media_tag("1") is None
    if dry_run:
        assert store.conn.total_changes == before
