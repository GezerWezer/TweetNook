from __future__ import annotations

import json
import struct
from pathlib import Path

import httpx
import pytest

from tests.conftest import make_tweet_result
from tweetnook.core.timeline_parsing import TimelineTweet
from tweetnook.enrichment.media import download_media
from tweetnook.storage import open_archive_store

JPEG = (Path(__file__).resolve().parent.parent / "demo/demo media/avatars/1.jpg").read_bytes()
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + struct.pack(">II", 2, 2) + bytes(9)


def _avatar_url(user_id: str) -> str:
    return f"https://pbs.twimg.com/profile_images/{user_id}/face_normal.jpg"


def _with_avatar(payload: dict[str, object], user_id: str) -> dict[str, object]:
    user = payload["core"]["user_results"]["result"]  # type: ignore[index]
    user["legacy"]["profile_image_url_https"] = _avatar_url(user_id)  # type: ignore[index]
    return payload


def _result(tweet_id: str, user_id: str, **kwargs: object) -> dict[str, object]:
    return _with_avatar(
        make_tweet_result(tweet_id, f"post {tweet_id}", user_id=user_id, **kwargs), user_id
    )


def _timeline_tweet(raw: dict[str, object], author_id: str) -> TimelineTweet:
    return TimelineTweet(
        tweet_id=str(raw["rest_id"]),
        text="saved",
        author_id=author_id,
        author_username=f"user{author_id}",
        author_display_name=f"User {author_id}",
        created_at="Sat Mar 14 00:00:00 +0000 2026",
        sort_index="10",
        raw_json=raw,
    )


def _seed(paths) -> None:
    """Saved bookmark by 1000 quoting 2000, saved like by 4000 retweeting 5000, and a reply
    by 3000 that exists only as conversation context."""
    store = open_archive_store(paths, create=True)
    assert store is not None
    bookmark = _result("100", "1000", quoted_tweet=_result("200", "2000"))
    like = _result("400", "4000", retweeted_tweet=_result("500", "5000"))
    for operation, collection, tweet in (
        ("Bookmarks", "bookmark", _timeline_tweet(bookmark, "1000")),
        ("Likes", "like", _timeline_tweet(like, "4000")),
    ):
        store.persist_page(
            operation=operation,
            collection_type=collection,
            cursor_in=None,
            cursor_out=None,
            http_status=200,
            raw_json={},
            tweets=[tweet],
            last_head_tweet_id=tweet.tweet_id,
            backfill_cursor=None,
            backfill_incomplete=False,
        )
    store.merge_rows(
        [
            store._record(
                row_key="tweet_object:300",
                record_type="tweet_object",
                tweet_id="300",
                author_id="3000",
                source="live",
                raw_json=store._json_value(_result("300", "3000", in_reply_to_status_id="100")),
            )
        ]
    )
    store.close()


def _avatars_dir(paths) -> Path:
    return paths.media_dir / "avatars"


def _recapture(paths, tweet_id: str, user_id: str, image: str) -> str:
    """Save a later capture of `user_id` showing a different profile picture."""
    url = f"https://pbs.twimg.com/profile_images/{user_id}/{image}_normal.jpg"
    payload = _result(tweet_id, user_id)
    payload["core"]["user_results"]["result"]["legacy"]["profile_image_url_https"] = url  # type: ignore[index]
    store = open_archive_store(paths, create=False)
    try:
        store.merge_rows(
            [
                store._record(
                    row_key=f"tweet_object:{tweet_id}",
                    record_type="tweet_object",
                    tweet_id=tweet_id,
                    author_id=user_id,
                    source="live",
                    last_seen_at="2999-01-01T00:00:00Z",
                    raw_json=store._json_value(payload),
                )
            ]
        )
    finally:
        store.close()
    return url


def _state(paths) -> dict[str, object]:
    return json.loads(paths.core_avatar_state_file.read_text())


def test_core_authors_are_saved_posts_and_what_they_quote_or_retweet(paths) -> None:
    _seed(paths)
    store = open_archive_store(paths, create=False)
    try:
        assert store.core_author_ids() == ["1000", "2000", "4000", "5000"]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_sync_media_step_downloads_core_avatars_only(paths, config) -> None:
    _seed(paths)
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"})

    result = await download_media(
        config=config, paths=paths, transport=httpx.MockTransport(handler)
    )

    core_dir = _avatars_dir(paths) / "core"
    assert sorted(path.name for path in core_dir.iterdir()) == [
        "1000.jpg",
        "2000.jpg",
        "4000.jpg",
        "5000.jpg",
    ]
    assert (core_dir / "1000.jpg").read_bytes() == JPEG
    assert result.avatars_downloaded == 4
    assert result.avatars_failed == 0
    # The large variant is tried first, as the on-demand route does.
    assert "https://pbs.twimg.com/profile_images/1000/face_400x400.jpg" in requested
    assert not any("/3000/" in url for url in requested)
    assert not list(_avatars_dir(paths).glob("3000.*"))

    requested.clear()
    repeat = await download_media(
        config=config, paths=paths, transport=httpx.MockTransport(handler)
    )
    assert repeat.avatars_downloaded == 0
    assert not any("profile_images" in url for url in requested)


@pytest.mark.asyncio
async def test_core_avatar_already_in_the_cache_moves_instead_of_downloading(paths, config) -> None:
    _seed(paths)
    cached = _avatars_dir(paths) / "2000.jpg"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(JPEG)
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"})

    await download_media(config=config, paths=paths, transport=httpx.MockTransport(handler))

    assert not cached.exists()
    assert (_avatars_dir(paths) / "core" / "2000.jpg").read_bytes() == JPEG
    assert not any("/2000/" in url for url in requested)


@pytest.mark.asyncio
async def test_unavailable_core_avatar_waits_for_a_new_capture(paths, config) -> None:
    _seed(paths)
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if "/2000/" in str(request.url):
            return httpx.Response(404)
        if "/5000/" in str(request.url):
            raise httpx.ConnectError("offline")
        return httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"})

    first = await download_media(config=config, paths=paths, transport=httpx.MockTransport(handler))
    assert first.avatars_downloaded == 2
    assert first.avatars_failed == 2
    # The 404 is remembered; the network failure is not.
    assert _state(paths) == {
        "saved": {"1000": _avatar_url("1000"), "4000": _avatar_url("4000")},
        "unavailable": {"2000": _avatar_url("2000")},
    }

    requested.clear()
    second = await download_media(
        config=config, paths=paths, transport=httpx.MockTransport(handler)
    )
    assert not any("/2000/" in url for url in requested)
    assert any("/5000/" in url for url in requested)
    assert second.avatars_failed == 1

    _recapture(paths, "201", "2000", "new")
    requested.clear()
    await download_media(config=config, paths=paths, transport=httpx.MockTransport(handler))
    assert "https://pbs.twimg.com/profile_images/2000/new_400x400.jpg" in requested


@pytest.mark.asyncio
async def test_core_avatars_download_even_when_on_demand_fetching_is_off(paths, config) -> None:
    _seed(paths)
    config.web.fetch_avatars = False

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"})

    result = await download_media(
        config=config, paths=paths, transport=httpx.MockTransport(handler)
    )

    assert result.avatars_downloaded == 4


@pytest.mark.asyncio
async def test_core_avatar_refreshes_when_a_newer_picture_is_captured(paths, config) -> None:
    _seed(paths)
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if "/1000/new" in str(request.url):
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
        if "/4000/gone" in str(request.url):
            return httpx.Response(404)
        return httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"})

    await download_media(config=config, paths=paths, transport=httpx.MockTransport(handler))
    new_url = _recapture(paths, "101", "1000", "new")
    gone_url = _recapture(paths, "401", "4000", "gone")
    requested.clear()

    result = await download_media(
        config=config, paths=paths, transport=httpx.MockTransport(handler)
    )

    core_dir = _avatars_dir(paths) / "core"
    # The changed format replaces the old file rather than sitting behind it.
    assert (core_dir / "1000.png").read_bytes() == PNG
    assert not (core_dir / "1000.jpg").exists()
    # A refresh that finds nothing keeps the picture already saved.
    assert (core_dir / "4000.jpg").read_bytes() == JPEG
    assert result.avatars_downloaded == 1
    assert result.avatars_failed == 1
    assert not any("/2000/" in url or "/5000/" in url for url in requested)
    state = _state(paths)
    assert state["saved"]["1000"] == new_url  # type: ignore[index]
    assert state["unavailable"] == {"4000": gone_url}

    requested.clear()
    await download_media(config=config, paths=paths, transport=httpx.MockTransport(handler))
    assert not any("profile_images" in url for url in requested)


@pytest.mark.asyncio
async def test_avatars_of_authors_no_longer_core_return_to_the_cache(paths, config) -> None:
    _seed(paths)
    core_dir = _avatars_dir(paths) / "core"
    core_dir.mkdir(parents=True)
    (core_dir / "9999.jpg").write_bytes(JPEG)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"})

    await download_media(config=config, paths=paths, transport=httpx.MockTransport(handler))

    assert not (core_dir / "9999.jpg").exists()
    assert (_avatars_dir(paths) / "9999.jpg").read_bytes() == JPEG
