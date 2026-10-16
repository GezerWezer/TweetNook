from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = ROOT / "scripts" / "build_pages_demo.py"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "demo-synthetic.json"
PUBLIC_FIXTURE_PATH = ROOT / "demo" / "data.json"
DEMO_CONFIG_PATH = ROOT / "demo" / "config.json"
NODE_TEST = ROOT / "tests" / "js" / "test_demo_api.cjs"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_pages_demo", BUILDER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_synthetic_fixture_is_valid_and_contains_no_raw_payload() -> None:
    builder = _load_builder()
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    builder.validate_fixture(data)

    serialized = FIXTURE_PATH.read_text(encoding="utf-8")
    assert '"raw_json"' not in serialized
    assert len(data["authors"]) == 7
    assert all(author["username"].startswith("demo_") for author in data["authors"])
    owner = next(author for author in data["authors"] if author["id"] == data["owner_user_id"])
    assert owner["display_name"] == "You"
    assert len(data["tweets"]) == 99
    assert all(2 <= len(tweet["tags"]) <= 5 for tweet in data["tweets"])
    assert all(isinstance(tweet["description"], str) for tweet in data["tweets"])
    assert all(bool(tweet["description"]) == bool(tweet["media"]) for tweet in data["tweets"])
    assert all(
        "thumbnail_url" not in media and "poster_url" not in media
        for tweet in data["tweets"]
        for media in tweet["media"]
    )
    community_notes = [
        (tweet["tweet_id"], tweet.get("community_note"))
        for tweet in data["tweets"]
        if tweet.get("community_note")
    ]
    assert community_notes == [
        ("202602130004", "community notes work too!"),
    ]
    replies = [tweet for tweet in data["tweets"] if tweet["reply_to_id"]]
    roots = [tweet for tweet in data["tweets"] if not tweet["reply_to_id"]]
    assert len(roots) == 31
    assert len(replies) == 68
    assert sum("like" in tweet["collections"] for tweet in replies) == 3
    assert any(tweet["text"].startswith("This is something I tweeted.") for tweet in roots)
    saved = [tweet for tweet in data["tweets"] if tweet["collections"]]
    assert len(saved) == 35
    assert all(tweet["collections"] for tweet in saved)
    assert sum(not tweet["media"] for tweet in roots) > sum(bool(tweet["media"]) for tweet in roots)
    assert (
        sum(
            bool(tweet["reply_to_id"])
            and not tweet["media"]
            and not any(
                candidate["reply_to_id"] == tweet["tweet_id"] for candidate in data["tweets"]
            )
            for tweet in data["tweets"]
        )
        >= 10
    )
    guide_text = "\n".join(
        tweet["text"] for tweet in data["tweets"] if "like" in tweet["collections"]
    )
    assert "Welcome to the TweetNook demo page." in guide_text
    assert "Browse like you would on X/Twitter." in guide_text
    assert "Check out Settings for appearance options. 👀" in guide_text
    ordered_likes = sorted(
        (tweet for tweet in data["tweets"] if "like" in tweet["collections"]),
        key=lambda tweet: tweet["liked_order"],
        reverse=True,
    )
    assert [tweet["tweet_id"] for tweet in ordered_likes[:6]] == [
        "202602150001",
        "202602150002",
        "202602150003",
        "202602110010",
        "202602090016",
        "202602150004",
    ]
    quote_index = next(
        index for index, tweet in enumerate(ordered_likes) if tweet["tweet_id"] == "202602120007"
    )
    assert ordered_likes[quote_index + 1]["tweet_id"] == "202601280043"
    browse_guide = next(tweet for tweet in data["tweets"] if tweet["tweet_id"] == "202602150002")
    assert "Check out the demo on mobile, too!" in browse_guide["text"]
    assert all("guide tweet" in tweet["tags"] for tweet in ordered_likes[:3])
    assert len(ordered_likes[3]["media"]) == 2
    assert [media["type"] for media in ordered_likes[4]["media"]] == ["video"]
    assert len(ordered_likes[5]["media"]) == 4
    assert "I said I\u2019m GOOD" in next(
        tweet["description"] for tweet in data["tweets"] if tweet["tweet_id"] == "202602110011"
    )

    visible_media = [media["url"] for tweet in saved for media in tweet["media"]]
    supplied_media = {
        f"demo/media/{path.name}"
        for path in (ROOT / "demo" / "demo media").iterdir()
        if path.is_file()
        and path.name != ".DS_Store"
        and path.suffix.lower() in {".gif", ".jpeg", ".jpg", ".mp4", ".png", ".webm"}
    }
    assert supplied_media <= set(visible_media)
    assert max(visible_media.count(url) for url in supplied_media) <= 2


def test_public_fixture_uses_approved_intro_copy_and_curates_saved_content() -> None:
    original = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    public = json.loads(PUBLIC_FIXTURE_PATH.read_text(encoding="utf-8"))
    guides = [tweet for tweet in public["tweets"] if "guide tweet" in tweet["tags"]]
    assert len({tweet["author_id"] for tweet in guides}) == 1
    assert [tweet["text"] for tweet in guides] == [
        (
            "Welcome to TweetNook. This demo contains real posts, replies, quotes, and "
            "media archived from X using the actual sync process. Explore the same "
            "interface you\u2019d use for your own archive."
        ),
        (
            "Open a post to explore its conversation and quoted posts. Browse Likes and "
            "Bookmarks, or use search and filters to find something specific. Try the "
            "article\u2019s focus mode, too."
        ),
        ("Check out Settings for appearance options. Try the demo on your phone, too."),
    ]

    # Guide identities/order/relationships stay intact; copy is deliberately curated.
    def content(tweet):
        return {
            key: value
            for key, value in tweet.items()
            if key not in {"author_id", "tags", "description", "text"}
        }

    assert [content(tweet) for tweet in guides] == [
        content({**tweet, "engagement": {**tweet["engagement"], "replies": 0}})
        for tweet in original["tweets"]
        if "guide tweet" in tweet["tags"]
    ]
    guide_author = next(
        author for author in public["authors"] if author["id"] == guides[0]["author_id"]
    )
    assert guide_author["avatar_url"] == "demo/media/avatars/default.svg"
    assert guide_author["display_name"] == "TweetNook"
    saved_replies = [tweet for tweet in public["tweets"] if "bookmark" in tweet["collections"]]
    assert {tweet["tweet_id"] for tweet in saved_replies} == {
        "2097587669198934196",
        "2108662185232679271",
        "2107978256003510696",
    }
    assert all(tweet["reply_to_id"] for tweet in saved_replies)
    assert {media["type"] for tweet in saved_replies for media in tweet["media"]} == {
        "photo",
    }
    owner_posts = [tweet for tweet in public["tweets"] if "tweet" in tweet["collections"]]
    assert len(owner_posts) == 1
    assert owner_posts[0]["author_id"] == public["owner_user_id"]
    main = next(tweet for tweet in public["tweets"] if tweet["tweet_id"] == "1794948623257440377")
    assert main["text"].startswith("daily reminder that international orange is goated")
    assert main["collections"] == ["like"]
    assert len(main["media"]) == 3
    assert all(media["url"].endswith(".webp") for media in main["media"])
    replies = [tweet for tweet in public["tweets"] if tweet["reply_to_id"] == main["tweet_id"]]
    assert len(replies) == 23
    assert all("like" not in tweet["collections"] for tweet in replies)
    quote = next(tweet for tweet in public["tweets"] if tweet["tweet_id"] == "2108702633980575995")
    assert quote["text"] == "you just freed her of the curse"
    assert quote["quoted_tweet_id"] == "2108567572597911816"
    quoted = next(
        tweet for tweet in public["tweets"] if tweet["tweet_id"] == quote["quoted_tweet_id"]
    )
    assert len(quoted["media"]) == 3
    assert quoted["collections"] == []
    qr_post = next(
        tweet for tweet in public["tweets"] if tweet["tweet_id"] == "2095945046193234399"
    )
    assert qr_post["text"] == "Don\u2019t trust QR codes. They\u2019re hiding something."
    assert qr_post["media"] == []
    assert qr_post["collections"] == ["like"]
    assert qr_post["community_note"] == "this is true"
    neuroscience = next(
        tweet for tweet in public["tweets"] if tweet["tweet_id"] == "2097653960811094318"
    )
    assert neuroscience["collections"] == ["like"]
    assert neuroscience["quoted_tweet_id"] == "2097527368919470162"
    quoted_video = next(
        tweet for tweet in public["tweets"] if tweet["tweet_id"] == neuroscience["quoted_tweet_id"]
    )
    assert quoted_video["collections"] == []
    assert quoted_video["media"][0]["type"] == "video"
    assert quoted_video["media"][0]["duration_millis"] > 0
    assert any(tweet["reply_to_id"] == quoted_video["tweet_id"] for tweet in public["tweets"])
    practical_shot = next(
        tweet for tweet in public["tweets"] if tweet["tweet_id"] == "2074192621291856126"
    )
    assert practical_shot["collections"] == ["like"]
    assert practical_shot["quoted_tweet_id"] == "2073881116818510069"
    quoted_gif = next(
        tweet
        for tweet in public["tweets"]
        if tweet["tweet_id"] == practical_shot["quoted_tweet_id"]
    )
    assert quoted_gif["collections"] == []
    assert quoted_gif["media"][0]["type"] == "animated_gif"
    assert quoted_gif["media"][0]["duration_millis"] > 0
    for tweet_id, quoted_id in (
        ("2090954221445149180", "2089485925503881494"),
        ("2087274031749927040", "2087170761823781046"),
    ):
        selected = next(tweet for tweet in public["tweets"] if tweet["tweet_id"] == tweet_id)
        assert selected["collections"] == ["like"]
        assert selected["quoted_tweet_id"] == quoted_id
        assert selected["media"][0]["type"] == "photo"
        assert any(tweet["tweet_id"] == quoted_id for tweet in public["tweets"])
    electronics = next(
        tweet for tweet in public["tweets"] if tweet["tweet_id"] == "2012988611570933773"
    )
    assert electronics["collections"] == ["like"]
    assert electronics["media"] == []
    article = electronics["article"]
    assert article["title"] == "i2cjak's authoritative guide to learning electronics"
    assert article["status"] == "body_present"
    assert len(article["content_state"]["blocks"]) == 110
    assert len(article["media"]) == 20
    assert all(item["url"].endswith(".webp") for item in article["media"])
    _load_builder().validate_fixture(public)


def test_static_builder_creates_subpath_safe_public_artifact(tmp_path: Path) -> None:
    builder = _load_builder()
    output = builder.build_pages_demo(
        tmp_path / "pages",
        version="0.0.9",
        base_path="/tweetnook/",
    )

    assert (output / ".nojekyll").is_file()
    assert (output / "404.html").read_text(encoding="utf-8") == (output / "index.html").read_text(
        encoding="utf-8"
    )
    for path in (
        "static/css/styles.css",
        "static/js/themes.js",
        "static/js/autocomplete.js",
        "static/js/app.js",
        "demo/demo-api.js",
        "demo/data.json",
        "demo/media/avatars/1.jpg",
        "demo/media/avatars/default.svg",
    ):
        assert (output / path).is_file()

    published_media = {
        path.relative_to(output / "demo" / "media").as_posix()
        for path in (output / "demo" / "media").rglob("*")
        if path.is_file()
    }
    public = json.loads(PUBLIC_FIXTURE_PATH.read_text())
    assert published_media == {path.as_posix() for path in builder.fixture_media_paths(public)}
    assert "image1.jpg" not in published_media
    published = json.loads((output / "demo/data.json").read_text())
    measured = published["demo_metadata"]
    assert measured["fixture_bytes"] == (output / "demo/data.json").stat().st_size
    assert measured["asset_bytes"] == {
        "demo/media/" + relative: (output / "demo/media" / relative).stat().st_size
        for relative in published_media
    }
    assert len(measured["expanded_thread_ids"]) == 18
    assert any(path.endswith(".mp4") for path in published_media)
    assert any(path.endswith("-poster.webp") for path in published_media)

    html = (output / "index.html").read_text(encoding="utf-8")
    assert '<base href="/tweetnook/">' in html
    assert "window.TWEETNOOK_DEMO = true" in html
    assert 'window.TWEETNOOK_DEMO_VERSION = "0.0.9"' in html
    assert 'window.TWEETNOOK_DEMO_DATA_URL = "demo/data.json?v=0.0.9"' in html
    assert 'window.TWEETNOOK_DEMO_CONFIG_URL = "demo/config.json?v=0.0.9"' in html
    assert json.loads((output / "demo" / "config.json").read_text(encoding="utf-8")) == json.loads(
        DEMO_CONFIG_PATH.read_text(encoding="utf-8")
    )
    assert html.index('src="demo/demo-api.js?v=0.0.9"') < html.index(
        'src="static/js/app.js?v=0.0.9"'
    )
    assert 'src="/static/' not in html
    assert 'href="/static/' not in html
    assert "fetch('/api/setup')" not in html.split('src="demo/demo-api.js?', 1)[0]


@pytest.mark.parametrize("poster", ["https://example.test/poster.jpg", "demo/media/missing.webp"])
def test_fixture_rejects_remote_or_missing_video_posters(poster: str) -> None:
    data = json.loads(PUBLIC_FIXTURE_PATH.read_text())
    media = next(
        item for tweet in data["tweets"] for item in tweet["media"] if item.get("thumbnail_url")
    )
    media["thumbnail_url"] = poster
    with pytest.raises(ValueError, match="thumbnail_url"):
        _load_builder().validate_fixture(data)


def test_public_output_scan_rejects_secrets_and_private_artifacts(tmp_path: Path) -> None:
    builder = _load_builder()
    output = tmp_path / "public"
    output.mkdir()
    (output / "index.html").write_text("auth_token=do-not-publish", encoding="utf-8")

    with pytest.raises(ValueError, match="auth cookie"):
        builder.scan_public_output(output)

    (output / "index.html").write_text("safe", encoding="utf-8")
    (output / "archive.sqlite").write_bytes(b"")
    with pytest.raises(ValueError, match="forbidden public file"):
        builder.scan_public_output(output)


def test_demo_javascript_adapter_suite() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the deterministic demo API harness")

    result = subprocess.run(
        [node, str(NODE_TEST)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "demo API tests passed" in result.stdout


def test_production_frontend_only_exposes_demo_behavior_behind_explicit_flag() -> None:
    html = (ROOT / "tweetnook" / "web" / "index.html").read_text(encoding="utf-8")
    app = (ROOT / "tweetnook" / "web" / "static" / "js" / "app.js").read_text(encoding="utf-8")

    assert "window.TWEETNOOK_DEMO = true" not in html
    assert "window.TWEETNOOK_DEMO === true" in app
    assert '<span x-show="isDemo" class="nook-demo-badge">Demo</span>' in html
    assert ':disabled="isDemo">Run setup again</button>' in html
    # Setup needs a local installation; Configuration and Activity logs are served by the adapter.
    assert "if (!this.isDemo) this.fetchSetup()" in app
    assert "if (!this.isDemo) this.fetchConfig()" not in app
    assert "if (!this.isDemo) this.fetchActivityRuns()" not in app


def test_demo_config_matches_the_server_schema_and_defaults() -> None:
    pytest.importorskip("pydantic")
    builder = _load_builder()
    expected = json.loads(json.dumps(builder.demo_config_payload()))

    assert json.loads(DEMO_CONFIG_PATH.read_text(encoding="utf-8")) == expected, (
        "demo/config.json is out of date; run "
        "`uv run python scripts/build_pages_demo.py --refresh-config`"
    )


def test_demo_tagging_and_reply_review_match_the_published_scope() -> None:
    data = json.loads(PUBLIC_FIXTURE_PATH.read_text())
    review = json.loads((ROOT / "demo/reply-review.json").read_text())
    excluded = set(review["excluded"])
    assert sum(review["conversations"].values()) == review["reviewed_reply_count"] == 596
    assert len(excluded) == 90
    assert excluded.isdisjoint(tweet["tweet_id"] for tweet in data["tweets"])
    assert sum(bool(tweet["reply_to_id"]) for tweet in data["tweets"]) == 344
    assert data["demo_metadata"]["reply_policy"]["max_hops"] == 1
    follow_up = review["follow_up_review"]
    reviewed_replies = set(follow_up["reviewed_reply_ids"])
    newly_excluded = set(follow_up["newly_excluded_reply_ids"])
    assert len(reviewed_replies) == 379 and len(newly_excluded) == 25
    requested_removals = review["user_requested_removals"]
    user_excluded = {
        tweet_id for removal in requested_removals for tweet_id in removal["excluded_reply_ids"]
    }
    user_pruned = {
        tweet_id for removal in requested_removals for tweet_id in removal["pruned_context_ids"]
    }
    assert all(author["username"].lower() != "almskxld" for author in data["authors"])
    assert reviewed_replies - newly_excluded - user_excluded - user_pruned == {
        tweet["tweet_id"] for tweet in data["tweets"] if tweet["reply_to_id"]
    }
    assert set(follow_up["reviewed_context_root_ids"]) - set(
        follow_up["orphaned_context_ids"]
    ) - user_pruned == {
        tweet["tweet_id"]
        for tweet in data["tweets"]
        if not tweet["reply_to_id"] and not tweet["collections"]
    }
    # The missed promotional reply and its branded quote must both stay unpublished.
    assert "2097683428334252331" in newly_excluded
    assert follow_up["orphaned_context_ids"] == ["2097670044427706495"]
    assert "2097670044427706495" not in {tweet["tweet_id"] for tweet in data["tweets"]}
    assert all("2097670044427706495/" not in url for url in data["demo_metadata"]["asset_bytes"])
    tagged = data["demo_metadata"]["tagging"]
    assert set(tagged["tagged_tweet_ids"]) == {
        tweet["tweet_id"] for tweet in data["tweets"] if "like" in tweet["collections"]
    }
    assert tagged["model"] == "gemini-3.6-flash"
    assert tagged["processing_tier"] == "batch" and tagged["google_search_enabled"]
    for tweet in data["tweets"]:
        if "like" in tweet["collections"]:
            assert 2 <= len(set(tweet["tags"]) - {"guide tweet"}) <= 5
        elif "bookmark" in tweet["collections"]:
            assert tweet["tags"] == [] and tweet["description"] == ""
    accounting = tagged["accounting"]
    assert len(tagged["jobs"]) == 3
    assert sum(job["request_count"] for job in tagged["jobs"]) == accounting["requests"] == 14
    assert accounting["total_tokens"] == 61190
    assert accounting["search_cost_usd"] is None
    assert accounting["actual_search_call_count"] is None
    assert accounting["cost_is_estimate_not_invoice"]
