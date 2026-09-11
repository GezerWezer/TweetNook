from __future__ import annotations

import tomllib
from pathlib import Path

import platformdirs
import pytest
from pydantic import ValidationError

from tweetnook.config import (
    AppConfig,
    AuthConfig,
    DatabaseConfig,
    TaggingConfig,
    WebConfig,
    XDGPaths,
    ensure_paths,
    get_config_ui_schema,
    get_explicit_config_fields,
    load_config,
    resolve_paths,
    update_config_values,
)


def _xdg_env(tmp_path: Path) -> dict[str, str]:
    return {
        "XDG_CONFIG_HOME": str(tmp_path / "config-home"),
        "XDG_DATA_HOME": str(tmp_path / "data-home"),
        "XDG_CACHE_HOME": str(tmp_path / "cache-home"),
    }


def test_resolve_paths_honors_explicit_xdg_roots(tmp_path: Path) -> None:
    paths = resolve_paths(_xdg_env(tmp_path))

    assert paths.config_dir == tmp_path / "config-home" / "tweetnook"
    assert paths.data_dir == tmp_path / "data-home" / "tweetnook"
    assert paths.cache_dir == tmp_path / "cache-home" / "tweetnook"
    assert paths.database_file == paths.database_path == paths.data_dir / "archive.db"
    assert paths.media_dir == paths.data_dir / "media"
    assert paths.avatar_cache_state_file == paths.data_dir / "avatar-cache-state.json"


def test_resolve_paths_uses_platform_defaults_for_explicit_empty_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(platformdirs, "user_config_dir", lambda _name: "/platform/config")
    monkeypatch.setattr(platformdirs, "user_data_dir", lambda _name: "/platform/data")
    monkeypatch.setattr(platformdirs, "user_cache_dir", lambda _name: "/platform/cache")

    paths = resolve_paths({})

    assert paths.config_dir == Path("/platform/config")
    assert paths.data_dir == Path("/platform/data")
    assert paths.cache_dir == Path("/platform/cache")


def test_ensure_paths_creates_all_application_directories(tmp_path: Path) -> None:
    paths = XDGPaths(
        config_dir=tmp_path / "nested" / "config",
        data_dir=tmp_path / "nested" / "data",
        cache_dir=tmp_path / "nested" / "cache",
    )

    assert ensure_paths(paths) is paths
    assert all(path.is_dir() for path in (paths.config_dir, paths.data_dir, paths.cache_dir))


def test_load_config_creates_auth_skeleton_idempotently(tmp_path: Path) -> None:
    env = _xdg_env(tmp_path)

    first, paths = load_config(env)
    first_text = paths.config_file.read_text(encoding="utf-8")
    second, _ = load_config(env)

    assert first.auth == AuthConfig()
    assert first.database == DatabaseConfig()
    assert second == first
    assert first_text == paths.config_file.read_text(encoding="utf-8")
    assert first_text == '[auth]\nauth_token = ""\nct0 = ""\nuser_id = ""\n'


def test_load_config_migrates_split_tagging_fields_to_single_model_and_daily_limit(
    tmp_path: Path,
) -> None:
    env = _xdg_env(tmp_path)
    paths = ensure_paths(resolve_paths(env))
    paths.config_file.write_text(
        """
[auth]
auth_token = ""
ct0 = ""
user_id = ""

[tagging]
text_model = "gemini-3.6-flash"
media_model = "old-media-model"
text_thinking_level = "medium"
media_thinking_level = "high"
spending_limit_usd = 2.5
batch = 40
paid_concurrency = 4
""".lstrip(),
        encoding="utf-8",
    )

    config, _ = load_config(env)
    raw = tomllib.loads(paths.config_file.read_text(encoding="utf-8"))

    assert config.tagging.model == "gemini-3.6-flash"
    assert config.tagging.thinking_level == "medium"
    assert config.tagging.daily_spend_limit_usd == 2.5
    assert config.tagging.free_batch_size == 20
    assert "paid_concurrency" not in TaggingConfig.model_fields
    assert raw["tagging"] == {
        "model": "gemini-3.6-flash",
        "thinking_level": "medium",
        "daily_spend_limit_usd": 2.5,
        "free_batch_size": 20,
    }


def test_load_config_repairs_only_missing_auth_skeleton(tmp_path: Path) -> None:
    env = _xdg_env(tmp_path)
    paths = ensure_paths(resolve_paths(env))
    paths.config_file.write_text(
        "# keep this comment\n[web]\nport = 9123\n",
        encoding="utf-8",
    )

    config, _ = load_config(env)
    repaired = paths.config_file.read_text(encoding="utf-8")

    assert config.web.port == 9123
    assert config.database == DatabaseConfig()
    assert "[database]" not in repaired
    assert repaired == ('[auth]\nauth_token = ""\nct0 = ""\nuser_id = ""\n\n[web]\nport = 9123\n')
    load_config(env)
    assert paths.config_file.read_text(encoding="utf-8") == repaired


def test_load_config_repairs_missing_auth_keys_and_preserves_values(tmp_path: Path) -> None:
    env = _xdg_env(tmp_path)
    paths = ensure_paths(resolve_paths(env))
    paths.config_file.write_text(
        '[auth]\nauth_token = "saved"\nbrowser = "firefox"\n',
        encoding="utf-8",
    )

    config, _ = load_config(env)

    assert config.auth.auth_token == "saved"
    assert config.auth.ct0 is None
    assert config.auth.user_id is None
    assert paths.config_file.read_text(encoding="utf-8") == (
        '[auth]\nauth_token = "saved"\nct0 = ""\nuser_id = ""\n'
    )


def test_load_config_applies_environment_overrides_after_toml(tmp_path: Path) -> None:
    env = _xdg_env(tmp_path)
    paths = ensure_paths(resolve_paths(env))
    paths.config_file.write_text(
        """
[auth]
auth_token = "file-token"
ct0 = "file-ct0"

[sync]
page_delay = 9
max_retries = 8
max_linked_depth = 2

[database]
cache_size_kb = 32
mmap_size_bytes = 64
""".lstrip(),
        encoding="utf-8",
    )
    env.update(
        {
            "TWEETNOOK_AUTH_TOKEN": "env-token",
            "TWEETNOOK_PAGE_DELAY": "1.5",
            "TWEETNOOK_MAX_RETRIES": "4",
            "TWEETNOOK_MAX_LINKED_DEPTH": "7",
        }
    )

    config, _ = load_config(env)

    assert config.auth.auth_token == "env-token"
    assert config.auth.ct0 == "file-ct0"
    assert config.sync.page_delay == 1.5
    assert config.sync.max_retries == 4
    assert config.sync.max_linked_depth == 7
    assert config.database.cache_size_kb == 32


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("TWEETNOOK_PAGE_DELAY", "not-a-float"),
        ("TWEETNOOK_MAX_RETRIES", "2.5"),
    ],
)
def test_load_config_rejects_invalid_numeric_environment_values(
    tmp_path: Path,
    name: str,
    value: str,
) -> None:
    env = _xdg_env(tmp_path)
    env[name] = value

    with pytest.raises(ValueError):
        load_config(env)


@pytest.mark.parametrize(
    ("model", "kwargs"),
    [
        (WebConfig, {"host": ""}),
        (WebConfig, {"port": 0}),
        (WebConfig, {"port": 65536}),
        (WebConfig, {"avatar_cache_limit_mb": 0}),
        (DatabaseConfig, {"cache_size_kb": -1}),
        (DatabaseConfig, {"mmap_size_bytes": -1}),
        (TaggingConfig, {"model": ""}),
        (TaggingConfig, {"thinking_level": "extreme"}),
        (TaggingConfig, {"free_batch_size": 0}),
        (TaggingConfig, {"free_batch_size": 21}),
        (TaggingConfig, {"free_rpd": 0}),
        (TaggingConfig, {"free_rpm": 0}),
        (TaggingConfig, {"max_media_size_mb": 0}),
    ],
)
def test_new_config_models_reject_invalid_values(model, kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        model(**kwargs)


def test_update_config_values_writes_only_changed_fields_and_escapes_strings(
    paths: XDGPaths,
) -> None:
    update_config_values(
        paths,
        {
            "auth.auth_token": 'slash\\quote"line\nnext',
            "web.port": 8765,
            "database.cache_size_kb": 786432,
        },
    )

    with paths.config_file.open("rb") as handle:
        raw = tomllib.load(handle)

    assert raw == {
        "auth": {"auth_token": 'slash\\quote"line\nnext', "ct0": "", "user_id": ""},
        "web": {"port": 8765},
        "database": {"cache_size_kb": 786432},
    }
    assert get_explicit_config_fields(paths) == [
        "auth.auth_token",
        "database.cache_size_kb",
        "web.port",
    ]


def test_update_config_values_keeps_sections_sparse(paths: XDGPaths) -> None:
    update_config_values(paths, {"sync.page_delay": 1.0})

    raw = tomllib.loads(paths.config_file.read_text(encoding="utf-8"))

    assert raw["sync"] == {"page_delay": 1.0}
    assert "database" not in raw
    assert set(raw) == {"auth", "sync"}


def test_update_config_values_removes_normal_override_and_empty_section(paths: XDGPaths) -> None:
    update_config_values(paths, {"database.cache_size_kb": 123})
    update_config_values(paths, {"database.cache_size_kb": None})

    assert tomllib.loads(paths.config_file.read_text(encoding="utf-8")) == {
        "auth": {"auth_token": "", "ct0": "", "user_id": ""}
    }


def test_update_config_values_removes_value_returned_to_default(paths: XDGPaths) -> None:
    update_config_values(paths, {"sync.page_delay": 1.0})
    update_config_values(paths, {"sync.page_delay": 2.0})

    assert "[sync]" not in paths.config_file.read_text(encoding="utf-8")


def test_update_config_values_none_restores_auth_placeholder(paths: XDGPaths) -> None:
    update_config_values(paths, {"auth.ct0": "secret"})
    update_config_values(paths, {"auth.ct0": None})

    raw = tomllib.loads(paths.config_file.read_text(encoding="utf-8"))
    assert raw["auth"]["ct0"] == ""
    assert set(raw["auth"]) == {"auth_token", "ct0", "user_id"}


def test_update_config_values_rejects_unknown_paths(paths: XDGPaths) -> None:
    with pytest.raises(ValueError, match="Unknown configuration field"):
        update_config_values(paths, {"web.auto_start": True})


def test_database_defaults_are_fixed() -> None:
    assert DatabaseConfig().cache_size_kb == 524288
    assert DatabaseConfig().mmap_size_bytes == 1073741824


def test_avatar_cache_limit_defaults_are_fixed() -> None:
    assert WebConfig().avatar_cache_limit_enabled is True
    assert WebConfig().avatar_cache_limit_mb == 512


def test_legacy_web_auto_start_setting_is_ignored_and_not_exposed() -> None:
    config = AppConfig.model_validate({"web": {"auto_start": True}})

    assert "auto_start" not in WebConfig.model_fields
    assert "auto_start" not in config.web.model_dump()
    assert "web.auto_start" not in get_config_ui_schema()["whitelist"]


def test_config_ui_schema_only_references_real_fields_and_masks_secrets() -> None:
    schema = get_config_ui_schema()
    config_dump = AppConfig().model_dump()

    def exists(path: str) -> bool:
        section, field = path.split(".", 1)
        return section in config_dump and field in config_dump[section]

    basic = set(schema["whitelist"])
    blocked = set(schema["blacklist"])
    editable = {
        f"{section}.{field}" for section, fields in config_dump.items() for field in fields
    } - blocked
    advanced = editable - basic

    assert basic
    assert basic.isdisjoint(blocked)
    assert all(exists(path) for path in basic | blocked)
    assert set(schema["full_width"]) <= editable
    assert set(schema["types"]) <= editable
    assert all(f"tagging.{field}" in blocked for field in TaggingConfig.model_fields)
    assert {path for path in blocked if path.startswith("auth.")} == {
        "auth.auth_token",
        "auth.ct0",
        "auth.user_id",
    }
    assert "web.password_hash" in blocked
    assert all(path in schema["labels"] for path in editable)
    assert all(path in schema["descriptions"] for path in editable)
    assert {
        "sync.page_delay",
        "sync.detail_delay",
        "sync.max_retries",
        "sync.backoff_base",
        "sync.detail_max_retries",
        "sync.detail_backoff_base",
        "sync.cooldown_threshold",
        "sync.cooldown_duration",
        "sync.timeout",
        "sync.max_linked_depth",
        "database.cache_size_kb",
        "database.mmap_size_bytes",
    } <= advanced
