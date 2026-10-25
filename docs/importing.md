# Imports

An official archive is a useful starting point if you've used X for years. It can bring over older tweets, likes, and media that a live sync may not reach.

## Download your archive

In X, open Settings and privacy, then Your account, and request a download of your data archive. X prepares it for you and provides a ZIP when it's ready. The [X archive-download guide](https://help.x.com/en/managing-your-account/how-to-download-your-x-archive) has the account-side steps.

## Browser import

Open Settings > Setup and choose the archive import option. Upload the original ZIP and start the import. You can continue connecting your X account and choosing collections while it runs. It saves local data, fills in available post details, then downloads media. You can close the page while it works.

Once the import succeeds and Setup is complete with X connected, TweetNook starts the first sync of your chosen collections. Watch Jobs for progress; Settings > Activity logs keeps the run's details. If enrichment or media stops, saved posts remain browsable and automatic preparation waits for you to [resume the failed task](#resume-an-import).

After import, Settings > Setup can clear the uploaded ZIP. This removes the staged copy and keeps imported posts and media.

## Command-line import

```bash
tweetnook import x-archive /path/to/twitter-archive.zip
tweetnook import x-archive /path/to/extracted-archive
```

A normal CLI import copies local data, reconciles authored/liked collections represented in the export when credentials are available, and fills in missing post details. Run Download media separately to fetch files absent from the export, or use `--defer-followup` for the browser import's sequence.

To load only the files you already have:

```bash
tweetnook import x-archive /path/to/twitter-archive.zip --offline
```

This imports local data and media without network follow-up. You can browse it straight away and connect an account later.

## Network access and import modes

With FxTwitter enabled (the default), both browser and normal CLI imports send post IDs to that service to fill in public details. This can happen without X cookies, even when the log says authenticated collection reconciliation was skipped. Use `--offline` for a local-only import.

## Import options

| Option | When it's useful |
| --- | --- |
| `--offline` | You want to import entirely from local files. |
| `--no-enrich` | You want the import's live collection pass but want to postpone individual post completion. Use `--offline` to avoid network follow-up. |
| `--detail-lookups 100` | You want to limit the number of imported posts completed during this import. A positive value requests those lookups even with `--no-enrich`; `0` adds no separate limit. |
| `--defer-followup` | Import, fill in details and download media now, leaving live collection sync for Setup to start separately. |
| `--regen` | You deliberately want to replace data and media owned by all previous archive imports. Back up first. Data collected by live sync is kept. |
| `--sample-limit 100` | You want a trial import. This limits each supported category, rather than the combined total, and still reads the full input. Rerun without it for a full import. |
| `--debug` | You need a more detailed import log for a support report. |

## Resume an import

If local ingest is interrupted, rerun the same command. Equivalent ZIP and extracted-folder contents are recognized as the same import. Reimporting a completed archive avoids duplicating its posts; a newer export can add material.

For media still pending, choose Download media in Jobs. If items are marked failed, fix the reported problem and enable Retry failed downloads, or run:

```bash
tweetnook media download --retry-failed
```

This downloads saved public media URLs without an X connection. Normal syncs and downloads leave previously failed items alone unless retrying is enabled.

To continue post completion and fill collection gaps, connect X and choose Enrich archive or run:

```bash
tweetnook import enrich
```

Both Enrich archive and Sync now require X credentials. `import enrich --limit 100` completes at most 100 eligible imported posts before syncing your configured collections; the limit doesn't cap that sync. Without credentials, you can continue browsing and downloading media. A deferred import can also fill public details through FxTwitter, but posts needing account access must wait until you connect.

## After importing

Some posts may remain incomplete, protected or unavailable. TweetNook keeps what it has; an unavailable post doesn't mean the whole import failed. Later runs can add details when they're accessible.

Likes can be browsed in liked order as well as by post date. Older archive history may have uncertain ordering, so that view isn't an exact record of when each like happened.

See [completing saved posts](syncing.md#post-details) for conversations, Articles, and media, and [maintenance](maintenance.md) for backups and moving an archive.
