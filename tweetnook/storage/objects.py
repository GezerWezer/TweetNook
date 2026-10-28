"""Canonical tweet objects and the relations, media, URLs and articles extracted with them."""

from __future__ import annotations

from typing import Any

from tweetnook.core.extractor import ExtractedTweetGraph, extract_secondary_objects
from tweetnook.core.timeline_parsing import TimelineTweet

from .common import AVAILABLE_SOURCES, LIVE_SOURCE, parse_created_at
from .records import PageBuffer, RecordsMixin


class ObjectsMixin(RecordsMixin):
    def _tweet_object_record(
        self,
        tweet: Any,
        *,
        source: str = LIVE_SOURCE,
        deleted_at: str | None = None,
        enrichment_state: str | None = None,
        enrichment_checked_at: str | None = None,
        enrichment_http_status: int | None = None,
        enrichment_reason: str | None = None,
        enrichment_detail: str | None = None,
        enrichment_retry_count: int | None = None,
        enrichment_next_retry_at: str | None = None,
        enrichment_first_unavailable_at: str | None = None,
        enrichment_retry_eligible: bool | None = None,
        preserve_lifecycle: bool = False,
        cursor: PageBuffer | None = None,
    ) -> dict[str, Any]:
        """Build a tweet_object row; ``preserve_lifecycle`` keeps an existing row's
        enrichment and deletion fields (replaying an old capture says nothing about now)."""

        row_key = self._row_key_for_tweet_object(tweet.tweet_id)
        context = self._record_context(row_key, cursor=cursor)
        keep_lifecycle = preserve_lifecycle and context.existing is not None
        prefer_incoming = self._prefer_incoming_source(
            context,
            source,
            deleted_at=deleted_at,
        )
        if (
            not prefer_incoming
            and source in AVAILABLE_SOURCES
            and self._existing_value(context, "enrichment_state") == "terminal_unavailable"
            and not self._existing_value(context, "text")
        ):
            # An unavailable tweet never captured with content holds only the error
            # payload, so any copy of the tweet itself is better.
            prefer_incoming = True
        if source in AVAILABLE_SOURCES:
            if enrichment_state is None:
                prior_state = self._existing_value(context, "enrichment_state")
                enrichment_state = (
                    "resurrected"
                    if prior_state in {"terminal_unavailable", "resurrected"}
                    else "done"
                )
            enrichment_checked_at = enrichment_checked_at or context.now
            enrichment_http_status = (
                200 if enrichment_http_status is None else enrichment_http_status
            )
            enrichment_reason = None
            enrichment_detail = None
            enrichment_retry_count = 0
            enrichment_next_retry_at = None
            enrichment_first_unavailable_at = None
            enrichment_retry_eligible = False
        elif deleted_at and enrichment_state is None:
            enrichment_state = "terminal_unavailable"
            enrichment_checked_at = enrichment_checked_at or context.now
            enrichment_reason = enrichment_reason or "archive_deleted"
            enrichment_first_unavailable_at = enrichment_first_unavailable_at or context.now
            enrichment_retry_count = enrichment_retry_count or 0
            enrichment_retry_eligible = False

        def merge_lifecycle(field_name: str, incoming: Any) -> Any:
            if keep_lifecycle:
                return self._existing_value(context, field_name)
            if source in AVAILABLE_SOURCES:
                return incoming
            return self._merge_by_source_precedence(
                context,
                field_name,
                incoming,
                prefer_incoming=prefer_incoming,
            )

        return self._record_with_context(
            context,
            row_key=row_key,
            record_type="tweet_object",
            tweet_id=tweet.tweet_id,
            source=self._merged_source_value(context, source, prefer_incoming=prefer_incoming),
            text=self._merge_by_source_precedence(
                context,
                "text",
                tweet.text,
                prefer_incoming=prefer_incoming,
            )
            or "",
            author_id=self._merge_by_source_precedence(
                context,
                "author_id",
                tweet.author_id,
                prefer_incoming=prefer_incoming,
            ),
            author_username=self._merge_by_source_precedence(
                context,
                "author_username",
                tweet.author_username,
                prefer_incoming=prefer_incoming,
            ),
            author_display_name=self._merge_by_source_precedence(
                context,
                "author_display_name",
                tweet.author_display_name,
                prefer_incoming=prefer_incoming,
            ),
            created_at=(
                merged_created_at := self._merge_by_source_precedence(
                    context,
                    "created_at",
                    tweet.created_at,
                    prefer_incoming=prefer_incoming,
                )
            ),
            created_at_ts=int(parse_created_at(merged_created_at).timestamp())
            if parse_created_at(merged_created_at)
            else None,
            deleted_at=(
                self._existing_value(context, "deleted_at")
                if keep_lifecycle
                else self._merged_deleted_at(
                    context,
                    deleted_at,
                    prefer_incoming=prefer_incoming,
                    incoming_source=source,
                )
            ),
            conversation_id=self._merge_by_source_precedence(
                context,
                "conversation_id",
                tweet.conversation_id,
                prefer_incoming=prefer_incoming,
            ),
            lang=self._merge_by_source_precedence(
                context,
                "lang",
                tweet.lang,
                prefer_incoming=prefer_incoming,
            ),
            note_tweet_text=self._merge_by_source_precedence(
                context,
                "note_tweet_text",
                tweet.note_tweet_text,
                prefer_incoming=prefer_incoming,
            ),
            enrichment_state=merge_lifecycle("enrichment_state", enrichment_state),
            enrichment_checked_at=merge_lifecycle("enrichment_checked_at", enrichment_checked_at),
            enrichment_http_status=merge_lifecycle(
                "enrichment_http_status", enrichment_http_status
            ),
            enrichment_reason=merge_lifecycle("enrichment_reason", enrichment_reason),
            enrichment_detail=merge_lifecycle("enrichment_detail", enrichment_detail),
            enrichment_retry_count=merge_lifecycle(
                "enrichment_retry_count", enrichment_retry_count
            ),
            enrichment_next_retry_at=merge_lifecycle(
                "enrichment_next_retry_at", enrichment_next_retry_at
            ),
            enrichment_first_unavailable_at=merge_lifecycle(
                "enrichment_first_unavailable_at", enrichment_first_unavailable_at
            ),
            enrichment_retry_eligible=merge_lifecycle(
                "enrichment_retry_eligible",
                int(enrichment_retry_eligible) if enrichment_retry_eligible is not None else None,
            ),
            raw_json=self._merged_tweet_raw_json(
                context, tweet.raw_json, prefer_incoming=prefer_incoming
            ),
        )

    def _tweet_relation_record(
        self,
        relation: Any,
        *,
        source: str = LIVE_SOURCE,
        cursor: PageBuffer | None = None,
    ) -> dict[str, Any]:
        row_key = self._row_key_for_tweet_relation(
            relation.source_tweet_id,
            relation.relation_type,
            relation.target_tweet_id,
        )
        context = self._record_context(row_key, cursor=cursor)
        prefer_incoming = self._prefer_incoming_source(context, source)
        return self._record_with_context(
            context,
            row_key=row_key,
            record_type="tweet_relation",
            tweet_id=relation.source_tweet_id,
            relation_type=relation.relation_type,
            target_tweet_id=relation.target_tweet_id,
            source=self._merged_source_value(context, source, prefer_incoming=prefer_incoming),
            raw_json=self._merge_by_source_precedence(
                context,
                "raw_json",
                self._json_value(relation.raw_json),
                prefer_incoming=prefer_incoming,
            ),
        )

    def _media_record(
        self,
        media: Any,
        *,
        provenance_source: str = LIVE_SOURCE,
        cursor: PageBuffer | None = None,
    ) -> dict[str, Any]:
        row_key = self._row_key_for_media(media.tweet_id, media.media_key)
        context = self._record_context(row_key, cursor=cursor)
        prefer_incoming = self._prefer_incoming_source(
            context,
            provenance_source,
            source_field="provenance_source",
        )
        return self._record_with_context(
            context,
            row_key=row_key,
            record_type="media",
            tweet_id=media.tweet_id,
            source=self._merge_by_source_precedence(
                context,
                "source",
                media.source,
                prefer_incoming=prefer_incoming,
            ),
            provenance_source=self._merged_source_value(
                context,
                provenance_source,
                prefer_incoming=prefer_incoming,
                field_name="provenance_source",
            ),
            article_id=self._merge_by_source_precedence(
                context,
                "article_id",
                media.article_id,
                prefer_incoming=prefer_incoming,
            ),
            position=media.position,
            media_key=media.media_key,
            media_type=self._merge_by_source_precedence(
                context,
                "media_type",
                media.media_type,
                prefer_incoming=prefer_incoming,
            ),
            media_url=self._merge_by_source_precedence(
                context,
                "media_url",
                media.media_url,
                prefer_incoming=prefer_incoming,
            ),
            thumbnail_url=self._merge_by_source_precedence(
                context,
                "thumbnail_url",
                media.thumbnail_url,
                prefer_incoming=prefer_incoming,
            ),
            width=self._merge_by_source_precedence(
                context,
                "width",
                media.width,
                prefer_incoming=prefer_incoming,
            ),
            height=self._merge_by_source_precedence(
                context,
                "height",
                media.height,
                prefer_incoming=prefer_incoming,
            ),
            duration_millis=self._merge_by_source_precedence(
                context,
                "duration_millis",
                media.duration_millis,
                prefer_incoming=prefer_incoming,
            ),
            variants_json=self._merge_by_source_precedence(
                context,
                "variants_json",
                self._json_value(media.variants) if media.variants else None,
                prefer_incoming=prefer_incoming,
            ),
            download_state=self._coalesce_value(
                self._existing_value(context, "download_state"),
                "pending",
            ),
            local_path=self._existing_value(context, "local_path"),
            sha256=self._existing_value(context, "sha256"),
            byte_size=self._existing_value(context, "byte_size"),
            content_type=self._existing_value(context, "content_type"),
            thumbnail_local_path=self._existing_value(context, "thumbnail_local_path"),
            thumbnail_sha256=self._existing_value(context, "thumbnail_sha256"),
            thumbnail_byte_size=self._existing_value(context, "thumbnail_byte_size"),
            thumbnail_content_type=self._existing_value(context, "thumbnail_content_type"),
            downloaded_at=self._existing_value(context, "downloaded_at"),
            download_error=self._existing_value(context, "download_error"),
            raw_json=self._merge_by_source_precedence(
                context,
                "raw_json",
                self._json_value(media.raw_json),
                prefer_incoming=prefer_incoming,
            ),
        )

    def _url_record(
        self,
        url: Any,
        *,
        source: str = LIVE_SOURCE,
        cursor: PageBuffer | None = None,
    ) -> dict[str, Any]:
        row_key = self._row_key_for_url(url.url_hash)
        context = self._record_context(row_key, cursor=cursor)
        prefer_incoming = self._prefer_incoming_source(context, source)
        return self._record_with_context(
            context,
            row_key=row_key,
            record_type="url",
            url_hash=url.url_hash,
            url=url.canonical_url,
            source=self._merged_source_value(context, source, prefer_incoming=prefer_incoming),
            expanded_url=self._merge_by_source_precedence(
                context,
                "expanded_url",
                url.expanded_url,
                prefer_incoming=prefer_incoming,
            ),
            final_url=self._merge_by_source_precedence(
                context,
                "final_url",
                url.final_url,
                prefer_incoming=prefer_incoming,
            ),
            canonical_url=url.canonical_url,
            url_host=self._merge_by_source_precedence(
                context,
                "url_host",
                url.host,
                prefer_incoming=prefer_incoming,
            ),
            title=self._merge_by_source_precedence(
                context,
                "title",
                url.title,
                prefer_incoming=prefer_incoming,
            ),
            description=self._merge_by_source_precedence(
                context,
                "description",
                url.description,
                prefer_incoming=prefer_incoming,
            ),
            site_name=self._merge_by_source_precedence(
                context,
                "site_name",
                url.site_name,
                prefer_incoming=prefer_incoming,
            ),
            unfurl_state=self._coalesce_value(
                self._existing_value(context, "unfurl_state"),
                "pending",
            ),
            last_fetched_at=self._existing_value(context, "last_fetched_at"),
            raw_json=self._merge_by_source_precedence(
                context,
                "raw_json",
                self._json_value(url.raw_json),
                prefer_incoming=prefer_incoming,
            ),
        )

    def _url_ref_record(
        self,
        url_ref: Any,
        *,
        source: str = LIVE_SOURCE,
        cursor: PageBuffer | None = None,
    ) -> dict[str, Any]:
        row_key = self._row_key_for_url_ref(url_ref.tweet_id, url_ref.position)
        context = self._record_context(row_key, cursor=cursor)
        prefer_incoming = self._prefer_incoming_source(context, source)
        return self._record_with_context(
            context,
            row_key=row_key,
            record_type="url_ref",
            tweet_id=url_ref.tweet_id,
            position=url_ref.position,
            source=self._merged_source_value(context, source, prefer_incoming=prefer_incoming),
            url_hash=self._merge_by_source_precedence(
                context,
                "url_hash",
                url_ref.url_hash,
                prefer_incoming=prefer_incoming,
            ),
            url=self._merge_by_source_precedence(
                context,
                "url",
                url_ref.short_url,
                prefer_incoming=prefer_incoming,
            ),
            expanded_url=self._merge_by_source_precedence(
                context,
                "expanded_url",
                url_ref.expanded_url,
                prefer_incoming=prefer_incoming,
            ),
            canonical_url=self._merge_by_source_precedence(
                context,
                "canonical_url",
                url_ref.canonical_url,
                prefer_incoming=prefer_incoming,
            ),
            display_url=self._merge_by_source_precedence(
                context,
                "display_url",
                url_ref.display_url,
                prefer_incoming=prefer_incoming,
            ),
            raw_json=self._merge_by_source_precedence(
                context,
                "raw_json",
                self._json_value(url_ref.raw_json),
                prefer_incoming=prefer_incoming,
            ),
        )

    def _article_record(
        self,
        article: Any,
        *,
        source: str = LIVE_SOURCE,
        cursor: PageBuffer | None = None,
    ) -> dict[str, Any]:
        row_key = self._row_key_for_article(article.tweet_id)
        context = self._record_context(row_key, cursor=cursor)
        prefer_incoming = self._prefer_incoming_source(context, source)
        content_text = self._merge_by_source_precedence(
            context,
            "content_text",
            article.content_text,
            prefer_incoming=prefer_incoming,
        )
        status = (
            "body_present"
            if content_text
            else self._coalesce_value(
                article.status,
                self._existing_value(context, "status"),
                "preview_only",
            )
        )
        return self._record_with_context(
            context,
            row_key=row_key,
            record_type="article",
            tweet_id=article.tweet_id,
            source=self._merged_source_value(context, source, prefer_incoming=prefer_incoming),
            article_id=self._merge_by_source_precedence(
                context,
                "article_id",
                article.article_id,
                prefer_incoming=prefer_incoming,
            ),
            title=self._merge_by_source_precedence(
                context,
                "title",
                article.title,
                prefer_incoming=prefer_incoming,
            ),
            summary_text=self._merge_by_source_precedence(
                context,
                "summary_text",
                article.summary_text,
                prefer_incoming=prefer_incoming,
            ),
            content_text=content_text,
            canonical_url=self._merge_by_source_precedence(
                context,
                "canonical_url",
                article.canonical_url,
                prefer_incoming=prefer_incoming,
            ),
            published_at=self._merge_by_source_precedence(
                context,
                "published_at",
                article.published_at,
                prefer_incoming=prefer_incoming,
            ),
            status=status,
            raw_json=self._merge_by_source_precedence(
                context,
                "raw_json",
                self._json_value(article.raw_json),
                prefer_incoming=prefer_incoming,
            ),
        )

    def _buffer_secondary_graph(
        self,
        graph: ExtractedTweetGraph,
        *,
        source: str = LIVE_SOURCE,
        preserve_lifecycle: bool = False,
        cursor: PageBuffer,
    ) -> None:
        keys = [
            self._row_key_for_tweet_object(item.tweet_id) for item in graph.tweet_objects.values()
        ]
        keys.extend(
            self._row_key_for_tweet_relation(
                item.source_tweet_id, item.relation_type, item.target_tweet_id
            )
            for item in graph.relations.values()
        )
        keys.extend(
            self._row_key_for_media(item.tweet_id, item.media_key) for item in graph.media.values()
        )
        keys.extend(self._row_key_for_url(item.url_hash) for item in graph.urls.values())
        keys.extend(
            self._row_key_for_url_ref(item.tweet_id, item.position)
            for item in graph.url_refs.values()
        )
        keys.extend(self._row_key_for_article(item.tweet_id) for item in graph.articles.values())
        self.prefetch_rows(keys, cursor=cursor)
        for item in graph.tweet_objects.values():
            self._queue_record(
                self._tweet_object_record(
                    item, source=source, preserve_lifecycle=preserve_lifecycle, cursor=cursor
                ),
                cursor=cursor,
            )
        for item in graph.relations.values():
            self._queue_record(
                self._tweet_relation_record(item, source=source, cursor=cursor),
                cursor=cursor,
            )
        for item in graph.media.values():
            self._queue_record(
                self._media_record(item, provenance_source=source, cursor=cursor),
                cursor=cursor,
            )
        for item in graph.urls.values():
            self._queue_record(self._url_record(item, source=source, cursor=cursor), cursor=cursor)
        for item in graph.url_refs.values():
            self._queue_record(
                self._url_ref_record(item, source=source, cursor=cursor),
                cursor=cursor,
            )
        for item in graph.articles.values():
            self._queue_record(
                self._article_record(item, source=source, cursor=cursor),
                cursor=cursor,
            )

    def _buffer_secondary_objects(
        self,
        tweets: list[TimelineTweet],
        *,
        source: str = LIVE_SOURCE,
        cursor: PageBuffer,
    ) -> None:
        graph = ExtractedTweetGraph()
        for tweet in tweets:
            graph.merge(extract_secondary_objects(tweet.raw_json))
        self._buffer_secondary_graph(graph, source=source, cursor=cursor)
