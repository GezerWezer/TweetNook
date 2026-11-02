/**
 * Navigation between the list, posts and quotes: routes and browser history, the post
 * layer on phones (its slide in and out and swipe back), the split panel on wide
 * screens, and opening and leaving a conversation.
 */

function navigationMethods() {
    // The post layer (phones): still copies of covered posts by history depth,
    // the depth on screen, whether a slide or drag is moving it, and a back
    // navigation this page started and is waiting on popstate to finish.
    const detailSnapshots = new Map();
    let shownDepth = 0;
    let detailMotion = false;
    let detailPop = null;
    // A post's slide in, while it runs. Its replies wait for it: rendering them
    // mid-slide stalls iOS long enough to lose most of the motion.
    let detailSlide = null;
    const nextFrame = () => new Promise(resolve => requestAnimationFrame(resolve));

    return {
        activeRoute: { viewMode: 'list' },
        pendingRoute: null,
        pendingRouteState: null,
        navigationDepth: 0,
        routeUsesPanel: false,
        detailLayer: false,
        detailLayerOpen: false,

        // Split panel state
        splitPanel: true,
        panelStack: [],
        panelMode: null,        // 'thread' | 'quotes' | null
        panelThreadData: null,
        panelReplyLimit: THREAD_REPLY_BATCH,
        panelOtherReplies: false,
        panelLoadingThread: false,
        panelQuotesTweetId: null,
        panelQuotesList: [],
        panelQuotesPage: 1,
        panelQuotesTotalPages: 1,
        panelQuotesLoading: false,
        panelQuotesLoadingMore: false,

        parseRoute(pathname) {
            let routePath = pathname;
            const basePath = this.demoBasePath();
            if (this.isDemo && basePath !== '/' && routePath.startsWith(basePath)) {
                routePath = '/' + routePath.slice(basePath.length);
            }
            if (routePath === '/' || routePath === '') return { viewMode: 'list' };
            const match = routePath.match(/^\/post\/(\d+)(?:\/(quotes))?\/?$/);
            if (!match) return null;
            return match[2]
                ? { viewMode: 'quotes', quotesTweetId: match[1] }
                : { viewMode: 'thread', tweetId: match[1] };
        },

        routeUrl(route) {
            const basePath = this.demoBasePath();
            if (route?.viewMode === 'list') return basePath;
            if (route?.viewMode === 'thread' && typeof route.tweetId === 'string' && /^\d+$/.test(route.tweetId)) {
                return `${basePath}post/${encodeURIComponent(route.tweetId)}`;
            }
            if (route?.viewMode === 'quotes' && typeof route.quotesTweetId === 'string' && /^\d+$/.test(route.quotesTweetId)) {
                return `${basePath}post/${encodeURIComponent(route.quotesTweetId)}/quotes`;
            }
            throw new Error('Invalid TweetNook route');
        },

        demoBasePath() {
            if (!this.isDemo) return '/';
            const configured = String(window.TWEETNOOK_DEMO_BASE || '/');
            return `/${configured.replace(/^\/+|\/+$/g, '')}${configured === '/' ? '' : '/'}`;
        },

        routeFromHistoryState(state) {
            if (!state || typeof state !== 'object') return null;
            if (state.viewMode === 'list') return { viewMode: 'list' };
            if (state.viewMode === 'thread' && typeof state.tweetId === 'string' && /^\d+$/.test(state.tweetId)) {
                return { viewMode: 'thread', tweetId: state.tweetId };
            }
            if (state.viewMode === 'quotes' && typeof state.quotesTweetId === 'string' && /^\d+$/.test(state.quotesTweetId)) {
                return { viewMode: 'quotes', quotesTweetId: state.quotesTweetId };
            }
            return null;
        },

        historyStateForRoute(route, { depth = this.navigationDepth, scrollY } = {}) {
            const state = { ...route, tweetNookDepth: depth };
            if (scrollY !== undefined) state.scrollY = scrollY;
            return state;
        },

        currentRoute() {
            return { ...this.activeRoute };
        },

        routeKey(route) {
            if (route.viewMode === 'thread') return `thread:${route.tweetId}`;
            if (route.viewMode === 'quotes') return `quotes:${route.quotesTweetId}`;
            return 'list';
        },

        shouldUseSplitPanel() {
            if (!this.splitPanel) return false;
            // Read the full column sizes even while the panel is hidden or the feed
            // is narrowed to a phone viewport. Exclude any viewport scrollbar.
            const feedWidth = parseFloat(getComputedStyle(document.querySelector('.app-column')).maxWidth);
            const panelWidth = parseFloat(getComputedStyle(document.querySelector('.nook-detail-panel')).width);
            const availableWidth = document.documentElement.clientWidth || window.innerWidth;
            return availableWidth >= feedWidth + panelWidth;
        },

        pushRoute(route) {
            const state = this.historyStateForRoute(route, { depth: this.navigationDepth + 1 });
            history.pushState(state, '', this.routeUrl(route));
            this.navigationDepth = state.tweetNookDepth;
            this.activeRoute = { ...route };
            return state;
        },

        replaceRoute(route, { scrollY, depth = this.navigationDepth } = {}) {
            const state = this.historyStateForRoute(route, { depth, scrollY });
            history.replaceState(state, '', this.routeUrl(route));
            this.navigationDepth = depth;
            this.activeRoute = { ...route };
            return state;
        },

        savePanelSnapshot() {
            const top = this.panelStack[this.panelStack.length - 1];
            if (!top) return;
            top.scrollY = this.$refs.detailPanel?.scrollTop || 0;
            if (this.panelMode === 'thread') top.data = this.panelThreadData?.partial ? null : this.panelThreadData;
            else if (this.panelMode === 'quotes') top.data = this.panelQuotesList;
        },

        saveCurrentRouteState() {
            const route = this.currentRoute();
            if (route.viewMode === 'list') {
                return this.replaceRoute(route, { scrollY: window.scrollY });
            }
            if (this.shouldUseSplitPanel()) this.savePanelSnapshot();
            return this.replaceRoute(route, { scrollY: this.detailScrollY() });
        },

        async applyRoute(route, { fromPopState = false, state = null } = {}) {
            if (!route) return false;
            window.TweetNookArticles?.closeFocus?.({restoreFocus: false});
            const routeState = state || this.historyStateForRoute(route);
            if (state && Number.isInteger(routeState.tweetNookDepth) && routeState.tweetNookDepth >= 0) {
                this.navigationDepth = routeState.tweetNookDepth;
            } else if (fromPopState) {
                this.navigationDepth = 0;
            }
            this.activeRoute = { ...route };
            const previousDepth = shownDepth;

            if (!this.archiveResourcesLoaded) {
                this.pendingRoute = { ...route };
                this.pendingRouteState = routeState;
                return false;
            }

            this.pendingRoute = null;
            this.pendingRouteState = null;
            this.routeUsesPanel = this.shouldUseSplitPanel();
            shownDepth = this.navigationDepth;
            const useLayer = this.usesDetailLayer();
            if (this.detailLayer && !useLayer) this.closeDetailLayer();
            this.detailLayer = useLayer;
            if (route.viewMode === 'list' && (this.detailLayerOpen || detailPop)) {
                // The feed stayed laid out and scrolled beneath the post.
                const pop = detailPop;
                detailPop = null;
                if (pop) await pop.done;
                else {
                    detailSnapshots.clear();
                    await this.slideDetailAway(0, { instant: !this.historyPopAnimates() });
                }
                this.closePanel(false);
                this.viewMode = 'list';
                return true;
            }
            if (route.viewMode === 'list') {
                this.closePanel(false);
                this.viewMode = 'list';
                if (routeState?.scrollY !== undefined) {
                    const scrollY = routeState.scrollY;
                    window.scrollTo(0, scrollY);
                    this.$nextTick(() => {
                        window.scrollTo(0, scrollY);
                        setTimeout(() => window.scrollTo(0, scrollY), 20);
                    });
                }
                return true;
            }

            if (this.shouldUseSplitPanel()) {
                this.viewMode = 'list';
                await this.applyPanelRoute(route, routeState);
                return true;
            }

            this.closePanel(false);
            if (this.detailLayer) {
                await this.showDetailRoute(route, routeState, previousDepth);
                return true;
            }
            if (route.viewMode === 'thread') await this.showFullThread(route.tweetId);
            else await this.showFullQuotes(route.quotesTweetId);
            return true;
        },

        // Phones show a post on a layer pushed over the feed (see DETAIL_SPRING).
        usesDetailLayer() {
            return !this.shouldUseSplitPanel() && this.isPhoneLayout();
        },

        // The view the page header and feed belong to: the list stays beneath a post layer.
        get pageView() {
            return this.detailLayer ? 'list' : this.viewMode;
        },

        detailScrollY() {
            return this.detailLayer ? this.$refs.detailStage?.scrollTop || 0 : window.scrollY;
        },

        // In a browser tab, Safari's own back swipe has already animated the page.
        historyPopAnimates() {
            const ios = /iP(hone|ad|od)/.test(globalThis.navigator?.userAgent || '');
            return !ios || Boolean(globalThis.navigator?.standalone);
        },

        detailAnimates() {
            return !window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches;
        },

        // The pieces a slide moves: the post layer, what lies beneath it (the feed,
        // or the still copy of the post it covers) and the shade between them.
        detailParts(snapshot = null) {
            const layer = this.$refs.detailStage;
            const underlay = layer.parentElement.querySelector('.detail-underlay');
            const under = snapshot ? [underlay] : [...document.querySelectorAll('[data-feed-stage]')];
            // As in X, the top bars do not slide: over the feed, the post's bar fades
            // out in place while the page header's title and tabs fade in. Between
            // two posts the bar reads the same and stays.
            const bars = snapshot ? {} : {
                top: layer.parentElement.querySelector('.detail-layer-header'),
                beneath: document.querySelector('.nook-header'),
            };
            return { layer, underlay, snapshot, under, bars, shade: this.$refs.detailShade, width: layer.clientWidth || window.innerWidth };
        },

        setDetailOffset(parts, offset) {
            const styles = detailTransitionStyles(offset, parts.width);
            parts.layer.style.transform = styles.layer;
            parts.under.forEach(element => { element.style.transform = styles.under; });
            parts.shade.style.opacity = styles.shade;
            const bars = detailBarOpacities(offset, parts.width);
            if (parts.bars.top) parts.bars.top.style.opacity = String(bars.top);
            // A variable, so tabs tucked away by scrolling stay hidden (header.css).
            parts.bars.beneath?.style.setProperty('--detail-beneath', String(bars.beneath));
        },

        // Settle the post frame by frame (see detailSettle). The clock advances at
        // most one 60Hz frame per frame, so a frame iOS stalls pauses the motion
        // instead of skipping most of it.
        animateDetailOffset(parts, from, to, { velocity = 0, animate = true } = {}) {
            if (!animate) {
                this.setDetailOffset(parts, to);
                return Promise.resolve();
            }
            const leaving = to > from;
            return new Promise(resolve => {
                let clock = 0, last = null;
                const step = now => {
                    clock += last === null ? 0 : Math.min(now - last, 1000 / 60);
                    last = now;
                    const { offset, done } = detailSettle(from, to, velocity, clock, leaving, parts.width);
                    this.setDetailOffset(parts, done ? to : offset);
                    if (done) resolve();
                    else requestAnimationFrame(step);
                };
                step(performance.now());
            });
        },

        beginDetailMotion(parts) {
            detailMotion = true;
            if (parts.snapshot) {
                parts.underlay.replaceChildren(parts.snapshot.node);
                parts.underlay.classList.add('is-shown');
                parts.underlay.scrollTop = parts.snapshot.scrollTop;
            }
            parts.shade.classList.add('is-shown');
            parts.bars.beneath?.classList.add('is-beneath-detail');
            parts.layer.classList.add('is-moving');
            parts.under.forEach(element => { element.style.willChange = 'transform'; });
        },

        endDetailMotion(parts) {
            parts.layer.classList.remove('is-moving');
            parts.layer.style.transform = '';
            parts.under.forEach(element => { element.style.transform = ''; element.style.willChange = ''; });
            if (parts.bars.top) parts.bars.top.style.opacity = '';
            parts.bars.beneath?.style.removeProperty('--detail-beneath');
            parts.bars.beneath?.classList.remove('is-beneath-detail');
            parts.shade.classList.remove('is-shown');
            parts.shade.style.opacity = '';
            parts.underlay.classList.remove('is-shown');
            parts.underlay.replaceChildren();
            detailMotion = false;
        },

        closeDetailLayer(parts = this.detailParts()) {
            // Hidden now, not on Alpine's next pass, so it never shows again at full strength.
            if (parts.bars.top) parts.bars.top.style.display = 'none';
            this.endDetailMotion(parts);
            parts.layer.classList.remove('is-open');
            this.detailLayerOpen = false;
            openDetailLayer = null;
            detailSnapshots.clear();
            this.autoplayNextVideo();
        },

        // Slide a post in over the feed, or over the post it was opened from.
        async pushDetailLayer(show, coveredDepth, animate) {
            const layer = this.$refs.detailStage;
            let snapshot = null;
            if (this.detailLayerOpen) {
                snapshot = { node: detailSnapshot(layer.firstElementChild), scrollTop: layer.scrollTop };
                for (const depth of [...detailSnapshots.keys()]) if (depth >= coveredDepth) detailSnapshots.delete(depth);
                detailSnapshots.set(coveredDepth, snapshot);
            } else {
                detailSnapshots.clear();
            }
            this.detailLayerOpen = true;
            layer.classList.add('is-open');
            openDetailLayer = layer;
            this.autoplayNextVideo();
            const parts = animate && this.detailAnimates() ? this.detailParts(snapshot) : null;
            if (parts) {
                // Paint the post in place, all but transparent, before sliding it in:
                // iOS paints nothing off screen, and painting it in the slide's first
                // frame stalls that frame for 100ms while the clock runs on.
                this.beginDetailMotion(parts);
                this.setDetailOffset(parts, parts.width);
                layer.style.opacity = '0.01';
            }
            const shown = show();
            await this.$nextTick();
            if (parts) {
                await nextFrame();
                await nextFrame();
                layer.style.opacity = '';
                detailSlide = this.animateDetailOffset(parts, parts.width, 0);
                await detailSlide;
                detailSlide = null;
                this.endDetailMotion(parts);
            }
            await shown;
        },

        // Slide the post away, uncovering the view beneath. A back swipe hands
        // over its pieces, where the finger let go and how fast it was moving.
        // Uncovering the feed closes the layer; uncovering a post's still copy
        // leaves it on screen until the route has rendered the post itself.
        async slideDetailAway(targetDepth, { from = 0, velocity = 0, parts = null, instant = false } = {}) {
            if (!parts) {
                parts = this.detailParts(detailSnapshots.get(targetDepth) || null);
                this.beginDetailMotion(parts);
            }
            await this.animateDetailOffset(parts, from, parts.width, { velocity, animate: !instant && this.detailAnimates() });
            if (parts.snapshot) return parts;
            this.closeDetailLayer(parts);
            return null;
        },

        async showDetailRoute(route, state, previousDepth) {
            const depth = this.navigationDepth;
            const pop = detailPop;
            detailPop = null;
            const restore = { scrollTop: undefined };
            const show = () => route.viewMode === 'thread'
                ? this.showFullThread(route.tweetId, restore)
                : this.showFullQuotes(route.quotesTweetId, restore);
            const goingBack = pop || (this.detailLayerOpen && depth < previousDepth);
            if (!goingBack) {
                // A forward step slides in; the same depth (a resize) or the first route does not.
                await this.pushDetailLayer(show, previousDepth, depth > previousDepth);
                return;
            }
            const parts = pop ? await pop.done : await this.slideDetailAway(depth, { instant: !this.historyPopAnimates() });
            if (!parts) {
                // Nothing of this post was kept, so it returns without a slide.
                await this.pushDetailLayer(show, previousDepth, false);
                return;
            }
            // Render the post beneath its still copy, where the reader left it, then swap.
            restore.scrollTop = parts.snapshot.scrollTop;
            detailSnapshots.delete(depth);
            await show();
            await this.$nextTick();
            this.endDetailMotion(parts);
            this.autoplayNextVideo();
        },

        // Back and forward, layout changes between the split panel and the post layer,
        // and list views reached without a route.
        listenForNavigation() {
            window.addEventListener('popstate', (event) => {
                // A back this page started already stopped the post's videos.
                if (!detailPop) this.pauseAllVideos();
                const route = this.routeFromHistoryState(event.state)
                    || this.parseRoute(window.location.pathname);
                if (route) return this.applyRoute(route, { fromPopState: true, state: event.state });
            });
            window.addEventListener('resize', () => {
                this.placeListMenu();
                const usePanel = this.shouldUseSplitPanel();
                if (usePanel === this.routeUsesPanel && this.usesDetailLayer() === this.detailLayer) return;
                this.routeUsesPanel = usePanel;
                if (this.activeRoute.viewMode !== 'list' && this.archiveResourcesLoaded) {
                    this.applyRoute(this.activeRoute, { state: history.state });
                }
            });

            this.$nextTick?.(() => this.attachDetailSwipe());
            // Search and other list views reached without a route leave the post layer.
            this.$watch?.('viewMode', mode => { if (mode === 'list' && this.detailLayerOpen && !detailMotion) this.closeDetailLayer(); });
        },

        // A rightward drag anywhere on a post pulls it back toward the view
        // beneath, following the finger, as in X's app.
        attachDetailSwipe() {
            const layer = this.$refs.detailStage;
            if (!layer?.addEventListener) return;
            let gesture = null;
            layer.addEventListener('touchstart', event => {
                gesture = null;
                if (!this.detailLayerOpen || detailMotion || detailPop || event.touches.length !== 1) return;
                if (startsInSideScroller(event.target, layer)) return;
                const touch = event.touches[0];
                gesture = { x: touch.clientX, y: touch.clientY, offset: 0, samples: [], parts: null };
            }, { passive: true });
            layer.addEventListener('touchmove', event => {
                if (!gesture) return;
                const touch = event.touches[0];
                if (!gesture.parts) {
                    const intent = event.touches.length === 1 ? swipeBackIntent(touch.clientX - gesture.x, touch.clientY - gesture.y) : false;
                    if (intent === false) { gesture = null; return; }
                    // Hold the page still while a sideways start is still undecided.
                    if (intent === null) {
                        if (Math.abs(touch.clientX - gesture.x) > Math.abs(touch.clientY - gesture.y)) event.preventDefault();
                        return;
                    }
                    // Follow the finger from here, so the post does not jump by the slop.
                    gesture.x = touch.clientX;
                    gesture.parts = this.detailParts(detailSnapshots.get(Math.max(0, this.navigationDepth - 1)) || null);
                    this.beginDetailMotion(gesture.parts);
                }
                event.preventDefault();
                gesture.offset = Math.max(0, touch.clientX - gesture.x);
                gesture.samples.push([event.timeStamp, touch.clientX]);
                while (gesture.samples.length > 2 && event.timeStamp - gesture.samples[0][0] > 100) gesture.samples.shift();
                this.setDetailOffset(gesture.parts, gesture.offset);
            }, { passive: false });
            const release = event => {
                const ended = gesture;
                gesture = null;
                if (!ended?.parts) return;
                const [first, last] = [ended.samples[0], ended.samples.at(-1)];
                const velocity = first && last && last[0] > first[0] ? (last[1] - first[1]) / (last[0] - first[0]) : 0;
                if (event.type !== 'touchcancel' && swipeBackCompletes(ended.offset, ended.parts.width, velocity)) {
                    this.goBack({ from: ended.offset, velocity, parts: ended.parts });
                    return;
                }
                this.animateDetailOffset(ended.parts, ended.offset, 0, { velocity, animate: this.detailAnimates() })
                    .then(() => this.endDetailMotion(ended.parts));
            };
            layer.addEventListener('touchend', release);
            layer.addEventListener('touchcancel', release);
        },

        async applyPanelRoute(route, state) {
            this.savePanelSnapshot();
            const key = this.routeKey(route);
            let matchIndex = -1;
            for (let index = this.panelStack.length - 1; index >= 0; index--) {
                const entry = this.panelStack[index];
                if (entry.routeKey !== key) continue;
                if (Number.isInteger(state?.tweetNookDepth) && entry.historyDepth !== state.tweetNookDepth) continue;
                matchIndex = index;
                break;
            }
            if (matchIndex >= 0) {
                this.panelStack = this.panelStack.slice(0, matchIndex + 1);
            } else {
                if (state?.tweetNookDepth === 0) this.panelStack = [];
                this.panelStack.push({
                    type: route.viewMode,
                    tweetId: route.viewMode === 'thread' ? route.tweetId : route.quotesTweetId,
                    routeKey: key,
                    historyDepth: state?.tweetNookDepth,
                });
            }

            const entry = this.panelStack[this.panelStack.length - 1];
            this.panelMode = route.viewMode;
            this.$nextTick(() => {
                if (this.$refs.detailPanel) this.$refs.detailPanel.scrollTop = entry.scrollY || 0;
            });
            if (route.viewMode === 'thread') {
                this.panelQuotesList = [];
                if (entry.data) {
                    this.panelThreadData = entry.data;
                    this.panelReplyLimit = THREAD_REPLY_BATCH;
                    this.panelOtherReplies = false;
                    this.panelLoadingThread = false;
                    return;
                }
                const seed = this.threadSeed(route.tweetId);
                this.panelThreadData = seed;
                this.panelReplyLimit = THREAD_REPLY_BATCH;
                this.panelOtherReplies = false;
                this.panelLoadingThread = !seed;
                try {
                    const data = await this.loadThread(route.tweetId);
                    entry.data = data;
                    if (this.panelStack.at(-1) === entry) {
                        this.keepThreadMainInPlace(true, () => { this.panelThreadData = data; });
                    }
                } catch (error) {
                    console.error(error);
                    if (this.panelStack.at(-1) === entry) this.goBack();
                } finally {
                    if (this.panelStack.at(-1) === entry) this.panelLoadingThread = false;
                }
                return;
            }

            this.panelThreadData = null;
            this.panelQuotesTweetId = route.quotesTweetId;
            this.panelQuotesPage = 1;
            if (entry.data) {
                this.panelQuotesList = entry.data;
                this.panelQuotesLoading = false;
                return;
            }
            this.panelQuotesList = [];
            await this.fetchPanelQuotes();
        },

        async showFullThread(tweetId, restore = {}) {
            this.viewMode = 'thread';
            const seed = this.threadSeed(tweetId);
            this.threadData = seed;
            this.threadReplyLimit = THREAD_REPLY_BATCH;
            this.threadOtherReplies = false;
            this.threadBottomSpace = 0;
            this.loadingThread = !seed;
            if (this.detailLayer) this.$refs.detailStage.scrollTop = 0;
            else window.scrollTo(0, 0);
            let pin = false;
            try {
                const data = await this.loadThread(tweetId);
                await detailSlide;
                if (this.routeKey(this.activeRoute) === `thread:${tweetId}` && !this.shouldUseSplitPanel()) {
                    // Parents arrive above the focal tweet; pinThreadMain keeps it in place.
                    this.threadData = data;
                    pin = Boolean(data?.parents?.length);
                }
            } catch (error) {
                console.error(error);
                if (this.routeKey(this.activeRoute) === `thread:${tweetId}`) this.goBack();
            } finally {
                this.loadingThread = false;
            }
            if (restore.scrollTop !== undefined) await this.restoreDetailScroll(restore.scrollTop);
            else if (pin) this.pinThreadMain();
        },

        // A post returned to by going back sits where the reader left it.
        async restoreDetailScroll(scrollTop) {
            await this.$nextTick();
            this.$refs.detailStage.scrollTop = scrollTop;
        },

        async showFullQuotes(tweetId, restore = {}) {
            this.viewMode = 'quotes';
            this.quotesTweetId = tweetId;
            this.quotesPage = 1;
            this.quotesList = [];
            this.quotesLoading = true;
            if (this.detailLayer) this.$refs.detailStage.scrollTop = 0;
            else window.scrollTo(0, 0);
            await this.fetchQuotes();
            if (restore.scrollTop !== undefined) await this.restoreDetailScroll(restore.scrollTop);
        },

        toggleSplitPanel(val) {
            const route = this.currentRoute();
            this.splitPanel = val;
            this.routeUsesPanel = this.shouldUseSplitPanel();
            localStorage.setItem('tvx-split-panel', val);
            if (route.viewMode === 'list') this.closePanel(false);
            else if (this.archiveResourcesLoaded) return this.applyRoute(route, { state: history.state });
        },

        closePanel(syncRoute = true) {
            this.panelStack = [];
            this.panelMode = null;
            this.panelThreadData = null;
            this.panelQuotesList = [];
            if (syncRoute && this.activeRoute.viewMode !== 'list') {
                const route = { viewMode: 'list' };
                const state = this.replaceRoute(route, { scrollY: window.scrollY });
                this.applyRoute(route, { state });
            }
        },

        panelGoBack() {
            if (this.navigationDepth > 0) this.goBack();
        },

        async openThread(tweetId, fromPopState = false, fromPanel = false) {
            void fromPanel;
            if (typeof tweetId !== 'string' || !/^\d+$/.test(tweetId)) return;
            const route = { viewMode: 'thread', tweetId };
            if (fromPopState) return this.applyRoute(route, { fromPopState: true });
            // A tap during a slide waits for it; X ignores taps on a moving screen.
            if (detailMotion || detailPop) return;
            this.saveCurrentRouteState();
            const state = this.pushRoute(route);
            return this.applyRoute(route, { state });
        },
        
        async openQuotes(tweetId, fromPanel = false) {
            void fromPanel;
            if (typeof tweetId !== 'string' || !/^\d+$/.test(tweetId)) return;
            const route = { viewMode: 'quotes', quotesTweetId: tweetId };
            if (detailMotion || detailPop) return;
            this.saveCurrentRouteState();
            const state = this.pushRoute(route);
            return this.applyRoute(route, { state });
        },
        
        goBack(swipe = {}) {
            this.pauseAllVideos();
            this.savePanelSnapshot();
            if (this.detailLayer && this.detailLayerOpen && !detailPop) {
                // The post slides away now; the route catches up when popstate arrives,
                // which iOS can deliver hundreds of milliseconds later.
                if (detailMotion && !swipe.parts) return;
                const targetDepth = Math.max(0, this.navigationDepth - 1);
                detailPop = { done: this.slideDetailAway(targetDepth, swipe) };
                if (this.navigationDepth > 0) {
                    history.back();
                    return;
                }
            } else if (detailPop) {
                return;
            }
            if (this.navigationDepth > 0) {
                history.back();
                return;
            }
            const route = { viewMode: 'list' };
            const state = this.replaceRoute(route, { scrollY: window.scrollY, depth: 0 });
            this.applyRoute(route, { state });
        },
    };
}
