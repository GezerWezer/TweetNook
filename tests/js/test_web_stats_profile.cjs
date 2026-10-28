'use strict';

const assert = require('node:assert/strict');
const { appScripts, pageHtml, pageScripts, readScripts } = require('./page_scripts.cjs');
const {
    WEB_FIXTURE,
    browserContext,
    feedListApp,
    immediateComponent,
    loadScripts,
    nookMergeScope,
    test,
    run,
    pageCss,
} = require('./web_harness.cjs');

test('every clickable byline name in the page opens that author\'s profile card', () => {
    const html = pageHtml();
    const names = html.match(/<span\b[^>]*hover:underline tweet-byline-name[^>]*>/g) || [];
    assert.equal(names.length, 10);
    for (const name of names) assert.match(name, /@click\.stop="openProfileCard\(\$event, [\w.]+\)"/);
});

test('reply-recipient links open an anchored profile card without searching', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(
        context,
        appScripts(),
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

test('profile cards show the newest captured profile and cycle through older ones', async () => {
    const context = browserContext();
    const requests = [];
    const pending = [];
    const snapshots = [
        { display_name: 'Alice Renamed', username: 'alice', description: 'New bio', followers_count: 20, following_count: 6, fetched_at: '2026-06-01T00:00:00Z' },
        { display_name: 'Alice', username: 'alice', description: 'Old bio', followers_count: 10, following_count: 5, fetched_at: '2026-01-01T00:00:00Z' },
    ];
    context.fetch = url => {
        requests.push(url);
        return new Promise(resolve => pending.push(() => resolve({
            ok: true,
            async json() { return { author_id: '42', snapshots }; },
        })));
    };
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    const anchor = { getBoundingClientRect: () => ({ left: 0, right: 10, top: 0, bottom: 10 }) };
    const settle = async () => { pending.shift()(); await new Promise(resolve => setTimeout(resolve, 0)); };
    const tweet = {
        author: { id: '42', username: 'alice', display_name: 'Alice' },
        synced_at: '2025-12-01T00:00:00Z',
        raw_json: { core: { user_results: { result: { legacy: { description: 'Tweet bio', followers_count: 1 } } } } },
    };

    app.openProfileCard({ currentTarget: anchor }, tweet, false, true);
    assert.equal(app.profileCard.description, 'Tweet bio');
    await settle();
    assert.equal(requests[0], '/api/authors/profile?author_id=42');
    assert.equal(app.profileCard.name, 'Alice Renamed');
    assert.equal(app.profileCard.description, 'New bio');
    assert.equal(app.profileCard.followersCount, 20);
    assert.equal(app.profileCard.syncDate, '2026-06-01T00:00:00Z');

    app.showProfileSnapshot(1);
    assert.equal(app.profileCard.snapshotIndex, 1);
    assert.equal(app.profileCard.description, 'Old bio');
    assert.equal(app.profileCard.followingCount, 5);
    assert.equal(app.profileCard.syncDate, '2026-01-01T00:00:00Z');
    app.showProfileSnapshot(2);
    assert.equal(app.profileCard.snapshotIndex, 1);

    // Mentions resolve their handle and pick up the author's avatar ID.
    app.openUsernameProfileCard({ currentTarget: anchor }, 'alice');
    await settle();
    assert.equal(requests[1], '/api/authors/profile?username=alice');
    assert.equal(app.profileCard.id, '42');
    assert.equal(app.profileCard.description, 'New bio');

    // A response for a card that was replaced or closed is ignored.
    app.openProfileCard({ currentTarget: anchor }, tweet, false, true);
    app.closeProfileCard();
    await settle();
    assert.equal(app.profileCard, null);

    // Profile cards near the bottom open above their anchor, then keep their
    // top while cycling so the arrows stay put, unless they would overflow.
    let cardHeight = 200;
    context.window.innerHeight = 600;
    context.document.getElementById = id => (id === 'profile-card-modal'
        ? { getBoundingClientRect: () => ({ width: 300, height: cardHeight }) }
        : null);
    const lowAnchor = { getBoundingClientRect: () => ({ left: 0, right: 10, top: 500, bottom: 520 }) };
    app.openProfileCard({ currentTarget: lowAnchor }, tweet, false, true);
    assert.equal(app.profileCard.y, 292);
    await settle();
    assert.equal(app.profileCard.y, 292);
    cardHeight = 150;
    app.showProfileSnapshot(1);
    assert.equal(app.profileCard.y, 292);
    cardHeight = 400;
    app.showProfileSnapshot(0);
    assert.equal(app.profileCard.y, 192);

    const html = pageHtml();
    assert.ok(html.includes('@click="showProfileSnapshot(profileCard?.snapshotIndex + 1)"'));
    assert.ok(html.includes('@click="showProfileSnapshot(profileCard?.snapshotIndex - 1)"'));
    assert.ok(html.includes('aria-label="Older profile capture"'));
});

test('analytics uses one cached snapshot and retains it during manual refresh', async () => {
    const context = browserContext();
    const calls = [];
    const generatedAt = new Date().toISOString();
    const snapshot = {
        generated_at: generatedAt,
        age_seconds: 0,
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
    const { tweetApp, statsDialog } = loadScripts(
        context,
        appScripts(),
        '({tweetApp, statsDialog})',
    );
    const app = immediateComponent(tweetApp());
    const watchers = new Map();
    app.$watch = (key, callback) => watchers.set(key, callback);
    const stats = nookMergeScope(app, statsDialog());
    stats.init();
    let polls = 0;
    stats.scheduleStatsRefreshPoll = () => { polls += 1; };

    app.openStatsModal();
    watchers.get('showStatsModal')(app.showStatsModal);
    await new Promise(resolve => setTimeout(resolve, 0));

    assert.equal(calls.length, 1);
    assert.equal(calls[0][0], '/api/stats/snapshot');
    assert.equal(stats.statsSummary.unique_posts, 12);
    assert.equal(stats.statsCollections[0].collection, 'Bookmarks');
    assert.equal(stats.storageData.total_bytes, 42);
    assert.equal(stats.statsTags.unique_tags, 3);
    assert.equal(stats.loadingStatsSnapshot, false);
    assert.equal(stats.statsAgeLabel(), 'Updated just now');
    // The header's last-sync text belongs to the app, which the dialog updates.
    assert.equal(app.lastSyncFormatted, 'Aug 10, 2026');

    await stats.refreshStats();

    assert.equal(calls[1][0], '/api/stats/refresh');
    assert.equal(calls[1][1].method, 'POST');
    assert.equal(stats.statsSummary.unique_posts, 12);
    assert.equal(stats.statsRefreshing, true);
    assert.equal(stats.statsAgeLabel(), 'Refreshing · updated just now');
    assert.equal(polls, 1);
});

test('closing analytics stops its refresh poll', () => {
    const context = browserContext();
    const cleared = [];
    context.clearTimeout = id => cleared.push(id);
    const { tweetApp, statsDialog } = loadScripts(
        context,
        appScripts(),
        '({tweetApp, statsDialog})',
    );
    const app = immediateComponent(tweetApp());
    const watchers = new Map();
    app.$watch = (key, callback) => watchers.set(key, callback);
    const stats = nookMergeScope(app, statsDialog());
    stats.init();
    app.showStatsModal = true;
    stats.statsRefreshPollTimer = 7;

    app.closeStatsModal();
    watchers.get('showStatsModal')(app.showStatsModal);

    assert.deepEqual(cleared, [7]);
    assert.equal(stats.statsRefreshPollTimer, null);
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
    const { tweetApp, statsDialog } = loadScripts(
        context,
        appScripts(),
        '({tweetApp, statsDialog})',
    );
    const app = immediateComponent(tweetApp());
    const stats = nookMergeScope(app, statsDialog());
    const originalSummary = { unique_posts: 12 };
    app.showStatsModal = true;
    stats.statsGeneratedAt = '2026-08-10T00:00:00+00:00';
    stats.statsSummary = originalSummary;
    stats.scheduleStatsRefreshPoll = () => {};

    await stats.pollStatsRefresh();

    assert.deepEqual(calls, ['/api/stats/status']);
    assert.equal(stats.statsSummary, originalSummary);
    assert.equal(stats.statsRefreshing, true);
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
        appScripts(),
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
    const { tweetApp, statsDialog } = loadScripts(
        context,
        appScripts(),
        '({tweetApp, statsDialog})',
    );
    const stats = nookMergeScope(immediateComponent(tweetApp()), statsDialog());
    stats.statsHealth = {
        enrichment: {
            unavailable: {
                reasons: [
                    {
                        reason: 'deleted_by_author',
                        count: 1,
                        percent_of_missing: 33.3,
                        due: 0,
                        delayed: 0,
                        permanent: 1,
                    },
                    {
                        reason: 'unavailable_unknown',
                        count: 2,
                        percent_of_missing: 66.7,
                        due: 1,
                        delayed: 1,
                        permanent: 0,
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
        Array.from(stats.getUnavailableReasons(), item => item.reason),
        ['unavailable_unknown', 'deleted_by_author'],
    );
    assert.deepEqual(
        Array.from(stats.getUnavailableBarReasons(), item => item.reason),
        ['unavailable_unknown', 'deleted_by_author'],
    );
    assert.equal(
        stats.getUnavailableReasonStatus(stats.statsHealth.enrichment.unavailable.reasons[1]),
        '1 due now · 1 scheduled',
    );
    assert.equal(
        stats.getUnavailableReasonStatus(stats.statsHealth.enrichment.unavailable.reasons[0]),
        '1 permanent',
    );
    assert.ok(
        stats.getUnavailableSegmentWidth(stats.statsHealth.enrichment.unavailable.reasons[1])
        > stats.getUnavailableSegmentWidth(stats.statsHealth.enrichment.unavailable.reasons[0]),
    );

    stats.showEmptyUnavailableReasons = true;
    assert.equal(stats.getUnavailableReasons().length, 3);
    assert.deepEqual(
        Array.from(stats.getUnavailableReasons(), item => item.reason),
        ['unavailable_unknown', 'deleted_by_author', 'withheld'],
    );
    assert.equal(stats.getUnavailableBarReasons().length, 2);
    assert.equal(
        stats.getUnavailableReasonStatus(stats.statsHealth.enrichment.unavailable.reasons[2]),
        'No unavailable tweets',
    );
});

test('analytics markup exposes the Archive status cards and reason breakdown', () => {
    const html = pageHtml();
    const appJs = readScripts(pageScripts());
    const styles = pageCss();

    assert.match(html, /role="switch"[^>]*:aria-checked="showEmptyUnavailableReasons" @click="showEmptyUnavailableReasons = !showEmptyUnavailableReasons"/);
    assert.match(html, /@click="refreshStats\(\)"/);
    assert.match(html, /@keydown\.window\.escape="closeStatsModal\(\)"/);
    assert.match(html, /@click\.self="closeStatsModal\(\)"/);
    assert.match(html, /@click="closeStatsModal\(\)"/);
    assert.match(html, /x-text="statsAgeLabel\(\)"/);
    assert.match(html, /x-text="statsRefreshing \? 'Refreshing' : 'Refresh'"/);
    assert.match(html, /x-show="statsRefreshing"/);
    assert.match(html, /class="stats-modal-scroll /);
    assert.match(styles, /\.stats-modal-scroll\s*\{[^}]*overscroll-behavior: contain;/s);
    const statsWatcherStart = appJs.indexOf("this.$watch('showStatsModal'");
    const settingsWatcherStart = appJs.indexOf("this.$watch('showSettingsModal'");
    assert.ok(statsWatcherStart >= 0 && statsWatcherStart < settingsWatcherStart);
    const statsWatcher = appJs.slice(statsWatcherStart, settingsWatcherStart);
    assert.match(statsWatcher, /if \(val\) this\.lockModalScroll\(\);/);
    assert.match(statsWatcher, /!this\.showSettingsModal && !this\.showSetupModal/);
    assert.match(statsWatcher, /this\.unlockModalScroll\(\);/);
});

test('reply-recipient markup opens profile cards on every tweet surface', () => {
    const html = pageHtml();

    assert.equal((html.match(/renderReplyRecipientContent\(/g) || []).length, 16);
    assert.equal((html.match(/getReplyRecipients\(child\)\.length > 1 \|\|/g) || []).length, 2);
    assert.equal((html.match(/threadData\.parents\.length === 0/g) || []).length, 1);
    assert.equal((html.match(/panelThreadData\.parents\.length === 0/g) || []).length, 1);
    // Feed cards and their quotes delegate reply targets (see the feed click test).
    const { app } = feedListApp();
    const card = app.renderFeedCard(JSON.parse(JSON.stringify(WEB_FIXTURE.slim[4])));
    assert.match(card, /data-action="reply-profile" data-username="frank">@frank</);
});

test('sync, analytics, and settings live in the top bar on every screen size', () => {
    const html = pageHtml();
    const utilities = html.slice(html.indexOf('<div class="nook-utilities">'), html.indexOf('<nav class="nook-tabs"'));

    assert.match(utilities, /class="nook-icon-button nook-activity-button" @click="nookToggleActivity\(\$data, \$el\)" aria-controls="nook-activity-drawer"/);
    assert.match(utilities, /@click="openStatsModal\(\)" aria-label="Analytics"/);
    assert.match(utilities, /@click="openSettings\(\)"/);
    assert.doesNotMatch(utilities, /hidden sm:flex/);
    assert.doesNotMatch(html, /<!-- Stats Button -->|<!-- Settings Button -->/);
    assert.match(html, /<section id="nook-activity-drawer" class="nook-sync-panel"/);
});

run();
