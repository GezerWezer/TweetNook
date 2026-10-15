from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

import tweetnook.cli as cli
from tests.conftest import make_photo_media, make_tweet_result, make_url_entity
from tweetnook.client.timelines import TimelineTweet
from tweetnook.config import AppConfig
from tweetnook.prune import apply_prune, plan_prune
from tweetnook.storage import ArchiveStore, open_archive_store

runner = CliRunner()


def _timeline(raw: dict[str, object]) -> TimelineTweet:
    tweet_id = str(raw["rest_id"])
    user_id = str(raw["core"]["user_results"]["result"]["rest_id"])  # type: ignore[index]
    return TimelineTweet(
        tweet_id=tweet_id,
        text=str(raw["legacy"]["full_text"]),  # type: ignore[index]
        author_id=user_id,
        author_username=f"user{user_id}",
        author_display_name=f"User {user_id}",
        created_at="Sat Mar 14 00:00:00 +0000 2026",
        sort_index=tweet_id,
        raw_json=raw,
    )


def _status(tweet_id: str) -> dict[str, object]:
    return make_url_entity(f"https://t.co/s{tweet_id}", f"https://x.com/u/status/{tweet_id}")


def _link(name: str) -> dict[str, object]:
    return make_url_entity(f"https://t.co/{name}", f"https://example.com/{name}")


def _reply(tweet_id: str, root: str, user_id: str, **kwargs) -> dict[str, object]:
    return make_tweet_result(
        tweet_id,
        f"reply {tweet_id}",
        user_id=user_id,
        conversation_id=root,
        in_reply_to_status_id=root,
        **kwargs,
    )


def _set_media_file(store: ArchiveStore, data_dir: Path, tweet_id: str) -> Path:
    relative = f"media/{tweet_id}/photo.jpg"
    path = data_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"jpeg")
    with store.conn:
        store.conn.execute(
            "UPDATE archive SET local_path = ?, download_state = 'done' "
            "WHERE record_type = 'media' AND tweet_id = ?",
            (relative, tweet_id),
        )
    return path


def _seed(paths) -> dict[str, Path]:
    """Saved tweet 100 with its own thread, a quote target, a self-thread link target,
    a thread linked only from someone else's reply (700) and an unconnected thread (900).
    """
    quoted = make_tweet_result("200", "quoted", user_id="2000", media=[_photo("200")])
    saved = make_tweet_result("100", "saved", user_id="1000", quoted_tweet=quoted)
    self_reply = _reply("101", "100", "1000", urls=[_status("500")])
    embedded = make_tweet_result("650", "quoted by a reply", user_id="6500")
    other_reply = _reply("102", "100", "3000", urls=[_status("700")], quoted_tweet=embedded)
    threads = {
        "100": [saved, self_reply, other_reply],
        "200": [quoted, _reply("201", "200", "2100", urls=[_link("shared")])],
        "500": [make_tweet_result("500", "self-thread target", user_id="5000")],
        "700": [
            make_tweet_result("700", "reply-linked", user_id="7000", media=[_photo("700")]),
            _reply("701", "700", "7100", urls=[_link("shared"), _link("offpath")]),
        ],
        "900": [
            make_tweet_result("900", "unconnected", user_id="9000"),
            _reply("901", "900", "9100"),
        ],
    }
    store = open_archive_store(paths, create=True)
    assert store is not None
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[_timeline(saved)],
        last_head_tweet_id="100",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    for target, tweets in threads.items():
        store.persist_thread_detail(
            focal_tweet_id=target,
            tweets=[_timeline(raw) for raw in tweets],
            raw_json={"thread": target},
        )
    files = {
        tweet_id: _set_media_file(store, paths.data_dir, tweet_id) for tweet_id in ("200", "700")
    }
    store.close()
    return files


def _photo(tweet_id: str) -> dict[str, object]:
    return make_photo_media(f"3_{tweet_id}", f"https://pbs.twimg.com/media/{tweet_id}.jpg")


def _plan(paths, **settings):
    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        return plan_prune(
            store,
            paths.data_dir,
            max_linked_depth=settings.get("max_linked_depth", 50),
            reply_linked_depth=settings.get("reply_linked_depth", 0),
        )
    finally:
        store.close()


def test_plan_prune_keeps_save_path_and_embedded_tweets(paths) -> None:
    files = _seed(paths)

    plan = _plan(paths)

    # 650 is only quoted by another user's reply: it stays, without its thread.
    assert plan.pruned_tweet_ids == {"700", "701", "900", "901"}
    assert plan.thread_fetch_count == 2
    assert plan.media_files == [files["700"].resolve()]
    assert plan.row_counts["tweet_object"] == 4
    assert plan.row_counts["url"] == 1
    assert plan.saved_count == 1


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        ({"reply_linked_depth": 1}, {"900", "901"}),
        # No linked expansion: only the saved conversation and embedded quotes remain.
        ({"max_linked_depth": 0}, {"201", "500", "700", "701", "900", "901"}),
    ],
)
def test_plan_prune_follows_linked_depth_settings(paths, settings, expected) -> None:
    _seed(paths)

    assert _plan(paths, **settings).pruned_tweet_ids == expected


def test_apply_prune_deletes_rows_files_and_orphaned_urls(paths) -> None:
    files = _seed(paths)
    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        plan = plan_prune(store, paths.data_dir, max_linked_depth=50, reply_linked_depth=0)
        result = apply_prune(store, plan, paths.data_dir, paths.database_path)

        assert result.rows_deleted == plan.row_total
        assert result.files_deleted == 1
        remaining = {
            row[0]
            for row in store.conn.execute(
                "SELECT DISTINCT COALESCE(tweet_id, cursor_in) FROM archive "
                "WHERE record_type != 'url' AND COALESCE(tweet_id, cursor_in) IS NOT NULL"
            )
        }
        assert remaining.isdisjoint({"700", "701", "900", "901"})
        assert {"100", "101", "102", "200", "201", "500", "650"} <= remaining
        urls = {
            row[0]
            for row in store.conn.execute("SELECT url FROM archive WHERE record_type = 'url'")
        }
        assert any("shared" in url for url in urls)
        assert not any("offpath" in url for url in urls)
        assert store.list_membership_tweet_ids() == ["100"]
        assert store.check_integrity() == ["ok"]
        assert _plan_is_empty(store, paths.data_dir)
    finally:
        store.close()
    assert not files["700"].exists()
    assert not files["700"].parent.exists()
    assert files["200"].exists()


def _plan_is_empty(store: ArchiveStore, data_dir: Path) -> bool:
    plan = plan_prune(store, data_dir, max_linked_depth=50, reply_linked_depth=0)
    return plan.row_total == 0 and not plan.media_files


def test_prune_cli_is_a_dry_run_until_apply(paths, monkeypatch) -> None:
    files = _seed(paths)
    buffer = StringIO()
    monkeypatch.setattr(
        cli,
        "_configure_logging",
        lambda: Console(file=buffer, force_terminal=False, color_system=None, width=200),
    )
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    lock_calls: list[Path] = []
    monkeypatch.setattr(
        cli,
        "_with_archive_write_lock",
        lambda lock_paths, fn: (lock_calls.append(lock_paths.lock_file), fn())[1],
    )

    dry = runner.invoke(cli.app, ["prune"])
    assert dry.exit_code == 0, dry.output
    output = buffer.getvalue()
    assert "4  tweets" in output
    assert "2  expanded threads" in output
    assert "Dry run: nothing was deleted" in output
    assert lock_calls == []
    assert files["700"].exists()

    applied = runner.invoke(cli.app, ["prune", "--apply", "--reply-linked-depth", "1"])
    assert applied.exit_code == 0, applied.output
    assert lock_calls == [paths.lock_file]
    assert "Deleted" in buffer.getvalue()
    # The reply hop keeps 700's thread and its media.
    assert files["700"].exists()
    assert _plan(paths, reply_linked_depth=1).row_total == 0
