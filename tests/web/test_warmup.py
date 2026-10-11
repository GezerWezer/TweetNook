from __future__ import annotations

import threading
import time

import pytest

from tweetnook.storage import backend
from tweetnook.storage.backend import ArchiveStore
from tweetnook.web.warmup import ArchiveWarmer


@pytest.fixture
def store(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    yield store
    store.close()


def test_warm_read_cache_reads_on_its_own_read_only_connection(store, monkeypatch):
    seen = []
    connect = backend.sqlite3.connect

    def recording_connect(database, *args, **kwargs):
        seen.append((database, kwargs.get("uri")))
        return connect(database, *args, **kwargs)

    monkeypatch.setattr(backend.sqlite3, "connect", recording_connect)
    changes = store.conn.total_changes

    store.warm_read_cache()

    assert len(seen) == 1
    assert seen[0][0].endswith("archive.db?mode=ro") and seen[0][1] is True
    assert store.conn.total_changes == changes


def test_interrupt_stops_a_running_warm_read_and_later_ones(store, monkeypatch):
    endless = (
        "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT COUNT(*) FROM n"
    )
    monkeypatch.setattr(backend, "_WARM_READ_QUERIES", (endless,))
    thread = threading.Thread(target=store.warm_read_cache)
    thread.start()
    time.sleep(0.05)

    store.interrupt_warm_reads()
    thread.join(timeout=10)

    assert not thread.is_alive()
    monkeypatch.setattr(
        backend.sqlite3, "connect", lambda *_a, **_k: pytest.fail("cancelled warm read connected")
    )
    store.warm_read_cache()


class _Store:
    def __init__(self):
        self.calls: list[str] = []
        self.release = threading.Event()
        self.started = threading.Event()
        self.interrupted = False
        self.finished = threading.Event()
        self.passes = 1

    def warm_like_order(self):
        self.calls.append("like_order")
        self.started.set()
        self.release.wait(timeout=10)

    def warm_read_cache(self):
        self.calls.append("read_cache")
        if self.calls.count("read_cache") == self.passes:
            self.finished.set()

    def interrupt_warm_reads(self):
        self.interrupted = True


def test_requests_during_a_pass_coalesce_into_one_follow_up_pass():
    store = _Store()
    store.passes = 2
    warmer = ArchiveWarmer(store)

    warmer.request()
    assert store.started.wait(timeout=10)
    warmer.request()
    warmer.request()
    store.release.set()
    assert store.finished.wait(timeout=10)
    warmer.stop()

    assert store.calls == ["like_order", "read_cache", "like_order", "read_cache"]
    assert store.interrupted is True


def test_failures_are_ignored_and_stopped_warmers_do_not_start():
    class Failing(_Store):
        def warm_like_order(self):
            self.calls.append("like_order")
            raise RuntimeError("busy")

    store = Failing()
    warmer = ArchiveWarmer(store)
    warmer.request()
    assert store.finished.wait(timeout=10)
    warmer.stop()
    assert store.calls == ["like_order", "read_cache"]

    warmer.request()
    assert store.calls == ["like_order", "read_cache"]
