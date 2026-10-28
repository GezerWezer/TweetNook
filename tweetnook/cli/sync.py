"""`tweetnook sync`: collection syncs and their follow-up passes."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

import typer
from rich.console import Console

from tweetnook.auth import resolve_auth_bundle
from tweetnook.cli.common import (
    SYNC_MAX_LINKED_DEPTH_OPTION,
    SYNC_REPLY_LINKED_DEPTH_OPTION,
    configure_logging,
    web_pipeline,
)
from tweetnook.config import load_config
from tweetnook.core.exceptions import ConfigError, TweetNookError
from tweetnook.sync.collection import sync_all, sync_collection
from tweetnook.sync.followups import SyncFollowupPlan, plan_sync_pipeline

SYNC_GROUP_HELP = (
    "Run the normal sync pass. Without a subcommand, this syncs the collections chosen in "
    "sync.collections (bookmarks and likes unless changed), then runs thread expansion, "
    "resurrection checks, article refresh, media download, URL unfurl, and configured AI "
    "tagging unless skipped or inapplicable."
)
SYNC_ALL_HELP = (
    "Sync the collections chosen in sync.collections (bookmarks and likes unless changed), "
    "then run thread expansion, resurrection checks, article "
    "refresh, media download, URL unfurl, and configured AI tagging unless skipped "
    "or inapplicable."
)
sync_app = typer.Typer(
    invoke_without_command=True,
    help=SYNC_GROUP_HELP,
)
ARTICLE_BACKFILL_HELP = (
    "Rewalk existing timeline pages without resetting sync state so older items can pick up "
    "new article fields."
)
SYNC_FULL_HELP = "Reset saved sync state for the targeted collection before syncing."
SYNC_BACKFILL_HELP = "Continue older history past duplicates without resetting sync state."
HEAD_ONLY_HELP = (
    "Clear any saved backfill cursor for the targeted collection and run only the head pass. "
    "Does not resume older historical backfill state."
)
SYNC_LIMIT_HELP = "Maximum number of pages to fetch for this run."
SYNC_SKIP_RESURRECTION_HELP = (
    "Skip bounded unavailable-tweet resurrection checks after sync. Sync only runs them "
    "while the check is off or FxTwitter is disabled; otherwise 'tweetnook check' does."
)
SYNC_SKIP_ARTICLES_HELP = "Skip automatic article-body refresh after sync."
SYNC_SKIP_MEDIA_HELP = "Skip automatic media downloads after sync."
SYNC_SKIP_UNFURL_HELP = "Skip automatic URL unfurls after sync."
SYNC_ARTICLE_BACKFILL_OPTION = Annotated[
    bool,
    typer.Option("--article-backfill", help=ARTICLE_BACKFILL_HELP),
]
SYNC_HEAD_ONLY_OPTION = Annotated[
    bool,
    typer.Option("--head-only", help=HEAD_ONLY_HELP),
]
SYNC_SKIP_RESURRECTION_OPTION = Annotated[
    bool,
    typer.Option("--skip-resurrection", help=SYNC_SKIP_RESURRECTION_HELP),
]
SYNC_SKIP_ARTICLES_OPTION = Annotated[
    bool,
    typer.Option("--skip-articles", help=SYNC_SKIP_ARTICLES_HELP),
]
SYNC_SKIP_MEDIA_OPTION = Annotated[
    bool,
    typer.Option("--skip-media", help=SYNC_SKIP_MEDIA_HELP),
]
SYNC_SKIP_UNFURL_OPTION = Annotated[
    bool,
    typer.Option("--skip-unfurl", help=SYNC_SKIP_UNFURL_HELP),
]
SYNC_SKIP_THREADS_OPTION = Annotated[
    bool, typer.Option("--skip-threads", help="Skip thread expansion pass.")
]


def _run_sync_command(
    *,
    runner: Callable[[Any, Any, Console], Awaitable[Any]],
    collections: tuple[str, ...] | None,
    followups: SyncFollowupPlan,
    head_only: bool,
    summarize: Callable[[Any], str] | None = None,
) -> tuple[Console, Any]:
    console = configure_logging()
    try:
        config, paths = load_config()
        with web_pipeline(console, "tweetnook sync", paths) as pipeline:
            plan_sync_pipeline(
                pipeline,
                config=config,
                # None means the collections chosen in Settings (sync.collections).
                collections=collections if collections is not None else config.sync.collections,
                followups=followups,
                head_only=head_only,
            )
            auth_bundle = resolve_auth_bundle(config)
            result = asyncio.run(runner(config, auth_bundle, console))
            errors = getattr(result, "errors", None)
            if errors:
                for name, error in errors.items():
                    pipeline.issue(
                        f"{str(name).title()} sync failed: {error}",
                        level="error",
                        dedupe_key=f"sync:{name}:failure",
                    )
                failed = ", ".join(str(name) for name in errors)
                completed = summarize(result) if summarize is not None else ""
                summary = f"{completed}. " if completed else ""
                pipeline.finish(
                    summary + f"Sync stopped after failure in {failed}.",
                    success=False,
                )
            elif summarize is not None and not pipeline.has_final_note:
                pipeline.final_note(summarize(result))
        return console, result
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except TweetNookError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc


def _sync_followup_plan(
    *,
    skip_resurrection: bool,
    skip_articles: bool,
    skip_media: bool,
    skip_unfurl: bool,
    skip_threads: bool,
) -> SyncFollowupPlan:
    return SyncFollowupPlan(
        resurrection=not skip_resurrection,
        articles=not skip_articles,
        media=not skip_media,
        unfurl=not skip_unfurl,
        threads=not skip_threads,
    )


def _run_sync_all_command(
    *,
    full: bool,
    backfill: bool,
    article_backfill: bool,
    head_only: bool,
    limit: int | None,
    skip_resurrection: bool,
    skip_articles: bool,
    skip_media: bool,
    skip_unfurl: bool,
    skip_threads: bool,
    max_linked_depth: int | None = None,
    reply_linked_depth: int | None = None,
) -> None:
    followups = _sync_followup_plan(
        skip_resurrection=skip_resurrection,
        skip_articles=skip_articles,
        skip_media=skip_media,
        skip_unfurl=skip_unfurl,
        skip_threads=skip_threads,
    )
    console, outcome = _run_sync_command(
        collections=None,
        followups=followups,
        head_only=head_only,
        runner=lambda config, auth_bundle, runner_console: _run_with_depth(
            config,
            auth_bundle,
            runner_console,
            max_linked_depth,
            reply_linked_depth,
            full,
            backfill,
            article_backfill,
            head_only,
            limit,
            followups,
        ),
        summarize=lambda outcome: "; ".join(
            f"{item.collection}: {item.pages_fetched} pages, {item.tweets_seen} tweets"
            for item in outcome.results
        ),
    )
    raise typer.Exit(outcome.exit_code)


def _run_with_depth(
    config,
    auth_bundle,
    runner_console,
    max_linked_depth,
    reply_linked_depth,
    full,
    backfill,
    article_backfill,
    head_only,
    limit,
    followups,
):
    if max_linked_depth is not None:
        config.sync.max_linked_depth = max_linked_depth
    if reply_linked_depth is not None:
        config.sync.reply_linked_depth = reply_linked_depth
    return sync_all(
        full=full,
        backfill=backfill,
        article_backfill=article_backfill,
        head_only=head_only,
        limit=limit,
        config=config,
        auth_bundle=auth_bundle,
        console=runner_console,
        followups=followups,
    )


@sync_app.callback()
def sync_default(
    ctx: typer.Context,
    full: Annotated[bool, typer.Option("--full", help=SYNC_FULL_HELP)] = False,
    backfill: Annotated[
        bool,
        typer.Option("--backfill", help=SYNC_BACKFILL_HELP),
    ] = False,
    article_backfill: SYNC_ARTICLE_BACKFILL_OPTION = False,
    head_only: SYNC_HEAD_ONLY_OPTION = False,
    limit: Annotated[int | None, typer.Option("--limit", help=SYNC_LIMIT_HELP)] = None,
    skip_resurrection: SYNC_SKIP_RESURRECTION_OPTION = False,
    skip_articles: SYNC_SKIP_ARTICLES_OPTION = False,
    skip_media: SYNC_SKIP_MEDIA_OPTION = False,
    skip_unfurl: SYNC_SKIP_UNFURL_OPTION = False,
    skip_threads: SYNC_SKIP_THREADS_OPTION = False,
    max_linked_depth: SYNC_MAX_LINKED_DEPTH_OPTION = None,
    reply_linked_depth: SYNC_REPLY_LINKED_DEPTH_OPTION = None,
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    _run_sync_all_command(
        full=full,
        backfill=backfill,
        article_backfill=article_backfill,
        head_only=head_only,
        limit=limit,
        skip_resurrection=skip_resurrection,
        skip_articles=skip_articles,
        skip_media=skip_media,
        skip_unfurl=skip_unfurl,
        skip_threads=skip_threads,
        max_linked_depth=max_linked_depth,
        reply_linked_depth=reply_linked_depth,
    )


def _register_sync_collection_command(collection: str):
    command_help = {
        "bookmarks": "Sync bookmarked tweets.",
        "likes": "Sync liked tweets.",
        "tweets": "Sync authored tweets.",
    }[collection]

    def command(
        full: Annotated[bool, typer.Option("--full", help=SYNC_FULL_HELP)] = False,
        backfill: Annotated[
            bool,
            typer.Option("--backfill", help=SYNC_BACKFILL_HELP),
        ] = False,
        article_backfill: SYNC_ARTICLE_BACKFILL_OPTION = False,
        head_only: SYNC_HEAD_ONLY_OPTION = False,
        limit: Annotated[int | None, typer.Option("--limit", help=SYNC_LIMIT_HELP)] = None,
        skip_resurrection: SYNC_SKIP_RESURRECTION_OPTION = False,
        skip_articles: SYNC_SKIP_ARTICLES_OPTION = False,
        skip_media: SYNC_SKIP_MEDIA_OPTION = False,
        skip_unfurl: SYNC_SKIP_UNFURL_OPTION = False,
        skip_threads: SYNC_SKIP_THREADS_OPTION = False,
    ) -> None:
        followups = _sync_followup_plan(
            skip_resurrection=skip_resurrection,
            skip_articles=skip_articles,
            skip_media=skip_media,
            skip_unfurl=skip_unfurl,
            skip_threads=skip_threads,
        )
        console, result = _run_sync_command(
            collections=(collection,),
            followups=followups,
            head_only=head_only,
            runner=lambda config, auth_bundle, runner_console: sync_collection(
                collection,
                full=full,
                backfill=backfill,
                article_backfill=article_backfill,
                head_only=head_only,
                limit=limit,
                config=config,
                auth_bundle=auth_bundle,
                console=runner_console,
                followups=followups,
            ),
            summarize=lambda result: (
                f"{collection}: {result.pages_fetched} pages, "
                f"{result.tweets_seen} tweets, {result.stop_reason}"
            ),
        )

    command.__name__ = f"sync_{collection}"
    return sync_app.command(collection, help=command_help)(command)


sync_bookmarks = _register_sync_collection_command("bookmarks")
sync_likes = _register_sync_collection_command("likes")
sync_tweets = _register_sync_collection_command("tweets")


@sync_app.command("all", help=SYNC_ALL_HELP)
def sync_everything(
    full: Annotated[bool, typer.Option("--full", help=SYNC_FULL_HELP)] = False,
    backfill: Annotated[
        bool,
        typer.Option("--backfill", help=SYNC_BACKFILL_HELP),
    ] = False,
    article_backfill: SYNC_ARTICLE_BACKFILL_OPTION = False,
    head_only: SYNC_HEAD_ONLY_OPTION = False,
    limit: Annotated[int | None, typer.Option("--limit", help=SYNC_LIMIT_HELP)] = None,
    skip_resurrection: SYNC_SKIP_RESURRECTION_OPTION = False,
    skip_articles: SYNC_SKIP_ARTICLES_OPTION = False,
    skip_media: SYNC_SKIP_MEDIA_OPTION = False,
    skip_unfurl: SYNC_SKIP_UNFURL_OPTION = False,
    skip_threads: SYNC_SKIP_THREADS_OPTION = False,
    max_linked_depth: SYNC_MAX_LINKED_DEPTH_OPTION = None,
    reply_linked_depth: SYNC_REPLY_LINKED_DEPTH_OPTION = None,
) -> None:
    _run_sync_all_command(
        full=full,
        backfill=backfill,
        article_backfill=article_backfill,
        head_only=head_only,
        limit=limit,
        skip_resurrection=skip_resurrection,
        skip_articles=skip_articles,
        skip_media=skip_media,
        skip_unfurl=skip_unfurl,
        skip_threads=skip_threads,
        max_linked_depth=max_linked_depth,
        reply_linked_depth=reply_linked_depth,
    )
