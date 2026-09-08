"""Continuation correctness across nullable/tied keys, search modes, and mutations."""

import base64
import json

import pytest

from tests.test_web_tweets_api import _api_client
from tweetnook.search import SearchCursorExpiredError, SearchQueryError, search_posts
from tweetnook.storage.backend import ArchiveStore


@pytest.fixture
def store(tmp_path):
    value = ArchiveStore(tmp_path / "archive.db", create=True)
    value.merge_rows(
        [
            value._record(
                row_key=f"tweet:{collection}::{i}",
                record_type="tweet",
                tweet_id=str(i),
                collection_type=collection,
                created_at_ts=None if i % 7 == 0 else i // 6,
                sort_index=None if i % 3 == 0 else str(i % 4),
                source="live_graphql",
                author_username="alice" if i % 2 else "bob",
                text="needle common" if i % 2 else "common",
            )
            for i in range(85)
            for collection in ("like", "bookmark")
        ]
    )
    yield value
    value.close()


@pytest.mark.parametrize(
    "sort", ["newest", "oldest", "liked_latest", "liked_earliest", "random", "relevance"]
)
@pytest.mark.parametrize("query", [None, "common", "from:alice", "needle OR from:bob -absent"])
def test_cursor_pages_match_complete_offset_order(store, sort, query):
    kwargs = {"sort": sort, "collections": {"like"}, "random_seed": 123}
    expected = search_posts(store, query, limit=100, **kwargs)
    ids = []
    cursor = ""
    for page in range(1, 20):
        result = search_posts(store, query, limit=7, cursor=cursor, **kwargs)
        assert result.page == page
        ids.extend(row["tweet_id"] for row in result.rows)
        if not result.has_more:
            assert result.next_cursor is None
            break
        assert result.next_cursor
        cursor = result.next_cursor
    else:
        pytest.fail("Cursor did not terminate")
    assert ids == [row["tweet_id"] for row in expected.rows]
    assert len(ids) == len(set(ids))


def test_chronological_cursor_survives_new_head_and_deleted_anchor(store):
    original = search_posts(store, None, limit=100).rows
    first = search_posts(store, None, limit=7, cursor="")
    anchor = first.rows[-1]["tweet_id"]
    with store.conn:
        store.conn.execute("DELETE FROM archive WHERE tweet_id = ?", (anchor,))
    store.merge_rows(
        [
            store._record(
                row_key="tweet:like::new",
                record_type="tweet",
                tweet_id="new",
                collection_type="like",
                created_at_ts=10000,
                sort_index="1000",
            )
        ]
    )
    second = search_posts(store, None, limit=7, cursor=first.next_cursor)
    assert [row["tweet_id"] for row in second.rows] == [row["tweet_id"] for row in original[7:14]]


def test_cursor_rejects_wrong_context_malformed_types_and_changed_like_order(store):
    first = search_posts(store, None, limit=7, cursor="")
    for change in ({"sort": "oldest"}, {"limit": 8}, {"collections": {"like"}}):
        kwargs = {"limit": 7, **change}
        with pytest.raises(SearchQueryError, match="Invalid pagination"):
            search_posts(store, None, cursor=first.next_cursor, **kwargs)
    for token in ("not-a-token", "x" * 4097, base64.urlsafe_b64encode(b"[]").decode()):
        with pytest.raises(SearchQueryError, match="Invalid pagination"):
            search_posts(store, None, cursor=token, limit=7)
    payload = json.loads(
        base64.urlsafe_b64decode(first.next_cursor + "=" * (-len(first.next_cursor) % 4))
    )
    payload["after"]["sort_index"] = "0 OR 1=1"
    token = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    with pytest.raises(SearchQueryError, match="Invalid pagination"):
        search_posts(store, None, cursor=token, limit=7)
    likes = search_posts(store, None, collections={"like"}, sort="liked_latest", limit=7, cursor="")
    with store.conn:
        store.conn.execute("UPDATE archive SET sort_index = '200' WHERE tweet_id = '0'")
    with pytest.raises(SearchCursorExpiredError):
        search_posts(
            store,
            None,
            collections={"like"},
            sort="liked_latest",
            limit=7,
            cursor=likes.next_cursor,
        )


def test_api_cursor_response_continuation_and_errors(store):
    with _api_client(store) as client:
        first = client.get("/api/tweets", params={"cursor": "", "limit": 7}).json()
        assert first["has_more"] and first["next_cursor"]
        second = client.get(
            "/api/tweets", params={"cursor": first["next_cursor"], "limit": 7}
        ).json()
        assert second["page"] == 2
        assert not {row["tweet_id"] for row in first["tweets"]} & {
            row["tweet_id"] for row in second["tweets"]
        }
        assert client.get("/api/tweets", params={"cursor": "bad"}).status_code == 400
        likes = client.get(
            "/api/tweets", params={"cursor": "", "collection": "likes", "sort": "liked_latest"}
        ).json()
        with store.conn:
            store.conn.execute("UPDATE archive SET sort_index = '200' WHERE tweet_id = '0'")
        assert (
            client.get(
                "/api/tweets",
                params={
                    "cursor": likes["next_cursor"],
                    "collection": "likes",
                    "sort": "liked_latest",
                },
            ).status_code
            == 409
        )
