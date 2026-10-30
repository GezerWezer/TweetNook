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
# The last release that created each schema. 1.2.0 ships schema 12, the current one; the
# change that raises SCHEMA_VERSION adds its fixture from that release's tag.
RELEASED_SCHEMAS = {5: "v0.1.0", 8: "v0.1.6", 9: "v0.1.8", 10: "v1.1.0"}


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


def test_every_released_schema_has_a_fixture() -> None:
    names = {path.name for path in FIXTURES.glob("schema_v*.sql")}
    assert names == {f"schema_v{version}.sql" for version in RELEASED_SCHEMAS}
    for version, tag in RELEASED_SCHEMAS.items():
        text = (FIXTURES / f"schema_v{version}.sql").read_text("utf-8")
        assert f"tweetnook {tag}" in text
        assert text.rstrip().endswith(f"PRAGMA user_version = {version};")
