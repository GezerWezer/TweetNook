"""Remove archived context that the current thread-expansion settings would not fetch."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from tweetnook.enrichment.prune_files import (
    cleanup_pending_files,
    pending_file,
    pending_paths,
    record_pending_files,
    referenced_media_files,
)
from tweetnook.enrichment.threads import walk_linked_statuses
from tweetnook.storage import ArchiveStore

ROW_LABELS = {
    "tweet_object": "tweets",
    "tweet_relation": "relations",
    "media": "media rows",
    "url_ref": "link refs",
    "url": "unfurled links",
    "article": "articles",
    "media_tag": "media tags",
    "tweet_hidden": "hidden markers",
    "raw_capture": "raw API captures",
}


@dataclass(slots=True)
class PrunePlan:
    max_linked_depth: int
    reply_linked_depth: int
    saved_count: int = 0
    kept_tweet_count: int = 0
    kept_conversation_count: int = 0
    pruned_tweet_ids: set[str] = field(default_factory=set)
    row_counts: Counter[str] = field(default_factory=Counter)
    thread_fetch_count: int = 0
    media_files: list[Path] = field(default_factory=list)
    media_bytes: int = 0
    cleanup_pending: bool = False

    @property
    def row_total(self) -> int:
        return sum(self.row_counts.values())


@dataclass(slots=True)
class PruneResult:
    rows_deleted: int = 0
    files_deleted: int = 0
    bytes_deleted: int = 0
    database_bytes_before: int = 0
    database_bytes_after: int = 0
    files_pending: int = 0


def _database_bytes(path: Path) -> int:
    return sum(
        candidate.stat().st_size
        for candidate in (path, path.with_name(path.name + "-wal"))
        if candidate.exists()
    )


def plan_prune(
    store: ArchiveStore,
    data_dir: Path,
    *,
    max_linked_depth: int,
    reply_linked_depth: int,
    log: Callable[[str], None] | None = None,
) -> PrunePlan:
    """Find archived tweets outside the thread-expansion scope and the rows they own.

    In scope: every conversation thread expansion would fetch under these settings
    (saved tweets' conversations and the conversations of quote/linked-status
    targets on the save path), plus tweets embedded by quote or retweet in a kept
    tweet. Saved tweets themselves are never pruned.
    """
    log = log or (lambda _message: None)
    plan = PrunePlan(max_linked_depth=max_linked_depth, reply_linked_depth=reply_linked_depth)
    membership_ids = store.list_membership_tweet_ids()
    plan.saved_count = len(membership_ids)
    walk = walk_linked_statuses(
        store,
        membership_ids,
        max_linked_depth=max_linked_depth,
        reply_linked_depth=reply_linked_depth,
        log=log,
    )

    log("loading archived tweet conversations...")
    object_ids, conversation_of, embedded, capture_targets = store.prune_graph()

    kept_conversations = {conversation_of.get(tweet_id, tweet_id) for tweet_id in walk.depths}
    kept = set(walk.depths)
    kept.update(
        tweet_id
        for tweet_id in object_ids
        if conversation_of.get(tweet_id, tweet_id) in kept_conversations
    )

    log("keeping tweets quoted or retweeted by kept tweets...")
    frontier = list(kept)
    while frontier:
        reached = []
        for source_id in frontier:
            for target_id in embedded.get(source_id, ()):
                if target_id not in kept:
                    kept.add(target_id)
                    reached.append(target_id)
        frontier = reached

    plan.pruned_tweet_ids = (object_ids | capture_targets) - kept
    plan.kept_tweet_count = len(object_ids & kept)
    plan.kept_conversation_count = len(kept_conversations)

    log(f"collecting rows owned by {len(plan.pruned_tweet_ids)} out-of-scope tweets...")
    selection = store.prune_plan(plan.pruned_tweet_ids)
    plan.row_counts.update(selection.row_counts)
    plan.thread_fetch_count = selection.thread_fetch_count

    media_root = (data_dir / "media").resolve()
    pending = pending_paths(data_dir, store.db_path)
    plan.cleanup_pending = pending_file(store.db_path).exists()
    candidates = {(data_dir / relative).resolve() for relative in selection.media_paths}
    if pending:
        # Cleanup skips files that gained a reference, so the plan must not count them.
        candidates.update(set(pending.values()) - referenced_media_files(store, data_dir))
    for path in sorted(candidates):
        if not path.is_relative_to(media_root) or not path.is_file():
            continue
        plan.media_files.append(path)
        plan.media_bytes += path.stat().st_size
    return plan


def apply_prune(
    store: ArchiveStore,
    plan: PrunePlan,
    data_dir: Path,
    database_path: Path,
    *,
    log: Callable[[str], None] | None = None,
) -> PruneResult:
    """Delete the planned rows and media files, then reclaim database space."""
    log = log or (lambda _message: None)
    result = PruneResult(database_bytes_before=_database_bytes(database_path))
    record_pending_files(data_dir, database_path, plan.media_files)
    log(f"deleting {plan.row_total} rows...")
    result.rows_deleted = store.apply_prune(plan.pruned_tweet_ids)

    log(f"deleting {len(plan.media_files)} media files...")
    result.files_deleted, result.bytes_deleted, result.files_pending = cleanup_pending_files(
        store, data_dir, database_path, log
    )

    log("reclaiming database space (VACUUM)...")
    store.optimize()
    # VACUUM in WAL mode rewrites the database into the WAL; fold it back so the
    # reported size is what is left on disk.
    store.checkpoint()
    result.database_bytes_after = _database_bytes(database_path)
    return result


def _format_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def describe_prune_plan(plan: PrunePlan) -> list[str]:
    scope = (
        f"quotes/linked statuses up to {plan.max_linked_depth} hops from saved tweets "
        "and their authors' own threads"
        if plan.max_linked_depth > 0
        else "no linked statuses"
    )
    replies = (
        "one hop from other people's replies"
        if plan.max_linked_depth > 0 and plan.reply_linked_depth > 0
        else "replies' links not followed"
    )
    lines = [
        f"Scope: saved tweets' conversations, {scope}; {replies}.",
        f"  {plan.saved_count:,} saved tweets · {plan.kept_conversation_count:,} conversations "
        f"in scope · {plan.kept_tweet_count:,} archived tweets kept",
    ]
    if plan.cleanup_pending:
        lines.append("Pending media cleanup will be rechecked against surviving references.")
    if not plan.row_total and not plan.media_files:
        lines.append("Nothing is out of scope.")
        return lines
    tweets = plan.row_counts.get("tweet_object", 0)
    lines.append("Out of scope:")
    lines.append(f"  {tweets:>10,}  tweets")
    lines.append(f"  {plan.thread_fetch_count:>10,}  expanded threads")
    lines.append(f"  {len(plan.media_files):>10,}  media files ({_format_bytes(plan.media_bytes)})")
    detail = " · ".join(
        f"{plan.row_counts[name]:,} {label}"
        for name, label in ROW_LABELS.items()
        if plan.row_counts.get(name)
    )
    lines.append(f"  {plan.row_total:>10,}  database rows: {detail}")
    if plan.row_counts.get("media_tag"):
        lines.append("  Includes Gemini media tags on out-of-scope tweets.")
    return lines


def describe_prune_result(result: PruneResult) -> list[str]:
    lines = [
        f"Deleted {result.rows_deleted:,} rows and {result.files_deleted:,} media files "
        f"({_format_bytes(result.bytes_deleted)}).",
        f"Database: {_format_bytes(result.database_bytes_before)} → "
        f"{_format_bytes(result.database_bytes_after)}.",
    ]
    if result.files_pending:
        lines.append(
            f"{result.files_pending:,} media files remain; run prune --apply to retry cleanup."
        )
    return lines
