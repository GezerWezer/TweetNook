from __future__ import annotations

import copy
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tweetnook.storage.backend import ArchiveStore
from tweetnook.web.availability import (
    annotate_web_tweets,
    availability_for_tweet,
    slim_raw_tweet,
    slim_web_tweets,
)
from tweetnook.web.deps import get_store
from tweetnook.web.routes.tweets import router as tweets_router


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


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "web_raw_tweets.json"


def test_abbreviated_fixture_matches_the_current_raw_json_pruning() -> None:
    fixture = json.loads(FIXTURE.read_text())
    tweets = copy.deepcopy(fixture["full"])

    slim_web_tweets(tweets)

    # The JS asset suite renders both sides of this fixture and requires identical output.
    assert tweets == fixture["slim"]


def test_slim_raw_tweet_drops_unrendered_fields_and_keeps_shape() -> None:
    raw = {
        "__typename": "Tweet",
        "rest_id": "1",
        "edit_control": {"edit_tweet_ids": ["1"]},
        "views": {"count": "5", "state": "EnabledWithCount"},
        "core": {
            "user_results": {"result": {"rest_id": "9", "avatar": {}, "legacy": {"name": "A"}}}
        },
        "legacy": {
            "full_text": "hi",
            "lang": "en",
            "entities": {"hashtags": [{"text": "x"}], "urls": [{"url": "u", "indices": [0, 1]}]},
            "retweeted_status_result": {"result": {"rest_id": "2", "source": "web"}},
        },
        "quoted_status_result": {"result": {"rest_id": "3", "is_translatable": False}},
    }

    assert slim_raw_tweet(raw) == {
        "__typename": "Tweet",
        "rest_id": "1",
        "views": {"count": "5"},
        "core": {"user_results": {"result": {"rest_id": "9", "legacy": {"name": "A"}}}},
        "legacy": {
            "full_text": "hi",
            "entities": {"urls": [{"url": "u"}]},
            "retweeted_status_result": {"result": {"rest_id": "2"}},
        },
        "quoted_status_result": {"result": {"rest_id": "3"}},
    }
    assert slim_raw_tweet(None) is None and slim_raw_tweet("text") == "text"


def test_lists_abbreviate_raw_json_and_raw_endpoint_returns_stored_documents(tmp_path) -> None:
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    member_raw = {"rest_id": "1", "edit_control": {"x": 1}, "legacy": {"full_text": "member"}}
    object_raw = {"rest_id": "2", "edit_control": {"x": 2}, "legacy": {"full_text": "context"}}
    store._merge_records(
        [
            store._record(
                row_key="tweet:like::1",
                record_type="tweet",
                tweet_id="1",
                collection_type="like",
                text="member",
                created_at_ts=1,
                raw_json=json.dumps(member_raw),
            ),
            store._record(
                row_key="tweet_object:2",
                record_type="tweet_object",
                tweet_id="2",
                raw_json=json.dumps(object_raw),
            ),
        ]
    )
    app = FastAPI()
    app.include_router(tweets_router)
    app.dependency_overrides[get_store] = lambda: store
    try:
        with TestClient(app) as client:
            listed = client.get("/api/tweets?collection=all&sort=newest").json()["tweets"]
            member = client.get("/api/tweets/1/raw")
            context = client.get("/api/tweets/2/raw")
            missing = client.get("/api/tweets/3/raw")
    finally:
        store.close()

    assert listed[0]["raw_json"] == {"rest_id": "1", "legacy": {"full_text": "member"}}
    assert member.json() == {"tweet_id": "1", "raw_json": member_raw}
    assert context.json() == {"tweet_id": "2", "raw_json": object_raw}
    assert missing.status_code == 404
