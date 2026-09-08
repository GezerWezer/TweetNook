# CLI reference

[Documentation home](README.md) · [Connect Twitter/X](getting-started.md) ·
[Configuration](configuration.md) · [Troubleshooting](troubleshooting.md)

Use this page to look up command syntax and options. For a walkthrough, start
with [Getting started](../README.md#getting-started) or [Syncing](syncing.md).

```bash
tweetnook --help
tweetnook sync --help
```


| Task | Command section |
|---|---|
| Save tweets from Twitter/X | [`sync`](#sync), [`auth`](#auth) |
| Browse and search | [`view`](#view), [`search`](#search), [`stats`](#stats) |
| Import or export | [`import`](#import), [`export`](#export) |
| Fill missing content | [`articles`](#articles), [`threads`](#threads), [`media`](#media), [`unfurl`](#unfurl) |
| Generate tags | [`tag`](#tag) |
| Maintain the archive | [`db`](#db), [`rehydrate`](#rehydrate), [`optimize`](#optimize), [`migrate`](#migrate) |
| Manage the server | [`serve`](#serve), [`service`](#service), [`web`](#web) |

Append `--help` to any command for its built-in help.

## Root

```text
tweetnook [OPTIONS] COMMAND [ARGS]...
```

Root options:

- `--version` — print package version plus Git revision/dirty state when running
  from a checkout.
- `--install-completion` — install shell completion.
- `--show-completion` — print completion script.
- `--help` — show help.

Running with no arguments prints help and exits `2`. `sync` runs a bookmark-and-like sync when used without a subcommand.

## `sync`

```text
tweetnook sync [OPTIONS]
tweetnook sync all [OPTIONS]
tweetnook sync bookmarks [OPTIONS]
tweetnook sync likes [OPTIONS]
tweetnook sync tweets [OPTIONS]
```

Bare `sync` and `sync all` capture bookmarks and likes. Authored tweets require
`sync tweets`. After saving tweets, each command runs enabled follow-up jobs such as media
downloads and thread expansion.

Common options:

| Option | Meaning |
|---|---|
| `--full` | Restart timeline progress for the selected collections; keeps saved tweets |
| `--backfill` | Continue older history past duplicates without resetting state |
| `--article-backfill` | Rewalk pages so older tweets can acquire article fields |
| `--head-only` | Abandon unfinished older history and check recent tweets only |
| `--limit N` | Maximum timeline pages; per collection for `sync`/`all` |
| `--skip-resurrection` | Skip rechecks of unavailable tweets |
| `--skip-articles` | Skip automatic article refresh |
| `--skip-media` | Skip automatic media download |
| `--skip-unfurl` | Skip automatic URL metadata fetch |
| `--skip-threads` | Skip thread/quote/linked-status expansion |

`--max-linked-depth N` is available on bare `sync` and `sync all`, not on the
individual collection commands. `--head-only` conflicts with `--full`,
`--backfill`, and `--article-backfill`.

There is no `--skip-tagging`; use `tagging.enabled = false`.

See [Syncing](syncing.md) for checkpoints, partial success, and rate limits.

## `auth`

```text
tweetnook auth check
tweetnook auth refresh-ids
```

- `check` resolves credentials, resolves query IDs, and probes bookmark
  readiness. With `user_id`, it also enforces archive ownership and remotely
  probes likes and authored tweets; without it, owner comparison cannot run and
  those two collections are locally reported not ready without being requested.
- `refresh-ids` downloads Twitter/X discovery/bundle assets, updates the query-ID cache,
  and prints cache path/count. It does not require Twitter/X cookies, but network errors
  can propagate.

Neither command has user-facing options.

## `view`

```text
tweetnook view bookmarks [--limit N] [--sort newest|oldest]
tweetnook view likes [--limit N] [--sort newest|oldest]
tweetnook view tweets [--limit N] [--sort newest|oldest]
tweetnook view all [--limit N] [--sort newest|oldest]
```

Default limit is 20 and default sort is newest. Rows are deduplicated by tweet
ID, raw JSON is excluded, dates render in local time, and displayed text is
flattened/truncated to 280 characters. An empty collection is successful.

Use nonnegative limits and the two documented sort values; invalid values can
currently surface a backend exception instead of a friendly validation error.

## `search`

```text
tweetnook search QUERY [--limit N] [--sort SORT]
                         [--type TYPE] [--collection COLLECTION]
```

| Option | Default | Canonical values |
|---|---:|---|
| `--limit N` | `20` | positive result limit |
| `--sort` | `relevance` | `relevance`, `newest`, `oldest` |
| `--type` | tweet search | comma-delimited `post`, `article` |
| `--collection` | all | comma-delimited `bookmark`, `like`, `tweet` |

Plural aliases `posts`, `articles`, `bookmarks`, `likes`, and `tweets` are also
accepted, but canonical singular values are clearest. Structured filters are tweet-only; use
`has:article` from tweet search rather than structured filters with article
search. Tweet searches evaluate the complete archive before applying the requested
result limit; article search uses a separate stored-article scan.

See [Search](search.md) for the full grammar.

## `stats`

```text
tweetnook stats [--detailed]
```

Build a fresh report. Summary mode hides zero-count maintenance queues/reasons;
`--detailed` includes them and the complete storage breakdown. `!` marks a
nonzero attention queue, while detailed zero queues use `·`. Some component byte
figures are sampled estimates.

## `export`

```text
tweetnook export json [--collection COLLECTION] [--out PATH]
```

- `--collection` defaults to `all` and accepts bookmark(s), like(s), tweet(s),
  and all.
- `--out` defaults to
  `<data>/exports/export-<collection>-<UTC timestamp>.json`.

The exporter writes a temporary file and atomically replaces the destination.
It materializes the full export in memory and has no limit/streaming option.
JSON is the supported export format.

The file contains one entry per tweet, with its saved details and source JSON. It is not a complete backup or supported restore format.

## `import`

### Official Twitter/X archive

```text
tweetnook import x-archive ARCHIVE [OPTIONS]
```

`ARCHIVE` is a ZIP or extracted directory.

| Option | Meaning |
|---|---|
| `--regen` | Delete all archive-import-owned rows/manifests/media, preserving live rows, then rebuild |
| `--enrich` / `--no-enrich` | Enable/disable detail enrichment; enabled by default |
| `--detail-lookups N` | Bound detail requests; default `0` means all eligible when enrichment is enabled |
| `--sample-limit N` | Keep at most N per supported dataset, record a sampled manifest, and skip live follow-up |
| `--debug` | Additional diagnostic output |

`--no-enrich` still allows bulk live reconciliation. With it, the default
`--detail-lookups 0` disables detail enrichment; a positive value explicitly
enables that many lookups. Sample mode is for smoke tests—rerun without
`--sample-limit` for a normal completed import. See [Importing](importing.md).

### Continue enrichment

```text
tweetnook import enrich [--limit N]
```

Omitted limit means every row eligible at command start. Interruption exits
`130`; systemic abort normally exits `2`.

## `articles`

```text
tweetnook articles refresh [TARGETS...] [--all] [--limit N]
```

Targets are numeric tweet IDs or strings containing `/status/<id>` (normally Twitter/X
status URLs). Without targets, refresh preview-only candidates. `--all` selects
every article row and cannot be combined with explicit targets. `--limit`
applies to automatic preview/all queue selection; explicit targets currently
ignore it.

## `threads`

```text
tweetnook threads expand [TARGETS...] [--limit N]
                           [--refresh] [--max-linked-depth N]
```

- No targets: discover pending thread/quote/linked work.
- `--limit`: global target limit across phases.
- `--refresh`: refetch already expanded explicit targets and therefore requires
  explicit targets.
- `--max-linked-depth`: override linked-status traversal depth.

## `media`

```text
tweetnook media download [--limit N] [--photos-only] [--retry-failed]
```

Pending work is selected by default. `--retry-failed` includes prior failures;
`--photos-only` excludes videos/GIFs.

## `unfurl`

```text
tweetnook unfurl [--limit N] [--retry-failed]
```

Fetch canonical/final URL and HTML metadata for saved links. Prior failures are
excluded unless requested.

## `tag`

```text
tweetnook tag [TARGET] [--limit N] [--test] [--batch N] [--model ID]
```

`TARGET` is a numeric tweet ID or Twitter/X status URL. Without it, process the pending
queue. `--limit` bounds the main request loop; retries and split batches can add provider calls; `--batch` overrides
Free-mode tweets/request with a hard maximum of 20; Paid mode remains sequential.
`--model` overrides the configured ID. `--test` handles exactly one tweet,
including when it selects from the queue, and does not save generated tags or descriptions. It still makes real requests
that can use quota or incur charges.

For example:

```bash
tweetnook tag --test 1234567890123456789
```

## `rehydrate`

```text
tweetnook rehydrate
```

Rebuild normalized membership fields and secondary graph rows from stored
tweet/detail raw JSON. It is local-only and does not call Twitter/X. It preserves values
when raw data lacks replacements and does not explicitly remove every stale
secondary row. Malformed stored JSON can abort the command.

## `db`

```text
tweetnook db check [--full]
```

Default uses SQLite `quick_check`; `--full` uses `integrity_check`. On an
existing v5 database, the integrity check does not intentionally repair archive
rows or acquire `sync.lock`. Config/path initialization and WAL setup may still
write, however, and opening an older schema can migrate under `sync.lock`; run
it on a quiescent copy.

## `optimize`

```text
tweetnook optimize
```

Acquire the archive lock, run SQLite `VACUUM`, refresh planner statistics, and
merge FTS index segments. It can be expensive, requires
temporary disk space, and does not clean media or activity files.

## `migrate`

```text
tweetnook migrate
```

The legacy migration support is optional. Install it before running this command:

```bash
python -m pip install "tweetnook[legacy-migration]"
```

Look for the fixed legacy `<data>/archive.lancedb` source and copy it into the
current SQLite `archive.db`. The command has no options. Keep the old source
until row counts/search/database checks validate the result.

Migration exits `0` for `complete` and `partial` (a usable result with skipped
rows and a warning). Keep the source after a partial result. Missing source,
missing dependency/table, destination failure, and aborted migration exit `2`.

## `serve`

```bash
tweetnook serve
```

Run the server in the foreground for a service manager. It starts without an
archive, creates no PID file or default password, and uses configured host/port.

## `service`

Linux with running systemd is required. Pip installation does not install a service.
Install using the exact Python environment that contains TweetNook:

```bash
sudo /absolute/path/to/venv/bin/python -m tweetnook service install --user your-user
sudo /absolute/path/to/venv/bin/python -m tweetnook service stop
sudo /absolute/path/to/venv/bin/python -m tweetnook service start
sudo /absolute/path/to/venv/bin/python -m tweetnook service restart
tweetnook service status
sudo /absolute/path/to/venv/bin/python -m tweetnook service uninstall
```

The unit executes that Python with `-m tweetnook serve`, enables boot startup,
restarts on failure, and allows 90 seconds for shutdown. The service user must
be able to access the installed environment. `--user` defaults to `SUDO_USER`;
direct root installation requires an explicit user.

`install` reloads the unit, enables boot startup, and restarts the service even
when it is already active. This loads updated Python routes together with the
installed Web assets. An inactive service is started once.

`install` accepts `--data-home`, `--config-home`, and `--cache-home` for absolute
XDG parent folders (each contains the `tweetnook/` application directory).
Defaults are the selected user's `.local/share`, `.config`, and `.cache`.
Use matching overrides when migrating an existing installation with custom paths.
Uninstall stops/disables the service and removes only a TweetNook-managed unit;
application data and configuration are preserved. Unit tests never run real systemctl.

## `web`

```text
tweetnook web start
tweetnook web stop
tweetnook web restart
tweetnook web status
tweetnook web set-password
```

These commands have no user-facing options; host/port live in configuration.
`start` works without `archive.db` and leaves initial password creation to Setup. `status` returns `0` for a detected PID/listener
and `1` when stopped. Password changes require restart.

See [Web App](web-app.md).

## Exit statuses

Exit handling varies slightly by command, but the common convention is:

| Status | Meaning |
|---|---|
| `0` | Command completed or a benign no-work state |
| `1` | Local prerequisite/config/missing-archive/auth error |
| `2` | Project/API/lock/integrity error or Typer usage error |
| `130` | Interrupted archive enrichment |

Important exceptions:

- `migrate` accepts a `partial` result with a warning and exit `0`.
- Some uncaught HTTP, JSON, SQLite, `ValueError`, or lock paths can emit a
  traceback instead of the normalized status.
- A successful core sync can list recoverable follow-up issues.
- `sync all` can leave its first collection committed when the second fails.
- Current-schema database reads normally do not take the archive writer lock
  and can observe a WAL database while another process writes. Opening an older
  schema is an exception because it can migrate under `sync.lock`.

For the hidden server entry point and worker process behavior, see
[Web and jobs](development/web-and-jobs.md).
