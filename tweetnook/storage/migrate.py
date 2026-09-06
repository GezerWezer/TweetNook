from __future__ import annotations

import importlib
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console

from tweetnook.config import load_config
from tweetnook.pipeline import current_pipeline
from tweetnook.storage import open_archive_store

WORKER_END_OF_TABLE = 42
WORKER_SOURCE_READ_FAILED = 43
WORKER_DESTINATION_WRITE_FAILED = 44
DEFAULT_BATCH_SIZE = 5000

WORKER_CODE = """
import sys
import lancedb
import sqlite3

def worker():
    lance_path = sys.argv[1]
    db_path = sys.argv[2]
    batch_size = int(sys.argv[3])
    offset = int(sys.argv[4])
    
    try:
        ldb = lancedb.connect(lance_path)
        table = ldb.open_table("archive")
        rows = table.search().limit(batch_size).offset(offset).to_list()
    except Exception as error:
        print(f"LanceDB read failed: {error}", file=sys.stderr)
        sys.exit(43)

    if not rows:
        sys.exit(42)

    try:
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")

        cols = [
            "row_key", "record_type", "tweet_id", "collection_type", "folder_id", "sort_index",
            "operation", "cursor_in", "cursor_out", "captured_at", "http_status", "source",
            "text", "author_id", "author_username", "author_display_name", "created_at",
            "deleted_at", "conversation_id", "lang", "note_tweet_text", "enrichment_state",
            "enrichment_checked_at", "enrichment_http_status", "enrichment_reason", "raw_json",
            "first_seen_at", "last_seen_at", "added_at", "synced_at", "relation_type",
            "target_tweet_id", "position", "media_key", "media_type", "media_url", "thumbnail_url",
            "width", "height", "duration_millis", "variants_json", "download_state", "local_path",
            "provenance_source", "sha256", "byte_size", "content_type", "thumbnail_local_path",
            "thumbnail_sha256", "thumbnail_byte_size", "thumbnail_content_type", "downloaded_at",
            "download_error", "url_hash", "url", "expanded_url", "final_url", "canonical_url",
            "display_url", "url_host", "description", "site_name", "unfurl_state",
            "last_fetched_at",
            "article_id", "title", "summary_text", "content_text", "published_at", "status",
            "archive_digest", "archive_generation_date", "import_started_at", "import_completed_at",
            "warnings_json", "counts_json", "last_head_tweet_id", "backfill_cursor",
            "backfill_incomplete",
            "updated_at", "key", "value"
        ]
        placeholders = ", ".join(["?"] * len(cols))
        col_names = ", ".join(cols)
        # A rerun must never overwrite newer data already present in SQLite.
        sql = f"INSERT OR IGNORE INTO archive ({col_names}) VALUES ({placeholders})"

        params = []
        for record in rows:
            row = []
            for col in cols:
                row.append(record.get(col))
            params.append(row)

        with conn:
            conn.executemany(sql, params)
        conn.close()
    except Exception as error:
        print(f"SQLite write failed: {error}", file=sys.stderr)
        sys.exit(44)

    sys.exit(0)

if __name__ == "__main__":
    worker()
"""


@dataclass(slots=True)
class MigrationResult:
    status: str
    total_rows: int = 0
    migrated_rows: int = 0
    skipped_rows: int = 0
    worker_calls: int = 0
    final_offset: int = 0
    fts_rebuilt: bool = False


def _import_lancedb() -> Any | None:
    """Load the legacy dependency only when the migration command is used."""
    try:
        return importlib.import_module("lancedb")
    except ImportError:
        return None


def _create_progress(total_rows: int) -> Any | None:
    if current_pipeline() is not None:
        return None
    try:
        from tqdm import tqdm
    except ImportError:
        return None
    return tqdm(total=total_rows, desc="Migrating to SQLite", unit="rows")


def _run_worker(
    lance_path: Path,
    database_path: Path,
    batch_size: int,
    offset: int,
) -> subprocess.CompletedProcess[bytes]:
    command = [
        sys.executable,
        "-c",
        WORKER_CODE,
        str(lance_path),
        str(database_path),
        str(batch_size),
        str(offset),
    ]
    return subprocess.run(command, capture_output=True, check=False)


def _progress_warning(progress: Any | None, message: str) -> None:
    pipeline = current_pipeline()
    if pipeline is not None:
        pipeline.issue(message.removeprefix("Warning: "), dedupe_key="migration:worker-warning")
        return
    if progress is not None:
        progress.write(message)
    else:
        print(message)


def _advance_progress(progress: Any | None, total_rows: int, batch_size: int) -> None:
    pipeline = current_pipeline()
    if pipeline is not None and pipeline.has_step("migration-copy"):
        step = pipeline.active_step
        completed = min(
            (step.completed if step is not None and step.key == "migration-copy" else 0)
            + batch_size,
            total_rows,
        )
        pipeline.update_step(
            "migration-copy",
            completed=completed,
            counters=f"{completed:,}/{total_rows:,} legacy rows examined",
        )
    if progress is None:
        return
    remaining = max(0, total_rows - int(progress.n))
    if remaining:
        progress.update(min(batch_size, remaining))


def _worker_error(worker_result: subprocess.CompletedProcess[bytes]) -> str:
    stderr = worker_result.stderr.decode(errors="replace").strip()
    return f": {stderr}" if stderr else ""


def _recoverable_source_failure(return_code: int) -> bool:
    return return_code == WORKER_SOURCE_READ_FAILED or return_code < 0


def _copy_batches(
    *,
    lance_path: Path,
    database_path: Path,
    total_rows: int,
    batch_size: int,
    progress: Any | None,
) -> MigrationResult:
    result = MigrationResult(status="complete", total_rows=total_rows)
    offset = 0

    def copy_range(range_offset: int, range_size: int) -> bool:
        try:
            worker_result = _run_worker(
                lance_path,
                database_path,
                range_size,
                range_offset,
            )
        except Exception as error:
            result.worker_calls += 1
            result.status = "aborted"
            _progress_warning(
                progress,
                f"Warning: Migration worker could not start at offset {range_offset}: {error}",
            )
            return False

        result.worker_calls += 1
        return_code = worker_result.returncode
        if return_code == 0:
            result.migrated_rows += range_size
            result.final_offset = max(result.final_offset, range_offset + range_size)
            _advance_progress(progress, total_rows, range_size)
            return True

        if return_code == WORKER_END_OF_TABLE:
            result.skipped_rows += range_size
            result.status = "partial"
            result.final_offset = max(result.final_offset, range_offset + range_size)
            _advance_progress(progress, total_rows, range_size)
            _progress_warning(
                progress,
                f"Warning: LanceDB ended unexpectedly at offset {range_offset}; "
                f"{range_size} rows could not be read.",
            )
            return True

        if not _recoverable_source_failure(return_code):
            result.status = "aborted"
            _progress_warning(
                progress,
                f"Warning: Migration worker failed at offset {range_offset} "
                f"with exit code {return_code}{_worker_error(worker_result)}",
            )
            return False

        if range_size == 1:
            result.skipped_rows += 1
            result.status = "partial"
            result.final_offset = max(result.final_offset, range_offset + 1)
            _advance_progress(progress, total_rows, 1)
            _progress_warning(
                progress,
                f"Warning: Corrupted row at offset {range_offset} could not be recovered"
                f"{_worker_error(worker_result)}",
            )
            return True

        left_size = range_size // 2
        right_size = range_size - left_size
        _progress_warning(
            progress,
            f"Warning: Corrupted range at offset {range_offset} ({range_size} rows); "
            "retrying smaller ranges.",
        )
        return copy_range(range_offset, left_size) and copy_range(
            range_offset + left_size,
            right_size,
        )

    while True:
        rows_in_chunk = min(batch_size, max(0, total_rows - offset))
        if rows_in_chunk == 0:
            result.worker_calls += 1
            try:
                worker_result = _run_worker(lance_path, database_path, batch_size, offset)
            except Exception as error:
                result.status = "aborted"
                _progress_warning(
                    progress,
                    f"Warning: Migration worker could not confirm the end of the table: {error}",
                )
                break
            if worker_result.returncode != WORKER_END_OF_TABLE:
                result.status = "aborted"
                _progress_warning(
                    progress,
                    "Warning: Migration worker did not confirm the end of the LanceDB table.",
                )
            break

        if not copy_range(offset, rows_in_chunk):
            break

        offset += batch_size

    return result


def _rebuild_fts(config: Any, paths: Any, *, console: Console | None = None) -> bool:
    pipeline = current_pipeline()
    if pipeline is not None:
        pipeline.status("migration-index", "Rebuilding the SQLite full-text search index")
    else:
        print("Rebuilding Full-Text Search index (this may take a few moments)...")
    try:
        store = open_archive_store(paths, create=True, config=config)
    except Exception as error:
        message = f"Failed to open SQLite store for FTS rebuild: {error}"
        if pipeline is not None:
            pipeline.issue(message, level="error", dedupe_key="migration:index-open")
        elif console is not None:
            console.print(f"Warning: {message}")
        else:
            print(f"Warning: {message}")
        return False
    if store is None:
        message = "Failed to open SQLite store for FTS rebuild."
        if pipeline is not None:
            pipeline.issue(message, level="error", dedupe_key="migration:index-open")
        else:
            print(f"Warning: {message}")
        return False
    try:
        store.rebuild_search_index()
        return True
    except Exception as error:
        message = f"Failed to rebuild FTS index: {error}"
        if pipeline is not None:
            pipeline.issue(message, level="error", dedupe_key="migration:index-rebuild")
        else:
            print(f"Warning: {message}")
        return False
    finally:
        store.close()


def run_migration(
    *, batch_size: int = DEFAULT_BATCH_SIZE, console: Console | None = None
) -> MigrationResult:
    config, paths = load_config()
    pipeline = current_pipeline()
    if pipeline is not None:
        pipeline.add_step(
            "migration-inspect",
            "Inspect source",
            total=1,
            unit="archive",
            detail="legacy LanceDB archive table and destination validation",
            show_rate=False,
            show_eta=False,
        )
        pipeline.add_step(
            "migration-copy",
            "Copy rows",
            total=1,
            unit="rows",
            detail=f"isolated worker chunks of {batch_size:,} rows · existing SQLite rows kept",
            rate_unit="rows/s",
        )
        pipeline.add_step(
            "migration-index",
            "Search index",
            total=1,
            unit="index",
            detail="rebuild SQLite FTS after imported rows are durable",
            show_rate=False,
            show_eta=False,
        )
        pipeline.start_step(
            "migration-inspect",
            activity="Locating the legacy LanceDB archive",
        )
    lance_path = paths.data_dir / "archive.lancedb"
    if not lance_path.exists():
        if pipeline is not None:
            pipeline.skip_step("migration-inspect", "no legacy LanceDB archive being present")
            pipeline.skip_step("migration-copy", "no legacy source archive being present")
            pipeline.skip_step("migration-index", "no rows being migrated")
            pipeline.final_note(f"No legacy LanceDB archive found at {lance_path}.")
        else:
            print(f"No old LanceDB archive found at {lance_path}.")
        return MigrationResult(status="source_missing")

    lancedb = _import_lancedb()
    if lancedb is None:
        if pipeline is not None:
            message = "lancedb and pyarrow are required to read the legacy archive"
            pipeline.fail_step("migration-inspect", message)
            pipeline.issue(message, level="error", dedupe_key="migration:dependencies")
            pipeline.skip_step("migration-copy", "legacy migration dependencies being unavailable")
            pipeline.skip_step("migration-index", "no rows being migrated")
        else:
            print("Error: lancedb and pyarrow are required to run the migration.")
            print("Please reinstall tweetnook with its migration dependencies.")
        return MigrationResult(status="dependency_missing")

    if pipeline is not None:
        pipeline.status("migration-inspect", f"Reading the archive table at {lance_path}")
    else:
        print(f"Reading LanceDB at {lance_path}...")
    try:
        legacy_database = lancedb.connect(lance_path)
        table = legacy_database.open_table("archive")
        total_rows = int(table.count_rows())
    except Exception as error:
        if pipeline is not None:
            message = f"Legacy archive table is unreadable: {error}"
            pipeline.fail_step("migration-inspect", message)
            pipeline.issue(message, level="error", dedupe_key="migration:source-table")
            pipeline.skip_step("migration-copy", "the legacy archive table being unreadable")
            pipeline.skip_step("migration-index", "no rows being migrated")
        else:
            print(f"LanceDB archive table not found or unreadable: {error}")
        return MigrationResult(status="table_missing")

    if pipeline is not None:
        pipeline.complete_step("migration-inspect", f"{total_rows:,} legacy rows ready to migrate")
    else:
        print(f"Found {total_rows} rows to migrate.")
    database_path = paths.database_path
    if pipeline is not None:
        pipeline.add_step(
            "migration-copy",
            "Copy rows",
            total=max(total_rows, 1),
            unit="rows",
            detail=f"{lance_path.name} → {database_path.name} · {batch_size:,}-row worker chunks",
            rate_unit="rows/s",
        )
        pipeline.start_step(
            "migration-copy",
            activity="Initializing the native SQLite destination",
            counters=f"0/{total_rows:,} legacy rows examined",
        )
    else:
        print(f"Inserting into native SQLite database at {database_path}...")

    try:
        store = open_archive_store(paths, create=True, config=config)
    except Exception as error:
        if pipeline is not None:
            pipeline.fail_step("migration-copy", f"Failed to initialize SQLite: {error}")
            pipeline.skip_step("migration-index", "the SQLite destination failing to initialize")
        else:
            print(f"Failed to initialize SQLite store: {error}")
        return MigrationResult(status="destination_failed", total_rows=total_rows)
    if store is None:
        if pipeline is not None:
            pipeline.fail_step("migration-copy", "Failed to open the SQLite destination")
            pipeline.skip_step("migration-index", "the SQLite destination failing to initialize")
        else:
            print("Failed to open SQLite store.")
        return MigrationResult(status="destination_failed", total_rows=total_rows)
    store.close()

    progress = _create_progress(total_rows)
    try:
        result = _copy_batches(
            lance_path=lance_path,
            database_path=database_path,
            total_rows=total_rows,
            batch_size=batch_size,
            progress=progress,
        )
    finally:
        if progress is not None:
            progress.close()

    if result.status == "aborted":
        if pipeline is not None:
            pipeline.fail_step(
                "migration-copy",
                f"stopped after {result.final_offset:,} rows due to repeated worker failures",
            )
            pipeline.skip_step("migration-index", "row migration aborting before completion")
            pipeline.final_note("Migration stopped before all readable chunks were processed.")
        else:
            print("Migration stopped before all readable chunks were processed.")
        return result

    if pipeline is not None:
        pipeline.complete_step(
            "migration-copy",
            f"{result.migrated_rows:,} migrated · {result.skipped_rows:,} skipped",
        )
        pipeline.start_step(
            "migration-index",
            activity="Rebuilding the SQLite full-text search index",
        )
    result.fts_rebuilt = _rebuild_fts(config, paths, console=console)
    skipped_unit = "row" if result.skipped_rows == 1 else "rows"
    if pipeline is not None:
        if result.fts_rebuilt:
            pipeline.complete_step("migration-index", "SQLite full-text search index rebuilt")
        else:
            pipeline.fail_step("migration-index", "SQLite full-text search index rebuild failed")
        if result.status == "partial":
            pipeline.final_note(
                f"Migration partially complete: {result.migrated_rows:,} rows copied and "
                f"{result.skipped_rows:,} unreadable {skipped_unit} skipped. Keep {lance_path}."
            )
        else:
            pipeline.final_note(
                f"Migration complete: {result.migrated_rows:,} rows copied into {database_path}."
            )
        return result

    if result.status == "partial":
        print(
            f"Migration partially complete: {result.migrated_rows} rows copied and "
            f"{result.skipped_rows} unreadable {skipped_unit} skipped."
        )
        print(f"Keep the original `{lance_path}` directory for future recovery attempts.")
        return result

    print("Migration complete! You can now run `tweetnook stats`.")
    print(
        "If it works correctly, you may safely backup and delete the original "
        f"`{lance_path}` directory."
    )
    return result


if __name__ == "__main__":
    migration_console = Console(stderr=True)
    from tweetnook.config import resolve_paths
    from tweetnook.pipeline import PipelineReporter

    with PipelineReporter(
        migration_console,
        "tweetnook migrate",
        state_path=resolve_paths().activity_status_file,
    ):
        run_migration(console=migration_console)
