from __future__ import annotations

import httpx
import pytest

from tweetnook.client import base, fxtwitter
from tweetnook.config import SyncConfig


async def test_adaptive_pacing_expires_at_reset_and_retains_valid_headerless_sample(monkeypatch):
    now = 100.0
    monkeypatch.setattr(base.time, "time", lambda: now)
    pacer = base.AdaptiveRequestPacer(base_delay=1.0)
    delays = []
    messages = []

    async def sleep(delay):
        delays.append(delay)

    def observe(headers):
        pacer.observe(httpx.Response(200, headers=headers), status=messages.append)

    observe({"x-rate-limit-remaining": "1", "x-rate-limit-reset": "109"})
    await pacer.wait(attempted=0, sleep=sleep)
    await pacer.wait(attempted=1, sleep=sleep)
    now = 105.0
    observe({})
    await pacer.wait(attempted=2, sleep=sleep)
    now = 109.0
    await pacer.wait(attempted=3, sleep=sleep)
    observe({"x-rate-limit-remaining": "1", "x-rate-limit-reset": "108"})
    await pacer.wait(attempted=4, sleep=sleep)
    observe({})
    await pacer.wait(attempted=5, sleep=sleep)
    observe({"x-rate-limit-remaining": "2", "x-rate-limit-reset": "114"})
    await pacer.wait(attempted=6, sleep=sleep)
    observe({"x-rate-limit-remaining": "2", "x-rate-limit-reset": "114"})
    assert delays == [10.0, 10.0, 1.0, 1.0, 1.0, 3.0]
    assert len(messages) == 2


async def test_observing_expired_headers_clears_an_old_pacing_sample(monkeypatch):
    now = 100.0
    monkeypatch.setattr(base.time, "time", lambda: now)
    pacer = base.AdaptiveRequestPacer(base_delay=2.0)
    pacer.observe(
        httpx.Response(200, headers={"x-rate-limit-remaining": "1", "x-rate-limit-reset": "109"})
    )
    now = 111.0
    pacer.observe(
        httpx.Response(200, headers={"x-rate-limit-remaining": "1", "x-rate-limit-reset": "109"})
    )
    delays = []

    async def sleep(delay):
        delays.append(delay)

    await pacer.wait(attempted=1, sleep=sleep)
    assert delays == [2.0]


@pytest.mark.parametrize("status_enabled", [False, True])
@pytest.mark.parametrize(
    "headers",
    [
        {"retry-after": "inf"},
        {"retry-after": "-inf"},
        {"retry-after": "nan"},
        {"x-rate-limit-reset": "999999999999999999999"},
        {"x-rate-limit-reset": "-999999999999999999999"},
    ],
)
async def test_invalid_rate_limit_timing_uses_backoff(headers, status_enabled):
    responses = iter([httpx.Response(429, headers=headers), httpx.Response(200)])
    delays = []
    messages = []

    async def sleep(delay):
        delays.append(delay)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: next(responses))
    ) as client:
        response = await base.request_with_backoff(
            client,
            "https://x.test",
            SyncConfig(backoff_base=2),
            status=messages.append if status_enabled else None,
            sleep=sleep,
        )
    assert response.status_code == 200
    assert delays == [2.0]
    assert len(messages) == int(status_enabled)


@pytest.mark.parametrize("header", ["nan", "inf", "-inf", "bad"])
async def test_fx_invalid_retry_after_uses_exponential_backoff(header):
    responses = iter(
        [httpx.Response(429, headers={"retry-after": header}, json={})] * 3
        + [httpx.Response(404, json={})]
    )
    delays = []

    async def sleep(delay):
        delays.append(delay)

    async with httpx.AsyncClient(
        base_url="https://fx.test",
        transport=httpx.MockTransport(lambda request: next(responses)),
    ) as client:
        result = await fxtwitter.fetch_fx_status(client, "123", sleep=sleep)
    assert result.kind == "not_found"
    assert delays == [1.0, 2.0, 4.0]


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"x-rate-limit-reset": "1700000005000"}, 6.0),
        ({"retry-after": "Tue, 14 Nov 2023 22:13:25 GMT"}, 5.0),
        ({"retry-after": "3.5"}, 3.5),
    ],
)
def test_valid_rate_limit_timing_forms_remain_supported(headers, expected):
    info = base.get_rate_limit_info(httpx.Response(429, headers=headers), now=1700000000)
    assert info is not None
    assert base.compute_rate_limit_wait(info, now=1700000000) == expected


def test_rate_limit_status_can_format_an_unusable_reset_without_aborting():
    summary = base.format_rate_limit_info(base.RateLimitInfo(remaining=0, reset_at=10**30))
    assert summary == "remaining 0"
