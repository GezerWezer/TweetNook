from __future__ import annotations

import hashlib
import json

import pytest

from tests.conftest import make_tweet_result
from tests.test_storage import _tweet
from tests.test_web_tweets_api import _list_tweets
from tweetnook.archive_import import _import_likes
from tweetnook.like_order import merge_missing, repair_archive_blocks
from tweetnook.search import SearchQueryError, search_posts
from tweetnook.storage.backend import ARCHIVE_SOURCE, LIVE_SOURCE, ArchiveStore, _PageBuffer


@pytest.fixture
def store(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    yield store
    store.close()


def membership(store, tweet_id, *, sort_index=None, source=ARCHIVE_SOURCE, text="needle"):
    tweet = _tweet(tweet_id)
    tweet.text = text
    buffer = _PageBuffer()
    store.upsert_tweet(tweet, cursor=buffer)
    store.upsert_membership(tweet_id, "like", source=source, sort_index=sort_index, cursor=buffer)
    store.merge_rows(list(buffer.records.values()))


def capture(store, ids, *, indices=None, cursor_in=None, cursor_out=None):
    entries = []
    for index, tweet_id in enumerate(ids):
        entry = {
            "entryId": f"tweet-{tweet_id}",
            "content": {
                "itemContent": {"tweet_results": {"result": make_tweet_result(tweet_id, "needle")}}
            },
        }
        if indices is not None:
            entry["sortIndex"] = indices[index]
        entries.append(entry)
    store.append_raw_capture(
        "Likes",
        cursor_in,
        cursor_out,
        200,
        {"data": {"timeline": {"instructions": [{"entries": entries}]}}},
    )


def archive_capture(store, parts, *, digest="archive", generation="2026-01-01", missing=()):
    filenames = [f"data/like-part{index}.js" for index in range(len(parts))]
    store.set_import_manifest(digest, archive_generation_date=generation, status="completed")
    payloads = [
        (
            "XArchiveManifest",
            "data/manifest.js",
            {"dataTypes": {"like": {"files": [{"fileName": name} for name in filenames]}}},
        ),
        *[("XArchiveLikes", name, part) for name, part in zip(filenames, parts, strict=True)],
    ]
    for operation, filename, payload in payloads:
        if filename in missing:
            continue
        key = hashlib.sha256(f"{digest}\0{operation}\0{filename}".encode()).hexdigest()
        store.append_raw_capture(
            operation, filename, None, 200, payload, source=ARCHIVE_SOURCE, capture_key=key
        )


def likes(ids):
    return [{"like": {"tweetId": tweet_id, "fullText": "needle"}} for tweet_id in ids]


def scrambled_301():
    # The short final block is encountered in the middle of the depth-first file.
    blocks = [list(range(start, min(start + 25, 301))) for start in range(0, 301, 25)]
    return [item for block in [0, 1, 10, 11, 12, 2, 3, 4, 5, 6, 7, 8, 9] for item in blocks[block]]


@pytest.mark.parametrize("size", [0, 1, 24, 25, 26, 100, 249, 250])
def test_small_archives_keep_order(size):
    original = list(range(size))
    assert repair_archive_blocks(original) == original


def test_repairs_known_block_layout_without_mutating_or_sorting_within_blocks():
    expected = list(range(301))
    scrambled = scrambled_301()
    # Within-block disorder must survive the block repair.
    scrambled[0], scrambled[1] = scrambled[1], scrambled[0]
    expected[0], expected[1] = expected[1], expected[0]
    original = list(scrambled)
    assert repair_archive_blocks(scrambled) == expected
    assert scrambled == original


@pytest.mark.parametrize(
    ("preferred", "fallback", "expected"),
    [
        ("AC", "ABCDE", "ABCDE"),
        ("DCA", "ABCDE", "DCABE"),
        ("AB", "XY", "ABXY"),
        ("", "ABA", "AB"),
        ("ABAC", "BBDD", "ABDC"),
    ],
)
def test_anchor_merge_preserves_authoritative_order(preferred, fallback, expected):
    assert merge_missing(list(preferred), list(fallback)) == list(expected)


def test_archive_import_and_missing_live_index_do_not_erase_live_sort_index(store):
    membership(store, "100", sort_index="999", source=LIVE_SOURCE)
    _import_likes(store, likes(["100"]), counts={"likes": 0})
    row = store._get_row(store._row_key_for_tweet("100", "like"))
    assert row["sort_index"] == "999"
    membership(store, "100", source=LIVE_SOURCE)
    assert store._get_row(store._row_key_for_tweet("100", "like"))["sort_index"] == "999"


def test_graphql_anchors_archive_gaps_and_restores_previously_overwritten_indices(store):
    for index, tweet_id in enumerate("ABCDE", 1):
        membership(store, tweet_id, sort_index=str(-index), source=LIVE_SOURCE)
    archive_capture(store, [likes(list("ABCDE"))])
    capture(store, ["A", "C", "E"], indices=["900", "800", "700"])
    assert store.get_like_order().ordered_ids("liked_latest") == list("ABCDE")
    assert store.get_like_order().ordered_ids("liked_earliest") == list("EDCBA")
    # Recovery is derived and does not rewrite the old content/raw records.
    assert store._get_row(store._row_key_for_tweet("A", "like"))["sort_index"] == "-1"


def test_multipart_repair_happens_before_filtering_missing_memberships(store):
    for tweet_id in ("49", "50", "249", "250", "300"):
        membership(store, tweet_id)
    raw = likes([str(value) for value in scrambled_301()])
    archive_capture(store, [raw[:73], raw[73:]])
    assert store.get_like_order().ordered_ids("liked_latest") == ["49", "50", "249", "250", "300"]


def test_incomplete_archive_is_not_unscrambled(store):
    raw = likes([str(value) for value in scrambled_301()])
    for tweet_id in ("50", "250", "300"):
        membership(store, tweet_id)
    archive_capture(store, [raw, []], missing=["data/like-part1.js"])
    assert store.get_like_order().ordered_ids("liked_latest") == ["250", "300", "50"]


def test_already_ordered_archive_keeps_file_order_when_live_anchors_support_it(store):
    for tweet_id in ("49", "50", "51", "249", "250", "300"):
        membership(store, tweet_id)
    archive_capture(store, [likes([str(value) for value in range(301)])])
    capture(store, ["49", "50", "249", "250", "300"])
    assert store.get_like_order().ordered_ids("liked_latest") == [
        "49",
        "50",
        "51",
        "249",
        "250",
        "300",
    ]


def test_disconnected_tail_is_after_head_and_unknowns_stay_last(store):
    for tweet_id in "ABCDE":
        membership(store, tweet_id)
    capture(store, ["A", "B"])
    capture(store, ["C", "D"], cursor_in="missing-page")
    assert store.get_like_order().ordered_ids("liked_latest") == list("ABCDE")
    assert store.get_like_order().ordered_ids("liked_earliest") == list("DCBAE")


def test_archive_generation_date_wins_over_import_date(store):
    for tweet_id in "ABC":
        membership(store, tweet_id)
    archive_capture(store, [likes(list("CAB"))], digest="new", generation="2026-02-01")
    archive_capture(store, [likes(list("ABC"))], digest="old", generation="2026-01-01")
    assert store.get_like_order().ordered_ids("liked_latest") == list("CAB")


def test_cursor_pages_use_local_indices_and_new_heads_win_even_after_resume(store):
    for tweet_id in "ABCDE":
        membership(store, tweet_id)
    capture(store, ["A", "B"], indices=["900", "800"], cursor_out="next")
    # A later observation re-likes B, moving it above A. Values are request-relative.
    capture(store, ["B", "A"], indices=["20", "10"])
    # Resume the old crawl after the new head; its stale head must not win.
    capture(store, ["C", "D"], indices=["999", "998"], cursor_in="next", cursor_out="last")
    capture(store, ["E"], cursor_in="last")
    assert store.get_like_order().ordered_ids("liked_latest") == list("BACDE")


@pytest.mark.parametrize("indices", [None, [None, None], ["5", "5"], ["bad", "10"], [False, "9"]])
def test_missing_or_invalid_indices_fall_back_to_observed_page_sequence(store, indices):
    for tweet_id in "AB":
        membership(store, tweet_id)
    capture(store, ["B", "A"], indices=indices)
    assert store.get_like_order().ordered_ids("liked_latest") == list("BA")


def test_valid_indices_order_a_page_numerically_and_detail_capture_is_ignored(store):
    for tweet_id in "ABC":
        membership(store, tweet_id)
    capture(store, ["A", "B"], indices=["9", "100"])
    store.append_raw_capture("TweetDetail", "C", None, 200, {"sortIndex": "99999"})
    assert store.get_like_order().ordered_ids("liked_latest") == list("BAC")
    assert store.get_like_order().ordered_ids("liked_earliest") == list("ABC")


def test_unavailable_timeline_entry_preserves_existing_like_order(store):
    for tweet_id in "AB":
        membership(store, tweet_id)
    store.append_raw_capture(
        "Likes",
        None,
        None,
        200,
        {
            "entries": [
                {
                    "entryId": f"tweet-{tweet_id}",
                    "sortIndex": str(index),
                    "content": {
                        "itemContent": {
                            "tweet_results": {"result": {"__typename": "TweetUnavailable"}}
                        }
                    },
                }
                for tweet_id, index in [("A", 10), ("B", 100), ("not-imported", 999)]
            ]
        },
    )
    assert store.get_like_order().ordered_ids("liked_latest") == ["B", "A"]


def test_cache_reused_and_invalidated_by_same_and_external_connection(store):
    membership(store, "A")
    first = store.get_like_order()
    assert store.get_like_order() is first
    capture(store, ["A"])
    second = store.get_like_order()
    assert second is not first
    other = ArchiveStore(store.db_path, create=False)
    try:
        membership(other, "B")
        capture(other, ["B", "A"])
        assert store.get_like_order().ordered_ids("liked_latest") == ["B", "A"]
    finally:
        other.close()


@pytest.mark.parametrize(
    "query",
    [
        None,
        "from:user1",
        "needle",
        "needle from:user1",
        "needle OR missing",
        "-missing",
        "from:user1 OR from:nobody",
        "needle min_faves:0",
        "-from:nobody",
    ],
)
@pytest.mark.parametrize(
    "sort, expected", [("liked_latest", ["C", "B", "A"]), ("liked_earliest", ["A", "B", "C"])]
)
def test_api_search_paths_sort_likes_before_pagination(store, query, sort, expected):
    for tweet_id in "ABC":
        membership(store, tweet_id, text="needle")
    capture(store, list("CBA"))
    result = _list_tweets(store, collection="likes", q=query, sort=sort, page=2, limit=1)
    assert result["total"] == 3
    assert result["pages"] == 3
    assert [row["tweet_id"] for row in result["tweets"]] == [expected[1]]
    all_results = _list_tweets(store, collection="likes", q=query, sort=sort)
    assert [row["tweet_id"] for row in all_results["tweets"]] == expected


def test_like_sort_requires_likes_and_does_not_change_date_sort(store):
    with pytest.raises(SearchQueryError, match="Likes collection"):
        search_posts(store, None, sort="liked_latest")
    for tweet_id in "ABC":
        membership(store, tweet_id, source=LIVE_SOURCE, sort_index=tweet_id)
    capture(store, list("ABC"))
    assert [
        r["tweet_id"] for r in _list_tweets(store, collection="likes", sort="newest")["tweets"]
    ] == list("CBA")


def test_filtered_sql_pagination_excludes_nonmatching_rows(store):
    for tweet_id in "ABCD":
        membership(store, tweet_id, text="needle")
    row = store._get_row(store._row_key_for_tweet("B", "like"))
    row["author_username"] = "somebody_else"
    store.merge_rows([row])
    capture(store, list("ABCD"))
    result = _list_tweets(
        store, collection="likes", q="from:user1", sort="liked_latest", page=2, limit=1
    )
    assert result["total"] == 3
    assert [row["tweet_id"] for row in result["tweets"]] == ["C"]


def test_like_pagination_uses_indexed_membership_probes(store):
    membership(store, "A")
    capture(store, ["A"])
    statements = []
    store.conn.set_trace_callback(statements.append)
    try:
        store.query_like_order_ids(
            "record_type = 'tweet' AND collection_type = 'like'",
            sort="liked_latest",
            limit=20,
            offset=0,
        )
    finally:
        store.conn.set_trace_callback(None)
    query = next(statement for statement in statements if "json_each" in statement)
    plan = store.conn.execute("EXPLAIN QUERY PLAN " + query).fetchall()
    assert any("USING INDEX idx_archive_tweet_id (tweet_id=?)" in row[3] for row in plan)


def test_corrupt_capture_payload_does_not_break_unknown_order(store):
    membership(store, "A")
    store.merge_rows(
        [
            store._record(
                row_key="raw_capture:bad",
                record_type="raw_capture",
                operation="Likes",
                source=LIVE_SOURCE,
                http_status=200,
                raw_json="not json",
            )
        ]
    )
    assert store.get_like_order().unknown == frozenset({"A"})
    assert json.loads(json.dumps(store.get_like_order().ordered_ids("liked_latest"))) == ["A"]
