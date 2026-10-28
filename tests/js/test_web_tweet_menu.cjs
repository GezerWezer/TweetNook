'use strict';

const assert = require('node:assert/strict');
const { appScripts, pageHtml } = require('./page_scripts.cjs');
const {
    WEB_FIXTURE,
    browserContext,
    dispatchFeed,
    feedListApp,
    immediateComponent,
    loadScripts,
    mountTagDialog,
    test,
    run,
} = require('./web_harness.cjs');

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
        appScripts(),
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    app.tweets = [{ tweet_id: '1', media_tags: { description: 'Old description', tags: ['Old'] } }];
    app.panelThreadData = {
        main: { tweet_id: '1', media_tags: { description: 'Old description', tags: ['Old'] } },
        parents: [],
        children: [],
    };
    const dialog = mountTagDialog(context, app);
    dialog.tagModalTweetId = '1';
    dialog.tagModalData = app.tweets[0].media_tags;
    dialog.editableTags = ['Night'];
    dialog.editableDescription = '  A moonlit view  ';
    dialog.addEditableTag(' night ');
    dialog.addEditableTag('Sky');
    assert.deepEqual(Array.from(dialog.editableTags), ['Night', 'Sky']);

    await dialog.saveTags();
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
        appScripts(),
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    const dialog = mountTagDialog(context, app);

    app.toggleTweetMenu('list:2');
    assert.equal(app.tweetMenuOpen, 'list:2');
    await app.copyRawTweet({ raw_json: { rest_id: '2', legacy: { full_text: 'Hello' } } });
    assert.equal(app.tweetMenuOpen, null);
    assert.equal(copiedText, '{\n  "rest_id": "2",\n  "legacy": {\n    "full_text": "Hello"\n  }\n}');

    app.toggleTweetMenu('list:2');
    app.openTagsFromTweetMenu('2', null);
    assert.equal(app.tweetMenuOpen, null);
    assert.equal(dialog.tagModalOpen, true);
    // Nothing saved yet, so the dialog opens in the editor.
    assert.equal(dialog.isEditingTags, true);
    assert.deepEqual(Array.from(dialog.tagModalData.tags), []);
    assert.equal(dialog.tagModalData.description, '');
    assert.deepEqual(Array.from(dialog.editableTags), []);

    app.startTweetMenuPress({ pointerType: 'touch', button: 0 }, '3', {
        description: 'Existing',
        tags: ['Bird'],
    });
    assert.equal(app.tweetMenuPressTimer, 41);
    pressCallback();
    assert.equal(dialog.tagModalTweetId, '3');
    assert.equal(dialog.tagModalData.description, 'Existing');
    assert.equal(dialog.isEditingTags, false);
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
        appScripts(),
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());

    await app.copyRawTweet({ raw_json: { rest_id: '3', legacy: { full_text: 'Fallback' } } });
    assert.equal(copiedText, '{\n  "rest_id": "3",\n  "legacy": {\n    "full_text": "Fallback"\n  }\n}');
});

test('tweet overflow menus are wired on list and detail surfaces', () => {
    const html = pageHtml();

    // Detail surfaces keep per-tweet menus; feed cards render their button in app.js
    // and share one Alpine menu.
    assert.equal((html.match(/aria-label="More actions"/g) || []).length, 2);
    assert.match(html, /copyRawTweet\(listMenu\.tweet\)/);
    assert.match(html, /copyRawTweet\(threadData\.main\)/);
    assert.match(html, /copyRawTweet\(panelThreadData\.main\)/);
    assert.match(html, /setTweetHidden\(listMenu\.key\.slice\('list:'\.length\), !listMenu\.hidden\)/);
    assert.match(html, /setTweetHidden\(threadData\.main\.tweet_id, !threadData\.main\.hidden\)/);
    assert.match(html, /setTweetHidden\(panelThreadData\.main\.tweet_id, !panelThreadData\.main\.hidden\)/);
    assert.match(html, /startTweetMenuPress\(\$event, threadData\.main\.tweet_id/);
    assert.match(html, /startTweetMenuPress\(\$event, panelThreadData\.main\.tweet_id/);
    assert.equal((html.match(/renderQuotePlaceholder\(getQuoteTweet/g) || []).length, 6);
    assert.match(html, /x-model="editableDescription"/);
    const { app } = feedListApp();
    const card = app.renderFeedCard({ tweet_id: '1', text: 'x', author: { id: '2', username: 'a', display_name: 'A' } });
    assert.match(card, /aria-label="More actions" aria-haspopup="menu" aria-expanded="false" title="More actions \(hold for Tags\)" data-action="menu"/);
});

test('copy raw prefers the stored document prefetched when the menu opens', async () => {
    const context = browserContext();
    const requests = [];
    let copiedText = null;
    context.fetch = async url => {
        requests.push(url);
        return { ok: true, json: async () => ({ raw_json: { rest_id: '7', full: true } }) };
    };
    context.navigator = { clipboard: { async writeText(value) { copiedText = value; } } };
    const app = immediateComponent(loadScripts(context, appScripts(), 'tweetApp')());

    app.toggleTweetMenu('list:7');
    await app.fullRawTweet({ tweet_id: '7' });
    await app.copyRawTweet({ tweet_id: '7', raw_json: { rest_id: '7' } });

    assert.deepEqual(requests, ['/api/tweets/7/raw']);
    assert.equal(JSON.parse(copiedText).full, true);

    context.fetch = async () => ({ ok: false, json: async () => ({}) });
    await app.copyRawTweet({ tweet_id: '8', raw_json: { rest_id: '8', abbreviated: true } });
    assert.equal(JSON.parse(copiedText).abbreviated, true);
});

test('the full tag list loads only when Tag Management opens', () => {
    const context = browserContext();
    const app = immediateComponent(loadScripts(context, appScripts(), 'tweetApp')());
    const watchers = {};
    let loads = 0;
    app.$watch = (key, callback) => { watchers[key] = callback; };
    for (const method of ['fetchSetup', 'fetchNotices', 'fetchActivityStatus', 'fetchConfig', 'fetchScheduleSettings', 'fetchActivityRuns', 'fetchAutomatedTagging']) {
        app[method] = async () => {};
    }
    app.fetchGlobalTags = async () => { loads++; };
    app.initApp();

    watchers.settingsTab('tags');
    assert.equal(loads, 0);
    app.showSettingsModal = true;
    watchers.settingsTab('appearance');
    watchers.settingsTab('tags');
    assert.equal(loads, 1);
    app.settingsTab = 'tags';
    watchers.showSettingsModal(true);
    assert.equal(loads, 2);
});

test('feed Show more expands text in place and tag edits keep the card', () => {
    const { app, list } = feedListApp();
    app.tweets = [JSON.parse(JSON.stringify(WEB_FIXTURE.slim[0])), {
        tweet_id: '700', text: 'Quoting a long tweet',
        author: { id: '1', username: 'q', display_name: 'Q' },
        quoted_tweet: { tweet_id: '100', text: 'long '.repeat(80), author: { id: '10', username: 'alice', display_name: 'Alice' } },
    }];
    app.renderFeedList(app.tweets);
    const [card, quoting] = list.children;
    const text = card.querySelector('[data-action="text"]');
    const quotedText = quoting.querySelector('[data-action="quote"] [data-action="text"]');
    const media = card.querySelector('img.cursor-pointer');
    const [shortText, shortQuote] = [text.textContent.length, quotedText.textContent.length];
    assert.ok(text.querySelector('.expand-tweet'));
    assert.ok(quotedText.querySelector('.expand-tweet'));

    const event = dispatchFeed(list, 'click', text.querySelector('.expand-tweet'));
    assert.equal(event.propagationStopped, true);
    assert.equal(app.expandedTweets['100'], true);
    assert.equal(list.children[0], card, 'the card and its media stay in place');
    assert.equal(card.querySelector('img.cursor-pointer'), media);
    assert.equal(card.querySelector('[data-action="text"]'), text);
    assert.equal(text.querySelector('.expand-tweet'), null);
    assert.ok(text.textContent.length > shortText);
    // Every copy of the tweet in the list expands, as the reactive template did.
    assert.equal(quotedText.querySelector('.expand-tweet'), null);
    assert.ok(quotedText.textContent.length > shortQuote);

    // Cards do not display tags: an edit updates the data the menu and long press
    // read, and leaves the card (and any playing media) in place.
    app.updateTweetTagData('100', { description: 'Edited', tags: ['Night'] });
    assert.equal(list.children[0], card);
    assert.equal(card.querySelector('img.cursor-pointer'), media);
    assert.equal(app.tweets[0].media_tags.description, 'Edited');
    assert.equal(app.feedCardTweet(card.querySelector('[data-action="menu"]')).media_tags.description, 'Edited');
    assert.equal(list.children.length, 2);
});

test('feed cards share one overflow menu placed over the opening button', async () => {
    const context = browserContext();
    const requests = [];
    context.fetch = async url => { requests.push(url); return { ok: true, json: async () => ({ raw_json: { rest_id: '300', full: true } }) }; };
    const { app, list } = feedListApp(context);
    app.tweets = WEB_FIXTURE.slim.map(tweet => JSON.parse(JSON.stringify(tweet))).filter((_, index) => index === 0 || index === 2);
    app.renderFeedList(app.tweets);
    const [quoted, retweet] = list.children;
    const button = retweet.querySelector('[data-action="menu"]');
    button.rect = { top: 420, right: 664, bottom: 456, left: 628, width: 36, height: 36 };

    let event = dispatchFeed(list, 'click', button.querySelector('circle'));
    assert.equal(event.propagationStopped, true, 'the opening click must not count as outside');
    assert.equal(app.tweetMenuOpen, 'list:301');
    assert.equal(app.listMenu.key, 'list:301');
    assert.equal(app.listMenu.tweet.tweet_id, '300');
    assert.deepEqual([app.listMenu.top, app.listMenu.right], [320, 36]);
    assert.equal(button.getAttribute('aria-expanded'), 'true');
    assert.ok(button.classList.contains('opacity-0'));
    assert.deepEqual(requests, ['/api/tweets/300/raw'], 'menus prefetch the displayed tweet');

    // The menu follows its button when content above it changes height.
    button.rect = { ...button.rect, top: 520 };
    app.placeListMenu();
    assert.equal(app.listMenu.top, 420);

    // Another card moves the menu; the first button is restored.
    const other = quoted.querySelector('[data-action="menu"]');
    other.rect = { top: 150, right: 664, bottom: 186, left: 628, width: 36, height: 36 };
    dispatchFeed(list, 'click', other);
    assert.equal(app.tweetMenuOpen, 'list:100');
    assert.equal(app.listMenu.top, 50);
    assert.equal(button.getAttribute('aria-expanded'), 'false');
    assert.equal(button.classList.contains('opacity-0'), false);
    assert.equal(other.getAttribute('aria-expanded'), 'true');

    // Detail menus and unrelated keys leave it open; its own key and the same button close it.
    app.closeTweetMenu('detail:100');
    assert.equal(app.tweetMenuOpen, 'list:100');
    dispatchFeed(list, 'click', other);
    assert.equal(app.tweetMenuOpen, null);
    assert.equal(other.getAttribute('aria-expanded'), 'false');
    dispatchFeed(list, 'click', other);
    app.closeTweetMenu(app.listMenu.key);
    assert.equal(app.tweetMenuOpen, null);
    assert.equal(other.classList.contains('opacity-0'), false);

    // Tag edits leave the card, and the open menu's button state, untouched.
    dispatchFeed(list, 'click', button);
    app.updateTweetTagData('300', null);
    assert.equal(list.children[1].querySelector('[data-action="menu"]'), button);
    assert.equal(button.getAttribute('aria-expanded'), 'true');
    assert.ok(button.classList.contains('opacity-0'));

    // Menu items act on the stored display tweet; a new list closes the menu.
    let tags = null;
    app.openTagModal = (id, data) => { tags = [id, data]; app.closeTweetMenu(); };
    app.openTagsFromTweetMenu(app.listMenu.tweet.tweet_id, app.listMenu.tweet.media_tags);
    assert.deepEqual(tags, ['300', null]);
    dispatchFeed(list, 'click', list.children[1].querySelector('[data-action="menu"]'));
    assert.equal(app.tweetMenuOpen, 'list:301');
    app.renderFeedList(app.tweets);
    assert.equal(app.tweetMenuOpen, null);
    const reloaded = list.children[1].querySelector('[data-action="menu"]');
    assert.equal(reloaded.getAttribute('aria-expanded'), 'false');
    assert.equal(reloaded.classList.contains('opacity-0'), false);

    const html = pageHtml();
    const menu = html.slice(html.indexOf('x-show="tweetMenuOpen === listMenu.key"'));
    assert.match(menu, /^x-show="tweetMenuOpen === listMenu\.key" x-cloak/);
    assert.match(menu, /@click\.outside="closeTweetMenu\(listMenu\.key\)" @keydown\.escape\.window="closeTweetMenu\(listMenu\.key\)"/);
    assert.match(menu, /:style="\{ top: listMenu\.top \+ 'px', right: listMenu\.right \+ 'px' \}"/);
    assert.match(menu, /x-transition:enter="transition ease-out duration-100"/);
    assert.match(menu, /@click\.stop="openTagsFromTweetMenu\(listMenu\.tweet\.tweet_id, listMenu\.tweet\.media_tags\)"/);
    assert.match(menu, /@click\.stop="copyRawTweet\(listMenu\.tweet\)"/);
});

test('hiding a tweet removes its card unless the search lists hidden tweets', async () => {
    const context = browserContext();
    const requests = [];
    let ok = true;
    context.fetch = async (url, options = {}) => {
        requests.push([options.method || 'GET', url]);
        return { ok, status: ok ? 200 : 500, json: async () => ({}) };
    };
    const alerts = [];
    context.alert = message => alerts.push(message);
    const { app, list } = feedListApp(context);
    app.tweets = WEB_FIXTURE.slim.map(tweet => JSON.parse(JSON.stringify(tweet))).filter((_, index) => index === 0 || index === 2);
    app.total = 2;
    app.renderFeedList(app.tweets);
    const [quoted, retweet] = list.children;

    // The shared menu reads the card's hidden state and hides the card, not its display tweet.
    dispatchFeed(list, 'click', retweet.querySelector('[data-action="menu"]'));
    assert.equal(app.listMenu.hidden, false);
    await app.setTweetHidden(app.listMenu.key.slice('list:'.length), !app.listMenu.hidden);
    assert.deepEqual(requests.at(-1), ['PUT', '/api/tweets/301/hidden']);
    assert.equal(app.tweetMenuOpen, null);
    assert.deepEqual(app.tweets.map(tweet => tweet.tweet_id), ['100']);
    assert.deepEqual(list.children, [quoted]);
    assert.equal(app.total, 1);

    // An is:hidden list keeps a newly hidden card and drops an unhidden one.
    app.searchQuery = 'needle is:hidden';
    await app.setTweetHidden('100', true);
    assert.equal(app.tweets[0].hidden, true);
    assert.deepEqual(list.children, [quoted]);
    await app.setTweetHidden('100', false);
    assert.deepEqual(requests.at(-1), ['DELETE', '/api/tweets/100/hidden']);
    assert.deepEqual(app.tweets, []);
    assert.equal(app.total, 0);

    // -is:hidden does not list hidden tweets; a failed request changes nothing.
    app.searchQuery = '-is:hidden';
    assert.equal(app.searchShowsHidden(), false);
    app.tweets = [JSON.parse(JSON.stringify(WEB_FIXTURE.slim[0]))];
    app.renderFeedList(app.tweets);
    ok = false;
    await app.setTweetHidden('100', true);
    assert.equal(alerts.length, 1);
    assert.equal(app.tweets.length, 1);
    assert.equal(app.tweets[0].hidden, undefined);
});

test('feed overflow buttons keep the long-press Tags shortcut and avatars their load state', () => {
    const context = browserContext();
    const timers = [];
    context.setTimeout = (callback, delay) => { timers.push({ callback, delay }); return timers.length; };
    context.clearTimeout = id => { if (timers[id - 1]) timers[id - 1].cleared = true; };
    const { app, list } = feedListApp(context);
    const dialog = mountTagDialog(context, app);
    app.tweets = [JSON.parse(JSON.stringify(WEB_FIXTURE.slim[2]))];
    app.tweets[0].retweeted_tweet.media_tags = { description: 'Kept', tags: ['Poll'] };
    app.renderFeedList(app.tweets);
    const card = list.children[0];
    const button = card.querySelector('[data-action="menu"]');
    const icon = button.querySelector('svg');

    const timerCount = timers.length;
    for (const modifiers of [{button: 2}, {button: 0, ctrlKey: true}]) {
        dispatchFeed(list, 'pointerdown', icon, {pointerType: 'mouse', ...modifiers});
        assert.equal(timers.length, timerCount, 'native menu gestures do not start the Tags hold timer');
    }

    const down = dispatchFeed(list, 'pointerdown', icon, { pointerType: 'touch', button: 0 });
    assert.equal(down.propagationStopped, true, 'pressing the button does not prefetch the thread');
    const press = timers.at(-1);
    assert.equal(press.delay, 1000);
    press.callback();
    assert.equal(dialog.tagModalOpen, true);
    assert.equal(dialog.tagModalTweetId, '300');
    assert.equal(dialog.tagModalData.description, 'Kept');
    // The click that ends a long press does not open the menu.
    dispatchFeed(list, 'click', button);
    assert.equal(app.tweetMenuOpen, null);

    for (const type of ['pointerup', 'pointercancel']) {
        dispatchFeed(list, 'pointerdown', button, { pointerType: 'mouse', button: 0 });
        const event = dispatchFeed(list, type, icon);
        assert.equal(event.propagationStopped, true);
        assert.equal(timers.at(-1).cleared, true, type);
    }
    dispatchFeed(list, 'pointerdown', button, { pointerType: 'touch', button: 0 });
    dispatchFeed(list, 'pointerleave', icon);
    assert.equal(timers.at(-1).cleared, undefined, 'leaving a child does not cancel');
    dispatchFeed(list, 'pointerleave', button);
    assert.equal(timers.at(-1).cleared, true);
    assert.equal(dispatchFeed(list, 'contextmenu', icon).defaultPrevented, false);
    assert.equal(dispatchFeed(list, 'contextmenu', button).defaultPrevented, false);
    assert.equal(dispatchFeed(list, 'contextmenu', card).defaultPrevented, false);
    assert.equal(dispatchFeed(list, 'pointerdown', card, { pointerType: 'mouse', button: 0 }).propagationStopped, false);
    assert.ok(list.listeners.some(entry => entry.type === 'pointerleave' && entry.capture));

    // Image load and error events do not bubble; capture listeners handle avatars only.
    const avatar = card.querySelector('img.avatar-image');
    avatar.naturalWidth = 48;
    dispatchFeed(list, 'load', avatar);
    assert.ok(avatar.classList.contains('avatar-image-loaded'));
    assert.ok(avatar.parentElement.classList.contains('avatar-loaded'));
    dispatchFeed(list, 'error', avatar);
    assert.equal(avatar.parentElement.classList.contains('avatar-loaded'), false);
    assert.ok(['load', 'error'].every(type => list.listeners.find(entry => entry.type === type).capture));
});

run();
