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
DEMO_CONFIG = DEMO_ROOT / "config.json"

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
    _require(isinstance(authors, list) and bool(authors), "demo fixture must contain authors")
    _require(isinstance(tweets, list) and tweets, "demo fixture must contain tweets")

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
        _require(
            all(isinstance(tag, str) and tag.strip() for tag in tags),
            f"tweet {tweet_id} tags must be non-empty strings",
        )
        _require(len(set(tags)) == len(tags), f"tweet {tweet_id} tags must be unique")
        article = tweet.get("article")
        if article is not None:
            _require(isinstance(article, dict), f"tweet {tweet_id} article must be an object")
            _require(bool(article.get("title")), f"tweet {tweet_id} article needs a title")
            for item in article.get("media", []):
                require_demo_asset(item.get("url"), f"tweet {tweet_id} article media")
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
            for field in ("thumbnail_url", "poster_url"):
                if media.get(field):
                    require_demo_asset(media[field], f"tweet {tweet_id} {field}")

    for tweet in tweets:
        tweet_id = str(tweet["tweet_id"])
        for field in ("reply_to_id", "quoted_tweet_id"):
            related_id = tweet.get(field)
            _require(
                related_id is None or str(related_id) in tweet_ids,
                f"tweet {tweet_id} has unresolved {field}: {related_id}",
            )

    guide_ids = {"202602150001", "202602150002", "202602150003"}
    _require(
        {
            tweet["tweet_id"]
            for tweet in tweets
            if "guide tweet" in tweet["tags"] and "like" in tweet["collections"]
        }
        == guide_ids,
        "demo fixture must contain all three liked guide posts",
    )

    ordered_likes = sorted(
        (tweet for tweet in tweets if "like" in tweet["collections"]),
        key=lambda tweet: int(tweet["liked_order"]),
        reverse=True,
    )
    _require(
        all("guide tweet" in tweet["tags"] for tweet in ordered_likes[:3]),
        "the first three liked tweets must carry the guide tweet tag",
    )


def fixture_media_paths(data: dict[str, Any]) -> set[Path]:
    """Publish only media used by the current fixture, including article imagery."""
    urls = {author["avatar_url"] for author in data["authors"]}
    for tweet in data["tweets"]:
        urls.update(media["url"] for media in tweet["media"])
        urls.update(
            media[field]
            for media in tweet["media"]
            for field in ("thumbnail_url", "poster_url")
            if media.get(field)
        )
        urls.update(media["url"] for media in (tweet.get("article") or {}).get("media", []))
    return {Path(url.removeprefix("demo/media/")) for url in urls}


def with_demo_metrics(data: dict[str, Any]) -> dict[str, Any]:
    """Measure the published JSON/media, not a fictional SQLite database."""
    data = json.loads(json.dumps(data))
    metadata = data.setdefault("demo_metadata", {})
    metadata["asset_bytes"] = {
        "demo/media/" + path.as_posix(): (DEMO_MEDIA_ROOT / path).stat().st_size
        for path in sorted(fixture_media_paths(data))
    }
    metadata["fixture_bytes"] = 0
    for _ in range(8):
        size = len((json.dumps(data, indent=2, ensure_ascii=True) + "\n").encode())
        if metadata["fixture_bytes"] == size:
            break
        metadata["fixture_bytes"] = size
    return data


def demo_config_payload() -> dict[str, Any]:
    """The Configuration page's schema and defaults, as the local server would serve them.

    The Pages build runs without project dependencies, so this is generated ahead of time into
    demo/config.json (``--refresh-config``); a test keeps the file in step with tweetnook/config.py.
    """
    from tweetnook.config import AppConfig, get_config_ui_schema

    return {"schema": get_config_ui_schema(), "defaults": AppConfig().model_dump(mode="json")}


def write_demo_config(path: Path = DEMO_CONFIG) -> Path:
    path.write_text(json.dumps(demo_config_payload(), indent=2) + "\n", encoding="utf-8")
    return path


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
        f"{json.dumps(f'demo/data.json?v={asset_version}')}; "
        f"window.TWEETNOOK_DEMO_CONFIG_URL = "
        f"{json.dumps(f'demo/config.json?v={asset_version}')};</script>\n"
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
    fixture = with_demo_metrics(fixture)
    normalized_base = normalize_base_path(base_path)

    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    shutil.copytree(WEB_ROOT / "static", output / "static")
    (output / "demo").mkdir()
    (output / "demo" / "data.json").write_text(
        json.dumps(fixture, indent=2, ensure_ascii=True) + "\n"
    )
    shutil.copy2(DEMO_CONFIG, output / "demo" / "config.json")
    shutil.copy2(DEMO_ROOT / "demo-api.js", output / "demo" / "demo-api.js")
    media_paths = fixture_media_paths(fixture)
    for relative in sorted(media_paths):
        target = output / "demo" / "media" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(DEMO_MEDIA_ROOT / relative, target)

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
        output / "demo" / "config.json",
        output / "demo" / "demo-api.js",
    ]
    expected.extend(output / "demo" / "media" / relative for relative in media_paths)
    missing = [path.relative_to(output) for path in expected if not path.exists()]
    _require(not missing, f"Pages build is missing: {', '.join(map(str, missing))}")
    scan_public_output(output)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "_dist" / "pages")
    parser.add_argument("--version", default="development")
    parser.add_argument("--base-path", default="/tweetnook/")
    parser.add_argument(
        "--refresh-config",
        action="store_true",
        help="Regenerate demo/config.json from tweetnook/config.py (needs the project installed).",
    )
    args = parser.parse_args()
    if args.refresh_config:
        print(f"Wrote {write_demo_config()}")
        return
    built = build_pages_demo(args.output.resolve(), version=args.version, base_path=args.base_path)
    print(f"Built TweetNook Pages demo at {built}")


if __name__ == "__main__":
    main()
