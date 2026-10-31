"""A settled request is priced by the tier the provider says served it."""

from __future__ import annotations

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
from tweetnook.tagging import tagger as tagging
from tweetnook.tagging.accounting import AIUsageLedger

# At .000001 input and .000002 output/thinking, 100 input + 20 output + 5 thinking is
# 0.00015 at the Standard rate, and half that at Flex.
@pytest.mark.parametrize(
    "requested,served,multiplier,cost",
    [
        ("standard", "flex", "0.5", "0.000075"),
        ("flex", "standard", "1", "0.00015"),
    ],
)
async def test_the_served_tier_prices_a_successful_request(
    monkeypatch, paths, requested, served, multiplier, cost
):
    store = Store()
    store.add_tweet("1")
    response = paid_response({"tags": ["One", "Two"]})
    response.service_tier = served
    install_fake_client(
        monkeypatch, models=FakeModels([]), interactions=FakeInteractions([response])
    )
    install_paid_pricing(monkeypatch)

    await tagging.tag_media_tweets(
        store,
        config(api_mode="paid", unlimited_spend=True, processing_tier=requested),
        paths,
        console()[0],
        ["1"],
        content_type="text",
    )

    record = ledger_records(AIUsageLedger(paths.ai_usage_dir))[0]
    assert record["service_tier"] == served
    assert record["pricing_multiplier"] == multiplier
    assert Decimal(record["estimated_cost_usd"]) == Decimal(cost)


@pytest.mark.parametrize(
    "requested,served,multiplier,cost",
    [
        ("standard", "flex", "0.5", "0.000075"),
        ("flex", "standard", "1", "0.00015"),
    ],
)
async def test_the_served_tier_prices_a_failure_with_usage(
    monkeypatch, paths, requested, served, multiplier, cost
):
    store = Store()
    store.add_tweet("1")
    error = RuntimeError("generation interrupted")
    error.status_code = 400
    error.response = SimpleNamespace(
        id="interaction-1",
        service_tier=served,
        usage=SimpleNamespace(
            total_input_tokens=100,
            total_cached_tokens=0,
            total_output_tokens=20,
            total_thought_tokens=5,
        ),
    )
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=FakeInteractions([error]))
    install_paid_pricing(monkeypatch)

    await tagging.tag_media_tweets(
        store,
        config(api_mode="paid", unlimited_spend=True, processing_tier=requested),
        paths,
        console()[0],
        ["1"],
        content_type="text",
    )

    record = ledger_records(AIUsageLedger(paths.ai_usage_dir))[0]
    assert record["status"] == "failed"
    assert record["service_tier"] == served
    assert record["pricing_multiplier"] == multiplier
    assert Decimal(record["estimated_cost_usd"]) == Decimal(cost)


async def test_the_requested_tier_prices_a_response_that_names_no_tier(monkeypatch, paths):
    store = Store()
    store.add_tweet("1")
    response = paid_response({"tags": ["One", "Two"]})
    response.service_tier = None
    install_fake_client(
        monkeypatch, models=FakeModels([]), interactions=FakeInteractions([response])
    )
    install_paid_pricing(monkeypatch)

    await tagging.tag_media_tweets(
        store,
        config(api_mode="paid", unlimited_spend=True, processing_tier="flex"),
        paths,
        console()[0],
        ["1"],
        content_type="text",
    )

    record = ledger_records(AIUsageLedger(paths.ai_usage_dir))[0]
    assert record["service_tier"] == "flex"
    assert record["pricing_multiplier"] == "0.5"
    assert Decimal(record["estimated_cost_usd"]) == Decimal("0.000075")
