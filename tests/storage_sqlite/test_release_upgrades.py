"""Archives created by each released schema upgrade to exactly today's schema.

The fixtures in tests/fixtures/schemas/ are the DDL each release created (see
scripts/dump_release_schema.py), so the starting point doesn't depend on what the current
code remembers older versions lacked.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest

from tweetnook.storage.backend import ArchiveStore
from tweetnook.storage.schema import SCHEMA_VERSION

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "schemas"
# The last release that created each schema. The change that raises SCHEMA_VERSION adds the
# fixture for the schema the latest release ships, from that release's tag.
RELEASED_SCHEMAS = {5: "v0.1.0", 8: "v0.1.6", 9: "v0.1.8", 10: "v1.1.0", 12: "v1.2.0"}


def schema_of(db_path: Path) -> dict[tuple[str, str], str]:
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_stat%'"
        ).fetchall()
    finally:
        connection.close()
    return {(kind, name): re.sub(r"\s+", " ", sql).strip() for kind, name, sql in rows}


def released_archive(db_path: Path, version: int) -> None:
    """Create the released schema and save rows the way that release's columns hold them."""
    tweet = {
        "rest_id": "100",
        "legacy": {
            "full_text": "lighthouse upgrade sentinel",
            "favorite_count": 7,
            "retweet_count": 2,
            "entities": {"hashtags": [{"text": "Coast"}]},
        },
    }
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript((FIXTURES / f"schema_v{version}.sql").read_text("utf-8"))
        connection.executemany(
            "INSERT INTO archive (row_key, record_type, tweet_id, collection_type, folder_id,"
            " text, author_username, created_at, created_at_ts, sort_index, raw_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "tweet:100",
                    "tweet",
                    "100",
                    None,
                    None,
                    "lighthouse upgrade sentinel",
                    "keeper",
                    "Wed Jan 04 12:00:00 +0000 2012",
                    1325678400,
                    None,
                    json.dumps(tweet),
                ),
                (
                    "tweet:bookmark::100",
                    "tweet",
                    "100",
                    "bookmark",
                    "",
                    "lighthouse upgrade sentinel",
                    "keeper",
                    "Wed Jan 04 12:00:00 +0000 2012",
                    1325678400,
                    "5",
                    json.dumps(tweet),
                ),
                (
                    "media_tag:100:3_1",
                    "media_tag",
                    "100",
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    json.dumps({"description": "foghorn beside the rocks"}),
                ),
            ],
        )
        connection.commit()
    finally:
        connection.close()


@pytest.mark.parametrize("version", sorted(RELEASED_SCHEMAS))
def test_released_schema_upgrades_to_the_fresh_schema(tmp_path: Path, version: int) -> None:
    fresh_path = tmp_path / "fresh.db"
    ArchiveStore(fresh_path, create=True).close()
    db_path = tmp_path / "released.db"
    released_archive(db_path, version)

    store = ArchiveStore(db_path, create=False)
    try:
        assert store.migration_report is not None
        assert store.migration_report.from_version == version
        assert [row["tweet_id"] for row in store.search_fts("sentinel")] == ["100"]
        facts = store.conn.execute(
            "SELECT favorite_count, retweet_count, hashtags FROM archive_tweet_facts"
            " WHERE row_key = 'tweet:100'"
        ).fetchone()
        assert tuple(facts) == (7, 2, "\x1fcoast\x1f")
        descriptions = store.conn.execute(
            "SELECT rowid FROM archive_description_fts"
            " WHERE archive_description_fts MATCH 'foghorn'"
        ).fetchall()
        assert len(descriptions) == 1
    finally:
        store.close()

    connection = sqlite3.connect(db_path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    connection.close()
    assert schema_of(db_path) == schema_of(fresh_path)


def _save_stopped_followup_manifest(db_path: Path) -> None:
    connection = sqlite3.connect(db_path)
    try:
        with connection:
            connection.execute(
                "INSERT INTO archive (row_key, record_type, archive_digest, status, counts_json,"
                " enrichment_followup_status, enrichment_aborted_reason)"
                " VALUES ('import_manifest:abc', 'import_manifest', 'abc', 'completed',"
                " '{\"likes\": 4}', 'enrichment_aborted', 'stale query id')"
            )
    finally:
        connection.close()


def _archive_columns(db_path: Path) -> set[str]:
    connection = sqlite3.connect(db_path)
    try:
        return {row[1] for row in connection.execute("PRAGMA table_info(archive)")}
    finally:
        connection.close()


@pytest.mark.parametrize("version", sorted(RELEASED_SCHEMAS))
def test_upgrade_drops_the_followup_columns_and_keeps_the_manifest(
    tmp_path: Path, version: int
) -> None:
    db_path = tmp_path / "released.db"
    released_archive(db_path, version)
    _save_stopped_followup_manifest(db_path)

    store = ArchiveStore(db_path, create=False)
    try:
        manifest = store.get_import_manifest("abc")
    finally:
        store.close()

    assert manifest is not None
    assert manifest["status"] == "completed"
    assert manifest["counts_json"] == '{"likes": 4}'
    assert "enrichment_followup_status" not in _archive_columns(db_path)
    assert "enrichment_aborted_reason" not in _archive_columns(db_path)


@pytest.mark.parametrize("version", [5, 12])
def test_failed_upgrade_keeps_the_columns_and_the_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: int
) -> None:
    db_path = tmp_path / "released.db"
    released_archive(db_path, version)
    _save_stopped_followup_manifest(db_path)
    dropped = []
    real_drop = ArchiveStore._drop_unread_followup_columns

    def drop_then_fail_next(self) -> None:
        real_drop(self)
        dropped.append(True)
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(ArchiveStore, "_drop_unread_followup_columns", drop_then_fail_next)

    with pytest.raises(sqlite3.OperationalError):
        ArchiveStore(db_path, create=False)

    assert dropped == [True]
    assert "enrichment_followup_status" in _archive_columns(db_path)
    connection = sqlite3.connect(db_path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == version
        assert connection.execute(
            "SELECT enrichment_followup_status FROM archive WHERE row_key = 'import_manifest:abc'"
        ).fetchone() == ("enrichment_aborted",)
    finally:
        connection.close()


def _insert_media(connection: sqlite3.Connection, row_key: str, **columns: object) -> None:
    fields = {"row_key": row_key, "record_type": "media", "tweet_id": "100", **columns}
    placeholders = ", ".join("?" for _field in fields)
    connection.execute(
        f"INSERT INTO archive ({', '.join(fields)}) VALUES ({placeholders})",
        tuple(fields.values()),
    )


@pytest.mark.parametrize("version", sorted(RELEASED_SCHEMAS))
def test_upgrade_merges_duplicate_media_and_keeps_saved_files(tmp_path: Path, version: int) -> None:
    db_path = tmp_path / "released.db"
    released_archive(db_path, version)
    connection = sqlite3.connect(db_path)
    try:
        # A live video the archive import also saved, with only the import's poster on disk.
        _insert_media(
            connection,
            "media:100:13_555",
            media_key="13_555",
            media_type="video",
            provenance_source="fxtwitter",
            media_url="https://video.twimg.com/fx.mp4",
            thumbnail_url="https://pbs.twimg.com/fx.jpg",
            duration_millis="1000",
            download_state="pending",
            thumbnail_local_path="media/100/13_555-poster.jpg",
            thumbnail_sha256="poster-sha",
            first_seen_at="2020-01-01T00:00:00+00:00",
        )
        _insert_media(
            connection,
            "media:100:7_555",
            media_key="7_555",
            media_type="video",
            provenance_source="live_graphql",
            media_url="https://video.twimg.com/live.mp4",
            download_state="failed",
            download_error="HTTP 503",
            local_path="media/100/7_555.mp4",
            sha256="main-sha",
            first_seen_at="2021-01-01T00:00:00+00:00",
        )
        # The same numeric id on another post, a different media id and an Article image.
        _insert_media(
            connection,
            "media:999:7_555",
            tweet_id="999",
            media_key="7_555",
            provenance_source="x_archive",
        )
        _insert_media(
            connection, "media:100:3_556", media_key="3_556", provenance_source="x_archive"
        )
        _insert_media(
            connection,
            "media:100:article-cover:100",
            media_key="article-cover:100",
            provenance_source="x_archive",
        )
        connection.commit()
    finally:
        connection.close()

    store = ArchiveStore(db_path, create=False)
    try:
        rows = {
            row["row_key"]: dict(row)
            for row in store.conn.execute("SELECT * FROM archive WHERE record_type = 'media'")
        }
        assert set(rows) == {
            "media:100:7_555",
            "media:999:7_555",
            "media:100:3_556",
            "media:100:article-cover:100",
        }
        merged = rows["media:100:7_555"]
        assert merged["provenance_source"] == "live_graphql"
        assert merged["media_url"] == "https://video.twimg.com/live.mp4"
        assert merged["local_path"] == "media/100/7_555.mp4"
        assert merged["sha256"] == "main-sha"
        assert merged["thumbnail_local_path"] == "media/100/13_555-poster.jpg"
        assert merged["thumbnail_sha256"] == "poster-sha"
        assert merged["thumbnail_url"] == "https://pbs.twimg.com/fx.jpg"
        assert merged["duration_millis"] == "1000"
        assert merged["first_seen_at"] == "2020-01-01T00:00:00+00:00"
        # Both of its files are saved now, so a failed earlier attempt no longer describes it.
        assert merged["download_state"] == "done"
        assert merged["download_error"] is None
    finally:
        store.close()
    assert schema_of(db_path) == _fresh_schema(tmp_path)


def _fresh_schema(tmp_path: Path) -> dict[tuple[str, str], str]:
    fresh_path = tmp_path / "fresh.db"
    ArchiveStore(fresh_path, create=True).close()
    return schema_of(fresh_path)


def test_every_released_schema_has_a_fixture() -> None:
    names = {path.name for path in FIXTURES.glob("schema_v*.sql")}
    assert names == {f"schema_v{version}.sql" for version in RELEASED_SCHEMAS}
    for version, tag in RELEASED_SCHEMAS.items():
        text = (FIXTURES / f"schema_v{version}.sql").read_text("utf-8")
        assert f"tweetnook {tag}" in text
        assert text.rstrip().endswith(f"PRAGMA user_version = {version};")
