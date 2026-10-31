from __future__ import annotations

import pytest
from pydantic import ValidationError

from tweetnook.config import AppConfig, ensure_paths, load_config, resolve_paths


def _env(tmp_path):
    return {
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
    }


FLOAT_SETTINGS = [
    ("sync", "page_delay"),
    ("sync", "detail_delay"),
    ("sync", "backoff_base"),
    ("sync", "detail_backoff_base"),
    ("sync", "cooldown_duration"),
    ("sync", "timeout"),
    ("fxtwitter", "timeout"),
    ("schedule", "random_offset_min_hours"),
    ("schedule", "random_offset_max_hours"),
    ("tagging", "daily_spend_limit_usd"),
    ("tagging", "unknown_request_reserve_usd"),
]


@pytest.mark.parametrize(("section", "field"), FLOAT_SETTINGS)
@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_operational_and_budget_settings_require_finite_values(section, field, value):
    with pytest.raises(ValidationError):
        AppConfig.model_validate({section: {field: value}})


@pytest.mark.parametrize(("section", "field"), FLOAT_SETTINGS)
def test_nonfinite_toml_settings_are_rejected(tmp_path, section, field):
    env = _env(tmp_path)
    paths = ensure_paths(resolve_paths(env))
    paths.config_file.write_text(f"[{section}]\n{field} = inf\n", encoding="utf-8")

    with pytest.raises(ValidationError):
        load_config(env)


def test_finite_budget_and_explicit_unlimited_remain_distinct():
    limited = AppConfig.model_validate(
        {"tagging": {"enabled": True, "api_mode": "paid", "daily_spend_limit_usd": 2.5}}
    )
    unlimited = AppConfig.model_validate(
        {"tagging": {"enabled": True, "api_mode": "paid", "unlimited_spend": True}}
    )

    assert limited.tagging.daily_spend_limit_usd == 2.5
    assert limited.tagging.unlimited_spend is False
    assert unlimited.tagging.daily_spend_limit_usd is None
    assert unlimited.tagging.unlimited_spend is True
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"tagging": {"enabled": True, "api_mode": "paid"}})
    with pytest.raises(ValidationError):
        AppConfig.model_validate(
            {"tagging": {"unlimited_spend": True, "daily_spend_limit_usd": "inf"}}
        )


def test_zero_delays_and_unset_optional_budget_remain_valid():
    config = AppConfig.model_validate(
        {
            "sync": {
                "page_delay": 0,
                "detail_delay": 0,
                "backoff_base": 0,
                "detail_backoff_base": 0,
                "cooldown_duration": 0,
            },
            "tagging": {"daily_spend_limit_usd": None},
        }
    )
    assert config.sync.page_delay == config.sync.cooldown_duration == 0
    assert config.tagging.daily_spend_limit_usd is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("page_delay", "-1"),
        ("detail_delay", "-1"),
        ("max_retries", "-5"),
        ("backoff_base", "-1"),
        ("detail_max_retries", "-1"),
        ("detail_backoff_base", "-1"),
        ("cooldown_threshold", "0"),
        ("cooldown_duration", "-1"),
        ("timeout", "0"),
        ("max_linked_depth", "-1"),
        ("reply_linked_depth", "9"),
        ("reply_linked_limit", "0"),
        ("page_delay", "nan"),
        ("timeout", "inf"),
    ],
)
def test_environment_and_toml_enforce_the_same_sync_bounds(tmp_path, field, value):
    env = _env(tmp_path)
    paths = ensure_paths(resolve_paths(env))
    paths.config_file.write_text(f"[sync]\n{field} = {value}\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(env)
    paths.config_file.write_text("", encoding="utf-8")
    env[f"TWEETNOOK_{field.upper()}"] = value

    with pytest.raises(ValidationError):
        load_config(env)
    assert paths.config_file.read_text() == ""
