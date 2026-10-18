# Maintenance

[User guide](README.md) · [Configuration](configuration.md) · [Troubleshooting](troubleshooting.md)

## Check database health

For a quick check:

```bash
tweetnook db check
```

For a full check:

```bash
tweetnook db check --full
```

These commands report database integrity; they do not repair corruption. Run
them while archive jobs are stopped. Opening an older database can also trigger
a format upgrade, so back it up first.

If a check fails, preserve the data folder and use a copy for further diagnosis.
See [Database problems](troubleshooting.md#database-problems).

## Understand archive statistics

`tweetnook stats` shows a fresh summary. Add `--detailed` to include
empty work queues, all unavailable reasons, and the full storage breakdown.

| Label or marker | How to read it |
|---|---|
| Unique tweets | A tweet counts once, even if it is both liked and bookmarked |
| Collection counts | The same tweet can count in several collections |
| Imported tweets | Includes stored context tweets, not only tweets from a Twitter/X ZIP |
| Media, articles, URLs | Saved records; not every record has a successful download or lookup |
| Local rehydrate gaps | Saved collection tweets missing their shared detail record |
| Backfill status | Whether older history is complete or has a saved position to resume |
| `!` | A nonempty queue that needs attention |
| `·` | An empty queue shown in detailed mode |

Total file sizes are measured. Some database storage categories are estimated
from samples; they need not add up like exact per-record measurements.

## Reclaim database space

After making a backup, run:

```bash
tweetnook optimize
```

This compacts the database, refreshes query-planner statistics, and merges
full-text search index segments. It can take time and needs temporary disk space, so
run it while other jobs are stopped. It does not delete media, exports, staged
uploads, logs, or cached files.

Normal writer jobs also perform lightweight planner-statistics maintenance when
they close the database. Ordinary browsing does not run compaction.

## Remove context outside the current scope

If you lower `sync.max_linked_depth` or `sync.reply_linked_depth`, context
fetched under the old settings keeps using space. To see what is now out of
scope, run:

```bash
tweetnook prune
```

This is a dry run: it reports the out-of-scope tweets, expanded threads, media
files, and database rows, and deletes nothing.

Scope follows the current `sync.max_linked_depth` and `sync.reply_linked_depth`
settings. TweetNook keeps:

- saved tweets and their conversations
- quoted and linked threads on the save path, including links in the saved
  author's own replies
- threads linked from other people's replies, when `sync.reply_linked_depth` is 1
- tweets quoted or retweeted by any kept tweet, without their threads

Everything else is out of scope, together with its media files, link records,
saved responses, media tags, and hidden markers. That includes threads you
expanded by hand with `tweetnook threads expand` that are not on the save path.
Use `--max-linked-depth` or `--reply-linked-depth` to preview a different scope
without changing your settings.

To delete them, run:

```bash
tweetnook prune --apply
```

## Repair older records

These commands are for specific problems in older archives. Back up before
using them.

### Rebuild details from saved responses

```bash
tweetnook rehydrate
```

This rebuilds searchable fields and related records from source responses
already saved in the database. It works locally; it does not contact Twitter/X or fetch
missing media. Malformed stored responses can make it fail. It is not a general
repair for database corruption.

## Upgrade or migrate an older archive

Migrating an older TweetXVault LanceDB archive requires the optional
`legacy-migration` dependencies. Stop the old app and its archive jobs, and back
up the complete data folder and configuration before starting.

For a new personal installation:

```bash
bash install.sh --no-service --legacy-migration
```

For a Linux service, use `--service` instead of `--no-service`, then stop it as
root with `tweetnook service stop` before copying the old archive.

If TweetNook is already installed, stop it and install the extra in its existing
Python environment. For a system install, run as root or prefix these with sudo:

```bash
tweetnook service stop
/opt/tweetnook/venv/bin/python -I -m pip --isolated install "tweetnook[legacy-migration]"
```

For a personal installation or older user-based service:

```bash
"$HOME/.local/share/tweetnook-runtime/venv/bin/python" -I -m pip --isolated install "tweetnook[legacy-migration]"
```

Adjust the Python path for a custom environment. Keep using the existing
environment rather than rerunning the installer.

TweetNook looks for `archive.lancedb/` in its
[data folder](configuration.md#resolved-directories), alongside `media/`. If the
old app used a different data folder, copy both folders into TweetNook's data
folder and keep the originals. Preserve any custom XDG settings so the app and
migration command use the same archive.

For system installs, the data folder is `/var/lib/tweetnook`; copied files must
be owned by `tweetnook:tweetnook`. Existing installations keep their current folders.

Start TweetNook with `tweetnook serve`, or run `tweetnook service restart` for an
existing systemd service so it loads the new dependencies. If no SQLite archive
exists, Web Setup detects the legacy source and offers **Migrate existing archive**.

Alternatively, keep the server and all archive jobs stopped and run:

```bash
tweetnook migrate
tweetnook db check
tweetnook stats
```

For personal installations, run these as the archive's owner.

Start or restart TweetNook after a terminal migration. Read the final migration
report carefully: a `partial` migration is usable but can skip unreadable source rows,
so a zero exit status alone does not prove that every record migrated. Keep the
original data until database checks, collection counts, known tweets, searches,
and media in the new SQLite archive are satisfactory.

See [Storage](development/storage.md#schema-migration) for format upgrade details.

## Manage logs and staged uploads

Activity history is kept under `activity/runs`. Defaults retain up to 100
completed runs for up to 90 days; change them in
[Configuration](configuration.md#activity). Gemini usage history under
`activity/ai-usage` is separate and worth preserving for spend controls.

An uploaded Twitter/X ZIP is staged at `setup/archive.zip`. Clear it through **Settings →
Setup** after importing if you no longer need that extra copy. This leaves
imported data intact.

Do not use [`import x-archive --regen`](importing.md#rebuild-imported-data) as a
cleanup command: it replaces data from all previous archive imports.

## Export JSON for other uses

```bash
tweetnook export json
tweetnook export json --collection bookmarks --out bookmarks.json
```

This creates a file for analysis or use in other tools. It includes saved tweet
content and metadata, but omits the media files and parts of the archive's
history and operational state. tweetnook cannot import this JSON as a restore.
Exports can contain private tweets and raw source data.

The exporter loads the result into memory, so large archives may need substantial
RAM. The default output goes under the data folder's `exports` directory.
