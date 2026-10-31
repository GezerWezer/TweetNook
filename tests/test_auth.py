from __future__ import annotations

import pytest

from tweetnook.auth import resolve_auth_bundle
from tweetnook.config import AppConfig, AuthConfig, load_config_at, update_config_values
from tweetnook.core.exceptions import AuthResolutionError


def test_resolve_auth_bundle_prefers_environment_over_config() -> None:
    config = AppConfig(
        auth=AuthConfig(
            auth_token="config-token",
            ct0="config-ct0",
            user_id="config-user",
        )
    )

    bundle = resolve_auth_bundle(
        config,
        env={
            "TWEETNOOK_AUTH_TOKEN": "env-token",
            "TWEETNOOK_CT0": "env-ct0",
            "TWEETNOOK_USER_ID": "env-user",
        },
    )

    assert bundle.auth_token == "env-token"
    assert bundle.ct0 == "env-ct0"
    assert bundle.user_id == "env-user"
    assert bundle.auth_token_source == "env"
    assert bundle.ct0_source == "env"
    assert bundle.user_id_source == "env"


def test_resolve_auth_bundle_uses_explicit_config_values() -> None:
    config = AppConfig(auth=AuthConfig(auth_token="config-token", ct0="config-ct0", user_id="42"))

    bundle = resolve_auth_bundle(config, env={})

    assert bundle.auth_token == "config-token"
    assert bundle.ct0 == "config-ct0"
    assert bundle.user_id == "42"
    assert bundle.auth_token_source == "config"
    assert bundle.ct0_source == "config"
    assert bundle.user_id_source == "config"


def test_resolve_auth_bundle_requires_explicit_session_values() -> None:
    with pytest.raises(AuthResolutionError, match="Settings → Setup") as exc_info:
        resolve_auth_bundle(AppConfig(), env={})

    assert "browser" not in str(exc_info.value).lower()


@pytest.mark.parametrize(("collection", "label"), [("likes", "Likes"), ("tweets", "Own-tweet")])
def test_user_id_is_required_for_user_scoped_collections(
    collection: str,
    label: str,
) -> None:
    bundle = resolve_auth_bundle(
        AppConfig(auth=AuthConfig(auth_token="token", ct0="csrf")),
        env={},
    )

    with pytest.raises(AuthResolutionError, match=label):
        bundle.validate_for_collection(collection)


@pytest.mark.parametrize(
    ("overrides", "values", "sources"),
    [
        ({}, ("config-token", "config-ct0", "42"), ("config", "config", "config")),
        (
            {"TWEETNOOK_AUTH_TOKEN": "env-token", "TWEETNOOK_USER_ID": "77"},
            ("env-token", "config-ct0", "77"),
            ("env", "config", "env"),
        ),
        (
            {"TWEETNOOK_AUTH_TOKEN": "", "TWEETNOOK_CT0": "", "TWEETNOOK_USER_ID": ""},
            ("config-token", "config-ct0", "42"),
            ("config", "config", "config"),
        ),
    ],
)
def test_loaded_and_direct_auth_share_selection_and_provenance(paths, overrides, values, sources):
    update_config_values(
        paths,
        {"auth.auth_token": "config-token", "auth.ct0": "config-ct0", "auth.user_id": "42"},
    )
    original = paths.config_file.read_bytes()
    direct = AppConfig(auth=AuthConfig(auth_token="config-token", ct0="config-ct0", user_id="42"))
    loaded, _ = load_config_at(paths, overrides)

    for bundle in (
        resolve_auth_bundle(direct, env=overrides),
        resolve_auth_bundle(loaded, env={}),
    ):
        assert (bundle.auth_token, bundle.ct0, bundle.user_id) == values
        assert (bundle.auth_token_source, bundle.ct0_source, bundle.user_id_source) == sources
    assert paths.config_file.read_bytes() == original
    assert set(loaded.auth.model_dump()) == {"auth_token", "ct0", "user_id"}


def test_loaded_auth_does_not_reselect_a_changed_process_environment(paths, monkeypatch):
    update_config_values(paths, {"auth.auth_token": "file-token", "auth.ct0": "file-ct0"})
    loaded, _ = load_config_at(paths, {"TWEETNOOK_AUTH_TOKEN": "selected-token"})
    monkeypatch.setenv("TWEETNOOK_AUTH_TOKEN", "later-token")
    monkeypatch.setenv("TWEETNOOK_CT0", "later-ct0")

    bundle = resolve_auth_bundle(loaded)

    assert bundle.auth_token == "selected-token"
    assert bundle.auth_token_source == "env"
    assert bundle.ct0 == "file-ct0"
    assert bundle.ct0_source == "config"


@pytest.mark.parametrize("loaded", [False, True])
def test_empty_override_without_saved_cookie_remains_missing(paths, loaded):
    env = {"TWEETNOOK_AUTH_TOKEN": "", "TWEETNOOK_CT0": ""}
    config = load_config_at(paths, env)[0] if loaded else AppConfig()

    with pytest.raises(AuthResolutionError, match="Missing Twitter/X session cookies"):
        resolve_auth_bundle(config, env=env)
