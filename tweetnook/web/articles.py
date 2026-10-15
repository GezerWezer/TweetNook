"""Attach archived X Articles to every Web tweet, including quote context."""

from __future__ import annotations

import json
from typing import Any

from tweetnook import fastjson

ARTICLE_COLUMNS = [
    "tweet_id",
    "article_id",
    "title",
    "summary_text",
    "content_text",
    "canonical_url",
    "published_at",
    "status",
    "raw_json",
]
MEDIA_COLUMNS = [
    "tweet_id",
    "media_key",
    "source",
    "article_id",
    "media_type",
    "media_url",
    "thumbnail_url",
    "width",
    "height",
    "duration_millis",
    "local_path",
    "thumbnail_local_path",
    "raw_json",
    "position",
]


def _dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = fastjson.loads(value)
        except (ValueError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _media(row: dict[str, Any]) -> dict[str, Any]:
    raw = _dict(row.get("raw_json"))
    return {
        "media_key": row.get("media_key"),
        "media_id": raw.get("media_id"),
        "source": row.get("source"),
        "type": row.get("media_type"),
        "url": row.get("media_url"),
        "thumbnail_url": row.get("thumbnail_url"),
        "width": row.get("width"),
        "height": row.get("height"),
        "duration_millis": row.get("duration_millis"),
        "alt_text": raw.get("alt_text") or "",
        "download": {
            "local_path": row.get("local_path"),
            "thumbnail_local_path": row.get("thumbnail_local_path"),
        },
    }


def annotate_web_articles(store: Any, tweets: list[dict[str, Any]]) -> None:
    """Batch-load articles; keep their cover/body images out of tweet galleries."""
    nodes: list[dict[str, Any]] = []

    def visit(tweet: Any, depth: int = 0) -> None:
        if not isinstance(tweet, dict) or depth > 3:
            return
        if (tweet.get("availability") or {}).get("placeholder"):
            tweet.pop("article", None)
            return
        nodes.append(tweet)
        for key in ("quoted_tweet", "retweeted_tweet"):
            visit(tweet.get(key), depth + 1)

    for tweet in tweets:
        visit(tweet)
    ids = list(dict.fromkeys(str(node["tweet_id"]) for node in nodes if node.get("tweet_id")))
    if not ids:
        return
    rows = store._rows_for_values("article", "tweet_id", ids, columns=ARTICLE_COLUMNS)
    articles = {str(row["tweet_id"]): row for row in rows if row.get("tweet_id")}
    if not articles:
        return
    media_rows = store._rows_for_values("media", "tweet_id", list(articles), columns=MEDIA_COLUMNS)
    media_by_id: dict[str, list[dict[str, Any]]] = {}
    for row in sorted(media_rows, key=lambda item: item.get("position") or 0):
        if str(row.get("source") or "").startswith("article_"):
            media_by_id.setdefault(str(row["tweet_id"]), []).append(_media(row))
    for node in nodes:
        tweet_id = str(node.get("tweet_id") or "")
        row = articles.get(tweet_id)
        if row is None:
            continue
        raw = _dict(row.get("raw_json"))
        article = {
            key: row.get(key) for key in ARTICLE_COLUMNS if key not in {"raw_json", "tweet_id"}
        }
        state = raw.get("content_state")
        article["content_state"] = state if isinstance(state, dict) else None
        article["media"] = media_by_id.get(tweet_id, [])
        node["article"] = article
        urls = {item.get("url") for item in article["media"] if item.get("url")}
        node["media"] = [
            item
            for item in node.get("media", [])
            if not str(item.get("source") or "").startswith("article_")
            and item.get("url") not in urls
        ]
    for node in nodes:
        quote = node.get("quoted_tweet")
        if isinstance(quote, dict):
            node["qt_media"] = quote.get("media", [])
