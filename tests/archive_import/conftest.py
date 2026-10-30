"""Helpers and fixtures shared by the archive import tests."""

from __future__ import annotations

import asyncio
import json
import zipfile
from collections.abc import Callable
from io import StringIO
from pathlib import Path

import httpx
import pytest
from rich.console import Console

import tweetnook.importer.archive_enrich as archive_enrich
from tests.conftest import request_details
from tweetnook.auth import ResolvedAuthBundle
from tweetnook.config import AppConfig, SyncConfig, XDGPaths
from tweetnook.core.exceptions import (
    ConfigError,
)
from tweetnook.core.timeline_parsing import TimelineTweet
from tweetnook.query_ids import QueryIdStore
from tweetnook.storage import ArchiveStore, open_archive_store


def _wrap_ytd(name: str, payload: object) -> str:
    return f"window.{name} = {json.dumps(payload, indent=2)}\n"


def _detail_entry_payload(entry_id: str, result: dict[str, object]) -> dict[str, object]:
    return {
        "data": {
            "threaded_conversation_with_injections_v2": {
                "instructions": [
                    {
                        "entries": [
                            {
                                "entryId": entry_id,
                                "content": {"itemContent": {"tweet_results": {"result": result}}},
                            }
                        ]
                    }
                ]
            }
        }
    }


def _empty_detail_entry_payload(entry_id: str) -> dict[str, object]:
    return {
        "data": {
            "threaded_conversation_with_injections_v2": {
                "instructions": [
                    {
                        "entries": [
                            {
                                "entryId": entry_id,
                                "content": {
                                    "__typename": "TimelineTimelineItem",
                                    "entryType": "TimelineTimelineItem",
                                    "itemContent": {
                                        "__typename": "TimelineTweet",
                                        "itemType": "TimelineTweet",
                                        "tweet_results": {},
                                    },
                                },
                            }
                        ]
                    }
                ]
            }
        }
    }


def _write_archive_dir(
    base: Path,
    *,
    like_tweet_id: str = "300",
    media_kind: str = "photo",
    include_video_main_asset: bool = False,
) -> Path:
    root = base / "archive"
    data_dir = root / "data"
    media_dir = data_dir / "tweets_media"
    media_dir.mkdir(parents=True)

    manifest = {
        "userInfo": {
            "accountId": "42",
            "userName": "archiveuser",
            "displayName": "Archive User",
        },
        "archiveInfo": {
            "generationDate": "2026-03-16T08:57:45.244Z",
            "isPartialArchive": False,
        },
        "dataTypes": {
            "account": {
                "files": [{"fileName": "data/account.js", "globalName": "YTD.account.part0"}]
            },
            "tweets": {
                "files": [{"fileName": "data/tweets.js", "globalName": "YTD.tweets.part0"}],
                "mediaDirectory": "data/tweets_media",
            },
            "tweetHeaders": {
                "files": [
                    {"fileName": "data/tweet-headers.js", "globalName": "YTD.tweet_headers.part0"}
                ]
            },
            "deletedTweets": {
                "files": [
                    {
                        "fileName": "data/deleted-tweets.js",
                        "globalName": "YTD.deleted_tweets.part0",
                    }
                ]
            },
            "deletedTweetHeaders": {
                "files": [
                    {
                        "fileName": "data/deleted-tweet-headers.js",
                        "globalName": "YTD.deleted_tweet_headers.part0",
                    }
                ]
            },
            "like": {"files": [{"fileName": "data/like.js", "globalName": "YTD.like.part0"}]},
        },
    }
    (data_dir / "manifest.js").write_text(
        f"window.__THAR_CONFIG = {json.dumps(manifest, indent=2)}\n",
        encoding="utf-8",
    )
    (data_dir / "account.js").write_text(
        _wrap_ytd(
            "YTD.account.part0",
            [
                {
                    "account": {
                        "username": "archiveuser",
                        "accountId": "42",
                        "accountDisplayName": "Archive User",
                    }
                }
            ],
        ),
        encoding="utf-8",
    )

    if media_kind == "video":
        media_item = {
            "id": "500",
            "id_str": "500",
            "media_url": "http://pbs.twimg.com/ext_tw_video_thumb/archive-poster.jpg",
            "media_url_https": "https://pbs.twimg.com/ext_tw_video_thumb/archive-poster.jpg",
            "expanded_url": "https://x.com/archiveuser/status/100/video/1",
            "url": "https://t.co/archive-video",
            "display_url": "pic.x.com/archive-video",
            "type": "video",
            "sizes": {"large": {"w": "1200", "h": "675", "resize": "fit"}},
            "video_info": {
                "duration_millis": 1000,
                "variants": [
                    {
                        "content_type": "application/x-mpegURL",
                        "url": "https://video.twimg.com/ext_tw_video/archive-video.m3u8",
                    },
                    {
                        "bitrate": 832000,
                        "content_type": "video/mp4",
                        "url": "https://video.twimg.com/ext_tw_video/archive-video.mp4",
                    },
                ],
            },
        }
        exported_media_names = ["100-archive-poster.jpg"]
        if include_video_main_asset:
            exported_media_names.append("100-archive-video.mp4")
    else:
        media_item = {
            "id": "500",
            "id_str": "500",
            "media_url": "http://pbs.twimg.com/media/archive-photo.jpg",
            "media_url_https": "https://pbs.twimg.com/media/archive-photo.jpg",
            "expanded_url": "https://x.com/archiveuser/status/100/photo/1",
            "url": "https://t.co/archive-photo",
            "display_url": "pic.x.com/archive-photo",
            "type": "photo",
            "sizes": {"large": {"w": "1200", "h": "675", "resize": "fit"}},
        }
        exported_media_names = ["100-archive-photo.jpg"]

    authored_tweet = {
        "tweet": {
            "id": "100",
            "id_str": "100",
            "full_text": "archive authored tweet",
            "created_at": "Sat Mar 14 00:00:00 +0000 2026",
            "lang": "en",
            "entities": {
                "urls": [],
                "hashtags": [],
                "user_mentions": [],
                "media": [media_item],
            },
            "extended_entities": {"media": [media_item]},
            "favorite_count": "0",
            "retweet_count": "0",
            "retweeted": False,
            "favorited": False,
            "source": '<a href="https://x.com" rel="nofollow">Twitter Web App</a>',
        }
    }
    deleted_tweet = {
        "tweet": {
            "id": "200",
            "id_str": "200",
            "full_text": "deleted archive tweet",
            "created_at": "Sun Mar 15 00:00:00 +0000 2026",
            "lang": "en",
            "entities": {"urls": [], "hashtags": [], "user_mentions": []},
            "favorite_count": "0",
            "retweet_count": "0",
            "retweeted": False,
            "favorited": False,
            "source": '<a href="https://x.com" rel="nofollow">Twitter Web App</a>',
        }
    }
    (data_dir / "tweets.js").write_text(
        _wrap_ytd("YTD.tweets.part0", [authored_tweet]),
        encoding="utf-8",
    )
    (data_dir / "tweet-headers.js").write_text(
        _wrap_ytd(
            "YTD.tweet_headers.part0",
            [
                {
                    "tweet": {
                        "tweet_id": "100",
                        "user_id": "42",
                        "created_at": authored_tweet["tweet"]["created_at"],
                    }
                }
            ],
        ),
        encoding="utf-8",
    )
    (data_dir / "deleted-tweets.js").write_text(
        _wrap_ytd("YTD.deleted_tweets.part0", [deleted_tweet]),
        encoding="utf-8",
    )
    (data_dir / "deleted-tweet-headers.js").write_text(
        _wrap_ytd(
            "YTD.deleted_tweet_headers.part0",
            [
                {
                    "tweet": {
                        "tweet_id": "200",
                        "user_id": "42",
                        "created_at": deleted_tweet["tweet"]["created_at"],
                        "deleted_at": "Mon Mar 16 00:00:00 +0000 2026",
                    }
                }
            ],
        ),
        encoding="utf-8",
    )
    (data_dir / "like.js").write_text(
        _wrap_ytd(
            "YTD.like.part0",
            [
                {
                    "like": {
                        "tweetId": like_tweet_id,
                        "fullText": "archive liked tweet",
                        "expandedUrl": f"https://twitter.com/i/web/status/{like_tweet_id}",
                    }
                }
            ],
        ),
        encoding="utf-8",
    )
    for exported_media_name in exported_media_names:
        payload = b"archive-video" if exported_media_name.endswith(".mp4") else b"archive-photo"
        (media_dir / exported_media_name).write_bytes(payload)
    return root


def _live_tweet(tweet_id: str, *, text: str) -> TimelineTweet:
    raw_json = {
        "__typename": "Tweet",
        "rest_id": tweet_id,
        "legacy": {
            "full_text": text,
            "created_at": "Sat Mar 14 00:00:00 +0000 2026",
            "conversation_id_str": tweet_id,
            "lang": "en",
            "entities": {"urls": []},
        },
        "core": {
            "user_results": {
                "result": {
                    "__typename": "User",
                    "rest_id": "999",
                    "legacy": {"screen_name": "liveuser", "name": "Live User"},
                }
            }
        },
    }
    return TimelineTweet(
        tweet_id=tweet_id,
        text=text,
        author_id="999",
        author_username="liveuser",
        author_display_name="Live User",
        created_at="Sat Mar 14 00:00:00 +0000 2026",
        sort_index="999",
        raw_json=raw_json,
    )


def _console() -> Console:
    return Console(file=StringIO(), force_terminal=False, color_system=None)


def _auth_bundle() -> ResolvedAuthBundle:
    return ResolvedAuthBundle(
        auth_token="auth",
        ct0="ct0",
        user_id="42",
        auth_token_source="test",
        ct0_source="test",
        user_id_source="test",
    )


def _disable_live_reconciliation(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_no_auth(_config):
        raise ConfigError("no auth configured")

    monkeypatch.setattr(archive_enrich, "resolve_auth_bundle", raise_no_auth)


def _write_archive_zip(archive_dir: Path, destination: Path) -> Path:
    with zipfile.ZipFile(destination, "w") as handle:
        for path in sorted(archive_dir.rglob("*")):
            if path.is_file():
                handle.write(path, path.relative_to(archive_dir).as_posix())
    return destination


class DetailEnrichment:
    """Runs archive detail enrichment through the real locked job and HTTP client.

    TweetDetail requests go through an httpx.MockTransport, so the code under test keeps its
    own lock, store, client and request path; only the remote answers are scripted.
    """

    def __init__(self, paths: XDGPaths, monkeypatch: pytest.MonkeyPatch) -> None:
        self.paths = paths
        self.requested: list[str] = []
        self.dirty: list[tuple[int, int]] = []
        # No retries, so a transport failure is final without fetch_page's real backoff.
        self.config = AppConfig(sync=SyncConfig(detail_delay=0.0, detail_max_retries=0))
        QueryIdStore(paths).save({"TweetDetail": "detail-query-id"})
        flush = ArchiveStore.flush

        def spy(store, buffer):
            attempts = sum(row["record_type"] == "tweet_object" for row in buffer.records.values())
            result = flush(store, buffer)
            self.dirty.append((attempts, 1))
            return result

        monkeypatch.setattr(ArchiveStore, "flush", spy)

    def transport(self, respond: Callable[[str, httpx.Request], object]) -> httpx.MockTransport:
        """Answer each TweetDetail request with respond(tweet_id, request).

        respond returns a JSON payload or an httpx.Response, or raises.
        """

        def handler(request: httpx.Request) -> httpx.Response:
            _operation, variables = request_details(str(request.url))
            tweet_id = str(variables["focalTweetId"])
            self.requested.append(tweet_id)
            answer = respond(tweet_id, request)
            if isinstance(answer, httpx.Response):
                return answer
            return httpx.Response(200, json=answer, request=request)

        return httpx.MockTransport(handler)

    def run(
        self,
        respond: Callable[[str, httpx.Request], object],
        *,
        limit: int | None = None,
        signed_in: bool = True,
        started_at: str | None = None,
    ) -> archive_enrich.ArchiveEnrichResult:
        return asyncio.run(
            archive_enrich._enrich_pending_rows(
                limit=limit,
                config=self.config,
                paths=self.paths,
                auth_bundle=_auth_bundle() if signed_in else None,
                transport=self.transport(respond),
                console=_console(),
                started_at=started_at,
            )
        )


@pytest.fixture
def detail_enrichment(paths: XDGPaths, monkeypatch: pytest.MonkeyPatch) -> DetailEnrichment:
    return DetailEnrichment(paths, monkeypatch)


def seed_tweet_objects(paths: XDGPaths, tweet_ids, **columns: object) -> None:
    """Save sparse archive posts that are waiting for detail enrichment."""
    values = {"enrichment_state": "pending", **columns}
    names = ["row_key", "record_type", "tweet_id", *values]
    store = open_archive_store(paths, create=True)
    assert store is not None
    try:
        store.conn.executemany(
            f"INSERT INTO archive ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
            [
                (f"tweet_object:{tweet_id}", "tweet_object", tweet_id, *values.values())
                for tweet_id in tweet_ids
            ],
        )
        store.conn.commit()
    finally:
        store.close()


def tweet_object_rows(paths: XDGPaths) -> dict[str, dict[str, object]]:
    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        rows = store.conn.execute(
            "SELECT * FROM archive WHERE record_type = 'tweet_object'"
        ).fetchall()
        return {row["tweet_id"]: dict(row) for row in rows}
    finally:
        store.close()
