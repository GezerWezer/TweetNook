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
from tweetnook.jobs.steps import ARTICLES, MEDIA, RESURRECTION, TAGGING, THREADS, URLS
from tweetnook.storage import open_archive_store
from tweetnook.sync.steps import (
    add_head_step,
    add_preflight_step,
)


@dataclass(slots=True)
class SyncFollowupPlan:
    enabled: bool = True
    resurrection: bool = True
    articles: bool = True
    media: bool = True
    unfurl: bool = True
    threads: bool = True
    tagging: bool = True


def plan_sync_pipeline(
    pipeline: PipelineReporter,
    *,
    config: AppConfig,
    collections: Sequence[str],
    followups: SyncFollowupPlan | None,
) -> None:
    """Declare the complete flag-relevant sync lifecycle before any work begins."""
    add_preflight_step(pipeline, collections)
    for collection in collections:
        add_head_step(pipeline, collection)

    if followups is not None and followups.enabled:
        for enabled, step in (
            (followups.threads, THREADS),
            (followups.resurrection and not config.check_runs, RESURRECTION),
            (followups.articles, ARTICLES),
            (followups.media, MEDIA),
            (followups.unfurl, URLS),
        ):
            if enabled:
                step.declare(pipeline)
        if followups.tagging and config.tagging.enabled:
            TAGGING.declare(pipeline, detail=config.tagging.model)


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
                step_key=THREADS.key,
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

    if plan.enabled and plan.resurrection and not config.check_runs:
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
                step_key=RESURRECTION.key,
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
                step_key=ARTICLES.key,
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
                step_key=MEDIA.key,
            )
        else:
            _log_sync_followup(
                console,
                "media: "
                f"{result.processed} processed, "
                f"{result.downloaded} downloaded, "
                f"{result.failed} failed, "
                f"{result.avatars_downloaded} avatars",
            )

    skip_previews = bool(config.replay.url and config.replay.skip_link_previews)
    if plan.enabled and plan.unfurl and skip_previews:
        pipeline = current_pipeline()
        if pipeline.has_step(URLS.key):
            pipeline.skip_step(URLS.key, "link previews being disabled for this replay sync")
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
                step_key=URLS.key,
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
                step_key=TAGGING.key,
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
