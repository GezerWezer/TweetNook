# Development

TweetNook is a Python application with a bundled web app. You can run the server directly from a checkout. Node is needed for the browser test harnesses and for rebuilding bundled browser dependencies and CSS. The server runs without Node.

## Setup

```bash
git clone https://github.com/gezerwezer/tweetnook.git
cd tweetnook
uv sync --group dev
uv run tweetnook --help
```

Use Python 3.12 or newer. Add `--extra automated-tagging` or `--extra legacy-migration` to `uv sync` when testing those optional features. Keep test credentials and archives outside Git.

Run a separate development archive by giving the process its own XDG directories:

```bash
XDG_CONFIG_HOME=/tmp/tweetnook-dev/config \
XDG_DATA_HOME=/tmp/tweetnook-dev/data \
XDG_CACHE_HOME=/tmp/tweetnook-dev/cache \
uv run tweetnook serve
```

The web app starts before an archive exists. Complete Setup in the browser, or use the replay tools described in [web and jobs](web-and-jobs.md) to exercise another archive without contacting X.

## Guides

| Area | Guide |
| --- | --- |
| Components and request flow | [Architecture](architecture.md) |
| Files, defaults, overrides, secrets | [Configuration and paths](configuration-and-paths.md) |
| Tables, record identities, migrations | [Storage](storage.md) |
| X requests and resumable timelines | [Client and sync](client-and-sync.md) |
| Official export ingest | [Archive import](archive-import.md) |
| Threads, media, links, Articles | [Enrichment](enrichment.md) |
| FxTwitter, checks, history, restoration | [Availability checks](availability-checks.md) |
| Query language and HTTP routes | [Search and web API](search-and-web-api.md) |
| Server lifecycle, Setup, jobs, replay | [Web and jobs](web-and-jobs.md) |
| Automatic sync and availability checks | [Scheduler](scheduler.md) |
| Gemini requests and usage accounting | [Automated tagging](automated-tagging.md) |
| Module ownership | [Module reference](module-reference.md) |
| Tests and validation | [Testing](testing.md) |
| Builds and publication | [Release process](release.md) and [publishing checklist](../PUBLISH.md) |

## Contributing

Read the repository's agent/contributor instructions and current work tracking first. Keep a change small enough to test in one useful unit. Run targeted tests, review the diff, and commit only the files belonging to that change.

Update the user guides when behavior changes. New CLI options need explicit help text and representative help tests. The cookie instructions in the getting-started guide also feed the in-app Setup help; regenerate that asset after editing them.

Keep architectural choices specific to TweetNook. Preserve third-party licenses and release history.
