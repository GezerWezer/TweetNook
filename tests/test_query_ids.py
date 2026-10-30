from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from tweetnook.query_ids.constants import FALLBACK_QUERY_IDS
from tweetnook.query_ids.scraper import (
    extract_bundle_urls,
    extract_candidate_chunk_urls,
    extract_query_ids,
)
from tweetnook.query_ids.store import QueryIdCache, QueryIdStore


def test_extract_bundle_urls() -> None:
    html = """
    <script src="https://abs.twimg.com/responsive-web/client-web/main.1234abcd.js"></script>
    <link rel="preload" href="https://abs.twimg.com/responsive-web/client-web/vendor.5678efgh.js">
    """
    urls = extract_bundle_urls(html)
    assert "https://abs.twimg.com/responsive-web/client-web/main.1234abcd.js" in urls
    assert "https://abs.twimg.com/responsive-web/client-web/vendor.5678efgh.js" in urls


def test_extract_query_ids_supports_multiple_patterns() -> None:
    snippet = """
    queryId:"abc1234567890123456789",operationName:"Bookmarks"
    {"queryId":"def1234567890123456789","operationName":"Likes"}
    {"queryId":"jkl1234567890123456789","operationName":"UserTweets"}
    operationName:"TweetDetail",queryId:"ghi1234567890123456789"
    """
    extracted = extract_query_ids(snippet)
    assert extracted["Bookmarks"] == "abc1234567890123456789"
    assert extracted["Likes"] == "def1234567890123456789"
    assert extracted["UserTweets"] == "jkl1234567890123456789"
    assert extracted["TweetDetail"] == "ghi1234567890123456789"


def test_extract_candidate_chunk_urls() -> None:
    manifest = (
        '"bundle.Bookmarks":"a060a2c",'
        '"shared~bundle.BookmarkFolders~bundle.Bookmarks":"877e5cb",'
        '"main":"1234abc"'
    )
    urls = extract_candidate_chunk_urls(manifest, ["Bookmarks", "Likes"])
    assert "https://abs.twimg.com/responsive-web/client-web/bundle.Bookmarks.a060a2ca.js" in urls
    assert (
        "https://abs.twimg.com/responsive-web/client-web/shared~bundle.BookmarkFolders~bundle.Bookmarks.877e5cba.js"
        in urls
    )


def test_query_id_store_freshness_and_fallback(paths) -> None:
    store = QueryIdStore(paths)
    saved = store.save({"Bookmarks": "fresh-bookmarks"})
    assert store.is_fresh(saved)
    assert store.get("Bookmarks") == "fresh-bookmarks"

    stale = QueryIdCache(
        fetched_at=datetime.now(tz=UTC) - timedelta(days=2),
        ttl_seconds=60,
        ids={"Bookmarks": "stale-bookmarks"},
    )
    store.path.write_text(stale.model_dump_json())
    assert not store.is_fresh(store.load())
    assert store.get("TweetDetail") is not None
    assert store.get("UserTweets") is not None


async def test_partial_refresh_preserves_unrelated_ids_and_their_expiry(paths, monkeypatch):
    import json

    from tweetnook.query_ids import scraper
    from tweetnook.query_ids.constants import FALLBACK_QUERY_IDS

    store = QueryIdStore(paths)
    stale = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    store.path.write_text(
        json.dumps(
            {
                "fetched_at": stale,
                "ttl_seconds": 60,
                "ids": {"Bookmarks": "saved-bookmarks", "Custom": "saved-custom"},
            }
        )
    )
    requests = []

    async def discover(client, *, operations):
        requests.append(list(operations))
        return {operation: f"discovered-{operation}" for operation in operations}

    monkeypatch.setattr(scraper, "discover_query_ids", discover)
    await scraper.refresh_query_ids(store, operations=["TweetDetail"])
    cache = store.load()
    assert cache.ids["Bookmarks"] == "saved-bookmarks"
    assert cache.ids["Custom"] == "saved-custom"
    assert cache.refreshed_at["Bookmarks"].isoformat() == stale
    assert store.get("Bookmarks") == FALLBACK_QUERY_IDS["Bookmarks"]
    assert store.get("Custom") == "saved-custom"
    assert store.is_fresh(operations=["TweetDetail"])
    assert not store.is_fresh(operations=["Bookmarks"])
    result = await scraper.resolve_query_ids(
        store, ["Bookmarks"], force_refresh=False, transport=None
    )
    assert result == {"Bookmarks": "discovered-Bookmarks"}
    assert requests == [["TweetDetail"], ["Bookmarks"]]


@pytest.mark.parametrize(
    "content", [b"{", b"\xff", b'{"ids": []}', b'{"fetched_at": "2026-01-01T00:00:00"}']
)
async def test_invalid_cache_is_a_miss_even_for_forced_refresh(paths, monkeypatch, content):
    from tweetnook.query_ids import scraper

    store = QueryIdStore(paths)
    store.path.write_bytes(content)
    calls = []

    async def discover(client, *, operations):
        calls.append(list(operations))
        return {"TweetDetail": "recovered-id"}

    monkeypatch.setattr(scraper, "discover_query_ids", discover)
    result = await scraper.resolve_query_ids(
        store, ["TweetDetail"], force_refresh=True, transport=None
    )
    assert result == {"TweetDetail": "recovered-id"}
    assert calls == [["TweetDetail"]]
    assert store.is_fresh(operations=["TweetDetail"])


def test_cache_permission_failure_remains_visible(paths, monkeypatch):
    from pathlib import Path

    store = QueryIdStore(paths)

    def denied(*args, **kwargs):
        raise PermissionError("cache unreadable")

    monkeypatch.setattr(Path, "read_text", denied)
    with pytest.raises(PermissionError):
        store.load()


async def test_operation_only_served_by_fallback_is_not_rediscovered_every_call(paths, monkeypatch):
    from tweetnook.query_ids import scraper

    store = QueryIdStore(paths)
    requests = []

    async def discover(client, *, operations):
        requests.append(list(operations))
        return {}

    monkeypatch.setattr(scraper, "discover_query_ids", discover)
    expected = {"Bookmarks": FALLBACK_QUERY_IDS["Bookmarks"]}
    for _ in range(2):
        result = await scraper.resolve_query_ids(
            store, ["Bookmarks"], force_refresh=False, transport=None
        )
        assert result == expected
    assert requests == [["Bookmarks"]]
    assert store.load().fetched_at is None


async def test_check_that_misses_a_saved_id_treats_it_as_stale(paths, monkeypatch):
    from tweetnook.query_ids import scraper

    store = QueryIdStore(paths)
    stale = (datetime.now(tz=UTC) - timedelta(days=2)).isoformat()
    store.path.write_text(
        json.dumps(
            {
                "fetched_at": stale,
                "ids": {"Bookmarks": "stale-id"},
                "refreshed_at": {"Bookmarks": stale},
            }
        )
    )

    async def discover(client, *, operations):
        return {}

    monkeypatch.setattr(scraper, "discover_query_ids", discover)
    await scraper.refresh_query_ids(store, operations=["Bookmarks"])
    assert store.is_fresh(operations=["Bookmarks"])
    assert store.get("Bookmarks") == FALLBACK_QUERY_IDS["Bookmarks"]


async def test_operation_with_no_id_is_retried_even_after_a_recent_check(paths, monkeypatch):
    from tweetnook.query_ids import scraper

    store = QueryIdStore(paths)
    store.save({}, checked=["NoFallback"])
    requests = []

    async def discover(client, *, operations):
        requests.append(list(operations))
        return {"NoFallback": "found"}

    monkeypatch.setattr(scraper, "discover_query_ids", discover)
    result = await scraper.resolve_query_ids(
        store, ["NoFallback"], force_refresh=False, transport=None
    )
    assert result == {"NoFallback": "found"}
    assert requests == [["NoFallback"]]


async def test_failed_refresh_keeps_usable_ids_and_does_not_mark_them_checked(paths, monkeypatch):
    from tweetnook.query_ids import scraper

    store = QueryIdStore(paths)

    async def discover(client, *, operations):
        raise httpx.ConnectError("bundles unavailable")

    monkeypatch.setattr(scraper, "discover_query_ids", discover)
    result = await scraper.resolve_query_ids(
        store, ["Bookmarks"], force_refresh=True, transport=None
    )
    assert result == {"Bookmarks": FALLBACK_QUERY_IDS["Bookmarks"]}
    assert not store.is_fresh(operations=["Bookmarks"])
    with pytest.raises(httpx.ConnectError):
        await scraper.resolve_query_ids(store, ["NoFallback"], force_refresh=False, transport=None)


def test_concurrent_saves_of_different_operations_keep_both(paths):
    store = QueryIdStore(paths)
    operations = [f"Operation{index}" for index in range(16)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda operation: store.save({operation: "id"}), operations))
    assert set(store.load().ids) == set(operations)


def test_empty_save_does_not_make_the_cache_look_fetched(paths):
    store = QueryIdStore(paths)
    assert store.save({}).fetched_at is None
    assert not store.path.exists()
