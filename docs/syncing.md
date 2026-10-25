# Syncing

Choose your collections and schedule in Settings > Schedule. Jobs lets you start a sync or run an individual follow-up task.

## Collections

A sync brings in new posts from your chosen collections and continues older history where there's more to fetch.

In Settings > Schedule, select Bookmarks, Likes, Your tweets, or a combination. Bookmarks and likes are the default. Your numeric user ID is needed for likes and your own tweets; [Setup](getting-started.md#connect-x) shows where to find it.

Choose Sync in Jobs to run now. The same choice is available from a terminal:

```bash
tweetnook sync
```

To run just one collection for this invocation:

```bash
tweetnook sync bookmarks
tweetnook sync likes
tweetnook sync tweets
```

## Scheduling

Turn on Sync automatically in Settings > Schedule. Choose an hourly interval, a daily time, a weekday, or a day of the month, then save. Monthly schedules use the month's last day when your chosen day doesn't exist.

For daily, weekly, and monthly syncs, you can add a random delay after the chosen time. For example, a 03:00 start with a zero-to-two-hour delay runs sometime between 03:00 and 05:00. The next run shown in Jobs reflects the scheduled time. Hourly schedules count from when you save, then from each scheduled launch attempt. Manual syncs don't reset the interval.

Schedules run while TweetNook is running and the host is awake. Closing your browser is fine. Closing a foreground server or turning the machine off stops scheduled work. Use a background service if you want syncs to keep running unattended.

## First sync

A large collection can take several runs to catch up. X can limit how much history your account can retrieve, and some saved posts may already be unavailable. An [official archive import](importing.md) helps bring in older history.

If you stop a sync, posts already saved remain in the archive. Run it again to continue. Limits and waiting periods can pause progress; check Jobs and the run log before starting a new run. TweetNook runs archive-changing jobs one at a time.

## Sync options

For a shorter run, use a page limit. It counts timeline pages for each collection; follow-up work can continue afterward.

```bash
tweetnook sync --limit 10
```

Recent-only mode fetches new posts and clears the saved place in older history. Backfill revisits known posts to look for gaps; starting over resets sync progress while keeping archived posts. Article backfill revisits timeline history for Article information. The [sync command options](cli-reference.md#sync-options) cover the flags, combinations, and ways to skip individual follow-up tasks.

Optional automated tagging runs when it's installed and enabled. To leave it out, disable it in Settings > Automated tagging; there is no `--skip-tagging` flag.

## Sync status

A completed collection sync can still have a media, Article, conversation, link-preview, or tagging issue listed in Activity logs. Those tasks can be continued separately.

Sync failed means a connection, collection, archive-write, storage, or worker failure prevented completion. Sync stopped records a requested stop, while Sync interrupted means the run has no confirmed final result. Check the log before deciding what to rerun.

A saved older-history position means there is more work to resume. Finishing available history doesn't prove X returned every item you've ever saved. After a server restart, a saved overdue schedule can try once and then resume its normal cadence.

## Post details

Sometimes a save has the main post but only part of the conversation, an Article preview, or media waiting to download. TweetNook normally works through these during a sync. You can run the individual tasks from Jobs when you need them sooner.

Jobs and Analytics show work that's still waiting.

These jobs contact X, media hosts, or the external links in the archive. Use trusted archive content.

### Conversations and quotes

Expand conversations collects the saved post's replies, its author's thread, and quoted or linked posts within your chosen follow depth. The default allows up to 50 hops through quoted and linked posts, starting from saved posts and their authors' own threads. Each hop can lead to several posts. Set the depth to zero to turn off linked discovery.

Links in other people's replies are off by default. You can allow one hop and set how many reply links a sync may follow in Settings > Configuration.

```bash
tweetnook threads expand --limit 50
tweetnook threads expand https://x.com/example/status/123456789 --refresh
tweetnook threads expand --max-linked-depth 10 --reply-linked-depth 1
```

Explicit targets can be IDs or X post URLs. `--refresh` requires explicit targets and revisits them even if they've been expanded before. The thread limit is shared across its expansion stages. X may only make part of a conversation available to your account.

The `--max-linked-depth` and `--reply-linked-depth` options also work on `tweetnook sync` and `tweetnook sync all`. See [Commands](cli-reference.md#sync-options) for the supported command forms.

### Articles

Refresh articles looks for full Article text and media where only a preview has been saved. Use the normal job for incomplete Articles or choose to refresh every Article.

```bash
tweetnook articles refresh --limit 20
tweetnook articles refresh --all
tweetnook articles refresh 123456789
```

For Articles, `--all` and explicit targets can't be combined. `--limit` bounds automatic Article selection; an explicit target list is processed as supplied. If the Article wasn't identified in an older capture at all, `sync --article-backfill` can revisit the timeline first.

### Media

Download media saves files on your TweetNook machine so you can read and watch them later without relying on the original media URL. The job can be limited to photos, and it can retry previous failed downloads.

```bash
tweetnook media download
tweetnook media download --photos-only --limit 100
tweetnook media download --retry-failed
```

You'll need disk space for the files, especially videos. Downloaded media is part of your archive backup.

Limits and progress count media items. A video or GIF can need both its main file and a poster image while counting as one item. Normal syncs and downloads select pending items; use `--retry-failed` or Retry failed downloads in Jobs to include failures after fixing their cause. Files no longer served by their host can remain unavailable after a retry.

### Link previews

Link previews adds titles, descriptions, and saved preview images for links shared in posts.

```bash
tweetnook unfurl --limit 100
tweetnook unfurl --retry-failed
```

This contacts the linked websites. Opening the link later still takes you to the website; a saved preview isn't a full copy of the page.

Link-preview jobs can save a file type without a page title when the destination isn't an ordinary web page. Normal syncs and link-preview jobs select pending links. Use `--retry-failed` to include previously failed lookups after fixing their cause.

### Imported posts

Use `tweetnook import enrich` to continue completing posts from an official archive. It also syncs your chosen collections afterward. [Importing](importing.md) explains how to limit the completion portion or import entirely offline.

## Availability checks

An availability check records whether your saved posts are still accessible. Unavailable posts may show a red warning symbol or an unavailable placeholder. You can search for these posts with `is:missing`. If an unavailable post returns, a later check can fill it in again when its recorded reason allows retries.

### Manual checks

Open Jobs and choose Availability check, or run:

```bash
tweetnook check
```

The check is off by default. In Settings > Configuration > Check, turn on Check automatically and choose Daily, Weekly (the default), or Monthly, then set the time and day. Saved changes take effect immediately while TweetNook is running.

The check looks at saved posts and their quotes, revisits unavailable posts, and can optionally work through conversation context over several runs. It also records changes to Community Notes that it observes.

### Results

| Result | What it means |
| --- | --- |
| Still on X | The post was accessible during this check. |
| Restored | A previously unavailable post was found again. |
| Unavailable | TweetNook recorded that the post couldn't be accessed. Saved content stays in your archive. |
| To recheck | The result needs another look before treating the post as unavailable. |
| Protected | Access depends on the account and your login. |
| Failed | The check couldn't get a usable answer this time. |

### Privacy

TweetNook uses FxTwitter for public post lookups. Those requests include the post IDs being checked and use the configured FxTwitter service. Your X cookies are used separately when a post needs your account's access. You can change the FxTwitter address to a service you host yourself.

Disabling FxTwitter also disables the availability-check job. Normal syncs can still revisit unavailable posts with your X connection. When checks and FxTwitter are both enabled, those rechecks belong to the check job instead.

### Run limits

```bash
tweetnook check --skip-context
tweetnook check --skip-saves --skip-unavailable
tweetnook check --detail-budget 0
```

`--skip-saves`, `--skip-context`, and `--skip-unavailable` omit those public FxTwitter passes. Authenticated rechecks run separately and can revisit posts already due before this check. `--detail-budget` limits that work to 200 posts by default; use `--detail-budget 0` to skip it, even when all public passes are disabled.
