#!/usr/bin/env python3
"""Capture selected public tweets in a private archive and prepare demo media."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import unicodedata
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path

from tweetnook.client.timelines import parse_tweet_detail_response
from tweetnook.config import XDGPaths, ensure_paths, load_config
from tweetnook.extractor import extract_status_id_from_url
from tweetnook.media import download_media
from tweetnook.media_metadata import raw_tweet_media
from tweetnook.storage import open_archive_store
from tweetnook.threads import expand_threads, normalize_thread_target
from tweetnook.web import deps
from tweetnook.web.articles import annotate_web_articles
from tweetnook.web.routes.avatars import _avatar_response

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "data" / "demo-content"
PUBLIC_MEDIA = ROOT / "demo" / "demo media"


def image_size(path: Path) -> tuple[int, int]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    stream = json.loads(result.stdout)["streams"][0]
    return int(stream["width"]), int(stream["height"])


def compress_image(source: Path, relative: Path, *, max_edge: int) -> tuple[str, int, int]:
    target = PUBLIC_MEDIA / relative.with_suffix(".webp")
    target.parent.mkdir(parents=True, exist_ok=True)
    width, height = image_size(source)
    scale = min(1, max_edge / max(width, height))
    width, height = max(1, round(width * scale)), max(1, round(height * scale))
    # Keep avatars at the original quality; sharper chroma protects colored text.
    is_avatar = max_edge <= 128
    temporary = target.with_suffix(".partial.webp")
    try:
        subprocess.run(
            [
                "cwebp",
                "-quiet",
                "-q",
                "78" if is_avatar else "72",
                "-m",
                "6",
                *([] if is_avatar else ["-sharp_yuv"]),
                "-metadata",
                "none",
                "-resize",
                str(width),
                str(height),
                str(source),
                "-o",
                str(temporary),
            ],
            check=True,
        )
        if (
            not target.exists()
            or target.stat().st_mtime < source.stat().st_mtime
            or temporary.stat().st_size < target.stat().st_size * 0.95
        ):
            temporary.replace(target)
        else:
            width, height = image_size(target)
    finally:
        temporary.unlink(missing_ok=True)
    return "demo/media/" + target.relative_to(PUBLIC_MEDIA).as_posix(), width, height


def compress_video(source: Path, relative: Path) -> dict:
    """Keep playback/audio and use H.264 MP4 for the static site's video player."""
    target = PUBLIC_MEDIA / relative.with_suffix(".mp4")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or target.stat().st_mtime < source.stat().st_mtime:
        info = json.loads(
            subprocess.check_output(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=avg_frame_rate",
                    "-of",
                    "json",
                    str(source),
                ]
            )
        )
        fps = Fraction(info["streams"][0]["avg_frame_rate"])
        filters = (
            "scale=w='min(1280,iw)':h='min(1280,ih)':"
            "force_original_aspect_ratio=decrease:force_divisible_by=2"
        )
        if fps > 30:
            filters += ",fps=30"
        temporary = target.with_suffix(".partial.mp4")
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-y",
                    "-i",
                    str(source),
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a?",
                    "-vf",
                    filters,
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryslow",
                    "-crf",
                    "28",
                    "-threads",
                    "2",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "64k",
                    "-ac",
                    "2",
                    "-map_metadata",
                    "-1",
                    "-movflags",
                    "+faststart",
                    str(temporary),
                ],
                check=True,
            )
            if temporary.stat().st_size >= source.stat().st_size:
                # Some small reaction GIFs are already efficient; keep their encoding.
                subprocess.run(
                    [
                        "ffmpeg",
                        "-nostdin",
                        "-v",
                        "error",
                        "-y",
                        "-i",
                        str(source),
                        "-map",
                        "0:v:0",
                        "-map",
                        "0:a?",
                        "-c",
                        "copy",
                        "-map_metadata",
                        "-1",
                        "-movflags",
                        "+faststart",
                        str(temporary),
                    ],
                    check=True,
                )
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    width, height = image_size(target)
    duration = float(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(target),
            ]
        )
    )
    return {
        "url": "demo/media/" + target.relative_to(PUBLIC_MEDIA).as_posix(),
        "width": width,
        "height": height,
        "aspect_ratio": [width, height],
        "duration_millis": round(duration * 1000),
    }


def context_rows(store, selected: list[str]) -> list[dict]:
    """Keep selected conversations and quotes; TweetDetail can also return unrelated ads."""
    rows = store._query(expr="record_type = 'tweet_object'", limit=100_000)
    edges: dict[str, set[str]] = {}
    for row in store._query(expr="record_type = 'tweet_relation'", limit=500_000):
        if row["relation_type"] not in {
            "quote_of",
            "retweet_of",
            "reply_to",
            "thread_parent",
            "thread_child",
        }:
            continue
        source, target = row["tweet_id"], row["target_tweet_id"]
        edges.setdefault(source, set()).add(target)
        edges.setdefault(target, set()).add(source)
    # Embedded quotes preserve their own links in raw_json even when the extractor's
    # bounded graph walk has not emitted that deeper relation yet.
    for row in rows:
        legacy = json.loads(row["raw_json"]).get("legacy") or {}
        for field in (
            "quoted_status_id_str",
            "in_reply_to_status_id_str",
            "retweeted_status_id_str",
        ):
            target = legacy.get(field)
            if target:
                edges.setdefault(row["tweet_id"], set()).add(target)
                edges.setdefault(target, set()).add(row["tweet_id"])
    keep = set(selected)
    pending = list(selected)
    while pending:
        for neighbor in edges.get(pending.pop(), set()) - keep:
            keep.add(neighbor)
            pending.append(neighbor)
    return [row for row in rows if row["tweet_id"] in keep]


def trim_basic_replies(tweets: list[dict]) -> list[dict]:
    """Omit mention-only context replies unless a kept tweet needs them as an ancestor/quote."""
    mentions = re.compile(r"(?<![\w@])@[A-Za-z0-9_]{1,15}\b")

    def basic(tweet: dict) -> bool:
        if not tweet.get("reply_to_id") or any(
            tweet.get(field)
            for field in (
                "collections",
                "media",
                "quoted_tweet_id",
                "urls",
                "article",
                "card",
                "tags",
                "description",
            )
        ):
            return False
        text = tweet.get("text") or ""
        return bool(mentions.search(text)) and all(
            char.isspace() or unicodedata.category(char).startswith("P")
            for char in mentions.sub("", text)
        )

    by_id = {tweet["tweet_id"]: tweet for tweet in tweets}
    keep = {tweet["tweet_id"] for tweet in tweets if not basic(tweet)}
    pending = list(keep)
    while pending:
        tweet = by_id[pending.pop()]
        for field in ("reply_to_id", "quoted_tweet_id"):
            related = tweet.get(field)
            if related in by_id and related not in keep:
                keep.add(related)
                pending.append(related)
    return [tweet for tweet in tweets if tweet["tweet_id"] in keep]


def trim_reviewed_replies(tweets: list[dict], excluded: set[str]) -> list[dict]:
    """Apply the explicit editorial review without dropping saves or required context."""
    dropped = {tweet["tweet_id"] for tweet in tweets if tweet["tweet_id"] in excluded}
    for tweet in tweets:
        if tweet["tweet_id"] in dropped:
            if not tweet.get("reply_to_id") or tweet.get("collections"):
                raise ValueError("Reply review cannot remove saved posts or conversation roots")
            continue
        if tweet.get("reply_to_id") in dropped or tweet.get("quoted_tweet_id") in dropped:
            raise ValueError("Reply review would remove context required by a retained post")
    return [tweet for tweet in tweets if tweet["tweet_id"] not in dropped]


def limit_reply_hops(tweets: list[dict], *, max_hops: int = 1) -> tuple[list[dict], set[str]]:
    """Keep direct replies per saved/quoted conversation, plus required context objects."""
    by_id = {tweet["tweet_id"]: tweet for tweet in tweets}
    children: dict[str, list[str]] = defaultdict(list)
    for tweet in tweets:
        if parent := tweet.get("reply_to_id"):
            children[parent].append(tweet["tweet_id"])

    def references(tweet: dict) -> set[str]:
        targets = {tweet["quoted_tweet_id"]} if tweet.get("quoted_tweet_id") else set()
        for row in tweet.get("urls", []):
            for field in ("expanded_url", "url", "short_url"):
                if target := extract_status_id_from_url(row.get(field) or ""):
                    if target in by_id:
                        targets.add(target)
                    break
        return targets

    depths = {tweet["tweet_id"]: 0 for tweet in tweets if tweet.get("collections")}
    pending = deque(depths)
    while pending:
        tweet_id = pending.popleft()
        edges = [(target, 0) for target in references(by_id[tweet_id])]
        if depths[tweet_id] < max_hops:
            edges.extend((target, depths[tweet_id] + 1) for target in children[tweet_id])
        for target, depth in edges:
            if target in by_id and (target not in depths or depth < depths[target]):
                depths[target] = depth
                pending.append(target)

    # Saved replies can lie beyond the cap; keep their ancestors without expanding
    # siblings or rediscovering branches through those extra context objects.
    keep = set(depths)
    required = list(keep)
    while required:
        tweet = by_id[required.pop()]
        for target in {tweet.get("reply_to_id"), *references(tweet)}:
            if target and target in by_id and target not in keep:
                keep.add(target)
                required.append(target)
    return [tweet for tweet in tweets if tweet["tweet_id"] in keep], {
        tweet_id for tweet_id, depth in depths.items() if depth == 0
    }


def publication_rows(store, guides: dict, selected: list[str]) -> tuple[list[dict], set[str]]:
    """Bound publication before encoding media or requesting author avatars."""
    rows = context_rows(store, selected)
    prior = {tweet["tweet_id"]: tweet for tweet in guides["tweets"]}
    media_tweets = {
        media["tweet_id"]
        for media in store.list_media_rows()
        if media.get("source") == "tweet_media"
    }
    records = [
        tweet
        for tweet in guides["tweets"]
        if "guide tweet" in tweet.get("tags", [])
        or (tweet["author_id"] == guides["owner_user_id"] and "tweet" in tweet["collections"])
    ]
    for row in rows:
        legacy = json.loads(row["raw_json"]).get("legacy") or {}
        records.append(
            {
                "tweet_id": row["tweet_id"],
                "text": row["text"],
                "media": [True] if row["tweet_id"] in media_tweets else [],
                "tags": prior.get(row["tweet_id"], {}).get("tags", []),
                "description": prior.get(row["tweet_id"], {}).get("description", ""),
                "reply_to_id": legacy.get("in_reply_to_status_id_str"),
                "quoted_tweet_id": legacy.get("quoted_status_id_str"),
                "urls": (legacy.get("entities") or {}).get("urls", []),
                "collections": (["like"] if row["tweet_id"] in selected else [])
                + (
                    ["bookmark"]
                    if "bookmark" in prior.get(row["tweet_id"], {}).get("collections", [])
                    else []
                ),
            }
        )
    review = json.loads((ROOT / "demo" / "reply-review.json").read_text())
    records = trim_basic_replies(records)
    records = trim_reviewed_replies(records, set(review["excluded"]))
    retained, roots = limit_reply_hops(records)
    keep = {tweet["tweet_id"] for tweet in retained}
    return [row for row in rows if row["tweet_id"] in keep], roots


def missing_context_targets(store, selected: list[str]) -> list[str]:
    """Find required parents/quotes omitted from captured TweetDetail result objects."""
    rows = context_rows(store, selected)
    reachable = {row["tweet_id"] for row in rows}
    known = store.list_known_tweet_ids()
    targets = {
        row["target_tweet_id"]
        for row in store._query(expr="record_type = 'tweet_relation'", limit=500_000)
        if row["tweet_id"] in reachable
        and row["relation_type"] in {"quote_of", "retweet_of", "reply_to", "thread_parent"}
        and row.get("target_tweet_id")
    }
    for row in rows:
        legacy = json.loads(row["raw_json"]).get("legacy") or {}
        targets.update(
            legacy[field]
            for field in (
                "quoted_status_id_str",
                "in_reply_to_status_id_str",
                "retweeted_status_id_str",
            )
            if legacy.get(field)
        )
    return sorted(targets - known)


def pending_context_targets(store, selected: list[str]) -> list[str]:
    """Embedded quote objects still need their own production conversation capture."""
    rows = context_rows(store, selected)
    reachable = {row["tweet_id"] for row in rows}
    quotes = {
        row["target_tweet_id"]
        for row in store._query(expr="record_type = 'tweet_relation'", limit=500_000)
        if row["tweet_id"] in reachable
        and row["relation_type"] == "quote_of"
        and row.get("target_tweet_id")
    }
    for row in rows:
        quote = (json.loads(row["raw_json"]).get("legacy") or {}).get("quoted_status_id_str")
        if quote:
            quotes.add(quote)
    expanded = set(store.list_expanded_thread_target_ids())
    return sorted(set(missing_context_targets(store, selected)) | (quotes - expanded))


def attachment_link_metadata(raw: dict) -> dict:
    """Retain the URL entities the production renderer uses to hide attachment links."""
    metadata = {}
    media_urls = list(
        dict.fromkeys(item["url"] for item in raw_tweet_media(raw) if item.get("url"))
    )
    if media_urls:
        metadata["media_short_urls"] = media_urls
    permalink = ((raw.get("legacy") or {}).get("quoted_status_permalink") or {}).get("url")
    if permalink:
        metadata["quote_short_url"] = permalink
    return metadata


def project_archive(
    store, guides: dict, selected: list[str], avatar_paths: dict[str, Path]
) -> dict:
    """Project captured canonical objects; collection membership is curated for the demo."""
    guide_tweets = [tweet for tweet in guides["tweets"] if "guide tweet" in tweet["tags"]]
    guide_author_ids = {tweet["author_id"] for tweet in guide_tweets} | {guides["owner_user_id"]}
    authors = {
        author["id"]: author for author in guides["authors"] if author["id"] in guide_author_ids
    }
    rows, _ = publication_rows(store, guides, selected)
    article_nodes = [{"tweet_id": row["tweet_id"]} for row in rows]
    annotate_web_articles(store, article_nodes)
    articles = {node["tweet_id"]: node["article"] for node in article_nodes if "article" in node}
    prior_order = {tweet["tweet_id"]: index for index, tweet in enumerate(guides["tweets"])}
    rows.sort(
        key=lambda row: (0, prior_order[row["tweet_id"]])
        if row["tweet_id"] in prior_order
        else (1, int(row["tweet_id"]))
    )
    ids = {row["tweet_id"] for row in rows}
    media_by_tweet: dict[str, list] = {}
    for media in store.list_media_rows():
        media_by_tweet.setdefault(media["tweet_id"], []).append(media)
    # The public fixture also carries deliberate demo curation, independent of X.
    prior_tweets = {tweet["tweet_id"]: tweet for tweet in guides["tweets"]}
    owned_tweets = [
        tweet
        for tweet in guides["tweets"]
        if tweet["author_id"] == guides["owner_user_id"] and "tweet" in tweet["collections"]
    ]
    tweets = [*guide_tweets, *owned_tweets]
    for row in rows:
        raw = json.loads(row["raw_json"])
        legacy = raw.get("legacy") or {}
        user = raw["core"]["user_results"]["result"]
        profile = user.get("legacy") or {}
        author_id = row["author_id"]
        if author_id not in authors:
            avatar_url, _, _ = compress_image(
                avatar_paths[author_id],
                Path("real/avatars") / f"{author_id}.webp",
                max_edge=128,
            )
            profile_snapshots = store.author_profile_snapshots(author_id)
            authors[author_id] = {
                "id": author_id,
                "username": row["author_username"],
                "display_name": row["author_display_name"],
                "verified": bool(user.get("is_blue_verified") or profile.get("verified")),
                "followers_count": profile.get("followers_count", 0),
                "following_count": profile.get("friends_count", 0),
                "description": (
                    profile_snapshots[0]["description"]
                    if profile_snapshots
                    else profile.get("description", "")
                ),
                "profile_snapshots": profile_snapshots,
                "avatar_url": avatar_url,
            }
        media_items = []
        article = articles.get(row["tweet_id"])
        article_media = []
        article_metadata = {item["media_key"]: item for item in (article or {}).get("media", [])}
        for media in sorted(
            media_by_tweet.get(row["tweet_id"], []), key=lambda m: int(m["position"])
        ):
            is_article = media["media_key"] in article_metadata
            if media.get("source") != "tweet_media" and not is_article:
                continue
            if is_article and media["media_type"] != "photo":
                raise ValueError("Article reader currently supports captured images only")
            if not media.get("local_path"):
                raise ValueError(f"Missing downloaded media for {row['tweet_id']}")
            source = PRIVATE / media["local_path"]
            if media["media_type"] in {"video", "animated_gif"}:
                item = {
                    "type": media["media_type"],
                    **compress_video(
                        source,
                        Path("real") / row["tweet_id"] / source.name,
                    ),
                }
                if media.get("thumbnail_local_path"):
                    poster = PRIVATE / media["thumbnail_local_path"]
                    item["thumbnail_url"], _, _ = compress_image(
                        poster,
                        Path("real") / row["tweet_id"] / poster.name,
                        max_edge=1280,
                    )
                media_items.append(item)
                continue
            if media["media_type"] != "photo":
                raise ValueError(f"Unsupported media type: {media['media_type']}")
            url, width, height = compress_image(
                source,
                Path("real") / row["tweet_id"] / source.name,
                max_edge=1280,
            )
            item = {"type": "photo", "url": url, "width": width, "height": height}
            if is_article:
                metadata = article_metadata[media["media_key"]]
                article_media.append(
                    {
                        **item,
                        **{
                            key: metadata[key]
                            for key in ("media_key", "media_id", "source", "alt_text")
                        },
                    }
                )
            else:
                media_items.append(item)
        if article is not None:
            article["media"] = article_media
        entities = legacy.get("entities") or {}
        is_selected = row["tweet_id"] in selected
        prior = prior_tweets.get(row["tweet_id"], {})
        tweets.append(
            {
                "tweet_id": row["tweet_id"],
                "text": row["text"],
                "display_text_range": legacy.get("display_text_range"),
                "author_id": author_id,
                "created_at": row["created_at"],
                "synced_at": row["synced_at"],
                "collections": (["like"] if is_selected else [])
                + (["bookmark"] if "bookmark" in prior.get("collections", []) else []),
                "liked_order": (prior.get("liked_order") or 100 - selected.index(row["tweet_id"]))
                if is_selected
                else 0,
                "engagement": {
                    "replies": legacy.get("reply_count", 0),
                    "retweets": legacy.get("retweet_count", 0),
                    "likes": legacy.get("favorite_count", 0),
                    "bookmarks": legacy.get("bookmark_count", 0),
                    "views": int((raw.get("views") or {}).get("count") or 0),
                },
                "hashtags": [item["text"] for item in entities.get("hashtags", [])],
                "mentions": [item["screen_name"] for item in entities.get("user_mentions", [])],
                "urls": entities.get("urls", []),
                "media": media_items,
                "source": raw.get("source"),
                "card_name": None,
                "conversation_id": legacy.get("conversation_id_str") or row["tweet_id"],
                "reply_to_id": legacy.get("in_reply_to_status_id_str"),
                "reply_to_username": legacy.get("in_reply_to_screen_name"),
                "quoted_tweet_id": legacy.get("quoted_status_id_str"),
                "tags": prior.get("tags", []),
                "description": prior.get("description", ""),
                **(
                    {"community_note": prior["community_note"]}
                    if prior.get("community_note")
                    else {}
                ),
                **({"article": article} if article is not None else {}),
                **attachment_link_metadata(raw),
            }
        )
        for field in ("reply_to_id", "quoted_tweet_id"):
            related = tweets[-1][field]
            if related and related not in ids:
                raise ValueError(f"Fetch missing {field} {related} before publishing")
    tweets = trim_basic_replies(tweets)
    review = json.loads((ROOT / "demo" / "reply-review.json").read_text())
    tweets = trim_reviewed_replies(tweets, set(review["excluded"]))
    tweets, reply_roots = limit_reply_hops(tweets)
    author_ids = {tweet["author_id"] for tweet in tweets} | {guides["owner_user_id"]}
    return {
        "schema_version": 2,
        "owner_user_id": guides["owner_user_id"],
        "generated_at": datetime.now(UTC).isoformat(),
        "demo_metadata": {
            **guides.get("demo_metadata", {}),
            "reply_policy": {"max_hops": 1, "preserve_saved_and_required_context": True},
            "expanded_thread_ids": sorted(
                set(store.list_expanded_thread_target_ids()) & reply_roots
            ),
        },
        "authors": [author for uid, author in authors.items() if uid in author_ids],
        "tweets": tweets,
    }


async def prepare(targets: list[str]) -> None:
    config, _ = load_config()  # Read existing credentials; never copy them into demo files.
    config.web = config.web.model_copy(
        update={"fetch_avatars": True, "avatar_cache_limit_enabled": False}
    )
    paths = ensure_paths(
        XDGPaths(config_dir=PRIVATE / "config", data_dir=PRIVATE, cache_dir=PRIVATE / "cache")
    )
    manifest_path = PRIVATE / "selected.json"
    selected = json.loads(manifest_path.read_text()) if manifest_path.exists() else []
    requested = list(dict.fromkeys(normalize_thread_target(target) for target in targets))
    store = open_archive_store(paths, create=True, config=config)
    store.ensure_archive_owner_id(config.auth.user_id)
    store.close()
    result = await expand_threads(targets=requested, config=config, paths=paths)
    if result.failed:
        raise RuntimeError(f"{result.failed} tweet captures failed")
    store = open_archive_store(paths, create=False, config=config)
    try:
        for tweet_id in requested:
            capture = store.conn.execute(
                "SELECT raw_json FROM archive WHERE record_type='raw_capture' "
                "AND operation='ThreadExpandDetail' AND cursor_in=? "
                "ORDER BY captured_at DESC LIMIT 1",
                (tweet_id,),
            ).fetchone()
            if capture is None:
                raise ValueError(f"Missing production capture for {tweet_id}")
            payload = json.loads(capture[0])
            focal = parse_tweet_detail_response(payload, tweet_id)
            if not focal.is_available:
                raise ValueError(f"Tweet {tweet_id} is unavailable")
            # This local membership means selected for the demo, not liked on the user's X account.
            store.persist_page(
                operation="DemoSelection",
                collection_type="like",
                cursor_in=None,
                cursor_out=None,
                http_status=200,
                raw_json=payload,
                tweets=[focal.tweet],
                last_head_tweet_id=None,
                backfill_cursor=None,
                backfill_incomplete=False,
            )
            if tweet_id not in selected:
                selected.append(tweet_id)
        manifest_path.write_text(json.dumps(selected, indent=2) + "\n")
    finally:
        store.close()
    # Capture every quoted conversation, even when its embedded object is already
    # known. Repeat for new quotes discovered in replies, within the configured bound.
    attempted: set[str] = set()
    for depth in range(config.sync.max_linked_depth + 1):
        store = open_archive_store(paths, create=False, config=config)
        try:
            missing = pending_context_targets(store, selected)
        finally:
            store.close()
        if not missing:
            break
        if depth == config.sync.max_linked_depth:
            raise ValueError("Required context exceeds sync.max_linked_depth")
        if attempted.intersection(missing):
            raise ValueError(f"Required context could not be captured: {missing}")
        attempted.update(missing)
        print(f"Expanding {len(missing)} pending context targets")
        result = await expand_threads(targets=missing, config=config, paths=paths)
        if result.failed:
            raise RuntimeError(f"{result.failed} required context captures failed")
    media = await download_media(config=config, paths=paths)
    if media.failed:
        raise RuntimeError(f"{media.failed} media downloads failed")
    store = open_archive_store(paths, create=False, config=config)
    try:
        fixture_path = ROOT / "demo" / "data.json"
        prior_fixture = json.loads(fixture_path.read_text())
        retained_rows, _ = publication_rows(store, prior_fixture, selected)
        previous_state = dict(deps.server_state)
        deps.server_state.update(paths=paths, config=config)
        author_ids = sorted({row["author_id"] for row in retained_rows})
        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                responses = list(pool.map(lambda uid: _avatar_response(uid, store), author_ids))
        finally:
            deps.server_state.clear()
            deps.server_state.update(previous_state)
        avatars = {}
        for uid, response in zip(author_ids, responses, strict=True):
            if not getattr(response, "path", None):
                raise ValueError(f"Avatar fetch failed for author {uid}")
            avatars[uid] = Path(response.path)
        fixture = project_archive(store, prior_fixture, selected, avatars)
        # Validate before replacing the public fixture; original captures stay private.
        from build_pages_demo import validate_fixture, with_demo_metrics

        fixture = with_demo_metrics(fixture)
        validate_fixture(fixture)
        fixture_path.write_text(json.dumps(fixture, indent=2, ensure_ascii=True) + "\n")
        assert store.check_integrity() == ["ok"]
        print(
            f"Prepared {len(selected)} selected tweets, "
            f"{len(fixture['tweets']) - 3} real records, {len(avatars)} avatars"
        )
    finally:
        store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "targets", nargs="+", help="Public tweet IDs or X status URLs to add to the demo."
    )
    asyncio.run(prepare(parser.parse_args().targets))
