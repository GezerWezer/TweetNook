"""Official Twitter/X archive import helpers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any

import httpx
from rich.console import Console

from tweetnook.auth import ResolvedAuthBundle
from tweetnook.config import AppConfig, XDGPaths
from tweetnook.core.exceptions import (
    ArchiveOwnerMismatchError,
    ConfigError,
)
from tweetnook.core.locking import ProcessLock
from tweetnook.core.utils import utc_now
from tweetnook.importer.archive_enrich import (
    ArchiveEnrichmentInterrupted,
    _followup_collections_from_counts,
    _run_archive_followup,
)
from tweetnook.importer.archive_input import _archive_identity, _ArchiveInput
from tweetnook.importer.archive_manifests import (
    _finalize_import_enrichment_status,
    _initial_counts,
    _manifest_counts,
    _manifest_generation_date,
    _manifest_warnings,
    _record_archive_capture,
    _remove_archive_owned_files,
)
from tweetnook.importer.archive_media import _copy_exported_media, _slice_for_sample
from tweetnook.importer.archive_progress import (
    _emit_status,
    _progress_callback,
    _record_debug_timing,
    _runner_console,
    _status_printer,
)
from tweetnook.importer.archive_rows import (
    _deleted_headers_map,
    _import_authored_tweets,
    _import_likes,
)
from tweetnook.jobs.context import (
    ArchiveWriteTracker,
    best_effort_interrupt_optimize,
    is_interrupt_exception,
    locked_archive_job,
    resolve_job_context,
)
from tweetnook.jobs.pipeline import current_pipeline
from tweetnook.jobs.resource_failure import raise_if_storage_exhausted
from tweetnook.storage import ArchiveStore, open_archive_store


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


async def import_x_archive(
    archive_path: Path,
    *,
    detail_lookups: int = 0,
    enrich: bool = True,
    offline: bool = False,
    defer_followup: bool = False,
    regen: bool = False,
    sample_limit: int | None = None,
    debug: bool = False,
    config: AppConfig | None = None,
    paths: XDGPaths | None = None,
    auth_bundle: ResolvedAuthBundle | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    console: Console | None = None,
) -> ArchiveImportResult:
    # Setup defers only the live sync; enrichment still runs, with or without an account.
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
        local_only = offline or (defer_followup and not followup_requested)
        followup_performed = False
        import_performed = False
        pending_after_import = 0

        lock = ProcessLock(paths.lock_file)
        lock.acquire()
        try:
            store = open_archive_store(paths, create=True, config=config)
            assert store is not None
            from tweetnook.jobs.reminders import print_archive_migration_report

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

        if local_only and not sampled_import:
            if pipeline is not None:
                pipeline.final_note(
                    "Local archive import complete; enrichment skipped and live sync deferred "
                    "to the separate Setup first-sync task."
                    if defer_followup
                    else "Local archive import complete; live follow-up skipped (offline)."
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
                reconcile_live=not defer_followup,
                config=config,
                paths=paths,
                auth_bundle=auth_bundle,
                transport=transport,
                console=runner_console,
                status=status,
                # Setup leaves rows nobody can enrich yet (no account, and no FxTwitter
                # under replay) pending for the first sync instead of failing the import.
                account_optional=defer_followup,
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
            raise_if_storage_exhausted(exc)
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
                + (
                    " Live sync deferred to the separate Setup first-sync task."
                    if defer_followup
                    else ""
                )
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
