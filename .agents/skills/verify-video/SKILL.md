---
name: verify-video
description: Verify feed video behavior (autoplay, audio ownership, decoders, posters, carousels, the mobile viewer) with evidence. Use for any change to video or scroll handoff code in static/js.
---

# Verify video behavior

Video bugs here are timing bugs that tests miss and that depend on real Safari. Work
up this ladder and stop when the evidence matches the risk of the change.

## The behavior being checked

The full rules are in the autoplay paragraphs of `docs/development/web-and-jobs.md`,
and their constants are at the top of `tweetnook/web/static/js/app.js`. In short:

- A video starts once 25% of its picture is visible and keeps playing until less
  than 10% is visible.
- Playback follows the picture occupying more of the viewport, but only switches
  on a lead of at least 10 percentage points.
- At most one video is audible. Audio stops when its video leaves the viewport,
  including during fast scrolling.
- Only a small number of nearby videos are prepared ahead of time, so decoders
  stay bounded on phones.

## 1. Harnesses (always)

```bash
node tests/js/test_media_playback.cjs
node tests/js/test_web_assets.cjs
uv run pytest tests/web/test_media_playback_assets.py tests/test_web_assets.py
```

For a bug fix, add a playback case that fails on the code before the fix.

## 2. Real Safari, synthetic posts

Boot an iPhone simulator, run `safaridriver -p 4444`, then
`node dev/web/safari-scroll-check.cjs` (see `dev/web/README.md`). It uses synthetic
posts and public demo videos, never an archive.

Limits: it enables Safari's autoplay test policy, so it doesn't prove gesture
permission, and WebDriver scrolling isn't native momentum.

## 3. Before/after in Chromium (optional)

`dev/web/playback-comparison.cjs` compares the current `app.js` with a saved one
(`PLAYBACK_APP_SCRIPT`). It needs Playwright and `CHROMIUM_EXECUTABLE_PATH`.
Chromium timing doesn't establish Safari timing.

## 4. Native simulator measurement (for scroll and audio bugs)

Use this when the bug only shows with real touch scrolling.

1. Run a preview with the `preview-server` skill (replay install for a real-looking
   feed). Use the same route for the baseline commit and for the fix.
2. Drive Safari natively, not through WebDriver: tap Unmute with a real tap, keep
   the default autoplay policy, and scroll with native flicks and reversals.
3. Serve a temporary copy of the page that records, on every animation frame, each
   video's geometry (visible area, viewport position) and playback state (paused,
   muted, volume, `readyState`, whether `currentTime` advances). Record no text,
   URLs or account data. Never commit the recorder.
4. Compute the expected owner independently from the recorded geometry with the
   rules above. Don't call `app.js` functions to compute the expectation.
5. Compare: ownership agreement, frames with audio from an offscreen video, frames
   with two audible videos, how long a stale owner kept playing, and startup gaps.

Afterwards, remove the recorder, stop what you started, and keep recordings outside
the repo.

## 5. Physical phone

Only the user can confirm behavior on a real device. Ask them when it matters.

## Reporting

- Give the numbers for each rung you ran and the commit each was run against.
- Recorded playback state is not sound output. Say "no audible offscreen playback
  was sampled", not "no audio played".
- List what wasn't verified, such as physical momentum or gesture permission.

## Docs gaps

If the steps above or the docs they point to are wrong or missing something you
needed, stop and propose the doc change to the user before continuing. Don't edit
the docs until they agree.
