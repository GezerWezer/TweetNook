# TweetNook

TweetNook is a self-hosted archive for your Twitter/X bookmarks, likes, and tweets.
Run it on your own computer or  server, import an archive downloaded from
Twitter/X, keep it updated with automated syncs, and browse everything in a Web UI from
your network. Search by words, author, date, or tag to find something you saved
without scrolling through your account.

Want to see TweetNook before installing it? [View the live demo](https://gezerwezer.github.io/TweetNook/).

## Features

- **Archive your tweets:** save bookmarks, likes, and your own tweets, along with replies and quoted tweets
- **Import existing data:** bring in an official Twitter/X archive.
- **Inspect job results:** import, enrichment, and other used jobs stay in the Jobs
  panel after completion or failure. Select a job to review its saved steps and errors.
- **Browse in your browser:** read saved threads, view photos and videos, and
  explore quoted tweets, or read saved X Articles with rich text and focus mode.
- **Search and organize:** combine search filters, add your own tags, edit
  local descriptions, and hide tweets you no longer want to see.
- **Automated tagging:** with an optional add-on, you can use Gemini to suggest tags and describe media.
- **Keep it up to date:** run a sync from the web app or terminal, or schedule
  regular syncs.
- **Unavailable-tweet recovery:** periodically recheck
  for unavailable tweets that might return.
- **Export your collection:** save tweet data as JSON for use in other tools.

## Project background

This project began as a fork of [TweetXVault](https://github.com/lhl/tweetxvault),
which provides the foundation for this work. This version is developed independently.

Thanks to [rrika](https://github.com/rrika) for sharing their
[archive like-order research](https://github.com/lhl/tweetxvault/issues/2).

> [!NOTE]
> If you are updating from TweetXVault, see the [migration instructions](docs/maintenance.md#upgrade-or-migrate-an-older-archive).


## Getting started

You need macOS or Linux. The installer finds or downloads Python 3.12 and
creates a private environment for TweetNook. Live syncing also needs
a browser signed in to your Twitter/X account. You can import a downloaded archive
without connecting to Twitter/X first.

### 1. Install

Download the installer from the latest GitHub release:

```bash
curl -fL https://github.com/gezerwezer/tweetnook/releases/latest/download/install.sh -o install.sh
bash install.sh
```

On Linux with systemd, the installer starts a background service under a dedicated
`tweetnook` account. Run as root or allow sudo. On macOS, Linux without systemd,
or with `--no-service`, run as your regular user for manual startup.

The installer uses the published PyPI package. For development, see the
[development guide](docs/development/README.md).

To skip the prompt or include optional features:

```bash
bash install.sh --no-service
bash install.sh --service                          # Linux/systemd; starts the service
bash install.sh --no-service --automated-tagging    # Gemini tagging support
bash install.sh --no-service --legacy-migration     # Older TweetXVault LanceDB archives
```

Flags can be combined. Use `bash install.sh --help` for all options, including
`--version VERSION`, `--python PYTHON`, `--install-dir PATH`, and `--bin-dir PATH`.
For an existing service, use `tweetnook update` instead of rerunning the installer.
See [Configuration](docs/configuration.md#resolved-directories) for file locations.

If you already manage a Python 3.12+ virtual environment, pip remains supported:

```bash
python -m pip install tweetnook
```

### 2. Start TweetNook

For manual startup on macOS or Linux:

```bash
tweetnook serve
```

Leave the terminal running while using TweetNook. Press Ctrl+C to stop it.

If you chose service installation, it is already running and starts automatically
at boot. Check its status with `tweetnook service status`. To restart it:

```bash
tweetnook service restart
```

Restart requests sudo when needed. Other service administration commands are
listed in the [CLI reference](docs/cli-reference.md#service).

### Update TweetNook

Back up your archive before upgrading. For an installer-created systemd service,
run:

```bash
tweetnook update
```

This stops active work, upgrades TweetNook, and restarts the service, requesting
sudo when needed. Existing installations keep their service user and file paths.

For a manual installation, stop the server and all archive jobs, then upgrade
its private environment and start it again:

```bash
"$HOME/.local/share/tweetnook-runtime/venv/bin/python" -I -m pip --isolated install --upgrade tweetnook
tweetnook serve
```

Include previously selected extras in the upgrade target, for example
`"tweetnook[automated-tagging]"`, so their dependencies are updated too.


### 3. Open TweetNook

After TweetNook starts, open the Web UI using the address it shows, for example:

```text
http://[Machine IP]:8000
```

On first launch, the Web UI will guide you through setup.


## Everyday use

### Keep your archive up to date

Run a sync from the sync menu or `tweetnook sync` whenever you want to save new bookmarks and likes.
To automate it, open **Settings → Schedule**, choose when to run, and save.
The TweetNook server/service must stay running for scheduled syncs to work;
the browser can be closed.

**Sync failed** is reserved for critical connection, collection capture, archive
write, storage, or worker errors. Follow-up issues and tagging budget pauses stay
in Activity while a completed collection sync remains successful. See
[sync status](docs/syncing.md#understand-waits-and-status) for the failure boundaries.

For a full list of CLI sync commands, see [syncing](docs/syncing.md).

Run `tweetnook check` to check saved tweets for availability, retry unavailable
tweets, and keep community note versions. Enable **Check automatically** in
**Settings → Config → Check** and choose Daily, Weekly (the default), or Monthly,
plus a time and weekday or day of the month. **Check context tweets** is a separate
basic toggle, off by default. When enabled, its **Context cycle** defaults to 60 days;
checks spread the context tweets across that cycle. Saved tweets are checked every
run, including quotes returned with them. Checks require FxTwitter and the running
Web service. Saved check settings take effect immediately.

Use **Stop Task** in the web app or **Ctrl+C** in the terminal to interrupt a job.
Wait for it to finish stopping before starting another. Stopping TweetNook also asks its active Web or scheduled job to stop gracefully,
preserving already committed work.

### Search saved tweets

Type words and filters together in the web app's search box:

| Find… | Query |
|---|---|
| An exact phrase | `"night sky"` |
| Images from an author | `from:alice has:image` |
| Tweets from January 2026 | `since:2026-01-01 until:2026-02-01` |
| A local tag | `tag:"read later"` |
| Words in a tag description | `description:"red car"` |
| Links, excluding reposts | `has:links -is:retweet` |
| Tweets you hid | `is:hidden` |
| Current or past Community Notes, including quoted posts | `has:note` |
| Posts whose content was never captured | `is:uncaptured` |

**Hide** in a tweet's menu keeps it out of lists and searches until you choose
**Unhide**; it stays in your archive.

Posts without captured content are hidden from feeds and searches by default.
Use `is:uncaptured` to find them. Saved copies remain visible after a post
becomes unavailable on X. `has:note` includes removed notes in saved history
and notes on directly quoted posts, including quotes outside your collections.

In the web search box, edit `tag:` and `description:` values inside their
capsules, including spaces. Press **Escape** to continue typing after a capsule;
multiword values are quoted automatically.

Search results show an exact count; text-search counts load after the first results.
Matching text is bold in result cards, including quoted tweets.

The same queries work in the terminal:

```bash
tweetnook search '"night sky" has:image' --sort newest --limit 20
```

See [Search](docs/search.md) for all filters and search behavior.

### Add tags

Open a tweet's menu in the web app to add tags or a description. Click a tag to
find related tweets. **Settings → Tags** lets you merge or delete tags across
your archive.

## Optional Gemini tagging

Gemini can suggest tags for text and media tweets and add media descriptions.
Include `--automated-tagging` during installation. To add support later, follow
the [Automated Tagging setup instructions](docs/automated-tagging.md#set-it-up).

>[!IMPORTANT]
> Please read [Automated tagging](docs/automated-tagging.md) before enabling.

Open **Settings → Automated Tagging**, enable the controls, enter your Gemini API
key, choose a model and Free or Paid mode, and set the usage limits. Try one
archived tweet with **Test run**, then save when you want tagging enabled.

Once saved and enabled, tagging runs after sync. To tag existing eligible tweets
without syncing:

```bash
tweetnook tag
```

### Fill in missing details

Normal sync handles follow-up work automatically. You can also run individual
jobs when you need them:

```bash
tweetnook import enrich
tweetnook threads expand
tweetnook media download --retry-failed
```

These commands fill in eligible missing details, expand saved conversations, and
retry media downloads. Run only the jobs you need. See
[Enrichment](docs/enrichment.md) for articles and link previews too.

Media limits and progress count media items. A video or GIF can download both
the main file and a poster image while counting as one item.

Setup downloads media inside the archive import, after enrichment. If enrichment
or media stops, saved posts remain available and automatic preparation waits for
you to resume. For a full disk, free or add space, then run **Sync now** or
**Enrich archive** from Activity. Setup does not queue a separate media retry.

## Your files

| System | Default data folder |
|---|---|
| macOS | `~/Library/Application Support/tweetnook` |
| Linux | `~/.local/share/tweetnook` |

The folder contains the archive database, downloaded media, tags, and activity
history. [Configuration](docs/configuration.md#resolved-directories)
explains custom locations.

To back up, stop the app and all archive jobs, then copy the **complete data
folder** and your configuration.


To export tweet data for analysis or another tool:

```bash
tweetnook export json --out my-tweets.json
```

JSON exports do not include media files or everything needed to restore an
archive. Use a full folder backup.

After large imports or deletions, `tweetnook optimize` reclaims database space,
refreshes SQLite query-planner statistics, and merges full-text index segments.
Run it while other archive jobs are stopped. Normal writer jobs also perform
lightweight planner maintenance when they close the database.


## Documentation and help

Use `tweetnook --help` to list commands or add `--help` to a command, such as
`tweetnook sync --help`.

| Guide | Covers |
|---|---|
| [User guide](docs/README.md) | All feature guides and references |
| [Syncing](docs/syncing.md) | Older history, resuming work, and sync options |
| [Web app](docs/web-app.md) | Browsing, settings, schedules, and activity |
| [Importing](docs/importing.md) | Official Twitter/X archives |
| [Configuration](docs/configuration.md) | Settings, defaults, and file locations |
| [CLI reference](docs/cli-reference.md) | Commands and options |
| [Troubleshooting](docs/troubleshooting.md) | Common errors and next steps |
| [Development guide](docs/development/README.md) | Architecture, tests, and contribution workflow |

## License

[Apache License 2.0](LICENSE). See [TweetXVault](https://github.com/lhl/tweetxvault)
for the original project.
