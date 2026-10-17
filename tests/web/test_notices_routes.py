from tweetnook.web.deps import server_state
from tweetnook.web.notices import NoticeStore
from tweetnook.web.routes import notices


def test_notice_routes_list_and_persist_dismissal(tmp_path, make_web_client) -> None:
    store = NoticeStore(tmp_path / "notices.json")
    store.add("one", "First", "First message", severity="info", action="setup")
    store.add("two", "Second", "Second message", severity="warning", action="schedule")
    server_state["notices"] = store
    client = make_web_client(notices.router)

    listing = client.get("/api/notices")
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()["notices"]] == ["two", "one"]

    dismissed = client.post("/api/notices/one/dismiss")
    assert dismissed.status_code == 200
    assert dismissed.json() == {"dismissed": True}

    restarted = NoticeStore(tmp_path / "notices.json")
    states = {item["id"]: item["dismissed"] for item in restarted.list()}
    assert states == {"two": False, "one": True}


def test_notice_routes_dismiss_all_and_clear_history(tmp_path, make_web_client) -> None:
    store = NoticeStore(tmp_path / "notices.json")
    store.add("one", "First", "First message")
    store.add("two", "Second", "Second message", run_id="run-2")
    store.add("three", "Third", "Third message")
    store.dismiss("one")
    server_state["notices"] = store
    client = make_web_client(notices.router)

    assert client.post("/api/notices/dismiss-all").json() == {"dismissed": 2}
    assert client.post("/api/notices/dismiss-all").json() == {"dismissed": 0}
    store.add("four", "Fourth", "Still unread")

    assert client.delete("/api/notices/dismissed").json() == {"cleared": 3}
    restarted = NoticeStore(tmp_path / "notices.json")
    assert [(item["id"], item["dismissed"]) for item in restarted.list()] == [("four", False)]


def test_notice_keeps_run_link_and_skips_redundant_writes(tmp_path) -> None:
    path = tmp_path / "notices.json"
    store = NoticeStore(path)
    store.add("job", "Sync failed", "Worker exited", run_id="run-1")
    store.add("plain", "Setup", "No run")
    items = {item["id"]: item for item in store.list()}
    assert items["job"]["run_id"] == "run-1"
    assert "run_id" not in items["plain"]

    assert store.dismiss("job") is True
    path.unlink()
    assert store.dismiss("job") is False
    assert store.dismiss("missing") is False
    assert not path.exists()


def test_notices_remain_readable_when_full_disk_prevents_persistence(tmp_path, monkeypatch):
    import errno

    from tweetnook.web.notices import NoticeStore

    def full(*args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr("tweetnook.web.notices.atomic_json", full)
    notices = NoticeStore(tmp_path / "notices.json")
    notices.add("disk-full", "Storage is full", "Free disk space, then resume.")
    assert notices.list()[0]["title"] == "Storage is full"
    assert notices.dismiss("disk-full") is True
    assert notices.list()[0]["dismissed"] is True
