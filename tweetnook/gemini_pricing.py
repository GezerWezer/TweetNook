"""Small OpenRouter pricing cache used as a direct-Gemini cost reference.

TweetNook never sends inference to OpenRouter.  The public model catalogue is
used only because it provides a convenient, machine-readable estimate for the
corresponding Gemini model.  The rates and the fetch timestamp are copied into
each request ledger row so historical estimates remain reproducible.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib import request

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
PRICING_CACHE_FILENAME = "openrouter-pricing.json"
PRICING_CACHE_TTL = timedelta(hours=24)
_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class OpenRouterPricing:
    """One model's base (Standard) rates from an OpenRouter snapshot."""

    pricing_model: str
    fetched_at: str
    stale: bool
    input_per_token: Decimal
    output_per_token: Decimal
    thinking_per_token: Decimal
    cached_input_per_token: Decimal
    pricing_source: str = "openrouter"

    @property
    def rates(self) -> dict[str, str]:
        return {
            "input_per_token": format(self.input_per_token, "f"),
            "output_per_token": format(self.output_per_token, "f"),
            "thinking_per_token": format(self.thinking_per_token, "f"),
            "cached_input_per_token": format(self.cached_input_per_token, "f"),
        }


def normalize_model(model: str) -> str:
    """Normalize a Gemini model ID without maintaining a provider mapping."""

    return str(model or "").strip().removeprefix("models/").casefold()


def openrouter_model_id(model: str) -> str:
    normalized = normalize_model(model)
    return normalized if "/" in normalized else f"google/{normalized}"


def pricing_cache_path(root: Path) -> Path:
    return Path(root) / PRICING_CACHE_FILENAME


def _parse_decimal(value: Any) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("OpenRouter returned an invalid pricing value") from error
    if not parsed.is_finite() or parsed < _ZERO:
        raise ValueError("OpenRouter returned an invalid pricing value")
    return parsed


def _parse_fetched_at(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("OpenRouter pricing cache has no fetched_at timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _read_cache_payload(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _pricing_from_cache(payload: Any, model_id: str, *, stale: bool) -> OpenRouterPricing | None:
    if not isinstance(payload, dict):
        return None
    fetched_at = payload.get("fetched_at")
    try:
        _parse_fetched_at(fetched_at)
    except (TypeError, ValueError):
        return None
    models = payload.get("models")
    if not isinstance(models, dict):
        return None
    raw = models.get(model_id)
    if not isinstance(raw, dict):
        return None
    try:
        return OpenRouterPricing(
            pricing_model=model_id,
            fetched_at=str(fetched_at),
            stale=stale,
            input_per_token=_parse_decimal(raw["prompt"]),
            output_per_token=_parse_decimal(raw["completion"]),
            thinking_per_token=_parse_decimal(raw.get("internal_reasoning") or raw["completion"]),
            cached_input_per_token=_parse_decimal(
                raw.get("input_cache_read")
                or raw.get("cache_read")
                or raw.get("cached_input_per_token")
                or "0"
            ),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _fetch_models(
    *,
    opener: Callable[..., Any] | None = None,
    timeout: float = 10.0,
) -> Any:
    opener = opener or request.urlopen
    req = request.Request(
        OPENROUTER_MODELS_URL,
        headers={"Accept": "application/json", "User-Agent": "TweetNook"},
    )
    try:
        response = opener(req, timeout=timeout)
    except TypeError:
        # Small test/fallback fetchers often accept a URL rather than a
        # urllib Request; keep the production request headers above while
        # accommodating that simple callable shape.
        try:
            response = opener(OPENROUTER_MODELS_URL, timeout=timeout)
        except TypeError:
            response = opener(OPENROUTER_MODELS_URL)
    if isinstance(response, dict | list):
        return response
    if isinstance(response, bytes):
        return json.loads(response.decode("utf-8"))
    if isinstance(response, str):
        return json.loads(response)
    if hasattr(response, "__enter__"):
        with response as handle:
            raw = handle.read()
            return json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    raw = response.read()
    return json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)


def _normalize_pricing(pricing: Any) -> dict[str, str] | None:
    if not isinstance(pricing, dict):
        return None
    try:
        prompt = _parse_decimal(pricing["prompt"])
        completion = _parse_decimal(pricing["completion"])
        reasoning = _parse_decimal(pricing.get("internal_reasoning") or completion)
        cached_input_price = _parse_decimal(
            pricing.get("input_cache_read") or pricing.get("cache_read") or "0"
        )
    except (KeyError, TypeError, ValueError):
        return None
    return {
        "prompt": format(prompt, "f"),
        "completion": format(completion, "f"),
        "internal_reasoning": format(reasoning, "f"),
        "input_cache_read": format(cached_input_price, "f"),
    }


def _extract_google_models(payload: Any) -> dict[str, dict[str, str]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return {}
    models: dict[str, dict[str, str]] = {}
    for item in payload["data"]:
        if not isinstance(item, dict):
            continue
        model_id = normalize_model(item.get("id", ""))
        if not model_id.startswith("google/"):
            continue
        normalized = _normalize_pricing(item.get("pricing"))
        if normalized is not None:
            models[model_id] = normalized
    return models


def _extract_model(payload: Any, model_id: str) -> dict[str, str] | None:
    """Return one model's normalized pricing for compatibility with old callers."""

    return _extract_google_models(payload).get(model_id)


def _write_cache(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def load_openrouter_pricing(
    root: Path,
    model: str,
    *,
    now: datetime | None = None,
    opener: Callable[..., Any] | None = None,
    timeout: float = 10.0,
) -> OpenRouterPricing | None:
    """Load a fresh cached price, refreshing the catalogue when necessary.

    A stale model entry is returned if refresh fails.  Network, malformed
    response, and filesystem errors are deliberately converted to ``None`` so
    automated tagging remains a recoverable enrichment step.
    """

    effective_now = (now or datetime.now(UTC)).astimezone(UTC)
    cache_path = pricing_cache_path(root)
    model_id = openrouter_model_id(model)
    cache_payload = _read_cache_payload(cache_path)
    cached = None
    fetched = None
    if cache_payload is not None:
        try:
            fetched = _parse_fetched_at(cache_payload.get("fetched_at"))
        except (TypeError, ValueError):
            pass
        if fetched is not None:
            cached = _pricing_from_cache(
                cache_payload,
                model_id,
                stale=effective_now - fetched >= PRICING_CACHE_TTL,
            )
            if effective_now - fetched < PRICING_CACHE_TTL:
                # ``fetched_at`` describes the complete catalogue snapshot,
                # not just the requested model.  A fresh snapshot therefore
                # also makes an absent model definitively unavailable without
                # another network request.
                return cached
    stale_cached = cached

    refresh_attempted_at = None
    if cache_payload is not None:
        try:
            refresh_attempted_at = _parse_fetched_at(cache_payload.get("refresh_attempted_at"))
        except (TypeError, ValueError):
            pass
    if (
        refresh_attempted_at is not None
        and effective_now - refresh_attempted_at < PRICING_CACHE_TTL
    ):
        return stale_cached

    attempted_at = effective_now.isoformat()
    attempt_payload = dict(cache_payload or {})
    models = attempt_payload.get("models")
    attempt_payload["models"] = dict(models) if isinstance(models, dict) else {}
    attempt_payload["refresh_attempted_at"] = attempted_at
    try:
        # Record the attempt before blocking on the network so failures are
        # throttled even when no new pricing snapshot is available.
        _write_cache(cache_path, attempt_payload)
    except Exception:
        pass
    try:
        payload = _fetch_models(opener=opener, timeout=timeout)
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            return stale_cached
        extracted_models = _extract_google_models(payload)
        if not extracted_models:
            return stale_cached
        fetched_at = effective_now.isoformat()
        refreshed_payload: dict[str, Any] = {
            "fetched_at": fetched_at,
            "refresh_attempted_at": fetched_at,
            "models": extracted_models,
        }
        _write_cache(cache_path, refreshed_payload)
        return _pricing_from_cache(refreshed_payload, model_id, stale=False)
    except Exception:
        return stale_cached


def pricing_multiplier(service_tier: str) -> Decimal:
    return Decimal("0.5") if str(service_tier).casefold() == "flex" else Decimal("1")


def calculate_estimated_cost_usd(
    pricing: OpenRouterPricing,
    *,
    input_tokens: int,
    cached_tokens: int,
    output_tokens: int,
    thinking_tokens: int,
    multiplier: Decimal = Decimal("1"),
    service_tier: str | None = None,
) -> Decimal:
    """Calculate a reproducible Decimal estimate from decomposed usage."""

    if service_tier is not None:
        multiplier = pricing_multiplier(service_tier)
    prompt = max(int(input_tokens), 0)
    cached = max(min(int(cached_tokens), prompt), 0)
    uncached = prompt - cached
    output = max(int(output_tokens), 0)
    thinking = max(int(thinking_tokens), 0)
    return multiplier * (
        Decimal(uncached) * pricing.input_per_token
        + Decimal(cached) * pricing.cached_input_per_token
        + Decimal(output) * pricing.output_per_token
        + Decimal(thinking) * pricing.thinking_per_token
    )


def minimum_input_cost_usd(
    pricing: OpenRouterPricing,
    input_tokens: int,
    *,
    multiplier: Decimal = Decimal("1"),
    service_tier: str | None = None,
) -> Decimal:
    if service_tier is not None:
        multiplier = pricing_multiplier(service_tier)
    return multiplier * Decimal(max(int(input_tokens), 0)) * pricing.input_per_token
