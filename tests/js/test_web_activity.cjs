'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { appScripts, appScriptsWith, pageHtml } = require('./page_scripts.cjs');
const {
    JS_DIR,
    browserContext,
    immediateComponent,
    loadScripts,
    nookMergeScope,
    test,
    run,
    pageCss,
} = require('./web_harness.cjs');

test('activity updates retain unchanged rows and handle removal, cancellation, and new runs', async () => {
    const context = browserContext();
    let data = {
        active: true,
        schedule: {enabled: true, relative: 'Tomorrow'},
        snapshot: {
            run_id: 'one', active_step: 'media',
            steps: [
                {key: 'media', state: 'active', completed: 1, total: 10, counters: 'old', metrics: {downloaded: 1, failed: 1}},
                {key: 'articles', state: 'pending', completed: 0, total: 10},
            ],
            issues: [{level: 'warning', message: 'Retry', count: 1}],
        },
    };
    context.fetch = async () => ({ok: true, json: async () => JSON.parse(JSON.stringify(data))});
    const {tweetApp} = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    await app.fetchActivityStatus();
    const original = app.activity;
    const steps = original.steps;
    const media = steps[0];
    const articles = steps[1];
    const metrics = media.metrics;
    const issues = original.issues;
    const schedule = app.activitySchedule;
    await app.fetchActivityStatus();
    assert.equal(app.activity, original);
    assert.equal(app.activity.steps, steps);
    assert.equal(app.activity.steps[0], media);
    assert.equal(app.activity.issues, issues);
    assert.equal(app.activitySchedule, schedule);

    data.snapshot.steps[0].completed = 2;
    data.snapshot.steps[0].metrics.downloaded = 2;
    delete data.snapshot.steps[0].metrics.failed;
    delete data.snapshot.steps[0].counters;
    data.snapshot.issues[0].count = 2;
    await app.fetchActivityStatus();
    assert.equal(app.activity.steps[0], media);
    assert.equal(app.activity.steps[1], articles);
    assert.equal(media.completed, 2);
    assert.equal(media.metrics, metrics);
    assert.equal(metrics.downloaded, 2);
    assert.equal(Object.hasOwn(metrics, 'failed'), false);
    assert.equal(Object.hasOwn(media, 'counters'), false);
    assert.equal(issues[0].count, 2);

    data.snapshot.steps[0].metrics = null;
    await app.fetchActivityStatus();
    assert.equal(media.metrics, null);
    data.snapshot.steps.reverse();
    await app.fetchActivityStatus();
    assert.deepEqual(Array.from(app.activity.steps, step => step.key), ['articles', 'media']);
    data.snapshot.steps.push({key: 'tag', state: 'pending'});
    await app.fetchActivityStatus();
    assert.equal(app.activity.steps.length, 3);

    data = {...data, active: false, last_snapshot: {...data.snapshot, stopped: true, success: false}, snapshot: null};
    data.last_snapshot.steps[1].state = 'failed';
    app.activityError = 'Temporary connection failure';
    await app.fetchActivityStatus();
    assert.equal(app.activity, null);
    assert.equal(app.activityError, null);
    assert.equal(app.activityPageKey, 'media');
    assert.equal(app.activitySegmentState(app.activitySelectedPage()), 'cancelled');
    const finished = app.lastActivity;
    await app.fetchActivityStatus();
    assert.equal(app.lastActivity, finished);

    data = {...data, active: true, snapshot: {run_id: 'two', steps: [{key: 'prepare', state: 'active'}]}};
    await app.fetchActivityStatus();
    assert.equal(app.activity.run_id, 'two');
    assert.equal(app.activityPageKey, null);
    assert.equal(app.activitySelectedPageKey(), 'prepare');
    assert.equal(Object.hasOwn(app.activity, 'stopped'), false);
});

test('activity requests coalesce and superseded responses cannot revive a finished run', async () => {
    const context = browserContext();
    const requests = [];
    context.fetch = (_url, {signal}) => new Promise(resolve => requests.push({resolve, signal}));
    const {tweetApp} = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    const older = app.fetchActivityStatus();
    const shared = app.fetchActivityStatus();
    assert.equal(requests.length, 1);
    const newer = app.fetchActivityStatus(true);
    assert.equal(requests.length, 2);
    assert.equal(requests[0].signal.aborted, true);
    requests[1].resolve({ok: true, json: async () => ({active: false, last_snapshot: {run_id: 'one', success: true, steps: []}})});
    await newer;
    const finished = app.lastActivity;
    // Ignore abort on purpose: late response/body completion must still be harmless.
    requests[0].resolve({ok: true, json: async () => ({active: true, snapshot: {run_id: 'one', steps: [{key: 'media', state: 'active', completed: 100}]}})});
    await Promise.all([older, shared]);
    assert.equal(app.activity, null);
    assert.equal(app.lastActivity, finished);
    assert.equal(app.activityError, null);
});

test('activity request timeout releases the request and a successful retry clears its error', async () => {
    const context = browserContext();
    let timeout;
    context.setTimeout = (callback, delay) => { assert.equal(delay, 10000); timeout = callback; return 1; };
    context.clearTimeout = () => {};
    context.fetch = (_url, {signal}) => new Promise((_resolve, reject) => {
        signal.addEventListener('abort', () => reject(new Error('aborted')), {once: true});
    });
    const {tweetApp} = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    const request = app.fetchActivityStatus();
    timeout();
    await request;
    assert.equal(app.activityError, 'Activity status request timed out');
    context.fetch = async () => ({ok: true, json: async () => ({active: false})});
    await app.fetchActivityStatus();
    assert.equal(app.activityError, null);
});

test('activity polling adapts to idle and visibility while its elapsed clock stays independent', async () => {
    const context = browserContext();
    const timers = new Map();
    const intervals = new Map();
    let nextId = 0;
    let now = 100000;
    context.Date = class extends Date { static now() { return now; } };
    context.setTimeout = (callback, delay) => { const id = ++nextId; timers.set(id, {callback, delay}); return id; };
    context.clearTimeout = id => timers.delete(id);
    context.setInterval = (callback, delay) => { const id = ++nextId; intervals.set(id, {callback, delay}); return id; };
    context.clearInterval = id => intervals.delete(id);
    let requests = 0;
    let data = {active: true, snapshot: {run_id: 'one', started_at: 90, steps: []}};
    context.fetch = async url => {
        if (url === '/api/stats/enrichment-incomplete') return {ok: true, json: async () => ({incomplete: 0})};
        requests++;
        return {ok: true, json: async () => JSON.parse(JSON.stringify(data))};
    };
    const {tweetApp} = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.startActivityPolling();
    app.startActivityPolling();
    await app.fetchActivityStatus();
    assert.equal(requests, 1);
    assert.equal(intervals.size, 1);
    assert.deepEqual([...timers.values()].map(timer => timer.delay), [1000]);
    const original = app.activity;
    now = 101000;
    [...intervals.values()][0].callback();
    assert.equal(app.activityNowSeconds, 101);
    assert.equal(app.activity, original);
    const html = pageHtml();
    const headerJs = fs.readFileSync(path.join(JS_DIR, 'header.js'), 'utf8');
    assert.match(headerJs, /this\.formatActivityDuration\(this\.activityNowSeconds - this\.activity\.started_at\)/);

    data = {active: false, last_snapshot: {run_id: 'one', success: true, steps: []}};
    await app.fetchActivityStatus();
    assert.deepEqual([...timers.values()].map(timer => timer.delay), [5000]);
    context.document.hidden = true;
    context.__state.events.get('document:visibilitychange')();
    assert.equal(timers.size, 0);
    now = 102000;
    [...intervals.values()][0].callback();
    assert.equal(app.activityNowSeconds, 101);
    context.document.hidden = false;
    context.__state.events.get('document:visibilitychange')();
    await app.fetchActivityStatus();
    assert.equal(requests, 3);
    assert.equal(app.activityNowSeconds, 102);
    app.destroy();
    assert.equal(timers.size, 0);
    assert.equal(intervals.size, 0);
    assert.equal(context.__state.events.has('document:visibilitychange'), false);
    assert.equal(context.__state.events.has('focus'), false);
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
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.archiveReady = true;
    let activityRefreshes = 0, enrichmentRefreshes = 0;
    app.fetchActivityStatus = async force => { assert.equal(force, true); activityRefreshes++; app.activityStartPending = false; };
    app.fetchArchiveEnrichmentStatus = async () => { enrichmentRefreshes++; };

    await app.startActivity('enrich');

    assert.deepEqual(requests, [['/api/activity/enrich', 'POST']]);
    assert.equal(activityRefreshes, 1);
    assert.equal(enrichmentRefreshes, 1);
    const html = pageHtml();
    assert.match(html, /Continue enrichment/);
});

test('enrichment completion refreshes pending work before showing a reminder', async () => {
    const context = browserContext();
    let finishCount;
    context.fetch = async url => {
        if (url === '/api/stats/enrichment-incomplete') {
            return {ok: true, json: () => new Promise(resolve => { finishCount = resolve; })};
        }
        assert.equal(url, '/api/activity/status');
        return {ok: true, json: async () => ({active: false})};
    };
    const {tweetApp} = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.setupForced = false;
    app.archiveEnrichmentIncomplete = 10;
    app.activity = {run_id: 'enrichment', title: 'tweetnook import enrich'};
    assert.equal(app.showArchiveEnrichmentReminder, false);
    await app.fetchActivityStatus();
    assert.equal(app.activity, null);
    assert.equal(app.showArchiveEnrichmentReminder, false);
    finishCount({incomplete: 0});
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(app.archiveEnrichmentIncomplete, 0);
    assert.equal(app.showArchiveEnrichmentReminder, false);
    app.archiveEnrichmentIncomplete = 2;
    assert.equal(app.showArchiveEnrichmentReminder, true);
    app.activityStartPending = true;
    assert.equal(app.showArchiveEnrichmentReminder, false);
    app.activityStartPending = false;
    // The first sync finishes leftovers once connected, so Setup's wait doesn't nag.
    for (const state of ['waiting_for_setup', 'waiting_for_auth']) {
        app.setupData = {archive: {enrichment_state: state}};
        assert.equal(app.showArchiveEnrichmentReminder, false);
    }
    const html = pageHtml();
    assert.match(html, /x-show="showArchiveEnrichmentReminder"/);
    assert.doesNotMatch(html, /role="status">Archive enrichment is running in the background/);
});

test('a slow enrichment count cannot overwrite the completion refresh', async () => {
    const context = browserContext();
    const counts = [];
    context.fetch = async () => ({ok: true, json: () => new Promise(resolve => counts.push(resolve))});
    const {tweetApp} = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    const older = app.fetchArchiveEnrichmentStatus();
    const newer = app.fetchArchiveEnrichmentStatus();
    await new Promise(resolve => setImmediate(resolve));
    counts[1]({incomplete: 0});
    await newer;
    counts[0]({incomplete: 50});
    await older;
    assert.equal(app.archiveEnrichmentIncomplete, 0);
    assert.equal(app.archiveEnrichmentRefreshing, false);
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
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
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

test('activity stages use focused pages and finish on a run summary', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
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
    assert.equal(app.activityIssues().length, 2);
    assert.equal(app.activityIssueCount(), 3);
    assert.equal(app.activityRunState(), 'active');
    assert.equal(app.activityRunStatusTitle(), 'Sync in progress');
    assert.equal(app.activityRunCounts(), '4 stages · 1 completed · 1 skipped');
    assert.deepEqual(
        Array.from(app.activityPages(), page => page.key),
        ['one', 'two', 'three', 'four', '__summary__'],
    );
    assert.equal(app.activityPages().at(-1).state, 'pending');
    assert.equal(app.activitySelectedSegmentGrow(), 8 / 3);
    assert.equal(app.activitySegmentPercent(app.activity.steps[0]), 100);
    assert.equal(app.activitySegmentPercent(app.activity.steps[1]), 100);
    assert.equal(app.activitySegmentPercent(app.activity.steps[2]), 50);
    assert.equal(app.activitySegmentPercent(app.activity.steps[3]), 0);
    assert.equal(app.activitySegmentPercent(app.activityPages().at(-1)), 0);
    assert.equal(app.activitySegmentState(app.activity.steps[2]), 'active');
    assert.equal(app.activityPageStateLabel(app.activity.steps[0]), 'Complete');
    assert.equal(app.activityPageStateLabel(app.activity.steps[1]), 'Skipped');
    assert.equal(app.activityPageStateLabel(app.activity.steps[2]), 'In progress');
    assert.equal(app.activityPageUpdateText(app.activity.steps[2]), 'Latest update · Waiting for first result');
    assert.equal(app.activityPageTimingText(app.activity.steps[2]), 'Timing · Estimating remaining time');
    app.activity.steps[2].counters = '2 processed';
    app.activity.steps[2].eta_seconds = 30;
    assert.equal(app.activityPageUpdateText(app.activity.steps[2]), 'Latest update · 2 processed');
    assert.equal(app.activityPageTimingText(app.activity.steps[2]), 'Timing · About 00:30 remaining');
    assert.equal(app.activityRunStateLabel(), 'In progress');
    assert.equal(app.activitySelectedPageKey(), 'three');
    assert.equal(app.activitySelectedPageIndex(), 2);
    assert.equal(app.activityPageDirection, 'forward');

    app.selectActivityPage('one');
    assert.equal(app.activitySelectedPageKey(), 'one');
    assert.equal(app.activityPageDirection, 'back');
    app.moveActivityPage(1);
    assert.equal(app.activitySelectedPageKey(), 'two');
    assert.equal(app.activityPageDirection, 'forward');
    app.moveActivityPage(-10);
    assert.equal(app.activitySelectedPageKey(), 'one');
    assert.equal(app.activityPageDirection, 'back');
    app.openActivityDrawer();
    assert.equal(app.activityDrawerOpen, true);
    assert.equal(app.activityPageKey, null);
    assert.equal(app.activityPageDirection, 'forward');
    assert.equal(app.activitySelectedPageKey(), 'three');
    app.toggleActivityDrawer();
    assert.equal(app.activityDrawerOpen, false);
    assert.equal(app.activityCommandLabel(), 'Archive Sync');

    app.activity.title = 'tweetnook import enrich';
    assert.equal(app.activityCommandLabel(), 'Archive Preparation');
    app.activity.title = 'tweetnook media download';
    assert.equal(app.activityCommandLabel(), 'Media Download');
    app.activityStartPending = true;
    app.activityStartingKind = 'enrich';
    assert.equal(app.activityCommandLabel(), 'Archive Preparation');
    app.activityStartPending = false;
    app.activityStartingKind = null;
    app.activity.title = 'tweetnook sync';

    app.lastActivity = {
        ...app.activity,
        running: false,
        success: true,
        steps: app.activity.steps.map(step => ({ ...step, state: 'complete' })),
    };
    app.activity = null;
    app.activityPageKey = null;
    assert.equal(app.activityRunState(), 'complete');
    assert.equal(app.activityRunStateLabel(), 'Complete');
    assert.equal(app.activityRunStatusTitle(), 'Last run completed');
    assert.equal(app.activitySelectedPageKey(), '__summary__');
    assert.equal(app.activitySelectedPage().is_summary, true);
    assert.equal(app.activityPages().at(-1).state, 'complete');
    assert.equal(app.activitySegmentPercent(app.activityPages().at(-1)), 100);
    assert.equal(app.activityRunCounts(), '4 stages · 4 completed');

    app.lastActivity = {
        ...app.lastActivity,
        success: false,
        stopped: true,
        steps: [
            { key: 'one', title: 'First step', state: 'complete', completed: 1, total: 1 },
            { key: 'three', title: 'Stopped step', state: 'failed', completed: 2, total: 4 },
            { key: 'four', title: 'Later step', state: 'skipped', completed: 0, total: 1 },
        ],
    };
    const stoppedPage = app.activityPages().find(page => page.key === 'three');
    assert.equal(app.activitySegmentState(stoppedPage), 'cancelled');
    assert.equal(app.activityPageStateLabel(stoppedPage), 'Canceled');
    assert.equal(app.activityRunStateLabel(), 'Canceled');
    assert.equal(app.activitySegmentPercent(stoppedPage), 50);
    assert.equal(app.activitySegmentState(app.activityPages().at(-1)), 'cancelled');
    assert.equal(app.activitySegmentPercent(app.activityPages().at(-1)), 100);
    assert.equal(app.activityRunCounts(), '3 stages · 1 completed · 1 skipped · 1 canceled');
});

test('inactive unknown results are interrupted across the drawer and run history', () => {
    const context = browserContext();
    const { tweetApp, nookSyncPanel, nookLogsPage } = loadScripts(context,
        appScriptsWith('header.js', 'settings.js'), '({tweetApp, nookSyncPanel, nookLogsPage})');
    const app = immediateComponent(tweetApp());
    app.lastActivity = {run_id: 'unknown', running: false, success: null, steps: []};
    const panel = nookMergeScope(app, nookSyncPanel());
    assert.equal(panel.headTitle(), 'Sync interrupted');
    assert.equal(app.activityRunStateLabel(), 'Interrupted');
    assert.equal(app.activityRunStatusTitle(), 'Last run interrupted');
    assert.equal(app.activityRunState(), 'failed');
    app.lastSyncFormatted = 'Oct 17, 2026';
    assert.equal(app.activityHeaderText(), 'Sync interrupted — open Activity');
    const logs = nookLogsPage();
    assert.equal(logs.statusLabel({status: 'interrupted'}), 'Interrupted');
    assert.equal(logs.statusKind({status: 'interrupted'}), 'failed');
    app.lastActivity.success = false;
    assert.equal(panel.headTitle(), 'Sync failed');
    assert.equal(app.activityHeaderText(), 'Sync failed — open Activity');
    app.lastActivity.success = true;
    assert.equal(panel.headTitle(), 'Synced');
    assert.equal(app.activityHeaderText(), 'Synced Oct 17, 2026');
});

test('activity schedule footer separates the day from the emphasized run time', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());

    app.activitySchedule = {
        enabled: true,
        relative: 'daily around 3:00 AM',
        date: 'Tomorrow · browser local time',
        config: { cadence: 'daily', time: '03:00', randomize_time: true },
    };
    assert.equal(app.activityScheduleDayText(), 'Tomorrow');
    assert.equal(app.activityScheduleTimeText(), 'around 3:00 AM');

    app.activitySchedule = {
        enabled: false,
        relative: 'Not scheduled',
        date: 'Enable scheduling in Settings',
        config: {},
    };
    assert.equal(app.activityScheduleDayText(), 'Not scheduled');
    assert.equal(app.activityScheduleTimeText(), '');
});

test('activity run summary turns structured outcomes into analytics-style cards', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.lastActivity = {
        running: false,
        success: true,
        elapsed_seconds: 180,
        issues: [{ level: 'warning', message: 'One retry', count: 2 }],
        steps: [
            { key: 'sync:bookmarks:head', metrics: { new_tweets: 12, tweets_seen: 20, pages: 1 } },
            { key: 'sync:likes:head', metrics: { new_tweets: 8, tweets_seen: 20, pages: 1 } },
            { key: 'threads', metrics: { expanded: 6, already_known: 148, failed: 0 } },
            { key: 'resurrection', metrics: { restored: 1, still_unavailable: 15, retry_later: 2, remaining_due: 3 } },
            { key: 'media', metrics: { downloaded: 28, downloaded_bytes: 19503514, already_saved: 4, failed: 0 } },
            { key: 'urls', metrics: { updated: 2, failed: 0 } },
            { key: 'articles', state: 'skipped', metrics: {} },
        ],
    };

    const cards = Object.fromEntries(
        app.activitySummaryMetrics().map(card => [card.key, card]),
    );
    assert.equal(cards['sync:bookmarks'].value, '12');
    assert.equal(cards['sync:bookmarks'].subtitle, '20 checked · 1 page');
    assert.equal(cards['sync:likes'].label, 'New liked tweets');
    assert.equal(cards['sync:likes'].value, '8');
    assert.equal(cards['tweets-checked'].value, '40');
    assert.equal(cards['pages-fetched'].value, '2');
    assert.equal(cards['media-downloaded'].value, '28');
    assert.equal(cards['media-size'].label, 'Media size');
    assert.equal(cards['media-size'].value, '18.6 MiB');
    assert.equal(cards['media-saved'].value, '4');
    assert.equal(cards['media-failed'].value, '0');
    assert.equal(cards.threads.subtitle, 'New context saved');
    assert.equal(cards['threads-known'].value, '148');
    assert.equal(cards['threads-known'].wide, true);
    assert.equal(cards['threads-failed'].value, '0');
    assert.equal(cards.restored.label, 'Tweets resurrected');
    assert.equal(cards.restored.value, '1');
    assert.equal(cards['still-unavailable'].value, '15');
    assert.equal(cards['still-unavailable'].subtitle, '2 retry later · 3 still due');
    assert.equal(cards['still-unavailable'].wide, true);
    assert.equal(cards['recovery-retries'].value, '2');
    assert.equal(cards['recovery-queue'].value, '3');
    assert.equal(cards.urls.value, '2');
    assert.equal(cards.urls.label, 'Link previews updated');
    assert.equal(cards.urls.wide, true);
    assert.equal(cards['urls-failed'].value, '0');
    assert.equal(cards.articles.subtitle, 'No refresh needed');
    assert.equal(cards['articles-failed'].value, '0');
    assert.equal(cards.duration.value, '03:00');
    assert.equal(cards.issues.value, '2');
    assert.equal(Object.keys(cards).length, 21);

    app.activity = {
        running: true,
        elapsed_seconds: 0,
        steps: [{ key: 'media', state: 'pending', metrics: {} }],
        issues: [],
    };
    app.lastActivity = null;
    const plannedCards = Object.fromEntries(
        app.activitySummaryMetrics().map(card => [card.key, card]),
    );
    assert.equal(plannedCards['media-downloaded'].value, '—');
    assert.equal(plannedCards['media-size'].value, '—');
    assert.equal(plannedCards['media-saved'].subtitle, 'Available after this stage');
});

test('notification badge counts every unread item and actions use Settings destinations', async () => {
    const context = browserContext();
    const requests = [];
    context.fetch = async (url, options) => { requests.push([url, options.method]); return {ok: true}; };
    const { tweetApp } = loadScripts(context, appScriptsWith('settings.js'), '({tweetApp})');
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

test('notices link to their run log and support bulk dismiss and clearing history', async () => {
    const context = browserContext();
    const requests = [];
    context.fetch = async (url, options) => { requests.push([url, options?.method]); return {ok: true, json: async () => ({runs: []})}; };
    const { tweetApp, nookNoticeWhen } = loadScripts(context, appScriptsWith('settings.js'), '({tweetApp, nookNoticeWhen})');
    const app = immediateComponent(tweetApp());
    app.openSettings();
    app.notices = [
        {id: 'a', dismissed: false, action: 'activity', run_id: 'run-a'},
        {id: 'b', dismissed: false, action: 'setup', run_id: 'run-b'},
        {id: 'c', dismissed: true, action: 'activity'},
    ];

    app.openNotice(app.notices[0]);
    assert.equal(app.settingsTab, 'logs');
    assert.equal(app.nookLogOpenRunId, 'run-a');
    assert.equal(app.activityDrawerOpen, false);
    app.openNoticeLog(app.notices[1]);
    assert.equal(app.nookLogOpenRunId, 'run-b');

    await app.dismissAllNotices();
    assert.equal(app.noticeCount, 0);
    await app.clearDismissedNotices();
    assert.deepEqual(app.notices, []);
    assert.deepEqual(requests.filter(([url]) => url.startsWith('/api/notices')), [
        ['/api/notices/dismiss-all', 'POST'],
        ['/api/notices/dismissed', 'DELETE'],
    ]);

    assert.equal(nookNoticeWhen({}), '');
    assert.match(nookNoticeWhen({created_at: new Date().toISOString()}), /^Today, /);
});

test('notification list shows times, run logs, and bulk actions', () => {
    const html = pageHtml();
    assert.match(html, /x-show="noticeCount > 1" @click="dismissAllNotices\(\)">Dismiss all</);
    assert.match(html, /@click="clearDismissedNotices\(\)">Clear history</);
    assert.match(html, /class="notice-time" x-show="nookNoticeWhen\(notice\)"/);
    assert.match(html, /x-show="notice.action !== 'activity' && notice.run_id" @click="openNoticeLog\(notice\)">View log</);
});

test('notifications live in Settings with unread counts', () => {
    const html = pageHtml();
    const settingsJs = fs.readFileSync(path.join(JS_DIR, 'settings.js'), 'utf8');
    assert.match(html, /id="nook-nav-notifications" :aria-label="noticeCount \? 'Notifications, ' \+ noticeCount \+ ' unread' : 'Notifications'"/);
    assert.match(html, /@click="nookSettingsNavigate\(\$data, 'notifications'\)"/);
    assert.match(settingsJs, /if \(key === 'notifications'\) app\.fetchNotices\(\);/);
    assert.match(settingsJs, /notifications:'Notifications'/);
    assert.match(html, /Settings, \$\{noticeCount\} unread notifications/);
    assert.match(html, /class="nook-nav-count" aria-hidden="true" x-text="noticeCount"/);
    assert.match(html, /class="nook-notice-count" aria-hidden="true" x-text="noticeCount > 9 \? '9\+' : noticeCount"/);
});

test('activity drawer animation isolates layout and transitions only geometry', () => {
    const css = pageCss();
    const drawerRule = css.match(/\.activity-drawer\s*\{([^}]*)\}/)?.[1] || '';
    const readyRule = css.match(/\.activity-drawer\.activity-drawer-ready\s*\{([^}]*)\}/)?.[1] || '';

    assert.match(drawerRule, /contain: layout paint style;/);
    assert.match(readyRule, /will-change: width, height, right, bottom, border-radius;/);
    assert.doesNotMatch(readyRule, /transition:\s*all\b/);
    for (const property of ['width', 'height', 'right', 'bottom', 'border-radius']) {
        assert.match(readyRule, new RegExp(`(?:transition:|,)\\s*${property} 0\\.35s`));
    }
});

test('activity log view follows a running log by byte offset', async () => {
    const context = browserContext();
    const chunks = {
        0: ['tweetnook sync: started\nPrepare: complete · Connected\n', 52],
        52: ['Bookmarks: complete · 1 pages · 3 tweets\n', 94],
        94: ['tweetnook sync: started\n', 24],
    };
    const requests = [];
    context.fetch = async url => {
        requests.push(url);
        const offset = Number(new URL(url, 'http://localhost').searchParams.get('offset'));
        const [text, next] = chunks[offset];
        return { ok: true, async text() { return text; }, headers: { get: name => (name === 'X-Log-Offset' ? String(next) : null) } };
    };
    const { nookLogsPage, nookRunTitle } = loadScripts(context, ['settings.js'], '({nookLogsPage, nookRunTitle})');
    const page = Object.assign(nookLogsPage(), { nookLogLines: null });

    page.nookLogLines = await page.readLog('run/1', 0);
    assert.equal(requests[0], '/api/activity/runs/run%2F1/log?offset=0');
    assert.equal(page.logOffset, 52);
    assert.equal(page.nookLogLines.length, 2);

    page.nookLogLines = await page.readLog('run/1', page.logOffset);
    assert.equal(page.logOffset, 94);
    assert.deepEqual([...page.nookLogLines.map(line => line.index)], [0, 1, 2]);
    assert.match(page.nookLogLines[2].text || JSON.stringify(page.nookLogLines[2]), /Bookmarks/);

    // A log that starts over (the server's offset went backwards) replaces what was shown.
    chunks[94] = ['tweetnook sync: started\n', 24];
    page.nookLogLines = await page.readLog('run/1', page.logOffset);
    assert.equal(page.logOffset, 24);
    assert.equal(page.nookLogLines.length, 1);

    assert.equal(nookRunTitle('tweetnook sync'), 'Archive sync');
    assert.equal(nookRunTitle('tweetnook import x-archive'), 'Archive import');
    assert.equal(nookRunTitle('tweetnook export'), 'Export');
});

test('timestamped worker logs show local times and retain UTC timestamps in exports', async () => {
    const context = browserContext();
    const { nookLogParse, nookLogsPage } = loadScripts(context, ['settings.js'], '({nookLogParse, nookLogsPage})');
    const stamp = '2026-10-17T21:01:16.123456Z';
    const raw = `${stamp} Media: progress · 47 files\n${stamp} ERROR: Storage is full.\n`;
    const lines = nookLogParse(raw);
    const localTime = new Date(stamp).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'});
    assert.equal(lines[0].time, localTime);
    assert.equal(lines[0].text, 'Media: progress · 47 files');
    assert.equal(lines[0].quiet, true);
    assert.equal(lines[1].tone, 'error');
    assert.equal(lines[1].time, localTime);
    assert.equal(nookLogParse('Older log without a timestamp\n')[0].time, '');
    const page = nookLogsPage();
    page.nookLogLines = lines;
    assert.equal(page.rawText(), raw); // Copy and Download both use rawText().
    let copied = null;
    context.navigator = {clipboard: {writeText: async value => { copied = value; }}};
    context.setTimeout = () => 0;
    await page.copy();
    assert.equal(copied, raw);
    assert.equal(page.copied, true);
});

test('log copying falls back on HTTP and clipboard rejection, and only confirms success', async () => {
    for (const mode of ['http', 'rejected', 'failed']) {
        const context = browserContext();
        const { tweetApp, nookLogsPage, nookLogParse } = loadScripts(
            context, appScriptsWith('settings.js'), '({tweetApp, nookLogsPage, nookLogParse})',
        );
        const page = nookMergeScope(tweetApp(), nookLogsPage());
        const raw = '2026-10-22T21:01:16.123456Z Media: progress · 47 files\nERROR: Storage is full.\n';
        page.nookLogLines = nookLogParse(raw);
        page.copied = true;
        let apiCalls = 0;
        context.navigator = mode === 'http' ? {} : {
            clipboard: { async writeText(text) {
                apiCalls++;
                assert.equal(text, raw);
                throw new Error('Clipboard access denied');
            } },
        };
        const calls = [];
        const textarea = {
            style: {},
            setAttribute() {},
            focus() { calls.push('focus'); },
            select() { calls.push('select'); },
        };
        context.document.createElement = tag => {
            assert.equal(tag, 'textarea');
            return textarea;
        };
        context.document.body.appendChild = node => {
            assert.equal(node, textarea);
            assert.equal(node.value, raw);
            calls.push('append');
        };
        context.document.body.removeChild = node => {
            assert.equal(node, textarea);
            calls.push('remove');
        };
        context.document.execCommand = command => {
            assert.equal(command, 'copy');
            calls.push('copy');
            return mode !== 'failed';
        };
        let reset = null;
        context.setTimeout = (callback, delay) => {
            assert.equal(delay, 2000);
            reset = callback;
        };

        await page.copy();

        assert.equal(apiCalls, mode === 'http' ? 0 : 1);
        assert.deepEqual(calls, ['append', 'focus', 'select', 'copy', 'remove']);
        assert.equal(page.copied, mode !== 'failed');
        if (mode === 'failed') assert.equal(reset, null);
        else {
            reset();
            assert.equal(page.copied, false);
        }
    }
});

test('combined archive preparation keeps its name through enrichment, sync, media and history', () => {
    const context = browserContext();
    const { tweetApp, nookRunTitle } = loadScripts(context, appScriptsWith('settings.js'), '({tweetApp, nookRunTitle})');
    const app = immediateComponent(tweetApp());
    for (const key of ['archive-enrich', 'sync:likes:head', 'media']) {
        app.activity = { title: 'tweetnook import enrich', steps: [{key, state: 'active'}] };
        assert.equal(app.activityHeaderText(), 'Archive Preparation in progress…');
        assert.equal(nookRunTitle(app.activity.title), 'Archive preparation');
    }
    app.activity = null;
    app.activityStartPending = true;
    app.activityStartingKind = 'enrich';
    assert.equal(app.activityCommandLabel(), 'Archive Preparation');
    assert.equal(nookRunTitle('tweetnook enrich'), 'Archive preparation');
    assert.equal(nookRunTitle('tweetnook rehydrate'), 'Archive enrichment');
    assert.equal(nookRunTitle('tweetnook sync'), 'Archive sync');
});

test('log restoration shares in-flight reads and ignores responses from a replaced log', async () => {
    const context = browserContext(), pending = [], timers = new Map(), watchers = {};
    let nextTimer = 0;
    context.setTimeout = callback => { timers.set(++nextTimer, callback); return nextTimer; };
    context.clearTimeout = timer => timers.delete(timer);
    context.fetch = url => new Promise(resolve => pending.push({url, resolve}));
    const page = immediateComponent(loadScripts(context, ['settings.js'], 'nookLogsPage')());
    Object.assign(page, {showSettingsModal: true, settingsTab: 'logs', activityRuns: []});
    page.$watch = (key, callback) => { watchers[key] = callback; };
    page.init();
    const opening = page.open({run_id: 'old', status: 'running'});
    page.showSettingsModal = false;
    await watchers.showSettingsModal();
    page.showSettingsModal = true;
    const restoring = watchers.showSettingsModal();
    assert.equal(pending.length, 2); // No competing poll while the first request is in flight.
    const switching = page.open({run_id: 'new', status: 'starting'});
    const resolve = (start, status, text) => {
        pending[start].resolve({ok: true, json: async () => ({status})});
        pending[start + 1].resolve({ok: true, text: async () => text, headers: {get: () => String(text.length)}});
    };
    resolve(2, 'running', 'New run\n');
    await switching;
    resolve(0, 'failed', 'Old run failed with a much longer log\n');
    await Promise.all([opening, restoring]);
    assert.equal(page.nookLogRun.run_id, 'new');
    assert.equal(page.nookLogRun.status, 'running');
    assert.equal(page.logOffset, 8);
    assert.equal(page.nookLogLines.length, 1);
    assert.equal(page.nookLogLines[0].raw, 'New run');
    assert.equal(timers.size, 1);
    const polling = page.refreshLive('new');
    page.showSettingsModal = false;
    await watchers.showSettingsModal();
    resolve(4, 'running', '');
    await polling;
    assert.equal(timers.size, 0);
});

test('historical activity on first load does not announce fresh content in demos or archives', async () => {
    for (const isDemo of [true, false]) {
        for (const feedLoaded of [true, false]) {
            const context = browserContext();
            context.window.TWEETNOOK_DEMO = isDemo;
            const history = {run_id: 'saved-sync', running: false, success: true,
                steps: [{key: 'sync:likes:head', state: 'complete'}, {key: 'media', state: 'complete'}]};
            let snapshot = history;
            context.fetch = async () => ({ok: true, json: async () => ({active: false, last_snapshot: snapshot})});
            const {tweetApp} = loadScripts(context, appScripts(), '({tweetApp})');
            const app = immediateComponent(tweetApp());
            app.archiveReady = true;
            app.tweets = feedLoaded ? [{tweet_id: 'reading'}] : [];
            let feeds = 0;
            app.fetchTweets = async () => { feeds++; };
            await app.fetchActivityStatus();
            assert.equal(app.lastActivity.run_id, 'saved-sync');
            assert.equal(app.feedUpdateAvailable, false, `initial history: demo=${isDemo}, feed=${feedLoaded}`);
            assert.equal(app.feedContentRevision, 0);
            assert.equal(feeds, 0);
            await app.fetchActivityStatus();
            assert.equal(app.feedUpdateAvailable, false);
            // A different completed run detected later still updates the feed.
            snapshot = {...history, run_id: 'new-sync'};
            await app.fetchActivityStatus();
            assert.equal(app.feedUpdateAvailable, true);
            assert.equal(app.feedContentRevision, 1);
            assert.equal(feeds, feedLoaded ? 0 : 1);
        }
    }
});

test('a run finishing between initial status polls still announces new content', async () => {
    const context = browserContext();
    let snapshot = null;
    context.fetch = async () => ({ok: true, json: async () => ({active: false, last_snapshot: snapshot})});
    const app = immediateComponent(loadScripts(context, appScripts(), 'tweetApp')());
    app.archiveReady = true;
    app.tweets = [{tweet_id: 'reading'}];
    await app.fetchActivityStatus();
    assert.equal(app.feedUpdateAvailable, false);
    snapshot = {run_id: 'new-sync', steps: [{key: 'sync:likes:head', state: 'complete'}]};
    await app.fetchActivityStatus();
    assert.equal(app.feedUpdateAvailable, true);
    assert.equal(app.feedContentRevision, 1);
});

test('a task started before the first status response still announces its saved content', async () => {
    const context = browserContext();
    context.fetch = async () => ({ok: true, json: async () => ({active: false,
        last_snapshot: {run_id: 'requested-sync', steps: [{key: 'sync:likes:head', state: 'complete'}]},
    })});
    const app = immediateComponent(loadScripts(context, appScripts(), 'tweetApp')());
    app.archiveReady = true;
    app.tweets = [{tweet_id: 'reading'}];
    app.activityStartPending = true;
    await app.fetchActivityStatus();
    assert.equal(app.feedUpdateAvailable, true);
    assert.equal(app.feedContentRevision, 1);
});

test('committed content offers a refresh without disturbing a populated feed', async () => {
    const context = browserContext();
    context.fetch = async () => ({ok: true, json: async () => ({active: true,
        snapshot: {run_id: 'sync', steps: [{key: 'sync:likes:head', state: 'complete'}]},
    })});
    const {tweetApp} = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.archiveReady = true;
    app.tweets = [{tweet_id: 'reading'}];
    let feeds = 0;
    app.fetchTweets = async () => { feeds++; };
    await app.fetchActivityStatus();
    assert.equal(app.feedUpdateAvailable, true);
    assert.equal(feeds, 0);
    assert.equal(app.tweets[0].tweet_id, 'reading');
    const markup = pageHtml();
    assert.match(markup, /x-show="feedUpdateAvailable"[^>]*@click="fetchTweets\(\)"/);
});

run();
