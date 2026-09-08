# Web and Jobs

[Development guide](README.md) · [Search and Web API](search-and-web-api.md) ·
[Scheduler](scheduler.md) · [Configuration and Paths](configuration-and-paths.md)

The web server provides archive APIs and the browser interface. It handles
short reads and settings or tag updates directly; long-running jobs use CLI
subprocesses managed by `JobSupervisor`. The scheduler runs in the server process.

## CLI lifecycle

`cli_web.py` implements:

- dependency preflight; no existing archive required;
- stale PID detection;
- port detection through `lsof`/`fuser`;
- password creation through Web Setup; no default password;
- detached `python -m tweetnook serve` launch;
- `.web.pid` persistence;
- SIGTERM then SIGKILL stop policy;
- restart/status/password management.

`start` does not perform an HTTP readiness wait. `status` accepts either a live
PID or a configured-port listener, so it can report an unrelated process.

Public `serve` and the hidden compatibility alias load configuration and call `run_server()`; storage belongs to the lifespan. Direct ASGI
app import without that initialization leaves process-global state empty,
authentication disabled, and no store; it is a test/internal path, not the
supported runtime.

## Managed systemd lifecycle

`cli_service.py` writes a managed unit for the invoking Python environment.
`service install` atomically replaces the unit, calls `daemon-reload`, enables it,
and explicitly restarts it. `enable --now` alone leaves an active process running:
its imported Python routes can stay old while static files already come from an
updated package, breaking pagination or refreshed detail URLs. Restart uses the
unit's existing cooperative shutdown policy and also starts an inactive service.

`tweetnook update` already stops the managed service before upgrading its exact
Python environment, then starts it again. A manual pip upgrade alone does not
restart an existing server; follow it with `service restart` (or restart the
foreground process). Pip installation itself never modifies host services.

## FastAPI lifespan

`web/server.py` lifespan:

1. initialize configuration, activity retention, notices, and `JobSupervisor`;
2. read durable Setup state outside SQLite (missing state plus existing DB means completed);
3. reconcile initialization and attach an existing ready archive with `create=False`;
4. set up indexes/FTS and clear stats only when a store attaches;
5. start one scheduler only when both storage and completed Setup are available;
6. on shutdown, stop scheduling, gracefully stop the managed worker, wait for FTS/stats,
   and close storage.

`SetupCoordinator` persists worker intent/run IDs before launch, waits for local
import plus successfully tested auth, and starts enrichment once. Failed or
interrupted enrichment remains visible without automatic retries. Import/migration
can finish after the walkthrough; attachment makes browsing available without restart.

Current `ensure_scalar_indexes()`/`ensure_fts_index()` are no-ops. A missing
derived index is not repaired by these startup calls.

The shared SQLite connection uses `check_same_thread=False`. Stats report
collection serializes itself with an in-process lock; general route use relies
on SQLite and route behavior.

## Authentication

`verify_credentials`:

- permits requests when `password_hash` is absent;
- otherwise parses HTTP Basic;
- ignores username;
- SHA-256-hashes the candidate password;
- compares with `secrets.compare_digest`;
- returns 401 plus `WWW-Authenticate: Basic` on failure.

Fresh Setup writes the password hash as its final action, completes Setup, updates the live hash, and reloads the browser. No default password is generated.
`set-password` stores an unsalted SHA-256 and requires restart.

Root, archive application APIs, and `/media` are authenticated in normal
runtime. `/static` assets are not. FastAPI's default `/docs`, `/redoc`, and
`/openapi.json` endpoints are also public; they expose the API schema, not
archive rows by themselves. Media serving resolves base/target and rejects
traversal, symlink escape, and non-files.

There is no TLS, session/cookie auth, CSRF/origin validation, CORS middleware,
login throttling, CSP/security-header middleware, or authorization roles.

## Route map

| Area | Prefix/routes | Responsibility |
|---|---|---|
| Tweets | `/api/tweets`, `/api/authors` | Search/list/detail/quotes/author autocomplete |
| Tags | `/api/tags` | Per-tweet CRUD, global delete/merge, autocomplete/stats |
| Setup | `/api/setup` | Credential probe/save, staged archive upload/import |
| Config | `/api/config` | Effective/default/schema values and sparse changes |
| Activity | `/api/activity` | Status/start/stop/history/log/schedule |
| Stats | `/api/stats` | Fresh report, cached snapshot, compatibility sections |
| Storage stats | `/api/storage/breakdown` | Legacy detailed storage shape |
| Avatars | `/api/avatar/{user_id}` | Cached/fetched avatar or transparent fallback |
| AI | `/api/automated-tagging` | Settings, model catalog, picker, test, accounting |
| Media | `/media/{path}` | Authenticated contained local file serving |

## Setup security boundary

Credential update accepts only auth token, ct0, optional user ID. Masked values
preserve saved secrets. Candidate credentials are written to an isolated
temporary XDG config; current credential env variables are removed; an
`auth check` subprocess must succeed before real persistence.

Archive upload:

- accepts raw request bytes (not multipart);
- streams to a unique temporary path;
- caps raw bytes at 50 GiB;
- validates nonempty ZIP;
- atomically replaces fixed staged `setup/archive.zip`;
- rejects mutation during active jobs.

Deletion removes only the stage. Setup import starts `import x-archive <stage> --offline`.
Auth testing is a separate subprocess, so it can run during the import. Normal
APIs are gated until Setup completes; archive-dependent routes use `require_store`.
Persistent notices and Setup/activity status remain available without SQLite.

The general activity-import endpoint accepts an authenticated server-readable
directory or `.zip` path, making an authenticated browser a local-admin
filesystem principal.

## Config API

Responses mask Twitter/X cookies, password hash, and tagging key. The general UI schema
whitelists only Web host/port/avatar fields and blacklists dedicated-pane
sections. The POST endpoint validates any known model field rather than enforcing
that whitelist.

Config POST persists TOML but does not refresh process-global config,
supervisor, or scheduler. Dedicated Setup and schedule/tagging routes perform
their own relevant refreshes. Restart is the safe general consistency boundary.

## JobSupervisor

The supervisor prevents one local/external activity at a time using:

- its worker process state;
- durable activity status/PID checks;
- the command lifecycle lock used by the child.

Launch details:

- executable: current Python with `-m tweetnook`;
- new process session/group;
- origin and run ID environment;
- stdout/stderr to `console.log`;
- durable activity metadata/finalization.

Stop sends SIGINT to the process group for Web/scheduled jobs. CLI-origin
activity discovered through status is signaled by PID. Successful stop request
returns before child finalization.

`shutdown()` sends SIGINT, waits 45 seconds, then allows 10 seconds after SIGTERM
and 5 seconds after SIGKILL if needed. It joins the monitor without holding its
lock. Web stop allows 70 seconds; the systemd unit allows 90 seconds. Completion
callbacks cannot launch new jobs while shutting down.

## Activity history

Each run directory contains:

```text
metadata.json
events.jsonl
output.log
console.log       # Web/scheduled child
final.json        # after completion
```

`activity-status.json` is the atomically replaced current snapshot. Run IDs are
UTC timestamp plus random hex and are validated before filesystem access.

Log retrieval prefers `console.log`, falls back to `output.log`, and returns at
most the last 2,000,000 bytes (about 1.91 MiB). Cleanup skips active runs and
applies max-count/age limits.

`sanitized_argv()` knows only selected secret option names. There is no universal
redaction for arbitrary strings, output, exception details, paths, URLs, or raw
tweet data.

Activity status considers a run active only when snapshot state is running and
the PID exists. A supervisor worker before its first snapshot receives a
synthetic “Starting worker process” status. Last activity in the main drawer is
restricted to completed runs titled exactly `tweetnook sync`.

## Frontend

`index.html` is a large Alpine template. Local script order is:

1. `themes.js`;
2. `autocomplete.js`;
3. `app.js`.

Tailwind CSS and pinned Alpine/plugins are bundled under `static/`; fonts use
only local families. Rebuild assets with the [asset tooling](../../dev/web/README.md)
when HTML/JavaScript utility classes change. No CDN or provider-model request is
needed on page load. The local archive server must still be running and reachable.

Major frontend states:

- paged feed/search/collection/sort;
- single/split thread and quote navigation;
- availability placeholders and downloaded-media lightbox;
- manual/global tag UI;
- Setup, Config, Schedule, Logs, Appearance, Automated Tagging panes;
- stats and spend-history modals;
- activity drawer.

Preferences persist in browser `localStorage`. Split mode requires ≥1024px.

Known frontend defects/limitations include duplicate closing `head`, a no-op
lightbox button, placeholder help links, hidden narrow-screen Settings/Stats
controls, unsurfaced search truncation, no reduced-motion policy, uneven focus
styles, and an activity title that always says Archive Sync.

## Avatars and outbound requests

Avatar route order is cached JPG, author lookup, optional fetch. It considers
recent `tweet` and `tweet_object` rows that contain either supported profile-image
field, rewrites `_normal` to `_400x400`, and tries up to eight candidates with a
10-second synchronous HTTPX call. Successful responses are cached as JPGs;
transparent failures are returned with `no-store` and are never persisted, so a
later sync or recovered network connection can succeed. The browser adds a
versioned query parameter to invalidate older cached transparent responses.

Media download, URL unfurl, and avatar fetching follow stored external URLs.
There is no comprehensive egress/SSRF policy or general response-size cap.

## Stats cache

`web_stats_cache` provides one five-minute stale-while-revalidate entry per data
context. Initial concurrent callers share collection through a condition.
Stale callers receive old data while one daemon refreshes. Failure preserves
old data and sets a flag. Tag mutations currently do not invalidate it.

## Regression targets

- `tests/test_cli_web.py`
- `tests/test_job_supervisor.py`
- `tests/test_activity_history.py`
- `tests/web/*`
- `tests/test_web_tweets_api.py`
- `tests/test_web_assets.py`
- `tests/js/test_web_assets.cjs`
