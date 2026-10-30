from __future__ import annotations

import json
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi")

from tests.tweets_api.conftest import _decoded  # noqa: E402
from tweetnook.core import fastjson  # noqa: E402
from tweetnook.storage.backend import ArchiveStore  # noqa: E402
from tweetnook.storage.conversations import ConversationsMixin  # noqa: E402
from tweetnook.web.routes.tweets import (  # noqa: E402
    _is_other_reply,
    api_tweet_thread,
)


def _thread_object(
    tweet_id: str,
    *,
    author_id: str = "u1",
    likes: int = 0,
    quote_id: str | None = None,
) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "legacy": {
            "favorite_count": likes,
            "extended_entities": {
                "media": [
                    {
                        "type": "video",
                        "original_info": {"width": 1600, "height": 900},
                        "video_info": {"aspect_ratio": [3, 4]},
                    }
                ]
            },
        }
    }
    if quote_id:
        raw["quoted_status_result"] = {
            "result": {
                "__typename": "TweetWithVisibilityResults",
                "tweet": {
                    "rest_id": quote_id,
                    "legacy": {
                        "extended_entities": {
                            "media": [
                                {
                                    "type": "video",
                                    "original_info": {"width": 1600, "height": 900},
                                    "video_info": {"aspect_ratio": [3, 4]},
                                }
                            ]
                        }
                    },
                },
            }
        }
    return {
        "tweet_id": tweet_id,
        "text": f"post {tweet_id}",
        "author_id": author_id,
        "author_username": f"user-{author_id}",
        "author_display_name": f"User {author_id}",
        "created_at": f"2026-01-0{min(len(tweet_id), 9)}T00:00:00Z",
        "synced_at": "2026-01-10T00:00:00Z",
        "raw_json": json.dumps(raw),
    }


class ThreadStore(ConversationsMixin):
    def __init__(self) -> None:
        self.relation_call = 0
        self.expressions: list[str] = []
        self.query_columns: list[tuple[str, list[str] | None]] = []
        self.query_indexes: list[tuple[str, str | None]] = []
        self.quote_count_call: tuple[str, tuple[object, ...]] | None = None
        self.reply_frontier_calls: list[tuple[str, tuple[object, ...]]] = []
        self.result_rows: list[tuple[object, ...]] = []
        self.conn = self

    def _query(
        self,
        expr: str,
        params: tuple[object, ...] = (),
        *,
        cols: list[str] | None = None,
        limit: int | None = None,
        indexed_by: str | None = None,
        **_: Any,
    ) -> list[dict[str, Any]]:
        self.expressions.append(expr)
        self.query_columns.append((expr, cols))
        self.query_indexes.append((expr, indexed_by))
        if "record_type = 'tweet_relation'" in expr and "quote_of" not in expr:
            self.relation_call += 1
            if " AND tweet_id = ?" in expr and params[0] == "main" and "reply_to" in params:
                return [
                    {
                        "tweet_id": "main",
                        "target_tweet_id": "parent",
                        "relation_type": "reply_to",
                    }
                ]
            return []
        if "record_type = 'tweet_object'" in expr:
            return [
                _thread_object("main", quote_id="quoted"),
                _thread_object("parent"),
                _thread_object("child", author_id="u2", likes=1),
                _thread_object("popular", author_id="u2", likes=20),
                _thread_object("grandchild", likes=0),
                _thread_object("non-op-grandchild", author_id="u3", likes=0),
                _thread_object("great-grandchild", author_id="u4", likes=0),
            ]
        if "record_type = 'media'" in expr:
            return [
                {
                    "tweet_id": tweet_id,
                    "position": 0,
                    "media_type": "video",
                    "width": None,
                    "height": None,
                    "local_path": f"media/{tweet_id}.mp4",
                }
                for tweet_id in (
                    "main",
                    "parent",
                    "child",
                    "popular",
                    "grandchild",
                    "non-op-grandchild",
                    "great-grandchild",
                )
            ]
        if "record_type = 'tweet' AND" in expr:
            return [
                {"tweet_id": "main", "collection_type": "bookmark"},
                {"tweet_id": "main", "collection_type": "like"},
            ]
        if "record_type = 'media_tag'" in expr:
            return [
                {"tweet_id": "main", "raw_json": '{"tags":["Night"]}'},
                {"tweet_id": "child", "raw_json": "not-json"},
            ]
        return []

    def execute(self, sql: str, params: tuple[object, ...]):
        if "/* tweetnook_reply_frontier */" in sql:
            self.reply_frontier_calls.append((sql, params))
            frontier_size = (len(params) - 1) // 2
            frontier = {str(value) for value in params[:frontier_size]}
            assert tuple(params[:frontier_size]) == tuple(params[frontier_size : frontier_size * 2])
            edges = [
                ("child", "main"),
                ("popular", "main"),
                ("grandchild", "child"),
                ("non-op-grandchild", "child"),
                ("great-grandchild", "grandchild"),
            ]
            parent_by_child: dict[str, str] = {}
            for child_id, parent_id in edges:
                if parent_id not in frontier:
                    continue
                parent_by_child[child_id] = min(parent_id, parent_by_child.get(child_id, parent_id))
            self.result_rows = sorted(parent_by_child.items())[: int(params[-1])]
            return self
        self.quote_count_call = (sql, params)
        self.result_rows = []
        return self

    def fetchone(self) -> tuple[int]:
        return (2,)

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.result_rows

    def _rows_for_values(
        self,
        record_type: str,
        field: str,
        values: list[str],
        *,
        columns: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        assert field == "tweet_id"
        if record_type in {"tweet_object", "tweet_relation", "tweet_hidden", "article"}:
            return []
        if "quoted" not in values:
            return []
        if record_type == "media_tag":
            assert columns == ["tweet_id", "raw_json"]
            return [
                {
                    "tweet_id": "quoted",
                    "raw_json": '{"tags":["Quoted Topic"]}',
                }
            ]
        assert record_type == "media"
        if columns == ["tweet_id", "source", "media_url", "thumbnail_url", "local_path"]:
            return []
        assert columns == [
            "tweet_id",
            "source",
            "position",
            "media_type",
            "media_url",
            "thumbnail_url",
            "width",
            "height",
            "duration_millis",
            "local_path",
            "thumbnail_local_path",
        ]
        return [
            {
                "tweet_id": "quoted",
                "position": 0,
                "media_type": "video",
                "width": 1600,
                "height": 900,
                "duration_millis": 12,
                "local_path": "media/quote.mp4",
            }
        ]


def test_api_tweet_thread_builds_parents_children_nested_replies_quotes_media_and_tags():
    store = ThreadStore()

    result = _decoded(api_tweet_thread("main", store=store, _auth=True))

    assert result["main"]["collections"] == ["bookmark", "like"]
    assert result["main"]["local_quote_count"] == 2
    assert result["main"]["media_tags"] == {"tags": ["Night"]}
    assert result["main"]["qt_media_tags"] == {"tags": ["Quoted Topic"]}
    assert result["main"]["qt_media"][0]["download"]["local_path"] == "media/quote.mp4"
    assert result["main"]["qt_media"][0]["aspect_ratio"] == [3, 4]
    assert result["main"]["media"][0]["aspect_ratio"] == [3, 4]
    assert [tweet["tweet_id"] for tweet in result["parents"]] == ["parent"]
    assert result["parents"][0]["media"][0]["aspect_ratio"] == [3, 4]
    assert [tweet["tweet_id"] for tweet in result["children"]] == ["child", "popular"]
    assert result["children"][0]["media"][0]["aspect_ratio"] == [3, 4]
    assert [reply["tweet_id"] for reply in result["children"][0]["op_replies"]] == [
        "grandchild",
        "great-grandchild",
        "non-op-grandchild",
    ]
    assert [reply["thread_depth"] for reply in result["children"][0]["op_replies"]] == [
        2,
        3,
        2,
    ]
    assert [reply["parent_tweet_id"] for reply in result["children"][0]["op_replies"]] == [
        "child",
        "grandchild",
        "child",
    ]
    assert result["children"][0]["op_replies"][2]["author"]["id"] == "u3"
    assert result["children"][0]["op_replies"][0]["media"][0]["aspect_ratio"] == [3, 4]
    assert result["reply_tree_truncated"] is False
    assert store.reply_frontier_calls
    first_frontier_sql, first_frontier_params = store.reply_frontier_calls[0]
    assert first_frontier_sql.count("INDEXED BY idx_archive_target_tweet_id") == 1
    assert first_frontier_sql.count("INDEXED BY idx_archive_tweet_id") == 1
    assert first_frontier_params == ("main", "main", 252)
    assert store.quote_count_call is not None
    assert store.quote_count_call[1] == ("main",)
    assert "INDEXED BY idx_archive_target_tweet_id" in store.quote_count_call[0]
    relation_columns = {
        tuple(columns or [])
        for expr, columns in store.query_columns
        if "record_type = 'tweet_relation'" in expr
    }
    assert relation_columns == {("tweet_id", "target_tweet_id", "relation_type")}
    relation_queries = [
        (expr, index)
        for expr, index in store.query_indexes
        if "record_type = 'tweet_relation'" in expr
    ]
    assert relation_queries
    assert all(" OR " not in expr for expr, _ in relation_queries)
    for expr, index in relation_queries:
        expected = (
            "idx_archive_target_tweet_id"
            if " AND target_tweet_id" in expr
            else "idx_archive_tweet_id"
        )
        assert index == expected
    expected_columns = {
        "tweet_object": {
            "tweet_id",
            "text",
            "author_id",
            "author_username",
            "author_display_name",
            "created_at",
            "synced_at",
            "raw_json",
        },
        "media": {
            "source",
            "tweet_id",
            "position",
            "media_type",
            "media_url",
            "thumbnail_url",
            "width",
            "height",
            "duration_millis",
            "local_path",
            "thumbnail_local_path",
        },
        "media_tag": {"tweet_id", "raw_json"},
    }
    for record_type, columns in expected_columns.items():
        actual = next(
            selected
            for expr, selected in store.query_columns
            if f"record_type = '{record_type}'" in expr
        )
        assert set(actual or []) == columns
    for expr, index in store.query_indexes:
        if any(f"record_type = '{record_type}'" in expr for record_type in expected_columns):
            assert index == "idx_archive_tweet_id"


def test_api_tweet_thread_recursively_loads_and_deduplicates_saved_reply_chain(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    records = []
    for tweet_id, author_id in (("100", "u1"), ("200", "u2"), ("300", "u3"), ("400", "u4")):
        tweet = _thread_object(tweet_id, author_id=author_id)
        records.append(
            store._record(
                row_key=f"tweet_object:{tweet_id}",
                record_type="tweet_object",
                tweet_id=tweet_id,
                text=tweet["text"],
                author_id=tweet["author_id"],
                author_username=tweet["author_username"],
                author_display_name=tweet["author_display_name"],
                created_at=tweet["created_at"],
                raw_json=tweet["raw_json"],
                enrichment_state="done",
            )
        )
    for child_id, parent_id in (("200", "100"), ("300", "200"), ("400", "300")):
        records.extend(
            [
                store._record(
                    row_key=f"tweet_relation:{child_id}:reply_to:{parent_id}",
                    record_type="tweet_relation",
                    tweet_id=child_id,
                    relation_type="reply_to",
                    target_tweet_id=parent_id,
                ),
                store._record(
                    row_key=f"tweet_relation:{child_id}:thread_parent:{parent_id}",
                    record_type="tweet_relation",
                    tweet_id=child_id,
                    relation_type="thread_parent",
                    target_tweet_id=parent_id,
                ),
                store._record(
                    row_key=f"tweet_relation:{parent_id}:thread_child:{child_id}",
                    record_type="tweet_relation",
                    tweet_id=parent_id,
                    relation_type="thread_child",
                    target_tweet_id=child_id,
                ),
            ]
        )
    store._merge_records(records)

    limited_rows, limited = store.reply_subtree("100", limit=2)
    assert [row["tweet_id"] for row in limited_rows] == ["200", "300"]
    assert limited is True

    traced_sql: list[str] = []
    store.conn.set_trace_callback(traced_sql.append)
    result = _decoded(api_tweet_thread("100", store=store, _auth=True))
    store.conn.set_trace_callback(None)

    assert [child["tweet_id"] for child in result["children"]] == ["200"]
    assert result["children"][0]["thread_depth"] == 1
    assert [reply["tweet_id"] for reply in result["children"][0]["op_replies"]] == [
        "300",
        "400",
    ]
    assert [reply["thread_depth"] for reply in result["children"][0]["op_replies"]] == [
        2,
        3,
    ]
    assert result["reply_tree_truncated"] is False
    frontier_queries = [sql for sql in traced_sql if "/* tweetnook_reply_frontier */" in sql]
    assert len(frontier_queries) == 4
    assert all("INDEXED BY idx_archive_target_tweet_id" in sql for sql in frontier_queries)
    assert all("INDEXED BY idx_archive_tweet_id" in sql for sql in frontier_queries)
    assert all("WITH RECURSIVE" not in sql for sql in frontier_queries)
    store.close()


def test_reply_tree_deduplicates_dense_alternate_paths_per_frontier(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    records = []
    previous_level = ["root"]
    levels = []
    for depth in range(1, 7):
        current_level = [f"{depth}-{index}" for index in range(8)]
        levels.append(current_level)
        for parent_id in previous_level:
            for child_id in current_level:
                records.extend(
                    [
                        store._record(
                            row_key=f"tweet_relation:{child_id}:reply_to:{parent_id}",
                            record_type="tweet_relation",
                            tweet_id=child_id,
                            relation_type="reply_to",
                            target_tweet_id=parent_id,
                        ),
                        store._record(
                            row_key=f"tweet_relation:{child_id}:thread_parent:{parent_id}",
                            record_type="tweet_relation",
                            tweet_id=child_id,
                            relation_type="thread_parent",
                            target_tweet_id=parent_id,
                        ),
                        store._record(
                            row_key=f"tweet_relation:{parent_id}:thread_child:{child_id}",
                            record_type="tweet_relation",
                            tweet_id=parent_id,
                            relation_type="thread_child",
                            target_tweet_id=child_id,
                        ),
                    ]
                )
        previous_level = current_level
    store._merge_records(records)

    traced_sql: list[str] = []
    store.conn.set_trace_callback(traced_sql.append)
    rows, truncated = store.reply_subtree("root")
    store.conn.set_trace_callback(None)

    assert truncated is False
    assert len(rows) == 48
    assert len({row["tweet_id"] for row in rows}) == 48
    for depth, level in enumerate(levels, start=1):
        assert {row["tweet_id"] for row in rows if row["depth"] == depth} == set(level)
    frontier_queries = [sql for sql in traced_sql if "/* tweetnook_reply_frontier */" in sql]
    assert len(frontier_queries) == 7
    assert all("WITH RECURSIVE" not in sql for sql in frontier_queries)
    store.close()


def test_api_tweet_thread_parses_each_tweet_json_once_and_tolerates_malformed(monkeypatch):
    class MalformedThreadStore(ThreadStore):
        def _query(self, expr: str, params=(), **kwargs: Any) -> list[dict[str, Any]]:
            rows = super()._query(expr, params, **kwargs)
            if "record_type = 'tweet_object'" in expr:
                rows[0]["raw_json"] = "not-json"
            return rows

    original_loads = fastjson.loads
    parsed_values: list[object] = []

    def track_loads(value: object) -> Any:
        parsed_values.append(value)
        return original_loads(value)

    monkeypatch.setattr(fastjson, "loads", track_loads)
    response = api_tweet_thread("main", store=MalformedThreadStore(), _auth=True)

    # Seven tweet objects and two direct media-tag rows; the malformed main has no quote.
    assert len(parsed_values) == 9
    assert original_loads(response.body)["main"]["raw_json"] is None


class CycleStore(ThreadStore):
    def _query(
        self, expr: str, params=(), *, limit: int | None = None, **kwargs: Any
    ) -> list[dict[str, Any]]:
        if "record_type = 'tweet_relation'" in expr and "quote_of" not in expr:
            self.expressions.append(expr)
            self.relation_call += 1
            if self.relation_call == 1:
                return [
                    {
                        "tweet_id": "main",
                        "target_tweet_id": "parent",
                        "relation_type": "reply_to",
                    }
                ]
            if self.relation_call == 2:
                return [
                    {
                        "tweet_id": "parent",
                        "target_tweet_id": "main",
                        "relation_type": "reply_to",
                    }
                ]
            return []
        return super()._query(expr, params, limit=limit, **kwargs)


def test_api_tweet_thread_stops_parent_cycles_before_depth_cap():
    store = CycleStore()
    result = _decoded(api_tweet_thread("main", store=store, _auth=True))
    assert result["main"]["tweet_id"] == "main"
    assert store.relation_call < 10


def test_api_tweet_thread_preserves_relation_only_placeholders(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    store._merge_records(
        [
            store._record(
                row_key="tweet_object:main",
                record_type="tweet_object",
                tweet_id="main",
                text="main post",
                author_id="u1",
                author_username="alice",
                author_display_name="Alice",
                created_at="2026-08-10T00:00:00+00:00",
                raw_json=json.dumps({"rest_id": "main", "legacy": {}}),
                enrichment_state="done",
            ),
            store._record(
                row_key="tweet_relation:main:reply_to:missing-parent",
                record_type="tweet_relation",
                tweet_id="main",
                relation_type="reply_to",
                target_tweet_id="missing-parent",
            ),
        ]
    )

    result = _decoded(api_tweet_thread("main", store=store, _auth=True))
    missing_main = _decoded(api_tweet_thread("missing-parent", store=store, _auth=True))

    assert [parent["tweet_id"] for parent in result["parents"]] == ["missing-parent"]
    assert result["parents"][0]["availability"] == {
        "state": "not_archived",
        "enrichment_state": None,
        "reason": "not_archived",
        "message": "This tweet was not captured in the local archive.",
        "placeholder": True,
        "confirmed": False,
        "retryable": None,
        "checked_at": None,
    }
    assert missing_main["main"]["availability"]["state"] == "not_archived"
    assert [child["tweet_id"] for child in missing_main["children"]] == ["main"]
    with pytest.raises(fastapi.HTTPException) as exc:
        api_tweet_thread("unknown", store=store, _auth=True)
    assert exc.value.status_code == 404
    store.close()


def _reply_tweet(text: str, *, author: str = "carol", likes: int = 0, **legacy: Any) -> dict:
    prefix = "@alice "
    return {
        "author": {"username": author},
        "collections": [],
        "media": [],
        "raw_json": {
            "legacy": {
                "full_text": prefix + text,
                "display_text_range": [len(prefix), len(prefix + text)],
                "favorite_count": likes,
                "entities": legacy.pop("entities", {}),
                **legacy,
            }
        },
    }


@pytest.mark.parametrize(
    ("tweet", "expected"),
    [
        (_reply_tweet("@grok"), True),
        (_reply_tweet("@Grok @DownloaderBot !"), True),
        (_reply_tweet("@grok\n\n@bot"), True),
        (_reply_tweet("@alice"), False),
        (_reply_tweet("@carol"), False),
        (_reply_tweet(""), False),
        (_reply_tweet("@grok is this true?"), False),
        (_reply_tweet("@grok", likes=4), True),
        (_reply_tweet("@grok", likes=5), False),
        (_reply_tweet("@grok", entities={"urls": [{"url": "https://t.co/x"}]}), False),
        (_reply_tweet("@grok", entities={"hashtags": [{"text": "tag"}]}), False),
        (_reply_tweet("@grok", is_quote_status=True), False),
        ({**_reply_tweet("@grok"), "media": [{"type": "photo"}]}, False),
        ({**_reply_tweet("@grok"), "collections": ["bookmark"]}, False),
        ({**_reply_tweet("@grok"), "op_replies": [{"collections": ["like"]}]}, False),
        ({"author": {"username": "carol"}, "raw_json": None}, False),
    ],
)
def test_is_other_reply_matches_only_low_like_mentions_of_other_accounts(tweet, expected):
    assert _is_other_reply(tweet, "alice") is expected


def test_api_tweet_thread_moves_mention_only_replies_to_other_children(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    tweets = {
        "100": ("alice", "a real post", 0),
        "200": ("carol", "@alice @grok", 0),
        "300": ("dave", "@alice @grok", 9),
        "400": ("erin", "@alice nice one", 0),
    }
    records = []
    for tweet_id, (author, text, likes) in tweets.items():
        start = len("@alice ") if tweet_id != "100" else 0
        raw = {
            "rest_id": tweet_id,
            "legacy": {
                "full_text": text,
                "display_text_range": [start, len(text)],
                "favorite_count": likes,
                "conversation_id_str": "100",
                "in_reply_to_status_id_str": None if tweet_id == "100" else "100",
            },
        }
        records.append(
            store._record(
                row_key=f"tweet_object:{tweet_id}",
                record_type="tweet_object",
                tweet_id=tweet_id,
                text=text,
                author_id=f"id-{author}",
                author_username=author,
                author_display_name=author.title(),
                created_at=f"2026-01-0{tweet_id[0]}T00:00:00Z",
                raw_json=json.dumps(raw),
                enrichment_state="done",
            )
        )
        if tweet_id != "100":
            records.append(
                store._record(
                    row_key=f"tweet_relation:{tweet_id}:reply_to:100",
                    record_type="tweet_relation",
                    tweet_id=tweet_id,
                    relation_type="reply_to",
                    target_tweet_id="100",
                )
            )
    store._merge_records(records)

    result = _decoded(api_tweet_thread("100", store=store, _auth=True))

    assert [child["tweet_id"] for child in result["children"]] == ["300", "400"]
    assert [child["tweet_id"] for child in result["other_children"]] == ["200"]
    assert result["other_children"][0]["thread_depth"] == 1
    store.close()


def test_api_tweet_thread_binds_untrusted_path_id_instead_of_writing_it_into_sql():
    payload = "main' OR 1=1 --"

    class MissingStore(ConversationsMixin):
        def __init__(self) -> None:
            self.queries: list[tuple[str, tuple[object, ...]]] = []
            self.conn = self

        def _query(self, expr: str, params=(), **_: Any) -> list[dict[str, Any]]:
            self.queries.append((expr, tuple(params)))
            return []

        def execute(self, _sql: str, _params: tuple[object, ...]):
            return self

        def fetchall(self) -> list[tuple[object, ...]]:
            return []

    store = MissingStore()
    with pytest.raises(fastapi.HTTPException) as exc:
        api_tweet_thread(payload, store=store, _auth=True)
    assert exc.value.status_code == 404
    assert store.queries
    assert all(payload in params for _expr, params in store.queries)
    assert all("OR 1=1" not in expr for expr, _params in store.queries)
