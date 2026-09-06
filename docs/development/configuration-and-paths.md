# Configuration and Paths

[Development guide](README.md) · [Architecture](architecture.md) ·
[Web and Jobs](web-and-jobs.md) · [User configuration](../configuration.md)

`config.py` loads and validates settings, applies environment overrides, and
resolves application folders. It also writes configuration updates and supplies
the general web settings schema. User-facing field names and defaults are listed
in the [configuration reference](../configuration.md).

## Model structure

`AppConfig` contains:

- `AuthConfig`
- `SyncConfig`
- `WebConfig`
- `ScheduleConfig`
- `ActivityConfig`
- `DatabaseConfig`
- `TaggingConfig`

Every model uses `extra='ignore'`. Unknown TOML fields do not affect effective
configuration but can remain in raw mappings unless a migration/update rewrites
them. Programmatic Web update paths build a whitelist from model fields and
reject unknown dotted paths.

Validators currently cover timezone validity, schedule random-window ordering,
and Paid tagging spend safety. Field constraints cover numeric ranges and
literals.

## Load sequence

`load_config(env)`:

1. resolve and create config/data/cache roots;
2. create or parse `config.toml`;
3. ensure blank auth placeholders and remove legacy browser fields;
4. migrate legacy tagging keys;
5. normalize blank auth values to `None` for model validation;
6. validate `AppConfig`;
7. overlay supported auth/sync/provider-key environment values;
8. return `(config, paths)`.

Environment overlay uses `model_copy(update=...)` after validation. Numeric sync
values therefore bypass Pydantic bounds, although parse syntax is validated by
`int()`/`float()`. Treat this as a known correctness gap; a future fix should
validate the final model and include compatibility tests.

## TOML writer

The project has a minimal serializer for strings, lists, booleans, and numbers.
Sections use a stable order; fields retain mapping order. Auth blank placeholders
are always preserved. Empty non-auth sections are omitted.

`_write_config_file()` currently calls `Path.write_text()` directly. Unlike the
query-ID cache, schedule state, activity snapshots, archive media/export files,
and AI summary, config TOML is **not** written through a temporary atomic rename.
Do not document it as atomic. Avoid concurrent configuration writers.

`update_config_values()`:

- validates dotted paths against model fields;
- removes default/`None` non-auth values for sparse storage;
- writes blank strings for cleared auth fields;
- revalidates the complete normalized config before write.

## Legacy normalization

Auth repair removes:

```text
browser
browser_profile
browser_profile_path
firefox_profile_path
```

Tagging migration removes old text/media model/thinking, spending, RPD, batch,
limit, and paid-concurrency keys. It maps the model, thinking, spending, RPD,
batch, and limit values where possible; `paid_concurrency` is discarded without
a replacement. When two old fields conflict, the selected precedence is encoded
in `_migrate_tagging_config`; change only with explicit migration tests.

## Environment variables

Auth:

```text
TWEETNOOK_AUTH_TOKEN
TWEETNOOK_CT0
TWEETNOOK_USER_ID
```

Sync:

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

Provider key:

```text
TWEETNOOK_GEMINI_API_KEY
GEMINI_API_KEY
```

The tweetnook-specific key wins. There are no environment overlays for
Web/schedule/activity/database/other tagging fields.

XDG variables are consumed by path resolution rather than config overlay:

```text
XDG_CONFIG_HOME
XDG_DATA_HOME
XDG_CACHE_HOME
```

## Path resolution

Explicit XDG roots are expanded and receive an `APP_NAME` child. Otherwise
platformdirs supplies OS-native roots.

`XDGPaths` properties define the stable application locations below. The Web
CLI also derives its PID marker directly from the data root.

| Property | Relative location |
|---|---|
| `config_file` | config root / `config.toml` |
| `database_path` | data root / `archive.db` |
| `media_dir` | data root / `media` |
| `lock_file` | data root / `sync.lock` |
| `command_lock_file` | data root / `command.lock` |
| Web CLI runtime path | data root / `.web.pid` |
| `activity_status_file` | data root / `activity-status.json` |
| `staged_archive_file` | data root / `setup/archive.zip` |
| `schedule_state_file` | data root / `schedule-state.json` |
| `activity_runs_dir` | data root / `activity/runs` |
| `ai_usage_dir` | data root / `activity/ai-usage` |
| `query_id_cache_file` | cache root / `query-ids.json` |

`database_file` is a backward-compatible alias. Keep new code on
`database_path`.

## File ownership and permissions

Directory/file creation relies on normal process umask. The code does not apply
0600/0700 to config, database, logs, or ledgers. Configuration can contain Twitter/X
cookies and Gemini keys; other data roots contain private raw tweets/media.

Do not introduce real secrets into repository-local XDG roots or tests. Test
fixtures should use temporary directories and synthetic values.

## Secret masking

General config responses mask:

- `auth.auth_token`
- `auth.ct0`
- `web.password_hash`
- `tagging.api_key`

Explicit field lists omit password hash and blank auth placeholders. Update
routes reject masked placeholder values for protected fields. Setup and AI routes
have dedicated mask-preservation behavior.

This is response masking, not encryption or universal log redaction.

## Web schema

`get_config_ui_schema()` currently whitelists only:

- `web.fetch_avatars`
- `web.host`
- `web.port`

Auth, password, schedule, activity, and all tagging fields are blacklisted from
the generic UI. The response still carries labels/descriptions for additional
fields, but dedicated panes own auth/schedule/tagging.

The general config POST does not enforce the UI whitelist; it accepts any valid
dotted model path except protected/masked cases. It also does not hot-reload
server state. Dedicated APIs or restart define runtime application.

## External constants

`config.py` also centralizes:

- internal GraphQL base URL;
- responsive Web bundle/discovery URLs and regex;
- public Web bearer token;
- browser-like default user agent;
- stable filenames/default SQLite cache/mmap targets.

These Twitter/X values are reverse-engineered compatibility data, not user
configuration or official API contract.

## Lock/state write patterns

Not all non-database files use the same durability pattern:

- config TOML: direct write;
- query-ID cache: temporary + replace;
- schedule state: temporary + replace;
- activity snapshot/final: atomic replacement;
- AI ledger/summary: file lock plus append/atomic summary;
- media/export: temporary + replace.

Document and test the actual pattern when adding state rather than assuming a
generic helper.

## Regression targets

- `tests/test_config.py`
- `tests/test_auth.py`
- `tests/web/test_config_routes.py`
- `tests/web/test_setup_routes.py`
- schedule/tagging model tests
- any CLI test whose environment changes effective configuration
