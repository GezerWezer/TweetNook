"""Load tweets and their media once, into parts any request over them can reuse.

A rejected Free batch is subdivided by selecting prepared tweets, not by loading them again,
so uploads and decoded images are held by one scope until every child request has finished.
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from rich.console import Console

from tweetnook.tagging.failures import safe_error
from tweetnook.tagging.sdk import upload_owned_file


@dataclass(frozen=True, slots=True)
class TweetTagContext:
    tweet_id: str
    tweet_type: str
    author_display_name: str
    author_username: str
    text: str


@dataclass(slots=True)
class PreparedAsset:
    media_type: str
    mime_type: str
    # An inline image, or the Gemini file for an upload.
    part: Any
    uri: str | None = None

    def interaction_part(self) -> dict[str, Any]:
        return {
            "type": "video" if self.media_type in {"video", "animated_gif"} else "image",
            "uri": self.uri,
            "mime_type": self.mime_type,
        }


@dataclass(slots=True)
class PreparedTweet:
    context: TweetTagContext
    # Tweet text, source labels and media in the order the model reads them.
    segments: list[str | PreparedAsset] = field(default_factory=list)

    @property
    def assets(self) -> list[PreparedAsset]:
        return [segment for segment in self.segments if isinstance(segment, PreparedAsset)]


@dataclass(frozen=True, slots=True)
class PreparedBatch:
    content_type: Literal["media", "text"]
    tweets: tuple[PreparedTweet, ...]

    @property
    def tweet_ids(self) -> list[str]:
        return [tweet.context.tweet_id for tweet in self.tweets]

    def halves(self) -> tuple[PreparedBatch, PreparedBatch]:
        midpoint = len(self.tweets) // 2
        return (
            PreparedBatch(self.content_type, self.tweets[:midpoint]),
            PreparedBatch(self.content_type, self.tweets[midpoint:]),
        )

    def contexts(self) -> dict[str, TweetTagContext]:
        return {tweet.context.tweet_id: tweet.context for tweet in self.tweets}

    def free_parts(self, system_prompt: str) -> list[Any]:
        parts: list[Any] = [system_prompt, "\n\n--- TWEETS TO TAG ---\n\n"]
        for tweet in self.tweets:
            parts.extend(
                segment.part if isinstance(segment, PreparedAsset) else segment
                for segment in tweet.segments
            )
            parts.append("\n")
        return parts

    def interaction_input(self) -> list[dict[str, Any]]:
        return [
            segment.interaction_part()
            if isinstance(segment, PreparedAsset)
            else {"type": "text", "text": segment}
            for tweet in self.tweets
            for segment in tweet.segments
        ]

    def request_metadata(self, *, existing_tag_count: int, tag_config: Any) -> dict[str, Any]:
        assets = [asset for tweet in self.tweets for asset in tweet.assets]
        videos = sum(asset.media_type == "video" for asset in assets)
        gifs = sum(asset.media_type == "animated_gif" for asset in assets)
        return {
            "tweet_count": len(self.tweets),
            "media_count": len(assets),
            "image_count": len(assets) - videos - gifs,
            "video_count": videos,
            "gif_count": gifs,
            "input_text_chars": sum(
                len(segment)
                for tweet in self.tweets
                for segment in tweet.segments
                if isinstance(segment, str)
            ),
            "existing_tag_count": existing_tag_count,
            "tagging_hint_count": len(tag_config.tagging_context),
            "additional_instructions_present": bool(tag_config.additional_instructions),
        }


@dataclass(slots=True)
class MediaLoader:
    """Everything loading media needs. The caller owns `images` and `uploads` and closes them."""

    client: Any
    data_dir: Path
    max_media_size_mb: float
    # Paid sends every asset as a Gemini file; Free sends photos inline.
    upload_all: bool
    image_module: Any
    images: list[Any]
    uploads: list[Any]
    console: Console
    api_key: str | None


def _media_mime(path: Path, media_type: str | None) -> str:
    guessed, _encoding = mimetypes.guess_type(path.name)
    if guessed:
        return guessed
    return "video/mp4" if media_type in {"video", "animated_gif"} else "image/jpeg"


def _remote_state_name(remote: Any) -> str:
    state = getattr(remote, "state", None)
    return str(getattr(state, "name", None) or state or "ACTIVE").upper()


async def _wait_for_remote_file(client: Any, remote: Any) -> Any:
    for _attempt in range(90):
        current = await asyncio.to_thread(client.files.get, name=remote.name)
        state = _remote_state_name(current)
        if state == "ACTIVE":
            return current
        if state == "FAILED":
            raise RuntimeError(f"Gemini failed to process {remote.name}")
        await asyncio.sleep(10)
    raise TimeoutError(f"Gemini file processing timed out for {remote.name}")


def _tweet_context(tweet_id: str, tweet: dict[str, Any], quoted_id: str | None) -> TweetTagContext:
    try:
        raw = json.loads(tweet.get("raw_json") or "{}")
        if not isinstance(raw, dict):
            raw = {}
    except (json.JSONDecodeError, TypeError):
        raw = {}
    legacy = raw.get("legacy") or {}
    tweet_type = "Standalone"
    if quoted_id or legacy.get("quoted_status_id_str"):
        tweet_type = "Quote Tweet"
    elif legacy.get("in_reply_to_status_id_str"):
        tweet_type = "Reply"
    return TweetTagContext(
        tweet_id=tweet_id,
        tweet_type=tweet_type,
        author_display_name=str(tweet.get("author_display_name") or ""),
        author_username=str(tweet.get("author_username") or ""),
        text=str(tweet.get("text") or ""),
    )


def _tweet_text(
    context: TweetTagContext,
    quoted_id: str | None,
    quoted: dict[str, Any] | None,
    *,
    with_ids: bool,
) -> str:
    text = ""
    if with_ids:
        # Free batches need an explicit identifier so the model can map each result back to
        # its source tweet. Paid requests are one-tweet operations, so do not expose archive
        # IDs in the model input/output path.
        text += f"[ID: {context.tweet_id}]\n"
    text += (
        f"Type: {context.tweet_type}\n"
        f"Author: {context.author_display_name} (@{context.author_username})\n"
        f"Text: {json.dumps(context.text)}\n"
    )
    if quoted is not None:
        text += "Quoted Tweet Context:\n"
        if with_ids:
            text += f"ID: {quoted_id}\n"
        text += (
            f"Author: {quoted.get('author_display_name', '')} "
            f"(@{quoted.get('author_username', '')})\n"
            f"Text: {json.dumps(quoted.get('text', ''))}\n"
        )
    return text


def _decode_image(loader: MediaLoader, path: Path) -> Any:
    image = loader.image_module.open(str(path))
    loader.images.append(image)
    try:
        image.load()
    except BaseException:
        loader.images.remove(image)
        with suppress(Exception):
            image.close()
        raise
    return image


def _unreadable_errors(loader: MediaLoader) -> tuple[type[Exception], ...]:
    """What reading or decoding one bad file raises. Pillow reports some as SyntaxError."""
    bomb = getattr(loader.image_module, "DecompressionBombError", OSError)
    return (OSError, ValueError, SyntaxError, bomb)


async def _load_asset(
    loader: MediaLoader, media: dict[str, Any], pending: list[tuple[PreparedAsset, Any]]
) -> PreparedAsset | None:
    """Load one saved file. A file that can't be read costs only itself, not its batch."""
    local_path = media.get("local_path")
    if not local_path:
        return None
    absolute = loader.data_dir / local_path
    try:
        if not absolute.exists():
            return None
        size_bytes = absolute.stat().st_size
        # stat succeeds without read permission. Checking here keeps a bad file apart from
        # a failed upload, which is the provider's failure and belongs to the batch.
        absolute.open("rb").close()
    except _unreadable_errors(loader) as error:
        _report_unreadable(loader, absolute, error)
        return None
    size_mb = size_bytes / (1024 * 1024)
    if size_mb > loader.max_media_size_mb:
        loader.console.print(
            f"[yellow]Skipping media {absolute.name}: size ({size_mb:.1f}MB) "
            f"exceeds limit ({loader.max_media_size_mb}MB)[/yellow]"
        )
        return None
    media_type = str(media.get("media_type") or "")
    mime_type = _media_mime(absolute, media_type)
    if loader.upload_all or media_type in {"video", "animated_gif"}:
        remote = await upload_owned_file(loader.client, str(absolute), loader.uploads)
        asset = PreparedAsset(media_type, mime_type, remote)
        pending.append((asset, remote))
        return asset
    try:
        return PreparedAsset(media_type, mime_type, _decode_image(loader, absolute))
    except _unreadable_errors(loader) as error:
        _report_unreadable(loader, absolute, error)
        return None


def _report_unreadable(loader: MediaLoader, path: Path, error: Exception) -> None:
    loader.console.print(
        f"[yellow]Skipping media {path.name}: {safe_error(error, loader.api_key)}[/yellow]"
    )


async def _activate_uploads(
    loader: MediaLoader,
    tweets: Sequence[PreparedTweet],
    pending: list[tuple[PreparedAsset, Any]],
) -> None:
    for asset, remote in pending:
        try:
            active = await _wait_for_remote_file(loader.client, remote)
        except Exception as error:
            loader.console.print(
                f"[red]Failed while processing Gemini file {remote.name}: "
                f"{safe_error(error, loader.api_key)}[/red]"
            )
            for tweet in tweets:
                if asset in tweet.segments:
                    tweet.segments.remove(asset)
            continue
        asset.part = active
        asset.uri = getattr(active, "uri", None) or getattr(remote, "uri", None)
        asset.mime_type = getattr(active, "mime_type", None) or asset.mime_type


async def prepare_batch(
    tweet_ids: list[str],
    content_type: Literal["media", "text"],
    *,
    quote_targets: dict[str, str],
    all_media: dict[str, list[dict[str, Any]]],
    tweet_objs: dict[str, dict[str, Any]],
    loader: MediaLoader,
    with_ids: bool,
) -> PreparedBatch:
    tweets: list[PreparedTweet] = []
    pending: list[tuple[PreparedAsset, Any]] = []
    for tweet_id in tweet_ids:
        tweet = tweet_objs.get(tweet_id)
        if tweet is None:
            continue
        quoted_id = quote_targets.get(tweet_id)
        context = _tweet_context(tweet_id, tweet, quoted_id)
        prepared = PreparedTweet(context)
        prepared.segments.append(
            _tweet_text(context, quoted_id, tweet_objs.get(quoted_id or ""), with_ids=with_ids)
        )
        tweets.append(prepared)
        if content_type == "text":
            continue
        media_sources = [("Direct Tweet Media", tweet_id)]
        if quoted_id:
            media_sources.append(("Quoted Tweet Media", quoted_id))
        for source_label, source_id in media_sources:
            source_media = all_media.get(source_id, [])
            if source_media:
                prepared.segments.append(f"\n{source_label}:\n")
            for media in source_media:
                asset = await _load_asset(loader, media, pending)
                if asset is not None:
                    prepared.segments.append(asset)
    await _activate_uploads(loader, tweets, pending)
    # Media tagging never falls back to text alone, so a tweet left with no usable media isn't
    # sent at all. It stays a candidate for the next run.
    sendable = [tweet for tweet in tweets if content_type == "text" or tweet.assets]
    return PreparedBatch(content_type, tuple(sendable))
