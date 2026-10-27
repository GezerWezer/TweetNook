"""Read commands: view, search, export and stats."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import typer
from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from tweetnook.cli.common import (
    _configure_logging,
    _normalize_collection_or_exit,
    _open_store_for_read,
    _with_auto_optimize,
)
from tweetnook.core.collection_names import display_collection_name, normalize_collection_name
from tweetnook.export import export_json_archive
from tweetnook.export.common import default_export_path
from tweetnook.stats import build_stats_report
from tweetnook.stats.render_cli import render_stats_report
from tweetnook.storage.search_query import SearchQueryError, parse_search_query, search_posts
from tweetnook.storage.search_query import sort_search_results as _sort_search_results

export_app = typer.Typer(no_args_is_help=True, help="Export the local archive.")
view_app = typer.Typer(no_args_is_help=True, help="Render archived tweets in the terminal.")
SEARCH_TYPE_HELP = "Comma-delimited search result types: post (default), article."
SEARCH_COLLECTION_HELP = "Comma-delimited collections: bookmark, like, tweet."
SEARCH_SORT_HELP = "Search result sort: relevance, newest, oldest."
VIEW_LIMIT_HELP = "Maximum number of rows to display."
VIEW_SORT_HELP = "Display order: newest or oldest."
EXPORT_COLLECTION_HELP = "Collection to export: bookmarks, likes, tweets, or all."
EXPORT_OUT_HELP = "Destination file path. Defaults to the exports/ directory."
SEARCH_QUERY_HELP = "Search query text."
SEARCH_LIMIT_HELP = "Maximum number of results to return."
# Keep the user-facing flag as --type, but map it onto internal search-result kinds so
# search code does not collide with storage-level record_type/type terminology.
SEARCH_TYPE_ALIASES = {
    "post": "post",
    "posts": "post",
    "article": "article",
    "articles": "article",
}
SEARCH_SORT_OPTION = Annotated[
    Literal["relevance", "newest", "oldest"],
    typer.Option("--sort", help=SEARCH_SORT_HELP),
]
VIEW_LIMIT_OPTION = Annotated[int, typer.Option("--limit", help=VIEW_LIMIT_HELP)]
VIEW_SORT_OPTION = Annotated[str, typer.Option("--sort", help=VIEW_SORT_HELP)]
EXPORT_COLLECTION_OPTION = Annotated[str, typer.Option("--collection", help=EXPORT_COLLECTION_HELP)]
EXPORT_OUT_OPTION = Annotated[Path | None, typer.Option("--out", help=EXPORT_OUT_HELP)]
SEARCH_QUERY_ARGUMENT = Annotated[str, typer.Argument(help=SEARCH_QUERY_HELP)]
SEARCH_LIMIT_OPTION = Annotated[int, typer.Option("--limit", help=SEARCH_LIMIT_HELP)]


def _parse_search_types(value: str | None, console: Console) -> set[str] | None:
    if value is None:
        return None
    normalized: set[str] = set()
    invalid: list[str] = []
    for raw_part in value.split(","):
        part = raw_part.strip().lower()
        if not part:
            continue
        search_type = SEARCH_TYPE_ALIASES.get(part)
        if search_type is None:
            invalid.append(raw_part.strip())
            continue
        normalized.add(search_type)
    if invalid:
        allowed = ", ".join(sorted(SEARCH_TYPE_ALIASES))
        console.print(
            f"[red]Unsupported search type(s): {', '.join(invalid)}. "
            f"Expected one of: {allowed}.[/red]"
        )
        raise typer.Exit(1)
    return normalized or None


def _parse_search_collections(value: str | None, console: Console) -> set[str] | None:
    if value is None:
        return None
    normalized: set[str] = set()
    invalid: list[str] = []
    for raw_part in value.split(","):
        part = raw_part.strip()
        if not part:
            continue
        try:
            normalized.add(normalize_collection_name(part))
        except ValueError:
            invalid.append(part)
    if invalid:
        allowed = ", ".join(sorted({"bookmark", "bookmarks", "like", "likes", "tweet", "tweets"}))
        console.print(
            f"[red]Unsupported collection(s): {', '.join(invalid)}. "
            f"Expected one of: {allowed}.[/red]"
        )
        raise typer.Exit(1)
    return normalized or None


def _format_created_at(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        dt = datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y")
        local_dt = dt.astimezone()
        date_part = local_dt.strftime("%b %-d, %Y")
        time_part = local_dt.strftime("%-I:%M %p").lower()
        return f"{date_part}\n{time_part}"
    except (ValueError, TypeError):
        return raw


@dataclass(slots=True)
class _TweetListRow:
    tweet_id: str | None
    created_at: str | None
    author_username: str | None
    author_id: str | None
    text: Text
    match: str | None = None
    score: str | None = None


def _format_tweet_text(raw: str | None, *, highlight_query: str | None = None) -> Text:
    text = (raw or "").replace("\n", " ")
    rendered = _highlight_search_matches(text, highlight_query) if highlight_query else Text(text)
    if len(rendered.plain) > 280:
        rendered.truncate(280, overflow="ellipsis")
    return rendered


def _tweet_row_url(row: _TweetListRow) -> str:
    if row.author_username and row.tweet_id:
        return f"https://x.com/{row.author_username}/status/{row.tweet_id}"
    return f"https://x.com/i/web/status/{row.tweet_id or ''}"


def _render_tweet_list(
    console: Console,
    *,
    title: str,
    rows: list[_TweetListRow],
    count_line: str | None = None,
) -> None:
    include_match = any(row.match is not None for row in rows)
    table = Table(
        title=title,
        box=box.HORIZONTALS,
        show_lines=True,
    )
    if include_match:
        table.add_column("Match", style="yellow", no_wrap=True)
    table.add_column("Created", style="cyan", no_wrap=True)
    table.add_column("Author", style="green", no_wrap=True)
    table.add_column("Text", overflow="fold")
    table.add_column("URL", style="magenta", overflow="fold")

    for row in rows:
        username = row.author_username or row.author_id or "unknown"
        values: list[Any] = []
        if include_match:
            values.append(row.match or (row.score or ""))
        values.extend(
            [
                _format_created_at(row.created_at),
                f"@{username}",
                row.text,
                _tweet_row_url(row),
            ]
        )
        table.add_row(*values)

    if count_line:
        console.print(count_line)
    console.print(table)


def _render_archive_view(
    console: Console, *, collection: str, limit: int, sort: str = "newest"
) -> None:
    normalized = _normalize_collection_or_exit(collection, console)
    store, paths = _open_store_for_read(console)
    try:
        total_rows = store.count_export_rows(normalized)
        rows = _with_auto_optimize(
            store,
            paths,
            console,
            lambda s: s.export_rows(
                normalized,
                sort=sort,
                limit=limit,
                include_raw_json=False,
            ),
        )
    finally:
        store.close()

    label = display_collection_name(normalized)
    if not rows:
        console.print(f"[yellow]No archived {label} rows found.[/yellow]")
        return

    display_rows = [
        _TweetListRow(
            tweet_id=row.get("tweet_id"),
            created_at=row.get("created_at"),
            author_username=(row.get("author") or {}).get("username"),
            author_id=(row.get("author") or {}).get("id"),
            text=_format_tweet_text(row.get("text")),
        )
        for row in rows
    ]
    _render_tweet_list(
        console,
        title=f"{label} archive",
        rows=display_rows,
        count_line=f"showing {len(rows)} of {total_rows} archived {label} tweets",
    )


def _highlight_search_matches(text: str, query: str) -> Text:
    rendered = Text(text)
    tokens = [token for token in dict.fromkeys(query.split()) if token]
    if not tokens:
        return rendered
    pattern = re.compile("|".join(re.escape(token) for token in tokens), re.IGNORECASE)
    for match in pattern.finditer(text):
        rendered.stylize("black on yellow", match.start(), match.end())
    return rendered


@view_app.command("bookmarks", help="View bookmarked tweets.")
def view_bookmarks(limit: VIEW_LIMIT_OPTION = 20, sort: VIEW_SORT_OPTION = "newest") -> None:
    console = _configure_logging()
    _render_archive_view(console, collection="bookmarks", limit=limit, sort=sort)


@view_app.command("likes", help="View liked tweets.")
def view_likes(limit: VIEW_LIMIT_OPTION = 20, sort: VIEW_SORT_OPTION = "newest") -> None:
    console = _configure_logging()
    _render_archive_view(console, collection="likes", limit=limit, sort=sort)


@view_app.command("all", help="View bookmarks, likes, and authored tweets together.")
def view_all(limit: VIEW_LIMIT_OPTION = 20, sort: VIEW_SORT_OPTION = "newest") -> None:
    console = _configure_logging()
    _render_archive_view(console, collection="all", limit=limit, sort=sort)


@view_app.command("tweets", help="View authored tweets.")
def view_tweets(limit: VIEW_LIMIT_OPTION = 20, sort: VIEW_SORT_OPTION = "newest") -> None:
    console = _configure_logging()
    _render_archive_view(console, collection="tweets", limit=limit, sort=sort)


@export_app.command("json", help="Export the archive as JSON.")
def export_json(
    collection: EXPORT_COLLECTION_OPTION = "all",
    out: EXPORT_OUT_OPTION = None,
) -> None:
    console = _configure_logging()
    normalized = _normalize_collection_or_exit(collection, console)
    store, paths = _open_store_for_read(console)
    try:
        out_path = out or default_export_path(
            paths.data_dir / "exports",
            normalized,
            extension="json",
        )
        _with_auto_optimize(
            store,
            paths,
            console,
            lambda s: export_json_archive(s, collection=normalized, out_path=out_path),
        )
    finally:
        store.close()
    console.print(f"exported {display_collection_name(normalized)} archive to {out_path}")


def stats_archive(
    detailed: Annotated[
        bool,
        typer.Option(
            "--detailed",
            help=(
                "Show the full storage breakdown, all unavailable-reason rows, "
                "and zero-count maintenance queues."
            ),
        ),
    ] = False,
) -> None:
    """Show archive totals, collection coverage, sync timestamps, and storage health."""
    console = _configure_logging()
    store, _paths = _open_store_for_read(console)
    try:
        render_stats_report(console, build_stats_report(store), detailed=detailed)
    finally:
        store.close()


def search_archive(
    query: SEARCH_QUERY_ARGUMENT,
    limit: SEARCH_LIMIT_OPTION = 20,
    sort: SEARCH_SORT_OPTION = "relevance",
    type_filter: Annotated[str | None, typer.Option("--type", help=SEARCH_TYPE_HELP)] = None,
    collection_filter: Annotated[
        str | None, typer.Option("--collection", help=SEARCH_COLLECTION_HELP)
    ] = None,
) -> None:
    """Full-text search archived tweets and articles."""
    console = _configure_logging()
    store, paths = _open_store_for_read(console)
    try:
        search_types = _parse_search_types(type_filter, console) or {"post"}
        search_collections = _parse_search_collections(collection_filter, console)
        parsed_query = parse_search_query(query)

        def run_search(search_store):
            combined: list[dict[str, Any]] = []
            post_page = None
            if "post" in search_types:
                post_page = search_posts(
                    search_store,
                    query,
                    collections=search_collections,
                    sort=sort,
                    limit=limit,
                )
                for row in post_page.rows:
                    author = row.get("author") or {}
                    collections = row.get("collections") or []
                    if not collections:
                        collection = (row.get("collection") or {}).get("type")
                        collections = [collection] if collection else []
                    combined.append(
                        {
                            "tweet_id": row.get("tweet_id"),
                            "type": "post",
                            "collections": collections,
                            "author_id": author.get("id"),
                            "author_username": author.get("username"),
                            "created_at": row.get("created_at"),
                            "text": row.get("text"),
                            "match_score": row.get("match_score"),
                        }
                    )
            if "article" in search_types:
                if any(
                    clause.kind == "filter" for group in parsed_query.groups for clause in group
                ):
                    raise SearchQueryError(
                        "Structured filters apply to tweets; use filter:articles to find tweets "
                        "with attached articles."
                    )
                combined.extend(
                    search_store.search_fts(
                        query,
                        limit=limit,
                        types={"article"},
                        collections=search_collections,
                        sort=sort,
                    )
                )
            return combined, post_page

        results, post_page = _with_auto_optimize(store, paths, console, run_search)
        results = _sort_search_results(results, sort=sort)[:limit]
        if not results:
            console.print("[yellow]No results found.[/yellow]")
            return

        display_rows: list[_TweetListRow] = []
        for row in results:
            score = row.get("match_score")
            if isinstance(score, float):
                score = f"{score:.3f}"
            type_label = row.get("type") or "post"
            collections = row.get("collections") or []
            match_label = str(type_label)
            if collections:
                match_label = f"{match_label} · {','.join(str(item) for item in collections)}"
            if score not in (None, ""):
                match_label = f"{match_label}\n{score}"
            display_rows.append(
                _TweetListRow(
                    tweet_id=row.get("tweet_id"),
                    created_at=row.get("created_at"),
                    author_username=row.get("author_username"),
                    author_id=row.get("author_id"),
                    text=_format_tweet_text(
                        row.get("text"),
                        highlight_query=" ".join(parsed_query.positive_text_terms()),
                    ),
                    match=match_label,
                    score=str(score) if score not in (None, "") else None,
                )
            )
        _render_tweet_list(
            console,
            title=f"search: {query}",
            rows=display_rows,
            count_line=(
                f"showing {len(display_rows)} of {post_page.total} search results"
                if post_page is not None
                and post_page.total is not None
                and search_types == {"post"}
                else f"showing {len(display_rows)} search results"
            ),
        )
    except SearchQueryError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    finally:
        store.close()
