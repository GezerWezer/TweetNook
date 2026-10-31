"""Official Twitter/X archive import helpers."""

from __future__ import annotations

import logging
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
from tweetnook.core.utils import utc_now
from tweetnook.importer.archive_enrich import run_import_followup
from tweetnook.importer.archive_input import ArchiveInput, archive_identity
from tweetnook.importer.archive_manifests import (
    initial_counts,
    manifest_counts,
    manifest_generation_date,
    manifest_warnings,
    record_archive_capture,
    remove_archive_owned_files,
)
from tweetnook.importer.archive_media import copy_exported_media, slice_for_sample
from tweetnook.importer.archive_progress import (
    archive_status_printer,
    record_debug_timing,
    runner_console_for,
)
from tweetnook.importer.archive_rows import (
    deleted_headers_map,
    import_authored_tweets,
    import_likes,
)
from tweetnook.jobs.context import (
    locked_archive_job,
    resolve_job_context,
)
from tweetnook.jobs.interactive import emit_status, progress_callback
from tweetnook.jobs.pipeline import current_pipeline
from tweetnook.storage import ArchiveStore
from tweetnook.sync.steps import head_step_key, preflight_step_key

logger = logging.getLogger(__name__)


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


def _record_failed_import(
    store: ArchiveStore,
    digest: str,
    *,
    archive_generation_date: str | None,
    import_started_at: str,
    warnings: list[str],
    counts: dict[str, int],
) -> None:
    """Mark the manifest failed without letting this write replace the import's own error.

    A full disk is the likely reason an import fails, and then this write fails too.
    """
    try:
        store.set_import_manifest(
            digest,
            archive_generation_date=archive_generation_date,
            status="failed",
            import_started_at=import_started_at,
            import_completed_at=utc_now(),
            warnings=warnings,
            counts=counts,
        )
    except Exception:
        logger.exception("Could not record the failed archive import")


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
    status = archive_status_printer(console, "archive import", force=debug)
    runner_console = runner_console_for(console, force=debug)
    if sample_limit is not None and sample_limit <= 0:
        raise ConfigError("--sample-limit must be greater than zero.")
    emit_status(status, f"opening {archive_path}")
    debug_summaries: list[str] = []
    with ArchiveInput(archive_path) as source:
        emit_status(status, "hashing archive contents for idempotence check...")
        hash_started = perf_counter()
        digest_total_bytes = (
            source.digest_total_bytes()
            if (debug or console.is_terminal or pipeline.active)
            else None
        )
        inspect_step_key = "archive-inspect"
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
        with progress_callback(
            console,
            label="archive import hash",
            total=digest_total_bytes or 0,
            unit="B",
            leave=debug,
        ) as hash_progress:

            def update_hash(done: int, total: int) -> None:
                if hash_progress is not None:
                    hash_progress(done, total)
                pipeline.update_step(
                    inspect_step_key,
                    completed=done,
                    total=max(total, 1),
                    counters=f"{done:,}/{total:,} bytes hashed",
                )

            digest = source.digest(
                progress=update_hash if pipeline.active else hash_progress,
                item_status=(
                    lambda relative_path: pipeline.status(
                        inspect_step_key, f"Hashing {relative_path}"
                    )
                    if pipeline.active
                    else None
                ),
                total_bytes=digest_total_bytes,
            )
        if debug:
            record_debug_timing(
                status,
                debug_summaries,
                "archive hash",
                hash_started,
                processed=digest_total_bytes,
                unit="bytes",
            )
        generation_date = manifest_generation_date(source.manifest)
        counts = initial_counts()
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
        emit_status(status, "loading archive account metadata...")
        account_started = perf_counter()
        account_items, account_parts = source.load_dataset("account")
        if debug:
            record_debug_timing(
                status,
                debug_summaries,
                "archive account metadata load",
                account_started,
                processed=len(account_parts),
                unit="parts",
            )
        identity = archive_identity(source.manifest, account_items)
        owner = f"@{identity.username}" if identity.username else "archive owner resolved"
        pipeline.complete_step(
            inspect_step_key,
            f"archive hash verified · {owner}",
        )
        existing_manifest: dict[str, Any] | None = None
        import_started_at = utc_now()
        followup_requested = not offline and (enrich or detail_lookups > 0)
        local_only = offline or (defer_followup and not followup_requested)
        followup_performed = False
        import_performed = False
        pending_after_import = 0

        async with locked_archive_job(
            config=config, paths=paths, console=console, create=True
        ) as job:
            store = job.store
            try:
                existing_owner = store.get_archive_owner_id()
                if identity.account_id is None and existing_owner is not None:
                    raise ArchiveOwnerMismatchError(
                        "Archive owner could not be determined, but the local archive already "
                        f"belongs to Twitter/X user {existing_owner}."
                    )
                store.ensure_archive_owner_id(identity.account_id)
                if regen:
                    regen_step_key = "archive-regen"
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
                    emit_status(status, "clearing previously imported archive-owned rows...")
                    regen_started = perf_counter()
                    archive_media_paths = store.list_archive_import_media_paths()
                    cleared_rows = store.clear_archive_import_data()
                    cleared_files = remove_archive_owned_files(paths.data_dir, archive_media_paths)
                    existing_manifest = None
                    emit_status(
                        status,
                        f"regen cleared {sum(cleared_rows.values())} rows and {cleared_files} "
                        "managed archive media files",
                    )
                    pipeline.complete_step(
                        regen_step_key,
                        f"{sum(cleared_rows.values()):,} rows removed · "
                        f"{cleared_files:,} managed media files removed",
                    )
                    if debug:
                        record_debug_timing(
                            status,
                            debug_summaries,
                            "archive-only regen cleanup",
                            regen_started,
                            processed=sum(cleared_rows.values()) + cleared_files,
                            unit="objects",
                        )
                existing_manifest = store.get_import_manifest(digest)
                if existing_manifest and existing_manifest.get("status") == "completed":
                    counts = manifest_counts(existing_manifest)
                    warnings = manifest_warnings(existing_manifest)
                    for step_key in (
                        "archive-source",
                        "archive-tweets",
                        "archive-deleted",
                        "archive-likes",
                        "archive-media",
                    ):
                        if pipeline.has_step(step_key):
                            pipeline.skip_step(step_key, "this archive already being imported")
                    if not followup_requested:
                        for step_key in (
                            "archive-auth",
                            preflight_step_key(["tweets"]),
                            head_step_key("tweets"),
                            preflight_step_key(["likes"]),
                            head_step_key("likes"),
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
                    emit_status(status, "loading archive datasets...")
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
                    if pipeline.active and source_part_total:
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
                                if pipeline.active and source_part_total
                                else None
                            ),
                            item_status=(
                                (
                                    lambda filename: pipeline.status(
                                        source_step_key, f"Parsing {filename}"
                                    )
                                )
                                if pipeline.active and source_part_total
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
                    record_archive_capture(
                        store,
                        "XArchiveManifest",
                        "data/manifest.js",
                        source.manifest,
                        archive_digest=digest,
                    )
                    for filename, payload in account_parts:
                        record_archive_capture(
                            store,
                            "XArchiveAccount",
                            filename,
                            payload,
                            archive_digest=digest,
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
                        record_debug_timing(
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
                    tweets = slice_for_sample(tweets, limit=sample_limit)
                    deleted_tweets = slice_for_sample(deleted_tweets, limit=sample_limit)
                    likes = slice_for_sample(likes, limit=sample_limit)
                    emit_status(
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
                            slice_for_sample(source.iter_files(media_directory), limit=sample_limit)
                        )
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
                                "authored archive rows · normalized objects, relations, "
                                "and raw source"
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
                        (
                            media_total,
                            "archive-media",
                            "the archive containing no exported media",
                        ),
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
                        *(
                            ("XArchiveTweets", filename, payload)
                            for filename, payload in tweet_parts
                        ),
                        *(
                            ("XArchiveDeletedTweets", filename, payload)
                            for filename, payload in deleted_tweet_parts
                        ),
                        *(("XArchiveLikes", filename, payload) for filename, payload in like_parts),
                    ]
                    for index, (operation, filename, payload) in enumerate(
                        raw_source_parts, start=1
                    ):
                        if source_part_total:
                            pipeline.update_step(
                                source_step_key,
                                completed=source_part_total + index - 1,
                                activity=f"Preserving {filename}",
                                counters=(
                                    f"{source_part_total}/{source_part_total} parts parsed · "
                                    f"{index - 1}/{source_part_total} preserved"
                                ),
                            )
                        record_archive_capture(
                            store,
                            operation,
                            filename,
                            payload,
                            archive_digest=digest,
                        )
                        if source_part_total:
                            pipeline.update_step(
                                source_step_key,
                                completed=source_part_total + index,
                                counters=(
                                    f"{source_part_total}/{source_part_total} parts parsed · "
                                    f"{index}/{source_part_total} preserved"
                                ),
                            )
                    if source_part_total:
                        record_total = total_tweets + total_deleted_tweets + total_likes
                        pipeline.complete_step(
                            source_step_key,
                            f"{source_part_total:,} parts parsed and preserved · "
                            f"{record_total:,} tweet/like records loaded",
                        )
                    if debug:
                        record_debug_timing(
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

                    deleted_headers = deleted_headers_map(deleted_headers_items)
                    emit_status(status, f"importing {len(tweets)} authored tweets...")
                    authored_started = perf_counter()
                    authored_step_key = "archive-tweets"
                    authored_start_count = (
                        counts["authored_tweets"] + counts["deleted_authored_tweets"]
                    )
                    if tweets:
                        pipeline.add_step(
                            authored_step_key,
                            "Tweets",
                            total=len(tweets),
                            unit="tweets",
                            detail=(
                                "authored archive rows · normalized objects, relations, "
                                "and raw source"
                            ),
                            rate_unit="tweets/s",
                        )
                        pipeline.start_step(
                            authored_step_key,
                            activity="Preparing authored tweet import",
                            counters="0 imported",
                        )
                    with progress_callback(
                        console,
                        label="archive import authored",
                        total=len(tweets),
                        unit="tweets",
                        leave=debug,
                    ) as authored_progress:
                        import_authored_tweets(
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
                                if pipeline.active
                                else None
                            ),
                        )
                    if tweets:
                        imported = (
                            counts["authored_tweets"]
                            + counts["deleted_authored_tweets"]
                            - authored_start_count
                        )
                        pipeline.complete_step(
                            authored_step_key, f"{imported:,} authored rows imported"
                        )
                    if debug:
                        record_debug_timing(
                            status,
                            debug_summaries,
                            "authored tweet import",
                            authored_started,
                            processed=len(tweets),
                            unit="tweets",
                        )
                    emit_status(
                        status, f"importing {len(deleted_tweets)} deleted authored tweets..."
                    )
                    deleted_started = perf_counter()
                    deleted_step_key = "archive-deleted"
                    deleted_start_count = counts["deleted_authored_tweets"]
                    if deleted_tweets:
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
                    with progress_callback(
                        console,
                        label="archive import deleted",
                        total=len(deleted_tweets),
                        unit="tweets",
                        leave=debug,
                    ) as deleted_progress:
                        import_authored_tweets(
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
                                if pipeline.active
                                else None
                            ),
                        )
                    if deleted_tweets:
                        imported = counts["deleted_authored_tweets"] - deleted_start_count
                        pipeline.complete_step(
                            deleted_step_key, f"{imported:,} deleted rows imported"
                        )
                    if debug:
                        record_debug_timing(
                            status,
                            debug_summaries,
                            "deleted authored tweet import",
                            deleted_started,
                            processed=len(deleted_tweets),
                            unit="tweets",
                        )
                    emit_status(status, f"importing {len(likes)} likes...")
                    likes_started = perf_counter()
                    likes_step_key = "archive-likes"
                    likes_start_count = counts["likes"]
                    if likes:
                        pipeline.add_step(
                            likes_step_key,
                            "Likes",
                            total=len(likes),
                            unit="likes",
                            detail=(
                                "archive like memberships · sparse objects queued for "
                                "live enrichment"
                            ),
                            rate_unit="likes/s",
                        )
                        pipeline.start_step(
                            likes_step_key,
                            activity="Preparing liked tweet import",
                            counters="0 imported",
                        )
                    with progress_callback(
                        console,
                        label="archive import likes",
                        total=len(likes),
                        unit="likes",
                        leave=debug,
                    ) as likes_progress:
                        import_likes(
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
                                if pipeline.active
                                else None
                            ),
                        )
                    if likes:
                        imported = counts["likes"] - likes_start_count
                        pipeline.complete_step(likes_step_key, f"{imported:,} likes imported")
                    if debug:
                        record_debug_timing(
                            status,
                            debug_summaries,
                            "like import",
                            likes_started,
                            processed=len(likes),
                            unit="likes",
                        )
                    emit_status(status, "copying exported media files...")
                    media_started = perf_counter()
                    media_step_key = "archive-media"
                    media_start_count = counts["media_files_copied"]
                    if media_total:
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
                    with progress_callback(
                        console,
                        label="archive import media",
                        total=media_total,
                        unit="files",
                        leave=debug,
                    ) as media_progress:
                        copy_exported_media(
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
                                if pipeline.active
                                else None
                            ),
                            limit=sample_limit,
                        )
                    if media_total:
                        copied = counts["media_files_copied"] - media_start_count
                        pipeline.complete_step(
                            media_step_key,
                            f"{media_total:,} scanned · {copied:,} copied",
                        )
                        for warning in warnings[warning_count_before_media:]:
                            pipeline.issue(warning, dedupe_key="archive-media:unmatched")
                    if debug:
                        record_debug_timing(
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
            except BaseException:
                if import_performed:
                    _record_failed_import(
                        store,
                        digest,
                        archive_generation_date=generation_date,
                        import_started_at=import_started_at,
                        warnings=warnings,
                        counts=counts,
                    )
                raise

        if local_only and not sampled_import:
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
                emit_status(status, "debug summary:")
                for summary in debug_summaries:
                    emit_status(status, f"  {summary}")
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
        emit_status(status, "running follow-up reconciliation and enrichment...")
        followup_started = perf_counter()
        followup = await run_import_followup(
            counts=counts,
            manifest_digest=digest,
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
        followup_performed = True
        warnings.extend(followup.warnings)
        if debug:
            record_debug_timing(
                status,
                debug_summaries,
                "archive follow-up",
                followup_started,
                processed=followup.detail_lookups,
                unit="details",
            )

        async with locked_archive_job(config=config, paths=paths, console=console) as job:
            store = job.store
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

        if debug:
            emit_status(status, "debug summary:")
            for summary in debug_summaries:
                emit_status(status, f"  {summary}")

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
