"""Config models and XDG path helpers."""

from __future__ import annotations

import copy
import fcntl
import os
import stat
import threading
import tomllib
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import tomli_w
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    field_validator,
    model_validator,
)

from tweetnook.core.fileio import atomic_write_text

config_lock = threading.RLock()
_config_generations: dict[str, int] = {}

APP_NAME = "tweetnook"
CONFIG_FILENAME = "config.toml"
QUERY_ID_CACHE_FILENAME = "query-ids.json"
LOCK_FILENAME = "sync.lock"
ACTIVITY_STATUS_FILENAME = "activity-status.json"
COMMAND_LOCK_FILENAME = "command.lock"
WEB_PID_FILENAME = ".web.pid"
SERVER_LOG_FILENAME = "server.log"
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
CONFIG_SECTION_ORDER = (
    "auth",
    "sync",
    "web",
    "schedule",
    "check",
    "fxtwitter",
    "activity",
    "database",
    "tagging",
    "smart_search",
    "replay",
)


class AuthConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    auth_token: str | None = None
    ct0: str | None = None
    user_id: str | None = None


SyncCollection = Literal["bookmarks", "likes", "tweets"]
SYNC_COLLECTION_ORDER: tuple[SyncCollection, ...] = ("bookmarks", "likes", "tweets")
DEFAULT_SYNC_COLLECTIONS: tuple[SyncCollection, ...] = ("bookmarks", "likes")


class SyncConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    collections: list[SyncCollection] = Field(
        default_factory=lambda: list(DEFAULT_SYNC_COLLECTIONS), min_length=1
    )

    page_delay: float = Field(default=2.0, ge=0, allow_inf_nan=False)
    detail_delay: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    max_retries: int = Field(default=3, ge=0)
    backoff_base: float = Field(default=2.0, ge=0, allow_inf_nan=False)
    detail_max_retries: int = Field(default=2, ge=0)
    detail_backoff_base: float = Field(default=30.0, ge=0, allow_inf_nan=False)
    cooldown_threshold: int = Field(default=3, ge=1)
    cooldown_duration: float = Field(default=300.0, ge=0, allow_inf_nan=False)
    timeout: float = Field(default=30.0, ge=1.0, allow_inf_nan=False)
    max_linked_depth: int = Field(default=50, ge=0)
    reply_linked_depth: int = Field(default=0, ge=0, le=1)
    reply_linked_limit: int = Field(default=1, ge=1)

    @field_validator("collections")
    @classmethod
    def order_collections(cls, value: list[SyncCollection]) -> list[SyncCollection]:
        """Keep one canonical order so the same choice always compares equal."""

        return [collection for collection in SYNC_COLLECTION_ORDER if collection in value]


class WebConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    password_hash: str | None = None
    host: str = Field(default="0.0.0.0", min_length=1)
    port: int = Field(default=8000, ge=1, le=65535)
    fetch_avatars: bool = True
    avatar_cache_limit_enabled: bool = True
    avatar_cache_limit_mb: int = Field(default=512, ge=1)
    analytics_refresh_time: str = Field(default="05:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    dev_replay_source: bool = False


class ScheduleConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    cadence: Literal["hours", "daily", "weekly", "monthly"] = "daily"
    every_hours: int = Field(default=6, ge=1, le=720)
    time: str = Field(default="03:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    randomize_time: bool = True
    random_offset_min_hours: float = Field(default=0.0, ge=0.0, le=24.0, allow_inf_nan=False)
    random_offset_max_hours: float = Field(default=2.0, ge=0.0, le=24.0, allow_inf_nan=False)
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


class CheckConfig(BaseModel):
    """Availability check of saved tweets, their context, and unavailable tweets."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    time: str = Field(default="04:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    cadence: Literal["daily", "weekly", "monthly"] = "weekly"
    weekday: int = Field(default=0, ge=0, le=6)
    day_of_month: int = Field(default=1, ge=1, le=31)
    context_enabled: bool = False
    context_days: int = Field(default=60, ge=1, le=365)
    detail_budget: int = Field(default=200, ge=0, le=10_000)


class FxTwitterConfig(BaseModel):
    """Cookie-free post lookups through the FxTwitter API (or a self-hosted copy)."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    base_url: str = Field(default="https://api.fxtwitter.com", min_length=1)
    concurrency: int = Field(default=4, ge=1, le=16)
    timeout: float = Field(default=30.0, ge=1.0, allow_inf_nan=False)


class ActivityConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    max_runs: int = Field(default=200, ge=1, le=10_000)
    retention_days: int = Field(default=180, ge=1, le=3650)


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
    google_search: bool = False
    free_batch_size: int = Field(default=20, ge=1, le=20)
    free_rpm: int = Field(default=10, ge=1)
    free_rpd: int = Field(default=20, ge=1)
    processing_tier: Literal["standard", "flex"] = "flex"
    daily_spend_limit_usd: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    unlimited_spend: bool = False
    search_safety_reserve: int = Field(default=100, ge=1, le=1000)
    unknown_billing_policy: Literal["reserve", "stop"] = "reserve"
    unknown_request_reserve_usd: float = Field(default=0.02, gt=0, allow_inf_nan=False)
    max_unknown_requests_per_day: int = Field(default=3, ge=1, le=100)
    search_unknown_policy: Literal["reserve", "disable"] = "reserve"
    max_unknown_search_requests: int = Field(default=3, ge=1, le=100)
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


class SmartSearchConfig(BaseModel):
    """Settings for local multimodal image Smart Search (CLIP/SigLIP ONNX)."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    model: str = "ViT-B-16-SigLIP"
    batch_size: int = Field(default=16, ge=1, le=256)
    min_score: float = Field(default=0.0, ge=-1.0, le=1.0)
    include_video_thumbnails: bool = True
    include_quoted_media: bool = True
    include_thread_media: bool = False


class ReplayConfig(BaseModel):
    """Developer replay source for a test install (see ``tweetnook.core.dev_replay``).

    While ``url`` is set, every Twitter/X request this install makes goes to that
    TweetNook replay source instead of Twitter/X, and every other host is blocked.
    """

    model_config = ConfigDict(extra="ignore")

    url: str | None = None
    password: str | None = None
    placeholder_media: bool = True
    skip_quote_expansion: bool = False
    skip_link_previews: bool = False


class AppConfig(BaseModel):
    _generation: int = PrivateAttr(default=0)
    _config_path: str | None = PrivateAttr(default=None)
    _auth_env_values: dict[str, str] | None = PrivateAttr(default=None)

    model_config = ConfigDict(extra="ignore")

    auth: AuthConfig = Field(default_factory=AuthConfig)
    sync: SyncConfig = Field(default_factory=SyncConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    check: CheckConfig = Field(default_factory=CheckConfig)
    fxtwitter: FxTwitterConfig = Field(default_factory=FxTwitterConfig)
    activity: ActivityConfig = Field(default_factory=ActivityConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    tagging: TaggingConfig = Field(default_factory=TaggingConfig)
    smart_search: SmartSearchConfig = Field(default_factory=SmartSearchConfig)
    replay: ReplayConfig = Field(default_factory=ReplayConfig)

    @property
    def fx_enabled(self) -> bool:
        # Replay installs must never reach a host other than their replay source.
        return self.fxtwitter.enabled and not self.replay.url

    @property
    def check_runs(self) -> bool:
        """The availability check is FxTwitter lookups, so it runs only while FxTwitter is on.
        While it runs it owns retrying unavailable tweets; otherwise sync does that."""
        return self.check.enabled and self.fx_enabled


def resolve_auth_config(
    config: AppConfig, *, env: Mapping[str, str] | None = None
) -> tuple[AuthConfig, dict[str, str | None]]:
    """Select auth overlays once, retaining the provenance of a loaded snapshot."""
    selected = config._auth_env_values
    if selected is None:
        env = os.environ if env is None else env
        selected = {
            field: value
            for field, name in (
                ("auth_token", "TWEETNOOK_AUTH_TOKEN"),
                ("ct0", "TWEETNOOK_CT0"),
                ("user_id", "TWEETNOOK_USER_ID"),
            )
            if (value := env.get(name))
        }
        values = config.auth.model_dump() | selected
    else:
        values = config.auth.model_dump()
    auth = AuthConfig.model_validate({field: value or None for field, value in values.items()})
    sources = {
        field: ("env" if field in selected and selected[field] == value else "config")
        if value
        else None
        for field, value in auth.model_dump().items()
    }
    return auth, sources


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
    def models_cache_dir(self) -> Path:
        return self.cache_dir / "models"

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
    def web_pid_file(self) -> Path:
        return self.data_dir / WEB_PID_FILENAME

    @property
    def server_log_file(self) -> Path:
        return self.data_dir / "logs" / SERVER_LOG_FILENAME

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
    def web_session_key_file(self) -> Path:
        return self.data_dir / "web-session.key"

    @property
    def schedule_state_file(self) -> Path:
        return self.data_dir / "schedule-state.json"

    @property
    def avatar_cache_state_file(self) -> Path:
        return self.data_dir / "avatar-cache-state.json"

    @property
    def core_avatar_state_file(self) -> Path:
        return self.data_dir / "core-avatar-state.json"

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
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError:
        return {}


@contextmanager
def config_writer(paths: XDGPaths) -> Iterator[None]:
    """Serialize supported writers across threads and processes, separate from archive locks."""
    with config_lock:
        paths.config_dir.mkdir(parents=True, exist_ok=True)
        with (paths.config_dir / "config.lock").open("a+b") as handle:
            _give_to_directory_owner(paths.config_dir / "config.lock")
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _give_to_directory_owner(path: Path) -> None:
    # A command run as root must not leave files the account that owns the config can't open.
    if os.geteuid() == 0:
        owner = path.parent.stat()
        os.chown(path, owner.st_uid, owner.st_gid)


def _upgraded_config(paths: XDGPaths) -> tuple[dict[str, Any], bool]:
    raw = _load_config_file(paths.config_file)
    changed = _ensure_auth_skeleton(raw)
    changed = _migrate_tagging_config(raw) or changed
    AppConfig.model_validate(_normalize_auth_placeholders(raw))
    return raw, changed


def upgrade_config(paths: XDGPaths | None = None) -> None:
    """Bootstrap and upgrade supported file formats explicitly at process startup.

    An already current file is left untouched, so commands still run when the config is
    mounted read-only or owned by another account.
    """
    paths = ensure_paths(paths or resolve_paths())
    _, changed = _upgraded_config(paths)
    if not changed:
        with suppress(OSError):
            if stat.S_IMODE(paths.config_file.stat().st_mode) != 0o600:
                paths.config_file.chmod(0o600)
        return
    try:
        with config_writer(paths):
            raw, changed = _upgraded_config(paths)
            if changed:
                _write_config_file(paths.config_file, raw)
    except OSError:
        # Loading applies the same upgrades in memory, so only a missing file is fatal.
        if not paths.config_file.exists():
            raise


def _ensure_auth_skeleton(raw: dict[str, Any]) -> bool:
    auth = raw.get("auth")
    changed = not isinstance(auth, dict)
    if not isinstance(auth, dict):
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


def _write_config_file(path: Path, raw: Mapping[str, Any]) -> None:
    section_names = [name for name in CONFIG_SECTION_ORDER if name in raw]
    section_names.extend(name for name in raw if name not in CONFIG_SECTION_ORDER)
    document = {
        section: {key: value for key, value in raw[section].items() if value is not None}
        for section in section_names
        if isinstance(raw[section], Mapping) and (section == "auth" or raw[section])
    }
    rendered = tomli_w.dumps(document)
    decoded = tomllib.loads(rendered)
    if decoded != document:
        raise ValueError("Configuration did not round-trip through TOML")
    AppConfig.model_validate(_normalize_auth_placeholders(decoded))
    # Publish beside the real file so a symlinked config.toml stays a symlink.
    target = path.resolve()
    atomic_write_text(target, rendered, mode=0o600)
    _give_to_directory_owner(target)
    key = str(target)
    _config_generations[key] = _config_generations.get(key, 0) + 1


def _env_float(env: Mapping[str, str], name: str) -> float | None:
    value = env.get(name)
    return float(value) if value is not None else None


def _env_int(env: Mapping[str, str], name: str) -> int | None:
    value = env.get(name)
    return int(value) if value is not None else None


def load_config(env: Mapping[str, str] | None = None) -> tuple[AppConfig, XDGPaths]:
    env = os.environ if env is None else env
    config, paths = load_config_at(resolve_paths(env), env)
    from tweetnook.core.dev_replay import configure_replay

    configure_replay(config.replay)
    return config, paths


def load_config_at(
    paths: XDGPaths, env: Mapping[str, str] | None = None
) -> tuple[AppConfig, XDGPaths]:
    env = os.environ if env is None else env
    with config_lock:
        raw = _load_config_file(paths.config_file)
        config = AppConfig.model_validate(_normalize_auth_placeholders(raw))
        config._config_path = str(paths.config_file.resolve())
        config._generation = _config_generations.get(config._config_path, 0)

    config.auth, auth_sources = resolve_auth_config(config, env=env)
    config._auth_env_values = {
        field: getattr(config.auth, field)
        for field, source in auth_sources.items()
        if source == "env"
    }
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
        "reply_linked_depth": _env_int(env, "TWEETNOOK_REPLY_LINKED_DEPTH"),
        "reply_linked_limit": _env_int(env, "TWEETNOOK_REPLY_LINKED_LIMIT"),
    }
    sync_updates = {key: value for key, value in sync_updates.items() if value is not None}

    if sync_updates:
        config.sync = SyncConfig.model_validate(config.sync.model_dump() | sync_updates)
    api_key = env.get("TWEETNOOK_GEMINI_API_KEY") or env.get("GEMINI_API_KEY")
    if api_key:
        config.tagging = TaggingConfig.model_validate(
            config.tagging.model_dump() | {"api_key": api_key}
        )
    return config, paths


def _valid_config_paths() -> set[str]:
    return {
        f"{section}.{field}"
        for section, model_field in AppConfig.model_fields.items()
        if isinstance(model_field.annotation, type)
        and issubclass(model_field.annotation, BaseModel)
        for field in model_field.annotation.model_fields
    }


def config_snapshot_is_current(config: AppConfig) -> bool:
    return config._config_path is None or config._generation >= _config_generations.get(
        config._config_path, 0
    )


def update_config_values(paths: XDGPaths, changes: Mapping[str, Any]) -> None:
    with config_writer(paths):
        _update_config_values(paths, changes)


def _update_config_values(paths: XDGPaths, changes: Mapping[str, Any]) -> None:
    raw = _load_config_file(paths.config_file)
    _migrate_tagging_config(raw)
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


def config_error_message(error: Exception) -> str:
    """Describe validation failures without Pydantic's secret-bearing input/context."""
    if isinstance(error, ValidationError):
        return "; ".join(
            f"{'.'.join(map(str, item['loc']))}: {item['msg']}"
            for item in error.errors(include_input=False, include_context=False, include_url=False)
        )
    return str(error)


def initialize_config(env: Mapping[str, str] | None = None) -> tuple[AppConfig, XDGPaths]:
    """Explicit CLI/startup bootstrap followed by a read-only load."""
    upgrade_config(resolve_paths(env))
    return load_config(env)
