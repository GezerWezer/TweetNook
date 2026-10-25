# Modules

Start at the public operation you're changing, then follow its storage and presentation paths. The main ownership boundaries are below; small supporting modules keep shared policy out of command handlers.

| Module or package | Owns |
| --- | --- |
| `__init__.py`, `__main__.py` | Package version and module entry point. |
| `cli.py` | User commands, option definitions and terminal reports. |
| `cli_web.py`, `cli_service.py`, `system_install.py` | Personal server, systemd integration, managed installs and updates. |
| `config.py` | Pydantic config, defaults, sparse TOML writes, environment overrides and UI schema. |
| `auth/cookies.py` | Explicit session credentials and per-collection requirements. |
| `client/base.py`, `client/features.py` | HTTP setup, pacing, headers, feature flags and transport errors. |
| `client/timelines.py` | X operation variables, timeline/detail parsing and focal-result classification. |
| `client/fxtwitter.py` | Cookie-free public lookup and payload adaptation. |
| `query_ids/` | Operation defaults, cache and bundle discovery. |
| `extractor.py`, `articles.py`, `media_metadata.py` | Canonical fields, relations, Article structures and media geometry. |
| `storage/backend.py`, `storage/migrate.py` | SQLite records/schema and explicit legacy migration. |
| `like_order.py` | Rebuildable like order from captured observations. |
| `sync.py` | Collection preflight, resumable pages and follow-up plan. |
| `archive_import.py` | Official export ingest, manifests and sparse completion. |
| `threads.py`, `media.py`, `unfurl.py` | Conversation expansion, file downloads and link previews. |
| `resurrection.py`, `check.py` | Authenticated retry policy and public availability checks. |
| `prune.py` | Reachability planning and scoped context/file cleanup. |
| `search.py` | Shared query parsing, predicates, result types, sorts and cursors. |
| `export/` | Collection selection and JSON output. |
| `stats/` | Structured reports shared by CLI and web consumers. |
| `tagging.py`, `tagging_prompts.py` | Gemini request planning, evidence, validation and generated tags. |
| `automated_tagging.py` | Optional dependency checks and model catalog. |
| `gemini_accounting.py`, `gemini_pricing.py`, `rpd.py` | Local spend, Search usage and request allowances. |
| `jobs.py`, `locking.py`, `resource_failure.py` | Locked job context, process locks and storage-failure handling. |
| `pipeline.py`, `activity_history.py`, `worker_logging.py` | Progress stages, durable outcomes and timestamped logs. |
| `job_supervisor.py`, `scheduler.py` | Worker/queue ownership and scheduled submission. |
| `dev_replay.py` | Redirected test requests and host isolation. |
| `fastjson.py`, `utils.py`, `reminders.py`, `interactive.py` | JSON compatibility, shared helpers, notices and terminal interaction. |
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
