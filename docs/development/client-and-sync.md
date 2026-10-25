# Client and sync

`sync.py` coordinates authenticated collection reads. `client/base.py` owns transport and pacing; `client/timelines.py` builds and parses timeline/detail operations. Keep protocol handling below the CLI and web layers so every entry point gets the same behavior.

## Credentials and preflight

Credentials come only from explicit config or environment values. `auth_token` and `ct0` are required for authenticated requests; likes and authored timelines also require a numeric user ID.

Preflight validates local configuration, account ownership, and the relevant remote operation before starting a collection. `auth check` probes bookmarks, likes, and tweets and reports local versus remote readiness. Use mocked transports in tests; never put a developer's cookies into a fixture.

## Query IDs and feature flags

Operation IDs are discovered from X's published web bundles and stored under the cache directory. `QueryIdStore` merges cached/discovered information with known defaults. `auth refresh-ids` forces discovery. Stale IDs and feature-flag drift have explicit exceptions so callers can refresh or report a useful error without treating a malformed response as an empty timeline.

Keep request headers, variables, feature flags, cursor formats, and response traversal in their protocol modules. Third-party reference snapshots can help establish these X details, but their application architecture is outside the project's reference scope.

## Sync progress

A collection stores a head anchor plus older-history cursor/incomplete state. A normal run checks the head for recent items and can continue an unfinished backfill. Rows and cursor progress are written incrementally, so interruption leaves a resumable archive.

| Mode | State behavior |
| --- | --- |
| Normal | Fetch recent posts and continue eligible older history. |
| `--head-only` | Clear the saved backfill cursor and run the head pass only. |
| `--backfill` | Continue past known posts to repair gaps and extend history. |
| `--full` | Reset targeted sync state before walking the collection again. |
| `--article-backfill` | Revisit existing pages for Article fields without clearing state. |

`--head-only` is incompatible with `--full`, `--backfill`, and `--article-backfill`. The other modes can be combined. A page limit bounds collection-page work, not the detail/media/tagging follow-ups. `sync` and `sync all` use `sync.collections`; individual subcommands target only their named collection.

## Page writes

Capture the raw page and its operation/cursors/status, extract graph records, persist membership/order observations, and advance sync state consistently. Tombstones need their IDs and reasons preserved even when their content isn't available. Repeated sparse or malformed focal results are different from an authoritative unavailable response.

## Requests and errors

Timeline and detail pacing use separate delay, retry and backoff settings. Adaptive pacing responds to remote rate limits and repeated failures; rate-limit resets may make a run wait. Auth expiry, stale IDs, feature drift, exhausted retries, repeated focal absence, and storage exhaustion remain distinguishable.

Do not skip past a failed page and claim the collection is caught up. Preserve the last committed state, expose the stop reason, and allow another run to continue. A later follow-up failure also must not erase the earlier successful collection outcome.

## Follow-up jobs

The normal run plans conversation expansion, unavailable-post restoration when owned by sync, Article refresh, media download, URL unfurling, and optional tagging. Individual skip flags remove those stages. When FxTwitter and automatic checks are enabled, the check job owns restoration; otherwise sync can perform its bounded authenticated restoration pass.

`PipelineReporter` publishes planned stages before work starts and records per-collection outcomes. The same report is consumed by the terminal, web Jobs panel, activity history, and scheduler notifications.

## Protocol and locks

Authenticated requests scope the two cookies to `.x.com` and include CSRF, public bearer, origin/referer, language, active-user and user-agent headers. Status classification distinguishes 401/403 auth expiry, 400 feature drift, 404 stale IDs, 429 pacing, and other API failures. Transport exceptions aren't universally wrapped.

Query-ID freshness is 24 hours. A stale cached value may still be readable, but a forced discovery failure can escape before fallback selection. Current operation names include Bookmarks, Likes, UserTweets, TweetDetail, BookmarkFolderTimeline and UserArticlesTweets; not every known protocol operation is a public CLI collection.

Account IDs are expected to be numeric in real X use, but local auth validation requires nonempty text rather than enforcing digit syntax. A bookmark-only run without an ID can't compare or seed the owner from that ID.

`PipelineReporter` owns the lifecycle lock, while sync holds the writer lock around archive work. Read-only commands on the current schema can use WAL snapshots without taking the writer lock; an opening schema migration is an exception. See [trust and security](architecture.md#trust-and-security) for log privacy limits.
