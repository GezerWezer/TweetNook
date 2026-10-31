from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tests.automated_tagging.conftest import (
    FakeInteractions,
    FakeModels,
    QueueStore,
    Store,
    allow_free_quota,
    config,
    console,
    free_response,
    install_fake_client,
    install_paid_pricing,
    paid_queue_store,
    paid_response,
)
from tweetnook.tagging import paid
from tweetnook.tagging import tagger as tagging


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
        QueueStore(
            [{"tweet_id": "1", "content_type": "text", "quoted_tweet_id": None, "sort_ts": 0}]
        ),
        config(),
        paths,
        out,
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
        {"tweet_id": tweet_id, "content_type": "text", "quoted_tweet_id": None, "sort_ts": 0}
        for tweet_id in ("1", "2", "3", "4")
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
    install_paid_pricing(monkeypatch)
    store = Store()
    for tweet_id in ("A", "B", "C", "D"):
        store.add_tweet(tweet_id)
    candidates = [
        {"tweet_id": tweet_id, "content_type": "text", "quoted_tweet_id": None, "sort_ts": 0}
        for tweet_id in ("A", "B", "C", "D")
    ]

    def select_candidates(*, limit: int, exclude_tweet_ids: set[str]):
        return [
            candidate for candidate in candidates if candidate["tweet_id"] not in exclude_tweet_ids
        ][:limit]

    store.get_eligible_tagging_candidates = select_candidates  # type: ignore[method-assign]

    def top_tags(current_store) -> list[str]:
        values = ["Deadlock"]
        if current_store.media_tag("A") is not None:
            values.append("Shiv (Deadlock)")
        return values

    monkeypatch.setattr(tagging, "_top_archive_tags", top_tags)
    interactions = FakeInteractions(
        [paid_response({"tags": ["Shiv (Deadlock)", "Deadlock"]}) for _ in range(4)]
    )
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=interactions)
    result = await tagging.tag_pending_media_tweets(
        store,
        config(api_mode="paid", unlimited_spend=True),
        paths,
        console()[0],
        batch_limit=4,
    )

    assert result == tagging.TaggingRunResult(processed=4, tagged=4, batches=4)
    assert ["- Shiv (Deadlock)" in call["system_instruction"] for call in interactions.calls] == [
        False,
        True,
        True,
        True,
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
        return [{"tweet_id": "1", "content_type": "text", "quoted_tweet_id": None, "sort_ts": 0}]

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
    install_paid_pricing(monkeypatch)
    store = paid_queue_store(4)
    interactions = FakeInteractions(
        [paid_response({"tags": ["One", "Two"]}) for _ in range(4)], delay=0.01
    )
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=interactions)
    result = await tagging.tag_pending_media_tweets(
        store,
        config(api_mode="paid", unlimited_spend=True),
        paths,
        console()[0],
        batch_limit=4,
    )
    assert result == tagging.TaggingRunResult(processed=4, tagged=4, batches=4)
    assert interactions.peak == 1
    assert all(len(call["input"]) == 1 for call in interactions.calls)
    assert all(store.media_tag(str(index)) is not None for index in range(1, 5))


@pytest.mark.asyncio
async def test_paid_engine_dispatches_independent_interactions_sequentially(
    monkeypatch, paths
) -> None:
    install_paid_pricing(monkeypatch)
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
    install_paid_pricing(monkeypatch)
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
    assert "config" not in models.count_calls[0]
    assert models.count_calls[0]["contents"][0] == request["system_instruction"]
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
    monkeypatch.setattr(paid, "load_openrouter_pricing", lambda *_args: None)

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
    assert "config" not in models.count_calls[0]
    assert models.count_calls[0]["contents"][0] == interactions.calls[0]["system_instruction"]
