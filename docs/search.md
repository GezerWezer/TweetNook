# Search

Start with a word you remember. Add an author, date, or type of media when the results need narrowing down. The same search language works in the browser and from `tweetnook search`.

```text
coffee from:alice
"train station" has:image
tag:recipes since:2025-01-01
```

## Search basics

Separate words mean that each word must match. Put a phrase in double quotes to keep its words together. A trailing `*` matches the beginning of a word, as in `garden*`. Use a leading minus or `NOT` to exclude a clause.

```text
"home office" -has:video
cats OR dogs from:alice
coffee NOT decaf
```

`OR` joins the two clauses beside it. In `cats OR dogs from:alice`, the result needs either cats or dogs and must be from Alice. `AND` is optional between the other clauses. Use uppercase for `OR`, `AND`, and `NOT`; parentheses aren't a grouping feature.

## People, dates, and links

| Search | Finds |
| --- | --- |
| `from:alice` | Posts by that account; an `@` before the name is optional. |
| `to:alice` | Replies addressed to that account. |
| `mentions:alice` | Posts mentioning the account. |
| `since:2025-01-01 until:2026-01-01` | Posts in 2025. The start is included; the end is excluded. Dates use UTC. |
| `#gardening` or `hashtag:gardening` | That hashtag. |
| `url:example.com` | Saved links matching that text. |
| `conversation_id:123456789` | A particular conversation. |
| `quoted_tweet_id:123456789` | Posts quoting a particular post. |

Dates refer to when the post was published, not when you saved it.

## Post types

| Search | Finds |
| --- | --- |
| `has:image` | Posts with photos. |
| `has:video` | Posts with videos or animated GIFs. |
| `has:media` | Posts with attached photos, videos, or GIFs. |
| `has:links` | Posts with saved links. |
| `has:article` | Posts with an Article. |
| `has:note` | Current or past Community Notes on the post or a directly quoted post. |
| `is:reply`, `is:quote`, `is:retweet` | Replies, quote posts, or reposts. |
| `is:thread` | Replies to the same author, such as continuations of their thread. |
| `is:verified` | Posts with saved verified-account information. |
| `is:hidden` | Posts you've hidden from normal browsing. |
| `is:uncaptured` | Entries whose post content has not been saved. |
| `is:missing` | Posts recorded as unavailable on X, but preserved by your archive. |
| `is:resurrected` | Posts that became available again after being unavailable. |


Entries without captured content are hidden from feeds and searches by default. Use `is:uncaptured` to find them, or `recipe OR is:uncaptured` to include them alongside another search. `-is:uncaptured` excludes them explicitly.

`has:note` includes saved note history, even removed notes, and notes on directly quoted posts outside your collections. It does not follow further levels of quotes. Use `-has:note` to exclude the same posts.

## Tags and descriptions

```text
tag:travel
tag:"home cooking"
description:"red bicycle"
```

`tag:` matches an assigned tag, including tags on a quoted post. `description:` searches your saved local descriptions, whether written manually or generated; all its words must match, and each word can end in `*`. Descriptions and tags have their own filters, so use those when the words don't appear in the original post.

## Advanced filters

`min_faves:100`, `min_retweets:20`, and `min_replies:5` use the engagement counts saved with the post. `since_time:` and `until_time:` accept Unix timestamps. `since_id:` and `max_id:` filter post IDs. `source:` searches saved posting-client information, and `card_name:` matches a saved card type.

The older `filter:` forms also work: `articles`, `images`, `links`, `media`, `native_video`, `nativeretweets`, `quote`, `replies`, `self_threads`, `threads`, `verified`, and `videos`.

## Command-line search

```bash
tweetnook search 'coffee from:alice' --limit 50 --sort newest
tweetnook search 'tag:travel' --collection bookmark,like
tweetnook search 'gardening' --type post,article
```

Terminal search defaults to posts, twenty results, and relevance order. Choose `--type article` to search saved Article text, or `--type post,article` for both. Article searches accept text rather than post filters; use a normal search with `has:article` for author, date, and other post filters. Available terminal sorts are `relevance`, `newest`, and `oldest`.

If you don't find a post, try All collections, remove a filter, or look for `is:hidden`. A post that hasn't been imported or synced yet won't appear. See [syncing](syncing.md) if you're still bringing in older history.
