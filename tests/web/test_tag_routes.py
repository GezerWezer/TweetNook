from __future__ import annotations

from collections.abc import Callable

import pytest

from tweetnook.web.routes import tags as tag_routes


class TagStore:
    def __init__(self):
        self.calls: list[tuple] = []
        self.counts = [{"tag": "bird", "count": 3}]
        self.failure: Exception | None = None

    def _call(self, *args):
        if self.failure:
            raise self.failure
        self.calls.append(args)

    def delete_media_tag(self, tweet_id):
        self._call("delete_media_tag", tweet_id)

    def update_media_tags(self, tweet_id, tags, *, description=None):
        self._call("update_media_tags", tweet_id, tags, description)

    def delete_global_tag(self, tag):
        self._call("delete_global_tag", tag)

    def merge_global_tags(self, primary_tag, merge_tags):
        self._call("merge_global_tags", primary_tag, merge_tags)

    def get_tag_counts(self, **kwargs):
        self._call("get_tag_counts", kwargs)
        return self.counts


@pytest.mark.parametrize(
    ("method", "path", "json_body", "expected_call"),
    [
        ("delete", "/api/tags/t1", None, ("delete_media_tag", "t1")),
        (
            "put",
            "/api/tags/t1",
            {"tags": ["Bird", "Night sky"], "description": "Evening birds"},
            ("update_media_tags", "t1", ["Bird", "Night sky"], "Evening birds"),
        ),
        (
            "delete",
            "/api/tags/global/Night%20Sky",
            None,
            ("delete_global_tag", "Night Sky"),
        ),
        (
            "post",
            "/api/tags/merge",
            {"primary_tag": "Bird", "merge_tags": ["birds", "BIRDS"]},
            ("merge_global_tags", "Bird", ["birds", "BIRDS"]),
        ),
    ],
)
def test_tag_mutations_call_store_and_return_success(
    make_web_client,
    method: str,
    path: str,
    json_body: dict | None,
    expected_call: tuple,
) -> None:
    store = TagStore()
    client = make_web_client(tag_routes.router, store=store)

    request: Callable = getattr(client, method)
    response = request(path, json=json_body) if json_body is not None else request(path)

    assert response.status_code == 200
    assert response.json() == {"success": True}
    assert store.calls == [expected_call]


def test_autocomplete_forwards_query_and_returns_counts(make_web_client) -> None:
    store = TagStore()
    client = make_web_client(tag_routes.router, store=store)

    response = client.get("/api/tags/autocomplete", params={"q": "bi"})

    assert response.status_code == 200
    assert response.json() == {"tags": [{"tag": "bird", "count": 3}]}
    assert store.calls == [("get_tag_counts", {"query": "bi"})]


def test_autocomplete_defaults_to_empty_query(make_web_client) -> None:
    store = TagStore()
    client = make_web_client(tag_routes.router, store=store)

    response = client.get("/api/tags/autocomplete")

    assert response.status_code == 200
    assert store.calls == [("get_tag_counts", {"query": ""})]


def test_tag_stats_requests_all_counts(make_web_client) -> None:
    store = TagStore()
    client = make_web_client(tag_routes.router, store=store)

    response = client.get("/api/tags/stats")

    assert response.status_code == 200
    assert response.json() == {"tags": [{"tag": "bird", "count": 3}]}
    assert store.calls == [("get_tag_counts", {"limit": -1})]


@pytest.mark.parametrize(
    ("method", "path", "json_body"),
    [
        ("delete", "/api/tags/t1", None),
        ("put", "/api/tags/t1", {"tags": ["bird"]}),
        ("delete", "/api/tags/global/bird", None),
        (
            "post",
            "/api/tags/merge",
            {"primary_tag": "bird", "merge_tags": ["birds"]},
        ),
        ("get", "/api/tags/autocomplete", None),
        ("get", "/api/tags/stats", None),
    ],
)
def test_tag_store_failures_return_500(
    make_web_client, method: str, path: str, json_body: dict | None
) -> None:
    store = TagStore()
    store.failure = RuntimeError("tag database unavailable")
    client = make_web_client(tag_routes.router, store=store)
    request: Callable = getattr(client, method)

    response = request(path, json=json_body) if json_body is not None else request(path)

    assert response.status_code == 500
    assert response.json() == {"detail": "tag database unavailable"}


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/tags/t1", {}),
        ("/api/tags/t1", {"tags": "bird"}),
        ("/api/tags/merge", {"primary_tag": "bird"}),
        ("/api/tags/merge", {"primary_tag": "bird", "merge_tags": "birds"}),
    ],
)
def test_tag_mutations_validate_request_shapes(make_web_client, path: str, body: dict) -> None:
    store = TagStore()
    client = make_web_client(tag_routes.router, store=store)
    method = client.put if path.endswith("/t1") else client.post

    response = method(path, json=body)

    assert response.status_code == 422
    assert store.calls == []


def test_tag_routes_require_authentication(make_web_client) -> None:
    store = TagStore()
    client = make_web_client(tag_routes.router, store=store, password="secret")

    response = client.get("/api/tags/stats")

    assert response.status_code == 401
    assert store.calls == []
