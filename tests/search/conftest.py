"""Helpers and fixtures shared by the search tests."""

from __future__ import annotations

import json
from typing import Any

from tweetnook.storage.backend import ArchiveStore


def _row(
    tweet_id: str,
    text: str,
    *,
    username: str = "alice",
    media: list[dict[str, Any]] | None = None,
    article: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "tweet_id": tweet_id,
        "text": text,
        "author": {"id": username, "username": username, "display_name": username.title()},
        "created_at": f"2026-01-0{tweet_id}T00:00:00Z",
        "media": media or [],
        "urls": [],
        "article": article,
        "raw_json": {"legacy": {}},
    }


def _real_store(tmp_path, rows: list[dict[str, Any]]) -> ArchiveStore:
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    records = []
    for row in rows:
        tweet_id = row["tweet_id"]
        author = row["author"]
        records.append(
            store._record(
                row_key=f"tweet:bookmark::{tweet_id}",
                record_type="tweet",
                tweet_id=tweet_id,
                collection_type="bookmark",
                text=row["text"],
                author_id=author["id"],
                author_username=author["username"],
                author_display_name=author["display_name"],
                created_at=row["created_at"],
                created_at_ts=int(tweet_id),
                sort_index=tweet_id,
                raw_json=json.dumps(row["raw_json"]),
            )
        )
        for index, media in enumerate(row["media"]):
            records.append(
                store._record(
                    row_key=f"media:{tweet_id}:{index}",
                    record_type="media",
                    tweet_id=tweet_id,
                    media_key=str(index),
                    media_type=media["type"],
                )
            )
    store._merge_records(records)
    return store
