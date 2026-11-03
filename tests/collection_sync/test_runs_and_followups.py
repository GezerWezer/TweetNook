from __future__ import annotations

import asyncio
import errno
import json
from contextlib import nullcontext
from io import StringIO
from types import SimpleNamespace

import httpx
import pytest
from rich.console import Console

import tweetnook.storage.backend as storage_backend
import tweetnook.sync.collection as sync_module
import tweetnook.sync.followups as followups_module
from tests.collection_sync.conftest import _console, _op_and_variables, _save_query_ids
from tests.conftest import (
    make_bookmarks_response,
)
from tweetnook.config import AppConfig
from tweetnook.enrichment.articles import ArticleRefreshResult
from tweetnook.enrichment.media import MediaDownloadResult
from tweetnook.enrichment.threads import ThreadExpandResult
from tweetnook.enrichment.unfurl import UrlUnfurlResult
from tweetnook.jobs.activity_history import mark_run_process_result, read_run
from tweetnook.jobs.pipeline import PipelineReporter
from tweetnook.jobs.resource_failure import StorageExhaustedError
from tweetnook.storage import open_archive_store
from tweetnook.sync.collection import sync_collection
from tweetnook.sync.followups import SyncFollowupPlan, plan_sync_pipeline


@pytest.mark.parametrize(
    "connected,skip,expected", [(True, True, True), (True, False, False), (False, True, False)]
)
async def test_sync_skips_only_quote_expansion_when_replay_option_is_enabled(
    paths, config, auth_bundle, monkeypatch, connected, skip, expected
) -> None:
    config.replay.url = "http://replay.test" if connected else None
    config.replay.skip_quote_expansion = skip
    calls = []

    async def expand(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(processed=1)

    monkeypatch.setattr("tweetnook.enrichment.threads.expand_threads", expand)
    await followups_module._run_followup_threads(
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=None,
        console=_console(),
        sleep=asyncio.sleep,
    )
    assert len(calls) == 1
    assert calls[0]["skip_quotes"] is expected
    assert calls[0]["limit"] is None


@pytest.mark.parametrize("connected,skip", [(True, True), (True, False), (False, True)])
async def test_replay_can_skip_only_link_previews(
    paths, config, auth_bundle, monkeypatch, connected, skip
):
    config.replay.url = "http://replay.test" if connected else None
    config.replay.skip_link_previews = skip
    calls = []

    async def previews(**kwargs):
        calls.append("previews")
        return SimpleNamespace(processed=1, updated=1, failed=0)

    monkeypatch.setattr(followups_module, "_run_followup_unfurl", previews)
    plan = SyncFollowupPlan(
        threads=False, resurrection=False, articles=False, media=False, tagging=False
    )
    reporter = PipelineReporter(_console(), "sync", interactive=False)
    with reporter:
        plan_sync_pipeline(reporter, config=config, collections=[], followups=plan, head_only=False)
        await followups_module.run_auto_followups(
            plan=plan,
            config=config,
            paths=paths,
            auth_bundle=auth_bundle,
            transport=None,
            console=_console(),
            sleep=asyncio.sleep,
        )
        assert calls == ([] if connected and skip else ["previews"])
        if connected and skip:
            assert reporter.step_state("urls") == "skipped"


def test_sync_pipeline_is_fully_planned_before_work_and_filters_disabled_followups() -> None:
    reporter = PipelineReporter(_console(), "sync", interactive=False)
    config = AppConfig()
    config.tagging.enabled = False
    followups = SyncFollowupPlan(
        threads=True,
        resurrection=False,
        articles=False,
        media=True,
        unfurl=False,
    )

    plan_sync_pipeline(
        reporter,
        config=config,
        collections=("bookmarks", "likes"),
        followups=followups,
        head_only=False,
    )

    assert [step.key for step in reporter.steps] == [
        "preflight:bookmarks,likes",
        "sync:bookmarks:head",
        "sync:likes:head",
        "threads",
        "media",
    ]
    assert all(step.state == "pending" for step in reporter.steps)


@pytest.mark.asyncio
async def test_sync_collection_end_to_end_and_resume(paths, config: AppConfig, auth_bundle) -> None:
    _save_query_ids(paths)
    seen: list[tuple[str, str | None, int]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        operation, variables = _op_and_variables(request)
        cursor = variables.get("cursor")
        count = int(variables["count"])
        seen.append((operation, cursor if isinstance(cursor, str) else None, count))

        if count == 1:
            return httpx.Response(
                200, json=make_bookmarks_response(["1"], cursor="probe-cursor"), request=request
            )
        if cursor is None:
            return httpx.Response(
                200, json=make_bookmarks_response(["1"], cursor="c1"), request=request
            )
        if cursor == "c1":
            return httpx.Response(
                200, json=make_bookmarks_response(["2"], cursor="c2"), request=request
            )
        if cursor == "c2":
            return httpx.Response(200, json=make_bookmarks_response(["3"]), request=request)
        raise AssertionError(f"unexpected request cursor {cursor}")

    transport = httpx.MockTransport(handler)
    first = await sync_collection(
        "bookmarks",
        full=False,
        limit=1,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        query_ids={"Bookmarks": "qid-bookmarks"},
        transport=transport,
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
    )
    assert first.pages_fetched == 1

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        state = store.get_sync_state("bookmark")
        assert state.backfill_incomplete is True
        assert state.backfill_cursor == "c1"
    finally:
        store.close()

    seen.clear()

    async def second_handler(request: httpx.Request) -> httpx.Response:
        operation, variables = _op_and_variables(request)
        cursor = variables.get("cursor")
        count = int(variables["count"])
        seen.append((operation, cursor if isinstance(cursor, str) else None, count))
        if count == 1:
            return httpx.Response(
                200, json=make_bookmarks_response(["new"], cursor="probe-cursor"), request=request
            )
        if cursor is None:
            return httpx.Response(
                200, json=make_bookmarks_response(["new", "1"], cursor="ignored"), request=request
            )
        if cursor == "c1":
            return httpx.Response(
                200, json=make_bookmarks_response(["2"], cursor="c2"), request=request
            )
        if cursor == "c2":
            return httpx.Response(200, json=make_bookmarks_response(["3"]), request=request)
        raise AssertionError(f"unexpected request cursor {cursor}")

    second = await sync_collection(
        "bookmarks",
        full=False,
        limit=None,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        query_ids={"Bookmarks": "qid-bookmarks"},
        transport=httpx.MockTransport(second_handler),
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
    )

    assert second.pages_fetched == 3
    assert [item for item in seen if item[2] == 20][:2] == [
        ("Bookmarks", None, 20),
        ("Bookmarks", "c1", 20),
    ]

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        counts = store.counts()
        assert counts["raw_captures"] == 4
        assert counts["tweets"] == 4
        assert counts["collections"] == 4
        state = store.get_sync_state("bookmark")
        assert state.backfill_incomplete is False
        assert state.last_head_tweet_id == "new"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_sync_collection_runs_followups_when_requested(
    paths,
    config: AppConfig,
    auth_bundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _save_query_ids(paths)
    calls: list[object] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        _operation, variables = _op_and_variables(request)
        count = int(variables["count"])
        if count == 1:
            return httpx.Response(
                200,
                json=make_bookmarks_response(["probe"], cursor="probe-cursor"),
                request=request,
            )
        return httpx.Response(200, json=make_bookmarks_response(["1"]), request=request)

    async def fake_followups(**kwargs) -> None:
        calls.append(kwargs["plan"])

    monkeypatch.setattr(sync_module, "run_auto_followups", fake_followups)

    result = await sync_collection(
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
        followups=SyncFollowupPlan(),
    )

    assert result.pages_fetched == 1
    assert calls == [SyncFollowupPlan()]


@pytest.mark.asyncio
async def test_sync_stage_database_opens_never_run_quick_check(
    paths,
    config: AppConfig,
    auth_bundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _save_query_ids(paths)
    initial = open_archive_store(paths, create=True, config=config)
    assert initial is not None
    initial.close()
    statements: list[str] = []
    original_connect = storage_backend.sqlite3.connect

    def tracing_connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(storage_backend.sqlite3, "connect", tracing_connect)

    async def handler(request: httpx.Request) -> httpx.Response:
        _operation, variables = _op_and_variables(request)
        count = int(variables["count"])
        tweet_ids = ["probe"] if count == 1 else ["1"]
        return httpx.Response(200, json=make_bookmarks_response(tweet_ids), request=request)

    async def fake_followups(**_kwargs) -> None:
        for _stage in range(5):
            stage_store = open_archive_store(paths, create=False, config=config)
            assert stage_store is not None
            stage_store.close()

    monkeypatch.setattr(sync_module, "run_auto_followups", fake_followups)

    result = await sync_collection(
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
        followups=SyncFollowupPlan(),
    )

    assert result.pages_fetched == 1
    assert statements
    assert not any("quick_check" in statement for statement in statements)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage", ["threads", "resurrection", "articles", "media", "unfurl", "tagging"]
)
@pytest.mark.parametrize("disk_full", [False, True])
async def test_followup_outcomes_preserve_success_except_fatal_storage(
    paths, config, auth_bundle, monkeypatch, stage, disk_full
):
    async def fail(**_kwargs):
        if disk_full:
            raise OSError(errno.ENOSPC, "Disk full")
        raise RuntimeError("Provider unavailable")

    if stage == "resurrection":
        monkeypatch.setattr("tweetnook.enrichment.resurrection.resurrect_due_tweets", fail)
    else:
        monkeypatch.setattr(followups_module, f"_run_followup_{stage}", fail)
    config.tagging.enabled = True
    plan = SyncFollowupPlan(
        **{
            key: key == stage
            for key in ("threads", "resurrection", "articles", "media", "unfurl", "tagging")
        }
    )
    path = paths.data_dir / "activity-status.json"
    reporter = PipelineReporter(_console(), "sync", interactive=False, state_path=path)
    expected = pytest.raises(StorageExhaustedError) if disk_full else nullcontext()
    with expected, reporter:
        plan_sync_pipeline(reporter, config=config, collections=[], followups=plan, head_only=False)
        await followups_module.run_auto_followups(
            plan=plan,
            config=config,
            paths=paths,
            auth_bundle=auth_bundle,
            transport=None,
            console=_console(),
            sleep=asyncio.sleep,
        )
    snapshot = json.loads(path.read_text())
    assert snapshot["success"] is not disk_full
    result = mark_run_process_result(paths.data_dir, reporter.run_id, exit_code=int(disk_full))
    assert result["status"] == ("failed" if disk_full else "completed")
    assert read_run(paths.data_dir, reporter.run_id)["snapshot"]["success"] is not disk_full
    if not disk_full:
        step = next(
            step
            for step in snapshot["steps"]
            if step["key"] == {"unfurl": "urls"}.get(stage, stage)
        )
        assert step["state"] == "failed"
        assert step["recoverable"] is True
        assert "Provider unavailable" in step["reason"]
        assert snapshot["issues"]


@pytest.mark.asyncio
async def test_run_auto_followups_continues_after_task_failure(
    paths,
    config: AppConfig,
    auth_bundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    buffer = StringIO()
    console = Console(file=buffer, force_terminal=False, color_system=None)
    calls: list[str] = []

    async def fail_resurrection(**kwargs):
        calls.append("resurrection")
        assert kwargs["budget"] == 200
        raise RuntimeError("boom")

    async def ok_threads(**_kwargs):
        calls.append("threads")
        return ThreadExpandResult(processed=1, expanded=1)

    async def ok_articles(**_kwargs):
        calls.append("articles")
        return ArticleRefreshResult(processed=1, updated=1)

    async def ok_media(**_kwargs):
        calls.append("media")
        return MediaDownloadResult(processed=1, downloaded=1)

    async def ok_unfurl(**_kwargs):
        calls.append("unfurl")
        return UrlUnfurlResult(processed=1, updated=1)

    import tweetnook.enrichment.resurrection as resurrection_module

    monkeypatch.setattr(resurrection_module, "resurrect_due_tweets", fail_resurrection)
    monkeypatch.setattr(followups_module, "_run_followup_threads", ok_threads)
    monkeypatch.setattr(followups_module, "_run_followup_articles", ok_articles)
    monkeypatch.setattr(followups_module, "_run_followup_media", ok_media)
    monkeypatch.setattr(followups_module, "_run_followup_unfurl", ok_unfurl)

    await followups_module.run_auto_followups(
        plan=SyncFollowupPlan(),
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=None,
        console=console,
        sleep=lambda _: asyncio.sleep(0),
    )

    assert calls == ["threads", "resurrection", "articles", "media", "unfurl"]
    output = buffer.getvalue()
    assert "archive enrich" not in output
    assert "sync follow-up: running resurrection checks" in output
    assert "sync follow-up resurrection failed" in output
    assert "sync follow-up: threads: 1 processed, 1 expanded, 0 skipped, 0 failed" in output
    assert "sync follow-up: articles: 1 processed, 1 refreshed, 0 failed" in output
    assert "sync follow-up: media: 1 processed, 1 downloaded, 0 failed, 0 avatars" in output
    assert "sync follow-up: unfurl: 1 processed, 1 updated, 0 failed" in output


@pytest.mark.asyncio
async def test_sync_remains_successful_when_automated_tagging_followup_fails(
    paths,
    config: AppConfig,
    auth_bundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_tagging(**_kwargs):
        raise RuntimeError("Gemini unavailable")

    monkeypatch.setattr(followups_module, "_run_followup_tagging", fail_tagging)
    enabled_tagging = config.tagging.model_copy(update={"enabled": True, "api_key": "test-key"})
    active_config = config.model_copy(update={"tagging": enabled_tagging})
    buffer = StringIO()
    console = Console(file=buffer, force_terminal=False, color_system=None)

    await followups_module.run_auto_followups(
        plan=SyncFollowupPlan(
            resurrection=False,
            articles=False,
            media=False,
            unfurl=False,
            threads=False,
            tagging=True,
        ),
        config=active_config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=None,
        console=console,
        sleep=lambda _: asyncio.sleep(0),
    )

    assert "sync follow-up tagging failed" in buffer.getvalue()
    assert "Gemini unavailable" in buffer.getvalue()


@pytest.mark.asyncio
async def test_run_auto_followups_only_reminds_about_incomplete_initial_enrichment(
    paths, config: AppConfig, auth_bundle
) -> None:
    store = open_archive_store(paths, create=True, config=config)
    assert store is not None
    store._merge_records(
        [
            store._record(
                row_key="tweet_object:pending",
                record_type="tweet_object",
                tweet_id="pending",
                enrichment_state="pending",
            )
        ]
    )
    store.close()
    buffer = StringIO()
    console = Console(file=buffer, force_terminal=False, color_system=None)

    await followups_module.run_auto_followups(
        plan=SyncFollowupPlan(enabled=False),
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=None,
        console=console,
        sleep=lambda _: asyncio.sleep(0),
    )

    output = buffer.getvalue()
    assert "Archive enrichment is incomplete: 1 tweet remains." in output
    assert "tweetnook import enrich" in output
    assert "running archive enrich" not in output


@pytest.mark.asyncio
async def test_run_auto_followups_does_not_add_archive_queue_note_to_pipeline(
    paths, config: AppConfig, auth_bundle
) -> None:
    buffer = StringIO()
    console = Console(file=buffer, force_terminal=False, color_system=None)
    reporter = PipelineReporter(console, "sync", interactive=False)

    with reporter:
        await followups_module.run_auto_followups(
            plan=SyncFollowupPlan(enabled=False),
            config=config,
            paths=paths,
            auth_bundle=auth_bundle,
            transport=None,
            console=console,
            sleep=lambda _: asyncio.sleep(0),
        )

    assert not reporter.has_final_note
    assert "Archive enrichment queue" not in buffer.getvalue()
