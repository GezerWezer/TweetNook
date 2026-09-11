# Troubleshooting

[User guide](README.md) · [Connect Twitter/X](getting-started.md) · [CLI reference](cli-reference.md)

Find the symptom below and start with its suggested check. If a command partly
succeeded, read its final summary before rerunning it: tweets already saved usually
remain in the archive.

| Problem | Start here |
|---|---|
| Cannot connect to Twitter/X | [Authentication](#authentication-problems) |
| Sync waits or fails | [Sync and request errors](#sync-waits-or-fails) |
| Another command is running | [Active jobs](#another-command-is-running) |
| Cannot open the app | [Web app](#the-web-app-does-not-open) |
| Scheduled sync is missing | [Scheduling](#a-scheduled-sync-did-not-run) |
| Import is incomplete | [Importing](#import-problems) |
| Tweets or downloads are missing | [Missing details](#missing-details-media-or-link-previews) |
| Search misses results | [Search](#search-results-are-missing-or-a-query-is-rejected) |
| Tagging is unavailable or stops | [Automated tagging](#automated-tagging-problems) |
| Database checks fail | [Database](#database-problems) |

## Authentication problems

### Missing `auth_token` or `ct0`

Copy both values from the same currently signed-in Twitter/X session. Follow
[Connect Twitter/X](getting-started.md), then run:

```bash
tweetnook auth check
```

Check the [configuration file location](configuration.md#resolved-directories),
especially on macOS. If you set environment variables earlier, they override
the file independently; an old variable can override a newly saved value.

### Missing `user_id`

Use your numeric Twitter/X account ID, not your username. Bookmarks can work without
it, but likes, your own tweets, and a complete connection check need it.

### Twitter/X returns 401 or 403

Your saved session may have expired or been rejected. Replace both cookies from
a current signed-in browser session. Check for old environment overrides, then
rerun `auth check`.

### “Archive belongs to another user”

Check that the configured account ID, browser session, and imported archive all
belong to the same account. Use separate data and configuration folders for a
different account. Do not change the database's owner to bypass this check.

## Sync waits or fails

### Rate limiting or long waits

Twitter/X limits requests, and tweetnook waits before trying again. Let the printed
wait finish, or stop the task and resume later. Reduce sync frequency if the
problem repeats. Avoid running several detail-heavy jobs close together.

[Configuration](configuration.md#sync) explains the delay settings. Repeatedly
restarting a waiting command does not solve the limit.

### A query-ID error or 404

Twitter/X sometimes changes the identifiers used for requests. Refresh them and check
your connection:

```bash
tweetnook auth refresh-ids
tweetnook auth check
```

If refresh itself fails, check network access to `x.com` and `abs.twimg.com`,
then try later. Persistent failures can need an application update.

### A 400 or repeated missing-tweet response

Twitter/X may have changed its request or response format. Refresh request IDs, check
authentication, and retry later. If the problem persists, update to a current
TweetNook release or [report the error](#report-a-problem).

A detail lookup that omits the requested tweet is not proof it was deleted.
tweetnook can stop after repeated omissions to avoid marking many tweets
incorrectly. Saved progress remains available.

## Another command is running

Check the activity drawer, open terminals, and any scheduled work. Wait for the
active job or use **Stop Task** and wait for it to finish stopping.

Restarting the web app gracefully stops its managed worker first. Do not delete
`sync.lock` or `command.lock`: the files can remain after a job ends, and their
presence alone does not mean the archive is locked.

## The web app does not open

### Archive setup is incomplete or still running

The server starts without `archive.db`. Open Setup and import your official
Twitter/X archive (recommended), migrate a detected upstream source, or explicitly
create an empty archive. Import can take hours or days; check Activity rather
than starting a second job. You may finish Setup while it runs.

Enrichment waits for both a completed local import and a successful saved Twitter/X
connection test. If Twitter/X was skipped, connect it later from Settings → Setup.
Interrupted initialization is shown as needing attention; retry the same import
without deleting existing data. Failed enrichment can be retried from Setup.
**Settings → Notifications** links to relevant failures and lets you dismiss
resolved notices; unread items are reflected by the Settings notification badge.

For systemd installations run `tweetnook service status` and inspect
`journalctl -u tweetnook`. Confirm that the service's XDG homes point to your
intended archive/configuration folders.

### Startup succeeds but the page does not load

Run `tweetnook service status` (systemd) or `tweetnook web status` (manual
background server), allow a moment for startup, and open the server
address on port 8000. On the server itself use
[http://127.0.0.1:8000](http://127.0.0.1:8000). Use your configured port if you
changed it. Check the latest web log if it still fails.

An unrelated process using the configured port can be mistaken for tweetnook
by status or stop commands. Inspect the reported process before stopping an
unknown one. You can choose a different port in [Configuration](configuration.md#web).

Do not run the manual `tweetnook web` background server alongside the managed
systemd service. `sudo tweetnook update` now refuses a live manual process recorded
in the service data directory. Stop it as the service user with `tweetnook web stop`,
then rerun the update. If update says the managed service did not stay running,
inspect `systemctl status tweetnook.service --no-pager`; another process may own
the configured port.

### The password is rejected

Enter any nonempty username and the password you set. To replace the password
from the CLI:

```bash
tweetnook web set-password
```

Then restart TweetNook using the mode you normally run: `tweetnook web restart`
for the manual background server, the systemd service restart command
for a service installation, or stop and restart a foreground `tweetnook serve`
process. Fresh installations create a password at the end of Setup; no default
password is assigned.

### The page is unstyled or controls do not work

Scripts and styles are served by the archive server, not public CDNs. Check that
the server is running and reachable, reload the page, and inspect failed `/static/`
requests in browser developer tools. For a source checkout, rebuild the
[web assets](../dev/web/README.md) after changing utility classes. Installed-font
discovery requires a supporting browser and HTTPS or localhost; otherwise use a
fallback font from the dropdown.

If Settings or Stats is missing, widen the browser window. Those controls are
currently hidden on narrow screens.

## A scheduled sync did not run

Open **Settings → Schedule** and check that it is enabled, saved, and using the
intended time zone. Also check the next run in the activity drawer.

- Keep the TweetNook server/service running; the browser can be closed.
- A random delay may move a daily, weekly, or monthly run later than the selected
  time. Turn it off if you want a fixed time.
- A due run is skipped when another tracked job is active.
- On restart, a saved overdue run is attempted once. Every missed interval is
  not replayed.
- The schedule saves bookmarks and likes. Run `sync tweets` separately for your
  own tweets.

See [Scheduling](web-app.md#scheduling) for setup details.

## Import problems

### Import finishes with authentication or enrichment warnings

The local data can import successfully even when live lookups are skipped. If
you imported through Web Setup, save and test your Twitter/X connection there; pending
enrichment starts automatically once the local import is complete. For a CLI
import or a manual retry, run:

```bash
tweetnook import enrich
```

`--no-enrich` skips detail lookups but still permits live timeline checks.
`--sample-limit` skips all live follow-up and imports only a sample.

### The archive is already imported

An identical completed archive is recognized to avoid duplicate work. If only
missing details need attention, use `import enrich`.

A sample can appear imported in Setup even though it is incomplete. Rerun the
original import without `--sample-limit` for a full import. Only use
[`--regen`](importing.md#rebuild-imported-data) if you intend to replace previous
imported data, and back up first.

### Bookmarks are missing after import

The importer supports your tweets, deleted tweets, likes, and matching media, but
not bookmarks. Save them with:

```bash
tweetnook sync bookmarks
```

## Missing details, media, or link previews

Use these commands to fill or retry missing work:

```bash
tweetnook import enrich
tweetnook media download --retry-failed
tweetnook unfurl --retry-failed
```

Run only the jobs you need. Detail lookups need Twitter/X credentials; downloads and
link previews contact saved URLs. Some hosts or tweets may no longer be available.

For videos and GIFs, remove `--photos-only` if you used it previously. Both the
main file and its preview image may be needed for completion. Media imported
from a Twitter/X ZIP must have filenames that can be matched to tweets.

For article or thread work, see [Enrichment](enrichment.md). An unavailable-tweet
placeholder does not mean the surrounding archive is corrupt.

## Search results are missing or a query is rejected

Use uppercase `AND`, `OR`, and `NOT`, double-quote multiword filter values, and
check that filter names and values are supported. Remember that `until` excludes
the date entered. Article-content searches do not accept tweet filters.

Broad text searches cover the complete archive. The web app intentionally labels
them **Results** without an exact count so it can return the first page without a
second full-match scan. If a broad query is slow, try more distinctive words or
an exact phrase; this narrows the work without changing query correctness.

See [Search](search.md) for examples and the full filter list.

## Statistics look outdated

Choose **Refresh** in Stats and wait for it to finish, or run
`tweetnook stats` for a fresh terminal report. Web statistics can be
cached for up to 12 hours, including after tag edits.

Some storage categories are estimates. Differences between them and exact file
sizes do not by themselves indicate corruption.

## Automated tagging problems

### The settings panel is missing

Install the optional dependencies into the Python environment used by the server:

```bash
python -m pip install "tweetnook[automated-tagging]"
```

Then restart TweetNook. For a systemd installation use
`tweetnook service restart`; for a foreground `tweetnook serve` process, stop
and start it again. Developers using a uv checkout can install once with
`uv sync --extra automated-tagging`; an exact later `uv sync` without the extra
can remove it. Normal runtime commands do not need `--extra`.

### A selected tweet cannot be tagged

Check that tagging is enabled, an API key is configured, and the tweet has usable
archived details. Media must be downloaded locally. Try one preview using the
[Automated tagging guide](automated-tagging.md#preview-one-tweet).

A model listed in Settings may not support your combination of media, thinking,
Search, and processing tier. Check its provider documentation or test another
compatible configuration.

### Tagging stops on quota, spend, pricing, or unknown usage

Read the stop reason and review usage history before raising a limit. Free
requests are counted before they are sent, including retries; failures may still
count. Paid runs with a finite budget pause when prices or usage are unknown.

The spend display is an estimate and excludes Search fees and other applications.
A preview still uses real provider requests. See
[Usage and spend](automated-tagging.md#view-usage-and-spend) for how to interpret
those figures.

## Database problems

If `db check` reports corruption, stop the app and every worker. Preserve the
complete data folder and run `db check --full` against a copy. Restore a verified
backup where possible.

Do not delete database companion files or use `rehydrate` as a general corruption
repair. If `rehydrate` itself fails on malformed saved JSON, keep a backup and
report the error so the affected records can be investigated.

For a partial LanceDB migration, keep the original `archive.lancedb` folder.
Read the printed result, then verify the SQLite archive with checks, counts, and
known searches. An exit code of zero alone does not establish a complete migration.

See [Backups and maintenance](maintenance.md).

## Report a problem

Include:

- Your operating system and the output of `tweetnook --version`.
- The command you ran, with private values removed.
- The final error and a short relevant log excerpt.
- Whether another job was running and whether you use custom data folders.

Keep Twitter/X cookies, API keys, private tweet content, and identifying local paths out
of the report. Do not attach a full configuration, database, export, or Twitter/X ZIP.
Activity logs are not guaranteed to remove every secret automatically.
