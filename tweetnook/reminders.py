"""User-facing reminders for incomplete archive maintenance."""

from __future__ import annotations

from rich.console import Console

from tweetnook.storage import ArchiveStore


def print_archive_migration_report(console: Console, store: ArchiveStore) -> bool:
    report = getattr(store, "migration_report", None)
    if report is None:
        return False
    console.print(
        f"Archive schema migrated from v{report.from_version} to v{report.to_version}.",
        highlight=False,
    )
    console.print(
        "Rebuilt the derived full-text index with searchable tweets only; "
        "canonical archive rows were unchanged.",
        highlight=False,
    )
    return True


def print_pending_archive_enrichment_reminder(
    console: Console,
    store: ArchiveStore,
) -> bool:
    incomplete = store.count_incomplete_initial_enrichment()
    if incomplete <= 0:
        return False
    remainder = "tweet remains" if incomplete == 1 else "tweets remain"
    console.print(f"Archive enrichment is incomplete: {incomplete:,} {remainder}.", highlight=False)
    console.print("Run `tweetnook import enrich` to continue.", highlight=False)
    return True
