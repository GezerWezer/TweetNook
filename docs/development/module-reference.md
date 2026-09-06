# Module Reference

[Development guide](README.md) · [Architecture](architecture.md) ·
[Testing](testing.md) · [Documentation audit](documentation-audit.md)

Use this reference to find the source file responsible for a feature. Each entry
links to the code, its design guide, and relevant tests.

## Package entry and orchestration

| File | Responsibility | Design/tests |
|---|---|---|
| [tweetnook/__init__.py](../../tweetnook/__init__.py) | Package version (`__version__`) | [Release](release.md); CLI/version tests |
| [tweetnook/__main__.py](../../tweetnook/__main__.py) | `python -m tweetnook` entry into Typer app | [Architecture](architecture.md); CLI tests |
| [tweetnook/cli.py](../../tweetnook/cli.py) | Root/nested command tree, rendering, command wiring, exit mapping, hidden daemon | [CLI reference](../cli-reference.md); `test_cli.py` |
| [tweetnook/cli_web.py](../../tweetnook/cli_web.py) | Detached Web start/stop/restart/status/password lifecycle | [Web and Jobs](web-and-jobs.md); `test_cli_web.py` |
| [tweetnook/exceptions.py](../../tweetnook/exceptions.py) | Domain error taxonomy for auth/API/storage/import/search/schedule | [Architecture](architecture.md); exercised throughout |
| [tweetnook/interactive.py](../../tweetnook/interactive.py) | TTY-gated status and tqdm callbacks outside active pipeline | [Architecture](architecture.md); `test_interactive.py` |
| [tweetnook/utils.py](../../tweetnook/utils.py) | UTC timestamp helper and query-ID resolution/refresh lifecycle | [Client and Sync](client-and-sync.md); `test_query_ids.py` |
| [tweetnook/reminders.py](../../tweetnook/reminders.py) | Schema-migration and pending-enrichment user notices | [Storage](storage.md); CLI/storage tests |

## Configuration, locks, jobs, and activity

| File | Responsibility | Design/tests |
|---|---|---|
| [tweetnook/config.py](../../tweetnook/config.py) | Pydantic models, TOML load/write/migration, env overlay, XDG paths, UI schema, Twitter/X constants | [Configuration and Paths](configuration-and-paths.md); `test_config.py` |
| [tweetnook/locking.py](../../tweetnook/locking.py) | Nonblocking POSIX `fcntl.flock` process lock with optional reentrancy | [Architecture](architecture.md); jobs/pipeline tests |
| [tweetnook/jobs.py](../../tweetnook/jobs.py) | Locked archive-job/store context and cleanup | [Architecture](architecture.md); `test_jobs.py` |
| [tweetnook/pipeline.py](../../tweetnook/pipeline.py) | PipelineReporter, steps/issues/progress, command lock, durable snapshot hooks | [Client and Sync](client-and-sync.md); `test_pipeline.py` |
| [tweetnook/activity_history.py](../../tweetnook/activity_history.py) | Run directories, metadata/events/log/output/final snapshots, argv redaction, retention | [Web and Jobs](web-and-jobs.md); `test_activity_history.py` |
| [tweetnook/job_supervisor.py](../../tweetnook/job_supervisor.py) | One Web/scheduled subprocess, process group, log redirection, stop/finalize | [Web and Jobs](web-and-jobs.md); `test_job_supervisor.py` |
| [tweetnook/scheduler.py](../../tweetnook/scheduler.py) | Web-owned recurrence calculation, persisted next run, conflict skip | [Scheduler](scheduler.md); `test_scheduler.py` |

## Authentication and query IDs

| File | Responsibility | Design/tests |
|---|---|---|
| [tweetnook/auth/__init__.py](../../tweetnook/auth/__init__.py) | Public auth exports | [Client and Sync](client-and-sync.md); auth tests |
| [tweetnook/auth/cookies.py](../../tweetnook/auth/cookies.py) | Explicit config/environment cookie bundle and `user_id` requirement | [Client and Sync](client-and-sync.md); `test_auth.py` |
| [tweetnook/query_ids/__init__.py](../../tweetnook/query_ids/__init__.py) | Public query-ID exports | [Client and Sync](client-and-sync.md); query-ID tests |
| [tweetnook/query_ids/constants.py](../../tweetnook/query_ids/constants.py) | Operation names and static compatibility IDs | [Client and Sync](client-and-sync.md); `test_query_ids.py` |
| [tweetnook/query_ids/scraper.py](../../tweetnook/query_ids/scraper.py) | Discovery HTML/bundle/chunk fetch and ID regex extraction | [Client and Sync](client-and-sync.md); `test_query_ids.py` |
| [tweetnook/query_ids/store.py](../../tweetnook/query_ids/store.py) | 24-hour cache, atomic persistence, fallback lookup | [Client and Sync](client-and-sync.md); `test_query_ids.py` |

## Twitter/X client

| File | Responsibility | Design/tests |
|---|---|---|
| [tweetnook/client/__init__.py](../../tweetnook/client/__init__.py) | Public client exports | [Client and Sync](client-and-sync.md); client tests |
| [tweetnook/client/base.py](../../tweetnook/client/base.py) | HTTPX headers/cookies, GraphQL requests, status classification, refresh, retry/cooldown/pacing | [Client and Sync](client-and-sync.md); `test_client.py` |
| [tweetnook/client/features.py](../../tweetnook/client/features.py) | Operation-specific feature flags and field toggles | [Client and Sync](client-and-sync.md); request-shape tests |
| [tweetnook/client/timelines.py](../../tweetnook/client/timelines.py) | Bookmark/like/authored/article/detail calls and nested entry/cursor/focal parsing | [Client and Sync](client-and-sync.md); `test_client.py`, sync/enrichment tests |

## Extraction, storage, and migration

| File | Responsibility | Design/tests |
|---|---|---|
| [tweetnook/extractor.py](../../tweetnook/extractor.py) | GraphQL normalization, canonical text, unavailable classification, relations/media/URLs/articles | [Enrichment](enrichment.md); extractor tests |
| [tweetnook/storage/__init__.py](../../tweetnook/storage/__init__.py) | Public storage types/open helpers | [Storage](storage.md); storage tests |
| [tweetnook/storage/backend.py](../../tweetnook/storage/backend.py) | SQLite v5 schema/FTS/indexes, transactions, merge/provenance, queues, hydration, tags, export, maintenance | [Storage](storage.md); storage/search/import/tag/stats tests |
| [tweetnook/storage/migrate.py](../../tweetnook/storage/migrate.py) | Isolated legacy LanceDB reader and SQLite copy/rebuild | [Storage](storage.md); `test_migrate.py` |

## Sync, import, and conversion

| File | Responsibility | Design/tests |
|---|---|---|
| [tweetnook/sync.py](../../tweetnook/sync.py) | Preflight, collection mode/state/page loop, all-sync boundary, ordered follow-ups | [Client and Sync](client-and-sync.md); `test_sync.py` |
| [tweetnook/archive_import.py](../../tweetnook/archive_import.py) | Official ZIP/directory reader, digest/manifest, datasets/media, regen/sample, reconciliation/detail | [Archive Import](archive-import.md); `test_archive_import.py` |

## Enrichment jobs

| File | Responsibility | Design/tests |
|---|---|---|
| [tweetnook/articles.py](../../tweetnook/articles.py) | Preview/all/explicit article refresh through focal TweetDetail | [Enrichment](enrichment.md); `test_articles.py` |
| [tweetnook/threads.py](../../tweetnook/threads.py) | Pending/explicit thread, quote, linked-status expansion and depth/circuit breaker | [Enrichment](enrichment.md); `test_threads.py` |
| [tweetnook/media.py](../../tweetnook/media.py) | Photo/video/GIF/poster downloads, temp/hash/type/path/state handling | [Enrichment](enrichment.md); `test_media.py` |
| [tweetnook/unfurl.py](../../tweetnook/unfurl.py) | Saved URL fetch/redirect/canonical/HTML metadata and state | [Enrichment](enrichment.md); `test_unfurl.py` |
| [tweetnook/resurrection.py](../../tweetnook/resurrection.py) | Weighted due unavailable selection, detail probing, confidence/retry/recovery | [Enrichment](enrichment.md); `test_resurrection.py` |

## Search, export, and statistics

| File | Responsibility | Design/tests |
|---|---|---|
| [tweetnook/search.py](../../tweetnook/search.py) | Query lexer/parser, filter validation, SQL/FTS planning, article search, sort/cap/page | [Search and Web API](search-and-web-api.md); `test_search.py` |
| [tweetnook/export/__init__.py](../../tweetnook/export/__init__.py) | Public export function exports | [Storage](storage.md); export tests |
| [tweetnook/export/common.py](../../tweetnook/export/common.py) | Collection aliases/labels and default timestamped path | [Storage](storage.md); CLI/export tests |
| [tweetnook/export/json_export.py](../../tweetnook/export/json_export.py) | Temporary-file/atomic JSON write | [Storage](storage.md); `test_export.py` |
| [tweetnook/stats/__init__.py](../../tweetnook/stats/__init__.py) | Public report service/model exports | [Search and Web API](search-and-web-api.md); stats tests |
| [tweetnook/stats/models.py](../../tweetnook/stats/models.py) | Typed report/section/tile/row model contracts | [Search and Web API](search-and-web-api.md); `test_stats.py` |
| [tweetnook/stats/service.py](../../tweetnook/stats/service.py) | Overview/collections/archive/storage/tagging collectors and estimates | [Search and Web API](search-and-web-api.md); CLI/Web stats tests |
| [tweetnook/stats/render_cli.py](../../tweetnook/stats/render_cli.py) | Rich terminal report rendering/status markers | [CLI reference](../cli-reference.md); `test_stats.py`, CLI tests |

## Automated/manual tagging and accounting

| File | Responsibility | Design/tests |
|---|---|---|
| [tweetnook/automated_tagging.py](../../tweetnook/automated_tagging.py) | Optional dependency probe and live model discovery/filtering | [Automated Tagging](automated-tagging.md); optional/Web tests |
| [tweetnook/tagging.py](../../tweetnook/tagging.py) | Candidate orchestration, Free/Paid provider execution, media, retries, parsing, persistence | [Automated Tagging](automated-tagging.md); `test_tagging.py`, CLI tests |
| [tweetnook/tagging_prompts.py](../../tweetnook/tagging_prompts.py) | Text/media/batch prompt policy and bounded context/instruction blocks | [Automated Tagging](automated-tagging.md); `test_tagging_prompts.py` |
| [tweetnook/gemini_accounting.py](../../tweetnook/gemini_accounting.py) | Usage normalization, Search counting, monthly ledger/summary and budget accounting | [Automated Tagging](automated-tagging.md); `test_gemini_foundation.py` |
| [tweetnook/gemini_pricing.py](../../tweetnook/gemini_pricing.py) | OpenRouter metadata cache and token/category cost estimates | [Automated Tagging](automated-tagging.md); `test_gemini_foundation.py` |
| [tweetnook/rpd.py](../../tweetnook/rpd.py) | Atomic per-model Pacific-day Free request reservations | [Automated Tagging](automated-tagging.md); `test_rpd.py` |

Manual tag CRUD itself is stored in `ArchiveStore` and exposed through Web tag
routes; there is no separate manual-tag service module.

## Web core

| File | Responsibility | Design/tests |
|---|---|---|
| [tweetnook/web/__init__.py](../../tweetnook/web/__init__.py) | Web package marker/public boundary | [Web and Jobs](web-and-jobs.md); Web tests |
| [tweetnook/web/address.py](../../tweetnook/web/address.py) | Display URL selection for loopback/wildcard/IPv4/IPv6 binds | [Web and Jobs](web-and-jobs.md); CLI Web tests |
| [tweetnook/web/availability.py](../../tweetnook/web/availability.py) | Canonical availability/placeholder/quote/retweet overlay for JSON/UI | [Search and Web API](search-and-web-api.md); availability tests |
| [tweetnook/web/deps.py](../../tweetnook/web/deps.py) | Process-global state and HTTP Basic password verification | [Web and Jobs](web-and-jobs.md); deps/server tests |
| [tweetnook/web/server.py](../../tweetnook/web/server.py) | FastAPI app/lifespan, routes/static/media, supervisor/scheduler/stats cleanup, Uvicorn | [Web and Jobs](web-and-jobs.md); deps/server/Web tests |
| [tweetnook/web/stats_cache.py](../../tweetnook/web/stats_cache.py) | Five-minute stale-while-revalidate snapshot cache | [Search and Web API](search-and-web-api.md); stats-cache tests |

## Web routes

| File | Responsibility | Principal tests |
|---|---|---|
| [tweetnook/web/routes/__init__.py](../../tweetnook/web/routes/__init__.py) | Route package marker | all Web route tests |
| [tweetnook/web/routes/activity.py](../../tweetnook/web/routes/activity.py) | Status, start/stop, run history/log, schedule GET/PUT | `test_activity_routes.py` |
| [tweetnook/web/routes/automated_tagging.py](../../tweetnook/web/routes/automated_tagging.py) | AI settings/accounting, picker, preview, live catalog | `test_automated_tagging_routes.py` |
| [tweetnook/web/routes/avatars.py](../../tweetnook/web/routes/avatars.py) | Cached/fetched author avatar or transparent fallback | `test_avatar_routes.py` |
| [tweetnook/web/routes/config.py](../../tweetnook/web/routes/config.py) | Masked effective/default/schema config and sparse updates | `test_config_routes.py` |
| [tweetnook/web/routes/setup.py](../../tweetnook/web/routes/setup.py) | Credential probe/save and fixed staged ZIP lifecycle | `test_setup_routes.py` |
| [tweetnook/web/routes/stats.py](../../tweetnook/web/routes/stats.py) | Fresh/cached/compat report endpoints | `test_stats_routes.py`, cache tests |
| [tweetnook/web/routes/storage_stats.py](../../tweetnook/web/routes/storage_stats.py) | Legacy storage-breakdown endpoint/byte formatting | `test_storage_stats_routes.py` |
| [tweetnook/web/routes/tags.py](../../tweetnook/web/routes/tags.py) | Per-tweet/global tag CRUD/autocomplete/stats | `test_tag_routes.py` |
| [tweetnook/web/routes/tweets.py](../../tweetnook/web/routes/tweets.py) | Search/list/detail/quotes/authors, relationship assembly | `test_web_tweets_api.py` |

## Web assets

| File | Responsibility | Principal tests |
|---|---|---|
| [tweetnook/web/index.html](../../tweetnook/web/index.html) | Alpine template for feed, details, settings, tags, stats, activity, AI | Python/JS asset tests and route contract tests |
| [tweetnook/web/static/css/styles.css](../../tweetnook/web/static/css/styles.css) | Theme variables, cards/placeholders, drawers/modals/search/setup/responsive styling | asset harness plus manual visual QA |
| [tweetnook/web/static/js/app.js](../../tweetnook/web/static/js/app.js) | State, API calls, rendering, navigation, media, settings, schedule, stats/activity | Python/JS asset tests |
| [tweetnook/web/static/js/autocomplete.js](../../tweetnook/web/static/js/autocomplete.js) | Contenteditable query formatting, suggestions, caret/keyboard/date behavior | Python/JS asset tests |
| [tweetnook/web/static/js/themes.js](../../tweetnook/web/static/js/themes.js) | Theme catalog, accent/font sources, local preference helpers | Python/JS asset tests |

The frontend ships pinned Alpine.js/plugins and precompiled Tailwind assets.
See [asset tooling](../../dev/web/README.md) for reproducible builds. Fonts are
local; offline browsing still requires the running archive server.

## Packaging and source-distribution inputs

| File | Responsibility | Review |
|---|---|---|
| [pyproject.toml](../../pyproject.toml) | Build metadata, dependencies/extras/dev group, entry point, Ruff/pytest, sdist selection | [Release](release.md) |
| [uv.lock](../../uv.lock) | Reproducible resolved dependency graph/local package metadata | `uv sync --locked`, release diff |
| [LICENSE](../../LICENSE) | Apache License 2.0 | release/package metadata |
| [README.md](../../README.md) | Current packaged long description; known stale behavior pending staged-doc promotion | [Release](release.md) |
| [CHANGELOG.md](../../CHANGELOG.md) | Historical/release notes included in the sdist | [Release](release.md) |

`dev/lancedb-test/*` is historical exploratory material, not production. Aside
from the explicitly listed sdist input above, the original `docs/*`,
`docs/initial/*`, and `reference/*` are research/history and are intentionally
outside this runtime/package-code ledger.
