from __future__ import annotations

import asyncio
from io import StringIO
from pathlib import Path

import httpx
import pytest
from rich.console import Console

import tweetnook
import tweetnook.sync.followups as followups_module
from tests.collection_sync.conftest import _console, _op_and_variables, _save_query_ids
from tests.conftest import (
    make_bookmarks_response,
    make_likes_response,
)
from tweetnook.config import AppConfig
from tweetnook.core.exceptions import ProcessLockError, TweetNookError
from tweetnook.core.locking import ProcessLock
from tweetnook.storage import open_archive_store
from tweetnook.sync.collection import RemotePreflightError, sync_all, sync_collection
from tweetnook.sync.followups import SyncFollowupPlan


@pytest.mark.asyncio
async def test_sync_all_aborts_on_failed_preflight_without_writes(paths, config: AppConfig) -> None:
    _save_query_ids(paths)

    async def handler(request: httpx.Request) -> httpx.Response:
        operation, variables = _op_and_variables(request)
        if int(variables["count"]) != 1:
            raise AssertionError("sync all should not reach persisted sync after failed preflight")
        if operation == "Bookmarks":
            return httpx.Response(200, json=make_bookmarks_response(["1"]), request=request)
        return httpx.Response(401, request=request)

    with pytest.raises(RemotePreflightError):
        await sync_all(
            full=False,
            limit=None,
            config=config,
            paths=paths,
            transport=httpx.MockTransport(handler),
            console=_console(),
            sleep=lambda _: asyncio.sleep(0),
        )

    assert not paths.database_path.exists()


@pytest.mark.asyncio
async def test_sync_all_reports_partial_runtime_failure(paths, config: AppConfig) -> None:
    _save_query_ids(paths)
    seen_operations: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        operation, variables = _op_and_variables(request)
        count = int(variables["count"])
        cursor = variables.get("cursor")
        seen_operations.append(f"{operation}:{count}:{cursor}")
        if count == 1 and operation == "Bookmarks":
            return httpx.Response(200, json=make_bookmarks_response(["1"]), request=request)
        if count == 1 and operation == "Likes":
            return httpx.Response(200, json=make_likes_response(["10"]), request=request)
        if operation == "Bookmarks":
            return httpx.Response(200, json=make_bookmarks_response(["1"]), request=request)
        return httpx.Response(500, request=request)

    outcome = await sync_all(
        full=False,
        limit=None,
        config=config,
        paths=paths,
        transport=httpx.MockTransport(handler),
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
    )

    assert outcome.exit_code == 2
    assert [result.collection for result in outcome.results] == ["bookmarks"]
    assert "likes" in outcome.errors

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        counts = store.counts()
        assert counts["collections"] == 1
        assert counts["tweets"] == 1
    finally:
        store.close()


@pytest.mark.asyncio
async def test_sync_all_limit_applies_per_collection(paths, config: AppConfig) -> None:
    _save_query_ids(paths)

    async def handler(request: httpx.Request) -> httpx.Response:
        operation, variables = _op_and_variables(request)
        count = int(variables["count"])
        cursor = variables.get("cursor")
        if count == 1:
            if operation == "Bookmarks":
                return httpx.Response(200, json=make_bookmarks_response(["1"]), request=request)
            return httpx.Response(200, json=make_likes_response(["10"]), request=request)
        if operation == "Bookmarks" and cursor is None:
            return httpx.Response(
                200,
                json=make_bookmarks_response(["1"], cursor="bookmark-next"),
                request=request,
            )
        if operation == "Likes" and cursor is None:
            return httpx.Response(
                200,
                json=make_likes_response(["10"], cursor="likes-next"),
                request=request,
            )
        raise AssertionError(f"unexpected request {operation} {cursor}")

    outcome = await sync_all(
        full=False,
        limit=1,
        config=config,
        paths=paths,
        transport=httpx.MockTransport(handler),
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
    )

    assert outcome.exit_code == 0
    assert [(result.collection, result.pages_fetched) for result in outcome.results] == [
        ("bookmarks", 1),
        ("likes", 1),
    ]


@pytest.mark.asyncio
async def test_sync_all_shared_preflight_probes_once_per_collection(
    paths, config: AppConfig
) -> None:
    _save_query_ids(paths)
    probe_counts = {"Bookmarks": 0, "Likes": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        operation, variables = _op_and_variables(request)
        count = int(variables["count"])
        cursor = variables.get("cursor")
        if count == 1:
            probe_counts[operation] += 1
            if operation == "Bookmarks":
                return httpx.Response(200, json=make_bookmarks_response(["1"]), request=request)
            return httpx.Response(200, json=make_likes_response(["10"]), request=request)
        if operation == "Bookmarks" and cursor is None:
            return httpx.Response(200, json=make_bookmarks_response(["1"]), request=request)
        if operation == "Likes" and cursor is None:
            return httpx.Response(200, json=make_likes_response(["10"]), request=request)
        raise AssertionError(f"unexpected request {operation} {cursor}")

    outcome = await sync_all(
        full=False,
        limit=1,
        config=config,
        paths=paths,
        transport=httpx.MockTransport(handler),
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
    )

    assert outcome.exit_code == 0
    assert probe_counts == {"Bookmarks": 1, "Likes": 1}


@pytest.mark.asyncio
async def test_sync_all_follows_the_configured_collections(paths, config: AppConfig) -> None:
    _save_query_ids(paths)
    config.sync.collections = ["likes"]
    requested: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        operation, _variables = _op_and_variables(request)
        requested.append(operation)
        if operation != "Likes":
            raise AssertionError(f"unexpected request {operation}")
        return httpx.Response(200, json=make_likes_response(["10"]), request=request)

    outcome = await sync_all(
        full=False,
        limit=1,
        config=config,
        paths=paths,
        transport=httpx.MockTransport(handler),
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
    )

    assert outcome.exit_code == 0
    assert [result.collection for result in outcome.results] == ["likes"]
    assert set(requested) == {"Likes"}


def test_process_lock_prevents_second_holder(paths) -> None:
    first = ProcessLock(paths.lock_file)
    second = ProcessLock(paths.lock_file)
    first.acquire()
    try:
        with pytest.raises(ProcessLockError):
            second.acquire()
    finally:
        first.release()


@pytest.mark.asyncio
async def test_sync_failure_releases_lock(paths, config: AppConfig, auth_bundle) -> None:
    _save_query_ids(paths)

    async def handler(request: httpx.Request) -> httpx.Response:
        _operation, variables = _op_and_variables(request)
        if int(variables["count"]) == 1:
            return httpx.Response(200, json=make_bookmarks_response(["1"]), request=request)
        return httpx.Response(500, request=request)

    with pytest.raises(TweetNookError):
        await sync_collection(
            "bookmarks",
            full=False,
            limit=None,
            config=config,
            paths=paths,
            auth_bundle=auth_bundle,
            query_ids={"Bookmarks": "qid-bookmarks"},
            transport=httpx.MockTransport(handler),
            console=_console(),
            sleep=lambda _: asyncio.sleep(0),
        )

    lock = ProcessLock(paths.lock_file)
    lock.acquire()
    lock.release()


def test_first_run_creates_dirs_and_missing_auth_is_actionable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = {
        "XDG_CONFIG_HOME": str(tmp_path / "config-root"),
        "XDG_DATA_HOME": str(tmp_path / "data-root"),
        "XDG_CACHE_HOME": str(tmp_path / "cache-root"),
    }
    monkeypatch.delenv("TWEETNOOK_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("TWEETNOOK_CT0", raising=False)
    monkeypatch.delenv("TWEETNOOK_USER_ID", raising=False)

    from tweetnook.auth import resolve_auth_bundle
    from tweetnook.config import load_config

    config, paths = load_config(env)
    assert paths.config_dir.exists()
    assert paths.data_dir.exists()
    assert paths.cache_dir.exists()

    with pytest.raises(Exception) as exc_info:
        resolve_auth_bundle(config, env=env)
    assert "TWEETNOOK_AUTH_TOKEN" in str(exc_info.value)
    assert "browser" not in str(exc_info.value).lower()


def _logger_lines_with_cookie_names(package: Path) -> tuple[int, list[str]]:
    scanned = 0
    found = []
    for path in sorted(package.rglob("*.py")):
        scanned += 1
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "logger." in line and ("auth_token" in line or "ct0" in line):
                found.append(f"{path.relative_to(package)}:{number}")
    return scanned, found


def test_security_audit_no_logger_calls_with_cookie_values() -> None:
    # Resolved from the import, so moving this test can't point the scan at an empty tree.
    package = Path(tweetnook.__file__).resolve().parent
    scanned, found = _logger_lines_with_cookie_names(package)

    assert scanned > 100
    assert (package / "auth" / "__init__.py").exists()
    assert found == []


def test_cookie_log_scan_flags_a_logged_cookie(tmp_path) -> None:
    (tmp_path / "client.py").write_text(
        'logger.info("token %s", auth_token)\nlogger.info("ok")\n', encoding="utf-8"
    )
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "auth.py").write_text('logger.debug("csrf=%s", ct0)\n', encoding="utf-8")

    assert _logger_lines_with_cookie_names(tmp_path) == (2, ["client.py:1", "web/auth.py:1"])


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["media", "database"])
async def test_storage_exhaustion_stops_followups_before_link_previews(
    paths, config, auth_bundle, monkeypatch, failure
):
    import errno
    import sqlite3

    from tweetnook.jobs.resource_failure import StorageExhaustedError

    calls = []

    async def full_media(**kwargs):
        calls.append("media")
        if failure == "media":
            raise OSError(errno.ENOSPC, "No space left on device")
        raise sqlite3.OperationalError("database or disk is full")

    async def unfurl(**kwargs):
        calls.append("urls")
        pytest.fail("link previews must not run on a full volume")

    monkeypatch.setattr(followups_module, "_run_followup_media", full_media)
    monkeypatch.setattr(followups_module, "_run_followup_unfurl", unfurl)
    with pytest.raises(StorageExhaustedError, match="Free or add disk space"):
        await followups_module._run_auto_followups(
            plan=SyncFollowupPlan(threads=False, resurrection=False, articles=False),
            config=config,
            paths=paths,
            auth_bundle=auth_bundle,
            transport=None,
            console=Console(file=StringIO()),
            sleep=lambda _: asyncio.sleep(0),
        )
    assert calls == ["media"]
