/**
 * Conversations and quotes: loading and caching a post's thread (seeded from the post
 * already on screen), revealing replies in batches, keeping the focal post in place as
 * parents arrive above it, and loading quote lists for the page and the split panel.
 */

function threadMethods() {
    return {
        threadData: null,
        threadReplyLimit: THREAD_REPLY_BATCH,
        threadOtherReplies: false,
        threadBottomSpace: 0, // px below a short thread, so its focal tweet can reach the top
        loadingThread: false,
        threadCache: new Map(),
        threadCacheTtlMs: 60000,
        
        quotesTweetId: null,
        quotesList: [],
        quotesPage: 1,
        quotesTotalPages: 1,
        quotesLoading: false,
        quotesLoadingMore: false,
        
        // Render a tweet the page already shows while its conversation loads.
        threadSeed(tweetId) {
            const known = [];
            const add = tweet => {
                if (!tweet || typeof tweet !== 'object') return;
                known.push(tweet);
                if (tweet.quoted_tweet) known.push(tweet.quoted_tweet);
                if (tweet.retweeted_tweet) known.push(tweet.retweeted_tweet);
            };
            this.tweets.forEach(add);
            for (const data of [this.threadData, this.panelThreadData]) {
                if (!data) continue;
                add(data.main);
                (data.parents || []).forEach(add);
                for (const child of [...(data.children || []), ...(data.other_children || [])]) {
                    add(child);
                    (child.op_replies || []).forEach(add);
                }
            }
            [...this.quotesList, ...this.panelQuotesList].forEach(add);
            const match = known.find(tweet => tweet.tweet_id === tweetId && tweet.author && !this.isPlaceholderTweet(tweet));
            if (!match) return null;
            const { op_replies, thread_depth, parent_tweet_id, ...main } = match;
            return { main, parents: [], children: [], reply_tree_truncated: false, partial: true };
        },

        prefetchThread(tweetId) {
            if (typeof tweetId === 'string' && /^\d+$/.test(tweetId) && typeof globalThis.fetch === 'function') {
                this.loadThread(tweetId).catch(() => {});
            }
        },

        // Parents arrive above the tweet already on screen; keep it where it was.
        keepThreadMainInPlace(panel, update) {
            const root = panel ? this.$refs?.detailPanel : globalThis.document;
            const main = () => root?.querySelector?.(`[data-thread-main="${panel ? 'panel' : 'page'}"]`);
            const visibleTop = () => {
                const element = main();
                if (!element || !element.getClientRects?.().length) return undefined;
                const rect = element.getBoundingClientRect?.();
                if (!rect || rect.width <= 0 || rect.height <= 0) return undefined;
                return rect.top;
            };
            const before = visibleTop();
            update();
            if (before === undefined) return;
            this.$nextTick(() => {
                const after = visibleTop();
                if (after === undefined || after === before) return;
                if (panel) root.scrollTop += after - before;
                else if (this.detailLayer) this.$refs.detailStage.scrollTop += after - before;
                else window.scrollBy(0, after - before);
            });
        },

        // Like X, a page with few replies still scrolls far enough for the focal
        // tweet to sit at the top: add the missing room below the thread first.
        scrollPageBy(delta) {
            this.scrollPageTo(this.detailScrollY() + delta);
        },

        // Scroll, then add whatever room was missing (a short page's minimum height
        // absorbs part of any spacer) and try again.
        scrollPageTo(target, attempts = 3) {
            if (this.detailLayer) this.$refs.detailStage.scrollTop = target;
            else window.scrollTo({ top: target, behavior: 'instant' });
            const missing = Math.ceil(target - this.detailScrollY());
            if (missing <= 0 || attempts <= 0) return;
            // A screen of room always suffices; never grow past it.
            this.threadBottomSpace = Math.min(this.threadBottomSpace + missing, window.innerHeight);
            // Safari can still lay out the old height on the next tick, so every retry
            // fell short (a reply opened under its parent's tail): size the spacer now.
            const root = this.detailLayer ? this.$refs.detailStage : globalThis.document;
            const spacer = root?.querySelector?.('.thread-bottom-space');
            if (spacer) spacer.style.height = `${this.threadBottomSpace}px`;
            this.$nextTick(() => this.scrollPageTo(target, attempts - 1));
        },

        // Parents render above the focal tweet; scroll so it sits where the first
        // parent starts, whether or not a seed was already on screen (a prefetched
        // conversation can replace the seed before it is ever measured).
        pinThreadMain(frames = 10) {
            this.$nextTick(() => {
                const main = globalThis.document?.querySelector?.('[data-thread-main="page"]');
                const first = main?.parentElement?.querySelector?.(':scope > .thread-row');
                if (!main || !first || main === first) return;
                // The view can still be hidden for a frame or two after the route change.
                if (!main.getClientRects?.().length) {
                    if (frames > 0 && typeof globalThis.requestAnimationFrame === 'function') {
                        globalThis.requestAnimationFrame(() => this.pinThreadMain(frames - 1));
                    }
                    return;
                }
                const delta = main.getBoundingClientRect().top - first.getBoundingClientRect().top - this.detailScrollY();
                if (Math.abs(delta) >= 1) this.scrollPageBy(delta);
            });
        },

        async fetchPanelQuotes(append = false) {
            if (append) this.panelQuotesLoadingMore = true;
            else this.panelQuotesLoading = true;
            try {
                const res = await fetch(`/api/tweets/${this.panelQuotesTweetId}/quotes?page=${this.panelQuotesPage}`);
                const data = await res.json();
                if (append) this.panelQuotesList = [...this.panelQuotesList, ...data.tweets];
                else this.panelQuotesList = data.tweets;
                this.panelQuotesTotalPages = Math.ceil(data.total / data.limit);
                // Cache on stack
                const top = this.panelStack[this.panelStack.length - 1];
                if (top) top.data = this.panelQuotesList;
            } catch (e) {
                console.error('Failed to fetch panel quotes', e);
            } finally {
                this.panelQuotesLoading = false;
                this.panelQuotesLoadingMore = false;
            }
        },
        
        visibleReplies(children, limit) {
            const visible = [];
            let shown = 0;
            for (const child of children || []) {
                const size = 1 + (child.op_replies?.length || 0);
                if (visible.length && shown + size > limit) break;
                visible.push(child);
                shown += size;
            }
            return visible;
        },

        hasHiddenReplies(children, limit) {
            return this.visibleReplies(children, limit).length < (children?.length || 0);
        },

        showMoreReplies(panel = false) {
            if (panel) this.panelReplyLimit += THREAD_REPLY_BATCH;
            else this.threadReplyLimit += THREAD_REPLY_BATCH;
        },

        // Mention-only replies stay behind "Show other replies" until requested.
        threadReplies(data, showOther) {
            const children = data?.children || [];
            return showOther ? [...children, ...(data?.other_children || [])] : children;
        },

        // Like X's "Show probable spam", the button waits until every regular reply is shown.
        hasOtherReplies(data, limit, shown) {
            return !shown && Boolean(data?.other_children?.length) && !this.hasHiddenReplies(data.children, limit);
        },

        // The revealed section starts with an "Other replies" heading, like X's "Probable spam".
        firstOtherReplyId(data, shown) {
            return shown ? data?.other_children?.[0]?.tweet_id ?? null : null;
        },

        showOtherReplies(panel = false) {
            if (panel) this.panelOtherReplies = true;
            else this.threadOtherReplies = true;
            this.showMoreReplies(panel);
        },

        async loadThread(tweetId) {
            const cached = this.threadCache.get(tweetId);
            if (cached && Date.now() - cached.loadedAt < this.threadCacheTtlMs) {
                return cached.request;
            }

            const request = (async () => {
                const url = `/api/tweets/${tweetId}`;
                const res = await (takeStartupRequest(url) || fetch(url));
                if (!res.ok) throw new Error('Failed to load thread');
                return res.json();
            })();
            const cacheEntry = { request, loadedAt: Date.now() };
            this.threadCache.set(tweetId, cacheEntry);
            if (this.threadCache.size > 25) {
                this.threadCache.delete(this.threadCache.keys().next().value);
            }
            try {
                return await request;
            } catch (error) {
                if (this.threadCache.get(tweetId) === cacheEntry) {
                    this.threadCache.delete(tweetId);
                }
                throw error;
            }
        },

        async fetchQuotes(append = false) {
            if (append) this.quotesLoadingMore = true;
            else this.quotesLoading = true;
            
            try {
                const res = await fetch(`/api/tweets/${this.quotesTweetId}/quotes?page=${this.quotesPage}`);
                const data = await res.json();
                
                if (append) this.quotesList = [...this.quotesList, ...data.tweets];
                else this.quotesList = data.tweets;
                
                this.quotesTotalPages = Math.ceil(data.total / data.limit);
            } catch (e) {
                console.error(e);
            } finally {
                this.quotesLoading = false;
                this.quotesLoadingMore = false;
            }
        },
    };
}
