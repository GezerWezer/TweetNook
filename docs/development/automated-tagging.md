# Automated tagging

The tagger is optional. `automated_tagging.py` checks for the SDK/Pillow dependencies and discovers usable model metadata. `tagging.py` plans requests, loads evidence, calls Gemini and persists descriptions/tags. Prompts live in `tagging_prompts.py`.

## Evidence and output

Text-only and media-bearing posts use different request shapes. Media evidence can include quoted content and local saved assets. Descriptions and tags must be grounded in the supplied evidence; tagging context is a weak hint, and user instructions remain below the built-in isolation/output rules.

Validate responses before saving them. Tagging state belongs to the post being tagged and must not create collection membership. Manual tag editing is available independently of the optional SDK and shouldn't be blocked by provider failures.

## Free requests

Free mode batches homogeneous kinds of posts, up to twenty per request. The configured batch size is a post count; `--limit` remains a request count. Free RPM and RPD are local admission controls, with per-model Pacific calendar-day accounting. They don't describe the actual quota of the user's Google project.

Google Search is disabled in Free mode. A request can contain several posts but still produces separately validated output for each target. Don't count a rejected response as successful completion of every batch item.

## Paid requests

Paid mode uses one post per request and supports Standard/Flex processing. A daily spend limit or explicit unlimited-spend choice is required when tagging is enabled in Paid mode. Internal processing waves don't change the user-visible one-post request unit.

Model discovery filters metadata for Gemini models capable of content generation and excludes image-generation families. It doesn't validate every model's support for thinking, structured output, media, Search or processing tiers. Preserve a useful validation/provider error rather than promising capabilities from the catalog alone.

## Usage accounting

`gemini_accounting.py`, `gemini_pricing.py`, and `rpd.py` maintain reservations, observed usage and local allowance state. Reserve before a provider attempt, commit known usage afterward, and mark uncertain usage explicitly. Every retry has its own lifecycle; double-releasing a reservation can permit overspending.

Paid requests with missing usage follow `unknown_billing_policy`: reserve an estimate and continue within limits, or stop. The minimum reserve and maximum uncertain requests per UTC day bound that policy. Actual charges can differ from estimates, and requests made outside this installation aren't visible here.

Search accounting has a separate policy and monthly rollover. An uncertain Search request reserves an estimated five queries; five is a local assumption, not a provider limit. The safety reserve and uncertainty ceiling can turn Search off for the rest of a run while allowing tagging to continue.

## Retries

Explicit provider overloads and uncertain billing failures need different handling. Keep rate limits, retry delays, consecutive-failure guards and usage reservations together. SDK-internal retries are constrained where necessary so they don't bypass accounting for each attempt.

Persist retry state for unfinished posts so the next eligible run can continue. Storage-full failures must fail the run accurately. A test preview performs real requests and accounting but doesn't save generated tags/descriptions or unsaved settings.

## Web controls

The settings API masks stored secrets, validates Paid controls, and keeps the previous key when the masked placeholder is submitted unchanged. Test runs use the submitted form settings, including unsaved edits. The model-refresh endpoint uses the selected key without saving it simply because it was tested.

The [testing guide](testing.md) covers provider mocks, accounting and preview checks.

## Requests and media

Free uses structured `generate_content` requests, homogeneous batches and an in-process rolling RPM queue. Its RPD reservation uses SQLite `BEGIN IMMEDIATE` before dispatch for the per-model America/Los_Angeles day. Reservations are conservative and aren't refunded on failure or interruption. Retries and recursively split invalid-argument batches reserve separately.

Free photos are sent inline through Pillow; Free videos/GIFs and Paid media use Gemini Files uploads. Enforce the per-item local-file size limit and clean remote uploads best-effort. Paid uses sequential Interactions requests with `store=false`, schema, thinking level, tier and optional Search. Prompts ask for two to five tags; text-only output has no description. At most fifty common existing tags are hints.

Paid requests retry overloads with bounded backoff and stop after sustained failures. Structured capacity failures retain uncertainty reserves but are counted separately from generic billing-unknown failures. Strict billing policy and monetary limits still apply. Retry thresholds and classifications are defined in `tagging.py`.

Search uses a local 5,000-query monthly safety allowance and the configured reserve. Provider grounding counts are preferred, with interaction query steps as fallback. Missing usage follows the configured reserve/disable policy. Search coordination is in-process, unlike Free RPD's transactional reservation.

Monthly JSONL and the atomic ledger summary live under `activity/ai-usage` with file locking. Usage history distinguishes unknown prices from unknown billing, including after a summary rebuild. Pricing metadata is cached for 24 hours from OpenRouter, stale data can survive refresh failure, and Flex estimates apply a 0.5 rate multiplier. Estimates exclude Search/tool fees and account charges.

Budget preflight and ledger append aren't one atomic provider transaction. Inline web previews or direct concurrent callers can overlap a budget decision; local limits aren't a hard spending cap. Test routes bypass the long-job writer lock, so avoid running them alongside archive writers/taggers. Previews leave saved tags unchanged but record real usage.
