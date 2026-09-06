from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from tweetnook.web.routes import storage_stats


@pytest.mark.parametrize(
    ("size", "formatted"),
    [
        (0, "0 B"),
        (1023, "1023 B"),
        (1024, "1.00 KB"),
        (1024**2 * 1.5, "1.50 MB"),
        (1024**6, "1024.00 PB"),
    ],
)
def test_format_bytes(size: float, formatted: str) -> None:
    assert storage_stats.format_bytes(size) == formatted


def _storage_store(tmp_path: Path) -> SimpleNamespace:
    db_path = tmp_path / "archive.db"
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE archive (
            record_type TEXT,
            tweet_id TEXT,
            target_tweet_id TEXT,
            relation_type TEXT,
            local_path TEXT,
            thumbnail_local_path TEXT,
            media_type TEXT,
            raw_json TEXT,
            text TEXT,
            content_text TEXT,
            summary_text TEXT,
            author_id TEXT,
            author_username TEXT,
            author_display_name TEXT
        )
        """
    )
    conn.execute("CREATE INDEX idx_archive_record_page ON archive(record_type, tweet_id)")
    conn.execute("CREATE INDEX idx_archive_tweet_id ON archive(tweet_id)")
    conn.executemany(
        """
        INSERT INTO archive (
            record_type, tweet_id, target_tweet_id, relation_type,
            local_path, thumbnail_local_path, media_type, raw_json, text,
            content_text, summary_text, author_id, author_username,
            author_display_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "tweet",
                "t1",
                None,
                None,
                None,
                None,
                None,
                "{}",
                "hello",
                None,
                None,
                "a1",
                "one",
                "One",
            ),
            (
                "tweet_relation",
                "t1",
                "q1",
                "quote_of",
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
            ),
            (
                "media",
                "q1",
                None,
                None,
                "remote/core.jpg",
                "remote/core-poster.jpg",
                "photo",
                None,
                None,
                None,
                None,
                None,
                None,
                None,
            ),
            (
                "media",
                "context",
                None,
                None,
                "remote/context.mp4",
                None,
                "video",
                None,
                None,
                None,
                None,
                None,
                None,
                None,
            ),
            (
                "tweet_object",
                "context",
                None,
                None,
                None,
                None,
                None,
                "thread-json",
                None,
                None,
                None,
                "a2",
                "two",
                "Two",
            ),
            (
                "article",
                "t1",
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                "article",
                "summary",
                None,
                None,
                None,
            ),
            (
                "media_tag",
                "t1",
                None,
                None,
                None,
                None,
                None,
                '{"tags":["bird"]}',
                None,
                None,
                None,
                None,
                None,
                None,
            ),
        ],
    )
    conn.commit()

    media_dir = tmp_path / "media"
    (media_dir / "avatars").mkdir(parents=True)
    (media_dir / "core.jpg").write_bytes(b"c" * 11)
    (media_dir / "core-poster.jpg").write_bytes(b"p" * 5)
    (media_dir / "context.mp4").write_bytes(b"v" * 13)
    (media_dir / "avatars" / "a1.jpg").write_bytes(b"a" * 7)
    return SimpleNamespace(conn=conn, db_path=db_path)


def test_storage_breakdown_accounts_for_database_media_and_avatars(
    make_web_client, tmp_path: Path
) -> None:
    store = _storage_store(tmp_path)
    db_bytes = store.db_path.stat().st_size
    client = make_web_client(storage_stats.router, store=store)

    response = client.get("/api/storage/breakdown")

    assert response.status_code == 200
    data = response.json()
    assert data["total_bytes"] == db_bytes + 11 + 5 + 13 + 7
    assert data["formatted_total"] == storage_stats.format_bytes(data["total_bytes"])
    by_id = {segment["id"]: segment for segment in data["segments"]}
    assert by_id["core_media"]["bytes"] == 11
    assert by_id["core_media"]["count"] == 1
    assert by_id["core_media"]["formatted_count"] == "1 photos · 0 videos/gifs"
    assert by_id["context_media"]["bytes"] == 13
    assert by_id["context_media"]["count"] == 1
    assert by_id["supplementary_media"]["bytes"] == 5
    assert by_id["supplementary_media"]["count"] == 1
    assert by_id["supplementary_media"]["formatted_count"] == "1 supporting files"
    assert by_id["avatars"]["bytes"] == 7
    assert by_id["avatars"]["count"] == 1
    assert by_id["core_db"]["count"] == 1
    assert by_id["threads"]["count"] == 1
    assert by_id["articles"]["count"] == 1
    assert by_id["tags"]["count"] == 1
    assert by_id["user_profiles"]["count"] == 2
    assert all(segment["bytes"] > 0 for segment in data["segments"])
    assert [segment["bytes"] for segment in data["segments"]] == sorted(
        (segment["bytes"] for segment in data["segments"]),
        reverse=True,
    )

    simplified = {segment["id"]: segment for segment in data["simplified_segments"]}
    assert simplified["database"]["bytes"] == db_bytes
    assert simplified["media"]["bytes"] == 36
    assert simplified["media"]["count"] == 4
    assert simplified["media"]["formatted_count"] == (
        "1 photos · 1 videos · 1 supplementary · 1 avatars"
    )


def test_storage_breakdown_reads_media_once_and_probes_relations_by_tweet_id(
    make_web_client, tmp_path: Path
) -> None:
    store = _storage_store(tmp_path)
    statements: list[str] = []
    store.conn.set_trace_callback(statements.append)
    client = make_web_client(storage_stats.router, store=store)

    response = client.get("/api/storage/breakdown")

    assert response.status_code == 200
    media_reads = [
        statement
        for statement in statements
        if "SELECT tweet_id, local_path, thumbnail_local_path, media_type" in statement
    ]
    assert len(media_reads) == 1
    assert "INDEXED BY idx_archive_record_page" in media_reads[0]
    relation_reads = [
        statement
        for statement in statements
        if "SELECT target_tweet_id" in statement and "relation_type IN" in statement
    ]
    assert len(relation_reads) == 1
    assert "INDEXED BY idx_archive_tweet_id" in relation_reads[0]
    assert all("NOT IN" not in statement for statement in statements)
    payload_samples = [statement for statement in statements if "sum(payload_bytes)" in statement]
    assert len(payload_samples) == 5
    assert all("LIMIT 4096" in statement for statement in payload_samples)


def test_storage_breakdown_falls_back_to_database_media_counts(
    make_web_client, tmp_path: Path
) -> None:
    store = _storage_store(tmp_path)
    for path in (tmp_path / "media").rglob("*"):
        if path.is_file():
            path.unlink()
    client = make_web_client(storage_stats.router, store=store)

    data = client.get("/api/storage/breakdown").json()

    by_id = {segment["id"]: segment for segment in data["segments"]}
    assert by_id["core_media"]["bytes"] == 0
    assert by_id["core_media"]["count"] == 1
    assert by_id["context_media"]["bytes"] == 0
    assert by_id["context_media"]["count"] == 1
    assert by_id["supplementary_media"]["bytes"] == 0
    assert by_id["supplementary_media"]["count"] == 1
    simplified = {segment["id"]: segment for segment in data["simplified_segments"]}
    assert simplified["media"]["bytes"] == 0
    assert simplified["media"]["count"] == 3
    assert simplified["media"]["percent"] == 0.0


def test_storage_breakdown_includes_wal_and_shm_sizes(make_web_client, tmp_path: Path) -> None:
    store = _storage_store(tmp_path)
    wal = store.db_path.with_name(store.db_path.name + "-wal")
    shm = store.db_path.with_name(store.db_path.name + "-shm")
    wal.write_bytes(b"w" * 3)
    shm.write_bytes(b"s" * 5)
    client = make_web_client(storage_stats.router, store=store)

    data = client.get("/api/storage/breakdown").json()

    expected_db_bytes = store.db_path.stat().st_size + wal.stat().st_size + shm.stat().st_size
    simplified = {segment["id"]: segment for segment in data["simplified_segments"]}
    assert simplified["database"]["bytes"] == expected_db_bytes


def test_storage_breakdown_requires_authentication(make_web_client, tmp_path: Path) -> None:
    client = make_web_client(
        storage_stats.router,
        store=_storage_store(tmp_path),
        password="secret",
    )

    response = client.get("/api/storage/breakdown")

    assert response.status_code == 401
