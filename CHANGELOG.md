# Changelog

This changelog records user-visible changes to TweetNook. The project follows
semantic-style versioning while it is in the `0.x` series.

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

[0.1.0]: https://github.com/gezerwezer/tweetnook/compare/v0.0.9...v0.1.0
[0.0.9]: https://github.com/gezerwezer/tweetnook/releases/tag/v0.0.9
