from __future__ import annotations

import pytest

from tests.storage_sqlite.conftest import (
    CREATED_2012,
    CREATED_2024,
    CREATED_2026,
    _membership,
    _seed_search_rows,
)
from tweetnook.storage import open_archive_store
from tweetnook.storage.search_query import search_posts


@pytest.mark.parametrize(
    ("query", "expected_ids"),
    [
        ('"alpha beta"', ["1"]),
        ("hello-world", ["1"]),
        ("alpha AND gamma", ["2"]),
        ("alpha OR gamma", ["1", "2"]),
        ("", []),
        ("   ", []),
    ],
)
def test_fts_handles_phrases_punctuation_booleans_and_blank_queries(
    paths,
    query: str,
    expected_ids: list[str],
) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    _seed_search_rows(store)

    results = store.search_fts(query, limit=20, types={"post"})

    assert sorted({row["tweet_id"] for row in results}) == sorted(expected_ids)
    store.close()


def test_fts_filters_types_and_collections_and_hydrates_memberships(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    _seed_search_rows(store)

    posts = store.search_fts("alpha", types={"post"}, collections={"bookmark"})
    articles = store.search_fts("alpha", types={"article"}, collections={"bookmark"})
    excluded = store.search_fts("alpha", types={"article"}, collections={"like"})

    assert [row["tweet_id"] for row in posts] == ["1"]
    assert posts[0]["collections"] == ["bookmark", "like"]
    assert [row["tweet_id"] for row in articles] == ["3"]
    assert articles[0]["type"] == "article"
    assert excluded == []
    store.close()


@pytest.mark.parametrize(
    ("query", "expected_ids"),
    [
        ("alpha AND beta", ["3"]),
        ("alpha OR missing", ["3"]),
        ("alpha NOT missing", ["3"]),
        ("alpha NOT beta", []),
        ("alpha -beta", []),
        ("alpha -missing", ["3"]),
        ('"focused beta"', ["3"]),
        ("focused-beta", ["3"]),
    ],
)
def test_article_search_honors_boolean_query_semantics(
    paths,
    query: str,
    expected_ids: list[str],
) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    _seed_search_rows(store)

    results = store.search_fts(query, limit=20, types={"article"})

    assert [row["tweet_id"] for row in results] == expected_ids
    store.close()


@pytest.mark.parametrize(
    ("query", "expected_ids"),
    [
        ("cats -coffee", {"1", "5"}),
        ("cats NOT coffee", {"1", "5"}),
        ("cats OR dogs coffee", {"2", "3"}),
        ("cats OR dogs -coffee", {"1", "5"}),
        ("cats OR -coffee", {"1", "3", "4", "5"}),
        ("-coffee", {"1", "4", "5"}),
        ("NOT cats", {"2", "4"}),
        ("cats not", {"5"}),
        ('"cats coffee"', {"3"}),
        ('cats -"cold brew"', {"1", "3"}),
        ("coff*", {"2", "3"}),
    ],
)
def test_article_queries_share_post_clause_semantics(paths, query, expected_ids) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    texts = {
        "1": "cats",
        "2": "dogs coffee",
        "3": "cats coffee",
        "4": "birds",
        "5": "cats not cold brew",
    }
    store.merge_rows(
        [
            row
            for tweet_id, text in texts.items()
            for row in (
                _membership(
                    store,
                    tweet_id,
                    collection="bookmark",
                    text=text,
                    created_at=CREATED_2026,
                    created_at_ts=1,
                    sort_index=tweet_id,
                ),
                store._record(
                    row_key=f"article:{tweet_id}",
                    record_type="article",
                    tweet_id=tweet_id,
                    content_text=text,
                ),
            )
        ]
    )
    try:
        posts = search_posts(store, query, limit=20).rows
        articles = store.search_fts(query, limit=20, types={"article"})
        combined = store.search_fts(query, limit=20, types={"post", "article"})
        assert {row["tweet_id"] for row in posts} == expected_ids
        assert {row["tweet_id"] for row in articles} == expected_ids
        for kind in ("post", "article"):
            assert {row["tweet_id"] for row in combined if row["type"] == kind} == expected_ids
    finally:
        store.close()


def test_article_search_filters_hidden_before_limiting_and_can_unhide(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store.merge_rows(
        [
            store._record(
                row_key=f"article:{i}",
                record_type="article",
                tweet_id=str(i),
                content_text="coffee " * (20 - i),
            )
            for i in range(12)
        ]
    )
    for i in range(10):
        store.set_tweet_hidden(str(i), True)
    try:
        visible = store.search_fts("coffee", limit=2, types={"article"})
        assert [row["tweet_id"] for row in visible] == ["10", "11"]
        store.set_tweet_hidden("0", False)
        assert store.search_fts("coffee", limit=1, types={"article"})[0]["tweet_id"] == "0"
    finally:
        store.close()


@pytest.mark.parametrize("collections", [{"bookmark"}, {"bookmark", "like"}])
def test_article_collection_search_does_not_stop_at_irrelevant_candidates(paths, collections):
    store = open_archive_store(paths, create=True)
    rows = [
        store._record(
            row_key=f"article:{i}",
            record_type="article",
            tweet_id=str(i),
            content_text="coffee " * 3,
        )
        for i in range(80)
    ]
    # Half the higher-ranked Articles are context-only; half belong to another collection.
    rows.extend(
        _membership(
            store,
            str(i),
            collection="tweet",
            text="unrelated post",
            created_at=CREATED_2026,
            created_at_ts=3,
            sort_index=str(i),
        )
        for i in range(40)
    )
    for tweet_id in ("100", "101"):
        rows.extend(
            [
                _membership(
                    store,
                    tweet_id,
                    collection="bookmark",
                    text="saved post",
                    created_at=CREATED_2024,
                    created_at_ts=2,
                    sort_index=tweet_id,
                ),
                store._record(
                    row_key=f"article:{tweet_id}",
                    record_type="article",
                    tweet_id=tweet_id,
                    content_text="coffee",
                ),
            ]
        )
    store.merge_rows(rows)
    store.set_tweet_hidden("100", True)
    try:
        results = store.search_fts("coffee", limit=1, types={"article"}, collections=collections)
        assert [row["tweet_id"] for row in results] == ["101"]
        assert results[0]["collections"] == ["bookmark"]
    finally:
        store.close()


def test_fts_overfetches_past_duplicate_memberships(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    duplicate_rows = [
        _membership(
            store,
            "1",
            collection="bookmark",
            folder_id=f"folder-{index}",
            text="shared overfetch token",
            created_at=CREATED_2012,
            created_at_ts=1,
            sort_index=str(100 - index),
        )
        for index in range(12)
    ]
    duplicate_rows.append(
        _membership(
            store,
            "2",
            collection="like",
            text="shared overfetch token",
            created_at=CREATED_2024,
            created_at_ts=2,
            sort_index="1",
        )
    )
    store._merge_records(duplicate_rows)

    results = store.search_fts("overfetch", limit=2, types={"post"})

    assert {row["tweet_id"] for row in results} == {"1", "2"}
    store.close()


def test_search_authors_matches_username_display_name_and_at_prefix(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    _seed_search_rows(store)

    assert [row["username"] for row in store.search_authors("@ali")] == ["alice"]
    assert [row["username"] for row in store.search_authors("Builder")] == ["bob"]
    assert store.search_authors("@") == []
    assert store.search_authors("") == []
    store.close()


def test_page_queries_use_covering_indexes_without_temporary_sort(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    all_plan = store.conn.execute(
        "EXPLAIN QUERY PLAN SELECT tweet_id FROM archive "
        "WHERE record_type = 'tweet' "
        "ORDER BY created_at_ts DESC, CAST(sort_index AS INTEGER) DESC, tweet_id DESC "
        "LIMIT 20"
    ).fetchall()
    collection_plan = store.conn.execute(
        "EXPLAIN QUERY PLAN SELECT tweet_id FROM archive "
        "WHERE record_type = 'tweet' AND collection_type = 'bookmark' "
        "ORDER BY created_at_ts DESC, CAST(sort_index AS INTEGER) DESC, tweet_id DESC "
        "LIMIT 20"
    ).fetchall()
    details = " ".join(row["detail"] for row in all_plan + collection_plan)

    assert "idx_archive_record_page" in details
    assert "idx_archive_record_collection_page" in details
    assert "USE TEMP B-TREE" not in details
    store.close()


def test_fts_native_rank_plan_does_not_materialize_a_temporary_sort(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store._merge_records(
        [
            _membership(
                store,
                "1",
                collection="bookmark",
                text="native rank sentinel",
                created_at=CREATED_2012,
                created_at_ts=1,
                sort_index="1",
            )
        ]
    )

    plan = store.conn.execute(
        "EXPLAIN QUERY PLAN "
        "SELECT archive.tweet_id, archive_fts.rank FROM archive "
        "JOIN archive_fts ON archive.rowid = archive_fts.rowid "
        "WHERE archive_fts MATCH 'sentinel' AND archive.record_type = 'tweet' "
        "ORDER BY archive_fts.rank LIMIT 20"
    ).fetchall()
    details = " ".join(row["detail"] for row in plan)

    assert "archive_fts" in details
    assert "USE TEMP B-TREE" not in details
    store.close()
