'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { appScripts, appScriptsWith, pageHtml } = require('./page_scripts.cjs');
const {
    FakeElement,
    ROOT,
    browserContext,
    dispatchFeed,
    immediateComponent,
    loadScripts,
    nookMergeScope,
    test,
    run,
} = require('./web_harness.cjs');

test('activity drawer loads any pipeline and starts production jobs', async () => {
    const context = browserContext();
    const calls = [];
    let active = true;
    let stopped = false;
    context.fetch = async (url, options = {}) => {
        calls.push([url, options.method || 'GET']);
        if (url === '/api/activity/import') {
            active = true;
            stopped = false;
            return { ok: true, json: async () => ({ started: true, kind: 'import', run_id: 'run' }) };
        }
        if (url === '/api/activity/stop') {
            active = false;
            stopped = true;
            return { ok: true, json: async () => ({ stopping: true }) };
        }
        return {
            ok: true,
            json: async () => active ? {
                active: true,
                snapshot: {
                    run_id: 'active-run',
                    title: 'tweetnook import enrich',
                    started_at: Date.now() / 1000,
                    active_step: 'enrich',
                    steps: [{ key: 'enrich', state: 'active', completed: 5, total: 10 }],
                    issues: [],
                },
                schedule: { configured: false, relative: 'Not configured', date: 'Use cron or a service timer' },
            } : {
                active: false,
                snapshot: null,
                last_snapshot: {
                    run_id: 'active-run',
                    title: 'tweetnook import enrich',
                    completed_at: Date.now() / 1000,
                    stopped,
                    success: !stopped,
                    steps: [{
                        key: 'enrich',
                        state: stopped ? 'failed' : 'complete',
                        completed: stopped ? 5 : 10,
                        total: 10,
                    }],
                    issues: [],
                },
                schedule: { configured: false, relative: 'Not configured', date: 'Use cron or a service timer' },
            },
        };
    };
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());

    app.activityPageKey = 'old-run-stage';
    await app.fetchActivityStatus();
    assert.equal(app.activity.title, 'tweetnook import enrich');
    assert.equal(app.activityPercent(app.activity.steps[0]), 50);
    assert.equal(app.activityPageKey, null);

    active = false;
    await app.fetchActivityStatus();
    assert.equal(app.activity, null);
    assert.equal(app.lastActivity.title, 'tweetnook import enrich');
    assert.equal(app.activitySchedule.relative, 'Not configured');
    app.activityPageKey = '__summary__';
    await app.startActivity('import');
    assert.ok(calls.some(([url, method]) => url === '/api/activity/import' && method === 'POST'));
    assert.equal(app.activityStartPending, false);
    assert.equal(app.activityStartingKind, null);
    assert.equal(app.activityPageKey, null);
    assert.equal(app.activity.title, 'tweetnook import enrich');
    await app.stopActivity();
    assert.ok(calls.some(([url, method]) => url === '/api/activity/stop' && method === 'POST'));
    assert.equal(app.activity, null);
    assert.equal(app.activityPageKey, 'enrich');
    assert.equal(app.activitySelectedPageKey(), 'enrich');
    assert.equal(app.activitySegmentState(app.activitySelectedPage()), 'cancelled');
    assert.equal(app.activitySegmentPercent(app.activitySelectedPage()), 50);
});

test('what to sync is the server sync.collections setting, shared by the sync panel and Schedule', async () => {
    const context = browserContext();
    const requests = [];
    let saved = ['likes'];
    let failNext = false;
    context.fetch = async (url, options = {}) => {
        requests.push([url, options.method || 'GET', options.body ? JSON.parse(options.body) : null]);
        if (options.method === 'POST') {
            if (failNext) { failNext = false; return { ok: false, async json() { return {}; } }; }
            saved = JSON.parse(options.body).changes['sync.collections'];
        }
        return { ok: true, async json() { return { values: { sync: { collections: saved } }, explicit: ['sync.collections'] }; } };
    };
    const { nookLoadSyncCollections, nookToggleSyncCollection, nookSyncCollectionsLabel } = loadScripts(
        context, ['header.js'], '({nookLoadSyncCollections, nookToggleSyncCollection, nookSyncCollectionsLabel})',
    );
    const app = { nookSyncCollections: ['bookmarks', 'likes'], configData: { sync: { collections: ['likes'] } }, configOriginal: { sync: { collections: ['likes'] } } };

    await nookLoadSyncCollections(app);
    assert.deepEqual([...app.nookSyncCollections], ['likes']);
    assert.equal(nookSyncCollectionsLabel(app), 'Likes');

    // Choices save right away, in the canonical order, and keep a loaded Configuration page in step.
    await nookToggleSyncCollection(app, 'tweets');
    await nookToggleSyncCollection(app, 'bookmarks');
    assert.deepEqual(requests.at(-1), ['/api/config', 'POST', { changes: { 'sync.collections': ['bookmarks', 'likes', 'tweets'] } }]);
    assert.deepEqual([...app.nookSyncCollections], ['bookmarks', 'likes', 'tweets']);
    assert.deepEqual([...app.configData.sync.collections], ['bookmarks', 'likes', 'tweets']);
    assert.deepEqual([...app.configOriginal.sync.collections], ['bookmarks', 'likes', 'tweets']);
    assert.equal(nookSyncCollectionsLabel(app), 'Bookmarks, likes and your tweets');

    // A failed save puts the previous choice back.
    failNext = true;
    await nookToggleSyncCollection(app, 'likes');
    assert.deepEqual([...app.nookSyncCollections], ['bookmarks', 'likes', 'tweets']);

    // The last collection can't be turned off.
    app.nookSyncCollections = ['likes'];
    const before = requests.length;
    await nookToggleSyncCollection(app, 'likes');
    assert.equal(requests.length, before);
    assert.deepEqual([...app.nookSyncCollections], ['likes']);
});

function jobsSheetFixture() {
    const context = browserContext();
    context.window.innerWidth = 390;
    context.window.innerHeight = 844;
    const timers = new Map();
    let timerId = 0;
    context.setTimeout = callback => { timers.set(++timerId, callback); return timerId; };
    context.clearTimeout = id => timers.delete(id);
    context.document.documentElement.classList = new FakeElement('html').classList;
    const panel = new FakeElement('section').cloneNode();
    panel.offsetHeight = 600;
    panel.scrollTop = 0;
    const body = new FakeElement('button');
    const handle = new FakeElement('div', [['class', 'cs-grab']]);
    const input = new FakeElement('input');
    panel.append([body, handle, input]);
    const app = {activityDrawerOpen: true};
    const sheet = loadScripts(context, ['header.js'], 'nookJobsSheet')(panel, app);
    const touch = (type, x, y, at, target = body, count = 1) => {
        const event = {type, target, timeStamp: at, cancelable: true, defaultPrevented: false,
            touches: Array.from({length: count}, () => ({clientX: x, clientY: y})),
            preventDefault() { this.defaultPrevented = true; }};
        if (type === 'touchmove') context.__state.events.get('document:touchmove')(event);
        else panel.listeners.find(listener => listener.type === type).listener(event);
        return event;
    };
    return {context, panel, body, handle, input, app, sheet, touch, timers};
}

test('phone jobs sheet locks the feed, follows a drag across content and dismisses on distance or a flick', () => {
    const fixture = jobsSheetFixture();
    const {context, panel, app, sheet, touch, timers} = fixture;
    assert.ok(context.document.documentElement.classList.contains('nook-jobs-open'));
    assert.equal(touch('touchmove', 10, 200, 0, new FakeElement('div')).defaultPrevented, true);

    touch('touchstart', 100, 100, 0);
    assert.equal(touch('touchmove', 102, 150, 100).defaultPrevented, true);
    assert.equal(panel.style.getPropertyValue('--cs-sheet-y'), '50px');
    touch('touchend', 102, 150, 250);
    assert.equal(app.activityDrawerOpen, true); // Short, slow drag returns to rest.
    assert.equal(panel.style.getPropertyValue('--cs-sheet-y'), '');
    for (const callback of [...timers.values()]) callback();
    assert.equal(panel.style.animation, 'none'); // Settling must not replay the opening slide.

    touch('touchstart', 100, 100, 300);
    touch('touchmove', 102, 130, 340);
    touch('touchend', 102, 130, 350);
    assert.equal(app.activityDrawerOpen, false); // A short deliberate flick closes it.
    sheet.sync();
    assert.equal(context.document.documentElement.classList.contains('nook-jobs-open'), false);
    assert.equal(panel.style.getPropertyValue('--cs-sheet-y'), '30px'); // Leave animation starts at the finger.

    app.activityDrawerOpen = true;
    sheet.sync();
    assert.equal(timers.size, 0); // Reopening cancels stale close cleanup.
    assert.equal(panel.style.getPropertyValue('--cs-sheet-y'), '');
    touch('touchstart', 100, 100, 400);
    touch('touchmove', 100, 240, 1000);
    touch('touchend', 100, 240, 1200);
    assert.equal(app.activityDrawerOpen, false); // Slow long drag also closes.
    sheet.destroy();
    assert.equal(context.__state.events.has('document:touchmove'), false);
});

test('phone jobs gestures preserve content scrolling, tabs and editors and recover from cancellation and resize', () => {
    const {context, panel, handle, input, app, sheet, touch} = jobsSheetFixture();
    panel.scrollTop = 80;
    touch('touchstart', 100, 100, 0);
    assert.equal(touch('touchmove', 100, 200, 100).defaultPrevented, false);
    assert.equal(panel.style.getPropertyValue('--cs-sheet-y'), '');
    panel.scrollTop = 0;
    touch('touchstart', 100, 100, 200);
    assert.equal(touch('touchmove', 100, 50, 300).defaultPrevented, false); // Scroll down into the sheet.
    touch('touchstart', 100, 100, 400);
    assert.equal(touch('touchmove', 160, 104, 450).defaultPrevented, false); // Horizontal tabs.
    assert.equal(touch('touchmove', 170, 200, 500).defaultPrevented, false);
    touch('touchstart', 100, 100, 600, input);
    assert.equal(touch('touchmove', 100, 200, 700, input).defaultPrevented, false);

    panel.scrollTop = 80;
    touch('touchstart', 100, 100, 800, handle);
    assert.equal(touch('touchmove', 100, 250, 900, handle).defaultPrevented, true);
    touch('touchcancel', 100, 250, 910, handle);
    assert.equal(app.activityDrawerOpen, true); // OS interruptions never dismiss.
    assert.equal(panel.style.getPropertyValue('--cs-sheet-y'), '');
    const click = dispatchFeed(panel, 'click', handle, {stopImmediatePropagation() { this.propagationStopped = true; }});
    assert.equal(click.defaultPrevented, true); // A drag must not activate a job button.
    touch('touchstart', 100, 100, 1000);
    const freshClick = dispatchFeed(panel, 'click', handle);
    assert.equal(freshClick.defaultPrevented, false);
    panel.scrollTop = 0;
    touch('touchstart', 100, 100, 1010);
    touch('touchmove', 100, 250, 1020);
    touch('touchstart', 100, 250, 1030, handle, 2);
    assert.equal(panel.style.getPropertyValue('--cs-sheet-y'), '');
    assert.equal(app.activityDrawerOpen, true); // A second finger cancels instead of stranding a dragged sheet.

    context.window.innerWidth = 1280;
    context.__state.events.get('resize')();
    assert.equal(context.document.documentElement.classList.contains('nook-jobs-open'), false);
    touch('touchstart', 100, 100, 1100);
    assert.equal(touch('touchmove', 100, 300, 1200).defaultPrevented, false);
    context.window.innerWidth = 390;
    context.__state.events.get('resize')();
    assert.ok(context.document.documentElement.classList.contains('nook-jobs-open'));
    sheet.destroy();
    assert.equal(context.document.documentElement.classList.contains('nook-jobs-open'), false);
});

test('phone Jobs anchor follows the locked viewport and releases its scroll listener on teardown', () => {
    const {context, app, sheet} = jobsSheetFixture();
    const offset = () => context.__state.cssProperties.get('--nook-jobs-page-top');
    const bleed = () => context.__state.cssProperties.get('--nook-jobs-bottom-bleed');
    context.window.innerHeight = 714;
    context.window.outerHeight = 874;
    context.window.scrollY = 500;
    sheet.sync();
    assert.equal(offset(), '500px');
    assert.equal(bleed(), '160px');
    context.window.scrollY = 520;
    context.__state.events.get('scroll')();
    assert.equal(offset(), '520px'); // Browser adjustments keep the sheet at the visible page edge.
    app.activityDrawerOpen = false;
    sheet.sync();
    context.window.scrollY = 900;
    context.window.outerHeight = 714;
    context.__state.events.get('scroll')();
    assert.equal(offset(), '520px'); // Keep the closing sheet in place through its leave animation.
    assert.equal(bleed(), '160px');
    app.activityDrawerOpen = true;
    context.window.innerWidth = 1280;
    sheet.sync();
    assert.equal(offset(), '520px');
    context.window.innerWidth = 390;
    context.__state.events.get('resize')();
    assert.equal(offset(), '900px');
    assert.equal(bleed(), '0px');
    context.window.outerHeight = 650;
    sheet.sync();
    assert.equal(bleed(), '0px'); // Never shrink the sheet if the browser reports a smaller outer window.
    context.window.outerHeight = undefined;
    context.window.screen = {height: 874};
    sheet.sync();
    assert.equal(bleed(), '160px');
    sheet.destroy();
    assert.equal(offset(), undefined);
    assert.equal(bleed(), undefined);
    assert.equal(context.__state.events.has('scroll'), false);
});

test('closing jobs restores focus without moving the feed', () => {
    const context = browserContext(), options = [];
    context.document.querySelector = selector => selector === '.nook-activity-button' ? {focus: value => options.push(value)} : null;
    const close = loadScripts(context, ['header.js'], 'nookCloseActivity');
    const app = {activityDrawerOpen: true};
    close(app);
    assert.equal(app.activityDrawerOpen, false);
    assert.equal(options[0].preventScroll, true);
});

test('Jobs browser tint follows the shaded theme and restores on close, resize and teardown', () => {
    const {context, panel, sheet} = jobsSheetFixture();
    sheet.destroy();
    const app = loadScripts(context, appScripts(), 'tweetApp')();
    app.activityDrawerOpen = true;
    const mobileSheet = vm.runInContext('nookJobsSheet', context)(panel, app);
    app.applyTheme('classic-dark');
    assert.equal(context.__state.themeColor.content, '#242d34');
    assert.equal(context.document.documentElement.style.backgroundColor, '#242d34');
    assert.equal(context.document.body.style.backgroundColor, '#242d34');
    app.applyTheme('classic-light');
    assert.equal(context.__state.themeColor.content, '#bdc6cd');
    app.activityDrawerOpen = false;
    mobileSheet.sync();
    assert.equal(context.__state.themeColor.content, '#ffffff');
    app.activityDrawerOpen = true;
    mobileSheet.sync();
    assert.equal(context.__state.themeColor.content, '#bdc6cd');
    context.window.innerWidth = 641;
    mobileSheet.sync();
    assert.equal(context.__state.themeColor.content, '#ffffff');
    context.window.innerWidth = 390;
    mobileSheet.sync();
    mobileSheet.destroy();
    assert.equal(context.__state.themeColor.content, '#ffffff');
    const css = fs.readFileSync(path.join(ROOT, 'tweetnook/web/static/css/header.css'), 'utf8');
    const html = pageHtml();
    assert.match(html, /class="nook-sync-scrim"[^>]*><\/div>\s*<div class="nook-sync-edge" aria-hidden="true"><\/div>/);
    assert.match(html, /class="nook-sync-scrim" x-cloak x-show="activityDrawerOpen \|\| activityShadeProgress"/);
    assert.match(css, /\.nook-sync-scrim \{[^}]*opacity: 0;/);
    assert.doesNotMatch(html, /class="nook-sync-scrim"[^>]*x-transition/);
    const edgeRule = css.match(/\.nook-sync-edge \{([^}]*position: fixed;[^}]+)\}/)[1];
    const height = Number(edgeRule.match(/height: (\d+)px/)[1]);
    const padding = Number(edgeRule.match(/padding-bottom: (\d+)px/)[1]);
    assert.ok(height > 10, 'Safari ignores background colours on surfaces at or below 10px');
    assert.equal(height - padding, 1, 'the sampling surface must not cover header text');
    assert.match(edgeRule, /box-sizing: border-box;/);
    assert.match(edgeRule, /background-clip: content-box;/);
    assert.match(edgeRule, /background-color: var\(--bg-primary/);
    const anchorRule = css.match(/\.nook-sync-anchor \{([^}]*position: absolute;[^}]+)\}/)[1];
    assert.ok(Number(edgeRule.match(/z-index: (\d+)/)[1]) > Number(anchorRule.match(/z-index: (\d+)/)[1]),
        'the transparent sheet anchor must not intercept Safari top-edge sampling');
    assert.match(anchorRule, /overflow: hidden;/); // Clip motion at the end of the sheet's toolbar extension.
    assert.doesNotMatch(css, /\.nook-sync-scrim::before/);
});

test('Jobs shade and tint share one animation value through close reversal and theme changes', () => {
    const {context, panel, sheet} = jobsSheetFixture();
    sheet.destroy();
    const query = context.document.querySelector, scrim = {style: {}}, edge = {style: {}}, frames = new Map();
    let frameId = 0, clock = 0;
    context.document.querySelector = selector => selector === '.nook-sync-scrim' ? scrim : selector === '.nook-sync-edge' ? edge : query(selector);
    context.performance = {now: () => clock};
    context.requestAnimationFrame = callback => { frames.set(++frameId, callback); return frameId; };
    context.cancelAnimationFrame = id => frames.delete(id);
    const app = loadScripts(context, appScripts(), 'tweetApp')();
    app.applyTheme('classic-dark');
    app.activityDrawerOpen = true;
    const mobileSheet = vm.runInContext('nookJobsSheet', context)(panel, app);
    const sample = (now, expected) => {
        clock = now;
        const [id, callback] = frames.entries().next().value;
        frames.delete(id);
        callback(clock);
        assert.equal(app.activityShadeProgress, expected);
        assert.equal(Number(scrim.style.opacity), expected);
        assert.equal(edge.style.backgroundColor, context.__state.themeColor.content);
        assert.equal(context.document.body.style.backgroundColor, expected ? edge.style.backgroundColor : '');
        assert.equal(context.document.documentElement.style.backgroundColor, expected ? edge.style.backgroundColor : '');
        assert.equal(context.__state.cssProperties.has('--nook-jobs-shade-progress'), false);
        assert.equal(context.__state.cssProperties.has('--nook-jobs-edge-color'), false);
    };
    sample(0, 0);
    sample(55, .15625);
    assert.equal(context.__state.themeColor.content, '#060708');
    sample(110, .5);
    assert.equal(context.__state.themeColor.content, '#12161a');
    app.applyTheme('classic-light');
    assert.equal(context.__state.themeColor.content, '#dee2e6'); // Keep the current fade fraction.
    app.applyTheme('classic-dark');
    sample(220, 1);
    assert.equal(frames.size, 0);
    app.activityDrawerOpen = false;
    mobileSheet.sync();
    assert.equal(app.activityShadeProgress, 1); // No jump at the start of closing.
    sample(330, .5);
    assert.ok(context.document.documentElement.classList.contains('nook-jobs-shading'));
    app.activityDrawerOpen = true;
    mobileSheet.sync();
    assert.equal(frames.size, 1);
    sample(385, .75);
    sample(440, 1);
    app.activityDrawerOpen = false;
    mobileSheet.sync();
    sample(550, .5);
    sample(660, 0);
    assert.equal(frames.size, 0);
    assert.equal(context.__state.themeColor.content, '#000000');
    assert.equal(context.document.documentElement.classList.contains('nook-jobs-shading'), false);
    context.window.matchMedia = () => ({matches: true});
    app.activityDrawerOpen = true;
    mobileSheet.sync();
    assert.equal(app.activityShadeProgress, 1); // Reduced motion changes both surfaces immediately.
    assert.equal(frames.size, 0);
    context.window.matchMedia = () => ({matches: false});
    app.activityDrawerOpen = false;
    mobileSheet.sync();
    mobileSheet.destroy();
    assert.equal(frames.size, 0);
});

test('Jobs keeps stage details and tab selection together through live stage gaps', () => {
    const context = browserContext();
    context.ResizeObserver = class { observe() {} disconnect() {} };
    const { tweetApp, nookSyncPanel } = loadScripts(context, appScriptsWith('header.js'), '({tweetApp, nookSyncPanel})');
    vm.runInContext('nookJobsSheet = () => ({sync() {}, destroy() {}})', context);
    const app = immediateComponent(tweetApp());
    app.fetchArchiveEnrichmentStatus = () => {};
    const panel = nookMergeScope(app, nookSyncPanel()), watchers = new Map();
    panel.$root = {};
    panel.$watch = (key, callback) => watchers.set(key, callback);
    panel.init();
    const keys = ['sync:bookmarks:head', 'threads', 'articles', 'media'];
    const snapshot = (index, state, runId = 'sync-one') => ({
        run_id: runId, title: 'tweetnook sync', running: true, success: null, issues: [],
        steps: keys.map((key, i) => ({
            key, title: key, state: i < index ? 'complete' : i === index ? state : 'pending',
            summary: 'Finished.', activity: 'Working',
        })),
    });
    const apply = data => {
        const before = app.activity && JSON.parse(JSON.stringify(app.activity));
        app.applyActivityStatus(data);
        watchers.get('activity')(app.activity, before);
    };
    const show = (index, state, runId) => apply({active: true, snapshot: snapshot(index, state, runId)});
    const assertStep = (index, state) => {
        assert.equal(panel.focusIndex(), index);
        assert.equal(panel.selectedStep().key, keys[index]);
        assert.equal(panel.detail().state, state);
        assert.notEqual(panel.caption().detailLabel, 'Run result');
    };

    // A starting run has no active stage yet; its selected tab still owns the details.
    show(-1, 'pending');
    assertStep(0, 'pending');
    for (let index = 0; index < keys.length; index++) {
        show(index, 'active');
        assertStep(index, 'active');
        show(index, 'complete');
        assertStep(index, 'complete');
        // Other stages can be skipped while the next worker is preparing.
        if (index === 1) {
            const gap = snapshot(index, 'complete');
            gap.steps[2].state = 'skipped';
            apply({active: true, snapshot: gap});
            assertStep(index, 'complete');
        }
    }
    // Inspecting another stage persists across the gap, then follows newly active work.
    panel.pick(0);
    show(3, 'complete');
    assertStep(0, 'complete');
    show(2, 'active');
    assertStep(2, 'active');
    for (const state of ['failed', 'cancelled', 'skipped']) {
        show(2, state);
        assertStep(2, state);
    }
    // A queued run using the same stage key must clear the previous manual selection.
    panel.pick(0);
    show(2, 'active', 'sync-two');
    assertStep(2, 'active');
    show(2, 'complete', 'sync-two');
    assertStep(2, 'complete');
    for (const success of [true, false]) {
        apply({active: false, last_snapshot: {...snapshot(4, 'complete', 'sync-two'), running: false, success}});
        assert.equal(panel.focusIndex(), null);
        assert.equal(panel.selectedStep(), null);
        assert.equal(panel.caption().detailLabel, 'Run result');
        panel.pick(2);
        assertStep(2, 'complete');
        panel.pick(null);
    }
    // A later run with no active stage cannot inherit a prior run's remembered stage.
    show(-1, 'pending', 'sync-three');
    assertStep(0, 'pending');
    panel.destroy();
});

test('sync panel shows paused and skipped reasons, new tweets, and the server schedule label', () => {
    const context = browserContext();
    const { tweetApp, nookSyncPanel } = loadScripts(context, appScriptsWith('header.js'), '({tweetApp, nookSyncPanel})');
    const app = immediateComponent(tweetApp());
    const panel = nookMergeScope(app, nookSyncPanel());

    const paused = { key: 'tagging', title: 'Automated tags', state: 'failed', paused: true, reason: 'daily spend limit reached', summary: 'Stopped at the spend limit' };
    assert.equal(panel.stepState(paused), 'paused');
    assert.equal(panel.stepStateLabel(paused), 'paused');
    assert.equal(panel.describe(paused).left, 'Tag saved posts');
    assert.equal(panel.describe(paused).right, 'Paused');
    assert.equal(panel.describe(paused).status, 'daily spend limit reached');

    const failed = { key: 'media', title: 'Media', state: 'failed', summary: 'Disk full', reason: null };
    assert.equal(panel.describe(failed).danger, true);
    assert.equal(panel.describe(failed).status, 'Disk full');

    const skipped = { key: 'articles', title: 'Articles', state: 'skipped', reason: 'no article rows requiring refresh', summary: 'skipped due to no article rows requiring refresh' };
    assert.equal(panel.describe(skipped).status, 'No article rows requiring refresh');

    app.activity = null;
    app.lastActivity = {
        run_id: 'one', success: true, completed_at: 100,
        steps: [
            { key: 'sync:bookmarks:head', title: 'Bookmarks', state: 'complete', metrics: { new_tweets: 2 } },
            { key: 'sync:tweets:head', title: 'Your tweets', state: 'complete', metrics: { new_tweets: 1 } },
        ],
        issues: [],
    };
    assert.equal(panel.caption().left, '2 bookmarks and 1 tweet added');

    app.activitySchedule = { configured: true, label: 'Daily at 3:00 AM', description: 'Every day · randomized delay' };
    assert.equal(panel.scheduleLabel(), 'Daily at 3:00 AM');
    app.activitySchedule = { configured: false, label: 'Daily at 3:00 AM' };
    assert.equal(panel.scheduleLabel(), 'Scheduled syncs off');
});

test('jobs panel retains import and enrichment results and keeps them selectable', () => {
    const context = browserContext();
    const { tweetApp, nookSyncPanel } = loadScripts(context, appScriptsWith('header.js'), '({tweetApp, nookSyncPanel})');
    for (const key of ['import', 'enrich']) {
        for (const success of [true, false, null]) {
            const app = immediateComponent(tweetApp());
            const panel = nookMergeScope(app, nookSyncPanel());
            const title = `tweetnook import ${key === 'import' ? 'x-archive' : 'enrich'}`;
            const snapshot = {
                run_id: `${key}-run`, title, started_at: 100, completed_at: 200,
                success, summary: success ? '12 posts enriched' : 'Replay source unavailable',
                steps: [{key: 'archive-enrich', title: 'Enrichment', state: success ? 'complete' : 'failed', reason: success ? null : 'Replay source unavailable'}],
                issues: [],
            };
            panel.jobs = [
                {key: 'sync', command: 'tweetnook sync', name: 'Sync', scheduled: true, state: 'never'},
                {key, command: title, name: key === 'import' ? 'Import X archive' : 'Enrich archive', scheduled: false, state: 'running'},
                {key: 'media', command: 'tweetnook media download', name: 'Download media', scheduled: false, state: 'never'},
            ];
            app.activity = {...snapshot, running: true, success: null};
            panel.jobsRunning = {key: 'media'}; // Previous Jobs response during a Setup handoff.
            assert.equal(panel.shownKey(), key);
            assert.equal(panel.live(), true);
            assert.equal(panel.runningKey(), key);
            // Completion arrives through activity before the Jobs request finishes.
            app.lastActivity = snapshot;
            app.activity = null;
            assert.equal(panel.shownKey(), key);
            assert.equal(panel.displayActivity.run_id, snapshot.run_id);
            panel.jobs[1] = {...panel.jobs[1], state: success ? 'ok' : 'failed', last: snapshot, last_snapshot: snapshot};
            assert.equal(panel.displayActivity.run_id, snapshot.run_id);
            assert.equal(panel.steps()[0].key, 'archive-enrich');
            assert.equal(panel.headTitle(), `${key === 'import' ? 'Import' : 'Enrichment'} ${success ? 'finished' : success === false ? 'failed' : 'interrupted'}`);
            assert.equal(panel.overview().status, success ? 'All steps finished.' : snapshot.summary);
            assert.ok(!/Sync|Nothing new/.test(panel.overview().left));
            assert.equal(panel.onDemandShown().length, success ? 0 : 1);
            assert.equal(panel.isFocused(panel.jobs[1]), true);
            // A following Setup job must not erase the import's retained details.
            const media = {run_id: 'media-run', title: 'tweetnook media download', started_at: 300, completed_at: 400, success: true, steps: []};
            panel.jobs[2] = {...panel.jobs[2], state: 'ok', last: media, last_snapshot: media};
            app.lastActivity = media;
            panel.focus(key);
            assert.equal(panel.displayActivity.run_id, snapshot.run_id);
            assert.equal(panel.caption().status, success ? 'All steps finished.' : snapshot.summary);
            app.lastActivity = null; // Reload: only persisted Jobs data remains.
            panel.focusKey = null;
            assert.equal(panel.shownKey(), success ? 'media' : key);
            snapshot.stopped = true;
            snapshot.success = false;
            panel.focus(key);
            assert.equal(panel.headTitle(), `${key === 'import' ? 'Import' : 'Enrichment'} stopped`);
            panel.focusKey = null;
            assert.equal(panel.shownKey(), 'media');
            panel.jobs[1] = {...panel.jobs[1], last: null, last_snapshot: null, state: 'never'};
            panel.focus(key);
            assert.match(panel.overview().left, /hasn’t run yet/);
            assert.equal(panel.onDemandShown().length, 0);
            panel.moreOpen = true;
            assert.equal(panel.onDemandShown().length, 2);
        }
    }
});

test('collapsed On demand shows only failed jobs and expands to all run options', () => {
    const context = browserContext();
    const { tweetApp, nookSyncPanel } = loadScripts(context, appScriptsWith('header.js'), '({tweetApp, nookSyncPanel})');
    const panel = nookMergeScope(immediateComponent(tweetApp()), nookSyncPanel());
    panel.jobs = [
        {key: 'sync', scheduled: true, state: 'failed'},
        {key: 'threads', state: 'ok', last: {success: true}, last_snapshot: {success: true, issues: [{message: 'One post unavailable'}]}},
        {key: 'media', state: 'failed', last: {success: false}},
        {key: 'urls', state: 'never'},
        {key: 'articles', state: 'running', last: {success: false}},
        {key: 'enrich', state: 'queued', last: {success: false}},
        {key: 'resurrection', state: 'in_sync'},
        {key: 'import', state: 'ok', last: {success: false, status: 'stopped'}},
        {key: 'rehydrate', state: 'ok', last: {success: null, status: 'interrupted'}},
    ];
    assert.deepEqual(Array.from(panel.onDemandShown(), job => job.key), ['media']);
    assert.equal(panel.moreLabel(), '7 more');
    panel.moreOpen = true;
    assert.deepEqual(Array.from(panel.onDemandShown(), job => job.key), panel.jobs.slice(1).map(job => job.key));
    assert.equal(panel.moreLabel(), 'Show less');
    panel.moreOpen = false;
    // Retrying a failed run removes its exception; its saved failure stays accessible on expansion.
    panel.jobs[2].state = 'running';
    assert.equal(panel.onDemandShown().length, 0);
    assert.equal(panel.moreLabel(), '8 more');
    panel.jobs[2].state = 'ok';
    assert.equal(panel.onDemandShown().length, 0);
});

test('sync panel labels unknown progress without fabricated fractions and animates active work', () => {
    const context = browserContext();
    const { tweetApp, nookSyncPanel, nookSyncBreakdown } = loadScripts(context, appScriptsWith('header.js'), '({tweetApp, nookSyncPanel, nookSyncBreakdown})');
    const panel = nookMergeScope(immediateComponent(tweetApp()), nookSyncPanel());
    const step = { state: 'active', title: 'Bookmarks', activity: 'Fetching page 3', completed: 3, total: 4, show_progress: false };

    // Timeline page totals are placeholders even when positive.
    assert.equal(panel.indeterminate(step), true);
    assert.equal(panel.describe(step).right, 'Page 3');
    assert.equal(panel.describe(step).status, 'Fetching page 3');

    step.activity = 'Downloading media';
    step.show_progress = true;
    assert.equal(panel.indeterminate(step), false);
    assert.equal(panel.describe(step).right, '3 / 4');
    for (const total of [undefined, null, 0, -1, 'unknown', Infinity]) {
        step.total = total;
        assert.equal(panel.indeterminate(step), true);
        assert.equal(panel.describe(step).right, 'Working');
    }

    step.show_progress = false;
    for (const state of ['pending', 'complete', 'skipped', 'failed', 'cancelled']) {
        step.state = state;
        assert.equal(panel.indeterminate(step), false);
        const breakdown = nookSyncBreakdown({ state, stats: [], total: 0 });
        assert.ok(breakdown.bars.every(bar => !bar.kind.includes('is-indeterminate')));
    }
    step.state = 'failed';
    step.paused = true;
    assert.equal(panel.indeterminate(step), false);
    assert.equal(panel.describe(step).right, 'Paused');

    const active = nookSyncBreakdown({ state: 'active', stats: [], total: 0 });
    assert.deepEqual(Array.from(active.bars, bar => ({ ...bar })), [{ kind: 'done is-indeterminate', width: 30 }]);

    const html = pageHtml();
    const css = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'static', 'css', 'header.css'), 'utf8');
    assert.match(html, /class="cs-step-tab"[^\n]*stepState\(steps\(\)\[index\]\)/);
    assert.match(html, /class="bd-part" :class="'is-' \+ part.kind" :style="`width:\$\{part.width\}%`"/);
    assert.match(css, /\.bd-part\.is-indeterminate \{[^}]*animation: nook-sync-bounce[^}]*infinite alternate/);
    assert.match(css, /@keyframes nook-sync-bounce \{[^\n]*translateX\(233\.333333%\)/);
    assert.match(css, /@media \(prefers-reduced-motion: reduce\) \{[^\n]*\.bd-part\.is-indeterminate \{ animation: none;/);
});

run();
