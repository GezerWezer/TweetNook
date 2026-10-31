# Modules

Start at the public operation you're changing, then follow its storage and presentation paths. The main ownership boundaries are below; small supporting modules keep shared policy out of command handlers.

## Layers

Each package belongs to one layer, and a module may only import from its own layer or a lower one:

| Layer | Packages and modules |
| --- | --- |
| Interface | `cli/`, `cli_service.py`, `web/`, `__main__.py`, `system_install.py` |
| Features | `sync/`, `importer/`, `enrichment/`, `tagging/`, `jobs/`, `export/`, `stats/`, `client/`, `auth/`, `query_ids/` |
| Storage | `storage/` |
| Core | `core/`, `config.py` |

CI enforces the rule with `scripts/check_structure.py --strict`. Features may import each other.

Some modules sit where their dependencies put them rather than where their name suggests:

- `config.py`, `cli_service.py` and `system_install.py` stay at the top level. `install.sh` imports them by name from whichever release it installed, older ones included, and installed launchers run `python -m tweetnook.system_install`.
- Process locks are in `core/locking.py`, not `jobs/`, because opening the archive takes the archive lock while it migrates the schema.
- Response parsing (`core/timeline_parsing.py`) and FxTwitter payload adaptation (`core/fxtwitter_payloads.py`) are in core, apart from the HTTP code in `client/`, because storage reparses saved captures.
- Like order and search query parsing are in `storage/` because archive reads depend on them and the search filters compile to SQL against the archive schema.
- Pipeline reporting is in `jobs/`, not `sync/`, because every job reports through it.
- The LanceDB migration is in `importer/`, beside the archive import, because it loads its own configuration and reports progress like an import.

## Ownership

| Module or package | Owns |
| --- | --- |
| `__init__.py`, `__main__.py` | Package version and module entry point. |
| `cli/` | User commands, option definitions and terminal reports: one module per command group, with root commands registered in `cli/__init__.py`. |
| `cli/web.py`, `cli_service.py`, `system_install.py` | Personal server, systemd integration, managed installs and updates. |
| `config.py` | Pydantic config, defaults, sparse TOML writes, environment overrides and UI schema. |
| `core/` | Errors (`exceptions.py`), JSON compatibility (`fastjson.py`), process locks (`locking.py`), collection names (`collection_names.py`), redirected test requests and host isolation (`dev_replay.py`) and shared helpers (`utils.py`). |
| `core/extractor.py`, `core/media_metadata.py` | Canonical fields, relations, Article structures and media geometry. |
| `core/timeline_parsing.py` | Timeline/detail parsing and focal-result classification. |
| `core/fxtwitter_payloads.py` | FxTwitter payload adaptation to the GraphQL tweet shape. |
| `core/tweet_presentations.py` | Pure embedded/legacy tweet and media adaptation for normalized presentation. |
| `storage/` | SQLite archive: `ArchiveStore` in `backend.py`, one module per storage concern (see [storage](storage.md#layout)), shared search query parsing (`search_query.py`), chronological keys (`ordering.py`), and rebuildable like order (`like_order.py`). |
| `auth/cookies.py` | Explicit session credentials and per-collection requirements. |
| `client/base.py`, `client/features.py` | HTTP setup, pacing, headers, feature flags and transport errors. |
| `client/timelines.py` | X operation variables and timeline/detail requests. |
| `client/fxtwitter.py` | Cookie-free public lookup. |
| `query_ids/` | Operation defaults, cache and bundle discovery. |
| `sync/collection.py` | Collection preflight and resumable pages. |
| `sync/followups.py` | The follow-up plan and the work a sync runs after its collections. |
| `importer/archive.py` | Official export ingest: one import run from reading the export to its follow-up. |
| `importer/archive_input.py`, `importer/archive_rows.py`, `importer/archive_media.py` | Reading the export, adapting observations into storage ingestion batches, and copying media files. |
| `importer/archive_manifests.py` | Import manifests, their counts and warnings, and the files an import owns. |
| `importer/archive_enrich.py` | Live reconciliation and sparse completion, after an import or for one already saved. |
| `importer/archive_progress.py` | Status lines and progress shared by import and enrichment. |
| `importer/legacy_migration.py` | Explicit migration of a legacy LanceDB archive. |
| `enrichment/threads.py`, `enrichment/media.py`, `enrichment/unfurl.py` | Conversation expansion, file downloads and link previews. |
| `enrichment/articles.py` | Article body refresh. |
| `enrichment/resurrection.py`, `enrichment/check.py` | Authenticated retry policy and public availability checks. |
| `enrichment/prune.py`, `enrichment/prune_files.py` | Reachability policy and recoverable context/media cleanup; storage owns the row inventory and deletion. |
| `export/` | JSON output and export file names. |
| `stats/` | Ordered data sections shared by CLI and web consumers; each surface owns its presentation. |
| `tagging/tagger.py`, `tagging/prompts.py` | Gemini request planning, evidence, validation and generated tags. |
| `tagging/sdk.py` | Optional dependency checks and model catalog. |
| `tagging/accounting.py`, `tagging/pricing.py`, `tagging/rpd.py` | Local spend, Search usage and request allowances. |
| `jobs/context.py`, `jobs/resource_failure.py` | Locked job context and storage-failure handling. |
| `jobs/pipeline.py`, `jobs/outcome.py`, `jobs/activity_history.py`, `jobs/worker_logging.py` | Progress stages, the typed job outcome, durable run records and timestamped logs. |
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
| `web/index.html`, `web/fragments/`, `web/page.py` | The page shell and per-feature markup, assembled when served. |
| `web/static/js/app.js` | `tweetApp()`, the page's Alpine component: shared state and the merge of the feature method factories. See [architecture](architecture.md#browser-ui). |
| `web/static/js/core/html.js` | The escaping `html` template, `rawHTML` and `escapeHTML`. |
| `web/static/js/feed/` | The post list, routes and navigation, conversations and quotes; `render/` builds feed card markup. |
| `web/static/js/media/` | Feed video playback and players, the media viewer, and the motion of the viewer and phone post layer. |
| `web/static/js/features/` | One file per feature: Jobs and activity, Stats, tags, Setup (`setup.js` in the app, `setup-dialog.js` the dialog), Configuration, Schedule, Appearance, notices, automated tagging, the tweet menu and the profile card. |
| `web/static/js/*.js` | The header, the Settings shell, Analytics helpers, Articles, search autocomplete, themes and the setup completion icons. |
| `web/static/css/` | One stylesheet per feature, linked in cascade order. `tailwind.css` is generated by `dev/web/`. |
| `web/static/vendor/` | Alpine and its plugins, copied by the `dev/web/` build. |

`scripts/build_setup_help.py` generates the in-app cookie guide. `scripts/build_pages_demo.py` builds the public fixture demo, and `scripts/prepare_demo_content.py` prepares approved demonstration content. `dev/web/` holds the Node asset build and browser diagnostics.
