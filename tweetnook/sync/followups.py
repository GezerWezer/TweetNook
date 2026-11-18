"""Work a sync starts after its collections: thread expansion, Articles, media, link previews,
tagging and resurrection, and the pipeline steps that announce them."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

import httpx
from rich.console import Console

from tweetnook.auth import ResolvedAuthBundle
from tweetnook.config import AppConfig, XDGPaths
from tweetnook.jobs.pipeline import PipelineReporter, current_pipeline
from tweetnook.jobs.resource_failure import raise_if_storage_exhausted
from tweetnook.jobs.steps import (
    ARTICLES,
    MEDIA,
    RESURRECTION,
    SMART_SEARCH,
    TAGGING,
    THREADS,
    URLS,
    StepSpec,
)
from tweetnook.smart_search.sdk import smart_search_installed
from tweetnook.storage import open_archive_store
from tweetnook.sync.steps import (
    add_head_step,
    add_preflight_step,
)


@dataclass(slots=True)
class SyncFollowupPlan:
    """Which follow-ups a sync runs. Tagging also needs it enabled in config."""

    resurrection: bool = True
    articles: bool = True
    media: bool = True
    unfurl: bool = True
    threads: bool = True


@dataclass(frozen=True, slots=True)
class _FollowupContext:
    config: AppConfig
    paths: XDGPaths
    auth_bundle: ResolvedAuthBundle
    transport: httpx.AsyncBaseTransport | None
    console: Console
    sleep: Callable[[float], Awaitable[None]]


async def _expand_threads(ctx: _FollowupContext) -> Any:
    from tweetnook.enrichment.threads import expand_threads

    replay = ctx.config.replay
    return await expand_threads(
        limit=None,
        skip_quotes=bool(replay.url and replay.skip_quote_expansion),
        config=ctx.config,
        paths=ctx.paths,
        auth_bundle=ctx.auth_bundle,
        transport=ctx.transport,
        console=ctx.console,
        sleep=ctx.sleep,
    )


async def _resurrect(ctx: _FollowupContext) -> Any:
    from tweetnook.enrichment.resurrection import (
        DEFAULT_RESURRECTION_BUDGET,
        resurrect_due_tweets,
    )

    return await resurrect_due_tweets(
        budget=DEFAULT_RESURRECTION_BUDGET,
        config=ctx.config,
        paths=ctx.paths,
        auth_bundle=ctx.auth_bundle,
        transport=ctx.transport,
        console=ctx.console,
        sleep=ctx.sleep,
    )


async def _refresh_articles(ctx: _FollowupContext) -> Any:
    from tweetnook.enrichment.articles import refresh_articles

    return await refresh_articles(
        targets=None,
        preview_only=True,
        limit=None,
        config=ctx.config,
        paths=ctx.paths,
        auth_bundle=ctx.auth_bundle,
        transport=ctx.transport,
        console=ctx.console,
        sleep=ctx.sleep,
    )


async def _download_media(ctx: _FollowupContext) -> Any:
    from tweetnook.enrichment.media import download_media

    return await download_media(
        limit=None,
        photos_only=False,
        retry_failed=False,
        config=ctx.config,
        paths=ctx.paths,
        console=ctx.console,
    )


async def _unfurl(ctx: _FollowupContext) -> Any:
    from tweetnook.enrichment.unfurl import unfurl_urls

    return await unfurl_urls(
        limit=None, retry_failed=False, config=ctx.config, paths=ctx.paths, console=ctx.console
    )


async def _tag(ctx: _FollowupContext) -> Any:
    from tweetnook.jobs.context import locked_archive_job
    from tweetnook.tagging.tagger import tag_pending_media_tweets

    async with locked_archive_job(config=ctx.config, paths=ctx.paths, console=ctx.console) as job:
        return await tag_pending_media_tweets(
            store=job.store, config=ctx.config, paths=ctx.paths, console=ctx.console
        )


async def _smart_search(ctx: _FollowupContext) -> Any:
    from tweetnook.jobs.context import archive_job
    from tweetnook.smart_search.indexer import index_media_embeddings

    with archive_job(config=ctx.config, paths=ctx.paths, console=ctx.console) as job:
        return index_media_embeddings(
            job.store,
            ctx.paths,
            ctx.config,
            pipeline=current_pipeline(),
        )


@dataclass(frozen=True, slots=True)
class _Followup:
    step: StepSpec
    running: str  # "running <this>" in console output
    label: str  # "<this> failed"
    command: str | None  # the command to run it later by hand, when it fails
    run: Callable[[_FollowupContext], Awaitable[Any]]
    summary: Callable[[Any], str]


_THREADS = _Followup(
    THREADS,
    "threads expand",
    "threads expand",
    "tweetnook threads expand",
    _expand_threads,
    lambda r: f"threads: {r.processed} processed, {r.expanded} expanded, "
    f"{r.skipped} skipped, {r.failed} failed",
)
_RESURRECTION = _Followup(
    RESURRECTION,
    "resurrection checks",
    "resurrection",
    None,
    _resurrect,
    lambda r: f"resurrection: {r.attempted} checked, {r.resurrected} returned, "
    f"{r.still_unavailable} still unavailable, {r.transient_failures} transient failures, "
    f"{r.remaining_due} due",
)
_ARTICLES = _Followup(
    ARTICLES,
    "articles refresh",
    "articles refresh",
    "tweetnook articles refresh",
    _refresh_articles,
    lambda r: f"articles: {r.processed} processed, {r.updated} refreshed, {r.failed} failed",
)
_MEDIA = _Followup(
    MEDIA,
    "media download",
    "media download",
    "tweetnook media download",
    _download_media,
    lambda r: f"media: {r.processed} processed, {r.downloaded} downloaded, "
    f"{r.failed} failed, {r.avatars_downloaded} avatars",
)
_UNFURL = _Followup(
    URLS,
    "unfurl",
    "unfurl",
    "tweetnook unfurl",
    _unfurl,
    lambda r: f"unfurl: {r.processed} processed, {r.updated} updated, {r.failed} failed",
)
_TAGGING = _Followup(
    TAGGING,
    "media tagging",
    "tagging",
    "tweetnook tag",
    _tag,
    lambda r: f"tagging: {r.tagged} tagged",
)
_SMART_SEARCH = _Followup(
    SMART_SEARCH,
    "smart search",
    "smart_search",
    "tweetnook smart-search index",
    _smart_search,
    lambda r: f"smart search: {r.indexed} indexed",
)


def _selected_followups(plan: SyncFollowupPlan, config: AppConfig) -> list[_Followup]:
    """The follow-ups this sync runs, in order; the plan and the run both use this list."""
    return [
        followup
        for followup, wanted in (
            (_THREADS, plan.threads),
            # While the availability check runs, it owns retrying unavailable tweets.
            (_RESURRECTION, plan.resurrection and not config.check_runs),
            (_ARTICLES, plan.articles),
            (_MEDIA, plan.media),
            (_UNFURL, plan.unfurl),
            (_TAGGING, config.tagging.enabled),
            (_SMART_SEARCH, config.smart_search.enabled and smart_search_installed()),
        )
        if wanted
    ]


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
    if followups is not None:
        for followup in _selected_followups(followups, config):
            detail = (
                config.tagging.model
                if followup is _TAGGING
                else (config.smart_search.model if followup is _SMART_SEARCH else None)
            )
            followup.step.declare(pipeline, detail=detail)


def _followup_failed(console: Console, followup: _Followup, error: Exception) -> None:
    later = f"; run '{followup.command}' later" if followup.command else ""
    message = f"sync follow-up {followup.label} failed{later} ({error})"
    pipeline = current_pipeline()
    if not pipeline.active:
        console.print(f"[yellow]{message}[/yellow]")
        return
    if pipeline.has_step(followup.step.key):
        pipeline.fail_step(followup.step.key, message, recoverable=True)
    pipeline.issue(message, dedupe_key=f"followup:{followup.step.key}")


def _log_sync_followup(console: Console, message: str) -> None:
    if not current_pipeline().active:
        console.print(f"sync follow-up: {message}", highlight=False)


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
    """Run each selected follow-up in turn. One failing doesn't stop the rest or fail the
    sync, except running out of storage, which stops everything."""
    ctx = _FollowupContext(config, paths, auth_bundle, transport, console, sleep)
    for followup in _selected_followups(plan, config):
        if followup is _UNFURL and config.replay.url and config.replay.skip_link_previews:
            pipeline = current_pipeline()
            if pipeline.has_step(URLS.key):
                pipeline.skip_step(URLS.key, "link previews being disabled for this replay sync")
            _log_sync_followup(console, "link previews skipped by developer replay setting")
            continue
        _log_sync_followup(console, f"running {followup.running}")
        try:
            result = await followup.run(ctx)
        except Exception as exc:
            raise_if_storage_exhausted(exc)
            _followup_failed(console, followup, exc)
        else:
            _log_sync_followup(console, followup.summary(result))

    if not current_pipeline().active:
        from tweetnook.jobs.reminders import print_pending_archive_enrichment_reminder

        store = open_archive_store(paths, create=False, config=config)
        if store is not None:
            try:
                print_pending_archive_enrichment_reminder(console, store)
            finally:
                store.close()
