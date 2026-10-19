from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import threading
from types import SimpleNamespace

import pytest

from tweetnook.web.activity_stream import ActivityStream
from tweetnook.web.deps import SESSION_COOKIE, create_session_token, server_state
from tweetnook.web.routes import activity
from tweetnook.web.server import app


async def _next_status(events):
    while True:
        frame = await asyncio.wait_for(anext(events), 2)
        if frame.startswith("event: activity"):
            return json.loads(frame.split("data: ", 1)[1])


async def test_shared_watcher_coalesces_updates_and_cleans_up():
    value = {"count": 1}
    reads = 0

    def snapshot():
        nonlocal reads
        reads += 1
        return dict(value)

    stream = ActivityStream(snapshot, interval=0.01)
    first, second = stream.events(), stream.events()
    assert await anext(first) == "retry: 2000\n\n"
    assert await anext(second) == "retry: 2000\n\n"
    task = stream._task
    assert await _next_status(first) == {"count": 1}
    assert await _next_status(second) == {"count": 1}
    assert reads == 1
    value["count"] = 2
    assert await _next_status(first) == {"count": 2}
    value["count"] = 3
    assert await _next_status(first) == {"count": 3}
    assert await _next_status(second) == {"count": 3}
    assert stream._task is task
    await first.aclose()
    assert stream._task is task
    await second.aclose()
    assert task.done()
    assert stream._task is None
    assert not stream._subscribers


async def test_reconnect_starts_with_latest_state_without_replay():
    value = {"count": 1}
    stream = ActivityStream(lambda: dict(value), interval=0.01)
    events = stream.events()
    assert await _next_status(events) == {"count": 1}
    await events.aclose()
    value["count"] = 9
    events = stream.events()
    assert await _next_status(events) == {"count": 9}
    await events.aclose()


async def test_keepalive_and_shutdown_release_waiting_connections():
    stream = ActivityStream(lambda: {"active": False}, interval=0.01, heartbeat=0.02)
    events = stream.events()
    await _next_status(events)
    assert await anext(events) == ": keepalive\n\n"
    waiting = asyncio.create_task(anext(events))
    await stream.close()
    with pytest.raises(StopAsyncIteration):
        await waiting
    assert not stream._subscribers
    assert stream._task is None


async def test_cancelled_connection_releases_watcher():
    stream = ActivityStream(lambda: {}, interval=0.01)
    events = stream.events()
    await _next_status(events)
    waiting = asyncio.create_task(anext(events))
    await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert not stream._subscribers
    assert stream._task is None


async def test_transient_reader_failure_recovers(caplog):
    calls = 0

    def read():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("temporary read failure")
        return {"active": True}

    stream = ActivityStream(read, interval=0.01)
    events = stream.events()
    assert await _next_status(events) == {"active": True}
    assert "temporary read failure" in caplog.text
    await events.aclose()


async def test_disconnect_during_read_cannot_publish_stale_state_after_reconnect():
    started, release = threading.Event(), threading.Event()
    calls = 0

    def snapshot():
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            assert release.wait(2)
            return {"count": 1}
        return {"count": 2}

    stream = ActivityStream(snapshot, interval=0.01)
    first = stream.events()
    await anext(first)
    try:
        assert await asyncio.to_thread(started.wait, 2)
        old_task = stream._task
        await first.aclose()
        assert old_task.done()
        second = stream.events()
        assert await _next_status(second) == {"count": 2}
        release.set()
        await asyncio.sleep(0.03)
        assert stream._latest == '{"count":2}'
        await second.aclose()
        assert not stream._subscribers
    finally:
        release.set()
        await stream.close()


async def test_stream_matches_polling_for_completion_stop_and_crash(tmp_path):
    status_file = tmp_path / "activity-status.json"
    server_state["paths"] = SimpleNamespace(activity_status_file=status_file, data_dir=tmp_path)
    stream = ActivityStream(activity._stream_snapshot_reader(), interval=0.01)
    events = stream.events()

    def save(**changes):
        snapshot = {
            "pid": os.getpid(),
            "running": True,
            "title": "tweetnook sync",
            "run_id": "example",
            "steps": [],
            **changes,
        }
        status_file.write_text(json.dumps(snapshot))

    save()
    assert await _next_status(events) == activity.get_activity_status(True)
    save(running=False, stopped=True, success=False)
    stopped = await _next_status(events)
    assert stopped == activity.get_activity_status(True)
    assert stopped["last_snapshot"]["stopped"] is True
    save(running=False, success=True)
    complete = await _next_status(events)
    assert complete == activity.get_activity_status(True)
    assert complete["last_snapshot"]["success"] is True
    save(pid=2**31 - 1)
    crashed = await _next_status(events)
    assert crashed == activity.get_activity_status(True)
    assert not crashed["active"]
    assert crashed["last_snapshot"]["success"] is None
    assert "worker ended" in crashed["last_snapshot"]["summary"]
    await events.aclose()


def test_idle_stream_caches_history_but_reads_schedule_and_snapshot_changes(tmp_path, monkeypatch):
    status_file = tmp_path / "activity-status.json"
    server_state["paths"] = SimpleNamespace(activity_status_file=status_file, data_dir=tmp_path)
    reads = []
    monkeypatch.setattr(activity.time, "monotonic", lambda: 1)
    monkeypatch.setattr(activity, "latest_finished_snapshot", lambda *_a, **_k: reads.append(1))
    reader = activity._stream_snapshot_reader()
    for _ in range(20):
        assert not reader()["active"]
    assert len(reads) == 1
    server_state["schedule_manager"] = SimpleNamespace(status=lambda: {"enabled": True})
    assert reader()["schedule"] == {"enabled": True}
    status_file.write_text(json.dumps({"running": False, "title": "tweetnook sync"}))
    assert reader()["last_snapshot"] is not None
    assert len(reads) == 2
    monkeypatch.setattr(activity.time, "monotonic", lambda: 6)
    reader()
    assert len(reads) == 3


def test_stream_requires_auth_before_subscribing(make_web_client):
    response = make_web_client(activity.router, password="secret").get("/api/activity/events")
    assert response.status_code == 401
    assert "activity_stream" not in server_state


@pytest.mark.parametrize("auth", ["basic", "cookie"])
async def test_authenticated_stream_setup_gate_proxy_headers_and_disconnect(auth):
    password_hash = hashlib.sha256(b"secret").hexdigest()
    server_state["password_hash"] = password_hash
    server_state["setup_coordinator"] = SimpleNamespace(state=SimpleNamespace(completed=False))
    headers = [(b"accept-encoding", b"gzip")]
    if auth == "basic":
        headers.append((b"authorization", b"Basic " + base64.b64encode(b"user:secret")))
    else:
        token = create_session_token(password_hash)
        headers.append((b"cookie", f"{SESSION_COOKIE}={token}".encode()))
    received = asyncio.Queue()
    sent = asyncio.Queue()
    await received.put({"type": "http.request", "body": b"", "more_body": False})
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/activity/events",
        "raw_path": b"/api/activity/events",
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 1234),
        "server": ("127.0.0.1", 8000),
    }
    connection = asyncio.create_task(app(scope, received.get, sent.put))
    try:
        start = await asyncio.wait_for(sent.get(), 2)
        assert start["status"] == 200
        response_headers = dict(start["headers"])
        assert response_headers[b"content-type"].startswith(b"text/event-stream")
        assert response_headers[b"x-accel-buffering"] == b"no"
        assert response_headers[b"cache-control"] == b"no-cache, no-transform"
        assert b"content-encoding" not in response_headers
        while True:
            message = await asyncio.wait_for(sent.get(), 2)
            if b"event: activity" in message.get("body", b""):
                assert b'"active":false' in message["body"]
                break
        await received.put({"type": "http.disconnect"})
        await asyncio.wait_for(connection, 2)
        assert not server_state["activity_stream"]._subscribers
        assert server_state["activity_stream"]._task is None
    finally:
        connection.cancel()
        await server_state["activity_stream"].close()
