"""Author lookups: avatars, handle resolution, profile snapshots and handle search."""

from __future__ import annotations

from typing import Any

from .db import ConnectionMixin
from .record_types import RecordType


class AuthorsMixin(ConnectionMixin):
    def avatar_source_urls(self, author_id: str, *, limit: int = 8) -> list[str]:
        """Return distinct captured profile URLs, newest first, using the author index."""
        rows = self.conn.execute(
            f"""
            SELECT avatar_url FROM (
                SELECT last_seen_at,
                    CASE WHEN json_valid(raw_json) THEN COALESCE(
                        NULLIF(json_extract(raw_json,
                            '$.core.user_results.result.avatar.image_url'), ''),
                        NULLIF(json_extract(raw_json,
                            '$.core.user_results.result.legacy.profile_image_url_https'), '')
                    ) END AS avatar_url
                FROM archive
                WHERE author_id IS NOT NULL AND author_id != '' AND author_id = ?
                    AND record_type IN ('{RecordType.TWEET}', '{RecordType.TWEET_OBJECT}')
            )
            WHERE typeof(avatar_url) = 'text' AND avatar_url != ''
            GROUP BY avatar_url
            ORDER BY MAX(last_seen_at) DESC, avatar_url
            LIMIT ?
            """,
            (author_id, limit),
        ).fetchall()
        return [row[0] for row in rows]

    def resolve_author_id(self, username: str) -> str | None:
        """Return the author ID behind a saved tweet's handle, using the author index."""
        row = self.conn.execute(
            f"""
            SELECT author_id FROM archive
            WHERE record_type = '{RecordType.TWEET}' AND LOWER(COALESCE(author_username, '')) = ?
                AND author_id IS NOT NULL AND author_id != ''
            ORDER BY created_at_ts DESC
            LIMIT 1
            """,
            (username.lstrip("@").lower(),),
        ).fetchone()
        return row[0] if row else None

    def author_profile_snapshots(self, author_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """Return distinct captured profile states, most recently fetched first.

        A profile is a snapshot taken when a tweet was fetched, so ordering follows
        fetch time rather than tweet age. Captures without profile details (such as
        archive imports) are skipped.
        """
        rows = self.conn.execute(
            f"""
            SELECT display_name, username, description, followers_count, following_count,
                MAX(last_seen_at) AS fetched_at
            FROM (
                SELECT last_seen_at,
                    COALESCE(json_extract(user_json, '$.legacy.name'),
                        json_extract(user_json, '$.core.name')) AS display_name,
                    COALESCE(json_extract(user_json, '$.legacy.screen_name'),
                        json_extract(user_json, '$.core.screen_name')) AS username,
                    json_extract(user_json, '$.legacy.description') AS description,
                    json_extract(user_json, '$.legacy.followers_count') AS followers_count,
                    json_extract(user_json, '$.legacy.friends_count') AS following_count
                FROM (
                    SELECT last_seen_at,
                        CASE WHEN json_valid(raw_json) THEN CASE
                            WHEN json_type(raw_json, '$.core.user_results.result') = 'object'
                            THEN json_extract(raw_json, '$.core.user_results.result')
                        END END AS user_json
                    FROM archive
                    WHERE author_id IS NOT NULL AND author_id != '' AND author_id = ?
                        AND record_type IN ('{RecordType.TWEET}', '{RecordType.TWEET_OBJECT}')
                )
            )
            WHERE description IS NOT NULL OR followers_count IS NOT NULL
            GROUP BY display_name, username, description, followers_count, following_count
            ORDER BY fetched_at DESC
            LIMIT ?
            """,
            (author_id, limit),
        ).fetchall()
        return [
            {
                "display_name": row["display_name"],
                "username": row["username"],
                "description": row["description"] or "",
                "followers_count": row["followers_count"] or 0,
                "following_count": row["following_count"] or 0,
                "fetched_at": row["fetched_at"],
            }
            for row in rows
        ]

    def search_authors(self, query: str, limit: int = 10) -> list[dict[str, str]]:
        """Search for authors by username or display name."""
        if not query:
            return []

        query = query.lstrip("@")
        if not query:
            return []

        sql = f"""
            SELECT MIN(author_id) AS author_id,
                   author_username,
                   MIN(author_display_name) AS author_display_name
            FROM archive
            WHERE record_type = '{RecordType.TWEET}' 
              AND author_username IS NOT NULL
              AND (LOWER(author_username) LIKE LOWER(?) OR LOWER(author_display_name) LIKE LOWER(?))
            GROUP BY LOWER(author_username)
            ORDER BY author_username ASC
            LIMIT ?
        """
        pattern = f"%{query}%"
        rows = self.conn.execute(sql, (pattern, pattern, limit)).fetchall()
        return [
            {
                "id": r["author_id"],
                "username": r["author_username"],
                "display_name": r["author_display_name"],
            }
            for r in rows
        ]
