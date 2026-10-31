"""FxTwitter API v2 lookups: cookie-free single-post checks.

FxTwitter (https://docs.fxembed.com) fetches posts from X through its own accounts, so
lookups here never touch the user's session or its rate limits. Responses are not X's
GraphQL; ``core.fxtwitter_payloads`` rebuilds the subset of a GraphQL tweet result that the
extractor and the Web UI read, so the existing persistence path can store them.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from tweetnook.config import AppConfig

FxLookupKind = Literal["available", "tombstone", "not_found", "protected", "error"]

FX_USER_AGENT = "tweetnook (+https://github.com/gezerwezer/tweetnook)"
FX_RETRY_ATTEMPTS = 4
# This many failed lookups in a row (each already retried) means FxTwitter is down.
FX_OUTAGE_ERRORS = 8
# A Retry-After longer than this means FxTwitter is down for a while, not throttling.
FX_MAX_RETRY_AFTER_SECONDS = 60.0


@dataclass(slots=True)
class FxLookup:
    """One ``/2/status/{id}`` outcome.

    ``tombstone`` carries FxTwitter's reason (deleted, suspended, private, blocked,
    unavailable); ``not_found`` is a 404 without one; ``protected`` is a 401, which
    FxTwitter returns for every protected account, including ones the user follows.
    """

    tweet_id: str
    kind: FxLookupKind
    http_status: int | None
    status: dict[str, Any] | None = None
    reason: str | None = None
    detail: str | None = None
    payload: Any = None


def fx_enabled(config: AppConfig) -> bool:
    # Replay installs must never reach a host other than their replay source.
    return config.fxtwitter.enabled and not config.replay.url


def build_fx_client(
    config: AppConfig,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=config.fxtwitter.base_url.rstrip("/"),
        headers={"user-agent": FX_USER_AGENT, "accept": "application/json"},
        timeout=config.fxtwitter.timeout,
        follow_redirects=True,
        transport=transport,
    )


def _retry_after(response: httpx.Response, attempt: int) -> float:
    header = response.headers.get("retry-after")
    try:
        delay = float(header) if header else float(2**attempt)
    except ValueError:
        delay = float(2**attempt)
    if not math.isfinite(delay):
        delay = float(2**attempt)
    return min(max(delay, 1.0), FX_MAX_RETRY_AFTER_SECONDS)


def classify_fx_response(tweet_id: str, http_status: int, payload: Any) -> FxLookup:
    status = payload.get("status") if isinstance(payload, dict) else None
    status = status if isinstance(status, dict) else None
    status_type = status.get("type") if status else None
    if http_status == 200 and status_type == "status" and status.get("id") == tweet_id:
        return FxLookup(tweet_id, "available", http_status, status=status, payload=payload)
    if status_type == "tombstone":
        reason = str(status.get("reason") or "unavailable")
        return FxLookup(
            tweet_id,
            "tombstone",
            http_status,
            status=status,
            reason=reason,
            detail=status.get("message") or f"FxTwitter tombstone: {reason}",
            payload=payload,
        )
    message = payload.get("message") if isinstance(payload, dict) else None
    if http_status == 404:
        return FxLookup(tweet_id, "not_found", http_status, detail=message, payload=payload)
    if http_status == 401:
        return FxLookup(tweet_id, "protected", http_status, detail=message, payload=payload)
    return FxLookup(
        tweet_id,
        "error",
        http_status,
        detail=message or f"FxTwitter returned HTTP {http_status}",
        payload=payload,
    )


async def fetch_fx_status(
    client: httpx.AsyncClient,
    tweet_id: str,
    *,
    attempts: int = FX_RETRY_ATTEMPTS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> FxLookup:
    """Look up one post, retrying rate limits, server errors, and transport failures."""

    last: FxLookup | None = None
    for attempt in range(attempts):
        try:
            response = await client.get(f"/2/status/{tweet_id}")
        except httpx.TransportError as exc:
            last = FxLookup(tweet_id, "error", None, detail=f"{exc.__class__.__name__}: {exc}")
            if attempt + 1 < attempts:
                await sleep(float(2**attempt))
            continue
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if response.status_code == 429 or response.status_code >= 500:
            last = classify_fx_response(tweet_id, response.status_code, payload)
            if attempt + 1 < attempts:
                await sleep(_retry_after(response, attempt))
            continue
        return classify_fx_response(tweet_id, response.status_code, payload)
    assert last is not None
    return last


async def iter_fx_statuses(
    client: httpx.AsyncClient,
    tweet_ids: Iterable[str],
    *,
    concurrency: int,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> AsyncIterator[FxLookup]:
    """Yield lookups as each finishes, with at most ``concurrency`` requests in flight."""

    semaphore = asyncio.Semaphore(max(concurrency, 1))

    async def one(tweet_id: str) -> FxLookup:
        async with semaphore:
            return await fetch_fx_status(client, tweet_id, sleep=sleep)

    tasks = [asyncio.ensure_future(one(tweet_id)) for tweet_id in dict.fromkeys(tweet_ids)]
    try:
        for finished in asyncio.as_completed(tasks):
            yield await finished
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def fetch_fx_statuses(
    client: httpx.AsyncClient,
    tweet_ids: Iterable[str],
    *,
    concurrency: int,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    on_lookup: Callable[[FxLookup], None] | None = None,
) -> tuple[dict[str, FxLookup], bool]:
    """Look up several posts with at most ``concurrency`` requests in flight.

    Returns the lookups and whether FxTwitter looked down: after ``FX_OUTAGE_ERRORS``
    failures in a row the rest are dropped rather than waited out. ``on_lookup`` sees
    each lookup as it finishes, so callers can show progress during the batch.
    """

    results: dict[str, FxLookup] = {}
    errors = 0
    lookups = iter_fx_statuses(client, tweet_ids, concurrency=concurrency, sleep=sleep)
    async with aclosing(lookups):
        async for lookup in lookups:
            results[lookup.tweet_id] = lookup
            if on_lookup is not None:
                on_lookup(lookup)
            errors = errors + 1 if lookup.kind == "error" else 0
            if errors >= FX_OUTAGE_ERRORS:
                return results, True
    return results, False
