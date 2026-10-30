from tweetnook.importer.archive_manifests import remove_archive_owned_files


def test_regeneration_cleanup_never_follows_media_symlinks(tmp_path):
    root = tmp_path / "archive"
    outside = tmp_path / "outside"
    outside.mkdir()
    unrelated = outside / "asset.jpg"
    unrelated.write_bytes(b"preserve")
    (root / "media").mkdir(parents=True)
    (root / "media/linked-dir").symlink_to(outside, target_is_directory=True)
    (root / "media/linked-file.jpg").symlink_to(unrelated)
    owned = root / "media/normal/asset.jpg"
    owned.parent.mkdir()
    owned.write_bytes(b"owned")
    assert (
        remove_archive_owned_files(
            root,
            [
                "media/linked-dir/asset.jpg",
                "media/linked-file.jpg",
                "media/normal/asset.jpg",
                "../outside/asset.jpg",
                str(unrelated),
            ],
        )
        == 1
    )
    assert unrelated.read_bytes() == b"preserve"
    assert not owned.exists()
    assert not owned.parent.exists()
    assert (root / "media").is_dir()
    assert (root / "media/linked-file.jpg").is_symlink()


def test_regeneration_cleanup_works_when_media_root_is_on_another_disk(tmp_path):
    root = tmp_path / "archive"
    root.mkdir()
    other_disk = tmp_path / "other-disk"
    (other_disk / "nested").mkdir(parents=True)
    (other_disk / "nested" / "asset.jpg").write_bytes(b"owned")
    (root / "media").symlink_to(other_disk, target_is_directory=True)
    assert remove_archive_owned_files(root, ["media/nested/asset.jpg"]) == 1
    assert not (other_disk / "nested").exists()
    assert other_disk.is_dir()


def test_regeneration_cleanup_does_not_follow_links_below_a_linked_media_root(tmp_path):
    root = tmp_path / "archive"
    root.mkdir()
    media = tmp_path / "other-disk"
    media.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "asset.jpg").write_bytes(b"preserve")
    (root / "media").symlink_to(media, target_is_directory=True)
    (media / "linked").symlink_to(outside, target_is_directory=True)
    assert remove_archive_owned_files(root, ["media/linked/asset.jpg"]) == 0
    assert (outside / "asset.jpg").read_bytes() == b"preserve"
