from __future__ import annotations

import asyncio
import json
import mimetypes
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError
from rich.console import Console
from rich.text import Text

from .activity_history import new_run_id
from .automated_tagging import AutomatedTaggingUnavailable, load_gemini
from .config import AppConfig, XDGPaths
from .gemini_accounting import (
    SEARCH_MONTHLY_ALLOWANCE,
    AIUsageLedger,
    SearchAccounting,
    _has_usable_interaction_usage,
    failed_usage_record,
    interaction_search_accounting,
    new_request_id,
    usage_from_generate_content,
    usage_from_interaction,
)
from .gemini_pricing import (
    load_openrouter_pricing,
    minimum_input_cost_usd,
    pricing_multiplier,
)
from .pipeline import current_pipeline
from .rpd import RpdStatus, get_rpd_status, reserve_rpd_request
from .storage.backend import ArchiveStore
from .tagging_prompts import build_media_system_prompt, build_text_system_prompt

try:  # Optional extra: importing this module must remain safe in base installations.
    from google import genai
    from google.genai import types
    from PIL import Image
except ImportError:  # pragma: no cover - exercised in a dependency-isolated subprocess test
    genai = None
    types = None
    Image = None


MEDIA_TAGGING_SYSTEM_PROMPT = build_media_system_prompt()
TEXT_TAGGING_SYSTEM_PROMPT = build_text_system_prompt()
TAGGING_SYSTEM_PROMPT = MEDIA_TAGGING_SYSTEM_PROMPT


class MediaTagResult(BaseModel):
    description: str = Field(min_length=1)
    tags: list[str] = Field(min_length=2, max_length=5)


class TextTagResult(BaseModel):
    tags: list[str] = Field(min_length=2, max_length=5)


class _FreeMediaTagResult(MediaTagResult):
    id: str


class _FreeTextTagResult(TextTagResult):
    id: str


class TaggingPreview(BaseModel):
    """Structured result returned by test runs without persisting tweet tags."""

    id: str
    content_type: Literal["media", "text"]
    model: str
    tweet_type: str
    author_display_name: str
    author_username: str
    text: str
    description: str | None = None
    tags: list[str]


TagResult = MediaTagResult
# Keep the wave-oriented execution path so a future backfill mode can raise
# this internal ceiling without changing tagging orchestration.  It is not a
# configuration setting and normal Paid processing is sequential for now.
PAID_CONCURRENCY = 1


@dataclass(frozen=True, slots=True)
class TaggingRunResult:
    processed: int = 0
    tagged: int = 0
    batches: int = 0


@dataclass(frozen=True, slots=True)
class _TweetTagContext:
    tweet_id: str
    tweet_type: str
    author_display_name: str
    author_username: str
    text: str


SEARCH_ATTEMPT_SAFETY_RESERVATION = 5


@dataclass(frozen=True, slots=True)
class _SearchReservation:
    """In-flight safety budget for one provider attempt.

    This is an application reservation, not a claim about how many searches a
    Gemini request can perform.  The provider's authoritative count replaces it
    when the response is available.
    """

    amount: int = SEARCH_ATTEMPT_SAFETY_RESERVATION


@dataclass(slots=True)
class _SearchPolicy:
    ledger: AIUsageLedger
    reserve: int
    enabled: bool
    in_flight: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    unavailable_warning_emitted: bool = False
    limit_warning_emitted: bool = False
    unknown_policy: str = "disable"
    max_unknown_attempts: int = 3
    unknown_attempts: int = 0

    async def reserve_attempt(self) -> tuple[_SearchReservation | None, str | None]:
        async with self.lock:
            if not self.enabled:
                return None, "accounting" if self.unavailable_warning_emitted else None
            used = self.ledger.monthly_search_count()
            if (
                used + self.in_flight + SEARCH_ATTEMPT_SAFETY_RESERVATION
                >= SEARCH_MONTHLY_ALLOWANCE - self.reserve
            ):
                first = not self.limit_warning_emitted
                self.limit_warning_emitted = True
                return None, "limit" if first else None
            reservation = _SearchReservation()
            self.in_flight += reservation.amount
            return reservation, None

    async def commit(
        self,
        reservation: _SearchReservation | None,
        accounting: SearchAccounting | None,
    ) -> None:
        if reservation is None:
            return
        async with self.lock:
            self.in_flight = max(self.in_flight - reservation.amount, 0)

    async def mark_unknown(self, reservation: _SearchReservation | None) -> bool:
        """Consume an estimated reservation; cap tolerated unknown attempts."""

        if reservation is None:
            return False
        async with self.lock:
            self.in_flight = max(self.in_flight - reservation.amount, 0)
            if not self.enabled:
                return False
            self.unknown_attempts += 1
            if (
                self.unknown_policy == "reserve"
                and self.unknown_attempts < self.max_unknown_attempts
            ):
                return False
            self.enabled = False
            first = not self.unavailable_warning_emitted
            self.unavailable_warning_emitted = True
            return first

    # Compatibility shims for callers from older integrations.  New code uses
    # reserve_attempt/commit/mark_unknown so every retry owns its own lifecycle.
    async def acquire(self) -> tuple[bool, str | None]:
        reservation, reason = await self.reserve_attempt()
        return reservation is not None, reason

    async def finish(
        self,
        acquired: bool,
        accounting: SearchAccounting | None,
        *,
        disable_if_unknown: bool = True,
    ) -> bool:
        reservation = _SearchReservation() if acquired else None
        if accounting is None or not accounting.reliable:
            if disable_if_unknown:
                return await self.mark_unknown(reservation)
            await self.commit(reservation, accounting)
            return False
        await self.commit(reservation, accounting)
        return False


_THINKING_BUDGETS = {
    "none": 0,
    "low": 1024,
    "medium": 4096,
    "high": 8192,
}
_FREE_REQUEST_TIMES: dict[str, deque[float]] = defaultdict(deque)


@dataclass(slots=True)
class _PaidRunState:
    attempts: int = 0
    stop_reason: str | None = None


def _provider_error_code(error: BaseException) -> int | None:
    for raw in (
        getattr(error, "status_code", None),
        getattr(error, "code", None),
        getattr(getattr(error, "response", None), "status_code", None),
    ):
        try:
            code = int(raw)
        except (ValueError, TypeError):
            continue
        if 400 <= code < 600:
            return code
    return None


def _budget_pause(tag_config: Any, usage: dict[str, Any]) -> str | None:
    if usage["unreserved"]:
        return "earlier billing uncertainty has no cost reservation"
    if usage["unknown"] and tag_config.unknown_billing_policy == "stop":
        return "billing usage is uncertain (strict policy)"
    if usage["unknown"] >= tag_config.max_unknown_requests_per_day:
        return "the daily limit on billing-unknown requests was reached"
    if usage["unpriced"]:
        return "an earlier request has no price estimate"
    return None


def _defer_tagging_failure(store: ArchiveStore, tweet_ids: list[str]) -> None:
    """Persist backoff separately so explicit retries preserve existing tags."""
    now = time.time()
    for tweet_id in tweet_ids:
        key = f"tagging_retry:{tweet_id}"
        row = store.conn.execute("SELECT value FROM archive WHERE row_key = ?", (key,)).fetchone()
        try:
            previous = json.loads(row[0]) if row else {}
            attempts = max(int(previous.get("attempts", 0)), 0) + 1
        except (TypeError, ValueError, AttributeError):
            attempts = 1
        delay = min(3600 * 2 ** min(attempts - 1, 8), 7 * 86400)
        store.conn.execute(
            "INSERT OR REPLACE INTO archive "
            "(row_key, record_type, key, value, updated_at) VALUES (?, 'metadata', ?, ?, ?)",
            (key, key, json.dumps({"attempts": attempts, "next_retry_at": now + delay}), str(now)),
        )
    store.conn.commit()


async def _wait_for_free_rpm(model: str, requests_per_minute: int) -> None:
    timestamps = _FREE_REQUEST_TIMES[model.casefold()]
    while True:
        now = time.monotonic()
        while timestamps and now - timestamps[0] >= 60:
            timestamps.popleft()
        if len(timestamps) < requests_per_minute:
            timestamps.append(now)
            return
        await asyncio.sleep(max(60 - (now - timestamps[0]), 0.01))


def _safe_error(error: BaseException, api_key: str | None) -> str:
    message = str(error)
    if api_key:
        message = message.replace(api_key, "[REDACTED]")
    return message


def _ensure_optional_dependencies() -> bool:
    global Image, genai, types
    if genai is not None and types is not None and Image is not None:
        return True
    try:
        genai, types, Image = load_gemini()
    except AutomatedTaggingUnavailable:
        return False
    return True


def _thinking_config(level: str) -> Any | None:
    normalized = level.strip().lower()
    fields = getattr(types.ThinkingConfig, "model_fields", {})
    if "thinking_level" in fields:
        return types.ThinkingConfig(thinking_level=normalized)
    if "thinking_budget" in fields:
        return types.ThinkingConfig(
            thinking_budget=_THINKING_BUDGETS.get(normalized, _THINKING_BUDGETS["high"])
        )
    return None


def _interaction_thinking_level(level: str) -> str:
    return "minimal" if level == "none" else level


def _mark_failed(store: ArchiveStore, tweet_ids: list[str]) -> None:
    for tweet_id in tweet_ids:
        store.conn.execute(
            "INSERT OR REPLACE INTO archive "
            "(row_key, record_type, tweet_id, raw_json, enrichment_state, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                f"media_tag:{tweet_id}",
                "media_tag",
                tweet_id,
                "{}",
                "failed",
                str(time.time()),
            ),
        )
    # Failed attempts are diagnostic records, but they must be durable so the
    # next sync/tagging run can see them as retryable rather than relying on an
    # uncommitted transaction in the current connection.
    store.conn.commit()


def _print_rpd_exhausted(console: Console, status: RpdStatus, model: str) -> None:
    reset_at = status.reset_at.strftime("%Y-%m-%d %H:%M %Z")
    console.print(
        Text(
            f"Gemini daily request limit reached for {model} "
            f"({status.used}/{status.limit}). No further requests will be sent until "
            f"{reset_at}.",
            style="yellow",
        )
    )


def _print_rpd_storage_error(
    console: Console,
    error: BaseException,
    api_key: str | None,
) -> None:
    console.print(
        Text(
            "Could not update Gemini daily request usage; request not sent: "
            f"{_safe_error(error, api_key)}",
            style="red",
        )
    )


def _print_tag_preview(
    console: Console,
    context: _TweetTagContext,
    *,
    description: str | None,
    tags: list[str],
) -> None:
    author = context.author_display_name
    if context.author_username:
        author = (
            f"{author} (@{context.author_username})" if author else f"@{context.author_username}"
        )
    console.print(Text(f"Tweet {context.tweet_id} ({context.tweet_type})", style="bold"))
    if author:
        console.print(Text(f"Author: {author}"))
    console.print(Text(f"Text: {context.text}"))
    if description:
        console.print(Text(f"Description: {description}"))
    console.print(Text(f"Tags: {', '.join(tags)}"))


def _quote_targets(store: ArchiveStore, tweet_ids: list[str]) -> dict[str, str]:
    if not tweet_ids:
        return {}
    placeholders = ", ".join("?" for _tweet_id in tweet_ids)
    try:
        rows = store.conn.execute(
            f"""
            SELECT tweet_id, target_tweet_id
            FROM archive INDEXED BY idx_archive_tweet_id
            WHERE record_type = 'tweet_relation'
              AND relation_type = 'quote_of'
              AND tweet_id IN ({placeholders})
            ORDER BY tweet_id, target_tweet_id
            """,
            tweet_ids,
        ).fetchall()
    except Exception:
        return {}
    targets: dict[str, str] = {}
    for row in rows:
        source_id = row["tweet_id"]
        target_id = row["target_tweet_id"]
        if source_id and target_id:
            targets.setdefault(source_id, target_id)
    return targets


def _content_types_for_tweets(
    store: ArchiveStore,
    tweet_ids: list[str],
    quote_targets: dict[str, str],
) -> dict[str, Literal["media", "text"]]:
    relevant_ids = list(dict.fromkeys([*tweet_ids, *quote_targets.values()]))
    if not relevant_ids:
        return {}
    placeholders = ", ".join("?" for _tweet_id in relevant_ids)
    rows = store.conn.execute(
        f"""
        SELECT DISTINCT tweet_id
        FROM archive INDEXED BY idx_archive_tweet_id
        WHERE record_type = 'media' AND tweet_id IN ({placeholders})
        """,
        relevant_ids,
    ).fetchall()
    media_ids = {row["tweet_id"] for row in rows if row["tweet_id"]}
    return {
        tweet_id: (
            "media" if tweet_id in media_ids or quote_targets.get(tweet_id) in media_ids else "text"
        )
        for tweet_id in tweet_ids
    }


def _top_archive_tags(store: ArchiveStore, limit: int = 50) -> list[str]:
    if not hasattr(store, "get_tag_counts"):
        return []
    try:
        return [str(row["tag"]) for row in store.get_tag_counts(limit=limit) if row.get("tag")]
    except Exception:
        return []


def _run_id() -> str:
    pipeline = current_pipeline()
    active = getattr(pipeline, "run_id", None) if pipeline is not None else None
    return str(active or new_run_id())


def _media_mime(path, media_type: str | None) -> str:
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


async def _count_request_tokens(
    client: Any,
    *,
    model: str,
    system_prompt: str,
    interaction_input: list[Any],
    search_enabled: bool = False,
    console: Console | None = None,
    api_key: str | None = None,
) -> tuple[int | None, bool]:
    """Count prompt/media input; subsequent Search/tool expansion is uncertain."""

    counter = getattr(getattr(client, "models", None), "count_tokens", None)
    if not callable(counter):
        if console is not None:
            console.print(
                Text("Token preflight unavailable: SDK has no token counter.", style="yellow")
            )
        return None, False
    contents: list[Any] = []
    for item in interaction_input:
        if isinstance(item, str):
            contents.append(item)
            continue
        if not isinstance(item, dict):
            contents.append(item)
            continue
        item_type = str(item.get("type") or "")
        if item_type == "text":
            contents.append(str(item.get("text") or ""))
            continue
        uri = item.get("uri")
        if uri:
            part_factory = getattr(getattr(types, "Part", None), "from_uri", None)
            if callable(part_factory):
                try:
                    contents.append(
                        part_factory(
                            file_uri=str(uri),
                            mime_type=str(item.get("mime_type") or "application/octet-stream"),
                        )
                    )
                    continue
                except Exception:
                    pass
            # Token preflight is telemetry only.  A URI string is still a
            # GenerateContent-compatible scalar when a test/fallback SDK has
            # no Part factory; never pass Interactions dictionaries through.
            contents.append(str(uri))
    kwargs: dict[str, Any] = {"model": model, "contents": contents}
    count_config_type = getattr(types, "CountTokensConfig", None)
    if count_config_type is not None:
        try:
            config_kwargs: dict[str, Any] = {"system_instruction": system_prompt}
            kwargs["config"] = count_config_type(**config_kwargs)
        except Exception:
            pass
    try:
        response = await asyncio.to_thread(counter, **kwargs)
    except TypeError:
        # Older SDK versions may not accept a CountTokensConfig. Retry the
        # same current request without the optional config before falling back.
        kwargs.pop("config", None)
        kwargs["contents"] = [system_prompt, *contents]
        try:
            response = await asyncio.to_thread(counter, **kwargs)
        except Exception as error:
            if console is not None:
                console.print(Text(f"Token preflight unavailable: {_safe_error(error, api_key)}"))
            return None, False
    except Exception as error:
        if console is not None:
            console.print(Text(f"Token preflight unavailable: {_safe_error(error, api_key)}"))
        return None, False
    total = getattr(response, "total_tokens", None)
    if total is None:
        total = getattr(response, "totalTokenCount", None)
    try:
        return max(int(total), 1), True
    except (TypeError, ValueError):
        if console is not None:
            console.print(Text("Token preflight unavailable: invalid token count.", style="yellow"))
        return None, False


async def _create_interaction(client: Any, **kwargs: Any) -> Any:
    aio = getattr(client, "aio", None)
    if aio is not None and getattr(aio, "interactions", None) is not None:
        if hasattr(aio.interactions, "sdk_configuration"):
            # Newer SDKs retry internally even with HttpRetryOptions(attempts=1).
            # Own retries here so every HTTP attempt has separate accounting.
            aio.interactions.sdk_configuration.retry_config = None
        return await aio.interactions.create(**kwargs)
    if hasattr(client.interactions, "sdk_configuration"):
        client.interactions.sdk_configuration.retry_config = None
    return await asyncio.to_thread(client.interactions.create, **kwargs)


def _normalized_tags(tags: list[str], existing_tags: list[str]) -> list[str]:
    canonical = {tag.casefold(): tag for tag in existing_tags}
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        value = str(raw).strip()
        if not value:
            continue
        value = canonical.get(value.casefold(), value)
        folded = value.casefold()
        if folded not in seen:
            normalized.append(value)
            seen.add(folded)
    return normalized


async def tag_media_tweets(
    store: ArchiveStore,
    config: AppConfig,
    paths: XDGPaths,
    console: Console,
    tweet_ids: list[str],
    model_override: str | None = None,
    *,
    dry_run: bool = False,
    content_type: Literal["media", "text"] | None = None,
    quoted_tweet_ids: dict[str, str] | None = None,
    preview_results: list[TaggingPreview] | None = None,
    _search_policy: _SearchPolicy | None = None,
    _ledger: AIUsageLedger | None = None,
    _run_identifier: str | None = None,
    _existing_tags: list[str] | None = None,
    _paid_stop: list[bool] | None = None,
    _paid_state: _PaidRunState | None = None,
    _attempt: int = 1,
    _logical_request_id: str | None = None,
) -> int:
    """Tag one Paid tweet or one homogeneous Free batch."""
    pipeline = current_pipeline()
    if pipeline is not None and pipeline.has_step("tagging") and not dry_run:
        console = pipeline.capture_console("tagging")  # type: ignore[assignment]
    tag_config = config.tagging
    if not tag_config.enabled or not tag_config.api_key:
        console.print("[yellow]Tagging is disabled or missing API key.[/yellow]")
        return 0
    if not _ensure_optional_dependencies():
        console.print(
            "[yellow]Automated tagging is not installed; sync will continue. Install "
            "tweetnook[automated-tagging] to enable it.[/yellow]"
        )
        return 0
    if not tweet_ids:
        return 0
    if dry_run and len(tweet_ids) != 1:
        raise ValueError("Test tag generation requires exactly one tweet")

    model_name = model_override or tag_config.model
    ledger = _ledger or AIUsageLedger(paths.ai_usage_dir)
    run_identifier = _run_identifier or _run_id()
    existing_tags = tuple(
        _existing_tags if _existing_tags is not None else _top_archive_tags(store)
    )
    search_policy = _search_policy or _SearchPolicy(
        ledger,
        tag_config.search_safety_reserve,
        tag_config.api_mode == "paid" and tag_config.google_search,
        unknown_policy=tag_config.search_unknown_policy,
        max_unknown_attempts=tag_config.max_unknown_search_requests,
    )
    paid_state = _paid_state or _PaidRunState()
    if paid_state.stop_reason:
        return 0
    logical_request_id = _logical_request_id or new_request_id()

    quote_targets = (
        _quote_targets(store, tweet_ids) if quoted_tweet_ids is None else quoted_tweet_ids
    )
    if content_type is None:
        content_types = _content_types_for_tweets(store, tweet_ids, quote_targets)
        grouped_ids = {
            kind: [tweet_id for tweet_id in tweet_ids if content_types[tweet_id] == kind]
            for kind in ("media", "text")
        }
        groups = [kind for kind, ids in grouped_ids.items() if ids]
        if len(groups) > 1:
            total = 0
            for kind in groups:
                ids = grouped_ids[kind]
                group_existing_tags = tuple(_top_archive_tags(store))
                total += await tag_media_tweets(
                    store,
                    config,
                    paths,
                    console,
                    ids,
                    model_override,
                    dry_run=dry_run,
                    content_type=kind,
                    quoted_tweet_ids={
                        tweet_id: quote_targets[tweet_id]
                        for tweet_id in ids
                        if tweet_id in quote_targets
                    },
                    preview_results=preview_results,
                    _search_policy=search_policy,
                    _ledger=ledger,
                    _run_identifier=run_identifier,
                    _existing_tags=group_existing_tags,
                    _paid_stop=_paid_stop,
                    _paid_state=paid_state,
                )
            return total
        content_type = groups[0] if groups else "text"

    if tag_config.api_mode == "paid" and len(tweet_ids) > 1:
        # Keep the generic wave path, but Paid processing is intentionally
        # sequential until a future feature opts into a higher internal cap.
        semaphore = asyncio.Semaphore(PAID_CONCURRENCY)

        async def paid_one(tweet_id: str) -> int:
            async with semaphore:
                return await tag_media_tweets(
                    store,
                    config,
                    paths,
                    console,
                    [tweet_id],
                    model_override,
                    dry_run=dry_run,
                    content_type=content_type,
                    quoted_tweet_ids=(
                        {tweet_id: quote_targets[tweet_id]} if tweet_id in quote_targets else {}
                    ),
                    preview_results=preview_results,
                    _search_policy=search_policy,
                    _ledger=ledger,
                    _run_identifier=run_identifier,
                    _existing_tags=existing_tags,
                    _paid_stop=_paid_stop,
                    _paid_state=paid_state,
                )

        return sum(await asyncio.gather(*(paid_one(tweet_id) for tweet_id in tweet_ids)))

    if tag_config.api_mode != "paid":
        try:
            status = get_rpd_status(store, model=model_name, limit=tag_config.free_rpd)
        except Exception as error:
            _print_rpd_storage_error(console, error, tag_config.api_key)
            return 0
        if not status.allowed:
            _print_rpd_exhausted(console, status, model_name)
            return 0

    try:
        client = genai.Client(
            api_key=tag_config.api_key,
            http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1)),
        )
    except Exception as error:
        console.print(
            f"[red]Could not initialize Gemini: {_safe_error(error, tag_config.api_key)}[/red]"
        )
        return 0

    def make_system_prompt(search_enabled: bool) -> str:
        if content_type == "media":
            return build_media_system_prompt(
                existing_tags=existing_tags,
                tagging_context=tag_config.tagging_context,
                additional_instructions=tag_config.additional_instructions,
                google_search=search_enabled,
                tweet_isolation=tag_config.api_mode == "free" and len(tweet_ids) > 1,
            )
        return build_text_system_prompt(
            existing_tags=existing_tags,
            tagging_context=tag_config.tagging_context,
            additional_instructions=tag_config.additional_instructions,
            google_search=search_enabled,
            tweet_isolation=tag_config.api_mode == "free" and len(tweet_ids) > 1,
        )

    # Free requests never enable Search.  Paid requests rebuild this prompt per
    # provider attempt so a retry after uncertain Search accounting can proceed
    # without the Search tool and its corresponding instruction.
    system_prompt = make_system_prompt(False)

    context_ids = list(dict.fromkeys([*tweet_ids, *quote_targets.values()]))
    placeholders = ", ".join("?" for _tweet_id in context_ids)
    all_media: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in store.conn.execute(
        f"""
        SELECT tweet_id, media_key, media_type, local_path
        FROM archive INDEXED BY idx_archive_tweet_id
        WHERE record_type = 'media' AND tweet_id IN ({placeholders})
        """,
        context_ids,
    ).fetchall():
        media = dict(row)
        all_media[media["tweet_id"]].append(media)
    tweet_objs = {
        row["tweet_id"]: dict(row)
        for row in store.conn.execute(
            f"""
            SELECT tweet_id, raw_json, author_display_name, author_username, text
            FROM archive INDEXED BY idx_archive_tweet_id
            WHERE record_type = 'tweet_object' AND tweet_id IN ({placeholders})
            """,
            context_ids,
        ).fetchall()
    }

    free_parts: list[Any] = [system_prompt, "\n\n--- TWEETS TO TAG ---\n\n"]
    interaction_input: list[Any] = []
    tweet_contexts: dict[str, _TweetTagContext] = {}
    active_media_by_tweet: dict[str, int] = defaultdict(int)
    opened_images: list[Any] = []
    uploaded_files: list[Any] = []
    pending_files: list[tuple[Any, str, str, str, int | None, int | None]] = []
    media_count = 0
    image_count = 0
    video_count = 0
    gif_count = 0

    async def split_free_batch(reason: str) -> int:
        if len(tweet_ids) <= 1:
            return 0
        midpoint = len(tweet_ids) // 2
        console.print(
            f"[yellow]{reason} for a Free batch of {len(tweet_ids)}. Splitting into "
            f"batches of {midpoint} and {len(tweet_ids) - midpoint}...[/yellow]"
        )
        total = 0
        for subset in (tweet_ids[:midpoint], tweet_ids[midpoint:]):
            split_existing_tags = tuple(_top_archive_tags(store))
            total += await tag_media_tweets(
                store,
                config,
                paths,
                console,
                subset,
                model_override,
                dry_run=dry_run,
                content_type=content_type,
                quoted_tweet_ids={
                    tweet_id: quote_targets[tweet_id]
                    for tweet_id in subset
                    if tweet_id in quote_targets
                },
                preview_results=preview_results,
                _search_policy=search_policy,
                _ledger=ledger,
                _run_identifier=run_identifier,
                _existing_tags=split_existing_tags,
            )
        return total

    try:
        for tweet_id in tweet_ids:
            tweet = tweet_objs.get(tweet_id)
            if tweet is None:
                continue
            try:
                raw = json.loads(tweet.get("raw_json") or "{}")
                if not isinstance(raw, dict):
                    raw = {}
            except (json.JSONDecodeError, TypeError):
                raw = {}
            legacy = raw.get("legacy") or {}
            quoted_id = quote_targets.get(tweet_id)
            tweet_type = "Standalone"
            if quoted_id or legacy.get("quoted_status_id_str"):
                tweet_type = "Quote Tweet"
            elif legacy.get("in_reply_to_status_id_str"):
                tweet_type = "Reply"
            context = _TweetTagContext(
                tweet_id=tweet_id,
                tweet_type=tweet_type,
                author_display_name=str(tweet.get("author_display_name") or ""),
                author_username=str(tweet.get("author_username") or ""),
                text=str(tweet.get("text") or ""),
            )
            tweet_contexts[tweet_id] = context
            tweet_text = ""
            if tag_config.api_mode == "free":
                # Free batches need an explicit identifier so the model can
                # map each result back to its source tweet. Paid requests are
                # one-tweet operations, so do not expose archive IDs in the
                # model input/output path.
                tweet_text += f"[ID: {tweet_id}]\n"
            tweet_text += (
                f"Type: {tweet_type}\n"
                f"Author: {context.author_display_name} (@{context.author_username})\n"
                f"Text: {json.dumps(context.text)}\n"
            )
            quoted = tweet_objs.get(quoted_id or "")
            if quoted is not None:
                tweet_text += "Quoted Tweet Context:\n"
                if tag_config.api_mode == "free":
                    tweet_text += f"ID: {quoted_id}\n"
                tweet_text += (
                    f"Author: {quoted.get('author_display_name', '')} "
                    f"(@{quoted.get('author_username', '')})\n"
                    f"Text: {json.dumps(quoted.get('text', ''))}\n"
                )
            free_parts.append(tweet_text)
            interaction_input.append({"type": "text", "text": tweet_text})
            if content_type == "text":
                free_parts.append("\n")
                continue

            media_sources = [("Direct Tweet Media", tweet_id)]
            if quoted_id:
                media_sources.append(("Quoted Tweet Media", quoted_id))
            for source_label, source_id in media_sources:
                source_media = all_media.get(source_id, [])
                if source_media:
                    label = f"\n{source_label}:\n"
                    free_parts.append(label)
                    interaction_input.append({"type": "text", "text": label})
                for media in source_media:
                    local_path = media.get("local_path")
                    if not local_path:
                        continue
                    absolute = paths.data_dir / local_path
                    if not absolute.exists():
                        continue
                    size_mb = absolute.stat().st_size / (1024 * 1024)
                    if size_mb > tag_config.max_media_size_mb:
                        console.print(
                            f"[yellow]Skipping media {absolute.name}: size ({size_mb:.1f}MB) "
                            f"exceeds limit ({tag_config.max_media_size_mb}MB)[/yellow]"
                        )
                        continue
                    media_type = str(media.get("media_type") or "")
                    media_count += 1
                    if media_type == "video":
                        video_count += 1
                    elif media_type == "animated_gif":
                        gif_count += 1
                    else:
                        image_count += 1
                    mime_type = _media_mime(absolute, media_type)
                    upload = tag_config.api_mode == "paid" or media_type in {
                        "video",
                        "animated_gif",
                    }
                    if upload:
                        remote = await asyncio.to_thread(client.files.upload, file=str(absolute))
                        uploaded_files.append(remote)
                        free_index = len(free_parts) if tag_config.api_mode == "free" else None
                        interaction_index = (
                            len(interaction_input) if tag_config.api_mode == "paid" else None
                        )
                        if free_index is not None:
                            free_parts.append(remote)
                        if interaction_index is not None:
                            interaction_input.append({"type": "text", "text": "Processing media"})
                        pending_files.append(
                            (
                                remote,
                                tweet_id,
                                media_type,
                                mime_type,
                                free_index,
                                interaction_index,
                            )
                        )
                    else:
                        image = Image.open(str(absolute))
                        image.load()
                        opened_images.append(image)
                        free_parts.append(image)
                        active_media_by_tweet[tweet_id] += 1
            free_parts.append("\n")

        for (
            remote,
            tweet_id,
            media_type,
            mime_type,
            free_index,
            interaction_index,
        ) in pending_files:
            try:
                active = await _wait_for_remote_file(client, remote)
            except Exception as error:
                console.print(
                    f"[red]Failed while processing Gemini file {remote.name}: "
                    f"{_safe_error(error, tag_config.api_key)}[/red]"
                )
                if free_index is not None:
                    free_parts[free_index] = None
                if interaction_index is not None:
                    interaction_input[interaction_index] = None
                continue
            active_media_by_tweet[tweet_id] += 1
            if free_index is not None:
                free_parts[free_index] = active
            if interaction_index is not None:
                kind = "video" if media_type in {"video", "animated_gif"} else "image"
                interaction_input[interaction_index] = {
                    "type": kind,
                    "uri": getattr(active, "uri", None) or getattr(remote, "uri", None),
                    "mime_type": getattr(active, "mime_type", None) or mime_type,
                }

        free_parts = [part for part in free_parts if part is not None]
        interaction_input = [part for part in interaction_input if part is not None]
        if not tweet_contexts:
            console.print("[yellow]No stored tweet objects found for the selected IDs.[/yellow]")
            return 0
        if content_type == "media" and not active_media_by_tweet:
            console.print("[yellow]No loadable media found for the selected tweets.[/yellow]")
            if not dry_run and tag_config.api_mode == "paid":
                _defer_tagging_failure(store, tweet_ids)
            return 0

        console.print(
            Text(
                f"Generating {content_type} tags for {len(tweet_ids)} tweets using {model_name}..."
            )
        )
        request_metadata = {
            "tweet_count": len(tweet_ids),
            "media_count": media_count,
            "image_count": image_count,
            "video_count": video_count,
            "gif_count": gif_count,
            "input_text_chars": sum(
                len(str(part.get("text") or ""))
                for part in interaction_input
                if isinstance(part, dict) and part.get("type") == "text"
            ),
            "existing_tag_count": len(existing_tags),
            "tagging_hint_count": len(tag_config.tagging_context),
            "additional_instructions_present": bool(tag_config.additional_instructions),
        }

        response: Any | None = None
        if tag_config.api_mode == "free":
            result_schema = _FreeMediaTagResult if content_type == "media" else _FreeTextTagResult
            config_args: dict[str, Any] = {
                "http_options": types.HttpOptions(
                    timeout=900_000,
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
                "response_mime_type": "application/json",
                "max_output_tokens": 8192,
                "response_schema": list[result_schema],
            }
            thinking_config = _thinking_config(tag_config.thinking_level)
            if thinking_config is not None:
                config_args["thinking_config"] = thinking_config
            generation_config = types.GenerateContentConfig(**config_args)
            for attempt in range(5):
                attempt_number = attempt + 1
                request_identifier = logical_request_id
                try:
                    reservation = reserve_rpd_request(
                        store,
                        model=model_name,
                        limit=tag_config.free_rpd,
                    )
                except Exception as error:
                    _print_rpd_storage_error(console, error, tag_config.api_key)
                    return 0
                if not reservation.allowed:
                    _print_rpd_exhausted(console, reservation, model_name)
                    return 0
                await _wait_for_free_rpm(model_name, tag_config.free_rpm)
                started = time.monotonic()
                try:
                    response = await asyncio.to_thread(
                        client.models.generate_content,
                        model=model_name,
                        contents=free_parts,
                        config=generation_config,
                    )
                    latency_ms = round((time.monotonic() - started) * 1000)
                    try:
                        ledger.append(
                            usage_from_generate_content(
                                response,
                                run_id=run_identifier,
                                request_id=request_identifier,
                                requested_model=model_name,
                                content_type=content_type,
                                tweet_ids=tweet_ids,
                                latency_ms=latency_ms,
                                attempt=attempt_number,
                                thinking_level=tag_config.thinking_level,
                                request_metadata=request_metadata,
                                reserved_cost_usd="0",
                            )
                        )
                    except Exception as error:
                        console.print(
                            "[yellow]Gemini Free usage could not be written to the AI ledger: "
                            f"{_safe_error(error, tag_config.api_key)}[/yellow]"
                        )
                    break
                except Exception as error:
                    latency_ms = round((time.monotonic() - started) * 1000)
                    try:
                        ledger.append(
                            failed_usage_record(
                                run_id=run_identifier,
                                request_id=request_identifier,
                                api_mode="free",
                                model=model_name,
                                service_tier="free",
                                content_type=content_type,
                                tweet_ids=tweet_ids,
                                latency_ms=latency_ms,
                                search_enabled=False,
                                attempt=attempt_number,
                                thinking_level=tag_config.thinking_level,
                                request_metadata=request_metadata,
                                reserved_cost_usd="0",
                            )
                        )
                    except Exception:
                        pass
                    error_text = str(error).upper()
                    if (
                        any(marker in error_text for marker in ("429", "503", "RESOURCE_EXHAUSTED"))
                        and attempt < 4
                    ):
                        delay = 15 * (2**attempt)
                        console.print(
                            f"[yellow]Gemini API busy. Retrying in {delay} seconds "
                            f"(Attempt {attempt + 1}/5)...[/yellow]"
                        )
                        await asyncio.sleep(delay)
                        continue
                    if any(marker in error_text for marker in ("400", "INVALID_ARGUMENT")):
                        split = await split_free_batch("Gemini rejected the payload")
                        if split or len(tweet_ids) > 1:
                            return split
                        if not dry_run:
                            _mark_failed(store, tweet_ids)
                        return 0
                    console.print(
                        "[red]Gemini Tagging Failed: "
                        f"{_safe_error(error, tag_config.api_key)}[/red]"
                    )
                    return 0
        else:
            result_schema = MediaTagResult if content_type == "media" else TextTagResult
            daily_limit = (
                None
                if tag_config.unlimited_spend or tag_config.daily_spend_limit_usd is None
                else Decimal(str(tag_config.daily_spend_limit_usd))
            )

            def pause(reason: str) -> None:
                paid_state.stop_reason = reason
                if _paid_stop is not None:
                    _paid_stop[0] = True
                console.print(Text(f"Paid tagging paused: {reason}.", style="yellow"))

            def append_usage(record: Any) -> None:
                try:
                    ledger.append(record)
                except Exception:
                    pause("the usage ledger could not be saved")
                    raise

            for attempt in range(5):
                attempt_number = attempt + 1
                started = time.monotonic()
                search_reservation = None
                attempt_use_search = False
                provider_started = False
                pricing = None
                preflight_input_tokens = None
                reserved_cost = Decimal("0")
                search_settled = False
                phase = "preflight"
                try:
                    budget = ledger.daily_budget_usage()
                    if daily_limit is not None:
                        reason = _budget_pause(tag_config, budget)
                        if reason:
                            pause(reason)
                            return 0
                        if budget["spend"] + budget["reserved"] >= daily_limit:
                            pause(
                                "daily estimated spend and uncertainty reserves reached the limit"
                            )
                            return 0
                    if tag_config.google_search:
                        search_reservation, reason = await search_policy.reserve_attempt()
                        attempt_use_search = search_reservation is not None
                        if reason:
                            console.print(
                                Text(
                                    f"Google Search disabled for this attempt: {reason}.",
                                    style="yellow",
                                )
                            )
                    attempt_prompt = make_system_prompt(attempt_use_search)
                    pricing = await asyncio.to_thread(
                        load_openrouter_pricing, paths.ai_usage_dir, model_name
                    )
                    preflight_input_tokens, _ = await _count_request_tokens(
                        client,
                        model=model_name,
                        system_prompt=attempt_prompt,
                        interaction_input=interaction_input,
                        search_enabled=attempt_use_search,
                        console=console,
                        api_key=tag_config.api_key,
                    )
                    # This is an explicit local uncertainty allowance, not a
                    # claimed provider billing cap. Never estimate old unknown
                    # records retroactively, or turn their actual cost into zero.
                    reserved_cost = Decimal(str(tag_config.unknown_request_reserve_usd))
                    if pricing is not None and preflight_input_tokens is not None:
                        token_estimate = minimum_input_cost_usd(
                            pricing,
                            preflight_input_tokens,
                            multiplier=pricing_multiplier(tag_config.processing_tier),
                        ) + Decimal(2048) * max(
                            pricing.output_per_token, pricing.thinking_per_token
                        ) * pricing_multiplier(tag_config.processing_tier)
                        reserved_cost = max(reserved_cost, token_estimate)
                    if daily_limit is not None:
                        if pricing is None:
                            pause("current pricing is unavailable")
                            return 0
                        if budget["spend"] + budget["reserved"] + reserved_cost > daily_limit:
                            pause("the next request's cost allowance would exceed the daily budget")
                            return 0
                    kwargs = {
                        "model": model_name,
                        "input": interaction_input,
                        "store": False,
                        "system_instruction": attempt_prompt,
                        "response_format": {
                            "type": "text",
                            "mime_type": "application/json",
                            "schema": result_schema.model_json_schema(),
                        },
                        "generation_config": {
                            "max_output_tokens": 2048,
                            "thinking_level": _interaction_thinking_level(
                                tag_config.thinking_level
                            ),
                        },
                        "service_tier": tag_config.processing_tier,
                    }
                    if tag_config.processing_tier == "flex":
                        kwargs["timeout"] = 900.0
                    if attempt_use_search:
                        kwargs["tools"] = [{"type": "google_search"}]
                    phase = "interaction"
                    provider_started = True
                    paid_state.attempts += 1
                    response = await _create_interaction(client, **kwargs)
                    usage_record, search = usage_from_interaction(
                        response,
                        run_id=run_identifier,
                        request_id=logical_request_id,
                        requested_model=model_name,
                        service_tier=tag_config.processing_tier,
                        content_type=content_type,
                        tweet_ids=tweet_ids,
                        search_enabled=attempt_use_search,
                        latency_ms=round((time.monotonic() - started) * 1000),
                        search_reservation_consumed=(
                            search_reservation.amount
                            if search_reservation is not None
                            and not interaction_search_accounting(
                                response, search_enabled=attempt_use_search
                            ).reliable
                            else None
                        ),
                        attempt=attempt_number,
                        thinking_level=tag_config.thinking_level,
                        request_metadata=request_metadata,
                        estimate={"input_tokens": preflight_input_tokens},
                        reserved_cost_usd=str(reserved_cost),
                        pricing=pricing,
                        pricing_multiplier=pricing_multiplier(tag_config.processing_tier),
                        preflight_input_tokens=preflight_input_tokens,
                    )
                    append_usage(usage_record)
                    if search.reliable:
                        await search_policy.commit(search_reservation, search)
                    else:
                        disabled = await search_policy.mark_unknown(search_reservation)
                        console.print(
                            Text(
                                "Search usage is unknown; retained an estimated five-query reserve."
                                + (
                                    " Search disabled for the remainder of this run."
                                    if disabled
                                    else ""
                                ),
                                style="yellow",
                            )
                        )
                    search_settled = True
                    if usage_record.status == "billing_unknown":
                        console.print(
                            Text(
                                f"Billing usage missing for tweet {tweet_ids[0]}, "
                                f"attempt {attempt_number}; "
                                f"retained ${reserved_cost} as an estimated uncertainty reserve.",
                                style="yellow",
                            )
                        )
                        if daily_limit is not None:
                            reason = _budget_pause(tag_config, ledger.daily_budget_usage())
                            if reason:
                                pause(reason)
                    break  # Keep useful output even if its billing is unknown.
                except Exception as error:
                    code = _provider_error_code(error)
                    message = _safe_error(error, tag_config.api_key)
                    console.print(
                        Text(
                            f"Gemini {phase} failed for tweet {tweet_ids[0]}, "
                            f"attempt {attempt_number}/5: "
                            f"{type(error).__name__} (HTTP {code or 'unknown'}): {message}",
                            style="yellow",
                        )
                    )
                    if paid_state.stop_reason == "the usage ledger could not be saved":
                        return 0
                    failure = (
                        getattr(error, "interaction", None)
                        or getattr(error, "response", None)
                        or getattr(error, "result", None)
                    )
                    if callable(getattr(failure, "json", None)):
                        try:
                            body = failure.json()
                            if isinstance(body, dict) and ("usage" in body or "id" in body):
                                failure = body
                        except (ValueError, TypeError):
                            pass
                    usage = (
                        failure.get("usage")
                        if isinstance(failure, dict)
                        else getattr(failure, "usage", None)
                    )
                    has_usage = _has_usable_interaction_usage(usage)
                    # Explicit validation/auth/rate rejections with no evidence
                    # of generation are distinct from timeouts/5xx ambiguity.
                    interaction_id = (
                        failure.get("id")
                        if isinstance(failure, dict)
                        else getattr(failure, "id", None)
                    )
                    rejected = code in {400, 401, 403, 404, 422, 429} and not (
                        usage is not None or interaction_id
                    )
                    billing_unknown = provider_started and not has_usage and not rejected
                    search = (
                        interaction_search_accounting(failure, search_enabled=attempt_use_search)
                        if provider_started and not rejected
                        else SearchAccounting(0, "disabled")
                    )
                    common = dict(
                        run_id=run_identifier,
                        request_id=logical_request_id,
                        service_tier=tag_config.processing_tier,
                        content_type=content_type,
                        tweet_ids=tweet_ids,
                        search_enabled=attempt_use_search,
                        latency_ms=round((time.monotonic() - started) * 1000),
                        search_reservation_consumed=(
                            search_reservation.amount
                            if search_reservation is not None and not search.reliable
                            else None
                        ),
                        attempt=attempt_number,
                        thinking_level=tag_config.thinking_level,
                        request_metadata=request_metadata,
                        estimate={"input_tokens": preflight_input_tokens},
                        reserved_cost_usd=str(reserved_cost),
                        pricing=pricing,
                        pricing_multiplier=pricing_multiplier(tag_config.processing_tier),
                        preflight_input_tokens=preflight_input_tokens,
                    )
                    if has_usage:
                        record, _ = usage_from_interaction(
                            failure, requested_model=model_name, **common
                        )
                        record = replace(record, status="failed")
                    else:
                        record = failed_usage_record(
                            api_mode="paid",
                            model=model_name,
                            search=search,
                            status="billing_unknown" if billing_unknown else "failed",
                            **common,
                        )
                        if not billing_unknown:
                            record = replace(record, cost_usd="0", estimated_cost_usd="0")
                    record = replace(
                        record,
                        error_type=type(error).__name__,
                        error_code=code,
                        error_message=message,
                        failure_phase=phase,
                    )
                    append_usage(record)
                    if not dry_run:
                        _defer_tagging_failure(store, tweet_ids)
                    if search.reliable:
                        await search_policy.commit(search_reservation, search)
                    else:
                        disabled = await search_policy.mark_unknown(search_reservation)
                        console.print(
                            Text(
                                "Search usage is unknown; retained an estimated five-query reserve."
                                + (
                                    " Search disabled for the remainder of this run."
                                    if disabled
                                    else ""
                                ),
                                style="yellow",
                            )
                        )
                    search_settled = True
                    if code in {401, 403}:
                        pause("Gemini rejected authentication or access")
                        return 0
                    if billing_unknown and daily_limit is not None:
                        reason = _budget_pause(tag_config, ledger.daily_budget_usage())
                        if reason:
                            pause(reason)
                            return 0
                    retryable = code in {429, 500, 502, 503, 504} or (
                        code is None
                        and any(
                            marker in str(error).upper()
                            for marker in (
                                "429",
                                "503",
                                "RESOURCE_EXHAUSTED",
                                "TIMEOUT",
                                "TIMED OUT",
                                "UNAVAILABLE",
                                "DEADLINE",
                            )
                        )
                    )
                    if retryable and attempt < 4:
                        delay = 15 * (2**attempt)
                        console.print(
                            Text(
                                f"Gemini API busy. Retrying in {delay} seconds "
                                f"(Attempt {attempt + 1}/5)...",
                                style="yellow",
                            )
                        )
                        await asyncio.sleep(delay)
                        continue
                    return 0
                finally:
                    # Also release reservations on a pricing/budget early return.
                    if not search_settled:
                        await search_policy.commit(search_reservation, None)

        if response is None:
            return 0
        response_text = (
            getattr(response, "text", None)
            if tag_config.api_mode == "free"
            else getattr(response, "output_text", None)
        )
        if not response_text:
            if tag_config.api_mode == "free" and len(tweet_ids) > 1:
                return await split_free_batch("Gemini returned an empty response")
            console.print("[red]Gemini returned an empty response.[/red]")
            if not dry_run:
                if tag_config.api_mode == "paid":
                    _defer_tagging_failure(store, tweet_ids)
                else:
                    _mark_failed(store, tweet_ids)
            return 0

        parsed: list[tuple[str, MediaTagResult | TextTagResult]] = []
        if tag_config.api_mode == "paid":
            result_model = MediaTagResult if content_type == "media" else TextTagResult
            try:
                parsed.append((tweet_ids[0], result_model.model_validate_json(response_text)))
            except (ValidationError, ValueError, TypeError) as error:
                console.print(
                    "[red]Gemini returned invalid structured output: "
                    f"{_safe_error(error, tag_config.api_key)}[/red]"
                )
                if not dry_run:
                    _defer_tagging_failure(store, tweet_ids)
                return 0
        else:
            free_model = _FreeMediaTagResult if content_type == "media" else _FreeTextTagResult
            try:
                raw_results = json.loads(response_text)
                if not isinstance(raw_results, list):
                    raise ValueError("top-level response must be a list")
            except (json.JSONDecodeError, TypeError, ValueError):
                if len(tweet_ids) > 1:
                    return await split_free_batch("Gemini returned invalid structured output")
                console.print("[red]Gemini returned invalid structured output.[/red]")
                return 0
            for raw in raw_results:
                try:
                    result = free_model.model_validate(raw)
                    parsed.append((result.id, result))
                except (ValidationError, TypeError, ValueError):
                    console.print(
                        "[yellow]Skipping a Gemini result that did not match the batch schema."
                        "[/yellow]"
                    )

        eligible = set(tweet_contexts)
        if content_type == "media":
            eligible &= {tweet_id for tweet_id, count in active_media_by_tweet.items() if count}
        tagged_count = 0
        for tweet_id, result in parsed:
            if tweet_id not in eligible:
                continue
            normalized_tags = _normalized_tags(result.tags, existing_tags)
            if not 2 <= len(normalized_tags) <= 5:
                if not dry_run and tag_config.api_mode == "paid":
                    _defer_tagging_failure(store, [tweet_id])
                continue
            description = getattr(result, "description", None)
            if dry_run:
                context = tweet_contexts[tweet_id]
                if preview_results is not None:
                    preview_results.append(
                        TaggingPreview(
                            id=tweet_id,
                            content_type=content_type,
                            model=model_name,
                            tweet_type=context.tweet_type,
                            author_display_name=context.author_display_name,
                            author_username=context.author_username,
                            text=context.text,
                            description=description,
                            tags=normalized_tags,
                        )
                    )
                _print_tag_preview(
                    console,
                    context,
                    description=description,
                    tags=normalized_tags,
                )
                tagged_count += 1
                continue
            payload: dict[str, Any] = {"tags": normalized_tags}
            if description:
                payload["description"] = description
            store.conn.execute(
                "INSERT OR REPLACE INTO archive "
                "(row_key, record_type, tweet_id, raw_json, enrichment_state, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    f"media_tag:{tweet_id}",
                    "media_tag",
                    tweet_id,
                    json.dumps(payload),
                    "done",
                    str(time.time()),
                ),
            )
            store.conn.execute(
                "DELETE FROM archive WHERE row_key = ?", (f"tagging_retry:{tweet_id}",)
            )
            tagged_count += 1

        if dry_run:
            console.print(
                f"[green]Generated tags for {tagged_count} tweet"
                f"{'s' if tagged_count != 1 else ''} in test mode; no tags were saved.[/green]"
            )
        else:
            store.conn.commit()
            console.print(f"[green]Successfully tagged {tagged_count} tweets![/green]")
        return tagged_count
    except Exception as error:
        try:
            store.conn.rollback()
        except Exception:
            pass
        console.print(f"[red]Gemini Tagging Failed: {_safe_error(error, tag_config.api_key)}[/red]")
        return 0
    finally:
        for image in opened_images:
            try:
                image.close()
            except Exception:
                pass
        for remote in uploaded_files:
            try:
                await asyncio.to_thread(client.files.delete, name=remote.name)
            except Exception:
                pass


async def tag_pending_media_tweets(
    store: ArchiveStore,
    config: AppConfig,
    paths: XDGPaths,
    console: Console,
    *,
    batch_limit: int | None = None,
    batch_size: int | None = None,
    model_override: str | None = None,
    dry_run: bool = False,
) -> TaggingRunResult:
    """Run homogeneous Free batches or one-request Paid waves."""
    if batch_limit is not None and batch_limit < 1:
        raise ValueError("Tagging request limit must be a positive integer")
    if batch_size is not None and batch_size < 1:
        raise ValueError("Free tagging batch override must be a positive integer")

    pipeline = current_pipeline()
    tag_config = config.tagging
    if not tag_config.enabled or not tag_config.api_key:
        if pipeline is not None and pipeline.has_step("tagging"):
            pipeline.skip_step(
                "tagging",
                "tagging being disabled in configuration"
                if not tag_config.enabled
                else "no Gemini API key being configured",
            )
        return TaggingRunResult()
    if not _ensure_optional_dependencies():
        if pipeline is not None and pipeline.has_step("tagging"):
            pipeline.skip_step("tagging", "the automated-tagging extra not being installed")
        else:
            console.print(
                "[yellow]Automated tagging is not installed; continuing without it.[/yellow]"
            )
        return TaggingRunResult()

    model_name = model_override or tag_config.model
    ledger = AIUsageLedger(paths.ai_usage_dir)
    run_identifier = _run_id()
    search_policy = _SearchPolicy(
        ledger,
        tag_config.search_safety_reserve,
        tag_config.api_mode == "paid" and tag_config.google_search,
        unknown_policy=tag_config.search_unknown_policy,
        max_unknown_attempts=tag_config.max_unknown_search_requests,
    )
    paid_state = _PaidRunState()
    max_requests = 1 if dry_run else batch_limit
    free_batch_size = min(batch_size or tag_config.free_batch_size, 20)
    processed = tagged = requests = sent_requests = 0
    attempted_ids: set[str] = set()
    rich_selector = hasattr(store, "get_eligible_tagging_candidates")

    if pipeline is not None:
        mode_detail = f"{model_name} · " + (
            f"Free · up to {free_batch_size} tweets/request · {tag_config.free_rpd} requests/day"
            if tag_config.api_mode == "free"
            else (f"Paid · {tag_config.processing_tier.title()} · sequential processing")
        )
        pipeline.add_step(
            "tagging",
            "Automated tags",
            total=max_requests
            or (free_batch_size if tag_config.api_mode == "free" else PAID_CONCURRENCY),
            unit="tweets",
            detail=mode_detail,
            show_rate=False,
            show_eta=False,
        )
        pipeline.start_step(
            "tagging",
            activity="Selecting tweets",
        )

    while max_requests is None or requests < max_requests:
        if tag_config.api_mode == "free":
            selection_limit = 1 if dry_run else free_batch_size
        else:
            remaining = PAID_CONCURRENCY
            if max_requests is not None:
                remaining = min(remaining, max_requests - requests)
            selection_limit = max(remaining, 1)
        content_type: Literal["media", "text"] = "media"
        candidates: list[dict[str, Any]] = []
        quote_targets: dict[str, str] = {}
        if rich_selector:
            candidates = store.get_eligible_tagging_candidates(
                limit=selection_limit,
                exclude_tweet_ids=attempted_ids,
            )
            tweet_ids = [candidate["tweet_id"] for candidate in candidates]
            if candidates:
                content_type = candidates[0]["content_type"]
                quote_targets = {
                    candidate["tweet_id"]: candidate["quoted_tweet_id"]
                    for candidate in candidates
                    if candidate.get("quoted_tweet_id")
                }
        else:
            tweet_ids = store.get_eligible_tweets_for_tagging(limit=selection_limit)
        if not tweet_ids:
            break

        if pipeline is not None:
            pipeline.update_step(
                "tagging",
                completed=processed,
                total=max(max_requests or processed + len(tweet_ids), 1),
                activity=(
                    f"Generating {content_type} tags for {len(tweet_ids)} tweet"
                    f"{'s' if len(tweet_ids) != 1 else ''}"
                ),
                counters=" · ".join(
                    part
                    for value, part in (
                        (tagged, f"{tagged} tagged"),
                        (sent_requests, f"{sent_requests} requests"),
                    )
                    if value
                ),
            )

        if tag_config.api_mode == "free":
            # Refresh after the previous request has committed its tags.  A
            # Free request owns one homogeneous batch, so every tweet in it
            # receives the same immutable vocabulary snapshot.
            existing_tags = tuple(_top_archive_tags(store))
            results = [
                await tag_media_tweets(
                    store=store,
                    config=config,
                    paths=paths,
                    console=console,
                    tweet_ids=tweet_ids,
                    model_override=model_override,
                    dry_run=dry_run,
                    content_type=content_type,
                    quoted_tweet_ids=quote_targets,
                    _search_policy=search_policy,
                    _ledger=ledger,
                    _run_identifier=run_identifier,
                    _existing_tags=existing_tags,
                )
            ]
            request_increment = 1
        else:
            # One snapshot per Paid concurrency wave.  Do not look up tags in
            # each task: completion order must not change the prompts within a
            # wave.
            existing_tags = tuple(_top_archive_tags(store))
            paid_stop = [False]
            attempts_before = paid_state.attempts

            async def tag_one(
                tweet_id: str,
                *,
                selected_content_type: Literal["media", "text"] = content_type,
                selected_quote_targets: dict[str, str] = quote_targets,
                selected_existing_tags: tuple[str, ...] = existing_tags,
                selected_paid_stop: list[bool] = paid_stop,
            ) -> int:
                return await tag_media_tweets(
                    store=store,
                    config=config,
                    paths=paths,
                    console=console,
                    tweet_ids=[tweet_id],
                    model_override=model_override,
                    dry_run=dry_run,
                    content_type=selected_content_type,
                    quoted_tweet_ids=(
                        {tweet_id: selected_quote_targets[tweet_id]}
                        if tweet_id in selected_quote_targets
                        else {}
                    ),
                    _search_policy=search_policy,
                    _ledger=ledger,
                    _run_identifier=run_identifier,
                    _existing_tags=selected_existing_tags,
                    _paid_stop=selected_paid_stop,
                    _paid_state=paid_state,
                )

            results = (
                [await tag_one(tweet_ids[0])]
                if len(tweet_ids) == 1
                else await asyncio.gather(*(tag_one(tweet_id) for tweet_id in tweet_ids))
            )
            request_increment = len(tweet_ids)

        if tag_config.api_mode == "paid":
            dispatched_ids = (
                tweet_ids if not paid_stop[0] or paid_state.attempts > attempts_before else []
            )
        else:
            dispatched_ids = tweet_ids
        processed += len(dispatched_ids)
        tagged += sum(results)
        requests += len(dispatched_ids) if tag_config.api_mode == "paid" else request_increment
        sent_requests = paid_state.attempts if tag_config.api_mode == "paid" else requests
        attempted_ids.update(dispatched_ids)
        if pipeline is not None:
            pipeline.update_step(
                "tagging",
                completed=processed,
                total=max(max_requests or processed, processed, 1),
                counters=" · ".join(
                    part
                    for value, part in (
                        (tagged, f"{tagged} tagged"),
                        (sent_requests, f"{sent_requests} requests"),
                    )
                    if value
                ),
                important=True,
            )
        if dry_run or paid_state.stop_reason:
            break

    if pipeline is not None:
        if paid_state.stop_reason:
            pipeline.update_step("tagging", completed=processed)
            pipeline.fail_step(
                "tagging",
                f"Paused: {paid_state.stop_reason}",
                metrics={"tagged": tagged, "requests": paid_state.attempts},
            )
        elif processed == 0:
            pipeline.skip_step("tagging", "no eligible tweets or available quota/spend")
        else:
            pipeline.complete_step(
                "tagging",
                f"{tagged} tagged · {sent_requests} requests",
                metrics={
                    "tagged": tagged,
                    "requests": sent_requests,
                },
            )
    return TaggingRunResult(processed=processed, tagged=tagged, batches=sent_requests)
