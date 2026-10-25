# Commands

Run `tweetnook --help` to see the command groups, or add `--help` after any command for its arguments. `python -m tweetnook` runs the same command-line interface when you need to select an environment explicitly.

```bash
tweetnook --version
tweetnook sync --help
tweetnook import x-archive --help
```

## Common commands

| Task | Command |
| --- | --- |
| Sync your selected collections | `tweetnook sync` or `tweetnook sync all` |
| Sync one collection | `tweetnook sync bookmarks`, `likes`, or `tweets` |
| Import an official archive | `tweetnook import x-archive PATH` |
| Continue imported post completion, then sync | `tweetnook import enrich` |
| Expand saved conversations | `tweetnook threads expand` |
| Refresh saved Articles | `tweetnook articles refresh` |
| Download media | `tweetnook media download` |
| Fill link previews | `tweetnook unfurl` |
| Generate optional Gemini tags | `tweetnook tag` |
| Check current availability | `tweetnook check` |
| Search locally | `tweetnook search QUERY` |
| Show archive totals | `tweetnook stats` |

Many commands accept `--limit`, but its unit depends on the task. Sync counts pages per collection; import completion counts posts; media counts items; tagging counts main-loop requests, with retries and split batches able to add calls. A media item can include a main file and a poster image. A limit on one stage doesn't limit every stage that follows it.

## Sync options

These apply to `sync`, `sync all`, and the named collection subcommands. `--head-only` cannot be combined with `--full`, `--backfill`, or `--article-backfill`.

| Option | Use |
| --- | --- |
| `--full` | Restart the targeted collections' saved sync progress, keeping archived posts. |
| `--backfill` | Continue through known posts to fill gaps and older history. |
| `--head-only` | Fetch recent posts only and clear the saved older-history position. |
| `--article-backfill` | Revisit pages for Article information. |
| `--limit N` | Fetch at most N timeline pages per collection. |
| `--skip-threads` | Leave conversation expansion for later. |
| `--skip-resurrection` | Leave unavailable-post rechecks out of this sync. |
| `--skip-articles` | Leave Article completion for later. |
| `--skip-media` | Leave media downloads for later. |
| `--skip-unfurl` | Leave link previews for later. |

`--max-linked-depth N` and `--reply-linked-depth 0|1` are available on `sync`, `sync all`, and `threads expand`. They aren't options on the named collection sync subcommands. See [syncing](syncing.md) for schedules and [post details](syncing.md#post-details) for the follow-up jobs.

## Import options

`tweetnook import x-archive PATH` accepts a ZIP or extracted directory. The import flags are `--offline`, `--defer-followup`, `--regen`, `--enrich`/`--no-enrich`, `--detail-lookups N`, `--sample-limit N`, and `--debug`. [Importing](importing.md#import-options) explains their effects, including which choices still contact X.

`tweetnook import enrich --limit N` limits imported post completion before an unrestricted sync of the selected collections. Leave the limit out to process every imported post currently eligible for completion.

## Post details and checks

| Command | Arguments and options |
| --- | --- |
| `articles refresh [TARGETS...]` | IDs/URLs; `--all` revisits complete Articles; `--limit N` limits automatic selection and is ignored when explicit targets are supplied. |
| `threads expand [TARGETS...]` | IDs/URLs; `--refresh` revisits explicit targets; `--limit N`, `--max-linked-depth N`, `--reply-linked-depth 0|1`. |
| `media download` | `--limit N`, `--photos-only`, `--retry-failed`. |
| `unfurl` | `--limit N`, `--retry-failed`. |
| `check` | `--skip-saves`, `--skip-context`, `--skip-unavailable`, `--detail-budget N`. |
| `tag [TARGET]` | An archived ID/URL; `--test` previews; `--limit N` counts requests; `--batch N` changes Free batch size; `--model MODEL` overrides the model. |

Tag previews use real provider requests and allowances. See [automated tagging](automated-tagging.md) before enabling it.

## Browsing and exports

```bash
tweetnook view all --limit 20 --sort newest
tweetnook search 'from:alice has:video' --sort oldest
tweetnook export json --collection bookmarks --out /path/to/posts.json
```

`view` has `all`, `bookmarks`, `likes`, and `tweets` subcommands. Each accepts `--limit` (default 20) and `--sort newest|oldest`.

Search accepts a query argument, `--limit` (default 20), `--sort relevance|newest|oldest`, `--type post|article|post,article`, and `--collection bookmark|like|tweet` with comma-separated combinations. Collection filtering and the chosen sort apply before the result limit, including for Articles. See [search](search.md) for filters and examples.

JSON export accepts `--collection all|bookmarks|likes|tweets` and `--out PATH`. It doesn't include the downloaded media files. Without a path, exports go under the archive's `exports/` directory.

## Server commands

| Command | Purpose |
| --- | --- |
| `serve` | Run in the foreground and complete Setup in your browser. |
| `web start`, `web stop`, `web restart`, `web status` | Control a personal background server. |
| `web set-password` | Prompt for a new web password; restart afterward. |
| `service install` | Install a Linux systemd unit. Administrator access is required. |
| `service start`, `service stop`, `service restart`, `service status` | Control the managed Linux service. |
| `service uninstall` | Remove its managed service while retaining data/configuration. |
| `update` | Update a managed service installation and restart it. |

`service install` accepts `--user USER`, `--data-home PATH`, `--config-home PATH`, and `--cache-home PATH`. Paths are absolute parent directories; TweetNook adds its own directory. The installer normally chooses these for you.

`serve`, `web start`, and `web restart` accept `--dev-tools` for the separate [developer replay workflow](development/web-and-jobs.md#developer-replay).

## Maintenance

| Command | Purpose |
| --- | --- |
| `stats [--detailed]` | Collections, sync history, outstanding work and storage totals. |
| `db check [--full]` | Check archive integrity, optionally more thoroughly. |
| `optimize` | Reclaim unused archive space and refresh search preparation. |
| `prune` | Preview context/media outside the current scope. `--apply` deletes it; back up first. |
| `rehydrate` | Rebuild extracted archive information from saved source data. |
| `migrate` | Convert an older archive with the optional migration add-on. |
| `auth check` | Test your configured X connection. |
| `auth refresh-ids` | Refresh X connection information used by requests. |

Prune also accepts `--max-linked-depth N` and `--reply-linked-depth 0|1`. Read [maintenance](maintenance.md) before applying it. Technical details of rebuilds and legacy migration are in the [storage guide](development/storage.md).

## Exit codes and completion

Most command failures use exit 1 for a local configuration/archive issue, 2 for a remote or job failure, and 130 for an interrupted enrichment run. Check the particular command's message; a nonzero exit doesn't imply that no work was saved.

`--version` prints version information. `--install-completion` installs completion for your current shell, and `--show-completion` prints completion code for you to install or customize. `--help` is available throughout the command tree.
