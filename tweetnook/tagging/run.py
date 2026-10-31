"""What every tagging stage shares: the run, its outcome, its prompt and its persistence."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from rich.console import Console
from rich.text import Text

from tweetnook.config import XDGPaths
from tweetnook.jobs.outcome import Outcome
from tweetnook.storage.backend import ArchiveStore
from tweetnook.tagging.accounting import AIUsageLedger
from tweetnook.tagging.preparation import PreparedBatch, TweetTagContext
from tweetnook.tagging.prompts import build_media_system_prompt, build_text_system_prompt
from tweetnook.tagging.results import (
    MediaTagResult,
    TaggingPreview,
    TextTagResult,
    normalized_tags,
)

if TYPE_CHECKING:
    from tweetnook.tagging.paid import PaidRunState, SearchPolicy


@dataclass(frozen=True, slots=True)
class TagRequest:
    """One caller's request to tag specific tweets."""

    tweet_ids: list[str]
    model_override: str | None = None
    dry_run: bool = False
    content_type: Literal["media", "text"] | None = None
    quoted_tweet_ids: dict[str, str] | None = None
    preview_results: list[TaggingPreview] | None = None


def _combined_status(first: Outcome, second: Outcome) -> Outcome:
    statuses = {first, second} - {Outcome.NO_WORK}
    if not statuses:
        return Outcome.NO_WORK
    if Outcome.PAUSED in statuses:
        return Outcome.PAUSED
    if len(statuses) == 1:
        return statuses.pop()
    return Outcome.PARTIAL


@dataclass(frozen=True, slots=True)
class TaggingOutcome:
    """What tagging some tweets achieved, with the attempts it actually sent."""

    status: Outcome = Outcome.NO_WORK
    tagged: int = 0
    attempts: int = 0
    reason: str | None = None
    # Nothing further in this run can succeed, so the tweets not reached stay untouched.
    halted: bool = False

    @classmethod
    def paused(cls, reason: str, *, tagged: int = 0) -> TaggingOutcome:
        return cls(Outcome.PAUSED, tagged=tagged, reason=reason, halted=True)

    @classmethod
    def failed(cls, reason: str, *, halted: bool = False) -> TaggingOutcome:
        return cls(Outcome.FAILED, reason=reason, halted=halted)

    @classmethod
    def from_results(cls, tagged: int) -> TaggingOutcome:
        if tagged:
            return cls(Outcome.SUCCEEDED, tagged=tagged)
        return cls.failed("Gemini returned no usable result")

    def plus(self, other: TaggingOutcome) -> TaggingOutcome:
        return TaggingOutcome(
            status=_combined_status(self.status, other.status),
            tagged=self.tagged + other.tagged,
            attempts=self.attempts + other.attempts,
            reason=other.reason if other.halted else self.reason or other.reason,
            halted=self.halted or other.halted,
        )


@dataclass(slots=True)
class TaggingRun:
    """State shared by every request of one run."""

    store: ArchiveStore
    tag_config: Any
    paths: XDGPaths
    console: Console
    model_name: str
    ledger: AIUsageLedger
    run_identifier: str
    search_policy: SearchPolicy
    paid_state: PaidRunState
    dry_run: bool = False
    previews: list[TaggingPreview] | None = None
    free_attempts: int = 0

    @property
    def paid(self) -> bool:
        return self.tag_config.api_mode == "paid"

    @property
    def attempts_sent(self) -> int:
        return self.paid_state.attempts + self.free_attempts


def system_prompt(
    run: TaggingRun, batch: PreparedBatch, existing_tags: Sequence[str], *, search: bool
) -> str:
    build = build_media_system_prompt if batch.content_type == "media" else build_text_system_prompt
    return build(
        existing_tags=tuple(existing_tags),
        tagging_context=run.tag_config.tagging_context,
        additional_instructions=run.tag_config.additional_instructions,
        google_search=search,
        tweet_isolation=not run.paid and len(batch.tweets) > 1,
    )


def _print_tag_preview(
    run: TaggingRun,
    context: TweetTagContext,
    *,
    description: str | None,
    tags: list[str],
) -> None:
    author = context.author_display_name
    if context.author_username:
        author = (
            f"{author} (@{context.author_username})" if author else f"@{context.author_username}"
        )
    run.console.print(Text(f"Tweet {context.tweet_id} ({context.tweet_type})", style="bold"))
    if author:
        run.console.print(Text(f"Author: {author}"))
    run.console.print(Text(f"Text: {context.text}"))
    if description:
        run.console.print(Text(f"Description: {description}"))
    run.console.print(Text(f"Tags: {', '.join(tags)}"))


def persist(
    run: TaggingRun,
    batch: PreparedBatch,
    parsed: list[tuple[str, MediaTagResult | TextTagResult]],
    existing_tags: Sequence[str],
) -> int:
    """Save accepted results, or preview them in test mode; returns how many were accepted."""
    eligible = batch.contexts()
    accepted: set[str] = set()
    # Test mode saves nothing, so it must not wait on or fail with another writer.
    with nullcontext() if run.dry_run else run.store.transaction():
        for tweet_id, result in parsed:
            context = eligible.get(tweet_id)
            # One result per tweet: the first usable one wins, so a repeated entry can't
            # overwrite it or count twice.
            if context is None or tweet_id in accepted:
                continue
            tags = normalized_tags(result.tags, existing_tags)
            if not 2 <= len(tags) <= 5:
                if not run.dry_run and run.paid:
                    run.store.defer_tagging_failure([tweet_id])
                continue
            description = getattr(result, "description", None)
            accepted.add(tweet_id)
            if run.dry_run:
                if run.previews is not None:
                    run.previews.append(
                        TaggingPreview(
                            id=tweet_id,
                            content_type=batch.content_type,
                            model=run.model_name,
                            tweet_type=context.tweet_type,
                            author_display_name=context.author_display_name,
                            author_username=context.author_username,
                            text=context.text,
                            description=description,
                            tags=tags,
                        )
                    )
                _print_tag_preview(run, context, description=description, tags=tags)
                continue
            payload: dict[str, Any] = {"tags": tags}
            if description:
                payload["description"] = description
            run.store.save_tagging_result(tweet_id, payload)

    tagged_count = len(accepted)
    if run.dry_run:
        run.console.print(
            f"[green]Generated tags for {tagged_count} tweet"
            f"{'s' if tagged_count != 1 else ''} in test mode; no tags were saved.[/green]"
        )
    else:
        run.console.print(f"[green]Successfully tagged {tagged_count} tweets![/green]")
    return tagged_count


def announce_batch(run: TaggingRun, batch: PreparedBatch) -> None:
    run.console.print(
        Text(
            f"Generating {batch.content_type} tags for {len(batch.tweets)} tweets "
            f"using {run.model_name}..."
        )
    )
