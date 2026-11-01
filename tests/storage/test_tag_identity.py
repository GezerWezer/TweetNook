"""One rule decides whether two tags are the same, wherever they are compared."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.conftest import make_tweet_result
from tests.storage.test_field_ownership import persist_like_page
from tweetnook.storage import open_archive_store
from tweetnook.storage.search_query import search_posts


def _tags(store: Any, tweet_id: str) -> list[str]:
    row = store.conn.execute(
        "SELECT json_extract(raw_json, '$.tags') FROM archive WHERE row_key = ?",
        (f"media_tag:{tweet_id}",),
    ).fetchone()
    return json.loads(row[0]) if row else []


@pytest.fixture
def store(paths):
    archive = open_archive_store(paths, create=True)
    yield archive
    archive.close()


# Spellings SQLite's LOWER keeps apart and casefolding joins, with their expected shared key.
SAME_TAG = [
    ("Straße", "STRASSE", "strasse"),
    ("École", "école", "école"),
    ("Road", " ROAD ", "road"),
]


@pytest.mark.parametrize(("left", "right", "key"), SAME_TAG)
def test_counts_and_autocomplete_group_one_tag(store, left: str, right: str, key: str) -> None:
    store.update_media_tags("1", [left])
    store.update_media_tags("2", [right.strip()])

    [counted] = store.get_tag_counts(query=key[:3].upper())

    assert counted["count"] == 2
    assert counted["tag"] in {left, right.strip()}


@pytest.mark.parametrize(("left", "right", "key"), SAME_TAG)
def test_coverage_counts_one_tag(store, left: str, right: str, key: str) -> None:
    store.update_media_tags("1", [left])
    store.update_media_tags("2", [right.strip()])

    facts = store.tagging_coverage()

    assert facts.unique_tags == 1
    assert facts.top_tags == [{"tag": key, "count": 2}]


@pytest.mark.parametrize(("left", "right", "key"), SAME_TAG)
def test_global_delete_removes_every_spelling(store, left: str, right: str, key: str) -> None:
    store.update_media_tags("1", [left, "kept"])
    store.update_media_tags("2", [right.strip(), "kept"])

    store.delete_global_tag(right)

    assert _tags(store, "1") == ["kept"]
    assert _tags(store, "2") == ["kept"]


def test_merge_reaches_every_spelling_and_keeps_the_rest(store) -> None:
    store.update_media_tags("1", ["Straße", "kept"])
    store.update_media_tags("2", ["STRASSE"])
    store.update_media_tags("3", ["other"])

    store.merge_global_tags("Road", [" strasse "])

    assert _tags(store, "1") == ["kept", "Road"]
    assert _tags(store, "2") == ["Road"]
    assert _tags(store, "3") == ["other"]


def test_merging_into_a_spelling_of_the_primary_changes_nothing(store) -> None:
    store.update_media_tags("1", ["Straße"])

    store.merge_global_tags("STRASSE", ["straße"])

    assert _tags(store, "1") == ["Straße"]


def test_saving_deduplicates_by_the_same_rule_and_keeps_the_spelling(store) -> None:
    store.update_media_tags("1", ["Straße", " STRASSE ", "École", "école"])

    assert _tags(store, "1") == ["Straße", "École"]


@pytest.mark.parametrize(
    "query", ["tag:École", "tag:école", "tag:ÉCOLE", "tag:Straße", "tag:strasse"]
)
def test_tag_search_finds_every_spelling(store, query: str) -> None:
    persist_like_page(store, make_tweet_result("1", "tagged"))
    store.update_media_tags("1", ["École", "STRASSE"])

    assert [row["tweet_id"] for row in search_posts(store, query).rows] == ["1"]


@pytest.mark.parametrize("query", ["#École", "#école", "#ÉCOLE", "#strasse", "#STRASSE", "#Straße"])
def test_hashtag_search_finds_every_spelling(store, query: str) -> None:
    raw = make_tweet_result("1", "a post")
    raw["legacy"]["entities"] = {"hashtags": [{"text": "École"}, {"text": "Straße"}]}
    persist_like_page(store, raw)
    persist_like_page(store, make_tweet_result("2", "another post"))

    assert [row["tweet_id"] for row in search_posts(store, query).rows] == ["1"]


def test_a_hashtag_search_still_matches_whole_values(store) -> None:
    raw = make_tweet_result("1", "a post")
    raw["legacy"]["entities"] = {"hashtags": [{"text": "École"}]}
    persist_like_page(store, raw)

    assert search_posts(store, "#éco").rows == []
