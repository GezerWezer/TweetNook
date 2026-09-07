# Automated tagging

[User guide](README.md) · [Tags](tags.md) · [Configuration](configuration.md#tagging)

Automated tagging uses Gemini to suggest tags for saved tweets and describe their
media. It is optional and off by default. You can still archive, search, and add
manual tags without it.

## Why does it use Google models?

TweetNook uses Gemini for two main reasons:

1. Google offers a free API tier, which makes it possible to try automated
   tagging without paying. Batching (explained below) can stretch those limits
   further.
2. In project testing, Gemini Flash models consistently produced useful tags for
   tweets whose meaning depended on ambiguous or limited context. Similar models failed with this.

## If I choose Paid mode, how much will it cost?

-- add data

## Set it up

If not installed initially, install the optional dependencies once in the Python environment used by the
TweetNook server:

```bash
python -m pip install "tweetnook[automated-tagging]"
```

1. Restart TweetNook after installing the dependencies. For a systemd
   installation use `tweetnook service restart`; for a foreground
   `tweetnook serve` process, stop it and start it again.
2. Open **Settings → Automated Tagging** and turn on the enable switch to edit
   its controls.
3. Enter your Gemini API key, choose **Free** or **Paid**, and select a model.
   Use **Refresh models** if needed. For Paid mode, set a spend limit.
4. Choose **Save** when you want to enable these settings for future runs.

If the panel is missing, check that the optional dependencies are installed in
the same Python environment used by the running TweetNook server.
>[!WARNING]
> A model appearing in the list does not guarantee it supports every media or
processing option. Check Google's [model documentation](https://ai.google.dev/gemini-api/docs/models)
and test your chosen settings.

You can also configure tagging in [`config.toml`](configuration.md#tagging)


## Choose Free or Paid mode

| Setting | Free | Paid |
|---|---|---|
| Tweets per normal request | Up to 20 | One |
| Local usage controls | Requests per minute and per day | Estimated daily spend limit |
| Google Search | Off | Optional |
| Processing tier | Default Free request path | Standard or Flex |

These are tweetnook's operating modes and local limits. Your Google account,
chosen model, and provider terms determine actual availability and charges.
Check [Google's pricing information](https://ai.google.dev/gemini-api/docs/pricing)
before choosing a mode.

### Free limits

To stretch the free limits, batching is enabled by default. Batching includes
multiple tweets in the same request. In project testing, batch sizes of 20 still
generated useful tags and descriptions. The tradeoff is that context from one
tweet can influence another tweet in the same batch. If this is an issue, reduce
the batch size or disable batching entirely.

Daily usage is counted per model using the Pacific time zone. Retries and split
batches also consume the local allowance. Failed requests can remain counted,
so the local counter may be more conservative than Google's.

### Paid spend limits

Set an amount in the web panel before enabling Paid mode. Its Day, Week, Month,
and Year choices convert the entered amount into a **daily** allowance. They
do not create separate rolling weekly or monthly budgets.

The limit is based on local cost estimates. It may be exceeded by a request
already underway, and it does not cover other applications using your Google
account. Unknown prices or usage can pause a run so it does not continue with
an unreliable estimate.

Standard and Flex select different provider processing tiers. Google may restrict
Search access for Flex on some models. Check
[Google's pricing information](https://ai.google.dev/gemini-api/docs/pricing)
for availability and tradeoffs on your chosen model. 

Unlimited spend is available in configuration. Use a finite limit unless you
intend to remove that guard.

## Preview one tweet

> [!IMPORTANT]
> A test run makes a real request and can use quota or incur charges, even though it does not save tags.

In the web panel, enter an archived tweet's numeric ID under **Test run** and
check the preview before running the test. The test uses the current form values
without saving them.

From the terminal:

```bash
tweetnook tag --test 1234567890123456789
```

The terminal test uses your configured settings. Without an ID, it selects one
eligible tweet. Neither test saves generated tags or descriptions.

Wait for other archive or tagging jobs to finish before running a test.

## Guide the suggestions

**Tagging Context** lets you provide preferred tag hints. Add these hints if you
are not getting good tag results. For example, if you commonly save tweets about
Overwatch, providing that as a hint may improve the results.

**Additional User Instructions** lets you add rules for your collection.
For example:

- Prefer original Japanese titles over localized names.
- Tag photography posts by genre, such as street photography, astrophotography, or wildlife photography.
- Tag car posts with the exact manufacturer and model when the vehicle is identifiable

The model can also receive commonly used tags from your archive to encourage
consistent names. These hints guide suggestions but do not guarantee a
particular result or override the tweet's content.

## What gets tagged

Normal runs start with newer eligible tweets and skip completed, nonempty tags.
Failed or empty results can be tried again. A saved tweet or its directly quoted
original needs usable archived details before it can be tagged.

Media must already be downloaded locally; tagging does not download it from Twitter/X.
Run [`media download`](enrichment.md#download-media) first if needed. The default
maximum is 100 MiB per media item.

The model is asked for two to five specific tags. Media can also receive a
visual description and transcription of prominent text. Text-only tweets receive
tags without a generated description. Review and edit results in the normal
[tag editor](tags.md).

## Google Search

In Paid mode, optional Google Search can give the model outside context. One
tweet can trigger several searches. tweetnook tracks locally observed Search
usage against a free monthly safety allowance and disables Search for the rest
of a run if it cannot determine usage reliably.

The allowance only covers this archive's recorded requests. Search charges are
**not included** in the token-cost estimates shown by tweetnook. See
[Google's Search grounding documentation](https://ai.google.dev/gemini-api/docs/google-search)
for provider billing details.

> [!WARNING]
> If you use the same billing account for other projects, be careful with Google
> Search: TweetNook cannot see whether those projects have already used part of
> your free monthly Search allowance.

## View usage and spend

Choose **View spend history** in the Paid spend-limit panel to see estimated
costs, request counts, models, media types, and observed Search usage. Available
ranges include 7 days, 30 days, 3 months, 1 year, and Lifetime. Chart dates use UTC.

The figures come from this archive's local usage history. They exclude other
applications, Search/tool fees, and account charges. Unknown usage or missing
pricing appears as a gap. Use Google's billing records for actual charges.

Pricing metadata is fetched from OpenRouter; tagging requests go to Google.
Keep the local usage history with your [backup](maintenance.md) so
usage controls and spend history remain available after a restore.

## Run tagging

Once enabled, tagging runs after the usual sync follow-up jobs:

```bash
tweetnook sync
```

To tag existing eligible tweets without syncing:

```bash
tweetnook tag
```

For a bounded run:

```bash
tweetnook tag --limit 5
```

`--limit` bounds the main request loop, not the number of tweets or a strict cap
on all provider calls. Free batches can contain several tweets, and retries or
split batches can add calls. Paid work runs one tweet at a time.

To target a particular archived tweet:

```bash
tweetnook tag 1234567890123456789
```

A Twitter/X status URL also works. Explicit targeting can replace existing tags, so
use a preview first if you want to inspect the result.
## Privacy

Depending on the tweet, Google can receive its text, author information, quoted
context, media, existing tag suggestions, and your additional instructions.
Some media is uploaded as a file; tweetnook attempts to delete those uploads
after use, but cleanup can fail.

Data-use and retention terms depend on the provider, service tier, region, and
features. Review the [Gemini API terms](https://ai.google.dev/gemini-api/terms)
and [Files documentation](https://ai.google.dev/gemini-api/docs/files) before
sending private content.

Keep your API key private. Turning tagging off stops future automated tagging;
it does not erase requests already sent or tags already saved.

For every setting, see [Configuration](configuration.md#tagging). For request
handling and accounting internals, see the developer
[Automated tagging guide](development/automated-tagging.md).
