from __future__ import annotations

from tweetnook.core.timeline_parsing import TimelineTweet
from tweetnook.stats import build_stats_section
from tweetnook.storage import ArchiveStore
from tweetnook.storage.search_query import count_posts

OWNER = "owner"
LONG_TEXT = "a saved post with enough words to read like a real one"  # 12 words


def _tweet(
    tweet_id, author, created_at, text, *, likes=0, lang="en", reply=False, tags=(), mentions=()
):
    legacy = {
        "full_text": text,
        "created_at": created_at,
        "favorite_count": likes,
        "lang": lang,
        "in_reply_to_status_id_str": "99" if reply else None,
        "entities": {
            "hashtags": [{"text": tag} for tag in tags],
            "user_mentions": [{"screen_name": name} for name in mentions],
        },
    }
    return TimelineTweet(
        tweet_id=tweet_id,
        text=text,
        author_id=author,
        author_username=f"{author}_name",
        author_display_name=author.title(),
        created_at=created_at,
        sort_index=tweet_id,
        raw_json={"rest_id": tweet_id, "legacy": legacy},
    )


def _save(store, collection, tweets):
    store.persist_page(
        operation=collection,
        collection_type=collection,
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"synthetic": True},
        tweets=tweets,
        last_head_tweet_id=tweets[-1].tweet_id,
        backfill_cursor=None,
        backfill_incomplete=False,
    )


def _seed(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    store.set_archive_owner_id(OWNER)
    # 2020-03-01 is a Sunday and 2020-03-02 a Monday.
    first = _tweet(
        "1",
        "alice",
        "Sun Mar 01 10:00:00 +0000 2020",
        LONG_TEXT,
        likes=500,
        tags=("Cats",),
        mentions=("Bob",),
    )
    reply = _tweet(
        "2", "alice", "Mon Mar 02 23:30:00 +0000 2020", LONG_TEXT, likes=3, lang="es", reply=True
    )
    gem = _tweet(
        "3",
        "bob",
        "Wed Jan 05 08:00:00 +0000 2022",
        LONG_TEXT,
        likes=7,
        lang="und",
        tags=("cats", "dogs"),
    )
    _save(store, "like", [first, reply])
    _save(store, "bookmark", [first, gem])
    _save(
        store,
        "tweet",
        [
            _tweet("4", OWNER, "Sat Jun 10 12:00:00 +0000 2023", "one two three"),
            _tweet("5", OWNER, "Sun Jun 11 12:00:00 +0000 2023", "four five"),
            _tweet("6", OWNER, "Tue Jun 13 12:00:00 +0000 2023", "six"),
        ],
    )
    store.merge_rows(
        [
            {
                "row_key": "url:h1",
                "record_type": "url",
                "url_hash": "h1",
                "url_host": "www.example.com",
            },
            {"row_key": "url:h2", "record_type": "url", "url_hash": "h2", "url_host": "x.com"},
            {
                "row_key": "url_ref:1:0",
                "record_type": "url_ref",
                "tweet_id": "1",
                "url_hash": "h1",
                "position": "0",
            },
            {
                "row_key": "url_ref:3:0",
                "record_type": "url_ref",
                "tweet_id": "3",
                "url_hash": "h2",
                "position": "0",
            },
            {
                "row_key": "media:1:v",
                "record_type": "media",
                "tweet_id": "1",
                "media_type": "video",
                "duration_millis": "90500",
            },
            {
                "row_key": "media:3:p",
                "record_type": "media",
                "tweet_id": "3",
                "media_type": "photo",
            },
        ]
    )
    return store


def test_highlights_count_each_saved_post_once(tmp_path):
    store = _seed(tmp_path)
    try:
        data = build_stats_section(store, "insights").data
    finally:
        store.close()

    activity = data["activity"]
    assert {name: series["total"] for name, series in activity.items()} == {
        "all": 6,
        "like": 2,
        "bookmark": 2,
        "tweet": 3,
    }
    assert activity["all"]["by_year"] == [
        {"year": 2020, "count": 2},
        {"year": 2022, "count": 1},
        {"year": 2023, "count": 3},
    ]
    week = activity["all"]["week_hours"]
    assert len(week) == 168 and sum(week) == 6
    # Sunday 10:00 and Monday 23:00, with buckets starting Sunday 00:00 UTC.
    assert (week[10], week[24 + 23]) == (1, 1)
    # Days since 1970-01-01 in UTC: 2020-03-01 is 18322; 2023-06-10, 11 and 13 are 19518,
    # 19519 and 19521.
    all_days = activity["all"]["days"]
    assert sum(count for _day, count in all_days) == 6
    assert all_days[0] == [18322, 1]
    assert [day for day, _count in all_days] == sorted(day for day, _count in all_days)
    assert activity["tweet"]["days"] == [[19518, 1], [19519, 1], [19521, 1]]
    # Every day holds one post, so the earliest wins the tie.
    assert data["busiest_day"] == {"date": "2020-03-01", "count": 1}
    assert data["longest_streak"] == {"days": 2, "start": "2023-06-10", "end": "2023-06-11"}

    assert [
        (a["username"], a["count"], a["likes"], a["bookmarks"]) for a in data["top_authors"]
    ] == [
        ("alice_name", 2, 2, 1),
        ("bob_name", 1, 0, 1),
    ]
    assert data["authors"] == {"total": 2, "one_time": 1, "regulars": 0}
    assert data["post_mix"]["total"] == 6
    assert data["post_mix"]["replies"] == 1
    assert (data["post_mix"]["with_media"], data["post_mix"]["with_links"]) == (2, 2)
    assert data["likes_on_x"] == 500 + 3 + 7
    assert data["saved_twice"] == 1

    assert data["hashtags"] == [{"tag": "cats", "count": 2}, {"tag": "dogs", "count": 1}]
    assert data["mentions"] == [{"username": "bob", "count": 1}]
    assert data["media_types"] == {"photo": 1, "video": 1}
    assert data["video_seconds"] == 90
    assert data["words"] == 12 * 3 + 3 + 2 + 1


def test_highlights_on_an_empty_archive(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    try:
        data = build_stats_section(store, "insights").data
    finally:
        store.close()
    assert data["activity"] == {
        "all": {"total": 0, "by_year": [], "week_hours": [0] * 168, "days": []}
    }
    assert data["busiest_day"] is None
    assert data["longest_streak"] == {"days": 0, "start": None, "end": None}
    assert data["top_authors"] == [] and data["words"] == 0
    assert data["context"] == {
        "posts": 0,
        "words": 0,
        "media_types": {},
        "video_seconds": 0,
        "activity": {"total": 0, "by_year": [], "week_hours": [0] * 168, "days": []},
        "reply_authors": [],
    }


def test_highlights_leave_out_conversation_context(tmp_path):
    store = _seed(tmp_path)
    try:
        _save(
            store,
            "like",
            [_tweet("7", "carol", "Fri Jan 07 08:00:00 +0000 2022", "big one", likes=120_000)],
        )
        with store.transaction():
            for tweet_id, state, reason in (
                ("1", "terminal_unavailable", "deleted_by_author"),
                ("3", "terminal_unavailable", "suspended_account"),
                ("2", "resurrected", None),
            ):
                store.conn.execute(
                    "UPDATE archive SET enrichment_state = ?, enrichment_reason = ? "
                    "WHERE row_key = ?",
                    (state, reason, f"tweet_object:{tweet_id}"),
                )
        store.merge_rows(
            [
                # Post 3 is hidden, so `is:missing` wouldn't show it either.
                {"row_key": "tweet_hidden:3", "record_type": "tweet_hidden", "tweet_id": "3"},
                # A parent kept as context: unavailable, with a video and a link of its own.
                {
                    "row_key": "tweet_object:90",
                    "record_type": "tweet_object",
                    "tweet_id": "90",
                    "author_id": "frank",
                    "text": "context parent",
                    "lang": "fr",
                    "enrichment_state": "terminal_unavailable",
                    "enrichment_reason": "deleted_by_author",
                },
                # A reply kept as context, linking to a site the saved posts also link to.
                {
                    "row_key": "tweet_object:91",
                    "record_type": "tweet_object",
                    "tweet_id": "91",
                    "text": "a reply with five words",
                    "lang": "en",
                    "author_id": "dave",
                    "author_username": "dave_x",
                    "author_display_name": "Dave",
                    "conversation_id": "90",
                    # Sunday 2020-03-01 10:00 UTC.
                    "created_at_ts": 18322 * 86400 + 10 * 3600,
                },
                {
                    "row_key": "media:91:g",
                    "record_type": "media",
                    "tweet_id": "91",
                    "media_type": "animated_gif",
                },
                {
                    "row_key": "url_ref:91:0",
                    "record_type": "url_ref",
                    "tweet_id": "91",
                    "url_hash": "h1",
                    "position": "0",
                },
                {
                    "row_key": "media:90:v",
                    "record_type": "media",
                    "tweet_id": "90",
                    "media_type": "video",
                    "duration_millis": "600000",
                },
                {
                    "row_key": "url:h9",
                    "record_type": "url",
                    "url_hash": "h9",
                    "url_host": "context.example",
                },
                {
                    "row_key": "url_ref:90:0",
                    "record_type": "url_ref",
                    "tweet_id": "90",
                    "url_hash": "h9",
                    "position": "0",
                },
                # Dave's second reply, and the owner's own, which isn't ranked.
                {
                    "row_key": "tweet_object:92",
                    "record_type": "tweet_object",
                    "tweet_id": "92",
                    "author_id": "dave",
                    "conversation_id": "90",
                },
                {
                    "row_key": "tweet_object:93",
                    "record_type": "tweet_object",
                    "tweet_id": "93",
                    "author_id": OWNER,
                    "conversation_id": "90",
                },
                # Frank wrote post 90, so his reply in its conversation continues his
                # own thread and isn't ranked.
                {
                    "row_key": "tweet_object:95",
                    "record_type": "tweet_object",
                    "tweet_id": "95",
                    "author_id": "frank",
                    "author_username": "frank_x",
                    "conversation_id": "90",
                },
                # Erin answers her own post 94 inside Gina's conversation, which isn't
                # replying either; her answer to Dave's post 92 is.
                {
                    "row_key": "tweet_object:96",
                    "record_type": "tweet_object",
                    "tweet_id": "96",
                    "author_id": "erin",
                    "author_username": "erin_x",
                    "conversation_id": "97",
                },
                {
                    "row_key": "tweet_relation:96:reply_to:94",
                    "record_type": "tweet_relation",
                    "tweet_id": "96",
                    "target_tweet_id": "94",
                    "relation_type": "reply_to",
                },
                {
                    "row_key": "tweet_object:98",
                    "record_type": "tweet_object",
                    "tweet_id": "98",
                    "author_id": "erin",
                    "author_username": "erin_x",
                    "conversation_id": "97",
                },
                {
                    "row_key": "tweet_relation:98:reply_to:92",
                    "record_type": "tweet_relation",
                    "tweet_id": "98",
                    "target_tweet_id": "92",
                    "relation_type": "reply_to",
                },
                {
                    "row_key": "tweet_object:97",
                    "record_type": "tweet_object",
                    "tweet_id": "97",
                    "author_id": "gina",
                    "conversation_id": "97",
                },
                # Alice and Bob, whose posts are saved, reply in Frank's conversation.
                {
                    "row_key": "tweet_object:88",
                    "record_type": "tweet_object",
                    "tweet_id": "88",
                    "author_id": "alice",
                    "author_username": "alice_name",
                    "conversation_id": "90",
                },
                {
                    "row_key": "tweet_object:89",
                    "record_type": "tweet_object",
                    "tweet_id": "89",
                    "author_id": "bob",
                    "author_username": "bob_name",
                    "conversation_id": "90",
                },
                # Erin started a conversation, so her post isn't a reply.
                {
                    "row_key": "tweet_object:94",
                    "record_type": "tweet_object",
                    "tweet_id": "94",
                    "author_id": "erin",
                    "conversation_id": "94",
                },
                # A like from an official X export, gone before its content was fetched.
                {
                    "row_key": "tweet:like::8",
                    "record_type": "tweet",
                    "tweet_id": "8",
                    "collection_type": "like",
                    "source": "x_archive",
                },
                {
                    "row_key": "tweet_object:8",
                    "record_type": "tweet_object",
                    "tweet_id": "8",
                    "enrichment_state": "terminal_unavailable",
                    "enrichment_reason": "suspended_account",
                },
            ]
        )
        data = build_stats_section(store, "insights").data
        # The card's buttons search for these, so the counts must match the results.
        missing = count_posts(store, "is:missing")
        missing_uncaptured = count_posts(store, "is:missing is:uncaptured")
    finally:
        store.close()

    assert (missing, missing_uncaptured) == (1, 1)
    # Only post 1 is kept in full; post 8 is uncaptured, so neither it nor its reason counts.
    assert data["gone"] == {
        "saved": 1,
        "context": 1,
        "resurrected": 1,
        "reasons": [
            {"reason": "deleted_by_author", "label": "Deleted by author", "count": 1},
        ],
    }
    assert data["viral"] == 1
    assert data["media_types"] == {"photo": 1, "video": 1}
    assert data["video_seconds"] == 90

    # Context is totalled apart. Post 8 is saved (an export like), so it isn't context;
    # the context video is 600 seconds.
    assert data["context"] == {
        "posts": 11,
        "words": 2 + 5,
        "media_types": {"video": 1, "animated_gif": 1},
        "video_seconds": 600,
        "reply_authors": [
            {"author_id": "dave", "username": "dave_x", "display_name": "Dave", "count": 2},
            {
                "author_id": "alice",
                "username": "alice_name",
                "display_name": "alice_name",
                "count": 1,
            },
            {"author_id": "bob", "username": "bob_name", "display_name": "bob_name", "count": 1},
            {"author_id": "erin", "username": "erin_x", "display_name": "erin_x", "count": 1},
        ],
        # Post 90 has no posting time, so only post 91 is charted.
        "activity": {
            "total": 1,
            "by_year": [{"year": 2020, "count": 1}],
            "week_hours": [1 if hour == 10 else 0 for hour in range(168)],
            "days": [[18322, 1]],
        },
    }
