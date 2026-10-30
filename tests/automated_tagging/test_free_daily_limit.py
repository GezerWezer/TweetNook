"""The Free daily request limit stops the tagger, using the real RPD counter and archive.

Other run tests replace the counter with one that always allows, so these are the only
tests that would notice a request sent without a reservation.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from tests.automated_tagging.conftest import (
    FakeInteractions,
    FakeModels,
    config,
    console,
    free_response,
    install_fake_client,
)
from tweetnook.storage.backend import ArchiveStore
from tweetnook.tagging import tagger as tagging
from tweetnook.tagging.rpd import get_rpd_status, reserve_rpd_request

MODEL = "gemini-3.6-flash"


@pytest.fixture
def store(paths):
    archive = ArchiveStore(paths.database_path, create=True)
    for tweet_id in ("1", "2"):
        archive.conn.execute(
            "INSERT INTO archive (row_key, record_type, tweet_id, raw_json, author_display_name,"
            " author_username, text) VALUES (?, 'tweet_object', ?, ?, 'Author', 'handle', ?)",
            (f"tweet_object:{tweet_id}", tweet_id, json.dumps({"legacy": {}}), "A post"),
        )
    archive.conn.commit()
    yield archive
    archive.close()


@pytest.fixture
def no_waits(monkeypatch):
    async def no_rpm_wait(_model: str, _limit: int) -> None:
        return None

    original_sleep = asyncio.sleep
    monkeypatch.setattr(tagging, "_wait_for_free_rpm", no_rpm_wait)
    monkeypatch.setattr(tagging.asyncio, "sleep", lambda _delay: original_sleep(0))


def used_today(store: ArchiveStore, limit: int) -> int:
    return get_rpd_status(store, model=MODEL, limit=limit).used


async def test_spent_allowance_stops_before_building_a_client(
    monkeypatch, paths, store, no_waits
) -> None:
    reserve_rpd_request(store, model=MODEL, limit=1)
    models = FakeModels([free_response([{"id": "1", "tags": ["Unused"]}])])
    client, _files = install_fake_client(
        monkeypatch, models=models, interactions=FakeInteractions([])
    )
    built: list[dict] = []
    monkeypatch.setattr(
        tagging,
        "genai",
        type("Genai", (), {"Client": staticmethod(lambda **kw: built.append(kw) or client)}),
    )
    output_console, output = console()

    tagged = await tagging.tag_media_tweets(
        store, config(model=MODEL, free_rpd=1), paths, output_console, ["1"], content_type="text"
    )

    assert tagged == 0
    assert built == []
    assert models.generate_calls == []
    assert used_today(store, 1) == 1
    reset = get_rpd_status(store, model=MODEL, limit=1).reset_at.strftime("%Y-%m-%d %H:%M %Z")
    text = " ".join(output.getvalue().split())
    assert f"Gemini daily request limit reached for {MODEL} (1/1)." in text
    assert f"No further requests will be sent until {reset}." in text


async def test_retry_after_a_busy_response_needs_its_own_reservation(
    monkeypatch, paths, store, no_waits
) -> None:
    models = FakeModels(
        [
            RuntimeError("429 RESOURCE_EXHAUSTED"),
            free_response([{"id": "1", "tags": ["Unsent"]}]),
        ]
    )
    install_fake_client(monkeypatch, models=models, interactions=FakeInteractions([]))
    output_console, output = console()

    tagged = await tagging.tag_media_tweets(
        store, config(model=MODEL, free_rpd=1), paths, output_console, ["1"], content_type="text"
    )

    assert tagged == 0
    assert len(models.generate_calls) == 1
    assert used_today(store, 1) == 1
    text = " ".join(output.getvalue().split())
    assert "Gemini API busy. Retrying" in text
    assert f"Gemini daily request limit reached for {MODEL} (1/1)." in text
    assert (
        store.conn.execute(
            "SELECT COUNT(*) FROM archive WHERE record_type = 'media_tag'"
        ).fetchone()[0]
        == 0
    )


async def test_failed_reservation_write_sends_no_request(
    monkeypatch, paths, store, no_waits
) -> None:
    models = FakeModels([free_response([{"id": "1", "tags": ["Unsent"]}])])
    install_fake_client(monkeypatch, models=models, interactions=FakeInteractions([]))
    # Fails the quota row's write, after the reservation's transaction has begun and read.
    store.conn.execute(
        "CREATE TEMP TRIGGER fail_rpd_write BEFORE INSERT ON main.archive"
        " WHEN NEW.key LIKE 'gemini_rpd:%'"
        " BEGIN SELECT RAISE(ABORT, 'disk I/O error'); END"
    )
    output_console, output = console()

    tagged = await tagging.tag_media_tweets(
        store, config(model=MODEL, free_rpd=5), paths, output_console, ["1"], content_type="text"
    )

    assert tagged == 0
    assert models.generate_calls == []
    assert not store.conn.in_transaction
    assert used_today(store, 5) == 0
    text = " ".join(output.getvalue().split())
    assert "Could not update Gemini daily request usage; request not sent: " in text
    assert "disk I/O error" in text
