# Search and Web API

[Development guide](README.md) · [Storage](storage.md) ·
[Web and Jobs](web-and-jobs.md) · [User search guide](../search.md)

CLI and web tweet searches share the parser and executor in `search.py`. Web
routes paginate results, attach shared availability information, and assemble
thread and quote responses. This page describes query execution and the read API.

## Query model

`search.py` parses into clauses/groups rather than passing user text directly to
FTS/SQL. Supported syntax includes:

- implicit AND;
- `AND`, `OR`, and `NOT` (normally written uppercase);
- leading `-` negation;
- quoted phrases;
- FTS prefix terms;
- hashtag terms;
- structured key/value filters.

The parser recognizes Boolean operators only in uppercase. Its downstream FTS
normalization can nevertheless promote lowercase `and`, `or`, and `not`
between terms, where SQLite FTS precedence can differ from the parser's
canonical clause/OR grouping. Callers should use uppercase operators for
deterministic grouping. OR alternatives form a group; separate groups are all
required. Recognized structured clauses with invalid enum/date/numeric
values can raise `SearchQueryError`, but several incomplete filter-looking
tokens (for example `from:` or `min_faves:`) fall back to ordinary text.
Unbalanced quoted filter values can also pass through, and numeric time parsing
currently accepts non-finite `nan`/`inf` float spellings. Do not describe the
parser as rejecting all malformed filter-shaped input.

## Filter registry

Parser keys are:

```text
from to mentions
since until since_time until_time since_id max_id
has is filter
min_retweets min_faves min_replies
conversation_id quoted_tweet_id
url source card_name tag hashtag
```

Enums:

- `has`: article, media, image, video, links;
- `is`: quote, reply, resurrected, retweet, thread, verified;
- `filter`: articles, images, links, media, native_video,
  nativeretweets, quote, replies, self_threads, threads, verified, videos.

Dates use UTC-midnight bounds: `since` inclusive, `until` exclusive. The shared
date parser also accepts `YYYY-MM-DD_HH:MM:SS` as UTC despite the public error
copy advertising date-only input. `since_time`/`until_time` accept numeric Unix
seconds. `since_id` is strict greater-than; `max_id` is inclusive.

Tag matching covers direct tags and direct quote-target tags, with result
metadata indicating quoted provenance.

## Execution plan

The executor applies every Boolean, structured, and collection predicate in SQL,
deduplicates logical tweet IDs, orders the matches, and fetches `limit + 1` IDs.
Only the displayed page is hydrated. Text searches have no 1,000-result cap;
`has_more` comes from the extra ID, and exact text totals/pages are omitted.
Filter-only/feed totals remain exact and use bounded caches with write invalidation.

Text clauses remain independent FTS relations so different membership documents
can satisfy different clauses without changing relevance semantics. A single
positive-text driver reuses its relation directly. The final grouped matches use
a co-routine when possible, and explicit non-relevance sorts omit BM25 scoring.
A positive `from:` AND group probes at most 201 IDs through the author index: up
to 200 candidate tweets use rowid-constrained FTS for non-relevance sorts and an
early intersection in a single FTS scan for relevance. Larger author sets fall
back to full FTS-led execution. This threshold selects a plan, never a result cap.
Other Boolean/OR predicates are still evaluated before pagination.

FTS values and cursor bounds are parameterized; structured SQL expressions come
from the canonical compiler. Tests cover injection-shaped filters, malformed
cursors, complete large result sets, and preserved relevance scores.

Positive text defaults to relevance; filter-only/no-positive-text defaults to
newest. Random results use a stable per-search seed. Explicit newest/oldest order
on stored timestamp, numeric sort index, and tweet ID.

For Likes, `liked_latest` and `liked_earliest` use the derived like sequence.
`like_order.py` merges cursor-linked GraphQL observations and complete multipart
archive datasets; live observations win conflicts and unknown-order rows stay
last. No timestamp is inferred from a sort index. Transactional input revisions
invalidate the immutable cache across connections, while media/tag/content-only
updates leave it reusable. A temporary table indexes both order directions for
paging, without serializing the entire sequence on each request.

Article search is a separate stored-article scan rather than membership FTS.
Structured tweet filters are rejected with article type; the recommended path is
tweet search plus `has:article`.

## Tweet list API

```http
GET /api/tweets?q=...&collection=all&sort=default&page=1&limit=20
```

Constraints/behavior:

- page ≥ 1;
- limit 1–100;
- collection aliases normalize to bookmark/like/tweet/all;
- an invalid collection currently falls back silently to all;
- default sort is relevance with positive text, newest otherwise;
- `liked_latest` / `liked_earliest` require `collection=likes` (or `like`), otherwise 400;
- malformed/mismatched cursors and search errors return 400; changed like-order cursors return 409; generic errors return 500.

Response shape:

```json
{
  "tweets": [],
  "total": 0,
  "page": 1,
  "pages": 1,
  "has_more": false,
  "next_cursor": null,
  "truncated": false
}
```

Start cursor scrolling with `cursor=` and send each returned `next_cursor` unchanged
on the next request, retaining the same query, collection, sort, seed, and limit.
The response `page` advances from the token. Omitting `cursor` preserves explicit
page/offset clients. Text totals/pages are `null`; `has_more` is authoritative.

Unfiltered newest/oldest feeds seek through composite chronological indexes,
including explicit NULL/tie ranges. Likes seek by indexed sequence position even
with a search query (matching complex queries can still require grouping). Other
search/relevance/random cursors carry an offset and retain their existing complete
result semantics; they do not promise constant-time deep searches.

Chronological cursors retain their boundary if its row is deleted and exclude new
head rows already above it. This is a live view, not a database snapshot: editing
sort keys can change membership/order during scrolling. Like-order revision changes
return 409; the browser restarts that list. The browser commits page/cursor state
only after success, ignores stale responses, deduplicates appended IDs, and exposes
a Load more/Retry control alongside automatic scrolling. Legacy APIs without
`has_more` use page counts as a compatibility fallback.

Hydrated rows can include author, collection membership, media, URLs, article,
direct/quote tags, raw JSON, availability, quote/retweet attachments, and match
score/type metadata.

## Detail assembly

```http
GET /api/tweets/{tweet_id}
```

The route:

- resolves the main tweet and canonical object;
- walks at most 50 parent iterations with cycle protection;
- fetches direct children plus one additional descendant level;
- loads relations, memberships, media, and tags through indexed queries;
- sorts parents oldest-first;
- nests same-author child replies under `op_replies`;
- sorts primary children by OP-reply presence then captured like count;
- adds `local_quote_count`.

Response:

```json
{
  "main": {},
  "parents": [],
  "children": []
}
```

Missing relation targets become `not_archived` placeholders. Unknown main IDs
return 404. Local quote count is archive-relative.

## Quote API

```http
GET /api/tweets/{tweet_id}/quotes?page=1&limit=20
```

It counts distinct quote-source IDs, pages newest-first, hydrates/annotates them,
and returns `tweets`, `total`, `page`, and `limit` (not `pages`/`truncated`).

## Availability overlay

`web/availability.py` is the presentation authority. It batch-loads canonical
objects in chunks, replaces stale embedded content when a done/resurrected
object exists, attaches quote/retweet objects/media/tags, and limits attached
media to 10.

Canonical cases:

- available: done/resurrected and confirmed;
- unavailable: explicit terminal reason, placeholder as appropriate;
- incomplete: pending/transient/missing detail, preserving useful embedded
  content;
- not archived: relation-only target with no local object.

A rare existing object with an unknown/NULL lifecycle state falls back to
`available` with `confirmed: false`.

Raw JSON/media is cleared for placeholders. Embedded content alone does not
prove current availability.

## Author and tag support APIs

```http
GET /api/authors/search?q=...
GET /api/tags/autocomplete?q=...
GET /api/tags/stats
```

Author search returns at most 10 local matches; blank input is empty. Tag
autocomplete returns tag/count entries and also supports blank input for popular
tags.

Tag mutation routes:

```http
PUT    /api/tags/{tweet_id}
DELETE /api/tags/{tweet_id}
DELETE /api/tags/global/{tag}
POST   /api/tags/merge
```

Mutations use storage normalization/merge rules. They do not invalidate the
five-minute Web stats cache.

## Stats routes

Canonical report sections are overview, collections, archive status, storage,
and tagging. `/api/stats/report` is fresh; `/api/stats/snapshot` is the cached
legacy/UI shape. Snapshot behavior:

- initial collection is synchronous and deduplicated;
- entries are fresh for 12 hours;
- stale data is returned immediately while one daemon refresh runs;
- failed refresh retains stale data and sets `refresh_failed`;
- explicit refresh starts one worker or collects synchronously when absent.

Compatibility endpoints expose individual legacy sections. Storage component
breakdowns include exact filesystem totals plus sampled logical estimates.

## Authentication and errors

Routers normally depend on `verify_credentials`; root and media are also
protected. Static assets are mounted without the dependency.

Many route-specific validation errors return 400/404/409/422/503 appropriately.
Several generic handlers return raw exception text and print tracebacks. Avoid
putting secrets into exception messages and do not treat this as a safe public
multi-tenant API.

## Frontend integration

`autocomplete.js` suggests only a subset: from, to, has, is, since, until, URL,
and tag. It formats `filter:` if typed but does not suggest it. Advanced parser
keys must remain documented/tested independently of UI discoverability.

List API defaults to 20 because the frontend does not send `limit`. Thread
responses are cached client-side for 60 seconds with a maximum of 25 entries.

## Regression targets

- `tests/test_search.py`
- `tests/test_web_tweets_api.py`
- `tests/web/test_tweet_availability.py`
- `tests/web/test_tag_routes.py`
- `tests/web/test_stats_routes.py`
- `tests/web/test_stats_cache.py`
- `tests/web/test_storage_stats_routes.py`
- `tests/test_web_assets.py`
- `tests/js/test_web_assets.cjs`
