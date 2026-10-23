# Web app

[User guide](README.md) · [Connect Twitter/X](getting-started.md) · [Search](search.md)

Use the web app to browse saved tweets, read archived threads, edit tags, and
keep your archive up to date.

## Start and stop the app

Run `tweetnook serve` in the foreground, or explicitly install the
[Linux systemd service](cli-reference.md#service) for continuous operation and boot startup.
The server starts without a database and does not create one just by starting.
Open `http://<server-address>:8000` from your LAN, or
[http://127.0.0.1:8000](http://127.0.0.1:8000) on the server itself.

First visit opens Setup in Settings: import an official Twitter/X archive (recommended),
connect Twitter/X or explicitly skip it, then create a Web password. There is no default
password. Any username works when the browser asks for HTTP Basic authentication.
An existing SQLite archive is recognized on upgrade without forcing first-run Setup.

The optional manual background commands remain available:

| Command | Use it to… |
|---|---|
| `tweetnook web start` | Start the background server |
| `tweetnook web status` | Check whether the background server is running |
| `tweetnook web stop` | Stop the background server and its managed job |
| `tweetnook web restart` | Restart after a general configuration change |
| `tweetnook web set-password` | Advanced password recovery; restart afterward |

Stopping TweetNook asks active Web/scheduled work to stop gracefully so already
committed work is preserved. Use `tweetnook service stop` for a systemd installation.

## Browse saved tweets

Choose **All Tweets**, **Likes**, **Bookmarks**, or **Your Tweets**. Scroll to
load more tweets, or choose **Latest**, **Oldest**, or **Random** to change the
order. Random order stays stable while you scroll and is reshuffled when you
start a new search. With a text search, **Relevance** becomes available.

With **Likes** selected, **Recently liked** and **Earliest liked** follow observed
like order instead of tweet publication date. These orders also apply to searches
and filters. Saved GraphQL Likes pages take priority; archive-only gaps use an
approximate reconstruction of 25-item blocks and shared-tweet anchors. Order
within those blocks and between disconnected observations remains uncertain.
Entries without ordering evidence stay last in either direction. Existing raw
captures are used automatically, without repeating an import or making Twitter/X requests.

Tweet cards show the content, media, and counts captured in your archive. Like
and bookmark icons show where you saved a tweet; they do not change anything on
Twitter/X. Counts are saved values and may differ from Twitter/X today.

Select an author's name, avatar, or an @mention to open their profile card. Each
saved tweet carries the profile as it was when TweetNook fetched that tweet, so the
card shows the most recently fetched profile among all of the author's saved tweets,
which is not necessarily the newest tweet's. When the bio, name, or counts changed
between fetches, use the arrows to step through older captures; the card shows when
each one was fetched. An @mention shows a profile only if you saved a tweet by that
account.

Choose **Show more** to expand long text. Open a photo or video to view downloaded
media at a larger size. In the fullscreen viewer, swipe left or right to move through
a tweet's media, or swipe down to close it. **Media not downloaded** means the tweet
was saved but the local media file is missing; see
[Download media](enrichment.md#download-media).

Tall quoted photos use cropped previews; open the photo to see the full image.
Videos in the default grid preserve the complete picture, with bars when their proportions
differ from the tile. On narrow screens, direct portrait photos and videos fill
the available width.

In **Settings → Appearance → Media layout**, choose how tweets with multiple
photos, videos, or GIFs appear:

- **Grid** (the default) shows all attachments together.
- **Carousel** uses X's horizontally scrolling tiles for every multi-media tweet.
- **By post date** keeps the grid for tweets before July 10, 2026 (UTC), and uses
  the carousel from that date onward. Quotes and reposts use the original tweet's
  posting date, independently of the parent tweet. Unknown dates keep the grid;
  liking or archiving an old tweet recently does not change its layout.

The cutoff follows the first public iOS carousel launch announcement, documented
in [contemporary reporting](https://news.denfaminicogamer.jp/news/260711d).
X rolled the change out gradually across clients, so this is a historical
approximation rather than an exact record of every author's experience.

The carousel shows separate rounded tiles: scroll horizontally or swipe through
them, or use the arrows on desktop. Focus the strip to use Left/Right and
Home/End. Select a photo to open the existing fullscreen viewer. Wide pictures
and videos may be cropped in carousel tiles. Quoted galleries use the same
choice. The choices include small layout previews and support arrow-key selection.
This preference is saved in the current browser; single attachments
keep their existing layout.

Volume and mute changes apply to the other videos on the page, including the
split panel, fullscreen viewer, and videos loaded as you scroll. This shared
setting lasts until the page reloads. GIFs remain silent.

## Read X Articles

X Articles are native long-form publications attached to posts. Feed cards show
an article's cover, title, and excerpt. Select the preview to read the saved body
in the conversation view or desktop split panel. **Focus mode** opens a centered
reader; select the close button or press Escape to return to the same place.

The reader preserves captured headings, emphasis, links, lists, quotations, and
inline images. Older captures with only plain text retain their paragraph breaks.
Quoted articles keep their full preview, including when the quoting post has its
own media. Nested quotes, replies, and reposts use the same saved article content.

Only captured content is shown. A preview-only article says that its body has not
been archived; missing image files show a download placeholder. Embedded content
without a saved representation links to X when a reference is available.
Use `has:article` to find article-bearing posts, and
[refresh articles](enrichment.md#refresh-articles) to fill in saved previews.

## Read threads and quotes

Select a tweet to open its archived conversation. Parent tweets appear in order,
followed by its saved reply tree regardless of author. Deeper replies are indented
under each direct reply. To keep unusually large conversations responsive, one view
shows up to 250 descendant replies and displays a notice when more are saved; select
a later reply to continue from that point. Long reply trees appear about 30 replies at
a time; more load as you reach the end, or select **Show more replies**. Direct
replies that only @mention someone other than the original poster, with nothing else
and fewer than 5 likes, wait behind **Show other replies** at the end of the list,
like X's **Show probable spam**. Selecting it adds them under an **Other replies**
heading. Replies you saved stay in the main list. A conversation
opens at once on the tweet you selected and fills in its replies as they load. You can also browse saved tweets that quote
the selected tweet, including quotes you have not liked or bookmarked.
Tweet and quote views have copyable URLs (`/post/<tweet-id>` and
`/post/<tweet-id>/quotes`), so they can be bookmarked, refreshed, or shared with
someone who can access the same TweetNook server and archive.

The conversation contains what tweetnook has captured. Gaps can mean a tweet
has not been downloaded yet or Twitter/X no longer makes it available. A placeholder
explains the reason when known. Quote counts refer to your archive's saved
quotes, not the total on Twitter/X.

A wide window can show the feed and conversation side by side. Smaller screens
use a single column.

## Search and organize

The search box accepts words and filters together:

```text
"night sky" has:image
tag:recipe -is:retweet
from:alice since:2026-01-01
```

Author and tag suggestions help you find values already in your archive.
[Search](search.md) lists all supported filters.

Open a tweet's menu to edit its tags and description. Use **Settings → Tags** to
merge or delete a tag across the archive. See [Tags](tags.md) for examples.

Choose **Hide** in a tweet's menu to keep it out of your lists and search
results. It stays in your archive: search `is:hidden` to see hidden tweets, and
choose **Unhide** in the menu to show one again.

## Run a sync and view progress

Use the floating activity drawer to start a sync of bookmarks and likes. It
shows the current step, progress, elapsed time, and any problems that need
attention. Use **Stop Task** to request cancellation.

Saved tweets remain available even if a later download or lookup fails. For older
runs, open **Settings → Logs** and select the relevant entry. By default, history
keeps up to 100 completed runs for up to 90 days.

Run one archive job at a time. Wait for jobs to finish before making bulk tag or
configuration changes.

## Scheduling

1. Open **Settings → Schedule** and enable scheduled syncing.
2. Choose an interval, or a daily, weekly, or monthly time.
3. Check the time zone.
4. Choose whether to add a random delay, then **Save**.
5. Check the next run shown in the activity drawer.

For daily, weekly, and monthly schedules, the default random delay is between
zero and two hours after your chosen time. Turn it off for a fixed time. Hourly
schedules ignore this setting. A monthly date past the end of a shorter month
uses that month's last day.

The TweetNook server/service must remain running; the browser can be closed.
If another job is active when a sync is due,
that occurrence is skipped. If the app restarts with a saved overdue run, it
tries once and then resumes the schedule; it does not replay every missed run.

Scheduled sync saves **bookmarks and likes**. Save your own tweets separately with
`tweetnook sync tweets`.

## Configure availability checks

In **Settings → Config → Check**, enable **Check automatically** and select Daily,
Weekly (default), or Monthly. Set its time and the weekday or monthly date.
**Check context tweets** is a separate basic toggle, off by default. Turn it on to
show **Context cycle**, which defaults to 60 days. The rolling batch size follows
the check frequency; saved tweets and quotes returned with them are checked every
run. Checks require FxTwitter and the running Web service. Saved changes take
effect immediately; the login lookup budget remains under Advanced.

## Update your Twitter/X connection or import an archive

**Settings → Setup** lets you replace your Twitter/X session values. New
values are checked before they are saved.

You can also upload an official Twitter/X archive ZIP and start an import. **Import** adds it to the
archive. Clearing the staged ZIP removes that uploaded copy only, not imported
tweets or media. Finish active jobs before uploading or importing.

**Run setup again** reopens the same flow without resetting data or settings.
Setup imports always run offline first. You can continue to test/save Twitter/X cookies
while import runs, finish Setup, and close the page. Once both the local import
and successful Twitter/X test finish, enrichment starts automatically in the background.
Skipping Twitter/X keeps local browsing available with enrichment pending. Initialization
can also migrate a detected upstream archive or explicitly create an empty one.
Migration does not trigger automatic enrichment.

Settings → Notifications retains failures and issues needing attention, with
links to Setup, Schedule, or Activity and a dismiss control. Unread notifications
also appear as a badge on the Settings button. Imports and enrichment use the
same Activity progress as other jobs. If enrichment remains incomplete, use
**Continue enrichment** in the Web app; it does not retry forever automatically.
See [Importing](importing.md) for supported data and import options.

## Settings and appearance

| Panel | What you can change |
|---|---|
| Appearance | Theme, accent, font, text size, and panel layout |
| Configuration | Avatar fetching, server address and port; additional settings in Advanced mode |
| Schedule | When bookmark-and-like sync runs |
| Automated Tagging | Gemini setup, tag generation, and usage history; requires the optional extra |

Supporting mobile browsers also receive the selected theme's background color
and light/dark scheme for their surrounding bars and native controls. The exact
bar appearance depends on the browser and operating system.

Appearance choices are saved in the browser, so another browser can have a
different layout. Restart the server after changing general configuration. The
advanced `web set-password` command also requires a restart. Schedule changes
take effect when saved.

**Restore Defaults** preserves Twitter/X credentials, the Gemini key, and your web
password. Use a desktop-sized window for administration; Settings and Stats
controls are currently hidden on narrow screens.

## Check archive statistics

Open **Stats** for collection counts, recent sync information, missing details,
media, tags, and storage use. Choose **Refresh** after a job or tag edit to get
updated figures. Automatic snapshots are rebuilt once a day.

Some storage categories are estimates; the total file sizes are measured. For
an immediate terminal report, run `tweetnook stats`.

## Network and security boundary

The default listener is `0.0.0.0`, supporting browsers on your homelab/LAN.
Set `web.host` to `127.0.0.1` for access only from the server itself. Fresh Setup
creates the password as its final action. The app has no built-in HTTPS or
separate user accounts. HTTP Basic credentials are not protected by transport
encryption without HTTPS, so use the built-in HTTP server only on a network you
trust or put TweetNook behind appropriate HTTPS/network controls.

The page ships its scripts and styles and uses local fonts. It works without
internet access while the archive server remains reachable. Downloaded media and
video posters stay local; link-card images without a matching archived copy are
omitted. The Web server limits cached avatars to 512 MiB by default using a separate
weekly least-recently-used cleanup job; both the limit and cleanup can be changed in
Config. Disable avatar fetching and scheduled syncs to avoid their network attempts.
Sync, enrichment, authentication checks, Gemini actions, and external links still
need internet. This is not a standalone HTML export or a replicated browser database.

In **Appearance**, use **Load local fonts** to request the browser's permission
to list fonts on the device displaying the page (not the server). This requires a
supporting browser and HTTPS or localhost. If unavailable or denied, use the fallback
choices. The custom dropdown previews each font in its own typeface.
