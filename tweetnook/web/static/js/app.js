/**
 * Main Alpine.js application for the TweetNook Web UI.
 */

const statsSuspendedVideos = new Set();
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
// First feed pages stay reusable this long; finished jobs clear them sooner.
const FEED_CACHE_MS = 5 * 60 * 1000;
const FEED_CACHE_LIMIT = 12;
// Load the next page while the end of the list is still this far below the viewport.
const FEED_PREFETCH_MARGIN_PX = 1200;
// Hovering a tweet this long starts loading its conversation.
const THREAD_HOVER_PREFETCH_MS = 150;
// warning-fill from icons.md: marks an archived tweet that is no longer on X.
// checkmark-circle from icons.md: the History dialog's "On X" status.
const AVAILABLE_ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="m16.28 9.62-5.41 6.84-3.54-3.22 1.34-1.48 1.96 1.78 4.09-5.16z"/><path d="M12 2a10 10 0 1 1 0 20 10 10 0 0 1 0-20m0 2a8 8 0 1 0 0 16 8 8 0 0 0 0-16"/></svg>';
const MISSING_ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M9.4 3.5a3 3 0 0 1 5.2 0L22.4 17a3 3 0 0 1-2.6 4.5H4.2A3 3 0 0 1 1.6 17zM12 15.2a1.2 1.2 0 1 0 0 2.4 1.2 1.2 0 0 0 0-2.4m0-6.95c-.8 0-1.4.7-1.3 1.49l.55 4.01h1.5l.55-4.01A1.3 1.3 0 0 0 12 8.25"/></svg>';
const SETUP_LOAD_ERROR = 'Could not load setup';
// Archive, Connect, Schedule, Appearance, Password.
const SETUP_STEP_COUNT = 5;
const DEFAULT_FEEDS = [['likes', 'liked_latest'], ['all', 'newest'], ['bookmarks', 'newest'], ['tweets', 'newest']];
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
    // Transport state is deliberately outside Alpine's reactive component.
    let activityRequest = null;
    let activityStatusLoaded = false;
    let activityPollingEnabled = false;
    let activityClockTimer = null;
    let activityVisibilityHandler = null;
    let activityEventSource = null;
    let activityStreamReady = false;
    let activityStreamRetryTimer = null;
    let activityStreamWatchdog = null;
    let lightboxMotionTimer = null;
    let lightboxSpringFrame = null;
    // Mobile video viewing hands playback back to the original inline player.
    let lightboxOriginVideo = null;
    // Photos and GIFs return to their tile in the post that opened them.
    let lightboxOriginHolder = null;
    let lightboxInlineMount = null;
    const lightboxInlineMounts = new Map();
    const lightboxOriginVideos = new Map();
    let lightboxViewportContent = null;
    let lightboxVideoMotion = null;
    let lightboxVideoReadyCancel = null;
    // The mobile video backdrop is page content rather than a fixed layer, so
    // Safari paints it beneath its toolbars too. It spans a viewport above and
    // below the visible page without extending the document.
    let lightboxShade = null;
    const placeLightboxShade = () => {
        const height = window.innerHeight || 800;
        const top = (window.scrollY || 0) - height;
        const bottom = Math.min(document.documentElement?.scrollHeight || Infinity, (window.scrollY || 0) + 2 * height);
        lightboxShade.style.top = `${top}px`;
        lightboxShade.style.height = `${Math.max(0, bottom - top)}px`;
    };
    // Lists and threads send abbreviated raw JSON. Copy raw reads the stored document,
    // prefetched when a tweet menu opens so the copy usually stays in the click gesture.
    const rawTweetRequests = new Map();
    const rawTweetValues = new Map();
    // First pages by URL ({at, speculative, result, promise}) and the URL on screen.
    const feedPages = new Map();
    let feedListUrl = null;
    let feedPrefetchScheduled = false;
    // Settles when the startup request for the first page does (see afterStartupFeed).
    let startupFeedSettled = Promise.resolve();
    // The list element whose delegated listeners are bound, and the card button
    // currently covered by the shared list menu.
    let boundFeedList = null;
    let listMenuButton = null;
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

    const app = {
        isDemo: window.TWEETNOOK_DEMO === true,
        viewMode: 'list',
        activeRoute: { viewMode: 'list' },
        pendingRoute: null,
        pendingRouteState: null,
        navigationDepth: 0,
        routeUsesPanel: false,
        detailLayer: false,
        detailLayerOpen: false,
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
        
        lightboxOpen: false,
        lightboxMedia: [],
        lightboxInlineIndex: null,
        lightboxIndex: 0,
        lightboxGestureStart: null,
        lightboxSuppressClickUntil: 0,
        lightboxGestureAxis: null,
        lightboxGestureOffsetX: 0,
        lightboxGestureOffsetY: 0,
        lightboxGestureTransition: 'none',
        lightboxGestureAnimating: false,
        lightboxDismissPending: false,
        lightboxVideoMotionPhase: '',
        lightboxChromeHidden: false,
        lightboxSettledIndex: 0,
        lightboxTweet: null,
        videoObserver: null,
        videoAutoplayObserver: null,
        mediaPrefetchObserver: null,

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

        tweetMenuOpen: null,
        // Feed cards share one overflow menu: the opening card's key and display
        // tweet, and where its button sits within the list area.
        listMenu: { key: 'list:', tweet: null, hidden: false, top: 0, right: 0 },
        tweetMenuPressTimer: null,
        tweetMenuClickBlockedUntil: 0,

        tagModalOpen: false,
        historyOpen: false,
        historyTweetId: null,
        historyData: null,
        historyLoading: false,
        historyError: '',
        notesOpen: false,
        notesTweetId: null,
        notesData: null,
        notesLoading: false,
        notesError: '',
        tagModalData: null,
        tagModalTweetId: null,
        
        isEditingTags: false,
        editableTags: [],
        editableDescription: '',
        tagSearchQuery: '',
        tagAutocompleteOptions: [],
        tagSearchDropdown: false,
        tagOptionIndex: -1,
        tagDeleteConfirm: false,
        tagSaving: false,
        tagError: '',
        tagConfirmError: '',
        tagFocusTarget: 'tags',

        showKeyboardShortcuts: false,
        profileCard: null,
        profileCardRequest: 0,
        activity: null,
        lastActivity: null,
        activitySchedule: { configured: false, relative: 'Not configured', date: 'Use cron or a service timer' },
        activityStartPending: false,
        activityStartingKind: null,
        activityError: null,
        feedUpdateAvailable: false,
        feedContentRevision: 0,
        activityDrawerOpen: false,
        activityShadeProgress: 0,
        activityPageKey: null,
        activityPageDirection: 'forward',
        activityNowSeconds: Math.floor(Date.now() / 1000),
        showLogModal: false,

        get displayActivity() {
            return this.activity || this.lastActivity;
        },

        applyActivityStatus(data) {
            const priorCommittedStages = new Set((this.displayActivity?.steps || [])
                .filter(step => step.state === 'complete')
                .map(step => `${this.displayActivity.run_id}:${step.key}`));
            const previousActiveStepKey = this.activity?.active_step
                || this.activity?.steps?.find(step => step.state === 'active')?.key
                || null;
            // A completed run returned on startup is history, not a new
            // change to the feed being loaded. Still catch active tasks
            // and tasks requested before the first status response.
            const checkForNewContent = activityStatusLoaded || data.active
                || Boolean(this.activity) || this.activityStartPending;
            activityStatusLoaded = true;
            this.activityNowSeconds = Math.floor(Date.now() / 1000);
            this.activitySchedule = reconcileActivityValue(this.activitySchedule, data.schedule || this.activitySchedule);
            this.activityError = null;
            if (data.active) {
                if (this.displayActivity?.run_id !== data.snapshot?.run_id) {
                    this.activityPageKey = null;
                    this.activityPageDirection = 'forward';
                }
                this.activity = this.activity?.run_id === data.snapshot?.run_id
                    ? reconcileActivityValue(this.activity, data.snapshot) : data.snapshot;
                this.activityStartPending = false;
                this.activityStartingKind = null;
            } else {
                // A finished job can change any list or conversation.
                if (this.activity) {
                    feedPages.clear();
                    this.threadCache.clear();
                    this.fetchArchiveEnrichmentStatus();
                }
                const previous = this.activity;
                const interrupted = previous ? {
                    ...previous, running: false, success: null, active_step: null,
                    summary: 'The worker ended before its final result was saved. Review Activity and check disk space before resuming.',
                    steps: (previous.steps || []).map(step => step.state === 'active'
                        ? {...step, state: 'failed', reason: 'The worker ended before this step finished.'}
                        : step.state === 'pending' ? {...step, state: 'skipped'} : step),
                } : null;
                const lastActivity = data.last_snapshot || interrupted || this.lastActivity;
                this.lastActivity = this.lastActivity?.run_id === lastActivity?.run_id
                    ? reconcileActivityValue(this.lastActivity, lastActivity) : lastActivity;
                this.activity = null;
                if (lastActivity?.stopped && !this.activityPageKey && previousActiveStepKey) {
                    this.activityPageKey = previousActiveStepKey;
                }
            }
            const committed = data.active ? data.snapshot : data.last_snapshot;
            if (checkForNewContent && (committed?.steps || []).some(step => step.state === 'complete'
                && (step.key?.startsWith('sync:') || ['archive-enrich', 'threads', 'resurrection', 'articles', 'media', 'urls'].includes(step.key))
                && !priorCommittedStages.has(`${committed.run_id}:${step.key}`))) {
                feedPages.clear();
                this.threadCache.clear();
                this.feedContentRevision++;
                this.feedUpdateAvailable = true;
                // Populate an empty open collection as soon as its stage commits.
                // Keep an occupied list stable while the user is reading it.
                if (this.archiveReady && !this.setupForced && this.viewMode === 'list' && !this.tweets.length) {
                    this.fetchTweets();
                }
            }
        },

        async fetchActivityStatus(force = false) {
            if (activityRequest && !force) return activityRequest.promise;
            activityRequest?.controller.abort();
            clearTimeout(this.activityPollTimer);
            this.activityPollTimer = null;
            const request = { controller: new AbortController(), promise: null };
            activityRequest = request;
            request.promise = (async () => {
                let timedOut = false;
                const timeout = setTimeout(() => {
                    timedOut = true;
                    request.controller.abort();
                }, 10000);
                try {
                    const response = await fetch('/api/activity/status', { signal: request.controller.signal });
                    if (!response.ok) throw new Error('Could not load activity status');
                    const data = await response.json();
                    // An action/focus refresh can supersede a slow request, even if
                    // its response body finishes after cancellation.
                    if (activityRequest !== request || request.controller.signal.aborted) return;
                    this.applyActivityStatus(data);
                } catch (error) {
                    if (activityRequest === request) {
                        this.activityError = timedOut ? 'Activity status request timed out' : error.message;
                    }
                } finally {
                    clearTimeout(timeout);
                    if (activityRequest === request) {
                        activityRequest = null;
                        this.scheduleActivityPoll();
                    }
                }
            })();
            return request.promise;
        },

        closeActivityStream() {
            clearTimeout(activityStreamRetryTimer);
            activityStreamRetryTimer = null;
            clearTimeout(activityStreamWatchdog);
            activityStreamWatchdog = null;
            const source = activityEventSource;
            activityEventSource = null;
            activityStreamReady = false;
            source?.close();
        },

        armActivityStreamWatchdog(source) {
            clearTimeout(activityStreamWatchdog);
            activityStreamWatchdog = setTimeout(() => {
                if (activityEventSource !== source) return;
                source.close();
                source.onerror();
            }, 45000);
        },

        startActivityStream() {
            if (!activityPollingEnabled || document.hidden || this.isDemo
                || typeof window.EventSource !== 'function' || activityEventSource) return;
            clearTimeout(activityStreamRetryTimer);
            activityStreamRetryTimer = null;
            let source;
            try {
                // Same-origin EventSource carries the existing Web session / Basic login.
                source = new window.EventSource('/api/activity/events');
            } catch {
                this.scheduleActivityPoll();
                return;
            }
            activityEventSource = source;
            this.armActivityStreamWatchdog(source);
            source.onopen = () => {
                if (activityEventSource === source) this.armActivityStreamWatchdog(source);
            };
            source.addEventListener('heartbeat', () => {
                if (activityEventSource === source) this.armActivityStreamWatchdog(source);
            });
            source.addEventListener('activity', event => {
                if (activityEventSource !== source || !activityPollingEnabled || document.hidden) return;
                try {
                    const data = JSON.parse(event.data);
                    if (!data || typeof data.active !== 'boolean') throw new Error('Invalid activity snapshot');
                    activityStreamReady = true;
                    this.armActivityStreamWatchdog(source);
                    // A newer stream snapshot supersedes any in-flight focus/action poll.
                    activityRequest?.controller.abort();
                    activityRequest = null;
                    clearTimeout(this.activityPollTimer);
                    this.activityPollTimer = null;
                    this.applyActivityStatus(data);
                } catch {
                    source.close();
                    source.onerror();
                }
            });
            source.onerror = () => {
                if (activityEventSource !== source) return;
                activityStreamReady = false;
                clearTimeout(activityStreamWatchdog);
                activityStreamWatchdog = null;
                // Native reconnect handles transient disconnects. A terminal failure
                // (including an older server without SSE) retries with a quiet backoff.
                if (source.readyState === 2) {
                    this.closeActivityStream();
                    activityStreamRetryTimer = setTimeout(() => this.startActivityStream(), 15000);
                }
                this.scheduleActivityPoll();
            };
        },

        scheduleActivityPoll() {
            clearTimeout(this.activityPollTimer);
            this.activityPollTimer = null;
            if (!activityPollingEnabled || document.hidden || activityStreamReady) return;
            const delay = this.activity || this.activityStartPending ? 1000 : 5000;
            this.activityPollTimer = setTimeout(() => this.fetchActivityStatus(), delay);
        },

        startActivityPolling() {
            if (activityPollingEnabled) return;
            activityPollingEnabled = true;
            this.startActivityStream();
            activityVisibilityHandler = () => {
                if (document.hidden) {
                    this.closeActivityStream();
                    clearTimeout(this.activityPollTimer);
                    this.activityPollTimer = null;
                    activityRequest?.controller.abort();
                    activityRequest = null;
                } else {
                    this.activityNowSeconds = Math.floor(Date.now() / 1000);
                    this.startActivityStream();
                    this.fetchActivityStatus(true);
                }
            };
            document.addEventListener('visibilitychange', activityVisibilityHandler);
            window.addEventListener('focus', activityVisibilityHandler);
            activityClockTimer = setInterval(() => {
                if (this.activity && !document.hidden) {
                    this.activityNowSeconds = Math.floor(Date.now() / 1000);
                }
            }, 1000);
            if (!document.hidden) this.fetchActivityStatus();
        },

        stopActivityPolling() {
            activityPollingEnabled = false;
            this.closeActivityStream();
            clearTimeout(this.activityPollTimer);
            this.activityPollTimer = null;
            clearInterval(activityClockTimer);
            if (activityVisibilityHandler) {
                document.removeEventListener('visibilitychange', activityVisibilityHandler);
                window.removeEventListener('focus', activityVisibilityHandler);
                activityVisibilityHandler = null;
            }
            activityRequest?.controller.abort();
            activityRequest = null;
        },

        destroy() {
            this.stopActivityPolling();
            clearTimeout(lightboxMotionTimer);
        },

        async fetchScheduleSettings() {
            this.scheduleError = '';
            try {
                const response = await fetch('/api/activity/schedule');
                if (!response.ok) throw new Error('Could not load schedule');
                const data = await response.json();
                this.scheduleSettings = data;
                this.scheduleForm = this.normalizeScheduleConfig(data.config);
                this.syncScheduleTimePicker();
                this.scheduleOriginal = JSON.parse(JSON.stringify(this.scheduleForm));
                this.scheduleSaved = false;
            } catch (error) {
                this.scheduleError = error.message;
                this.scheduleSettings = null;
            }
        },

        normalizeScheduleConfig(config = {}) {
            return {
                enabled: !!config.enabled,
                cadence: ['hours', 'daily', 'weekly', 'monthly'].includes(config.cadence)
                    ? config.cadence
                    : 'daily',
                every_hours: Number(config.every_hours ?? 6),
                time: config.time || '03:00',
                randomize_time: !!(config.randomize_time ?? true),
                random_offset_min_hours: Number(config.random_offset_min_hours ?? 0),
                random_offset_max_hours: Number(config.random_offset_max_hours ?? 2),
                weekday: Number(config.weekday ?? 0),
                day_of_month: Number(config.day_of_month ?? 1),
                timezone: config.timezone || 'local',
            };
        },

        scheduleTimezoneOptions() {
            const detected = Intl.DateTimeFormat().resolvedOptions().timeZone;
            const current = this.scheduleForm?.timezone;
            const values = [
                { value: 'local', label: 'System local time' },
                { value: 'UTC', label: 'UTC' },
            ];
            if (detected && !values.some(option => option.value === detected)) {
                values.push({ value: detected, label: `${detected} (browser)` });
            }
            const supported = typeof Intl.supportedValuesOf === 'function'
                ? Intl.supportedValuesOf('timeZone')
                : [];
            for (const timezone of supported) {
                if (!values.some(option => option.value === timezone)) {
                    values.push({ value: timezone, label: timezone });
                }
            }
            if (current && !values.some(option => option.value === current)) {
                values.push({ value: current, label: current });
            }
            return values;
        },

        scheduleFieldChanged() {
            this.scheduleSaved = false;
            this.scheduleError = '';
        },

        syncScheduleTimePicker() {
            const [hourValue, minuteValue] = (this.scheduleForm?.time || '03:00').split(':');
            const hour24 = Number(hourValue);
            this.scheduleTimeHour = String(hour24 % 12 || 12);
            this.scheduleTimeMinute = String(Number(minuteValue || 0)).padStart(2, '0');
            this.scheduleTimePeriod = hour24 >= 12 ? 'PM' : 'AM';
        },

        setScheduleTimePart(part, value) {
            if (!this.scheduleForm) return;
            if (part === 'hour') this.scheduleTimeHour = String(value);
            if (part === 'minute') this.scheduleTimeMinute = String(value).padStart(2, '0');
            if (part === 'period') this.scheduleTimePeriod = String(value);
            let hour = Number(this.scheduleTimeHour) % 12;
            if (this.scheduleTimePeriod === 'PM') hour += 12;
            this.scheduleForm.time = `${String(hour).padStart(2, '0')}:${this.scheduleTimeMinute}`;
            this.scheduleFieldChanged();
        },

        scheduleRandomWindowValid() {
            if (
                !this.scheduleForm?.randomize_time
                || this.scheduleForm.cadence === 'hours'
            ) return true;
            const minimum = Number(this.scheduleForm.random_offset_min_hours);
            const maximum = Number(this.scheduleForm.random_offset_max_hours);
            return Number.isFinite(minimum)
                && Number.isFinite(maximum)
                && minimum >= 0
                && maximum >= 0
                && minimum <= 24
                && maximum <= 24
                && minimum <= maximum;
        },

        scheduleHasChanges() {
            return !!this.scheduleForm
                && JSON.stringify(this.scheduleForm) !== JSON.stringify(this.scheduleOriginal);
        },

        async saveScheduleSettings() {
            if (!this.scheduleForm || this.scheduleSaving || !this.scheduleRandomWindowValid()) return;
            this.scheduleSaving = true;
            this.scheduleSaved = false;
            this.scheduleError = '';
            const values = this.normalizeScheduleConfig(this.scheduleForm);
            try {
                const response = await fetch('/api/activity/schedule', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(values),
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(
                        typeof data.detail === 'string'
                            ? data.detail
                            : 'Could not save schedule',
                    );
                }
                this.scheduleSettings = data;
                this.activitySchedule = data;
                this.scheduleForm = this.normalizeScheduleConfig(data.config);
                this.syncScheduleTimePicker();
                this.scheduleOriginal = JSON.parse(JSON.stringify(this.scheduleForm));
                this.scheduleSaved = true;
            } catch (error) {
                this.scheduleError = error.message;
            } finally {
                this.scheduleSaving = false;
            }
        },

        async fetchActivityRuns() {
            this.activityRunsLoading = true;
            try {
                const response = await fetch('/api/activity/runs?limit=100');
                if (!response.ok) throw new Error('Could not load activity logs');
                this.activityRuns = (await response.json()).runs || [];
            } catch (error) {
                this.activityError = error.message;
            } finally {
                this.activityRunsLoading = false;
            }
        },

        async openActivityRun(run) {
            this.selectedActivityRun = run;
            this.selectedActivityLog = 'Loading log…';
            this.showLogModal = true;
            try {
                const [detailsResponse, logResponse] = await Promise.all([
                    fetch(`/api/activity/runs/${encodeURIComponent(run.run_id)}`),
                    fetch(`/api/activity/runs/${encodeURIComponent(run.run_id)}/log`),
                ]);
                if (!detailsResponse.ok || !logResponse.ok) throw new Error('Could not load run log');
                this.selectedActivityRun = await detailsResponse.json();
                this.selectedActivityLog = await logResponse.text();
            } catch (error) {
                this.selectedActivityLog = error.message;
            }
        },

        formatActivityTimestamp(value) {
            if (!value) return '—';
            return new Date(Number(value) * 1000).toLocaleString();
        },

        activityScheduleDayText() {
            const schedule = this.activitySchedule || {};
            if (!schedule.enabled) return schedule.relative || 'Not scheduled';

            const nextRunAt = Number(schedule.next_run_at);
            if (Number.isFinite(nextRunAt) && nextRunAt > 0) {
                const now = new Date();
                const next = new Date(nextRunAt * 1000);
                const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
                const nextDay = new Date(next.getFullYear(), next.getMonth(), next.getDate());
                const dayOffset = Math.round((nextDay - today) / 86400000);
                if (dayOffset === 0) return 'Today';
                if (dayOffset === 1) return 'Tomorrow';
                return next.toLocaleDateString('en-US', {
                    weekday: 'short',
                    month: 'short',
                    day: 'numeric',
                });
            }

            const dateLabel = String(schedule.date || '').split('·')[0].trim();
            return dateLabel || schedule.relative || 'Scheduled';
        },

        activityScheduleTimeText() {
            const schedule = this.activitySchedule || {};
            if (!schedule.enabled) return '';

            const configuredTime = String(schedule.config?.time || '');
            const configuredMatch = configuredTime.match(/^(\d{2}):(\d{2})$/);
            if (configuredMatch && schedule.config?.cadence !== 'hours') {
                const hour = Number(configuredMatch[1]);
                const minute = configuredMatch[2];
                const period = hour >= 12 ? 'PM' : 'AM';
                const displayHour = hour % 12 || 12;
                const prefix = schedule.config?.randomize_time ? 'around' : 'at';
                return `${prefix} ${displayHour}:${minute} ${period}`;
            }

            const displayedTime = String(schedule.date || schedule.relative || '')
                .match(/\b\d{1,2}:\d{2}\s*[AP]M\b/i)?.[0];
            return displayedTime ? `at ${displayedTime.toUpperCase()}` : '';
        },

        formatLogText(text) {
            if (!text) return 'No log output.';
            return text.replace(/[<>]/g, c => c === '<' ? '&lt;' : '&gt;').split('\n').map(line => {
                if (line.includes('ERROR:')) return `<span style="color: var(--destructive-color)">${line}</span>`;
                if (line.includes('WARNING:')) return `<span style="color: var(--accent-color)">${line}</span>`;
                const failureText = line.replace(/\b0+\s+failed\b/gi, '');
                if (/\bfailed\b/i.test(failureText)) {
                    return `<span style="color: var(--destructive-color)">${line}</span>`;
                }
                if (line.includes('completed')) return `<span style="color: var(--accent-color)">${line}</span>`;
                const parts = line.split(/Z (.*)/);
                if (parts.length === 3) {
                    return `<span style="color: var(--text-secondary)">${parts[0]}Z</span> ${parts[1]}`;
                }
                return line;
            }).join('\n');
        },

        async startActivity(kind) {
            if (this.archiveReady === false || this.setupForced) return;
            if (this.activity || this.activityStartPending) return;
            this.activityStartPending = true;
            this.activityStartingKind = kind;
            this.activityPageKey = null;
            this.activityPageDirection = 'forward';
            this.activityError = null;
            try {
                const response = await fetch(`/api/activity/${kind}`, { method: 'POST' });
                if (!response.ok) {
                    const data = await response.json().catch(() => ({}));
                    throw new Error(data.detail || 'Could not start sync');
                }
                await new Promise(resolve => setTimeout(resolve, 250));
                await this.fetchActivityStatus(true);
                if (kind === 'enrich') await this.fetchArchiveEnrichmentStatus();
            } catch (error) {
                this.activityError = error.message;
                this.activityStartPending = false;
                this.activityStartingKind = null;
            }
        },

        async stopActivity() {
            if (!this.activity && !this.activityStartPending) return;
            this.activityError = null;
            try {
                const response = await fetch('/api/activity/stop', { method: 'POST' });
                if (!response.ok) {
                    const data = await response.json().catch(() => ({}));
                    throw new Error(data.detail || 'Could not stop task');
                }
                await new Promise(resolve => setTimeout(resolve, 100));
                await this.fetchActivityStatus(true);
            } catch (error) {
                this.activityError = error.message;
            }
        },

        formatActivityDuration(seconds) {
            const whole = Math.max(Math.round(Number(seconds) || 0), 0);
            const hours = Math.floor(whole / 3600);
            const minutes = Math.floor((whole % 3600) / 60);
            const remainder = whole % 60;
            return hours
                ? `${hours}:${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`
                : `${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`;
        },

        formatActivityBytes(value) {
            const bytes = Math.max(Number(value) || 0, 0);
            if (bytes < 1024) return `${bytes.toLocaleString()} B`;
            const units = ['KiB', 'MiB', 'GiB', 'TiB'];
            let amount = bytes / 1024;
            let index = 0;
            while (amount >= 1024 && index < units.length - 1) {
                amount /= 1024;
                index += 1;
            }
            return `${amount.toFixed(amount >= 10 ? 1 : 2)} ${units[index]}`;
        },

        activityPercent(step) {
            if (!step || !step.total) return 0;
            return Math.max(0, Math.min(100, step.completed / step.total * 100));
        },

        activitySegmentPercent(page) {
            if (!page) return 0;
            if (page.is_summary) return this.activity ? 0 : 100;
            if (page.state === 'complete' || page.state === 'skipped') return 100;
            return this.activityPercent(page);
        },

        activitySegmentState(page) {
            if (this.displayActivity?.stopped && page?.state === 'failed') return 'cancelled';
            return page?.state || 'pending';
        },

        activityPageStateLabel(page) {
            const labels = {
                active: 'In progress',
                complete: 'Complete',
                failed: 'Failed',
                cancelled: 'Canceled',
                skipped: 'Skipped',
                pending: 'Waiting',
            };
            return labels[this.activitySegmentState(page)] || 'Waiting';
        },

        activityPageUpdateText(page) {
            const counters = String(page?.counters || '').trim();
            if (counters) return `Latest update · ${counters}`;
            const labels = {
                active: 'Waiting for first result',
                complete: 'Stage complete',
                failed: 'Stopped with an error',
                cancelled: 'Canceled',
                skipped: 'No work needed',
                pending: 'Waiting to start',
            };
            return `Latest update · ${labels[this.activitySegmentState(page)] || 'Waiting to start'}`;
        },

        activityPageTimingText(page) {
            const state = this.activitySegmentState(page);
            const eta = Number(page?.eta_seconds);
            if (state === 'active' && page?.eta_seconds != null && Number.isFinite(eta)) {
                return `Timing · About ${this.formatActivityDuration(eta)} remaining`;
            }
            const labels = {
                active: 'Estimating remaining time',
                complete: 'Completed',
                failed: 'Stopped',
                cancelled: 'Canceled',
                skipped: 'No runtime needed',
                pending: 'Available when this stage starts',
            };
            return `Timing · ${labels[state] || 'Available when this stage starts'}`;
        },

        activitySteps() {
            return this.displayActivity?.steps || [];
        },

        activityActiveStep() {
            return this.activitySteps().find(step => step.state === 'active') || null;
        },

        // The availability check shares the sync drawer; its wording follows the run.
        activityIsCheck(run = this.displayActivity) {
            return !this.activityStartPending && /^tweetnook\s+check$/i.test(String(run?.title || '').trim());
        },

        activityCommandLabel() {
            const pending = this.activityStartingKind || 'sync';
            const rawTitle = String(
                this.activityStartPending && this.activityStartingKind
                    ? `tweetnook ${pending}`
                    : this.displayActivity?.title || `tweetnook ${pending}`
            );
            const command = rawTitle.replace(/^tweetnook\s+/i, '').trim().toLowerCase();
            const labels = {
                sync: 'Archive Sync',
                enrich: 'Archive Preparation',
                import: 'Archive Import',
                'import enrich': 'Archive Preparation',
                'import x-archive': 'Archive Import',
                'threads expand': 'Thread Expansion',
                check: 'Availability Check',
                'articles refresh': 'Article Refresh',
                'media download': 'Media Download',
                unfurl: 'Link Preview Refresh',
                tag: 'Automated Tagging',
                migrate: 'Archive Migration',
            };
            return labels[command] || rawTitle.replace(/^tweetnook\s+/i, '') || 'Activity';
        },

        activityIssues() {
            return Array.isArray(this.displayActivity?.issues) ? this.displayActivity.issues : [];
        },

        activityIssueCount() {
            return this.activityIssues().reduce(
                (total, issue) => total + Math.max(Number(issue.count) || 1, 1),
                0,
            );
        },

        activityRunState() {
            if (this.activity) return 'active';
            if (this.displayActivity?.success === true) return 'complete';
            if (this.displayActivity?.stopped || this.displayActivity?.success === false) {
                return 'failed';
            }
            return this.displayActivity ? 'failed' : 'pending';
        },

        activityRunStateLabel() {
            if (this.activity) return 'In progress';
            if (this.displayActivity?.stopped) return 'Canceled';
            if (this.displayActivity?.success === false) return 'Failed';
            if (this.displayActivity?.success === true) return 'Complete';
            return this.displayActivity ? 'Interrupted' : 'Waiting';
        },

        activityRunStatusTitle() {
            if (this.activity) return 'Sync in progress';
            if (this.displayActivity?.stopped) return 'Last run stopped';
            if (this.displayActivity?.success === false) return 'Last run failed';
            if (this.displayActivity?.success === true) return 'Last run completed';
            return this.displayActivity ? 'Last run interrupted' : 'Run summary';
        },

        activityHeaderText() {
            if (this.activity || this.activityStartPending) return this.activityCommandLabel() + ' in progress…';
            const last = this.lastActivity;
            const command = String(last?.title || '').replace(/^tweetnook\s+/i, '').trim();
            const noun = this.activityIsCheck(last) ? 'Check' : !command || command === 'sync' ? 'Sync' : 'Job';
            if (last?.stopped) return `${noun} stopped — open Activity`;
            if (last?.success === false) return `${noun} failed — open Activity`;
            if (last && last.success !== true) return `${noun} interrupted — open Activity`;
            return this.lastSyncFormatted ? 'Synced ' + this.lastSyncFormatted : 'Open the Jobs panel to start a sync.';
        },

        activityRunCounts() {
            const steps = this.activitySteps();
            const complete = steps.filter(step => step.state === 'complete').length;
            const skipped = steps.filter(step => step.state === 'skipped').length;
            const failed = steps.filter(step => step.state === 'failed').length;
            return [
                `${steps.length} stage${steps.length === 1 ? '' : 's'}`,
                complete ? `${complete} completed` : '',
                skipped ? `${skipped} skipped` : '',
                failed ? `${failed} ${this.displayActivity?.stopped ? 'canceled' : 'failed'}` : '',
            ].filter(Boolean).join(' · ');
        },

        activitySummaryMetrics() {
            const steps = this.activitySteps();
            const cards = [];
            const stepFor = key => steps.find(step => step.key === key || step.key.startsWith(`${key}:`));
            const metric = (step, key) => {
                if (!step?.metrics || !Object.prototype.hasOwnProperty.call(step.metrics, key)) {
                    return null;
                }
                const value = Number(step.metrics[key]);
                return Number.isFinite(value) ? Math.max(value, 0) : null;
            };
            const count = value => Math.round(value).toLocaleString();
            const add = (key, label, value, subtitle = '') => {
                const wide = String(label).length > 20
                    || String(value).length > 12
                    || String(subtitle).length > 25;
                cards.push({ key, label, value, subtitle, wide });
            };
            const unavailableSubtitle = step => {
                const state = this.activitySegmentState(step);
                if (state === 'skipped') return 'Stage did not run';
                if (state === 'failed' || state === 'cancelled') return 'Not reported before stop';
                return this.activity ? 'Available after this stage' : 'Not reported';
            };
            const sumMetrics = (matchingSteps, key) => {
                const values = matchingSteps
                    .map(step => metric(step, key))
                    .filter(value => value !== null);
                return values.length ? values.reduce((total, value) => total + value, 0) : null;
            };
            const syncCard = (key, label) => {
                const step = stepFor(key);
                if (!step) return;
                const newTweets = metric(step, 'new_tweets');
                const tweetsSeen = metric(step, 'tweets_seen');
                const pages = metric(step, 'pages');
                const subtitle = newTweets === null
                    ? unavailableSubtitle(step)
                    : [
                        tweetsSeen === null ? '' : `${count(tweetsSeen)} checked`,
                        pages === null ? '' : `${count(pages)} page${pages === 1 ? '' : 's'}`,
                    ].filter(Boolean).join(' · ');
                add(key, label, newTweets === null ? '—' : count(newTweets), subtitle);
            };

            syncCard('sync:bookmarks', 'New bookmarks');
            syncCard('sync:likes', 'New liked tweets');
            syncCard('sync:tweets', 'New tweets');

            const syncSteps = steps.filter(step => step.key?.startsWith('sync:'));
            if (syncSteps.length) {
                const tweetsSeen = sumMetrics(syncSteps, 'tweets_seen');
                const pages = sumMetrics(syncSteps, 'pages');
                add(
                    'tweets-checked',
                    'Tweets checked',
                    tweetsSeen === null ? '—' : count(tweetsSeen),
                    tweetsSeen === null ? 'Available after stages' : 'All collections',
                );
                add(
                    'pages-fetched',
                    'Pages fetched',
                    pages === null ? '—' : count(pages),
                    pages === null ? 'Available after stages' : 'All collections',
                );
            }

            const media = stepFor('media');
            if (media) {
                const downloaded = metric(media, 'downloaded');
                const downloadedBytes = metric(media, 'downloaded_bytes');
                const alreadySaved = metric(media, 'already_saved');
                const failed = metric(media, 'failed');
                add(
                    'media-downloaded',
                    'Media downloaded',
                    downloaded === null ? '—' : count(downloaded),
                    downloaded === null
                        ? unavailableSubtitle(media)
                        : failed ? `${count(failed)} failed` : 'Saved locally',
                );
                add(
                    'media-size',
                    'Media size',
                    downloadedBytes === null ? '—' : this.formatActivityBytes(downloadedBytes),
                    downloadedBytes === null ? unavailableSubtitle(media) : 'Added this run',
                );
                add(
                    'media-saved',
                    'Media already saved',
                    alreadySaved === null ? '—' : count(alreadySaved),
                    alreadySaved === null ? unavailableSubtitle(media) : 'No download needed',
                );
                add(
                    'media-failed',
                    'Media failures',
                    failed === null ? '—' : count(failed),
                    failed === null ? unavailableSubtitle(media) : failed ? 'Review sync issues' : 'No media failures',
                );
            }

            const threads = stepFor('threads');
            if (threads) {
                const expanded = metric(threads, 'expanded');
                const known = metric(threads, 'already_known');
                add(
                    'threads',
                    'Threads expanded',
                    expanded === null ? '—' : count(expanded),
                    expanded === null ? unavailableSubtitle(threads) : 'New context saved',
                );
                add(
                    'threads-known',
                    'Threads already known',
                    known === null ? '—' : count(known),
                    known === null ? unavailableSubtitle(threads) : 'Skipped safely',
                );
                const failed = metric(threads, 'failed');
                add(
                    'threads-failed',
                    'Thread failures',
                    failed === null ? '—' : count(failed),
                    failed === null ? unavailableSubtitle(threads) : failed ? 'Review sync issues' : 'No thread failures',
                );
            }

            const resurrection = stepFor('resurrection');
            if (resurrection) {
                const restored = metric(resurrection, 'restored');
                const unavailable = metric(resurrection, 'still_unavailable');
                const retryLater = metric(resurrection, 'retry_later');
                const remainingDue = metric(resurrection, 'remaining_due');
                add(
                    'restored',
                    'Tweets resurrected',
                    restored === null ? '—' : count(restored),
                    restored === null ? unavailableSubtitle(resurrection) : 'Returned to the archive',
                );
                add(
                    'still-unavailable',
                    'Still unavailable',
                    unavailable === null ? '—' : count(unavailable),
                    unavailable === null
                        ? unavailableSubtitle(resurrection)
                        : [
                            retryLater ? `${count(retryLater)} retry later` : '',
                            remainingDue ? `${count(remainingDue)} still due` : '',
                        ].filter(Boolean).join(' · ') || 'Checked this run',
                );
                add(
                    'recovery-retries',
                    'Recovery retries',
                    retryLater === null ? '—' : count(retryLater),
                    retryLater === null ? unavailableSubtitle(resurrection) : 'Transient failures',
                );
                add(
                    'recovery-queue',
                    'Recovery queue',
                    remainingDue === null ? '—' : count(remainingDue),
                    remainingDue === null ? unavailableSubtitle(resurrection) : 'Still due',
                );
            }

            const urls = stepFor('urls');
            if (urls) {
                const updatedUrls = metric(urls, 'updated');
                const failedUrls = metric(urls, 'failed');
                add(
                    'urls',
                    'Link previews updated',
                    updatedUrls === null ? '—' : count(updatedUrls),
                    updatedUrls === null ? unavailableSubtitle(urls) : 'Metadata updated',
                );
                add(
                    'urls-failed',
                    'Preview failures',
                    failedUrls === null ? '—' : count(failedUrls),
                    failedUrls === null ? unavailableSubtitle(urls) : failedUrls ? 'Review sync issues' : 'No preview failures',
                );
            }

            const articles = stepFor('articles');
            if (articles) {
                const refreshedArticles = metric(articles, 'refreshed')
                    ?? (articles.state === 'skipped' ? 0 : null);
                const failedArticles = metric(articles, 'failed')
                    ?? (articles.state === 'skipped' ? 0 : null);
                add(
                    'articles',
                    'Articles refreshed',
                    refreshedArticles === null ? '—' : count(refreshedArticles),
                    refreshedArticles === null
                        ? unavailableSubtitle(articles)
                        : articles.state === 'skipped' ? 'No refresh needed' : 'Content updated',
                );
                add(
                    'articles-failed',
                    'Article failures',
                    failedArticles === null ? '—' : count(failedArticles),
                    failedArticles === null
                        ? unavailableSubtitle(articles)
                        : failedArticles ? 'Review sync issues' : 'No article failures',
                );
            }

            const tagging = stepFor('tagging');
            if (tagging) {
                const tagged = metric(tagging, 'tagged');
                const requests = metric(tagging, 'requests');
                add(
                    'tagging',
                    'Tweets tagged',
                    tagged === null ? '—' : count(tagged),
                    tagged === null
                        ? unavailableSubtitle(tagging)
                        : requests === null ? '' : `${count(requests)} request${requests === 1 ? '' : 's'}`,
                );
                add(
                    'tagging-requests',
                    'Tagging requests',
                    requests === null ? '—' : count(requests),
                    requests === null ? unavailableSubtitle(tagging) : 'Model requests',
                );
            }

            add(
                'duration',
                'Duration',
                this.formatActivityDuration(this.displayActivity?.elapsed_seconds),
                'Total runtime',
            );
            const issueCount = this.activityIssueCount();
            add('issues', 'Issues', count(issueCount), issueCount ? 'Review below' : 'No issues');
            return cards;
        },

        activityPages() {
            if (!this.displayActivity) return [];
            return [
                ...this.activitySteps(),
                {
                    key: '__summary__',
                    title: 'Summary',
                    state: this.activity ? 'pending' : this.activityRunState(),
                    is_summary: true,
                },
            ];
        },

        activitySelectedSegmentGrow() {
            const pageCount = this.activityPages().length;
            if (pageCount <= 2) return 2;
            return (2 * (pageCount - 1)) / (pageCount - 2);
        },

        activitySelectedPageKey() {
            const pages = this.activityPages();
            if (this.activityPageKey && pages.some(page => page.key === this.activityPageKey)) {
                return this.activityPageKey;
            }
            return this.activityActiveStep()?.key || '__summary__';
        },

        activitySelectedPage() {
            const key = this.activitySelectedPageKey();
            return this.activityPages().find(page => page.key === key) || null;
        },

        activitySelectedPageIndex() {
            return Math.max(
                this.activityPages().findIndex(page => page.key === this.activitySelectedPageKey()),
                0,
            );
        },

        selectActivityPage(key) {
            const pages = this.activityPages();
            const nextIndex = pages.findIndex(page => page.key === key);
            const currentIndex = this.activitySelectedPageIndex();
            if (nextIndex < 0 || nextIndex === currentIndex) return;
            this.activityPageDirection = nextIndex < currentIndex ? 'back' : 'forward';
            this.activityPageKey = key;
        },

        moveActivityPage(offset) {
            const pages = this.activityPages();
            const nextIndex = Math.max(
                0,
                Math.min(this.activitySelectedPageIndex() + offset, pages.length - 1),
            );
            if (pages[nextIndex]) this.selectActivityPage(pages[nextIndex].key);
        },

        openActivityDrawer() {
            this.activityPageKey = null;
            this.activityPageDirection = 'forward';
            this.activityDrawerOpen = true;
            if (activityPollingEnabled) this.fetchActivityStatus();
        },

        toggleActivityDrawer() {
            if (this.activityDrawerOpen) {
                this.activityDrawerOpen = false;
                return;
            }
            this.openActivityDrawer();
        },

        openUsernameProfileCard(event, username) {
            if (!username) return;
            this.openProfileCard(
                event,
                {
                    author: {
                        display_name: username,
                        username,
                        id: 'unknown',
                    },
                },
                false,
                true,
            );
        },

        openProfileCard(event, tweet, isQt = false, useClickedAnchor = false) {
            let anchor = event.currentTarget;
            if (!useClickedAnchor && !anchor.classList.contains('w-10')) {
                // Attempt to find the pfp avatar container which is usually a previous sibling or in the parent flex row
                let container = anchor.closest('.flex.space-x-2') || anchor.closest('.flex.relative');
                if (!container && anchor.parentElement && anchor.parentElement.parentElement) {
                    container = anchor.parentElement.parentElement;
                }
                if (container) {
                    const pfp = container.querySelector('.w-10.h-10.rounded-full');
                    if (pfp) anchor = pfp;
                }
            }

            const rect = anchor.getBoundingClientRect();

            let name, username, id;
            let raw;
            if (isQt) {
                raw = tweet.raw_json || tweet;
                const authorInfo = this.getQuoteAuthor(tweet);
                name = authorInfo.name;
                username = authorInfo.screen_name;
                id = authorInfo.id;
            } else {
                raw = tweet.raw_json || {};
                name = tweet.author?.display_name || 'Unknown';
                username = tweet.author?.username || 'unknown';
                id = tweet.author?.id || 'unknown';
            }
            
            const core = raw.core || {};
            const userResult = core.user_results?.result || {};
            const legacy = userResult.legacy || raw.user || {};
            
            this.profileCard = {
                x: rect.left,
                y: rect.bottom + 8,
                anchorRect: { left: rect.left, top: rect.top, bottom: rect.bottom },
                request: ++this.profileCardRequest,
                name,
                username,
                id,
                initial: avatarInitial(name).toUpperCase(),
                description: legacy.description || '',
                followersCount: legacy.followers_count || 0,
                followingCount: legacy.friends_count || 0,
                syncDate: tweet.synced_at || (tweet.collection ? tweet.collection.synced_at : null) || raw.synced_at || null,
                snapshots: [],
                snapshotIndex: 0,
            };
            this.positionProfileCard();
            this.loadProfileSnapshots();

            if (!this._profileMouseMoveHandler) {
                this._profileMouseMoveHandler = (e) => {
                    if (!this.profileCard) return;
                    const card = document.getElementById('profile-card-modal');
                    if (!card) return;
                    const cardRect = card.getBoundingClientRect();
                    const bufferX = 30;
                    const bufferY = 80;
                    if (
                        e.clientX < cardRect.left - bufferX ||
                        e.clientX > cardRect.right + bufferX ||
                        e.clientY < cardRect.top - bufferY ||
                        e.clientY > cardRect.bottom + bufferY
                    ) {
                        this.closeProfileCard();
                    }
                };
            }
            window.addEventListener('mousemove', this._profileMouseMoveHandler);
        },
        
        // The clicked tweet carries the profile as it was when that tweet was
        // fetched. Replace it with the most recently fetched capture of the
        // author, keeping older distinct captures to cycle through.
        async loadProfileSnapshots() {
            const card = this.profileCard;
            if (!card) return;
            const request = card.request;
            let query;
            if (card.id && card.id !== 'unknown') {
                query = `author_id=${encodeURIComponent(card.id)}`;
            } else if (card.username && card.username !== 'unknown') {
                query = `username=${encodeURIComponent(card.username)}`;
            } else {
                return;
            }
            try {
                const res = await fetch(`/api/authors/profile?${query}`);
                if (!res.ok) return;
                const data = await res.json();
                // The card may have closed or opened for someone else meanwhile.
                if (this.profileCard?.request !== request) return;
                if (data.author_id) this.profileCard.id = data.author_id;
                if (!data.snapshots?.length) return;
                this.profileCard.snapshots = data.snapshots;
                this.showProfileSnapshot(0, false);
            } catch (e) {
                console.error('Failed to load profile snapshots:', e);
            }
        },

        // Index 0 is the most recent capture; higher indexes are older. Cycling
        // keeps the card's top fixed so the arrows stay under the pointer.
        showProfileSnapshot(index, keepTop = true) {
            const card = this.profileCard;
            const snapshot = card?.snapshots?.[index];
            if (!snapshot) return;
            card.snapshotIndex = index;
            card.name = snapshot.display_name || card.name;
            card.username = snapshot.username || card.username;
            card.initial = avatarInitial(card.name).toUpperCase();
            card.description = snapshot.description || '';
            card.followersCount = snapshot.followers_count || 0;
            card.followingCount = snapshot.following_count || 0;
            card.syncDate = snapshot.fetched_at || card.syncDate;
            this.positionProfileCard(keepTop);
        },

        // Places the card below its anchor, or above it when it would overflow.
        // With keepTop, the card stays put unless it would leave the viewport.
        positionProfileCard(keepTop = false) {
            this.$nextTick(() => {
                const card = document.getElementById('profile-card-modal');
                if (!card || !this.profileCard) return;
                const anchor = this.profileCard.anchorRect;
                const cardRect = card.getBoundingClientRect();
                let x = anchor.left;
                let y = keepTop ? this.profileCard.y : anchor.bottom + 8;
                if (x + cardRect.width > window.innerWidth) {
                    x = window.innerWidth - cardRect.width - 16;
                }
                if (y + cardRect.height > window.innerHeight) {
                    y = keepTop
                        ? Math.max(8, window.innerHeight - cardRect.height - 8)
                        : anchor.top - cardRect.height - 8;
                }
                this.profileCard.x = x;
                this.profileCard.y = y;
            });
        },

        closeProfileCard() {
            this.profileCard = null;
            if (this._profileMouseMoveHandler) {
                window.removeEventListener('mousemove', this._profileMouseMoveHandler);
            }
        },
        
        showSettingsModal: false,
        showSetupModal: false,
        settingsBodyOverflow: null,
        settingsTab: 'appearance',
        nookSettingsDetail: false,
        nookConfigSaved: false,
        nookConfigResets: [],
        nookSyncCollections: ['bookmarks', 'likes'],
        nookLogOpenRunId: null,
        nookLogRun: null,
        nookLogLines: null,
        nookDetailScrolled: false,
        globalTags: [],
        globalTagsLoading: false,
        tagSearchTerm: '',
        
        configSchema: null,
        configData: null,
        configOriginal: null,
        configDefaults: null,
        configExplicit: [],
        configSaving: false,
        showAdvancedConfig: false,
        automatedTaggingInstalled: false,
        automatedTagging: null,
        automatedTaggingOriginal: null,
        automatedTaggingContext: '',
        automatedTaggingContextItems: [],
        automatedTaggingContextInput: '',
        automatedTaggingInstructionItems: [],
        automatedTaggingInstructionInput: '',
        automatedTaggingModels: [],
        automatedTaggingModelCache: null,
        automatedTaggingModelCacheKey: '',
        automatedTaggingModelCacheExpiresAt: 0,
        automatedTaggingAccounting: null,
        automatedTaggingSaving: false,
        automatedTaggingLoadingModels: false,
        automatedTaggingSpendAmount: null,
        automatedTaggingSpendPeriod: 'day',
        showAutomatedTaggingSpendModal: false,
        automatedTaggingSpendRange: '30d',
        automatedTaggingError: '',
        automatedTaggingTweetId: '',
        automatedTaggingTweetLookingUp: false,
        automatedTaggingTweetLookupError: '',
        automatedTaggingSelectedTweet: null,
        automatedTaggingTesting: false,
        automatedTaggingTestResult: null,
        automatedTaggingTestError: '',
        scheduleSettings: null,
        scheduleForm: null,
        scheduleOriginal: null,
        scheduleSaving: false,
        scheduleSaved: false,
        scheduleError: '',
        scheduleTimeHour: '3',
        scheduleTimeMinute: '00',
        scheduleTimePeriod: 'AM',
        activityRuns: [],
        activityRunsLoading: false,
        selectedActivityRun: null,
        selectedActivityLog: '',
        setupData: null,
        archiveReady: null,
        archiveResourcesLoaded: false,
        setupForced: false,
        setupRerun: false,
        setupCompletionVisible: false,
        setupCompletionNeedsReload: false,
        setupFlowStep: 1,
        setupDirection: 'forward',
        setupSkipAuth: false,
        setupPassword: '',
        setupPasswordConfirm: '',
        setupNoPassword: false,
        setupFinishing: false,
        setupError: '',
        setupPolling: false,
        notices: [],
        setupAuth: null,
        setupAuthSaved: false,
        setupSavingAuth: false,
        setupAuthSuccessPending: false,
        setupTestingAuth: false,
        setupAuthResult: null,
        setupUploading: false,
        setupUpload: null,
        setupUploadXhr: null,
        setupArchiveError: '',
        setupArchiveFresh: false,
        setupArchiveChoice: null,
        setupStepResumed: false,
        setupAuthEdited: false,
        setupStartingImport: false,
        setupConfirm: null,
        setupHelp: null,
        setupReplayForm: { url: '', password: '', placeholder_media: true, skip_quote_expansion: false, skip_link_previews: false },
        setupReplaySaving: false,
        setupReplayError: '',
        setupHelpOpen: false,
        setupHelpBrowser: 0,
        setupHelpError: '',

        mergePrimaryTag: '',
        mergeTagsList: [],
        mergeSearchTerm: '',
        mergePrimarySearchTerm: '',
        mergePrimaryDropdown: false,
        mergeTagsDropdown: false,
        mergeSelectedIndex: 0,
        primarySelectedIndex: 0,
        
        lastSyncFormatted: '',
        darkMode: true,
        
        showStatsModal: false,
        statsSummary: null,
        loadingStatsSummary: false,
        statsCollections: [],
        loadingStatsCollections: false,
        statsHealth: null,
        loadingStatsHealth: false,
        showEmptyUnavailableReasons: false,
        hoveredUnavailableReason: null,
        archiveEnrichmentIncomplete: 0,
        archiveEnrichmentRefreshing: false,
        archiveEnrichmentRequestId: 0,
        statsTags: null,
        loadingStatsTags: false,
        loadingStatsSnapshot: false,
        statsGeneratedAt: null,
        statsRefreshing: false,
        statsRefreshFailed: false,
        statsRefreshPollTimer: null,
        statsRefreshPollAttempts: 0,
        statsAgeNow: Date.now(),

        activityPollTimer: null,

        storageData: null,
        storageLoading: false,
        storageDetailedView: false,
        storageGapsCollapsed: false,
        animatingStorageToggle: false,
        hoveredStorageId: null,
        expandedStorageId: null,
        
        THEMES: THEMES,
        currentTheme: 'classic-light',
        currentAccent: null,

        fontFamily: 'system',
        installedFonts: [],
        fontDiscoveryBusy: false,
        fontDiscoveryMessage: '',
        fontPickerOpen: false,
        fontSize: 'default',
        videoPlayback: 'autoplay', // 'autoplay' | 'paused'
        mediaGallery: 'date', // 'grid' | 'carousel' | 'date'; browser-local appearance
        // Shared by feed, detail, and lightbox players for the current page.
        videoMuted: true,
        videoVolume: 1,

        FONT_OPTIONS: [
            {key: 'system', label: 'System Default', family: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif'},
            {key: 'serif', label: 'System Serif', family: 'serif'},
            {key: 'mono', label: 'System Monospace', family: 'monospace'},
            {key: 'inter', label: 'Inter', family: '"Inter", sans-serif'},
            {key: 'roboto', label: 'Roboto', family: '"Roboto", sans-serif'},
            {key: 'jakarta', label: 'Plus Jakarta Sans', family: '"Plus Jakarta Sans", sans-serif'},
            {key: 'outfit', label: 'Outfit', family: '"Outfit", sans-serif'},
            {key: 'nunito', label: 'Nunito', family: '"Nunito", sans-serif'},
            {key: 'jetbrains', label: 'JetBrains Mono', family: '"JetBrains Mono", monospace'}
        ],

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
            setInterval(() => { this.statsAgeNow = Date.now(); }, 30000);
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

        setMediaGallery(style) {
            const next = ['grid', 'carousel', 'date'].includes(style) ? style : 'date';
            if (next === this.mediaGallery) return;
            this.mediaGallery = next;
            localStorage.setItem('tvx-media-gallery', next);
            // Detail/reply templates react to the preference. The feed is rendered
            // outside Alpine, so refresh its cards explicitly as well.
            this.renderFeedList(this.tweets);
        },

        useCarouselForTweet(tweet) {
            if (this.mediaGallery !== 'date') return this.mediaGallery === 'carousel';
            const source = this.getRetweet(tweet) || tweet;
            const raw = source?.raw_json || source;
            const created = raw?.legacy?.created_at || raw?.created_at || source?.created_at;
            // Unknown dates keep the grid; collection and archive dates never
            // tell us when the author composed the attachment layout.
            return typeof created === 'string' && Date.parse(created) >= MEDIA_CAROUSEL_INTRODUCED_AT;
        },

        lightboxMotionDuration(duration) {
            return window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches ? 0 : duration;
        },

        queueLightboxMotion(callback, delay) {
            clearTimeout(lightboxMotionTimer);
            lightboxMotionTimer = setTimeout(callback, this.lightboxMotionDuration(delay));
            lightboxMotionTimer?.unref?.();
        },

        resetLightboxMotion() {
            lightboxVideoReadyCancel?.();
            lightboxVideoReadyCancel = null;
            lightboxVideoMotion?.cancel();
            lightboxVideoMotion = null;
            this.lightboxVideoMotionPhase = '';
            clearTimeout(lightboxMotionTimer);
            lightboxMotionTimer = null;
            if (lightboxSpringFrame !== null) cancelAnimationFrame(lightboxSpringFrame);
            lightboxSpringFrame = null;
            this.lightboxGestureStart = null;
            this.lightboxGestureAxis = null;
            this.lightboxGestureOffsetX = 0;
            this.lightboxGestureOffsetY = 0;
            this.lightboxGestureTransition = 'none';
            this.lightboxGestureAnimating = false;
            this.lightboxDismissPending = false;
        },

        openLightbox(media, index = 0, originVideo = null, origin = null) {
            if (this.lightboxOpen && lightboxOriginVideo) return;
            this.resetLightboxMotion();
            this.lightboxMedia = Array.isArray(media) ? media : [];
            const lastIndex = Math.max(0, this.lightboxMedia.length - 1);
            this.lightboxIndex = Math.min(lastIndex, Math.max(0, Number(index) || 0));
            this.lightboxSettledIndex = this.lightboxIndex;
            lightboxOriginVideo = originVideo;
            lightboxOriginHolder = (origin || originVideo)?.closest?.('[data-lightbox-media]') || null;
            lightboxOriginVideos.clear();
            this.lightboxInlineIndex = null;
            this.lightboxChromeHidden = false;
            this.lightboxTweet = this.lightboxTweetFor(origin || originVideo);
            if (originVideo && this.lightboxMedia[this.lightboxIndex]) {
                preparingVideoPosters.get(originVideo)?.();
                const inlinePlayer = this.isMobileVideoViewer() && Boolean(tweetNookVideoPlayer(originVideo)?.cloneNode);
                lightboxOriginVideos.set(this.lightboxIndex, originVideo);
                if (inlinePlayer) {
                    this.lightboxInlineIndex = this.lightboxIndex;
                    lightboxOriginHolder?.querySelectorAll?.('video[data-media-index]').forEach(video => {
                        if (isVideoPlayer(video)) lightboxOriginVideos.set(Number(video.dataset.mediaIndex), video);
                    });
                }
                this.lightboxMedia = this.lightboxMedia.map((item, itemIndex) => {
                    const video = lightboxOriginVideos.get(itemIndex);
                    if (!video) return item;
                    this.restoreVideoResource(video);
                    preparingVideoPosters.get(video)?.();
                    return {...item, currentTime: video.currentTime, muted: video.muted, volume: video.volume, loop: video.loop, inlinePlayer};
                });
                if (!inlinePlayer) originVideo.pause();
            }
            this.lightboxOpen = this.lightboxMedia.length > 0;
            if (this.isMobileVideoViewer()) this.setLightboxViewport(true);
            this.followLightboxShade(this.lightboxOpen);
            const animate = this.isMobileViewer() && this.lightboxMotionDuration(300) && this.lightboxMotionOrigin()?.getBoundingClientRect;
            if (animate) this.lightboxVideoMotionPhase = 'preparing';
            // Reparent before play(), within the original tap's user activation.
            // iOS must not be asked to unlock an audible player in a later tick.
            this.mountInlineLightboxVideo();
            if (lightboxInlineMount) this.setExpandedVideoAudio(originVideo, true);
            // Alpine's overlay enter lifecycle otherwise hides the live surface
            // for two animation frames. Install the first frame in the tap itself.
            const inlineMotion = animate && lightboxInlineMount && this.$refs.lightboxViewer
                && this.startLightboxVideoMotion(true);
            if (this.lightboxMedia[this.lightboxIndex]?.inlinePlayer) {
                tweetNookShowVideoUi(originVideo);
                originVideo.play()?.catch?.(() => {});
            }
            if (this.lightboxOpen) this.$nextTick(() => {
                if (!this.lightboxOpen) return;
                this.mountInlineLightboxVideo();
                this.syncLightboxPlayback();
                if (animate && !inlineMotion) this.prepareLightboxVideoMotion();
            });
        },

        closeLightbox() {
            if (this.lightboxVideoMotionPhase === 'closing') return;
            // Only touch dismissals are followed by Safari's late compatibility click.
            if (this.isMobileViewer()) videoTapBlockedUntil = Date.now() + 600;
            const preparing = this.lightboxVideoMotionPhase === 'preparing';
            const active = this.activeLightboxVideo();
            if (active) tweetNookFinishVideoScrub(active);
            this.revealLightboxOrigin();
            const media = this.activeLightboxMedia();
            // A phone close returns the picture to its feed position when that is
            // on screen, and otherwise fades; it never hands off to Alpine's fade.
            const mobile = !preparing && this.lightboxOpen && this.isMobileViewer() && this.lightboxMotionDuration(280);
            const animate = mobile && media && this.lightboxReturnOrigin();
            const shade = this.$refs.lightboxShade;
            const startBounds = animate ? {
                ...(lightboxVideoMotion?.bounds() || {...fittedMediaRect(media), radius: 0}),
                backdropOpacity: shade ? Number(getComputedStyle(shade).opacity) : Number(this.lightboxBackdropStyle()['--lightbox-backdrop-opacity']),
            } : null;
            this.resetLightboxMotion();
            const slide = lightboxInlineMount?.player.closest?.('.lightbox-slide') || media?.closest?.('.lightbox-slide');
            if (lightboxInlineMount?.player.parentElement?.style) {
                lightboxInlineMount.player.parentElement.style.transform = 'translate3d(0, 0, 0)';
                lightboxInlineMount.player.parentElement.style.transition = 'none';
            }
            if (slide?.style && (animate || lightboxInlineMount)) {
                // The reactive drag transform updates later. Neutralize it now
                // before measuring the destination for the return animation.
                slide.style.transform = 'translate3d(0, 0, 0)';
                slide.style.transition = 'none';
            }
            if (animate && this.startLightboxVideoMotion(false, startBounds)) return;
            if (mobile && this.fadeLightbox(false)) return;
            this.finishCloseLightbox();
        },

        activeLightboxVideo() {
            return lightboxInlineMounts.get(this.lightboxIndex)?.surface.querySelector('video')
                || this.$refs.lightboxStage?.querySelector?.('.lightbox-slide.is-active video[data-video-player]');
        },

        activeLightboxMedia() {
            return this.activeLightboxVideo() || this.$refs.lightboxStage?.querySelector?.('.lightbox-slide.is-active .lightbox-media');
        },

        // The feed element the current picture expands from and returns to.
        lightboxMotionOrigin() {
            if (lightboxInlineMount) return lightboxInlineMount.placeholder;
            if (lightboxOriginVideo) return lightboxOriginVideo;
            const pictures = [...(lightboxOriginHolder?.querySelectorAll?.('img, video') || [])]
                .filter(element => !element.hasAttribute?.('data-video-poster'));
            const element = pictures[this.lightboxIndex];
            return element?.isConnected ? element : null;
        },

        revealLightboxOrigin(origin = this.lightboxMotionOrigin()) {
            const track = origin?.closest?.('.media-carousel-track');
            const tile = origin?.closest?.('.media-carousel-item');
            if (!track || !tile) return;
            const frame = tile.getBoundingClientRect(), clip = track.getBoundingClientRect();
            track.scrollLeft += frame.left - clip.left - Math.max(0, (clip.width - frame.width) / 2);
        },

        lightboxReturnOrigin() {
            const origin = this.lightboxMotionOrigin();
            const rect = origin?.getBoundingClientRect?.();
            return rect?.width && rect.height && rect.top < (window.innerHeight || Infinity) && rect.top + rect.height > 0 ? origin : null;
        },

        followLightboxShade(follow) {
            if (lightboxShade) {
                window.removeEventListener('scroll', placeLightboxShade);
                window.removeEventListener('resize', placeLightboxShade);
            }
            lightboxShade = follow ? this.$refs.lightboxShade : null;
            if (!lightboxShade) return;
            placeLightboxShade();
            window.addEventListener('scroll', placeLightboxShade, {passive: true});
            window.addEventListener('resize', placeLightboxShade);
        },

        mountInlineLightboxVideo() {
            const stage = this.$refs.lightboxStage;
            lightboxOriginVideos.forEach((video, index) => {
                if (!this.lightboxMedia[index]?.inlinePlayer || lightboxInlineMounts.has(index)) return;
                const slot = index === this.lightboxInlineIndex
                    ? this.$refs.inlineVideoSlot || stage?.querySelector?.('[data-inline-video-slot]')
                    : stage?.querySelector?.(`[data-inline-video-index="${index}"]`);
                const player = tweetNookVideoPlayer(video);
                if (!slot || !player) return;
                const placeholder = player.cloneNode(false);
                const picture = getComputedStyle(video);
                placeholder.setAttribute('data-feed-fit', picture.objectFit || 'contain');
                if (picture.backgroundColor) placeholder.setAttribute('data-feed-background', picture.backgroundColor);
                const frame = player.getBoundingClientRect();
                placeholder.style.setProperty('aspect-ratio', `${frame.width} / ${frame.height}`);
                placeholder.setAttribute('aria-hidden', 'true');
                placeholder.removeAttribute('tabindex');
                player.parentElement.insertBefore(placeholder, player);
                const surface = document.createElement('div');
                surface.setAttribute('class', 'video-motion-surface');
                player.insertBefore(surface, video);
                surface.appendChild(video);
                lightboxInlineMounts.set(index, {player, placeholder, surface, preload: video.preload});
                player.classList.add('lightbox-video-player');
                // Fill portrait footage without changing its aspect ratio; the
                // existing fit control can reveal the uncropped picture.
                const aspect = video.videoWidth / video.videoHeight || Number(video.dataset?.videoAspect);
                player.classList.toggle('is-video-fill', aspect > 0 && aspect < 1);
                video.setAttribute('data-viewer-fit-ready', '');
                video.setAttribute('data-lightbox-index', String(index));
                video.preload = 'auto';
                slot.appendChild(player);
                if (index !== this.lightboxIndex || !this.$refs.lightboxViewer) return;
                const viewer = this.$refs.lightboxViewer;
                viewer.classList.remove('is-video-closed');
                viewer.classList.add('is-video-viewer');
                if (this.lightboxVideoMotionPhase) viewer.classList.add('is-video-motion');
                viewer.style.display = 'flex';
                viewer.style.setProperty?.('--lightbox-video-lift', `${this.lightboxVideoLift()}px`);
                slot.style.display = '';
                // On reopening a later gallery item Alpine has not yet moved
                // this persistent slot from its previous index. Measure the
                // opening flight in its active position, within this tap.
                slot.style.transform = 'translate3d(0, 0, 0)';
                slot.style.transition = 'none';
                slot.classList.add('is-active');
            });
            lightboxInlineMount = lightboxInlineMounts.get(this.lightboxIndex) || null;
        },

        restoreInlineLightboxVideo() {
            lightboxInlineMounts.forEach(({player, placeholder, surface, preload}) => {
                const video = surface.querySelector('video');
                surface.replaceWith(video);
                video.removeAttribute('data-lightbox-index');
                video.removeAttribute('data-viewer-fit-ready');
                video.preload = preload;
                player.classList.remove('lightbox-video-player', 'is-video-fill');
                if (placeholder.isConnected) placeholder.replaceWith(player);
                else { video.pause(); player.remove(); }
            });
            lightboxInlineMounts.clear();
            lightboxInlineMount = null;
            this.lightboxInlineIndex = null;
        },

        isMobileViewer() {
            return Boolean(this.lightboxMedia[this.lightboxIndex])
                && isPhoneLayout();
        },

        isMobileVideoViewer() {
            const media = this.lightboxMedia[this.lightboxIndex];
            return media?.type === 'video' && !media.isGif && this.isMobileViewer();
        },

        setLightboxViewport(expanded) {
            const meta = document.querySelector?.('meta[name="viewport"]');
            if (!meta) return;
            if (expanded && lightboxViewportContent === null) {
                lightboxViewportContent = meta.getAttribute('content') || '';
                const content = lightboxViewportContent.replace(/(?:^|,)\s*viewport-fit\s*=\s*[^,]*/gi, '');
                meta.setAttribute('content', `${content}, viewport-fit=cover`);
            } else if (!expanded && lightboxViewportContent !== null) {
                meta.setAttribute('content', lightboxViewportContent);
                lightboxViewportContent = null;
            }
        },

        prepareLightboxVideoMotion() {
            const media = this.activeLightboxMedia();
            if (!media) { this.lightboxVideoMotionPhase = ''; return; }
            // The portaled video already has its feed picture/poster. Expand it
            // immediately, including when a first tap is still loading metadata.
            if (lightboxInlineMount) {
                if (!this.startLightboxVideoMotion(true)) this.lightboxVideoMotionPhase = '';
                return;
            }
            // Wait for the image or the restored playback frame so the expanding
            // surface never replaces the feed picture with an empty or black frame.
            const origin = this.lightboxMotionOrigin();
            // A GIF carries on from the frame the feed was showing.
            const gif = media.tagName === 'VIDEO' && !isVideoPlayer(media) && origin?.tagName === 'VIDEO';
            const follow = () => {
                if (gif && Number.isFinite(media.duration) && media.duration > 0) media.currentTime = (origin.currentTime || 0) % media.duration;
            };
            const events = ['load', 'loadeddata', 'seeked'];
            let timeout;
            const cleanup = () => {
                clearTimeout(timeout);
                media.removeEventListener('loadedmetadata', follow);
                events.forEach(name => media.removeEventListener(name, ready));
            };
            const begin = () => {
                cleanup();
                lightboxVideoReadyCancel = null;
                if (!this.startLightboxVideoMotion(true) && !this.fadeLightbox(true)) this.lightboxVideoMotionPhase = '';
            };
            const ready = () => {
                if (media.tagName === 'IMG' ? media.complete && media.naturalWidth : media.readyState >= 2 && !media.seeking) begin();
            };
            lightboxVideoReadyCancel = cleanup;
            events.forEach(name => media.addEventListener(name, ready));
            if (gif && media.readyState >= 1) follow();
            else if (gif) media.addEventListener('loadedmetadata', follow, {once: true});
            timeout = setTimeout(begin, 500);
            ready();
        },

        startLightboxVideoMotion(opening, startBounds = null) {
            const motion = animateMobileMediaBounds(this.activeLightboxMedia(), this.lightboxMotionOrigin(), opening,
                this.lightboxMotionDuration(opening ? 300 : 280), startBounds, this.$refs.lightboxShade, this.lightboxRestingOpacity());
            if (!motion) return false;
            this.runLightboxMotion(motion, opening);
            return true;
        },

        // A phone viewer without a feed picture to move to fades in or out instead.
        fadeLightbox(opening) {
            const options = {duration: this.lightboxMotionDuration(180), easing: 'ease-out', fill: 'both'};
            const fades = [this.$refs.lightboxViewer, this.$refs.lightboxShade].filter(element => element?.animate).map(element => {
                // Closing continues from the shade's dragged opacity.
                const current = opening ? 0 : Number(getComputedStyle(element).opacity ?? 1);
                // The shade opens to the resting opacity its reactive style keeps.
                const target = !opening ? 0 : element === this.$refs.lightboxShade ? this.lightboxRestingOpacity() : 1;
                return element.animate([{opacity: Number.isFinite(current) ? current : 1}, {opacity: target}], options);
            });
            if (!fades.length || !options.duration) return false;
            this.runLightboxMotion({
                cancel: () => fades.forEach(fade => fade.cancel()),
                bounds: () => null,
                finished: Promise.all(fades.map(fade => fade.finished)).then(() => true, () => false),
            }, opening);
            return true;
        },

        runLightboxMotion(motion, opening) {
            lightboxVideoMotion = motion;
            this.lightboxVideoMotionPhase = opening ? 'opening' : 'closing';
            motion.finished.then(completed => {
                if (lightboxVideoMotion !== motion) return;
                if (completed && !opening) { this.finishCloseLightbox(); return; }
                motion.cancel();
                lightboxVideoMotion = null;
                this.lightboxVideoMotionPhase = '';
                this.$refs.lightboxViewer?.classList.remove('is-video-motion');
                const video = this.activeLightboxVideo();
                if (completed && opening && video) tweetNookShowVideoUi(video);
            });
        },

        // Whether the viewer is showing this feed player in place, borrowed from its card.
        isInlineLightboxPlayer(player) {
            return lightboxInlineMount?.player === player;
        },

        finishCloseLightbox() {
            const active = this.activeLightboxVideo();
            const origin = lightboxOriginVideo;
            const resume = Boolean(origin && active && !active.paused && !active.ended);
            if (origin && active && origin !== active) {
                origin.currentTime = active.currentTime;
            }
            this.$refs.lightboxStage?.querySelectorAll?.('video').forEach(video => this.setExpandedVideoAudio(video, false));
            if (origin) this.setExpandedVideoAudio(origin, false);
            if (origin && !resume) heldAutoplayVideos.add(origin);
            this.lightboxOpen = false;
            if (this.isMobileViewer()) videoTapBlockedUntil = Date.now() + 500;
            if ((lightboxInlineMount || this.isMobileViewer()) && this.$refs.lightboxViewer) {
                // Hide the viewer and restore the feed in one handoff; never
                // expose a fullscreen frame while Alpine schedules leave.
                this.$refs.lightboxViewer.classList.add('is-video-closed');
                this.$refs.lightboxViewer.classList.remove('is-video-controls-hidden');
                this.$refs.lightboxViewer.style.display = 'none';
            }
            this.restoreInlineLightboxVideo();
            this.setLightboxViewport(false);
            if (origin?.isConnected && isFeedVideoPlayer(origin)) {
                this.revealLightboxOrigin(origin);
                const track = origin.closest?.('.media-carousel-track');
                if (resume && feedVideoVisibility(origin).ratio >= VIDEO_AUTOPLAY_STOP_RATIO) this.resumeAutoplayAt(origin, track);
                // Rejoin even when portaling happened within the observer's
                // near-viewport margin and produced no new intersection entry.
                this.updateAutoplayVisibility(origin, true, false);
            }
            lightboxOriginVideo = null;
            lightboxOriginVideos.clear();
            this.pauseLightboxPlayback();
            this.resetLightboxMotion();
            if (resume && origin.isConnected && origin.paused) origin.play()?.catch?.(() => {});
            this.autoplayNextVideo();
        },

        pauseLightboxPlayback() {
            this.$refs.lightboxStage?.querySelectorAll?.('video').forEach(video => video.pause());
        },

        syncLightboxPlayback() {
            const stage = this.$refs.lightboxStage;
            if (!stage) return;
            if (!this.lightboxOpen) { this.pauseLightboxPlayback(); return; }
            if (this.lightboxVideoMotionPhase === 'closing') return;
            this.mountInlineLightboxVideo();
            const videos = [...stage.querySelectorAll('video')];
            const isActive = video => {
                const indexed = video.getAttribute?.('data-lightbox-index');
                return indexed !== null && indexed !== undefined
                    ? Number(indexed) === this.lightboxIndex : video.closest('.lightbox-slide')?.classList.contains('is-active');
            };
            videos.forEach(video => { if (!isActive(video)) video.pause(); });
            videos.forEach(video => {
                if (!isActive(video)) return;
                else {
                    const media = this.lightboxMedia[Number(video.dataset?.lightboxIndex)];
                    if (media && !media.inlinePlayer && Number.isFinite(media.currentTime) && !video.hasAttribute('data-playback-restored')) {
                        video.setAttribute('data-playback-restored', '');
                        video.muted = media.muted;
                        video.volume = media.volume;
                        const restore = () => {
                            video.currentTime = media.currentTime;
                        };
                        if (video.readyState >= 1) restore();
                        else video.addEventListener('loadedmetadata', restore, { once: true });
                    }
                    this.setExpandedVideoAudio(video, true);
                    if (this.isMobileVideoViewer() && !video.hasAttribute('data-viewer-fit-ready')) {
                        const player = tweetNookVideoPlayer(video);
                        const aspect = video.videoWidth / video.videoHeight || this.mediaFrameLayout(media?.m || {}, 'detail').sourceAspect;
                        player?.classList?.toggle('is-video-fill', aspect < 1);
                        video.setAttribute('data-viewer-fit-ready', '');
                    }
                    if ((video.hasAttribute('autoplay') || isVideoPlayer(video)) && video.paused !== false) video.play().catch(() => {});
                }
            });
        },

        lightboxVisibleIndices() {
            // While a page settles, the next neighbour mounts only once it stops:
            // mounting it mid-motion stalled a frame on the phone.
            const center = this.lightboxGestureAnimating ? this.lightboxSettledIndex : this.lightboxIndex;
            const first = Math.max(0, Math.min(this.lightboxIndex, center - 1));
            const last = Math.min(this.lightboxMedia.length - 1, Math.max(this.lightboxIndex, center + 1));
            const indices = [];
            for (let index = first; index <= last; index += 1) if (index !== this.lightboxInlineIndex) indices.push(index);
            // A post has at most four attachments. Keep its live video decoders
            // mounted so swiping back preserves both progress and permission.
            this.lightboxMedia.forEach((media, index) => {
                if (media.inlinePlayer && index !== this.lightboxInlineIndex && !indices.includes(index)) indices.push(index);
            });
            return indices.sort((a, b) => a - b);
        },

        lightboxSlideStyle(mediaIndex) {
            const position = (mediaIndex - this.lightboxIndex) * 100;
            const verticalProgress = Math.max(0, this.lightboxGestureOffsetY)
                / Math.max(1, window.innerHeight || 800);
            const scale = this.lightboxGestureAxis === 'vertical'
                ? Math.max(0.9, 1 - verticalProgress * 0.13)
                : 1;
            return {
                transform: `translate3d(calc(${position}% + ${this.lightboxGestureOffsetX}px), ${this.lightboxGestureOffsetY}px, 0) scale(${scale})`,
                transition: this.lightboxGestureTransition,
                zIndex: mediaIndex === this.lightboxIndex ? 2 : 1,
                pointerEvents: mediaIndex === this.lightboxIndex && !this.lightboxGestureAnimating ? 'auto' : 'none',
            };
        },

        // Desktop photos keep a hint of the page behind them; phones (as in X) and
        // video get a solid backdrop.
        lightboxRestingOpacity() {
            return this.isMobileViewer() ? 1 : 0.95;
        },

        lightboxBackdropStyle() {
            const height = Math.max(1, window.innerHeight || 800);
            const progress = Math.min(1, Math.max(0, this.lightboxGestureOffsetY) / (height * 0.7));
            const opacity = this.lightboxVideoMotionPhase === 'preparing' || this.lightboxDismissPending ? 0
                : this.lightboxRestingOpacity() * (1 - progress * 0.72);
            // The spring sets the drag offset every frame, so only a dismissal fades.
            const transitionDuration = this.lightboxDismissPending ? this.lightboxMotionDuration(180) : 0;
            return {
                backgroundColor: `rgba(0, 0, 0, ${opacity})`,
                '--lightbox-backdrop-opacity': String(opacity),
                '--lightbox-video-lift': `${this.lightboxVideoLift()}px`,
                transition: transitionDuration ? `background-color ${transitionDuration}ms linear` : 'none',
            };
        },

        // X centres a phone video between the top of the screen and its playback
        // bar, 29.5pt above the middle of the space this page has under the status
        // bar. Lift it that far, or as far as the space above the picture allows.
        lightboxVideoLift() {
            const item = this.lightboxMedia[this.lightboxIndex];
            if (!this.isMobileVideoViewer() || !item) return 0;
            const width = window.innerWidth || 0;
            const box = (window.innerHeight || 0) - 80;
            const aspect = this.mediaFrameLayout(item.m || {}, 'detail').sourceAspect;
            if (aspect < 1) return 0;
            const height = Math.min(box, width / aspect);
            return Math.round(Math.max(0, Math.min(29.5, (box - height) / 2)) * 10) / 10;
        },

        // The phone backdrop is a black page layer whose opacity follows the drag.
        lightboxShadeStyle() {
            const {transition, '--lightbox-backdrop-opacity': opacity} = this.lightboxBackdropStyle();
            return {opacity, transition: transition.replace('background-color', 'opacity')};
        },

        lightboxChromeStyle() {
            // X fades a photo's controls in while the photo flies open.
            const photoOpening = this.lightboxVideoMotionPhase === 'opening' && this.isMobileViewer() && !this.isMobileVideoViewer();
            if ((this.lightboxVideoMotionPhase && !photoOpening) || this.lightboxDismissPending) return { opacity: '0' };
            // Phone controls stay put while photos page, as in X; a drag down fades them.
            const phonePaging = this.isMobileViewer() && this.lightboxGestureAxis === 'horizontal';
            if (this.lightboxGestureAnimating && !phonePaging) return { opacity: '0' };
            const distance = phonePaging ? Math.abs(this.lightboxGestureOffsetY) : Math.hypot(this.lightboxGestureOffsetX, this.lightboxGestureOffsetY);
            return { opacity: String(Math.max(0, 1 - distance / 180)) };
        },

        // The post whose media is open, for the viewer's counts and X link.
        lightboxTweetFor(origin) {
            const id = origin?.closest?.('[data-thread-id]')?.dataset?.threadId;
            if (id) return this.threadSeed(id)?.main || null;
            const main = origin?.closest?.('[data-thread-main]')?.dataset?.threadMain;
            if (main) return (main === 'panel' ? this.panelThreadData : this.threadData)?.main || null;
            const card = origin?.closest?.('[data-tweet-id]')?.dataset?.tweetId;
            return card ? this.threadSeed(card)?.main || null : null;
        },

        // A phone photo viewer shows or hides its controls on a tap, as X does;
        // elsewhere a tap beside the media closes it.
        lightboxTap(event, onMedia = false) {
            if (this.lightboxVideoMotionPhase) return;
            if (Date.now() < this.lightboxSuppressClickUntil) {
                this.lightboxSuppressClickUntil = 0;
                event?.stopPropagation?.();
                return;
            }
            if (this.isMobileViewer() && !this.isMobileVideoViewer()) {
                this.lightboxChromeHidden = !this.lightboxChromeHidden;
                return;
            }
            if (!onMedia) this.closeLightbox();
        },

        prevLightbox() {
            if (this.lightboxOpen && this.lightboxGestureAnimating) return;
            if (this.lightboxMedia.length < 2 || this.lightboxIndex <= 0) return;
            if (this.lightboxOpen) this.commitLightboxNavigation(-1);
            else this.lightboxIndex -= 1;
        },

        nextLightbox() {
            if (this.lightboxOpen && this.lightboxGestureAnimating) return;
            if (this.lightboxMedia.length < 2 || this.lightboxIndex >= this.lightboxMedia.length - 1) return;
            if (this.lightboxOpen) this.commitLightboxNavigation(1);
            else this.lightboxIndex += 1;
        },

        startLightboxGesture(event) {
            if (!this.lightboxOpen || this.lightboxGestureAnimating || this.lightboxVideoMotionPhase) return;
            this.lightboxSuppressClickUntil = 0;
            if (event.touches?.length > 1) {
                this.cancelLightboxGesture();
                return;
            }

            const target = event.target;
            if (target?.closest?.('button, .video-control-bar, video[controls], audio, input, textarea, select, a')) return;

            const touch = event.touches?.[0] || event.changedTouches?.[0];
            if (!touch) return;

            this.lightboxGestureStart = {
                x: touch.clientX,
                y: touch.clientY,
                lastX: touch.clientX,
                lastY: touch.clientY,
                lastTime: Number(event.timeStamp) || Date.now(),
                velocityX: 0,
                velocityY: 0,
                moved: false,
                closesOnClick: !target?.closest?.('img, video, button'),
            };
            this.lightboxGestureAxis = null;
            this.lightboxGestureOffsetX = 0;
            this.lightboxGestureOffsetY = 0;
            this.lightboxGestureTransition = 'none';
        },

        updateLightboxGesture(touch, timeStamp, trackVelocity = true) {
            const start = this.lightboxGestureStart;
            if (!start || !touch) return null;

            const deltaX = touch.clientX - start.x;
            const deltaY = touch.clientY - start.y;
            const absX = Math.abs(deltaX);
            const absY = Math.abs(deltaY);
            if (!this.lightboxGestureAxis && Math.max(absX, absY) >= 8) {
                if (absX > absY * 1.08 && this.lightboxMedia.length > 1) {
                    this.lightboxGestureAxis = 'horizontal';
                } else if (absY > absX * 1.08) {
                    this.lightboxGestureAxis = 'vertical';
                }
            }

            const now = Number(timeStamp) || Date.now();
            if (trackVelocity) {
                const elapsed = Math.max(8, now - start.lastTime);
                const instantX = (touch.clientX - start.lastX) / elapsed;
                const instantY = (touch.clientY - start.lastY) / elapsed;
                start.velocityX = start.moved ? start.velocityX * 0.35 + instantX * 0.65 : instantX;
                start.velocityY = start.moved ? start.velocityY * 0.35 + instantY * 0.65 : instantY;
                start.lastX = touch.clientX;
                start.lastY = touch.clientY;
                start.lastTime = now;
                start.moved = true;
            }

            if (this.lightboxGestureAxis === 'horizontal') {
                const movingPastStart = this.lightboxIndex === 0 && deltaX > 0;
                const movingPastEnd = this.lightboxIndex === this.lightboxMedia.length - 1 && deltaX < 0;
                this.lightboxGestureOffsetX = deltaX * (movingPastStart || movingPastEnd ? 0.22 : 1);
                this.lightboxGestureOffsetY = 0;
            } else if (this.lightboxGestureAxis === 'vertical') {
                this.lightboxGestureOffsetX = 0;
                this.lightboxGestureOffsetY = deltaY >= 0 ? deltaY : deltaY * 0.16;
            }

            return { deltaX, deltaY, absX, absY };
        },

        moveLightboxGesture(event) {
            if (this.lightboxOpen && this.isMobileViewer() && event.touches?.length === 1) event.preventDefault?.();
            if (!this.lightboxGestureStart) return;
            if (event.touches?.length !== 1) {
                this.cancelLightboxGesture();
                return;
            }
            const movement = this.updateLightboxGesture(event.touches[0], event.timeStamp, true);
            if (movement && this.lightboxGestureAxis) event.preventDefault?.();
        },

        endLightboxGesture(event) {
            const start = this.lightboxGestureStart;
            if (!start || !this.lightboxOpen) return;

            const touch = event.changedTouches?.[0];
            if (!touch) {
                this.cancelLightboxGesture();
                return;
            }

            // A lift right where the last move ended keeps that move's speed (Safari
            // reports the lift a few ms later); one after a pause still slows it.
            const still = touch.clientX === start.lastX && touch.clientY === start.lastY;
            const recent = (Number(event.timeStamp) || Date.now()) - start.lastTime < 50;
            const movement = this.updateLightboxGesture(touch, event.timeStamp, start.moved && !(still && recent));
            this.lightboxGestureStart = null;
            if (!movement) return;

            const { deltaX, deltaY } = movement;
            const distance = Math.hypot(deltaX, deltaY);
            if (distance >= 8) {
                videoTapBlockedUntil = Date.now() + 600;
                this.lightboxSuppressClickUntil = Date.now() + 500;
            }

            if (this.lightboxGestureAxis === 'horizontal') {
                const direction = deltaX < 0 ? 1 : -1;
                const canNavigate = direction > 0
                    ? this.lightboxIndex < this.lightboxMedia.length - 1
                    : this.lightboxIndex > 0;
                const width = Math.max(1, window.innerWidth || 320);
                // A slow release past half the width, or a flick that way, pages.
                const flick = -start.velocityX * direction >= LIGHTBOX_FLICK && Math.abs(deltaX) >= 24;
                if (canNavigate && (Math.abs(this.lightboxGestureOffsetX) > width / 2 || flick)) {
                    this.commitLightboxNavigation(direction, start.velocityX);
                    return;
                }
            } else if (this.lightboxGestureAxis === 'vertical') {
                const height = Math.max(1, window.innerHeight || 800);
                const threshold = Math.min(84, Math.max(68, height * 0.1));
                if (deltaY > 0 && (this.lightboxGestureOffsetY >= threshold
                    || (start.velocityY >= 0.5 && deltaY >= 30))) {
                    this.animateLightboxDismiss();
                    return;
                }
            }

            this.snapBackLightbox(start.velocityX, start.velocityY);
        },

        commitLightboxNavigation(direction, velocity = 0) {
            const width = Math.max(1, window.innerWidth || 320);
            this.lightboxGestureAxis = 'horizontal';
            this.lightboxIndex += direction;
            if (this.isMobileVideoViewer()) this.setLightboxViewport(true);
            lightboxOriginVideo = lightboxOriginVideos.get(this.lightboxIndex) || null;
            lightboxInlineMount = lightboxInlineMounts.get(this.lightboxIndex) || null;
            // Neighbors are mounted before the swipe. Start in this touch's
            // activation, then let Alpine update the slide classes.
            this.syncLightboxPlayback();
            this.lightboxGestureOffsetX += direction * width;
            this.$nextTick(() => this.syncLightboxPlayback());
            this.springLightbox(velocity, 0);
        },

        // Settle the drag offsets at rest with X's spring, a display frame at a
        // time so an iOS stall pauses the motion instead of skipping it.
        springLightbox(velocityX = 0, velocityY = 0) {
            if (lightboxSpringFrame !== null) cancelAnimationFrame(lightboxSpringFrame);
            lightboxSpringFrame = null;
            const fromX = this.lightboxGestureOffsetX, fromY = this.lightboxGestureOffsetY;
            this.lightboxGestureAnimating = true;
            this.lightboxGestureTransition = 'none';
            const settle = () => {
                lightboxSpringFrame = null;
                this.lightboxSettledIndex = this.lightboxIndex;
                this.lightboxGestureOffsetX = 0;
                this.lightboxGestureOffsetY = 0;
                this.lightboxGestureAxis = null;
                this.lightboxGestureAnimating = false;
                this.$nextTick(() => this.mountInlineLightboxVideo());
            };
            if (!this.lightboxMotionDuration(1) || typeof requestAnimationFrame !== 'function') { settle(); return; }
            let clock = 0, last = null;
            const step = now => {
                clock += last === null ? 0 : Math.min(now - last, 1000 / 60);
                last = now;
                const x = lightboxSpring(fromX, velocityX, clock), y = lightboxSpring(fromY, velocityY, clock);
                if (clock > 0 && Math.abs(x) < 0.5 && Math.abs(y) < 0.5) { settle(); return; }
                this.lightboxGestureOffsetX = x;
                this.lightboxGestureOffsetY = y;
                lightboxSpringFrame = requestAnimationFrame(step);
            };
            step(performance.now());
        },

        animateLightboxDismiss() {
            if (lightboxOriginVideo || (this.isMobileViewer() && this.lightboxReturnOrigin())) { this.closeLightbox(); return; }
            const duration = this.lightboxMotionDuration(180);
            this.lightboxGestureAnimating = true;
            this.lightboxDismissPending = true;
            this.lightboxGestureTransition = duration
                ? `transform ${duration}ms cubic-bezier(0.4, 0, 1, 1)`
                : 'none';
            this.lightboxGestureOffsetY = Math.max(1, window.innerHeight || 800) * 1.05;
            this.queueLightboxMotion(() => this.finishLightboxDismiss(), 180);
        },

        finishLightboxDismiss() {
            clearTimeout(lightboxMotionTimer);
            lightboxMotionTimer = null;
            this.$refs.lightboxStage?.querySelectorAll?.('video').forEach(video => this.setExpandedVideoAudio(video, false));
            this.pauseLightboxPlayback();
            this.lightboxOpen = false;
            this.queueLightboxMotion(() => this.resetLightboxMotion(), 180);
        },

        snapBackLightbox(velocityX = 0, velocityY = 0) {
            this.springLightbox(velocityX, velocityY);
        },

        cancelLightboxGesture() {
            this.lightboxGestureStart = null;
            if (this.lightboxGestureOffsetX || this.lightboxGestureOffsetY) this.snapBackLightbox();
            else this.lightboxGestureAxis = null;
        },

        handleLightboxBackdropClick(event) {
            this.lightboxTap(event);
        },

        scrollToTop() {
            const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches;
            window.scrollTo({ top: 0, behavior: reducedMotion ? 'instant' : 'smooth' });
        },

        // Startup requests the first screen does not need wait for the first page, so
        // they do not compete with it on the server.
        afterStartupFeed(callback) {
            return startupFeedSettled.then(callback);
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
            outsideAlpine(() => {
                if (append) list.insertAdjacentHTML('beforeend', html);
                else list.innerHTML = html;
            });
            this.measureTextClamps(list);
        },

        isPhoneLayout() {
            return Boolean(globalThis.matchMedia?.('(max-width: 640px)')?.matches);
        },

        // Mark clamped post text that really overflows, so only it shows Show more.
        measureTextClamps(root) {
            if (!this.isPhoneLayout()) return;
            const measure = () => {
                for (const clamp of root.querySelectorAll?.('.tweet-text-clamp') || []) {
                    if ('measured' in clamp.dataset) continue;
                    clamp.dataset.measured = '';
                    clamp.classList.toggle('is-clamped', clamp.scrollHeight > clamp.clientHeight + 1);
                }
            };
            if (typeof globalThis.requestAnimationFrame === 'function') globalThis.requestAnimationFrame(measure);
            else measure();
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

        applyTheme(key, persist = true) {
            const theme = THEMES[key];
            if (!theme) return;
            this.currentTheme = key;
            const root = document.documentElement;
            for (const [prop, val] of Object.entries(theme)) {
                if (prop.startsWith('--')) root.style.setProperty(prop, val);
            }
            const bgHex = theme['--bg-primary'] || '#000000';
            const r = parseInt(bgHex.slice(1,3),16), g = parseInt(bgHex.slice(3,5),16), b = parseInt(bgHex.slice(5,7),16);
            this.darkMode = (r*0.299 + g*0.587 + b*0.114) < 128;
            if (this.darkMode) { root.classList.remove('light'); } else { root.classList.add('light'); }
            root.style.setProperty('color-scheme', this.darkMode ? 'dark' : 'light');
            this.updateBrowserTheme();

            // Restore saved accent for this theme (if it has selectable accents)
            if (theme._accents) {
                const saved = localStorage.getItem('tvx-accent-' + key);
                const accent = saved ? theme._accents.find(a => a.color === saved) : null;
                if (accent) {
                    this.currentAccent = accent.color;
                    root.style.setProperty('--accent-color', accent.color);
                    root.style.setProperty('--accent-hover', accent.hover);
                    root.style.setProperty('--accent-text', accent.text);
                } else {
                    this.currentAccent = theme['--accent-color'];
                }
            } else {
                this.currentAccent = theme['--accent-color'];
            }

            if (persist) localStorage.setItem('tvx-theme', key);
            this.updateFavicon();
        },

        updateBrowserTheme(progress = this.activityShadeProgress) {
            progress = Math.max(0, Math.min(1, Number(progress) || 0));
            this.activityShadeProgress = progress;
            const background = THEMES[this.currentTheme]?.['--bg-primary'] || '#000000';
            // Match the Jobs scrim composited over the selected theme's background.
            const tint = [91, 112, 131];
            const alpha = .4 * progress;
            const color = progress ? '#' + tint.map((channel, index) => {
                const base = parseInt(background.slice(1 + index * 2, 3 + index * 2), 16);
                return Math.round(channel * alpha + base * (1 - alpha)).toString(16).padStart(2, '0');
            }).join('') : background;
            // Keep frame-by-frame paint values on their surfaces. Inherited root
            // variables force Safari to restyle the entire archive on every tick.
            const scrim = document.querySelector('.nook-sync-scrim');
            const edge = document.querySelector('.nook-sync-edge');
            if (scrim) scrim.style.opacity = String(progress);
            if (edge) edge.style.backgroundColor = color;
            document.documentElement.style.backgroundColor = progress ? color : '';
            document.body.style.backgroundColor = progress ? color : '';
            document.querySelector('meta[name="theme-color"]')?.setAttribute('content', color);
        },

        setAccent(accent) {
            this.currentAccent = accent.color;
            const root = document.documentElement;
            root.style.setProperty('--accent-color', accent.color);
            root.style.setProperty('--accent-hover', accent.hover);
            root.style.setProperty('--accent-text', accent.text);
            localStorage.setItem('tvx-accent-' + this.currentTheme, accent.color);
            this.updateFavicon();
        },

        // Redraws the tab icon in the active theme: corner in the accent,
        // bookmark in the text color, on a tile of the page background.
        updateFavicon() {
            const link = document.querySelector('link[rel="icon"]');
            if (!link) return;
            const style = document.documentElement.style;
            const bg = style.getPropertyValue('--bg-primary') || '#ffffff';
            const text = style.getPropertyValue('--text-primary') || '#0f1419';
            const accent = style.getPropertyValue('--accent-color') || '#1d9bf0';
            const svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
                + `<rect width="32" height="32" rx="7.5" fill="${bg}"/>`
                + `<path d="M8 8v12a4 4 0 0 0 4 4h12" fill="none" stroke="${accent}" stroke-width="4" stroke-linecap="round"/>`
                + `<path d="M14 7.5A1.5 1.5 0 0 1 15.5 6h7A1.5 1.5 0 0 1 24 7.5V18l-5-3-5 3z" fill="${text}"/>`
                + '</svg>';
            link.href = 'data:image/svg+xml,' + encodeURIComponent(svg);
        },

        canDiscoverFonts() {
            return window.isSecureContext && typeof window.queryLocalFonts === 'function';
        },

        fontChoices() {
            const choices = this.FONT_OPTIONS.map(font => ({
                ...font,
                label: ['system', 'serif', 'mono'].includes(font.key)
                    ? font.label : `${font.label} (if installed)`,
            }));
            const families = new Set(this.installedFonts);
            if (this.fontFamily.startsWith('local:')) families.add(this.fontFamily.slice(6));
            for (const family of [...families].sort((a, b) => a.localeCompare(b))) {
                choices.push({ key: `local:${family}`, label: family, family: this.localFontFamily(family) });
            }
            return choices;
        },

        localFontFamily(name) {
            // Escape a CSS string, keeping arbitrary font names out of CSS syntax.
            const escaped = String(name).replace(/[\\"\n\r\f]/g, character =>
                `\\${character.charCodeAt(0).toString(16)} `);
            return `"${escaped}", sans-serif`;
        },

        async discoverFonts() {
            if (this.fontDiscoveryBusy) return;
            if (!this.canDiscoverFonts()) {
                this.fontDiscoveryMessage = 'Font discovery is unavailable here. Choose one of the local fallback fonts.';
                return;
            }
            this.fontDiscoveryBusy = true;
            this.fontDiscoveryMessage = '';
            try {
                const fonts = await window.queryLocalFonts();
                this.installedFonts = [...new Set(fonts.map(font => font.family).filter(Boolean))];
                this.fontDiscoveryMessage = `${this.installedFonts.length} font families available on this device.`;
            } catch (error) {
                this.fontDiscoveryMessage = error.name === 'NotAllowedError'
                    ? 'Font access was not granted. The local fallback fonts are still available.'
                    : 'Could not list installed fonts. The local fallback fonts are still available.';
            } finally {
                this.fontDiscoveryBusy = false;
            }
        },

        selectFont(key) {
            const font = this.fontChoices().find(font => font.key === key);
            if (font) this.setFont(font.key, font.family);
            this.fontPickerOpen = false;
            this.$refs?.fontTrigger?.focus();
        },

        selectedFontChoice() {
            return this.fontChoices().find(font => font.key === this.fontFamily) || this.FONT_OPTIONS[0];
        },

        openFontPicker(edge = 'selected') {
            this.fontPickerOpen = true;
            this.$nextTick(() => this.focusFontOption(edge));
        },

        focusFontOption(direction) {
            const options = [...this.$refs.fontOptions.querySelectorAll('[role="option"]')];
            if (!options.length) return;
            const current = options.indexOf(document.activeElement);
            let index;
            if (direction === 'first') index = 0;
            else if (direction === 'last') index = options.length - 1;
            else if (direction === 'selected') index = this.fontChoices().findIndex(font => font.key === this.fontFamily);
            else index = (current + direction + options.length) % options.length;
            options[Math.max(0, index)].focus();
        },

        setFont(key, family) {
            this.fontFamily = key;
            document.body.style.fontFamily = family;
            localStorage.setItem('tvx-font', key);
            localStorage.setItem('tvx-font-family', family);
        },

        setFontSize(key, px) {
            this.fontSize = key;
            document.documentElement.style.setProperty('--font-size-base', px);
            localStorage.setItem('tvx-font-size', key);
            localStorage.setItem('tvx-font-size-px', px);
        },

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
            return this.splitPanel && window.innerWidth >= 1024;
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
            const bars = detailBarOpacities(offset, parts.width, parts.opening);
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
                parts.opening = true;
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
        
        loadMore() {
            if (this.loadingMore || this.loading || !this.hasMore) return;
            return this.fetchTweets(true);
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

        async fetchStats() {
            try {
                const res = await fetch('/api/stats/latest-sync');
                const data = await res.json();
                if (data.latest_sync) {
                    this.lastSyncFormatted = data.latest_sync;
                }
            } catch (e) {
                console.error('Failed to fetch stats', e);
            }
        },

        async openStatsModal() {
            this.suspendMediaForStats();
            this.showStatsModal = true;
            await this.fetchStatsSnapshot();
        },

        closeStatsModal() {
            this.showStatsModal = false;
            if (this.statsRefreshPollTimer) {
                clearTimeout(this.statsRefreshPollTimer);
                this.statsRefreshPollTimer = null;
            }
            this.resumeMediaAfterStats();
        },

        suspendMediaForStats() {
            statsSuspendedVideos.clear();
            document.querySelectorAll('video').forEach(video => {
                if (!video.paused) {
                    statsSuspendedVideos.add(video);
                    video.pause();
                }
            });
        },

        resumeMediaAfterStats() {
            statsSuspendedVideos.forEach(video => {
                if (video.isConnected !== false) {
                    const playResult = video.play();
                    if (playResult?.catch) playResult.catch(() => {});
                }
            });
            statsSuspendedVideos.clear();
        },

        setStatsLoading(loading) {
            this.loadingStatsSnapshot = loading;
            this.loadingStatsSummary = loading;
            this.loadingStatsCollections = loading;
            this.loadingStatsHealth = loading;
            this.loadingStatsTags = loading;
            this.storageLoading = loading;
        },

        applyStatsSnapshot(snapshot) {
            this.statsSummary = snapshot.summary;
            this.statsCollections = snapshot.collections || [];
            this.statsHealth = snapshot.health;
            this.storageData = snapshot.storage;
            this.statsTags = snapshot.tags;
            this.statsGeneratedAt = snapshot.generated_at;
            this.statsRefreshing = Boolean(snapshot.refreshing);
            this.statsRefreshFailed = Boolean(snapshot.refresh_failed);
            this.statsAgeNow = Date.now();
            if (snapshot.summary?.latest_sync) {
                this.lastSyncFormatted = snapshot.summary.latest_sync;
            }
        },

        applyStatsStatus(status) {
            this.statsRefreshing = Boolean(status.refreshing);
            this.statsRefreshFailed = Boolean(status.refresh_failed);
            this.statsAgeNow = Date.now();
        },

        async fetchStatsSnapshot() {
            const showSkeletons = !this.statsGeneratedAt;
            if (showSkeletons) this.setStatsLoading(true);
            try {
                const res = await fetch('/api/stats/snapshot');
                if (!res.ok) throw new Error(`Statistics request failed (${res.status})`);
                this.applyStatsSnapshot(await res.json());
                if (this.statsRefreshing) {
                    this.statsRefreshPollAttempts = 0;
                    this.scheduleStatsRefreshPoll();
                }
            } catch (e) {
                console.error('Failed to fetch statistics', e);
                this.statsRefreshFailed = true;
            } finally {
                if (showSkeletons) this.setStatsLoading(false);
            }
        },

        async refreshStats() {
            if (this.statsRefreshing || this.loadingStatsSnapshot) return;
            this.statsRefreshing = true;
            this.statsRefreshFailed = false;
            try {
                const res = await fetch('/api/stats/refresh', { method: 'POST' });
                if (!res.ok) throw new Error(`Statistics refresh failed (${res.status})`);
                const snapshot = await res.json();
                if (snapshot.generated_at !== this.statsGeneratedAt) {
                    this.applyStatsSnapshot(snapshot);
                } else {
                    this.applyStatsStatus(snapshot);
                }
                if (this.statsRefreshing) {
                    this.statsRefreshPollAttempts = 0;
                    this.scheduleStatsRefreshPoll();
                }
            } catch (e) {
                console.error('Failed to refresh statistics', e);
                this.statsRefreshing = false;
                this.statsRefreshFailed = true;
            }
        },

        scheduleStatsRefreshPoll() {
            if (this.statsRefreshPollTimer) clearTimeout(this.statsRefreshPollTimer);
            if (!this.statsRefreshing || !this.showStatsModal) return;
            this.statsRefreshPollAttempts += 1;
            if (this.statsRefreshPollAttempts > 300) {
                this.statsRefreshing = false;
                this.statsRefreshFailed = true;
                return;
            }
            this.statsRefreshPollTimer = setTimeout(() => this.pollStatsRefresh(), 1000);
        },

        async pollStatsRefresh() {
            this.statsRefreshPollTimer = null;
            if (!this.showStatsModal) return;
            try {
                const res = await fetch('/api/stats/status');
                if (!res.ok) throw new Error(`Statistics refresh poll failed (${res.status})`);
                const status = await res.json();
                if (status.generated_at !== this.statsGeneratedAt) {
                    await this.fetchStatsSnapshot();
                    return;
                }
                this.applyStatsStatus(status);
            } catch (e) {
                console.error('Failed to check statistics refresh', e);
            }
            if (this.statsRefreshing) {
                this.scheduleStatsRefreshPoll();
            } else {
                this.statsRefreshPollAttempts = 0;
            }
        },

        statsAgeLabel() {
            if (!this.statsGeneratedAt) return '';
            // Reading the timer-backed value keeps Alpine's relative label current.
            void this.statsAgeNow;
            const relative = this.formatRelativeDate(this.statsGeneratedAt);
            if (this.statsRefreshFailed) return `Refresh failed · updated ${relative}`;
            if (this.statsRefreshing) return `Refreshing · updated ${relative}`;
            return `Updated ${relative}`;
        },

        sortUnavailableReasons(reasons) {
            return [...reasons].sort((left, right) => (right.count || 0) - (left.count || 0));
        },

        getUnavailableReasons() {
            const reasons = this.statsHealth?.enrichment?.unavailable?.reasons || [];
            const visibleReasons = this.showEmptyUnavailableReasons
                ? reasons
                : reasons.filter(reason => (reason.count || 0) > 0);
            return this.sortUnavailableReasons(visibleReasons);
        },

        getUnavailableBarReasons() {
            const reasons = this.statsHealth?.enrichment?.unavailable?.reasons || [];
            return this.sortUnavailableReasons(reasons.filter(reason => (reason.count || 0) > 0));
        },

        getUnavailableSegmentWidth(reason) {
            const reasons = this.getUnavailableBarReasons();
            if (!reasons.length) return 0;

            const minFloor = 1.2;
            const logWeights = reasons.map(item => Math.log10(Math.max(10, item.count || 0)));
            const totalLog = logWeights.reduce((total, weight) => total + weight, 0);
            const rawWeights = reasons.map((item, index) => {
                const linearPct = item.percent_of_missing || 0;
                const logPct = totalLog > 0 ? (logWeights[index] / totalLog) * 100 : 0;
                return Math.max(minFloor, (linearPct * 0.82) + (logPct * 0.18));
            });
            const totalWeight = rawWeights.reduce((total, weight) => total + weight, 0);
            const index = reasons.findIndex(item => item.reason === reason.reason);
            return index === -1 ? 0 : Math.max(minFloor, (rawWeights[index] / totalWeight) * 100);
        },

        getUnavailableReasonStatus(reason) {
            const parts = [];
            if ((reason.due || 0) > 0) parts.push(`${reason.due.toLocaleString()} due now`);
            if ((reason.delayed || 0) > 0) parts.push(`${reason.delayed.toLocaleString()} scheduled`);
            if ((reason.permanent || 0) > 0) parts.push(`${reason.permanent.toLocaleString()} permanent`);
            if (parts.length) return parts.join(' · ');
            return (reason.count || 0) > 0 ? 'Availability recorded' : 'No unavailable tweets';
        },

        get showArchiveEnrichmentReminder() {
            return this.archiveEnrichmentIncomplete > 0
                && !this.archiveEnrichmentRefreshing
                && !this.activity && !this.activityStartPending
                && !this.setupForced
                // Setup's first sync finishes these posts once X is connected.
                && !['running', 'waiting_for_import', 'waiting_for_worker', 'waiting_for_setup', 'waiting_for_auth'].includes(
                    this.setupData?.archive?.enrichment_state);
        },

        async fetchArchiveEnrichmentStatus() {
            // Import completion and store attachment can briefly occur between polls.
            if (this.archiveReady === false || this.setupForced) return;
            const requestId = ++this.archiveEnrichmentRequestId;
            this.archiveEnrichmentRefreshing = true;
            try {
                const response = await fetch('/api/stats/enrichment-incomplete');
                if (!response.ok) {
                    if (response.status === 503 || response.status === 409) {
                        const pending = await response.json().catch(() => ({}));
                        if (['archive_not_ready', 'setup_required'].includes(pending.detail?.code)) return;
                    }
                    throw new Error('Could not load archive enrichment status');
                }
                const data = await response.json();
                if (requestId === this.archiveEnrichmentRequestId) {
                    this.archiveEnrichmentIncomplete = data.incomplete || 0;
                }
            } catch (error) {
                // A stale count must not become a completion warning on refresh failure.
                if (requestId === this.archiveEnrichmentRequestId) this.archiveEnrichmentIncomplete = 0;
                console.error('Failed to fetch archive enrichment status', error);
            } finally {
                if (requestId === this.archiveEnrichmentRequestId) this.archiveEnrichmentRefreshing = false;
            }
        },

        setHoveredStorage(id) {
            this.hoveredStorageId = id;
        },
        clearHoveredStorage() {
            this.hoveredStorageId = null;
        },
        getActiveStorageSegments() {
            if (!this.storageData) return [];
            const raw = this.storageDetailedView ? this.storageData.segments : this.storageData.simplified_segments;
            return (raw || []).filter(s => (s.bytes || 0) > 0);
        },
        getSegmentWidth(seg) {
            const segments = this.getActiveStorageSegments();
            if (!segments || !segments.length) return 2;
            
            const minFloor = 1.2;
            const logWeights = segments.map(s => Math.log10(Math.max(10, s.bytes || 0)));
            const totalLog = logWeights.reduce((a, b) => a + b, 0);
            
            const rawWeights = segments.map((s, idx) => {
                const linearPct = s.percent || 0;
                const logPct = totalLog > 0 ? (logWeights[idx] / totalLog) * 100 : 0;
                return Math.max(minFloor, (linearPct * 0.82) + (logPct * 0.18));
            });

            const totalWeight = rawWeights.reduce((a, b) => a + b, 0);
            const myIndex = segments.findIndex(s => s.id === seg.id);
            if (myIndex === -1) return minFloor;
            
            const normalizedWidth = (rawWeights[myIndex] / totalWeight) * 100;
            return Math.max(minFloor, normalizedWidth);
        },
        toggleStorageDetailView() {
            if (this.animatingStorageToggle) return;
            this.animatingStorageToggle = true;

            // Phase 1: Collapse all segment gaps so the bar becomes solid.
            this.storageGapsCollapsed = true;

            // Phase 2: After the bar becomes solid, flip state and reopen the gaps.
            setTimeout(() => {
                this.storageDetailedView = !this.storageDetailedView;
                this.expandedStorageId = null;

                this.$nextTick(() => {
                    this.storageGapsCollapsed = false;
                    setTimeout(() => {
                        this.animatingStorageToggle = false;
                    }, 600);
                });
            }, 250);
        },
        toggleExpandStorage(id) {
            if (!this.storageDetailedView) return; // Only detailed view allows row expansion
            this.expandedStorageId = this.expandedStorageId === id ? null : id;
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
        
        getInitial(tweet) {
            if (!tweet || !tweet.author) return '?';
            if (tweet.author.display_name) return avatarInitial(tweet.author.display_name);
            if (tweet.author.username) return avatarInitial(tweet.author.username);
            return '?';
        },

        toggleTweetMenu(menuKey) {
            if (Date.now() < this.tweetMenuClickBlockedUntil) return;
            this.tweetMenuOpen = this.tweetMenuOpen === menuKey ? null : menuKey;
            if (this.tweetMenuOpen) this.fullRawTweet({ tweet_id: menuKey.slice(menuKey.indexOf(':') + 1) });
            this.syncListMenuButton();
        },

        // Feed cards share one menu, keyed by the card and placed over the button that
        // opened it, where each card's own menu used to open.
        toggleListTweetMenu(button) {
            if (Date.now() < this.tweetMenuClickBlockedUntil) return;
            const card = button.closest('.feed-card');
            const tweet = this.feedCardTweet(button);
            if (!card || !tweet) return;
            const key = `list:${card.dataset.tweetId}`;
            if (this.tweetMenuOpen === key) {
                this.closeTweetMenu(key);
                return;
            }
            const hidden = Boolean(this.tweets.find(item => String(item.tweet_id) === card.dataset.tweetId)?.hidden);
            this.listMenu = { key, tweet, hidden, ...this.listMenuPosition(button) };
            this.tweetMenuOpen = key;
            this.fullRawTweet(tweet);
            this.syncListMenuButton();
        },

        listMenuPosition(button) {
            const area = this.$refs?.feedArea?.getBoundingClientRect();
            const rect = button.getBoundingClientRect();
            return area ? { top: rect.top - area.top, right: area.right - rect.right } : { top: 0, right: 0 };
        },

        // Keep the open list menu on its button when cards above it change height.
        placeListMenu() {
            if (!listMenuButton?.isConnected || this.tweetMenuOpen !== this.listMenu.key) return;
            const { top, right } = this.listMenuPosition(listMenuButton);
            if (top !== this.listMenu.top) this.listMenu.top = top;
            if (right !== this.listMenu.right) this.listMenu.right = right;
        },

        // The open menu covers its button, which is hidden and marked expanded meanwhile.
        syncListMenuButton() {
            const open = this.tweetMenuOpen && this.tweetMenuOpen === this.listMenu.key
                ? this.feedCard(this.tweetMenuOpen.slice('list:'.length))?.querySelector('[data-action="menu"]') || null
                : null;
            if (open === listMenuButton) return;
            outsideAlpine(() => {
                listMenuButton?.classList.remove('opacity-0');
                listMenuButton?.setAttribute('aria-expanded', 'false');
                open?.classList.add('opacity-0');
                open?.setAttribute('aria-expanded', 'true');
            });
            listMenuButton = open;
        },

        fullRawTweet(tweet) {
            const tweetId = this.getTweetId(tweet);
            if (this.isDemo || typeof globalThis.fetch !== 'function' || !/^\d+$/.test(tweetId || '')) {
                return Promise.resolve(null);
            }
            if (!rawTweetRequests.has(tweetId)) {
                rawTweetRequests.set(tweetId, globalThis.fetch(`/api/tweets/${tweetId}/raw`)
                    .then(response => (response.ok ? response.json() : null))
                    .then(data => data?.raw_json || null)
                    .catch(() => null)
                    .then(raw => {
                        if (raw) rawTweetValues.set(tweetId, raw);
                        else rawTweetRequests.delete(tweetId);
                        return raw;
                    }));
                if (rawTweetRequests.size > 50) {
                    const oldest = rawTweetRequests.keys().next().value;
                    rawTweetRequests.delete(oldest);
                    rawTweetValues.delete(oldest);
                }
            }
            return rawTweetRequests.get(tweetId);
        },

        closeTweetMenu(menuKey = null) {
            if (!menuKey || this.tweetMenuOpen === menuKey) this.tweetMenuOpen = null;
            this.syncListMenuButton();
        },

        startTweetMenuPress(event, tweetId, tagsData) {
            if (event?.ctrlKey || (event?.pointerType === 'mouse' && event.button !== 0)) return;
            this.cancelTweetMenuPress();
            this.tweetMenuPressTimer = setTimeout(() => {
                this.tweetMenuPressTimer = null;
                this.tweetMenuClickBlockedUntil = Date.now() + 750;
                this.openTagModal(tweetId, tagsData);
            }, 1000);
        },

        cancelTweetMenuPress() {
            if (this.tweetMenuPressTimer !== null) {
                clearTimeout(this.tweetMenuPressTimer);
                this.tweetMenuPressTimer = null;
            }
        },

        async openTweetHistory(tweetId) {
            this.closeTweetMenu();
            const id = String(tweetId || '');
            if (!/^\d+$/.test(id)) return;
            this.historyTweetId = id;
            this.historyData = null;
            this.historyError = '';
            this.historyOpen = true;
            if (this.isDemo || typeof globalThis.fetch !== 'function') {
                this.historyError = 'History is not available in the demo.';
                return;
            }
            this.historyLoading = true;
            try {
                const response = await globalThis.fetch(`/api/tweets/${id}/history`);
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                const data = await response.json();
                if (this.historyTweetId === id) this.historyData = data;
            } catch {
                if (this.historyTweetId === id) this.historyError = "Couldn't load this tweet's history.";
            } finally {
                if (this.historyTweetId === id) this.historyLoading = false;
            }
        },

        closeTweetHistory() {
            this.historyOpen = false;
        },

        // Offered only on tweets that have, or once had, a community note.
        hasNoteHistory(tweet) {
            const raw = tweet?.raw_json;
            return Boolean(tweet?.note_history || (raw && typeof raw === 'object' && raw.birdwatch_pivot));
        },

        async openNoteHistory(tweetId) {
            this.closeTweetMenu();
            const id = String(tweetId || '');
            if (!/^\d+$/.test(id)) return;
            this.notesTweetId = id;
            this.notesData = null;
            this.notesError = '';
            this.notesOpen = true;
            if (this.isDemo || typeof globalThis.fetch !== 'function') {
                this.notesError = 'Community note history is not available in the demo.';
                return;
            }
            this.notesLoading = true;
            try {
                const response = await globalThis.fetch(`/api/tweets/${id}/notes`);
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                const data = await response.json();
                if (this.notesTweetId === id) this.notesData = data;
            } catch {
                if (this.notesTweetId === id) this.notesError = "Couldn't load this tweet's community notes.";
            } finally {
                if (this.notesTweetId === id) this.notesLoading = false;
            }
        },

        closeNoteHistory() {
            this.notesOpen = false;
        },

        notesCurrent() {
            return this.notesData?.versions?.find(version => version.current) || null;
        },

        notesEarlier() {
            return (this.notesData?.versions || []).filter(version => !version.current);
        },

        noteVersionDates(version) {
            const day = value => this.historyTime(value);
            const saved = version.source === 'Saved with the tweet' ? ' · saved with the tweet' : '';
            if (!version.first_seen_at) return `Shown at an unknown time${saved}`;
            if (version.removed_at) return `Shown ${day(version.first_seen_at)} – ${day(version.removed_at)}${saved}`;
            return `Shown since ${day(version.first_seen_at)}${saved}`;
        },

        historyLegend() {
            const shown = new Set(this.historyTimeline().map(event => event.segment));
            return [['on', 'On X'], ['missing', 'Missing'], ['protected', 'Protected']]
                .filter(([segment]) => shown.has(segment));
        },

        historyStatusMeta() {
            const status = this.historyData?.status || {};
            const parts = [status.last_checked_at ? `Last checked ${this.historyTime(status.last_checked_at)}` : 'Not checked yet'];
            if (status.next_login_check_at) parts.push(`next login check ${this.historyTime(status.next_login_check_at)}`);
            return parts.join(' · ');
        },

        // Newest first. Each event's segment is the tweet's state from the older event
        // below it up to this one, so the rail reads as stretches on X, missing, or private.
        historyTimeline() {
            const events = this.historyData?.events || [];
            const stateOf = { available: 'on', missing: 'missing', protected: 'protected' };
            const states = new Array(events.length);
            let state = 'unknown';
            for (let index = events.length - 1; index >= 0; index--) {
                const event = events[index];
                // Being saved to the archive means the tweet was on X at the time.
                const saved = event.source === 'Archive' && state === 'unknown';
                state = stateOf[event.status] || (saved ? 'on' : state);
                states[index] = state;
            }
            return events.map((event, index) => ({
                ...event,
                segment: index + 1 < events.length ? states[index + 1] : 'none',
            }));
        },

        historyTime(value) {
            const date = value ? new Date(value) : null;
            if (!date || Number.isNaN(date.getTime())) return '—';
            return date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
        },

        openTagsFromTweetMenu(tweetId, tagsData) {
            this.closeTweetMenu();
            this.openTagModal(tweetId, tagsData);
        },

        copyTextWithLegacyFallback(text) {
            const textarea = document.createElement('textarea');
            textarea.value = text;
            textarea.setAttribute('readonly', '');
            textarea.style.position = 'fixed';
            textarea.style.top = '0';
            textarea.style.left = '-9999px';
            textarea.style.opacity = '0';
            document.body.appendChild(textarea);
            try {
                textarea.focus();
                textarea.select();
                if (!document.execCommand('copy')) {
                    throw new Error('Legacy clipboard command failed');
                }
            } finally {
                document.body.removeChild(textarea);
            }
        },

        async copyRawTweet(tweet) {
            this.closeTweetMenu();
            const tweetId = this.getTweetId(tweet);
            const raw = rawTweetValues.get(tweetId) || (await this.fullRawTweet(tweet)) || tweet?.raw_json;
            if (!raw) {
                alert('No raw tweet data is available.');
                return;
            }
            const rawText = JSON.stringify(raw, null, 2);
            try {
                if (globalThis.navigator?.clipboard?.writeText) {
                    try {
                        await globalThis.navigator.clipboard.writeText(rawText);
                        return;
                    } catch (error) {
                        console.warn('Clipboard API copy failed; trying fallback', error);
                    }
                }
                this.copyTextWithLegacyFallback(rawText);
            } catch (error) {
                console.error('Could not copy raw tweet data', error);
                alert('Could not copy raw tweet data.');
            }
        },

        openTagModal(tweetId, tagsData) {
            const normalizedData = {
                description: typeof tagsData?.description === 'string' ? tagsData.description : '',
                tags: Array.isArray(tagsData?.tags) ? [...tagsData.tags] : [],
            };
            if (this._tagModalCloseTimer) clearTimeout(this._tagModalCloseTimer);
            this.closeTweetMenu();
            this.tagModalTweetId = tweetId;
            this.tagModalData = normalizedData;
            this.isEditingTags = false;
            this.editableTags = [...normalizedData.tags];
            this.editableDescription = normalizedData.description;
            this.tagSearchQuery = '';
            this.tagSearchDropdown = false;
            this.tagOptionIndex = -1;
            this.tagDeleteConfirm = false;
            this.tagSaving = false;
            this.tagError = '';
            this.tagConfirmError = '';
            // A tweet with nothing saved has nothing to view, so it opens in the editor.
            if (!this.tagModalHasContent()) this.startEditingTags('tags');
            this.tagModalOpen = true;
            document.body.style.overflow = 'hidden';
        },
        
        closeTagModal() {
            this.tagModalOpen = false;
            this.tagDeleteConfirm = false;
            this._tagModalCloseTimer = setTimeout(() => {
                this.tagModalData = null;
                this.tagModalTweetId = null;
                this.isEditingTags = false;
                this.editableTags = [];
                this.editableDescription = '';
                this.tagSearchDropdown = false;
                this.tagSearchQuery = '';
                document.body.style.overflow = '';
                this._tagModalCloseTimer = null;
            }, 300);
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

        updateTweetTagData(tweetId, tagData) {
            this.updateLoadedTweet(tweetId, tweet => {
                tweet.media_tags = tagData ? {
                    description: tagData.description,
                    tags: [...tagData.tags],
                } : null;
            });
        },

        // A query that names is:hidden (not -is:hidden) lists hidden tweets.
        searchShowsHidden() {
            return /(^|\s)is:hidden(?=\s|$)/i.test(this.searchQuery);
        },

        async setTweetHidden(tweetId, hidden) {
            this.closeTweetMenu();
            if (!tweetId) return;
            try {
                const res = await fetch(`/api/tweets/${encodeURIComponent(tweetId)}/hidden`, {
                    method: hidden ? 'PUT' : 'DELETE',
                });
                if (!res.ok) throw new Error(`Server error: ${res.status}`);
            } catch (e) {
                alert(`Could not ${hidden ? 'hide' : 'unhide'} the tweet: ${e.message}`);
                return;
            }
            // Cached pages and threads predate the change.
            feedPages.clear();
            this.threadCache.clear();
            this.updateLoadedTweet(tweetId, tweet => { tweet.hidden = hidden; });
            if (hidden !== this.searchShowsHidden()) this.removeFeedTweet(tweetId);
        },

        // Drop a card from the current list without reloading it.
        removeFeedTweet(tweetId) {
            if (!this.tweets.some(tweet => tweet.tweet_id === tweetId)) return;
            this.tweets = this.tweets.filter(tweet => tweet.tweet_id !== tweetId);
            const card = this.feedCard(tweetId);
            if (card) outsideAlpine(() => card.remove());
            if (this.total) this.total -= 1;
        },
        
        tagModalHasContent() {
            return Boolean(this.tagModalData?.tags?.length || this.tagModalData?.description);
        },

        // The loaded tweet the dialog is tagging, shown as a quoted card; null when it is not loaded.
        tagModalTweet() {
            if (!this.tagModalTweetId) return null;
            let found = null;
            this.updateLoadedTweet(this.tagModalTweetId, tweet => { found = found || tweet; });
            const retweet = found && this.getRetweet(found);
            return retweet ? this.formatRetweetToTweet(found, retweet) : found;
        },

        tagIsDirty() {
            const saved = this.tagModalData || { tags: [], description: '' };
            return this.editableTags.length !== saved.tags.length
                || this.editableTags.some((tag, i) => tag !== saved.tags[i])
                || this.editableDescription !== saved.description
                || Boolean(this.tagSearchQuery.trim());
        },

        // Focuses a dialog control once x-show has made it visible.
        focusTagDialogControl(id, tries = 12) {
            const el = document.getElementById(id);
            if (el && el.getClientRects().length) {
                el.focus();
                if (id === 'nook-td-desc') el.setSelectionRange(el.value.length, el.value.length);
            } else if (tries > 0) {
                setTimeout(() => this.focusTagDialogControl(id, tries - 1), 16);
            }
        },

        startEditingTags(focusTarget = 'tags') {
            this.tagFocusTarget = focusTarget;
            this.isEditingTags = true;
            this.editableTags = [...(this.tagModalData?.tags || [])];
            this.editableDescription = this.tagModalData?.description || '';
            this.tagSearchQuery = '';
            this.tagSearchDropdown = false;
            this.tagOptionIndex = -1;
            this.tagError = '';
        },

        // Cancel discards edits; a tweet without tags has no view to return to.
        cancelEditingTags() {
            if (this.tagSaving) return;
            if (!this.tagModalHasContent()) {
                this.closeTagModal();
                return;
            }
            this.isEditingTags = false;
            this.tagSearchDropdown = false;
            this.tagSearchQuery = '';
            this.tagOptionIndex = -1;
            this.tagError = '';
        },

        // Unsaved edits keep the dialog open when the backdrop is clicked.
        tagBackdropClick() {
            if (this.tagSaving || (this.isEditingTags && this.tagIsDirty())) return;
            this.closeTagModal();
        },

        // Escape closes the innermost layer first; Cmd/Ctrl+Enter saves the editor.
        tagDialogKeydown(event) {
            if (event.key === 'Escape') {
                event.preventDefault();
                this.tagDialogEscape();
            } else if (event.key === 'Enter' && (event.metaKey || event.ctrlKey) && this.isEditingTags && !this.tagDeleteConfirm) {
                event.preventDefault();
                this.saveTags();
            }
        },

        tagDialogEscape() {
            if (this.tagDeleteConfirm) this.closeDeleteConfirm();
            else if (this.tagSearchDropdown && this.tagSuggestions().length) {
                this.tagSearchDropdown = false;
                this.tagOptionIndex = -1;
            } else if (!this.tagSaving) this.closeTagModal();
        },

        tagDeleteLabel() {
            const hasTags = Boolean(this.tagModalData?.tags?.length);
            const hasDescription = Boolean(this.tagModalData?.description);
            if (hasTags && hasDescription) return 'Delete tags and description';
            return hasTags ? 'Delete tags' : 'Delete description';
        },

        tagDeleteCopy() {
            const count = this.tagModalData?.tags?.length || 0;
            const parts = [];
            if (count) parts.push(count === 1 ? '1 tag' : `${count} tags`);
            if (this.tagModalData?.description) parts.push('the description');
            return `This removes ${parts.join(' and ')} from this tweet. Other tweets keep their tags. This can’t be undone.`;
        },

        requestDeleteTags() {
            this.tagConfirmError = '';
            this.tagDeleteConfirm = true;
            this.focusTagDialogControl('nook-td-confirm-cancel');
        },

        closeDeleteConfirm() {
            if (this.tagSaving) return;
            this.tagDeleteConfirm = false;
            this.focusTagDialogControl('nook-td-delete');
        },

        searchForTag(tag) {
            this.closeTagModal();
            this.searchQuery = 'tag:' + (/\s/.test(tag) ? `"${tag}"` : tag);
            this.search();
        },

        tagCountLabel(count) {
            return count === 1 ? '1 tweet' : `${Number(count || 0).toLocaleString()} tweets`;
        },

        // Suggestion label with the typed text in bold.
        tagOptionHtml(tag) {
            const query = this.tagSearchQuery.trim();
            const at = query ? tag.toLowerCase().indexOf(query.toLowerCase()) : -1;
            if (at < 0) return this.escapeHTML(tag);
            return this.escapeHTML(tag.slice(0, at))
                + '<b>' + this.escapeHTML(tag.slice(at, at + query.length)) + '</b>'
                + this.escapeHTML(tag.slice(at + query.length));
        },

        // Existing tags not already on the tweet, then an "Add new tag" row for unknown text.
        tagSuggestions() {
            const query = this.tagSearchQuery.trim().replace(/\s+/g, ' ');
            const folded = query.toLowerCase();
            const added = new Set(this.editableTags.map(tag => tag.toLowerCase()));
            const known = this.tagAutocompleteOptions || [];
            const options = known
                .filter(opt => !added.has(opt.tag.toLowerCase()) && (!folded || opt.tag.toLowerCase().includes(folded)))
                .map(opt => ({ tag: opt.tag, count: opt.count, isNew: false }));
            if (query && !known.some(opt => opt.tag.toLowerCase() === folded) && !added.has(folded)) {
                options.push({ tag: query, count: 0, isNew: true });
            }
            return options;
        },

        openTagSuggestions() {
            this.tagSearchDropdown = true;
            this.tagOptionIndex = -1;
            this.fetchTagAutocomplete(this.tagSearchQuery);
        },

        // Keeps the suggestion menu and its active row inside the dialog's scroll area.
        revealTagOptions() {
            const list = this.$refs?.tagOptions;
            if (!list || !list.getClientRects().length) return;
            const active = this.tagOptionIndex >= 0 && document.getElementById('nook-td-option-' + this.tagOptionIndex);
            (active || list).scrollIntoView({ block: 'nearest' });
        },

        pickTagSuggestion(opt) {
            this.addEditableTag(opt.tag);
            this.tagSearchDropdown = false;
            this.tagOptionIndex = -1;
            this.$refs?.tagSearchInput?.focus();
        },

        // Returns the removed tag's position so focus can move to its neighbour.
        removeEditableTag(tag) {
            const index = this.editableTags.indexOf(tag);
            if (index >= 0) this.editableTags = this.editableTags.filter(t => t !== tag);
            return index;
        },

        tagInputKeydown(event) {
            if (event.isComposing || event.keyCode === 229) return;
            const options = this.tagSuggestions();
            const open = this.tagSearchDropdown && options.length > 0;
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault();
                const down = event.key === 'ArrowDown';
                if (!this.tagSearchDropdown) {
                    this.openTagSuggestions();
                    this.tagOptionIndex = down ? 0 : -1;
                    return;
                }
                if (!options.length) return;
                if (down) this.tagOptionIndex = (this.tagOptionIndex + 1) % options.length;
                else this.tagOptionIndex = this.tagOptionIndex <= 0 ? options.length - 1 : this.tagOptionIndex - 1;
                this.$nextTick?.(() => this.revealTagOptions());
            } else if (event.key === 'Enter') {
                if (event.metaKey || event.ctrlKey) return;
                event.preventDefault();
                if (open && this.tagOptionIndex >= 0) this.pickTagSuggestion(options[this.tagOptionIndex]);
                else if (this.tagSearchQuery.trim()) this.pickTagSuggestion({ tag: this.tagSearchQuery });
            } else if (event.key === 'Backspace' && !this.tagSearchQuery && this.editableTags.length) {
                event.preventDefault();
                this.editableTags = this.editableTags.slice(0, -1);
            } else if (event.key === 'Escape' && open) {
                event.preventDefault();
                event.stopPropagation();
                this.tagSearchDropdown = false;
                this.tagOptionIndex = -1;
            } else if (event.key === 'Tab') {
                this.tagSearchDropdown = false;
            }
        },

        async fetchTagAutocomplete(query) {
            const request = (this._tagAutocompleteRequest || 0) + 1;
            this._tagAutocompleteRequest = request;
            try {
                const res = await fetch(`/api/tags/autocomplete?q=${encodeURIComponent(query.trim())}`);
                if (res.ok && request === this._tagAutocompleteRequest) {
                    const data = await res.json();
                    this.tagAutocompleteOptions = data.tags;
                    if (this.tagOptionIndex >= this.tagSuggestions().length) this.tagOptionIndex = -1;
                }
            } catch(e) {}
        },

        addEditableTag(tag) {
            const trimmed = tag.trim().replace(/\s+/g, ' ');
            if(trimmed && !this.editableTags.find(t => t.toLowerCase() === trimmed.toLowerCase())) {
                this.editableTags.push(trimmed);
            }
            this.tagSearchQuery = '';
        },

        async saveTags() {
            if (this.tagSaving || !this.tagIsDirty()) return;
            // Text left in the tag field counts as a tag.
            if (this.tagSearchQuery.trim()) this.addEditableTag(this.tagSearchQuery);
            this.tagSaving = true;
            this.tagSearchDropdown = false;
            this.tagError = '';
            try {
                const description = this.editableDescription.trim();
                const res = await fetch(`/api/tags/${this.tagModalTweetId}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ tags: this.editableTags, description })
                });
                if (!res.ok) throw new Error(`Server error: ${res.status}`);
                const savedData = this.editableTags.length || description ? {
                    description,
                    tags: [...this.editableTags],
                } : null;
                this.updateTweetTagData(this.tagModalTweetId, savedData);
                if (savedData) {
                    this.tagModalData = savedData;
                    this.isEditingTags = false;
                } else {
                    this.closeTagModal();
                }
            } catch(e) {
                this.tagError = 'Couldn’t save your changes. They’re still here, so you can try again.';
            } finally {
                this.tagSaving = false;
            }
        },

        // Runs from the dialog's Delete confirmation.
        async deleteTags() {
            if (!this.tagModalTweetId || this.tagSaving) return;
            this.tagSaving = true;
            this.tagConfirmError = '';
            try {
                const res = await fetch(`/api/tags/${this.tagModalTweetId}`, { method: 'DELETE' });
                if (!res.ok) throw new Error(`Server error: ${res.status}`);
                this.updateTweetTagData(this.tagModalTweetId, null);
                this.tagSaving = false;
                this.closeTagModal();
            } catch (e) {
                this.tagConfirmError = 'Couldn’t delete. Nothing was changed. Try again.';
            } finally {
                this.tagSaving = false;
            }
        },
        
        async fetchGlobalTags() {
            this.globalTagsLoading = true;
            try {
                const res = await fetch('/api/tags/stats');
                if (res.ok) {
                    const data = await res.json();
                    this.globalTags = data.tags;
                }
            } catch(e) { console.error(e); }
            finally { this.globalTagsLoading = false; }
        },
        
        get filteredGlobalTags() {
            if(!this.tagSearchTerm) return this.globalTags;
            const term = this.tagSearchTerm.toLowerCase();
            return this.globalTags.filter(t => t.tag.toLowerCase().includes(term));
        },
        
        async deleteGlobalTag(tag) {
            if(!confirm(`Are you sure you want to permanently delete the tag "${tag}" from all tweets?`)) return;
            try {
                const res = await fetch(`/api/tags/global/${encodeURIComponent(tag)}`, { method: 'DELETE' });
                if(res.ok) {
                    this.globalTags = this.globalTags.filter(t => t.tag !== tag);
                } else {
                    alert("Failed to delete global tag");
                }
            } catch(e) {
                alert("Error: " + e.message);
            }
        },
        
        get filteredMergeOptions() {
            let opts = this.globalTags.filter(t => !this.mergeTagsList.includes(t.tag) && t.tag !== this.mergePrimaryTag);
            if(this.mergeSearchTerm) {
                const q = this.mergeSearchTerm.toLowerCase();
                opts = opts.filter(t => t.tag.toLowerCase().includes(q));
            }
            return opts.slice(0, 10);
        },
        
        get filteredPrimaryOptions() {
            let opts = this.globalTags.filter(t => !this.mergeTagsList.includes(t.tag));
            if(this.mergePrimarySearchTerm) {
                const q = this.mergePrimarySearchTerm.toLowerCase();
                opts = opts.filter(t => t.tag.toLowerCase().includes(q));
            }
            return opts.slice(0, 10);
        },
        
        mergeMoveUp() {
            if (this.mergeSelectedIndex > 0) this.mergeSelectedIndex--;
        },
        
        mergeMoveDown() {
            if (this.mergeSelectedIndex < this.filteredMergeOptions.length - 1) this.mergeSelectedIndex++;
        },
        
        primaryMoveUp() {
            if (this.primarySelectedIndex > 0) this.primarySelectedIndex--;
        },
        
        primaryMoveDown() {
            if (this.primarySelectedIndex < this.filteredPrimaryOptions.length - 1) this.primarySelectedIndex++;
        },

        addMergeTag(tag) {
            if(!this.mergeTagsList.includes(tag)) {
                this.mergeTagsList.push(tag);
            }
            this.$nextTick(() => { this.mergeTagsDropdown = true; });
        },
        
        addMergeTagFromInput() {
            if(!this.mergeSearchTerm || this.filteredMergeOptions.length === 0) return;
            const tag = this.filteredMergeOptions[this.mergeSelectedIndex]?.tag || this.filteredMergeOptions[0].tag;
            this.addMergeTag(tag);
            this.mergeTagsDropdown = true;
            this.mergeSelectedIndex = 0;
        },
        
        setPrimaryTagFromInput() {
            if(!this.mergePrimarySearchTerm || this.filteredPrimaryOptions.length === 0) return;
            const tag = this.filteredPrimaryOptions[this.primarySelectedIndex]?.tag || this.filteredPrimaryOptions[0].tag;
            this.mergePrimaryTag = tag;
            this.mergePrimarySearchTerm = '';
            this.mergePrimaryDropdown = false;
        },
        
        removeMergeTag(tag) {
            this.mergeTagsList = this.mergeTagsList.filter(t => t !== tag);
        },
        
        getMergePath(index, total) {
            if (total === 1) return 'M 0 50 L 100 50';
            const startY = ((index + 0.5) / total) * 100;
            return `M 0 ${startY} C 35 ${startY}, 35 50, 75 50 L 100 50`;
        },
        
        async submitMerge() {
            if(!this.mergePrimaryTag || this.mergeTagsList.length === 0) return;
            if(!confirm(`Merge ${this.mergeTagsList.length} tags into "${this.mergePrimaryTag}"? This cannot be undone.`)) return;
            
            try {
                const res = await fetch('/api/tags/merge', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ primary_tag: this.mergePrimaryTag, merge_tags: this.mergeTagsList })
                });
                if (res.ok) {
                    alert("Tags merged successfully!");
                    this.mergeTagsList = [];
                    this.mergePrimaryTag = '';
                    this.mergePrimarySearchTerm = '';
                    this.fetchGlobalTags();
                } else {
                    alert("Failed to merge tags.");
                }
            } catch(e) {
                alert("Error merging tags: " + e.message);
            }
        },

        
        automatedTaggingSpendPeriodDays(period) {
            return {
                day: 1,
                week: 7,
                month: 30,
                year: 365,
            }[period] || 1;
        },

        syncAutomatedTaggingSpendState(resetPeriod = true) {
            if (!this.automatedTagging) return;
            if (resetPeriod || !this.automatedTaggingSpendPeriod) {
                this.automatedTaggingSpendPeriod = 'day';
            }
            const daily = Number(this.automatedTagging.daily_spend_limit_usd);
            this.automatedTaggingSpendAmount = Number.isFinite(daily) && daily > 0
                ? Number((daily * this.automatedTaggingSpendPeriodDays(this.automatedTaggingSpendPeriod)).toFixed(6))
                : null;
        },

        setAutomatedTaggingSpendPeriod(period) {
            if (!this.automatedTagging) return;
            this.automatedTaggingSpendPeriod = period;
            const daily = Number(this.automatedTagging.daily_spend_limit_usd);
            this.automatedTaggingSpendAmount = Number.isFinite(daily) && daily > 0
                ? Number((daily * this.automatedTaggingSpendPeriodDays(period)).toFixed(6))
                : null;
        },

        setAutomatedTaggingSpendAmount(rawAmount) {
            if (!this.automatedTagging) return;
            if (rawAmount === '') {
                this.automatedTaggingSpendAmount = null;
                this.automatedTagging.daily_spend_limit_usd = null;
                return;
            }
            const amount = Number(rawAmount);
            this.automatedTaggingSpendAmount = Number.isFinite(amount) ? amount : null;
            this.automatedTagging.daily_spend_limit_usd = Number.isFinite(amount) && amount > 0
                ? Number((amount / this.automatedTaggingSpendPeriodDays(this.automatedTaggingSpendPeriod)).toFixed(6))
                : null;
        },

        openAutomatedTaggingSpendHistory() {
            this.automatedTaggingSpendRange = '30d';
            this.showAutomatedTaggingSpendModal = true;
            this.$nextTick(() => this.$refs.automatedTaggingSpendClose?.focus());
        },

        closeAutomatedTaggingSpendHistory() {
            this.showAutomatedTaggingSpendModal = false;
            this.$nextTick(() => this.$refs.automatedTaggingSpendButton?.focus());
        },

        automatedTaggingSpendRangeDays() {
            if (this.automatedTaggingSpendRange === 'lifetime') return null;
            return {
                '7d': 7,
                '30d': 30,
                '3m': 90,
                '1y': 365,
            }[this.automatedTaggingSpendRange] || 30;
        },

        automatedTaggingSpendHistory() {
            if (this.automatedTaggingSpendRange === 'lifetime') {
                return this.automatedTaggingAccounting?.lifetime_spend_history || [];
            }
            const history = this.automatedTaggingAccounting?.spend_history || [];
            return history.slice(-this.automatedTaggingSpendRangeDays());
        },

        automatedTaggingSpendChartData() {
            const history = this.automatedTaggingSpendHistory();
            const groupSize = history.length > 60 ? Math.ceil(history.length / 52) : 1;
            const grouped = [];
            for (let index = 0; index < history.length; index += groupSize) {
                const items = history.slice(index, index + groupSize);
                grouped.push({
                    date: items[0]?.date,
                    endDate: items.at(-1)?.date,
                    interval: items[0]?.interval || 'day',
                    estimated_cost_usd: items.reduce(
                        (sum, item) => sum + Number(item.estimated_cost_usd || 0),
                        0,
                    ),
                    requests: items.reduce((sum, item) => sum + Number(item.requests || 0), 0),
                });
            }
            return grouped;
        },

        automatedTaggingSpendRangeTotal() {
            const total = this.automatedTaggingSpendHistory().reduce(
                (sum, item) => sum + Number(item.estimated_cost_usd || 0),
                0,
            );
            return Number(total.toFixed(12));
        },

        automatedTaggingSpendRangeRequests() {
            return this.automatedTaggingSpendHistory().reduce(
                (sum, item) => sum + Number(item.requests || 0),
                0,
            );
        },

        automatedTaggingSpendBarHeight(item) {
            const maximum = Math.max(
                0,
                ...this.automatedTaggingSpendChartData().map(point => Number(point.estimated_cost_usd || 0)),
            );
            const value = Number(item?.estimated_cost_usd || 0);
            if (maximum <= 0 || value <= 0) return '0%';
            return `${Math.max((value / maximum) * 100, 3)}%`;
        },

        automatedTaggingSpendModels() {
            return Object.entries(this.automatedTaggingAccounting?.models || {})
                .map(([model, values]) => ({
                    model,
                    requests: Number(values.requests || 0),
                    estimated_cost_usd: Number(
                        values.estimated_cost_usd ?? values.cost_usd ?? 0,
                    ),
                }))
                .sort((left, right) => (
                    right.estimated_cost_usd - left.estimated_cost_usd
                    || right.requests - left.requests
                    || left.model.localeCompare(right.model)
                ));
        },

        automatedTaggingSpendTweetTypes() {
            const labels = {
                text_only: 'Text only',
                image: 'Image',
                video: 'Video',
                gif: 'GIF',
                mixed_media: 'Mixed media',
                other_media: 'Other media',
            };
            const order = Object.keys(labels);
            return Object.entries(this.automatedTaggingAccounting?.tweet_types || {})
                .map(([type, values]) => {
                    const cost = Number(values.estimated_cost_usd ?? values.cost_usd ?? 0);
                    const pricedTweets = Number(values.priced_tweets || 0);
                    const pricedRequests = Number(values.priced_requests || 0);
                    return {
                        type,
                        label: labels[type] || type,
                        requests: Number(values.requests || 0),
                        tweets: Number(values.tweets || 0),
                        media_items: Number(values.media_items || 0),
                        image_items: Number(values.image_items || 0),
                        video_items: Number(values.video_items || 0),
                        gif_items: Number(values.gif_items || 0),
                        estimated_cost_usd: cost,
                        average_tweet_usd: pricedTweets ? cost / pricedTweets : 0,
                        average_request_usd: pricedRequests ? cost / pricedRequests : 0,
                    };
                })
                .sort((left, right) => {
                    const leftIndex = order.indexOf(left.type);
                    const rightIndex = order.indexOf(right.type);
                    return (leftIndex < 0 ? order.length : leftIndex)
                        - (rightIndex < 0 ? order.length : rightIndex);
                });
        },

        automatedTaggingTweetTypeMediaLabel(row) {
            const parts = [
                [row?.image_items, 'image'],
                [row?.video_items, 'video'],
                [row?.gif_items, 'GIF'],
            ]
                .filter(([count]) => Number(count || 0) > 0)
                .map(([count, label]) => `${Number(count).toLocaleString()} ${label}${Number(count) === 1 ? '' : 's'}`);
            return parts.join(' · ') || (row?.media_items ? 'Unclassified media' : 'No media');
        },

        formatAutomatedTaggingUsd(value) {
            const amount = Number(value || 0);
            if (!Number.isFinite(amount) || amount === 0) return '$0.00';
            if (amount < 0.01) return `$${amount.toFixed(4)}`;
            return `$${amount.toFixed(2)}`;
        },

        formatAutomatedTaggingSpendDate(value, includeYear = false, monthOnly = false) {
            if (!value) return '';
            return new Date(`${value}T00:00:00Z`).toLocaleDateString('en-US', {
                month: 'short',
                day: monthOnly ? undefined : 'numeric',
                year: includeYear ? 'numeric' : undefined,
                timeZone: 'UTC',
            });
        },

        automatedTaggingSpendBarLabel(item) {
            const monthOnly = item?.interval === 'month';
            const first = this.formatAutomatedTaggingSpendDate(item?.date, monthOnly, monthOnly);
            const last = this.formatAutomatedTaggingSpendDate(item?.endDate, monthOnly, monthOnly);
            const dates = first === last ? first : `${first}–${last}`;
            return `${dates}: ${this.formatAutomatedTaggingUsd(item?.estimated_cost_usd)} across ${item?.requests || 0} requests`;
        },
        
        async fetchAutomatedTagging() {
            try {
                const response = await fetch('/api/automated-tagging');
                if (!response.ok) return;
                const data = await response.json();
                this.automatedTaggingInstalled = !!data.installed;
                this.automatedTagging = JSON.parse(JSON.stringify(data.values));
                this.automatedTaggingOriginal = JSON.parse(JSON.stringify(data.values));
                this.syncAutomatedTaggingSpendState();
                this.setAutomatedTaggingContextItems(data.values.tagging_context || []);
                this.setAutomatedTaggingInstructionItems(data.values.additional_instructions || '');
                if (this.automatedTagging.api_mode === 'free') {
                    this.automatedTagging.google_search = false;
                }
                this.automatedTaggingAccounting = data.accounting;
                if (!this.automatedTagging.api_key) {
                    this.automatedTaggingModels = [];
                    this.automatedTaggingModelCache = null;
                    this.automatedTaggingModelCacheKey = '';
                    this.automatedTaggingModelCacheExpiresAt = 0;
                }
            } catch (error) {
                console.error('Error fetching automated tagging settings', error);
            }
        },

        async loadAutomatedTaggingModels(forceRefresh = false) {
            if (!this.automatedTagging || this.automatedTaggingLoadingModels) return;
            const cacheKey = [
                this.automatedTagging.api_key || '',
                this.automatedTagging.api_mode,
                this.automatedTagging.processing_tier,
            ].join('|');
            if (
                !forceRefresh
                && this.automatedTaggingModelCacheKey === cacheKey
                && Date.now() < this.automatedTaggingModelCacheExpiresAt
            ) {
                this.automatedTaggingModels = [...(this.automatedTaggingModelCache || [])];
                return;
            }
            this.automatedTaggingLoadingModels = true;
            this.automatedTaggingError = '';
            try {
                const response = await fetch('/api/automated-tagging/models', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        api_key: this.automatedTagging.api_key,
                        api_mode: this.automatedTagging.api_mode,
                        processing_tier: this.automatedTagging.processing_tier,
                    }),
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.detail || 'Could not load Gemini models');
                this.automatedTaggingModels = data.models || [];
                this.automatedTaggingModelCache = [...this.automatedTaggingModels];
                this.automatedTaggingModelCacheKey = cacheKey;
                this.automatedTaggingModelCacheExpiresAt = Date.now() + 15 * 60 * 1000;
                const modelIds = this.automatedTaggingModels.map(model => model.id);
                if (!modelIds.includes(this.automatedTagging.model) && modelIds.length) {
                    this.automatedTagging.model = modelIds[0];
                }
            } catch (error) {
                this.automatedTaggingError = error.message;
            } finally {
                this.automatedTaggingLoadingModels = false;
            }
        },

        async saveAutomatedTagging() {
            if (!this.automatedTagging || this.automatedTaggingSaving) return;
            this.addAutomatedTaggingContext();
            this.addAutomatedTaggingInstruction();
            this.automatedTaggingSaving = true;
            this.automatedTaggingError = '';
            const values = JSON.parse(JSON.stringify(this.automatedTagging));
            values.tagging_context = [...this.automatedTaggingContextItems];
            values.additional_instructions = this.automatedTaggingInstructionItems.join('\n') || null;
            try {
                const response = await fetch('/api/automated-tagging', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ values }),
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.detail || 'Could not save settings');
                this.automatedTagging = JSON.parse(JSON.stringify(data.values));
                this.automatedTaggingOriginal = JSON.parse(JSON.stringify(data.values));
                this.syncAutomatedTaggingSpendState(false);
                this.setAutomatedTaggingContextItems(data.values.tagging_context || []);
                this.setAutomatedTaggingInstructionItems(data.values.additional_instructions || '');
                if (data.accounting) this.automatedTaggingAccounting = data.accounting;
            } catch (error) {
                this.automatedTaggingError = error.message;
            } finally {
                this.automatedTaggingSaving = false;
            }
        },

        setAutomatedTaggingContextItems(items) {
            const seen = new Set();
            this.automatedTaggingContextItems = (items || [])
                .map(value => String(value).trim())
                .filter(value => {
                    const folded = value.toLowerCase();
                    if (!value || seen.has(folded)) return false;
                    seen.add(folded);
                    return true;
                });
            this.automatedTaggingContext = this.automatedTaggingContextItems.join('\n');
            if (this.automatedTagging) {
                this.automatedTagging.tagging_context = [...this.automatedTaggingContextItems];
            }
        },

        setAutomatedTaggingApiMode(mode) {
            if (!this.automatedTagging) return;
            this.automatedTagging.api_mode = mode;
            this.automatedTagging.google_search = mode === 'paid';
        },

        addAutomatedTaggingContext() {
            const values = this.automatedTaggingContextInput
                .split(/[,\n]/)
                .map(value => value.trim())
                .filter(Boolean);
            if (!values.length) return;
            this.setAutomatedTaggingContextItems([
                ...this.automatedTaggingContextItems,
                ...values,
            ]);
            this.automatedTaggingContextInput = '';
        },

        handleAutomatedTaggingContextKeydown(event) {
            if (!event) return;
            if (event.key === 'Enter' || event.key === ',') {
                event.preventDefault();
                this.addAutomatedTaggingContext();
            } else if (event.key === 'Tab') {
                // Commit before the browser moves focus to the next control.
                this.addAutomatedTaggingContext();
            }
        },

        removeAutomatedTaggingContext(item) {
            this.setAutomatedTaggingContextItems(
                this.automatedTaggingContextItems.filter(value => value !== item)
            );
        },

        setAutomatedTaggingInstructionItems(value) {
            const source = Array.isArray(value)
                ? value
                : String(value || '').split(/\r?\n/);
            const seen = new Set();
            this.automatedTaggingInstructionItems = source
                .map(item => String(item).trim())
                .filter(item => {
                    const folded = item.toLowerCase();
                    if (!item || seen.has(folded)) return false;
                    seen.add(folded);
                    return true;
                });
            if (this.automatedTagging) {
                this.automatedTagging.additional_instructions = this.automatedTaggingInstructionItems.join('\n') || null;
            }
        },

        addAutomatedTaggingInstruction() {
            const values = this.automatedTaggingInstructionInput
                .split(/\r?\n/)
                .map(value => value.trim())
                .filter(Boolean);
            if (!values.length) return;
            this.setAutomatedTaggingInstructionItems([
                ...this.automatedTaggingInstructionItems,
                ...values,
            ]);
            this.automatedTaggingInstructionInput = '';
        },

        removeAutomatedTaggingInstruction(item) {
            this.setAutomatedTaggingInstructionItems(
                this.automatedTaggingInstructionItems.filter(value => value !== item)
            );
        },

        async lookupAutomatedTaggingTweet() {
            const tweetId = this.automatedTaggingTweetId.trim();
            this.automatedTaggingSelectedTweet = null;
            this.automatedTaggingTestResult = null;
            this.automatedTaggingTweetLookupError = '';
            if (!tweetId) {
                return;
            }
            if (!/^\d+$/.test(tweetId)) {
                this.automatedTaggingTweetLookupError = 'Enter a numeric archived tweet ID.';
                return;
            }
            this.automatedTaggingTweetLookingUp = true;
            try {
                const response = await fetch(`/api/automated-tagging/tweets?q=${encodeURIComponent(tweetId)}`);
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.detail || 'Could not load archived tweet');
                if (tweetId === this.automatedTaggingTweetId.trim()) {
                    const tweet = Array.isArray(data.tweets) ? data.tweets[0] : null;
                    if (tweet) {
                        this.automatedTaggingSelectedTweet = tweet;
                    } else {
                        this.automatedTaggingTweetLookupError = 'No archived tweet was found with that ID.';
                    }
                }
            } catch (error) {
                if (tweetId === this.automatedTaggingTweetId.trim()) {
                    this.automatedTaggingTweetLookupError = error.message;
                }
            } finally {
                if (tweetId === this.automatedTaggingTweetId.trim()) {
                    this.automatedTaggingTweetLookingUp = false;
                }
            }
        },

        async runAutomatedTaggingTest() {
            if (!this.automatedTagging || !this.automatedTaggingSelectedTweet || this.automatedTaggingTesting) return;
            this.addAutomatedTaggingContext();
            this.addAutomatedTaggingInstruction();
            this.automatedTaggingTesting = true;
            this.automatedTaggingTestResult = null;
            this.automatedTaggingTestError = '';
            const values = JSON.parse(JSON.stringify(this.automatedTagging));
            values.tagging_context = [...this.automatedTaggingContextItems];
            values.additional_instructions = this.automatedTaggingInstructionItems.join('\n') || null;
            try {
                const response = await fetch('/api/automated-tagging/test', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        tweet_id: this.automatedTaggingSelectedTweet.tweet_id,
                        values,
                    }),
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.detail || 'Test run failed');
                this.automatedTaggingTestResult = data;
                if (data.accounting) this.automatedTaggingAccounting = data.accounting;
                if (!data.success) {
                    this.automatedTaggingTestError = data.output || 'Gemini did not return a valid tagging result';
                }
            } catch (error) {
                this.automatedTaggingTestError = error.message;
            } finally {
                this.automatedTaggingTesting = false;
            }
        },

        async fetchConfig() {
            try {
                const [resSchema, resConfig, resDefaults] = await Promise.all([
                    fetch('/api/config/schema'),
                    fetch('/api/config'),
                    fetch('/api/config/defaults')
                ]);
                if (resSchema.ok && resConfig.ok && resDefaults.ok) {
                    this.configSchema = await resSchema.json();
                    const config = await resConfig.json();
                    this.configDefaults = await resDefaults.json();
                    this.configData = JSON.parse(JSON.stringify(config.values));
                    this.configOriginal = JSON.parse(JSON.stringify(config.values));
                    this.configExplicit = config.explicit;
                }
            } catch (e) {
                console.error("Error fetching config", e);
            }
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

        closeSetup() {
            if (this.setupHelpOpen) {
                this.closeSetupHelp();
                return;
            }
            if (this.setupConfirm) {
                this.setupConfirm.resolve(false);
                return;
            }
            if (this.setupForced) return;
            this.setupRerun = false;
            this.setupCompletionVisible = false;
            this.showSetupModal = false;
            if (this.setupCompletionNeedsReload) {
                this.setupCompletionNeedsReload = false;
                window.location.reload();
            }
        },

        openSetup() {
            if (this.isDemo) return;
            this.setupCompletionVisible = false;
            if (this.setupData?.completed) this.setupRerun = true;
            this.showSetupModal = true;
            this.showSettingsModal = false;
            this.fetchSetup();
        },

        goToSetupStep(step) {
            const next = Number(step);
            if (!Number.isInteger(next) || next < 1 || next > SETUP_STEP_COUNT || next === this.setupFlowStep) return;
            this.setupDirection = next < this.setupFlowStep ? 'back' : 'forward';
            this.setupFlowStep = next;
            this.setupError = '';
            // Move focus to the new step's title so keyboard and screen-reader users follow along.
            this.$nextTick?.(() => document.getElementById(`setup-step-title-${next}`)?.focus({ preventScroll: true }));
        },

        get setupCompletion() {
            return nookSetupCompletion(this);
        },

        showSetupCompletion() {
            this.setupCompletionVisible = true;
            this.showSetupModal = true;
            this.showSettingsModal = false;
            this.$nextTick?.(() => {
                const content = document.querySelector('.setup-modal-content');
                if (content) content.scrollTop = 0;
                document.getElementById('setup-completion-title')?.focus({ preventScroll: true });
            });
        },

        setupCompletionAction() {
            if (this.setupCompletion.needsConnection) {
                this.setupCompletionVisible = false;
                this.setupRerun = true;
                this.setupSkipAuth = false;
                this.goToSetupStep(2);
            } else {
                this.closeSetup();
                this.openActivityDrawer();
            }
        },

        // Setup's Schedule step shows only how often and when; Settings → Schedule has the rest.
        get setupScheduleSummary() {
            if (!this.scheduleForm) return '';
            const sentence = nookScheduleSentence(this, { details: false });
            const when = sentence ? sentence[0].toLowerCase() + sentence.slice(1) : '';
            return this.scheduleForm.enabled ? `Syncs ${when}.` : `Off. When on, syncs ${when}.`;
        },

        // Until Setup finishes the scheduler is not running and its API is gated, so
        // the first run reads and saves the schedule through Setup's own endpoint.
        async loadSetupSchedule() {
            if (this.setupData?.completed) {
                await Promise.all([this.fetchScheduleSettings(), nookLoadSyncCollections(this)]);
                return;
            }
            this.scheduleError = '';
            try {
                const response = await fetch('/api/setup/schedule');
                if (!response.ok) throw new Error('Could not load schedule');
                const data = await response.json();
                this.scheduleSettings = data;
                this.scheduleForm = this.normalizeScheduleConfig(data.config);
                this.syncScheduleTimePicker();
                this.scheduleOriginal = JSON.parse(JSON.stringify(this.scheduleForm));
                if (Array.isArray(data.collections) && data.collections.length) this.nookSyncCollections = data.collections;
            } catch (error) {
                this.scheduleError = error.message;
            }
        },

        async saveSetupSchedule() {
            if (!this.scheduleForm || this.scheduleSaving || !this.scheduleRandomWindowValid()) return;
            if (this.setupData?.completed) {
                // After Setup, the Schedule page saves as it does in Settings.
                if (this.scheduleHasChanges()) await this.saveScheduleSettings();
                if (!this.scheduleError) this.goToSetupStep(4);
                return;
            }
            this.scheduleSaving = true;
            this.scheduleError = '';
            try {
                const response = await fetch('/api/setup/schedule', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        schedule: this.normalizeScheduleConfig(this.scheduleForm),
                        collections: this.nookSyncCollections,
                    }),
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not save schedule');
                this.scheduleSettings = data;
                this.scheduleForm = this.normalizeScheduleConfig(data.config);
                this.syncScheduleTimePicker();
                this.scheduleOriginal = JSON.parse(JSON.stringify(this.scheduleForm));
                this.nookSyncCollections = data.collections;
                this.goToSetupStep(4);
            } catch (error) {
                this.scheduleError = error.message;
            } finally {
                this.scheduleSaving = false;
            }
        },

        // Resolves true when the in-app confirmation sheet is accepted.
        askSetupConfirm({ title, body, confirmLabel, destructive = false }) {
            this.setupConfirm?.resolve(false);
            const returnFocus = document.activeElement;
            return new Promise(resolve => {
                this.setupConfirm = {
                    title, body, confirmLabel, destructive,
                    resolve: accepted => {
                        this.setupConfirm = null;
                        returnFocus?.focus?.();
                        resolve(accepted);
                    },
                };
                this.$nextTick?.(() => document.querySelector('.setup-confirm .setup-confirm-accept')?.focus());
            });
        },

        // Step 1 is a choice confirmed by its footer button. Uploading picks the import;
        // the option rows pick a migration or an empty archive, and clicking again undoes it.
        // An empty archive is only created when Setup finishes, so it stays reversible.
        get setupArchiveStatus() {
            const archive = this.setupData?.archive;
            if (!archive) return 'none';
            if (this.setupUploading) return 'uploading';
            const state = archive.initialization_state;
            if (state === 'running') return 'importing';
            if (this.setupArchiveChoice === 'empty') return 'empty-selected';
            if (this.setupArchiveChoice === 'migration') return 'migration-selected';
            if (['failed', 'interrupted'].includes(state)) return 'failed';
            if (archive.uploaded && (this.setupArchiveFresh || !archive.initialization_mode)) return 'staged';
            if (archive.ready || archive.initialization_mode) return 'existing';
            return archive.uploaded ? 'staged' : 'none';
        },

        get setupArchiveFileState() {
            switch (this.setupArchiveStatus) {
                case 'staged': return 'Ready to import';
                case 'importing': return 'Importing';
                case 'failed': return 'Import stopped';
                case 'empty-selected':
                case 'migration-selected': return 'Not used';
                default: return 'Uploaded';
            }
        },

        // What the archive already holds, shown above the upload area once one exists.
        get setupArchiveSummary() {
            const archive = this.setupData?.archive || {};
            if (!archive.ready || this.setupArchiveStatus === 'importing') return '';
            if (archive.imported) return 'Your Twitter/X archive has been imported.';
            if (archive.initialization_mode === 'migration') return 'Your TweetXVault archive has been migrated.';
            if (archive.initialization_mode === 'empty') return 'Your archive was started without an import.';
            return 'Your archive is set up.';
        },

        get setupArchiveStatusText() {
            switch (this.setupArchiveStatus) {
                case 'importing': return this.activity?.summary || 'Importing. You can keep going while it runs.';
                case 'failed': return 'The import did not finish. Already imported data is kept; try again.';
                default: return '';
            }
        },

        get setupArchiveAction() {
            const status = this.setupArchiveStatus;
            const archive = this.setupData?.archive || {};
            if (status === 'empty-selected') {
                return { label: 'Continue', disabled: false, run: () => this.continueWithEmptyArchive() };
            }
            if (['importing', 'existing'].includes(status)) {
                return { label: 'Continue', disabled: false, run: () => this.goToSetupStep(2) };
            }
            if (status === 'migration-selected' || (status === 'failed' && !archive.uploaded && archive.initialization_mode === 'migration')) {
                return {
                    label: status === 'failed' ? 'Retry migration' : 'Start migration',
                    disabled: !!this.activity || this.setupStartingImport,
                    run: () => this.startSetupMigration(),
                };
            }
            return {
                label: status === 'failed' ? 'Try import again' : 'Start import',
                disabled: !archive.uploaded || this.setupUploading || this.setupStartingImport || !!this.activity,
                run: () => this.importSetupArchive(),
            };
        },

        // The empty archive is still only created at Finish; this confirms the choice before moving on.
        async continueWithEmptyArchive() {
            if (await this.askSetupConfirm({
                title: 'Start with an empty archive?',
                body: 'Importing your official Twitter/X archive first is recommended. A live sync may not recover all of your older history.',
                confirmLabel: 'Start empty',
            })) this.goToSetupStep(2);
        },

        chooseSetupArchive(choice) {
            this.setupArchiveError = '';
            this.setupArchiveChoice = this.setupArchiveChoice === choice ? null : choice;
        },

        // A first-run reload returns to the first unfinished step instead of step 1.
        resumeSetupStep() {
            if (this.setupStepResumed || !this.setupForced || !this.setupData) return;
            this.setupStepResumed = true;
            const archive = this.setupData.archive || {};
            if (!archive.initialization_mode) return;
            this.setupFlowStep = this.setupData.auth?.verified ? 3 : 2;
        },

        get setupAuthNeedsTest() {
            return !(this.setupData?.auth?.verified && !this.setupAuthEdited);
        },

        get setupAuthMissing() {
            return !this.setupAuth?.auth_token?.trim() || !this.setupAuth?.ct0?.trim();
        },

        get setupPasswordRequired() {
            return !this.setupData?.web?.password_configured;
        },

        get setupPasswordMismatch() {
            // Stay quiet while the confirmation is still a prefix of the password being typed.
            const confirm = this.setupPasswordConfirm;
            return !!confirm && confirm !== this.setupPassword && !this.setupPassword.startsWith(confirm);
        },

        get setupCanFinish() {
            if (this.setupFinishing) return false;
            if (this.setupNoPassword) return true;
            if (!this.setupPasswordRequired && !this.setupPassword) return true;
            return !!this.setupPassword && this.setupPassword === this.setupPasswordConfirm;
        },

        // Cookie instructions are generated from docs/getting-started.md by
        // scripts/build_setup_help.py, so the app and the guide cannot drift apart.
        async openSetupHelp() {
            this.setupHelpOpen = true;
            this.setupHelpError = '';
            if (!this.setupHelp) {
                try {
                    const response = await fetch('/static/setup-help.json');
                    if (!response.ok) throw new Error();
                    this.setupHelp = await response.json();
                    this.setupHelpBrowser = this.setupHelpDefaultBrowser(navigator.userAgent || '');
                } catch (_) {
                    this.setupHelpError = 'The instructions could not be loaded.';
                }
            }
            this.$nextTick?.(() => document.querySelector('.setup-help-sheet .nook-sheet-header button')?.focus());
        },

        closeSetupHelp() {
            this.setupHelpOpen = false;
            this.$nextTick?.(() => document.getElementById('setup-help-trigger')?.focus());
        },

        // Opens on the tab for the browser showing TweetNook, the likeliest one signed in to Twitter/X.
        setupHelpDefaultBrowser(userAgent) {
            const browsers = this.setupHelp?.browsers || [];
            const find = word => browsers.findIndex(browser => browser.name.toLowerCase().includes(word));
            let index = -1;
            if (/firefox|fxios/i.test(userAgent)) index = find('firefox');
            else if (/safari/i.test(userAgent) && !/chrome|chromium|crios|edg|opr/i.test(userAgent)) index = find('safari');
            return Math.max(index, 0);
        },

        setupHelpTabLabel(browser) {
            return browser.name.split(' / ')[0];
        },

        setupHelpAlsoFor(browser) {
            const names = browser.name.split(' / ').slice(1);
            return names.length ? `Also for ${names.join(', ')}.` : '';
        },

        // Developer replay source (serve --dev-tools): this install's Twitter/X traffic
        // goes to another TweetNook archive. The server keeps the password masked.
        get setupReplayConnected() {
            return !!this.setupData?.dev?.replay?.connected;
        },

        get setupReplayPlaceholder() {
            return this.setupReplayConnected
                ? !!this.setupData.dev.replay.placeholder_media
                : !!this.setupReplayForm.placeholder_media;
        },

        get setupReplaySkipQuotes() {
            return this.setupReplayConnected
                ? !!this.setupData.dev.replay.skip_quote_expansion
                : !!this.setupReplayForm.skip_quote_expansion;
        },

        get setupReplaySkipPreviews() {
            return this.setupReplayConnected
                ? !!this.setupData.dev.replay.skip_link_previews
                : !!this.setupReplayForm.skip_link_previews;
        },

        get setupReplaySummary() {
            const replay = this.setupData?.dev?.replay;
            if (!replay?.connected) return '';
            const status = replay.status || {};
            const pages = [
                `${status.bookmarks_pages ?? 0} bookmark pages`,
                `${status.likes_pages ?? 0} like pages`,
                `${status.thread_captures ?? 0} threads`,
            ].join(', ');
            return `Connected to ${replay.url}. It can replay ${pages}.`;
        },

        async setupReplayRequest(method, url, body) {
            this.setupReplaySaving = true;
            this.setupReplayError = '';
            try {
                const response = await fetch(url, {
                    method,
                    headers: { 'Content-Type': 'application/json' },
                    body: body === undefined ? undefined : JSON.stringify(body),
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'The replay source could not be updated.');
                this.setupData = { ...this.setupData, dev: data };
                return true;
            } catch (error) {
                this.setupReplayError = error.message;
                return false;
            } finally {
                this.setupReplaySaving = false;
            }
        },

        async connectSetupReplay() {
            if (this.setupReplaySaving || !this.setupReplayForm.url.trim()) return;
            const { url, password, placeholder_media, skip_quote_expansion, skip_link_previews } = this.setupReplayForm;
            if (await this.setupReplayRequest('PUT', '/api/setup/replay', { url: url.trim(), password, placeholder_media, skip_quote_expansion, skip_link_previews })) {
                this.setupReplayForm.password = '';
            }
        },

        async disconnectSetupReplay() {
            if (this.setupReplaySaving) return;
            const url = this.setupData?.dev?.replay?.url || '';
            if (await this.setupReplayRequest('DELETE', '/api/setup/replay')) this.setupReplayForm.url = url;
        },

        async setSetupReplayPlaceholder(value) {
            if (!this.setupReplayConnected) {
                this.setupReplayForm.placeholder_media = value;
                return;
            }
            await this.setupReplayRequest('PUT', '/api/setup/replay/placeholder-media', { placeholder_media: value });
        },

        async setSetupReplaySkipQuotes(value) {
            if (!this.setupReplayConnected) {
                this.setupReplayForm.skip_quote_expansion = value;
                return;
            }
            await this.setupReplayRequest('PUT', '/api/setup/replay/skip-quote-expansion', { skip_quote_expansion: value });
        },

        async setSetupReplaySkipPreviews(value) {
            if (!this.setupReplayConnected) {
                this.setupReplayForm.skip_link_previews = value;
                return;
            }
            await this.setupReplayRequest('PUT', '/api/setup/replay/skip-link-previews', { skip_link_previews: value });
        },

        get setupNoPasswordWarning() {
            const reach = 'Anyone who can reach the TweetNook server, such as other devices on your network, can open your archive and change its settings.';
            return this.setupData?.web?.password_configured ? `${reach} Your current password is removed.` : reach;
        },

        skipSetupAuth() {
            this.setupSkipAuth = true;
            this.goToSetupStep(3);
        },

        rerunSetup() {
            if (this.isDemo) return;
            this.setupFlowStep = 1;
            this.setupDirection = 'forward';
            this.setupRerun = true;
            this.settingsTab = 'setup';
            this.openSetup();
        },

        get setupProgressMessage() {
            const archive = this.setupData?.archive;
            if (!archive) return '';
            if (archive.initialization_state === 'running') return this.activity?.summary || 'Importing your archive locally in the background…';
            if (['failed', 'interrupted'].includes(archive.initialization_state)) return 'Archive setup needs attention. Retry the import or migration.';
            if (archive.followup_state === 'failed' || archive.enrichment_state === 'failed') return 'Saved posts are available to browse. Preparation did not finish; open Activity to review the error and resume.';
            if (archive.followup_state === 'running' && archive.details_complete) return 'Imported details are complete. Sync and follow-up work are still running.';
            if (archive.media_state === 'running') return 'Your archive is imported. Media is downloading in the background.';
            if (archive.followup_state === 'running') return 'Your first sync is running in the background.';
            if (archive.followup_state === 'waiting_for_auth') return 'Import complete. Connect Twitter/X to start your first sync.';
            if (archive.followup_state === 'waiting_for_worker') return 'Your first sync will start when the active task finishes.';
            if (archive.ready) return 'Your archive is ready to browse.';
            return 'Start by importing your official Twitter/X archive.';
        },

        async fetchSetup() {
            if (this.isDemo) return;
            if (this.setupPolling || this.setupFinishing) return;
            this.setupPolling = true;
            try {
                const response = await fetch('/api/setup');
                if (!response.ok) throw new Error(SETUP_LOAD_ERROR);
                const data = await response.json();
                // A transient polling failure should not outlive the next successful poll.
                if (this.setupError === SETUP_LOAD_ERROR) this.setupError = '';
                // Polling every few seconds; keep unchanged parts so their bindings stay put.
                this.setupData = this.setupData ? reconcileActivityValue(this.setupData, data) : data;
                if (!this.setupAuth) this.setupAuth = JSON.parse(JSON.stringify(data.auth.values));
                else if (!this.setupAuthEdited && !this.setupAuth.user_id && data.auth.values.user_id) {
                    this.setupAuth.user_id = data.auth.values.user_id;
                }
                this.archiveReady = !!data.archive.ready;
                this.setupForced = !!data.required;
                if (data.completed && !data.required) {
                    // Finish reloads for the new password. Only this tab resumes the
                    // extra page, once authenticated; no credentials are stored.
                    try {
                        if (sessionStorage.getItem('tweetnook.setup-completion') === 'pending') {
                            sessionStorage.removeItem('tweetnook.setup-completion');
                            this.showSetupCompletion();
                        }
                    } catch (_) { /* Storage can be disabled by browser policy. */ }
                }
                if (this.setupForced) {
                    this.showSetupModal = true;
                    this.showSettingsModal = false;
                    this.resumeSetupStep();
                }
                if (this.archiveReady && !this.setupForced && !this.archiveResourcesLoaded) {
                    await this.loadArchiveResources();
                }
            } catch (error) {
                this.setupError = error.message;
            } finally {
                this.setupPolling = false;
            }
        },

        // Runs once, when Setup status or the first feed page shows the archive is ready.
        async loadArchiveResources() {
            if (this.archiveResourcesLoaded) return;
            this.archiveResourcesLoaded = true;
            const feed = this.fetchTweets();
            // Counters ask once the feed is in, so their queries do not slow it on the server.
            const resources = feed.then(() => Promise.all([this.fetchStats(),
                this.fetchArchiveEnrichmentStatus(), this.fetchAutomatedTagging()]));
            const pendingRoute = this.pendingRoute;
            const pendingRouteState = this.pendingRouteState;
            if (pendingRoute) {
                this.pendingRoute = null;
                this.pendingRouteState = null;
                // A linked conversation needs none of these; open it now. A list
                // restores its scroll position, so it waits for its tweets unless
                // another route was opened meanwhile.
                if (pendingRoute.viewMode === 'list') await feed;
                if (pendingRoute.viewMode !== 'list' || this.routeKey(this.activeRoute) === 'list') {
                    await this.applyRoute(pendingRoute, { state: pendingRouteState });
                }
            }
            await resources;
        },

        // Starts the upstream migration; an empty archive is created by finishSetup().
        async startSetupMigration() {
            this.setupArchiveError = '';
            this.setupStartingImport = true;
            try {
                const response = await fetch('/api/setup/migrate', { method: 'POST' });
                const data = await response.json();
                if (!response.ok) throw new Error(data.detail?.message || data.detail || 'Could not start the migration');
                this.setupArchiveChoice = null;
                this.goToSetupStep(2);
                await this.fetchSetup();
                await this.fetchActivityStatus();
            } catch (error) {
                this.setupArchiveError = error.message;
            } finally {
                this.setupStartingImport = false;
            }
        },

        async finishSetup() {
            if (!this.setupCanFinish) return;
            if (this.setupNoPassword && !await this.askSetupConfirm({
                title: 'Continue without a password?',
                body: this.setupNoPasswordWarning,
                confirmLabel: 'Continue',
            })) return;
            this.setupFinishing = true;
            this.setupError = '';
            try {
                // The empty-archive choice is applied only now, so it could be changed until here.
                if (this.setupArchiveChoice === 'empty' && !this.setupData?.archive?.initialization_mode) {
                    const created = await fetch('/api/setup/archive/empty', { method: 'POST' });
                    const detail = (await created.json().catch(() => ({}))).detail;
                    if (!created.ok) throw new Error(detail?.message || detail || 'Could not create the archive');
                    this.setupArchiveChoice = null;
                }
                const body = { skip_auth: this.setupSkipAuth };
                if (this.setupNoPassword) body.no_password = true;
                else if (!this.setupData?.web?.password_configured || this.setupPassword) {
                    body.password = this.setupPassword;
                    body.confirm_password = this.setupPasswordConfirm;
                }
                const response = await fetch('/api/setup/complete', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
                });
                const data = await response.json();
                if (!response.ok) throw new Error(data.detail?.message || data.detail || 'Could not finish Setup');
                this.setupPassword = '';
                this.setupPasswordConfirm = '';
                this.setupForced = false;
                this.setupRerun = false;
                this.setupData = { ...this.setupData, completed: true, required: false };
                if (data.reload) {
                    try { sessionStorage.setItem('tweetnook.setup-completion', 'pending'); }
                    catch (_) { this.setupCompletionNeedsReload = true; }
                    if (!this.setupCompletionNeedsReload) {
                        window.location.reload();
                        return;
                    }
                }
                this.showSetupCompletion();
            } catch (error) { this.setupError = error.message; }
            finally { this.setupFinishing = false; }
            if (this.setupCompletionVisible && !this.setupCompletionNeedsReload) {
                await this.fetchSetup();
                await this.fetchActivityStatus();
            }
        },

        get noticeCount() {
            return this.notices.filter(n => !n.dismissed).length;
        },

        async fetchNotices() {
            try {
                const response = await fetch('/api/notices');
                if (response.ok) this.notices = (await response.json()).notices || [];
            } catch (_) { /* Retry on the next status poll. */ }
        },

        async dismissNotice(id) {
            const response = await fetch(`/api/notices/${encodeURIComponent(id)}/dismiss`, { method: 'POST' });
            if (response.ok) this.notices = this.notices.map(n => n.id === id ? {...n, dismissed: true} : n);
        },

        async dismissAllNotices() {
            const response = await fetch('/api/notices/dismiss-all', { method: 'POST' });
            if (response.ok) this.notices = this.notices.map(n => ({...n, dismissed: true}));
        },

        async clearDismissedNotices() {
            const response = await fetch('/api/notices/dismissed', { method: 'DELETE' });
            if (response.ok) this.notices = this.notices.filter(n => !n.dismissed);
        },

        // Notices raised by a run open that run's log in Settings → Activity logs.
        openNoticeLog(notice) {
            nookSettingsNavigate(this, 'logs');
            this.nookLogOpenRunId = notice.run_id;
        },

        openNotice(notice) {
            if (notice.action === 'activity') {
                if (notice.run_id) this.openNoticeLog(notice);
                else this.openActivityDrawer();
                return;
            }
            if (notice.action !== 'schedule') {
                this.openSetup();
                return;
            }
            this.openSettings();
            nookSettingsNavigate(this, 'schedule');
        },

        async saveSetupAuth() {
            if (!this.setupAuth || this.setupSavingAuth || this.setupAuthSuccessPending) return false;
            // A connection verified earlier needs no new test unless the cookies were edited.
            if (!this.setupAuthNeedsTest) {
                this.setupSkipAuth = false;
                this.goToSetupStep(3);
                return true;
            }
            if (this.setupAuthMissing && !this.setupReplayConnected) {
                this.setupAuthResult = { success: false, message: 'Paste both auth_token and ct0 to connect.' };
                return false;
            }
            this.setupSavingAuth = true;
            this.setupAuthSuccessPending = false;
            this.setupAuthResult = null;

            try {
                // The server validates these candidate values before committing them.
                const saveResponse = await fetch('/api/setup/auth', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(this.setupAuth),
                });

                if (!saveResponse.ok) {
                    const data = await saveResponse.json().catch(() => ({}));
                    throw new Error(saveResponse.status === 503
                        ? 'TweetNook could not reach Twitter/X. Check the server’s internet connection and try again.'
                        : 'Twitter/X did not accept these cookies. Copy fresh values from a signed-in browser and try again.');
                }

                const auth = await saveResponse.json();
                this.setupData.auth = auth;
                this.setupAuth = JSON.parse(JSON.stringify(auth.values));
                this.setupAuthSaved = !!auth.verified;
                this.setupAuthEdited = false;
                this.setupSkipAuth = false;
                await this.fetchSetup();
                if (this.setupReplayConnected && !auth.configured) {
                    this.setupSkipAuth = true;
                    this.setupAuthResult = { success: true, message: 'No Twitter/X account connected.' };
                    this.goToSetupStep(3);
                    return true;
                }
                if (this.setupData.auth.verified) {
                    this.setupAuthResult = {
                        success: true,
                        message: this.setupData.archive.followup_state === 'running'
                            ? 'Connected successfully. Your first sync has started.'
                            : 'Connected successfully. Your first sync starts after you finish setup with your chosen collections.',
                    };
                    this.setupSavingAuth = false;
                    this.setupAuthSuccessPending = true;
                    await new Promise(resolve => setTimeout(resolve, 1000));
                    this.setupAuthSuccessPending = false;
                    this.goToSetupStep(3);
                    return true;
                }
                throw new Error('Twitter/X did not accept these cookies. Copy fresh values from a signed-in browser and try again.');

            } catch (error) {
                this.setupAuthResult = { success: false, message: error.message };
                return false;
            } finally {
                this.setupSavingAuth = false;
            }
        },

        editSetupAuth() {
            this.setupAuthSaved = false;
            this.setupAuthEdited = true;
            this.setupAuthResult = null;
        },

        // Uses XHR rather than fetch so multi-gigabyte archives can report upload progress.
        async uploadSetupArchive(event) {
            const file = event.target.files?.[0];
            event.target.value = '';
            if (!file) return;
            this.setupArchiveError = '';
            if (!file.name.toLowerCase().endsWith('.zip')) {
                this.setupArchiveError = `“${file.name}” is not a .zip file. Choose the archive.zip you downloaded from Twitter/X.`;
                return;
            }
            this.setupUploading = true;
            this.setupUpload = { name: file.name, loaded: 0, total: file.size || 0 };
            try {
                const archive = await new Promise((resolve, reject) => {
                    const xhr = new XMLHttpRequest();
                    this.setupUploadXhr = xhr;
                    xhr.open('PUT', '/api/setup/archive');
                    xhr.setRequestHeader('Content-Type', 'application/zip');
                    xhr.upload.onprogress = progress => {
                        this.setupUpload = { ...this.setupUpload, loaded: progress.loaded, total: progress.total || this.setupUpload.total };
                    };
                    xhr.onload = () => {
                        let data = {};
                        try { data = JSON.parse(xhr.responseText || '{}'); } catch (_) { /* Non-JSON error page. */ }
                        if (xhr.status >= 200 && xhr.status < 300) resolve(data);
                        else reject(new Error(typeof data.detail === 'string' ? data.detail : 'Could not upload archive.zip.'));
                    };
                    xhr.onerror = () => reject(new Error('The upload was interrupted. Check that TweetNook is still running and try again.'));
                    xhr.onabort = () => resolve(null);
                    xhr.send(file);
                });
                if (archive) {
                    this.setupData.archive = archive;
                    this.setupArchiveFresh = true;
                    this.setupArchiveChoice = null;
                }
            } catch (error) {
                this.setupArchiveError = error.message;
            } finally {
                this.setupUploading = false;
                this.setupUpload = null;
                this.setupUploadXhr = null;
            }
        },

        cancelSetupUpload() {
            this.setupUploadXhr?.abort();
        },

        get setupUploadPercent() {
            const upload = this.setupUpload;
            if (!upload?.total) return 0;
            return Math.min(100, Math.round((upload.loaded / upload.total) * 100));
        },

        async clearSetupArchive() {
            if (!await this.askSetupConfirm({
                title: 'Remove archive.zip?',
                body: 'The uploaded file is deleted. Imported tweets stay in your archive.',
                confirmLabel: 'Remove',
                destructive: true,
            })) return;
            this.setupArchiveError = '';
            try {
                const response = await fetch('/api/setup/archive', { method: 'DELETE' });
                if (!response.ok) {
                    const data = await response.json().catch(() => ({}));
                    throw new Error(data.detail || 'Could not remove archive.zip');
                }
                this.setupData.archive = await response.json();
                this.setupArchiveFresh = false;
            } catch (error) {
                this.setupArchiveError = error.message;
            }
        },

        async importSetupArchive() {
            if (!this.setupData?.archive?.uploaded || this.setupStartingImport) return;
            this.setupStartingImport = true;
            this.setupArchiveError = '';
            this.activityError = null;
            try {
                const response = await fetch('/api/setup/archive/import', { method: 'POST' });
                if (!response.ok) {
                    const data = await response.json().catch(() => ({}));
                    throw new Error(data.detail || 'Could not start archive import');
                }
                this.setupArchiveFresh = false;
                this.goToSetupStep(2);
                await this.fetchSetup();
                await this.fetchActivityStatus();
            } catch (error) {
                this.activityError = error.message;
                this.setupArchiveError = error.message;
            } finally {
                this.setupStartingImport = false;
            }
        },

        formatSetupBytes(value) {
            return this.formatActivityBytes(value);
        },
        
        isFieldVisible(section, key) {
            const fullKey = `${section}.${key}`;
            if (!this.configSchema || !this.configData) return false;
            if (this.configSchema.blacklist.includes(fullKey)) return false;
            if (this.configSchema.whitelist.includes(fullKey)) return true;
            return this.showAdvancedConfig;
        },
        
        hasVisibleFields(section) {
            if (!this.configSchema || !this.configData || !this.configData[section]) return false;
            return Object.keys(this.configData[section]).some(k => this.isFieldVisible(section, k));
        },
        
        getFieldType(section, key) {
            if (!this.configSchema || !this.configData) return 'text';
            const fullKey = `${section}.${key}`;
            if (this.configSchema.types && this.configSchema.types[fullKey]) return this.configSchema.types[fullKey];
            
            const val = this.configData[section][key];
            if (typeof val === 'boolean') return 'boolean';
            if (typeof val === 'number') return 'number';
            return 'text';
        },
        
        isFieldFullWidth(section, key) {
            const fullKey = `${section}.${key}`;
            if (this.configSchema && this.configSchema.full_width && this.configSchema.full_width.includes(fullKey)) return true;
            return false;
        },

        isConfigExplicit(section, key) {
            return this.configExplicit.includes(`${section}.${key}`);
        },

        changedConfigValues() {
            const changes = {};
            if (!this.configData || !this.configOriginal) return changes;
            Object.entries(this.configData).forEach(([section, fields]) => {
                Object.entries(fields).forEach(([key, value]) => {
                    const original = this.configOriginal[section][key];
                    if (JSON.stringify(value) !== JSON.stringify(original)) {
                        changes[`${section}.${key}`] = value;
                    }
                });
            });
            return changes;
        },

        async submitConfigChanges(changes) {
            if (Object.keys(changes).length === 0) return true;
            this.configSaving = true;
            try {
                const res = await fetch('/api/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ changes })
                });
                if (!res.ok) {
                    alert("Failed to save config: " + await res.text());
                    return false;
                }
                const config = await res.json();
                if ('web.fetch_avatars' in changes) {
                    avatarRevision = Date.now().toString(36);
                    try { localStorage.setItem(AVATAR_REVISION_KEY, avatarRevision); } catch (_) { /* Storage can be disabled. */ }
                }
                this.configData = JSON.parse(JSON.stringify(config.values));
                this.configOriginal = JSON.parse(JSON.stringify(config.values));
                this.configExplicit = config.explicit;
                return true;
            } catch (e) {
                console.error("Error saving config", e);
                alert("Error saving config.");
                return false;
            } finally {
                this.configSaving = false;
            }
        },
        
        async saveConfig() {
            const changes = this.changedConfigValues();
            if (Object.keys(changes).length === 0) return;
            if (await this.submitConfigChanges(changes)) {
                const btn = document.getElementById('save-config-btn');
                if (btn) {
                    const old = btn.innerHTML;
                    btn.innerHTML = "Saved!";
                    btn.classList.replace('bg-[var(--accent-color)]', 'bg-green-600');
                    setTimeout(() => {
                        btn.innerHTML = old;
                        btn.classList.replace('bg-green-600', 'bg-[var(--accent-color)]');
                    }, 2000);
                }
            }
        },

        async resetConfigField(section, key) {
            await this.submitConfigChanges({ [`${section}.${key}`]: null });
        },

        async restoreConfigDefaults() {
            if(!confirm("Are you sure you want to restore default configuration settings? Your credentials and keys will be preserved.")) return;
            const preserved = new Set(['tagging.api_key', 'web.password_hash']);
            const resettable = this.configExplicit.filter(path => (
                !path.startsWith('auth.') && !preserved.has(path)
            ));
            const changes = Object.fromEntries(resettable.map(path => [path, null]));
            await this.submitConfigChanges(changes);
        },


        expandedTweets: {},
        
    };
    // Feature files each return a group of the component's methods. Copying property
    // descriptors, unlike spreading, keeps getters live.
    for (const part of [feedVideoMethods(), feedCardMethods(), tweetTextMethods(), tweetMediaMethods(), tweetMetaMethods()]) {
        Object.defineProperties(app, Object.getOwnPropertyDescriptors(part));
    }
    return app;
}
