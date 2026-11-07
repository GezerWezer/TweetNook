/**
 * Analytics: the Stats dialog's report, refreshed in the background, and the reminder
 * shown while archive enrichment is incomplete. The app opens and closes the dialog;
 * statsDialog() owns the report while it is shown.
 */

const statsSuspendedVideos = new Set();

function statsMethods() {
    return {
        lastSyncFormatted: '',

        showStatsModal: false,
        archiveEnrichmentIncomplete: 0,
        archiveEnrichmentRefreshing: false,
        archiveEnrichmentRequestId: 0,

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

        openStatsModal() {
            this.suspendMediaForStats();
            this.showStatsModal = true;
        },

        closeStatsModal() {
            this.showStatsModal = false;
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
    };
}

// The Stats dialog (x-data="statsDialog()"): loads the snapshot when the dialog opens
// and polls a background refresh until it finishes or the dialog closes.
function statsDialog() {
    return {
        ...statsInsightMethods(),
        statsSummary: null,
        loadingStatsSummary: false,
        statsCollections: [],
        loadingStatsCollections: false,
        statsHealth: null,
        loadingStatsHealth: false,
        showEmptyUnavailableReasons: false,
        hoveredUnavailableReason: null,
        statsTags: null,
        loadingStatsTags: false,
        loadingStatsSnapshot: false,
        statsGeneratedAt: null,
        statsRefreshing: false,
        statsRefreshFailed: false,
        statsRefreshPollTimer: null,
        statsRefreshPollAttempts: 0,
        statsAgeNow: Date.now(),

        storageData: null,
        storageLoading: false,
        storageDetailedView: false,
        storageGapsCollapsed: false,
        animatingStorageToggle: false,
        hoveredStorageId: null,
        expandedStorageId: null,

        init() {
            this.$watch('showStatsModal', open => {
                if (open) {
                    this.fetchStatsSnapshot();
                } else if (this.statsRefreshPollTimer) {
                    clearTimeout(this.statsRefreshPollTimer);
                    this.statsRefreshPollTimer = null;
                }
            });
            setInterval(() => { this.statsAgeNow = Date.now(); }, 30000);
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
            this.applyInsights(snapshot.insights);
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
    };
}
