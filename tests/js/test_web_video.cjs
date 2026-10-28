'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { appScripts, pageHtml } = require('./page_scripts.cjs');
const {
    FakeElement,
    ROOT,
    browserContext,
    dispatchFeed,
    immediateComponent,
    loadScripts,
    parseFakeHTML,
    test,
    run,
} = require('./web_harness.cjs');

test('media renderer preserves direct playback parents, placeholders and existing galleries', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(
        context,
        appScripts(),
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
    assert.match(photo, /media-frame-feed/);
    assert.match(photo, /<img src="\/media\/a.jpg"/);

    const ordinaryQuotedPortrait = app.renderMediaGrid([
        { type: 'photo', width: 360, height: 640, download: { local_path: 'media/quoted.jpg' } },
    ], 'quote-detail');
    assert.match(ordinaryQuotedPortrait, /--media-frame-aspect: 0\.75;/);
    assert.doesNotMatch(ordinaryQuotedPortrait, /--media-photo-cap-width|media-frame-portrait-photo/);
    const originalPhoto = JSON.parse(parseFakeHTML(ordinaryQuotedPortrait)[0]
        .querySelector('[data-lightbox-media]').getAttribute('data-lightbox-media'))[0].m;
    assert.equal(originalPhoto.width, 360);
    assert.equal(originalPhoto.height, 640);

    const quotedListPhoto = app.renderMediaGrid([
        { type: 'photo', width: 360, height: 640, download: { local_path: 'media/quoted.jpg' } },
    ], 'quote');
    assert.match(quotedListPhoto, /--media-photo-cap-width: 382\.5px;/);
    assert.match(quotedListPhoto, /media-frame-portrait-photo/);

    const expandedQuotedPortrait = app.renderMediaGrid([
        { type: 'photo', width: 2816, height: 3072, download: { local_path: 'media/expanded-quoted.jpg' } },
    ], 'quote-detail-expanded');
    assert.match(expandedQuotedPortrait, /--media-frame-aspect: 0\.9166666666666666;/);
    assert.match(expandedQuotedPortrait, /class="[^"]*media-frame-quote-detail-expanded[^"]*"/);
    assert.doesNotMatch(expandedQuotedPortrait, /--media-photo-cap-width|media-frame-portrait-photo/);

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
    assert.match(video, /media-frame-video/);
    const parsedVideo = parseFakeHTML(video)[0];
    const player = parsedVideo.querySelector('video').parentElement;
    assert.ok(player.classList.contains('media-video-player'));
    assert.ok(player.querySelector('[data-video-duration]'));
    assert.match(video, /aria-label="Remaining video duration 2:00">2:00<\/span>/);
    assert.match(video, /onloadedmetadata="window\.tweetNookSyncVideoDuration\(this\)"/);
    assert.match(video, /ontimeupdate="window\.tweetNookSyncVideoDuration\(this\)"/);
    assert.match(video, /onended="window\.tweetNookSyncVideoDuration\(this\); window\.tweetNookSyncVideoUi\(this\)"/);
    assert.match(video, /onplay="window\.tweetNookShowVideoUi\(this\)"/);
    assert.match(video, /onpause="window\.tweetNookSyncVideoUi\(this\)"/);
    assert.doesNotMatch(video, /onfocus=|onblur=/);
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
    assert.match(gif, /media-frame-gif-video/);
    const gifPlayer = parseFakeHTML(gif)[0].querySelector('video').parentElement;
    assert.ok(gifPlayer.querySelector('[data-gif-toggle]'));
    assert.doesNotMatch(gif, /media-video-duration/);

    const missing = app.renderMediaGrid([{ type: 'photo', width: 4, height: 3 }]);
    assert.match(missing, /Media not downloaded/);
    assert.ok(parseFakeHTML(missing)[0].querySelector('.media-frame-placeholder'));

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
    assert.match(gridVideo, /aria-label="Remaining video duration 0:03">0:03<\/span>/);
    for (const context of ['feed', 'detail', 'quote', 'quote-detail']) {
        const gallery = app.renderMediaGrid([
            { type: 'photo', width: 640, height: 360, download: { local_path: 'media/photo.jpg' } },
            { type: 'video', width: 360, height: 640, download: { local_path: 'media/video.mp4' } },
        ], context);
        const parsed = parseFakeHTML(gallery)[0];
        assert.ok(parsed.querySelector('img').classList.contains('object-cover'));
        assert.ok(parsed.querySelector('video').classList.contains('media-grid-video'));
        assert.ok(!parsed.querySelector('video').classList.contains('object-cover'));
    }

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
    assert.match(css, /\.media-video-duration\s*\{[^}]*display: inline-flex;[^}]*align-items: center;/s);
    assert.match(css, /\.media-video-player\.is-native-ui-hidden \.media-video-duration \{/);
    assert.doesNotMatch(css, /\.media-video-player:(hover|focus-within) \.media-video-duration/);
    assert.match(css, /\.media-gif-indicator \{/);
    assert.match(css, /\.media-gif-indicator\.is-paused \.media-gif-play-icon/);
    assert.match(css, /\.quote-thumbnail-badge\s*\{[^}]*display: inline-flex;[^}]*align-items: center;/s);
    assert.match(css, /\.quote-thumbnail-duration\s*\{[^}]*background: rgba\(0, 0, 0, 0\.77\);/s);
    assert.match(css, /\.media-frame-video,[\s\S]*object-fit: contain;[\s\S]*background: #000;/);
    // Photo previews crop; playing videos contain their pixels in every gallery.
    assert.match(css, /\.quote-card:not\(\.quote-compact\) > \.quote-media > \.media-single img \{\s*object-fit: cover;/);
    assert.match(css, /\.media-grid-video\s*\{[^}]*object-fit: contain;[^}]*background: #000;/);
    assert.match(css, /@media \(max-width: 675px\) \{\s*\.media-frame-feed,\s*\.media-frame-detail,\s*\.media-frame-quote \{\s*--media-max-width: 100%;\s*--media-photo-cap-width: 100%;/);
    assert.match(css, /\.quote-card:not\(\.quote-compact\) > \.quote-media > \.media-single video \{\s*object-fit: contain;/);
    assert.doesNotMatch(css, /\.quote-card:not\(\.quote-compact\) > \.quote-media (img|video) \{/);
});

test('video duration uses loaded metadata and shows only while the control bar is hidden', () => {
    const context = browserContext();
    const timers = new Map();
    let nextTimerId = 0;
    context.setTimeout = (callback, delay) => {
        const id = ++nextTimerId;
        timers.set(id, {
            callback: () => {
                timers.delete(id);
                callback();
            },
            delay,
        });
        return id;
    };
    context.clearTimeout = id => timers.delete(id);
    loadScripts(context, appScripts(), '({tweetApp})');
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
    const video = { duration: 125.9, currentTime: 30.2, parentElement: frame };

    context.window.tweetNookSyncVideoDuration(video);
    assert.equal(indicator.textContent, '1:35');
    assert.equal(indicator.hidden, false);
    assert.equal(attributes.get('aria-label'), 'Remaining video duration 1:35');
    assert.equal(attributes.has('aria-hidden'), false);

    video.currentTime = video.duration;
    context.window.tweetNookSyncVideoDuration(video);
    assert.equal(indicator.textContent, '0:00');
    assert.equal(attributes.get('aria-label'), 'Remaining video duration 0:00');

    const hidden = 'is-native-ui-hidden';
    const fireTimers = () => [...timers.values()].forEach(timer => timer.callback());

    // Native controls stay up while paused, so pointer activity alone never shows the badge.
    video.paused = true;
    context.window.tweetNookShowVideoUi(video);
    fireTimers();
    assert.ok(!classes.has(hidden));

    // Playback starts with the controls up, then they fade after the idle delay.
    video.paused = false;
    context.window.tweetNookShowVideoUi(video);
    assert.ok(!classes.has(hidden));
    assert.equal(timers.size, 1);
    assert.equal(timers.values().next().value.delay, 3000);
    fireTimers();
    assert.ok(classes.has(hidden));

    // Moving the pointer again brings the controls back and restarts the idle delay.
    context.window.tweetNookShowVideoUi(video);
    const canceledTimerId = timers.keys().next().value;
    assert.ok(!classes.has(hidden));
    context.window.tweetNookShowVideoUi(video);
    assert.ok(!timers.has(canceledTimerId));
    assert.equal(timers.size, 1);

    // Leaving the player hides the controls almost immediately.
    context.window.tweetNookLeaveVideoUi(video);
    assert.equal(timers.size, 1);
    assert.equal(timers.values().next().value.delay, 300);
    fireTimers();
    assert.ok(classes.has(hidden));

    // Pausing or ending brings the native controls back without pointer activity.
    video.paused = true;
    context.window.tweetNookSyncVideoUi(video);
    assert.ok(!classes.has(hidden));
    video.paused = false;
    video.ended = true;
    context.window.tweetNookSyncVideoUi(video);
    assert.ok(!classes.has(hidden));
});

test('custom players retain native fallback, seek, toggle sound, and isolate keyboard shortcuts', async () => {
    const context = browserContext();
    context.setTimeout = () => 1;
    context.clearTimeout = () => {};
    const { tweetApp, tweetNookInitVideoPlayer, tweetNookSyncVideoDuration, isVideoPlayer } = loadScripts(
        context, appScripts(),
        '({tweetApp, tweetNookInitVideoPlayer, tweetNookSyncVideoDuration, isVideoPlayer})',
    );
    const app = immediateComponent(tweetApp());
    const player = parseFakeHTML(app.renderMediaGrid([{type: 'video', download: {local_path: 'media/v.mp4'}}]))[0].querySelector('video').parentElement;
    const video = player.querySelector('video');
    const seek = player.querySelector('[data-video-seek]');
    const volume = player.querySelector('[data-video-volume]');
    for (const element of [player, seek]) element.style = { setProperty() {} };
    Object.assign(video, { paused: true, ended: false, duration: NaN, currentTime: 0, volume: 1, muted: false,
        play() { this.paused = false; return Promise.resolve(); }, pause() { this.paused = true; },
    });
    assert.equal(player.querySelector('[data-video-action="pip"]'), null);
    assert.equal(player.querySelector('[data-video-action="settings"]'), null);
    assert.ok(!video.hasAttribute('controls'));
    assert.ok(video.hasAttribute('playsinline'));
    assert.ok(video.hasAttribute('data-video-custom'));
    tweetNookInitVideoPlayer(video);
    assert.ok(!video.hasAttribute('controls'));
    assert.ok(isVideoPlayer(video));
    assert.equal(seek.disabled, true);
    const listeners = player.listeners.length;
    tweetNookInitVideoPlayer(video);
    assert.equal(player.listeners.length, listeners);

    for (const modifiers of [{button: 2}, {button: 0, ctrlKey: true}]) {
        const click = dispatchFeed(player, 'click', video, modifiers);
        assert.equal(click.defaultPrevented, false, 'native context-menu clicks keep their default action');
        assert.equal(video.paused, true, 'native context-menu clicks do not toggle playback');
    }

    video.duration = 120;
    tweetNookSyncVideoDuration(video);
    assert.equal(seek.disabled, false);
    seek.value = 25;
    dispatchFeed(player, 'input', seek);
    assert.equal(video.currentTime, 30);
    assert.equal(player.querySelector('[data-video-time]').textContent, '0:30 / 2:00');
    assert.equal(seek.getAttribute('aria-valuetext'), '0:30 of 2:00');
    seek.value = 150;
    dispatchFeed(player, 'input', seek);
    assert.equal(video.currentTime, 120);
    dispatchFeed(player, 'keydown', player, {key: 'ArrowLeft'});
    assert.equal(video.currentTime, 115);
    const space = dispatchFeed(player, 'keydown', player, {key: ' '});
    assert.ok(!video.paused && space.defaultPrevented && space.propagationStopped);
    assert.equal(player.querySelector('[data-video-action="play"]').getAttribute('aria-label'), 'Pause video');
    const sliderKey = dispatchFeed(player, 'keydown', seek, {key: 'ArrowLeft'});
    assert.equal(video.currentTime, 110); // The slider seeks by seconds, not its 0.1% native step.
    assert.ok(sliderKey.propagationStopped && sliderKey.defaultPrevented); // Do not navigate lightbox slides.
    const find = dispatchFeed(player, 'keydown', player, {key: 'f', metaKey: true});
    assert.ok(!find.defaultPrevented && !find.propagationStopped); // Browser shortcuts such as Cmd+F still work.
    const mute = player.querySelector('[data-video-action="mute"]');
    assert.ok(dispatchFeed(player, 'click', mute).propagationStopped);
    assert.equal(video.muted, true);
    assert.equal(mute.getAttribute('aria-label'), 'Unmute');
    volume.value = 0.3;
    dispatchFeed(player, 'input', volume);
    assert.equal(video.volume, 0.3);
    assert.equal(video.muted, false);
    context.window.dispatchEvent = event => {
        if (event.type === 'video-expanded-audio') app.setExpandedVideoAudio(event.detail.video, event.detail.expanded);
    };
    video.muted = true;
    let fullscreen = 0;
    player.requestFullscreen = () => { fullscreen++; return Promise.resolve(); };
    dispatchFeed(player, 'keydown', player, {key: 'f'});
    assert.equal(fullscreen, 1);
    assert.equal(video.muted, false);
    assert.equal(app.videoMuted, true);
    context.document.fullscreenElement = player;
    dispatchFeed(player, 'fullscreenchange', player);
    context.document.exitFullscreen = () => { fullscreen--; return Promise.resolve(); };
    dispatchFeed(player, 'click', player.querySelector('[data-video-action="fullscreen"]'));
    assert.equal(fullscreen, 0);
    context.document.fullscreenElement = null;
    dispatchFeed(player, 'fullscreenchange', player); // Also delivered for browser Escape.
    assert.equal(video.muted, true);
    for (const request of [() => Promise.reject(new Error('Denied')), () => {throw new Error('Denied');}]) {
        player.requestFullscreen = request;
        dispatchFeed(player, 'keydown', player, {key: 'f'});
        await Promise.resolve();
        assert.equal(video.muted, true);
    }
    // The seek thumb follows playback even after keyboard focus stays on it.
    context.document.activeElement = seek;
    video.currentTime = 60;
    tweetNookSyncVideoDuration(video);
    assert.equal(seek.value, 50);

    const nativeVideo = new FakeElement('video', [['controls', '']]);
    tweetNookInitVideoPlayer(nativeVideo);
    assert.ok(nativeVideo.hasAttribute('controls'));
});

test('scrubbing holds playback, tracks preview time, resumes on release and restores on cancellation', () => {
    const context = browserContext();
    const {tweetApp, tweetNookInitVideoPlayer} = loadScripts(context, appScripts(), '({tweetApp, tweetNookInitVideoPlayer})');
    const player = parseFakeHTML(tweetApp().renderMediaGrid([{type: 'video', download: {local_path: 'media/v.mp4'}}]))[0].querySelector('video').parentElement;
    const video = player.querySelector('video');
    const seek = player.querySelector('[data-video-seek]');
    for (const element of [player, seek]) element.style = {setProperty() {}};
    let plays = 0;
    let root = player;
    while (root.parentElement) root = root.parentElement;
    root.connected = true;
    Object.assign(video, {paused: false, duration: 10, currentTime: 3, volume: 1, muted: true,
        pause() {this.paused = true;}, play() {plays++; this.paused = false; return Promise.resolve();}});
    tweetNookInitVideoPlayer(video);
    dispatchFeed(player, 'pointerdown', seek, {button: 0});
    assert.equal(video.paused, true);
    assert.ok(player.classList.contains('is-scrubbing'));
    seek.value = 75;
    dispatchFeed(player, 'input', seek);
    assert.equal(video.currentTime, 7.5);
    assert.equal(player.querySelector('[data-video-scrub-time]').textContent, '0:07 / 0:10');
    context.__state.events.get('document:pointerup')();
    assert.equal(video.paused, false);
    assert.equal(plays, 1);
    assert.ok(!player.classList.contains('is-scrubbing'));
    assert.ok(!context.__state.events.has('document:pointercancel'));
    dispatchFeed(player, 'pointerdown', seek, {button: 0});
    seek.value = 20;
    dispatchFeed(player, 'input', seek);
    context.__state.events.get('document:pointercancel')();
    assert.equal(video.currentTime, 7.5);
    assert.equal(video.paused, false);
    video.pause();
    dispatchFeed(player, 'pointerdown', seek, {button: 0});
    context.__state.events.get('document:pointerup')();
    assert.equal(video.paused, true); // A paused video remains paused after seeking.
    assert.equal(plays, 2);
});

test('ordinary feed and grid videos start inline without a native controls mount race', () => {
    const context = browserContext();
    const {tweetApp, tweetNookInitVideoPlayer} = loadScripts(context, appScripts(), '({tweetApp, tweetNookInitVideoPlayer})');
    const media = {type: 'video', width: 720, height: 1280, download: {local_path: 'media/v.mp4'}};
    for (const list of [[media], [media, media]]) {
        const root = parseFakeHTML(tweetApp().renderMediaGrid(list))[0];
        for (const video of root.querySelectorAll('video')) {
            assert.ok(video.hasAttribute('playsinline'));
            assert.ok(video.hasAttribute('data-video-custom'));
            assert.equal(Number(video.dataset.videoAspect), 720 / 1280);
            assert.ok(!video.hasAttribute('controls'));
            const player = video.parentElement;
            for (const element of [player, player.querySelector('[data-video-seek]')]) element.style = {setProperty() {}};
            Object.assign(video, {paused: true, volume: 1, muted: true, currentTime: 0, duration: 10});
            tweetNookInitVideoPlayer(video);
            assert.equal(video.playsInline, true);
            assert.ok(video.hasAttribute('webkit-playsinline'));
            assert.ok(!video.hasAttribute('controls'));
        }
    }
    const html = pageHtml();
    assert.doesNotMatch(html, /<video[^>]*\scontrols(?:\s|>)/);
});

test('touch scrub owns its pointer, coalesces decoder seeks and retains the pending preview', () => {
    const context = browserContext();
    const frames = new Map();
    let nextFrame = 0;
    context.window.requestAnimationFrame = callback => {frames.set(++nextFrame, callback); return nextFrame;};
    context.window.cancelAnimationFrame = id => frames.delete(id);
    const {tweetApp, tweetNookInitVideoPlayer, tweetNookSyncVideoDuration} = loadScripts(context, appScripts(), '({tweetApp, tweetNookInitVideoPlayer, tweetNookSyncVideoDuration})');
    const root = parseFakeHTML(tweetApp().renderMediaGrid([{type: 'video', download: {local_path: 'media/v.mp4'}}]))[0];
    root.connected = true;
    const video = root.querySelector('video'), player = video.parentElement, seek = player.querySelector('[data-video-seek]');
    const picture = new FakeElement('div', [['class', 'video-motion-surface']]);
    player.insertBefore(picture, video);
    picture.appendChild(video); // Fullscreen scrubbing still belongs to the outer control anchor.
    for (const element of [player, seek]) element.style = {setProperty() {}};
    seek.rect = {left: 10, width: 100};
    let captured, released, time = 2, seeks = 0;
    seek.setPointerCapture = id => {captured = id;};
    seek.hasPointerCapture = id => captured === id;
    seek.releasePointerCapture = id => {released = id; captured = null;};
    Object.assign(video, {paused: false, duration: 10, muted: true, volume: 1, seeking: false,
        pause() {this.paused = true;}, play() {this.paused = false; return Promise.resolve();}});
    Object.defineProperty(video, 'currentTime', {get: () => time, set: value => {time = value; seeks++;}});
    tweetNookInitVideoPlayer(video);
    const down = dispatchFeed(player, 'pointerdown', seek, {button: 0, pointerType: 'touch', pointerId: 7, clientX: 40});
    assert.ok(down.defaultPrevented);
    assert.equal(captured, 7);
    const move = context.__state.events.get('document:pointermove');
    move({pointerId: 8, clientX: 100}); // A second finger must not steal the scrub.
    assert.equal(seek.value, 30);
    move({pointerId: 7, clientX: 90});
    move({pointerId: 7, clientX: 80});
    assert.equal(frames.size, 1);
    assert.equal(seeks, 0);
    frames.values().next().value(); frames.clear();
    assert.equal(time, 7);
    assert.equal(seeks, 1);
    dispatchFeed(video, 'seeked', video);
    frames.values().next().value(); frames.clear();
    assert.equal(seeks, 1); // A settled finger must not create a decoder seek loop.
    video.seeking = true;
    move({pointerId: 7, clientX: 100});
    tweetNookSyncVideoDuration(video); // An older decoded frame must not pull the thumb back.
    assert.equal(seek.value, 90);
    assert.equal(player.querySelector('[data-video-scrub-time]').textContent, '0:09 / 0:10');
    assert.equal(frames.size, 0);
    context.__state.events.get('document:pointerup')({pointerId: 7, clientX: 95});
    assert.equal(time, 8.5); // Final position is exact, even if the last decode is pending.
    assert.equal(video.paused, false);
    assert.equal(released, 7);
    assert.ok(!context.__state.events.has('document:pointermove'));
});

test('generic visibility observer leaves feed autoplay timing to its selector', () => {
    const context = browserContext();
    const {tweetApp} = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.fetchSetup = async () => {};
    app.fetchNotices = async () => {};
    app.fetchActivityStatus = async () => {};
    app.initApp();
    const video = {tagName: 'VIDEO', paused: false, closest: () => null,
        hasAttribute: name => name === 'controls', pause() {this.paused = true;}};
    // A tall player can cross the raw-area observer's boundary while still
    // showing enough of the available viewport to retain autoplay ownership.
    app.videoObserver.callback([{target: video, isIntersecting: false}]);
    assert.equal(video.paused, false);
    app.videoPlayback = 'paused';
    app.videoObserver.callback([{target: video, isIntersecting: false}]);
    assert.equal(video.paused, true, 'click-to-play still pauses outside view');
    app.videoPlayback = 'autoplay';
    video.paused = false;
    video.hasAttribute = name => name === 'autoplay';
    app.videoObserver.callback([{target: video, isIntersecting: false}]);
    assert.equal(video.paused, true, 'GIF visibility remains independent');
});

test('volume and mute are shared in both playback modes and inherited by new players', () => {
    for (const mode of ['autoplay', 'paused']) {
        const context = browserContext();
        const { tweetApp, forcedMuteVideos } = loadScripts(context, appScripts(), '({tweetApp, forcedMuteVideos})');
        const app = immediateComponent(tweetApp());
        app.videoPlayback = mode;
        app.videoObserver = app.videoAutoplayObserver = { observe() {} };
        const player = (lightbox = false) => {
            const video = new FakeElement('video', [['controls', '']]);
            let volume = 1, muted = false;
            Object.defineProperties(video, {
                volume: { get: () => volume, set(value) { volume = value; app.handleVideoVolumeChange(video); } },
                muted: { get: () => muted, set(value) { muted = value; app.handleVideoVolumeChange(video); } },
            });
            const closest = video.closest.bind(video);
            video.closest = selector => lightbox && selector === '.lightbox-stage' ? {} : closest(selector);
            context.__state.videos.push(video);
            app.observeVideo(video);
            return video;
        };
        const feed = player(), panel = player(), lightbox = player(true);
        feed.volume = .3;
        feed.muted = false;
        assert.equal(app.videoVolume, .3);
        for (const video of [panel, lightbox, player()]) {
            assert.equal(video.volume, .3);
            assert.equal(video.muted, false);
        }
        forcedMuteVideos.add(feed);
        feed.muted = true;
        feed.volume = .2;
        assert.equal(panel.volume, .2);
        assert.equal(panel.muted, false, 'browser-imposed mute stays local');
        feed.muted = false;
        assert.equal(forcedMuteVideos.has(feed), false);
        lightbox.volume = .7;
        lightbox.muted = true;
        assert.equal(feed.volume, .7);
        assert.equal(panel.muted, true);
        // GIF playback has its own permanently silent policy.
        const gif = new FakeElement('video', [['data-animated-gif', '']]);
        gif.volume = .1;
        gif.muted = true;
        app.handleVideoVolumeChange(gif);
        assert.equal(app.videoVolume, .7);
    }
});

test('autoplay setting plays the topmost visible video muted and shares unmuting', async () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    // Fake players dispatch their media events synchronously to the app handlers.
    const player = (top, { lightbox = false, custom = false } = {}) => {
        const video = {
            tagName: 'VIDEO',
            paused: true,
            isConnected: true,
            playCalls: 0,
            _muted: false,
            hasAttribute: name => name === (custom ? 'data-video-player' : 'controls'),
            closest: selector => (lightbox && selector === '.lightbox-stage' ? {} : null),
            getBoundingClientRect: () => ({ top }),
            get muted() { return this._muted; },
            set muted(value) {
                const changed = this._muted !== value;
                this._muted = value;
                if (changed) app.handleVideoVolumeChange(this);
            },
            play() {
                this.playCalls += 1;
                this.paused = false;
                app.handleVideoPlay(this);
                return Promise.resolve();
            },
            pause() {
                if (this.paused) return;
                this.paused = true;
                app.handleVideoPause(this);
            },
        };
        context.__state.videos.push(video);
        return video;
    };
    const lower = player(400, {custom: true});
    const upper = player(100);
    const lightbox = player(0, { lightbox: true });

    // Autoplay is the default; Click to Play never autoplays.
    assert.equal(app.videoPlayback, 'autoplay');
    app.setVideoPlayback('paused');
    app.updateAutoplayVisibility(lower, true);
    app.updateAutoplayVisibility(upper, true);
    assert.equal(upper.playCalls + lower.playCalls, 0);

    app.setVideoPlayback('autoplay');
    assert.equal(context.localStorage.getItem('tvx-video-playback'), 'autoplay');
    assert.equal(upper.playCalls, 1);
    assert.equal(lower.playCalls, 0);
    assert.ok(upper.muted && lower.muted && lightbox.muted);

    // Unmuting one player unmutes every player.
    upper.muted = false;
    assert.equal(app.videoMuted, false);
    assert.ok(!lower.muted && !lightbox.muted);

    // A video the viewer pauses stays paused; the next visible one takes over with sound.
    upper.pause();
    app.autoplayNextVideo();
    assert.equal(upper.playCalls, 1);
    assert.equal(lower.playCalls, 1);
    assert.equal(lower.muted, false);

    // Playing another video pauses the current one, and muting is shared again.
    lightbox.play();
    assert.ok(lower.paused);
    lightbox.muted = true;
    assert.ok(upper.muted && lower.muted);
    lightbox.paused = true;

    // Scrolling away clears a hold, but overlays keep autoplay back.
    app.updateAutoplayVisibility(upper, false);
    app.updateAutoplayVisibility(lower, false);
    app.lightboxOpen = true;
    app.updateAutoplayVisibility(lower, true);
    assert.equal(lower.playCalls, 1);
    app.lightboxOpen = false;
    app.autoplayNextVideo();
    assert.equal(lower.playCalls, 2);
    assert.equal(lower.muted, true);

    // Switching back pauses feed players and restores normal sound.
    app.setVideoPlayback('paused');
    assert.ok(lower.paused);
    assert.ok(!upper.muted && !lower.muted && !lightbox.muted);

    // Click to Play also keeps manual playback to one video at a time.
    upper.play();
    lower.play();
    assert.ok(upper.paused && !lower.paused);
    assert.equal(lower.muted, false);
    lower.pause();

    const restoredPlayback = () => {
        const restored = immediateComponent(tweetApp());
        restored.fetchSetup = async () => {};
        restored.fetchNotices = async () => {};
        restored.fetchActivityStatus = async () => {};
        restored.initApp();
        return restored.videoPlayback;
    };
    assert.equal(restoredPlayback(), 'paused');
    context.localStorage.removeItem('tvx-video-playback');
    assert.equal(restoredPlayback(), 'autoplay');

    const html = pageHtml();
    assert.match(html, /role="switch" aria-label="Autoplay videos" :aria-checked="videoPlayback === 'autoplay'" @click="setVideoPlayback\(videoPlayback === 'autoplay' \? 'paused' : 'autoplay'\)"/);
    assert.match(html, /Play videos muted as they scroll into view\./);
});

test('carousel arrows advance one tile, clamp at the ends, and retain player keyboard controls', () => {
    const context = browserContext();
    const app = loadScripts(context, appScripts(), 'window');
    const calls = [];
    const buttons = {'.media-carousel-previous':{}, '.media-carousel-next':{}};
    const track = {scrollLeft:0, scrollWidth:1471, clientWidth:598,
        children:[64,266,684,1040].map(offsetLeft => ({offsetLeft})),
        classList:{contains: name => name === 'media-carousel-track'},
        parentElement:{querySelector: selector => buttons[selector]},
        scrollTo(options) {calls.push(options); this.scrollLeft = Math.max(0, Math.min(873, options.left));},
    };
    app.tweetNookGalleryScroll(track);
    assert.equal(buttons['.media-carousel-previous'].disabled, true);
    assert.equal(buttons['.media-carousel-next'].disabled, false);
    app.tweetNookScrollGallery(track, 1);
    assert.equal(track.scrollLeft, 202, 'advance to the second tile, not the next page');
    assert.equal(calls.at(-1).behavior, 'auto', 'native arrow clicks move immediately, without a smooth slide');
    app.tweetNookScrollGallery(track, 1);
    assert.equal(track.scrollLeft, 620);
    app.tweetNookScrollGallery(track, 1);
    assert.equal(track.scrollLeft, 873);
    app.tweetNookGalleryScroll(track);
    assert.equal(buttons['.media-carousel-next'].disabled, true);
    const key = (name, target = track) => ({key:name, target, preventDefault(){}, stopPropagation(){}});
    app.tweetNookGalleryKey(key('Home'), track);
    assert.equal(track.scrollLeft, 0);
    app.tweetNookGalleryKey(key('End'), track);
    assert.equal(track.scrollLeft, 873);
    app.tweetNookGalleryKey(key('ArrowLeft', {}), track);
    assert.equal(track.scrollLeft, 873, 'a video/slider owns its own arrow keys');
    context.window.matchMedia = () => ({matches:true});
    app.tweetNookScrollGallery(track, -1);
    assert.equal(calls.at(-1).behavior, 'auto');
});

run();
