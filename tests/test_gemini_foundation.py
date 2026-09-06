from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tweetnook.automated_tagging import model_from_api, model_is_compatible
from tweetnook.config import TaggingConfig
from tweetnook.gemini_accounting import (
    AIUsageLedger,
    UsageRecord,
    failed_usage_record,
    interaction_search_accounting,
    search_step_queries,
    usage_from_interaction,
)
from tweetnook.gemini_pricing import (
    OpenRouterPricing,
    calculate_estimated_cost_usd,
    load_openrouter_pricing,
    pricing_cache_path,
)


def usage_record(
    request_id: str,
    *,
    timestamp: str = "2026-08-15T12:00:00+00:00",
    model: str = "gemini-3.6-flash",
    cost: str | None = "0.01",
    searches: int = 0,
    status: str = "success",
) -> UsageRecord:
    return UsageRecord(
        timestamp=timestamp,
        run_id="run-1",
        request_id=request_id,
        interaction_id=f"interaction-{request_id}",
        api_mode="paid",
        model=model,
        model_version=f"{model}-001",
        service_tier="flex",
        content_type="text",
        tweet_ids=[request_id],
        prompt_tokens=100,
        cached_tokens=0,
        output_tokens=20,
        thinking_tokens=10,
        google_search_count=searches,
        google_search_accounting_source="usage",
        cost_usd=cost,
        pricing_version="test-price",
        status=status,
        latency_ms=25,
        pricing_source="test",
        pricing_model="google/gemini-3.6-flash",
        pricing_fetched_at=timestamp,
        pricing={
            "input_per_token": "0.000001",
            "output_per_token": "0.000002",
            "thinking_per_token": "0.000002",
            "cached_input_per_token": "0.0000001",
        },
        estimated_cost_usd=cost,
    )


def test_paid_config_requires_daily_limit_or_explicit_unlimited() -> None:
    with pytest.raises(ValidationError, match="daily spending limit or Unlimited"):
        TaggingConfig(enabled=True, api_mode="paid")

    assert TaggingConfig(enabled=True, api_mode="paid", unlimited_spend=True).unlimited_spend
    assert (
        TaggingConfig(
            enabled=True,
            api_mode="paid",
            daily_spend_limit_usd=Decimal("2.50"),
        ).daily_spend_limit_usd
        == 2.5
    )


def test_blank_additional_instructions_are_normalized_to_none() -> None:
    assert TaggingConfig(additional_instructions="  \n ").additional_instructions is None
    assert (
        TaggingConfig(
            additional_instructions=" Prefer exact camera models. "
        ).additional_instructions
        == "Prefer exact camera models."
    )


def test_search_defaults_on_but_free_execution_disables_it() -> None:
    assert TaggingConfig().google_search is True


def test_live_model_metadata_drives_model_discovery() -> None:
    model = model_from_api(
        SimpleNamespace(
            name="models/gemini-3.6-flash",
            base_model_id="gemini-3.6-flash",
            display_name="Gemini 3.6 Flash",
            version="2026-08",
            input_token_limit=1_000_000,
            output_token_limit=64_000,
            supported_actions=["generateContent", "countTokens"],
            thinking=True,
        )
    )
    assert model is not None
    assert model.input_token_limit == 1_000_000
    assert model.thinking is True
    assert model_is_compatible(model, api_mode="paid", processing_tier="flex")
    assert model_is_compatible(model, api_mode="free", processing_tier="standard")


@pytest.mark.parametrize(
    "model_id",
    ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.7-flash", "gemini-future-flash"],
)
def test_supported_flash_variants_are_discoverable(model_id: str) -> None:
    model = model_from_api(
        SimpleNamespace(
            name=f"models/{model_id}",
            base_model_id=model_id,
            display_name=model_id,
            version="2026-08",
            input_token_limit=1_000_000,
            output_token_limit=64_000,
            supported_actions=["generateContent", "countTokens"],
            thinking=True,
        )
    )
    assert model is not None
    assert model_is_compatible(model, api_mode="paid", processing_tier="flex")


@pytest.mark.parametrize(
    ("model_id", "display_name"),
    [
        ("gemini-3.5-flash-image", "Gemini 3.5 Flash Image"),
        ("gemini-3.1-flash-lite-image", "Gemini 3.1 Flash Lite Image"),
        ("gemini-2.5-flash-image", "Nano Banana"),
    ],
)
def test_image_generation_variants_are_not_discoverable(model_id: str, display_name: str) -> None:
    assert (
        model_from_api(
            SimpleNamespace(
                name=f"models/{model_id}",
                base_model_id=model_id,
                display_name=display_name,
                supported_actions=["generateContent"],
                thinking=True,
            )
        )
        is None
    )


def test_live_gemini_model_without_thinking_remains_available_for_user_validation() -> None:
    model = model_from_api(
        SimpleNamespace(
            name="models/gemini-3.6-flash",
            base_model_id="gemini-3.6-flash",
            display_name="Gemini 3.6 Flash",
            supported_actions=["generateContent"],
            thinking=False,
        )
    )
    assert model is not None
    assert model_is_compatible(model, api_mode="paid", processing_tier="standard")


def test_interaction_search_prefers_usage_count_and_records_step_queries() -> None:
    interaction = SimpleNamespace(
        usage=SimpleNamespace(
            grounding_tool_count=[SimpleNamespace(type="google_search", count=3)]
        ),
        steps=[
            SimpleNamespace(type="google_search_call", arguments={"query": "Gemini 3.6"}),
            SimpleNamespace(type="google_search_call", arguments={"query": "new releases"}),
        ],
    )
    accounting = interaction_search_accounting(interaction, search_enabled=True)
    assert accounting.count == 3
    assert accounting.source == "usage"
    assert accounting.queries == ("Gemini 3.6", "new releases")


def test_interaction_search_falls_back_to_explicit_nonempty_steps() -> None:
    interaction = SimpleNamespace(
        usage=SimpleNamespace(grounding_tool_count=None),
        steps=[
            {"type": "google_search_call", "arguments": {"query": "Deadlock Shiv"}},
            {"type": "google_search_call", "arguments": {"query": "  "}},
        ],
    )
    accounting = interaction_search_accounting(interaction, search_enabled=True)
    assert accounting.count == 1
    assert accounting.source == "interaction_steps"


def test_interaction_search_marks_unknown_accounting_unavailable() -> None:
    accounting = interaction_search_accounting(
        SimpleNamespace(usage=SimpleNamespace(grounding_tool_count=None), steps=[]),
        search_enabled=True,
    )
    assert accounting.count is None
    assert accounting.source == "unknown"
    assert accounting.reliable is False


def test_search_step_queries_supports_plural_queries_and_preserve_order() -> None:
    interaction = {
        "steps": [
            {
                "type": "google_search_call",
                "arguments": {"queries": ["query one", "", " query two "]},
            },
            {
                "type": "google_search_call",
                "arguments": {"queries": ["query three", "query three"]},
            },
            {"type": "google_search_call", "arguments": {"query": "legacy query"}},
        ]
    }
    assert search_step_queries(interaction) == (
        "query one",
        "query two",
        "query three",
        "query three",
        "legacy query",
    )


def test_actual_interaction_cost_is_calculated_and_stored_at_request_time() -> None:
    now = datetime(2026, 8, 15, tzinfo=UTC)
    interaction = SimpleNamespace(
        id="interaction-1",
        model="gemini-3.6-flash-001",
        service_tier="flex",
        usage=SimpleNamespace(
            total_input_tokens=1_000,
            total_cached_tokens=200,
            total_output_tokens=100,
            total_thought_tokens=50,
            grounding_tool_count=[],
        ),
        steps=[],
    )
    pricing = OpenRouterPricing(
        pricing_model="google/gemini-3.6-flash",
        fetched_at=now.isoformat(),
        stale=False,
        input_per_token=Decimal("0.000001"),
        output_per_token=Decimal("0.000002"),
        thinking_per_token=Decimal("0.000003"),
        cached_input_per_token=Decimal("0.0000001"),
    )
    record, _search = usage_from_interaction(
        interaction,
        run_id="run-1",
        request_id="request-1",
        requested_model="gemini-3.6-flash",
        service_tier="flex",
        content_type="media",
        tweet_ids=["42"],
        search_enabled=True,
        latency_ms=123,
        pricing=pricing,
        pricing_multiplier=Decimal("0.5"),
        now=now,
    )
    expected = calculate_estimated_cost_usd(
        pricing,
        input_tokens=1_000,
        cached_tokens=200,
        output_tokens=100,
        thinking_tokens=50,
        multiplier=Decimal("0.5"),
    )
    assert Decimal(record.estimated_cost_usd or "0") == expected
    assert record.pricing_model == "google/gemini-3.6-flash"
    assert record.pricing_multiplier == "0.5"
    assert record.interaction_id == "interaction-1"


def test_known_interaction_usage_without_pricing_is_not_billing_unknown(tmp_path) -> None:
    now = datetime(2026, 8, 15, tzinfo=UTC)
    record, _search = usage_from_interaction(
        SimpleNamespace(
            id="interaction-known-no-price",
            usage=SimpleNamespace(
                total_input_tokens=0,
                total_output_tokens=0,
                total_thought_tokens=0,
            ),
            steps=[],
        ),
        run_id="run-1",
        request_id="request-known-no-price",
        requested_model="gemini-3.6-flash",
        service_tier="standard",
        content_type="text",
        tweet_ids=["42"],
        search_enabled=False,
        latency_ms=10,
        pricing=None,
        now=now,
    )

    assert record.status == "success"
    assert record.estimated_cost_usd is None
    assert record.actual["input_tokens"] == 0
    assert record.actual["cost_usd"] is None

    ledger = AIUsageLedger(tmp_path / "ai-usage")
    ledger.append(record)
    assert ledger.daily_unknown_cost_requests(now=now) == 0
    assert ledger.daily_unpriced_requests(now=now) == 1
    assert ledger.daily_unpriced_requests(now=datetime(2026, 8, 16, tzinfo=UTC)) == 0

    ledger.summary_path.write_text("not-json", encoding="utf-8")
    rebuilt = ledger.rebuild()
    assert rebuilt["daily"]["2026-08-15"]["unknown_cost_requests"] == 0
    assert rebuilt["daily"]["2026-08-15"]["unpriced_requests"] == 1


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        SimpleNamespace(total_tokens=1_000),
        SimpleNamespace(total_input_tokens=100, total_output_tokens=20),
        SimpleNamespace(total_input_tokens=100, total_thought_tokens=5),
    ],
    ids=["none", "empty", "aggregate-only", "missing-thinking", "missing-output"],
)
def test_unusable_interaction_usage_is_billing_unknown(usage) -> None:
    record, _search = usage_from_interaction(
        SimpleNamespace(id="interaction-unknown", usage=usage, steps=[]),
        run_id="run-1",
        request_id="request-unknown",
        requested_model="gemini-3.6-flash",
        service_tier="standard",
        content_type="text",
        tweet_ids=["42"],
        search_enabled=False,
        latency_ms=10,
        pricing=None,
    )

    assert record.status == "billing_unknown"
    assert record.estimated_cost_usd is None
    assert record.actual == {
        "input_tokens": None,
        "cached_tokens": None,
        "output_tokens": None,
        "thinking_tokens": None,
        "tool_tokens": None,
        "cost_usd": None,
    }
    assert record.prompt_tokens is None


def test_explicit_zero_interaction_usage_is_authoritative() -> None:
    record, _search = usage_from_interaction(
        SimpleNamespace(
            id="interaction-zero",
            usage=SimpleNamespace(
                total_input_tokens=0,
                total_output_tokens=0,
                total_thought_tokens=0,
            ),
            steps=[],
        ),
        run_id="run-1",
        request_id="request-zero",
        requested_model="gemini-3.6-flash",
        service_tier="standard",
        content_type="text",
        tweet_ids=["42"],
        search_enabled=False,
        latency_ms=10,
        pricing=None,
    )

    assert record.status == "success"
    assert record.prompt_tokens == 0
    assert record.actual["input_tokens"] == 0


def test_failed_usage_records_keep_zero_only_for_definite_pre_provider_failures() -> None:
    failed = failed_usage_record(
        run_id="run-1",
        request_id="request-failed",
        api_mode="paid",
        model="gemini-3.6-flash",
        service_tier="standard",
        content_type="text",
        tweet_ids=["42"],
        latency_ms=10,
        search_enabled=False,
        status="failed",
    )
    unknown = failed_usage_record(
        run_id="run-1",
        request_id="request-unknown",
        api_mode="paid",
        model="gemini-3.6-flash",
        service_tier="standard",
        content_type="text",
        tweet_ids=["42"],
        latency_ms=10,
        search_enabled=False,
        status="billing_unknown",
    )

    assert failed.actual["cost_usd"] == "0"
    assert failed.actual["input_tokens"] == 0
    assert unknown.actual["cost_usd"] is None
    assert unknown.actual["input_tokens"] is None


def test_legacy_paid_record_without_status_uses_narrow_unknown_fallback(tmp_path) -> None:
    ledger = AIUsageLedger(tmp_path / "ai-usage")
    payload = asdict(usage_record("legacy", cost=None))
    payload.pop("status")
    ledger.root.mkdir(parents=True, exist_ok=True)
    (ledger.root / "2026-08.jsonl").write_text(json.dumps(payload) + "\n", encoding="utf-8")

    summary = ledger.rebuild()
    assert summary["daily"]["2026-08-15"]["unknown_cost_requests"] == 1
    assert summary["daily"]["2026-08-15"]["unpriced_requests"] == 0


def _openrouter_response(payload: dict) -> SimpleNamespace:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(payload).encode()

    return Response()


def test_openrouter_pricing_cache_fetches_and_reuses_fresh_snapshot(tmp_path) -> None:
    payload = {
        "data": [
            {
                "id": "google/gemini-3.6-flash",
                "pricing": {
                    "prompt": "0.000001",
                    "completion": "0.000002",
                    "internal_reasoning": "0.000003",
                    "input_cache_read": "0.0000001",
                },
            },
            {
                "id": "google/gemini-3.5-flash",
                "pricing": {
                    "prompt": "0.000004",
                    "completion": "0.000005",
                    "internal_reasoning": "0.000006",
                    "input_cache_read": "0.0000004",
                },
            },
            {
                "id": "anthropic/claude-3.5-sonnet",
                "pricing": {
                    "prompt": "0.000007",
                    "completion": "0.000008",
                },
            },
        ]
    }
    calls = 0

    def opener(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _openrouter_response(payload)

    now = datetime(2026, 8, 15, tzinfo=UTC)
    first = load_openrouter_pricing(tmp_path, "gemini-3.6-flash", now=now, opener=opener)
    second = load_openrouter_pricing(
        tmp_path, "gemini-3.5-flash", now=datetime(2026, 8, 15, 1, tzinfo=UTC), opener=opener
    )
    assert first is not None and first.input_per_token == Decimal("0.000001")
    assert first.cached_input_per_token == Decimal("0.0000001")
    assert calculate_estimated_cost_usd(
        first,
        input_tokens=10,
        cached_tokens=10,
        output_tokens=0,
        thinking_tokens=0,
    ) == Decimal("0.000001")
    assert second is not None and second.stale is False
    assert second.input_per_token == Decimal("0.000004")
    assert calls == 1
    assert pricing_cache_path(tmp_path).exists()
    cached_payload = json.loads(pricing_cache_path(tmp_path).read_text(encoding="utf-8"))
    assert cached_payload["refresh_attempted_at"] == cached_payload["fetched_at"]
    assert set(cached_payload["models"]) == {
        "google/gemini-3.6-flash",
        "google/gemini-3.5-flash",
    }
    assert (
        load_openrouter_pricing(
            tmp_path,
            "gemini-not-in-catalog",
            now=datetime(2026, 8, 15, 2, tzinfo=UTC),
            opener=opener,
        )
        is None
    )
    assert calls == 1


def test_openrouter_pricing_uses_stale_cache_when_refresh_fails(tmp_path) -> None:
    cache = pricing_cache_path(tmp_path)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(
        json.dumps(
            {
                "fetched_at": "2026-08-13T00:00:00+00:00",
                "models": {
                    "google/gemini-3.6-flash": {
                        "prompt": "0.000001",
                        "completion": "0.000002",
                        "internal_reasoning": "0.000003",
                        "cache_read": "0.0000001",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    calls = 0

    def opener(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise OSError("offline")

    stale = load_openrouter_pricing(
        tmp_path,
        "gemini-3.6-flash",
        now=datetime(2026, 8, 15, tzinfo=UTC),
        opener=opener,
    )
    assert stale is not None and stale.stale is True
    payload = json.loads(cache.read_text(encoding="utf-8"))
    assert payload["fetched_at"] == "2026-08-13T00:00:00+00:00"
    assert payload["refresh_attempted_at"] == "2026-08-15T00:00:00+00:00"
    second = load_openrouter_pricing(
        tmp_path,
        "gemini-3.6-flash",
        now=datetime(2026, 8, 15, 0, 1, tzinfo=UTC),
        opener=opener,
    )
    assert second is not None and second.stale is True
    assert (
        load_openrouter_pricing(
            tmp_path, "gemini-unknown", now=datetime(2026, 8, 15, tzinfo=UTC), opener=opener
        )
        is None
    )
    assert calls == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"data": []},
        {
            "data": [
                {
                    "id": "anthropic/claude-3.5-sonnet",
                    "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                }
            ]
        },
        {
            "data": [
                {
                    "id": "google/gemini-no-price",
                    "pricing": {"prompt": "0.000001"},
                }
            ]
        },
    ],
    ids=["empty", "non-google", "missing-required-price"],
)
def test_openrouter_empty_usable_catalog_preserves_stale_snapshot(tmp_path, payload) -> None:
    cache = pricing_cache_path(tmp_path)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(
        json.dumps(
            {
                "fetched_at": "2026-08-13T00:00:00+00:00",
                "models": {
                    "google/gemini-3.6-flash": {
                        "prompt": "0.000001",
                        "completion": "0.000002",
                        "internal_reasoning": "0.000003",
                        "input_cache_read": "0.0000001",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    calls = 0

    def opener(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _openrouter_response(payload)

    stale = load_openrouter_pricing(
        tmp_path,
        "gemini-3.6-flash",
        now=datetime(2026, 8, 15, tzinfo=UTC),
        opener=opener,
    )
    assert stale is not None and stale.stale is True
    cached_payload = json.loads(cache.read_text(encoding="utf-8"))
    assert cached_payload["fetched_at"] == "2026-08-13T00:00:00+00:00"
    assert cached_payload["refresh_attempted_at"] == "2026-08-15T00:00:00+00:00"
    assert set(cached_payload["models"]) == {"google/gemini-3.6-flash"}
    second = load_openrouter_pricing(
        tmp_path,
        "gemini-3.6-flash",
        now=datetime(2026, 8, 15, 0, 1, tzinfo=UTC),
        opener=opener,
    )
    assert second is not None and second.stale is True
    assert calls == 1


def test_openrouter_pricing_retries_stale_refresh_after_attempt_ttl(tmp_path) -> None:
    cache = pricing_cache_path(tmp_path)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(
        json.dumps(
            {
                "fetched_at": "2026-08-13T00:00:00+00:00",
                "refresh_attempted_at": "2026-08-14T00:00:00+00:00",
                "models": {
                    "google/gemini-3.6-flash": {
                        "prompt": "0.000001",
                        "completion": "0.000002",
                        "internal_reasoning": "0.000003",
                        "cache_read": "0.0000001",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    payload = {
        "data": [
            {
                "id": "google/gemini-3.7-flash",
                "pricing": {
                    "prompt": "0.000004",
                    "completion": "0.000005",
                    "internal_reasoning": "0.000006",
                    "input_cache_read": "0.0000007",
                },
            }
        ]
    }
    calls = 0

    def opener(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _openrouter_response(payload)

    refreshed = load_openrouter_pricing(
        tmp_path,
        "gemini-3.7-flash",
        now=datetime(2026, 8, 15, tzinfo=UTC),
        opener=opener,
    )
    assert refreshed is not None and refreshed.stale is False
    assert refreshed.input_per_token == Decimal("0.000004")
    refreshed_payload = json.loads(cache.read_text(encoding="utf-8"))
    assert refreshed_payload["fetched_at"] == "2026-08-15T00:00:00+00:00"
    assert refreshed_payload["refresh_attempted_at"] == "2026-08-15T00:00:00+00:00"
    assert set(refreshed_payload["models"]) == {"google/gemini-3.7-flash"}
    assert calls == 1


def test_ledger_appends_jsonl_and_updates_atomic_summary(tmp_path) -> None:
    ledger = AIUsageLedger(tmp_path / "ai-usage")
    ledger.append(usage_record("one", searches=2))

    lines = (ledger.root / "2026-08.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["request_id"] == "one"
    summary = json.loads(ledger.summary_path.read_text(encoding="utf-8"))
    assert summary["lifetime"]["estimated_cost_usd"] == "0.01"
    assert summary["lifetime"]["requests"] == 1
    assert summary["search"]["2026-08"]["count"] == 2
    assert not list(ledger.root.glob(".summary.json.*.tmp"))


def test_concurrent_ledger_writes_do_not_corrupt_jsonl(tmp_path) -> None:
    ledger = AIUsageLedger(tmp_path / "ai-usage")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda index: ledger.append(usage_record(str(index))), range(40)))

    lines = (ledger.root / "2026-08.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 40
    assert len({json.loads(line)["request_id"] for line in lines}) == 40
    assert ledger.summary()["lifetime"]["requests"] == 40


def test_corrupt_summary_is_rebuilt_from_jsonl(tmp_path) -> None:
    ledger = AIUsageLedger(tmp_path / "ai-usage")
    ledger.append(usage_record("one", cost="1.25", searches=4))
    ledger.summary_path.write_text("not-json", encoding="utf-8")

    summary = ledger.summary()
    assert summary["lifetime"]["estimated_cost_usd"] == "1.25"
    assert summary["models"]["gemini-3.6-flash"]["requests"] == 1
    assert summary["search"]["2026-08"]["count"] == 4


def test_daily_spend_is_separate_from_lifetime_and_monthly_search_rolls_over(tmp_path) -> None:
    ledger = AIUsageLedger(tmp_path / "ai-usage")
    ledger.append(usage_record("old", timestamp="2026-07-31T23:00:00+00:00", cost="10"))
    ledger.append(
        usage_record(
            "today",
            timestamp="2026-08-15T12:00:00+00:00",
            cost="0.50",
            searches=4_900,
        )
    )
    now = datetime(2026, 8, 15, tzinfo=UTC)
    stats = ledger.statistics(now=now)
    assert ledger.daily_spend(now=now) == Decimal("0.50")
    assert stats["lifetime_cost_usd"] == "10.50"
    assert stats["models"]["gemini-3.6-flash"]["cost_usd"] == "10.50"
    assert stats["today_requests"] == 1
    assert stats["month_requests"] == 1
    assert len(stats["spend_history"]) == 365
    history = {item["date"]: item for item in stats["spend_history"]}
    assert history["2026-07-31"]["estimated_cost_usd"] == "10"
    assert history["2026-07-31"]["requests"] == 1
    assert history["2026-08-15"]["estimated_cost_usd"] == "0.50"
    assert history["2026-08-14"]["estimated_cost_usd"] == "0"
    assert stats["lifetime_spend_history"] == [
        {
            "date": "2026-07-01",
            "interval": "month",
            "estimated_cost_usd": "10",
            "requests": 1,
        },
        {
            "date": "2026-08-01",
            "interval": "month",
            "estimated_cost_usd": "0.50",
            "requests": 1,
        },
    ]
    assert not ledger.search_available(reserve=100, now=now)
    assert ledger.monthly_search_count(now=datetime(2026, 9, 1, tzinfo=UTC)) == 0


def test_statistics_break_down_known_cost_averages_by_tweet_type(tmp_path) -> None:
    ledger = AIUsageLedger(tmp_path / "ai-usage")
    ledger.append(replace(usage_record("text", cost="0.02"), tweet_count=2))
    ledger.append(
        replace(
            usage_record("image", cost="0.06"),
            content_type="media",
            tweet_count=2,
            media_count=3,
            image_count=3,
        )
    )
    ledger.append(
        replace(
            usage_record("video", cost="0.10"),
            content_type="media",
            tweet_count=1,
            media_count=1,
            video_count=1,
        )
    )
    ledger.append(
        replace(
            usage_record("unknown-video", cost=None, status="billing_unknown"),
            content_type="media",
            tweet_count=1,
            media_count=1,
            video_count=1,
        )
    )
    ledger.append(
        replace(
            usage_record("mixed", cost="0.08"),
            content_type="media",
            tweet_count=1,
            media_count=2,
            image_count=1,
            gif_count=1,
        )
    )

    types = ledger.statistics(now=datetime(2026, 8, 15, tzinfo=UTC))["tweet_types"]
    assert types["text_only"] == {
        "estimated_cost_usd": "0.02",
        "cost_usd": "0.02",
        "requests": 1,
        "priced_requests": 1,
        "tweets": 2,
        "priced_tweets": 2,
        "media_items": 0,
        "image_items": 0,
        "video_items": 0,
        "gif_items": 0,
    }
    assert types["image"]["media_items"] == 3
    assert types["image"]["image_items"] == 3
    assert types["image"]["priced_tweets"] == 2
    assert types["video"]["requests"] == 2
    assert types["video"]["priced_requests"] == 1
    assert types["video"]["tweets"] == 2
    assert types["video"]["priced_tweets"] == 1
    assert types["video"]["estimated_cost_usd"] == "0.10"
    assert types["mixed_media"]["image_items"] == 1
    assert types["mixed_media"]["gif_items"] == 1


def test_daily_unknown_billing_is_rebuilt_and_scoped_to_the_calendar_day(tmp_path) -> None:
    ledger = AIUsageLedger(tmp_path / "ai-usage")
    ledger.append(
        usage_record(
            "unknown",
            timestamp="2026-08-15T12:00:00+00:00",
            cost=None,
            status="billing_unknown",
        )
    )
    ledger.append(
        replace(
            usage_record("free", timestamp="2026-08-15T13:00:00+00:00", cost="0"),
            api_mode="free",
            service_tier="free",
            pricing_version="free",
            pricing_source="free",
            pricing_model=None,
            pricing_fetched_at=None,
            pricing={},
        )
    )
    ledger.append(
        replace(
            usage_record("pre-provider", timestamp="2026-08-15T14:00:00+00:00", cost=None),
            interaction_id=None,
            status="failed",
        )
    )

    summary = ledger.summary()
    assert summary["daily"]["2026-08-15"]["unknown_cost_requests"] == 1
    assert summary["unknown_cost_requests"] == 1
    assert (
        ledger.statistics(now=datetime(2026, 8, 15, tzinfo=UTC))["today_unknown_cost_requests"] == 1
    )
    assert ledger.daily_unknown_cost_requests(now=datetime(2026, 8, 16, tzinfo=UTC)) == 0
    assert ledger.daily_unpriced_requests(now=datetime(2026, 8, 16, tzinfo=UTC)) == 0

    ledger.summary_path.write_text("not-json", encoding="utf-8")
    rebuilt = ledger.rebuild()
    assert rebuilt["daily"]["2026-08-15"]["unknown_cost_requests"] == 1
    assert rebuilt["daily"]["2026-08-15"]["unpriced_requests"] == 0
