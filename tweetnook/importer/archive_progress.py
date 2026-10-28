"""Status lines, progress callbacks and debug timing shared by archive import and enrichment."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from io import StringIO
from time import perf_counter
from typing import Any

from rich.console import Console

from tweetnook.jobs.pipeline import current_pipeline


def _log_archive_phase(console: Console, prefix: str, message: str) -> None:
    console.print(f"{prefix}: {message}", highlight=False)


def _emit_status(status: Callable[[str], None] | None, message: str) -> None:
    if status is not None:
        status(message)


def _status_printer(
    console: Console, prefix: str, *, force: bool = False
) -> Callable[[str], None] | None:
    pipeline = current_pipeline()
    if pipeline is not None:
        if force:
            return lambda message: pipeline.detail(prefix, message)
        return None
    if not (force or console.is_terminal):
        return None
    return lambda message: _log_archive_phase(console, prefix, message)


def _runner_console(console: Console, *, force: bool = False) -> Console:
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


def _record_debug_timing(
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
    _emit_status(status, f"debug {summary}")


@contextmanager
def _progress_callback(
    console: Console,
    *,
    label: str,
    total: int,
    unit: str,
    leave: bool,
):
    if current_pipeline() is not None or not console.is_terminal or total <= 0:
        yield None
        return
    from tqdm import tqdm

    progress_kwargs: dict[str, Any] = {
        "total": total,
        "desc": label,
        "unit": unit,
        "dynamic_ncols": True,
        "leave": leave,
        "file": console.file,
    }
    if unit == "B":
        progress_kwargs["unit_scale"] = True
        progress_kwargs["unit_divisor"] = 1024
    with tqdm(**progress_kwargs) as progress_bar:
        last_done = 0

        def callback(done: int, _total: int) -> None:
            nonlocal last_done
            delta = done - last_done
            if delta > 0:
                progress_bar.update(delta)
            last_done = done

        yield callback
