"""A tagging run reports what it actually did, through the real runner, reporter and archive."""

from __future__ import annotations

import json
from io import StringIO

import pytest
from rich.console import Console

from tests.automated_tagging.conftest import (
    FakeInteractions,
    FakeModels,
    config,
    console,
    free_response,
    install_fake_client,
    install_paid_pricing,
    paid_response,
)
from tweetnook.jobs.outcome import Outcome
from tweetnook.jobs.pipeline import PipelineReporter
from tweetnook.jobs.resource_failure import StorageExhaustedError
from tweetnook.storage.backend import ArchiveStore
from tweetnook.tagging import tagger as tagging
from tweetnook.tagging.rpd import get_rpd_status, reserve_rpd_request
from tweetnook.tagging.run import TaggingOutcome

MODEL = "gemini-3.6-flash"
TAGS = ["One", "Two"]


@pytest.fixture
def store(paths):
    archive = ArchiveStore(paths.database_path, create=True)
    for tweet_id in ("1", "2", "3", "4", "5"):
        archive.conn.execute(
            "INSERT INTO archive (row_key, record_type, tweet_id, raw_json, author_display_name,"
            " author_username, text, enrichment_state) VALUES (?, 'tweet_object', ?, ?, 'Author',"
            " 'handle', 'A post', 'done')",
            (f"tweet_object:{tweet_id}", tweet_id, json.dumps({"legacy": {}})),
        )
        archive.conn.execute(
            "INSERT INTO archive (row_key, record_type, tweet_id, created_at_ts, sort_index)"
            " VALUES (?, 'tweet', ?, ?, ?)",
            (f"tweet:{tweet_id}", tweet_id, int(tweet_id), tweet_id),
        )
    archive.conn.commit()
    yield archive
    archive.close()


@pytest.fixture(autouse=True)
def no_waits(monkeypatch):
    async def no_rpm_wait(_model: str, _limit: int) -> None:
        return None

    monkeypatch.setattr(tagging, "_wait_for_free_rpm", no_rpm_wait)


def media_tag_rows(store: ArchiveStore) -> list[tuple[str, str]]:
    return store.conn.execute(
        "SELECT tweet_id, enrichment_state FROM archive WHERE record_type = 'media_tag'"
        " ORDER BY tweet_id"
    ).fetchall()


def fill_disk_on_tag_write(store: ArchiveStore) -> None:
    # Fails the provider response's own result write, after the request has been paid for.
    store.conn.execute(
        "CREATE TRIGGER full_disk BEFORE INSERT ON archive WHEN NEW.record_type = 'media_tag'"
        " BEGIN SELECT RAISE(ABORT, 'database or disk is full'); END"
    )
    store.conn.commit()


def reporter_for_tagging() -> PipelineReporter:
    reporter = PipelineReporter(Console(file=StringIO()), "sync", interactive=False)
    reporter.add_step("tagging", "Automated tags", total=1, unit="tweets")
    return reporter


async def test_spent_daily_allowance_sends_nothing_and_leaves_every_candidate_untouched(
    monkeypatch, paths, store
) -> None:
    reserve_rpd_request(store, model=MODEL, limit=1)
    models = FakeModels([])
    install_fake_client(monkeypatch, models=models, interactions=FakeInteractions([]))
    reporter = reporter_for_tagging()

    with reporter:
        result = await tagging.tag_pending_media_tweets(
            store, config(model=MODEL, free_rpd=1, free_batch_size=2), paths, console()[0]
        )

    assert result == tagging.TaggingRunResult(processed=0, tagged=0, batches=0)
    assert result.outcome is Outcome.PAUSED
    assert models.generate_calls == []
    assert get_rpd_status(store, model=MODEL, limit=1).used == 1
    assert media_tag_rows(store) == []
    step = reporter._step_by_key["tagging"]
    assert step.paused is True
    assert "daily request limit" in step.reason
    assert step.metrics == {"tagged": 0, "requests": 0}


async def test_allowance_spent_partway_saves_what_finished_and_pauses(
    monkeypatch, paths, store
) -> None:
    models = FakeModels([free_response([{"id": "5", "tags": TAGS}, {"id": "4", "tags": TAGS}])])
    install_fake_client(monkeypatch, models=models, interactions=FakeInteractions([]))
    reporter = reporter_for_tagging()

    with reporter:
        result = await tagging.tag_pending_media_tweets(
            store, config(model=MODEL, free_rpd=1, free_batch_size=2), paths, console()[0]
        )

    assert result == tagging.TaggingRunResult(processed=2, tagged=2, batches=1)
    assert result.outcome is Outcome.PAUSED
    assert [row[0] for row in media_tag_rows(store)] == ["4", "5"]
    assert reporter._step_by_key["tagging"].paused is True
    assert reporter._step_by_key["tagging"].metrics == {"tagged": 2, "requests": 1}


@pytest.mark.parametrize("api_mode", ["free", "paid"])
async def test_tweet_with_nothing_to_send_does_not_use_up_the_limit(
    monkeypatch, paths, store, api_mode
) -> None:
    # Tweets 5 and 4 are selected first. Only 4's file exists, and 3 must not be reached.
    media_dir = paths.data_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    (media_dir / "4.mp4").write_bytes(b"synthetic video")
    for tweet_id in ("5", "4"):
        store.conn.execute(
            "INSERT INTO archive (row_key, record_type, tweet_id, media_key, media_type,"
            " local_path) VALUES (?, 'media', ?, ?, 'video', ?)",
            (f"media:{tweet_id}", tweet_id, f"m{tweet_id}", f"media/{tweet_id}.mp4"),
        )
    store.conn.commit()
    description = {"description": "A video.", "tags": TAGS}
    models = FakeModels([free_response([{"id": "4", **description}])])
    interactions = FakeInteractions([paid_response(description)])
    install_fake_client(monkeypatch, models=models, interactions=interactions)
    install_paid_pricing(monkeypatch)
    settings = (
        config(model=MODEL, api_mode="paid", unlimited_spend=True, google_search=False)
        if api_mode == "paid"
        else config(model=MODEL, free_batch_size=1)
    )

    result = await tagging.tag_pending_media_tweets(
        store, settings, paths, console()[0], batch_limit=1
    )

    assert result.tagged == result.batches == 1
    assert len(models.generate_calls) + len(interactions.calls) == 1
    assert [row[0] for row in media_tag_rows(store)] == ["4"]


async def test_split_batch_reports_each_attempt_it_sent(monkeypatch, paths, store) -> None:
    rejected = RuntimeError("Bad request")
    rejected.status_code = 400
    models = FakeModels(
        [
            rejected,
            free_response([{"id": "5", "tags": TAGS}]),
            free_response([{"id": "4", "tags": TAGS}]),
        ]
    )
    install_fake_client(monkeypatch, models=models, interactions=FakeInteractions([]))

    result = await tagging.tag_pending_media_tweets(
        store,
        config(model=MODEL, free_rpd=10, free_batch_size=2),
        paths,
        console()[0],
        batch_limit=1,
    )

    assert result == tagging.TaggingRunResult(processed=2, tagged=2, batches=3)
    assert result.outcome is Outcome.SUCCEEDED
    assert len(models.generate_calls) == 3
    assert get_rpd_status(store, model=MODEL, limit=10).used == 3


async def test_provider_failure_is_a_failed_outcome_with_its_attempts_counted(
    monkeypatch, paths, store
) -> None:
    denied = RuntimeError("Forbidden")
    denied.status_code = 403
    models = FakeModels([denied])
    install_fake_client(monkeypatch, models=models, interactions=FakeInteractions([]))
    reporter = reporter_for_tagging()

    with reporter:
        result = await tagging.tag_pending_media_tweets(
            store, config(model=MODEL, free_rpd=10), paths, console()[0], batch_limit=1
        )

    assert result.outcome is Outcome.FAILED
    assert (result.tagged, result.batches) == (0, 1)
    step = reporter._step_by_key["tagging"]
    assert step.state == "failed"
    assert step.recoverable is True


async def test_one_success_and_one_failure_is_partial(monkeypatch, paths, store) -> None:
    denied = RuntimeError("Forbidden")
    denied.status_code = 403
    models = FakeModels([free_response([{"id": "5", "tags": TAGS}]), denied])
    install_fake_client(monkeypatch, models=models, interactions=FakeInteractions([]))
    reporter = reporter_for_tagging()

    with reporter:
        result = await tagging.tag_pending_media_tweets(
            store,
            config(model=MODEL, free_rpd=10, free_batch_size=1),
            paths,
            console()[0],
            batch_limit=2,
        )

    assert result.outcome is Outcome.PARTIAL
    assert (result.tagged, result.batches) == (1, 2)
    assert reporter._step_by_key["tagging"].state == "complete"
    assert "some requests failed" in reporter._step_by_key["tagging"].summary


async def test_free_result_write_on_a_full_disk_stops_the_run_as_storage_exhaustion(
    monkeypatch, paths, store
) -> None:
    models = FakeModels([free_response([{"id": "5", "tags": TAGS}])])
    install_fake_client(monkeypatch, models=models, interactions=FakeInteractions([]))
    fill_disk_on_tag_write(store)

    with pytest.raises(StorageExhaustedError, match="Free or add disk space"):
        await tagging.tag_pending_media_tweets(
            store,
            config(model=MODEL, free_rpd=10, free_batch_size=1),
            paths,
            console()[0],
        )

    assert len(models.generate_calls) == 1
    assert media_tag_rows(store) == []


async def test_paid_result_write_on_a_full_disk_stops_the_run_as_storage_exhaustion(
    monkeypatch, paths, store
) -> None:
    install_paid_pricing(monkeypatch)
    interactions = FakeInteractions([paid_response({"tags": TAGS})])
    install_fake_client(monkeypatch, models=FakeModels([]), interactions=interactions)
    fill_disk_on_tag_write(store)

    with pytest.raises(StorageExhaustedError, match="Free or add disk space"):
        await tagging.tag_pending_media_tweets(
            store, config(model=MODEL, api_mode="paid", unlimited_spend=True), paths, console()[0]
        )

    assert len(interactions.calls) == 1
    assert media_tag_rows(store) == []


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([], Outcome.NO_WORK),
        ([Outcome.NO_WORK, Outcome.SUCCEEDED], Outcome.SUCCEEDED),
        ([Outcome.SUCCEEDED, Outcome.SUCCEEDED], Outcome.SUCCEEDED),
        ([Outcome.FAILED, Outcome.NO_WORK, Outcome.FAILED], Outcome.FAILED),
        ([Outcome.SUCCEEDED, Outcome.FAILED], Outcome.PARTIAL),
        ([Outcome.SUCCEEDED, Outcome.PAUSED], Outcome.PAUSED),
        ([Outcome.PAUSED, Outcome.FAILED], Outcome.PAUSED),
    ],
)
def test_combining_outcomes(statuses, expected) -> None:
    total = TaggingOutcome()
    for status in statuses:
        total = total.plus(TaggingOutcome(status))
    assert total.status is expected
