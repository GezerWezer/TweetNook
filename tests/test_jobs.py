from __future__ import annotations

import pytest

import tweetnook.jobs as jobs
from tweetnook.exceptions import ConfigError


class _FakeStore:
    def __init__(self) -> None:
        self.optimize_calls = 0
        self.closed = False

    def version_count(self) -> int:
        return 0

    def optimize(self) -> None:
        self.optimize_calls += 1

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize("mark_dirty", [False, True])
@pytest.mark.asyncio
async def test_locked_archive_job_never_auto_optimizes(
    paths,
    config,
    monkeypatch: pytest.MonkeyPatch,
    mark_dirty: bool,
) -> None:
    store = _FakeStore()
    monkeypatch.setattr(jobs, "open_archive_store", lambda _paths, create=False, config=None: store)

    async with jobs.locked_archive_job(config=config, paths=paths) as job:
        assert job.config == config
        assert job.paths == paths
        assert job.store is store
        if mark_dirty:
            job.mark_dirty()

    assert store.optimize_calls == 0
    assert store.closed is True


@pytest.mark.asyncio
async def test_locked_archive_job_interrupt_closes_without_optimizing(
    paths,
    config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _FakeStore()
    monkeypatch.setattr(jobs, "open_archive_store", lambda _paths, create=False, config=None: store)

    with pytest.raises(KeyboardInterrupt):
        async with jobs.locked_archive_job(config=config, paths=paths) as job:
            job.mark_dirty()
            raise KeyboardInterrupt()

    assert store.optimize_calls == 0
    assert store.closed is True


@pytest.mark.asyncio
async def test_locked_archive_job_loads_context_and_errors_when_archive_missing(
    paths,
    config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jobs, "load_config", lambda: (config, paths))
    monkeypatch.setattr(jobs, "open_archive_store", lambda _paths, create=False, config=None: None)

    with pytest.raises(ConfigError, match="No local archive found."):
        async with jobs.locked_archive_job():
            raise AssertionError("context should not yield without an archive")
