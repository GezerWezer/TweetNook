from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = ROOT / "scripts" / "build_pages_demo.py"
FIXTURE_PATH = ROOT / "demo" / "data.json"
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
    assert len(data["tweets"]) == 78
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
    assert community_notes == [("202602130004", "community notes work too!")]
    replies = [tweet for tweet in data["tweets"] if tweet["reply_to_id"]]
    roots = [tweet for tweet in data["tweets"] if not tweet["reply_to_id"]]
    assert len(roots) == 22
    assert len(replies) == 56
    assert sum("like" in tweet["collections"] for tweet in replies) == 3
    assert any(tweet["text"].startswith("This is something I tweeted.") for tweet in roots)
    saved = [tweet for tweet in data["tweets"] if tweet["collections"]]
    assert len(saved) == 26
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
        "demo/media/image1.jpg",
        "demo/media/image2.jpg",
        "demo/media/image3.jpg",
        "demo/media/image4.jpeg",
        "demo/media/image5.jpeg",
        "demo/media/image6.jpeg",
        "demo/media/gif1.mp4",
        "demo/media/gif2.mp4",
        "demo/media/video1.mp4",
        "demo/media/video2.mp4",
        "demo/media/video3.mp4",
        "demo/media/avatars/1.jpg",
        "demo/media/avatars/7.png",
    ):
        assert (output / path).is_file()

    html = (output / "index.html").read_text(encoding="utf-8")
    assert '<base href="/tweetnook/">' in html
    assert "window.TWEETNOOK_DEMO = true" in html
    assert 'window.TWEETNOOK_DEMO_VERSION = "0.0.9"' in html
    assert 'window.TWEETNOOK_DEMO_DATA_URL = "demo/data.json?v=0.0.9"' in html
    assert html.index('src="demo/demo-api.js?v=0.0.9"') < html.index(
        'src="static/js/app.js?v=0.0.9"'
    )
    assert 'src="/static/' not in html
    assert 'href="/static/' not in html
    assert "fetch('/api/setup')" not in html.split('src="demo/demo-api.js?', 1)[0]


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
    assert 'x-show="isDemo"' in html
    assert 'text-[var(--accent-color)]">DEMO</span>' in html
    assert "This demo uses synthetic, in-memory data." in html
    assert "Setup, Configuration, and Logs are hidden" in html
    assert html.count('x-show="!isDemo"') >= 6
    assert "if (!this.isDemo) this.fetchSetup()" in app
    assert "if (!this.isDemo) this.fetchConfig()" in app
    assert "if (!this.isDemo) this.fetchActivityRuns()" in app
