"""Which writer may change each field family, one source pair at a time."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.conftest import (
    list_article_rows,
    make_article_result,
    make_photo_media,
    make_tweet_detail_response,
    make_tweet_result,
    make_url_entity,
    make_video_media,
)
from tweetnook.core.extractor import (
    extract_author_fields,
    extract_canonical_text,
    extract_note_tweet_text,
    extract_secondary_objects,
    link_card_has_image,
)
from tweetnook.core.timeline_parsing import TimelineTweet
from tweetnook.enrichment.availability import record_unavailable
from tweetnook.storage import open_archive_store
from tweetnook.storage.ingestion import ImportObservation
from tweetnook.storage.ownership import worker_changes
from tweetnook.storage.record_types import RecordType
from tweetnook.storage.records import PageBuffer
from tweetnook.storage.search_query import search_posts
from tweetnook.web.availability import annotate_web_tweets

LIFECYCLE = (
    "enrichment_state",
    "enrichment_checked_at",
    "enrichment_http_status",
    "enrichment_reason",
    "enrichment_detail",
    "enrichment_retry_count",
    "enrichment_next_retry_at",
    "enrichment_first_unavailable_at",
    "enrichment_retry_eligible",
)


def timeline_tweet(raw: dict[str, Any], *, sort_index: str = "10") -> TimelineTweet:
    author_id, username, display_name = extract_author_fields(raw)
    return TimelineTweet(
        tweet_id=str(raw["rest_id"]),
        text=extract_canonical_text(raw),
        author_id=author_id,
        author_username=username,
        author_display_name=display_name,
        created_at=raw["legacy"].get("created_at"),
        sort_index=sort_index,
        raw_json=raw,
    )


def persist_like_page(store: Any, *raws: dict[str, Any]) -> None:
    store.persist_page(
        operation="Likes",
        collection_type="like",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[timeline_tweet(raw) for raw in raws],
        last_head_tweet_id=None,
        backfill_cursor=None,
        backfill_incomplete=False,
    )


def persist_from(store: Any, source: str, raw: dict[str, Any]) -> None:
    """Store a post through the public writer for each provider."""
    tweet = timeline_tweet(raw)
    if source == "live_graphql":
        store.persist_tweet_detail(tweet=tweet, raw_json=make_tweet_detail_response([raw]))
    elif source == "fxtwitter":
        store.persist_fx_status(tweet=tweet, payload={"status": {"type": "status"}})
    else:
        store.ingest_import_batch([ImportObservation(timeline_tweet=tweet)], membership="tweet")


def mark_not_found(store: Any, tweet_id: str) -> None:
    store.persist_unavailable_tweet(
        tweet_id=tweet_id,
        operation="TweetDetail",
        raw_json={"errors": [{"message": "not found"}]},
        http_status=404,
        reason="not_found",
        detail="gone",
        retry_eligible=True,
        next_retry_at="2099-01-01T00:00:00+00:00",
        retry_count=2,
        checked_at="2026-05-01T00:00:00+00:00",
    )


def lifecycle(store: Any, tweet_id: str) -> dict[str, Any]:
    row = store.current_objects([tweet_id])[tweet_id]
    return {field: row[field] for field in LIFECYCLE}


@pytest.mark.parametrize("replayed_capture", ["membership", "saved_thread_detail"])
def test_replay_keeps_a_newer_unavailable_observation(paths, replayed_capture: str) -> None:
    store = open_archive_store(paths, create=True)
    if replayed_capture == "membership":
        persist_like_page(store, make_tweet_result("100", "saved text"))
    else:
        # Rehydration starts from memberships, so it needs one to reach the saved details.
        persist_like_page(store, make_tweet_result("900", "unrelated"))
        raw = make_tweet_result("100", "saved text")
        store.persist_thread_detail(
            focal_tweet_id="100",
            tweets=[timeline_tweet(raw)],
            raw_json=make_tweet_detail_response([raw]),
        )
    mark_not_found(store, "100")
    expected = lifecycle(store, "100")
    assert expected["enrichment_state"] == "terminal_unavailable"
    assert str(expected["enrichment_http_status"]) == "404"

    store.rehydrate_from_raw_json()

    assert lifecycle(store, "100") == expected
    store.close()


def test_a_real_response_still_ends_an_unavailable_state(paths) -> None:
    store = open_archive_store(paths, create=True)
    persist_like_page(store, make_tweet_result("100", "saved text"))
    mark_not_found(store, "100")

    persist_like_page(store, make_tweet_result("100", "saved text"))

    state = lifecycle(store, "100")
    assert state["enrichment_state"] == "resurrected"
    assert str(state["enrichment_http_status"]) == "200"
    assert state["enrichment_reason"] is None
    store.close()


def test_a_worker_result_cannot_write_another_owners_field() -> None:
    assert worker_changes(RecordType.MEDIA, {"local_path": "a.jpg"}) == {"local_path": "a.jpg"}
    with pytest.raises(ValueError, match="media_url"):
        worker_changes(RecordType.MEDIA, {"media_url": "https://example.com/a.jpg"})
    with pytest.raises(ValueError, match="local_path"):
        worker_changes(RecordType.URL, {"local_path": "a.jpg"})


@pytest.mark.parametrize("stale_timestamp", [None, 0])
def test_rehydration_repairs_a_date_and_its_timestamp_together(paths, stale_timestamp) -> None:
    store = open_archive_store(paths, create=True)
    persist_like_page(store, make_tweet_result("100", "saved text"))
    row = store.conn.execute("SELECT * FROM archive WHERE row_key = 'tweet:like::100'").fetchone()
    store.merge_rows([{**dict(row), "created_at": None, "created_at_ts": stale_timestamp}])

    assert store.rehydrate_from_raw_json().tweets_updated == 1

    repaired = store.conn.execute(
        "SELECT created_at, created_at_ts FROM archive WHERE row_key = 'tweet:like::100'"
    ).fetchone()
    # 2026-03-14T00:00:00Z, the date make_tweet_result writes.
    assert tuple(repaired) == ("Sat Mar 14 00:00:00 +0000 2026", 1773446400)
    assert [row["tweet_id"] for row in search_posts(store, "since:2026-03-01").rows] == ["100"]
    store.close()


@pytest.mark.parametrize(
    ("operation", "expected_source"),
    [("FxTwitterStatus", "fxtwitter"), ("TweetDetailResurrection", "live_graphql")],
)
def test_an_unavailable_capture_names_the_provider_that_answered(
    paths, operation: str, expected_source: str
) -> None:
    store = open_archive_store(paths, create=True)
    persist_like_page(store, make_tweet_result("100", "saved text"))

    record_unavailable(
        store,
        tweet_id="100",
        operation=operation,
        payload={"code": 404},
        http_status=404,
        reason="not_found",
        detail=None,
    )

    captures = store.list_target_captures("100", (operation,))
    assert [row["source"] for row in captures] == [expected_source]
    # The body still came from the authenticated page.
    assert store.current_objects(["100"])["100"]["source"] == "live_graphql"
    store.close()


def _two_videos(first: str, second: str) -> dict[str, Any]:
    return make_tweet_result(
        "100",
        "two videos",
        media=[
            make_video_media(
                first,
                f"https://pbs.twimg.com/{first}.jpg",
                bitrate_url="https://video.twimg.com/a.mp4",
            ),
            make_video_media(
                second,
                f"https://pbs.twimg.com/{second}.jpg",
                bitrate_url="https://video.twimg.com/b.mp4",
            ),
        ],
    )


def _media_positions(store: Any) -> dict[str, tuple[str, str]]:
    return {
        row["media_key"]: (str(row["position"]), row["provenance_source"])
        for row in store.conn.execute(
            "SELECT media_key, position, provenance_source FROM archive WHERE record_type = 'media'"
        )
    }


@pytest.mark.parametrize(
    ("first_source", "second_source", "expected"),
    [
        # A weaker source can't reorder what a stronger one saved.
        ("live_graphql", "x_archive", {"7_1": "0", "7_2": "1"}),
        ("live_graphql", "fxtwitter", {"7_1": "0", "7_2": "1"}),
        ("fxtwitter", "x_archive", {"7_1": "0", "7_2": "1"}),
        # A stronger or equal source's order wins.
        ("x_archive", "live_graphql", {"7_1": "1", "7_2": "0"}),
        ("live_graphql", "live_graphql", {"7_1": "1", "7_2": "0"}),
    ],
)
def test_media_position_follows_the_winning_source(
    paths, first_source: str, second_source: str, expected: dict[str, str]
) -> None:
    store = open_archive_store(paths, create=True)
    persist_from(store, first_source, _two_videos("7_1", "7_2"))
    persist_from(store, second_source, _two_videos("7_2", "7_1"))

    positions = _media_positions(store)
    assert {key: value[0] for key, value in positions.items()} == expected
    store.close()


def test_export_keeps_each_video_with_its_own_geometry_after_a_weaker_copy(paths) -> None:
    def video(key: str, width: int, height: int) -> dict[str, Any]:
        media = make_video_media(
            key,
            f"https://pbs.twimg.com/{key}.jpg",
            bitrate_url=f"https://video.twimg.com/{key}.mp4",
        )
        media["video_info"]["aspect_ratio"] = [width, height]
        return media

    store = open_archive_store(paths, create=True)
    landscape, portrait = video("7_1", 16, 9), video("7_2", 9, 16)
    persist_like_page(store, make_tweet_result("100", "two videos", media=[landscape, portrait]))
    persist_from(
        store, "x_archive", make_tweet_result("100", "two videos", media=[portrait, landscape])
    )

    media = store.export_rows("all")[0]["media"]
    assert [item["media_key"] for item in media] == ["7_1", "7_2"]
    assert [item["aspect_ratio"] for item in media] == [[16, 9], [9, 16]]
    store.close()


URL_ENTITY = make_url_entity(
    "https://t.co/a", "https://example.com/story", display_url="example.com/story"
)


def _url_row(store: Any) -> dict[str, Any]:
    return store.list_url_rows()[0]


def _store_with_unfurl_result(paths, *, failed: bool) -> Any:
    store = open_archive_store(paths, create=True)
    persist_like_page(store, make_tweet_result("100", "linked", urls=[URL_ENTITY]))
    store.merge_rows(
        [
            store.build_url_unfurl_update(
                _url_row(store),
                http_status=503 if failed else 200,
                final_url=None if failed else "https://www.example.com/final",
                canonical_url=_url_row(store)["canonical_url"]
                if failed
                else "https://www.example.com/clean",
                title=None if failed else "Fetched title",
                description=None if failed else "Fetched description",
                site_name=None if failed else "Fetched site",
                content_type=None if failed else "text/html",
                unfurl_state="failed" if failed else "done",
                last_fetched_at="2026-05-01T00:00:00+00:00",
                download_error="HTTP 503" if failed else None,
            )
        ]
    )
    return store


def _reingest(store: Any, source: str) -> None:
    persist_from(store, source, make_tweet_result("100", "linked", urls=[URL_ENTITY]))


UNFURL_FIELDS = (
    "url",
    "final_url",
    "canonical_url",
    "http_status",
    "title",
    "description",
    "site_name",
    "content_type",
    "unfurl_state",
)


@pytest.mark.parametrize("source", ["live_graphql", "fxtwitter", "x_archive"])
def test_reingesting_a_post_keeps_a_finished_unfurl(paths, source: str) -> None:
    store = _store_with_unfurl_result(paths, failed=False)
    expected = {field: _url_row(store)[field] for field in UNFURL_FIELDS}
    assert expected["final_url"] == "https://www.example.com/final"
    assert str(expected["http_status"]) == "200"
    url_hash = _url_row(store)["url_hash"]

    _reingest(store, source)

    row = _url_row(store)
    assert {field: row[field] for field in UNFURL_FIELDS} == expected
    assert row["url_hash"] == url_hash
    store.close()


@pytest.mark.parametrize("source", ["live_graphql", "x_archive"])
def test_reingesting_a_post_keeps_a_failed_unfurls_evidence(paths, source: str) -> None:
    store = _store_with_unfurl_result(paths, failed=True)

    _reingest(store, source)

    row = store.conn.execute(
        "SELECT unfurl_state, http_status, download_error FROM archive WHERE record_type = 'url'"
    ).fetchone()
    assert (row["unfurl_state"], str(row["http_status"]), row["download_error"]) == (
        "failed",
        "503",
        "HTTP 503",
    )
    store.close()


def test_entity_hints_update_only_a_url_the_worker_has_not_fetched(paths) -> None:
    def hinted(title: str) -> dict[str, Any]:
        return make_tweet_result(
            "100", "linked", urls=[{**URL_ENTITY, "unwound_url": {"title": title}}]
        )

    store = open_archive_store(paths, create=True)
    persist_from(store, "live_graphql", hinted("First hint"))
    persist_from(store, "live_graphql", hinted("Second hint"))
    assert _url_row(store)["title"] == "Second hint"

    row = _url_row(store)
    store.merge_rows(
        [
            store.build_url_unfurl_update(
                row,
                http_status=200,
                final_url=row["canonical_url"],
                canonical_url=row["canonical_url"],
                title="Fetched title",
                description=None,
                site_name=None,
                content_type="text/html",
                unfurl_state="done",
                last_fetched_at="2026-05-01T00:00:00+00:00",
                download_error=None,
            )
        ]
    )
    persist_from(store, "live_graphql", hinted("Later hint"))
    assert _url_row(store)["title"] == "Fetched title"
    store.close()


def _card(url: str, *, image: str | None) -> dict[str, Any]:
    bindings: list[dict[str, Any]] = [
        {"key": "title", "value": {"string_value": "A story"}},
        {"key": "card_url", "value": {"string_value": url}},
    ]
    if image:
        bindings.append(
            {
                "key": "thumbnail_image_original",
                "value": {"image_value": {"url": image, "width": 1200, "height": 628}},
            }
        )
    return {"legacy": {"name": "summary_large_image", "binding_values": bindings}}


def _with_card(card: dict[str, Any]) -> dict[str, Any]:
    return {**make_tweet_result("100", "carded"), "card": card}


def _save_detail_capture(store: Any, raw: dict[str, Any]) -> None:
    store.append_raw_capture("TweetDetail", "100", None, 200, make_tweet_detail_response([raw]))


def _saved_card_images(store: Any) -> list[str]:
    return [
        row["media_url"]
        for row in store.conn.execute(
            "SELECT media_url FROM archive WHERE record_type = 'media' AND source = 'link_card'"
        )
    ]


def test_merging_a_card_back_keeps_a_wide_integer_exact(paths) -> None:
    store = open_archive_store(paths, create=True)
    wide = 1180591620717411303425
    persist_like_page(
        store,
        {**_with_card(_card("https://a.example/story", image="https://pbs.twimg.com/a.jpg"))},
    )
    stripped = _with_card(_card("https://a.example/story", image=None))
    stripped["legacy"]["wide_count"] = wide

    persist_like_page(store, stripped)

    saved = store.conn.execute(
        "SELECT raw_json FROM archive WHERE row_key = 'tweet:like::100' "
        "OR (record_type = 'tweet' AND tweet_id = '100')"
    ).fetchone()[0]
    # The card came back, which means the document was decoded and written again.
    assert "thumbnail_image_original" in saved
    assert json.loads(saved)["legacy"]["wide_count"] == wide
    store.close()


def test_card_restoration_finds_an_older_capture_of_the_same_card(paths) -> None:
    store = open_archive_store(paths, create=True)
    store.backfill_link_card_media()
    persist_like_page(store, _with_card(_card("https://a.example/story", image=None)))
    _save_detail_capture(
        store, _with_card(_card("https://a.example/story", image="https://pbs.twimg.com/a.jpg"))
    )
    # Captured later, but of a different card.
    _save_detail_capture(
        store, _with_card(_card("https://b.example/story", image="https://pbs.twimg.com/b.jpg"))
    )

    assert store.restore_link_card_images() == 1

    assert _saved_card_images(store) == ["https://pbs.twimg.com/a.jpg"]
    store.close()


def test_card_restoration_gives_each_saved_copy_its_own_cards_image(paths) -> None:
    store = open_archive_store(paths, create=True)
    store.backfill_link_card_media()
    persist_like_page(store, _with_card(_card("https://a.example/story", image=None)))
    # The canonical object moved on to another card, also without its image.
    store.conn.execute(
        "UPDATE archive SET raw_json = ? WHERE row_key = 'tweet_object:100'",
        (json.dumps(_with_card(_card("https://b.example/story", image=None))),),
    )
    store.conn.commit()
    _save_detail_capture(
        store, _with_card(_card("https://a.example/story", image="https://pbs.twimg.com/a.jpg"))
    )
    _save_detail_capture(
        store, _with_card(_card("https://b.example/story", image="https://pbs.twimg.com/b.jpg"))
    )

    assert store.restore_link_card_images() == 2

    assert sorted(_saved_card_images(store)) == [
        "https://pbs.twimg.com/a.jpg",
        "https://pbs.twimg.com/b.jpg",
    ]
    store.close()


def test_card_restoration_rejects_another_cards_image(paths) -> None:
    store = open_archive_store(paths, create=True)
    store.backfill_link_card_media()
    persist_like_page(store, _with_card(_card("https://a.example/story", image=None)))
    _save_detail_capture(
        store, _with_card(_card("https://b.example/story", image="https://pbs.twimg.com/b.jpg"))
    )

    assert store.restore_link_card_images() == 0

    assert _saved_card_images(store) == []
    store.close()


LONG_TEXT = "the whole long-form post"
SHORT_TEXT = "the whole long\u2026"
BODY_STATE = {"blocks": [{"text": "article body"}], "entityMap": {}}


def _rich_post(tweet_id: str = "100") -> dict[str, Any]:
    article = make_article_result(
        "article-1", title="Title", preview_text="Preview", plain_text="article body"
    )
    article["content_state"] = BODY_STATE
    return make_tweet_result(tweet_id, SHORT_TEXT, note_text=LONG_TEXT, article=article)


def _sparse_post(tweet_id: str = "100") -> dict[str, Any]:
    return make_tweet_result(
        tweet_id,
        SHORT_TEXT,
        article=make_article_result("article-1", title="Title", preview_text="Preview"),
    )


def _assert_rich_post_saved(store: Any, row_key: str) -> None:
    row = store.conn.execute(
        "SELECT text, note_tweet_text, raw_json FROM archive WHERE row_key = ?", (row_key,)
    ).fetchone()
    raw = json.loads(row["raw_json"])
    assert row["text"] == LONG_TEXT
    assert row["note_tweet_text"] == LONG_TEXT
    assert extract_note_tweet_text(raw) == LONG_TEXT
    assert extract_canonical_text(raw) == LONG_TEXT
    assert raw["article"]["article_results"]["result"]["content_state"] == BODY_STATE


def _assert_rich_article_saved(store: Any) -> None:
    row = store.conn.execute(
        "SELECT status, content_text, raw_json FROM archive WHERE row_key = 'article:100'"
    ).fetchone()
    assert (row["status"], row["content_text"]) == ("body_present", "article body")
    assert json.loads(row["raw_json"])["content_state"] == BODY_STATE


@pytest.mark.parametrize(
    "order",
    [
        pytest.param(("rich root", "sparse attachment"), id="rich-first"),
        pytest.param(("sparse attachment", "rich root"), id="sparse-first"),
    ],
)
def test_one_page_keeps_a_rich_post_whatever_order_its_copies_arrive(paths, order) -> None:
    store = open_archive_store(paths, create=True)
    pages = {
        "rich root": _rich_post(),
        "sparse attachment": make_tweet_result("200", "quoting", quoted_tweet=_sparse_post()),
    }

    persist_like_page(store, *(pages[name] for name in order))

    _assert_rich_post_saved(store, "tweet_object:100")
    _assert_rich_article_saved(store)
    store.close()


@pytest.mark.parametrize(
    ("first", "second"),
    [
        pytest.param("live_graphql", "live_graphql", id="same-source"),
        pytest.param("live_graphql", "fxtwitter", id="stronger-then-weaker"),
        pytest.param("live_graphql", "x_archive", id="strongest-then-weakest"),
        pytest.param("fxtwitter", "live_graphql", id="weaker-then-stronger"),
        pytest.param("x_archive", "live_graphql", id="weakest-then-strongest"),
        pytest.param("x_archive", "fxtwitter", id="weakest-then-weaker"),
    ],
)
def test_a_sparse_copy_keeps_a_rich_post_across_writes(paths, first: str, second: str) -> None:
    store = open_archive_store(paths, create=True)

    persist_from(store, first, _rich_post())
    persist_from(store, second, _sparse_post())

    _assert_rich_post_saved(store, "tweet_object:100")
    _assert_rich_article_saved(store)
    store.close()


def test_a_sparse_page_keeps_a_rich_membership_and_the_web_shows_it(paths) -> None:
    store = open_archive_store(paths, create=True)
    persist_like_page(store, _rich_post())

    persist_like_page(store, _sparse_post())

    _assert_rich_post_saved(store, "tweet:like::100")
    _assert_rich_post_saved(store, "tweet_object:100")
    tweets = store.fetch_tweets_by_ids(["100"], include_objects=True)
    annotate_web_tweets(store, tweets)
    assert tweets[0]["text"] == LONG_TEXT
    assert tweets[0]["article"]["content_text"] == "article body"
    assert tweets[0]["article"]["content_state"] == BODY_STATE
    store.close()


def test_a_replaced_article_keeps_nothing_of_the_old_one(paths) -> None:
    store = open_archive_store(paths, create=True)
    persist_from(store, "live_graphql", _rich_post())

    replacement = make_article_result("article-2", title="Other", preview_text="Other preview")
    persist_from(store, "live_graphql", make_tweet_result("100", "text", article=replacement))

    raw = json.loads(store.current_objects(["100"])["100"]["raw_json"])
    result = raw["article"]["article_results"]["result"]
    assert result["rest_id"] == "article-2"
    assert "content_state" not in result
    store.close()


def _detail_writers() -> list[Any]:
    return [
        pytest.param(
            lambda store, raw: store.persist_tweet_detail(
                tweet=timeline_tweet(raw), raw_json=make_tweet_detail_response([raw])
            ),
            id="tweet-detail",
        ),
        pytest.param(
            lambda store, raw: store.persist_thread_detail(
                focal_tweet_id=str(raw["rest_id"]),
                tweets=[timeline_tweet(raw)],
                raw_json=make_tweet_detail_response([raw]),
            ),
            id="thread-detail",
        ),
    ]


def _membership_raw(store: Any) -> dict[str, Any]:
    row = store.conn.execute("SELECT raw_json FROM archive WHERE row_key = 'tweet:like::100'")
    return json.loads(row.fetchone()["raw_json"])


@pytest.mark.parametrize("write", _detail_writers())
def test_a_stripped_detail_keeps_a_memberships_card_image(paths, write) -> None:
    store = open_archive_store(paths, create=True)
    persist_like_page(
        store, _with_card(_card("https://a.example/story", image="https://pbs.twimg.com/a.jpg"))
    )

    write(store, _with_card(_card("https://a.example/story", image=None)))

    membership = _membership_raw(store)
    assert link_card_has_image(membership)
    assert link_card_has_image(json.loads(store.current_objects(["100"])["100"]["raw_json"]))
    store.close()


@pytest.mark.parametrize("write", _detail_writers())
def test_a_detail_with_another_card_replaces_the_memberships_image(paths, write) -> None:
    store = open_archive_store(paths, create=True)
    persist_like_page(
        store, _with_card(_card("https://a.example/story", image="https://pbs.twimg.com/a.jpg"))
    )

    write(store, _with_card(_card("https://b.example/story", image=None)))

    assert not link_card_has_image(_membership_raw(store))
    store.close()


@pytest.mark.parametrize("write", _detail_writers())
def test_a_sparse_detail_keeps_a_rich_membership(paths, write) -> None:
    store = open_archive_store(paths, create=True)
    persist_like_page(store, _rich_post())

    write(store, _sparse_post())

    _assert_rich_post_saved(store, "tweet:like::100")
    _assert_rich_post_saved(store, "tweet_object:100")
    store.close()


def _named_post(username: str | None) -> dict[str, Any]:
    raw = make_tweet_result("100", "text")
    legacy = raw["core"]["user_results"]["result"]["legacy"]
    if username:
        legacy["screen_name"] = username
    else:
        del legacy["screen_name"]
    return raw


def _article_row(store: Any) -> dict[str, Any]:
    columns = "article_id, title, summary_text, content_text, status"
    row = store.conn.execute(
        f"SELECT {columns} FROM archive WHERE row_key = 'article:100'"
    ).fetchone()
    return dict(row)


def test_a_replaced_article_row_keeps_no_field_of_the_old_one(paths) -> None:
    store = open_archive_store(paths, create=True)
    persist_from(store, "live_graphql", _rich_post())
    replacement = make_article_result("article-2", title="Other", preview_text="Other preview")
    first_seen_at = store.conn.execute(
        "SELECT first_seen_at FROM archive WHERE row_key = 'article:100'"
    ).fetchone()[0]

    persist_from(store, "live_graphql", make_tweet_result("100", "text", article=replacement))

    assert _article_row(store) == {
        "article_id": "article-2",
        "title": "Other",
        "summary_text": "Other preview",
        "content_text": None,
        "status": "preview_only",
    }
    assert (
        store.conn.execute(
            "SELECT first_seen_at FROM archive WHERE row_key = 'article:100'"
        ).fetchone()[0]
        == first_seen_at
    )
    store.close()


def test_a_weaker_copy_of_another_article_fills_no_gap(paths) -> None:
    store = open_archive_store(paths, create=True)
    preview = make_article_result("article-1", title="Title", preview_text="Preview")
    persist_from(store, "live_graphql", make_tweet_result("100", "text", article=preview))
    saved = _article_row(store)
    other = make_article_result(
        "article-2", title="Other", preview_text="Other preview", plain_text="other body"
    )

    persist_from(store, "fxtwitter", make_tweet_result("100", "text", article=other))

    assert _article_row(store) == saved
    store.close()


def test_a_copy_of_an_article_without_an_id_keeps_the_saved_body(paths) -> None:
    store = open_archive_store(paths, create=True)
    persist_from(store, "live_graphql", _rich_post())
    partial = make_article_result("article-1", title="Title", preview_text="Preview")
    del partial["id"], partial["rest_id"]

    persist_from(store, "live_graphql", make_tweet_result("100", "text", article=partial))

    assert _article_row(store)["content_text"] == "article body"
    store.close()


def test_a_replay_claims_nothing_about_an_object_it_creates(paths) -> None:
    store = open_archive_store(paths, create=True)
    attachment = make_tweet_result("200", "attachment")
    persist_like_page(store, make_tweet_result("100", "outer", quoted_tweet=attachment))
    store.conn.execute("DELETE FROM archive WHERE row_key = 'tweet_object:200'")
    store.conn.commit()

    store.rehydrate_from_raw_json()

    saved = lifecycle(store, "200")
    assert saved["enrichment_state"] == "pending"
    assert saved["enrichment_checked_at"] is None
    assert saved["enrichment_http_status"] is None
    assert [row["tweet_id"] for row in store.list_tweet_objects_for_enrichment()] == ["200"]
    store.close()


def test_a_note_without_text_keeps_the_saved_payload_through_a_rehydrate(paths) -> None:
    store = open_archive_store(paths, create=True)
    persist_like_page(store, make_tweet_result("100", SHORT_TEXT, note_text=LONG_TEXT))
    partial = make_tweet_result("100", SHORT_TEXT)
    partial["note_tweet"] = {"note_tweet_results": {"result": {"id": "note-1"}}}

    persist_like_page(store, partial)
    store.rehydrate_from_raw_json()

    row = store.conn.execute(
        "SELECT text, note_tweet_text, raw_json FROM archive WHERE record_type = 'tweet'"
    ).fetchone()
    assert (row["text"], row["note_tweet_text"]) == (LONG_TEXT, LONG_TEXT)
    assert extract_note_tweet_text(json.loads(row["raw_json"])) == LONG_TEXT
    store.close()


def test_a_weaker_detail_fills_a_note_the_membership_lacks(paths) -> None:
    store = open_archive_store(paths, create=True)
    persist_like_page(store, make_tweet_result("100", SHORT_TEXT))
    synced_at = store.conn.execute(
        "SELECT synced_at FROM archive WHERE record_type = 'tweet'"
    ).fetchone()[0]

    persist_from(store, "fxtwitter", make_tweet_result("100", "other", note_text=LONG_TEXT))

    row = store.conn.execute(
        "SELECT text, note_tweet_text, source, synced_at, raw_json FROM archive "
        "WHERE record_type = 'tweet'"
    ).fetchone()
    assert (row["text"], row["note_tweet_text"]) == (LONG_TEXT, LONG_TEXT)
    assert (row["source"], row["synced_at"]) == ("live_graphql", synced_at)
    assert extract_note_tweet_text(json.loads(row["raw_json"])) == LONG_TEXT
    hits = store.conn.execute(
        "SELECT a.record_type FROM archive_fts JOIN archive a ON a.rowid = archive_fts.rowid "
        "WHERE archive_fts MATCH ?",
        ("post",),
    ).fetchall()
    assert "tweet" in [hit[0] for hit in hits]
    store.close()


def test_a_weaker_detail_keeps_the_membership_text_it_has(paths) -> None:
    store = open_archive_store(paths, create=True)
    persist_like_page(store, make_tweet_result("100", SHORT_TEXT, lang="en"))

    persist_from(store, "fxtwitter", make_tweet_result("100", "other", lang="fr"))

    row = store.conn.execute(
        "SELECT text, lang FROM archive WHERE record_type = 'tweet'"
    ).fetchone()
    assert (row["text"], row["lang"]) == (SHORT_TEXT, "en")
    store.close()


def test_overlapping_details_in_one_batch_merge_with_each_other(paths) -> None:
    store = open_archive_store(paths, create=True)
    persist_like_page(store, _named_post("old"))
    buffer = PageBuffer()

    for username in ("new", None):
        raw = _named_post(username)
        store.persist_thread_detail(
            focal_tweet_id="100",
            tweets=[timeline_tweet(raw)],
            raw_json=make_tweet_detail_response([raw]),
            cursor=buffer,
        )
    store.flush(buffer)

    usernames = store.conn.execute(
        "SELECT row_key, author_username FROM archive "
        "WHERE row_key IN ('tweet:like::100', 'tweet_object:100')"
    ).fetchall()
    assert {row["row_key"]: row["author_username"] for row in usernames} == {
        "tweet:like::100": "new",
        "tweet_object:100": "new",
    }
    store.close()


def test_a_detail_refreshes_a_membership_still_waiting_in_its_batch(paths) -> None:
    store = open_archive_store(paths, create=True)
    buffer = PageBuffer()
    store.upsert_membership(timeline_tweet(_named_post("old")), "like", cursor=buffer)

    raw = _named_post("new")
    store.persist_thread_detail(
        focal_tweet_id="100",
        tweets=[timeline_tweet(raw)],
        raw_json=make_tweet_detail_response([raw]),
        cursor=buffer,
    )
    store.flush(buffer)

    row = store.conn.execute(
        "SELECT author_username, collection_type FROM archive WHERE row_key = 'tweet:like::100'"
    ).fetchone()
    assert tuple(row) == ("new", "like")
    # Detail ingestion refreshes memberships and never adds them.
    assert (
        store.conn.execute("SELECT COUNT(*) FROM archive WHERE record_type = 'tweet'").fetchone()[0]
        == 1
    )
    store.close()


def _nested_quotes() -> dict[str, Any]:
    leaf = make_tweet_result(
        "300",
        "leaf",
        media=[make_photo_media("3_77", "https://pbs.twimg.com/leaf.jpg")],
        article=make_article_result("leaf-article", title="Leaf", preview_text="Leaf preview"),
    )
    leaf["card"] = _card("https://leaf.example/story", image="https://pbs.twimg.com/card.jpg")
    middle = make_tweet_result("200", "middle")
    # X embeds a quoted post's own quote under this key.
    middle["nested_quoted_tweet_results"] = {"result": leaf}
    return make_tweet_result("100", "outer", quoted_tweet=middle)


def test_a_quote_of_a_quote_is_saved_from_the_outer_payload_alone(paths) -> None:
    store = open_archive_store(paths, create=True)

    persist_like_page(store, _nested_quotes())

    assert set(store.current_objects(["100", "200", "300"])) == {"100", "200", "300"}
    relations = {
        (row["tweet_id"], row["relation_type"], row["target_tweet_id"])
        for row in store.conn.execute(
            "SELECT tweet_id, relation_type, target_tweet_id FROM archive "
            "WHERE record_type = 'tweet_relation'"
        )
    }
    assert relations == {("100", "quote_of", "200"), ("200", "quote_of", "300")}
    media = {
        (row["tweet_id"], row["source"])
        for row in store.conn.execute(
            "SELECT tweet_id, source FROM archive WHERE record_type = 'media' "
            "AND source != 'article_cover'"
        )
    }
    assert media == {("300", "tweet_media"), ("300", "link_card")}
    assert [row["tweet_id"] for row in list_article_rows(store)] == ["300"]
    # Attached posts never become saved collection items.
    memberships = store.conn.execute(
        "SELECT tweet_id FROM archive WHERE record_type = 'tweet'"
    ).fetchall()
    assert [row["tweet_id"] for row in memberships] == ["100"]
    store.close()


def test_nested_attachments_stop_where_the_web_stops_showing_them(paths) -> None:
    deepest = make_tweet_result("400", "too deep to display")
    third = make_tweet_result("300", "third", quoted_tweet=deepest)
    second = make_tweet_result("200", "second", quoted_tweet=third)
    store = open_archive_store(paths, create=True)

    persist_like_page(store, make_tweet_result("100", "first", quoted_tweet=second))

    assert set(store.current_objects(["100", "200", "300", "400"])) == {"100", "200", "300"}
    store.close()


def test_a_payload_that_quotes_itself_ends_instead_of_looping(paths) -> None:
    first = make_tweet_result("100", "first")
    second = make_tweet_result("200", "second", quoted_tweet=first)
    first["quoted_status_result"] = {"result": second}
    store = open_archive_store(paths, create=True)

    graph = extract_secondary_objects(first)

    assert set(graph.tweet_objects) == {"100", "200"}
    store.close()


def test_a_repost_keeps_its_original_and_that_originals_quote(paths) -> None:
    quote = make_tweet_result("300", "quoted")
    original = make_tweet_result("200", "original", quoted_tweet=quote)
    store = open_archive_store(paths, create=True)

    persist_like_page(store, make_tweet_result("100", "RT", retweeted_tweet=original))

    assert set(store.current_objects(["100", "200", "300"])) == {"100", "200", "300"}
    store.close()
