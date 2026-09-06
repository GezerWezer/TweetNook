from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from tweetnook.config import AppConfig, load_config
from tweetnook.web.routes import config as config_routes


def _env(tmp_path: Path) -> dict[str, str]:
    return {
        "XDG_CONFIG_HOME": str(tmp_path / "config-home"),
        "XDG_DATA_HOME": str(tmp_path / "data-home"),
        "XDG_CACHE_HOME": str(tmp_path / "cache-home"),
    }


def _use_temp_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    env = _env(tmp_path)
    config, paths = load_config(env)
    monkeypatch.setattr("tweetnook.config.load_config", lambda: load_config(env))
    return config, paths


def test_get_config_returns_masked_values_and_explicit(
    monkeypatch, make_web_client, tmp_path: Path
) -> None:
    _, paths = _use_temp_config(monkeypatch, tmp_path)
    paths.config_file.write_text(
        """
[auth]
auth_token = "auth-secret"
ct0 = "csrf-secret"
user_id = "123"

[web]
password_hash = "password-secret"
port = 9000

[database]
cache_size_kb = 786432

[tagging]
api_key = "gemini-secret"
""".lstrip(),
        encoding="utf-8",
    )
    client = make_web_client(config_routes.router)

    response = client.get("/api/config")

    assert response.status_code == 200
    data = response.json()
    assert data["values"]["auth"]["auth_token"] == "********"
    assert data["values"]["auth"]["ct0"] == "********"
    assert data["values"]["web"]["password_hash"] == "********"
    assert data["values"]["tagging"]["api_key"] == "********"
    assert data["values"]["web"]["port"] == 9000
    assert data["explicit"] == [
        "auth.auth_token",
        "auth.ct0",
        "auth.user_id",
        "database.cache_size_kb",
        "tagging.api_key",
        "web.port",
    ]


def test_get_config_blank_auth_placeholders_are_unset(
    monkeypatch, make_web_client, tmp_path: Path
) -> None:
    _use_temp_config(monkeypatch, tmp_path)
    client = make_web_client(config_routes.router)

    data = client.get("/api/config").json()

    assert data["values"]["auth"]["auth_token"] is None
    assert data["values"]["auth"]["ct0"] is None
    assert data["values"]["auth"]["user_id"] is None
    assert data["explicit"] == []


def test_get_defaults_returns_complete_valid_config(make_web_client) -> None:
    client = make_web_client(config_routes.router)
    response = client.get("/api/config/defaults")
    assert response.status_code == 200
    assert AppConfig.model_validate(response.json()) == AppConfig()


def test_get_schema_returns_ui_contract(make_web_client) -> None:
    client = make_web_client(config_routes.router)
    response = client.get("/api/config/schema")
    assert response.status_code == 200
    schema = response.json()
    assert "tagging.api_key" in schema["blacklist"]
    assert not any(path.startswith("tagging.") for path in schema["whitelist"])
    assert "auth.auth_token" in schema["blacklist"]
    assert "web.password_hash" in schema["blacklist"]


def test_post_config_accepts_one_field_and_returns_refreshed_state(
    monkeypatch, make_web_client, tmp_path: Path
) -> None:
    _, paths = _use_temp_config(monkeypatch, tmp_path)
    client = make_web_client(config_routes.router)

    response = client.post("/api/config", json={"changes": {"web.port": 8123}})

    assert response.status_code == 200
    assert response.json()["values"]["web"]["port"] == 8123
    assert response.json()["explicit"] == ["web.port"]
    raw = tomllib.loads(paths.config_file.read_text(encoding="utf-8"))
    assert raw["web"] == {"port": 8123}


def test_post_config_null_resets_field(monkeypatch, make_web_client, tmp_path: Path) -> None:
    _, paths = _use_temp_config(monkeypatch, tmp_path)
    paths.config_file.write_text(
        '[auth]\nauth_token = ""\nct0 = ""\nuser_id = ""\n\n[web]\nport = 8123\n',
        encoding="utf-8",
    )
    client = make_web_client(config_routes.router)

    response = client.post("/api/config", json={"changes": {"web.port": None}})

    assert response.status_code == 200
    assert response.json()["values"]["web"]["port"] == 8000
    assert response.json()["explicit"] == []
    assert "[web]" not in paths.config_file.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"web.password_hash": "new-hash"}, "cannot be changed"),
        ({"auth.auth_token": "********"}, "Masked secret"),
        ({"web.auto_start": True}, "Unknown configuration field"),
        ({"tagging.free_rpd": 0}, "greater than or equal to 1"),
    ],
)
def test_post_config_rejects_blocked_masked_unknown_and_invalid_values(
    monkeypatch, make_web_client, tmp_path: Path, changes: dict, message: str
) -> None:
    _use_temp_config(monkeypatch, tmp_path)
    client = make_web_client(config_routes.router)

    response = client.post("/api/config", json={"changes": changes})

    assert response.status_code == 400
    assert message in response.json()["detail"]


def test_post_config_leaves_unrelated_raw_settings_untouched(
    monkeypatch, make_web_client, tmp_path: Path
) -> None:
    _, paths = _use_temp_config(monkeypatch, tmp_path)
    paths.config_file.write_text(
        """
[auth]
auth_token = ""
ct0 = ""
user_id = ""

[sync]
page_delay = 1

[tagging]
enabled = true
""".lstrip(),
        encoding="utf-8",
    )
    client = make_web_client(config_routes.router)

    response = client.post("/api/config", json={"changes": {"web.port": 9123}})

    assert response.status_code == 200
    raw = tomllib.loads(paths.config_file.read_text(encoding="utf-8"))
    assert raw["sync"] == {"page_delay": 1}
    assert raw["tagging"] == {"enabled": True}
    assert raw["web"] == {"port": 9123}


@pytest.mark.parametrize(
    ("path", "attribute"),
    [
        ("/api/config", "load_config"),
        ("/api/config/defaults", "AppConfig"),
        ("/api/config/schema", "get_config_ui_schema"),
    ],
)
def test_get_config_endpoints_translate_failures_to_500(
    monkeypatch, make_web_client, path: str, attribute: str
) -> None:
    def fail(*_args, **_kwargs):
        raise RuntimeError("config unavailable")

    monkeypatch.setattr(f"tweetnook.config.{attribute}", fail)
    client = make_web_client(config_routes.router)
    response = client.get(path)
    assert response.status_code == 500
    assert response.json() == {"detail": "config unavailable"}


def test_post_config_translates_write_failure_to_400(
    monkeypatch, make_web_client, tmp_path: Path
) -> None:
    _use_temp_config(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "tweetnook.config.update_config_values",
        lambda *_args: (_ for _ in ()).throw(OSError("read-only config")),
    )
    client = make_web_client(config_routes.router)
    response = client.post("/api/config", json={"changes": {"web.port": 8123}})
    assert response.status_code == 400
    assert response.json() == {"detail": "read-only config"}


def test_config_endpoints_require_authentication(make_web_client) -> None:
    client = make_web_client(config_routes.router, password="secret")
    assert client.get("/api/config/defaults").status_code == 401
    assert client.post("/api/config", json={"changes": {"web.port": 8123}}).status_code == 401
