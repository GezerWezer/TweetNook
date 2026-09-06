"""Explicit Twitter/X session credential resolution."""

from __future__ import annotations

import os
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict

from tweetnook.config import AppConfig
from tweetnook.exceptions import AuthResolutionError


class ResolvedAuthBundle(BaseModel):
    model_config = ConfigDict(extra="ignore")

    auth_token: str
    ct0: str
    user_id: str | None = None
    auth_token_source: str
    ct0_source: str
    user_id_source: str | None = None

    def validate_for_collection(self, collection: str) -> None:
        if collection == "likes" and not self.user_id:
            raise AuthResolutionError(
                "Likes sync requires a numeric user_id. Set TWEETNOOK_USER_ID or "
                "auth.user_id in config.toml."
            )
        if collection == "tweets" and not self.user_id:
            raise AuthResolutionError(
                "Own-tweet sync requires a numeric user_id. Set TWEETNOOK_USER_ID or "
                "auth.user_id in config.toml."
            )


def resolve_auth_bundle(
    config: AppConfig,
    *,
    env: Mapping[str, str] | None = None,
    status=None,
) -> ResolvedAuthBundle:
    """Resolve credentials from explicit environment or config values only."""

    del status
    env = os.environ if env is None else env

    def pick(env_name: str, config_attr: str) -> tuple[str | None, str | None]:
        if value := env.get(env_name):
            return value, "env"
        if value := getattr(config.auth, config_attr):
            return value, "config"
        return None, None

    auth_token, auth_token_source = pick("TWEETNOOK_AUTH_TOKEN", "auth_token")
    ct0, ct0_source = pick("TWEETNOOK_CT0", "ct0")
    user_id, user_id_source = pick("TWEETNOOK_USER_ID", "user_id")

    missing = [name for name, value in {"auth_token": auth_token, "ct0": ct0}.items() if not value]
    if missing:
        raise AuthResolutionError(
            f"Missing Twitter/X session cookies: {', '.join(missing)}. Set "
            "TWEETNOOK_AUTH_TOKEN/TWEETNOOK_CT0 or add them in Settings → Setup."
        )

    return ResolvedAuthBundle(
        auth_token=auth_token,
        ct0=ct0,
        user_id=user_id,
        auth_token_source=auth_token_source or "unknown",
        ct0_source=ct0_source or "unknown",
        user_id_source=user_id_source,
    )
