# TweetNook

TweetNook archives a person's Twitter/X likes, bookmarks, tweets and official X exports into a
local SQLite database and serves them through a self-hosted web app. It is a Python 3.12 package
(FastAPI, Typer) with a browser UI built from Alpine.js and plain JavaScript, with no JS build step.

## Commands

```bash
uv sync --group dev                       # add --extra automated-tagging / legacy-migration when needed
uv run pytest tests/storage/             # run the suites for what you changed while iterating
uv run pytest                             # full suite (~75s) before committing a cross-cutting change
uv run ruff check . && uv run ruff format --check .
for f in tests/js/test_*.cjs; do node "$f"; done   # browser-asset harnesses
```

- Run the app against throwaway data, never the user's archive: set `XDG_DATA_HOME`,
  `XDG_CONFIG_HOME` and `XDG_CACHE_HOME` to a scratch directory, then `uv run tweetnook serve`
  (port 8000 by default; `--dev-tools` enables replay from another server).
- After changing Tailwind input or vendored JS, rebuild with `npm run build` in `dev/web/`.
- `docs/development/testing.md` maps each area to its test files.

## Where things live

`docs/development/module-reference.md` maps operations to modules, and
`docs/development/architecture.md` explains the data flow. In short:

- `tweetnook/core/` - errors, locks, X response parsing and extraction; `config.py` - settings.
- `tweetnook/client/`, `auth/`, `query_ids/` - X requests and credentials.
- `tweetnook/storage/` - the SQLite archive (`ArchiveStore`).
- `sync/`, `importer/` - collection runs, official-export import and the LanceDB migration.
- `enrichment/`, `tagging/` - follow-up work on archived posts, and automated tagging.
- `jobs/` - locked archive jobs, progress reporting, the worker supervisor and the scheduler.
- `tweetnook/web/` - FastAPI app; `routes/` are thin handlers over the same archive operations
  the CLI uses. `page.py` assembles the page from the `index.html` shell and per-feature
  markup in `fragments/`.
- `web/static/js/` - the browser UI: `app.js` builds the page's Alpine component from method
  factories in `features/`, `feed/` (with card rendering in `feed/render/`) and `media/`;
  `core/html.js` is the escaping template. `static/css/` has one stylesheet per feature,
  linked in cascade order.
- `cli/` - the Typer command tree, one module per command group.

Packages are layered: CLI and web, then features, then storage, then core. A module never
imports from a higher layer, and CI fails if one does; the module reference lists the layers.

Invariants that must hold:

- Source precedence is authenticated X, then FxTwitter, then official-archive content. A sparser
  payload never overwrites richer saved content.
- A timeline page's raw capture, normalized rows, memberships and cursor persist atomically.
- Imports and restored quotes never create collection membership on their own.
- Archive-changing work takes the process lock; the scheduler submits through the job
  supervisor rather than starting its own write path.

Put new code in the module, fragment or stylesheet for its feature rather than in `app.js` or
the `index.html` shell. CI fails on files over 1,200 lines:

- Split a file that approaches the limit along a real boundary (a stage, a feature, a separate
  concern), and keep moves separate from behavior changes.
- If it has no such boundary, or a fix pushes it over, add it to `SIZE_EXEMPT` in
  `scripts/check_structure.py` in the same commit, with the reason. Never split code only to
  meet the number.
- Modules that import each other's underscore names usually share one concern, so a split
  between them is suspect; `check_structure.py` lists those imports. A helper other modules
  are meant to use is public: name it without a leading underscore.

## Code

- Match the surrounding code's naming, idiom and comment density.
- Comments explain why: constraints, browser quirks, invariants. Don't restate the code, and
  don't describe what the code used to do.
- Commit messages explain why a change was made: the problem, the cause, and why this fix.
  They are where reasoning is recorded. Write them, and comments and docs, for a reader who
  never saw the session: no process IDs, scratch paths, tool names or step-by-step debugging.
- Feed cards are rendered as HTML strings outside Alpine (`static/js/feed/render/`). Build
  markup with the `html` template from `static/js/core/html.js`, which escapes every value;
  pass links through `httpURL`, and use `rawHTML` only for another renderer's output or a
  constant. Markup still built as plain strings needs `escapeHTML` or `safeURL`.
- Use the custom listbox dropdown pattern, not native `<select>` elements.
- In a dialog component (`x-data` on a fragment's root), a write to a name the dialog doesn't
  declare lands on the app. Declare the dialog's state as fields.
- New or changed CLI flags need explicit help text and a `--help` test.
- Prefer deleting dead code over keeping compatibility paths. Exceptions: the LanceDB
  migration and upgrades from released SQLite schemas (5 and later) still serve real users.

## Tests

- Compute expected values independently of the code under test. A test that calls the same
  calculator for its expectation proves nothing.
- A failure-injection test must fail inside the code path it claims to cover (for example,
  after the first database write, not before the transaction starts).
- Don't skip tests, loosen assertions or widen timeouts to get green. Find the cause.
- Don't assume a failure is pre-existing; check it on a clean `main`.
- Use offline fixtures and temporary archives. Live X or Gemini calls need the user's request.
- Node harnesses don't prove layout, gestures or video playback. Check those in a browser and
  say what was verified and what wasn't.

## Docs

- A doc needs updating when a change makes it false or misleading, or when a feature can't be
  used correctly without it. Missing optional detail doesn't count.
- User docs (`README.md`, `docs/*.md`): change them only for a substantial new user-facing
  feature or under the rule above. Keep the `docs/README.md` index in sync.
- Developer docs (`docs/development/`): keep them accurate. When a change makes them wrong,
  fix them in the same commit.
- Docs describe how things work now, in the present tense, with no dates, task logs or
  investigation notes. When you correct a doc, explain the correction in the commit message,
  not the doc. Mention history only when its reason still matters, and frame it as history:
  "We used X, but it caused Y, so now we do Z."
- `CHANGELOG.md` is updated during release work, following `docs/PUBLISH.md`.

## Boundaries

Ask first:

- New dependencies, schema migrations, or changes to persistence or source-precedence rules.
- Removing compatibility code, or anything that touches the user's real archive or server.
- Pushing, tagging, or publishing a release (follow `docs/PUBLISH.md`).

Never:

- Commit or log credentials, cookies, session files or databases, or put real account data in
  fixtures.
- Hand-edit `reference/` or `tweetnook/web/static/vendor/`. Use `reference/` only for X API
  details (query IDs, feature flags, cursors, headers), never for architecture.
- Run destructive git commands (`reset --hard`, `clean`, `checkout --`, `restore`) on work you
  didn't make.

## Git

- Other sessions and the user may have uncommitted work here. Leave changes you didn't make
  alone, and stage files explicitly (never `git add -A` or `git commit -a`).
- Commit each validated logical unit without waiting to be asked. Use conventional prefixes
  (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`) and an imperative summary.
- No co-author or agent attribution lines.
