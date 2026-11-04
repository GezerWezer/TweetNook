/**
 * Bounding the post list: cards far outside the viewport are swapped for empty
 * placeholders of the same height, which releases their players, observers and
 * layout, and are rendered again from this.tweets as the reader scrolls back.
 * State a card's data doesn't hold (carousel and video positions) is saved with
 * its placeholder and reapplied; expanded text already lives in expandedTweets.
 */

// Cards leave beyond the far distance and return within the near one, so a card
// near the boundary does not churn. Returning cards are still outside the media
// prefetch margin, so their media loads as it did when the list first rendered.
const FEED_EVICT_MARGIN_PX = 6000;
const FEED_RESTORE_MARGIN_PX = 3000;
// Cards kept because the feed was collapsed or busy are checked again this often.
const FEED_EVICT_RETRY_MS = 2000;

function feedEvictionMethods() {
    let evictObserver = null;
    let restoreObserver = null;
    // Placeholder -> {height, contentHeight, tracks, videos} captured from the card it replaced.
    const savedCards = new WeakMap();
    const deferredCards = new Set();
    let retryTimer = null;

    return {
        // Observes freshly rendered cards. A full render replaces every card and
        // placeholder, so it starts the observers over.
        trackFeedCards(cards, { reset = false } = {}) {
            if (typeof globalThis.IntersectionObserver !== 'function') return;
            if (!evictObserver) {
                evictObserver = new IntersectionObserver(entries => {
                    this.evictFeedCards(entries.filter(entry => !entry.isIntersecting).map(entry => entry.target));
                }, { rootMargin: `${FEED_EVICT_MARGIN_PX}px 0px` });
                restoreObserver = new IntersectionObserver(entries => {
                    for (const entry of entries) {
                        if (entry.isIntersecting) this.restoreFeedCard(entry.target);
                    }
                }, { rootMargin: `${FEED_RESTORE_MARGIN_PX}px 0px` });
            }
            if (reset) {
                evictObserver.disconnect();
                restoreObserver.disconnect();
                deferredCards.clear();
            }
            for (const card of cards) evictObserver.observe(card);
        },

        // A collapsed feed (a conversation over it) clips every card, so the observer
        // reports none of them as near. The viewer and an open menu hold their card.
        canEvictFeedCard(card) {
            if (this.pageView !== 'list' || this.lightboxOpen) return false;
            if (this.tweetMenuOpen === `list:${card.dataset.tweetId}`) return false;
            if (card.contains(document.activeElement)) return false;
            return ![...card.querySelectorAll('video')].some(video => video.paused === false);
        },

        // A fast scroll reports many distant cards at once. Every card is measured
        // before any is replaced: a replacement invalidates layout, so measuring
        // after it would force a style and layout pass per card.
        evictFeedCards(cards) {
            const evicted = [];
            for (const card of cards) {
                if (!card.isConnected) {
                    evictObserver.unobserve(card);
                    continue;
                }
                if (!this.canEvictFeedCard(card)) {
                    this.deferFeedEviction(card);
                    continue;
                }
                evictObserver.unobserve(card);
                deferredCards.delete(card);
                const style = getComputedStyle(card);
                const height = card.getBoundingClientRect().height;
                evicted.push([card, {
                    height,
                    // contain-intrinsic-size sizes the content box.
                    contentHeight: height - ['paddingTop', 'paddingBottom', 'borderTopWidth', 'borderBottomWidth']
                        .reduce((total, name) => total + (parseFloat(style[name]) || 0), 0),
                    tracks: [...card.querySelectorAll('.media-carousel-track')].map(track => track.scrollLeft),
                    // Keyed by source: the audible decoder pool can move players between cards.
                    videos: [...card.querySelectorAll('video')].map(video => this.feedVideoPosition(video)),
                }]);
            }
            if (!evicted.length) return;
            outsideAlpine(() => {
                for (const [card, state] of evicted) {
                    card.insertAdjacentHTML('beforebegin', String(html`<div class="feed-card-placeholder" data-tweet-id="${card.dataset.tweetId}" style="height: ${state.height}px"></div>`));
                    const placeholder = card.previousElementSibling;
                    savedCards.set(placeholder, state);
                    card.remove();
                    restoreObserver.observe(placeholder);
                }
            });
        },

        // Observing a target again reports its current intersection, which is the
        // only way to learn about cards that stayed far while eviction was blocked.
        deferFeedEviction(card) {
            deferredCards.add(card);
            if (retryTimer !== null) return;
            retryTimer = setTimeout(() => {
                retryTimer = null;
                const cards = [...deferredCards];
                deferredCards.clear();
                for (const deferred of cards) {
                    evictObserver.unobserve(deferred);
                    if (deferred.isConnected) evictObserver.observe(deferred);
                }
            }, FEED_EVICT_RETRY_MS);
        },

        restoreFeedCard(placeholder) {
            restoreObserver.unobserve(placeholder);
            const state = savedCards.get(placeholder);
            if (!placeholder.isConnected || !state) return;
            const tweetId = placeholder.dataset.tweetId;
            const tweetObj = this.tweets.find(tweet => String(tweet.tweet_id) === tweetId);
            if (!tweetObj) {
                outsideAlpine(() => placeholder.remove());
                return;
            }
            outsideAlpine(() => {
                placeholder.insertAdjacentHTML('beforebegin', this.renderFeedCard(tweetObj));
                const card = placeholder.previousElementSibling;
                placeholder.remove();
                // A skipped (content-visibility) card is sized by its estimate until it
                // paints; the size it had keeps cards above the viewport from shifting.
                card.style.containIntrinsicSize = `auto ${state.contentHeight}px`;
                const tracks = card.querySelectorAll('.media-carousel-track');
                state.tracks.forEach((left, index) => {
                    if (!left || !tracks[index]) return;
                    // A track inside a skipped card has no scroll range to restore into.
                    card.style.contentVisibility = 'visible';
                    tracks[index].scrollLeft = left;
                });
                const players = [...card.querySelectorAll('video')];
                for (const { src, time } of state.videos) {
                    const video = time && players.find(player => player.getAttribute('src') === src);
                    // Before metadata loads this sets the start position; it loads nothing.
                    if (video) video.currentTime = time;
                }
                this.measureTextClamps(card);
                evictObserver.observe(card);
            });
        },
    };
}
