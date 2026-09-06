# Archive Import

[Development guide](README.md) · [Storage](storage.md) ·
[Enrichment](enrichment.md) · [User import guide](../importing.md)

`archive_import.py` reads official Twitter/X ZIPs or extracted folders, records their
source identity, and merges tweets and media into the archive. Local import can
complete without authentication. Live timeline and detail lookups then fill gaps
when credentials are available.

## Input abstraction

Accepted inputs are:

- a ZIP;
- an extracted directory containing `data/manifest.js`;
- an extracted directory with root-level `manifest.js`.

Archive paths normalize into `data/`, and lexical parent components are
rejected. ZIP members are opened directly rather than passed to `extractall`,
which avoids a basic extraction-traversal class. This is not full hostile-input
containment: extracted-directory reads follow symlinks, and imported tweet IDs
are not constrained to digits before media destination construction. A crafted
parent-relative ID can escape `paths.data_dir`. Treat ZIPs and directories as
trusted, require a symlink-free extracted tree, and do not claim path safety
until IDs and resolved filesystem targets are contained in code.

The importer does not enforce a comprehensive compressed/uncompressed-size or
entry-count budget. Web upload limits only the raw ZIP bytes to 50 GiB and does
not make decompression bombs safe.

## JavaScript dataset parsing

Official files wrap JSON in `window.YTD.<dataset>.partN = ...`. Adapters strip
the assignment and normalize known dataset object shapes. The importer currently
uses account/profile identity, authored tweets, deleted authored tweets, likes,
headers/manifest, and matched tweet media.

There is no bookmark adapter, even if a future Twitter/X export adds such a dataset.
DMs, relationships, lists, and other files are ignored.

## Identity and owner guard

Official archives require an account ID and username.

Before writes, the importer reconciles:

- archive account identity;
- existing archive owner metadata.

Those two identities conflict-abort. The effective configured/authenticated
`user_id` is checked only by live reconciliation after bulk writes, and that
phase may be skipped when credentials/live work are unavailable. Verify the
account manually before importing into a new database.

## Digest and manifest

The idempotence digest covers relevant manifest, account/profile, authored,
deleted, like, header, and listed media files. It is based on content, so an
equivalent extracted directory and ZIP share identity.

`import_manifest:<digest>` records lifecycle, counts, warnings, errors, and
enrichment follow-up/abort outcome metadata.
Important outcomes include:

- `completed` — bulk import completed;
- `sampled` — a bounded diagnostic subset completed;
- `failed` — bulk phase interrupted/failed;
- completed bulk plus separate enrichment-interrupted/aborted metadata.

A completed identical digest short-circuits bulk parsing/writes while allowing
requested follow-up. Deterministic raw-capture keys make a failed rerun
idempotent.

There is a current semantic mismatch: `has_completed_archive_import()` treats
`sampled` as imported for Web Setup readiness, while duplicate/full-enrichment
paths require `completed`. Tests/documentation should preserve this distinction
until the model is unified.

## Dataset preparation

### Authored tweets

Authored entries become:

- `tweet` membership in collection `tweet`;
- a normalized canonical/secondary graph;
- archive source/provenance;
- deterministic raw captures.

Deleted authored entries remain membership rows and seed terminal
`archive_deleted` lifecycle state.

### Likes

Likes are sparse and often lack full author/object detail. They receive
negative synthetic sort indexes in archive file order. A sparse canonical
placeholder is seeded only when no richer live object exists. Later live/detail
merge must not downgrade existing richer content.

### Media

Media matching expects official-style names prefixed by tweet ID and normalized
media URLs. Files are copied through temporary destinations into:

```text
<data>/media/<tweet-id>/<media-key>[-poster].<extension>
```

Video/GIF completion can require both main asset and poster. Existing
destinations may be reused without recomputing full hash/size fields, leaving a
legacy row apparently pending/incomplete despite a file.

Media destination construction uses unvalidated imported tweet IDs. Normal
official IDs are numeric, but a crafted parent-relative ID can escape the data
directory; this is a current security gap, not an enforced invariant.

## Bulk write/checkpoint model

The importer first preserves manifest/account/header/dataset raw captures, then
writes prepared normalized rows in chunks. This is not one transaction for the
whole archive. On interruption:

- committed captures/chunks remain;
- the manifest is marked failed;
- a rerun reuses deterministic records and merge rules.

This trades global rollback for bounded memory and resumability. Any future
change must preserve archive-versus-existing-owner validation before the first
archive-row mutation.

## Source-aware merge

Archive source fills missing fields but does not replace richer live GraphQL
data. Conversely, later live ingestion can upgrade sparse archive placeholders,
clear stale unavailable scheduler fields, and mark resurrection.

`--regen` invokes archive-owned cleanup before import:

- all archive-source raw captures and normalized rows, including tweet objects,
  relations, URLs/URL refs, and articles;
- tracked imported managed media;
- every import manifest.

Live-source rows remain. Because cleanup is source-wide rather than digest-only,
multiple prior archive digests are affected. The command is destructive and
must retain path containment and source-provenance tests.

## Sample mode

`--sample-limit N` still reads/hashes/parses the full input, then slices each
supported authored/deleted/like/media dataset. It writes a sampled manifest and
skips reconciliation/detail follow-up entirely.

This is intentionally a smoke-test mode, not a fast metadata-only preflight.

## Live reconciliation

After successful full bulk import, default behavior attempts live authored/like
timeline reconciliation. This can upgrade sparse objects and establish current
membership/detail. Missing credentials produce warnings and skip live work
without failing the local import.

`--no-enrich` disables the subsequent per-object TweetDetail pass but does not
turn off bulk live reconciliation. With enrichment enabled,
`--detail-lookups 0` means unbounded/all eligible. With `--no-enrich`, zero
disables detail lookups while a positive value explicitly enables that many.

## Detail enrichment

Eligible rows are captured into a stable command-start snapshot. Work uses
TweetDetail, adaptive pacing, detail-specific retries/backoff, and periodic
checkpoints (100 attempts).

Per-row transient conditions are persisted and the loop continues. Systemic
conditions abort:

- auth expiration;
- stale query-ID/feature drift that cannot recover;
- exhausted rate-limit policy;
- repeated focal absence.

Focal absence is transient/ambiguous and is not converted into deletion. Three
consecutive focal absences abort after flushing prior results.

An interrupted/aborted follow-up leaves the bulk manifest completed and records
the separate outcome, enabling `import enrich` continuation.

## Web boundary

Setup uploads a raw ZIP to fixed staged storage and starts the normal CLI
import. The general authenticated activity-import route can also accept an
arbitrary server-readable directory or `.zip` path. This is acceptable only
under the current local-admin trust model and should not be exposed to untrusted
Web users.

## Regression targets

- `tests/test_archive_import.py`
- `tests/web/test_setup_routes.py`
- import/help/exit coverage in `tests/test_cli.py`
- storage/source-precedence tests

Use generated, sanitized archives. Never commit a real account export.
