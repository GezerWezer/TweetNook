from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from io import StringIO

import pytest
from rich.console import Console

from tweetnook.stats import (
    STATS_SECTION_SPECS,
    StatItem,
    StatsReport,
    StatsSection,
    TableColumn,
    build_stats_report,
    build_stats_section,
)
from tweetnook.stats import service as stats_service
from tweetnook.stats.render_cli import render_stats_report
from tweetnook.storage import ArchiveStore


def _seed_stats_store(tmp_path) -> ArchiveStore:
    store = ArchiveStore(tmp_path / "archive.db", create=True)
    store._merge_records(
        [
            store._record(
                row_key="tweet:bookmark::1",
                record_type="tweet",
                tweet_id="1",
                collection_type="bookmark",
                author_id="author-1",
                created_at="Sat Mar 14 00:00:00 +0000 2026",
                raw_json="{}",
                text="hello",
            ),
            store._record(
                row_key="tweet_object:1",
                record_type="tweet_object",
                tweet_id="1",
                enrichment_state="done",
                raw_json="{}",
            ),
            store._record(
                row_key="media:1:photo",
                record_type="media",
                tweet_id="1",
                media_type="video",
                local_path="media/1.mp4",
                thumbnail_local_path="media/1-poster.jpg",
            ),
            store._record(
                row_key="media_tag:1",
                record_type="media_tag",
                tweet_id="1",
                raw_json=json.dumps({"tags": ["Bird", "Sky"]}),
            ),
            store._record(
                row_key="raw_capture:thread:1",
                record_type="raw_capture",
                operation="ThreadExpandDetail",
                cursor_in="1",
                captured_at="2026-08-09T12:00:00+00:00",
            ),
            store._record(
                row_key="sync_state:bookmark:",
                record_type="sync_state",
                collection_type="bookmark",
                updated_at="2026-08-09T12:00:00+00:00",
            ),
        ]
    )
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    (media_dir / "1.mp4").write_bytes(b"video")
    (media_dir / "1-poster.jpg").write_bytes(b"poster")
    store.set_archive_owner_id("owner-1")
    return store


def test_report_follows_registry_and_serializes_all_sections(tmp_path) -> None:
    store = _seed_stats_store(tmp_path)

    report = build_stats_report(store)

    expected = [(spec.id, spec.title, spec.kind) for spec in STATS_SECTION_SPECS]
    actual = [(section.id, section.title, section.kind) for section in report.sections]
    assert actual == expected
    assert report.owner == "owner-1"
    assert report.section("overview").data["unique_posts"] == 1
    assert report.section("archive_status").data["threads_expanded"] == 1
    assert "version_count" not in report.section("storage").data
    assert "optimize_status" not in report.section("storage").data
    assert report.section("tagging").data["top_tags"] == [
        {"tag": "bird", "count": 1},
        {"tag": "sky", "count": 1},
    ]
    assert "follow_up" not in {section.id for section in report.sections}
    json.dumps(report.as_dict())
    store.close()


def test_report_can_collect_one_registered_section(tmp_path) -> None:
    store = _seed_stats_store(tmp_path)

    report = build_stats_report(store, include={"tagging"})

    assert [section.id for section in report.sections] == ["tagging"]
    assert build_stats_section(store, "tagging").as_dict() == report.sections[0].as_dict()
    with pytest.raises(KeyError, match="unknown statistics section"):
        build_stats_section(store, "missing")
    with pytest.raises(KeyError, match="unknown statistics sections"):
        build_stats_report(store, include={"missing"})
    store.close()


def test_shared_collectors_serialize_parallel_web_snapshots(tmp_path, monkeypatch) -> None:
    store = _seed_stats_store(tmp_path)
    original = stats_service._COLLECTORS["overview"]
    state_lock = threading.Lock()
    active = 0
    maximum_active = 0

    def observed_collector(context):
        nonlocal active, maximum_active
        with state_lock:
            active += 1
            maximum_active = max(maximum_active, active)
        try:
            time.sleep(0.03)
            return original(context)
        finally:
            with state_lock:
                active -= 1

    monkeypatch.setitem(stats_service._COLLECTORS, "overview", observed_collector)
    with ThreadPoolExecutor(max_workers=2) as executor:
        reports = list(
            executor.map(lambda _index: build_stats_section(store, "overview"), range(2))
        )

    assert [report.data["unique_posts"] for report in reports] == [1, 1]
    assert maximum_active == 1
    store.close()


def test_cli_renderer_handles_new_generic_sections_without_command_changes() -> None:
    report = StatsReport(
        archive_path="/tmp/archive.db",
        owner="owner-1",
        generated_at="2026-08-09T00:00:00+00:00",
        sections=[
            StatsSection(
                id="new_cards",
                title="New cards",
                kind="cards",
                items=[StatItem("items", "Items", 1234)],
            ),
            StatsSection(
                id="new_status",
                title="New status",
                kind="status",
                columns=[TableColumn("task", "Task"), TableColumn("status", "Status")],
                rows=[{"task": "Index", "status": "ready"}],
            ),
        ],
    )
    buffer = StringIO()

    render_stats_report(Console(file=buffer, force_terminal=False), report)

    rendered = buffer.getvalue()
    output = " ".join(rendered.split())
    assert "New cards" in output
    assert "Items" in output
    assert "1,234" in output
    assert "New status" in output
    assert "Index" in output
    assert "ready" in output


def test_cli_renderer_uses_the_terminal_summary_without_follow_up(tmp_path) -> None:
    store = _seed_stats_store(tmp_path)
    report = build_stats_report(store)
    buffer = StringIO()

    render_stats_report(Console(file=buffer, force_terminal=False, width=120), report)

    rendered = buffer.getvalue()
    output = " ".join(rendered.split())
    assert "TweetNook statistics" in output
    assert "View summary" in output
    assert "Archive" in output
    assert "Archive health" in output
    assert "Enrichment" in output
    assert "Database & Indexes" in output
    assert "Coverage" in output
    assert "Core Tweet Database" not in output
    assert "Protected account" not in output
    assert "Versions" not in output
    assert "Optimize" not in output
    assert "Follow-up" not in output
    assert "·  ·" not in rendered
    assert rendered.count("\n\n") >= 4
    store.close()


def test_cli_detailed_renderer_swaps_in_full_storage_and_hidden_rows(tmp_path) -> None:
    store = _seed_stats_store(tmp_path)
    report = build_stats_report(store)
    buffer = StringIO()

    render_stats_report(
        Console(file=buffer, force_terminal=False, width=120),
        report,
        detailed=True,
    )

    output = " ".join(buffer.getvalue().split())
    assert "TweetNook statistics" in output
    assert "View detailed" in output
    assert "Core Tweet Database" in output
    assert "Supplementary Media Files" in output
    assert "Protected account" in output
    assert "Enrichment pending" in output
    assert "awaiting first attempt" in output
    assert "Raw captures" in output
    store.close()
