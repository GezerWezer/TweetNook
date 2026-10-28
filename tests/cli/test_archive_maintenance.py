from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
import typer

import tweetnook.cli as cli
from tests.cli.conftest import _capture_console, _seed_archive, runner
from tests.conftest import patch_cli
from tweetnook.cli import enrichment as cli_enrichment
from tweetnook.cli import maintenance as cli_maintenance
from tweetnook.config import AppConfig


def test_expand_archive_threads_refresh_requires_targets(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    patch_cli(monkeypatch, "load_config", lambda: (AppConfig(), paths))
    patch_cli(
        monkeypatch,
        "resolve_auth_bundle",
        lambda config: SimpleNamespace(auth_token="token", ct0="ct0"),
    )

    with pytest.raises(typer.Exit) as excinfo:
        cli_enrichment.expand_archive_threads(refresh=True)

    assert excinfo.value.exit_code == 1
    assert "--refresh requires one or more explicit thread targets." in buffer.getvalue()


def test_optimize_archive_uses_write_lock(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    patch_cli(monkeypatch, "load_config", lambda: (AppConfig(), paths))
    lock_calls: list[Path] = []

    class FakeStore:
        def __init__(self) -> None:
            self.optimized = False
            self.closed = False

        def optimize(self) -> None:
            self.optimized = True

        def close(self) -> None:
            self.closed = True

    store = FakeStore()
    patch_cli(monkeypatch, "open_archive_store", lambda _paths, create=False, config=None: store)
    patch_cli(
        monkeypatch,
        "with_archive_write_lock",
        lambda lock_paths, fn: (lock_calls.append(lock_paths.lock_file), fn())[1],
    )

    cli.optimize_archive()

    assert lock_calls == [paths.lock_file]
    assert store.optimized is True
    assert store.closed is True
    assert "optimizing database and search index..." in buffer.getvalue()
    assert "database optimization complete." in buffer.getvalue()


def test_optimize_help_describes_query_and_search_maintenance() -> None:
    result = runner.invoke(cli.app, ["optimize", "--help"])
    assert result.exit_code == 0
    assert "query planning" in result.stdout
    assert "full-text index" in result.stdout


def test_prune_help_describes_dry_run_and_scope_overrides() -> None:
    result = runner.invoke(cli.app, ["prune", "--help"])

    assert result.exit_code == 0
    normalized = " ".join(result.stdout.replace("│", " ").split())
    assert "--apply" in normalized
    assert "only report what would be deleted" in normalized
    assert "--max-linked-depth" in normalized
    assert "default: the sync max_linked_depth setting" in normalized
    assert "--reply-linked-depth" in normalized
    assert "default: the sync reply_linked_depth setting" in normalized
    assert "threads expand" in normalized


def test_prune_reply_linked_depth_rejects_values_above_one() -> None:
    result = runner.invoke(cli.app, ["prune", "--reply-linked-depth", "2"])

    assert result.exit_code == 2


@pytest.mark.parametrize(
    ("full", "expected_pragma"),
    [(False, "quick_check"), (True, "integrity_check")],
)
def test_database_check_runs_requested_diagnostic_and_closes_store(
    paths,
    monkeypatch: pytest.MonkeyPatch,
    full: bool,
    expected_pragma: str,
) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)

    class FakeStore:
        def __init__(self) -> None:
            self.full_values: list[bool] = []
            self.closed = False

        def check_integrity(self, *, full: bool = False) -> list[str]:
            self.full_values.append(full)
            return ["ok"]

        def close(self) -> None:
            self.closed = True

    store = FakeStore()
    patch_cli(monkeypatch, "open_store_for_read", lambda _console: (store, paths))

    cli_maintenance.check_database(full=full)

    assert store.full_values == [full]
    assert store.closed is True
    assert f"database {expected_pragma}: ok" in buffer.getvalue()


def test_database_check_reports_integrity_problems(
    paths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)

    class FakeStore:
        def __init__(self) -> None:
            self.closed = False

        def check_integrity(self, *, full: bool = False) -> list[str]:
            assert full is False
            return ["row 10 missing from index"]

        def close(self) -> None:
            self.closed = True

    store = FakeStore()
    patch_cli(monkeypatch, "open_store_for_read", lambda _console: (store, paths))

    with pytest.raises(typer.Exit) as excinfo:
        cli_maintenance.check_database()

    assert excinfo.value.exit_code == 2
    assert store.closed is True
    assert "row 10 missing from index" in buffer.getvalue()


def test_stats_archive_renders_summary_tables(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    patch_cli(monkeypatch, "load_config", lambda: (AppConfig(), paths))
    _seed_archive(paths)

    cli.stats_archive()

    output = buffer.getvalue()
    normalized = " ".join(output.split())
    assert "TweetNook statistics" in normalized
    assert "View summary" in normalized
    assert "Archive" in normalized
    assert "Unique tweets" in normalized
    assert "Collection" in normalized
    assert "memberships" in normalized
    assert "Collections" in normalized
    assert "Storage" in normalized
    assert "Database & Indexes" in normalized
    assert "Archive health" in normalized
    assert "Tagging & search" not in normalized
    assert "Local rehydrate gaps" in normalized
    assert "Thread memberships pending" in normalized
    assert "related pending" in normalized
    assert "Versions" not in normalized
    assert "Optimize" not in normalized
    assert "Follow-up" not in normalized


def test_stats_archive_builds_a_fresh_report_on_every_invocation(paths, monkeypatch) -> None:
    class Store:
        def close(self) -> None:
            pass

    store = Store()
    reports: list[object] = []
    rendered: list[object] = []
    patch_cli(monkeypatch, "configure_logging", lambda: object())
    patch_cli(monkeypatch, "open_store_for_read", lambda _console: (store, paths))

    def build(store_arg):
        assert store_arg is store
        report = object()
        reports.append(report)
        return report

    patch_cli(monkeypatch, "build_stats_report", build)
    patch_cli(
        monkeypatch,
        "render_stats_report",
        lambda _console, report, *, detailed: rendered.append(report),
    )

    cli.stats_archive()
    cli.stats_archive()

    assert len(reports) == 2
    assert reports[0] is not reports[1]
    assert rendered == reports


def test_stats_archive_detailed_shows_full_storage_and_hidden_rows(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    patch_cli(monkeypatch, "load_config", lambda: (AppConfig(), paths))
    _seed_archive(paths)

    cli.stats_archive(detailed=True)

    normalized = " ".join(buffer.getvalue().split())
    assert "TweetNook statistics" in normalized
    assert "View detailed" in normalized
    assert "Core Tweet Database" in normalized
    assert "Protected account" in normalized
    assert "Enrichment pending" in normalized


def test_rehydrate_archive_uses_write_lock(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    patch_cli(monkeypatch, "load_config", lambda: (AppConfig(), paths))
    lock_calls: list[Path] = []

    class FakeTqdm:
        def __init__(self, *args, **kwargs) -> None:
            self.updates: list[int] = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def update(self, count: int) -> None:
            self.updates.append(count)

    class FakeStore:
        def __init__(self) -> None:
            self.table = SimpleNamespace(count_rows=lambda expr: 2)
            self.optimized = False
            self.closed = False

        def _count(self, filter_expr: str | None = None) -> int:
            return 2

        def rehydrate_from_raw_json(self, *, progress=None):
            if progress is not None:
                progress(2)
            return SimpleNamespace(tweets_updated=2, secondary_records=5)

        def optimize(self) -> None:
            self.optimized = True

        def close(self) -> None:
            self.closed = True

    store = FakeStore()
    patch_cli(monkeypatch, "open_archive_store", lambda _paths, create=False, config=None: store)
    patch_cli(
        monkeypatch,
        "with_archive_write_lock",
        lambda lock_paths, fn: (lock_calls.append(lock_paths.lock_file), fn())[1],
    )
    monkeypatch.setitem(sys.modules, "tqdm", SimpleNamespace(tqdm=FakeTqdm))

    cli.rehydrate_archive()

    assert lock_calls == [paths.lock_file]
    assert store.closed is True
    assert "rehydrated 2 tweet rows and rebuilt 5 secondary rows" in buffer.getvalue()
