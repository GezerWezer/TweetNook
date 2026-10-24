"""FxTwitter API v2 lookups: cookie-free single-post checks.

FxTwitter (https://docs.fxembed.com) fetches posts from X through its own accounts, so
lookups here never touch the user's session or its rate limits. Responses are not X's
GraphQL; ``fx_status_to_graphql`` rebuilds the subset of a GraphQL tweet result that the
extractor and the Web UI read, so the existing persistence path can store them.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import aclosing
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import httpx

from tweetnook.client.timelines import TimelineTweet
from tweetnook.config import AppConfig
from tweetnook.extractor import extract_author_fields, extract_canonical_text

FxLookupKind = Literal["available", "tombstone", "not_found", "protected", "error"]

FX_USER_AGENT = "tweetnook (+https://github.com/gezerwezer/tweetnook)"
FX_RETRY_ATTEMPTS = 4
# This many failed lookups in a row (each already retried) means FxTwitter is down.
FX_OUTAGE_ERRORS = 8
# A Retry-After longer than this means FxTwitter is down for a while, not throttling.
FX_MAX_RETRY_AFTER_SECONDS = 60.0
FX_MEDIA_KEY_PREFIX = {"photo": "3_", "video": "13_", "gif": "16_"}
FX_MEDIA_TYPE = {"photo": "photo", "video": "video", "gif": "animated_gif"}
COMMUNITY_NOTE_SHORT_TITLE = "Readers added context"
COMMUNITY_NOTE_TITLE = "Readers added context they thought people might want to know"


@dataclass(slots=True)
class FxLookup:
    """One ``/2/status/{id}`` outcome.

    ``tombstone`` carries FxTwitter's reason (deleted, suspended, private, blocked,
    unavailable); ``not_found`` is a 404 without one; ``protected`` is a 401, which
    FxTwitter returns for every protected account, including ones the user follows.
    """

    tweet_id: str
    kind: FxLookupKind
    http_status: int | None
    status: dict[str, Any] | None = None
    reason: str | None = None
    detail: str | None = None
    payload: Any = None


def fx_enabled(config: AppConfig) -> bool:
    # Replay installs must never reach a host other than their replay source.
    return config.fxtwitter.enabled and not config.replay.url


def build_fx_client(
    config: AppConfig,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=config.fxtwitter.base_url.rstrip("/"),
        headers={"user-agent": FX_USER_AGENT, "accept": "application/json"},
        timeout=config.fxtwitter.timeout,
        follow_redirects=True,
        transport=transport,
    )


def _retry_after(response: httpx.Response, attempt: int) -> float:
    header = response.headers.get("retry-after")
    try:
        delay = max(float(header), 1.0) if header else float(2**attempt)
    except ValueError:
        delay = float(2**attempt)
    return min(delay, FX_MAX_RETRY_AFTER_SECONDS)


def classify_fx_response(tweet_id: str, http_status: int, payload: Any) -> FxLookup:
    status = payload.get("status") if isinstance(payload, dict) else None
    status = status if isinstance(status, dict) else None
    status_type = status.get("type") if status else None
    if http_status == 200 and status_type == "status" and status.get("id") == tweet_id:
        return FxLookup(tweet_id, "available", http_status, status=status, payload=payload)
    if status_type == "tombstone":
        reason = str(status.get("reason") or "unavailable")
        return FxLookup(
            tweet_id,
            "tombstone",
            http_status,
            status=status,
            reason=reason,
            detail=status.get("message") or f"FxTwitter tombstone: {reason}",
            payload=payload,
        )
    message = payload.get("message") if isinstance(payload, dict) else None
    if http_status == 404:
        return FxLookup(tweet_id, "not_found", http_status, detail=message, payload=payload)
    if http_status == 401:
        return FxLookup(tweet_id, "protected", http_status, detail=message, payload=payload)
    return FxLookup(
        tweet_id,
        "error",
        http_status,
        detail=message or f"FxTwitter returned HTTP {http_status}",
        payload=payload,
    )


async def fetch_fx_status(
    client: httpx.AsyncClient,
    tweet_id: str,
    *,
    attempts: int = FX_RETRY_ATTEMPTS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> FxLookup:
    """Look up one post, retrying rate limits, server errors, and transport failures."""

    last: FxLookup | None = None
    for attempt in range(attempts):
        try:
            response = await client.get(f"/2/status/{tweet_id}")
        except httpx.TransportError as exc:
            last = FxLookup(tweet_id, "error", None, detail=f"{exc.__class__.__name__}: {exc}")
            if attempt + 1 < attempts:
                await sleep(float(2**attempt))
            continue
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if response.status_code == 429 or response.status_code >= 500:
            last = classify_fx_response(tweet_id, response.status_code, payload)
            if attempt + 1 < attempts:
                await sleep(_retry_after(response, attempt))
            continue
        return classify_fx_response(tweet_id, response.status_code, payload)
    assert last is not None
    return last


async def iter_fx_statuses(
    client: httpx.AsyncClient,
    tweet_ids: Iterable[str],
    *,
    concurrency: int,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> AsyncIterator[FxLookup]:
    """Yield lookups as each finishes, with at most ``concurrency`` requests in flight."""

    semaphore = asyncio.Semaphore(max(concurrency, 1))

    async def one(tweet_id: str) -> FxLookup:
        async with semaphore:
            return await fetch_fx_status(client, tweet_id, sleep=sleep)

    tasks = [asyncio.ensure_future(one(tweet_id)) for tweet_id in dict.fromkeys(tweet_ids)]
    try:
        for finished in asyncio.as_completed(tasks):
            yield await finished
    finally:
        for task in tasks:
            task.cancel()


async def fetch_fx_statuses(
    client: httpx.AsyncClient,
    tweet_ids: Iterable[str],
    *,
    concurrency: int,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    on_lookup: Callable[[FxLookup], None] | None = None,
) -> tuple[dict[str, FxLookup], bool]:
    """Look up several posts with at most ``concurrency`` requests in flight.

    Returns the lookups and whether FxTwitter looked down: after ``FX_OUTAGE_ERRORS``
    failures in a row the rest are dropped rather than waited out. ``on_lookup`` sees
    each lookup as it finishes, so callers can show progress during the batch.
    """

    results: dict[str, FxLookup] = {}
    errors = 0
    lookups = iter_fx_statuses(client, tweet_ids, concurrency=concurrency, sleep=sleep)
    async with aclosing(lookups):
        async for lookup in lookups:
            results[lookup.tweet_id] = lookup
            if on_lookup is not None:
                on_lookup(lookup)
            errors = errors + 1 if lookup.kind == "error" else 0
            if errors >= FX_OUTAGE_ERRORS:
                return results, True
    return results, False


# --- FxTwitter status -> GraphQL tweet result --------------------------------


def _x_date(timestamp: Any) -> str | None:
    if not isinstance(timestamp, int | float):
        return None
    return datetime.fromtimestamp(timestamp, UTC).strftime("%a %b %d %H:%M:%S +0000 %Y")


def _user_result(author: dict[str, Any]) -> dict[str, Any]:
    verification = author.get("verification") or {}
    raw_description = author.get("raw_description")
    description = (
        raw_description.get("text")
        if isinstance(raw_description, dict)
        else author.get("description")
    )
    avatar = author.get("avatar_url") or ""
    legacy = {
        "screen_name": author.get("screen_name"),
        "name": author.get("name"),
        "description": description,
        "location": author.get("location"),
        "followers_count": author.get("followers"),
        "friends_count": author.get("following"),
        "favourites_count": author.get("likes"),
        "media_count": author.get("media_count"),
        "statuses_count": author.get("statuses"),
        "profile_banner_url": author.get("banner_url"),
        "profile_image_url_https": avatar.replace("_200x200", "_normal") or None,
        "created_at": author.get("joined"),
        "protected": author.get("protected"),
    }
    return {
        "__typename": "User",
        "rest_id": author.get("id"),
        "is_blue_verified": bool(verification.get("verified")),
        "core": {
            "screen_name": author.get("screen_name"),
            "name": author.get("name"),
            "created_at": author.get("joined"),
        },
        "avatar": {"image_url": legacy["profile_image_url_https"]},
        "legacy": {key: value for key, value in legacy.items() if value is not None},
    }


def _media_entity(item: dict[str, Any], facet: dict[str, Any] | None) -> dict[str, Any] | None:
    media_type = item.get("type")
    if media_type not in FX_MEDIA_TYPE or not item.get("id"):
        return None
    if media_type == "photo":
        media_url = str(item.get("url") or "").removesuffix("?name=orig")
    else:
        media_url = item.get("thumbnail_url")
    entity: dict[str, Any] = {
        "id_str": str(item["id"]),
        "media_key": FX_MEDIA_KEY_PREFIX[media_type] + str(item["id"]),
        "type": FX_MEDIA_TYPE[media_type],
        "media_url_https": media_url,
        "original_info": {"width": item.get("width"), "height": item.get("height")},
    }
    if facet:
        entity.update(
            url=facet.get("original"),
            display_url=facet.get("display"),
            expanded_url=facet.get("replacement"),
            indices=facet.get("indices"),
        )
    if item.get("altText"):
        entity["ext_alt_text"] = item["altText"]
    if media_type != "photo":
        variants = []
        for fmt in item.get("formats") or item.get("variants") or []:
            if not isinstance(fmt, dict) or not fmt.get("url"):
                continue
            if fmt.get("container") == "m3u8":
                variants.append({"content_type": "application/x-mpegURL", "url": fmt["url"]})
            else:
                variants.append(
                    {
                        "bitrate": fmt.get("bitrate"),
                        "content_type": fmt.get("format") or "video/mp4",
                        "url": fmt["url"],
                    }
                )
        video_info: dict[str, Any] = {"variants": variants}
        if media_type == "video" and isinstance(item.get("duration"), int | float):
            video_info["duration_millis"] = int(item["duration"] * 1000)
        entity["video_info"] = video_info
    return entity


def community_note_pivot(note: dict[str, Any]) -> dict[str, Any] | None:
    text = note.get("text")
    if not text:
        return None
    entities = [
        {
            "fromIndex": facet["indices"][0],
            "toIndex": facet["indices"][1],
            "ref": {"urlType": "ExternalUrl", "url": facet.get("replacement")},
        }
        for facet in note.get("facets") or []
        if isinstance(facet, dict)
        and facet.get("type") == "url"
        and facet.get("replacement")
        and len(facet.get("indices") or []) == 2
    ]
    return {
        "shorttitle": COMMUNITY_NOTE_SHORT_TITLE,
        "title": COMMUNITY_NOTE_TITLE,
        "subtitle": {"text": text, "entities": entities},
    }


def fx_status_to_graphql(status: dict[str, Any]) -> dict[str, Any]:
    """Rebuild the GraphQL tweet-result fields the extractor and Web UI read.

    FxTwitter has no conversation id, edit history, client source, or viewer state
    (favorited/bookmarked), so those stay absent; the original FxTwitter JSON is kept
    under ``fxtwitter`` and in the raw capture.
    """

    raw_text = status.get("raw_text") or {}
    facets = [facet for facet in raw_text.get("facets") or [] if isinstance(facet, dict)]
    media_facets = {facet.get("id"): facet for facet in facets if facet.get("type") == "media"}
    media = [
        entity
        for item in ((status.get("media") or {}).get("all") or [])
        if isinstance(item, dict)
        and (entity := _media_entity(item, media_facets.get(item.get("id"))))
    ]
    entities: dict[str, Any] = {
        "urls": [
            {
                "url": facet.get("original"),
                "expanded_url": facet.get("replacement"),
                "display_url": facet.get("display"),
                "indices": facet.get("indices"),
            }
            for facet in facets
            if facet.get("type") == "url"
        ],
        "user_mentions": [
            {
                "screen_name": str(facet.get("display") or "").lstrip("@"),
                "id_str": facet.get("id"),
                "indices": facet.get("indices"),
            }
            for facet in facets
            if facet.get("type") == "mention"
        ],
        "hashtags": [
            {"text": str(facet.get("display") or "").lstrip("#"), "indices": facet.get("indices")}
            for facet in facets
            if facet.get("type") == "hashtag"
        ],
        "symbols": [],
    }
    if media:
        entities["media"] = media
    author = status.get("author") or {}
    reply = status.get("replying_to") or {}
    quote = status.get("quote") if isinstance(status.get("quote"), dict) else None
    legacy: dict[str, Any] = {
        "id_str": status.get("id"),
        "full_text": raw_text.get("text", status.get("text") or ""),
        "display_text_range": raw_text.get("display_text_range"),
        "created_at": _x_date(status.get("created_timestamp")) or status.get("created_at"),
        "lang": status.get("lang"),
        "favorite_count": status.get("likes"),
        "retweet_count": status.get("reposts"),
        "reply_count": status.get("replies"),
        "quote_count": status.get("quotes"),
        "bookmark_count": status.get("bookmarks"),
        "possibly_sensitive": status.get("possibly_sensitive"),
        "is_quote_status": quote is not None,
        "user_id_str": author.get("id"),
        "entities": entities,
    }
    if media:
        legacy["extended_entities"] = {"media": media}
    if reply.get("status"):
        legacy["in_reply_to_status_id_str"] = reply["status"]
        legacy["in_reply_to_screen_name"] = reply.get("screen_name")
    if quote is not None and quote.get("id"):
        legacy["quoted_status_id_str"] = quote["id"]
    result: dict[str, Any] = {
        "__typename": "Tweet",
        "rest_id": status.get("id"),
        "core": {"user_results": {"result": _user_result(author)}},
        "legacy": {key: value for key, value in legacy.items() if value is not None},
        "views": (
            {"count": str(status["views"]), "state": "EnabledWithCount"}
            if status.get("views") is not None
            else {"state": "Enabled"}
        ),
        "fxtwitter": {"source": "fxtwitter", "url": status.get("url")},
    }
    if status.get("is_note_tweet"):
        result["note_tweet"] = {
            "is_expandable": True,
            "note_tweet_results": {"result": {"text": legacy["full_text"]}},
        }
    if isinstance(status.get("community_note"), dict):
        pivot = community_note_pivot(status["community_note"])
        if pivot is not None:
            result["birdwatch_pivot"] = pivot
    if quote is not None and quote.get("type") == "status" and quote.get("id"):
        result["quoted_status_result"] = {"result": fx_status_to_graphql(quote)}
    return result


def fx_timeline_tweet(result: dict[str, Any]) -> TimelineTweet:
    """Wrap a rebuilt GraphQL result the way timeline parsing wraps a live one."""

    author_id, author_username, author_display_name = extract_author_fields(result)
    return TimelineTweet(
        tweet_id=str(result["rest_id"]),
        text=extract_canonical_text(result),
        author_id=author_id,
        author_username=author_username,
        author_display_name=author_display_name,
        created_at=(result.get("legacy") or {}).get("created_at"),
        sort_index=None,
        raw_json=result,
    )
