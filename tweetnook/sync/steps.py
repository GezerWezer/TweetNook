"""The pipeline steps a collection sync shows, declared once for the plan and the runner."""

from __future__ import annotations

from collections.abc import Sequence

from tweetnook.jobs.pipeline import NullPipeline, PipelineReporter

_PAGE_STEP = {
    "total": 1,
    "unit": "current page",
    "show_rate": False,
    "show_eta": False,
    "show_progress": False,
}


def preflight_step_key(collections: Sequence[str]) -> str:
    return "preflight:" + ",".join(collections)


def head_step_key(collection: str) -> str:
    return f"sync:{collection}:head"


def sync_step_keys(collection: str) -> tuple[str, str]:
    """The keys of one collection's own sync steps, for a caller that skips them."""
    return preflight_step_key([collection]), head_step_key(collection)


def add_preflight_step(
    pipeline: PipelineReporter | NullPipeline,
    collections: Sequence[str],
    *,
    title: str = "Prepare",
) -> None:
    pipeline.add_step(
        preflight_step_key(collections),
        title,
        total=3 + len(collections),
        unit="checks",
        detail=f"sign-in · archive owner · {len(collections)} collection checks",
        rate_unit="checks/s",
    )


def add_head_step(
    pipeline: PipelineReporter | NullPipeline, collection: str, *, title: str | None = None
) -> None:
    pipeline.add_step(
        head_step_key(collection),
        title or collection.title(),
        detail="head timeline pass · each page committed with its resume cursor",
        **_PAGE_STEP,
    )
