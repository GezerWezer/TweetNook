"""Regress actual query shapes and bounded reads without machine-dependent timers."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from tweetnook.search import search_posts
from tweetnook.storage.backend import SCHEMA_VERSION, ArchiveStore


def _statements(store, operation):
    statements = []
    store.conn.set_trace_callback(statements.append)
    try:
        result = operation()
    finally:
        store.conn.set_trace_callback(None)
    return result, [sql for sql in statements if sql.startswith(("SELECT", "WITH"))]


def _plan(store, sql):
    return " ".join(row[3] for row in store.conn.execute("EXPLAIN QUERY PLAN " + sql))


def _seed_memberships(store, count=100):
    store._merge_records(
        [
            store._record(
                row_key=f"tweet:{collection}::{i}",
                record_type="tweet",
                tweet_id=str(i),
                collection_type=collection,
                created_at_ts=i,
                sort_index=str(i),
                author_username=f"author{i}",
                author_id=str(i),
                text="common text",
            )
            for i in range(count)
            for collection in ("bookmark", "like")
        ]
    )


def test_concurrent_readers_preserve_complete_independent_results(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    _seed_memberships(store, count=40)
    expected = [str(index) for index in range(30, 9, -1)]
    barrier = Barrier(8)

    def read_pages():
        for _ in range(20):
            barrier.wait(timeout=10)
            rows = store.conn.execute(
                "SELECT tweet_id FROM archive WHERE record_type = 'tweet' "
                "AND collection_type = ? AND created_at_ts BETWEEN ? AND ? "
                "ORDER BY created_at_ts DESC",
                ("bookmark", 10, 30),
            ).fetchall()
            assert [row[0] for row in rows] == expected

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(read_pages) for _ in range(8)]
            for future in futures:
                future.result()
    finally:
        store.close()


def test_actual_feed_duplicate_probes_and_counts_use_membership_index(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    _seed_memberships(store)
    for collection in ("all", "like"):
        ids, statements = _statements(
            store, lambda collection=collection: store.get_paginated_tweet_ids(collection, 20, 0)
        )
        assert ids == [str(i) for i in range(99, 79, -1)]
        plan = _plan(store, statements[0])
        assert "idx_archive_membership" in plan
        assert "tweet_id=?" in plan
        assert "TEMP B-TREE" not in plan
        count, statements = _statements(
            store, lambda collection=collection: store.count_export_rows(collection)
        )
        assert count == 100
        count_sql = next(sql for sql in statements if "COUNT(DISTINCT tweet_id)" in sql)
        assert "idx_archive_membership" in _plan(store, count_sql)
        assert "TEMP B-TREE" not in _plan(store, count_sql)
    store.close()


def test_url_hydration_uses_primary_keys_and_handles_missing_and_quoted_values(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    hashes = ["one", "two'quoted", "missing"]
    store._merge_records(
        [
            store._record(row_key=f"url:{h}", record_type="url", url_hash=h, title=h)
            for h in hashes[:2]
        ]
    )
    rows, statements = _statements(
        store, lambda: store._rows_for_values("url", "url_hash", hashes, columns=["title"])
    )
    assert {row["title"] for row in rows} == set(hashes[:2])
    assert len(statements) == 1
    assert "sqlite_autoindex_archive_1 (row_key=?)" in _plan(store, statements[0])
    store.close()


def test_selective_author_and_profile_queries_use_partial_indexes(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    _seed_memberships(store)
    result, statements = _statements(store, lambda: search_posts(store, "from:AUTHOR12"))
    assert [row["tweet_id"] for row in result.rows] == ["12"]
    assert result.total == 1
    assert "idx_archive_search_author" in _plan(store, statements[0])
    profile_sql = (
        "SELECT COUNT(DISTINCT author_id) FROM archive "
        "WHERE author_id IS NOT NULL AND author_id != ''"
    )
    assert "idx_archive_profile_author" in _plan(store, profile_sql)
    store.close()


def test_queue_limits_reach_sql_and_preserve_filtering_and_order(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    for kind in ("media", "url", "article"):
        store._merge_records(
            [
                store._record(
                    row_key=f"{kind}:{i}",
                    record_type=kind,
                    tweet_id=str(i),
                    position="0",
                    download_state="pending" if i != 0 else "done",
                    unfurl_state="pending" if i != 0 else "done",
                    status="preview" if i != 0 else "body_present",
                    canonical_url=f"https://example.test/{i}",
                )
                for i in range(20)
            ]
        )
    operations = [
        lambda: store.list_media_rows(states={"pending"}, limit=2),
        lambda: store.list_url_rows(states={"pending"}, limit=2),
        lambda: store.list_article_rows(preview_only=True, limit=2),
        lambda: store.get_article_tweet_ids(preview_only=True, limit=2),
    ]
    for operation in operations:
        rows, statements = _statements(store, operation)
        assert len(rows) == 2
        assert len(statements) == 1
        assert "ORDER BY" in statements[0] and "LIMIT 2" in statements[0]
        assert [
            (row.get("tweet_id") or row["row_key"].rsplit(":", 1)[-1])
            if isinstance(row, dict)
            else row
            for row in rows
        ] == ["1", "10"]
    store.close()


def test_v6_upgrade_adds_indexes_without_rebuilding_fts(tmp_path, monkeypatch):
    path = tmp_path / "archive.db"
    store = ArchiveStore(path, create=True)
    _seed_memberships(store, 1)
    with store.conn:
        store.conn.execute("DROP INDEX idx_archive_search_author")
        store.conn.execute("PRAGMA user_version=6")
    store.close()

    def unexpected(*args):
        raise AssertionError("additive migration must not rebuild FTS")

    monkeypatch.setattr(ArchiveStore, "_rebuild_fts_schema", unexpected)
    store = ArchiveStore(path, create=False)
    assert store.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert store.migration_report.backup_path is None
    assert not store.migration_report.search_index_rebuilt
    assert store.conn.execute("SELECT COUNT(*) FROM archive_fts").fetchone()[0] == 2
    store.close()


def test_writer_close_maintains_stats_but_read_close_does_not(tmp_path):
    path = tmp_path / "archive.db"
    store = ArchiveStore(path, create=True)
    _seed_memberships(store)
    statements = []
    store.conn.set_trace_callback(statements.append)
    store.close()
    assert "PRAGMA optimize" in statements
    store.close()
    reader = ArchiveStore(path, create=False)
    statements.clear()
    reader.conn.set_trace_callback(statements.append)
    reader.close()
    assert not statements


def test_membership_counts_are_reused_and_invalidate_on_external_changes(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    _seed_memberships(store)
    assert store.count_export_rows("all") == 100
    _, sql = _statements(store, lambda: store.count_export_rows("all"))
    assert not any("COUNT(" in statement for statement in sql)
    other = ArchiveStore(store.db_path, create=False)
    with other.conn:
        other.conn.execute("UPDATE archive SET text = 'changed' WHERE record_type = 'tweet'")
    _, sql = _statements(store, lambda: store.count_export_rows("all"))
    assert not any("COUNT(" in statement for statement in sql)
    with other.conn:
        other.conn.execute("DELETE FROM archive WHERE tweet_id = '0'")
    assert store.count_export_rows("all") == 99
    other.close()
    # Reading uncommitted changes must not poison a cache after rollback.
    store.conn.execute("DELETE FROM archive WHERE tweet_id = '1'")
    assert store.count_export_rows("all") == 98
    store.conn.rollback()
    assert store.count_export_rows("all") == 99
    store.close()


def test_bounded_exports_rank_keys_before_loading_content(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    _seed_memberships(store)
    rows, sql = _statements(store, lambda: store.export_rows("all", limit=2, offset=20))
    assert [row["tweet_id"] for row in rows] == ["79", "78"]
    key_query = next(statement for statement in sql if statement.startswith("WITH candidates"))
    assert "LIMIT 2 OFFSET 20" in key_query
    assert "raw_json" not in key_query
    assert "SELECT row_key, tweet_id, text" in " ".join(sql)
    store.close()


def test_chronological_continuation_seeks_instead_of_scanning_old_pages(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    _seed_memberships(store, 5000)
    boundary = store.get_paginated_tweet_rows("all", 1, 3999)[0]

    def steps(operation):
        count = 0

        def progress():
            nonlocal count
            count += 1

        store.conn.set_progress_handler(progress, 100)
        try:
            rows = operation()
        finally:
            store.conn.set_progress_handler(None, 0)
        return rows, count

    cursor_rows, cursor_steps = steps(
        lambda: store.get_paginated_tweet_rows("all", 20, 0, after=boundary)
    )
    offset_rows, offset_steps = steps(lambda: store.get_paginated_tweet_rows("all", 20, 4000))
    assert cursor_rows == offset_rows
    assert cursor_steps * 20 < offset_steps
    _, sql = _statements(
        store, lambda: store.get_paginated_tweet_rows("all", 20, 0, after=boundary)
    )
    assert all("OFFSET" not in statement for statement in sql)
    assert all("TEMP B-TREE" not in _plan(store, statement) for statement in sql)
    store.close()


def test_non_relevance_search_avoids_ranking_and_redundant_candidates(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    _seed_memberships(store)
    result, sql = _statements(store, lambda: search_posts(store, "common", sort="newest"))
    assert len(result.rows) == 20
    search_sql = next(statement for statement in sql if statement.startswith("WITH text_match"))
    assert "archive_fts.rank" not in search_sql
    assert "candidate_ids" not in search_sql
    assert "MATERIALIZE matches" not in _plan(store, search_sql)
    assert "archive_fts" in _plan(store, search_sql)
    store.close()


def test_selective_text_author_driver_preserves_ranking_and_all_matches(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    _seed_memberships(store, 1300)
    groups = (
        ({"kind": "text", "value": "common", "negated": False},),
        (
            {
                "kind": "filter",
                "expression": "LOWER(COALESCE(author_username, '')) = 'author12'",
                "index_driver": True,
            },
        ),
    )
    opts = dict(collections=None, sort="relevance", limit=20, offset=0)
    narrow, sql = _statements(store, lambda: store.search_tweet_ids(groups, **opts))
    ordinary = (groups[0], ({**groups[1][0], "index_driver": False},))
    assert narrow == store.search_tweet_ids(ordinary, **opts)
    query = next(
        statement for statement in sql if statement.startswith("WITH structured_candidates")
    )
    assert "INDEXED BY idx_archive_tweet_id" in query
    assert "archive.tweet_id IN (SELECT tweet_id FROM structured_candidates)" in query
    # A popular author must fall back to full FTS execution, with no 200/1000 cap.
    with store.conn:
        store.conn.execute(
            "UPDATE archive SET author_username = 'author12' WHERE record_type = 'tweet'"
        )
    result = search_posts(store, "common from:author12", limit=1301)
    assert len(result.rows) == 1300
    store.close()


def test_live_graph_prefetch_batches_existing_secondary_rows(tmp_path):
    from tests.test_storage import _complex_tweet
    from tweetnook.extractor import extract_secondary_objects
    from tweetnook.storage.backend import _PageBuffer

    store = ArchiveStore(tmp_path / "archive.db", create=True)
    graph = extract_secondary_objects(_complex_tweet("123").raw_json)
    for _ in range(2):
        buffer = _PageBuffer()
        _, sql = _statements(
            store, lambda buffer=buffer: store._buffer_secondary_graph(graph, cursor=buffer)
        )
        assert len(sql) == 1
        assert "row_key IN (" in sql[0]
        assert len(buffer.records) > 5
        store.merge_rows(list(buffer.records.values()))
    store.close()
