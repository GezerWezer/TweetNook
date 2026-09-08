# GitHub Pages demo internals

`data.json` contains synthetic placeholder posts only. `demo-api.js` deep-clones that fixture at
page load and intercepts TweetNook API requests in memory; content mutations are never persisted.
The fixture contains seven placeholder users, 22 root posts, and 56 contextual replies. The
showcases cover standalone text, text quoting text or media, media quoting text or media, images,
galleries, GIFs, and videos. Eight conversations include a nested original-poster response; ten base
examples use a plain-text direct reply with no original-poster continuation. Every liked post also
has two reusable text-only replies: one containing a quote tweet and one containing no quote. Only
three replies are themselves liked. The 15 liked records open with three guides, a two-image gallery,
a standalone video, and a four-image gallery. The guides introduce the demo, browsing/search, and
Appearance settings. Every record has two to five prompt-aligned search tags; media records also have
descriptions, while text-only descriptions remain empty. One standalone source post demonstrates a
synthetic community note. Invented media posters and thumbnails remain intentionally absent.

The fixture stores 78 records for complete conversation and quote context, but the **All Tweets**
feed exposes only the 26 records that belong to at least one user-facing collection: authored,
liked, or bookmarked. Context-only saved replies remain accessible inside their conversations.

The builder copies the complete `demo/demo media/` tree to the public artifact, so additional demo
assets placed there are included automatically. Every avatar and post-media URL referenced by the
fixture is validated against that source tree before the build succeeds; the fixture may not invent
poster or thumbnail files. Every supplied non-avatar media asset must appear in a feed-visible post,
and no such asset may be reused there more than twice.

Its three-minute Sync simulation is calibrated to a representative production trace: quick
Prepare, Bookmarks, and Likes passes; a detailed thread scan; paced Resurrection retries; skipped
Articles when none need refresh; then Media and URL processing. All IDs, hosts, and archive counts
shown by the simulation are synthetic.

Build the static artifact with:

```bash
python scripts/build_pages_demo.py --output _dist/pages --version 0.0.9 --base-path /tweetnook/
```

Treat every generated file as public. The builder validates the fixture and scans the artifact for
database, archive, credential, and obvious secret material before succeeding.
