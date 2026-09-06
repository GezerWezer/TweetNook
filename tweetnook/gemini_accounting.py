"""Append-only Gemini request ledger and fast aggregate accounting."""

from __future__ import annotations

import fcntl
import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from .gemini_pricing import (
    OpenRouterPricing,
    calculate_estimated_cost_usd,
)
from .gemini_pricing import (
    pricing_multiplier as tier_pricing_multiplier,
)

SEARCH_MONTHLY_ALLOWANCE = 5_000
LEDGER_VERSION = 2
_WRITE_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class SearchAccounting:
    """Search usage observed for one provider attempt.

    ``count`` is deliberately nullable: an enabled Search request whose provider
    response cannot be accounted for is materially different from a request that
    authoritative metadata says performed zero searches.
    """

    count: int | None
    source: Literal[
        "usage",
        "interaction_steps",
        "steps",  # legacy spelling accepted when reading older callers
        "unknown",
        "unavailable",  # legacy spelling accepted when reading older callers
        "disabled",
    ]
    queries: tuple[str, ...] = ()

    @property
    def reliable(self) -> bool:
        return self.source in {"usage", "interaction_steps", "steps", "disabled"}


@dataclass(frozen=True, slots=True)
class UsageRecord:
    timestamp: str
    run_id: str
    request_id: str
    interaction_id: str | None
    api_mode: Literal["free", "paid"]
    model: str
    model_version: str
    service_tier: str
    content_type: Literal["text", "media"]
    tweet_ids: list[str]
    prompt_tokens: int | None
    cached_tokens: int | None
    output_tokens: int | None
    thinking_tokens: int | None
    google_search_count: int | None
    google_search_accounting_source: str
    cost_usd: str | None
    pricing_version: str
    status: str
    latency_ms: int
    google_search_queries: list[str] = field(default_factory=list)
    search_reservation_consumed: int | None = None
    # Request metadata retained for diagnostics and future analysis.
    attempt: int = 1
    api_tier: str | None = None
    api_surface: str | None = None
    requested_model: str | None = None
    thinking_level: str | None = None
    request_type: str | None = None
    tweet_count: int = 0
    media_count: int = 0
    image_count: int = 0
    video_count: int = 0
    gif_count: int = 0
    input_text_chars: int = 0
    existing_tag_count: int = 0
    tagging_hint_count: int = 0
    additional_instructions_present: bool = False
    google_search_enabled: bool = False
    estimate: dict[str, Any] = field(default_factory=dict)
    actual: dict[str, Any] = field(default_factory=dict)
    reserved_cost_usd: str = "0"
    # OpenRouter is a pricing metadata source only.  Persist the snapshot and
    # rates used for this request so old estimates never depend on a future
    # catalogue response.
    pricing_source: str | None = None
    pricing_model: str | None = None
    pricing_fetched_at: str | None = None
    pricing_stale: bool = False
    pricing_multiplier: str = "1"
    pricing: dict[str, str] = field(default_factory=dict)
    estimated_cost_usd: str | None = None
    preflight_input_tokens: int | None = None
    tool_tokens: int | None = 0


def new_request_id() -> str:
    return uuid.uuid4().hex


def _attribute(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _usage_value(metadata: Any, *names: str) -> int:
    for name in names:
        value = _attribute(metadata, name)
        if value is not None:
            return max(int(value), 0)
    return 0


def _usage_field_present(usage: Any, *names: str) -> bool:
    return usage is not None and any(_attribute(usage, name, None) is not None for name in names)


def _has_usable_interaction_usage(usage: Any) -> bool:
    """Return whether Gemini supplied all billed token categories explicitly."""

    return (
        usage is not None
        and _usage_field_present(
            usage,
            "total_input_tokens",
            "input_tokens",
            "prompt_token_count",
        )
        and _usage_field_present(
            usage,
            "total_output_tokens",
            "output_tokens",
            "output_token_count",
            "candidates_token_count",
        )
        and _usage_field_present(
            usage,
            "total_thought_tokens",
            "total_thinking_tokens",
            "thinking_token_count",
            "thought_tokens",
            "thoughts_token_count",
        )
    )


def _service_tier(value: Any) -> str:
    normalized = str(value or "standard").casefold()
    for tier in ("flex", "standard", "priority", "batch"):
        if normalized.endswith(tier):
            return tier
    return normalized


def search_step_queries(interaction: Any) -> tuple[str, ...]:
    queries: list[str] = []
    steps = _attribute(interaction, "steps", None)
    if not steps:
        outputs = _attribute(interaction, "outputs", None)
        steps = outputs if outputs is not None else steps
    if not steps:
        steps = _attribute(interaction, "outputs_or_steps", ())
    for step in steps or ():
        if str(_attribute(step, "type", "")) != "google_search_call":
            continue
        arguments = _attribute(step, "arguments", {}) or {}
        plural = _attribute(arguments, "queries", None)
        if plural:
            if isinstance(plural, str):
                plural = (plural,)
            else:
                try:
                    plural = tuple(plural)
                except TypeError:
                    plural = (plural,)
            for value in plural or ():
                if isinstance(value, str):
                    query = value.strip()
                    if query:
                        queries.append(query)
            continue
        singular = _attribute(arguments, "query", "")
        if singular is not None:
            query = str(singular).strip()
            if query:
                queries.append(query)
    return tuple(queries)


def interaction_search_accounting(
    interaction: Any,
    *,
    search_enabled: bool,
) -> SearchAccounting:
    if not search_enabled:
        return SearchAccounting(0, "disabled")
    queries = search_step_queries(interaction)
    usage = _attribute(interaction, "usage")
    counts = _attribute(usage, "grounding_tool_count") if usage is not None else None
    if counts is not None:
        if isinstance(counts, dict) and "type" not in counts:
            counts = [{"type": key, "count": value} for key, value in counts.items()]
        elif isinstance(counts, dict | str | bytes):
            counts = (counts,)
        elif not isinstance(counts, list | tuple | set):
            counts = (counts,)
        count = sum(
            max(int(_attribute(item, "count", 0) or 0), 0)
            for item in counts
            if str(_attribute(item, "type", "")) == "google_search"
        )
        return SearchAccounting(count, "usage", queries)
    if queries:
        return SearchAccounting(len(queries), "interaction_steps", queries)
    return SearchAccounting(None, "unknown")


def usage_from_interaction(
    interaction: Any,
    *,
    run_id: str,
    request_id: str,
    requested_model: str,
    service_tier: str,
    content_type: Literal["text", "media"],
    tweet_ids: list[str],
    search_enabled: bool,
    latency_ms: int,
    search_reservation_consumed: int | None = None,
    attempt: int = 1,
    thinking_level: str | None = None,
    request_metadata: dict[str, Any] | None = None,
    estimate: dict[str, Any] | None = None,
    reserved_cost_usd: str = "0",
    pricing: OpenRouterPricing | None = None,
    pricing_multiplier: Decimal | None = None,
    preflight_input_tokens: int | None = None,
    now: datetime | None = None,
) -> tuple[UsageRecord, SearchAccounting]:
    effective_now = now or datetime.now(UTC)
    usage = _attribute(interaction, "usage")
    actual_model = str(_attribute(interaction, "model", None) or requested_model)
    actual_tier = _service_tier(_attribute(interaction, "service_tier", None) or service_tier)
    usage_known = _has_usable_interaction_usage(usage)
    if usage_known:
        prompt = _usage_value(
            usage,
            "total_input_tokens",
            "input_tokens",
            "prompt_token_count",
        )
        cached = _usage_value(usage, "total_cached_tokens", "cached_tokens")
        output = _usage_value(
            usage,
            "total_output_tokens",
            "output_tokens",
            "output_token_count",
            "candidates_token_count",
        )
        thinking = _usage_value(
            usage,
            "total_thought_tokens",
            "total_thinking_tokens",
            "thinking_token_count",
            "thought_tokens",
            "thoughts_token_count",
        )
        tool_tokens = _usage_value(usage, "total_tool_tokens", "tool_tokens", "tool_token_count")
    else:
        prompt = cached = output = thinking = tool_tokens = None
    effective_multiplier = (
        pricing_multiplier
        if pricing_multiplier is not None
        else tier_pricing_multiplier(actual_tier)
    )
    cost = (
        calculate_estimated_cost_usd(
            pricing,
            input_tokens=prompt,
            cached_tokens=cached,
            output_tokens=output,
            thinking_tokens=thinking,
            multiplier=effective_multiplier,
        )
        if pricing is not None and usage_known
        else None
    )
    search = interaction_search_accounting(interaction, search_enabled=search_enabled)
    metadata = request_metadata or {}
    actual = {
        "input_tokens": prompt,
        "cached_tokens": cached,
        "output_tokens": output,
        "thinking_tokens": thinking,
        "tool_tokens": tool_tokens,
        "cost_usd": format(cost, "f") if cost is not None else None,
    }
    estimated = format(cost, "f") if cost is not None else None
    status = "success" if usage_known else "billing_unknown"
    return (
        UsageRecord(
            timestamp=effective_now.isoformat(),
            run_id=run_id,
            request_id=request_id,
            interaction_id=str(_attribute(interaction, "id", "") or "") or None,
            api_mode="paid",
            model=requested_model,
            model_version=actual_model,
            service_tier=actual_tier,
            content_type=content_type,
            tweet_ids=list(tweet_ids),
            prompt_tokens=prompt,
            cached_tokens=cached,
            output_tokens=output,
            thinking_tokens=thinking,
            google_search_count=search.count,
            google_search_accounting_source=search.source,
            google_search_queries=list(search.queries),
            cost_usd=estimated,
            pricing_version=(pricing.pricing_model if pricing is not None else "unavailable"),
            status=status,
            latency_ms=max(int(latency_ms), 0),
            search_reservation_consumed=search_reservation_consumed,
            attempt=attempt,
            api_tier="paid",
            api_surface="interactions",
            requested_model=requested_model,
            thinking_level=thinking_level,
            request_type=content_type,
            tweet_count=int(metadata.get("tweet_count", len(tweet_ids))),
            media_count=int(metadata.get("media_count", 0)),
            image_count=int(metadata.get("image_count", 0)),
            video_count=int(metadata.get("video_count", 0)),
            gif_count=int(metadata.get("gif_count", 0)),
            input_text_chars=int(metadata.get("input_text_chars", 0)),
            existing_tag_count=int(metadata.get("existing_tag_count", 0)),
            tagging_hint_count=int(metadata.get("tagging_hint_count", 0)),
            additional_instructions_present=bool(
                metadata.get("additional_instructions_present", False)
            ),
            google_search_enabled=search_enabled,
            estimate=dict(estimate or {}),
            actual=actual,
            reserved_cost_usd=str(reserved_cost_usd),
            pricing_source=pricing.pricing_source if pricing is not None else "unavailable",
            pricing_model=pricing.pricing_model if pricing is not None else None,
            pricing_fetched_at=pricing.fetched_at if pricing is not None else None,
            pricing_stale=bool(pricing.stale) if pricing is not None else False,
            pricing_multiplier=format(effective_multiplier, "f"),
            pricing=pricing.rates if pricing is not None else {},
            estimated_cost_usd=estimated,
            preflight_input_tokens=preflight_input_tokens,
            tool_tokens=tool_tokens,
        ),
        search,
    )


def usage_from_generate_content(
    response: Any,
    *,
    run_id: str,
    request_id: str,
    requested_model: str,
    content_type: Literal["text", "media"],
    tweet_ids: list[str],
    latency_ms: int,
    attempt: int = 1,
    thinking_level: str | None = None,
    request_metadata: dict[str, Any] | None = None,
    estimate: dict[str, Any] | None = None,
    reserved_cost_usd: str = "0",
    now: datetime | None = None,
) -> UsageRecord:
    effective_now = now or datetime.now(UTC)
    metadata = _attribute(response, "usage_metadata")
    if metadata is None:
        raise ValueError("Gemini response did not include usage metadata")
    prompt = _usage_value(metadata, "prompt_token_count", "input_token_count")
    cached = _usage_value(metadata, "cached_content_token_count", "cached_token_count")
    output = _usage_value(metadata, "candidates_token_count", "output_token_count")
    thinking = _usage_value(
        metadata,
        "thoughts_token_count",
        "thinking_token_count",
        "total_thinking_tokens",
    )
    request_metadata = request_metadata or {}
    return UsageRecord(
        timestamp=effective_now.isoformat(),
        run_id=run_id,
        request_id=request_id,
        interaction_id=None,
        api_mode="free",
        model=requested_model,
        model_version=str(_attribute(response, "model_version", None) or requested_model),
        service_tier="free",
        content_type=content_type,
        tweet_ids=list(tweet_ids),
        prompt_tokens=prompt,
        cached_tokens=cached,
        output_tokens=output,
        thinking_tokens=thinking,
        google_search_count=0,
        google_search_accounting_source="disabled",
        cost_usd="0",
        pricing_version="free",
        status="success",
        latency_ms=max(int(latency_ms), 0),
        attempt=attempt,
        api_tier="free",
        api_surface="generate_content",
        requested_model=requested_model,
        thinking_level=thinking_level,
        request_type=content_type,
        tweet_count=int(request_metadata.get("tweet_count", len(tweet_ids))),
        media_count=int(request_metadata.get("media_count", 0)),
        image_count=int(request_metadata.get("image_count", 0)),
        video_count=int(request_metadata.get("video_count", 0)),
        gif_count=int(request_metadata.get("gif_count", 0)),
        input_text_chars=int(request_metadata.get("input_text_chars", 0)),
        existing_tag_count=int(request_metadata.get("existing_tag_count", 0)),
        tagging_hint_count=int(request_metadata.get("tagging_hint_count", 0)),
        additional_instructions_present=bool(
            request_metadata.get("additional_instructions_present", False)
        ),
        google_search_enabled=False,
        estimate=dict(estimate or {}),
        actual={
            "input_tokens": prompt,
            "cached_tokens": cached,
            "output_tokens": output,
            "thinking_tokens": thinking,
            "tool_tokens": 0,
            "cost_usd": "0",
        },
        reserved_cost_usd=str(reserved_cost_usd),
        pricing_source="free",
        pricing_model=None,
        pricing_fetched_at=None,
        pricing_stale=False,
        pricing_multiplier="1",
        pricing={},
        estimated_cost_usd="0",
        tool_tokens=0,
    )


def failed_usage_record(
    *,
    run_id: str,
    request_id: str,
    api_mode: Literal["free", "paid"],
    model: str,
    service_tier: str,
    content_type: Literal["text", "media"],
    tweet_ids: list[str],
    latency_ms: int,
    search_enabled: bool,
    search: SearchAccounting | None = None,
    search_reservation_consumed: int | None = None,
    attempt: int = 1,
    thinking_level: str | None = None,
    request_metadata: dict[str, Any] | None = None,
    estimate: dict[str, Any] | None = None,
    reserved_cost_usd: str = "0",
    pricing: OpenRouterPricing | None = None,
    pricing_multiplier: Decimal = Decimal("1"),
    preflight_input_tokens: int | None = None,
    status: str = "failed",
    now: datetime | None = None,
) -> UsageRecord:
    effective_now = now or datetime.now(UTC)
    request_metadata = request_metadata or {}
    billing_unknown = str(status).casefold() == "billing_unknown"
    known_zero = None if billing_unknown else 0
    actual_cost = None if billing_unknown else "0"
    return UsageRecord(
        timestamp=effective_now.isoformat(),
        run_id=run_id,
        request_id=request_id,
        interaction_id=None,
        api_mode=api_mode,
        model=model,
        model_version=model,
        service_tier=service_tier if api_mode == "paid" else "free",
        content_type=content_type,
        tweet_ids=list(tweet_ids),
        prompt_tokens=known_zero,
        cached_tokens=known_zero,
        output_tokens=known_zero,
        thinking_tokens=known_zero,
        google_search_count=search.count if search is not None else (None if search_enabled else 0),
        google_search_accounting_source=(
            search.source if search is not None else ("unknown" if search_enabled else "disabled")
        ),
        google_search_queries=list(search.queries) if search is not None else [],
        cost_usd="0" if api_mode == "free" else None,
        pricing_version=(
            pricing.pricing_model
            if pricing is not None
            else ("free" if api_mode == "free" else "unavailable")
        ),
        status=status,
        latency_ms=max(int(latency_ms), 0),
        search_reservation_consumed=search_reservation_consumed,
        attempt=attempt,
        api_tier=api_mode,
        api_surface="interactions" if api_mode == "paid" else "generate_content",
        requested_model=model,
        thinking_level=thinking_level,
        request_type=content_type,
        tweet_count=int(request_metadata.get("tweet_count", len(tweet_ids))),
        media_count=int(request_metadata.get("media_count", 0)),
        image_count=int(request_metadata.get("image_count", 0)),
        video_count=int(request_metadata.get("video_count", 0)),
        gif_count=int(request_metadata.get("gif_count", 0)),
        input_text_chars=int(request_metadata.get("input_text_chars", 0)),
        existing_tag_count=int(request_metadata.get("existing_tag_count", 0)),
        tagging_hint_count=int(request_metadata.get("tagging_hint_count", 0)),
        additional_instructions_present=bool(
            request_metadata.get("additional_instructions_present", False)
        ),
        google_search_enabled=search_enabled,
        estimate=dict(estimate or {}),
        actual={
            "input_tokens": known_zero,
            "cached_tokens": known_zero,
            "output_tokens": known_zero,
            "thinking_tokens": known_zero,
            "tool_tokens": known_zero,
            "cost_usd": actual_cost,
        },
        reserved_cost_usd=str(reserved_cost_usd),
        pricing_source=(
            "free" if api_mode == "free" else (pricing.pricing_source if pricing else "unavailable")
        ),
        pricing_model=pricing.pricing_model if pricing is not None else None,
        pricing_fetched_at=pricing.fetched_at if pricing is not None else None,
        pricing_stale=bool(pricing.stale) if pricing is not None else False,
        pricing_multiplier=format(pricing_multiplier, "f"),
        pricing=pricing.rates if pricing is not None else {},
        estimated_cost_usd="0" if api_mode == "free" else None,
        preflight_input_tokens=preflight_input_tokens,
        tool_tokens=known_zero,
    )


def _empty_summary() -> dict[str, Any]:
    return {
        "version": LEDGER_VERSION,
        "lifetime": {"estimated_cost_usd": "0", "cost_usd": "0", "requests": 0},
        "models": {},
        "tweet_types": {},
        "daily": {},
        "monthly": {},
        "search": {},
        "unknown_cost_requests": 0,
    }


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _increment_cost(bucket: dict[str, Any], cost: Decimal) -> None:
    total = _decimal_text(
        Decimal(str(bucket.get("estimated_cost_usd", bucket.get("cost_usd", "0")))) + cost
    )
    # ``cost_usd`` remains as a read-compatible alias for older UI/API clients;
    # new consumers should use the explicit estimated_cost_usd name.
    bucket["estimated_cost_usd"] = total
    bucket["cost_usd"] = total
    bucket["requests"] = int(bucket.get("requests", 0)) + 1


def _is_unknown_paid_billing(record: dict[str, Any], raw_cost: Any) -> bool:
    if str(record.get("api_mode") or record.get("api_tier") or "").casefold() != "paid":
        return False
    if "status" in record:
        return str(record.get("status", "")).casefold() == "billing_unknown"
    # A failed interaction can still carry an interaction ID even when the
    # provider omitted usage. Pre-provider failures have no such evidence and
    # must remain ordinary failed requests rather than billing uncertainty.
    return raw_cost is None and bool(record.get("interaction_id"))


def _record_has_known_provider_usage(record: dict[str, Any]) -> bool:
    actual = record.get("actual")
    if not isinstance(actual, dict):
        return False
    return all(
        actual.get(name) is not None
        for name in ("input_tokens", "output_tokens", "thinking_tokens")
    )


def _is_unpriced_paid_request(record: dict[str, Any], raw_cost: Any) -> bool:
    if (
        str(record.get("api_mode") or record.get("api_tier") or "").casefold() != "paid"
        or raw_cost is not None
    ):
        return False
    status = str(record.get("status", "")).casefold()
    if status == "billing_unknown":
        return False
    if status == "success":
        return True
    if status == "failed":
        actual = record.get("actual")
        return (
            _record_has_known_provider_usage(record)
            and isinstance(actual, dict)
            and actual.get("cost_usd") is None
        )
    return False


def _nonnegative_record_count(record: dict[str, Any], field: str) -> int:
    try:
        return max(int(record.get(field, 0) or 0), 0)
    except (TypeError, ValueError):
        return 0


def _tweet_type(record: dict[str, Any]) -> tuple[str, int, int, int, int]:
    image_count = _nonnegative_record_count(record, "image_count")
    video_count = _nonnegative_record_count(record, "video_count")
    gif_count = _nonnegative_record_count(record, "gif_count")
    typed_media_count = image_count + video_count + gif_count
    media_count = max(_nonnegative_record_count(record, "media_count"), typed_media_count)
    active_types = sum(count > 0 for count in (image_count, video_count, gif_count))
    if media_count == 0 and str(record.get("content_type", "")).casefold() != "media":
        kind = "text_only"
    elif active_types > 1:
        kind = "mixed_media"
    elif image_count:
        kind = "image"
    elif video_count:
        kind = "video"
    elif gif_count:
        kind = "gif"
    else:
        kind = "other_media"
    return kind, media_count, image_count, video_count, gif_count


def _apply_tweet_type(
    summary: dict[str, Any], record: dict[str, Any], cost: Decimal | None
) -> None:
    kind, media_count, image_count, video_count, gif_count = _tweet_type(record)
    tweet_ids = record.get("tweet_ids")
    fallback_tweets = len(tweet_ids) if isinstance(tweet_ids, list) else 0
    tweet_count = _nonnegative_record_count(record, "tweet_count") or fallback_tweets or 1
    bucket = summary["tweet_types"].setdefault(
        kind,
        {
            "estimated_cost_usd": "0",
            "cost_usd": "0",
            "requests": 0,
            "priced_requests": 0,
            "tweets": 0,
            "priced_tweets": 0,
            "media_items": 0,
            "image_items": 0,
            "video_items": 0,
            "gif_items": 0,
        },
    )
    bucket["requests"] = int(bucket.get("requests", 0)) + 1
    bucket["tweets"] = int(bucket.get("tweets", 0)) + tweet_count
    bucket["media_items"] = int(bucket.get("media_items", 0)) + media_count
    bucket["image_items"] = int(bucket.get("image_items", 0)) + image_count
    bucket["video_items"] = int(bucket.get("video_items", 0)) + video_count
    bucket["gif_items"] = int(bucket.get("gif_items", 0)) + gif_count
    if cost is not None:
        total = _decimal_text(
            Decimal(str(bucket.get("estimated_cost_usd", bucket.get("cost_usd", "0")))) + cost
        )
        bucket["estimated_cost_usd"] = total
        bucket["cost_usd"] = total
        bucket["priced_requests"] = int(bucket.get("priced_requests", 0)) + 1
        bucket["priced_tweets"] = int(bucket.get("priced_tweets", 0)) + tweet_count


def _apply_record(summary: dict[str, Any], record: dict[str, Any]) -> None:
    try:
        timestamp = datetime.fromisoformat(str(record["timestamp"]).replace("Z", "+00:00"))
        model = str(record["model"])
    except (KeyError, TypeError, ValueError):
        return
    has_estimate = "estimated_cost_usd" in record
    raw_cost = record.get("estimated_cost_usd") if has_estimate else record.get("cost_usd", "0")
    # Rows created by older integrations predate the explicit estimate field;
    # retain their historical cost alias.  New unavailable/billing-unknown
    # rows carry pricing_source="unavailable" and must remain unknown rather
    # than being silently treated as zero.
    if (
        raw_cost is None
        and record.get("pricing_source") is None
        and str(record.get("status", "")).casefold() != "billing_unknown"
    ):
        raw_cost = record.get("cost_usd", "0")
    billing_unknown = _is_unknown_paid_billing(record, raw_cost)
    cost: Decimal | None = None
    if not billing_unknown and raw_cost is not None:
        try:
            cost = Decimal(str(raw_cost))
            if not cost.is_finite() or cost < 0:
                cost = None
        except (TypeError, ValueError, ArithmeticError):
            cost = None
    day = timestamp.astimezone(UTC).strftime("%Y-%m-%d")
    month = day[:7]
    daily = summary["daily"].setdefault(
        day,
        {
            "estimated_cost_usd": "0",
            "cost_usd": "0",
            "requests": 0,
            "unknown_cost_requests": 0,
            "unpriced_requests": 0,
        },
    )
    if billing_unknown:
        summary["unknown_cost_requests"] = int(summary.get("unknown_cost_requests", 0)) + 1
        daily["unknown_cost_requests"] = int(daily.get("unknown_cost_requests", 0)) + 1
    elif _is_unpriced_paid_request(record, raw_cost):
        daily["unpriced_requests"] = int(daily.get("unpriced_requests", 0)) + 1
    # Request counts include unknown/billing-unknown attempts.  Only known
    # estimates contribute to spend totals.
    for bucket in (
        summary["lifetime"],
        summary["models"].setdefault(
            model, {"estimated_cost_usd": "0", "cost_usd": "0", "requests": 0}
        ),
        daily,
        summary["monthly"].setdefault(
            month, {"estimated_cost_usd": "0", "cost_usd": "0", "requests": 0}
        ),
    ):
        if cost is not None:
            _increment_cost(bucket, cost)
        else:
            bucket["requests"] = int(bucket.get("requests", 0)) + 1
    _apply_tweet_type(summary, record, cost)
    raw_search_count = record.get("google_search_count")
    if raw_search_count is None:
        raw_search_count = record.get("search_reservation_consumed", 0)
    search_count = max(int(raw_search_count or 0), 0)
    search = summary["search"].setdefault(month, {"count": 0})
    search["count"] = int(search.get("count", 0)) + search_count


class AIUsageLedger:
    """Concurrent append-only request records plus an atomically replaced summary."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.summary_path = self.root / "summary.json"
        self.lock_path = self.root / "ledger.lock"

    def _atomic_summary(self, summary: dict[str, Any]) -> None:
        temporary = self.summary_path.with_name(
            f".{self.summary_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(summary, sort_keys=True, separators=(",", ":")))
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(self.summary_path)

    def _read_summary(self) -> dict[str, Any] | None:
        try:
            value = json.loads(self.summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        required = {"lifetime", "models", "tweet_types", "daily", "monthly", "search"}
        if not isinstance(value, dict) or not required.issubset(value):
            return None
        if any(not isinstance(value.get(key), dict) for key in required):
            return None
        if "unknown_cost_requests" not in value:
            return None
        if any(
            not isinstance(bucket, dict)
            or "unknown_cost_requests" not in bucket
            or "unpriced_requests" not in bucket
            for bucket in value["daily"].values()
        ):
            return None
        value["version"] = LEDGER_VERSION
        value.setdefault("unknown_cost_requests", 0)
        buckets = [
            value.get("lifetime"),
            *value.get("models", {}).values(),
            *value.get("daily", {}).values(),
            *value.get("monthly", {}).values(),
        ]
        for bucket in buckets:
            if isinstance(bucket, dict):
                if "estimated_cost_usd" not in bucket:
                    bucket["estimated_cost_usd"] = str(bucket.get("cost_usd", "0"))
                bucket.setdefault("cost_usd", str(bucket["estimated_cost_usd"]))
                bucket.setdefault("requests", 0)
        return value

    def _rebuild_locked(self) -> dict[str, Any]:
        summary = _empty_summary()
        for path in sorted(self.root.glob("????-??.jsonl")):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    _apply_record(summary, payload)
        self._atomic_summary(summary)
        return summary

    def _with_lock(self, operation):
        self.root.mkdir(parents=True, exist_ok=True)
        with _WRITE_LOCK, self.lock_path.open("a+") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                return operation()
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def summary(self) -> dict[str, Any]:
        return self._with_lock(lambda: self._read_summary() or self._rebuild_locked())

    def rebuild(self) -> dict[str, Any]:
        return self._with_lock(self._rebuild_locked)

    def append(self, record: UsageRecord) -> dict[str, Any]:
        payload = asdict(record)

        def write() -> dict[str, Any]:
            summary = self._read_summary() or self._rebuild_locked()
            timestamp = datetime.fromisoformat(record.timestamp.replace("Z", "+00:00"))
            ledger_path = self.root / f"{timestamp.astimezone(UTC):%Y-%m}.jsonl"
            with ledger_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            _apply_record(summary, payload)
            self._atomic_summary(summary)
            return summary

        return self._with_lock(write)

    def statistics(self, *, now: datetime | None = None) -> dict[str, Any]:
        effective_now = (now or datetime.now(UTC)).astimezone(UTC)
        day = effective_now.strftime("%Y-%m-%d")
        month = effective_now.strftime("%Y-%m")
        summary = self.summary()
        today = summary["daily"].get(day, {})
        month_bucket = summary["monthly"].get(month, {})
        lifetime = summary["lifetime"]
        today_cost = str(today.get("estimated_cost_usd", today.get("cost_usd", "0")))
        month_cost = str(month_bucket.get("estimated_cost_usd", month_bucket.get("cost_usd", "0")))
        lifetime_cost = str(lifetime.get("estimated_cost_usd", lifetime.get("cost_usd", "0")))
        spend_history = []
        first_day = effective_now.date() - timedelta(days=364)
        for offset in range(365):
            history_day = first_day + timedelta(days=offset)
            history_key = history_day.isoformat()
            bucket = summary["daily"].get(history_key, {})
            history_cost = str(bucket.get("estimated_cost_usd", bucket.get("cost_usd", "0")))
            spend_history.append(
                {
                    "date": history_key,
                    "estimated_cost_usd": history_cost,
                    "requests": int(bucket.get("requests", 0)),
                    "unknown_cost_requests": int(bucket.get("unknown_cost_requests", 0)),
                    "unpriced_requests": int(bucket.get("unpriced_requests", 0)),
                }
            )
        lifetime_spend_history = []
        valid_months = []
        for month_key in summary["monthly"]:
            try:
                parsed_month = datetime.strptime(month_key, "%Y-%m")
            except (TypeError, ValueError):
                continue
            if parsed_month.date() <= effective_now.date():
                valid_months.append((parsed_month.year, parsed_month.month))
        if valid_months:
            year, month_number = min(valid_months)
            while (year, month_number) <= (effective_now.year, effective_now.month):
                history_key = f"{year:04d}-{month_number:02d}"
                bucket = summary["monthly"].get(history_key, {})
                lifetime_spend_history.append(
                    {
                        "date": f"{history_key}-01",
                        "interval": "month",
                        "estimated_cost_usd": str(
                            bucket.get("estimated_cost_usd", bucket.get("cost_usd", "0"))
                        ),
                        "requests": int(bucket.get("requests", 0)),
                    }
                )
                if month_number == 12:
                    year += 1
                    month_number = 1
                else:
                    month_number += 1
        return {
            "today_cost_usd": today_cost,
            "month_cost_usd": month_cost,
            "lifetime_cost_usd": lifetime_cost,
            "today_estimated_cost_usd": today_cost,
            "month_estimated_cost_usd": month_cost,
            "lifetime_estimated_cost_usd": lifetime_cost,
            "today_requests": int(today.get("requests", 0)),
            "month_requests": int(month_bucket.get("requests", 0)),
            "lifetime_requests": int(lifetime.get("requests", 0)),
            "models": summary["models"],
            "tweet_types": summary["tweet_types"],
            "spend_history": spend_history,
            "lifetime_spend_history": lifetime_spend_history,
            "search_queries_this_month": int(summary["search"].get(month, {}).get("count", 0)),
            "search_allowance": SEARCH_MONTHLY_ALLOWANCE,
            "unknown_cost_requests": int(summary.get("unknown_cost_requests", 0)),
            "today_unknown_cost_requests": int(today.get("unknown_cost_requests", 0)),
            "today_unpriced_requests": int(today.get("unpriced_requests", 0)),
        }

    def daily_spend(self, *, now: datetime | None = None) -> Decimal:
        return Decimal(self.statistics(now=now)["today_cost_usd"])

    def daily_unknown_cost_requests(self, *, now: datetime | None = None) -> int:
        return int(self.statistics(now=now)["today_unknown_cost_requests"])

    def daily_unpriced_requests(self, *, now: datetime | None = None) -> int:
        return int(self.statistics(now=now)["today_unpriced_requests"])

    def monthly_search_count(self, *, now: datetime | None = None) -> int:
        return int(self.statistics(now=now)["search_queries_this_month"])

    def search_available(self, *, reserve: int, now: datetime | None = None) -> bool:
        return self.monthly_search_count(now=now) < SEARCH_MONTHLY_ALLOWANCE - reserve
