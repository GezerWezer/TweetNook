/**
 * The Jobs panel's live state: the activity status stream with polling as its fallback,
 * starting and stopping jobs, the drawer and its pages, and the runs listed in Activity logs.
 */

function activityMethods() {
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

    return {
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
                    this.discardCachedPages();
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
                this.discardCachedPages();
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

        activityRuns: [],
        activityRunsLoading: false,
        activityPollTimer: null,
    };
}
