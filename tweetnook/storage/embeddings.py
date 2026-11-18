"""Derived CLIP/SigLIP media embedding storage and similarity scoring."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from .common import listed_tweet_sql
from .db import ConnectionMixin
from .record_types import RecordType

try:
    import numpy as _np
except ImportError:  # pragma: no cover - optional smart-search dependency
    _np = None


def _cosine_dot(query_bytes: bytes, candidate_bytes: bytes, dims: int) -> float:
    """Compute the dot product of two L2-normalized float32 vectors."""
    if _np is not None:
        q = _np.frombuffer(query_bytes, dtype=_np.float32, count=dims)
        c = _np.frombuffer(candidate_bytes, dtype=_np.float32, count=dims)
        return float(_np.dot(q, c))
    q_view = memoryview(query_bytes).cast("f")
    c_view = memoryview(candidate_bytes).cast("f")
    return sum(q_view[i] * c_view[i] for i in range(dims))


def _media_path_expr(*, include_video_thumbnails: bool) -> str:
    if include_video_thumbnails:
        return (
            "CASE "
            "WHEN m.media_type = 'photo' AND COALESCE(m.local_path, '') != '' "
            "THEN m.local_path "
            "WHEN m.media_type IN ('video', 'animated_gif') "
            "AND COALESCE(m.thumbnail_local_path, '') != '' "
            "THEN m.thumbnail_local_path "
            "WHEN m.media_type = 'animated_gif' AND COALESCE(m.local_path, '') != '' "
            "AND LOWER(m.local_path) NOT LIKE '%.mp4' "
            "THEN m.local_path "
            "ELSE NULL END"
        )
    return (
        "CASE "
        "WHEN m.media_type = 'photo' AND COALESCE(m.local_path, '') != '' "
        "THEN m.local_path "
        "WHEN m.media_type = 'animated_gif' AND COALESCE(m.local_path, '') != '' "
        "AND LOWER(m.local_path) NOT LIKE '%.mp4' "
        "THEN m.local_path "
        "ELSE NULL END"
    )


def _media_scope_clause(*, include_quoted_media: bool, include_thread_media: bool) -> str:
    if include_thread_media:
        return ""
    saved = (
        f"EXISTS (SELECT 1 FROM archive t WHERE t.tweet_id = m.tweet_id "
        f"AND t.record_type = '{RecordType.TWEET}')"
    )
    if not include_quoted_media:
        return f"AND {saved}"
    quoted = (
        f"EXISTS (SELECT 1 FROM archive r "
        f"JOIN archive qt ON qt.tweet_id = r.tweet_id AND qt.record_type = '{RecordType.TWEET}' "
        f"WHERE r.record_type = '{RecordType.TWEET_RELATION}' "
        f"AND r.relation_type = 'quote_of' AND r.target_tweet_id = m.tweet_id)"
    )
    return f"AND ({saved} OR {quoted})"


class EmbeddingsMixin(ConnectionMixin):
    """Read, write, and score derived media embeddings in SQLite."""

    def _media_embeddings_exists(self) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'media_embeddings'"
        ).fetchone()
        return row is not None

    def _pending_media_where(
        self,
        model: str,
        *,
        include_video_thumbnails: bool,
        include_quoted_media: bool,
        include_thread_media: bool,
    ) -> tuple[str, str, list[Any]]:
        path_expr = _media_path_expr(include_video_thumbnails=include_video_thumbnails)
        scope_clause = _media_scope_clause(
            include_quoted_media=include_quoted_media,
            include_thread_media=include_thread_media,
        )
        has_table = self._media_embeddings_exists()
        not_indexed = (
            "AND NOT EXISTS (SELECT 1 FROM media_embeddings e "
            "WHERE e.media_row_key = m.row_key AND e.model = ?)"
            if has_table
            else ""
        )
        where_sql = f"""
            WHERE m.record_type = ?
              AND m.row_key IS NOT NULL
              AND m.tweet_id IS NOT NULL
              AND m.download_state = 'done'
              AND ({path_expr}) IS NOT NULL
              {scope_clause}
              {not_indexed}
        """
        params: list[Any] = [RecordType.MEDIA]
        if has_table:
            params.append(model)
        return path_expr, where_sql, params

    def pending_embedding_media(
        self,
        model: str,
        *,
        limit: int | None = None,
        include_video_thumbnails: bool = False,
        include_quoted_media: bool = True,
        include_thread_media: bool = False,
    ) -> list[dict[str, Any]]:
        """Return downloaded local media rows not yet indexed for *model*."""
        path_expr, where_sql, params = self._pending_media_where(
            model,
            include_video_thumbnails=include_video_thumbnails,
            include_quoted_media=include_quoted_media,
            include_thread_media=include_thread_media,
        )
        sql = f"""
            SELECT
                m.row_key AS media_row_key,
                m.tweet_id AS tweet_id,
                m.media_type AS media_type,
                {path_expr} AS file_path
            FROM archive m
            {where_sql}
            ORDER BY m.tweet_id DESC, m.position ASC, m.row_key ASC
        """
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def upsert_media_embeddings(
        self,
        rows: Sequence[tuple[str, str, str, int, bytes, str | None]],
    ) -> int:
        """Insert or replace `(media_row_key, tweet_id, model, dims, embedding, indexed_at)`."""
        if not rows:
            return 0
        now = datetime.now(UTC).isoformat()
        payload = [
            (media_row_key, tweet_id, model, dims, embedding, indexed_at or now)
            for media_row_key, tweet_id, model, dims, embedding, indexed_at in rows
        ]
        with self.transaction():
            self.conn.executemany(
                "INSERT OR REPLACE INTO media_embeddings "
                "(media_row_key, tweet_id, model, dims, embedding, indexed_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                payload,
            )
        return len(payload)

    def embedding_stats(
        self,
        model: str,
        *,
        include_video_thumbnails: bool = False,
        include_quoted_media: bool = True,
        include_thread_media: bool = False,
    ) -> dict[str, int]:
        """Return indexed and pending media counts for *model*."""
        indexed = (
            int(
                self.conn.execute(
                    "SELECT COUNT(*) FROM media_embeddings WHERE model = ?",
                    (model,),
                ).fetchone()[0]
            )
            if self._media_embeddings_exists()
            else 0
        )
        _path_expr, where_sql, params = self._pending_media_where(
            model,
            include_video_thumbnails=include_video_thumbnails,
            include_quoted_media=include_quoted_media,
            include_thread_media=include_thread_media,
        )
        pending = int(
            self.conn.execute(
                f"SELECT COUNT(*) FROM archive m {where_sql}",
                params,
            ).fetchone()[0]
        )
        return {"indexed": indexed, "pending": pending, "total": indexed + pending}

    def tweet_embedding_vector(self, tweet_id: str, model: str) -> bytes | None:
        """Return the first stored embedding BLOB for *tweet_id* and *model*, if any."""
        if not self._media_embeddings_exists():
            return None
        row = self.conn.execute(
            "SELECT embedding FROM media_embeddings WHERE model = ? AND tweet_id = ? LIMIT 1",
            (model, tweet_id),
        ).fetchone()
        return bytes(row["embedding"]) if row is not None else None

    def search_media_embeddings(
        self,
        query_vec: bytes,
        model: str,
        *,
        collections: set[str] | None = None,
        exclude_hidden: bool = True,
        exclude_uncaptured: bool = True,
        include_quoted_media: bool = True,
        allowed_tweet_ids: set[str] | None = None,
        min_score: float = 0.0,
        limit: int = 100,
    ) -> list[tuple[str, float]]:
        """Score stored embeddings against *query_vec* and return top `(tweet_id, score)`."""
        if limit < 1 or not self._media_embeddings_exists():
            return []
        if allowed_tweet_ids is not None and not allowed_tweet_ids:
            return []
        expected_bytes = len(query_vec)
        if expected_bytes == 0 or expected_bytes % 4 != 0:
            raise ValueError("query_vec must be a non-empty float32 byte buffer")
        dims = expected_bytes // 4

        col_filter = ""
        col_params: list[Any] = []
        if collections:
            placeholders = ", ".join("?" for _ in collections)
            col_filter = f"AND t.collection_type IN ({placeholders})"
            col_params = sorted(collections)

        id_filter = ""
        id_params: list[Any] = []
        if allowed_tweet_ids is not None and len(allowed_tweet_ids) <= 900:
            id_placeholders = ", ".join("?" for _ in allowed_tweet_ids)
            id_filter = f"AND t.tweet_id IN ({id_placeholders})"
            id_params = sorted(allowed_tweet_ids)

        excluded_sql = listed_tweet_sql(
            "t.tweet_id",
            exclude_hidden=exclude_hidden,
            exclude_uncaptured=exclude_uncaptured,
        )
        excluded_clause = f"AND {excluded_sql}" if excluded_sql else ""

        params: list[Any] = [model, dims, *col_params, *id_params]
        direct_sql = f"""
            SELECT t.tweet_id AS result_tweet_id, e.embedding AS embedding
            FROM media_embeddings e
            JOIN archive t ON t.tweet_id = e.tweet_id AND t.record_type = '{RecordType.TWEET}'
            WHERE e.model = ? AND e.dims = ?
              {col_filter}
              {id_filter}
              {excluded_clause}
        """

        if include_quoted_media:
            params.extend([model, dims, *col_params, *id_params])
            quoted_sql = f"""
                UNION ALL
                SELECT t.tweet_id AS result_tweet_id, e.embedding AS embedding
                FROM media_embeddings e
                JOIN archive r ON r.target_tweet_id = e.tweet_id
                    AND r.record_type = '{RecordType.TWEET_RELATION}'
                    AND r.relation_type = 'quote_of'
                JOIN archive t ON t.tweet_id = r.tweet_id AND t.record_type = '{RecordType.TWEET}'
                WHERE e.model = ? AND e.dims = ?
                  {col_filter}
                  {id_filter}
                  {excluded_clause}
            """
            sql = direct_sql + quoted_sql
        else:
            sql = direct_sql

        tids: list[str] = []
        blobs: list[bytes] = []
        for row in self.conn.execute(sql, params).fetchall():
            tid = row["result_tweet_id"]
            if allowed_tweet_ids is not None and tid not in allowed_tweet_ids:
                continue
            blob = bytes(row["embedding"])
            if len(blob) != expected_bytes:
                continue
            tids.append(tid)
            blobs.append(blob)

        if not tids:
            return []

        if _np is not None:
            q = _np.frombuffer(query_vec, dtype=_np.float32, count=dims)
            matrix = _np.frombuffer(b"".join(blobs), dtype=_np.float32).reshape(len(blobs), dims)
            raw_scores: Sequence[float] = (matrix @ q).tolist()
        else:
            raw_scores = [_cosine_dot(query_vec, blob, dims) for blob in blobs]

        best_by_tweet: dict[str, float] = {}
        for tid, score in zip(tids, raw_scores, strict=True):
            if not math.isfinite(score) or score < min_score:
                continue
            prev = best_by_tweet.get(tid)
            if prev is None or score > prev:
                best_by_tweet[tid] = score

        ranked = sorted(best_by_tweet.items(), key=lambda item: (-item[1], -int(item[0])))
        return ranked[:limit]
