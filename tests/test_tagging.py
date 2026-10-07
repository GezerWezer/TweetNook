from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from rich.console import Console

from tweetnook import tagging
from tweetnook.config import AppConfig, TaggingConfig
from tweetnook.gemini_accounting import (
    AIUsageLedger,
    UsageRecord,
    failed_usage_record,
    usage_from_interaction,
)
from tweetnook.gemini_pricing import OpenRouterPricing
from tweetnook.pipeline import PipelineReporter


class Store:
    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE archive (
                row_key TEXT PRIMARY KEY, record_type TEXT NOT NULL, tweet_id TEXT,
                media_key TEXT, media_type TEXT, local_path TEXT, raw_json TEXT,
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
    files = FakeFiles()
    client = SimpleNamespace(
        models=models,
        interactions=interactions,
        aio=SimpleNamespace(interactions=interactions),
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


def test_structured_models_keep_distinct_text_and_media_schemas() -> None:
    result = tagging.MediaTagResult(
        description="A concise description.", tags=["Deadlock", "Ivy (Deadlock)"]
    )
    assert result.description == "A concise description."
    assert "description" not in tagging.TextTagResult.model_json_schema()["properties"]
    with pytest.raises(ValueError):
        tagging.MediaTagResult(description="x", tags=["only one"])


@pytest.mark.asyncio
async def test_missing_optional_extra_is_a_recoverable_noop(monkeypatch, paths) -> None:
    monkeypatch.setattr(tagging, "_ensure_optional_dependencies", lambda: False)
    out, stream = console()
    result = await tagging.tag_pending_media_tweets(
        QueueStore([{"tweet_id": "1", "content_type": "text"}]), config(), paths, out
    )
    assert result == tagging.TaggingRunResult()
    assert "not installed" in stream.getvalue()


@pytest.mark.asyncio
async def test_free_batches_multiple_text_tweets_with_configured_limits(monkeypatch, paths) -> None:
    store = Store()
    store.add_tweet("1", text="First")
    store.add_tweet("2", text="Second")
    models = FakeModels(
        [
            free_response(
                [
                    {"id": "1", "tags": ["Deadlock", "Ivy (Deadlock)"]},
                    {"id": "2", "tags": ["Linux", "Wayland"]},
                ]
            )
        ]
    )
    client, _files = install_fake_client(
        monkeypatch, models=models, interactions=FakeInteractions([])
    )
    rpm, rpd = allow_free_quota(monkeypatch)

    tagged = await tagging.tag_media_tweets(
        store,
        config(free_rpm=7, free_rpd=99, free_batch_size=20),
        paths,
        console()[0],
        ["1", "2"],
        content_type="text",
    )

    assert tagged == 2
    assert len(client.models.generate_calls) == 1
    assert rpm == [7]
    assert rpd == [99]
    prompt = client.models.generate_calls[0]["contents"][0]
    assert "**Tweet Isolation:**" in prompt
    assert any("[ID: 1]" in str(part) for part in client.models.generate_calls[0]["contents"])
    assert "Existing tags:\n- Deadlock\n- Ivy (Deadlock)" in prompt
    assert client.models.generate_calls[0]["model"] == "gemini-3.6-flash"
    assert (
        client.models.generate_calls[0]["config"].response_schema
        == list[tagging._FreeTextTagResult]
    )


@pytest.mark.asyncio
async def test_free_refreshes_existing_tags_between_batches(monkeypatch, paths) -> None:
    store = Store()
    for tweet_id in ("1", "2", "3", "4"):
        store.add_tweet(tweet_id)
    candidates = [
        {"tweet_id": tweet_id, "content_type": "text"} for tweet_id in ("1", "2", "3", "4")
    ]

    def select_candidates(*, limit: int, exclude_tweet_ids: set[str]):
        return [
            candidate for candidate in candidates if candidate["tweet_id"] not in exclude_tweet_ids
        ][:limit]

    store.get_eligible_tagging_candidates = select_candidates  # type: ignore[method-assign]
    snapshots: list[tuple[str, ...]] = []

    def top_tags(current_store) -> list[str]:
        values = ["Deadlock"]
        if current_store.media_tag("1") is not None:
            values.append("Shiv (Deadlock)")
        snapshots.append(tuple(values))
        return values

    monkeypatch.setattr(tagging, "_top_archive_tags", top_tags)
    models = FakeModels(
        [
            free_response(
                [
                    {"id": "1", "tags": ["Shiv (Deadlock)", "Deadlock"]},
                    {"id": "2", "tags": ["Shiv (Deadlock)", "Deadlock"]},
                ]
            ),
            free_response(
                [
                    {"id": "3", "tags": ["Ivy (Deadlock)", "Deadlock"]},
                    {"id": "4", "tags": ["Ivy (Deadlock)", "Deadlock"]},
                ]
            ),
        ]
    )
    client, _files = install_fake_client(
        monkeypatch, models=models, interactions=FakeInteractions([])
    )
    allow_free_quota(monkeypatch)

    result = await tagging.tag_pending_media_tweets(
        store,
        config(free_batch_size=2),
        paths,
        console()[0],
        batch_limit=2,
    )

    assert result == tagging.TaggingRunResult(processed=4, tagged=4, batches=2)
    assert snapshots == [("Deadlock",), ("Deadlock", "Shiv (Deadlock)")]
    assert "Shiv (Deadlock)" in client.models.generate_calls[1]["contents"][0]


@pytest.mark.asyncio
async def test_paid_refreshes_existing_tags_once_per_concurrency_wave(monkeypatch, paths) -> None:
    store = Store()
    for tweet_id in ("A", "B", "C", "D"):
        store.add_tweet(tweet_id)
    candidates = [
        {"tweet_id": tweet_id, "content_type": "text"} for tweet_id in ("A", "B", "C", "D")
    ]

    def select_candidates(*, limit: int, exclude_tweet_ids: set[str]):
        return [
            candidate for candidate in candidates if candidate["tweet_id"] not in exclude_tweet_ids
        ][:limit]

    store.get_eligible_tagging_candidates = select_candidates  # type: ignore[method-assign]
    snapshots: list[tuple[str, tuple[str, ...]]] = []

    def top_tags(current_store) -> list[str]:
        values = ["Deadlock"]
        if current_store.media_tag("A") is not None:
            values.append("Shiv (Deadlock)")
        return values

    monkeypatch.setattr(tagging, "_top_archive_tags", top_tags)
    monkeypatch.setattr(tagging, "_ensure_optional_dependencies", lambda: True)

    async def fake_tag(**kwargs: Any) -> int:
        tweet_id = kwargs["tweet_ids"][0]
        kwargs["_paid_state"].attempts += 1
        snapshots.append((tweet_id, tuple(kwargs["_existing_tags"])))
        await asyncio.sleep(0)
        store.conn.execute(
            "INSERT OR REPLACE INTO archive "
            "(row_key, record_type, tweet_id, raw_json, enrichment_state, updated_at) "
            "VALUES (?, 'media_tag', ?, ?, 'done', 'now')",
            (
                f"media_tag:{tweet_id}",
                tweet_id,
                json.dumps({"tags": ["Shiv (Deadlock)", "Deadlock"]}),
            ),
        )
        store.conn.commit()
        return 1

    monkeypatch.setattr(tagging, "tag_media_tweets", fake_tag)
    result = await tagging.tag_pending_media_tweets(
        store,
        config(api_mode="paid", unlimited_spend=True),
        paths,
        console()[0],
        batch_limit=4,
    )

    assert result == tagging.TaggingRunResult(processed=4, tagged=4, batches=4)
    assert snapshots == [
        ("A", ("Deadlock",)),
        ("B", ("Deadlock", "Shiv (Deadlock)")),
        ("C", ("Deadlock", "Shiv (Deadlock)")),
        ("D", ("Deadlock", "Shiv (Deadlock)")),
    ]


@pytest.mark.asyncio
async def test_failed_automated_tag_remains_retryable_until_success(monkeypatch, paths) -> None:
    store = Store()
    store.add_tweet("1")

    def select_candidates(*, limit: int, exclude_tweet_ids: set[str]):
        if "1" in exclude_tweet_ids:
            return []
        row = store.media_tag("1")
        if row is not None:
            state = store.conn.execute(
                "SELECT enrichment_state FROM archive "
                "WHERE record_type = 'media_tag' AND tweet_id = '1'"
            ).fetchone()[0]
            if state == "done" and json.loads(row["raw_json"]).get("tags"):
                return []
        return [{"tweet_id": "1", "content_type": "text"}]

    store.get_eligible_tagging_candidates = select_candidates  # type: ignore[method-assign]
    models = FakeModels(
        [
            SimpleNamespace(text="", usage_metadata=None),
            free_response([{"id": "1", "tags": ["One", "Two"]}]),
        ]
    )
    install_fake_client(monkeypatch, models=models, interactions=FakeInteractions([]))
    allow_free_quota(monkeypatch)

    first = await tagging.tag_pending_media_tweets(
        store, config(), paths, console()[0], batch_limit=1
    )
    assert first == tagging.TaggingRunResult(processed=1, tagged=0, batches=1)
    failed_state = store.conn.execute(
        "SELECT enrichment_state FROM archive WHERE record_type = 'media_tag' AND tweet_id = '1'"
    ).fetchone()[0]
    assert failed_state == "failed"

    second = await tagging.tag_pending_media_tweets(
        store, config(), paths, console()[0], batch_limit=1
    )
    assert second == tagging.TaggingRunResult(processed=1, tagged=1, batches=1)
    assert store.media_tag("1") is not None
    assert (
        store.conn.execute(
            "SELECT enrichment_state FROM archive "
            "WHERE record_type = 'media_tag' AND tweet_id = '1'"
        ).fetchone()[0]
        == "done"
    )


@pytest.mark.asyncio
async def test_free_does_not_enable_google_search(monkeypatch, paths) -> None:
    store = Store()
    store.add_tweet("1")
    models = FakeModels([free_response([{"id": "1", "tags": ["One", "Two"]}])])
    install_fake_client(monkeypatch, models=models, interactions=FakeInteractions([]))
    allow_free_quota(monkeypatch)

    await tagging.tag_media_tweets(
        store, config(google_search=True), paths, console()[0], ["1"], content_type="text"
    )

    request = models.generate_calls[0]
    assert "Search and Verification" not in request["contents"][0]
    assert not hasattr(request["config"], "tools")


@pytest.mark.asyncio
async def test_rejected_free_batch_splits_and_retries_smaller_batches(monkeypatch, paths) -> None:
    store = Store()
    for tweet_id in ("1", "2", "3", "4"):
        store.add_tweet(tweet_id)
    models = FakeModels(
        [
            RuntimeError("400 INVALID_ARGUMENT"),
            free_response(
                [
                    {"id": "1", "tags": ["One", "Two"]},
                    {"id": "2", "tags": ["Three", "Four"]},
                ]
            ),
            free_response(
                [
                    {"id": "3", "tags": ["Five", "Six"]},
                    {"id": "4", "tags": ["Seven", "Eight"]},
                ]
            ),
        ]
    )
    install_fake_client(monkeypatch, models=models, interactions=FakeInteractions([]))
    _rpm, rpd = allow_free_quota(monkeypatch)

    tagged = await tagging.tag_media_tweets(
        store, config(), paths, console()[0], ["1", "2", "3", "4"], content_type="text"
    )

    assert tagged == 4
    assert len(models.generate_calls) == 3
    assert len(rpd) == 3


@pytest.mark.asyncio
async def test_paid_uses_one_independent_interaction_per_tweet_sequentially(
    monkeypatch, paths
) -> None:
    monkeypatch.setattr(tagging, "_ensure_optional_dependencies", lambda: True)
    active = peak = 0
    calls: list[list[str]] = []
    lock = asyncio.Lock()

    async def fake_tag(**kwargs: Any) -> int:
        nonlocal active, peak
        kwargs["_paid_state"].attempts += 1
        calls.append(kwargs["tweet_ids"])
        async with lock:
            active += 1
            peak = max(peak, active)
        await asyncio.sleep(0.01)
        async with lock:
            active -= 1
        return 1

    monkeypatch.setattr(tagging, "tag_media_tweets", fake_tag)
    store = QueueStore([{"tweet_id": str(index), "content_type": "text"} for index in range(4)])
    result = await tagging.tag_pending_media_tweets(
        store,
        config(api_mode="paid", unlimited_spend=True),
        paths,
        console()[0],
        batch_limit=4,
    )
    assert result == tagging.TaggingRunResult(processed=4, tagged=4, batches=4)
    assert peak == 1
    assert sorted(calls) == [["0"], ["1"], ["2"], ["3"]]


@pytest.mark.asyncio
async def test_paid_engine_dispatches_independent_interactions_sequentially(
    monkeypatch, paths
) -> None:
    store = Store()
    for tweet_id in ("1", "2", "3", "4"):
        store.add_tweet(tweet_id)
    interactions = FakeInteractions(
        [
            paid_response(
                {"tags": [f"Specific {index}", f"Entity {index}"]},
                interaction_id=f"interaction-{index}",
            )
            for index in range(4)
        ],
        delay=0.02,
    )
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=interactions)
    tagged = await tagging.tag_media_tweets(
        store,
        config(api_mode="paid", unlimited_spend=True),
        paths,
        console()[0],
        ["1", "2", "3", "4"],
        content_type="text",
        dry_run=False,
    )

    assert tagged == 4
    assert interactions.peak == 1
    assert len(interactions.calls) == 4
    assert all(len(call["input"]) == 1 for call in interactions.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("tier", ["standard", "flex"])
async def test_paid_interaction_uses_model_schema_search_and_service_tier(
    monkeypatch, paths, tier
) -> None:
    store = Store()
    store.add_tweet("1", text="A recent release")
    interactions = FakeInteractions([paid_response({"tags": ["Specific One", "Specific Two"]})])
    models = FakeModels([])
    install_fake_client(monkeypatch, models=models, interactions=interactions)

    tagged = await tagging.tag_media_tweets(
        store,
        config(
            api_mode="paid",
            unlimited_spend=True,
            processing_tier=tier,
            google_search=True,
        ),
        paths,
        console()[0],
        ["1"],
        content_type="text",
        dry_run=True,
    )

    assert tagged == 1
    request = interactions.calls[0]
    assert request["model"] == "gemini-3.6-flash"
    assert request["service_tier"] == tier
    assert request["tools"] == [{"type": "google_search"}]
    assert len(models.count_calls) == 1
    assert models.count_calls[0]["model"] == "gemini-3.6-flash"
    assert not hasattr(models.count_calls[0]["config"], "tools")
    usage_path = next(paths.ai_usage_dir.glob("????-??.jsonl"))
    usage = json.loads(usage_path.read_text(encoding="utf-8").splitlines()[0])
    assert usage["estimate"]["input_tokens"] == 100
    assert usage["actual"]["input_tokens"] == 100
    assert request["store"] is False
    assert request["response_format"]["schema"] == tagging.TextTagResult.model_json_schema()
    assert "description" not in request["response_format"]["schema"]["properties"]
    assert all(
        "ID: 1" not in str(part.get("text", ""))
        for part in request["input"]
        if isinstance(part, dict)
    )
    assert "Search and Verification" in request["system_instruction"]
    assert "Tweet Isolation" not in request["system_instruction"]
    if tier == "flex":
        assert request["timeout"] == 900.0
    else:
        assert "timeout" not in request


@pytest.mark.asyncio
async def test_paid_count_tokens_omits_search_tool_when_search_is_disabled(
    monkeypatch, paths
) -> None:
    store = Store()
    store.add_tweet("1")
    interactions = FakeInteractions([paid_response({"tags": ["One", "Two"]})])
    models = FakeModels([])
    install_fake_client(monkeypatch, models=models, interactions=interactions)
    monkeypatch.setattr(tagging, "load_openrouter_pricing", lambda *_args: None)

    tagged = await tagging.tag_media_tweets(
        store,
        config(api_mode="paid", unlimited_spend=True, google_search=False),
        paths,
        console()[0],
        ["1"],
        content_type="text",
        dry_run=True,
    )

    assert tagged == 1
    assert "tools" not in interactions.calls[0]
    assert not hasattr(models.count_calls[0]["config"], "tools")


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
    monkeypatch.setattr(tagging, "load_openrouter_pricing", lambda *_args: None)

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
    monkeypatch.setattr(tagging, "load_openrouter_pricing", lambda *_args: None)

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
    monkeypatch.setattr(tagging, "load_openrouter_pricing", lambda *_args: None)

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
    policy = tagging._SearchPolicy(AIUsageLedger(paths.ai_usage_dir), reserve=100, enabled=True)

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
    monkeypatch.setattr(tagging, "load_openrouter_pricing", lambda *_args: None)
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
    assert records[0]["search_reservation_consumed"] == tagging.SEARCH_ATTEMPT_SAFETY_RESERVATION
    assert ledger.monthly_search_count() == tagging.SEARCH_ATTEMPT_SAFETY_RESERVATION


@pytest.mark.asyncio
async def test_monthly_search_reserve_prevents_intentional_allowance_crossing(
    monkeypatch, paths
) -> None:
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


@pytest.mark.asyncio
async def test_optional_prompt_sections_and_existing_tags_are_sent_directly(
    monkeypatch, paths
) -> None:
    store = Store()
    store.add_tweet("1", text="A Deadlock update")
    models = FakeModels([free_response([{"id": "1", "tags": ["deadlock", "Ivy (Deadlock)"]}])])
    install_fake_client(monkeypatch, models=models, interactions=FakeInteractions([]))
    allow_free_quota(monkeypatch)

    tagged = await tagging.tag_media_tweets(
        store,
        config(
            tagging_context=["Deadlock"],
            additional_instructions="Prefer canonical capitalization.",
        ),
        paths,
        console()[0],
        ["1"],
        content_type="text",
    )

    assert tagged == 1
    prompt = models.generate_calls[0]["contents"][0]
    assert "Existing tags:\n- Deadlock\n- Ivy (Deadlock)" in prompt
    assert "Tagging context:\n- Deadlock" in prompt
    assert "Prefer canonical capitalization." in prompt
    assert "search_existing_tags" not in prompt
    assert json.loads(store.media_tag("1")["raw_json"])["tags"][0] == "Deadlock"


@pytest.mark.asyncio
async def test_invalid_structured_output_is_recoverable_and_not_saved(monkeypatch, paths) -> None:
    store = Store()
    store.add_tweet("1")
    models = FakeModels([SimpleNamespace(text='{"not": "a list"}', usage_metadata=None)])
    install_fake_client(monkeypatch, models=models, interactions=FakeInteractions([]))
    allow_free_quota(monkeypatch)
    tagged = await tagging.tag_media_tweets(
        store, config(), paths, console()[0], ["1"], content_type="text"
    )
    assert tagged == 0
    assert store.media_tag("1") is None


@pytest.mark.asyncio
async def test_dry_run_returns_preview_without_persisting_tags(monkeypatch, paths) -> None:
    store = Store()
    store.add_tweet("1", text="A Deadlock update")
    models = FakeModels([free_response([{"id": "1", "tags": ["Deadlock", "Ivy (Deadlock)"]}])])
    install_fake_client(monkeypatch, models=models, interactions=FakeInteractions([]))
    allow_free_quota(monkeypatch)
    previews: list[tagging.TaggingPreview] = []

    tagged = await tagging.tag_media_tweets(
        store,
        config(),
        paths,
        console()[0],
        ["1"],
        content_type="text",
        dry_run=True,
        preview_results=previews,
    )

    assert tagged == 1
    assert store.media_tag("1") is None
    assert previews[0].model == "gemini-3.6-flash"
    assert previews[0].tags == ["Deadlock", "Ivy (Deadlock)"]


@pytest.mark.asyncio
async def test_owned_gemini_files_are_cleaned_without_listing_unrelated_files(
    monkeypatch, paths
) -> None:
    media_dir = paths.data_dir / "media"
    media_dir.mkdir(parents=True)
    (media_dir / "photo.jpg").write_bytes(b"not-decoded-in-paid-mode")
    store = Store()
    store.add_tweet("1", media=True)
    interactions = FakeInteractions(
        [paid_response({"description": "A precise image description.", "tags": ["One", "Two"]})]
    )
    _client, files = install_fake_client(
        monkeypatch, models=FakeModels([]), interactions=interactions
    )

    tagged = await tagging.tag_media_tweets(
        store,
        config(api_mode="paid", unlimited_spend=True, google_search=False),
        paths,
        console()[0],
        ["1"],
        content_type="media",
        dry_run=True,
    )

    assert tagged == 1
    assert files.deleted == ["owned-1"]
    assert "unrelated-file" not in files.deleted
    assert not hasattr(files, "list")
    assert interactions.calls[0]["model"] == "gemini-3.6-flash"
    assert "description" in interactions.calls[0]["response_format"]["schema"]["properties"]


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
    assert store.conn.execute(
        "SELECT value FROM archive WHERE row_key='tagging_retry:1'"
    ).fetchone()


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
    store.get_eligible_tagging_candidates = lambda **_: [{"tweet_id": "1", "content_type": "text"}]
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
    queue = QueueStore([{"tweet_id": str(i), "content_type": "text"} for i in [1, 2]])
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
