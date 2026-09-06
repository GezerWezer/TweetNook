# Offline web assets

The Python package ships the browser runtime and precompiled styles. Users do
not need Node or internet access to load the UI from a running local server.

To rebuild after changing HTML or JavaScript classes:

```sh
cd dev/web
npm ci --ignore-scripts
npm run build
```

Commit the lockfile and generated `tweetnook/web/static/css/tailwind.css` and
`tweetnook/web/static/vendor/` assets. Tailwind scans the HTML and application
JavaScript; use complete utility class strings, not constructed class names.
Never run the asset build on server startup or in the user's browser.

Pinned dependencies: Tailwind CSS 3.4.17 and Alpine.js, Intersect, and Collapse
3.13.3. The build copies their MIT licenses beside the generated assets.
Alpine's npm packages omit the license, so `alpine.LICENSE.md` is retained from
[the v3.13.3 source](https://github.com/alpinejs/alpine/blob/v3.13.3/LICENSE.md).

Fonts are system/local only. Installed-font enumeration is optional and only
invoked from a user click in a supporting secure-context browser. Font names
are not uploaded to the server. Unsupported browsers can use the fallback font
choices. The custom dropdown previews each family in its own typeface.

An optional cold-browser integration check blocks every external request and uses
fixture APIs (no real archive or account). With Playwright installed separately:

```sh
node dev/web/offline-smoke.cjs
```

Run this command from the repository root. Set `CHROMIUM_EXECUTABLE_PATH` to a
Chromium executable if Playwright's browser is not installed.
