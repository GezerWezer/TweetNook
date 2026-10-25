# Testing

Use offline fixtures and temporary archives for normal development. Live X or Gemini requests need explicit intent and private credentials; they aren't part of the ordinary test suite.

## Python checks

```bash
uv sync --group dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Optional tagging tests need `uv sync --group dev --extra automated-tagging`; migration tests may need `--extra legacy-migration`. Keep a minimal-install test path too, so ordinary imports, browsing and manual tags continue working without optional SDKs.

Existing failures should be recorded with their command and scope. Targeted tests are useful while iterating; broaden validation when the change crosses a boundary or leaves a concern unresolved.

## Test suites

| Change | Starting tests |
| --- | --- |
| CLI/help and selected collections | `tests/test_cli.py`, `tests/test_cli_web.py`, `tests/test_cli_service.py` |
| Config and paths | `tests/test_config.py`, web configuration routes |
| Client and sync | Client, query-ID, auth, pagination and sync suites |
| Import and deferred Setup work | Archive-import, Setup routes and server-first suites |
| Storage and search | SQLite storage, search, pagination, like order and database-performance suites |
| Availability and note history | FxTwitter, check, resurrection and web availability suites |
| Enrichment and local assets | `tests/test_threads.py`, `tests/test_articles.py`, `tests/test_media.py`, `tests/test_unfurl.py`, `tests/test_resource_failure.py` |
| Gemini | Optional tagging, SDK, prompts, media tags, RPD and web tagging suites |
| Jobs and schedules | Supervisor, pipeline, activity-history, scheduler and jobs-route suites |
| Browser presentation | Python asset assertions and `tests/js/` harnesses |
| Replay and server lifecycle | `tests/test_dev_replay.py`, `tests/web/test_dev_replay_routes.py`, `tests/web/test_server_first.py`, `tests/web/test_deps_server.py` |
| Installer or release | Installer/system-install/service, package and demo tests |

Use the actual filenames under `tests/` when selecting a command. The [module reference](module-reference.md) maps operations to their owners.

For storage changes, cover repeated opens, rollback, source precedence, raw-capture retention, skipped migration rows and derived-index refresh. Availability tests need public-provider failures, ambiguous results, protected fallback, quote scope and observed note changes. Enrichment checks should exercise explicit targets, retry state, quoted content and file boundaries.

For job and server changes, cover startup without storage, Setup completion, manual queues, saved results, event-stream reconnection, shutdown and session authentication. Scheduler tests inject clocks and offsets for save/restart behavior, conflicts, month ends and timezone transitions. Tagging tests mock the provider while exercising reservations, retries, incomplete usage and previews that spend allowance without changing tags.

## Browser assets

```bash
node tests/js/test_web_assets.cjs
node tests/js/test_articles.cjs
node tests/js/test_media_playback.cjs
node tests/js/test_activity_stream.cjs
node tests/js/test_most_likes.cjs
node tests/js/test_demo_api.cjs
```

These are Node harnesses, not a substitute for opening the UI. For layout, navigation, fullscreen media, keyboard focus and touch gestures, verify the interaction in a browser and capture the relevant viewport/state.

After changing bundled dependencies or Tailwind input, run the [asset build](../../dev/web/README.md) and review generated changes. Ordinary JS/CSS source edits don't all require reinstalling Node dependencies.

## Documentation checks

```bash
uv run python scripts/build_setup_help.py
uv run pytest tests/test_setup_help.py tests/test_web_assets.py
```

Validate local links and heading fragments, parse fenced TOML/JSON, and run `bash -n` over shell examples. Walk visible CLI `--help` pages in isolated XDG roots. Compare the configuration reference against `AppConfig` defaults, flags against the command tree, and the API index against route decorators and router prefixes. Update those references when their source definitions change. Help should not create a config just by being viewed.

Preserve the browser section/step structure consumed by the Setup-help builder, including slash-separated browser aliases used for tab labels. Cookie names need to appear as individual code blocks for each supported browser, and the generated caution needs to explain password-level sensitivity.

## Performance checks

Use query-plan assertions for indexed ID, relation and filter access. Verify count/index cache invalidation after writes and rollback. Page through data large enough to reveal work proportional to the full archive, and check deep-feed detail navigation separately from the first page.

Tests should establish user-visible behavior or a meaningful safety/performance property. Avoid tests that only reproduce the implementation's internal shape without testing its result.
