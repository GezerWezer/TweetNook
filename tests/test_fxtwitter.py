from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from io import StringIO

import httpx
import pytest
from rich.console import Console

import tweetnook.archive_import as archive_import
from tests.conftest import make_tweet_detail_response, make_tweet_result, request_details
from tweetnook.auth import ResolvedAuthBundle
from tweetnook.client import fxtwitter
from tweetnook.client.timelines import TimelineTweet
from tweetnook.config import AppConfig, CheckConfig, ScheduleConfig, XDGPaths
from tweetnook.extractor import extract_secondary_objects
from tweetnook.job_supervisor import JobConflictError
from tweetnook.scheduler import CHECK_CONFLICT_RETRY_SECONDS, ScheduleManager
from tweetnook.storage import open_archive_store
from tweetnook.storage.backend import ARCHIVE_SOURCE, FX_SOURCE, LIVE_SOURCE


def fx_status(
    tweet_id: str,
    text: str = "hello",
    *,
    author_id: str = "100",
    screen_name: str = "alice",
    quote: dict | None = None,
    note: str | None = None,
    media: list[dict] | None = None,
    facets: list[dict] | None = None,
) -> dict:
    status = {
        "type": "status",
        "id": tweet_id,
        "url": f"https://x.com/{screen_name}/status/{tweet_id}",
        "text": text,
        "raw_text": {"text": text, "display_text_range": [0, len(text)], "facets": facets or []},
        "author": {
            "type": "profile",
            "id": author_id,
            "screen_name": screen_name,
            "name": screen_name.title(),
            "avatar_url": f"https://pbs.twimg.com/profile_images/{author_id}/a_200x200.jpg",
            "verification": {"verified": False},
        },
        "replies": 1,
        "reposts": 2,
        "likes": 3,
        "quotes": 0,
        "bookmarks": 4,
        "views": 50,
        "created_at": "Sat Mar 14 00:00:00 +0000 2026",
        "created_timestamp": 1773446400,
        "lang": "en",
        "is_note_tweet": False,
        "possibly_sensitive": False,
        "community_note": {"text": note, "facets": []} if note else None,
        "replying_to": None,
        "provider": "twitter",
    }
    if quote is not None:
        status["quote"] = quote
    if media is not None:
        status["media"] = {"all": media}
    return status


def fx_transport(responses: dict[str, tuple[int, dict]]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        tweet_id = request.url.path.rsplit("/", 1)[-1]
        status, body = responses.get(tweet_id, (404, {"code": 404, "message": "NOT_FOUND"}))
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


def ok(status: dict) -> tuple[int, dict]:
    return 200, {"code": 200, "message": "OK", "status": status}


async def _no_sleep(_seconds: float) -> None:
    return None


def test_classify_fx_response_outcomes() -> None:
    available = fxtwitter.classify_fx_response("1", 200, {"status": fx_status("1")})
    tombstone = fxtwitter.classify_fx_response(
        "2",
        200,
        {"status": {"type": "tombstone", "reason": "suspended", "id": "2"}},
    )
    missing = fxtwitter.classify_fx_response("3", 404, {"code": 404, "message": "NOT_FOUND"})
    protected = fxtwitter.classify_fx_response("4", 401, {"code": 401})
    broken = fxtwitter.classify_fx_response("5", 418, {"code": 418})

    assert available.kind == "available" and available.status["id"] == "1"
    assert (tombstone.kind, tombstone.reason) == ("tombstone", "suspended")
    assert missing.kind == "not_found"
    assert protected.kind == "protected"
    assert broken.kind == "error"


def test_fetch_fx_status_retries_rate_limits_and_server_errors() -> None:
    calls: list[str] = []
    replies = iter(
        [
            httpx.Response(429, headers={"retry-after": "3"}, json={"code": 429}),
            httpx.Response(502, json={"code": 502}),
            httpx.Response(200, json={"code": 200, "status": fx_status("9")}),
        ]
    )
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return next(replies)

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def run() -> fxtwitter.FxLookup:
        async with httpx.AsyncClient(
            base_url="https://fx.test", transport=httpx.MockTransport(handler)
        ) as client:
            return await fxtwitter.fetch_fx_status(client, "9", sleep=sleep)

    lookup = asyncio.run(run())

    assert lookup.kind == "available"
    assert calls == ["/2/status/9"] * 3
    assert sleeps == [3.0, 2.0]


def test_fetch_fx_status_caps_retry_after_and_skips_the_last_wait() -> None:
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def run() -> fxtwitter.FxLookup:
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(429, headers={"retry-after": "3600"}, json={})
        )
        async with httpx.AsyncClient(base_url="https://fx.test", transport=transport) as client:
            return await fxtwitter.fetch_fx_status(client, "9", sleep=sleep)

    lookup = asyncio.run(run())

    assert lookup.kind == "error"
    assert sleeps == [fxtwitter.FX_MAX_RETRY_AFTER_SECONDS] * (fxtwitter.FX_RETRY_ATTEMPTS - 1)


def test_fx_is_off_for_replay_installs() -> None:
    config = AppConfig()
    assert fxtwitter.fx_enabled(config)
    config.replay.url = "http://replay.test"
    assert not fxtwitter.fx_enabled(config)
    config.replay.url = None
    config.fxtwitter.enabled = False
    assert not fxtwitter.fx_enabled(config)


def test_rebuilt_status_feeds_the_extractor_like_graphql() -> None:
    quoted = fx_status("20", "quoted post", author_id="200", screen_name="bob")
    status = fx_status(
        "10",
        "look https://t.co/abc https://t.co/pic",
        quote=quoted,
        note="Readers say otherwise",
        facets=[
            {
                "type": "url",
                "indices": [5, 22],
                "original": "https://t.co/abc",
                "replacement": "https://example.com/story",
                "display": "example.com/story",
            },
            {
                "type": "media",
                "indices": [23, 39],
                "id": "555",
                "original": "https://t.co/pic",
                "replacement": "https://x.com/alice/status/10/video/1",
                "display": "pic.x.com/pic",
            },
        ],
        media=[
            {
                "type": "video",
                "id": "555",
                "url": "https://video.twimg.com/v.mp4",
                "thumbnail_url": "https://pbs.twimg.com/thumb.jpg",
                "width": 1280,
                "height": 720,
                "duration": 12.5,
                "formats": [
                    {"url": "https://video.twimg.com/v.m3u8", "container": "m3u8"},
                    {
                        "url": "https://video.twimg.com/v.mp4",
                        "bitrate": 2176000,
                        "container": "mp4",
                    },
                ],
            }
        ],
    )

    result = fxtwitter.fx_status_to_graphql(status)
    graph = extract_secondary_objects(result)

    tweet = graph.tweet_objects["10"]
    assert (tweet.author_id, tweet.author_username, tweet.author_display_name) == (
        "100",
        "alice",
        "Alice",
    )
    assert tweet.created_at == "Sat Mar 14 00:00:00 +0000 2026"
    assert "20" in graph.tweet_objects
    assert ("10", "quote_of", "20") in graph.relations
    media = graph.media[("10", "13_555")]
    assert (media.media_type, media.width, media.height, media.duration_millis) == (
        "video",
        1280,
        720,
        12500,
    )
    assert {variant["url"] for variant in media.variants} == {
        "https://video.twimg.com/v.m3u8",
        "https://video.twimg.com/v.mp4",
    }
    assert [ref.expanded_url for ref in graph.url_refs.values()] == ["https://example.com/story"]
    assert result["birdwatch_pivot"]["subtitle"]["text"] == "Readers say otherwise"
    assert result["legacy"]["favorite_count"] == 3


def _seed_live_tweet(store, tweet_id: str, text: str) -> None:
    tweet_result = make_tweet_result(tweet_id, text)
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={},
        tweets=[
            TimelineTweet(
                tweet_id=tweet_id,
                text=text,
                author_id="100",
                author_username="user100",
                author_display_name="User 100",
                created_at="Sat Mar 14 00:00:00 +0000 2026",
                sort_index="1",
                raw_json=tweet_result,
            )
        ],
        last_head_tweet_id=tweet_id,
        backfill_cursor=None,
        backfill_incomplete=False,
    )


def test_fx_placeholder_never_replaces_live_data_but_replaces_archive_rows(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    _seed_live_tweet(store, "1", "live text")
    buffer = archive_import._PageBuffer()
    store._queue_record(
        store._tweet_object_record(
            archive_import._PlaceholderTweetObject(tweet_id="2", text="archive text"),
            source=ARCHIVE_SOURCE,
            enrichment_state="pending",
            cursor=buffer,
        ),
        cursor=buffer,
    )
    archive_import._flush_buffer(store, buffer)

    for tweet_id in ("1", "2"):
        store.persist_fx_status(
            tweet=fxtwitter.fx_timeline_tweet(
                fxtwitter.fx_status_to_graphql(fx_status(tweet_id, "fx text"))
            ),
            payload={"code": 200, "status": fx_status(tweet_id, "fx text")},
        )

    live = store._get_row("tweet_object:1")
    archived = store._get_row("tweet_object:2")
    membership = store._get_row("tweet:bookmark:none:1") or next(
        row for row in store._query(expr="record_type = 'tweet'") if row["tweet_id"] == "1"
    )
    assert (live["source"], live["text"]) == (LIVE_SOURCE, "live text")
    assert (membership["source"], membership["text"]) == (LIVE_SOURCE, "live text")
    assert (archived["source"], archived["text"], archived["enrichment_state"]) == (
        FX_SOURCE,
        "fx text",
        "done",
    )
    captures = store._query(expr="record_type = 'raw_capture' AND operation = 'FxTwitterStatus'")
    assert {row["cursor_in"] for row in captures} == {"1", "2"}
    store.close()


def test_community_note_versions_are_added_changed_removed_and_restored(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    assert store.record_community_note("1", None, seen_at="2026-01-01") is None
    assert store.record_community_note("1", {"text": "v1"}, seen_at="2026-01-01") == "added"
    assert store.record_community_note("1", {"text": "v1"}, seen_at="2026-01-02") is None
    assert store.record_community_note("1", {"text": "v2"}, seen_at="2026-01-03") == "changed"
    assert store.record_community_note("1", None, seen_at="2026-01-04") == "removed"
    assert store.record_community_note("1", {"text": "v1"}, seen_at="2026-01-05") == "added"

    notes = {row["text"]: row for row in store.list_community_notes("1")}
    assert notes["v1"]["first_seen_at"] == "2026-01-01"
    assert notes["v1"]["last_seen_at"] == "2026-01-05"
    assert notes["v1"]["removed_at"] is None
    assert (notes["v2"]["first_seen_at"], notes["v2"]["removed_at"]) == (
        "2026-01-03",
        "2026-01-04",
    )
    store.close()


def _run_enrichment(
    paths, monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport, tweet_ids
) -> tuple[archive_import.ArchiveEnrichResult, list[str]]:
    store = open_archive_store(paths, create=True)
    assert store is not None
    buffer = archive_import._PageBuffer()
    for tweet_id in tweet_ids:
        store._queue_record(
            store._tweet_object_record(
                archive_import._PlaceholderTweetObject(tweet_id=tweet_id, text="placeholder"),
                source=ARCHIVE_SOURCE,
                enrichment_state="pending",
                cursor=buffer,
            ),
            cursor=buffer,
        )
    archive_import._flush_buffer(store, buffer)
    store.close()

    @asynccontextmanager
    async def fake_locked_archive_job(*, config=None, paths=None, console=None):
        job_store = open_archive_store(paths, create=False)

        class _Job:
            store = job_store

            def mark_dirty(self, rows: int = 1, batches: int = 1) -> None:
                return None

        try:
            yield _Job()
        finally:
            job_store.close()

    async def fake_resolve_query_ids(*_args, **_kwargs):
        return {"TweetDetail": "detail-query-id"}

    detail_requests: list[str] = []

    async def fake_fetch_page(*args, **_kwargs):
        tweet_id = request_details(args[1])[1]["focalTweetId"]
        detail_requests.append(tweet_id)
        payload = make_tweet_detail_response([make_tweet_result(tweet_id, "from login")])
        return httpx.Response(200, json=payload, request=httpx.Request("GET", args[1]))

    class DummyClient:
        async def aclose(self) -> None:
            return None

    real_build = fxtwitter.build_fx_client
    monkeypatch.setattr(
        fxtwitter, "build_fx_client", lambda config, **_: real_build(config, transport=transport)
    )
    monkeypatch.setattr(archive_import, "locked_archive_job", fake_locked_archive_job)
    monkeypatch.setattr(archive_import, "resolve_query_ids", fake_resolve_query_ids)
    monkeypatch.setattr(archive_import, "build_async_client", lambda *_a, **_k: DummyClient())
    monkeypatch.setattr(archive_import, "fetch_page", fake_fetch_page)

    result = asyncio.run(
        archive_import._enrich_pending_rows(
            limit=None,
            config=AppConfig(),
            paths=paths,
            auth_bundle=ResolvedAuthBundle(
                auth_token="t",
                ct0="c",
                user_id="42",
                auth_token_source="test",
                ct0_source="test",
                user_id_source="test",
            ),
            transport=None,
            console=Console(file=StringIO()),
        )
    )
    return result, detail_requests


def test_enrichment_uses_fxtwitter_first_and_tweetdetail_for_the_rest(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = fx_transport({"1": ok(fx_status("1", "from fxtwitter")), "2": (401, {})})

    result, detail_requests = _run_enrichment(paths, monkeypatch, transport, ("1", "2"))

    assert result.completed == 2
    assert detail_requests == ["2"]
    store = open_archive_store(paths, create=False)
    assert store is not None
    fx_row = store._get_row("tweet_object:1")
    login_row = store._get_row("tweet_object:2")
    assert (fx_row["source"], fx_row["text"], fx_row["enrichment_state"]) == (
        FX_SOURCE,
        "from fxtwitter",
        "done",
    )
    assert (login_row["source"], login_row["text"]) == (LIVE_SOURCE, "from login")
    assert json.loads(fx_row["raw_json"])["fxtwitter"]["source"] == "fxtwitter"
    store.close()


def test_enrichment_stops_asking_fxtwitter_once_it_is_down(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx_requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        fx_requests.append(request.url.path)
        await asyncio.sleep(0.01)
        return httpx.Response(418, json={})

    ids = [str(index) for index in range(1, 41)]
    result, detail_requests = _run_enrichment(paths, monkeypatch, httpx.MockTransport(handler), ids)

    assert result.completed == len(ids)
    # The batch stops after a few failures in a row instead of waiting out all 40.
    assert len(fx_requests) < 20
    assert sorted(detail_requests, key=int) == ids


class _Supervisor:
    def __init__(self, config: AppConfig, *, busy: bool = False) -> None:
        self.config = config
        self.busy = busy
        self.started: list[dict] = []
        self.active_run_id = None
        self.active_kind = None

    def start(self, **kwargs):
        if self.busy:
            raise JobConflictError("busy")
        self.started.append(kwargs)
        return {"run_id": f"run-{len(self.started)}"}


def _paths(tmp_path) -> XDGPaths:
    return XDGPaths(config_dir=tmp_path / "config", data_dir=tmp_path, cache_dir=tmp_path / "c")


def test_scheduler_runs_the_check_without_a_sync_schedule(tmp_path) -> None:
    config = AppConfig(
        schedule=ScheduleConfig(enabled=False, timezone="UTC"),
        check=CheckConfig(enabled=True, cadence="daily", time="04:30"),
    )
    supervisor = _Supervisor(config)
    manager = ScheduleManager(_paths(tmp_path), config, supervisor)
    manager._state["check_next_run_at"] = 1_000.0

    manager.tick(now=1_001.0)

    assert [call["cli_args"] for call in supervisor.started] == [["check"]]
    assert supervisor.started[0]["kind"] == "check"
    assert manager._state["check_last_result"] == "started"
    # 1_001 is 1970-01-01T00:16:41Z, so the next check is that day at 04:30 UTC.
    assert manager._state["check_next_run_at"] == 4 * 3600 + 30 * 60


def test_scheduler_defers_the_check_while_another_command_runs(tmp_path) -> None:
    config = AppConfig(schedule=ScheduleConfig(timezone="UTC"), check=CheckConfig(enabled=True))
    manager = ScheduleManager(_paths(tmp_path), config, _Supervisor(config, busy=True))
    manager._state["check_next_run_at"] = 1_000.0

    manager.tick(now=1_001.0)

    assert manager._state["check_last_result"] == "deferred: another command was running"
    assert manager._state["check_next_run_at"] == 1_001.0 + CHECK_CONFLICT_RETRY_SECONDS


@pytest.mark.parametrize(
    ("cadence", "weekday", "day", "expected"),
    [
        ("daily", 0, 1, "2026-02-03T04:00:00+00:00"),
        ("weekly", 0, 1, "2026-02-09T04:00:00+00:00"),
        ("weekly", 4, 1, "2026-02-06T04:00:00+00:00"),
        ("monthly", 0, 31, "2026-02-28T04:00:00+00:00"),
    ],
)
def test_check_reschedules_at_its_configured_frequency(
    tmp_path, cadence, weekday, day, expected
) -> None:
    config = AppConfig(
        schedule=ScheduleConfig(timezone="UTC"),
        check=CheckConfig(enabled=True, cadence=cadence, weekday=weekday, day_of_month=day),
    )
    supervisor = _Supervisor(config)
    manager = ScheduleManager(_paths(tmp_path), config, supervisor)
    due = datetime(2026, 2, 2, 4, tzinfo=UTC).timestamp()
    manager._state["check_next_run_at"] = due
    manager.tick(now=due)
    assert len(supervisor.started) == 1
    assert manager._state["check_next_run_at"] == datetime.fromisoformat(expected).timestamp()


def test_check_is_off_by_default_and_never_scheduled(tmp_path) -> None:
    config = AppConfig()
    assert config.check.enabled is False
    supervisor = _Supervisor(config)
    manager = ScheduleManager(_paths(tmp_path), config, supervisor)

    manager.tick(now=10**10)

    assert manager._state["check_next_run_at"] is None
    assert supervisor.started == []


def test_sync_retries_unavailable_tweets_only_while_the_check_cannot() -> None:
    from io import StringIO

    from rich.console import Console

    from tweetnook.pipeline import PipelineReporter
    from tweetnook.sync import SyncFollowupPlan, check_owns_resurrection, plan_sync_pipeline

    def steps(config: AppConfig) -> list[str]:
        reporter = PipelineReporter(Console(file=StringIO()), "sync", interactive=False)
        plan_sync_pipeline(
            reporter,
            config=config,
            collections=[],
            followups=SyncFollowupPlan(threads=False, articles=False, media=False, unfurl=False),
            head_only=False,
        )
        return [step.key for step in reporter.steps]

    config = AppConfig()
    assert not check_owns_resurrection(config)
    assert "resurrection" in steps(config)
    config.check.enabled = True
    assert check_owns_resurrection(config)
    assert "resurrection" not in steps(config)
    config.fxtwitter.enabled = False
    assert not check_owns_resurrection(config)
    assert "resurrection" in steps(config)


def test_scheduled_sync_waits_for_a_running_check_instead_of_skipping(tmp_path) -> None:
    config = AppConfig(schedule=ScheduleConfig(enabled=True, timezone="UTC"))
    supervisor = _Supervisor(config, busy=True)
    supervisor.active_kind = "check"
    conflicts: list[dict] = []
    manager = ScheduleManager(
        _paths(tmp_path), config, supervisor, on_job_conflict=conflicts.append
    )
    manager._state["next_run_at"] = 1_000.0

    manager.tick(now=1_001.0)

    assert manager._state["last_result"] == "deferred: waiting for the availability check"
    assert manager._state["next_run_at"] == 1_001.0 + CHECK_CONFLICT_RETRY_SECONDS
    assert conflicts == []

    supervisor.active_kind = "sync"
    manager.tick(now=manager._state["next_run_at"])

    assert manager._state["last_result"] == "skipped: another command was running"
    assert len(conflicts) == 1


def test_check_is_not_scheduled_while_fxtwitter_is_off(tmp_path) -> None:
    config = AppConfig(schedule=ScheduleConfig(timezone="UTC"), check=CheckConfig(enabled=True))
    config.fxtwitter.enabled = False
    supervisor = _Supervisor(config)
    manager = ScheduleManager(_paths(tmp_path), config, supervisor)

    manager.tick(now=10**10)

    assert manager._state["check_next_run_at"] is None
    assert supervisor.started == []
