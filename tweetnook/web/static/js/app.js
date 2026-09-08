/**
 * Main Alpine.js application for the TweetNook Web UI.
 */

const statsSuspendedVideos = new Set();

function tweetNookSyncGifIndicator(video) {
    const indicator = video?.parentElement?.querySelector?.('[data-gif-toggle]');
    if (!indicator) return;

    const isPaused = Boolean(video.paused || video.ended);
    indicator.classList.toggle('is-paused', isPaused);
    const action = isPaused ? 'Play GIF' : 'Pause GIF';
    indicator.setAttribute('aria-label', action);
    indicator.setAttribute('title', action);
}

function tweetNookToggleGif(event, indicator) {
    event?.preventDefault?.();
    event?.stopPropagation?.();

    const video = indicator?.parentElement?.querySelector?.('video[data-animated-gif]');
    if (!video) return;
    if (video.paused || video.ended) {
        const playResult = video.play();
        if (playResult?.catch) playResult.catch(() => tweetNookSyncGifIndicator(video));
    } else {
        video.pause();
    }
}

window.tweetNookSyncGifIndicator = tweetNookSyncGifIndicator;
window.tweetNookToggleGif = tweetNookToggleGif;

function tweetApp() {
    return {
        isDemo: window.TWEETNOOK_DEMO === true,
        viewMode: 'list',
        activeRoute: { viewMode: 'list' },
        pendingRoute: null,
        pendingRouteState: null,
        navigationDepth: 0,
        routeUsesPanel: false,
        tweets: [],
        loading: true,
        loadingMore: false,
        page: 1,
        totalPages: 1,
        total: 0,
        hasMore: false,
        nextCursor: null,
        feedRequestId: 0,
        randomSeed: null,
        collectionFilter: 'likes',
        sortOrder: 'liked_latest',
        feedMenu: null,
        feedMenuTrigger: null,

        feedOptions(kind) {
            if (kind === 'collection') return [
                { value: 'all', label: 'All Tweets' },
                { value: 'likes', label: 'Likes' },
                { value: 'bookmarks', label: 'Bookmarks' },
                { value: 'tweets', label: 'Your Tweets' },
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
            ];
        },
        feedValue(kind) {
            return kind === 'collection' ? this.collectionFilter : this.sortOrder;
        },
        feedLabel(kind) {
            return this.feedOptions(kind).find(option => option.value === this.feedValue(kind))?.label || 'Relevance';
        },
        resultCountLabel() {
            if (this.searchQuery.trim() && this.total === null) return 'Results';
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
        error: null,
        showScrollTop: false,

        threadData: null,
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
        lightboxIndex: 0,
        videoObserver: null,

        // Split panel state
        splitPanel: false,
        panelStack: [],
        panelMode: null,        // 'thread' | 'quotes' | null
        panelThreadData: null,
        panelLoadingThread: false,
        panelQuotesTweetId: null,
        panelQuotesList: [],
        panelQuotesPage: 1,
        panelQuotesTotalPages: 1,
        panelQuotesLoading: false,
        panelQuotesLoadingMore: false,

        tweetMenuOpen: null,
        tweetMenuPressTimer: null,
        tweetMenuClickBlockedUntil: 0,

        tagModalOpen: false,
        tagModalData: null,
        tagModalTweetId: null,
        
        isEditingTags: false,
        editableTags: [],
        editableDescription: '',
        tagSearchQuery: '',
        tagAutocompleteOptions: [],
        tagSearchDropdown: false,
        
        showKeyboardShortcuts: false,
        profileCard: null,
        activity: null,
        lastActivity: null,
        activitySchedule: { configured: false, relative: 'Not configured', date: 'Use cron or a service timer' },
        activityStartPending: false,
        activityStartingKind: null,
        activityError: null,
        activityDrawerOpen: false,
        showLogModal: false,

        get displayActivity() {
            return this.activity || this.lastActivity;
        },

        async fetchActivityStatus() {
            try {
                const response = await fetch('/api/activity/status');
                if (!response.ok) throw new Error('Could not load activity status');
                const data = await response.json();
                this.activitySchedule = data.schedule || this.activitySchedule;
                if (data.active) {
                    this.activity = data.snapshot;
                    this.activityStartPending = false;
                    this.activityStartingKind = null;
                    this.activityError = null;
                } else {
                    this.lastActivity = data.last_snapshot || this.activity || this.lastActivity;
                    this.activity = null;
                }
            } catch (error) {
                this.activityError = error.message;
            }
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

        formatLogText(text) {
            if (!text) return 'No log output.';
            return text.replace(/[<>]/g, c => c === '<' ? '&lt;' : '&gt;').split('\n').map(line => {
                if (line.includes('ERROR:')) return `<span style="color: var(--danger-color)">${line}</span>`;
                if (line.includes('WARNING:')) return `<span style="color: var(--accent-color)">${line}</span>`;
                if (line.includes('failed')) return `<span style="color: var(--danger-color)">${line}</span>`;
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
            this.activityError = null;
            try {
                const response = await fetch(`/api/activity/${kind}`, { method: 'POST' });
                if (!response.ok) {
                    const data = await response.json().catch(() => ({}));
                    throw new Error(data.detail || 'Could not start sync');
                }
                await new Promise(resolve => setTimeout(resolve, 250));
                await this.fetchActivityStatus();
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
                await this.fetchActivityStatus();
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

        activityPercent(step) {
            if (!step || !step.total) return 0;
            return Math.max(0, Math.min(100, step.completed / step.total * 100));
        },

        activitySteps() {
            return this.displayActivity?.steps || [];
        },

        activityCompletedSteps() {
            return this.activitySteps().filter(step =>
                step.state === 'complete' || step.state === 'skipped' || step.state === 'failed'
            );
        },

        activityActiveStep() {
            return this.activitySteps().find(step => step.state === 'active') || null;
        },

        activityHasActiveStep() {
            return this.activityActiveStep() !== null;
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

        activityCompletedStackHeight() {
            // Completed cards are intentionally fixed-height so the stack can grow
            // upward without changing the active slot's layout position.
            return `${this.activityCompletedSteps().length * 64}px`;
        },

        reverseActivityScroll(event) {
            const viewport = event?.currentTarget;
            if (!viewport) return;
            // Completed history uses the browser's normal scroll direction.
            if (!this.activityHasActiveStep()) return;
            event.preventDefault?.();
            // This is direct manual input, not automatic progression scrolling.
            // Wheel-up pulls the scene down; wheel-down returns it upward.
            const deltaScale = event.deltaMode === 1
                ? 16
                : (event.deltaMode === 2 ? viewport.clientHeight : 1);
            const delta = (Number(event.deltaY) || 0) * deltaScale;
            const maxScroll = Math.max(viewport.scrollHeight - viewport.clientHeight, 0);
            viewport.scrollTop = Math.max(0, Math.min(viewport.scrollTop - delta, maxScroll));
            this.setActivityScrollPosition({ currentTarget: viewport });
        },

        setActivityScrollPosition(event) {
            const viewport = event?.currentTarget;
            if (!viewport?.style) return;
            // Native scrolling moves the canvas upward by one unit. Moving the
            // connected scene downward by two produces a net one-unit downward
            // pull, revealing completed cards from beneath the controls.
            const scrollTop = Math.max(Number(viewport.scrollTop) || 0, 0);
            const sceneShift = scrollTop * 2;
            const fadeTravel = 64;
            const fadeProgress = Math.min(scrollTop / fadeTravel, 1);
            const fadeOpacity = 1 - fadeProgress;
            const fadeShift = -Math.min(scrollTop, fadeTravel);
            viewport.style.setProperty('--activity-scroll-shift', `${sceneShift}px`);
            const controls = viewport.closest?.('.activity-drawer-body')
                ?.querySelector('.activity-drawer-controls');
            controls?.style?.setProperty('--activity-fade-opacity', `${fadeOpacity}`);
            controls?.style?.setProperty('--activity-fade-shift', `${fadeShift}px`);
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
            let x = rect.left;
            let y = rect.bottom + 8;

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
                x,
                y,
                name,
                username,
                id,
                initial: name.charAt(0).toUpperCase(),
                description: legacy.description || '',
                followersCount: legacy.followers_count || 0,
                followingCount: legacy.friends_count || 0,
                syncDate: tweet.synced_at || (tweet.collection ? tweet.collection.synced_at : null) || raw.synced_at || null
            };
            
            this.$nextTick(() => {
                const card = document.getElementById('profile-card-modal');
                if (card) {
                    const cardRect = card.getBoundingClientRect();
                    if (this.profileCard.x + cardRect.width > window.innerWidth) {
                        this.profileCard.x = window.innerWidth - cardRect.width - 16;
                    }
                    if (this.profileCard.y + cardRect.height > window.innerHeight) {
                        this.profileCard.y = rect.top - cardRect.height - 8;
                    }
                }
            });

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
        setupFlowStep: 1,
        setupDirection: 'forward',
        setupSkipAuth: false,
        setupPassword: '',
        setupPasswordConfirm: '',
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
        setupStartingImport: false,
        
        mergePrimaryTag: '',
        mergeTagsList: [],
        mergeSearchTerm: '',
        mergePrimarySearchTerm: '',
        mergePrimaryDropdown: false,
        mergeTagsDropdown: false,
        mergeSelectedIndex: 0,
        primarySelectedIndex: 0,
        
        lastSyncAt: null,
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
        currentTheme: 'classic-dark',
        currentAccent: null,

        fontFamily: 'system',
        installedFonts: [],
        fontDiscoveryBusy: false,
        fontDiscoveryMessage: '',
        fontPickerOpen: false,
        fontSize: 'default',

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
            if (savedTheme && THEMES[savedTheme]) {
                this.applyTheme(savedTheme);
            } else if (savedTheme === 'light') {
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
            });
            
            const savedFontSize = localStorage.getItem('tvx-font-size');
            const savedFontSizePx = localStorage.getItem('tvx-font-size-px');
            if (savedFontSize && savedFontSizePx) {
                this.fontSize = savedFontSize;
                document.documentElement.style.setProperty('--font-size-base', savedFontSizePx);
            }
            
            // Restore split panel preference
            this.splitPanel = localStorage.getItem('tvx-split-panel') === 'true';
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

            window.addEventListener('popstate', (event) => {
                this.pauseAllVideos();
                const route = this.routeFromHistoryState(event.state)
                    || this.parseRoute(window.location.pathname);
                if (route) return this.applyRoute(route, { fromPopState: true, state: event.state });
            });
            window.addEventListener('resize', () => {
                const usePanel = this.shouldUseSplitPanel();
                if (usePanel === this.routeUsesPanel) return;
                this.routeUsesPanel = usePanel;
                if (this.activeRoute.viewMode !== 'list' && this.archiveResourcesLoaded) {
                    this.applyRoute(this.activeRoute, { state: history.state });
                }
            });

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
                    this.fetchGlobalTags(),
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
                this.fetchSetup();
            }
            setInterval(() => {
                if (!this.isDemo) this.fetchSetup();
                this.fetchNotices();
            }, 3000);
            setInterval(() => { if (this.archiveResourcesLoaded) this.fetchArchiveEnrichmentStatus(); }, 60000);
            setInterval(() => { this.statsAgeNow = Date.now(); }, 30000);
            this.fetchActivityStatus();
            this.activityPollTimer = setInterval(() => this.fetchActivityStatus(), 1000);

            this.$watch('showStatsModal', val => {
                if (val) this.lockModalScroll();
                else if (!this.showSettingsModal && !this.showSetupModal) this.unlockModalScroll();
            });
            this.$watch('showSettingsModal', val => {
                if (val) {
                    this.lockModalScroll();
                    if (!this.isDemo) this.fetchSetup();
                    if (!this.setupForced) {
                        if (!this.isDemo) this.fetchConfig();
                        if (this.archiveReady) this.fetchScheduleSettings();
                        if (!this.isDemo) this.fetchActivityRuns();
                        this.fetchAutomatedTagging();
                    }
                } else {
                    this.showAutomatedTaggingSpendModal = false;
                    if (!this.showSetupModal) this.unlockModalScroll();
                }
            });
            this.$watch('showSetupModal', val => {
                if (val) this.lockModalScroll();
                else if (!this.showSettingsModal) this.unlockModalScroll();
            });

            window.addEventListener('open-lightbox', (e) => {
                this.lightboxMedia = e.detail.media;
                this.lightboxIndex = e.detail.index;
                this.lightboxOpen = true;
            });
            
            this.videoObserver = new IntersectionObserver((entries) => {
                entries.forEach(e => {
                    if (e.target.tagName === 'VIDEO') {
                        if (!e.isIntersecting) {
                            e.target.pause();
                        } else if (e.target.hasAttribute('autoplay')) {
                            e.target.play().catch(() => {});
                        }
                    }
                });
            }, { threshold: 0.1 });

            const domObserver = new MutationObserver((mutations) => {
                mutations.forEach(m => {
                    m.addedNodes.forEach(node => {
                        if (node.nodeType === 1) {
                            if (node.tagName === 'VIDEO') this.videoObserver.observe(node);
                            node.querySelectorAll('video').forEach(v => this.videoObserver.observe(v));
                        }
                    });
                });
            });
            domObserver.observe(document.body, { childList: true, subtree: true });
            
        },

        pauseAllVideos() {
            document.querySelectorAll('video').forEach(v => {
                v.pause();
                v.currentTime = 0;
            });
        },

        prevLightbox() {
            if (this.lightboxMedia.length < 2) return;
            this.lightboxIndex = Math.max(0, this.lightboxIndex - 1);
        },

        nextLightbox() {
            if (this.lightboxMedia.length < 2) return;
            this.lightboxIndex = Math.min(this.lightboxMedia.length - 1, this.lightboxIndex + 1);
        },

        scrollToTop() {
            window.scrollTo({ top: 0, behavior: 'smooth' });
        },

        async fetchTweets(append = false) {
            if (this.archiveReady === false || this.setupForced) return;
            const requestId = ++this.feedRequestId;
            const requestedPage = append ? this.page + 1 : 1;
            if (!append) {
                this.loading = true;
                this.tweets = [];
                this.hasMore = false;
                this.nextCursor = null;
                window.scrollTo(0, 0);
            } else {
                this.loadingMore = true;
            }
            
            let url = `/api/tweets?collection=${this.collectionFilter}&sort=${this.sortOrder}&page=${requestedPage}`;
            if (this.sortOrder === 'random') {
                if (this.randomSeed === null) this.randomSeed = Math.floor(Math.random() * 2147483648);
                url += `&random_seed=${this.randomSeed}`;
            }
            if (this.searchQuery.trim()) {
                url += `&q=${encodeURIComponent(this.searchQuery)}`;
            }
            if (!append || this.nextCursor !== null) {
                url += `&cursor=${encodeURIComponent(this.nextCursor || '')}`;
            }

            try {
                this.error = null;
                const res = await fetch(url);

                let data;
                try {
                    data = await res.json();
                } catch (parseError) {
                    throw new Error(`Invalid server response (${res.status}): Please check server logs.`);
                }

                if (requestId !== this.feedRequestId) return;
                if (res.status === 409 && append && this.nextCursor) {
                    // A new sync changed derived like order. Start a fresh list.
                    return await this.fetchTweets();
                }
                if (!res.ok) {
                    throw new Error(data.detail || `Server error: ${res.status}`);
                }

                if (append) {
                    const seen = new Set(this.tweets.map(tweet => tweet.tweet_id));
                    this.tweets = [...this.tweets, ...data.tweets.filter(tweet => {
                        if (seen.has(tweet.tweet_id)) return false;
                        seen.add(tweet.tweet_id);
                        return true;
                    })];
                } else {
                    this.tweets = data.tweets;
                }
                this.page = data.page;
                this.totalPages = data.pages;
                this.total = data.total;
                this.hasMore = typeof data.has_more === 'boolean'
                    ? data.has_more : data.page < data.pages;
                this.nextCursor = data.next_cursor || null;
            } catch (e) {
                if (requestId !== this.feedRequestId) return;
                console.error(e);
                this.error = e.message;
            } finally {
                if (requestId === this.feedRequestId) {
                    this.loading = false;
                    this.loadingMore = false;
                }
            }
        },
        
        toggleTheme() {
            // Legacy — handled by applyTheme
        },

        applyTheme(key) {
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

            localStorage.setItem('tvx-theme', key);
        },

        setAccent(accent) {
            this.currentAccent = accent.color;
            const root = document.documentElement;
            root.style.setProperty('--accent-color', accent.color);
            root.style.setProperty('--accent-hover', accent.hover);
            root.style.setProperty('--accent-text', accent.text);
            localStorage.setItem('tvx-accent-' + this.currentTheme, accent.color);
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
            if (this.panelMode === 'thread') top.data = this.panelThreadData;
            else if (this.panelMode === 'quotes') top.data = this.panelQuotesList;
        },

        saveCurrentRouteState() {
            const route = this.currentRoute();
            if (route.viewMode === 'list') {
                return this.replaceRoute(route, { scrollY: window.scrollY });
            }
            if (this.shouldUseSplitPanel()) this.savePanelSnapshot();
            return this.replaceRoute(route, { scrollY: window.scrollY });
        },

        async applyRoute(route, { fromPopState = false, state = null } = {}) {
            if (!route) return false;
            const routeState = state || this.historyStateForRoute(route);
            if (state && Number.isInteger(routeState.tweetNookDepth) && routeState.tweetNookDepth >= 0) {
                this.navigationDepth = routeState.tweetNookDepth;
            } else if (fromPopState) {
                this.navigationDepth = 0;
            }
            this.activeRoute = { ...route };

            if (!this.archiveResourcesLoaded) {
                this.pendingRoute = { ...route };
                this.pendingRouteState = routeState;
                return false;
            }

            this.pendingRoute = null;
            this.pendingRouteState = null;
            this.routeUsesPanel = this.shouldUseSplitPanel();
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
            if (route.viewMode === 'thread') await this.showFullThread(route.tweetId);
            else await this.showFullQuotes(route.quotesTweetId);
            return true;
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
                    this.panelLoadingThread = false;
                    return;
                }
                this.panelLoadingThread = true;
                this.panelThreadData = null;
                try {
                    const data = await this.loadThread(route.tweetId);
                    entry.data = data;
                    if (this.panelStack.at(-1) === entry) this.panelThreadData = data;
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

        async showFullThread(tweetId) {
            this.viewMode = 'thread';
            this.loadingThread = true;
            this.threadData = null;
            window.scrollTo(0, 0);
            try {
                const data = await this.loadThread(tweetId);
                if (this.routeKey(this.activeRoute) === `thread:${tweetId}` && !this.shouldUseSplitPanel()) {
                    this.threadData = data;
                }
            } catch (error) {
                console.error(error);
                if (this.routeKey(this.activeRoute) === `thread:${tweetId}`) this.goBack();
            } finally {
                this.loadingThread = false;
            }
        },

        async showFullQuotes(tweetId) {
            this.viewMode = 'quotes';
            this.quotesTweetId = tweetId;
            this.quotesPage = 1;
            this.quotesList = [];
            this.quotesLoading = true;
            window.scrollTo(0, 0);
            await this.fetchQuotes();
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

        async loadThread(tweetId) {
            const cached = this.threadCache.get(tweetId);
            if (cached && Date.now() - cached.loadedAt < this.threadCacheTtlMs) {
                return cached.request;
            }

            const request = (async () => {
                const res = await fetch(`/api/tweets/${tweetId}`);
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
            this.saveCurrentRouteState();
            const state = this.pushRoute(route);
            return this.applyRoute(route, { state });
        },
        
        async openQuotes(tweetId, fromPanel = false) {
            void fromPanel;
            if (typeof tweetId !== 'string' || !/^\d+$/.test(tweetId)) return;
            const route = { viewMode: 'quotes', quotesTweetId: tweetId };
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
        
        goBack() {
            this.pauseAllVideos();
            this.savePanelSnapshot();
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

        async fetchStatsSnapshot(revalidate = true) {
            const showSkeletons = !this.statsGeneratedAt;
            if (showSkeletons) this.setStatsLoading(true);
            try {
                const res = await fetch(`/api/stats/snapshot?revalidate=${revalidate}`);
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
                    await this.fetchStatsSnapshot(false);
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

        getUnavailableReasons() {
            const reasons = this.statsHealth?.enrichment?.unavailable?.reasons || [];
            return this.showEmptyUnavailableReasons
                ? reasons
                : reasons.filter(reason => (reason.count || 0) > 0);
        },

        getUnavailableBarReasons() {
            const reasons = this.statsHealth?.enrichment?.unavailable?.reasons || [];
            return reasons.filter(reason => (reason.count || 0) > 0);
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

        fetchArchiveEnrichmentStatus() {
            fetch('/api/stats/enrichment-incomplete')
                .then(r => r.json())
                .then(d => {
                    this.archiveEnrichmentIncomplete = d.incomplete || 0;
                })
                .catch(e => console.error('Failed to fetch archive enrichment status', e));
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

        updateSyncTime() {
            if (!this.lastSyncAt) return;
            
            const now = new Date();
            const diffMs = now - this.lastSyncAt;
            const diffMins = Math.floor(diffMs / 60000);
            const diffHours = Math.floor(diffMins / 60);
            const diffDays = Math.floor(diffHours / 24);
            
            let relativeStr = '';
            if (diffMins < 1) relativeStr = 'Just now';
            else if (diffMins < 60) relativeStr = `${diffMins}m ago`;
            else if (diffHours < 24) relativeStr = `${diffHours}h ago`;
            else relativeStr = `${diffDays}d ago`;
            
            const options = { year: 'numeric', month: 'short', day: 'numeric' };
            const dateStr = this.lastSyncAt.toLocaleDateString(undefined, options);
            
            this.lastSyncFormatted = `Last sync: ${dateStr} · ${relativeStr}`;
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
            if (tweet.author.display_name) return tweet.author.display_name.charAt(0);
            if (tweet.author.username) return tweet.author.username.charAt(0);
            return '?';
        },

        toggleTweetMenu(menuKey) {
            if (Date.now() < this.tweetMenuClickBlockedUntil) return;
            this.tweetMenuOpen = this.tweetMenuOpen === menuKey ? null : menuKey;
        },

        closeTweetMenu(menuKey = null) {
            if (!menuKey || this.tweetMenuOpen === menuKey) this.tweetMenuOpen = null;
        },

        startTweetMenuPress(event, tweetId, tagsData) {
            if (event?.pointerType === 'mouse' && event.button !== 0) return;
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
            if (!tweet?.raw_json) {
                alert('No raw tweet data is available.');
                return;
            }
            const rawText = JSON.stringify(tweet.raw_json, null, 2);
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
            this.tagModalOpen = true;
            document.body.style.overflow = 'hidden';
        },
        
        closeTagModal() {
            this.tagModalOpen = false;
            this._tagModalCloseTimer = setTimeout(() => {
                this.tagModalData = null;
                this.tagModalTweetId = null;
                this.isEditingTags = false;
                this.editableTags = [];
                this.editableDescription = '';
                this.tagSearchDropdown = false;
                document.body.style.overflow = '';
                this._tagModalCloseTimer = null;
            }, 300);
        },

        updateTweetTagData(tweetId, tagData) {
            const assign = tweet => {
                if (!tweet || tweet.tweet_id !== tweetId) return;
                tweet.media_tags = tagData ? {
                    description: tagData.description,
                    tags: [...tagData.tags],
                } : null;
            };
            const visitThread = thread => {
                if (!thread) return;
                assign(thread.main);
                for (const tweet of thread.parents || []) assign(tweet);
                for (const tweet of thread.children || []) {
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
        
        async deleteTags() {
            if (!this.tagModalTweetId || !confirm("Are you sure you want to delete the tags for this tweet?")) return;
            try {
                const res = await fetch(`/api/tags/${this.tagModalTweetId}`, { method: 'DELETE' });
                if (res.ok) {
                    this.updateTweetTagData(this.tagModalTweetId, null);
                    this.closeTagModal();
                } else {
                    alert("Failed to delete tags.");
                }
            } catch (e) {
                alert("Error: " + e.message);
            }
        },
        
        startEditingTags() {
            this.isEditingTags = true;
            this.editableTags = [...(this.tagModalData?.tags || [])];
            this.editableDescription = this.tagModalData?.description || '';
        },
        async fetchTagAutocomplete(query) {
            try {
                const res = await fetch(`/api/tags/autocomplete?q=${encodeURIComponent(query)}`);
                if(res.ok) {
                    const data = await res.json();
                    this.tagAutocompleteOptions = data.tags;
                }
            } catch(e) {}
        },
        addEditableTag(tag) {
            const trimmed = tag.trim();
            if(trimmed && !this.editableTags.find(t => t.toLowerCase() === trimmed.toLowerCase())) {
                this.editableTags.push(trimmed);
            }
            this.tagSearchQuery = '';
        },
        async saveTags() {
            try {
                const description = this.editableDescription.trim();
                const res = await fetch(`/api/tags/${this.tagModalTweetId}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ tags: this.editableTags, description })
                });
                if (res.ok) {
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
                } else {
                    alert("Failed to save tags.");
                }
            } catch(e) {
                alert("Error saving tags: " + e.message);
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

        getReplyTo(tweet) {
            if (!tweet || !tweet.raw_json) return null;
            let raw = tweet.raw_json.raw_json || tweet.raw_json;
            if (raw.legacy && raw.legacy.in_reply_to_screen_name) return raw.legacy.in_reply_to_screen_name;
            if (raw.in_reply_to_screen_name) return raw.in_reply_to_screen_name;
            return null;
        },

        formatDate(dateStr, includeTime = false) {
            if (!dateStr) return '';
            const d = new Date(dateStr);
            if (includeTime) {
                return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }) + ' · ' + 
                       d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
            }
            const now = new Date();
            const diff = (now - d) / 1000;
            if (diff < 60) return 'Just now';
            if (diff < 3600) return Math.floor(diff/60) + 'm';
            if (diff < 86400) return Math.floor(diff/3600) + 'h';
            const options = { month: 'short', day: 'numeric' };
            if (d.getFullYear() !== now.getFullYear()) options.year = 'numeric';
            return d.toLocaleDateString('en-US', options);
        },
        
        formatRelativeDate(dateStr) {
            if (!dateStr) return '';
            const d = new Date(dateStr);
            const now = new Date();
            const diff = (now - d) / 1000;
            if (diff < 60) return 'just now';
            if (diff < 3600) {
                const m = Math.floor(diff/60);
                return m === 1 ? '1 min ago' : m + ' mins ago';
            }
            if (diff < 86400) {
                const h = Math.floor(diff/3600);
                return h === 1 ? '1 hr ago' : h + ' hrs ago';
            }
            const days = Math.floor(diff/86400);
            if (days < 30) return days === 1 ? '1 day ago' : days + ' days ago';
            if (days < 365) {
                const mo = Math.floor(days/30);
                return mo === 1 ? '1 mo ago' : mo + ' mos ago';
            }
            const y = Math.floor(days/365);
            return y === 1 ? '1 yr ago' : y + ' yrs ago';
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
            if (this.isDemo) return;
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
            this.showSettingsModal = false;
        },

        openSettings() {
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
            if (this.setupForced) return;
            this.setupRerun = false;
            this.showSetupModal = false;
        },

        openSetup() {
            if (this.isDemo) return;
            if (this.setupData?.completed) this.setupRerun = true;
            this.showSetupModal = true;
            this.showSettingsModal = false;
            this.fetchSetup();
        },

        goToSetupStep(step) {
            const next = Number(step);
            if (![1, 2, 3].includes(next) || next === this.setupFlowStep) return;
            this.setupDirection = next < this.setupFlowStep ? 'back' : 'forward';
            this.setupFlowStep = next;
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
            if (archive.enrichment_state === 'running') return 'Archive enrichment is running in the background.';
            if (archive.enrichment_state === 'waiting_for_auth') return 'Local import complete. Connect Twitter/X to start enrichment.';
            if (archive.enrichment_state === 'waiting_for_worker') return 'Enrichment will start when the active task finishes.';
            if (archive.ready) return 'Your archive is ready to browse.';
            return 'Start by importing your official Twitter/X archive.';
        },

        async fetchSetup() {
            if (this.isDemo) return;
            if (this.setupPolling || this.setupFinishing) return;
            this.setupPolling = true;
            try {
                const response = await fetch('/api/setup');
                if (!response.ok) throw new Error('Could not load setup');
                const data = await response.json();
                this.setupData = data;
                if (!this.setupAuth) this.setupAuth = JSON.parse(JSON.stringify(data.auth.values));
                this.archiveReady = !!data.archive.ready;
                this.setupForced = !!data.required;
                if (this.setupForced) {
                    this.showSetupModal = true;
                    this.showSettingsModal = false;
                }
                if (this.archiveReady && !this.setupForced && !this.archiveResourcesLoaded) {
                    this.archiveResourcesLoaded = true;
                    await Promise.all([this.fetchTweets(), this.fetchStats(), this.fetchGlobalTags(),
                        this.fetchArchiveEnrichmentStatus(), this.fetchAutomatedTagging()]);
                    const pendingRoute = this.pendingRoute;
                    const pendingRouteState = this.pendingRouteState;
                    if (pendingRoute) {
                        this.pendingRoute = null;
                        this.pendingRouteState = null;
                        await this.applyRoute(pendingRoute, { state: pendingRouteState });
                    }
                }
            } catch (error) {
                this.setupError = error.message;
            } finally {
                this.setupPolling = false;
            }
        },

        async initializeSetupArchive(mode) {
            if (mode === 'empty' && !confirm('Importing your official Twitter/X archive first is recommended. A live sync may not recover all of your historical data. Create an empty archive?')) return;
            this.setupError = '';
            try {
                const response = await fetch(mode === 'empty' ? '/api/setup/archive/empty' : '/api/setup/migrate', { method: 'POST' });
                const data = await response.json();
                if (!response.ok) throw new Error(data.detail?.message || data.detail || 'Could not initialize archive');
                this.goToSetupStep(2);
                await this.fetchSetup();
                await this.fetchActivityStatus();
            } catch (error) { this.setupError = error.message; }
        },

        async finishSetup() {
            if (this.setupFinishing) return;
            this.setupFinishing = true;
            this.setupError = '';
            try {
                const body = { skip_auth: this.setupSkipAuth };
                if (!this.setupData?.web?.password_configured || this.setupPassword) {
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
                if (data.reload) window.location.reload();
                else this.closeSetup();
            } catch (error) { this.setupError = error.message; }
            finally { this.setupFinishing = false; }
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

        openNotice(notice) {
            if (notice.action === 'activity') {
                this.activityDrawerOpen = true;
                return;
            }
            if (notice.action === 'schedule') {
                this.settingsTab = 'schedule';
                this.fetchScheduleSettings();
            } else {
                this.openSetup();
                return;
            }
            this.openSettings();
        },

        async saveSetupAuth() {
            if (!this.setupAuth || this.setupSavingAuth || this.setupAuthSuccessPending) return false;
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
                    throw new Error(data.detail || 'Could not save authentication');
                }

                const auth = await saveResponse.json();
                this.setupData.auth = auth;
                this.setupAuth = JSON.parse(JSON.stringify(auth.values));
                this.setupAuthSaved = !!auth.verified;
                this.setupSkipAuth = false;
                await this.fetchSetup();
                if (this.setupData.auth.verified) {
                    this.setupAuthResult = {
                        success: true,
                        message: this.setupData.archive.enrichment_state === 'running'
                            ? 'Connected successfully. Archive enrichment has started.'
                            : 'Connected successfully. Archive enrichment will start when the local import finishes.',
                    };
                    this.setupSavingAuth = false;
                    this.setupAuthSuccessPending = true;
                    await new Promise(resolve => setTimeout(resolve, 1000));
                    this.setupAuthSuccessPending = false;
                    this.goToSetupStep(3);
                    return true;
                }
                throw new Error('Authentication was not verified');

            } catch (error) {
                this.setupAuthResult = { success: false, message: 'Authentication failed, verify cookies', output: '' };
                return false;
            } finally {
                this.setupSavingAuth = false;
            }
        },

        async uploadSetupArchive(event) {
            const file = event.target.files?.[0];
            event.target.value = '';
            if (!file) return;
            if (!file.name.toLowerCase().endsWith('.zip')) {
                alert('Choose an archive.zip file.');
                return;
            }
            this.setupUploading = true;
            try {
                const response = await fetch('/api/setup/archive', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/zip' },
                    body: file,
                });
                if (!response.ok) {
                    const data = await response.json().catch(() => ({}));
                    throw new Error(data.detail || 'Could not upload archive');
                }
                this.setupData.archive = await response.json();
            } catch (error) {
                alert(error.message);
            } finally {
                this.setupUploading = false;
            }
        },

        async clearSetupArchive() {
            if (!confirm('Clear the uploaded archive.zip? Imported tweets will not be deleted.')) return;
            try {
                const response = await fetch('/api/setup/archive', { method: 'DELETE' });
                if (!response.ok) {
                    const data = await response.json().catch(() => ({}));
                    throw new Error(data.detail || 'Could not clear archive');
                }
                this.setupData.archive = await response.json();
            } catch (error) {
                alert(error.message);
            }
        },

        async importSetupArchive() {
            if (!this.setupData?.archive?.uploaded || this.setupStartingImport) return;
            this.setupStartingImport = true;
            this.activityError = null;
            try {
                const response = await fetch('/api/setup/archive/import', { method: 'POST' });
                if (!response.ok) {
                    const data = await response.json().catch(() => ({}));
                    throw new Error(data.detail || 'Could not start archive import');
                }
                this.goToSetupStep(2);
                await this.fetchSetup();
                await this.fetchActivityStatus();
            } catch (error) {
                this.activityError = error.message;
                alert(error.message);
            } finally {
                this.setupStartingImport = false;
            }
        },

        formatSetupBytes(value) {
            const bytes = Number(value) || 0;
            if (bytes < 1024) return `${bytes} B`;
            const units = ['KiB', 'MiB', 'GiB', 'TiB'];
            let amount = bytes / 1024;
            let index = 0;
            while (amount >= 1024 && index < units.length - 1) {
                amount /= 1024;
                index += 1;
            }
            return `${amount.toFixed(amount >= 10 ? 1 : 2)} ${units[index]}`;
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

        formatSyncDate(tweet) {
            let synced = tweet.synced_at || (tweet.collection ? tweet.collection.synced_at : null);
            if (!synced) return 'Archived';
            let dS = new Date(synced);
            return `Synced ${dS.toLocaleDateString('en-US', {month:'short', day:'numeric'})}`;
        },

        getQuoteTweet(tweet) {
            if (!tweet || this.isPlaceholderTweet(tweet)) return null;
            if (tweet.quoted_tweet) return tweet.quoted_tweet;
            if (!tweet.raw_json) return null;
            const quote = tweet.raw_json.quoted_status_result?.result;
            if (quote?.__typename === 'TweetWithVisibilityResults') {
                if (quote.birdwatch_pivot && quote.tweet) {
                    quote.tweet.birdwatch_pivot = quote.birdwatch_pivot;
                }
                return quote.tweet;
            }
            if (quote?.__typename === 'Tweet') return quote;
            if (quote?.__typename === 'TweetTombstone' || quote?.__typename === 'TweetUnavailable') return quote;
            if (tweet.raw_json.quoted_status) return tweet.raw_json.quoted_status;
            return null;
        },

        isTombstone(qt) {
            if (!qt) return false;
            return this.isPlaceholderTweet(qt) || qt.__typename === 'TweetTombstone' || qt.__typename === 'TweetUnavailable' || qt.__tombstone__ === true;
        },

        isPlaceholderTweet(tweet) {
            const typename = tweet?.__typename || tweet?.__typename__;
            return Boolean(tweet?.availability?.placeholder)
                || typename === 'TweetTombstone'
                || typename === 'TweetUnavailable'
                || tweet?.__tombstone__ === true;
        },

        isTextPlaceholderTweet(tweet) {
            if (!tweet || this.isPlaceholderTweet(tweet)) return false;
            const raw = tweet.raw_json?.raw_json || tweet.raw_json || tweet;
            const text = tweet.text || raw?.legacy?.full_text || raw?.full_text || '';
            return Boolean(this.getTweetId(tweet)) && !String(text).trim();
        },

        renderTweetSkeleton(tweet, { compact = false, contentOnly = false } = {}) {
            const reasonKey = tweet?.availability?.reason
                || (this.isTombstone(tweet) ? 'unavailable_unknown' : 'details_not_archived');
            const fallbackMessages = {
                protected_account: 'This tweet is from a protected account.',
                suspended_account: 'This tweet is from a suspended account.',
                account_missing: 'This tweet is from an account that no longer exists.',
                deleted_by_author: 'This tweet was deleted by its author.',
                archive_deleted: 'This tweet was deleted from Twitter/X.',
                withheld: 'This tweet is unavailable in your location.',
                not_found: 'This tweet could not be found.',
                not_archived: 'This tweet was not captured in the local archive.',
                details_not_archived: 'Tweet details have not been archived yet.',
                text_not_archived: 'Tweet text was not captured in the local archive.',
                unavailable_unknown: 'This tweet is unavailable.',
            };
            const tombstoneMessage = this.isTombstone(tweet)
                ? tweet?.tombstone?.text?.text || tweet?.text
                : '';
            const message = this.escapeHTML(
                tweet?.availability?.message
                    || tombstoneMessage
                    || fallbackMessages[reasonKey]
                    || 'This tweet is unavailable.',
            );
            const reason = this.escapeHTML(
                reasonKey,
            );
            const tweetId = this.escapeHTML(this.getTweetId(tweet) || '');
            const label = compact ? 'Quoted tweet unavailable' : 'Tweet unavailable';
            const rootClasses = [
                'tweet-skeleton',
                compact ? 'tweet-skeleton-compact' : '',
                contentOnly ? 'tweet-skeleton-content' : '',
            ].filter(Boolean).join(' ');
            const meta = contentOnly ? '' : `
                <div class="tweet-skeleton-meta" aria-hidden="true">
                    <span class="tweet-skeleton-bar tweet-skeleton-name"></span>
                    <span class="tweet-skeleton-bar tweet-skeleton-handle"></span>
                    <span class="tweet-skeleton-bar tweet-skeleton-date"></span>
                </div>`;
            const lineClasses = compact
                ? ['tweet-skeleton-line-wide', 'tweet-skeleton-line-medium']
                : ['tweet-skeleton-line-wide', 'tweet-skeleton-line-medium', 'tweet-skeleton-line-short'];
            const lines = lineClasses.map(className => (
                `<span class="tweet-skeleton-bar tweet-skeleton-line ${className}"></span>`
            )).join('');
            const body = `
                <div class="tweet-skeleton-message">${message}</div>
                <div class="tweet-skeleton-lines" aria-hidden="true">${lines}</div>
                <div class="tweet-skeleton-actions" aria-hidden="true">
                    <span class="tweet-skeleton-bar tweet-skeleton-action"></span>
                    <span class="tweet-skeleton-bar tweet-skeleton-action"></span>
                    <span class="tweet-skeleton-bar tweet-skeleton-action"></span>
                    <span class="tweet-skeleton-bar tweet-skeleton-action"></span>
                </div>`;
            const avatar = contentOnly
                ? ''
                : '<span class="tweet-skeleton-avatar" aria-hidden="true"></span>';
            return `<div class="${rootClasses}" role="status" aria-label="${label}" data-tweet-skeleton="true" data-availability-reason="${reason}" data-tweet-id="${tweetId}"><span class="sr-only">${label}</span>${avatar}<div class="tweet-skeleton-main">${meta}${body}</div></div>`;
        },

        renderContentPlaceholder(message, reason = 'details_not_archived') {
            return this.renderTweetSkeleton(
                { availability: { placeholder: true, message, reason } },
                { contentOnly: true },
            );
        },

        renderTweetPlaceholder(tweet) {
            return this.renderTweetSkeleton(tweet, { contentOnly: true });
        },

        renderQuotePlaceholder(tweet) {
            return this.renderTweetSkeleton(tweet, { compact: true });
        },

        getRetweet(tweet) {
            if (!tweet || this.isPlaceholderTweet(tweet)) return null;
            if (tweet.retweeted_tweet) return tweet.retweeted_tweet;
            if (!tweet.raw_json) return null;
            const rt = tweet.raw_json.legacy?.retweeted_status_result?.result;
            if (rt?.__typename === 'TweetWithVisibilityResults') {
                if (rt.birdwatch_pivot && rt.tweet) rt.tweet.birdwatch_pivot = rt.birdwatch_pivot;
                return rt.tweet;
            }
            if (rt?.__typename === 'Tweet') return rt;
            if (tweet.raw_json.retweeted_status) return tweet.raw_json.retweeted_status;
            return null;
        },

        formatRetweetToTweet(originalTweet, rt) {
            if (rt.author) {
                return {
                    ...rt,
                    synced_at: originalTweet.synced_at,
                    collection: originalTweet.collection,
                    collections: originalTweet.collections,
                    media_tags: rt.media_tags || originalTweet.media_tags,
                };
            }
            const author = this.getQuoteAuthor(rt);
            return {
                tweet_id: rt.rest_id || rt.id_str || originalTweet.tweet_id,
                author: { display_name: author.name, username: author.screen_name, id: author.id },
                text: this.getQuoteText(rt),
                created_at: rt.legacy?.created_at || rt.created_at || originalTweet.created_at,
                synced_at: originalTweet.synced_at,
                collection: originalTweet.collection,
                raw_json: rt,
                media: originalTweet.media,
                qt_media: originalTweet.qt_media
            };
        },

        getQuoteAuthor(qt) {
            if (!qt) return { name: 'Unknown', screen_name: 'unknown', id: 'x', initial: '?' };
            if (qt.author) {
                const name = qt.author.display_name || 'Unknown';
                return {
                    name,
                    screen_name: qt.author.username || 'unknown',
                    id: qt.author.id || 'x',
                    initial: name.charAt(0),
                };
            }
            const userResult = qt.core?.user_results?.result;
            let name = userResult?.legacy?.name || userResult?.core?.name || qt.user?.name || 'Unknown';
            let screen_name = userResult?.legacy?.screen_name || userResult?.core?.screen_name || qt.user?.screen_name || 'unknown';
            let id = userResult?.rest_id || qt.user?.id_str || 'x';
            return { name, screen_name, id, initial: name.charAt(0) };
        },

        getQuoteText(qt) {
            if (!qt) return '';
            if (this.isTombstone(qt)) {
                return qt.availability?.message || qt.tombstone?.text?.text || qt.text || "This tweet is unavailable.";
            }
            return qt.text || qt.legacy?.full_text || qt.full_text || '';
        },

        getTweetId(tweet) {
            return tweet?.tweet_id || tweet?.rest_id || tweet?.id_str || null;
        },

        formatText(tweet, forceFull = false) {
            if (!tweet) return '';
            if (this.isPlaceholderTweet(tweet)) return this.renderTweetPlaceholder(tweet);
            const raw = tweet.raw_json?.raw_json || tweet.raw_json || tweet;
            let text = tweet.text || raw?.legacy?.full_text || raw?.full_text || '';
            if (this.isTextPlaceholderTweet(tweet)) {
                return this.renderContentPlaceholder(
                    'Tweet text was not captured in the local archive.',
                    'text_not_archived',
                );
            }
            
            if (raw && raw.legacy && raw.legacy.in_reply_to_status_id_str) {
                text = text.replace(/^(@\w+\s+)+/, '');
            }

            let isTruncated = false;
            let tweetId = tweet.tweet_id || (raw && raw.rest_id);
            
            if (!forceFull && text.length > 280 && tweetId && !this.expandedTweets[tweetId]) {
                let truncated = text.substring(0, 280);
                let lastSpace = truncated.lastIndexOf(' ');
                if (lastSpace > 200) {
                    truncated = truncated.substring(0, lastSpace);
                }
                text = truncated + '...';
                isTruncated = true;
            }
            
            let urlMap = {};
            if (raw && raw.legacy) {
                const urlsToRemove = [];
                if (raw.legacy.entities?.media) urlsToRemove.push(...raw.legacy.entities.media.map(m => m.url));
                if (raw.legacy.extended_entities?.media) urlsToRemove.push(...raw.legacy.extended_entities.media.map(m => m.url));
                if (raw.legacy.quoted_status_permalink?.url) urlsToRemove.push(raw.legacy.quoted_status_permalink.url);
                
                if (raw.card && raw.card.legacy && raw.card.legacy.url) urlsToRemove.push(raw.card.legacy.url);
                if (raw.card && raw.card.url) urlsToRemove.push(raw.card.url);
                
                urlsToRemove.filter(Boolean).forEach(u => {
                    text = text.split(u).join('');
                });
                
                if (raw.legacy.entities?.urls) {
                    raw.legacy.entities.urls.forEach(u => {
                        if (!urlsToRemove.includes(u.url)) {
                            const expandedUrl = this.safeURL(u.expanded_url);
                            const displayUrl = this.escapeHTML(u.display_url || u.expanded_url || '');
                            urlMap[u.url] = `<a href="${expandedUrl}" target="_blank" rel="noopener noreferrer" class="text-[var(--accent-color)] hover:underline" @click.stop>${displayUrl}</a>`;
                        }
                    });
                }
            }
            
            text = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
            text = text.replace(/@(\w+)/g, '<span class="mention text-[var(--accent-color)] hover:underline cursor-pointer" data-user="$1">@$1</span>');
            text = text.replace(/#(\w+)/g, '<span class="hashtag text-[var(--accent-color)] hover:underline cursor-pointer" data-tag="$1">#$1</span>');
            
            Object.keys(urlMap).forEach(u => {
                text = text.split(u).join(urlMap[u]);
            });
            
            if (isTruncated) {
                text += `<span class="text-[var(--accent-color)] hover:underline cursor-pointer block mt-1 expand-tweet" data-id="${tweetId}">Show more</span>`;
            }
            
            return text.trim();
        },


        expandedTweets: {},

        handleTextClick(e) {
            if (e.target.tagName === 'SPAN' && e.target.classList.contains('mention')) {
                e.stopPropagation();
                const username = e.target.dataset.user;
                const dummyTweet = {
                    author: { username: username, display_name: username, id: 'unknown' }
                };
                this.openProfileCard(e, dummyTweet);
            } else if (e.target.tagName === 'SPAN' && e.target.classList.contains('hashtag')) {
                e.stopPropagation();
                this.searchQuery = '#' + e.target.dataset.tag;
                this.search();
            } else if (e.target.tagName === 'SPAN' && e.target.classList.contains('expand-tweet')) {
                e.stopPropagation();
                const tid = e.target.dataset.id;
                if (tid) {
                    this.expandedTweets[tid] = true;
                }
            }
        },
        
        renderCard(tweet) {
            if (this.isPlaceholderTweet(tweet)) return '';
            if (!tweet || !tweet.raw_json) return '';
            const raw = tweet.raw_json.raw_json || tweet.raw_json;
            const card = raw.card;
            if (!card) return '';
            const name = card.name || card.legacy?.name || '';
            const bindingArray = card.binding_values || card.legacy?.binding_values || [];
            const binding = {};
            if (Array.isArray(bindingArray)) {
                bindingArray.forEach(item => {
                    binding[item.key] = item.value;
                });
            } else {
                Object.assign(binding, bindingArray);
            }
            let html = '';

            if (name.startsWith('poll')) {
                const choices = [];
                for (let i = 1; i <= 4; i++) {
                    const choice = binding[`choice${i}_label`];
                    const count = binding[`choice${i}_count`];
                    if (choice && choice.string_value) {
                        choices.push({
                            label: choice.string_value,
                            count: count ? parseInt(count.string_value) : 0
                        });
                    }
                }
                if (choices.length > 0) {
                    const total = choices.reduce((s, c) => s + c.count, 0) || 1;
                    const trueTotal = choices.reduce((s, c) => s + c.count, 0);
                    const maxCount = Math.max(...choices.map(c => c.count));
                    const isFinal = binding.counts_are_final?.boolean_value === true;
                    
                    html += `<div class="mt-3 flex flex-col space-y-[6px]">`;
                    choices.forEach(c => {
                        const pct = Math.round((c.count / total) * 100);
                        const isWinner = c.count === maxCount && maxCount > 0;
                        const weightClass = isWinner ? 'font-bold' : 'font-normal';
                        const barColor = isWinner ? 'bg-[var(--accent-color)] opacity-[0.4]' : 'bg-[var(--border-color)] opacity-[0.6]';
                        const label = this.escapeHTML(c.label);
                        
                        html += `<div class="relative w-full h-[32px] rounded flex items-center overflow-hidden">
                                    <div class="absolute left-0 top-0 bottom-0 ${barColor} rounded" style="width: ${pct}%"></div>
                                    <span class="relative z-10 text-[15px] ${weightClass} text-[var(--text-primary)] w-full flex justify-between px-3">
                                        <span class="truncate pr-4" title="${label}">${label}</span>
                                        <span>${pct}%</span>
                                    </span>
                                 </div>`;
                    });
                    
                    const statusText = isFinal ? 'Final results' : '';
                    const dot = isFinal ? ' · ' : '';
                    html += `<div class="text-[14px] text-[var(--text-secondary)] mt-2">${trueTotal} votes${dot}${statusText}</div></div>`;
                }
            } else if (name === 'summary' || name === 'summary_large_image') {
                const title = this.escapeHTML(binding.title?.string_value || '');
                const desc = this.escapeHTML(binding.description?.string_value || '');
                const vanityUrl = this.escapeHTML(binding.vanity_url?.string_value || '');
                const expandedUrl = this.safeURL(
                    binding.card_url?.string_value || binding.vanity_url?.string_value || '#'
                );
                let imageHtml = '';
                
                if (name === 'summary_large_image') {
                    const imgUrl = binding.thumbnail_image_original?.image_value?.url || 
                                   binding.summary_photo_image_original?.image_value?.url || 
                                   binding.thumbnail_image_x_large?.image_value?.url ||
                                   binding.thumbnail_image?.image_value?.url || 
                                   binding.photo_image_full_size?.image_value?.url || 
                                   binding.summary_photo_image?.image_value?.url;
                    const localImage = this.localCardImage(tweet, imgUrl);
                    if (localImage) {
                        const safeImgUrl = this.escapeHTML(localImage);
                        imageHtml = `<div class="w-full aspect-[1.91/1] bg-[var(--bg-tertiary)] overflow-hidden border-b border-[var(--border-color)]">
                                        <img src="${safeImgUrl}" class="w-full h-full object-cover" loading="lazy" onerror="this.parentElement.hidden=true">
                                     </div>`;
                    }
                }
                
                if (title || desc || imageHtml) {
                    html += `<a href="${expandedUrl}" target="_blank" rel="noopener noreferrer" class="mt-3 block border border-[var(--border-color)] rounded-xl overflow-hidden hover-bg transition cursor-pointer">
                                ${imageHtml}
                                <div class="p-3">
                                    <div class="text-[13px] text-[var(--text-secondary)] mb-1">${vanityUrl}</div>
                                    <div class="text-[15px] text-[var(--text-primary)] font-bold leading-tight mb-1" style="display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;">${title}</div>
                                    <div class="text-[14px] text-[var(--text-secondary)]" style="display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;">${desc}</div>
                                </div>
                             </a>`;
                }
            }
            return html;
        },

        localCardImage(tweet, remoteUrl) {
            if (!remoteUrl) return null;
            // Use the same archived media/poster files as the media grid.
            const media = tweet.media || [];
            for (const item of media) {
                const download = item.download || {};
                const path = item.thumbnail_url === remoteUrl && download.thumbnail_local_path
                    ? download.thumbnail_local_path
                    : (item.url === remoteUrl || item.media_url === remoteUrl) && download.local_path;
                if (typeof path === 'string' && path.startsWith('media/') && !path.includes('..') && !path.includes('\\')) {
                    return '/' + path.split('/').map(encodeURIComponent).join('/');
                }
            }
            return null;
        },

        avatarUrl(userId) {
            if (this.isDemo) return window.TweetNookDemo?.avatarUrl(userId) || '';
            return '/api/avatar/' + (userId || 'unknown');
        },

        formatMediaDuration(durationMillis) {
            const millis = Number(durationMillis);
            if (!Number.isFinite(millis) || millis <= 0) return '';

            const totalSeconds = Math.floor(millis / 1000);
            const seconds = String(totalSeconds % 60).padStart(2, '0');
            const totalMinutes = Math.floor(totalSeconds / 60);
            if (totalMinutes < 60) return `${totalMinutes}:${seconds}`;

            const minutes = String(totalMinutes % 60).padStart(2, '0');
            return `${Math.floor(totalMinutes / 60)}:${minutes}:${seconds}`;
        },

        renderMediaPlaybackIndicator(item) {
            if (item.isGif) {
                return `<button type="button" class="media-gif-indicator" data-gif-toggle aria-label="Pause GIF" title="Pause GIF" onclick="window.tweetNookToggleGif(event, this)">
                            <span class="media-gif-action" aria-hidden="true">
                                <svg class="media-gif-pause-icon" viewBox="0 0 24 24"><path d="M6 4h4v16H6zm8 0h4v16h-4z"></path></svg>
                                <svg class="media-gif-play-icon" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"></path></svg>
                            </span>
                            <span class="media-gif-label">GIF</span>
                        </button>`;
            }

            const duration = this.formatMediaDuration(item.m.duration_millis);
            if (!duration) return '';
            return `<span class="media-video-duration" aria-label="Video duration ${duration}">${duration}</span>`;
        },

        renderMediaGrid(mediaList) {
            if (!mediaList || mediaList.length === 0) return '';
            
            const getSrc = (m) => {
                if (m.download?.local_path) return `/${m.download.local_path}`;
                return this.isDemo ? (m.url || m.media_url || null) : null;
            };

            const getPoster = (m) => {
                if (m.download?.thumbnail_local_path) return `/${m.download.thumbnail_local_path}`;
                return this.isDemo ? (m.thumbnail_url || null) : null;
            };

            const allMedia = mediaList.map(m => {
                const type = (m.type === 'video' || m.type === 'animated_gif') ? 'video' : 'photo';
                const isGif = m.type === 'animated_gif'; 
                const duration = m.duration_millis;
                const isShort = duration && duration <= 60000;
                return { m, src: getSrc(m), poster: getPoster(m), type, isGif, isShort };
            });
            
            if (allMedia.length === 0) return '';
            
            const downloadedMedia = allMedia.filter(x => x.src);
            const jsonStr = JSON.stringify(downloadedMedia).replace(/'/g, "&#39;").replace(/"/g, "&quot;");

            const placeholderHtml = `
                <div class="absolute inset-0 w-full h-full flex flex-col items-center justify-center bg-[var(--bg-secondary)] text-[var(--text-secondary)]">
                    <svg class="w-8 h-8 mb-2 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"></path></svg>
                    <span class="text-xs font-medium">Media not downloaded</span>
                </div>
            `;

            if (allMedia.length === 1) {
                const item = allMedia[0];
                const { src, poster, type, isGif, isShort } = item;
                const aspect = allMedia[0].m.width && allMedia[0].m.height ? (allMedia[0].m.width / allMedia[0].m.height) : 0;
                const containerStyle = aspect 
                    ? `width: min(100%, calc(512px * ${aspect})); aspect-ratio: ${aspect}; max-height: 512px;`
                    : 'width: 100%; max-height: 512px; aspect-ratio: 16/9;';

                if (!src) {
                    return `<div class="mt-3 relative max-w-full rounded-2xl border border-[var(--border-color)] overflow-hidden block" style="${containerStyle}" @click.stop>
                                ${placeholderHtml}
                            </div>`;
                } else if (type === 'photo') {
                    return `<div class="mt-3 relative max-w-full rounded-2xl border border-[var(--border-color)] overflow-hidden block" style="${containerStyle}" @click.stop>
                                <img src="${src}" onclick="window.dispatchEvent(new CustomEvent('open-lightbox', { detail: { media: JSON.parse('${jsonStr}'), index: 0 } }))" class="w-full h-full object-cover cursor-pointer hover:opacity-90 transition block">
                            </div>`;
                } else {
                    const loopAttr = (isGif || isShort) ? 'loop' : '';
                    const autoplayAttr = isGif ? 'autoplay muted playsinline' : '';
                    const controlsAttr = isGif ? '' : 'controls';
                    const gifEvents = isGif
                        ? 'data-animated-gif onplay="window.tweetNookSyncGifIndicator(this)" onpause="window.tweetNookSyncGifIndicator(this)" onloadeddata="window.tweetNookSyncGifIndicator(this)"'
                        : '';
                    const indicator = this.renderMediaPlaybackIndicator(item);

                    return `<div class="mt-3 relative max-w-full rounded-2xl border border-[var(--border-color)] overflow-hidden block" style="${containerStyle}" @click.stop>
                                <video src="${src}" poster="${poster || ''}" ${autoplayAttr} ${loopAttr} ${controlsAttr} ${gifEvents} class="w-full h-full object-cover outline-none block"></video>
                                ${indicator}
                            </div>`;
                }
            }
            
            let gridClass = allMedia.length === 2 ? 'grid-cols-2' : 'grid-cols-2 grid-rows-2';
            let html = `<div class="mt-3 grid gap-[2px] rounded-2xl overflow-hidden border border-[var(--border-color)] aspect-[16/9] ${gridClass}" @click.stop>`;
            
            let downloadedIdx = 0;
            allMedia.forEach((item, idx) => {
                const { src, poster, type, isGif, isShort } = item;
                let itemClass = (allMedia.length === 3 && idx === 0) ? 'row-span-2 col-span-1' : (allMedia.length === 3 ? 'col-span-1' : '');
                
                if (!src) {
                    html += `<div class="relative w-full h-full bg-[var(--border-color)] ${itemClass}">
                                ${placeholderHtml}
                            </div>`;
                } else {
                    const currentDlIdx = downloadedIdx++;
                    if (type === 'photo') {
                        html += `<div class="relative w-full h-full bg-[var(--border-color)] ${itemClass}">
                                    <img src="${src}" onclick="window.dispatchEvent(new CustomEvent('open-lightbox', { detail: { media: JSON.parse('${jsonStr}'), index: ${currentDlIdx} } }))" class="absolute inset-0 w-full h-full object-cover cursor-pointer hover:opacity-90 transition">
                                </div>`;
                    } else {
                        const loopAttr = (isGif || isShort) ? 'loop' : '';
                        const autoplayAttr = isGif ? 'autoplay muted playsinline' : '';
                        const controlsAttr = isGif ? '' : 'controls';
                        const gifEvents = isGif
                            ? 'data-animated-gif onplay="window.tweetNookSyncGifIndicator(this)" onpause="window.tweetNookSyncGifIndicator(this)" onloadeddata="window.tweetNookSyncGifIndicator(this)"'
                            : '';
                        const indicator = this.renderMediaPlaybackIndicator(item);
                        html += `<div class="relative w-full h-full bg-[var(--border-color)] ${itemClass}">
                                    <video src="${src}" poster="${poster || ''}" ${autoplayAttr} ${loopAttr} ${controlsAttr} ${gifEvents} class="absolute inset-0 w-full h-full object-cover outline-none"></video>
                                    ${indicator}
                                </div>`;
                    }
                }
            });
            
            html += `</div>`;
            return html;
        },

        renderRawMediaGrid(rawMediaList) {
            return this.renderMediaGrid(rawMediaList, true);
        },

        escapeHTML(str) {
            if (!str) return '';
            return String(str).replace(/&/g, '&amp;')
                      .replace(/</g, '&lt;')
                      .replace(/>/g, '&gt;')
                      .replace(/"/g, '&quot;')
                      .replace(/'/g, '&#039;');
        },

        safeURL(value) {
            if (!value || value === '#') return '#';
            try {
                const parsed = new URL(value, window.location?.origin || 'http://localhost');
                return ['http:', 'https:'].includes(parsed.protocol)
                    ? this.escapeHTML(parsed.href)
                    : '#';
            } catch (_) {
                return '#';
            }
        },

        formatCommunityNoteText(bw) {
            let text = bw.subtitle?.text || '';
            if (!text) return '';
            let entities = bw.subtitle?.entities || [];
            
            entities = [...entities].sort((a, b) => a.fromIndex - b.fromIndex);
            
            let html = '';
            let lastIndex = 0;
            
            for (const ent of entities) {
                if (ent.ref?.urlType === 'ExternalUrl' && ent.ref?.url) {
                    html += this.escapeHTML(text.substring(lastIndex, ent.fromIndex));
                    const linkText = this.escapeHTML(text.substring(ent.fromIndex, ent.toIndex));
                    const href = this.escapeHTML(ent.ref.url);
                    html += `<a href="${href}" target="_blank" class="text-[var(--accent-color)] hover:underline" @click.stop>${linkText}</a>`;
                    lastIndex = ent.toIndex;
                }
            }
            html += this.escapeHTML(text.substring(lastIndex));
            return html;
        },

        renderCommunityNote(raw_json, isQuote = false) {
            raw_json = raw_json?.raw_json || raw_json;
            if (!raw_json || !raw_json.birdwatch_pivot) return '';
            const bw = raw_json.birdwatch_pivot;
            if (!bw.subtitle || !bw.subtitle.text) return '';
            
            const textHtml = this.formatCommunityNoteText(bw);
            const containerClass = isQuote 
                ? "-mx-3 -mb-3 p-3 bg-[var(--bg-secondary)] border-t border-[var(--border-color)] rounded-b-xl text-left" 
                : "mt-3 border border-[var(--border-color)] rounded-xl p-3 bg-[var(--bg-secondary)] hover-bg transition cursor-pointer text-left";

            const iconSvg = `<svg viewBox="0 0 24 24" fill="var(--accent-color)" class="w-[18px] h-[18px]"><path fill-rule="evenodd" d="M8.25 6.75a3.75 3.75 0 117.5 0 3.75 3.75 0 01-7.5 0zM15.75 9.75a3 3 0 116 0 3 3 0 01-6 0zM2.25 9.75a3 3 0 116 0 3 3 0 01-6 0zM6.31 15.117A6.745 6.745 0 0112 12a6.745 6.745 0 016.709 7.498.75.75 0 01-.372.568A12.696 12.696 0 0112 21.75c-2.305 0-4.47-.612-6.337-1.684a.75.75 0 01-.372-.568 6.787 6.787 0 011.019-4.38z" clip-rule="evenodd" /><path d="M5.082 14.254a8.287 8.287 0 00-1.308 5.135 9.687 9.687 0 01-1.764-.44l-.115-.04a.563.563 0 01-.373-.487l-.01-.121a3.75 3.75 0 016.576-3.036c.32.338.608.708.857 1.103a6.732 6.732 0 00-3.863 1.341 6.772 6.772 0 01-.004-3.456z" /><path d="M18.918 14.254a8.287 8.287 0 011.308 5.135 9.687 9.687 0 001.764-.44l.115-.04a.563.563 0 00.373-.487l-.01-.121a3.75 3.75 0 00-6.576-3.036c-.32.338-.608.708-.857 1.103a6.732 6.732 0 013.863 1.341 6.772 6.772 0 00.004-3.456z" /></svg>`;
            
            return `
            <div class="${containerClass}" ${!isQuote ? 'onclick="event.stopPropagation()"' : ''}>
                <div class="flex items-center space-x-2 text-[15px] font-bold text-[var(--text-primary)] mb-1">
                    ${iconSvg}
                    <span>${this.escapeHTML(bw.shorttitle || 'Readers added context')}</span>
                </div>
                <div class="text-[15px] text-[var(--text-primary)] whitespace-pre-wrap break-words leading-normal mt-2">${textHtml}</div>
            </div>`;
        },

        renderActionBar(tweet, isMain = false) {
            if (this.isPlaceholderTweet(tweet) || this.isTextPlaceholderTweet(tweet)) return '';
            const legacy = tweet.raw_json?.legacy || {};
            const replyCount = legacy.reply_count || 0;
            const retweetCount = (legacy.retweet_count || 0) + (legacy.quote_count || 0);
            const likeCount = legacy.favorite_count || 0;
            const viewCount = tweet.raw_json?.views?.count || 0;
            const bmkCount = legacy.bookmark_count || 0;
            
            const formatNum = (num) => num > 0 ? (num > 999 ? (num/1000).toFixed(1) + 'K' : num) : '';
            
            const collections = tweet.collections || [];
            if (tweet.collection?.type) collections.push(tweet.collection.type);
            const isLiked = collections.includes('like');
            const isBookmarked = collections.includes('bookmark');
            
            const likeIcon = isLiked ? 
                `<svg viewBox="0 0 24 24" class="w-[18.5px] h-[18.5px]" style="fill: var(--danger-color)"><path d="M20.884 13.19c-1.351 2.48-4.001 5.12-8.379 7.67l-.503.3-.504-.3C7.121 18.31 4.471 15.67 3.119 13.19 1.928 10.99 1.898 8.48 2.921 6.45 3.864 4.56 5.8 3.32 8.016 3.42c1.474.07 2.812.8 3.486 2.08l.498.94.498-.94c.674-1.28 2.012-2.01 3.486-2.08 2.216-.1 4.152 1.14 5.095 3.03 1.023 2.03.993 4.54-.195 6.74z"></path></svg>` : 
                `<svg viewBox="0 0 24 24" class="w-[18.5px] h-[18.5px] fill-current group-hover:fill-[var(--danger-color)]"><path d="M16.697 5.5c-1.222-.06-2.679.51-3.89 2.16l-.805 1.09-.806-1.09C9.984 6.01 8.526 5.44 7.304 5.5c-1.243.07-2.349.78-2.91 1.91-.552 1.12-.633 2.78.479 4.82 1.074 1.97 3.257 4.27 7.129 6.61 3.87-2.34 6.052-4.64 7.126-6.61 1.111-2.04 1.03-3.7.477-4.82-.561-1.13-1.666-1.84-2.908-1.91zm4.187 7.69c-1.351 2.48-4.001 5.12-8.379 7.67l-.503.3-.504-.3c-4.379-2.55-7.029-5.19-8.382-7.67-1.36-2.5-1.41-4.86-.514-6.67.887-1.79 2.647-2.91 4.601-3.01 1.651-.09 3.368.56 4.798 2.01 1.429-1.45 3.146-2.1 4.796-2.01 1.954.1 3.714 1.22 4.601 3.01.896 1.81.846 4.17-.514 6.67z"></path></svg>`;

            const bmkIcon = isBookmarked ? 
                `<svg viewBox="0 0 24 24" class="w-[18.5px] h-[18.5px] fill-[var(--accent-color)]"><path d="M4 4.5C4 3.12 5.119 2 6.5 2h11C18.881 2 20 3.12 20 4.5v18.44l-8-5.71-8 5.71V4.5z"></path></svg>` : 
                `<svg viewBox="0 0 24 24" class="w-[18.5px] h-[18.5px] fill-current"><path d="M4 4.5C4 3.12 5.119 2 6.5 2h11C18.881 2 20 3.12 20 4.5v18.44l-8-5.71-8 5.71V4.5zM6.5 4c-.276 0-.5.22-.5.5v14.56l6-4.29 6 4.29V4.5c0-.28-.224-.5-.5-.5H6.5z"></path></svg>`;

            const margin = isMain ? 'pt-3 pb-0 border-t border-[var(--border-color)] w-full' : 'mt-3 w-full';
            
            return `
            <div class="flex items-center justify-between text-[var(--text-secondary)] w-full ${margin}">
                <div class="flex items-center">
                    <svg viewBox="0 0 24 24" class="w-[18.5px] h-[18.5px] fill-current"><path d="M1.751 10c0-4.42 3.584-8 8.005-8h4.366c4.49 0 8.129 3.64 8.129 8.13 0 2.96-1.607 5.68-4.196 7.11l-8.054 4.46v-3.69h-.067c-4.49.1-8.183-3.51-8.183-8.01zm8.005-6c-3.317 0-6.005 2.69-6.005 6 0 3.37 2.77 6.08 6.138 6.01l.351-.01h1.761v2.3l5.087-2.81c1.951-1.08 3.163-3.13 3.163-5.36 0-3.39-2.744-6.13-6.129-6.13H9.756z"></path></svg>
                    <span class="text-[13px] ml-2 font-medium">${formatNum(replyCount)}</span>
                </div>
                <div class="flex items-center">
                    <svg viewBox="0 0 24 24" class="w-[18.5px] h-[18.5px] fill-current"><path d="M4.5 3.88l4.432 4.14-1.364 1.46L5.5 7.55V16c0 1.1.896 2 2 2H13v2H7.5c-2.209 0-4-1.79-4-4V7.55L1.432 9.48.068 8.02 4.5 3.88zM16.5 6H11V4h5.5c2.209 0 4 1.79 4 4v8.45l2.068-1.93 1.364 1.46-4.432 4.14-4.432-4.14 1.364-1.46 2.068 1.93V8c0-1.1-.896-2-2-2z"></path></svg>
                    <span class="text-[13px] ml-2 font-medium">${formatNum(retweetCount)}</span>
                </div>
                <div class="flex items-center group cursor-pointer ${isLiked ? 'text-[var(--danger-color)]' : 'hover:text-[var(--danger-color)]'}">
                    ${likeIcon}
                    <span class="text-[13px] ml-2 font-medium">${formatNum(likeCount)}</span>
                </div>
                <div class="flex items-center group cursor-pointer ${isBookmarked ? 'text-[var(--accent-color)]' : 'hover:text-[var(--accent-color)]'}">
                    ${bmkIcon}
                    <span class="text-[13px] ml-2 font-medium">${formatNum(bmkCount)}</span>
                </div>
                <div class="flex items-center">
                    <svg viewBox="0 0 24 24" class="w-[18.5px] h-[18.5px] fill-current"><path d="M8.75 21V3h2v18h-2zM18 21V8.5h2V21h-2zM4 21l.004-10h2L6 21H4zm9.248 0v-7h2v7h-2z"></path></svg>
                    <span class="text-[13px] ml-2 font-medium">${formatNum(viewCount)}</span>
                </div>
                <div class="flex items-center">
                    <a href="https://x.com/${tweet.author?.username || 'i'}/status/${tweet.tweet_id}" target="_blank" @click.stop class="flex items-center" title="Open on Twitter/X">
                        <svg viewBox="0 0 24 24" class="w-[18.5px] h-[18.5px] fill-current hover:text-[#e7e9ea] transition"><path d="M18 19H6c-.55 0-1-.45-1-1V6c0-.55.45-1 1-1h5c.55 0 1-.45 1-1s-.45-1-1-1H6c-1.65 0-3 1.35-3 3v12c0 1.65 1.35 3 3 3h12c1.65 0 3-1.35 3-3v-5c0-.55-.45-1-1-1s-1 .45-1 1v5c0 .55-.45 1-1 1zM14 4c0 .55.45 1 1 1h2.59l-9.13 9.13c-.39.39-.39 1.02 0 1.41.19.19.45.29.71.29s.51-.1.71-.29L19 6.41V9c0 .55.45 1 1 1s1-.45 1-1V4c0-.55-.45-1-1-1h-5c-.55 0-1 .45-1 1z"></path></svg>
                    </a>
                </div>
            </div>`;
        }
    };
}
