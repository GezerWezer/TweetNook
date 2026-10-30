"""Tweet query, thread, and quote endpoints."""

import html
import json
import re
import unicodedata

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from tweetnook.core import fastjson
from tweetnook.core.collection_names import normalize_collection_name
from tweetnook.core.media_metadata import aspect_ratio_for_media
from tweetnook.storage.search_query import (
    SearchCursorExpiredError,
    SearchQueryError,
    count_posts,
    parse_search_query,
    search_posts,
)
from tweetnook.web.availability import annotate_web_tweets, missing_web_tweet, slim_web_tweets
from tweetnook.web.deps import require_store, verify_credentials
from tweetnook.web.history import tweet_history, tweet_note_history

router = APIRouter()

# Direct replies that only @mention someone else move behind "Show other
# replies" unless they have at least this many likes.
OTHER_REPLY_LIKE_THRESHOLD = 5
_MENTION_RE = re.compile(r"(?<![\w@])@(\w{1,15})", re.ASCII)
_LEADING_MENTIONS_RE = re.compile(r"^(?:\s*@\w{1,15})+\s*", re.ASCII)


def _json(payload: dict) -> Response:
    """Serialize plain JSON data directly with orjson, skipping FastAPI's encoder.

    Tweet payloads are large nested trees of JSON-native values; the generic
    encoder costs several times the serialization itself.
    """
    return Response(fastjson.dumps(payload), media_type="application/json")


def _reply_body(legacy: dict) -> str:
    """The text a reply shows, without the automatic leading @mentions."""
    text = legacy.get("full_text")
    if not isinstance(text, str):
        return ""
    bounds = legacy.get("display_text_range")
    if (
        isinstance(bounds, list)
        and len(bounds) == 2
        and all(isinstance(value, int) for value in bounds)
        and 0 <= bounds[0] <= bounds[1] <= len(text)
    ):
        body = text[bounds[0] : bounds[1]]
    else:
        body = _LEADING_MENTIONS_RE.sub("", text)
    return html.unescape(body)


def _is_mention_only_reply(tweet: dict, op_username: str | None) -> bool:
    """True for a low-like reply whose text only @mentions accounts other than OP."""
    raw = tweet.get("raw_json")
    legacy = raw.get("legacy") if isinstance(raw, dict) else None
    if not isinstance(legacy, dict) or tweet.get("media"):
        return False
    try:
        likes = int(legacy.get("favorite_count") or 0)
    except (TypeError, ValueError):
        likes = 0
    if likes >= OTHER_REPLY_LIKE_THRESHOLD:
        return False
    entities = legacy.get("entities") or {}
    if (
        any(entities.get(key) for key in ("media", "urls", "hashtags", "symbols"))
        or (legacy.get("extended_entities") or {}).get("media")
        or legacy.get("is_quote_status")
        or legacy.get("quoted_status_id_str")
        or legacy.get("retweeted_status_id_str")
        or any(raw.get(key) for key in ("quoted_status_result", "card", "note_tweet", "article"))
    ):
        return False
    body = _reply_body(legacy)
    mentions = {name.lower() for name in _MENTION_RE.findall(body)}
    remainder = _MENTION_RE.sub("", body)
    if not mentions or any(
        not ch.isspace() and unicodedata.category(ch)[0] not in "PZ" for ch in remainder
    ):
        return False
    ignored = {(tweet.get("author") or {}).get("username") or "", op_username or ""}
    return bool(mentions - {name.lower() for name in ignored})


def _is_other_reply(child: dict, op_username: str | None) -> bool:
    """Hide a mention-only branch unless any tweet in it is saved to the archive."""
    branch = [child, *child.get("op_replies", [])]
    if any(tweet.get("collections") for tweet in branch):
        return False
    return _is_mention_only_reply(child, op_username)


def _conversation_author(main: dict, parents: list[dict], formatted: dict) -> str | None:
    raw = main.get("raw_json")
    legacy = raw.get("legacy") if isinstance(raw, dict) else None
    root_id = legacy.get("conversation_id_str") if isinstance(legacy, dict) else None
    root = formatted.get(root_id) if root_id else None
    root = root or (parents[0] if parents else main)
    return (root.get("author") or {}).get("username")


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
        slim_web_tweets(paginated_tweets)

        return _json(
            {
                "tweets": paginated_tweets,
                "total": result.total,
                "page": result.page,
                "pages": result.pages,
                "has_more": result.has_more,
                "next_cursor": result.next_cursor,
                "truncated": result.truncated,
                "highlight_terms": parse_search_query(q).highlight_terms(),
            }
        )
    except SearchCursorExpiredError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SearchQueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as e:
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/tweets/count")
def api_tweets_count(
    q: str | None = None,
    collection: str = Query("all"),
    store=Depends(require_store),  # noqa: B008
    _auth: bool = Depends(verify_credentials),
):
    """Count a search separately so the first page can render immediately."""
    try:
        try:
            internal_col = normalize_collection_name(collection)
        except ValueError:
            internal_col = "all"
        reader = store.open_reader()
        try:
            total = count_posts(
                reader, q, collections={internal_col} if internal_col != "all" else None
            )
        finally:
            reader.close()
        return {"total": total}
    except SearchQueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/tweets/{tweet_id}")
def api_tweet_thread(
    tweet_id: str,
    store=Depends(require_store),  # noqa: B008
    _auth: bool = Depends(verify_credentials),
):
    try:
        ancestor_ids = store.ancestor_chain(tweet_id)
        related_ids = {tweet_id, *ancestor_ids}

        descendant_relations, reply_tree_truncated = store.reply_subtree(tweet_id)
        all_relations = descendant_relations
        for relation in descendant_relations:
            related_ids.add(relation["tweet_id"])
            related_ids.add(relation["target_tweet_id"])

        observations = store.thread_observations(related_ids)
        objs, media = observations.objects, observations.media
        col_rows, tag_rows = observations.memberships, observations.tags

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
                tags_dict[row["tweet_id"]] = fastjson.loads(row["raw_json"])
            except (TypeError, json.JSONDecodeError):
                continue

        raw_by_tweet_id = {}
        for obj in objs:
            tid = obj.get("tweet_id")
            raw_json = None
            if obj.get("raw_json"):
                try:
                    parsed_raw = fastjson.loads(obj["raw_json"])
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

        def thread_media_payload(row, raw_tweet):
            payload = {
                "source": row.get("source"),
                "type": row.get("media_type"),
                "url": row.get("media_url"),
                "thumbnail_url": row.get("thumbnail_url"),
                "width": row.get("width"),
                "height": row.get("height"),
                "duration_millis": row.get("duration_millis"),
                "download": {
                    "local_path": row.get("local_path"),
                    "thumbnail_local_path": row.get("thumbnail_local_path"),
                },
            }
            aspect_ratio = aspect_ratio_for_media(
                raw_tweet, row.get("position"), row.get("media_type")
            )
            if aspect_ratio is not None:
                payload["aspect_ratio"] = aspect_ratio
            return payload

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
                "media": [thread_media_payload(m, raw_json) for m in t_media],
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
                    parsed_raw = fastjson.loads(membership["raw_json"])
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
                    thread_media_payload(item, raw_json) for item in media_by_tweet_id.get(tid, [])
                ],
                "raw_json": raw_json,
                "qt_media": [],
                "qt_media_tags": None,
                "media_tags": tags_dict.get(tid),
            }

        known_relation_ids = set(ancestor_ids) | {
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

        main_tweet["local_quote_count"] = store.quote_count(tweet_id)

        parents = [formatted[parent_id] for parent_id in ancestor_ids if parent_id in formatted]

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
        op_username = _conversation_author(main_tweet, parents, formatted)
        other_children = [child for child in children if _is_other_reply(child, op_username)]
        if other_children:
            other_ids = {child["tweet_id"] for child in other_children}
            children = [child for child in children if child["tweet_id"] not in other_ids]
        slim_web_tweets(formatted.values())

        return _json(
            {
                "main": main_tweet,
                "parents": parents,
                "children": children,
                "other_children": other_children,
                "reply_tree_truncated": reply_tree_truncated,
            }
        )
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

        total = store.quote_count(tweet_id)
        paginated_ids = store.quote_page(tweet_id, offset=start, limit=limit)
        # Quotes by other people are usually stored only as tweet objects, outside
        # every collection; a source with no stored tweet keeps its place as a
        # placeholder so the page matches the total.
        fetched = {
            tweet["tweet_id"]: tweet
            for tweet in store.fetch_tweets_by_ids(paginated_ids, include_objects=True)
        }
        paginated_tweets = [fetched.get(tid) or missing_web_tweet(tid) for tid in paginated_ids]
        annotate_web_tweets(store, paginated_tweets)
        slim_web_tweets(paginated_tweets)

        return _json({"tweets": paginated_tweets, "total": total, "page": page, "limit": limit})
    except Exception as e:
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/tweets/{tweet_id}/raw")
def api_tweet_raw(
    tweet_id: str,
    store=Depends(require_store),  # noqa: B008
    _auth: bool = Depends(verify_credentials),
):
    """Return the complete stored tweet JSON that list and thread views abbreviate.

    A collection tweet returns the same canonical document the list shows; other
    archived tweets return their stored object.
    """
    tweets = store.fetch_tweets_by_ids([tweet_id])
    if tweets:
        annotate_web_tweets(store, tweets)
        raw_json = tweets[0].get("raw_json")
    else:
        saved_json = store.stored_tweet_json(tweet_id)
        raw_json = None
        if saved_json:
            try:
                raw_json = fastjson.loads(saved_json)
            except (TypeError, json.JSONDecodeError):
                raw_json = None
    if not isinstance(raw_json, dict):
        raise HTTPException(status_code=404, detail="No stored JSON for this tweet")
    return _json({"tweet_id": tweet_id, "raw_json": raw_json})


@router.get("/api/tweets/{tweet_id}/history")
def api_tweet_history(
    tweet_id: str,
    store=Depends(require_store),  # noqa: B008
    _auth: bool = Depends(verify_credentials),
):
    """Availability checks, lookups, copies, and community-note versions for one tweet."""
    if not tweet_id.isdigit():
        raise HTTPException(status_code=404, detail="Unknown tweet")
    return _json(tweet_history(store, tweet_id))


@router.get("/api/tweets/{tweet_id}/notes")
def api_tweet_notes(
    tweet_id: str,
    store=Depends(require_store),  # noqa: B008
    _auth: bool = Depends(verify_credentials),
):
    """Every recorded version of one tweet's community note."""
    if not tweet_id.isdigit():
        raise HTTPException(status_code=404, detail="Unknown tweet")
    return _json(tweet_note_history(store, tweet_id))


@router.put("/api/tweets/{tweet_id}/hidden")
def api_hide_tweet(
    tweet_id: str,
    store=Depends(require_store),  # noqa: B008
    _auth: bool = Depends(verify_credentials),
) -> dict[str, bool]:
    """Hide a tweet from lists and searches; `is:hidden` still finds it."""
    store.set_tweet_hidden(tweet_id, True)
    return {"hidden": True}


@router.delete("/api/tweets/{tweet_id}/hidden")
def api_unhide_tweet(
    tweet_id: str,
    store=Depends(require_store),  # noqa: B008
    _auth: bool = Depends(verify_credentials),
) -> dict[str, bool]:
    """Show a hidden tweet in lists and searches again."""
    store.set_tweet_hidden(tweet_id, False)
    return {"hidden": False}


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


@router.get("/api/authors/profile")
def author_profile_api(
    author_id: str = Query("", description="Author user ID; takes precedence over username."),
    username: str = Query("", description="Handle to resolve through saved tweets."),
    limit: int = Query(50, ge=1, le=100, description="Most snapshots to return."),
    store=Depends(require_store),  # noqa: B008
    _auth: bool = Depends(verify_credentials),
):
    """Return an author's captured profile snapshots, most recently fetched first."""
    resolved = author_id.strip() or (
        store.resolve_author_id(username.strip()) if username.strip() else None
    )
    snapshots = store.author_profile_snapshots(resolved, limit=limit) if resolved else []
    return {"author_id": resolved, "snapshots": snapshots}
