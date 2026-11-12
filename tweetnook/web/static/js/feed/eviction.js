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
// Evicted cards kept detached for reuse. A reused card skips parsing and keeps its
// decoded images; its players' sources are released, so none holds a decoder.
const FEED_CARD_POOL_SIZE = 150;

function feedEvictionMethods() {
    let evictObserver = null;
    let restoreObserver = null;
    // Placeholder -> {height, contentHeight, tracks, videos} captured from the card it replaced.
    const savedCards = new WeakMap();
    const deferredCards = new Set();
    // Tweet id -> {card, tweet, phone}: the post object and layout the card was rendered from.
    const pooledCards = new Map();
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
                    this.restoreFeedCards(entries.filter(entry => entry.isIntersecting).map(entry => entry.target));
                }, { rootMargin: `${FEED_RESTORE_MARGIN_PX}px 0px` });
            }
            if (reset) {
                evictObserver.disconnect();
                restoreObserver.disconnect();
                deferredCards.clear();
                pooledCards.clear();
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
                    this.poolFeedCard(card);
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

        // A card is reused only while it would render the same markup. Rendering it to
        // compare cost most of a fresh render, so instead whatever can change the markup
        // drops pooled cards: a new list (search, highlight, sort) resets the pool, edits
        // to a loaded post go through updateLoadedTweet, and expanding text changes what
        // every copy of that post renders. A reloaded post is a new object.
        poolFeedCard(card) {
            // Released the way distant players are; prefetching the returning card restores
            // each source and position. A decoder kept for audio permission is not released,
            // and a detached card must not hold one, so that card renders afresh instead.
            const videos = [...card.querySelectorAll('video')];
            for (const video of videos) this.releaseVideoResource(video);
            if (videos.some(video => video.getAttribute('src'))) return;
            const tweetObj = this.feedTweetById(card.dataset.tweetId);
            if (!tweetObj) return;
            pooledCards.delete(card.dataset.tweetId);
            pooledCards.set(card.dataset.tweetId, { card, tweet: tweetObj, phone: this.isPhoneLayout() });
            if (pooledCards.size > FEED_CARD_POOL_SIZE) pooledCards.delete(pooledCards.keys().next().value);
        },

        dropPooledFeedCards(tweetId = null) {
            if (tweetId === null) pooledCards.clear();
            else pooledCards.delete(String(tweetId));
        },

        // A fast scroll back reports many placeholders at once. Every card is inserted
        // before any carousel position is set: setting scrollLeft forces layout, so doing
        // it between insertions would force a layout pass per card.
        restoreFeedCards(placeholders) {
            const restored = [];
            const phone = this.isPhoneLayout();
            outsideAlpine(() => {
                for (const placeholder of placeholders) {
                    restoreObserver.unobserve(placeholder);
                    const state = savedCards.get(placeholder);
                    if (!placeholder.isConnected || !state) continue;
                    const tweetId = placeholder.dataset.tweetId;
                    const tweetObj = this.feedTweetById(tweetId);
                    if (!tweetObj) {
                        placeholder.remove();
                        continue;
                    }
                    const pooled = pooledCards.get(tweetId);
                    pooledCards.delete(tweetId);
                    const reused = pooled?.tweet === tweetObj && pooled.phone === phone;
                    let card;
                    if (reused) {
                        card = pooled.card;
                        placeholder.replaceWith(card);
                    } else {
                        placeholder.insertAdjacentHTML('beforebegin', this.renderFeedCard(tweetObj));
                        card = placeholder.previousElementSibling;
                        placeholder.remove();
                    }
                    // A skipped (content-visibility) card is sized by its estimate until it
                    // paints; the size it had keeps cards above the viewport from shifting.
                    card.style.containIntrinsicSize = `auto ${state.contentHeight}px`;
                    if (state.tracks.some(Boolean)) {
                        // A track inside a skipped card has no scroll range to restore into.
                        card.style.contentVisibility = 'visible';
                    }
                    restored.push([card, state, reused]);
                }
                for (const [card, state] of restored) {
                    const tracks = card.querySelectorAll('.media-carousel-track');
                    state.tracks.forEach((left, index) => {
                        if (left && tracks[index]) tracks[index].scrollLeft = left;
                    });
                    const players = [...card.querySelectorAll('video')];
                    for (const { src, time } of state.videos) {
                        const video = time && players.find(player => player.getAttribute('src') === src);
                        // Before metadata loads this sets the start position; it loads nothing.
                        if (video) video.currentTime = time;
                    }
                }
                for (const [card, , reused] of restored) {
                    // A reused card keeps its measured clamps, and its layout hasn't changed.
                    if (!reused) this.measureTextClamps(card);
                    evictObserver.observe(card);
                }
            });
        },
    };
}
