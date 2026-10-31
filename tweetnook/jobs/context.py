"""Shared helpers for archive batch jobs."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass

from rich.console import Console

from tweetnook.config import AppConfig, XDGPaths, ensure_paths, load_config
from tweetnook.core.exceptions import ArchiveNotFoundError
from tweetnook.core.locking import ProcessLock
from tweetnook.jobs.reminders import print_archive_migration_report
from tweetnook.jobs.resource_failure import STORAGE_RECOVERY, raise_if_storage_exhausted
from tweetnook.storage import ArchiveStore, open_archive_store

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LockedArchiveJob:
    config: AppConfig
    paths: XDGPaths
    store: ArchiveStore


def resolve_job_context(
    *,
    config: AppConfig | None = None,
    paths: XDGPaths | None = None,
) -> tuple[AppConfig, XDGPaths]:
    if config is None or paths is None:
        loaded_config, loaded_paths = load_config()
        config = config or loaded_config
        paths = paths or loaded_paths
    assert config is not None
    assert paths is not None
    return config, ensure_paths(paths)


def close_after_failure(store: ArchiveStore) -> None:
    """Close a store while another error propagates, so a close failure can't replace it."""
    try:
        store.close()
    except Exception:
        logger.exception("Could not close the archive after a failed job")


def open_job_store(
    paths: XDGPaths,
    *,
    create: bool,
    config: AppConfig | None,
    console: Console,
) -> ArchiveStore | None:
    """Open the archive and announce any schema upgrade this open performed.

    Only the instance that upgraded knows about it, so whoever opens first must report.
    """
    store = open_archive_store(paths, create=create, config=config)
    if store is None:
        return None
    try:
        print_archive_migration_report(console, store)
    except BaseException:
        close_after_failure(store)
        raise
    return store


@contextmanager
def archive_job(
    *,
    config: AppConfig | None = None,
    paths: XDGPaths | None = None,
    console: Console | None = None,
    create: bool = False,
    hold_lock: bool = True,
    storage_recovery: str = STORAGE_RECOVERY,
) -> Iterator[LockedArchiveJob]:
    """Own one job's lock, open, body and close, and classify a full disk in any of them.

    hold_lock is for read-only work that runs beside other jobs, such as a prune preview.
    """
    try:
        config, paths = resolve_job_context(config=config, paths=paths)
        lock = ProcessLock(paths.lock_file)
        try:
            if hold_lock:
                lock.acquire()
            store = open_job_store(
                paths,
                create=create,
                config=config,
                console=console or Console(stderr=True),
            )
            if store is None:
                raise ArchiveNotFoundError("No local archive found.")
            try:
                yield LockedArchiveJob(config=config, paths=paths, store=store)
            except BaseException:
                close_after_failure(store)
                raise
            store.close()
        finally:
            lock.release()
    except BaseException as exc:
        raise_if_storage_exhausted(exc, recovery=storage_recovery)
        raise


@asynccontextmanager
async def locked_archive_job(
    *,
    config: AppConfig | None = None,
    paths: XDGPaths | None = None,
    console: Console | None = None,
    create: bool = False,
) -> AsyncIterator[LockedArchiveJob]:
    with archive_job(config=config, paths=paths, console=console, create=create) as job:
        yield job
