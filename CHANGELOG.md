# Changelog

This changelog records user-visible changes to TweetNook. The project follows
semantic-style versioning while it is in the `0.x` series.

## [Unreleased]

### Fixed

- Prevented uncached author avatars from scanning the full post archive and
  stalling reply navigation and pagination. Disabling avatar fetching now skips
  database candidate lookup while continuing to serve cached avatars.
- Disabled Python's prepared-statement cache on the shared archive connection
  to avoid inconsistent results and SQLite errors during concurrent requests.

## [0.1.3] - 2026-09-10

### Changed

- Kept complete saved conversation trees responsive with one indexed recursive
  lookup, cycle protection, bounded visual indentation, and a 250-descendant
  response cap that is disclosed when reached.

### Fixed

- Fixed archived conversation views omitting replies below the first level or
  replies from non-original-post authors, so every saved descendant is shown in
  its reply hierarchy.

### Validation

- `UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check`
- `uv run ruff check`
- `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q`
- `node --test tests/js/test_web_assets.cjs`
- `node --test tests/js/test_demo_api.cjs`
- `git diff --check`
- `uv build --clear`
- `uvx --from twine twine check dist/*`
- `uv run --isolated --no-project --with dist/tweetnook-0.1.3-py3-none-any.whl -- tweetnook --help`

## [0.1.2] - 2026-09-10

### Added

- Added a paged, segmented Web pipeline activity drawer with state-aware
  progress cards, live sync outcome metrics, and an Analytics-style run summary.

### Changed

- Scaled the static Pages demo’s Analytics data to its synthetic archive while
  preserving production category proportions and fixture-specific tag data.
- Sorted Analytics archive-status reasons and rows by descending unavailable
  tweet count, and recorded the latest cold-read performance findings for
  future optimization work.

### Fixed

- Fixed video duration overlays across standalone and gallery media so they
  synchronize with loaded metadata, show live remaining playback time, and
  reach `0:00` at the end of playback.
- Fixed resurrection thread expansion so recovered tweets retain the complete
  TweetDetail context and stale expansion markers are retried after recovery.

### Validation

- `UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check`
- `uv run ruff check`
- `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q`
- `node tests/js/test_web_assets.cjs`
- `node tests/js/test_demo_api.cjs`
- `git diff --check`
- `uv build --clear --out-dir /tmp/tweetnook-release-0.1.2`
- `uvx --from twine twine check /tmp/tweetnook-release-0.1.2/tweetnook-0.1.2.tar.gz /tmp/tweetnook-release-0.1.2/tweetnook-0.1.2-py3-none-any.whl`
- `uv run --isolated --no-project --with /tmp/tweetnook-release-0.1.2/tweetnook-0.1.2-py3-none-any.whl -- tweetnook --help`

## [0.1.1] - 2026-09-08

### Highlights

- Made archive browsing, filtered search, Likes pagination, enrichment, and
  bounded exports substantially faster and more responsive on large archives.
- Improved archive reliability by preserving full-text search data during
  updates and recovering profile avatars after temporary lookup failures.
- Added saved video durations and GIF playback controls across feeds, galleries,
  quotes, and conversation views.
- Made background operation more dependable with correct randomized schedule
  advancement and managed-service restarts after reinstall.
- Smoothed Web interactions with contained Analytics scrolling, lighter sync
  drawer transitions, and cleaner mobile controls.

### Changed

- Feed and Likes continuation now use indexed cursors and safely recover when
  the underlying like order changes.
- Filtered search and enrichment work now avoid unnecessary materialization and
  hydration, while preserving complete result sets and existing query behavior.
- Archive writes now repair derived full-text documents, preserve stable rowids,
  batch live-graph lookups, and maintain relevant SQLite planner statistics.

### Fixed

- Fixed randomized daily, weekly, and monthly schedules so consumed and overdue
  occurrences advance to the next valid run.
- Fixed Analytics modal scroll chaining into the page background.
- Fixed profile-avatar misses becoming permanent after a transient failure.
- Fixed managed-service reinstall leaving active Python processes on stale code.

### Validation

- `UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check`
- `uv run ruff check`
- `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q`
- `node tests/js/test_web_assets.cjs`
- `node tests/js/test_demo_api.cjs`
- `git diff --check`

## [0.1.0] - 2026-09-07

### Added

- Added `sudo tweetnook update` for safely upgrading TweetNook-managed systemd
  installations with service ownership checks, preflight validation, and
  restart-on-failure protection.
- Added canonical, copyable Web URLs for archived posts and quote-tweet lists,
  including browser Back/Forward behavior and responsive split-panel routing.
- Added a static GitHub Pages demo with realistic synthetic posts, media,
  conversations, search, settings, tagging previews, statistics, and a simulated
  sync run. Tagged releases deploy the demo after PyPI publication and GitHub
  Release creation.

### Changed

- Web text search now evaluates the complete SQLite FTS result set before
  pagination instead of stopping at 1,000 candidates. It preserves boolean and
  structured filters, collection membership, all sort modes, deduplication, and
  stable random ordering while hydrating only the displayed page.
- Automated-tagging candidate selection now streams index-ordered saved and
  quoted posts and stops as soon as a batch is full, avoiding archive-sized
  temporary sorts.
- LanceDB is now installed only through the `legacy-migration` extra. Direct
  PyArrow, unused Loguru setup, and the unused Mypy development dependency were
  removed from the default dependency graph.
- Automated Tagging defaults now match the documented conservative settings:
  Google Search starts disabled, the free daily limit is 20, and paid requests
  use Flex processing.
- Setup and maintenance documentation was condensed around the current
  server-first workflow.

### Fixed

- Fixed quote-tweet rendering inside direct and nested thread replies.
- Restored keyboard and button navigation in the full-screen media gallery.
- Kept the activity drawer beneath Settings and Setup dialogs.
- Prevented hidden Settings checkboxes from scrolling modal content out of view.
- Added a fallback for copying raw tweet JSON when the modern Clipboard API is
  unavailable or rejects the request.

### Validation

- `UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check`
- `uv run ruff check`
- `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q`
- `node tests/js/test_web_assets.cjs`
- `node tests/js/test_demo_api.cjs`
- `uv build --clear --out-dir /tmp/tweetnook-release-0.1.0`
- `uvx --from twine twine check /tmp/tweetnook-release-0.1.0/tweetnook-0.1.0.tar.gz /tmp/tweetnook-release-0.1.0/tweetnook-0.1.0-py3-none-any.whl`
- `uv run --isolated --no-project --with /tmp/tweetnook-release-0.1.0/tweetnook-0.1.0-py3-none-any.whl -- tweetnook --help`

## [0.0.9] - 2026-09-06

### Added

- Initial TweetNook release: a self-hosted Web archive for Twitter/X bookmarks,
  likes, authored tweets, and official account exports.
- Added server-first setup, local SQLite/SeekDB storage, full-text search,
  conversation and quote browsing, media archiving, scheduling, activity logs,
  and optional automated tagging.
- Added PyPI Trusted Publishing through the tagged GitHub Actions release
  workflow.

[0.1.3]: https://github.com/gezerwezer/tweetnook/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/gezerwezer/tweetnook/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/gezerwezer/tweetnook/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/gezerwezer/tweetnook/compare/v0.0.9...v0.1.0
[0.0.9]: https://github.com/gezerwezer/tweetnook/releases/tag/v0.0.9
