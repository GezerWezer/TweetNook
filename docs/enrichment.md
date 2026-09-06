# Enrichment

[User guide](README.md) · [Syncing](syncing.md) · [Importing](importing.md)

A saved tweet may be missing its thread, full article, media files, or link
preview. Enrichment fills in that information where it is still available.
Normal sync runs most of these jobs automatically; the commands below let you
run them separately or retry failures.

## Choose a task

| What is missing? | Command |
|---|---|
| Details on imported tweets | `tweetnook import enrich` |
| Threads, quotes, or linked Twitter/X tweets | `tweetnook threads expand` |
| Full content for known articles | `tweetnook articles refresh` |
| Downloaded photos, videos, or GIFs | `tweetnook media download` |
| Titles and descriptions for links | `tweetnook unfurl` |

Detail, thread, and article lookups need your [Twitter/X connection](getting-started.md).
Media downloads contact the saved media hosts. Link lookups visit the external
URLs in your tweets.

Run one job at a time. To inspect outstanding work, use:

```bash
tweetnook stats --detailed
```

## Fill in imported tweet details

```bash
tweetnook import enrich --limit 100
```

This looks up up to 100 eligible tweets, including missing details and failures
that are ready to retry. Omit `--limit` to process all tweets eligible when the
command starts. Newly discovered work can be handled by a later run.

If Twitter/X rejects the connection or repeatedly returns unusable responses, the job
stops. Previously saved progress remains. Fix the reported issue and rerun the
command.

## Expand threads and quoted tweets

```bash
tweetnook threads expand
```

The command uses your saved tweets to find missing threads, quoted tweets, and
linked Twitter/X tweets. To look up a particular tweet, give its ID or Twitter/X URL:

```bash
tweetnook threads expand 'https://x.com/example/status/1234567890123456789'
```

| Option | Use it to… |
|---|---|
| `--limit 50` | Process at most 50 targets across all expansion steps |
| `--refresh` | Look up an explicit target again, even if already expanded |
| `--max-linked-depth 2` | Follow quoted and linked tweets to a greater depth |

`--refresh` requires one or more explicit targets. The default linked depth is 1;
increasing it can fetch substantially more context and take longer.

## Refresh articles

```bash
tweetnook articles refresh
```

By default, this tries to replace saved article previews with fuller content.
To refresh a particular article, give the ID or Twitter/X URL of its tweet:

```bash
tweetnook articles refresh 1234567890123456789
```

To revisit known articles, including ones with saved content:

```bash
tweetnook articles refresh --all --limit 50
```

`--all` cannot be combined with explicit targets. The limit applies to automatic
selection, not an explicit target list. For tweets whose article information was
never captured, [`sync --article-backfill`](syncing.md#advanced-sync-modes) revisits
the timeline first.

## Download media

```bash
tweetnook media download
```

This downloads pending photos, videos, and GIFs into the archive's `media`
folder. Completed downloads are skipped. To download photos only, add
`--photos-only`; to bound the number of media records, add `--limit 100`.

Retry previous failures with:

```bash
tweetnook media download --retry-failed
```

A video or GIF may need both the main file and a preview image before it counts
as complete. If a file is no longer available from its host, another attempt may
still fail. Large media collections need extra disk space and bandwidth.

## Fetch link previews

The command name `unfurl` means following a saved link and fetching its title,
site name, and description:

```bash
tweetnook unfurl
tweetnook unfurl --retry-failed
```

Add `--limit 100` to bound the number of links processed. Pages that are offline
or block automated requests may fail. Files and other non-web-page responses
can have a saved URL and file type without a title or description.

This visits the URLs from the TweetNook server. Run it only for archive content you
trust.

## Understand missing and unavailable tweets

| Status | Meaning |
|---|---|
| Pending or incomplete | Details have not been retrieved yet |
| Transient failure | A lookup failed and may be retried later |
| Unavailable | Twitter/X returned an unavailable result; the reason is shown when known |
| Resurrected | A later lookup recovered a previously unavailable tweet |
| Not archived | A thread or quote refers to a tweet that has not been captured |

Normal sync periodically rechecks some unavailable tweets, including tweets from
protected or suspended accounts that might become accessible later. Tweets known
to have been deleted by their author, including archive-deleted tweets, are not
part of this ordinary retry schedule. Recovery is not guaranteed.

To skip rechecks during a sync, use `--skip-resurrection`. There is no standalone
resurrection command.

See [Troubleshooting](troubleshooting.md) for failed jobs.
