"""Where query IDs are discovered, and the operations TweetNook requests."""

from __future__ import annotations

from tweetnook.core.operations import BOOKMARKS, LIKES, TWEET_DETAIL, USER_TWEETS

DISCOVERY_PAGE_URL = "https://x.com/?lang=en"
CLIENT_WEB_BUNDLE_URL = "https://abs.twimg.com/responsive-web/client-web"
BUNDLE_URL_PATTERN = r"https://abs\.twimg\.com/responsive-web/client-web/[A-Za-z0-9_.~-]+\.js"
TARGET_OPERATIONS = (BOOKMARKS, LIKES, USER_TWEETS, TWEET_DETAIL)

# Bookmarks remains a fallback from an authenticated capture on 2026-03-14.
# The other IDs below were reverified against the public Twitter/X web bundle on 2026-03-16.
FALLBACK_QUERY_IDS = {
    BOOKMARKS: "Fy0QMy4q_aZCpkO0PnyLYw",
    LIKES: "a2vYKkx2AtoCmEIRO8Gfbw",
    USER_TWEETS: "Y59DTUMfcKmUAATiT2SlTw",
    TWEET_DETAIL: "9rs110LSoPARDs61WOBZ7A",
}
