from __future__ import annotations

from io import StringIO
from types import SimpleNamespace

import pytest

import tweetnook.cli as cli
from tests.cli.conftest import _capture_console, runner
from tests.conftest import patch_cli
from tweetnook.cli import auth as cli_auth
from tweetnook.cli import imports as cli_imports
from tweetnook.cli import sync as cli_sync
from tweetnook.config import AppConfig, AuthConfig
from tweetnook.sync.collection import SyncMode


@pytest.mark.parametrize("command", [[], ["all"], ["likes"]])
def test_sync_forwards_catch_up(paths, monkeypatch, command):
    _capture_console(monkeypatch, StringIO())
    patch_cli(monkeypatch, "load_config", lambda: (AppConfig(), paths))
    patch_cli(monkeypatch, "resolve_auth_bundle", lambda config: object())
    forwarded = []

    async def sync(*args, **kwargs):
        if args:
            forwarded.append(kwargs["catch_up"])
            assert not kwargs["backfill"]
        else:
            forwarded.append(kwargs["mode"] is SyncMode.CATCH_UP)
        return SimpleNamespace(
            pages_fetched=2,
            tweets_seen=40,
            stop_reason="import-overlap",
            results=[],
            errors={},
            exit_code=0,
        )

    patch_cli(monkeypatch, "sync_all", sync)
    patch_cli(monkeypatch, "sync_collection", sync)
    result = runner.invoke(cli.app, ["sync", *command, "--catch-up"])
    assert result.exit_code == 0, result.output
    assert forwarded == [True]


@pytest.mark.parametrize("command", [[], ["all"], ["likes"]])
@pytest.mark.parametrize("other", ["--full", "--backfill", "--article-backfill", "--head-only"])
def test_catch_up_rejects_other_modes_before_loading_config(monkeypatch, command, other):
    def unexpected():
        pytest.fail("conflicting sync modes must not load config")

    patch_cli(monkeypatch, "load_config", unexpected)
    result = runner.invoke(cli.app, ["sync", *command, "--catch-up", other])
    assert result.exit_code == 2
    assert "--catch-up cannot be combined" in result.output


def test_auth_check_uses_explicit_credentials(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    patch_cli(
        monkeypatch,
        "load_config",
        lambda: (
            AppConfig(auth=AuthConfig(auth_token="config-token", ct0="config-ct0")),
            paths,
        ),
    )
    selected = {}
    patch_cli(
        monkeypatch,
        "resolve_auth_bundle",
        lambda config, env=None: SimpleNamespace(
            auth_token="config-token",
            ct0="config-ct0",
            user_id="42",
            auth_token_source="config",
            ct0_source="config",
            user_id_source="config",
        ),
    )

    async def fake_run_preflight(*, config, paths, collections, auth_bundle=None):
        selected["auth_bundle"] = auth_bundle
        return SimpleNamespace(
            auth=auth_bundle,
            probes={
                "bookmarks": SimpleNamespace(ready=True, detail="Remote probe succeeded."),
                "likes": SimpleNamespace(ready=True, detail="Remote probe succeeded."),
                "tweets": SimpleNamespace(ready=True, detail="Remote probe succeeded."),
            },
            has_local_error=False,
            has_remote_error=False,
        )

    patch_cli(monkeypatch, "run_preflight", fake_run_preflight)

    cli_auth.auth_check()

    assert selected["auth_bundle"].auth_token == "config-token"
    output = buffer.getvalue()
    assert "local auth: auth_token=config" in output
    assert "bookmarks: ready" in output
    assert "tweets: ready" in output


@pytest.mark.parametrize("collection_name", ["bookmarks", "likes", "tweets"])
def test_sync_collection_commands_forward_article_backfill(
    paths, monkeypatch, collection_name: str
) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    patch_cli(monkeypatch, "load_config", lambda: (AppConfig(), paths))
    patch_cli(
        monkeypatch,
        "resolve_auth_bundle",
        lambda config: SimpleNamespace(auth_token="t"),
    )
    forwarded = {}

    async def fake_sync_collection(
        collection,
        *,
        full,
        backfill=False,
        head_only=False,
        catch_up=False,
        limit=None,
        config=None,
        auth_bundle=None,
        console=None,
        followups=None,
    ):
        forwarded.update(
            {
                "collection": collection,
                "full": full,
                "backfill": backfill,
                "head_only": head_only,
                "limit": limit,
                "followups": followups,
            }
        )
        return SimpleNamespace(pages_fetched=2, tweets_seen=3, stop_reason="empty")

    patch_cli(monkeypatch, "sync_collection", fake_sync_collection)

    result = runner.invoke(cli.app, ["sync", collection_name, "--article-backfill"])
    assert result.exit_code == 0, result.output

    assert forwarded == {
        "collection": collection_name,
        "full": False,
        "backfill": True,
        "head_only": False,
        "limit": None,
        "followups": cli_imports.SyncFollowupPlan(),
    }
    assert f"{collection_name}: 2 pages, 3 tweets, empty" in buffer.getvalue()


@pytest.mark.parametrize("subcommand", [[], ["all"]])
def test_sync_all_forwards_article_backfill(paths, monkeypatch, subcommand) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    patch_cli(monkeypatch, "load_config", lambda: (AppConfig(), paths))
    patch_cli(
        monkeypatch,
        "resolve_auth_bundle",
        lambda config: SimpleNamespace(auth_token="t"),
    )
    forwarded = {}

    async def fake_sync_all(
        *,
        mode,
        limit=None,
        config=None,
        auth_bundle=None,
        console=None,
        followups=None,
    ):
        forwarded.update(
            {
                "mode": mode,
                "limit": limit,
                "followups": followups,
            }
        )
        return SimpleNamespace(
            results=[SimpleNamespace(collection="bookmarks", pages_fetched=2, tweets_seen=3)],
            errors={"likes": "boom"},
            exit_code=2,
        )

    patch_cli(monkeypatch, "sync_all", fake_sync_all)

    result = runner.invoke(cli.app, ["sync", *subcommand, "--article-backfill"])

    assert result.exit_code == 2, result.output
    assert forwarded == {
        "mode": SyncMode.BACKFILL,
        "limit": None,
        "followups": cli_imports.SyncFollowupPlan(),
    }
    output = buffer.getvalue()
    assert "bookmarks: 2 pages, 3 tweets" in output
    assert "Sync stopped after failure in likes." in output


def test_sync_default_runs_sync_all_with_full_followups(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    patch_cli(monkeypatch, "load_config", lambda: (AppConfig(), paths))
    patch_cli(
        monkeypatch,
        "resolve_auth_bundle",
        lambda config: SimpleNamespace(auth_token="t"),
    )
    forwarded = {}

    async def fake_sync_all(
        *,
        mode,
        limit=None,
        config=None,
        auth_bundle=None,
        console=None,
        followups=None,
    ):
        forwarded.update(
            {
                "mode": mode,
                "limit": limit,
                "followups": followups,
            }
        )
        return SimpleNamespace(
            results=[
                SimpleNamespace(collection="bookmarks", pages_fetched=2, tweets_seen=3),
                SimpleNamespace(collection="likes", pages_fetched=1, tweets_seen=2),
            ],
            errors={},
            exit_code=0,
        )

    patch_cli(monkeypatch, "sync_all", fake_sync_all)

    result = runner.invoke(cli.app, ["sync"])

    assert result.exit_code == 0
    assert forwarded == {
        "mode": SyncMode.INCREMENTAL,
        "limit": None,
        "followups": cli_imports.SyncFollowupPlan(),
    }
    output = buffer.getvalue()
    assert "bookmarks: 2 pages, 3 tweets" in output
    assert "likes: 1 pages, 2 tweets" in output


def test_sync_default_skip_flags_disable_selected_followups(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    patch_cli(monkeypatch, "load_config", lambda: (AppConfig(), paths))
    patch_cli(
        monkeypatch,
        "resolve_auth_bundle",
        lambda config: SimpleNamespace(auth_token="t"),
    )
    forwarded = {}

    async def fake_sync_all(
        *,
        mode,
        limit=None,
        config=None,
        auth_bundle=None,
        console=None,
        followups=None,
    ):
        forwarded["followups"] = followups
        return SimpleNamespace(results=[], errors={}, exit_code=0)

    patch_cli(monkeypatch, "sync_all", fake_sync_all)

    result = runner.invoke(
        cli.app, ["sync", "--skip-resurrection", "--skip-media", "--skip-threads"]
    )

    assert result.exit_code == 0
    assert forwarded["followups"] == cli_imports.SyncFollowupPlan(
        resurrection=False,
        media=False,
        threads=False,
    )


def test_sync_likes_forwards_head_only(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    patch_cli(monkeypatch, "load_config", lambda: (AppConfig(), paths))
    patch_cli(
        monkeypatch,
        "resolve_auth_bundle",
        lambda config: SimpleNamespace(auth_token="t"),
    )
    forwarded = {}

    async def fake_sync_collection(
        collection,
        *,
        full,
        backfill=False,
        head_only=False,
        catch_up=False,
        limit=None,
        config=None,
        auth_bundle=None,
        console=None,
        followups=None,
    ):
        forwarded.update(
            {
                "collection": collection,
                "full": full,
                "backfill": backfill,
                "head_only": head_only,
                "limit": limit,
                "followups": followups,
            }
        )
        return SimpleNamespace(pages_fetched=1, tweets_seen=20, stop_reason="duplicate")

    patch_cli(monkeypatch, "sync_collection", fake_sync_collection)

    cli_sync.sync_likes(head_only=True)

    assert forwarded == {
        "collection": "likes",
        "full": False,
        "backfill": False,
        "head_only": True,
        "limit": None,
        "followups": cli_imports.SyncFollowupPlan(),
    }
    assert "likes: 1 pages, 20 tweets, duplicate" in buffer.getvalue()


@pytest.mark.parametrize("subcommand", [[], ["all"], ["bookmarks"], ["likes"], ["tweets"]])
def test_article_backfill_alias_is_hidden_from_help(subcommand):
    result = runner.invoke(cli.app, ["sync", *subcommand, "--help"])
    assert result.exit_code == 0
    assert "--backfill" in result.output
    assert "--article-backfill" not in result.output


@pytest.mark.parametrize("command", [["sync"], ["sync", "all"], ["threads", "expand"]])
def test_linked_depth_rejects_negative_values_before_loading_config(monkeypatch, command):
    def unexpected_load():
        pytest.fail("invalid command must not load configuration")

    patch_cli(monkeypatch, "load_config", unexpected_load)
    result = runner.invoke(cli.app, [*command, "--max-linked-depth", "-1"])

    assert result.exit_code == 2, result.output
    assert "--max-linked-depth" in result.output


@pytest.mark.parametrize("command", [["sync"], ["sync", "all"], ["threads", "expand"]])
def test_linked_depth_help_describes_zero_and_default(command):
    result = runner.invoke(cli.app, [*command, "--help"])

    assert result.exit_code == 0
    assert "--max-linked-depth" in result.output
    help_text = " ".join(result.output.replace("│", " ").split())
    assert "0 disables linked discovery" in help_text
    assert "default 50" in help_text


@pytest.mark.parametrize("depths", [(-1, None), (None, 2)])
def test_programmatic_depth_override_does_not_leave_invalid_sync_settings(depths):
    from pydantic import ValidationError

    from tweetnook.cli.common import apply_sync_depth_options

    config = AppConfig()
    original = config.sync
    with pytest.raises(ValidationError):
        apply_sync_depth_options(config, *depths)
    assert config.sync is original
    assert config.sync.max_linked_depth == 50
    assert config.sync.reply_linked_depth == 0


def test_programmatic_depth_override_preserves_other_sync_settings():
    from tweetnook.cli.common import apply_sync_depth_options

    config = AppConfig(sync={"timeout": 90, "collections": ["tweets"]})
    apply_sync_depth_options(config, 0, 1)

    assert config.sync.max_linked_depth == 0
    assert config.sync.reply_linked_depth == 1
    assert config.sync.timeout == 90
    assert config.sync.collections == ["tweets"]


@pytest.mark.parametrize("subcommand", ["all", "bookmarks", "likes", "tweets"])
@pytest.mark.parametrize(
    "options",
    [
        ["--full"],
        ["--backfill"],
        ["--article-backfill"],
        ["--head-only"],
        ["--limit", "0"],
        ["--skip-resurrection"],
        ["--skip-articles"],
        ["--skip-media"],
        ["--skip-unfurl"],
        ["--skip-threads"],
        ["--max-linked-depth", "3"],
        ["--reply-linked-depth", "0"],
    ],
)
def test_sync_rejects_parent_modifiers_before_any_setup(monkeypatch, options, subcommand):
    def unexpected_setup(*args, **kwargs):
        pytest.fail("misplaced options must stop before command setup")

    for name in ("load_config", "configure_logging", "resolve_auth_bundle", "web_pipeline"):
        patch_cli(monkeypatch, name, unexpected_setup)

    result = runner.invoke(cli.app, ["sync", *options, subcommand])

    assert result.exit_code == 2, result.output
    assert options[0] in result.output
    assert "after" in result.output


@pytest.mark.parametrize("subcommand", [[], ["all"], ["bookmarks"], ["likes"], ["tweets"]])
def test_sync_modifiers_at_the_execution_command_are_preserved(paths, monkeypatch, subcommand):
    _capture_console(monkeypatch, StringIO())
    config = AppConfig()
    patch_cli(monkeypatch, "load_config", lambda: (config, paths))
    patch_cli(monkeypatch, "resolve_auth_bundle", lambda config: SimpleNamespace(auth_token="t"))
    forwarded = {}

    async def fake_sync(*args, **kwargs):
        forwarded.update(kwargs)
        if args:
            forwarded["collection"] = args[0]
            return SimpleNamespace(pages_fetched=0, tweets_seen=0, stop_reason="empty")
        return SimpleNamespace(results=[], errors={}, exit_code=0)

    patch_cli(monkeypatch, "sync_all", fake_sync)
    patch_cli(monkeypatch, "sync_collection", fake_sync)
    result = runner.invoke(cli.app, ["sync", *subcommand, "--full", "--skip-media", "--limit", "0"])

    assert result.exit_code == 0, result.output
    if subcommand in ([], ["all"]):
        assert forwarded["mode"] is SyncMode.FULL
    else:
        assert forwarded["full"] is True
    assert forwarded["limit"] == 0
    assert forwarded["followups"].media is False
    assert forwarded["followups"].threads is True
    if subcommand and subcommand != ["all"]:
        assert forwarded["collection"] == subcommand[0]
