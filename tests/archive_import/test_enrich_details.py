from __future__ import annotations

from contextlib import contextmanager

import pytest

import tweetnook.importer.archive as archive_import
import tweetnook.importer.archive_enrich as archive_enrich
from tests.archive_import.conftest import (
    _detail_entry_payload,
    _empty_detail_entry_payload,
    seed_tweet_objects,
    tweet_object_rows,
)
from tests.conftest import make_tweet_detail_response, make_tweet_result


def _available(tweet_id: str, text: str) -> dict[str, object]:
    return make_tweet_detail_response([make_tweet_result(tweet_id, text, user_id="777")])


@pytest.mark.parametrize(
    ("limit", "expected_attempts"),
    [(None, 1_200), (200, 200)],
)
def test_enrich_pending_rows_uses_full_snapshot_or_explicit_limit(
    paths,
    detail_enrichment,
    monkeypatch: pytest.MonkeyPatch,
    limit: int | None,
    expected_attempts: int,
) -> None:
    total_rows = 1_200
    seed_tweet_objects(paths, [str(tweet_id) for tweet_id in range(1, total_rows + 1)])
    progress_totals: list[int] = []
    progress_updates: list[tuple[int, int]] = []

    @contextmanager
    def fake_progress_callback(_console, *, total: int, **_kwargs):
        progress_totals.append(total)
        yield lambda current, callback_total: progress_updates.append((current, callback_total))

    monkeypatch.setattr(archive_enrich, "_progress_callback", fake_progress_callback)

    result = detail_enrichment.run(
        lambda tweet_id, _request: _available(tweet_id, f"enriched {tweet_id}"), limit=limit
    )

    attempts = detail_enrichment.requested
    assert result.detail_lookups == expected_attempts
    assert len(attempts) == expected_attempts
    assert len(set(attempts)) == expected_attempts
    assert progress_totals == [expected_attempts]
    assert progress_updates[-1] == (expected_attempts, expected_attempts)
    assert sum(rows for rows, _batches in detail_enrichment.dirty) == expected_attempts
    states = [row["enrichment_state"] for row in tweet_object_rows(paths).values()]
    assert states.count("done") == expected_attempts
    assert states.count("pending") == total_rows - expected_attempts


def test_enrich_pending_rows_uses_command_start_time_for_stable_snapshot(
    paths,
    detail_enrichment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started_at = "2099-01-01T00:00:00+00:00"
    seed_tweet_objects(
        paths, ["1"], enrichment_state="transient_failure", enrichment_next_retry_at=started_at
    )
    seed_tweet_objects(
        paths,
        ["2"],
        enrichment_state="transient_failure",
        enrichment_next_retry_at="2099-01-01T00:01:00+00:00",
    )
    selection_calls: list[tuple[int | None, str | None]] = []
    original_selector = archive_import.ArchiveStore.list_tweet_objects_for_enrichment

    def tracked_selector(self, *, limit=None, now=None):
        selection_calls.append((limit, now))
        return original_selector(self, limit=limit, now=now)

    monkeypatch.setattr(
        archive_import.ArchiveStore,
        "list_tweet_objects_for_enrichment",
        tracked_selector,
    )

    result = detail_enrichment.run(
        lambda tweet_id, _request: _available(tweet_id, "available"), started_at=started_at
    )

    assert result.detail_lookups == 1
    assert detail_enrichment.requested == ["1"]
    assert selection_calls == [(None, started_at)]
    rows = tweet_object_rows(paths)
    assert rows["1"]["enrichment_state"] == "done"
    assert rows["2"]["enrichment_state"] == "transient_failure"
    assert rows["2"]["enrichment_next_retry_at"] == "2099-01-01T00:01:00+00:00"


def test_enrich_pending_rows_batches_detail_writes(
    paths, detail_enrichment, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_tweet_objects(
        paths, [str(1000 + index) for index in range(12)], source="x_archive", text="placeholder"
    )
    monkeypatch.setattr(archive_enrich, "_DETAIL_ENRICH_WRITE_BATCH", 5)

    result = detail_enrichment.run(
        lambda tweet_id, _request: _available(tweet_id, f"enriched {tweet_id}")
    )

    assert (
        result.detail_lookups,
        result.detail_terminal_unavailable,
        result.detail_transient_failures,
        result.pending_enrichment,
    ) == (12, 0, 0, 0)
    assert detail_enrichment.dirty == [(5, 1), (5, 1), (2, 1)]
    assert {row["enrichment_state"] for row in tweet_object_rows(paths).values()} == {"done"}


@pytest.mark.parametrize(
    ("message", "expected_reason", "retry_eligible", "expected_detail"),
    [
        ("These posts are protected.", "protected_account", 1, "These posts are protected."),
        ("This account is suspended.", "suspended_account", 1, "This account is suspended."),
        ("This account doesn't exist.", "account_missing", 1, "This account doesn't exist."),
        (
            "This Post was deleted by the Post author.",
            "deleted_by_author",
            0,
            "This Post was deleted by the Post author.",
        ),
        (
            "Dieses Posting ist nicht verfügbar.",
            "unavailable_unknown",
            1,
            "Dieses Posting ist nicht verfügbar.",
        ),
        (
            None,
            "unavailable_unknown",
            1,
            "TweetDetail returned an empty tweet_results object for the requested focal entry.",
        ),
    ],
)
def test_enrich_pending_rows_classifies_explicit_unavailable_shapes(
    paths,
    detail_enrichment,
    message: str | None,
    expected_reason: str,
    retry_eligible: int,
    expected_detail: str,
) -> None:
    seed_tweet_objects(paths, ["900"], source="x_archive")

    def respond(_tweet_id: str, _request):
        if message is None:
            return _empty_detail_entry_payload("tweet-900")
        return _detail_entry_payload(
            "conversationthread-777-tweet-900",
            {
                "__typename": "TweetTombstone",
                "tombstone": {"text": {"text": message}},
            },
        )

    result = detail_enrichment.run(respond)

    assert result.detail_terminal_unavailable == 1
    assert result.pending_enrichment == 0
    row = tweet_object_rows(paths)["900"]
    assert row["enrichment_state"] == "terminal_unavailable"
    assert row["enrichment_reason"] == expected_reason
    assert row["enrichment_detail"] == expected_detail
    assert row["enrichment_retry_eligible"] == retry_eligible


def test_enrich_pending_rows_defers_one_ambiguous_focal_absence(paths, detail_enrichment) -> None:
    seed_tweet_objects(paths, ["1"])

    result = detail_enrichment.run(
        lambda _tweet_id, _request: make_tweet_detail_response(
            [make_tweet_result("999", "unrelated")]
        )
    )

    assert result.detail_transient_failures == 1
    assert result.detail_terminal_unavailable == 0
    row = tweet_object_rows(paths)["1"]
    assert row["enrichment_state"] == "transient_failure"
    assert row["enrichment_reason"] == "focal_tweet_absent"
    assert "identifiable focal result" in row["enrichment_detail"]
    assert row["enrichment_next_retry_at"] is not None
