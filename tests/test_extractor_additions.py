from __future__ import annotations

import pytest

from tests.conftest import make_tweet_result
from tweetnook.extractor import (
    classify_tweet_unavailability,
    extract_canonical_text,
    extract_note_tweet_text,
    extract_secondary_objects,
    extract_secondary_objects_from_tweets,
    extract_thread_objects,
    unwrap_tweet_result,
)


@pytest.mark.parametrize("value", [None, "tweet", 1, [], {"legacy": {}}])
def test_unwrap_tweet_result_rejects_non_tweet_shapes(value) -> None:
    assert unwrap_tweet_result(value) is None


@pytest.mark.parametrize("typename", ["TweetTombstone", "TweetUnavailable"])
def test_unwrap_tweet_result_preserves_terminal_shapes(typename: str) -> None:
    payload = {
        "__typename": typename,
        "rest_id": "deleted-id",
        "reason": "This Post is unavailable.",
    }

    assert unwrap_tweet_result(payload) is payload


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("These posts are protected.", "protected_account"),
        (
            "You're unable to view this Post because this account owner limits who can view "
            "their Posts. {learnmore}",
            "protected_account",
        ),
        ("This post is unavailable because the account is protected.", "protected_account"),
        ("This Post is from a private account. {learnmore}", "protected_account"),
        ("Account suspended", "suspended_account"),
        (
            "The account that authored this post has been suspended.",
            "suspended_account",
        ),
        ("This account is suspended.", "suspended_account"),
        ("This account doesn't exist.", "account_missing"),
        ("This Post is from an account that no longer exists.", "account_missing"),
        ("This Post was deleted by the Post author.", "deleted_by_author"),
        ("This post was deleted by its author.", "deleted_by_author"),
        ("The author deleted this post.", "deleted_by_author"),
        ("This Post has been withheld in your country.", "withheld"),
        ("Post not found", "not_found"),
        ("Dieses Posting ist nicht verfügbar.", "unavailable_unknown"),
        (None, "unavailable_unknown"),
    ],
)
def test_classify_tweet_unavailability(message: str | None, expected: str) -> None:
    assert classify_tweet_unavailability("TweetUnavailable", message) == expected


def test_visibility_wrapper_preserves_birdwatch_pivot_and_canonical_note_text() -> None:
    tweet = make_tweet_result(
        "42",
        "legacy short text",
        note_text="long-form canonical tweet text",
    )
    pivot = {
        "destinationUrl": {"url": "https://x.com/i/birdwatch/n/123"},
        "footer": {"text": "Readers added context"},
        "note": {"text": "Community note text is metadata, not tweet text."},
    }
    wrapped = {
        "__typename": "TweetWithVisibilityResults",
        "tweet": tweet,
        "birdwatch_pivot": pivot,
    }

    unwrapped = unwrap_tweet_result(wrapped)

    assert unwrapped is not None
    assert unwrapped["rest_id"] == "42"
    assert unwrapped["birdwatch_pivot"] is pivot
    assert extract_note_tweet_text(unwrapped) == "long-form canonical tweet text"
    assert extract_canonical_text(unwrapped) == "long-form canonical tweet text"


def test_visibility_wrapper_without_birdwatch_does_not_invent_metadata() -> None:
    wrapped = {
        "__typename": "TweetWithVisibilityResults",
        "tweet": make_tweet_result("42", "legacy text"),
    }

    unwrapped = unwrap_tweet_result(wrapped)

    assert unwrapped is not None
    assert "birdwatch_pivot" not in unwrapped
    assert extract_canonical_text(unwrapped) == "legacy text"


@pytest.mark.parametrize(
    "wrapped",
    [
        {"__typename": "TweetWithVisibilityResults"},
        {"__typename": "TweetWithVisibilityResults", "tweet": "bad"},
        {"__typename": "TweetWithVisibilityResults", "tweet": []},
        {
            "__typename": "TweetWithVisibilityResults",
            "tweet": {"__typename": "Unknown"},
            "birdwatch_pivot": {"note": "context"},
        },
    ],
)
def test_visibility_wrapper_rejects_malformed_nested_tweets(wrapped: dict) -> None:
    assert unwrap_tweet_result(wrapped) is None


@pytest.mark.parametrize(
    "note_tweet",
    [
        "invalid",
        [],
        {"note_tweet_results": "invalid"},
        {"note_tweet_results": []},
        {"note_tweet_results": {"result": "invalid"}},
        {"note_tweet_results": {"result": []}},
        {"note_tweet_results": {"result": {"text": [], "richtext": "invalid"}}},
        {"note_tweet_results": {"result": {"text": {}, "richtext": {"text": []}}}},
    ],
)
def test_malformed_note_tweet_falls_back_to_legacy_text(note_tweet) -> None:
    tweet = {
        "rest_id": "42",
        "legacy": {"full_text": "legacy fallback"},
        "note_tweet": note_tweet,
    }

    assert extract_note_tweet_text(tweet) is None
    assert extract_canonical_text(tweet) == "legacy fallback"


def test_note_tweet_uses_richtext_fallback_only_when_it_is_text() -> None:
    tweet = {
        "legacy": {"full_text": "legacy"},
        "note_tweet": {
            "note_tweet_results": {"result": {"text": "", "richtext": {"text": "rich note"}}}
        },
    }

    assert extract_note_tweet_text(tweet) == "rich note"
    assert extract_canonical_text(tweet) == "rich note"


@pytest.mark.parametrize("legacy", ["invalid", [], {"full_text": []}, None])
def test_canonical_text_handles_malformed_legacy_shapes(legacy) -> None:
    assert extract_canonical_text({"legacy": legacy}) == ""


def test_secondary_extraction_survives_malformed_note_metadata() -> None:
    tweet = make_tweet_result("42", "legacy fallback")
    tweet["note_tweet"] = {
        "note_tweet_results": {"result": {"text": [], "richtext": {"text": {"bad": True}}}}
    }

    graph = extract_secondary_objects(tweet)

    assert graph.tweet_objects["42"].text == "legacy fallback"
    assert graph.tweet_objects["42"].note_tweet_text is None


def test_terminal_results_do_not_create_storage_facing_graph_rows() -> None:
    unavailable = {
        "__typename": "TweetUnavailable",
        "rest_id": "deleted-id",
    }
    tombstone = {
        "__typename": "TweetTombstone",
        "rest_id": "private-id",
    }

    secondary = extract_secondary_objects_from_tweets([unavailable, tombstone])
    thread = extract_thread_objects([unavailable, tombstone])

    assert not secondary.tweet_objects
    assert not secondary.relations
    assert not thread.tweet_objects
    assert not thread.relations


def test_attached_terminal_result_does_not_create_false_relation() -> None:
    root = make_tweet_result("42", "available")
    root["quoted_status_result"] = {
        "result": {"__typename": "TweetTombstone", "rest_id": "deleted-id"}
    }

    graph = extract_secondary_objects(root)

    assert set(graph.tweet_objects) == {"42"}
    assert not graph.relations


def test_restored_visibility_result_builds_complete_storage_facing_row() -> None:
    restored = make_tweet_result(
        "restored-id",
        "restored legacy",
        note_text="restored canonical note",
        user_id="author-id",
    )
    pivot = {"footer": {"text": "Readers added context"}}
    wrapped = {
        "__typename": "TweetWithVisibilityResults",
        "tweet": restored,
        "birdwatch_pivot": pivot,
    }

    graph = extract_secondary_objects_from_tweets([{"__typename": "TweetUnavailable"}, wrapped])

    assert set(graph.tweet_objects) == {"restored-id"}
    row = graph.tweet_objects["restored-id"]
    assert row.text == "restored canonical note"
    assert row.note_tweet_text == "restored canonical note"
    assert row.author_id == "author-id"
    assert row.raw_json["birdwatch_pivot"] == pivot
