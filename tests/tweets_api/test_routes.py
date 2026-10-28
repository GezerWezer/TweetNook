from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from tests.tweets_api.conftest import ListingStore, _api_client, _decoded  # noqa: E402
from tweetnook.storage.backend import ArchiveStore  # noqa: E402
from tweetnook.web.deps import get_store, server_state  # noqa: E402
from tweetnook.web.routes.tweets import (  # noqa: E402
    api_tweet_quotes,
    api_tweet_thread,
    api_tweets,
    router,
)


class QuotesConnection:
    def __init__(self) -> None:
        self.call: tuple[str, tuple[str, ...]] | None = None

    def execute(self, sql: str, params: tuple[str, ...]):
        self.call = (sql, params)
        return self

    def fetchone(self) -> tuple[int]:
        return (3,)


class QuotesStore:
    def __init__(self) -> None:
        self.conn = QuotesConnection()
        self.query: dict[str, Any] | None = None

    def _query(self, expr: str, params=(), **kwargs: Any) -> list[dict[str, Any]]:
        self.query = {"expr": expr, "params": tuple(params), **kwargs}
        return [{"tweet_id": "q3"}, {"tweet_id": "q2"}]

    def fetch_tweets_by_ids(
        self, ids: list[str], *, include_objects: bool = False
    ) -> list[dict[str, Any]]:
        assert include_objects
        return [{"tweet_id": value} for value in ids]

    def _rows_for_values(
        self,
        _record_type: str,
        _field: str,
        _values: list[str],
        *,
        columns: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        return []


def test_api_tweet_quotes_parameterizes_count_and_paginates_distinct_ids():
    store = QuotesStore()
    payload = "target' OR 1=1 --"

    result = _decoded(api_tweet_quotes(payload, page=2, limit=2, store=store, _auth=True))

    assert [tweet["tweet_id"] for tweet in result["tweets"]] == ["q3", "q2"]
    assert all(tweet["availability"]["state"] == "incomplete" for tweet in result["tweets"])
    assert result["total"] == 3
    assert result["page"] == 2
    assert result["limit"] == 2
    assert store.conn.call is not None
    sql, params = store.conn.call
    assert payload not in sql
    assert params == (payload,)
    assert "INDEXED BY idx_archive_target_tweet_id" in sql
    assert store.query is not None
    assert payload not in store.query["expr"]
    assert store.query["params"] == (payload,)
    assert store.query["cols"] == ["DISTINCT tweet_id"]
    assert store.query["offset"] == 2
    assert store.query["indexed_by"] == "idx_archive_target_tweet_id"


def test_search_count_endpoint_uses_private_reader_and_closes_it(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    store._merge_records(
        [
            store._record(
                row_key=f"tweet:bookmark::{i}",
                record_type="tweet",
                tweet_id=str(i),
                collection_type="bookmark",
                text="gif",
                raw_json=json.dumps({"legacy": {"in_reply_to_status_id_str": "100"}}),
            )
            for i in range(1, 25)
        ]
    )
    readers = []
    closed = []
    open_reader = store.open_reader

    def reader():
        result = open_reader()
        readers.append(result)
        close = result.close

        def record_close():
            closed.append(result)
            close()

        result.close = record_close
        return result

    store.open_reader = reader
    client = _api_client(store)
    listing = client.get("/api/tweets", params={"q": "gif is:reply"}).json()
    assert listing["total"] is None
    assert listing["has_more"] is True
    assert listing["highlight_terms"] == ["gif"]
    response = client.get(
        "/api/tweets/count", params={"q": "gif is:reply", "collection": "bookmarks"}
    )
    assert response.status_code == 200
    assert response.json() == {"total": 24}
    assert len(readers) == 1
    assert closed == readers
    assert client.get("/api/tweets/count?q=is:invalid").status_code == 400
    assert len(readers) == 2
    assert closed == readers
    store.close()


@pytest.mark.parametrize(
    "url",
    [
        "/api/tweets?page=0",
        "/api/tweets?limit=0",
        "/api/tweets?limit=101",
        "/api/tweets?random_seed=-1",
        "/api/tweets?random_seed=2147483648",
        "/api/tweets/1/quotes?page=0",
        "/api/tweets/1/quotes?limit=101",
    ],
)
def test_api_query_validation_rejects_out_of_bounds_pagination(url: str):
    response = _api_client(ListingStore([])).get(url)
    assert response.status_code == 422


def test_authors_search_endpoint_forwards_query_and_limit():
    class AuthorStore:
        def search_authors(self, query: str, *, limit: int) -> list[dict[str, str]]:
            assert query == "ali"
            assert limit == 10
            return [{"id": "1", "username": "alice", "display_name": "Alice"}]

    response = _api_client(AuthorStore()).get("/api/authors/search?q=ali")
    assert response.status_code == 200
    assert response.json()["authors"][0]["username"] == "alice"


def _profile_raw(user: object) -> str:
    return json.dumps({"core": {"user_results": {"result": user}}})


def test_author_profile_returns_distinct_captures_by_fetch_time(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    old_profile = {
        "rest_id": "42",
        "legacy": {
            "name": "Alice",
            "screen_name": "alice",
            "description": "Old bio",
            "followers_count": 10,
            "friends_count": 5,
        },
    }
    # Newer GraphQL responses move name and handle into `core`.
    new_profile = {
        "rest_id": "42",
        "core": {"name": "Alice Renamed", "screen_name": "alice"},
        "legacy": {"description": "New bio", "followers_count": 20, "friends_count": 6},
    }

    def record(row_key, month, user, *, record_type="tweet", author_id="42", created=0):
        return store._record(
            row_key=row_key,
            record_type=record_type,
            tweet_id=row_key.rsplit(":", 1)[-1],
            author_id=author_id,
            author_username="Alice" if author_id == "42" else "bob",
            created_at_ts=created,
            last_seen_at=f"2026-{month:02d}-01T00:00:00Z",
            raw_json=user if isinstance(user, str) else _profile_raw(user),
        )

    store._merge_records(
        [
            # The newest tweet was fetched first, so it carries the older profile.
            record("tweet:like::new", 1, old_profile, created=2),
            record("tweet_object:old", 6, new_profile, record_type="tweet_object", created=1),
            record("tweet:bookmark::old", 3, new_profile, created=1),
            # Captures without profile details or with unusable JSON are skipped.
            record("tweet:like::import", 7, {"rest_id": "42"}),
            record("tweet:like::scalar", 8, ["unavailable"]),
            record("tweet:like::broken", 9, "{not json"),
            record("tweet:like::other", 10, new_profile, author_id="99"),
        ]
    )
    client = _api_client(store)
    try:
        expected = [
            {
                "display_name": "Alice Renamed",
                "username": "alice",
                "description": "New bio",
                "followers_count": 20,
                "following_count": 6,
                "fetched_at": "2026-06-01T00:00:00Z",
            },
            {
                "display_name": "Alice",
                "username": "alice",
                "description": "Old bio",
                "followers_count": 10,
                "following_count": 5,
                "fetched_at": "2026-01-01T00:00:00Z",
            },
        ]
        response = client.get("/api/authors/profile?author_id=42")
        assert response.status_code == 200
        assert response.json() == {"author_id": "42", "snapshots": expected}

        by_handle = client.get("/api/authors/profile?username=@ALICE")
        assert by_handle.json() == {"author_id": "42", "snapshots": expected}

        limited = client.get("/api/authors/profile?author_id=42&limit=1")
        assert limited.json()["snapshots"] == expected[:1]
        assert client.get("/api/authors/profile?username=nobody").json() == {
            "author_id": None,
            "snapshots": [],
        }
        assert client.get("/api/authors/profile").json() == {"author_id": None, "snapshots": []}
        assert client.get("/api/authors/profile?author_id=42&limit=0").status_code == 422
        assert store.author_profile_snapshots("42' OR 1=1 --") == []
    finally:
        store.close()


@pytest.mark.parametrize(
    "url",
    [
        "/api/tweets",
        "/api/tweets/1",
        "/api/tweets/1/quotes",
        "/api/authors/search",
        "/api/authors/profile",
    ],
)
def test_tweet_routes_require_authentication(url: str):
    previous_state = dict(server_state)
    server_state.clear()
    server_state["password_hash"] = hashlib.sha256(b"secret").hexdigest()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_store] = lambda: ListingStore([])
    try:
        with TestClient(app) as client:
            response = client.get(url)
    finally:
        server_state.clear()
        server_state.update(previous_state)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Basic"


@pytest.mark.parametrize(
    ("method", "expected_status"),
    [
        (
            lambda: api_tweets(
                q=None,
                collection="all",
                sort="newest",
                page=1,
                limit=20,
                store=type("BadStore", (), {"_count": lambda *_: 1 / 0})(),
                _auth=True,
            ),
            500,
        ),
        (
            lambda: api_tweet_thread(
                "1",
                store=type("BadStore", (), {"_query": lambda *_a, **_k: 1 / 0})(),
                _auth=True,
            ),
            500,
        ),
    ],
)
def test_api_failures_are_mapped_to_http_500(method: Callable[[], object], expected_status: int):
    with pytest.raises(fastapi.HTTPException) as exc:
        method()
    assert exc.value.status_code == expected_status


def test_api_hide_and_unhide_tweet_controls_list_membership_and_flag(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    store._merge_records(
        [
            store._record(
                row_key=f"tweet:bookmark::{tweet_id}",
                record_type="tweet",
                tweet_id=tweet_id,
                collection_type="bookmark",
                text=f"tweet {tweet_id}",
                created_at_ts=int(tweet_id),
                sort_index=tweet_id,
                raw_json=json.dumps({"legacy": {}}),
            )
            for tweet_id in ("1", "2")
        ]
    )
    client = _api_client(store)

    assert client.put("/api/tweets/2/hidden").json() == {"hidden": True}
    listed = client.get("/api/tweets").json()
    hidden = client.get("/api/tweets", params={"q": "is:hidden"}).json()
    assert [(tweet["tweet_id"], tweet["hidden"]) for tweet in listed["tweets"]] == [("1", False)]
    assert listed["total"] == 1
    assert [(tweet["tweet_id"], tweet["hidden"]) for tweet in hidden["tweets"]] == [("2", True)]
    assert client.get("/api/tweets/2").json()["main"]["hidden"] is True

    assert client.delete("/api/tweets/2/hidden").json() == {"hidden": False}
    assert [tweet["tweet_id"] for tweet in client.get("/api/tweets").json()["tweets"]] == ["2", "1"]
    assert client.get("/api/tweets/2").json()["main"]["hidden"] is False
    store.close()
