from __future__ import annotations

import pytest

from tweetnook.auth import resolve_auth_bundle
from tweetnook.config import AppConfig, AuthConfig
from tweetnook.exceptions import AuthResolutionError


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


def test_resolve_auth_bundle_does_not_probe_status_callback() -> None:
    messages: list[str] = []
    config = AppConfig(auth=AuthConfig(auth_token="token", ct0="csrf"))

    resolve_auth_bundle(config, env={}, status=messages.append)

    assert messages == []


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
