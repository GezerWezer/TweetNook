"""Only usable results are saved, once per tweet, and a failure keeps earlier output."""

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
    install_paid_pricing,
    paid_response,
)
from tweetnook.tagging import tagger as tagging


def saved(store: Store, tweet_id: str) -> dict | None:
    row = store.media_tag(tweet_id)
    return json.loads(row["raw_json"]) if row is not None else None


def retry_row(store: Store, tweet_id: str):
    return store.conn.execute(
        "SELECT 1 FROM archive WHERE row_key = ?", (f"tagging_retry:{tweet_id}",)
    ).fetchone()


def free_setup(monkeypatch, responses: list, tweet_ids=("1",)) -> Store:
    store = Store()
    for tweet_id in tweet_ids:
        store.add_tweet(tweet_id)
    install_fake_client(
        monkeypatch, models=FakeModels(responses), interactions=FakeInteractions([])
    )
    allow_free_quota(monkeypatch)
    return store


def free_media_setup(monkeypatch, paths, responses: list) -> tuple[Store, FakeModels]:
    store = Store()
    store.add_tweet("1", media=True)
    (paths.data_dir / "media").mkdir(parents=True)
    (paths.data_dir / "media/photo.jpg").write_bytes(b"synthetic")
    models = FakeModels(responses)
    install_fake_client(monkeypatch, models=models, interactions=FakeInteractions([]))
    allow_free_quota(monkeypatch)
    image = SimpleNamespace(load=lambda: None, close=lambda: None)
    monkeypatch.setattr(tagging, "Image", SimpleNamespace(open=lambda _: image))
    return store, models


@pytest.mark.parametrize("blank", [" ", "\n\t ", "\u00a0"])
async def test_free_blank_description_is_not_saved(monkeypatch, paths, blank):
    store, models = free_media_setup(
        monkeypatch,
        paths,
        [free_response([{"id": "1", "description": blank, "tags": ["One", "Two"]}])],
    )
    tagged = await tagging.tag_media_tweets(store, config(), paths, console()[0], ["1"])
    assert len(models.generate_calls) == 1
    assert tagged == 0
    assert saved(store, "1") is None


async def test_paid_blank_description_is_deferred_not_saved(monkeypatch, paths):
    store = Store()
    store.add_tweet("1", media=True)
    (paths.data_dir / "media").mkdir(parents=True)
    (paths.data_dir / "media/photo.jpg").write_bytes(b"synthetic")
    interactions = FakeInteractions(
        [paid_response({"description": "  \n", "tags": ["One", "Two"]})]
    )
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=interactions)
    install_paid_pricing(monkeypatch)

    tagged = await tagging.tag_media_tweets(
        store,
        config(api_mode="paid", unlimited_spend=True),
        paths,
        console()[0],
        ["1"],
        content_type="media",
    )

    assert len(interactions.calls) == 1
    assert tagged == 0
    assert saved(store, "1") is None
    assert retry_row(store, "1")


async def test_a_meaningful_description_is_saved_as_written(monkeypatch, paths):
    written = " A cat on a shelf. "
    store, _models = free_media_setup(
        monkeypatch,
        paths,
        [free_response([{"id": "1", "description": written, "tags": ["Cats", "Shelves"]}])],
    )

    assert await tagging.tag_media_tweets(store, config(), paths, console()[0], ["1"]) == 1

    assert saved(store, "1") == {"tags": ["Cats", "Shelves"], "description": written}


async def test_text_results_still_need_no_description(monkeypatch, paths):
    store = free_setup(monkeypatch, [free_response([{"id": "1", "tags": ["One", "Two"]}])])
    assert (
        await tagging.tag_media_tweets(
            store, config(), paths, console()[0], ["1"], content_type="text"
        )
        == 1
    )
    assert saved(store, "1") == {"tags": ["One", "Two"]}


async def test_duplicate_result_counts_once_and_first_usable_wins(monkeypatch, paths):
    store = free_setup(
        monkeypatch,
        [
            free_response(
                [
                    {"id": "1", "tags": ["First", "First"]},
                    {"id": "1", "tags": ["Second", "Result"]},
                    {"id": "1", "tags": ["Third", "Result"]},
                ]
            )
        ],
    )
    tagged = await tagging.tag_media_tweets(
        store, config(), paths, console()[0], ["1"], content_type="text"
    )
    assert tagged == 1
    assert saved(store, "1") == {"tags": ["Second", "Result"]}


async def test_duplicate_preview_is_offered_once(monkeypatch, paths):
    store = free_setup(
        monkeypatch,
        [
            free_response(
                [{"id": "1", "tags": ["One", "Two"]}, {"id": "1", "tags": ["Three", "Four"]}]
            )
        ],
    )
    previews = []
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
    assert [preview.tags for preview in previews] == [["One", "Two"]]


async def test_foreign_and_missing_targets_do_not_cost_the_valid_result(monkeypatch, paths):
    store = free_setup(
        monkeypatch,
        [
            free_response(
                [{"id": "999", "tags": ["Foreign", "Post"]}, {"id": "1", "tags": ["One", "Two"]}]
            )
        ],
        tweet_ids=("1", "2"),
    )
    tagged = await tagging.tag_media_tweets(
        store, config(), paths, console()[0], ["1", "2"], content_type="text"
    )
    assert tagged == 1
    assert saved(store, "999") is None
    assert saved(store, "1") == {"tags": ["One", "Two"]}
    assert saved(store, "2") is None


PRIOR = {"tags": ["Earlier", "Tags"], "description": "An earlier description."}


def store_with_saved_output(monkeypatch, responses: list) -> Store:
    store = free_setup(monkeypatch, responses)
    store.save_tagging_result("1", PRIOR)
    return store


@pytest.mark.parametrize(
    "response",
    [
        SimpleNamespace(text="", usage_metadata=None),
        RuntimeError("400 INVALID_ARGUMENT"),
        SimpleNamespace(text='{"not": "a list"}', usage_metadata=None),
    ],
    ids=["empty", "rejected", "malformed"],
)
async def test_failed_retag_keeps_saved_output(monkeypatch, paths, response):
    store = store_with_saved_output(monkeypatch, [response])

    tagged = await tagging.tag_media_tweets(
        store, config(), paths, console()[0], ["1"], content_type="text"
    )

    assert tagged == 0
    assert saved(store, "1") == PRIOR
    state = store.conn.execute(
        "SELECT enrichment_state FROM archive WHERE row_key = 'media_tag:1'"
    ).fetchone()[0]
    assert state == "done"


async def test_failed_first_attempt_leaves_a_retryable_marker(monkeypatch, paths):
    store = free_setup(monkeypatch, [SimpleNamespace(text="", usage_metadata=None)])

    await tagging.tag_media_tweets(store, config(), paths, console()[0], ["1"], content_type="text")

    assert saved(store, "1") == {}
    state = store.conn.execute(
        "SELECT enrichment_state FROM archive WHERE row_key = 'media_tag:1'"
    ).fetchone()[0]
    assert state == "failed"


async def test_successful_retag_replaces_saved_output(monkeypatch, paths):
    store = store_with_saved_output(
        monkeypatch, [free_response([{"id": "1", "tags": ["New", "Tags"]}])]
    )

    tagged = await tagging.tag_media_tweets(
        store, config(), paths, console()[0], ["1"], content_type="text"
    )

    assert tagged == 1
    assert saved(store, "1") == {"tags": ["New", "Tags"]}
