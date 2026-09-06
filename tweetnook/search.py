"""Shared archive tweet search used by the CLI and Web API."""

from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from tweetnook.export.common import normalize_collection_name
from tweetnook.like_order import LIKE_SORTS

_FILTER_KEYS = frozenset(
    {
        "from",
        "to",
        "mentions",
        "since",
        "until",
        "since_time",
        "until_time",
        "since_id",
        "max_id",
        "has",
        "is",
        "filter",
        "min_retweets",
        "min_faves",
        "min_replies",
        "conversation_id",
        "quoted_tweet_id",
        "url",
        "source",
        "card_name",
        "tag",
        "hashtag",
    }
)
_FILTER_VALUES = {
    "has": frozenset({"article", "media", "image", "video", "links"}),
    "is": frozenset({"quote", "reply", "resurrected", "retweet", "thread", "verified"}),
    "filter": frozenset(
        {
            "articles",
            "images",
            "links",
            "media",
            "native_video",
            "nativeretweets",
            "quote",
            "replies",
            "self_threads",
            "threads",
            "verified",
            "videos",
        }
    ),
}
_NUMERIC_FILTERS = frozenset(
    {
        "since_time",
        "until_time",
        "since_id",
        "max_id",
        "min_retweets",
        "min_faves",
        "min_replies",
    }
)
_TOKEN_PATTERN = re.compile(r'-?[\w_]+:(?:"[^"]*"|[^\s]+)|-?"[^"]*"|[^\s]+')


class SearchQueryError(ValueError):
    """Raised when a shared search query is malformed."""


@dataclass(frozen=True, slots=True)
class SearchClause:
    kind: Literal["text", "filter"]
    value: str
    key: str | None = None
    negated: bool = False


@dataclass(frozen=True, slots=True)
class ParsedSearchQuery:
    groups: tuple[tuple[SearchClause, ...], ...]

    @property
    def has_or(self) -> bool:
        return any(len(group) > 1 for group in self.groups)

    @property
    def has_text(self) -> bool:
        return any(clause.kind == "text" for group in self.groups for clause in group)

    @property
    def has_positive_text(self) -> bool:
        return any(
            clause.kind == "text" and not clause.negated
            for group in self.groups
            for clause in group
        )

    @property
    def has_negative_text(self) -> bool:
        return any(
            clause.kind == "text" and clause.negated for group in self.groups for clause in group
        )

    def positive_text_terms(self) -> list[str]:
        terms: list[str] = []
        for group in self.groups:
            for clause in group:
                if clause.kind != "text" or clause.negated:
                    continue
                value = clause.value
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                terms.extend(part for part in value.split() if part)
        return list(dict.fromkeys(terms))

    def conjunctive_parts(self) -> tuple[dict[str, list[str]], str]:
        """Return the legacy filter/text split for a query without OR or negative text."""
        filters: dict[str, list[str]] = {}
        text: list[str] = []
        for group in self.groups:
            clause = group[0]
            if clause.kind == "text":
                text.append(clause.value)
                continue
            key = f"-{clause.key}" if clause.negated else str(clause.key)
            filters.setdefault(key, []).append(clause.value)
        return filters, " ".join(text)


@dataclass(slots=True)
class SearchPage:
    rows: list[dict[str, Any]]
    total: int
    page: int
    pages: int
    truncated: bool = False


def _strip_quotes(value: str) -> str:
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    return value


def _validate_filter(key: str, value: str) -> None:
    if key not in _FILTER_KEYS:
        raise SearchQueryError(f"Unsupported search filter: {key}:")
    if not value:
        raise SearchQueryError(f"Search filter {key}: requires a value.")
    allowed = _FILTER_VALUES.get(key)
    if allowed is not None and value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise SearchQueryError(f"Unsupported {key}: value {value!r}. Expected one of: {choices}.")
    if key in {"since", "until"} and _parse_twitter_date(value) is None:
        raise SearchQueryError(f"Search filter {key}: expects YYYY-MM-DD.")
    if key in _NUMERIC_FILTERS:
        try:
            float(value) if key.endswith("_time") else int(value)
        except ValueError as exc:
            raise SearchQueryError(f"Search filter {key}: expects a number.") from exc


def parse_search_query(query: str | None) -> ParsedSearchQuery:
    """Parse implicit-AND search text with adjacent-clause OR groups."""
    groups: list[list[SearchClause]] = []
    join_with_or = False
    negate_next = False
    expecting_clause = False

    for token in _TOKEN_PATTERN.findall(query or ""):
        if token == "OR":
            if not groups or join_with_or or expecting_clause:
                raise SearchQueryError("OR must appear between two search clauses.")
            join_with_or = True
            expecting_clause = True
            continue
        if token == "AND":
            if not groups or join_with_or or expecting_clause:
                raise SearchQueryError("AND must appear between two search clauses.")
            expecting_clause = True
            continue
        if token == "NOT":
            if negate_next:
                raise SearchQueryError("NOT must be followed by a search clause.")
            negate_next = True
            expecting_clause = True
            continue

        negated = token.startswith("-")
        if negated:
            token = token[1:]
        negated = negated ^ negate_next
        negate_next = False

        filter_match = re.fullmatch(r'([\w_]+):("[^"]*"|[^\s]+)', token)
        if filter_match:
            key, raw_value = filter_match.groups()
            key = key.lower()
            value = _strip_quotes(raw_value).lower()
            _validate_filter(key, value)
            clause = SearchClause("filter", value, key=key, negated=negated)
        elif token.startswith("#") and len(token) > 1:
            clause = SearchClause("filter", token[1:].lower(), key="hashtag", negated=negated)
        else:
            if not token:
                raise SearchQueryError("Search terms cannot be empty.")
            clause = SearchClause("text", token, negated=negated)

        if join_with_or:
            groups[-1].append(clause)
        else:
            groups.append([clause])
        join_with_or = False
        expecting_clause = False

    if join_with_or or negate_next or expecting_clause:
        raise SearchQueryError("Search query cannot end with an operator.")
    return ParsedSearchQuery(tuple(tuple(group) for group in groups))


def _extract_advanced_filters(q: str | None) -> tuple[dict[str, list[str]], str]:
    """Compatibility projection used by route-level tests and older callers."""
    parsed = parse_search_query(q)
    filters: dict[str, list[str]] = {}
    text: list[str] = []
    for group in parsed.groups:
        for clause in group:
            if clause.kind == "text":
                prefix = "-" if clause.negated else ""
                text.append(f"{prefix}{clause.value}")
            else:
                key = f"-{clause.key}" if clause.negated else str(clause.key)
                filters.setdefault(key, []).append(clause.value)
    return filters, " ".join(text)


def _parse_twitter_date(date_str: str) -> float | None:
    if not date_str:
        return None
    try:
        if "_" in date_str:
            parts = date_str.split("_")
            dt = datetime.strptime(f"{parts[0]} {parts[1]}", "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=UTC
            )
            return dt.timestamp()
        return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC).timestamp()
    except (ValueError, IndexError):
        return None


def _row_created_at_timestamp(row: dict[str, Any]) -> float | None:
    raw_ts = row.get("created_at_ts")
    if isinstance(raw_ts, int | float):
        return float(raw_ts)
    raw = row.get("created_at")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        try:
            return datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y").timestamp()
        except ValueError:
            return None


def _filter_matches(row: dict[str, Any], key: str, value: str) -> bool:
    raw = row.get("raw_json") or {}
    if not isinstance(raw, dict):
        raw = {}
    legacy = raw.get("legacy") or {}
    author = row.get("author") or {}

    if key == "from":
        return author.get("username", "").lower() == value.replace("@", "")
    if key == "to":
        return (legacy.get("in_reply_to_screen_name") or "").lower() == value.replace("@", "")
    if key == "mentions":
        mentions = [
            mention.get("screen_name", "").lower()
            for mention in legacy.get("entities", {}).get("user_mentions", [])
        ]
        return value.replace("@", "") in mentions
    if key in {"since", "until"}:
        boundary = _parse_twitter_date(value)
        created = _row_created_at_timestamp(row)
        if boundary is None or created is None:
            return False
        return created >= boundary if key == "since" else created < boundary
    if key in {"since_time", "until_time"}:
        created = _row_created_at_timestamp(row)
        if created is None:
            return False
        boundary = float(value)
        return created >= boundary if key == "since_time" else created < boundary
    if key == "since_id":
        return int(row.get("tweet_id", 0)) > int(value)
    if key == "max_id":
        return int(row.get("tweet_id", 0)) <= int(value)
    if key == "has":
        if value == "article":
            return bool(row.get("article"))
        if value == "media":
            return bool(row.get("media"))
        if value == "image":
            return any(media.get("type") == "photo" for media in row.get("media", []))
        if value == "video":
            return any(
                media.get("type") in {"video", "animated_gif"} for media in row.get("media", [])
            )
        if value == "links":
            return bool(row.get("urls"))
    if key == "is":
        if value == "reply":
            return bool(legacy.get("in_reply_to_status_id_str"))
        if value == "quote":
            return bool(legacy.get("is_quote_status"))
        if value == "retweet":
            return bool(legacy.get("retweeted_status_id_str"))
        if value == "thread":
            reply_username = (legacy.get("in_reply_to_screen_name") or "").lower()
            author_username = (author.get("username") or "").lower()
            return bool(reply_username) and reply_username == author_username
        if value == "verified":
            user_result = raw.get("core", {}).get("user_results", {}).get("result", {})
            return bool(
                user_result.get("is_blue_verified") or user_result.get("legacy", {}).get("verified")
            )
        if value == "resurrected":
            return row.get("enrichment_state") == "resurrected"
    if key == "filter":
        if value == "articles":
            return bool(row.get("article"))
        if value == "replies":
            return bool(legacy.get("in_reply_to_status_id_str"))
        if value == "quote":
            return bool(legacy.get("is_quote_status"))
        if value == "nativeretweets":
            return bool(legacy.get("retweeted_status_id_str"))
        if value in {"self_threads", "threads"}:
            reply_username = (legacy.get("in_reply_to_screen_name") or "").lower()
            author_username = (author.get("username") or "").lower()
            return bool(reply_username) and reply_username == author_username
        if value == "media":
            return bool(row.get("media"))
        if value == "images":
            return any(media.get("type") == "photo" for media in row.get("media", []))
        if value in {"videos", "native_video"}:
            return any(
                media.get("type") in {"video", "animated_gif"} for media in row.get("media", [])
            )
        if value == "links":
            return bool(row.get("urls"))
        if value == "verified":
            user_result = raw.get("core", {}).get("user_results", {}).get("result", {})
            return bool(
                user_result.get("is_blue_verified") or user_result.get("legacy", {}).get("verified")
            )
    if key == "min_retweets":
        return int(legacy.get("retweet_count", 0)) >= int(value)
    if key == "min_faves":
        return int(legacy.get("favorite_count", 0)) >= int(value)
    if key == "min_replies":
        return int(legacy.get("reply_count", 0)) >= int(value)
    if key == "conversation_id":
        return legacy.get("conversation_id_str") == value
    if key == "quoted_tweet_id":
        return legacy.get("quoted_status_id_str") == value
    if key == "url":
        return any(
            value in (url.get("expanded_url") or "").lower()
            or value in (url.get("display_url") or "").lower()
            for url in row.get("urls", [])
        )
    if key == "source":
        return value.replace("_", " ") in raw.get("source", "").lower()
    if key == "card_name":
        return raw.get("card", {}).get("name") == value
    if key == "tag":
        tags = [
            *(row.get("media_tags") or {}).get("tags", []),
            *(row.get("qt_media_tags") or {}).get("tags", []),
        ]
        return any(value == tag.lower() for tag in tags)
    if key == "hashtag":
        hashtags = [
            hashtag.get("text", "").lower()
            for hashtag in legacy.get("entities", {}).get("hashtags", [])
        ]
        return value.replace("#", "").lower() in hashtags
    return False


def _apply_advanced_filters(
    rows: list[dict[str, Any]], filters: dict[str, list[str]]
) -> list[dict[str, Any]]:
    """Apply AND-by-default structured filters to hydrated tweet rows."""
    if not filters:
        return rows
    filtered: list[dict[str, Any]] = []
    for row in rows:
        keep = True
        for raw_key, values in filters.items():
            negated = raw_key.startswith("-")
            key = raw_key[1:] if negated else raw_key
            for value in values:
                try:
                    matched = _filter_matches(row, key, value)
                except (TypeError, ValueError):
                    matched = False
                if matched == negated:
                    keep = False
                    break
            if not keep:
                break
        if keep:
            filtered.append(row)
    return filtered


def _sql_quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _collection_expr(collections: set[str] | None) -> str:
    if not collections:
        return ""
    if len(collections) == 1:
        return f"collection_type = {_sql_quote(next(iter(collections)))}"
    values = ", ".join(_sql_quote(value) for value in sorted(collections))
    return f"collection_type IN ({values})"


def _normalized_filter_expr(key: str, value: str, *, negated: bool = False) -> str | None:
    """Translate filters backed by archive columns or JSON into SQLite predicates."""
    condition = None
    related_index = "idx_archive_tweet_id"
    if (key, value) in {("has", "media"), ("filter", "media")}:
        condition = "related.record_type = 'media'"
    elif (key, value) in {("has", "image"), ("filter", "images")}:
        condition = "related.record_type = 'media' AND related.media_type = 'photo'"
    elif (key, value) in {
        ("has", "video"),
        ("filter", "videos"),
        ("filter", "native_video"),
    }:
        condition = (
            "related.record_type = 'media' AND related.media_type IN ('video', 'animated_gif')"
        )
    elif (key, value) in {("has", "links"), ("filter", "links")}:
        condition = "related.record_type = 'url_ref'"
    elif (key, value) in {("has", "article"), ("filter", "articles")}:
        condition = "related.record_type = 'article'"
    elif (key, value) == ("is", "resurrected"):
        condition = (
            "related.record_type = 'tweet_object' AND related.enrichment_state = 'resurrected'"
        )
    if condition is not None:
        if (key, value) in {
            ("has", "media"),
            ("filter", "media"),
            ("has", "image"),
            ("filter", "images"),
            ("has", "video"),
            ("filter", "videos"),
            ("filter", "native_video"),
            ("has", "links"),
            ("filter", "links"),
        }:
            related_index = "idx_archive_search_attachment"
            condition += " AND related.record_type IN ('media', 'url_ref')"
        expression = (
            f"EXISTS (SELECT 1 FROM archive AS related INDEXED BY {related_index} "
            "WHERE related.tweet_id = archive.tweet_id "
            f"AND {condition})"
        )
        return f"NOT (COALESCE(({expression}), 0))" if negated else expression

    legacy_path = "$.legacy"
    expression = None
    if key == "from":
        expression = f"LOWER(COALESCE(author_username, '')) = {_sql_quote(value.replace('@', ''))}"
    elif key == "to":
        expression = (
            f"LOWER(COALESCE(json_extract(raw_json, '{legacy_path}.in_reply_to_screen_name'), "
            f"'')) = {_sql_quote(value.replace('@', ''))}"
        )
    elif key == "mentions":
        expression = (
            "EXISTS (SELECT 1 FROM json_each(CASE WHEN json_valid(archive.raw_json) "
            "THEN archive.raw_json ELSE '{}' END, '$.legacy.entities.user_mentions') AS mention "
            "WHERE LOWER(COALESCE(json_extract(mention.value, '$.screen_name'), '')) = "
            f"{_sql_quote(value.replace('@', ''))})"
        )
    elif key in {"since", "since_time", "until", "until_time"}:
        boundary = _parse_twitter_date(value) if key in {"since", "until"} else float(value)
        comparison = ">=" if key in {"since", "since_time"} else "<"
        expression = f"created_at_ts {comparison} {int(boundary)}"
    elif key == "since_id":
        expression = f"CAST(tweet_id AS INTEGER) > {int(value)}"
    elif key == "max_id":
        expression = f"CAST(tweet_id AS INTEGER) <= {int(value)}"
    elif (key, value) in {("is", "reply"), ("filter", "replies")}:
        expression = (
            f"NULLIF(json_extract(raw_json, '{legacy_path}.in_reply_to_status_id_str'), "
            "'') IS NOT NULL"
        )
    elif (key, value) in {("is", "quote"), ("filter", "quote")}:
        expression = f"COALESCE(json_extract(raw_json, '{legacy_path}.is_quote_status'), 0) != 0"
    elif (key, value) in {("is", "retweet"), ("filter", "nativeretweets")}:
        expression = (
            f"NULLIF(json_extract(raw_json, '{legacy_path}.retweeted_status_id_str'), "
            "'') IS NOT NULL"
        )
    elif (key, value) in {
        ("is", "thread"),
        ("filter", "self_threads"),
        ("filter", "threads"),
    }:
        reply_path = f"{legacy_path}.in_reply_to_screen_name"
        expression = (
            f"NULLIF(json_extract(raw_json, '{reply_path}'), '') IS NOT NULL AND "
            f"LOWER(json_extract(raw_json, '{reply_path}')) = "
            "LOWER(COALESCE(author_username, ''))"
        )
    elif (key, value) in {("is", "verified"), ("filter", "verified")}:
        expression = (
            "COALESCE(json_extract(raw_json, '$.core.user_results.result.is_blue_verified'), "
            "json_extract(raw_json, '$.core.user_results.result.legacy.verified'), 0) != 0"
        )
    elif key in {"min_retweets", "min_faves", "min_replies"}:
        count_field = {
            "min_retweets": "retweet_count",
            "min_faves": "favorite_count",
            "min_replies": "reply_count",
        }[key]
        expression = (
            f"CAST(COALESCE(json_extract(raw_json, '{legacy_path}.{count_field}'), 0) "
            f"AS INTEGER) >= {int(value)}"
        )
    elif key == "conversation_id":
        expression = f"conversation_id = {_sql_quote(value)}"
    elif key == "quoted_tweet_id":
        expression = (
            f"json_extract(raw_json, '{legacy_path}.quoted_status_id_str') = {_sql_quote(value)}"
        )
    elif key == "url":
        escaped = _sql_quote(f"%{value}%")
        expression = (
            "EXISTS (SELECT 1 FROM archive AS related INDEXED BY idx_archive_search_attachment "
            "WHERE related.tweet_id = archive.tweet_id AND related.record_type = 'url_ref' "
            "AND related.record_type IN ('media', 'url_ref') "
            f"AND (LOWER(COALESCE(related.expanded_url, '')) LIKE {escaped} "
            f"OR LOWER(COALESCE(related.display_url, '')) LIKE {escaped}))"
        )
    elif key == "source":
        expression = (
            "LOWER(COALESCE(json_extract(raw_json, '$.source'), '')) LIKE "
            f"{_sql_quote('%' + value.replace('_', ' ') + '%')}"
        )
    elif key == "card_name":
        expression = f"json_extract(raw_json, '$.card.name') = {_sql_quote(value)}"
    elif key == "tag":
        normalized_value = _sql_quote(value.lower())
        expression = (
            "tweet_id IN ("
            "WITH matching_tags(tweet_id) AS MATERIALIZED ("
            "SELECT DISTINCT tagged.tweet_id "
            "FROM archive tagged INDEXED BY idx_archive_media_tag_lookup "
            "JOIN json_each(CASE WHEN json_valid(tagged.raw_json) "
            "THEN tagged.raw_json ELSE '{}' END, '$.tags') AS media_tag "
            "WHERE tagged.record_type = 'media_tag' "
            "AND media_tag.type = 'text' "
            f"AND LOWER(CAST(media_tag.value AS TEXT)) = {normalized_value}) "
            "SELECT tweet_id FROM matching_tags "
            "UNION SELECT relation.tweet_id FROM matching_tags "
            "CROSS JOIN archive relation INDEXED BY idx_archive_target_tweet_id "
            "WHERE relation.target_tweet_id = matching_tags.tweet_id "
            "AND relation.record_type = 'tweet_relation' "
            "AND relation.relation_type = 'quote_of' "
            ")"
        )
    elif key == "hashtag":
        expression = (
            "EXISTS (SELECT 1 FROM json_each(CASE WHEN json_valid(archive.raw_json) "
            "THEN archive.raw_json ELSE '{}' END, '$.legacy.entities.hashtags') AS hashtag "
            "WHERE LOWER(COALESCE(json_extract(hashtag.value, '$.text'), '')) = "
            f"{_sql_quote(value.replace('#', '').lower())})"
        )

    if expression is None:
        return None
    return f"NOT (COALESCE(({expression}), 0))" if negated else expression


def _filter_candidate_ids(store: Any, tweet_ids: list[str], expressions: list[str]) -> set[str]:
    """Apply SQL predicates to a bounded list of tweet candidate IDs."""
    if not expressions:
        return set(tweet_ids)
    matched: set[str] = set()
    for chunk_start in range(0, len(tweet_ids), 100):
        chunk = tweet_ids[chunk_start : chunk_start + 100]
        ids = ", ".join(_sql_quote(tweet_id) for tweet_id in chunk)
        expr = f"record_type = 'tweet' AND tweet_id IN ({ids})"
        for expression in expressions:
            expr += f" AND {expression}"
        rows = store._query(expr=expr, cols=["DISTINCT tweet_id"])
        matched.update(row["tweet_id"] for row in rows if row.get("tweet_id"))
    return matched


def _effective_sort(sort: str, *, has_positive_text: bool) -> str:
    if sort in LIKE_SORTS:
        return sort
    if sort == "oldest":
        return "oldest"
    if sort == "random":
        return "random"
    if sort == "relevance" and has_positive_text:
        return "relevance"
    if sort == "default":
        return "relevance" if has_positive_text else "newest"
    return "newest"


def _sort_rows(rows: list[dict[str, Any]], sort: str, *, store: Any = None) -> None:
    if sort in LIKE_SORTS:
        order = store.get_like_order().ordered_ids(sort)
        ranks = {tweet_id: rank for rank, tweet_id in enumerate(order)}
        rows.sort(key=lambda row: (ranks.get(row.get("tweet_id"), len(ranks)), row["tweet_id"]))
        return
    if sort == "random":
        random.shuffle(rows)
        return

    def sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
        timestamp = _row_created_at_timestamp(row)
        missing = timestamp is None
        timestamp = timestamp or 0.0
        tweet_id = row.get("tweet_id") or ""
        return (missing, timestamp if sort == "oldest" else -timestamp, tweet_id)

    rows.sort(key=sort_key)


def _hydrate_metadata(
    rows: list[dict[str, Any]], hits: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    hit_by_id = {hit.get("tweet_id"): hit for hit in hits}
    for row in rows:
        hit = hit_by_id.get(row.get("tweet_id"), {})
        row["type"] = "post"
        if hit.get("collections") is not None:
            row["collections"] = hit["collections"]
        if hit.get("match_score") is not None:
            row["match_score"] = hit["match_score"]
    return rows


def _page(rows: list[dict[str, Any]], *, page: int, limit: int, truncated: bool) -> SearchPage:
    total = len(rows)
    start = (page - 1) * limit
    return SearchPage(
        rows=rows[start : start + limit],
        total=total,
        page=page,
        pages=math.ceil(total / limit) if total else 1,
        truncated=truncated,
    )


def _collection_ids(store: Any, collections: set[str] | None) -> set[str] | None:
    expr = _collection_expr(collections)
    if not expr:
        return None
    rows = store._query(
        expr=f"record_type = 'tweet' AND {expr}",
        cols=["DISTINCT tweet_id"],
    )
    return {row["tweet_id"] for row in rows if row.get("tweet_id")}


def _export_candidates(store: Any, collections: set[str] | None) -> list[dict[str, Any]]:
    rows = store.export_rows("all", sort="newest", include_raw_json=True)
    allowed_ids = _collection_ids(store, collections)
    if allowed_ids is not None:
        rows = [row for row in rows if row.get("tweet_id") in allowed_ids]
    return rows


def _search_post_candidates(
    store: Any,
    query: str,
    *,
    limit: int,
    collections: set[str] | None,
) -> list[dict[str, Any]]:
    narrow_search = getattr(store, "search_post_fts_candidates", None)
    if callable(narrow_search):
        return narrow_search(query, limit=limit, collections=collections)
    return store.search_fts(
        query,
        limit=limit,
        types={"post"},
        collections=collections,
    )


def _search_positive_text_or(
    store: Any,
    parsed: ParsedSearchQuery,
    *,
    collections: set[str] | None,
    sort: str,
    page: int,
    limit: int,
    candidate_limit: int,
) -> SearchPage | None:
    if len(parsed.groups) != 1 or not all(
        clause.kind == "text" and not clause.negated for clause in parsed.groups[0]
    ):
        return None
    text_query = " OR ".join(clause.value for clause in parsed.groups[0])
    hits = _search_post_candidates(
        store,
        text_query,
        limit=candidate_limit,
        collections=collections,
    )
    effective_sort = _effective_sort(sort, has_positive_text=True)
    if effective_sort != "relevance":
        _sort_rows(hits, effective_sort, store=store)
    total = len(hits)
    start = (page - 1) * limit
    page_hits = hits[start : start + limit]
    ids = [hit["tweet_id"] for hit in page_hits if hit.get("tweet_id")]
    return SearchPage(
        rows=_hydrate_metadata(store.fetch_tweets_by_ids(ids), page_hits),
        total=total,
        page=page,
        pages=math.ceil(total / limit) if total else 1,
        truncated=len(hits) >= candidate_limit,
    )


def _search_negative_text_only(
    store: Any,
    parsed: ParsedSearchQuery,
    *,
    collections: set[str] | None,
    sort: str,
    page: int,
    limit: int,
    candidate_limit: int,
) -> SearchPage | None:
    clauses = [clause for group in parsed.groups for clause in group]
    if (
        any(len(group) != 1 for group in parsed.groups)
        or not clauses
        or not all(clause.kind == "text" and clause.negated for clause in clauses)
    ):
        return None

    excluded_ids: set[str] = set()
    truncated = False
    for clause in clauses:
        hits = _search_post_candidates(
            store,
            clause.value,
            limit=candidate_limit,
            collections=collections,
        )
        truncated = truncated or len(hits) >= candidate_limit
        excluded_ids.update(str(hit["tweet_id"]) for hit in hits if hit.get("tweet_id"))

    filter_expr = "record_type = 'tweet'"
    collection_expr = _collection_expr(collections)
    if collection_expr:
        filter_expr += f" AND {collection_expr}"
    if excluded_ids:
        values = ", ".join(_sql_quote(tweet_id) for tweet_id in sorted(excluded_ids))
        filter_expr += f" AND tweet_id NOT IN ({values})"

    effective_sort = _effective_sort(sort, has_positive_text=False)
    order_by = "created_at_ts DESC, CAST(sort_index AS INTEGER) DESC, tweet_id DESC"
    if effective_sort == "oldest":
        order_by = "created_at_ts ASC, CAST(sort_index AS INTEGER) ASC, tweet_id ASC"
    elif effective_sort == "random":
        order_by = "RANDOM()"
    total = store._count_distinct("tweet_id", filter_expr)
    start = (page - 1) * limit
    if effective_sort in LIKE_SORTS:
        id_rows = store.query_like_order_ids(
            filter_expr, sort=effective_sort, limit=limit, offset=start
        )
    else:
        id_rows = store._query(
            expr=filter_expr,
            cols=["DISTINCT tweet_id"],
            limit=limit,
            offset=start,
            order_by=order_by,
        )
    ids = [row["tweet_id"] for row in id_rows if row.get("tweet_id")]
    return SearchPage(
        rows=_hydrate_metadata(store.fetch_tweets_by_ids(ids), []),
        total=total,
        page=page,
        pages=math.ceil(total / limit) if total else 1,
        truncated=truncated,
    )


def _search_grouped(
    store: Any,
    parsed: ParsedSearchQuery,
    *,
    collections: set[str] | None,
    sort: str,
    page: int,
    limit: int,
    candidate_limit: int,
) -> SearchPage:
    rows = _export_candidates(store, collections)
    needs_resurrected = any(
        clause.kind == "filter" and clause.key == "is" and clause.value == "resurrected"
        for group in parsed.groups
        for clause in group
    )
    resurrected_ids: set[str] = set()
    if needs_resurrected:
        state_rows = store._query(
            expr="record_type = 'tweet_object' AND enrichment_state = 'resurrected'",
            cols=["tweet_id"],
        )
        resurrected_ids = {
            row["tweet_id"] for row in state_rows if isinstance(row.get("tweet_id"), str)
        }
    text_matches: dict[SearchClause, dict[str, float]] = {}
    truncated = False
    for group in parsed.groups:
        for clause in group:
            if clause.kind != "text" or clause in text_matches:
                continue
            hits = _search_post_candidates(
                store,
                clause.value,
                limit=candidate_limit,
                collections=collections,
            )
            truncated = truncated or len(hits) >= candidate_limit
            text_matches[clause] = {
                hit["tweet_id"]: float(hit.get("match_score") or 0.0)
                for hit in hits
                if hit.get("tweet_id")
            }

    scores: dict[str, float] = {}
    matched_rows: list[dict[str, Any]] = []
    for row in rows:
        tweet_id = row.get("tweet_id")
        row_score = 0.0
        keep = True
        for group in parsed.groups:
            group_match = False
            group_score = 0.0
            for clause in group:
                if clause.kind == "text":
                    matched = tweet_id in text_matches[clause]
                    if clause.negated:
                        matched = not matched
                    elif matched:
                        group_score = max(group_score, text_matches[clause][str(tweet_id)])
                else:
                    if clause.key == "is" and clause.value == "resurrected":
                        matched = tweet_id in resurrected_ids
                    else:
                        matched = _filter_matches(row, str(clause.key), clause.value)
                    if clause.negated:
                        matched = not matched
                group_match = group_match or matched
            if not group_match:
                keep = False
                break
            row_score += group_score
        if keep:
            matched_rows.append(row)
            if isinstance(tweet_id, str):
                scores[tweet_id] = row_score

    effective_sort = _effective_sort(sort, has_positive_text=parsed.has_positive_text)
    if effective_sort == "relevance":
        matched_rows.sort(
            key=lambda row: (scores.get(str(row.get("tweet_id")), 0.0), row.get("tweet_id") or ""),
            reverse=True,
        )
    else:
        _sort_rows(matched_rows, effective_sort, store=store)
    for row in matched_rows:
        row["type"] = "post"
        if row.get("tweet_id") in scores:
            row["match_score"] = scores[str(row["tweet_id"])]
    return _page(matched_rows, page=page, limit=limit, truncated=truncated)


def search_posts(
    store: Any,
    query: str | None,
    *,
    collections: set[str] | None = None,
    sort: str = "default",
    page: int = 1,
    limit: int = 20,
    candidate_limit: int = 1000,
) -> SearchPage:
    """Search hydrated archive tweets using the canonical Web query language."""
    if page < 1:
        raise ValueError("page must be at least 1")
    if limit < 1:
        raise ValueError("limit must be at least 1")
    if collections:
        collections = {normalize_collection_name(value) for value in collections}
        collections.discard("all")
        collections = collections or None
    if sort in LIKE_SORTS and collections != {"like"}:
        raise SearchQueryError("Like order is available only for the Likes collection.")

    parsed = parse_search_query(query)
    if parsed.has_or:
        text_or_page = _search_positive_text_or(
            store,
            parsed,
            collections=collections,
            sort=sort,
            page=page,
            limit=limit,
            candidate_limit=candidate_limit,
        )
        if text_or_page is not None:
            return text_or_page
    if parsed.has_negative_text:
        negative_page = _search_negative_text_only(
            store,
            parsed,
            collections=collections,
            sort=sort,
            page=page,
            limit=limit,
            candidate_limit=candidate_limit,
        )
        if negative_page is not None:
            return negative_page
    if parsed.has_or or parsed.has_negative_text:
        return _search_grouped(
            store,
            parsed,
            collections=collections,
            sort=sort,
            page=page,
            limit=limit,
            candidate_limit=candidate_limit,
        )

    filters, text_query = parsed.conjunctive_parts()
    pushable_exprs: list[str] = []
    post_filters: dict[str, list[str]] = {}
    for raw_key, values in filters.items():
        negated = raw_key.startswith("-")
        key = raw_key[1:] if negated else raw_key
        normalized_exprs = [
            _normalized_filter_expr(key, value, negated=negated) for value in values
        ]
        if all(expression is not None for expression in normalized_exprs):
            pushable_exprs.extend(str(expression) for expression in normalized_exprs)
            continue
        post_filters[raw_key] = values

    effective_sort = _effective_sort(sort, has_positive_text=bool(text_query))
    start = (page - 1) * limit
    collection_expr = _collection_expr(collections)

    if not post_filters and not text_query:
        filter_expr = "record_type = 'tweet'"
        if collection_expr:
            filter_expr += f" AND {collection_expr}"
        for expression in pushable_exprs:
            filter_expr += f" AND {expression}"
        total = store._count_distinct("tweet_id", filter_expr)
        order_by = "created_at_ts DESC, CAST(sort_index AS INTEGER) DESC, tweet_id DESC"
        if effective_sort == "oldest":
            order_by = "created_at_ts ASC, CAST(sort_index AS INTEGER) ASC, tweet_id ASC"
        elif effective_sort == "random":
            order_by = "RANDOM()"
        if effective_sort in LIKE_SORTS:
            id_rows = store.query_like_order_ids(
                filter_expr, sort=effective_sort, limit=limit, offset=start
            )
        else:
            id_rows = store._query(
                expr=filter_expr,
                cols=["DISTINCT tweet_id"],
                limit=limit,
                offset=start,
                order_by=order_by,
            )
        ids = [row["tweet_id"] for row in id_rows if row.get("tweet_id")]
        hydrated = _hydrate_metadata(store.fetch_tweets_by_ids(ids), [])
        return SearchPage(
            rows=hydrated,
            total=total,
            page=page,
            pages=math.ceil(total / limit) if total else 1,
        )

    if not post_filters and text_query and not pushable_exprs:
        hits = _search_post_candidates(
            store,
            text_query,
            limit=candidate_limit,
            collections=collections,
        )
        truncated = len(hits) >= candidate_limit
        if effective_sort != "relevance":
            _sort_rows(hits, effective_sort, store=store)
        total = len(hits)
        page_hits = hits[start : start + limit]
        ids = [hit["tweet_id"] for hit in page_hits if hit.get("tweet_id")]
        hydrated = _hydrate_metadata(store.fetch_tweets_by_ids(ids), page_hits)
        return SearchPage(
            rows=hydrated,
            total=total,
            page=page,
            pages=math.ceil(total / limit) if total else 1,
            truncated=truncated,
        )

    hits: list[dict[str, Any]] = []
    truncated = False
    if text_query:
        hits = _search_post_candidates(
            store,
            text_query,
            limit=candidate_limit,
            collections=collections,
        )
        truncated = len(hits) >= candidate_limit
        candidate_ids = [hit["tweet_id"] for hit in hits if hit.get("tweet_id")]
        matched_ids = _filter_candidate_ids(store, candidate_ids, pushable_exprs)
        hits = [hit for hit in hits if hit.get("tweet_id") in matched_ids]
        if not post_filters:
            if effective_sort != "relevance":
                _sort_rows(hits, effective_sort, store=store)
            total = len(hits)
            page_hits = hits[start : start + limit]
            ids = [hit["tweet_id"] for hit in page_hits if hit.get("tweet_id")]
            hydrated = _hydrate_metadata(store.fetch_tweets_by_ids(ids), page_hits)
            return SearchPage(
                rows=hydrated,
                total=total,
                page=page,
                pages=math.ceil(total / limit) if total else 1,
                truncated=truncated,
            )
        rows = store.fetch_tweets_by_ids([hit["tweet_id"] for hit in hits])
    else:
        rows = _export_candidates(store, collections)
    rows = _apply_advanced_filters(rows, post_filters if text_query else filters)
    if effective_sort == "relevance" and hits:
        order = {hit["tweet_id"]: index for index, hit in enumerate(hits)}
        rows.sort(key=lambda row: order.get(row.get("tweet_id"), candidate_limit + 1))
    else:
        _sort_rows(rows, effective_sort, store=store)
    hit_by_id = {hit.get("tweet_id"): hit for hit in hits}
    for row in rows:
        row["type"] = "post"
        hit = hit_by_id.get(row.get("tweet_id"), {})
        if hit.get("match_score") is not None:
            row["match_score"] = hit["match_score"]
        if hit.get("collections") is not None:
            row["collections"] = hit["collections"]
    return _page(rows, page=page, limit=limit, truncated=truncated)
