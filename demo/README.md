# Demo

The public demo uses the real TweetNook browser UI with a curated fixture and bundled media. It runs as a static site, so visitors can browse without installing TweetNook, signing into X, or connecting a server archive.

## Local preview

From the repository root:

```bash
python scripts/build_pages_demo.py --output /tmp/tweetnook-demo --base-path / --version dev
python -m http.server 8080 --directory /tmp/tweetnook-demo
```

Open `http://localhost:8080`. For GitHub Pages under the repository path, pass `--base-path /tweetnook/`. The release workflow supplies the release version and repository base path automatically.

The builder packages the UI, fixture/API adapter, local media, version and route fallbacks. It validates fixture fields and relations, checks media destinations, and scans output for secrets/private artifacts. Don't disable those checks to make a private export publishable.

## Fixture content

`data.json` defines the demonstration data. `config.json` holds public demo defaults. Authors, posts and their relationships need matching IDs. The schema includes collection membership, liked order, engagement, hashtags, mentions, URLs, tags/descriptions, media and optional Articles/Community Notes.

All images, posters, videos, GIFs and avatars must resolve to approved `demo/media/` assets in the built site. Keep source fixture paths under `demo/demo media/` and use the builder's mapping. Raw account captures, cookies, keys, database files, archives and local home paths don't belong in the demo.

`scripts/prepare_demo_content.py` can prepare selected content, but its output still needs deliberate review. Use public content you have chosen for the demonstration, and review each item before publication.

## Demo behavior

`demo-api.js` handles supported read requests against the fixture. Search, sorts, collection views and detail/Article presentation should be exercised against the same contracts as the server where supported. Appearance preferences can remain local to the visiting browser.

Real server actions such as importing, syncing, scheduling and Gemini requests are disabled in the static demo. A clearly labeled local Demo replay can simulate progress and logs; manual fixture changes remain session-local. The demo shouldn't send a visitor's request to a live personal TweetNook server or imply that a simulated action saved account data.

## Tests

```bash
uv run pytest tests/test_pages_demo.py tests/test_demo_content.py
node tests/js/test_demo_api.cjs
```

Also check direct post links, refresh on a detail page, media playback, Article views, empty searches and operation under a non-root base path. The static demo is separate from [developer replay](../docs/development/web-and-jobs.md#developer-replay), which uses a second running TweetNook installation.

## Content preparation

The production capture pipeline feeds a private archive under ignored `data/demo-content/`. It retains original downloads, selected IDs and review decisions. The projection only publishes approved normalized content and referenced assets. It fetches author avatars explicitly; ordinary media downloading doesn't include them.

```bash
PYTHONPATH=. .venv/bin/python scripts/prepare_demo_content.py 'https://x.com/USERNAME/status/POST_ID'
```

Replace the URL placeholders with a public post chosen for the demo. This uses configured account access and needs the project environment plus `ffprobe`, `ffmpeg` and `cwebp`. New URLs add selected roots. Review the resulting posts, conversation context and media before building. Fixture memberships are local demo choices and do not change likes or bookmarks on X.

The preparation script applies reply exclusions from `demo/reply-review.json` while retaining the context required by selected posts. Its media helpers define the publication encoding settings. Encode publication copies from the original downloads and keep the originals for future changes; repeatedly recompressing published copies loses quality.

Adapter tests use the synthetic fixture at `tests/fixtures/demo-synthetic.json`. Publish only current referenced files, including Article imagery and captured profile-history states. Preserve original short-URL entities and UTF-16 visible ranges so the normal text renderer can distinguish attachment links and reply prefixes.

## Tagging and analytics

`scripts/tag_demo_likes_batch.py --help` documents prepare, submit, status, collect and cleanup. This is a separate provider Batch workflow; it isn't the ordinary Free/Paid tagging path. Submission is billable and uses an environment-supplied key. Private job files stay under ignored data, and collect validates output without silently editing the public fixture. Review and apply results only to the original selected liked IDs.
