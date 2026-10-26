# TweetNook

TweetNook archives a person's Twitter/X likes, bookmarks, tweets and official X exports into a
local SQLite database and serves them through a self-hosted web app. It is a Python 3.12 package
(FastAPI, Typer) with a browser UI built from Alpine.js and plain JavaScript, with no JS build step.

## Commands

```bash
uv sync --group dev                       # add --extra automated-tagging / legacy-migration when needed
uv run pytest tests/test_storage.py       # run the suites for what you changed while iterating
uv run pytest                             # full suite (~75s) before committing a cross-cutting change
uv run ruff check . && uv run ruff format --check .
node tests/js/test_web_assets.cjs         # browser-asset harnesses; see tests/js/ for the others
```

- Run the app against throwaway data, never the user's archive: set `XDG_DATA_HOME`,
  `XDG_CONFIG_HOME` and `XDG_CACHE_HOME` to a scratch directory, then `uv run tweetnook serve`
  (port 8000 by default; `--dev-tools` enables replay from another server).
- After changing Tailwind input or vendored JS, rebuild with `npm run build` in `dev/web/`.
- `docs/development/testing.md` maps each area to its test files.

## Where things live

`docs/development/module-reference.md` maps operations to modules, and
`docs/development/architecture.md` explains the data flow. In short:

- `tweetnook/client/`, `extractor.py` - X requests and response parsing.
- `tweetnook/storage/` - the SQLite archive (`ArchiveStore`) and the LanceDB migration.
- `sync.py`, `pipeline.py`, `archive_import.py` - collection runs and official-export import.
- `tweetnook/web/` - FastAPI app; `routes/` are thin handlers over the same archive operations
  the CLI uses. `static/js/` and `index.html` are the browser UI.
- `cli.py` - the Typer command tree.

Invariants that must hold:

- Source precedence is authenticated X, then FxTwitter, then official-archive content. A sparser
  payload never overwrites richer saved content.
- A timeline page's raw capture, normalized rows, memberships and cursor persist atomically.
- Imports and restored quotes never create collection membership on their own.
- Archive-changing work takes the process lock; the scheduler submits through the job
  supervisor rather than starting its own write path.

The codebase is being restructured out of a few very large files (`static/js/app.js`,
`storage/backend.py`, `cli.py`, `index.html`). Put new code in a focused module for its
feature instead of growing those files, and keep moves separate from behavior changes.

## Code

- Match the surrounding code's naming, idiom and comment density.
- Comments explain why: constraints, browser quirks, invariants. Don't restate the code, and
  don't describe what the code used to do.
- Commit messages explain why a change was made: the problem, the cause, and why this fix.
  They are where reasoning is recorded. Write them, and comments and docs, for a reader who
  never saw the session: no process IDs, scratch paths, tool names or step-by-step debugging.
- Feed cards are rendered as HTML strings outside Alpine. Pass every archived value through
  `escapeHTML` or `safeURL`.
- Use the custom listbox dropdown pattern, not native `<select>` elements.
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
