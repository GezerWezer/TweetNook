# Configuration and paths

`config.py` owns the validated models, sparse TOML format, environment overlays and browser field schema. Paths resolve before files are loaded. Use `resolve_paths` for a read-only lookup and `ensure_paths` only when an operation should create directories.

## Load and save

`load_config` creates an auth skeleton when a config doesn't exist, migrates supported old tagging/auth fields, validates the TOML, then applies environment overlays. Empty auth placeholders become `None` for validation. Unknown fields are ignored by the models; they aren't a supported way to add settings.

`update_config_values` accepts known dotted paths, validates the resulting configuration and omits values equal to their defaults. Auth placeholders stay explicit. Resetting a field therefore often removes its line from TOML rather than writing the default value.

Use the config APIs rather than editing TOML strings in route handlers. Secrets are masked in web responses. Setup and tagging endpoints preserve unchanged masked secrets. Generic `POST /api/config` rejects masked placeholders; omit unchanged secrets. Environment values take precedence for that process and are not necessarily written to the file.

## Directories

Personal defaults come from `platformdirs`: XDG locations on Linux and `~/Library/Application Support/tweetnook` plus `~/Library/Caches/tweetnook` on macOS. Explicit `XDG_CONFIG_HOME`, `XDG_DATA_HOME` and `XDG_CACHE_HOME` overrides are expanded and receive a `tweetnook/` suffix.

The installer's global Linux launcher runs archive commands as the `tweetnook` service account with parent roots `/etc`, `/var/lib` and `/var/cache`. It discards caller credential and XDG overrides and can request sudo. Personal Python/pip commands use their own account and environment.

A service installed with the manual service command can use `--config-home`, `--data-home` and `--cache-home` with absolute parent directories. Those settings belong to that unit; use the same account and environment when launching archive commands directly.

| Location | Durable or derived content |
| --- | --- |
| Config | `config.toml`, including private account/key values. |
| Data | `archive.db`, managed `media/`, exports, lock/activity files and web session key. |
| Data/Setup | `setup/archive.zip` and `setup/state.json`. |
| Data/activity | Run records/logs and AI-usage history. |
| Data | `schedule-state.json`, `notices.json`, `avatar-cache-state.json`. |
| Data/media | Downloaded avatar cache under `media/avatars/`. |
| Cache | Discovered query IDs. |

Backups need archive data/media and private configuration. Don't commit any of these runtime files. See [file locations](../maintenance.md#file-locations) for the platform-specific paths used in normal administration.

## Environment overrides

| Variable | Model field |
| --- | --- |
| `TWEETNOOK_AUTH_TOKEN` | `auth.auth_token` |
| `TWEETNOOK_CT0` | `auth.ct0` |
| `TWEETNOOK_USER_ID` | `auth.user_id` |
| `TWEETNOOK_GEMINI_API_KEY`, fallback `GEMINI_API_KEY` | `tagging.api_key` |
| `TWEETNOOK_PAGE_DELAY` | `sync.page_delay` |
| `TWEETNOOK_DETAIL_DELAY` | `sync.detail_delay` |
| `TWEETNOOK_MAX_RETRIES` | `sync.max_retries` |
| `TWEETNOOK_BACKOFF_BASE` | `sync.backoff_base` |
| `TWEETNOOK_DETAIL_MAX_RETRIES` | `sync.detail_max_retries` |
| `TWEETNOOK_DETAIL_BACKOFF_BASE` | `sync.detail_backoff_base` |
| `TWEETNOOK_COOLDOWN_THRESHOLD` | `sync.cooldown_threshold` |
| `TWEETNOOK_COOLDOWN_DURATION` | `sync.cooldown_duration` |
| `TWEETNOOK_TIMEOUT` | `sync.timeout` |
| `TWEETNOOK_MAX_LINKED_DEPTH` | `sync.max_linked_depth` |
| `TWEETNOOK_REPLY_LINKED_DEPTH` | `sync.reply_linked_depth` |
| `TWEETNOOK_REPLY_LINKED_LIMIT` | `sync.reply_linked_limit` |

These overrides apply to direct Python/pip processes. The installer's global Linux launcher filters them out for archive commands, as described under [directories](#directories). `TWEETNOOK_DEV_TOOLS=1` exposes the test-install replay controls for direct launches. Activity origin/run-ID variables coordinate workers. Setting a value in a shell doesn't change an already running service's environment.

## Applying changes

Successful web saves call `web/runtime_config.py`'s `reload_server_config` with the validated configuration. It updates request handlers, the job supervisor, scheduler, avatar manager, Analytics timer and archive connection tuning. Future private readers use the new database settings. An unchanged sync schedule keeps its chosen next run; dedicated schedule saves request a reset.

Active worker processes keep their starting configuration, and later workers read the saved file. Host and port changes require restarting the listener. Setup owns password changes and session renewal; `web set-password` writes the file and requires a restart. Direct TOML edits should be made with the server stopped, followed by a restart.

The UI groups advanced fields with their owning section. Schedule and tagging have dedicated editors, auth is edited through Setup, and password hashes aren't ordinary editable configuration fields. Restore defaults retains auth, password and API key while resetting operational settings.

## Settings reference

Defaults are defined by `AppConfig` in `config.py`; the [documentation checks](testing.md#documentation-checks) compare this reference with the model. Unset optional fields are omitted from TOML. Time strings use `HH:MM`; durations are seconds unless another unit is stated.

### auth

| Field | Default | Meaning |
| --- | --- | --- |
| `auth_token` | `unset` | Explicit X session token. |
| `ct0` | `unset` | Explicit CSRF cookie paired with the session. |
| `user_id` | `unset` | Numeric account ID, required for likes and authored collections. |

### sync

| Field | Default | Meaning |
| --- | --- | --- |
| `collections` | `["bookmarks", "likes"]` | Ordered subset of bookmarks, likes and tweets; at least one is required. |
| `page_delay` | `2.0` | Delay between timeline page requests. |
| `detail_delay` | `0.0` | Base delay for detail lookups. |
| `max_retries` | `3` | Timeline retry count. |
| `backoff_base` | `2.0` | Base timeline retry backoff. |
| `detail_max_retries` | `2` | Detail retry count. |
| `detail_backoff_base` | `30.0` | Base detail retry backoff. |
| `cooldown_threshold` | `3` | Consecutive detail errors before a cooldown. |
| `cooldown_duration` | `300.0` | Cooldown duration. |
| `timeout` | `30.0` | HTTP request timeout. |
| `max_linked_depth` | `50` | Maximum quote/linked-status hops; 0 disables linked discovery. |
| `reply_linked_depth` | `0` | 0 or 1 hop from other authors’ replies. |
| `reply_linked_limit` | `1` | Maximum reply-linked roots followed per sync. |

### web

| Field | Default | Meaning |
| --- | --- | --- |
| `password_hash` | `unset` | Web password digest; set through the password command or Setup. |
| `host` | `"0.0.0.0"` | Listen address; changes require restart. |
| `port` | `8000` | Listen port, 1 through 65535; changes require restart. |
| `fetch_avatars` | `true` | Permit downloading profile pictures. |
| `avatar_cache_limit_enabled` | `true` | Enable avatar cache eviction by size. |
| `avatar_cache_limit_mb` | `512` | Avatar cache size allowance in MiB. |
| `analytics_refresh_time` | `"05:00"` | Daily analytics snapshot refresh clock. |
| `dev_replay_source` | `false` | Allow this server to supply a developer replay archive. |

### schedule

| Field | Default | Meaning |
| --- | --- | --- |
| `enabled` | `false` | Enable automatic collection sync. |
| `cadence` | `"daily"` | hours, daily, weekly or monthly. |
| `every_hours` | `6` | Interval for hours cadence, 1 through 720. |
| `time` | `"03:00"` | Nominal time for non-hourly schedules. |
| `randomize_time` | `true` | Add a random delay to non-hourly occurrences. |
| `random_offset_min_hours` | `0.0` | Minimum added delay, 0 through 24 hours. |
| `random_offset_max_hours` | `2.0` | Maximum added delay, 0 through 24 hours. |
| `weekday` | `0` | Monday=0 through Sunday=6. |
| `day_of_month` | `1` | 1 through 31; clamp to last day when needed. |
| `timezone` | `"local"` | local or a valid IANA timezone name. |

### check

| Field | Default | Meaning |
| --- | --- | --- |
| `enabled` | `false` | Enable scheduled availability checks. |
| `cadence` | `"weekly"` | daily, weekly or monthly. |
| `time` | `"04:00"` | Check time in the schedule timezone. |
| `weekday` | `0` | Monday=0 through Sunday=6 for weekly checks. |
| `day_of_month` | `1` | Monthly date, 1 through 31; clamp at month end. |
| `context_enabled` | `false` | Also check a rolling share of context posts. |
| `context_days` | `60` | Target context cycle, 1 through 365 days. |
| `detail_budget` | `200` | Authenticated fallback budget, 0 through 10000 posts. |

### fxtwitter

| Field | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` | Permit public lookup; required by the check job. |
| `base_url` | `"https://api.fxtwitter.com"` | FxTwitter API base, including a self-hosted service. |
| `concurrency` | `4` | Concurrent public lookups, 1 through 16. |
| `timeout` | `30.0` | Public lookup request timeout. |

### activity

| Field | Default | Meaning |
| --- | --- | --- |
| `max_runs` | `200` | Retained run count, 1 through 10000. |
| `retention_days` | `180` | Run-history age limit, 1 through 3650 days. |

### database

| Field | Default | Meaning |
| --- | --- | --- |
| `cache_size_kb` | `524288` | SQLite page-cache allowance in KiB. |
| `mmap_size_bytes` | `1073741824` | SQLite memory-map allowance in bytes. |

### tagging

| Field | Default | Meaning |
| --- | --- | --- |
| `enabled` | `false` | Enable eligible tagging work when the optional dependencies are installed. |
| `api_key` | `unset` | Private Gemini API key. |
| `api_mode` | `"free"` | free or paid. |
| `model` | `"gemini-3.6-flash"` | Configured Gemini model ID. |
| `thinking_level` | `"high"` | high, medium, low or none. |
| `google_search` | `false` | Allow Search in Paid mode. |
| `free_batch_size` | `20` | Posts per homogeneous Free request, 1 through 20. |
| `free_rpm` | `10` | Local Free requests-per-minute ceiling. |
| `free_rpd` | `20` | Local Free requests-per-Pacific-day ceiling. |
| `processing_tier` | `"flex"` | standard or flex for Paid requests. |
| `daily_spend_limit_usd` | `unset` | Positive local daily Paid budget; omit only if otherwise valid. |
| `unlimited_spend` | `false` | Explicitly allow Paid work without a spending cap. |
| `search_safety_reserve` | `100` | Search allowance held back, 1 through 1000 queries. |
| `unknown_billing_policy` | `"reserve"` | reserve or stop when billing usage is unavailable. |
| `unknown_request_reserve_usd` | `0.02` | Positive minimum reserve for an uncertain request. |
| `max_unknown_requests_per_day` | `3` | Uncertain billing attempts allowed per UTC day, 1 through 100. |
| `search_unknown_policy` | `"reserve"` | reserve or disable Search after uncertain usage. |
| `max_unknown_search_requests` | `3` | Uncertain Search requests per run, 1 through 100. |
| `tagging_context` | `[]` | Up to 200 normalized, case-insensitively deduplicated subject hints. |
| `additional_instructions` | `unset` | Optional user preferences, at most 4000 characters. |
| `max_media_size_mb` | `100` | Maximum tagging media size in MiB. |

### replay

| Field | Default | Meaning |
| --- | --- | --- |
| `url` | `unset` | Source TweetNook URL for an isolated test install. |
| `password` | `unset` | Source web password, required to connect to a replay source. |
| `placeholder_media` | `true` | Use generated media during replay. |
| `skip_quote_expansion` | `false` | Skip linked quote work in replay. |
| `skip_link_previews` | `false` | Skip URL unfurling in replay. |

### Validation

Paid tagging requires a daily limit or explicit unlimited spending when enabled. Non-hourly random windows require the minimum to be no greater than the maximum. Timezones are validated, and collection choices are normalized into bookmarks/likes/tweets order. All config edits should preserve those invariants before writing.

`load_config` applies supported environment overlays after model validation using model copies. That is an implementation detail to consider when changing overlay validation; a UI field's validation contract should not be inferred from an environment overlay alone.

## Writes and permissions

The TOML writer uses a direct file write, not atomic replacement. Avoid concurrent config writers. Query-ID cache, schedule state and activity snapshots use temporary-file replacement; AI history uses a file lock and append-only ledger with an atomic summary. These formats have different durability properties.

The system installer keeps runtime/launcher ownership separate from its `tweetnook` state account and creates state directories with restricted access. Other file permissions follow the process umask; see [trust and security](architecture.md#trust-and-security). Web responses mask replay passwords, auth cookies, the password hash and Gemini key.

Legacy normalization removes browser/profile-selection fields and maps old tagging model/thinking/budget/RPD/batch fields where supported. Removed paid-concurrency values have no replacement. Test conflicting legacy fields and sparse saves rather than inferring migration order from a current model dump.
