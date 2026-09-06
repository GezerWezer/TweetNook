# Automated Tagging

[Development guide](README.md) · [Architecture](architecture.md) ·
[Configuration and Paths](configuration-and-paths.md) ·
[User automated-tagging guide](../automated-tagging.md)

Tagging selects eligible archive records, prepares text or media requests for
Gemini, and saves validated results with local usage accounting. Free and Paid
modes have separate request paths. Core CLI and web startup must also work when
the optional Gemini and Pillow dependencies are absent.

## Optional dependency boundary

The `automated-tagging` extra provides `google-genai` and Pillow.
`automated_tagging.py` probes both imports lazily. CLI, sync, Web server, and tag
route modules are tested under simulated missing dependencies.

Missing support returns a warning/no-op for the sync follow-up rather than
failing core capture. Web settings reports installation availability and route
updates can return 404 when the extra is absent.

Do not add provider imports to package/module import paths that core startup
always traverses.

## Configuration validation

`TaggingConfig` owns enable state, key, Free/Paid mode, model, thinking, Search,
Free batch/RPM/RPD, paid tier/spend, Search reserve, context/instructions, and
media ceiling.

Enabled Paid mode requires a positive daily
limit unless `unlimited_spend` is true.

Context normalization accepts list or comma/newline string, trims, removes empty
items, and case-insensitively deduplicates. Additional instructions trim to
`None` when blank.

## Model discovery

The live catalog filters out image-generation/Nano Banana-style families and
requires advertised `generateContent`. It does not verify:

- structured output;
- Interactions API;
- thinking configuration;
- text/media modality details;
- Google Search;
- Standard/Flex tier compatibility.

The UI/CLI must treat a returned model as a candidate, not a capability
guarantee. Provider failure remains a normal execution outcome.

## Candidate selection

Storage selection:

- includes saved membership tweets and directly quoted originals;
- requires an available canonical enrichment state for both kinds of work;
- excludes valid, nonempty successful `media_tag` rows;
- leaves pending/failed/malformed/empty tag rows eligible;
- classifies a candidate as media if direct or quote-target media exists;
- returns one newest-first homogeneous text or media batch.

An explicit numeric/status-URL target bypasses the queue. Target validation
happens before acquiring the archive lock, but execution still requires enabled
tagging, an API key, optional dependencies, a stored tweet object, and loadable
local media for a media request.

## Prompt policy

`tagging_prompts.py` has separate text/media instructions.

Media output:

- concise visual description;
- prominent visible-text transcription;
- identifiable entity/source/parent-work context;
- 2–5 specific tags.

Text output contains 2–5 specific tags and no description. Both discourage
generic platform/meta labels.

At most the 50 most-used existing tags are contextual hints. User
context/additional instructions are explicitly weak and cannot override
evidence, schema, isolation instructions, or tag count. Search prompt language
appears only when Search is truly enabled.

Free batches include per-item isolation language intended to prevent content
from crossing between items inside one model call; model isolation is not a
guarantee. Prompt builders omit empty optional sections.

## Media preparation

Media input comes only from existing `local_path` files and is bounded by the
configured per-item byte limit. Free photos are loaded through Pillow and sent
inline; Free videos/GIFs and every Paid media item are uploaded through Gemini
Files. Uploaded files are deleted during cleanup on a best-effort basis. Free
batch text includes archive tweet IDs so returned rows can be mapped. If cleanup
fails, uploads remain subject to the provider's
[Files API retention and quota rules](https://ai.google.dev/gemini-api/docs/files).

Never log raw API keys. `_safe_error` masks the configured key in selected
provider errors, but it is not a universal scrubber for media content, prompts,
URLs, or other secrets.

## Free execution

Free mode uses `generate_content`:

- one homogeneous request can contain 1–20 candidates;
- structured JSON response schema;
- thinking budgets: none 0, low 1024, medium 4096, high 8192;
- Google Search forcibly disabled;
- a per-model in-process rolling RPM queue;
- persistent per-model RPD reservations.

Transient 429/503/resource-exhausted failures retry with 15/30/60/120-second
delays. An invalid-argument/400 multi-item batch recursively splits. A failing
single item is marked failed rather than saved as success.

### RPD reservation

`rpd.py` uses the America/Los_Angeles calendar day and archive metadata. A
reservation uses `BEGIN IMMEDIATE`, checks current per-model/day count, increments,
and commits before provider dispatch.

Reservations are conservative and never refunded after process interruption or
failure. Retries and recursive splits reserve separately. This protects against
overshoot across processes but can underutilize actual provider quota.

## Paid execution

Paid mode uses one Interactions request per tweet. `PAID_CONCURRENCY = 1`, so
only one tweet is processed at a time.

Requests specify:

- model;
- thinking level;
- Standard/Flex service tier;
- structured JSON schema;
- `store=false`;
- optional Google Search tool.

Transient 429/503/resource/timeout/unavailable/deadline failures retry with
exponential delays. Attempts share one logical request ID but are separately
accounted.

Finite-budget preflight/execution stops when:

- the daily estimate limit is reached;
- pricing required for a finite decision is unavailable;
- known minimum input cost exceeds remaining budget;
- a dispatched request returns unknown billing cost.

Unlimited mode can continue through unknown/unpriced usage and therefore must
remain an explicit opt-in.

Budget preflight and after-response ledger append are separate operations. CLI
and worker jobs normally serialize through process locks, but the inline Web
Test route, another Web process, or direct library calls can overlap, pass the
same preflight, and overspend. The configured amount is a best-effort local
safety guard rather than a hard provider cap.

## Search accounting

Paid Search uses a local 5,000-query monthly allowance and configured safety
reserve. Each in-flight request reserves five queries as a safety upper bound;
that is not claimed actual usage.

Final Search count prefers provider `grounding_tool_count`, then ordered
interaction-step queries. Missing both is unknown. Known attempt usage is
recorded independently across retry; unknown usage consumes the reservation and
disables Search for the rest of the run.

This is per local data-directory ledger only, not a provider project-wide quota.
`_SearchPolicy` uses an `asyncio.Lock`, so reservation coordination is
in-process only; separate processes can exceed the local allowance. Free RPD
uses a SQLite `BEGIN IMMEDIATE` reservation and has the stronger cross-process
property.

## Accounting model

Each usage row can include:

- request/run/attempt identifiers;
- model, API mode, tier;
- text/media candidate counts;
- input/cached/output/thinking tokens;
- Search count/source/query list;
- success/failure/billing-unknown status;
- latency;
- pricing snapshot and cost estimate;
- budget/search preflight metadata.

Tweet IDs make the ledger archive-identifying metadata, not merely anonymous
billing telemetry. Backup and permission guidance must treat it as sensitive.

Free success normally records provider usage metadata with cost zero. If that
metadata is absent or ledger conversion/writing fails, the caller warns and
continues parsing the response without appending a usage row for that request.
Paid success is billing-known only when required token categories are present;
explicit zeros are valid.

Definite pre-provider failure records zero. Ambiguous after-dispatch failure uses
unknown tokens/cost. Known usage without known pricing is distinguished from
unknown billing.

## Ledger durability

`gemini_accounting.py` uses monthly JSONL plus an atomic summary under
`activity/ai-usage`, guarded by a file lock. It can rebuild a corrupt/missing
summary from ledger rows.

Pricing comes from a cached OpenRouter metadata snapshot only; inference never
goes there. Cache freshness is 24 hours. A failed refresh retains stale data and
throttles refresh attempts. Standard component rates are applied to token
categories; Flex uses a fixed `0.5` multiplier. Local estimates exclude Search
or other tool fees and account/subscription charges. They are not Google
billing.

Paid daily spend and accounting summaries use UTC calendar days. The Free RPD
reservation boundary is separately based on `America/Los_Angeles`.

## Tag persistence and previews

Normal success writes a `media_tag` row even for text-only candidates. Storage
normalizes/deduplicates tags and removes a row only when both tags/description
are empty.

CLI `--test` and Web Test Run set dry-run/no-write behavior for tags and Web
settings. They still execute provider requests, RPD/RPM, retries, Search, and
accounting. CLI test uses `locked_archive_job`; the Web Test route calls
`tag_media_tweets()` directly on the shared store without `ProcessLock` or
`sync.lock`. It must not overlap another writer/tagger, and accounting/budget
preflight can race. Tests must assert both no archive tag mutation and real
mocked provider/accounting behavior.

## Web API

- GET settings/accounting masks the key.
- PUT validates only known TaggingConfig fields and preserves a mask.
- tweet picker accepts exact ID/status URL or shared search (up to eight).
- test validates a 1–30 digit ID and unsaved copied config.
- models endpoint returns live compatible-looking metadata or 502 on provider
  catalog failure.

## Regression targets

- `tests/test_optional_tagging.py`
- `tests/test_tagging_prompts.py`
- `tests/test_tagging.py`
- `tests/test_tag_cli.py`
- `tests/test_media_tags.py`
- `tests/test_gemini_foundation.py`
- `tests/test_rpd.py`
- `tests/web/test_automated_tagging_routes.py`

All provider tests should mock network and use synthetic media/content.
