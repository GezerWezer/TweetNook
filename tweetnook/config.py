"""Config models and XDG path helpers."""

from __future__ import annotations

import copy
import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

APP_NAME = "tweetnook"
API_BASE_URL = "https://x.com/i/api/graphql"
CLIENT_WEB_BUNDLE_BASE = "https://abs.twimg.com/responsive-web/client-web"
DISCOVERY_PAGE_URL = "https://x.com/?lang=en"
BUNDLE_URL_REGEX = r"https://abs\.twimg\.com/responsive-web/client-web/[A-Za-z0-9_.~-]+\.js"
PUBLIC_BEARER_TOKEN = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16c"
    "HjhLTvJu4FA33AGWWjCpTnA"
)
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
)
CONFIG_FILENAME = "config.toml"
QUERY_ID_CACHE_FILENAME = "query-ids.json"
LOCK_FILENAME = "sync.lock"
ACTIVITY_STATUS_FILENAME = "activity-status.json"
COMMAND_LOCK_FILENAME = "command.lock"
DB_FILENAME = "archive.db"
DEFAULT_SQLITE_CACHE_SIZE_KB = 512 * 1024
DEFAULT_SQLITE_MMAP_SIZE_BYTES = 1024**3
AUTH_PLACEHOLDER_FIELDS = ("auth_token", "ct0", "user_id")
LEGACY_BROWSER_AUTH_FIELDS = (
    "browser",
    "browser_profile",
    "browser_profile_path",
    "firefox_profile_path",
)
CONFIG_SECTION_ORDER = ("auth", "sync", "web", "schedule", "activity", "database", "tagging")


class AuthConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    auth_token: str | None = None
    ct0: str | None = None
    user_id: str | None = None


class SyncConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    page_delay: float = Field(default=2.0, ge=0)
    detail_delay: float = Field(default=0.0, ge=0)
    max_retries: int = Field(default=3, ge=0)
    backoff_base: float = Field(default=2.0, ge=0)
    detail_max_retries: int = Field(default=2, ge=0)
    detail_backoff_base: float = Field(default=30.0, ge=0)
    cooldown_threshold: int = Field(default=3, ge=1)
    cooldown_duration: float = Field(default=300.0, ge=0)
    timeout: float = Field(default=30.0, ge=1.0)
    max_linked_depth: int = Field(default=1, ge=0)


class WebConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    password_hash: str | None = None
    host: str = Field(default="0.0.0.0", min_length=1)
    port: int = Field(default=8000, ge=1, le=65535)
    fetch_avatars: bool = True


class ScheduleConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    cadence: Literal["hours", "daily", "weekly", "monthly"] = "daily"
    every_hours: int = Field(default=6, ge=1, le=720)
    time: str = Field(default="03:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    randomize_time: bool = True
    random_offset_min_hours: float = Field(default=0.0, ge=0.0, le=24.0)
    random_offset_max_hours: float = Field(default=2.0, ge=0.0, le=24.0)
    weekday: int = Field(default=0, ge=0, le=6)
    day_of_month: int = Field(default=1, ge=1, le=31)
    timezone: str = Field(default="local", min_length=1)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        if value == "local":
            return value
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown timezone: {value}") from exc
        return value

    @model_validator(mode="after")
    def validate_random_offset_window(self) -> ScheduleConfig:
        if (
            self.cadence != "hours"
            and self.randomize_time
            and self.random_offset_min_hours > self.random_offset_max_hours
        ):
            raise ValueError("Random added delay minimum must not exceed the maximum")
        return self


class ActivityConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    max_runs: int = Field(default=100, ge=1, le=10_000)
    retention_days: int = Field(default=90, ge=1, le=3650)


class DatabaseConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    cache_size_kb: int = Field(default=DEFAULT_SQLITE_CACHE_SIZE_KB, ge=0)
    mmap_size_bytes: int = Field(default=DEFAULT_SQLITE_MMAP_SIZE_BYTES, ge=0)


class TaggingConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    api_key: str | None = None
    api_mode: Literal["free", "paid"] = "free"
    model: str = Field(default="gemini-3.6-flash", min_length=1)
    thinking_level: Literal["high", "medium", "low", "none"] = "high"
    google_search: bool = True
    free_batch_size: int = Field(default=20, ge=1, le=20)
    free_rpm: int = Field(default=10, ge=1)
    free_rpd: int = Field(default=250, ge=1)
    processing_tier: Literal["standard", "flex"] = "standard"
    daily_spend_limit_usd: float | None = Field(default=None, gt=0)
    unlimited_spend: bool = False
    search_safety_reserve: int = Field(default=100, ge=1, le=1000)
    tagging_context: list[str] = Field(default_factory=list, max_length=200)
    additional_instructions: str | None = Field(default=None, max_length=4000)
    max_media_size_mb: int = Field(default=100, ge=1)

    @field_validator("tagging_context", mode="before")
    @classmethod
    def normalize_tagging_context(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = value.replace(",", "\n").splitlines()
        if not isinstance(value, list):
            raise ValueError("Tagging context must be a list or multiline string")
        hints: list[str] = []
        seen: set[str] = set()
        for item in value:
            hint = str(item).strip()
            folded = hint.casefold()
            if hint and folded not in seen:
                hints.append(hint)
                seen.add(folded)
        return hints

    @field_validator("additional_instructions", mode="before")
    @classmethod
    def normalize_additional_instructions(cls, value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_paid_spend_control(self) -> TaggingConfig:
        if (
            self.enabled
            and self.api_mode == "paid"
            and not self.unlimited_spend
            and self.daily_spend_limit_usd is None
        ):
            raise ValueError("Paid automated tagging requires a daily spending limit or Unlimited")
        return self


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    auth: AuthConfig = Field(default_factory=AuthConfig)
    sync: SyncConfig = Field(default_factory=SyncConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    activity: ActivityConfig = Field(default_factory=ActivityConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    tagging: TaggingConfig = Field(default_factory=TaggingConfig)


class XDGPaths(BaseModel):
    """Resolved application paths."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    config_dir: Path
    data_dir: Path
    cache_dir: Path

    @property
    def config_file(self) -> Path:
        return self.config_dir / CONFIG_FILENAME

    @property
    def query_id_cache_file(self) -> Path:
        return self.cache_dir / QUERY_ID_CACHE_FILENAME

    @property
    def lock_file(self) -> Path:
        return self.data_dir / LOCK_FILENAME

    @property
    def activity_status_file(self) -> Path:
        return self.data_dir / ACTIVITY_STATUS_FILENAME

    @property
    def command_lock_file(self) -> Path:
        return self.data_dir / COMMAND_LOCK_FILENAME

    @property
    def staged_archive_file(self) -> Path:
        return self.data_dir / "setup" / "archive.zip"

    @property
    def setup_state_file(self) -> Path:
        return self.data_dir / "setup" / "state.json"

    @property
    def notices_file(self) -> Path:
        return self.data_dir / "notices.json"

    @property
    def schedule_state_file(self) -> Path:
        return self.data_dir / "schedule-state.json"

    @property
    def activity_runs_dir(self) -> Path:
        return self.data_dir / "activity" / "runs"

    @property
    def ai_usage_dir(self) -> Path:
        return self.data_dir / "activity" / "ai-usage"

    @property
    def database_path(self) -> Path:
        return self.data_dir / DB_FILENAME

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def database_file(self) -> Path:
        """Backward-compatible alias for older callers/tests."""
        return self.database_path


def resolve_paths(env: Mapping[str, str] | None = None) -> XDGPaths:
    import platformdirs

    env = os.environ if env is None else env
    # Allow explicit env-var overrides; otherwise use platformdirs for
    # cross-platform defaults (XDG on Linux, ~/Library on macOS, %APPDATA% on Windows).
    if raw := env.get("XDG_CONFIG_HOME"):
        config_dir = Path(raw).expanduser() / APP_NAME
    else:
        config_dir = Path(platformdirs.user_config_dir(APP_NAME))

    if raw := env.get("XDG_DATA_HOME"):
        data_dir = Path(raw).expanduser() / APP_NAME
    else:
        data_dir = Path(platformdirs.user_data_dir(APP_NAME))

    if raw := env.get("XDG_CACHE_HOME"):
        cache_dir = Path(raw).expanduser() / APP_NAME
    else:
        cache_dir = Path(platformdirs.user_cache_dir(APP_NAME))

    return XDGPaths(
        config_dir=config_dir,
        data_dir=data_dir,
        cache_dir=cache_dir,
    )


def ensure_paths(paths: XDGPaths) -> XDGPaths:
    for path in (paths.config_dir, paths.data_dir, paths.cache_dir):
        path.mkdir(parents=True, exist_ok=True)
    return paths


def _load_config_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raw: dict[str, Any] = {"auth": {key: "" for key in AUTH_PLACEHOLDER_FIELDS}}
        _write_config_file(path, raw)
        return raw

    with path.open("rb") as handle:
        loaded = tomllib.load(handle)
    if not isinstance(loaded, dict):
        loaded = {}
    changed = _ensure_auth_skeleton(loaded)
    changed = _migrate_tagging_config(loaded) or changed
    if changed:
        _write_config_file(path, loaded)
    return loaded


def _ensure_auth_skeleton(raw: dict[str, Any]) -> bool:
    auth = raw.get("auth")
    changed = not isinstance(auth, dict)
    if changed:
        auth = {}
        raw["auth"] = auth
    for key in LEGACY_BROWSER_AUTH_FIELDS:
        if key in auth:
            del auth[key]
            changed = True
    for key in AUTH_PLACEHOLDER_FIELDS:
        if key not in auth:
            auth[key] = ""
            changed = True
    return changed


def _normalize_auth_placeholders(raw: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(raw)
    auth = normalized.get("auth")
    if isinstance(auth, dict):
        for key in AUTH_PLACEHOLDER_FIELDS:
            if auth.get(key) == "":
                auth[key] = None
    _migrate_tagging_config(normalized)
    return normalized


def _migrate_tagging_config(raw: dict[str, Any]) -> bool:
    tagging = raw.get("tagging")
    changed = False
    if isinstance(tagging, dict):
        legacy_text_model = tagging.pop("text_model", None)
        legacy_media_model = tagging.pop("media_model", None)
        legacy_text_thinking = tagging.pop("text_thinking_level", None)
        legacy_media_thinking = tagging.pop("media_thinking_level", None)
        legacy_spending_limit = tagging.pop("spending_limit_usd", None)
        legacy_rpd = tagging.pop("rpd", None)
        legacy_batch = tagging.pop("batch", None)
        legacy_limit = tagging.pop("limit", None)
        legacy_paid_concurrency = tagging.pop("paid_concurrency", None)
        changed = any(
            value is not None
            for value in (
                legacy_text_model,
                legacy_media_model,
                legacy_text_thinking,
                legacy_media_thinking,
                legacy_spending_limit,
                legacy_rpd,
                legacy_batch,
                legacy_limit,
                legacy_paid_concurrency,
            )
        )
        if legacy_text_model or legacy_media_model:
            tagging.setdefault("model", legacy_text_model or legacy_media_model)
        if legacy_text_thinking or legacy_media_thinking:
            tagging.setdefault("thinking_level", legacy_text_thinking or legacy_media_thinking)
        if legacy_spending_limit is not None:
            tagging.setdefault("daily_spend_limit_usd", legacy_spending_limit)
        if legacy_rpd:
            tagging.setdefault("free_rpd", legacy_rpd)
        if legacy_batch or legacy_limit:
            requested_batch = legacy_batch or legacy_limit
            if isinstance(requested_batch, int):
                tagging.setdefault("free_batch_size", min(max(requested_batch, 1), 20))
    return changed


def _format_toml_value(value: Any) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(_format_toml_value(item) for item in value) + "]"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    escaped = (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\b", "\\b")
        .replace("\t", "\\t")
        .replace("\n", "\\n")
        .replace("\f", "\\f")
        .replace("\r", "\\r")
    )
    return f'"{escaped}"'


def _write_config_file(path: Path, raw: Mapping[str, Any]) -> None:
    lines: list[str] = []
    section_names = [name for name in CONFIG_SECTION_ORDER if name in raw]
    section_names.extend(name for name in raw if name not in CONFIG_SECTION_ORDER)
    for section in section_names:
        fields = raw[section]
        if not isinstance(fields, Mapping) or (section != "auth" and not fields):
            continue
        lines.append(f"[{section}]")
        for key, value in fields.items():
            if value is not None:
                lines.append(f"{key} = {_format_toml_value(value)}")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def _env_float(env: Mapping[str, str], name: str) -> float | None:
    value = env.get(name)
    return float(value) if value is not None else None


def _env_int(env: Mapping[str, str], name: str) -> int | None:
    value = env.get(name)
    return int(value) if value is not None else None


def load_config(env: Mapping[str, str] | None = None) -> tuple[AppConfig, XDGPaths]:
    env = os.environ if env is None else env
    paths = ensure_paths(resolve_paths(env))
    raw = _load_config_file(paths.config_file)
    config = AppConfig.model_validate(_normalize_auth_placeholders(raw))

    auth_updates = {
        "auth_token": env.get("TWEETNOOK_AUTH_TOKEN"),
        "ct0": env.get("TWEETNOOK_CT0"),
        "user_id": env.get("TWEETNOOK_USER_ID"),
    }
    auth_updates = {key: value for key, value in auth_updates.items() if value is not None}
    sync_updates = {
        "page_delay": _env_float(env, "TWEETNOOK_PAGE_DELAY"),
        "detail_delay": _env_float(env, "TWEETNOOK_DETAIL_DELAY"),
        "max_retries": _env_int(env, "TWEETNOOK_MAX_RETRIES"),
        "backoff_base": _env_float(env, "TWEETNOOK_BACKOFF_BASE"),
        "detail_max_retries": _env_int(env, "TWEETNOOK_DETAIL_MAX_RETRIES"),
        "detail_backoff_base": _env_float(env, "TWEETNOOK_DETAIL_BACKOFF_BASE"),
        "cooldown_threshold": _env_int(env, "TWEETNOOK_COOLDOWN_THRESHOLD"),
        "cooldown_duration": _env_float(env, "TWEETNOOK_COOLDOWN_DURATION"),
        "timeout": _env_float(env, "TWEETNOOK_TIMEOUT"),
        "max_linked_depth": _env_int(env, "TWEETNOOK_MAX_LINKED_DEPTH"),
    }
    sync_updates = {key: value for key, value in sync_updates.items() if value is not None}

    if auth_updates:
        config.auth = config.auth.model_copy(update=auth_updates)
    if sync_updates:
        config.sync = config.sync.model_copy(update=sync_updates)
    api_key = env.get("TWEETNOOK_GEMINI_API_KEY") or env.get("GEMINI_API_KEY")
    if api_key:
        config.tagging = config.tagging.model_copy(update={"api_key": api_key})
    return config, paths


def _valid_config_paths() -> set[str]:
    return {
        f"{section}.{field}"
        for section, model_field in AppConfig.model_fields.items()
        for field in model_field.annotation.model_fields
    }


def update_config_values(paths: XDGPaths, changes: Mapping[str, Any]) -> None:
    raw = _load_config_file(paths.config_file)
    valid_paths = _valid_config_paths()
    defaults = AppConfig().model_dump()

    for path, value in changes.items():
        if path not in valid_paths:
            raise ValueError(f"Unknown configuration field: {path}")
        section, field = path.split(".", 1)
        section_data = raw.get(section)
        if not isinstance(section_data, dict):
            section_data = {}
            raw[section] = section_data

        if section == "auth" and field in AUTH_PLACEHOLDER_FIELDS:
            section_data[field] = "" if value is None else value
        elif value is None or value == defaults[section][field]:
            section_data.pop(field, None)
        else:
            section_data[field] = value

    for section in tuple(raw):
        if section != "auth" and isinstance(raw[section], dict) and not raw[section]:
            del raw[section]
    _ensure_auth_skeleton(raw)
    AppConfig.model_validate(_normalize_auth_placeholders(raw))
    _write_config_file(paths.config_file, raw)


def get_explicit_config_fields(paths: XDGPaths) -> list[str]:
    raw = _load_config_file(paths.config_file)
    valid_paths = _valid_config_paths()
    explicit: list[str] = []
    for section in CONFIG_SECTION_ORDER:
        fields = raw.get(section)
        if not isinstance(fields, dict):
            continue
        for field, value in fields.items():
            path = f"{section}.{field}"
            if path not in valid_paths or path == "web.password_hash":
                continue
            if section == "auth" and field in AUTH_PLACEHOLDER_FIELDS and value == "":
                continue
            explicit.append(path)
    return sorted(explicit)


def get_config_ui_schema() -> dict[str, Any]:
    return {
        "whitelist": [
            "web.fetch_avatars",
            "web.host",
            "web.port",
        ],
        "blacklist": [
            "auth.auth_token",
            "auth.ct0",
            "auth.user_id",
            "web.password_hash",
            "schedule.enabled",
            "schedule.cadence",
            "schedule.every_hours",
            "schedule.time",
            "schedule.randomize_time",
            "schedule.random_offset_min_hours",
            "schedule.random_offset_max_hours",
            "schedule.weekday",
            "schedule.day_of_month",
            "schedule.timezone",
            "activity.max_runs",
            "activity.retention_days",
            *[f"tagging.{field}" for field in TaggingConfig.model_fields],
        ],
        "types": {},
        "full_width": [],
        "select_options": {},
        "labels": {
            "auth": "Authentication",
            "sync": "Sync & Delays",
            "web": "Web Server",
            "schedule": "Scheduled Syncs",
            "activity": "Activity History",
            "database": "Database",
            "auth.auth_token": "Auth Token",
            "auth.ct0": "CT0 (CSRF Token)",
            "auth.user_id": "User ID",
            "sync.page_delay": "Page Delay (s)",
            "sync.detail_delay": "Detail Delay (s)",
            "sync.max_retries": "Max Retries",
            "sync.backoff_base": "Backoff Base (s)",
            "sync.detail_max_retries": "Detail Max Retries",
            "sync.detail_backoff_base": "Detail Backoff Base (s)",
            "sync.cooldown_threshold": "Cooldown Threshold",
            "sync.cooldown_duration": "Cooldown Duration (s)",
            "sync.timeout": "Timeout (s)",
            "sync.max_linked_depth": "Max Linked Depth",
            "web.host": "Host",
            "web.port": "Port",
            "web.fetch_avatars": "Fetch Avatars locally",
            "schedule.enabled": "Enable Scheduled Syncs",
            "schedule.cadence": "Schedule Frequency",
            "schedule.every_hours": "Every N Hours",
            "schedule.time": "Run Time",
            "schedule.randomize_time": "Randomize Run Time",
            "schedule.random_offset_min_hours": "Minimum Added Delay",
            "schedule.random_offset_max_hours": "Maximum Added Delay",
            "schedule.weekday": "Weekday",
            "schedule.day_of_month": "Day of Month",
            "schedule.timezone": "Time Zone",
            "activity.max_runs": "Maximum Saved Runs",
            "activity.retention_days": "Log Retention (Days)",
            "database.cache_size_kb": "Cache Size (KiB)",
            "database.mmap_size_bytes": "MMap Size (Bytes)",
        },
        "descriptions": {
            "auth.auth_token": "Your Twitter/X authentication token. See README",
            "auth.ct0": "Your CSRF token. See README",
            "auth.user_id": "Your numerical Twitter/X user ID.",
            "database.cache_size_kb": (
                "Maximum SQLite page-cache target per database connection. "
                "Default: 524288 KiB (512 MiB)."
            ),
            "database.mmap_size_bytes": (
                "Maximum portion of the database SQLite may access through memory-mapped I/O. "
                "Default: 1073741824 bytes (1 GiB)."
            ),
            "sync.page_delay": "How many seconds to wait between fetching pages of tweets.",
            "sync.detail_delay": (
                "How many seconds to wait between fetching individual tweet details."
            ),
            "sync.max_retries": (
                "How many times to retry fetching a timeline if Twitter/X rate-limits you."
            ),
            "sync.backoff_base": (
                "How much to multiply the wait time by after each failed timeline request."
            ),
            "sync.detail_max_retries": (
                "How many times to retry fetching a single tweet if Twitter/X rate-limits you."
            ),
            "sync.detail_backoff_base": (
                "How much to multiply the wait time by after each failed single tweet request."
            ),
            "sync.cooldown_threshold": (
                "How many consecutive rate-limit errors trigger a long cooldown pause."
            ),
            "sync.cooldown_duration": (
                "How many seconds to pause when a long cooldown is triggered."
            ),
            "sync.timeout": "How many seconds to wait before giving up on a slow network request.",
            "sync.max_linked_depth": (
                "How deep to go when fetching nested tweet replies or quoted links."
            ),
            "web.fetch_avatars": "Automatically download and cache user profile pictures.",
            "web.host": "The IP address the Web UI binds to (default is 0.0.0.0 for LAN access).",
            "web.port": "The port the Web UI runs on.",
            "schedule.enabled": "Run sync automatically from the always-on Web service.",
            "schedule.cadence": "Run every N hours, every day, every week, or every month.",
            "schedule.every_hours": "Hours between runs when the hourly cadence is selected.",
            "schedule.time": "Local or configured-zone time for daily, weekly, and monthly runs.",
            "schedule.randomize_time": "Add a random delay after the selected run time by default.",
            "schedule.random_offset_min_hours": (
                "Smallest added delay in hours; 0 keeps the selected time."
            ),
            "schedule.random_offset_max_hours": ("Largest added delay in hours."),
            "schedule.weekday": "Weekday for weekly runs, where Monday is zero.",
            "schedule.day_of_month": "Preferred calendar day for monthly runs.",
            "schedule.timezone": "IANA time zone name, or local for the system time zone.",
            "activity.max_runs": "Maximum number of completed command histories to retain.",
            "activity.retention_days": "Maximum age of completed command histories.",
        },
    }
