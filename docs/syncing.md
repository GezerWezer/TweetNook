# Syncing

[User guide](README.md) · [Connect Twitter/X](getting-started.md) · [Troubleshooting](troubleshooting.md)

Syncing saves tweets from your Twitter/X account to your local archive. Run the same
command regularly to pick up new items. Tweets you have already saved are kept.
## Syncing steps

A normal sync has two parts: first tweetnook saves tweets from Twitter/X, then it runs
follow-up jobs to fill in additional information. Each stage saves its work as
it goes, so a later failure does not undo tweets or enrichment that have already
been written to the archive.

1. **Check your Twitter/X connection.**  
   tweetnook verifies the saved Twitter/X credentials before making timeline requests.
   If authentication is no longer valid, the sync stops before collecting new
   data.

2. **Fetch bookmarks and likes.**  
   A normal `tweetnook sync` checks your bookmarks and likes for new tweets.
   Collection-specific commands can limit the run to one collection, and your
   own tweets are fetched separately with `tweetnook sync tweets`.

3. **Save new tweets and sync progress.**  
   Tweets are written to the local archive as pages are received. tweetnook
   also saves its position through the timeline so interrupted or partial runs
   can continue later without starting over.

4. **Expand threads and related Twitter/X tweets.**  
   tweetnook fills in conversation context where possible, including thread
   tweets, quoted tweets, and linked Twitter/X tweets that are needed to understand items
   already in the archive.

5. **Recheck unavailable tweets.**  
   Tweets that could not previously be retrieved may be checked again in case
   they have become available.

6. **Refresh Twitter/X Articles.**  
   For tweets that reference Twitter/X Articles, tweetnook attempts to retrieve fuller
   article content and metadata.

7. **Download media.**  
   Pending photos, videos, and GIFs are downloaded to local storage when they
   are still available from Twitter/X.

8. **Fetch information for external links.**  
   tweetnook visits saved external links to collect useful metadata such as
   page titles and descriptions.

9. **Run automated tagging, if enabled.**  
   If the optional automated-tagging feature is installed and enabled,
   eligible tweets are processed after the other follow-up jobs.

## Choose what to save

Connect Twitter/X first through Web Setup; use [Connect Twitter/X](getting-started.md) for the detailed cookie instructions.

| Command | Saves |
|---|---|
| `tweetnook sync` | Bookmarks and likes |
| `tweetnook sync bookmarks` | Bookmarks only |
| `tweetnook sync likes` | Likes only |
| `tweetnook sync tweets` | Your own tweets |

`sync all` is another name for the default bookmark-and-like sync. Your own
Tweets always use the separate `sync tweets` command.

All live syncs need `auth_token` and `ct0`. Likes and your own tweets also need
your numeric account ID. Configure all three so tweetnook can check that the
archive belongs to the account you are using.

## Run a regular sync

```bash
tweetnook sync
```

The command checks your connection, saves new tweets, then works on missing
context and downloads. It stops collecting recent tweets when it reaches ones
already saved. If an earlier run left older history unfinished, it can continue
that work too.

Progress is saved after each page. If likes fail after bookmarks have been
saved, the bookmarks remain in your archive. A failed media download or other
follow-up also leaves saved tweets intact; read the final summary for outstanding
work.

## Collect older history

Use `--backfill` when you want to continue through older tweets even after
reaching ones already in the archive:

```bash
tweetnook sync --backfill
```

Twitter/X may limit how much history it returns. Completing a run means tweetnook has
finished the history available through that request, rather than proving every
item ever saved on Twitter/X has been recovered.

## Limit a run

```bash
tweetnook sync --limit 5
```

This saves up to five pages **per collection**, not five tweets. Pages normally
contain up to 20 tweets. A default sync can therefore fetch five bookmark pages
and five like pages. Follow-up work has its own scope and can continue after
this page limit is reached.

## Control downloads and follow-up work

After saving tweets, a normal sync tries to:

1. Fill in threads, quoted tweets, and linked Twitter/X tweets.
2. Recheck some previously unavailable tweets.
3. Retrieve fuller article content.
4. Download pending photos, videos, and GIFs.
5. Fetch titles and descriptions for saved links.
6. Generate tags, if you have enabled automated tagging.

You can skip individual steps. For example, save tweets without downloading media
or visiting their external links:

```bash
tweetnook sync --skip-media --skip-unfurl
```

| Option | Skips |
|---|---|
| `--skip-threads` | Threads, quoted tweets, and linked Twitter/X tweets |
| `--skip-resurrection` | Rechecks of unavailable tweets |
| `--skip-articles` | Article refreshes |
| `--skip-media` | Media downloads |
| `--skip-unfurl` | Link titles and descriptions |

To skip automated tagging, turn it off in **Settings → Automated Tagging** or
set `enabled = false` under `[tagging]`. There is no `--skip-tagging` option.
When tagging is enabled, it runs after the usual sync follow-up jobs.

Failed downloads and link lookups need an explicit retry. See
[Enrichment](enrichment.md) for the commands to run those jobs separately.

## Pause and resume

Use **Stop Task** in the web app or interrupt a foreground command with
**Ctrl+C**. Wait for it to finish stopping, then rerun the command when ready.
Pages already saved remain available; unfinished work can resume from the saved
position.

Run one sync, import, or enrichment job at a time. If tweetnook reports another
command is running, check the activity drawer and any open terminals. Stopping or
restarting TweetNook asks its managed Web/scheduled job to stop gracefully;
wait for it to finish stopping before starting another.

## Understand waits and status

Twitter/X can limit requests. tweetnook reports waits and retries automatically; allow
these to finish or stop the task and try later. Repeatedly restarting a waiting
sync will not fix a rate limit.

Use `tweetnook stats` to check collection coverage and sync progress.
A saved older-history position means there is unfinished work to resume. A
completed run can still list follow-up issues, such as media that Twitter/X no longer serves.

For authentication errors, failed requests, or lock conflicts, use
[Troubleshooting](troubleshooting.md).

## Schedule regular syncs

Use **Settings → Schedule** in the [web app](web-app.md#scheduling). The
TweetNook server/service must remain running for its schedule to work; the
browser can be closed. Scheduled runs save bookmarks and
likes, including enabled follow-up jobs. They do not save your own tweets.

## Advanced sync modes

Most users can stay with regular sync and `--backfill`.

| Option | When to use it | Effect on saved work |
|---|---|---|
| `--full` | Start the timeline walk again from the newest tweets | Resets sync progress for the selected collections; keeps archived tweets |
| `--article-backfill` | Revisit saved timeline pages for article fields | Keeps existing progress and tweets |
| `--head-only` | Check recent tweets and abandon unfinished older-history work | Clears the saved older-history position; keeps archived tweets |

`--head-only` cannot be combined with the other three modes (`--full`,
`--backfill`, or `--article-backfill`). Article backfill revisits timelines;
[`articles refresh`](enrichment.md#refresh-articles) fetches fuller content for
articles already identified in the archive.

`--max-linked-depth N` controls how far follow-up work follows quoted and linked
Twitter/X tweets. It is available on `sync` and `sync all`, and on the standalone
`threads expand` command. The default depth is 1.

See the [CLI reference](cli-reference.md#sync) for the complete option list and
[Client and sync](development/client-and-sync.md) for checkpoint internals.
