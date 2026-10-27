# Modules

Start at the public operation you're changing, then follow its storage and presentation paths. The main ownership boundaries are below; small supporting modules keep shared policy out of command handlers.

| Module or package | Owns |
| --- | --- |
| `__init__.py`, `__main__.py` | Package version and module entry point. |
| `cli.py` | User commands, option definitions and terminal reports. |
| `cli_web.py`, `cli_service.py`, `system_install.py` | Personal server, systemd integration, managed installs and updates. |
| `config.py` | Pydantic config, defaults, sparse TOML writes, environment overrides and UI schema. |
| `core/` | Foundation modules: errors (`exceptions.py`), JSON compatibility (`fastjson.py`), process locks (`locking.py`), collection names (`collection_names.py`), redirected test requests and host isolation (`dev_replay.py`) and shared helpers (`utils.py`). |
| `auth/cookies.py` | Explicit session credentials and per-collection requirements. |
| `client/base.py`, `client/features.py` | HTTP setup, pacing, headers, feature flags and transport errors. |
| `client/timelines.py` | X operation variables and timeline/detail requests. |
| `core/timeline_parsing.py` | Timeline/detail parsing and focal-result classification. |
| `client/fxtwitter.py` | Cookie-free public lookup. |
| `core/fxtwitter_payloads.py` | FxTwitter payload adaptation to the GraphQL tweet shape. |
| `query_ids/` | Operation defaults, cache and bundle discovery. |
| `core/extractor.py`, `core/media_metadata.py` | Canonical fields, relations, Article structures and media geometry. |
| `enrichment/articles.py` | Article body refresh. |
| `storage/` | SQLite archive: `ArchiveStore` in `backend.py`, one module per storage concern (see [storage](storage.md#layout)), shared search query parsing and sorts (`search_query.py`), and rebuildable like order (`like_order.py`). |
| `sync/collection.py` | Collection preflight, resumable pages and follow-up plan. |
| `importer/archive.py` | Official export ingest, manifests and sparse completion. |
| `importer/legacy_migration.py` | Explicit migration of a legacy LanceDB archive. |
| `enrichment/threads.py`, `enrichment/media.py`, `enrichment/unfurl.py` | Conversation expansion, file downloads and link previews. |
| `enrichment/resurrection.py`, `enrichment/check.py` | Authenticated retry policy and public availability checks. |
| `enrichment/prune.py` | Reachability planning and scoped context/file cleanup. |
| `export/` | JSON output and export file names. |
| `stats/` | Structured reports shared by CLI and web consumers. |
| `tagging/tagger.py`, `tagging/prompts.py` | Gemini request planning, evidence, validation and generated tags. |
| `tagging/sdk.py` | Optional dependency checks and model catalog. |
| `tagging/accounting.py`, `tagging/pricing.py`, `tagging/rpd.py` | Local spend, Search usage and request allowances. |
| `jobs/context.py`, `jobs/resource_failure.py` | Locked job context and storage-failure handling. |
| `jobs/pipeline.py`, `jobs/activity_history.py`, `jobs/worker_logging.py` | Progress stages, durable outcomes and timestamped logs. |
| `jobs/supervisor.py`, `jobs/scheduler.py` | Worker/queue ownership and scheduled submission. |
| `jobs/reminders.py`, `jobs/interactive.py` | Notices and terminal interaction. |
| `web/server.py`, `web/deps.py` | FastAPI lifecycle, shared state, sessions and archive readiness. |
| `web/runtime_config.py` | Apply saved settings to running services under the shared lifecycle lock. |
| `web/setup_coordinator.py`, `web/jobs.py` | Setup transitions and validated job-to-CLI mapping. |
| `web/availability.py`, `web/cards.py`, `web/articles.py`, `web/history.py` | Read presentation and observed history. |
| `web/avatar_cache.py`, `web/stats_cache.py`, `web/warmup.py` | Background caches and archive preparation. |
| `web/activity_stream.py`, `web/notices.py` | Live activity snapshots and durable notifications. |
| `web/replay_source.py`, `web/replay_placeholders.py` | Source-side replay and placeholder media. |
| `web/routes/` | HTTP resource groups; see the [API guide](search-and-web-api.md). |
| `web/index.html`, `web/static/` | Browser UI and shipped assets. |

`scripts/build_setup_help.py` generates the in-app cookie guide. `scripts/build_pages_demo.py` builds the public fixture demo, and `scripts/prepare_demo_content.py` prepares approved demonstration content. `dev/web/` holds the Node asset build and browser diagnostics.
