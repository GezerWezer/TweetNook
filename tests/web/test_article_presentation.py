from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path

from tweetnook.storage.backend import ArchiveStore
from tweetnook.web.availability import annotate_web_tweets, slim_web_tweets


def _seed(store, tweet_id, *, quote=None, retweet=None, article=False, unavailable=False):
    raw = {"rest_id": tweet_id, "legacy": {"full_text": "saved post"}}
    records = [
        dict(
            row_key=f"tweet_object:{tweet_id}",
            record_type="tweet_object",
            tweet_id=tweet_id,
            text="saved post",
            raw_json=json.dumps(raw),
            enrichment_state="terminal_unavailable" if unavailable else "done",
            enrichment_reason="deleted_by_author" if unavailable else None,
        )
    ]
    for target, kind in ((quote, "quote_of"), (retweet, "retweet_of")):
        if target:
            records.append(
                dict(
                    row_key=f"relation:{tweet_id}:{kind}:{target}",
                    record_type="tweet_relation",
                    tweet_id=tweet_id,
                    target_tweet_id=target,
                    relation_type=kind,
                )
            )
    if article:
        records.append(
            dict(
                row_key=f"article:{tweet_id}",
                record_type="article",
                tweet_id=tweet_id,
                article_id=f"a{tweet_id}",
                title="Article title",
                summary_text="Preview",
                content_text="Saved article body",
                status="body_present",
                raw_json=json.dumps(
                    {
                        "content_state": {
                            "blocks": [{"type": "unstyled", "text": "Saved article body"}],
                            "entityMap": [],
                        }
                    }
                ),
            )
        )
        for pos, source in enumerate(("article_cover", "article_media")):
            records.append(
                dict(
                    row_key=f"media:{tweet_id}:{pos}",
                    record_type="media",
                    tweet_id=tweet_id,
                    media_key=f"3_{pos}",
                    source=source,
                    article_id=f"a{tweet_id}",
                    media_type="photo",
                    position=pos,
                    media_url=f"https://example.test/{tweet_id}/{pos}.jpg",
                    local_path=f"media/{tweet_id}-{pos}.jpg",
                    raw_json=json.dumps({"media_id": str(pos)}),
                )
            )
    store._merge_records(records)


def _tweet(tweet_id):
    return {
        "tweet_id": tweet_id,
        "text": "saved post",
        "raw_json": {"rest_id": tweet_id},
        "media": [],
        "author": {"id": "1", "username": "demo"},
    }


def test_web_articles_survive_raw_slimming_and_remove_gallery_duplicates(tmp_path):
    with closing(ArchiveStore(tmp_path / "archive.db", create=True)) as store:
        _seed(store, "1", article=True)
        tweet = store.fetch_tweets_by_ids(["1"], include_objects=True)[0]
        annotate_web_tweets(store, [tweet])
        slim_web_tweets([tweet])
        assert tweet["media"] == []
        assert tweet["article"]["content_state"]["blocks"][0]["text"] == "Saved article body"
        assert [m["source"] for m in tweet["article"]["media"]] == [
            "article_cover",
            "article_media",
        ]
        assert tweet["article"]["media"][1]["media_id"] == "1"
        assert tweet["article"]["media"][0]["download"]["local_path"] == "media/1-0.jpg"


def test_articles_attach_to_quotes_nested_quotes_retweets_and_context(tmp_path):
    with closing(ArchiveStore(tmp_path / "archive.db", create=True)) as store:
        _seed(store, "1", article=True)
        _seed(store, "2", quote="1")
        _seed(store, "3", quote="2")
        _seed(store, "4", retweet="2")
        tweets = [_tweet("2"), _tweet("3"), _tweet("4")]
        annotate_web_tweets(store, tweets)
        slim_web_tweets(tweets)
        direct = tweets[0]["quoted_tweet"]
        nested = tweets[1]["quoted_tweet"]["quoted_tweet"]
        repost = tweets[2]["retweeted_tweet"]["quoted_tweet"]
        for article_tweet in (direct, nested, repost):
            assert article_tweet["article"]["title"] == "Article title"
            assert article_tweet["media"] == []
        assert tweets[0]["qt_media"] == []


def test_unavailable_article_does_not_leak_saved_body_into_placeholder(tmp_path):
    with closing(ArchiveStore(tmp_path / "archive.db", create=True)) as store:
        _seed(store, "1", article=True, unavailable=True)
        tweet = store.fetch_tweets_by_ids(["1"], include_objects=True)[0]
        annotate_web_tweets(store, [tweet])
        assert tweet["availability"]["placeholder"]
        assert "article" not in tweet


def test_article_preview_without_media_or_rich_state_remains_a_preview(tmp_path):
    with closing(ArchiveStore(tmp_path / "archive.db", create=True)) as store:
        _seed(store, "1")
        store._merge_records(
            [
                dict(
                    row_key="article:1",
                    record_type="article",
                    tweet_id="1",
                    title="Preview title",
                    summary_text="Saved excerpt",
                    status="preview_only",
                    raw_json="{}",
                )
            ]
        )
        tweet = _tweet("1")
        annotate_web_tweets(store, [tweet])
        assert tweet["article"]["content_state"] is None
        assert tweet["article"]["content_text"] is None
        assert tweet["article"]["media"] == []


def test_real_article_fixture_preserves_rich_body_and_inline_media(tmp_path):
    from tweetnook.client.timelines import parse_tweet_detail_response

    payload = json.loads(
        (Path(__file__).parents[1] / "fixtures/dimitris_article_tweet_detail.json").read_text()
    )
    focal = parse_tweet_detail_response(payload, "2026531440414925307")
    with closing(ArchiveStore(tmp_path / "archive.db", create=True)) as store:
        store.persist_tweet_detail(tweet=focal.tweet, raw_json=payload, http_status=200)
        tweet = store.fetch_tweets_by_ids(["2026531440414925307"], include_objects=True)[0]
        annotate_web_tweets(store, [tweet])
        slim_web_tweets([tweet])
        assert tweet["media"] == []
        assert len(tweet["article"]["content_text"]) == 17308
        assert len(tweet["article"]["content_state"]["blocks"]) == 115
        assert len(tweet["article"]["media"]) == 10
        assert all(item["media_id"] for item in tweet["article"]["media"])
