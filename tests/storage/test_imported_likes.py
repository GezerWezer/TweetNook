from __future__ import annotations

import pytest

from tests.collection_sync.conftest import _seed_like_import
from tweetnook.importer.archive_manifests import record_archive_capture
from tweetnook.storage import open_archive_store


@pytest.mark.parametrize("bad_part", [None, {"unexpected": []}, [{"like": {"tweetId": "1"}}]])
def test_import_like_snapshot_requires_every_declared_part(paths, bad_part):
    store = open_archive_store(paths, create=True)
    try:
        _seed_like_import(store, ["1", "2", "3"])
        manifest = {
            "archiveInfo": {"isPartialArchive": False},
            "dataTypes": {
                "like": {
                    "files": [
                        {"fileName": "data/like.js", "count": "3"},
                        {"fileName": "data/like-part1.js", "count": "2"},
                    ]
                }
            },
        }
        record_archive_capture(
            store, "XArchiveManifest", "data/manifest.js", manifest, archive_digest="export"
        )
        if bad_part is not None:
            record_archive_capture(
                store, "XArchiveLikes", "data/like-part1.js", bad_part, archive_digest="export"
            )
        assert store.get_complete_import_like_ids() == frozenset()
    finally:
        store.close()


def test_import_like_snapshot_combines_complete_exports_and_parts(paths):
    store = open_archive_store(paths, create=True)
    try:
        _seed_like_import(store, ["1", "2"])
        _seed_like_import(store, ["3"], digest="another")
        _seed_like_import(store, ["4"], digest="sampled", status="sampled")
        record_archive_capture(
            store,
            "XArchiveManifest",
            "data/manifest.js",
            {
                "archiveInfo": {"isPartialArchive": False},
                "dataTypes": {
                    "like": {
                        "files": [
                            {"fileName": "data/like.js", "count": "2"},
                            {"fileName": "data/like-part1.js", "count": "2"},
                        ]
                    }
                },
            },
            archive_digest="export",
        )
        record_archive_capture(
            store,
            "XArchiveLikes",
            "data/like-part1.js",
            [{"like": {"tweetId": "3"}}, {"like": {"tweetId": "not-imported"}}],
            archive_digest="export",
        )
        assert store.get_complete_import_like_ids() == frozenset({"1", "2", "3"})
    finally:
        store.close()
