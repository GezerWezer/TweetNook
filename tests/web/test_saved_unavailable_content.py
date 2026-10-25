from __future__ import annotations

import json
from contextlib import closing

import pytest

from tweetnook.search import count_posts, search_posts
from tweetnook.storage.backend import UNCAPTURED_TWEET_IDS_SQL, ArchiveStore
from tweetnook.web.availability import UNAVAILABLE_MESSAGES, annotate_web_tweets, slim_web_tweets


def _save(store, tweet_id, *, text="Saved body", reason=None, media=False, article=False):
    records = [
        dict(
            row_key=f"tweet_object:{tweet_id}",
            record_type="tweet_object",
            tweet_id=tweet_id,
            text=text,
            raw_json=json.dumps({"rest_id": tweet_id, "legacy": {"full_text": text}})
            if text
            else None,
            enrichment_state="terminal_unavailable" if reason else "done",
            enrichment_reason=reason,
        )
    ]
    if media:
        records.append(
            dict(
                row_key=f"media:{tweet_id}:photo",
                record_type="media",
                tweet_id=tweet_id,
                media_key="photo",
                source="tweet",
                media_type="photo",
                position=0,
                media_url=f"https://example.test/{tweet_id}.jpg",
                local_path=f"media/{tweet_id}.jpg",
                download_state="done",
            )
        )
    if article:
        records.append(
            dict(
                row_key=f"article:{tweet_id}",
                record_type="article",
                tweet_id=tweet_id,
                article_id=f"article-{tweet_id}",
                title="Saved article",
                content_text="Saved article body",
                status="body_present",
                raw_json="{}",
            )
        )
    store._merge_records(records)


def _wrapper(store, target, relation, *, tombstone=False, reason=None, embedded=None):
    _save(store, "wrapper", reason=reason)
    store._merge_records(
        [
            dict(
                row_key=f"relation:wrapper:{relation}:{target}",
                record_type="tweet_relation",
                tweet_id="wrapper",
                target_tweet_id=target,
                relation_type=relation,
            )
        ]
    )
    tweet = store.fetch_tweets_by_ids(["wrapper"], include_objects=True)[0]
    if tombstone:
        embedded = {"__typename": "TweetTombstone", "tombstone": {"text": {"text": "Gone"}}}
    if embedded is not None:
        if relation == "quote_of":
            tweet["raw_json"]["quoted_status_result"] = {"result": embedded}
        else:
            tweet["raw_json"]["legacy"]["retweeted_status_result"] = {"result": embedded}
        row = store._get_row("tweet_object:wrapper")
        store.merge_rows([dict(row, raw_json=json.dumps(tweet["raw_json"]))])
    return tweet


@pytest.mark.parametrize("reason", UNAVAILABLE_MESSAGES)
def test_saved_text_and_media_survive_every_unavailable_reason(tmp_path, reason):
    with closing(ArchiveStore(tmp_path / "archive.db", create=True)) as store:
        _save(store, "saved", reason=reason, media=True)
        tweet = store.fetch_tweets_by_ids(["saved"], include_objects=True)[0]
        annotate_web_tweets(store, [tweet])
        slim_web_tweets([tweet])
        assert tweet["availability"]["state"] == "unavailable"
        assert tweet["availability"]["reason"] == reason
        assert tweet["availability"]["placeholder"] is False
        assert tweet["text"] == "Saved body"
        assert tweet["media"][0]["download"]["local_path"] == "media/saved.jpg"


@pytest.mark.parametrize("relation", [None, "quote_of", "retweet_of"])
@pytest.mark.parametrize("content", ["media", "article"])
@pytest.mark.parametrize("reason", ["suspended_account", "deleted_by_author", "archive_deleted"])
def test_saved_content_without_post_text_survives_in_main_and_attached_posts(
    tmp_path, relation, content, reason
):
    with closing(ArchiveStore(tmp_path / "archive.db", create=True)) as store:
        _save(store, "saved", text="", reason=reason, **{content: True})
        tweet = (
            _wrapper(store, "saved", relation)
            if relation
            else store.fetch_tweets_by_ids(["saved"], include_objects=True)[0]
        )
        annotate_web_tweets(store, [tweet])
        slim_web_tweets([tweet])
        saved = (
            tweet[{"quote_of": "quoted_tweet", "retweet_of": "retweeted_tweet"}[relation]]
            if relation
            else tweet
        )
        assert saved["availability"]["placeholder"] is False
        assert saved["availability"]["reason"] == reason
        if content == "media":
            assert saved["media"][0]["download"]["local_path"] == "media/saved.jpg"
        else:
            assert saved["article"]["content_text"] == "Saved article body"


@pytest.mark.parametrize("relation", ["quote_of", "retweet_of"])
@pytest.mark.parametrize("reason", ["suspended_account", "deleted_by_author"])
def test_stale_embedded_tombstone_uses_saved_terminal_content(tmp_path, relation, reason):
    with closing(ArchiveStore(tmp_path / "archive.db", create=True)) as store:
        _save(store, "saved", reason=reason, media=True, article=True)
        tweet = _wrapper(store, "saved", relation, tombstone=True)
        annotate_web_tweets(store, [tweet])
        saved = tweet["quoted_tweet" if relation == "quote_of" else "retweeted_tweet"]
        assert saved["availability"]["placeholder"] is False
        assert saved["text"] == "Saved body"
        assert saved["article"]["content_text"] == "Saved article body"


@pytest.mark.parametrize("relation", ["quote_of", "retweet_of"])
def test_deleted_parent_keeps_available_saved_target(tmp_path, relation):
    with closing(ArchiveStore(tmp_path / "archive.db", create=True)) as store:
        _save(store, "saved", article=True)
        tweet = _wrapper(store, "saved", relation, reason="deleted_by_author")
        annotate_web_tweets(store, [tweet])
        assert tweet["availability"]["placeholder"] is False
        target = tweet["quoted_tweet" if relation == "quote_of" else "retweeted_tweet"]
        assert target["availability"]["state"] == "available"
        assert target["article"]["content_text"] == "Saved article body"


def test_terminal_canonical_copy_replaces_stale_membership_tombstone(tmp_path):
    with closing(ArchiveStore(tmp_path / "archive.db", create=True)) as store:
        _save(store, "saved", reason="deleted_by_author")
        tweet = {
            "tweet_id": "saved",
            "text": "This Post is from a suspended account. {learnmore}",
            "raw_json": {"__typename": "TweetTombstone"},
            "media": [],
        }
        annotate_web_tweets(store, [tweet])
        assert tweet["text"] == "Saved body"
        assert tweet["availability"]["placeholder"] is False
        assert tweet["availability"]["reason"] == "deleted_by_author"


@pytest.mark.parametrize("relation", ["quote_of", "retweet_of"])
def test_saved_embedded_body_survives_a_canonical_row_without_content(tmp_path, relation):
    with closing(ArchiveStore(tmp_path / "archive.db", create=True)) as store:
        _save(store, "saved", text="", reason="deleted_by_author")
        embedded = {
            "__typename": "Tweet",
            "rest_id": "saved",
            "legacy": {"full_text": "Embedded body"},
        }
        tweet = _wrapper(store, "saved", relation, embedded=embedded)
        annotate_web_tweets(store, [tweet])
        target = tweet["quoted_tweet" if relation == "quote_of" else "retweeted_tweet"]
        assert target["text"] == "Embedded body"
        assert target["availability"]["placeholder"] is False
        assert target["availability"]["reason"] == "deleted_by_author"


@pytest.mark.parametrize("reason", UNAVAILABLE_MESSAGES)
def test_uncaptured_unavailable_posts_remain_placeholders(tmp_path, reason):
    with closing(ArchiveStore(tmp_path / "archive.db", create=True)) as store:
        _save(store, "missing", text="", reason=reason)
        tweet = store.fetch_tweets_by_ids(["missing"], include_objects=True)[0]
        annotate_web_tweets(store, [tweet])
        assert tweet["availability"]["placeholder"] is True
        assert tweet["media"] == []
        assert not tweet.get("article")


@pytest.mark.parametrize("content", ["media", "article"])
def test_saved_secondary_content_remains_in_default_feed_and_search(
    tmp_path, make_web_client, content
):
    with closing(ArchiveStore(tmp_path / "archive.db", create=True)) as store:
        _save(store, "saved", text="", reason="deleted_by_author", **{content: True})
        _save(store, "missing", text="", reason="deleted_by_author")
        store._merge_records(
            [
                dict(
                    row_key=f"tweet:bookmark::{tweet_id}",
                    record_type="tweet",
                    tweet_id=tweet_id,
                    collection_type="bookmark",
                    text="",
                )
                for tweet_id in ("saved", "missing")
            ]
        )
        assert [tweet["tweet_id"] for tweet in search_posts(store, "").rows] == ["saved"]
        assert count_posts(store, "") == 1
        assert {tweet["tweet_id"] for tweet in store.export_rows("bookmark")} == {
            "saved",
            "missing",
        }
        assert [tweet["tweet_id"] for tweet in search_posts(store, "is:uncaptured").rows] == [
            "missing"
        ]
        from tweetnook.web.routes.tweets import router

        client = make_web_client(router, store=store)
        response = client.get("/api/tweets")
        assert response.status_code == 200
        assert [tweet["tweet_id"] for tweet in response.json()["tweets"]] == ["saved"]
        detail = client.get("/api/tweets/saved")
        assert detail.status_code == 200
        saved = detail.json()["main"]
        assert saved["availability"]["placeholder"] is False
        if content == "article":
            assert saved["article"]["content_text"] == "Saved article body"
        else:
            assert saved["media"][0]["download"]["local_path"] == "media/saved.jpg"
        plan = " ".join(
            row[3] for row in store.conn.execute("EXPLAIN QUERY PLAN " + UNCAPTURED_TWEET_IDS_SQL)
        )
        assert "SEARCH saved USING INDEX idx_archive_tweet_id" in plan
        # Secondary record removal must immediately restore uncaptured filtering.
        with store.conn:
            store.conn.execute(
                "DELETE FROM archive WHERE tweet_id = 'saved' AND record_type = ?", (content,)
            )
        assert search_posts(store, "").rows == []
        assert count_posts(store, "is:uncaptured") == 2
