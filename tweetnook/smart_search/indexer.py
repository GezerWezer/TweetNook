"""Batch embedding indexer for downloaded archive media."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tweetnook.config import AppConfig, XDGPaths
from tweetnook.jobs.pipeline import NullPipeline, PipelineReporter
from tweetnook.jobs.steps import SMART_SEARCH
from tweetnook.storage.backend import ArchiveStore

from .encoder import ClipOnnxEncoder


@dataclass(frozen=True, slots=True)
class SmartSearchIndexResult:
    model: str
    indexed: int
    skipped: int
    total_pending: int


def index_media_embeddings(
    store: ArchiveStore,
    paths: XDGPaths,
    config: AppConfig,
    *,
    limit: int | None = None,
    pipeline: PipelineReporter | NullPipeline | None = None,
    encoder: ClipOnnxEncoder | None = None,
) -> SmartSearchIndexResult:
    """Compute and store CLIP/SigLIP embeddings for pending local archive media."""
    reporter = pipeline if pipeline is not None else NullPipeline()
    cfg = config.smart_search
    pending = store.pending_embedding_media(
        cfg.model,
        limit=limit,
        include_video_thumbnails=cfg.include_video_thumbnails,
        include_quoted_media=cfg.include_quoted_media,
        include_thread_media=cfg.include_thread_media,
    )
    total_pending = len(pending)
    SMART_SEARCH.declare(
        reporter,
        total=max(total_pending, 1),
        detail=f"model={cfg.model}",
    )
    reporter.start_step(SMART_SEARCH.key, activity=f"Indexing images ({cfg.model})")
    if total_pending == 0:
        reporter.complete_step(SMART_SEARCH.key, "All downloaded media already indexed.")
        return SmartSearchIndexResult(model=cfg.model, indexed=0, skipped=0, total_pending=0)

    active_encoder = encoder or ClipOnnxEncoder.from_cache(cfg.model, paths.models_cache_dir)
    data_root = paths.data_dir
    indexed = 0
    skipped = 0
    batch_size = max(1, cfg.batch_size)

    for offset in range(0, total_pending, batch_size):
        chunk = pending[offset : offset + batch_size]
        valid_rows: list[dict[str, Any]] = []
        valid_paths: list[Path] = []
        for row in chunk:
            rel_path = Path(row["file_path"])
            full_path = rel_path if rel_path.is_absolute() else (data_root / rel_path)
            if full_path.is_file():
                valid_rows.append(row)
                valid_paths.append(full_path)
            else:
                skipped += 1

        if valid_paths:
            vectors = _encode_with_fallback(active_encoder, valid_paths)
            now = datetime.now(UTC).isoformat()
            upsert_rows = []
            for row, vec in zip(valid_rows, vectors, strict=True):
                if vec is None:
                    skipped += 1
                    continue
                upsert_rows.append(
                    (
                        row["media_row_key"],
                        row["tweet_id"],
                        cfg.model,
                        active_encoder.spec.dims,
                        vec.tobytes(),
                        now,
                    )
                )
            if upsert_rows:
                indexed += store.upsert_media_embeddings(upsert_rows)

        reporter.update_step(
            SMART_SEARCH.key,
            completed=offset + len(chunk),
            counters=f"{indexed} indexed · {skipped} skipped",
        )

    reporter.complete_step(
        SMART_SEARCH.key,
        f"Indexed {indexed} images ({skipped} skipped) with {cfg.model}.",
    )
    return SmartSearchIndexResult(
        model=cfg.model,
        indexed=indexed,
        skipped=skipped,
        total_pending=total_pending,
    )


def _encode_with_fallback(encoder: ClipOnnxEncoder, paths: list[Path]) -> list[Any]:
    """Encode a batch, falling back to per-file encoding if one corrupt image fails."""
    try:
        batch_vecs = encoder.encode_images(paths)
        return [batch_vecs[i] for i in range(len(paths))]
    except Exception:
        results: list[Any] = []
        for path in paths:
            try:
                results.append(encoder.encode_images([path])[0])
            except Exception:
                results.append(None)
        return results
