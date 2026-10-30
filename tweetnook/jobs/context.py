"""Shared helpers for archive batch jobs."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from rich.console import Console

from tweetnook.config import AppConfig, XDGPaths, ensure_paths, load_config
from tweetnook.core.exceptions import ConfigError
from tweetnook.jobs.resource_failure import raise_if_storage_exhausted
from tweetnook.storage import ArchiveStore, open_archive_store


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


@asynccontextmanager
async def locked_archive_job(
    *,
    config: AppConfig | None = None,
    paths: XDGPaths | None = None,
    console: Console | None = None,
) -> AsyncIterator[LockedArchiveJob]:
    from tweetnook.core.locking import ProcessLock

    config, paths = resolve_job_context(config=config, paths=paths)
    lock = ProcessLock(paths.lock_file)
    lock.acquire()
    try:
        store = open_archive_store(paths, create=False, config=config)
        if store is None:
            raise ConfigError("No local archive found.")
        if console is not None:
            from tweetnook.jobs.reminders import print_archive_migration_report

            print_archive_migration_report(console, store)
        job = LockedArchiveJob(
            config=config,
            paths=paths,
            store=store,
        )
        try:
            yield job
        except BaseException as exc:
            raise_if_storage_exhausted(exc)
            raise
        finally:
            store.close()
    finally:
        lock.release()
