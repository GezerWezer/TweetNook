# Storage

The active backend is SQLite, opened through `open_archive_store`. It stores multiple record types in the `archive` table and maintains derived search structures alongside it. A database is `archive.db` under the resolved data directory; files such as media live beside it.

## Opening the archive

`open_archive_store` in `storage/backend.py` opens SQLite archives. With `create=False`, a missing archive stays missing, which lets the web server offer Setup without creating an empty database as a side effect. Legacy LanceDB content requires an explicit migration.

Writable connections use WAL and recursive triggers. Database configuration sets the page-cache and memory-mapping allowances. Shared web connections disable CPython's prepared-statement cache; private read-only connections serve work that needs a reader confined to one thread. Close connections through the store lifecycle.

The schema maintains content and Community Note flags used by search, including uncaptured placeholders and note history. Migrations backfill derived data transactionally while preserving saved content.

Schema migrations run when a writable store opens. `SCHEMA_VERSION` in `storage/backend.py` is the authoritative version. New databases receive the latest schema; migrations must preserve existing archive content and update derived structures as needed.

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

Use the row-key helpers in `ArchiveStore`; don't invent identifiers in callers. A canonical tweet object and its memberships are different facts. Deleting or enriching context must not manufacture a bookmark, like, or authored membership.

## Merge rules

`_PageBuffer` collects related writes and tracks pending rows so a later item in a batch sees earlier writes. Source ranks prefer `live_graphql` over `fxtwitter` over `x_archive`. Merge code preserves better content, timestamps, downloaded files, and provenance when a weaker payload arrives.

Raw captures remain evidence of the source response. Normalized tweet text, author fields, media and relation rows can be rebuilt through `rehydrate`, but rebuilding isn't a new remote observation. Test quoted and nested content as well as the focal post when changing extraction.

## Availability and history

Enrichment states include `pending`, `done`, `transient_failure`, `terminal_unavailable`, and `resurrected`. The unavailable reason, checked time, retry count, retry eligibility, and next retry time are separate fields. A transient transport failure must not turn into proof of deletion.

Checks and Community Note changes have dedicated history storage. A post can have saved content and a current unavailable state at the same time. See [availability checks](availability-checks.md) for the transition rules.

## Search and ordering

Scalar indexes cover membership, ID, relation, media and queue reads. FTS5 indexes support saved post text and local descriptions. Article text is matched in Python using the shared query parser. Trigger-maintained tweet facts extract common raw-payload filters once when data changes. Cache revisions invalidate derived totals and search pages after relevant writes.

Like order derives from immutable live/archive observations rather than invented timestamps. `like_order.py` uses anchors shared between captures, preserves preferred observations, and places disconnected history deterministically. Its archive block repair is empirical and chooses the candidate with fewer ordering disagreements against live anchors. Rebuilding is possible without changing the captured evidence.

## Maintenance

`db check` uses SQLite's quick check; `db check --full` uses its integrity check. `optimize` handles SQLite space/query/index maintenance. Old fragment-compaction hooks remain compatibility no-ops for this backend and shouldn't be described as active maintenance work.

Prune computes a reachable context graph from saved memberships and current depth settings. It can remove manually expanded context outside that graph. Planning is read-only by default; applying takes the write lock, removes scoped rows/files, and vacuums. Test the graph and file boundaries before changing it.

Stop writers before copying a whole installation. A live database copy needs SQLite's backup protocol or the relevant WAL state; copying only the main file during writes is not a reliable backup. Keep data, media, configuration and session material private.

## Transactions and indexes

`persist_page` buffers raw capture, memberships, the canonical/secondary graph and sync state before one merge transaction. Direct membership helpers require an active page buffer. UPSERT preserves rowids, which matters for the derived full-text documents.

`archive_fts` is contentless FTS5 with author username, display name, text and Note Tweet text. Triggers index membership rows, not every context object. `archive_tweet_facts` is keyed by membership row key and holds structured fields extracted from raw JSON; triggers keep it aligned even for direct SQL writes. Malformed JSON yields absent facts rather than a failed write.

`archive_tweet_content` holds `has_content` and `has_note` for membership and context-object row keys. Insert/update/delete triggers maintain these indexed flags. When no membership or context object has captured content, the uncaptured filter also checks separate media and Article records by indexed tweet ID and scalar fields. Feeds and Community Note searches do not need to parse JSON on each read.

`archive_description_fts` stores nonempty string descriptions from `media_tag` rows. Its update/delete path removes old entries by rowid. Keep description matching separate from ordinary post-text clauses.

`ensure_scalar_indexes` and `ensure_fts_index` are currently no-ops. Startup invokes them, but creation/migration is what establishes those structures. `rebuild_search_index` is the explicit rebuild path. Opening a current-schema archive does not repair missing indexes or run an integrity check.

Bounded caches use persisted revisions and bypass ordinary reuse inside an uncommitted transaction, so a rolled-back write doesn't poison later results. Like-order input revisions are narrower than general content changes. Export ranks/deduplicates membership keys before hydrating full payloads, but an unlimited export still materializes the complete result.

## Legacy migration

The optional LanceDB reader runs in subprocesses and writes the current SQLite shape. Destination inserts ignore existing row keys so a rerun cannot replace newer rows. A failed source range is retried in smaller batches; unreadable rows can be skipped and reported. Inspect the final migration report, not just the exit code, and retain the source until collection/search/media checks are satisfactory.

`archive.lancedb/` remains a separate source. A current schema open is different from that explicit one-way migration. Test skipped-row/partial reporting, reruns, empty input, destination failures and full-text rebuild after import.
