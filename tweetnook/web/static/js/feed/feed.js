/**
 * The post list: the collection and sort menu, search, loading pages (the first page
 * cached briefly by URL and prefetched while idle), rendering cards into the list and
 * handling clicks on them, and updating or removing a card in place.
 */

// First feed pages stay reusable this long; finished jobs clear them sooner.
const FEED_CACHE_MS = 5 * 60 * 1000;
const FEED_CACHE_LIMIT = 12;
// Load the next page while the end of the list is still this far below the viewport.
const FEED_PREFETCH_MARGIN_PX = 1200;
const DEFAULT_FEEDS = [['likes', 'liked_latest'], ['all', 'newest'], ['bookmarks', 'newest'], ['tweets', 'newest']];

function feedMethods() {
    // First pages by URL ({at, speculative, result, promise}) and the URL on screen.
    const feedPages = new Map();
    let feedListUrl = null;
    let feedPrefetchScheduled = false;
    // The list element whose delegated listeners are bound.
    let boundFeedList = null;
    // Roots waiting for the next frame's clamp measurement (measureTextClamps).
    const clampRoots = new Set();

    return {
        tweets: [],
        loading: true,
        loadingMore: false,
        page: 1,
        totalPages: 1,
        total: 0,
        hasMore: false,
        nextCursor: null,
        feedRequestId: 0,
        feedGeneration: 0,
        countController: null,
        randomSeed: null,
        collectionFilter: 'likes',
        sortOrder: 'liked_latest',
        feedMenu: null,
        feedMenuTrigger: null,

        feedOptions(kind) {
            if (kind === 'collection') return [
                { value: 'all', label: 'All tweets' },
                { value: 'likes', label: 'Likes' },
                { value: 'bookmarks', label: 'Bookmarks' },
                { value: 'tweets', label: 'Your tweets' },
            ];
            return [
                ...(this.searchQuery.trim() ? [{ value: 'default', label: 'Relevance' }] : []),
                ...(this.collectionFilter === 'likes' ? [
                    { value: 'liked_latest', label: 'Recently liked' },
                    { value: 'liked_earliest', label: 'Earliest liked' },
                ] : []),
                { value: 'newest', label: 'Latest' },
                { value: 'oldest', label: 'Oldest' },
                { value: 'random', label: 'Random' },
                { value: 'most_likes', label: 'Most likes' },
            ];
        },
        feedValue(kind) {
            return kind === 'collection' ? this.collectionFilter : this.sortOrder;
        },
        feedLabel(kind) {
            return this.feedOptions(kind).find(option => option.value === this.feedValue(kind))?.label || 'Relevance';
        },
        resultCountLabel() {
            if (this.searchQuery.trim() && this.total === null) {
                return this.countError ? 'Count unavailable · Results' : 'Counting… Results';
            }
            const count = Number(this.total) || 0;
            const noun = this.searchQuery.trim()
                ? 'Results'
                : (this.collectionFilter === 'all'
                    ? 'Tweets'
                    : this.collectionFilter.charAt(0).toUpperCase() + this.collectionFilter.slice(1));
            return `${count.toLocaleString()} ${noun}`;
        },
        openFeedMenu(kind, trigger) {
            this.feedMenu = kind;
            this.feedMenuTrigger = trigger;
            const selected = this.feedOptions(kind).findIndex(option => option.value === this.feedValue(kind));
            this.$nextTick(() => this.focusFeedOption(kind, Math.max(0, selected)));
        },
        closeFeedMenu(restoreFocus = false) {
            this.feedMenu = null;
            if (restoreFocus) this.feedMenuTrigger?.focus();
        },
        focusFeedOption(kind, index) {
            const buttons = this.$refs.feedMenus.querySelectorAll(`[data-feed-menu="${kind}"] [role="menuitemradio"]`);
            buttons[(index + buttons.length) % buttons.length]?.focus();
        },
        selectFeedOption(kind, value) {
            if (!this.feedOptions(kind).some(option => option.value === value)) return;
            const changed = value !== this.feedValue(kind);
            if (kind === 'collection') {
                this.collectionFilter = value;
                if (changed) this.sortOrder = value === 'likes' ? 'liked_latest' : 'newest';
            } else {
                this.sortOrder = value;
            }
            this.closeFeedMenu(true);
            if (changed) this.search();
        },
        searchQuery: '',
        searchHighlightTerms: [],
        countError: false,
        error: null,
        showScrollTop: false,

        scrollToTop() {
            const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches;
            window.scrollTo({ top: 0, behavior: reducedMotion ? 'instant' : 'smooth' });
        },

        feedUrl(page) {
            let url = `/api/tweets?collection=${this.collectionFilter}&sort=${this.sortOrder}&page=${page}`;
            if (this.sortOrder === 'random') {
                if (this.randomSeed === null) this.randomSeed = Math.floor(Math.random() * 2147483648);
                url += `&random_seed=${this.randomSeed}`;
            }
            if (this.searchQuery.trim()) {
                url += `&q=${encodeURIComponent(this.searchQuery)}`;
            }
            return url;
        },

        // First pages are kept briefly by URL, so returning to a recent list, or opening
        // one requested at startup or while idle, renders without waiting.
        requestFeedPage(url, { speculative = false } = {}) {
            const cached = feedPages.get(url);
            if (cached && Date.now() - cached.at < FEED_CACHE_MS) return cached;
            if (typeof globalThis.fetch !== 'function') return null;
            const entry = { at: Date.now(), speculative, result: null, promise: null };
            const forget = () => {
                if (feedPages.get(url) === entry) feedPages.delete(url);
            };
            entry.promise = (takeStartupRequest(url) || globalThis.fetch(url)).then(async response => {
                let data;
                try {
                    data = await response.json();
                } catch (parseError) {
                    throw new Error(`Invalid server response (${response.status}): Please check server logs.`);
                }
                const result = { ok: response.ok, status: response.status, data };
                if (result.ok) entry.result = result;
                else forget();
                return result;
            }, error => {
                forget();
                throw error;
            });
            entry.promise.catch(() => {});
            feedPages.set(url, entry);
            if (feedPages.size > FEED_CACHE_LIMIT) feedPages.delete(feedPages.keys().next().value);
            return entry;
        },

        // For changes to the archive, which cached first pages and conversations predate.
        discardCachedPages() {
            feedPages.clear();
            this.threadCache.clear();
        },

        scheduleFeedPrefetch() {
            if (feedPrefetchScheduled || typeof globalThis.fetch !== 'function') return;
            feedPrefetchScheduled = true;
            const prefetch = () => {
                for (const [collection, sort] of DEFAULT_FEEDS) {
                    this.requestFeedPage(`/api/tweets?collection=${collection}&sort=${sort}&page=1&cursor=`);
                }
            };
            if (typeof globalThis.requestIdleCallback === 'function') globalThis.requestIdleCallback(prefetch, { timeout: 3000 });
            else setTimeout(prefetch, 1500);
        },

        async fetchFeedPage(url) {
            const response = await fetch(url);
            let data;
            try {
                data = await response.json();
            } catch (parseError) {
                throw new Error(`Invalid server response (${response.status}): Please check server logs.`);
            }
            return { ok: response.ok, status: response.status, data };
        },

        async fetchResultCount(listUrl, generation) {
            const params = new URL(listUrl, window.location.origin).searchParams;
            const url = `/api/tweets/count?collection=${encodeURIComponent(params.get('collection'))}&q=${encodeURIComponent(params.get('q') || '')}`;
            const controller = new AbortController();
            this.countController = controller;
            try {
                const response = await fetch(url, { signal: controller.signal });
                const data = await response.json();
                if (!response.ok || !Number.isInteger(data.total) || data.total < 0) throw new Error('Count unavailable');
                if (generation === this.feedGeneration) this.total = data.total;
            } catch (error) {
                if (generation === this.feedGeneration && !controller.signal.aborted) this.countError = true;
            } finally {
                if (this.countController === controller) this.countController = null;
            }
        },

        // An appended page can leave the trigger inside the prefetch margin, where the
        // observer does not fire again; keep loading until it is out of reach.
        continueFeedPrefetch() {
            const sentinel = this.$refs?.feedSentinel;
            if (!sentinel || sentinel.offsetParent === null || !this.hasMore || this.loading || this.loadingMore) return;
            if (sentinel.getBoundingClientRect().top < window.innerHeight + FEED_PREFETCH_MARGIN_PX) this.loadMore();
        },

        async fetchTweets(append = false) {
            if (this.archiveReady === false || this.setupForced) return;
            const contentRevision = this.feedContentRevision;
            const requestId = ++this.feedRequestId;
            const requestedPage = append ? this.page + 1 : 1;
            let url = this.feedUrl(requestedPage);
            if (!append) url += '&cursor=';
            else if (this.nextCursor !== null) url += `&cursor=${encodeURIComponent(this.nextCursor)}`;

            let entry = null;
            if (!append) {
                this.feedGeneration += 1;
                this.countController?.abort();
                this.countError = false;
                this.total = null;
                this.hasMore = false;
                this.nextCursor = null;
                window.scrollTo(0, 0);
                // Reloading the list already on screen (retry, repeated search) asks again.
                if (url === feedListUrl) feedPages.delete(url);
                feedListUrl = url;
                entry = this.requestFeedPage(url);
                // Keep the previous list visible (dimmed) until the new one arrives.
                if (!entry?.result) this.loading = true;
            } else {
                this.loadingMore = true;
            }

            try {
                this.error = null;
                let result;
                if (append || !entry) {
                    result = await this.fetchFeedPage(url);
                } else {
                    result = entry.result || await entry.promise;
                    if (!result.ok && entry.speculative) {
                        // The startup request can race setup; ask again now that it is ready.
                        result = await this.fetchFeedPage(url);
                    }
                }
                const data = result.data;

                if (requestId !== this.feedRequestId) return;
                if (result.status === 409 && append && this.nextCursor) {
                    // A new sync changed derived like order. Start a fresh list.
                    feedPages.clear();
                    return await this.fetchTweets();
                }
                if (!result.ok) {
                    throw new Error(data.detail || `Server error: ${result.status}`);
                }

                if (append) {
                    const seen = new Set(this.tweets.map(tweet => tweet.tweet_id));
                    const added = data.tweets.filter(tweet => {
                        if (seen.has(tweet.tweet_id)) return false;
                        seen.add(tweet.tweet_id);
                        return true;
                    });
                    this.tweets = [...this.tweets, ...added];
                    this.renderFeedList(added, { append: true });
                } else {
                    this.tweets = data.tweets;
                    if (contentRevision === this.feedContentRevision) this.feedUpdateAvailable = false;
                    this.searchHighlightTerms = data.highlight_terms || [];
                    this.renderFeedList(data.tweets);
                    this.scheduleFeedPrefetch();
                }
                this.page = data.page;
                this.totalPages = data.pages;
                if (!append || data.total !== null) this.total = data.total;
                this.hasMore = data.has_more === true;
                this.nextCursor = data.next_cursor || null;
                if (!append && data.total === null && new URL(url, window.location.origin).searchParams.get('q')) {
                    if (!data.has_more) this.total = this.tweets.length;
                    else this.fetchResultCount(url, this.feedGeneration);
                }
            } catch (e) {
                if (requestId !== this.feedRequestId) return;
                console.error(e);
                this.error = e.message;
                if (!append) {
                    this.tweets = [];
                    this.renderFeedList([]);
                }
            } finally {
                if (requestId === this.feedRequestId) {
                    this.loading = false;
                    this.loadingMore = false;
                    this.$nextTick(() => this.continueFeedPrefetch());
                }
            }
        },

        // The feed list renders outside Alpine: each card is one HTML string built by
        // renderFeedCard(), a page is a single DOM write, and listeners on the list
        // handle every card. this.tweets remains the data source.
        feedListElement() {
            // A profile search outlives the x-if card that initiated it, so its
            // Alpine magic refs may disappear before the response renders.
            const list = this.$refs?.feedList || boundFeedList;
            if (list && list !== boundFeedList) {
                this.bindFeedList(list);
                boundFeedList = list;
            }
            return list || null;
        },

        renderFeedList(tweets, { append = false } = {}) {
            const list = this.feedListElement();
            if (!list) return;
            // A new list closes the menu first, so no new card renders as its opener.
            if (!append && this.tweetMenuOpen === this.listMenu.key) this.closeTweetMenu(this.listMenu.key);
            const html = tweets.map(tweet => this.renderFeedCard(tweet)).join('');
            const start = append ? list.children.length : 0;
            outsideAlpine(() => {
                if (append) list.insertAdjacentHTML('beforeend', html);
                else list.innerHTML = html;
            });
            this.measureTextClamps(list);
            this.trackFeedCards([...list.children].slice(start), { reset: !append });
        },

        isPhoneLayout() {
            return Boolean(globalThis.matchMedia?.('(max-width: 640px)')?.matches);
        },

        // Mark clamped post text that really overflows, so only it shows Show more.
        // Cards restored during one scroll frame share one measurement, and every
        // clamp is read before any is marked, so layout is computed once per frame.
        measureTextClamps(root) {
            if (!this.isPhoneLayout()) return;
            const measure = () => {
                const roots = [...clampRoots];
                clampRoots.clear();
                const clamps = [...new Set(roots.flatMap(item => [...(item.querySelectorAll?.('.tweet-text-clamp') || [])]))]
                    .filter(clamp => !('measured' in clamp.dataset));
                const clamped = clamps.map(clamp => clamp.scrollHeight > clamp.clientHeight + 1);
                clamps.forEach((clamp, index) => {
                    clamp.dataset.measured = '';
                    clamp.classList.toggle('is-clamped', clamped[index]);
                });
            };
            const scheduled = clampRoots.size > 0;
            clampRoots.add(root);
            if (typeof globalThis.requestAnimationFrame !== 'function') measure();
            else if (!scheduled) globalThis.requestAnimationFrame(measure);
        },

        bindFeedList(list) {
            const menuButton = event => event.target?.closest?.('[data-action="menu"]') || null;
            list.addEventListener('click', event => this.handleFeedClick(event, list));
            // Holding the overflow button opens Tags; pressing it must not prefetch the
            // conversation (the document-level pointerdown listener).
            list.addEventListener('pointerdown', event => {
                const button = menuButton(event);
                if (!button) return;
                event.stopPropagation();
                const tweet = this.feedCardTweet(button);
                if (tweet) this.startTweetMenuPress(event, tweet.tweet_id, tweet.media_tags);
            });
            for (const type of ['pointerup', 'pointercancel']) {
                list.addEventListener(type, event => {
                    if (!menuButton(event)) return;
                    event.stopPropagation();
                    this.cancelTweetMenuPress();
                });
            }
            // pointerleave, load, and error do not bubble; catch them on the way down.
            list.addEventListener('pointerleave', event => {
                if (event.target?.dataset?.action === 'menu') this.cancelTweetMenuPress();
            }, true);
            list.addEventListener('load', event => {
                if (event.target?.classList?.contains('avatar-image')) this.avatarLoaded(event.target);
            }, true);
            list.addEventListener('error', event => {
                if (event.target?.classList?.contains('avatar-image')) this.avatarFailed(event.target);
            }, true);
        },

        // Card regions declare their click behavior with data-action. The nearest one
        // wins, and every action except opening the card's thread stops propagation,
        // as the per-card template's @click.stop handlers did.
        handleFeedClick(event, list) {
            const carouselGap = isCarouselGap(event.target);
            for (let element = event.target; element && element !== list; element = element.parentElement) {
                // Links (tweet text, link cards, Open on X) navigate on their own.
                if (element.tagName === 'A') {
                    event.stopPropagation();
                    return;
                }
                const action = element.dataset?.action;
                if (!action) continue;
                if (action === 'text') {
                    if (this.handleTextClick(event, element)) return;
                    continue;
                }
                if (action === 'thread') {
                    this.openThread(element.dataset.threadId);
                    return;
                }
                // A carousel's padding and gaps belong to the post or quote around it,
                // so none of the media wrappers it sits in consume them.
                if (carouselGap && (action === 'media' || action === 'stop')) continue;
                // Narrow media leaves a blank region in its full-width wrapper.
                // That region belongs to the post; only the media frame consumes clicks.
                if (action === 'media' && !event.target.closest('[data-lightbox-media], .media-video-player, .media-frame')) continue;
                event.stopPropagation();
                if (action === 'quote') {
                    this.openThread(element.dataset.threadId);
                } else if (action === 'profile') {
                    const tweet = this.feedCardTweet(element);
                    if (tweet) this.openProfileCard({ currentTarget: element }, tweet);
                } else if (action === 'reply-profile') {
                    this.openUsernameProfileCard({ currentTarget: element }, element.dataset.username);
                } else if (action === 'menu') {
                    this.toggleListTweetMenu(element);
                } else if (action === 'history') {
                    this.openTweetHistory(element.dataset.historyId);
                }
                return;
            }
        },

        // Thread views wrap x-html media in this instead of @click.stop, so a
        // carousel's padding and gaps open the row or quote around it.
        stopMediaClick(event) {
            if (!isCarouselGap(event.target)) event.stopPropagation();
        },

        // Thread ancestors use the same quote markup and data-action map as feed
        // cards. Their expanded quote text lives outside the feed list, so finish
        // the Show more update in place after the shared click handling.
        handleThreadParentQuoteClick(event, parent) {
            const expanded = event.target?.closest?.('.expand-tweet');
            const text = expanded?.closest?.('[data-action="text"]');
            this.handleFeedClick(event, event.currentTarget);
            if (!text || !text.contains(expanded)) return;
            const quote = this.getQuoteTweet(parent);
            if (quote) text.innerHTML = this.formatText(quote, false, true);
        },

        feedDisplayTweet(tweetObj) {
            const retweet = this.getRetweet(tweetObj);
            return retweet ? this.formatRetweetToTweet(tweetObj, retweet) : tweetObj;
        },

        feedCard(tweetId) {
            for (const card of this.feedListElement()?.children || []) {
                if (card.dataset.tweetId === tweetId) return card;
            }
            return null;
        },

        // The displayed tweet (a retweet's original) of the card containing an element.
        feedCardTweet(element) {
            const tweetId = element?.closest?.('.feed-card')?.dataset.tweetId;
            const tweetObj = tweetId && this.tweets.find(tweet => String(tweet.tweet_id) === tweetId);
            return tweetObj ? this.feedDisplayTweet(tweetObj) : null;
        },

        // Show more expands text in place, as x-html did, leaving media playing.
        expandFeedText(tweetId) {
            const list = this.feedListElement();
            if (!list || !tweetId) return;
            for (const toggle of list.querySelectorAll('.expand-tweet')) {
                if (toggle.dataset.id !== tweetId) continue;
                const text = toggle.closest('[data-action="text"]');
                const tweet = this.feedCardTweet(text);
                if (!text || !tweet) continue;
                const source = text.closest('[data-action="quote"]') ? this.getQuoteTweet(tweet) : tweet;
                outsideAlpine(() => { text.innerHTML = this.formatText(source, false, true); });
            }
            this.placeListMenu();
        },

        loadMore() {
            if (this.loadingMore || this.loading || !this.hasMore) return;
            return this.fetchTweets(true);
        },

        search() { 
            if (this.collectionFilter !== 'likes' && this.sortOrder.startsWith('liked_')) this.sortOrder = 'newest';
            if (!this.searchQuery.trim() && this.sortOrder === 'default') this.sortOrder = this.collectionFilter === 'likes' ? 'liked_latest' : 'newest';
            this.page = 1;
            this.randomSeed = this.sortOrder === 'random' ? Math.floor(Math.random() * 2147483648) : null;
            const route = { viewMode: 'list' };
            if (this.activeRoute.viewMode !== 'list') this.replaceRoute(route, { scrollY: window.scrollY });
            this.activeRoute = route;
            this.closePanel(false);
            this.viewMode = 'list';
            this.fetchTweets();
        },
        searchFrom(username) {
            if(!username) return;
            this.searchQuery = `from:${username}`;
            this.search();
        },
        
        // Calls update for every loaded copy of a tweet: the feed, quote lists, and threads.
        updateLoadedTweet(tweetId, update) {
            const assign = tweet => {
                if (tweet && tweet.tweet_id === tweetId) update(tweet);
            };
            const visitThread = thread => {
                if (!thread) return;
                assign(thread.main);
                for (const tweet of thread.parents || []) assign(tweet);
                for (const tweet of [...(thread.children || []), ...(thread.other_children || [])]) {
                    assign(tweet);
                    for (const reply of tweet.op_replies || []) assign(reply);
                }
            };

            for (const tweet of this.tweets) assign(tweet);
            for (const tweet of this.quotesList) assign(tweet);
            for (const tweet of this.panelQuotesList) assign(tweet);
            visitThread(this.threadData);
            visitThread(this.panelThreadData);
            for (const entry of this.panelStack) {
                if (entry.type === 'thread') visitThread(entry.data);
                else if (entry.type === 'quotes') {
                    for (const tweet of entry.data || []) assign(tweet);
                }
            }
        },

        // Drop a card from the current list without reloading it.
        removeFeedTweet(tweetId) {
            if (!this.tweets.some(tweet => tweet.tweet_id === tweetId)) return;
            this.tweets = this.tweets.filter(tweet => tweet.tweet_id !== tweetId);
            const card = this.feedCard(tweetId);
            if (card) outsideAlpine(() => card.remove());
            if (this.total) this.total -= 1;
        },
        
        expandedTweets: {},
    };
}
