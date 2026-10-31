import pytest

from tweetnook.core.media_metadata import validated_aspect_ratio
from tweetnook.core.timeline_parsing import TimelineTweet
from tweetnook.storage import ArchiveStore
from tweetnook.web.routes import tweets


@pytest.mark.parametrize(
    "ratio", [[10**400, 4], [4, 10**400], [float("inf"), 4], [4, float("nan")]]
)
def test_unusable_optional_aspect_ratio_is_omitted(ratio):
    assert validated_aspect_ratio(ratio) is None


def test_ingested_overflowing_geometry_still_allows_archive_and_web_reads(
    tmp_path, make_web_client
):
    raw = {
        "rest_id": "101",
        "legacy": {
            "full_text": "Readable video post",
            "extended_entities": {
                "media": [
                    {
                        "id_str": "501",
                        "media_key": "7_501",
                        "type": "video",
                        "media_url_https": "https://example.test/poster.jpg",
                        "video_info": {
                            "aspect_ratio": [10**400, 4],
                            "variants": [
                                {
                                    "content_type": "video/mp4",
                                    "url": "https://example.test/video.mp4",
                                    "bitrate": 100,
                                }
                            ],
                        },
                    }
                ]
            },
        },
    }
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    try:
        store.persist_page(
            operation="Likes",
            collection_type="like",
            cursor_in=None,
            cursor_out=None,
            http_status=200,
            raw_json={"synthetic": True},
            tweets=[
                TimelineTweet(
                    "101", "Readable video post", "42", "fixture", "Fixture", None, "101", raw
                )
            ],
            last_head_tweet_id="101",
            backfill_cursor=None,
            backfill_incomplete=False,
        )
        row = store.fetch_tweets_by_ids(["101"])[0]
        assert row["text"] == "Readable video post"
        assert row["media"][0]["type"] == "video"
        assert "aspect_ratio" not in row["media"][0]

        client = make_web_client(tweets.router, store=store)
        feed = client.get("/api/tweets", params={"collection": "likes"})
        assert feed.status_code == 200
        assert feed.json()["tweets"][0]["tweet_id"] == "101"
        assert "aspect_ratio" not in feed.json()["tweets"][0]["media"][0]
        assert client.get("/api/tweets/101/raw").status_code == 200
        assert client.get("/api/tweets/101").status_code == 200
    finally:
        store.close()
