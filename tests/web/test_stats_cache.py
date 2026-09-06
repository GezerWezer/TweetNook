from __future__ import annotations

from threading import Event, Thread
from types import SimpleNamespace

from tweetnook.stats import StatsReport
from tweetnook.web.stats_cache import WebStatsCache


def _report(version: int) -> StatsReport:
    return StatsReport(
        archive_path="archive.db",
        owner=f"owner-{version}",
        generated_at=f"2026-08-10T00:00:0{version}+00:00",
        sections=[],
    )


def test_cache_deduplicates_concurrent_initial_collection() -> None:
    calls = 0
    build_started = Event()
    allow_build = Event()
    owners: list[str] = []

    def build(_store):
        nonlocal calls
        calls += 1
        build_started.set()
        assert allow_build.wait(timeout=2)
        return _report(1)

    store = SimpleNamespace()
    cache = WebStatsCache(builder=build)

    def load() -> None:
        owners.append(cache.get(store).report.owner)

    first = Thread(target=load)
    second = Thread(target=load)
    first.start()
    assert build_started.wait(timeout=2)
    second.start()
    allow_build.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert first.is_alive() is False
    assert second.is_alive() is False
    assert calls == 1
    assert owners == ["owner-1", "owner-1"]


def test_cache_serves_stale_report_while_one_background_refresh_runs() -> None:
    now = [0.0]
    calls = 0
    refresh_started = Event()
    allow_refresh = Event()

    def build(_store):
        nonlocal calls
        calls += 1
        if calls == 2:
            refresh_started.set()
            assert allow_refresh.wait(timeout=2)
        return _report(calls)

    store = SimpleNamespace()
    cache = WebStatsCache(max_age_seconds=300, builder=build, clock=lambda: now[0])

    first = cache.get(store)
    now[0] = 299
    fresh = cache.get(store)

    assert first.report.owner == fresh.report.owner == "owner-1"
    assert calls == 1
    assert fresh.stale is False

    now[0] = 301
    stale = cache.get(store)
    assert refresh_started.wait(timeout=2)
    duplicate = cache.get(store)

    assert stale.report.owner == duplicate.report.owner == "owner-1"
    assert stale.stale is True
    assert stale.refreshing is True
    assert duplicate.refreshing is True
    assert calls == 2

    allow_refresh.set()
    cache.wait_for_refreshes()
    refreshed = cache.get(store, revalidate=False)

    assert refreshed.report.owner == "owner-2"
    assert refreshed.age_seconds == 0
    assert refreshed.stale is False
    assert refreshed.refreshing is False
    assert refreshed.refresh_failed is False


def test_failed_refresh_retains_stale_report_and_can_be_retried() -> None:
    now = [0.0]
    calls = 0

    def build(_store):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("collector failed")
        return _report(calls)

    store = SimpleNamespace()
    cache = WebStatsCache(max_age_seconds=10, builder=build, clock=lambda: now[0])
    cache.get(store)
    now[0] = 11

    stale = cache.get(store)
    cache.wait_for_refreshes()
    failed = cache.get(store, revalidate=False)

    assert stale.report.owner == "owner-1"
    assert failed.report.owner == "owner-1"
    assert failed.refresh_failed is True
    assert failed.refreshing is False

    retrying = cache.refresh(store)
    cache.wait_for_refreshes()
    recovered = cache.get(store, revalidate=False)

    assert retrying.report.owner == "owner-1"
    assert retrying.refreshing is True
    assert recovered.report.owner == "owner-3"
    assert recovered.refresh_failed is False
