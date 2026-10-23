from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from io import StringIO
from itertools import product

import httpx
import pytest
from rich.console import Console
from typer.testing import CliRunner

from tests.conftest import make_tweet_result
from tests.test_fxtwitter import fx_status, fx_transport, ok
from tweetnook import cli
from tweetnook.check import FX_NOT_FOUND_DETAIL, MAX_CONSECUTIVE_ERRORS, run_check
from tweetnook.client.timelines import TimelineTweet
from tweetnook.pipeline import PipelineReporter
from tweetnook.storage import open_archive_store
from tweetnook.storage.backend import FX_SOURCE, LIVE_SOURCE


def _save(store, tweet_id: str, text: str, *, quoted: dict | None = None) -> None:
    result = make_tweet_result(tweet_id, text, quoted_tweet=quoted)
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
                sort_index=tweet_id,
                raw_json=result,
            )
        ],
        last_head_tweet_id=tweet_id,
        backfill_cursor=None,
        backfill_incomplete=False,
    )


def _unavailable(
    store, tweet_id: str, reason: str, *, raw_json: str | None = None, detail: str | None = None
) -> dict:
    return store._record(
        row_key=f"tweet_object:{tweet_id}",
        record_type="tweet_object",
        tweet_id=tweet_id,
        author_id="300",
        # Never-captured unavailable tweets hold the live error payload and no text.
        source=LIVE_SOURCE,
        text="old live text" if raw_json else "",
        raw_json=raw_json or json.dumps({"errors": [{"message": "unavailable"}]}),
        enrichment_state="terminal_unavailable",
        enrichment_reason=reason,
        enrichment_detail=detail,
        enrichment_retry_eligible=1,
        enrichment_retry_count=0,
        enrichment_next_retry_at="2030-01-01T00:00:00+00:00",
        enrichment_checked_at="2025-01-01T00:00:00+00:00",
    )


async def _no_sleep(_seconds: float) -> None:
    return None


def _run(config, paths, transport):
    return asyncio.run(
        run_check(
            config=config,
            paths=paths,
            detail_budget=0,
            fx_transport=transport,
            console=Console(file=StringIO()),
            sleep=_no_sleep,
        )
    )


def _history(store, tweet_id: str) -> list[str]:
    return [row["outcome"] for row in store.list_tweet_check_history(tweet_id)]


@pytest.mark.parametrize(("saves", "context", "unavailable"), product((False, True), repeat=3))
@pytest.mark.parametrize("detail_budget", [None, 0])
def test_check_publishes_all_enabled_steps_before_work(
    config, paths, monkeypatch, saves, context, unavailable, detail_budget
) -> None:
    import tweetnook.check as check

    store = open_archive_store(paths, create=True, config=config)
    assert store is not None
    for tweet_id in ("1", "2", "3"):
        _save(store, tweet_id, f"saved {tweet_id}")
    store.close()
    expected = [
        key
        for enabled, key in (
            (saves, "check:saves"),
            (unavailable, "check:unavailable"),
            (context, "check:context"),
            (detail_budget is None, "resurrection"),
        )
        if enabled
    ]
    state_path = paths.data_dir / "activity-status.json"
    locked_job = check.locked_archive_job

    @asynccontextmanager
    async def observe_job(**kwargs):
        snapshot = json.loads(state_path.read_text())
        assert [step["key"] for step in snapshot["steps"]] == expected
        assert all(step["state"] == "pending" for step in snapshot["steps"])
        async with locked_job(**kwargs) as job:
            yield job

    monkeypatch.setattr(check, "locked_archive_job", observe_job)
    requests = []

    def handler(request):
        snapshot = json.loads(state_path.read_text())
        assert [step["key"] for step in snapshot["steps"]] == expected
        assert [step["state"] for step in snapshot["steps"]] == ["active"] + ["pending"] * (
            len(expected) - 1
        )
        assert snapshot["steps"][0]["total"] == 3
        tweet_id = request.url.path.rsplit("/", 1)[-1]
        requests.append(tweet_id)
        status, body = ok(fx_status(tweet_id))
        return httpx.Response(status, json=body)

    console = Console(file=StringIO())
    with PipelineReporter(
        console, "tweetnook check", interactive=False, state_path=state_path
    ) as pipeline:
        asyncio.run(
            run_check(
                config=config,
                paths=paths,
                saves=saves,
                context=context,
                unavailable=unavailable,
                detail_budget=detail_budget,
                fx_transport=httpx.MockTransport(handler),
                console=console,
                sleep=_no_sleep,
            )
        )
    assert [step.key for step in pipeline.steps] == expected
    assert set(requests) == ({"1", "2", "3"} if saves else set())
    assert all(
        step.state == ("complete" if step.key == "check:saves" else "skipped")
        for step in pipeline.steps
    )
    assert all(
        step.reason == "nothing due"
        for step in pipeline.steps
        if step.key.startswith("check:") and step.key != "check:saves"
    )


@pytest.mark.parametrize("expired", [False, True])
def test_check_skips_planned_login_step_when_login_is_unavailable(
    config, paths, monkeypatch, expired
) -> None:
    import tweetnook.auth as auth
    import tweetnook.resurrection as resurrection
    from tweetnook.exceptions import AuthExpiredError, AuthResolutionError

    def missing_login(_config):
        raise AuthResolutionError("No login configured")

    async def expired_login(**_kwargs):
        pipeline.start_step("resurrection", activity="Checking with your login")
        raise AuthExpiredError("Login expired", status_code=401)

    if expired:
        monkeypatch.setattr(resurrection, "resurrect_due_tweets", expired_login)
    else:
        monkeypatch.setattr(auth, "resolve_auth_bundle", missing_login)
    console = Console(file=StringIO())
    with PipelineReporter(console, "tweetnook check", interactive=False) as pipeline:
        result = asyncio.run(
            run_check(
                config=config,
                paths=paths,
                saves=False,
                context=False,
                unavailable=False,
                console=console,
            )
        )
        assert len(pipeline.steps) == 1
        step = pipeline.steps[0]
        assert (step.key, step.state) == ("resurrection", "skipped")
        assert step.reason == (
            "the login having expired" if expired else "login credentials being unavailable"
        )
    assert result.detail is None
    assert len(result.warnings) == 1


def test_check_restores_marks_and_versions_from_fxtwitter(config, paths) -> None:
    store = open_archive_store(paths, create=True, config=config)
    assert store is not None
    _save(store, "1", "quotes two", quoted=make_tweet_result("2", "quoted live", user_id="200"))
    for tweet_id in ("4", "5", "6"):
        _save(store, tweet_id, f"saved {tweet_id}")
    store._merge_records(
        [
            _unavailable(store, "3", "suspended_account"),
            _unavailable(store, "7", "unavailable_unknown"),
            _unavailable(
                store,
                "8",
                "protected_account",
                raw_json=json.dumps(make_tweet_result("8", "old live text")),
            ),
        ]
    )
    store.close()

    quote = fx_status("2", "quoted live", author_id="200")
    responses = {
        "1": ok(fx_status("1", "quotes two", quote=quote, note="Context v1")),
        "3": ok(fx_status("3", "back again", author_id="300")),
        "4": ok({"type": "tombstone", "id": "4", "reason": "suspended"}),
        "5": (404, {"code": 404, "message": "NOT_FOUND"}),
        "6": (401, {"code": 401}),
        "7": (404, {"code": 404}),
        "8": ok(fx_status("8", "fx text", author_id="300")),
    }

    result = _run(config, paths, fx_transport(responses))

    assert result.saves_checked == 4
    assert result.unavailable_checked == 3
    assert result.quotes_checked == 1
    assert result.restored == 2
    # Tweet 5's first 404 only makes it suspect.
    assert (result.became_unavailable, result.suspect) == (1, 1)
    assert result.still_unavailable == 1
    assert result.protected == 1
    assert result.notes_added == 1

    store = open_archive_store(paths, create=False, config=config)
    assert store is not None
    rows = store.get_tweet_object_rows(["2", "3", "4", "5", "6", "7", "8"])
    restored = store._get_row("tweet_object:3")
    assert (restored["source"], restored["text"], restored["enrichment_state"]) == (
        FX_SOURCE,
        "back again",
        "resurrected",
    )
    # A tweet that died after a live capture keeps the richer live copy.
    live = store._get_row("tweet_object:8")
    assert (live["source"], live["text"], live["enrichment_state"]) == (
        LIVE_SOURCE,
        "old live text",
        "resurrected",
    )
    assert (rows["4"]["enrichment_state"], rows["4"]["enrichment_reason"]) == (
        "terminal_unavailable",
        "suspended_account",
    )
    assert rows["5"]["enrichment_state"] == "done"
    assert store.get_tweet_check("5")["outcome"] == "suspect"
    assert _history(store, "5") == []
    assert rows["6"]["enrichment_state"] == "done"
    assert rows["7"]["enrichment_retry_count"] == 0
    assert rows["7"]["enrichment_next_retry_at"] == "2030-01-01T00:00:00+00:00"
    assert rows["2"]["enrichment_state"] == "done"
    assert store.get_tweet_check("2")["scope"] == "quote"
    assert store.get_tweet_check("6")["outcome"] == "protected"
    assert [note["text"] for note in store.list_community_notes("1")] == ["Context v1"]
    store.close()

    responses["1"] = ok(fx_status("1", "quotes two", quote=quote, note="Context v2"))
    second = _run(config, paths, fx_transport(responses))
    responses["1"] = ok(fx_status("1", "quotes two", quote=quote))
    third = _run(config, paths, fx_transport(responses))

    assert (second.notes_changed, third.notes_removed) == (1, 1)
    # A second 404 in a row marks tweet 5, due for a login check that night.
    assert (second.became_unavailable, second.suspect) == (1, 0)
    store = open_archive_store(paths, create=False, config=config)
    assert store is not None
    five = store.get_tweet_object_rows(["5"])["5"]
    assert (five["enrichment_state"], five["enrichment_reason"]) == (
        "terminal_unavailable",
        "unavailable_unknown",
    )
    # Marked from FxTwitter's 404s alone, whatever their message: the third night's 404
    # leaves it due for the login lookup that never ran (the budget here is 0).
    assert five["enrichment_detail"] == FX_NOT_FOUND_DETAIL
    assert five["enrichment_retry_count"] == 0
    assert five["enrichment_next_retry_at"] <= store.get_tweet_check("5")["checked_at"]
    assert _history(store, "5") == ["not_found"]
    assert store.get_tweet_check("5")["outcome"] == "not_found"
    notes = store.list_community_notes("1")
    assert [note["text"] for note in notes] == ["Context v1", "Context v2"]
    assert all(note["removed_at"] for note in notes)
    store.close()


def test_check_rolls_through_context_tweets(config, paths) -> None:
    config.check.context_enabled = True
    config.check.cadence = "daily"
    config.check.context_days = 2
    store = open_archive_store(paths, create=True, config=config)
    assert store is not None
    _save(store, "1", "saved", quoted=make_tweet_result("2", "quoted", user_id="200"))
    for tweet_id in ("10", "11", "12"):
        _save(store, tweet_id, f"context {tweet_id}")
    # Drop their bookmark rows so they remain only as context tweet objects.
    store.conn.execute(
        "DELETE FROM archive WHERE record_type = 'tweet' AND tweet_id IN ('10', '11', '12')"
    )
    store.conn.commit()
    assert store.count_context_tweets() == 4
    store.close()
    responses = {tweet_id: ok(fx_status(tweet_id)) for tweet_id in ("1", "2", "10", "11", "12")}

    first = _run(config, paths, fx_transport(responses))
    second = _run(config, paths, fx_transport(responses))

    # Half of the four context tweets each night, never-checked first.
    assert (first.context_checked, second.context_checked) == (2, 2)
    store = open_archive_store(paths, create=False, config=config)
    assert store is not None
    assert all(store.get_tweet_check(tweet_id) for tweet_id in ("2", "10", "11", "12"))
    store.close()


@pytest.mark.parametrize(
    ("enabled", "cadence", "expected"),
    [(False, "weekly", 0), (True, "daily", 1), (True, "weekly", 3), (True, "monthly", 11)],
)
def test_check_context_is_opt_in_and_scales_with_frequency(
    config, paths, enabled, cadence, expected
) -> None:
    config.check.context_enabled = enabled
    config.check.cadence = cadence
    store = open_archive_store(paths, create=True, config=config)
    assert store is not None
    ids = [str(100 + index) for index in range(20)]
    for tweet_id in ids:
        _save(store, tweet_id, "context")
    store.conn.execute("DELETE FROM archive WHERE record_type = 'tweet'")
    store.conn.commit()
    store.close()
    requests = []

    def handler(request):
        tweet_id = request.url.path.rsplit("/", 1)[-1]
        requests.append(tweet_id)
        return httpx.Response(200, json=ok(fx_status(tweet_id))[1])

    result = _run(config, paths, httpx.MockTransport(handler))
    assert result.context_checked == expected
    assert len(requests) == expected


def test_check_does_not_run_while_fxtwitter_is_off(config, paths, monkeypatch) -> None:
    import tweetnook.check as check

    config.fxtwitter.enabled = False
    store = open_archive_store(paths, create=True, config=config)
    assert store is not None
    store.close()

    async def no_login_lookups(**_kwargs):
        raise AssertionError("sync owns login retries while FxTwitter is off")

    monkeypatch.setattr(check, "_run_detail_fallback", no_login_lookups)
    console = Console(file=StringIO())
    with PipelineReporter(console, "tweetnook check", interactive=False) as pipeline:
        result = asyncio.run(
            run_check(
                config=config,
                paths=paths,
                detail_budget=50,
                console=console,
            )
        )

    assert pipeline.steps == []
    assert result.checked == 0
    assert result.warnings == ["FxTwitter is off, so the check didn't run."]


def test_mass_404s_mark_nothing_missing(config, paths) -> None:
    store = open_archive_store(paths, create=True, config=config)
    assert store is not None
    ids = [str(100 + index) for index in range(60)]
    for tweet_id in ids:
        _save(store, tweet_id, f"saved {tweet_id}")
    store.ensure_check_tables()
    # Every one 404'd last night too, so each would be a confirmed second 404.
    store.record_tweet_checks([(tweet_id, "suspect", 404, "save", None) for tweet_id in ids])
    store.close()

    result = _run(config, paths, fx_transport({}))

    assert (result.became_unavailable, result.suspect) == (0, 60)
    assert any("none were marked missing" in warning for warning in result.warnings)
    store = open_archive_store(paths, create=False, config=config)
    assert store is not None
    rows = store.get_tweet_object_rows(ids)
    assert {row["enrichment_state"] for row in rows.values()} == {"done"}
    assert {store.get_tweet_check(tweet_id)["outcome"] for tweet_id in ids} == {"suspect"}
    assert all(_history(store, tweet_id) == [] for tweet_id in ids)
    store.close()


def test_mass_404s_undo_nothing_marked_in_earlier_chunks(config, paths, monkeypatch) -> None:
    import tweetnook.check as check

    monkeypatch.setattr(check, "CHECK_CHUNK_SIZE", 10)
    store = open_archive_store(paths, create=True, config=config)
    assert store is not None
    ids = [str(100 + index) for index in range(60)]
    for tweet_id in ids:
        _save(store, tweet_id, f"saved {tweet_id}")
    store.ensure_check_tables()
    # Only five 404'd last night, all in the first chunk; tonight everything 404s.
    store.record_tweet_checks([(tweet_id, "suspect", 404, "save", None) for tweet_id in ids[:5]])
    store.close()

    result = _run(config, paths, fx_transport({}))

    assert result.became_unavailable == 0
    assert any("none were marked missing" in warning for warning in result.warnings)
    store = open_archive_store(paths, create=False, config=config)
    assert store is not None
    rows = store.get_tweet_object_rows(ids[:5])
    assert {row["enrichment_state"] for row in rows.values()} == {"done"}
    store.close()


def test_check_stops_when_fxtwitter_keeps_failing(config, paths, monkeypatch) -> None:
    import tweetnook.check as check

    monkeypatch.setattr(check, "CHECK_CHUNK_SIZE", 10)
    config.check.context_enabled = True
    store = open_archive_store(paths, create=True, config=config)
    assert store is not None
    ids = [str(100 + index) for index in range(40)]
    for tweet_id in ids:
        _save(store, tweet_id, f"saved {tweet_id}")
    store.close()
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(503, json={})

    with PipelineReporter(
        Console(file=StringIO()), "tweetnook check", interactive=False
    ) as pipeline:
        result = _run(config, paths, httpx.MockTransport(handler))
        assert [(step.key, step.state) for step in pipeline.steps] == [
            ("check:saves", "failed"),
            ("check:unavailable", "skipped"),
            ("check:context", "skipped"),
        ]
        assert all(step.reason == "FxTwitter stopped answering" for step in pipeline.steps[1:])

    assert result.errors == MAX_CONSECUTIVE_ERRORS
    assert result.saves_checked == MAX_CONSECUTIVE_ERRORS
    assert any(
        f"failed {MAX_CONSECUTIVE_ERRORS} lookups in a row" in warning
        for warning in result.warnings
    )
    # The chunk after the 25th failure in a row is never requested.
    assert "/2/status/139" not in requests
    store = open_archive_store(paths, create=False, config=config)
    assert store is not None
    # Failed lookups aren't recorded, so every tweet is still due next time.
    assert all(store.get_tweet_check(tweet_id) is None for tweet_id in ids)
    store.close()


def test_each_tweet_gets_one_result_and_quotes_add_no_notes(config, paths) -> None:
    store = open_archive_store(paths, create=True, config=config)
    assert store is not None
    _save(store, "1", "quotes two", quoted=make_tweet_result("2", "two", user_id="200"))
    _save(store, "2", "two")
    _save(store, "4", "quotes three", quoted=make_tweet_result("3", "three", user_id="300"))
    store.close()
    two = fx_status("2", "two", author_id="200", note="Seen through the quote")
    three = fx_status("3", "three", author_id="300", note="Embedded note")
    responses = {
        "1": ok(fx_status("1", "quotes two", quote=two)),
        "2": ok(fx_status("2", "two", author_id="200")),
        "4": ok(fx_status("4", "quotes three", quote=three)),
    }

    result = _run(config, paths, fx_transport(responses))

    # Tweet 2 is saved, so only its own lookup counts; 3 is checked through tweet 4.
    assert result.quotes_checked == 1
    store = open_archive_store(paths, create=False, config=config)
    assert store is not None
    assert store.get_tweet_check("2")["scope"] == "save"
    assert store.get_tweet_check("3")["scope"] == "quote"
    assert store.list_community_notes("2") == []
    assert store.list_community_notes("3") == []
    store.close()


def test_fxtwitter_never_moves_the_login_retry_schedule(config, paths) -> None:
    store = open_archive_store(paths, create=True, config=config)
    assert store is not None
    due = _unavailable(store, "9", "unavailable_unknown", detail="This post is unavailable.")
    due["enrichment_next_retry_at"] = "2020-01-01T00:00:00+00:00"
    due["enrichment_retry_count"] = 2
    store._merge_records(
        [
            _unavailable(store, "7", "unavailable_unknown", detail=FX_NOT_FOUND_DETAIL),
            _unavailable(store, "8", "unavailable_unknown", detail="This post is unavailable."),
            due,
        ]
    )
    store.close()
    responses = {"9": ok({"type": "tombstone", "id": "9", "reason": "suspended"})}

    for _night in range(2):
        _run(config, paths, fx_transport(responses))

    store = open_archive_store(paths, create=False, config=config)
    assert store is not None
    rows = store.get_tweet_object_rows(["7", "8", "9"])
    # Bare 404s change nothing, whoever marked the tweet.
    for tweet_id in ("7", "8"):
        assert rows[tweet_id]["enrichment_retry_count"] == 0
        assert rows[tweet_id]["enrichment_next_retry_at"] == "2030-01-01T00:00:00+00:00"
    # A tombstone sharpens the reason, but the due login retry stays due and uncounted.
    assert rows["9"]["enrichment_reason"] == "suspended_account"
    assert rows["9"]["enrichment_retry_count"] == 2
    assert rows["9"]["enrichment_next_retry_at"] == "2020-01-01T00:00:00+00:00"
    store.close()


def test_first_check_compares_with_the_note_saved_with_the_tweet(config, paths) -> None:
    store = open_archive_store(paths, create=True, config=config)
    assert store is not None
    for tweet_id in ("1", "2"):
        result = make_tweet_result(tweet_id, f"noted {tweet_id}")
        result["birdwatch_pivot"] = {"subtitle": {"text": "Saved note", "entities": []}}
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
                    text=f"noted {tweet_id}",
                    author_id="100",
                    author_username="user100",
                    author_display_name="User 100",
                    created_at="Sat Mar 14 00:00:00 +0000 2026",
                    sort_index=tweet_id,
                    raw_json=result,
                )
            ],
            last_head_tweet_id=tweet_id,
            backfill_cursor=None,
            backfill_incomplete=False,
        )
    store.close()
    responses = {
        "1": ok(fx_status("1", "noted 1")),
        "2": ok(fx_status("2", "noted 2", note="Newer note")),
    }

    result = _run(config, paths, fx_transport(responses))

    assert (result.notes_removed, result.notes_changed) == (1, 1)
    store = open_archive_store(paths, create=False, config=config)
    assert store is not None
    gone = store.list_community_notes("1")
    assert [(note["text"], bool(note["removed_at"])) for note in gone] == [("Saved note", True)]
    replaced = {note["text"]: note["removed_at"] for note in store.list_community_notes("2")}
    assert replaced["Saved note"] is not None and replaced["Newer note"] is None
    store.close()


def test_rehydrate_keeps_availability_set_after_an_fxtwitter_copy(paths) -> None:
    from tweetnook.client.fxtwitter import fx_status_to_graphql, fx_timeline_tweet

    store = open_archive_store(paths, create=True)
    assert store is not None
    _save(store, "1", "saved")
    status = fx_status("2", "fx copy", author_id="200")
    store.persist_fx_status(
        tweet=fx_timeline_tweet(fx_status_to_graphql(status)), payload=ok(status)[1]
    )
    store.persist_unavailable_tweet(
        tweet_id="2",
        operation="FxTwitterStatus",
        raw_json={},
        http_status=200,
        reason="suspended_account",
        detail="This post is from a suspended account.",
        retry_eligible=True,
        next_retry_at="2030-01-01T00:00:00+00:00",
    )

    store.rehydrate_from_raw_json()

    row = store.get_tweet_object_rows(["2"])["2"]
    assert (row["enrichment_state"], row["enrichment_reason"]) == (
        "terminal_unavailable",
        "suspended_account",
    )
    assert store._get_row("tweet_object:2")["text"] == "fx copy"
    store.close()


def test_check_help_lists_its_flags() -> None:
    result = CliRunner().invoke(cli.app, ["check", "--help"])

    assert result.exit_code == 0
    normalized = " ".join(result.stdout.split())
    assert "Check that archived tweets still exist" in normalized
    assert "Runs at the configured frequency (weekly by default)" in normalized
    assert "check.context_enabled" in normalized
    assert "nightly" not in normalized.lower()
    for flag in ("--skip-saves", "--skip-context", "--skip-unavailable", "--detail-budget"):
        assert flag in result.stdout


@pytest.mark.parametrize(("args", "expected"), [([], None), (["--skip-context"], False)])
def test_check_cli_uses_context_setting_unless_skipped(config, paths, monkeypatch, args, expected):
    import tweetnook.check as check

    seen = []

    async def run(**kwargs):
        seen.append(kwargs["context"])
        return check.CheckResult()

    monkeypatch.setattr(cli, "load_config", lambda: (config, paths))
    monkeypatch.setattr(check, "run_check", run)
    result = CliRunner().invoke(cli.app, ["check", *args])
    assert result.exit_code == 0, result.output
    assert seen == [expected]


def test_check_history_records_changes_not_every_night(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store.ensure_check_tables()
    nights = [
        ("available", None),
        ("available", None),
        ("tombstone", "suspended_account"),
        ("tombstone", "suspended_account"),
        ("not_found", "unavailable_unknown"),
        ("error", None),
        ("suspect", None),
        ("restored", None),
        ("available", None),
    ]
    for night, (outcome, reason) in enumerate(nights, start=1):
        store.record_tweet_checks(
            [("1", outcome, 200, "save", reason)], checked_at=f"2026-01-0{night}"
        )

    history = store.list_tweet_check_history("1")
    assert [(row["checked_at"], row["outcome"]) for row in history] == [
        ("2026-01-01", "available"),
        ("2026-01-03", "tombstone"),
        ("2026-01-05", "not_found"),
        ("2026-01-08", "restored"),
    ]
    assert store.get_tweet_check("1")["checked_at"] == "2026-01-09"
    # A failed lookup isn't recorded at all, so the tweet stays due.
    store.record_tweet_checks([("2", "error", 503, "context", None)], checked_at="2026-01-09")
    assert store.get_tweet_check("2") is None
    store.close()


def test_feed_shows_the_note_the_check_last_saw(config, paths) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from tweetnook.web.deps import get_store, verify_credentials
    from tweetnook.web.routes.tweets import router

    store = open_archive_store(paths, create=True, config=config)
    assert store is not None
    result = make_tweet_result("4", "noted")
    result["birdwatch_pivot"] = {"subtitle": {"text": "Saved v1", "entities": []}}
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={},
        tweets=[
            TimelineTweet(
                tweet_id="4",
                text="noted",
                author_id="100",
                author_username="user100",
                author_display_name="User 100",
                created_at="Sat Mar 14 00:00:00 +0000 2026",
                sort_index="4",
                raw_json=result,
            )
        ],
        last_head_tweet_id="4",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.ensure_check_tables()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[verify_credentials] = lambda: True
    client = TestClient(app)

    def shown_note():
        feed = client.get("/api/tweets", params={"collection": "bookmarks"}).json()
        raw = next(tweet for tweet in feed["tweets"] if tweet["tweet_id"] == "4")["raw_json"]
        return ((raw.get("birdwatch_pivot") or {}).get("subtitle") or {}).get("text")

    assert shown_note() == "Saved v1"
    store.record_community_note("4", {"text": "Saved v1"}, seen_at="2026-01-01T00:00:00+00:00")
    store.record_community_note("4", {"text": "Checked v2"}, seen_at="2026-01-02T00:00:00+00:00")
    assert shown_note() == "Checked v2"
    store.record_community_note("4", None, seen_at="2026-01-03T00:00:00+00:00")
    assert shown_note() is None
    store.close()


def test_history_api_and_missing_tweets_keep_their_archived_copy(config, paths) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from tweetnook.web.deps import get_store, verify_credentials
    from tweetnook.web.routes.tweets import router

    store = open_archive_store(paths, create=True, config=config)
    assert store is not None
    _save(store, "4", "saved four")
    store.close()
    responses = {"4": ok({"type": "tombstone", "id": "4", "reason": "suspended"})}
    _run(config, paths, fx_transport(responses))

    store = open_archive_store(paths, create=False, config=config)
    assert store is not None
    store.record_community_note("4", {"text": "Context"}, seen_at="2026-01-01T00:00:00+00:00")
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[verify_credentials] = lambda: True
    client = TestClient(app)

    history = client.get("/api/tweets/4/history").json()
    feed = client.get("/api/tweets", params={"collection": "bookmarks"}).json()

    assert history["status"]["state"] == "missing"
    assert history["status"]["message"] == "This tweet is from a suspended account."
    events = [(event["event"], event["source"]) for event in history["events"]]
    assert ("Missing from X", "Availability check") in events
    assert ("Saved to bookmarks", "Archive") in events
    assert ("Community note added", "Availability check") in events
    # History only says the note changed; its text is in the Community notes dialog.
    assert all(event["detail"] is None for event in history["events"] if event["status"] == "note")
    assert ("Missing from X", "FxTwitter") not in events
    assert client.get("/api/tweets/abc/history").status_code == 404
    saved = next(tweet for tweet in feed["tweets"] if tweet["tweet_id"] == "4")
    # Missing, but shown from the archive with a warning rather than as a placeholder.
    assert saved["availability"]["state"] == "unavailable"
    assert saved["availability"]["placeholder"] is False
    assert saved["text"] == "saved four"
    # The note found by the check is shown even though the stored X payload predates it.
    assert saved["raw_json"]["birdwatch_pivot"]["subtitle"]["text"] == "Context"
    assert saved["note_history"] is True
    assert saved["raw_json"]["birdwatch_pivot"]["note_history"] is True
    store.record_community_note("4", {"text": "Context v2"}, seen_at="2026-01-02T00:00:00+00:00")
    notes = client.get("/api/tweets/4/notes").json()["versions"]
    assert [(note["text"], note["current"]) for note in notes] == [
        ("Context v2", True),
        ("Context", False),
    ]
    assert notes[1]["removed_at"] == "2026-01-02T00:00:00+00:00"
    assert client.get("/api/tweets/1/notes").json()["versions"] == []
    store.close()
