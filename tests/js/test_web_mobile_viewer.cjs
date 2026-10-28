'use strict';

const assert = require('node:assert/strict');
const vm = require('node:vm');
const { appScripts, pageHtml, pageScripts, readScripts } = require('./page_scripts.cjs');
const {
    FakeElement,
    browserContext,
    dispatchFeed,
    immediateComponent,
    loadScripts,
    parseFakeHTML,
    test,
    run,
    pageCss,
} = require('./web_harness.cjs');

test('the phone viewer pages and springs back on X\'s measured spring', () => {
    const context = browserContext();
    loadScripts(context, appScripts(), 'tweetApp');
    const spring = vm.runInContext('lightboxSpring', context);
    // X's spring-back from a 131pt drag at its recorded frame times (iPhone recording).
    [[17, -116], [34, -88], [51, -63], [67, -44], [84, -28], [101, -17]]
        .forEach(([t, x]) => assert.ok(Math.abs(spring(-131, 0, t) - x) <= 4, `${spring(-131, 0, t)} vs ${x} at ${t}ms`));
    // Finishing a slow drag from 148pt at the finger's 0.42 px/ms: X had 133, 103, 76, 53 pt left.
    [[12, 133], [29, 103], [46, 76], [62, 53]]
        .forEach(([t, x]) => assert.ok(Math.abs(spring(148, -0.42, t) - x) <= 6, `${spring(148, -0.42, t)} vs ${x} at ${t}ms`));
    assert.ok(Math.abs(spring(148, -0.42, 400)) < 0.5);
});

test('the phone video viewer sits where X puts it and shows the post\'s counts', () => {
    const context = browserContext();
    context.window.matchMedia = query => ({matches: query.includes('(max-width: 640px)')});
    context.window.innerWidth = 430;
    context.window.innerHeight = 873;
    const app = immediateComponent(loadScripts(context, appScripts(), 'tweetApp')());
    // Landscape footage keeps its measured lift; portrait footage fills the video area.
    app.lightboxMedia = [{type: 'video', m: {type: 'video', aspect_ratio: [16, 9]}}];
    assert.equal(app.lightboxVideoLift(), 29.5);
    assert.equal(app.lightboxBackdropStyle()['--lightbox-video-lift'], '29.5px');
    app.lightboxMedia = [{type: 'video', m: {type: 'video', aspect_ratio: [9, 16]}}];
    assert.equal(app.lightboxVideoLift(), 0);
    app.lightboxMedia = [{type: 'photo', m: {type: 'photo'}}];
    assert.equal(app.lightboxVideoLift(), 0);
    const css = pageCss();
    assert.match(css, /\.lightbox-video-player > \.video-motion-surface \{ translate: 0 calc\(-1 \* var\(--lightbox-video-lift, 0px\)\); \}/);
    assert.match(css, /\.lightbox-viewer \.lightbox-actions \{\s*position: absolute;\s*right: 14px;\s*bottom: 105px;/);
    assert.match(css, /\.lightbox-video-player \.video-control-row \{ height: 62px; padding-bottom: 32px; \}/);
});

test('a tap shows or hides the phone photo viewer\'s controls instead of closing it', () => {
    const context = browserContext();
    context.window.matchMedia = query => ({matches: query.includes('(max-width: 640px)')});
    const app = immediateComponent(loadScripts(context, appScripts(), 'tweetApp')());
    app.lightboxOpen = true;
    app.lightboxMedia = [{type: 'photo'}, {type: 'photo'}];
    app.closeLightbox = () => { app.closed = true; };
    app.lightboxTap({stopPropagation() {}}, true);
    assert.equal(app.lightboxChromeHidden, true);
    app.handleLightboxBackdropClick({stopPropagation() {}});
    assert.equal(app.lightboxChromeHidden, false);
    assert.equal(app.closed, undefined);
    assert.equal(app.lightboxRestingOpacity(), 1);
    const html = pageHtml();
    assert.match(html, /'is-chrome-hidden': lightboxChromeHidden/);
    assert.match(html, /class="lightbox-dots"/);
    assert.match(html, /class="lightbox-actions" x-html="lightboxTweet \? renderActionBar\(lightboxTweet\) : ''"/);
});

test('mobile feed taps open a video viewer, sound stays inline, and desktop clicks pause', () => {
    const context = browserContext();
    const timers = [];
    context.setTimeout = callback => { timers.push(callback); return timers.length; };
    context.clearTimeout = () => {};
    context.window.matchMedia = () => ({matches: true});
    const opened = [];
    context.window.dispatchEvent = event => opened.push(event);
    const { tweetApp, tweetNookInitVideoPlayer } = loadScripts(context, appScripts(), '({tweetApp, tweetNookInitVideoPlayer})');
    const player = parseFakeHTML(tweetApp().renderMediaGrid([{type: 'video', download: {local_path: 'media/v.mp4'}}]))[0].querySelector('video').parentElement;
    const video = player.querySelector('video');
    for (const element of [player, player.querySelector('[data-video-seek]')]) element.style = {setProperty() {}};
    Object.assign(video, {src: '/media/v.mp4', paused: false, duration: 10, currentTime: 3, volume: 1, muted: true, pause() {this.paused = true;}});
    tweetNookInitVideoPlayer(video);
    assert.ok(player.classList.contains('is-native-ui-hidden'));
    dispatchFeed(player, 'pointerenter', player, {pointerType: 'touch'});
    dispatchFeed(player, 'focusin', {matches: () => false});
    assert.ok(player.classList.contains('is-native-ui-hidden'));
    dispatchFeed(player, 'click', video);
    assert.equal(opened.length, 1);
    assert.equal(opened[0].type, 'open-lightbox');
    assert.equal(opened[0].detail.originVideo, video);
    assert.equal(opened[0].detail.media[0].src, '/media/v.mp4');
    assert.equal(player.querySelector('[data-video-remaining-time]').textContent, '-0:07');
    const sound = player.querySelector('.video-idle-mute');
    assert.equal(sound.getAttribute('data-video-icon'), 'mobileMuted');
    assert.equal(sound.querySelectorAll('path')[1].getAttribute('d'), 'm17 10 4 4m0-4-4 4');
    dispatchFeed(player, 'click', sound);
    assert.equal(video.muted, false);
    assert.equal(opened.filter(event => event.type === 'open-lightbox').length, 1);
    assert.equal(opened.at(-1).type, 'video-audio-intent');
    assert.equal(opened.at(-1).detail.muted, false);
    // Taps inside the viewer still reveal/dismiss chrome without pausing.
    timers.at(-1)();
    video.closest = selector => selector === '.lightbox-stage' ? {} : null;
    dispatchFeed(player, 'click', video);
    dispatchFeed(player, 'pointerleave', player, {pointerType: 'touch'});
    assert.ok(!player.classList.contains('is-native-ui-hidden'));
    assert.equal(video.paused, false);
    dispatchFeed(player, 'click', video);
    assert.ok(player.classList.contains('is-native-ui-hidden'));
    assert.equal(video.paused, false);
    context.window.matchMedia = () => ({matches: false});
    dispatchFeed(player, 'click', video);
    assert.equal(video.paused, true);
});

test('mobile viewer transfers playback position and restores feed mute on minimize', () => {
    for (const readyState of [0, 1]) {
        const context = browserContext();
        const {tweetApp} = loadScripts(context, appScripts(), '({tweetApp})');
        const app = immediateComponent(tweetApp());
        app.videoVolume = .6;
        const origin = {currentTime: 3, muted: true, volume: .6, loop: true, isConnected: true,
            pause() {this.paused = true;}, play() {this.paused = false; return Promise.resolve();}};
        const active = new FakeElement('video', [['autoplay', ''], ['data-lightbox-index', '0'], ['data-video-player', '']]);
        Object.assign(active, {readyState, paused: true, ended: false, currentTime: 0, muted: false, volume: 1,
            closest: () => ({classList: {contains: () => true}}),
            pause() {this.paused = true;}, play() {this.paused = false; return Promise.resolve();}});
        app.$refs.lightboxStage = {querySelectorAll: () => [active], querySelector: () => active};
        app.openLightbox([{type: 'video', src: '/media/v.mp4', isGif: false}], 0, origin);
        assert.equal(origin.paused, true);
        assert.equal(app.lightboxMedia[0].loop, true);
        if (!readyState) dispatchFeed(active, 'loadedmetadata', active);
        assert.equal(active.currentTime, 3);
        assert.equal(active.muted, false);
        assert.equal(app.videoMuted, true);
        assert.equal(active.volume, .6);
        active.currentTime = 7;
        active.muted = false;
        app.syncLightboxPlayback();
        assert.equal(active.currentTime, 7); // Repeated syncing never resets progress.
        if (!readyState) active.pause();
        app.closeLightbox();
        assert.equal(app.lightboxOpen, false);
        assert.equal(active.paused, true);
        assert.equal(origin.currentTime, 7);
        assert.equal(origin.muted, true);
        assert.equal(origin.paused, !readyState);
    }
});

test('mobile video bounds wait for a playback frame, return to the feed, and respect reduced motion', async () => {
    for (const reduced of [false, true]) {
        const context = browserContext();
        context.window.matchMedia = query => ({matches: query.includes('prefers-reduced-motion') ? reduced : true});
        context.getComputedStyle = () => ({borderRadius: '16px', objectFit: 'contain'});
        const {tweetApp} = loadScripts(context, appScripts(), '({tweetApp})');
        const app = immediateComponent(tweetApp());
        const origin = {currentTime: 3, muted: true, volume: .6, loop: true, isConnected: true,
            parentElement: {}, getBoundingClientRect: () => ({left: 60, top: 200, width: 300, height: 533}),
            pause() {this.paused = true;}, play() {this.paused = false; return Promise.resolve();}};
        const values = new Map();
        const surfaceValues = new Map();
        const motions = [];
        let backdropCancels = 0;
        const viewer = {};
        app.$refs.lightboxShade = {style: {}, animate: () => ({cancel() {backdropCancels++;}})};
        const active = new FakeElement('video', [['autoplay', ''], ['data-lightbox-index', '0'], ['data-video-player', '']]);
        Object.assign(active, {readyState: 0, paused: true, ended: false, currentTime: 0, muted: false, volume: 1,
            videoWidth: 720, videoHeight: 1280,
            style: {getPropertyValue: name => values.get(name) || '', setProperty: (name, value) => values.set(name, value), removeProperty: name => values.delete(name)},
            getBoundingClientRect: () => ({left: 0, top: 0, width: 390, height: 772}),
            parentElement: {parentElement: {getBoundingClientRect: () => ({left: 0, top: 0})},
                style: {getPropertyValue: name => surfaceValues.get(name) || '', setProperty: (name, value) => surfaceValues.set(name, value), removeProperty: name => surfaceValues.delete(name)}},
            closest: selector => selector === '.lightbox-viewer' ? viewer : selector === '.lightbox-slide' ? {classList: {contains: () => true}} : null,
            pause() {this.paused = true;}, play() {this.paused = false; return Promise.resolve();},
            animate(frames, options) {
                let complete, reject;
                const finished = new Promise((resolve, fail) => {complete = resolve; reject = fail;});
                const motion = {frames, options, complete, finished, cancel() {reject(new Error('cancelled'));}};
                motions.push(motion);
                return motion;
            }});
        active.parentElement.animate = active.animate;
        app.$refs.lightboxStage = {querySelectorAll: () => [active], querySelector: () => active};
        app.openLightbox([{type: 'video', src: '/media/v.mp4', isGif: false}], 0, origin);
        assert.equal(motions.length, 0); // Do not grow a black unloaded frame.
        active.readyState = 2;
        dispatchFeed(active, 'loadedmetadata', active);
        dispatchFeed(active, 'loadeddata', active);
        if (!reduced) {
            assert.equal(app.lightboxVideoMotionPhase, 'opening');
            assert.match(motions[0].frames[0].transform, /translate3d\(60\.09375px,/); // The contained feed picture, not its frame.
            assert.ok(parseFloat(motions[0].frames[0].borderRadius) > 0);
            assert.equal(Object.keys(motions[0].frames[1]).sort().join(','), 'borderRadius,transform');
            motions[0].complete();
            await Promise.resolve(); await Promise.resolve();
            assert.equal(app.lightboxVideoMotionPhase, '');
            assert.equal(values.size, 0); // Temporary sizing/crop styles are restored.
            assert.equal(surfaceValues.size, 0);
        }
        active.currentTime = 7;
        app.startLightboxGesture({target: active, touches: [{clientX: 100, clientY: 200}], timeStamp: 100});
        assert.ok(app.lightboxGestureStart); // The enhanced video surface accepts dismissal drags.
        app.endLightboxGesture({changedTouches: [{clientX: 100, clientY: 300}], timeStamp: 200});
        if (!reduced) {
            assert.equal(app.lightboxOpen, true);
            assert.equal(app.lightboxVideoMotionPhase, 'closing');
            app.closeLightbox(); // Repeated close requests do not replace the return animation.
            assert.equal(motions.length, 2);
            assert.match(motions[1].frames[1].transform, /translate3d\(60\.09375px,/);
            assert.equal(origin.paused, true);
            motions[1].complete();
            await Promise.resolve(); await Promise.resolve();
            assert.equal(backdropCancels, 2);
        }
        assert.equal(app.lightboxOpen, false);
        assert.equal(origin.currentTime, 7);
        assert.equal(origin.paused, false);
        assert.equal(values.size, 0);
        assert.equal(surfaceValues.size, 0);
        assert.equal(motions.length, reduced ? 0 : 2);
    }
});

test('video motion keeps the resting lift exactly once in both directions', async () => {
    for (const opening of [true, false]) {
        const context = browserContext();
        const values = new Map();
        const style = {getPropertyValue: name => values.get(name) || '',
            setProperty: (name, value) => values.set(name, value), removeProperty: name => values.delete(name)};
        const surface = {style, parentElement: {getBoundingClientRect: () => ({left: 0, top: 0})},
            getBoundingClientRect: () => ({left: 0, top: Number.parseFloat(values.get('top') || '0')
                + (values.get('translate') === 'none' ? 0 : -29.5),
                width: Number.parseFloat(values.get('width') || '390'), height: Number.parseFloat(values.get('height') || '772')})};
        const video = {tagName: 'VIDEO', hasAttribute: () => true, parentElement: surface,
            style: {getPropertyValue: () => '', setProperty() {}, removeProperty() {}},
            videoWidth: 690, videoHeight: 1156, closest: () => null,
            getBoundingClientRect: surface.getBoundingClientRect};
        context.getComputedStyle = () => ({objectFit: 'contain'});
        let complete;
        surface.animate = () => ({finished: new Promise(resolve => {complete = resolve;}), cancel() {}});
        const origin = {isConnected: true, getBoundingClientRect: () => ({left: 65, top: 300, width: 316, height: 178})};
        const {animateMobileMediaBounds, fittedMediaRect} = loadScripts(context, appScripts(), '({animateMobileMediaBounds, fittedMediaRect})');
        const resting = fittedMediaRect(video);
        const motion = animateMobileMediaBounds(video, origin, opening, 280);
        assert.ok(Math.abs(surface.getBoundingClientRect().top - resting.top) < .001);
        assert.equal(values.get('translate'), 'none');
        complete(); await motion.finished;
        motion.cancel();
        assert.equal(values.size, 0); // The CSS lift resumes only when temporary absolute sizing is removed.
    }
});

test('video motion holds its final surface for handoff and preserves cropped picture geometry', async () => {
    for (const mode of ['quote', 'grid', 'fill', 'occluded']) {
        const fill = mode === 'fill', occluded = mode === 'occluded', quote = mode === 'quote', grid = mode === 'grid';
        const context = browserContext();
        context.window.innerWidth = 390;
        context.window.innerHeight = 844;
        if (occluded) context.document.querySelectorAll = selector => selector === '[data-video-occluder]'
            ? [{getBoundingClientRect: () => ({left: 0, top: 0, width: 390, height: 90})}] : [];
        const surfaceValues = new Map(), videoValues = new Map();
        const style = values => ({getPropertyValue: name => values.get(name) || '', setProperty: (name, value) => values.set(name, value), removeProperty: name => values.delete(name)});
        let computedRadius = '0px', computedCrop = 'none';
        const surface = {style: style(surfaceValues), parentElement: {getBoundingClientRect: () => ({left: 0, top: 0})},
            getBoundingClientRect: () => ({left: 20, top: 80, width: 195, height: 347})};
        const video = {parentElement: surface, style: style(videoValues), videoWidth: 720, videoHeight: 1280,
            getBoundingClientRect: () => ({left: 0, top: 0, width: 390, height: 772}), closest: () => null,
            animate() {throw new Error('Video animation would disturb the decoder surface.');}};
        context.getComputedStyle = element => element === video
            ? {objectFit: videoValues.get('object-fit') || (fill ? 'cover' : 'contain')}
            : element === surface ? {borderRadius: computedRadius, clipPath: computedCrop} : {borderRadius: '16px'};
        let complete, frames;
        surface.animate = keyframes => {
            frames = keyframes;
            return {finished: new Promise(resolve => {complete = resolve;}), cancel() {}};
        };
        const {animateMobileMediaBounds} = loadScripts(context, appScripts(), '({animateMobileMediaBounds})');
        const originValues = new Map();
        const origin = {isConnected: true, style: style(originValues),
            // The inline player's placeholder records how the feed drew its picture.
            getAttribute: name => ({'data-feed-fit': grid ? 'cover' : 'contain', 'data-feed-background': 'rgb(0, 0, 0)'})[name],
            getBoundingClientRect: () => ({left: 60, top: occluded ? -120 : 200, width: 300, height: fill || occluded ? 533 : 168.75})};
        const motion = animateMobileMediaBounds(video, origin, false, 280,
            {left: 0, top: fill ? 0 : (772 - 390 * 1280 / 720) / 2, width: 390, height: fill ? 772 : 390 * 1280 / 720});
        assert.ok(motion);
        assert.equal(videoValues.get('width'), '100%');
        assert.equal(videoValues.get('height'), '100%');
        assert.equal(videoValues.get('object-fit'), 'contain');
        assert.ok(Math.abs(parseFloat(surfaceValues.get('width')) / parseFloat(surfaceValues.get('height')) - 720 / 1280) < .00001);
        if (!quote) assert.ok(frames.some(frame => frame.clipPath)); // A grid crop or fill-screen crop belongs to the surface.
        if (fill) assert.match(frames[0].clipPath, /inset\(0px 22\.125px/); // Keep the current fill crop at dismissal start.
        else if (grid) assert.match(frames[1].clipPath, /inset\(236\.9/); // Return to the wide cropped grid cell without stretching.
        else if (quote) {
            // A quoted portrait video is pillarboxed in its 16:9 frame: land on that picture, uncropped.
            assert.ok(frames.every(frame => !frame.clipPath));
            const [, x, , scale] = frames[1].transform.match(/translate3d\(([-\d.]+)px, ([-\d.]+)px, 0\) scale\(([-\d.]+)\)/).map(Number);
            const width = 390 * scale;
            assert.ok(Math.abs(width - 168.75 * 720 / 1280) < .001);
            assert.ok(Math.abs(x - (60 + (300 - width) / 2)) < .001);
            assert.equal(originValues.get('background-color'), 'rgb(0, 0, 0)'); // Its bars stay black during flight.
        }
        else {
            const transform = frames[1].transform.match(/translate3d\(([-\d.]+)px, ([-\d.]+)px, 0\) scale\(([-\d.]+)\)/);
            const clip = frames[1].clipPath.match(/inset\(([\d.]+)px ([\d.]+)px ([\d.]+)px ([\d.]+)px/);
            const visibleTop = parseFloat(surfaceValues.get('top')) + Number(transform[2]) + Number(clip[1]) * Number(transform[3]);
            assert.ok(Math.abs(visibleTop - 90) < .001); // Last frame matches the sticky header's occlusion, before DOM handoff.
            assert.match(frames[1].clipPath, /round 0px 0px /); // The header cuts a straight edge, not new rounded corners.
            const visibleBottom = parseFloat(surfaceValues.get('top')) + Number(transform[2])
                + (parseFloat(surfaceValues.get('height')) - Number(clip[3])) * Number(transform[3]);
            assert.ok(Math.abs(visibleBottom - 413) < .001); // The header clip must not recenter the picture.
        }
        computedRadius = '24px'; computedCrop = 'inset(20px 10px round 24px)';
        const interrupted = motion.bounds();
        const scale = 195 / parseFloat(surfaceValues.get('width'));
        assert.equal(interrupted.left, 20 + 10 * scale);
        assert.equal(interrupted.top, 80 + 20 * scale);
        assert.equal(interrupted.radius, 24 * scale); // A mid-flight reversal preserves its visible corners.
        complete();
        assert.equal(await motion.finished, true);
        assert.ok(surfaceValues.size > 0); // Don't reveal fullscreen before the caller restores the feed.
        motion.cancel();
        assert.equal(surfaceValues.size, 0);
        assert.equal(videoValues.size, 0);
        assert.equal(originValues.size, 0); // The placeholder's own look returns at handoff.
    }
});

test('dismissal fades the page shade and lifts covering headers above the returning picture', async () => {
    for (const raisable of [true, false]) {
        const context = browserContext();
        context.window.visualViewport = {offsetLeft: 0, offsetTop: 0, width: 390, height: 844};
        const style = entries => ({getPropertyValue: name => entries.get(name) || '',
            setProperty: (name, value) => entries.set(name, value), removeProperty: name => entries.delete(name)});
        const viewer = {};
        const headerValues = new Map([['z-index', '20']]);
        const fades = [];
        const animate = target => (frames, options) => {
            const fade = {target, frames, options, cancelled: false, cancel() {this.cancelled = true;}};
            fades.push(fade); return fade;
        };
        const title = {};
        const header = {style: style(headerValues), contains: node => node === title, animate: animate('header'),
            getBoundingClientRect: () => ({left: 0, top: 0, width: 390, height: 90})};
        let hitPoint;
        context.document.querySelectorAll = selector => selector === '[data-video-occluder]' ? [header] : [];
        context.document.elementFromPoint = (x, y) => {
            hitPoint = [x, y];
            return raisable && headerValues.get('z-index') === '101' ? title : viewer;
        };
        context.getComputedStyle = element => element === viewer ? {zIndex: '100'} : {objectFit: 'contain', borderRadius: '16px'};
        const shade = {animate: animate('shade')};
        let frames, complete;
        const surface = {style: style(new Map()), parentElement: {getBoundingClientRect: () => ({left: 0, top: 0})},
            animate: keyframes => {frames = keyframes; return {finished: new Promise(resolve => {complete = resolve;}), cancel() {}};}};
        const video = {parentElement: surface, style: style(new Map()), videoWidth: 720, videoHeight: 1280,
            getBoundingClientRect: () => ({left: 0, top: 0, width: 390, height: 772}), closest: () => viewer};
        const origin = {isConnected: true, getBoundingClientRect: () => ({left: 60, top: 40, width: 300, height: 300 * 1280 / 720})};
        const {animateMobileMediaBounds} = loadScripts(context, appScripts(), '({animateMobileMediaBounds})');
        const motion = animateMobileMediaBounds(video, origin, false, 280, null, shade);
        assert.deepEqual(hitPoint, [210, 65]); // Probe where the header covers the returning picture.
        const shadeFade = fades.find(fade => fade.target === 'shade');
        assert.equal(JSON.stringify(shadeFade.frames), '[{"opacity":1},{"opacity":0}]'); // Compositor opacity, not a repainted color.
        assert.equal(shadeFade.options.duration, 120); // The page returns before the picture lands.
        assert.equal(shadeFade.options.easing, 'ease-out');
        const headerFade = fades.find(fade => fade.target === 'header');
        if (raisable) {
            assert.equal(headerValues.get('z-index'), '101'); // The picture travels beneath the header, as it will rest.
            assert.equal(frames[1].clipPath, undefined); // No header crop that would reappear behind its glass.
            assert.equal(JSON.stringify(headerFade.frames), '[{"opacity":0},{"opacity":1}]'); // It fades in with the rest of the page.
            assert.equal(headerFade.options, shadeFade.options);
        } else {
            assert.equal(headerValues.get('z-index'), '20'); // A header in another stacking context isn't moved.
            assert.equal(headerFade, undefined);
            const top = frames[1].clipPath.match(/^inset\(([\d.]+)px/)[1];
            assert.ok(Math.abs(Number(top) * 300 / 390 - 50) < .001); // It keeps the crop at its 90px edge instead.
        }
        complete();
        assert.equal(await motion.finished, true);
        motion.cancel();
        assert.deepEqual([...headerValues], [['z-index', '20']]);
        assert.ok(fades.every(fade => fade.cancelled));
    }
});

test('phone media viewer stays outside Safari toolbar sampling and backs itself with page content', () => {
    const css = pageCss();
    const html = pageHtml();
    const mobile = css.slice(css.indexOf('/* The phone feed uses a sound badge'));
    const viewer = mobile.match(/\n    \.lightbox-viewer \{([^}]*)\}/)[1];
    const bleed = Number(viewer.match(/--lightbox-bleed:\s*(\d+)px/)[1]);
    assert.ok(2 * bleed > .05 * 1366); // Over 105% of even a tall viewport, on every side.
    assert.match(viewer, /inset:\s*calc\(-1 \* var\(--lightbox-bleed\)\)/);
    assert.match(viewer, /background-color:\s*transparent !important/); // The fixed layer adds no sampled color.
    assert.match(mobile, /\.lightbox-viewer > \.lightbox-stage,\s*\.lightbox-viewer > \.lightbox-chrome \{ inset: var\(--lightbox-bleed\); \}/);
    assert.match(mobile, /\.lightbox-viewer \.lightbox-close-button \{\s*top: calc\(var\(--lightbox-bleed\) \+/);
    assert.match(mobile, /\.lightbox-viewer:not\(\.is-video-closed\) \+ \.lightbox-shade \{ display: block; \}/);
    // Photos and GIFs move or fade themselves on a phone, never with Alpine's overlay fade.
    assert.match(mobile, /\.lightbox-viewer\.lightbox-overlay-enter,\s*\.lightbox-viewer\.lightbox-overlay-leave \{ transition: none; \}/);
    assert.match(mobile, /\.is-video-preparing \.lightbox-slide\.is-active \.lightbox-media \{ visibility: hidden; \}/);
    assert.match(css, /\.lightbox-shade \{[^}]*display: none;[^}]*position: absolute;[^}]*z-index: 99;[^}]*pointer-events: none;/s);
    assert.match(html, /<\/div>\s*<div x-ref="lightboxShade" class="lightbox-shade" :style="lightboxShadeStyle\(\)" aria-hidden="true"><\/div>\s*<!-- Tag Modal -->/);
    assert.match(css, /\.lightbox-shade \{[^}]*background: #000;/s);
    assert.doesNotMatch(css, /is-video-closing/);
});

test('video motion crops its feed frame at the visible viewport in both directions', async () => {
    for (const opening of [true, false]) {
        const context = browserContext();
        context.window.visualViewport = {offsetLeft: 0, offsetTop: 42, width: 390, height: 730};
        context.getComputedStyle = () => ({objectFit: 'contain', borderRadius: '16px'});
        const style = () => ({getPropertyValue: () => '', setProperty() {}, removeProperty() {}});
        let frames, complete;
        const surface = {style: style(), parentElement: {getBoundingClientRect: () => ({left: 0, top: 42})},
            animate: keyframes => {frames = keyframes; return {finished: new Promise(resolve => {complete = resolve;}), cancel() {}};}};
        const video = {parentElement: surface, style: style(), videoWidth: 720, videoHeight: 1280,
            getBoundingClientRect: () => ({left: 0, top: 42, width: 390, height: 650}), closest: () => null};
        const origin = {isConnected: true, getBoundingClientRect: () => ({left: 60, top: 500, width: 300, height: 300 * 1280 / 720})};
        const {animateMobileMediaBounds} = loadScripts(context, appScripts(), '({animateMobileMediaBounds})');
        const motion = animateMobileMediaBounds(video, origin, opening, 280);
        const insets = frames[opening ? 0 : 1].clipPath.match(/^inset\((.*?) round/)[1].split(' ').map(parseFloat);
        assert.ok(insets[2] > 0); // Safari never draws a fixed layer beneath its toolbar, so cut a straight edge there.
        assert.ok(Math.abs(insets[0]) < .001 && Math.abs(insets[1]) < .001 && Math.abs(insets[3]) < .001);
        complete();
        assert.equal(await motion.finished, true);
        motion.cancel();
    }
});

function mediaMotionFixture(context) {
    const styleOf = values => ({getPropertyValue: name => values.get(name) || '',
        setProperty: (name, value) => values.set(name, value), removeProperty: name => values.delete(name)});
    const motions = [];
    const record = target => (frames, options) => {
        let complete;
        const motion = {target, frames, options, cancelled: false, complete: () => complete(),
            finished: new Promise(resolve => {complete = resolve;}), cancel() {this.cancelled = true;}};
        motions.push(motion);
        return motion;
    };
    const viewer = new FakeElement('div', [['class', 'lightbox-viewer']]);
    viewer.connected = true;
    viewer.style = {};
    viewer.animate = record('viewer');
    const stage = new FakeElement('div', [['class', 'lightbox-stage']]);
    viewer.appendChild(stage);
    const shade = {style: {}, animate: record('shade')};
    return {styleOf, motions, record, viewer, stage, shade};
}

test('in-flight media joins the page so Safari draws it beneath its toolbars', async () => {
    for (const opening of [true, false]) {
        const context = browserContext();
        context.window.scrollX = 0;
        context.window.scrollY = 1200;
        context.window.visualViewport = {offsetLeft: 0, offsetTop: 0, width: 390, height: 714};
        const styleOf = values => ({getPropertyValue: name => values.get(name) || '',
            setProperty: (name, value) => values.set(name, value), removeProperty: name => values.delete(name)});
        const viewerValues = new Map(), stageValues = new Map();
        const stage = {style: styleOf(stageValues), getBoundingClientRect: () => ({left: 0, top: 0, width: 390, height: 714})};
        const viewer = {style: styleOf(viewerValues), querySelector: selector => selector === '.lightbox-stage' ? stage : null};
        context.getComputedStyle = element => element === viewer ? {zIndex: '100'} : {objectFit: 'contain', borderRadius: '0px'};
        let frames, complete;
        const surface = {style: styleOf(new Map()), parentElement: {getBoundingClientRect: () => ({left: 0, top: 0})},
            animate: keyframes => {frames = keyframes; return {finished: new Promise(resolve => {complete = resolve;}), cancel() {}};}};
        const video = {parentElement: surface, style: styleOf(new Map()), videoWidth: 720, videoHeight: 1280,
            getBoundingClientRect: () => ({left: 0, top: 0, width: 390, height: 650}), closest: () => viewer};
        // The feed video's lower part sits beneath Safari's toolbar, past the 714px viewport.
        const origin = {isConnected: true, getBoundingClientRect: () => ({left: 60, top: 500, width: 300, height: 300 * 1280 / 720})};
        const {animateMobileMediaBounds} = loadScripts(context, appScripts(), '({animateMobileMediaBounds})');
        const motion = animateMobileMediaBounds(video, origin, opening, 280);
        assert.equal(viewerValues.get('position'), 'absolute'); // In the page at the same screen position.
        assert.equal(viewerValues.get('top'), '1200px');
        assert.equal(viewerValues.get('left'), '0px');
        assert.equal(viewerValues.get('width'), '390px');
        assert.equal(viewerValues.get('height'), '714px');
        assert.equal(viewerValues.get('--lightbox-bleed'), '0px');
        assert.equal(stageValues.get('overflow'), 'visible');
        assert.doesNotMatch(stageValues.get('contain'), /paint|strict/); // Paint containment would clip it again.
        assert.equal(frames[opening ? 0 : 1].clipPath, undefined); // No crop at the viewport's bottom edge.
        complete();
        assert.equal(await motion.finished, true);
        motion.cancel();
        assert.equal(viewerValues.size, 0); // Back to the screen-fixed viewer once the motion ends.
        assert.equal(stageValues.size, 0);
    }
});

test('phone photos expand from their tile and return to the one on screen, or fade when it is gone', async () => {
    const context = browserContext();
    context.window.matchMedia = query => ({matches: !query.includes('prefers-reduced-motion')});
    context.window.innerHeight = 844;
    const opened = [];
    context.window.dispatchEvent = event => opened.push(event.detail);
    const {tweetApp, tweetNookOpenLightbox} = loadScripts(context, appScripts(), '({tweetApp, tweetNookOpenLightbox})');
    const app = immediateComponent(tweetApp());
    const {styleOf, motions, record, viewer, stage, shade} = mediaMotionFixture(context);
    const photo = name => ({type: 'photo', download: {local_path: `media/${name}.jpg`}});
    const feed = parseFakeHTML(app.renderMediaGrid([photo('a'), photo('b')]))[0];
    feed.connected = true;
    feed.rect = {left: 50, top: 300, width: 302, height: 170};
    const tiles = feed.querySelectorAll('img');
    // A video poster in an earlier gallery cell is decoration, not another slide.
    tiles[0].parentElement.appendChild(new FakeElement('img', [['data-video-poster', '']]));
    tiles[0].rect = {left: 51, top: 301, width: 149, height: 168};
    tiles[1].rect = {left: 202, top: 301, width: 149, height: 168};
    const tileValues = tiles.map(tile => { const values = new Map(); tile.style = styleOf(values); return values; });
    const images = [0, 1].map(() => {
        const slide = new FakeElement('div', [['class', 'lightbox-slide']]);
        slide.style = {};
        const image = new FakeElement('img', [['class', 'lightbox-media']]);
        Object.assign(image, {complete: true, naturalWidth: 1200, naturalHeight: 1600, style: styleOf(new Map()),
            rect: {left: 0, top: 162, width: 390, height: 520}, animate: record('image')});
        slide.appendChild(image);
        stage.appendChild(slide);
        return image;
    });
    const show = index => stage.children.forEach((slide, slideIndex) => slide.classList.toggle('is-active', slideIndex === index));
    context.getComputedStyle = element => element === feed ? {borderTopLeftRadius: '16px', borderTopWidth: '1px'}
        : images.includes(element) ? {objectFit: 'contain', borderRadius: '0px'}
        : element === viewer ? {zIndex: '100'}
        : element === shade ? {opacity: '0.95'} : {objectFit: 'cover'};
    Object.assign(app.$refs, {lightboxViewer: viewer, lightboxStage: stage, lightboxShade: shade});

    tweetNookOpenLightbox(tiles[1], 1);
    assert.equal(opened[0].origin, tiles[1]); // The tapped tile is where the photo expands from.
    show(1);
    app.openLightbox(opened[0].media, opened[0].index, null, opened[0].origin);
    const expand = motions[0];
    assert.equal(expand.target, 'image'); // The photo moves itself; it has no player wrapper.
    const darken = motions.find(motion => motion.target === 'shade');
    // Ends at the photo backdrop's resting opacity, so nothing snaps when the motion is released.
    assert.equal(JSON.stringify(darken.frames), '[{"opacity":0},{"opacity":1}]');
    assert.equal(app.lightboxRestingOpacity(), 1);
    assert.equal(images[1].style.getPropertyValue('transform-origin'), '0 0');
    assert.equal(images[1].style.getPropertyValue('position'), 'absolute');
    assert.equal(images[1].style.getPropertyValue('width'), '390px'); // The moving surface fits the picture, excluding letterbox space.
    assert.equal(images[1].style.getPropertyValue('height'), '520px');
    assert.match(expand.frames[0].clipPath, /^inset\([\d.]+px 0px [\d.]+px 0px round 0px [1-9][\d.]*px [1-9][\d.]*px 0px\)$/);
    assert.equal(app.lightboxVideoMotionPhase, 'opening');
    assert.equal(tileValues[1].get('visibility'), 'hidden'); // One copy of the photo on screen at a time.
    expand.complete(); await Promise.resolve(); await Promise.resolve();
    assert.equal(app.lightboxVideoMotionPhase, '');
    assert.equal(tileValues[1].size, 0);

    app.lightboxIndex = 0;
    show(0);
    app.closeLightbox();
    const land = motions.find(motion => motion.target === 'image' && motion !== expand);
    assert.ok(land, 'the photo now on screen returns');
    assert.equal(tileValues[0].get('visibility'), 'hidden'); // Its own tile, not the one it opened from.
    assert.equal(tileValues[1].size, 0);
    // The left tile meets the grid's rounded corners on its left only.
    assert.match(land.frames[1].clipPath, /round [1-9][\d.]*px 0px 0px [1-9][\d.]*px\)$/);
    const backdrop = motions.find(motion => motion.target === 'shade' && motion.options.duration === 120);
    assert.equal(JSON.stringify(backdrop.frames), '[{"opacity":0.95},{"opacity":0}]');
    land.complete(); await Promise.resolve(); await Promise.resolve();
    assert.equal(app.lightboxOpen, false);
    assert.equal(tileValues[0].size, 0); // Shown again in the same frame the viewer hides.
    assert.equal(viewer.style.display, 'none'); // Hidden at handoff, with no Alpine fade over the feed.
    tweetNookOpenLightbox(tiles[0], 0);
    assert.equal(opened.length, 1); // Safari's compatibility click after dismissal doesn't reopen it.

    show(1);
    app.openLightbox(opened[0].media, 1, null, tiles[1]);
    motions.at(-1).complete(); await Promise.resolve(); await Promise.resolve();
    tiles.forEach(tile => { tile.rect = {...tile.rect, top: -400}; });
    const before = motions.length;
    app.closeLightbox();
    const fades = motions.slice(before);
    assert.deepEqual(fades.map(motion => motion.target).sort(), ['shade', 'viewer']); // Nothing to land on: fade.
    assert.equal(JSON.stringify(fades[0].frames), '[{"opacity":1},{"opacity":0}]');
    assert.equal(app.lightboxVideoMotionPhase, 'closing');
    fades.forEach(fade => fade.complete());
    await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    assert.equal(app.lightboxOpen, false);
});

test('a fresh touch reopens photos and videos immediately while dismissal compatibility clicks stay blocked', () => {
    const context = browserContext();
    context.window.matchMedia = () => ({matches: true});
    const {tweetApp, tweetNookOpenLightbox, tweetNookOpenMobileVideo} = loadScripts(context,
        appScripts(), '({tweetApp, tweetNookOpenLightbox, tweetNookOpenMobileVideo})');
    const app = immediateComponent(tweetApp());
    app.fetchSetup = app.fetchNotices = app.fetchActivityStatus = async () => {};
    app.initApp();
    const opened = [];
    context.window.dispatchEvent = event => opened.push(event);
    const holder = {dataset: {lightboxMedia: JSON.stringify([{type: 'photo', src: '/photo.png'}])}};
    const photo = {closest: () => holder};
    for (const open of [() => tweetNookOpenLightbox(photo, 0), () => tweetNookOpenMobileVideo({src: '/video.mp4'})]) {
        app.lightboxMedia = [{type: 'photo'}];
        app.lightboxOpen = true;
        app.finishCloseLightbox();
        const before = opened.length;
        open();
        assert.equal(opened.length, before); // No new finger-down: the late compatibility click is ignored.
        context.__state.events.get('document:touchstart')();
        open();
        assert.equal(opened.length, before + 1); // A new tap needs no timeout or second tap.
    }
});

test('phone GIF taps open the viewer, which continues from the feed frame', async () => {
    const context = browserContext();
    let phone = false;
    context.window.matchMedia = query => ({matches: phone && !query.includes('prefers-reduced-motion')});
    const opened = [];
    context.window.dispatchEvent = event => opened.push(event.detail);
    const {tweetApp} = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    const {styleOf, motions, record, viewer, stage, shade} = mediaMotionFixture(context);
    const feed = parseFakeHTML(app.renderMediaGrid([{type: 'animated_gif', download: {local_path: 'media/g.mp4'}}]))[0];
    feed.connected = true;
    const gif = feed.querySelector('video');
    assert.match(gif.getAttribute('onclick'), /window\.tweetNookOpenGif\(event, this, 0\)/);
    const click = {stopPropagation() {this.stopped = true;}};
    context.window.tweetNookOpenGif(click, gif, 0);
    assert.equal(opened.length, 0); // Desktop GIFs keep playing in place.
    assert.ok(!click.stopped);
    phone = true;
    context.window.tweetNookOpenGif(click, gif, 0);
    assert.ok(click.stopped); // The card behind it doesn't open the thread.
    assert.equal(opened[0].origin, gif);
    assert.equal(opened[0].media[0].isGif, true);

    Object.assign(gif, {currentTime: 5.5, rect: {left: 60, top: 200, width: 300, height: 270}});
    const slide = new FakeElement('div', [['class', 'lightbox-slide is-active']]);
    slide.style = {};
    const loop = new FakeElement('video', [['class', 'lightbox-media'], ['autoplay', ''], ['loop', '']]);
    let time = 0;
    Object.defineProperty(loop, 'currentTime', {get: () => time, set: value => { time = value; loop.seeking = true; }});
    Object.assign(loop, {readyState: 1, duration: 4, seeking: false, paused: true, style: styleOf(new Map()),
        rect: {left: 0, top: 233, width: 390, height: 351}, animate: record('gif'),
        play() { this.paused = false; return Promise.resolve(); }, pause() { this.paused = true; }});
    slide.appendChild(loop);
    stage.appendChild(slide);
    context.getComputedStyle = element => element === loop ? {objectFit: 'contain'} : element === viewer ? {zIndex: '100'} : {borderRadius: '16px'};
    Object.assign(app.$refs, {lightboxViewer: viewer, lightboxStage: stage, lightboxShade: shade});
    app.openLightbox(opened[0].media, 0, null, gif);
    assert.equal(loop.currentTime, 1.5); // 5.5s into a 4s loop.
    assert.equal(motions.length, 0); // Wait for the seeked frame rather than expanding frame 0.
    Object.assign(loop, {readyState: 2, seeking: false});
    dispatchFeed(loop, 'seeked', loop);
    assert.equal(motions[0].target, 'gif');
    assert.match(motions[0].frames[0].transform, /translate3d\(60px, -33px, 0\) scale\(0\.769/); // From the feed GIF's box.
    assert.equal(loop.paused, false);
});

test('Home Screen apps are told apart from Safari tabs for bottom spacing', () => {
    for (const standalone of [true, false]) {
        const context = browserContext();
        context.navigator = {standalone};
        loadScripts(context, appScripts(), 'null');
        assert.equal(context.__state.rootClasses.has('ios-home-screen'), standalone);
    }
    const css = pageCss();
    const html = pageHtml();
    // Safari tabs sit on the toolbar's reserved strip; Home Screen apps reach the screen edge.
    assert.match(css, /\.lightbox-video-player \{[^}]*--video-controls-bottom: 0px;/s);
    assert.match(css, /\.ios-home-screen \.lightbox-video-player \{ --video-controls-bottom: max\(8px, env\(safe-area-inset-bottom\)\); \}/);
    assert.match(css, /:root:not\(\.ios-home-screen\) \.scroll-to-top \{ bottom: 12px; \}/);
    assert.match(html, /class="scroll-to-top"/);
    assert.match(css, /\.scroll-to-top \{[^}]*right: max\(24px, env\(safe-area-inset-right\)\);/s);
    assert.match(css, /\.scroll-to-top \{[^}]*bottom: max\(24px, env\(safe-area-inset-bottom\)\);/s);
});

test('phone media viewers keep the page paintable under Safari chrome while blocking one-finger page pans', () => {
    const context = browserContext();
    let phone = true;
    context.window.matchMedia = () => ({matches: phone});
    context.window.scrollY = 500;
    const {tweetApp} = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    const watchers = {};
    app.$watch = (name, callback) => {watchers[name] = callback;};
    app.fetchSetup = async () => {};
    app.fetchNotices = async () => {};
    app.fetchActivityStatus = async () => {};
    app.initApp();
    app.lightboxMedia = [{type: 'video', isGif: false}];
    app.lightboxOpen = true;
    context.document.body.style.overflow = 'auto';
    watchers.lightboxOpen(true);
    assert.equal(context.document.body.style.overflow, 'auto'); // Don't make Safari repaint its under-toolbar region on close.
    let prevented = 0;
    app.moveLightboxGesture({touches: [{}], preventDefault() {prevented++;}});
    assert.equal(prevented, 1); // Block background pan even if the touch began on controls.
    app.moveLightboxGesture({touches: [{}, {}], preventDefault() {prevented++;}});
    assert.equal(prevented, 1); // Native pinch zoom remains available.
    assert.equal(context.window.scrollY, 500);
    app.lightboxOpen = false;
    watchers.lightboxOpen(false);
    assert.equal(context.document.body.style.overflow, 'auto');
    app.lightboxMedia = [{type: 'photo'}];
    app.lightboxOpen = true;
    watchers.lightboxOpen(true);
    assert.equal(context.document.body.style.overflow, 'auto'); // Photos and GIFs use the same phone viewer.
    app.moveLightboxGesture({touches: [{}], preventDefault() {prevented++;}});
    assert.equal(prevented, 2);
    watchers.lightboxOpen(false);
    phone = false;
    watchers.lightboxOpen(true);
    assert.equal(context.document.body.style.overflow, 'hidden'); // Desktop modal locking is retained.
    watchers.lightboxOpen(false);
    assert.equal(context.document.body.style.overflow, 'auto');
});

test('mobile viewer reuses its inline player and rejects dismissal click-through without native fullscreen', async () => {
    const context = browserContext();
    context.window.matchMedia = query => ({matches: !query.includes('prefers-reduced-motion')});
    context.getComputedStyle = () => ({borderRadius: '16px', objectFit: 'contain'});
    let now = 1000;
    context.Date = class extends Date { static now() {return now;} };
    const uiTimers = [];
    context.setTimeout = (callback, delay) => {uiTimers.push({callback, delay}); return uiTimers.length;};
    context.clearTimeout = () => {};
    const opened = [];
    context.window.dispatchEvent = event => opened.push(event);
    const {tweetApp, tweetNookInitVideoPlayer} = loadScripts(context, appScripts(), '({tweetApp, tweetNookInitVideoPlayer})');
    const app = immediateComponent(tweetApp());
    const pendingTicks = [];
    app.$nextTick = callback => pendingTicks.push(callback);
    const feed = parseFakeHTML(app.renderMediaGrid([{type: 'video', download: {local_path: 'media/v.mp4'}}]))[0];
    feed.connected = true;
    const video = feed.querySelector('video'), player = video.parentElement;
    const stage = new FakeElement('div', [['class', 'lightbox-stage']]);
    stage.connected = true;
    const slide = new FakeElement('div', [['class', 'lightbox-slide is-active']]);
    const slot = new FakeElement('div', [['data-inline-video-slot', '']]);
    slide.appendChild(slot); stage.appendChild(slide);
    app.$refs.lightboxStage = stage;
    const viewer = new FakeElement('div', [['class', 'lightbox-viewer is-video-closed']]);
    viewer.connected = true;
    viewer.appendChild(stage);
    const viewerValues = new Map();
    viewer.style = {display: 'none', setProperty: (name, value) => viewerValues.set(name, value),
        getPropertyValue: name => viewerValues.get(name) || '', removeProperty: name => viewerValues.delete(name)};
    viewer.rect = {left: 0, top: 0, width: 390, height: 844};
    context.window.innerHeight = 844;
    context.window.scrollY = 240;
    app.$refs.lightboxViewer = viewer;
    const shade = {style: {}};
    app.$refs.lightboxShade = shade;
    const fades = [];
    shade.animate = (frames, options) => {
        const fade = {frames, options, cancelled: false, cancel() {this.cancelled = true;}};
        fades.push(fade); return fade;
    };
    context.getComputedStyle = element => element === shade
        ? {opacity: '0.6'} : {borderRadius: '16px', objectFit: 'contain'};
    app.$refs.inlineVideoSlot = slot;
    slot.style = {display: 'none'};
    const originalParent = player.parentElement;
    player.rect = {left: 60, top: 200, width: 300, height: 533};
    player.getBoundingClientRect = () => player.closest('.lightbox-stage') ? {left: 0, top: 0, width: 390, height: 844} : player.rect;
    video.getBoundingClientRect = () => video.closest('.lightbox-stage') ? {left: 0, top: slot.style.transform?.includes('180') ? 180 : 0, width: 390, height: 772} : player.rect;
    for (const element of [video, player, player.querySelector('[data-video-seek]')]) {
        const values = new Map();
        element.style = {setProperty: (name, value) => values.set(name, value), getPropertyValue: name => values.get(name) || '', removeProperty: name => values.delete(name)};
    }
    const motions = [];
    let nativeFullscreen = 0;
    let playParent;
    Object.assign(video, {readyState: 0, paused: false, ended: false, currentTime: 3, volume: .6, muted: true,
        videoWidth: 720, videoHeight: 1280, loop: true,
        pause() {this.paused = true;}, play() {playParent = this.closest('.lightbox-stage'); this.paused = false; return Promise.resolve();},
        webkitEnterFullscreen() {nativeFullscreen++;},
        animate(frames) {
            let complete;
            const finished = new Promise(resolve => {complete = resolve;});
            const motion = {frames, finished, complete, cancel() {}};
            motions.push(motion); return motion;
        }});
    player.animate = video.animate;
    context.document.createElement = tag => {
        const surface = new FakeElement(tag).cloneNode(false);
        surface.animate = player.animate;
        surface.getBoundingClientRect = video.getBoundingClientRect;
        return surface;
    };
    video.animate = () => {throw new Error('The decoder surface must not be animated.');};
    player.requestFullscreen = () => {nativeFullscreen++; return Promise.resolve();};
    tweetNookInitVideoPlayer(video);
    app.openLightbox([{type: 'video', src: '/media/v.mp4', isGif: false}], 0, video);
    assert.equal(playParent, stage); // Mount and play run in the original trusted tap.
    assert.equal(video.muted, false);
    assert.equal(app.videoMuted, true);
    assert.equal(app.activeLightboxVideo(), video);
    assert.equal(feed.querySelectorAll('video').length, 0);
    assert.equal(stage.querySelectorAll('video').length, 1);
    assert.equal(video.currentTime, 3);
    assert.equal(viewer.style.display, 'flex');
    assert.equal(context.window.scrollY, 240); // Opening doesn't move the document.
    assert.equal(shade.style.top, '-604px'); // The page shade spans a viewport above and below the visible page.
    assert.equal(shade.style.height, '2532px');
    context.window.scrollY = 300;
    context.__state.events.get('scroll')();
    assert.equal(shade.style.top, '-544px'); // It follows any scroll while the viewer is open.
    context.window.scrollY = 240;
    context.__state.events.get('scroll')();
    assert.equal(JSON.stringify(fades[0].frames), '[{"opacity":0},{"opacity":1}]');
    assert.equal(fades[0].options.duration, 150); // The backdrop is dark well before the 300ms expansion lands.
    assert.equal(fades[0].options.easing, 'ease-out');
    assert.equal(slot.style.display, '');
    assert.equal(app.lightboxVideoMotionPhase, 'opening'); // First frame is ready before Alpine's next tick.
    assert.equal(motions.length, 1);
    assert.ok(video.parentElement.classList.contains('video-motion-surface'));
    assert.equal(player.querySelector('[data-video-controls]').parentElement, player); // Controls remain outside the animated picture.
    assert.equal(player.style.getPropertyValue('height'), ''); // The fullscreen control anchor never resizes.
    pendingTicks.shift()();
    assert.equal(motions.length, 1); // The deferred overlay mount must not restart motion.
    assert.equal(app.lightboxVideoMotionPhase, 'opening'); // No metadata/decoder wait for the existing poster.
    motions[0].complete(); await Promise.resolve(); await Promise.resolve();
    uiTimers.at(-1).callback();
    assert.ok(player.classList.contains('is-native-ui-hidden'));
    assert.ok(viewer.classList.contains('is-video-controls-hidden'));
    dispatchFeed(player, 'pointermove', player, {pointerType: 'mouse'});
    assert.ok(player.classList.contains('is-native-ui-hidden')); // Mobile tap visibility must not fight hover events.
    dispatchFeed(player, 'click', player);
    assert.ok(!player.classList.contains('is-native-ui-hidden'));
    assert.ok(!viewer.classList.contains('is-video-controls-hidden'));
    dispatchFeed(player, 'focusout', player);
    assert.equal(uiTimers.at(-1).delay, 3000); // Touch focus changes keep the normal reading interval.
    assert.ok(player.classList.contains('is-video-fill')); // Portrait footage starts filled.
    dispatchFeed(player, 'click', player.querySelector('[data-video-action="fullscreen"]'));
    assert.ok(!player.classList.contains('is-video-fill')); // Fit reveals the uncropped picture.
    assert.equal(nativeFullscreen, 0);
    video.currentTime = 7;
    slot.style.transform = 'translate3d(0, 180px, 0)';
    app.lightboxGestureOffsetY = 180;
    app.closeLightbox();
    assert.equal(fades[0].cancelled, true);
    assert.equal(JSON.stringify(fades[1].frames), '[{"opacity":0.6},{"opacity":0}]'); // Continue from the dragged shade.
    assert.equal(slot.style.transform, 'translate3d(0, 0, 0)');
    assert.deepEqual([...viewerValues], [['--lightbox-video-lift', `${app.lightboxVideoLift()}px`]]); // Resting lift is set before the first motion measurement.
    assert.equal(fades[1].options.duration, 120);
    assert.match(motions[1].frames[0].transform, /translate3d\(0px, 180px, 0\)/); // Continue from the finger, with no doubled/stale drag transform.
    motions[1].complete(); await Promise.resolve(); await Promise.resolve();
    assert.equal(app.lightboxOpen, false);
    assert.equal(viewer.style.display, 'none');
    assert.deepEqual([...viewerValues], [['--lightbox-video-lift', `${app.lightboxVideoLift()}px`]]);
    assert.equal(context.window.scrollY, 240);
    assert.equal(fades[1].cancelled, true);
    app.followLightboxShade(false);
    assert.ok(!context.__state.events.has('scroll')); // Closing stops following the page.
    assert.ok(!context.__state.events.has('resize'));
    assert.equal(player.parentElement, originalParent);
    assert.equal(feed.querySelector('video'), video);
    assert.equal(video.parentElement, player);
    assert.equal(stage.querySelectorAll('video').length, 0);
    assert.equal(video.currentTime, 7);
    assert.equal(video.paused, false);
    assert.equal(video.muted, true);
    assert.ok(!player.classList.contains('is-video-fill'));
    dispatchFeed(player, 'click', video); // Safari's compatibility click following dismissal.
    assert.equal(opened.length, 0);
    now += 1000;
    dispatchFeed(player, 'click', video);
    assert.equal(opened.length, 1); // A subsequent intentional tap still opens the viewer.
    assert.equal(nativeFullscreen, 0);
});

test('opening a mobile video includes mixed gallery attachments and the tapped attachment index', () => {
    const context=browserContext(),opened=[];context.window.dispatchEvent=e=>opened.push(e);
    const {tweetApp,tweetNookOpenMobileVideo}=loadScripts(context,appScripts(),'({tweetApp,tweetNookOpenMobileVideo})');
    const root=parseFakeHTML(tweetApp().renderMediaGrid([
        {type:'photo',download:{local_path:'media/photo.jpg'}},
        {type:'video',width:720,height:1280,download:{local_path:'media/first.mp4'}},
        {type:'photo'},
        {type:'video',width:1280,height:720,download:{local_path:'media/second.mp4'}},
    ]))[0];
    const videos=root.querySelectorAll('video');
    tweetNookOpenMobileVideo(videos[1]);
    assert.equal(opened[0].detail.media.length,3);assert.equal(opened[0].detail.index,2);
    assert.equal(opened[0].detail.originVideo,videos[1]);
    assert.equal(opened[0].detail.media[2].m.width,1280);
});

test('mobile video gallery swipes keep live decoders, one audio owner and each clips progress on return', () => {
    const context=browserContext();context.window.matchMedia=()=>({matches:true});
    context.getComputedStyle=()=>({objectFit:'contain',borderRadius:'16px'});
    const {tweetApp,tweetNookInitVideoPlayer}=loadScripts(context,appScripts(),'({tweetApp,tweetNookInitVideoPlayer})');
    const app=immediateComponent(tweetApp());app.videoPlayback='paused';
    const media=[0,1,2].map(i=>({type:'video',width:720,height:1280,download:{local_path:`media/${i}.mp4`}}));
    const root=parseFakeHTML(app.renderMediaGrid(media))[0];root.connected=true;
    const videos=root.querySelectorAll('video'),parents=videos.map(v=>v.parentElement),playLog=[];
    const styled=tag=>new FakeElement(tag).cloneNode(false);
    context.document.createElement=styled;
    const stage=styled('div');stage.setAttribute('class','lightbox-stage');stage.connected=true;
    const viewer=styled('div');viewer.setAttribute('class','lightbox-viewer');viewer.appendChild(stage);
    app.$refs.lightboxViewer=viewer;
    const initial=styled('div');initial.setAttribute('class','lightbox-slide is-active');initial.setAttribute('data-inline-video-slot','');stage.appendChild(initial);
    for(const i of [1,2]){const slide=styled('div'),slot=styled('div');slide.setAttribute('class','lightbox-slide');slot.setAttribute('data-inline-video-index',String(i));slide.appendChild(slot);stage.appendChild(slide)}
    app.$refs.lightboxStage=stage;app.$refs.inlineVideoSlot=initial;
    videos.forEach((v,i)=>{v.parentElement.style=styled('div').style;v.parentElement.rect={left:0,top:120,width:200,height:356};
        for(const n of [v,...v.parentElement.querySelectorAll('input')])n.style=styled('div').style;
        Object.assign(v,{videoWidth:720,videoHeight:1280,volume:1,muted:true,currentTime:i*7,readyState:4,paused:i!==0,ended:false,
            play(){playLog.push({i,otherPaused:videos.every(other=>other===this||other.paused)});this.paused=false;return Promise.resolve()},pause(){this.paused=true}});
        tweetNookInitVideoPlayer(v);
    });
    const payload=JSON.parse(root.dataset.lightboxMedia);
    app.openLightbox(payload,0,videos[0]);
    assert.equal(stage.querySelectorAll('video').length,3);assert.equal(root.querySelectorAll('video').length,0);
    assert.equal(app.activeLightboxVideo(),videos[0]);assert.ok(videos[0].parentElement.parentElement.classList.contains('is-video-fill'));
    app.commitLightboxNavigation(1);assert.equal(app.activeLightboxVideo(),videos[1]);
    assert.equal(videos[0].paused,true);assert.equal(videos[1].paused,false);assert.equal(playLog.at(-1).otherPaused,true);
    videos[1].currentTime=9;app.commitLightboxNavigation(-1);videos[0].currentTime=4;app.commitLightboxNavigation(1);
    app.finishCloseLightbox();
    assert.equal(videos[0].parentElement,parents[0]);assert.equal(videos[1].parentElement,parents[1]);
    assert.equal(videos[0].currentTime,4);assert.equal(videos[1].currentTime,9);
    assert.equal(stage.querySelectorAll('video').length,0);assert.equal(videos[0].paused,true);assert.equal(videos[1].paused,false);
    // The persistent initial slot still has the previous session's reactive
    // transform when a later attachment opens, before Alpine's next tick.
    initial.style.transform='translate3d(-300%, 0, 0)';
    initial.style.transition='transform 180ms ease';
    videos[2].play=function(){
        assert.equal(initial.style.transform,'translate3d(0, 0, 0)','position the selected picture before audible play or motion measurement');
        assert.equal(initial.style.transition,'none');
        this.paused=false;return Promise.resolve();
    };
    app.openLightbox(payload,2,videos[2]);
    assert.equal(app.activeLightboxVideo(),videos[2]);
    assert.equal(videos[2].paused,false);
    app.finishCloseLightbox();
});

test('closing a swiped video reveals its feed destination before measuring the return motion', () => {
    const context = browserContext();
    const app = immediateComponent(loadScripts(context, appScripts(), 'tweetApp')());
    const track = {scrollLeft: 0, getBoundingClientRect: () => ({left: 0, width: 400})};
    const tile = {getBoundingClientRect: () => ({left: 1200 - track.scrollLeft, width: 200})};
    const origin = {closest: selector => selector === '.media-carousel-track' ? track : tile};
    app.lightboxMotionOrigin = () => origin;
    app.isMobileViewer = () => true;
    app.lightboxOpen = true;
    app.activeLightboxVideo = () => null;
    app.activeLightboxMedia = () => {
        assert.equal(track.scrollLeft, 1100);
        return null;
    };
    app.fadeLightbox = () => false;
    app.finishCloseLightbox = () => {};
    app.closeLightbox();
    assert.equal(tile.getBoundingClientRect().left, 100);
});

test('pages never zoom on touch screens, except media in the fullscreen viewer', () => {
    const html = pageHtml();
    const css = pageCss();
    const js = readScripts(pageScripts());
    // No double-tap zoom anywhere; no pinch on the app's content.
    assert.match(css, /html \{ touch-action: manipulation; \}/);
    assert.match(css, /\.app-column, \.detail-layer-header, \.detail-underlay \{ touch-action: pan-x pan-y; \}/);
    // The viewer is outside the column, so its surface keeps native pinch zoom.
    assert.ok(html.indexOf('class="lightbox-viewer') > html.indexOf('<!-- /LEFT COLUMN -->'));
    assert.match(css, /\.lightbox-gesture-surface \{\s*touch-action: pinch-zoom;/);
    // Safari ignores user-scalable=no; its pinch gestures are cancelled outside the viewer.
    assert.match(js, /addEventListener\('gesturestart', blockZoom, \{ passive: false \}\)/);
    assert.match(js, /if \(!event\.target\?\.closest\?\.\('\.lightbox-viewer'\)\) event\.preventDefault\(\);/);
    assert.doesNotMatch(html, /user-scalable|maximum-scale/);
});

run();
