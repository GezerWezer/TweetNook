#!/usr/bin/env python3
"""Build the static TweetNook GitHub Pages demo without a database or secrets."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "tweetnook" / "web"
DEMO_ROOT = ROOT / "demo"
DEMO_MEDIA_ROOT = DEMO_ROOT / "demo media"

ALLOWED_COLLECTIONS = {"bookmark", "like", "tweet"}
ALLOWED_MEDIA = {"photo", "video", "animated_gif"}
REQUIRED_TWEET_FIELDS = {
    "tweet_id",
    "text",
    "author_id",
    "created_at",
    "synced_at",
    "collections",
    "liked_order",
    "engagement",
    "hashtags",
    "mentions",
    "urls",
    "media",
    "source",
    "card_name",
    "conversation_id",
    "reply_to_id",
    "reply_to_username",
    "quoted_tweet_id",
    "tags",
    "description",
}
SECRET_PATTERNS = {
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "Twitter/X auth cookie": re.compile(r"(?:^|[\s\"'])auth_token\s*[:=]", re.IGNORECASE),
    "Twitter/X CSRF cookie": re.compile(r"(?:^|[\s\"'])ct0\s*[:=]", re.IGNORECASE),
    "Gemini key": re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    "local home path": re.compile(r"/(?:Users|home)/[^/\s\"']+/"),
}
FORBIDDEN_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".zip", ".toml", ".env"}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate_fixture(data: dict[str, Any]) -> None:
    """Reject incomplete, relationally invalid, or unexpectedly raw demo fixtures."""
    _require(data.get("schema_version") == 2, "demo fixture schema_version must be 2")
    authors = data.get("authors")
    tweets = data.get("tweets")
    _require(isinstance(authors, list) and len(authors) == 7, "demo fixture must contain 7 authors")
    _require(isinstance(tweets, list) and tweets, "demo fixture must contain tweets")
    _require(12 <= len(tweets) <= 96, "demo fixture must contain between 12 and 96 tweets")

    def require_demo_asset(value: Any, label: str) -> None:
        asset_url = str(value or "")
        _require(asset_url.startswith("demo/media/"), f"{label} must be a demo/media asset")
        relative = Path(asset_url.removeprefix("demo/media/"))
        _require(
            relative.parts and ".." not in relative.parts and not relative.is_absolute(),
            f"{label} contains an unsafe path",
        )
        _require((DEMO_MEDIA_ROOT / relative).is_file(), f"{label} does not exist: {asset_url}")

    author_ids: set[str] = set()
    for index, author in enumerate(authors):
        _require(isinstance(author, dict), f"author {index} must be an object")
        missing = {"id", "username", "display_name", "verified", "avatar_url"} - set(author)
        _require(not missing, f"author {index} is missing fields: {sorted(missing)}")
        author_id = str(author["id"])
        _require(author_id not in author_ids, f"duplicate author ID: {author_id}")
        author_ids.add(author_id)
        _require(
            str(author["username"]).startswith("demo_"),
            "placeholder usernames must start with demo_",
        )
        require_demo_asset(author["avatar_url"], f"author {author_id} avatar")

    tweet_ids: set[str] = set()
    for index, tweet in enumerate(tweets):
        _require(isinstance(tweet, dict), f"tweet {index} must be an object")
        missing = REQUIRED_TWEET_FIELDS - set(tweet)
        _require(not missing, f"tweet {index} is missing fields: {sorted(missing)}")
        _require("raw_json" not in tweet, f"tweet {index} must not contain raw_json")
        tweet_id = str(tweet["tweet_id"])
        _require(tweet_id.isdigit(), f"tweet ID must be numeric: {tweet_id}")
        _require(tweet_id not in tweet_ids, f"duplicate tweet ID: {tweet_id}")
        tweet_ids.add(tweet_id)
        _require(
            str(tweet["author_id"]) in author_ids, f"tweet {tweet_id} references an unknown author"
        )
        collections = tweet["collections"]
        _require(isinstance(collections, list), f"tweet {tweet_id} collections must be an array")
        _require(
            set(collections) <= ALLOWED_COLLECTIONS, f"tweet {tweet_id} has an invalid collection"
        )
        tags = tweet["tags"]
        _require(isinstance(tags, list), f"tweet {tweet_id} tags must be an array")
        _require(2 <= len(tags) <= 5, f"tweet {tweet_id} must contain 2 to 5 tags")
        _require(
            all(isinstance(tag, str) and tag.strip() for tag in tags),
            f"tweet {tweet_id} tags must be non-empty strings",
        )
        _require(len(set(tags)) == len(tags), f"tweet {tweet_id} tags must be unique")
        community_note = tweet.get("community_note")
        _require(
            community_note is None
            or (isinstance(community_note, str) and bool(community_note.strip())),
            f"tweet {tweet_id} community note must be a non-empty string",
        )
        _require(
            isinstance(tweet["description"], str),
            f"tweet {tweet_id} description must be a string",
        )
        _require(isinstance(tweet["media"], list), f"tweet {tweet_id} media must be an array")
        for media in tweet["media"]:
            _require(media.get("type") in ALLOWED_MEDIA, f"tweet {tweet_id} has invalid media type")
            require_demo_asset(media.get("url"), f"tweet {tweet_id} media URL")
            _require(
                "thumbnail_url" not in media and "poster_url" not in media,
                f"tweet {tweet_id} must not invent a poster or thumbnail",
            )
        if tweet["media"]:
            _require(
                bool(tweet["description"].strip()),
                f"media tweet {tweet_id} must contain a description",
            )
        else:
            _require(
                tweet["description"] == "",
                f"text-only tweet {tweet_id} must not contain a description",
            )

    for tweet in tweets:
        tweet_id = str(tweet["tweet_id"])
        for field in ("reply_to_id", "quoted_tweet_id"):
            related_id = tweet.get(field)
            _require(
                related_id is None or str(related_id) in tweet_ids,
                f"tweet {tweet_id} has unresolved {field}: {related_id}",
            )

    roots = [tweet for tweet in tweets if tweet.get("reply_to_id") is None]
    chained_root_ids: set[str] = set()
    for root in roots:
        root_id = str(root["tweet_id"])
        direct_replies = [
            tweet for tweet in tweets if str(tweet.get("reply_to_id") or "") == root_id
        ]
        _require(direct_replies, f"root tweet {root_id} must have a reply")
        root_author_id = str(root["author_id"])
        if any(
            str(candidate.get("reply_to_id") or "") == str(reply["tweet_id"])
            and str(candidate["author_id"]) == root_author_id
            for reply in direct_replies
            for candidate in tweets
        ):
            chained_root_ids.add(root_id)

    _require(chained_root_ids, "demo fixture must contain original-poster reply chains")
    _require(
        len(chained_root_ids) < len(roots),
        "demo fixture must also contain direct replies without an original-poster follow-up",
    )

    liked_replies = [
        tweet for tweet in tweets if tweet.get("reply_to_id") and "like" in tweet["collections"]
    ]
    _require(1 <= len(liked_replies) <= 3, "demo fixture must contain only a few liked replies")

    liked_tweets = [tweet for tweet in tweets if "like" in tweet["collections"]]
    for liked_tweet in liked_tweets:
        liked_id = str(liked_tweet["tweet_id"])
        direct_replies = [
            tweet for tweet in tweets if str(tweet.get("reply_to_id") or "") == liked_id
        ]
        _require(
            any(not reply["media"] and reply["quoted_tweet_id"] for reply in direct_replies),
            f"liked tweet {liked_id} must have a text-only quote reply",
        )
        _require(
            any(not reply["media"] and not reply["quoted_tweet_id"] for reply in direct_replies),
            f"liked tweet {liked_id} must have a plain-text reply",
        )

    ordered_likes = sorted(liked_tweets, key=lambda tweet: int(tweet["liked_order"]), reverse=True)
    _require(len(ordered_likes) >= 6, "demo fixture must contain at least six liked tweets")
    _require(
        all("guide tweet" in tweet["tags"] for tweet in ordered_likes[:3]),
        "the first three liked tweets must carry the guide tweet tag",
    )
    _require(
        len(ordered_likes[3]["media"]) == 2
        and all(media["type"] == "photo" for media in ordered_likes[3]["media"]),
        "the fourth liked tweet must be a two-image gallery",
    )
    _require(
        len(ordered_likes[4]["media"]) == 1 and ordered_likes[4]["media"][0]["type"] == "video",
        "the fifth liked tweet must be a standalone video",
    )
    _require(
        len(ordered_likes[5]["media"]) == 4
        and all(media["type"] == "photo" for media in ordered_likes[5]["media"]),
        "the sixth liked tweet must be a four-image gallery",
    )

    visible_media_urls = [
        media["url"] for tweet in tweets if tweet["collections"] for media in tweet["media"]
    ]
    source_media_urls = {
        f"demo/media/{path.name}"
        for path in DEMO_MEDIA_ROOT.iterdir()
        if path.is_file()
        and path.name != ".DS_Store"
        and path.suffix.lower() in {".gif", ".jpeg", ".jpg", ".mp4", ".png", ".webm"}
    }
    _require(
        source_media_urls <= set(visible_media_urls),
        "every supplied non-avatar media asset must appear in a feed-visible tweet",
    )
    _require(
        all(visible_media_urls.count(url) <= 2 for url in source_media_urls),
        "feed-visible media assets must not be reused more than twice",
    )

    guide_fragments = {
        "Welcome to the TweetNook demo page.",
        "Browse like you would on X/Twitter.",
        "Check out Settings for appearance options.",
    }
    _require(
        all(
            any(fragment in tweet["text"] and "like" in tweet["collections"] for tweet in tweets)
            for fragment in guide_fragments
        ),
        "demo fixture must contain all three liked guide posts",
    )

    tweets_by_id = {str(tweet["tweet_id"]): tweet for tweet in tweets}
    feature_checks = {
        "image": any(any(media["type"] == "photo" for media in tweet["media"]) for tweet in tweets),
        "gallery": any(len(tweet["media"]) >= 2 for tweet in tweets),
        "gif": any(
            any(media["type"] == "animated_gif" for media in tweet["media"]) for tweet in tweets
        ),
        "video": any(any(media["type"] == "video" for media in tweet["media"]) for tweet in tweets),
        "quote": any(tweet["quoted_tweet_id"] for tweet in tweets),
        "text quoting media": any(
            tweet["quoted_tweet_id"]
            and not tweet["media"]
            and tweets_by_id[str(tweet["quoted_tweet_id"])]["media"]
            for tweet in tweets
        ),
        "media quoting text": any(
            tweet["quoted_tweet_id"]
            and tweet["media"]
            and not tweets_by_id[str(tweet["quoted_tweet_id"])]["media"]
            for tweet in tweets
        ),
        "plain non-chain reply": any(
            tweet["reply_to_id"]
            and not tweet["media"]
            and not any(candidate.get("reply_to_id") == tweet["tweet_id"] for candidate in tweets)
            for tweet in tweets
        ),
        "reply/OP chain": any(
            tweet.get("reply_to_id")
            and str(tweet["author_id"])
            == str(
                next(
                    (
                        parent["author_id"]
                        for parent in tweets
                        if parent["tweet_id"] == tweet["conversation_id"]
                    ),
                    "",
                )
            )
            for tweet in tweets
        ),
        "like": any("like" in tweet["collections"] for tweet in tweets),
        "bookmark": any("bookmark" in tweet["collections"] for tweet in tweets),
        "owner tweet": any(
            str(tweet["author_id"]) == str(data.get("owner_user_id")) for tweet in tweets
        ),
        "verified author": any(author["verified"] for author in authors),
        "community note": any(tweet.get("community_note") for tweet in tweets),
    }
    missing_features = [name for name, present in feature_checks.items() if not present]
    _require(not missing_features, f"demo fixture does not exercise: {', '.join(missing_features)}")


def normalize_base_path(value: str) -> str:
    value = value.strip()
    _require(value.startswith("/"), "base path must begin with /")
    normalized = "/" + value.strip("/")
    return "/" if normalized == "/" else normalized + "/"


def render_index(source: str, *, base_path: str, version: str) -> str:
    asset_version = quote(version, safe="")
    bootstrap = (
        f'    <base href="{base_path}">\n'
        f"    <script>window.TWEETNOOK_DEMO = true; "
        f"window.TWEETNOOK_DEMO_BASE = {json.dumps(base_path)}; "
        f"window.TWEETNOOK_DEMO_VERSION = {json.dumps(version)}; "
        f"window.TWEETNOOK_DEMO_DATA_URL = "
        f"{json.dumps(f'demo/data.json?v={asset_version}')};</script>\n"
        '    <script src="demo/demo-api.js"></script>\n'
    )
    _require("<head>" in source, "production index is missing <head>")
    _require(
        '<script src="/static/js/app.js"></script>' in source, "production app script was not found"
    )
    rendered = source.replace("<head>\n", "<head>\n" + bootstrap, 1)
    rendered = rendered.replace('href="/static/', 'href="static/')
    rendered = rendered.replace('src="/static/', 'src="static/')
    rendered = re.sub(
        r'((?:href|src)="(?:static|demo)/[^"?]+)(")',
        rf"\1?v={asset_version}\2",
        rendered,
    )
    _require(
        rendered.index("demo/demo-api.js") < rendered.index("static/js/app.js"),
        "demo adapter must load before app.js",
    )
    return rendered


def scan_public_output(output: Path) -> None:
    for path in output.rglob("*"):
        if not path.is_file():
            continue
        lowered = path.name.lower()
        _require(
            path.suffix.lower() not in FORBIDDEN_SUFFIXES, f"forbidden public file: {path.name}"
        )
        _require(
            "session" not in lowered and "cookie" not in lowered,
            f"credential-like public filename: {path.name}",
        )
        if path.suffix.lower() not in {".html", ".js", ".css", ".json", ".md", ""}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for label, pattern in SECRET_PATTERNS.items():
            _require(
                pattern.search(text) is None,
                f"{label} found in public output: {path.relative_to(output)}",
            )


def build_pages_demo(output: Path, *, version: str, base_path: str) -> Path:
    fixture = json.loads((DEMO_ROOT / "data.json").read_text(encoding="utf-8"))
    validate_fixture(fixture)
    normalized_base = normalize_base_path(base_path)

    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    shutil.copytree(WEB_ROOT / "static", output / "static")
    (output / "demo").mkdir()
    shutil.copy2(DEMO_ROOT / "data.json", output / "demo" / "data.json")
    shutil.copy2(DEMO_ROOT / "demo-api.js", output / "demo" / "demo-api.js")
    shutil.copytree(
        DEMO_MEDIA_ROOT,
        output / "demo" / "media",
        ignore=shutil.ignore_patterns(".DS_Store"),
    )

    source = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    rendered = render_index(source, base_path=normalized_base, version=version)
    (output / "index.html").write_text(rendered, encoding="utf-8")
    (output / "404.html").write_text(rendered, encoding="utf-8")
    (output / ".nojekyll").touch()

    expected = [
        output / "index.html",
        output / "404.html",
        output / ".nojekyll",
        output / "static" / "css" / "styles.css",
        output / "static" / "js" / "app.js",
        output / "demo" / "data.json",
        output / "demo" / "demo-api.js",
    ]
    expected.extend(
        output / "demo" / "media" / path.relative_to(DEMO_MEDIA_ROOT)
        for path in DEMO_MEDIA_ROOT.rglob("*")
        if path.is_file() and path.name != ".DS_Store"
    )
    missing = [path.relative_to(output) for path in expected if not path.exists()]
    _require(not missing, f"Pages build is missing: {', '.join(map(str, missing))}")
    scan_public_output(output)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "_dist" / "pages")
    parser.add_argument("--version", default="development")
    parser.add_argument("--base-path", default="/tweetnook/")
    args = parser.parse_args()
    built = build_pages_demo(args.output.resolve(), version=args.version, base_path=args.base_path)
    print(f"Built TweetNook Pages demo at {built}")


if __name__ == "__main__":
    main()
