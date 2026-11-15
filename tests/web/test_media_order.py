"""A post's media reach the feed and the detail view in the order the post lists them."""

from __future__ import annotations

from contextlib import closing

from tests.conftest import make_photo_media, make_tweet_result
from tests.storage.test_field_ownership import persist_like_page
from tweetnook.storage.backend import ArchiveStore


def test_a_gallery_of_more_than_ten_photos_keeps_all_items_in_post_order(
    tmp_path, make_web_client
) -> None:
    # Positions are stored as text, so "10" sorting before "2" is what this guards; the
    # media ids fall as positions rise so neither key nor id order can pass by accident.
    urls = [f"https://pbs.twimg.com/media/photo-{index}.jpg" for index in range(12)]
    media = [make_photo_media(f"3_{900 - index}", url) for index, url in enumerate(urls)]
    with closing(ArchiveStore(tmp_path / "archive.db", create=True)) as store:
        persist_like_page(store, make_tweet_result("1", "gallery", media=media))

        feed = store.fetch_tweets_by_ids(["1"], include_objects=True)[0]
        assert [item["url"] for item in feed["media"]] == urls

        from tweetnook.web.routes.tweets import router

        response = make_web_client(router, store=store).get("/api/tweets/1")
        assert response.status_code == 200
        assert [item["url"] for item in response.json()["main"]["media"]] == urls
