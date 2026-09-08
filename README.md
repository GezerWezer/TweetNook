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
- **Browse in your browser:** read saved threads, view photos and videos, and
  explore quoted tweets and articles.
- **Search and organize:** combine search filters, add your own tags, and edit
  local descriptions.
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
> If you are upgrading from an older upstream installation that uses LanceDB, see the [migration instructions](docs/maintenance.md#upgrade-or-migrate-an-older-archive).


## Getting started

You need **macOS or Linux** and **Python 3.12 or newer**. Live syncing also needs
a browser signed in to your Twitter/X account. You can import a downloaded archive
without connecting to Twitter/X first.

### 1. Install

Install TweetNook with pip:

```bash
python3 -m pip install tweetnook
````

If you need to migrate from a TweetXVault LanceDB archive, install the migration support too:

```bash
python3 -m pip install "tweetnook[legacy-migration]"
```

To include automated tagging support:

```bash
python3 -m pip install "tweetnook[automated-tagging]"
```

### 2. Install the service

For a persistent homelab/server installation, install TweetNook as a system service:

```bash
sudo tweetnook service install
```

This installs and enables the TweetNook service so it starts automatically at boot and keeps running in the background.

Check its status with:

```bash
tweetnook service status
```

You can also manage it with:

```bash
sudo tweetnook service restart
sudo tweetnook service stop
sudo tweetnook service start
```

### Update TweetNook

For an installation created by `tweetnook service install`, update TweetNook with:

```bash
sudo tweetnook update
```

The command gracefully stops active Web and scheduled work, upgrades TweetNook in the exact Python
environment recorded by the managed systemd service, and restarts the service afterward.

### 3. Open TweetNook

After the service starts, open the Web UI using the address shown by TweetNook, for example:

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

For a full list of CLI sync commands, see [syncing](docs/syncing.md).

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
| Links, excluding reposts | `has:links -is:retweet` |

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
To install support in the active pip environment:

```bash
python -m pip install "tweetnook[automated-tagging]"
```

Restart the TweetNook server afterward. For a systemd installation, use the
service restart command; for a foreground `tweetnook serve` process, stop and
start it again. Developers using uv can install the extra once with
`uv sync --extra automated-tagging`.

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

[Apache License 2.0](LICENSE). See [upstream](https://github.com/lhl/tweetxvault)
for the original project.
