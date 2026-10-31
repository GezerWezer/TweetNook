"""A subdivided Free batch reuses what it prepared, under one owner."""

from __future__ import annotations

import sqlite3
from pathlib import Path
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


def video_store(paths, tweet_ids: list[str]) -> Store:
    store = Store()
    media_dir = paths.data_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    for tweet_id in tweet_ids:
        store.add_tweet(tweet_id)
        store.conn.execute(
            "INSERT INTO archive (row_key, record_type, tweet_id, media_key, media_type, "
            "local_path) VALUES (?, 'media', ?, ?, 'video', ?)",
            (f"media:{tweet_id}", tweet_id, f"m{tweet_id}", f"media/{tweet_id}.mp4"),
        )
        (media_dir / f"{tweet_id}.mp4").write_bytes(b"synthetic video")
    store.conn.commit()
    return store


@pytest.mark.asyncio
async def test_rejected_media_batch_splits_without_loading_anything_again(monkeypatch, paths):
    ids = ["1", "2", "3", "4"]
    store = video_store(paths, ids)
    deleted_during_children: list[list[str]] = []

    class Models(FakeModels):
        def generate_content(self, **kwargs):
            deleted_during_children.append(list(files.deleted))
            return super().generate_content(**kwargs)

    models = Models(
        [
            RuntimeError("400 INVALID_ARGUMENT"),
            free_response(
                [
                    {"id": "1", "description": "One.", "tags": ["A", "B"]},
                    {"id": "2", "description": "Two.", "tags": ["C", "D"]},
                ]
            ),
            free_response(
                [
                    {"id": "3", "description": "Three.", "tags": ["E", "F"]},
                    {"id": "4", "description": "Four.", "tags": ["G", "H"]},
                ]
            ),
        ]
    )
    _client, files = install_fake_client(
        monkeypatch, models=models, interactions=FakeInteractions([])
    )
    _rpm, rpd = allow_free_quota(monkeypatch)
    context_queries: list[list[str]] = []
    load_context = store.tagging_context

    def counted_context(context_ids):
        context_queries.append(list(context_ids))
        return load_context(context_ids)

    monkeypatch.setattr(store, "tagging_context", counted_context)

    tagged = await tagging.tag_media_tweets(
        store, config(), paths, console()[0], ids, content_type="media"
    )

    assert tagged == 4
    assert len(files.uploaded) == 4
    assert sorted(files.deleted) == ["owned-1", "owned-2", "owned-3", "owned-4"]
    assert deleted_during_children == [[], [], []]
    assert len(context_queries) == 1
    # Attempts and quota are unchanged: the parent and each child reserve and send once.
    assert len(models.generate_calls) == len(rpd) == 3
    children = models.generate_calls[1:]
    assert [
        [str(part).split("\n")[0] for part in call["contents"] if "[ID:" in str(part)]
        for call in children
    ] == [["[ID: 1]", "[ID: 2]"], ["[ID: 3]", "[ID: 4]"]]


@pytest.mark.asyncio
async def test_split_children_read_the_vocabulary_their_sibling_saved(monkeypatch, paths):
    store = Store()
    for tweet_id in ("1", "2"):
        store.add_tweet(tweet_id)
    snapshots: list[bool] = []

    def top_tags(current_store) -> list[str]:
        saved = current_store.media_tag("1") is not None
        snapshots.append(saved)
        return ["Saved Earlier"] if saved else []

    monkeypatch.setattr(tagging, "_top_archive_tags", top_tags)
    models = FakeModels(
        [
            RuntimeError("400 INVALID_ARGUMENT"),
            free_response([{"id": "1", "tags": ["One", "Two"]}]),
            free_response([{"id": "2", "tags": ["Three", "Four"]}]),
        ]
    )
    install_fake_client(monkeypatch, models=models, interactions=FakeInteractions([]))
    allow_free_quota(monkeypatch)

    await tagging.tag_media_tweets(
        store, config(), paths, console()[0], ["1", "2"], content_type="text"
    )

    assert "- Saved Earlier" not in models.generate_calls[1]["contents"][0]
    assert "- Saved Earlier" in models.generate_calls[2]["contents"][0]
    assert snapshots == [False, False, True]


class FakeImage:
    def __init__(self, path: str, closed: list[str], *, fail: Exception | None = None) -> None:
        self.path = path
        self.closed = closed
        self.fail = fail

    def load(self) -> None:
        if self.fail is not None:
            raise self.fail

    def close(self) -> None:
        self.closed.append(self.path)


def photo_store(paths, layout: dict[str, list[str]]) -> Store:
    store = Store()
    media_dir = paths.data_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    for tweet_id, names in layout.items():
        store.add_tweet(tweet_id)
        for name in names:
            store.conn.execute(
                "INSERT INTO archive (row_key, record_type, tweet_id, media_key, media_type, "
                "local_path) VALUES (?, 'media', ?, ?, 'photo', ?)",
                (f"media:{name}", tweet_id, name, f"media/{name}"),
            )
            (media_dir / name).write_bytes(b"synthetic image")
    store.conn.commit()
    return store


def fake_image_module(monkeypatch, bad: set[str], error: Exception) -> list[str]:
    closed: list[str] = []

    def open_image(path: str) -> FakeImage:
        name = path.rsplit("/", 1)[-1]
        return FakeImage(name, closed, fail=error if name in bad else None)

    monkeypatch.setattr(tagging, "Image", SimpleNamespace(open=open_image))
    return closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [OSError("cannot decode"), ValueError("bad header"), SyntaxError("broken PNG file")],
    ids=["oserror", "valueerror", "syntaxerror"],
)
async def test_unreadable_photo_only_costs_its_own_tweet(monkeypatch, paths, error):
    store = photo_store(paths, {"1": ["bad.jpg"], "2": ["good.jpg"]})
    models = FakeModels(
        [free_response([{"id": "2", "description": "Good.", "tags": ["One", "Two"]}])]
    )
    install_fake_client(monkeypatch, models=models, interactions=FakeInteractions([]))
    allow_free_quota(monkeypatch)
    closed = fake_image_module(monkeypatch, {"bad.jpg"}, error)
    sink, output = console()

    tagged = await tagging.tag_media_tweets(store, config(), paths, sink, ["1", "2"])

    assert tagged == 1
    assert store.media_tag("2") is not None
    assert store.media_tag("1") is None
    contents = models.generate_calls[0]["contents"]
    assert not any("[ID: 1]" in str(part) for part in contents)
    assert sum(isinstance(part, FakeImage) for part in contents) == 1
    assert "Skipping media bad.jpg" in output.getvalue()
    assert sorted(closed) == ["bad.jpg", "good.jpg"]


@pytest.mark.asyncio
async def test_failed_asset_beside_a_usable_one_still_sends_the_tweet(monkeypatch, paths):
    store = photo_store(paths, {"1": ["bad.jpg", "good.jpg"]})
    models = FakeModels(
        [free_response([{"id": "1", "description": "Good.", "tags": ["One", "Two"]}])]
    )
    install_fake_client(monkeypatch, models=models, interactions=FakeInteractions([]))
    allow_free_quota(monkeypatch)
    fake_image_module(monkeypatch, {"bad.jpg"}, OSError("cannot decode"))

    assert await tagging.tag_media_tweets(store, config(), paths, console()[0], ["1"]) == 1

    contents = models.generate_calls[0]["contents"]
    assert [part.path for part in contents if isinstance(part, FakeImage)] == ["good.jpg"]


@pytest.mark.asyncio
async def test_no_usable_media_sends_nothing_rather_than_tagging_from_text(monkeypatch, paths):
    store = photo_store(paths, {"1": ["bad.jpg"]})
    models = FakeModels([])
    install_fake_client(monkeypatch, models=models, interactions=FakeInteractions([]))
    _rpm, rpd = allow_free_quota(monkeypatch)
    fake_image_module(monkeypatch, {"bad.jpg"}, OSError("cannot decode"))
    sink, output = console()

    assert await tagging.tag_media_tweets(store, config(), paths, sink, ["1"]) == 0

    assert models.generate_calls == []
    assert rpd == []
    assert "No loadable media found" in output.getvalue()


def uploads_read_their_file(monkeypatch, files, *, unreadable: str) -> None:
    """The SDK opens the file only when uploading, and stat doesn't need read permission."""
    upload = files.upload
    open_path = Path.open

    def reading_upload(*, file: str):
        Path(file).open("rb").close()
        return upload(file=file)

    def denying_open(path: Path, *args, **kwargs):
        if path.name == unreadable:
            raise PermissionError(13, "Permission denied", str(path))
        return open_path(path, *args, **kwargs)

    monkeypatch.setattr(files, "upload", reading_upload)
    monkeypatch.setattr(Path, "open", denying_open)


@pytest.mark.asyncio
async def test_unreadable_upload_only_costs_its_own_free_tweet(monkeypatch, paths):
    store = video_store(paths, ["1", "2"])
    models = FakeModels(
        [free_response([{"id": "2", "description": "Good.", "tags": ["One", "Two"]}])]
    )
    _client, files = install_fake_client(
        monkeypatch, models=models, interactions=FakeInteractions([])
    )
    allow_free_quota(monkeypatch)
    uploads_read_their_file(monkeypatch, files, unreadable="1.mp4")
    sink, output = console()

    tagged = await tagging.tag_media_tweets(store, config(), paths, sink, ["1", "2"])

    assert tagged == 1
    assert store.media_tag("1") is None
    assert store.media_tag("2") is not None
    assert [Path(file).name for file in files.uploaded] == ["2.mp4"]
    assert "Skipping media 1.mp4" in output.getvalue()


@pytest.mark.asyncio
async def test_unreadable_upload_beside_a_usable_one_still_sends_the_paid_tweet(monkeypatch, paths):
    store = photo_store(paths, {"1": ["bad.jpg", "good.jpg"]})
    interactions = FakeInteractions(
        [paid_response({"description": "Good.", "tags": ["One", "Two"]})]
    )
    _client, files = install_fake_client(
        monkeypatch, models=FakeModels([]), interactions=interactions
    )
    install_paid_pricing(monkeypatch)
    uploads_read_their_file(monkeypatch, files, unreadable="bad.jpg")

    tagged = await tagging.tag_media_tweets(
        store, config(api_mode="paid", unlimited_spend=True), paths, console()[0], ["1"]
    )

    assert tagged == 1
    assert [Path(file).name for file in files.uploaded] == ["good.jpg"]
    assert len(interactions.calls) == 1


@pytest.mark.asyncio
async def test_failed_upload_of_a_readable_file_still_fails_the_batch(monkeypatch, paths):
    store = video_store(paths, ["1", "2"])
    models = FakeModels([])
    _client, files = install_fake_client(
        monkeypatch, models=models, interactions=FakeInteractions([])
    )
    allow_free_quota(monkeypatch)

    def refused_upload(*, file: str):
        raise ConnectionError("connection reset")

    monkeypatch.setattr(files, "upload", refused_upload)
    sink, output = console()

    assert await tagging.tag_media_tweets(store, config(), paths, sink, ["1", "2"]) == 0

    assert models.generate_calls == []
    assert "Gemini Tagging Failed" in output.getvalue()
    assert "Skipping media" not in output.getvalue()


@pytest.mark.asyncio
async def test_a_storage_failure_reading_the_archive_is_not_an_asset_failure(monkeypatch, paths):
    store = photo_store(paths, {"1": ["good.jpg"]})
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=FakeInteractions([]))
    allow_free_quota(monkeypatch)
    fake_image_module(monkeypatch, set(), OSError("unused"))

    def broken_context(_ids):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "tagging_context", broken_context)
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        await tagging.tag_media_tweets(store, config(), paths, console()[0], ["1"])
