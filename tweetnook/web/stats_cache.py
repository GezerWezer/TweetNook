"""Process-local Web statistics cache, rebuilt in the background once a day."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from threading import Condition, Event, Lock, Thread

from tweetnook.config import ScheduleConfig
from tweetnook.scheduler import next_run_after
from tweetnook.stats import StatsReport, build_stats_report
from tweetnook.storage import ArchiveStore


@dataclass(frozen=True, slots=True)
class CachedStatsReport:
    """One report plus cache state needed by the browser."""

    report: StatsReport
    age_seconds: float
    refreshing: bool
    refresh_failed: bool


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    report: StatsReport
    stored_at: float


class WebStatsCache:
    """Cache full reports for Web callers without affecting the CLI service path.

    Reports change only through start_refresh() and refresh(); reading never
    triggers collection unless no report exists yet.
    """

    def __init__(
        self,
        *,
        builder: Callable[[ArchiveStore], StatsReport] = build_stats_report,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._builder = builder
        self._clock = clock
        self._condition = Condition()
        self._entries: dict[int, _CacheEntry] = {}
        self._refreshing: set[int] = set()
        self._refresh_failed: set[int] = set()
        self._errors: dict[int, Exception] = {}
        self._threads: dict[int, Thread] = {}

    @staticmethod
    def _key(store: ArchiveStore) -> int:
        return id(store)

    def _result_locked(self, key: int, entry: _CacheEntry) -> CachedStatsReport:
        return CachedStatsReport(
            report=entry.report,
            age_seconds=max(0.0, self._clock() - entry.stored_at),
            refreshing=key in self._refreshing,
            refresh_failed=key in self._refresh_failed,
        )

    def _finish_success(self, key: int, report: StatsReport) -> None:
        with self._condition:
            self._entries[key] = _CacheEntry(report=report, stored_at=self._clock())
            self._refreshing.discard(key)
            self._refresh_failed.discard(key)
            self._errors.pop(key, None)
            self._threads.pop(key, None)
            self._condition.notify_all()

    def _finish_failure(self, key: int, error: Exception) -> None:
        with self._condition:
            self._refreshing.discard(key)
            self._refresh_failed.add(key)
            self._errors[key] = error
            self._threads.pop(key, None)
            self._condition.notify_all()

    def _refresh_in_background(self, key: int, store: ArchiveStore) -> None:
        try:
            report = self._builder(store)
        except Exception as error:
            self._finish_failure(key, error)
        else:
            self._finish_success(key, report)

    def _start_refresh_locked(self, key: int, store: ArchiveStore) -> None:
        if key in self._refreshing:
            return
        self._refreshing.add(key)
        self._refresh_failed.discard(key)
        self._errors.pop(key, None)
        thread = Thread(
            target=self._refresh_in_background,
            args=(key, store),
            name="tweetnook-stats-refresh",
            daemon=True,
        )
        self._threads[key] = thread
        thread.start()

    def get(self, store: ArchiveStore) -> CachedStatsReport:
        """Return the cached report, waiting for or collecting the first one."""
        key = self._key(store)
        with self._condition:
            entry = self._entries.get(key)
            if entry is not None:
                return self._result_locked(key, entry)

            if key in self._refreshing:
                while key in self._refreshing and key not in self._entries:
                    self._condition.wait()
                entry = self._entries.get(key)
                if entry is not None:
                    return self._result_locked(key, entry)
                error = self._errors.get(key)
                if error is not None:
                    raise RuntimeError("statistics collection failed") from error

            self._refreshing.add(key)
            self._refresh_failed.discard(key)
            self._errors.pop(key, None)

        try:
            report = self._builder(store)
        except Exception as error:
            self._finish_failure(key, error)
            raise
        self._finish_success(key, report)
        with self._condition:
            return self._result_locked(key, self._entries[key])

    def refresh(self, store: ArchiveStore) -> CachedStatsReport:
        """Start one forced background refresh and keep serving the current report."""
        key = self._key(store)
        with self._condition:
            has_entry = key in self._entries
        if not has_entry:
            return self.get(store)
        with self._condition:
            entry = self._entries[key]
            self._start_refresh_locked(key, store)
            return self._result_locked(key, entry)

    def start_refresh(self, store: ArchiveStore) -> None:
        """Collect a new report in the background; readers keep the current one."""
        with self._condition:
            self._start_refresh_locked(self._key(store), store)

    def wait_for_refreshes(self) -> None:
        """Wait for active collectors before their shared store is closed."""
        while True:
            with self._condition:
                threads = list(self._threads.values())
            if not threads:
                return
            for thread in threads:
                thread.join()

    def clear(self) -> None:
        """Discard completed cache state."""
        with self._condition:
            self._entries.clear()
            self._refresh_failed.clear()
            self._errors.clear()


web_stats_cache = WebStatsCache()


def next_daily_refresh(refresh_time: str, after: datetime) -> datetime:
    """Return the first local refresh_time (HH:MM) strictly after after."""
    return next_run_after(
        ScheduleConfig(cadence="daily", time=refresh_time, randomize_time=False), after
    )


class DailyStatsRefresher:
    """Collect the Web report when the archive attaches, then once a day.

    A request that arrives before the first report waits for that collection;
    later requests keep the previous report while a new one is collected.
    """

    def __init__(
        self,
        store: ArchiveStore,
        refresh_time: str,
        *,
        cache: WebStatsCache = web_stats_cache,
        now: Callable[[], datetime] = lambda: datetime.now().astimezone(),
    ) -> None:
        self._store = store
        self._refresh_time = refresh_time
        self._cache = cache
        self._now = now
        self._stop = Event()
        self._wake = Event()
        self._lock = Lock()
        self._revision = 0
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._cache.start_refresh(self._store)
        self._thread = Thread(target=self._run, name="tweetnook-stats-daily", daemon=True)
        self._thread.start()

    def reload(self, refresh_time: str) -> None:
        with self._lock:
            if refresh_time == self._refresh_time:
                return
            self._refresh_time = refresh_time
            self._revision += 1
            self._wake.set()

    def _run(self) -> None:
        with self._lock:
            revision = self._revision
            due = next_daily_refresh(self._refresh_time, self._now())
        while not self._stop.is_set():
            with self._lock:
                if revision != self._revision:
                    revision = self._revision
                    due = next_daily_refresh(self._refresh_time, self._now())
                refresh_time = self._refresh_time
            # Re-check after every wake: waits can end early and the clock can jump.
            remaining = (due - self._now()).total_seconds()
            if remaining > 0:
                self._wake.wait(remaining)
                self._wake.clear()
                continue
            self._cache.start_refresh(self._store)
            due = next_daily_refresh(refresh_time, max(due, self._now()))

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join()
