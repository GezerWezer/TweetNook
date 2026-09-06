# Documentation review record

[Development guide](README.md) · [User guide](../README.md) · [Release](release.md)

This record tracks the revised documentation in `README.md` and
`docs/`. These files describe the current checkout; package metadata
still uses the original `README.md`.

## September 5, 2026: upstream comparison

Expanded the README's upstream notice after comparing the old README with the
implementation and Git history. The comparison credits inherited capabilities,
explains changed workflows, and identifies removed upstream features.

### Comparison baseline

The local history contains the shared upstream revision
[`3551e26`](https://github.com/lhl/tweetxvault/tree/3551e263358982f29992e3b21c245b3675304a33),
the parent of the fork's initial web-app commit. Source comparisons used
`git show` and `git diff` against that revision, including current uncommitted
implementation files. The old README supplied leads; it was not treated as
authoritative evidence of current behavior.

Upstream's published metadata reports version 0.2.5. Its subsequent history
includes a LanceDB requirement update for full-text-index maintenance fixes and
release metadata changes. The local source comparison does not constitute a
complete checkout comparison with that newer release.
See [upstream metadata](https://github.com/lhl/tweetxvault/blob/main/pyproject.toml)
and [upstream history](https://github.com/lhl/tweetxvault/commits/main/).

### Findings and source evidence

| Area | Verified difference | Current source |
|---|---|---|
| Web app | Adds interactive browsing, thread/quote navigation, media viewing, appearance settings, credential setup, and archive upload. The frontend uses Alpine.js, not Vue. | [Page](../../tweetnook/web/index.html), [client](../../tweetnook/web/static/js/app.js), [setup routes](../../tweetnook/web/routes/setup.py) |
| Storage and search | Replaces the active LanceDB backend with SQLite/FTS5. Adds a shared search parser and database filtering/pagination; removes vector/hybrid search and the embedding extra. | [Backend](../../tweetnook/storage/backend.py), [search](../../tweetnook/search.py), [package metadata](../../pyproject.toml) |
| Migration and maintenance | Adds migration in subprocesses, smaller retry batches for unreadable source ranges, skipped-row reporting, and SQLite checks/upgrades. Optimization is explicit; the interrupt helper no longer compacts the archive. | [Migration](../../tweetnook/storage/migrate.py), [backend](../../tweetnook/storage/backend.py), [job helpers](../../tweetnook/jobs.py) |
| Tags | Adds manual tags/descriptions, tag management, quoted-original tag matching, and optional Gemini generation with test runs and persistent usage/spend accounting. | [Tagging](../../tweetnook/tagging.py), [automated tagging](../../tweetnook/automated_tagging.py), [accounting](../../tweetnook/gemini_accounting.py) |
| Scheduling and jobs | Adds a web-service scheduler, supervised CLI workers, durable activity, and a command-lifecycle lock shared by managed jobs. Upstream already had terminal progress and a storage writer lock. | [Scheduler](../../tweetnook/scheduler.py), [supervisor](../../tweetnook/job_supervisor.py), [pipeline](../../tweetnook/pipeline.py), [locking](../../tweetnook/locking.py) |
| Import defaults | Changes `import x-archive` from `enrich=False` to `True`. Standalone `import enrich` defaults to no live timeline reconciliation and processes a fixed snapshot of eligible work, committing progress in batches. | [Importer](../../tweetnook/archive_import.py), [CLI](../../tweetnook/cli.py) |
| Ordinary sync | Removes the automatic initial-import detail queue from normal sync. Adds a separate bounded unavailable-tweet recovery pass and optional tagging to the existing follow-up workflow. | [Sync](../../tweetnook/sync.py), [recovery](../../tweetnook/resurrection.py) |
| Unavailable tweets | Distinguishes confirmed unavailability from missing or ambiguous focal results. Persists reasons and recovery state; checks at most 200 due retryable tweets per normal sync and excludes permanent deletion reasons. Repeated focal absences stop detail processing. | [Timeline client](../../tweetnook/client/timelines.py), [importer](../../tweetnook/archive_import.py), [recovery](../../tweetnook/resurrection.py), [display](../../tweetnook/web/availability.py) |
| Thread expansion | Adds a separate quoted-target pass and bounded traversal of quote/link relations. Explicit target expansion, refresh, and per-run limits already existed upstream. The depth setting does not limit every related tweet returned within a Twitter/X response. | [Threads](../../tweetnook/threads.py) |
| Statistics | Extends existing CLI statistics with availability reasons, separate initial-enrichment/recovery counts, tag coverage, and storage breakdowns. Shares collection logic with the web app and caches web results with background refresh. | [Service](../../tweetnook/stats/service.py), [web cache](../../tweetnook/web/stats_cache.py) |
| Authentication and export | Keeps explicit session configuration and JSON export; removes automatic browser-cookie extraction, profile selection, and standalone HTML export. | [Authentication](../../tweetnook/auth/cookies.py), [CLI](../../tweetnook/cli.py), [JSON export](../../tweetnook/export/json_export.py) |

### Claims corrected or excluded

- LanceDB remains available through the optional `legacy-migration` extra for
  legacy migration; PyArrow is installed transitively by that extra.
- Upstream already supplies syncing and archive import,
  media downloads, article retrieval, thread expansion, and link previews. Those
  features are credited as inherited rather than presented as new additions.
- The old README's broad instability claims are not a current assessment of
  upstream. The comparison describes concrete storage/query changes without
  claiming a measured speed or memory improvement over upstream 0.2.5.
- The local web app is not a standalone HTML export. Its assets are bundled;
  offline browsing requires a reachable archive server. Avatars, live jobs,
  provider actions, and external links can still require network access.
- Gemini spend figures are estimates, not a provider-enforced billing cap.
- The command lock coordinates managed archive jobs; it does not serialize every
  web setting edit, direct tag edit, or preview request.

### Validation for this update

- Fourteen existing focused tests passed for fixed import snapshots, optional
  timeline reconciliation, unavailable-tweet retry budgets and exclusions,
  ambiguous lookup handling, quote depth, and command-lock conflict detection.
- Markdown checks passed across 29 pages, 145 language-tagged fences, 67 tables,
  and 433 local links, including heading fragments and the added source links.
- `git diff --check` passed. No live Twitter/X or Gemini requests were made, and no
  production code or package metadata was changed by this documentation update.

## September 4, 2026: readability review

The review focused on making the guides usable without knowledge of the
implementation. The original draft often opened with storage and API details,
repeated audit caveats across pages, and gave readers reference material before
the steps needed to complete a task.

Changes:

- Rewrote the README and user guides around installation, saving, browsing,
  searching, importing, and maintaining an archive.
- Explained technical command names where users encounter them, with examples
  before advanced options.
- Kept complete field and command tables in dedicated references.
- Kept implementation boundaries, API contracts, and detailed limitations in
  developer guides; added source links and a GitHub-rendered Mermaid overview.
- Used GitHub-flavored Markdown headings, tables, fenced examples, and focused
  notes or warnings for consequential behavior.
- Corrected contradictory schedule-downtime and optional-dependency advice.
- Clarified configuration bootstrap, tagging request limits, daily spend
  conversion, link-description storage, and consistent release artifact paths.

## Validation

- [x] Review all user guides and developer pages for audience and navigation.
- [x] Check setup, configuration, sync options, web startup, schedule, and tagging
  claims against the implementation and existing test coverage.
- [x] Check Markdown structure, local links, and heading fragments.
- [x] Check command examples and configuration bootstrap in an isolated environment.
- [x] Run the relevant existing CLI, web lifecycle, configuration, schedule, and tag tests.
- [x] Record final results and check the scoped diff.

| Check | Result |
|---|---|
| Markdown | 29 pages, 137 language-tagged fences, 62 tables; one H1 per page and no heading jumps |
| Local navigation | 392 links, including heading fragments and source links, resolve in this checkout |
| Shell examples | All 81 Bash examples pass `bash -n` |
| CLI help | All 43 visible command/group help pages load successfully |
| Configuration examples | Both TOML blocks parse and validate against `AppConfig` |
| Isolated setup | `--version` leaves configuration uncreated; `auth check` creates the starter file and reports missing credentials; `web start` rejects an absent archive |
| Existing tests | 179 tests pass across `test_cli.py`, `test_cli_web.py`, `test_config.py`, `test_scheduler.py`, and `test_tag_cli.py` |
| Diff hygiene | `git diff --check` passes |

Tests ran with `.venv/bin/python -m pytest` against the existing checkout.
Markdown checks used MarkdownIt's CommonMark parser with tables and strikethrough
enabled, plus local-link and GitHub-style heading checks. This is structural
validation; it does not claim a live GitHub rendering or a real Twitter/X/Gemini sync.
Source links include files in the existing uncommitted implementation work.

The previous draft recorded an August 19 research run with 872 Python tests and
47 JavaScript asset cases passing. Those are historical reports from that draft,
not results of this readability review. This review does not repeat its full
security audit, provider-policy research, or distribution build.

## Documentation boundaries

| Readers | Content |
|---|---|
| New users | README and getting-started walkthrough |
| Regular users | Task guides, examples, troubleshooting, and practical limitations |
| Users looking up an option | CLI and configuration references |
| Contributors | Architecture, code responsibilities, API contracts, and test guidance |
| Maintainers | Release instructions and this review record |

User guides should describe what an action does and how to tell whether it
worked. Developer pages should explain the code and its contracts. Avoid copying
implementation inventories or review history into everyday instructions.

Provider models, pricing, quotas, and policies are maintained by their providers.
The guides link to primary documentation rather than presenting a dated policy
summary as a permanent application guarantee.

## Promotion checklist

When moving this documentation into its final published location:

- [ ] Resolve repository and distribution identity in package metadata.
- [ ] Promote `README.md` and the user/developer guides into their final paths.
- [ ] Update the documentation index and all relative links.
- [ ] Update `pyproject.toml` README and source-distribution includes.
- [ ] Remove obsolete primary navigation while preserving historical records.
- [ ] Rebuild and inspect the wheel and source distribution.
- [ ] Recheck local links, Markdown rendering, and source-install instructions.

Follow [Release](release.md) and the repository's
[release punch list](../../docs/PUBLISH.md) for the complete publication workflow.
