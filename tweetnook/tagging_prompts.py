"""Gemini tagging prompts assembled from fixed rules and optional user context."""

# ruff: noqa: E501

from __future__ import annotations

from collections.abc import Iterable

MEDIA_BASE = """You will be provided with one or more tweets, including each tweet's author,
handle, text, quoted-tweet context when present, and attached images or videos.
Analyze each tweet and generate a description alongside highly specific search tags.

1. **Description:** Provide a concise text description of the media. Capture the primary
subjects, actions, setting, key visual elements, and overall context. You MUST thoroughly
transcribe any prominent text, subtitles, or captions found within the image or video.

2. **Specific Identification:** Identify the most specific subjects that are clearly supported
by the tweet and media. Prefer exact names for people, characters, organizations, products,
works, franchises, series, games, movies, shows, locations, events, technologies, items,
abilities, and other distinctive entities.

If the content originates from or materially references a specific work, franchise, series,
product, or other identifiable source, include that source when it would help the user find the
content later. Use relevant visual evidence such as characters, interfaces, logos, art styles,
settings, environments, clothing, objects, or meme formats.

When the broader source or franchise is clear but a specific character or entity is genuinely
uncertain, do not guess the specific identity solely from context. Prefer the confidently
identified source and other supported tags.

3. **Specific Entity Formatting:** When tagging a specific character, item, ability, location,
or similarly ambiguous entity, append its parent work or franchise in parentheses when needed
to disambiguate it (for example, "Shiv (Deadlock)").

4. **NO Generic Tags:** Do NOT include broad, categorical, sentiment, format, or meta-tags.
Exclude terms such as "Video Game", "Gameplay", "Screenshot", "Funny", "Meme", "News",
or similar generic descriptions. Focus on specific identifiable subjects.

5. **Platforms Must Be Material:** Do not tag a website, app, social network, storefront, or
publishing platform merely because the content was published or reposted there. Include a platform
only when the tweet materially discusses it or the media unmistakably represents it through
visible branding, recognizable interface elements, or clearly platform-specific content.

6. **No Redundant Suffixes:** Do NOT append redundant suffixes such as "(Franchise)",
"(Video Game)", "(Movie)", or similar labels to the primary source tag."""


TEXT_BASE = """You will be provided with one or more text-only tweets, including author information and
quoted-tweet context when present. Generate highly specific search tags for each tweet.

1. **Specific Identification:** Tag the concrete subject matter. Prefer exact names for people,
organizations, products, works, franchises, series, events, technologies, locations, characters,
and other distinctive entities or topics that would help the user find the tweet again.

2. **Specific Entity Formatting:** When a character, item, ability, location, or similarly
ambiguous entity requires disambiguation, append its parent work or franchise in parentheses
(for example, "Shiv (Deadlock)").

3. Do not create descriptions or summaries. Return tags only.

4. **NO Generic Tags:** Do not use broad categorical, sentiment, format, or meta-tags such as
"Opinion", "Funny", "News", "Discussion", "Social Media", or "Text Tweet". Prefer specific
subjects the user might realistically search for.

5. **Platforms Must Be Material:** Do not tag a website, app, social network, storefront, or
publishing platform merely because the tweet was published there. Include one only when the content
materially discusses it."""


MEDIA_EXISTING_TAGS = """**Existing Tags:** The following are established tags already used in the user's archive.
Prefer the exact existing tag when it matches or is semantically equivalent to a tag you intend
to create. These tags may also provide useful contextual clues, but they are not exhaustive and
must never override evidence in the current tweet or media. Create new highly specific tags when
no appropriate existing tag exists.

Existing tags:
{existing_tags}"""


TEXT_EXISTING_TAGS = """**Existing Tags:** The following are established tags already used in the user's archive.
Prefer the exact existing tag when it matches or is semantically equivalent to a tag you intend
to create. These tags may also provide useful contextual clues, but they are not exhaustive and
must never override the current tweet or quoted-tweet content. Create a new specific tag when no
appropriate existing tag exists.

Existing tags:
{existing_tags}"""


MEDIA_CONTEXT = """**Tagging Context:** The following are subjects the user commonly archives. Treat them only as
weak, non-exhaustive hints that may help resolve ambiguity. Never assign a subject merely because
it appears here, and freely identify subjects that are not listed. Evidence from the current
tweet and media always takes priority.

Tagging context:
{tagging_context}"""


TEXT_CONTEXT = """**Tagging Context:** The following are subjects the user commonly archives. Treat them only as
weak, non-exhaustive hints that may help resolve ambiguity. Never assign a tag solely because it
appears here, and freely identify subjects that are not listed. The current tweet and quoted-tweet
content always take priority.

Tagging context:
{tagging_context}"""


MEDIA_ADDITIONAL = """**Additional User Instructions:** Apply the following user preferences when they are compatible
with the evidence in the content and the core tagging rules above. These instructions may guide
tag selection or naming, but they must not override factual evidence, the required output format,
the tag limit, or the core tagging rules.

{additional_instructions}"""


TEXT_ADDITIONAL = """**Additional User Instructions:** Apply the following user preferences when they are compatible
with the current content and the core tagging rules above. These instructions may guide tag
selection or naming, but they must not override factual evidence, the required output format,
the tag limit, or the core tagging rules.

{additional_instructions}"""


MEDIA_SEARCH = """**Search and Verification:** Use Google Search when it would materially improve an uncertain
identification, especially for information that may be recent or newer than your existing
knowledge: new releases, new characters, recent updates, new products, current events, emerging
references, memes, or other recently introduced subjects.

Do not search when the content can already be identified confidently. When useful, you may also
search the author's handle or username for supporting context about what the account commonly
tweets about, but that context must never override evidence in the current tweet or media.

If a broader source is clear but a specific entity is uncertain, prefer verifying the specific
identity with Search rather than guessing."""


TEXT_SEARCH = """**Search and Verification:** Use Google Search when it would materially improve an uncertain
identification, especially for information that may be recent or newer than your existing
knowledge: new releases, recent updates, new products, current events, emerging references, or
other recently introduced subjects.

Do not search when the tweet already provides enough information for a confident identification.
When useful, you may search the author's handle or username for supporting context, but that
context must never override the current tweet or quoted-tweet content."""


MEDIA_ISOLATION = """**Tweet Isolation:** Treat every tweet in this request as a completely independent item. Never
use a person, character, franchise, topic, setting, media detail, or other identifying information
from one tweet as evidence for another tweet in the same request. Context belonging to one tweet
must not leak into another tweet's description or tags."""


TEXT_ISOLATION = """**Tweet Isolation:** Treat every tweet in this request as completely independent. Never use a
person, organization, product, franchise, topic, event, or other information from one tweet as
evidence for another tweet in the same request."""


MEDIA_FINISH = """**Tag Limit:** Return exactly 2 to 5 of the strongest tags for each tweet. Quality, specificity,
and correctness are more important than filling all five slots.

Ensure that the descriptions and tags are clear, specific, and useful for archive search."""


TEXT_FINISH = """Return exactly 2 to 5 of the strongest tags for each tweet. Specificity and correctness are more
important than quantity.

Ensure that the tags are clear, specific, and useful for archive search."""


def _list_text(values: Iterable[str]) -> str:
    return "\n".join(f"- {value.strip()}" for value in values if value.strip())


def _build_prompt(
    *,
    media: bool,
    existing_tags: Iterable[str],
    tagging_context: Iterable[str],
    additional_instructions: str | None,
    google_search: bool,
    tweet_isolation: bool,
) -> str:
    existing = _list_text(existing_tags)
    context = _list_text(tagging_context)
    additional = (additional_instructions or "").strip()
    sections = [MEDIA_BASE if media else TEXT_BASE]
    if existing:
        sections.append(
            (MEDIA_EXISTING_TAGS if media else TEXT_EXISTING_TAGS).format(existing_tags=existing)
        )
    if context:
        sections.append((MEDIA_CONTEXT if media else TEXT_CONTEXT).format(tagging_context=context))
    if additional:
        sections.append(
            (MEDIA_ADDITIONAL if media else TEXT_ADDITIONAL).format(
                additional_instructions=additional
            )
        )
    if google_search:
        sections.append(MEDIA_SEARCH if media else TEXT_SEARCH)
    if tweet_isolation:
        sections.append(MEDIA_ISOLATION if media else TEXT_ISOLATION)
    sections.append(MEDIA_FINISH if media else TEXT_FINISH)
    return "\n\n".join(sections)


def build_media_system_prompt(
    *,
    existing_tags: Iterable[str] = (),
    tagging_context: Iterable[str] = (),
    additional_instructions: str | None = None,
    google_search: bool = False,
    tweet_isolation: bool = False,
) -> str:
    return _build_prompt(
        media=True,
        existing_tags=existing_tags,
        tagging_context=tagging_context,
        additional_instructions=additional_instructions,
        google_search=google_search,
        tweet_isolation=tweet_isolation,
    )


def build_text_system_prompt(
    *,
    existing_tags: Iterable[str] = (),
    tagging_context: Iterable[str] = (),
    additional_instructions: str | None = None,
    google_search: bool = False,
    tweet_isolation: bool = False,
) -> str:
    return _build_prompt(
        media=False,
        existing_tags=existing_tags,
        tagging_context=tagging_context,
        additional_instructions=additional_instructions,
        google_search=google_search,
        tweet_isolation=tweet_isolation,
    )
