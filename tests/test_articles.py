from __future__ import annotations

import json
import time
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from rich.console import Console
from typer.testing import CliRunner

from tests.conftest import (
    list_article_rows,
    make_article_result,
    make_tweet_detail_response,
    make_tweet_result,
)
from tweetnook import cli
from tweetnook.config import AppConfig, AuthConfig, SyncConfig
from tweetnook.core.exceptions import (
    AuthExpiredError,
    ConfigError,
    FeatureFlagDriftError,
    RepeatedFocalAbsenceError,
)
from tweetnook.core.timeline_parsing import TimelineTweet
from tweetnook.enrichment.articles import normalize_article_target, refresh_articles
from tweetnook.jobs.pipeline import PipelineReporter
from tweetnook.query_ids import QueryIdStore
from tweetnook.storage import open_archive_store

TWEET_ID = "2026531440414925307"


def _fixture_payload() -> dict[str, object]:
    fixture = Path(__file__).parent / "fixtures" / "dimitris_article_tweet_detail.json"
    return json.loads(fixture.read_text(encoding="utf-8"))


def _preview_tweet(tweet_id: str, *, plain_text: str | None = None) -> TimelineTweet:
    return TimelineTweet(
        tweet_id=tweet_id,
        text="preview tweet",
        author_id="1000",
        author_username="user1000",
        author_display_name="User 1000",
        created_at="Sat Mar 14 00:00:00 +0000 2026",
        sort_index="10",
        raw_json=make_tweet_result(
            tweet_id,
            "preview tweet",
            user_id="1000",
            article=make_article_result(
                f"preview-article-{tweet_id}",
                title="Preview title",
                preview_text="Preview only",
                plain_text=plain_text,
            ),
        ),
    )


def _seed_preview_article(paths, *tweet_ids: str) -> None:
    tweets = [_preview_tweet(tweet_id) for tweet_id in (tweet_ids or (TWEET_ID,))]
    store = open_archive_store(paths, create=True)
    assert store is not None
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=tweets,
        last_head_tweet_id=tweets[-1].tweet_id,
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.close()


def _article_detail_payload(tweet_id: str, *, plain_text: str = "Body") -> dict[str, object]:
    return make_tweet_detail_response(
        [
            make_tweet_result(
                tweet_id,
                "detail tweet",
                user_id="1000",
                article=make_article_result(
                    f"detail-article-{tweet_id}",
                    title=f"Title {tweet_id}",
                    preview_text="Preview only",
                    plain_text=plain_text,
                    url=f"https://x.com/i/article/{tweet_id}",
                ),
            )
        ]
    )


@pytest.mark.parametrize(
    "target",
    ["https://example.com/status/100", "https://x.com/user/status/123abc", "/status/100"],
)
def test_normalize_article_target_rejects_other_hosts_and_partial_ids(target: str) -> None:
    with pytest.raises(ConfigError, match="Unsupported article target"):
        normalize_article_target(target)


def test_normalize_article_target_keeps_query_suffixes() -> None:
    assert normalize_article_target("https://twitter.com/u/status/100?s=20") == "100"


def test_normalize_article_target_accepts_ids_and_urls() -> None:
    assert normalize_article_target(TWEET_ID) == TWEET_ID
    assert normalize_article_target(f"https://x.com/dimitrispapail/status/{TWEET_ID}") == TWEET_ID


@pytest.mark.asyncio
async def test_refresh_articles_marks_planned_step_skipped_when_queue_is_empty(
    paths,
    config,
    auth_bundle,
) -> None:
    store = open_archive_store(paths, create=True, config=config)
    assert store is not None
    store.close()
    console = Console(file=StringIO(), force_terminal=False, color_system=None)
    reporter = PipelineReporter(console, "articles", interactive=False)

    with reporter:
        result = await refresh_articles(
            config=config,
            paths=paths,
            auth_bundle=auth_bundle,
            console=console,
        )

    assert result.processed == 0
    assert reporter._step_by_key["articles"].state == "skipped"
    assert "no article rows" in reporter._step_by_key["articles"].summary


@pytest.mark.asyncio
async def test_refresh_articles_updates_preview_rows_from_tweet_detail(
    paths,
    config,
    auth_bundle,
) -> None:
    _seed_preview_article(paths)
    QueryIdStore(paths).save({"TweetDetail": "detail-qid"})
    payload = _fixture_payload()

    def handler(request: httpx.Request) -> httpx.Response:
        assert "/TweetDetail" in str(request.url)
        return httpx.Response(200, json=payload)

    result = await refresh_articles(
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(handler),
    )

    assert result.processed == 1
    assert result.updated == 1
    assert result.failed == 0

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        article_rows = list_article_rows(store, preview_only=False)
        assert len(article_rows) == 1
        article = article_rows[0]
        assert article["status"] == "body_present"
        assert article["title"] == "You Don't Need to Run Every Eval"
        assert len(article["content_text"]) == 17308
        media_rows = store.list_media_rows()
        assert len(media_rows) == 10
        assert any(row["source"] == "article_cover" for row in media_rows)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_refresh_articles_marks_missing_focal_tweet_as_failure(
    paths,
    config,
    auth_bundle,
) -> None:
    _seed_preview_article(paths)
    QueryIdStore(paths).save({"TweetDetail": "detail-qid"})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_article_detail_payload("999", plain_text="Wrong tweet"),
            request=request,
        )

    result = await refresh_articles(
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(handler),
    )

    assert result.processed == 1
    assert result.updated == 0
    assert result.failed == 1

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        article_rows = list_article_rows(store, preview_only=True)
        assert len(article_rows) == 1
        assert article_rows[0]["status"] == "preview_only"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_refresh_articles_respects_limit(paths, config, auth_bundle) -> None:
    _seed_preview_article(paths, "100", "101")
    QueryIdStore(paths).save({"TweetDetail": "detail-qid"})
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        variables = request.url.params["variables"]
        if '"focalTweetId":"100"' in variables:
            requests.append("100")
            return httpx.Response(
                200,
                json=_article_detail_payload("100", plain_text="Body 100"),
                request=request,
            )
        raise AssertionError(f"unexpected request {request.url}")

    result = await refresh_articles(
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        limit=1,
        transport=httpx.MockTransport(handler),
    )

    assert result.processed == 1
    assert result.updated == 1
    assert result.failed == 0
    assert requests == ["100"]

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        preview_rows = list_article_rows(store, preview_only=True)
        all_rows = list_article_rows(store, preview_only=False)
        assert [row["tweet_id"] for row in preview_rows] == ["101"]
        assert {row["tweet_id"]: row["status"] for row in all_rows} == {
            "100": "body_present",
            "101": "preview_only",
        }
    finally:
        store.close()


@pytest.mark.asyncio
async def test_refresh_articles_interactive_reports_status(paths, config, auth_bundle, monkeypatch):
    _seed_preview_article(paths)
    QueryIdStore(paths).save({"TweetDetail": "detail-qid"})
    buffer = StringIO()
    console = Console(file=buffer, force_terminal=True, color_system=None)
    progress = MagicMock()
    on_progress = MagicMock()
    progress.return_value.__enter__.return_value = on_progress
    monkeypatch.setattr("tweetnook.enrichment.articles.progress_callback", progress)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_fixture_payload(), request=request)

    result = await refresh_articles(
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(handler),
        console=console,
    )

    assert result.updated == 1
    progress.assert_called_once_with(
        console,
        label="articles refresh",
        total=1,
        unit="tweets",
    )
    on_progress.assert_called_once_with(1, 1)
    output = buffer.getvalue()
    assert "articles refresh: refreshing 1 preview-only article rows" in output
    assert "articles refresh: resolving TweetDetail query ID" in output


@pytest.mark.asyncio
async def test_refresh_articles_respects_detail_sleep(paths, auth_bundle) -> None:
    _seed_preview_article(paths, "100", "101")
    QueryIdStore(paths).save({"TweetDetail": "detail-qid"})
    sleep_calls: list[float] = []
    config = AppConfig(
        auth=AuthConfig(auth_token="token", ct0="ct0", user_id="42"),
        sync=SyncConfig(detail_delay=1.0),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        variables = request.url.params["variables"]
        if '"focalTweetId":"100"' in variables:
            return httpx.Response(
                200,
                json=_article_detail_payload("100", plain_text="Body 100"),
                request=request,
            )
        if '"focalTweetId":"101"' in variables:
            return httpx.Response(
                200,
                json=_article_detail_payload("101", plain_text="Body 101"),
                request=request,
            )
        raise AssertionError(f"unexpected request {request.url}")

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    result = await refresh_articles(
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(handler),
        sleep=fake_sleep,
    )

    assert result.processed == 2
    assert result.updated == 2
    assert sleep_calls == [1.0]


@pytest.mark.asyncio
async def test_refresh_articles_adapts_to_rate_limit_headers(paths, auth_bundle) -> None:
    _seed_preview_article(paths, "100", "101")
    QueryIdStore(paths).save({"TweetDetail": "detail-qid"})
    sleep_calls: list[float] = []
    config = AppConfig(
        auth=AuthConfig(auth_token="token", ct0="ct0", user_id="42"),
        sync=SyncConfig(detail_delay=1.0),
    )
    reset_at = str(int(time.time()) + 12)

    def handler(request: httpx.Request) -> httpx.Response:
        variables = request.url.params["variables"]
        headers = {
            "x-rate-limit-limit": "300",
            "x-rate-limit-remaining": "3",
            "x-rate-limit-reset": reset_at,
        }
        if '"focalTweetId":"100"' in variables:
            return httpx.Response(
                200,
                json=_article_detail_payload("100", plain_text="Body 100"),
                headers=headers,
                request=request,
            )
        if '"focalTweetId":"101"' in variables:
            return httpx.Response(
                200,
                json=_article_detail_payload("101", plain_text="Body 101"),
                headers=headers,
                request=request,
            )
        raise AssertionError(f"unexpected request {request.url}")

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    result = await refresh_articles(
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(handler),
        sleep=fake_sleep,
    )

    assert result.processed == 2
    assert result.updated == 2
    assert len(sleep_calls) == 1
    assert sleep_calls[0] > 3.0


def _requested_focal(request: httpx.Request) -> str:
    return json.loads(request.url.params["variables"])["focalTweetId"]


async def _refresh_each(paths, config, auth_bundle, respond) -> tuple[list[str], object]:
    """Refresh every seeded row; return the requests made and the result or raised error."""
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        tweet_id = _requested_focal(request)
        requested.append(tweet_id)
        return respond(tweet_id, request)

    QueryIdStore(paths).save({"TweetDetail": "detail-qid"})
    try:
        outcome = await refresh_articles(
            targets=sorted(_SEEDED_IDS),
            config=config,
            paths=paths,
            auth_bundle=auth_bundle,
            transport=httpx.MockTransport(handler),
        )
    except Exception as exc:
        outcome = exc
    return requested, outcome


_SEEDED_IDS = ("100", "101", "102", "103", "104")


@pytest.mark.parametrize(
    ("status_code", "error"),
    [(401, AuthExpiredError), (400, FeatureFlagDriftError)],
)
@pytest.mark.asyncio
async def test_refresh_articles_stops_the_run_on_a_typed_stop(
    paths, config, auth_bundle, status_code, error
) -> None:
    _seed_preview_article(paths, *_SEEDED_IDS)

    def respond(tweet_id: str, request: httpx.Request) -> httpx.Response:
        if tweet_id == "100":
            return httpx.Response(
                200, json=_article_detail_payload("100", plain_text="Kept"), request=request
            )
        return httpx.Response(status_code, json={}, request=request)

    requested, outcome = await _refresh_each(paths, config, auth_bundle, respond)

    assert isinstance(outcome, error)
    assert requested == ["100", "101"]
    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        # The success before the stop was already committed.
        assert "100" not in {row["tweet_id"] for row in list_article_rows(store, preview_only=True)}
    finally:
        store.close()


@pytest.mark.asyncio
async def test_refresh_articles_stops_after_three_focal_absences(
    paths, config, auth_bundle
) -> None:
    _seed_preview_article(paths, *_SEEDED_IDS)

    def respond(tweet_id: str, request: httpx.Request) -> httpx.Response:
        if tweet_id == "100":
            return httpx.Response(
                200, json=_article_detail_payload("100", plain_text="Kept"), request=request
            )
        return httpx.Response(
            200, json=_article_detail_payload("999", plain_text="Wrong"), request=request
        )

    requested, outcome = await _refresh_each(paths, config, auth_bundle, respond)

    assert isinstance(outcome, RepeatedFocalAbsenceError)
    assert requested == ["100", "101", "102", "103"]


@pytest.mark.asyncio
async def test_refresh_articles_resets_the_absence_count_on_a_saved_or_unavailable_result(
    paths, config, auth_bundle
) -> None:
    _seed_preview_article(paths, *_SEEDED_IDS)

    def respond(tweet_id: str, request: httpx.Request) -> httpx.Response:
        if tweet_id == "102":
            # An explicit answer about the requested post breaks the run of absences.
            payload = {
                "data": {
                    "threaded_conversation_with_injections_v2": {
                        "instructions": [
                            {
                                "entries": [
                                    {
                                        "entryId": "tweet-102",
                                        "content": {
                                            "itemContent": {
                                                "tweet_results": {
                                                    "result": {
                                                        "__typename": "TweetUnavailable",
                                                        "rest_id": "102",
                                                    }
                                                }
                                            }
                                        },
                                    }
                                ]
                            }
                        ]
                    }
                }
            }
            return httpx.Response(200, json=payload, request=request)
        return httpx.Response(
            200, json=_article_detail_payload("999", plain_text="Wrong"), request=request
        )

    requested, outcome = await _refresh_each(paths, config, auth_bundle, respond)

    assert requested == list(_SEEDED_IDS)
    assert (outcome.processed, outcome.updated, outcome.failed) == (5, 0, 5)


@pytest.mark.asyncio
async def test_refresh_articles_keeps_remote_failures_per_item(paths, config, auth_bundle) -> None:
    _seed_preview_article(paths, "100", "101", "102")

    def respond(tweet_id: str, request: httpx.Request) -> httpx.Response:
        if tweet_id == "100":
            return httpx.Response(503, json={}, request=request)
        if tweet_id == "101":
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(
            200, json=_article_detail_payload("102", plain_text="Saved"), request=request
        )

    QueryIdStore(paths).save({"TweetDetail": "detail-qid"})
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(_requested_focal(request))
        return respond(requested[-1], request)

    result = await refresh_articles(
        targets=["100", "101", "102"],
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(handler),
    )

    assert requested == ["100", "101", "102"]
    assert (result.processed, result.updated, result.failed) == (3, 1, 2)


@pytest.mark.asyncio
async def test_refresh_articles_does_not_report_a_local_error_as_a_failed_item(
    paths, config, auth_bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_preview_article(paths, *_SEEDED_IDS)
    from tweetnook.storage.backend import ArchiveStore

    real_persist = ArchiveStore.persist_tweet_detail
    writes: list[str] = []

    def failing_persist(self, *, tweet, **kwargs):
        if tweet.tweet_id == "101":
            raise RuntimeError("local bug")
        writes.append(tweet.tweet_id)
        return real_persist(self, tweet=tweet, **kwargs)

    monkeypatch.setattr(ArchiveStore, "persist_tweet_detail", failing_persist)

    def respond(tweet_id: str, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_article_detail_payload(tweet_id, plain_text="Body"), request=request
        )

    requested, outcome = await _refresh_each(paths, config, auth_bundle, respond)

    assert isinstance(outcome, RuntimeError)
    assert requested == ["100", "101"]
    assert writes == ["100"]


@pytest.mark.asyncio
async def test_refresh_articles_runs_every_named_target_regardless_of_limit(
    paths, config, auth_bundle
) -> None:
    _seed_preview_article(paths, "100", "101", "102")
    QueryIdStore(paths).save({"TweetDetail": "detail-qid"})
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(_requested_focal(request))
        return httpx.Response(
            200, json=_article_detail_payload(requested[-1], plain_text="Saved"), request=request
        )

    result = await refresh_articles(
        targets=["100", "101", "102"],
        limit=1,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(handler),
    )

    assert requested == ["100", "101", "102"]
    assert result.updated == 3


def test_articles_refresh_help_scopes_the_limit_to_automatic_selection() -> None:
    result = CliRunner().invoke(cli.app, ["articles", "refresh", "--help"])

    assert result.exit_code == 0
    # Option help wraps inside Rich's table borders.
    normalized = " ".join(result.stdout.replace("│", " ").split())
    assert "archived article rows to select automatically" in normalized
    assert "Targets you name all run" in normalized
