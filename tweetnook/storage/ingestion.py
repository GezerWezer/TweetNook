"""Normalized official-import observations and store-owned batch ingestion."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from tweetnook.core.extractor import ExtractedTweetGraph, extract_secondary_objects
from tweetnook.core.timeline_parsing import TimelineTweet

from .common import ARCHIVE_SOURCE, LIVE_SOURCE
from .records import PageBuffer
from .tweets import TweetsMixin


@dataclass(slots=True)
class _LikePlaceholder:
    tweet_id: str
    text: str
    author_id: str | None = None
    author_username: str | None = None
    author_display_name: str | None = None
    created_at: str | None = None
    conversation_id: str | None = None
    lang: str | None = None
    note_tweet_text: str | None = None
    raw_json: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ImportObservation:
    timeline_tweet: TimelineTweet
    deleted_at: str | None = None
    graph: ExtractedTweetGraph | None = None


class IngestionMixin(TweetsMixin):
    def ingest_import_batch(
        self,
        observations: list[ImportObservation],
        *,
        membership: Literal["tweet", "like"],
        force_deleted: bool = False,
        before_item: Callable[[ImportObservation], None] | None = None,
        after_item: Callable[[ImportObservation], None] | None = None,
    ) -> int:
        buffer = PageBuffer()
        keys: list[str] = []
        if membership != "like":
            for observation in observations:
                if observation.graph is None:
                    observation.graph = extract_secondary_objects(
                        observation.timeline_tweet.raw_json
                    )
            self._saved_media_rows(
                (
                    item.tweet_id
                    for observation in observations
                    for item in observation.graph.media.values()
                ),
                cursor=buffer,
            )
        for observation in observations:
            tweet = observation.timeline_tweet
            keys.append(self._row_key_for_tweet(tweet.tweet_id, membership))
            if membership == "like":
                keys.append(self._row_key_for_tweet_object(tweet.tweet_id))
            else:
                keys.extend(self._secondary_graph_keys(observation.graph, cursor=buffer))
        self.prefetch_rows(keys, cursor=buffer)
        for observation in observations:
            tweet = observation.timeline_tweet
            if before_item is not None:
                before_item(observation)
            self.upsert_membership(
                tweet,
                membership,
                source=ARCHIVE_SOURCE,
                deleted_at=observation.deleted_at,
                sort_index=tweet.sort_index,
                cursor=buffer,
            )
            if membership == "like":
                if self._should_seed_like_placeholder(buffer, tweet.tweet_id):
                    self._queue_record(
                        self._tweet_object_record(
                            _LikePlaceholder(
                                tweet_id=tweet.tweet_id, text=tweet.text, raw_json=tweet.raw_json
                            ),
                            source=ARCHIVE_SOURCE,
                            enrichment_state="pending",
                            cursor=buffer,
                        ),
                        cursor=buffer,
                    )
            else:
                assert observation.graph is not None
                self._queue_secondary_graph(
                    observation.graph,
                    source=ARCHIVE_SOURCE,
                    deleted_at_by_tweet_id=(
                        {tweet.tweet_id: observation.deleted_at} if observation.deleted_at else {}
                    ),
                    force_deleted_tweet_id=tweet.tweet_id if force_deleted else None,
                    cursor=buffer,
                )
            if after_item is not None:
                after_item(observation)
        return self.flush(buffer)

    def _should_seed_like_placeholder(self, buffer: PageBuffer, tweet_id: str) -> bool:
        row = self._lookup_row(self._row_key_for_tweet_object(tweet_id), cursor=buffer)
        if row is None:
            return True
        if row.get("source") == LIVE_SOURCE:
            return False
        return not any(
            row.get(field_name)
            for field_name in (
                "author_id",
                "author_username",
                "created_at",
                "conversation_id",
                "lang",
            )
        )
