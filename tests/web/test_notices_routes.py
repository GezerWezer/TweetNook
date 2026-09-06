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
