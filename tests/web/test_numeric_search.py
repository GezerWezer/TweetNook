import pytest

from tests.search.conftest import _real_store, _row
from tweetnook.storage.search_query import (
    SearchQueryError,
    count_posts,
    parse_search_query,
    search_posts,
)
from tweetnook.web.routes import tweets


@pytest.mark.parametrize(
    "query",
    [
        "since_time:nan",
        "until_time:inf",
        "since_time:-inf",
        "until_time:1e400",
        "min_faves:9223372036854775808",
        "since_id:9223372036854775808",
        "max_id:-9223372036854775809",
        "min_retweets:-9223372036854775809",
        "min_replies:1.5",
    ],
)
def test_unexecutable_numeric_search_is_rejected_at_every_boundary(
    tmp_path, make_web_client, query
):
    store = _real_store(tmp_path, [_row("1", "post")])
    try:
        with pytest.raises(SearchQueryError):
            parse_search_query(query)
        with pytest.raises(SearchQueryError):
            search_posts(store, query)
        with pytest.raises(SearchQueryError):
            count_posts(store, query)
        client = make_web_client(tweets.router, store=store)
        for endpoint in ("/api/tweets", "/api/tweets/count"):
            response = client.get(endpoint, params={"q": query})
            assert response.status_code == 400
    finally:
        store.close()


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("since_time:1.5", ["2", "3"]),
        ("until_time:1.5", ["1"]),
        ("-since_time:1.5", ["1"]),
        ("since_time:1.5 OR until_time:1.5", ["1", "2", "3"]),
        ("since_time:1e308", []),
        ("until_time:1e308", ["1", "2", "3"]),
        ("since_id:9223372036854775807", []),
        ("max_id:9223372036854775807", ["1", "2", "3"]),
        ("since_id:-9223372036854775808", ["1", "2", "3"]),
    ],
)
def test_numeric_search_preserves_fractional_boundaries_and_sqlite_edges(
    tmp_path, make_web_client, query, expected
):
    store = _real_store(tmp_path, [_row(str(i), "post") for i in (1, 2, 3)])
    try:
        result = search_posts(store, query, sort="oldest")
        assert [row["tweet_id"] for row in result.rows] == expected
        assert count_posts(store, query) == len(expected)
        client = make_web_client(tweets.router, store=store)
        response = client.get("/api/tweets", params={"q": query, "sort": "oldest"})
        assert response.status_code == 200
        assert [row["tweet_id"] for row in response.json()["tweets"]] == expected
        response = client.get("/api/tweets/count", params={"q": query})
        assert response.status_code == 200
        assert response.json()["total"] == len(expected)
    finally:
        store.close()
