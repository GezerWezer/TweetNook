"""Helpers and fixtures shared by the storage sqlite tests."""

from __future__ import annotations

from tweetnook.storage.backend import ArchiveStore

CREATED_2012 = "Tue Oct 09 21:39:26 +0000 2012"


CREATED_2024 = "Thu Apr 11 03:55:13 +0000 2024"


CREATED_2026 = "Sat Mar 14 00:00:00 +0000 2026"


def _membership(
    store: ArchiveStore,
    tweet_id: str,
    *,
    collection: str,
    text: str,
    created_at: str,
    created_at_ts: int,
    sort_index: str,
    folder_id: str | None = None,
    author_username: str = "alice",
    author_display_name: str = "Alice Example",
) -> dict[str, object]:
    return store._record(
        row_key=f"tweet:{collection}:{folder_id or ''}:{tweet_id}",
        record_type="tweet",
        tweet_id=tweet_id,
        collection_type=collection,
        folder_id=folder_id or "",
        text=text,
        author_id=f"author-{tweet_id}",
        author_username=author_username,
        author_display_name=author_display_name,
        created_at=created_at,
        created_at_ts=created_at_ts,
        sort_index=sort_index,
        raw_json=f'{{"id":"{tweet_id}"}}',
    )


def _seed_search_rows(store: ArchiveStore) -> None:
    rows = [
        _membership(
            store,
            "1",
            collection="bookmark",
            text="alpha beta hello-world",
            created_at=CREATED_2012,
            created_at_ts=1,
            sort_index="10",
        ),
        _membership(
            store,
            "1",
            collection="like",
            text="alpha beta hello-world",
            created_at=CREATED_2012,
            created_at_ts=1,
            sort_index="9",
        ),
        _membership(
            store,
            "2",
            collection="like",
            text="alpha gamma",
            created_at=CREATED_2024,
            created_at_ts=2,
            sort_index="20",
            author_username="bob",
            author_display_name="Bob Builder",
        ),
        _membership(
            store,
            "3",
            collection="bookmark",
            text="article membership",
            created_at=CREATED_2026,
            created_at_ts=3,
            sort_index="30",
        ),
        store._record(
            row_key="article:3",
            record_type="article",
            tweet_id="3",
            title="Alpha Analysis",
            summary_text="A focused beta report",
            content_text="Long form article content",
        ),
    ]
    store._merge_records(rows)


def add_released_followup_columns(store) -> None:
    """Give a fresh archive being rolled back to an old schema the columns those schemas held."""
    for column in ("enrichment_followup_status", "enrichment_aborted_reason"):
        store.conn.execute(f"ALTER TABLE archive ADD COLUMN {column} TEXT")
