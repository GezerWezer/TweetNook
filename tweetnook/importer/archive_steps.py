"""The pipeline steps an archive import shows, each declared once."""

from __future__ import annotations

from dataclasses import dataclass

from tweetnook.jobs.pipeline import NullPipeline, PipelineReporter
from tweetnook.sync.steps import head_step_key, preflight_step_key


@dataclass(frozen=True, slots=True)
class ArchiveStep:
    key: str
    title: str
    unit: str
    detail: str
    rate_unit: str | None = None
    show_rate: bool = True
    show_eta: bool = True


INSPECT = ArchiveStep(
    "archive-inspect",
    "Inspect",
    "bytes",
    "content hash and archive owner validation",
    rate_unit="bytes/s",
)
REGEN = ArchiveStep(
    "archive-regen",
    "Regenerate",
    "cleanup",
    "archive-import-owned rows and managed media only",
    show_rate=False,
    show_eta=False,
)
SOURCE = ArchiveStep(
    "archive-source",
    "Archive source",
    "operations",
    "parse every dataset part · preserve every raw payload",
    rate_unit="operations/s",
)
TWEETS = ArchiveStep(
    "archive-tweets",
    "Tweets",
    "tweets",
    "authored archive rows · normalized objects, relations, and raw source",
    rate_unit="tweets/s",
)
DELETED = ArchiveStep(
    "archive-deleted",
    "Deleted tweets",
    "tweets",
    "deleted authored rows · archive deletion provenance preserved",
    rate_unit="tweets/s",
)
LIKES = ArchiveStep(
    "archive-likes",
    "Likes",
    "likes",
    "archive like memberships · sparse objects queued for live enrichment",
    rate_unit="likes/s",
)
MEDIA = ArchiveStep(
    "archive-media",
    "Archive media",
    "files",
    "exported media matched to normalized tweet assets",
    rate_unit="files/s",
)

# The steps an import writes through, which a duplicate archive skips together.
INGEST_STEPS = (SOURCE, TWEETS, DELETED, LIKES, MEDIA)


def add_archive_step(
    pipeline: PipelineReporter | NullPipeline,
    step: ArchiveStep,
    *,
    total: int = 1,
    detail: str | None = None,
) -> None:
    pipeline.add_step(
        step.key,
        step.title,
        total=total,
        unit=step.unit,
        detail=detail or step.detail,
        rate_unit=step.rate_unit,
        show_rate=step.show_rate,
        show_eta=step.show_eta,
    )


def plan_archive_import(
    pipeline: PipelineReporter | NullPipeline,
    *,
    regen: bool,
    enrich: bool,
    detail_lookups: int,
    sample_limit: int | None,
    offline: bool = False,
    defer_followup: bool = False,
) -> None:
    add_archive_step(pipeline, INSPECT)
    if regen:
        add_archive_step(pipeline, REGEN)
    for step in INGEST_STEPS:
        add_archive_step(pipeline, step)

    if sample_limit is not None or offline:
        return
    pipeline.add_step(
        "archive-auth",
        "Authentication",
        total=1,
        unit="session",
        detail=(
            "optional Twitter/X session; enrichment uses FxTwitter without one"
            if defer_followup
            else "configured Twitter/X session for live archive follow-up"
        ),
        show_rate=False,
        show_eta=False,
    )
    if not defer_followup:
        for collection in ("tweets", "likes"):
            pipeline.add_step(
                preflight_step_key([collection]),
                f"Prepare {collection}",
                total=4,
                unit="checks",
                detail="authentication · archive owner · remote endpoint probe",
                rate_unit="checks/s",
            )
            pipeline.add_step(
                head_step_key(collection),
                f"Reconcile {collection}",
                total=1,
                unit="current page",
                detail="live Twitter/X timeline reconciled into imported archive rows",
                show_rate=False,
                show_eta=False,
                show_progress=False,
            )
    if enrich or detail_lookups > 0:
        pipeline.add_step(
            "archive-enrich",
            "Enrich",
            total=1,
            unit="tweets",
            detail="stable snapshot of eligible sparse archive tweets",
            rate_unit="tweets/s",
        )
    if defer_followup:
        pipeline.add_step(
            "media",
            "Media",
            total=1,
            unit="items",
            detail="pending archived media selected for local download",
            rate_unit="items/s",
        )
