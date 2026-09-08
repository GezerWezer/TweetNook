# Search

[User guide](README.md) · [Web app](web-app.md) · [Tags](tags.md)

Search for words you remember, then add filters to narrow the results. The web
app and terminal use the same search syntax. Searches look through your saved
archive, including tweets that may no longer be available on Twitter/X.

## The basics

| Find… | Search |
|---|---|
| Tweets containing a word | `word` |
| An exact phrase | `"exact phrase"` |
| Photos by an author | `from:handle has:image` |
| Tweets from January 2026 | `since:2026-01-01 until:2026-02-01` |
| A tag containing spaces | `tag:"street photography"` |
| Links, excluding reposts | `has:links -is:retweet` |
| Either of two words | `foo OR bar` |

To search in the terminal, wrap the whole query in single quotes:

```bash
tweetnook search 'from:alice has:image "night sky"'
```

Search matches words and phrases, not concepts with unrelated wording. 
## Combine words and filters

Words next to each other must all match. Use `OR` for alternatives and `-` to
exclude a word or filter.

| Syntax | Example | Meaning |
|---|---|---|
| Space or `AND` | `sqlite archive` | Both words |
| `OR` | `photo OR video` | Either word |
| `NOT` or `-` | `cats -dogs` | Cats, excluding dogs |
| Double quotes | `"local archive"` | An exact phrase |
| Trailing `*` | `archiv*` | Words starting with `archiv` |
| Hashtag | `#sqlite` | The hashtag as a search term |

Write `AND`, `OR`, and `NOT` in uppercase. Put multiword filter values in double
quotes, such as `tag:"machine learning"`. Parentheses are not supported for
controlling groups.

## People

| Filter | Finds |
|---|---|
| `from:alice` | Tweets by Alice |
| `to:alice` | Replies to Alice |
| `mentions:alice` | Tweets mentioning Alice |

Usernames work with or without `@`. The web app suggests authors already in
your archive as you type.

## Dates

| Filter | Finds |
|---|---|
| `since:2026-01-01` | Tweets on or after January 1 |
| `until:2026-02-01` | Tweets before February 1 |

Dates use UTC. `since` includes the day you enter; `until` excludes it. To
search one day, use that date and the next day's date:

```text
since:2026-08-19 until:2026-08-20
```

## Media and tweet types

| Filter | Finds |
|---|---|
| `has:media` | Tweets with media |
| `has:image` | Tweets with images |
| `has:video` | Tweets with video |
| `has:links` | Tweets with links |
| `has:article` | Tweets with a Twitter/X article |
| `is:reply` | Replies |
| `is:quote` | Quote tweets |
| `is:retweet` | Reposts |
| `is:thread` | Tweets matching the archive's thread information |
| `is:verified` | Tweets whose captured author data indicates verification |
| `is:resurrected` | Tweets recovered after previously being unavailable |

To exclude a type, put `-` before the filter: `has:media -is:retweet`.

## Tags and links

| Filter | Finds |
|---|---|
| `tag:recipe` | Tweets with the local tag `recipe` |
| `tag:"street photography"` | Tweets with a multiword local tag |
| `url:example.com` | Matching text in saved expanded or display URLs |

Tag searches also include tags on a tweet's directly quoted original.
[Tags](tags.md) explains how to add and edit them.

## Choose a collection and order

In the web app, choose **Bookmarks**, **Likes**, **Your Tweets**, or **All Tweets**.
In the terminal:

```bash
tweetnook search 'recipe' --collection bookmark
tweetnook search 'recipe' --collection bookmark,like --sort newest --limit 50
```

Collection values are `bookmark`, `like`, and `tweet`. A tweet saved in more than
one collection appears once in search results.

Text searches normally put the most relevant matches first. Searches containing
only filters put the newest tweets first. The terminal also accepts `--sort newest`
and `--sort oldest`; the web app offers those orders plus **Random**.

## Search article content

To search stored article text from the terminal:

```bash
tweetnook search 'local databases' --type article
```

Article searches do not support the tweet filters listed above. Use a normal
tweet search with `has:article` when you need author, date, or other tweet filters:

```bash
tweetnook search 'from:alice has:article'
```

## Large searches

Broad searches cover the complete archive and can be browsed past 1,000 matches.
TweetNook applies text, collection, and structured filters before choosing and
hydrating each page. The selected relevance, date, like, or random order is also
applied before pagination, and a tweet saved in several collections still
occupies one result slot.

The web app does not calculate an exact total for text searches because counting
every match would delay the first page. Its header therefore says **Results**
without a number. Filter-only and ordinary collection feeds continue to show an
exact total. Ordinary newest/oldest feeds and Likes use cursor scrolling to avoid
skipping all earlier rows. Complex searches and explicit numbered pages can still
take longer at large depths. If loading fails, use **Retry loading more**; the
failed page is retried without advancing past it.

## Advanced filters

| Filter | Meaning |
|---|---|
| `since_time:1704067200` | Created at or after a Unix timestamp in seconds |
| `until_time:1704153600` | Created before a Unix timestamp in seconds |
| `since_id:123` | Tweet ID greater than the given ID |
| `max_id:456` | Tweet ID less than or equal to the given ID |
| `min_retweets:10` | At least 10 captured reposts |
| `min_faves:100` | At least 100 captured likes |
| `min_replies:5` | At least 5 captured replies |
| `conversation_id:123` | A specific conversation |
| `quoted_tweet_id:123` | Tweets quoting a specific tweet |
| `source:"Twitter for iPhone"` | A captured posting-client label |
| `card_name:summary` | A captured link-card type |

Engagement counts are those saved in the archive, not live counts from Twitter/X.

See the [CLI reference](cli-reference.md#search) for command options or
[Troubleshooting](troubleshooting.md#search-results-are-missing-or-a-query-is-rejected)
if a query does not behave as expected.
