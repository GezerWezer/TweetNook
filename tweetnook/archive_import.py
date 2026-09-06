"""Official Twitter/X archive import helpers."""

from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import re
import tempfile
import zipfile
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path, PurePosixPath
from time import perf_counter
from typing import Any, TypeVar
from urllib.parse import urlsplit

import httpx
from rich.console import Console

from tweetnook.auth import ResolvedAuthBundle, resolve_auth_bundle
from tweetnook.client.base import AdaptiveRequestPacer, build_async_client
from tweetnook.client.timelines import (
    MAX_CONSECUTIVE_FOCAL_ABSENCES,
    FocalResultKind,
    TimelineTweet,
    build_tweet_detail_url,
    fetch_page,
    parse_tweet_detail_response,
)
from tweetnook.config import AppConfig, XDGPaths
from tweetnook.exceptions import (
    APIResponseError,
    ArchiveOwnerMismatchError,
    AuthExpiredError,
    ConfigError,
    FeatureFlagDriftError,
    RateLimitExhaustedError,
    RepeatedFocalAbsenceError,
    StaleQueryIdError,
    TweetNookError,
)
from tweetnook.extractor import ExtractedTweetGraph, extract_secondary_objects
from tweetnook.jobs import (
    ArchiveWriteTracker,
    best_effort_interrupt_optimize,
    is_interrupt_exception,
    locked_archive_job,
    resolve_job_context,
)
from tweetnook.pipeline import current_pipeline
from tweetnook.query_ids import QueryIdStore, refresh_query_ids
from tweetnook.resurrection import resurrection_retry_schedule, transient_retry_at
from tweetnook.storage import ArchiveStore, open_archive_store
from tweetnook.storage.backend import ARCHIVE_SOURCE, LIVE_SOURCE, _PageBuffer
from tweetnook.sync import ProcessLock, sync_collection
from tweetnook.utils import resolve_query_ids, utc_now

_YTD_ASSIGNMENT_RE = re.compile(r"^\s*window\.YTD\.[A-Za-z0-9_.]+\s*=\s*", re.DOTALL)
_MANIFEST_ASSIGNMENT_RE = re.compile(r"^\s*window\.__THAR_CONFIG\s*=\s*", re.DOTALL)
_AUTHORED_IMPORT_PREFETCH_CHUNK = 50
_LIKE_IMPORT_PREFETCH_CHUNK = 200
_DETAIL_ENRICH_WRITE_BATCH = 100
_T = TypeVar("_T")


@dataclass(slots=True)
class _ArchiveIdentity:
    account_id: str | None
    username: str | None
    display_name: str


@dataclass(slots=True)
class _PlaceholderTweetObject:
    tweet_id: str
    text: str
    author_id: str | None = None
    author_username: str | None = None
    author_display_name: str | None = None
    created_at: str | None = None
    conversation_id: str | None = None
    lang: str | None = None
    note_tweet_text: str | None = None
    raw_json: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class _PreparedAuthoredImport:
    timeline_tweet: TimelineTweet
    deleted_at: str | None
    graph: ExtractedTweetGraph


@dataclass(slots=True)
class _PreparedLikeImport:
    tweet_id: str
    timeline_tweet: TimelineTweet
    payload: dict[str, Any]


@dataclass(slots=True)
class ArchiveImportResult:
    skipped: bool = False
    followup_performed: bool = False
    counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    reconciled_collections: list[str] = field(default_factory=list)
    detail_lookups: int = 0
    detail_terminal_unavailable: int = 0
    detail_transient_failures: int = 0
    pending_enrichment: int = 0
    pending_untouched: int = 0
    transient_due: int = 0
    transient_delayed: int = 0


@dataclass(slots=True)
class ArchiveEnrichResult:
    warnings: list[str] = field(default_factory=list)
    reconciled_collections: list[str] = field(default_factory=list)
    detail_lookups: int = 0
    detail_terminal_unavailable: int = 0
    detail_transient_failures: int = 0
    pending_enrichment: int = 0
    selected: int = 0
    completed: int = 0
    classified_unavailable: int = 0
    transient_failures: int = 0
    pending_untouched: int = 0
    transient_due: int = 0
    transient_delayed: int = 0

    def __iter__(self):
        yield self.detail_lookups
        yield self.detail_terminal_unavailable
        yield self.detail_transient_failures
        yield self.pending_enrichment


class ArchiveEnrichmentInterrupted(KeyboardInterrupt):
    def __init__(self, remaining: int):
        super().__init__("Archive enrichment interrupted.")
        self.remaining = remaining


class ArchiveEnrichmentAborted(TweetNookError):
    def __init__(self, remaining: int, cause: Exception):
        super().__init__(f"Archive enrichment stopped after {cause.__class__.__name__}: {cause}")
        self.remaining = remaining
        self.cause = cause


@dataclass(slots=True)
class _FocalAbsenceTracker:
    consecutive: int = 0

    def observe(self, kind: FocalResultKind, tweet_id: str) -> None:
        if kind == FocalResultKind.ABSENT:
            self.consecutive += 1
            if self.consecutive >= MAX_CONSECUTIVE_FOCAL_ABSENCES:
                raise RepeatedFocalAbsenceError(
                    f"TweetDetail omitted its requested focal tweet for "
                    f"{self.consecutive} consecutive responses; last target was {tweet_id}."
                )
        else:
            self.consecutive = 0


def _log_archive_phase(console: Console, prefix: str, message: str) -> None:
    console.print(f"{prefix}: {message}", highlight=False)


def _emit_status(status: Callable[[str], None] | None, message: str) -> None:
    if status is not None:
        status(message)


def _status_printer(
    console: Console, prefix: str, *, force: bool = False
) -> Callable[[str], None] | None:
    pipeline = current_pipeline()
    if pipeline is not None:
        if force:
            return lambda message: pipeline.detail(prefix, message)
        return None
    if not (force or console.is_terminal):
        return None
    return lambda message: _log_archive_phase(console, prefix, message)


def _runner_console(console: Console, *, force: bool = False) -> Console:
    if current_pipeline() is not None:
        return console
    if force or console.is_terminal:
        return console
    return Console(file=StringIO(), force_terminal=False, color_system=None)


def _format_debug_rate(processed: int | None, elapsed: float, *, unit: str) -> str | None:
    if processed is None or processed <= 0 or elapsed <= 0:
        return None
    if unit == "bytes":
        mib_per_second = processed / elapsed / (1024 * 1024)
        return f"{mib_per_second:.1f} MiB/s"
    suffix = unit if unit.endswith("/s") else f"{unit}/s"
    return f"{processed / elapsed:.1f} {suffix}"


def _record_debug_timing(
    status: Callable[[str], None] | None,
    summaries: list[str],
    label: str,
    started_at: float,
    *,
    processed: int | None = None,
    unit: str = "items",
) -> None:
    elapsed = perf_counter() - started_at
    summary = f"{label}: {elapsed:.2f}s"
    rate = _format_debug_rate(processed, elapsed, unit=unit)
    if rate is not None:
        summary += f" ({rate})"
    summaries.append(summary)
    _emit_status(status, f"debug {summary}")


@contextmanager
def _progress_callback(
    console: Console,
    *,
    label: str,
    total: int,
    unit: str,
    leave: bool,
):
    if current_pipeline() is not None or not console.is_terminal or total <= 0:
        yield None
        return
    from tqdm import tqdm

    progress_kwargs: dict[str, Any] = {
        "total": total,
        "desc": label,
        "unit": unit,
        "dynamic_ncols": True,
        "leave": leave,
        "file": console.file,
    }
    if unit == "B":
        progress_kwargs["unit_scale"] = True
        progress_kwargs["unit_divisor"] = 1024
    with tqdm(**progress_kwargs) as progress_bar:
        last_done = 0

        def callback(done: int, _total: int) -> None:
            nonlocal last_done
            delta = done - last_done
            if delta > 0:
                progress_bar.update(delta)
            last_done = done

        yield callback


class _ArchiveInput:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._zip: zipfile.ZipFile | None = None
        self._data_dir: Path | None = None
        if path.is_dir():
            if (path / "data" / "manifest.js").exists():
                self._data_dir = path / "data"
            elif (path / "manifest.js").exists():
                self._data_dir = path
            else:
                raise ConfigError("Archive directory is missing manifest.js.")
        elif path.is_file() and zipfile.is_zipfile(path):
            self._zip = zipfile.ZipFile(path)
        else:
            raise ConfigError(
                "Archive input must be a .zip file or an extracted archive directory."
            )
        try:
            self.manifest = self._load_manifest()
        except Exception:
            self.close()
            raise

    def __enter__(self) -> _ArchiveInput:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()

    def _normalize_name(self, relative_path: str) -> str:
        relative = relative_path.replace("\\", "/").lstrip("/")
        normalized = relative if relative.startswith("data/") else f"data/{relative}"
        parts = [part for part in PurePosixPath(normalized).parts if part not in {"", "."}]
        if any(part == ".." for part in parts):
            raise ConfigError("Archive paths must stay within the archive data/ directory.")
        return "/".join(parts)

    def _directory_path(self, relative_path: str) -> Path:
        assert self._data_dir is not None
        relative = self._normalize_name(relative_path)
        return self._data_dir / relative.removeprefix("data/")

    def read_text(self, relative_path: str) -> str:
        if self._zip is not None:
            return self._zip.read(self._normalize_name(relative_path)).decode("utf-8")
        return self._directory_path(relative_path).read_text(encoding="utf-8")

    @contextmanager
    def open_binary(self, relative_path: str):
        if self._zip is not None:
            handle = self._zip.open(self._normalize_name(relative_path), "r")
            try:
                yield handle
            finally:
                handle.close()
            return
        with self._directory_path(relative_path).open("rb") as handle:
            yield handle

    def iter_files(self, relative_dir: str) -> list[str]:
        prefix = self._normalize_name(relative_dir).rstrip("/") + "/"
        if self._zip is not None:
            return [
                name
                for name in sorted(self._zip.namelist())
                if name.startswith(prefix) and name != prefix
            ]
        directory = self._directory_path(relative_dir)
        if not directory.exists():
            return []
        return [f"{prefix}{path.name}" for path in sorted(directory.iterdir()) if path.is_file()]

    def _load_manifest(self) -> dict[str, Any]:
        raw = self.read_text("data/manifest.js")
        return _parse_assigned_json(raw, _MANIFEST_ASSIGNMENT_RE, "manifest.js")

    def dataset_files(self, key: str) -> list[str]:
        dataset = ((self.manifest.get("dataTypes") or {}).get(key)) or {}
        files = dataset.get("files") or []
        return [
            item["fileName"]
            for item in files
            if isinstance(item, dict) and isinstance(item.get("fileName"), str)
        ]

    def load_dataset(
        self,
        key: str,
        *,
        progress: Callable[[int, int], None] | None = None,
        item_status: Callable[[str], None] | None = None,
    ) -> tuple[list[Any], list[tuple[str, Any]]]:
        items: list[Any] = []
        parts: list[tuple[str, Any]] = []
        files = self.dataset_files(key)
        for index, filename in enumerate(files, start=1):
            if item_status is not None:
                item_status(filename)
            parsed = parse_ytd_js(self.read_text(filename), label=filename)
            parts.append((filename, parsed))
            if isinstance(parsed, list):
                items.extend(parsed)
            if progress is not None:
                progress(index, len(files))
        return items, parts

    def _digest_paths(self) -> list[str]:
        relevant: set[str] = {"data/manifest.js"}
        for dataset_key in (
            "account",
            "profile",
            "tweets",
            "tweetHeaders",
            "deletedTweets",
            "deletedTweetHeaders",
            "like",
        ):
            relevant.update(self.dataset_files(dataset_key))
        tweets_info = ((self.manifest.get("dataTypes") or {}).get("tweets")) or {}
        media_directory = tweets_info.get("mediaDirectory")
        if isinstance(media_directory, str):
            relevant.update(self.iter_files(media_directory))
        return sorted(relevant)

    def _file_size(self, relative_path: str) -> int:
        normalized = self._normalize_name(relative_path)
        if self._zip is not None:
            return self._zip.getinfo(normalized).file_size
        return self._directory_path(normalized).stat().st_size

    def digest_total_bytes(self) -> int:
        return sum(self._file_size(relative_path) for relative_path in self._digest_paths())

    def digest(
        self,
        *,
        progress: Callable[[int, int], None] | None = None,
        item_status: Callable[[str], None] | None = None,
        total_bytes: int | None = None,
    ) -> str:
        digest = hashlib.sha256()
        total_bytes = (
            total_bytes
            if progress is not None and total_bytes is not None
            else (self.digest_total_bytes() if progress is not None else 0)
        )
        processed_bytes = 0
        for relative_path in self._digest_paths():
            if item_status is not None:
                item_status(relative_path)
            digest.update(relative_path.encode("utf-8"))
            with self.open_binary(relative_path) as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
                    processed_bytes += len(chunk)
                    if progress is not None:
                        progress(processed_bytes, total_bytes)
        return digest.hexdigest()


def _parse_assigned_json(raw: str, pattern: re.Pattern[str], label: str) -> Any:
    stripped = pattern.sub("", raw, count=1).strip()
    if stripped.endswith(";"):
        stripped = stripped[:-1].rstrip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Failed to parse {label}.") from exc


def parse_ytd_js(raw: str, *, label: str = "YTD data") -> Any:
    return _parse_assigned_json(raw, _YTD_ASSIGNMENT_RE, label)


def _archive_identity(manifest: dict[str, Any], account_items: list[Any]) -> _ArchiveIdentity:
    account_entry = account_items[0] if account_items and isinstance(account_items[0], dict) else {}
    account_block = account_entry.get("account") or {}
    user_info = manifest.get("userInfo") or {}
    account_id = str(user_info.get("accountId") or account_block.get("accountId") or "").strip()
    if account_id.lower() == "unknown":
        account_id = ""
    username = str(user_info.get("userName") or account_block.get("username") or "").strip()
    if not account_id:
        raise ConfigError("Archive account.js/manifest.js does not include an account id.")
    if not username:
        raise ConfigError("Archive account.js/manifest.js does not include a username.")
    display_name = str(
        user_info.get("displayName") or account_block.get("accountDisplayName") or username
    ).strip()
    return _ArchiveIdentity(
        account_id=account_id or None,
        username=username or None,
        display_name=display_name or username or "Unknown User",
    )


def _media_key_for_archive_item(item: dict[str, Any], *, position: int) -> str:
    media_id = item.get("id_str") or item.get("id")
    media_type = item.get("type")
    prefix = "3" if media_type == "photo" else "7"
    if media_id:
        return f"{prefix}_{media_id}"
    return f"{prefix}_archive_{position}"


def _deepcopy_json(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _adapt_archive_tweet_payload(
    tweet_payload: dict[str, Any], identity: _ArchiveIdentity
) -> dict[str, Any]:
    legacy = _deepcopy_json(tweet_payload)
    tweet_id = str(legacy.get("id_str") or legacy.get("id") or "").strip()
    if not tweet_id:
        raise ConfigError("Archive tweet payload is missing id_str.")
    legacy["id_str"] = tweet_id
    legacy["conversation_id_str"] = str(legacy.get("conversation_id_str") or tweet_id)
    for key in ("entities", "extended_entities"):
        block = legacy.get(key) or {}
        media_items = block.get("media")
        if not isinstance(media_items, list):
            continue
        for position, item in enumerate(media_items):
            if isinstance(item, dict) and not item.get("media_key"):
                item["media_key"] = _media_key_for_archive_item(item, position=position)
    user_result: dict[str, Any] = {"__typename": "User"}
    if identity.account_id:
        user_result["rest_id"] = identity.account_id
    user_legacy: dict[str, Any] = {}
    if identity.username:
        user_legacy["screen_name"] = identity.username
    if identity.display_name:
        user_legacy["name"] = identity.display_name
    if user_legacy:
        user_result["legacy"] = user_legacy
    return {
        "__typename": "Tweet",
        "rest_id": tweet_id,
        "legacy": legacy,
        "core": {"user_results": {"result": user_result}},
    }


def _timeline_tweet_from_archive(
    tweet_payload: dict[str, Any],
    identity: _ArchiveIdentity,
    *,
    sort_index: str | None,
) -> TimelineTweet:
    raw_json = _adapt_archive_tweet_payload(tweet_payload, identity)
    legacy = raw_json.get("legacy") or {}
    return TimelineTweet(
        tweet_id=raw_json["rest_id"],
        text=str(legacy.get("full_text") or ""),
        author_id=identity.account_id,
        author_username=identity.username,
        author_display_name=identity.display_name,
        created_at=legacy.get("created_at"),
        sort_index=sort_index,
        raw_json=raw_json,
    )


def _placeholder_tweet_object(like_payload: dict[str, Any]) -> _PlaceholderTweetObject:
    tweet_id = str(like_payload.get("tweetId") or "").strip()
    if not tweet_id:
        raise ConfigError("Archive like row is missing tweetId.")
    return _PlaceholderTweetObject(
        tweet_id=tweet_id,
        text=str(like_payload.get("fullText") or ""),
        raw_json={"like": like_payload},
    )


def _queue_secondary_graph(
    store: ArchiveStore,
    buffer: _PageBuffer,
    graph: ExtractedTweetGraph,
    *,
    source: str,
    deleted_at_by_tweet_id: dict[str, str] | None = None,
    force_deleted_tweet_id: str | None = None,
) -> None:
    deleted_at_by_tweet_id = deleted_at_by_tweet_id or {}
    for item in graph.tweet_objects.values():
        force_deleted = item.tweet_id == force_deleted_tweet_id
        store._queue_record(
            store._tweet_object_record(
                item,
                source=source,
                deleted_at=deleted_at_by_tweet_id.get(item.tweet_id),
                enrichment_state="terminal_unavailable" if force_deleted else None,
                enrichment_reason="archive_deleted" if force_deleted else None,
                enrichment_retry_count=0 if force_deleted else None,
                enrichment_retry_eligible=False if force_deleted else None,
                cursor=buffer,
            ),
            cursor=buffer,
        )
    for item in graph.relations.values():
        store._queue_record(
            store._tweet_relation_record(item, source=source, cursor=buffer),
            cursor=buffer,
        )
    for item in graph.media.values():
        store._queue_record(
            store._media_record(item, provenance_source=source, cursor=buffer),
            cursor=buffer,
        )
    for item in graph.urls.values():
        store._queue_record(store._url_record(item, source=source, cursor=buffer), cursor=buffer)
    for item in graph.url_refs.values():
        store._queue_record(
            store._url_ref_record(item, source=source, cursor=buffer),
            cursor=buffer,
        )
    for item in graph.articles.values():
        store._queue_record(
            store._article_record(item, source=source, cursor=buffer),
            cursor=buffer,
        )


def _flush_buffer(store: ArchiveStore, buffer: _PageBuffer) -> int:
    if not buffer.records:
        return 0
    pending = len(buffer.records)
    store.merge_rows(list(buffer.records.values()))
    buffer.records.clear()
    buffer.pending_tweets.clear()
    buffer.existing_rows.clear()
    return pending


def _deleted_headers_map(items: list[Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in items:
        tweet = item.get("tweet") if isinstance(item, dict) else {}
        tweet = tweet or {}
        tweet_id = tweet.get("tweet_id")
        deleted_at = tweet.get("deleted_at")
        if isinstance(tweet_id, str) and tweet_id and isinstance(deleted_at, str) and deleted_at:
            result[tweet_id] = deleted_at
    return result


def _chunked(items: list[_T], size: int):
    for index in range(0, len(items), size):
        yield items[index : index + size]


def _secondary_graph_row_keys(store: ArchiveStore, graph: ExtractedTweetGraph) -> list[str]:
    row_keys: list[str] = []
    row_keys.extend(
        store._row_key_for_tweet_object(item.tweet_id) for item in graph.tweet_objects.values()
    )
    row_keys.extend(
        store._row_key_for_tweet_relation(
            item.source_tweet_id,
            item.relation_type,
            item.target_tweet_id,
        )
        for item in graph.relations.values()
    )
    row_keys.extend(
        store._row_key_for_media(item.tweet_id, item.media_key) for item in graph.media.values()
    )
    row_keys.extend(store._row_key_for_url(item.url_hash) for item in graph.urls.values())
    row_keys.extend(
        store._row_key_for_url_ref(item.tweet_id, item.position) for item in graph.url_refs.values()
    )
    row_keys.extend(store._row_key_for_article(item.tweet_id) for item in graph.articles.values())
    return row_keys


def _import_authored_tweets(
    store: ArchiveStore,
    tweets: list[Any],
    identity: _ArchiveIdentity,
    *,
    deleted_headers: dict[str, str],
    counts: dict[str, int],
    force_deleted: bool = False,
    progress: Callable[[int, int], None] | None = None,
    item_status: Callable[[str, int, int], None] | None = None,
    write_tracker: ArchiveWriteTracker | None = None,
) -> None:
    buffer = _PageBuffer()
    valid_items = [
        item for item in tweets if isinstance(item, dict) and isinstance(item.get("tweet"), dict)
    ]
    total = len(valid_items)
    processed = 0
    for chunk_items in _chunked(valid_items, _AUTHORED_IMPORT_PREFETCH_CHUNK):
        prepared: list[_PreparedAuthoredImport] = []
        for item in chunk_items:
            payload = item["tweet"]
            timeline_tweet = _timeline_tweet_from_archive(
                payload,
                identity,
                sort_index=str(payload.get("id_str") or payload.get("id") or ""),
            )
            deleted_at = deleted_headers.get(timeline_tweet.tweet_id) or payload.get("deleted_at")
            prepared.append(
                _PreparedAuthoredImport(
                    timeline_tweet=timeline_tweet,
                    deleted_at=deleted_at if isinstance(deleted_at, str) else None,
                    graph=extract_secondary_objects(timeline_tweet.raw_json),
                )
            )
        if not prepared:
            continue
        row_keys: list[str] = []
        for item in prepared:
            row_keys.append(store._row_key_for_tweet(item.timeline_tweet.tweet_id, "tweet"))
            row_keys.extend(_secondary_graph_row_keys(store, item.graph))
        store.prefetch_rows(row_keys, cursor=buffer)
        for item in prepared:
            timeline_tweet = item.timeline_tweet
            if item_status is not None:
                item_status(timeline_tweet.tweet_id, processed, total)
            store.upsert_tweet(timeline_tweet, cursor=buffer)
            store.upsert_membership(
                timeline_tweet.tweet_id,
                "tweet",
                source=ARCHIVE_SOURCE,
                deleted_at=item.deleted_at,
                sort_index=timeline_tweet.sort_index,
                cursor=buffer,
            )
            _queue_secondary_graph(
                store,
                buffer,
                item.graph,
                source=ARCHIVE_SOURCE,
                deleted_at_by_tweet_id=(
                    {timeline_tweet.tweet_id: item.deleted_at} if item.deleted_at else {}
                ),
                force_deleted_tweet_id=timeline_tweet.tweet_id if force_deleted else None,
            )
            if force_deleted or item.deleted_at:
                counts["deleted_authored_tweets"] += 1
            else:
                counts["authored_tweets"] += 1
            processed += 1
            if progress:
                progress(processed, total)
        flushed = _flush_buffer(store, buffer)
        if write_tracker is not None and flushed > 0:
            write_tracker.mark_dirty(rows=flushed, batches=1)


def _should_seed_like_placeholder(store: ArchiveStore, buffer: _PageBuffer, tweet_id: str) -> bool:
    row = store._lookup_row(store._row_key_for_tweet_object(tweet_id), cursor=buffer)
    if row is None:
        return True
    if row.get("source") == LIVE_SOURCE:
        return False
    return not any(
        row.get(field_name)
        for field_name in ("author_id", "author_username", "created_at", "conversation_id", "lang")
    )


def _import_likes(
    store: ArchiveStore,
    likes: list[Any],
    *,
    counts: dict[str, int],
    progress: Callable[[int, int], None] | None = None,
    item_status: Callable[[str, int, int], None] | None = None,
    write_tracker: ArchiveWriteTracker | None = None,
) -> None:
    buffer = _PageBuffer()
    prepared: list[_PreparedLikeImport] = []
    for index, item in enumerate(likes, start=1):
        payload = item.get("like") if isinstance(item, dict) else None
        if not isinstance(payload, dict):
            continue
        tweet_id = str(payload.get("tweetId") or "").strip()
        if not tweet_id:
            continue
        prepared.append(
            _PreparedLikeImport(
                tweet_id=tweet_id,
                timeline_tweet=TimelineTweet(
                    tweet_id=tweet_id,
                    text=str(payload.get("fullText") or ""),
                    author_id=None,
                    author_username=None,
                    author_display_name=None,
                    created_at=None,
                    # Existing list/view code sorts these numerically descending, so -1 stays
                    # ahead of -2 and preserves the original archive file order without a
                    # special-case code path.
                    sort_index=str(-index),
                    raw_json={"like": payload},
                ),
                payload=payload,
            )
        )
    total = len(prepared)
    processed = 0
    for chunk in _chunked(prepared, _LIKE_IMPORT_PREFETCH_CHUNK):
        row_keys: list[str] = []
        for item in chunk:
            row_keys.append(store._row_key_for_tweet(item.tweet_id, "like"))
            row_keys.append(store._row_key_for_tweet_object(item.tweet_id))
        store.prefetch_rows(row_keys, cursor=buffer)
        for item in chunk:
            if item_status is not None:
                item_status(item.tweet_id, processed, total)
            store.upsert_tweet(item.timeline_tweet, cursor=buffer)
            store.upsert_membership(
                item.tweet_id,
                "like",
                source=ARCHIVE_SOURCE,
                sort_index=item.timeline_tweet.sort_index,
                cursor=buffer,
            )
            if _should_seed_like_placeholder(store, buffer, item.tweet_id):
                store._queue_record(
                    store._tweet_object_record(
                        _placeholder_tweet_object(item.payload),
                        source=ARCHIVE_SOURCE,
                        enrichment_state="pending",
                        cursor=buffer,
                    ),
                    cursor=buffer,
                )
            counts["likes"] += 1
            processed += 1
            if progress:
                progress(processed, total)
        flushed = _flush_buffer(store, buffer)
        if write_tracker is not None and flushed > 0:
            write_tracker.mark_dirty(rows=flushed, batches=1)


def _url_basename(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    name = Path(urlsplit(value).path).name
    return name or None


def _copy_file_from_archive(
    source: _ArchiveInput, relative_path: str, destination: Path
) -> tuple[str, int]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256()
    byte_size = 0
    with tempfile.NamedTemporaryFile(
        "wb",
        dir=destination.parent,
        delete=False,
        prefix=f"{destination.name}.",
        suffix=".tmp",
    ) as handle:
        temp_path = Path(handle.name)
        try:
            with source.open_binary(relative_path) as input_handle:
                for chunk in iter(lambda: input_handle.read(1024 * 1024), b""):
                    handle.write(chunk)
                    sha.update(chunk)
                    byte_size += len(chunk)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
    temp_path.replace(destination)
    return sha.hexdigest(), byte_size


def _resolve_media_path(base_dir: Path, relative_path: Any) -> Path | None:
    if not isinstance(relative_path, str) or not relative_path:
        return None
    return base_dir / relative_path


def _media_download_complete(base_dir: Path, row: dict[str, Any]) -> bool:
    local_path = _resolve_media_path(base_dir, row.get("local_path"))
    if local_path is None or not local_path.exists():
        return False
    if row.get("media_type") not in {"video", "animated_gif"}:
        return True
    poster_path = _resolve_media_path(base_dir, row.get("thumbnail_local_path"))
    return poster_path is not None and poster_path.exists()


def _copy_exported_media(
    source: _ArchiveInput,
    store: ArchiveStore,
    paths: XDGPaths,
    media_directory: str | None,
    *,
    counts: dict[str, int],
    warnings: list[str],
    progress: Callable[[int, int], None] | None = None,
    item_status: Callable[[str, int, int], None] | None = None,
    limit: int | None = None,
    write_tracker: ArchiveWriteTracker | None = None,
) -> None:
    if not media_directory:
        return
    files = _slice_for_sample(source.iter_files(media_directory), limit=limit)
    if not files:
        return

    rows = store.list_media_rows()
    row_by_asset: dict[tuple[str, str], tuple[dict[str, Any], bool]] = {}
    for row in rows:
        tweet_id = row.get("tweet_id")
        if not isinstance(tweet_id, str) or not tweet_id:
            continue
        media_name = _url_basename(row.get("media_url"))
        if media_name:
            row_by_asset[(tweet_id, media_name)] = (row, False)
        thumbnail_name = _url_basename(row.get("thumbnail_url"))
        if thumbnail_name and thumbnail_name != media_name:
            row_by_asset[(tweet_id, thumbnail_name)] = (row, True)

    pending_updates: dict[str, dict[str, Any]] = {}
    unmatched = 0
    total = len(files)
    for index, relative_path in enumerate(files, start=1):
        if item_status is not None:
            item_status(relative_path, index - 1, total)
        filename = Path(relative_path).name
        if "-" not in filename:
            unmatched += 1
        else:
            tweet_id, asset_name = filename.split("-", 1)
            target = row_by_asset.get((tweet_id, asset_name))
            if target is None:
                unmatched += 1
            else:
                row, is_thumbnail = target
                suffix = "-poster" if is_thumbnail else ""
                media_key = str(row.get("media_key") or "media")
                extension = Path(asset_name).suffix.lower() or ".bin"
                relative_dest = Path("media") / tweet_id / f"{media_key}{suffix}{extension}"
                destination = paths.data_dir / relative_dest
                if not destination.exists():
                    sha256, byte_size = _copy_file_from_archive(source, relative_path, destination)
                    counts["media_files_copied"] += 1
                else:
                    sha256 = str(
                        (row.get("thumbnail_sha256") if is_thumbnail else row.get("sha256")) or ""
                    )
                    byte_size = int(
                        (row.get("thumbnail_byte_size") if is_thumbnail else row.get("byte_size"))
                        or 0
                    )
                content_type = mimetypes.guess_type(destination.name)[0]
                base_row = pending_updates.get(str(row["row_key"]), row)
                local_path = (
                    base_row.get("local_path") if is_thumbnail else relative_dest.as_posix()
                )
                thumbnail_local_path = (
                    relative_dest.as_posix()
                    if is_thumbnail
                    else base_row.get("thumbnail_local_path")
                )
                updated_row = dict(base_row)
                updated_row.update(
                    {
                        "local_path": local_path,
                        "sha256": base_row.get("sha256") if is_thumbnail else sha256 or None,
                        "byte_size": (
                            base_row.get("byte_size") if is_thumbnail else byte_size or None
                        ),
                        "content_type": (
                            base_row.get("content_type") if is_thumbnail else content_type
                        ),
                        "thumbnail_local_path": thumbnail_local_path,
                        "thumbnail_sha256": (sha256 or None)
                        if is_thumbnail
                        else base_row.get("thumbnail_sha256"),
                        "thumbnail_byte_size": (byte_size or None)
                        if is_thumbnail
                        else base_row.get("thumbnail_byte_size"),
                        "thumbnail_content_type": content_type
                        if is_thumbnail
                        else base_row.get("thumbnail_content_type"),
                    }
                )
                complete = _media_download_complete(paths.data_dir, updated_row)
                pending_updates[str(row["row_key"])] = store.build_media_download_update(
                    base_row,
                    download_state="done" if complete else "pending",
                    local_path=local_path,
                    sha256=updated_row.get("sha256"),
                    byte_size=updated_row.get("byte_size"),
                    content_type=updated_row.get("content_type"),
                    thumbnail_local_path=thumbnail_local_path,
                    thumbnail_sha256=updated_row.get("thumbnail_sha256"),
                    thumbnail_byte_size=updated_row.get("thumbnail_byte_size"),
                    thumbnail_content_type=updated_row.get("thumbnail_content_type"),
                    downloaded_at=(
                        utc_now() if complete else (base_row.get("downloaded_at") or None)
                    ),
                    download_error=None,
                )
        if len(pending_updates) >= 100:
            updates = list(pending_updates.values())
            store.merge_rows(updates)
            if write_tracker is not None:
                write_tracker.mark_dirty(rows=len(updates), batches=1)
            pending_updates.clear()
        if progress:
            progress(index, total)
    if pending_updates:
        updates = list(pending_updates.values())
        store.merge_rows(updates)
        if write_tracker is not None:
            write_tracker.mark_dirty(rows=len(updates), batches=1)
    if unmatched:
        warnings.append(f"{unmatched} archive media files did not match normalized media rows.")


def _followup_collections_from_counts(counts: dict[str, int]) -> list[str]:
    collections: list[str] = []
    if counts.get("authored_tweets") or counts.get("deleted_authored_tweets"):
        collections.append("tweets")
    if counts.get("likes"):
        collections.append("likes")
    return collections


def _resolve_archive_followup_auth(
    config: AppConfig,
    auth_bundle: ResolvedAuthBundle | None,
) -> ResolvedAuthBundle:
    if auth_bundle is not None:
        return auth_bundle
    pipeline = current_pipeline()
    step_key = "archive-auth"
    if pipeline is not None:
        pipeline.add_step(
            step_key,
            "Authentication",
            total=1,
            unit="session",
            detail="resolve the configured Twitter/X session values for live follow-up",
            show_rate=False,
            show_eta=False,
        )
        pipeline.start_step(
            step_key,
            activity="Resolving Twitter/X authentication for archive follow-up",
        )
    try:
        resolved = resolve_auth_bundle(config)
    except ConfigError as exc:
        if pipeline is not None:
            pipeline.fail_step(step_key, str(exc))
        raise
    if pipeline is not None:
        pipeline.complete_step(step_key, "Twitter/X authentication resolved")
    return resolved


async def _run_live_reconciliation(
    *,
    collections: list[str],
    config: AppConfig,
    paths: XDGPaths,
    auth_bundle: ResolvedAuthBundle | None,
    transport: httpx.AsyncBaseTransport | None,
    console: Console,
    status: Callable[[str], None] | None = None,
) -> tuple[list[str], list[str], ResolvedAuthBundle | None]:
    warnings: list[str] = []
    pipeline = current_pipeline()
    if pipeline is not None:
        for collection in ("tweets", "likes"):
            if collection in collections:
                continue
            for step_key in (f"preflight:{collection}", f"sync:{collection}:head"):
                if pipeline.has_step(step_key):
                    pipeline.skip_step(
                        step_key,
                        f"the archive containing no {collection} dataset rows",
                    )
    try:
        resolved_auth = _resolve_archive_followup_auth(config, auth_bundle)
    except ConfigError as exc:
        warnings.append(f"live reconciliation skipped: {exc}")
        if pipeline is not None:
            for collection in collections:
                for step_key in (f"preflight:{collection}", f"sync:{collection}:head"):
                    if pipeline.has_step(step_key):
                        pipeline.skip_step(step_key, "Twitter/X authentication being unavailable")
        return [], warnings, None

    completed: list[str] = []
    for collection in collections:
        try:
            _emit_status(status, f"running live {collection} reconciliation...")
            await sync_collection(
                collection,
                full=False,
                resume_backfill=False,
                limit=None,
                config=config,
                paths=paths,
                auth_bundle=resolved_auth,
                transport=transport,
                console=console,
            )
            completed.append(collection)
        except Exception as exc:
            warnings.append(f"{collection} reconciliation failed: {exc}")
    return completed, warnings, resolved_auth


async def _run_archive_followup(
    *,
    collections: list[str],
    detail_limit: int | None,
    reconcile_live: bool,
    config: AppConfig,
    paths: XDGPaths,
    auth_bundle: ResolvedAuthBundle | None,
    transport: httpx.AsyncBaseTransport | None,
    console: Console,
    status: Callable[[str], None] | None = None,
    enrichment_started_at: str | None = None,
) -> ArchiveEnrichResult:
    warnings: list[str] = []
    if reconcile_live:
        reconciled_collections, reconcile_warnings, resolved_auth = await _run_live_reconciliation(
            collections=collections,
            config=config,
            paths=paths,
            auth_bundle=auth_bundle,
            transport=transport,
            console=console,
            status=status,
        )
        warnings.extend(reconcile_warnings)
    else:
        reconciled_collections = []
        try:
            resolved_auth = _resolve_archive_followup_auth(config, auth_bundle)
        except ConfigError as exc:
            warnings.append(f"detail enrichment skipped: {exc}")
            resolved_auth = None
    enrichment_result = ArchiveEnrichResult()
    if resolved_auth is not None:
        try:
            absence_tracker = _FocalAbsenceTracker()
            enrichment_result = await _enrich_pending_rows(
                limit=detail_limit,
                config=config,
                paths=paths,
                auth_bundle=resolved_auth,
                transport=transport,
                console=console,
                status=status,
                absence_tracker=absence_tracker,
                started_at=enrichment_started_at,
            )
            if isinstance(enrichment_result, tuple):
                completed, unavailable, transient, remaining = enrichment_result
                enrichment_result = ArchiveEnrichResult(
                    detail_lookups=completed,
                    detail_terminal_unavailable=unavailable,
                    detail_transient_failures=transient,
                    pending_enrichment=remaining,
                    selected=completed + unavailable + transient,
                    completed=completed,
                    classified_unavailable=unavailable,
                    transient_failures=transient,
                    pending_untouched=remaining,
                )
        except Exception as exc:
            async with locked_archive_job(config=config, paths=paths) as job:
                remaining = job.store.count_incomplete_initial_enrichment()
            raise ArchiveEnrichmentAborted(remaining, exc) from exc
    else:
        async with locked_archive_job(config=config, paths=paths) as job:
            enrichment_result = _archive_enrich_counts(job.store)
        pipeline = current_pipeline()
        if pipeline is not None and pipeline.has_step("archive-enrich"):
            pipeline.skip_step("archive-enrich", "Twitter/X authentication being unavailable")
    enrichment_result.warnings = warnings
    enrichment_result.reconciled_collections = reconciled_collections
    pipeline = current_pipeline()
    if pipeline is not None:
        for warning in warnings:
            pipeline.issue(
                warning,
                dedupe_key=f"archive-followup:{warning.split(':', 1)[0]}",
            )
    return enrichment_result


def _archive_enrich_counts(store: ArchiveStore, *, now: str | None = None) -> ArchiveEnrichResult:
    return ArchiveEnrichResult(
        pending_enrichment=store.count_incomplete_initial_enrichment(),
        pending_untouched=store.count_pending_initial_enrichment(),
        transient_due=store.count_due_transient_enrichment(now),
        transient_delayed=store.count_delayed_transient_enrichment(now),
    )


async def _enrich_pending_rows(
    *,
    limit: int | None,
    config: AppConfig,
    paths: XDGPaths,
    auth_bundle: ResolvedAuthBundle,
    transport: httpx.AsyncBaseTransport | None,
    console: Console,
    status: Callable[[str], None] | None = None,
    absence_tracker: _FocalAbsenceTracker | None = None,
    started_at: str | None = None,
) -> ArchiveEnrichResult:
    pipeline = current_pipeline()
    if limit is not None and limit <= 0:
        async with locked_archive_job(config=config, paths=paths, console=console) as job:
            result = _archive_enrich_counts(job.store)
        if pipeline is not None and pipeline.has_step("archive-enrich"):
            pipeline.skip_step("archive-enrich", "detail enrichment being disabled for this run")
        return result

    async with locked_archive_job(config=config, paths=paths, console=console) as job:
        store = job.store
        selection_started_at = started_at or utc_now()
        rows = store.list_tweet_objects_for_enrichment(limit=limit, now=selection_started_at)
        desc_type = "eligible sparse tweets"
        if not rows:
            result = _archive_enrich_counts(store, now=selection_started_at)
            _emit_status(status, "no archive enrichment rows are currently due")
            if result.transient_delayed:
                _emit_status(
                    status,
                    f"{result.transient_delayed:,} transient failures remain scheduled "
                    "for later retry",
                )
            if pipeline is not None and pipeline.has_step("archive-enrich"):
                pipeline.skip_step(
                    "archive-enrich",
                    "no sparse archive tweets being eligible for enrichment",
                )
            return result
        queue_counts = _archive_enrich_counts(store, now=selection_started_at)
        step_key = "archive-enrich"
        if pipeline is not None:
            pipeline.add_step(
                step_key,
                "Enrich",
                total=len(rows),
                unit="tweets",
                detail=(
                    f"stable command-start snapshot · {queue_counts.transient_delayed} "
                    f"delayed outside this run · {_DETAIL_ENRICH_WRITE_BATCH}-attempt checkpoints"
                ),
                rate_unit="tweets/s",
            )
            pipeline.start_step(
                step_key,
                activity=f"Fetching live details for tweet {rows[0]['tweet_id']}",
                counters="0 selected · 0 refreshed · 0 unavailable · 0 transient",
            )
        limit_suffix = "" if limit is None else f" (limit {limit})"
        _emit_status(status, f"detail enrichment over {len(rows)} {desc_type}{limit_suffix}")
        query_store = QueryIdStore(paths)
        query_ids = await resolve_query_ids(
            query_store,
            ["TweetDetail"],
            force_refresh=not query_store.is_fresh(),
            transport=transport,
        )
        client = build_async_client(auth_bundle, timeout=config.sync.timeout, transport=transport)
        succeeded = 0
        terminal = 0
        transient = 0
        pacer = AdaptiveRequestPacer(config.sync.detail_delay)
        write_buffer = _PageBuffer()
        buffered_writes = 0
        absence_tracker = absence_tracker or _FocalAbsenceTracker()

        def flush_detail_writes() -> None:
            nonlocal buffered_writes
            if buffered_writes <= 0:
                return
            _flush_buffer(store, write_buffer)
            job.mark_dirty(rows=buffered_writes, batches=1)
            buffered_writes = 0

        try:
            with _progress_callback(
                console,
                label="archive enrich detail",
                total=len(rows),
                unit="tweets",
                leave=False,
            ) as detail_progress:
                for index, row in enumerate(rows, start=1):
                    tweet_id = row["tweet_id"]
                    if pipeline is not None:
                        pipeline.update_step(
                            step_key,
                            completed=index - 1,
                            activity=f"Fetching live details for tweet {tweet_id}",
                            counters=(
                                f"{index - 1} selected · {succeeded} refreshed · "
                                f"{terminal} unavailable · {transient} transient"
                            ),
                        )
                    wrote_row = False
                    absence_error: RepeatedFocalAbsenceError | None = None
                    await pacer.wait(attempted=index - 1)

                    async def refresh_once(tweet_id: str = tweet_id) -> str:
                        refreshed = await refresh_query_ids(
                            query_store,
                            operations=["TweetDetail"],
                            client=client,
                        )
                        query_ids.update(refreshed)
                        return build_tweet_detail_url(query_ids["TweetDetail"], tweet_id)

                    write_checkpoint = write_buffer.checkpoint()
                    try:
                        response = await fetch_page(
                            client,
                            build_tweet_detail_url(query_ids["TweetDetail"], tweet_id),
                            config.sync,
                            max_retries=config.sync.detail_max_retries,
                            backoff_base=config.sync.detail_backoff_base,
                            refresh_once=refresh_once,
                            status=(
                                (
                                    lambda message, tweet_id=tweet_id: _emit_status(
                                        status, f"detail {tweet_id}: {message}"
                                    )
                                )
                                if status is not None
                                else (
                                    lambda message, tweet_id=tweet_id: pipeline.status(
                                        step_key,
                                        f"Enrichment tweet {tweet_id}: {message}",
                                        important=True,
                                    )
                                    if pipeline is not None
                                    else None
                                )
                            ),
                        )
                        pacer.observe(
                            response,
                            status=(
                                status
                                if status is not None
                                else (
                                    lambda message: pipeline.status(
                                        step_key, message, important=True
                                    )
                                    if pipeline is not None
                                    else None
                                )
                            ),
                        )
                        payload = response.json()
                        focal = parse_tweet_detail_response(payload, tweet_id)
                        if focal.kind == FocalResultKind.AVAILABLE and focal.tweet is not None:
                            absence_tracker.observe(focal.kind, tweet_id)
                            store.persist_tweet_detail(
                                tweet=focal.tweet,
                                raw_json=payload,
                                http_status=response.status_code,
                                cursor=write_buffer,
                            )
                            succeeded += 1
                        elif focal.kind == FocalResultKind.EXPLICIT_UNAVAILABLE:
                            absence_tracker.observe(focal.kind, tweet_id)
                            assert focal.unavailable is not None
                            eligible, next_retry = resurrection_retry_schedule(
                                focal.unavailable.reason, 0
                            )
                            store.persist_unavailable_tweet(
                                tweet_id=tweet_id,
                                operation="TweetDetail",
                                raw_json=payload,
                                http_status=response.status_code,
                                reason=focal.unavailable.reason,
                                detail=focal.unavailable.detail,
                                retry_eligible=eligible,
                                next_retry_at=next_retry,
                                retry_count=0,
                                cursor=write_buffer,
                            )
                            terminal += 1
                        else:
                            assert focal.kind == FocalResultKind.ABSENT
                            assert focal.unavailable is not None
                            write_buffer.restore(write_checkpoint)
                            retry_count = int(row.get("enrichment_retry_count") or 0) + 1
                            store.update_tweet_object_enrichment(
                                tweet_id,
                                enrichment_state="transient_failure",
                                enrichment_checked_at=utc_now(),
                                enrichment_http_status=response.status_code,
                                enrichment_reason="focal_tweet_absent",
                                enrichment_detail=focal.unavailable.detail,
                                enrichment_retry_count=retry_count,
                                enrichment_next_retry_at=transient_retry_at(retry_count),
                                enrichment_retry_eligible=None,
                                cursor=write_buffer,
                            )
                            transient += 1
                            if pipeline is not None:
                                pipeline.issue(
                                    f"Enrichment tweet {tweet_id}: focal tweet absent; "
                                    "retry scheduled",
                                    dedupe_key="archive-enrich:focal-absence",
                                )
                        wrote_row = True
                        if focal.kind == FocalResultKind.ABSENT:
                            try:
                                absence_tracker.observe(focal.kind, tweet_id)
                            except RepeatedFocalAbsenceError as exc:
                                absence_error = exc
                    except APIResponseError as exc:
                        if isinstance(
                            exc,
                            StaleQueryIdError
                            | AuthExpiredError
                            | FeatureFlagDriftError
                            | RateLimitExhaustedError,
                        ):
                            raise
                        if exc.status_code == 410:
                            eligible, next_retry = resurrection_retry_schedule("not_found", 0)
                            store.persist_unavailable_tweet(
                                tweet_id=tweet_id,
                                operation="TweetDetail",
                                raw_json={"http_status": 410, "error": str(exc)},
                                http_status=exc.status_code,
                                reason="not_found",
                                detail=str(exc),
                                retry_eligible=eligible,
                                next_retry_at=next_retry,
                                retry_count=0,
                                cursor=write_buffer,
                            )
                            terminal += 1
                            wrote_row = True
                        else:
                            retry_count = int(row.get("enrichment_retry_count") or 0) + 1
                            store.update_tweet_object_enrichment(
                                tweet_id,
                                enrichment_state="transient_failure",
                                enrichment_checked_at=utc_now(),
                                enrichment_http_status=exc.status_code,
                                enrichment_reason=None,
                                enrichment_detail=str(exc),
                                enrichment_retry_count=retry_count,
                                enrichment_next_retry_at=transient_retry_at(retry_count),
                                enrichment_retry_eligible=None,
                                cursor=write_buffer,
                            )
                            transient += 1
                            wrote_row = True
                            if pipeline is not None:
                                pipeline.issue(
                                    f"Enrichment tweet {tweet_id}: {exc}",
                                    dedupe_key="archive-enrich:api-failure",
                                )
                    except httpx.TransportError as exc:
                        retry_count = int(row.get("enrichment_retry_count") or 0) + 1
                        store.update_tweet_object_enrichment(
                            tweet_id,
                            enrichment_state="transient_failure",
                            enrichment_checked_at=utc_now(),
                            enrichment_http_status=None,
                            enrichment_reason=None,
                            enrichment_detail=f"{exc.__class__.__name__}: {exc}",
                            enrichment_retry_count=retry_count,
                            enrichment_next_retry_at=transient_retry_at(retry_count),
                            enrichment_retry_eligible=None,
                            cursor=write_buffer,
                        )
                        transient += 1
                        wrote_row = True
                        if pipeline is not None:
                            pipeline.issue(
                                f"Enrichment tweet {tweet_id}: {exc.__class__.__name__}: {exc}",
                                dedupe_key="archive-enrich:transport-failure",
                            )
                    except Exception:
                        write_buffer.restore(write_checkpoint)
                        raise
                    if wrote_row:
                        buffered_writes += 1
                        if buffered_writes >= _DETAIL_ENRICH_WRITE_BATCH:
                            flush_detail_writes()
                            if pipeline is not None:
                                pipeline.status(
                                    step_key,
                                    f"Committed {index}/{len(rows)} enrichment attempts",
                                    important=True,
                                )
                    if absence_error is not None:
                        flush_detail_writes()
                        raise absence_error
                    if detail_progress:
                        detail_progress(index, len(rows))
                    if pipeline is not None:
                        pipeline.update_step(
                            step_key,
                            completed=index,
                            counters=(
                                f"{index} selected · {succeeded} refreshed · "
                                f"{terminal} unavailable · {transient} transient"
                            ),
                        )
        finally:
            try:
                await client.aclose()
            finally:
                flush_detail_writes()
        result = _archive_enrich_counts(store)
        result.selected = len(rows)
        result.completed = succeeded
        result.classified_unavailable = terminal
        result.transient_failures = transient
        result.detail_lookups = succeeded
        result.detail_terminal_unavailable = terminal
        result.detail_transient_failures = transient
        if pipeline is not None:
            pipeline.complete_step(
                step_key,
                f"{succeeded} refreshed · {terminal} unavailable · {transient} transient · "
                f"{result.pending_untouched} pending untouched · "
                f"{result.transient_due} due · {result.transient_delayed} delayed",
            )
    return result


def _manifest_generation_date(manifest: dict[str, Any]) -> str | None:
    archive_info = manifest.get("archiveInfo") or {}
    generation = archive_info.get("generationDate")
    return generation if isinstance(generation, str) and generation else None


def _record_archive_capture(
    store: ArchiveStore,
    operation: str,
    filename: str,
    payload: Any,
    *,
    archive_digest: str,
    write_tracker: ArchiveWriteTracker | None = None,
) -> None:
    capture_key = hashlib.sha256(f"{archive_digest}\0{operation}\0{filename}".encode()).hexdigest()
    store.append_raw_capture(
        operation,
        filename,
        None,
        200,
        payload,
        source=ARCHIVE_SOURCE,
        capture_key=capture_key,
    )
    if write_tracker is not None:
        write_tracker.mark_dirty()


def _slice_for_sample(items: list[Any], *, limit: int | None) -> list[Any]:
    if limit is None:
        return items
    return items[:limit]


def _safe_relative_data_path(relative_path: str) -> Path | None:
    relative = Path(relative_path)
    if relative.is_absolute():
        return None
    if any(part == ".." for part in relative.parts):
        return None
    return relative


def _remove_archive_owned_files(base_dir: Path, relative_paths: list[str]) -> int:
    removed = 0
    for relative_path in sorted(set(relative_paths)):
        relative = _safe_relative_data_path(relative_path)
        if relative is None or relative.parts[:1] != ("media",):
            continue
        destination = base_dir / relative
        if not destination.exists() or not destination.is_file():
            continue
        destination.unlink()
        removed += 1
        parent = destination.parent
        while parent != base_dir:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
    return removed


def _initial_counts() -> dict[str, int]:
    return {
        "authored_tweets": 0,
        "deleted_authored_tweets": 0,
        "likes": 0,
        "media_files_copied": 0,
    }


def _manifest_counts(manifest_row: dict[str, Any] | None) -> dict[str, int]:
    if not manifest_row:
        return _initial_counts()
    raw = manifest_row.get("counts_json")
    if not isinstance(raw, str) or not raw:
        return _initial_counts()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return _initial_counts()
    if not isinstance(parsed, dict):
        return _initial_counts()
    counts = _initial_counts()
    for key in counts:
        value = parsed.get(key)
        if isinstance(value, int):
            counts[key] = value
    return counts


def _manifest_all_counts(manifest_row: dict[str, Any] | None) -> dict[str, Any]:
    if not manifest_row:
        return _initial_counts()
    raw = manifest_row.get("counts_json")
    if not isinstance(raw, str) or not raw:
        return _initial_counts()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return _initial_counts()
    return parsed if isinstance(parsed, dict) else _initial_counts()


def _manifest_warnings(manifest_row: dict[str, Any] | None) -> list[str]:
    if not manifest_row:
        return []
    raw = manifest_row.get("warnings_json")
    if not isinstance(raw, str) or not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, str) and item]


def _list_import_manifest_rows(store: ArchiveStore) -> list[dict[str, Any]]:
    return store._query("record_type = 'import_manifest' AND status = 'completed'")


def _finalize_import_enrichment_status(
    store: ArchiveStore,
    manifest_row: dict[str, Any],
    *,
    followup_status: str,
    aborted_reason: str | None = None,
    count_updates: dict[str, Any] | None = None,
) -> None:
    digest = manifest_row.get("archive_digest")
    if not isinstance(digest, str) or not digest:
        return
    counts = _manifest_all_counts(manifest_row)
    if count_updates:
        counts.update(count_updates)
    counts["pending_enrichment"] = store.count_incomplete_initial_enrichment()
    counts["transient_due"] = store.count_due_transient_enrichment()
    counts["transient_delayed"] = store.count_delayed_transient_enrichment()
    counts.update(store.enrichment_status_counts())
    store.set_import_manifest(
        digest,
        archive_generation_date=manifest_row.get("archive_generation_date"),
        status=str(manifest_row.get("status") or "completed"),
        import_started_at=manifest_row.get("import_started_at"),
        import_completed_at=utc_now(),
        warnings=_manifest_warnings(manifest_row),
        counts=counts,
        enrichment_followup_status=followup_status,
        enrichment_aborted_reason=aborted_reason,
    )


def _aggregate_import_counts(manifest_rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = _initial_counts()
    for row in manifest_rows:
        parsed = _manifest_counts(row)
        for key, value in parsed.items():
            counts[key] += value
    return counts


async def enrich_imported_archive(
    *,
    limit: int | None = None,
    reconcile_live: bool = False,
    config: AppConfig | None = None,
    paths: XDGPaths | None = None,
    auth_bundle: ResolvedAuthBundle | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    console: Console | None = None,
) -> ArchiveEnrichResult:
    config, paths = resolve_job_context(config=config, paths=paths)
    enrichment_started_at = utc_now()
    console = console or Console(stderr=True)
    status = _status_printer(console, "archive enrich")
    runner_console = _runner_console(console)
    _emit_status(status, "loading completed archive imports...")
    try:
        async with locked_archive_job(config=config, paths=paths) as job:
            manifest_rows = _list_import_manifest_rows(job.store)
            if not manifest_rows:
                raise ConfigError(
                    "No completed Twitter/X archive import found. Run "
                    "'tweetnook import x-archive' first."
                )
            counts = _aggregate_import_counts(manifest_rows)
    except ConfigError as exc:
        if str(exc) == "No local archive found.":
            raise ConfigError(
                "No completed Twitter/X archive import found. Run "
                "'tweetnook import x-archive' first."
            ) from exc
        raise
    try:
        result = await _run_archive_followup(
            collections=_followup_collections_from_counts(counts),
            detail_limit=limit,
            reconcile_live=reconcile_live,
            config=config,
            paths=paths,
            auth_bundle=auth_bundle,
            transport=transport,
            console=runner_console,
            status=status,
            enrichment_started_at=enrichment_started_at,
        )
    except (KeyboardInterrupt, asyncio.CancelledError) as exc:
        async with locked_archive_job(config=config, paths=paths) as job:
            remaining = job.store.count_incomplete_initial_enrichment()
            for manifest_row in _list_import_manifest_rows(job.store):
                _finalize_import_enrichment_status(
                    job.store,
                    manifest_row,
                    followup_status="enrichment_interrupted",
                    aborted_reason=str(exc) or exc.__class__.__name__,
                )
        raise ArchiveEnrichmentInterrupted(remaining) from exc
    except Exception as exc:
        async with locked_archive_job(config=config, paths=paths) as job:
            for manifest_row in _list_import_manifest_rows(job.store):
                _finalize_import_enrichment_status(
                    job.store,
                    manifest_row,
                    followup_status="enrichment_aborted",
                    aborted_reason=str(exc),
                )
        raise
    else:
        async with locked_archive_job(config=config, paths=paths) as job:
            count_updates = {
                "detail_lookups": result.detail_lookups,
                "detail_terminal_unavailable": result.detail_terminal_unavailable,
                "detail_transient_failures": result.detail_transient_failures,
            }
            for manifest_row in _list_import_manifest_rows(job.store):
                _finalize_import_enrichment_status(
                    job.store,
                    manifest_row,
                    followup_status="enrichment_complete",
                    count_updates=count_updates,
                )
        pipeline = current_pipeline()
        if pipeline is not None:
            pipeline.final_note(
                f"Archive enrichment queue: {result.pending_untouched:,} pending untouched · "
                f"{result.transient_due:,} transient due · "
                f"{result.transient_delayed:,} transient delayed."
            )
        return result


async def import_x_archive(
    archive_path: Path,
    *,
    detail_lookups: int = 0,
    enrich: bool = True,
    offline: bool = False,
    regen: bool = False,
    sample_limit: int | None = None,
    debug: bool = False,
    config: AppConfig | None = None,
    paths: XDGPaths | None = None,
    auth_bundle: ResolvedAuthBundle | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    console: Console | None = None,
) -> ArchiveImportResult:
    config, paths = resolve_job_context(config=config, paths=paths)
    console = console or Console(stderr=True)
    pipeline = current_pipeline()
    status = _status_printer(console, "archive import", force=debug)
    runner_console = _runner_console(console, force=debug)
    if sample_limit is not None and sample_limit <= 0:
        raise ConfigError("--sample-limit must be greater than zero.")
    _emit_status(status, f"opening {archive_path}")
    debug_summaries: list[str] = []
    with _ArchiveInput(archive_path) as source:
        _emit_status(status, "hashing archive contents for idempotence check...")
        hash_started = perf_counter()
        digest_total_bytes = (
            source.digest_total_bytes()
            if (debug or console.is_terminal or pipeline is not None)
            else None
        )
        inspect_step_key = "archive-inspect"
        if pipeline is not None:
            pipeline.add_step(
                inspect_step_key,
                "Inspect",
                total=max(digest_total_bytes or 0, 1),
                unit="bytes",
                detail=f"{archive_path.name} · content hash and archive owner validation",
                rate_unit="bytes/s",
            )
            pipeline.start_step(
                inspect_step_key,
                activity=f"Hashing {archive_path.name} for idempotence",
                counters="verifying every imported dataset and media file",
            )
        with _progress_callback(
            console,
            label="archive import hash",
            total=digest_total_bytes or 0,
            unit="B",
            leave=debug,
        ) as hash_progress:

            def update_hash(done: int, total: int) -> None:
                if hash_progress is not None:
                    hash_progress(done, total)
                if pipeline is not None:
                    pipeline.update_step(
                        inspect_step_key,
                        completed=done,
                        total=max(total, 1),
                        counters=f"{done:,}/{total:,} bytes hashed",
                    )

            digest = source.digest(
                progress=update_hash if pipeline is not None else hash_progress,
                item_status=(
                    lambda relative_path: pipeline.status(
                        inspect_step_key, f"Hashing {relative_path}"
                    )
                    if pipeline is not None
                    else None
                ),
                total_bytes=digest_total_bytes,
            )
        if debug:
            _record_debug_timing(
                status,
                debug_summaries,
                "archive hash",
                hash_started,
                processed=digest_total_bytes,
                unit="bytes",
            )
        generation_date = _manifest_generation_date(source.manifest)
        counts = _initial_counts()
        warnings: list[str] = []
        sampled_import = sample_limit is not None
        final_status = "sampled" if sampled_import else "completed"
        if sampled_import:
            warnings.append(
                f"sampled import: limiting authored tweets, deleted tweets, likes, and "
                f"media files to {sample_limit} items each after full dataset load"
            )
            warnings.append(
                "sampled import still hashes and parses the full archive files before slicing "
                "imported rows"
            )
        _emit_status(status, "loading archive account metadata...")
        account_started = perf_counter()
        account_items, account_parts = source.load_dataset("account")
        if debug:
            _record_debug_timing(
                status,
                debug_summaries,
                "archive account metadata load",
                account_started,
                processed=len(account_parts),
                unit="parts",
            )
        identity = _archive_identity(source.manifest, account_items)
        if pipeline is not None:
            owner = f"@{identity.username}" if identity.username else "archive owner resolved"
            pipeline.complete_step(
                inspect_step_key,
                f"archive hash verified · {owner}",
            )
        existing_manifest: dict[str, Any] | None = None
        store: ArchiveStore | None = None
        write_tracker: ArchiveWriteTracker | None = None
        import_started_at = utc_now()
        followup_requested = not offline and (enrich or detail_lookups > 0)
        followup_performed = False
        import_performed = False
        pending_after_import = 0

        lock = ProcessLock(paths.lock_file)
        lock.acquire()
        try:
            store = open_archive_store(paths, create=True, config=config)
            assert store is not None
            from tweetnook.reminders import print_archive_migration_report

            print_archive_migration_report(console, store)
            write_tracker = ArchiveWriteTracker(store)
            existing_owner = store.get_archive_owner_id()
            if identity.account_id is None and existing_owner is not None:
                raise ArchiveOwnerMismatchError(
                    "Archive owner could not be determined, but the local archive already "
                    f"belongs to Twitter/X user {existing_owner}."
                )
            store.ensure_archive_owner_id(identity.account_id)
            if regen:
                regen_step_key = "archive-regen"
                if pipeline is not None:
                    pipeline.add_step(
                        regen_step_key,
                        "Regenerate",
                        total=1,
                        unit="cleanup",
                        detail="archive-import-owned rows and managed media only",
                        show_rate=False,
                        show_eta=False,
                    )
                    pipeline.start_step(
                        regen_step_key,
                        activity="Clearing the previous import owned by this archive",
                    )
                _emit_status(status, "clearing previously imported archive-owned rows...")
                regen_started = perf_counter()
                archive_media_paths = store.list_archive_import_media_paths()
                cleared_rows = store.clear_archive_import_data()
                write_tracker.mark_dirty(rows=sum(cleared_rows.values()), batches=1)
                cleared_files = _remove_archive_owned_files(paths.data_dir, archive_media_paths)
                existing_manifest = None
                _emit_status(
                    status,
                    f"regen cleared {sum(cleared_rows.values())} rows and {cleared_files} "
                    "managed archive media files",
                )
                if pipeline is not None:
                    pipeline.complete_step(
                        regen_step_key,
                        f"{sum(cleared_rows.values()):,} rows removed · "
                        f"{cleared_files:,} managed media files removed",
                    )
                if debug:
                    _record_debug_timing(
                        status,
                        debug_summaries,
                        "archive-only regen cleanup",
                        regen_started,
                        processed=sum(cleared_rows.values()) + cleared_files,
                        unit="objects",
                    )
            existing_manifest = store.get_import_manifest(digest)
            if existing_manifest and existing_manifest.get("status") == "completed":
                counts = _manifest_counts(existing_manifest)
                warnings = _manifest_warnings(existing_manifest)
                if pipeline is not None:
                    for step_key in (
                        "archive-source",
                        "archive-tweets",
                        "archive-deleted",
                        "archive-likes",
                        "archive-media",
                    ):
                        if pipeline.has_step(step_key):
                            pipeline.skip_step(step_key, "this archive already being imported")
                store.close()
                if not followup_requested:
                    if pipeline is not None:
                        for step_key in (
                            "archive-auth",
                            "preflight:tweets",
                            "sync:tweets:head",
                            "preflight:likes",
                            "sync:likes:head",
                        ):
                            if pipeline.has_step(step_key):
                                pipeline.skip_step(
                                    step_key,
                                    "the duplicate archive having no requested follow-up",
                                )
                        pipeline.final_note(
                            "Archive already imported; no follow-up work requested."
                        )
                    return ArchiveImportResult(
                        skipped=True,
                        followup_performed=False,
                        counts=counts,
                        warnings=["archive already imported; skipping duplicate import"],
                    )
            else:
                import_performed = True
                _emit_status(status, "loading archive datasets...")
                dataset_started = perf_counter()
                source_step_key = "archive-source"
                source_dataset_keys = (
                    "tweetHeaders",
                    "deletedTweets",
                    "deletedTweetHeaders",
                    "tweets",
                    "like",
                )
                source_part_total = sum(
                    len(source.dataset_files(key)) for key in source_dataset_keys
                )
                source_parts_loaded = 0
                if pipeline is not None and source_part_total:
                    first_source_file = next(
                        filename
                        for key in source_dataset_keys
                        for filename in source.dataset_files(key)
                    )
                    pipeline.add_step(
                        source_step_key,
                        "Archive source",
                        total=source_part_total * 2,
                        unit="operations",
                        detail="parse every dataset part · preserve every raw payload",
                        rate_unit="operations/s",
                    )
                    pipeline.start_step(
                        source_step_key,
                        activity=f"Parsing {first_source_file}",
                        counters=f"0/{source_part_total} parts parsed · 0 preserved",
                    )

                def load_source_dataset(key: str):
                    nonlocal source_parts_loaded
                    part_files = source.dataset_files(key)
                    offset = source_parts_loaded

                    def update_source_part(done: int, _total: int) -> None:
                        if pipeline is not None:
                            pipeline.update_step(
                                source_step_key,
                                completed=offset + done,
                                counters=(
                                    f"{offset + done}/{source_part_total} parts parsed · "
                                    "0 preserved"
                                ),
                            )

                    loaded = source.load_dataset(
                        key,
                        progress=(
                            update_source_part
                            if pipeline is not None and source_part_total
                            else None
                        ),
                        item_status=(
                            (
                                lambda filename: pipeline.status(
                                    source_step_key, f"Parsing {filename}"
                                )
                            )
                            if pipeline is not None and source_part_total
                            else None
                        ),
                    )
                    source_parts_loaded += len(part_files)
                    return loaded

                store.set_import_manifest(
                    digest,
                    archive_generation_date=generation_date,
                    status="in_progress",
                    import_started_at=import_started_at,
                    warnings=warnings,
                    counts=counts,
                )
                write_tracker.mark_dirty()
                _record_archive_capture(
                    store,
                    "XArchiveManifest",
                    "data/manifest.js",
                    source.manifest,
                    archive_digest=digest,
                    write_tracker=write_tracker,
                )
                for filename, payload in account_parts:
                    _record_archive_capture(
                        store,
                        "XArchiveAccount",
                        filename,
                        payload,
                        archive_digest=digest,
                        write_tracker=write_tracker,
                    )

                tweets_info = ((source.manifest.get("dataTypes") or {}).get("tweets")) or {}
                if "bookmark" not in {
                    key.lower() for key in (source.manifest.get("dataTypes") or {})
                }:
                    warnings.append(
                        "archive does not contain a bookmark dataset "
                        "(expected for current official Twitter/X archives)"
                    )

                _, tweet_header_parts = load_source_dataset("tweetHeaders")
                deleted_tweets, deleted_tweet_parts = load_source_dataset("deletedTweets")
                deleted_headers_items, deleted_header_parts = load_source_dataset(
                    "deletedTweetHeaders"
                )
                tweets, tweet_parts = load_source_dataset("tweets")
                likes, like_parts = load_source_dataset("like")
                if debug:
                    _record_debug_timing(
                        status,
                        debug_summaries,
                        "archive dataset load",
                        dataset_started,
                        processed=(
                            len(tweets)
                            + len(deleted_tweets)
                            + len(likes)
                            + len(tweet_parts)
                            + len(deleted_tweet_parts)
                            + len(deleted_header_parts)
                            + len(tweet_header_parts)
                            + len(like_parts)
                        ),
                        unit="records",
                    )
                total_tweets = len(tweets)
                total_deleted_tweets = len(deleted_tweets)
                total_likes = len(likes)
                tweets = _slice_for_sample(tweets, limit=sample_limit)
                deleted_tweets = _slice_for_sample(deleted_tweets, limit=sample_limit)
                likes = _slice_for_sample(likes, limit=sample_limit)
                _emit_status(
                    status,
                    (
                        f"loaded {total_tweets} tweets, "
                        f"{total_deleted_tweets} deleted tweets, "
                        f"{total_likes} likes"
                        if not sampled_import
                        else (
                            f"loaded {total_tweets} tweets -> sampling {len(tweets)}, "
                            f"{total_deleted_tweets} deleted tweets -> sampling "
                            f"{len(deleted_tweets)}, {total_likes} likes -> sampling "
                            f"{len(likes)}"
                        )
                    ),
                )

                media_directory = (
                    tweets_info.get("mediaDirectory") if isinstance(tweets_info, dict) else None
                )
                media_total = 0
                if isinstance(media_directory, str):
                    media_total = len(
                        _slice_for_sample(source.iter_files(media_directory), limit=sample_limit)
                    )
                if pipeline is not None:
                    if not source_part_total:
                        pipeline.skip_step(
                            "archive-source", "the archive containing no tweet or like datasets"
                        )
                    if tweets:
                        pipeline.add_step(
                            "archive-tweets",
                            "Tweets",
                            total=len(tweets),
                            unit="tweets",
                            detail=(
                                "authored archive rows · normalized objects, relations, and raw "
                                "source"
                            ),
                            rate_unit="tweets/s",
                        )
                    if deleted_tweets:
                        pipeline.add_step(
                            "archive-deleted",
                            "Deleted tweets",
                            total=len(deleted_tweets),
                            unit="tweets",
                            detail=(
                                "deleted authored rows · archive deletion provenance preserved"
                            ),
                            rate_unit="tweets/s",
                        )
                    if likes:
                        pipeline.add_step(
                            "archive-likes",
                            "Likes",
                            total=len(likes),
                            unit="likes",
                            detail=(
                                "archive like memberships · sparse objects queued for live "
                                "enrichment"
                            ),
                            rate_unit="likes/s",
                        )
                    if media_total:
                        pipeline.add_step(
                            "archive-media",
                            "Archive media",
                            total=media_total,
                            unit="files",
                            detail="exported media matched to normalized tweet assets",
                            rate_unit="files/s",
                        )
                    for present, step_key, reason in (
                        (tweets, "archive-tweets", "the authored-tweet dataset being empty"),
                        (
                            deleted_tweets,
                            "archive-deleted",
                            "the deleted-tweet dataset being empty",
                        ),
                        (likes, "archive-likes", "the likes dataset being empty"),
                        (media_total, "archive-media", "the archive containing no exported media"),
                    ):
                        if not present:
                            pipeline.skip_step(step_key, reason)

                capture_started = perf_counter()
                raw_source_parts = [
                    *(
                        ("XArchiveTweetHeaders", filename, payload)
                        for filename, payload in tweet_header_parts
                    ),
                    *(
                        ("XArchiveDeletedTweetHeaders", filename, payload)
                        for filename, payload in deleted_header_parts
                    ),
                    *(("XArchiveTweets", filename, payload) for filename, payload in tweet_parts),
                    *(
                        ("XArchiveDeletedTweets", filename, payload)
                        for filename, payload in deleted_tweet_parts
                    ),
                    *(("XArchiveLikes", filename, payload) for filename, payload in like_parts),
                ]
                for index, (operation, filename, payload) in enumerate(raw_source_parts, start=1):
                    if pipeline is not None and source_part_total:
                        pipeline.update_step(
                            source_step_key,
                            completed=source_part_total + index - 1,
                            activity=f"Preserving {filename}",
                            counters=(
                                f"{source_part_total}/{source_part_total} parts parsed · "
                                f"{index - 1}/{source_part_total} preserved"
                            ),
                        )
                    _record_archive_capture(
                        store,
                        operation,
                        filename,
                        payload,
                        archive_digest=digest,
                        write_tracker=write_tracker,
                    )
                    if pipeline is not None and source_part_total:
                        pipeline.update_step(
                            source_step_key,
                            completed=source_part_total + index,
                            counters=(
                                f"{source_part_total}/{source_part_total} parts parsed · "
                                f"{index}/{source_part_total} preserved"
                            ),
                        )
                if pipeline is not None and source_part_total:
                    record_total = total_tweets + total_deleted_tweets + total_likes
                    pipeline.complete_step(
                        source_step_key,
                        f"{source_part_total:,} parts parsed and preserved · "
                        f"{record_total:,} tweet/like records loaded",
                    )
                if debug:
                    _record_debug_timing(
                        status,
                        debug_summaries,
                        "archive raw-capture persist",
                        capture_started,
                        processed=(
                            2
                            + len(tweet_header_parts)
                            + len(deleted_header_parts)
                            + len(tweet_parts)
                            + len(deleted_tweet_parts)
                            + len(like_parts)
                        ),
                        unit="rows",
                    )

                deleted_headers = _deleted_headers_map(deleted_headers_items)
                _emit_status(status, f"importing {len(tweets)} authored tweets...")
                authored_started = perf_counter()
                authored_step_key = "archive-tweets"
                authored_start_count = counts["authored_tweets"] + counts["deleted_authored_tweets"]
                if pipeline is not None and tweets:
                    pipeline.add_step(
                        authored_step_key,
                        "Tweets",
                        total=len(tweets),
                        unit="tweets",
                        detail=(
                            "authored archive rows · normalized objects, relations, and raw source"
                        ),
                        rate_unit="tweets/s",
                    )
                    pipeline.start_step(
                        authored_step_key,
                        activity="Preparing authored tweet import",
                        counters="0 imported",
                    )
                with _progress_callback(
                    console,
                    label="archive import authored",
                    total=len(tweets),
                    unit="tweets",
                    leave=debug,
                ) as authored_progress:
                    _import_authored_tweets(
                        store,
                        tweets,
                        identity,
                        deleted_headers=deleted_headers,
                        counts=counts,
                        force_deleted=False,
                        progress=authored_progress,
                        item_status=(
                            lambda tweet_id, done, total: pipeline.update_step(
                                authored_step_key,
                                completed=done,
                                total=total,
                                activity=f"Importing authored tweet {tweet_id}",
                                counters=f"{done} imported",
                            )
                            if pipeline is not None
                            else None
                        ),
                        write_tracker=write_tracker,
                    )
                if pipeline is not None and tweets:
                    imported = (
                        counts["authored_tweets"]
                        + counts["deleted_authored_tweets"]
                        - authored_start_count
                    )
                    pipeline.complete_step(
                        authored_step_key, f"{imported:,} authored rows imported"
                    )
                if debug:
                    _record_debug_timing(
                        status,
                        debug_summaries,
                        "authored tweet import",
                        authored_started,
                        processed=len(tweets),
                        unit="tweets",
                    )
                _emit_status(status, f"importing {len(deleted_tweets)} deleted authored tweets...")
                deleted_started = perf_counter()
                deleted_step_key = "archive-deleted"
                deleted_start_count = counts["deleted_authored_tweets"]
                if pipeline is not None and deleted_tweets:
                    pipeline.add_step(
                        deleted_step_key,
                        "Deleted tweets",
                        total=len(deleted_tweets),
                        unit="tweets",
                        detail="deleted authored rows · archive deletion provenance preserved",
                        rate_unit="tweets/s",
                    )
                    pipeline.start_step(
                        deleted_step_key,
                        activity="Preparing deleted authored tweet import",
                        counters="0 imported",
                    )
                with _progress_callback(
                    console,
                    label="archive import deleted",
                    total=len(deleted_tweets),
                    unit="tweets",
                    leave=debug,
                ) as deleted_progress:
                    _import_authored_tweets(
                        store,
                        deleted_tweets,
                        identity,
                        deleted_headers=deleted_headers,
                        counts=counts,
                        force_deleted=True,
                        progress=deleted_progress,
                        item_status=(
                            lambda tweet_id, done, total: pipeline.update_step(
                                deleted_step_key,
                                completed=done,
                                total=total,
                                activity=f"Importing deleted tweet {tweet_id}",
                                counters=f"{done} imported",
                            )
                            if pipeline is not None
                            else None
                        ),
                        write_tracker=write_tracker,
                    )
                if pipeline is not None and deleted_tweets:
                    imported = counts["deleted_authored_tweets"] - deleted_start_count
                    pipeline.complete_step(deleted_step_key, f"{imported:,} deleted rows imported")
                if debug:
                    _record_debug_timing(
                        status,
                        debug_summaries,
                        "deleted authored tweet import",
                        deleted_started,
                        processed=len(deleted_tweets),
                        unit="tweets",
                    )
                _emit_status(status, f"importing {len(likes)} likes...")
                likes_started = perf_counter()
                likes_step_key = "archive-likes"
                likes_start_count = counts["likes"]
                if pipeline is not None and likes:
                    pipeline.add_step(
                        likes_step_key,
                        "Likes",
                        total=len(likes),
                        unit="likes",
                        detail=(
                            "archive like memberships · sparse objects queued for live enrichment"
                        ),
                        rate_unit="likes/s",
                    )
                    pipeline.start_step(
                        likes_step_key,
                        activity="Preparing liked tweet import",
                        counters="0 imported",
                    )
                with _progress_callback(
                    console,
                    label="archive import likes",
                    total=len(likes),
                    unit="likes",
                    leave=debug,
                ) as likes_progress:
                    _import_likes(
                        store,
                        likes,
                        counts=counts,
                        progress=likes_progress,
                        item_status=(
                            lambda tweet_id, done, total: pipeline.update_step(
                                likes_step_key,
                                completed=done,
                                total=total,
                                activity=f"Importing liked tweet {tweet_id}",
                                counters=f"{done} imported",
                            )
                            if pipeline is not None
                            else None
                        ),
                        write_tracker=write_tracker,
                    )
                if pipeline is not None and likes:
                    imported = counts["likes"] - likes_start_count
                    pipeline.complete_step(likes_step_key, f"{imported:,} likes imported")
                if debug:
                    _record_debug_timing(
                        status,
                        debug_summaries,
                        "like import",
                        likes_started,
                        processed=len(likes),
                        unit="likes",
                    )
                _emit_status(status, "copying exported media files...")
                media_started = perf_counter()
                media_step_key = "archive-media"
                media_start_count = counts["media_files_copied"]
                if pipeline is not None and media_total:
                    pipeline.add_step(
                        media_step_key,
                        "Archive media",
                        total=media_total,
                        unit="files",
                        detail="exported media matched to normalized tweet assets",
                        rate_unit="files/s",
                    )
                    pipeline.start_step(
                        media_step_key,
                        activity="Preparing exported media copy",
                        counters="0 scanned · 0 copied",
                    )
                warning_count_before_media = len(warnings)
                with _progress_callback(
                    console,
                    label="archive import media",
                    total=media_total,
                    unit="files",
                    leave=debug,
                ) as media_progress:
                    _copy_exported_media(
                        source,
                        store,
                        paths,
                        media_directory,
                        counts=counts,
                        warnings=warnings,
                        progress=media_progress,
                        item_status=(
                            lambda relative_path, done, total: pipeline.update_step(
                                media_step_key,
                                completed=done,
                                total=total,
                                activity=f"Copying {relative_path}",
                                counters=(
                                    f"{done} scanned · "
                                    f"{counts['media_files_copied'] - media_start_count} copied"
                                ),
                            )
                            if pipeline is not None
                            else None
                        ),
                        limit=sample_limit,
                        write_tracker=write_tracker,
                    )
                if pipeline is not None and media_total:
                    copied = counts["media_files_copied"] - media_start_count
                    pipeline.complete_step(
                        media_step_key,
                        f"{media_total:,} scanned · {copied:,} copied",
                    )
                    for warning in warnings[warning_count_before_media:]:
                        pipeline.issue(warning, dedupe_key="archive-media:unmatched")
                if debug:
                    _record_debug_timing(
                        status,
                        debug_summaries,
                        "archive media copy",
                        media_started,
                        processed=media_total,
                        unit="files",
                    )
                pending_after_import = store.count_tweet_objects_for_enrichment()
                store.set_import_manifest(
                    digest,
                    archive_generation_date=generation_date,
                    status=final_status,
                    import_started_at=import_started_at,
                    import_completed_at=utc_now(),
                    warnings=warnings,
                    counts=counts,
                )
                write_tracker.mark_dirty()
            store.close()
        except BaseException as exc:
            try:
                if store is not None and import_performed:
                    store.set_import_manifest(
                        digest,
                        archive_generation_date=generation_date,
                        status="failed",
                        import_started_at=import_started_at,
                        import_completed_at=utc_now(),
                        warnings=warnings,
                        counts=counts,
                    )
                    if write_tracker is not None:
                        write_tracker.mark_dirty()
                if store is not None and write_tracker is not None and is_interrupt_exception(exc):
                    best_effort_interrupt_optimize(store, write_tracker, console=runner_console)
            finally:
                if store is not None:
                    store.close()
            raise
        finally:
            lock.release()

        if offline and not sampled_import:
            if pipeline is not None:
                pipeline.final_note(
                    "Local archive import complete; live follow-up skipped (offline)."
                )
            return ArchiveImportResult(
                skipped=not import_performed,
                followup_performed=False,
                counts=counts,
                warnings=warnings,
                pending_enrichment=pending_after_import,
            )

        if sampled_import:
            warnings.append(
                "sampled import skipped automatic live reconciliation and detail enrichment; "
                "rerun without --sample-limit for normal follow-up"
            )
            final_counts = dict(counts)
            final_counts["pending_enrichment"] = pending_after_import
            if debug:
                _emit_status(status, "debug summary:")
                for summary in debug_summaries:
                    _emit_status(status, f"  {summary}")
            if pipeline is not None:
                pipeline.final_note(
                    f"Sample imported: {counts['authored_tweets']:,} authored · "
                    f"{counts['deleted_authored_tweets']:,} deleted · {counts['likes']:,} likes · "
                    f"{counts['media_files_copied']:,} media copied."
                )
            return ArchiveImportResult(
                skipped=False,
                followup_performed=False,
                counts=final_counts,
                warnings=warnings,
                pending_enrichment=pending_after_import,
            )

        # Bulk archive writes are complete at this point. The follow-up sync/enrichment helpers
        # reacquire the same process lock around their own writes, so we intentionally do not hold
        # the outer lock across potentially long network I/O.
        _emit_status(status, "running follow-up reconciliation and enrichment...")
        followup_started = perf_counter()
        try:
            followup = await _run_archive_followup(
                collections=_followup_collections_from_counts(counts),
                detail_limit=(detail_lookups if detail_lookups > 0 else None if enrich else 0),
                reconcile_live=True,
                config=config,
                paths=paths,
                auth_bundle=auth_bundle,
                transport=transport,
                console=runner_console,
                status=status,
            )
        except (KeyboardInterrupt, asyncio.CancelledError) as exc:
            async with locked_archive_job(config=config, paths=paths) as job:
                remaining = job.store.count_incomplete_initial_enrichment()
                manifest_row = job.store.get_import_manifest(digest)
                if manifest_row is not None:
                    _finalize_import_enrichment_status(
                        job.store,
                        manifest_row,
                        followup_status="enrichment_interrupted",
                        aborted_reason=str(exc) or exc.__class__.__name__,
                    )
            raise ArchiveEnrichmentInterrupted(remaining) from exc
        except Exception as exc:
            async with locked_archive_job(config=config, paths=paths) as job:
                manifest_row = job.store.get_import_manifest(digest)
                if manifest_row is not None:
                    _finalize_import_enrichment_status(
                        job.store,
                        manifest_row,
                        followup_status="enrichment_aborted",
                        aborted_reason=str(exc),
                    )
            raise
        followup_performed = True
        warnings.extend(followup.warnings)
        if debug:
            _record_debug_timing(
                status,
                debug_summaries,
                "archive follow-up",
                followup_started,
                processed=followup.detail_lookups,
                unit="details",
            )

        lock = ProcessLock(paths.lock_file)
        lock.acquire()
        try:
            store = open_archive_store(paths, create=False, config=config)
            assert store is not None
            final_counts = dict(counts)
            final_counts["detail_lookups"] = followup.detail_lookups
            final_counts["detail_terminal_unavailable"] = followup.detail_terminal_unavailable
            final_counts["detail_transient_failures"] = followup.detail_transient_failures
            final_counts["pending_enrichment"] = followup.pending_enrichment
            final_counts.update(store.enrichment_status_counts())
            store.set_import_manifest(
                digest,
                archive_generation_date=generation_date,
                status=final_status,
                import_started_at=(
                    existing_manifest.get("import_started_at")
                    if existing_manifest and not import_performed
                    else import_started_at
                ),
                import_completed_at=utc_now(),
                warnings=warnings,
                counts=final_counts,
                enrichment_followup_status="enrichment_complete",
                enrichment_aborted_reason=None,
            )
            store.close()
        finally:
            lock.release()

        if debug:
            _emit_status(status, "debug summary:")
            for summary in debug_summaries:
                _emit_status(status, f"  {summary}")

        if pipeline is not None:
            pipeline.final_note(
                f"Imported totals: {final_counts.get('authored_tweets', 0):,} authored · "
                f"{final_counts.get('deleted_authored_tweets', 0):,} deleted · "
                f"{final_counts.get('likes', 0):,} likes · "
                f"{final_counts.get('media_files_copied', 0):,} media copied. "
                f"Enrichment queue: {followup.pending_untouched:,} pending untouched · "
                f"{followup.transient_due:,} due · {followup.transient_delayed:,} delayed."
            )

        return ArchiveImportResult(
            skipped=not import_performed,
            followup_performed=followup_performed,
            counts=final_counts,
            warnings=warnings,
            reconciled_collections=followup.reconciled_collections,
            detail_lookups=followup.detail_lookups,
            detail_terminal_unavailable=followup.detail_terminal_unavailable,
            detail_transient_failures=followup.detail_transient_failures,
            pending_enrichment=followup.pending_enrichment,
            pending_untouched=followup.pending_untouched,
            transient_due=followup.transient_due,
            transient_delayed=followup.transient_delayed,
        )
