from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

from tests.conftest import make_article_result, make_tweet_detail_response, make_tweet_result
from tweetnook.client.timelines import parse_tweet_detail_tweets
from tweetnook.storage import ArchiveStore

ROOT = Path(__file__).resolve().parents[1]


def _preparer():
    spec = importlib.util.spec_from_file_location(
        "prepare_demo_content", ROOT / "scripts" / "prepare_demo_content.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_projection_keeps_context_out_of_collections_and_raw_payloads_private(
    tmp_path, monkeypatch
):
    module = _preparer()
    guides = json.loads((ROOT / "tests" / "fixtures" / "demo-synthetic.json").read_text())
    guides["tweets"] = [tweet for tweet in guides["tweets"] if "guide tweet" in tweet["tags"]]
    before = deepcopy(guides)
    root = make_tweet_result("100", "Selected post", user_id="123")
    root["private_marker"] = "must stay private"
    reply = make_tweet_result("101", "Context only", user_id="123", in_reply_to_status_id="100")
    unrelated = make_tweet_result("102", "Unrelated promoted post", user_id="999")
    nested = make_tweet_result("103", "Nested context", user_id="999", in_reply_to_status_id="101")
    for captured in (root, reply):
        captured["core"]["user_results"]["result"]["legacy"].update(
            description="Captured public bio", followers_count=42, friends_count=7
        )
    reply["legacy"]["display_text_range"] = [0, len(reply["legacy"]["full_text"])]
    payload = make_tweet_detail_response([root, reply, unrelated, nested])
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    try:
        store.persist_thread_detail(
            focal_tweet_id="100",
            tweets=parse_tweet_detail_tweets(payload),
            raw_json=payload,
        )
        monkeypatch.setattr(
            module,
            "compress_image",
            lambda *args, **kwargs: ("demo/media/real/avatar.webp", 128, 128),
        )
        projected = module.project_archive(store, guides, ["100"], {"123": tmp_path / "avatar.jpg"})
        tweets = {tweet["tweet_id"]: tweet for tweet in projected["tweets"]}
        assert tweets["100"]["collections"] == ["like"]
        assert tweets["101"]["collections"] == []
        assert tweets["101"]["reply_to_id"] == "100"
        assert tweets["101"]["display_text_range"] == reply["legacy"]["display_text_range"]
        assert "102" not in tweets
        assert "103" not in tweets
        assert "private_marker" not in json.dumps(projected)
        assert "raw_json" not in json.dumps(projected)
        assert guides == before
        assert len(projected["tweets"]) == 5
        author = next(author for author in projected["authors"] if author["id"] == "123")
        assert author["description"] == "Captured public bio"
        assert len(author["profile_snapshots"]) == 1
        snapshot = author["profile_snapshots"][0]
        assert snapshot["description"] == "Captured public bio"
        assert snapshot["followers_count"] == 42 and snapshot["following_count"] == 7
        assert snapshot["fetched_at"]
        assert set(snapshot) == {
            "display_name",
            "username",
            "description",
            "followers_count",
            "following_count",
            "fetched_at",
        }
        original_context_rows = module.context_rows
        monkeypatch.setattr(
            module,
            "context_rows",
            lambda *args: list(reversed(original_context_rows(*args))),
        )
        repeated = module.project_archive(
            store, projected, ["100"], {"123": tmp_path / "avatar.jpg"}
        )
        assert repeated["tweets"] == projected["tweets"]
        assert repeated["authors"] == projected["authors"]
        # Demo-only saved replies, notes and the owner post survive later imports.
        tweets["101"]["collections"] = ["bookmark"]
        tweets["100"]["community_note"] = "Demo context"
        tweets["100"]["liked_order"] = 96
        tweets["100"]["tags"] = ["RealTopic", "SpecificSubject"]
        tweets["100"]["description"] = "Generated media description"
        owner = next(
            tweet
            for tweet in json.loads((ROOT / "tests/fixtures/demo-synthetic.json").read_text())[
                "tweets"
            ]
            if tweet["tweet_id"] == "202602140001"
        )
        projected["tweets"].insert(3, owner)
        curated = module.project_archive(
            store,
            projected,
            ["100"],
            {"123": tmp_path / "avatar.jpg", "999": tmp_path / "nested-avatar.jpg"},
        )
        by_id = {tweet["tweet_id"]: tweet for tweet in curated["tweets"]}
        assert by_id["101"]["collections"] == ["bookmark"]
        assert by_id["103"]["reply_to_id"] == "101"
        assert by_id["100"]["community_note"] == "Demo context"
        assert by_id["100"]["liked_order"] == 96
        assert by_id["100"]["tags"] == ["RealTopic", "SpecificSubject"]
        assert by_id["100"]["description"] == "Generated media description"
        assert by_id[owner["tweet_id"]] == owner

    finally:
        store.close()


def test_article_projection_preserves_body_and_maps_images_without_private_payloads(
    tmp_path, monkeypatch
):
    module = _preparer()
    guides = json.loads((ROOT / "tests" / "fixtures" / "demo-synthetic.json").read_text())
    article = make_article_result(
        "article-1", title="Real article", preview_text="Preview", plain_text="Body"
    )
    article["private_marker"] = "not public"
    article["content_state"] = {"blocks": [{"type": "unstyled", "text": "Body"}], "entityMap": []}
    article["media_entities"] = [
        {
            "media_key": "3_55",
            "media_id": "55",
            "alt_text": "Circuit diagram",
            "media_info": {"original_img_url": "https://pbs.twimg.com/body.png"},
        }
    ]
    raw = make_tweet_result("100", "Article post", user_id="123", article=article)
    payload = make_tweet_detail_response([raw])
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    try:
        store.persist_thread_detail(
            focal_tweet_id="100", tweets=parse_tweet_detail_tweets(payload), raw_json=payload
        )
        store.conn.execute(
            "UPDATE archive SET local_path = 'media/captured.png' WHERE record_type = 'media'"
        )
        store.conn.commit()
        monkeypatch.setattr(
            module,
            "compress_image",
            lambda source, relative, **kwargs: ("demo/media/" + str(relative), 128, 128),
        )
        projected = module.project_archive(store, guides, ["100"], {"123": tmp_path / "avatar.jpg"})
        tweet = next(t for t in projected["tweets"] if t["tweet_id"] == "100")
        assert tweet["media"] == []
        result = tweet["article"]
        assert result["title"] == "Real article"
        assert result["status"] == "body_present"
        assert result["content_text"] == "Body"
        assert result["content_state"] == article["content_state"]
        assert [m["source"] for m in result["media"]] == ["article_cover", "article_media"]
        assert result["media"][1]["media_id"] == "55"
        assert result["media"][1]["alt_text"] == "Circuit diagram"
        assert all(m["url"].startswith("demo/media/real/100/") for m in result["media"])
        serialized = json.dumps(projected)
        assert "private_marker" not in serialized
        assert "raw_json" not in serialized
        assert "local_path" not in serialized
        assert "pbs.twimg.com" not in serialized
    finally:
        store.close()


def test_projection_rejects_unresolved_context(tmp_path, monkeypatch):
    module = _preparer()
    guides = json.loads((ROOT / "demo" / "data.json").read_text())
    raw = make_tweet_result("100", "Missing parent", user_id="123", in_reply_to_status_id="999")
    payload = make_tweet_detail_response([raw])
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    try:
        store.persist_thread_detail(
            focal_tweet_id="100",
            tweets=parse_tweet_detail_tweets(payload),
            raw_json=payload,
        )
        monkeypatch.setattr(
            module,
            "compress_image",
            lambda *args, **kwargs: ("demo/media/real/avatar.webp", 128, 128),
        )
        with pytest.raises(ValueError, match="Fetch missing reply_to_id 999"):
            module.project_archive(store, guides, ["100"], {"123": tmp_path / "avatar.jpg"})
        assert module.missing_context_targets(store, ["100"]) == ["999"]
    finally:
        store.close()


def test_basic_reply_trimming_preserves_attachments_and_conversation_anchors():
    module = _preparer()

    def reply(tid, text="@someone", **extra):
        return {"tweet_id": tid, "text": text, "reply_to_id": "root", **extra}

    tweets = [
        {"tweet_id": "root", "text": "Original"},
        reply("cut", "@one @two!"),
        reply("text", "@someone real"),
        reply("emoji", "@someone 👀"),
        reply("media", media=[{"type": "photo"}]),
        reply("url", urls=[{"expanded_url": "https://example.test/"}]),
        reply("saved", collections=["like"]),
        reply("quoted", quoted_tweet_id="cut"),
        reply("parent"),
        reply("continuation", "Worth keeping", reply_to_id="parent"),
        reply("drop-parent"),
        reply("drop-child", reply_to_id="drop-parent"),
    ]
    kept = {tweet["tweet_id"] for tweet in module.trim_basic_replies(tweets)}
    assert kept == {
        "root",
        "cut",
        "text",
        "emoji",
        "media",
        "url",
        "saved",
        "quoted",
        "parent",
        "continuation",
    }


def test_reviewed_reply_filter_preserves_saves_and_required_context():
    module = _preparer()
    root = {"tweet_id": "root", "text": "Selected", "collections": ["like"]}
    spam = {"tweet_id": "spam", "reply_to_id": "root", "text": "Buy my token"}
    useful = {"tweet_id": "useful", "reply_to_id": "root", "text": "Specific correction"}
    assert module.trim_reviewed_replies([root, spam, useful], {"spam"}) == [root, useful]
    with pytest.raises(ValueError, match="saved posts"):
        module.trim_reviewed_replies([root, {**spam, "collections": ["bookmark"]}], {"spam"})
    with pytest.raises(ValueError, match="required"):
        module.trim_reviewed_replies([root, spam, {**useful, "reply_to_id": "spam"}], {"spam"})


def test_known_embedded_quotes_still_require_their_own_conversation_expansion(tmp_path):
    module = _preparer()
    quote = make_tweet_result("200", "Embedded quote", user_id="123")
    root = make_tweet_result("100", "Selected", user_id="123", quoted_tweet=quote)
    root["legacy"]["quoted_status_id_str"] = "200"
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    try:
        payload = make_tweet_detail_response([root])
        store.persist_thread_detail(
            focal_tweet_id="100", tweets=parse_tweet_detail_tweets(payload), raw_json=payload
        )
        assert module.missing_context_targets(store, ["100"]) == []
        assert module.pending_context_targets(store, ["100"]) == ["200"]
        nested = make_tweet_result("300", "Quote inside reply", user_id="123")
        reply = make_tweet_result(
            "201",
            "Reply quoting more context",
            user_id="123",
            in_reply_to_status_id="200",
            quoted_tweet=nested,
        )
        reply["legacy"]["quoted_status_id_str"] = "300"
        payload = make_tweet_detail_response([quote, reply])
        store.persist_thread_detail(
            focal_tweet_id="200", tweets=parse_tweet_detail_tweets(payload), raw_json=payload
        )
        assert module.pending_context_targets(store, ["100"]) == ["300"]
        payload = make_tweet_detail_response([nested])
        store.persist_thread_detail(
            focal_tweet_id="300", tweets=parse_tweet_detail_tweets(payload), raw_json=payload
        )
        assert module.pending_context_targets(store, ["100"]) == []
        assert {row["tweet_id"] for row in module.context_rows(store, ["100"])} == {
            "100",
            "200",
            "201",
            "300",
        }
    finally:
        store.close()


def test_nested_raw_quote_links_are_reachable_before_relation_backfill(tmp_path):
    module = _preparer()
    deep = make_tweet_result("300", "Deep quote", user_id="123")
    middle = make_tweet_result("200", "Middle quote", user_id="123")
    middle["legacy"]["quoted_status_id_str"] = "300"
    outer = make_tweet_result("100", "Selected", user_id="123", quoted_tweet=middle)
    outer["legacy"]["quoted_status_id_str"] = "200"
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    try:
        payload = make_tweet_detail_response([outer])
        store.persist_thread_detail(
            focal_tweet_id="100",
            tweets=parse_tweet_detail_tweets(payload),
            raw_json=payload,
        )
        assert module.missing_context_targets(store, ["100"]) == ["300"]
        payload = make_tweet_detail_response([deep])
        store.persist_thread_detail(
            focal_tweet_id="300",
            tweets=parse_tweet_detail_tweets(payload),
            raw_json=payload,
        )
        assert {row["tweet_id"] for row in module.context_rows(store, ["100"])} == {
            "100",
            "200",
            "300",
        }
        assert module.missing_context_targets(store, ["100"]) == []
    finally:
        store.close()


@pytest.mark.parametrize("with_audio", [False, True])
def test_video_publication_preserves_playback_and_sound(tmp_path, monkeypatch, with_audio):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg and ffprobe are needed for media integration checks")
    module = _preparer()
    source = tmp_path / "source.mp4"
    command = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=96x64:rate=12",
    ]
    if with_audio:
        command += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100"]
    command += ["-t", "1.25", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)]
    subprocess.run(command, check=True, capture_output=True)
    monkeypatch.setattr(module, "PUBLIC_MEDIA", tmp_path / "public")
    projected = module.compress_video(source, Path("real/clip.mp4"))
    output = tmp_path / "public" / "real" / "clip.mp4"
    info = json.loads(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(output),
            ]
        )
    )
    assert projected["url"] == "demo/media/real/clip.mp4"
    assert projected["aspect_ratio"] == [96, 64]
    assert abs(projected["duration_millis"] - 1250) < 100
    assert any(stream["codec_type"] == "audio" for stream in info["streams"]) == with_audio
    video = next(stream for stream in info["streams"] if stream["codec_type"] == "video")
    assert video["codec_name"] == "h264"
    assert video["avg_frame_rate"] == "12/1"
    content = output.read_bytes()
    assert content.index(b"moov") < content.index(b"mdat")
    assert source.is_file()


def test_reply_hop_limit_keeps_saves_quotes_and_ancestors_without_reopening_branches():
    module = _preparer()
    tweets = [
        {"tweet_id": "100", "collections": ["like"]},
        {"tweet_id": "101", "reply_to_id": "100"},
        {"tweet_id": "102", "reply_to_id": "101", "quoted_tweet_id": "300"},
        {"tweet_id": "300"},
        {"tweet_id": "301", "reply_to_id": "300"},
        {"tweet_id": "103", "reply_to_id": "100", "quoted_tweet_id": "200"},
        {"tweet_id": "200"},
        {"tweet_id": "201", "reply_to_id": "200"},
        {"tweet_id": "202", "reply_to_id": "201"},
        {
            "tweet_id": "104",
            "reply_to_id": "100",
            "urls": [{"expanded_url": "https://x.com/example/status/500"}],
        },
        {"tweet_id": "500"},
        {"tweet_id": "501", "reply_to_id": "500"},
        {"tweet_id": "600", "reply_to_id": "100"},
        {"tweet_id": "601", "reply_to_id": "600"},
        {"tweet_id": "900", "reply_to_id": "601", "collections": ["bookmark"]},
        {"tweet_id": "901", "reply_to_id": "900"},
        {"tweet_id": "602", "reply_to_id": "601"},
    ]
    kept, roots = module.limit_reply_hops(tweets)
    assert {t["tweet_id"] for t in kept} == {
        "100",
        "101",
        "103",
        "200",
        "201",
        "104",
        "500",
        "501",
        "600",
        "601",
        "900",
        "901",
    }
    assert roots == {"100", "200", "500", "900"}
    assert module.limit_reply_hops(kept) == (kept, roots)
