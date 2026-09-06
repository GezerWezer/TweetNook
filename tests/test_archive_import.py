from __future__ import annotations

import asyncio
import json
import zipfile
from contextlib import asynccontextmanager, contextmanager
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from rich.console import Console

import tweetnook.archive_import as archive_import
from tests.conftest import make_tweet_detail_response, make_tweet_result, request_details
from tweetnook.archive_import import enrich_imported_archive, import_x_archive
from tweetnook.auth import ResolvedAuthBundle
from tweetnook.client.timelines import TimelineTweet
from tweetnook.config import AppConfig
from tweetnook.exceptions import (
    APIResponseError,
    ArchiveOwnerMismatchError,
    ConfigError,
    RepeatedFocalAbsenceError,
    StaleQueryIdError,
)
from tweetnook.pipeline import PipelineReporter
from tweetnook.storage import open_archive_store


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


def _write_archive_zip(archive_dir: Path, destination: Path) -> Path:
    with zipfile.ZipFile(destination, "w") as handle:
        for path in sorted(archive_dir.rglob("*")):
            if path.is_file():
                handle.write(path, path.relative_to(archive_dir).as_posix())
    return destination


def _write_root_layout_archive_dir(base: Path, **kwargs: object) -> Path:
    root = _write_archive_dir(base, **kwargs)
    data_dir = root / "data"
    for path in sorted(data_dir.iterdir()):
        path.rename(root / path.name)
    data_dir.rmdir()
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

    monkeypatch.setattr(archive_import, "resolve_auth_bundle", raise_no_auth)


def test_offline_import_copies_full_archive_without_live_followup(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)

    async def unexpected_followup(*args, **kwargs):
        pytest.fail("Offline import must not start live follow-up")

    monkeypatch.setattr(archive_import, "_run_archive_followup", unexpected_followup)
    for repeated in (False, True):
        result = asyncio.run(
            import_x_archive(
                archive_dir,
                config=AppConfig(),
                paths=paths,
                console=_console(),
                offline=True,
                enrich=True,
                detail_lookups=100,
            )
        )
        assert result.skipped is repeated
        assert not result.followup_performed
        assert result.counts["authored_tweets"] == 1
        assert result.counts["likes"] == 1
        assert result.counts["media_files_copied"] == 1
        assert (paths.data_dir / "media/100/3_500.jpg").is_file()

    store = open_archive_store(paths, create=False)
    assert store is not None
    assert store._query(expr="record_type = 'import_manifest'")[0]["status"] == "completed"
    store.close()


def test_import_x_archive_directory_populates_archive_and_copies_media(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    _disable_live_reconciliation(monkeypatch)

    result = asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    assert result.skipped is False
    assert result.counts["authored_tweets"] == 1
    assert result.counts["deleted_authored_tweets"] == 1
    assert result.counts["likes"] == 1
    assert result.counts["media_files_copied"] == 1
    assert result.pending_enrichment == 1
    assert any("bookmark dataset" in warning for warning in result.warnings)
    assert any("live reconciliation skipped" in warning for warning in result.warnings)

    store = open_archive_store(paths, create=False)
    assert store is not None
    assert store.get_archive_owner_id() == "42"
    assert store.counts()["import_manifests"] == 1

    tweet_rows = store._query(expr="record_type = 'tweet'")
    row_by_key = {(row["collection_type"], row["tweet_id"]): row for row in tweet_rows}
    assert row_by_key[("tweet", "100")]["source"] == "x_archive"
    assert row_by_key[("tweet", "200")]["deleted_at"] == "Mon Mar 16 00:00:00 +0000 2026"
    assert row_by_key[("like", "300")]["sort_index"] == "-1"

    tweet_objects = {
        row["tweet_id"]: row for row in store._query(expr="record_type = 'tweet_object'")
    }
    assert tweet_objects["100"]["source"] == "x_archive"
    assert tweet_objects["200"]["enrichment_state"] == "terminal_unavailable"
    assert tweet_objects["200"]["enrichment_reason"] == "archive_deleted"
    assert tweet_objects["200"]["enrichment_retry_eligible"] == 0
    assert tweet_objects["300"]["enrichment_state"] == "pending"

    media_rows = store._query(expr="record_type = 'media'")
    assert len(media_rows) == 1
    assert media_rows[0]["download_state"] == "done"
    assert media_rows[0]["local_path"] == "media/100/3_500.jpg"
    assert (paths.data_dir / media_rows[0]["local_path"]).exists()

    manifest_rows = store._query(expr="record_type = 'import_manifest'")
    manifest_counts = json.loads(manifest_rows[0]["counts_json"])
    assert manifest_rows[0]["enrichment_followup_status"] == "enrichment_complete"
    assert manifest_rows[0]["enrichment_aborted_reason"] is None
    assert manifest_counts["pending_enrichment"] == 1
    store.close()


def test_deleted_tweets_without_deleted_header_remain_deleted(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    (archive_dir / "data" / "deleted-tweet-headers.js").write_text(
        _wrap_ytd("YTD.deleted_tweet_headers.part0", []),
        encoding="utf-8",
    )
    _disable_live_reconciliation(monkeypatch)

    result = asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    assert result.counts["authored_tweets"] == 1
    assert result.counts["deleted_authored_tweets"] == 1

    store = open_archive_store(paths, create=False)
    assert store is not None
    tweet_row = store._query(expr="row_key = 'tweet:tweet::200'", limit=1)[0]
    assert tweet_row["deleted_at"] is None
    tweet_object = store._query(expr="row_key = 'tweet_object:200'", limit=1)[0]
    assert tweet_object["deleted_at"] is None
    assert tweet_object["enrichment_state"] == "terminal_unavailable"
    assert tweet_object["enrichment_reason"] == "archive_deleted"
    assert tweet_object["enrichment_retry_eligible"] == 0
    store.close()


def test_import_x_archive_logs_progress_on_tty(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    _disable_live_reconciliation(monkeypatch)
    buffer = StringIO()
    console = Console(file=buffer, force_terminal=True, color_system=None)

    asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=console,
            debug=True,
        )
    )

    output = buffer.getvalue()
    assert "archive import: opening" in output
    assert "archive import: hashing archive contents for idempotence check..." in output
    assert "archive import: loading archive datasets..." in output
    assert "archive import hash" in output
    assert "archive import likes" in output
    assert "archive import media" in output
    assert "archive import: running follow-up reconciliation and enrichment..." in output
    assert "archive import: debug summary:" in output


def test_import_x_archive_zip_populates_archive_and_copies_media(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    archive_zip = _write_archive_zip(archive_dir, tmp_path / "archive.zip")
    _disable_live_reconciliation(monkeypatch)

    result = asyncio.run(
        import_x_archive(
            archive_zip,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    assert result.skipped is False
    assert result.counts["authored_tweets"] == 1
    assert result.counts["deleted_authored_tweets"] == 1
    assert result.counts["likes"] == 1
    assert result.counts["media_files_copied"] == 1
    assert (paths.data_dir / "media" / "100" / "3_500.jpg").exists()


def test_import_x_archive_supports_root_manifest_layout(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_root_layout_archive_dir(tmp_path)
    _disable_live_reconciliation(monkeypatch)

    result = asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    assert result.skipped is False
    assert result.counts["authored_tweets"] == 1
    assert result.counts["deleted_authored_tweets"] == 1
    assert result.counts["likes"] == 1
    assert result.counts["media_files_copied"] == 1
    assert (paths.data_dir / "media" / "100" / "3_500.jpg").exists()


def test_repeated_import_short_circuits_across_directory_and_zip_inputs(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    archive_zip = _write_archive_zip(archive_dir, tmp_path / "archive.zip")
    _disable_live_reconciliation(monkeypatch)

    first = asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )
    console = _console()
    reporter = PipelineReporter(console, "archive import", interactive=False)
    with reporter:
        second = asyncio.run(
            import_x_archive(
                archive_zip,
                enrich=False,
                config=AppConfig(),
                paths=paths,
                console=console,
            )
        )

    assert first.skipped is False
    assert second.skipped is True
    assert [step.key for step in reporter.steps] == ["archive-inspect"]
    store = open_archive_store(paths, create=False)
    assert store is not None
    assert store.counts()["import_manifests"] == 1
    store.close()


def test_repeated_import_can_reuse_existing_archive_for_enrich_followup(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    _disable_live_reconciliation(monkeypatch)

    first = asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    async def fake_reconciliation(**_kwargs):
        return ["likes"], [], _auth_bundle()

    async def fake_enrich_pending_rows(**kwargs):
        assert kwargs["limit"] is None
        return 3, 1, 2, 4

    monkeypatch.setattr(archive_import, "_run_live_reconciliation", fake_reconciliation)
    monkeypatch.setattr(archive_import, "_enrich_pending_rows", fake_enrich_pending_rows)

    second = asyncio.run(
        import_x_archive(
            archive_dir,
            enrich=True,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    assert first.skipped is False
    assert second.skipped is True
    assert second.followup_performed is True
    assert second.reconciled_collections == ["likes"]
    assert second.counts["authored_tweets"] == 1
    assert second.counts["deleted_authored_tweets"] == 1
    assert second.counts["likes"] == 1
    assert second.detail_lookups == 3
    assert second.detail_terminal_unavailable == 1
    assert second.detail_transient_failures == 2
    assert second.pending_enrichment == 4

    store = open_archive_store(paths, create=False)
    assert store is not None
    manifest_rows = store._query(expr="record_type = 'import_manifest'")
    manifest_counts = json.loads(manifest_rows[0]["counts_json"])
    assert manifest_counts["detail_lookups"] == 3
    assert manifest_counts["pending_enrichment"] == 4
    store.close()


def test_repeated_import_enrich_preserves_existing_manifest_warnings(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    _disable_live_reconciliation(monkeypatch)

    first = asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    async def fake_followup(**_kwargs):
        return archive_import.ArchiveEnrichResult(
            warnings=["detail enrichment failed: upstream 429"],
            pending_enrichment=first.pending_enrichment,
        )

    monkeypatch.setattr(archive_import, "_run_archive_followup", fake_followup)

    second = asyncio.run(
        import_x_archive(
            archive_dir,
            enrich=True,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    expected_warning = (
        "archive does not contain a bookmark dataset (expected for current official "
        "Twitter/X archives)"
    )
    assert expected_warning in second.warnings
    assert "detail enrichment failed: upstream 429" in second.warnings

    store = open_archive_store(paths, create=False)
    assert store is not None
    manifest_row = store._query(expr="record_type = 'import_manifest'", limit=1)[0]
    manifest_warnings = json.loads(manifest_row["warnings_json"])
    assert expected_warning in manifest_warnings
    assert "detail enrichment failed: upstream 429" in manifest_warnings
    store.close()


def test_enrich_imported_archive_requires_completed_import(paths) -> None:
    with pytest.raises(ConfigError, match="No completed Twitter/X archive import found"):
        asyncio.run(
            enrich_imported_archive(
                config=AppConfig(),
                paths=paths,
                console=_console(),
            )
        )


def test_enrich_imported_archive_reuses_existing_import_state(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    _disable_live_reconciliation(monkeypatch)

    asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    captured: dict[str, object] = {}
    enrichment_started_at = "2099-01-01T00:00:00+00:00"

    async def fake_reconciliation(**kwargs):
        captured["collections"] = kwargs["collections"]
        return ["tweets", "likes"], ["bulk reconciliation warning"], _auth_bundle()

    async def fake_enrich_pending_rows(**kwargs):
        captured["limit"] = kwargs["limit"]
        captured["started_at"] = kwargs["started_at"]
        return 4, 1, 2, 3

    monkeypatch.setattr(archive_import, "_run_live_reconciliation", fake_reconciliation)
    monkeypatch.setattr(archive_import, "_enrich_pending_rows", fake_enrich_pending_rows)
    monkeypatch.setattr(archive_import, "utc_now", lambda: enrichment_started_at)

    result = asyncio.run(
        enrich_imported_archive(
            limit=25,
            reconcile_live=True,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    assert captured == {
        "collections": ["tweets", "likes"],
        "limit": 25,
        "started_at": enrichment_started_at,
    }
    assert result.reconciled_collections == ["tweets", "likes"]
    assert result.warnings == ["bulk reconciliation warning"]
    assert result.detail_lookups == 4
    assert result.detail_terminal_unavailable == 1
    assert result.detail_transient_failures == 2
    assert result.pending_enrichment == 3


def test_enrich_imported_archive_can_skip_live_reconciliation(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    _disable_live_reconciliation(monkeypatch)

    asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    provided_auth = _auth_bundle()
    captured: dict[str, object] = {}

    async def fail_reconciliation(**_kwargs):
        raise AssertionError("live reconciliation should be skipped")

    async def fake_enrich_pending_rows(**kwargs):
        captured["limit"] = kwargs["limit"]
        captured["auth_token"] = kwargs["auth_bundle"].auth_token
        captured["user_id"] = kwargs["auth_bundle"].user_id
        return 5, 0, 1, 2

    monkeypatch.setattr(archive_import, "_run_live_reconciliation", fail_reconciliation)
    monkeypatch.setattr(archive_import, "_enrich_pending_rows", fake_enrich_pending_rows)

    result = asyncio.run(
        enrich_imported_archive(
            limit=7,
            reconcile_live=False,
            config=AppConfig(),
            paths=paths,
            auth_bundle=provided_auth,
            console=_console(),
        )
    )

    assert captured == {"limit": 7, "auth_token": "auth", "user_id": "42"}
    assert result.reconciled_collections == []
    assert result.warnings == []
    assert result.detail_lookups == 5
    assert result.detail_terminal_unavailable == 0
    assert result.detail_transient_failures == 1
    assert result.pending_enrichment == 2


def test_unlimited_archive_followup_selects_one_snapshot_without_limit(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    limits: list[int | None] = []
    trackers: list[object] = []

    async def fake_enrich_pending_rows(**kwargs):
        limits.append(kwargs["limit"])
        trackers.append(kwargs["absence_tracker"])
        return archive_import.ArchiveEnrichResult(
            selected=1_200,
            completed=1_200,
            detail_lookups=1_200,
            pending_enrichment=0,
        )

    monkeypatch.setattr(archive_import, "_enrich_pending_rows", fake_enrich_pending_rows)

    result = asyncio.run(
        archive_import._run_archive_followup(
            collections=[],
            detail_limit=None,
            reconcile_live=False,
            config=AppConfig(),
            paths=paths,
            auth_bundle=_auth_bundle(),
            transport=None,
            console=_console(),
        )
    )

    assert limits == [None]
    assert len(trackers) == 1
    assert result.selected == 1_200
    assert result.completed == 1_200


@pytest.mark.parametrize(
    ("limit", "expected_attempts"),
    [(None, 1_200), (200, 200)],
)
def test_enrich_pending_rows_uses_full_snapshot_or_explicit_limit(
    paths,
    monkeypatch: pytest.MonkeyPatch,
    limit: int | None,
    expected_attempts: int,
) -> None:
    total_rows = 1_200
    store = open_archive_store(paths, create=True)
    assert store is not None
    store._merge_records(
        [
            store._record(
                row_key=f"tweet_object:{tweet_id}",
                record_type="tweet_object",
                tweet_id=str(tweet_id),
                enrichment_state="pending",
                source="x_archive",
            )
            for tweet_id in range(1, total_rows + 1)
        ]
    )
    store.close()
    attempts: list[str] = []
    dirty_calls: list[tuple[int, int]] = []
    progress_totals: list[int] = []
    progress_updates: list[tuple[int, int]] = []

    @asynccontextmanager
    async def fake_locked_archive_job(*, config=None, paths=None, console=None):
        opened = open_archive_store(paths, create=False)
        assert opened is not None

        def mark_dirty(*, rows: int, batches: int) -> None:
            dirty_calls.append((rows, batches))

        try:
            yield SimpleNamespace(store=opened, mark_dirty=mark_dirty)
        finally:
            opened.close()

    @contextmanager
    def fake_progress_callback(_console, *, total: int, **_kwargs):
        progress_totals.append(total)
        yield lambda current, callback_total: progress_updates.append((current, callback_total))

    async def fake_resolve_query_ids(*_args, **_kwargs):
        return {"TweetDetail": "detail-query-id"}

    async def fake_fetch_page(*args, **_kwargs):
        _operation, variables = request_details(args[1])
        tweet_id = variables["focalTweetId"]
        attempts.append(tweet_id)
        payload = make_tweet_detail_response(
            [make_tweet_result(tweet_id, f"enriched {tweet_id}", user_id="777")]
        )
        return httpx.Response(200, json=payload, request=httpx.Request("GET", args[1]))

    class DummyClient:
        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(archive_import, "locked_archive_job", fake_locked_archive_job)
    monkeypatch.setattr(archive_import, "_progress_callback", fake_progress_callback)
    monkeypatch.setattr(archive_import, "resolve_query_ids", fake_resolve_query_ids)
    monkeypatch.setattr(
        archive_import, "build_async_client", lambda *_args, **_kwargs: DummyClient()
    )
    monkeypatch.setattr(archive_import, "fetch_page", fake_fetch_page)

    result = asyncio.run(
        archive_import._enrich_pending_rows(
            limit=limit,
            config=AppConfig(),
            paths=paths,
            auth_bundle=_auth_bundle(),
            transport=None,
            console=_console(),
        )
    )

    assert result.selected == expected_attempts
    assert result.completed == expected_attempts
    assert len(attempts) == expected_attempts
    assert len(set(attempts)) == expected_attempts
    assert progress_totals == [expected_attempts]
    assert progress_updates[-1] == (expected_attempts, expected_attempts)
    assert sum(rows for rows, _batches in dirty_calls) == expected_attempts
    store = open_archive_store(paths, create=False)
    assert store is not None
    assert store._count("record_type = 'tweet_object' AND enrichment_state = 'done'") == (
        expected_attempts
    )
    assert store._count("record_type = 'tweet_object' AND enrichment_state = 'pending'") == (
        total_rows - expected_attempts
    )
    store.close()


def test_enrich_pending_rows_uses_command_start_time_for_stable_snapshot(
    paths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started_at = "2099-01-01T00:00:00+00:00"
    store = open_archive_store(paths, create=True)
    assert store is not None
    store._merge_records(
        [
            store._record(
                row_key="tweet_object:1",
                record_type="tweet_object",
                tweet_id="1",
                enrichment_state="transient_failure",
                enrichment_next_retry_at=started_at,
            ),
            store._record(
                row_key="tweet_object:2",
                record_type="tweet_object",
                tweet_id="2",
                enrichment_state="transient_failure",
                enrichment_next_retry_at="2099-01-01T00:01:00+00:00",
            ),
        ]
    )
    store.close()
    selection_calls: list[tuple[int | None, str | None]] = []
    attempts: list[str] = []
    original_selector = archive_import.ArchiveStore.list_tweet_objects_for_enrichment

    def tracked_selector(self, *, limit=None, now=None):
        selection_calls.append((limit, now))
        return original_selector(self, limit=limit, now=now)

    @asynccontextmanager
    async def fake_locked_archive_job(*, config=None, paths=None, console=None):
        opened = open_archive_store(paths, create=False)
        assert opened is not None
        try:
            yield SimpleNamespace(store=opened, mark_dirty=lambda **_kwargs: None)
        finally:
            opened.close()

    async def fake_resolve_query_ids(*_args, **_kwargs):
        return {"TweetDetail": "detail-query-id"}

    async def fake_fetch_page(*args, **_kwargs):
        _operation, variables = request_details(args[1])
        tweet_id = variables["focalTweetId"]
        attempts.append(tweet_id)
        payload = make_tweet_detail_response([make_tweet_result(tweet_id, "available")])
        return httpx.Response(200, json=payload, request=httpx.Request("GET", args[1]))

    class DummyClient:
        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        archive_import.ArchiveStore,
        "list_tweet_objects_for_enrichment",
        tracked_selector,
    )
    monkeypatch.setattr(archive_import, "locked_archive_job", fake_locked_archive_job)
    monkeypatch.setattr(archive_import, "resolve_query_ids", fake_resolve_query_ids)
    monkeypatch.setattr(
        archive_import, "build_async_client", lambda *_args, **_kwargs: DummyClient()
    )
    monkeypatch.setattr(archive_import, "fetch_page", fake_fetch_page)

    result = asyncio.run(
        archive_import._enrich_pending_rows(
            limit=None,
            config=AppConfig(),
            paths=paths,
            auth_bundle=_auth_bundle(),
            transport=None,
            console=_console(),
            started_at=started_at,
        )
    )

    assert result.selected == 1
    assert attempts == ["1"]
    assert selection_calls == [(None, started_at)]
    store = open_archive_store(paths, create=False)
    assert store is not None
    assert store._get_row("tweet_object:1")["enrichment_state"] == "done"
    delayed = store._get_row("tweet_object:2")
    assert delayed["enrichment_state"] == "transient_failure"
    assert delayed["enrichment_next_retry_at"] == "2099-01-01T00:01:00+00:00"
    store.close()


def test_enrich_pending_rows_batches_detail_writes(paths, monkeypatch: pytest.MonkeyPatch) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    buffer = archive_import._PageBuffer()
    for index in range(12):
        tweet_id = str(1000 + index)
        store._queue_record(
            store._tweet_object_record(
                archive_import._PlaceholderTweetObject(
                    tweet_id=tweet_id,
                    text=f"placeholder {tweet_id}",
                ),
                source=archive_import.ARCHIVE_SOURCE,
                enrichment_state="pending",
                cursor=buffer,
            ),
            cursor=buffer,
        )
    archive_import._flush_buffer(store, buffer)
    store.close()
    dirty_calls: list[tuple[int, int]] = []

    @asynccontextmanager
    async def fake_locked_archive_job(*, config=None, paths=None, console=None):
        store = open_archive_store(paths, create=False)
        assert store is not None

        class _Job:
            def __init__(self, store):
                self.store = store

            def mark_dirty(self, rows: int = 1, batches: int = 1) -> None:
                dirty_calls.append((rows, batches))

        try:
            yield _Job(store)
        finally:
            store.close()

    async def fake_resolve_query_ids(*_args, **_kwargs):
        return {"TweetDetail": "detail-query-id"}

    async def fake_fetch_page(*args, **_kwargs):
        detail_url = args[1]
        _operation, variables = request_details(detail_url)
        tweet_id = variables["focalTweetId"]
        payload = make_tweet_detail_response(
            [make_tweet_result(tweet_id, f"enriched {tweet_id}", user_id="777")]
        )
        return httpx.Response(
            200,
            json=payload,
            request=httpx.Request("GET", detail_url),
        )

    class DummyClient:
        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(archive_import, "_DETAIL_ENRICH_WRITE_BATCH", 5)
    monkeypatch.setattr(archive_import, "locked_archive_job", fake_locked_archive_job)
    monkeypatch.setattr(archive_import, "resolve_query_ids", fake_resolve_query_ids)
    monkeypatch.setattr(
        archive_import, "build_async_client", lambda *_args, **_kwargs: DummyClient()
    )
    monkeypatch.setattr(archive_import, "fetch_page", fake_fetch_page)

    refreshed, terminal, transient, pending = asyncio.run(
        archive_import._enrich_pending_rows(
            limit=None,
            config=AppConfig(),
            paths=paths,
            auth_bundle=_auth_bundle(),
            transport=None,
            console=_console(),
        )
    )

    assert (refreshed, terminal, transient, pending) == (12, 0, 0, 0)
    assert dirty_calls == [(5, 1), (5, 1), (2, 1)]

    store = open_archive_store(paths, create=False)
    assert store is not None
    tweet_object_rows = store.list_tweet_objects_for_enrichment()
    store.close()
    assert tweet_object_rows == []


@pytest.mark.parametrize(
    ("message", "expected_reason", "retry_eligible", "expected_detail"),
    [
        ("These posts are protected.", "protected_account", 1, "These posts are protected."),
        ("This account is suspended.", "suspended_account", 1, "This account is suspended."),
        ("This account doesn't exist.", "account_missing", 1, "This account doesn't exist."),
        (
            "This Post was deleted by the Post author.",
            "deleted_by_author",
            0,
            "This Post was deleted by the Post author.",
        ),
        (
            "Dieses Posting ist nicht verfügbar.",
            "unavailable_unknown",
            1,
            "Dieses Posting ist nicht verfügbar.",
        ),
        (
            None,
            "unavailable_unknown",
            1,
            "TweetDetail returned an empty tweet_results object for the requested focal entry.",
        ),
    ],
)
def test_enrich_pending_rows_classifies_explicit_unavailable_shapes(
    paths,
    monkeypatch: pytest.MonkeyPatch,
    message: str | None,
    expected_reason: str,
    retry_eligible: int,
    expected_detail: str,
) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store._merge_records(
        [
            store._record(
                row_key="tweet_object:900",
                record_type="tweet_object",
                tweet_id="900",
                enrichment_state="pending",
                source="x_archive",
            )
        ]
    )
    store.close()

    @asynccontextmanager
    async def fake_locked_archive_job(*, config=None, paths=None, console=None):
        opened = open_archive_store(paths, create=False)
        assert opened is not None
        try:
            yield SimpleNamespace(store=opened, mark_dirty=lambda **_kwargs: None)
        finally:
            opened.close()

    async def fake_resolve_query_ids(*_args, **_kwargs):
        return {"TweetDetail": "detail-query-id"}

    async def fake_fetch_page(*args, **_kwargs):
        if message is None:
            payload = _empty_detail_entry_payload("tweet-900")
        else:
            payload = _detail_entry_payload(
                "conversationthread-777-tweet-900",
                {
                    "__typename": "TweetTombstone",
                    "tombstone": {"text": {"text": message}},
                },
            )
        return httpx.Response(200, json=payload, request=httpx.Request("GET", args[1]))

    class DummyClient:
        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(archive_import, "locked_archive_job", fake_locked_archive_job)
    monkeypatch.setattr(archive_import, "resolve_query_ids", fake_resolve_query_ids)
    monkeypatch.setattr(
        archive_import, "build_async_client", lambda *_args, **_kwargs: DummyClient()
    )
    monkeypatch.setattr(archive_import, "fetch_page", fake_fetch_page)

    result = asyncio.run(
        archive_import._enrich_pending_rows(
            limit=None,
            config=AppConfig(),
            paths=paths,
            auth_bundle=_auth_bundle(),
            transport=None,
            console=_console(),
        )
    )

    assert result.selected == 1
    assert result.classified_unavailable == 1
    assert result.pending_enrichment == 0
    store = open_archive_store(paths, create=False)
    assert store is not None
    row = store._get_row("tweet_object:900")
    assert row is not None
    assert row["enrichment_state"] == "terminal_unavailable"
    assert row["enrichment_reason"] == expected_reason
    assert row["enrichment_detail"] == expected_detail
    assert row["enrichment_retry_eligible"] == retry_eligible
    store.close()


def test_enrich_pending_rows_defers_one_ambiguous_focal_absence(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store._merge_records(
        [
            store._record(
                row_key="tweet_object:1",
                record_type="tweet_object",
                tweet_id="1",
                enrichment_state="pending",
            )
        ]
    )
    store.close()

    @asynccontextmanager
    async def fake_locked_archive_job(*, config=None, paths=None, console=None):
        opened = open_archive_store(paths, create=False)
        assert opened is not None
        try:
            yield SimpleNamespace(store=opened, mark_dirty=lambda **_kwargs: None)
        finally:
            opened.close()

    async def fake_resolve_query_ids(*_args, **_kwargs):
        return {"TweetDetail": "detail-query-id"}

    async def fake_fetch_page(*args, **_kwargs):
        payload = make_tweet_detail_response([make_tweet_result("999", "unrelated")])
        return httpx.Response(200, json=payload, request=httpx.Request("GET", args[1]))

    class DummyClient:
        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(archive_import, "locked_archive_job", fake_locked_archive_job)
    monkeypatch.setattr(archive_import, "resolve_query_ids", fake_resolve_query_ids)
    monkeypatch.setattr(
        archive_import, "build_async_client", lambda *_args, **_kwargs: DummyClient()
    )
    monkeypatch.setattr(archive_import, "fetch_page", fake_fetch_page)

    result = asyncio.run(
        archive_import._enrich_pending_rows(
            limit=None,
            config=AppConfig(),
            paths=paths,
            auth_bundle=_auth_bundle(),
            transport=None,
            console=_console(),
        )
    )

    assert result.transient_failures == 1
    assert result.classified_unavailable == 0
    store = open_archive_store(paths, create=False)
    assert store is not None
    row = store._get_row("tweet_object:1")
    assert row["enrichment_state"] == "transient_failure"
    assert row["enrichment_reason"] == "focal_tweet_absent"
    assert "identifiable focal result" in row["enrichment_detail"]
    assert row["enrichment_next_retry_at"] is not None
    store.close()


def test_enrich_pending_rows_aborts_after_three_absences_and_flushes_prior_rows(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store._merge_records(
        [
            store._record(
                row_key=f"tweet_object:{tweet_id}",
                record_type="tweet_object",
                tweet_id=tweet_id,
                enrichment_state="pending",
            )
            for tweet_id in ("1", "2", "3", "4", "5")
        ]
    )
    store.close()
    attempts: list[str] = []

    @asynccontextmanager
    async def fake_locked_archive_job(*, config=None, paths=None, console=None):
        opened = open_archive_store(paths, create=False)
        assert opened is not None
        try:
            yield SimpleNamespace(store=opened, mark_dirty=lambda **_kwargs: None)
        finally:
            opened.close()

    async def fake_resolve_query_ids(*_args, **_kwargs):
        return {"TweetDetail": "detail-query-id"}

    async def fake_fetch_page(*args, **_kwargs):
        _operation, variables = request_details(args[1])
        tweet_id = variables["focalTweetId"]
        attempts.append(tweet_id)
        if tweet_id == "1":
            payload = make_tweet_detail_response(
                [make_tweet_result("1", "completed before breaker", user_id="42")]
            )
        else:
            payload = make_tweet_detail_response([make_tweet_result("999", "unrelated")])
        return httpx.Response(200, json=payload, request=httpx.Request("GET", args[1]))

    class DummyClient:
        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(archive_import, "locked_archive_job", fake_locked_archive_job)
    monkeypatch.setattr(archive_import, "resolve_query_ids", fake_resolve_query_ids)
    monkeypatch.setattr(
        archive_import, "build_async_client", lambda *_args, **_kwargs: DummyClient()
    )
    monkeypatch.setattr(archive_import, "fetch_page", fake_fetch_page)

    with pytest.raises(RepeatedFocalAbsenceError):
        asyncio.run(
            archive_import._enrich_pending_rows(
                limit=None,
                config=AppConfig(),
                paths=paths,
                auth_bundle=_auth_bundle(),
                transport=None,
                console=_console(),
            )
        )

    assert attempts == ["1", "2", "3", "4"]
    store = open_archive_store(paths, create=False)
    assert store is not None
    assert store._get_row("tweet_object:1")["enrichment_state"] == "done"
    for tweet_id in ("2", "3", "4"):
        assert store._get_row(f"tweet_object:{tweet_id}")["enrichment_state"] == (
            "transient_failure"
        )
    assert store._get_row("tweet_object:5")["enrichment_state"] == "pending"
    store.close()


def test_enrich_focal_absence_counter_resets_on_explicit_empty_and_available_results(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    tweet_ids = ("1", "2", "3", "4", "5", "6", "7")
    store = open_archive_store(paths, create=True)
    assert store is not None
    store._merge_records(
        [
            store._record(
                row_key=f"tweet_object:{tweet_id}",
                record_type="tweet_object",
                tweet_id=tweet_id,
                enrichment_state="pending",
            )
            for tweet_id in tweet_ids
        ]
    )
    store.close()

    @asynccontextmanager
    async def fake_locked_archive_job(*, config=None, paths=None, console=None):
        opened = open_archive_store(paths, create=False)
        assert opened is not None
        try:
            yield SimpleNamespace(store=opened, mark_dirty=lambda **_kwargs: None)
        finally:
            opened.close()

    async def fake_resolve_query_ids(*_args, **_kwargs):
        return {"TweetDetail": "detail-query-id"}

    async def fake_fetch_page(*args, **_kwargs):
        _operation, variables = request_details(args[1])
        tweet_id = variables["focalTweetId"]
        if tweet_id == "2":
            payload = _detail_entry_payload(
                f"conversationthread-999-tweet-{tweet_id}",
                {
                    "__typename": "TweetUnavailable",
                    "reason": "This account is suspended.",
                },
            )
        elif tweet_id == "4":
            payload = make_tweet_detail_response(
                [make_tweet_result(tweet_id, "available reset", user_id="42")]
            )
        elif tweet_id == "6":
            payload = _empty_detail_entry_payload(f"tweet-{tweet_id}")
        else:
            payload = make_tweet_detail_response([make_tweet_result("999", "unrelated")])
        return httpx.Response(200, json=payload, request=httpx.Request("GET", args[1]))

    class DummyClient:
        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(archive_import, "locked_archive_job", fake_locked_archive_job)
    monkeypatch.setattr(archive_import, "resolve_query_ids", fake_resolve_query_ids)
    monkeypatch.setattr(
        archive_import, "build_async_client", lambda *_args, **_kwargs: DummyClient()
    )
    monkeypatch.setattr(archive_import, "fetch_page", fake_fetch_page)

    result = asyncio.run(
        archive_import._enrich_pending_rows(
            limit=None,
            config=AppConfig(),
            paths=paths,
            auth_bundle=_auth_bundle(),
            transport=None,
            console=_console(),
        )
    )

    assert result.selected == 7
    assert result.classified_unavailable == 2
    assert result.completed == 1
    assert result.transient_failures == 4


def test_enrich_pending_rows_flushes_when_client_close_fails(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store._merge_records(
        [
            store._record(
                row_key="tweet_object:1",
                record_type="tweet_object",
                tweet_id="1",
                enrichment_state="pending",
            )
        ]
    )
    store.close()

    @asynccontextmanager
    async def fake_locked_archive_job(*, config=None, paths=None, console=None):
        opened = open_archive_store(paths, create=False)
        assert opened is not None
        try:
            yield SimpleNamespace(store=opened, mark_dirty=lambda **_kwargs: None)
        finally:
            opened.close()

    async def fake_resolve_query_ids(*_args, **_kwargs):
        return {"TweetDetail": "detail-query-id"}

    async def fake_fetch_page(*args, **_kwargs):
        payload = make_tweet_detail_response([make_tweet_result("1", "complete")])
        return httpx.Response(200, json=payload, request=httpx.Request("GET", args[1]))

    class FailingCloseClient:
        async def aclose(self) -> None:
            raise RuntimeError("close failed")

    monkeypatch.setattr(archive_import, "locked_archive_job", fake_locked_archive_job)
    monkeypatch.setattr(archive_import, "resolve_query_ids", fake_resolve_query_ids)
    monkeypatch.setattr(
        archive_import,
        "build_async_client",
        lambda *_args, **_kwargs: FailingCloseClient(),
    )
    monkeypatch.setattr(archive_import, "fetch_page", fake_fetch_page)

    with pytest.raises(RuntimeError, match="close failed"):
        asyncio.run(
            archive_import._enrich_pending_rows(
                limit=None,
                config=AppConfig(),
                paths=paths,
                auth_bundle=_auth_bundle(),
                transport=None,
                console=_console(),
            )
        )

    store = open_archive_store(paths, create=False)
    assert store is not None
    assert store._get_row("tweet_object:1")["enrichment_state"] == "done"
    store.close()


def test_enrich_pending_rows_flushes_completed_writes_on_interrupt(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store._merge_records(
        [
            store._record(
                row_key=f"tweet_object:{tweet_id}",
                record_type="tweet_object",
                tweet_id=tweet_id,
                enrichment_state="pending",
            )
            for tweet_id in ("1", "2")
        ]
    )
    store.close()

    @asynccontextmanager
    async def fake_locked_archive_job(*, config=None, paths=None, console=None):
        store = open_archive_store(paths, create=False)
        assert store is not None
        try:
            yield SimpleNamespace(store=store, mark_dirty=lambda **_kwargs: None)
        finally:
            store.close()

    async def fake_resolve_query_ids(*_args, **_kwargs):
        return {"TweetDetail": "detail-query-id"}

    attempts = 0

    async def fake_fetch_page(*args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise KeyboardInterrupt
        payload = make_tweet_detail_response([make_tweet_result("1", "complete", user_id="42")])
        return httpx.Response(200, json=payload, request=httpx.Request("GET", args[1]))

    class DummyClient:
        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(archive_import, "locked_archive_job", fake_locked_archive_job)
    monkeypatch.setattr(archive_import, "resolve_query_ids", fake_resolve_query_ids)
    monkeypatch.setattr(
        archive_import, "build_async_client", lambda *_args, **_kwargs: DummyClient()
    )
    monkeypatch.setattr(archive_import, "fetch_page", fake_fetch_page)

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(
            archive_import._enrich_pending_rows(
                limit=None,
                config=AppConfig(),
                paths=paths,
                auth_bundle=_auth_bundle(),
                transport=None,
                console=_console(),
            )
        )

    store = open_archive_store(paths, create=False)
    assert store is not None
    assert store._get_row("tweet_object:1")["enrichment_state"] == "done"
    assert store._get_row("tweet_object:2")["enrichment_state"] == "pending"
    store.close()


def test_enrich_pending_rows_aborts_on_unexpected_parser_error_without_mutating_rows(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store._merge_records(
        [
            store._record(
                row_key=f"tweet_object:{tweet_id}",
                record_type="tweet_object",
                tweet_id=tweet_id,
                enrichment_state="pending",
            )
            for tweet_id in ("1", "2")
        ]
    )
    store.close()

    @asynccontextmanager
    async def fake_locked_archive_job(*, config=None, paths=None, console=None):
        store = open_archive_store(paths, create=False)
        assert store is not None
        try:
            yield SimpleNamespace(store=store, mark_dirty=lambda **_kwargs: None)
        finally:
            store.close()

    async def fake_resolve_query_ids(*_args, **_kwargs):
        return {"TweetDetail": "detail-query-id"}

    attempts = 0

    async def fake_fetch_page(*args, **_kwargs):
        nonlocal attempts
        attempts += 1
        payload = make_tweet_detail_response(
            [make_tweet_result("1", "would be complete", user_id="42")]
        )
        return httpx.Response(200, json=payload, request=httpx.Request("GET", args[1]))

    class DummyClient:
        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(archive_import, "locked_archive_job", fake_locked_archive_job)
    monkeypatch.setattr(archive_import, "resolve_query_ids", fake_resolve_query_ids)
    monkeypatch.setattr(
        archive_import, "build_async_client", lambda *_args, **_kwargs: DummyClient()
    )
    monkeypatch.setattr(archive_import, "fetch_page", fake_fetch_page)
    monkeypatch.setattr(
        archive_import,
        "parse_tweet_detail_response",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TypeError("parser bug")),
    )

    with pytest.raises(TypeError, match="parser bug"):
        asyncio.run(
            archive_import._enrich_pending_rows(
                limit=None,
                config=AppConfig(),
                paths=paths,
                auth_bundle=_auth_bundle(),
                transport=None,
                console=_console(),
            )
        )

    assert attempts == 1
    store = open_archive_store(paths, create=False)
    assert store is not None
    assert store._get_row("tweet_object:1")["enrichment_state"] == "pending"
    assert store._get_row("tweet_object:2")["enrichment_state"] == "pending"
    store.close()


def test_enrich_pending_rows_keeps_known_transport_errors_per_row_retryable(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store._merge_records(
        [
            store._record(
                row_key="tweet_object:1",
                record_type="tweet_object",
                tweet_id="1",
                enrichment_state="pending",
            )
        ]
    )
    store.close()

    @asynccontextmanager
    async def fake_locked_archive_job(*, config=None, paths=None, console=None):
        store = open_archive_store(paths, create=False)
        assert store is not None
        try:
            yield SimpleNamespace(store=store, mark_dirty=lambda **_kwargs: None)
        finally:
            store.close()

    async def fake_resolve_query_ids(*_args, **_kwargs):
        return {"TweetDetail": "detail-query-id"}

    async def fake_fetch_page(*args, **_kwargs):
        request = httpx.Request("GET", args[1])
        raise httpx.ConnectError("offline", request=request)

    class DummyClient:
        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(archive_import, "locked_archive_job", fake_locked_archive_job)
    monkeypatch.setattr(archive_import, "resolve_query_ids", fake_resolve_query_ids)
    monkeypatch.setattr(
        archive_import, "build_async_client", lambda *_args, **_kwargs: DummyClient()
    )
    monkeypatch.setattr(archive_import, "fetch_page", fake_fetch_page)

    result = asyncio.run(
        archive_import._enrich_pending_rows(
            limit=None,
            config=AppConfig(),
            paths=paths,
            auth_bundle=_auth_bundle(),
            transport=None,
            console=_console(),
        )
    )

    assert result.transient_failures == 1
    store = open_archive_store(paths, create=False)
    assert store is not None
    row = store._get_row("tweet_object:1")
    assert row["enrichment_state"] == "transient_failure"
    assert row["enrichment_reason"] is None
    assert row["enrichment_retry_count"] == 1
    assert row["enrichment_next_retry_at"] is not None
    store.close()


def test_archive_live_reconciliation_skips_resuming_saved_backfills(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[dict[str, object]] = []

    async def fake_sync_collection(*args, **kwargs):
        captured.append({"collection": args[0], **kwargs})
        return SimpleNamespace(pages_fetched=1, tweets_seen=1, stop_reason="duplicate")

    monkeypatch.setattr(archive_import, "sync_collection", fake_sync_collection)

    reconciled, warnings, resolved_auth = asyncio.run(
        archive_import._run_live_reconciliation(
            collections=["likes", "tweets"],
            config=AppConfig(),
            paths=paths,
            auth_bundle=_auth_bundle(),
            transport=None,
            console=_console(),
        )
    )

    assert reconciled == ["likes", "tweets"]
    assert warnings == []
    assert resolved_auth is not None
    assert [kwargs["collection"] for kwargs in captured] == ["likes", "tweets"]
    assert all(kwargs["resume_backfill"] is False for kwargs in captured)


def test_archive_import_does_not_downgrade_existing_live_tweet_object(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path, like_tweet_id="300")
    store = open_archive_store(paths, create=True)
    assert store is not None
    live_tweet = _live_tweet("300", text="live bookmark tweet")
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[live_tweet],
        last_head_tweet_id="300",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.close()
    _disable_live_reconciliation(monkeypatch)

    asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    store = open_archive_store(paths, create=False)
    assert store is not None
    tweet_object = store._query(expr="row_key = 'tweet_object:300'", limit=1)[0]
    assert tweet_object["source"] == "live_graphql"
    assert tweet_object["text"] == "live bookmark tweet"
    assert tweet_object["enrichment_state"] == "done"
    like_row = store._query(expr="row_key = 'tweet:like::300'", limit=1)[0]
    assert like_row["source"] == "x_archive"
    store.close()


def test_live_sync_can_upgrade_archive_like_placeholder_after_import(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path, like_tweet_id="300")
    _disable_live_reconciliation(monkeypatch)

    asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    store = open_archive_store(paths, create=False)
    assert store is not None
    store.persist_page(
        operation="Likes",
        collection_type="like",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[_live_tweet("300", text="live liked tweet")],
        last_head_tweet_id="300",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    tweet_object = store._query(expr="row_key = 'tweet_object:300'", limit=1)[0]
    assert tweet_object["source"] == "live_graphql"
    assert tweet_object["text"] == "live liked tweet"
    assert tweet_object["enrichment_state"] == "done"
    store.close()


def test_import_x_archive_rejects_missing_manifest(paths, tmp_path: Path) -> None:
    broken_dir = tmp_path / "broken-archive"
    broken_dir.mkdir()

    with pytest.raises(ConfigError, match="missing manifest.js"):
        asyncio.run(
            import_x_archive(
                broken_dir,
                config=AppConfig(),
                paths=paths,
                console=_console(),
            )
        )


def test_archive_input_closes_zip_when_manifest_load_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_zip = tmp_path / "broken-archive.zip"
    with zipfile.ZipFile(archive_zip, "w") as handle:
        handle.writestr("data/manifest.js", "window.__THAR_CONFIG = {}\n")

    closed: list[str] = []
    original_close = zipfile.ZipFile.close

    def tracking_close(self: zipfile.ZipFile) -> None:
        if self.fp is not None:
            closed.append(str(self.filename))
        original_close(self)

    def raise_bad_manifest(_self: archive_import._ArchiveInput) -> dict[str, object]:
        raise ConfigError("bad manifest")

    monkeypatch.setattr(zipfile.ZipFile, "close", tracking_close)
    monkeypatch.setattr(archive_import._ArchiveInput, "_load_manifest", raise_bad_manifest)

    with pytest.raises(ConfigError, match="bad manifest"):
        archive_import._ArchiveInput(archive_zip)

    assert closed == [str(archive_zip)]


def test_import_x_archive_rejects_owner_mismatch(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    store = open_archive_store(paths, create=True)
    assert store is not None
    store.ensure_archive_owner_id("84")
    store.close()
    _disable_live_reconciliation(monkeypatch)

    with pytest.raises(ArchiveOwnerMismatchError):
        asyncio.run(
            import_x_archive(
                archive_dir,
                config=AppConfig(),
                paths=paths,
                console=_console(),
            )
        )


def test_import_x_archive_rejects_parent_segments_in_manifest_paths(paths, tmp_path: Path) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    manifest_path = archive_dir / "data" / "manifest.js"
    manifest = json.loads(
        manifest_path.read_text(encoding="utf-8").removeprefix("window.__THAR_CONFIG = ")
    )
    manifest["dataTypes"]["tweets"]["files"][0]["fileName"] = "data/../../../etc/passwd"
    manifest_path.write_text(
        f"window.__THAR_CONFIG = {json.dumps(manifest, indent=2)}\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="must stay within the archive data/ directory"):
        asyncio.run(
            import_x_archive(
                archive_dir,
                config=AppConfig(),
                paths=paths,
                console=_console(),
            )
        )


def test_import_x_archive_parse_errors_include_filename(paths, tmp_path: Path) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    (archive_dir / "data" / "like.js").write_text(
        "window.YTD.like.part0 = not-json\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=r"like\.js"):
        asyncio.run(
            import_x_archive(
                archive_dir,
                config=AppConfig(),
                paths=paths,
                console=_console(),
            )
        )


def test_import_x_archive_reuses_existing_thumbnail_destination(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path, media_kind="video")
    (paths.data_dir / "media" / "100").mkdir(parents=True, exist_ok=True)
    (paths.data_dir / "media" / "100" / "7_500-poster.jpg").write_bytes(b"poster")
    _disable_live_reconciliation(monkeypatch)

    result = asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    assert result.counts["media_files_copied"] == 0
    store = open_archive_store(paths, create=False)
    assert store is not None
    media_row = store._query(expr="record_type = 'media'", limit=1)[0]
    assert media_row["download_state"] == "pending"
    assert media_row["local_path"] is None
    assert media_row["thumbnail_local_path"] == "media/100/7_500-poster.jpg"
    assert media_row["thumbnail_sha256"] is None
    assert media_row["thumbnail_byte_size"] is None
    assert store.list_media_rows(states={"pending"})[0]["row_key"] == media_row["row_key"]
    store.close()


def test_import_x_archive_preserves_main_and_thumbnail_updates_for_video_media(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(
        tmp_path,
        media_kind="video",
        include_video_main_asset=True,
    )
    _disable_live_reconciliation(monkeypatch)

    result = asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    assert result.counts["media_files_copied"] == 2
    store = open_archive_store(paths, create=False)
    assert store is not None
    media_row = store._query(expr="record_type = 'media'", limit=1)[0]
    assert media_row["local_path"] == "media/100/7_500.mp4"
    assert media_row["thumbnail_local_path"] == "media/100/7_500-poster.jpg"
    assert media_row["download_state"] == "done"
    assert (paths.data_dir / "media" / "100" / "7_500.mp4").exists()
    assert (paths.data_dir / "media" / "100" / "7_500-poster.jpg").exists()
    store.close()


def test_archive_deleted_tweet_preserves_existing_live_fields(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    store = open_archive_store(paths, create=True)
    assert store is not None
    live_tweet = _live_tweet("200", text="live deleted tweet")
    store.persist_page(
        operation="UserTweets",
        collection_type="tweet",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[live_tweet],
        last_head_tweet_id="200",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.close()
    _disable_live_reconciliation(monkeypatch)

    asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    store = open_archive_store(paths, create=False)
    assert store is not None
    tweet_row = store._query(expr="row_key = 'tweet:tweet::200'", limit=1)[0]
    assert tweet_row["source"] == "live_graphql"
    assert tweet_row["text"] == "live deleted tweet"
    assert tweet_row["deleted_at"] == "Mon Mar 16 00:00:00 +0000 2026"
    tweet_object = store._query(expr="row_key = 'tweet_object:200'", limit=1)[0]
    assert tweet_object["source"] == "live_graphql"
    assert tweet_object["text"] == "live deleted tweet"
    assert tweet_object["deleted_at"] == "Mon Mar 16 00:00:00 +0000 2026"
    store.close()


def test_import_x_archive_detail_api_errors_become_transient_failures(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)

    async def fake_reconciliation(**_kwargs):
        return [], [], _auth_bundle()

    async def fake_resolve_query_ids(*_args, **_kwargs):
        return {"TweetDetail": "detail-query-id"}

    async def fake_fetch_page(*_args, **_kwargs):
        raise APIResponseError("server error", status_code=500)

    class DummyClient:
        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(archive_import, "_run_live_reconciliation", fake_reconciliation)
    monkeypatch.setattr(archive_import, "resolve_query_ids", fake_resolve_query_ids)
    monkeypatch.setattr(
        archive_import, "build_async_client", lambda *_args, **_kwargs: DummyClient()
    )
    monkeypatch.setattr(archive_import, "fetch_page", fake_fetch_page)

    result = asyncio.run(
        import_x_archive(
            archive_dir,
            detail_lookups=1,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    assert result.detail_lookups == 0
    assert result.detail_transient_failures == 1
    assert result.pending_enrichment == 1

    store = open_archive_store(paths, create=False)
    assert store is not None
    tweet_object = store._query(expr="row_key = 'tweet_object:300'", limit=1)[0]
    assert tweet_object["enrichment_state"] == "transient_failure"
    assert tweet_object["enrichment_http_status"] == "500"
    manifest_row = store._query(expr="record_type = 'import_manifest'", limit=1)[0]
    manifest_counts = json.loads(manifest_row["counts_json"])
    assert manifest_counts["detail_transient_failures"] == 1
    store.close()


def test_import_x_archive_detail_stale_query_id_aborts_and_leaves_row_untouched(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)

    async def fake_reconciliation(**_kwargs):
        return [], [], _auth_bundle()

    async def fake_resolve_query_ids(*_args, **_kwargs):
        return {"TweetDetail": "detail-query-id"}

    async def fake_fetch_page(*_args, **_kwargs):
        raise StaleQueryIdError("stale query id", status_code=404)

    class DummyClient:
        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(archive_import, "_run_live_reconciliation", fake_reconciliation)
    monkeypatch.setattr(archive_import, "resolve_query_ids", fake_resolve_query_ids)
    monkeypatch.setattr(
        archive_import, "build_async_client", lambda *_args, **_kwargs: DummyClient()
    )
    monkeypatch.setattr(archive_import, "fetch_page", fake_fetch_page)

    with pytest.raises(archive_import.ArchiveEnrichmentAborted, match="stale query id") as exc:
        asyncio.run(
            import_x_archive(
                archive_dir,
                detail_lookups=1,
                config=AppConfig(),
                paths=paths,
                console=_console(),
            )
        )
    assert isinstance(exc.value.cause, StaleQueryIdError)

    store = open_archive_store(paths, create=False)
    assert store is not None
    tweet_object = store._query(expr="row_key = 'tweet_object:300'", limit=1)[0]
    assert tweet_object["enrichment_state"] == "pending"
    assert tweet_object["enrichment_http_status"] is None
    manifest_row = store._query(expr="record_type = 'import_manifest'", limit=1)[0]
    assert manifest_row["status"] == "completed"
    assert manifest_row["enrichment_followup_status"] == "enrichment_aborted"
    assert "stale query id" in manifest_row["enrichment_aborted_reason"]
    store.close()


def test_import_x_archive_finalizes_manifest_when_followup_is_interrupted(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)

    async def interrupt_followup(**_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(archive_import, "_run_archive_followup", interrupt_followup)

    with pytest.raises(archive_import.ArchiveEnrichmentInterrupted):
        asyncio.run(
            import_x_archive(
                archive_dir,
                config=AppConfig(),
                paths=paths,
                console=_console(),
            )
        )

    store = open_archive_store(paths, create=False)
    assert store is not None
    manifest = store._query(expr="record_type = 'import_manifest'", limit=1)[0]
    counts = json.loads(manifest["counts_json"])
    assert manifest["status"] == "completed"
    assert manifest["enrichment_followup_status"] == "enrichment_interrupted"
    assert manifest["enrichment_aborted_reason"] == "KeyboardInterrupt"
    assert counts["pending_enrichment"] == store.count_incomplete_initial_enrichment()
    assert "transient_due" in counts
    assert "transient_delayed" in counts
    store.close()


def test_import_x_archive_preserves_attempt_start_time(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    _disable_live_reconciliation(monkeypatch)
    timestamps = iter(
        [
            "2026-03-17T00:00:00Z",
            "2026-03-17T00:00:01Z",
            "2026-03-17T00:00:02Z",
            "2026-03-17T00:00:03Z",
        ]
    )
    monkeypatch.setattr(archive_import, "utc_now", lambda: next(timestamps))

    asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    store = open_archive_store(paths, create=False)
    assert store is not None
    manifest_row = store._query(expr="record_type = 'import_manifest'", limit=1)[0]
    assert manifest_row["import_started_at"] == "2026-03-17T00:00:00Z"
    assert manifest_row["import_completed_at"] == "2026-03-17T00:00:03Z"
    store.close()


def test_import_x_archive_sample_limit_does_not_require_debug(paths, tmp_path: Path) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    console = _console()
    reporter = PipelineReporter(console, "archive import", interactive=False)

    with reporter:
        result = asyncio.run(
            import_x_archive(
                archive_dir,
                sample_limit=1,
                config=AppConfig(),
                paths=paths,
                console=console,
            )
        )

    assert result.skipped is False
    assert result.followup_performed is False
    assert any("sampled import" in warning for warning in result.warnings)
    assert {step.key for step in reporter.steps} == {
        "archive-inspect",
        "archive-source",
        "archive-tweets",
        "archive-deleted",
        "archive-likes",
        "archive-media",
    }
    assert not reporter.has_step("archive-enrich")


def test_sampled_debug_import_stays_non_completed_and_full_import_can_rerun(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    _disable_live_reconciliation(monkeypatch)

    sampled = asyncio.run(
        import_x_archive(
            archive_dir,
            sample_limit=1,
            debug=True,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    assert sampled.skipped is False
    assert sampled.followup_performed is False
    assert any("sampled import" in warning for warning in sampled.warnings)

    store = open_archive_store(paths, create=False)
    assert store is not None
    manifest_row = store._query(expr="record_type = 'import_manifest'", limit=1)[0]
    assert manifest_row["status"] == "sampled"
    store.close()

    full = asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    assert full.skipped is False
    store = open_archive_store(paths, create=False)
    assert store is not None
    manifest_row = store._query(expr="record_type = 'import_manifest'", limit=1)[0]
    assert manifest_row["status"] == "completed"
    store.close()


def test_interrupted_import_marks_manifest_failed_and_rerun_reuses_archive_captures(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    _disable_live_reconciliation(monkeypatch)
    original_import_authored_tweets = archive_import._import_authored_tweets
    optimize_calls = {"count": 0}

    def abort_import(*_args, **_kwargs) -> None:
        raise KeyboardInterrupt()

    def fake_optimize(self) -> None:
        optimize_calls["count"] += 1

    monkeypatch.setattr(archive_import, "_import_authored_tweets", abort_import)
    monkeypatch.setattr(archive_import.ArchiveStore, "optimize", fake_optimize)

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(
            import_x_archive(
                archive_dir,
                config=AppConfig(),
                paths=paths,
                console=_console(),
            )
        )

    store = open_archive_store(paths, create=False)
    assert store is not None
    manifest_row = store._query(expr="record_type = 'import_manifest'", limit=1)[0]
    assert manifest_row["status"] == "failed"
    raw_capture_count = store.counts()["raw_captures"]
    store.close()

    assert optimize_calls["count"] == 0

    monkeypatch.setattr(archive_import, "_import_authored_tweets", original_import_authored_tweets)

    rerun = asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    assert rerun.skipped is False
    store = open_archive_store(paths, create=False)
    assert store is not None
    manifest_row = store._query(expr="record_type = 'import_manifest'", limit=1)[0]
    assert manifest_row["status"] == "completed"
    assert store.counts()["raw_captures"] == raw_capture_count
    store.close()


def test_import_x_archive_regen_clears_archive_rows_but_keeps_live_rows(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    _disable_live_reconciliation(monkeypatch)

    store = open_archive_store(paths, create=True)
    assert store is not None
    live_tweet = _live_tweet("900", text="existing live bookmark")
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[live_tweet],
        last_head_tweet_id="900",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.close()

    asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    store = open_archive_store(paths, create=False)
    assert store is not None
    first_raw_capture_count = store.counts()["raw_captures"]
    media_row = store._query(expr="record_type = 'media'", limit=1)[0]
    original_media_path = paths.data_dir / str(media_row["local_path"])
    stale_media_path = paths.data_dir / "media" / "100" / "stale.jpg"
    stale_media_path.parent.mkdir(parents=True, exist_ok=True)
    original_media_path.rename(stale_media_path)
    updated_media_row = dict(media_row)
    updated_media_row["local_path"] = "media/100/stale.jpg"
    store.merge_rows([updated_media_row])
    store.close()

    rerun = asyncio.run(
        import_x_archive(
            archive_dir,
            regen=True,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    assert rerun.skipped is False
    assert not stale_media_path.exists()
    assert (paths.data_dir / "media" / "100" / "3_500.jpg").exists()

    store = open_archive_store(paths, create=False)
    assert store is not None
    assert store.counts()["raw_captures"] == first_raw_capture_count
    live_row = store._query(expr="row_key = 'tweet:bookmark::900'", limit=1)[0]
    assert live_row["source"] == "live_graphql"
    assert store.counts()["import_manifests"] == 1
    store.close()


def test_remove_archive_owned_files_only_removes_media_subtree(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    media_path = data_dir / "media" / "100" / "asset.jpg"
    notes_path = data_dir / "notes" / "asset.jpg"
    media_path.parent.mkdir(parents=True, exist_ok=True)
    notes_path.parent.mkdir(parents=True, exist_ok=True)
    media_path.write_bytes(b"media")
    notes_path.write_bytes(b"notes")

    removed = archive_import._remove_archive_owned_files(
        data_dir,
        ["media/100/asset.jpg", "notes/asset.jpg", "../escape.jpg", "/tmp/escape.jpg"],
    )

    assert removed == 1
    assert not media_path.exists()
    assert notes_path.exists()
