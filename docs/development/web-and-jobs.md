# Web and jobs

`web/server.py` owns FastAPI startup and teardown. The browser can load before an archive exists, which keeps installation and Setup in the same application as ordinary browsing.

## Startup

The lifespan creates the job supervisor, notices, Setup coordinator, and other shared services. An existing archive can attach immediately. A fresh installation exposes Setup while archive-dependent routes use `require_store` to return a clear not-ready response.

Database creation and schema migration establish the indexes. After attaching a store, the server warms existing archive data in the background and schedules cached-stat refreshes. Warmup does not repair missing indexes. The scheduler starts only after storage is ready and Setup is complete. Counters and Setup polling run separately from the first feed request.

## Setup state

`SetupCoordinator` persists `setup/state.json` with initialization mode, run identities, completion state, verified/skipped auth, and deferred-enrichment intent. A file's mere existence doesn't prove initialization or Setup finished successfully.

Initialization can import a staged ZIP, migrate a legacy archive, or create an empty archive. Import/migration work runs as a supervised worker. A Setup import saves local data, enriches through FxTwitter (with optional account fallback), then downloads media within the same job. It does not reconcile live collections yet.

After a successful import, completed Setup, verified auth and confirmed collections allow `import enrich` to run once as the first sync, including normal media follow-up. Failed/stopped import follow-up retains committed local data and the failed result, and cancels automatic first-sync intent until the user resumes sync or enrichment.

Completion requires an initialization choice, a verified or explicitly skipped connection, and either an existing/new password or an explicit no-password choice. Reopening Setup should keep prior archive work and credentials unless the user asks to change them.

## Authentication

`web/deps.py` checks the password through HTTP Basic, ignores the username, and issues a signed session cookie for subsequent browser requests. The signing key lives beside the archive; sessions bind to the current password hash, so changing the password invalidates old sessions. HTTPS responses add the Secure cookie attribute. Completing Setup issues the signed session in the completion response and refreshes status/resources in place, so the completion panel does not require navigation. Password changes renew the current browser session and invalidate previous sessions.

An unset password permits access. This is a deliberate local-use option, so the UI must require an explicit choice instead of treating an empty password field as consent. Apply authentication consistently to reads, writes, logs, media and developer replay endpoints.

## Applying settings

All web-save routes use `reload_server_config` to update the running services from one validated snapshot. The shared lifecycle lock prevents a reload from overlapping archive attachment or teardown. Avatar-limit changes wake cleanup, and Analytics time changes reschedule the existing refresher thread. The active worker and listener stay in place.

## Workers

`JobSupervisor` launches commands with the installed Python, explicit run identity/origin, and durable logs. It owns one active worker, records exit status, and can queue supported jobs. A separate process lock prevents a CLI writer from racing a web worker.

`web/jobs.py` defines job names, CLI arguments and option types. Validate values before building arguments, reject unsupported options, and keep flags consistent with the CLI. Queue entries can be removed before launch. Manual queues live in memory and are cleared at shutdown; run history and Setup's first-sync intent are persisted separately. Avoid a second implementation of an operation in a route handler.

## Logs

`PipelineReporter` publishes stages and final outcomes. `activity_history.py` keeps per-run records and logs under `activity/runs/`; retention is configurable. Worker logs receive timestamps. Notifications identify events that require attention and can link to a run.

The browser receives live activity over an event stream, with polling as a fallback. `/api/jobs` returns each job's latest saved result, including import and enrichment. Jobs follows active work, then shows an unsuccessful run or the most recent result. Saved results remain available after reconnecting or restarting the server.

## Shutdown

Stop requests cooperatively interrupt the owned worker. Shutdown stops scheduling new work, asks the worker to finish, waits within the supervisor's grace period, then tears down background readers and closes the store. The personal-web and system-service timeouts allow that sequence to complete.

Keep process identities scoped to TweetNook. Service updates need to stop/restart the right unit and any owned workers; personal web commands must not mistake a client connection for the listening server.

## Developer replay

1. On the source server, set a web password and enable `web.dev_replay_source`. The source needs an existing archive and refuses replay requests without a password.
2. Give the test install its own XDG directories, as in the [development setup](README.md#setup). Start it with `tweetnook serve --dev-tools` or `TWEETNOOK_DEV_TOOLS=1`.
3. In the test install's Setup, enter the source URL and web password, then connect.
4. To simulate a connected X account, enter nonblank `auth_token` and `ct0` values. A user ID is optional for this connection check.

X requests use the replay source. Other requests using the replay transport are blocked; Gemini and OpenRouter requests can still reach their live services. The test install can use placeholder media and skip linked quote expansion or link previews to focus a test.

With a connected replay source, Setup skips the live credential probe. Clearing either cookie or entering only whitespace disconnects the simulated account. Developer tools without a replay source keep the normal probe.

To simulate job failures, put `fail`, `ratelimit`, or `offline` in `auth_token` for a request failure, rate limit, or network error. Keep `ct0` nonblank when testing a connected account.

The source and test archives remain separate. This is different from the static public demo, which uses a packaged fixture and no server database.

Use distinct XDG roots for the test install and keep replay tests away from production write paths. The [testing guide](testing.md) lists the lifecycle and replay checks.

## Caching

The archive warmer prepares like order and reads common archive pages through a private read-only connection. Requests during warmup coalesce into one follow-up pass. Shutdown interrupts its reads, and managed job completion requests another warmup.

Statistics use private readers so a long query doesn't stall the shared connection. The previous snapshot remains available during refresh and after a failed refresh. `web.analytics_refresh_time` controls daily refreshes; cached results also revalidate when stale. A caller with uncommitted writes collects on its own connection.

Avatar fetching uses a separate executor so remote image waits don't occupy feed-request threads. Cached pictures remain available when downloading is disabled; failed or transparent responses aren't saved. Periodic size cleanup uses last-use timestamps and persisted run state, independently of sync scheduling.

Browser caches and prefetch reuse feed and conversation results during navigation. Search suggestions discard stale responses. Media loads lazily, and activity updates preserve the visible feed and playback.

## HTTP caching and files

Text/API responses use gzip when appropriate, while media/avatar paths bypass it to preserve compressed bytes and range behavior. Document routes add static content hashes, ETags and no-cache revalidation. Matching versioned static assets can be immutable; unversioned/stale-hash assets revalidate. Saved media is private-cacheable. Failed-fetch fallbacks use `no-store`; fallbacks when downloading is disabled use `private, max-age=300`.

`/media` resolves the base and target and rejects traversal, symlink escape and non-files. Other filesystem boundaries and public endpoints are covered under [trust and security](architecture.md#trust-and-security).

## Run files and timeouts

A run directory can contain `metadata.json`, `events.jsonl`, `output.log`, worker `console.log`, and `final.json`. The current `activity-status.json` snapshot uses atomic replacement. Log retrieval prefers worker console output and bounds it to the last 2,000,000 bytes. Retention skips active runs.

Stop sends SIGINT to owned process groups, or the PID of discovered CLI activity. Shutdown waits for cooperative interruption, then escalates to SIGTERM and SIGKILL if needed. Timeout constants in `job_supervisor.py` leave the personal server and systemd enough time to finish worker shutdown.

Candidate X credentials are tested through an isolated temporary config with inherited credential overrides removed before real persistence. Upload staging streams to a unique temporary path, enforces raw bytes/nonempty ZIP, then replaces the fixed stage. Setup uses `--defer-followup` to enrich and download media without reconciling live collections; the first sync waits for the Setup gates described above.
