"""Regress actual query shapes and bounded reads without machine-dependent timers."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

from tweetnook.storage.backend import ArchiveStore
from tweetnook.storage.search_query import search_posts


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


def test_random_search_and_export_do_not_replace_functions_with_an_active_cursor(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    _seed_memberships(store, count=8)
    cursor = store.conn.execute("SELECT tweet_id FROM archive WHERE record_type = 'tweet'")
    try:
        assert cursor.fetchone() is not None
        first = search_posts(store, "common", sort="random", random_seed=37, limit=20)
        second = search_posts(store, "common", sort="random", random_seed=37, limit=20)
        assert len(first.rows) == 8
        assert [row["tweet_id"] for row in first.rows] == [row["tweet_id"] for row in second.rows]
        assert len(store.export_rows("all")) == 8
    finally:
        cursor.close()
        store.close()


def test_export_counts_use_membership_index(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    _seed_memberships(store)
    for collection in ("all", "like"):
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


def test_temp_like_index_does_not_trigger_close_time_writes(tmp_path):
    path = tmp_path / "archive.db"
    writer = ArchiveStore(path, create=True)
    _seed_memberships(writer)
    writer.close()
    for store in (
        ArchiveStore(path, create=False),
        ArchiveStore(path, create=False, read_only=True),
    ):
        statements = []
        store.conn.set_trace_callback(statements.append)
        store.warm_like_order()
        assert store.conn.total_changes > 0
        store.close()
        assert "PRAGMA optimize" not in statements


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
    from tests.storage.conftest import _complex_tweet
    from tweetnook.core.extractor import extract_secondary_objects
    from tweetnook.storage.records import PageBuffer

    store = ArchiveStore(tmp_path / "archive.db", create=True)
    graph = extract_secondary_objects(_complex_tweet("123").raw_json)
    for _ in range(2):
        buffer = PageBuffer()
        _, sql = _statements(
            store, lambda buffer=buffer: store._buffer_secondary_graph(graph, cursor=buffer)
        )
        # One lookup of the post's saved media identities, then one for every row it touches.
        assert len(sql) == 2
        assert "record_type = 'media'" in sql[0]
        assert "row_key IN (" in sql[1]
        assert len(buffer.records) > 5
        store.merge_rows(list(buffer.records.values()))
    store.close()


def test_like_temp_transaction_serializes_with_archive_mutations(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    _seed_memberships(store, count=8)
    connection = store.conn
    indexed, release, writer_started = Event(), Event(), Event()

    class Connection:
        def __getattr__(self, name):
            return getattr(connection, name)

        def executemany(self, sql, params):
            result = connection.executemany(sql, params)
            if "INSERT INTO temp.tweetnook_like_order" in sql:
                indexed.set()
                assert release.wait(5)
            return result

    def edit():
        writer_started.set()
        store.set_tweet_hidden("unrelated", True)

    store.conn = Connection()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            warm = pool.submit(store.warm_like_order)
            try:
                assert indexed.wait(5)
                write = pool.submit(edit)
                assert writer_started.wait(5)
                assert not write.done()
            finally:
                release.set()
            warm.result(timeout=5)
            write.result(timeout=5)
        assert not store.conn.in_transaction
    finally:
        release.set()
        store.close()


def test_retry_predicates_seek_eligibility_and_classify_unknown_as_permanent(tmp_path):
    from tweetnook.storage.common import RESURRECTION_ELIGIBLE_SQL, RESURRECTION_PERMANENT_SQL

    store = ArchiveStore(tmp_path / "archive.db", create=True)
    try:
        base = "record_type = 'tweet_object' AND enrichment_state = 'terminal_unavailable'"
        plan = _plan(
            store, f"SELECT count(*) FROM archive WHERE {base} AND {RESURRECTION_ELIGIBLE_SQL}"
        )
        assert "enrichment_retry_eligible=?" in plan
        store.merge_rows(
            [
                {
                    "row_key": f"tweet_object:{tweet_id}",
                    "record_type": "tweet_object",
                    "tweet_id": tweet_id,
                    "enrichment_state": "terminal_unavailable",
                    "enrichment_retry_eligible": eligible,
                    "enrichment_reason": reason,
                }
                for tweet_id, eligible, reason in (
                    ("retry", 1, "not_found"),
                    ("no-reason", 1, None),
                    ("unknown", None, "not_found"),
                    ("ineligible", 0, "not_found"),
                    ("deleted", 1, "deleted_by_author"),
                )
            ]
        )

        def ids(predicate):
            sql = f"SELECT tweet_id FROM archive WHERE {base} AND {predicate}"
            return {row[0] for row in store.conn.execute(sql)}

        assert ids(RESURRECTION_ELIGIBLE_SQL) == {"retry", "no-reason"}
        assert ids(RESURRECTION_PERMANENT_SQL) == {"unknown", "ineligible", "deleted"}
    finally:
        store.close()


def test_note_checks_do_not_take_the_write_lock_for_no_ops(tmp_path):
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    try:
        statements = []
        store.conn.set_trace_callback(statements.append)
        assert store.record_community_note("1", None) is None
        store.conn.set_trace_callback(None)
        assert not any(sql.startswith("BEGIN") for sql in statements)
    finally:
        store.close()
