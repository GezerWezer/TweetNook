"""What a later write must leave of a saved row: content, deletion, timestamps and captures."""

from __future__ import annotations

import json
from typing import Any

from tests.conftest import make_photo_media, make_tweet_result
from tests.storage.test_field_ownership import (
    lifecycle,
    mark_not_found,
    persist_like_page,
    timeline_tweet,
)
from tweetnook.core.timeline_parsing import TimelineTweet
from tweetnook.storage import open_archive_store, records
from tweetnook.storage.ingestion import ImportObservation


def _row(store: Any, row_key: str) -> dict[str, Any]:
    return dict(
        store.conn.execute("SELECT * FROM archive WHERE row_key = ?", (row_key,)).fetchone()
    )


def _like_row(store: Any, tweet_id: str) -> dict[str, Any]:
    return _row(store, store._row_key_for_tweet(tweet_id, "like"))


def test_an_empty_string_from_the_same_source_keeps_saved_content(paths) -> None:
    store = open_archive_store(paths, create=True)
    persist_like_page(store, make_tweet_result("100", "saved text"))
    saved = _like_row(store, "100")
    assert saved["text"] == "saved text"
    assert saved["author_display_name"]

    # The same provider answering with blank strings is a sparser payload, not a correction.
    blank = TimelineTweet(
        tweet_id="100",
        text="",
        author_id="",
        author_username="",
        author_display_name="",
        created_at="",
        sort_index="10",
        raw_json=make_tweet_result("100", ""),
    )
    store.persist_page(
        operation="Likes",
        collection_type="like",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[blank],
        last_head_tweet_id=None,
        backfill_cursor=None,
        backfill_incomplete=False,
    )

    after = _like_row(store, "100")
    for field in ("text", "author_id", "author_username", "author_display_name", "created_at"):
        assert after[field] == saved[field], field
    store.close()


def test_reimporting_an_archive_post_keeps_its_deletion(paths) -> None:
    store = open_archive_store(paths, create=True)
    tweet = timeline_tweet(make_tweet_result("100", "deleted post"))
    store.ingest_import_batch(
        [ImportObservation(timeline_tweet=tweet, deleted_at="2026-02-01T00:00:00+00:00")],
        membership="tweet",
    )
    row_key = store._row_key_for_tweet("100", "tweet")
    assert _row(store, row_key)["deleted_at"] == "2026-02-01T00:00:00+00:00"

    # A later archive that no longer lists the post as deleted is not evidence it came back:
    # only an available provider (live X or FxTwitter) can clear a deletion.
    store.ingest_import_batch([ImportObservation(timeline_tweet=tweet)], membership="tweet")

    assert _row(store, row_key)["deleted_at"] == "2026-02-01T00:00:00+00:00"
    store.close()


def test_fxtwitter_resurrects_a_post_live_x_marked_unavailable(paths) -> None:
    store = open_archive_store(paths, create=True)
    mark_not_found(store, "100")
    unavailable = store.current_objects(["100"])["100"]
    assert unavailable["source"] == "live_graphql"
    assert not unavailable["text"]

    store.persist_fx_status(
        tweet=timeline_tweet(make_tweet_result("100", "recovered text")),
        payload={"status": {"type": "status"}},
    )

    assert store.current_objects(["100"])["100"]["text"] == "recovered text"
    assert lifecycle(store, "100")["enrichment_state"] == "resurrected"
    store.close()


def test_a_quoted_copy_fills_a_post_live_x_marked_unavailable(paths) -> None:
    store = open_archive_store(paths, create=True)
    mark_not_found(store, "200")
    assert store.current_objects(["200"])["200"]["source"] == "live_graphql"

    # FxTwitter ranks below live X, but the live row holds only an error payload, so the
    # quoted copy inside another post's FxTwitter answer is the better record.
    quoting = make_tweet_result(
        "100", "quoting post", quoted_tweet=make_tweet_result("200", "quoted text")
    )
    store.persist_fx_status(tweet=timeline_tweet(quoting), payload={"status": {"type": "status"}})

    recovered = store.current_objects(["200"])["200"]
    assert recovered["text"] == "quoted text"
    assert recovered["source"] == "fxtwitter"
    assert json.loads(recovered["raw_json"])["rest_id"] == "200"
    store.close()


def test_a_weaker_copy_leaves_a_live_post_that_has_no_text(paths) -> None:
    store = open_archive_store(paths, create=True)
    photo_only = make_tweet_result(
        "200", "", media=[make_photo_media("3_1", "https://pbs.twimg.com/live.jpg")]
    )
    persist_like_page(store, photo_only)
    assert store.current_objects(["200"])["200"]["source"] == "live_graphql"

    # Only an unavailable row gives way: empty text on a live post is the post itself.
    quoting = make_tweet_result(
        "100", "quoting post", quoted_tweet=make_tweet_result("200", "fx text")
    )
    store.persist_fx_status(tweet=timeline_tweet(quoting), payload={"status": {"type": "status"}})

    kept = store.current_objects(["200"])["200"]
    assert kept["source"] == "live_graphql"
    assert json.loads(kept["raw_json"]) == photo_only
    store.close()


def test_a_resync_keeps_when_a_post_was_first_seen_and_added(paths, monkeypatch) -> None:
    clock = {"now": "2026-01-01T00:00:00+00:00"}
    monkeypatch.setattr(records, "utc_now", lambda: clock["now"])
    store = open_archive_store(paths, create=True)
    persist_like_page(store, make_tweet_result("100", "saved text"))

    clock["now"] = "2026-06-01T00:00:00+00:00"
    persist_like_page(store, make_tweet_result("100", "saved text"))

    row = _like_row(store, "100")
    assert row["first_seen_at"] == "2026-01-01T00:00:00+00:00"
    assert row["added_at"] == "2026-01-01T00:00:00+00:00"
    assert row["last_seen_at"] == "2026-06-01T00:00:00+00:00"
    store.close()


def test_a_page_saves_its_raw_capture_as_received(paths) -> None:
    store = open_archive_store(paths, create=True)
    response = {"data": {"page": 2}, "note": "verbatim"}
    store.persist_page(
        operation="Likes",
        collection_type="like",
        cursor_in="cursor-a",
        cursor_out="cursor-b",
        http_status=206,
        raw_json=response,
        tweets=[timeline_tweet(make_tweet_result("100", "saved text"))],
        last_head_tweet_id="100",
        backfill_cursor="cursor-b",
        backfill_incomplete=True,
    )

    capture = dict(
        store.conn.execute("SELECT * FROM archive WHERE record_type = 'raw_capture'").fetchone()
    )
    assert capture["operation"] == "Likes"
    assert capture["cursor_in"] == "cursor-a"
    assert capture["cursor_out"] == "cursor-b"
    assert str(capture["http_status"]) == "206"
    assert capture["source"] == "live_graphql"
    assert json.loads(capture["raw_json"]) == response
    store.close()
