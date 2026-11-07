"""Archive facts behind the Analytics highlights: activity, people, topics and records."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .common import HIDDEN_TWEET_IDS_SQL, UNCAPTURED_TWEET_IDS_SQL
from .record_types import RecordType
from .sync_state import SyncStateMixin

_TOP_LIMIT = 10
# The like count the "viral" highlight starts at.
_VIRAL_LIKES = 100_000

# One membership row per saved post, so a post both liked and bookmarked counts once.
_ONE_MEMBERSHIP_PER_POST = f"""
    SELECT min(row_key) AS row_key
    FROM archive INDEXED BY idx_archive_membership
    WHERE record_type = '{RecordType.TWEET}' AND tweet_id IS NOT NULL
    GROUP BY tweet_id
"""
# Highlights describe what was saved, so attachments and links on conversation context
# (parents, replies and quoted posts kept around saved posts) stay out of them.
_SAVED_TWEET_IDS = f"""
    SELECT tweet_id FROM archive INDEXED BY idx_archive_membership
    WHERE record_type = '{RecordType.TWEET}'
"""
# Conversation context: post objects kept around saved posts that aren't saved themselves.
_CONTEXT_POSTS = (
    f"record_type = '{RecordType.TWEET_OBJECT}' AND tweet_id NOT IN ({_SAVED_TWEET_IDS})"
)
_CONTEXT_TWEET_IDS = f"SELECT tweet_id FROM archive WHERE {_CONTEXT_POSTS}"
# Attachment rows for context posts, read from the attachment index alone. The planner
# otherwise probes the tweet_id index once per context post and reads every row it finds.
_CONTEXT_ATTACHMENTS = f"""
    archive INDEXED BY idx_archive_search_attachment
    WHERE record_type IN ('{RecordType.MEDIA}', '{RecordType.URL_REF}')
      AND +tweet_id IN ({_CONTEXT_TWEET_IDS})
"""
_WORDS_SQL = "length(trim(text)) - length(replace(trim(text), ' ', '')) + 1"


@dataclass(slots=True)
class ActivitySeries:
    """Posting-time counts for one collection, or for all saved posts."""

    by_year: dict[int, int] = field(default_factory=dict)
    # 168 buckets, Sunday 00:00 UTC first, in hours.
    week_hours: list[int] = field(default_factory=lambda: [0] * 168)
    # Posts per UTC calendar day, keyed by days since 1970-01-01.
    days: dict[int, int] = field(default_factory=dict)
    total: int = 0


@dataclass(slots=True)
class GoneFacts:
    """Posts X no longer serves, split between saved posts and conversation context."""

    # Saved posts as `is:missing` finds them: captured and not hidden.
    saved: int = 0
    reasons: dict[str, int] = field(default_factory=dict)
    context: int = 0
    # Saved posts that were unavailable and later came back.
    resurrected: int = 0


@dataclass(slots=True)
class ContextFacts:
    """Totals for conversation context, shown beside the saved-post figures they extend.

    Like counts, hashtags and mentions are left out: they live in each post's raw JSON,
    and parsing it for every context post would make the report several times slower.
    """

    posts: int = 0
    words: int = 0
    media_types: dict[str, int] = field(default_factory=dict)
    video_seconds: int = 0
    activity: ActivitySeries = field(default_factory=ActivitySeries)
    # Accounts with the most replies among the context posts, the owner left out.
    reply_authors: list[dict[str, Any]] = field(default_factory=list)


_EPOCH_ORDINAL = date(1970, 1, 1).toordinal()


@dataclass(slots=True)
class InsightFacts:
    activity: dict[str, ActivitySeries]
    busiest_day: tuple[str, int] | None
    longest_streak: tuple[int, str | None, str | None]
    top_authors: list[dict[str, Any]]
    author_count: int
    one_time_authors: int
    regular_authors: int
    post_mix: dict[str, int]
    likes_on_x: int
    hashtags: list[tuple[str, int]]
    mentions: list[tuple[str, int]]
    media_types: dict[str, int]
    video_seconds: int
    words: int
    saved_twice: int
    viral: int
    gone: GoneFacts
    context: ContextFacts


class InsightFactsMixin(SyncStateMixin):
    def insight_facts(self) -> InsightFacts:
        owner = self.get_archive_owner_id()
        activity, busiest_day, streak = self._activity_facts()
        authors, author_count, one_time, regulars = self._author_facts(owner)
        post_mix, likes_on_x, viral, hashtags, mentions = self._post_fact_totals()
        media_types, video_seconds = self._media_facts()
        return InsightFacts(
            activity=activity,
            busiest_day=busiest_day,
            longest_streak=streak,
            top_authors=authors,
            author_count=author_count,
            one_time_authors=one_time,
            regular_authors=regulars,
            post_mix=post_mix,
            likes_on_x=likes_on_x,
            hashtags=hashtags,
            mentions=mentions,
            media_types=media_types,
            video_seconds=video_seconds,
            words=self._word_count(),
            saved_twice=self._saved_twice_count(),
            viral=viral,
            gone=self._gone_facts(),
            context=self._context_facts(owner),
        )

    def _activity_facts(
        self,
    ) -> tuple[
        dict[str, ActivitySeries], tuple[str, int] | None, tuple[int, str | None, str | None]
    ]:
        # Each post falls in one bucket, so distinct counts per bucket count a post once even
        # when it sits in several collections or bookmark folders. The record indexes cover
        # these columns, so none of these queries read table rows.
        bucket = (
            "CAST(strftime('%Y', created_at_ts, 'unixepoch') AS INTEGER), "
            "CAST(strftime('%w', created_at_ts, 'unixepoch') AS INTEGER) * 24 "
            "+ CAST(strftime('%H', created_at_ts, 'unixepoch') AS INTEGER)"
        )
        posted = f"record_type = '{RecordType.TWEET}' AND created_at_ts IS NOT NULL"
        series: dict[str, ActivitySeries] = {"all": ActivitySeries()}
        for collection, year, week_hour, count in self.conn.execute(
            f"""
            SELECT collection_type, {bucket}, count(DISTINCT tweet_id)
            FROM archive INDEXED BY idx_archive_record_collection_page
            WHERE {posted} AND collection_type IS NOT NULL AND collection_type != ''
            GROUP BY 1, 2, 3
            """
        ):
            self._add_bucket(
                series.setdefault(collection, ActivitySeries()), year, week_hour, count
            )
        for year, week_hour, count in self.conn.execute(
            f"""
            SELECT {bucket}, count(DISTINCT tweet_id)
            FROM archive INDEXED BY idx_archive_record_page
            WHERE {posted}
            GROUP BY 1, 2
            """
        ):
            self._add_bucket(series["all"], year, week_hour, count)
        for collection, day, count in self.conn.execute(
            f"""
            SELECT collection_type, created_at_ts / 86400, count(DISTINCT tweet_id)
            FROM archive INDEXED BY idx_archive_record_collection_page
            WHERE {posted} AND collection_type IS NOT NULL AND collection_type != ''
            GROUP BY 1, 2
            """
        ):
            series.setdefault(collection, ActivitySeries()).days[int(day)] = count
        everything = series["all"].days
        for day, count in self.conn.execute(
            f"""
            SELECT created_at_ts / 86400, count(DISTINCT tweet_id)
            FROM archive INDEXED BY idx_archive_record_page
            WHERE {posted}
            GROUP BY 1
            """
        ):
            everything[int(day)] = count
        busiest_day = min(everything, key=lambda day: (-everything[day], day), default=None)
        busiest = (
            (
                date.fromordinal(_EPOCH_ORDINAL + busiest_day).isoformat(),
                everything[busiest_day],
            )
            if busiest_day is not None
            else None
        )
        tweet_days = {
            date.fromordinal(_EPOCH_ORDINAL + day)
            for day in series.get("tweet", ActivitySeries()).days
        }
        return series, busiest, self._longest_streak(tweet_days)

    @staticmethod
    def _add_bucket(target: ActivitySeries, year: int, week_hour: int, count: int) -> None:
        target.by_year[year] = target.by_year.get(year, 0) + count
        target.week_hours[week_hour] += count
        target.total += count

    @staticmethod
    def _longest_streak(days: set[date]) -> tuple[int, str | None, str | None]:
        best = (0, None, None)
        for day in days:
            if date.fromordinal(day.toordinal() - 1) in days:
                continue
            end = day
            while date.fromordinal(end.toordinal() + 1) in days:
                end = date.fromordinal(end.toordinal() + 1)
            length = end.toordinal() - day.toordinal() + 1
            if length > best[0]:
                best = (length, day.isoformat(), end.isoformat())
        return best

    def _author_facts(self, owner: str | None) -> tuple[list[dict[str, Any]], int, int, int]:
        rows = self.conn.execute(
            f"""
            SELECT author_id, max(author_username), max(author_display_name),
                   count(DISTINCT tweet_id),
                   sum(collection_type = 'like'), sum(collection_type = 'bookmark')
            FROM archive
            WHERE record_type = '{RecordType.TWEET}'
              AND author_id IS NOT NULL AND author_id != '' AND author_id IS NOT ?
            GROUP BY author_id
            """,
            (owner,),
        ).fetchall()
        ranked = sorted(rows, key=lambda row: (-int(row[3]), str(row[1] or "").lower()))
        top = [
            {
                "author_id": str(row[0]),
                "username": row[1] or "",
                "display_name": row[2] or row[1] or "",
                "count": int(row[3]),
                "likes": int(row[4] or 0),
                "bookmarks": int(row[5] or 0),
            }
            for row in ranked[:_TOP_LIMIT]
        ]
        one_time = sum(1 for row in rows if int(row[3]) == 1)
        regulars = sum(1 for row in rows if int(row[3]) >= 10)
        return top, len(rows), one_time, regulars

    def _post_fact_totals(
        self,
    ) -> tuple[dict[str, int], int, int, list[tuple[str, int]], list[tuple[str, int]]]:
        row = self.conn.execute(
            f"""
            SELECT count(*), sum(f.is_reply), sum(f.is_quote), sum(f.is_retweet),
                   sum(f.is_verified), sum(f.favorite_count), sum(f.favorite_count >= ?)
            FROM ({_ONE_MEMBERSHIP_PER_POST}) AS one
            JOIN archive_tweet_facts AS f ON f.row_key = one.row_key
            """,
            (_VIRAL_LIKES,),
        ).fetchone()
        total, replies, quotes, reposts, verified, likes, viral = (int(value or 0) for value in row)
        with_media, with_links = (
            int(value or 0)
            for value in self.conn.execute(
                f"""
                SELECT
                    count(DISTINCT tweet_id) FILTER (WHERE record_type = '{RecordType.MEDIA}'),
                    count(DISTINCT tweet_id) FILTER (WHERE record_type = '{RecordType.URL_REF}')
                FROM archive INDEXED BY idx_archive_search_attachment
                WHERE record_type IN ('{RecordType.MEDIA}', '{RecordType.URL_REF}')
                  AND tweet_id IN (
                      SELECT tweet_id FROM archive INDEXED BY idx_archive_membership
                      WHERE record_type = '{RecordType.TWEET}'
                  )
                """
            ).fetchone()
        )
        hashtags: Counter[str] = Counter()
        mentions: Counter[str] = Counter()
        for tags, names in self.conn.execute(
            f"""
            SELECT f.hashtags, f.mentions
            FROM ({_ONE_MEMBERSHIP_PER_POST}) AS one
            JOIN archive_tweet_facts AS f ON f.row_key = one.row_key
            WHERE f.hashtags IS NOT NULL OR f.mentions IS NOT NULL
            """
        ):
            # Facts store lowercased values wrapped in and separated by char(31).
            hashtags.update({value for value in (tags or "").split("\x1f") if value})
            mentions.update({value for value in (names or "").split("\x1f") if value})
        mix = {
            "total": total,
            "replies": replies,
            "quotes": quotes,
            "reposts": reposts,
            "verified": verified,
            "with_media": with_media,
            "with_links": with_links,
        }
        return mix, likes, viral, _top(hashtags), _top(mentions)

    def _media_facts(self) -> tuple[dict[str, int], int]:
        types: dict[str, int] = {}
        video_millis = 0
        for media_type, count, millis in self.conn.execute(
            f"""
            SELECT media_type, count(*), sum(CAST(duration_millis AS INTEGER))
            FROM archive INDEXED BY idx_archive_record_page
            WHERE record_type = '{RecordType.MEDIA}' AND tweet_id IN ({_SAVED_TWEET_IDS})
            GROUP BY media_type
            """
        ):
            types[str(media_type or "photo")] = types.get(str(media_type or "photo"), 0) + count
            if media_type == "video":
                video_millis += int(millis or 0)
        return types, video_millis // 1000

    def _word_count(self) -> int:
        # Words are runs between spaces; each post counts once, at its longest saved text.
        row = self.conn.execute(
            f"""
            SELECT sum(words) FROM (
                SELECT max({_WORDS_SQL}) AS words
                FROM archive
                WHERE record_type = '{RecordType.TWEET}' AND trim(COALESCE(text, '')) != ''
                GROUP BY tweet_id
            )
            """
        ).fetchone()
        return int(row[0] or 0) if row else 0

    def _saved_twice_count(self) -> int:
        row = self.conn.execute(
            f"""
            SELECT count(*) FROM (
                SELECT tweet_id
                FROM archive INDEXED BY idx_archive_membership
                WHERE record_type = '{RecordType.TWEET}'
                  AND collection_type IN ('like', 'bookmark')
                GROUP BY tweet_id
                HAVING count(DISTINCT collection_type) = 2
            )
            """
        ).fetchone()
        return int(row[0] or 0) if row else 0

    def _gone_facts(self) -> GoneFacts:
        gone = GoneFacts()
        unavailable = (
            f"record_type = '{RecordType.TWEET_OBJECT}' "
            "AND enrichment_state = 'terminal_unavailable'"
        )
        for reason, count in self.conn.execute(
            f"""
            SELECT COALESCE(NULLIF(enrichment_reason, ''), 'unavailable_unknown'), count(*)
            FROM archive
            WHERE {unavailable} AND tweet_id IN ({_SAVED_TWEET_IDS})
              AND tweet_id NOT IN ({HIDDEN_TWEET_IDS_SQL})
              AND tweet_id NOT IN ({UNCAPTURED_TWEET_IDS_SQL})
            GROUP BY 1
            """
        ):
            gone.reasons[str(reason)] = int(count)
        gone.saved = sum(gone.reasons.values())
        context, resurrected = self.conn.execute(
            f"""
            SELECT
                count(*) FILTER (WHERE {unavailable} AND tweet_id NOT IN ({_SAVED_TWEET_IDS})),
                count(*) FILTER (
                    WHERE enrichment_state = 'resurrected' AND tweet_id IN ({_SAVED_TWEET_IDS})
                )
            FROM archive
            WHERE record_type = '{RecordType.TWEET_OBJECT}'
              AND enrichment_state IN ('terminal_unavailable', 'resurrected')
            """
        ).fetchone()
        gone.context, gone.resurrected = int(context or 0), int(resurrected or 0)
        return gone

    def _context_facts(self, owner: str | None) -> ContextFacts:
        context = ContextFacts()
        names: dict[str, tuple[str, str]] = {}
        # Who wrote each post a reply could answer: context posts, read in the pass below,
        # and saved posts.
        authors: dict[str, str] = dict(
            self.conn.execute(
                f"""
                SELECT tweet_id, author_id FROM archive INDEXED BY idx_archive_membership
                WHERE record_type = '{RecordType.TWEET}' AND author_id IS NOT NULL
                """
            )
        )
        replies: list[tuple[str, str, str]] = []
        # One pass over the context posts. A post object exists once per post, so no
        # deduplication is needed. A post whose conversation started elsewhere is a reply.
        for tweet_id, text, author_id, username, display_name, conversation_id in self.conn.execute(
            f"""
            SELECT tweet_id, text, author_id, author_username, author_display_name,
                   conversation_id
            FROM archive
            WHERE {_CONTEXT_POSTS}
            """
        ):
            context.posts += 1
            # Matches the saved-post count: runs between spaces, after SQLite's trim().
            stripped = (text or "").strip(" ")
            if stripped:
                context.words += stripped.count(" ") + 1
            if not author_id:
                continue
            authors[tweet_id] = author_id
            if conversation_id and conversation_id != tweet_id and author_id != owner:
                replies.append((tweet_id, author_id, conversation_id))
                if username or display_name:
                    names[author_id] = (username or "", display_name or username or "")
        answered = dict(
            self.conn.execute(
                f"""
                SELECT tweet_id, target_tweet_id FROM archive
                WHERE record_type = '{RecordType.TWEET_RELATION}' AND relation_type = 'reply_to'
                """
            )
        )
        # A reply ranks its author only when someone else wrote the conversation's first
        # post and the post it answers, so continuing your own thread isn't replying.
        repliers = Counter(
            author_id
            for tweet_id, author_id, conversation_id in replies
            if author_id != authors.get(conversation_id)
            and author_id != authors.get(answered.get(tweet_id, ""))
        )
        context.reply_authors = [
            {
                "author_id": str(author_id),
                "username": names.get(author_id, ("", ""))[0],
                "display_name": names.get(author_id, ("", ""))[1],
                "count": count,
            }
            for author_id, count in sorted(
                repliers.items(),
                key=lambda item: (-item[1], names.get(item[0], ("", ""))[0].lower()),
            )[:_TOP_LIMIT]
        ]
        # Posting times, from the record index alone. A post object exists once per post.
        activity = context.activity
        for day, hour, count in self.conn.execute(
            f"""
            SELECT created_at_ts / 86400,
                   CAST(strftime('%H', created_at_ts, 'unixepoch') AS INTEGER), count(*)
            FROM archive INDEXED BY idx_archive_record_page
            WHERE {_CONTEXT_POSTS} AND created_at_ts IS NOT NULL
            GROUP BY 1, 2
            """
        ):
            posted = date.fromordinal(_EPOCH_ORDINAL + int(day))
            # isoweekday() is 7 for Sunday; the week buckets start on Sunday.
            week_hour = posted.isoweekday() % 7 * 24 + int(hour)
            self._add_bucket(activity, posted.year, week_hour, int(count))
            activity.days[int(day)] = activity.days.get(int(day), 0) + int(count)
        for media_type, count in self.conn.execute(
            f"""
            SELECT media_type, count(*) FROM {_CONTEXT_ATTACHMENTS}
              AND record_type = '{RecordType.MEDIA}'
            GROUP BY media_type
            """
        ):
            key = str(media_type or "photo")
            context.media_types[key] = context.media_types.get(key, 0) + int(count)
        # Durations aren't in the index, so only the video rows it finds are read.
        row = self.conn.execute(
            f"""
            SELECT sum(CAST(duration_millis AS INTEGER)) FROM archive
            WHERE rowid IN (
                SELECT rowid FROM {_CONTEXT_ATTACHMENTS}
                  AND record_type = '{RecordType.MEDIA}' AND media_type = 'video'
            )
            """
        ).fetchone()
        context.video_seconds = int(row[0] or 0) // 1000 if row else 0
        return context


def _top(counter: Counter[str]) -> list[tuple[str, int]]:
    return sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:_TOP_LIMIT]
