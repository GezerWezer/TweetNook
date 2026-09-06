"""Bento-style Rich renderer for archive statistics."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from rich import box
from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn
from rich.table import Table
from rich.text import Text

from tweetnook.stats.models import StatFormat, StatsReport, StatsSection
from tweetnook.stats.service import format_bytes

ACCENT = "#1d9bf0"
ACCENT_SOFT = "#8ecdf7"
MUTED = "grey62"
DIM = "grey46"
SUCCESS = "green3"
WARNING = "yellow3"
TILE_BORDER = "grey30"


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    raw = str(value)
    for candidate in (raw, raw.replace("Z", "+00:00")):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            pass
    try:
        return datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y")
    except ValueError:
        return None


def format_stat_value(value: Any, value_format: StatFormat = "text") -> str:
    """Format a presentation-neutral statistic for terminal display."""
    if value is None or value == "":
        return "—"
    if value_format == "integer":
        return f"{int(value):,}"
    if value_format == "decimal":
        return f"{float(value):,.1f}"
    if value_format == "percent":
        return f"{float(value):,.1f}%"
    if value_format == "bytes":
        return format_bytes(float(value))
    if value_format in {"date", "datetime"}:
        parsed = _parse_datetime(value)
        if parsed is None:
            return str(value)
        if value_format == "date":
            return parsed.strftime("%b %-d, %Y")
        return parsed.strftime("%b %-d, %Y · %-I:%M %p")
    return str(value)


def _metric_grid(metrics: Iterable[tuple[str, str]]) -> Table:
    """Render aligned label/value rows inside a bento tile."""
    table = Table.grid(expand=True, padding=(0, 1))
    table.add_column(style=MUTED, ratio=3)
    table.add_column(justify="right", style="bold white", ratio=1, no_wrap=True)
    for label, value in metrics:
        table.add_row(label, value)
    return table


def _progress(rows: Iterable[tuple[str, float, str]], *, style: str = ACCENT) -> Progress:
    progress = Progress(
        TextColumn("{task.description}", style="bold white", justify="right"),
        BarColumn(
            bar_width=None,
            style="grey23",
            complete_style=style,
            finished_style=style,
        ),
        TextColumn("{task.fields[summary]}", style=MUTED, justify="right"),
        expand=True,
        auto_refresh=False,
    )
    for label, percent, summary in rows:
        progress.add_task(
            label,
            total=100,
            completed=max(0.0, min(100.0, percent)),
            summary=summary,
        )
    return progress


def _tile(
    title: str,
    content: RenderableType,
    *,
    subtitle: str | None = None,
    border_style: str = TILE_BORDER,
    height: int | None = None,
) -> Panel:
    return Panel(
        content,
        title=Text(title, style=f"bold {ACCENT_SOFT}"),
        subtitle=Text(subtitle, style=MUTED) if subtitle else None,
        title_align="left",
        subtitle_align="right",
        border_style=border_style,
        box=box.ROUNDED,
        padding=(1, 2),
        height=height,
    )


def _print_tiles(
    console: Console,
    tiles: list[tuple[RenderableType, int]],
) -> None:
    """Print one bento row, stacking it when the terminal is narrow."""
    if len(tiles) == 1 or console.width < 100:
        for renderable, _ratio in tiles:
            console.print(renderable)
            console.print()
        return
    grid = Table.grid(expand=True, padding=(0, 1))
    for _renderable, ratio in tiles:
        grid.add_column(ratio=ratio)
    grid.add_row(*[renderable for renderable, _ratio in tiles])
    console.print(grid)
    console.print()


def _print_masonry(
    console: Console,
    left: list[RenderableType],
    right: list[RenderableType],
    *,
    narrow_order: list[RenderableType],
) -> None:
    """Pack tiles into independent columns so shorter tiles leave no row gap."""
    if console.width < 100:
        _print_tiles(console, [(tile, 1) for tile in narrow_order])
        return

    def column(tiles: list[RenderableType]) -> Group:
        renderables: list[RenderableType] = []
        for index, tile in enumerate(tiles):
            if index:
                renderables.append(Text(""))
            renderables.append(tile)
        return Group(*renderables)

    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(ratio=3)
    grid.add_column(ratio=2)
    grid.add_row(column(left), column(right))
    console.print(grid)
    console.print()


def _header(report: StatsReport, *, detailed: bool, width: int) -> Panel:
    metadata = Table.grid(expand=True, padding=(0, 1))
    metadata.add_column(style=DIM, no_wrap=True)
    metadata.add_column(style="white", overflow="fold")
    if width < 100:
        metadata.add_row("Archive", report.archive_path)
        metadata.add_row("Owner", report.owner)
        metadata.add_row("Generated", format_stat_value(report.generated_at, "datetime"))
        metadata.add_row("View", "detailed" if detailed else "summary")
    else:
        metadata.add_column(style=DIM, no_wrap=True)
        metadata.add_column(style=MUTED, justify="right")
        metadata.add_row("Archive", report.archive_path, "Owner", report.owner)
        metadata.add_row(
            "Generated",
            format_stat_value(report.generated_at, "datetime"),
            "View",
            "detailed" if detailed else "summary",
        )
    title = Text("Tweet", style=f"bold {ACCENT}")
    title.append("N", style="bold white")
    title.append("ook", style=f"bold {ACCENT}")
    title.append("  statistics", style="bold white")
    return Panel(
        metadata,
        title=title,
        title_align="left",
        border_style=ACCENT,
        box=box.HEAVY_HEAD,
        padding=(1, 2),
    )


def _overview_tiles(section: StatsSection, *, detailed: bool) -> list[tuple[Panel, int]]:
    data = section.data
    metrics = [
        ("Unique tweets", f"{int(data.get('unique_posts', 0)):,}"),
        ("Collection memberships", f"{int(data.get('collection_memberships', 0)):,}"),
        ("Media", f"{int(data.get('media_rows', 0)):,}"),
        ("Articles", f"{int(data.get('articles', 0)):,}"),
        ("Authors", f"{int(data.get('profiles', 0)):,}"),
        ("URLs", f"{int(data.get('urls', 0)):,}"),
    ]
    if detailed:
        metrics.extend(
            [
                ("Raw captures", f"{int(data.get('raw_captures', 0)):,}"),
                ("Imported tweets", f"{int(data.get('archive_tweets', 0)):,}"),
                (
                    "Unavailable imported",
                    f"{int(data.get('missing_archive_tweets', 0)):,} "
                    f"({float(data.get('missing_archive_pct', 0)):.1f}%)",
                ),
            ]
        )
    dates = Table.grid(expand=True, padding=(0, 0))
    dates.add_column(style=MUTED)
    dates.add_row("First tweet")
    dates.add_row(Text(format_stat_value(data.get("oldest_post"), "datetime"), style="white"))
    dates.add_row("")
    dates.add_row("Latest tweet")
    dates.add_row(Text(format_stat_value(data.get("newest_post"), "datetime"), style="white"))
    dates.add_row("")
    dates.add_row("Last sync")
    dates.add_row(Text(format_stat_value(data.get("latest_sync"), "datetime"), style="white"))
    if detailed:
        dates.add_row("")
        dates.add_row("Latest capture")
        dates.add_row(
            Text(format_stat_value(data.get("latest_capture"), "datetime"), style="white")
        )
    return [
        (_tile("Archive", _metric_grid(metrics), subtitle="content"), 2),
        (_tile("Timeline", dates, subtitle="local time"), 1),
    ]


def _collections_tile(section: StatsSection) -> Panel:
    table = Table(
        box=box.SIMPLE_HEAVY,
        header_style=f"bold {ACCENT_SOFT}",
        row_styles=("", "on grey7"),
        expand=True,
        padding=(0, 0),
    )
    for column in section.columns:
        table.add_column(
            "Synced" if column.key == "last_synced" else column.label,
            justify=column.align,
            no_wrap=True,
        )
    for row in section.rows:
        values = []
        for column in section.columns:
            value = row.get(column.key)
            if column.key in {"oldest", "newest"}:
                parsed = _parse_datetime(value)
                values.append(parsed.strftime("%Y-%m-%d") if parsed else "—")
            elif column.key == "last_synced":
                parsed = _parse_datetime(value)
                values.append(parsed.strftime("%b %-d · %-I:%M %p") if parsed else "—")
            else:
                values.append(format_stat_value(value, column.format))
        table.add_row(*values)
    if not section.rows:
        table.add_row("No synced collections", *["—"] * (len(section.columns) - 1))
    return _tile("Collections", table, subtitle="coverage & sync state")


def _archive_tiles(
    section: StatsSection,
    *,
    detailed: bool,
) -> tuple[list[tuple[Panel, int]], Panel | None]:
    data = section.data
    enrichment = data.get("enrichment", {})
    unavailable = enrichment.get("unavailable", {})
    done = int(enrichment.get("done", 0))
    resurrected = int(enrichment.get("resurrected", 0))
    available = int(enrichment.get("available", done + resurrected))
    incomplete = int(enrichment.get("incomplete", 0))
    unavailable_total = int(unavailable.get("total", 0))
    total = available + incomplete + unavailable_total
    coverage = available / total * 100 if total else 100.0

    health = Group(
        _progress(
            [("Enrichment", coverage, f"{available:,} / {total:,} · {coverage:.1f}%")],
            style=SUCCESS,
        ),
        Text(""),
        _metric_grid(
            [
                ("Enriched", f"{done:,}"),
                ("Resurrected", f"{resurrected:,}"),
                ("Threads expanded", f"{int(data.get('threads_expanded', 0)):,}"),
                ("Unavailable", f"{unavailable_total:,}"),
            ]
        ),
    )

    queues = [
        ("Enrichment pending", int(enrichment.get("pending", 0)), "awaiting first attempt"),
        (
            "Transient retries due",
            int(enrichment.get("transient_due", 0)),
            f"{int(enrichment.get('transient_delayed', 0)):,} delayed",
        ),
        (
            "Unavailable retries due",
            int(unavailable.get("due", 0)),
            f"{int(unavailable.get('permanent', 0)):,} permanent",
        ),
        (
            "Thread memberships pending",
            int(data.get("pending_thread_memberships", 0)),
            f"{int(data.get('pending_linked_statuses', 0)):,} related pending",
        ),
        (
            "Local rehydrate gaps",
            int(data.get("missing_tweet_objects", 0)),
            "missing tweet objects",
        ),
        (
            "Preview-only articles",
            int(data.get("preview_articles", 0)),
            "content not stored",
        ),
    ]
    visible_queues = queues if detailed else [row for row in queues if row[1]]
    queue_group: list[RenderableType] = []
    if visible_queues:
        for index, (label, count, context) in enumerate(visible_queues):
            if index:
                queue_group.append(Text(""))
            queue_group.append(
                Text.assemble(
                    ("! " if count else "· ", f"bold {WARNING if count else DIM}"),
                    (label, "white"),
                    (f"  {count:,}", f"bold {WARNING if count else MUTED}"),
                )
            )
            queue_group.append(Text(context, style=MUTED))
    else:
        queue_group.append(Text("No outstanding maintenance.", style=MUTED))

    reasons = unavailable.get("reasons", [])
    visible_reasons = reasons if detailed else [row for row in reasons if row.get("count", 0)]
    reasons_tile = None
    if visible_reasons:
        table = Table(
            "Unavailable reason",
            "Tweets",
            "% missing",
            "Retryable",
            "Due",
            "Permanent",
            box=box.SIMPLE_HEAVY,
            header_style=f"bold {ACCENT_SOFT}",
            expand=True,
        )
        for index in range(1, 6):
            table.columns[index].justify = "right"
        for reason in visible_reasons:
            table.add_row(
                str(reason["label"]),
                f"{int(reason['count']):,}",
                f"{float(reason['percent_of_missing']):.1f}%",
                f"{int(reason['retryable']):,}",
                f"{int(reason['due']):,}",
                f"{int(reason['permanent']):,}",
            )
        reasons_tile = _tile("Unavailable detail", table, subtitle="showing hidden rows")

    attention_height = max(7, len(visible_queues) * 3 + 3)
    health_height = attention_height + 2
    return (
        [
            (
                _tile(
                    "Archive health",
                    health,
                    subtitle="availability",
                    border_style=SUCCESS,
                    height=health_height,
                ),
                3,
            ),
            (
                _tile(
                    "Needs attention",
                    Group(*queue_group),
                    subtitle="work queues",
                    height=attention_height,
                ),
                2,
            ),
        ],
        reasons_tile,
    )


def _segment_contents(segment: dict[str, Any]) -> str:
    return str(
        segment.get("formatted_count")
        or f"{int(segment.get('count', 0)):,} {segment.get('unit', 'items')}"
    )


def _storage_tile(section: StatsSection, *, detailed: bool) -> Panel:
    data = section.data
    total = int(data.get("total_bytes", 0))
    if not detailed:
        segments = data.get("simplified_segments", [])
        content: RenderableType = Group(
            _progress(
                [
                    (
                        str(segment["name"]),
                        float(segment.get("percent", 0)),
                        f"{segment.get('formatted_size', format_bytes(segment.get('bytes', 0)))} · "
                        f"{float(segment.get('percent', 0)):.1f}%",
                    )
                    for segment in segments
                ]
            ),
            Text(""),
            *[
                Text.assemble(
                    (f"{segment['name']}  ", MUTED),
                    (_segment_contents(segment), "white"),
                )
                for segment in segments
            ],
        )
    else:
        table = Table(
            "Component",
            "Contents",
            "Size",
            "Share",
            box=box.SIMPLE_HEAVY,
            header_style=f"bold {ACCENT_SOFT}",
            expand=True,
        )
        table.columns[2].justify = "right"
        table.columns[3].justify = "right"
        for segment in data.get("segments", []):
            name = Text(str(segment.get("name", "Unknown")), style="white")
            description = segment.get("description")
            if description:
                name.append(f"\n{description}", style=MUTED)
            table.add_row(
                name,
                _segment_contents(segment),
                str(segment.get("formatted_size", format_bytes(segment.get("bytes", 0)))),
                f"{float(segment.get('percent', 0)):.1f}%",
            )
        content = table
    return _tile("Storage", content, subtitle=f"{format_bytes(total)} total")


def _tags_tile(section: StatsSection, *, detailed: bool) -> Panel:
    data = section.data
    coverage = float(data.get("coverage_pct", 0))
    tagged = int(data.get("tagged_tweets", 0))
    eligible = int(data.get("eligible_tweets", 0))
    metrics = [
        ("Unique tags", f"{int(data.get('unique_tags', 0)):,}"),
        ("Average per tweet", f"{float(data.get('avg_tags_per_tweet', 0)):.1f}"),
    ]
    if detailed:
        metrics.extend(
            [
                ("Tag instances", f"{int(data.get('total_tag_instances', 0)):,}"),
                ("Eligible untagged", f"{int(data.get('untagged_eligible', 0)):,}"),
            ]
        )
    top_tags = data.get("top_tags", [])[: 20 if detailed else 6]
    tag_lines = [
        Text.assemble(
            (f"{index:>2}. ", DIM),
            (str(row["tag"]), ACCENT_SOFT),
            (f"  {int(row['count']):,}", MUTED),
        )
        for index, row in enumerate(top_tags, start=1)
    ]
    return _tile(
        "Tagging & search",
        Group(
            _progress([("Coverage", coverage, f"{tagged:,} / {eligible:,} · {coverage:.1f}%")]),
            Text(""),
            _metric_grid(metrics),
            Text(""),
            *tag_lines,
        ),
        subtitle="frequent topics",
    )


def _generic_tile(section: StatsSection) -> Panel:
    content: list[RenderableType] = []
    if section.items:
        content.append(
            _metric_grid(
                [(item.label, format_stat_value(item.value, item.format)) for item in section.items]
            )
        )
    if section.columns:
        table = Table(
            box=box.SIMPLE_HEAVY,
            header_style=f"bold {ACCENT_SOFT}",
            expand=True,
        )
        for column in section.columns:
            table.add_column(column.label, justify=column.align)
        for row in section.rows:
            table.add_row(
                *[
                    format_stat_value(row.get(column.key), column.format)
                    for column in section.columns
                ]
            )
        content.append(table)
    return _tile(section.title, Group(*content), subtitle=section.description)


def render_stats_report(
    console: Console,
    report: StatsReport,
    *,
    detailed: bool = False,
) -> None:
    """Render the statistics report as a responsive terminal bento grid."""
    console.print(_header(report, detailed=detailed, width=console.width))
    console.print()

    sections = {section.id: section for section in report.sections}
    rendered = {"overview", "collections", "archive_status", "storage", "tagging"}
    overview = sections.get("overview")
    archive_status = sections.get("archive_status")
    unavailable_detail = None
    if overview and archive_status:
        overview_row = _overview_tiles(overview, detailed=detailed)
        archive_row, unavailable_detail = _archive_tiles(archive_status, detailed=detailed)
        archive_tile = overview_row[0][0]
        timeline_tile = overview_row[1][0]
        health_tile = archive_row[0][0]
        attention_tile = archive_row[1][0]
        left = [archive_tile, health_tile]
        narrow_order = [archive_tile, timeline_tile, health_tile, attention_tile]
        _print_masonry(
            console,
            left,
            [timeline_tile, attention_tile],
            narrow_order=narrow_order,
        )
        if unavailable_detail is not None:
            _print_tiles(console, [(unavailable_detail, 1)])
    else:
        if overview:
            _print_tiles(console, _overview_tiles(overview, detailed=detailed))
        if archive_status:
            archive_row, unavailable_detail = _archive_tiles(
                archive_status,
                detailed=detailed,
            )
            _print_tiles(console, archive_row)
            if unavailable_detail is not None:
                _print_tiles(console, [(unavailable_detail, 1)])

    if collections := sections.get("collections"):
        _print_tiles(console, [(_collections_tile(collections), 1)])

    storage = sections.get("storage")
    tagging = sections.get("tagging")
    show_tagging = bool(tagging and int(tagging.data.get("total_tag_instances", 0)))
    if storage:
        _print_tiles(console, [(_storage_tile(storage, detailed=detailed), 1)])
    if tagging and show_tagging:
        _print_tiles(console, [(_tags_tile(tagging, detailed=detailed), 1)])

    for section in report.sections:
        if section.id not in rendered:
            _print_tiles(console, [(_generic_tile(section), 1)])
