"""Tests for Smart Search settings routes and `smart:` query prefix on /api/tweets."""

from __future__ import annotations

import array
from pathlib import Path

from tweetnook.config import AppConfig, XDGPaths
from tweetnook.storage.backend import ArchiveStore
from tweetnook.storage.record_types import RecordType
from tweetnook.web.routes import smart_search as smart_routes
from tweetnook.web.routes import tweets as tweets_routes


class _FakeTextVec:
    def __init__(self, values: list[float]) -> None:
        self._bytes = array.array("f", values).tobytes()

    def tobytes(self) -> bytes:
        return self._bytes


class _FakeQueryEncoder:
    def encode_text(self, query: str) -> _FakeTextVec:
        return _FakeTextVec([1.0, 0.0, 0.0])


def _setup_store(tmp_path: Path, monkeypatch) -> ArchiveStore:
    cfg_dir = tmp_path / "cfg"
    data_dir = tmp_path / "data"
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg_dir))
    monkeypatch.setenv("XDG_DATA_HOME", str(data_dir))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_dir))

    paths = XDGPaths(
        config_dir=cfg_dir / "tweetnook",
        data_dir=data_dir / "tweetnook",
        cache_dir=cache_dir / "tweetnook",
    )
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    return ArchiveStore(paths.data_dir / "archive.db", create=True, config=AppConfig())


def test_smart_search_settings_routes(tmp_path: Path, monkeypatch, make_web_client) -> None:
    store = _setup_store(tmp_path, monkeypatch)
    client = make_web_client(smart_routes.router, store=store)
    try:
        res = client.get("/api/smart-search")
        assert res.status_code == 200
        payload = res.json()
        assert payload["values"]["model"] == "ViT-B-16-SigLIP"
        assert payload["values"]["include_video_thumbnails"] is True
        assert payload["values"]["include_quoted_media"] is True
        assert payload["values"]["include_thread_media"] is False

        stats_res = client.get("/api/smart-search/stats")
        assert stats_res.status_code == 200
        assert stats_res.json()["stats"] == {"indexed": 0, "pending": 0, "total": 0}

        put_res = client.put(
            "/api/smart-search",
            json={"values": {"enabled": True, "min_score": 0.25}},
        )
        assert put_res.status_code == 200
        assert put_res.json()["values"]["enabled"] is True
        assert put_res.json()["values"]["min_score"] == 0.25

        bad_res = client.put("/api/smart-search", json={"values": {"unknown_key": 1}})
        assert bad_res.status_code == 400
    finally:
        store.close()


def test_api_tweets_smart_prefix(tmp_path: Path, monkeypatch, make_web_client) -> None:
    store = _setup_store(tmp_path, monkeypatch)
    monkeypatch.setattr(store, "pooled_reader", lambda: store)
    client = make_web_client(tweets_routes.router, store=store)
    try:
        with store.transaction():
            store.conn.execute(
                f"INSERT INTO archive (row_key, record_type, tweet_id, collection_type, text, "
                f"created_at_ts, sort_index) VALUES "
                f"('t:500', '{RecordType.TWEET}', '500', 'likes', 'sunset photo', 1000, '500')"
            )
            store.conn.execute(
                f"INSERT INTO archive (row_key, record_type, tweet_id, media_key, media_type, "
                f"local_path, download_state, position) VALUES "
                f"('m:500', '{RecordType.MEDIA}', '500', '3_500', 'photo', "
                f"'photos/500.jpg', 'done', 0)"
            )
        store.upsert_media_embeddings(
            [("m:500", "500", "ViT-B-16-SigLIP", 3, _FakeTextVec([1.0, 0.0, 0.0]).tobytes(), None)]
        )

        monkeypatch.setattr(tweets_routes, "smart_search_installed", lambda: False)
        missing_res = client.get("/api/tweets", params={"q": "smart:sunset"})
        assert missing_res.status_code == 400

        monkeypatch.setattr(tweets_routes, "smart_search_installed", lambda: True)
        disabled_res = client.get("/api/tweets", params={"q": "smart:sunset"})
        assert disabled_res.status_code == 400

        enabled_cfg = AppConfig()
        enabled_cfg.smart_search.enabled = True
        monkeypatch.setattr(
            tweets_routes,
            "load_config",
            lambda: (
                enabled_cfg,
                XDGPaths(
                    config_dir=tmp_path / "cfg" / "tweetnook",
                    data_dir=tmp_path / "data" / "tweetnook",
                    cache_dir=tmp_path / "cache" / "tweetnook",
                ),
            ),
        )
        monkeypatch.setattr(
            tweets_routes,
            "get_cached_encoder",
            lambda model, cache: _FakeQueryEncoder(),
        )
        ok_res = client.get("/api/tweets", params={"q": "smart:sunset"})
        assert ok_res.status_code == 200
        data = ok_res.json()
        assert data["total"] == 1
        assert len(data["tweets"]) == 1
        assert data["tweets"][0]["tweet_id"] == "500"

        count_res = client.get("/api/tweets/count", params={"q": "smart:sunset"})
        assert count_res.status_code == 200
        assert count_res.json() == {"total": 1}

        mixed_res = client.get("/api/tweets", params={"q": "since_id:999 smart:sunset"})
        assert mixed_res.status_code == 200
        assert mixed_res.json()["total"] == 0

        mixed_match_res = client.get("/api/tweets", params={"q": "smart:sunset max_id:500"})
        assert mixed_match_res.status_code == 200
        assert mixed_match_res.json()["total"] == 1
    finally:
        from tweetnook.web.card_cache import _WRITER

        _WRITER.submit(lambda: None).result(timeout=5)
        store.close()
