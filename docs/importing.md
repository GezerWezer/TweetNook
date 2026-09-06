# Importing a Twitter/X archive

[User guide](README.md) · [Connect Twitter/X](getting-started.md) · [Enrichment](enrichment.md)

Import an archive downloaded from Twitter/X to add your tweets, likes, and matching media
to tweetnook. The local import works without a live Twitter/X connection.

If you need to request an archive first, follow
[Twitter/X download instructions](https://help.x.com/en/managing-your-account/how-to-download-your-x-archive).

## Import from Web Setup

Open Setup on first visit, or **Settings → Setup → Run setup again**. Upload your
ZIP, select **Start local import**, and continue to Connect Twitter/X immediately. The
background import is always offline at first; it can take hours or days and does
not require the page to remain open. Enrichment starts automatically once both local import and a successful
Twitter/X connection test finish. You may finish Setup and browse after local import
while enrichment continues. 

## Import your ZIP file from the CLI

If you prefer to import from the terminal instead of Web Setup, run:

```bash
tweetnook import x-archive /path/to/twitter-x-archive.zip
```

Replace the path with your downloaded ZIP. You can also use an extracted folder
containing `data/manifest.js` or a root-level `manifest.js`:

```bash
tweetnook import x-archive /path/to/extracted-archive
```

Use the archive for the same Twitter/X account as your existing tweetnook database.
Each database is for one account. Import only archives you trust.

The command saves supported data and copies matching media. If Twitter/X credentials
are configured, it also checks your tweets and likes against Twitter/X and looks up
missing details. If credentials are unavailable, it skips that live work with
a warning; the local import can still complete.

Afterward, check the result:

```bash
tweetnook stats
tweetnook view tweets --limit 10
```

You can now [start the web app](web-app.md#start-and-stop-the-app).

## What gets imported

| Data | What to expect |
|---|---|
| Your tweets | Added to **Your Tweets** |
| Deleted tweets included in the export | Retained with their archive-deleted status |
| Likes | Added to **Likes**; some need a later lookup for full details |
| Media attached to your tweets | Copied when it can be matched to a tweet |
| Account information | Used to identify the archive's owner |

The importer does not add bookmarks, Direct Messages, followers, following,
or lists. Use `tweetnook sync bookmarks` to save bookmarks from Twitter/X.
Unmatched media files remain in the original archive.

## Fill in missing information

Web Setup starts enrichment automatically after both the local import and a
successful Twitter/X connection test are complete. Run the command below manually if
you imported through the CLI, skipped Twitter/X during Setup, or need to retry pending
enrichment later:

```bash
tweetnook import enrich
```

To limit this work:

```bash
tweetnook import enrich --limit 100
```

This checks up to 100 eligible tweets. Twitter/X may no longer provide some tweets, so
missing details do not necessarily indicate an import problem.

## Rerun an interrupted import

Run the same import command again. Completed work is retained, and repeated
imports merge with existing data. An identical completed archive is recognized
even if you switch between an equivalent ZIP and extracted folder.

If the local import finished and only detail lookups were interrupted, use
`import enrich` to continue them.

## Control live lookups

| Option | Effect |
|---|---|
| `--offline` | Imports local data/media and skips all live follow-up |
| `--no-enrich` | Skips individual detail lookups, but still allows live timeline checks |
| `--detail-lookups 100` | Limits individual detail lookups to 100 |
| `--sample-limit 10` | Imports a small sample from each supported dataset and skips all live follow-up |

For example:

```bash
tweetnook import x-archive /path/to/archive.zip --detail-lookups 100
```

With normal enrichment enabled, `--detail-lookups 0` means all eligible tweets.
With `--no-enrich`, zero means no detail lookups; a positive number enables that
many lookups despite `--no-enrich`.

> [!NOTE]
> `--no-enrich` is not an offline mode. Use `--offline` for a full local import
> without Twitter/X requests, even with valid credentials. It overrides enrichment and
> detail-lookup options. 

A sample still reads and hashes the full input, so it may take time on a large
archive. Rerun without `--sample-limit` to import the full archive, even if Setup
already labels the sample as imported.

## Rebuild imported data

Use `--regen` only when you intend to replace previously imported data:

```bash
tweetnook import x-archive /path/to/archive.zip --regen
```

> [!WARNING]
> Back up first. This removes data and managed media belonging to **all previous
> archive imports**, then rebuilds from the supplied archive. It is broader than
> replacing just this ZIP. Data captured directly from Twitter/X and unrelated files are
> retained.
> 
> A normal rerun does not need `--regen`. 
