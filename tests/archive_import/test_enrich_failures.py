from __future__ import annotations

import httpx
import pytest

from tests.archive_import.conftest import (
    _detail_entry_payload,
    _empty_detail_entry_payload,
    seed_tweet_objects,
    tweet_object_rows,
)
from tests.conftest import make_tweet_detail_response, make_tweet_result
from tweetnook.core.exceptions import (
    RepeatedFocalAbsenceError,
)
from tweetnook.core.locking import ProcessLock
from tweetnook.jobs.resource_failure import StorageExhaustedError
from tweetnook.storage import open_archive_store


def _available(tweet_id: str, text: str = "complete") -> dict[str, object]:
    return make_tweet_detail_response([make_tweet_result(tweet_id, text, user_id="42")])


def _absent() -> dict[str, object]:
    return make_tweet_detail_response([make_tweet_result("999", "unrelated")])


def test_enrich_pending_rows_aborts_after_three_absences_and_flushes_prior_rows(
    paths, detail_enrichment
) -> None:
    seed_tweet_objects(paths, ["1", "2", "3", "4", "5"])

    def respond(tweet_id: str, _request):
        return _available("1", "completed before breaker") if tweet_id == "1" else _absent()

    with pytest.raises(RepeatedFocalAbsenceError):
        detail_enrichment.run(respond)

    assert detail_enrichment.requested == ["1", "2", "3", "4"]
    rows = tweet_object_rows(paths)
    assert rows["1"]["enrichment_state"] == "done"
    for tweet_id in ("2", "3", "4"):
        assert rows[tweet_id]["enrichment_state"] == "transient_failure"
    assert rows["5"]["enrichment_state"] == "pending"


def test_enrich_focal_absence_counter_resets_on_explicit_empty_and_available_results(
    paths, detail_enrichment
) -> None:
    seed_tweet_objects(paths, ["1", "2", "3", "4", "5", "6", "7"])

    def respond(tweet_id: str, _request):
        if tweet_id == "2":
            return _detail_entry_payload(
                f"conversationthread-999-tweet-{tweet_id}",
                {
                    "__typename": "TweetUnavailable",
                    "reason": "This account is suspended.",
                },
            )
        if tweet_id == "4":
            return _available(tweet_id, "available reset")
        if tweet_id == "6":
            return _empty_detail_entry_payload(f"tweet-{tweet_id}")
        return _absent()

    result = detail_enrichment.run(respond)

    assert result.detail_terminal_unavailable == 2
    assert result.detail_lookups == 1
    assert result.detail_transient_failures == 4


def test_enrich_pending_rows_flushes_when_client_close_fails(
    paths, detail_enrichment, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_tweet_objects(paths, ["1"])
    close = httpx.AsyncClient.aclose

    async def failing_close(client: httpx.AsyncClient) -> None:
        await close(client)
        raise RuntimeError("close failed")

    monkeypatch.setattr(httpx.AsyncClient, "aclose", failing_close)

    with pytest.raises(RuntimeError, match="close failed"):
        detail_enrichment.run(lambda tweet_id, _request: _available(tweet_id))

    assert tweet_object_rows(paths)["1"]["enrichment_state"] == "done"


def test_enrich_pending_rows_flushes_completed_writes_on_interrupt(
    paths, detail_enrichment
) -> None:
    seed_tweet_objects(paths, ["1", "2"])

    def respond(tweet_id: str, _request):
        if len(detail_enrichment.requested) == 2:
            raise KeyboardInterrupt
        return _available(tweet_id)

    with pytest.raises(KeyboardInterrupt):
        detail_enrichment.run(respond)

    rows = tweet_object_rows(paths)
    first, second = detail_enrichment.requested
    assert rows[first]["enrichment_state"] == "done"
    assert rows[second]["enrichment_state"] == "pending"


def test_enrich_pending_rows_aborts_on_unexpected_parser_error_without_mutating_rows(
    paths, detail_enrichment, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_tweet_objects(paths, ["1", "2"])
    monkeypatch.setattr(
        "tweetnook.enrichment.detail.parse_tweet_detail_response",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TypeError("parser bug")),
    )

    with pytest.raises(TypeError, match="parser bug"):
        detail_enrichment.run(lambda tweet_id, _request: _available(tweet_id, "would be complete"))

    assert len(detail_enrichment.requested) == 1
    rows = tweet_object_rows(paths)
    assert rows["1"]["enrichment_state"] == "pending"
    assert rows["2"]["enrichment_state"] == "pending"


def test_enrich_pending_rows_keeps_known_transport_errors_per_row_retryable(
    paths, detail_enrichment
) -> None:
    seed_tweet_objects(paths, ["1"])

    def offline(_tweet_id: str, request: httpx.Request):
        raise httpx.ConnectError("offline", request=request)

    result = detail_enrichment.run(offline)

    assert result.detail_transient_failures == 1
    row = tweet_object_rows(paths)["1"]
    assert row["enrichment_state"] == "transient_failure"
    assert row["enrichment_reason"] is None
    assert row["enrichment_retry_count"] == 1
    assert row["enrichment_next_retry_at"] is not None


def test_full_disk_at_the_first_flush_stops_with_the_storage_error_and_releases_the_lock(
    paths, detail_enrichment
) -> None:
    seed_tweet_objects(paths, ["1", "2"])
    store = open_archive_store(paths, create=False)
    assert store is not None
    # Fails the first write of an enriched row, inside the job's own flush.
    for event in ("INSERT", "UPDATE"):
        store.conn.execute(
            f"CREATE TRIGGER full_disk_{event.lower()} BEFORE {event} ON archive"
            " WHEN NEW.record_type = 'tweet_object' AND NEW.enrichment_state = 'done'"
            " BEGIN SELECT RAISE(ABORT, 'database or disk is full'); END"
        )
    store.conn.commit()
    store.close()

    with pytest.raises(StorageExhaustedError, match="Free or add disk space"):
        detail_enrichment.run(
            lambda tweet_id, _request: make_tweet_detail_response(
                [make_tweet_result(tweet_id, "enriched", user_id="42")]
            )
        )

    assert sorted(detail_enrichment.requested) == ["1", "2"]
    assert detail_enrichment.dirty == []
    lock = ProcessLock(paths.lock_file)
    lock.acquire()
    lock.release()
    rows = tweet_object_rows(paths)
    assert {tweet_id: row["enrichment_state"] for tweet_id, row in rows.items()} == {
        "1": "pending",
        "2": "pending",
    }
