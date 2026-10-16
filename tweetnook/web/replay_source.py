"""Read-only archive lookups that answer replayed Twitter/X requests.

A replay source serves a test install the Twitter/X responses this archive
already saved: timeline pages chained by cursor, TweetDetail payloads, and
downloaded media (or tiny placeholders). The client sends each original
Twitter/X request as ``GET /api/dev/replay/x/{host}{path}?{query}`` with the
source's Web password. The archive is opened with SQLite's ``mode=ro`` and
never written.
"""

from __future__ import annotations

import copy
import json
import mimetypes
import sqlite3
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs

from tweetnook import fastjson
from tweetnook.client.timelines import parse_tweet_detail_tweets
from tweetnook.config import DB_FILENAME
from tweetnook.web.replay_placeholders import IMAGE_FORMATS, placeholder_bytes

# Wire contract shared with replaying clients (test installs).
REPLAY_PATH_PREFIX = "/api/dev/replay"
FAILURE_HEADER = "X-TweetNook-Replay-Fail"
MEDIA_HEADER = "X-TweetNook-Replay-Media"
RESULT_HEADER = "X-TweetNook-Replay"
PLACEHOLDER_MEDIA = "placeholder"

TIMELINE_OPERATIONS = ("Bookmarks", "Likes", "UserTweets")
DETAIL_OPERATIONS = ("ThreadExpandDetail", "TweetDetail", "TweetDetailResurrection")
MEDIA_HOST_SUFFIX = "twimg.com"
TIMELINE_TTL_SECONDS = 60.0
MEDIA_TTL_SECONDS = 300.0
DETAIL_TTL_SECONDS = 900.0
RATE_LIMIT_WINDOW_SECONDS = 60
_REMOVE = object()
_EMPTY_INSTRUCTIONS = [{"type": "TimelineAddEntries", "entries": []}]
_EMPTY_TIMELINES: dict[str, dict[str, Any]] = {
    "Bookmarks": {
        "data": {"bookmark_timeline_v2": {"timeline": {"instructions": _EMPTY_INSTRUCTIONS}}}
    },
    "Likes": {
        "data": {
            "user": {
                "result": {
                    "__typename": "User",
                    "timeline": {"timeline": {"instructions": _EMPTY_INSTRUCTIONS}},
                }
            }
        }
    },
    "UserTweets": {
        "data": {
            "user": {
                "result": {
                    "__typename": "User",
                    "timeline": {"timeline": {"instructions": _EMPTY_INSTRUCTIONS}},
                }
            }
        }
    },
}
DISCOVERY_PAGE = (
    b"<!doctype html><html><head><title>TweetNook replay</title></head><body></body></html>"
)


@dataclass(slots=True)
class ReplayResult:
    status_code: int
    outcome: str
    body: bytes = b""
    media_type: str = "application/json"
    headers: dict[str, str] = field(default_factory=dict)
    file: Path | None = None

    def response_headers(self) -> dict[str, str]:
        return {**self.headers, RESULT_HEADER: self.outcome}


@dataclass(slots=True)
class _Timeline:
    first_row_key: str | None
    by_cursor: dict[str, str]
    pages: int
    chain_pages: int
    latest_row_key: str | None


def _json_result(payload: Any, *, status_code: int = 200, outcome: str = "hit") -> ReplayResult:
    return ReplayResult(
        status_code=status_code,
        outcome=outcome,
        body=json.dumps(payload, separators=(",", ":")).encode(),
    )


def _is_entry(node: Any) -> bool:
    return isinstance(node, dict) and "entryId" in node and "content" in node


def _prune_entries(node: Any) -> Any:
    """Drop every timeline entry (tweets and cursors), keeping the response shape."""

    if _is_entry(node):
        return _REMOVE
    if isinstance(node, dict):
        pruned: dict[str, Any] = {}
        for key, value in node.items():
            new_value = _prune_entries(value)
            if new_value is _REMOVE:
                if key == "entry":
                    # TimelineReplaceEntry/TimelinePinEntry without its entry is meaningless.
                    return _REMOVE
                continue
            pruned[key] = new_value
        return pruned
    if isinstance(node, list):
        return [item for item in map(_prune_entries, node) if item is not _REMOVE]
    return node


def empty_timeline_page(operation: str, template: Any = None) -> dict[str, Any]:
    """A valid timeline page with no entries and no bottom cursor (end of timeline)."""

    if isinstance(template, dict):
        pruned = _prune_entries(copy.deepcopy(template))
        if isinstance(pruned, dict) and pruned.get("data"):
            return pruned
    return copy.deepcopy(_EMPTY_TIMELINES.get(operation, _EMPTY_TIMELINES["Bookmarks"]))


def unavailable_detail_page(tweet_id: str) -> dict[str, Any]:
    """TweetDetail shape X returns for a tweet that cannot be shown."""

    return {
        "data": {
            "threaded_conversation_with_injections_v2": {
                "instructions": [
                    {
                        "type": "TimelineAddEntries",
                        "entries": [
                            {
                                "entryId": f"tweet-{tweet_id}",
                                "sortIndex": tweet_id,
                                "content": {
                                    "entryType": "TimelineTimelineItem",
                                    "__typename": "TimelineTimelineItem",
                                    "itemContent": {
                                        "itemType": "TimelineTweet",
                                        "__typename": "TimelineTweet",
                                        "tweet_results": {
                                            "result": {
                                                "__typename": "TweetUnavailable",
                                                "reason": "Unavailable",
                                            }
                                        },
                                    },
                                },
                            }
                        ],
                    }
                ]
            }
        }
    }


def detail_page_for_tweet(tweet: dict[str, Any]) -> dict[str, Any]:
    tweet_id = str(tweet.get("rest_id"))
    page = unavailable_detail_page(tweet_id)
    entry = page["data"]["threaded_conversation_with_injections_v2"]["instructions"][0]
    entry["entries"][0]["content"]["itemContent"]["tweet_results"] = {"result": tweet}
    return page


def _status_code(value: Any) -> int:
    try:
        status = int(value)
    except (TypeError, ValueError):
        return 200
    return status if 100 <= status <= 599 else 200


def _url_extension(path: str, query: Mapping[str, list[str]]) -> str:
    name = PurePosixPath(path).name.split(":", 1)[0]
    suffix = PurePosixPath(name).suffix.lower().lstrip(".")
    if suffix:
        return suffix
    return (query.get("format") or [""])[0].lower()


def media_key(host: str, path: str, query: str = "") -> str:
    """Normalize a twimg URL so sizes/name variants of one asset match."""

    params = parse_qs(query)
    clean_path = path.split(":", 1)[0] if ":" in PurePosixPath(path).name else path
    if not PurePosixPath(clean_path).suffix:
        extension = _url_extension(clean_path, params)
        if extension:
            clean_path = f"{clean_path}.{extension}"
    return f"{host.lower()}{clean_path}"


def _media_key_from_url(url: Any) -> str | None:
    if not isinstance(url, str) or "://" not in url:
        return None
    rest = url.split("://", 1)[1]
    host, _, path_query = rest.partition("/")
    path, _, query = f"/{path_query}".partition("?")
    return media_key(host, path, query)


def placeholder_media_type(host: str, path: str, query: str) -> str:
    extension = _url_extension(path, parse_qs(query))
    if extension in IMAGE_FORMATS:
        return IMAGE_FORMATS[extension]
    if host.lower().startswith("video.") or extension in {"mp4", "m3u8", "m4s", "mov", "ts"}:
        return "video/mp4"
    return "image/jpeg"


class ReplayArchive:
    """Lazily indexed, read-only view of one archive database."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.db_path = data_dir / DB_FILENAME
        self._lock = threading.Lock()
        self._timelines: dict[str, tuple[float, _Timeline]] = {}
        self._empty_pages: dict[str, dict[str, Any]] = {}
        self._media: tuple[float, dict[str, tuple[str, str | None]]] | None = None
        self._detail_index: tuple[float, dict[str, str]] | None = None

    # -- database -----------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        if not self.db_path.is_file():
            raise FileNotFoundError(f"No archive database at {self.db_path}")
        connection = sqlite3.connect(
            f"{self.db_path.resolve().as_uri()}?mode=ro", uri=True, check_same_thread=False
        )
        connection.row_factory = sqlite3.Row
        return connection

    def _query(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        connection = self._connect()
        try:
            return connection.execute(sql, params).fetchall()
        finally:
            connection.close()

    def _capture(self, row_key: str) -> sqlite3.Row | None:
        rows = self._query(
            "SELECT raw_json, http_status FROM archive WHERE row_key = ?", (row_key,)
        )
        return rows[0] if rows else None

    # -- timelines ----------------------------------------------------------------

    def _timeline(self, operation: str) -> _Timeline:
        now = time.monotonic()
        with self._lock:
            cached = self._timelines.get(operation)
            if cached is not None and now - cached[0] < TIMELINE_TTL_SECONDS:
                return cached[1]
        rows = self._query(
            """
            SELECT row_key, cursor_in, cursor_out, captured_at
            FROM archive
            WHERE record_type = 'raw_capture' AND operation = ?
              AND CAST(http_status AS INTEGER) = 200
            ORDER BY captured_at ASC
            """,
            (operation,),
        )
        by_cursor: dict[str, str] = {}
        cursor_out: dict[str, str | None] = {}
        firsts: list[str] = []
        for row in rows:
            cursor_out[row["row_key"]] = row["cursor_out"]
            if row["cursor_in"]:
                by_cursor[row["cursor_in"]] = row["row_key"]
            else:
                firsts.append(row["row_key"])

        def chain_length(row_key: str) -> int:
            length = 1
            seen = {row_key}
            next_cursor = cursor_out.get(row_key)
            while next_cursor and (next_key := by_cursor.get(next_cursor)) and next_key not in seen:
                seen.add(next_key)
                length += 1
                next_cursor = cursor_out.get(next_key)
            return length

        best: tuple[int, int] | None = None
        first_row_key: str | None = None
        for position, row_key in enumerate(firsts):
            # Prefer the longest saved chain; among equals, the most recent first page.
            score = (chain_length(row_key), position)
            if best is None or score > best:
                best, first_row_key = score, row_key
        timeline = _Timeline(
            first_row_key=first_row_key,
            by_cursor=by_cursor,
            pages=len(rows),
            chain_pages=best[0] if best else 0,
            latest_row_key=rows[-1]["row_key"] if rows else None,
        )
        with self._lock:
            self._timelines[operation] = (now, timeline)
        return timeline

    def _empty_page(self, operation: str, timeline: _Timeline) -> dict[str, Any]:
        with self._lock:
            cached = self._empty_pages.get(operation)
        if cached is not None:
            return cached
        template = None
        if timeline.latest_row_key is not None:
            capture = self._capture(timeline.latest_row_key)
            if capture is not None and capture["raw_json"]:
                template = fastjson.loads(capture["raw_json"])
        page = empty_timeline_page(operation, template)
        with self._lock:
            self._empty_pages[operation] = page
        return page

    def timeline_page(self, operation: str, cursor: str | None) -> ReplayResult:
        timeline = self._timeline(operation)
        row_key = timeline.by_cursor.get(cursor) if cursor else timeline.first_row_key
        if row_key is not None and (capture := self._capture(row_key)) is not None:
            return ReplayResult(
                status_code=_status_code(capture["http_status"]),
                outcome="hit",
                body=str(capture["raw_json"]).encode(),
            )
        # End of the saved chain: an entry-less page ends the sync cleanly.
        return _json_result(self._empty_page(operation, timeline), outcome="empty")

    # -- tweet detail -------------------------------------------------------------

    def _detail_lookup(self) -> dict[str, str]:
        now = time.monotonic()
        with self._lock:
            if self._detail_index is not None and now - self._detail_index[0] < DETAIL_TTL_SECONDS:
                return self._detail_index[1]
        index: dict[str, str] = {}
        connection = self._connect()
        try:
            placeholders = ",".join("?" for _ in DETAIL_OPERATIONS)
            cursor = connection.execute(
                f"""
                SELECT row_key, raw_json FROM archive
                WHERE record_type = 'raw_capture' AND operation IN ({placeholders})
                  AND CAST(http_status AS INTEGER) = 200
                ORDER BY captured_at ASC
                """,
                DETAIL_OPERATIONS,
            )
            for row in cursor:
                try:
                    payload = fastjson.loads(row["raw_json"])
                except (TypeError, ValueError):
                    continue
                if not isinstance(payload, dict):
                    continue
                for tweet in parse_tweet_detail_tweets(payload):
                    index[tweet.tweet_id] = row["row_key"]
        finally:
            connection.close()
        with self._lock:
            self._detail_index = (now, index)
        return index

    def tweet_detail(self, tweet_id: str | None) -> ReplayResult:
        if not tweet_id:
            return _json_result(unavailable_detail_page(""), outcome="miss")
        placeholders = ",".join("?" for _ in DETAIL_OPERATIONS)
        rows = self._query(
            f"""
            SELECT raw_json, http_status FROM archive
            WHERE record_type = 'raw_capture' AND cursor_in = ?
              AND operation IN ({placeholders})
            ORDER BY captured_at DESC LIMIT 1
            """,
            (tweet_id, *DETAIL_OPERATIONS),
        )
        if rows and rows[0]["raw_json"]:
            return ReplayResult(
                status_code=_status_code(rows[0]["http_status"]),
                outcome="hit",
                body=str(rows[0]["raw_json"]).encode(),
            )
        row_key = self._detail_lookup().get(tweet_id)
        if row_key is not None and (capture := self._capture(row_key)) is not None:
            return ReplayResult(
                status_code=_status_code(capture["http_status"]),
                outcome="hit",
                body=str(capture["raw_json"]).encode(),
            )
        objects = self._query(
            "SELECT raw_json FROM archive WHERE record_type = 'tweet_object' AND tweet_id = ?",
            (tweet_id,),
        )
        for row in objects:
            try:
                tweet = fastjson.loads(row["raw_json"]) if row["raw_json"] else None
            except (TypeError, ValueError):
                continue
            if (
                isinstance(tweet, dict)
                and str(tweet.get("rest_id")) == tweet_id
                and ("legacy" in tweet or "core" in tweet)
            ):
                return _json_result(detail_page_for_tweet(tweet), outcome="hit")
        return _json_result(unavailable_detail_page(tweet_id), outcome="miss")

    # -- media --------------------------------------------------------------------

    def _media_index(self) -> dict[str, tuple[str, str | None]]:
        now = time.monotonic()
        with self._lock:
            if self._media is not None and now - self._media[0] < MEDIA_TTL_SECONDS:
                return self._media[1]
        index: dict[str, tuple[str, str | None]] = {}
        rows = self._query(
            """
            SELECT media_url, variants_json, local_path, content_type,
                   thumbnail_url, thumbnail_local_path, thumbnail_content_type
            FROM archive
            WHERE record_type = 'media'
              AND ((local_path IS NOT NULL AND local_path != '')
                OR (thumbnail_local_path IS NOT NULL AND thumbnail_local_path != ''))
            """
        )
        for row in rows:
            if row["thumbnail_local_path"] and (key := _media_key_from_url(row["thumbnail_url"])):
                index[key] = (row["thumbnail_local_path"], row["thumbnail_content_type"])
            if not row["local_path"]:
                continue
            if key := _media_key_from_url(row["media_url"]):
                index[key] = (row["local_path"], row["content_type"])
            try:
                variants = fastjson.loads(row["variants_json"]) if row["variants_json"] else []
            except (TypeError, ValueError):
                variants = []
            for variant in variants if isinstance(variants, list) else []:
                url = variant.get("url") if isinstance(variant, dict) else None
                if (key := _media_key_from_url(url)) and key not in index:
                    index[key] = (row["local_path"], row["content_type"])
        with self._lock:
            self._media = (now, index)
        return index

    def media(self, host: str, path: str, query: str, *, placeholder: bool) -> ReplayResult:
        if placeholder:
            media_type = placeholder_media_type(host, path, query)
            return ReplayResult(
                status_code=200,
                outcome="placeholder",
                body=placeholder_bytes(media_type),
                media_type=media_type,
            )
        entry = self._media_index().get(media_key(host, path, query))
        if entry is not None:
            data_dir = self.data_dir.resolve()
            target = (data_dir / entry[0]).resolve()
            if target.is_relative_to(data_dir) and target.is_file():
                media_type = (entry[1] or "").split(";", 1)[0].strip() or (
                    mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                )
                return ReplayResult(
                    status_code=200, outcome="hit", media_type=media_type, file=target
                )
        return miss_result(host, path, "media file not downloaded on the replay source")

    # -- status -------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        operations = (*TIMELINE_OPERATIONS, *DETAIL_OPERATIONS)
        placeholders = ",".join("?" for _ in operations)
        counts = {
            row["operation"]: row["count"]
            for row in self._query(
                f"""
                SELECT operation, COUNT(*) AS count FROM archive
                WHERE record_type = 'raw_capture' AND operation IN ({placeholders})
                  AND CAST(http_status AS INTEGER) = 200
                GROUP BY operation
                """,
                operations,
            )
        }
        media = self._query(
            "SELECT COUNT(*) AS count FROM archive WHERE record_type = 'media' "
            "AND local_path IS NOT NULL AND local_path != ''"
        )
        owner = self._query(
            "SELECT value FROM archive WHERE row_key = 'metadata:owner_user_id' LIMIT 1"
        )
        bookmarks = self._timeline("Bookmarks")
        likes = self._timeline("Likes")
        return {
            "bookmarks_pages": counts.get("Bookmarks", 0),
            "bookmarks_chain_pages": bookmarks.chain_pages,
            "likes_pages": counts.get("Likes", 0),
            "likes_chain_pages": likes.chain_pages,
            "thread_captures": sum(counts.get(operation, 0) for operation in DETAIL_OPERATIONS),
            "downloaded_media": media[0]["count"] if media else 0,
            "placeholder_media": True,
            "owner_user_id": owner[0]["value"] if owner else None,
        }


def miss_result(host: str, path: str, reason: str, **extra: Any) -> ReplayResult:
    return _json_result(
        {"replay": "miss", "host": host, "path": path, "reason": reason, **extra},
        status_code=404,
        outcome="miss",
    )


def fake_failure_result(keyword: str) -> ReplayResult | None:
    if keyword == "fail":
        return _json_result(
            {"errors": [{"code": 32, "message": "Could not authenticate you."}]},
            status_code=401,
            outcome="fake-fail",
        )
    if keyword == "ratelimit":
        result = _json_result(
            {"errors": [{"code": 88, "message": "Rate limit exceeded."}]},
            status_code=429,
            outcome="fake-ratelimit",
        )
        result.headers.update(
            {
                "x-rate-limit-limit": "500",
                "x-rate-limit-remaining": "0",
                "x-rate-limit-reset": str(int(time.time()) + RATE_LIMIT_WINDOW_SECONDS),
            }
        )
        return result
    return None


def _graphql_operation(path: str) -> str | None:
    parts = [part for part in path.split("/") if part]
    if len(parts) >= 5 and parts[:3] == ["i", "api", "graphql"]:
        return parts[4]
    return None


def _variables(query: str) -> dict[str, Any]:
    raw = (parse_qs(query).get("variables") or ["{}"])[0]
    try:
        variables = json.loads(raw)
    except ValueError:
        return {}
    return variables if isinstance(variables, dict) else {}


def replay_request(
    archive: ReplayArchive,
    *,
    host: str,
    path: str,
    query: str,
    headers: Mapping[str, str],
) -> ReplayResult:
    """Answer one replayed Twitter/X request from the archive."""

    host = host.lower()
    keyword = (headers.get(FAILURE_HEADER.lower()) or "").strip().lower()
    if failure := fake_failure_result(keyword):
        return failure

    if host.endswith(MEDIA_HOST_SUFFIX):
        if host == f"abs.{MEDIA_HOST_SUFFIX}" and path.startswith("/responsive-web/"):
            return miss_result(host, path, "client-web bundles are not replayed")
        placeholder = (headers.get(MEDIA_HEADER.lower()) or "").lower() == PLACEHOLDER_MEDIA
        return archive.media(host, path, query, placeholder=placeholder)

    if host in {"x.com", "twitter.com", "www.x.com", "www.twitter.com"} and path in {"", "/"}:
        # Query-ID discovery: a page without bundles makes the client keep its
        # built-in fallback query IDs.
        return ReplayResult(
            status_code=200, outcome="discovery", body=DISCOVERY_PAGE, media_type="text/html"
        )

    operation = _graphql_operation(path)
    if operation in {"Bookmarks", "Likes"}:
        cursor = _variables(query).get("cursor")
        return archive.timeline_page(operation, cursor if isinstance(cursor, str) else None)
    if operation == "UserTweets":
        return _json_result(empty_timeline_page("UserTweets"), outcome="empty")
    if operation == "TweetDetail":
        focal = _variables(query).get("focalTweetId")
        return archive.tweet_detail(str(focal) if focal else None)
    return miss_result(host, path, "request is not replayed", operation=operation)


_archives_lock = threading.Lock()
_archives: dict[Path, ReplayArchive] = {}


def replay_archive(data_dir: Path) -> ReplayArchive:
    with _archives_lock:
        archive = _archives.get(data_dir)
        if archive is None:
            archive = _archives[data_dir] = ReplayArchive(data_dir)
        return archive
