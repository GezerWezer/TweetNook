'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { appScripts, pageHtml, pageScripts, readScripts } = require('./page_scripts.cjs');
const {
    ROOT,
    WEB_FIXTURE,
    browserContext,
    dispatchFeed,
    feedListApp,
    immediateComponent,
    loadScripts,
    parseFakeHTML,
    test,
    run,
    pageCss,
} = require('./web_harness.cjs');

test('detail routes parse and generate canonical string tweet IDs', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
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
    const html = pageHtml();
    assert.match(html, /threadData\?\.main\?\.local_quote_count > 0/);
});

test('split-panel view counts tolerate conversation teardown during resize', () => {
    const html = pageHtml();
    for (const attribute of ['x-if', 'x-text']) {
        const expression = html.match(new RegExp(`${attribute}="([^"]*panelThreadData\\?\\.main\\?\\.raw_json\\?\\.views\\?\\.count[^"]*)"`))[1];
        assert.doesNotThrow(() => vm.runInNewContext(expression, {
            panelThreadData: null,
            formatCompactCount: value => String(value || 0),
        }));
    }
});

test('split view needs room for both columns, excluding the viewport scrollbar', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    for (const [viewport, available, expected] of [
        [1280, 1280, true],
        [1200, 1200, true],
        [1199, 1199, false],
        [1100, 1100, false],
        [1024, 1024, false],
        [390, 390, false],
        [1200, 1185, false],
    ]) {
        context.window.innerWidth = viewport;
        context.document.documentElement.clientWidth = available;
        assert.equal(app.shouldUseSplitPanel(), expected, `${available}px available`);
    }
    context.document.documentElement.clientWidth = 1280;
    context.__state.layoutStyles.feed.maxWidth = '700px';
    context.__state.layoutStyles.panel.width = '650px';
    assert.equal(app.shouldUseSplitPanel(), false, 'larger columns need more room');
    context.document.documentElement.clientWidth = 1350;
    assert.equal(app.shouldUseSplitPanel(), true);
    context.__state.layoutStyles.feed.maxWidth = '450px';
    context.__state.layoutStyles.panel.width = '400px';
    context.document.documentElement.clientWidth = 850;
    assert.equal(app.shouldUseSplitPanel(), true, 'smaller columns fit below the old breakpoint');
    app.splitPanel = false;
    assert.equal(app.shouldUseSplitPanel(), false);
});

test('resizing an open split conversation switches layouts before either column clips', async () => {
    for (const viewMode of ['thread', 'quotes']) {
        const context = browserContext();
        context.fetch = async () => ({
            ok: true,
            async json() {
                return viewMode === 'thread'
                    ? { main: { tweet_id: '123' }, parents: [], children: [] }
                    : { tweets: [], total: 0, limit: 20 };
            },
        });
        const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
        const app = immediateComponent(tweetApp());
        app.archiveResourcesLoaded = true;
        app.$refs.detailPanel = { scrollTop: 0 };
        app.listenForNavigation();
        if (viewMode === 'thread') await app.openThread('123');
        else await app.openQuotes('123');
        assert.equal(app.panelMode, viewMode);
        const url = context.window.location.pathname;
        const depth = app.navigationDepth;
        const applyRoute = app.applyRoute.bind(app);
        let transition;
        app.applyRoute = (...args) => (transition = applyRoute(...args));

        for (const width of [1199, 1200, 1100, 1280]) {
            context.window.innerWidth = width;
            await context.window.dispatchEvent({ type: 'resize' });
            await transition;
            const split = width >= 1200;
            assert.equal(app.routeUsesPanel, split);
            assert.equal(app.viewMode, split ? 'list' : viewMode);
            assert.equal(app.panelMode, split ? viewMode : null);
            if (viewMode === 'thread') {
                assert.equal((split ? app.panelThreadData : app.threadData).main.tweet_id, '123');
            } else {
                assert.equal(split ? app.panelQuotesTweetId : app.quotesTweetId, '123');
            }
            assert.equal(context.window.location.pathname, url);
            assert.equal(app.navigationDepth, depth);
            assert.equal(app.splitPanel, true);
        }
        assert.equal(context.localStorage.getItem('tvx-split-panel'), null);
    }
});

test('initialization preserves direct detail paths without adding history entries', () => {
    for (const [pathname, viewMode] of [['/post/123', 'thread'], ['/post/123/quotes', 'quotes']]) {
        const context = browserContext(pathname);
        const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
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
    context.localStorage.setItem('tvx-split-panel', 'false');
    context.fetch = async url => ({
        ok: true,
        async json() {
            if (url.includes('/quotes')) return { tweets: [], total: 0, limit: 20 };
            return { main: { tweet_id: url.split('/').at(-1) }, parents: [], children: [] };
        },
    });
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
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

test('Back and Forward dismiss article focus before restoring routes and scroll in both layouts', async () => {
    for (const splitPanel of [false, true]) {
        const context = browserContext('/post/123');
        const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
        const app = immediateComponent(tweetApp());
        app.fetchSetup = async () => {};
        app.fetchNotices = async () => {};
        app.fetchActivityStatus = async () => {};
        app.initApp();
        app.archiveResourcesLoaded = true;
        app.splitPanel = splitPanel;
        app.$refs.detailPanel = {scrollTop: 0};
        let focusOpen = true;
        let closes = 0;
        context.document.body.style.overflow = 'hidden';
        context.window.TweetNookArticles = {closeFocus(options) {
            assert.equal(options.restoreFocus, false, 'route changes must not refocus the old article');
            focusOpen = false;
            context.document.body.style.overflow = '';
            closes++;
        }};
        const originalScrollTo = context.window.scrollTo;
        context.window.scrollTo = (...args) => {
            assert.equal(focusOpen, false, 'focus cleanup must finish before route scroll restoration');
            originalScrollTo(...args);
        };
        app.showFullThread = async id => {
            assert.equal(focusOpen, false);
            app.viewMode = 'thread';
            app.threadData = {main: {tweet_id: id}};
        };
        app.applyPanelRoute = async route => {
            assert.equal(focusOpen, false);
            app.panelMode = route.viewMode;
            app.panelThreadData = {main: {tweet_id: route.tweetId}};
        };
        await context.window.dispatchEvent({type: 'popstate', state: {viewMode: 'list', scrollY: 321, tweetNookDepth: 0}});
        assert.equal(focusOpen, false);
        assert.equal(context.document.body.style.overflow, '');
        assert.equal(app.viewMode, 'list');
        assert.ok(context.__state.scrollCalls.some(call => call[1] === 321));
        await context.window.dispatchEvent({type: 'popstate', state: {viewMode: 'thread', tweetId: '123', tweetNookDepth: 1}});
        assert.equal(splitPanel ? app.panelThreadData.main.tweet_id : app.threadData.main.tweet_id, '123');
        assert.equal(focusOpen, false, 'Forward must not reopen the dismissed dialog');
        focusOpen = true;
        context.document.body.style.overflow = 'hidden';
        await app.applyRoute({viewMode: 'list'}, {state: {viewMode: 'list', scrollY: 22}});
        assert.equal(focusOpen, false, 'programmatic navigation uses the same cleanup');
        assert.equal(closes, 3);
    }
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
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
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
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
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
        appScripts(),
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    app.archiveResourcesLoaded = true;
    app.$refs.detailPanel = { scrollTop: 99 };
    app.splitPanel = false;

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
        appScripts(),
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
        appScripts(),
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    app.navigationDepth = 1;
    app.goBack();
    assert.equal(video.paused, true);
    assert.equal(video.currentTime, 0);
    assert.equal(context.__state.historyCalls.at(-1)[0], 'back');
});

test('thread reply surfaces render quoted tweets in both presentations', () => {
    const html = pageHtml();

    assert.equal((html.match(/<template x-if="getQuoteTweet\(child\)">/g) || []).length, 2);
    assert.equal((html.match(/<template x-if="getQuoteTweet\(reply\)">/g) || []).length, 2);
    assert.equal((html.match(/renderQuotePlaceholder\(getQuoteTweet\(child\)\)/g) || []).length, 2);
    assert.equal((html.match(/renderQuotePlaceholder\(getQuoteTweet\(reply\)\)/g) || []).length, 2);
    assert.equal((html.match(/formatText\(getQuoteTweet\(child\), isMediaQuotingMedia\(child\)\)/g) || []).length, 2);
    assert.equal((html.match(/formatText\(getQuoteTweet\(reply\), isMediaQuotingMedia\(reply\)\)/g) || []).length, 2);
    assert.equal((html.match(/renderMediaGrid\(child\.qt_media, 'quote', getQuoteTweet\(child\)\)/g) || []).length, 2);
    assert.equal((html.match(/renderMediaGrid\(reply\.qt_media, 'quote', getQuoteTweet\(reply\)\)/g) || []).length, 2);
    assert.match(html, /openThread\(getTweetId\(getQuoteTweet\(child\)\)\)/);
    assert.match(html, /openThread\(getTweetId\(getQuoteTweet\(child\)\), false, true\)/);
    assert.match(html, /openThread\(getTweetId\(getQuoteTweet\(reply\)\)\)/);
    assert.match(html, /openThread\(getTweetId\(getQuoteTweet\(reply\)\), false, true\)/);
});

test('recursive thread replies indent by bounded depth and disclose truncation', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    const html = pageHtml();

    assert.equal(app.nestedReplyIndent({ thread_depth: 2 }), 0);
    assert.equal(app.nestedReplyIndent({ thread_depth: 3 }), 12);
    assert.equal(app.nestedReplyIndent({ thread_depth: 20 }), 72);
    assert.equal(app.nestedReplyIndent({}), 0);
    assert.equal((html.match(/nestedReplyIndent\(reply\)/g) || []).length, 2);
    assert.equal((html.match(/reply_tree_truncated/g) || []).length, 2);
    assert.equal((html.match(/more saved replies than can be shown at once/g) || []).length, 2);
});

test('thread parents render shared quotes with the right layout, interactions, and source data intact', () => {
    const { app } = feedListApp();
    const video = {
        type: 'video',
        duration_millis: 21000,
        download: { local_path: 'media/parent-quote.mp4', thumbnail_local_path: 'media/parent-quote.jpg' },
    };
    const quote = {
        tweet_id: 'quoted-parent',
        text: `A quoted post ${'word '.repeat(80)}`,
        author: { id: 'quote-author', username: 'q', display_name: 'Quote Author' },
        raw_json: {
            in_reply_to_screen_name: 'frank',
            birdwatch_pivot: {
                shorttitle: 'Quoted note',
                subtitle: { text: 'Context from the note', entities: [] },
            },
        },
    };
    const standaloneParent = {
        tweet_id: 'parent-regular', text: 'Parent text', author: { username: 'p' },
        media: [], quoted_tweet: quote, qt_media: [video],
    };
    const mediaParent = {
        ...standaloneParent,
        tweet_id: 'parent-media',
        media: [{ type: 'photo', download: { local_path: 'media/parent.jpg' } }],
    };
    const originalPayloads = JSON.parse(JSON.stringify([standaloneParent, mediaParent]));

    const regular = app.renderQuoteCard(standaloneParent);
    assert.match(regular, /class="quote-card[^\"]*"[^>]*data-thread-id="quoted-parent" data-action="quote"/);
    assert.doesNotMatch(regular, /quote-compact|quote-thumbnail/);
    assert.match(regular, /media-video-player/);
    assert.match(regular, /expand-tweet/);
    assert.match(regular, /community-note-quote/);
    assert.ok(regular.indexOf('class="quote-media"') < regular.indexOf('community-note-quote'));
    const regularRoot = parseFakeHTML(regular)[0];
    assert.equal(regularRoot.querySelector('.quote-media').parentElement, regularRoot);
    assert.equal(regularRoot.querySelector('.quote-reply').parentElement, regularRoot);
    const regularWrapper = parseFakeHTML(`<div>${regular}</div>`)[0];
    app.handleThreadParentQuoteClick({
        target: regularWrapper.querySelector('.expand-tweet'),
        currentTarget: regularWrapper,
        stopPropagation() {},
    }, standaloneParent);
    assert.equal(regularWrapper.querySelector('.expand-tweet'), null);
    assert.equal(regularWrapper.querySelector('.quote-text').querySelectorAll('strong').length, 0);

    const compact = app.renderQuoteCard(mediaParent);
    assert.match(compact, /quote-card[^\"]*quote-compact/);
    assert.match(compact, /class="quote-reply tweet-reply-context[^\"]*"[^>]*>Replying to/);
    assert.match(compact, /class="quote-media"><div class="quote-thumbnail"><img src="\/media\/parent-quote.jpg"/);
    assert.match(compact, /community-note-quote-media/);
    assert.ok(compact.indexOf('class="quote-media"') < compact.indexOf('community-note-quote-media'));
    const compactRoot = parseFakeHTML(compact)[0];
    assert.equal(compactRoot.querySelector('.quote-media').parentElement, compactRoot);
    assert.equal(compactRoot.querySelector('.quote-reply').parentElement, compactRoot);

    // The same data-action delegation used by feed quotes opens a parent quote and
    // routes reply mentions to profile cards.
    let opened;
    app.openThread = id => { opened = id; };
    const wrapper = parseFakeHTML(`<div>${compact}</div>`)[0];
    app.handleThreadParentQuoteClick({
        target: wrapper.querySelector('.quote-thumbnail img'), currentTarget: wrapper, stopPropagation() {},
    }, mediaParent);
    assert.equal(opened, 'quoted-parent');
    let profile;
    app.openUsernameProfileCard = (_event, username) => { profile = username; };
    let stopped = false;
    app.handleThreadParentQuoteClick({
        target: wrapper.querySelector('[data-action="reply-profile"]'), currentTarget: wrapper,
        stopPropagation() { stopped = true; },
    }, mediaParent);
    assert.equal(profile, 'frank');
    assert.equal(stopped, true);

    assert.deepEqual([standaloneParent, mediaParent], originalPayloads);
    const html = pageHtml();
    const parentRenderer = 'x-html="renderQuoteCard(parent)" @click.stop="handleThreadParentQuoteClick($event, parent)" @load.capture="avatarEvent($event)" @error.capture="avatarEvent($event)"';
    assert.equal(html.split(parentRenderer).length - 1, 2);
});

test('a linked conversation opens without waiting for the feed and counters; a list waits for its feed', async () => {
    const setup = { required: false, completed: true, web: {password_configured: true},
        auth: {configured: true, verified: true, values: {auth_token: '', ct0: '', user_id: ''}},
        archive: {ready: true, uploaded: false} };
    const flush = () => new Promise(resolve => setImmediate(resolve));
    const load = route => {
        const context = browserContext();
        context.fetch = async () => ({ ok: true, json: async () => setup });
        const app = immediateComponent(loadScripts(context, appScripts(), 'tweetApp')());
        const pending = {};
        const requested = [];
        for (const method of ['fetchTweets', 'fetchStats', 'fetchArchiveEnrichmentStatus', 'fetchAutomatedTagging']) {
            app[method] = () => new Promise(resolve => { requested.push(method); pending[method] = resolve; });
        }
        const applied = [];
        app.applyRoute = async next => { applied.push(app.routeKey(next)); };
        app.activeRoute = route;
        app.pendingRoute = route;
        app.pendingRouteState = { ...route, tweetNookDepth: 0 };
        const finish = async () => {
            pending.fetchTweets();
            await flush();
            Object.values(pending).forEach(resolve => resolve());
            await setupDone;
        };
        const setupDone = app.fetchSetup();
        return { app, pending, requested, applied, finish };
    };

    const thread = load({ viewMode: 'thread', tweetId: '123' });
    await flush();
    assert.deepEqual(thread.applied, ['thread:123']);
    assert.equal(thread.app.pendingRoute, null);
    assert.deepEqual(thread.requested, ['fetchTweets'], 'counters wait for the feed so they do not slow it');
    await thread.finish();
    assert.deepEqual(thread.requested, ['fetchTweets', 'fetchStats', 'fetchArchiveEnrichmentStatus', 'fetchAutomatedTagging']);
    assert.deepEqual(thread.applied, ['thread:123']);

    const list = load({ viewMode: 'list' });
    await flush();
    assert.deepEqual(list.applied, [], 'the list restores scroll only once its tweets are in');
    await list.finish();
    assert.deepEqual(list.applied, ['list']);

    const moved = load({ viewMode: 'list' });
    await flush();
    moved.app.activeRoute = { viewMode: 'thread', tweetId: '9' };
    await moved.finish();
    assert.deepEqual(moved.applied, [], 'a stale list route never replaces one opened meanwhile');
});

test('large threads render replies in batches of nested tweets', async () => {
    const context = browserContext('/post/1');
    const app = immediateComponent(loadScripts(context, appScripts(), 'tweetApp')());
    const child = (id, nested) => ({
        tweet_id: id,
        op_replies: Array.from({ length: nested }, (_, index) => ({ tweet_id: `${id}-${index}` })),
    });
    const children = [child('a', 20), child('b', 5), child('c', 0), child('d', 40)];

    assert.deepEqual(Array.from(app.visibleReplies(children, 30), item => item.tweet_id), ['a', 'b', 'c']);
    assert.deepEqual(Array.from(app.visibleReplies([child('big', 99)], 30), item => item.tweet_id), ['big']);
    assert.equal(app.hasHiddenReplies(children, 30), true);
    app.showMoreReplies();
    assert.equal(app.hasHiddenReplies(children, app.threadReplyLimit), true);
    app.showMoreReplies();
    assert.equal(app.hasHiddenReplies(children, app.threadReplyLimit), false);
    app.showMoreReplies(true);
    assert.equal(app.panelReplyLimit, 60);

    app.activeRoute = { viewMode: 'thread', tweetId: '1' };
    app.loadThread = async () => ({ main: { tweet_id: '1' }, parents: [], children });
    await app.showFullThread('1');
    assert.equal(app.threadReplyLimit, 30);

    const html = pageHtml();
    assert.ok(html.includes('x-for="child in visibleReplies(threadReplies(threadData, threadOtherReplies), threadReplyLimit)"'));
    assert.ok(html.includes('x-for="child in visibleReplies(threadReplies(panelThreadData, panelOtherReplies), panelReplyLimit)"'));
    assert.ok(html.includes('x-intersect="showMoreReplies()"'));
    assert.ok(html.includes('x-intersect="showMoreReplies(true)"'));
});

test('mention-only replies wait behind Show other replies until regular replies are shown', async () => {
    const context = browserContext('/post/1');
    const app = immediateComponent(loadScripts(context, appScripts(), 'tweetApp')());
    const reply = id => ({ tweet_id: id, op_replies: [] });
    const data = {
        main: { tweet_id: '1' },
        parents: [],
        children: Array.from({ length: 31 }, (_, index) => reply(`r${index}`)),
        other_children: [reply('spam1'), reply('spam2')],
    };
    const ids = list => Array.from(list, item => item.tweet_id);

    assert.deepEqual(ids(app.threadReplies(data, false)), ids(data.children));
    assert.equal(app.firstOtherReplyId(data, false), null);
    assert.equal(app.firstOtherReplyId(data, true), 'spam1');
    assert.equal(app.hasOtherReplies(data, app.threadReplyLimit, app.threadOtherReplies), false);
    app.showMoreReplies();
    assert.equal(app.hasOtherReplies(data, app.threadReplyLimit, app.threadOtherReplies), true);

    app.showOtherReplies();
    assert.equal(app.threadOtherReplies, true);
    assert.equal(app.hasOtherReplies(data, app.threadReplyLimit, app.threadOtherReplies), false);
    assert.deepEqual(
        ids(app.visibleReplies(app.threadReplies(data, app.threadOtherReplies), app.threadReplyLimit)).slice(-2),
        ['spam1', 'spam2'],
    );
    assert.equal(app.hasOtherReplies({ children: [], other_children: [] }, 30, false), false);
    assert.deepEqual(ids(app.threadReplies({ children: [reply('a')] }, true)), ['a']);

    app.activeRoute = { viewMode: 'thread', tweetId: '1' };
    app.loadThread = async () => data;
    await app.showFullThread('1');
    assert.equal(app.threadOtherReplies, false);

    app.showOtherReplies(true);
    assert.equal(app.panelOtherReplies, true);
    assert.equal(app.panelReplyLimit, 60);

    const html = pageHtml();
    assert.ok(html.includes('x-show="hasOtherReplies(threadData, threadReplyLimit, threadOtherReplies)"'));
    assert.ok(html.includes('x-show="hasOtherReplies(panelThreadData, panelReplyLimit, panelOtherReplies)"'));
    assert.equal(html.split('>Show other replies</button>').length - 1, 2);
    assert.equal(html.split('font-extrabold text-[var(--text-primary)]">Other replies</h2>').length - 1, 2);
    assert.ok(html.includes('x-if="child.tweet_id === firstOtherReplyId(threadData, threadOtherReplies)"'));
});

test('threads open on the tweet already shown while the conversation loads', async () => {
    const context = browserContext();
    const pending = [];
    context.fetch = url => new Promise(resolve => pending.push({ url, resolve }));
    const app = immediateComponent(loadScripts(context, appScripts(), 'tweetApp')());
    app.splitPanel = false;
    const feedTweet = {
        tweet_id: '5', text: 'Shown in the feed', author: { id: '1', username: 'a', display_name: 'A' },
        quoted_tweet: { tweet_id: '6', text: 'Quoted', author: { id: '2', username: 'b', display_name: 'B' } },
    };
    app.tweets = [feedTweet];
    app.activeRoute = { viewMode: 'thread', tweetId: '5' };

    const opening = app.showFullThread('5');
    assert.equal(app.loadingThread, false);
    assert.equal(app.threadData.partial, true);
    assert.equal(app.threadData.main.text, 'Shown in the feed');
    assert.equal(pending[0].url, '/api/tweets/5');
    pending[0].resolve({ ok: true, json: async () => ({ main: { tweet_id: '5', text: 'Full' }, parents: [], children: [] }) });
    await opening;
    assert.equal(app.threadData.partial, undefined);
    assert.equal(app.threadData.main.text, 'Full');

    assert.equal(app.threadSeed('6').main.text, 'Quoted');
    assert.equal(app.threadSeed('404'), null);
    app.panelMode = 'thread';
    app.panelStack = [{ routeKey: 'thread:6' }];
    app.panelThreadData = app.threadSeed('6');
    app.savePanelSnapshot();
    assert.equal(app.panelStack[0].data, null);
});

test('thread main anchoring ignores hidden content and preserves visible content on page and panel', () => {
    const context = browserContext();
    const pageScrolls = [];
    context.window.scrollBy = (...args) => pageScrolls.push(args);
    const app = immediateComponent(loadScripts(context, appScripts(), 'tweetApp')());

    for (const panel of [false, true]) {
        let visible = false;
        let top = 0;
        const main = {
            getClientRects: () => visible ? [{}] : [],
            getBoundingClientRect: () => ({ top, width: visible ? 598 : 0, height: visible ? 373 : 0 }),
        };
        const root = { scrollTop: 0, querySelector: () => main };
        if (panel) app.$refs.detailPanel = root;
        else context.document.querySelector = () => main;

        app.keepThreadMainInPlace(panel, () => {
            visible = true;
            top = 69;
        });
        assert.equal(panel ? root.scrollTop : pageScrolls.length, 0, 'newly rendered content must not cause an anchor adjustment');

        top = 100;
        app.keepThreadMainInPlace(panel, () => { top = 160; });
        if (panel) assert.equal(root.scrollTop, 60);
        else assert.deepEqual(pageScrolls, [[0, 60]]);
    }
});

test('pressing or hovering a tweet starts loading its conversation once', () => {
    const context = browserContext();
    const requests = [];
    const timers = [];
    context.fetch = url => {
        requests.push(url);
        return new Promise(() => {});
    };
    context.setTimeout = (callback, delay) => timers.push({ callback, delay });
    context.clearTimeout = () => {};
    const app = immediateComponent(loadScripts(context, appScripts(), 'tweetApp')());
    app.fetchSetup = async () => {};
    app.fetchNotices = async () => {};
    app.fetchActivityStatus = async () => {};
    app.initApp();
    const target = id => ({ target: { closest: selector => (selector === '[data-thread-id]' ? { dataset: { threadId: id } } : null) } });

    context.__state.events.get('document:pointerdown')(target('7'));
    context.__state.events.get('document:mouseover')(target('7'));
    timers.filter(timer => timer.delay === 150).forEach(timer => timer.callback());
    context.__state.events.get('document:pointerdown')(target('not-a-tweet'));

    assert.deepEqual(requests.filter(url => url.startsWith('/api/tweets/')), ['/api/tweets/7']);
    const html = pageHtml();
    assert.equal((html.match(/:data-thread-id="/g) || []).length, (html.match(/="openThread\(/g) || []).length);
    assert.ok(html.includes('data-thread-main="page"') && html.includes('data-thread-main="panel"'));
});

test('the page requests the first feed page and a linked conversation before its scripts load', async () => {
    const html = pageHtml();
    const startup = /<script>([\s\S]*?)<\/script>/.exec(html);
    assert.ok(startup && startup.index < html.indexOf('<link rel="stylesheet"'), 'runs before any stylesheet or script');
    const run = (pathname, demo = false) => {
        const context = browserContext(pathname);
        const requests = [];
        context.fetch = url => { requests.push(url); return new Promise(() => {}); };
        context.window.TWEETNOOK_DEMO = demo;
        vm.runInContext(startup[1], context);
        return { context, requests };
    };
    const { context, requests } = run('/post/42');
    const app = immediateComponent(loadScripts(context, appScripts(), 'tweetApp')());
    assert.deepEqual(requests, [`${app.feedUrl(1)}&cursor=`, '/api/tweets/42'], 'matches the app\'s first page');
    assert.deepEqual(run('/post/42/quotes').requests, [`${app.feedUrl(1)}&cursor=`]);
    assert.deepEqual(run('/', true).requests, [], 'the static demo has no API');

    // The app adopts each early request once instead of asking again.
    app.requestFeedPage(`${app.feedUrl(1)}&cursor=`, { speculative: true });
    app.loadThread('42').catch(() => {});
    assert.equal(requests.length, 2);
    assert.deepEqual(Object.keys(context.window.TWEETNOOK_STARTUP), []);
    app.threadCache.clear();
    app.loadThread('42').catch(() => {});
    assert.equal(requests.at(-1), '/api/tweets/42');
    assert.equal(requests.length, 3);
});

test('media whitespace opens another post in split view while media surfaces retain their actions', () => {
    const { app, list } = feedListApp();
    app.panelStack = [{ tweetId: '999' }];
    app.panelMode = 'thread';
    const opened = [];
    app.openThread = id => opened.push(id);
    const tweet = JSON.parse(JSON.stringify(WEB_FIXTURE.slim[0]));
    app.renderFeedList([tweet]);
    const wrapper = list.querySelector('[data-action="media"]');
    assert.ok(wrapper);
    dispatchFeed(list, 'click', wrapper.querySelector('.media-single') || wrapper);
    assert.deepEqual(opened, [tweet.tweet_id]);
    const image = wrapper.querySelector('img');
    assert.ok(image);
    const event = dispatchFeed(list, 'click', image);
    assert.equal(event.propagationStopped, true);
    assert.deepEqual(opened, [tweet.tweet_id]);
});

test('opening a conversation in the split panel keeps the column width', () => {
    const html = pageHtml();
    const css = pageCss();
    // Swapping border-x for border-l narrowed the column by 1px and re-wrapped the feed.
    const column = html.match(/<div [^>]*class="[^"]*app-column[^"]*"[^>]*>/)[0];
    assert.match(column, /class="border-x [^"]*app-column/);
    assert.doesNotMatch(column, /border-l\b/);
    assert.match(column, /:class="\{ 'app-column-split': splitPanel && panelStack\.length > 0 \}"/);
    assert.match(css, /\.app-column\.app-column-split \{ border-right-color: transparent; \}/);
});

test('a conversation collapses the feed instead of removing it', () => {
    const html = pageHtml();
    const css = pageCss();
    const feed = html.match(/<div id="nook-feed"[^>]*>/)[0];
    // display: none made WebKit lay out every card of a long feed again on return.
    assert.doesNotMatch(feed, /x-show="[^"]*viewMode/);
    // A phone's post layer covers the feed instead, so it stays expanded beneath (pageView).
    assert.match(feed, /:class="\{ 'feed-collapsed': pageView !== 'list' \}"/);
    assert.match(feed, /:aria-hidden="viewMode !== 'list' \? 'true' : null"/);
    assert.match(css, /#nook-feed \{ overflow-y: clip; \}/);
    const collapsed = css.match(/#nook-feed\.feed-collapsed \{([^}]*)\}/)[1];
    assert.match(collapsed, /height: 0;/);
    // Toggling these restyles every card.
    assert.doesNotMatch(collapsed, /visibility|overflow|content-visibility|display/);
});

test('a phone post slides as a layer over the feed, with its own header', () => {
    const html = pageHtml();
    const css = pageCss();
    const stage = html.match(/<div x-ref="detailStage"[^>]*>/)[0];
    assert.match(stage, /'detail-layer': detailLayer, 'is-open': detailLayerOpen/);
    const body = html.slice(html.indexOf(stage), html.indexOf('<!-- /detail-stage -->'));
    // The post and quote views scroll on the layer; its top bar with Back sits
    // outside the scroller, so it can stay put while the post slides.
    const bar = html.slice(html.indexOf('<!-- /detail-stage -->'), html.indexOf('<div x-ignore data-detail-snapshot'));
    assert.match(bar, /<header x-cloak x-show="detailLayer && detailLayerOpen" data-video-occluder class="nook-header detail-layer-header">/);
    assert.match(bar, /@click="goBack\(\)" class="nook-icon-button nook-back" type="button" aria-label="Back"/);
    assert.doesNotMatch(body, /detail-layer-header/);
    assert.match(css, /\.detail-layer-header\.nook-header \{ position: fixed;/);
    assert.match(body, /<!-- THREAD VIEW/);
    assert.match(body, /<!-- QUOTES VIEW/);
    // The feed drifts beneath a post; the page header stays put.
    assert.equal((html.match(/data-feed-stage/g) || []).length, 3);
    assert.match(html, /<header data-video-occluder class="nook-header">/);
    assert.doesNotMatch(html, /data-feed-stage class="nook-tabs-frame"/);
    // Its title and tabs fade through a variable, so tucked-away tabs stay hidden,
    // and the tabs' slide transition is paused while a post moves.
    const header = fs.readFileSync(path.join(ROOT, 'tweetnook/web/static/css/header.css'), 'utf8');
    assert.match(css, /\.nook-header > \.nook-title-row, \.nook-header > \.nook-tabs-frame \{ opacity: var\(--detail-beneath, 1\); \}/);
    assert.match(header, /\.nook-header\.tabs-hidden \.nook-tabs-frame \{[^}]*opacity: 0;/);
    assert.match(css, /\.nook-header\.is-beneath-detail > \.nook-tabs-frame \{ transition: none; \}/);
    assert.match(html, /<div x-ignore data-detail-snapshot class="detail-underlay" aria-hidden="true"><\/div>/);
    assert.match(html, /<div x-ref="detailShade" class="detail-shade" aria-hidden="true"><\/div>/);
    const z = name => Number(css.match(new RegExp(`\\.${name} \\{[^}]*z-index: (\\d+)`))[1]);
    assert.ok(z('detail-underlay') < z('detail-shade') && z('detail-shade') < z('detail-layer') && z('detail-layer') < 100);
    // Vertical scrolling stays native; a drag at either end never scrolls the feed.
    assert.match(css, /\.detail-layer \{[^}]*touch-action: pan-y;/);
    assert.match(css, /\.detail-layer > \.detail-body \{ min-height: calc\(100% \+ 1px\); \}/);
    assert.match(css, /overscroll-behavior: contain/);
});

test('the post layer moves as X does: spring in, follow the finger, spring back or leave', () => {
    const context = browserContext();
    const { swipeBackIntent, swipeBackCompletes, detailSettle, detailTransitionStyles, detailBarOpacities } = loadScripts(context, appScripts(),
        '({swipeBackIntent, swipeBackCompletes, detailSettle, detailTransitionStyles, detailBarOpacities})');
    // Undecided until 10px; rightward and mostly sideways becomes a back swipe.
    assert.equal(swipeBackIntent(6, 2), null);
    assert.equal(swipeBackIntent(14, 4), true);
    assert.equal(swipeBackIntent(-14, 2), false);
    assert.equal(swipeBackIntent(10, 12), false);
    // X springs a post let go 56% across back and lets one at 63% go; flicks decide by direction.
    assert.equal(swipeBackCompletes(0.56 * 430, 430, 0), false);
    assert.equal(swipeBackCompletes(0.63 * 430, 430, 0), true);
    assert.equal(swipeBackCompletes(40, 430, 0.8), true);
    assert.equal(swipeBackCompletes(380, 430, -0.8), false);
    // Opening, sampled at 60Hz, against X frame by frame (430pt screen, 196/116/65/35/19).
    const opening = [37, 53, 70, 87, 103].map(t => detailSettle(430, 0, 0, t, false, 430).offset);
    [196, 116, 65, 35, 19].forEach((x, i) => assert.ok(Math.abs(opening[i] - x) < 20, `${opening[i]} vs X ${x}`));
    assert.equal(detailSettle(430, 0, 0, 200, false, 430).done, true);
    // Springing back from a held drag lands in seven frames (X: six).
    assert.equal(detailSettle(177, 0, 0, 100, false, 430).done, false);
    assert.equal(detailSettle(177, 0, 0, 117, false, 430).done, true);
    // A flick leaves at the finger's speed and stops exactly at the edge (X: 63 -> 100 -> 147).
    const flick = [17, 33].map(t => detailSettle(63, 430, 37 / 16.7, t, true, 430).offset);
    assert.ok(Math.abs(flick[0] - 100) < 15 && Math.abs(flick[1] - 147) < 15, flick.join());
    assert.deepEqual({ ...detailSettle(63, 430, 37 / 16.7, 1000, true, 430) }, { offset: 430, done: true });
    // What it covers drifts 30% of the post's travel under a shade reaching 40% black.
    assert.deepEqual({ ...detailTransitionStyles(0, 430) }, { layer: 'translate3d(0px, 0, 0)', under: 'translate3d(-129px, 0, 0)', shade: '0.4' });
    // Top bars stay put: pulled back, the post's is gone by a third of the way and the
    // feed's appears only near the end; an opening post's bar shows at once.
    const bars = pulled => ({ ...detailBarOpacities(pulled * 430, 430, false) });
    assert.deepEqual(bars(0), { top: 1, beneath: 0 });
    assert.ok(bars(0.09).top > 0.4 && bars(0.09).top < 0.6);
    assert.deepEqual(bars(0.5), { top: 0, beneath: 0 });
    assert.deepEqual(bars(0.8), { top: 0, beneath: 1 });
    assert.deepEqual({ ...detailBarOpacities(300, 430, true) }, { top: 1, beneath: 0 });
    assert.deepEqual({ ...detailTransitionStyles(430, 430) }, { layer: 'translate3d(430px, 0, 0)', under: 'translate3d(0px, 0, 0)', shade: '0' });
});

test('the feed scroll indicator is never hidden or rebuilt around a post layer', () => {
    // iOS never shows the page's indicator again once scrollbar-width or
    // ::-webkit-scrollbar hides it, and an overflow change to rebuild it made the
    // feed jump; the brief indicator over a post opened mid-scroll is the lesser cost.
    const css = pageCss();
    const js = readScripts(pageScripts());
    assert.doesNotMatch(css, /detail-covering/);
    assert.doesNotMatch(js, /detail-covering|overflowY = 'scroll'/);
});

test('the phone post page spaces its parts and replies like X and shows the whole post', () => {
    const css = pageCss();
    const html = pageHtml();
    assert.match(css, /--detail-bar: 38px;/);
    assert.doesNotMatch(css, /\.detail-layer-header \.nook-title-row \{[^}]*box-shadow/);
    assert.match(css, /\.detail-layer-header \.nook-title-block \{[^}]*justify-content: center/);
    const phone = css.slice(css.indexOf('@media (max-width: 640px)'));
    assert.match(phone, /\[data-thread-main\]\.thread-row \{\s*padding-top: 13px;/);
    assert.match(phone, /\[data-thread-main\] > div > \.mt-3 \{\s*margin-top: 22px;/);
    assert.match(phone, /\[data-thread-main\] > \.tweet-text-detail:empty \{\s*display: none;/);
    assert.match(phone, /\[data-thread-main\] \.tweet-timestamp \{\s*padding: 22px 0 6px;/);
    assert.match(phone, /\.tweet-actions-main \{\s*height: 38px;/);
    assert.match(phone, /\.thread-row:not\(\[data-thread-main\]\) > \.flex-1 \{\s*margin-top: -3\.5px;/);
    assert.match(phone, /\.thread-row:not\(\[data-thread-main\]\) \.tweet-actions \{\s*margin-top: 7px;/);
    assert.ok(html.includes('x-html="formatText(threadData.main, true)"'));
    assert.ok(html.includes('x-html="formatText(panelThreadData.main, true)"'));
});

test('a short reply page sizes its spacer before retrying the scroll to the post', () => {
    const context = browserContext();
    const app = loadScripts(context, appScripts(), 'tweetApp')();
    const spacer = {style: {}};
    let scrollTop = 0;
    const stage = {
        querySelector: selector => (selector === '.thread-bottom-space' ? spacer : null),
        get scrollTop() { return scrollTop; },
        // The page can scroll only as far as the spacer's applied height allows.
        set scrollTop(value) { scrollTop = Math.min(value, 378 + (parseInt(spacer.style.height, 10) || 0)); },
    };
    app.$refs = {detailStage: stage};
    app.detailLayer = true;
    app.detailScrollY = () => scrollTop;
    const ticks = [];
    app.$nextTick = fn => ticks.push(fn);
    context.window.innerHeight = 873;
    app.threadBottomSpace = 0;
    app.scrollPageTo(558);
    assert.equal(spacer.style.height, '180px');
    while (ticks.length) ticks.shift()();
    assert.equal(scrollTop, 558);
});

test('the page header and feed stay on the list beneath a post layer', () => {
    const context = browserContext();
    const app = loadScripts(context, appScripts(), 'tweetApp')();
    app.viewMode = 'thread';
    assert.equal(app.pageView, 'thread');
    app.detailLayer = true;
    assert.equal(app.pageView, 'list');
});

run();
