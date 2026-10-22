# Optional Twitter carousel: verification record

Verified on 2026-10-20. **Settings → Appearance → Media layout → Carousel**
changes inline multi-media tweets and expanded quoted galleries. Grid remains
the default. Single attachments and the existing fullscreen viewer retain their
layout. The choice is stored in this browser as `tvx-media-gallery`.

## Mobile fullscreen fixes (2026-10-22)

Reproduced the four reported viewer issues against the current source in an
isolated Chromium mobile fixture (390 × 844, touch and coarse pointer), before
changing their behavior:

- A 152 × 155 photo stayed at its intrinsic size. Phone photos now fill the
  available viewer box with `object-fit: contain`; motion uses the fitted picture
  so letterbox space does not distort the animation.
- A fresh tap immediately after closing was swallowed by the 500 ms dismissal
  guard. A new `touchstart` clears that guard, while a compatibility click without
  a new touch remains blocked. Other possible causes of intermittent missed taps
  were not established.
- Video opening and closing could end 29.5 px away from their resting picture.
  The animation positioned its surface using already lifted bounds, then applied
  the CSS lift again. Temporary absolute surfaces now suppress that translate;
  synchronous inline mounting sets the resting lift before measuring it.

Afterward, the small photo's visible picture measures 390 × 397.697 px; a tall
1080 × 2342 photo fits at 332.485 × 721 px. Paging from a landscape photo to the
small photo preserves that enlarged fit. Six opening/closing video handoffs
(portrait, a letterboxed portrait, and a real landscape clip) differ by at most
0.008 px across cleanup. A fresh post-close tap opens immediately, and the desktop
small photo remains 152 × 155. Browser console has no errors. Scoped validation:
238 asset cases, 25 playback cases, the four other JavaScript suites, and eight
Python web asset/playback tests pass.

Local before/after JSON, screenshots, and the reproduction harness are under
`design-assets/gallery-fixes-20261022/` (ignored by Git). These are browser
reproductions, not an X parity comparison or a physical iPhone verification:
the previously configured iPhone automation endpoint was unreachable.

## Date-based layout policy and improved selector

Appearance now offers illustrated native radio controls for **Grid**, **Carousel**,
and **By post date**. Existing `grid` and `carousel` preferences remain valid;
`date` is the new optional value. Grid remains the default. Native radio inputs
provide grouped keyboard navigation and accessible labels/descriptions.

Research found the [July 10, 2026 iOS announcement](https://x.com/dinkin_flickaa/status/2075693625149653331),
quoted in [contemporary July 11 coverage](https://news.denfaminicogamer.jp/news/260711d).
Its follow-up says Android and web were still to come. Later reporting describes
the [gradual rollout across clients](https://www.comnico.jp/we-love-social/x-carousel).
There is no single verified global activation date for every client/account.
Direct retrieval of the announcement returned HTTP 403 during research; the
announcement and follow-up are reproduced in the contemporary coverage.

The selected policy uses **2026-07-10 00:00:00 UTC**, the first publicly announced
launch day, as its calendar boundary. This is a TweetNook appearance policy,
not a claim that X changed all authors' layouts at that instant. Posts on or after
the boundary use carousel; earlier or undated posts use grid. It uses the original
post's date, including each quoted/reposted source, never collection or archive
time. All feed, parent, detail, reply, quote-list and split-panel media calls
receive their own tweet context. Single attachments and compact quote thumbnails
keep their existing behavior.

Scoped automated checks cover the UTC boundary and offsets, legacy dates,
missing/invalid dates, recently archived old tweets, independent quote/repost
dates, setting overrides, unchanged singles, and preference restoration.
The disposable demo includes seven additional old/new, quote and reply examples;
the published fixture is unchanged. Live UI results, source hashes, fixture
examples and responsive screenshots are retained locally under
`design-assets/gallery-date-20261020/`. This selector/policy change does not alter
the previously compared gallery dimensions or arrow behavior. Validation passed
all six JavaScript harnesses (221 asset cases), 17 scoped Python tests, and 15
live UI checks covering keyboard selection, restoration, mixed feeds, quotes,
replies, split panels, and the selector at 1200px / 518px / 390px widths in dark
and light appearance, plus removal of the rollout note at the user's request.

## Method and environment

Used the local `dev/web/media-parity-handoff.md` workflow: build a disposable
demo, overlay synthetic posts, and inject the same graph and identical media
bytes into X's client state. X's own React components rendered the reference.
No tweets were posted and no X account data was changed. The published demo
fixture and archive database were unchanged.

The tested source was based on `c9d9067`, with this feature's working changes.
`tested-source.json` in the evidence directory records SHA-256 hashes of the
tested application JavaScript, stylesheet, and HTML. All 13 synthetic assets
were hash checked: eight WebP photos and five MP4s, totaling 206,802 bytes.
The final regression checks also include the independent card-link correction
in `089e7ec`, which landed after the live captures; gallery code is unchanged.

Native Helium controls and DevTools were used, with Chrome 155's engine,
100% zoom, DPR 2, and light appearance. The existing browser profile included
Control Panel for Twitter, uBlock, and Bitwarden; this was not an extension-free
reference. Desktop viewport: 865 × 1091 CSS pixels, desktop UA, no touch,
fine pointer. Phone: 390 × 844, Android 16 / Pixel 10 Mobile Chrome 155 UA,
one touch point, coarse pointer, `userAgentData.mobile === true`. Both apps used
matching 600px desktop columns and 390px phone layouts.

## Applicable paired matrix

Only seven original handoff cases concern the requested multi-media layout.
Eighteen cases extend that coverage. Each of these 25 cases passed in feed and
focal detail on desktop and phone, on both X and TweetNook: **100 paired
comparisons, 200 site/case observations**, stored in eight result sets.

| Case IDs | Attachments or relationship | Result |
|---|---|---|
| `202610160041`, `044`, `047` | Direct 2 / 3 / 4 mixed-ratio photos¹ | Pass |
| `202610160043`, `046`, `049` | Quoted 2 / 3 / 4 mixed-ratio photos¹ | Pass |
| `202610160050` | Landscape photo + portrait video | Pass |
| `202610170001`–`003` | 2 / 3 / 4 portrait photos | Pass |
| `202610170004`–`006` | 2 / 3 / 4 square photos | Pass |
| `202610170007`–`009` | 2 / 3 / 4 landscape photos | Pass |
| `202610170010`, `011` | Two ultrawide / two panoramic photos | Pass |
| `202610170012`, `013` | Small + large photo / portrait + panorama | Pass |
| `202610170014` | Own four photos + quoted four photos | Pass |
| `202610170015` | Own single photo + quoted four photos | Pass |
| `202610170016` | Photo + portrait GIF entity | Pass |
| `202610170017` | Portrait + landscape videos | Pass |
| `202610170018` | Square photo + landscape video + portrait GIF | Pass |

¹ Abbreviated original IDs share prefix `202610160`.

The audit checks identical unique case sets, viewports, track counts, padding,
scroll widths, tile dimensions, decoded photos, and intrinsic video dimensions.
Every video/GIF reached `readyState === 4` and was individually observed playing
before measurement. X pauses earlier players when another starts, so individual
readiness records distinguish playback verification from the later snapshot.
Compact quote thumbnails are checked separately from the parent's inline strip.
X's photo wrapper excludes its 1px border; the audit compares outer tile frames.

| Context | Maximum gallery dimension difference, CSS px |
|---|---:|
| Desktop feed | 0.015 |
| Desktop detail | 0.008 |
| Phone feed | 0.008 |
| Phone detail | 0.016 |

Reviewed synthetic circles, corner labels, and borders for crop and stretching
alongside the geometry. Tiles use 8px corners, a 1px border, 4px gaps, common
heights, and centered cover cropping. Portrait/square pairs pack differently
from scrolling strips. Panoramic pairs keep X's trailing inset. Expanded quotes
have separate sizing and padding.

Six additional phone-detail quote probes cover 2 / 3 / 4 portraits, 2 / 3 squares,
and 3 landscapes. All six matched rounded track and tile dimensions. They caught
the 66% quoted portrait cap and minimum height for quoted square strips. These
are targeted probes, not additional full matrix rows. The handoff's unrelated
single-media cases are excluded from this feature's final matrix count.

## Interaction checks

Native arrow clicks in both apps advance one tile. For the four-photo desktop
detail case, the first step lands at 220.5px and the last clamps at 953px;
the appropriate end arrow is disabled. Controls use the measured 36px size,
hover opacity, and dark fill. Both apps use mandatory horizontal browser snap,
start alignment, matching scroll padding, normal snap stops, and horizontal
overscroll containment. Phone drags snap to tiles without opening a tweet or
viewer. Input velocity was not instrumented, so identical flick trajectories
are not asserted.

Additional local checks passed:

- Keyboard focus reaches the strip; Home and Right return to the start and
  advance one tile. Unit checks cover End, boundaries, reduced motion, and
  retaining a child player's arrow-key controls.
- Clicking the third photo opens viewer index 2. Quoted photos preserve their
  own viewer indices; quote arrows keep the parent tweet open.
- Compact quote thumbnails still open the quoted source.
- Missing downloads retain their slots and do not corrupt viewer indexing.
- Switching layout updates existing feed/detail content. The original grid
  survives a round trip unchanged; single-media markup is identical.
- Actual Appearance radio controls switch layout, and carousel restores after
  reloading a direct detail URL.
- Autoplay pauses an offscreen clip and starts the next visible clip as an
  overflowing three-video test strip scrolls. Paused playback stops all clips.
  This probe adds a third existing synthetic clip only to the disposable browser
  row; the two-video packed pair does not overflow.
- At a targeted 1200px responsive viewport, both split-panel columns render
  their carousels without horizontal page overflow. This is a local responsive
  check, separate from the paired desktop matrix.

## Evidence, regressions, and limits

### Arrow and motion correction after initial review

The initial geometry checks did not establish motion parity. A user review
caught two appearance errors: chevrons instead of X's full arrows, and controls
that were half opaque whenever the gallery was hovered. The initial version
also requested smooth arrow scrolling without comparing its timing to X.

A focused live follow-up measured X's exact SVG paths, 36px buttons / 20px SVGs,
18px outer tweet insets, dark fill, and pointer states. Revealed native controls
are fully opaque, dim to 0.5 while pressed, and return to 1 after release.
The corrected controls use those paths and states, and reveal without an opacity
fade. Keyboard focus still reveals the controls; pointer focus alone does not
keep them visible after leaving the gallery.

Frame-by-frame `requestAnimationFrame` samples compared native X with the local
demo in the same 865 × 1091 / DPR 2 Helium environment, with reduced motion off.
X calls `scrollIntoView({behavior: 'auto', block: 'nearest', inline: 'center'})`.
Its arrow moves were complete before the first recorded animation frame;
our original first step took 334.4ms. Arrow clicks now request automatic scrolling
instead of a smooth slide. Native browser snapping and swipe momentum remain
in place. Corrected first, middle, last, and reverse detail steps settled before
the first recorded frame, at 220.5, 677.5, 953, and 677.5px respectively.

Native physical pointer clicks and accessibility activations were both observed.
For unobstructed glyph captures, reference arrow wrappers were forced visible;
this did not change their native click handlers. Capture listeners sampled scroll
positions and temporarily logged native scrolling/focus calls. One physical
activation had two capture listeners, so the seven native trace records represent
six independent activations. Continuous swipe velocity/easing was not measured;
this follow-up establishes arrow-click timing, not universal gesture physics.

Follow-up evidence is in `design-assets/gallery-motion-20261020/`: native traces,
local before/after traces, `motion-probe.js`, `audit.json`, source hashes, raw AX
captures, arrow comparison crops, and the corrected preview screenshot. The
initial 100 geometry pairs remain the layout record; this focused follow-up did
not repeat unrelated layout rows. All six JavaScript harnesses (218 asset cases)
and the 17 scoped Python tests passed again.

Durable local evidence lives in
`design-assets/gallery-parity-20261020/` (ignored by Git). It includes
`manifest.json`, `fixture.json`, the 13 media files, generator/bootstrap/measurement
scripts, eight final `{x,demo}-{desktop,mobile}-{feed,detail}.json` sets,
`audit.py`, `audit.json`, interaction logs, quoted-boundary results, source
hashes, and original screenshots. `desktop-gallery-comparison.png` crops equal
gallery columns for review. Files labeled `baseline`, `exploratory`, or earlier
failed runs are retained separately and are not counted as final results.
Run `python3 design-assets/gallery-parity-20261020/audit.py` to repeat the saved
matrix audit. The local handoff and evidence remain untracked.

Regression checks: all six JavaScript harness files passed, including 218 asset
cases, 24 playback cases, and 19 article cases; 17 targeted Python web/demo
tests passed. JavaScript syntax and diff whitespace checks passed. The temporary
comparison tabs were closed, disposing of the injected reducers and their device
emulation; the loopback preview server was stopped.

This verifies the listed layouts and interactions in the recorded Helium
environment. Physical iOS/Android devices, Safari/Firefox, RTL, assistive
technology, and motion/audio-quality parity were not tested. The MP4s are silent
three-second synthetic clips, not a motion-quality benchmark. Fullscreen-viewer
redesign and the handoff's unrelated single-media matrix were outside scope.
