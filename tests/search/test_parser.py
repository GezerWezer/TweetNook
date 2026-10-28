from __future__ import annotations

import pytest

from tests.search.conftest import _real_store, _row
from tweetnook.storage.search_query import (
    SearchQueryError,
    count_posts,
    parse_search_query,
    search_posts,
)


def test_parser_uses_implicit_and_and_adjacent_or_groups() -> None:
    parsed = parse_search_query("from:alice cats OR dogs has:image")

    assert [[clause.value for clause in group] for group in parsed.groups] == [
        ["alice"],
        ["cats", "dogs"],
        ["image"],
    ]
    assert parsed.has_or is True


def test_parser_accepts_explicit_and_but_keeps_lowercase_words_searchable() -> None:
    explicit = parse_search_query("cats AND dogs")
    lowercase = parse_search_query("cats and dogs or birds")

    assert [[clause.value for clause in group] for group in explicit.groups] == [
        ["cats"],
        ["dogs"],
    ]
    assert [group[0].value for group in lowercase.groups] == ["cats", "and", "dogs", "or", "birds"]


def test_parser_preserves_text_and_filter_negation() -> None:
    parsed = parse_search_query('cats -dogs -filter:videos "night sky"')
    clauses = [clause for group in parsed.groups for clause in group]

    assert [(clause.kind, clause.value, clause.negated) for clause in clauses] == [
        ("text", "cats", False),
        ("text", "dogs", True),
        ("filter", "videos", True),
        ("text", '"night sky"', False),
    ]


@pytest.mark.parametrize(
    "query", ["OR cats", "cats OR", "cats AND", "unknown:value", 'description:"* *"']
)
def test_parser_rejects_malformed_or_unknown_operators(query: str) -> None:
    with pytest.raises(SearchQueryError):
        parse_search_query(query)


def test_highlight_terms_preserve_phrases_and_prefixes_and_exclude_negative_filters():
    query = (
        'gif is:reply "night sky" gif* OR cats -dogs NOT "bad phrase" '
        "from:alice #python -#hidden tag:gift description:red"
    )
    assert parse_search_query(query).highlight_terms() == [
        "gif",
        '"night sky"',
        "gif*",
        "cats",
        "#python",
    ]


def test_grouped_search_requires_all_groups_and_any_or_alternative(tmp_path) -> None:
    photo = [{"type": "photo"}]
    store = _real_store(
        tmp_path,
        [
            _row("1", "cats", media=photo),
            _row("2", "dogs", media=photo),
            _row("3", "dogs", username="bob", media=photo),
            _row("4", "cats"),
        ],
    )

    result = search_posts(store, "from:alice cats OR dogs has:image", limit=20)

    assert {row["tweet_id"] for row in result.rows} == {"1", "2"}
    assert result.total is None
    store.close()


@pytest.mark.parametrize(
    "query",
    [
        "gif is:reply",
        "gif",
        '"funny gif"',
        "gif OR cats",
        "NOT cats",
        "-gif",
        "from:alice gif",
        "has:image",
        "is:hidden",
        "gif OR is:hidden",
        "gif*",
        "missing",
        "",
    ],
)
@pytest.mark.parametrize("collections", [None, {"bookmarks"}, {"likes"}])
def test_exact_count_matches_search_without_paging_or_hydration(tmp_path, query, collections):
    rows = [
        _row("1", "funny gif"),
        _row("2", "cats", media=[{"type": "photo"}]),
        _row("3", "gifs", username="bob"),
        _row("4", "funny gif"),
    ]
    rows[0]["raw_json"]["legacy"]["in_reply_to_status_id_str"] = "100"
    store = _real_store(tmp_path, rows)
    duplicate = store._query(expr="row_key = 'tweet:bookmark::1'")[0]
    duplicate.update(row_key="tweet:like::1", collection_type="like")
    store._merge_records([duplicate])
    store.set_tweet_hidden("4", True)
    expected = len(search_posts(store, query, collections=collections, limit=100).rows)
    statements = []
    store.conn.set_trace_callback(statements.append)
    store.fetch_tweets_by_ids = lambda *_: pytest.fail("Counts must not hydrate tweets")
    assert count_posts(store, query, collections=collections) == expected
    assert not any("LIMIT 2 OFFSET" in sql or "bm25(" in sql for sql in statements)
    store.close()


def test_explicit_or_makes_repeated_filters_alternatives(tmp_path) -> None:
    store = _real_store(
        tmp_path,
        [
            _row("1", "one"),
            _row("2", "two", username="bob"),
            _row("3", "three", username="carol"),
        ],
    )

    result = search_posts(store, "from:alice OR from:bob", limit=20)

    assert {row["tweet_id"] for row in result.rows} == {"1", "2"}
    assert result.total == 2
    store.close()
