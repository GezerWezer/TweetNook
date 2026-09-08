# Development guide

[User guide](../README.md) · [Module reference](module-reference.md) · [Testing](testing.md)

tweetnook is a Python application with a SQLite archive, a command-line
interface, and a local web app. This guide explains how its components fit
together and where to make changes.

For installation and everyday use, start with the [user guide](../README.md).

## Set up a development environment

Use Python 3.12 or newer on macOS or Linux. Install the locked dependencies:

```bash
uv sync --locked
```

Run the standard checks:

```bash
uv run ruff format --check
uv run ruff check
uv run pytest -q
node tests/js/test_web_assets.cjs
```

Node.js is needed for the JavaScript asset harness. For optional tagging work,
install with `uv sync --extra automated-tagging`, then run normal `uv run tweetnook` commands.
For legacy LanceDB migration tests, add `--extra legacy-migration` to that command.
Use temporary archives and mocked provider calls in tests; see [Testing](testing.md).

## Understand the application

Start with [Architecture](architecture.md), then choose the area you are changing:

| Area | Guide |
|---|---|
| Twitter/X authentication, requests, and timeline sync | [Client and sync](client-and-sync.md) |
| Database schema, transactions, and merge rules | [Storage](storage.md) |
| Official Twitter/X archives | [Archive import](archive-import.md) |
| Tweet extraction, threads, media, and availability | [Enrichment](enrichment.md) |
| Query parsing and archive read APIs | [Search and web API](search-and-web-api.md) |
| Server lifecycle, routes, and worker processes | [Web and jobs](web-and-jobs.md) |
| Recurrence and persisted schedule state | [Scheduler](scheduler.md) |
| Gemini requests, prompts, and usage accounting | [Automated tagging](automated-tagging.md) |
| Settings, environment variables, and paths | [Configuration and paths](configuration-and-paths.md) |

The [module reference](module-reference.md) links source files to their
responsibilities and relevant tests.

## Preserve these contracts

- Each database belongs to one Twitter/X account.
- A tweet's collection memberships are separate from its shared content record.
- A timeline page saves its raw response, extracted records, and next position
  in one transaction. Later failures leave completed pages intact.
- Long-running web jobs use the same CLI pipelines as terminal commands.
- The scheduler runs only during the web server's lifetime.
- Core startup works without Gemini's optional dependencies.
- The active store is SQLite with FTS5; the optional `legacy-migration` extra supports legacy imports.

Lock coverage, merge precedence, and failure handling are detailed in
[Architecture](architecture.md) and [Storage](storage.md).

## Repository workflow

Follow [AGENTS.md](../../AGENTS.md). Read [WORKLOG.md](../../WORKLOG.md) and the
[implementation checklist](../../docs/IMPLEMENTATION.md) before starting work,
and inspect the working tree so unrelated edits remain intact. Keep reference
snapshots and frozen planning documents unchanged.

When behavior changes, update its user guide and relevant reference page.
New CLI options need explicit help and help-test coverage. Run the checks
appropriate to the change before committing a completed unit of work.

## Documentation and releases

These guides describe the implementation in this checkout. Older design
material also describes retired backends and features; check the code and tests
when a behavior differs, and follow the repository's process instructions for
resolving plan changes.

The revised documentation currently lives in `README.md` and `docs/`.
Package metadata still selects the original README. [Release](release.md)
explains how to promote and package the new documentation; the
[documentation review record](documentation-audit.md) records this set's scope
and validation.
