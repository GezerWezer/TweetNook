# Client and Sync

[Development guide](README.md) · [Architecture](architecture.md) ·
[Storage](storage.md) · [Enrichment](enrichment.md)

Live sync uses the request interface of the Twitter/X web client. Authentication and
query-ID discovery prepare the client; sync then saves each timeline page with
its progress checkpoint. Twitter/X can change this undocumented interface at any time.

## Authentication resolution

`auth/cookies.py` resolves `auth_token`, `ct0`, and optional `user_id` into a
`ResolvedAuthBundle` from environment first, then typed config. The two cookies
are mandatory for any live endpoint. Likes and authored-tweet timelines require
a nonempty `user_id`; Twitter/X account IDs are normally numeric, but the code does not
enforce numeric syntax. Bookmarks do not require it.

There is no browser adapter, cookie database reader, Playwright session, or
interactive sign-in. `ResolvedAuthBundle` keeps value and source information
for status reporting.

Archive owner validation happens before live writes when a resolved user ID is
available. A bookmark-only run without one cannot compare or seed owner
metadata. The account invariant is shared with archive import.

## Query-ID discovery

GraphQL URLs are shaped as:

```text
https://x.com/i/api/graphql/<query-id>/<operation-name>
```

Target operations include Bookmarks, Likes, UserTweets, TweetDetail,
BookmarkFolderTimeline, and UserArticlesTweets.

`query_ids/scraper.py`:

1. fetches the Twitter/X discovery page;
2. extracts responsive-Web bundle URLs;
3. fetches main/chunk JavaScript;
4. applies operation/query-ID regexes;
5. merges discovered IDs with static compatibility fallbacks;
6. atomically writes `query-ids.json`.

`QueryIdStore` considers the cache fresh for 24 hours. A stale cache can still
provide values through `get()`, but sync preflight explicitly forces refresh
when missing/stale. Network exceptions in that forced path are not caught before
the fallback lookup, so “discovery then guaranteed fallback” is not currently a
safe invariant.

Never document the checked-in fallback hashes as stable public API.

## HTTP client

`client/base.py` uses HTTPX and sends:

- `auth_token`/`ct0` scoped to `.x.com`;
- `x-csrf-token`;
- the public Web bearer token;
- origin/referer, user-agent, active-user, language, and client headers.

Timeline methods encode operation variables, `features`, and field toggles into
query parameters. `client/features.py` centralizes current static flags by
operation; it is expected to drift with Twitter/X deployments.

### Status classification

| HTTP result | Domain interpretation |
|---|---|
| `401`, `403` | `AuthExpiredError` |
| `400` | `FeatureFlagDriftError` |
| `404` | stale query ID; refresh callback attempted once |
| `429` | rate-limit path |
| other failure | `APIResponseError` |

Transport exceptions are not universally wrapped.

### Rate limiting

Header-aware 429s calculate a delay from `Retry-After` or rate-reset metadata.
Headerless 429s use exponential backoff, then a configured cooldown after a
threshold. Repeated header-aware 429s reset retry state and can extend an
operation indefinitely.

Adaptive pacing reads `x-rate-limit-*` headers and adds inter-request delay as
remaining quota declines. Timeline and detail jobs can supply separate retry,
backoff, and base-delay settings.

## Timeline/detail parsing

`client/timelines.py` traverses nested instruction/entry structures, extracts
normal `tweet-*` entries, finds bottom cursors, unwraps supported visibility
wrappers, drops TweetTombstone/TweetUnavailable entries from the normalized
timeline list, and deduplicates tweets. The enclosing sync raw capture still
preserves the original response payload for later analysis/repair.

Timeline page size is 20. Current request builders cover bookmarks, likes,
authored tweets, and TweetDetail. Article refresh also uses TweetDetail.

Detail workflows must locate the requested focal tweet rather than accepting any
tweet in the response. Focal absence is a distinct signal from an explicit
unavailable result. Higher layers use consecutive-absence circuit breakers.

## Preflight

`run_preflight()` executes before the writer lock/page persistence:

1. resolve and validate credentials required by all selected collections;
2. enforce archive owner;
3. resolve/refresh required operation IDs;
4. instantiate/probe every requested timeline with `count=1`;
5. return reusable auth/query/client state.

For `sync all`, both collection probes are all-or-nothing. This prevents a
known-bad second endpoint from allowing the first collection to begin writes.

`auth check` uses the same readiness ideas but also creates config/path state and
can update the query-ID cache; it is not strictly filesystem read-only.

## Sync-state mapping

User collection commands map to storage membership labels:

| Command collection | Stored membership |
|---|---|
| `bookmarks` | `bookmark` |
| `likes` | `like` |
| `tweets` | `tweet` |

Each collection/folder has `SyncState`: latest head ID, backfill cursor,
incomplete flag, and timestamp.

## Per-page algorithm

For each page, `sync.py`:

1. fetches the timeline and cursors;
2. stores the raw response capture;
3. extracts membership rows and canonical/secondary graph;
4. identifies duplicates and head/backfill stop conditions;
5. buffers new sync state;
6. calls `persist_page()` for one transaction;
7. reports progress and sleeps/paces before the next request.

The transaction uses complete-row merge builders, so injected failure leaves no
partial page/cursor. A later page or collection can still fail after earlier
transactions commit.

## Mode semantics

- **default**: capture new head, stop at saved duplicates, and resume saved
  incomplete older state when appropriate;
- **backfill**: continue older history past duplicates without resetting state;
- **full**: reset selected state, begin from head, preserve archived rows;
- **article backfill**: rewalk pages for article extraction without state reset;
- **head only**: clear the saved older cursor and avoid resumption.

`head_only` is mutually exclusive with the three continuation/rewalk modes.
`limit` counts pages, per collection for all-sync, and is shared between head
and resumed-backfill work inside one collection.

## All-collection failure boundary

`sync_all()` preflights both endpoints, then runs bookmarks followed by likes.
The collections are not wrapped in a cross-collection SQLite transaction. A
likes failure represented by the domain `TweetNookError` boundary preserves
completed bookmark pages and reports exit `2`; an unwrapped exception can still
escape that normalized path.

## Follow-up orchestration

The ordered plan is:

1. threads;
2. resurrection;
3. article preview refresh;
4. media;
5. URL unfurl;
6. automated tagging when enabled.

Each follow-up is wrapped as a recoverable pipeline step. Exceptions are logged
as issues rather than changing a successful core collection outcome. Threads
have no default target cap; resurrection defaults to 200 attempts; articles
target preview-only rows; media/unfurl select pending rather than failed work.

`_embed_new_tweets()` deliberately does nothing. Do not reintroduce embeddings
through documentation or assume the stale “run tweetnook embed” error hint is
valid.

## Locks and activity

PipelineReporter owns `command.lock` for the command lifecycle and records
durable metadata/events/output/snapshots. `sync_collection`/`sync_all` acquire
the archive lock around writer work. Interrupted transactions roll back; earlier
page commits and cursor checkpoints remain.

Service-mode output is compact; terminal mode uses Rich live progress. Web
workers supply origin/run ID so their console output and activity history refer
to the same logical run.

## Regression targets

Changes to this area should normally cover:

- `tests/test_auth.py`
- `tests/test_query_ids.py`
- `tests/test_client.py`
- `tests/test_sync.py`
- `tests/test_pipeline.py`
- `tests/test_jobs.py`
- `tests/test_activity_history.py`
- CLI help/exit tests in `tests/test_cli.py`

Add raw response fixtures only after removing credentials and private payloads.
