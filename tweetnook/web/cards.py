"""Attach locally archived link images without adding them to tweet galleries."""

from __future__ import annotations

from typing import Any


def annotate_web_link_cards(store: Any, tweets: list[dict[str, Any]]) -> None:
    nodes: list[dict[str, Any]] = []

    def visit(tweet: Any, depth: int = 0) -> None:
        if not isinstance(tweet, dict) or depth > 3:
            return
        if (tweet.get("availability") or {}).get("placeholder"):
            tweet.pop("card_media", None)
            return
        nodes.append(tweet)
        for key in ("quoted_tweet", "retweeted_tweet"):
            visit(tweet.get(key), depth + 1)

    for tweet in tweets:
        visit(tweet)
    ids = list(dict.fromkeys(str(node["tweet_id"]) for node in nodes if node.get("tweet_id")))
    if not ids:
        return
    media_by_id: dict[str, list[dict[str, Any]]] = {}
    for row in store._rows_for_values(
        "media",
        "tweet_id",
        ids,
        columns=["tweet_id", "source", "media_url", "thumbnail_url", "local_path"],
    ):
        if row.get("source") == "link_card":
            media_by_id.setdefault(str(row["tweet_id"]), []).append(
                {
                    "url": row.get("media_url"),
                    "thumbnail_url": row.get("thumbnail_url"),
                    "download": {"local_path": row.get("local_path")},
                }
            )
    for node in nodes:
        node["card_media"] = media_by_id.get(str(node.get("tweet_id") or ""), [])
        node["media"] = [
            item for item in node.get("media", []) if item.get("source") != "link_card"
        ]
    for node in nodes:
        quote = node.get("quoted_tweet")
        if isinstance(quote, dict):
            node["qt_media"] = quote.get("media", [])
