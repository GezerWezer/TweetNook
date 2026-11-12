import json
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tweetnook.storage.backend import ArchiveStore
from tweetnook.web import card_cache
from tweetnook.web.deps import get_store
from tweetnook.web.routes.tweets import router as tweets_router


def _member(store: ArchiveStore, tweet_id: str, text: str, ts: int) -> None:
    raw = {"rest_id": tweet_id, "legacy": {"full_text": text}}
    store._merge_records(
        [
            store._record(
                row_key=f"tweet:like::{tweet_id}",
                record_type="tweet",
                tweet_id=tweet_id,
                collection_type="like",
                text=text,
                created_at_ts=ts,
                raw_json=json.dumps(raw),
            )
        ]
    )


def _page(client: TestClient, query: str = "collection=all&sort=newest") -> dict:
    response = client.get(f"/api/tweets?{query}")
    card_cache._WRITER.submit(lambda: None).result()  # Let the stored cards land.
    return response.json()


def test_feed_cards_are_stored_reused_and_retired_by_writes(tmp_path, monkeypatch) -> None:
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    _member(store, "1", "first", 1)
    _member(store, "2", "second", 2)
    app = FastAPI()
    app.include_router(tweets_router)
    app.dependency_overrides[get_store] = lambda: store
    try:
        with TestClient(app) as client:
            built = _page(client)
            real_fetch = store.fetch_tweets_by_ids
            calls: list[list[str]] = []

            def tracking_fetch(ids, **kwargs):
                calls.append(list(ids))
                return real_fetch(ids, **kwargs)

            monkeypatch.setattr(store, "fetch_tweets_by_ids", tracking_fetch)
            reused = _page(client)
            assert calls == []
            assert reused == built
            assert [t["tweet_id"] for t in reused["tweets"]] == ["2", "1"]
            liked = _page(client, "collection=likes&sort=newest")
            assert calls == [["2", "1"]]  # Another filter has its own cards.

            # Any archive write retires every stored card.
            _member(store, "1", "first, edited", 1)
            calls.clear()
            changed = _page(client)
            assert calls == [["2", "1"]]
            assert changed["tweets"][1]["text"] == "first, edited"
            assert liked["tweets"][0]["collection"]["type"] == "like"
    finally:
        store.close()


def test_card_triggers_need_no_python_functions(tmp_path) -> None:
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    store.close()
    # A plain connection, like the sqlite3 shell, can still write to the archive.
    conn = sqlite3.connect(tmp_path / "archive.db")
    before = conn.execute("SELECT generation FROM tweetnook_web_cards_state").fetchone()[0]
    with conn:
        conn.execute(
            "INSERT INTO community_notes (tweet_id, text_hash, text, raw_json, first_seen_at, "
            "last_seen_at) VALUES ('1', 'h', 'note', '{}', 't', 't')"
        )
    after = conn.execute("SELECT generation FROM tweetnook_web_cards_state").fetchone()[0]
    conn.close()
    assert after > before


def test_stored_cards_carry_each_search_hit_score(tmp_path) -> None:
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    _member(store, "1", "apple pie", 1)
    _member(store, "2", "apple apple tart", 2)
    app = FastAPI()
    app.include_router(tweets_router)
    app.dependency_overrides[get_store] = lambda: store
    try:
        with TestClient(app) as client:
            built = _page(client, "collection=all&sort=relevance&q=apple")
            reused = _page(client, "collection=all&sort=relevance&q=apple")
            browsed = _page(client)
    finally:
        store.close()
    assert reused == built
    assert {tweet["tweet_id"] for tweet in built["tweets"]} == {"1", "2"}
    assert all(isinstance(tweet["match_score"], float) for tweet in built["tweets"])
    # The same stored cards serve a browse page, which has no scores.
    assert all("match_score" not in tweet for tweet in browsed["tweets"])
    assert built["highlight_terms"] == ["apple"]
