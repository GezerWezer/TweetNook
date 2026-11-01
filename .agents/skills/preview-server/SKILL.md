---
name: preview-server
description: Run TweetNook locally against throwaway data and check a UI or server change in a browser. Use when a change needs to be seen working, not just tested.
---

# Preview a change

Never point a preview at the user's real archive or server. Every mode below uses
its own scratch data. To test the UI on a real archive, use the `ui-proxy` skill,
and only when the user asks.

## 1. Pick a mode

| Mode | Use when | Data |
|---|---|---|
| Empty install | Setup, first run, settings, anything that works without an archive | None until you add it |
| Replay install | The change needs a realistic archive (feed, threads, media, search) | Read through another TweetNook server's replay source |
| Static demo | Read-only UI checks that the public demo also exercises | The packaged demo fixture |

### Empty install

```bash
SCRATCH=$(mktemp -d)
XDG_CONFIG_HOME=$SCRATCH/config XDG_DATA_HOME=$SCRATCH/data XDG_CACHE_HOME=$SCRATCH/cache \
  uv run tweetnook serve
```

Open `http://127.0.0.1:8000/` for Setup. The server listens on all interfaces
(`0.0.0.0`) by default, so other devices on the network can reach it while it runs.
Don't enter real X cookies; without them the install can't sync from X.

### Replay install

Start the empty install with `--dev-tools`, then connect it to a replay source in
Setup. `docs/development/web-and-jobs.md` ("Developer replay") has the steps,
including how to simulate a connected account and request failures.

A replay source is another person's server. Ask the user which one to use and for
its password; never guess or reuse one from history.

### Static demo

```bash
uv run python scripts/build_pages_demo.py --output "$SCRATCH/demo" --base-path /
python3 -m http.server 8790 --bind 127.0.0.1 --directory "$SCRATCH/demo"
```

Open `/` and navigate from there. Deep links such as `/post/<id>` return 404 here,
because they rely on GitHub Pages serving `404.html` as a fallback.

## 2. Check the change

- Reload after editing JS, CSS or `index.html`; the server serves the checkout as is.
- Exercise the change at desktop width and at a phone viewport (390 x 844).
- Read the browser console and server output for errors.
- For autoplay, audio, scrolling or the mobile viewer, use the `verify-video` skill.

## 3. Report and clean up

- Say what you verified, how, and what you couldn't (for example, a physical phone).
- Stop the servers you started, by the process IDs you started, and delete the
  scratch directory.

## Docs gaps

If the steps above or the docs they point to are wrong or missing something you
needed, stop and propose the doc change to the user before continuing. Don't edit
the docs until they agree.
