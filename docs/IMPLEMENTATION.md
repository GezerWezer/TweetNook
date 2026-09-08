# TweetNook — MVP Implementation Punchlist

This is the active implementation checklist. Update checkboxes as items complete. Prefer small, reviewable commits.

## Managed systemd update command (2026-09-07)

- [x] Add `sudo tweetnook update` with managed-unit validation and service-Python discovery.
- [x] Preflight pip and the installed version before gracefully stopping the service.
- [x] Upgrade with the service Python, restart on success or package-upgrade failure, and report the version.
- [x] Document the command and cover success, already-current, failure, ownership, preflight, and help behavior.

## GitHub Pages demo (2026-09-07)

- [x] Add a synthetic, schema-validated seven-user fixture with 22 root posts, mixed chained and
  standalone replies, text/media quote combinations, and prompt-aligned tags plus media descriptions;
  keep invented posters absent.
- [x] Make the demo All Tweets feed the union of authored, liked, and bookmarked records while
  retaining context-only saved replies inside conversation views.
- [x] Add three recent liked guide posts and give every liked record a text-only quote reply plus a
  plain text-only reply, without promoting those helper replies into feed collections.
- [x] Order Likes as the three guides, two-image gallery, standalone video, and four-image gallery;
  use every supplied non-avatar media asset in the visible feed with at most two uses per asset.
- [x] Include a synthetic community note on the standalone User Two source post and preserve it
  wherever that post appears directly or inside a quote.
- [x] Preserve feed, search/autocomplete, multi-author replies/threads/quotes, stats, schedule,
  automated tagging, and a trace-calibrated three-minute Sync pipeline without a FastAPI server.
- [x] Gate demo initialization, hide Setup/Configuration/Logs, explain those hidden panels in the
  Settings sidebar footer, add the accent-colored topbar marker, and preserve production behavior.
- [x] Build a deterministic subpath-safe Pages artifact with public-output safety checks.
- [x] Add focused adapter/build regressions and run the existing Web/search plus full validation suites.
- [x] Deploy Pages only after successful PyPI publication and add the concise README demo link.

## Bounded automated-tagging candidate selection (2026-09-06)

- [x] Replace full-population candidate materialization with index-ordered streaming selection.
- [x] Preserve saved and directly quoted eligibility, homogeneous text/media batches, retries,
  exclusions, deduplication, and global newest-first processing.
- [x] Add query-plan and early-stop regressions proving candidate discovery does not create
  archive-sized temporary B-trees or continue classifying after a batch is full.

## Settings checkbox scroll containment (2026-09-06)

- [x] Anchor visually hidden checkbox inputs inside their visible Automated Tagging and Schedule controls.
- [x] Audit every Settings checkbox and add browser-asset regression coverage preventing modal-shell scrolling.
- [x] Verify Google Search, Automated Tagging enablement, Schedule enable/randomization, and Configuration Advanced in a real browser.

## Raw JSON clipboard fallback (2026-09-06)

- [x] Fall back to the legacy textarea copy path when the Clipboard API is unavailable or rejects a `Copy raw` request.
- [x] Add browser-asset regression coverage for non-secure/HTTP-style pages; the harness passes 61/61.

## Dependency trim (2026-09-06)

- [x] Move LanceDB legacy migration support to the optional `legacy-migration` extra and remove the direct PyArrow dependency.
- [x] Remove unused Loguru setup and the uninvoked Mypy dev dependency.
- [x] Update migration/install guidance and validate the locked dependency graph.

## Application rename (2026-09-06)

- [x] Rename the Python package, distribution metadata, console command, and systemd identifiers to TweetNook.
- [x] Rename environment variables, XDG application directories, web labels, CLI output, tests, and active documentation.
- [x] Complete the full Python, browser-asset, lint, format, and packaging validation.
  - `.venv/bin/pytest -q`, `node tests/js/test_web_assets.cjs` (60/60), Ruff format/check,
    `git diff --check`, and `uv build --out-dir /private/tmp/tweetnook-dist` pass.

## Terminology pass (2026-09-06)

- [x] Standardize maintained user-facing and developer-facing copy on
  “Twitter/X” for the service and “tweet(s)” for archived content.
- [x] Update CLI/runtime messages, Web labels and empty states, setup guidance,
  statistics, and maintained user guides.
- [x] Preserve protocol/API identifiers, URLs, raw provider text, storage/search
  enum values, and historical worklog/checklist entries where they are compatibility data.
- [x] Run focused terminology regression checks after the source and asset updates:
  the full Python suite, 60 browser asset tests, Ruff check/format validation,
  and `git diff --check` all pass.

## Settings tab order (2026-09-06)

- [x] Reorder desktop and mobile Settings navigation and make Appearance the default page.
- [x] Rename Config to Configuration and preserve Setup selection for setup reruns.
- [x] Add browser-asset regression coverage for the order and default.

## Notification count badge repair (2026-09-06)

- [x] Anchor the Settings unread badge as a fixed circle at the button's upper-left corner.
- [x] Show the unread count beside Notifications in desktop and mobile Settings navigation.
- [x] Use the active theme accent/contrast colors for both count circles.
- [x] Run focused frontend validation and inspect the rendered badge and Settings navigation.
  - Both notification regressions pass; the full JS harness remains 56/59 on its three previously
    recorded feed-order failures. The other five Python Web asset tests pass.
  - Rendered geometry confirms a 22px circular FAB badge at (-4px, -4px) from the upper-left and a
    20px circular Settings count; both resolve to the active `#1d9bf0` accent in the default theme.

## X-style Setup modal (2026-09-06)

- [x] Give the active Setup flow a dedicated one-column modal instead of the Settings sidebar shell.
- [x] Restyle all three steps with a Setup header, progress bar, X-like typography, controls, and upload surface.
- [x] Keep Setup and Settings on independent state, top-level overlays, and dialog shells so neither can reshape or expose the other.
- [x] Keep Setup navigation as a Settings-native status page; launch the standalone flow only from explicit setup actions.
- [x] On mobile, replace the standalone setup controls with a clear instruction to complete setup on desktop.
- [x] Remove redundant setup status/activity UI and animate forward/backward step navigation horizontally.
- [x] Preserve forced-first-run locking, optional reruns, activity visibility, and the ordinary Settings layout.
- [x] Add frontend regression coverage and inspect desktop plus 390×844 mobile rendering.

## Server-first release cleanup (2026-09-06)

- [x] Update Web bind tests for the LAN default and resolved display URL.
- [x] Distinguish configured from coordinator-verified X credentials in Setup.
- [x] Persist deduplicated warnings when scheduled syncs are blocked by an active job.
- [x] Continue pending enrichment through the supervised Web action.
- [x] Move Notifications into Settings, badge the mobile-capable Settings FAB, and avoid Activity overlap.
- [x] Link Setup to the canonical getting-started guide and remove the bundled Markdown copy.
- [x] Complete targeted/full validation and rendered desktop/mobile inspection.
  - Focused layers and the full suite pass; date-independent tagging ledger assertions and current
    Likes/feed-menu expectations now match the active implementation.
  - Desktop and 390×844 mobile QA pass; Playwright smoke is unavailable because it is not installed.

## Server-first transition (2026-09-05)

- [x] Persist Setup and notices outside SQLite; coordinate offline import, auth, and enrichment across restarts.
- [x] Boot without an archive; attach storage and start scheduling only when ready; gate normal APIs.
- [x] Add initialization/password APIs and reuse Settings for forced first-run and optional reruns.
- [x] Stop managed jobs gracefully; expose foreground serve and explicit Linux systemd management.
- [x] Promote the owner's README/docs, preserving unrelated prose and developer documentation.
- [x] Validate lifecycle, orchestration, API, browser, shutdown, service, and packaging scenarios.
  - Real synthetic offline worker and live attachment pass; systemd commands are mocked on macOS.
  - Full suite: 950 passed; four known August-date tagging failures and one JS wrapper failure
    (three unrelated feed-picker/default-sort assertions). All new Setup/notice cases pass.
  - Wheel/sdist and Twine pass; browser steps show no JS errors; local documentation links resolve.
- Changes are intentionally uncommitted per the user's instruction; existing dirty changes are preserved.

## Like ordering (2026-09-05)

- [x] Review upstream issue #2, its repair gist and sequence-merging reference; reproduce archive imports overwriting live sort indices.
- [x] Preserve live order values and reconstruct a separate like order from retained GraphQL/archive captures, including multipart archives.
- [x] Add Recently liked / Earliest liked to the Likes web view and filtered search with stable pagination.
- [x] Add README credit and document the approximate archive fallback; 52 new cases, 53 frontend cases, repository Ruff and diff checks pass. The frontend and accounting expectations were later updated for the active Likes defaults and current-month ledger files.
- [x] Benchmark 25,000 synthetic likes: 0.081 s reconstruction, 0.018 s cached deep pagination after forcing indexed membership probes.
- [x] Validate the scoped patch independently of unrelated dirty work: 225 targeted tests pass against an isolated HEAD copy. Commit deferred because Git staging approval was declined.

## Remove Appearance tweet density (2026-09-05)

- [x] Remove the Tweet Density control and its browser-persisted state.
- [x] Update user guidance and validate the focused frontend tests and diff hygiene.

## Offline Web use (2026-09-05)

- [x] Bundle pinned Alpine/plugins and precompiled Tailwind with reproducible asset tooling.
- [x] Add a permission-based local-font picker with per-font previews; remove manual entry and descriptive subtext per user feedback.
- [x] Serve archived card imagery locally and avoid automatic Gemini model requests on page load.
- [x] Expose a full local-only archive import and label actions needing internet.
- [x] Verify 198 targeted Python tests (including 52 JS cases), lint/format, and wheel/sdist assets.
- [ ] Fresh-browser offline smoke: fixture harness added, execution permission declined. Full suite: 871 passed, four unrelated tagging tests fail on hardcoded August accounting dates.

## Detailed upstream comparison (2026-09-05)

- [x] Compare the old README with the shared upstream revision and current source.
- [x] Check upstream release metadata and distinguish inherited features from additions.
- [x] Expand the README notice with practical changes, removed features, and migration advice.
- [x] Record source evidence and validate Markdown and navigation; 14 focused existing tests pass.

## Self-contained README (2026-09-04)

- [x] Include the project description, features, pip installation, and complete first-run steps.
- [x] Cover everyday commands, search, tags, scheduling, optional tagging, and backups.
- [x] Credit upstream and describe the planned new repository and PyPI release.
- [x] Validate Markdown links, shell/TOML examples, command help, and package metadata.

## Documentation readability review (2026-09-04)

- [x] Review the new README and user/developer documentation against the checkout.
- [x] Rewrite user guides around tasks, explain technical terms, and retain complete references.
- [x] Improve developer navigation and source links; use GitHub-flavored Markdown throughout.
- [x] Correct contradictory setup, scheduler, dependency, tagging, and release guidance.
- [x] Validate focused tests, CLI help, TOML examples, and isolated first-run behavior.
- [x] Complete final Markdown/link/shell checks and review the documentation scope.

## Project constraints

Hard constraints:
- `docs/initial/` is historical and intentionally frozen (do not edit).
- No credentials (cookies/tokens/session files) in git, logs, or test fixtures.
- MVP is direct GraphQL API + query-id auto-discovery. No Playwright in MVP.
- Sync loop calls the GraphQL client directly — no premature `Fetcher` protocol abstraction.
- All architectural decisions are in `docs/PLAN.md`. If something isn't specified there, check before guessing.

Definition of done: passes `uv run ruff format --check`, `uv run ruff check`, and `uv run pytest`.

Follow-up: missing-post explanation text now renders above the skeleton bars on every tweet surface.

## Missing tweet skeletons (2026-08-19)

- [x] Replace direct missing, textless, tombstone, quoted, and repost-target placeholders with static tweet-shaped skeletons that retain the canonical explanation text.
- [x] Add browser-asset regressions for all terminal availability reasons, relation-only targets, tombstones, and textless records.
- [x] Validate the repository's actual `tweetnook/web/index.html` in a local browser against 14 fixture cases; manual approval pending.

## Schedule settings controls (2026-08-18)

- [x] Replace the Schedule status placeholder with Settings-native enable, cadence, interval,
  run-time, weekday, month-day, and IANA timezone controls.
- [x] Replace the native browser time field with a custom hour/minute/AM–PM control and add an
  default-on nonnegative random-delay window after the selected time for fixed-time schedules.
- [x] Load and save the complete validated schedule through the existing authenticated scheduler
  API, refresh the activity-drawer schedule, and distinguish saved from unsaved state.
- [x] Add browser/API regressions and update the Web UI guide.
- [x] Revalidate the custom picker and random-window follow-up with browser QA, the full Python
  suite, browser assets, repository Ruff, and diff hygiene.

## Automated Tagging spend-history analytics (2026-08-17)

- [x] Wire View spend history to a nested, accessible Analytics-style modal using theme colors.
- [x] Add 7D/30D/3M/1Y/Lifetime spend trends, summary metrics, per-model pricing, Search usage, and
  pricing-gap diagnostics.
- [x] Break out known-cost averages per tweet and request for Text only, Image, Video, GIF,
  Mixed media, and legacy/other media, alongside request/tweet/media-item counts.
- [x] Expose zero-filled 365-day UTC spend history and request totals from the existing accounting
  summary plus complete monthly Lifetime history without recalculating historical estimates.
- [x] Add accounting/API/browser-asset regressions and update the user guide.
- [x] Validate the full Python suite, browser assets, repository Ruff, and diff hygiene; omit live
  rendered QA per user direction.

## Automated Tagging test-run copy (2026-08-17)

- [x] Replace API-oriented Test run activity wording with user-facing save, quota, retry, Search,
  and paid-usage guidance.
- [x] Validate browser assets, Ruff, and diff hygiene without committing or staging the change.

## Automated Tagging test-run controls (2026-08-17)

- [x] Remove the “Preview only” badge and accent the no-save safety message.
- [x] Place the full-width Run test button below the safety message at the bottom of the controls.
- [x] Validate browser assets, Ruff, and diff hygiene without committing or staging the change.

## Automated Tagging subtitle placement (2026-08-17)

- [x] Move the Automated Tagging description from the settings header to the Enable automated
  tagging section and replace its prior subtext.
- [x] Validate browser assets, Ruff, and diff hygiene without committing or staging the change.

## Automated Tagging model description width (2026-08-17)

- [x] Place the model guidance text below the heading/Refresh row so it uses the full available
  settings width.
- [x] Validate browser assets, Ruff, and diff hygiene without committing or staging the change.

## Automated Tagging model refresh button label (2026-08-17)

- [x] Keep the Refresh models label on one line and aligned beside the Model heading.
- [x] Validate browser assets, Ruff, and diff hygiene without committing or staging the change.

## Automated Tagging model selector sizing (2026-08-16)

- [x] Keep the Refresh button width stable across its idle/loading labels and let the model copy
  use the remaining header width.
- [x] Make the live model selector fill the available settings content width.
- [x] Validate browser assets, Ruff, and diff hygiene without committing or staging the change.

## Automated Tagging live model catalog (2026-08-16)

- [x] Remove the hardcoded Gemini compatibility table and discover Gemini models from the live
  catalog, retaining only the `generateContent` requirement and generic image/Nano Banana
  exclusions.
- [x] Add model-capability guidance linking to Google's pricing/model documentation so users can
  verify that their selected model supports the configured tagging mode.
- [x] Add regression coverage for live models without a static allowlist and Nano Banana/image
  exclusions; leave the worktree uncommitted and unstaged.

## Automated Tagging model catalog options (2026-08-16)

- [x] Expand the existing compatibility registry so the live Gemini catalog can return multiple
  compatible model choices instead of only the default model.
- [x] Replace model cards with a native dropdown, remove token-limit details, cache catalog results
  with a forced Refresh models action, and exclude image-generation variants.
- [x] Add regression coverage and update model/test-run documentation; leave the worktree uncommitted
  and unstaged.

## Automated Tagging archived tweet preview (2026-08-16)

- [x] Replace the test tweet search picker with exact archived tweet ID lookup and a resolved tweet
  preview.
- [x] Validate browser assets, automated-tagging routes, Ruff, and diff hygiene without committing
  or staging the change.

## Settings background scroll lock (2026-08-16)

- [x] Disable list/page scrolling while Settings is open and restore the previous body overflow on
  close.
- [x] Validate browser assets, Ruff, and diff hygiene without committing or staging the change.

## Automated Tagging instruction rules (2026-08-16)

- [x] Replace the Advanced Additional User Instructions textarea with an Add input and removable
  rule boxes while preserving the existing saved configuration field.
- [x] Validate browser assets, Ruff, and diff hygiene without committing or staging the change.

## Automated Tagging spend control focus (2026-08-16)

- [x] Make the Spend amount/dropdown focus border continuous and add explicit chevron padding.
- [x] Validate browser assets, Ruff, and diff hygiene without committing or staging the change.

## Improved direct-Gemini automated tagging (2026-08-13)

- [x] Create `improved-gemini-tagging` from `feat/web-live-sync-indicator` without disturbing the
  overlapping uncommitted OpenRouter worktree.
- [x] Move Gemini/Pillow dependencies into `tweetnook[automated-tagging]` and verify core startup,
  sync, search, and manual tag behavior without the extra.
- [x] Simplify discovery and configuration to one fully compatible Gemini model and thinking level,
  while retaining separate text/media prompts and schemas.
- [x] Restore homogeneous Free batches of up to 20 through `generateContent`, configured RPM/RPD,
  split/retry behavior, Tweet Isolation, and no Search.
- [x] Move Paid one-tweet requests to the Interactions API with an internal sequential wave path and
  Standard/Flex service tiers.
- [x] Add a monthly JSONL AI request ledger, atomically replaced/rebuildable summary, cached
  OpenRouter pricing provenance, per-model totals, and estimated daily spend accounting.
- [x] Account Paid Search from Interactions grounding counts with explicit-step fallback, safe
  run-level disable when accounting is unknown, and a conservative 5,000-query cutoff.
- [x] Place a bounded established archive-tag vocabulary directly in prompts; remove custom
  function declarations, tool handling, and second Gemini turns.
- [x] Keep optional weak Tagging Context and add optional lower-priority Additional User
  Instructions, omitting both prompt fragments when empty.
- [x] Preserve media descriptions, structured validation, recoverable follow-up behavior, and
  operation-owned Gemini file cleanup.
- [x] Add a conditional Settings → Automated Tagging page and remove tagging from Config.
- [x] Add searchable single-tweet test runs using current unsaved settings without saving tags.
- [x] Match Tagging Context to the tag editor's removable-chip input without autocomplete; commit
  hints reliably from Enter, Tab, or comma.
- [x] Complete full Python/JS validation, package/lock metadata checks, documentation review, and
  final diff hygiene. Rendered localhost QA was attempted through the in-app browser but blocked by
  its local-navigation policy; static responsive markup and browser-asset behavior tests pass.
- [x] Refresh archive-tag snapshots between Free batches and Paid concurrency waves, retaining one
  immutable snapshot per wave.
- [x] Account Paid Search reservations per provider attempt, preserve known counts across retries,
  conservatively consume unknown attempts, and disable subsequent Search safely.
- [x] Parse Interactions `arguments.queries[]` Search steps with singular compatibility fallback.
- [x] Keep failed/pending automated-tag rows retryable while excluding valid completed rows from
  candidate selection and coverage statistics.
- [x] Keep Paid concurrency internal and fixed at one request while preserving the generic wave path.
- [x] Preflight current Paid input tokens for telemetry, append pricing/usage metadata for every
  provider attempt and retry, and calculate immutable estimated cost from actual Gemini usage.

## Pricing/cache/spend safety follow-up (2026-08-16)

- [x] Parse OpenRouter `input_cache_read` pricing with compatibility support for older cache fields;
  verify cached-token estimates remain non-zero.
- [x] Persist `refresh_attempted_at` separately from successful `fetched_at`, throttle failed
  refresh retries for 24 hours, and run blocking pricing lookups off the async tagging loop.
- [x] Persist daily unknown-billing counts, gate finite-budget Paid runs across process restarts,
  preserve unlimited/day-rollover behavior, and rebuild the state from JSONL.
- [x] Include the SDK-native Google Search tool in Paid token preflight when Search is enabled,
  keep preflight failure nonfatal, and remove the manual token estimator.
- [x] Run complete Python/JS validation and review the scoped diff: full pytest, all 40 browser/JS
  tests, repository Ruff format/check, and `git diff --check` passed.

## Final pricing/accounting correctness fixes (2026-08-16)

- [x] Cache every usable `google/*` pricing entry from each successful OpenRouter catalog refresh;
  replace the snapshot atomically and keep exact model lookup/unavailable behavior.
- [x] Keep pricing-unavailable success records distinct from provider billing uncertainty, with
  explicit status authoritative and only a narrow legacy fallback for status-less rows.
- [x] Validate Interaction usage by aggregate-field presence, preserving explicit zero values and
  classifying empty/incomplete success or retry responses as `billing_unknown`.
- [x] Keep billing-unknown JSONL records internally consistent with null actual cost/token fields;
  retain known zero usage only for definite pre-provider failures.
- [x] Validate full Python and browser/JS suites, Ruff, and diff hygiene; leave the worktree
  uncommitted per the user request.

## Final spend-accounting correctness fixes (2026-08-16)

- [x] Require explicit Interaction input, output, and thinking usage fields; keep zero valid and
  remove `total_tokens` as an input fallback in success and retry/error accounting.
- [x] Track successful-but-unpriced Paid requests in date-scoped `daily.unpriced_requests`,
  preserve the separate billing-unknown counter, gate finite-limit runs, and rebuild both states
  from JSONL.
- [x] Preserve a good stale OpenRouter snapshot when a refresh yields zero usable Google models,
  while retaining attempt throttling and full replacement for non-empty snapshots.
- [x] Run the full Python/browser validation and final diff hygiene review; leave the worktree
  uncommitted per the user request.

## Automated Tagging Appearance-style layout (2026-08-16)

- [x] Replace Automated Tagging native Model, Thinking, and Processing selectors with Appearance-style
  model choices and segmented controls while preserving existing bindings and dynamic model loading.
- [x] Rework the page into titled, spaced sections without large background/bordered section cards.
- [x] Match API mode selection to the same active/inactive selector treatment.
- [x] Add browser-asset regression coverage; validate the change without committing or staging it.

## Automated Tagging checkbox styling (2026-08-16)

- [x] Replace visible Automated Tagging native checkboxes with accessible custom controls using the
  supplied Twitter-style checkmark SVG while preserving Alpine bindings, focus treatment, and the
  disabled Google Search state.
- [x] Validate browser assets, Ruff, and diff hygiene without committing or staging the change.

## Automated Tagging checkbox outline cleanup (2026-08-16)

- [x] Remove the extra focus-ring and offset outlines from the custom checkboxes while retaining
  their checked fill/checkmark, unchecked border, and existing bindings.
- [x] Validate browser assets, Ruff, and diff hygiene without committing or staging the change.

## Automated Tagging spend history placeholder (2026-08-16)

- [x] Remove the spend breakdown cards, per-model summary, and estimate note beneath the Spend limit
  control.
- [x] Add a View spend history placeholder button.
- [x] Validate browser assets, Ruff, and diff hygiene without committing or staging the change.

## Settings sidebar and Flex help link (2026-08-16)

- [x] Make Settings sidebar hover feedback instant by removing transition timing from its buttons.
- [x] Remove the Flex discount label and add the requested accent Learn more link to Google’s Flex
  Inference documentation.
- [x] Validate browser assets, Ruff, and diff hygiene without committing or staging the change.

## Automated Tagging settings controls (2026-08-16)

- [x] Update the modal subtitle to describe automatic Gemini tagging and retain the manual-tag note;
  place Save with the content actions without reintroducing duplicate headers.
- [x] Make the Free/Paid selector full width with explanatory copy and an accent Learn more
  placeholder link; keep Model and Thinking on separate lines.
- [x] Add a Paid Spend limit section with a USD amount input and Day/Week/Month/Year dropdown;
  normalize the selected period to the existing stored daily limit without displaying that value.
- [x] Hide Unlimited spend for now while preserving existing backend compatibility.
- [x] Run the full Python suite, browser/JS suite, Ruff, and diff hygiene checks; leave the worktree
  uncommitted per the user request.

## Automated Tagging hierarchy and disabled state (2026-08-16)

- [x] Remove repeated Automated Tagging headers and keep a distinct Enable automated tagging
  section with Save available beside the enable control.
- [x] Dim and disable all remaining Automated Tagging settings while tagging is off, preserving
  the enable toggle and Save action.
- [x] Validate browser assets, Ruff, and diff hygiene without committing or staging the change.

## Required Web UI and live activity drawer (2026-08-11)

- [x] Move FastAPI and Uvicorn from the optional `web` extra into required dependencies.
- [x] Publish atomic shared-pipeline snapshots for sync, import, enrich, and other lifecycle jobs.
- [x] Add an authenticated activity API and detached normal-sync launcher with active-job guards.
- [x] Add a Twitter DM-style lower-left drawer with a collapsed running spinner and live details.
- [x] Add pipeline, route, markup, and deterministic browser-state regressions.
- [x] Render-check idle, expanded, live import/enrich, and collapsed-spinner states.
- [x] Pass complete repository validation.
- [x] Temporarily route Web Sync, Enrich, and Import actions through realistic, non-mutating
  pipeline simulations for drawer development.
- [x] Label simulated actions explicitly and add no-operation lifecycle and browser regressions.
- [ ] Commit the logical unit; staging approval was declined, so the validated changes remain
  intact and unstaged on `feat/web-live-sync-indicator`.

### Production jobs, schedules, and retained logs (2026-08-12)

- [x] Replace simulated Web actions with isolated production CLI worker processes.
- [x] Guard Web and scheduled launches against live external pipeline snapshots.
- [x] Add graceful process-group cancellation and terminal stopped history.
- [x] Persist structured snapshots, semantic events, readable logs, sanitized commands, and origin
  for manual CLI, Web, and scheduled pipeline runs.
- [x] Add bounded retained-run history/detail/log APIs and a functional Logs settings tab.
- [x] Add persisted every-N-hours, daily, weekly, and monthly scheduled sync calculation/execution.
- [x] Populate the existing drawer schedule fields from the built-in scheduler.
- [x] Add the requested placeholder Schedule settings tab without changing the drawer design.
- [x] Remove the temporary activity simulation/debug surface.
- [x] Render-check retained manual CLI logs, log browsing, disabled/enabled schedule states, and
  the Schedule placeholder without running a production X sync.
- [ ] Pass complete repository validation. Ruff, 853 Python tests, 36 browser-asset tests, offline
  lock validation, diff hygiene, and task-file formatting pass; the repository-wide format check
  still reports four pre-existing out-of-scope files.

### Serialized jobs and focused Web setup (2026-08-12)

- [x] Add a command-lifecycle lock spanning complete sync, import, enrich, and maintenance runs.
- [x] Make Web and scheduled supervisors consult the lifecycle lock as well as live snapshots.
- [x] Add Setup as the first Settings pane and remove authentication from generic Config controls.
- [x] Add focused session authentication editing and a real auth preflight button.
- [x] Stream a validated archive.zip into app-owned staging with guarded replacement and clearing.
- [x] Launch the staged archive through the real import-plus-enrichment worker and activity drawer.
- [x] Report persisted archive-import and enrichment readiness warnings in Setup.
- [x] Render-check the focused Setup pane and pass complete repository validation. Ruff lint, the
  complete Python suite, 37 browser-asset tests, offline lock validation, diff hygiene, and every
  task-file format check pass; the repository-wide format check retains four existing files.

### Explicit-only authentication (2026-08-12)

- [x] Remove automatic Firefox and Chromium-family browser/profile credential discovery.
- [x] Remove browser/profile config fields, environment mappings, CLI flags, and Setup controls.
- [x] Resolve credentials only from explicit Setup/config values or environment variables.
- [x] Remove `browser-cookie3` and its now-unused transitive dependencies from the lockfile.
- [x] Replace browser extraction coverage with explicit credential resolution regressions.
- [x] Update active architecture, setup, authentication, and CLI documentation.
- [x] Validate Setup credential candidates in isolation and save only successful replacements.
- [x] Pass complete repository validation: Ruff lint, 850 Python tests, 37 browser-asset tests,
  offline lock validation, diff hygiene, and formatting for every task file pass. The repository-wide
  format check retains three pre-existing out-of-scope files.

### Redesigned drawer wiring follow-up (2026-08-12)

- [x] Preserve the redesigned drawer markup, layout, transitions, and CSS.
- [x] Replace hard-coded last-run state with completed pipeline snapshots from the activity API.
- [x] Add cooperative Stop Task cancellation and retain the stopped terminal pipeline.
- [x] Report the truthful unconfigured scheduler state in the existing schedule fields.
- [x] Add completion timestamps and start/status/stop/cancellation regressions.
- [x] Render-check the redesigned start, progress, stop, and retained-result states.
- [ ] Pass complete repository validation. Ruff, 844 Python tests, 36 browser-asset tests, the
  offline lock check, diff hygiene, and formatting for every task file pass; the repository-wide
  format check remains blocked by four pre-existing out-of-scope files.

## Fixed sync-step stack reveal (2026-08-11)

- [x] Keep the active step at a fixed offset below the Run/Stop control during normal progress.
- [x] Move completed steps into an upward-growing stack beneath the control-layer fade.
- [x] Let manual native scrolling move the active/tail content in the browser's native direction
  while the completed stack is manually revealed.
- [x] Keep the issue/tail section in that same native scroll flow and bound the scroll range finitely.
- [x] Inset cards without adding unwanted side outlines; add deterministic browser/style coverage.
- [x] Pass focused Python and browser-asset validation plus rendered local QA.

## Natural sync-drawer scroll and mask follow-up (2026-08-11)

- [x] Restore the earlier card geometry and 20px horizontal content inset.
- [x] Remove the extra active-card transform so wheel scrolling follows the native direction.
- [x] Extend the background fade through the control/button layer and preserve the fixed active offset.
- [x] Verify the real authenticated `127.0.0.1:8000` server in-browser, including bounded manual scroll.

## Sync-drawer mask and stack direction follow-up (2026-08-11)

- [x] Anchor the fade to the control layer and end it at the active-card boundary.
- [x] Move the fade back with manual scrolling so it does not remain over the active area.
- [x] Keep the completed-stack shift isolated from the active card.
- [x] Validate the source-level and focused automated regressions without another browser test.

## Sync-drawer pull direction correction (2026-08-11)

- [x] Pull the entire completed stack downward when the user scrolls down.
- [x] Keep the fade receding upward so it clears the active-card area during the pull-out.
- [x] Preserve manual-only scrolling and the finite scroll range.

## Connected reverse sync-drawer scrolling (2026-08-11)

- [x] Apply manual movement to the shared track instead of only the completed stack.
- [x] Keep completed, active, pending, and issue content connected during scrolling.
- [x] Reverse the visible scroll direction for the entire connected track.

## Sync-drawer scroll architecture replacement (2026-08-11)

- [x] Separate the finite native-scroll canvas from the transformed visual scene.
- [x] Put completed, active, pending, and issue rows in one scene so they cannot drift apart.
- [x] Keep completed-card bounds inside the stationary canvas before applying the reveal transform.
- [x] Size the canvas to one viewport plus one stack height for a finite scroll endpoint.
- [x] Use a two-to-one scene shift to overcome native upward movement and produce net downward pull.
- [x] Validate source structure and static regressions without browser testing.

## Sync-drawer overscroll boundary (2026-08-11)

- [x] Prevent activity scroll input from chaining into the archive list at either endpoint.
- [x] Disable native overscroll bounce on the drawer and its scroll viewport.
- [x] Add static coverage without browser testing.

## Sync-drawer scrollbar and first-completion transition (2026-08-12)

- [x] Hide the activity scrollbar without disabling manual scrolling.
- [x] Start the active step at the top when no completed steps exist.
- [x] Enable the fade and animate the active step into its stacked offset after the first completion.
- [x] Validate source/static regressions without browser testing.

## Sync-drawer corrected pull direction and adaptive fade (2026-08-12)
- [x] Remove the artificial top scroll spacer from the activity viewport.
- [x] Reverse direct wheel input so wheel-up pulls the connected activity scene downward.
- [x] Match the completed-stack active offset to one compact 64px card so its lower half remains visible.
- [x] Balance completed-card vertical spacing by removing the oversized fixed-height bottom area.
- [x] Limit the finite reveal distance so the oldest completed card stops at the control divider.
- [x] Extend the completed-card fade slightly while preserving a visible lower section.
- [x] Align active and completed rows with the Web UI's Twitter-like typography and status styling.
- [x] Remove the pending-step collapsible menu from the activity drawer.
- [x] Move the Archive Sync title into the icon header and completion time beneath the schedule.
- [x] Keep collapsed activity hover backgrounds opaque.
- [x] Label the completion timestamp as the last sync and balance button spacing vertically.
- [x] Restyle the schedule and Run/Stop action area to match the Twitter-like Web UI system.
- [x] Keep Issues visible with an occurrence counter and independent bounded scrolling.
- [x] Restore native scroll direction after an activity completes.
- [x] Make fade travel and opacity respond to manual scroll distance.
- [x] Remove fade interpolation lag and isolate the horizontal divider above the fade layer.
- [x] Validate source/static regressions without browser testing.

## High-cardinality tag search performance (2026-08-11)

- [x] Reproduce count and page latency on a production-sized clone with 700 tagged posts.
- [x] Replace the global quote-relation scan with target-index probes driven by matching tags.
- [x] Add a covering partial media-tag index through an additive schema migration.
- [x] Add migration, exact-match, and query-plan regressions and pass complete validation.

## Search performance follow-up (2026-08-11)

- [x] Force selective partial or tweet-ID indexes for correlated attachment/state filters.
- [x] Push JSON-backed post-state, identity, engagement, and entity filters into SQLite.
- [x] Make thread classification null-safe on incomplete archived authors.
- [x] Use FTS5's native rank ordering and restrict the index to searchable tweet rows.
- [x] Rebuild the derived FTS index safely for existing schema-v3 archives.
- [x] Add plan, migration, trigger, semantics, and production-size performance regressions.
- [x] Update search/storage documentation and pass focused plus complete validation.

## Web statistics performance follow-up (2026-08-11)

- [x] Reproduce cold report latency against the configured vault and time each section.
- [x] Inspect production-sized row counts, planner statistics, query plans, and filesystem cost.
- [x] Identify the cached snapshot's all-or-nothing cold-load and shared-store refresh contention.
- [x] Identify loaded-state reactive snapshot churn and obscured autoplay media playback.
- [x] Poll refresh metadata without reapplying unchanged statistics object trees.
- [x] Suspend autoplay media while Analytics obscures the feed and restore it safely on close.
- [ ] Restore progressive first paint if the remaining 26-second cold-process report is still too
  slow; deferred from this loaded-state fix after the measured 81% collector improvement.
- [x] Rewrite tagging coverage, archive aggregation, and storage classification/payload reads.
- [x] Reduce refresh contention and prevent polling from creating loaded-state browser churn.
- [x] Add production-sized performance checks and pass focused plus complete validation.

## Instant Web statistics hover states (2026-08-10)

- [x] Remove transition timing from statistics cards, info icons, and status bar segments.
- [x] Preserve non-hover storage expansion animation and existing hover highlighting.
- [x] Add deterministic asset regressions and pass focused validation.

## Sparse configuration and SQLite defaults (2026-08-10)

- [x] Set fixed 512 MiB page-cache and 1 GiB mmap defaults in `DatabaseConfig`.
- [x] Make fresh and repaired configuration files contain the permanent auth skeleton only.
- [x] Normalize blank auth placeholders without changing the raw file representation.
- [x] Add validated sparse targeted writes, default removal, and explicit-field discovery.
- [x] Convert Web password CLI writes to targeted configuration updates.
- [x] Change the Web configuration API to `values`/`explicit` reads and `changes` writes.
- [x] Save only changed Web form fields and reset only eligible explicit overrides.
- [x] Update user documentation and pass focused plus complete validation.

## Search vocabulary and resurrection state follow-up (2026-08-10)

- [x] Preserve `resurrected` across later successful live sync/detail/thread writes.
- [x] Replace visible catch-all filter suggestions with distinct `has:` and `is:` groups.
- [x] Add `is:resurrected` search support backed by canonical tweet-object state.
- [x] Render valid uppercase `AND` / `OR` search operators as neutral capsules.
- [x] Keep autocomplete functional while entering quoted multi-word values.
- [x] Open Replying-to usernames in an anchored profile card instead of starting a search.
- [x] Pass focused and complete validation.

## Web unavailable-post placeholders (2026-08-10)

- [x] Stop excluding unavailable membership rows from shared search pagination.
- [x] Define one Web availability contract from canonical enrichment state and safe fallback data.
- [x] Normalize direct, quoted, retweeted, and relation-only posts through that contract.
- [x] Preserve missing ancestors, children, quote targets, and retweet targets as typed placeholders.
- [x] Prevent stale text, raw cards, media, and action counts from leaking into placeholders.
- [x] Keep text and raw-only media gaps visible with compact placeholders, and quote-target gaps
  inside the quote-post frame.
- [x] Refresh bounded thread-cache entries so newly resurrected posts become visible.
- [x] Add search, route, extractor, presentation, and browser-state regression coverage.

## Web tweet overflow and manual tag editing (2026-08-10)

- [x] Add a Twitter-style overflow menu to top-level list cards and main detail tweets.
- [x] Move tag access into the overflow menu and support a one-second press-and-hold shortcut.
- [x] Let the tag modal create tags for untagged tweets and add/edit descriptions.
- [x] Persist manual descriptions through the tag API/storage layer and refresh visible tweet state.
- [x] Align the dropdown over its trigger, remove outer vertical gaps, and make hover immediate.
- [x] Add route, storage, browser-state, and markup regressions; pass targeted validation.

## Cached Web statistics (2026-08-10)

- [x] Add a Web-only, stale-while-revalidate cache for complete statistics snapshots.
- [x] Deduplicate initial loads and background refreshes while retaining stale data on failure.
- [x] Load the statistics modal from one cached snapshot and add manual refresh plus data age.
- [x] Keep the page-level latest-sync lookup lightweight and keep CLI `stats` uncached.
- [x] Add backend, route, browser, documentation, and complete validation coverage.

## Tagging coverage statistics (2026-08-10)

- [x] Define coverage eligibility from direct membership, available enrichment, and media rows.
- [x] Count valid tagged posts only within that eligible population while retaining tagged posts
  in the denominator.
- [x] Force existing record-page and tweet-ID indexes throughout the coverage query.
- [x] Exclude thread-only, incomplete-enrichment, and text-only rows in functional regressions.
- [x] Pass focused and complete repository validation.

## Tagging queue performance (2026-08-09)

- [x] Remove the exact full-queue eligibility count added for pipeline progress.
- [x] Derive bounded progress from batch/RPD ceilings and discover unlimited queues per batch.
- [x] Force the existing tweet-ID index for candidate relation checks and batch hydration.
- [x] Preserve tagging eligibility, batching, runtime media validation, and empty-queue reporting.
- [x] Add no-full-count, deterministic-plan, batch-hydration, progress, and functional regressions.
- [x] Pass focused and complete repository validation.

## LanceDB migration recovery (2026-08-09)

- [x] Support the tested LanceDB 0.34 release line in project metadata and the lockfile.
- [x] Classify source-read/native failures separately from destination and worker-launch failures.
- [x] Retry unreadable chunks as progressively smaller ranges, skipping only irrecoverable rows.
- [x] Report partial migrations distinctly and retain the legacy archive recovery warning.
- [x] Add focused worker classification, adaptive recovery, fatal failure, and progress tests.

## Web/search performance (2026-08-09)

- [x] Push normalized media, link, and article filters into SQLite and bound mixed-text hydration.
- [x] Reduce tweet-detail query columns, JSON parsing, media scans, and quote-count work.
- [x] Split relation branches and force existing tweet/target ID indexes for detail hydration.
- [x] Benchmark the complete detail route read-only against the statistic-free local archive.
- [x] Share a bounded successful thread-response cache across normal and split-panel views.
- [x] Add search, detail, and browser regressions; pass focused and full local validation.
- [ ] Capture production before/after timings and query plans when `192.168.1.52` is reachable.

## Quoted-post thread expansion (2026-08-09)

- [x] Add stored `quote_of` relations to the existing membership-rooted thread traversal graph.
- [x] Apply `max_linked_depth` uniformly to quote relations and linked X status URLs.
- [x] Expand quoted originals even though their embedded tweet objects are already known locally.
- [x] Preserve command-start snapshots, expanded-target skipping, shared limits, and request pacing.
- [x] Include quote-derived work in thread progress and pending-related-status statistics.
- [x] Add mixed-edge depth, wrapper/original reply capture, rerun, and membership-dedup regressions.
- [x] Pass focused and complete repository validation.

## Numeric tagging batches (2026-08-09)

- [x] Require a positive size for `tweetnook tag --batch N`.
- [x] Interpret `tweetnook tag --limit N` as a limit on top-level batches rather than tweets.
- [x] Preserve configured batching for bare tagging and the automatic sync follow-up.
- [x] Update CLI/runner regressions and user-facing documentation; pass focused and full validation.

## Shared CLI/Web search (2026-08-09)

- [x] Extract the Web query parser, filters, search planning, sorting, availability rules, and
  pagination into one presentation-neutral `tweetnook.search` module.
- [x] Make ordinary clauses implicit AND requirements; accept standalone uppercase `AND`, group
  only adjacent alternatives with uppercase `OR`, and retain quoted and negated clauses.
- [x] Keep lowercase `and` / `or` searchable and reject malformed queries, unknown filters, and
  invalid structured values with a useful client/CLI error.
- [x] Make `/api/tweets` a thin adapter over shared post search while preserving Web-only quote
  media hydration and leaving AND/OR rendering and autocomplete unchanged.
- [x] Route default CLI post search through the same engine, retain explicit article-body search,
  and add `filter:articles` for posts with attached archived articles.
- [x] Add shared parser/engine, CLI/Web parity, route, article-filter, and browser regressions; pass
  focused and complete validation.

## Unified pipeline CLI (2026-08-02)

- [x] Audit sync, archive import/enrichment, follow-up workers, terminal output, and
  unattended execution paths.
- [x] Define a shared semantic reporter with a live TTY pipeline and plain bounded service logs.
- [x] Implement the shared renderer, command-lifecycle spinner, issue sidebar, and log throttling.
- [x] Declare flag-relevant steps up front, retain zero-work steps with specific skip reasons, and
  keep truthful progress totals, rates, and ETAs.
- [x] Integrate sync, archive import/enrichment, threads, resurrection, articles, media, URLs,
  and conditional tagging without nested progress output.
- [x] Update CLI documentation and add interactive, non-TTY, conditional-step, and regression tests.
- [x] Pass focused and full repository validation.

## Shared CLI/Web statistics (2026-08-09)

- [x] Define typed, presentation-neutral statistic values, tables, sections, and reports.
- [x] Move overview, collections, archive status, storage, and tagging collection into one ordered
  section registry outside the CLI and Web layers.
- [x] Build a responsive Rich bento grid for `tweetnook stats`, pairing archive/timeline and
  health/work tiles on wide terminals while stacking tiles on narrow terminals and keeping dense
  collection/storage tables full-width.
- [x] Pack unequal paired tiles into independent masonry columns instead of rigid rows, with clean
  edge-aligned metric labels and values inside each tile.
- [x] Add `stats --detailed` to swap simplified storage for all component segments and reveal
  zero-count maintenance queues and unavailable reasons, matching the Web controls' information.
- [x] Separate posters/thumbnails/supporting media from primary photo and video counts, expose the
  supporting-file segment in detailed mode, and omit the CLI tagging section when no tags exist.
- [x] Preserve the original Web statistics layout and per-section loading behavior exactly while
  sourcing every legacy endpoint from the shared collectors.
- [x] Keep a generic CLI fallback for future card/table/status sections; adding Web presentation
  remains an explicit small adapter so new data cannot silently change the browser design.
- [x] Retain the old Web endpoints as compatibility adapters and keep background enrichment-banner
  polling on a dedicated inexpensive count instead of collecting the complete report.
- [x] Add registry parity, serialization, API, CLI, storage, and browser regression coverage.

### Pipeline lifecycle follow-up (2026-08-08)

- [x] Restore command and completed-step elapsed times without restoring an overall ETA.
- [x] Bulk-skip previously expanded thread targets before remote metadata setup or live redraws.
- [x] Keep tagging active while its queue is selected, and remove tagging rate/ETA output.
- [x] Predeclare flag-relevant sync/import/standalone steps and line-mark empty queues with reasons.
- [x] Reduce spinner-column spacing and migrate legacy LanceDB progress to the shared reporter.
- [x] Compact unattended logs while preserving bounded counters, issues, retries, and timings.
- [x] Pass focused and full repository validation for the follow-up.

### Explicit Web server lifecycle (2026-08-08)

- [x] Replace wildcard bind hosts with reachable device addresses in displayed Web URLs.
- [x] Remove Web auto-start configuration and all sync/import restart hooks.
- [x] Add an explicit `tweetnook web restart` daemon command with preflight validation.
- [x] Pass focused and full repository validation for the Web lifecycle changes.

### JSON-only export surface (2026-08-08)

- [x] Remove the HTML exporter, CLI command, package export, tests, and active documentation.
- [x] Pass focused and full repository validation after removing HTML export.

## Web archive availability and storage clarity (2026-08-02)

- [x] Add an Archive overview card for missing imported tweets and their archive percentage.
- [x] Expose reason-specific unavailable counts, percentages, and retry state for future cards.
- [x] Remove Storage connector traces and move the segmented bar into the Total card.
- [x] Preserve the current Pipeline health presentation until its dedicated visual rework.
- [x] Pass Web route, browser-asset, full-suite, and rendered browser validation.

### Archive status presentation follow-up

- [x] Rename Pipeline health to Archive status.
- [x] Show enriched, expanded threads, missing enrichment, and resurrected summary cards.
- [x] Add an unavailable-tweet total, reason bar, and storage-style reason list.
- [x] Hide zero-count reasons by default with a Settings-style Show empty switch.
- [x] Pass browser-asset, full-suite, and rendered desktop/mobile validation.

## Nested tombstone matching and full enrichment snapshots (2026-08-01)

- [x] Match direct and nested `*-tweet-<id>` entry IDs with exact suffix boundaries.
- [x] Recognize an exact focal entry with live X's empty `tweet_results` sentinel as retryable
  unavailable while leaving nonempty malformed results absent.
- [x] Preserve explicit nonmatching `rest_id` precedence and structured tombstone details.
- [x] Keep the three-response focal-absence breaker and reset it for nested tombstones.
- [x] Remove the internal 500-row page and select one stable eligible snapshot per command.
- [x] Preserve explicit limits, deterministic SQL ordering, rate pacing, and 100-write flushes.
- [x] Split initial-enrichment transient status into due and delayed counts.
- [x] Update CLI help and README behavior documentation.
- [x] Pass focused parser/enrichment/status tests and full repository validation.

## Remove sync-stage database delays (2026-08-01)

- [x] Make current-schema database opens return after the schema-version check.
- [x] Keep quick/integrity checks, schema setup, indexes, and legacy repair off routine opens.
- [x] Create fresh databases directly at the latest schema without backups or historical stages.
- [x] Collapse existing legacy SQLite upgrades into one validated, additive direct-to-v3 path.
- [x] Finalize LanceDB imports explicitly without invoking legacy SQLite migration behavior.
- [x] Add `tweetnook db check [--full]` for user-invoked SQLite diagnostics.
- [x] Pass focused migration/sync/CLI tests and full repository validation.

## Archive enrichment and reason-aware resurrection (2026-08-01)

- [x] Add versioned, backed-up, additive SQLite migration for enrichment scheduler metadata.
- [x] Preserve rich tweet fields when recording unavailable results and repair recoverable legacy rows.
- [x] Preserve tombstone payloads and return structured focal TweetDetail results with normalized reasons.
- [x] Separate finite initial archive enrichment from recurring resurrection scheduling.
- [x] Make X archive import enrich automatically by default and make `import enrich` uncapped by default.
- [x] Add interruption-safe progress flushing, distinct queue counts, and persistent reminders.
- [x] Add a bounded, reason-weighted resurrection scheduler with same-author recovery boosts.
- [x] Remove initial archive enrichment from sync and update CLI flags/help/follow-up ordering.
- [x] Expose enrichment completeness in stats and the Web UI warning banner.
- [x] Add migration, persistence, parsing, enrichment, scheduler, CLI, sync, and Web UI tests.
- [x] Update README/PLAN/worklog and pass focused plus full validation.

### Post-implementation audit corrections

- [x] Require positive focal-ID association and cover unrelated/context tombstones.
- [x] Classify representative private and missing-account messages after text normalization.
- [x] Preserve and schedule structured thread tombstones without same-sync resurrection.
- [x] Abort on unexpected parser/storage errors while retaining completed buffered work.
- [x] Clear stale terminal metadata on live revival and share available-state consumers.
- [x] Keep transport failures separate from completed availability retry counts.
- [x] Add manual continuation interruption/abort reporting with conventional exit statuses.
- [x] Rank indexed legacy repair sources, report deferred deep recovery, and remove stdout output.
- [x] Pass audit-focused tests, full pytest, Ruff, compileall, diff, and browser validation.

### Final hardening pass

- [x] Classify focal TweetDetail results as available, explicitly unavailable, or absent.
- [x] Stop archive enrichment, resurrection, and thread expansion after three consecutive
  ambiguous focal absences without converting those rows into confirmed tombstones.
- [x] Preserve stronger unavailable reasons and keep same-author probes/boosts due across
  resurrection budget boundaries.
- [x] Apply ordering, exclusion, and optional limits in SQL and process uncapped enrichment from
  one stable eligible snapshot.
- [x] Advance SQLite to schema v3 with a validated atomic backup, sequential migration stages,
  unknown-reason preservation, scheduler normalization, ranked repair, and surfaced reporting.
- [x] Add bounded indexed tombstone repair to the automatic legacy-schema migration.
- [x] Finalize import follow-up status on success, interruption, and abort; flush writes even
  when HTTP client shutdown fails; preserve unavailable rows' `last_seen_at`.
- [x] Count both `done` and `resurrected` rows as Web UI “Enriched” and cover the final behavior
  with parser, worker, migration, SQL-bound, CLI, and Web regressions.

Planning note (2026-03-15):
- The sections below describe the completed SQLite-backed MVP.
- The SQLite -> LanceDB migration landed on 2026-03-15.
- Capture expansion landed after the LanceDB migration.
- The active next milestone is turning the X-archive import stub into a real importer using the fresh 2026-03-16 archive fixture.
- The review-cleanup checklist lower in this file is complete and retained as historical record.

## Comprehensive non-sync test audit (2026-07-30)

- [x] Restore the Web UI basic/advanced configuration split after the coverage pass
  accidentally classified every editable setting as always visible.
- [x] Replace Gemini RPD inference and request pacing with a persistent per-model hard cap
  that reserves every generation attempt against the Pacific quota day, including failures,
  retries, grounding fallbacks, and split batches.
- [x] Remove the sync follow-up's tag-count-derived quota precheck and disable SDK retries so
  centralized tagging accounting is authoritative and explicit exponential backoff is the
  only retry layer.
- [x] Create quota state lazily without migrating historical tag rows; existing installations
  intentionally start with fresh local usage state after upgrading.
- [x] Make bare `tweetnook tag` share the sync tagging loop, with a batch-count `--limit`,
  numeric batch-size override, model override, and explicit tweet ID/status-URL targeting.
- [x] Add one-tweet `tag --test` generation that prints validated tweet context, description,
  and normalized tags without changing media-tag state while still accounting for RPD usage.
- [x] Remove obsolete embedding/vector surfaces and their redundant tests.
- [x] Add focused configuration, SQLite storage, migration, media-tag, Gemini-tagging,
  tag-CLI, extractor, interactive-progress, and web-daemon coverage.
- [x] Add FastAPI route coverage for authentication, configuration, media, avatars,
  statistics, storage statistics, tags, tweet listings, threads, quotes, and authors.
- [x] Add deterministic JavaScript coverage for themes, autocomplete, application state,
  navigation, rendering, media, persistence, and HTML/URL escaping.
- [x] Add real-SQLite regressions for membership deduplication, tombstone pagination,
  production timestamp formats, sync-state statistics, article statuses, tag coverage,
  Boolean article search, legacy author schemas, and resurrection.
- [x] Execute the real migration worker in tests and cover transactional rollback/resource
  cleanup for multi-result media tagging.
- [x] Consolidate redundant progress/job tests and strengthen the archive enrichment batch
  test to assert its exact write batches.
- [x] Validate 568 pytest cases, 18 Node browser-asset cases, repository-wide non-sync Ruff
  lint, and a live FastAPI/Alpine browser smoke test.
- [x] Leave syncing implementation and sync-test logic unchanged and outside this audit.

---

## Task 0: SeekDB Spikes

Resolve the open questions from PLAN.md before building the storage layer. Timebox each spike to ~2 hours.

- [x] **Startup/footprint spike**
  - Measure cold-start time and RSS for: open DB, create schema, insert 1k rows, query 10 rows.
  - Decide on-disk location (XDG data dir) and file naming.
  - **Exit criteria**: if cold-start > 3s or RSS > 200MB for an empty DB, evaluate alternatives (SQLite fallback) and flag to the lead.
  - Record results in `WORKLOG.md`.
- [x] **Raw JSON storage spike**
  - Try storing realistic-sized JSON blobs (a full page capture ~50-200KB, and individual tweet blocks ~2-10KB).
  - **Exit criteria**: if insert/query perf is unacceptable or SeekDB rejects large TEXT fields, switch to gzipped JSON files on disk with `raw_json_path` + hash in DB. Update PLAN.md schema if changed.
  - Record decision in `WORKLOG.md`.
- [x] **API surface spike** (SQL tables vs Collection API)
  - Determine which SeekDB API to use for Phase 1: SQL-style tables or the Collection/document API.
  - **Exit criteria**: pick whichever supports upsert-by-key and basic queries without friction. Document choice in `WORKLOG.md` and update PLAN.md schema section.
  - Result: the original sandboxed spike failed initialization, but a fresh full-permission re-spike showed embedded SeekDB only works reliably on non-tmpfs paths and still misses the MVP startup/footprint threshold (~3.23s cold benchmark, ~1.0GB RSS). We shipped SQLite first, then replaced it with LanceDB in Task 10.

## Task 1: Project Bootstrap

- [x] Add `pyproject.toml` (hatchling backend) with:
  - Project metadata (`name=tweetnook`, `requires-python>=3.12`).
  - Runtime deps: `httpx`, `lancedb`, `pyarrow`, `pydantic>=2`, `typer`, `rich`, `loguru`.
  - Dev deps: `ruff`, `pytest`, `pytest-asyncio` (and `mypy` optional).
  - Console entrypoint: `tweetnook = tweetnook.cli:app`.
- [x] Add ruff configuration (format + lint) in `pyproject.toml`.
- [x] Add pytest configuration (asyncio mode, test discovery) in `pyproject.toml`.
- [x] Create package skeleton: `tweetnook/__init__.py`, `tweetnook/cli.py` (stub), `tests/`.
- [x] Verify: `uv sync && uv run tweetnook --help` works.

## Task 2: Config + Auth

Config and auth are tightly coupled — build them together.

- [x] Implement `tweetnook/config.py`
  - XDG dirs: config (`~/.config/tweetnook/`), data (`~/.local/share/tweetnook/`), cache (`~/.cache/tweetnook/`). Support `XDG_*_HOME` overrides. Auto-create on first access.
  - Central constants: API base URL (`https://x.com/i/api/graphql`), bearer token (see PLAN.md Auth section), user agent string, cache filenames.
- [x] Define Pydantic v2 config model(s):
  - Auth: optional `auth_token`, `ct0`, `user_id` overrides.
  - Sync: `page_delay` (default 2s), TweetDetail-specific `detail_delay` (default 0s floor, with live header-based pacing when available), `max_retries` (default 3), `backoff_base` (default 2s), TweetDetail-specific `detail_max_retries` (default 2) and `detail_backoff_base` (default 30s), `cooldown_threshold` (default 3), `cooldown_duration` (default 300s).
- [x] Implement config loading: read `config.toml` from XDG config dir (optional — tool works without it). Env var overrides with `TWEETNOOK_` prefix.
- [x] Implement `tweetnook/auth/cookies.py` — cookie resolution chain:
  - Priority: env vars (`TWEETNOOK_AUTH_TOKEN`, `TWEETNOOK_CT0`, `TWEETNOOK_USER_ID`) → config file → browser extraction.
  - Return a resolved auth bundle: `auth_token`, `ct0`, `user_id` (optional — only needed for Likes).
  - If nothing found: raise clear error with setup instructions.
- [x] Add archive-owner guardrails:
  - Persist local archive owner id in DB metadata on first successful sync.
  - Refuse later syncs if resolved owner id differs from stored owner id.
- [x] Implement `tweetnook/auth/firefox.py`
  - Discover candidate profiles from `profiles.ini`, rank install-default/default profiles first, and allow explicit path/name override via config/env when a different profile is needed.
  - Copy `cookies.sqlite` to a temp snapshot before reading; see Review item 8 below for the current sidecar-copy details used on live profiles.
  - Extract `auth_token`, `ct0`, `twid`.
  - Parse `twid` (`u%3D<numeric_id>`) into numeric user_id.
- [x] Extend auth extraction to Chromium-family browsers
  - Added `tweetnook/auth/chromium.py` using `browser-cookie3` for Chrome, Chromium, Brave, Edge, Opera, Opera GX, Vivaldi, and Arc.
  - Added generic `auth.browser`, `auth.browser_profile`, and `auth.browser_profile_path` config/env overrides.
  - Added `--browser`, `--profile`, and `--profile-path` flags to sync/auth-check commands plus `tweetnook auth check --interactive`.
- [x] Unit tests: cookie resolution chain (mock each source), Firefox extraction with synthetic sqlite fixture, Chromium profile discovery/extraction, twid parsing, interactive auth-check selection.

## Task 3: Query ID Discovery

- [x] Implement `tweetnook/query_ids/constants.py`
  - Discovery page URL(s).
  - Bundle URL regex pattern (`abs.twimg.com/responsive-web/client-web/*.js`).
  - Target operations: `Bookmarks`, `Likes` (Phase 1), plus `BookmarkFolderTimeline`, `TweetDetail`, `UserArticlesTweets` (reserved).
  - `FALLBACK_QUERY_IDS` dict — source current values from browser DevTools. Document date sourced.
- [x] Implement `tweetnook/query_ids/store.py`
  - Cache JSON file in XDG cache dir: `{fetched_at, ttl_seconds, ids}`.
  - `get(operation) -> str`: returns cached ID if fresh, else fallback.
  - `is_fresh() -> bool`: check `fetched_at + ttl_seconds > now`.
- [x] Implement `tweetnook/query_ids/scraper.py`
  - Fetch discovery page HTML, extract JS bundle URLs.
  - Fetch bundles, extract `(operationName, queryId)` pairs via regex.
  - Use multiple regex patterns (the format has varied over time).
  - Update cache on success.
- [x] Unit tests: bundle URL extraction, queryId regex extraction from synthetic JS snippets, cache TTL logic, fallback behavior.

## Task 4: GraphQL Client

- [x] Implement `tweetnook/client/base.py`
  - Build `httpx.AsyncClient` with cookie jar + required headers (see PLAN.md Auth section for full header list including bearer token).
  - Error classification: `is_rate_limit(resp)`, `is_auth_error(resp)`, `is_stale_query_id(resp)`, `is_feature_flag_error(resp)`.
  - Backoff engine: retry with exponential delay on 429, configurable via config model.
- [x] Implement `tweetnook/client/features.py`
  - `build_bookmarks_features() -> dict` and `build_likes_features() -> dict`.
  - Source initial flag sets from a browser DevTools capture. Keep per-operation (not shared), commit them as static code data for MVP, and document date sourced in code comments.
- [x] Implement `tweetnook/client/timelines.py`
  - `build_bookmarks_url(query_id, cursor=None) -> str` — variables: `{count: 20, ...}`.
  - `build_likes_url(query_id, user_id, cursor=None) -> str` — variables: `{userId, count: 20, ...}`.
  - `fetch_page(client, url) -> httpx.Response` with retry/backoff on 429 and refresh-once on 404.
  - Add a lightweight probe path (`count=1`) that reuses the same request builders but does **not** write captures or checkpoints.
  - `parse_timeline_response(data, operation) -> (tweets: list, cursor: str | None)` — extract tweet entries and bottom cursor. Per-operation parsing since response shapes differ.
- [x] Unit tests: URL building, cursor extraction for both Bookmarks and Likes response shapes (minimal JSON fixtures), `400/404/429` classification, backoff logic (httpx.MockTransport to simulate 429/404/200 sequences).

## Task 5: Storage (SQLite Fallback Backend)

Historical note: this was the shipped MVP backend after the SeekDB spike failed the runtime/footprint gate.

- [x] Implement `tweetnook/storage/seekdb.py`
  - Open/create embedded DB in XDG data dir.
  - Schema/collections per PLAN.md: `raw_captures`, `tweets`, `collections`, `sync_state`, `archive_metadata`.
  - Methods:
    - `append_raw_capture(operation, cursor_in, cursor_out, http_status, raw_json)`
    - `upsert_tweet(tweet_id, text, author_id, author_username, author_display_name, created_at, raw_json)`
    - `upsert_membership(tweet_id, collection_type, sort_index=None, folder_id=None)`
    - `get_sync_state(collection_type) -> SyncState`
    - `set_sync_state(collection_type, *, last_head_tweet_id=None, backfill_cursor=None, backfill_incomplete=False)`
    - `reset_sync_state(collection_type)` (for `--full`)
    - `has_membership(tweet_id, collection_type, folder_id=None) -> bool` (for collection-scoped incremental duplicate detection)
    - `get_archive_owner_id() -> str | None`
    - `set_archive_owner_id(user_id: str)`
  - Use one DB transaction per persisted page so raw capture, tweet upserts, membership upserts, and sync-state updates commit atomically.
- [x] Raw JSON persistence: implement based on Task 0 spike decision (inline blobs or gzipped files).
  - If using gzipped sidecar files, write them atomically (temp file + rename) and only commit DB references after the file exists.
- [x] Unit tests using a temp data dir (no network, no real embeddings), including atomic page-write behavior and owner-id mismatch handling.

## Task 6: Sync Orchestration

- [x] Implement `tweetnook/sync.py`
  - Add a shared preflight helper used by both `auth check` and `sync`: resolve auth, resolve query IDs, and run lightweight remote probes with **no DB writes**.
  - `async def sync_collection(collection: str, *, full: bool, limit: int | None)` — the main sync loop per PLAN.md "Sync Loop + Stop Conditions" section.
  - Validates auth before first API call (auth_token + ct0 present; user_id present if syncing likes) and performs a remote readiness probe before opening the main loop.
  - Incremental by default: do a head pass from `cursor=None`; if `backfill_incomplete`, continue from stored `backfill_cursor` after the head pass.
  - `--full` resets only the targeted collection sync state after preflight + lock acquisition; it does not delete existing tweet/membership data.
  - Stop conditions: empty page, collection-scoped duplicate detection during head pass (unless `--full`), `--limit`, rate limit exhaustion.
  - Persist sync state after each page in the same DB transaction as tweet/membership writes (crash-safe resume).
  - Ensure the process lock is released via `try/finally`, including on `429` exhaustion or unexpected exceptions.
  - Progress output via Rich (tweets synced, pages fetched, current status).
  - `sync_all(full, limit)` — preflights both requested collections before any writes, then runs bookmarks followed by likes; runtime failures are reported as partial failure rather than rolled back across collections.
- [x] Add a process lock helper (lock file in XDG data dir) so overlapping sync commands fail fast instead of racing.
- [x] Unit tests: run sync against mocked HTTP responses and verify raw_captures appended, tweets upserted, memberships created, head-pass + backfill state advance correctly, stop conditions trigger correctly, preflight probes do not count against `--limit`, `--limit` applies per collection, and `sync all` does not partially write if one collection fails preflight.

## Task 7: CLI

- [x] Implement `tweetnook/cli.py` (Typer) with commands:
  - `tweetnook sync bookmarks [--full] [--limit N]`
  - `tweetnook sync likes [--full] [--limit N]`
  - `tweetnook sync all [--full] [--limit N]`
  - `tweetnook auth check` — run shared preflight without DB writes, print local credential status plus remote readiness for bookmarks/likes, exit 0/1/2.
  - `tweetnook auth refresh-ids` — force query ID refresh from JS bundles.
- [x] Added `tweetnook --version`:
  - prints the package semver and, when running from a git checkout, the short commit hash
  - appends `dirty` when tracked files differ from `HEAD`, which makes editable local-tool installs easier to verify before pushing
- [x] Improved sync help output:
  - `tweetnook sync --help` now explains that it is a command group and lists descriptive summaries for `bookmarks`, `likes`, `tweets`, and `all`
  - `tweetnook sync <subcommand> --help` now gives real descriptions for `--full`, `--backfill`, and `--limit` instead of showing bare flag names
- [x] Documented sync flags and backfill markers in the README:
  - added one-line explanations for when to use `--full`, `--backfill`, `--head-only`, `--article-backfill`, `--limit`, and browser/profile overrides
  - documented the `resume older`, `none saved`, `saved only`, and `incomplete` status markers plus the exact `--head-only` command used to clear `resume older`
- [x] Updated `AGENTS.md` to keep CLI help/docs/tests in sync going forward:
  - new user-facing flags and status markers now require explicit CLI help text, README updates when behavior is user-facing, and representative help-output test coverage
- [x] Made bare `tweetnook sync` the default archive-maintenance entrypoint:
  - `tweetnook sync` now behaves like the normal bookmarks + likes sync pass instead of acting as a help-only group
  - the default sync path visibly runs archive enrich, thread expansion, article refresh, media download, and unfurl unless `--skip-*` flags opt out
  - authored tweets remain explicit via `tweetnook sync tweets`, so the comprehensive default still does not silently expand into a third live collection
- [x] Documented how to backfill older archives after upgrading:
  - `README.md` now includes a single manual follow-up recipe covering `import enrich`, `threads expand`, `articles refresh`, `media download`, and `unfurl`
  - the README also shows bounded `--limit` examples for each follow-up command so incremental validation runs are part of the documented workflow
- [x] Improved interactive observability for follow-up maintenance commands:
  - `articles refresh`, `media download`, and `unfurl` now show TTY-only status lines plus progress bars instead of staying mostly silent until completion
  - article refresh also forwards TweetDetail pacing/retry status during interactive runs, bringing it closer to archive enrich/thread expansion
- [x] First-run UX: all commands auto-create XDG dirs. `sync` commands validate auth before API calls, probe the target collection(s) before writing data, and print actionable errors (not stack traces) on failure.
- [x] Exit codes: 0 success, 1 auth/config error, 2 API/network/runtime sync error. `sync all` uses 2 for partial runtime failure after reporting per-collection results.

## Task 8: JSON Export

Optional but useful early.

- [x] Implement `tweetnook/export/json_export.py`
  - Export by collection type (likes/bookmarks/all) to a JSON file.
  - Include: tweet_id, text, author info, created_at, collection membership, raw_json (or path).
- [x] Add `tweetnook export json [--collection likes|bookmarks|all] [--out path]`.
- [x] Add `tweetnook view bookmarks|likes|all [--limit N]`.

## Task 9: Integration Test + Polish

- [x] **End-to-end integration test**: mock HTTP transport that returns realistic multi-page Bookmarks + Likes responses. Run full `sync_collection` → verify raw_captures, tweets, memberships, and checkpoints are all correct. Verify resume after simulated interruption.
- [x] **Collection-scoped duplicate test**: a tweet that already exists in `tweets` but not yet in the current collection must not stop sync early.
- [x] **Preflight behavior test**: `auth check` and `sync all` share the same probe path; failed likes preflight must abort `sync all` before bookmark writes.
- [x] **Incremental-vs-backfill test**: after an interrupted first run with stored `backfill_cursor`, the next sync must still fetch new head items before resuming older pages.
  - Follow-up cleanup: if a resumed backfill page comes back empty, the sync state now clears the saved backfill cursor even when X echoes the same bottom cursor back, so stale `resume older` status does not stick around indefinitely.
- [x] **Single-writer lock test**: a second sync process/instance must fail cleanly without mutating DB or cache state.
- [x] **Atomic checkpoint test**: simulated write failure must not leave `sync_state` advanced past durable tweet/membership writes.
- [x] **`--full` resume test**: interrupted `--full` sync leaves resumable backfill state and does not require deleting prior data.
- [x] **Lock release test**: failures during sync still release the process lock for the next run.
- [x] **Partial `sync all` test**: if bookmarks succeed and likes later fail, bookmark writes remain committed and the command exits with partial-failure status.
- [x] Security audit: ensure logs never include cookie values (grep for auth_token/ct0 in any logging/exception paths).
- [x] Verify first-run UX: run against empty XDG dirs with no config → confirm dirs created, clear error message about missing cookies.
- [x] Update `WORKLOG.md` with milestone completions.
- [x] Keep `docs/PLAN.md` in sync if any decision changed during implementation.

## Task 10: Replace SQLite Storage with LanceDB

Completed on 2026-03-15. This replaced the temporary SQLite fallback before any real archive data was loaded.

- [x] Replace runtime storage deps in `pyproject.toml`:
  - Removed `pyseekdb`.
  - Added `lancedb` and `pyarrow`.
  - Refreshed `uv.lock`.
- [x] Rename the concrete storage module to `tweetnook/storage/backend.py`.
  - Stopped using backend-specific filenames like `seekdb.py` / `lancedb.py` for the shipped implementation.
  - Kept the public `ArchiveStore` / `SyncState` API in `tweetnook/storage/__init__.py`.
- [x] Eliminate the current sync-loop cleanup items during the backend migration.
  - Removed double-preflight in `sync_all`.
  - Moved the final `last_head_tweet_id` update into backend-managed state semantics instead of a bare outer `commit()`.
  - Returned parsed payloads from `_fetch_and_parse_page(...)` to avoid the duplicate `response.json()` call.
- [x] Implement the LanceDB-backed archive in `tweetnook/storage/backend.py`.
  - Use a single LanceDB table keyed by `row_key`.
  - Row types per `docs/PLAN.md`: `tweet`, `raw_capture`, `sync_state`, `metadata`.
  - Represent one persisted page as one batched table merge.
- [x] Update storage path conventions in `tweetnook/config.py`.
  - Switched from `archive.sqlite3` to `archive.lancedb/`.
  - Preserved XDG behavior and first-run auto-create semantics.
- [x] Keep higher-level call sites stable where possible.
  - `tweetnook/storage/__init__.py`
  - `tweetnook/sync.py`
  - `tweetnook/export/json_export.py`
  - `tweetnook/cli.py`
- [x] Port the storage test suite to the LanceDB backend semantics.
  - Atomic page persistence
  - Owner guardrail
  - Sync-state reset/resume
  - Collection-scoped duplicate detection
  - Export ordering
- [x] Re-run the existing sync/integration tests against the LanceDB backend and fix behavioral regressions.
- [x] Add LanceDB-specific regression coverage:
  - one table-version increment per successful page write
  - no partial state if failure occurs before the batch write
  - filtered export/search queries over `tweet` rows only
  - `sync_all` does not reprobe collections after a successful shared preflight
- [x] Verify the landed backend:
  - `uv run ruff format --check tweetnook tests`
  - `uv run ruff check tweetnook tests`
  - `uv run pytest`
  - `uv run tweetnook --help`
- [x] Update docs after migration lands.
  - `docs/PLAN.md`
  - `docs/ANALYSIS-db.md`
  - `docs/README.md`
  - `WORKLOG.md`

## Task 11: Secondary Object Extraction Foundation

This is the next real implementation milestone after the LanceDB migration. The goal is to stop treating each collection-scoped tweet row as the only normalized object in the system.

- [x] Extend the archive schema in `tweetnook/storage/backend.py` with new `record_type` values:
  - `tweet_object`
  - `tweet_relation`
  - `media`
  - `url`
  - `url_ref`
  - `article`
- [x] Add a parser/extractor layer that takes a raw tweet object and emits:
  - canonical tweet-object fields
  - attached-tweet relations (`retweet_of`, `quote_of`)
  - media metadata (`extended_entities`, `video_info`)
  - URL refs / canonical URL candidates
  - article payloads when present
- [x] Keep collection-scoped `tweet` rows as the duplicate-detection and export-ordering layer during the transition.
- [x] Persist the new rows in the same page-sized LanceDB batch as the current raw capture + membership rows.
- [x] Extend rehydrate support so new normalized rows can be rebuilt from stored `raw_json` without refetching.
- [x] Tests:
  - quote/retweet relation extraction
  - media extraction for photos/videos/GIFs
  - URL extraction from entity/card payloads
  - one-page atomicity across the expanded record set
  - same tweet appearing in bookmarks and likes does not duplicate global secondary objects

## Task 12: Media Downloads + URL Unfurls

- [x] Add per-media download state fields and local-path metadata.
- [x] Implement photo download first:
  - deterministic on-disk layout under the XDG data dir
  - SHA-256 + byte-size verification
  - idempotent retries
- [x] Implement video/GIF download later:
  - variant selection policy (prefer highest-bitrate MP4 when present)
  - poster image capture
- [x] Implement URL canonicalization and unfurl persistence:
  - preserve original `t.co` URL
  - store expanded/final/canonical URL values
  - store metadata already present in GraphQL payloads before doing network fetches
- [x] Add a follow-on command or job-runner surface for remote unfurl fetches / snapshots without coupling them to the sync transaction.
  - Landed as inline commands: `tweetnook media download` and `tweetnook unfurl`
- [x] Leave ArchiveBox integration as optional queue/runner plumbing until the metadata model is stable.

## Task 13: Articles

- [x] Add a dedicated `--article-backfill` timeline rescan mode so existing collection pages can be refetched after article field toggles change, without resetting sync state.
- [x] Add an article probe fixture once we capture a real authenticated `UserArticlesTweets` or article-bearing timeline response.
  - Working example URL: `https://x.com/dimitrispapail/status/2026531440414925307`
  - Captured as `tests/fixtures/dimitris_article_tweet_detail.json` from an authenticated `TweetDetail` response on 2026-03-16
- [x] Verify whether full bodies are returned now that article field toggles are enabled on timeline requests.
  - Result on 2026-03-16: authenticated `TweetDetail` returned full `plain_text`, `content_state`, `cover_media`, and `media_entities`
- [x] Persist article rows keyed by source tweet id until a stable article-specific id is confirmed.
- [x] Export article metadata/body in JSON once extraction is stable.
- [x] Decide whether article-only fallback fetching is needed if GraphQL returns preview-only payloads.
  - Current decision: no extra fallback is needed right now; `tweetnook articles refresh` uses authenticated `TweetDetail`, which returned full bodies for the Dimitris validation tweet on 2026-03-16

## Task 14: Own Tweet Capture

This is materially smaller than archive import because it reuses the live GraphQL sync path, the current extractor layer, and the existing media/unfurl/export follow-on jobs.

- [x] Add `UserTweets` query-id coverage and a dedicated request builder.
- [x] Reserve `UserTweetsAndReplies` for a later follow-on; start with authored tweets only.
- [x] Add CLI shape:
  - `tweetnook sync tweets`
  - `tweetnook view tweets`
  - `tweetnook export json --collection tweets`
- [x] Add a collection/storage label for authored tweets that reuses the current `tweet` membership rows plus secondary-object extraction.
- [x] Reuse the existing duplicate-detection, sync-state, rehydrate, media-download, URL-unfurl, and article-refresh paths for own-tweet rows.
- [x] Decide whether `tweetnook sync all` should include own tweets, or whether authored tweets stay an explicit opt-in collection.
  - Current decision: keep authored tweets as an explicit opt-in collection via `tweetnook sync tweets` so `sync all` does not unexpectedly expand archive size.
- [x] Add regression coverage for:
  - incremental `UserTweets` pagination
  - collection-scoped duplicate detection on authored tweets
  - export/view support for the new collection
  - same authored tweet later appearing in likes/bookmarks without duplicating secondary objects

## Task 15: Thread Expansion + Linked Tweet Capture

This is separate from attached-tweet extraction. The current extractor already stores one-level quote/retweet payloads when they are embedded in the timeline response, but it does not fetch missing parents, replies, or linked tweet URLs.

- [x] Add a follow-on `TweetDetail` expansion path for archived tweets.
- [x] Decide the initial trigger surface:
  - explicit command landed first via `tweetnook threads expand`
  - optional sync-time expansion can be reconsidered later if the current runner stays stable
- [x] Reserve CLI shape for the first pass:
  - `tweetnook threads expand`
  - optional `tweetnook threads expand <tweet-id-or-status-url> ...`
  - optional `tweetnook threads expand --refresh <tweet-id-or-status-url> ...` for explicit re-fetches
- [x] Persist thread/context tweets as global `tweet_object` rows plus `tweet_relation` edges without inventing bookmark/like/tweets memberships for them.
- [x] Add new relation types for thread context as needed (`reply_to`, `in_reply_to`, `thread_parent`, `thread_child`) once we lock the exact `TweetDetail` shape.
- [x] Expand linked X-status URLs found in `url_ref` rows:
  - detect `x.com/.../status/<id>` and `twitter.com/.../status/<id>`
  - fetch them through the same `TweetDetail` path
  - avoid duplicate fetches when the linked tweet is already present as a membership, attached tweet, or previously-expanded context tweet
- [x] Reuse rehydrate where possible for relation rebuilding, but document that remote thread expansion itself is not recoverable from local data unless the `TweetDetail` payload was already captured.
  - Current behavior: `tweetnook rehydrate` now also rescans stored `TweetDetail` / `ThreadExpandDetail` raw captures, so previously captured detail payloads can rebuild thread/context rows without another network fetch.
- [x] Add regression coverage for:
  - parent-thread capture from `TweetDetail`
  - linked status-URL capture
  - idempotent repeated expansion runs
  - preserving collection-scoped membership boundaries while adding global thread/context rows

## Task 16: X Archive Import

Fresh fixture status (2026-03-16):
- Real sample cataloged in `docs/ANALYSIS-archive-import.md`.
- Confirmed overlap: `tweets.js`, `tweet-headers.js`, `deleted-tweets.js`, `deleted-tweet-headers.js`, `like.js`, and `tweets_media/`.
- Confirmed gaps in this sample: no bookmark dataset; `article.js`, `article-metadata.js`, `note-tweet.js`, and `community-tweet.js` are present but empty.

- [x] Catalog the real archive shape and record overlap findings.
- [x] Lock first-pass precedence rules from the sample:
  - Live GraphQL wins for richer normalized tweet/media/url/article fields when both sources overlap.
  - `x_archive` wins for deleted authored tweets and already-exported media binaries.
  - `like.js` imports are sparse membership/provenance rows until live sync enriches them.
  - Do not rely on the current new-non-empty-wins coalescing semantics for live/archive merges.
- [x] Reserve CLI shape:
  - `tweetnook import x-archive <zip-or-dir>`
- [x] Add storage-layer source-aware merge logic in `ArchiveStore`, using the normalized row `source` field as the winning-source marker instead of caller-side ad hoc merges.
  - Treat pre-existing `source = NULL` rows as `live_graphql` for backward compatibility until a later backfill/migration populates them explicitly.
  - Start writing `source = "live_graphql"` from live sync and `source = "x_archive"` from archive import.
- [x] Add a dedicated `import_manifest` record type keyed by archive digest with generation date, status, warnings, and per-dataset counts.
- [x] Validate archive ownership before import writes by comparing `account.js.account.accountId` against the stored archive owner metadata.
- [x] Add a generic `parse_ytd_js(...)` / zip-directory loader for `manifest.js` plus `window.YTD.*` `data/*.js` parts, then layer per-file adapters on top.
- [x] Import authored tweets from `tweets.js` / `deleted-tweets.js` through a YTD-to-internal adapter, including nullable `deleted_at` support on both membership `tweet` rows and normalized `tweet_object` rows.
- [x] Import `like.js` into collection rows with a synthetic archive-order `sort_index` and raw provenance, while also seeding sparse global tweet placeholders for later enrichment.
  - Encode synthetic archive ordering as negative numeric-string sort indexes (`-1`, `-2`, ...) so existing integer-based ordering code keeps working.
- [x] Copy `tweets_media/` exports into the managed tweetnook media layout, then register them on `media.local_path` / `download_state`.
- [x] Add post-import live reconciliation:
  - run normal bulk live syncs first (`tweets`, `likes`, later bookmarks if available) to upgrade overlapping rows cheaply
  - run targeted per-item GraphQL lookups only for rows that remain sparse after the bulk pass
  - Shipped behavior: bulk syncs run automatically when auth is available; per-item `TweetDetail` lookups stay explicitly bounded via `--detail-lookups` (default `0`) so huge like archives do not fan out into an unbounded follow-up crawl.
- [x] Track per-tweet live-enrichment fields on sparse `tweet_object` rows:
  - `enrichment_state`
  - `enrichment_checked_at`
  - `enrichment_http_status`
  - `enrichment_reason`
- [x] Keep archive provenance even when a later live likes/bookmarks sync no longer includes that item; collection absence is not by itself a terminal lookup result.
- [x] Add regression fixtures/tests for repeated imports, live+archive merges, and archive-after-live precedence behavior.
- [x] Landed the first post-review hardening pass for Task 16:
  - fixed the existing-thumbnail fallback so reused archive poster files do not coerce missing metadata into `"None"` / `int(None)`
  - closed zip inputs on manifest-load failure and made `_ArchiveInput` usable as a context manager
  - rejected `..` path segments in manifest-provided archive filenames before resolving extracted-directory paths
  - added regressions for missing manifests, owner mismatch, zip-close-on-init-failure, malicious manifest filenames, and pre-existing thumbnail destinations
- [x] Landed the second post-review hardening pass for Task 16:
  - coalesced media download updates per normalized `media` row so importing both the main asset and thumbnail cannot clobber whichever field was written first
  - downgraded non-terminal `TweetDetail` API failures during `--detail-lookups` to tracked `transient_failure` states instead of aborting the overall import after archive writes had already completed
  - preserved one attempt-scoped `import_started_at` value across all import-manifest rewrites for the run
  - added regressions for combined main+thumbnail media import, transient detail API failures, and manifest start-time preservation
- [x] Smoothed the archive-import follow-up UX:
  - documented `TweetDetail` in end-user terms in the README instead of assuming users know the internal X API name
  - added `tweetnook import x-archive --enrich` as the “do the rest” path for pending sparse archive tweets
  - changed repeated imports so a plain rerun still short-circuits by digest, but `--enrich` reuses the existing import and runs the follow-up enrichment instead of skipping outright
  - documented `tweetnook threads expand` as the broader incremental TweetDetail/context follow-up command after import
- [x] Added a standalone archive follow-up command:
  - added `tweetnook import enrich [--limit N]` so pending archive-placeholder enrichment can be resumed later without the original ZIP/directory path
  - routed both `import x-archive --enrich` and `import enrich` through the same reconciliation + pending-row follow-up runner
  - restricted standalone enrich discovery to completed archive imports so stale failed manifests do not masquerade as resumable archive state
- [x] Reduced LanceDB version churn during archive detail enrichment:
  - changed `import enrich` / archive follow-up detail writes to batch TweetDetail success/failure row updates into chunked Lance merges instead of committing one version per tweet
  - preserved the existing end-of-job optimize path, but made interrupted large enrich runs far less likely to strand tens of thousands of table versions in the middle of a long rate-limited crawl
  - added regression coverage proving buffered detail writes collapse 12 refreshed tweets into 3 Lance versions when the batch size is forced to 5 for the test
- [x] Added best-effort interrupt compaction for long-running archive writers:
  - introduced shared write tracking that records committed batches/rows plus a Lance version-delta fallback, so interrupt cleanup does not rely on whether the runner reached its normal success tail
  - moved dirty-write marking to actual persist/flush points in `sync`, archive import/enrich, `threads expand`, `articles refresh`, `media download`, and `unfurl`
  - first `Ctrl-C` after substantial committed work now attempts a best-effort compact before exit; a second `Ctrl-C` during that compact skips it and warns to run `tweetnook optimize` later
  - added focused regressions for interrupted shared jobs, interrupted sync after committed pages, and interrupted archive import after bulk writes
- [x] Landed the third post-review hardening pass for Task 16:
  - keep archive-imported video/animated-GIF media rows `pending` until both the main asset and poster file are present, so `tweetnook media download` can still fill gaps after poster-only archive imports
  - stopped archive `deleted_at` imports from flipping source precedence away from richer live rows; archive deletion metadata now merges in without overwriting live text/author fields
  - expanded archive-import regressions to cover ZIP happy-path import, extracted root-layout archives, and filename-specific parse errors
- [x] Added interactive observability for archive import/enrich:
  - `tweetnook import x-archive` now prints immediate startup and phase/progress status on interactive TTY runs during archive hashing, dataset loading, bulk row import, media copy, and follow-up reconciliation/enrichment
  - `tweetnook import enrich` uses the same TTY-gated follow-up status path
  - non-interactive runs stay quiet by default, so cron/piped runs do not inherit the new progress chatter
- [x] Follow-up hardening for interrupted/large archive imports:
  - interrupted archive imports now mark the manifest `failed` even on `KeyboardInterrupt` / cancellation before re-raising
  - archive dataset `raw_capture` rows now use deterministic keys per `(archive_digest, operation, filename)` so interrupted reruns overwrite instead of duplicating archive captures
  - added `tweetnook import x-archive --regen` to clear archive-import-owned rows, manifests, and copied archive media files without touching live-owned rows
  - upgraded interactive archive import progress from coarse phase markers to tqdm-backed rate/ETA output, and added sampled import mode (now exposed as `--sample-limit N`) that does not poison normal `completed` manifests
- [x] Optimized archive import for large existing archives:
  - added storage-level row-key prefetching so archive import can bulk hydrate existing rows into the page buffer instead of issuing one LanceDB lookup per merged tweet/object/media/url/article record
  - changed authored-tweet import to precompute and merge small graph chunks with one prefetch pass per chunk, and changed like import to prefetch `tweet:like` + `tweet_object` rows before placeholder seeding
  - validated the optimization against a `/tmp` copy of the real optimized archive DB: sampled authored import improved from `39.09s` (`25.6 tweets/s`) to `1.37s` (`728.1 tweets/s`), and sampled like import improved from `24.69s` (`40.5 likes/s`) to `0.55s` (`1806.1 likes/s`)
- [x] Clarified reconciliation progress output after the first real archive follow-up run:
  - changed the shared sync logger used by live sync and archive follow-up to label `head` vs `backfill` passes explicitly instead of printing one ambiguous cumulative `tweets N` counter
  - per-page reconciliation lines now report both `page_tweets` and `total_tweets`, and resumed runs emit a `resuming saved backfill pass` line before continuing older pages
  - confirmed the large speedup fix was specific to archive ingest's LanceDB point-lookups; `import enrich`, `threads expand`, and the other follow-up jobs remain primarily network-bound and did not need the same storage prefetch optimization
- [x] Hardened archive follow-up safety and rerun semantics before first real enrichment use:
  - stopped systemic TweetDetail failures (`StaleQueryIdError`, auth expiry, feature-flag drift, rate-limit exhaustion) from mutating per-tweet enrichment state; those now bubble once as follow-up warnings so pending rows remain retryable
  - restricted terminal TweetDetail classification to `410` instead of treating all `404` responses as not-found in the archive enrichment path
  - preserved existing manifest warnings when reusing a completed archive with `import x-archive --enrich`
  - constrained `--regen` archive-managed file deletion to the `media/` subtree and changed the bookmark-dataset warning/docs to note that missing bookmarks are expected for current official X archives
  - removed the full-archive authored secondary-graph precompute by preparing authored import chunks lazily inside the batched merge loop
- [x] Hardened archive-import identity and deletion classification:
  - require official archive owner metadata before archive captures or data rows are written
  - keep every `deletedTweets` row in the deleted-authored count and classify its normalized tweet object as permanently archive-deleted even when no `deleted_at` value exists
  - preserve real deletion timestamps when available
  - added focused regressions for unknown-owner rejection, no partial writes, and timestamp-less deleted rows
- [ ] Post-rollout archive-import follow-ups:
  - narrow archive media copy lookups so `_copy_exported_media(...)` only scans rows relevant to the imported archive's tweet ids / provenance instead of materializing the full `media` table
  - revisit `clear_archive_import_data()` / `--regen` manifest semantics so archive-only cleanup can preserve multi-digest manifest history when desired
  - evaluate whether `ArchiveStore.prefetch_rows(...)` should gain a lighter-weight projection mode if another real-world ingest perf pass is needed
## Review Cleanup

Follow-up maintenance work after the content-expansion milestone. Land these as small, well-tested refactors instead of rolling them into feature work.

- [x] Review item 1: de-duplicate the sync CLI command implementations in `tweetnook/cli.py`.
  - Current problem: `sync bookmarks`, `sync likes`, `sync tweets`, and `sync all` repeat the same config/auth/error-handling flow.
  - Landed approach: registered the per-collection sync commands through one factory and moved shared config/auth/error handling into one helper, while keeping the existing command names and options unchanged.
  - Coverage: CLI forwarding now exercises `sync bookmarks`, `sync likes`, `sync tweets`, and `sync all` against the shared path.
- [x] Review item 2: extract the shared locked-store batch-job skeleton used by `media.py`, `unfurl.py`, `articles.py`, and `threads.py`.
  - Current problem: each runner repeats the same config/path resolution, archive lock acquisition, store open/close handling, and conditional optimize flow.
  - Landed approach: added a shared `locked_archive_job(...)` async context plus `resolve_job_context(...)` in `tweetnook/jobs.py`, and moved the four runners onto that helper while keeping auth resolution outside the lock where needed.
  - Optimize semantics preserved: media/unfurl mark the job dirty after any processed rows; articles/threads only mark dirty after successful updates/expansions.
  - Coverage: direct helper tests now cover close/error/conditional-optimize behavior, and the existing media/unfurl/articles/threads runner tests still pass on top.
- [x] Review item 3: reduce the repeated coalesce/timestamp boilerplate in `tweetnook/storage/backend.py`.
  - Current problem: each secondary `_..._record` builder repeats the same row timestamp setup and `existing[\"field\"] if existing else None` coalescing pattern.
  - Landed approach: added a small internal `_RecordContext` helper plus `_coalesce_existing(...)` / `_record_with_context(...)` so the record builders share row/timestamp setup without turning into a generic mapper.
  - Coverage: storage now has a regression proving a later thinner secondary payload does not wipe richer existing media/article fields.
- [x] Review item 4: centralize the duplicate `utc_now` helper into a shared utility module.
  - Current problem: identical `_utc_now()` helpers exist in `media.py`, `unfurl.py`, and `storage/backend.py`.
  - Landed approach: moved the shared timestamp helper into `tweetnook/utils.py` and reused it from storage, media, and unfurl.
  - Coverage: the existing storage/media/unfurl tests stayed green after the helper move.
- [x] Review item 5: unify `_canonical_url_candidate` and `_final_url_candidate` in `tweetnook/extractor.py`.
  - Current problem: the two helpers are nearly identical but diverge in subtle ways, which is an easy future bug source if one path gets updated without the other.
  - Landed approach: replaced the parallel helpers with one `_url_candidate(...)` helper that takes the candidate key order plus a `require_absolute` switch, so the canonical-vs-final differences stay explicit in the call sites.
  - Coverage: extractor tests now exercise both unwound final-URL selection and `t.co` canonical fallback through the shared helper.
- [x] Review item 6: push state/type filtering for secondary row listings into LanceDB predicates in `tweetnook/storage/backend.py`.
  - Current problem: `list_media_rows(...)`, `list_url_rows(...)`, and `list_article_rows(...)` currently materialize every row of that record type and then filter in Python.
  - Landed approach: moved the state/type/preview filters into shared LanceDB expression helpers so `ArchiveStore` only materializes matching media/url/article rows, while keeping the existing Python-side sort order unchanged.
  - Coverage: storage now has a real LanceDB-backed regression covering pending/done media filters, URL state filters, and preview-only article selection.
- [x] Review item 7: de-duplicate the repeated thread-expansion try/except/counting blocks in `tweetnook/threads.py`.
  - Current problem: the explicit-target loop, membership loop, and linked-status loop all repeat the same `_expand_target(...)` error-handling and result-counting path.
  - Landed approach: extracted one `_try_expand_target(...)` helper that owns the shared processed/expanded/failed bookkeeping plus `expanded_targets` / `known_tweet_ids` updates, while leaving the loop-specific skip/selection rules unchanged.
  - Coverage: thread tests now cover both the existing membership+linked-status path and an explicit-target case that locks in duplicate skipping plus failure counting.
- [x] Review item 8: make Firefox cookie snapshotting WAL-safe in `tweetnook/auth/firefox.py`.
  - Current problem: copying `cookies.sqlite` plus `-wal` / `-shm` sidecars separately can still race a live Firefox write and produce an inconsistent snapshot.
  - Landed approach: the first SQLite-backup snapshot attempt proved capable of hanging on busy live profiles, so the bounded shipped path copies `cookies.sqlite` plus any present `-wal` / `-shm` / `-journal` sidecars into a temp snapshot before querying cookies.
  - Coverage: auth tests now cover reading cookies from a WAL-mode Firefox DB while the source connection remains live.
- [x] Review item 9: broaden runner and extractor test coverage for error paths and edge cases.
  - Current problem: the new runner modules mostly only have happy-path tests, and extractor coverage is still thin on malformed payloads.
  - Landed approach: added focused tests for runner failure states, retries, limits, non-HTML responses, invalid detail payloads, and malformed extractor inputs without changing runner behavior.
  - Coverage: media/unfurl now cover retry + limit flows, articles/threads cover invalid-response or limit behavior, and extractor tests cover malformed article/attached/url/media payload shapes.
- [x] Review item 10: add direct unit coverage for `ExtractedTweetGraph` merge/coalesce behavior.
  - Current problem: the graph-level `add_*` methods are only exercised indirectly through extraction/storage integration tests, which makes edge-case precedence rules harder to lock down.
  - Landed approach: added focused unit tests for direct `add_tweet_object(...)`, `add_media(...)`, and `merge(...)` behavior, covering new-vs-existing precedence, empty-string handling, `min(position)`, and article status promotion.
  - Coverage: direct graph tests now cover tweet/media/url/url_ref/article coalescing plus one explicit `merge(...)` path.
- [x] Review item 11: batch media/unfurl row updates and clean up minor clarity issues.
  - Current problem: `media` and `unfurl` currently do a LanceDB read + merge per item via `update_media_download(...)` / `update_url_unfurl(...)`, which is unnecessarily expensive at larger archive sizes.
  - Landed approach: added reusable row-update builders plus `ArchiveStore.merge_rows(...)`, switched media/unfurl to flush updated rows in batches, and folded in the low-risk CLI-parentheses + media-URL safety fixes while touching those files.
  - Coverage: media/unfurl tests now prove the batched merge path is actually used, and the existing state-transition coverage stayed green on top.
- [x] Review item 12: improve long-running thread-expansion observability.
  - Current problem: `tweetnook threads expand` only emits per-target failures plus the final summary, so long runs can appear hung while they are retrying, cooling down on 429s, or scanning a large archive.
  - Landed approach: added a shared request-status callback path in the HTTP client, then wired `threads expand` to print pass-level progress plus tweet-scoped 429 retry/cooldown and query-id-refresh messages.
  - Coverage: client tests now lock in retry/cooldown and 404-refresh status messages, and thread tests now cover visible rate-limit diagnostics during an explicit-target expansion failure.
- [x] Review item 13: show startup progress before thread-expansion preload scans and defer unnecessary archive scans.
  - Current problem: `tweetnook threads expand` still stays silent at startup on large archives because it eagerly loads prior expansion targets, known tweet ids, and membership ids before the first progress line.
  - Landed approach: added immediate startup/preload status lines, then deferred the expensive `known_tweet_ids` scan until the linked-status pass actually needs it so explicit-target runs and some limit-bounded runs stop paying that cost up front.
  - Coverage: thread tests now lock in early preload logging for both the normal membership+linked-status path and the explicit-target/rate-limit path.
- [x] Review item 14: add an auth-resolution debug path for long-running CLI jobs.
  - Current problem: if a command stalls before the archive job starts, there is no visibility into whether browser cookie resolution or profile/keyring probing is the blocking step.
  - Landed approach: added a `--debug-auth` flag for `threads expand` and `auth check`, plus auth-resolution status callbacks that surface browser/profile probing steps from the cookie resolver.
  - Coverage: auth tests now lock in emitted browser-probe status, and CLI tests cover `--debug-auth` output plumbing for both `auth check` and `threads expand`.
- [x] Review item 15: make post-sync auto-embedding best-effort instead of failing a successful sync.
  - Current problem: `_sync_collection_ready()` persists fetched pages, then auto-embedding can still raise on model/artifact/runtime issues and flip the whole command to failure after the archive write already succeeded.
  - Landed approach: successful sync persistence now wins. Auto-embedding failures are caught, surfaced as warnings, and deferred to a later `tweetnook embed` run or a future sync instead of failing the capture command.
  - Coverage: `tests/test_sync.py` now forces embedding initialization to fail after page persistence and proves the sync still succeeds with stored rows intact.
- [x] Review item 16: define `--browser` auth-override semantics for `user_id`.
  - Current problem: the current browser override drops explicit env/config `user_id`, which can break likes/tweets even when the user configured a numeric fallback.
  - Landed approach: browser overrides now only force cookie sourcing (`auth_token` / `ct0`) from the selected browser/profile; explicit env/config `user_id` remains a fallback for likes/tweets.
  - Coverage: CLI tests now lock in that `--browser` preserves explicit `user_id` fallback inputs.
- [x] Review item 17: tighten thread-expansion rerun/dedupe semantics.
  - Current problem: explicit `threads expand <id/url>...` currently refetches already-expanded targets, and linked-status expansion only remembers successful targets within a run, so one failing target can be retried repeatedly from multiple URL refs.
  - Landed approach: explicit targets are now idempotent by default, `--refresh` is the explicit re-fetch escape hatch, and linked status-URL targets are attempted at most once per run.
  - Coverage: thread tests now cover default explicit-target skipping, `--refresh` refetches, and duplicate linked-status failures only attempting one network call per run; CLI tests cover the new `--refresh` flag and validation.
- [x] Review item 18: decide and document the supported runtime platforms.
  - Current problem: README/path messaging implies Windows support, but core runtime pieces (`fcntl`, `resource`, `strftime("%-d")`) keep the current CLI Unix-specific.
  - Landed approach: documented the current runtime as Unix-like only until platform-specific replacements and tests land for those dependencies.
- [x] Review item 19: tighten PyPI release metadata and artifact contents.
  - Current problem: the PyPI-facing README used a repo-relative screenshot, install docs led with source checkout instead of `pip install`, and the default sdist pulled in repo-internal docs/tests/worklog files.
  - Landed approach: switched the README screenshot to a direct GitHub raw URL, moved PyPI install instructions ahead of the source-install path, added explicit project URLs plus Unix-like trove classifiers, and constrained hatchling wheel/sdist targets to the package and release files. Hatchling still auto-includes `.gitignore` in the sdist.
  - Validation: `uv build`, `uv run --with twine twine check dist/*`, and direct wheel/sdist content inspection.
- [x] Review item 20: normalize embeddings and align semantic search to cosine distance.
  - Current problem: the ONNX embedding pipeline stored raw mean-pooled vectors, and LanceDB vector/hybrid search used the default distance metric instead of explicitly matching sentence-transformer-style cosine similarity.
  - Landed approach: L2-normalized embedding outputs in `tweetnook/embed.py`, forced cosine distance in `ArchiveStore.search_vector(...)` / `search_hybrid(...)`, and documented that existing archives should rerun `tweetnook embed --regen` once after upgrading.
  - Validation: `uv run pytest tests/test_embed.py tests/test_storage.py tests/test_cli.py tests/test_sync.py -q`, `uv run ruff check tweetnook/embed.py tweetnook/storage/backend.py tests/test_embed.py tests/test_storage.py`, and `uv run ruff format --check tweetnook/embed.py tweetnook/storage/backend.py tests/test_embed.py tests/test_storage.py`.
- [x] Review item 21: reduce query-id coupling and clean up dead sync-state parameters.
  - Current problem: `articles.py` / `threads.py` imported the private `_resolve_query_ids(...)` helper from `sync.py`, and `_store_state_for_page(...)` still carried unused parameters from an older sync-state shape.
  - Landed approach: promoted the shared query-id resolver into `tweetnook/utils.py`, updated the callers to use the public helper, removed the dead `_store_state_for_page(...)` parameters, and annotated the historical Firefox implementation note to point readers at the newer WAL-safe snapshot details below.
  - Validation: `uv run pytest tests/test_sync.py tests/test_articles.py tests/test_threads.py tests/test_cli.py -q`, `uv run ruff check tweetnook/utils.py tweetnook/sync.py tweetnook/articles.py tweetnook/threads.py`, and `uv run ruff format --check tweetnook/utils.py tweetnook/sync.py tweetnook/articles.py tweetnook/threads.py`.
- [x] Review item 22: highlight literal query matches in CLI search output.
  - Current problem: `tweetnook search` printed matching tweets plainly, which made it harder to scan FTS and hybrid results when the literal query text was actually present in a longer tweet body.
  - Landed approach: added case-insensitive reverse-video highlighting for the whitespace-split query terms in the rendered search text column, without changing retrieval semantics or scoring.
  - Validation: `uv run pytest tests/test_cli.py -q`, `uv run ruff check tweetnook/cli.py tests/test_cli.py`, and `uv run ruff format --check tweetnook/cli.py tests/test_cli.py`.
- [x] Review item 23: search surfaced result types instead of raw membership rows only.
  - Current problem: `tweetnook search` only queried `record_type='tweet'`, so article titles/bodies were invisible and duplicate bookmark/like memberships leaked storage internals into the result list.
  - Landed approach: added a search-result projection layer that emits `post` and `article` hits, aggregates collections per `tweet_id`, accepts comma-delimited `--type` / `--collection` filters, and renders the combined `type · collections` label above the numeric match score in the shared table.
  - Validation: `uv run pytest -q tests/test_cli.py tests/test_storage.py`.
- [x] Review item 24: add chronological sorting to search results.
  - Current problem: `tweetnook search` only surfaced relevance ordering, so there was no way to inspect the oldest/newest matches after narrowing by query/type/collection.
  - Landed approach: added `--sort relevance|newest|oldest`, kept `relevance` as the default, and reorder the fetched relevance/semantic result set by `created_at` before rendering so chronological modes stay cheap while preserving relevance-first candidate selection.
  - Validation: `uv run pytest -q tests/test_cli.py tests/test_storage.py`.
- [x] Review item 25: add an archive stats command.
  - Current problem: there was no single command to inspect archive size/health, collection coverage, sync recency, or the high-level shape of stored content.
  - Landed approach: added `tweetnook stats`, backed by a storage summary that reports overall post/article totals, per-collection counts with first/last/sync/backfill metadata, storage health including DB/media disk usage plus an optimize hint, and follow-up queues for pending archive enrichment, missing normalized tweet objects, and thread expansion work.
  - Follow-up cleanup: tightened the storage scans so the command stays fast on large archives and added an in-command legend that explains the backfill labels plus the difference between archive enrich, local rehydrate gaps, and the two thread-expansion target buckets. The obsolete LanceDB version/optimize fields were later removed after the SQLite migration.
  - Validation: `uv run pytest -q tests/test_storage.py::test_archive_stats_summarizes_collections_and_bounds tests/test_cli.py::test_stats_archive_renders_summary_tables`.
