---
name: ui-proxy
description: Test this checkout's web UI on a real TweetNook archive by serving local HTML, JS and CSS in front of a running server. Use when a UI change needs real data and the user asks for it.
---

# Test the local UI on a real archive

`scripts/ui_proxy.py` serves the page and `/static/` files from this checkout and
sends every other request (API, media, event streams) to a running TweetNook server.
The browser runs the local UI on that server's archive.

## Before starting

- **Ask the user first, every time.** The UI acts on their real archive: tag
  edits, deletions, settings and jobs started from the page all change it. Unless
  they say otherwise, only browse and play media.
- Ask which server to use. Never guess its address or reuse one from history.
- Check that the change is UI-only. The server runs its own Python code, so local
  JS that needs new or changed API routes will fail against it. Ask which version
  the server runs and compare it with the routes the change uses.

## Run it

From the repository root:

```bash
uv run python .agents/skills/ui-proxy/scripts/ui_proxy.py --target http://HOST:8000
```

Open `http://127.0.0.1:8001/` and log in with the server's own password. The
server still checks every page request, so the proxy grants no access the server
wouldn't.

To test on a phone, add `--bind` with this machine's LAN address and open that
address on the phone. Only do this on a network the user trusts: if the server has
no password, anyone who can reach the proxy can read the archive.

## Check it's serving the checkout

- Page and `/static/` responses carry `X-TweetNook-Preview: local-ui` and
  `Cache-Control: no-store`; API and media responses come from the server unchanged.
- Asset URLs carry a hash of the local file, so a reload picks up saved edits
  without a restart, even if the browser cached the server's own copies.
- Deep links (`/post/<id>`, `/post/<id>/quotes`) also get the local page. If the
  server gains another page route, add it to `PAGE_PATH` in the script.

Then check the change at desktop width and on the phone. For video, autoplay or
scrolling, follow the `verify-video` skill.

## Clean up

- Stop the proxy you started, by its process ID.
- Report what you checked and anything you did that changed the archive.

## Docs gaps

If the steps above or the docs they point to are wrong or missing something you
needed, stop and propose the doc change to the user before continuing. Don't edit
the docs until they agree.
