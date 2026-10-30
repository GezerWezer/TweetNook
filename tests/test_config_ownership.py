import threading
import tomllib
from concurrent.futures import ThreadPoolExecutor

import pytest

from tweetnook import config as configuration
from tweetnook.config import load_config_at, update_config_values, upgrade_config
from tweetnook.web.deps import server_state
from tweetnook.web.runtime_config import reload_server_config


def test_load_is_read_only_and_explicit_upgrade_preserves_old_config(tmp_path):
    env = {f"XDG_{kind}_HOME": str(tmp_path / kind) for kind in ["CONFIG", "DATA", "CACHE"]}
    config, paths = configuration.load_config(env)
    assert config.web.port == 8000
    assert list(tmp_path.iterdir()) == []
    paths.config_dir.mkdir(parents=True)
    old = '[auth]\nauth_token = "saved"\n[tagging]\ntext_model = "legacy-model"\n'
    paths.config_file.write_text(old)
    config, _ = configuration.load_config(env)
    assert config.tagging.model == "legacy-model"
    assert paths.config_file.read_text() == old
    upgrade_config(paths)
    upgraded = paths.config_file.read_text()
    assert "text_model" not in upgraded
    assert tomllib.loads(upgraded)["tagging"]["model"] == "legacy-model"
    assert paths.config_file.stat().st_mode & 0o777 == 0o600
    upgrade_config(paths)
    assert paths.config_file.read_text() == upgraded


def test_failed_config_publication_preserves_previous_file(paths, monkeypatch):
    update_config_values(paths, {"web.port": 8123})
    before = paths.config_file.read_bytes()
    original = configuration.atomic_write_text

    def partial_write(path, text, **kwargs):
        write_bytes = type(path).write_bytes

        def fail_after_write(temporary, data):
            write_bytes(temporary, data[:8])
            raise OSError("disk full")

        with monkeypatch.context() as patch:
            patch.setattr(type(path), "write_bytes", fail_after_write)
            original(path, text, **kwargs)

    monkeypatch.setattr(configuration, "atomic_write_text", partial_write)
    with pytest.raises(OSError, match="disk full"):
        update_config_values(paths, {"web.port": 9123})
    assert paths.config_file.read_bytes() == before
    assert sorted(p.name for p in paths.config_dir.iterdir()) == ["config.lock", "config.toml"]


def test_control_character_roundtrip(paths):
    update_config_values(paths, {"web.host": "host\x01name"})
    assert load_config_at(paths, {})[0].web.host == "host\x01name"


def test_concurrent_config_writers_preserve_disjoint_updates(paths, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    second_started = threading.Event()
    second_published = threading.Event()
    write = configuration.atomic_write_text

    def delayed_write(path, text, **kwargs):
        if "page_delay" in text and "port" not in text:
            entered.set()
            assert release.wait(5)
        else:
            second_published.set()
        write(path, text, **kwargs)

    def second_save():
        second_started.set()
        update_config_values(paths, {"web.port": 8123})

    monkeypatch.setattr(configuration, "atomic_write_text", delayed_write)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(update_config_values, paths, {"sync.page_delay": 3})
        assert entered.wait(5)
        second = pool.submit(second_save)
        assert second_started.wait(5)
        try:
            assert not second_published.wait(0.05)
        finally:
            release.set()
        first.result(5)
        second.result(5)
    raw = tomllib.loads(paths.config_file.read_text())
    assert raw["sync"]["page_delay"] == 3
    assert raw["web"]["port"] == 8123


def test_delayed_snapshot_cannot_replace_newer_runtime_config(paths, monkeypatch):
    update_config_values(paths, {"web.fetch_avatars": True})
    older, _ = load_config_at(paths, {})
    update_config_values(paths, {"web.fetch_avatars": False})
    newer, _ = load_config_at(paths, {})
    monkeypatch.setattr(
        "tweetnook.web.runtime_config.load_config", lambda: load_config_at(paths, {})
    )
    server_state["config"] = newer
    try:
        applied = reload_server_config(older)
        assert applied.web.fetch_avatars is False
        assert server_state["config"].web.fetch_avatars is False
    finally:
        server_state.clear()


def test_upgrade_of_a_current_config_needs_no_write_access(paths, monkeypatch):
    update_config_values(paths, {"web.port": 8123})
    paths.config_file.chmod(0o600)
    before = paths.config_file.read_bytes()

    def forbidden(*args, **kwargs):
        raise PermissionError("read-only")

    monkeypatch.setattr(configuration, "config_writer", forbidden)
    monkeypatch.setattr(type(paths.config_file), "chmod", forbidden)
    upgrade_config(paths)
    assert paths.config_file.read_bytes() == before


def test_upgrade_tolerates_a_config_it_cannot_tighten_or_rewrite(paths, monkeypatch):
    old = '[auth]\nauth_token = "saved"\n[tagging]\ntext_model = "legacy-model"\n'
    paths.config_file.write_text(old)
    paths.config_file.chmod(0o644)

    def forbidden(*args, **kwargs):
        raise PermissionError("read-only")

    monkeypatch.setattr(configuration, "atomic_write_text", forbidden)
    upgrade_config(paths)
    assert paths.config_file.read_text() == old
    # The upgrade still reaches the command through the in-memory load.
    assert load_config_at(paths)[0].tagging.model == "legacy-model"

    monkeypatch.setattr(type(paths.config_file), "chmod", forbidden)
    update = '[auth]\nauth_token = ""\nct0 = ""\nuser_id = ""\n'
    paths.config_file.write_text(update)
    upgrade_config(paths)
    assert paths.config_file.read_text() == update


def test_upgrade_still_reports_a_config_it_cannot_create(paths, monkeypatch):
    def forbidden(*args, **kwargs):
        raise PermissionError("read-only")

    monkeypatch.setattr(configuration, "atomic_write_text", forbidden)
    with pytest.raises(PermissionError):
        upgrade_config(paths)


def test_config_write_keeps_a_symlinked_file_a_symlink(paths, tmp_path):
    dotfiles = tmp_path / "dotfiles"
    dotfiles.mkdir()
    real = dotfiles / "config.toml"
    real.write_text("[web]\nport = 8000\n")
    paths.config_file.symlink_to(real)
    update_config_values(paths, {"web.port": 8123})
    assert paths.config_file.is_symlink()
    assert tomllib.loads(real.read_text())["web"]["port"] == 8123
    assert load_config_at(paths)[0].web.port == 8123


def test_config_written_as_root_stays_with_the_directory_owner(paths, monkeypatch):
    owner = paths.config_dir.stat()
    chowned = []
    monkeypatch.setattr(configuration.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        configuration.os, "chown", lambda path, uid, gid: chowned.append((path.name, uid, gid))
    )
    update_config_values(paths, {"web.port": 8123})
    assert {name for name, _, _ in chowned} == {"config.lock", paths.config_file.name}
    assert {(uid, gid) for _, uid, gid in chowned} == {(owner.st_uid, owner.st_gid)}
