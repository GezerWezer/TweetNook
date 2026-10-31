"""Free reads a provider's numeric status first, and message text only when it has none."""

from __future__ import annotations

import asyncio

import pytest

from tests.automated_tagging.conftest import (
    FakeInteractions,
    FakeModels,
    Store,
    allow_free_quota,
    config,
    console,
    free_response,
    install_fake_client,
)
from tweetnook.tagging import tagger as tagging


def failure(message: str, code: int | None = None) -> Exception:
    error = RuntimeError(message)
    if code is not None:
        error.status_code = code
    return error


async def run_one(monkeypatch, paths, responses: list) -> tuple[int, FakeModels, Store]:
    store = Store()
    store.add_tweet("1")
    models = FakeModels(responses)
    install_fake_client(monkeypatch, models=models, interactions=FakeInteractions([]))
    allow_free_quota(monkeypatch)
    original_sleep = asyncio.sleep
    monkeypatch.setattr(tagging.asyncio, "sleep", lambda _delay: original_sleep(0))
    tagged = await tagging.tag_media_tweets(
        store, config(), paths, console()[0], ["1"], content_type="text"
    )
    return tagged, models, store


GOOD = free_response([{"id": "1", "tags": ["One", "Two"]}])


@pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
async def test_numeric_busy_status_with_ordinary_text_retries(monkeypatch, paths, code):
    tagged, models, _store = await run_one(
        monkeypatch, paths, [failure("Provider busy", code), GOOD]
    )
    assert tagged == 1
    assert len(models.generate_calls) == 2


async def test_status_outranks_a_message_that_reads_as_busy(monkeypatch, paths):
    tagged, models, _store = await run_one(
        monkeypatch, paths, [failure("Tweet 1429 is 503 words", 403), GOOD]
    )
    assert tagged == 0
    assert len(models.generate_calls) == 1


async def test_status_outranks_a_message_that_reads_as_a_rejection(monkeypatch, paths):
    tagged, models, store = await run_one(
        monkeypatch, paths, [failure("Row 400 was INVALID_ARGUMENT", 503), GOOD]
    )
    assert tagged == 1
    assert len(models.generate_calls) == 2
    assert store.media_tag("1") is not None


async def test_numeric_rejection_with_ordinary_text_is_not_retried(monkeypatch, paths):
    tagged, models, store = await run_one(monkeypatch, paths, [failure("Bad request", 400), GOOD])
    assert tagged == 0
    assert len(models.generate_calls) == 1
    assert store.media_tag("1")["raw_json"] == "{}"


@pytest.mark.parametrize("message", ["429 RESOURCE_EXHAUSTED", "503 overloaded"])
async def test_an_error_without_a_status_still_falls_back_to_its_text(monkeypatch, paths, message):
    tagged, models, _store = await run_one(monkeypatch, paths, [failure(message), GOOD])
    assert tagged == 1
    assert len(models.generate_calls) == 2


async def test_ordinary_error_without_a_status_is_not_retried(monkeypatch, paths):
    tagged, models, _store = await run_one(monkeypatch, paths, [failure("Provider busy"), GOOD])
    assert tagged == 0
    assert len(models.generate_calls) == 1
