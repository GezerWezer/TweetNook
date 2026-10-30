"""Helpers and fixtures shared by the automated tagging tests."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace
from typing import Any, ClassVar

import httpx
from rich.console import Console

from tweetnook.config import AppConfig, TaggingConfig
from tweetnook.storage.tags import TagsMixin
from tweetnook.tagging import tagger as tagging
from tweetnook.tagging.accounting import (
    AIUsageLedger,
)
from tweetnook.tagging.pricing import OpenRouterPricing


class Store:
    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE archive (
                row_key TEXT PRIMARY KEY, record_type TEXT NOT NULL, tweet_id TEXT,
                media_key TEXT, media_type TEXT, local_path TEXT, raw_json TEXT, source TEXT,
                author_display_name TEXT, author_username TEXT, text TEXT,
                relation_type TEXT, target_tweet_id TEXT, enrichment_state TEXT,
                updated_at TEXT, key TEXT, value TEXT
            )
            """
        )
        self.conn.execute("CREATE INDEX idx_archive_tweet_id ON archive(tweet_id)")

    def add_tweet(self, tweet_id: str, *, text: str = "A post", media: bool = False) -> None:
        self.conn.execute(
            "INSERT INTO archive (row_key, record_type, tweet_id, raw_json, "
            "author_display_name, author_username, text) VALUES (?, 'tweet_object', ?, ?, "
            "'Author', 'handle', ?)",
            (f"tweet_object:{tweet_id}", tweet_id, json.dumps({"legacy": {}}), text),
        )
        if media:
            self.conn.execute(
                "INSERT INTO archive (row_key, record_type, tweet_id, media_key, media_type, "
                "local_path) VALUES (?, 'media', ?, 'm1', 'photo', 'media/photo.jpg')",
                (f"media:{tweet_id}", tweet_id),
            )
        self.conn.commit()

    def get_tag_counts(self, query: str = "", limit: int = 50) -> list[dict[str, Any]]:
        tags = [{"tag": "Deadlock", "count": 9}, {"tag": "Ivy (Deadlock)", "count": 3}]
        return [tag for tag in tags if query.casefold() in tag["tag"].casefold()][:limit]

    def media_tag(self, tweet_id: str):
        return self.conn.execute(
            "SELECT raw_json FROM archive WHERE record_type = 'media_tag' AND tweet_id = ?",
            (tweet_id,),
        ).fetchone()

    def defer_tagging_failure(self, tweet_ids: list[str]) -> None:
        TagsMixin.defer_tagging_failure(self, tweet_ids)


class QueueStore:
    def __init__(self, candidates: list[dict[str, Any]]) -> None:
        self.candidates = candidates

    def get_eligible_tagging_candidates(self, *, limit: int, exclude_tweet_ids: set[str]):
        available = [
            candidate
            for candidate in self.candidates
            if candidate["tweet_id"] not in exclude_tweet_ids
        ]
        if not available:
            return []
        content_type = available[0]["content_type"]
        return [item for item in available if item["content_type"] == content_type][:limit]

    def get_tag_counts(self, *, limit: int = 50):
        return []


def config(**updates: Any) -> AppConfig:
    values: dict[str, Any] = {
        "enabled": True,
        "api_key": "secret",
        "model": "gemini-3.6-flash",
        "free_rpm": 10_000,
        "free_rpd": 1_000,
    }
    values.update(updates)
    return AppConfig(tagging=TaggingConfig(**values))


def console() -> tuple[Console, StringIO]:
    output = StringIO()
    return Console(file=output, color_system=None, width=240), output


def ledger_records(ledger: AIUsageLedger) -> list[dict[str, Any]]:
    files = sorted(ledger.root.glob("*.jsonl"))
    assert len(files) == 1
    return [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]


class FakeTypes:
    class ThinkingConfig:
        model_fields: ClassVar = {"thinking_level": object()}

        def __init__(self, **values: Any) -> None:
            self.__dict__.update(values)

    class HttpRetryOptions:
        def __init__(self, **values: Any) -> None:
            self.__dict__.update(values)

    class HttpOptions:
        def __init__(self, **values: Any) -> None:
            self.__dict__.update(values)

    class GenerateContentConfig:
        def __init__(self, **values: Any) -> None:
            self.__dict__.update(values)

    class CountTokensConfig:
        def __init__(self, **values: Any) -> None:
            self.__dict__.update(values)

    class GoogleSearch:
        def __init__(self, **values: Any) -> None:
            self.__dict__.update(values)

    class Tool:
        def __init__(self, **values: Any) -> None:
            self.__dict__.update(values)


def free_response(results: list[dict[str, Any]]) -> Any:
    return SimpleNamespace(
        text=json.dumps(results),
        model_version="gemini-3.6-flash-001",
        usage_metadata=SimpleNamespace(
            prompt_token_count=100,
            cached_content_token_count=0,
            candidates_token_count=20,
            thoughts_token_count=5,
        ),
    )


def paid_response(
    result: dict[str, Any],
    *,
    interaction_id: str = "interaction-1",
    searches: int | None = 0,
    steps: list[Any] | None = None,
) -> Any:
    return SimpleNamespace(
        id=interaction_id,
        output_text=json.dumps(result),
        model="gemini-3.6-flash-001",
        service_tier="flex",
        usage=SimpleNamespace(
            total_input_tokens=100,
            total_cached_tokens=0,
            total_output_tokens=20,
            total_thought_tokens=5,
            grounding_tool_count=(
                None
                if searches is None
                else [SimpleNamespace(type="google_search", count=searches)]
            ),
        ),
        steps=steps or [],
    )


class FakeModels:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.generate_calls: list[dict[str, Any]] = []
        self.count_calls: list[dict[str, Any]] = []

    def generate_content(self, **kwargs: Any) -> Any:
        self.generate_calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    def count_tokens(self, **kwargs: Any) -> Any:
        self.count_calls.append(kwargs)
        if getattr(kwargs.get("config"), "system_instruction", None) is not None:
            raise ValueError("system_instruction is unsupported for Developer API countTokens")
        return SimpleNamespace(total_tokens=100)


class FakeInteractions:
    def __init__(self, responses: list[Any], *, delay: float = 0) -> None:
        self.responses = list(responses)
        self.delay = delay
        self.calls: list[dict[str, Any]] = []
        self.active = 0
        self.peak = 0

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        self.active += 1
        self.peak = max(self.peak, self.active)
        if self.delay:
            await asyncio.sleep(self.delay)
        self.active -= 1
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class FakeFiles:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.uploaded: list[str] = []
        self.unrelated = SimpleNamespace(name="unrelated-file", uri="files/unrelated")

    def upload(self, *, file: str) -> Any:
        self.uploaded.append(file)
        return SimpleNamespace(
            name=f"owned-{len(self.uploaded)}",
            uri=f"files/owned-{len(self.uploaded)}",
            mime_type="image/jpeg",
            state=SimpleNamespace(name="ACTIVE"),
        )

    def get(self, *, name: str) -> Any:
        return SimpleNamespace(
            name=name,
            uri=f"files/{name}",
            mime_type="image/jpeg",
            state=SimpleNamespace(name="ACTIVE"),
        )

    def delete(self, *, name: str) -> None:
        self.deleted.append(name)


def install_fake_client(monkeypatch, *, models: FakeModels, interactions: FakeInteractions):
    async def close_async():
        client.async_closed = True

    files = FakeFiles()
    client = SimpleNamespace(
        models=models,
        interactions=interactions,
        aio=SimpleNamespace(interactions=interactions, aclose=close_async),
        async_closed=False,
        close=lambda: None,
        files=files,
    )
    monkeypatch.setattr(tagging, "types", FakeTypes)
    monkeypatch.setattr(tagging, "genai", SimpleNamespace(Client=lambda **_kwargs: client))
    monkeypatch.setattr(tagging, "Image", SimpleNamespace())
    monkeypatch.setattr(tagging, "_ensure_optional_dependencies", lambda: True)
    return client, files


def allow_free_quota(monkeypatch) -> tuple[list[int], list[int]]:
    rpm: list[int] = []
    rpd: list[int] = []

    async def fake_wait(_model: str, limit: int) -> None:
        rpm.append(limit)

    def fake_status(_store, *, model: str, limit: int):
        return SimpleNamespace(allowed=True, used=0, limit=limit, model=model)

    def fake_reserve(_store, *, model: str, limit: int):
        rpd.append(limit)
        return SimpleNamespace(allowed=True, used=1, limit=limit, model=model)

    monkeypatch.setattr(tagging, "_wait_for_free_rpm", fake_wait)
    monkeypatch.setattr(tagging, "get_rpd_status", fake_status)
    monkeypatch.setattr(tagging, "reserve_rpd_request", fake_reserve)
    return rpm, rpd


def install_paid_pricing(monkeypatch) -> None:
    pricing = OpenRouterPricing(
        pricing_model="google/gemini-3.6-flash",
        fetched_at=datetime.now(UTC).isoformat(),
        stale=False,
        input_per_token=Decimal("0.000001"),
        output_per_token=Decimal("0.000002"),
        thinking_per_token=Decimal("0.000002"),
        cached_input_per_token=Decimal("0.0000001"),
    )
    monkeypatch.setattr(tagging, "load_openrouter_pricing", lambda *_args: pricing)


def paid_queue_store(count: int = 3) -> Store:
    store = Store()
    for index in range(1, count + 1):
        store.add_tweet(str(index))
    queue = QueueStore(
        [
            {"tweet_id": str(index), "content_type": "text", "quoted_tweet_id": None, "sort_ts": 0}
            for index in range(1, count + 1)
        ]
    )
    store.get_eligible_tagging_candidates = queue.get_eligible_tagging_candidates
    return store


def provider_failure(code: int | str | None) -> Exception:
    if code is None:
        return TimeoutError()
    if code == "http_timeout":
        return httpx.ReadTimeout("")
    error = RuntimeError("Provider busy")
    error.status_code = code
    return error
