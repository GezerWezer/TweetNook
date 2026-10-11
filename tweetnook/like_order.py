"""Derived like order, rebuilt from immutable observations without inventing dates.

Archive block layout is based on rrika's empirical finding linked in upstream
issue #2. This module implements its own permutation and anchored merge; it does
not use the third-party sequence-alignment implementation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from itertools import pairwise
from typing import Any, TypeVar

from tweetnook.client.timelines import _extract_tweet_results_from_content

LIKE_SORTS = frozenset({"liked_latest", "liked_earliest"})
T = TypeVar("T")
_UNAVAILABLE_TYPENAMES = frozenset({"TweetTombstone", "TweetUnavailable"})

# One capture's ordered (tweet_id, sort_index, requires_membership) observations.
PageObservations = tuple[tuple[str, int | None, bool], ...]


def repair_archive_blocks(items: list[T]) -> list[T]:
    """Undo the observed depth-first export of breadth-first 25-item blocks.

    Block zero has nine children; subsequent blocks have ten. Individual block
    contents remain in their original, potentially scrambled order. Apply only
    to a complete dataset, before removing invalid or unimported entries.
    """
    count = len(items)
    if not count:
        return []
    result = list(items)
    blocks = (count + 24) // 25
    pending = [0]
    offset = 0
    while pending:
        block = pending.pop()
        start = block * 25
        size = min(25, count - start)
        result[start : start + size] = items[offset : offset + size]
        offset += size
        first_child = max(1, block * 10)
        last_child = min(blocks, block * 10 + 10)
        pending.extend(range(last_child - 1, first_child - 1, -1))
    return result


def merge_missing(
    preferred: list[str], fallback: list[str], *, unanchored: str = "after"
) -> list[str]:
    """Keep preferred order, inserting missing runs using shared ID anchors.

    Conflicting anchors never reorder the preferred observation. A disconnected
    older/tail observation goes after it; a disconnected preceding page goes
    before it. Such placement is deterministic, not a claim of exact chronology.
    """
    preferred = list(dict.fromkeys(preferred))
    fallback = list(dict.fromkeys(fallback))
    positions = {tweet_id: index for index, tweet_id in enumerate(preferred)}
    if not positions.keys() & set(fallback):
        return fallback + preferred if unanchored == "before" else preferred + fallback
    buckets: dict[int, list[str]] = {}
    pending: list[str] = []
    left = -1
    for tweet_id in fallback:
        right = positions.get(tweet_id)
        if right is None:
            pending.append(tweet_id)
            continue
        if pending:
            slot = right if right > left else left + 1
            buckets.setdefault(slot, []).extend(pending)
            pending = []
        left = max(left, right)
    if pending:
        buckets.setdefault(left + 1, []).extend(pending)
    merged: list[str] = []
    for index, tweet_id in enumerate(preferred):
        merged.extend(buckets.get(index, []))
        merged.append(tweet_id)
    merged.extend(buckets.get(len(preferred), []))
    return merged


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, str | int):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _payload(row: Any) -> Any:
    try:
        return json.loads(row["raw_json"] or "null")
    except (TypeError, ValueError):
        return None


def _archive_ids(items: list[Any]) -> list[str]:
    ids = []
    for item in items:
        payload = item.get("like") if isinstance(item, dict) else None
        if isinstance(payload, dict):
            tweet_id = str(payload.get("tweetId") or "").strip()
            if tweet_id:
                ids.append(tweet_id)
    return list(dict.fromkeys(ids))


def _reconstructed_archive_ids(items: list[Any], live: list[str]) -> list[str]:
    original = _archive_ids(items)
    repaired = _archive_ids(repair_archive_blocks(items))
    positions = {tweet_id: index for index, tweet_id in enumerate(live)}

    def disagreements(ids: list[str]) -> int:
        anchors = [positions[tweet_id] for tweet_id in ids if tweet_id in positions]
        return sum(left > right for left, right in pairwise(anchors))

    # The export quirk is empirical, not a versioned Twitter/X contract. Keep file order
    # when overlapping live evidence supports it more strongly than the repair.
    return original if disagreements(original) < disagreements(repaired) else repaired


def _capture_key(digest: str, operation: str, filename: str) -> str:
    hashed = hashlib.sha256(f"{digest}\0{operation}\0{filename}".encode()).hexdigest()
    return f"raw_capture:{hashed}"


def _entry_spans(node: Any) -> list[list[Any]]:
    """List timeline entries in pre-order, each with the end of its nested-entry run.

    Matches the timeline parser's entry discovery in one walk. Scalars and tweet
    bodies are skipped: a tweet result never contains timeline entries, and its JSON
    is most of each page. An entry's nested entries are spans[index + 1 : end].
    """
    spans: list[list[Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            index = None
            if "entryId" in value and "content" in value:
                index = len(spans)
                spans.append([value, 0])
            for key, child in value.items():
                if key != "tweet_results" and isinstance(child, dict | list):
                    visit(child)
            if index is not None:
                spans[index][1] = len(spans)
        else:
            for child in value:
                if isinstance(child, dict | list):
                    visit(child)

    if isinstance(node, dict | list):
        visit(node)
    return spans


def page_observations(payload: dict[str, Any]) -> PageObservations:
    """Extract one Likes page's tweet observations independently of current memberships.

    Equivalent to parsing each entry (and its nested entries) as a Likes timeline,
    without building full tweet records. Captures are immutable, so callers may
    reuse the result across rebuilds.
    """
    spans = _entry_spans(payload)
    observations: list[tuple[str, int | None, bool]] = []
    for start, (entry, end) in enumerate(spans):
        seen: set[str] = set()
        for nested, _end in spans[start:end]:
            sort_index = _integer(nested.get("sortIndex"))
            for result in _extract_tweet_results_from_content(nested.get("content", {})):
                tweet_id = result.get("rest_id")
                if result.get("__typename") in _UNAVAILABLE_TYPENAMES or not tweet_id:
                    continue
                if tweet_id not in seen:
                    seen.add(tweet_id)
                    observations.append((tweet_id, sort_index, False))
        # An unavailable tweet can still have a usable Likes timeline entry. Only
        # retain such an ID if a membership already exists; don't fabricate tweets.
        entry_id = str(entry.get("entryId") or "")
        if not seen and entry_id.startswith("tweet-"):
            tweet_id = entry_id.removeprefix("tweet-")
            observations.append((tweet_id, _integer(entry.get("sortIndex")), True))
    return tuple(observations)


def _page_ids(page: PageObservations, members: set[str]) -> list[str]:
    observations: dict[str, int | None] = {}
    for tweet_id, sort_index, requires_membership in page:
        if requires_membership and tweet_id not in members:
            continue
        observations.setdefault(tweet_id, sort_index)
    indices = list(observations.values())
    if all(index is not None and index >= 0 for index in indices):
        if len(set(indices)) == len(indices):
            return sorted(observations, key=lambda tweet_id: observations[tweet_id], reverse=True)
    return list(observations)


@dataclass(frozen=True)
class LikeOrder:
    ids: tuple[str, ...]
    unknown: frozenset[str]

    def ordered_ids(self, sort: str) -> list[str]:
        known = [tweet_id for tweet_id in self.ids if tweet_id not in self.unknown]
        if sort == "liked_earliest":
            known.reverse()
        return known + [tweet_id for tweet_id in self.ids if tweet_id in self.unknown]


def _capture_pages(
    store: Any, captures: list[Any]
) -> dict[tuple[str, str | None], PageObservations | None]:
    """Parse only captures not seen by an earlier rebuild on this store.

    Live captures are append-only observations with unique keys. Keying by the
    capture timestamp as well reparses a capture if a row is ever replaced.
    """
    cached = dict(getattr(store, "_like_capture_pages", None) or {})
    keys = [(row["row_key"], row["captured_at"]) for row in captures]
    missing = {row_key for row_key, captured_at in keys if (row_key, captured_at) not in cached}
    if missing:
        rows = store._rows_for_values(
            "raw_capture", "row_key", list(missing), columns=["row_key", "captured_at", "raw_json"]
        )
        for row in rows:
            payload = _payload(row)
            cached[(row["row_key"], row["captured_at"])] = (
                page_observations(payload) if isinstance(payload, dict) else None
            )
    pages = {key: cached[key] for key in keys if key in cached}
    if not store.conn.in_transaction:
        store._like_capture_pages = pages
    return pages


def load_like_order(store: Any) -> LikeOrder:
    """Recover captures only when like-order inputs change, including external writes."""
    signature = store.cache_revision("like_order")
    cached = getattr(store, "_like_order_cache", None)
    if not store.conn.in_transaction and cached is not None and cached[0] == signature:
        return cached[1]

    memberships = store.conn.execute(
        "SELECT tweet_id, sort_index, source FROM archive "
        "WHERE record_type = 'tweet' AND collection_type = 'like' ORDER BY tweet_id"
    ).fetchall()
    members = {row["tweet_id"] for row in memberships}
    if not members:
        result = LikeOrder((), frozenset())
        store._like_order_cache = None if store.conn.in_transaction else (signature, result)
        return result

    # Join paginated captures by cursor, not by sortIndex magnitudes from different
    # requests. Some responses use request-relative indices rather than like IDs.
    captures = store.conn.execute(
        "SELECT row_key, captured_at, cursor_in, cursor_out FROM archive "
        "WHERE record_type = 'raw_capture' AND operation = 'Likes' "
        "AND source = 'live_graphql' AND http_status = 200 "
        "ORDER BY captured_at, rowid"
    ).fetchall()
    pages = _capture_pages(store, captures)
    crawls: list[list[str]] = []
    tails: dict[str, int] = {}
    disconnected: list[bool] = []
    for row in captures:
        page = pages.get((row["row_key"], row["captured_at"]))
        if page is None:
            continue
        ids = _page_ids(page, members)
        cursor_in = row["cursor_in"]
        crawl_index = tails.get(cursor_in) if cursor_in else None
        if crawl_index is None:
            crawl_index = len(crawls)
            crawls.append([])
            disconnected.append(bool(cursor_in))
        crawls[crawl_index].extend(ids)
        if row["cursor_out"]:
            tails[row["cursor_out"]] = crawl_index
    live: list[str] = []
    for ids, is_tail in zip(crawls, disconnected, strict=True):
        live = merge_missing(ids, live, unanchored="before" if is_tail else "after")

    # Last-resort live values cover legacy databases without page captures. Only
    # positive indices count; archive positions can carry a live content source
    # in databases affected by the old import precedence bug.
    stored_live = [
        (index, row["tweet_id"])
        for row in memberships
        if row["source"] in (None, "live_graphql")
        and (index := _integer(row["sort_index"])) is not None
        and index > 0
    ]
    live = merge_missing(live, [tweet_id for _, tweet_id in sorted(stored_live, reverse=True)])

    # Manifest keys identify archive boundaries and declared part order even if
    # imports were retried or the files were captured out of order. Never repair
    # each part separately, or repair a truncated/missing-part dataset.
    archive: list[str] = []
    accounted: set[str] = set()
    manifests = store.conn.execute(
        "SELECT archive_digest FROM archive WHERE record_type = 'import_manifest' "
        "ORDER BY COALESCE(archive_generation_date, import_started_at), row_key"
    ).fetchall()
    for manifest in manifests:
        digest = manifest["archive_digest"]
        if not digest:
            continue
        raw_manifest = store.conn.execute(
            "SELECT raw_json FROM archive WHERE row_key = ?",
            (_capture_key(digest, "XArchiveManifest", "data/manifest.js"),),
        ).fetchone()
        payload = _payload(raw_manifest) if raw_manifest else None
        if not isinstance(payload, dict):
            continue
        data_types = payload.get("dataTypes")
        if not isinstance(data_types, dict):
            continue
        dataset = data_types.get("like")
        if not isinstance(dataset, dict):
            continue
        files = dataset.get("files") or []
        if not isinstance(files, list):
            continue
        combined: list[Any] = []
        archive_info = payload.get("archiveInfo") or {}
        complete = bool(files) and not (
            isinstance(archive_info, dict) and archive_info.get("isPartialArchive")
        )
        for file in files:
            filename = file.get("fileName") if isinstance(file, dict) else None
            if not isinstance(filename, str):
                complete = False
                continue
            key = _capture_key(digest, "XArchiveLikes", filename)
            part = store.conn.execute(
                "SELECT raw_json FROM archive WHERE row_key = ?", (key,)
            ).fetchone()
            items = _payload(part) if part else None
            if not isinstance(items, list):
                complete = False
                continue
            accounted.add(key)
            combined.extend(items)
        ids = _reconstructed_archive_ids(combined, live) if complete else _archive_ids(combined)
        archive = merge_missing(ids, archive)

    for row in store.conn.execute(
        "SELECT row_key, raw_json FROM archive WHERE record_type = 'raw_capture' "
        "AND operation = 'XArchiveLikes' ORDER BY captured_at DESC, rowid DESC"
    ):
        if row["row_key"] in accounted:
            continue
        items = _payload(row)
        if isinstance(items, list):
            archive = merge_missing(archive, _archive_ids(items))
    stored_archive = [
        (index, row["tweet_id"])
        for row in memberships
        if (index := _integer(row["sort_index"])) is not None and index < 0
    ]
    archive = merge_missing(
        archive, [tweet_id for _, tweet_id in sorted(stored_archive, reverse=True)]
    )
    ordered = [tweet_id for tweet_id in merge_missing(live, archive) if tweet_id in members]
    unknown = members - set(ordered)
    result = LikeOrder(tuple(ordered + sorted(unknown)), frozenset(unknown))
    store._like_order_cache = None if store.conn.in_transaction else (signature, result)
    return result
