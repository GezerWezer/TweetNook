"""Feed cards from the stored card table, building and storing the ones it lacks."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from tweetnook.core import fastjson
from tweetnook.storage.web_cards import write_web_cards
from tweetnook.web.availability import annotate_web_tweets, slim_web_tweets

# One writer, off the request: storing cards must never make a page wait for the
# write lock, and two writers would only contend with each other.
_WRITER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tweetnook-web-cards")


def card_scope(collections: set[str] | None) -> str:
    """A post's card depends on which membership the collection filter selected."""
    return ",".join(sorted(collections)) if collections else "all"


def feed_cards(
    store: Any, tweet_ids: list[str], collections: set[str] | None
) -> list[tuple[str, bytes]]:
    """Return each listed post's id and finished JSON card, in order, skipping unknown ids."""
    scope = card_scope(collections)
    stored = getattr(store, "web_cards", None)  # Stores without the card table build every card.
    generation, cards = stored(tweet_ids, scope) if stored else (None, {})
    missing = [tweet_id for tweet_id in tweet_ids if tweet_id not in cards]
    if missing:
        # The Web UI shows links from the post's own entities, not resolved URL rows.
        rows = store.fetch_tweets_by_ids(missing, collections=collections, include_urls=False)
        for row in rows:
            row["type"] = "post"
        annotate_web_tweets(store, rows)
        slim_web_tweets(rows, list_cards=True)
        built = {str(row["tweet_id"]): fastjson.dumps(row) for row in rows if row.get("tweet_id")}
        if built and generation is not None:
            _WRITER.submit(write_web_cards, store.db_path, scope, generation, built)
        cards.update(built)
    return [(tweet_id, cards[tweet_id]) for tweet_id in tweet_ids if tweet_id in cards]
