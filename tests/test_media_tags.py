from __future__ import annotations

import json

from tweetnook.storage import open_archive_store


def _tag_payload(store, tweet_id: str) -> dict[str, object] | None:
    row = store.conn.execute(
        "SELECT raw_json FROM archive WHERE record_type = 'media_tag' AND tweet_id = ?",
        (tweet_id,),
    ).fetchone()
    return json.loads(row["raw_json"]) if row is not None else None


def _seed_tag_candidate(
    store,
    tweet_id: str,
    *,
    created_at_ts: int,
    enrichment_state: str = "done",
    with_media: bool = True,
) -> None:
    rows = [
        store._record(
            row_key=f"tweet:bookmark::{tweet_id}",
            record_type="tweet",
            tweet_id=tweet_id,
            collection_type="bookmark",
            created_at_ts=created_at_ts,
            text=f"tweet {tweet_id}",
            raw_json=f'{{"id":"{tweet_id}"}}',
        ),
        store._record(
            row_key=f"tweet_object:{tweet_id}",
            record_type="tweet_object",
            tweet_id=tweet_id,
            enrichment_state=enrichment_state,
        ),
    ]
    if with_media:
        rows.extend(
            [
                store._record(
                    row_key=f"media:{tweet_id}:first",
                    record_type="media",
                    tweet_id=tweet_id,
                    media_key="first",
                ),
                store._record(
                    row_key=f"media:{tweet_id}:second",
                    record_type="media",
                    tweet_id=tweet_id,
                    media_key="second",
                ),
            ]
        )
    store._merge_records(rows)


def test_tagging_eligibility_filters_dedupes_orders_and_limits(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    _seed_tag_candidate(store, "newest", created_at_ts=30)
    _seed_tag_candidate(store, "older", created_at_ts=20)
    _seed_tag_candidate(store, "resurrected", created_at_ts=25, enrichment_state="resurrected")
    _seed_tag_candidate(store, "pending", created_at_ts=50, enrichment_state="pending")
    _seed_tag_candidate(store, "no-media", created_at_ts=40, with_media=False)
    _seed_tag_candidate(store, "tagged", created_at_ts=60)
    store.update_media_tags("tagged", ["Existing"])
    statements: list[str] = []
    store.conn.set_trace_callback(statements.append)

    text_candidates = store.get_eligible_tagging_candidates(limit=20)
    assert [candidate["tweet_id"] for candidate in text_candidates] == ["no-media"]
    assert {candidate["content_type"] for candidate in text_candidates} == {"text"}
    assert store.get_tagging_coverage_counts() == (5, 1)

    store.update_media_tags("no-media", ["Text Topic"])
    media_candidates = store.get_eligible_tagging_candidates(limit=20)
    assert [candidate["tweet_id"] for candidate in media_candidates] == [
        "newest",
        "resurrected",
        "older",
    ]
    assert {candidate["content_type"] for candidate in media_candidates} == {"media"}
    assert store.get_tagging_coverage_counts() == (5, 2)
    eligibility_queries = [statement for statement in statements if "classified AS" in statement]
    assert len(eligibility_queries) == 2
    assert all(
        "FROM archive t INDEXED BY idx_archive_record_page" in statement
        for statement in eligibility_queries
    )
    assert all("classified AS" in statement for statement in eligibility_queries)
    coverage_queries = [statement for statement in statements if "eligible_tweets AS" in statement]
    assert len(coverage_queries) == 2
    assert "FROM archive t INDEXED BY idx_archive_record_page" in coverage_queries[0]
    assert "quoted AS" in coverage_queries[0]
    assert "FROM saved\n                CROSS JOIN archive relation" in coverage_queries[0]
    store.close()


def test_failed_and_pending_tag_rows_remain_eligible_but_done_valid_rows_do_not(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    for tweet_id in ("none", "failed", "pending", "done-invalid", "done-valid"):
        _seed_tag_candidate(store, tweet_id, created_at_ts=10, with_media=False)
    store._merge_records(
        [
            store._record(
                row_key="media_tag:failed",
                record_type="media_tag",
                tweet_id="failed",
                raw_json=json.dumps({"error": "temporary"}),
                enrichment_state="failed",
            ),
            store._record(
                row_key="media_tag:pending",
                record_type="media_tag",
                tweet_id="pending",
                raw_json=json.dumps({}),
                enrichment_state="pending",
            ),
            store._record(
                row_key="media_tag:done-invalid",
                record_type="media_tag",
                tweet_id="done-invalid",
                raw_json="not json",
                enrichment_state="done",
            ),
            store._record(
                row_key="media_tag:done-valid",
                record_type="media_tag",
                tweet_id="done-valid",
                raw_json=json.dumps({"tags": ["One", "Two"]}),
                enrichment_state="done",
            ),
        ]
    )

    eligible = {row["tweet_id"] for row in store.get_eligible_tagging_candidates(limit=20)}
    assert eligible == {"none", "failed", "pending", "done-invalid"}
    assert store.get_tagging_coverage_counts() == (5, 1)
    store.close()


def test_tagging_candidates_include_and_dedupe_direct_quoted_originals(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    _seed_tag_candidate(store, "quote-a", created_at_ts=70, with_media=False)
    _seed_tag_candidate(store, "quote-b", created_at_ts=60, with_media=False)
    store._merge_records(
        [
            store._record(
                row_key="tweet_object:original",
                record_type="tweet_object",
                tweet_id="original",
                created_at_ts=50,
                enrichment_state="done",
            ),
            store._record(
                row_key="media:original:first",
                record_type="media",
                tweet_id="original",
                media_key="first",
            ),
            store._record(
                row_key="tweet_relation:quote-a:quote_of:original",
                record_type="tweet_relation",
                tweet_id="quote-a",
                relation_type="quote_of",
                target_tweet_id="original",
            ),
            store._record(
                row_key="tweet_relation:quote-b:quote_of:original",
                record_type="tweet_relation",
                tweet_id="quote-b",
                relation_type="quote_of",
                target_tweet_id="original",
            ),
        ]
    )

    candidates = store.get_eligible_tagging_candidates(limit=20)

    assert [candidate["tweet_id"] for candidate in candidates] == [
        "quote-a",
        "quote-b",
        "original",
    ]
    assert all(candidate["content_type"] == "media" for candidate in candidates)
    assert candidates[0]["quoted_tweet_id"] == "original"
    assert candidates[1]["quoted_tweet_id"] == "original"
    assert candidates[2]["quoted_tweet_id"] is None
    assert store.get_tagging_coverage_counts() == (3, 0)
    store.close()


def test_update_media_tags_creates_normalizes_and_is_idempotent(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    store.update_media_tags("1", [" Nature ", "nature", "", "SKY", "sky"])
    first = _tag_payload(store, "1")
    store.update_media_tags("1", ["Nature", "SKY"])
    second = _tag_payload(store, "1")

    assert first == second == {"description": "", "tags": ["Nature", "SKY"]}
    assert (
        store.conn.execute(
            "SELECT COUNT(*) FROM archive WHERE record_type = 'media_tag' AND tweet_id = '1'"
        ).fetchone()[0]
        == 1
    )
    store.close()


def test_update_media_tags_preserves_description_and_recovers_invalid_json(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store._merge_records(
        [
            store._record(
                row_key="media_tag:1",
                record_type="media_tag",
                tweet_id="1",
                raw_json=json.dumps({"description": "A scene", "tags": ["Old"]}),
            ),
            store._record(
                row_key="media_tag:2",
                record_type="media_tag",
                tweet_id="2",
                raw_json="{broken",
            ),
        ]
    )

    store.update_media_tags("1", ["New"])
    store.update_media_tags("2", ["Recovered"])

    assert _tag_payload(store, "1") == {"description": "A scene", "tags": ["New"]}
    assert _tag_payload(store, "2") == {"tags": ["Recovered"]}
    store.close()


def test_update_media_tags_can_edit_clear_and_store_description_without_tags(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    store.update_media_tags("1", ["Nature"], description="  A quiet scene  ")
    assert _tag_payload(store, "1") == {
        "description": "A quiet scene",
        "tags": ["Nature"],
    }

    store.update_media_tags("1", ["Nature"], description="  ")
    assert _tag_payload(store, "1") == {"description": "", "tags": ["Nature"]}

    store.update_media_tags("2", [], description="Description only")
    assert _tag_payload(store, "2") == {
        "description": "Description only",
        "tags": [],
    }
    store.close()


def test_empty_update_and_delete_media_tag_remove_row(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store.update_media_tags("1", ["Nature"])
    store.update_media_tags("1", [" ", ""])
    assert _tag_payload(store, "1") is None

    store.update_media_tags("1", ["Nature"])
    store.delete_media_tag("1")
    store.delete_media_tag("missing")
    assert _tag_payload(store, "1") is None
    store.close()


def test_delete_global_tag_is_case_insensitive_and_removes_empty_rows(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store.update_media_tags("1", ["Nature", "Sky"])
    store.update_media_tags("2", ["NATURE"])
    store._merge_records(
        [
            store._record(
                row_key="media_tag:broken",
                record_type="media_tag",
                tweet_id="broken",
                raw_json="{not-json",
            )
        ]
    )

    store.delete_global_tag("nature")

    assert _tag_payload(store, "1") == {"description": "", "tags": ["Sky"]}
    assert _tag_payload(store, "2") is None
    assert (
        store.conn.execute("SELECT raw_json FROM archive WHERE tweet_id = 'broken'").fetchone()[0]
        == "{not-json"
    )
    store.close()


def test_merge_global_tags_is_case_insensitive_and_deduplicates(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store.update_media_tags("1", ["Landscape", "Nature", "SKY", "sky"])
    store.update_media_tags("2", ["nature", "Portrait"])
    store.update_media_tags("3", ["Unrelated"])

    store.merge_global_tags("Scenery", [" nature ", "SKY", "scenery", "NATURE"])

    assert _tag_payload(store, "1")["tags"] == ["Landscape", "Scenery"]
    assert _tag_payload(store, "2")["tags"] == ["Portrait", "Scenery"]
    assert _tag_payload(store, "3")["tags"] == ["Unrelated"]
    store.close()


def test_merge_global_tags_noops_for_blank_primary_or_empty_sources(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store.update_media_tags("1", ["Nature"])

    store.merge_global_tags("", ["Nature"])
    store.merge_global_tags("Scenery", [])
    store.merge_global_tags("Nature", ["nature", " NATURE "])

    assert _tag_payload(store, "1")["tags"] == ["Nature"]
    store.close()


def test_tag_counts_support_query_unlimited_results_and_malformed_rows(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store.update_media_tags("1", ["Nature", "Sky"])
    store.update_media_tags("2", ["nature", "City"])
    store.update_media_tags("3", ["Portrait"])
    store._merge_records(
        [
            store._record(
                row_key="media_tag:broken",
                record_type="media_tag",
                tweet_id="broken",
                raw_json="{broken",
            )
        ]
    )

    all_counts = store.get_tag_counts(limit=-1)
    nature_counts = store.get_tag_counts(query="NAT", limit=50)
    limited = store.get_tag_counts(limit=1)

    assert len(all_counts) == 4
    assert nature_counts == [{"tag": "Nature", "count": 2}]
    assert len(limited) == 1
    assert limited[0]["count"] == 2
    store.close()


def test_export_includes_valid_tags_and_skips_malformed_tag_json(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store._merge_records(
        [
            store._record(
                row_key="tweet:bookmark::1",
                record_type="tweet",
                tweet_id="1",
                collection_type="bookmark",
                text="one",
                raw_json='{"id":"1"}',
            ),
            store._record(
                row_key="tweet:bookmark::2",
                record_type="tweet",
                tweet_id="2",
                collection_type="bookmark",
                text="two",
                raw_json='{"id":"2"}',
            ),
        ]
    )
    store.update_media_tags("1", ["Nature"])
    store._merge_records(
        [
            store._record(
                row_key="media_tag:2",
                record_type="media_tag",
                tweet_id="2",
                raw_json="{broken",
            )
        ]
    )

    exported = {row["tweet_id"]: row for row in store.export_rows("bookmark")}

    assert exported["1"]["media_tags"]["tags"] == ["Nature"]
    assert exported["2"]["media_tags"] is None
    store.close()
