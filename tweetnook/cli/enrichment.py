"""Enrichment commands: articles, threads, media, unfurl, check and tag."""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer

from tweetnook.auth import resolve_auth_bundle
from tweetnook.cli.common import (
    SYNC_MAX_LINKED_DEPTH_OPTION,
    SYNC_REPLY_LINKED_DEPTH_OPTION,
    configure_logging,
    web_pipeline,
)
from tweetnook.config import load_config
from tweetnook.core.exceptions import ConfigError, TweetNookError
from tweetnook.core.extractor import extract_status_id_from_url
from tweetnook.enrichment.articles import refresh_articles
from tweetnook.enrichment.media import download_media
from tweetnook.enrichment.threads import expand_threads
from tweetnook.enrichment.unfurl import unfurl_urls
from tweetnook.jobs.pipeline import current_pipeline

article_app = typer.Typer(no_args_is_help=True, help="Refresh archived article bodies.")
media_app = typer.Typer(no_args_is_help=True, help="Download archived tweet media.")
thread_app = typer.Typer(no_args_is_help=True, help="Expand archived tweet threads.")
ARTICLE_LIMIT_HELP = "Maximum number of archived article rows or explicit targets to process."
THREAD_LIMIT_HELP = "Maximum number of archived thread targets to process."
MEDIA_LIMIT_HELP = (
    "Maximum number of media items to process. A video or GIF can include a poster file."
)
MEDIA_PHOTOS_ONLY_HELP = "Only download photo rows and skip video or animated GIF media."
RETRY_FAILED_HELP = "Retry rows that previously failed instead of only untouched pending rows."
UNFURL_LIMIT_HELP = "Maximum number of saved URL rows to fetch metadata for."
TAG_LIMIT_HELP = "Maximum number of independent Gemini requests to tag in this run."
TAG_BATCH_HELP = (
    "Override the Free-mode tweets-per-request batch size. Paid requests remain sequential "
    "with internal wave processing."
)
ARTICLE_LIMIT_OPTION = Annotated[int | None, typer.Option("--limit", help=ARTICLE_LIMIT_HELP)]
THREAD_LIMIT_OPTION = Annotated[int | None, typer.Option("--limit", help=THREAD_LIMIT_HELP)]
MEDIA_LIMIT_OPTION = Annotated[int | None, typer.Option("--limit", help=MEDIA_LIMIT_HELP)]
PHOTOS_ONLY_OPTION = Annotated[
    bool,
    typer.Option("--photos-only", help=MEDIA_PHOTOS_ONLY_HELP),
]
RETRY_FAILED_OPTION = Annotated[
    bool,
    typer.Option("--retry-failed", help=RETRY_FAILED_HELP),
]
UNFURL_LIMIT_OPTION = Annotated[int | None, typer.Option("--limit", help=UNFURL_LIMIT_HELP)]
TAG_LIMIT_OPTION = Annotated[
    int | None,
    typer.Option("--limit", min=1, help=TAG_LIMIT_HELP),
]


@article_app.command("refresh", help="Refresh archived article bodies via TweetDetail.")
def refresh_archived_articles(
    targets: Annotated[
        list[str] | None,
        typer.Argument(help="Tweet IDs or Twitter/X status URLs to refresh."),
    ] = None,
    all_articles: Annotated[
        bool,
        typer.Option(
            "--all", help="Refresh all archived article rows, not just preview-only ones."
        ),
    ] = False,
    limit: ARTICLE_LIMIT_OPTION = None,
) -> None:
    console = configure_logging()
    try:
        config, paths = load_config()
        with web_pipeline(console, "tweetnook articles refresh", paths) as pipeline:
            pipeline.add_step(
                "articles",
                "Articles",
                total=1,
                unit="tweets",
                detail="archived article rows selected for TweetDetail refresh",
                rate_unit="tweets/s",
            )
            if all_articles and targets:
                raise ConfigError("--all cannot be combined with explicit article targets.")
            auth_bundle = resolve_auth_bundle(config)
            result = asyncio.run(
                refresh_articles(
                    targets=targets,
                    preview_only=not all_articles,
                    limit=limit,
                    config=config,
                    paths=paths,
                    auth_bundle=auth_bundle,
                    console=console,
                )
            )
            if pipeline.step_state("articles") == "pending":
                prefix = "No article rows required refresh. " if result.processed == 0 else ""
                pipeline.final_note(
                    prefix + "articles: "
                    f"{result.processed} processed, "
                    f"{result.updated} refreshed, "
                    f"{result.failed} failed"
                )
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except TweetNookError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc


@thread_app.command("expand", help="Expand archived tweet threads via TweetDetail.")
def expand_archive_threads(
    targets: Annotated[
        list[str] | None,
        typer.Argument(help="Tweet IDs or Twitter/X status URLs to expand."),
    ] = None,
    limit: THREAD_LIMIT_OPTION = None,
    refresh: Annotated[
        bool,
        typer.Option(
            "--refresh",
            help="Re-fetch explicit thread targets even if they were already expanded.",
        ),
    ] = False,
    max_linked_depth: SYNC_MAX_LINKED_DEPTH_OPTION = None,
    reply_linked_depth: SYNC_REPLY_LINKED_DEPTH_OPTION = None,
) -> None:
    console = configure_logging()
    try:
        config, paths = load_config()
        with web_pipeline(console, "tweetnook threads expand", paths) as pipeline:
            pipeline.add_step(
                "threads",
                "Threads",
                total=1,
                unit="candidates",
                detail="membership and reachable linked-status candidates",
                rate_unit="candidates/s",
            )
            if max_linked_depth is not None:
                config.sync.max_linked_depth = max_linked_depth
            if reply_linked_depth is not None:
                config.sync.reply_linked_depth = reply_linked_depth
            auth_bundle = resolve_auth_bundle(config)
            result = asyncio.run(
                expand_threads(
                    targets=targets,
                    limit=limit,
                    refresh=refresh,
                    config=config,
                    paths=paths,
                    auth_bundle=auth_bundle,
                    console=console,
                )
            )
            if pipeline.step_state("threads") == "pending":
                prefix = "No thread candidates required fetching. " if result.processed == 0 else ""
                pipeline.final_note(
                    prefix + "threads: "
                    f"{result.processed} processed, "
                    f"{result.expanded} expanded, "
                    f"{result.skipped} skipped, "
                    f"{result.failed} failed"
                )
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except TweetNookError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc


CHECK_HELP = (
    "Check that archived tweets still exist: every saved tweet and the post it quotes, a "
    "rolling share of context tweets when check.context_enabled is on, and every "
    "unavailable tweet that may come back. "
    "Looks up through FxTwitter, then uses your login for protected accounts and results "
    "FxTwitter can't confirm. Runs at the configured frequency (weekly by default) from "
    "the Web service when check.enabled is on."
)


def check_command(
    skip_saves: Annotated[
        bool, typer.Option("--skip-saves", help="Don't check saved tweets.")
    ] = False,
    skip_context: Annotated[
        bool,
        typer.Option("--skip-context", help="Don't check the rolling share of context tweets."),
    ] = False,
    skip_unavailable: Annotated[
        bool,
        typer.Option(
            "--skip-unavailable", help="Don't recheck unavailable tweets through FxTwitter."
        ),
    ] = False,
    detail_budget: Annotated[
        int | None,
        typer.Option(
            "--detail-budget",
            min=0,
            help=(
                "Most tweets to look up with your login after FxTwitter. Defaults to "
                "check.detail_budget; 0 skips login lookups."
            ),
        ),
    ] = None,
) -> None:
    from tweetnook.enrichment.check import run_check

    console = configure_logging()
    try:
        config, paths = load_config()
        with web_pipeline(console, "tweetnook check", paths) as pipeline:
            result = asyncio.run(
                run_check(
                    config=config,
                    paths=paths,
                    saves=not skip_saves,
                    context=False if skip_context else None,
                    unavailable=not skip_unavailable,
                    detail_budget=detail_budget,
                    console=console,
                )
            )
            summary = (
                f"checked {result.checked} tweets ({result.saves_checked} saved, "
                f"{result.context_checked} context, {result.unavailable_checked} unavailable, "
                f"{result.quotes_checked} quoted) · {result.restored} restored · "
                f"{result.enriched} enriched · {result.became_unavailable} newly unavailable · "
                f"{result.suspect} to recheck · {result.protected} protected · "
                f"{result.errors} failed · community notes: "
                f"{result.notes_added} added, {result.notes_changed} changed, "
                f"{result.notes_removed} removed"
            )
            if result.detail is not None:
                summary += (
                    f" · login lookups: {result.detail.attempted} checked, "
                    f"{result.detail.resurrected} restored"
                )
            for warning in result.warnings:
                console.print(f"[yellow]{warning}[/yellow]")
            pipeline.final_note(summary)
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except TweetNookError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc


@media_app.command("download", help="Download archived tweet media files.")
def media_download(
    limit: MEDIA_LIMIT_OPTION = None,
    photos_only: PHOTOS_ONLY_OPTION = False,
    retry_failed: RETRY_FAILED_OPTION = False,
) -> None:
    console = configure_logging()
    try:
        config, paths = load_config()
        with web_pipeline(console, "tweetnook media download", paths) as pipeline:
            pipeline.add_step(
                "media",
                "Media",
                total=1,
                unit="items",
                detail="pending archived media selected for local download",
                rate_unit="items/s",
            )
            result = asyncio.run(
                download_media(
                    limit=limit,
                    photos_only=photos_only,
                    retry_failed=retry_failed,
                    config=config,
                    paths=paths,
                    console=console,
                )
            )
            if pipeline.step_state("media") == "pending":
                prefix = "No media items require download. " if result.processed == 0 else ""
                pipeline.final_note(
                    prefix + "media: "
                    f"{result.processed} processed, "
                    f"{result.downloaded} downloaded, "
                    f"{result.failed} failed"
                )
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except TweetNookError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc


def unfurl_archive(
    limit: UNFURL_LIMIT_OPTION = None,
    retry_failed: RETRY_FAILED_OPTION = False,
) -> None:
    console = configure_logging()
    try:
        config, paths = load_config()
        with web_pipeline(console, "tweetnook unfurl", paths) as pipeline:
            pipeline.add_step(
                "urls",
                "Link previews",
                total=1,
                unit="URLs",
                detail="saved URLs selected for redirect and canonical metadata refresh",
                rate_unit="URLs/s",
            )
            result = asyncio.run(
                unfurl_urls(
                    limit=limit,
                    retry_failed=retry_failed,
                    config=config,
                    paths=paths,
                    console=console,
                )
            )
            if pipeline.step_state("urls") == "pending":
                prefix = "No saved URLs require metadata. " if result.processed == 0 else ""
                pipeline.final_note(
                    prefix + f"unfurl: {result.processed} processed, "
                    f"{result.updated} updated, {result.failed} failed"
                )
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except TweetNookError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc


def tag_archive(
    target: Annotated[
        str | None,
        typer.Argument(help="Tweet ID or Twitter/X status URL to tag."),
    ] = None,
    limit: TAG_LIMIT_OPTION = None,
    test: Annotated[
        bool,
        typer.Option(
            "--test",
            help="Generate and display tags for one tweet without saving them.",
        ),
    ] = False,
    batch: Annotated[
        int | None,
        typer.Option(
            "--batch",
            min=1,
            help=TAG_BATCH_HELP,
        ),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Override the Gemini model specified in config.toml"),
    ] = None,
) -> None:
    console = configure_logging()
    try:
        config, paths = load_config()
        from tweetnook.jobs.context import locked_archive_job
        from tweetnook.tagging.tagger import (
            TaggingRunResult,
            tag_media_tweets,
            tag_pending_media_tweets,
        )

        tweet_id = None
        if target is not None:
            candidate = target.strip()
            tweet_id = candidate if candidate.isdigit() else extract_status_id_from_url(candidate)
            if tweet_id is None:
                raise ConfigError("Unsupported tag target. Use a tweet ID or Twitter/X status URL.")

        async def run_tagging() -> TaggingRunResult:
            async with locked_archive_job(config=config, paths=paths, console=console) as job:
                if tweet_id is not None:
                    pipeline = current_pipeline()
                    if pipeline is not None:
                        pipeline.add_step(
                            "tagging",
                            "Automated tags",
                            total=1,
                            unit="tweet",
                            detail=f"explicit target · {model or config.tagging.model}",
                            show_rate=False,
                            show_eta=False,
                        )
                        pipeline.start_step(
                            "tagging",
                            activity="Generating tags",
                        )
                    tagged = await tag_media_tweets(
                        store=job.store,
                        config=config,
                        paths=paths,
                        console=console,
                        tweet_ids=[tweet_id],
                        model_override=model,
                        dry_run=test,
                    )
                    if pipeline is not None:
                        summary = f"{tagged} tagged" + (" · test mode, not saved" if test else "")
                        pipeline.complete_step(
                            "tagging",
                            summary,
                            counters=summary,
                        )
                    return TaggingRunResult(processed=1, tagged=tagged, batches=1)

                return await tag_pending_media_tweets(
                    store=job.store,
                    config=config,
                    paths=paths,
                    console=console,
                    batch_limit=limit,
                    batch_size=batch,
                    model_override=model,
                    dry_run=test,
                )

        with web_pipeline(console, "tweetnook tag", paths) as pipeline:
            pipeline.add_step(
                "tagging",
                "Automated tags",
                total=1,
                unit="tweet" if tweet_id is not None else "tweets",
                detail=(
                    f"explicit target · {model or config.tagging.model}"
                    if tweet_id is not None
                    else f"{model or config.tagging.model}"
                ),
                show_rate=False,
                show_eta=False,
            )
            result = asyncio.run(run_tagging())
            if result.processed == 0:
                pipeline.skip_step("tagging", "no eligible untagged tweets")
                pipeline.final_note("No eligible untagged tweets found.")
            elif not test:
                pipeline.final_note(f"tag: {result.processed} processed, {result.tagged} tagged")
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except TweetNookError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
