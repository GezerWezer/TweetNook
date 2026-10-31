import pytest

from tweetnook.storage import ArchiveStore
from tweetnook.web.routes import tweets


def _store(tmp_path, authors):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    store.merge_rows(
        [
            {
                "row_key": f"tweet:bookmark::{index}",
                "record_type": "tweet",
                "collection_type": "bookmark",
                "tweet_id": str(index),
                "author_id": author_id,
                "author_username": handle,
                "author_display_name": name,
                "created_at_ts": timestamp,
                "raw_json": "{}",
                "text": "Synthetic author example",
            }
            for index, (author_id, handle, name, timestamp) in enumerate(authors, 1)
        ]
    )
    return store


def test_exact_and_prefix_handles_rank_before_substrings_and_limit(tmp_path, make_web_client):
    authors = [(f"id{i}", f"Asam{i}", f"Person {i}", i) for i in range(10)]
    authors += [("exact", "sam", "Sam", 20), ("prefix", "sammy", "Sammy", 21)]
    store = _store(tmp_path, authors)
    try:
        assert [row["username"] for row in store.search_authors("sam", limit=3)] == [
            "sam",
            "sammy",
            "Asam0",
        ]
        response = make_web_client(tweets.router, store=store).get("/api/authors/search?q=@SAM")
        assert response.status_code == 200
        assert response.json()["authors"][0] == {
            "id": "exact",
            "username": "sam",
            "display_name": "Sam",
        }
    finally:
        store.close()


@pytest.mark.parametrize("query", ["a_b", "100%", "slash\\name"])
def test_author_queries_treat_like_wildcards_and_escape_characters_literally(tmp_path, query):
    store = _store(
        tmp_path,
        [
            ("exact", "a_b", "100% slash\\name", 1),
            ("other", "aXb", "1000 slashXname", 2),
        ],
    )
    try:
        assert store.search_authors(query) == [
            {"id": "exact", "username": "a_b", "display_name": "100% slash\\name"}
        ]
    finally:
        store.close()


def test_reused_handle_identity_and_display_name_come_from_the_latest_owner(tmp_path):
    store = _store(
        tmp_path,
        [
            ("100", "Reused", "Z Old owner", 1),
            ("200", "reused", "A New owner", 2),
        ],
    )
    try:
        assert store.resolve_author_id("@REUSED") == "200"
        assert store.search_authors("reused") == [
            {"id": "200", "username": "reused", "display_name": "A New owner"}
        ]
        assert store.search_authors("Old owner") == []
    finally:
        store.close()
