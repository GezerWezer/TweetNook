from __future__ import annotations

import json

import pytest

from tests.archive_import.conftest import _console, _write_archive_dir
from tweetnook.config import AppConfig
from tweetnook.core.exceptions import ConfigError
from tweetnook.importer.archive import import_x_archive
from tweetnook.importer.archive_input import ArchiveInput
from tweetnook.storage import open_archive_store


@pytest.mark.parametrize("payload", [{"tweet": {"id_str": "123"}}, None, "tweets", 1])
async def test_wrong_shaped_dataset_cannot_complete_an_import(paths, tmp_path, payload):
    archive_dir = _write_archive_dir(tmp_path)
    (archive_dir / "data" / "tweets.js").write_text(
        f"window.YTD.tweets.part0 = {json.dumps(payload)};"
    )
    with pytest.raises(ConfigError, match="tweets.js.*array"):
        await import_x_archive(
            archive_dir, detail_lookups=0, config=AppConfig(), paths=paths, console=_console()
        )
    store = open_archive_store(paths, create=False)
    if store is not None:
        try:
            assert store.completed_manifests() == []
        finally:
            store.close()


@pytest.mark.parametrize(
    ("manifest", "label"),
    [
        ([], "manifest.js"),
        ({"dataTypes": []}, "dataTypes"),
        ({"userInfo": "bad"}, "userInfo"),
        ({"archiveInfo": [1]}, "archiveInfo"),
        ({"dataTypes": {"tweets": "bad"}}, "dataTypes.tweets"),
        ({"dataTypes": {"tweets": {"files": {}}}}, "dataTypes.tweets.files"),
        ({"dataTypes": {"tweets": {"files": ["tweets.js"]}}}, "files"),
        ({"dataTypes": {"tweets": {"files": [{}]}}}, "fileName"),
        ({"dataTypes": {"tweets": {"files": [{"fileName": 12}]}}}, "fileName"),
        ({"dataTypes": {"tweets": {"mediaDirectory": []}}}, "mediaDirectory"),
    ],
)
def test_manifest_container_errors_name_the_field(tmp_path, manifest, label):
    archive_dir = _write_archive_dir(tmp_path)
    (archive_dir / "data" / "manifest.js").write_text(
        f"window.__THAR_CONFIG = {json.dumps(manifest)};"
    )
    with pytest.raises(ConfigError, match=label):
        with ArchiveInput(archive_dir) as source:
            source.digest()


def test_absent_and_empty_optional_datasets_remain_valid(tmp_path):
    archive_dir = _write_archive_dir(tmp_path)
    (archive_dir / "data" / "like.js").write_text("window.YTD.like.part0 = [];")
    with ArchiveInput(archive_dir) as source:
        assert source.load_dataset("profile") == ([], [])
        assert source.load_dataset("like") == ([], [("data/like.js", [])])


def test_zip_and_directory_enumerate_only_readable_direct_children(tmp_path):
    import zipfile

    archive_dir = _write_archive_dir(tmp_path)
    media = archive_dir / "data" / "tweets_media"
    (media / "nested").mkdir()
    (media / "nested" / "other.jpg").write_bytes(b"nested image")
    (media / "empty").mkdir()
    archive_zip = tmp_path / "archive.zip"
    with zipfile.ZipFile(archive_zip, "w") as zipped:
        for path in sorted(archive_dir.rglob("*")):
            zipped.write(path, path.relative_to(archive_dir).as_posix())
    with ArchiveInput(archive_dir) as directory, ArchiveInput(archive_zip) as zipped:
        expected = ["data/tweets_media/100-archive-photo.jpg"]
        assert directory.iter_files("tweets_media") == expected
        assert zipped.iter_files("data/tweets_media/") == expected
        assert zipped.iter_files("missing") == []
        assert zipped.digest_total_bytes() == directory.digest_total_bytes()
        assert zipped.digest() == directory.digest()
