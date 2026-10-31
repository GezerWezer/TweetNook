from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

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
from tweetnook.tagging.accounting import (
    AIUsageLedger,
    UsageRecord,
    failed_usage_record,
    usage_from_interaction,
)


def seed_unknown_billing(paths) -> AIUsageLedger:
    ledger = AIUsageLedger(paths.ai_usage_dir)
    ledger.append(
        failed_usage_record(
            run_id="previous-run",
            request_id="previous-request",
            api_mode="paid",
            model="gemini-3.6-flash",
            service_tier="standard",
            content_type="text",
            tweet_ids=["previous-tweet"],
            latency_ms=10,
            search_enabled=False,
            status="billing_unknown",
            now=datetime.now(UTC),
        )
    )
    return ledger


def seed_unpriced_request(paths) -> AIUsageLedger:
    ledger = AIUsageLedger(paths.ai_usage_dir)
    record, _search = usage_from_interaction(
        SimpleNamespace(
            id="unpriced-interaction",
            usage=SimpleNamespace(
                total_input_tokens=100,
                total_output_tokens=20,
                total_thought_tokens=5,
            ),
            steps=[],
        ),
        run_id="previous-run",
        request_id="unpriced-request",
        requested_model="gemini-3.6-flash",
        service_tier="standard",
        content_type="text",
        tweet_ids=["previous-tweet"],
        search_enabled=False,
        latency_ms=10,
        pricing=None,
        now=datetime.now(UTC),
    )
    ledger.append(record)
    return ledger


@pytest.mark.asyncio
async def test_billing_unknown_blocks_a_new_finite_budget_run(monkeypatch, paths) -> None:
    seed_unknown_billing(paths)
    store = Store()
    store.add_tweet("1")
    interactions = FakeInteractions([paid_response({"tags": ["One", "Two"]})])
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=interactions)

    tagged = await tagging.tag_media_tweets(
        store,
        config(api_mode="paid", daily_spend_limit_usd=Decimal("5")),
        paths,
        console()[0],
        ["1"],
        content_type="text",
        dry_run=True,
    )

    assert tagged == 0
    assert interactions.calls == []


@pytest.mark.asyncio
async def test_billing_unknown_does_not_block_unlimited_paid_work(monkeypatch, paths) -> None:
    seed_unknown_billing(paths)
    store = Store()
    store.add_tweet("1")
    interactions = FakeInteractions([paid_response({"tags": ["One", "Two"]})])
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=interactions)
    monkeypatch.setattr(paid, "load_openrouter_pricing", lambda *_args: None)

    tagged = await tagging.tag_media_tweets(
        store,
        config(api_mode="paid", unlimited_spend=True),
        paths,
        console()[0],
        ["1"],
        content_type="text",
        dry_run=True,
    )

    assert tagged == 1
    assert len(interactions.calls) == 1


@pytest.mark.asyncio
async def test_unpriced_request_blocks_a_new_finite_budget_run(monkeypatch, paths) -> None:
    ledger = seed_unpriced_request(paths)
    assert ledger.daily_unpriced_requests() == 1
    store = Store()
    store.add_tweet("1")
    interactions = FakeInteractions([paid_response({"tags": ["One", "Two"]})])
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=interactions)

    tagged = await tagging.tag_media_tweets(
        store,
        config(api_mode="paid", daily_spend_limit_usd=Decimal("5")),
        paths,
        console()[0],
        ["1"],
        content_type="text",
        dry_run=True,
    )

    assert tagged == 0
    assert interactions.calls == []


@pytest.mark.asyncio
async def test_unpriced_request_does_not_block_unlimited_paid_work(monkeypatch, paths) -> None:
    seed_unpriced_request(paths)
    store = Store()
    store.add_tweet("1")
    interactions = FakeInteractions([paid_response({"tags": ["One", "Two"]})])
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=interactions)
    monkeypatch.setattr(paid, "load_openrouter_pricing", lambda *_args: None)

    tagged = await tagging.tag_media_tweets(
        store,
        config(api_mode="paid", unlimited_spend=True),
        paths,
        console()[0],
        ["1"],
        content_type="text",
        dry_run=True,
    )

    assert tagged == 1
    assert len(interactions.calls) == 1


@pytest.mark.asyncio
async def test_paid_token_preflight_failure_is_nonfatal(monkeypatch, paths) -> None:
    store = Store()
    store.add_tweet("1")
    interactions = FakeInteractions([paid_response({"tags": ["One", "Two"]})])
    models = FakeModels([])
    install_fake_client(monkeypatch, models=models, interactions=interactions)
    monkeypatch.setattr(paid, "load_openrouter_pricing", lambda *_args: None)

    def fail_count(**_kwargs: Any) -> Any:
        raise RuntimeError("counting unavailable")

    monkeypatch.setattr(models, "count_tokens", fail_count)
    tagged = await tagging.tag_media_tweets(
        store,
        config(api_mode="paid", unlimited_spend=True),
        paths,
        console()[0],
        ["1"],
        content_type="text",
        dry_run=True,
    )

    assert tagged == 1
    assert len(interactions.calls) == 1


@pytest.mark.asyncio
async def test_unknown_search_accounting_disables_search_for_later_requests(
    monkeypatch, paths
) -> None:
    install_paid_pricing(monkeypatch)
    store = Store()
    store.add_tweet("1")
    store.add_tweet("2")
    interactions = FakeInteractions(
        [
            paid_response({"tags": ["One", "Two"]}, searches=None),
            paid_response({"tags": ["Three", "Four"]}, searches=0),
        ]
    )
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=interactions)
    policy = paid.SearchPolicy(AIUsageLedger(paths.ai_usage_dir), reserve=100, enabled=True)

    for tweet_id in ("1", "2"):
        await tagging.tag_media_tweets(
            store,
            config(api_mode="paid", unlimited_spend=True, google_search=True),
            paths,
            console()[0],
            [tweet_id],
            content_type="text",
            dry_run=True,
            _search_policy=policy,
        )

    assert interactions.calls[0]["tools"] == [{"type": "google_search"}]
    assert "tools" not in interactions.calls[1]
    assert "Search and Verification" not in interactions.calls[1]["system_instruction"]


@pytest.mark.asyncio
async def test_retry_with_known_search_usage_keeps_first_attempt_accounted(
    monkeypatch, paths
) -> None:
    install_paid_pricing(monkeypatch)
    store = Store()
    store.add_tweet("1")
    failure = RuntimeError("503 after provider response")
    failure.response = paid_response(  # type: ignore[attr-defined]
        {"tags": ["Ignored", "Failure"]}, searches=2, interaction_id="failed-interaction"
    )
    interactions = FakeInteractions([failure, paid_response({"tags": ["One", "Two"]}, searches=0)])
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=interactions)
    original_sleep = asyncio.sleep
    monkeypatch.setattr(tagging.asyncio, "sleep", lambda _delay: original_sleep(0))
    ledger = AIUsageLedger(paths.ai_usage_dir)

    tagged = await tagging.tag_media_tweets(
        store,
        config(api_mode="paid", unlimited_spend=True, google_search=True),
        paths,
        console()[0],
        ["1"],
        content_type="text",
        dry_run=True,
        _ledger=ledger,
    )

    assert tagged == 1
    assert len(interactions.calls) == 2
    records = ledger_records(ledger)
    assert records[0]["status"] == "failed"
    assert records[0]["request_id"] == records[1]["request_id"]
    assert [record["attempt"] for record in records] == [1, 2]
    assert records[0]["google_search_count"] == 2
    assert records[0]["google_search_accounting_source"] == "usage"
    assert ledger.monthly_search_count() == 2


@pytest.mark.asyncio
async def test_retry_with_empty_usage_marks_billing_unknown(monkeypatch, paths) -> None:
    store = Store()
    store.add_tweet("1")
    failure = RuntimeError("503 empty usage")
    failure.response = SimpleNamespace(  # type: ignore[attr-defined]
        id="empty-usage-interaction",
        output_text=json.dumps({"tags": ["Ignored", "Failure"]}),
        model="gemini-3.6-flash-001",
        service_tier="standard",
        usage=SimpleNamespace(),
        steps=[],
    )
    interactions = FakeInteractions([failure, paid_response({"tags": ["One", "Two"]})])
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=interactions)
    monkeypatch.setattr(paid, "load_openrouter_pricing", lambda *_args: None)
    original_sleep = asyncio.sleep
    monkeypatch.setattr(tagging.asyncio, "sleep", lambda _delay: original_sleep(0))
    ledger = AIUsageLedger(paths.ai_usage_dir)

    tagged = await tagging.tag_media_tweets(
        store,
        config(api_mode="paid", unlimited_spend=True),
        paths,
        console()[0],
        ["1"],
        content_type="text",
        dry_run=True,
        _ledger=ledger,
    )

    assert tagged == 1
    assert len(interactions.calls) == 2
    records = ledger_records(ledger)
    assert records[0]["status"] == "billing_unknown"
    assert records[0]["actual"]["cost_usd"] is None
    assert records[0]["actual"]["input_tokens"] is None
    assert records[1]["status"] == "success"


@pytest.mark.asyncio
async def test_retry_with_unknown_search_usage_disables_search_and_consumes_reservation(
    monkeypatch, paths
) -> None:
    install_paid_pricing(monkeypatch)
    store = Store()
    store.add_tweet("1")
    interactions = FakeInteractions(
        [RuntimeError("503 timeout"), paid_response({"tags": ["One", "Two"]}, searches=0)]
    )
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=interactions)
    original_sleep = asyncio.sleep
    monkeypatch.setattr(tagging.asyncio, "sleep", lambda _delay: original_sleep(0))
    ledger = AIUsageLedger(paths.ai_usage_dir)

    tagged = await tagging.tag_media_tweets(
        store,
        config(
            api_mode="paid",
            unlimited_spend=True,
            google_search=True,
            search_unknown_policy="disable",
        ),
        paths,
        console()[0],
        ["1"],
        content_type="text",
        dry_run=True,
        _ledger=ledger,
    )

    assert tagged == 1
    assert interactions.calls[0]["tools"] == [{"type": "google_search"}]
    assert "tools" not in interactions.calls[1]
    records = ledger_records(ledger)
    assert records[0]["google_search_count"] is None
    assert records[0]["google_search_accounting_source"] == "unknown"
    assert records[0]["search_reservation_consumed"] == paid.SEARCH_ATTEMPT_SAFETY_RESERVATION
    assert ledger.monthly_search_count() == paid.SEARCH_ATTEMPT_SAFETY_RESERVATION


@pytest.mark.asyncio
async def test_monthly_search_reserve_prevents_intentional_allowance_crossing(
    monkeypatch, paths
) -> None:
    install_paid_pricing(monkeypatch)
    ledger = AIUsageLedger(paths.ai_usage_dir)
    now = datetime.now(UTC)
    ledger.append(
        UsageRecord(
            timestamp=now.isoformat(),
            run_id="seed",
            request_id="seed",
            interaction_id="seed",
            api_mode="paid",
            model="gemini-3.6-flash",
            model_version="gemini-3.6-flash",
            service_tier="standard",
            content_type="text",
            tweet_ids=[],
            prompt_tokens=0,
            cached_tokens=0,
            output_tokens=0,
            thinking_tokens=0,
            google_search_count=4_900,
            google_search_accounting_source="usage",
            cost_usd="0",
            pricing_version="seed",
            status="success",
            latency_ms=0,
        )
    )
    store = Store()
    store.add_tweet("1")
    interactions = FakeInteractions([paid_response({"tags": ["One", "Two"]}, searches=0)])
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=interactions)

    await tagging.tag_media_tweets(
        store,
        config(api_mode="paid", unlimited_spend=True, search_safety_reserve=100),
        paths,
        console()[0],
        ["1"],
        content_type="text",
        dry_run=True,
        _ledger=ledger,
    )

    assert "tools" not in interactions.calls[0]
