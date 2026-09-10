"""Tweet query, thread, and quote endpoints."""

import json

from fastapi import APIRouter, Depends, HTTPException, Query

from tweetnook import search as archive_search
from tweetnook.export.common import normalize_collection_name
from tweetnook.search import SearchCursorExpiredError, SearchQueryError, search_posts
from tweetnook.web.availability import annotate_web_tweets, missing_web_tweet
from tweetnook.web.deps import require_store, verify_credentials

router = APIRouter()

# Keep these private route names import-compatible while the implementation lives in the
# presentation-neutral shared search module.
_apply_advanced_filters = archive_search._apply_advanced_filters
_extract_advanced_filters = archive_search._extract_advanced_filters
_parse_twitter_date = archive_search._parse_twitter_date

_THREAD_DESCENDANT_LIMIT = 250


def _sql_quote(value: object) -> str:
    """Quote a scalar for the store's expression-only query interface."""
    return "'" + str(value).replace("'", "''") + "'"


def _indexed_relation_rows(
    store,
    tweet_id: str,
    *,
    source_types: tuple[str, ...] = (),
    target_types: tuple[str, ...] = (),
    limit: int,
):
    """Load both sides of a relation without leaving an index choice to SQLite."""
    rows = []
    seen = set()
    quoted_tweet_id = _sql_quote(tweet_id)
    lookups = (
        ("tweet_id", source_types, "idx_archive_tweet_id"),
        ("target_tweet_id", target_types, "idx_archive_target_tweet_id"),
    )
    for field, relation_types, index_name in lookups:
        if not relation_types or len(rows) >= limit:
            continue
        quoted_types = ", ".join(_sql_quote(value) for value in relation_types)
        candidates = store._query(
            expr=(
                f"record_type = 'tweet_relation' AND {field} = {quoted_tweet_id} "
                f"AND relation_type IN ({quoted_types})"
            ),
            cols=["tweet_id", "target_tweet_id", "relation_type"],
            limit=limit - len(rows),
            indexed_by=index_name,
        )
        for row in candidates:
            key = (row.get("tweet_id"), row.get("target_tweet_id"), row.get("relation_type"))
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
            if len(rows) >= limit:
                break
    return rows


def _recursive_reply_rows(
    store,
    tweet_id: str,
    *,
    limit: int = _THREAD_DESCENDANT_LIMIT,
) -> tuple[list[dict], bool]:
    """Load a bounded reply subtree in one indexed, cycle-safe query."""
    query_limit = limit + 1
    rows = store.conn.execute(
        """
        WITH RECURSIVE reply_tree(tweet_id, parent_tweet_id, depth, path) AS (
            SELECT relation.tweet_id,
                   relation.target_tweet_id,
                   1,
                   ',' || ? || ',' || relation.tweet_id || ','
            FROM archive AS relation INDEXED BY idx_archive_target_tweet_id
            WHERE relation.record_type = 'tweet_relation'
              AND relation.relation_type IN ('reply_to', 'thread_parent')
              AND relation.target_tweet_id = ?
            UNION
            SELECT relation.target_tweet_id,
                   relation.tweet_id,
                   1,
                   ',' || ? || ',' || relation.target_tweet_id || ','
            FROM archive AS relation INDEXED BY idx_archive_tweet_id
            WHERE relation.record_type = 'tweet_relation'
              AND relation.relation_type = 'thread_child'
              AND relation.tweet_id = ?
            UNION
            SELECT relation.tweet_id,
                   relation.target_tweet_id,
                   tree.depth + 1,
                   tree.path || relation.tweet_id || ','
            FROM reply_tree AS tree
            JOIN archive AS relation INDEXED BY idx_archive_target_tweet_id
              ON relation.target_tweet_id = tree.tweet_id
            WHERE relation.record_type = 'tweet_relation'
              AND relation.relation_type IN ('reply_to', 'thread_parent')
              AND tree.depth < ?
              AND instr(tree.path, ',' || relation.tweet_id || ',') = 0
            UNION
            SELECT relation.target_tweet_id,
                   relation.tweet_id,
                   tree.depth + 1,
                   tree.path || relation.target_tweet_id || ','
            FROM reply_tree AS tree
            JOIN archive AS relation INDEXED BY idx_archive_tweet_id
              ON relation.tweet_id = tree.tweet_id
            WHERE relation.record_type = 'tweet_relation'
              AND relation.relation_type = 'thread_child'
              AND tree.depth < ?
              AND instr(tree.path, ',' || relation.target_tweet_id || ',') = 0
            ORDER BY 3 ASC
            LIMIT ?
        )
        SELECT tweet_id, parent_tweet_id, MIN(depth) AS depth
        FROM reply_tree
        GROUP BY tweet_id, parent_tweet_id
        ORDER BY depth, tweet_id
        """,
        (
            tweet_id,
            tweet_id,
            tweet_id,
            tweet_id,
            query_limit,
            query_limit,
            query_limit,
        ),
    ).fetchall()
    truncated = len(rows) > limit
    normalized = [
        {
            "tweet_id": str(row[0]),
            "target_tweet_id": str(row[1]),
            "relation_type": "reply_to",
            "depth": int(row[2]),
        }
        for row in rows[:limit]
        if row[0] and row[1]
    ]
    return normalized, truncated


@router.get("/api/tweets")
def api_tweets(
    q: str | None = None,
    collection: str = Query("all"),
    sort: str = Query("default"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    random_seed: int | None = Query(None, ge=0, le=2_147_483_647),
    cursor: str | None = None,
    store=Depends(require_store),  # noqa: B008
    _auth: bool = Depends(verify_credentials),
):
    """Fetch tweets with pagination, collection filtering, and advanced search."""
    try:
        try:
            internal_col = normalize_collection_name(collection)
        except ValueError:
            internal_col = "all"

        collections = {internal_col} if internal_col != "all" else None
        result = search_posts(
            store,
            q,
            collections=collections,
            sort=sort,
            page=page,
            limit=limit,
            random_seed=random_seed,
            cursor=cursor,
        )
        paginated_tweets = result.rows
        annotate_web_tweets(store, paginated_tweets)

        return {
            "tweets": paginated_tweets,
            "total": result.total,
            "page": result.page,
            "pages": result.pages,
            "has_more": result.has_more,
            "next_cursor": result.next_cursor,
            "truncated": result.truncated,
        }
    except SearchCursorExpiredError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SearchQueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as e:
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/tweets/{tweet_id}")
def api_tweet_thread(
    tweet_id: str,
    store=Depends(require_store),  # noqa: B008
    _auth: bool = Depends(verify_credentials),
):
    try:
        all_relations = []
        related_ids = {tweet_id}

        curr_id = tweet_id
        seen_ancestor_ids = {tweet_id}
        for _ in range(50):
            p_rels = _indexed_relation_rows(
                store,
                curr_id,
                source_types=("reply_to", "thread_parent"),
                target_types=("thread_child",),
                limit=10,
            )
            if not p_rels:
                break

            next_parent = None
            for r in p_rels:
                all_relations.append(r)
                if r.get("tweet_id"):
                    related_ids.add(r["tweet_id"])
                if r.get("target_tweet_id"):
                    related_ids.add(r["target_tweet_id"])
                if (
                    r.get("relation_type") in ("reply_to", "thread_parent")
                    and r.get("tweet_id") == curr_id
                ):
                    next_parent = r.get("target_tweet_id")
                elif (
                    r.get("relation_type") == "thread_child" and r.get("target_tweet_id") == curr_id
                ):
                    next_parent = r.get("tweet_id")

            if not next_parent or next_parent in seen_ancestor_ids:
                break
            seen_ancestor_ids.add(next_parent)
            curr_id = next_parent

        descendant_relations, reply_tree_truncated = _recursive_reply_rows(store, tweet_id)
        all_relations.extend(descendant_relations)
        for relation in descendant_relations:
            related_ids.add(relation["tweet_id"])
            related_ids.add(relation["target_tweet_id"])

        id_list = ", ".join(_sql_quote(tid) for tid in related_ids)
        related_count = len(related_ids)
        objs = store._query(
            expr=f"record_type = 'tweet_object' AND tweet_id IN ({id_list})",
            cols=[
                "tweet_id",
                "text",
                "author_id",
                "author_username",
                "author_display_name",
                "created_at",
                "synced_at",
                "raw_json",
            ],
            limit=related_count,
            indexed_by="idx_archive_tweet_id",
        )
        media = store._query(
            expr=f"record_type = 'media' AND tweet_id IN ({id_list})",
            cols=[
                "tweet_id",
                "media_type",
                "media_url",
                "thumbnail_url",
                "width",
                "height",
                "duration_millis",
                "local_path",
                "thumbnail_local_path",
            ],
            limit=related_count * 10,
            indexed_by="idx_archive_tweet_id",
        )
        col_rows = store._query(
            expr=f"record_type = 'tweet' AND tweet_id IN ({id_list})",
            cols=[
                "tweet_id",
                "collection_type",
                "text",
                "author_id",
                "author_username",
                "author_display_name",
                "created_at",
                "synced_at",
                "raw_json",
            ],
            limit=related_count * 3,
            indexed_by="idx_archive_tweet_id",
        )
        tag_rows = store._query(
            expr=f"record_type = 'media_tag' AND tweet_id IN ({id_list})",
            cols=["tweet_id", "raw_json"],
            limit=related_count,
            indexed_by="idx_archive_tweet_id",
        )

        col_dict = {}
        membership_by_id = {}
        for c in col_rows:
            col_dict.setdefault(c["tweet_id"], []).append(c["collection_type"])
            membership_by_id.setdefault(c["tweet_id"], c)

        tags_dict = {}
        for row in tag_rows:
            if not row.get("tweet_id") or not row.get("raw_json"):
                continue
            try:
                tags_dict[row["tweet_id"]] = json.loads(row["raw_json"])
            except (TypeError, json.JSONDecodeError):
                continue

        raw_by_tweet_id = {}
        for obj in objs:
            tid = obj.get("tweet_id")
            raw_json = None
            if obj.get("raw_json"):
                try:
                    parsed_raw = json.loads(obj["raw_json"])
                    if isinstance(parsed_raw, dict):
                        raw_json = parsed_raw
                except (TypeError, json.JSONDecodeError):
                    pass
            if tid:
                raw_by_tweet_id[tid] = raw_json

        media_by_tweet_id = {}
        for row in media:
            if row.get("tweet_id"):
                media_by_tweet_id.setdefault(row["tweet_id"], []).append(row)

        formatted = {}
        for obj in objs:
            tid = obj["tweet_id"]
            t_media = media_by_tweet_id.get(tid, [])
            raw_json = raw_by_tweet_id.get(tid)

            formatted[tid] = {
                "tweet_id": tid,
                "text": obj.get("text", ""),
                "collections": col_dict.get(tid, []),
                "author": {
                    "id": obj.get("author_id"),
                    "username": obj.get("author_username"),
                    "display_name": obj.get("author_display_name"),
                },
                "created_at": obj.get("created_at"),
                "synced_at": obj.get("synced_at"),
                "media": [
                    {
                        "type": m.get("media_type"),
                        "url": m.get("media_url"),
                        "thumbnail_url": m.get("thumbnail_url"),
                        "width": m.get("width"),
                        "height": m.get("height"),
                        "duration_millis": m.get("duration_millis"),
                        "download": {
                            "local_path": m.get("local_path"),
                            "thumbnail_local_path": m.get("thumbnail_local_path"),
                        },
                    }
                    for m in t_media
                ],
                "raw_json": raw_json,
                "qt_media": [],
                "qt_media_tags": None,
                "media_tags": tags_dict.get(tid),
            }

        for tid, membership in membership_by_id.items():
            if tid in formatted:
                continue
            raw_json = None
            if membership.get("raw_json"):
                try:
                    parsed_raw = json.loads(membership["raw_json"])
                    if isinstance(parsed_raw, dict):
                        raw_json = parsed_raw
                except (TypeError, json.JSONDecodeError):
                    pass
            formatted[tid] = {
                "tweet_id": tid,
                "text": membership.get("text") or "",
                "collections": col_dict.get(tid, []),
                "author": {
                    "id": membership.get("author_id"),
                    "username": membership.get("author_username"),
                    "display_name": membership.get("author_display_name"),
                },
                "created_at": membership.get("created_at"),
                "synced_at": membership.get("synced_at"),
                "media": [
                    {
                        "type": item.get("media_type"),
                        "url": item.get("media_url"),
                        "thumbnail_url": item.get("thumbnail_url"),
                        "width": item.get("width"),
                        "height": item.get("height"),
                        "duration_millis": item.get("duration_millis"),
                        "download": {
                            "local_path": item.get("local_path"),
                            "thumbnail_local_path": item.get("thumbnail_local_path"),
                        },
                    }
                    for item in media_by_tweet_id.get(tid, [])
                ],
                "raw_json": raw_json,
                "qt_media": [],
                "qt_media_tags": None,
                "media_tags": tags_dict.get(tid),
            }

        known_relation_ids = {
            value
            for relation in all_relations
            for value in (relation.get("tweet_id"), relation.get("target_tweet_id"))
            if value
        }
        for tid in related_ids:
            if tid not in formatted and tid in known_relation_ids:
                formatted[tid] = missing_web_tweet(tid)

        if tweet_id not in formatted:
            raise HTTPException(status_code=404, detail="Tweet not found")

        annotate_web_tweets(store, list(formatted.values()))

        main_tweet = formatted.get(tweet_id)

        main_tweet["local_quote_count"] = store.conn.execute(
            """
            SELECT COUNT(DISTINCT tweet_id)
            FROM archive INDEXED BY idx_archive_target_tweet_id
            WHERE record_type = 'tweet_relation'
              AND relation_type = 'quote_of'
              AND target_tweet_id = ?
            """,
            (tweet_id,),
        ).fetchone()[0]

        parents = []
        curr_id = tweet_id
        seen_parents = set()
        while True:
            next_parent = None
            for r in all_relations:
                rel_type = r.get("relation_type")
                src = r.get("tweet_id")
                tgt = r.get("target_tweet_id")

                if src == curr_id and rel_type in ("reply_to", "thread_parent"):
                    next_parent = tgt
                    break
                elif tgt == curr_id and rel_type == "thread_child":
                    next_parent = src
                    break

            if next_parent and next_parent in formatted and next_parent not in seen_parents:
                parents.append(formatted[next_parent])
                seen_parents.add(next_parent)
                curr_id = next_parent
            else:
                break

        child_ids_by_parent: dict[str, list[str]] = {}
        depth_by_tweet_id: dict[str, int] = {}
        for relation in descendant_relations:
            child_id = relation["tweet_id"]
            parent_id = relation["target_tweet_id"]
            children_for_parent = child_ids_by_parent.setdefault(parent_id, [])
            if child_id not in children_for_parent:
                children_for_parent.append(child_id)
            depth_by_tweet_id[child_id] = min(
                relation["depth"], depth_by_tweet_id.get(child_id, relation["depth"])
            )

        children_map = {}
        seen_reply_ids = {tweet_id}

        def append_descendants(parent_id: str, destination: list[dict]) -> None:
            for nested_id in child_ids_by_parent.get(parent_id, []):
                if nested_id in seen_reply_ids:
                    continue
                seen_reply_ids.add(nested_id)
                nested_reply = formatted.get(nested_id)
                if nested_reply is None:
                    continue
                nested_reply["thread_depth"] = depth_by_tweet_id.get(nested_id, 2)
                nested_reply["parent_tweet_id"] = parent_id
                # Keep the response key for compatibility. Each destination contains its
                # direct child's complete bounded descendant list in depth-first order.
                destination.append(nested_reply)
                append_descendants(nested_id, destination)

        for child_id in child_ids_by_parent.get(tweet_id, []):
            child = formatted.get(child_id)
            if child is None or child_id in seen_reply_ids:
                continue
            seen_reply_ids.add(child_id)
            child["thread_depth"] = 1
            child["parent_tweet_id"] = tweet_id
            child["op_replies"] = []
            children_map[child_id] = child
            append_descendants(child_id, child["op_replies"])

        parents.sort(key=lambda x: x["created_at"] or "")
        children = list(children_map.values())

        def get_likes(t):
            raw = t.get("raw_json", {}) or {}
            return int(raw.get("legacy", {}).get("favorite_count", 0))

        children.sort(key=lambda x: (len(x.get("op_replies", [])) > 0, get_likes(x)), reverse=True)

        return {
            "main": main_tweet,
            "parents": parents,
            "children": children,
            "reply_tree_truncated": reply_tree_truncated,
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/tweets/{tweet_id}/quotes")
def api_tweet_quotes(
    tweet_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    store=Depends(require_store),  # noqa: B008
    _auth: bool = Depends(verify_credentials),
):
    """Fetch quotes of a specific tweet."""
    try:
        start = (page - 1) * limit

        expr = (
            "record_type = 'tweet_relation' AND relation_type = 'quote_of' "
            f"AND target_tweet_id = {_sql_quote(tweet_id)}"
        )
        total = store.conn.execute(
            """
            SELECT COUNT(DISTINCT tweet_id)
            FROM archive INDEXED BY idx_archive_target_tweet_id
            WHERE record_type = 'tweet_relation'
              AND relation_type = 'quote_of'
              AND target_tweet_id = ?
            """,
            (tweet_id,),
        ).fetchone()[0]

        rows = store._query(
            expr=expr,
            cols=["DISTINCT tweet_id"],
            order_by="tweet_id DESC",
            limit=limit,
            offset=start,
            indexed_by="idx_archive_target_tweet_id",
        )
        paginated_ids = [r.get("tweet_id") for r in rows if r.get("tweet_id")]
        paginated_tweets = store.fetch_tweets_by_ids(paginated_ids)
        annotate_web_tweets(store, paginated_tweets)

        return {"tweets": paginated_tweets, "total": total, "page": page, "limit": limit}
    except Exception as e:
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/authors/search")
def search_authors_api(
    q: str = Query(""),
    store=Depends(require_store),  # noqa: B008
    _auth: bool = Depends(verify_credentials),
):
    """Search for authors by username or display name."""
    try:
        authors = store.search_authors(q, limit=10)
        return {"authors": authors}
    except Exception as e:
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e)) from e
