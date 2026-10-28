# Web assets

TweetNook ships the JavaScript dependencies and generated CSS it needs. Users don't need Node or a CDN connection to open the web app. This directory holds the build inputs used by maintainers.

## Build

```bash
cd dev/web
npm ci
npm run build
```

The pinned package versions are in `package.json` and its lockfile: Alpine and its collapse/intersect plugins are 3.13.3, and Tailwind is 3.4.17. `build.cjs` copies browser bundles and license files into `tweetnook/web/static/vendor/`, then generates `static/css/tailwind.css` from the Tailwind inputs.

Review and commit generated assets when their source inputs or dependency versions change. Keep upstream license text intact. Do not edit a minified bundle as the primary source of a behavior change.

## Source files

The page structure lives in `tweetnook/web/index.html`. Browser behavior lives under `static/js/`, and application styles live under `static/css/`. The Tailwind build scans the configured source paths; update the inputs if a new template needs generated utilities.

The in-app cookie guide has a separate generator, run from the repository root:

```bash
uv run python scripts/build_setup_help.py
```

## Checks

Run the Python asset/setup-help tests and the relevant JavaScript harnesses from the repository root. Open the app without internet access to check that the local shell and bundled dependencies load. This doesn't imply every remote link or undownloaded asset is offline-ready.

Diagnostic scripts in this directory help inspect playback, media handoff, loading and browser behavior. Read a script's arguments and target before running it against an installation. Keep generated reports and test archives outside the repository.

See [testing](../../docs/development/testing.md) for the usual harness commands.

### iOS Safari scroll regression

With an iPhone simulator booted, start `safaridriver -p 4444` in another terminal,
then run `node dev/web/safari-scroll-check.cjs` from the repository root. Use
`SAFARI_DEVICE_UDID` to select a simulator when several are available. The script
serves synthetic posts and public demo videos on localhost, scrolls both ways
with audible autoplay, and asserts that decoder handoffs preserve scroll position
and never overlap audio. It saves measurements and a screenshot in a temporary
folder printed at completion. No archive or account is opened.

The test session enables Safari's autoplay test policy to avoid requiring a native
tap, then injects per-element audible refusals to exercise the authorized-decoder
fallback. It checks real Safari decoding, layout and scrolling; it does not establish
user-gesture permission or physical-device momentum behavior. Set
`SAFARI_SCROLL_CSS` to a saved copy of `tweetnook/web/static/css` to check a pre-fix
version.
