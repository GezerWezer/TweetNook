"""Tests for derived CLIP/SigLIP media embedding storage and scoring."""

from __future__ import annotations

import array
from pathlib import Path

from tweetnook.storage.backend import ArchiveStore
from tweetnook.storage.record_types import RecordType


def _vec_bytes(values: list[float]) -> bytes:
    return array.array("f", values).tobytes()


def _seed_tweet_with_media(
    store: ArchiveStore,
    *,
    tweet_id: str,
    collection: str = "likes",
    media_key: str,
    media_type: str = "photo",
    local_path: str | None = None,
    thumbnail_local_path: str | None = None,
    download_state: str = "done",
) -> str:
    media_row_key = f"media:{tweet_id}:{media_key}"
    with store.transaction():
        store.conn.execute(
            f"INSERT INTO archive (row_key, record_type, tweet_id, collection_type, text, "
            f"created_at_ts, sort_index) VALUES (?, '{RecordType.TWEET}', ?, ?, ?, ?, ?)",
            (
                f"tweet:{tweet_id}:{collection}",
                tweet_id,
                collection,
                f"post {tweet_id}",
                1000,
                tweet_id,
            ),
        )
        store.conn.execute(
            f"INSERT INTO archive (row_key, record_type, tweet_id, media_key, media_type, "
            f"local_path, thumbnail_local_path, download_state, position) "
            f"VALUES (?, '{RecordType.MEDIA}', ?, ?, ?, ?, ?, ?, 0)",
            (
                media_row_key,
                tweet_id,
                media_key,
                media_type,
                local_path,
                thumbnail_local_path,
                download_state,
            ),
        )
    return media_row_key


def test_pending_embedding_media_and_thumbnail_toggle(tmp_path: Path) -> None:
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    try:
        rk_photo = _seed_tweet_with_media(
            store,
            tweet_id="101",
            media_key="3_101",
            media_type="photo",
            local_path="photos/101.jpg",
        )
        rk_video = _seed_tweet_with_media(
            store,
            tweet_id="102",
            media_key="7_102",
            media_type="video",
            local_path="videos/102.mp4",
            thumbnail_local_path="thumbnails/102.jpg",
        )
        _seed_tweet_with_media(
            store,
            tweet_id="103",
            media_key="3_103",
            media_type="photo",
            local_path=None,
            download_state="pending",
        )

        without_thumbs = store.pending_embedding_media(
            "ViT-B-16-SigLIP", include_video_thumbnails=False
        )
        assert [row["media_row_key"] for row in without_thumbs] == [rk_photo]

        with_thumbs = store.pending_embedding_media(
            "ViT-B-16-SigLIP", include_video_thumbnails=True
        )
        assert {row["media_row_key"] for row in with_thumbs} == {rk_photo, rk_video}
        by_key = {row["media_row_key"]: row for row in with_thumbs}
        assert by_key[rk_video]["file_path"] == "thumbnails/102.jpg"

        store.upsert_media_embeddings(
            [(rk_photo, "101", "ViT-B-16-SigLIP", 3, _vec_bytes([1.0, 0.0, 0.0]), None)]
        )
        stats = store.embedding_stats("ViT-B-16-SigLIP", include_video_thumbnails=True)
        assert stats == {"indexed": 1, "pending": 1, "total": 2}
    finally:
        store.close()


def test_search_media_embeddings_filters_and_ranks(tmp_path: Path) -> None:
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    try:
        rk1 = _seed_tweet_with_media(
            store,
            tweet_id="201",
            collection="likes",
            media_key="3_201",
            local_path="photos/201.jpg",
        )
        rk2 = _seed_tweet_with_media(
            store,
            tweet_id="202",
            collection="bookmarks",
            media_key="3_202",
            local_path="photos/202.jpg",
        )
        rk3 = _seed_tweet_with_media(
            store,
            tweet_id="203",
            collection="likes",
            media_key="3_203",
            local_path="photos/203.jpg",
        )
        store.upsert_media_embeddings(
            [
                (rk1, "201", "ViT-B-16-SigLIP", 3, _vec_bytes([0.8, 0.6, 0.0]), None),
                (rk2, "202", "ViT-B-16-SigLIP", 3, _vec_bytes([1.0, 0.0, 0.0]), None),
                (rk3, "203", "ViT-B-16-SigLIP", 3, _vec_bytes([0.0, 1.0, 0.0]), None),
            ]
        )

        query = _vec_bytes([1.0, 0.0, 0.0])
        all_matches = store.search_media_embeddings(
            query, "ViT-B-16-SigLIP", min_score=0.5, limit=10
        )
        assert [tid for tid, _ in all_matches] == ["202", "201"]
        assert all_matches[0][1] > all_matches[1][1]

        likes_only = store.search_media_embeddings(
            query,
            "ViT-B-16-SigLIP",
            collections={"likes"},
            min_score=0.5,
            limit=10,
        )
        assert [tid for tid, _ in likes_only] == ["201"]

        store.set_tweet_hidden("201", True)
        after_hide = store.search_media_embeddings(
            query,
            "ViT-B-16-SigLIP",
            collections={"likes"},
            exclude_hidden=True,
            min_score=0.5,
            limit=10,
        )
        assert after_hide == []
    finally:
        store.close()


def test_quoted_and_thread_media_options(tmp_path: Path) -> None:
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    try:
        rk_saved = _seed_tweet_with_media(
            store,
            tweet_id="301",
            media_key="3_301",
            local_path="photos/301.jpg",
        )
        with store.transaction():
            # Quoted tweet (TWEET_OBJECT referenced via quote_of from saved tweet 301)
            store.conn.execute(
                f"INSERT INTO archive (row_key, record_type, tweet_id, text) "
                f"VALUES ('obj:302', '{RecordType.TWEET_OBJECT}', '302', 'quoted post')"
            )
            store.conn.execute(
                f"INSERT INTO archive (row_key, record_type, tweet_id, relation_type, "
                f"target_tweet_id) VALUES ('rel:301:302', '{RecordType.TWEET_RELATION}', "
                f"'301', 'quote_of', '302')"
            )
            store.conn.execute(
                f"INSERT INTO archive (row_key, record_type, tweet_id, media_key, media_type, "
                f"local_path, download_state, position) "
                f"VALUES ('media:302:3_302', '{RecordType.MEDIA}', '302', '3_302', 'photo', "
                f"'photos/302.jpg', 'done', 0)"
            )
            # Expanded thread reply (TWEET_OBJECT not quoted by any saved tweet)
            store.conn.execute(
                f"INSERT INTO archive (row_key, record_type, tweet_id, text) "
                f"VALUES ('obj:303', '{RecordType.TWEET_OBJECT}', '303', 'thread reply')"
            )
            store.conn.execute(
                f"INSERT INTO archive (row_key, record_type, tweet_id, media_key, media_type, "
                f"local_path, download_state, position) "
                f"VALUES ('media:303:3_303', '{RecordType.MEDIA}', '303', '3_303', 'photo', "
                f"'photos/303.jpg', 'done', 0)"
            )

        # Default: saved + quoted included, thread reply excluded
        default_pending = store.pending_embedding_media("ViT-B-16-SigLIP")
        assert {row["media_row_key"] for row in default_pending} == {rk_saved, "media:302:3_302"}

        # Quoted opt-out: only saved
        no_quoted = store.pending_embedding_media("ViT-B-16-SigLIP", include_quoted_media=False)
        assert [row["media_row_key"] for row in no_quoted] == [rk_saved]

        # Thread opt-in: all three
        with_thread = store.pending_embedding_media("ViT-B-16-SigLIP", include_thread_media=True)
        assert {row["media_row_key"] for row in with_thread} == {
            rk_saved,
            "media:302:3_302",
            "media:303:3_303",
        }

        # Searching quoted media surfaces the saved quoting tweet (301)
        store.upsert_media_embeddings(
            [("media:302:3_302", "302", "ViT-B-16-SigLIP", 3, _vec_bytes([1.0, 0.0, 0.0]), None)]
        )
        matches = store.search_media_embeddings(
            _vec_bytes([1.0, 0.0, 0.0]), "ViT-B-16-SigLIP", min_score=0.5
        )
        assert [tid for tid, _ in matches] == ["301"]
        assert (
            store.search_media_embeddings(
                _vec_bytes([1.0, 0.0, 0.0]),
                "ViT-B-16-SigLIP",
                include_quoted_media=False,
                min_score=0.5,
            )
            == []
        )
    finally:
        store.close()
