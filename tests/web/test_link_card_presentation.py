from __future__ import annotations

from contextlib import closing

from tests.conftest import make_link_card, make_photo_media, make_tweet_result
from tweetnook.extractor import extract_secondary_objects
from tweetnook.search import search_posts
from tweetnook.storage.backend import ArchiveStore, _PageBuffer
from tweetnook.web.availability import annotate_web_tweets, slim_web_tweets


def test_link_card_media_survives_feed_thread_quote_and_retweet_presentation(
    tmp_path, make_web_client
):
    raw = make_tweet_result("1", "link", media=[make_photo_media("photo", "https://x.test/photo")])
    raw["card"] = make_link_card()
    quote = make_tweet_result("2", "quote", quoted_tweet=raw)
    nested = make_tweet_result("3", "nested", quoted_tweet=quote)
    repost = make_tweet_result("4", "repost", retweeted_tweet=raw)
    with closing(ArchiveStore(tmp_path / "archive.db", create=True)) as store:
        buffer = _PageBuffer()
        for tweet in (raw, quote, nested, repost):
            store._buffer_secondary_graph(extract_secondary_objects(tweet), cursor=buffer)
        store._merge_records(list(buffer.records.values()))
        cards = [row for row in store.list_media_rows() if row["source"] == "link_card"]
        assert len(cards) == 1
        store.merge_rows(
            [
                dict(
                    store._get_row(cards[0]["row_key"]),
                    local_path="media/1/card.jpg",
                    download_state="done",
                )
            ]
        )
        tweets = store.fetch_tweets_by_ids(["1", "2", "3", "4"], include_objects=True)
        annotate_web_tweets(store, tweets)
        slim_web_tweets(tweets)
        by_id = {tweet["tweet_id"]: tweet for tweet in tweets}
        nodes = [
            by_id["1"],
            by_id["2"]["quoted_tweet"],
            by_id["3"]["quoted_tweet"]["quoted_tweet"],
            by_id["4"]["retweeted_tweet"],
        ]
        for node in nodes:
            assert node["card_media"][0]["download"]["local_path"] == "media/1/card.jpg"
            assert len(node["media"]) == 1
            assert node["media"][0]["url"] == "https://x.test/photo"
            assert node["raw_json"]["card"]["legacy"]["name"] == "summary_large_image"
        assert len(by_id["2"]["qt_media"]) == 1

        from tweetnook.web.routes.tweets import router

        client = make_web_client(router, store=store)
        response = client.get("/api/tweets/1")
        assert response.status_code == 200
        main = response.json()["main"]
        assert len(main["media"]) == 1
        assert main["card_media"][0]["download"]["local_path"] == "media/1/card.jpg"


def test_link_card_images_do_not_match_tweet_media_filters_and_survive_deletion(tmp_path):
    with closing(ArchiveStore(tmp_path / "archive.db", create=True)) as store:
        raw = make_tweet_result("1", "link only")
        raw["card"] = make_link_card()
        buffer = _PageBuffer()
        store._buffer_secondary_graph(extract_secondary_objects(raw), cursor=buffer)
        store._merge_records(list(buffer.records.values()))
        store.merge_rows(
            [
                store._record(
                    row_key="tweet:bookmark::1",
                    record_type="tweet",
                    tweet_id="1",
                    collection_type="bookmark",
                    text="link only",
                    raw_json=store._json_value(raw),
                )
            ]
        )
        object_row = store._get_row("tweet_object:1")
        store.merge_rows([dict(object_row, enrichment_state="done")])
        assert store.get_eligible_tagging_candidates()[0]["content_type"] == "text"
        assert [row["tweet_id"] for row in search_posts(store, "").rows] == ["1"]
        for query in ("has:media", "has:image", "filter:images", "filter:media"):
            assert search_posts(store, query).rows == []
        row = store._get_row("tweet_object:1")
        store.merge_rows(
            [
                dict(
                    row,
                    enrichment_state="terminal_unavailable",
                    enrichment_reason="deleted_by_author",
                )
            ]
        )
        tweet = store.fetch_tweets_by_ids(["1"], include_objects=True)[0]
        annotate_web_tweets(store, [tweet])
        assert tweet["availability"]["placeholder"] is False
        assert tweet["media"] == []
        assert tweet["card_media"]
