import sqlite3

import pytest

from tweetnook.core.timeline_parsing import TimelineTweet
from tweetnook.storage import ArchiveStore
from tweetnook.storage.ingestion import ImportObservation
from tweetnook.storage.records import PageBuffer


def test_failed_flush_retains_the_batch_and_rolls_back_its_real_first_write(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    batch = PageBuffer()
    tweet = TimelineTweet(
        tweet_id="42",
        text="saved",
        author_id=None,
        author_username=None,
        author_display_name=None,
        created_at=None,
        sort_index=None,
        raw_json={"rest_id": "42", "legacy": {"full_text": "saved"}},
    )
    store.persist_tweet_detail(tweet=tweet, raw_json={"synthetic": True}, cursor=batch)
    store.conn.execute(
        "CREATE TEMP TRIGGER reject_object_after_capture BEFORE INSERT ON archive "
        "WHEN new.record_type='tweet_object' AND EXISTS "
        "(SELECT 1 FROM archive WHERE record_type='raw_capture') "
        "BEGIN SELECT RAISE(ABORT, 'after capture write'); END"
    )
    try:
        with pytest.raises(sqlite3.IntegrityError, match="after capture write"):
            store.flush(batch)
        store.set_tweet_hidden("unrelated", True)
        assert store.counts()["raw_captures"] == store.counts()["tweet_objects"] == 0
        store.conn.execute("DROP TRIGGER reject_object_after_capture")
        assert store.flush(batch) == 2
        assert store.flush(batch) == 0
        assert store.counts()["raw_captures"] == store.counts()["tweet_objects"] == 1
    finally:
        store.close()


@pytest.mark.parametrize("membership", ["tweet", "like"])
def test_import_batch_rolls_back_after_membership_write_and_retries(tmp_path, membership):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    tweet = TimelineTweet(
        tweet_id="42",
        text="imported",
        author_id=None,
        author_username=None,
        author_display_name=None,
        created_at=None,
        sort_index="42",
        raw_json={"rest_id": "42", "legacy": {"full_text": "imported"}},
    )
    store.conn.execute(
        "CREATE TEMP TRIGGER reject_import_object BEFORE INSERT ON archive "
        "WHEN new.record_type='tweet_object' AND EXISTS "
        "(SELECT 1 FROM archive WHERE record_type='tweet' AND tweet_id='42') "
        "BEGIN SELECT RAISE(ABORT, 'after membership write'); END"
    )
    try:
        with pytest.raises(sqlite3.IntegrityError, match="after membership write"):
            store.ingest_import_batch([ImportObservation(tweet)], membership=membership)
        store.set_tweet_hidden("unrelated", True)
        assert store.counts()["tweets"] == store.counts()["tweet_objects"] == 0
        store.conn.execute("DROP TRIGGER reject_import_object")
        assert store.ingest_import_batch([ImportObservation(tweet)], membership=membership) == 2
        assert store.list_membership_tweet_ids() == ["42"]
        assert store.counts()["tweet_objects"] == 1
    finally:
        store.close()


def test_current_objects_include_pending_content_and_lifecycle_before_flush(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    batch = PageBuffer()
    tweet = TimelineTweet(
        tweet_id="42",
        text="pending content",
        author_id=None,
        author_username=None,
        author_display_name=None,
        created_at=None,
        sort_index=None,
        raw_json={"rest_id": "42", "legacy": {"full_text": "pending content"}},
    )
    try:
        store.persist_tweet_detail(tweet=tweet, raw_json={"synthetic": True}, cursor=batch)
        assert store.current_objects(["42"]) == {}
        assert store.current_objects(["42"], cursor=batch)["42"]["text"] == "pending content"
        assert store.flush(batch) == 2
        store.update_tweet_object_enrichment(
            "42",
            enrichment_state="terminal_unavailable",
            enrichment_checked_at="2026-01-01T00:00:00Z",
            enrichment_http_status=404,
            enrichment_reason="not_found",
            cursor=batch,
        )
        assert store.current_objects(["42"])["42"]["enrichment_state"] == "done"
        assert (
            store.current_objects(["42"], cursor=batch)["42"]["enrichment_state"]
            == "terminal_unavailable"
        )
        store.flush(batch)
        assert store.current_objects(["42"])["42"]["enrichment_state"] == "terminal_unavailable"
    finally:
        store.close()


def test_import_batch_extracts_a_missing_graph_once(tmp_path, monkeypatch):
    import tweetnook.storage.ingestion as ingestion

    calls = []
    extract = ingestion.extract_secondary_objects
    monkeypatch.setattr(
        ingestion,
        "extract_secondary_objects",
        lambda raw: (calls.append(raw), extract(raw))[1],
    )
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    tweet = TimelineTweet(
        tweet_id="42",
        text="imported",
        author_id=None,
        author_username=None,
        author_display_name=None,
        created_at=None,
        sort_index="1",
        raw_json={"rest_id": "42", "legacy": {"full_text": "imported"}},
    )
    try:
        store.ingest_import_batch([ImportObservation(tweet)], membership="tweet")
        assert len(calls) == 1
        assert store.counts()["tweets"] == 1
    finally:
        store.close()
