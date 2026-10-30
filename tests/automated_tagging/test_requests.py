from __future__ import annotations

import json
from types import SimpleNamespace

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
    paid_response,
)
from tweetnook.tagging import tagger as tagging


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
async def test_link_previews_keep_text_tagging_and_never_upload_card_images(monkeypatch, paths):
    store = Store()
    store.add_tweet("1", text="A link preview", media=True)
    store.conn.execute("UPDATE archive SET source = 'link_card' WHERE record_type = 'media'")
    store.conn.commit()
    models = FakeModels([free_response([{"id": "1", "tags": ["Links", "Stories"]}])])
    _client, files = install_fake_client(
        monkeypatch, models=models, interactions=FakeInteractions([])
    )
    allow_free_quota(monkeypatch)
    previews = []
    tagged = await tagging.tag_media_tweets(
        store, config(), paths, console()[0], ["1"], dry_run=True, preview_results=previews
    )
    assert tagged == 1
    assert previews[0].content_type == "text"
    assert files.uploaded == []


@pytest.mark.asyncio
async def test_dry_run_returns_preview_without_persisting_tags(monkeypatch, paths) -> None:
    store = Store()
    store.add_tweet("1", text="A Deadlock update")
    models = FakeModels([free_response([{"id": "1", "tags": ["Deadlock", "Ivy (Deadlock)"]}])])
    install_fake_client(monkeypatch, models=models, interactions=FakeInteractions([]))
    allow_free_quota(monkeypatch)
    previews: list[tagging.TaggingPreview] = []
    # Test mode saves nothing, so another writer holding the archive must not fail it.
    monkeypatch.setattr(store, "transaction", lambda: pytest.fail("dry run took the write lock"))

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
