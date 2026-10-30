"""On-disk query ID cache."""

from __future__ import annotations

import fcntl
import json
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from pydantic import AwareDatetime, BaseModel, Field

from tweetnook.config import XDGPaths
from tweetnook.core.fileio import atomic_write_text
from tweetnook.query_ids.constants import FALLBACK_QUERY_IDS

DEFAULT_TTL_SECONDS = 24 * 60 * 60


class QueryIdCache(BaseModel):
    fetched_at: AwareDatetime | None = None
    ttl_seconds: int = DEFAULT_TTL_SECONDS
    ids: dict[str, str] = Field(default_factory=dict)
    refreshed_at: dict[str, AwareDatetime] = Field(default_factory=dict)
    # When discovery last completed for an operation, even if X no longer exposes it. Without
    # this, an operation served by a fallback ID would never stop asking for a refresh.
    checked_at: dict[str, AwareDatetime] = Field(default_factory=dict)


class QueryIdStore:
    def __init__(self, paths: XDGPaths):
        self.path = paths.query_id_cache_file
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> QueryIdCache:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            cache = QueryIdCache.model_validate(raw)
        except (FileNotFoundError, ValueError, UnicodeError):
            return QueryIdCache()
        # Released caches have one timestamp covering their complete saved mapping.
        if "refreshed_at" not in raw and cache.fetched_at is not None:
            cache.refreshed_at = {operation: cache.fetched_at for operation in cache.ids}
        return cache

    @contextmanager
    def _save_lock(self) -> Iterator[None]:
        with self.path.with_name(f"{self.path.name}.lock").open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield

    def save(
        self,
        ids: dict[str, str],
        *,
        checked: Iterable[str] = (),
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> QueryIdCache:
        now = datetime.now(tz=UTC)
        checked_at = {operation: now for operation in [*ids, *checked]}
        # Refreshes of different operations can overlap across processes; each merges into the
        # latest file so neither drops the other's IDs.
        with self._save_lock():
            cache = self.load()
            if not checked_at:
                return cache
            cache.checked_at.update(checked_at)
            if ids:
                cache.ids.update(ids)
                cache.refreshed_at.update({operation: now for operation in ids})
                cache.fetched_at = now
            cache.ttl_seconds = ttl_seconds
            # Rediscovered if lost, so a crash needn't be survived at the cost of a flush.
            atomic_write_text(self.path, cache.model_dump_json(indent=2), fsync=False)
        return cache

    def is_fresh(
        self,
        cache: QueryIdCache | None = None,
        *,
        operations: Iterable[str] | None = None,
        now: datetime | None = None,
    ) -> bool:
        """Whether discovery has looked for these operations within the cache lifetime."""
        cache = cache or self.load()
        selected = list(cache.ids if operations is None else operations)
        stamps = {**cache.refreshed_at, **cache.checked_at}
        return bool(selected) and all(
            self._within_ttl(cache, stamps.get(operation), now) for operation in selected
        )

    @staticmethod
    def _within_ttl(cache: QueryIdCache, stamp: datetime | None, now: datetime | None) -> bool:
        if stamp is None:
            return False
        return ((now or datetime.now(tz=UTC)) - stamp).total_seconds() < cache.ttl_seconds

    def get(self, operation: str) -> str | None:
        cache = self.load()
        # A saved ID that the latest check didn't find again is stale, not current.
        if operation in cache.ids and self._within_ttl(
            cache, cache.refreshed_at.get(operation), None
        ):
            return cache.ids[operation]
        return FALLBACK_QUERY_IDS.get(operation) or cache.ids.get(operation)
