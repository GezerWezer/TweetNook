# Testing

[Development guide](README.md) · [Module reference](module-reference.md) ·
[Release](release.md) · [Documentation audit](documentation-audit.md)

Tests use temporary archives, synthetic account data, and mocked network calls.
Run focused tests while developing, then the broader checks required for the
change. This page maps features to test suites and explains isolation and
packaging checks.

## Standard validation

Use a writable cache outside restricted home directories when needed:

```bash
UV_CACHE_DIR=/tmp/tweetnook-uv-cache uv run ruff format --check
UV_CACHE_DIR=/tmp/tweetnook-uv-cache uv run ruff check
UV_CACHE_DIR=/tmp/tweetnook-uv-cache uv run pytest -q
node tests/js/test_web_assets.cjs
```

`pytest.ini` settings live in `pyproject.toml`: quiet default, asyncio auto mode,
function-scoped async fixture loop, and `tests/` discovery.

Ruff targets Python 3.12, line length 100, double-quote formatting, and selected
ASYNC/B/E/F/I/RUF/UP rules.

## Test isolation

Shared fixtures should:

- use `tmp_path` for config/data/cache roots;
- set XDG environment explicitly;
- use synthetic cookie values only;
- mock HTTPX/provider calls and sleep/time/randomness;
- close SQLite stores;
- avoid writing to the developer's real application directories;
- leave the repository worktree unchanged.

New real Twitter/X responses must be aggressively minimized/sanitized before becoming a
fixture. Remove cookies, headers, private text, IDs where unnecessary, URLs,
media, and account metadata. The existing
`tests/fixtures/dimitris_article_tweet_detail.json` is a deliberate public-
content regression fixture retaining article text, public IDs, and URLs; review
that exception explicitly rather than treating it as a sanitization example.

## Suite map

### CLI, configuration, auth, client

```text
tests/test_cli.py
tests/test_cli_web.py
tests/test_config.py
tests/test_auth.py
tests/test_query_ids.py
tests/test_client.py
tests/test_interactive.py
```

These cover help/command presence, flags, exit behavior, environment precedence,
legacy config removal, explicit credentials, query discovery/cache, status
classification, retries, pacing, and Web process lifecycle.

Every new user-facing flag/argument needs explicit Typer help, representative
`--help` assertions, behavior tests, and user-doc updates.

### Storage, migration, search, reporting, export

```text
tests/test_storage.py
tests/test_storage_sqlite.py
tests/test_migrate.py
tests/test_search.py
tests/test_stats.py
tests/test_export.py
```

Coverage includes schema creation/upgrades, FTS triggers/counts, indexes,
page-atomic rollback, source precedence, hydration, enrichment queues, tag rows,
legacy source splitting, parser/filter semantics, injection resistance, stats
meaning, and atomic JSON export.

Storage changes should add both success and injected-failure assertions. Any
schema change requires current-create, upgrade, validation, and newer-version
rejection coverage.

### Sync and lifecycle

```text
tests/test_sync.py
tests/test_pipeline.py
tests/test_jobs.py
tests/test_job_supervisor.py
tests/test_activity_history.py
tests/test_scheduler.py
```

These validate preflight-before-write, state/mode/page limits, multi-collection
partial failure, follow-up isolation/order, process locks, snapshots/issues,
worker groups/stop/finalization, retention, and deterministic schedule
calculation/conflicts.

Patch clocks/random offsets rather than sleeping. Never launch a persistent Web
worker from a unit test without guaranteed cleanup.

### Extraction and enrichment

```text
tests/test_extractor.py
tests/test_extractor_additions.py
tests/test_threads.py
tests/test_articles.py
tests/test_media.py
tests/test_unfurl.py
tests/test_resurrection.py
```

Coverage includes wrapper/tombstone/note parsing, graph records, canonical URLs,
focal absence, depth/refresh/limits, long article bodies/media, atomic downloads,
retry-failed selection, metadata parsing, reason confidence, due scheduling, and
circuit breakers.

Network tests should use fake transports and assert exact state transitions,
not only printed summaries.

### Official archive

```text
tests/test_archive_import.py
```

These generate representative ZIP/directory structures and cover identity,
owner mismatch, digest equivalence, supported datasets, sparse likes, media,
source-aware merge, sample/regen, interruption, and reconciliation/detail.

Do not commit a real Twitter/X archive fixture.

### Manual/automated tags and accounting

```text
tests/test_media_tags.py
tests/test_tag_cli.py
tests/test_optional_tagging.py
tests/test_tagging_prompts.py
tests/test_tagging.py
tests/test_gemini_foundation.py
tests/test_rpd.py
tests/web/test_automated_tagging_routes.py
```

Provider boundaries are mocked. Tests cover optional import safety, candidate
selection, prompt policy, structured parsing, Free batches/splits/retries,
Paid sequential interactions, Search reservations/accounting, finite-budget
stops, pricing cache, ledger rebuild/concurrency, RPD day/DST/reservations, Web
masking/catalog/test no-write behavior.

Every dry-run regression should assert provider/accounting side effects and lack
of tag/config persistence separately.

### Web backend and frontend

```text
tests/test_web_tweets_api.py
tests/web/test_activity_routes.py
tests/web/test_avatar_routes.py
tests/web/test_config_routes.py
tests/web/test_deps_server.py
tests/web/test_setup_routes.py
tests/web/test_stats_cache.py
tests/web/test_stats_routes.py
tests/web/test_storage_stats_routes.py
tests/web/test_tag_routes.py
tests/web/test_tweet_availability.py
tests/test_web_assets.py
tests/js/test_web_assets.cjs
```

Coverage includes route auth/status/contracts, SQL/path injection cases,
relationship cycles, placeholders, media containment/symlink escape, setup
credential isolation/upload, stats concurrency/cache, tag CRUD, frontend endpoint
wiring, script order, and critical UI strings/behaviors.

The JavaScript harness is static/source-oriented, not a full browser E2E suite.
Changes to complex interaction/layout/accessibility can still require manual
browser testing at desktop and narrow widths.

## Focused commands

Examples:

```bash
UV_CACHE_DIR=/tmp/tweetnook-uv-cache uv run pytest -q \
  tests/test_sync.py tests/test_pipeline.py tests/test_scheduler.py

UV_CACHE_DIR=/tmp/tweetnook-uv-cache uv run pytest -q \
  tests/test_storage.py tests/test_storage_sqlite.py tests/test_migrate.py

UV_CACHE_DIR=/tmp/tweetnook-uv-cache uv run pytest -q \
  tests/web tests/test_web_tweets_api.py tests/test_web_assets.py

UV_CACHE_DIR=/tmp/tweetnook-uv-cache uv run pytest -q \
  tests/test_tagging.py tests/test_gemini_foundation.py tests/test_rpd.py
```

Run the full suite before commit/release after cross-cutting config, storage,
pipeline, CLI, or Web asset changes.

## Packaging smoke tests

Repository tests can accidentally depend on un-packaged files. After a build:

```bash
uv build --clear --out-dir dist/release-X.Y.Z
uvx --from twine twine check \
  dist/release-X.Y.Z/tweetnook-X.Y.Z.tar.gz \
  dist/release-X.Y.Z/tweetnook-X.Y.Z-py3-none-any.whl
uv run --isolated --no-project \
  --with dist/release-X.Y.Z/tweetnook-X.Y.Z-py3-none-any.whl -- \
  tweetnook --help
```

Also smoke-import CLI/Web without the optional extra and exercise the optional
tagging import path with it. Verify HTML/CSS/JS package data is present in the
wheel.

## Test review checklist

- [ ] New behavior has success, failure, and interrupted/partial-state coverage.
- [ ] No real credential, account archive, or provider call is used.
- [ ] Filesystem work is isolated under a temporary root.
- [ ] Time/random/retry tests are deterministic.
- [ ] CLI options have explicit help and help tests.
- [ ] Schema changes cover new, old, and too-new databases.
- [ ] Web mutations test authentication and conflict behavior.
- [ ] Security-sensitive paths test traversal, masking, and containment.
- [ ] Optional dependencies remain optional at import time.
- [ ] User and developer docs match the tested contract.
