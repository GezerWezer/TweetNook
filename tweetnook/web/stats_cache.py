"""Process-local stale-while-revalidate cache for Web statistics."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import Condition, Thread

from tweetnook.stats import StatsReport, build_stats_report
from tweetnook.storage import ArchiveStore

WEB_STATS_MAX_AGE_SECONDS = 5 * 60


@dataclass(frozen=True, slots=True)
class CachedStatsReport:
    """One report plus cache state needed by the browser."""

    report: StatsReport
    age_seconds: float
    stale: bool
    refreshing: bool
    refresh_failed: bool


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    report: StatsReport
    stored_at: float


class WebStatsCache:
    """Cache full reports for Web callers without affecting the CLI service path."""

    def __init__(
        self,
        *,
        max_age_seconds: float = WEB_STATS_MAX_AGE_SECONDS,
        builder: Callable[[ArchiveStore], StatsReport] = build_stats_report,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_age_seconds = max_age_seconds
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
        age_seconds = max(0.0, self._clock() - entry.stored_at)
        return CachedStatsReport(
            report=entry.report,
            age_seconds=age_seconds,
            stale=age_seconds >= self.max_age_seconds,
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

    def get(
        self,
        store: ArchiveStore,
        *,
        revalidate: bool = True,
    ) -> CachedStatsReport:
        """Return a cached report, refreshing stale entries in the background."""
        key = self._key(store)
        with self._condition:
            entry = self._entries.get(key)
            if entry is not None:
                result = self._result_locked(key, entry)
                if revalidate and result.stale:
                    self._start_refresh_locked(key, store)
                    result = self._result_locked(key, entry)
                return result

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
            return self.get(store, revalidate=False)
        with self._condition:
            entry = self._entries[key]
            self._start_refresh_locked(key, store)
            return self._result_locked(key, entry)

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
