"""Article refresh helpers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

import httpx
from rich.console import Console

from tweetnook.auth import ResolvedAuthBundle, resolve_auth_bundle
from tweetnook.client.base import AdaptiveRequestPacer, build_async_client
from tweetnook.client.detail import TweetDetailRequester, resolve_detail_query_ids
from tweetnook.config import AppConfig, XDGPaths
from tweetnook.enrichment.detail import (
    DetailAvailable,
    DetailExecutor,
    DetailFailed,
    DetailOutcome,
    detail_reporters,
    normalize_status_target,
)
from tweetnook.jobs.context import locked_archive_job, resolve_job_context
from tweetnook.jobs.interactive import emit_status, progress_callback, status_printer
from tweetnook.jobs.pipeline import current_pipeline


@dataclass(slots=True)
class ArticleRefreshResult:
    processed: int = 0
    updated: int = 0
    failed: int = 0


def _pipeline_outcomes(result: ArticleRefreshResult, *, final: bool = False) -> str:
    parts: list[str] = []
    if final or result.updated:
        parts.append(f"{result.updated} refreshed")
    if result.failed:
        parts.append(f"{result.failed} failed")
    return " · ".join(parts)


def normalize_article_target(value: str) -> str:
    return normalize_status_target(value, "article")


async def refresh_articles(
    *,
    targets: list[str] | None = None,
    preview_only: bool = True,
    limit: int | None = None,
    config: AppConfig | None = None,
    paths: XDGPaths | None = None,
    auth_bundle: ResolvedAuthBundle | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    console: Console | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> ArticleRefreshResult:
    config, paths = resolve_job_context(config=config, paths=paths)
    console = console or Console(stderr=True)
    pipeline = current_pipeline()
    status = None if pipeline.active else status_printer(console, "articles refresh")
    step_key = "articles"
    pipeline.add_step(
        step_key,
        "Articles",
        total=1,
        unit="tweets",
        detail="archived article rows selected for TweetDetail refresh",
        rate_unit="tweets/s",
    )

    async with locked_archive_job(config=config, paths=paths, console=console) as job:
        store = job.store
        if targets:
            tweet_ids = [normalize_article_target(target) for target in targets]
            emit_status(status, f"explicit target refresh over {len(tweet_ids)} tweets")
        else:
            tweet_ids = store.get_article_tweet_ids(preview_only=preview_only, limit=limit)
            row_label = "preview-only article rows" if preview_only else "article rows"
            limit_suffix = "" if limit is None else f" (limit {limit})"
            emit_status(status, f"refreshing {len(tweet_ids)} {row_label}{limit_suffix}")
        result = ArticleRefreshResult()
        if not tweet_ids:
            emit_status(status, "no article rows pending refresh")
            pipeline.skip_step(step_key, "no article rows requiring refresh")
            return result

        mode = (
            "explicit targets"
            if targets
            else ("preview-only article rows" if preview_only else "all article rows")
        )
        pipeline.add_step(
            step_key,
            "Articles",
            total=len(tweet_ids),
            unit="tweets",
            detail=f"{mode} · TweetDetail adaptive pacing",
            rate_unit="tweets/s",
        )
        pipeline.start_step(
            step_key,
            activity=(
                "Connecting to Twitter/X" if auth_bundle is None else "Preparing article refresh"
            ),
        )

        if auth_bundle is None:
            emit_status(status, "resolving auth bundle")
            auth_bundle = resolve_auth_bundle(config)
            pipeline.status(step_key, "Preparing article refresh")

        emit_status(status, "resolving TweetDetail query ID")
        query_store, query_ids = await resolve_detail_query_ids(paths, transport=transport)
        pipeline.status(step_key, "Refreshing an article")

        retry_status, pacing_status = detail_reporters(step_key, label="Article", status=status)
        client = build_async_client(auth_bundle, timeout=config.sync.timeout, transport=transport)
        try:
            executor = DetailExecutor(
                requester=TweetDetailRequester(
                    client, query_store, query_ids, config.sync, sleep=sleep
                ),
                pacer=AdaptiveRequestPacer(config.sync.detail_delay),
                retry_status=retry_status,
                pacing_status=pacing_status,
                sleep=sleep,
            )
            with progress_callback(
                console,
                label="articles refresh",
                total=len(tweet_ids),
                unit="tweets",
            ) as progress:

                async def targets() -> AsyncIterator[str]:
                    for index, tweet_id in enumerate(tweet_ids):
                        result.processed += 1
                        pipeline.update_step(
                            step_key,
                            completed=index,
                            activity="Refreshing an article",
                            counters=_pipeline_outcomes(result),
                        )
                        yield tweet_id

                def record(tweet_id: str, outcome: DetailOutcome) -> None:
                    if isinstance(outcome, DetailAvailable):
                        store.persist_tweet_detail(
                            tweet=outcome.tweet,
                            raw_json=outcome.payload,
                            http_status=outcome.http_status,
                        )
                        result.updated += 1
                    else:
                        result.failed += 1
                        message = (
                            outcome.error
                            if isinstance(outcome, DetailFailed)
                            else f"TweetDetail did not include focal tweet {tweet_id}."
                        )
                        if pipeline.active:
                            pipeline.issue(
                                f"Article tweet {tweet_id}: {message}",
                                dedupe_key="articles:refresh-failure",
                            )
                        elif console:
                            console.print(
                                f"article {tweet_id}: failed ({message})", highlight=False
                            )
                    pipeline.update_step(
                        step_key,
                        completed=result.processed,
                        counters=_pipeline_outcomes(result),
                    )
                    if progress is not None:
                        progress(result.processed, len(tweet_ids))

                await executor.run(targets(), tweet_id_of=str, record=record)
        finally:
            await client.aclose()
        pipeline.complete_step(
            step_key,
            _pipeline_outcomes(result, final=True),
            metrics={
                "refreshed": result.updated,
                "failed": result.failed,
            },
        )
        return result
