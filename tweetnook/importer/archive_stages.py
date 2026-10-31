"""The stages of one archive import, over the run state they share."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any

from rich.console import Console

from tweetnook.config import XDGPaths
from tweetnook.core.exceptions import ArchiveOwnerMismatchError
from tweetnook.core.utils import utc_now
from tweetnook.importer.archive_input import ArchiveIdentity, ArchiveInput, archive_identity
from tweetnook.importer.archive_manifests import (
    initial_counts,
    manifest_generation_date,
    record_archive_capture,
    remove_archive_owned_files,
)
from tweetnook.importer.archive_media import copy_exported_media, slice_for_sample
from tweetnook.importer.archive_progress import record_debug_timing
from tweetnook.importer.archive_rows import (
    deleted_headers_map,
    import_authored_tweets,
    import_likes,
)
from tweetnook.importer.archive_steps import (
    DELETED,
    INSPECT,
    LIKES,
    MEDIA,
    REGEN,
    SOURCE,
    TWEETS,
    ArchiveStep,
    add_archive_step,
)
from tweetnook.jobs.interactive import emit_status, progress_callback
from tweetnook.jobs.pipeline import NullPipeline, PipelineReporter
from tweetnook.storage import ArchiveStore

_SOURCE_DATASET_KEYS = (
    "tweetHeaders",
    "deletedTweets",
    "deletedTweetHeaders",
    "tweets",
    "like",
)


@dataclass(slots=True)
class ImportRun:
    """What one import knows, from opening the archive to its result."""

    source: ArchiveInput
    paths: XDGPaths
    console: Console
    pipeline: PipelineReporter | NullPipeline
    status: Callable[[str], None] | None
    debug: bool = False
    sample_limit: int | None = None
    digest: str = ""
    generation_date: str | None = None
    identity: ArchiveIdentity | None = None
    account_parts: list[tuple[str, Any]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=initial_counts)
    warnings: list[str] = field(default_factory=list)
    timings: list[str] = field(default_factory=list)
    import_started_at: str = ""
    imported: bool = False
    duplicate: bool = False
    pending_after_import: int = 0

    @property
    def sampled(self) -> bool:
        return self.sample_limit is not None

    def record_timing(
        self, label: str, started: float, *, processed: int | None, unit: str
    ) -> None:
        if self.debug:
            record_debug_timing(
                self.status, self.timings, label, started, processed=processed, unit=unit
            )


@dataclass(slots=True)
class ArchiveDatasets:
    """The loaded datasets, already sliced to the sample limit, and their raw source parts."""

    tweets: list[Any]
    deleted_tweets: list[Any]
    deleted_headers: dict[str, Any]
    likes: list[Any]
    media_directory: str | None
    media_total: int
    raw_parts: list[tuple[str, str, Any]]
    source_part_total: int
    record_total: int


def inspect_archive(run: ImportRun, archive_path: Path) -> None:
    """Hash the archive and read its owner: the digest is what identifies an import."""
    pipeline = run.pipeline
    source = run.source
    emit_status(run.status, "hashing archive contents for idempotence check...")
    hash_started = perf_counter()
    digest_total_bytes = (
        source.digest_total_bytes()
        if (run.debug or run.console.is_terminal or pipeline.active)
        else None
    )
    add_archive_step(
        pipeline,
        INSPECT,
        total=max(digest_total_bytes or 0, 1),
        detail=f"{archive_path.name} · {INSPECT.detail}",
    )
    pipeline.start_step(
        INSPECT.key,
        activity=f"Hashing {archive_path.name} for idempotence",
        counters="verifying every imported dataset and media file",
    )
    with progress_callback(
        run.console,
        label="archive import hash",
        total=digest_total_bytes or 0,
        unit="B",
        leave=run.debug,
    ) as hash_progress:

        def update_hash(done: int, total: int) -> None:
            if hash_progress is not None:
                hash_progress(done, total)
            pipeline.update_step(
                INSPECT.key,
                completed=done,
                total=max(total, 1),
                counters=f"{done:,}/{total:,} bytes hashed",
            )

        run.digest = source.digest(
            progress=update_hash if pipeline.active else hash_progress,
            item_status=(
                lambda relative_path: pipeline.status(INSPECT.key, f"Hashing {relative_path}")
                if pipeline.active
                else None
            ),
            total_bytes=digest_total_bytes,
        )
    run.record_timing("archive hash", hash_started, processed=digest_total_bytes, unit="bytes")
    run.generation_date = manifest_generation_date(source.manifest)
    if run.sampled:
        run.warnings.append(
            f"sampled import: limiting authored tweets, deleted tweets, likes, and "
            f"media files to {run.sample_limit} items each after full dataset load"
        )
        run.warnings.append(
            "sampled import still hashes and parses the full archive files before slicing "
            "imported rows"
        )
    emit_status(run.status, "loading archive account metadata...")
    account_started = perf_counter()
    account_items, run.account_parts = source.load_dataset("account")
    run.record_timing(
        "archive account metadata load",
        account_started,
        processed=len(run.account_parts),
        unit="parts",
    )
    run.identity = archive_identity(source.manifest, account_items)
    owner = f"@{run.identity.username}" if run.identity.username else "archive owner resolved"
    pipeline.complete_step(INSPECT.key, f"archive hash verified · {owner}")


def claim_archive_owner(run: ImportRun, store: ArchiveStore) -> None:
    existing_owner = store.get_archive_owner_id()
    if run.identity.account_id is None and existing_owner is not None:
        raise ArchiveOwnerMismatchError(
            "Archive owner could not be determined, but the local archive already "
            f"belongs to Twitter/X user {existing_owner}."
        )
    store.ensure_archive_owner_id(run.identity.account_id)


def clear_previous_import(run: ImportRun, store: ArchiveStore) -> None:
    pipeline = run.pipeline
    add_archive_step(pipeline, REGEN)
    pipeline.start_step(REGEN.key, activity="Clearing the previous import owned by this archive")
    emit_status(run.status, "clearing previously imported archive-owned rows...")
    regen_started = perf_counter()
    archive_media_paths = store.list_archive_import_media_paths()
    cleared_rows = store.clear_archive_import_data()
    cleared_files = remove_archive_owned_files(run.paths.data_dir, archive_media_paths)
    emit_status(
        run.status,
        f"regen cleared {sum(cleared_rows.values())} rows and {cleared_files} "
        "managed archive media files",
    )
    pipeline.complete_step(
        REGEN.key,
        f"{sum(cleared_rows.values()):,} rows removed · "
        f"{cleared_files:,} managed media files removed",
    )
    run.record_timing(
        "archive-only regen cleanup",
        regen_started,
        processed=sum(cleared_rows.values()) + cleared_files,
        unit="objects",
    )


def _record_import_start(run: ImportRun, store: ArchiveStore) -> None:
    store.set_import_manifest(
        run.digest,
        archive_generation_date=run.generation_date,
        status="in_progress",
        import_started_at=run.import_started_at,
        warnings=run.warnings,
        counts=run.counts,
    )
    record_archive_capture(
        store,
        "XArchiveManifest",
        "data/manifest.js",
        run.source.manifest,
        archive_digest=run.digest,
    )
    for filename, payload in run.account_parts:
        record_archive_capture(
            store,
            "XArchiveAccount",
            filename,
            payload,
            archive_digest=run.digest,
        )
    if "bookmark" not in {key.lower() for key in (run.source.manifest.get("dataTypes") or {})}:
        run.warnings.append(
            "archive does not contain a bookmark dataset "
            "(expected for current official Twitter/X archives)"
        )


def _source_dataset_loader(run: ImportRun, source_part_total: int) -> Callable[[str], Any]:
    """A dataset loader that reports parsing as one step across every dataset."""
    pipeline = run.pipeline
    source = run.source
    reporting = pipeline.active and source_part_total > 0
    parts_loaded = 0

    def load(key: str) -> Any:
        nonlocal parts_loaded
        part_files = source.dataset_files(key)
        offset = parts_loaded

        def update_source_part(done: int, _total: int) -> None:
            pipeline.update_step(
                SOURCE.key,
                completed=offset + done,
                counters=f"{offset + done}/{source_part_total} parts parsed · 0 preserved",
            )

        loaded = source.load_dataset(
            key,
            progress=update_source_part if reporting else None,
            item_status=(
                (lambda filename: pipeline.status(SOURCE.key, f"Parsing {filename}"))
                if reporting
                else None
            ),
        )
        parts_loaded += len(part_files)
        return loaded

    return load


def load_datasets(run: ImportRun, store: ArchiveStore) -> ArchiveDatasets:
    pipeline = run.pipeline
    source = run.source
    emit_status(run.status, "loading archive datasets...")
    dataset_started = perf_counter()
    source_part_total = sum(len(source.dataset_files(key)) for key in _SOURCE_DATASET_KEYS)
    if pipeline.active and source_part_total:
        first_source_file = next(
            filename for key in _SOURCE_DATASET_KEYS for filename in source.dataset_files(key)
        )
        add_archive_step(pipeline, SOURCE, total=source_part_total * 2)
        pipeline.start_step(
            SOURCE.key,
            activity=f"Parsing {first_source_file}",
            counters=f"0/{source_part_total} parts parsed · 0 preserved",
        )
    _record_import_start(run, store)

    load = _source_dataset_loader(run, source_part_total)
    _, tweet_header_parts = load("tweetHeaders")
    deleted_tweets, deleted_tweet_parts = load("deletedTweets")
    deleted_headers_items, deleted_header_parts = load("deletedTweetHeaders")
    tweets, tweet_parts = load("tweets")
    likes, like_parts = load("like")
    run.record_timing(
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
    tweets = slice_for_sample(tweets, limit=run.sample_limit)
    deleted_tweets = slice_for_sample(deleted_tweets, limit=run.sample_limit)
    likes = slice_for_sample(likes, limit=run.sample_limit)
    emit_status(
        run.status,
        (
            f"loaded {total_tweets} tweets, "
            f"{total_deleted_tweets} deleted tweets, "
            f"{total_likes} likes"
            if not run.sampled
            else (
                f"loaded {total_tweets} tweets -> sampling {len(tweets)}, "
                f"{total_deleted_tweets} deleted tweets -> sampling "
                f"{len(deleted_tweets)}, {total_likes} likes -> sampling "
                f"{len(likes)}"
            )
        ),
    )

    tweets_info = ((source.manifest.get("dataTypes") or {}).get("tweets")) or {}
    media_directory = tweets_info.get("mediaDirectory") if isinstance(tweets_info, dict) else None
    media_total = 0
    if isinstance(media_directory, str):
        media_total = len(
            slice_for_sample(source.iter_files(media_directory), limit=run.sample_limit)
        )
    return ArchiveDatasets(
        tweets=tweets,
        deleted_tweets=deleted_tweets,
        deleted_headers=deleted_headers_map(deleted_headers_items),
        likes=likes,
        media_directory=media_directory,
        media_total=media_total,
        raw_parts=[
            *(("XArchiveTweetHeaders", name, payload) for name, payload in tweet_header_parts),
            *(
                ("XArchiveDeletedTweetHeaders", name, payload)
                for name, payload in deleted_header_parts
            ),
            *(("XArchiveTweets", name, payload) for name, payload in tweet_parts),
            *(("XArchiveDeletedTweets", name, payload) for name, payload in deleted_tweet_parts),
            *(("XArchiveLikes", name, payload) for name, payload in like_parts),
        ],
        source_part_total=source_part_total,
        record_total=total_tweets + total_deleted_tweets + total_likes,
    )


def preserve_raw_sources(run: ImportRun, store: ArchiveStore, data: ArchiveDatasets) -> None:
    pipeline = run.pipeline
    part_total = data.source_part_total
    capture_started = perf_counter()
    for index, (operation, filename, payload) in enumerate(data.raw_parts, start=1):
        if part_total:
            pipeline.update_step(
                SOURCE.key,
                completed=part_total + index - 1,
                activity=f"Preserving {filename}",
                counters=(
                    f"{part_total}/{part_total} parts parsed · {index - 1}/{part_total} preserved"
                ),
            )
        record_archive_capture(store, operation, filename, payload, archive_digest=run.digest)
        if part_total:
            pipeline.update_step(
                SOURCE.key,
                completed=part_total + index,
                counters=f"{part_total}/{part_total} parts parsed · {index}/{part_total} preserved",
            )
    if part_total:
        pipeline.complete_step(
            SOURCE.key,
            f"{part_total:,} parts parsed and preserved · "
            f"{data.record_total:,} tweet/like records loaded",
        )
    run.record_timing(
        "archive raw-capture persist",
        capture_started,
        processed=2 + len(data.raw_parts),
        unit="rows",
    )


def _write_authored(
    run: ImportRun, store: ArchiveStore, data: ArchiveDatasets, **callbacks: Any
) -> None:
    import_authored_tweets(
        store,
        data.tweets,
        run.identity,
        deleted_headers=data.deleted_headers,
        counts=run.counts,
        force_deleted=False,
        **callbacks,
    )


def _write_deleted(
    run: ImportRun, store: ArchiveStore, data: ArchiveDatasets, **callbacks: Any
) -> None:
    import_authored_tweets(
        store,
        data.deleted_tweets,
        run.identity,
        deleted_headers=data.deleted_headers,
        counts=run.counts,
        force_deleted=True,
        **callbacks,
    )


def _write_likes(
    run: ImportRun, store: ArchiveStore, data: ArchiveDatasets, **callbacks: Any
) -> None:
    import_likes(store, data.likes, counts=run.counts, **callbacks)


def _copy_media(
    run: ImportRun, store: ArchiveStore, data: ArchiveDatasets, **callbacks: Any
) -> None:
    copy_exported_media(
        run.source,
        store,
        run.paths,
        data.media_directory,
        counts=run.counts,
        warnings=run.warnings,
        limit=run.sample_limit,
        **callbacks,
    )


def _imported_counters(done: int, _written: int) -> str:
    return f"{done} imported"


@dataclass(frozen=True, slots=True)
class _WriteStage:
    """One bulk write and how its pipeline step, progress bar and debug timing describe it."""

    step: ArchiveStep
    total: Callable[[ArchiveDatasets], int]
    write: Callable[..., None]
    count_keys: tuple[str, ...]
    announce: str
    preparing: str
    progress_label: str
    item_activity: str
    summary: Callable[[int, int], str]
    timing_label: str
    counters: Callable[[int, int], str] = _imported_counters


_WRITE_STAGES = (
    _WriteStage(
        step=TWEETS,
        total=lambda data: len(data.tweets),
        write=_write_authored,
        # A tweet in the main dataset can be marked deleted by its header.
        count_keys=("authored_tweets", "deleted_authored_tweets"),
        announce="importing {total} authored tweets...",
        preparing="Preparing authored tweet import",
        progress_label="archive import authored",
        item_activity="Importing authored tweet",
        summary=lambda _total, written: f"{written:,} authored rows imported",
        timing_label="authored tweet import",
    ),
    _WriteStage(
        step=DELETED,
        total=lambda data: len(data.deleted_tweets),
        write=_write_deleted,
        count_keys=("deleted_authored_tweets",),
        announce="importing {total} deleted authored tweets...",
        preparing="Preparing deleted authored tweet import",
        progress_label="archive import deleted",
        item_activity="Importing deleted tweet",
        summary=lambda _total, written: f"{written:,} deleted rows imported",
        timing_label="deleted authored tweet import",
    ),
    _WriteStage(
        step=LIKES,
        total=lambda data: len(data.likes),
        write=_write_likes,
        count_keys=("likes",),
        announce="importing {total} likes...",
        preparing="Preparing liked tweet import",
        progress_label="archive import likes",
        item_activity="Importing liked tweet",
        summary=lambda _total, written: f"{written:,} likes imported",
        timing_label="like import",
    ),
)

_MEDIA_STAGE = _WriteStage(
    step=MEDIA,
    total=lambda data: data.media_total,
    write=_copy_media,
    count_keys=("media_files_copied",),
    announce="copying exported media files...",
    preparing="Preparing exported media copy",
    progress_label="archive import media",
    item_activity="Copying",
    summary=lambda total, written: f"{total:,} scanned · {written:,} copied",
    timing_label="archive media copy",
    counters=lambda done, written: f"{done} scanned · {written} copied",
)


def _run_write_stage(
    run: ImportRun, store: ArchiveStore, data: ArchiveDatasets, stage: _WriteStage
) -> None:
    pipeline = run.pipeline
    total = stage.total(data)
    start_count = sum(run.counts[key] for key in stage.count_keys)

    def written() -> int:
        return sum(run.counts[key] for key in stage.count_keys) - start_count

    def item_status(item: str, done: int, item_total: int) -> None:
        pipeline.update_step(
            stage.step.key,
            completed=done,
            total=item_total,
            activity=f"{stage.item_activity} {item}",
            counters=stage.counters(done, written()),
        )

    emit_status(run.status, stage.announce.format(total=total))
    started = perf_counter()
    if total:
        pipeline.start_step(
            stage.step.key,
            activity=stage.preparing,
            counters=stage.counters(0, 0),
        )
    with progress_callback(
        run.console,
        label=stage.progress_label,
        total=total,
        unit=stage.step.unit,
        leave=run.debug,
    ) as progress:
        stage.write(run, store, data, progress=progress, item_status=item_status)
    if total:
        pipeline.complete_step(stage.step.key, stage.summary(total, written()))
    run.record_timing(stage.timing_label, started, processed=total, unit=stage.step.unit)


def declare_write_steps(run: ImportRun, data: ArchiveDatasets) -> None:
    """Size each write step now that the datasets are loaded, and skip what has nothing to do."""
    pipeline = run.pipeline
    if not data.source_part_total:
        pipeline.skip_step(SOURCE.key, "the archive containing no tweet or like datasets")
    empty_reasons = {
        TWEETS.key: "the authored-tweet dataset being empty",
        DELETED.key: "the deleted-tweet dataset being empty",
        LIKES.key: "the likes dataset being empty",
        MEDIA.key: "the archive containing no exported media",
    }
    stages = (*_WRITE_STAGES, _MEDIA_STAGE)
    for stage in stages:
        if total := stage.total(data):
            add_archive_step(pipeline, stage.step, total=total)
    for stage in stages:
        if not stage.total(data):
            pipeline.skip_step(stage.step.key, empty_reasons[stage.step.key])


def ingest_archive(run: ImportRun, store: ArchiveStore, *, final_status: str) -> None:
    """Write everything the archive holds, leaving a manifest with the import's final status."""
    run.imported = True
    data = load_datasets(run, store)
    declare_write_steps(run, data)
    preserve_raw_sources(run, store, data)
    for stage in (*_WRITE_STAGES, _MEDIA_STAGE):
        _run_write_stage(run, store, data, stage)
    run.pending_after_import = store.count_tweet_objects_for_enrichment()
    store.set_import_manifest(
        run.digest,
        archive_generation_date=run.generation_date,
        status=final_status,
        import_started_at=run.import_started_at,
        import_completed_at=utc_now(),
        warnings=run.warnings,
        counts=run.counts,
    )
