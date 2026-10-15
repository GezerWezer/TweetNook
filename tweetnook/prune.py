"""Remove archived context that the current thread-expansion settings would not fetch."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from tweetnook.storage import ArchiveStore
from tweetnook.threads import walk_linked_statuses

# Rows owned by a single tweet, keyed by its tweet_id.
TWEET_RECORD_TYPES = (
    "tweet_object",
    "tweet_relation",
    "media",
    "url_ref",
    "article",
    "media_tag",
    "tweet_hidden",
)
# Raw captures whose cursor_in is the tweet they fetched.
TWEET_CAPTURE_OPERATIONS = ("ThreadExpandDetail", "TweetDetail", "TweetDetailResurrection")
# Tweets carried inside another tweet's payload; sync stores them without fetching
# their threads, so they stay with the tweet that embeds them.
EMBEDDED_RELATIONS = ("quote_of", "retweet_of")

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


def _database_bytes(path: Path) -> int:
    return sum(
        candidate.stat().st_size
        for candidate in (path, path.with_name(path.name + "-wal"))
        if candidate.exists()
    )


def _load_prune_rows(store: ArchiveStore, pruned_tweet_ids: Iterable[str]) -> None:
    """Fill temp tables with the out-of-scope tweet ids and every row to delete."""
    conn = store.conn
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS prune_ids (tweet_id TEXT PRIMARY KEY)")
    conn.execute(
        "CREATE TEMP TABLE IF NOT EXISTS prune_rows "
        "(rid INTEGER PRIMARY KEY, record_type TEXT, tweet_id TEXT, operation TEXT)"
    )
    conn.execute("DELETE FROM temp.prune_ids")
    conn.execute("DELETE FROM temp.prune_rows")
    conn.executemany(
        "INSERT OR IGNORE INTO temp.prune_ids VALUES (?)",
        ((tweet_id,) for tweet_id in pruned_tweet_ids),
    )
    record_types = ", ".join(f"'{name}'" for name in TWEET_RECORD_TYPES)
    conn.execute(
        f"""
        INSERT OR IGNORE INTO temp.prune_rows
        SELECT owned.rowid, owned.record_type, owned.tweet_id, NULL
        FROM temp.prune_ids AS pruned
        JOIN archive AS owned INDEXED BY idx_archive_tweet_id ON owned.tweet_id = pruned.tweet_id
        WHERE owned.record_type IN ({record_types})
        """
    )
    operations = ", ".join(f"'{name}'" for name in TWEET_CAPTURE_OPERATIONS)
    conn.execute(
        f"""
        INSERT OR IGNORE INTO temp.prune_rows
        SELECT capture.rowid, 'raw_capture', capture.cursor_in, capture.operation
        FROM temp.prune_ids AS pruned
        JOIN archive AS capture INDEXED BY idx_archive_capture_target
          ON capture.record_type = 'raw_capture' AND capture.cursor_in = pruned.tweet_id
        WHERE capture.operation IN ({operations})
        """
    )
    # Unfurled links are shared by url_hash; drop only those no surviving ref uses.
    conn.execute(
        """
        INSERT OR IGNORE INTO temp.prune_rows
        SELECT url.rowid, 'url', NULL, NULL
        FROM archive AS url
        WHERE url.row_key IN (
            SELECT 'url:' || ref.url_hash
            FROM temp.prune_rows AS doomed
            JOIN archive AS ref ON ref.rowid = doomed.rid
            WHERE doomed.record_type = 'url_ref' AND ref.url_hash IS NOT NULL
            EXCEPT
            SELECT 'url:' || url_hash
            FROM archive
            WHERE record_type = 'url_ref' AND url_hash IS NOT NULL
              AND rowid NOT IN (SELECT rid FROM temp.prune_rows)
        )
        AND url.record_type = 'url'
        """
    )


def _media_paths(store: ArchiveStore, *, pruned: bool) -> set[str]:
    membership = "IN" if pruned else "NOT IN"
    rows = store.conn.execute(
        f"""
        SELECT local_path, thumbnail_local_path FROM archive
        WHERE record_type = 'media'
          AND rowid {membership} (SELECT rid FROM temp.prune_rows WHERE record_type = 'media')
          AND (local_path IS NOT NULL OR thumbnail_local_path IS NOT NULL)
        """
    )
    return {value for row in rows for value in row if value}


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
    conversation_of: dict[str, str] = {}
    object_ids: set[str] = set()
    for tweet_id, record_type, conversation_id in store.conn.execute(
        "SELECT tweet_id, record_type, conversation_id FROM archive "
        "WHERE record_type IN ('tweet', 'tweet_object') "
        "AND tweet_id IS NOT NULL AND tweet_id != ''"
    ):
        if record_type == "tweet_object":
            object_ids.add(tweet_id)
        if conversation_id:
            conversation_of[tweet_id] = conversation_id

    kept_conversations = {conversation_of.get(tweet_id, tweet_id) for tweet_id in walk.depths}
    kept = set(walk.depths)
    kept.update(
        tweet_id
        for tweet_id in object_ids
        if conversation_of.get(tweet_id, tweet_id) in kept_conversations
    )

    log("keeping tweets quoted or retweeted by kept tweets...")
    relation_types = ", ".join(f"'{name}'" for name in EMBEDDED_RELATIONS)
    embedded: dict[str, list[str]] = {}
    for source_id, target_id in store.conn.execute(
        "SELECT tweet_id, target_tweet_id FROM archive "
        f"WHERE record_type = 'tweet_relation' AND relation_type IN ({relation_types}) "
        "AND target_tweet_id IS NOT NULL AND target_tweet_id != ''"
    ):
        embedded.setdefault(source_id, []).append(target_id)
    frontier = list(kept)
    while frontier:
        reached = []
        for source_id in frontier:
            for target_id in embedded.get(source_id, ()):
                if target_id not in kept:
                    kept.add(target_id)
                    reached.append(target_id)
        frontier = reached

    capture_targets = {
        str(row[0])
        for row in store.conn.execute(
            "SELECT DISTINCT cursor_in FROM archive INDEXED BY idx_archive_capture_target "
            "WHERE record_type = 'raw_capture' AND operation = 'ThreadExpandDetail' "
            "AND cursor_in IS NOT NULL AND cursor_in != ''"
        )
    }
    plan.pruned_tweet_ids = (object_ids | capture_targets) - kept
    plan.kept_tweet_count = len(object_ids & kept)
    plan.kept_conversation_count = len(kept_conversations)

    log(f"collecting rows owned by {len(plan.pruned_tweet_ids)} out-of-scope tweets...")
    _load_prune_rows(store, plan.pruned_tweet_ids)
    plan.row_counts.update(
        dict(
            store.conn.execute(
                "SELECT record_type, COUNT(*) FROM temp.prune_rows GROUP BY record_type"
            ).fetchall()
        )
    )
    plan.thread_fetch_count = store.conn.execute(
        "SELECT COUNT(DISTINCT tweet_id) FROM temp.prune_rows "
        "WHERE record_type = 'raw_capture' AND operation = 'ThreadExpandDetail'"
    ).fetchone()[0]

    media_root = (data_dir / "media").resolve()
    for relative in sorted(_media_paths(store, pruned=True) - _media_paths(store, pruned=False)):
        path = (data_dir / relative).resolve()
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
    log(f"deleting {plan.row_total} rows...")
    with store.conn:
        _load_prune_rows(store, plan.pruned_tweet_ids)
        cursor = store.conn.execute(
            "DELETE FROM archive WHERE rowid IN (SELECT rid FROM temp.prune_rows)"
        )
        result.rows_deleted = cursor.rowcount
        store.conn.execute("DELETE FROM temp.prune_rows")
        store.conn.execute("DELETE FROM temp.prune_ids")

    log(f"deleting {len(plan.media_files)} media files...")
    media_root = (data_dir / "media").resolve()
    parents: set[Path] = set()
    for path in plan.media_files:
        try:
            size = path.stat().st_size
            path.unlink()
        except FileNotFoundError:
            continue
        result.files_deleted += 1
        result.bytes_deleted += size
        parents.add(path.parent)
    for parent in sorted(parents, key=lambda value: len(value.parts), reverse=True):
        while parent != media_root and parent.is_relative_to(media_root):
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

    log("reclaiming database space (VACUUM)...")
    store.optimize()
    # VACUUM in WAL mode rewrites the database into the WAL; fold it back so the
    # reported size is what is left on disk.
    store.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
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
    return [
        f"Deleted {result.rows_deleted:,} rows and {result.files_deleted:,} media files "
        f"({_format_bytes(result.bytes_deleted)}).",
        f"Database: {_format_bytes(result.database_bytes_before)} → "
        f"{_format_bytes(result.database_bytes_after)}.",
    ]
