# Tags

[User guide](README.md) · [Search](search.md) · [Automated tagging](automated-tagging.md)

Tags help you organize saved tweets using your own labels, such as `recipes`,
`read later`, or `street photography`.

## Add or edit a tag

1. Open a tweet's `…` menu in the web app, or long-press `…`.
2. Open the tag editor.
3. Add tags, choose an existing suggestion, or remove tags you no longer want.
4. Optionally add a description, then save your changes.

Both text-only tweets and tweets with media can have tags. Empty entries are
ignored, and tags that differ only in capitalization are treated as duplicates.
The editor also lets you clear all tags and the description for a tweet.

Manual tag editing is only available in the web app.
## Find tagged tweets

Click a tag in the web app or search for it:

```text
tag:recipe
tag:"street photography"
```

Combine tags with other filters:

```text
tag:recipe has:image
tag:"read later" since:2026-01-01
```

Results include tweets with that tag and tweets whose directly quoted original
has it. See [Search](search.md) for more filters.

## Merge or delete tags across the archive

Open **Settings → Tags** to manage tags in one place.

- **Merge** combines source tags into one primary tag. For example, merge
  `cooking` into `recipes` to use one name throughout your archive.
This can be useful if automated tagging makes up a new tag instead of using an existing one
- **Delete** removes a tag from every tweet that uses it.

> [!WARNING]
> Global merges and deletions have no undo. 

If the totals in Stats still show old values after an edit, choose **Refresh**.
Automatic statistics snapshots can be cached for up to 12 hours.

## Generate tags with Gemini

Optional [Automated tagging](automated-tagging.md) can suggest tags for text and
media, and add descriptions of media. It requires separate setup and sends
selected content to Google.

You can edit generated tags in the same editor. Manual and generated values
share the same saved record. Normal automated runs skip tweets with completed,
nonempty tags; explicitly targeting a tweet can replace its existing values.
