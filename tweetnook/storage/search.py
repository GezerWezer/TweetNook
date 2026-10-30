"""Full-text and structured archive search."""

from __future__ import annotations

import json
from contextlib import nullcontext
from typing import TYPE_CHECKING, Any

from .common import (
    HIDDEN_TWEET_IDS_SQL,
    SEARCH_COLLECTION_ORDER,
    UNCAPTURED_TWEET_IDS_SQL,
    Clause,
    and_clauses,
    clause_in,
    join_clauses,
)
from .ordering import chronological_order, representative_membership, sort_chronologically
from .record_types import RecordType

if TYPE_CHECKING:
    from tweetnook.storage.search_query import ParsedSearchQuery
from .reads import ReadsMixin
from .tags import TagsMixin

SEARCH_KIND_POST = "post"
SEARCH_KIND_ARTICLE = "article"


class SearchMixin(ReadsMixin, TagsMixin):
    def order_search_results(
        self, rows: list[dict[str, Any]], *, sort: str, collections: set[str] | None = None
    ) -> list[dict[str, Any]]:
        """Merge chronological results using the same logical keys as SQL pages."""
        if sort == "relevance":
            return rows
        ids = {row["tweet_id"] for row in rows}
        if not ids:
            return rows
        representative = representative_membership("saved.tweet_id", collections)
        selected = clause_in("saved.tweet_id", ids)
        keys = self.conn.execute(
            "SELECT saved.tweet_id, saved.created_at_ts, "
            "CAST(saved.sort_index AS INTEGER) AS sort_index "
            f"FROM archive AS saved WHERE {selected.sql} AND ("
            f"saved.row_key = {representative.sql} OR (saved.record_type = 'tweet_object' "
            f"AND {representative.sql} IS NULL))",
            (*selected.params, *representative.params, *representative.params),
        ).fetchall()
        return sort_chronologically(rows, {row["tweet_id"]: dict(row) for row in keys}, sort=sort)

    def _dedupe_search_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen_tweet_ids: set[str] = set()
        for row in rows:
            tweet_id = row.get("tweet_id")
            if not isinstance(tweet_id, str) or not tweet_id or tweet_id in seen_tweet_ids:
                continue
            seen_tweet_ids.add(tweet_id)
            deduped.append(row)
        return deduped

    def _collect_search_context(
        self, tweet_ids: list[str]
    ) -> tuple[dict[str, list[str]], dict[str, dict[str, Any]]]:
        tweet_rows = self._rows_for_values(RecordType.TWEET, "tweet_id", tweet_ids)
        tweet_object_rows = self._rows_for_values(RecordType.TWEET_OBJECT, "tweet_id", tweet_ids)
        collections_by_tweet_id: dict[str, set[str]] = {}
        metadata_by_tweet_id: dict[str, dict[str, Any]] = {}

        def merge_metadata(row: dict[str, Any]) -> None:
            tweet_id = row.get("tweet_id")
            if not isinstance(tweet_id, str) or not tweet_id:
                return
            metadata = metadata_by_tweet_id.setdefault(tweet_id, {})
            for metadata_field in (
                "author_id",
                "author_username",
                "author_display_name",
                "created_at",
                "text",
                "note_tweet_text",
            ):
                if metadata.get(metadata_field) not in (None, ""):
                    continue
                value = row.get(metadata_field)
                if value not in (None, ""):
                    metadata[metadata_field] = value

        for row in tweet_object_rows:
            merge_metadata(row)
        for row in tweet_rows:
            tweet_id = row.get("tweet_id")
            collection_type = row.get("collection_type")
            if isinstance(tweet_id, str) and tweet_id and isinstance(collection_type, str):
                collections_by_tweet_id.setdefault(tweet_id, set()).add(collection_type)
            merge_metadata(row)

        ordered_collections = {
            tweet_id: self._ordered_search_collections(values)
            for tweet_id, values in collections_by_tweet_id.items()
        }
        return ordered_collections, metadata_by_tweet_id

    def _ordered_search_collections(self, collections: set[str]) -> list[str]:
        order = {k: i for i, k in enumerate(SEARCH_COLLECTION_ORDER)}
        return sorted(list(collections), key=lambda c: (order.get(c, 99), c))

    def _search_collection_expr(self, collections: set[str] | None) -> Clause:
        if not collections:
            return Clause("")
        if len(collections) == 1:
            return Clause("collection_type = ?", (next(iter(collections)),))
        return clause_in("collection_type", collections)

    def _prepare_fts_query(self, query: str) -> str:
        import re

        raw_parts = re.findall(r'"[^"]*"|\S+', query.strip())
        if not raw_parts:
            return ""

        operators = {"AND", "OR", "NOT"}
        prepared: list[str] = []
        for index, part in enumerate(raw_parts):
            upper = part.upper()
            previous_is_term = index > 0 and raw_parts[index - 1].upper() not in operators
            next_is_term = (
                index + 1 < len(raw_parts) and raw_parts[index + 1].upper() not in operators
            )
            if upper in operators and previous_is_term and next_is_term:
                prepared.append(upper)
                continue

            if len(part) >= 2 and part.startswith('"') and part.endswith('"'):
                inner = part[1:-1].replace('"', '""')
                if inner:
                    prepared.append(f'"{inner}"')
                continue
            if re.fullmatch(r"[\w]+(?:\*)?", part) and upper not in operators | {"NEAR"}:
                prepared.append(part)
                continue
            safe_part = part.replace('"', '""')
            if safe_part:
                prepared.append(f'"{safe_part}"')
        return " ".join(prepared)

    def _search_score(self, row: dict[str, Any]) -> float:
        rank = row.get("rank")
        if rank is not None:
            return float(-rank)
        score = row.get("match_score")
        if score is not None:
            return float(score)
        return float("-inf")

    def _search_article_rows(
        self, parsed: ParsedSearchQuery, *, collections: set[str] | None = None
    ) -> list[dict[str, Any]]:
        import re

        def term_parts(term: str) -> list[str]:
            if term.startswith('"') and term.endswith('"'):
                term = term[1:-1]
            return re.findall(r"\w+", term.casefold())

        def matches_term(term: str, words: list[str]) -> bool:
            pieces = term_parts(term)
            if not pieces:
                return False
            if term.endswith("*") and len(pieces) == 1:
                return any(word.startswith(pieces[0]) for word in words)
            if len(pieces) > 1 or (term.startswith('"') and term.endswith('"')):
                width = len(pieces)
                return any(words[index : index + width] == pieces for index in range(len(words)))
            return pieces[0] in words

        expr = f"record_type = '{RecordType.ARTICLE}' AND tweet_id NOT IN ({HIDDEN_TWEET_IDS_SQL})"
        params: tuple[Any, ...] = ()
        if collections:
            membership = self._search_collection_expr(collections)
            expr += (
                " AND tweet_id IN (SELECT tweet_id FROM archive "
                f"WHERE record_type = '{RecordType.TWEET}' AND ({membership.sql}))"
            )
            params = membership.params
        rows = self._query(expr, params)
        matches: list[dict[str, Any]] = []
        for row in rows:
            haystack = " ".join(
                part
                for part in (
                    row.get("title"),
                    row.get("summary_text"),
                    row.get("content_text"),
                )
                if isinstance(part, str) and part
            )
            folded = haystack.casefold()
            words = re.findall(r"\w+", folded)
            normalized = " ".join(words)
            if not all(
                any(matches_term(clause.value, words) != clause.negated for clause in group)
                for group in parsed.groups
            ):
                continue
            matched = dict(row)
            matched["match_score"] = float(
                sum(
                    normalized.count(" ".join(term_parts(clause.value)))
                    for group in parsed.groups
                    for clause in group
                    if not clause.negated and matches_term(clause.value, words)
                )
            )
            matches.append(matched)
        matches.sort(
            key=lambda row: (
                row.get("match_score") if row.get("match_score") is not None else float("-inf"),
                len(row.get("content_text") or row.get("summary_text") or row.get("title") or ""),
            ),
            reverse=True,
        )
        return matches

    def _project_post_search_results(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        tweet_ids = [
            tweet_id
            for row in rows
            if isinstance((tweet_id := row.get("tweet_id")), str) and tweet_id
        ]
        collections_by_tweet_id, metadata_by_tweet_id = self._collect_search_context(tweet_ids)
        results: list[dict[str, Any]] = []
        for row in rows:
            tweet_id = row.get("tweet_id")
            if not isinstance(tweet_id, str) or not tweet_id:
                continue
            metadata = metadata_by_tweet_id.get(tweet_id, {})
            collections = collections_by_tweet_id.get(tweet_id)
            if not collections:
                fallback = row.get("collection_type")
                collections = [fallback] if isinstance(fallback, str) and fallback else []
            results.append(
                {
                    "tweet_id": tweet_id,
                    "type": SEARCH_KIND_POST,
                    "collections": collections,
                    "author_id": row.get("author_id") or metadata.get("author_id"),
                    "author_username": row.get("author_username")
                    or metadata.get("author_username"),
                    "created_at": row.get("created_at") or metadata.get("created_at"),
                    "text": self._coalesce_value(
                        row.get("note_tweet_text"),
                        row.get("text"),
                        metadata.get("note_tweet_text"),
                        metadata.get("text"),
                    ),
                    "match_score": self._search_score(row),
                }
            )
        return results

    def _compose_article_search_text(
        self,
        row: dict[str, Any],
        metadata: dict[str, Any],
    ) -> str | None:
        summary_or_body = self._coalesce_value(row.get("summary_text"), row.get("content_text"))
        title = row.get("title")
        if title and summary_or_body:
            return f"{title}\\n\\n{summary_or_body}"
        return self._coalesce_value(
            title,
            summary_or_body,
            metadata.get("note_tweet_text"),
            metadata.get("text"),
        )

    def _project_article_search_results(
        self,
        rows: list[dict[str, Any]],
        *,
        collections: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        tweet_ids = [
            tweet_id
            for row in rows
            if isinstance((tweet_id := row.get("tweet_id")), str) and tweet_id
        ]
        collections_by_tweet_id, metadata_by_tweet_id = self._collect_search_context(tweet_ids)
        results: list[dict[str, Any]] = []
        for row in rows:
            tweet_id = row.get("tweet_id")
            if not isinstance(tweet_id, str) or not tweet_id:
                continue
            result_collections = collections_by_tweet_id.get(tweet_id, [])
            if collections is not None and not set(result_collections).intersection(collections):
                continue
            metadata = metadata_by_tweet_id.get(tweet_id, {})
            results.append(
                {
                    "tweet_id": tweet_id,
                    "type": SEARCH_KIND_ARTICLE,
                    "collections": result_collections,
                    "author_id": metadata.get("author_id"),
                    "author_username": metadata.get("author_username"),
                    "created_at": metadata.get("created_at"),
                    "text": self._compose_article_search_text(row, metadata),
                    "match_score": self._search_score(row),
                }
            )
        return results

    def search_fts(
        self,
        query: str,
        *,
        limit: int = 20,
        types: set[str] | None = None,
        collections: set[str] | None = None,
        sort: str = "relevance",
    ) -> list[dict[str, Any]]:
        """Full-text search over exposed types, filtering and sorting before the limit."""
        from tweetnook.storage.search_query import (
            SearchQueryError,
            parse_search_query,
            search_posts,
        )

        if sort not in {"relevance", "newest", "oldest"}:
            raise ValueError(f"Unsupported sort order: {sort}")
        parsed = parse_search_query(query)
        if not parsed.groups:
            return []
        if types is None or SEARCH_KIND_ARTICLE in types:
            if any(clause.kind == "filter" for group in parsed.groups for clause in group):
                raise SearchQueryError(
                    "Structured filters apply to tweets; use filter:articles to find tweets "
                    "with attached articles."
                )

        results: list[dict[str, Any]] = []
        if types is None or SEARCH_KIND_POST in types:
            posts = search_posts(
                self, query, limit=max(limit, 1), collections=collections, sort=sort
            ).rows
            results.extend(self._project_post_search_results(posts))
        if types is None or SEARCH_KIND_ARTICLE in types:
            articles = self._dedupe_search_rows(
                self._search_article_rows(parsed, collections=collections)
            )
            results.extend(self._project_article_search_results(articles, collections=collections))
        results.sort(key=self._search_score, reverse=True)
        return self.order_search_results(results, sort=sort, collections=collections)[:limit]

    def search_tweet_ids(
        self,
        query_groups: tuple[tuple[dict[str, Any], ...], ...],
        *,
        collections: set[str] | None,
        sort: str,
        limit: int,
        offset: int,
        random_seed: int | None = None,
        include_total: bool = False,
        count_only: bool = False,
        after: dict[str, Any] | None = None,
        exclude_hidden: bool = False,
        exclude_uncaptured: bool = False,
    ) -> tuple[list[dict[str, Any]], int | None]:
        """Evaluate the Web tweet query in SQLite and return one lightweight page.

        Text clauses are independent FTS relations. The supplied filter expressions
        are produced by the canonical query compiler, never by HTTP input directly.
        exclude_hidden leaves out tweets hidden with set_tweet_hidden().
        exclude_uncaptured leaves out tweets with no saved content in any copy.
        """
        if count_only:
            include_total = True
            sort = "newest"  # Counts need neither BM25 nor like-order rebuilding.
        excluded_ids = []
        if exclude_hidden:
            excluded_ids.append(HIDDEN_TWEET_IDS_SQL)
        if exclude_uncaptured:
            excluded_ids.append(UNCAPTURED_TWEET_IDS_SQL)
        excluded_sql = " UNION ".join(excluded_ids)
        excluded_expr = f"archive.tweet_id NOT IN ({excluded_sql})" if excluded_sql else ""

        if limit < 1:
            raise ValueError("limit must be at least 1")
        if offset < 0:
            raise ValueError("offset must be non-negative")

        # Preserve the index-ordered ordinary-feed path. It already deduplicates
        # logical tweets before LIMIT/OFFSET and avoids building a search CTE when
        # there is no query to evaluate.
        if (
            not query_groups
            and sort not in {"random", "most_likes"}
            and (not collections or len(collections) == 1)
        ):
            collection = next(iter(collections)) if collections else "all"
            filter_expr = f"record_type = '{RecordType.TWEET}'"
            filter_params: tuple[str, ...] = ()
            if collection != "all":
                filter_expr += " AND collection_type = ?"
                filter_params = (collection,)
            total = self.count_export_rows(collection) if include_total else None
            if total is not None and excluded_sql:
                total -= self._excluded_member_count(collection, excluded_sql)
            if count_only:
                return [], total
            if sort in {"liked_latest", "liked_earliest"}:
                rows = self.query_like_order_ids(
                    f"{filter_expr} AND {excluded_expr}" if excluded_expr else filter_expr,
                    filter_params,
                    sort=sort,
                    limit=limit + 1,
                    offset=offset,
                    after_position=after["like_position"] if after else None,
                )
                return rows, total
            rows = self.get_paginated_tweet_rows(
                collection,
                limit=limit + 1,
                offset=offset,
                sort=sort,
                after=after,
                exclude_hidden=exclude_hidden,
                exclude_uncaptured=exclude_uncaptured,
            )
            return rows, total

        collection_expr = self._search_collection_expr(collections)
        ctes: list[str] = []
        params: list[Any] = []
        author_groups = [
            group
            for group in query_groups
            if group and all(clause.get("index_driver") for clause in group)
        ]
        narrow_ids = None
        if author_groups and any(
            clause["kind"] == "text" for group in query_groups for clause in group
        ):
            author_where = [Clause(f"archive.record_type = '{RecordType.TWEET}'"), collection_expr]
            author_where.extend(
                Clause(
                    "(" + " OR ".join(f"({clause['expression']})" for clause in group) + ")",
                    tuple(param for clause in group for param in clause.get("params", ())),
                )
                for group in author_groups
            )
            author_filter = and_clauses(*author_where)
            # A bounded selectivity probe chooses a per-row FTS lookup only for
            # small author sets. Larger sets use the complete FTS-led executor;
            # this threshold never caps the returned search population.
            probe = self.conn.execute(
                "SELECT DISTINCT archive.tweet_id FROM archive "
                f"WHERE {author_filter.sql} LIMIT 201",
                author_filter.params,
            ).fetchall()
            if len(probe) <= 200:
                narrow_ids = [row[0] for row in probe]
                if not narrow_ids:
                    return [], 0 if include_total else None
                ctes.append(
                    "structured_candidates(tweet_id) AS MATERIALIZED "
                    "(SELECT value FROM json_each(?))"
                )
                params.append(json.dumps(narrow_ids))
        compiled_groups: list[list[dict[str, Any]]] = []
        text_index = 0
        for group in query_groups:
            compiled_group: list[dict[str, Any]] = []
            for clause in group:
                compiled = dict(clause)
                if compiled["kind"] == "text":
                    prepared = self._prepare_fts_query(str(compiled["value"]))
                    alias = f"text_match_{text_index}"
                    text_index += 1
                    compiled["alias"] = alias
                    where = f"archive.record_type = '{RecordType.TWEET}'"
                    if collection_expr.sql:
                        where += f" AND {collection_expr.sql}"
                    rank = "MIN(archive_fts.rank)" if sort == "relevance" else "0.0"
                    text_from = "archive_fts JOIN archive ON archive.rowid = archive_fts.rowid"
                    if narrow_ids is not None:
                        # Include every membership of the selected logical IDs:
                        # text and author predicates may match different rows.
                        text_from = (
                            "structured_candidates CROSS JOIN archive "
                            "INDEXED BY idx_archive_tweet_id "
                            "ON archive.tweet_id = structured_candidates.tweet_id "
                            "CROSS JOIN archive_fts ON archive_fts.rowid = archive.rowid"
                        )
                        if sort == "relevance":
                            # BM25 initialization per row is costly for common
                            # terms. Scan FTS once, intersect before aggregation,
                            # and rank only matching authors' membership rows.
                            text_from = (
                                "archive_fts CROSS JOIN archive "
                                "ON archive.rowid = archive_fts.rowid"
                            )
                            where += (
                                " AND archive.tweet_id IN "
                                "(SELECT tweet_id FROM structured_candidates)"
                            )
                    ctes.append(
                        f"{alias}(tweet_id, rank) AS MATERIALIZED ("
                        f"SELECT archive.tweet_id, {rank} "
                        f"FROM {text_from} "
                        f"WHERE archive_fts MATCH ? AND {where} "
                        "GROUP BY archive.tweet_id)"
                    )
                    params.append(prepared)
                    params.extend(collection_expr.params)
                compiled_group.append(compiled)
            compiled_groups.append(compiled_group)

        # Any all-positive-text AND group is a complete candidate driver. Using one
        # keeps ordinary text searches FTS-led while mixed OR and negative-only
        # queries correctly evaluate against the complete logical tweet population.
        driver_group = next(
            (
                group
                for group in compiled_groups
                if group
                and all(
                    clause["kind"] == "text" and not clause.get("negated", False)
                    for clause in group
                )
            ),
            None,
        )
        driver_alias = None
        if driver_group is not None and len(driver_group) == 1:
            # Reuse the already-deduplicated text relation directly, avoiding a
            # second copy of the same candidate IDs and a redundant self-join.
            driver_alias = driver_group[0]["alias"]
            from_sql = (
                f"{driver_alias} JOIN archive AS archive INDEXED BY idx_archive_tweet_id "
                f"ON archive.tweet_id = {driver_alias}.tweet_id"
            )
        elif driver_group is not None:
            union = " UNION ".join(
                f"SELECT tweet_id FROM {clause['alias']}" for clause in driver_group
            )
            ctes.append(f"candidate_ids(tweet_id) AS MATERIALIZED ({union})")
            from_sql = (
                "candidate_ids JOIN archive AS archive INDEXED BY idx_archive_tweet_id "
                "ON archive.tweet_id = candidate_ids.tweet_id"
            )
        else:
            from_sql = "archive AS archive"

        joins: list[str] = []
        group_conditions: list[Clause] = []
        group_scores: list[str] = []
        for group in compiled_groups:
            alternatives: list[Clause] = []
            positive_scores: list[str] = []
            for clause in group:
                if clause["kind"] == "text":
                    alias = str(clause["alias"])
                    if alias != driver_alias:
                        joins.append(f"LEFT JOIN {alias} ON {alias}.tweet_id = archive.tweet_id")
                    if clause.get("negated", False):
                        alternatives.append(Clause(f"{alias}.tweet_id IS NULL"))
                    else:
                        alternatives.append(Clause(f"{alias}.tweet_id IS NOT NULL"))
                        positive_scores.append(f"COALESCE(-{alias}.rank, 0.0)")
                else:
                    alternatives.append(
                        Clause(f"({clause['expression']})", tuple(clause.get("params", ())))
                    )
            if alternatives:
                either = join_clauses(" OR ", alternatives)
                group_conditions.append(Clause(f"({either.sql})", either.params))
            if positive_scores:
                group_scores.append(
                    positive_scores[0]
                    if len(positive_scores) == 1
                    else f"MAX({', '.join(positive_scores)})"
                )

        match_filter = and_clauses(
            f"archive.record_type = '{RecordType.TWEET}'",
            collection_expr,
            excluded_expr,
            *group_conditions,
        )
        score_expr = " + ".join(group_scores) if group_scores else "0.0"
        likes_column = ", favorite_count" if sort == "most_likes" else ""
        likes_value = (
            ", MAX(COALESCE((SELECT favorite_count FROM archive_tweet_facts "
            "WHERE row_key = archive.row_key), 0))"
            if sort == "most_likes"
            else ""
        )
        ctes.append(
            f"matches(tweet_id, match_score, created_at_ts, sort_index{likes_column}) "
            "AS NOT MATERIALIZED ("
            "SELECT archive.tweet_id, "
            f"MAX({score_expr}), MAX(archive.created_at_ts), "
            f"MAX(CAST(archive.sort_index AS INTEGER)){likes_value} "
            f"FROM {from_sql} {' '.join(dict.fromkeys(joins))} "
            f"WHERE {match_filter.sql} GROUP BY archive.tweet_id)"
        )
        params.extend(match_filter.params)
        with_sql = "WITH " + ", ".join(ctes)

        page_from = "matches"
        page_params = list(params)
        page_where = ""
        page_extra = ""
        ordering_columns = "matches.created_at_ts, matches.sort_index"
        if sort in {"liked_latest", "liked_earliest"}:
            position = "earliest_position" if sort == "liked_earliest" else "latest_position"
            page_from = (
                "matches JOIN temp.tweetnook_like_order AS like_order "
                "ON like_order.tweet_id = matches.tweet_id"
            )
            order_by = f"like_order.{position}, matches.tweet_id"
            page_extra = f", like_order.{position} AS like_position"
            if after:
                page_where = f"WHERE like_order.{position} > ?"
                page_params.append(after["like_position"])
        elif sort in {"oldest", "newest"}:
            representative = representative_membership("matches.tweet_id", collections)
            page_from = (
                f"matches JOIN archive AS ordering ON ordering.row_key = {representative.sql}"
            )
            page_params.extend(representative.params)
            ordering_columns = (
                "ordering.created_at_ts, CAST(ordering.sort_index AS INTEGER) AS sort_index"
            )
            order_by = chronological_order("ordering", sort)
        elif sort == "most_likes":
            order_by = (
                "matches.favorite_count DESC, matches.created_at_ts DESC, "
                "matches.sort_index DESC, matches.tweet_id DESC"
            )
        elif sort == "random":
            seed = int(random_seed or 0)

            order_by = "tweetnook_random_key(matches.tweet_id, ?), matches.tweet_id"
            page_params.append(seed)
        else:
            order_by = "matches.match_score DESC, matches.tweet_id DESC"

        page_sql = (
            f"{with_sql} SELECT matches.tweet_id, matches.match_score, "
            f"{ordering_columns}{page_extra} "
            f"FROM {page_from} {page_where} ORDER BY {order_by} LIMIT ? OFFSET ?"
        )
        page_params.extend([limit + 1, offset])
        like_sort = sort in {"liked_latest", "liked_earliest"}
        rows = []
        if not count_only:
            with self._like_order_lock if like_sort else nullcontext():
                if like_sort:
                    self._ensure_like_order_table()
                rows = [dict(row) for row in self.conn.execute(page_sql, page_params).fetchall()]

        total = None
        if include_total:
            signature = (
                self.conn.total_changes,
                self.conn.execute("PRAGMA data_version").fetchone()[0],
            )
            cache_key = (with_sql, tuple(params))
            cached = self._search_count_cache.get(cache_key)
            if not self.conn.in_transaction and cached is not None and cached[0] == signature:
                total = cached[1]
            else:
                total = int(
                    self.conn.execute(
                        f"{with_sql} SELECT COUNT(*) FROM matches", params
                    ).fetchone()[0]
                )
                if len(self._search_count_cache) >= 64 or self.conn.in_transaction:
                    self._search_count_cache.clear()
                if not self.conn.in_transaction:
                    self._search_count_cache[cache_key] = (signature, total)
        return rows, total
