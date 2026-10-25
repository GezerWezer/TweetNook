from __future__ import annotations

from datetime import datetime, timedelta
from threading import Event, Thread
from types import SimpleNamespace

from tweetnook.stats import StatsReport
from tweetnook.web.stats_cache import DailyStatsRefresher, WebStatsCache, next_daily_refresh


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


def test_cache_never_recollects_an_old_report_on_read() -> None:
    now = [0.0]
    calls = 0

    def build(_store):
        nonlocal calls
        calls += 1
        return _report(calls)

    store = SimpleNamespace()
    cache = WebStatsCache(builder=build, clock=lambda: now[0])
    cache.get(store)
    now[0] = 10 * 24 * 60 * 60

    old = cache.get(store)

    assert old.report.owner == "owner-1"
    assert old.age_seconds == 10 * 24 * 60 * 60
    assert old.refreshing is False
    assert calls == 1


def test_background_refresh_serves_previous_report_until_it_finishes() -> None:
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
    cache = WebStatsCache(builder=build, clock=lambda: now[0])
    cache.get(store)
    now[0] = 30

    cache.start_refresh(store)
    assert refresh_started.wait(timeout=2)
    cache.start_refresh(store)
    during = cache.get(store)

    assert during.report.owner == "owner-1"
    assert during.refreshing is True
    assert calls == 2

    allow_refresh.set()
    cache.wait_for_refreshes()
    refreshed = cache.get(store)

    assert refreshed.report.owner == "owner-2"
    assert refreshed.age_seconds == 0
    assert refreshed.refreshing is False
    assert refreshed.refresh_failed is False


def test_requests_wait_for_the_first_background_collection() -> None:
    calls = 0
    build_started = Event()
    allow_build = Event()
    owners: list[str] = []

    def build(_store):
        nonlocal calls
        calls += 1
        build_started.set()
        assert allow_build.wait(timeout=2)
        return _report(calls)

    store = SimpleNamespace()
    cache = WebStatsCache(builder=build)
    cache.start_refresh(store)
    assert build_started.wait(timeout=2)
    request = Thread(target=lambda: owners.append(cache.get(store).report.owner))
    request.start()
    request.join(timeout=0.1)

    assert request.is_alive()
    allow_build.set()
    request.join(timeout=2)
    assert owners == ["owner-1"]
    assert calls == 1


def test_failed_refresh_retains_previous_report_and_can_be_retried() -> None:
    calls = 0

    def build(_store):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("collector failed")
        return _report(calls)

    store = SimpleNamespace()
    cache = WebStatsCache(builder=build)
    cache.get(store)

    cache.start_refresh(store)
    cache.wait_for_refreshes()
    failed = cache.get(store)

    assert failed.report.owner == "owner-1"
    assert failed.refresh_failed is True
    assert failed.refreshing is False

    retrying = cache.refresh(store)
    cache.wait_for_refreshes()
    recovered = cache.get(store)

    assert retrying.report.owner == "owner-1"
    assert retrying.refreshing is True
    assert recovered.report.owner == "owner-3"
    assert recovered.refresh_failed is False


def test_next_daily_refresh_is_the_next_local_occurrence() -> None:
    before = datetime(2026, 10, 11, 4, 59).astimezone()
    at = datetime(2026, 10, 11, 5, 0).astimezone()

    assert next_daily_refresh("05:00", before) == datetime(2026, 10, 11, 5, 0).astimezone()
    assert next_daily_refresh("05:00", at) == datetime(2026, 10, 12, 5, 0).astimezone()


def test_daily_refresher_collects_on_attach_and_at_each_daily_time() -> None:
    attach = datetime(2026, 10, 11, 4, 0).astimezone()
    due = datetime(2026, 10, 11, 5, 0).astimezone()
    readings = [attach, due, due]
    refreshes: list[object] = []
    second_refresh = Event()

    def start_refresh(store) -> None:
        refreshes.append(store)
        if len(refreshes) == 2:
            second_refresh.set()

    def now() -> datetime:
        # After the scheduled run, report the clock just past it so the thread sleeps.
        return readings.pop(0) if readings else due + timedelta(seconds=1)

    store = SimpleNamespace()
    cache = SimpleNamespace(start_refresh=start_refresh)
    refresher = DailyStatsRefresher(store, "05:00", cache=cache, now=now)

    refresher.start()
    assert second_refresh.wait(timeout=2)
    refresher.stop()

    assert refreshes == [store, store]


def test_daily_refresher_reloads_a_sleeping_schedule(monkeypatch) -> None:
    from tweetnook.web import stats_cache

    clock = [datetime(2026, 10, 25, 3, 0).astimezone()]
    initial_plan = Event()
    changed_plan = Event()
    refreshed = Event()
    requests = []
    original_next = stats_cache.next_daily_refresh

    def next_refresh(refresh_time, after):
        due = original_next(refresh_time, after)
        (changed_plan if refresh_time == "04:00" else initial_plan).set()
        return due

    def collect(store):
        requests.append(store)
        if len(requests) == 2:
            refreshed.set()

    monkeypatch.setattr(stats_cache, "next_daily_refresh", next_refresh)
    store = object()
    refresher = DailyStatsRefresher(
        store, "05:00", now=lambda: clock[0], cache=SimpleNamespace(start_refresh=collect)
    )
    refresher.start()
    try:
        assert initial_plan.wait(2)
        refresher.reload("04:00")
        assert changed_plan.wait(2)
        clock[0] = clock[0].replace(hour=4)
        refresher._wake.set()
        assert refreshed.wait(2)
        assert requests == [store, store]
    finally:
        refresher.stop()


def test_daily_refresher_keeps_an_unchanged_schedule() -> None:
    refresher = DailyStatsRefresher(object(), "05:00")
    refresher.reload("05:00")
    assert not refresher._wake.is_set()
    assert refresher._revision == 0
