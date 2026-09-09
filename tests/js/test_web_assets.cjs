'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '..', '..');
const JS_DIR = path.join(ROOT, 'tweetnook', 'web', 'static', 'js');

function browserContext(pathname = '/') {
    const stored = new Map();
    const cssProperties = new Map();
    const rootClasses = new Set();
    const events = new Map();
    const historyCalls = [];
    const scrollCalls = [];
    const appendedLinks = [];
    const videos = [];
    const location = { origin: 'http://localhost', pathname };
    let historyState = null;

    const context = {
        console: {
            log() {},
            error() {},
            warn() {},
        },
        setTimeout,
        clearTimeout,
        setInterval: () => 0,
        clearInterval() {},
        URL,
        Date,
        Math,
        JSON,
        Promise,
        encodeURIComponent,
        decodeURIComponent,
        confirm: () => true,
        alert() {},
        CustomEvent: class CustomEvent {
            constructor(type, options = {}) {
                this.type = type;
                this.detail = options.detail;
            }
        },
        localStorage: {
            getItem(key) {
                return stored.has(key) ? stored.get(key) : null;
            },
            setItem(key, value) {
                stored.set(key, String(value));
            },
            removeItem(key) {
                stored.delete(key);
            },
        },
        history: {
            scrollRestoration: 'auto',
            get state() {
                return historyState;
            },
            replaceState(...args) {
                historyCalls.push(['replace', ...args]);
                historyState = args[0];
                if (args[2]) location.pathname = args[2];
            },
            pushState(...args) {
                historyCalls.push(['push', ...args]);
                historyState = args[0];
                if (args[2]) location.pathname = args[2];
            },
            back() {
                historyCalls.push(['back']);
            },
        },
        document: {
            documentElement: {
                style: {
                    setProperty(key, value) {
                        cssProperties.set(key, value);
                    },
                },
                classList: {
                    add(value) {
                        rootClasses.add(value);
                    },
                    remove(value) {
                        rootClasses.delete(value);
                    },
                },
            },
            body: {
                style: {},
                querySelectorAll() {
                    return [];
                },
            },
            head: {
                appendChild(node) {
                    appendedLinks.push(node);
                },
            },
            querySelector(selector) {
                if (selector.startsWith('link[data-font=')) {
                    return appendedLinks.find(link => selector.includes(link.dataset.font)) || null;
                }
                return null;
            },
            querySelectorAll(selector) {
                return selector === 'video' ? videos : [];
            },
            getElementById() {
                return null;
            },
            createElement(tag) {
                return { tagName: tag.toUpperCase(), dataset: {}, style: {} };
            },
            createRange() {
                return {
                    cloneRange() {
                        return this;
                    },
                    selectNodeContents() {},
                    setEnd() {},
                    setStart() {},
                    collapse() {},
                    toString() {
                        return '';
                    },
                };
            },
        },
        IntersectionObserver: class IntersectionObserver {
            constructor(callback) {
                this.callback = callback;
                this.observed = [];
            }
            observe(node) {
                this.observed.push(node);
            }
        },
        MutationObserver: class MutationObserver {
            constructor(callback) {
                this.callback = callback;
            }
            observe() {}
        },
    };

    context.window = {
        innerWidth: 1280,
        scrollY: 0,
        location,
        addEventListener(name, callback) {
            events.set(name, callback);
        },
        dispatchEvent(event) {
            const callback = events.get(event.type);
            if (callback) return callback(event);
        },
        scrollTo(...args) {
            scrollCalls.push(args);
        },
        getSelection() {
            return {
                rangeCount: 0,
                removeAllRanges() {},
                addRange() {},
            };
        },
    };
    context.globalThis = context;
    context.__state = {
        stored,
        cssProperties,
        rootClasses,
        events,
        historyCalls,
        scrollCalls,
        appendedLinks,
        videos,
    };
    vm.createContext(context);
    return context;
}

function loadScripts(context, names, exportExpression) {
    const source = names
        .map(name => fs.readFileSync(path.join(JS_DIR, name), 'utf8'))
        .join('\n');
    vm.runInContext(`${source}\nglobalThis.__exports = ${exportExpression};`, context, {
        filename: names.join('+'),
    });
    return context.__exports;
}

function immediateComponent(component) {
    component.$nextTick = callback => callback();
    component.$watch = () => {};
    component.$refs = {};
    return component;
}

const tests = [];
function test(name, fn) {
    tests.push({ name, fn });
}

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
        ]) {
            assert.ok(theme[key], `missing ${key}`);
        }
    }
    assert.ok(exported.THEMES['classic-dark']._accents.length >= 6);
});

test('autocomplete formats valid, invalid, negative, quoted, and escaped capsules', () => {
    const context = browserContext();
    const { searchAutocomplete } = loadScripts(
        context,
        ['autocomplete.js'],
        '({searchAutocomplete})',
    );
    const input = { innerHTML: '' };
    const component = immediateComponent(searchAutocomplete());
    component.$refs.searchInput = input;
    component.globalTags = [{ tag: 'Night Sky' }];
    component.knownAuthors = new Set(['alice']);

    component.formatRichText(
        'from:alice -has:video tag:"Night Sky" filter:nope <img src=x>',
    );

    assert.match(input.innerHTML, /valid-capsule/);
    assert.match(input.innerHTML, /negative-capsule/);
    assert.match(input.innerHTML, /hidden-quote/);
    assert.match(input.innerHTML, /invalid-capsule/);
    assert.match(input.innerHTML, /&lt;img/);
    assert.doesNotMatch(input.innerHTML, /<img src=x>/);
});

test('autocomplete renders only valid uppercase boolean operators as neutral capsules', () => {
    const context = browserContext();
    const { searchAutocomplete } = loadScripts(
        context,
        ['autocomplete.js'],
        '({searchAutocomplete})',
    );
    const input = { innerHTML: '' };
    const component = immediateComponent(searchAutocomplete());
    component.$refs.searchInput = input;

    component.formatRichText('cats AND dogs OR birds and fish OR');

    assert.equal((input.innerHTML.match(/boolean-capsule/g) || []).length, 2);
    assert.match(input.innerHTML, /boolean-capsule">AND<\/span>/);
    assert.match(input.innerHTML, /boolean-capsule">OR<\/span>/);
    assert.match(input.innerHTML, /birds&nbsp;and&nbsp;fish&nbsp;OR$/);
});

test('autocomplete highlighting escapes API-provided labels', () => {
    const context = browserContext();
    const { searchAutocomplete } = loadScripts(
        context,
        ['autocomplete.js'],
        '({searchAutocomplete})',
    );
    const component = searchAutocomplete();
    const highlighted = component.highlightMatch('<img onerror=x> Alice', 'ali');
    assert.equal(highlighted, '&lt;img onerror=x&gt; <b>Ali</b>ce');
    assert.equal(component.highlightMatch('<script>', ''), '&lt;script&gt;');
});

test('autocomplete separates attached content from post types and states', () => {
    const context = browserContext();
    const { searchAutocomplete } = loadScripts(
        context,
        ['autocomplete.js'],
        '({searchAutocomplete})',
    );
    const component = searchAutocomplete();
    assert.ok(component.hasOptions.some(option => option.value === 'article'));
    assert.ok(component.isOptions.some(option => option.value === 'resurrected'));
    assert.ok(component.allFilters.some(option => option.prefix === 'is:'));
    assert.ok(!component.allFilters.some(option => option.prefix === 'filter:'));
});

test('autocomplete keyboard navigation wraps and scrolls selected option', () => {
    const context = browserContext();
    const { searchAutocomplete } = loadScripts(
        context,
        ['autocomplete.js'],
        '({searchAutocomplete})',
    );
    let scrolled = 0;
    const component = immediateComponent(searchAutocomplete());
    component.showDropdown = true;
    component.options = [{}, {}, {}];
    component.$refs.dropdownMenu = {
        querySelectorAll() {
            return component.options.map(() => ({
                scrollIntoView() {
                    scrolled += 1;
                },
            }));
        },
    };

    component.selectedIndex = 2;
    component.moveDown();
    assert.equal(component.selectedIndex, 0);
    component.moveUp();
    assert.equal(component.selectedIndex, 2);
    assert.equal(scrolled, 2);
});

test('autocomplete quotes selected multi-word values and keeps caret position', () => {
    const context = browserContext();
    const { searchAutocomplete } = loadScripts(
        context,
        ['autocomplete.js'],
        '({searchAutocomplete})',
    );
    let selection = null;
    const component = immediateComponent(searchAutocomplete());
    component.searchQuery = 'before tag:ni after';
    component.cursorPos = 'before tag:ni'.length;
    component.showDropdown = true;
    component.options = [{ prefix: 'tag:', value: 'Night Sky' }];
    component.$refs.searchInput = {
        isContentEditable: false,
        focus() {},
        setSelectionRange(start, end) {
            selection = [start, end];
        },
    };
    component.handleInput = () => {};

    component.selectOption();

    assert.equal(component.searchQuery, 'before tag:"Night Sky"  after');
    assert.deepEqual(selection, [23, 23]);
});

test('autocomplete keeps unfinished quoted values together across spaces', async () => {
    const context = browserContext();
    const calls = [];
    context.fetch = async url => {
        calls.push(url);
        return {
            ok: true,
            async json() {
                return { tags: [{ tag: 'Word 1', count: 2 }] };
            },
        };
    };
    const { searchAutocomplete } = loadScripts(
        context,
        ['autocomplete.js'],
        '({searchAutocomplete})',
    );
    const component = immediateComponent(searchAutocomplete());
    component.searchQuery = 'before tag:"word 1';
    component.$refs.searchInput = {
        isContentEditable: false,
        selectionStart: component.searchQuery.length,
        focus() {},
        setSelectionRange() {},
    };

    component.handleInput();
    await new Promise(resolve => setTimeout(resolve, 0));

    assert.equal(calls[0], '/api/tags/autocomplete?q=word%201');
    assert.equal(component.options[0].value, 'Word 1');

    component.selectOption();
    assert.equal(component.searchQuery, 'before tag:"Word 1" ');

    component.formatRichText('tag:"word 1');
    assert.equal((component.$refs.searchInput.innerHTML.match(/capsule-key/g) || []).length, 1);
    assert.match(component.$refs.searchInput.innerHTML, /&quot;word 1/);
});

test('autocomplete fetches author and tag suggestions with encoded queries', async () => {
    const context = browserContext();
    const calls = [];
    context.fetch = async url => {
        calls.push(url);
        if (url.startsWith('/api/authors')) {
            return {
                ok: true,
                async json() {
                    return {
                        authors: [
                            { id: '1', username: 'Alice', display_name: 'Alice A' },
                        ],
                    };
                },
            };
        }
        return {
            ok: true,
            async json() {
                return { tags: [{ tag: 'Night Sky', count: 1200 }] };
            },
        };
    };
    const { searchAutocomplete } = loadScripts(
        context,
        ['autocomplete.js'],
        '({searchAutocomplete})',
    );
    const component = immediateComponent(searchAutocomplete());
    component.$refs.searchInput = {
        isContentEditable: false,
        selectionStart: 10,
    };

    component.searchQuery = 'from:a b';
    component.cursorPos = component.searchQuery.length;
    component.handleInput();
    component.searchQuery = 'tag:night sky';
    component.cursorPos = component.searchQuery.length;
    component.handleInput();
    await new Promise(resolve => setTimeout(resolve, 0));

    assert.deepEqual(calls, []);

    component.searchQuery = 'from:ali';
    component.cursorPos = component.searchQuery.length;
    component.handleInput();
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.equal(component.options[0].value, 'Alice');
    assert.ok(component.knownAuthors.has('alice'));
    assert.match(calls[0], /q=ali$/);

    component.searchQuery = 'tag:night';
    component.cursorPos = component.searchQuery.length;
    component.handleInput();
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.equal(component.options[0].value, 'Night Sky');
    assert.equal(component.options[0].count, '1,200');
});

test('autocomplete calendar handles leap years and month boundaries', () => {
    const context = browserContext();
    const { searchAutocomplete } = loadScripts(
        context,
        ['autocomplete.js'],
        '({searchAutocomplete})',
    );
    const component = searchAutocomplete();
    component.dpYear = 2024;
    component.dpMonth = 1;
    const days = component.dpGetDays();
    assert.equal(days.length, 42);
    assert.ok(days.some(day => day.isCurrentMonth && day.dateStr === '2024-02-29'));

    component.dpMonth = 0;
    component.dpPrevMonth();
    assert.deepEqual([component.dpYear, component.dpMonth], [2023, 11]);
    component.dpNextMonth();
    assert.deepEqual([component.dpYear, component.dpMonth], [2024, 0]);

    component.initDatePicker('2026-07-30');
    assert.deepEqual([component.dpYear, component.dpMonth], [2026, 6]);
    assert.equal(component.formatDateStr(2026, 7, 3), '2026-07-03');
});

test('tweet app starts with coherent list, panel, modal, and theme state', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    assert.equal(app.viewMode, 'list');
    assert.equal(app.page, 1);
    assert.equal(app.collectionFilter, 'likes');
    assert.equal(app.sortOrder, 'liked_latest');
    assert.equal(app.panelMode, null);
    assert.deepEqual(Array.from(app.panelStack), []);
    assert.equal(app.tagModalOpen, false);
    assert.equal(app.currentTheme, 'classic-dark');
    assert.equal(app.showEmptyUnavailableReasons, false);
    assert.equal(app.statsGeneratedAt, null);
    assert.equal(app.statsRefreshing, false);
    assert.ok(Object.keys(app.THEMES).length >= 15);
    assert.equal(app.avatarUrl('user/42'), '/api/avatar/user%2F42?v=2');
});

test('lightbox navigation moves through media and clamps at both ends', () => {
    const context = browserContext();
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook/web/index.html'), 'utf8');
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    app.lightboxMedia = [{type: 'photo'}, {type: 'photo'}, {type: 'photo'}];
    app.lightboxIndex = 0;

    app.prevLightbox();
    assert.equal(app.lightboxIndex, 0);
    app.nextLightbox();
    assert.equal(app.lightboxIndex, 1);
    app.nextLightbox();
    app.nextLightbox();
    assert.equal(app.lightboxIndex, 2);
    app.prevLightbox();
    assert.equal(app.lightboxIndex, 1);

    app.lightboxMedia = [{type: 'photo'}];
    app.lightboxIndex = 0;
    app.nextLightbox();
    app.prevLightbox();
    assert.equal(app.lightboxIndex, 0);
    assert.match(html, /@click\.stop="prevLightbox\(\)"/);
    assert.match(html, /@click\.stop="nextLightbox\(\)"/);
    assert.match(html, /@click\.stop="lightboxOpen = false"/);
});

test('reply-recipient links open an anchored profile card without searching', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    let searches = 0;
    app.searchFrom = () => { searches += 1; };
    const anchor = {
        getBoundingClientRect() {
            return { left: 40, right: 100, top: 20, bottom: 50 };
        },
    };

    app.openUsernameProfileCard({ currentTarget: anchor }, 'reply_target');

    assert.equal(searches, 0);
    assert.equal(app.profileCard.username, 'reply_target');
    assert.equal(app.profileCard.name, 'reply_target');
    assert.equal(app.profileCard.id, 'unknown');
    assert.deepEqual([app.profileCard.x, app.profileCard.y], [40, 58]);
});

test('analytics uses one cached snapshot and retains it during manual refresh', async () => {
    const context = browserContext();
    const calls = [];
    const generatedAt = new Date().toISOString();
    const snapshot = {
        generated_at: generatedAt,
        age_seconds: 0,
        stale: false,
        refreshing: false,
        refresh_failed: false,
        summary: { unique_posts: 12, latest_sync: 'Aug 10, 2026' },
        collections: [{ collection: 'Bookmarks', count: 12 }],
        health: { enrichment: { unavailable: { reasons: [] } } },
        storage: { total_bytes: 42 },
        tags: { unique_tags: 3 },
    };
    context.fetch = async (url, options = {}) => {
        calls.push([url, options]);
        return {
            ok: true,
            status: 200,
            async json() {
                return url === '/api/stats/refresh'
                    ? { ...snapshot, refreshing: true }
                    : snapshot;
            },
        };
    };
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    let polls = 0;
    app.scheduleStatsRefreshPoll = () => { polls += 1; };

    await app.openStatsModal();

    assert.equal(calls.length, 1);
    assert.equal(calls[0][0], '/api/stats/snapshot?revalidate=true');
    assert.equal(app.statsSummary.unique_posts, 12);
    assert.equal(app.statsCollections[0].collection, 'Bookmarks');
    assert.equal(app.storageData.total_bytes, 42);
    assert.equal(app.statsTags.unique_tags, 3);
    assert.equal(app.loadingStatsSnapshot, false);
    assert.equal(app.statsAgeLabel(), 'Updated just now');

    await app.refreshStats();

    assert.equal(calls[1][0], '/api/stats/refresh');
    assert.equal(calls[1][1].method, 'POST');
    assert.equal(app.statsSummary.unique_posts, 12);
    assert.equal(app.statsRefreshing, true);
    assert.equal(app.statsAgeLabel(), 'Refreshing · updated just now');
    assert.equal(polls, 1);
});

test('analytics refresh polling does not replace an unchanged report', async () => {
    const context = browserContext();
    const calls = [];
    context.fetch = async url => {
        calls.push(url);
        return {
            ok: true,
            async json() {
                return {
                    generated_at: '2026-08-10T00:00:00+00:00',
                    refreshing: true,
                    refresh_failed: false,
                };
            },
        };
    };
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    const originalSummary = { unique_posts: 12 };
    app.showStatsModal = true;
    app.statsGeneratedAt = '2026-08-10T00:00:00+00:00';
    app.statsSummary = originalSummary;
    app.scheduleStatsRefreshPoll = () => {};

    await app.pollStatsRefresh();

    assert.deepEqual(calls, ['/api/stats/status']);
    assert.equal(app.statsSummary, originalSummary);
    assert.equal(app.statsRefreshing, true);
});

test('analytics suspends only playing videos and resumes them on close', async () => {
    const context = browserContext();
    const playing = {
        paused: false,
        isConnected: true,
        pauseCalls: 0,
        playCalls: 0,
        pause() { this.paused = true; this.pauseCalls += 1; },
        play() { this.paused = false; this.playCalls += 1; return Promise.resolve(); },
    };
    const alreadyPaused = {
        paused: true,
        isConnected: true,
        pauseCalls: 0,
        playCalls: 0,
        pause() { this.pauseCalls += 1; },
        play() { this.playCalls += 1; return Promise.resolve(); },
    };
    context.__state.videos.push(playing, alreadyPaused);
    context.fetch = async () => ({
        ok: true,
        async json() {
            return {
                generated_at: '2026-08-10T00:00:00+00:00',
                refreshing: false,
                refresh_failed: false,
                summary: {}, collections: [], health: {}, storage: {}, tags: {},
            };
        },
    });
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());

    await app.openStatsModal();
    assert.equal(playing.pauseCalls, 1);
    assert.equal(alreadyPaused.pauseCalls, 0);

    app.closeStatsModal();
    assert.equal(playing.playCalls, 1);
    assert.equal(alreadyPaused.playCalls, 0);
});

test('archive status filters empty reasons and summarizes retry state', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    app.statsHealth = {
        enrichment: {
            unavailable: {
                reasons: [
                    {
                        reason: 'unavailable_unknown',
                        count: 2,
                        percent_of_missing: 66.7,
                        due: 1,
                        delayed: 1,
                        permanent: 0,
                    },
                    {
                        reason: 'deleted_by_author',
                        count: 1,
                        percent_of_missing: 33.3,
                        due: 0,
                        delayed: 0,
                        permanent: 1,
                    },
                    {
                        reason: 'withheld',
                        count: 0,
                        percent_of_missing: 0,
                        due: 0,
                        delayed: 0,
                        permanent: 0,
                    },
                ],
            },
        },
    };

    assert.deepEqual(
        Array.from(app.getUnavailableReasons(), item => item.reason),
        ['unavailable_unknown', 'deleted_by_author'],
    );
    assert.deepEqual(
        Array.from(app.getUnavailableBarReasons(), item => item.reason),
        ['unavailable_unknown', 'deleted_by_author'],
    );
    assert.equal(
        app.getUnavailableReasonStatus(app.statsHealth.enrichment.unavailable.reasons[0]),
        '1 due now · 1 scheduled',
    );
    assert.equal(
        app.getUnavailableReasonStatus(app.statsHealth.enrichment.unavailable.reasons[1]),
        '1 permanent',
    );
    assert.ok(
        app.getUnavailableSegmentWidth(app.statsHealth.enrichment.unavailable.reasons[0])
        > app.getUnavailableSegmentWidth(app.statsHealth.enrichment.unavailable.reasons[1]),
    );

    app.showEmptyUnavailableReasons = true;
    assert.equal(app.getUnavailableReasons().length, 3);
    assert.equal(app.getUnavailableBarReasons().length, 2);
    assert.equal(
        app.getUnavailableReasonStatus(app.statsHealth.enrichment.unavailable.reasons[2]),
        'No unavailable tweets',
    );
});

test('analytics markup exposes the Archive status cards and reason breakdown', () => {
    const html = fs.readFileSync(
        path.join(ROOT, 'tweetnook', 'web', 'index.html'),
        'utf8',
    );
    const appJs = fs.readFileSync(path.join(JS_DIR, 'app.js'), 'utf8');
    const styles = fs.readFileSync(
        path.join(ROOT, 'tweetnook', 'web', 'static', 'css', 'styles.css'),
        'utf8',
    );

    assert.match(html, />Archive status</);
    assert.match(html, />Enriched /);
    assert.match(html, />Threads /);
    assert.match(html, />Missing enrichment /);
    assert.match(html, />Resurrected /);
    assert.match(html, />Unavailable tweets /);
    assert.match(html, /x-model="showEmptyUnavailableReasons"/);
    assert.match(html, /archive-status-bar-seg/);
    assert.match(html, /archive-status-reason-row/);
    assert.match(html, /@click="refreshStats\(\)"/);
    assert.match(html, /@keydown\.window\.escape="closeStatsModal\(\)"/);
    assert.match(html, /@click\.self="closeStatsModal\(\)"/);
    assert.match(html, /@click="closeStatsModal\(\)"/);
    assert.match(html, /x-text="statsAgeLabel\(\)"/);
    assert.match(html, /x-show="!statsRefreshing">Refresh</);
    assert.match(html, /x-show="statsRefreshing" class="inline-flex items-center gap-1.5"/);
    assert.match(html, /\sRefreshing\s+<\/span>/);
    assert.match(html, /class="stats-modal-scroll flex-1 overflow-y-auto p-4 md:p-6 space-y-8"/);
    assert.match(styles, /\.stats-modal-scroll\s*\{[^}]*overscroll-behavior: contain;/s);
    const statsWatcherStart = appJs.indexOf("this.$watch('showStatsModal'");
    const settingsWatcherStart = appJs.indexOf("this.$watch('showSettingsModal'");
    assert.ok(statsWatcherStart >= 0 && statsWatcherStart < settingsWatcherStart);
    const statsWatcher = appJs.slice(statsWatcherStart, settingsWatcherStart);
    assert.match(statsWatcher, /if \(val\) this\.lockModalScroll\(\);/);
    assert.match(statsWatcher, /!this\.showSettingsModal && !this\.showSetupModal/);
    assert.match(statsWatcher, /this\.unlockModalScroll\(\);/);
    assert.doesNotMatch(html, />Pipeline health</);
    assert.doesNotMatch(
        html,
        /class="archive-status-bar-seg[^"]*(?:transition|duration-)/,
    );
    assert.doesNotMatch(styles.match(/\.stats-card \{[^}]+\}/s)[0], /transition:/);
    assert.doesNotMatch(styles.match(/\.stats-info-icon \{[^}]+\}/s)[0], /transition:/);
});

test('tweet fetching encodes search state, hydrates pagination, appends, and reports errors', async () => {
    const context = browserContext();
    const calls = [];
    let responseData = {
        tweets: [{ tweet_id: '1' }],
        page: 1,
        pages: 3,
        total: 5,
        has_more: true,
    };
    context.fetch = async url => {
        calls.push(url);
        return {
            ok: true,
            status: 200,
            async json() {
                return responseData;
            },
        };
    };
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    app.collectionFilter = 'likes';
    app.sortOrder = 'relevance';
    app.searchQuery = 'from:alice night sky';

    await app.fetchTweets();
    assert.equal(
        calls[0],
        '/api/tweets?collection=likes&sort=relevance&page=1&q=from%3Aalice%20night%20sky&cursor=',
    );
    assert.deepEqual(Array.from(app.tweets, tweet => tweet.tweet_id), ['1']);
    assert.equal(app.totalPages, 3);
    assert.equal(app.total, 5);
    assert.equal(app.hasMore, true);
    assert.equal(app.loading, false);
    app.total = null;
    assert.equal(app.resultCountLabel(), 'Results');
    app.total = 3482;
    assert.equal(app.resultCountLabel(), '3,482 Results');

    responseData = { tweets: [{ tweet_id: '2' }], page: 2, pages: 3, total: 5, has_more: false };
    await app.fetchTweets(true);
    assert.deepEqual(Array.from(app.tweets, tweet => tweet.tweet_id), ['1', '2']);
    assert.equal(app.hasMore, false);
    assert.equal(app.loadingMore, false);

    context.fetch = async () => ({
        ok: false,
        status: 503,
        async json() {
            return { detail: 'temporarily unavailable' };
        },
    });
    await app.fetchTweets();
    assert.equal(app.error, 'temporarily unavailable');
    assert.deepEqual(Array.from(app.tweets), []);
});

test('feed cursors survive retries and ignore duplicate membership results', async () => {
    const context = browserContext();
    const calls = [];
    const response = data => ({ok: true, status: 200, json: async () => data});
    let fail = false;
    context.fetch = async url => {
        calls.push(url);
        if (fail) throw new Error('offline');
        return response(calls.length === 1
            ? {tweets: [{tweet_id: '1'}], page: 1, pages: null, total: null, has_more: true, next_cursor: 'cursor/+='}
            : {tweets: [{tweet_id: '1'}, {tweet_id: '2'}], page: 2, pages: null, total: null, has_more: false, next_cursor: null});
    };
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    await app.fetchTweets();
    fail = true;
    await app.loadMore();
    assert.equal(app.page, 1);
    assert.equal(app.nextCursor, 'cursor/+=');
    assert.equal(app.hasMore, true);
    assert.equal(app.loadingMore, false);
    fail = false;
    await app.loadMore();
    assert.equal(calls[1], calls[2]);
    assert.match(calls[2], /page=2&cursor=cursor%2F%2B%3D$/);
    assert.equal(app.page, 2);
    assert.deepEqual(Array.from(app.tweets, tweet => tweet.tweet_id), ['1', '2']);
});

test('feed ignores stale responses and falls back to legacy page counts', async () => {
    const context = browserContext();
    const pending = [];
    context.fetch = url => new Promise(resolve => pending.push({url, resolve}));
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    const first = app.fetchTweets();
    app.searchQuery = 'new query';
    const second = app.fetchTweets();
    const resolve = (index, data) => pending[index].resolve({ok: true, status: 200, json: async () => data});
    resolve(0, {tweets: [{tweet_id: 'old'}], page: 1, pages: 1, total: 1});
    await first;
    assert.equal(app.loading, true);
    assert.equal(app.tweets.length, 0);
    resolve(1, {tweets: [{tweet_id: 'new'}], page: 1, pages: 2, total: 2});
    await second;
    assert.equal(app.loading, false);
    assert.equal(app.hasMore, true);
    assert.deepEqual(Array.from(app.tweets, tweet => tweet.tweet_id), ['new']);
    const more = app.loadMore();
    assert.match(pending[2].url, /page=2/);
    assert.doesNotMatch(pending[2].url, /cursor=/);
    resolve(2, {tweets: [{tweet_id: 'next'}], page: 2, pages: 2, total: 2});
    await more;
    assert.equal(app.hasMore, false);
});

test('expired like cursor reloads the list once with a fresh cursor', async () => {
    const context = browserContext();
    const calls = [];
    context.fetch = async url => {
        calls.push(url);
        const expired = calls.length === 2;
        return {ok: !expired, status: expired ? 409 : 200, json: async () => expired
            ? {detail: 'Like order changed.'}
            : {tweets: [{tweet_id: String(calls.length)}], page: 1, pages: 2, total: 30, has_more: true, next_cursor: 'next'}};
    };
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    await app.fetchTweets();
    await app.loadMore();
    assert.equal(calls.length, 3);
    assert.match(calls[2], /page=1&cursor=$/);
    assert.equal(app.page, 1);
    assert.equal(app.error, null);
    assert.equal(app.loading, false);
    assert.equal(app.loadingMore, false);
    assert.deepEqual(Array.from(app.tweets, tweet => tweet.tweet_id), ['3']);
});

test('random feeds keep one seed across pages and rotate it for a new search', async () => {
    const context = browserContext();
    context.Math = Object.create(Math);
    context.Math.random = () => 0.5;
    const calls = [];
    context.fetch = async url => {
        calls.push(url);
        return {
            ok: true,
            status: 200,
            async json() {
                return { tweets: [], page: 1, pages: 1, total: 0, has_more: false };
            },
        };
    };
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.sortOrder = 'random';
    app.randomSeed = 12345;

    await app.fetchTweets();
    app.page = 2;
    await app.fetchTweets(true);
    assert.match(calls[0], /random_seed=12345/);
    assert.match(calls[1], /random_seed=12345/);

    app.fetchTweets = () => {};
    app.search();
    assert.notEqual(app.randomSeed, 12345);
});

test('like order is exposed for Likes, preserved during search, and reset on collection change', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    const requests = [];
    app.fetchTweets = () => requests.push([app.collectionFilter, app.sortOrder, app.page]);
    for (const sort of ['liked_latest', 'liked_earliest']) {
        app.collectionFilter = 'likes';
        app.sortOrder = sort;
        app.searchQuery = 'needle';
        app.page = 5;
        app.search();
        assert.deepEqual(requests.at(-1), ['likes', sort, 1]);
        app.collectionFilter = 'bookmarks';
        app.search();
        assert.deepEqual(requests.at(-1), ['bookmarks', 'newest', 1]);
    }
    app.collectionFilter = 'likes';
    app.searchQuery = '';
    assert.equal(
        JSON.stringify(app.feedOptions('sort').slice(0, 2).map(option => [option.value, option.label])),
        JSON.stringify([
            ['liked_latest', 'Recently liked'],
            ['liked_earliest', 'Earliest liked'],
        ]),
    );
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'index.html'), 'utf8');
    assert.match(html, /x-for="\(option, index\) in feedOptions\(kind\)"/);
});

test('list searching, incremental loading, and back-to-top obey state guards', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    let fetches = 0;
    app.fetchTweets = append => {
        fetches += append ? 10 : 1;
    };

    app.searchQuery = '';
    app.sortOrder = 'default';
    app.search();
    assert.equal(app.sortOrder, 'liked_latest');
    assert.equal(fetches, 1);

    app.loading = false;
    app.loadingMore = false;
    app.page = 1;
    app.totalPages = 2;
    app.hasMore = true;
    app.loadMore();
    assert.equal(app.page, 1); // Only a successful fetch advances the committed page.
    assert.equal(fetches, 11);
    app.hasMore = false;
    app.loadMore();
    assert.equal(fetches, 11);

    const html = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'index.html'), 'utf8');
    assert.match(html, /x-show="!loading && hasMore"/);
    assert.doesNotMatch(html, /x-show="!loading && page < totalPages"/);

    app.scrollToTop();
    const scrollOptions = context.__state.scrollCalls.at(-1)[0];
    assert.equal(scrollOptions.top, 0);
    assert.equal(scrollOptions.behavior, 'smooth');
});

test('themes, accents, fonts, sizes, and split panel persist preferences', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());

    app.applyTheme('classic-light');
    assert.equal(app.currentTheme, 'classic-light');
    assert.equal(context.__state.cssProperties.get('--bg-primary'), '#ffffff');
    assert.ok(context.__state.rootClasses.has('light'));
    assert.equal(context.localStorage.getItem('tvx-theme'), 'classic-light');

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

test('detail routes parse and generate canonical string tweet IDs', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    const largeId = '12345678901234567890';

    assert.deepEqual({ ...app.parseRoute('/') }, { viewMode: 'list' });
    assert.deepEqual({ ...app.parseRoute(`/post/${largeId}`) }, { viewMode: 'thread', tweetId: largeId });
    assert.deepEqual(
        { ...app.parseRoute(`/post/${largeId}/quotes`) },
        { viewMode: 'quotes', quotesTweetId: largeId },
    );
    assert.equal(typeof app.parseRoute(`/post/${largeId}`).tweetId, 'string');
    assert.equal(app.routeUrl({ viewMode: 'thread', tweetId: largeId }), `/post/${largeId}`);
    assert.equal(app.routeUrl({ viewMode: 'quotes', quotesTweetId: largeId }), `/post/${largeId}/quotes`);
    assert.throws(() => app.routeUrl({ viewMode: 'thread', tweetId: Number(largeId) }), /Invalid TweetNook route/);
    assert.equal(app.parseRoute('/post/not-a-number'), null);
    assert.equal(app.parseRoute('/post/123/other'), null);
});

test('single-column thread quote count tolerates route teardown', () => {
    const html = fs.readFileSync(
        path.join(ROOT, 'tweetnook', 'web', 'index.html'),
        'utf8',
    );
    assert.match(html, /threadData\?\.main\?\.local_quote_count > 0/);
});

test('initialization preserves direct detail paths without adding history entries', () => {
    for (const [pathname, viewMode] of [['/post/123', 'thread'], ['/post/123/quotes', 'quotes']]) {
        const context = browserContext(pathname);
        const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
        const app = immediateComponent(tweetApp());
        app.fetchSetup = async () => {};
        app.fetchNotices = async () => {};
        app.fetchActivityStatus = async () => {};

        app.initApp();

        assert.equal(app.pendingRoute.viewMode, viewMode);
        assert.equal(context.window.location.pathname, pathname);
        assert.equal(context.__state.historyCalls.filter(call => call[0] === 'replace').length, 1);
        assert.equal(context.__state.historyCalls.filter(call => call[0] === 'push').length, 0);
        assert.equal(context.history.state.tweetNookDepth, 0);
    }
});

test('popstate applies URL routes without pushing and restores list scroll', async () => {
    const context = browserContext();
    context.fetch = async url => ({
        ok: true,
        async json() {
            if (url.includes('/quotes')) return { tweets: [], total: 0, limit: 20 };
            return { main: { tweet_id: url.split('/').at(-1) }, parents: [], children: [] };
        },
    });
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.fetchSetup = async () => {};
    app.fetchNotices = async () => {};
    app.fetchActivityStatus = async () => {};
    app.initApp();
    app.archiveResourcesLoaded = true;
    context.__state.historyCalls.length = 0;

    await context.window.dispatchEvent({
        type: 'popstate',
        state: { viewMode: 'thread', tweetId: '123', tweetNookDepth: 1 },
    });
    assert.equal(app.viewMode, 'thread');
    assert.equal(app.threadData.main.tweet_id, '123');

    context.window.location.pathname = '/post/123/quotes';
    await context.window.dispatchEvent({ type: 'popstate', state: null });
    assert.equal(app.viewMode, 'quotes');
    assert.equal(app.quotesTweetId, '123');

    await context.window.dispatchEvent({
        type: 'popstate',
        state: { viewMode: 'list', scrollY: 321, tweetNookDepth: 0 },
    });
    assert.equal(app.viewMode, 'list');
    assert.ok(context.__state.scrollCalls.some(call => call[0] === 0 && call[1] === 321));
    assert.equal(context.__state.historyCalls.filter(call => call[0] === 'push').length, 0);
});

test('split-panel routes follow browser history and rebuild cached content', async () => {
    const context = browserContext();
    const requests = [];
    context.fetch = async url => {
        requests.push(url);
        return {
            ok: true,
            async json() {
                if (url.includes('/quotes')) return { tweets: [{ tweet_id: 'quote' }], total: 1, limit: 20 };
                return { main: { tweet_id: url.split('/').at(-1) }, parents: [], children: [] };
            },
        };
    };
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.archiveResourcesLoaded = true;
    app.splitPanel = true;
    app.$refs.detailPanel = { scrollTop: 0 };

    await app.openThread('123');
    await app.toggleSplitPanel(false);
    assert.equal(app.viewMode, 'thread');
    assert.equal(app.panelMode, null);
    assert.equal(context.window.location.pathname, '/post/123');
    await app.toggleSplitPanel(true);
    assert.equal(app.panelMode, 'thread');
    await app.openThread('456', false, true);
    await app.openQuotes('456', true);
    assert.deepEqual(
        context.__state.historyCalls.filter(call => call[0] === 'push').map(call => call[3]),
        ['/post/123', '/post/456', '/post/456/quotes'],
    );
    assert.equal(app.panelStack.length, 3);

    app.panelGoBack();
    assert.equal(context.__state.historyCalls.at(-1)[0], 'back');
    await app.applyRoute(
        { viewMode: 'thread', tweetId: '456' },
        { fromPopState: true, state: { viewMode: 'thread', tweetId: '456', tweetNookDepth: 2 } },
    );
    assert.equal(app.panelMode, 'thread');
    assert.equal(app.panelThreadData.main.tweet_id, '456');
    assert.equal(app.panelStack.length, 2);

    await app.applyRoute(
        { viewMode: 'thread', tweetId: '123' },
        { fromPopState: true, state: { viewMode: 'thread', tweetId: '123', tweetNookDepth: 1 } },
    );
    assert.equal(app.panelStack.length, 1);
    assert.equal(app.panelThreadData.main.tweet_id, '123');
    await app.applyRoute(
        { viewMode: 'list' },
        { fromPopState: true, state: { viewMode: 'list', scrollY: 0, tweetNookDepth: 0 } },
    );
    assert.equal(app.panelMode, null);
    assert.equal(app.panelStack.length, 0);

    await app.applyRoute(
        { viewMode: 'thread', tweetId: '456' },
        { fromPopState: true, state: { viewMode: 'thread', tweetId: '456', tweetNookDepth: 2 } },
    );
    assert.equal(app.panelThreadData.main.tweet_id, '456');
    assert.equal(requests.filter(url => url === '/api/tweets/456').length, 1);
});

test('direct detail Back and detail searches stay inside the archive route', async () => {
    const context = browserContext('/post/123');
    context.fetch = async url => ({
        ok: true,
        async json() {
            if (url.startsWith('/api/tweets?')) return { tweets: [], page: 1, total_pages: 1, total: 0 };
            return { main: { tweet_id: '123' }, parents: [], children: [] };
        },
    });
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.archiveResourcesLoaded = true;
    app.activeRoute = { viewMode: 'thread', tweetId: '123' };
    app.navigationDepth = 0;
    await app.applyRoute(app.activeRoute, {
        state: { viewMode: 'thread', tweetId: '123', tweetNookDepth: 0 },
    });

    app.goBack();
    assert.equal(context.window.location.pathname, '/');
    assert.notEqual(context.__state.historyCalls.at(-1)[0], 'back');

    app.activeRoute = { viewMode: 'thread', tweetId: '123' };
    app.viewMode = 'thread';
    app.searchQuery = 'from:alice';
    app.search();
    assert.equal(context.window.location.pathname, '/');
    assert.equal(app.viewMode, 'list');
});

test('thread and quote navigation update history, panel state, and network state', async () => {
    const context = browserContext();
    let threadFetches = 0;
    context.fetch = async url => ({
        ok: true,
        async json() {
            if (url.includes('/quotes')) {
                return { tweets: [{ tweet_id: 'q1' }], total: 21, limit: 20 };
            }
            threadFetches++;
            return { main: { tweet_id: '42' }, parents: [], children: [] };
        },
    });
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    app.archiveResourcesLoaded = true;
    app.$refs.detailPanel = { scrollTop: 99 };

    await app.openThread('42');
    assert.equal(app.viewMode, 'thread');
    assert.equal(app.threadData.main.tweet_id, '42');
    assert.equal(app.loadingThread, false);
    assert.ok(
        context.__state.historyCalls.some(
            call => call[0] === 'push' && call[1].tweetId === '42' && call[3] === '/post/42',
        ),
    );

    await app.openQuotes('42');
    assert.equal(app.viewMode, 'quotes');
    assert.deepEqual(Array.from(app.quotesList, tweet => tweet.tweet_id), ['q1']);
    assert.equal(app.quotesTotalPages, 2);
    assert.equal(context.__state.historyCalls.at(-1)[3], '/post/42/quotes');

    app.splitPanel = true;
    context.window.innerWidth = 1280;
    await app.openThread('42');
    assert.equal(app.panelMode, 'thread');
    assert.equal(app.panelThreadData.main.tweet_id, '42');
    assert.equal(threadFetches, 1);
    await app.openQuotes('42', true);
    assert.equal(app.panelMode, 'quotes');
    assert.equal(app.panelStack.length, 2);
    app.panelGoBack();
    assert.equal(context.__state.historyCalls.at(-1)[0], 'back');
    app.closePanel();
    assert.equal(app.panelMode, null);
    assert.equal(context.window.location.pathname, '/');
});

test('thread cache reuses successes across modes but retries failures', async () => {
    const context = browserContext();
    let fetches = 0;
    let fail = true;
    context.fetch = async url => {
        fetches++;
        if (fail) return { ok: false };
        return {
            ok: true,
            async json() {
                return { main: { tweet_id: url.split('/').at(-1) }, parents: [], children: [] };
            },
        };
    };
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());

    await assert.rejects(app.loadThread('failed'), /Failed to load thread/);
    fail = false;
    const loaded = await app.loadThread('failed');
    const cached = await app.loadThread('failed');

    assert.equal(loaded.main.tweet_id, 'failed');
    assert.equal(cached, loaded);
    assert.equal(fetches, 2);

    app.threadCache.set('stale', {
        loadedAt: Date.now() - app.threadCacheTtlMs - 1,
        request: Promise.resolve({ main: { tweet_id: 'old' } }),
    });
    const refreshed = await app.loadThread('stale');
    assert.equal(refreshed.main.tweet_id, 'stale');
    assert.equal(fetches, 3);
});

test('goBack tears down playing videos before navigating history', () => {
    const context = browserContext();
    const video = {
        paused: false,
        currentTime: 30,
        pause() {
            this.paused = true;
        },
    };
    context.__state.videos.push(video);
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    app.navigationDepth = 1;
    app.goBack();
    assert.equal(video.paused, true);
    assert.equal(video.currentTime, 0);
    assert.equal(context.__state.historyCalls.at(-1)[0], 'back');
});

test('tag editing deduplicates case-insensitively and persists response state', async () => {
    const context = browserContext();
    const requests = [];
    context.fetch = async (url, options = {}) => {
        requests.push([url, options]);
        return {
            ok: true,
            async json() {
                return { tags: [{ tag: 'Night', count: 2 }] };
            },
        };
    };
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    app.tweets = [{ tweet_id: '1', media_tags: { description: 'Old description', tags: ['Old'] } }];
    app.panelThreadData = {
        main: { tweet_id: '1', media_tags: { description: 'Old description', tags: ['Old'] } },
        parents: [],
        children: [],
    };
    app.tagModalTweetId = '1';
    app.tagModalData = app.tweets[0].media_tags;
    app.editableTags = ['Night'];
    app.editableDescription = '  A moonlit view  ';
    app.addEditableTag(' night ');
    app.addEditableTag('Sky');
    assert.deepEqual(Array.from(app.editableTags), ['Night', 'Sky']);

    await app.saveTags();
    assert.deepEqual(Array.from(app.tweets[0].media_tags.tags), ['Night', 'Sky']);
    assert.equal(app.tweets[0].media_tags.description, 'A moonlit view');
    assert.equal(app.panelThreadData.main.media_tags.description, 'A moonlit view');
    assert.equal(requests[0][1].method, 'PUT');
    assert.deepEqual(JSON.parse(requests[0][1].body), {
        tags: ['Night', 'Sky'],
        description: 'A moonlit view',
    });

    await app.fetchGlobalTags();
    assert.equal(app.globalTags[0].tag, 'Night');
    assert.equal(app.globalTagsLoading, false);
});

test('tweet overflow supports copying raw data, untagged tweets, and a one-second Tags shortcut', async () => {
    const context = browserContext();
    let pressCallback = null;
    let clearedTimer = null;
    let copiedText = null;
    context.navigator = {
        clipboard: {
            async writeText(value) {
                copiedText = value;
            },
        },
    };
    context.setTimeout = (callback, delay) => {
        assert.equal(delay, 1000);
        pressCallback = callback;
        return 41;
    };
    context.clearTimeout = timer => { clearedTimer = timer; };
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());

    app.toggleTweetMenu('list:2');
    assert.equal(app.tweetMenuOpen, 'list:2');
    await app.copyRawTweet({ raw_json: { rest_id: '2', legacy: { full_text: 'Hello' } } });
    assert.equal(app.tweetMenuOpen, null);
    assert.equal(copiedText, '{\n  "rest_id": "2",\n  "legacy": {\n    "full_text": "Hello"\n  }\n}');

    app.toggleTweetMenu('list:2');
    app.openTagsFromTweetMenu('2', null);
    assert.equal(app.tweetMenuOpen, null);
    assert.equal(app.tagModalOpen, true);
    assert.equal(app.isEditingTags, false);
    assert.deepEqual(Array.from(app.tagModalData.tags), []);
    assert.equal(app.tagModalData.description, '');

    app.startEditingTags();
    assert.equal(app.isEditingTags, true);
    assert.deepEqual(Array.from(app.editableTags), []);

    app.startTweetMenuPress({ pointerType: 'touch', button: 0 }, '3', {
        description: 'Existing',
        tags: ['Bird'],
    });
    assert.equal(app.tweetMenuPressTimer, 41);
    pressCallback();
    assert.equal(app.tagModalTweetId, '3');
    assert.equal(app.tagModalData.description, 'Existing');
    assert.equal(app.isEditingTags, false);
    assert.equal(app.tweetMenuPressTimer, null);

    app.startTweetMenuPress({ pointerType: 'mouse', button: 0 }, '4', null);
    app.cancelTweetMenuPress();
    assert.equal(clearedTimer, 41);
    assert.equal(app.tweetMenuPressTimer, null);
});

test('tweet overflow copies raw data when the Clipboard API is unavailable', async () => {
    const context = browserContext();
    let copiedText = null;
    const textarea = {
        value: '',
        style: {},
        setAttribute() {},
        focus() {},
        select() {
            copiedText = this.value;
        },
    };
    context.navigator = {};
    context.document.createElement = tag => {
        assert.equal(tag, 'textarea');
        return textarea;
    };
    context.document.body.appendChild = node => assert.equal(node, textarea);
    context.document.body.removeChild = node => assert.equal(node, textarea);
    context.document.execCommand = command => {
        assert.equal(command, 'copy');
        return true;
    };
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());

    await app.copyRawTweet({ raw_json: { rest_id: '3', legacy: { full_text: 'Fallback' } } });
    assert.equal(copiedText, '{\n  "rest_id": "3",\n  "legacy": {\n    "full_text": "Fallback"\n  }\n}');
});

test('tweet overflow markup covers list and detail surfaces without legacy tag icons', () => {
    const html = fs.readFileSync(
        path.join(ROOT, 'tweetnook', 'web', 'index.html'),
        'utf8',
    );
    const appJs = fs.readFileSync(path.join(JS_DIR, 'app.js'), 'utf8');

    assert.equal((html.match(/aria-label="More actions"/g) || []).length, 3);
    assert.equal((html.match(/<span>Tags<\/span>/g) || []).length, 3);
    assert.equal((html.match(/<span>Copy raw<\/span>/g) || []).length, 3);
    assert.match(html, /copyRawTweet\(tweet\)/);
    assert.match(html, /copyRawTweet\(threadData\.main\)/);
    assert.match(html, /copyRawTweet\(panelThreadData\.main\)/);
    assert.equal((html.match(/right-0 top-0 z-50 w-56/g) || []).length, 3);
    assert.equal((html.match(/'opacity-0': tweetMenuOpen ===/g) || []).length, 3);
    assert.doesNotMatch(html, /right-\[18px\] top-\[18px\]/);
    assert.doesNotMatch(html, /bg-\[var\(--dropdown-bg\)\] py-1 shadow-2xl/);
    assert.doesNotMatch(
        html,
        /hover:bg-\[var\(--hover-bg\)\] transition">\s*<svg viewBox="0 0 24 24" class="w-5 h-5/,
    );
    assert.match(html, /startTweetMenuPress\(\$event, tweet\.tweet_id, tweet\.media_tags\)/);
    assert.match(html, /startTweetMenuPress\(\$event, threadData\.main\.tweet_id/);
    assert.match(html, /startTweetMenuPress\(\$event, panelThreadData\.main\.tweet_id/);
    assert.equal((html.match(/renderQuotePlaceholder\(getQuoteTweet/g) || []).length, 7);
    assert.doesNotMatch(html, /View quoted tweet tags/);
    assert.doesNotMatch(appJs, /title="View Tags"/);
    assert.match(html, /x-model="editableDescription"/);
});

test('thread reply surfaces render quoted tweets in both presentations', () => {
    const html = fs.readFileSync(
        path.join(ROOT, 'tweetnook', 'web', 'index.html'),
        'utf8',
    );

    assert.equal((html.match(/<template x-if="getQuoteTweet\(child\)">/g) || []).length, 2);
    assert.equal((html.match(/<template x-if="getQuoteTweet\(reply\)">/g) || []).length, 2);
    assert.equal((html.match(/renderQuotePlaceholder\(getQuoteTweet\(child\)\)/g) || []).length, 2);
    assert.equal((html.match(/renderQuotePlaceholder\(getQuoteTweet\(reply\)\)/g) || []).length, 2);
    assert.equal((html.match(/formatText\(getQuoteTweet\(child\)\)/g) || []).length, 2);
    assert.equal((html.match(/formatText\(getQuoteTweet\(reply\)\)/g) || []).length, 2);
    assert.equal((html.match(/renderMediaGrid\(child\.qt_media\)/g) || []).length, 2);
    assert.equal((html.match(/renderMediaGrid\(reply\.qt_media\)/g) || []).length, 2);
    assert.match(html, /openThread\(getTweetId\(getQuoteTweet\(child\)\)\)/);
    assert.match(html, /openThread\(getTweetId\(getQuoteTweet\(child\)\), false, true\)/);
    assert.match(html, /openThread\(getTweetId\(getQuoteTweet\(reply\)\)\)/);
    assert.match(html, /openThread\(getTweetId\(getQuoteTweet\(reply\)\), false, true\)/);
});

test('reply-recipient markup opens profile cards on every tweet surface', () => {
    const html = fs.readFileSync(
        path.join(ROOT, 'tweetnook', 'web', 'index.html'),
        'utf8',
    );

    assert.equal((html.match(/openUsernameProfileCard\(\$event, getReplyTo/g) || []).length, 18);
    assert.doesNotMatch(html, /searchFrom\(getReplyTo/);
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
        ['themes.js', 'app.js'],
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
        if (url === '/api/setup/archive' && options.method === 'PUT') {
            archive = { ...archive, uploaded: true, filename: 'archive.zip', size: 2048 };
            return { ok: true, async json() { return archive; } };
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
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());

    await app.fetchSetup();
    assert.equal(app.setupAuth.auth_token, '********');
    app.setupFlowStep = 2;
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

    await app.uploadSetupArchive({
        target: { files: [{ name: 'archive.zip', size: 2048 }], value: 'archive.zip' },
    });
    assert.equal(app.setupData.archive.uploaded, true);
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

    await app.clearSetupArchive();
    assert.equal(app.setupData.archive.uploaded, false);
    assert.ok(requests.some(([url, method]) => url === '/api/setup/archive' && method === 'DELETE'));
});

test('setup uses a dedicated responsive X-style modal shell', () => {
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook/web/index.html'), 'utf8');
    const css = fs.readFileSync(path.join(ROOT, 'tweetnook/web/static/css/styles.css'), 'utf8');
    const js = fs.readFileSync(path.join(JS_DIR, 'app.js'), 'utf8');
    const settingsMarkup = html.slice(html.indexOf('<!-- Settings Modal -->'), html.indexOf('<!-- Setup Modal -->'));
    const setupMarkup = html.slice(html.indexOf('<!-- Setup Modal -->'), html.indexOf('<!-- Automated Tagging Spend History Modal -->'));

    assert.match(settingsMarkup, /x-show="showSettingsModal"/);
    assert.match(settingsMarkup, /class="settings-modal-shell/);
    assert.doesNotMatch(settingsMarkup, /showSetupModal|setup-modal-header|setup-modal-content/);
    assert.match(settingsMarkup, /@click="settingsTab = 'setup'; fetchSetup\(\)"/);
    assert.doesNotMatch(settingsMarkup, /@click="openSetup\(\)"/);
    assert.match(settingsMarkup, /Setup status/);
    assert.match(settingsMarkup, /@click="rerunSetup\(\)">Run setup again/);
    assert.doesNotMatch(settingsMarkup, />View activity<\/button>/);
    assert.match(setupMarkup, /x-show="showSetupModal"/);
    assert.match(setupMarkup, /class="setup-modal-shell/);
    assert.match(setupMarkup, /id="setup-modal-title" class="setup-modal-heading">Setup<\/h1>/);
    assert.match(setupMarkup, /class="setup-desktop-required" role="note"/);
    assert.match(setupMarkup, /Continue setup on desktop/);
    assert.doesNotMatch(setupMarkup, /class="setup-modal-mark"/);
    assert.match(setupMarkup, /class="setup-modal-progress"/);
    assert.doesNotMatch(setupMarkup, /class="setup-status/);
    assert.match(setupMarkup, /class="setup-step setup-flow-panel"/);
    assert.match(setupMarkup, /x-transition:enter="setup-flow-enter"/);
    assert.match(html, /Bring your archive with you/);
    assert.match(setupMarkup, /<button type="button" @click\.stop="clearSetupArchive\(\)"\s+class="setup-btn is-ghost-danger"\s+aria-label="Remove uploaded archive" title="Remove uploaded archive">/);
    assert.match(html, /Connect your Twitter\/X account/);
    assert.match(setupMarkup, /<button type="button" class="setup-btn" :disabled="setupSavingAuth \|\| setupAuthSuccessPending" @click="goToSetupStep\(1\)">Back<\/button>\s+<button type="button" @click="if \(!setupAuthSuccessPending\) saveSetupAuth\(\)" :disabled="setupSavingAuth"\s+:aria-disabled="setupAuthSuccessPending"\s+class="setup-btn is-primary flex-1">/);
    assert.match(setupMarkup, /x-text="setupSavingAuth \? 'Testing authentication…' : \(setupAuthSuccessPending \? 'Test successful' : 'Save and continue'\)"/);
    assert.doesNotMatch(setupMarkup, /x-show="setupAuthResult\?\.success"/);
    assert.doesNotMatch(setupMarkup, /Save and test/);
    assert.doesNotMatch(setupMarkup, /@click="goToSetupStep\(3\)">Continue<\/button>/);
    assert.match(setupMarkup, /<summary class="cursor-pointer">Other setup options<\/summary>[\s\S]*?Continue without connecting Twitter\/X/);
    assert.match(setupMarkup, />Continue setup<\/button>/);
    assert.match(setupMarkup, /<p class="text-sm text-\[var\(--accent-color\)\] mt-2">The username field is unused and can be left blank\.<\/p>/);
    assert.match(html, /Protect your archive/);
    assert.match(css, /\.setup-modal-shell\s*\{[^}]*600px[^}]*720px/s);
    assert.match(css, /@media \(max-width: 767px\)[\s\S]*?\.setup-modal-shell\s*\{[^}]*height: 100%/);
    assert.match(css, /@media \(max-width: 767px\)[\s\S]*?\.setup-modal-progress,[\s\S]*?\.setup-modal-content\s*\{ display: none !important; \}/);
    assert.match(js, /openSetup\(\)\s*\{[\s\S]*?this\.showSetupModal = true;[\s\S]*?this\.showSettingsModal = false;/);
    assert.match(js, /openSettings\(\)\s*\{[\s\S]*?this\.showSetupModal = false;[\s\S]*?this\.showSettingsModal = true;/);
    assert.match(js, /goToSetupStep\(step\)[\s\S]*?this\.setupDirection = next < this\.setupFlowStep \? 'back' : 'forward';/);
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
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
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
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'index.html'), 'utf8');
    assert.match(html, /<option :value="true">On<\/option>/);
    assert.match(html, /<option :value="false">Off<\/option>/);
    assert.doesNotMatch(html, /<option value="(?:true|false)">/);
});

test('text and card renderers escape HTML and reject active URL schemes', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    const formatted = app.formatText({
        tweet_id: '1',
        text: '<img src=x onerror=alert(1)> @alice #topic https://t.co/x',
        raw_json: {
            legacy: {
                entities: {
                    urls: [
                        {
                            url: 'https://t.co/x',
                            expanded_url: 'javascript:alert(1)',
                            display_url: '<svg onload=alert(1)>',
                        },
                    ],
                },
            },
        },
    });
    assert.match(formatted, /&lt;img/);
    assert.match(formatted, /class="mention/);
    assert.match(formatted, /class="hashtag/);
    assert.match(formatted, /href="#"/);
    assert.match(formatted, /&lt;svg/);
    assert.doesNotMatch(formatted, /<img src=x/);

    const card = app.renderCard({
        raw_json: {
            card: {
                name: 'summary_large_image',
                binding_values: [
                    { key: 'title', value: { string_value: '<img onerror=x>' } },
                    { key: 'description', value: { string_value: '<script>x</script>' } },
                    { key: 'card_url', value: { string_value: 'javascript:x' } },
                    {
                        key: 'thumbnail_image_original',
                        value: { image_value: { url: 'data:text/html,x' } },
                    },
                ],
            },
        },
    });
    assert.match(card, /href="#"/);
    assert.doesNotMatch(card, /<img src=/);
    assert.match(card, /&lt;img onerror=x&gt;/);
    assert.doesNotMatch(card, /<script>/);
});

test('availability placeholders replace content and support normalized attachments', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    const unavailable = {
        tweet_id: 'missing',
        text: 'stale private text',
        raw_json: { card: { name: 'summary' } },
        availability: {
            placeholder: true,
            reason: 'protected_account',
            message: 'This tweet is from a protected account. <unsafe>',
        },
    };

    const placeholder = app.formatText(unavailable);
    assert.match(placeholder, /tweet-skeleton/);
    assert.match(placeholder, /tweet-skeleton-content/);
    assert.match(placeholder, /data-tweet-skeleton="true"/);
    assert.match(placeholder, /data-availability-reason="protected_account"/);
    assert.match(placeholder, /This tweet is from a protected account\. &lt;unsafe&gt;/);
    assert.ok(placeholder.indexOf('tweet-skeleton-message') < placeholder.indexOf('tweet-skeleton-lines'));
    assert.doesNotMatch(placeholder, /<unsafe>/);
    assert.doesNotMatch(placeholder, /stale private text/);
    assert.equal(app.renderCard(unavailable), '');
    assert.equal(app.renderActionBar(unavailable), '');
    assert.equal(app.getQuoteTweet(unavailable), null);

    const wrapper = {
        quoted_tweet: unavailable,
        retweeted_tweet: unavailable,
        raw_json: {},
    };
    assert.equal(app.getQuoteTweet(wrapper), unavailable);
    assert.equal(app.getQuoteText(app.getQuoteTweet(wrapper)), unavailable.availability.message);
    assert.equal(app.getRetweet(wrapper), unavailable);
    assert.equal(app.getTweetId(unavailable), 'missing');

    const quotePlaceholder = app.renderQuotePlaceholder(unavailable);
    assert.match(quotePlaceholder, /tweet-skeleton tweet-skeleton-compact/);
    assert.match(quotePlaceholder, /data-tweet-skeleton="true"/);
    assert.match(quotePlaceholder, /data-availability-reason="protected_account"/);
    assert.match(quotePlaceholder, /aria-label="Quoted tweet unavailable"/);
    assert.match(quotePlaceholder, /This tweet is from a protected account\. &lt;unsafe&gt;/);
    assert.doesNotMatch(quotePlaceholder, /stale private text/);

    const missingText = app.formatText({
        tweet_id: 'textless',
        text: '',
        raw_json: { rest_id: 'textless', legacy: {} },
    });
    assert.match(missingText, /data-availability-reason="text_not_archived"/);
    assert.match(missingText, /tweet-skeleton-content/);
    assert.match(missingText, /Tweet text was not captured in the local archive\./);
    assert.equal(app.renderActionBar({
        tweet_id: 'textless',
        text: '',
        raw_json: { rest_id: 'textless', legacy: {} },
    }), '');
    assert.match(
        app.formatText({ rest_id: 'quoted-textless', legacy: {} }),
        /tweet-skeleton[\s\S]*data-availability-reason="text_not_archived"/,
    );
});

test('missing tweet skeletons cover terminal, relation, tombstone, and textless variants', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    const reasons = [
        'protected_account',
        'suspended_account',
        'account_missing',
        'deleted_by_author',
        'archive_deleted',
        'withheld',
        'not_found',
        'unavailable_unknown',
    ];

    for (const reason of reasons) {
        const missing = {
            tweet_id: `missing-${reason}`,
            availability: { placeholder: true, reason, message: `hidden ${reason}` },
        };
        const direct = app.renderTweetPlaceholder(missing);
        const quoted = app.renderQuotePlaceholder(missing);
        assert.match(direct, new RegExp(`data-availability-reason="${reason}"`));
        assert.match(quoted, new RegExp(`data-availability-reason="${reason}"`));
        assert.match(direct, /tweet-skeleton-content/);
        assert.match(quoted, /tweet-skeleton-compact/);
        assert.match(direct, new RegExp(`hidden ${reason}`));
        assert.match(quoted, new RegExp(`hidden ${reason}`));
        assert.ok(direct.indexOf('tweet-skeleton-message') < direct.indexOf('tweet-skeleton-lines'));
        assert.ok(quoted.indexOf('tweet-skeleton-message') < quoted.indexOf('tweet-skeleton-lines'));
    }

    const relationOnly = {
        tweet_id: 'missing-quoted',
        availability: { placeholder: true, reason: 'not_archived', message: 'not captured' },
    };
    const wrapper = { quoted_tweet: relationOnly, retweeted_tweet: relationOnly, raw_json: {} };
    assert.equal(app.getQuoteTweet(wrapper), relationOnly);
    assert.equal(app.getRetweet(wrapper), relationOnly);
    assert.match(app.renderQuotePlaceholder(app.getQuoteTweet(wrapper)), /tweet-skeleton-compact/);
    assert.match(app.renderTweetPlaceholder(app.getRetweet(wrapper)), /tweet-skeleton-content/);
    assert.match(app.renderQuotePlaceholder(relationOnly), /not captured/);

    const tombstone = { __typename: 'TweetTombstone', rest_id: 'tombstone-1' };
    assert.equal(app.isPlaceholderTweet(tombstone), true);
    assert.match(app.formatText(tombstone), /data-tweet-skeleton="true"/);
    assert.match(app.formatText(tombstone), /This tweet is unavailable\./);
    assert.match(app.renderQuotePlaceholder(tombstone), /data-availability-reason="unavailable_unknown"/);

    const css = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'static', 'css', 'styles.css'), 'utf8');
    assert.match(css, /\.tweet-skeleton/);
    assert.match(css, /\.tweet-skeleton-main \{[\s\S]*font-size: 0;[\s\S]*line-height: 0;/);
    assert.doesNotMatch(css, /tweet-skeleton-pulse/);
    assert.doesNotMatch(css, /animation:[^;]*tweet-skeleton/);
});

test('media renderer preserves dimensions and adds Twitter/X-style playback indicators', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());

    const photo = app.renderMediaGrid([
        {
            type: 'photo',
            width: 1200,
            height: 600,
            download: { local_path: 'media/a.jpg' },
        },
    ]);
    assert.match(photo, /aspect-ratio: 2/);
    assert.match(photo, /<img src="\/media\/a.jpg"/);

    const video = app.renderMediaGrid([
        {
            type: 'video',
            duration_millis: 120000,
            download: {
                local_path: 'media/a.mp4',
                thumbnail_local_path: 'media/a.jpg',
            },
        },
    ]);
    assert.match(video, /controls/);
    assert.doesNotMatch(video, /autoplay muted/);
    assert.match(video, /class="[^"]*media-video-player[^"]*"/);
    assert.match(video, /class="media-video-duration" data-video-duration/);
    assert.match(video, /aria-label="Video duration 2:00">2:00<\/span>/);
    assert.match(video, /onloadedmetadata="window\.tweetNookSyncVideoDuration\(this\)"/);
    assert.match(video, /onmouseenter="window\.tweetNookSetVideoUiVisible\(this, true\)"/);
    assert.match(video, /onmouseleave="window\.tweetNookSetVideoUiVisible\(this, false\)"/);
    assert.match(video, /onfocus="window\.tweetNookSetVideoUiVisible\(this, true\)"/);
    assert.match(video, /onblur="window\.tweetNookSetVideoUiVisible\(this, false\)"/);
    assert.equal(app.formatMediaDuration(3000), '0:03');
    assert.equal(app.formatMediaDuration(3723000), '1:02:03');
    assert.equal(app.formatMediaDuration(null), '');

    const gif = app.renderMediaGrid([
        {
            type: 'animated_gif',
            download: { local_path: 'media/a.mp4' },
        },
    ]);
    assert.match(gif, /autoplay muted playsinline/);
    assert.match(gif, /loop/);
    assert.match(gif, /data-animated-gif/);
    assert.match(gif, /data-gif-toggle aria-label="Pause GIF"/);
    assert.match(gif, /media-gif-pause-icon/);
    assert.match(gif, /media-gif-play-icon/);
    assert.doesNotMatch(gif, /media-video-duration/);

    const missing = app.renderMediaGrid([{ type: 'photo', width: 4, height: 3 }]);
    assert.match(missing, /Media not downloaded/);

    const grid = app.renderMediaGrid([
        { type: 'photo', download: { local_path: 'media/1.jpg' } },
        { type: 'photo', download: { local_path: 'media/2.jpg' } },
        { type: 'photo', download: { local_path: 'media/3.jpg' } },
    ]);
    assert.match(grid, /grid-rows-2/);
    assert.match(grid, /row-span-2/);

    const gridVideo = app.renderMediaGrid([
        { type: 'photo', download: { local_path: 'media/1.jpg' } },
        {
            type: 'video',
            duration_millis: 3000,
            download: { local_path: 'media/2.mp4' },
        },
    ]);
    assert.match(gridVideo, /media-video-player/);
    assert.match(gridVideo, /aria-label="Video duration 0:03">0:03<\/span>/);

    const videoWithoutArchivedDuration = app.renderMediaGrid([
        { type: 'video', download: { local_path: 'media/no-duration.mp4' } },
    ]);
    assert.match(
        videoWithoutArchivedDuration,
        /class="media-video-duration" data-video-duration hidden aria-hidden="true"><\/span>/,
    );

    const css = fs.readFileSync(
        path.join(ROOT, 'tweetnook', 'web', 'static', 'css', 'styles.css'),
        'utf8',
    );
    assert.match(css, /\.media-video-duration,/);
    assert.match(css, /\.media-video-player:hover \.media-video-duration,/);
    assert.match(css, /\.media-video-player:focus-within \.media-video-duration,/);
    assert.match(css, /\.media-video-player\.is-player-ui-visible \.media-video-duration/);
    assert.match(css, /\.media-gif-indicator \{/);
    assert.match(css, /\.media-gif-indicator\.is-paused \.media-gif-play-icon/);
});

test('video duration uses loaded media metadata and yields to the player UI', () => {
    const context = browserContext();
    loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const classes = new Set();
    const attributes = new Map([['aria-hidden', 'true']]);
    const indicator = {
        hidden: true,
        textContent: '',
        removeAttribute(name) {
            attributes.delete(name);
        },
        setAttribute(name, value) {
            attributes.set(name, value);
        },
    };
    const frame = {
        classList: {
            toggle(name, enabled) {
                if (enabled) classes.add(name);
                else classes.delete(name);
            },
        },
        querySelector: () => indicator,
    };
    const video = { duration: 125.9, parentElement: frame };

    context.window.tweetNookSyncVideoDuration(video);
    assert.equal(indicator.textContent, '2:05');
    assert.equal(indicator.hidden, false);
    assert.equal(attributes.get('aria-label'), 'Video duration 2:05');
    assert.equal(attributes.has('aria-hidden'), false);

    context.window.tweetNookSetVideoUiVisible(video, true);
    assert.ok(classes.has('is-player-ui-visible'));
    context.window.tweetNookSetVideoUiVisible(video, false);
    assert.ok(!classes.has('is-player-ui-visible'));
});

test('GIF indicator tracks playback state and toggles the archived animation', async () => {
    const context = browserContext();
    loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const classes = new Set();
    const attributes = new Map();
    let playCalls = 0;
    let pauseCalls = 0;
    const indicator = {
        classList: {
            toggle(name, enabled) {
                if (enabled) classes.add(name);
                else classes.delete(name);
            },
        },
        setAttribute(name, value) {
            attributes.set(name, value);
        },
    };
    const video = {
        paused: false,
        ended: false,
        parentElement: { querySelector: () => indicator },
        play() {
            playCalls += 1;
            this.paused = false;
            context.window.tweetNookSyncGifIndicator(this);
            return Promise.resolve();
        },
        pause() {
            pauseCalls += 1;
            this.paused = true;
            context.window.tweetNookSyncGifIndicator(this);
        },
    };
    indicator.parentElement = { querySelector: () => video };
    const event = {
        prevented: false,
        stopped: false,
        preventDefault() { this.prevented = true; },
        stopPropagation() { this.stopped = true; },
    };

    context.window.tweetNookSyncGifIndicator(video);
    assert.equal(attributes.get('aria-label'), 'Pause GIF');
    context.window.tweetNookToggleGif(event, indicator);
    assert.equal(pauseCalls, 1);
    assert.ok(classes.has('is-paused'));
    assert.equal(attributes.get('title'), 'Play GIF');
    context.window.tweetNookToggleGif(event, indicator);
    await Promise.resolve();
    assert.equal(playCalls, 1);
    assert.ok(!classes.has('is-paused'));
    assert.equal(event.prevented, true);
    assert.equal(event.stopped, true);
});

test('activity drawer loads any pipeline and starts production jobs', async () => {
    const context = browserContext();
    const calls = [];
    let active = true;
    context.fetch = async (url, options = {}) => {
        calls.push([url, options.method || 'GET']);
        if (url === '/api/activity/import') {
            active = true;
            return { ok: true, json: async () => ({ started: true, kind: 'import', run_id: 'run' }) };
        }
        if (url === '/api/activity/stop') {
            active = false;
            return { ok: true, json: async () => ({ stopping: true }) };
        }
        return {
            ok: true,
            json: async () => active ? {
                active: true,
                snapshot: {
                    title: 'tweetnook import enrich',
                    started_at: Date.now() / 1000,
                    steps: [{ key: 'enrich', completed: 5, total: 10 }],
                    issues: [],
                },
                schedule: { configured: false, relative: 'Not configured', date: 'Use cron or a service timer' },
            } : {
                active: false,
                snapshot: null,
                last_snapshot: {
                    title: 'tweetnook import enrich',
                    completed_at: Date.now() / 1000,
                    steps: [{ key: 'enrich', state: 'complete', completed: 10, total: 10 }],
                    issues: [],
                },
                schedule: { configured: false, relative: 'Not configured', date: 'Use cron or a service timer' },
            },
        };
    };
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());

    await app.fetchActivityStatus();
    assert.equal(app.activity.title, 'tweetnook import enrich');
    assert.equal(app.activityPercent(app.activity.steps[0]), 50);

    active = false;
    await app.fetchActivityStatus();
    assert.equal(app.activity, null);
    assert.equal(app.lastActivity.title, 'tweetnook import enrich');
    assert.equal(app.activitySchedule.relative, 'Not configured');
    await app.startActivity('import');
    assert.ok(calls.some(([url, method]) => url === '/api/activity/import' && method === 'POST'));
    assert.equal(app.activityStartPending, false);
    assert.equal(app.activityStartingKind, null);
    assert.equal(app.activity.title, 'tweetnook import enrich');
    await app.stopActivity();
    assert.ok(calls.some(([url, method]) => url === '/api/activity/stop' && method === 'POST'));
    assert.equal(app.activity, null);
});

test('incomplete enrichment starts the fixed Web action and refreshes status', async () => {
    const context = browserContext();
    const requests = [];
    context.fetch = async (url, options = {}) => {
        requests.push([url, options.method || 'GET']);
        if (url === '/api/activity/enrich') {
            return {ok: true, json: async () => ({started: true, kind: 'enrich', run_id: 'run'})};
        }
        throw new Error(`unexpected request: ${url}`);
    };
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.archiveReady = true;
    let activityRefreshes = 0, enrichmentRefreshes = 0;
    app.fetchActivityStatus = async () => { activityRefreshes++; app.activityStartPending = false; };
    app.fetchArchiveEnrichmentStatus = async () => { enrichmentRefreshes++; };

    await app.startActivity('enrich');

    assert.deepEqual(requests, [['/api/activity/enrich', 'POST']]);
    assert.equal(activityRefreshes, 1);
    assert.equal(enrichmentRefreshes, 1);
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook/web/index.html'), 'utf8');
    assert.match(html, /Continue enrichment/);
    assert.doesNotMatch(html, /tweetnook import enrich/);
});

test('schedule settings load, normalize, and save through the activity API', async () => {
    const context = browserContext();
    const requests = [];
    let schedule = {
        configured: true,
        enabled: true,
        description: 'Every week',
        relative: 'in 3 days',
        date: 'Mon, Aug 24 at 9:15 AM',
        config: {
            enabled: true,
            cadence: 'weekly',
            every_hours: 6,
            time: '09:15',
            randomize_time: true,
            random_offset_min_hours: 0,
            random_offset_max_hours: 2,
            weekday: 0,
            day_of_month: 1,
            timezone: 'UTC',
        },
    };
    context.fetch = async (url, options = {}) => {
        assert.equal(url, '/api/activity/schedule');
        requests.push(options);
        if (options.method === 'PUT') {
            const config = JSON.parse(options.body);
            schedule = {
                ...schedule,
                configured: config.enabled,
                enabled: config.enabled,
                description: 'Every month',
                relative: 'in 12 days',
                date: 'Tue, Sep 1 at 4:30 AM',
                config,
            };
        }
        return { ok: true, async json() { return schedule; } };
    };
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());

    await app.fetchScheduleSettings();
    assert.equal(app.scheduleForm.cadence, 'weekly');
    assert.equal(app.scheduleForm.weekday, 0);
    assert.equal(app.scheduleTimeHour, '9');
    assert.equal(app.scheduleTimeMinute, '15');
    assert.equal(app.scheduleTimePeriod, 'AM');
    assert.equal(app.scheduleForm.randomize_time, true);
    assert.equal(app.normalizeScheduleConfig({}).randomize_time, true);
    assert.equal(app.normalizeScheduleConfig({ randomize_time: false }).randomize_time, false);
    assert.equal(app.scheduleForm.random_offset_min_hours, 0);
    assert.equal(app.scheduleForm.random_offset_max_hours, 2);
    assert.equal(app.normalizeScheduleConfig({}).random_offset_min_hours, 0);
    assert.equal(app.normalizeScheduleConfig({}).random_offset_max_hours, 2);
    assert.equal(app.scheduleHasChanges(), false);
    assert.ok(app.scheduleTimezoneOptions().some(option => option.value === 'UTC'));
    assert.ok(app.scheduleTimezoneOptions().some(option => option.value === 'America/Detroit'));

    app.setScheduleTimePart('period', 'PM');
    app.setScheduleTimePart('minute', '45');
    assert.equal(app.scheduleForm.time, '21:45');
    app.scheduleForm.cadence = 'monthly';
    app.scheduleForm.day_of_month = 31;
    app.scheduleForm.randomize_time = true;
    app.scheduleForm.random_offset_min_hours = 0.5;
    app.scheduleForm.random_offset_max_hours = 2;
    app.scheduleFieldChanged();
    assert.equal(app.scheduleHasChanges(), true);
    await app.saveScheduleSettings();

    assert.equal(requests.at(-1).method, 'PUT');
    assert.equal(JSON.parse(requests.at(-1).body).day_of_month, 31);
    assert.equal(JSON.parse(requests.at(-1).body).time, '21:45');
    assert.equal(JSON.parse(requests.at(-1).body).random_offset_min_hours, 0.5);
    assert.equal(app.scheduleSettings.description, 'Every month');
    assert.equal(app.activitySchedule.description, 'Every month');
    assert.equal(app.scheduleSaved, true);
    assert.equal(app.scheduleHasChanges(), false);
});

test('schedule settings use the established Settings control patterns', () => {
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'index.html'), 'utf8');
    const start = html.indexOf('<!-- Scheduled sync settings -->');
    const end = html.indexOf('<!-- Activity logs -->');
    const scheduleHtml = html.slice(start, end);

    assert.match(scheduleHtml, /id="save-schedule-btn"/);
    assert.match(scheduleHtml, /<div class="mb-6 flex items-start justify-between gap-4">[\s\S]*Scheduled syncs are off[\s\S]*id="save-schedule-btn"/);
    assert.match(scheduleHtml, /Enable scheduled syncs/);
    assert.doesNotMatch(scheduleHtml, /Run a full sync automatically while the Web service is running\./);
    assert.match(scheduleHtml, /x-model="scheduleForm\.enabled"/);
    assert.match(scheduleHtml, /<fieldset[^>]*:disabled="!scheduleForm\.enabled"/);
    assert.match(scheduleHtml, /role="group" aria-label="Schedule frequency"/);
    assert.match(scheduleHtml, /scheduleForm\.cadence = cadence\.key/);
    assert.match(scheduleHtml, /x-model\.number="scheduleForm\.every_hours"/);
    assert.match(scheduleHtml, /aria-label="Hour"/);
    assert.match(scheduleHtml, /aria-label="Minute"/);
    assert.match(scheduleHtml, /aria-label="AM or PM"/);
    assert.match(scheduleHtml, /<div class="flex w-full max-w-md items-center gap-3"[^>]*role="group" aria-label="Run time">/);
    assert.match(scheduleHtml, /<div class="flex h-12 min-w-0 flex-1 items-stretch rounded-xl/);
    assert.match(scheduleHtml, /class="relative h-12 w-28 shrink-0"/);
    assert.match(scheduleHtml, /class="flex items-center px-1 text-lg font-bold text-\[var\(--text-secondary\)\]" aria-hidden="true">:<\/span>/);
    assert.equal((scheduleHtml.match(/schedule-dropdown-chevron/g) || []).length, 5);
    assert.match(scheduleHtml, /x-model="scheduleTimeHour"[^>]*class="[^"]*appearance-none[^"]*pr-9/);
    assert.match(scheduleHtml, /x-model="scheduleTimeMinute"[^>]*class="[^"]*appearance-none[^"]*pr-9/);
    assert.match(scheduleHtml, /x-model="scheduleTimePeriod"[^>]*class="[^"]*appearance-none[^"]*pr-9/);
    assert.match(scheduleHtml, /x-model\.number="scheduleForm\.weekday"[^>]*class="[^"]*appearance-none[^"]*pr-9/);
    assert.match(scheduleHtml, /x-model="scheduleForm\.timezone"[^>]*class="[^"]*appearance-none[^"]*pr-9/);
    assert.doesNotMatch(scheduleHtml, /type="time"/);
    assert.match(scheduleHtml, /x-model="scheduleForm\.randomize_time"/);
    assert.match(scheduleHtml, /random time between these two added delays after the selected time/);
    assert.match(scheduleHtml, /x-model\.number="scheduleForm\.random_offset_min_hours"/);
    assert.match(scheduleHtml, /x-model\.number="scheduleForm\.random_offset_max_hours"/);
    assert.match(scheduleHtml, /min="0" max="24" step="0\.25" x-model\.number="scheduleForm\.random_offset_min_hours"/);
    assert.match(scheduleHtml, /min="0" max="24" step="0\.25" x-model\.number="scheduleForm\.random_offset_max_hours"/);
    assert.doesNotMatch(scheduleHtml, /Negative hours run earlier|signed offset|From offset \(hours\)/);
    assert.match(scheduleHtml, /x-model\.number="scheduleForm\.weekday"/);
    assert.match(scheduleHtml, /x-model\.number="scheduleForm\.day_of_month"/);
    assert.match(scheduleHtml, /x-model="scheduleForm\.timezone"/);
    assert.doesNotMatch(scheduleHtml, /Configuration controls will be added here/);
});

test('unchecked square checkboxes use thinner outlines while checked states retain weight', () => {
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'index.html'), 'utf8');
    assert.equal(
        (html.match(/class="flex h-6 w-6 items-center justify-center rounded-lg border-2 transition"/g) || []).length,
        4,
    );
    assert.equal((html.match(/text-white border-\[3px\]'/g) || []).length, 4);
});

test('activity steps reverse wheel input and update the connected scene and fade immediately', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.activity = {
        title: 'tweetnook sync',
        steps: [
            { key: 'one', title: 'First step', state: 'complete', summary: 'done' },
            { key: 'two', title: 'Second step', state: 'skipped', summary: 'not needed' },
            { key: 'three', title: 'Current step', state: 'active', completed: 2, total: 4 },
            { key: 'four', title: 'Later step', state: 'pending' },
        ],
        issues: [
            { level: 'warning', message: 'Retrying one item', count: 2 },
            { level: 'error', message: 'One item failed', count: 1 },
        ],
    };

    assert.equal(app.activityActiveStep().key, 'three');
    assert.deepEqual(app.activityCompletedSteps().map(step => step.key), ['one', 'two']);
    assert.equal(app.activityCompletedStackHeight(), '128px');
    assert.equal(app.activityIssues().length, 2);
    assert.equal(app.activityIssueCount(), 3);

    const styleValues = new Map();
    const viewport = {
        scrollTop: 18,
        scrollHeight: 352,
        clientHeight: 200,
        style: {
            setProperty(name, value) {
                styleValues.set(name, value);
            },
        },
    };
    const controlStyleValues = new Map();
    viewport.closest = () => ({
        querySelector() {
            return {
                style: {
                    setProperty(name, value) {
                        controlStyleValues.set(name, value);
                    },
                },
            };
        },
    });
    let prevented = false;
    app.reverseActivityScroll({
        currentTarget: viewport,
        deltaY: -12,
        deltaMode: 0,
        preventDefault() {
            prevented = true;
        },
    });
    assert.equal(prevented, true);
    assert.equal(viewport.scrollTop, 30);
    assert.equal(styleValues.get('--activity-scroll-shift'), '60px');
    assert.equal(controlStyleValues.get('--activity-fade-opacity'), String(1 - 30 / 64));
    assert.equal(controlStyleValues.get('--activity-fade-shift'), '-30px');

    viewport.scrollTop = 18;
    app.setActivityScrollPosition({ currentTarget: viewport });
    assert.equal(viewport.scrollTop, 18);
    assert.equal(styleValues.get('--activity-scroll-shift'), '36px');
    assert.equal(controlStyleValues.get('--activity-fade-opacity'), String(1 - 18 / 64));
    assert.equal(controlStyleValues.get('--activity-fade-shift'), '-18px');

    app.lastActivity = {
        ...app.activity,
        state: 'complete',
        steps: app.activity.steps.map(step => ({ ...step, state: 'complete' })),
    };
    app.activity = null;
    prevented = false;
    viewport.scrollTop = 18;
    app.reverseActivityScroll({
        currentTarget: viewport,
        deltaY: -12,
        deltaMode: 0,
        preventDefault() {
            prevented = true;
        },
    });
    assert.equal(prevented, false);
    assert.equal(viewport.scrollTop, 18);
});

test('community notes escape headings, text, labels, and external links', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    const note = app.renderCommunityNote({
        birdwatch_pivot: {
            shorttitle: '<img onerror=x>',
            subtitle: {
                text: 'Read source',
                entities: [
                    {
                        fromIndex: 5,
                        toIndex: 11,
                        ref: { urlType: 'ExternalUrl', url: '" onmouseover="x' },
                    },
                ],
            },
        },
    });
    assert.match(note, /&lt;img onerror=x&gt;/);
    assert.match(note, /href="&quot; onmouseover=&quot;x"/);
    assert.doesNotMatch(note, /<img onerror/);
});

test('automated tagging has a dedicated conditional Settings page', () => {
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'index.html'), 'utf8');
    const js = fs.readFileSync(path.join(JS_DIR, 'app.js'), 'utf8');
    const css = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'static', 'css', 'styles.css'), 'utf8');
    assert.match(html, /x-show="automatedTaggingInstalled"/);
    assert.match(html, /settingsTab = 'automated-tagging'/);
    assert.match(html, /class="settings-sidebar flex-1 overflow-y-auto hidden md:block"/);
    assert.doesNotMatch(html, /settings-sidebar[\s\S]*?class="w-full text-left px-6 py-3 text-\[15px\] font-medium text-\[var\(--text-primary\)\] transition"/);
    assert.match(html, /Generate tags for archived tweets automatically via a Gemini LLM\. Manual tags remain available when this is off\./);
    assert.doesNotMatch(html, /Turn this on to run automated tagging after sync\./);
    assert.match(html, /md:hidden[\s\S]*x-model="settingsTab"/);
    assert.match(html, /Tagging Context/);
    assert.match(html, /automatedTaggingContextItems/);
    assert.match(html, /handleAutomatedTaggingContextKeydown\(\$event\)/);
    assert.match(html, /Add a subject/);
    assert.match(html, /No autocomplete is used here/);
    assert.doesNotMatch(html, /textarea[^>]*x-model="automatedTaggingContext"/);
    const automatedTaggingStart = html.indexOf('<!-- Optional direct-Gemini automated tagging -->');
    const automatedTaggingEnd = html.indexOf('<!-- Config Section -->');
    const automatedTaggingHtml = html.slice(automatedTaggingStart, automatedTaggingEnd);
    assert.match(automatedTaggingHtml, /<select[^>]*x-model="automatedTaggingSpendPeriod"/);
    assert.doesNotMatch(automatedTaggingHtml, /<section/);
    assert.doesNotMatch(automatedTaggingHtml, /focus-within:ring/);
    assert.match(automatedTaggingHtml, /M9\.64 18\.952l-5\.55-4\.861 1\.317-1\.504 3\.951 3\.459 8\.459-10\.948L19\.4 6\.32 9\.64 18\.952z/);
    assert.match(automatedTaggingHtml, /<fieldset[^>]*:disabled="!automatedTagging\.enabled"[^>]*opacity-50/);
    assert.match(automatedTaggingHtml, /Enable automated tagging/);
    assert.match(automatedTaggingHtml, /Enable automated tagging<\/h3><p[^>]*>Generate tags for archived tweets automatically via a Gemini LLM\. Manual tags remain available when this is off\./);
    assert.match(automatedTaggingHtml, /Please read the <a href="https:\/\/github\.com\/gezerwezer\/tweetnook\/blob\/main\/docs\/automated-tagging\.md" target="_blank" rel="noopener noreferrer"[^>]*>automated tagging documentation<\/a> first\./);
    assert.ok(automatedTaggingHtml.indexOf('automated tagging documentation') < automatedTaggingHtml.indexOf('id="save-automated-tagging-btn"'));
    assert.doesNotMatch(automatedTaggingHtml, /<h3[^>]*>Automated Tagging<\/h3>/);
    assert.match(automatedTaggingHtml, /<h3[^>]*>Model<\/h3>/);
    assert.match(automatedTaggingHtml, /<select[^>]*x-model="automatedTagging\.model"/);
    assert.match(automatedTaggingHtml, /class="flex items-center justify-between gap-3"/);
    assert.match(automatedTaggingHtml, /class="w-auto min-w-\[9rem\] shrink-0 whitespace-nowrap/);
    assert.match(automatedTaggingHtml, /<p class="mt-2 mb-4 text-sm text-\[var\(--text-secondary\)\]">Model refresh and test runs require internet access/);
    assert.match(automatedTaggingHtml, /<select[^>]*class="block w-full min-w-0/);
    assert.match(automatedTaggingHtml, /Choose a Gemini model for text and media tagging/);
    assert.match(automatedTaggingHtml, /https:\/\/ai\.google\.dev\/gemini-api\/docs\/pricing/);
    assert.doesNotMatch(automatedTaggingHtml, /input_token_limit/);
    assert.doesNotMatch(automatedTaggingHtml, /output_token_limit/);
    assert.match(automatedTaggingHtml, /<h3[^>]*>Thinking<\/h3>/);
    assert.match(automatedTaggingHtml, /<h3[^>]*>Spend limit<\/h3>/);
    assert.match(automatedTaggingHtml, /<h3[^>]*>Google Search<\/h3>/);
    assert.match(automatedTaggingHtml, /<label class="[^"]*\brelative\b[^"]*">\s*<input type="checkbox" x-model="automatedTagging\.enabled"/);
    assert.match(automatedTaggingHtml, /<span class="[^"]*\brelative\b[^"]*">\s*<input type="checkbox" x-model="automatedTagging\.google_search"/);
    assert.match(automatedTaggingHtml, /role="group" aria-label="Thinking level"/);
    assert.match(automatedTaggingHtml, /automatedTagging\.thinking_level = level\.key/);
    assert.match(automatedTaggingHtml, /role="group" aria-label="Processing tier"/);
    assert.match(automatedTaggingHtml, /automatedTagging\.processing_tier = 'standard'/);
    assert.match(automatedTaggingHtml, /automatedTagging\.processing_tier = 'flex'/);
    assert.match(automatedTaggingHtml, /https:\/\/ai\.google\.dev\/gemini-api\/docs\/flex-inference#how-it-works/);
    assert.doesNotMatch(automatedTaggingHtml, /50% less/);
    assert.match(automatedTaggingHtml, /<option[^>]*:value="model\.id"[^>]*x-text="model\.name"/);
    assert.match(automatedTaggingHtml, /loadAutomatedTaggingModels\(true\)/);
    assert.match(automatedTaggingHtml, /role="group" aria-label="API usage"/);
    assert.match(automatedTaggingHtml, /Free batches multiple tweets without Google Search/);
    assert.match(automatedTaggingHtml, /Learn more/);
    assert.match(automatedTaggingHtml, /x-model="automatedTaggingSpendAmount"/);
    assert.match(automatedTaggingHtml, /x-model="automatedTaggingSpendPeriod"/);
    assert.match(automatedTaggingHtml, /<div class="flex w-full max-w-md items-center gap-3" role="group" aria-label="Spend limit">/);
    assert.match(automatedTaggingHtml, /<label class="relative flex h-12 min-w-0 flex-1 items-center rounded-xl border/);
    assert.match(automatedTaggingHtml, /<label class="relative h-12 w-28 shrink-0">/);
    assert.match(automatedTaggingHtml, /x-model="automatedTaggingSpendPeriod"[^>]*class="[^"]*appearance-none[^"]*pr-9/);
    assert.match(automatedTaggingHtml, /View spend history/);
    assert.doesNotMatch(automatedTaggingHtml, /Estimated spend today/);
    assert.doesNotMatch(automatedTaggingHtml, /Estimated lifetime spend/);
    assert.doesNotMatch(automatedTaggingHtml, /Per model:/);
    assert.doesNotMatch(automatedTaggingHtml, /Unlimited paid spend/);
    assert.doesNotMatch(html, /automatedTagging\.(?:text|media)_model/);
    assert.match(html, /x-model\.number="automatedTagging\.free_batch_size"/);
    assert.match(html, /x-model="automatedTaggingInstructionInput"/);
    assert.match(html, /addAutomatedTaggingInstruction\(\)/);
    assert.match(html, /automatedTaggingInstructionItems/);
    assert.match(html, /removeAutomatedTaggingInstruction\(item\)/);
    assert.doesNotMatch(html, /<summary[^>]*>Advanced<\/summary>/);
    assert.match(html, /id="automated-tagging-test"/);
    assert.match(html, /Archived tweet ID/);
    assert.match(html, /x-model="automatedTaggingTweetId"/);
    assert.match(html, /lookupAutomatedTaggingTweet()/);
    assert.doesNotMatch(html, /Search text, author, tweet ID, or status URL/);
    assert.doesNotMatch(html, /Preview only/);
    assert.match(html, /<span class="font-bold text-\[var\(--accent-color\)\]">Your settings, descriptions, and tags won't be changed\.<\/span>/);
    assert.match(html, /id="run-automated-tagging-test-btn"[^>]*class="w-full rounded-full/);
    assert.match(html, /cannot see searches made by another application/);
    assert.match(js, /\/api\/automated-tagging\/models/);
    assert.match(js, /\/api\/automated-tagging\/tweets\?q=/);
    assert.match(js, /lookupAutomatedTaggingTweet/);
    assert.match(js, /\/api\/automated-tagging\/test/);
    assert.match(js, /saveAutomatedTagging/);
    assert.match(js, /runAutomatedTaggingTest/);
    assert.match(js, /this\.addAutomatedTaggingInstruction\(\);/);
    assert.match(js, /this\.automatedTagging\.google_search = mode === 'paid'/);
    assert.match(js, /settingsBodyOverflow/);
    assert.match(js, /document\.body\.style\.overflow = 'hidden'/);
    assert.match(js, /automatedTaggingSpendPeriodDays/);
    assert.match(js, /automatedTaggingModelCache/);
    assert.match(js, /loadAutomatedTaggingModels\(forceRefresh = false\)/);
    assert.match(js, /setAutomatedTaggingInstructionItems/);
    assert.match(js, /values\.additional_instructions = this\.automatedTaggingInstructionItems\.join\('\\n'\) \|\| null/);
    assert.match(html, /@click="openAutomatedTaggingSpendHistory\(\)"/);
    assert.match(html, /aria-labelledby="automated-tagging-spend-title"/);
    assert.match(html, /Pricing overview/);
    assert.match(html, /\{key: 'lifetime', label: 'Lifetime'\}/);
    assert.doesNotMatch(html, /Automated Tagging · estimated Gemini pricing/);
    assert.match(html, /Cost by model/);
    assert.match(html, /Averages by tweet type/);
    assert.match(html, /Avg \/ tweet/);
    assert.match(html, /Media items/);
    assert.match(html, /How pricing is calculated/);
    assert.match(html, /automatedTaggingSpendChartData\(\)/);
    assert.match(html, /automatedTaggingSpendModels\(\)/);
    assert.match(html, /Gemini API pricing/);
    assert.match(js, /showAutomatedTaggingSpendModal/);
    assert.match(js, /automatedTaggingSpendHistory\(\)/);
    assert.match(css, /\.spend-history-chart/);
    assert.match(css, /background: var\(--accent-color\)/);
});

test('Settings hidden checkboxes stay anchored inside the modal', () => {
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'index.html'), 'utf8');
    const settingsHtml = html.slice(
        html.indexOf('<!-- Settings Modal -->'),
        html.indexOf('<!-- Setup Modal -->'),
    );
    for (const model of [
        'automatedTagging.enabled',
        'automatedTagging.google_search',
        'showAdvancedConfig',
        'scheduleForm.enabled',
        'scheduleForm.randomize_time',
    ]) {
        const inputAt = settingsHtml.indexOf(`x-model="${model}"`);
        assert.notEqual(inputAt, -1, `missing Settings checkbox for ${model}`);
        const beforeInput = settingsHtml.slice(Math.max(0, inputAt - 300), inputAt);
        const anchorAt = Math.max(beforeInput.lastIndexOf('<label'), beforeInput.lastIndexOf('<span'), beforeInput.lastIndexOf('<div'));
        const anchor = beforeInput.slice(anchorAt);
        assert.match(anchor, /class="[^"]*\brelative\b[^"]*"/, `${model} needs a positioned ancestor`);
    }
});

test('automated tagging spend periods normalize to the stored daily limit', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.automatedTagging = { daily_spend_limit_usd: 2 };

    app.syncAutomatedTaggingSpendState();
    assert.equal(app.automatedTaggingSpendAmount, 2);
    app.setAutomatedTaggingSpendPeriod('week');
    assert.equal(app.automatedTaggingSpendAmount, 14);

    app.setAutomatedTaggingSpendAmount('21');
    assert.equal(app.automatedTaggingSpendAmount, 21);
    assert.equal(app.automatedTagging.daily_spend_limit_usd, 3);
    app.setAutomatedTaggingSpendPeriod('month');
    assert.equal(app.automatedTaggingSpendAmount, 90);
});

test('automated tagging spend history summarizes ranges, chart groups, and models', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.automatedTaggingAccounting = {
        spend_history: [
            { date: '2026-08-14', estimated_cost_usd: '0.001', requests: 1 },
            { date: '2026-08-15', estimated_cost_usd: '0.029', requests: 2 },
        ],
        lifetime_spend_history: [
            { date: '2026-07-01', interval: 'month', estimated_cost_usd: '0.20', requests: 5 },
            { date: '2026-08-01', interval: 'month', estimated_cost_usd: '0.30', requests: 6 },
        ],
        models: {
            'gemini-flash': { estimated_cost_usd: '0.01', requests: 4 },
            'gemini-pro': { estimated_cost_usd: '0.02', requests: 1 },
        },
        tweet_types: {
            text_only: {
                estimated_cost_usd: '0.01', requests: 2, priced_requests: 2,
                tweets: 4, priced_tweets: 4, media_items: 0,
            },
            video: {
                estimated_cost_usd: '0.02', requests: 1, priced_requests: 1,
                tweets: 1, priced_tweets: 1, media_items: 2, video_items: 2,
            },
        },
    };
    app.automatedTaggingSpendRange = '7d';

    assert.equal(app.automatedTaggingSpendRangeDays(), 7);
    assert.equal(app.automatedTaggingSpendRangeTotal(), 0.03);
    assert.equal(app.automatedTaggingSpendRangeRequests(), 3);
    assert.equal(app.automatedTaggingSpendChartData().length, 2);
    assert.equal(app.automatedTaggingSpendBarHeight(app.automatedTaggingSpendChartData()[1]), '100%');
    assert.equal(app.automatedTaggingSpendModels()[0].model, 'gemini-pro');
    assert.equal(app.automatedTaggingSpendTweetTypes()[0].label, 'Text only');
    assert.equal(app.automatedTaggingSpendTweetTypes()[0].average_tweet_usd, 0.0025);
    assert.equal(app.automatedTaggingSpendTweetTypes()[1].average_request_usd, 0.02);
    assert.equal(app.automatedTaggingTweetTypeMediaLabel(app.automatedTaggingSpendTweetTypes()[1]), '2 videos');
    assert.equal(app.formatAutomatedTaggingUsd(0.001), '$0.0010');
    assert.equal(app.formatAutomatedTaggingSpendDate('2026-08-15'), 'Aug 15');
    assert.match(app.automatedTaggingSpendBarLabel(app.automatedTaggingSpendChartData()[0]), /\$0\.0010 across 1 requests/);

    app.automatedTaggingSpendRange = 'lifetime';
    assert.equal(app.automatedTaggingSpendRangeDays(), null);
    assert.equal(app.automatedTaggingSpendRangeTotal(), 0.5);
    assert.equal(app.automatedTaggingSpendRangeRequests(), 11);
    assert.equal(app.automatedTaggingSpendChartData()[0].interval, 'month');
    assert.equal(app.formatAutomatedTaggingSpendDate('2026-08-01', true, true), 'Aug 2026');
    assert.match(app.automatedTaggingSpendBarLabel(app.automatedTaggingSpendChartData()[0]), /Jul 2026/);

    let closeFocused = false;
    let buttonFocused = false;
    app.$refs = {
        automatedTaggingSpendClose: { focus() { closeFocused = true; } },
        automatedTaggingSpendButton: { focus() { buttonFocused = true; } },
    };
    app.openAutomatedTaggingSpendHistory();
    assert.equal(app.showAutomatedTaggingSpendModal, true);
    assert.equal(app.automatedTaggingSpendRange, '30d');
    assert.equal(closeFocused, true);
    app.closeAutomatedTaggingSpendHistory();
    assert.equal(app.showAutomatedTaggingSpendModal, false);
    assert.equal(buttonFocused, true);
});

test('automated tagging model refresh caches until forced', async () => {
    const context = browserContext();
    let requests = 0;
    context.fetch = async () => {
        requests += 1;
        return {
            ok: true,
            async json() {
                return { models: [{ id: 'gemini-3.6-flash', name: 'Gemini 3.6 Flash' }] };
            },
        };
    };
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.automatedTagging = {
        api_key: '********',
        api_mode: 'free',
        processing_tier: 'standard',
        model: 'gemini-3.6-flash',
    };

    await app.loadAutomatedTaggingModels();
    await app.loadAutomatedTaggingModels();
    assert.equal(requests, 1);
    await app.loadAutomatedTaggingModels(true);
    assert.equal(requests, 2);
});

test('automated tagging context commits on Enter, comma, and Tab', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.automatedTaggingContextInput = 'Deadlock';
    let prevented = false;
    app.handleAutomatedTaggingContextKeydown({
        key: 'Enter',
        preventDefault() {
            prevented = true;
        },
    });
    assert.equal(prevented, true);
    assert.deepEqual(Array.from(app.automatedTaggingContextItems), ['Deadlock']);
    assert.equal(app.automatedTaggingContextInput, '');

    app.automatedTaggingContextInput = 'Guilty Gear';
    prevented = false;
    app.handleAutomatedTaggingContextKeydown({
        key: ',',
        preventDefault() {
            prevented = true;
        },
    });
    assert.equal(prevented, true);
    assert.deepEqual(Array.from(app.automatedTaggingContextItems), ['Deadlock', 'Guilty Gear']);

    app.automatedTaggingContextInput = 'Rust';
    let tabPrevented = false;
    app.handleAutomatedTaggingContextKeydown({
        key: 'Tab',
        preventDefault() {
            tabPrevented = true;
        },
    });
    assert.equal(tabPrevented, false);
    assert.deepEqual(Array.from(app.automatedTaggingContextItems), ['Deadlock', 'Guilty Gear', 'Rust']);
});

test('saving automated tagging includes a context hint still in the input', async () => {
    const context = browserContext();
    const requests = [];
    context.fetch = async (url, options = {}) => {
        requests.push([url, JSON.parse(options.body)]);
        return {
            ok: true,
            async json() {
                return {
                    values: {
                        enabled: false,
                        api_key: '********',
                        tagging_context: ['Deadlock'],
                    },
                };
            },
        };
    };
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.automatedTagging = { enabled: false, api_key: '********', tagging_context: [] };
    app.automatedTaggingContextInput = 'Deadlock';

    await app.saveAutomatedTagging();

    assert.equal(requests.length, 1);
    assert.deepEqual(requests[0][1].values.tagging_context, ['Deadlock']);
    assert.deepEqual(Array.from(app.automatedTaggingContextItems), ['Deadlock']);
});

test('offline fonts use local families, permission-based discovery, and no stylesheets', async () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({ tweetApp })');
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

test('card thumbnails only render matching archived images or posters', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({ tweetApp })');
    const component = immediateComponent(tweetApp());
    const remote = 'https://example.com/card.jpg';
    assert.equal(component.localCardImage({}, remote), null);
    assert.equal(component.localCardImage({ media: [{ url: remote, download: {
        local_path: 'media/100/card image.jpg',
    } }] }, remote), '/media/100/card%20image.jpg');
    assert.equal(component.localCardImage({ media: [{ thumbnail_url: remote, download: {
        thumbnail_local_path: 'media/100/poster.jpg',
    } }] }, remote), '/media/100/poster.jpg');
    for (const local_path of ['https://example.com/a.jpg', '//example.com/a.jpg', 'media/../a.jpg']) {
        assert.equal(component.localCardImage({ media: [{ url: remote, download: { local_path } }] }, remote), null);
    }
});

test('custom font dropdown supports keyboard focus and selection', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({ tweetApp })');
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
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook/web/index.html'), 'utf8');
    assert.match(html, /Load local fonts/);
    assert.match(html, /:style="\{ fontFamily: f.family \}"/);
    assert.doesNotMatch(html, /custom-font-name|Installed font family name|Fonts come from this device/);
});

test('loading tagging settings never discovers provider models implicitly', async () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({ tweetApp })');
    const component = immediateComponent(tweetApp());
    let requests = 0;
    context.fetch = async url => {
        assert.equal(url, '/api/automated-tagging');
        requests++;
        return { ok: true, json: async () => ({ installed: true, values: {
            ...component.automatedTagging, api_key: 'test-key',
        }, accounting: {} }) };
    };
    let modelRequests = 0;
    component.loadAutomatedTaggingModels = async () => { modelRequests++; };
    await component.fetchAutomatedTagging();
    assert.equal(requests, 1);
    assert.equal(modelRequests, 0);
});

test('packaged page loads only same-origin runtime assets', () => {
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook/web/index.html'), 'utf8');
    const head = html.split('</head>')[0];
    assert.doesNotMatch(head, /(?:src|href)=["']https?:/);
    for (const match of head.matchAll(/(?:src|href)=["'](\/static\/[^"']+)/g)) {
        assert.ok(fs.existsSync(path.join(ROOT, 'tweetnook/web', match[1])), match[1]);
    }
    assert.doesNotMatch(fs.readFileSync(path.join(JS_DIR, 'themes.js'), 'utf8'), /fonts\.googleapis/);
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
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    const calls = [];
    for (const method of ['fetchTweets', 'fetchStats', 'fetchGlobalTags', 'fetchArchiveEnrichmentStatus', 'fetchAutomatedTagging']) app[method] = async () => calls.push(method);
    let appliedDeepLinks = 0;
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
    assert.equal(calls.length, 5);
    assert.equal(appliedDeepLinks, 1);
    await app.fetchSetup();
    assert.equal(calls.length, 5);
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

test('notification badge counts every unread item and actions use Settings destinations', async () => {
    const context = browserContext();
    const requests = [];
    context.fetch = async (url, options) => { requests.push([url, options.method]); return {ok: true}; };
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.notices = [
        {id: 'a', severity: 'error', dismissed: false, action: 'setup'},
        {id: 'b', severity: 'warning', dismissed: false},
        {id: 'c', severity: 'info', dismissed: false},
        {id: 'd', severity: 'error', dismissed: true},
    ];
    assert.equal(app.noticeCount, 3);
    await app.dismissNotice('a');
    assert.equal(app.noticeCount, 2);
    assert.deepEqual(requests, [['/api/notices/a/dismiss', 'POST']]);
    app.openNotice(app.notices[0]);
    assert.equal(app.showSetupModal, true);
    assert.equal(app.showSettingsModal, false);
    app.fetchScheduleSettings = () => { requests.push(['schedule-refresh', 'GET']); };
    app.openNotice({action: 'schedule'});
    assert.equal(app.settingsTab, 'schedule');
    app.openNotice({action: 'activity'});
    assert.equal(app.activityDrawerOpen, true);
});

test('notifications live in Settings and the old header popup is removed', () => {
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook/web/index.html'), 'utf8');
    const js = fs.readFileSync(path.join(JS_DIR, 'app.js'), 'utf8');
    const css = fs.readFileSync(path.join(ROOT, 'tweetnook/web/static/css/styles.css'), 'utf8');
    assert.match(html, /<option value="notifications" x-text="noticeCount \? `Notifications \(\$\{noticeCount\}\)` : 'Notifications'">/);
    assert.match(html, /settingsTab = 'notifications'; fetchNotices\(\)/);
    assert.match(html, /settingsTab === 'notifications' \? 'Notifications'/);
    assert.match(html, /Settings, \$\{noticeCount\} unread notifications/);
    assert.match(html, /class="settings-notification-count" x-text="noticeCount"/);
    assert.match(html, /class="notification-count-badge" x-text="noticeCount > 99 \? '99' : noticeCount"/);
    assert.match(css, /\.notification-count-badge,[\s\S]*?\.settings-notification-count\s*\{[^}]*color: var\(--accent-text\);[^}]*background: var\(--accent-color\);/s);
    assert.match(css, /\.notification-count-badge\s*\{[^}]*position: absolute;[^}]*top: -5px;[^}]*left: -5px;[^}]*width: 22px;[^}]*height: 22px;/s);
    assert.match(css, /\.settings-notification-count\s*\{[^}]*width: 20px;[^}]*height: 20px;/s);
    assert.doesNotMatch(html, /notice-control|notice-inbox/);
    assert.doesNotMatch(js, /noticesOpen/);
    assert.doesNotMatch(html, /class="setup-btn sm:hidden"[^>]*aria-label="Settings"/);
});

test('Settings and Setup stay above an expanded activity drawer', () => {
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook/web/index.html'), 'utf8');
    const css = fs.readFileSync(path.join(ROOT, 'tweetnook/web/static/css/styles.css'), 'utf8');
    const activityStart = html.indexOf('<!-- Twitter/X-style activity drawer -->');
    const activityMarkup = html.slice(activityStart, html.indexOf('</section>', activityStart));

    assert.equal((html.match(/settings-modal-backdrop fixed inset-0 z-\[100\]/g) || []).length, 2);
    assert.match(css, /\.activity-drawer\s*\{[\s\S]*?z-index: 70;/);
    assert.doesNotMatch(activityMarkup, /activity-over-settings/);
    assert.doesNotMatch(css, /\.activity-drawer\.activity-over-settings/);
});

test('activity drawer animation isolates layout and transitions only geometry', () => {
    const css = fs.readFileSync(path.join(ROOT, 'tweetnook/web/static/css/styles.css'), 'utf8');
    const drawerRule = css.match(/\.activity-drawer\s*\{([^}]*)\}/)?.[1] || '';
    const readyRule = css.match(/\.activity-drawer\.activity-drawer-ready\s*\{([^}]*)\}/)?.[1] || '';

    assert.match(drawerRule, /contain: layout paint style;/);
    assert.match(readyRule, /will-change: width, height, right, bottom, border-radius;/);
    assert.doesNotMatch(readyRule, /transition:\s*all\b/);
    for (const property of ['width', 'height', 'right', 'bottom', 'border-radius']) {
        assert.match(readyRule, new RegExp(`(?:transition:|,)\\s*${property} 0\\.35s`));
    }
});

test('floating analytics, settings, and sync controls are desktop-only', () => {
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook/web/index.html'), 'utf8');
    const statsStart = html.indexOf('<!-- Stats Button -->');
    const settingsStart = html.indexOf('<!-- Settings Button -->');
    const activityStart = html.indexOf('<!-- Twitter/X-style activity drawer -->');

    assert.match(html.slice(statsStart, settingsStart), /class="[^\"]*hidden sm:flex[^\"]*"/);
    assert.match(html.slice(settingsStart, activityStart), /class="[^\"]*hidden sm:flex[^\"]*"/);
    assert.match(html.slice(activityStart, html.indexOf('</section>', activityStart)), /class="activity-drawer hidden sm:block"/);
});

test('Settings tabs use the requested order and Appearance is the default', () => {
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook/web/index.html'), 'utf8');
    const js = fs.readFileSync(path.join(JS_DIR, 'app.js'), 'utf8');
    const mobileStart = html.indexOf('<select x-show="!setupForced"');
    const mobileEnd = html.indexOf('</select>', mobileStart);
    const mobileTabs = html.slice(mobileStart, mobileEnd);
    const sidebarStart = html.indexOf('<div x-show="!setupForced" class="settings-sidebar');
    const sidebarEnd = html.indexOf('</div>\n                </div>', sidebarStart);
    const sidebarTabs = html.slice(sidebarStart, sidebarEnd);
    const mobileMarkers = ['value="appearance"', 'value="schedule"', 'value="tags"', 'value="automated-tagging"', 'value="notifications"', 'value="config"', 'value="logs"', 'value="setup"'];
    const sidebarMarkers = [
        "settingsTab = 'appearance'", "settingsTab = 'schedule'", "settingsTab = 'tags'",
        "settingsTab = 'automated-tagging'", "settingsTab = 'notifications'", "settingsTab = 'config'",
        "settingsTab = 'logs'", "settingsTab = 'setup'",
    ];
    const positions = (source, markers) => markers.map(marker => source.indexOf(marker));
    assert.ok(positions(mobileTabs, mobileMarkers).every(index => index >= 0));
    assert.ok(positions(sidebarTabs, sidebarMarkers).every(index => index >= 0));
    assert.deepEqual(positions(mobileTabs, mobileMarkers), [...positions(mobileTabs, mobileMarkers)].sort((a, b) => a - b));
    assert.deepEqual(positions(sidebarTabs, sidebarMarkers), [...positions(sidebarTabs, sidebarMarkers)].sort((a, b) => a - b));
    assert.match(js, /settingsTab: 'appearance'/);
    assert.match(js, /rerunSetup\(\)[\s\S]*?this\.settingsTab = 'setup'/);
});

test('setup password finalization reloads only after successful completion', async () => {
    const context = browserContext();
    let reloaded = false;
    context.window.location = {reload() { reloaded = true; }};
    context.fetch = async (url, options) => {
        assert.equal(url, '/api/setup/complete');
        assert.deepEqual(JSON.parse(options.body), {password: 'test-secret', confirm_password: 'test-secret', skip_auth: true});
        return {ok: true, async json() {return {completed: true, reload: true};}};
    };
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.setupForced = true;
    app.setupSkipAuth = true;
    app.setupPassword = app.setupPasswordConfirm = 'test-secret';
    app.setupData = {web: {password_configured: false}};
    await app.finishSetup();
    assert.equal(reloaded, true);
    assert.equal(app.setupForced, false);
    assert.equal(app.setupPassword, '');
});

async function main() {
    let failures = 0;
    for (const { name, fn } of tests) {
        try {
            await fn();
            process.stdout.write(`ok - ${name}\n`);
        } catch (error) {
            failures += 1;
            process.stderr.write(`not ok - ${name}\n${error.stack}\n`);
        }
    }
    process.stdout.write(`\n${tests.length - failures}/${tests.length} browser asset tests passed\n`);
    if (failures) process.exitCode = 1;
}

main();
