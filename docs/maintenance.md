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

For the older/upstream LanceDB format, keep `archive.lancedb` in the data folder.
Before migrating, install the one-time migration support:

```bash
python -m pip install "tweetnook[legacy-migration]"
```

If no SQLite archive exists, Web Setup detects it and offers **Migrate existing
archive**. Advanced users can run the same migration from the terminal:

```bash
tweetnook migrate
```

Read the final migration report carefully. A `partial` migration is usable but
can contain skipped rows, so a zero exit status alone does not prove that every
source record migrated. Keep the old folder until database checks,
counts, known tweets, and searches in the new SQLite archive are satisfactory.
A partial migration may skip unreadable source records.

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
