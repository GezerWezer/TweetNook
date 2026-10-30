from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

import tweetnook.cli as cli
from tests.conftest import make_photo_media, make_tweet_result, make_url_entity, patch_cli
from tweetnook.config import AppConfig
from tweetnook.core.timeline_parsing import TimelineTweet
from tweetnook.enrichment.prune import apply_prune, plan_prune
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
    patch_cli(
        monkeypatch,
        "configure_logging",
        lambda: Console(file=buffer, force_terminal=False, color_system=None, width=200),
    )
    patch_cli(monkeypatch, "load_config", lambda: (AppConfig(), paths))
    lock_calls: list[Path] = []
    patch_cli(
        monkeypatch,
        "with_archive_write_lock",
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


def test_prune_preview_leaves_no_transaction_on_writer_or_private_reader(paths):
    _seed(paths)
    writer = open_archive_store(paths, create=False)
    reader = writer.open_reader()
    try:
        for store in (writer, reader):
            plan_prune(store, paths.data_dir, max_linked_depth=50, reply_linked_depth=0)
            assert not store.conn.in_transaction
            assert not store.conn.execute(
                "SELECT 1 FROM sqlite_temp_master WHERE name IN ('prune_rows', 'prune_ids')"
            ).fetchall()
        writer.update_media_tags("100", ["Still writable"])
    finally:
        reader.close()
        writer.close()


def test_prune_removes_focal_fx_captures_so_rehydrate_cannot_restore_them(paths):
    from tweetnook.core.fxtwitter_payloads import fx_status_to_graphql, fx_timeline_tweet

    _seed(paths)
    store = open_archive_store(paths, create=False)
    try:
        for tweet_id in ("100", "990"):
            status = {
                "id": tweet_id,
                "type": "status",
                "text": "Fx saved content",
                "author": {"id": "author", "screen_name": "synthetic"},
            }
            store.persist_fx_status(
                tweet=fx_timeline_tweet(fx_status_to_graphql(status)),
                payload={"code": 200, "status": status},
            )
        plan = plan_prune(store, paths.data_dir, max_linked_depth=50, reply_linked_depth=0)
        assert "990" in plan.pruned_tweet_ids
        apply_prune(store, plan, paths.data_dir, paths.database_path)
        assert store.list_target_captures("990", ("FxTwitterStatus",)) == []
        assert len(store.list_target_captures("100", ("FxTwitterStatus",))) == 1
        store.rehydrate_from_raw_json()
        assert "990" not in store.get_tweet_object_rows(["990"])
        assert (
            plan_prune(store, paths.data_dir, max_linked_depth=50, reply_linked_depth=0).row_total
            == 0
        )
    finally:
        store.close()


@pytest.mark.parametrize("rescued", [False, True])
def test_failed_file_cleanup_survives_reopen_and_rechecks_references(paths, monkeypatch, rescued):
    from tweetnook.enrichment.prune_files import pending_file

    files = _seed(paths)
    store = open_archive_store(paths, create=False)
    failed = paths.data_dir / "media/701/second.jpg"
    failed.parent.mkdir(parents=True)
    failed.write_bytes(b"second image")
    store.merge_rows(
        [
            dict(
                row_key="media:701:second",
                record_type="media",
                tweet_id="701",
                local_path="media/701/second.jpg",
            )
        ]
    )
    original_unlink = Path.unlink

    def unlink(path, *args, **kwargs):
        if path == failed:
            raise PermissionError("synthetic cleanup failure")
        return original_unlink(path, *args, **kwargs)

    try:
        plan = plan_prune(store, paths.data_dir, max_linked_depth=50, reply_linked_depth=0)
        with monkeypatch.context() as patch:
            patch.setattr(Path, "unlink", unlink)
            result = apply_prune(store, plan, paths.data_dir, paths.database_path)
        assert result.rows_deleted == plan.row_total
        assert (result.files_deleted, result.files_pending) == (1, 1)
        assert not files["700"].exists() and failed.exists()
        assert pending_file(paths.database_path).is_file()
    finally:
        store.close()

    store = open_archive_store(paths, create=False)
    try:
        if rescued:
            store.merge_rows(
                [
                    dict(
                        row_key="media:100:rescued",
                        record_type="media",
                        tweet_id="100",
                        local_path="media/701/second.jpg",
                    )
                ]
            )
        retry = plan_prune(store, paths.data_dir, max_linked_depth=50, reply_linked_depth=0)
        assert retry.row_total == 0
        assert retry.cleanup_pending
        result = apply_prune(store, retry, paths.data_dir, paths.database_path)
        assert result.files_pending == 0
        assert result.files_deleted == (0 if rescued else 1)
        assert failed.exists() is rescued
        assert files["200"].exists()
        assert not pending_file(paths.database_path).exists()
    finally:
        store.close()


def test_prune_cannot_delete_rows_when_cleanup_intent_cannot_be_published(paths, monkeypatch):
    from tweetnook.enrichment import prune_files

    files = _seed(paths)
    store = open_archive_store(paths, create=False)
    try:
        plan = plan_prune(store, paths.data_dir, max_linked_depth=50, reply_linked_depth=0)

        def fail(*args, **kwargs):
            raise OSError("intent publication failed")

        monkeypatch.setattr(prune_files, "atomic_write_json", fail)
        with pytest.raises(OSError, match="intent publication failed"):
            apply_prune(store, plan, paths.data_dir, paths.database_path)
        assert "700" in store.get_tweet_object_rows(["700"])
        assert files["700"].exists()
    finally:
        store.close()


def test_prune_cleanup_rejects_paths_outside_media_and_below_symlinks(tmp_path):
    import json

    from tweetnook.enrichment.prune_files import cleanup_pending_files, pending_file

    store = ArchiveStore(tmp_path / "archive.db", create=True)
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"keep")
    media = tmp_path / "media"
    media.mkdir()
    (media / "link").symlink_to(tmp_path, target_is_directory=True)
    pending_file(store.db_path).write_text(
        json.dumps(
            {
                "version": 1,
                "paths": ["media/../outside.jpg", str(outside), "media/link/outside.jpg"],
            }
        )
    )
    try:
        assert cleanup_pending_files(store, tmp_path, store.db_path, lambda _: None) == (0, 0, 0)
        assert outside.read_bytes() == b"keep"
    finally:
        store.close()


@pytest.mark.parametrize("content", ["{broken", "[]", '{"version": 1, "paths": [1]}'])
@pytest.mark.parametrize("args", [["prune"], ["prune", "--apply"]])
def test_prune_cli_names_an_invalid_cleanup_file(paths, monkeypatch, content, args) -> None:
    from tweetnook.enrichment.prune_files import pending_file

    _seed(paths)
    intent = pending_file(paths.database_path)
    intent.write_text(content)
    buffer = StringIO()
    patch_cli(
        monkeypatch,
        "configure_logging",
        lambda: Console(file=buffer, force_terminal=False, color_system=None, width=400),
    )
    patch_cli(monkeypatch, "load_config", lambda: (AppConfig(), paths))
    patch_cli(monkeypatch, "with_archive_write_lock", lambda _paths, fn: fn())

    result = runner.invoke(cli.app, args)

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert str(intent) in buffer.getvalue()
    assert intent.read_text() == content


def test_prune_plan_does_not_count_pending_files_with_saved_references(paths) -> None:
    from tweetnook.enrichment.prune_files import pending_file, record_pending_files

    files = _seed(paths)
    kept = files["700"]
    orphan = paths.data_dir / "media" / "orphan.jpg"
    orphan.write_bytes(b"gone")
    record_pending_files(paths.data_dir, paths.database_path, [kept, orphan])
    assert pending_file(paths.database_path).exists()

    plan = _plan(paths, reply_linked_depth=1)

    assert plan.cleanup_pending
    assert orphan in plan.media_files
    assert kept.resolve() not in plan.media_files
