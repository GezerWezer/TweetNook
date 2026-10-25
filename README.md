# TweetNook

TweetNook is a self-hosted archive for your Twitter/X bookmarks, likes, and tweets that shows them almost exactly as they look on Twitter, including threads, quoted tweets, photos, videos, and Articles. Import your official Twitter archive, pull in new bookmarks and likes automatically, and search by words, author, date, or tag from any device on your network. It runs on your own computer or server and stays fast even with years of posts. TweetNook is a fork of [TweetXVault](https://github.com/lhl/tweetxvault).

**[Try the demo](https://gezerwezer.github.io/TweetNook/)** and browse a sample archive of real tweets.

## Features

- Import your official X archive to bring in your full history of tweets and likes, then let TweetNook fill in the details and download media the archive leaves out.
- Collect new bookmarks, likes, and your own tweets automatically on a schedule you choose, or start a sync from the browser and watch its progress.
- Everything you save stays on your own disk, photos and videos included. TweetNook can flag posts that later disappear from X, so you can browse the ones you preserved.
- Read saved threads, quoted posts, and Articles the way they appeared on X, with photos, videos, GIFs, link previews, and Community Notes. TweetNook fills in the rest of a thread even if you only saved one post from it.
- Find anything in seconds by searching words, authors, dates, post type, or media type. Sort likes by the order you liked them, or shuffle to rediscover old saves.
- Organize posts with your own tags and descriptions, or use optional automated tagging to find images and videos by what they show.
- Browse from your phone or desktop behind an optional password, customize themes, fonts, and layouts.
- Don't need the web app? Use TweetNook entirely from the command line.

## One-Step Install

Run this command to install TweetNook. It sets up Python for you and asks how you want TweetNook to start and who can access it.

```bash
bash -c "$(curl -fsSL https://github.com/gezerwezer/tweetnook/releases/latest/download/install.sh)"
```

On Linux with systemd, choose the background option to have TweetNook start at boot. Otherwise, start it yourself with:

```bash
tweetnook serve
```

Then open the address shown in the terminal, and complete the quick web setup. Import your archive or start fresh, connect your X account, choose what to sync, and set a web password. If you only want to browse an imported archive, with all media downloaded locally, you can skip connecting X.

### Install with pip

For a macOS or Linux server with Python 3.12 or newer:

```bash
python -m pip install tweetnook
tweetnook serve
```

You can use the web app from a browser on other operating systems, including Windows. Native Windows server installations are not supported. See the [getting started guide](docs/getting-started.md) for installation and connection steps, including [command-line setup](docs/getting-started.md#command-line-setup).

## Imports

An official X archive is a good starting point for older history. Request your data archive from X, then upload the ZIP during Setup.

The import brings in your tweets, likes, and some exported media. Some likes contain only a link or a little text; connecting your account lets TweetNook fill in what X still makes available.

To import entirely from local files without contacting X:

```bash
tweetnook import x-archive /path/to/twitter-archive.zip --offline
```

See [imports](docs/importing.md) for the download steps, supported archive contents, and ways to continue an interrupted import.

## Updates

For a managed Linux background service, run:

```bash
tweetnook update
```

TweetNook updates and restarts the service, keeping your archive, settings, and installed add-ons. For macOS or manual installations, follow the [update steps](docs/maintenance.md#updates-and-add-ons).

## Syncing

A sync uses your X account to copy your newest bookmarks, likes, or tweets into your archive.

Run a sync on demand, or schedule it to run automatically. Syncs run on the computer where TweetNook is installed, not in your browser. You can close the page, and scheduled syncs keep running as long as that computer is awake.

Your first sync may take a while if you have a lot of saved posts. You can stop a sync at any time without losing anything. Posts already saved stay in your archive, and the next sync picks up where it left off. You can also run individual steps on their own: download media, fill in the rest of saved conversations, or refresh X Articles.

Live sync needs an X account and its session cookies. It can only collect what X makes available to that account, so combining sync with an official archive is useful for older history. The [syncing guide](docs/syncing.md) covers schedules and catching up.

## Browsing

Switch between Bookmarks, Likes, Your tweets, and All. Sort by post date or popularity, browse likes in liked order, or choose random order when you don't have anything particular in mind.

Open a post to read its saved conversation and quotes. On desktop, a split view keeps your feed and the open post side by side. On a phone, it looks and feels like the X app.

You can change the theme, accent color, font, and text size, and choose a grid or carousel for posts with several photos or videos. Each browser keeps its own appearance choices, so your phone and desktop can look different.

Author cards let you look through saved versions of a profile. Posts can also show Community Note changes and availability history as TweetNook observes them. Availability checks help you see whether a saved post is still on X, while keeping the material you've already archived.

See [browsing](docs/web-app.md) for media controls, profiles, Jobs, and the rest of the web app.

## Search

Start with a word you remember, then narrow it down. The same queries work in the browser's search box and the command line:

| Search | Finds |
| --- | --- |
| `"train station"` | That exact phrase. |
| `coffee from:alice` | Posts by Alice containing coffee. |
| `since:2025-01-01 until:2026-01-01` | Posts published in 2025. |
| `has:video -is:reply` | Posts with videos, excluding replies. |
| `has:note` | Posts with current or past Community Notes, including on directly quoted posts. |
| `is:uncaptured` | Entries whose post content has not been saved. |
| `tag:"home cooking"` | Posts with that tag. |
| `description:"red bicycle"` | Those words in your saved descriptions. |
| `url:example.com` | Posts with matching saved links. |

You can combine filters, exclude words with a minus, or use uppercase `OR` for alternatives. Search looks through your saved archive. Entries without captured content are hidden by default; use `is:uncaptured` to find them.

```bash
tweetnook search 'coffee from:alice' --limit 50 --sort newest
```

See [search](docs/search.md) for the full list of filters and more examples.

## Tags

Add tags from a post's menu, and write a description when there's something you'd like to remember or search for later. Settings > Tags lets you browse your tags, merge duplicates, and remove ones you no longer use.

Hiding a post removes it from ordinary feed and search results. Its saved copy remains available in direct links, conversations, quotes, terminal views and JSON exports. Search for `is:hidden` to find it and show it in your feed again.

Optional automated tagging suggests tags and can add descriptions of downloaded images and videos, including text visible in them. That lets you search for something in a picture even if the original post never mentioned it. You can review and edit the results. Manual tags and ordinary browsing, imports, and sync work without it.

See [tags](docs/web-app.md#tags) for organizing posts and [automated tagging](docs/automated-tagging.md) for setup, previews, and usage controls.

## Security

TweetNook runs entirely on your own machine, and your archive is stored there. If you only use TweetNook on the computer it's installed on, set the host to 127.0.0.1 in Settings. If you plan to open it from other devices:

- Use it on a trusted network. Anyone on your network can reach the web app, so set a web password.
- Don't expose it to the internet. The web app serves plain HTTP. To reach it from outside your network, use a VPN such as Tailscale or a reverse proxy with HTTPS.

## Documentation

Start with the [user guides](docs/README.md), or jump to what you need:

- [Import an X archive](docs/importing.md)
- [Keep new posts coming in](docs/syncing.md)
- [Browse and read your archive](docs/web-app.md)
- [Search](docs/search.md) and [organize with tags](docs/web-app.md#tags)
- [Set up automated tagging](docs/automated-tagging.md)
- [Download media and complete saved posts](docs/syncing.md#post-details)
- [Back up, update, or move TweetNook](docs/maintenance.md)
- [Use the command line](docs/cli-reference.md)

For contributions, see the [developer docs](docs/development/README.md). Release history is in [CHANGELOG.md](CHANGELOG.md), and the project uses the [Apache 2.0 license](LICENSE).

## Credits

TweetNook began as a fork of [TweetXVault](https://github.com/lhl/tweetxvault) and is developed independently. Thanks to [rrika](https://github.com/rrika) for sharing [research on archive like order](https://github.com/lhl/tweetxvault/issues/2).

If you would like to migrate from a TweetXVault archive, follow the [migration steps](docs/maintenance.md#migration).
