"""The pipeline steps an archive import shows, each declared once."""

from __future__ import annotations

from tweetnook.jobs.pipeline import NullPipeline, PipelineReporter
from tweetnook.jobs.steps import ARCHIVE_AUTH, ARCHIVE_ENRICH, MEDIA, StepSpec
from tweetnook.sync.steps import add_head_step, add_preflight_step

INSPECT = StepSpec(
    "archive-inspect",
    "Inspect",
    "bytes",
    "content hash and archive owner validation",
    rate_unit="bytes/s",
)
REGEN = StepSpec(
    "archive-regen",
    "Regenerate",
    "cleanup",
    "archive-import-owned rows and managed media only",
    show_rate=False,
    show_eta=False,
)
SOURCE = StepSpec(
    "archive-source",
    "Archive source",
    "operations",
    "parse every dataset part · preserve every raw payload",
    rate_unit="operations/s",
)
TWEETS = StepSpec(
    "archive-tweets",
    "Tweets",
    "tweets",
    "authored archive rows · normalized objects, relations, and raw source",
    rate_unit="tweets/s",
)
DELETED = StepSpec(
    "archive-deleted",
    "Deleted tweets",
    "tweets",
    "deleted authored rows · archive deletion provenance preserved",
    rate_unit="tweets/s",
)
LIKES = StepSpec(
    "archive-likes",
    "Likes",
    "likes",
    "archive like memberships · sparse objects queued for live enrichment",
    rate_unit="likes/s",
)
ARCHIVE_MEDIA = StepSpec(
    "archive-media",
    "Archive media",
    "files",
    "exported media matched to normalized tweet assets",
    rate_unit="files/s",
)

# The steps an import writes through, which a duplicate archive skips together.
INGEST_STEPS = (SOURCE, TWEETS, DELETED, LIKES, ARCHIVE_MEDIA)
# The archive datasets a live sync reconciles after import, by sync collection.
FOLLOWUP_COLLECTIONS = ("tweets", "likes")


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
    INSPECT.declare(pipeline)
    if regen:
        REGEN.declare(pipeline)
    for step in INGEST_STEPS:
        step.declare(pipeline)

    if sample_limit is not None or offline:
        return
    ARCHIVE_AUTH.declare(
        pipeline,
        detail="optional Twitter/X session; enrichment uses FxTwitter without one"
        if defer_followup
        else "configured Twitter/X session for live archive follow-up",
    )
    if not defer_followup:
        for collection in FOLLOWUP_COLLECTIONS:
            add_preflight_step(pipeline, [collection], title=f"Prepare {collection}")
            add_head_step(pipeline, collection, title=f"Reconcile {collection}")
    if enrich or detail_lookups > 0:
        ARCHIVE_ENRICH.declare(pipeline)
    if defer_followup:
        MEDIA.declare(pipeline)
