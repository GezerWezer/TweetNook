"""Batched saved-content, attachment, note and archive-save observations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tweetnook.core import fastjson

from .checks import ChecksMixin
from .record_types import RecordType

OBJECT_COLUMNS = [
    "tweet_id",
    "text",
    "author_id",
    "author_username",
    "author_display_name",
    "created_at",
    "synced_at",
    "raw_json",
    "enrichment_state",
    "enrichment_checked_at",
    "enrichment_reason",
    "enrichment_retry_eligible",
]


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


@dataclass(slots=True)
class PostObservations:
    objects: list[dict[str, Any]]
    hidden_ids: set[str]


@dataclass(slots=True)
class AttachmentObservations:
    objects: list[dict[str, Any]]
    media: list[dict[str, Any]]
    tags: list[dict[str, Any]]


@dataclass(slots=True)
class ArticleObservations:
    articles: list[dict[str, Any]]
    media: list[dict[str, Any]]


@dataclass(slots=True)
class NoteObservation:
    # A missing dictionary entry means never observed; None means observed absent.
    note: dict[str, Any] | None


@dataclass(slots=True)
class ArchiveSaveObservation:
    saved_at: str | None
    collection: str | None = None
    related: bool = False


class PresentationMixin(ChecksMixin):
    def post_observations(self, tweet_ids: list[str]) -> PostObservations:
        objects = self._rows_for_values(
            RecordType.TWEET_OBJECT, "tweet_id", tweet_ids, columns=OBJECT_COLUMNS
        )
        hidden = self._rows_for_values(
            RecordType.TWEET_HIDDEN, "tweet_id", tweet_ids, columns=["tweet_id"]
        )
        return PostObservations(
            objects, {str(row["tweet_id"]) for row in hidden if row.get("tweet_id")}
        )

    def presentation_targets(
        self, tweet_ids: list[str], relation_types: tuple[str, ...]
    ) -> dict[tuple[str, str], str]:
        rows = self._rows_for_values(
            RecordType.TWEET_RELATION,
            "tweet_id",
            tweet_ids,
            columns=["tweet_id", "target_tweet_id", "relation_type"],
        )
        return {
            (str(row["tweet_id"]), str(row["relation_type"])): str(row["target_tweet_id"])
            for row in rows
            if row.get("relation_type") in relation_types
            and row.get("tweet_id")
            and row.get("target_tweet_id")
        }

    def attachment_observations(self, tweet_ids: list[str]) -> AttachmentObservations:
        objects = self._rows_for_values(
            RecordType.TWEET_OBJECT, "tweet_id", tweet_ids, columns=OBJECT_COLUMNS
        )
        media = self._rows_for_values(
            RecordType.MEDIA,
            "tweet_id",
            tweet_ids,
            columns=[
                "tweet_id",
                "source",
                "position",
                "media_type",
                "media_url",
                "thumbnail_url",
                "width",
                "height",
                "duration_millis",
                "local_path",
                "thumbnail_local_path",
            ],
        )
        tags = self._rows_for_values(
            RecordType.MEDIA_TAG, "tweet_id", tweet_ids, columns=["tweet_id", "raw_json"]
        )
        return AttachmentObservations(objects, media, tags)

    def article_observations(self, tweet_ids: list[str]) -> ArticleObservations:
        articles = self._rows_for_values(
            RecordType.ARTICLE, "tweet_id", tweet_ids, columns=ARTICLE_COLUMNS
        )
        ids = [str(row["tweet_id"]) for row in articles if row.get("tweet_id")]
        media = (
            self._rows_for_values(RecordType.MEDIA, "tweet_id", ids, columns=MEDIA_COLUMNS)
            if ids
            else []
        )
        return ArticleObservations(articles, media)

    def link_card_observations(self, tweet_ids: list[str]) -> list[dict[str, Any]]:
        return self._rows_for_values(
            RecordType.MEDIA,
            "tweet_id",
            tweet_ids,
            columns=["tweet_id", "source", "media_url", "thumbnail_url", "local_path"],
        )

    def note_observations(self, tweet_ids: list[str]) -> dict[str, NoteObservation]:
        if not tweet_ids:
            return {}
        observations: dict[str, NoteObservation] = {}
        for start in range(0, len(tweet_ids), 500):
            chunk = tweet_ids[start : start + 500]
            placeholders = ", ".join("?" for _ in chunk)
            for tweet_id, note_json, removed_at in self.conn.execute(
                "SELECT tweet_id, raw_json, removed_at FROM community_notes "
                f"WHERE tweet_id IN ({placeholders})",
                chunk,
            ).fetchall():
                observations.setdefault(str(tweet_id), NoteObservation(None))
                if removed_at is None:
                    try:
                        note = fastjson.loads(note_json) if note_json else None
                    except (TypeError, ValueError):
                        note = None
                    observations[str(tweet_id)] = NoteObservation(
                        note if isinstance(note, dict) else None
                    )
        return observations

    def archive_save_observations(
        self, tweet_ids: list[str]
    ) -> dict[str, list[ArchiveSaveObservation]]:
        observations: dict[str, list[ArchiveSaveObservation]] = {}
        for row in self._rows_for_values(
            RecordType.TWEET,
            "tweet_id",
            tweet_ids,
            columns=["tweet_id", "collection_type", "first_seen_at"],
        ):
            observations.setdefault(str(row["tweet_id"]), []).append(
                ArchiveSaveObservation(row.get("first_seen_at"), row.get("collection_type"))
            )
        missing = [tweet_id for tweet_id in tweet_ids if tweet_id not in observations]
        for row in self._rows_for_values(
            RecordType.TWEET_OBJECT, "tweet_id", missing, columns=["tweet_id", "first_seen_at"]
        ):
            observations.setdefault(str(row["tweet_id"]), []).append(
                ArchiveSaveObservation(row.get("first_seen_at"), related=True)
            )
        return observations
