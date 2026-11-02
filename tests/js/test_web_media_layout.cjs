'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { appScripts, appScriptsWith, pageHtml } = require('./page_scripts.cjs');
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

test('lazy feed images are observed per card and loaded and decoded ahead of scrolling', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    const observed = [];
    app.mediaPrefetchObserver = { observe: node => observed.push(node) };
    const image = card => {
        const img = { tagName: 'IMG', loading: 'lazy', decoded: 0, closest: () => card };
        img.decode = () => { img.decoded += 1; return Promise.resolve(); };
        return img;
    };
    const card = { tagName: 'DIV' };
    const images = [image(card), image(card)];
    card.querySelectorAll = () => images;
    const threadImage = image(null);

    app.observeMediaPrefetch(card);
    app.observeMediaPrefetch(threadImage);
    assert.deepEqual(observed, [card, card, threadImage]);

    app.prefetchMedia(card);
    app.prefetchMedia(threadImage);
    for (const img of [...images, threadImage]) {
        assert.equal(img.loading, 'eager');
        assert.equal(img.decoded, 1);
    }
    observed.length = 0;
    app.observeMediaPrefetch(threadImage);
    assert.deepEqual(observed, []);
});

test('avatar load and error state hides initials only for real images and recovers on reuse', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    const classes = () => {
        const values = new Set();
        return {
            contains: value => values.has(value),
            toggle(value, enabled) { enabled ? values.add(value) : values.delete(value); },
            remove(value) { values.delete(value); },
        };
    };
    const parent = { classList: classes() };
    const image = { naturalWidth: 128, naturalHeight: 128, classList: classes(), parentElement: parent };

    app.avatarLoaded(image); // A real alpha image must suppress the fallback.
    assert.equal(parent.classList.contains('avatar-loaded'), true);
    assert.equal(image.classList.contains('avatar-image-loaded'), true);

    image.naturalWidth = image.naturalHeight = 1;
    app.avatarLoaded(image); // HTTP 200 transparent placeholders do not trigger error.
    assert.equal(parent.classList.contains('avatar-loaded'), false);
    assert.equal(image.classList.contains('avatar-image-loaded'), false);

    image.naturalWidth = image.naturalHeight = 48;
    app.avatarLoaded(image);
    app.avatarFailed(image);
    assert.equal(parent.classList.contains('avatar-loaded'), false);
    assert.equal(image.classList.contains('avatar-image-loaded'), false);
    app.avatarLoaded(image); // Reusing the same element must recover after an error.
    assert.equal(parent.classList.contains('avatar-loaded'), true);
    assert.equal(image.classList.contains('avatar-image-loaded'), true);
});

test('avatar initials preserve emoji and combining graphemes across author surfaces', () => {
    const context = browserContext();
    const { tweetApp, avatarInitial } = loadScripts(context, appScripts(), '({tweetApp, avatarInitial})');
    const app = immediateComponent(tweetApp());
    app.positionProfileCard = () => {};
    for (const initial of ['😀', '👩🏽‍💻', '🇨🇦', 'e\u0301', 'A']) {
        const name = initial + ' Author';
        assert.equal(avatarInitial(name), initial);
        assert.equal(app.getInitial({author: {display_name: name}}), initial);
        assert.equal(app.getInitial({author: {username: name}}), initial);
        assert.equal(app.getQuoteAuthor({author: {display_name: name}}).initial, initial);
        assert.equal(app.getQuoteAuthor({core: {user_results: {result: {legacy: {name}}}}}).initial, initial);
        app.profileCard = { name: 'Original', snapshots: [{display_name: name}] };
        app.showProfileSnapshot(0);
        assert.equal(app.profileCard.initial, initial.toUpperCase());
    }
    assert.equal(app.getInitial({author: {}}), '?');
    assert.equal(avatarInitial(''), '?');
    assert.equal(avatarInitial(null), '?');
    // Older browsers still preserve a complete supplementary-plane code point.
    const fallbackContext = browserContext();
    vm.runInContext('Intl.Segmenter = undefined', fallbackContext);
    const fallback = loadScripts(fallbackContext, appScripts(), 'avatarInitial');
    assert.equal(fallback('😀 Author'), '😀');
    assert.equal(fallback('Alice'), 'A');
});

test('all avatar templates expose load and error handling, including reply and profile initials', () => {
    const html = pageHtml();
    const images = html.match(/<img\b[^>]*:src="avatarUrl\([^>]*>/g) || [];
    assert.equal(images.length, 20);
    for (const image of images) {
        assert.ok(image.includes('@load="avatarLoaded($el)"'));
        assert.ok(image.includes('@error="avatarFailed($el)"'));
        assert.ok(image.includes('class="avatar-image '));
    }
    const initialPairs = html.match(/<span[^>]*x-text="(?:getInitial\([^\"]*|getQuoteAuthor\([^\"]*\.initial|profileCard\?\.initial|avatarInitial\([^\"]*)"[^>]*><\/span>\s*<img[^>]*:src="avatarUrl/g) || [];
    assert.equal(initialPairs.length, 16);
    for (const pair of initialPairs) assert.ok(pair.includes('class="avatar-initial"'));
    // Feed cards (main and quote avatars) render in app.js; capture listeners on the
    // list handle their load and error events.
    const { app } = feedListApp();
    const card = parseFakeHTML(app.renderFeedCard(JSON.parse(JSON.stringify(WEB_FIXTURE.slim[0]))))[0];
    const avatars = card.querySelectorAll('img.avatar-image');
    assert.equal(avatars.length, 2);
    for (const avatar of avatars) assert.ok(avatar.parentElement.firstElementChild.matches('span.avatar-initial'));
});

test('tweet text and link cards open expanded destinations instead of t.co', () => {
    const context = browserContext();
    const app = immediateComponent(loadScripts(context, appScripts(), 'tweetApp')());
    const entity = { url: 'https://t.co/late', expanded_url: 'https://late.example/post', display_url: 'late.example/post' };
    const longText = `${'word '.repeat(70)}see https://t.co/late`;
    const note = { tweet_id: '1', text: longText, raw_json: { rest_id: '1', legacy: { full_text: 'word', entities: { urls: [entity] } } } };
    const body = app.formatText(note, true);
    assert.match(body, /href="https:\/\/late\.example\/post"[^>]*>late\.example\/post<\/a>$/);
    assert.doesNotMatch(body, /t\.co/);

    const card = (bindings, legacy = {}) => app.renderCard({ tweet_id: '2', raw_json: {
        rest_id: '2',
        legacy: { full_text: 'https://t.co/card', entities: { urls: [{ url: 'https://t.co/card', expanded_url: 'https://story.example/a' }] }, ...legacy },
        card: { name: 'summary', binding_values: bindings },
    } });
    const title = { key: 'title', value: { string_value: 'Story' } };
    assert.match(card([title, { key: 'card_url', value: { string_value: 'https://t.co/card' } }]), /href="https:\/\/story\.example\/a"/);
    assert.match(card([title, { key: 'card_url', value: { string_value: 'https://t.co/other' } }]), /href="https:\/\/t\.co\/other"/,
        'an unmatched short link still opens');
});

test('media quoting media is compact in lists and flush in the detail view', () => {
    const { app, list } = feedListApp();
    const author = { id: '2', username: 'a', display_name: 'A' };
    const photo = { type: 'photo', download: { local_path: 'media/outer.jpg' } };
    const video = {
        type: 'video',
        duration_millis: 21000,
        download: { local_path: 'media/q.mp4', thumbnail_local_path: 'media/q.jpg' },
    };
    const quoted_tweet = {
        tweet_id: '3',
        text: `quote ${'word '.repeat(80)}`,
        author: { username: 'q', display_name: 'Q' },
        raw_json: { in_reply_to_screen_name: 'frank' },
    };
    const doubleMedia = { tweet_id: '1', text: 'x', author, media: [photo], quoted_tweet, qt_media: [video] };

    assert.equal(app.isMediaQuotingMedia(doubleMedia), true);
    assert.equal(app.isMediaQuotingMedia({ media: [], qt_media: [video] }), false);
    assert.equal(app.isMediaQuotingMedia({ media: [photo] }), false);
    assert.equal(app.isMediaQuotingMedia(null), false);

    // Lists: a thumbnail with the duration badge beside the reply line and clamped
    // text, never Show more.
    const compact = app.renderFeedCard(doubleMedia);
    assert.ok(compact.includes(app.renderQuoteCard(doubleMedia)), 'feed quote markup comes from the shared renderer');
    assert.match(compact, /cursor-pointer quote-compact" data-thread-id="3" data-action="quote"/);
    assert.match(compact, /<div class="quote-reply tweet-reply-context text-\[15px\][^"]*">Replying to <span[^>]*data-username="frank">@frank<\/span>/);
    assert.match(compact, /<div class="quote-media"><div class="quote-thumbnail"><img src="\/media\/q\.jpg" loading="lazy" decoding="async" alt=""><span class="quote-thumbnail-badge quote-thumbnail-duration">0:21<\/span><\/div><\/div>/);
    assert.match(compact, /class="quote-text text-\[15px\]/);
    assert.doesNotMatch(compact, /media-video-player|expand-tweet/);

    // A quote under a tweet without media keeps the full media grid.
    const regular = app.renderFeedCard({ ...doubleMedia, tweet_id: '4', media: [] });
    assert.doesNotMatch(regular, /quote-compact|quote-thumbnail/);
    assert.match(regular, /media-video-player/);
    assert.match(regular, /expand-tweet/);

    // The thumbnail has no controls, so a click opens the quoted tweet.
    app.tweets = [doubleMedia];
    app.renderFeedList(app.tweets);
    let opened;
    app.openThread = id => { opened = id; };
    dispatchFeed(list, 'click', list.children[0].querySelector('.quote-thumbnail img'));
    assert.equal(opened, '3');

    // Several items tile in a small grid of up to four, without badges.
    const pair = app.renderQuoteThumbnail([{ type: 'photo', download: { local_path: 'media/p.jpg' } }, video]);
    assert.match(
        pair,
        /^<div class="quote-thumbnail quote-thumbnail-grid"><div class="quote-thumbnail-cell"><img src="\/media\/p\.jpg"[^>]*><\/div><div class="quote-thumbnail-cell"><img src="\/media\/q\.jpg"[^>]*><\/div><\/div>$/,
    );
    const videos = app.renderQuoteThumbnail(Array(5).fill(video));
    assert.equal((videos.match(/class="quote-thumbnail-cell"/g) || []).length, 4);
    assert.doesNotMatch(videos, /quote-thumbnail-badge/);
    const gif = app.renderQuoteThumbnail([{ type: 'animated_gif', download: { local_path: 'media/g.mp4' } }]);
    assert.match(gif, /<video src="\/media\/g\.mp4#t=0\.1" preload="metadata" muted playsinline><\/video><span class="quote-thumbnail-badge">GIF<\/span>/);
    assert.doesNotMatch(gif, /autoplay|controls/);
    assert.match(app.renderQuoteThumbnail([{ type: 'video' }]), /aria-label="Media not downloaded"/);
    assert.equal(app.renderQuoteThumbnail([]), '');

    const html = pageHtml();
    for (const name of ['child', 'reply']) {
        assert.equal(html.split(`:class="{ 'quote-compact': isMediaQuotingMedia(${name}) }"`).length - 1, 2);
        assert.equal(html.split(`isMediaQuotingMedia(${name}) ? renderQuoteThumbnail(${name}.qt_media) : renderMediaGrid(${name}.qt_media, 'quote', getQuoteTweet(${name}))`).length - 1, 2);
        assert.equal(html.split(`@click="if (!isMediaQuotingMedia(${name})) stopMediaClick($event)"`).length - 1, 2);
        const replyLines = html.split(`<template x-if="getReplyRecipients({raw_json: getQuoteTweet(${name})}).length">`).slice(1);
        assert.equal(replyLines.length, 2);
        for (const line of replyLines) {
            assert.ok(line.includes('class="quote-reply tweet-reply-context'));
            assert.ok(line.includes(`renderReplyRecipientContent({raw_json: getQuoteTweet(${name})})`));
        }
    }
    for (const main of ['threadData?.main', 'panelThreadData?.main']) {
        const mainQuote = html.split(`:data-thread-id="getTweetId(getQuoteTweet(${main}))"`)[0].split('\n').at(-1);
        assert.match(mainQuote, /class="quote-card quote-card-detail /);
        const mediaOwner = main === 'threadData?.main' ? 'threadData' : 'panelThreadData';
        assert.ok(html.includes(`class="main-tweet-quote" :class="{ 'main-tweet-quote-after-single-media': (${mediaOwner}.main.media || []).length === 1 }"`));
        assert.ok(html.includes(`<div class="quote-media" x-html="renderMediaGrid(${main}?.qt_media, (${mediaOwner}.main.media || []).length ? 'quote-detail-expanded' : 'quote-detail', getQuoteTweet(${mediaOwner}.main))" @click="stopMediaClick($event)"></div>`));
    }
    assert.equal((html.match(/renderMediaGrid\((?:threadData|panelThreadData)\.main\.media, 'detail', (?:threadData|panelThreadData)\.main\)/g) || []).length, 2);
    const css = pageCss();
    assert.match(css, /\.quote-compact > \.quote-text \{[^}]*-webkit-line-clamp: 4;/);
    assert.match(css, /\.quote-compact \{[^}]*grid-template-columns: calc\(\(100% \+ 12px\) \/ 5\) minmax\(0, 1fr\);/);
    assert.match(css, /\.quote-compact > \.quote-media \{[^}]*margin-top: 4px;/);
    assert.match(css, /\.quote-compact:not\(:has\(> \.quote-reply\)\) > \.quote-text \{[^}]*margin-top: 8px;/);
    assert.match(css, /\.quote-compact \.quote-thumbnail \{[^}]*width: 100%;[^}]*aspect-ratio: 1;[^}]*border-radius: 16px;/);
    assert.match(css, /\.quote-compact > \.quote-reply \{[^}]*grid-row: 2;/);
    assert.match(css, /\.quote-thumbnail-cell:first-child:nth-last-child\(3\) \{[^}]*grid-row: span 2;/);
    assert.match(css, /\.quote-card:not\(\.quote-compact\) > \.quote-media:not\(:empty\) \{[^}]*margin: 16px -12px 0;/);
});

test('demo hides captured media and quote links while preserving ordinary links', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    vm.runInContext(fs.readFileSync(path.join(ROOT, 'demo', 'jobs-replay.js'), 'utf8'), context);
    vm.runInContext(fs.readFileSync(path.join(ROOT, 'demo', 'search-unicode.js'), 'utf8'), context);
    vm.runInContext(fs.readFileSync(path.join(ROOT, 'demo', 'search.js'), 'utf8'), context);
    vm.runInContext(fs.readFileSync(path.join(ROOT, 'demo', 'demo-api.js'), 'utf8'), context);
    const fixture = JSON.parse(fs.readFileSync(path.join(ROOT, 'demo', 'data.json'), 'utf8'));
    const store = context.window.TweetNookDemo.createStore(fixture);
    const app = immediateComponent(tweetApp());
    const mediaOnly = store.hydrate('2087274031749927040');
    assert.equal(app.formatText(mediaOnly, true), '');
    assert.equal(mediaOnly.text, 'https://t.co/iJmE5Nu84I');
    assert.equal(mediaOnly.media.length, 1);
    for (const source of fixture.tweets) {
        const html = app.formatText(store.hydrate(source.tweet_id), true);
        for (const url of [...(source.media_short_urls || []), source.quote_short_url].filter(Boolean)) {
            assert.ok(!html.includes(url), `${source.tweet_id} displays attachment URL ${url}`);
        }
    }
    const mixed = fixture.tweets.find(tweet => tweet.tweet_id === '2087274031749927040');
    mixed.text = 'Read https://t.co/story https://t.co/iJmE5Nu84I';
    mixed.urls = [{url: 'https://t.co/story', expanded_url: 'https://example.com/story', display_url: 'example.com/story'}];
    const regular = app.formatText(context.window.TweetNookDemo.createStore(fixture).hydrate(mixed.tweet_id), true);
    assert.match(regular, /href="https:\/\/example.com\/story"/);
    assert.match(regular, />example.com\/story<\/a>/);
    assert.ok(!regular.includes('https://t.co/iJmE5Nu84I'));
    mixed.text = 'A quote https://t.co/quote';
    mixed.quote_short_url = 'https://t.co/quote';
    assert.equal(app.formatText(context.window.TweetNookDemo.createStore(fixture).hydrate(mixed.tweet_id), true), 'A quote');
});

test('single media layout distinguishes portraits, square boundaries, GIFs and quotes', () => {
    const { app } = feedListApp();
    const layout = (type, width, height, context = 'feed', extra = {}) =>
        app.mediaFrameLayout({ type, width, height, ...extra }, context);

    // Captured square and landscape photos must fill detail columns, even when
    // their width/ratio would otherwise produce a height above 510px.
    for (const context of ['feed', 'detail', 'quote', 'quote-detail']) {
        assert.equal(layout('photo', 2400, 2400, context).maxWidth, null);
        assert.equal(layout('photo', 1200, 1168, context).photoCapWidth, null);
        assert.equal(layout('photo', 1200, 1168, context).maxWidth, null);
        assert.equal(layout('video', 1080, 1080, context).maxWidth, null);
    }
    const portrait = layout('photo', '1028', '1275', 'detail');
    assert.ok(Math.abs(portrait.maxWidth - 411.2) < 0.00001);
    assert.equal(portrait.photoCapWidth, portrait.maxWidth);
    assert.equal(layout('photo', 999, 1000, 'detail').maxWidth, 509.49);
    assert.equal(layout('photo', 1000, 1000, 'detail').maxWidth, null);
    assert.equal(layout('photo', 1001, 1000, 'detail').maxWidth, null);

    // Quoted photo previews crop very tall sources to 3:4. Only list previews
    // retain the 510px cap; a focal quote fills its available detail column.
    for (const context of ['quote', 'quote-detail']) {
        const quoted = layout('photo', 1536, 2048, context);
        assert.equal(quoted.maxWidth, null);
        assert.equal(quoted.photoCapWidth, context === 'quote' ? 382.5 : null);
        assert.equal(quoted.frameAspect, 0.75);
        assert.equal(quoted.portraitPhoto, context === 'quote');
        assert.equal(layout('video', 720, 1280, context).frameAspect, 16 / 9);
        assert.equal(layout('animated_gif', 498, 402, context).frameAspect, 16 / 9);
    }
    for (const [width, height] of [[360, 640], [600, 900], [750, 1000], [800, 1000]]) {
        const previewAspect = Math.max(width / height, 3 / 4);
        for (const context of ['quote', 'quote-detail', 'quote-detail-expanded']) {
            const photo = layout('photo', width, height, context);
            assert.equal(photo.sourceAspect, width / height);
            assert.equal(photo.frameAspect, previewAspect);
            assert.equal(photo.photoCapWidth, context === 'quote' ? 510 * previewAspect : null);
        }
        assert.equal(layout('photo', width, height).frameAspect, width / height);
    }
    const expandedQuote = layout('photo', 2816, 3072, 'quote-detail-expanded');
    assert.equal(expandedQuote.frameAspect, 2816 / 3072);
    assert.equal(expandedQuote.maxWidth, null);
    assert.equal(expandedQuote.photoCapWidth, null);
    assert.equal(expandedQuote.portraitPhoto, false);
    assert.equal(layout('video', 1639, 2048, 'quote-detail-expanded').frameAspect, 16 / 9);
    assert.equal(layout('animated_gif', 1639, 2048, 'quote-detail-expanded').frameAspect, 16 / 9);
    assert.ok(Math.abs(layout('video', 720, 742).maxWidth - 494.8787062) < 0.00001);
    assert.equal(layout('video', 480, 472).maxWidth, null);
    assert.equal(layout('animated_gif', 404, 512).frameAspect, 1);
    assert.equal(layout('animated_gif', 404, 512).maxWidth, null);
    assert.equal(layout('animated_gif', 746, 640).frameAspect, 1.165625);
    assert.equal(layout('photo', 637, 130).frameAspect, 4.9);
    assert.equal(layout('photo', 1634, 67).frameAspect, 5);

    // Playback metadata can differ from a poster; missing or invalid dimensions
    // must not leave a collapsed frame or produce invalid CSS values.
    const playback = layout('video', 1600, 900, 'detail', { aspect_ratio: [3, 4] });
    assert.equal(playback.sourceAspect, 0.75);
    assert.equal(playback.maxWidth, 382.5);
    assert.equal(layout('video', null, null, 'feed', { aspect_ratio: [3, 4] }).frameAspect, 0.75);
    for (const dimensions of [[null, null], [0, 100], [-2, 100], ['bad', 100], [Infinity, 2]]) {
        assert.equal(layout('photo', ...dimensions).frameAspect, 16 / 9);
        assert.equal(layout('animated_gif', ...dimensions).frameAspect, 1);
    }
    for (const aspect_ratio of [[true, 1], [0, 1], [1, -1], [Infinity, 1], [1], '3:4']) {
        assert.equal(layout('video', 16, 9, 'feed', { aspect_ratio }).frameAspect, 16 / 9);
    }
});

test('saved media and Articles remain readable with unavailable warnings and no text', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, appScriptsWith('articles.js'), '({ tweetApp })');
    const app = immediateComponent(tweetApp());
    const missing = {
        tweet_id: 'saved', text: '', raw_json: null, media: [],
        availability: { state: 'unavailable', placeholder: false, reason: 'deleted_by_author',
            message: 'This tweet was deleted by its author.' },
    };
    for (const content of [
        { media: [{ type: 'photo', download: { local_path: 'media/saved.jpg' } }] },
        { article: { title: 'Saved article', content_text: 'Saved article body', status: 'body_present' } },
        { card_media: [{ download: { local_path: 'media/card.jpg' } }] },
    ]) {
        const tweet = { ...missing, ...content };
        assert.equal(app.isPlaceholderTweet(tweet), false);
        assert.equal(app.isTextPlaceholderTweet(tweet), false);
        assert.doesNotMatch(app.formatText(tweet), /tweet-skeleton/);
        assert.match(app.missingBadge(tweet), /deleted by its author/);
        assert.notEqual(app.renderActionBar(tweet), '');
    }
    assert.equal(app.isTextPlaceholderTweet(missing), true);
    assert.equal(app.isPlaceholderTweet({ ...missing, availability: { placeholder: true } }), true);
});

test('card thumbnails only render matching archived images or posters', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, appScripts(), '({ tweetApp })');
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

test('link cards show archived large images and summary thumbnails in normalized quotes', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, appScripts(), '({ tweetApp })');
    const app = immediateComponent(tweetApp());
    const remote = 'https://example.com/preview.jpg';
    for (const name of ['summary', 'summary_large_image']) {
        const tweet = {
            raw_json: { card: { legacy: { name, binding_values: [
                { key: 'title', value: { string_value: 'A saved story' } },
                { key: 'thumbnail_image_original', value: { image_value: { url: remote } } },
            ] } } },
            card_media: [{ url: remote, download: { local_path: 'media/1/card image.jpg' } }],
            media: [],
        };
        for (const input of [tweet, { raw_json: tweet }]) {
            const html = app.renderCard(input);
            assert.match(html, /<img src="\/media\/1\/card%20image.jpg" alt=""/);
            assert.match(html, /A saved story/);
            assert.equal(html.includes('link-card-summary'), name === 'summary');
            assert.doesNotMatch(html, /src="https?:/);
        }
        tweet.card_media = [];
        assert.doesNotMatch(app.renderCard(tweet), /<img/);
        assert.match(app.renderCard(tweet), /A saved story/);
        tweet.card_media = [{ url: remote, download: { local_path: 'https://host/remote.jpg' } }];
        assert.doesNotMatch(app.renderCard(tweet), /<img/);
    }
});

test('media grid sources and posters are escaped inside their attributes', () => {
    const app = immediateComponent(loadScripts(browserContext(), appScripts(), 'tweetApp')());
    const odd = 'media/1/a"b&c<d>.jpg';
    const photo = { type: 'photo', width: 10, height: 10, download: { local_path: odd } };
    const video = { type: 'video', download: { local_path: 'media/1/v"x.mp4', thumbnail_local_path: odd } };
    const escaped = '/media/1/a&quot;b&amp;c&lt;d&gt;.jpg';
    for (const markup of [app.renderMediaGrid([photo]), app.renderMediaGrid([photo, video]), app.renderMediaGrid([video])]) {
        assert.doesNotMatch(markup, /a"b|v"x|&c<d>/);
    }
    assert.ok(app.renderMediaGrid([photo]).includes(`<img src="${escaped}"`));
    const single = app.renderMediaGrid([video]);
    assert.ok(single.includes('src="/media/1/v&quot;x.mp4"'));
    assert.ok(single.includes(`poster="${escaped}"`));
    assert.ok(app.renderMediaGrid([photo, video]).includes(`<img src="${escaped}"`));
});

test('feed media loads lazily and tweets render without mutating their data', () => {
    const app = immediateComponent(loadScripts(browserContext(), appScripts(), 'tweetApp')());
    const photo = { type: 'photo', width: 10, height: 10, download: { local_path: 'media/1/p.jpg' } };
    const video = { type: 'video', download: { local_path: 'media/1/v.mp4', thumbnail_local_path: 'media/1/v.jpg' } };
    const bare = { type: 'video', download: { local_path: 'media/1/w.mp4' } };
    assert.match(app.renderMediaGrid([photo]), /<img src="\/media\/1\/p\.jpg" loading="lazy" decoding="async"/);
    assert.match(app.renderMediaGrid([photo, video]), /loading="lazy" decoding="async"/);
    assert.match(app.renderMediaGrid([video]), /preload="none"/);
    assert.match(app.renderMediaGrid([bare]), /preload="metadata"/);

    const quoted = { rest_id: '2', legacy: { full_text: 'q' } };
    const tweet = {
        tweet_id: '1', collections: ['bookmark'], collection: { type: 'like' },
        raw_json: { quoted_status_result: { result: { __typename: 'TweetWithVisibilityResults', birdwatch_pivot: { shorttitle: 'n' }, tweet: quoted } } },
    };
    assert.equal(app.getQuoteTweet(tweet).birdwatch_pivot.shorttitle, 'n');
    assert.equal('birdwatch_pivot' in quoted, false);
    app.renderActionBar(tweet);
    app.renderActionBar(tweet);
    assert.deepEqual(Array.from(tweet.collections), ['bookmark']);
});

test('gallery preference defaults to the post date and changes the already rendered feed', () => {
    const context = browserContext();
    const app = immediateComponent(loadScripts(context, appScripts(), 'tweetApp')());
    assert.equal(app.mediaGallery, 'date');
    const calls = [];
    app.tweets = [{tweet_id: 'keep'}];
    app.renderFeedList = tweets => calls.push(tweets);
    app.setMediaGallery('carousel');
    assert.equal(context.localStorage.getItem('tvx-media-gallery'), 'carousel');
    assert.equal(calls[0], app.tweets);
    app.setMediaGallery('carousel');
    assert.equal(calls.length, 1);
    app.setMediaGallery('grid');
    assert.equal(context.localStorage.getItem('tvx-media-gallery'), 'grid');
    assert.equal(calls.length, 2);
    app.setMediaGallery('unknown');
    assert.equal(app.mediaGallery, 'date');
    assert.equal(context.localStorage.getItem('tvx-media-gallery'), 'date');
    const html = pageHtml();
    assert.match(html, /class="nook-media-tiles" role="radiogroup" aria-label="Multi-media layout"/);
    assert.match(html, /id="media-date-description">Grid for tweets posted before July 10, 2026; carousel from then on\.</);
    assert.match(html, /role="radio" class="nook-theme-tile nook-media-tile" aria-label="Carousel" :aria-checked="mediaGallery === 'carousel'" @click="setMediaGallery\('carousel'\)"/);
    assert.match(html, /role="radio" class="nook-theme-tile nook-media-tile" aria-label="By post date" aria-describedby="media-date-description" :aria-checked="mediaGallery === 'date'" @click="setMediaGallery\('date'\)"/);
});

test('date-based media layout uses the UTC launch day and ignores archive dates', () => {
    const app = immediateComponent(loadScripts(browserContext(), appScripts(), 'tweetApp')());
    app.mediaGallery = 'date';
    const photos = Array(2).fill({type:'photo', width:480, height:480, download:{local_path:'media/p.webp'}});
    const hasCarousel = created_at => app.renderMediaGrid(photos, 'feed', {created_at}).includes('media-carousel-track');
    assert.equal(hasCarousel('2026-07-09T23:59:59.999Z'), false);
    assert.equal(hasCarousel('2026-07-10T00:00:00Z'), true);
    assert.equal(hasCarousel('2026-07-09T20:00:00-04:00'), true);
    assert.equal(hasCarousel('2026-07-10T00:59:59+01:00'), false);
    assert.equal(hasCarousel('Fri Jul 10 00:00:00 +0000 2026'), true);
    for (const created of [undefined, '', 'unknown', null]) assert.equal(hasCarousel(created), false);
    assert.equal(app.useCarouselForTweet({created_at:'2020-01-01T00:00:00Z', synced_at:'2026-10-20T00:00:00Z'}), false);
    assert.equal(app.useCarouselForTweet({synced_at:'2026-10-20T00:00:00Z'}), false);
    const single = app.renderMediaGrid(photos.slice(0, 1), 'feed', {created_at:'2020-01-01'});
    assert.equal(app.renderMediaGrid(photos.slice(0, 1), 'feed', {created_at:'2026-10-20'}), single);
    app.mediaGallery = 'carousel';
    assert.equal(hasCarousel('2020-01-01T00:00:00Z'), true);
    app.mediaGallery = 'grid';
    assert.equal(hasCarousel('2026-10-20T00:00:00Z'), false);
});

test('quoted and reposted media use their original posting dates independently', () => {
    const app = immediateComponent(loadScripts(browserContext(), appScripts(), 'tweetApp')());
    app.mediaGallery = 'date';
    const photos = Array(2).fill({type:'photo', width:480, height:480, download:{local_path:'media/p.webp'}});
    const parent = {tweet_id:'1', text:'New quote', created_at:'2026-10-20T00:00:00Z', media:photos,
        qt_media:photos, quoted_tweet:{id_str:'2', text:'Old photos', legacy:{created_at:'Wed Jan 01 00:00:00 +0000 2020'}}};
    assert.match(app.renderMediaGrid(parent.media, 'detail', parent), /media-carousel-track/);
    assert.doesNotMatch(app.renderQuoteCard({...parent, media:[]}), /media-carousel-track/);
    parent.quoted_tweet.legacy.created_at = 'Fri Jul 10 00:00:00 +0000 2026';
    assert.match(app.renderQuoteCard({...parent, media:[]}), /media-carousel-track/);
    const repost = {tweet_id:'3', text:'Repost', author:{}, media:photos, created_at:parent.created_at,
        raw_json:{retweeted_status:{id_str:'4', full_text:'Original', created_at:'2020-01-01T00:00:00Z'}}};
    assert.equal(app.useCarouselForTweet(repost), false);
    assert.doesNotMatch(app.renderFeedCard(repost), /media-carousel-track/);
    delete repost.raw_json.retweeted_status.created_at;
    assert.doesNotMatch(app.renderFeedCard(repost), /media-carousel-track/, 'an undated original must not borrow its newer repost date');
    repost.raw_json.retweeted_status.created_at = '2026-10-01T00:00:00Z';
    repost.created_at = '2020-01-01T00:00:00Z';
    assert.equal(app.useCarouselForTweet(repost), true);
});

test('carousel uses shared heights, native pair packing, and independent quoted proportions', () => {
    const app = immediateComponent(loadScripts(browserContext(), appScripts(), 'tweetApp')());
    const portrait = {type:'photo', width:360, height:640};
    const landscape = {type:'photo', width:640, height:360};
    const square = {type:'photo', width:480, height:480};
    const panorama = {type:'photo', width:1000, height:160};
    assert.equal(app.mediaCarouselLayout([portrait, landscape]).height,
        `calc((100cqw - 4px) / ${9 / 16 + 16 / 9})`);
    assert.equal(app.mediaCarouselLayout([square, square]).height, 'calc((100cqw - 4px) / 2)');
    assert.match(app.mediaCarouselLayout([landscape, landscape]).height, /^max\(68cqw,/);
    assert.match(app.mediaCarouselLayout([portrait, landscape, square], 'quote').height, /^min\(66cqw, max\(57\.8cqw,/);
    assert.match(app.mediaCarouselLayout([square, square, square], 'quote').height, /\/ 1\.67\)/);
    assert.equal(app.mediaCarouselLayout([portrait, panorama]).maxAspect, 9 / 4);
    assert.equal(app.mediaCarouselLayout([portrait, panorama]).height, 'calc((100cqw - 20px) / 2.8125)');
    assert.ok(app.mediaCarouselLayout([portrait, portrait, portrait], 'quote').height.startsWith('min(66cqw,'));
    assert.ok(app.mediaCarouselLayout([portrait, portrait, portrait]).height.includes(String(1.67 * 9 / 16)));
    assert.ok(Number.isFinite(app.mediaCarouselLayout([{}, {}]).maxAspect));
});

test('phone layout follows the X app: strip heights, dates, large link cards, compact nested quotes', () => {
    const context = browserContext();
    const app = immediateComponent(loadScripts(context, appScripts(), 'tweetApp')());
    const portrait = {type:'photo', width:360, height:640};
    const landscape = {type:'photo', width:640, height:360};
    assert.equal(app.mediaCarouselLayout([portrait, landscape, landscape]).appHeight,
        `clamp(68cqw, calc(59.5cqw / ${9 / 16}), 100cqw)`);
    const pair = app.mediaCarouselLayout([portrait, landscape]);
    assert.equal(pair.appHeight, pair.height);
    assert.equal(app.mediaCarouselLayout([portrait, portrait, portrait], 'quote').appHeight, null);

    assert.equal(app.formatAppDate(new Date(Date.now() - 3 * 86400e3 - 5000).toISOString()), '3d');
    assert.equal(app.formatAppDate(new Date(Date.now() - 2 * 3600e3 - 5000).toISOString()), '2h');
    assert.equal(app.dateLabel('2024-01-05T12:00:00Z'),
        '<span class="date-web">Jan 5, 2024</span><span class="date-app">1/5/24</span>');
    assert.match(app.dateLabel('2024-01-05T12:00:00Z', true), /<span class="date-app">[^<]+ · 1\/5\/24<\/span>/);

    const html = app.renderCard({
        raw_json: {card: {name: 'summary_large_image', binding_values: {
            title: {string_value: 'Headline <b>'}, vanity_url: {string_value: 'example.com'},
            thumbnail_image_large: {image_value: {url: 'https://pbs.example/card.jpg'}},
        }}},
        card_media: [{thumbnail_url: 'https://pbs.example/card.jpg', download: {thumbnail_local_path: 'media/cards/card.jpg'}}],
    });
    assert.match(html, /class="link-card link-card-large /);
    assert.match(html, /<span class="link-card-overlay-title">Headline &lt;b&gt;<\/span><\/div>/);
    assert.match(html, /<div class="link-card-from">From example\.com<\/div>/);

    const outer = {quote: {quote: {}}, qt_media: [{type: 'photo'}]};
    app.getQuoteTweet = tweet => tweet?.quote;
    app.isTombstone = () => false;
    app.articleForTweet = () => null;
    assert.equal(app.isMediaQuotingMedia(outer), false);
    context.matchMedia = query => ({matches: query === '(max-width: 640px)'});
    assert.equal(app.isMediaQuotingMedia(outer), true);
    assert.equal(app.isMediaQuotingMedia({...outer, quote: {}}), false);
});

test('phone carousel scrolls freely by touch and its padding opens the post', () => {
    const css = pageCss();
    // pointer-events: none on a scroller stops iOS touch scrolling even when its children accept touches.
    assert.doesNotMatch(css, /\.media-carousel-track \{ pointer-events: none; \}/);
    // Its padding opens the post, so it keeps the post's pointer cursor.
    assert.doesNotMatch(css.match(/\.media-carousel-track \{[^}]*\}/)[0], /cursor/);
    assert.match(css, /@media \(max-width: 640px\)[\s\S]*\.media-carousel-track \{\s*scroll-snap-type: none;\s*\}/);
    assert.match(css, /@media \(hover: none\) \{\s*\.media-carousel-arrow \{ display: none; \}/);

    const app = immediateComponent(loadScripts(browserContext(), appScripts(), 'tweetApp')());
    const opened = [];
    app.openThread = id => opened.push(id);
    const node = (classes, dataset = {}, parentElement = null) => ({
        tagName: 'DIV', dataset, parentElement,
        classList: {contains: name => classes.includes(name)},
        closest(selector) {
            const matches = (el, part) => part === '[data-lightbox-media]'
                ? 'lightboxMedia' in el.dataset : el.classList.contains(part.slice(1));
            for (let el = this; el; el = el.parentElement) {
                if (selector.split(',').some(part => matches(el, part.trim()))) return el;
            }
            return null;
        },
    });
    // The feed's markup: the carousel carries the lightbox data inside the media wrapper.
    const list = node([]);
    const card = node([], {action: 'thread', threadId: '7'}, list);
    const media = node([], {action: 'media'}, card);
    const carousel = node(['media-carousel'], {action: 'stop', lightboxMedia: '[]'}, media);
    const track = node(['media-carousel-track'], {}, node(['media-carousel-nav'], {}, carousel));
    const item = node(['media-carousel-item'], {}, track);
    const click = target => {
        let stopped = false;
        app.handleFeedClick({target, stopPropagation: () => { stopped = true; }}, list);
        return stopped;
    };
    assert.equal(click(track), false);
    assert.deepEqual(opened, ['7']);
    assert.equal(click(item), true);
    assert.deepEqual(opened, ['7']);

    // A quoted carousel bleeds into the quote's padding, which opens the quote.
    const quote = node(['quote-card'], {action: 'quote', threadId: '9'}, card);
    const quoteMedia = node(['quote-media'], {action: 'stop'}, quote);
    const quoteCarousel = node(['media-carousel'], {action: 'stop', lightboxMedia: '[]'}, quoteMedia);
    const quoteTrack = node(['media-carousel-track'], {}, node(['media-carousel-nav'], {}, quoteCarousel));
    const quoteItem = node(['media-carousel-item'], {}, quoteTrack);
    assert.equal(click(quoteTrack), true);
    assert.deepEqual(opened, ['7', '9']);
    assert.equal(click(quoteItem), true);
    assert.deepEqual(opened, ['7', '9']);

    // Thread views wrap x-html media in stopMediaClick, which lets the same gaps through.
    const stops = target => {
        let stopped = false;
        app.stopMediaClick({target, stopPropagation: () => { stopped = true; }});
        return stopped;
    };
    assert.equal(stops(track), false);
    assert.equal(stops(item), true);
    assert.equal(stops(node([], {}, media)), true);
    const html = pageHtml();
    assert.doesNotMatch(html, /x-html="[^"]*renderMediaGrid\([^"]*"[^>]*@click\.stop/);
    assert.doesNotMatch(html, /renderMediaGrid\([^"]*"[^>]*\$event\.stopPropagation\(\)/);
    // Alpine binds handlers inside x-html, so the carousel itself must not stop clicks.
    app.mediaGallery = 'carousel';
    const photos = Array(2).fill({type: 'photo', width: 480, height: 480, download: {local_path: 'media/p.webp'}});
    const strip = app.renderMediaGrid(photos, 'feed', null);
    assert.match(strip, /media-carousel-track/);
    assert.doesNotMatch(strip.match(/<div class="media-carousel [^>]*>/)[0], /@click/);
});

test('phone avatar shrinks to clear a scrolled carousel and restores at the first photo', () => {
    const context = browserContext();
    context.window.matchMedia = query => ({matches: query.includes('(max-width: 640px)')});
    const app = loadScripts(context, appScripts(), 'window');
    const avatar = (() => {
        const classes = new Set(['avatar']);
        const props = {};
        return {
            offsetHeight: 48, props, classes,
            getBoundingClientRect: () => ({top: 159}),
            classList: {contains: name => classes.has(name), toggle: (name, on) => on ? classes.add(name) : classes.delete(name)},
            style: {setProperty: (name, value) => { props[name] = value; }, getPropertyValue: name => props[name] || ''},
        };
    })();
    const buttons = {'.media-carousel-previous': {}, '.media-carousel-next': {}};
    const track = (stripTop, quote = false) => ({
        scrollLeft: 0, scrollWidth: 900, clientWidth: 430,
        firstElementChild: {getBoundingClientRect: () => ({top: stripTop})},
        parentElement: {querySelector: selector => buttons[selector]},
        closest: selector => selector === '.quote-card' ? (quote ? {} : null)
            : {querySelector: () => avatar},
    });
    const strip = track(199);
    strip.scrollLeft = 40;
    app.tweetNookGalleryScroll(strip);
    assert.equal(avatar.classes.has('avatar-yield'), true);
    assert.equal(avatar.props['--avatar-yield-scale'], (34 / 48).toFixed(3), 'X: strip top - avatar top - 6pt');
    assert.equal(avatar.props['--avatar-yield-shift'], `${(18 - 34 / 2).toFixed(2)}px`, 'centered 18pt below the resting top');
    strip.scrollLeft = 0;
    app.tweetNookGalleryScroll(strip);
    assert.equal(avatar.classes.has('avatar-yield'), false);

    const low = track(260);
    low.scrollLeft = 40;
    delete avatar.props['--avatar-yield-scale'];
    app.tweetNookGalleryScroll(low);
    assert.equal(avatar.props['--avatar-yield-scale'], '1.000');
    assert.equal(avatar.classes.has('avatar-yield'), false, 'strips 54pt or more below the avatar leave it alone');
    const quoted = track(199, true);
    quoted.scrollLeft = 40;
    app.tweetNookGalleryScroll(quoted);
    assert.equal(avatar.classes.has('avatar-yield'), false, 'quoted strips stay inside their card');

    const dots = [0, 1, 2, 3].map(() => {
        const props = {};
        return {props, style: {setProperty: (name, value) => { props[name] = value; }}};
    });
    const dotted = track(207);
    dotted.parentElement.parentElement = {querySelector: selector =>
        selector === ':scope > .media-carousel-dots' ? {children: dots} : null};
    dotted.scrollLeft = 470 * 1.5 / 3;  // halfway between the second and third photo
    app.tweetNookGalleryScroll(dotted);
    assert.deepEqual(dots.map(dot => dot.props['--dot-active']), ['0.000', '0.500', '0.500', '0.000']);

    const css = pageCss();
    assert.match(css, /\.avatar\.avatar-yield \{\s*transform: translateY\(var\(--avatar-yield-shift, 0\)\) scale\(var\(--avatar-yield-scale, 1\)\);/);
    assert.match(css, /\.feed-card \[data-action="media"\] > \.mt-3 \{\s*margin-top: 8px;/);
    assert.match(css, /\.media-carousel-dots > span \{\s*width: calc\(12px \+ 10px \* var\(--dot-active, 0\)\);/);
    assert.match(css, /@media \(prefers-reduced-motion: no-preference\) \{[^}]*\.thread-row > \.avatar \{\s*transition: transform 160ms ease;/);
});

test('optional carousel preserves original indices, placeholders, GIFs, and single-media markup', () => {
    const context = browserContext();
    const app = immediateComponent(loadScripts(context, appScripts(), 'tweetApp')());
    const photo = {type:'photo', width:360, height:640, download:{local_path:'media/p.webp'}};
    const gif = {type:'animated_gif', width:480, height:480, download:{local_path:'media/g.mp4'}};
    const video = {type:'video', width:640, height:360, download:{local_path:'media/v.mp4'}};
    const missing = {type:'photo', width:480, height:480};
    const single = app.renderMediaGrid([photo]);
    const original = app.renderMediaGrid([photo, video]);
    app.mediaGallery = 'carousel';
    assert.equal(app.renderMediaGrid([photo]), single);
    const html = app.renderMediaGrid([missing, photo, gif, video]);
    assert.equal((html.match(/class="media-carousel-item"/g) || []).length, 4);
    assert.doesNotMatch(html, /media-carousel-dots/);
    const dotted = app.renderMediaGrid([photo, gif, video], 'feed', null, {indicator: true});
    assert.match(dotted, /<div class="media-carousel-dots" aria-hidden="true"><span style="--dot-active: 1"><\/span>(<span style="--dot-active: 0"><\/span>){2}<\/div><div class="media-carousel-nav">/);
    assert.doesNotMatch(app.renderMediaGrid([photo, gif], 'detail', null, {indicator: true}), /media-carousel-dots/);
    assert.ok(html.includes('Media not downloaded'));
    assert.match(html, /tweetNookOpenLightbox\(this, 0\)/);
    assert.match(html, /tweetNookOpenGif\(event, this, 1\)/);
    assert.match(html, /data-video-custom/);
    assert.match(html, /aria-label="Media 4 of 4"/);
    assert.match(html, /M12\.957 4\.54L20\.414 12/, 'use the native arrow with a stem');
    app.mediaGallery = 'grid';
    assert.equal(app.renderMediaGrid([photo, video]), original);
});

run();
