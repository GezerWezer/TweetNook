/**
 * The setup dialog (x-data="setupDialog()"): the steps' forms (archive upload and import,
 * X connection, schedule, appearance, password), the replay source and the help panel.
 */

// Archive, Connect, Schedule, Appearance, Password.
const SETUP_STEP_COUNT = 5;

function setupDialog() {
    return {
        setupCompletionNeedsReload: false,
        setupSkipAuth: false,
        setupPassword: '',
        setupPasswordConfirm: '',
        setupNoPassword: false,
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
        setupStartingImport: false,
        setupConfirm: null,
        setupHelp: null,
        setupReplayForm: { url: '', password: '', placeholder_media: true, skip_quote_expansion: false, skip_link_previews: false },
        setupReplaySaving: false,
        setupReplayError: '',
        setupHelpOpen: false,
        setupHelpBrowser: 0,
        setupHelpError: '',

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
    };
}
