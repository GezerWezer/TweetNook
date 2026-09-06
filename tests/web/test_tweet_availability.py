from __future__ import annotations

import json

from tweetnook.storage.backend import ArchiveStore
from tweetnook.web.availability import annotate_web_tweets, availability_for_tweet


def _web_tweet(tweet_id: str, raw_json: dict | None = None) -> dict:
    return {
        "tweet_id": tweet_id,
        "text": "wrapper",
        "author": {"id": "u1", "username": "alice", "display_name": "Alice"},
        "created_at": "2026-08-10T00:00:00+00:00",
        "raw_json": raw_json or {"rest_id": tweet_id, "legacy": {}},
        "media": [{"type": "photo"}],
    }


def test_availability_reason_catalog_and_incomplete_states() -> None:
    for reason, message in {
        "protected_account": "This tweet is from a protected account.",
        "suspended_account": "This tweet is from a suspended account.",
        "account_missing": "This tweet is from an account that no longer exists.",
        "deleted_by_author": "This tweet was deleted by its author.",
        "archive_deleted": "This tweet was deleted from Twitter/X.",
        "withheld": "This tweet is unavailable in your location.",
        "not_found": "This tweet could not be found.",
        "unavailable_unknown": "This tweet is unavailable.",
    }.items():
        availability = availability_for_tweet(
            _web_tweet("1"),
            {
                "enrichment_state": "terminal_unavailable",
                "enrichment_reason": reason,
            },
        )
        assert availability["message"] == message
        assert availability["placeholder"] is True

    partial = availability_for_tweet(_web_tweet("2"), {"enrichment_state": "transient_failure"})
    empty = availability_for_tweet(
        {"tweet_id": "3", "text": "", "raw_json": None},
        {"enrichment_state": "pending"},
    )
    assert partial["state"] == "incomplete"
    assert partial["placeholder"] is False
    assert empty["state"] == "incomplete"
    assert empty["placeholder"] is True

    ordinary_text = _web_tweet("4")
    ordinary_text["text"] = "I heard the account is protected, but this post is still here."
    available = availability_for_tweet(ordinary_text, None)
    assert available["state"] == "incomplete"
    assert available["placeholder"] is False

    legacy_private = _web_tweet("5")
    legacy_private["text"] = "This Post is from a private account. {learnmore}"
    protected = availability_for_tweet(legacy_private, None)
    assert protected["reason"] == "protected_account"
    assert protected["placeholder"] is True


def test_canonical_attached_state_overrides_stale_embedded_payloads(tmp_path) -> None:
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    live_quote = {
        "__typename": "Tweet",
        "rest_id": "terminal-target",
        "legacy": {"full_text": "stale live quote"},
    }
    stale_tombstone = {
        "__typename": "TweetTombstone",
        "tombstone": {"text": {"text": "This account is suspended."}},
    }
    store._merge_records(
        [
            store._record(
                row_key="tweet_object:terminal-target",
                record_type="tweet_object",
                tweet_id="terminal-target",
                text="previous text",
                raw_json=json.dumps(live_quote),
                enrichment_state="terminal_unavailable",
                enrichment_reason="deleted_by_author",
                enrichment_retry_eligible=0,
            ),
            store._record(
                row_key="tweet_object:resurrected-target",
                record_type="tweet_object",
                tweet_id="resurrected-target",
                text="available again",
                author_id="u2",
                author_username="bob",
                author_display_name="Bob",
                raw_json=json.dumps(
                    {
                        "__typename": "Tweet",
                        "rest_id": "resurrected-target",
                        "legacy": {"full_text": "available again"},
                    }
                ),
                enrichment_state="resurrected",
            ),
        ]
    )
    terminal_wrapper = _web_tweet(
        "wrapper-1",
        {
            "rest_id": "wrapper-1",
            "legacy": {"quoted_status_id_str": "terminal-target"},
            "quoted_status_result": {"result": live_quote},
        },
    )
    resurrected_wrapper = _web_tweet(
        "wrapper-2",
        {
            "rest_id": "wrapper-2",
            "legacy": {"quoted_status_id_str": "resurrected-target"},
            "quoted_status_result": {"result": stale_tombstone},
        },
    )

    annotate_web_tweets(store, [terminal_wrapper, resurrected_wrapper])

    terminal_quote = terminal_wrapper["quoted_tweet"]
    assert terminal_quote["availability"]["reason"] == "deleted_by_author"
    assert terminal_quote["availability"]["confirmed"] is True
    assert terminal_quote["raw_json"] is None
    resurrected_quote = resurrected_wrapper["quoted_tweet"]
    assert resurrected_quote["availability"]["state"] == "available"
    assert resurrected_quote["text"] == "available again"
    assert resurrected_quote["author"]["username"] == "bob"
    store.close()


def test_canonical_state_refreshes_content_but_pending_keeps_embedded(tmp_path) -> None:
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    store._merge_records(
        [
            store._record(
                row_key="tweet_object:resurrected",
                record_type="tweet_object",
                tweet_id="resurrected",
                text="current text",
                author_id="u2",
                author_username="bob",
                author_display_name="Bob",
                raw_json=json.dumps({"rest_id": "resurrected", "legacy": {}}),
                enrichment_state="resurrected",
            ),
            store._record(
                row_key="tweet_object:pending-target",
                record_type="tweet_object",
                tweet_id="pending-target",
                text="",
                raw_json=None,
                enrichment_state="pending",
            ),
        ]
    )
    direct = _web_tweet("resurrected")
    direct["text"] = "This Post is from a suspended account. {learnmore}"
    wrapper = _web_tweet(
        "wrapper",
        {
            "rest_id": "wrapper",
            "legacy": {"quoted_status_id_str": "pending-target"},
            "quoted_status_result": {
                "result": {
                    "__typename": "Tweet",
                    "rest_id": "pending-target",
                    "legacy": {"full_text": "captured quote"},
                }
            },
        },
    )

    annotate_web_tweets(store, [direct, wrapper])

    assert direct["availability"]["state"] == "available"
    assert direct["text"] == "current text"
    assert direct["author"]["username"] == "bob"
    pending_quote = wrapper["quoted_tweet"]
    assert pending_quote["availability"]["state"] == "incomplete"
    assert pending_quote["availability"]["placeholder"] is False
    assert pending_quote["text"] == "captured quote"
    store.close()


def test_raw_media_and_unfetched_quote_get_presentation_placeholders(tmp_path) -> None:
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    tweet = _web_tweet(
        "source",
        {
            "__typename": "Tweet",
            "rest_id": "source",
            "legacy": {
                "full_text": "media post",
                "is_quote_status": True,
                "quoted_status_id_str": "missing-quote",
                "extended_entities": {
                    "media": [
                        {
                            "type": "animated_gif",
                            "original_info": {"width": 640, "height": 360},
                            "video_info": {"duration_millis": 2400},
                        }
                    ]
                },
            },
        },
    )
    tweet["media"] = []
    marker_only_quote = _web_tweet(
        "marker-only",
        {
            "rest_id": "marker-only",
            "legacy": {"full_text": "quote marker", "is_quote_status": True},
        },
    )

    annotate_web_tweets(store, [tweet, marker_only_quote])

    assert tweet["media"] == [
        {
            "type": "animated_gif",
            "width": 640,
            "height": 360,
            "duration_millis": 2400,
            "download": {"local_path": None, "thumbnail_local_path": None},
        }
    ]
    quote = tweet["quoted_tweet"]
    assert quote["tweet_id"] == "missing-quote"
    assert quote["availability"]["state"] == "not_archived"
    assert quote["availability"]["placeholder"] is True
    assert marker_only_quote["quoted_tweet"]["availability"]["state"] == "not_archived"
    store.close()
