# Archive imports

`archive_import.py` ingests an official X ZIP or extracted directory. Local ingest and live completion are separate phases, which lets Setup load a large archive while the user configures the connection and collection choices.

## Archive identity

The input adapter locates the export's datasets, reads JavaScript-assigned JSON without executing it, and derives archive/account identity from the available manifest and account records. Validate owner compatibility before mixing imported data with an existing archive.

The content digest identifies a completed import. Manifests keep counts, warnings, start/completion timestamps and follow-up status. A sampled import has a sampled manifest, so it doesn't pretend the complete archive was ingested.

## Local import

1. Preserve source data as raw captures.
2. Adapt authored and deleted-tweet records into the common graph model.
3. Import likes as memberships, using placeholders when the export lacks full content.
4. Copy exported media into managed data paths and record local provenance.
5. Save manifest/count information and leave incomplete objects eligible for later detail completion.

The export doesn't supply the bookmark collection. Missing likes' content is expected input, not a reason to drop those memberships. Preserve existing richer live content when merging archive records.

## Files and limits

ZIP members are read directly and parent-path components are rejected. Extracted-directory reads can follow symlinks, and media destinations built from imported tweet IDs do not all check resolved containment. Use trusted exports and a symlink-free extracted tree; see [trust and security](architecture.md#trust-and-security). The importer records ownership of copied media so regeneration can remove its own files.

The web upload limit is 50 GiB and uploads are staged under `setup/archive.zip`. CLI inputs can be an on-disk ZIP or directory. Sample limits apply after full dataset load and separately to supported categories; they don't make archive inspection or parsing constant work.

## Live follow-up

A normal CLI import reconciles live authored/like collections and can complete sparse post details. `--no-enrich` disables automatic individual detail completion but does not suppress live reconciliation. A positive `--detail-lookups` requests a bounded detail pass even when automatic enrichment is disabled. `--offline` suppresses all X follow-up.

Setup uses `--defer-followup` to ingest, enrich and download media now, without live collection reconciliation. Enrichment can use FxTwitter without an account; unresolved rows remain pending for authenticated completion. The deferred CLI import includes media in the same job; ordinary CLI import copies exported files and leaves remote media downloads to a separate task. The coordinator waits for a successful import, completed Setup, verified connection and confirmed collections before launching `import enrich` as the first sync.

If import enrichment or media fails or stops after local ingest, the saved archive remains browsable and the result stays visible. Automatic first-sync intent is cancelled; the user resumes explicitly after resolving the problem.

## Resuming imports

`import enrich` takes a stable command-start set of eligible rows. With no limit it processes that set; a limit bounds only detail completion. Afterward it calls a backfill-enabled sync of `config.sync.collections` with no page limit to repair gaps. A bounded detail invocation can therefore still lead to substantial timeline work.

Interruption preserves completed rows and reports remaining incomplete work. Aborted or inaccessible detail results retain reasons and retry eligibility. Authentication and repeated-focal-absence errors stop the relevant work without turning transport failures into unavailable posts.

## Regeneration

`--regen` removes prior import-owned rows, manifests and managed imported files before reimporting. It preserves live-synced data. Keep the cleanup predicates and filesystem validation together; an import must never delete unrelated files merely because they share a directory.

## Input limits

The raw upload byte limit doesn't impose a complete decompressed-size, entry-count or memory budget. Sample mode still loads full datasets. Identity from the archive is checked against existing owner metadata before writes; a configured live `user_id` can be checked only later during reconciliation, which may be skipped. Those are distinct guards.

Manifests distinguish completed, sampled and failed local ingest from an interrupted/aborted live follow-up. Setup's imported-readiness predicate accepts sampled state, while duplicate/full-follow-up paths require completed state. Keep that distinction explicit when changing the state model.

Detail completion checkpoints progress and keeps per-post retry state. Authentication expiry, unrecoverable protocol errors, exhausted rate limits or repeated missing focal results stop the pass after prior work is saved. A missing focal result alone does not establish deletion.

The general authenticated activity-import route accepts server-readable ZIP/directory paths. It has the authority of the local administrator; the fixed Setup upload path doesn't reduce that separate route's trust boundary.
