/**
 * Main Alpine.js application for the TweetNook Web UI.
 */

const AVATAR_URL_VERSION = 3;
// Saving the avatar download setting changes avatar URLs, so placeholders the
// browser kept while downloads were off are asked for again (see avatarUrl and
// submitConfigChanges).
const AVATAR_REVISION_KEY = 'tweetnook.avatar-revision';
let avatarRevision = '';
try { avatarRevision = localStorage.getItem(AVATAR_REVISION_KEY) || ''; } catch (_) { /* Storage can be disabled. */ }
const avatarInitialSegmenter = typeof Intl.Segmenter === 'function'
    ? new Intl.Segmenter(undefined, { granularity: 'grapheme' }) : null;
function avatarInitial(name) {
    const value = String(name || '');
    return (avatarInitialSegmenter
        ? avatarInitialSegmenter.segment(value)[Symbol.iterator]().next().value?.segment
        : Array.from(value)[0]) || '?';
}
// Replies (including nested ones) rendered per batch in a thread; a saved reply tree
// can hold 250 tweets, and rendering them all at once delays the first paint.
const THREAD_REPLY_BATCH = 30;
// Hovering a tweet this long starts loading its conversation.
const THREAD_HOVER_PREFETCH_MS = 150;
// iOS opens a Home Screen web app without a manifest in the "browser" display
// mode; only navigator.standalone tells it apart from a Safari tab, whose
// toolbar reserves a strip at the bottom of the screen.
if (globalThis.navigator?.standalone) document.documentElement.classList.add('ios-home-screen');

// Feed cards are plain markup handled by delegated listeners. Writing them while
// Alpine's observer is paused keeps Alpine from walking (and binding) every card.
function outsideAlpine(update) {
    return window.Alpine?.mutateDom ? window.Alpine.mutateDom(update) : update();
}

// index.html requests the first feed page and a linked conversation before the
// scripts load. Each response is used once, by the first request for its URL.
function takeStartupRequest(url) {
    const requests = window.TWEETNOOK_STARTUP;
    const response = requests?.[url] || null;
    if (response) delete requests[url];
    return response;
}

// Status responses are JSON trees. Retain unchanged reactive objects so polling
// doesn't invalidate every binding in the mounted (including hidden) tray pages.
function reconcileActivityValue(current, next) {
    if (current === next) return current;
    if (!current || !next || typeof current !== 'object' || typeof next !== 'object'
        || Array.isArray(current) !== Array.isArray(next)) return next;
    if (Array.isArray(next) && (current.length !== next.length
        || next.some((item, index) => item?.key !== current[index]?.key))) return next;

    for (const key of Object.keys(current)) {
        if (!Object.prototype.hasOwnProperty.call(next, key)) delete current[key];
    }
    for (const key of Object.keys(next)) {
        if (key === '__proto__' || key === 'constructor' || key === 'prototype') continue;
        const value = reconcileActivityValue(current[key], next[key]);
        if (current[key] !== value) current[key] = value;
    }
    return current;
}

function tweetApp() {
    // Settles when the startup request for the first page does (see afterStartupFeed).
    let startupFeedSettled = Promise.resolve();

    const app = {
        isDemo: window.TWEETNOOK_DEMO === true,
        viewMode: 'list',
        videoObserver: null,
        videoAutoplayObserver: null,
        mediaPrefetchObserver: null,

        showKeyboardShortcuts: false,
        destroy() {
            this.stopActivityPolling();
            this.cancelQueuedLightboxMotion();
        },

        showSettingsModal: false,
        settingsBodyOverflow: null,
        settingsTab: 'appearance',
        nookSettingsDetail: false,
        nookSyncCollections: ['bookmarks', 'likes'],
        nookLogOpenRunId: null,
        nookLogRun: null,
        nookLogLines: null,
        nookDetailScrolled: false,
        videoPlayback: 'autoplay', // 'autoplay' | 'paused'
        // Shared by feed, detail, and lightbox players for the current page.
        videoMuted: true,
        videoVolume: 1,

        initApp() {
            if ('scrollRestoration' in history) {
                history.scrollRestoration = 'manual';
            }

            const savedTheme = localStorage.getItem('tvx-theme') || localStorage.getItem('theme');
            if (this.isDemo && (!savedTheme || !THEMES[savedTheme])) {
                const colorScheme = window.matchMedia('(prefers-color-scheme: dark)');
                const applySystemTheme = () => {
                    const selected = localStorage.getItem('tvx-theme') || localStorage.getItem('theme');
                    if (!selected || !THEMES[selected]) {
                        this.applyTheme(colorScheme.matches ? 'classic-dark' : 'classic-light', false);
                    }
                };
                applySystemTheme();
                colorScheme.addEventListener('change', applySystemTheme);
            } else if (savedTheme && THEMES[savedTheme]) {
                this.applyTheme(savedTheme);
            } else {
                this.applyTheme('classic-light');
            }
            
            const savedFont = localStorage.getItem('tvx-font');
            const savedFontFamily = localStorage.getItem('tvx-font-family');
            if (savedFont && savedFontFamily) {
                this.fontFamily = savedFont;
                document.body.style.fontFamily = savedFontFamily;
            }

            window.addEventListener('scroll', () => {
                this.showScrollTop = window.scrollY > 300;
                this.placeListMenu();
            });
            
            const savedFontSize = localStorage.getItem('tvx-font-size');
            const savedFontSizePx = localStorage.getItem('tvx-font-size-px');
            if (savedFontSize && savedFontSizePx) {
                this.fontSize = savedFontSize;
                document.documentElement.style.setProperty('--font-size-base', savedFontSizePx);
            }
            
            this.videoPlayback = localStorage.getItem('tvx-video-playback') === 'paused' ? 'paused' : 'autoplay';
            this.videoMuted = this.videoPlayback === 'autoplay';
            const savedMediaGallery = localStorage.getItem('tvx-media-gallery');
            this.mediaGallery = ['grid', 'carousel', 'date'].includes(savedMediaGallery) ? savedMediaGallery : 'date';

            // Restore split panel preference
            const savedSplitPanel = localStorage.getItem('tvx-split-panel');
            this.splitPanel = savedSplitPanel === null ? true : savedSplitPanel === 'true';
            this.routeUsesPanel = this.shouldUseSplitPanel();

            const initialRoute = this.parseRoute(window.location.pathname);
            if (initialRoute) {
                const initialState = this.historyStateForRoute(initialRoute, {
                    depth: 0,
                    scrollY: initialRoute.viewMode === 'list' ? window.scrollY : undefined,
                });
                this.activeRoute = initialRoute;
                this.pendingRoute = initialRoute;
                this.pendingRouteState = initialState;
                history.replaceState(initialState, '', this.routeUrl(initialRoute));
            }

            // Pages never zoom on touch screens; the media viewer keeps its pinch zoom.
            // Safari ignores user-scalable=no, but honours a cancelled pinch gesture.
            if (window.matchMedia?.('(pointer: coarse)')?.matches) {
                const blockZoom = event => {
                    if (!event.target?.closest?.('.lightbox-viewer')) event.preventDefault();
                };
                document.addEventListener('gesturestart', blockZoom, { passive: false });
                document.addEventListener('gesturechange', blockZoom, { passive: false });
            }
            // A new finger-down is a fresh tap. Only the compatibility click
            // following the previous dismissal should remain blocked.
            document.addEventListener('touchstart', event => {
                videoTapBlockedUntil = 0;
                this.trackVideoScrollTouch(event);
            }, { capture: true, passive: true });
            this.listenForNavigation();

            this.fetchNotices();
            if (this.isDemo) {
                this.setupData = {
                    completed: true,
                    required: false,
                    auth: {configured: false, verified: false},
                    archive: {ready: true, enrichment_state: 'complete', pending_enrichment: 0},
                    web: {password_configured: true},
                };
                this.archiveReady = true;
                this.setupForced = false;
                this.archiveResourcesLoaded = true;
                Promise.all([
                    this.fetchTweets(),
                    this.fetchStats(),
                    this.fetchArchiveEnrichmentStatus(),
                    this.fetchAutomatedTagging(),
                ]).then(() => {
                    if (this.pendingRoute) {
                        const route = this.pendingRoute;
                        const state = this.pendingRouteState;
                        this.pendingRoute = null;
                        this.pendingRouteState = null;
                        this.applyRoute(route, {state});
                    }
                });
            } else {
                // Ask for the first page and any linked conversation alongside setup
                // status instead of after it; fetchTweets() reuses the request.
                const firstPage = this.requestFeedPage(`${this.feedUrl(1)}&cursor=`, { speculative: true });
                // The server answers a feed request only once Setup is complete and the
                // archive is open (409 or 503 otherwise), so a first page can be shown
                // without waiting for Setup status.
                firstPage?.promise.then(result => {
                    if (!result.ok || this.archiveResourcesLoaded) return;
                    this.archiveReady = true;
                    this.setupForced = false;
                    this.loadArchiveResources();
                }, () => {});
                if (firstPage) startupFeedSettled = firstPage.promise.then(() => {}, () => {});
                if (initialRoute?.viewMode === 'thread') this.prefetchThread(initialRoute.tweetId);
                this.fetchSetup();
            }
            // Start loading a conversation on press or a short hover, so it is often
            // ready by the time the click opens it.
            let hoverTimer = null;
            const threadTarget = event => event.target?.closest?.('[data-thread-id]')?.dataset.threadId;
            document.addEventListener('pointerdown', event => this.prefetchThread(threadTarget(event)), { passive: true });
            // Community notes render as markup inside many click-stopping wrappers, so their
            // history button is handled here, before any of them.
            document.addEventListener('click', event => {
                const button = event.target?.closest?.('[data-note-history]');
                if (!button) return;
                event.preventDefault();
                event.stopPropagation();
                this.openNoteHistory(button.dataset.noteHistory);
            }, true);
            document.addEventListener('mouseover', event => {
                clearTimeout(hoverTimer);
                const tweetId = threadTarget(event);
                if (tweetId) hoverTimer = setTimeout(() => this.prefetchThread(tweetId), THREAD_HOVER_PREFETCH_MS);
            }, { passive: true });
            // Background polling pauses while the tab is hidden and catches up on return.
            const pollStatus = () => {
                if (!this.isDemo) this.fetchSetup();
                this.fetchNotices();
            };
            document.addEventListener('visibilitychange', () => {
                if (!document.hidden) pollStatus();
                this.autoplayNextVideo();
            });
            setInterval(() => {
                if (!document.hidden) pollStatus();
            }, 3000);
            setInterval(() => {
                if (!document.hidden && this.archiveResourcesLoaded) this.fetchArchiveEnrichmentStatus();
            }, 60000);
            this.startActivityPolling();

            this.$watch('showStatsModal', val => {
                if (val) this.lockModalScroll();
                else if (!this.showSettingsModal && !this.showSetupModal && !this.lightboxOpen) this.unlockModalScroll();
                this.autoplayNextVideo();
            });
            this.$watch('showSettingsModal', val => {
                if (val) {
                    this.lockModalScroll();
                    if (!this.isDemo) this.fetchSetup();
                    if (!this.setupForced) {
                        this.fetchConfig();
                        if (this.archiveReady) this.fetchScheduleSettings();
                        this.fetchActivityRuns();
                        this.fetchAutomatedTagging();
                        if (this.settingsTab === 'tags') this.fetchGlobalTags();
                    }
                } else {
                    this.showAutomatedTaggingSpendModal = false;
                    if (!this.showSetupModal && !this.lightboxOpen) this.unlockModalScroll();
                    this.autoplayNextVideo();
                }
                if (val) this.autoplayNextVideo();
            });
            // The full tag list is only shown in Tag Management; load it there.
            this.$watch('settingsTab', tab => {
                if (tab === 'tags' && this.showSettingsModal) this.fetchGlobalTags();
            });
            this.$watch('setupFlowStep', step => {
                if (step === 3) this.loadSetupSchedule();
            });
            this.$watch('showSetupModal', val => {
                if (val) this.lockModalScroll();
                else if (!this.showSettingsModal && !this.lightboxOpen) this.unlockModalScroll();
                this.autoplayNextVideo();
            });
            this.$watch('lightboxOpen', val => {
                // The mobile viewer blocks pan gestures itself. Changing body
                // overflow makes Safari stop painting beneath its floating bar.
                if (val) { if (!this.isMobileViewer()) this.lockModalScroll(); }
                else {
                    this.followLightboxShade(false);
                    if (!this.showStatsModal && !this.showSettingsModal && !this.showSetupModal) this.unlockModalScroll();
                }
            });

            window.addEventListener('open-article', (event) => {
                const id = event.detail?.tweetId;
                if (id && /^\d+$/.test(id)) this.openThread(id);
            });
            window.addEventListener('open-lightbox', (e) => {
                this.openLightbox(e.detail.media, e.detail.index, e.detail.originVideo, e.detail.origin);
            });
            
            this.initVideoPlayback();

            const domObserver = new MutationObserver((mutations) => {
                mutations.forEach(m => {
                    // A post's still copy beneath a sliding post holds no live media.
                    if (m.target.closest?.('[data-detail-snapshot]')) return;
                    m.removedNodes.forEach(node => {
                        // Moving a live player into the viewer is not teardown.
                        if (node.nodeType !== 1 || node.isConnected) return;
                        tweetNookDisposeGalleries(node);
                        for (const target of [node, ...node.querySelectorAll('.feed-card, img, video')]) {
                            if (target.isConnected) continue;
                            this.mediaPrefetchObserver.unobserve(target);
                            if (target.tagName === 'VIDEO') this.forgetVideo(target);
                        }
                    });
                    m.addedNodes.forEach(node => {
                        if (node.nodeType === 1) {
                            tweetNookInitGalleries(node);
                            if (node.tagName === 'VIDEO') this.observeVideo(node);
                            node.querySelectorAll('video').forEach(v => this.observeVideo(v));
                            const player = node.closest?.('.media-video-player');
                            const video = player?.querySelector('video');
                            if (video && isVideoPlayer(video)) tweetNookInitVideoPlayer(video);
                            this.observeMediaPrefetch(node);
                        }
                    });
                });
            });
            domObserver.observe(document.body, { childList: true, subtree: true });
            
        },

        renderVideoControls,

        // Startup requests the first screen does not need wait for the first page, so
        // they do not compete with it on the server.
        afterStartupFeed(callback) {
            return startupFeedSettled.then(callback);
        },

        getInitial(tweet) {
            if (!tweet || !tweet.author) return '?';
            if (tweet.author.display_name) return avatarInitial(tweet.author.display_name);
            if (tweet.author.username) return avatarInitial(tweet.author.username);
            return '?';
        },

        closeSettings() {
            const wasOpen = this.showSettingsModal;
            this.showSettingsModal = false;
            if (wasOpen) this.$nextTick(() => nookSettingsRestoreFocus());
        },

        openSettings() {
            nookSettingsRememberFocus();
            this.nookSettingsDetail = window.matchMedia('(min-width: 800px)').matches;
            this.$nextTick(() => nookSettingsFocus(this));
            this.showSetupModal = false;
            this.showSettingsModal = true;
        },

        lockModalScroll() {
            if (this.settingsBodyOverflow === null) this.settingsBodyOverflow = document.body.style.overflow;
            document.body.style.overflow = 'hidden';
        },

        unlockModalScroll() {
            if (this.settingsBodyOverflow === null) return;
            document.body.style.overflow = this.settingsBodyOverflow;
            this.settingsBodyOverflow = null;
        },

    };
    // Feature files each return a group of the component's methods. Copying property
    // descriptors, unlike spreading, keeps getters live.
    for (const part of [
        feedVideoMethods(),
        feedCardMethods(),
        tweetTextMethods(),
        tweetMediaMethods(),
        tweetMetaMethods(),
        statsMethods(),
        automatedTaggingMethods(),
        tweetMenuMethods(),
        tagMethods(),
        setupMethods(),
        noticeMethods(),
        configMethods(),
        scheduleMethods(),
        activityViewMethods(),
        activityMethods(),
        profileCardMethods(),
        appearanceMethods(),
        lightboxMethods(),
        threadMethods(),
        navigationMethods(),
        feedMethods(),
    ]) {
        Object.defineProperties(app, Object.getOwnPropertyDescriptors(part));
    }
    return app;
}
