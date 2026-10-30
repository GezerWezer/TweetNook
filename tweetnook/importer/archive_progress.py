"""Status lines, progress callbacks and debug timing shared by archive import and enrichment."""

from __future__ import annotations

from collections.abc import Callable
from io import StringIO
from time import perf_counter

from rich.console import Console

from tweetnook.jobs.interactive import emit_status, status_printer
from tweetnook.jobs.pipeline import current_pipeline


def archive_status_printer(
    console: Console, prefix: str, *, force: bool = False
) -> Callable[[str], None] | None:
    pipeline = current_pipeline()
    if pipeline is not None:
        if force:
            return lambda message: pipeline.detail(prefix, message)
        return None
    return status_printer(console, prefix, force=force)


def runner_console_for(console: Console, *, force: bool = False) -> Console:
    if current_pipeline() is not None:
        return console
    if force or console.is_terminal:
        return console
    return Console(file=StringIO(), force_terminal=False, color_system=None)


def _format_debug_rate(processed: int | None, elapsed: float, *, unit: str) -> str | None:
    if processed is None or processed <= 0 or elapsed <= 0:
        return None
    if unit == "bytes":
        mib_per_second = processed / elapsed / (1024 * 1024)
        return f"{mib_per_second:.1f} MiB/s"
    suffix = unit if unit.endswith("/s") else f"{unit}/s"
    return f"{processed / elapsed:.1f} {suffix}"


def record_debug_timing(
    status: Callable[[str], None] | None,
    summaries: list[str],
    label: str,
    started_at: float,
    *,
    processed: int | None = None,
    unit: str = "items",
) -> None:
    elapsed = perf_counter() - started_at
    summary = f"{label}: {elapsed:.2f}s"
    rate = _format_debug_rate(processed, elapsed, unit=unit)
    if rate is not None:
        summary += f" ({rate})"
    summaries.append(summary)
    emit_status(status, f"debug {summary}")
