"""Helpers and fixtures shared by the storage tests."""

from __future__ import annotations

from tests.conftest import (
    make_article_result,
    make_photo_media,
    make_tweet_result,
    make_url_entity,
    make_video_media,
)
from tweetnook.core.timeline_parsing import TimelineTweet


def _counts(**overrides: int) -> dict[str, int]:
    counts = {
        "raw_captures": 0,
        "tweets": 0,
        "collections": 0,
        "tweet_objects": 0,
        "tweet_relations": 0,
        "media": 0,
        "urls": 0,
        "url_refs": 0,
        "articles": 0,
        "import_manifests": 0,
        "sync_state": 0,
    }
    counts.update(overrides)
    return counts


def _tweet(tweet_id: str) -> TimelineTweet:
    return TimelineTweet(
        tweet_id=tweet_id,
        text=f"tweet {tweet_id}",
        author_id="1",
        author_username="user1",
        author_display_name="User 1",
        created_at="Sat Mar 14 00:00:00 +0000 2026",
        sort_index="10",
        raw_json={"tweet": tweet_id},
    )


def _tweet_with_created_at(
    tweet_id: str,
    *,
    created_at: str | None,
    sort_index: str,
) -> TimelineTweet:
    return TimelineTweet(
        tweet_id=tweet_id,
        text=f"tweet {tweet_id}",
        author_id="1",
        author_username=f"user{tweet_id}",
        author_display_name=f"User {tweet_id}",
        created_at=created_at,
        sort_index=sort_index,
        raw_json={"tweet": tweet_id},
    )


def _complex_tweet(tweet_id: str = "100") -> TimelineTweet:
    quoted = make_tweet_result(
        "200",
        "quoted short",
        user_id="2000",
        note_text="quoted longform",
        urls=[
            make_url_entity(
                "https://t.co/quoted",
                "https://quoted.example.com/post",
                display_url="quoted.example.com/post",
            )
        ],
        media=[
            make_video_media(
                "7_quoted",
                "https://pbs.twimg.com/ext_tw_video_thumb/quoted.jpg",
                bitrate_url="https://video.twimg.com/ext_tw_video/quoted.mp4",
            )
        ],
    )
    raw = make_tweet_result(
        tweet_id,
        "root short",
        user_id="1000",
        note_text="root longform text",
        urls=[
            make_url_entity(
                "https://t.co/root",
                "https://example.com/story?utm_source=x&keep=1",
                display_url="example.com/story",
            )
        ],
        media=[make_photo_media("3_root", "https://pbs.twimg.com/media/root.jpg")],
        quoted_tweet=quoted,
        article=make_article_result(
            "article-1",
            title="Article title",
            preview_text="Article preview",
            plain_text="Article body",
            url="https://x.com/i/article/123",
        ),
    )
    return TimelineTweet(
        tweet_id=tweet_id,
        text="root longform text",
        author_id="1000",
        author_username="user1000",
        author_display_name="User 1000",
        created_at="Sat Mar 14 00:00:00 +0000 2026",
        sort_index="10",
        raw_json=raw,
    )
