"""Pipeline steps more than one job runs, each declared once.

A step can run on its own (``tweetnook media download``), as a sync follow-up, or as part
of an archive import, and every one of those declares it before work starts. Declaring
it again with a real total or detail updates the step in place, so the plan and the
runner share one key, title and unit.
"""

from __future__ import annotations

from dataclasses import dataclass

from tweetnook.jobs.pipeline import NullPipeline, PipelineReporter


@dataclass(frozen=True, slots=True)
class StepSpec:
    key: str
    title: str
    unit: str
    detail: str = ""
    rate_unit: str | None = None
    show_rate: bool = True
    show_eta: bool = True

    def declare(
        self,
        pipeline: PipelineReporter | NullPipeline,
        *,
        total: int = 1,
        detail: str | None = None,
        unit: str | None = None,
    ) -> None:
        pipeline.add_step(
            self.key,
            self.title,
            total=total,
            unit=unit or self.unit,
            detail=detail or self.detail,
            rate_unit=self.rate_unit,
            show_rate=self.show_rate,
            show_eta=self.show_eta,
        )


THREADS = StepSpec(
    "threads",
    "Threads",
    "candidates",
    "membership and reachable linked-status candidates",
    rate_unit="candidates/s",
)
RESURRECTION = StepSpec(
    "resurrection", "Unavailable tweets", "tweets", "due unavailable tweets", rate_unit="tweets/s"
)
ARTICLES = StepSpec(
    "articles",
    "Articles",
    "tweets",
    "archived article rows selected for TweetDetail refresh",
    rate_unit="tweets/s",
)
MEDIA = StepSpec(
    "media",
    "Media",
    "items",
    "pending archived media selected for local download",
    rate_unit="items/s",
)
URLS = StepSpec(
    "urls",
    "Link previews",
    "URLs",
    "saved URLs selected for redirect and canonical metadata refresh",
    rate_unit="URLs/s",
)
TAGGING = StepSpec("tagging", "Automated tags", "tweets", show_rate=False, show_eta=False)
SMART_SEARCH = StepSpec(
    "smart-search",
    "Smart search",
    "images",
    "archived media indexed for semantic search",
    rate_unit="images/s",
)
ARCHIVE_AUTH = StepSpec(
    "archive-auth",
    "Authentication",
    "session",
    "configured Twitter/X session for archive enrichment",
    show_rate=False,
    show_eta=False,
)
ARCHIVE_ENRICH = StepSpec(
    "archive-enrich",
    "Enrich",
    "tweets",
    "stable snapshot of eligible sparse archive tweets",
    rate_unit="tweets/s",
)
