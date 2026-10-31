import re
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from tweetnook.stats import build_stats_report
from tweetnook.stats.render_cli import render_stats_report
from tweetnook.storage import ArchiveStore


@pytest.mark.parametrize("width", [90, 140])
@pytest.mark.parametrize("directory", ["[blue]", "[/red]"])
def test_statistics_metadata_renders_literal_brackets_for_real_archive_paths(
    tmp_path, monkeypatch, width, directory
):
    monkeypatch.chdir(tmp_path)
    path = Path(directory) / "archive.db"
    path.parent.mkdir(parents=True)
    store = ArchiveStore(path, create=True)
    try:
        owner = "[bold]fixture[/bold]"
        store.set_archive_owner_id(owner)
        report = build_stats_report(store)
        output = StringIO()

        render_stats_report(Console(file=output, width=width, color_system=None), report)

        # Rich can fold long paths across table rows; only layout whitespace and
        # vertical borders are removed, leaving every path and markup character.
        text = re.sub(r"[\s│┃]", "", output.getvalue())
        assert str(path) in text
        assert owner in text
    finally:
        store.close()
