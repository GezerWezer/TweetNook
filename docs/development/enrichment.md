# Enrichment

[Development guide](README.md) · [Client and Sync](client-and-sync.md) ·
[Storage](storage.md) · [Archive Import](archive-import.md)

Enrichment connects saved tweets to their threads, quoted tweets, media, links,
and articles. The extractor converts responses into shared records; individual
jobs fetch missing content and update availability. A missing tweet in a response
is treated separately from an explicit unavailable result.

## Extractor responsibilities

`extractor.py` is the normalization boundary for GraphQL-shaped data. It:

- unwraps tweet results and visibility wrappers;
- prefers Note Tweet text over legacy `full_text` when valid;
- recognizes tombstone/system/unavailable messages;
- emits canonical objects, relations, media, URLs/references, and articles;
- follows one level of embedded quoted/retweeted objects during extraction;
- preserves useful payloads such as Birdwatch/community-note pivots;
- merges graph duplicates without discarding richer values.

Terminal/tombstone results do not become fake normal tweet objects. Higher layers
can retain membership/relationship placeholders with classified availability.

## URL canonicalization

Canonical URLs accept HTTP(S), lowercase scheme/host, drop fragments/default
ports, and remove `utm_*` plus known tracking parameters while preserving other
path/query data. URL records are keyed by canonical hash; positional `url_ref`
rows attach them to tweets.

Changes to normalization affect identity/deduplication and require migration or
compatibility analysis, not only parser tests.

## Relationship graph

Recognized edges include:

- quote;
- retweet/repost;
- reply;
- thread parent/child;
- linked Twitter/X status URL.

Media/article/URL objects are separate records attached by tweet ID/key. Merge
rules coalesce fields and preserve the lowest stable media position.

## Availability classification

Unavailable text/payloads are classified into protected, suspended,
missing-account, deleted-by-author, withheld, not-found, archive-deleted, or
unknown-like states.

The key distinction is:

- **explicit unavailable result** — evidence that can update reason/confidence
  and retry schedule;
- **focal tweet absent from a response** — ambiguous transport/schema/visibility
  condition that remains transient and participates in a consecutive-absence
  circuit breaker.

Presentation should use canonical `tweet_object` state over stale embedded
wrapper content. The Web layer centralizes this in `availability.py`.

## Initial detail enrichment

Archive import queues pending/sparse canonical objects. `import enrich` freezes
eligible pending/due-transient rows at command start, calls TweetDetail, writes
checkpoints every 100 attempts, and separates row-level retry from systemic
abort.

Successful detail yields `done`; explicit unavailable yields terminal/retry
metadata; focal absence yields transient state. Auth/query/feature/rate-limit
systemic failure aborts so a broken contract does not stamp thousands of rows.

## Thread expansion

`threads.py` discovers work from:

- collection memberships;
- canonical objects;
- reply/quote relations;
- URL references to Twitter/X status IDs;
- linked-depth configuration.

Processing phases are thread targets, quotes, then linked statuses. One global
limit applies. Successful TweetDetail extraction persists raw captures and the
full discovered secondary graph.

Traversal is bounded by `max_linked_depth`. Depth 1 handles a wrapper/direct
quote; higher values follow additional linked/quoted layers. A per-run seen set
deduplicates targets.

Already expanded implicit work is skipped. `--refresh` is legal only with
explicit targets so a user cannot accidentally refetch the full graph.

Three consecutive missing focal results raise `RepeatedFocalAbsenceError`.
Explicit auth errors abort immediately; configured detail rate-limit retry/
cooldown applies.

## Article refresh

`articles.py` accepts numeric/status-URL targets. Default queue selection is
preview-only; `--all` selects every article record.

Each target uses TweetDetail and must match the focal tweet. Article body/media
are extracted when present; nonempty article content is not itself required for
success. Target parsing accepts digits or any string containing `/status/<id>`.
Pacing/retry behavior comes from detail config.
The timeline `--article-backfill` path is separate: it re-extracts article
fields while walking old collection pages.

## Media downloader

`media.py` converts a media row into one or more download tasks:

- photos normalize to `name=orig` and remove fragments;
- video/GIF can require a main asset and poster;
- output lives under `<data>/media/<tweet-id>/`;
- bytes stream to a temporary file;
- SHA-256, size, content type, and final extension are recorded;
- final installation is atomic.

Pending rows are default candidates; failed rows need `retry_failed=True`.
Completed rows are skipped. `photos_only` filters video/GIF.

The downloader follows stored external URLs and has no comprehensive host/
private-network allowlist or general response-size cap. Maintain trust-boundary
documentation/tests when broadening accepted inputs.

## URL unfurl

`unfurl.py` follows redirects with a descriptive user agent, prefers final/
expanded/canonical URL, and extracts:

- title;
- one description, using standard, OpenGraph, or Twitter/X metadata as fallbacks;
- site name;
- content type.

Only the first 500,000 HTML characters are parsed, but the full response body is
loaded before slicing; this is a parser-work cap, not a download or memory-size
limit. Non-HTML content can complete without page metadata. Updates merge in
batches of 100. There is no application retry loop inside one run; prior failure
inclusion is explicit.

This is the strongest SSRF boundary because arbitrary stored HTTP(S) URLs can be
requested. Any future public/multi-user deployment needs network egress and
private-address controls.

## Resurrection

`resurrection.py` selects due terminal-unavailable canonical objects with a
default global budget of 200. Approximate category weights are:

- 60% protected;
- 25% suspended/missing;
- 15% ambiguous.

Unused category capacity spills into other due categories. Permanent
archive-deleted/deleted-by-author rows are excluded. Retryable reasons use
intervals from days to months.

Reason-confidence rules prevent weaker evidence overwriting stronger prior
classification; unknown can upgrade. Successful detail becomes `resurrected`,
clears scheduler fields, and persists every thread tweet and relationship already
returned by that TweetDetail response. This also writes the current thread
expansion marker without making a redundant request.

If a tweet is recovered independently after an older thread-expansion attempt,
the recovery timestamp makes that marker stale. The next thread pass fetches the
current context once and then records a newer marker, including for archives made
by versions that previously left resurrected tweets permanently skipped.

Same-author probing can use up to five probes; after three successes, up to 15
same-author rows can be boosted within the global budget. Transport failures do
not increment explicit availability retry count; explicit unavailable responses
do. Three focal absences trip the same defensive breaker.

## Rehydration

`ArchiveStore.rehydrate_from_raw_json()` reparses every membership row in batches
of 500 and relevant TweetDetail/ThreadExpandDetail captures. It is local-only.

Known limitations:

- raw JSON parse/type errors are not handled per row;
- stale secondary rows are not comprehensively deleted;
- it is not a substitute for live detail enrichment.

## Pipeline integration

Normal sync follow-ups run threads → resurrection → article refresh → media →
unfurl → optional tagging. Each step reports progress/issues through
PipelineReporter and is recoverable relative to core page capture.

Standalone commands use `locked_archive_job` or explicit `ProcessLock` patterns.

## Regression targets

- `tests/test_extractor.py`
- `tests/test_extractor_additions.py`
- `tests/test_threads.py`
- `tests/test_articles.py`
- `tests/test_media.py`
- `tests/test_unfurl.py`
- `tests/test_resurrection.py`
- archive-import detail tests
- Web availability tests
