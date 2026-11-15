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


def test_a_reply_under_a_large_gallery_keeps_its_downloaded_media(
    tmp_path, make_web_client
) -> None:
    # The parent's twenty photos once filled a ten-per-post row cap on the whole thread,
    # leaving the reply none, so it fell back to its payload's undownloaded media.
    def photos(tweet_id: str, count: int) -> list[dict[str, object]]:
        return [
            make_photo_media(f"3_{tweet_id}{index:02d}", f"https://x.test/{tweet_id}/{index}.jpg")
            for index in range(count)
        ]

    parent = make_tweet_result("1", "gallery", media=photos("1", 20))
    reply = make_tweet_result("2", "reply", media=photos("2", 8), in_reply_to_status_id="1")
    with closing(ArchiveStore(tmp_path / "archive.db", create=True)) as store:
        persist_like_page(store, parent, reply)
        store._merge_records(
            [
                store._record(
                    row_key="tweet_relation:2:reply_to:1",
                    record_type="tweet_relation",
                    tweet_id="2",
                    relation_type="reply_to",
                    target_tweet_id="1",
                )
            ]
        )
        assert store.ancestor_chain("2") == ["1"]
        store.merge_rows(
            [
                dict(row, local_path=f"media/{row['row_key']}.jpg", download_state="done")
                for row in store._query("record_type = 'media'")
            ]
        )

        from tweetnook.web.routes.tweets import router

        response = make_web_client(router, store=store).get("/api/tweets/2")
        assert response.status_code == 200
        media = response.json()["main"]["media"]
        assert [item["url"] for item in media] == [f"https://x.test/2/{i}.jpg" for i in range(8)]
        assert all(item["download"]["local_path"] for item in media)
