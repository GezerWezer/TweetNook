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
    "query", ['""', '-""', 'NOT ""', 'cats OR ""', '"" OR cats', '"   "', '-"\t"']
)
def test_empty_search_phrase_is_a_controlled_query_error(tmp_path, make_web_client, query):
    store = _real_store(tmp_path, [_row("1", "cats dogs")])
    try:
        with pytest.raises(SearchQueryError, match="empty"):
            parse_search_query(query)
        for execute in (search_posts, count_posts):
            with pytest.raises(SearchQueryError, match="empty"):
                execute(store, query)
        client = make_web_client(tweets.router, store=store)
        for endpoint in ("/api/tweets", "/api/tweets/count"):
            response = client.get(endpoint, params={"q": query})
            assert response.status_code == 400
            assert "empty" in response.json()["detail"]
    finally:
        store.close()


def test_nonempty_quoted_phrase_remains_searchable(tmp_path, make_web_client):
    store = _real_store(tmp_path, [_row("1", "cats dogs"), _row("2", "cats and dogs")])
    try:
        assert [row["tweet_id"] for row in search_posts(store, '"cats dogs"').rows] == ["1"]
        assert count_posts(store, '"cats dogs"') == 1
    finally:
        store.close()
