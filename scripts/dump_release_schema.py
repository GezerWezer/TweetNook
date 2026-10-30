"""Print the schema a released version creates, as SQL for an upgrade-test fixture.

    uv run python scripts/dump_release_schema.py v1.1.0 > tests/fixtures/schemas/schema_v10.sql

The tag's own package is extracted to a temporary directory and creates an empty archive,
so the fixture records what that release shipped rather than what today's code remembers
it lacked. Objects SQLite creates for itself (FTS shadow tables, ANALYZE statistics) are
left out, because replaying the virtual-table statements recreates them.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_DUMP = """
import sqlite3, sys, tempfile
from pathlib import Path
from tweetnook.storage.backend import ArchiveStore

with tempfile.TemporaryDirectory() as directory:
    db_path = Path(directory) / "archive.sqlite3"
    ArchiveStore(db_path, create=True).close()
    connection = sqlite3.connect(db_path)
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    rows = connection.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY rowid"
    ).fetchall()
    virtual = {name for kind, name, sql in rows if sql.upper().startswith("CREATE VIRTUAL")}
    for kind, name, sql in rows:
        shadow = kind == "table" and any(name.startswith(f"{table}_") for table in virtual)
        if shadow or name.startswith("sqlite_"):
            continue
        print(f"{sql};")
    print(f"PRAGMA user_version = {version};")
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tag", help="release tag whose schema to print, such as v1.1.0")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory() as directory:
        archive = subprocess.run(
            ["git", "archive", args.tag, "tweetnook"], cwd=ROOT, capture_output=True, check=True
        )
        subprocess.run(["tar", "-x", "-C", directory], input=archive.stdout, check=True)
        dump = subprocess.run(
            [sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {directory!r})\n{_DUMP}"],
            capture_output=True,
            text=True,
            check=True,
        )
    print(f"-- Schema created by tweetnook {args.tag}; regenerate with")
    print(f"-- scripts/dump_release_schema.py {args.tag}. Do not edit by hand.")
    sys.stdout.write(dump.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
