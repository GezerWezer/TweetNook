# Storage

The active backend is SQLite, opened through `open_archive_store`. It stores multiple record types in the `archive` table and maintains derived search structures alongside it. A database is `archive.db` under the resolved data directory; files such as media live beside it.

## Layout

`ArchiveStore` in `storage/backend.py` owns the SQLite connection and combines one mixin per storage concern. Each mixin lives in its own module and inherits the mixins it calls, so the class headers show which groups depend on which.

| Module | Owns |
| --- | --- |
| `record_types.py` | `RecordType`, the names of the record types below. |
| `common.py` | SQL fragment helpers, source names and ranks, shared hidden/uncaptured/note filters. |
| `db.py` | Serialized transactions, query helpers, cache revisions, read warming, close and optimize. |
| `schema.py` | Table layout, FTS and derived tables, `SCHEMA_VERSION`, upgrades from released schemas. |
| `records.py` | Row construction, row keys, `PageBuffer` and source-precedence merging. |
| `sync_state.py` | Sync cursors, the archive owner and import manifests. |
| `ingestion.py` | Normalized official-import observations, membership and graph prefetch, placeholder gating and batch completion. |
| `tweets.py` | Timeline page writes: raw captures, memberships and `persist_page`. |
| `objects.py` | Canonical tweet objects and their relations, media, URLs and articles. |
| `details.py` | Tweet detail, thread, unavailable-tweet and FxTwitter writes. |
| `enrichment.py` | Media download, URL unfurl, link card and resurrection queues. |
| `checks.py` | Availability check results, context checks and Community Notes. |
| `tags.py` | Media tags, global tag edits, hidden tweets, tagging candidates and retry metadata. |
| `presentation.py` | Batched saved post/attachment/article/link-card observations, note observation state and archive-save history facts. |
| `conversations.py` | Directional ancestor/reply traversal, bounded thread observations, quote counts/pages and stored tweet JSON. |
| `relations.py` | Known-ID, thread-target, URL-reference and quote lookups. |
| `authors.py` | Avatar URLs, handle resolution, profile snapshots and handle search. |
| `reads.py` | Feed pagination, like order, ID fetches and export hydration. |
| `stats.py`, `report_facts.py` | Counts, the archive summary and typed database facts for requested report sections. |
| `prune.py` | Context graph projections, scoped row and media inventories, prune deletion. |
| `imports.py` | Official-archive import cleanup and rehydration from raw captures. |
| `search.py` | Full-text and structured search. |
| `search_query.py` | Search query parsing, filter predicates, sorts and cursors, shared by CLI search and web feeds. |
| `ordering.py` | Logical-post membership keys and shared chronological ordering for feeds and mixed search. |
| `like_order.py` | Like order rebuilt from captured observations. |

Put a new storage operation in the module for its concern, and add a mixin base only when it calls into another group. Bases follow the order in `ArchiveStore`'s header, which keeps Python's method resolution order consistent.

## Opening the archive

`open_archive_store` in `storage/backend.py` opens SQLite archives. With `create=False`, a missing archive stays missing, which lets the web server offer Setup without creating an empty database as a side effect. Legacy LanceDB content requires an explicit migration.

Writable connections use WAL and recursive triggers. Database configuration sets the page-cache and memory-mapping allowances. Shared web connections disable CPython's prepared-statement cache; private read-only connections serve work that needs a reader confined to one thread. Close connections through the store lifecycle.

`transaction()` serializes mutations and nests helpers through savepoints. If SQLite aborts the entire transaction or savepoint rollback fails, further mutations are refused until the outer scope exits, and that scope cannot report success. The original database failure still propagates from the failing helper.

Statistics collect only the requested database facts in one bounded read snapshot, then release it before filesystem measurement and presentation. Collection-only reports avoid health graphs; owner-only access reads archive metadata directly. Overview and collections use the same valid-date bounds and ignore malformed dates. A caller with pending writes borrows its existing transaction without committing or rolling it back.

Random-search and legacy-export SQL callbacks register once at connection creation, before the store is shared. Reads never replace functions while another cursor is active.

The schema maintains content and Community Note flags used by search, including uncaptured placeholders and note history. Migrations backfill derived data transactionally while preserving saved content.

Schema migrations run when a writable store opens. `SCHEMA_VERSION` in `storage/schema.py` is the authoritative version. New databases receive the latest schema; migrations must preserve existing archive content and update derived structures as needed.

## Record types

| Record | Purpose and identity |
| --- | --- |
| `tweet` | Collection membership, including collection/folder and timeline ordering. |
| `tweet_object` | Canonical saved content for one tweet ID. |
| `tweet_relation` | Directed reply, quote, repost, and other extracted relationships. |
| `raw_capture` | A source observation with operation, cursors, timestamp, HTTP status and raw JSON. |
| `sync_state` | Per-collection head and backfill progress. |
| `media` | Media identity, remote variants, local paths, hashes and download state. |
| `url`, `url_ref` | Shared URL metadata and each post's link references. |
| `article` | Article metadata, text, content payload and status. |
| `import_manifest` | Archive digest, owner/import counts, warnings and follow-up state. |
| `media_tag` | Tags, description and tagging status attached to a tweet. |
| `tweet_hidden` | A local visibility choice. |
| `metadata` | Durable application metadata and accounting values. |

Name record types through `RecordType` from `storage/record_types.py`, in SQL text as well as Python, so a misspelt type fails at import time instead of matching no rows. In SQL, interpolate the member as a quoted literal (`'{RecordType.MEDIA}'`) rather than binding it as a parameter: the partial indexes are declared on literal record types, and SQLite only uses one when the query's condition is the same literal.

Use the row-key helpers in `ArchiveStore`; don't invent identifiers in callers. A canonical tweet object and its memberships are different facts. Deleting or enriching context must not manufacture a bookmark, like, or authored membership.

`metadata_value(key)` reads a durable value. `reserve_metadata_counter(key, decide)` runs its read and optional replacement inside one immediate transaction and commits before returning. It refuses enclosing transactions so quota reservations cannot be lost after a request is dispatched; quota policy stays in tagging and never opens or rolls back a connection transaction itself.

## Merge rules

`flush(buffer)` owns complete-record persistence and clears the batch only after a successful commit. A nonempty flush refuses any enclosing transaction, keeping its rows available for a standalone retry. A failed flush keeps the batch available for retry; feature code does not merge or clear its dictionaries. The low-level `merge_rows` operation replaces complete records, setting omitted columns to NULL and participating in an enclosing transaction when present.

`ingest_import_batch(observations, membership=...)` owns import row construction, key prefetch, sparse-like placeholder gating and completion. Importers keep payload adaptation, chunk sizing, counts and progress. `current_objects(ids, cursor=...)` reads canonical state with pending batch visibility; checks and resurrection use storage-owned flushes.

`PageBuffer` collects related writes and tracks pending rows so a later item in a batch sees earlier writes. Source ranks prefer `live_graphql` over `fxtwitter` over `x_archive`. Merge code preserves better content, timestamps, downloaded files, and provenance when a weaker payload arrives.

Raw captures remain evidence of the source response. Normalized tweet text, author fields, media and relation rows can be rebuilt through `rehydrate`, but rebuilding isn't a new remote observation. Test quoted and nested content as well as the focal post when changing extraction.

Tagging coverage counts eligible saved posts and their direct quotes. Tag usage and the average tags per tagged post use the same archive-wide population, including manually tagged context and unavailable posts.

Removing labels preserves an existing description, including removal of the final label. Passing an empty description explicitly clears it; `delete_media_tag` deletes both tags and description.

Tagging uses storage-owned quote/type classification and `tagging_context(ids)` for bounded media and text inputs. `save_tagging_result` and `mark_tagging_failed` own tag-row and retry-metadata writes; the feature validates provider output and retains its batch transaction. A failed attempt never replaces saved tags or a description, and generated tags join a description that outlived its tags instead of replacing it.

## Availability and history

Enrichment states include `pending`, `done`, `transient_failure`, `terminal_unavailable`, and `resurrected`. The unavailable reason, checked time, retry count, retry eligibility, and next retry time are separate fields. A transient transport failure must not turn into proof of deletion. Retry queues, marking, check scope and reports share one eligibility predicate: no saved deletion, an explicit eligible flag, and no permanent deletion reason. A NULL flag is ineligible; a NULL reason does not exclude an explicitly eligible row.

Checks and Community Note changes have dedicated history storage. `note_observations(ids)` distinguishes a missing entry (never observed), an observed absent note and an active note. `archive_save_observations(ids)` supplies membership save times or related-post addition times without exposing record types to event presentation. A post can have saved content and a current unavailable state at the same time. See [availability checks](availability-checks.md) for the transition rules.

## Search and ordering

Scalar indexes cover membership, ID, relation, media and queue reads. FTS5 indexes support saved post text and local descriptions. Article text is matched in Python using the shared query parser. Trigger-maintained tweet facts extract common raw-payload filters once when data changes. Cache revisions invalidate derived totals and search pages after relevant writes.

Chronological feed, filtered post search and mixed post/Article results use `storage/ordering.py`: the lowest membership row key within the requested collections supplies the normalized timestamp and numeric timeline key. Undated posts follow dated posts in both directions; timeline keys and text IDs follow the requested direction, with SQLite's native NULL order for timeline keys. Page hydration selects that same membership. Fast continuations seek indexed ranges, including a separate undated tail for oldest order. Export retains its independent legacy-date and integer-key ordering.

Like-order result, capture-page and TEMP-index caches are declared store state. TEMP paging and archive mutations use the same connection lock, while paging savepoints preserve an enclosing transaction.

Like order derives from immutable live/archive observations rather than invented timestamps. `storage/like_order.py` uses anchors shared between captures, preserves preferred observations, and places disconnected history deterministically. Its archive block repair is empirical and chooses the candidate with fewer ordering disagreements against live anchors. Rebuilding is possible without changing the captured evidence.

`completed_manifests()` selects completed imports for deferred enrichment. Rehydration exposes its membership work total through `rehydration_work_total()`; callers do not choose record predicates or count detail captures as membership progress.

## Maintenance

`db check` uses SQLite's quick check; `db check --full` uses its integrity check. `optimize` handles SQLite space/query/index maintenance. Jobs do not perform automatic mid-run or interrupt compaction. Explicit optimization and close-time planner maintenance are separate from job progress.

Prune computes a reachable context graph from saved memberships and current depth settings. It can remove manually expanded context outside that graph. Planning owns a bounded read transaction and drops its TEMP inventory before returning; applying takes the write lock, removes scoped rows/files, and vacuums. Focal-owned authenticated and FxTwitter captures are deleted with pruned context, so local rehydration cannot replay those captures. Kept captures remain source evidence. Before deleting rows, apply atomically publishes root-contained file cleanup intent in `prune-pending.json` beside the database. Failed unlinks remain in that file and are retried by `prune --apply`, including when no rows remain to prune. Each retry protects surviving media references; paths outside media or below symlinks are ignored. Partial cleanup reports committed rows and pending files and exits unsuccessfully. An invalid `prune-pending.json` stops prune, including its dry run, with an error naming the file, because silently dropping it would forget the files it lists. Test the graph and file boundaries before changing it.

Stop writers before copying a whole installation. A live database copy needs SQLite's backup protocol or the relevant WAL state; copying only the main file during writes is not a reliable backup. Keep data, media, configuration and session material private.

## Transactions and indexes

`persist_page` buffers raw capture, memberships, the canonical/secondary graph and sync state before one merge transaction. Direct membership writes take a `TimelineTweet` and an active page buffer; no separate pending-tweet staging map is needed. UPSERT preserves rowids, which matters for the derived full-text documents.

`store.transaction()` serializes writers on the shared connection across selection, transformation and commit. Store helpers nest through savepoints and never commit the enclosing operation. An unowned transaction is rejected without rolling back its work. Passing `write=False` opens a bounded read snapshot on a private reader. Global tag edits, regeneration cleanup and card restoration each own their complete database mutation; file cleanup stays outside SQL transactions. Only writable stores that completed a write transaction run planner maintenance on close; TEMP read indexes do not make a reader a writer.

`archive_fts` is contentless FTS5 with author username, display name, text and Note Tweet text. Triggers index membership rows, not every context object. `archive_tweet_facts` is keyed by membership row key and holds structured fields extracted from raw JSON; triggers keep it aligned even for direct SQL writes. Malformed JSON yields absent facts rather than a failed write.

`archive_tweet_content` holds `has_content` and `has_note` for membership and context-object row keys. Insert/update/delete triggers maintain these indexed flags. When no membership or context object has captured content, the uncaptured filter also checks separate media and Article records by indexed tweet ID and scalar fields. Feeds and Community Note searches do not need to parse JSON on each read.

`archive_description_fts` stores nonempty string descriptions from `media_tag` rows. Its update/delete path removes old entries by rowid. Keep description matching separate from ordinary post-text clauses.

Creation and migration establish the scalar and full-text indexes. `rebuild_search_index` is the explicit rebuild path. Opening a current-schema archive does not repair missing indexes or run an integrity check.

Bounded caches use persisted revisions and bypass ordinary reuse inside an uncommitted transaction, so a rolled-back write doesn't poison later results. Like-order input revisions are narrower than general content changes. Export ranks/deduplicates membership keys before hydrating full payloads, but an unlimited export still materializes the complete result.

## Legacy migration

`importer/legacy_migration.py` runs the optional LanceDB reader in subprocesses and writes the current SQLite shape. Destination inserts ignore existing row keys so a rerun cannot replace newer rows. A failed source range is retried in smaller batches; unreadable rows can be skipped and reported. Inspect the final migration report, not just the exit code, and retain the source until collection/search/media checks are satisfactory.

`archive.lancedb/` remains a separate source. A current schema open is different from that explicit one-way migration. Test skipped-row/partial reporting, reruns, empty input, destination failures and full-text rebuild after import.
