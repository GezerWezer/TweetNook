# Browsing

## Collections and sorting

Use the search box for words or an author, or open the filters to narrow things down. You can sort by post date, popularity, or random order. The Likes view also has newest-liked and oldest-liked order; those follow your likes rather than the date the author posted.

The [search guide](search.md) has examples you can paste into the search box.

## Reading posts

Open a card to read the post with its saved conversation and quotes. On a desktop you can keep it beside the feed in a split panel or read it in the main column. On a phone, use the back button or the swipe-back gesture to return to where you were browsing.

Photos, videos, GIFs, link cards, quoted posts, and Articles appear with the saved post. Open media to see it at a larger size. Articles can be read in TweetNook when their full text has been saved; otherwise you'll see the information currently available.

Saved content stays readable with a warning if the original post or quote is deleted or becomes unavailable on X. If nothing was saved, an unavailable placeholder explains the gap. Text and media can be saved separately: saved media can appear without post text, and saved text can appear beside a **Media not downloaded** frame. That frame appears only when TweetNook has captured the media details.

Posts can include saved translation and Community Note information. Availability and note history help you see what changed after a post was collected. These are records of what TweetNook has observed, rather than a complete history of every change on X.

## Tags

### Editing tags

Open the post's `…` menu and choose the tag editor, enter a name, and save it. Long-pressing the menu button also opens the editor. Existing tags are suggested as you type. Use a tag such as `weekend projects` if that's how you'll remember the post later; tags can contain spaces.

You can also write a local description, edit an existing one, or clear all tags and the description. Both text-only and media posts support manual tags. Empty tags are ignored, and names that differ only in capitalization count as duplicates.

Remove a tag from the same dialog when it no longer fits. Removing a tag changes the organization of that post and leaves the post itself in your archive.

### Searching tags

Select a tag to see its posts, or search explicitly:

```text
tag:gardening
tag:"weekend projects" has:video
```

Tag searches also find saved posts that quote a post with that tag. A search for ordinary words looks in the post text; use `tag:` when you're searching your labels.

### Managing tags

To change the wording of a tag on one post, replace it in that post's tag editor. Settings > Tags lists the tags in your archive. You can sort by name or by how often a tag is used and open the posts carrying it.

- Merge tags when several names mean the same thing. Choose the name to keep; the others are replaced on every affected post.
- Delete a tag to remove it everywhere. To remove it from just one post, use that post's tag dialog.

If a merge combines the same tag on a post, it stays a single tag. Review the selected names before confirming a merge or global deletion.

Global merges and deletions have no undo. Make a backup if you may need the old organization later.

### Suggested tags

[Automated tagging](automated-tagging.md) can add AI-generated tags and descriptions. It's optional, and manual tags work without it. You can review and edit suggested tags in the same places as your own.

Manual and generated values share the same editor; normal automated runs skip completed, nonempty tags, while explicitly tagging a post can replace its values.

### Hidden posts

Use a post's menu to open it on X or hide it. Hiding removes the post from ordinary feed and search results. It remains accessible through direct links, conversations, quotes, terminal views and JSON exports; hiding does not restrict access to its contents. Search for `is:hidden` to find hidden posts and show them again.

## Appearance

Settings > Appearance has themes, accent colors, text sizes, and fonts. Desktop layout controls the single-column and split-panel views. Media layout can use a grid, a carousel, or choose by post date: a grid before July 10, 2026, and a carousel from that date onward.

You can turn muted video autoplay on or off. Appearance choices are kept by the browser, so your phone can have different preferences from your desktop.

## Jobs and logs

Open Jobs to start a sync, check availability, or run an individual task such as downloading media or refreshing Articles. Each job shows the available run options, and scheduled jobs show their next run. You can queue supported jobs while another job is running and remove a queued job before it starts.

Queued manual jobs are held in memory. After restarting the server, queue them again if they are still needed. Setup keeps its first-sync intent separately.

Completed and unsuccessful jobs stay in Jobs. Select a job to review its saved steps and errors; use its options button to change run settings.

On a phone, Jobs opens as a bottom sheet. Scroll inside it to browse jobs. To close it, pull down when its content is at the top, swipe down on the handle, or tap outside.

Use the stop action if you need to interrupt active work. Give it time to finish stopping before starting another task. Work already saved remains available.

Settings > Activity logs keeps past runs and their logs, which you can download. Settings > Notifications collects events that need attention and links them to the relevant run. Dismissing a notification or clearing dismissed history doesn't delete archived posts.

Analytics shows collection totals, storage usage, and the state of outstanding work. Some figures refresh periodically; use the refresh action when you need a new snapshot.

## Network access

Open the server's network address from another device, and use your web password if one is set. TweetNook continues doing scheduled work when every browser tab is closed, as long as its server stays running.

[Maintenance](maintenance.md) covers settings, server access, updates, and backups.

## Profiles and counts

Select an author's name or avatar to open their saved profile. An @mention can open a profile when your archive has a saved post by that account. The card starts with the most recently fetched profile, and its arrows let you step through older saved versions of the name, bio, or counts. The fetch date tells you when TweetNook observed each version.

Post counts and profile figures reflect saved observations. The like and bookmark icons show where you saved a post; selecting them isn't a way to like or bookmark it on X. Quote counts refer to quotes in your own archive.

Random order stays steady as you scroll and reshuffles for a new search. In liked-order views, posts without enough ordering evidence stay at the end in either direction.

## Conversations and Articles

Conversation views show saved parents and replies. Large reply trees load in sections; use Show more replies to continue. Show other replies reveals replies kept in a separate group. A view can show up to 250 descendant replies; if a notice says more are saved, open a later reply to continue there. Saved posts stay in the main reply list.

Post and quote views have copyable URLs, `/post/ID` and `/post/ID/quotes`. You can bookmark those addresses or share them with someone who has access to the same TweetNook server.

Open an Article preview to read its saved body, then use Focus mode for a centered reader. Close it or press Escape to return. Saved headings, links, lists, quotations and inline images remain part of the Article. A preview-only Article or undownloaded image tells you what's still missing; use Refresh articles or Download media to complete it.

## Media controls

In the fullscreen viewer, swipe horizontally to move through a post's media and swipe down to close. On a focused carousel strip, Left/Right and Home/End move through the tiles. Wide images can be cropped in tiles; open them to see the complete picture.

By post date uses each original post's publication date, including quoted and reposted content, with a UTC boundary on July 10, 2026. Unknown dates use a grid. Saving an old post today doesn't give it a newer layout, and single attachments keep their existing layout.

Changing volume or mute affects other videos on the same page, including fullscreen and split-panel players, until the page reloads. GIFs stay silent.

Load local fonts asks the browser for permission to list fonts on the device you're using. It needs a supporting browser and HTTPS or localhost. If permission or support isn't available, choose a fallback font.

## Passwords

There is no default web password. When the browser asks for a username and password, any username works; enter the password you chose in Setup. A saved browser session can remember it. Use `tweetnook web set-password` and restart the appropriate server if you need to replace it from the terminal.
