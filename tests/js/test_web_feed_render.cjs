'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { appScripts, pageHtml } = require('./page_scripts.cjs');
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

test('tweet counts use compact detail precision and whole feed units', () => {
    const context = browserContext();
    const app = loadScripts(context, appScripts(), 'tweetApp')();
    const cases = [
        [0, ''], [null, ''], [undefined, ''], [-1, ''], ['invalid', ''],
        [999, '999'], [1000, '1K'], [1250, '1.2K'], [999949, '999K'],
        [999950, '999K'], [1000000, '1M'], [1250000, '1.2M'],
        [999949999, '999M'], [999950000, '999M'], [1000000000, '1B'],
        [1250000000, '1.2B'], [999950000000, '999B'], [1000000000000, '1T'],
        ['1000000', '1M'],
    ];
    for (const [count, expected] of cases) {
        for (const isMain of [false, true]) {
            const html = app.renderActionBar({
                tweet_id: '123',
                text: 'Archived tweet',
                raw_json: {
                    legacy: {
                        reply_count: count, retweet_count: Number(count) || 0, quote_count: 0,
                        favorite_count: count, bookmark_count: count,
                    },
                    views: { count },
                },
            }, isMain);
            const labels = [...html.matchAll(/<span class="tweet-action-count">([^<]*)<\/span>/g)]
                .map(match => match[1]);
            assert.deepEqual(labels, Array(4).fill(expected), `count ${count}, main ${isMain}`);
            assert.equal([...html.matchAll(/<div class="flex items-center/g)].length, isMain ? 5 : 6, `action groups, main ${isMain}`);
        }
    }
    assert.equal(app.formatActionCount(11753), '11K');
    assert.equal(app.formatActionCount(33119), '33K');
    assert.equal(app.formatActionCount(57501), '57K');
    assert.equal(app.formatActionCount(915225), '915K');
    assert.equal(app.formatActionCount(87610), '87K');
    assert.equal(app.formatActionCount(7177), '7.1K');
    assert.equal(app.formatActionCount(20656), '20K');
    assert.equal(app.formatActionCount(1139), '1.1K');
    assert.equal(app.formatActionCount(999950), '999K');
    assert.equal(app.formatActionCount(1000000), '1M');
    assert.equal(app.formatCompactCount(999500), '999.5K');
    assert.equal(app.formatCompactCount(999950), '999.9K');
    assert.equal(app.formatCompactCount(853200), '853.2K');
    assert.equal(app.formatCompactCount(859320), '859.3K');
    assert.equal(app.formatCompactCount(0), '');
    const html = app.renderActionBar({
        tweet_id: '123',
        text: 'Archived tweet',
        raw_json: { legacy: { retweet_count: 600000, quote_count: 400000 } },
    });
    assert.match(html, /tweet-action-count">1M<\/span>/);
    const feed = app.renderActionBar({
        tweet_id: '123', text: 'Archived tweet',
        raw_json: { legacy: { bookmark_count: 12345 }, views: { count: 692000 } },
    });
    assert.match(feed, /title="12\.3K bookmarks" aria-label="12\.3K bookmarks"/);
    assert.doesNotMatch(feed, /12K<\/span>/);
    assert.match(feed, /tweet-action-count">692K<\/span>/);
    for (const views of [undefined, { count: 0 }]) {
        const detail = app.renderActionBar({ tweet_id: '123', text: 'Archived tweet', raw_json: { legacy: {}, ...(views ? { views } : {}) } }, true);
        assert.equal([...detail.matchAll(/class="tweet-action-count"/g)].length, 4);
        assert.doesNotMatch(detail, /tweet-action-count">(?:0|undefined)/);
    }
    const page = pageHtml();
    for (const prefix of ['threadData.main', 'panelThreadData.main']) {
        const at = page.indexOf(`class="tweet-timestamp`, page.indexOf(`renderCommunityNote(${prefix}.raw_json, false, true)`));
        const end = page.indexOf(`renderActionBar(${prefix}, true)`, at);
        const timestamp = page.slice(at, end);
        const count = prefix === 'panelThreadData.main'
            ? 'panelThreadData?.main?.raw_json?.views?.count' : 'threadData.main.raw_json.views.count';
        assert.ok(timestamp.includes(`formatCompactCount(${count})`));
        assert.match(timestamp, /<span>Views<\/span>/);
        assert.match(timestamp, /tweet-timestamp-meta[\s\S]*?roll-wrapper[\s\S]*?tweet-detail-views/);
        assert.match(timestamp, /tweet-detail-quotes/);
    }
});

test('demo reply metadata keeps body mentions out of the automatic recipient label', () => {
    const context = browserContext();
    const app = immediateComponent(loadScripts(context, appScripts(), 'tweetApp')());
    context.window.TWEETNOOK_DEMO = false;
    vm.runInContext(fs.readFileSync(path.join(ROOT, 'demo', 'jobs-replay.js'), 'utf8'), context);
    vm.runInContext(fs.readFileSync(path.join(ROOT, 'demo', 'demo-api.js'), 'utf8'), context);
    const fixture = JSON.parse(fs.readFileSync(path.join(ROOT, 'demo', 'data.json'), 'utf8'));
    const store = context.window.TweetNookDemo.createStore(fixture);
    const reply = store.hydrate('1795129713683255380');
    assert.deepEqual(Array.from(reply.raw_json.legacy.display_text_range), [13, 42]);
    assert.equal(app.getReplyTo(reply), 'aleksliving');
    assert.deepEqual(Array.from(app.getReplyRecipients(reply)), ['aleksliving']);
    const body = app.formatText(reply);
    assert.match(body, /@RPhantom00<\/span> it/);
    assert.doesNotMatch(body, /@aleksliving/);
});

test('reply recipients use only the captured automatic prefix and retain safe labels', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    const makeReply = (text, prefix, primary, mentions = null) => ({
        text,
        raw_json: { legacy: {
            full_text: text,
            in_reply_to_status_id_str: 'parent',
            in_reply_to_screen_name: primary,
            ...(prefix === null ? {} : { display_text_range: [prefix, text.length] }),
            ...(mentions === null ? {} : { entities: { user_mentions: mentions } }),
        } },
    });
    const mention = (text, name) => {
        const start = text.indexOf(`@${name}`);
        return { screen_name: name, indices: [start, start + name.length + 1] };
    };
    const twoText = '@first @second body @bodymention';
    const two = makeReply(twoText, '@first @second '.length, 'first', [mention(twoText, 'bodymention'), mention(twoText, 'second'), mention(twoText, 'first')]);
    assert.deepEqual(Array.from(app.getReplyRecipients(two)), ['first', 'second']);
    assert.match(app.renderReplyRecipients(two), /Replying to .*@first<\/span> and .*@second<\/span>/);
    assert.match(app.formatText(two), /^body <span[^>]+>@bodymention<\/span>$/);
    const malformedIndices = makeReply(twoText, '@first @second '.length, 'first', [
        { screen_name: 'first', indices: { start: 0, end: 6 } },
        { screen_name: 'second', indices: 7 },
    ]);
    assert.deepEqual(Array.from(app.getReplyRecipients(malformedIndices)), ['first', 'second']);

    const threeText = '@first @second @third body';
    const three = makeReply(threeText, '@first @second @third '.length, 'first', [
        mention(threeText, 'first'), mention(threeText, 'second'), mention(threeText, 'third'),
    ]);
    assert.deepEqual(Array.from(app.getReplyRecipients(three)), ['first', 'second', 'third']);
    assert.match(app.renderReplyRecipients(three), /@first<\/span> .*@second<\/span> and .*@third<\/span>/);

    const duplicateText = '@FIRST @second @first body';
    const duplicates = makeReply(duplicateText, '@FIRST @second @first '.length, 'SECOND', [
        mention(duplicateText, 'FIRST'), mention(duplicateText, 'second'), mention(duplicateText, 'first'),
    ]);
    assert.deepEqual(Array.from(app.getReplyRecipients(duplicates)), ['FIRST', 'second']);
    assert.equal(app.getReplyTo(duplicates), 'SECOND', 'primary reply target compatibility');

    const bodyLeading = makeReply('@body is intentional', 0, 'primary', []);
    assert.deepEqual(Array.from(app.getReplyRecipients(bodyLeading)), ['primary']);
    assert.match(app.formatText(bodyLeading), /^<span[^>]+>@body<\/span> is intentional$/);
    assert.deepEqual(Array.from(app.getReplyRecipients({ text: '@ordinary mention', raw_json: { legacy: { full_text: '@ordinary mention' } } })), []);
    const nonReply = {
        text: '@ordinary @body',
        raw_json: { legacy: {
            full_text: '@ordinary @body', display_text_range: ['@ordinary '.length, 14],
            entities: { user_mentions: [mention('@ordinary @body', 'ordinary')] },
        } },
    };
    assert.deepEqual(Array.from(app.getReplyRecipients(nonReply)), []);

    const legacy = makeReply('@old @primary body @notrecipient', null, 'primary');
    assert.deepEqual(Array.from(app.getReplyRecipients(legacy)), ['old', 'primary']);
    assert.match(app.formatText(legacy), /^body <span[^>]+>@notrecipient<\/span>$/);
    const nameOnlyFlat = {
        text: '@old @primary body',
        raw_json: { full_text: '@old @primary body', in_reply_to_screen_name: 'primary' },
    };
    assert.equal(app.formatText(nameOnlyFlat), 'body', 'screen-name-only flat replies use the legacy prefix fallback');
    const normalizedSource = {
        text: '@body has an intentional leading mention',
        raw_json: { legacy: {
            full_text: '@first @second @body has an intentional leading mention',
            display_text_range: ['@first @second '.length, '@first @second @body has an intentional leading mention'.length],
            in_reply_to_status_id_str: 'parent',
            in_reply_to_screen_name: 'first',
        } },
    };
    assert.match(app.formatText(normalizedSource), /^<span[^>]+>@body<\/span> has an intentional leading mention$/,
        'valid raw offsets do not strip a normalized source that has a different prefix');
    assert.deepEqual(Array.from(app.getReplyRecipients({ legacy: three.raw_json.legacy })), ['first', 'second', 'third'], 'flat raw shape');
    assert.deepEqual(Array.from(app.getReplyRecipients({
        full_text: threeText,
        display_text_range: three.raw_json.legacy.display_text_range,
        in_reply_to_status_id_str: 'parent',
        in_reply_to_screen_name: 'first',
    })), ['first', 'second', 'third'], 'flat raw with missing mention entities');

    const hostileText = '@<img src=x onerror=alert(1)> body';
    const hostile = makeReply(hostileText, '@<img src=x onerror=alert(1)> '.length, '<img src=x onerror=alert(1)>', []);
    const hostileHtml = app.renderReplyRecipients(hostile);
    assert.doesNotMatch(hostileHtml, /<img/);
    assert.match(hostileHtml, /&lt;img src=x onerror=alert\(1\)&gt;/);

    let opened = null;
    app.openUsernameProfileCard = (event, username) => { opened = { event, username }; };
    const second = { dataset: { username: 'second' } };
    const event = { target: { closest: selector => {
        assert.equal(selector, '[data-action="reply-profile"][data-username]');
        return second;
    } }, stopPropagation() { this.stopped = true; } };
    app.handleReplyRecipientClick(event);
    assert.equal(opened.username, 'second');
    assert.equal(opened.event.currentTarget, second);
    assert.equal(event.stopped, true);
});

test('tweet text decodes one entity layer before safe rendering and length checks', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    const text = 'Fish &amp; chips &lt;3 &quot;yes&quot; &apos;ok&apos;';
    const expected = 'Fish &amp; chips &lt;3 "yes" \'ok\'';
    assert.equal(app.decodeTweetText(text), 'Fish & chips <3 "yes" \'ok\'');
    assert.equal(app.formatText({ text }), expected);
    assert.equal(app.formatText({ raw_json: { legacy: { full_text: text } } }), expected);
    assert.equal(app.formatText({ legacy: { full_text: text } }), expected); // Raw quote/reply object.
    assert.equal(app.formatText({ text: 'Already & decoded <3' }), 'Already &amp; decoded &lt;3');
    assert.equal(app.formatText({ text: '&amp;lt; literal entity' }), '&amp;lt; literal entity');
    assert.equal(app.formatText({ text: '&unknown;' }), '&amp;unknown;');
    const attack = app.formatText({ text: '&lt;img src=x onerror=alert(1)&gt; @alice #topic' });
    assert.match(attack, /^&lt;img src=x onerror=alert\(1\)&gt;/);
    assert.doesNotMatch(attack, /<img/);
    assert.match(attack, /class="mention/);
    assert.match(attack, /class="hashtag/);
    const shortDecoded = app.formatText({ tweet_id: 'entity-length', text: 'A &amp; '.repeat(40) });
    assert.doesNotMatch(shortDecoded, /Show more/);
    assert.equal(shortDecoded, 'A &amp; '.repeat(40).trim());
});

test('result text bolds words, phrases, prefixes and hashtags safely', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.searchHighlightTerms = ['gif', '"night sky"', 'archiv*', '#python', 'cafe'];
    const tweet = {text: 'GIF gift gif night sky night blue sky Archives #Python python café Cafe\u0301 <gif> &amp; gif'};
    const html = app.formatText(tweet, false, true);
    assert.match(html, /^<strong>GIF<\/strong> gift <strong>gif<\/strong> <strong>night sky<\/strong> night blue sky <strong>Archives<\/strong>/);
    assert.match(html, /data-tag="Python"><strong>#Python<\/strong><\/span> python <strong>café<\/strong> <strong>Cafe\u0301<\/strong>/);
    assert.match(html, /&lt;<strong>gif<\/strong>&gt; &amp; <strong>gif<\/strong>/);
    assert.doesNotMatch(html, /<gif>|<strong>gift|<strong>night blue sky/);
    assert.doesNotMatch(app.formatText(tweet), /<strong>/); // Detail text stays plain.
    app.searchHighlightTerms = ['gif', 'gif*'];
    assert.equal(app.highlightSearchText('gif GIFS'), '<strong>gif</strong> <strong>GIFS</strong>');
    app.searchHighlightTerms = ['"gif*"'];
    assert.equal(app.highlightSearchText('gif gifts'), '<strong>gif</strong> gifts');
    app.searchHighlightTerms = ['"hello alice"'];
    assert.match(app.formatText({text: 'hello @alice'}, false, true), /<strong>hello <\/strong><span[^>]+><strong>@alice<\/strong>/);
});

test('result bolding preserves link destinations, attributes, entities and placeholders', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.searchHighlightTerms = ['gif', 'amp', 'class', 'script'];
    const html = app.formatText({text: 'gif &amp; https://t.co/x', raw_json: {legacy: {entities: {urls: [{
        url: 'https://t.co/x', expanded_url: 'https://example.com/gif?q=class&x=amp', display_url: 'example.com/gif<script>',
    }]}}}}, false, true);
    assert.match(html, /href="https:\/\/example.com\/gif\?q=class&amp;x=amp"/);
    assert.match(html, />example.com\/<strong>gif<\/strong>&lt;<strong>script<\/strong>&gt;<\/a>/);
    assert.doesNotMatch(html, /<script|&<strong>amp|class="<strong>|href="[^\"]*<strong>/);
    assert.doesNotMatch(app.formatText({availability: {state: 'not_archived', message: 'gif not archived'}}, false, true), /<strong>/);
});

test('feed result bolding applies to quotes and Show more and keeps mention/hashtag clicks', () => {
    const { app, list } = feedListApp();
    app.searchHighlightTerms = ['long', 'alice', '#topic'];
    app.tweets = [{tweet_id: '1', text: 'long @alice #topic ' + 'long '.repeat(80),
        author: {id: '2', username: 'bob', display_name: 'Bob'},
        quoted_tweet: {tweet_id: '3', text: 'long quote', author: {username: 'q', display_name: 'Q'}},
    }];
    app.renderFeedList(app.tweets);
    const card = list.children[0];
    assert.ok(card.querySelector('[data-action="quote"] strong'));
    let profile;
    app.openProfileCard = (event, tweet) => { profile = tweet.author.username; };
    dispatchFeed(list, 'click', card.querySelector('.mention strong'));
    assert.equal(profile, 'alice');
    let searched;
    app.search = () => { searched = app.searchQuery; };
    dispatchFeed(list, 'click', card.querySelector('.hashtag strong'));
    assert.equal(searched, '#topic');
    const text = card.querySelector('[data-action="text"]');
    dispatchFeed(list, 'click', text.querySelector('.expand-tweet'));
    assert.equal(text.querySelector('.expand-tweet'), null);
    assert.equal(text.querySelectorAll('strong').length, 83);
    assert.equal(list.children[0], card);
});

test('a quote shows its own quote nested inside it, as X does', () => {
    const { app, list } = feedListApp();
    const photo = { type: 'photo', download: { local_path: 'media/n.jpg' } };
    const deepest = {
        tweet_id: '9',
        text: `nested ${'word '.repeat(80)}`,
        author: { id: '7', username: 'n', display_name: 'N' },
        created_at: 'Fri Oct 09 18:25:33 +0000 2026',
        raw_json: { birdwatch_pivot: { subtitle: { text: 'nested note' } } },
        quoted_tweet: { tweet_id: '10', text: 'too deep', author: { username: 'd' } },
    };
    const quote = {
        tweet_id: '3',
        text: 'middle',
        author: { username: 'q', display_name: 'Q' },
        raw_json: { birdwatch_pivot: { subtitle: { text: 'middle note' } } },
        quoted_tweet: deepest,
        qt_media: [photo],
    };
    const tweet = { tweet_id: '1', text: 'outer', author: { username: 'a' }, quoted_tweet: quote, qt_media: [] };

    // A bar-marked block after the quote's content and before its note, with a
    // small thumbnail beside clamped text; nothing deeper and no note of its own.
    const html = app.renderFeedCard(tweet);
    assert.match(html, /<div class="quote-nested quote-nested-media" data-thread-id="9" data-action="quote">/);
    assert.match(html, /<div class="quote-nested[^>]*>.*@n<\/span>.*<div class="quote-media"><div class="quote-thumbnail"><img src="\/media\/n\.jpg"/);
    assert.ok(html.indexOf('quote-nested') < html.indexOf('middle note'));
    assert.doesNotMatch(html, /too deep|nested note|expand-tweet/);
    assert.equal((html.match(/class="quote-nested/g) || []).length, 1);
    // Like X, the quote's avatar is 24px and the nested quote's 20px.
    assert.deepEqual(html.match(/class="avatar w-\d h-\d/g), ['class="avatar w-6 h-6', 'class="avatar w-5 h-5']);

    // Without media it is text only; unavailable nested quotes are left out.
    const textOnly = app.renderNestedQuote({ ...quote, qt_media: [] });
    assert.match(textOnly, /^<div class="quote-nested" data-thread-id="9" data-action="quote">/);
    assert.doesNotMatch(textOnly, /quote-thumbnail/);
    const missing = { tweet_id: '9', availability: { placeholder: true } };
    assert.equal(app.renderNestedQuote({ ...quote, quoted_tweet: missing }), '');
    assert.equal(app.renderNestedQuote(null), '');

    // In lists a click on the nested quote opens it, and elsewhere opens the quote.
    app.tweets = [tweet];
    app.renderFeedList(app.tweets);
    let opened;
    app.openThread = id => { opened = id; };
    const card = list.children[0];
    const [quoteText, nestedText] = card.querySelectorAll('.quote-text');
    dispatchFeed(list, 'click', nestedText);
    assert.equal(opened, '9');
    dispatchFeed(list, 'click', quoteText);
    assert.equal(opened, '3');

    // Thread templates render it with x-html; their click handler opens it too.
    opened = null;
    const event = { target: nestedText, propagationStopped: false, stopPropagation() { this.propagationStopped = true; } };
    app.openNestedQuote(event);
    assert.equal(opened, '9');
    assert.equal(event.propagationStopped, true);
    app.openNestedQuote({ target: quoteText, stopPropagation() { throw new Error('outside'); } });

    const template = pageHtml();
    for (const name of ['threadData?.main', 'panelThreadData?.main', 'child', 'reply']) {
        const slot = `<div x-html="renderNestedQuote(getQuoteTweet(${name}))" @click="openNestedQuote($event)" @load.capture="avatarEvent($event)" @error.capture="avatarEvent($event)"></div>`;
        const hasMedia = name.endsWith('main') ? name.replace('?.main', '?.main?.qt_media?.length') : `${name}?.qt_media?.length`;
        assert.equal(template.split(slot).length - 1, name.endsWith('main') ? 1 : 2);
        assert.ok(template.includes(`${slot}\n${' '.repeat(template.split(slot)[0].split('\n').at(-1).length)}<div x-html="renderCommunityNote(getQuoteTweet(${name}), true, false, Boolean(${hasMedia}))"`));
    }
    const css = pageCss();
    assert.match(css, /\.quote-nested \{[^}]*margin-top: 12px;[^}]*padding: 8px 0 8px 15px;/);
    assert.match(css, /\.quote-text \+ \.quote-nested,\n\.quote-text \+ :empty \+ \.quote-media:empty \+ \* > \.quote-nested \{[^}]*margin-top: 6px;/);
    assert.match(css, /\.quote-nested > \.quote-text \{[^}]*-webkit-line-clamp: 2;/);
    assert.match(css, /\.quote-nested \.quote-thumbnail \{[^}]*width: 64px;/);
});

test('result highlights follow the returned query rather than edits to the search box', async () => {
    const { app, list, context } = feedListApp();
    context.fetch = async () => ({ok: true, status: 200, json: async () => ({
        tweets: [{tweet_id: '1', text: 'GIF cats', author: {username: 'a'}}],
        page: 1, total: 1, pages: 1, has_more: false, highlight_terms: ['gif'],
    })});
    app.searchQuery = 'gif is:reply';
    await app.fetchTweets();
    assert.equal(list.children[0].querySelector('strong').textContent, 'GIF');
    app.searchQuery = 'cats';
    assert.match(app.formatText(app.tweets[0], false, true), /^<strong>GIF<\/strong> cats$/);
    context.fetch = async () => ({ok: true, status: 200, json: async () => ({
        tweets: app.tweets, page: 1, total: 1, pages: 1, has_more: false, highlight_terms: [],
    })});
    await app.fetchTweets();
    assert.equal(list.children[0].querySelector('strong'), null);
});

test('text and card renderers escape HTML and reject active URL schemes', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(
        context,
        appScripts(),
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
        appScripts(),
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
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
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

    const css = pageCss();
    assert.match(css, /\.tweet-skeleton/);
    assert.match(css, /\.tweet-skeleton-main \{[\s\S]*font-size: 0;[\s\S]*line-height: 0;/);
});

test('community notes escape headings, text, labels, and external links', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(
        context,
        appScripts(),
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
    // Note links pass safeURL: no attribute breakout, and only http(s) targets.
    assert.doesNotMatch(note, /onmouseover="/);
    assert.match(note, /href="http:\/\/localhost\/%22%20onmouseover=%22x"/);
    assert.doesNotMatch(note, /<img onerror/);
});

test('community note detail uses captured title and omits footers, while feed and quote retain compact title', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    const raw = { birdwatch_pivot: {
        title: 'Full <b>title</b>',
        shorttitle: 'Short title',
        subtitle: { text: 'Note body\nsecond line', entities: [] },
        footer: { text: 'Footer information', entities: [] },
    } };
    const feed = app.renderCommunityNote(raw);
    const quote = app.renderCommunityNote(raw, true);
    const detail = app.renderCommunityNote(raw, false, true);
    const quoteWithMedia = app.renderCommunityNote(raw, true, false, true);
    assert.match(feed, /Short title/);
    assert.doesNotMatch(feed, /Footer information/);
    assert.match(quote, /community-note-quote/);
    assert.match(quote, /community-note-quote-spaced/);
    assert.match(quoteWithMedia, /community-note-quote-media/);
    assert.match(quote, /Short title/);
    assert.match(detail, /Full &lt;b&gt;title&lt;\/b&gt;/);
    assert.ok(detail.includes('Note body\nsecond line'));
    assert.doesNotMatch(detail, /Footer information|community-note-footer/);
    assert.doesNotMatch(quote, /Footer information/);
    assert.doesNotMatch(detail, /<b>title<\/b>/);

    const missingText = app.renderCommunityNote({ birdwatch_pivot: { shorttitle: 'No note text' } }, false, true);
    assert.equal(missingText, '');

    const html = pageHtml();
    for (const prefix of ['threadData.main', 'panelThreadData.main']) {
        const detailCall = `renderCommunityNote(${prefix}.raw_json, false, true)`;
        const detailIndex = html.indexOf(detailCall);
        const timestampIndex = html.indexOf('class="tweet-timestamp', detailIndex);
        assert.ok(detailIndex >= 0 && timestampIndex > detailIndex, `${prefix} detail note precedes timestamp`);
    }
});

test('tweet bylines size dates from the visible label and truncate handles when needed', () => {
    const html = pageHtml();
    const css = pageCss();

    assert.match(css, /\.tweet-byline-name\s*\{[^}]*min-width:\s*0;[^}]*overflow:\s*hidden;[^}]*text-overflow:\s*ellipsis;[^}]*white-space:\s*nowrap;/s);
    assert.match(css, /\.tweet-byline-detail\s*\{[^}]*flex-shrink:\s*0;[^}]*white-space:\s*nowrap;/s);
    assert.match(css, /\.tweet-byline \.roll-item:nth-child\(2\)\s*\{[^}]*position:\s*absolute;[^}]*top:\s*20px;/s);
    assert.match(css, /\.tweet-byline \.roll-wrapper:hover \.roll-item:nth-child\(2\)\s*\{[^}]*position:\s*static;/s);
    assert.match(css, /\.tweet-byline \.roll-wrapper:hover\s*\{[^}]*max-width:\s*100%;/s);
    assert.match(css, /\.tweet-byline-handle\s*\{[^}]*flex-shrink:\s*1;[^}]*min-width:\s*0;[^}]*overflow:\s*hidden;[^}]*text-overflow:\s*ellipsis;/s);
    assert.match(html, /class="[^"]*tweet-byline-name[^"]*"[^>]*x-text="parent\.author\.display_name/);
    assert.match(html, /tweet-byline-detail tweet-byline-handle" x-text="'@' \+ \(parent\.author\.username/);
    assert.match(html, /class="roll-wrapper[^"]*tweet-byline-detail[^"]*"/);
    assert.match(html, /class="[^"]*tweet-byline-detail[^"]*" x-text="formatDate\(threadData\.main\.created_at\)"/);
    const { app } = feedListApp();
    const card = parseFakeHTML(app.renderFeedCard({ tweet_id: '1', text: 'x', author: { id: '2', username: 'a', display_name: 'A' } }))[0];
    assert.equal(card.querySelector('.tweet-byline-name').textContent, 'A');
    assert.equal(card.querySelector('.tweet-byline-detail').textContent, '@a');
    assert.ok(card.querySelector('.tweet-byline-handle'));
    assert.ok(card.querySelector('.roll-wrapper').classList.contains('tweet-byline-detail'));
});

test('abbreviated raw tweet JSON renders exactly like the stored document', () => {
    const fixture = JSON.parse(fs.readFileSync(
        path.join(ROOT, 'tests', 'fixtures', 'web_raw_tweets.json'),
        'utf8',
    ));
    const app = immediateComponent(loadScripts(browserContext(), appScripts(), 'tweetApp')());
    const event = () => ({
        currentTarget: {
            classList: { contains: () => true },
            closest: () => null,
            getBoundingClientRect: () => ({ left: 0, top: 0, bottom: 0 }),
        },
    });
    const profile = (tweet, isQuote) => {
        app.openProfileCard(event(), tweet, isQuote);
        const { x, y, request, ...card } = app.profileCard;
        app.closeProfileCard();
        return card;
    };
    const render = source => {
        const tweetObj = JSON.parse(JSON.stringify(source));
        const retweet = app.getRetweet(tweetObj);
        const tweet = retweet ? app.formatRetweetToTweet(tweetObj, retweet) : tweetObj;
        const quote = app.getQuoteTweet(tweet);
        const copy = () => JSON.parse(JSON.stringify(tweet));
        return JSON.stringify([
            Boolean(retweet), app.getTweetId(tweet), app.formatText(tweet), app.formatText(tweet, true),
            app.renderMediaGrid(tweet.media), app.renderCard(tweet), app.getReplyTo(tweet),
            app.renderCommunityNote(tweet.raw_json), app.renderActionBar(copy()),
            app.renderActionBar(copy(), true), app.getInitial(tweet), app.formatDate(tweet.created_at),
            app.isTextPlaceholderTweet(tweet), profile(tweet, false),
            quote && [
                app.isTombstone(quote), app.renderQuotePlaceholder(quote), app.getQuoteAuthor(quote),
                app.getTweetId(quote), app.formatDate(quote?.legacy?.created_at || quote?.created_at),
                app.getReplyTo({ raw_json: quote }), app.formatText(quote),
                app.renderCard({ raw_json: quote }), app.renderCommunityNote(quote, true),
                profile(quote, true),
            ],
        ]);
    };

    assert.equal(fixture.full.length, fixture.slim.length);
    fixture.full.forEach((tweet, index) => {
        assert.ok(JSON.stringify(fixture.slim[index]).length < JSON.stringify(tweet).length);
        assert.equal(render(fixture.slim[index]), render(tweet), `tweet ${index}`);
    });
});

test('feed cards render each fixture tweet shape as plain markup', () => {
    const { app } = feedListApp();
    const render = tweet => parseFakeHTML(app.renderFeedCard(JSON.parse(JSON.stringify(tweet))))[0];
    WEB_FIXTURE.slim.forEach((tweet, index) => {
        // Abbreviated list JSON renders exactly like the stored document.
        assert.equal(
            app.renderFeedCard(JSON.parse(JSON.stringify(tweet))),
            app.renderFeedCard(JSON.parse(JSON.stringify(WEB_FIXTURE.full[index]))),
            `tweet ${index}`,
        );
    });
    const [quoted, rawQuoted, retweet, rawRetweet, legacy, tombstoneQuote] = WEB_FIXTURE.slim.map(render);

    for (const card of [quoted, rawQuoted, retweet, rawRetweet, legacy, tombstoneQuote]) {
        assert.ok(card.classList.contains('feed-card'));
        assert.equal(card.dataset.action, 'thread');
        assert.equal(card.querySelectorAll('[data-action="profile"]').length, 2);
        assert.equal(card.querySelector('[data-action="menu"]').getAttribute('aria-expanded'), 'false');
        assert.equal(card.querySelector('[data-action="menu"]').getAttribute('aria-label'), 'More actions');
        assert.ok(card.querySelector('.roll-wrapper').matches('[data-action="stop"]'));
        assert.equal(card.querySelectorAll('img.avatar-image').length, card.querySelectorAll('.avatar-initial').length);
    }

    // Quote with a visibility wrapper: reply line, Show more, link card, notes on both tweets.
    assert.equal(quoted.dataset.tweetId, '100');
    assert.equal(quoted.dataset.threadId, '100');
    assert.equal(quoted.querySelector('[data-action="reply-profile"]').dataset.username, 'Dave');
    assert.equal(quoted.querySelector('[data-action="reply-profile"]').textContent, '@Dave');
    const quote = quoted.querySelector('[data-action="quote"]');
    assert.equal(quote.dataset.threadId, '200');
    assert.equal(quote.querySelector('.tweet-byline-name').textContent, 'Carol');
    assert.equal(quote.querySelector('.tweet-byline-detail').textContent, '@carol');
    assert.equal(quote.querySelector('.avatar-initial').textContent, 'C');
    assert.equal(quote.querySelector('img.avatar-image').getAttribute('src'), '/api/avatar/12?v=3');
    assert.equal(quoted.querySelectorAll('.expand-tweet').length, 1);
    assert.equal(quoted.querySelector('.expand-tweet').closest('[data-action="text"]').closest('[data-action="quote"]'), null);
    assert.ok(quoted.querySelector('.hashtag') && quoted.querySelector('.mention'));
    assert.match(quoted.querySelector('[data-action="text"] a').getAttribute('href'), /^https:\/\/example\.test\/story/);
    assert.ok(quoted.querySelector('a.mt-3').closest('[data-action="stop"]'), 'link card');
    assert.equal(quoted.textContent.match(/Readers added context/g).length, 2);
    assert.ok(quote.querySelector('.community-note-quote').closest('[data-action="stop"]'), 'quote note');
    assert.equal(quoted.querySelector('[data-action="text"]').getAttribute('class'), 'tweet-text text-[15px] whitespace-pre-wrap break-words');

    // Retweets show the original under a repost header and keep the list item id.
    for (const card of [retweet, rawRetweet]) {
        assert.equal(card.dataset.tweetId, '301');
        assert.equal(card.dataset.threadId, '300');
        assert.match(card.firstElementChild.textContent, /^Alice Reposted$/);
        assert.equal(card.querySelector('.tweet-byline-name').textContent, 'Erin');
        assert.match(card.textContent, /40 votes · Final results/);
    }
    assert.equal(quoted.textContent.includes('Reposted'), false);

    // Legacy v1 tweets resolve their quote and reply target from the raw shape.
    assert.equal(legacy.querySelector('[data-action="reply-profile"]').dataset.username, 'frank');
    assert.equal(legacy.querySelector('[data-action="quote"]').dataset.threadId, '401');

    // A tombstone quote renders the compact skeleton, which does not open a thread.
    assert.equal(tombstoneQuote.querySelector('[data-action="quote"]'), null);
    const skeleton = tombstoneQuote.querySelector('.tweet-skeleton-compact');
    assert.ok(skeleton.closest('[data-action="stop"]'));
    assert.equal(skeleton.closest('.feed-card'), tombstoneQuote);

    // A placeholder list item keeps the card frame with a content skeleton and no actions.
    const placeholder = render({
        tweet_id: '900', text: 'stale', author: { id: null, username: null, display_name: null },
        raw_json: null, media: [], availability: { placeholder: true, reason: 'deleted_by_author', message: 'Gone.' },
    });
    assert.equal(placeholder.dataset.threadId, '900');
    assert.equal(placeholder.querySelector('.tweet-byline-name').textContent, 'Unknown');
    assert.equal(placeholder.querySelector('.avatar-initial').textContent, '?');
    assert.ok(placeholder.querySelector('[data-action="text"] .tweet-skeleton-content'));
    assert.equal(placeholder.textContent.includes('stale'), false);
    assert.equal(placeholder.querySelector('a[title="Open on Twitter/X"]'), null);
});

test('feed cards escape hostile names, handles, reply targets, dates, sync labels, and ids', () => {
    const { app } = feedListApp();
    const hostile = '<script>alert(1)</script>"\'&x';
    app.formatDate = () => `<img src=x onerror=alert(1)>"&`;
    app.formatSyncDate = () => `Synced <b>"&'`;
    const tweet = {
        tweet_id: '1"><script>x</script>',
        text: 'Hello',
        author: { id: '"><img>', username: hostile, display_name: hostile },
        collection: { synced_at: '2026-01-01T00:00:00Z' },
        raw_json: { legacy: { in_reply_to_screen_name: hostile, full_text: 'Hello' } },
        quoted_tweet: {
            tweet_id: '2"><b>', text: 'Quoted',
            author: { id: '3', username: hostile, display_name: hostile },
            raw_json: { legacy: { in_reply_to_screen_name: hostile } },
        },
        retweeted_tweet: null,
    };
    const html = app.renderFeedCard(tweet);
    assert.doesNotMatch(html, /<script>|<img src=x|<b>|"><img>/);
    assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;&quot;&#039;&amp;x/);

    const card = parseFakeHTML(html)[0];
    assert.equal(card.dataset.tweetId, tweet.tweet_id);
    assert.equal(card.querySelector('.tweet-byline-name').textContent, hostile);
    assert.equal(card.querySelector('.tweet-byline-detail').textContent, `@${hostile}`);
    assert.equal(card.querySelector('.avatar-initial').textContent, '<');
    assert.equal(card.querySelector('img.avatar-image').getAttribute('src'), '/api/avatar/%22%3E%3Cimg%3E?v=3');
    assert.equal(card.querySelector('.roll-item').textContent, '<img src=x onerror=alert(1)>"&');
    assert.equal(card.querySelectorAll('.roll-item')[1].textContent, `Synced <b>"&'`);
    const replies = card.querySelectorAll('[data-action="reply-profile"]');
    assert.equal(replies.length, 2);
    for (const reply of replies) {
        assert.equal(reply.dataset.username, hostile);
        assert.equal(reply.textContent, `@${hostile}`);
    }
    const quote = card.querySelector('[data-action="quote"]');
    assert.equal(quote.dataset.threadId, '2"><b>');
    assert.equal(quote.querySelector('.tweet-byline-name').textContent, hostile);
    // The Open on X link escapes its path segments too.
    assert.match(html, /href="https:\/\/x\.com\/&lt;script&gt;alert\(1\)&lt;\/script&gt;&quot;&#039;&amp;x\/status\/1&quot;&gt;&lt;script&gt;x&lt;\/script&gt;"/);

    // Retweet headers escape the reposting account's name.
    const repost = app.renderFeedCard({
        tweet_id: '5', author: { display_name: hostile },
        retweeted_tweet: { tweet_id: '6', text: 'Original', author: { id: '7', username: 'o', display_name: 'O' } },
    });
    assert.equal(parseFakeHTML(repost)[0].firstElementChild.textContent, `${hostile} Reposted`);
    assert.doesNotMatch(repost, /<script>/);
});

test('desktop list and post layouts follow X web: strips, page dashes, quotes, Show more, large cards', () => {
    const context = browserContext();
    const app = immediateComponent(loadScripts(context, appScripts(), 'tweetApp')());
    const css = pageCss();
    const desktop = css.slice(css.indexOf("/* X's desktop web, measured in October 2026."));
    const block = desktop.slice(0, desktop.indexOf('\n}\n'));
    // Inline strips use the first-item height at every width; quotes keep the shared height.
    assert.match(css, /^\.media-carousel-feed \.media-carousel-item,\n\.media-carousel-detail \.media-carousel-item \{\s*height: var\(--gallery-height-app, var\(--gallery-height\)\);/m);
    assert.match(block, /\.media-carousel-dots \{\s*display: flex;/);
    assert.match(block, /opacity: calc\(0\.35 \+ 0\.65 \* var\(--dot-active, 0\)\);/);
    assert.match(block, /\.quote-card:not\(\.quote-compact\) > \.quote-text \{[^}]*-webkit-line-clamp: 5;/);
    assert.match(block, /\.quote-card:not\(\.quote-compact\) > \.quote-media:empty \{\s*margin-bottom: 0;/);
    assert.match(block, /\.quote-media:has\(> \.media-carousel\) \{\s*margin-top: 16px;/);
    assert.match(block, /\[data-action="media"\]:has\(> \.media-single\) \+ \.quote-card,[^{]*\{\s*margin-top: 4px;/);
    // Large link cards are no longer limited to phone widths.
    assert.match(css, /^\.link-card-large:has\(\.link-card-image\) > \.link-card-text \{\s*display: none;/m);

    const long = app.formatText({tweet_id: 'long-show-more', text: 'word '.repeat(80)});
    assert.doesNotMatch(long, /\.\.\.|…/);
    assert.match(long, /class="text-\[var\(--accent-color\)\] hover:underline cursor-pointer block expand-tweet"/);
});

run();
