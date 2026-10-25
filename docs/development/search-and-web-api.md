# Search and API

`search.py` is shared by CLI search and web feeds. Routes handle HTTP validation/authentication and presentation; query parsing and result semantics belong in the shared search layer.

## Query syntax

The parser tokenizes words, quoted phrases, negated clauses and recognized filters. Clauses are implicitly ANDed. Uppercase `OR` groups adjacent alternatives; uppercase `NOT` negates the next clause. Parentheses don't create groups. For example, `cats OR dogs from:alice` means an animal alternative combined with the author filter.

Validate filter keys, enum values, numeric inputs and date forms before querying. Quote user text in generated predicates and full-text expressions; a user's filter value isn't SQL or an invitation to inject FTS operators. Description searches match every supplied word with optional suffix-prefix markers.

The [user search guide](../search.md) lists supported operators. Hidden posts are excluded by default and `is:hidden` explicitly selects them. `is:missing` and `is:resurrected` use canonical object state. Tag/description filters can find a saved post through its quoted target's tags.

Posts without captured content are excluded from list/count results unless any clause names `is:uncaptured`. The write-maintained `archive_tweet_content` flags cover memberships and context objects. Separate saved media and Article records also count, even when post text and raw media metadata are absent. An ID is uncaptured only when none of these records has content. Tombstones, system text and metadata-only post rows do not count on their own. Feed totals subtract the union of hidden and uncaptured members once; detail/raw/quote routes retain access to placeholders.

`has:note` matches captured Community Note text or any `community_notes` version, including removed notes. It also includes direct `quote_of` sources using indexed relations, including quoted targets outside collections. It does not follow deeper quotes.

## Results and pagination

Search can return posts and Articles, with collection filtering and relevance/newest/oldest CLI sorts. Web feeds also support random, most-liked and liked-order modes. Post results deduplicate membership observations at the post boundary; collection filters still refer to actual memberships.

Indexes and derived facts support normal reads without reparsing each raw payload. Count work can be requested independently from the first result page. Keep exact totals distinct from absent/deferred totals; `has_more` and `next_cursor` let a client continue without inventing a page count.

Cursors bind query context, ordering mode and position. Like-order cursors also carry a derived revision so a changed order can return an explicit expired-cursor error instead of silently skipping posts. Invalid search/cursor inputs return 400; an order revision conflict returns 409.

## Feed and post routes

`GET /api/tweets` accepts `q`, `collection`, `sort`, `page`, `limit`, `random_seed`, and `cursor`. Limit is 1 through 100. It returns `tweets`, optional total/pages, current page, `has_more`, `next_cursor`, `truncated`, and highlight terms. `GET /api/tweets/count` provides the matching count separately.

Detail routes collect saved parents, focal content, author-thread rows and other replies through indexed relationships. Quote pages, raw capture views, availability/history and Community Notes have separate resources. Use the shared availability/card/Article formatters so a quote and a focal tweet follow the same contract.

Normal list payloads are slimmed after presentation annotation; raw JSON is available through its own route. Direct browser document paths serve the app shell, including `/post/ID` and quote views, so refresh/back/forward remains usable.

## Route groups

- Tweet, thread, quote, author and search routes are in `routes/tweets.py`.
- Local/manual tags are in `routes/tags.py`.
- Setup, credentials, import staging and replay connection are in `routes/setup.py`.
- Worker/job options, run/start/queue controls and activity streams are in `routes/jobs.py` and `routes/activity.py`.
- Runtime configuration, schedule and Gemini controls have dedicated route modules.
- Analytics, storage totals, notices and avatars are separate read/control resources.

Most application routes require `verify_credentials`; archive-dependent reads also require `require_store`. Setup/status/configuration remain usable before a store exists. Keep masked secrets out of serialized responses and validate run IDs and managed paths before returning logs/files.

## Endpoints

Routes and request/response models are defined in `web/routes/`. The [documentation checks](testing.md#documentation-checks) compare this index with registered methods and paths, including router prefixes and replay methods.

| Method | Path | Module |
| --- | --- | --- |
| GET | `/api/activity/status` | `activity.py` |
| GET | `/api/activity/events` | `activity.py` |
| POST | `/api/activity/stop` | `activity.py` |
| POST | `/api/activity/sync` | `activity.py` |
| POST | `/api/activity/enrich` | `activity.py` |
| POST | `/api/activity/import` | `activity.py` |
| GET | `/api/activity/runs` | `activity.py` |
| GET | `/api/activity/runs/{run_id}` | `activity.py` |
| GET | `/api/activity/runs/{run_id}/log` | `activity.py` |
| GET | `/api/activity/schedule` | `activity.py` |
| PUT | `/api/activity/schedule` | `activity.py` |
| GET | `/api/automated-tagging` | `automated_tagging.py` |
| PUT | `/api/automated-tagging` | `automated_tagging.py` |
| GET | `/api/automated-tagging/tweets` | `automated_tagging.py` |
| POST | `/api/automated-tagging/test` | `automated_tagging.py` |
| POST | `/api/automated-tagging/models` | `automated_tagging.py` |
| GET | `/api/avatar/{user_id}` | `avatars.py` |
| GET | `/api/config` | `config.py` |
| GET | `/api/config/defaults` | `config.py` |
| GET | `/api/config/schema` | `config.py` |
| POST | `/api/config` | `config.py` |
| GET | `/api/dev/replay/status` | `dev_replay.py` |
| GET | `/api/dev/replay/x/{host}/{path:path}` | `dev_replay.py` |
| HEAD | `/api/dev/replay/x/{host}/{path:path}` | `dev_replay.py` |
| POST | `/api/dev/replay/x/{host}/{path:path}` | `dev_replay.py` |
| GET | `/api/jobs` | `jobs.py` |
| POST | `/api/jobs/{key}/run` | `jobs.py` |
| DELETE | `/api/jobs/{key}/queue` | `jobs.py` |
| GET | `/api/notices` | `notices.py` |
| POST | `/api/notices/dismiss-all` | `notices.py` |
| DELETE | `/api/notices/dismissed` | `notices.py` |
| POST | `/api/notices/{notice_id}/dismiss` | `notices.py` |
| GET | `/api/setup` | `setup.py` |
| PUT | `/api/setup/replay` | `setup.py` |
| PUT | `/api/setup/replay/placeholder-media` | `setup.py` |
| PUT | `/api/setup/replay/skip-quote-expansion` | `setup.py` |
| PUT | `/api/setup/replay/skip-link-previews` | `setup.py` |
| DELETE | `/api/setup/replay` | `setup.py` |
| PUT | `/api/setup/auth` | `setup.py` |
| PUT | `/api/setup/archive` | `setup.py` |
| DELETE | `/api/setup/archive` | `setup.py` |
| POST | `/api/setup/archive/import` | `setup.py` |
| POST | `/api/setup/archive/empty` | `setup.py` |
| POST | `/api/setup/migrate` | `setup.py` |
| GET | `/api/setup/schedule` | `setup.py` |
| PUT | `/api/setup/schedule` | `setup.py` |
| PUT | `/api/setup/password` | `setup.py` |
| POST | `/api/setup/complete` | `setup.py` |
| GET | `/api/stats/report` | `stats.py` |
| GET | `/api/stats/snapshot` | `stats.py` |
| GET | `/api/stats/status` | `stats.py` |
| POST | `/api/stats/refresh` | `stats.py` |
| GET | `/api/stats/enrichment-incomplete` | `stats.py` |
| GET | `/api/stats/latest-sync` | `stats.py` |
| GET | `/api/stats/summary` | `stats.py` |
| GET | `/api/stats/collections` | `stats.py` |
| GET | `/api/stats/health` | `stats.py` |
| GET | `/api/stats/tags` | `stats.py` |
| GET | `/api/storage/breakdown` | `storage_stats.py` |
| DELETE | `/api/tags/{tweet_id}` | `tags.py` |
| PUT | `/api/tags/{tweet_id}` | `tags.py` |
| DELETE | `/api/tags/global/{tag}` | `tags.py` |
| POST | `/api/tags/merge` | `tags.py` |
| GET | `/api/tags/autocomplete` | `tags.py` |
| GET | `/api/tags/stats` | `tags.py` |
| GET | `/api/tweets` | `tweets.py` |
| GET | `/api/tweets/count` | `tweets.py` |
| GET | `/api/tweets/{tweet_id}` | `tweets.py` |
| GET | `/api/tweets/{tweet_id}/quotes` | `tweets.py` |
| GET | `/api/tweets/{tweet_id}/raw` | `tweets.py` |
| GET | `/api/tweets/{tweet_id}/history` | `tweets.py` |
| GET | `/api/tweets/{tweet_id}/notes` | `tweets.py` |
| PUT | `/api/tweets/{tweet_id}/hidden` | `tweets.py` |
| DELETE | `/api/tweets/{tweet_id}/hidden` | `tweets.py` |
| GET | `/api/authors/search` | `tweets.py` |
| GET | `/api/authors/profile` | `tweets.py` |

## Response details

Chronological feed cursors seek through indexed date/sort/ID boundaries; liked-order cursors seek sequence positions and carry an input revision. Other complex search cursors can retain offsets, so deep paging isn't universally constant-time. Treat cursor data as opaque and retain query, collection, sort, seed and limit between requests. An empty `cursor=` starts cursor paging.

Text results can omit totals/pages while `/api/tweets/count` computes exact deduplicated matches on a private reader. Filter-only/feed totals can be returned immediately. Positive text drives relevance by default; filter-only queries default to newest. Hidden exclusion is relaxed when any `is:hidden` clause is present, then the compiler evaluates its positive/negative/OR predicate normally.

Article search uses the shared query parser and matches saved Article text in Python. It rejects structured post filters, selects collection memberships and excludes hidden posts before limiting results. Article dates and relevance are sorted across matching candidates before the result limit is applied.

Detail assembly walks up to fifty parents with cycle protection and up to 250 descendant replies with indexed breadth-first frontier reads. Replies expose flattened descendant metadata under `op_replies`, plus `thread_depth` and `parent_tweet_id`. Mention-only direct replies below five likes can be placed in `other_children`; media, links, quotes, substantive text, and saved branches retain primary visibility.

Quote results include canonical context objects outside collections and retain placeholder slots for uncaptured sources. They return tweets/total/page/limit rather than the list endpoint's full paging shape. Author search is local and capped at ten matches; profile history returns distinct captured states by fetch time, not post time, with a limit from 1 through 100. Tag autocomplete matches every query fragment literally and case-insensitively.

List/detail/quote payloads abbreviate raw data to fields the UI renders. The complete stored document remains available at `/api/tweets/{tweet_id}/raw`; fixtures test that abbreviated/full forms render equivalently. Highlight terms come from submitted positive text/hashtag clauses and are applied before safe HTML escaping, including quote text and Show more.
