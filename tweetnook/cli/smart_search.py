"""CLI commands for local CLIP/SigLIP Smart Search indexing and querying."""

from __future__ import annotations

from typing import Annotated

import typer

from tweetnook.cli.common import configure_logging, web_pipeline
from tweetnook.config import load_config
from tweetnook.core.exceptions import TweetNookError
from tweetnook.jobs.catalog import JOBS_BY_KEY
from tweetnook.jobs.context import archive_job

smart_search_app = typer.Typer(
    no_args_is_help=True,
    help="Index and search archived images with local CLIP/SigLIP embeddings.",
)


@smart_search_app.command("index")
def smart_search_index(
    limit: Annotated[
        int | None,
        typer.Option("--limit", min=1, help="Maximum number of unindexed images to process."),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Override the Smart Search model configured in config.toml."),
    ] = None,
) -> None:
    """Compute CLIP/SigLIP embeddings for downloaded archive images."""
    console = configure_logging()
    try:
        config, paths = load_config()
        if not config.smart_search.enabled:
            raise RuntimeError(
                "Smart Search is disabled. Enable it in Settings > Smart search or config.toml."
            )
        if model is not None:
            config.smart_search.model = model
        from tweetnook.smart_search.indexer import index_media_embeddings
        from tweetnook.smart_search.sdk import SmartSearchUnavailable, smart_search_installed

        if not smart_search_installed():
            raise SmartSearchUnavailable(
                "Smart Search is not installed. Install 'tweetnook[smart-search]' to enable it."
            )

        with web_pipeline(console, JOBS_BY_KEY["smart-search"].title, paths) as pipeline:
            console.print(f"Indexing images with {config.smart_search.model}…")
            with archive_job(config=config, paths=paths, console=console) as job:
                result = index_media_embeddings(
                    job.store,
                    paths,
                    config,
                    limit=limit,
                    pipeline=pipeline,
                )
                summary = (
                    f"Indexed {result.indexed} image(s) "
                    f"({result.skipped} skipped) with {result.model}."
                )
                console.print(f"[green]{summary}[/green]")
                pipeline.final_note(summary)
    except (TweetNookError, RuntimeError, ValueError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


@smart_search_app.command("status")
def smart_search_status() -> None:
    """Show Smart Search installation, model, and index coverage."""
    console = configure_logging()
    config, paths = load_config()
    from tweetnook.smart_search.sdk import smart_search_installed
    from tweetnook.storage.backend import ArchiveStore

    installed = smart_search_installed()
    console.print(f"Installed: {'yes' if installed else 'no (install tweetnook[smart-search])'}")
    console.print(f"Enabled: {'yes' if config.smart_search.enabled else 'no'}")
    console.print(f"Model: {config.smart_search.model}")
    db_path = paths.data_dir / "archive.db"
    if not db_path.exists():
        console.print("Archive: not initialized")
        return
    store = ArchiveStore(db_path, create=False, config=config, read_only=True)
    try:
        stats = store.embedding_stats(
            config.smart_search.model,
            include_video_thumbnails=config.smart_search.include_video_thumbnails,
            include_quoted_media=config.smart_search.include_quoted_media,
            include_thread_media=config.smart_search.include_thread_media,
        )
        console.print(
            f"Embeddings: {stats['indexed']} indexed, {stats['pending']} pending "
            f"({stats['total']} total eligible)"
        )
    finally:
        store.close()


@smart_search_app.command("search")
def smart_search_query(
    query: Annotated[str, typer.Argument(help="Natural-language description of the image.")],
    limit: Annotated[
        int,
        typer.Option("--limit", min=1, max=200, help="Maximum number of matching tweets."),
    ] = 20,
    min_score: Annotated[
        float | None,
        typer.Option("--min-score", min=-1.0, max=1.0, help="Minimum cosine similarity score."),
    ] = None,
) -> None:
    """Search archived images using a natural-language description."""
    console = configure_logging()
    try:
        config, paths = load_config()
        if not config.smart_search.enabled:
            raise RuntimeError(
                "Smart Search is disabled. Enable it in Settings > Smart search or config.toml."
            )
        from tweetnook.smart_search.encoder import ClipOnnxEncoder
        from tweetnook.storage.backend import ArchiveStore

        db_path = paths.data_dir / "archive.db"
        if not db_path.exists():
            console.print("Archive is not initialized.")
            raise typer.Exit(code=1)

        encoder = ClipOnnxEncoder.from_cache(config.smart_search.model, paths.models_cache_dir)
        query_vec = encoder.encode_text(query).tobytes()
        threshold = min_score if min_score is not None else config.smart_search.min_score

        store = ArchiveStore(db_path, create=False, config=config, read_only=True)
        try:
            matches = store.search_media_embeddings(
                query_vec,
                config.smart_search.model,
                include_quoted_media=config.smart_search.include_quoted_media,
                min_score=threshold,
                limit=limit,
            )
        finally:
            store.close()

        if not matches:
            console.print("No matching images found.")
            return
        for tweet_id, score in matches:
            console.print(f"{score:.4f}  https://x.com/i/status/{tweet_id}")
    except (TweetNookError, RuntimeError, ValueError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
