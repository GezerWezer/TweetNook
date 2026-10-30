import asyncio
import threading
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
)
from tweetnook.tagging import tagger as tagging


@pytest.mark.parametrize("failure", [False, True])
async def test_owned_client_closes_on_success_and_sql_setup_failure(paths, monkeypatch, failure):
    store = Store()
    store.add_tweet("1")
    allow_free_quota(monkeypatch)
    client, _ = install_fake_client(
        monkeypatch,
        models=FakeModels([free_response([{"id": "1", "tags": ["test", "second"]}])]),
        interactions=FakeInteractions([]),
    )
    closed = []
    client.close = lambda: closed.append(True)
    if failure:
        store.conn.execute("DROP INDEX idx_archive_tweet_id")
        with pytest.raises(Exception, match="no such index"):
            await tagging.tag_media_tweets(
                store, config(), paths, console()[0], ["1"], content_type="text"
            )
    else:
        assert (
            await tagging.tag_media_tweets(
                store, config(), paths, console()[0], ["1"], content_type="text"
            )
            == 1
        )
    assert closed == [True]
    assert client.async_closed
    store.conn.close()


async def test_failed_image_load_closes_image_and_client(paths, monkeypatch):
    store = Store()
    store.add_tweet("1", media=True)
    (paths.data_dir / "media").mkdir()
    (paths.data_dir / "media/photo.jpg").write_bytes(b"synthetic image")
    allow_free_quota(monkeypatch)
    client, _ = install_fake_client(
        monkeypatch, models=FakeModels([]), interactions=FakeInteractions([])
    )
    closed = []
    client.close = lambda: closed.append("client")

    def load():
        raise OSError("image failed")

    image = SimpleNamespace(load=load, close=lambda: closed.append("image"))
    monkeypatch.setattr(tagging, "Image", SimpleNamespace(open=lambda _: image))
    assert await tagging.tag_media_tweets(store, config(), paths, console()[0], ["1"]) == 0
    assert closed == ["image", "client"]
    assert client.async_closed
    store.conn.close()


async def test_cancelled_upload_joins_late_result_then_deletes_before_client_close(
    paths, monkeypatch
):
    store = Store()
    store.add_tweet("1", media=True)
    store.conn.execute("UPDATE archive SET media_type='video' WHERE record_type='media'")
    (paths.data_dir / "media").mkdir()
    (paths.data_dir / "media/photo.jpg").write_bytes(b"synthetic video")
    allow_free_quota(monkeypatch)
    client, files = install_fake_client(
        monkeypatch, models=FakeModels([]), interactions=FakeInteractions([])
    )
    started, release = threading.Event(), threading.Event()
    closed = []

    def upload(**kwargs):
        started.set()
        assert release.wait(5)
        return SimpleNamespace(name="late-owned")

    client.files.upload = upload
    client.close = lambda: closed.append(list(files.deleted))
    task = asyncio.create_task(
        tagging.tag_media_tweets(store, config(), paths, console()[0], ["1"])
    )
    assert await asyncio.to_thread(started.wait, 2)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    assert closed == []
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert files.deleted == ["late-owned"]
    assert closed == [["late-owned"]]
    assert client.async_closed
    store.conn.close()
