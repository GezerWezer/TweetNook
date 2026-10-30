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
        full,
        backfill=False,
        head_only=False,
        limit=None,
        config=None,
        auth_bundle=None,
        console=None,
        followups=None,
    ):
        forwarded.update(
            {
                "full": full,
                "backfill": backfill,
                "head_only": head_only,
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
        "full": False,
        "backfill": True,
        "head_only": False,
        "limit": None,
        "followups": cli_imports.SyncFollowupPlan(),
    }
    output = buffer.getvalue()
    assert "bookmarks: 2 pages, 3 tweets" in output
    assert "Likes sync failed: boom" in output


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
        full,
        backfill=False,
        head_only=False,
        limit=None,
        config=None,
        auth_bundle=None,
        console=None,
        followups=None,
    ):
        forwarded.update(
            {
                "full": full,
                "backfill": backfill,
                "head_only": head_only,
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
        "full": False,
        "backfill": False,
        "head_only": False,
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
        full,
        backfill=False,
        head_only=False,
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
