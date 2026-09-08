# Storage

[Development guide](README.md) · [Architecture](architecture.md) ·
[Archive Import](archive-import.md) · [Search and Web API](search-and-web-api.md)

`ArchiveStore` owns database access, schema upgrades, and record merging. It uses
SQLite schema version 7 with FTS5 search. Collection memberships, shared tweet
content, and raw captures have distinct roles within the same archive table.
The optional `legacy-migration` extra provides LanceDB for the legacy importer;
PyArrow is installed transitively with it.

## Connection and schema lifecycle

Opening the store:

- creates the parent directory only when `create=True`;
- opens SQLite with `check_same_thread=False` and `sqlite3.Row` rows;
- enables WAL mode;
- requests negative `cache_size` in KiB and configured `mmap_size`;
- checks `PRAGMA user_version` and migrates when required.

A current v7 database takes the cheap version path; it does not run integrity
checks or derived-index repair on every open.

The single wide table is:

```text
archive(row_key TEXT PRIMARY KEY, record_type TEXT, ...typed nullable fields...)
```

Most columns are `TEXT` to support heterogeneous records. Indexed numeric fields
include `created_at_ts`, retry count, and retry eligibility. Every record builder
initializes the complete schema shape before an UPSERT that preserves the rowid.
Known enrichment status updates write only their changed fields.

## Record types and keys

| Record type | Stable key shape | Responsibility |
|---|---|---|
| `tweet` | `tweet:<collection>:<folder>:<tweet-id>` | Collection membership and searchable captured tweet fields |
| `raw_capture` | UUID or deterministic capture hash | Source payload, operation/cursors/status/time |
| `sync_state` | `sync_state:<collection>:<folder>` | Head/backfill checkpoint |
| `metadata` | `metadata:<key>` | Owner and application metadata |
| `import_manifest` | `import_manifest:<archive-digest>` | Archive-import identity/status/counts |
| `tweet_object` | `tweet_object:<tweet-id>` | Canonical global tweet/detail/availability |
| `tweet_relation` | `tweet_relation:<source>:<type>:<target>` | Reply/quote/retweet/thread/linked graph |
| `media` | `media:<tweet-id>:<media-key>` | Media URL/type/local download state |
| `url` | `url:<canonical-hash>` | Canonical URL/unfurl state |
| `url_ref` | `url_ref:<tweet-id>:<position>` | Tweet-to-URL attachment |
| `article` | `article:<tweet-id>` | Article preview/body/media metadata |
| `media_tag` | `media_tag:<tweet-id>` | Manual/AI tags and optional description for text or media |

`SECONDARY_RECORD_TYPES` is narrower than the full table discriminator list and
is used for graph merge/hydration paths.

## Membership versus canonical object

A `tweet` row represents why/how a tweet is in the archive and can exist once per
collection/folder. `tweet_object` represents the best known account-global
content and lifecycle. This supports:

- membership deduplication in view/export/search;
- one canonical quote/thread object shared by wrappers;
- sparse likes from official archive import;
- independent unavailable/retry state;
- source-aware merge without destroying collection provenance.

## Contentless FTS5

`archive_fts` is created with `content=''` and columns:

- `author_username`
- `author_display_name`
- `text`
- `note_tweet_text`

Insert/update/delete triggers index only `record_type='tweet'`. Secondary objects
never become standalone FTS rows. An update removes/reinserts indexed text only
when the record type or searchable fields change. Connections enable recursive
triggers so an explicit replacement also removes the old FTS document. Normal
merge UPSERTs preserve rowids and avoid replacement deletes entirely.

FTS is a derived index. The v3 schema-migration rebuild validates its count
against tweet membership rows; the public `rebuild_search_index()` path rebuilds
without that explicit count assertion. `ensure_fts_index()` and
`ensure_scalar_indexes()` are currently no-ops, despite Web startup calling
them; creation/migration is expected to have built the structures.

Scalar/partial indexes cover membership deduplication/counts, normalized author
equality, profile aggregation, tweet IDs, relation targets, collection/date/sort
pagination, attachment presence, tags, enrichment eligibility/due dates, and raw
capture targets.

## Atomic page persistence

Direct `upsert_tweet()`/`upsert_membership()` require an active page buffer.
`persist_page()` combines:

- raw capture;
- membership rows;
- extracted canonical/secondary graph;
- sync state.

One merge transaction applies the buffer. Failure injection tests assert that
neither partial rows nor cursor survive. This is the strongest atomicity
guarantee; a full run/import is intentionally checkpointed across transactions.

## Merge and source precedence

Rows are merged as complete shapes. General rules:

- nonempty incoming values win within equivalent provenance;
- live GraphQL data wins over archive-import data;
- archive values fill nulls but do not downgrade richer live fields;
- minimum media positions and coalesced URL/article fields preserve stable
  attachment identity;
- live content clears obsolete archive-unavailable scheduler state and can mark
  a `resurrected` object.

Source labels currently include `live_graphql` and `x_archive`. New ingestion
paths must define precedence explicitly and add regression tests rather than
depending on row order.

## Raw captures

Raw response JSON is stored with operation, cursor input/output, HTTP status,
capture time, source, and target context. Live keys use UUIDs; archive-import
keys use deterministic SHA-256 inputs so a failed/rerun import does not duplicate
captures.

Raw captures are internal evidence, not included as independent records in JSON
export. Membership `raw_json` is included in exported hydrated tweets by default.

`rehydrate_from_raw_json()` reads membership rows plus TweetDetail/
ThreadExpandDetail captures to rebuild normalized/secondary state. It assumes
parseable JSON objects, preserves values absent from raw input, and does not
explicitly delete all stale secondary rows.

## Enrichment queues

Canonical object states include pending, done, resurrected, transient failure,
and terminal unavailable. Indexed fields track reason, checked/first-unavailable/
next-retry times, count, eligibility, and detail.

Eligible initial enrichment selects pending plus due transient failures. Normal
resurrection selection further categorizes permanent versus retryable reasons.
Available states clear retry scheduling.

## Search/hydration/export

Storage exposes candidate ID queries, membership paging, secondary joins, and
`fetch_tweets_by_ids()` hydration. Hydrated rows can include collection metadata,
canonical author/text, media, URLs, articles, tags, quote-target tags, and raw
JSON.

`export_rows()` deduplicates IDs across memberships, sorts by parsed timestamp
and sort index, and fetches secondary state only for the selected IDs when a
limit exists. Full JSON export materializes all rows in memory.

## Tags

Tag writes normalize whitespace, case-insensitively deduplicate while preserving
first spelling, and delete empty records. Global delete/merge is case-insensitive
and avoids destroying malformed legacy JSON. Candidate selection treats only a
valid nonempty successful tag record as complete.

The name `media_tag` is historical; text-only generated/manual tags use it too.

## Schema migration

Open behavior by source version:

| Source | Migration |
|---|---|
| no archive table | create latest table, FTS, indexes, set v7 |
| v3–v5 | transactionally rebuild derived FTS/triggers/indexes, removing old replacement orphans; validate membership count, no backup |
| v6 | add partial membership/author/profile indexes and planner statistics; no FTS rebuild or backup |
| older/unknown legacy | quick-check, validated SQLite backup, add fields, backfill timestamps/scheduler, rebuild, targeted legacy-terminal repair, validate, set v7 |
| > v7 | reject as newer than supported |

Legacy backup naming is `archive.db.pre-schema-v7.<UTC>.bak`, with collision
suffixes. SQLite's backup API writes a temporary file, quick-checks it, then
atomically renames it.

Opening a mismatched database through `open_archive_store()` acquires
`sync.lock`. A current-version read open does not.

## Legacy LanceDB migration

`storage/migrate.py` looks for `<data>/archive.lancedb`, creates/opens SQLite v7,
and reads source ranges in isolated subprocess workers. Source failures split
ranges recursively so a bad row can be isolated; destination/worker-launch
failures abort. Destination inserts use `INSERT OR IGNORE`, enabling reruns that
preserve newer SQLite rows. FTS is rebuilt afterward.

The compatibility column list omits newer fields including several enrichment
scheduler/detail fields. `created_at_ts` is backfilled; omitted lifecycle fields
remain defaults. Partial migration keeps the source and can skip isolated bad
rows. The CLI does not reliably encode every result status in its exit code.

## Integrity and maintenance

- `check_integrity(full=False)` uses `quick_check`; full uses
  `integrity_check`.
- `optimize()` compacts with `VACUUM`, runs `PRAGMA optimize=0x10002`, and merges
  FTS segments. Writer connection close runs lightweight `PRAGMA optimize`;
  read-only/current-schema sessions skip that maintenance.
- `version_count()` is a compatibility method hard-coded to 1.
- `counts()['collections']` is membership-row count, not distinct collection
  names or unique tweet count.

Do not infer active storage architecture from old Lance-specific method names or
dependencies.

## Concurrency

The connection is shared across Web request threads with
`check_same_thread=False`. Stats report assembly has an in-process `RLock`, but
ordinary tweet/tag routes do not use that stats lock. Cross-process writer safety
comes from job/archive locks plus SQLite transactions.

WAL sidecars matter for live backup. Stop active Web/workers before copying the
data directory.

## Regression targets

- `tests/test_storage.py`
- `tests/test_storage_sqlite.py`
- `tests/test_migrate.py`
- `tests/test_search.py`
- `tests/test_export.py`
- `tests/test_stats.py`
- import/media/tag/Web route tests that depend on merge/hydration
