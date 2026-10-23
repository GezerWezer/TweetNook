"""Shared archive tweet search used by the CLI and Web API."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from tweetnook.export.common import normalize_collection_name
from tweetnook.like_order import LIKE_SORTS
from tweetnook.storage.backend import HIDDEN_TWEET_IDS_SQL

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
        "description",
        "hashtag",
    }
)
_FILTER_VALUES = {
    "has": frozenset({"article", "media", "image", "video", "links"}),
    "is": frozenset(
        {"hidden", "missing", "quote", "reply", "resurrected", "retweet", "thread", "verified"}
    ),
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


class SearchCursorExpiredError(SearchQueryError):
    """The derived ordering changed while the caller was paging through it."""


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

    def highlight_terms(self) -> list[str]:
        """Visible positive text/hashtag clauses, preserving phrase and prefix syntax."""
        return list(
            dict.fromkeys(
                clause.value if clause.kind == "text" else f"#{clause.value}"
                for group in self.groups
                for clause in group
                if not clause.negated and (clause.kind == "text" or clause.key == "hashtag")
            )
        )


@dataclass(slots=True)
class SearchPage:
    rows: list[dict[str, Any]]
    total: int | None
    page: int
    pages: int | None
    has_more: bool
    truncated: bool = False
    next_cursor: str | None = None


def _read_page_cursor(token: str, context: str, mode: str) -> dict[str, Any]:
    try:
        if len(token) > 4096:
            raise ValueError
        value = json.loads(
            base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
        )
        if not isinstance(value, dict) or value.get("v") != 1:
            raise ValueError
        if value.get("context") != context or value.get("mode") != mode:
            raise ValueError
        for key in ("page", "offset"):
            if type(value.get(key)) is not int or not 0 <= value[key] < 2**31:
                raise ValueError
        if value["page"] < 1:
            raise ValueError
        after = value.get("after")
        if not isinstance(after, dict):
            raise ValueError
        if mode == "feed":
            if not isinstance(after.get("tweet_id"), str) or not 0 < len(after["tweet_id"]) <= 256:
                raise ValueError
            for key in ("created_at_ts", "sort_index"):
                number = after[key]
                if number is not None and (
                    type(number) is not int or not -(2**63) <= number < 2**63
                ):
                    raise ValueError
        if mode == "like":
            for key in ("like_position", "revision"):
                if type(after.get(key)) is not int or not 0 <= after[key] < 2**63:
                    raise ValueError
        return value
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
        raise SearchQueryError("Invalid pagination cursor for this search.") from exc


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
    if key == "description" and not _description_match(value):
        raise SearchQueryError("Search filter description: requires a word to match.")
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


def _sql_quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _fact(column: str) -> str:
    """Read a derived search value of the membership row being filtered.

    archive_tweet_facts holds values extracted from raw_json by triggers, so
    filters never parse each tweet's JSON during a search.
    """
    return (
        f"(SELECT facts.{column} FROM archive_tweet_facts AS facts "
        "WHERE facts.row_key = archive.row_key)"
    )


def _fact_list_contains(column: str, value: str) -> str:
    # Lists are lowercased values wrapped in char(31); no stored value contains one.
    if "\x1f" in value:
        return "0"
    return f"instr({_fact(column)}, char(31) || {_sql_quote(value)} || char(31)) > 0"


def _description_match(value: str) -> str:
    """FTS5 query matching every word of a description: value, in any order.

    Each word is quoted so FTS operators are matched literally; a trailing * keeps
    prefix matching.
    """
    phrases = []
    for word in value.split():
        prefix = "*" if word.endswith("*") else ""
        word = word.rstrip("*")
        if word:
            phrases.append('"' + word.replace('"', '""') + '"' + prefix)
    return " ".join(phrases)


def _media_tag_match(matching_tweet_ids: str) -> str:
    """Match tweets selected from media_tag rows, and tweets that quote them."""
    return (
        "archive.tweet_id IN ("
        f"WITH matching_tags(tweet_id) AS MATERIALIZED ({matching_tweet_ids}) "
        "SELECT tweet_id FROM matching_tags "
        "UNION SELECT relation.tweet_id FROM matching_tags "
        "CROSS JOIN archive relation INDEXED BY idx_archive_target_tweet_id "
        "WHERE relation.target_tweet_id = matching_tags.tweet_id "
        "AND relation.record_type = 'tweet_relation' "
        "AND relation.relation_type = 'quote_of' "
        ")"
    )


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
    elif (key, value) == ("is", "missing"):
        # Archived tweets that are no longer on X, as the check or a lookup found.
        condition = (
            "related.record_type = 'tweet_object' "
            "AND related.enrichment_state = 'terminal_unavailable'"
        )
    if condition is not None:
        if key in {"has", "filter"} and value in {"media", "image", "images"}:
            condition += " AND COALESCE(related.source, '') != 'link_card'"
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

    expression = None
    if key == "from":
        expression = f"LOWER(COALESCE(author_username, '')) = {_sql_quote(value.replace('@', ''))}"
    elif key == "to":
        expression = f"COALESCE({_fact('reply_to')}, '') = {_sql_quote(value.replace('@', ''))}"
    elif key == "mentions":
        expression = _fact_list_contains("mentions", value.replace("@", ""))
    elif key in {"since", "since_time", "until", "until_time"}:
        boundary = _parse_twitter_date(value) if key in {"since", "until"} else float(value)
        comparison = ">=" if key in {"since", "since_time"} else "<"
        expression = f"created_at_ts {comparison} {int(boundary)}"
    elif key == "since_id":
        expression = f"CAST(archive.tweet_id AS INTEGER) > {int(value)}"
    elif key == "max_id":
        expression = f"CAST(archive.tweet_id AS INTEGER) <= {int(value)}"
    elif (key, value) in {("is", "reply"), ("filter", "replies")}:
        expression = _fact("is_reply")
    elif (key, value) in {("is", "quote"), ("filter", "quote")}:
        expression = _fact("is_quote")
    elif (key, value) in {("is", "retweet"), ("filter", "nativeretweets")}:
        expression = _fact("is_retweet")
    elif (key, value) in {
        ("is", "thread"),
        ("filter", "self_threads"),
        ("filter", "threads"),
    }:
        reply_to = _fact("reply_to")
        expression = (
            f"NULLIF({reply_to}, '') IS NOT NULL AND "
            f"{reply_to} = LOWER(COALESCE(author_username, ''))"
        )
    elif (key, value) in {("is", "verified"), ("filter", "verified")}:
        expression = _fact("is_verified")
    elif (key, value) == ("is", "hidden"):
        expression = f"archive.tweet_id IN ({HIDDEN_TWEET_IDS_SQL})"
    elif key in {"min_retweets", "min_faves", "min_replies"}:
        count_field = {
            "min_retweets": "retweet_count",
            "min_faves": "favorite_count",
            "min_replies": "reply_count",
        }[key]
        expression = f"{_fact(count_field)} >= {int(value)}"
    elif key == "conversation_id":
        expression = f"conversation_id = {_sql_quote(value)}"
    elif key == "quoted_tweet_id":
        expression = f"{_fact('quoted_status_id')} = {_sql_quote(value)}"
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
        expression = f"{_fact('source')} LIKE {_sql_quote('%' + value.replace('_', ' ') + '%')}"
    elif key == "card_name":
        expression = f"{_fact('card_name')} = {_sql_quote(value)}"
    elif key == "tag":
        normalized_value = _sql_quote(value.lower())
        expression = _media_tag_match(
            "SELECT DISTINCT tagged.tweet_id "
            "FROM archive tagged INDEXED BY idx_archive_media_tag_lookup "
            "JOIN json_each(CASE WHEN json_valid(tagged.raw_json) "
            "THEN tagged.raw_json ELSE '{}' END, '$.tags') AS media_tag "
            "WHERE tagged.record_type = 'media_tag' "
            "AND media_tag.type = 'text' "
            f"AND LOWER(CAST(media_tag.value AS TEXT)) = {normalized_value}"
        )
    elif key == "description":
        expression = _media_tag_match(
            "SELECT DISTINCT described.tweet_id FROM archive_description_fts "
            "JOIN archive described ON described.rowid = archive_description_fts.rowid "
            f"WHERE archive_description_fts MATCH {_sql_quote(_description_match(value))}"
        )
    elif key == "hashtag":
        expression = _fact_list_contains("hashtags", value.replace("#", "").lower())

    if expression is None:
        return None
    return f"NOT (COALESCE(({expression}), 0))" if negated else expression


def _effective_sort(sort: str, *, has_positive_text: bool) -> str:
    if sort in LIKE_SORTS:
        return sort
    if sort == "oldest":
        return "oldest"
    if sort in {"random", "most_likes"}:
        return sort
    if sort == "relevance" and has_positive_text:
        return "relevance"
    if sort == "default":
        return "relevance" if has_positive_text else "newest"
    return "newest"


def _hydrate_metadata(
    rows: list[dict[str, Any]], hits: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    hit_by_id = {hit.get("tweet_id"): hit for hit in hits}
    for row in rows:
        hit = hit_by_id.get(row.get("tweet_id"), {})
        row["type"] = "post"
        if hit.get("match_score") is not None:
            row["match_score"] = hit["match_score"]
    return rows


def _compile_tweet_query(parsed: ParsedSearchQuery) -> tuple[tuple, bool]:
    # Hidden tweets stay out of results unless the query mentions is:hidden.
    exclude_hidden = not any(
        clause.key == "is" and clause.value == "hidden"
        for group in parsed.groups
        for clause in group
    )
    query_groups: list[tuple[dict[str, Any], ...]] = []
    for group in parsed.groups:
        compiled_group: list[dict[str, Any]] = []
        for clause in group:
            if clause.kind == "text":
                compiled_group.append(
                    {"kind": "text", "value": clause.value, "negated": clause.negated}
                )
                continue
            expression = _normalized_filter_expr(
                str(clause.key), clause.value, negated=clause.negated
            )
            if expression is None:
                raise SearchQueryError(f"Unsupported search filter: {clause.key}:")
            compiled_group.append(
                {
                    "kind": "filter",
                    "expression": expression,
                    "index_driver": clause.key == "from" and not clause.negated,
                }
            )
        query_groups.append(tuple(compiled_group))

    return tuple(query_groups), exclude_hidden


def count_posts(store, query: str | None, *, collections: set[str] | None = None) -> int:
    """Count the same logical matches as search_posts, without sorting or hydration."""
    if collections:
        collections = {normalize_collection_name(value) for value in collections} - {"all"}
    query_groups, exclude_hidden = _compile_tweet_query(parse_search_query(query))
    _, total = store.search_tweet_ids(
        query_groups,
        collections=collections or None,
        sort="newest",
        limit=1,
        offset=0,
        count_only=True,
        exclude_hidden=exclude_hidden,
    )
    return total


def search_posts(
    store: Any,
    query: str | None,
    *,
    collections: set[str] | None = None,
    sort: str = "default",
    page: int = 1,
    limit: int = 20,
    random_seed: int | None = None,
    cursor: str | None = None,
) -> SearchPage:
    """Search the complete logical archive and hydrate only the requested page."""
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
    query_groups, exclude_hidden = _compile_tweet_query(parsed)

    effective_sort = _effective_sort(sort, has_positive_text=parsed.has_positive_text)
    mode = "offset"
    if effective_sort in LIKE_SORTS:
        mode = "like"
    elif (
        not query_groups
        and (not collections or len(collections) == 1)
        and effective_sort in {"newest", "oldest"}
    ):
        mode = "feed"
    context = hashlib.sha256(
        json.dumps(
            [query_groups, sorted(collections or []), effective_sort, limit, random_seed],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()[:24]
    offset = (page - 1) * limit
    after = None
    if cursor is not None:
        page, offset = 1, 0
        if cursor:
            continuation = _read_page_cursor(cursor, context, mode)
            page = continuation["page"]
            offset = continuation["offset"] if mode == "offset" else 0
            after = continuation["after"] if mode != "offset" else None
    revision = store.cache_revision("like_order") if cursor is not None and mode == "like" else None
    if after and mode == "like" and after["revision"] != revision:
        raise SearchCursorExpiredError("Like order changed. Reload the list to continue.")
    cursor_options = {"after": after} if after is not None else {}
    hits, total = store.search_tweet_ids(
        tuple(query_groups),
        collections=collections,
        sort=effective_sort,
        limit=limit,
        offset=offset,
        random_seed=random_seed,
        include_total=not parsed.has_text,
        exclude_hidden=exclude_hidden,
        **cursor_options,
    )
    if revision is not None and revision != store.cache_revision("like_order"):
        raise SearchCursorExpiredError("Like order changed. Reload the list to continue.")
    has_more = len(hits) > limit
    page_hits = hits[:limit]
    ids = [str(hit["tweet_id"]) for hit in page_hits if hit.get("tweet_id")]
    hydrated_by_id = {
        row.get("tweet_id"): row for row in store.fetch_tweets_by_ids(ids) if row.get("tweet_id")
    }
    hydrated = [hydrated_by_id[tweet_id] for tweet_id in ids if tweet_id in hydrated_by_id]
    hydrated = _hydrate_metadata(hydrated, page_hits)
    next_cursor = None
    if cursor is not None and has_more and page_hits:
        last = page_hits[-1]
        boundary = {}
        if mode == "feed":
            boundary = {key: last[key] for key in ("created_at_ts", "sort_index", "tweet_id")}
        elif mode == "like":
            boundary = {"like_position": last["like_position"], "revision": revision}
        payload = {
            "v": 1,
            "context": context,
            "mode": mode,
            "after": boundary,
            "offset": offset + limit,
            "page": page + 1,
        }
        next_cursor = (
            base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode())
            .decode()
            .rstrip("=")
        )
    return SearchPage(
        rows=hydrated,
        total=total,
        page=page,
        pages=(math.ceil(total / limit) if total else 1) if total is not None else None,
        has_more=has_more,
        next_cursor=next_cursor,
    )
