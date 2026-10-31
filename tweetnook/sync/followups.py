"""Work a sync starts after its collections: thread expansion, Articles, media, link previews,
tagging and resurrection, and the pipeline steps that announce them."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

import httpx
from rich.console import Console

from tweetnook.auth import ResolvedAuthBundle
from tweetnook.config import AppConfig, XDGPaths
from tweetnook.jobs.pipeline import PipelineReporter, current_pipeline
from tweetnook.jobs.resource_failure import raise_if_storage_exhausted
from tweetnook.storage import open_archive_store


@dataclass(slots=True)
class SyncFollowupPlan:
    enabled: bool = True
    resurrection: bool = True
    articles: bool = True
    media: bool = True
    unfurl: bool = True
    threads: bool = True
    tagging: bool = True


def check_owns_resurrection(config: AppConfig) -> bool:
    """The check retries unavailable tweets when it runs with FxTwitter; otherwise
    sync keeps doing it, as before the check existed."""
    from tweetnook.client.fxtwitter import fx_enabled

    return config.check.enabled and fx_enabled(config)


def plan_sync_pipeline(
    pipeline: PipelineReporter,
    *,
    config: AppConfig,
    collections: Sequence[str],
    followups: SyncFollowupPlan | None,
    head_only: bool,
) -> None:
    """Declare the complete flag-relevant sync lifecycle before any work begins."""
    pipeline.add_step(
        "preflight:" + ",".join(collections),
        "Prepare",
        total=3 + len(collections),
        unit="checks",
        detail=f"sign-in · archive owner · {len(collections)} collection checks",
        rate_unit="checks/s",
    )
    stop_policy = "head timeline pass · each page committed with its resume cursor"
    for collection in collections:
        pipeline.add_step(
            f"sync:{collection}:head",
            collection.title(),
            total=1,
            unit="current page",
            detail=stop_policy,
            show_rate=False,
            show_eta=False,
            show_progress=False,
        )
        if not head_only and collection not in {"bookmarks", "likes"}:
            pipeline.add_step(
                f"sync:{collection}:backfill",
                f"{collection.title()} history",
                total=1,
                unit="current page",
                detail="saved older-history cursor, when one exists",
                show_rate=False,
                show_eta=False,
                show_progress=False,
            )

    if followups is not None and followups.enabled:
        followup_steps = (
            (followups.threads, "threads", "Threads", "candidates"),
            (
                followups.resurrection and not check_owns_resurrection(config),
                "resurrection",
                "Unavailable tweets",
                "tweets",
            ),
            (followups.articles, "articles", "Articles", "tweets"),
            (followups.media, "media", "Media", "items"),
            (followups.unfurl, "urls", "Link previews", "URLs"),
        )
        for enabled, key, title, unit in followup_steps:
            if enabled:
                pipeline.add_step(
                    key,
                    title,
                    total=1,
                    unit=unit,
                    detail="pending archive work is selected at this step",
                )
        if followups.tagging and config.tagging.enabled:
            pipeline.add_step(
                "tagging",
                "Automated tags",
                total=1,
                unit="tweets",
                detail=config.tagging.model,
                show_rate=False,
                show_eta=False,
            )


def _log_embedding_warning(
    console: Console | None, message: str, *, step_key: str | None = None
) -> None:
    pipeline = current_pipeline()
    if pipeline.active:
        if step_key is not None and pipeline.has_step(step_key):
            pipeline.fail_step(step_key, message, recoverable=True)
        else:
            active = pipeline.active_step
            if active is not None and active.state == "active":
                pipeline.fail_step(active.key, message, recoverable=True)
        pipeline.issue(message, dedupe_key=f"followup:{message.split(':', 1)[0]}")
    elif console is not None:
        console.print(f"[yellow]{message}[/yellow]")


def _log_sync_followup(console: Console | None, message: str) -> None:
    if current_pipeline().active:
        return
    if console is not None:
        console.print(f"sync follow-up: {message}", highlight=False)


async def _run_followup_threads(
    *,
    config: AppConfig,
    paths: XDGPaths,
    auth_bundle: ResolvedAuthBundle,
    transport: httpx.AsyncBaseTransport | None,
    console: Console,
    sleep: Callable[[float], Awaitable[None]],
):
    from tweetnook.enrichment.threads import expand_threads

    return await expand_threads(
        limit=None,
        skip_quotes=bool(config.replay.url and config.replay.skip_quote_expansion),
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=transport,
        console=console,
        sleep=sleep,
    )


async def _run_followup_articles(
    *,
    config: AppConfig,
    paths: XDGPaths,
    auth_bundle: ResolvedAuthBundle,
    transport: httpx.AsyncBaseTransport | None,
    console: Console,
    sleep: Callable[[float], Awaitable[None]],
):
    from tweetnook.enrichment.articles import refresh_articles

    return await refresh_articles(
        targets=None,
        preview_only=True,
        limit=None,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=transport,
        console=console,
        sleep=sleep,
    )


async def _run_followup_media(
    *,
    config: AppConfig,
    paths: XDGPaths,
    console: Console,
):
    from tweetnook.enrichment.media import download_media

    return await download_media(
        limit=None,
        photos_only=False,
        retry_failed=False,
        config=config,
        paths=paths,
        console=console,
    )


async def _run_followup_unfurl(
    *,
    config: AppConfig,
    paths: XDGPaths,
    console: Console,
):
    from tweetnook.enrichment.unfurl import unfurl_urls

    return await unfurl_urls(
        limit=None,
        retry_failed=False,
        config=config,
        paths=paths,
        console=console,
    )


async def _run_followup_tagging(
    *,
    config: AppConfig,
    paths: XDGPaths,
    console: Console,
):
    from tweetnook.jobs.context import locked_archive_job
    from tweetnook.tagging.tagger import tag_pending_media_tweets

    async with locked_archive_job(config=config, paths=paths, console=console) as job:
        result = await tag_pending_media_tweets(
            store=job.store,
            config=config,
            paths=paths,
            console=console,
        )
        return result.tagged


async def run_auto_followups(
    *,
    plan: SyncFollowupPlan,
    config: AppConfig,
    paths: XDGPaths,
    auth_bundle: ResolvedAuthBundle,
    transport: httpx.AsyncBaseTransport | None,
    console: Console,
    sleep: Callable[[float], Awaitable[None]],
) -> None:
    if plan.enabled and plan.threads:
        _log_sync_followup(console, "running threads expand")
        try:
            result = await _run_followup_threads(
                config=config,
                paths=paths,
                auth_bundle=auth_bundle,
                transport=transport,
                console=console,
                sleep=sleep,
            )
        except Exception as exc:
            raise_if_storage_exhausted(exc)
            _log_embedding_warning(
                console,
                "sync follow-up threads expand failed; "
                f"run 'tweetnook threads expand' later ({exc})",
                step_key="threads",
            )
        else:
            _log_sync_followup(
                console,
                "threads: "
                f"{result.processed} processed, "
                f"{result.expanded} expanded, "
                f"{result.skipped} skipped, "
                f"{result.failed} failed",
            )

    if plan.enabled and plan.resurrection and not check_owns_resurrection(config):
        _log_sync_followup(console, "running resurrection checks")
        try:
            from tweetnook.enrichment.resurrection import (
                DEFAULT_RESURRECTION_BUDGET,
                resurrect_due_tweets,
            )

            result = await resurrect_due_tweets(
                budget=DEFAULT_RESURRECTION_BUDGET,
                config=config,
                paths=paths,
                auth_bundle=auth_bundle,
                transport=transport,
                console=console,
                sleep=sleep,
            )
        except Exception as exc:
            raise_if_storage_exhausted(exc)
            _log_embedding_warning(
                console,
                f"sync follow-up resurrection failed ({exc})",
                step_key="resurrection",
            )
        else:
            _log_sync_followup(
                console,
                "resurrection: "
                f"{result.attempted} checked, "
                f"{result.resurrected} returned, "
                f"{result.still_unavailable} still unavailable, "
                f"{result.transient_failures} transient failures, "
                f"{result.remaining_due} due",
            )

    if plan.enabled and plan.articles:
        _log_sync_followup(console, "running articles refresh")
        try:
            result = await _run_followup_articles(
                config=config,
                paths=paths,
                auth_bundle=auth_bundle,
                transport=transport,
                console=console,
                sleep=sleep,
            )
        except Exception as exc:
            raise_if_storage_exhausted(exc)
            _log_embedding_warning(
                console,
                "sync follow-up articles refresh failed; "
                f"run 'tweetnook articles refresh' later ({exc})",
                step_key="articles",
            )
        else:
            _log_sync_followup(
                console,
                "articles: "
                f"{result.processed} processed, "
                f"{result.updated} refreshed, "
                f"{result.failed} failed",
            )

    if plan.enabled and plan.media:
        _log_sync_followup(console, "running media download")
        try:
            result = await _run_followup_media(
                config=config,
                paths=paths,
                console=console,
            )
        except Exception as exc:
            raise_if_storage_exhausted(exc)
            _log_embedding_warning(
                console,
                "sync follow-up media download failed; "
                f"run 'tweetnook media download' later ({exc})",
                step_key="media",
            )
        else:
            _log_sync_followup(
                console,
                "media: "
                f"{result.processed} processed, "
                f"{result.downloaded} downloaded, "
                f"{result.skipped} skipped, "
                f"{result.failed} failed",
            )

    skip_previews = bool(config.replay.url and config.replay.skip_link_previews)
    if plan.enabled and plan.unfurl and skip_previews:
        pipeline = current_pipeline()
        if pipeline.has_step("urls"):
            pipeline.skip_step("urls", "link previews being disabled for this replay sync")
        _log_sync_followup(console, "link previews skipped by developer replay setting")
    if plan.enabled and plan.unfurl and not skip_previews:
        _log_sync_followup(console, "running unfurl")
        try:
            result = await _run_followup_unfurl(
                config=config,
                paths=paths,
                console=console,
            )
        except Exception as exc:
            raise_if_storage_exhausted(exc)
            _log_embedding_warning(
                console,
                f"sync follow-up unfurl failed; run 'tweetnook unfurl' later ({exc})",
                step_key="urls",
            )
        else:
            _log_sync_followup(
                console,
                "unfurl: "
                f"{result.processed} processed, "
                f"{result.updated} updated, "
                f"{result.failed} failed",
            )

    if plan.enabled and plan.tagging and config.tagging.enabled:
        _log_sync_followup(console, "running media tagging")
        try:
            tagged_count = await _run_followup_tagging(
                config=config,
                paths=paths,
                console=console,
            )
        except Exception as exc:
            raise_if_storage_exhausted(exc)
            _log_embedding_warning(
                console,
                f"sync follow-up tagging failed; run 'tweetnook tag' later ({exc})",
                step_key="tagging",
            )
        else:
            _log_sync_followup(
                console,
                f"tagging: {tagged_count} tagged",
            )

    if not current_pipeline().active:
        from tweetnook.jobs.reminders import print_pending_archive_enrichment_reminder

        store = open_archive_store(paths, create=False, config=config)
        if store is not None:
            try:
                print_pending_archive_enrichment_reminder(console, store)
            finally:
                store.close()
