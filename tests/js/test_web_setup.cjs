'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { appScripts, appScriptsWith, pageHtml, pageScripts, readScripts } = require('./page_scripts.cjs');
const {
    JS_DIR,
    ROOT,
    browserContext,
    immediateComponent,
    loadScripts,
    withSetupDialog,
    test,
    run,
    pageCss,
} = require('./web_harness.cjs');

test('setup polling keeps unchanged status objects and updates only what changed', async () => {
    const context = browserContext();
    const status = () => ({ required: false, completed: true, web: { password_configured: true },
        auth: { configured: true, verified: true, values: { auth_token: '', ct0: '', user_id: '' } },
        archive: { ready: true, pending_enrichment: 3, warnings: ['a'] } });
    let next = status();
    context.fetch = async () => ({ ok: true, json: async () => next });
    const app = immediateComponent(loadScripts(context, appScripts(), 'tweetApp')());
    app.archiveResourcesLoaded = true;
    await app.fetchSetup();
    const first = app.setupData;
    const archive = first.archive;
    next = status();
    await app.fetchSetup();
    assert.equal(app.setupData, first);
    assert.equal(app.setupData.archive, archive);
    next = status();
    next.archive.pending_enrichment = 0;
    next.archive.warnings = [];
    await app.fetchSetup();
    assert.equal(app.setupData.archive, archive);
    assert.equal(app.setupData.archive.pending_enrichment, 0);
    assert.deepEqual(Array.from(app.setupData.archive.warnings), []);
});

test('replay setup saves an empty cookie as a disconnected account and can reconnect', async () => {
    const context = browserContext();
    context.setTimeout = callback => { callback(); return 0; };
    let auth = { configured: true, verified: true, values: { auth_token: '********', ct0: '********', user_id: '' } };
    const requests = [];
    const dev = { tools: true, replay: { connected: true } };
    context.fetch = async (url, options = {}) => {
        if (url === '/api/setup/auth') {
            const values = JSON.parse(options.body);
            requests.push(values);
            const connected = !!(values.auth_token?.trim() && values.ct0?.trim());
            auth = { configured: connected, verified: connected, values };
            return { ok: true, async json() { return auth; } };
        }
        assert.equal(url, '/api/setup');
        return { ok: true, async json() { return { auth, dev, archive: { ready: false } }; } };
    };
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = withSetupDialog(context, immediateComponent(tweetApp()));
    await app.fetchSetup();
    app.setupFlowStep = 2;
    app.setupAuth.ct0 = '  ';
    app.editSetupAuth();
    assert.equal(await app.saveSetupAuth(), true);
    assert.equal(requests.length, 1);
    assert.equal(app.setupData.auth.verified, false);
    assert.equal(app.setupSkipAuth, true);
    assert.equal(app.setupAuthResult.success, true);
    assert.equal(app.setupAuthResult.message, 'No Twitter/X account connected.');
    assert.equal(app.setupFlowStep, 3);

    app.setupFlowStep = 2;
    app.setupAuth.auth_token = 'offline';
    app.setupAuth.ct0 = 'anything';
    app.editSetupAuth();
    assert.equal(await app.saveSetupAuth(), true);
    assert.equal(requests.length, 2);
    assert.equal(app.setupData.auth.verified, true);
    assert.equal(app.setupSkipAuth, false);
    assert.equal(app.setupAuthResult.success, true);
    assert.equal(app.setupFlowStep, 3);
});

test('setup validates before saving auth then uploads, imports, and confirms archive clearing', async () => {
    const context = browserContext();
    const requests = [];
    let authVerified = false;
    let archive = {
        uploaded: false,
        filename: null,
        size: null,
        imported: false,
        enriched: false,
        pending_enrichment: 0,
        warnings: ['A Twitter/X archive has not been imported yet.'],
    };
    context.fetch = async (url, options = {}) => {
        requests.push([url, options.method || 'GET']);
        if (url === '/api/setup' && !options.method) {
            return { ok: true, async json() { return {
                auth: { configured: true, verified: authVerified, values: { auth_token: '********', ct0: '********', user_id: '42' } },
                archive,
            }; } };
        }
        if (url === '/api/setup/auth') {
            authVerified = true;
            return { ok: true, async json() { return {
                configured: true,
                verified: true,
                values: { auth_token: '********', ct0: '********', user_id: '42' },
            }; } };
        }
        if (url === '/api/setup/archive' && options.method === 'DELETE') {
            archive = { ...archive, uploaded: false, filename: null, size: null };
            return { ok: true, async json() { return archive; } };
        }
        if (url === '/api/setup/archive/import') {
            return { ok: true, async json() { return { started: true }; } };
        }
        if (url === '/api/activity/status') {
            return { ok: true, async json() { return { active: false, schedule: {} }; } };
        }
        throw new Error(`unexpected request: ${url}`);
    };
    // Archive uploads go through XHR so the file row can report progress.
    context.XMLHttpRequest = class {
        constructor() { this.upload = {}; }
        open(method, url) { requests.push([url, method]); }
        setRequestHeader() {}
        send(file) {
            this.upload.onprogress({ loaded: file.size / 2, total: file.size });
            archive = { ...archive, uploaded: true, filename: 'archive.zip', size: file.size };
            this.status = 200;
            this.responseText = JSON.stringify(archive);
            this.onload();
        }
    };
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = withSetupDialog(context, immediateComponent(tweetApp()));

    await app.fetchSetup();
    assert.equal(app.setupAuth.auth_token, '********');
    assert.equal(app.setupArchiveStatus, 'none');
    assert.equal(app.setupArchiveAction.label, 'Start import');
    assert.equal(app.setupArchiveAction.disabled, true);
    app.setupFlowStep = 2;
    assert.equal(app.setupAuthNeedsTest, true);
    const authDelays = [];
    context.setTimeout = (callback, delay) => {
        authDelays.push(delay);
        callback();
        return 0;
    };
    await app.saveSetupAuth();
    assert.equal(app.setupAuthResult.success, true);
    assert.deepEqual(authDelays, [1000]);
    assert.equal(app.setupFlowStep, 3);
    assert.equal(requests.filter(([url]) => url === '/api/setup/auth').length, 1);
    // A verified, unedited connection continues without another probe.
    app.goToSetupStep(2);
    assert.equal(app.setupAuthNeedsTest, false);
    await app.saveSetupAuth();
    assert.equal(app.setupFlowStep, 3);
    assert.equal(requests.filter(([url]) => url === '/api/setup/auth').length, 1);
    app.editSetupAuth();
    assert.equal(app.setupAuthNeedsTest, true);

    await app.uploadSetupArchive({ target: { files: [{ name: 'notes.txt', size: 4 }], value: 'notes.txt' } });
    assert.match(app.setupArchiveError, /not a \.zip file/);
    assert.equal(requests.some(([url, method]) => url === '/api/setup/archive' && method === 'PUT'), false);

    await app.uploadSetupArchive({
        target: { files: [{ name: 'archive.zip', size: 2048 }], value: 'archive.zip' },
    });
    assert.equal(app.setupData.archive.uploaded, true);
    assert.equal(app.setupArchiveError, '');
    assert.equal(app.setupUploading, false);
    assert.equal(app.setupArchiveStatus, 'staged');
    assert.equal(app.setupArchiveAction.label, 'Start import');
    assert.equal(app.setupArchiveAction.disabled, false);
    assert.equal(app.formatSetupBytes(2048), '2.00 KiB');

    app.showSetupModal = true;
    app.setupFlowStep = 1;
    await app.importSetupArchive();
    assert.equal(app.activityDrawerOpen, false);
    assert.equal(app.showSetupModal, true);
    assert.equal(app.setupFlowStep, 2);
    assert.equal(app.setupDirection, 'forward');
    app.goToSetupStep(1);
    assert.equal(app.setupDirection, 'back');
    app.goToSetupStep(2);

    const confirmations = [];
    app.askSetupConfirm = async options => { confirmations.push(options); return false; };
    await app.clearSetupArchive();
    assert.equal(app.setupData.archive.uploaded, true);
    assert.equal(confirmations[0].destructive, true);
    app.askSetupConfirm = async () => true;
    await app.clearSetupArchive();
    assert.equal(app.setupData.archive.uploaded, false);
    assert.ok(requests.some(([url, method]) => url === '/api/setup/archive' && method === 'DELETE'));
});

test('setup archive choices stay reversible until Setup finishes', async () => {
    const context = browserContext();
    const requests = [];
    let archive = { ready: false, database_exists: false, uploaded: false, initialization_mode: null, initialization_state: 'missing', legacy_migration_available: true };
    context.fetch = async (url, options = {}) => {
        requests.push([url, options.method || 'GET']);
        if (url === '/api/setup/archive/empty') {
            archive = { ...archive, ready: true, database_exists: true, initialization_mode: 'empty', initialization_state: 'complete' };
            return { ok: true, async json() { return {}; } };
        }
        if (url === '/api/setup/complete') return { ok: true, async json() { return { completed: true, reload: false }; } };
        throw new Error(`unexpected request: ${url}`);
    };
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = withSetupDialog(context, immediateComponent(tweetApp()));
    app.setupData = { archive, auth: { verified: false }, web: { password_configured: true } };
    app.fetchSetup = async () => {};
    app.fetchActivityStatus = async () => {};

    assert.equal(app.setupArchiveStatus, 'none');
    assert.equal(app.setupArchiveAction.disabled, true);

    // Choosing an empty archive only selects it; nothing is created yet.
    app.chooseSetupArchive('empty');
    assert.equal(app.setupArchiveStatus, 'empty-selected');
    assert.equal(app.setupArchiveAction.label, 'Continue');
    assert.equal(app.setupArchiveStatusText, '', 'selecting adds no line, so the layout stays put');
    // Continue confirms the choice; declining stays on step 1.
    const confirms = [];
    app.askSetupConfirm = async options => { confirms.push(options.title); return confirms.length > 1; };
    await app.setupArchiveAction.run();
    assert.equal(app.setupFlowStep, 1);
    await app.setupArchiveAction.run();
    assert.equal(app.setupFlowStep, 2);
    assert.deepEqual(confirms, ['Start with an empty archive?', 'Start with an empty archive?']);
    assert.equal(requests.length, 0);

    // Going back, the choice can be switched or undone.
    app.goToSetupStep(1);
    app.chooseSetupArchive('migration');
    assert.equal(app.setupArchiveAction.label, 'Start migration');
    app.chooseSetupArchive('migration');
    assert.equal(app.setupArchiveStatus, 'none');

    // The empty archive is created as part of finishing.
    app.chooseSetupArchive('empty');
    app.setupSkipAuth = true;
    await app.finishSetup();
    assert.deepEqual(requests.map(([url]) => url), ['/api/setup/archive/empty', '/api/setup/complete']);
    assert.equal(app.setupArchiveChoice, null);
});

test('first-run setup resumes at the first unfinished step after a reload', async () => {
    let auth = { verified: false, configured: false, values: { auth_token: '', ct0: '', user_id: '' } };
    let archive = { ready: false, uploaded: false, initialization_mode: null };
    const load = async () => {
        const context = browserContext();
        context.fetch = async () => ({ ok: true, async json() { return { required: true, completed: false, web: {}, auth, archive }; } });
        const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
        const app = withSetupDialog(context, immediateComponent(tweetApp()));
        await app.fetchSetup();
        return app;
    };
    assert.equal((await load()).setupFlowStep, 1);
    archive = { ...archive, ready: true, initialization_mode: 'import' };
    assert.equal((await load()).setupFlowStep, 2);
    auth = { ...auth, verified: true };
    const app = await load();
    assert.equal(app.setupFlowStep, 3);
    // Later polls do not move someone who went back.
    app.goToSetupStep(1);
    await app.fetchSetup();
    assert.equal(app.setupFlowStep, 1);
});

test('setup schedule step saves through Setup before the scheduler starts', async () => {
    const context = browserContext();
    const requests = [];
    context.fetch = async (url, options = {}) => {
        requests.push([url, options.method || 'GET', options.body ? JSON.parse(options.body) : null]);
        if (url === '/api/setup/schedule' && !options.method) {
            return { ok: true, async json() { return { config: { enabled: false, cadence: 'daily', time: '03:00' }, collections: ['bookmarks'] }; } };
        }
        if (url === '/api/setup/schedule') {
            const body = JSON.parse(options.body);
            return { ok: true, async json() { return { config: body.schedule, collections: body.collections }; } };
        }
        throw new Error(`unexpected request: ${url}`);
    };
    const { tweetApp, nookToggleSyncCollection, nookScheduleHeadline } = loadScripts(
        context, appScriptsWith('header.js', 'settings.js'), '({tweetApp, nookToggleSyncCollection, nookScheduleHeadline})');
    const app = withSetupDialog(context, immediateComponent(tweetApp()));
    app.setupData = { completed: false, archive: {}, auth: { verified: false }, web: {} };
    app.showSetupModal = true;
    app.setupFlowStep = 3;

    await app.loadSetupSchedule();
    assert.equal(app.scheduleForm.enabled, false);
    assert.deepEqual([...app.nookSyncCollections], ['bookmarks']);
    // The simple step states the default without the random start window.
    assert.equal(app.setupScheduleSummary, 'Off. When on, syncs every day at 3:00 AM.');

    // What to sync stays local until Continue; the gated config API is not called.
    await nookToggleSyncCollection(app, 'tweets');
    assert.deepEqual([...app.nookSyncCollections], ['bookmarks', 'tweets']);
    app.scheduleForm.enabled = true;
    app.scheduleForm.randomize_time = true;
    app.scheduleForm.random_offset_max_hours = 2;
    assert.equal(app.setupScheduleSummary, 'Syncs every day at 3:00 AM.');
    assert.match(nookScheduleHeadline(app), /later/, 'Settings still describes the random start');

    await app.saveSetupSchedule();
    const saved = requests.at(-1);
    assert.deepEqual([saved[0], saved[1]], ['/api/setup/schedule', 'PUT']);
    assert.equal(saved[2].schedule.enabled, true);
    assert.deepEqual(saved[2].collections, ['bookmarks', 'tweets']);
    assert.equal(requests.some(([url]) => url === '/api/config'), false);
    assert.equal(app.setupFlowStep, 4);
    assert.equal(app.scheduleHasChanges(), false);

    // Setup has five steps.
    app.goToSetupStep(5);
    assert.equal(app.setupFlowStep, 5);
    app.goToSetupStep(6);
    assert.equal(app.setupFlowStep, 5);
});

test('setup replay source connects, toggles placeholder media, and disconnects', async () => {
    const context = browserContext();
    const requests = [];
    let skipQuotes = false, skipPreviews = false;
    const replay = (connected, placeholder = true) => ({
        tools: true,
        replay: connected
            ? { connected: true, url: 'http://192.168.1.20:8000', password: '********', placeholder_media: placeholder, skip_quote_expansion: skipQuotes, skip_link_previews: skipPreviews,
                status: { bookmarks_pages: 3, likes_pages: 2, thread_captures: 9 } }
            : { connected: false, url: null, password: null, placeholder_media: true, status: null },
    });
    let fail = true;
    context.fetch = async (url, options = {}) => {
        const body = options.body ? JSON.parse(options.body) : undefined;
        requests.push([url, options.method, body]);
        if (url === '/api/setup/replay' && options.method === 'PUT') {
            if (fail) return { ok: false, async json() { return { detail: 'The replay source rejected the password (its Web password).' }; } };
            skipQuotes = body.skip_quote_expansion;
            skipPreviews = body.skip_link_previews;
            return { ok: true, async json() { return replay(true, body.placeholder_media); } };
        }
        if (url === '/api/setup/replay/placeholder-media') return { ok: true, async json() { return replay(true, body.placeholder_media); } };
        if (url === '/api/setup/replay/skip-link-previews') {
            skipPreviews = body.skip_link_previews;
            return {ok: true, json: async () => replay(true)};
        }
        if (url === '/api/setup/replay/skip-quote-expansion') {
            skipQuotes = body.skip_quote_expansion;
            return {ok: true, async json() { return replay(true); }};
        }
        if (url === '/api/setup/replay' && options.method === 'DELETE') return { ok: true, async json() { return replay(false); } };
        throw new Error(`unexpected request: ${url}`);
    };
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = withSetupDialog(context, immediateComponent(tweetApp()));
    app.setupData = { dev: replay(false), archive: {}, auth: {} };

    assert.equal(app.setupReplayConnected, false);
    await app.connectSetupReplay();
    assert.equal(requests.length, 0, 'an empty address sends nothing');

    // Before connecting, the checkbox only changes what will be sent.
    await app.setSetupReplayPlaceholder(false);
    assert.equal(app.setupReplayPlaceholder, false);
    assert.equal(requests.length, 0);

    assert.equal(app.setupReplaySkipPreviews, false);
    await app.setSetupReplaySkipPreviews(true);
    assert.equal(app.setupReplaySkipPreviews, true);
    assert.equal(app.setupReplaySkipQuotes, false);
    await app.setSetupReplaySkipQuotes(true);
    assert.equal(app.setupReplaySkipQuotes, true);
    assert.equal(requests.length, 0);

    app.setupReplayForm.url = ' 192.168.1.20:8000 ';
    app.setupReplayForm.password = 'secret';
    await app.connectSetupReplay();
    assert.match(app.setupReplayError, /rejected the password/);
    assert.equal(app.setupReplayConnected, false);
    assert.equal(app.setupReplayForm.password, 'secret', 'a failed test keeps the typed password');

    fail = false;
    await app.connectSetupReplay();
    assert.deepEqual(requests.at(-1), ['/api/setup/replay', 'PUT', { url: '192.168.1.20:8000', password: 'secret', placeholder_media: false, skip_quote_expansion: true, skip_link_previews: true }]);
    assert.equal(app.setupReplayConnected, true);
    assert.equal(app.setupReplayError, '');
    assert.equal(app.setupReplayForm.password, '');
    assert.equal(app.setupReplaySummary, 'Connected to http://192.168.1.20:8000. It can replay 3 bookmark pages, 2 like pages, 9 threads.');

    // Once connected, the checkbox saves straight away.
    await app.setSetupReplayPlaceholder(true);
    assert.deepEqual(requests.at(-1), ['/api/setup/replay/placeholder-media', 'PUT', { placeholder_media: true }]);
    assert.equal(app.setupReplayPlaceholder, true);
    assert.equal(app.setupReplaySkipQuotes, true);
    await app.setSetupReplaySkipQuotes(false);
    assert.deepEqual(requests.at(-1), ['/api/setup/replay/skip-quote-expansion', 'PUT', {skip_quote_expansion: false}]);
    assert.equal(app.setupReplaySkipPreviews, true);
    await app.setSetupReplaySkipPreviews(false);
    assert.deepEqual(requests.at(-1), ['/api/setup/replay/skip-link-previews', 'PUT', {skip_link_previews: false}]);
    assert.equal(app.setupReplaySkipPreviews, false);
    assert.equal(app.setupReplaySkipQuotes, false);
    const markup = pageHtml();
    assert.match(markup, /Use placeholder media[\s\S]*Skip quote expansion during sync[\s\S]*setSetupReplaySkipQuotes/);

    await app.disconnectSetupReplay();
    assert.equal(app.setupReplayConnected, false);
    assert.equal(app.setupReplayForm.url, 'http://192.168.1.20:8000', 'the address is kept for reconnecting');
});

test('setup password step validates matching passwords before finishing', async () => {
    const context = browserContext();
    const bodies = [];
    context.fetch = async (url, options = {}) => {
        assert.equal(url, '/api/setup/complete');
        bodies.push(JSON.parse(options.body));
        return { ok: true, async json() { return { completed: true, reload: false }; } };
    };
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = withSetupDialog(context, immediateComponent(tweetApp()));
    app.setupData = { web: { password_configured: false }, archive: {}, auth: {} };

    assert.equal(app.setupCanFinish, false);
    app.setupPassword = 'secret-1';
    app.setupPasswordConfirm = 'secret';
    assert.equal(app.setupPasswordMismatch, false, 'a prefix still being typed is not an error');
    assert.equal(app.setupCanFinish, false);
    app.setupPasswordConfirm = 'secret-2';
    assert.equal(app.setupPasswordMismatch, true);
    await app.finishSetup();
    assert.equal(bodies.length, 0);

    app.setupPasswordConfirm = 'secret-1';
    assert.equal(app.setupCanFinish, true);
    await app.finishSetup();
    assert.deepEqual(bodies, [{ skip_auth: false, password: 'secret-1', confirm_password: 'secret-1' }]);

    // An existing password may be kept by leaving both fields empty.
    app.setupData.web.password_configured = true;
    assert.equal(app.setupCanFinish, true);

    // No password is an explicit choice; typed passwords are not sent with it.
    app.setupData.web = { password_configured: false };
    app.setupPassword = 'secret-1';
    app.setupPasswordConfirm = 'other';
    app.setupNoPassword = true;
    assert.equal(app.setupCanFinish, true);
    assert.match(app.setupNoPasswordWarning, /other devices on your network/);
    app.setupData.web = { password_configured: true };
    assert.match(app.setupNoPasswordWarning, /other devices on your network.*current password is removed/);
    // Finishing without a password asks first; declining sends nothing.
    const sent = bodies.length;
    app.askSetupConfirm = async options => { assert.equal(options.body, app.setupNoPasswordWarning); return false; };
    await app.finishSetup();
    assert.equal(bodies.length, sent);
    app.askSetupConfirm = async () => true;
    await app.finishSetup();
    assert.deepEqual(bodies.at(-1), { skip_auth: false, no_password: true });
});

test('setup cookie help loads the generated guide and opens on the current browser', async () => {
    const context = browserContext();
    const help = JSON.parse(fs.readFileSync(path.join(ROOT, 'tweetnook/web/static/setup-help.json'), 'utf8'));
    let loads = 0;
    context.fetch = async url => {
        assert.equal(url, '/static/setup-help.json');
        loads++;
        return { ok: true, async json() { return help; } };
    };
    context.navigator = { ...(context.navigator || {}), userAgent: 'Mozilla/5.0 (Macintosh) AppleWebKit/605.1.15 Version/18.0 Safari/605.1.15' };
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = withSetupDialog(context, immediateComponent(tweetApp()));

    await app.openSetupHelp();
    assert.equal(app.setupHelpOpen, true);
    assert.equal(app.setupHelp.browsers[app.setupHelpBrowser].name, 'Safari');
    assert.equal(app.setupHelpDefaultBrowser('Mozilla/5.0 Firefox/131.0'), help.browsers.findIndex(b => b.name === 'Firefox'));
    assert.equal(app.setupHelpDefaultBrowser('Mozilla/5.0 Chrome/129.0 Safari/537.36'), 0);
    assert.equal(app.setupHelpTabLabel(help.browsers[0]), 'Chrome');
    assert.equal(app.setupHelpAlsoFor(help.browsers[0]), 'Also for Chromium, Helium, Microsoft Edge, Opera, Brave.');
    assert.equal(app.setupHelpAlsoFor({ name: 'Safari' }), '');

    // Escape closes the help sheet before it would close Setup.
    app.showSetupModal = true;
    app.closeSetup();
    assert.equal(app.setupHelpOpen, false);
    assert.equal(app.showSetupModal, true);
    await app.openSetupHelp();
    assert.equal(loads, 1, 'the guide is fetched once');
});

test('setup uses a dedicated responsive X-style modal shell', () => {
    const html = pageHtml();
    const css = pageCss();
    const js = readScripts(pageScripts());
    const settingsMarkup = html.slice(html.indexOf('<!-- Settings Modal'), html.indexOf('<!-- Setup Modal -->'));
    const settingsJs = fs.readFileSync(path.join(JS_DIR, 'settings.js'), 'utf8');
    const setupMarkup = html.slice(html.indexOf('<!-- Setup Modal -->'), html.indexOf('<!-- Automated Tagging Spend History Modal -->'));

    assert.match(settingsMarkup, /x-show="showSettingsModal"/);
    assert.match(settingsMarkup, /@click="nookSettingsNavigate\(\$data, 'setup'\)"/);
    assert.match(settingsJs, /if \(key === 'setup' && !app\.isDemo\) app\.fetchSetup\(\);/);
    assert.match(settingsMarkup, /@click="rerunSetup\(\)"/);
    assert.match(setupMarkup, /x-show="showSetupModal"/);
    assert.match(setupMarkup, /class="setup-desktop-required" role="note"/);
    assert.match(setupMarkup, /@click="clearSetupArchive\(\)"/);
    assert.match(setupMarkup, /@click="goToSetupStep\(setupFlowStep - 1\)"/);
    assert.match(setupMarkup, /class="setup-modal-footer"/);
    // The replay source section only appears with developer tools, and replay mode is always labelled.
    assert.match(setupMarkup, /<section class="setup-options setup-replay" x-show="setupData\.dev\?\.tools"/);
    assert.match(setupMarkup, /x-show="setupReplayConnected"[^>]*class="nook-demo-badge setup-replay-badge">Replay mode</);
    assert.match(html, /x-show="setupReplayConnected" x-cloak class="nook-demo-badge"[^>]*>Replay mode</);
    // Going back to step 1 after an earlier choice still offers an archive upload.
    assert.match(setupMarkup, /class="setup-upload-zone" x-show="!setupUploading && !setupData\.archive\.uploaded && setupArchiveStatus !== 'importing'"/);
    assert.doesNotMatch(setupMarkup, /<details/);
    assert.doesNotMatch(js, /alert\(error\.message\)|confirm\('Clear the uploaded/);
    assert.match(setupMarkup, /@click="if \(!setupAuthSuccessPending\) saveSetupAuth\(\)" :disabled="setupSavingAuth"/);
    assert.match(css, /@media \(max-width: 767px\)[\s\S]*?\.setup-modal-progress,[\s\S]*?\.setup-modal-content\s*\{ display: none !important; \}/);
    assert.match(js, /openSetup\(\)\s*\{[\s\S]*?this\.showSetupModal = true;[\s\S]*?this\.showSettingsModal = false;/);
    assert.match(js, /openSettings\(\)\s*\{[\s\S]*?this\.showSetupModal = false;[\s\S]*?this\.showSettingsModal = true;/);
});

test('first-run gating loads archive resources only after completion and preserves edited cookies during polling', async () => {
    const context = browserContext();
    let required = true, ready = false;
    context.fetch = async (url) => {
        assert.equal(url, '/api/setup');
        return { ok: true, async json() { return {
            required, completed: !required, web: {password_configured: !required},
            auth: {configured: false, verified: false, values: {auth_token: '', ct0: '', user_id: ''}},
            archive: {ready, uploaded: false},
        }; } };
    };
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = withSetupDialog(context, immediateComponent(tweetApp()));
    const calls = [];
    for (const method of ['fetchTweets', 'fetchStats', 'fetchGlobalTags', 'fetchArchiveEnrichmentStatus', 'fetchAutomatedTagging']) app[method] = async () => calls.push(method);
    let appliedDeepLinks = 0;
    app.splitPanel = false;
    app.pendingRoute = { viewMode: 'thread', tweetId: '123' };
    app.pendingRouteState = { viewMode: 'thread', tweetId: '123', tweetNookDepth: 0 };
    app.showFullThread = async tweetId => {
        assert.equal(tweetId, '123');
        appliedDeepLinks++;
    };
    await app.fetchSetup();
    assert.equal(app.setupForced, true);
    assert.equal(app.showSetupModal, true);
    assert.equal(app.showSettingsModal, false);
    app.closeSetup();
    assert.equal(app.showSetupModal, true);
    app.setupAuth.auth_token = 'edited';
    ready = true;
    await app.fetchSetup();
    assert.equal(app.setupAuth.auth_token, 'edited');
    assert.deepEqual(calls, []);
    required = false;
    await app.fetchSetup();
    assert.deepEqual(calls, ['fetchTweets', 'fetchStats', 'fetchArchiveEnrichmentStatus', 'fetchAutomatedTagging']);
    assert.equal(appliedDeepLinks, 1);
    await app.fetchSetup();
    assert.equal(calls.length, 4);
    assert.equal(appliedDeepLinks, 1);
    const before = JSON.stringify(app.setupData);
    app.rerunSetup();
    assert.equal(app.setupFlowStep, 1);
    assert.equal(app.setupRerun, true);
    assert.equal(app.settingsTab, 'setup');
    assert.equal(app.showSettingsModal, false);
    assert.equal(app.showSetupModal, true);
    assert.equal(JSON.stringify(app.setupData), before);
    app.closeSetup();
    assert.equal(app.showSetupModal, false);
});

test('Settings and Setup stay above an expanded activity drawer', () => {
    const html = pageHtml();
    const headerCss = fs.readFileSync(path.join(ROOT, 'tweetnook/web/static/css/header.css'), 'utf8');
    const settingsCss = fs.readFileSync(path.join(ROOT, 'tweetnook/web/static/css/settings.css'), 'utf8');
    const zIndex = (css, selector) => Number(css.match(new RegExp(`${selector.replace('.', '\\.')}\\s*\\{[^}]*z-index:\\s*(\\d+)`))?.[1]);

    assert.match(html, /class="settings-modal-backdrop nook-settings-backdrop"/);
    assert.match(html, /class="settings-modal-backdrop fixed inset-0 z-\[100\]/);
    assert.equal(zIndex(settingsCss, '.nook-settings-backdrop'), 100);
    assert.ok(zIndex(headerCss, '.nook-sync-panel') < 100);
});

test('legacy setup reload queues completion without flashing it before navigation', async () => {
    const context = browserContext();
    let reloaded = false;
    context.window.location = {reload() { reloaded = true; }};
    context.fetch = async (url, options) => {
        assert.equal(url, '/api/setup/complete');
        assert.deepEqual(JSON.parse(options.body), {password: 'test-secret', confirm_password: 'test-secret', skip_auth: true});
        return {ok: true, async json() {return {completed: true, reload: true};}};
    };
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = withSetupDialog(context, immediateComponent(tweetApp()));
    app.setupForced = true;
    app.setupSkipAuth = true;
    app.setupPassword = app.setupPasswordConfirm = 'test-secret';
    app.setupData = {web: {password_configured: false}};
    await app.finishSetup();
    assert.equal(reloaded, true);
    assert.equal(app.setupForced, false);
    assert.equal(app.setupPassword, '');
    assert.equal(context.sessionStorage.getItem('tweetnook.setup-completion'), 'pending');
    assert.equal(app.setupCompletionVisible, false);
    assert.equal(app.setupFlowStep, 1, 'the explanation is separate from the counted steps');
});

test('setup finishes in place and refreshes archive resources and progress without reloading', async () => {
    const context = browserContext();
    const requests = [];
    const data = { completed: true, required: false, auth: { verified: true, values: {} },
        web: { password_configured: true }, archive: { ready: true, initialization_mode: 'empty' } };
    context.sessionStorage.setItem = () => { throw new Error('Storage disabled'); };
    context.window.location.reload = () => assert.fail('Setup must not reload');
    context.fetch = async (url) => {
        requests.push(url);
        if (url === '/api/setup/complete') return { ok: true, json: async () => ({ completed: true, reload: false }) };
        assert.equal(url, '/api/setup');
        return { ok: true, json: async () => data };
    };
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = withSetupDialog(context, immediateComponent(tweetApp()));
    app.setupForced = true;
    app.setupPassword = app.setupPasswordConfirm = 'test-secret';
    app.setupData = { web: { password_configured: false } };
    let resources = 0, progress = 0;
    app.loadArchiveResources = async () => { resources++; };
    app.fetchActivityStatus = async () => { progress++; };
    await app.finishSetup();
    assert.deepEqual(requests, ['/api/setup/complete', '/api/setup']);
    assert.equal(resources, 1);
    assert.equal(progress, 1);
    assert.equal(app.setupFinishing, false);
    assert.equal(app.setupCompletionVisible, true);
    assert.equal(app.showSetupModal, true);
    assert.equal(app.setupForced, false);
    assert.equal(app.archiveReady, true);
    assert.equal(app.setupData.web.password_configured, true);
    assert.equal(app.setupPassword, '');
    assert.equal(app.setupPasswordConfirm, '');
    app.closeSetup();
    assert.equal(app.showSetupModal, false);
    await app.fetchSetup();
    assert.equal(app.setupCompletionVisible, false, 'polling must not reopen completion');
});

test('setup completion survives the password reload once and supports both real handoffs', async () => {
    const context = browserContext();
    const data = { completed: true, required: false, auth: { verified: false, values: {} }, archive: { ready: true, initialization_mode: 'empty' } };
    context.fetch = async () => ({ ok: true, json: async () => data });
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = withSetupDialog(context, immediateComponent(tweetApp()));
    app.archiveResourcesLoaded = true;
    context.sessionStorage.setItem('tweetnook.setup-completion', 'pending');
    await app.fetchSetup();
    assert.equal(app.setupCompletionVisible, true);
    assert.equal(app.showSetupModal, true);
    assert.equal(app.setupForced, false);
    assert.equal(context.sessionStorage.getItem('tweetnook.setup-completion'), null);
    app.setupCompletionAction();
    assert.equal(app.setupCompletionVisible, false);
    assert.equal(app.setupFlowStep, 2);
    assert.equal(app.setupRerun, true);
    app.closeSetup();
    await app.fetchSetup();
    assert.equal(app.showSetupModal, false, 'polling must not reopen a dismissed completion page');
    data.auth.verified = true;
    app.showSetupCompletion();
    app.setupCompletionAction();
    assert.equal(app.activityDrawerOpen, true);
    assert.equal(app.showSetupModal, false);
    assert.equal(app.setupCompletionVisible, false);
});

test('failed setup completion neither reloads nor queues the explanation', async () => {
    const context = browserContext();
    context.fetch = async () => ({ ok: false, json: async () => ({ detail: 'Cannot finish' }) });
    let reloads = 0;
    context.window.location.reload = () => reloads++;
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = withSetupDialog(context, immediateComponent(tweetApp()));
    app.setupData = { web: { password_configured: true } };
    app.setupForced = true;
    await app.finishSetup();
    assert.equal(app.setupError, 'Cannot finish');
    assert.equal(app.setupCompletionVisible, false);
    assert.equal(app.setupForced, true);
    assert.equal(reloads, 0);
    assert.equal(context.sessionStorage.getItem('tweetnook.setup-completion'), null);
});

test('setup still shows completion before reloading when browser storage is disabled', async () => {
    const context = browserContext();
    context.sessionStorage.setItem = () => { throw new Error('Storage disabled'); };
    context.fetch = async () => ({ ok: true, json: async () => ({ completed: true, reload: true }) });
    let reloads = 0;
    context.window.location.reload = () => reloads++;
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = withSetupDialog(context, immediateComponent(tweetApp()));
    app.setupData = { web: { password_configured: true } };
    await app.finishSetup();
    assert.equal(app.setupCompletionVisible, true);
    assert.equal(reloads, 0);
    app.closeSetup();
    assert.equal(reloads, 1);
});

test('setup completion explains import with enrichment, media, sync and skipped steps', () => {
    const context = browserContext();
    const { nookSetupCompletion } = loadScripts(context, ['setup-completion.js'], '({nookSetupCompletion})');
    const describe = (archive, extra = {}) => nookSetupCompletion({ setupData: {
        archive, auth: { verified: true }, schedule: { enabled: false, collections: ['bookmarks', 'likes'] },
    }, ...extra });
    const imported = { initialization_mode: 'import', initialization_state: 'complete', imported: true };
    const importing = describe({ ...imported, initialization_state: 'running', media_state: 'waiting_for_import' });
    assert.equal(importing.stages.map(stage => stage.key).join(','), 'import,media,sync');
    assert.equal(importing.stages.map(stage => stage.state).join(','), 'active,queued,queued');
    assert.match(importing.stages[0].description, /fill in missing text/);
    assert.match(importing.stages[1].icon, /data-icon="photo"/);
    const mediaInImport = describe({ ...imported, initialization_state: 'running', media_state: 'running' });
    assert.equal(mediaInImport.stages.map(stage => stage.state).join(','), 'done,active,queued');
    const media = describe({ ...imported, media_state: 'running', followup_state: 'waiting_for_worker' },
        { activity: { kind: 'media' } });
    assert.equal(media.stages.map(stage => stage.state).join(','), 'done,active,queued');
    assert.match(media.note, /Media is downloading; the first sync follows automatically/);
    assert.match(media.time, /hours or potentially days.*limits/);
    assert.match(media.usability, /until the first sync finishes/);
    const queued = describe({ ...imported, media_state: 'queued' }, { lastActivity: { kind: 'enrich', success: true } });
    assert.equal(queued.stages[2].state, 'queued', 'an older first sync must not complete a new import');
    const syncing = describe({ ...imported, media_state: 'complete', followup_state: 'running' },
        { activity: { kind: 'enrich' } });
    assert.equal(syncing.stages.map(stage => stage.state).join(','), 'done,done,active');
    const failed = describe({ ...imported, media_state: 'complete', followup_state: 'failed',
        warnings: ['Storage is full. Free disk space, then resume.'] },
    { lastActivity: { title: 'tweetnook import enrich', success: false } });
    assert.equal(failed.stages.at(-1).state, 'attention');
    assert.match(failed.note, /Storage is full/);
    const done = describe({ ...imported, media_state: 'complete', followup_state: 'complete' },
        { lastActivity: { kind: 'enrich', success: true } });
    assert.equal(done.stages.map(stage => stage.state).join(','), 'done,done,done');
    const mediaFailed = describe({ ...imported, media_state: 'failed' });
    assert.equal(mediaFailed.stages[1].state, 'attention');
    assert.match(mediaFailed.note, /Download media again/);
    const stoppedMediaImport = describe({ ...imported, media_state: 'failed', followup_state: 'failed',
        warnings: ['Storage is full. Free disk space, then run Sync now to resume.'] },
    { lastActivity: { kind: 'import', success: false } });
    assert.equal(stoppedMediaImport.stages.map(stage => stage.state).join(','), 'done,attention,attention');
    assert.match(stoppedMediaImport.note, /Storage is full.*Sync now/);
    const importFailed = describe({ ...imported, initialization_state: 'failed' });
    assert.equal(importFailed.stages[0].state, 'attention');
    // Import, enrichment and media need no account; only the first sync waits for one.
    const offline = describe({}, { setupData: { auth: { verified: false }, archive: { ...imported, media_state: 'running' } } });
    assert.equal(offline.needsConnection, true);
    assert.equal(offline.stages.map(stage => stage.state).join(','), 'done,active,connection');
    assert.match(offline.note, /first sync starts once you connect X/);
    const manual = describe({ initialization_mode: 'empty' });
    assert.equal(manual.stages.length, 1);
    assert.equal(manual.stages[0].label, 'Not started');
    assert.match(manual.note, /Nothing is running/);
    const scheduled = describe({}, { setupData: { auth: { verified: true }, archive: { initialization_mode: 'empty' }, schedule: { enabled: true, collections: ['tweets'] } } });
    assert.equal(scheduled.stages[0].label, 'Scheduled');
    assert.match(scheduled.stages[0].description, /authored posts/);
    assert.doesNotMatch(scheduled.stages[0].description, /bookmarks/);
    const migration = describe({ initialization_mode: 'migration', initialization_state: 'complete' });
    assert.equal(migration.stages.map(stage => stage.title).join(','), 'Migration,First sync');
});

test('a first feed page is shown without waiting for Setup status', async () => {
    const start = async status => {
        const context = browserContext();
        context.fetch = url => (url.startsWith('/api/tweets?')
            ? Promise.resolve({ ok: status === 200, status, json: async () => ({ tweets: [] }) })
            : new Promise(() => {}));
        const app = immediateComponent(loadScripts(context, appScripts(), 'tweetApp')());
        app.fetchNotices = async () => {};
        app.fetchActivityStatus = async () => {};
        let loads = 0;
        app.loadArchiveResources = async () => { loads++; };
        app.initApp();
        await new Promise(resolve => setImmediate(resolve));
        return { app, loads };
    };
    const ready = await start(200);
    assert.equal(ready.app.archiveReady, true);
    assert.equal(ready.app.setupForced, false);
    assert.equal(ready.loads, 1);
    const gated = await start(409);
    assert.notEqual(gated.app.archiveReady, true, 'Setup status still decides when the server refuses');
    assert.equal(gated.loads, 0);
});

test('setup polls fill an unset archive ID without replacing a user entry', async () => {
    const context = browserContext();
    context.fetch = async () => ({ok: true, json: async () => ({
        required: true, auth: {values: {user_id: '4242'}}, archive: {ready: true},
    })});
    const {tweetApp} = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.setupAuth = {auth_token: 'synthetic', ct0: 'synthetic', user_id: ''};
    await app.fetchSetup();
    assert.equal(app.setupAuth.user_id, '4242');
    app.setupAuth.user_id = '5151';
    await app.fetchSetup();
    assert.equal(app.setupAuth.user_id, '5151');
    const markup = pageHtml();
    assert.match(markup, /Settings<\/h2><span x-show="isDemo"/);
    assert.doesNotMatch(markup, /Filled from your imported archive when available/);
});

test('enrichment status waits for setup and tolerates the archive attachment race', async () => {
    const context = browserContext();
    let calls = 0, errors = 0;
    context.console.error = () => { errors++; };
    context.fetch = async () => { calls++; return {
        ok: false, status: 503, json: async () => ({detail: {code: 'archive_not_ready'}}),
    }; };
    const {tweetApp} = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.archiveReady = false;
    await app.fetchArchiveEnrichmentStatus();
    assert.equal(calls, 0);
    app.archiveReady = true;
    app.setupForced = true;
    await app.fetchArchiveEnrichmentStatus();
    assert.equal(calls, 0);
    app.setupForced = false;
    await app.fetchArchiveEnrichmentStatus();
    assert.equal(calls, 1);
    assert.equal(errors, 0);
    assert.equal(app.archiveEnrichmentRefreshing, false);
});

run();
