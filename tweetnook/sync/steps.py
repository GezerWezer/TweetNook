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


def backfill_step_key(collection: str) -> str:
    return f"sync:{collection}:backfill"


def history_shares_head_step(collection: str) -> bool:
    """Bookmarks and Likes show older history inside their one step; others list it separately."""
    return collection in {"bookmarks", "likes"}


def add_preflight_step(
    pipeline: PipelineReporter | NullPipeline, collections: Sequence[str]
) -> None:
    pipeline.add_step(
        preflight_step_key(collections),
        "Prepare",
        total=3 + len(collections),
        unit="checks",
        detail=f"sign-in · archive owner · {len(collections)} collection checks",
        rate_unit="checks/s",
    )


def add_head_step(pipeline: PipelineReporter | NullPipeline, collection: str) -> None:
    pipeline.add_step(
        head_step_key(collection),
        collection.title(),
        detail="head timeline pass · each page committed with its resume cursor",
        **_PAGE_STEP,
    )


def add_backfill_step(pipeline: PipelineReporter | NullPipeline, collection: str) -> None:
    pipeline.add_step(
        backfill_step_key(collection),
        f"{collection.title()} history",
        detail="saved older-history cursor, when one exists",
        **_PAGE_STEP,
    )
