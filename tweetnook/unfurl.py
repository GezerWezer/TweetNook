"""URL unfurl helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from urllib.parse import urlsplit

import httpx
from rich.console import Console

from tweetnook.config import DEFAULT_USER_AGENT, AppConfig, XDGPaths
from tweetnook.extractor import canonicalize_url
from tweetnook.interactive import emit_status, progress_callback, status_printer
from tweetnook.jobs import locked_archive_job
from tweetnook.pipeline import current_pipeline
from tweetnook.utils import utc_now

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_CANONICAL_RE = re.compile(
    r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_META_RE = re.compile(
    r'<meta[^>]+(?:name|property)=["\']([^"\']+)["\'][^>]+content=["\']([^"\']*)["\']',
    re.IGNORECASE,
)
_UPDATE_BATCH_SIZE = 100


@dataclass(slots=True)
class UrlUnfurlResult:
    processed: int = 0
    updated: int = 0
    failed: int = 0


def _clean_html_text(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = " ".join(unescape(value).split())
    return cleaned or None


def _extract_html_metadata(html: str) -> tuple[str | None, str | None, str | None, str | None]:
    title_match = _TITLE_RE.search(html)
    canonical_match = _CANONICAL_RE.search(html)
    title = _clean_html_text(title_match.group(1) if title_match else None)
    canonical_url = _clean_html_text(canonical_match.group(1) if canonical_match else None)
    meta: dict[str, str] = {}
    for key, value in _META_RE.findall(html):
        lowered = key.strip().lower()
        if lowered not in meta:
            meta[lowered] = value
    description = _clean_html_text(
        meta.get("description") or meta.get("og:description") or meta.get("twitter:description")
    )
    site_name = _clean_html_text(
        meta.get("og:site_name") or meta.get("application-name") or meta.get("twitter:site")
    )
    return title, description, site_name, canonical_url


async def unfurl_urls(
    *,
    limit: int | None = None,
    retry_failed: bool = False,
    config: AppConfig | None = None,
    paths: XDGPaths | None = None,
    console: Console | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> UrlUnfurlResult:
    console = console or Console(stderr=True)
    pipeline = current_pipeline()
    status = None if pipeline is not None else status_printer(console, "unfurl")
    step_key = "urls"
    if pipeline is not None:
        pipeline.add_step(
            step_key,
            "URLs",
            total=1,
            unit="URLs",
            detail="saved URLs selected for redirect and canonical metadata refresh",
            rate_unit="URLs/s",
        )
    async with locked_archive_job(config=config, paths=paths, console=console) as job:
        config = job.config
        store = job.store
        states = {"pending"}
        if retry_failed:
            states.add("failed")
        emit_status(status, "loading saved URL rows")
        rows = store.list_url_rows(states=states, limit=limit)
        result = UrlUnfurlResult()
        if not rows:
            emit_status(status, "no URL rows pending unfurl")
            if pipeline is not None:
                pipeline.skip_step(step_key, "no saved URLs requiring metadata")
            return result
        mode_suffix = " (including failed)" if retry_failed else ""
        limit_suffix = "" if limit is None else f" (limit {limit})"
        emit_status(
            status,
            f"fetching metadata for {len(rows)} saved URLs{mode_suffix}{limit_suffix}",
        )
        if pipeline is not None:
            scope = "saved URLs · redirects followed · canonical metadata persisted"
            if retry_failed:
                scope += " · retrying failed rows"
            first_url = (
                rows[0].get("final_url")
                or rows[0].get("expanded_url")
                or rows[0].get("canonical_url")
                or rows[0].get("url")
                or "unknown URL"
            )
            first_host = urlsplit(str(first_url)).netloc or str(first_url)
            pipeline.add_step(
                step_key,
                "URLs",
                total=len(rows),
                unit="URLs",
                detail=scope,
                rate_unit="URLs/s",
            )
            pipeline.start_step(
                step_key,
                activity=f"Fetching metadata from {first_host}",
                counters="0 processed · 0 updated · 0 failed",
            )

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=max(config.sync.timeout, 30.0),
            transport=transport,
            headers={
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
            },
        ) as client:
            pending_updates: list[dict[str, object]] = []

            def flush_updates() -> None:
                if not pending_updates:
                    return
                updates = pending_updates.copy()
                store.merge_rows(updates)
                job.mark_dirty(rows=len(updates), batches=1)
                pending_updates.clear()

            with progress_callback(
                console,
                label="unfurl",
                total=len(rows),
                unit="urls",
            ) as progress:
                for index, row in enumerate(rows, start=1):
                    try:
                        result.processed += 1
                        request_url = (
                            row.get("final_url")
                            or row.get("expanded_url")
                            or row.get("canonical_url")
                            or row.get("url")
                        )
                        if pipeline is not None:
                            host = (
                                urlsplit(str(request_url)).netloc
                                if request_url is not None
                                else "missing URL"
                            )
                            pipeline.update_step(
                                step_key,
                                completed=index - 1,
                                activity=f"Fetching metadata from {host or request_url}",
                                counters=(
                                    f"{index - 1} processed · {result.updated} updated · "
                                    f"{result.failed} failed"
                                ),
                            )
                        if not isinstance(request_url, str) or not request_url:
                            pending_updates.append(
                                store.build_url_unfurl_update(
                                    row,
                                    http_status=row.get("http_status"),
                                    final_url=row.get("final_url"),
                                    canonical_url=row.get("canonical_url"),
                                    title=row.get("title"),
                                    description=row.get("description"),
                                    site_name=row.get("site_name"),
                                    content_type=row.get("content_type"),
                                    unfurl_state="failed",
                                    last_fetched_at=utc_now(),
                                    download_error="missing URL to unfurl",
                                )
                            )
                            result.failed += 1
                            if len(pending_updates) >= _UPDATE_BATCH_SIZE:
                                flush_updates()
                            continue

                        try:
                            response = await client.get(request_url)
                            response.raise_for_status()
                            final_url = str(response.url)
                            content_type = response.headers.get("content-type")
                            title = row.get("title")
                            description = row.get("description")
                            site_name = row.get("site_name")
                            canonical_url = row.get("canonical_url")
                            if content_type and "html" in content_type.lower():
                                html = response.text[:500000]
                                (
                                    parsed_title,
                                    parsed_description,
                                    parsed_site_name,
                                    parsed_canonical_url,
                                ) = _extract_html_metadata(html)
                                title = parsed_title or title
                                description = parsed_description or description
                                site_name = parsed_site_name or site_name
                                canonical_url = (
                                    canonicalize_url(parsed_canonical_url)
                                    or canonicalize_url(final_url)
                                    or canonical_url
                                )
                            else:
                                canonical_url = canonical_url or canonicalize_url(final_url)
                            pending_updates.append(
                                store.build_url_unfurl_update(
                                    row,
                                    http_status=response.status_code,
                                    final_url=final_url,
                                    canonical_url=canonical_url,
                                    title=title,
                                    description=description,
                                    site_name=site_name,
                                    content_type=content_type,
                                    unfurl_state="done",
                                    last_fetched_at=utc_now(),
                                    download_error=None,
                                )
                            )
                            result.updated += 1
                        except Exception as exc:
                            pending_updates.append(
                                store.build_url_unfurl_update(
                                    row,
                                    http_status=getattr(
                                        getattr(exc, "response", None),
                                        "status_code",
                                        None,
                                    ),
                                    final_url=row.get("final_url"),
                                    canonical_url=row.get("canonical_url"),
                                    title=row.get("title"),
                                    description=row.get("description"),
                                    site_name=row.get("site_name"),
                                    content_type=row.get("content_type"),
                                    unfurl_state="failed",
                                    last_fetched_at=utc_now(),
                                    download_error=str(exc),
                                )
                            )
                            result.failed += 1
                            if pipeline is not None:
                                pipeline.issue(
                                    f"URL {row['row_key']}: {exc}",
                                    dedupe_key="urls:unfurl-failure",
                                )
                            elif console:
                                console.print(
                                    f"url {row['row_key']}: failed ({exc})",
                                    highlight=False,
                                )
                        if len(pending_updates) >= _UPDATE_BATCH_SIZE:
                            flush_updates()
                    finally:
                        if pipeline is not None:
                            pipeline.update_step(
                                step_key,
                                completed=index,
                                counters=(
                                    f"{index} processed · {result.updated} updated · "
                                    f"{result.failed} failed"
                                ),
                            )
                        if progress is not None:
                            progress(index, len(rows))

            flush_updates()
        if pipeline is not None:
            pipeline.complete_step(
                step_key,
                f"{result.processed} processed · {result.updated} updated · {result.failed} failed",
            )
        return result
