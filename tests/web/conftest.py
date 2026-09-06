from __future__ import annotations

import hashlib
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tweetnook.web.deps import get_store, server_state


@pytest.fixture(autouse=True)
def isolated_server_state() -> Iterator[None]:
    original = dict(server_state)
    server_state.clear()
    yield
    server_state.clear()
    server_state.update(original)


@pytest.fixture
def make_web_client():
    clients: list[TestClient] = []

    def factory(router, *, store=None, password: str | None = None) -> TestClient:
        app = FastAPI()
        app.include_router(router)
        if store is not None:
            app.dependency_overrides[get_store] = lambda: store
        if password is not None:
            server_state["password_hash"] = hashlib.sha256(password.encode()).hexdigest()
        client = TestClient(app)
        clients.append(client)
        return client

    yield factory

    for client in clients:
        client.close()
