"""Helpers and fixtures shared by the tweets api tests."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from tweetnook.storage.presentation import PresentationMixin  # noqa: E402
from tweetnook.web.deps import get_store, verify_credentials  # noqa: E402
from tweetnook.web.routes.tweets import (  # noqa: E402
    api_tweets,
    router,
)


class ListingStore(PresentationMixin):
    def note_observations(self, tweet_ids):
        return {}

    def __init__(
        self,
        rows: list[dict[str, Any]],
        *,
        search_rows: list[dict[str, Any]] | None = None,
        object_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self.rows = rows
        self.search_rows = search_rows if search_rows is not None else rows
        self.object_rows = object_rows or []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.media_rows: list[dict[str, Any]] = []

    def _count(self, expr: str) -> int:
        self.calls.append(("count", {"expr": expr}))
        return len(self.rows)

    def _query(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("query", kwargs))
        rows = self.rows
        if kwargs.get("cols") == ["DISTINCT tweet_id"]:
            rows = list({row["tweet_id"]: row for row in rows}.values())
        offset = kwargs.get("offset", 0)
        limit = kwargs.get("limit", len(rows))
        return [{"tweet_id": row["tweet_id"]} for row in rows[offset : offset + limit]]

    def fetch_tweets_by_ids(
        self, ids: list[str], *, collections: set[str] | None = None
    ) -> list[dict[str, Any]]:
        self.calls.append(("fetch", {"ids": ids}))
        by_id = {row["tweet_id"]: row for row in self.rows}
        return [by_id[tweet_id] for tweet_id in ids if tweet_id in by_id]

    def search_fts(
        self,
        query: str,
        *,
        limit: int,
        types: set[str] | None = None,
        collections: set[str] | None,
    ) -> list[dict[str, Any]]:
        self.calls.append(
            (
                "search",
                {
                    "query": query,
                    "limit": limit,
                    "types": types,
                    "collections": collections,
                },
            )
        )
        return [dict(row) for row in self.search_rows]

    def search_tweet_ids(
        self,
        query_groups,
        *,
        collections,
        sort,
        limit,
        offset,
        random_seed=None,
        include_total=False,
        exclude_hidden=False,
        exclude_uncaptured=False,
    ):
        self.calls.append(
            (
                "search_tweet_ids",
                {
                    "query_groups": query_groups,
                    "collections": collections,
                    "sort": sort,
                    "limit": limit,
                    "offset": offset,
                    "random_seed": random_seed,
                    "include_total": include_total,
                    "exclude_hidden": exclude_hidden,
                    "exclude_uncaptured": exclude_uncaptured,
                },
            )
        )
        has_text = any(clause.get("kind") == "text" for group in query_groups for clause in group)
        source = self.search_rows if has_text else self.rows

        def timestamp(row):
            full_row = next(
                (candidate for candidate in self.rows if candidate["tweet_id"] == row["tweet_id"]),
                row,
            )
            if full_row.get("created_at_ts") is not None:
                return full_row["created_at_ts"]
            created_at = full_row.get("created_at", "")
            if "T" in created_at:
                return datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp()
            return datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y").timestamp()

        rows = [
            {
                "tweet_id": row["tweet_id"],
                "match_score": row.get("match_score", 0.0),
                "created_at_ts": timestamp(row),
                "sort_index": row.get("sort_index"),
            }
            for row in source
        ]
        if sort == "oldest":
            rows.sort(key=lambda row: (row.get("created_at_ts") or 0, row["tweet_id"]))
        elif sort == "newest":
            rows.sort(
                key=lambda row: (row.get("created_at_ts") or 0, row["tweet_id"]), reverse=True
            )
        return rows[offset : offset + limit + 1], len(rows) if include_total else None

    def export_rows(
        self, collection: str, *, sort: str, include_raw_json: bool
    ) -> list[dict[str, Any]]:
        self.calls.append(
            (
                "export",
                {
                    "collection": collection,
                    "sort": sort,
                    "include_raw_json": include_raw_json,
                },
            )
        )
        return [dict(row) for row in self.rows]

    def _rows_for_values(
        self,
        record_type: str,
        field: str,
        values: list[str],
        *,
        columns: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        self.calls.append(
            (
                "rows_for_values",
                {
                    "record_type": record_type,
                    "field": field,
                    "values": values,
                    "columns": columns,
                },
            )
        )
        if record_type == "tweet_object":
            return [row for row in self.object_rows if row.get(field) in values]
        if record_type == "media":
            return [row for row in self.media_rows if row.get(field) in values]
        return []


def _decoded(response: Any) -> dict[str, Any]:
    """Tweet routes return pre-serialized JSON responses; read them as data."""
    return json.loads(response.body)


def _list_tweets(
    store: ListingStore,
    *,
    q: str | None = None,
    collection: str = "all",
    sort: str = "default",
    page: int = 1,
    limit: int = 20,
) -> dict[str, Any]:
    return _decoded(
        api_tweets(
            q=q,
            collection=collection,
            sort=sort,
            page=page,
            limit=limit,
            random_seed=None,
            store=store,
            _auth=True,
        )
    )


def _api_client(store: object) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[verify_credentials] = lambda: True
    return TestClient(app)
