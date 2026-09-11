# Configuration reference

[Documentation home](README.md) · [Connect Twitter/X](getting-started.md) ·
[CLI reference](cli-reference.md) · [Automated Tagging](automated-tagging.md)

Most setup can be done in the web app. Use this page when you need to edit
`config.toml`, look up a default, or find where your archive is stored.

| Setting | Where to change it |
|---|---|
| Twitter/X connection | Settings → Setup, or [`[auth]`](#auth) |
| Web address, port, avatars | Settings → Config, or [`[web]`](#web) |
| Scheduled syncing | Settings → Schedule, or [`[schedule]`](#schedule) |
| Gemini tagging | Settings → Automated Tagging, or [`[tagging]`](#tagging) |
| Request delays and retries | [`[sync]`](#sync) |
| Log retention | [`[activity]`](#activity) |
| Database memory settings | [`[database]`](#database) |
| File locations | [Resolved directories](#resolved-directories) |

## Edit the configuration file

The default file is `~/Library/Application Support/tweetnook/config.toml` on
macOS or `~/.config/tweetnook/config.toml` on Linux. Custom XDG folders can
change these paths.

You do not need to create `config.toml` before Web Setup. Most users should save
settings through the Web UI. If you prefer to manage the file manually, create
it at the resolved path and edit it while archive jobs are stopped. Keep it
private because it can contain Twitter/X session values and your Gemini key.

Only add settings you want to change. For example, to use a different port, stop
fetching author avatars, and raise the avatar cache limit, add or edit this section:

```toml
[web]
port = 8080
fetch_avatars = false
avatar_cache_limit_mb = 1024
```

Restart the server after editing general settings. For a systemd installation,
use `tweetnook service restart`. If you are running `tweetnook serve` in the
foreground, stop it and start it again. The optional `tweetnook web restart`
command is available for the manual background-server mode.

Install optional tagging dependencies once before restarting; no runtime `--extra` is needed.

## Which value takes effect?

For fields with environment support, the order is:

1. Environment variable.
2. Value in `config.toml`.
3. Application default.

Environment values are not saved to the file. Settings saved through the web app
omit values equal to the default, so a short configuration file is normal.
Dedicated Schedule settings take effect when saved; restart after general
configuration changes.

The tables below list every configuration field. `unset` means no value is
configured. Most installations can leave sync and database settings at their
defaults.

## `[auth]`

| Field | Default | Meaning |
|---|---:|---|
| `auth_token` | unset | Twitter/X session authentication cookie; required for live calls |
| `ct0` | unset | Twitter/X CSRF cookie/token; required for live calls |
| `user_id` | unset | Numeric Twitter/X account ID; required for likes and authored-tweet endpoints |

Environment overrides:

```text
TWEETNOOK_AUTH_TOKEN
TWEETNOOK_CT0
TWEETNOOK_USER_ID
```

Each environment value overrides its corresponding file value. The Setup panel
checks new credentials before saving them and masks saved secrets. Values in
`config.toml` are plain text. See [Connect Twitter/X](getting-started.md)
for copying session values from your browser.

## `[sync]`

| Field | Default | Validation | Meaning |
|---|---:|---:|---|
| `page_delay` | `2.0` | ≥ 0 seconds | Base delay between timeline pages |
| `detail_delay` | `0.0` | ≥ 0 seconds | Base delay between individual detail requests |
| `max_retries` | `3` | ≥ 0 | Timeline retry count |
| `backoff_base` | `2.0` | ≥ 0 seconds | Timeline exponential-backoff base |
| `detail_max_retries` | `2` | ≥ 0 | Detail retry count |
| `detail_backoff_base` | `30.0` | ≥ 0 seconds | Detail exponential-backoff base |
| `cooldown_threshold` | `3` | ≥ 1 | Consecutive headerless rate limits before long cooldown |
| `cooldown_duration` | `300.0` | ≥ 0 seconds | Long cooldown duration |
| `timeout` | `30.0` | ≥ 1 second | HTTP request timeout |
| `max_linked_depth` | `1` | ≥ 0 | Default quote/linked-status traversal depth |

Environment overrides:

```text
TWEETNOOK_PAGE_DELAY
TWEETNOOK_DETAIL_DELAY
TWEETNOOK_MAX_RETRIES
TWEETNOOK_BACKOFF_BASE
TWEETNOOK_DETAIL_MAX_RETRIES
TWEETNOOK_DETAIL_BACKOFF_BASE
TWEETNOOK_COOLDOWN_THRESHOLD
TWEETNOOK_COOLDOWN_DURATION
TWEETNOOK_TIMEOUT
TWEETNOOK_MAX_LINKED_DEPTH
```

Keep environment values within the same ranges shown in the table. Values outside
those ranges may fail later even if they are accepted when the command starts.

Provider rate-limit headers and adaptive pacing can add waits beyond these base
values.

## `[web]`

| Field | Default | Validation | Meaning |
|---|---:|---:|---|
| `password_hash` | unset | internal | SHA-256 Web password hash; create in Setup; CLI recovery via `web set-password` |
| `host` | `0.0.0.0` | nonempty | Address where the web app listens |
| `port` | `8000` | 1–65535 | Web app port |
| `fetch_avatars` | `true` | Boolean | Fetch/cache successful author avatars from captured URLs |
| `avatar_cache_limit_enabled` | `true` | Boolean | Run weekly least-recently-used avatar cache cleanup |
| `avatar_cache_limit_mb` | `512` | ≥1 | Maximum avatar cache size in MiB |

Setup creates the initial password as its final action; no default password is
created by `serve` or `web start`. `tweetnook web set-password` remains an
advanced recovery option and requires a server restart afterward.

The default `0.0.0.0` listener supports LAN access. Use the server's device address
in a browser; set `127.0.0.1` explicitly for local-only access. See
[Web access](web-app.md#network-and-security-boundary) for the security boundary.
The server starts without an archive and only creates one after an explicit
Setup initialization choice. The scheduler waits for both archive readiness
and completed Setup.

Avatar fetching visits URLs saved in the archive. Scripts and styles are bundled,
and fonts are local. Disable avatar fetching and scheduled syncs when you want
archive browsing without their background internet attempts.

The Web server maintains the avatar limit in its own weekly job; it is not part
of archive syncs. Successful downloads begin with a current modification time,
and serving a cached avatar refreshes that time at most once per day. Cleanup
deletes the oldest modification times until the recognized avatar files fit the
configured limit. Avatars saved before this behavior use their existing file
time, so untouched legacy files are normally considered first. Disable
`avatar_cache_limit_enabled` to retain cached avatars without size-based cleanup.

## `[schedule]`

| Field | Default | Validation | Meaning |
|---|---:|---:|---|
| `enabled` | `false` | Boolean | Run scheduled bookmark-and-like syncs while the web app is running |
| `cadence` | `daily` | `hours`, `daily`, `weekly`, `monthly` | Schedule type |
| `every_hours` | `6` | 1–720 | Hourly interval |
| `time` | `03:00` | `HH:MM`, 24-hour | Fixed local schedule time |
| `randomize_time` | `true` | Boolean | Add a random delay to non-hourly schedules |
| `random_offset_min_hours` | `0.0` | 0–24 | Minimum added delay |
| `random_offset_max_hours` | `2.0` | 0–24 | Maximum added delay |
| `weekday` | `0` | 0–6 | Monday=0 through Sunday=6 |
| `day_of_month` | `1` | 1–31 | Monthly target, clamped at month end |
| `timezone` | `local` | `local` or installed IANA name | Calendar/timezone basis |

For randomized non-hourly schedules, minimum must not exceed maximum. The
random value is additive; it never makes a run earlier than `time`. Hourly
schedules ignore randomization and count from the schedule's current anchor.

The scheduler runs only inside the Web service. Its next time is stored in
`schedule-state.json`; editing schedule fields through the dedicated Web pane
reloads it. It launches bookmarks/likes sync, not `sync tweets`.

## `[activity]`

| Field | Default | Validation | Meaning |
|---|---:|---:|---|
| `max_runs` | `100` | 1–10,000 | Maximum number of completed runs to keep |
| `retention_days` | `90` | 1–3650 | Age cutoff for retained completed runs |

Old completed logs are removed when the web app starts and after jobs finish.
Active runs are kept. These settings do not control Gemini usage-history retention.

## `[database]`

| Field | Default | Validation | Meaning |
|---|---:|---:|---|
| `cache_size_kb` | `524288` | ≥ 0 | SQLite page-cache target per connection (512 MiB) |
| `mmap_size_bytes` | `1073741824` | ≥ 0 | Maximum SQLite memory-mapped I/O request (1 GiB) |

These are requested limits/targets, not guaranteed resident-memory allocation.
Leave these at their defaults unless you are tuning database memory use. See the
developer [Storage guide](development/storage.md) for connection behavior.

## `[tagging]`

| Field | Default | Validation | Meaning |
|---|---:|---:|---|
| `enabled` | `false` | Boolean | Add tagging to normal sync and enable queued runs |
| `api_key` | unset | secret string | Gemini API key |
| `api_mode` | `free` | `free` or `paid` | Free or Paid tagging mode |
| `model` | `gemini-3.6-flash` | nonempty | Gemini model ID |
| `thinking_level` | `high` | `high`, `medium`, `low`, `none` | Model reasoning setting |
| `google_search` | `false` | Boolean | Allow Search in Paid mode; Free execution forces it off |
| `free_batch_size` | `20` | 1–20 | Tweets of the same content type per Free request |
| `free_rpm` | `10` | ≥ 1 | Local Free requests/minute limiter |
| `free_rpd` | `20` | ≥ 1 | Persistent per-model Free requests/day limiter |
| `processing_tier` | `flex` | `standard` or `flex` | Paid service tier |
| `daily_spend_limit_usd` | unset | > 0 when set | Finite Paid daily estimate cap |
| `unlimited_spend` | `false` | Boolean | Allow Paid mode without a finite cap |
| `search_safety_reserve` | `100` | 1–1000 | Monthly Search allowance kept unused as safety margin |
| `tagging_context` | `[]` | ≤ 200 entries | Preferred contextual tag hints |
| `additional_instructions` | unset | ≤ 4000 characters | Lower-priority prompt guidance |
| `max_media_size_mb` | `100` | ≥ 1 | Maximum individual media size sent to the provider |

If enabled Paid mode is not unlimited, `daily_spend_limit_usd` is required.
Context can be written as a TOML array; input normalization also accepts a
comma/newline-separated string through programmatic/Web paths and removes
case-insensitive duplicates.

API key environment precedence:

1. `TWEETNOOK_GEMINI_API_KEY`
2. `GEMINI_API_KEY`
3. `tagging.api_key`

See [Automated Tagging](automated-tagging.md) before changing quota, Search, or
spend controls.

## Resolved directories

Default locations are:

| System | Configuration | Data | Cache |
|---|---|---|---|
| macOS | `~/Library/Application Support/tweetnook` | Same as configuration | `~/Library/Caches/tweetnook` |
| Linux | `~/.config/tweetnook` | `~/.local/share/tweetnook` | `~/.cache/tweetnook` |

To confirm the exact paths used on your machine without printing secrets:

```bash
uv run python -c 'from tweetnook.config import resolve_paths; p = resolve_paths(); print("Config:", p.config_dir); print("Data:", p.data_dir); print("Cache:", p.cache_dir)'
```

### Use custom folders

Set these environment variables before running a command. Each root receives
its own `tweetnook` subfolder:

```bash
export XDG_CONFIG_HOME=/absolute/path/to/custom/config
export XDG_DATA_HOME=/absolute/path/to/custom/data
export XDG_CACHE_HOME=/absolute/path/to/custom/cache
```

The database would then be at
`/absolute/path/to/custom/data/tweetnook/archive.db`. Changing these variables
does not move your existing files. Use the same values when starting the web app
and running terminal commands against that archive.

## Files and directories

| Location | Purpose | Backup priority |
|---|---|---|
| `config.toml` | Settings and optional plaintext secrets | Required, separately protected |
| `archive.db` | SQLite normalized rows, raw captures, state, manifests, tags | Required |
| `archive.db-wal`, `archive.db-shm` | Live SQLite WAL sidecars | Stop the app and jobs before copying |
| `media/` | Imported/downloaded media and cached avatars | Required for a complete local archive |
| `exports/` | Generated logical JSON exports | Optional; regenerate when database exists |
| `sync.lock` | Archive writer lock | Runtime only |
| `command.lock` | Lifecycle-command lock | Runtime only |
| `.web.pid` | Detached Web daemon PID marker | Runtime only |
| `activity-status.json` | Current/last pipeline snapshot | Optional operational state |
| `activity/runs/<run-id>/` | Metadata, events, output, Web console log | Optional, sensitive diagnostics |
| `activity/ai-usage/` | Monthly Gemini usage ledger/summary | Recommended when using spend controls |
| `setup/archive.zip` | One staged Web upload | Optional; may duplicate sensitive source archive |
| `schedule-state.json` | Persisted next scheduled occurrence | Optional operational state |
| `avatar-cache-state.json` | Persisted last/next weekly avatar cleanup and result | Optional operational state |
| `query-ids.json` | 24-hour discovered/fallback query-ID cache | Optional; refetched when possible |
| `archive.lancedb/` | Possible legacy migration source | Preserve until migration is validated |

The data directory can contain private tweets, raw Twitter/X responses, URLs, media,
avatars, import metadata, and usage logs. Protect it accordingly.

Setup workflow state is stored in `<data>/setup/state.json`; notices are in
`<data>/notices.json`. These are available before `archive.db` exists. Rerunning
Setup does not clear them or reset configuration. The Linux service defaults to
the selected Unix user's home directories; its install options can set explicit
XDG data/config/cache homes.

## Web configuration surfaces

The general Config pane's default view exposes avatar fetching/cache limits plus
`web.host` and `web.port`. Advanced mode exposes additional sync/database fields.
Authentication, schedule, and automated tagging have dedicated panes; remaining
fields may require direct TOML editing.

Restart the app after general configuration changes. Schedule changes made in
the dedicated pane take effect when saved. For configuration loading and API
behavior, see [Configuration and paths](development/configuration-and-paths.md).
