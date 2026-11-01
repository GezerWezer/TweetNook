from __future__ import annotations

import asyncio

import httpx
import pytest

from tests.collection_sync.conftest import (
    _console,
    _op_and_variables,
    _save_query_ids,
    _seed_like_import,
)
from tests.conftest import make_bookmarks_response, make_likes_response
from tweetnook.core.exceptions import ConfigError
from tweetnook.storage import open_archive_store
from tweetnook.sync.collection import SyncMode, sync_all, sync_collection


async def _sync(paths, config, auth_bundle, pages, **kwargs):
    requested = []
    unavailable_ids = kwargs.pop("unavailable_ids", set())

    def handler(request):
        _, variables = _op_and_variables(request)
        cursor = variables.get("cursor")
        if variables["count"] == 1:
            return httpx.Response(200, json=make_likes_response(["900"]))
        number = 0 if cursor is None else int(cursor)
        requested.append(number)
        assert number < len(pages), "catch-up fetched past its overlap boundary"
        payload = make_likes_response(pages[number], cursor=str(number + 1))
        entries = payload["data"]["user"]["result"]["timeline_v2"]["timeline"]["instructions"][0][
            "entries"
        ]
        for entry in entries:
            if entry["entryId"].removeprefix("tweet-") in unavailable_ids:
                entry["content"]["itemContent"]["tweet_results"]["result"] = {
                    "__typename": "TweetUnavailable",
                    "reason": "Unavailable",
                }
        return httpx.Response(200, json=payload)

    result = await sync_collection(
        "likes",
        full=False,
        catch_up=kwargs.pop("catch_up", True),
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        query_ids={"Likes": "qid-likes"},
        transport=httpx.MockTransport(handler),
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
        **kwargs,
    )
    return result, requested


async def test_catch_up_requires_consecutive_imported_pages(paths, config, auth_bundle):
    store = open_archive_store(paths, create=True)
    _seed_like_import(store, ["1", "2", "3", "4", "5", "6"])
    store.set_sync_state(
        "like", last_head_tweet_id="1", backfill_cursor="old", backfill_incomplete=True
    )
    store.close()

    result, requested = await _sync(
        paths,
        config,
        auth_bundle,
        [["900", "1"], ["2"], ["899"], ["3", "4"], ["5", "6"]],
    )

    assert requested == [0, 1, 2, 3, 4]
    assert result.pages_fetched == 5
    assert result.tweets_seen == 8
    assert result.stop_reason == "import-overlap"
    store = open_archive_store(paths, create=False)
    try:
        assert store.get_collection_tweet_ids("like") == {
            "900",
            "899",
            "1",
            "2",
            "3",
            "4",
            "5",
            "6",
        }
        state = store.get_sync_state("like")
        assert state.last_head_tweet_id == "900"
        assert state.backfill_cursor is None
        assert not state.backfill_incomplete
        assert (
            store.conn.execute("SELECT COUNT(*) FROM archive WHERE operation = 'Likes'").fetchone()[
                0
            ]
            == 5
        )
        assert store.get_complete_import_like_ids() == frozenset({"1", "2", "3", "4", "5", "6"})
    finally:
        store.close()


async def test_newly_saved_likes_and_repeated_pages_do_not_count_as_import_overlap(
    paths, config, auth_bundle
):
    store = open_archive_store(paths, create=True)
    _seed_like_import(store, ["1", "2", "3"])
    store.close()
    result, requested = await _sync(
        paths,
        config,
        auth_bundle,
        [["900"], ["900"], ["1"], ["1"], ["2"], ["3"]],
    )
    assert requested == [0, 1, 2, 3, 4, 5]
    assert result.stop_reason == "import-overlap"


async def test_unknown_unavailable_like_resets_import_overlap(paths, config, auth_bundle):
    store = open_archive_store(paths, create=True)
    _seed_like_import(store, ["1", "2", "3", "4"])
    store.close()
    result, requested = await _sync(
        paths,
        config,
        auth_bundle,
        [["1"], ["2", "999"], ["3"], ["4"]],
        unavailable_ids={"999"},
    )
    assert requested == [0, 1, 2, 3]
    assert result.stop_reason == "import-overlap"
    assert result.tweets_seen == 4


@pytest.mark.parametrize(
    "status,partial", [("sampled", False), ("in_progress", False), ("completed", True)]
)
async def test_catch_up_does_not_use_incomplete_imports(
    paths, config, auth_bundle, status, partial
):
    store = open_archive_store(paths, create=True)
    _seed_like_import(store, ["1", "2"], status=status, partial=partial)
    store.close()
    result, requested = await _sync(paths, config, auth_bundle, [["1"], ["2"], ["899"], []])
    assert requested == [0, 1, 2, 3]
    assert result.stop_reason == "empty"


async def test_catch_up_without_an_import_walks_to_end(paths, config, auth_bundle):
    result, requested = await _sync(paths, config, auth_bundle, [["900"], ["899"], []])
    assert requested == [0, 1, 2]
    assert result.stop_reason == "empty"


async def test_catch_up_limit_and_retry_do_not_stop_at_partial_head(paths, config, auth_bundle):
    store = open_archive_store(paths, create=True)
    _seed_like_import(store, ["1", "2", "3"])
    store.close()
    pages = [["900"], ["1"], ["899"], ["2"], ["3"]]
    limited, requested = await _sync(paths, config, auth_bundle, pages, limit=2)
    assert requested == [0, 1]
    assert limited.stop_reason == "limit"
    store = open_archive_store(paths, create=False)
    assert store.get_sync_state("like").backfill_cursor == "2"
    assert store.get_sync_state("like").backfill_incomplete
    store.close()
    retried, requested = await _sync(paths, config, auth_bundle, pages)
    assert requested == [0, 1, 2, 3, 4]
    assert retried.stop_reason == "import-overlap"


async def test_normal_sync_after_catch_up_uses_head_and_backfill_remains_available(
    paths, config, auth_bundle
):
    store = open_archive_store(paths, create=True)
    _seed_like_import(store, ["1", "2"])
    store.close()
    await _sync(paths, config, auth_bundle, [["900"], ["1"], ["2"]])
    normal, requested = await _sync(paths, config, auth_bundle, [["900"]], catch_up=False)
    assert requested == [0]
    assert normal.stop_reason == "duplicate"
    backfill, requested = await _sync(
        paths,
        config,
        auth_bundle,
        [["900"], ["1"], ["2"], ["899"], []],
        catch_up=False,
        backfill=True,
    )
    assert requested == [0, 1, 2, 3, 4]
    assert backfill.stop_reason == "empty"


@pytest.mark.parametrize("options", [{"full": True}, {"backfill": True}, {"head_only": True}])
async def test_catch_up_conflicts_fail_before_preflight(
    paths, config, auth_bundle, monkeypatch, options
):
    import tweetnook.sync.collection as collection

    async def unexpected(**kwargs):
        pytest.fail("invalid flags reached preflight")

    monkeypatch.setattr(collection, "run_preflight", unexpected)
    with pytest.raises(ConfigError, match="--catch-up cannot be combined"):
        await sync_collection(
            "likes", config=config, paths=paths, catch_up=True, **({"full": False} | options)
        )
    assert not paths.database_path.exists()


async def test_catch_up_all_keeps_other_collections_backfilling(paths, config, auth_bundle):
    _save_query_ids(paths)
    config.sync.collections = ["bookmarks", "likes"]
    store = open_archive_store(paths, create=True)
    _seed_like_import(store, ["1", "2"])
    store.set_sync_state("bookmark", last_head_tweet_id="10")
    store.close()
    requested = {"Bookmarks": [], "Likes": []}

    def handler(request):
        operation, variables = _op_and_variables(request)
        response_for = make_likes_response if operation == "Likes" else make_bookmarks_response
        if variables["count"] == 1:
            return httpx.Response(200, json=response_for(["1"]))
        cursor = variables.get("cursor")
        requested[operation].append(cursor)
        if operation == "Bookmarks":
            payload = (
                response_for(["10"], cursor="older") if cursor is None else response_for(["9"])
            )
        else:
            payload = response_for(
                ["1"] if cursor is None else ["2"], cursor="older" if cursor is None else "end"
            )
        return httpx.Response(200, json=payload)

    outcome = await sync_all(
        mode=SyncMode.CATCH_UP,
        limit=None,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(handler),
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
    )
    assert outcome.exit_code == 0
    assert requested == {"Bookmarks": [None, "older"], "Likes": [None, "older"]}
    assert [r.stop_reason for r in outcome.results] == ["head-complete", "import-overlap"]
