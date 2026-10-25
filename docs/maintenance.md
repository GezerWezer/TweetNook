# Maintenance

Back up the archive and its media regularly, especially before moving it or running a cleanup. A JSON export is not a backup.

## Starting and stopping

| Startup mode | Start | Stop or restart |
| --- | --- | --- |
| Foreground terminal | `tweetnook serve` | Press `Ctrl+C`; run it again to restart. |
| Personal background process | `tweetnook web start` | `tweetnook web stop` or `tweetnook web restart` |
| Linux service | `tweetnook service start` | `tweetnook service stop` or `tweetnook service restart` |

## Settings

Most settings are available in the web app. Use Settings > Schedule for collections and automatic syncs, Appearance for this browser's display preferences, Setup for your X connection and web password, and Automated tagging for Gemini.

Settings > Configuration has server settings and the options you use less often. Some optional extras, like automated checks, can be enabled here.

### Network access

The package defaults to port 8000 on all interfaces. The installer defaults to this computer only for manual installations and network access for the Linux service. Set the host to `127.0.0.1` if only the same machine should reach it. Use `0.0.0.0` for access from other devices, then open the machine's actual IP address in their browsers.

```toml
[web]
host = "127.0.0.1"
port = 8000
```

Restart TweetNook after changing the host or port. Use Settings > Setup to choose a web password, or `tweetnook web set-password` from a terminal. Restart after changing it through that terminal command.

### Settings file

Close TweetNook before editing `config.toml` directly, then restart it. You only need to include settings you've changed. For example:

```toml
[sync]
collections = ["bookmarks", "likes", "tweets"]

[schedule]
enabled = true
cadence = "daily"
time = "03:00"
timezone = "America/Detroit"
randomize_time = true
random_offset_min_hours = 0.0
random_offset_max_hours = 2.0

[check]
enabled = true
cadence = "weekly"
time = "04:00"
weekday = 0
```

Choose `hours`, `daily`, `weekly`, or `monthly` for cadence. Hourly schedules use `every_hours`; weekly schedules use `weekday` from 0 for Monday to 6 for Sunday; monthly schedules use `day_of_month` from 1 to 31. `timezone = "local"` uses the host's timezone. For an explicit timezone, use a name such as `America/Detroit`.

### Credentials

Setup saves the connection for you. Direct Python/pip processes also support these environment variables:

| Variable | Purpose |
| --- | --- |
| `TWEETNOOK_AUTH_TOKEN` | X session cookie. |
| `TWEETNOOK_CT0` | X connection cookie. |
| `TWEETNOOK_USER_ID` | Numeric account ID, needed for likes and your own tweets. |
| `TWEETNOOK_GEMINI_API_KEY` | Optional Gemini key; `GEMINI_API_KEY` is also accepted. |

For direct Python/pip processes, environment credentials take priority over saved values. If changing a value in Setup doesn't affect that process, check its environment overrides. Avoid putting secrets into shared shell history, screenshots, or support logs.

The installer's global Linux command runs archive operations as the `tweetnook` service account and uses the service archive. It discards caller credentials and XDG overrides, and can request sudo. Save service credentials through Setup or the service's `config.toml`.

### Storage and jobs

Avatar settings control whether profile pictures are downloaded and how much cache space they may use. The default limit is 512 MiB. Analytics has a daily refresh time, 05:00 by default. Activity-log retention defaults to 200 runs and 180 days; it doesn't control how long posts are kept.

Web saves update the running server's settings immediately, including avatar downloads, cache limits, schedules and the Analytics refresh time. Active jobs keep the settings they started with; later jobs use the saved values. Host and port changes still need a restart.

Restore defaults applies those defaults to running services too, while retaining your X login, web password, and API key. Appearance preferences belong to the browser and are separate.

The complete list of settings, defaults, environment overrides, and restart rules is in the [technical configuration reference](development/configuration-and-paths.md). Use that when administering an installation or changing advanced options.

## File locations

| Installation | Configuration | Archive and media | Cache |
| --- | --- | --- | --- |
| Personal Linux | `~/.config/tweetnook/` | `~/.local/share/tweetnook/` | `~/.cache/tweetnook/` |
| Personal macOS | `~/Library/Application Support/tweetnook/` | `~/Library/Application Support/tweetnook/` | `~/Library/Caches/tweetnook/` |
| Installer's Linux service | `/etc/tweetnook/` | `/var/lib/tweetnook/` | `/var/cache/tweetnook/` |

The settings file is `config.toml`. Archive files include `archive.db` and the `media/` directory. Keep both when moving or backing up your archive. Treat the configuration and other session files as private.

Personal installations and direct Python/pip processes honor `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, and `XDG_CACHE_HOME`. Each points to a parent directory; TweetNook adds `tweetnook/`. The installer's global Linux command always uses the service paths above. A service created separately has its own environment, which may differ from a personal shell.

## Backups

1. Stop TweetNook and wait for its active job to stop. Stop any separate command-line jobs too.
2. Copy the whole archive/data directory and configuration directory to your backup location. Include downloaded media and any companion files beside `archive.db`.
3. Keep the backup private because it can contain account and session information.
4. Start TweetNook again.

For a restore, stop TweetNook, put those directories back at the intended locations, check that the account running it can read and write them, then restart. Keep the original backup until you've opened the restored archive successfully.

Use [file locations](#file-locations) to find the right directories. The installer-created Linux service uses different paths from a personal installation.

## JSON exports

```bash
tweetnook export json --out /path/to/backup/posts.json
tweetnook export json --collection bookmarks --out /path/to/backup/bookmarks.json
```

Collections can be `all`, `bookmarks`, `likes`, or `tweets`. Without `--out`, TweetNook writes to an `exports/` directory under its data location. JSON export doesn't copy your downloaded media or replace a full installation backup.

## Updates and add-ons

For a managed Linux service, run:

```bash
tweetnook update
```

TweetNook updates and restarts the service, keeping your archive, settings, and installed add-ons.

You can also rerun the installer to update an installation it created or add optional features. It keeps your startup mode, paths, existing add-ons, archive, and settings. A background service is stopped and restarted for you; stop manually started TweetNook processes before updating.

```bash
bash -c "$(curl -fsSL https://github.com/gezerwezer/tweetnook/releases/latest/download/install.sh)"
bash -c "$(curl -fsSL https://github.com/gezerwezer/tweetnook/releases/latest/download/install.sh)" -- --automated-tagging
```

`--reinstall` reinstalls the current version, while `--version VERSION` chooses an exact published version. They aren't used together to change versions.

If you installed with pip in your own environment, update in that environment and restart:

```bash
python -m pip install --upgrade tweetnook
```

Keep optional extras in the package specification when you need them. An existing service created outside the installer should be maintained with its own service/update commands.

## Archive health

```bash
tweetnook stats
tweetnook stats --detailed
tweetnook db check
tweetnook db check --full
tweetnook optimize
```

Stats shows collections, saved history, unavailable posts, and outstanding completion work. `db check` checks the archive, and `--full` requests a more thorough check. `optimize` reclaims unused space and refreshes the archive's search preparation. It doesn't delete your posts.

Archive checks report integrity; they don't repair damaged files. If a check reports a problem, preserve the complete data directory and work from a copy. Back up before opening an older archive for an upgrade. Optimize can need temporary disk space and doesn't remove media, exports, uploaded ZIPs, or logs.

`tweetnook rehydrate` rebuilds extracted details from saved source material. It works locally, doesn't fetch missing media, and isn't a repair for damaged archive files.

## Cleanup

Pruning keeps saved collection posts and removes conversation or linked-post context outside your follow-depth settings. Turning off a collection for future syncs leaves its saved posts intact. Pruning can remove conversations you expanded by hand, so make a backup before applying it.

```bash
tweetnook prune
```

The default is a preview of what would be removed. Read the result, then use `tweetnook prune --apply` only if it matches what you want. `--max-linked-depth` and `--reply-linked-depth` let you preview or apply a different context scope for that run.

## Uninstalling the service

`tweetnook service uninstall` stops and removes TweetNook's managed Linux service. It preserves data and configuration. Removing the program or its service is separate from deleting your archive.

## Statistics

| Figure or marker | Meaning |
| --- | --- |
| Unique tweets | Each post counts once, even if it's in several collections. |
| Collection counts | One post can count in more than one collection. |
| Imported tweets | Context posts saved in the archive, not only an official ZIP import. |
| Media, Articles, URLs | Saved records, including work that hasn't downloaded or completed yet. |
| Local rehydrate gaps | Saved posts whose extracted details can be rebuilt from existing saved data. |
| Backfill status | Saved progress through older collection history. |
| `!` | Outstanding work needs attention. |
| `·` | An empty work queue included in detailed output. |

Backfill status describes the saved progress through older collection history:

| Status | Meaning |
| --- | --- |
| `resume older` | Older history is incomplete and has a saved position to continue from. |
| `incomplete` | Older history is marked incomplete, but no position was saved. Run another sync to continue collecting it. |
| `saved cursor` | An older-history position is saved, but the collection isn't marked incomplete. Check the last run if this is unexpected. |
| `none saved` | No older-history position or incomplete marker is saved. This can mean history finished or that no progress has been recorded yet. |

Check the last sync and collection counts before treating history as complete. X may still have withheld older items. Filesystem totals are measured, while some category breakdowns are estimates.

<a id="upgrade-or-migrate-an-older-archive"></a>

## Migration

Keep the old archive and its `media/` folder. Stop the old app, make a full backup, and install the migration add-on into the environment running TweetNook. The current installer can add it:

```bash
bash -c "$(curl -fsSL https://github.com/gezerwezer/tweetnook/releases/latest/download/install.sh)" -- --legacy-migration
```

Place `archive.lancedb/` alongside `media/` in TweetNook's data directory. If using the installer-created Linux service, preserve the service account's ownership of the copied files. Setup offers Migrate existing archive when it detects the older data and no current archive exists.

For a terminal migration, leave servers and archive jobs stopped, run `tweetnook migrate`, then check `tweetnook db check` and `tweetnook stats` before restarting. Use the archive owner's account and the same file-location settings as the server.

Read the final migration report. A partial migration can be usable while some old records were skipped. Keep the original until counts, known posts, search and media look right in the new archive.

## Uploads and logs

Settings > Setup can clear a staged archive ZIP after import. That removes an extra uploaded copy and leaves imported content intact. Activity-log retention is separate from saved-post retention; Gemini usage history under `activity/ai-usage/` is also separate and should be included in backups so usage controls and charts remain available.

JSON export can contain private post information. TweetNook doesn't use that export as a restore format, and very large exports can need substantial memory. Keep both export files and full backups private.
