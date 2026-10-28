/**
 * Setup's place in the app: whether Setup is open or required, the setup status and
 * archive readiness, the current step, the completion screen, and loading the archive's
 * resources once it is ready. The dialog's own forms are in setup-dialog.js.
 */

const SETUP_LOAD_ERROR = 'Could not load setup';

function setupMethods() {
    return {
        showSetupModal: false,

        setupData: null,
        archiveReady: null,
        archiveResourcesLoaded: false,
        setupForced: false,
        setupRerun: false,
        setupCompletionVisible: false,
        setupFlowStep: 1,
        setupDirection: 'forward',
        setupFinishing: false,
        setupError: '',
        setupPolling: false,

        setupAuth: null,
        setupStepResumed: false,
        setupAuthEdited: false,

        openSetup() {
            if (this.isDemo) return;
            this.setupCompletionVisible = false;
            if (this.setupData?.completed) this.setupRerun = true;
            this.showSetupModal = true;
            this.showSettingsModal = false;
            this.fetchSetup();
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

        // A first-run reload returns to the first unfinished step instead of step 1.
        resumeSetupStep() {
            if (this.setupStepResumed || !this.setupForced || !this.setupData) return;
            this.setupStepResumed = true;
            const archive = this.setupData.archive || {};
            if (!archive.initialization_mode) return;
            this.setupFlowStep = this.setupData.auth?.verified ? 3 : 2;
        },

        // Developer replay source (serve --dev-tools): this install's Twitter/X traffic
        // goes to another TweetNook archive. The server keeps the password masked.
        get setupReplayConnected() {
            return !!this.setupData?.dev?.replay?.connected;
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
    };
}
