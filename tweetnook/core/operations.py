"""X GraphQL operation names, and the names raw captures record for each request.

Captures store the operation that fetched them, so these values are persisted: storage
queries, replay and history read captures back by them.
"""

from __future__ import annotations

BOOKMARKS = "Bookmarks"
LIKES = "Likes"
USER_TWEETS = "UserTweets"
TWEET_DETAIL = "TweetDetail"

TIMELINE_OPERATIONS = (BOOKMARKS, LIKES, USER_TWEETS)

# A TweetDetail response is captured under the pass that requested it, so thread expansion
# and resurrection can tell their own captures apart.
THREAD_EXPAND_DETAIL = "ThreadExpandDetail"
TWEET_DETAIL_RESURRECTION = "TweetDetailResurrection"
DETAIL_CAPTURE_OPERATIONS = (THREAD_EXPAND_DETAIL, TWEET_DETAIL, TWEET_DETAIL_RESURRECTION)
