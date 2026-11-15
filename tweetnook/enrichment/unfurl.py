"""URL unfurl helpers."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from rich.console import Console

from tweetnook.client.base import DEFAULT_USER_AGENT
from tweetnook.config import AppConfig, XDGPaths
from tweetnook.core.dev_replay import client_transport
from tweetnook.core.exceptions import error_text
from tweetnook.core.extractor import canonicalize_url
from tweetnook.core.utils import utc_now
from tweetnook.jobs.context import locked_archive_job
from tweetnook.jobs.interactive import emit_status, progress_callback, status_printer
from tweetnook.jobs.pipeline import current_pipeline
from tweetnook.jobs.steps import URLS

_REMOTE_FAILURES = (httpx.HTTPError, httpx.InvalidURL)
# Title, description and canonical link sit in the page head, well inside this many bytes.
_HTML_READ_LIMIT = 500_000
_MIN_CLIENT_TIMEOUT = 30.0
# Each URL gets this many client timeouts in total before it is recorded as failed.
_DEADLINE_TIMEOUTS = 2.0


@dataclass(slots=True)
class UrlUnfurlResult:
    processed: int = 0
    updated: int = 0
    failed: int = 0


def _pipeline_outcomes(result: UrlUnfurlResult, *, final: bool = False) -> str:
    parts: list[str] = []
    if final or result.updated:
        parts.append(f"{result.updated} updated")
    if result.failed:
        parts.append(f"{result.failed} failed")
    return " · ".join(parts)


def _url_host(url: object) -> str:
    """Name a URL in progress text. A saved URL can be too malformed to parse, and that belongs
    to the request's failure handling rather than stopping the job here."""
    try:
        return urlsplit(str(url)).netloc or str(url)
    except ValueError:
        return str(url)


class _MetadataParser(HTMLParser):
    """The first title, canonical link and value of each meta tag, in any attribute order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self.canonical: str | None = None
        self.meta: dict[str, str] = {}
        self._title_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value for name, value in reversed(attrs) if value is not None}
        if tag == "title" and self.title is None and self._title_parts is None:
            self._title_parts = []
        elif tag == "link" and self.canonical is None:
            if "canonical" in values.get("rel", "").lower().split() and values.get("href"):
                self.canonical = values["href"]
        elif tag == "meta" and "content" in values:
            key = values.get("name") or values.get("property")
            if key:
                self.meta.setdefault(key.strip().lower(), values["content"])

    def handle_data(self, data: str) -> None:
        if self._title_parts is not None:
            self._title_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self._title_parts is not None:
            self.title = "".join(self._title_parts)
            self._title_parts = None


def _clean_html_text(value: str | None) -> str | None:
    if not value:
        return None
    return " ".join(value.split()) or None


def _extract_html_metadata(html: str) -> tuple[str | None, str | None, str | None, str | None]:
    parser = _MetadataParser()
    # Older html.parser versions raise on a bogus marked section in a page we don't control; what
    # was read before it is still good.
    with suppress(AssertionError):
        parser.feed(html)
        parser.close()
    meta = parser.meta
    description = _clean_html_text(
        meta.get("description") or meta.get("og:description") or meta.get("twitter:description")
    )
    site_name = _clean_html_text(
        meta.get("og:site_name") or meta.get("application-name") or meta.get("twitter:site")
    )
    return (
        _clean_html_text(parser.title),
        description,
        site_name,
        _clean_html_text(parser.canonical),
    )


def _resolve_canonical(href: str | None, final_url: str) -> str | None:
    """A canonical link's identity, which may be written relative to the page declaring it."""
    if not href:
        return None
    try:
        return canonicalize_url(urljoin(final_url, href))
    except ValueError:
        return None


def _decode_html(body: bytes, charset: str | None) -> str:
    try:
        return body.decode(charset or "utf-8", errors="replace")
    except LookupError:
        # A page can declare a charset Python doesn't know.
        return body.decode("utf-8", errors="replace")


async def _read_destination(
    client: httpx.AsyncClient, request_url: str
) -> tuple[httpx.Response, str | None, str | None]:
    """Fetch a URL's status, headers and the start of its HTML.

    Only HTML is read, and only up to the metadata limit: a saved URL can point at a video or
    an archive, and reading the whole body would download it just to learn it isn't a page.
    """

    async with client.stream("GET", request_url) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type")
        if not content_type or "html" not in content_type.lower():
            return response, content_type, None
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body += chunk
            if len(body) >= _HTML_READ_LIMIT:
                break
        return (
            response,
            content_type,
            _decode_html(bytes(body[:_HTML_READ_LIMIT]), response.charset_encoding),
        )


async def _fetch_destination(
    client: httpx.AsyncClient,
    request_url: str,
    row: dict[str, object],
    *,
    deadline: float,
) -> dict[str, Any]:
    """Fetch a URL and read what it says about itself, falling back to the saved values."""

    # The client's timeout bounds each connect and read, so a server that keeps sending
    # slowly never trips it. The deadline bounds the whole fetch.
    try:
        async with asyncio.timeout(deadline):
            response, content_type, html = await _read_destination(client, request_url)
    except TimeoutError as exc:
        raise httpx.TimeoutException(
            f"gave up after {deadline:g}s without a complete answer"
        ) from exc
    final_url = str(response.url)
    title = row.get("title")
    description = row.get("description")
    site_name = row.get("site_name")
    canonical_url = row.get("canonical_url")
    if html is not None:
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
            _resolve_canonical(parsed_canonical_url, final_url)
            or canonicalize_url(final_url)
            or canonical_url
        )
    else:
        canonical_url = canonical_url or canonicalize_url(final_url)
    return {
        "http_status": response.status_code,
        "final_url": final_url,
        "canonical_url": canonical_url,
        "title": title,
        "description": description,
        "site_name": site_name,
        "content_type": content_type,
    }


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
    status = None if pipeline.active else status_printer(console, "unfurl")
    step_key = URLS.key
    URLS.declare(pipeline)
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
            pipeline.skip_step(step_key, "no saved URLs requiring metadata")
            return result
        mode_suffix = " (including failed)" if retry_failed else ""
        limit_suffix = "" if limit is None else f" (limit {limit})"
        emit_status(
            status,
            f"fetching metadata for {len(rows)} saved URLs{mode_suffix}{limit_suffix}",
        )
        if pipeline.active:
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
            first_host = _url_host(first_url)
            URLS.declare(pipeline, total=len(rows), detail=scope)
            pipeline.start_step(
                step_key,
                activity=f"Fetching {first_host}",
            )

        client_timeout = max(config.sync.timeout, _MIN_CLIENT_TIMEOUT)
        deadline = client_timeout * _DEADLINE_TIMEOUTS
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=client_timeout,
            transport=client_transport(transport),
            headers={
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
            },
        ) as client:
            # Each URL is saved as it finishes: a fetch takes far longer than its write, and a
            # worker killed mid-run must not fetch the same URLs again on the next run.
            def save(update: dict[str, object]) -> None:
                store.merge_rows([update])

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
                        if pipeline.active:
                            host = _url_host(request_url) if request_url else "missing URL"
                            pipeline.update_step(
                                step_key,
                                completed=index - 1,
                                activity=f"Fetching {host}",
                                counters=_pipeline_outcomes(result),
                            )
                        if not isinstance(request_url, str) or not request_url:
                            save(
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
                            continue

                        try:
                            fetched = await _fetch_destination(
                                client, request_url, row, deadline=deadline
                            )
                        except _REMOTE_FAILURES as exc:
                            save(
                                store.build_url_unfurl_update(
                                    row,
                                    http_status=(
                                        exc.response.status_code
                                        if isinstance(exc, httpx.HTTPStatusError)
                                        else None
                                    ),
                                    final_url=row.get("final_url"),
                                    canonical_url=row.get("canonical_url"),
                                    title=row.get("title"),
                                    description=row.get("description"),
                                    site_name=row.get("site_name"),
                                    content_type=row.get("content_type"),
                                    unfurl_state="failed",
                                    last_fetched_at=utc_now(),
                                    download_error=error_text(exc),
                                )
                            )
                            result.failed += 1
                            if pipeline.active:
                                pipeline.issue(
                                    f"URL {_url_host(request_url)}: {error_text(exc)}",
                                    dedupe_key="urls:unfurl-failure",
                                )
                            elif console:
                                console.print(
                                    f"url {_url_host(request_url)}: failed ({error_text(exc)})",
                                    highlight=False,
                                )
                        else:
                            save(
                                store.build_url_unfurl_update(
                                    row,
                                    unfurl_state="done",
                                    last_fetched_at=utc_now(),
                                    download_error=None,
                                    **fetched,
                                )
                            )
                            result.updated += 1
                    finally:
                        pipeline.update_step(
                            step_key,
                            completed=index,
                            counters=_pipeline_outcomes(result),
                        )
                        if progress is not None:
                            progress(index, len(rows))

        pipeline.complete_step(
            step_key,
            _pipeline_outcomes(result, final=True),
            metrics={
                "updated": result.updated,
                "failed": result.failed,
            },
        )
        return result
