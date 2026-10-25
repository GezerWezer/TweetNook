# Enrichment

Enrichment takes content already selected for the archive and fills out its graph or local assets. Standalone jobs and sync follow-ups share configuration, the archive lock, and pipeline reporting.

## Thread expansion

`threads.py` selects due or explicit targets, fetches TweetDetail responses, and persists extracted graph records. Explicit IDs and status URLs are normalized before deduplication. `--refresh` permits revisiting explicitly named targets that have already been expanded.

Saved posts and the author's own thread can lead to quote or linked-status discovery up to `max_linked_depth` (default 50). Other authors' replies use `reply_linked_depth`, limited to zero or one, and `reply_linked_limit`, default 1 per sync. These are graph traversal bounds, not a promise that the remote response contains every reply.

Repeated responses that omit the requested focal post stop expansion. Related posts in a response are insufficient evidence that the target was captured or deleted.

## Articles

`articles.py` extracts and refreshes archived Article bodies using TweetDetail. The normal refresh selects incomplete/preview-only Articles; `--all` also revisits complete ones. `--limit` limits automatic selection and is ignored when explicit targets are supplied. Preserve structured body content as well as searchable text, covers, inline media and links.

`web/articles.py` formats saved bodies for the browser, and `static/js/articles.js` renders them. Presentation reads the saved Article state without starting remote fetches.

## Media

`media.py` selects pending files, resolves image/video variants, downloads into managed paths, and records hashes, sizes, content types, local paths and failures. Photos-only mode excludes videos and animated GIFs. Retry-failed mode includes previous failures rather than just untouched work.

`media_metadata.py` reconciles dimensions/aspect ratios used by the browser. A downloaded file, thumbnail, poster, and remote URL are different candidates; preserve their identities and provenance. Support quoted media and Article assets as well as the focal tweet.

Storage exhaustion is a job-level failure. Don't report a failed write as a successful download or leave a row claiming a partial file is complete. The [testing guide](testing.md) covers fixtures and file-boundary checks.

## URL unfurling

`unfurl.py` resolves expanded/canonical URLs and saves metadata and preview images. URL rows can be shared by multiple tweet references. Keep fetched metadata separate from original tweet text/entities, and don't replace a real destination with the shortened display label.

A link-card image is presentation metadata, not an attached-photo fact for `has:image`. Browser cards, searches and detail views should use that distinction consistently.

## Unavailable posts

Authenticated restoration is in `resurrection.py`; FxTwitter/public checks are in `check.py`. Retry policy is reason-aware, bounded, and independent from the last usable saved body. See [availability checks](availability-checks.md).

## Extraction and rebuilds

The extractor unwraps visibility wrappers, prefers valid Note Tweet text, follows embedded quoted/reposted objects for graph extraction, and retains Community Note pivots. Linked traversal across requests is separate from that bounded embedded extraction. Tombstones remain classified results rather than fake normal tweet bodies.

Canonical HTTP(S) URLs lowercase scheme/host, remove fragments/default ports and known tracking parameters, and preserve other path/query data. The canonical hash is a storage identity, so normalization changes need compatibility analysis. Reply, quote, repost, thread-parent/child and linked-status edges have different meanings.

URL unfurling parses at most the first 500,000 HTML characters after loading the body. This limits parser work, not response bytes or memory use. Updates are batched, and previous failures need explicit retry selection. See [trust and security](architecture.md#trust-and-security) for remote-host and response-size limits.

Rehydration reparses membership rows in batches of 500 plus relevant saved detail captures. Malformed raw responses can fail the pass, and stale secondary rows aren't comprehensively removed. It isn't corruption repair or a live refresh.

Restoration saves recovered context and makes stale expansion state eligible for refresh. Evidence that an account has returned can prioritize its other unavailable posts within the restoration budget.
