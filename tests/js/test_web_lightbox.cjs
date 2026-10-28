'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { appScripts, pageHtml } = require('./page_scripts.cjs');
const {
    ROOT,
    browserContext,
    immediateComponent,
    loadScripts,
    test,
    run,
} = require('./web_harness.cjs');

test('lightbox navigation moves through media and clamps at both ends', () => {
    const context = browserContext();
    const html = pageHtml();
    const { tweetApp } = loadScripts(
        context,
        appScripts(),
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
    assert.match(html, /@click\.stop="closeLightbox\(\)"/);
    assert.match(html, /x-transition:enter="lightbox-overlay-enter"/);
    assert.match(html, /lightbox-position/);
});

test('lightbox touch drags track the finger, navigate, and protect the following click', () => {
    const context = browserContext();
    const html = pageHtml();
    const { tweetApp } = loadScripts(
        context,
        appScripts(),
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    const imageTarget = { closest: (selector) => selector === 'img, video, button' ? {} : null };
    const backdropTarget = { closest: () => null };
    const touchEvent = (x, y, touches = [{clientX: x, clientY: y}], target = backdropTarget, timeStamp = 0) => ({
        target,
        touches,
        changedTouches: [{clientX: x, clientY: y}],
        timeStamp,
        preventDefault() { this.prevented = true; },
        stopPropagation() { this.stopped = true; },
    });

    app.lightboxOpen = true;
    app.lightboxMedia = [{type: 'photo'}, {type: 'photo'}, {type: 'photo'}];
    app.startLightboxGesture({...touchEvent(220, 180, undefined, imageTarget, 100), target: imageTarget});
    const move = touchEvent(160, 184, undefined, imageTarget, 150);
    app.moveLightboxGesture(move);
    assert.equal(move.prevented, true);
    assert.equal(app.lightboxGestureAxis, 'horizontal');
    assert.equal(app.lightboxGestureOffsetX, -60);
    assert.match(app.lightboxSlideStyle(1).transform, /calc\(100% \+ -60px\)/);
    app.endLightboxGesture({...touchEvent(145, 184, undefined, imageTarget, 175), target: imageTarget});
    assert.equal(app.lightboxIndex, 1);
    assert.equal(app.lightboxOpen, true);
    app.resetLightboxMotion();

    // Like X, a slow release short of half the width springs back; past it, pages.
    context.window.innerWidth = 400;
    app.startLightboxGesture({...touchEvent(220, 180), target: backdropTarget});
    app.endLightboxGesture({...touchEvent(145, 184), target: backdropTarget});
    assert.equal(app.lightboxIndex, 1);
    const swipeClick = touchEvent(145, 184);
    app.handleLightboxBackdropClick(swipeClick);
    assert.equal(swipeClick.stopped, true);
    assert.equal(app.lightboxOpen, true);
    app.resetLightboxMotion();

    app.startLightboxGesture({...touchEvent(320, 180), target: backdropTarget});
    app.endLightboxGesture({...touchEvent(100, 184), target: backdropTarget});
    assert.equal(app.lightboxIndex, 2);
    app.resetLightboxMotion();

    app.startLightboxGesture({...touchEvent(100, 180), target: backdropTarget});
    app.endLightboxGesture({...touchEvent(320, 175), target: backdropTarget});
    assert.equal(app.lightboxIndex, 1);
    assert.match(html, /@touchstart\.passive="startLightboxGesture\(\$event\)"/);
    assert.match(html, /@touchmove="moveLightboxGesture\(\$event\)"/);
    assert.match(html, /@touchend\.passive="endLightboxGesture\(\$event\)"/);
    assert.match(html, /lightboxSlideStyle\(mediaIndex\)/);
    assert.match(html, /lightboxVisibleIndices\(\)/);
    const css = fs.readFileSync(path.join(ROOT, 'tweetnook/web/static/css/styles.css'), 'utf8');
    assert.match(css, /\.lightbox-gesture-surface\s*\{[^}]*touch-action:\s*pinch-zoom;/s);
    assert.match(css, /\.lightbox-slide\s*\{[^}]*will-change:\s*transform;/s);
});

test('lightbox drag-to-dismiss fades and scales before closing, while invalid drags snap back', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(
        context,
        appScripts(),
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    const touchEvent = (target, x, y, touches = [{clientX: x, clientY: y}], timeStamp = 0) => ({
        target,
        touches,
        changedTouches: [{clientX: x, clientY: y}],
        timeStamp,
        preventDefault() { this.prevented = true; },
    });

    app.lightboxOpen = true;
    app.lightboxMedia = [{type: 'photo'}];
    const imageTarget = {closest: (selector) => selector === 'img, video, button' ? {} : null};
    app.startLightboxGesture(touchEvent(imageTarget, 100, 100, undefined, 100));
    const dismissMove = touchEvent(imageTarget, 105, 170, undefined, 170);
    app.moveLightboxGesture(dismissMove);
    assert.equal(dismissMove.prevented, true);
    assert.equal(app.lightboxGestureOffsetY, 70);
    assert.match(app.lightboxSlideStyle(0).transform, /scale\(0\.9\d+/);
    assert.match(app.lightboxBackdropStyle().backgroundColor, /rgba\(0, 0, 0, 0\.[0-8]/);
    assert.equal(app.lightboxShadeStyle().opacity, app.lightboxBackdropStyle()['--lightbox-backdrop-opacity']); // The page shade follows the drag.
    app.endLightboxGesture(touchEvent(imageTarget, 105, 185, undefined, 190));
    assert.equal(app.lightboxOpen, true);
    assert.equal(app.lightboxDismissPending, true);
    assert.ok(app.lightboxGestureOffsetY >= 800);
    app.finishLightboxDismiss();
    assert.equal(app.lightboxOpen, false);
    app.resetLightboxMotion();

    app.lightboxOpen = true;
    app.startLightboxGesture(touchEvent({closest: () => ({})}, 100, 100));
    app.endLightboxGesture(touchEvent({closest: () => ({})}, 100, 190));
    assert.equal(app.lightboxOpen, true);

    app.startLightboxGesture(touchEvent({closest: () => null}, 100, 100));
    app.endLightboxGesture(touchEvent({closest: () => null}, 100, 150));
    assert.equal(app.lightboxOpen, true);
    assert.equal(app.lightboxGestureOffsetY, 0);
    app.resetLightboxMotion();

    app.startLightboxGesture(touchEvent({closest: () => null}, 100, 100, [
        {clientX: 100, clientY: 100},
        {clientX: 105, clientY: 105},
    ]));
    app.endLightboxGesture(touchEvent({closest: () => null}, 100, 190));
    assert.equal(app.lightboxOpen, true);
});

test('lightbox gestures use edge resistance and velocity-aware flick navigation', () => {
    const context = browserContext();
    context.window.innerWidth = 390;
    const { tweetApp } = loadScripts(
        context,
        appScripts(),
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    const target = {closest: () => null};
    const event = (x, y, timeStamp) => ({
        target,
        timeStamp,
        touches: [{clientX: x, clientY: y}],
        changedTouches: [{clientX: x, clientY: y}],
        preventDefault() {},
    });

    app.lightboxOpen = true;
    app.lightboxMedia = [{type: 'photo'}, {type: 'photo'}];
    app.startLightboxGesture(event(100, 100, 100));
    app.moveLightboxGesture(event(160, 102, 180));
    assert.equal(app.lightboxGestureOffsetX, 13.2);
    app.endLightboxGesture(event(160, 102, 200));
    assert.equal(app.lightboxIndex, 0);
    app.resetLightboxMotion();

    app.startLightboxGesture(event(200, 100, 300));
    app.moveLightboxGesture(event(174, 101, 320));
    app.endLightboxGesture(event(170, 101, 330));
    assert.equal(app.lightboxIndex, 1);
    // Without animation frames the spring settles at once.
    assert.equal(app.lightboxGestureOffsetX, 0);
    assert.equal(app.lightboxGestureAnimating, false);
});

test('lightbox playback follows the active animated slide and pauses on close', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(
        context,
        appScripts(),
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    const counts = {activePlay: 0, activePause: 0, neighborPlay: 0, neighborPause: 0};
    const video = (active, prefix) => ({
        closest() {
            return {classList: {contains: () => active}};
        },
        hasAttribute(name) {
            return name === 'autoplay';
        },
        pause() {
            counts[`${prefix}Pause`] += 1;
        },
        play() {
            counts[`${prefix}Play`] += 1;
            return Promise.resolve();
        },
    });
    const active = video(true, 'active');
    const neighbor = video(false, 'neighbor');
    app.$refs.lightboxStage = {querySelectorAll: () => [active, neighbor]};
    app.lightboxOpen = true;

    app.syncLightboxPlayback();
    assert.deepEqual(counts, {
        activePlay: 1,
        activePause: 0,
        neighborPlay: 0,
        neighborPause: 1,
    });

    app.closeLightbox();
    assert.equal(app.lightboxOpen, false);
    assert.equal(counts.activePause, 1);
    assert.equal(counts.neighborPause, 2);
});

test('late lightbox observer and playback callbacks cannot restart a dismissed player', () => {
    const context = browserContext();
    const {tweetApp} = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.fetchSetup = async () => {};
    app.fetchNotices = async () => {};
    app.fetchActivityStatus = async () => {};
    app.initApp();
    let plays = 0;
    const video = {tagName: 'VIDEO', paused: true, hasAttribute: name => name === 'autoplay',
        closest: selector => selector === '.lightbox-stage' ? {} : {classList: {contains: () => true}},
        pause() {this.paused = true;}, play() {plays++; this.paused = false; return Promise.resolve();}};
    app.$refs.lightboxStage = {querySelectorAll: () => [video], querySelector: () => video};
    app.lightboxOpen = true;
    app.syncLightboxPlayback();
    assert.equal(plays, 1);
    app.closeLightbox();
    app.videoObserver.callback([{target: video, isIntersecting: true}]);
    app.syncLightboxPlayback();
    assert.equal(plays, 1);
    assert.equal(video.paused, true);
});

test('GIF indicator tracks playback state and toggles the archived animation', async () => {
    const context = browserContext();
    loadScripts(context, appScripts(), '({tweetApp})');
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

test('media lightbox data and community note links cannot inject script', () => {
    const context = browserContext();
    const events = [];
    context.window.dispatchEvent = event => events.push(event);
    const app = immediateComponent(loadScripts(context, appScripts(), 'tweetApp')());
    const hostile = "x'),alert(1),('\"<b>";
    const photo = { type: 'photo', url: hostile, download: { local_path: 'media/1/p.jpg' } };
    for (const html of [app.renderMediaGrid([photo]), app.renderMediaGrid([photo, photo])]) {
        assert.doesNotMatch(html, /JSON\.parse\('/);
        assert.match(html, /onclick="window\.tweetNookOpenLightbox\(this, \d\)"/);
        const attribute = html.match(/data-lightbox-media="([^"]*)"/)[1];
        assert.doesNotMatch(attribute, /['<>]/);
        const decoded = attribute.replace(/&quot;/g, '"').replace(/&#039;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
        const holder = { dataset: { lightboxMedia: decoded } };
        context.tweetNookOpenLightbox({ closest: () => holder }, 1);
        assert.equal(events.at(-1).detail.media[0].m.url, hostile);
        assert.equal(events.at(-1).detail.index, 1);
    }

    const note = app.renderCommunityNote({ birdwatch_pivot: { subtitle: {
        text: 'bad good',
        entities: [
            { fromIndex: 0, toIndex: 3, ref: { urlType: 'ExternalUrl', url: 'javascript:alert(1)' } },
            { fromIndex: 4, toIndex: 8, ref: { urlType: 'ExternalUrl', url: 'https://example.test/a?b=1&c=2' } },
        ],
    } } });
    assert.doesNotMatch(note, /javascript:/);
    assert.match(note, /<a href="#"[^>]*>bad<\/a>/);
    assert.match(note, /<a href="https:\/\/example\.test\/a\?b=1&amp;c=2"/);
});

run();
