from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tweetnook.config import AppConfig
from tweetnook.web import server
from tweetnook.web.deps import server_state


def test_failed_attachment_rolls_back_store_and_can_retry(paths, monkeypatch):
    closed = []
    store = SimpleNamespace(
        migration_report=None,
        count_incomplete_initial_enrichment=lambda: 0,
        close=lambda: closed.append("store"),
    )
    server_state.update(paths=paths, config=AppConfig())
    monkeypatch.setattr(server, "open_archive_store", lambda *a, **k: store)

    def fail(*args):
        raise OSError("reminder query failed")

    monkeypatch.setattr(server, "print_pending_archive_enrichment_reminder", fail)
    with pytest.raises(OSError, match="reminder query failed"):
        server.attach_archive_store()
    assert closed == ["store"]
    assert not server.archive_is_ready()
    monkeypatch.setattr(server, "print_pending_archive_enrichment_reminder", lambda *a: None)
    monkeypatch.setattr(
        server, "ArchiveWarmer", lambda _: SimpleNamespace(request=lambda: None, stop=lambda: None)
    )
    monkeypatch.setattr(
        server,
        "DailyStatsRefresher",
        lambda *a: SimpleNamespace(start=lambda: None, stop=lambda: None),
    )
    assert server.attach_archive_store()
    server.detach_archive_store()
    assert closed == ["store", "store"]


def test_shutdown_attempts_all_owners_after_stream_and_refresher_fail(monkeypatch):
    events = []

    async def close_stream():
        events.append("stream")
        raise OSError("stream failed")

    def stop_refresher():
        events.append("refresher")
        raise OSError("refresher failed")

    server_state.update(
        activity_stream=SimpleNamespace(close=close_stream),
        stats_refresher=SimpleNamespace(stop=stop_refresher),
        archive_warmer=SimpleNamespace(stop=lambda: events.append("warmer")),
        store=SimpleNamespace(close=lambda: events.append("store")),
        job_supervisor=SimpleNamespace(shutdown=lambda: events.append("supervisor")),
    )
    with TestClient(FastAPI(lifespan=server.lifespan)):
        pass
    assert events == ["stream", "supervisor", "refresher", "warmer", "store"]
    assert "store" not in server_state
