import asyncio
import sqlite3
from types import SimpleNamespace

import httpx
import pytest

from tweetnook.client.fxtwitter import fetch_fx_statuses, iter_fx_statuses
from tweetnook.storage.backend import ArchiveStore
from tweetnook.storage.connection import SerializedConnection
from tweetnook.tagging import sdk


@pytest.mark.parametrize("exit_path", ["close", "callback", "outage", "cancel"])
async def test_fx_iterator_joins_workers_before_borrowed_client_cleanup(exit_path):
    entered, cleaning, release, cleaned = (asyncio.Event() for _ in range(4))

    async def transport(request):
        if request.url.path.endswith("/slow"):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cleaning.set()
                await release.wait()
                cleaned.set()
        await entered.wait()
        if exit_path == "cancel":
            await asyncio.Future()
        return httpx.Response(200, json={"code": 500})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url="https://fake"
    ) as client:
        ids = ["slow", *map(str, range(8))]
        if exit_path == "close":
            iterator = iter_fx_statuses(client, ids, concurrency=9)
            await anext(iterator)
            task = asyncio.create_task(iterator.aclose())
        else:

            def callback(lookup):
                if exit_path == "callback":
                    raise RuntimeError("callback failed")

            task = asyncio.create_task(
                fetch_fx_statuses(client, ids, concurrency=9, on_lookup=callback)
            )
            if exit_path == "cancel":
                await entered.wait()
                task.cancel()
        try:
            await asyncio.wait_for(cleaning.wait(), 2)
            assert not task.done()
            assert not client.is_closed
        finally:
            release.set()
        if exit_path == "callback":
            with pytest.raises(RuntimeError, match="callback failed"):
                await task
        elif exit_path == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await task
        assert cleaned.is_set()
        assert not client.is_closed


def test_store_constructor_closes_connection_when_setup_fails(tmp_path, monkeypatch):
    connections = []
    connect = sqlite3.connect

    class SetupFailure(SerializedConnection):
        closed = False

        def execute(self, sql, *args):
            if "cache_size" in sql:
                raise sqlite3.OperationalError("setup failed")
            return super().execute(sql, *args)

        def close(self):
            self.closed = True
            return super().close()

    def open_connection(*args, **kwargs):
        connection = connect(*args, **{**kwargs, "factory": SetupFailure})
        connections.append(connection)
        return connection

    monkeypatch.setattr("tweetnook.storage.backend.sqlite3.connect", open_connection)
    with pytest.raises(sqlite3.OperationalError, match="setup failed"):
        ArchiveStore(tmp_path / "archive.db", create=True)
    assert connections[0].closed
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")


@pytest.mark.parametrize("borrowed", [False, True])
@pytest.mark.parametrize("failure", [None, "listing", "adaptation"])
def test_catalog_client_closes_only_owned_clients(monkeypatch, borrowed, failure):
    closed = []

    def listing():
        if failure == "listing":
            raise RuntimeError("listing failed")
        yield SimpleNamespace(
            name="gemini-test",
            supported_actions=["generateContent"],
            input_token_limit="bad" if failure == "adaptation" else 1,
        )

    client = SimpleNamespace(
        models=SimpleNamespace(list=listing), close=lambda: closed.append(True)
    )
    monkeypatch.setattr(
        sdk, "load_gemini", lambda: (SimpleNamespace(Client=lambda **kw: client), None, None)
    )
    if failure:
        with pytest.raises((RuntimeError, ValueError)):
            sdk.list_models("synthetic", client=client if borrowed else None)
    else:
        assert len(sdk.list_models("synthetic", client=client if borrowed else None)) == 1
    assert closed == ([] if borrowed else [True])
