import json

from tweetnook.storage import ArchiveStore
from tweetnook.web.routes.tweets import api_tweet_thread


def test_ambiguous_ancestors_load_and_render_one_chain_without_the_focal_cycle(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    store.merge_rows(
        [
            {
                "row_key": f"tweet_object:{tweet_id}",
                "record_type": "tweet_object",
                "tweet_id": tweet_id,
                "text": f"post {tweet_id}",
                "enrichment_state": "done",
            }
            for tweet_id in ("100", "200", "300", "400", "500")
        ]
        + [
            {
                "row_key": f"relation:{child}:{parent}",
                "record_type": "tweet_relation",
                "tweet_id": child,
                "target_tweet_id": parent,
                "relation_type": "reply_to",
            }
            for child, parent in [
                ("100", "200"),
                ("100", "300"),
                ("200", "500"),
                ("300", "400"),
                ("500", "100"),
            ]
        ]
    )
    try:
        response = json.loads(api_tweet_thread("100", store=store, _auth=True).body)
        assert [row["tweet_id"] for row in response["parents"]] == ["200", "500"]
        assert response["main"]["tweet_id"] == "100"
    finally:
        store.close()
