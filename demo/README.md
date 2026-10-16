# GitHub Pages demo internals

The demo is being rebuilt from real tweets supplied by the user. `data.json`
currently contains three instructional tweets from the same TweetNook demo author,
eight selected real tweets, 344 retained replies, 10 quoted/context posts, and one
sample post from You. The intro explains the archive and navigation, then points
to Settings and mobile. Its shared author uses a local default-profile silhouette. The guides stay first in Likes, followed by the orange
gallery, practical-shot clip, QR joke, electronics article, runes image, fly-brain
video, mystery case, and Klarna. Future projections retain this curated order.
Three replies are bookmarked: the fly sketch, cursed-doll explanation and click-through
reaction. Other replies remain conversation context. Sources:

- [Aleks's international-orange post](https://x.com/aleksliving/status/1794948623257440377)
- [BlSCUlTS's mystery-case quote](https://x.com/BlSCUlTS/status/2108702633980575995)
- [CornDogs27USPS's QR-code post](https://x.com/CornDogs27USPS/status/2095945046193234399)
- [jbohnslav's neuroscience quote](https://x.com/jbohnslav/status/2097653960811094318)
- [YourAvgBellman's practical-shot quote](https://x.com/YourAvgBellman/status/2074192621291856126)
- [slothy_420's Klarna quote](https://x.com/slothy_420/status/2090954221445149180)
- [Zasorimm's runes quote](https://x.com/Zasorimm/status/2087274031749927040)
- [i2cjak's electronics article](https://x.com/i2cjak/status/2012988611570933773)

`demo-api.js` deep-clones the fixture at page load; mutations remain session-local.
The QR-code post `2095945046193234399` has the requested demo community note,
"this is true". This note, bookmarks, and the sample You post are deliberate demo
curation. They do not represent actions or a Community Note published on X.
Future projections preserve these curated fields and the shared intro author.

The actual SQLite archive and original downloads are retained locally in the
ignored `data/demo-content/` directory. Production `expand_threads` captured the
TweetDetail response, extractor/storage persisted its graph, and `download_media`
saved the photos, GIFs, videos, and their posters. `DemoSelection` records the curated local membership;
this does not change or claim the user's Likes on X. The avatar endpoint was
called explicitly for all retained real authors, with fetching enabled only in memory
for this job (the user's saved setting remains untouched).

The previous 99-record synthetic dataset lives in
`tests/fixtures/demo-synthetic.json` to preserve API regression coverage during
the rebuild. Its source media remains available for those tests. The builder
publishes only avatars, tweet media, and article imagery referenced by the
current public fixture, so unused showcase media is excluded from the site.

## Add supplied tweets

- Capture the selected tweets into an isolated local archive using the production
  fetching, extraction, storage, and enrichment pipeline. Keep the real database,
  raw captures, account credentials, and original downloads outside public output.
- Explicitly fetch author avatars; normal media fetching does not cover them.
- Generate the public fixture from the archive's normalized presentation data,
  retaining real tweet identities, text, relationships, and media. Keep the approved
  instructional copy first in Likes order.
- Compress publication copies of all media, including avatars, thumbnails, and
  article images. Retain original downloads in the private archive.
- Build and verify the public artifact before publishing.

From the repository root, with its dependencies and `ffprobe`/`cwebp` installed:

```bash
PYTHONPATH=. .venv/bin/python scripts/prepare_demo_content.py 'https://x.com/aleksliving/status/1794948623257440377'
```

The script reuses production captures on reruns and retains its selected IDs in
`data/demo-content/selected.json`. It reads the configured session in memory;
no credentials or database files are included in the public fixture. Additional
URLs append to the selection; their actual downloaded media requires review.
The publication path supports photos, GIFs, and videos with their downloaded
posters, plus full archived X Article bodies and compressed cover/inline images.
Article content uses the production Web projection; image IDs, alt text, and rich
text formatting are retained without publishing raw captures or private download paths.
Relationship traversal keeps selected conversations and quotes while
excluding unrelated promoted/recommended posts returned alongside TweetDetail.
Mention-only replies without media, links, quotes, tags, or descriptions are
omitted from the public fixture. A full manual review of the remaining 596
replies excluded 53 empty, unrelated, promotional or spam replies; reasons are
recorded in `reply-review.json` and reapplied on future imports. That first pass
missed a topical podcast promotion embedded as a quote. A second review of all
379 then-published replies and 13 unsaved context roots removed 25 more replies
and the now-unreferenced branded recap (78 explicit reply exclusions in total).
This pass read full reply/quote text and inspected all 71 unsaved photos and 33
video/GIF thumbnails; it was not a complete video-content audit. Its reviewed IDs,
new exclusions and reasons are recorded in the audit. Topical promotion is still
excluded when it merely repackages the original for a branded channel. The second
pass also removed 26 unused author avatars and the recap image (136,404 bytes).
A subsequent user-requested removal excludes all ten captured replies by
@Almskxld (six were published, including one bookmark). Their unused context root,
seven video/poster pairs and avatar were pruned too. This explicit request is
recorded separately from the quality review and reapplied on future projections.
The latest bookmark selection is exactly `2097587669198934196`,
`2108662185232679271` and `2107978256003510696` (duplicate input deduplicated).
Requested removal `2097889944408588708` also prunes its unused quoted branch;
the former deeper bookmarked laser-training reply is outside the one-hop cap.
The publication then keeps one direct reply level per saved or quoted/linked conversation. Deeper
branches are pruned unless needed to preserve a saved post, its ancestors or a
quoted object. Preserved ancestors do not reopen their sibling branches. All
bookmarks remain saved seeds, so their own direct replies are eligible too.
This is a demo publication policy; the complete private sync archive is retained. Relevant humor,
media-only reactions, substantive criticism and technical links remain. Saved
posts and replies needed as a parent or quote of retained content stay intact. Unused
authors and their avatars are excluded from the build. Full captures remain in
the private database. Missing required quote/parent objects are fetched through
production expansion within `sync.max_linked_depth`. Quoted posts also receive
their own TweetDetail conversation capture even when their embedded tweet object
is already archived; expansion repeats for quotes discovered in those replies.
Only user-supplied roots receive curated Likes membership. Captured raw quote IDs also
connect embedded results whose deeper relations have not yet been normalized.

Optimized photos, posters and article images use WebP quality 72/method 6 with
sharp YUV conversion to preserve colored edges. Avatars retain quality 78 and a
128px cap. Photos retain their existing resolution, capped at 1280px with no
upscaling. Newly optimized videos/GIFs use H.264 MP4, CRF 28/veryslow, preserving
the existing dimensions, frame rate (at most 30fps) and AAC 64kbps sound. Metadata
is stripped and MP4 fast-start retained. Existing efficient copies remain when a
candidate does not save at least 5% or fails the quality review. Originals are
always the encoding inputs; publication copies are never recompressed as inputs.

The curated fixture has **366 records and 355 author profiles**, including 344
replies, the three guides, eight selected real posts, 10 quote/context roots and
one You sample. All 11 Likes and three bookmarks remain. There are 18 published
expanded conversation anchors. Media comprises 77 tweet photos, 22 GIFs, 3 videos,
25 posters, 20 article images and 355 avatars: **502 unique assets totaling
11,296,564 bytes**. With JSON and shared Web UI assets, the static site is about
13.63 MB. Analytics and replay counts derive from these retained records/files.

Applying the one-hop policy removed 175 posts and 158 unreferenced publication
files, saving 30,334,810 bytes (54.46%) from the prior 55,703,998-byte media set.
That includes **102 excluded-author avatars / 279,384 bytes**. Avatars for retained
authors remain unchanged. Scope filtering happens before avatar fetching and
media encoding during projection, so future imports retain the cap and do not
recreate assets for excluded authors. Required parents and all saved/quoted
relationships remain valid. The builder publishes only referenced assets.

The earlier compression pass reduced the full pre-pruning selection from
65,714,407 to 55,703,998 bytes. It used original downloads, preserved dimensions,
frame rates, audio and fast-start, and accepted candidates within conservative
VMAF limits after full-duration sampling. Photo/video side-by-sides and full-size
article text/equipment crops were inspected. Those encodings are retained here.
The source downloads and complete captures remain private and unchanged; the
article still has 110 rich-text blocks and all 20 images. The 19 legacy-only media
assets needed by synthetic API tests are retained and never published.

The public projection preserves captured media/quote short-URL metadata. The demo
adapter restores those entities so the production text renderer hides redundant
`t.co` attachment links while keeping original tweet text and ordinary links.
Captured UTF-16 `display_text_range` boundaries are also retained so automatic
reply recipients stay separate from intentional @mentions in the message body.

The fixture validator checks required fields, relationships, local media paths,
and the guide posts without requiring synthetic usernames, a fixed archive size,
or invented replies/tags/descriptions to fill a showcase matrix.

Profile cards use archived public bios, follower/following counts and actual fetch
times. Distinct captured profile states are exported through the production
`author_profile_snapshots` projection, so the four authors with history can be
cycled through just like a local archive. Only normalized card fields are public;
raw captures remain private. Empty source bios remain empty.

The demo follows the device's light/dark appearance automatically until a theme is
selected in Settings. Split panel is the default on desktop; a saved layout
choice takes precedence, and narrow screens use a single column.
Only the 11 liked posts were sent to Gemini 3.6 Flash's provider Batch API, with
Google Search enabled and the production `tweetnook.tagging_prompts` prompts and
`tweetnook.tagging` schemas. Quoted media and the archived article body/images
were included as context. The three guides retain their `guide tweet` marker;
bookmarks, unsaved context, and the You sample received no generated tags.
The model returned 34 tags; three invalid outputs were retried through Batch,
for 14 requests across three jobs. Recorded usage is 58,789 input, 1,371 output
and 1,030 thinking tokens: 61,190 total. The token estimate is $0.02654775 using
official Batch rates. Search was enabled but no queries were reported; actual
Search charges and shared allowance usage are unknown. Sanitized provenance and
usage are in `demo_metadata.tagging`; provider uploads were deleted afterward.

For another reviewed tagging job, `scripts/tag_demo_likes_batch.py --help`
describes `prepare`, `submit`, `status`, `collect` and `cleanup`, plus `--output`.
Use the project Python environment and supply `GEMINI_API_KEY` through the process
environment. `submit` makes billable requests and refuses to submit twice when
private job metadata already exists. Requests/results remain under ignored
`data/demo-content/tagging/`; `collect` enforces production validation and never
edits the public fixture. Review results and apply only to their original liked
IDs, preserving guide markers. Failed validation requires a separately audited
Batch retry; never fill missing tags manually or silently use Flex.

Analytics measures this public fixture: post/profile/article/media-row counts,
collection dates, expanded targets, Likes-only tag coverage and unique referenced
asset bytes. The builder embeds a file-size manifest and exact JSON size; storage
reports the published JSON and media, without claiming a deployed SQLite database
or imaginary unavailable posts. Media rows include article imagery, while storage
counts shared files only once. Post URL counts use captured URL entities.

Sync remains an explicitly labeled three-minute **Demo replay**. Its phases follow
read-only production activity traces: Prepare, Bookmarks, Likes, Threads, unavailable
recovery, Articles, Media, URLs and Automated tags. Counts derive from the current
fixture; replay reports no downloads, imports or model/network requests. The page
opens with a completed replay and offers two clearly labeled local replay logs.
Production logs, hosts, account identifiers and raw requests stay private.

Build the static artifact with:

```bash
python scripts/build_pages_demo.py --output _dist/pages --version 0.0.9 --base-path /tweetnook/
```

Treat every generated file as public. The builder validates the fixture and scans the artifact for
database, archive, credential, and obvious secret material before succeeding.
