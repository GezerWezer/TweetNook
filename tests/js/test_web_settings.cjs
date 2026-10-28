'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { appScripts, appScriptsWith, pageHtml, pageScripts, readScripts } = require('./page_scripts.cjs');
const {
    FakeElement,
    ROOT,
    browserContext,
    immediateComponent,
    loadScripts,
    test,
    run,
} = require('./web_harness.cjs');

test('theme color helpers and catalog generate complete deterministic themes', () => {
    const context = browserContext();
    const exported = loadScripts(
        context,
        ['themes.js'],
        '({_hexToRgb, _rgbToHex, _rgbToHsl, _hslToRgb, _adjustL, _mix, _rgba, _surface, _generateTheme, THEME_SEEDS, THEMES})',
    );

    assert.deepEqual(Array.from(exported._hexToRgb('#1d9bf0')), [29, 155, 240]);
    assert.equal(exported._rgbToHex(29, 155, 240), '#1d9bf0');
    const hsl = exported._rgbToHsl(29, 155, 240);
    const rgb = exported._hslToRgb(...hsl);
    assert.equal(exported._rgbToHex(...rgb), '#1d9bf0');
    assert.equal(exported._mix('#000000', '#ffffff', 0.5), '#808080');
    assert.equal(exported._rgba('#010203', 0.5), 'rgba(1,2,3,0.5)');
    assert.match(exported._adjustL('#000000', 10), /^#[0-9a-f]{6}$/);
    assert.match(exported._surface('#000000', '#71767b', 8), /^#[0-9a-f]{6}$/);

    const themeKeys = Object.keys(exported.THEMES);
    assert.ok(themeKeys.length >= 15);
    assert.deepEqual(themeKeys, Object.keys(exported.THEME_SEEDS));
    for (const theme of Object.values(exported.THEMES)) {
        for (const key of [
            '--bg-primary',
            '--bg-secondary',
            '--text-primary',
            '--text-secondary',
            '--border-color',
            '--accent-color',
            '--danger-color',
            '--destructive-color',
        ]) {
            assert.ok(theme[key], `missing ${key}`);
        }
    }
    assert.ok(exported.THEMES['classic-dark']._accents.length >= 6);
    // Classic danger is the like pink, so Delete uses X's red; other themes reuse their danger red.
    assert.equal(exported.THEMES['classic-dark']['--destructive-color'], '#f4212e');
    assert.equal(exported.THEMES['classic-light']['--destructive-color'], '#f4212e');
    assert.equal(exported.THEMES['classic-light']['--danger-color'], '#f91880');
    assert.equal(exported.THEMES.dracula['--destructive-color'], exported.THEMES.dracula['--danger-color']);
});

test('demo follows system appearance until an explicit theme is chosen', () => {
    for (const dark of [false, true]) {
        const context = browserContext();
        context.window.TWEETNOOK_DEMO = true;
        let onChange;
        const scheme = {
            matches: dark,
            addEventListener(name, callback) {
                assert.equal(name, 'change');
                onChange = callback;
            },
        };
        context.window.matchMedia = query => {
            // Startup also asks whether this is a touch screen (zoom blocking).
            if (query === '(pointer: coarse)') return { matches: false };
            assert.equal(query, '(prefers-color-scheme: dark)');
            return scheme;
        };
        const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
        const start = () => {
            const app = immediateComponent(tweetApp());
            app.fetchSetup = async () => {};
            app.fetchNotices = async () => {};
            app.fetchActivityStatus = async () => {};
            app.fetchTweets = async () => {};
            app.fetchStats = async () => {};
            app.fetchArchiveEnrichmentStatus = async () => {};
            app.fetchAutomatedTagging = async () => {};
            app.initApp();
            return app;
        };
        const app = start();
        assert.equal(app.currentTheme, dark ? 'classic-dark' : 'classic-light');
        assert.equal(app.darkMode, dark);
        assert.equal(context.__state.cssProperties.get('color-scheme'), dark ? 'dark' : 'light');
        assert.equal(context.__state.themeColor.content, dark ? '#000000' : '#ffffff');
        assert.equal(context.__state.rootClasses.has('light'), !dark);
        assert.equal(context.localStorage.getItem('tvx-theme'), null);
        assert.equal(app.splitPanel, true);
        assert.equal(app.shouldUseSplitPanel(), true);
        context.window.innerWidth = 390;
        assert.equal(app.shouldUseSplitPanel(), false);

        scheme.matches = !dark;
        onChange();
        assert.equal(app.currentTheme, dark ? 'classic-light' : 'classic-dark');
        assert.equal(context.__state.cssProperties.get('color-scheme'), dark ? 'light' : 'dark');
        assert.equal(context.__state.themeColor.content, dark ? '#ffffff' : '#000000');
        assert.equal(context.localStorage.getItem('tvx-theme'), null);
        assert.equal(start().currentTheme, app.currentTheme);

        app.applyTheme('classic-light');
        assert.equal(context.localStorage.getItem('tvx-theme'), 'classic-light');
        scheme.matches = true;
        onChange();
        assert.equal(app.currentTheme, 'classic-light');
        assert.equal(start().currentTheme, 'classic-light');
    }
});

test('desktop split-panel defaults respect saved layout and theme choices in demos and local archives', () => {
    for (const isDemo of [false, true]) {
        for (const savedSplit of [null, 'false', 'true']) {
            const context = browserContext();
            context.window.TWEETNOOK_DEMO = isDemo;
            context.localStorage.setItem('tvx-theme', 'classic-dark');
            if (savedSplit !== null) context.localStorage.setItem('tvx-split-panel', savedSplit);
            const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
            const app = immediateComponent(tweetApp());
            app.fetchSetup = async () => {};
            app.fetchNotices = async () => {};
            app.fetchActivityStatus = async () => {};
            app.fetchTweets = async () => {};
            app.fetchStats = async () => {};
            app.fetchArchiveEnrichmentStatus = async () => {};
            app.fetchAutomatedTagging = async () => {};
            app.initApp();
            assert.equal(app.currentTheme, 'classic-dark');
            assert.equal(context.__state.cssProperties.get('color-scheme'), 'dark');
            assert.equal(context.__state.themeColor.content, '#000000');
            assert.equal(app.splitPanel, savedSplit === null ? true : savedSplit === 'true');
        }
    }
});

test('tweet app starts with coherent list, panel, modal, and theme state', () => {
    const context = browserContext();
    const { tweetApp, statsDialog } = loadScripts(
        context,
        appScripts(),
        '({tweetApp, statsDialog})',
    );
    const app = immediateComponent(tweetApp());
    assert.equal(app.viewMode, 'list');
    assert.equal(app.page, 1);
    assert.equal(app.collectionFilter, 'likes');
    assert.equal(app.sortOrder, 'liked_latest');
    assert.equal(app.panelMode, null);
    assert.deepEqual(Array.from(app.panelStack), []);
    assert.equal(vm.runInContext('tagDialog()', context).tagModalOpen, false);
    assert.equal(app.currentTheme, 'classic-light');
    assert.equal(app.showStatsModal, false);
    const stats = statsDialog();
    assert.equal(stats.showEmptyUnavailableReasons, false);
    assert.equal(stats.statsGeneratedAt, null);
    assert.equal(stats.statsRefreshing, false);
    assert.ok(Object.keys(app.THEMES).length >= 15);
    assert.equal(app.avatarUrl('user/42'), '/api/avatar/user%2F42?v=3');
});

test('saving the avatar download setting changes avatar URLs, here and after reloads', async () => {
    const context = browserContext();
    context.fetch = async () => ({ ok: true, json: async () => ({ values: {}, explicit: [] }) });
    const app = immediateComponent(loadScripts(context, appScripts(), 'tweetApp')());
    await app.submitConfigChanges({ 'web.avatar_cache_limit_mb': 256 });
    assert.equal(app.avatarUrl('42'), '/api/avatar/42?v=3', 'other settings keep cached avatars');
    await app.submitConfigChanges({ 'web.fetch_avatars': true });
    const revision = context.localStorage.getItem('tweetnook.avatar-revision');
    assert.ok(revision);
    assert.equal(app.avatarUrl('42'), `/api/avatar/42?v=3&r=${revision}`);
    const reload = browserContext();
    reload.localStorage.setItem('tweetnook.avatar-revision', revision);
    const reloaded = immediateComponent(loadScripts(reload, appScripts(), 'tweetApp')());
    assert.equal(reloaded.avatarUrl('42'), `/api/avatar/42?v=3&r=${revision}`);
});

test('themes, accents, fonts, sizes, and split panel persist preferences', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(
        context,
        appScripts(),
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());

    app.applyTheme('dracula');
    assert.equal(context.__state.themeColor.content, '#282a36');
    assert.equal(context.__state.cssProperties.get('color-scheme'), 'dark');
    app.applyTheme('classic-light');
    assert.equal(app.currentTheme, 'classic-light');
    assert.equal(context.__state.cssProperties.get('--bg-primary'), '#ffffff');
    assert.ok(context.__state.rootClasses.has('light'));
    assert.equal(context.localStorage.getItem('tvx-theme'), 'classic-light');
    assert.equal(context.__state.themeColor.content, '#ffffff');
    assert.equal(context.__state.cssProperties.get('color-scheme'), 'light');

    const accent = app.THEMES['classic-light']._accents[1];
    app.setAccent(accent);
    assert.equal(context.__state.cssProperties.get('--accent-color'), accent.color);
    app.setFont('inter', '"Inter", sans-serif');
    assert.equal(context.__state.appendedLinks.length, 0);
    app.setFontSize('large', '18px');
    assert.equal(context.__state.cssProperties.get('--font-size-base'), '18px');
    assert.equal(app.setTweetDensity, undefined);

    app.panelStack = [{ type: 'thread' }];
    app.panelMode = 'thread';
    app.toggleSplitPanel(false);
    assert.deepEqual(Array.from(app.panelStack), []);
    assert.equal(app.panelMode, null);
});

test('tag settings bound rendered rows and reuse archive-wide counts for large catalogs', () => {
    const { nookTagsPage } = loadScripts(browserContext(), ['settings.js'], '({nookTagsPage})');
    const page = nookTagsPage();
    let reads = 0;
    page.globalTags = Array.from({ length: 20000 }, (_, index) => ({
        tag: `Tag ${String(index).padStart(5, '0')}`,
        get count() { reads++; return index + 1; },
    }));
    assert.equal(page.rows().length, 100);
    assert.equal(page.rows()[0].tag, 'Tag 19999');
    assert.equal(page.pageCount(), 200);
    assert.equal(page.pageLabel(), '1–100 of 20,000 tags');
    assert.equal(page.share(20000), 100);
    assert.equal(page.share(1), 4);
    assert.equal(page.countOf('Tag 19999'), 20000);
    assert.match(page.summary(), /200,010,000 times/);
    const warmedReads = reads;
    const seen = new Set();
    for (let index = 1; index <= page.pageCount(); index++) {
        page.page = index;
        for (const tag of page.rows()) {
            seen.add(tag.tag);
            page.share(index);
            page.countOf(tag.tag);
        }
        page.summary();
    }
    assert.equal(seen.size, 20000);
    assert.equal(reads, warmedReads, 'paging and row bars must not rescan the catalog');
    assert.equal(page.globalTags[0].tag, 'Tag 00000', 'sorting must preserve the source array');

    // More tags than can be spread into Math.max() arguments.
    page.globalTags = Array.from({ length: 150000 }, (_, index) => ({ tag: `New ${index}`, count: index + 1 }));
    assert.equal(page.share(150000), 100);
    assert.equal(page.countOf('Tag 19999'), 0);
    assert.equal(page.countOf('New 149999'), 150000);
});

test('tag settings search all pages and preserve merge selection through paging and refreshes', async () => {
    const context = browserContext();
    const requests = [];
    context.fetch = async (url, options) => { requests.push([url, options]); return { ok: true }; };
    const { nookTagsPage } = loadScripts(context, ['settings.js'], '({nookTagsPage})');
    const watchers = new Map();
    const page = Object.assign(nookTagsPage(), {
        globalTags: Array.from({ length: 205 }, (_, index) => ({ tag: `Tag ${String(index).padStart(3, '0')}`, count: index + 1 })),
        $watch: (key, callback) => watchers.set(key, callback),
        $el: { scrollTop: 500 },
        $refs: { tagListHeading: { focus() {} } },
        $nextTick: callback => callback(),
    });
    page.init();
    page.toggleSelect();
    page.toggle('Tag 204');
    page.changePage(1);
    assert.equal(page.currentPage(), 2);
    assert.equal(page.$el.scrollTop, 0);
    page.toggle(page.rows()[0].tag);
    page.changePage(1);
    assert.equal(page.rows().length, 5);
    assert.equal(page.pageLabel(), '201–205 of 205 tags');
    page.query = 'TAG 000';
    watchers.get('query')();
    assert.equal(page.currentPage(), 1);
    assert.equal(page.rows().length, 1);
    assert.equal(page.rows()[0].tag, 'Tag 000');
    assert.equal(page.selected.length, 2);
    page.query = '';
    watchers.get('query')();
    page.sort = 'name';
    watchers.get('sort')();
    assert.equal(page.rows()[0].tag, 'Tag 000');
    page.changePage(1);
    page.changePage(1);
    page.globalTags = page.globalTags.slice(0, 100);
    assert.equal(page.rows().length, 100, 'a shrinking result set must not leave a blank page');
    watchers.get('globalTags')();
    assert.equal(page.page, 1);
    page.globalTags = [{ tag: 'Tag 204', count: 205 }, { tag: 'Tag 104', count: 105 }];
    watchers.get('globalTags')();
    page.keep = 'Tag 204';
    page.fetchGlobalTags = async () => { page.globalTags = [{ tag: 'Tag 204', count: 310 }]; };
    await page.submitMerge();
    assert.equal(requests[0][0], '/api/tags/merge');
    assert.deepEqual(JSON.parse(requests[0][1].body), { primary_tag: 'Tag 204', merge_tags: ['Tag 104'] });
    assert.equal(page.countOf('Tag 204'), 310);
    assert.equal(page.selected.length, 0);
    page.confirmDelete = 'Tag 204';
    await page.deleteTag();
    assert.equal(page.rows().length, 0);
    assert.equal(page.countOf('Tag 204'), 0);
    assert.match(page.summary(), /Tag a tweet/);
});

test('tag settings only mount when open and expose accessible bounded page controls', () => {
    const html = pageHtml();
    assert.match(html, /<template x-if="showSettingsModal && settingsTab === 'tags'">/);
    assert.match(html, /<template x-for="tag in rows\(\)"/);
    assert.match(html, /aria-label="Tag pages" x-show="filteredRows\(\).length > pageSize"/);
    assert.match(html, /:disabled="currentPage\(\) <= 1" @click="changePage\(-1\)"/);
    assert.match(html, /:disabled="currentPage\(\) >= pageCount\(\)" @click="changePage\(1\)"/);
});

test('config and stats requests update their matching UI state', async () => {
    const context = browserContext();
    context.fetch = async url => ({
        ok: true,
        async json() {
            if (url === '/api/config/schema') {
                return {
                    whitelist: ['web.host'],
                    blacklist: ['auth.auth_token'],
                    full_width: ['web.host'],
                    types: { 'web.port': 'number' },
                };
            }
            if (url === '/api/config') {
                return {
                    values: {
                        auth: { auth_token: 'masked' },
                        sync: { page_delay: 2 },
                        web: { host: '127.0.0.1', port: 8000 },
                    },
                    explicit: ['web.host'],
                };
            }
            if (url === '/api/config/defaults') return { web: { port: 8000 } };
            if (url === '/api/stats/latest-sync') return { latest_sync: '2026-07-30T00:00:00Z' };
            return {};
        },
    });
    const { tweetApp } = loadScripts(
        context,
        appScripts(),
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());

    await app.fetchConfig();
    assert.equal(app.configData.web.port, 8000);
    assert.equal(app.configOriginal.web.port, 8000);
    assert.equal(app.configDefaults.web.port, 8000);
    assert.deepEqual(Array.from(app.configExplicit), ['web.host']);
    assert.equal(app.isFieldVisible('web', 'host'), true);
    assert.equal(app.isFieldVisible('auth', 'auth_token'), false);
    assert.equal(app.isFieldVisible('sync', 'page_delay'), false);
    app.showAdvancedConfig = true;
    assert.equal(app.isFieldVisible('sync', 'page_delay'), true);
    assert.equal(app.isFieldVisible('auth', 'auth_token'), false);
    assert.equal(app.getFieldType('web', 'port'), 'number');
    assert.equal(app.isFieldFullWidth('web', 'host'), true);
    assert.equal(app.isConfigExplicit('web', 'host'), true);

    await app.fetchStats();
    assert.equal(app.lastSyncFormatted, '2026-07-30T00:00:00Z');
});

test('config saves only diffs and resets only eligible explicit fields', async () => {
    const context = browserContext();
    const requests = [];
    const plain = value => JSON.parse(JSON.stringify(value));
    let failNext = false;
    const effective = {
        auth: { auth_token: '********', ct0: null, user_id: '123' },
        sync: { page_delay: 2 },
        web: { host: '127.0.0.1', port: 8000, fetch_avatars: true },
        database: { cache_size_kb: 524288, mmap_size_bytes: 1073741824 },
        tagging: { api_key: '********', enabled: false },
    };
    let explicit = ['auth.auth_token', 'sync.page_delay', 'database.cache_size_kb', 'tagging.api_key'];
    context.fetch = async (url, options = {}) => {
        if (url === '/api/config/schema') return { ok: true, async json() { return { whitelist: [], blacklist: [], full_width: [], types: {} }; } };
        if (url === '/api/config/defaults') return { ok: true, async json() { return JSON.parse(JSON.stringify(effective)); } };
        if (url === '/api/config' && !options.method) {
            return { ok: true, async json() { return { values: effective, explicit }; } };
        }
        requests.push(JSON.parse(options.body));
        if (failNext) {
            failNext = false;
            return { ok: false, async text() { return 'invalid'; } };
        }
        const changes = requests.at(-1).changes;
        for (const [field, value] of Object.entries(changes)) {
            const [section, key] = field.split('.');
            if (value === null) {
                explicit = explicit.filter(item => item !== field);
            } else {
                effective[section][key] = value;
                if (!explicit.includes(field)) explicit.push(field);
            }
        }
        return { ok: true, async json() { return { values: effective, explicit }; } };
    };
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());

    await app.fetchConfig();
    app.showAdvancedConfig = true;
    assert.deepEqual(plain(app.changedConfigValues()), {});
    await app.saveConfig();
    assert.equal(requests.length, 0);

    app.configData.web.port = 9000;
    await app.saveConfig();
    assert.deepEqual(plain(requests.at(-1)), { changes: { 'web.port': 9000 } });
    assert.equal(app.configOriginal.web.port, 9000);

    app.configData.database.mmap_size_bytes = 536870912;
    await app.saveConfig();
    assert.deepEqual(plain(requests.at(-1)), { changes: { 'database.mmap_size_bytes': 536870912 } });
    assert.equal('database.cache_size_kb' in requests.at(-1).changes, false);

    await app.resetConfigField('database', 'cache_size_kb');
    assert.deepEqual(plain(requests.at(-1)), { changes: { 'database.cache_size_kb': null } });

    await app.restoreConfigDefaults();
    assert.deepEqual(plain(requests.at(-1)), {
        changes: {
            'sync.page_delay': null,
            'database.mmap_size_bytes': null,
            'web.port': null,
        },
    });
    assert.equal('auth.auth_token' in requests.at(-1).changes, false);
    assert.equal('tagging.api_key' in requests.at(-1).changes, false);

    app.configData.web.port = 7000;
    failNext = true;
    await app.saveConfig();
    assert.equal(app.configData.web.port, 7000);
    assert.notEqual(app.configOriginal.web.port, 7000);
});

test('config boolean controls bind actual booleans', () => {
    const html = pageHtml();
    assert.match(html, /role="switch"[^>]*:aria-checked="nookConfigOn\(\$data, field\)"[^>]*@click="nookConfigToggle\(\$data, field\)"/);
    assert.doesNotMatch(html, /<option value="(?:true|false)">/);
    const { nookConfigToggle } = loadScripts(browserContext(), ['settings.js'], '({nookConfigToggle})');
    const app = { configData: { web: { fetch_avatars: true }, sync: { reply_linked_depth: 0 } } };
    nookConfigToggle(app, { path: 'web.fetch_avatars', section: 'web', key: 'fetch_avatars', type: 'boolean' });
    assert.equal(app.configData.web.fetch_avatars, false);
    nookConfigToggle(app, { path: 'web.fetch_avatars', section: 'web', key: 'fetch_avatars', type: 'boolean' });
    assert.equal(app.configData.web.fetch_avatars, true);
    // Number fields shown as a switch keep their numeric type.
    nookConfigToggle(app, { path: 'sync.reply_linked_depth', section: 'sync', key: 'reply_linked_depth', type: 'toggle01' });
    assert.equal(app.configData.sync.reply_linked_depth, 1);
});

test('the mobile viewer requests edge-to-edge layout and restores the original viewport settings', () => {
    const context=browserContext(),meta=new FakeElement('meta',[['content','width=device-width, initial-scale=1.0, viewport-fit=contain']]);
    context.document.querySelector=selector=>selector==='meta[name="viewport"]'?meta:null;
    const app=loadScripts(context,appScripts(),'tweetApp')();
    app.setLightboxViewport(true);assert.equal(meta.getAttribute('content'),'width=device-width, initial-scale=1.0, viewport-fit=cover');
    app.setLightboxViewport(true);app.setLightboxViewport(false);
    assert.equal(meta.getAttribute('content'),'width=device-width, initial-scale=1.0, viewport-fit=contain');
});

test('schedule settings bind every control to the schedule form', () => {
    const html = pageHtml();
    const start = html.indexOf('<!-- Scheduled sync settings -->');
    const end = html.indexOf('<!-- Activity logs -->');
    const scheduleHtml = html.slice(start, end);

    assert.match(html, /id="save-schedule-btn"[^>]*@click="saveScheduleSettings\(\)"/);
    assert.match(scheduleHtml, /aria-label="Sync automatically" :aria-checked="scheduleForm\.enabled" @click="scheduleForm\.enabled = !scheduleForm\.enabled; scheduleFieldChanged\(\)"/);
    assert.match(scheduleHtml, /<fieldset[^>]*:disabled="!scheduleForm\.enabled"/);
    assert.match(scheduleHtml, /scheduleForm\.cadence = cadence\.key/);
    // Dropdowns are custom listboxes (nookSelect) bound to the same form fields.
    const select = field => new RegExp(`value: \\(\\) => ${field.replace('.', '\\.')}, choose: value => \\{`);
    for (const field of ['scheduleForm.every_hours', 'scheduleTimeHour', 'scheduleTimeMinute', 'scheduleForm.day_of_month', 'scheduleForm.timezone', 'scheduleForm.random_offset_min_hours', 'scheduleForm.random_offset_max_hours']) {
        assert.match(scheduleHtml, select(field), field);
    }
    assert.match(scheduleHtml, /@click="setScheduleTimePart\('period', 'AM'\)"/);
    assert.match(scheduleHtml, /@click="scheduleForm\.weekday = index; scheduleFieldChanged\(\)"/);
    assert.match(scheduleHtml, /aria-label="Vary the start time" :aria-checked="scheduleForm\.randomize_time"/);
    // What to sync mirrors the sync panel's choice (sync.collections).
    for (const key of ['bookmarks', 'likes', 'tweets']) {
        assert.match(scheduleHtml, new RegExp(`:aria-checked="nookSyncCollections\\.includes\\('${key}'\\)" @click="nookToggleSyncCollection\\(\\$data, '${key}'\\)"`));
    }
});

test('Settings hidden checkboxes stay anchored inside the modal', () => {
    const html = pageHtml();
    const settingsHtml = html.slice(
        html.indexOf('<!-- Settings Modal'),
        html.indexOf('<!-- Setup Modal -->'),
    );
    // Other Settings switches are buttons (role="switch"); these two still wrap a hidden checkbox.
    for (const model of [
        'automatedTagging.enabled',
        'automatedTagging.google_search',
    ]) {
        const inputAt = settingsHtml.indexOf(`x-model="${model}"`);
        assert.notEqual(inputAt, -1, `missing Settings checkbox for ${model}`);
        const beforeInput = settingsHtml.slice(Math.max(0, inputAt - 300), inputAt);
        const anchorAt = Math.max(beforeInput.lastIndexOf('<label'), beforeInput.lastIndexOf('<span'), beforeInput.lastIndexOf('<div'));
        const anchor = beforeInput.slice(anchorAt);
        assert.match(anchor, /class="[^"]*\brelative\b[^"]*"/, `${model} needs a positioned ancestor`);
    }
});

test('offline fonts use local families, permission-based discovery, and no stylesheets', async () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, appScripts(), '({ tweetApp })');
    const component = immediateComponent(tweetApp());
    await component.discoverFonts();
    assert.match(component.fontDiscoveryMessage, /unavailable/);
    context.window.isSecureContext = true;
    context.window.queryLocalFonts = async () => [
        { family: 'Zed' }, { family: 'Alpha' }, { family: 'Alpha' },
    ];
    await component.discoverFonts();
    assert.equal(component.installedFonts.length, 2);
    assert.equal(component.fontDiscoveryBusy, false);
    component.selectFont('local:Alpha');
    assert.equal(context.document.body.style.fontFamily, '"Alpha", sans-serif');
    assert.equal(context.__state.stored.get('tvx-font'), 'local:Alpha');
    component.setFont('local:My "Font"', component.localFontFamily('My "Font"'));
    assert.equal(component.fontFamily, 'local:My "Font"');
    assert.match(context.document.body.style.fontFamily, /\\22 /);
    assert.ok(component.fontChoices().some(font => font.key === component.fontFamily));
    context.window.queryLocalFonts = async () => { throw { name: 'NotAllowedError' }; };
    await component.discoverFonts();
    assert.match(component.fontDiscoveryMessage, /not granted/);
    assert.equal(component.fontDiscoveryBusy, false);
    assert.equal(context.__state.appendedLinks.length, 0);
});

test('custom font dropdown supports keyboard focus and selection', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, appScripts(), '({ tweetApp })');
    const component = immediateComponent(tweetApp());
    const choices = component.fontChoices();
    const options = choices.map(() => ({ focus() { context.document.activeElement = this; } }));
    const trigger = { focus() { context.document.activeElement = this; } };
    component.$refs = { fontOptions: { querySelectorAll: () => options }, fontTrigger: trigger };
    component.openFontPicker();
    assert.equal(component.fontPickerOpen, true);
    assert.equal(context.document.activeElement, options[0]);
    component.focusFontOption(-1);
    assert.equal(context.document.activeElement, options.at(-1));
    component.focusFontOption(1);
    assert.equal(context.document.activeElement, options[0]);
    component.focusFontOption('last');
    assert.equal(context.document.activeElement, options.at(-1));
    component.selectFont(choices.at(-1).key);
    assert.equal(component.fontPickerOpen, false);
    assert.equal(context.document.activeElement, trigger);
    assert.equal(component.selectedFontChoice().key, choices.at(-1).key);
    const html = pageHtml();
    assert.match(html, /Load local fonts/);
    assert.match(html, /:style="\{ fontFamily: f.family \}"/);
});

test('settings for later menus are requested after the first feed page', async () => {
    const context = browserContext();
    const requests = [];
    let answerFeed;
    context.fetch = url => {
        requests.push(url);
        if (url.startsWith('/api/tweets?')) return new Promise(resolve => { answerFeed = resolve; });
        return new Promise(() => {});
    };
    const { tweetApp, nookLoadSyncCollections } = loadScripts(
        context, appScriptsWith('header.js'), '({tweetApp, nookLoadSyncCollections})',
    );
    const app = immediateComponent(tweetApp());
    app.fetchNotices = async () => {};
    app.fetchActivityStatus = async () => {};
    app.initApp();
    const html = pageHtml();
    assert.ok(html.includes('afterStartupFeed(() => nookLoadSyncCollections($data))'));
    app.afterStartupFeed(() => nookLoadSyncCollections(app));
    await new Promise(resolve => setImmediate(resolve));
    assert.ok(!requests.includes('/api/config'));
    answerFeed({ ok: false, status: 409, json: async () => ({}) });
    await new Promise(resolve => setImmediate(resolve));
    assert.ok(requests.includes('/api/config'), 'a refused first page still releases them');
});

test('Settings tabs use the requested order and Appearance is the default', () => {
    const html = pageHtml();
    const js = readScripts(pageScripts());
    const keys = ['appearance', 'schedule', 'tags', 'automated-tagging', 'notifications', 'config', 'logs', 'setup'];
    const positions = keys.map(key => html.indexOf(`id="nook-nav-${key}"`));
    assert.ok(positions.every(index => index >= 0));
    assert.deepEqual(positions, [...positions].sort((a, b) => a - b));
    for (const key of keys) assert.match(html, new RegExp(`@click="nookSettingsNavigate\\(\\$data, '${key}'\\)"`));
    assert.match(js, /settingsTab: 'appearance'/);
    assert.match(js, /rerunSetup\(\)[\s\S]*?this\.settingsTab = 'setup'/);
});

test('restored Settings logs catch up, resume a single poll and show results finished while closed', async () => {
    for (const status of ['failed', 'completed']) {
        const context = browserContext();
        const timers = new Map(), watchers = {};
        let nextTimer = 0, output = 'Likes: progress · 61 pages\n', result = {status: 'running'};
        const offsets = [];
        context.setTimeout = callback => { timers.set(++nextTimer, callback); return nextTimer; };
        context.clearTimeout = timer => timers.delete(timer);
        context.fetch = async url => {
            if (!url.includes('/log?')) return {ok: true, json: async () => ({...result})};
            const offset = Number(new URL(url, 'http://localhost').searchParams.get('offset'));
            offsets.push(offset);
            return {ok: true, text: async () => output.slice(offset), headers: {get: () => String(output.length)}};
        };
        const page = immediateComponent(loadScripts(context, ['settings.js'], 'nookLogsPage')());
        Object.assign(page, {showSettingsModal: true, settingsTab: 'logs', activityRuns: []});
        page.$watch = (key, callback) => { watchers[key] = callback; };
        page.init();
        await page.open({run_id: 'first-sync', status: 'running'});
        assert.equal(timers.size, 1);
        assert.equal(page.nookLogLines.length, 1);
        page.openFolds = [0];
        page.showSettingsModal = false;
        await watchers.showSettingsModal();
        assert.equal(timers.size, 0);
        output += 'Likes: progress · 173 pages\n';
        page.showSettingsModal = true;
        await watchers.showSettingsModal();
        assert.equal(page.nookLogLines.length, 2);
        assert.deepEqual([...page.openFolds], [0]);
        assert.equal(timers.size, 1);
        assert.ok(offsets[1] > 0); // Resume by offset without duplicating the restored log.
        const readsBeforeSameRun = offsets.length;
        await watchers['nookLogRun?.run_id']('first-sync', 'first-sync');
        assert.equal(offsets.length, readsBeforeSameRun); // Replacing metadata must not trigger a polling loop.
        assert.equal(timers.size, 1);
        const [timer, poll] = [...timers][0];
        timers.delete(timer);
        output += 'Media: progress · 10 files\n';
        await poll();
        assert.equal(page.nookLogLines.length, 3);
        assert.equal(timers.size, 1);
        page.showSettingsModal = false;
        await watchers.showSettingsModal();
        result = {status, completed_at: 1000, exit_code: status === 'failed' ? 2 : 0,
            summary: status === 'failed' ? 'Storage is full. Free disk space, then resume.' : 'Complete'};
        output += result.summary + '\n';
        page.showSettingsModal = true;
        await watchers.showSettingsModal();
        assert.equal(page.nookLogRun.status, status);
        assert.equal(page.nookLogRun.exit_code, result.exit_code);
        assert.equal(page.nookLogRun.completed_at, 1000);
        assert.equal(page.nookLogRun.summary, result.summary);
        assert.match(page.nookLogLines.at(-1).raw, new RegExp(result.summary));
        assert.equal(timers.size, 0);
        // Returning to the log tab also refreshes the restored view.
        page.settingsTab = 'appearance';
        await watchers.settingsTab();
        const reads = offsets.length;
        page.settingsTab = 'logs';
        await watchers.settingsTab();
        assert.equal(offsets.length, reads + 1);
        assert.equal(timers.size, 0);
    }
});

test('Configuration page groups and formats fields from the server schema', () => {
    const context = browserContext();
    const { nookConfigSections, nookConfigFormat, nookConfigDisabled } = loadScripts(
        context, ['settings.js'], '({nookConfigSections, nookConfigFormat, nookConfigDisabled})',
    );
    const { schema, defaults } = JSON.parse(fs.readFileSync(path.join(ROOT, 'demo', 'config.json'), 'utf8'));
    const app = {
        configSchema: schema,
        configData: JSON.parse(JSON.stringify(defaults)),
        showAdvancedConfig: false,
        configSaving: false,
        getFieldType(section, key) {
            if (this.configSchema.types?.[`${section}.${key}`]) return this.configSchema.types[`${section}.${key}`];
            const value = this.configData[section][key];
            return typeof value === 'boolean' ? 'boolean' : typeof value === 'number' ? 'number' : 'text';
        },
    };
    const basic = nookConfigSections(app, 'basic');
    assert.deepEqual([...basic.map(group => group.title)], ['Web server', 'Avatars', 'Quoted and linked posts', 'Check']);
    assert.ok(basic.flatMap(group => group.fields).some(field => field.path === 'sync.reply_linked_depth'));
    // Frequency and the independent context toggle are basic settings.
    assert.deepEqual(basic.find(group => group.title === 'Check').fields.map(field => field.path), [
        'check.enabled', 'check.cadence', 'check.time', 'check.weekday', 'check.context_enabled',
    ]);
    assert.equal(defaults.check.cadence, 'weekly');
    assert.equal(defaults.check.context_enabled, false);
    assert.equal(defaults.check.context_days, 60);
    assert.equal(basic[0].note, 'Restart TweetNook after changing these.');
    assert.equal(nookConfigSections(app, 'advanced').length, 0);
    // The page renders one list: with the switch off it is just the basic sections.
    assert.deepEqual(nookConfigSections(app).map(group => group.title), basic.map(group => group.title));
    assert.ok(nookConfigSections(app).every(group => !group.advanced && group.fields.every(item => !item.advanced)));

    app.showAdvancedConfig = true;
    const advanced = nookConfigSections(app, 'advanced');
    // With the switch on, advanced fields join their own sections in schema order, flagged.
    const merged = nookConfigSections(app);
    assert.deepEqual(merged.slice(0, 3).map(group => group.title), ['Web server', 'Avatars', 'Analytics']);
    const check = merged.find(group => group.title === 'Check');
    assert.equal(check.advanced, false);
    assert.deepEqual(check.fields.map(item => [item.path, item.advanced]), [
        ['check.enabled', false], ['check.cadence', false], ['check.time', false],
        ['check.weekday', false], ['check.context_enabled', false], ['check.detail_budget', true],
    ]);
    assert.equal(merged.find(group => group.title === 'Sync speed').advanced, true);
    assert.deepEqual(merged.flatMap(group => group.fields).map(item => item.path).sort(),
        [...basic, ...advanced].flatMap(group => group.fields).map(item => item.path).sort());
    const fields = [...basic, ...advanced].flatMap(group => group.fields);
    const paths = fields.map(field => field.path);
    // Every visible field outside the blacklist appears exactly once.
    const expected = Object.entries(defaults).flatMap(([section, values]) => Object.keys(values).map(key => `${section}.${key}`))
        .filter(item => !schema.blacklist.includes(item) && !['sync.reply_linked_limit', 'check.context_days', 'check.day_of_month'].includes(item));
    assert.deepEqual([...paths].sort(), [...expected].sort());
    assert.ok(!paths.includes('sync.collections'));

    const field = name => fields.find(item => item.path === name);
    assert.equal(field('web.analytics_refresh_time').type, 'time');
    assert.equal(field('sync.reply_linked_depth').type, 'toggle01');
    assert.ok(!paths.includes('sync.reply_linked_limit'));
    app.configData.sync.reply_linked_depth = 1;
    const replyLimit = nookConfigSections(app, 'advanced').flatMap(group => group.fields)
        .find(item => item.path === 'sync.reply_linked_limit');
    assert.equal(replyLimit.type, 'number');
    assert.equal(replyLimit.number.min, 1);
    assert.equal(replyLimit.number.step, 1);
    app.configData.sync.reply_linked_limit = 3;
    app.configData.sync.reply_linked_depth = 0;
    assert.ok(!nookConfigSections(app, 'advanced').flatMap(group => group.fields)
        .some(item => item.path === 'sync.reply_linked_limit'));
    assert.equal(app.configData.sync.reply_linked_limit, 3, 'hiding the limit preserves the saved choice');
    assert.equal(field('check.cadence').type, 'select');
    assert.equal(nookConfigFormat(field('check.cadence'), 'weekly'), 'Weekly');
    assert.deepEqual(field('check.cadence').options.map(option => option.value), ['daily', 'weekly', 'monthly']);
    assert.equal(nookConfigDisabled(app, field('check.context_enabled')), false, 'context can be configured while automatic checks are off');
    app.configData.check.context_enabled = true;
    const contextCycle = nookConfigSections(app, 'basic').flatMap(group => group.fields).find(item => item.path === 'check.context_days');
    assert.equal(contextCycle.advanced, false);
    assert.equal(nookConfigDisabled(app, contextCycle), false);
    app.configData.check.context_days = 90;
    app.configData.check.context_enabled = false;
    assert.ok(!nookConfigSections(app).flatMap(group => group.fields).some(item => item.path === 'check.context_days'));
    assert.equal(app.configData.check.context_days, 90);
    app.configData.check.cadence = 'monthly';
    const monthly = nookConfigSections(app, 'basic').flatMap(group => group.fields);
    assert.ok(monthly.some(item => item.path === 'check.day_of_month'));
    assert.ok(!monthly.some(item => item.path === 'check.weekday'));
    app.configData.check.cadence = 'daily';
    assert.ok(!nookConfigSections(app).flatMap(group => group.fields).some(item => ['check.weekday', 'check.day_of_month'].includes(item.path)));
    assert.equal(nookConfigFormat(field('database.cache_size_kb'), 524288), '512 MiB');
    assert.equal(nookConfigFormat(field('sync.page_delay'), 2), `2 ${schema.units['sync.page_delay']}`);
    // Avatar cache limits depend on fetching avatars.
    app.configData.web.fetch_avatars = false;
    assert.equal(nookConfigDisabled(app, field('web.avatar_cache_limit_mb')), true);
    app.configData.web.fetch_avatars = true;
    assert.equal(nookConfigDisabled(app, field('web.avatar_cache_limit_mb')), false);
});

test('all media layout preferences restore after initialization, with a safe default', () => {
    for (const saved of ['grid', 'carousel', 'date', 'invalid', null]) {
        const context = browserContext();
        if (saved !== null) context.localStorage.setItem('tvx-media-gallery', saved);
        const app = immediateComponent(loadScripts(context, appScripts(), 'tweetApp')());
        for (const method of ['fetchSetup', 'fetchNotices', 'fetchActivityStatus', 'fetchTweets', 'fetchStats', 'fetchArchiveEnrichmentStatus', 'fetchAutomatedTagging']) app[method] = async () => {};
        app.initApp();
        assert.equal(app.mediaGallery, ['grid', 'carousel', 'date'].includes(saved) ? saved : 'date');
    }
});

run();
