import pytest
from typer.testing import CliRunner

from tests.cli.conftest import _seed_archive
from tests.conftest import patch_cli
from tweetnook.cli import app
from tweetnook.config import AppConfig
from tweetnook.storage import open_archive_store


@pytest.mark.parametrize("kind", ["post", "article", "post,article"])
@pytest.mark.parametrize("limit", ["0", "-1"])
def test_search_requires_a_positive_limit_before_opening_any_archive(monkeypatch, kind, limit):
    patch_cli(monkeypatch, "open_store_for_read", lambda *args: pytest.fail("archive opened"))

    result = CliRunner().invoke(app, ["search", "cats", "--type", kind, "--limit", limit])

    assert result.exit_code == 2, result.output
    assert "--limit" in result.output


@pytest.mark.parametrize("collection", ["all", "bookmarks", "likes", "tweets"])
@pytest.mark.parametrize("options", [["--limit", "-1"], ["--limit", "0"], ["--sort", "wrong"]])
def test_view_validates_options_before_opening_an_archive(monkeypatch, collection, options):
    patch_cli(monkeypatch, "open_store_for_read", lambda *args: pytest.fail("archive opened"))

    result = CliRunner().invoke(app, ["view", collection, *options])

    assert result.exit_code == 2, result.output
    assert options[0] in result.output


@pytest.mark.parametrize("command", [["search"], ["view", "all"], ["view", "likes"]])
def test_read_limit_help_explains_positive_domain(command):
    result = CliRunner().invoke(app, [*command, "--help"])

    assert result.exit_code == 0
    text = " ".join(result.output.replace("│", " ").split())
    assert "--limit" in text
    assert "at least 1" in text


def test_valid_view_choices_and_storage_zero_limit_remain_supported(paths, monkeypatch):
    _seed_archive(paths)
    store = open_archive_store(paths, create=False)
    try:
        assert store.export_rows("all", limit=0) == []
    finally:
        store.close()
    patch_cli(monkeypatch, "load_config", lambda: (AppConfig(), paths))

    for sort in ("newest", "oldest"):
        result = CliRunner().invoke(app, ["view", "all", "--sort", sort, "--limit", "1000000"])
        assert result.exit_code == 0, result.output
        assert "bookmark tweet" in result.output
