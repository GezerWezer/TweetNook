'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { appScripts, pageHtml } = require('./page_scripts.cjs');
const {
    ROOT,
    WEB_FIXTURE,
    browserContext,
    dispatchFeed,
    feedListApp,
    immediateComponent,
    loadScripts,
    test,
    run,
} = require('./web_harness.cjs');

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
        appScripts(),
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
    assert.equal(app.resultCountLabel(), 'Counting… Results');
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

test('text counts arrive independently, survive append, and ignore an older search', async () => {
    const context = browserContext();
    const counts = [];
    context.fetch = async (url, options) => {
        if (url.startsWith('/api/tweets/count?')) {
            return new Promise(resolve => counts.push({url, options, resolve}));
        }
        return {ok: true, status: 200, json: async () => ({
            tweets: [{tweet_id: url.includes('page=2') ? '2' : '1'}],
            page: url.includes('page=2') ? 2 : 1, total: null, pages: null, has_more: true,
        })};
    };
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.searchQuery = 'gif is:reply';
    await app.fetchTweets();
    assert.equal(app.loading, false);
    assert.equal(app.tweets.length, 1);
    assert.equal(app.total, null);
    assert.equal(counts[0].url, '/api/tweets/count?collection=likes&q=gif%20is%3Areply');
    counts[0].resolve({ok: true, json: async () => ({total: 42})});
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(app.resultCountLabel(), '42 Results');
    await app.fetchTweets(true);
    assert.equal(app.total, 42);
    assert.equal(counts.length, 1);

    await app.fetchTweets();
    app.searchQuery = 'cats';
    await app.fetchTweets();
    assert.equal(counts[1].options.signal.aborted, true);
    counts[2].resolve({ok: true, json: async () => ({total: 8})});
    counts[1].resolve({ok: true, json: async () => ({total: 99})});
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(app.total, 8);

    await app.fetchTweets();
    counts[3].resolve({ok: false, json: async () => ({detail: 'failed'})});
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(app.resultCountLabel(), 'Count unavailable · Results');
    assert.equal(app.tweets.length, 1);
});

test('a complete first text page supplies its own count, including zero', async () => {
    const context = browserContext();
    let tweets = [{tweet_id: '1'}];
    const calls = [];
    context.fetch = async url => {
        calls.push(url);
        return {ok: true, status: 200, json: async () => ({tweets, page: 1, total: null, pages: null, has_more: false})};
    };
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.searchQuery = 'gif is:reply';
    await app.fetchTweets();
    assert.equal(app.resultCountLabel(), '1 Results');
    tweets = [];
    await app.fetchTweets();
    assert.equal(app.resultCountLabel(), '0 Results');
    assert.equal(calls.length, 2);
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
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
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

test('feed ignores stale responses and pages without a cursor', async () => {
    const context = browserContext();
    const pending = [];
    context.fetch = url => new Promise(resolve => pending.push({url, resolve}));
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    const first = app.fetchTweets();
    app.searchQuery = 'new query';
    const second = app.fetchTweets();
    const resolve = (index, data) => pending[index].resolve({ok: true, status: 200, json: async () => data});
    resolve(0, {tweets: [{tweet_id: 'old'}], page: 1, pages: 1, total: 1, has_more: false});
    await first;
    assert.equal(app.loading, true);
    assert.equal(app.tweets.length, 0);
    resolve(1, {tweets: [{tweet_id: 'new'}], page: 1, pages: 2, total: 2, has_more: true});
    await second;
    assert.equal(app.loading, false);
    assert.equal(app.hasMore, true);
    assert.deepEqual(Array.from(app.tweets, tweet => tweet.tweet_id), ['new']);
    const more = app.loadMore();
    assert.match(pending[2].url, /page=2/);
    assert.doesNotMatch(pending[2].url, /cursor=/);
    resolve(2, {tweets: [{tweet_id: 'next'}], page: 2, pages: 2, total: 2, has_more: false});
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
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
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
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
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
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
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
    const html = pageHtml();
    assert.match(html, /x-for="option in feedOptions\('collection'\)"/);
    assert.match(html, /x-for="\(option, index\) in feedOptions\('sort'\)"/);
});

test('feed menus retain null focus transitions and close when focus moves outside', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    const html = pageHtml();
    const expression = html.match(/@focusout="([^"]+)"/)[1];
    const handleFocusOut = new Function('$el', '$event', 'closeFeedMenu', expression);
    const inside = {};
    const outside = {};
    const container = { contains: target => target === inside };
    app.$refs = { feedMenus: { querySelectorAll: () => [] } };
    for (const kind of ['collection', 'sort']) {
        app.openFeedMenu(kind, { focus() {} });
        for (const relatedTarget of [null, inside]) {
            handleFocusOut(container, { relatedTarget }, () => app.closeFeedMenu());
            assert.equal(app.feedMenu, kind);
        }
        handleFocusOut(container, { relatedTarget: outside }, () => app.closeFeedMenu());
        assert.equal(app.feedMenu, null);
    }
});

test('list searching, incremental loading, and back-to-top obey state guards', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(
        context,
        appScripts(),
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

    const html = pageHtml();
    assert.match(html, /x-show="!loading && hasMore"/);

    app.scrollToTop();
    const scrollOptions = context.__state.scrollCalls.at(-1)[0];
    assert.equal(scrollOptions.top, 0);
    assert.equal(scrollOptions.behavior, 'smooth');

    context.window.matchMedia = query => ({ matches: query === '(prefers-reduced-motion: reduce)' });
    app.scrollToTop();
    const reducedScroll = context.__state.scrollCalls.at(-1)[0];
    assert.equal(reducedScroll.top, 0);
    assert.equal(reducedScroll.behavior, 'instant');
});

test('feeds reuse recent first pages and keep the old list while loading', async () => {
    const context = browserContext();
    const requests = [];
    const pending = new Map();
    context.fetch = url => {
        requests.push(url);
        return new Promise(resolve => pending.set(url, data => resolve({ ok: true, status: 200, json: async () => data })));
    };
    let idle = null;
    context.requestIdleCallback = callback => { idle = callback; };
    const app = immediateComponent(loadScripts(context, appScripts(), 'tweetApp')());
    const page = ids => ({ tweets: ids.map(tweet_id => ({ tweet_id })), page: 1, pages: 1, total: ids.length, has_more: false });
    const likes = '/api/tweets?collection=likes&sort=liked_latest&page=1&cursor=';
    const all = '/api/tweets?collection=all&sort=newest&page=1&cursor=';

    // A startup request made alongside setup is reused by the first load.
    app.requestFeedPage(likes, { speculative: true });
    const first = app.fetchTweets();
    pending.get(likes)(page(['L1']));
    await first;
    assert.deepEqual(requests, [likes]);
    assert.deepEqual(Array.from(app.tweets, tweet => tweet.tweet_id), ['L1']);

    // Idle time fetches the other collections' first pages once.
    idle();
    assert.deepEqual(requests.slice(1), [all,
        '/api/tweets?collection=bookmarks&sort=newest&page=1&cursor=',
        '/api/tweets?collection=tweets&sort=newest&page=1&cursor=']);

    app.collectionFilter = 'all';
    app.sortOrder = 'newest';
    const switching = app.fetchTweets();
    assert.equal(app.loading, true);
    assert.deepEqual(Array.from(app.tweets, tweet => tweet.tweet_id), ['L1']);
    pending.get(all)(page(['A1']));
    await switching;
    assert.deepEqual(Array.from(app.tweets, tweet => tweet.tweet_id), ['A1']);

    // Returning to a recent list renders it at once, without a request or loading state.
    app.collectionFilter = 'likes';
    app.sortOrder = 'liked_latest';
    const back = app.fetchTweets();
    assert.equal(app.loading, false);
    await back;
    assert.equal(requests.length, 4);
    assert.deepEqual(Array.from(app.tweets, tweet => tweet.tweet_id), ['L1']);

    // A finished job invalidates cached lists.
    app.activity = { run_id: 'sync' };
    context.fetch = async url => {
        requests.push(url);
        return { ok: true, status: 200, json: async () => (url.includes('activity') ? { active: false } : page(['A2'])) };
    };
    app.scheduleActivityPoll = () => {};
    await app.fetchActivityStatus(true);
    app.collectionFilter = 'all';
    app.sortOrder = 'newest';
    await app.fetchTweets();
    assert.equal(requests.at(-1), all);
    assert.deepEqual(Array.from(app.tweets, tweet => tweet.tweet_id), ['A2']);
});

test('feed layout hooks load early and skip off-screen work', () => {
    const html = pageHtml();
    const css = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'static', 'css', 'styles.css'), 'utf8');
    assert.ok(html.includes('x-ref="feedSentinel" x-intersect.margin.1200px="loadMore()"'));
    assert.equal((html.match(/x-intersect\.margin\.600px="if\(!(panelQ|q)uotesLoadingMore/g) || []).length, 2);
    assert.ok(html.includes(`<div x-ref="feedList" :class="{ 'feed-refreshing': loading && tweets.length > 0 }"></div>`));
    // The shared menu sits outside the cards, so every card can skip off-screen work.
    assert.match(css, /\n\.feed-card \{\s*content-visibility: auto;/);
    assert.doesNotMatch(css + html, /feed-card-active/);
});

test('status polling pauses in hidden tabs and refreshes on return', () => {
    const context = browserContext();
    const intervals = [];
    context.setInterval = (callback, delay) => intervals.push({ callback, delay });
    context.document.hidden = true;
    const app = immediateComponent(loadScripts(context, appScripts(), 'tweetApp')());
    let polls = 0;
    app.fetchSetup = async () => { polls++; };
    app.fetchNotices = async () => {};
    app.fetchActivityStatus = async () => {};
    app.archiveResourcesLoaded = true;
    app.fetchArchiveEnrichmentStatus = () => { polls += 100; };
    // The harness keeps one listener per event; the activity poller has its own.
    app.startActivityPolling = () => {};
    app.initApp();
    const before = polls;
    intervals.filter(interval => [3000, 60000].includes(interval.delay)).forEach(interval => interval.callback());
    assert.equal(polls, before);
    context.document.hidden = false;
    context.__state.events.get('document:visibilitychange')();
    assert.equal(polls, before + 1);
});

test('feed list replaces pages, appends only new tweets, and ignores stale responses', async () => {
    const context = browserContext();
    const pending = [];
    context.fetch = url => new Promise(resolve => pending.push({ url, resolve }));
    const { app, list } = feedListApp(context);
    const tweet = id => ({ tweet_id: id, text: `Tweet ${id}`, author: { id: '1', username: 'a', display_name: 'A' } });
    const respond = (index, data) => pending[index].resolve({ ok: true, status: 200, json: async () => data });
    const ids = () => list.children.map(card => card.dataset.tweetId);

    const first = app.fetchTweets();
    respond(0, { tweets: [tweet('1'), tweet('2')], page: 1, pages: 2, total: 4, has_more: true, next_cursor: 'c' });
    await first;
    assert.deepEqual(ids(), ['1', '2']);
    const firstCard = list.children[0];

    const more = app.loadMore();
    respond(1, { tweets: [tweet('2'), tweet('3')], page: 2, pages: 2, total: 4, has_more: false });
    await more;
    assert.deepEqual(ids(), ['1', '2', '3']);
    assert.equal(list.children[0], firstCard, 'appending keeps existing cards');
    assert.deepEqual(Array.from(app.tweets, item => item.tweet_id), ids());

    // A superseded request renders nothing; the newer list replaces the old one.
    const stale = app.fetchTweets();
    app.searchQuery = 'new';
    const fresh = app.fetchTweets();
    assert.deepEqual(ids(), ['1', '2', '3'], 'the previous list stays while loading');
    respond(2, { tweets: [tweet('old')], page: 1, pages: 1, total: 1, has_more: false });
    await stale;
    assert.deepEqual(ids(), ['1', '2', '3']);
    respond(3, { tweets: [tweet('9')], page: 1, pages: 1, total: 1, has_more: false });
    await fresh;
    assert.deepEqual(ids(), ['9']);

    // A failed list clears the cards along with the data.
    context.fetch = async () => ({ ok: false, status: 500, json: async () => ({ detail: 'down' }) });
    app.searchQuery = 'broken';
    await app.fetchTweets();
    assert.equal(app.error, 'down');
    assert.deepEqual(ids(), []);
});

test('profile search replaces feed cards after its Alpine template closes', async () => {
    const context = browserContext();
    const pending = [];
    context.fetch = url => new Promise(resolve => pending.push({ url, resolve }));
    const { app, list } = feedListApp(context);
    const tweet = (id, username) => ({
        tweet_id: id, text: `Tweet ${id}`,
        author: { id: username, username, display_name: username },
    });
    const original = [tweet('1', 'alice'), tweet('2', 'bob')];
    app.tweets = original;
    app.renderFeedList(original);
    app.profileCard = { username: 'bob' };
    let searched;
    const fetchTweets = app.fetchTweets;
    app.fetchTweets = () => (searched = fetchTweets.call(app));

    app.searchFrom(app.profileCard.username);
    app.closeProfileCard();
    // Alpine resolves magic refs through the initiating element. Removing the
    // profile's x-if template leaves the async search with an empty refs scope.
    app.$refs = {};
    assert.match(pending[0].url, /q=from%3Abob/);
    pending[0].resolve({ ok: true, status: 200, json: async () => ({
        tweets: [tweet('2', 'bob')], page: 1, pages: 1, total: 1, has_more: false,
    }) });
    await searched;

    assert.equal(app.profileCard, null);
    assert.equal(app.searchQuery, 'from:bob');
    assert.equal(app.total, 1);
    assert.deepEqual(list.children.map(card => card.dataset.tweetId), ['2']);
    assert.deepEqual(Array.from(app.tweets, item => item.tweet_id), ['2']);
});

test('feed clicks are delegated by data-action like the per-card handlers', () => {
    const { app, list } = feedListApp();
    const opened = [];
    const profiles = [];
    app.openThread = id => opened.push(id);
    app.openProfileCard = (event, tweet, isQt, useClickedAnchor) => profiles.push({ anchor: event.currentTarget, tweet, useClickedAnchor });
    app.tweets = WEB_FIXTURE.slim.map(tweet => JSON.parse(JSON.stringify(tweet)));
    app.tweets.splice(1, 2);  // one copy of each shape; list ids must be unique
    app.renderFeedList(app.tweets);
    const [quoted, retweet, legacy, tombstoneQuote] = list.children;
    const click = target => dispatchFeed(list, 'click', target);

    // The card body opens the displayed tweet and lets the click reach the document.
    let event = click(quoted.querySelector('[data-action="text"]'));
    assert.deepEqual(opened, ['100']);
    assert.equal(event.propagationStopped, false);
    click(retweet.querySelector('.tweet-byline-detail'));
    assert.equal(opened.at(-1), '300');
    click(quoted.querySelector('.group svg'));  // action bar icons open the thread as before
    assert.equal(opened.at(-1), '100');

    // The quote opens the quoted thread, including from its text.
    event = click(quoted.querySelector('[data-action="quote"] .tweet-byline-name'));
    assert.equal(opened.at(-1), '200');
    assert.equal(event.propagationStopped, true);
    click(legacy.querySelector('[data-action="quote"] [data-action="text"]'));
    assert.equal(opened.at(-1), '401');
    const openedCount = opened.length;

    // Links, media, link cards, notes, dates, and tombstone quotes never open a thread.
    for (const target of [
        quoted.querySelector('[data-action="text"] a'),
        quoted.querySelector('a.mt-3 div'),
        quoted.querySelector('img.cursor-pointer'),
        quoted.querySelector('.roll-item'),
        quoted.querySelector('[data-action="quote"] .community-note-quote span'),
        tombstoneQuote.querySelector('.tweet-skeleton-message'),
        retweet.querySelector('[data-action="stop"] .truncate'),
        quoted.querySelector('a[title="Open on Twitter/X"] svg'),
    ]) {
        assert.ok(target, 'target exists');
        event = click(target);
        assert.equal(event.propagationStopped, true);
    }
    assert.equal(opened.length, openedCount);

    // Avatar and name open the profile card for the displayed tweet.
    event = click(retweet.querySelector('.avatar-initial'));
    assert.equal(profiles.at(-1).anchor, retweet.querySelector('.avatar'));
    assert.equal(profiles.at(-1).tweet.author.username, 'erin');
    assert.equal(event.propagationStopped, true);
    click(quoted.querySelector('.tweet-byline-name'));
    assert.equal(profiles.at(-1).anchor, quoted.querySelector('.tweet-byline-name'));
    assert.equal(profiles.at(-1).tweet.tweet_id, '100');

    // Reply targets anchor the card at the clicked name.
    click(legacy.querySelector('[data-action="reply-profile"]'));
    assert.equal(profiles.at(-1).anchor, legacy.querySelector('[data-action="reply-profile"]'));
    assert.equal(profiles.at(-1).tweet.author.username, 'frank');
    assert.equal(profiles.at(-1).useClickedAnchor, true);

    // Mentions anchor at the card avatar through the text element; hashtags search.
    const textElement = quoted.querySelector('[data-action="text"]');
    event = click(quoted.querySelector('.mention'));
    assert.equal(profiles.at(-1).anchor, textElement);
    assert.equal(profiles.at(-1).tweet.author.username, 'bob');
    assert.equal(event.propagationStopped, true);
    let searched = null;
    app.search = () => { searched = app.searchQuery; };
    click(quoted.querySelector('.hashtag'));
    assert.equal(searched, '#tag');
    assert.equal(opened.length, openedCount);
});

test('committed collection stages refresh an empty feed and its count while media still runs', async () => {
    const context = browserContext();
    let committed = false, feeds = 0;
    context.fetch = async url => {
        if (url === '/api/activity/status') return {ok: true, json: async () => ({
            active: true, snapshot: {run_id: 'first-sync', steps: [
                {key: 'sync:bookmarks:head', state: 'complete'},
                {key: 'media', state: 'active'},
            ]},
        })};
        assert.match(url, /^\/api\/tweets\?/);
        feeds++;
        return {ok: true, json: async () => ({
            tweets: committed ? [{tweet_id: 'saved'}] : [], total: committed ? 227 : 0,
            page: 1, pages: 1, has_more: false,
        })};
    };
    const {tweetApp} = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.archiveReady = true;
    app.collectionFilter = 'bookmarks';
    app.renderFeedList = () => {};
    app.scheduleFeedPrefetch = () => {};
    await app.fetchTweets();
    assert.equal(app.total, 0);
    app.activity = {run_id: 'first-sync', steps: [{key: 'sync:bookmarks:head', state: 'active'}]};
    committed = true;
    await app.fetchActivityStatus();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(app.total, 227);
    assert.equal(app.tweets[0].tweet_id, 'saved');
    assert.equal(app.activity.steps[1].state, 'active');
    assert.equal(app.feedUpdateAvailable, false);
    assert.equal(feeds, 2);
    await app.fetchActivityStatus();
    assert.equal(feeds, 2); // Identical polls do not keep refreshing the feed.
});

run();
