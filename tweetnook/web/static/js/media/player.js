/**
 * Feed media players: video controls, scrubbing and audio, GIF playback, gallery
 * carousels, and the phone viewer's media bounds. Feed markup calls these through the
 * window.tweetNook* handlers; tweetApp() calls them as globals.
 */

// Video controls fade this long after the last pointer/touch/key activity
// during playback, and almost immediately once the pointer leaves the video.
const VIDEO_UI_IDLE_MS = 3000;
const VIDEO_UI_LEAVE_MS = 300;
const videoUiHideTimers = new WeakMap();
const videoScrubStates = new WeakMap();
let videoTapBlockedUntil = 0;
const VIDEO_PLAYER_EVENTS = 'onloadedmetadata="window.tweetNookSyncVideoDuration(this)" ondurationchange="window.tweetNookSyncVideoDuration(this)" ontimeupdate="window.tweetNookSyncVideoDuration(this)" onended="window.tweetNookSyncVideoDuration(this); window.tweetNookSyncVideoUi(this)" onplay="window.tweetNookShowVideoUi(this)" onpause="window.tweetNookSyncVideoUi(this)"';

function formatMediaDuration(durationMillis) {
    const millis = Number(durationMillis);
    if (!Number.isFinite(millis) || millis <= 0) return '';

    const totalSeconds = Math.floor(millis / 1000);
    const seconds = String(totalSeconds % 60).padStart(2, '0');
    const totalMinutes = Math.floor(totalSeconds / 60);
    if (totalMinutes < 60) return `${totalMinutes}:${seconds}`;

    const minutes = String(totalMinutes % 60).padStart(2, '0');
    return `${Math.floor(totalMinutes / 60)}:${minutes}:${seconds}`;
}

// The post layer while it is on screen. Feed videos beneath it are covered.
let openDetailLayer = null;

// Phones and touch-first tablets get the full-screen media viewer; media-viewer.css
// uses the same query.
const PHONE_MEDIA_QUERY = '(max-width: 640px), (pointer: coarse)';

function isPhoneLayout() {
    return Boolean(window.matchMedia?.(PHONE_MEDIA_QUERY)?.matches);
}

function tweetNookVideoPlayer(video) {
    return video?.closest?.('.media-video-player') || video?.parentElement;
}

// Native posters can disappear as soon as play() starts, before the decoder has
// presented a picture. Keep a separately painted image until that first frame.
function tweetNookWatchVideoPoster(video) {
    if (videoPosterStates.has(video)) return;
    const state = { generation: 0, frame: null };
    videoPosterStates.set(video, state);
    const poster = () => tweetNookVideoPlayer(video)?.querySelector?.('[data-video-poster]');
    const reset = () => {
        state.generation++;
        if (state.frame !== null) video.cancelVideoFrameCallback?.(state.frame);
        state.frame = null;
        const image = poster();
        if (image) image.hidden = !image.getAttribute('src');
    };
    state.reset = reset;
    const ready = () => {
        if (!poster() || state.frame !== null) return;
        const generation = state.generation;
        const reveal = () => {
            state.frame = null;
            if (generation === state.generation && video.readyState >= 2) {
                const image = poster();
                if (image) image.hidden = true;
            }
        };
        if (video.requestVideoFrameCallback) state.frame = video.requestVideoFrameCallback(reveal);
        // Older engines lack presentation callbacks; playing plus two paints
        // gives the decoded picture time to replace the native poster.
        else if (!video.paused) window.requestAnimationFrame(() => window.requestAnimationFrame(reveal));
    };
    video.addEventListener('emptied', reset);
    video.addEventListener('loadstart', reset);
    video.addEventListener('loadeddata', ready);
    video.addEventListener('playing', ready);
    ready();
}

// Handle audio intent in the original gesture, before asynchronous volumechange
// events. Safari grants audible playback to an element, not the whole feed.
function tweetNookChangeVideoAudio(video, muted, volume = video.volume) {
    window.dispatchEvent(new CustomEvent('video-audio-intent', {detail: {video, muted, volume}}));
    // Players also work independently of the main application (native fallback).
    if (video.volume !== volume) video.volume = volume;
    if (video.muted !== muted) video.muted = muted;
}

function isMobileFullscreenVideo(video) {
    return Boolean(video?.closest?.('.lightbox-stage')) && isPhoneLayout();
}

function tweetNookSyncVideoDuration(video) {
    tweetNookSyncPlayerControls(video);
    const indicator = tweetNookVideoPlayer(video)?.querySelector?.('[data-video-duration]');
    const durationSeconds = Number(video?.duration);
    if (!indicator || !Number.isFinite(durationSeconds) || durationSeconds <= 0) return;

    const currentTime = Number(video?.currentTime);
    const elapsedSeconds = Number.isFinite(currentTime) ? Math.max(0, currentTime) : 0;
    const remainingSeconds = Math.max(0, durationSeconds - elapsedSeconds);
    const remaining = formatMediaDuration(remainingSeconds * 1000) || '0:00';
    indicator.textContent = remaining;
    indicator.hidden = false;
    if (remaining) {
        indicator.removeAttribute('aria-hidden');
        indicator.setAttribute('aria-label', `Remaining video duration ${remaining}`);
    }
}

// Keep the remaining-time badge separate from the full control bar.
function tweetNookSyncVideoUi(video) {
    const player = tweetNookVideoPlayer(video);
    if (!player?.classList) return;

    const nativeUiVisible = Boolean(video.paused || video.ended) || videoUiHideTimers.has(player)
        || (document.activeElement?.matches?.(':focus-visible') && player.contains?.(document.activeElement));
    player.classList.toggle('is-native-ui-hidden', !nativeUiVisible);
    const viewer = video.closest?.('.lightbox-viewer');
    if (viewer && isMobileFullscreenVideo(video)) {
        viewer.classList.toggle('is-video-controls-hidden', !nativeUiVisible);
    }
    tweetNookSyncPlayerControls(video);
}

function tweetNookShowVideoUi(video, holdMs = VIDEO_UI_IDLE_MS) {
    const player = tweetNookVideoPlayer(video);
    if (!player?.classList) return;

    clearTimeout(videoUiHideTimers.get(player));
    videoUiHideTimers.set(player, setTimeout(() => {
        videoUiHideTimers.delete(player);
        tweetNookSyncVideoUi(video);
    }, holdMs));
    tweetNookSyncVideoUi(video);
}

function tweetNookLeaveVideoUi(video) {
    tweetNookShowVideoUi(video, VIDEO_UI_LEAVE_MS);
}

function tweetNookFinishVideoScrub(video, cancelled = false) {
    const state = videoScrubStates.get(video);
    if (!state) return;
    videoScrubStates.delete(video);
    state.cleanup();
    tweetNookVideoPlayer(video).classList.remove('is-scrubbing');
    video.currentTime = cancelled ? state.time : state.targetTime;
    if (state.resume && video.isConnected) video.play()?.catch?.(() => {});
    tweetNookSyncVideoDuration(video);
    tweetNookShowVideoUi(video);
}

function tweetNookQueueScrubSeek(video, state) {
    if (state.frame !== null || video.seeking) return;
    const update = () => {
        state.frame = null;
        if (videoScrubStates.get(video) === state && !video.seeking && video.currentTime !== state.targetTime) video.currentTime = state.targetTime;
    };
    state.frame = window.requestAnimationFrame ? window.requestAnimationFrame(update) : setTimeout(update, 16);
}

function tweetNookStartVideoScrub(video, event = null) {
    if (videoScrubStates.has(video) || !Number.isFinite(video.duration) || video.duration <= 0) return;
    const seek = tweetNookVideoPlayer(video).querySelector('[data-video-seek]');
    const pointerControlled = Number.isFinite(event?.clientX);
    const state = {
        resume: !video.paused && !video.ended,
        time: video.currentTime,
        targetTime: video.currentTime,
        pointerControlled,
        pointerId: event?.pointerId,
        frame: null,
        cleanup() {
            if (state.frame !== null) {
                if (window.cancelAnimationFrame) window.cancelAnimationFrame(state.frame);
                else clearTimeout(state.frame);
            }
            document.removeEventListener('pointermove', move, true);
            document.removeEventListener('pointerup', finish, true);
            document.removeEventListener('pointercancel', cancel, true);
            window.removeEventListener('blur', cancel);
            video.removeEventListener('seeked', ready);
            if (seek.hasPointerCapture?.(state.pointerId)) seek.releasePointerCapture(state.pointerId);
        },
    };
    const samePointer = e => state.pointerId === undefined || e?.pointerId === state.pointerId;
    const move = e => {
        if (!pointerControlled || !samePointer(e) || !Number.isFinite(e?.clientX)) return;
        e.preventDefault?.();
        const rect = seek.getBoundingClientRect();
        if (!rect.width) return;
        state.targetTime = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)) * video.duration;
        tweetNookSyncPlayerControls(video);
        tweetNookQueueScrubSeek(video, state);
    };
    const finish = e => {
        if (!samePointer(e)) return;
        move(e);
        tweetNookFinishVideoScrub(video);
    };
    const cancel = e => { if (e?.type === 'blur' || samePointer(e)) tweetNookFinishVideoScrub(video, true); };
    const ready = () => tweetNookQueueScrubSeek(video, state);
    videoScrubStates.set(video, state);
    document.addEventListener('pointermove', move, {capture: true, passive: false});
    document.addEventListener('pointerup', finish, true);
    document.addEventListener('pointercancel', cancel, true);
    window.addEventListener('blur', cancel);
    video.addEventListener('seeked', ready);
    tweetNookVideoPlayer(video).classList.add('is-scrubbing');
    video.pause();
    if (pointerControlled) {
        event.preventDefault?.();
        seek.focus?.({preventScroll: true});
        seek.setPointerCapture?.(state.pointerId);
        move(event);
    }
    tweetNookSyncVideoUi(video);
}

function tweetNookSyncGifIndicator(video) {
    const indicator = video?.parentElement?.querySelector?.('[data-gif-toggle]');
    if (!indicator) return;

    const isPaused = Boolean(video.paused || video.ended);
    indicator.classList.toggle('is-paused', isPaused);
    const action = isPaused ? 'Play GIF' : 'Pause GIF';
    indicator.setAttribute('aria-label', action);
    indicator.setAttribute('title', action);
}

function tweetNookToggleGif(event, indicator) {
    event?.preventDefault?.();
    event?.stopPropagation?.();

    const video = indicator?.parentElement?.querySelector?.('video[data-animated-gif]');
    if (!video) return;
    if (video.paused || video.ended) {
        const playResult = video.play();
        if (playResult?.catch) playResult.catch(() => tweetNookSyncGifIndicator(video));
    } else {
        video.pause();
    }
}

// GIFs play without controls; every other video is a player.
function isVideoPlayer(video) {
    return video?.tagName === 'VIDEO' && (video.hasAttribute('controls') || video.hasAttribute('data-video-custom') || video.hasAttribute('data-video-player'));
}

function isFeedVideoPlayer(video) {
    return isVideoPlayer(video) && !video.closest?.('.lightbox-stage');
}

window.tweetNookSyncGifIndicator = tweetNookSyncGifIndicator;
window.tweetNookToggleGif = tweetNookToggleGif;
window.tweetNookSyncVideoDuration = tweetNookSyncVideoDuration;
window.tweetNookSyncVideoUi = tweetNookSyncVideoUi;
window.tweetNookShowVideoUi = tweetNookShowVideoUi;
window.tweetNookLeaveVideoUi = tweetNookLeaveVideoUi;

const galleryObservers = new WeakMap();
// First public carousel launch day (iOS); other clients rolled out later.
// The date-based appearance policy uses this UTC day, not an account's rollout.
const MEDIA_CAROUSEL_INTRODUCED_AT = Date.parse('2026-07-10T00:00:00Z');
function tweetNookGalleryScroll(track) {
    const nav = track.parentElement;
    const end = Math.max(0, track.scrollWidth - track.clientWidth);
    nav.querySelector('.media-carousel-previous').disabled = track.scrollLeft <= 1;
    nav.querySelector('.media-carousel-next').disabled = track.scrollLeft >= end - 1;
    // Page indicator: each dash widens and brightens as the strip passes it, linearly in scroll.
    const dots = nav.parentElement?.querySelector(':scope > .media-carousel-dots')?.children || [];
    const page = end > 0 ? Math.min(1, Math.max(0, track.scrollLeft / end)) * (dots.length - 1) : 0;
    for (let idx = 0; idx < dots.length; idx++) {
        dots[idx].style.setProperty('--dot-active', Math.max(0, 1 - Math.abs(page - idx)).toFixed(3));
    }
    tweetNookGalleryAvatar(track);
}
// The X app shrinks a post's avatar while its strip is scrolled, so photos can
// pass under the avatar column, and restores it at the first photo. Measured on
// four posts: the avatar becomes (strip top - avatar top - 6pt) tall, centered
// 18pt below its resting top; strips 54pt or more below keep the full avatar.
function tweetNookGalleryAvatar(track) {
    if (!isPhoneLayout() || track.closest('.quote-card')) return;
    const row = track.closest('.feed-card, .thread-row');
    const avatar = row?.querySelector(':scope > .flex > .avatar, :scope > .avatar');
    if (!avatar) return;
    const scrolled = track.scrollLeft > 1;
    if (scrolled === avatar.classList.contains('avatar-yield')) return;
    if (scrolled && !avatar.style.getPropertyValue('--avatar-yield-scale')) {
        // Measured once, while the avatar is still untransformed.
        const size = avatar.offsetHeight;
        const scale = Math.min(1, (track.firstElementChild.getBoundingClientRect().top
            - avatar.getBoundingClientRect().top - 6) / size);
        avatar.style.setProperty('--avatar-yield-scale', scale.toFixed(3));
        avatar.style.setProperty('--avatar-yield-shift', `${(size * (0.375 - scale / 2)).toFixed(2)}px`);
    }
    if (scrolled && Number(avatar.style.getPropertyValue('--avatar-yield-scale')) >= 1) return;
    avatar.classList.toggle('avatar-yield', scrolled);
}
// The carousel scrollport bleeds past its wrappers (under the avatar column, into
// a quote's padding); only its tiles and arrows are media. pointer-events on the
// padding would stop iOS scrolling, so click handlers ask this instead.
function isCarouselGap(target) {
    return Boolean(target?.closest?.('.media-carousel'))
        && !target.closest('.media-carousel-item, .media-carousel-arrow');
}
function tweetNookGalleryTracks(root) {
    return [...(root.matches?.('.media-carousel-track') ? [root] : []),
        ...root.querySelectorAll('.media-carousel-track')];
}
function tweetNookInitGalleries(root) {
    for (const track of tweetNookGalleryTracks(root)) {
        if (galleryObservers.has(track)) continue;
        const observer = new ResizeObserver(() => tweetNookGalleryScroll(track));
        galleryObservers.set(track, observer);
        observer.observe(track);
        tweetNookGalleryScroll(track);
    }
}
function tweetNookDisposeGalleries(root) {
    for (const track of tweetNookGalleryTracks(root)) {
        galleryObservers.get(track)?.disconnect();
        galleryObservers.delete(track);
    }
}
function tweetNookScrollGallery(source, direction) {
    const track = source.classList.contains('media-carousel-track') ? source
        : source.closest('.media-carousel-nav').querySelector('.media-carousel-track');
    // Native arrows advance one tile, including when several fit in the viewport.
    const items = [...track.children];
    const positions = items.map(item => item.offsetLeft - items[0].offsetLeft);
    let target = direction > 0
        ? positions.find(position => position > track.scrollLeft + 1)
        : positions.filter(position => position < track.scrollLeft - 1).at(-1);
    target ??= direction > 0 ? track.scrollWidth - track.clientWidth : 0;
    // Match X's immediate arrow steps; browser gestures retain native momentum.
    track.scrollTo({left: target, behavior: 'auto'});
}
window.tweetNookGalleryScroll = tweetNookGalleryScroll;
window.tweetNookScrollGallery = tweetNookScrollGallery;
window.tweetNookGalleryKey = (event, track) => {
    if (event.target !== track) return; // Keep video/slider keyboard controls.
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    event.stopPropagation();
    if (event.key === 'Home' || event.key === 'End') {
        track.scrollTo({left: event.key === 'Home' ? 0 : track.scrollWidth, behavior: 'instant'});
    } else tweetNookScrollGallery(track, event.key === 'ArrowLeft' ? -1 : 1);
};

const VIDEO_PLAYER_SELECTOR = 'video[controls], video[data-video-custom], video[data-video-player]';
// SVG geometry read from X's live player in Helium (2026-10-12).
const VIDEO_ICONS = {
    play: '<g><path d="M7.00 4.30L13.30 8.45L13.30 15.55L7.00 19.70Z" fill="currentcolor" stroke="currentcolor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2"/><path d="M13.30 8.45L18.70 12.00L18.70 12.00L13.30 15.55Z" fill="currentcolor" stroke="currentcolor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2"/></g>',
    pause: '<g><path d="M6.00 6.00L9.00 6.00L9.00 18.00L6.00 18.00Z" fill="currentcolor" stroke="currentcolor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2"/><path d="M15.00 6.00L18.00 6.00L18.00 18.00L15.00 18.00Z" fill="currentcolor" stroke="currentcolor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2"/></g>',
    sound: '<g><path clip-rule="evenodd" d="M14 22h-2.35l-.275-.219L6.648 18H4.5C2.567 18 1 16.433 1 14.5v-5C1 7.567 2.567 6 4.5 6h2.148l4.727-3.781.274-.219H14v20zM7.625 7.781L7.351 8H4.5C3.672 8 3 8.672 3 9.5v5c0 .828.672 1.5 1.5 1.5h2.85l.275.219 4.375 3.5V4.28l-4.375 3.5z" fill-rule="evenodd"/><path d="M20.817 5.098C22.192 7.05 23 9.432 23 12c0 2.568-.808 4.95-2.183 6.902l-1.634-1.152C20.328 16.124 21 14.142 21 12s-.672-4.124-1.817-5.75l1.634-1.152zm-3.252 2.33C18.47 8.724 19 10.302 19 12s-.53 3.276-1.435 4.572l-1.64-1.144C16.602 14.456 17 13.276 17 12c0-1.276-.398-2.456-1.075-3.428l1.64-1.144z"/></g>',
    muted: '<g><path d="M16 22h-2.35l-.275-.219-3.842-3.073 1.424-1.424L14 19.72v-5.477l2-2V22z"/><path clip-rule="evenodd" d="M16 6.586l4.293-4.293 1.414 1.414-18 18-1.414-1.414 2.657-2.658C3.795 17.063 3 15.875 3 14.5v-5C3 7.567 4.567 6 6.5 6h2.148l4.727-3.781.274-.219H16v4.586zM9.625 7.78L9.351 8H6.5C5.672 8 5 8.672 5 9.5v5c0 .828.672 1.5 1.5 1.5h.086L14 8.586V4.28l-4.375 3.5z" fill-rule="evenodd"/></g>',
    mobileMuted: '<g fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 9h4l5-4v14l-5-4H3z"/><path d="m17 10 4 4m0-4-4 4"/></g>',
    mobileSound: '<g fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 9h4l5-4v14l-5-4H3z"/><path d="M16 8a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14"/></g>',
    fullscreen: '<g><path d="M13 3h8v8h-2V6.41l-5.043 5.05-1.414-1.42L17.586 5H13V3zm-1.543 10.96L6.414 19H11v2H3v-8h2v4.59l5.043-5.05 1.414 1.42z"/></g>',
    exitFullscreen: '<g><path d="M21.457 3.96L16.414 9H21v2h-8V3h2v4.59l5.043-5.05 1.414 1.42zM3 13h8v8H9v-4.59l-5.043 5.05-1.414-1.42L7.586 15H3v-2z"/></g>',
    poster: '<g><circle cx="30" cy="30.4219" fill="#333333" opacity="0.6" r="30"/><path d="M22.2275 17.1971V43.6465L43.0304 30.4218L22.2275 17.1971Z" fill="white"/></g>',
};
function videoIcon(name) {
    return `<svg viewBox="${name === 'poster' ? '0 0 60 61' : '0 0 24 24'}" aria-hidden="true">${VIDEO_ICONS[name]}</svg>`;
}

function renderVideoControls() {
    const button = (action, label, icon, className = '') => `<button type="button" class="video-button ${className}" data-video-action="${action}" aria-label="${label}" title="${label}">${videoIcon(icon)}</button>`;
    return `<div data-video-controls hidden>
        ${button('play', 'Play video', 'poster', 'video-center-play')}
        ${button('mute', 'Mute', 'mobileSound', 'video-idle-mute')}
        <div class="video-control-bar">
            <span class="video-scrub-time" data-video-scrub-time aria-hidden="true">0:00 / 0:00</span>
            <div class="video-seek-wrap">
                <input class="video-seek" data-video-seek type="range" min="0" max="100" step="0.1" value="0" aria-label="Seek video" disabled>
                <span class="video-scrub-track" aria-hidden="true"><span></span></span>
            </div>
            <div class="video-control-row">
                ${button('play', 'Play video', 'play')}
                <span class="video-time" data-video-time>0:00 / 0:00</span>
                <span class="video-time video-mobile-time" data-video-remaining-time>-0:00</span>
                <div class="video-volume-group">${button('mute', 'Mute', 'sound')}
                    <input class="video-volume" data-video-volume type="range" min="0" max="1" step="0.05" value="1" aria-label="Volume">
                </div>
                ${button('fullscreen', 'Full screen', 'fullscreen')}
            </div>
        </div>
    </div>`;
}

function tweetNookSyncPlayerControls(video) {
    if (!video?.hasAttribute?.('data-video-player')) return;
    const player = tweetNookVideoPlayer(video);
    const paused = video.paused || video.ended;
    const muted = video.muted || video.volume === 0;
    player.classList.toggle('is-paused', paused);
    if (!paused || video.currentTime > 0) player.classList.add('has-started');
    player.querySelectorAll('[data-video-action="play"]').forEach(button => {
        const label = paused ? 'Play video' : 'Pause video';
        if (button.getAttribute('aria-label') !== label) {
            button.setAttribute('aria-label', label);
            button.title = label;
            if (!button.classList.contains('video-center-play')) button.innerHTML = videoIcon(paused ? 'play' : 'pause');
        }
    });
    player.querySelectorAll('[data-video-action="mute"]').forEach(button => {
        const label = muted ? 'Unmute' : 'Mute';
        const mobile = button.classList.contains('video-idle-mute')
            || isMobileFullscreenVideo(video);
        const icon = mobile ? (muted ? 'mobileMuted' : 'mobileSound') : (muted ? 'muted' : 'sound');
        if (button.getAttribute('data-video-icon') !== icon) {
            button.setAttribute('aria-label', label);
            button.title = label;
            button.setAttribute('data-video-icon', icon);
            button.innerHTML = videoIcon(icon);
        }
    });
    const duration = Number.isFinite(video.duration) ? Math.max(0, video.duration) : 0;
    const time = Math.max(0, videoScrubStates.get(video)?.targetTime ?? video.currentTime ?? 0);
    const seek = player.querySelector('[data-video-seek]');
    seek.disabled = !duration;
    seek.value = duration ? time / duration * 100 : 0;
    player.style.setProperty('--video-progress', `${duration ? Math.min(100, time / duration * 100) : 0}%`);
    seek.style.setProperty('--video-progress', `${duration ? Math.min(100, time / duration * 100) : 0}%`);
    seek.setAttribute('aria-valuetext', `${formatMediaDuration(time * 1000) || '0:00'} of ${formatMediaDuration(duration * 1000) || '0:00'}`);
    player.querySelector('[data-video-time]').textContent = `${formatMediaDuration(Math.round(time) * 1000) || '0:00'} / ${formatMediaDuration(Math.round(duration) * 1000) || '0:00'}`;
    player.querySelector('[data-video-scrub-time]').textContent = `${formatMediaDuration(time * 1000) || '0:00'} / ${formatMediaDuration(duration * 1000) || '0:00'}`;
    player.querySelector('[data-video-remaining-time]').textContent = `-${formatMediaDuration(Math.max(0, Math.round(duration - time)) * 1000) || '0:00'}`;
    const volume = player.querySelector('[data-video-volume]');
    if (document.activeElement !== volume) volume.value = muted ? 0 : video.volume;
    const fullscreen = player.querySelector('[data-video-action="fullscreen"]');
    const filled = player.classList.contains('is-video-fill');
    const mobileViewer = isMobileFullscreenVideo(video);
    const fullscreenLabel = document.fullscreenElement === player ? 'Exit full screen' : mobileViewer ? (filled ? 'Fit video' : 'Fill screen') : 'Full screen';
    if (fullscreen.getAttribute('aria-label') !== fullscreenLabel) {
        fullscreen.setAttribute('aria-label', fullscreenLabel);
        fullscreen.title = fullscreenLabel;
        fullscreen.innerHTML = videoIcon(document.fullscreenElement === player || filled ? 'exitFullscreen' : 'fullscreen');
    }
    if (video.videoWidth && video.videoHeight) player.style.setProperty('--video-aspect', video.videoWidth / video.videoHeight);
}

function isMobileInlineVideo(video) {
    return isPhoneLayout()
        && !video.closest?.('.lightbox-stage') && document.fullscreenElement !== tweetNookVideoPlayer(video);
}

function tweetNookOpenMobileVideo(video) {
    if (Date.now() < videoTapBlockedUntil) return;
    const holder = video.closest?.('[data-lightbox-media]');
    let media;
    try { media = JSON.parse(holder?.dataset.lightboxMedia || 'null'); } catch { /* Fall back to this clip. */ }
    if (!Array.isArray(media) || !media.length) media = [{type: 'video', src: video.currentSrc || video.src, isGif: false,
        m: {aspect_ratio: [video.videoWidth || Number(video.dataset?.videoAspect) || 16 / 9, video.videoHeight || 1]}}];
    const index = Math.max(0, Math.min(media.length - 1, Number(video.dataset?.mediaIndex) || 0));
    window.dispatchEvent(new CustomEvent('open-lightbox', {
        detail: {media, index, originVideo: video, origin: video},
    }));
}

function tweetNookPlayerAction(video, action) {
    if (action === 'play') {
        if (video.paused || video.ended) video.play()?.catch?.(() => tweetNookSyncVideoUi(video));
        else video.pause();
    } else if (action === 'mute') {
        tweetNookChangeVideoAudio(video, video.volume === 0 ? false : !video.muted, video.volume || 1);
    } else if (action === 'fullscreen') {
        if (document.fullscreenElement) document.exitFullscreen()?.catch?.(() => {});
        else if (isMobileInlineVideo(video)) tweetNookOpenMobileVideo(video);
        else if (isMobileFullscreenVideo(video)) {
            tweetNookVideoPlayer(video).classList.toggle('is-video-fill');
        }
        else if (tweetNookVideoPlayer(video).requestFullscreen) {
            window.dispatchEvent(new CustomEvent('video-expanded-audio', {detail: {video, expanded: true}}));
            const restore = () => {
                if (!video.closest('.lightbox-stage')) window.dispatchEvent(new CustomEvent('video-expanded-audio', {detail: {video, expanded: false}}));
            };
            try { tweetNookVideoPlayer(video).requestFullscreen()?.catch?.(restore); }
            catch { restore(); }
        }
        else if (!video.closest?.('.lightbox-stage')) tweetNookOpenMobileVideo(video);
    }
    tweetNookShowVideoUi(video);
}

// Inline playback must be configured before any observer can start the video.
function tweetNookInitVideoPlayer(video) {
    video.playsInline = true;
    video.setAttribute('playsinline', '');
    video.setAttribute('webkit-playsinline', '');
    if (video.hasAttribute('data-video-custom')) { video.controls = false; video.removeAttribute('controls'); }
    const player = tweetNookVideoPlayer(video);
    const controls = player?.querySelector?.('[data-video-controls]');
    if (!controls || video.hasAttribute('data-video-player')) return;
    video.setAttribute('data-video-player', '');
    video.removeAttribute('controls');
    controls.hidden = false;
    player.classList.add('has-custom-controls');
    player.addEventListener('click', event => {
        const video = player.querySelector('video');
        event.stopPropagation();
        // Right-click and macOS Control-click belong to the browser's menu.
        if (event.button > 0 || event.ctrlKey) return;
        event.preventDefault();
        if (isMobileInlineVideo(video) && Date.now() < videoTapBlockedUntil) return;
        const button = event.target.closest('[data-video-action]');
        // In the phone viewer, a tap anywhere off the controls shows or hides them, as in X.
        const surface = event.target === video || event.target === player
            || (isMobileFullscreenVideo(video) && !event.target.closest('.video-control-bar, [data-video-action], input'));
        if (isMobileInlineVideo(video) && (surface || button?.dataset.videoAction === 'play')) tweetNookOpenMobileVideo(video);
        else if (button) tweetNookPlayerAction(video, button.dataset.videoAction);
        else if (surface) {
            if (isMobileFullscreenVideo(video) && !video.paused) {
                if (player.classList.contains('is-native-ui-hidden')) tweetNookShowVideoUi(video);
                else { clearTimeout(videoUiHideTimers.get(player)); videoUiHideTimers.delete(player); tweetNookSyncVideoUi(video); }
            } else tweetNookPlayerAction(video, 'play');
        }
    });
    player.addEventListener('input', event => {
        const video = player.querySelector('video');
        if (event.target.hasAttribute('data-video-seek') && Number.isFinite(video.duration)) {
            const state = videoScrubStates.get(video);
            if (state?.pointerControlled) return;
            video.currentTime = Math.max(0, Math.min(video.duration, Number(event.target.value) / 100 * video.duration));
            if (state) state.targetTime = video.currentTime;
        } else if (event.target.hasAttribute('data-video-volume')) {
            const volume = Math.max(0, Math.min(1, Number(event.target.value)));
            tweetNookChangeVideoAudio(video, volume === 0, volume);
        }
        tweetNookShowVideoUi(video);
        tweetNookSyncVideoDuration(video);
    });
    player.addEventListener('pointerdown', event => {
        const video = player.querySelector('video');
        if (event.target.hasAttribute('data-video-seek') && !(event.button > 0)) tweetNookStartVideoScrub(video, event);
    });
    player.addEventListener('keydown', event => {
        const video = player.querySelector('video');
        // Leave browser and system shortcuts (Cmd/Ctrl+F, Ctrl+K, ...) alone.
        if (event.metaKey || event.ctrlKey || event.altKey) return;
        // The seek slider's native step is 0.1% of the video; arrows seek by seconds instead.
        const onSeek = event.target.hasAttribute?.('data-video-seek');
        const seekBy = {ArrowLeft: -5, ArrowRight: 5, ...(onSeek ? {ArrowDown: -5, ArrowUp: 5} : {})}[event.key];
        if (event.target.closest('input, button') && !(onSeek && seekBy)) {
            if (event.key !== 'Escape') event.stopPropagation();
            return;
        }
        const action = onSeek ? null : { ' ': 'play', k: 'play', m: 'mute', f: 'fullscreen' }[event.key.toLowerCase()];
        if (action) { event.preventDefault(); event.stopPropagation(); tweetNookPlayerAction(video, action); }
        else if (seekBy && Number.isFinite(video.duration)) {
            event.preventDefault(); event.stopPropagation();
            video.currentTime = Math.max(0, Math.min(video.duration, video.currentTime + seekBy));
            tweetNookShowVideoUi(video);
            tweetNookSyncVideoDuration(video);
        }
    });
    player.tabIndex = 0;
    player.setAttribute('role', 'group');
    player.setAttribute('aria-label', 'Video player');
    for (const name of ['pointermove', 'pointerenter']) player.addEventListener(name, event => {
        const video = player.querySelector('video');
        if (event.pointerType !== 'touch' && !isMobileFullscreenVideo(video)) tweetNookShowVideoUi(video);
    });
    player.addEventListener('focusin', event => {
        const video = player.querySelector('video');
        if (event.target.matches(':focus-visible')) tweetNookShowVideoUi(video);
    });
    player.addEventListener('pointerleave', event => {
        const video = player.querySelector('video');
        if (event.pointerType !== 'touch' && !isMobileFullscreenVideo(video)) tweetNookLeaveVideoUi(video);
    });
    player.addEventListener('focusout', () => {
        const video = player.querySelector('video');
        tweetNookShowVideoUi(video, isMobileFullscreenVideo(video) ? VIDEO_UI_IDLE_MS : VIDEO_UI_LEAVE_MS);
    });
    for (const name of ['loadedmetadata', 'durationchange', 'timeupdate', 'volumechange']) video.addEventListener(name, () => tweetNookSyncVideoDuration(video));
    video.addEventListener('play', () => tweetNookShowVideoUi(video));
    for (const name of ['pause', 'ended']) video.addEventListener(name, () => tweetNookSyncVideoUi(video));
    player.addEventListener('fullscreenchange', () => {
        const video = player.querySelector('video');
        // Closing native fullscreen inside the viewer still leaves it expanded.
        const expanded = document.fullscreenElement === player || Boolean(video.closest('.lightbox-stage'));
        window.dispatchEvent(new CustomEvent('video-expanded-audio', {detail: {video, expanded}}));
        tweetNookShowVideoUi(video);
    });
    tweetNookSyncVideoDuration(video);
    tweetNookSyncVideoUi(video);
}

// Keep a picture's surface (a video decoder, an image) at one size and move/crop it on the compositor.
function fittedMediaRect(media, includeCrop = false) {
    const frame = media.getBoundingClientRect();
    const cover = getComputedStyle(media).objectFit === 'cover';
    if (cover && !includeCrop) return frame;
    const ratio = media.videoWidth / media.videoHeight || media.naturalWidth / media.naturalHeight
        || Number(media.dataset?.videoAspect) || frame.width / frame.height;
    const width = cover ? Math.max(frame.width, frame.height * ratio) : Math.min(frame.width, frame.height * ratio);
    const height = width / ratio;
    return { left: frame.left + (frame.width - width) / 2, top: frame.top + (frame.height - height) / 2, width, height };
}

// A screen-fixed viewer stops at the visible viewport; one placed in the page
// also paints beneath Safari's toolbars, as the feed picture does.
function videoFeedInsets(source, raised = [], clipToViewport = true) {
    const viewport = window.visualViewport;
    const left = viewport?.offsetLeft || 0;
    let top = viewport?.offsetTop || 0;
    const right = left + (viewport?.width || window.innerWidth || Infinity);
    const bottom = top + (viewport?.height || window.innerHeight || Infinity);
    let occluded = false;
    document.querySelectorAll('[data-video-occluder]').forEach(header => {
        if (raised.includes(header)) return;
        const rect = header.getBoundingClientRect();
        if (rect.width && rect.height && rect.left < source.left + source.width && rect.left + rect.width > source.left
            && rect.top <= Math.max(top, source.top) && rect.top + rect.height > top) {
            top = Math.max(top, rect.top + rect.height);
            occluded = true;
        }
    });
    const edge = (clipped, value) => clipped ? value : 0;
    return {top: edge(clipToViewport || occluded, Math.min(source.height, Math.max(0, top - source.top))),
        bottom: edge(clipToViewport, Math.min(source.height, Math.max(0, source.top + source.height - bottom))),
        left: edge(clipToViewport, Math.min(source.width, Math.max(0, left - source.left))),
        right: edge(clipToViewport, Math.min(source.width, Math.max(0, source.left + source.width - right)))};
}

function feedVideoVisibility(video) {
    const rect = video.getBoundingClientRect();
    // Non-layout hosts (including the asset harness) can supply only position.
    if (!Number.isFinite(rect.height) || !Number.isFinite(rect.width)) return { ratio: 1, top: rect.top };
    if (!rect.height || !rect.width) return { ratio: 0, top: rect.top };
    // A post layer covers the feed and anything else beneath it.
    if (openDetailLayer && !openDetailLayer.contains(video)) return { ratio: 0, top: rect.top };
    const insets = videoFeedInsets(rect);
    let top = rect.top + insets.top, bottom = rect.top + rect.height - insets.bottom;
    let left = rect.left + insets.left, right = rect.left + rect.width - insets.right;
    // A split conversation can scroll independently of the window.
    for (let node = video.parentElement; node && node !== document.body; node = node.parentElement) {
        const style = getComputedStyle(node);
        if (!/(auto|scroll|hidden|clip)/.test(`${style.overflowX} ${style.overflowY}`)) continue;
        const clip = node.getBoundingClientRect();
        top = Math.max(top, clip.top); bottom = Math.min(bottom, clip.bottom);
        left = Math.max(left, clip.left); right = Math.min(right, clip.right);
    }
    // A portrait video taller than the available viewport can still fill it.
    const viewport = window.visualViewport;
    const viewportTop = viewport?.offsetTop || 0;
    const viewportHeight = viewport?.height || window.innerHeight || rect.height;
    const occlusion = Math.max(0, rect.top + insets.top - Math.max(rect.top, viewportTop));
    const availableHeight = Math.max(1, viewportHeight - occlusion);
    const horizontalRatio = Math.max(0, right - left) / rect.width;
    const visibleArea = Math.max(0, bottom - top) * Math.max(0, right - left);
    return { ratio: Math.max(0, bottom - top) / Math.min(rect.height, availableHeight)
        * horizontalRatio, visibleArea, pictureArea: rect.width * Math.min(rect.height, viewportHeight),
        top: rect.top, left: rect.left };
}

// Feed media is rounded by its own frame or by the grid around it, so only the
// corners that meet that rounded edge are curved.
function feedMediaRadii(element, rect = element.getBoundingClientRect()) {
    for (let node = element; node && node !== document.body; node = node.parentElement) {
        const style = getComputedStyle(node);
        const radius = parseFloat(style.borderTopLeftRadius || style.borderRadius) || 0;
        if (!radius) continue;
        const frame = node.getBoundingClientRect();
        const border = parseFloat(style.borderTopWidth) || 0;
        const near = (a, b) => Math.abs(a - b) < 2;
        const left = near(rect.left, frame.left + border), right = near(rect.left + rect.width, frame.left + frame.width - border);
        const top = near(rect.top, frame.top + border), bottom = near(rect.top + rect.height, frame.top + frame.height - border);
        return [top && left, top && right, bottom && right, bottom && left].map(meets => meets ? Math.max(0, radius - border) : 0);
    }
    return [0, 0, 0, 0];
}

// Where the feed draws a picture. A photo or GIF is its own picture. The inline
// player's placeholder keeps the player's frame, border included, and the
// picture sits contained (letterboxed) or cropped inside it.
function feedPicture(origin, aspect) {
    if (origin.tagName === 'IMG' || origin.tagName === 'VIDEO') {
        return {rect: fittedMediaRect(origin), box: origin.getBoundingClientRect(), holder: origin.parentElement,
            background: getComputedStyle(origin).backgroundColor, element: origin};
    }
    const frame = origin.getBoundingClientRect();
    const style = getComputedStyle(origin);
    const [top, right, bottom, left] = ['Top', 'Right', 'Bottom', 'Left'].map(side => parseFloat(style[`border${side}Width`]) || 0);
    const box = {left: frame.left + left, top: frame.top + top, width: frame.width - left - right, height: frame.height - top - bottom};
    let rect = box;
    if (origin.getAttribute?.('data-feed-fit') !== 'cover' && aspect) {
        const width = Math.min(box.width, box.height * aspect), height = width / aspect;
        rect = {left: box.left + (box.width - width) / 2, top: box.top + (box.height - height) / 2, width, height};
    }
    return {rect, box, holder: origin, background: origin.getAttribute?.('data-feed-background'), element: null};
}

// A video player moves its picture inside a wrapper while its controls stay put;
// a photo or GIF moves itself.
function animateMobileMediaBounds(media, origin, opening, duration, startBounds = null, shade = null, shadeOpacity = 1) {
    const ownSurface = media?.tagName === 'IMG' || (media?.tagName === 'VIDEO' && !isVideoPlayer(media));
    const surface = ownSurface ? media : media?.parentElement;
    if (!surface?.animate || !origin?.isConnected || !duration) return null;
    const originRect = origin.getBoundingClientRect();
    const frame = media.getBoundingClientRect();
    if (!originRect.width || !originRect.height || !frame.width || !frame.height) return null;
    const fitted = fittedMediaRect(media, true);
    // Land exactly where the feed draws the picture, so nothing shifts at handoff.
    const picture = feedPicture(origin, fitted.width / fitted.height);
    const source = picture.rect;
    const saved = [];
    const style = (element, values) => {
        for (const [name, value] of values) {
            saved.push([element, name, element.style.getPropertyValue(name)]);
            element.style.setProperty(name, value);
        }
    };
    const viewer = media.closest('.lightbox-viewer');
    // A returning picture passes beneath the sticky headers that cover it in the
    // feed. Cropping it at their edge instead makes the covered part reappear
    // behind their translucent glass after handoff. A header inside another
    // stacking context can't rise above the viewer, so it keeps the crop.
    const raised = [];
    if (!opening && viewer) {
        const zIndex = String((parseInt(getComputedStyle(viewer).zIndex, 10) || 0) + 1);
        document.querySelectorAll('[data-video-occluder]').forEach(header => {
            const rect = header.getBoundingClientRect();
            const left = Math.max(rect.left, source.left), right = Math.min(rect.left + rect.width, source.left + source.width);
            const top = Math.max(rect.top, source.top, 0), bottom = Math.min(rect.top + rect.height, source.top + source.height);
            if (left >= right || top >= bottom) return;
            const previous = header.style.getPropertyValue('z-index');
            header.style.setProperty('z-index', zIndex);
            if (header.contains(document.elementFromPoint?.((left + right) / 2, (top + bottom) / 2))) {
                saved.push([header, 'z-index', previous]);
                raised.push(header);
            } else if (previous) header.style.setProperty('z-index', previous);
            else header.style.removeProperty('z-index');
        });
    }
    // Safari never draws a screen-fixed layer beneath its toolbars. While the
    // picture travels, place the viewer in the page at the same screen position,
    // so the part of the picture beneath a toolbar stays visible there.
    const stage = viewer?.querySelector?.('.lightbox-stage');
    const page = stage?.getBoundingClientRect();
    const inPage = Boolean(page?.width && page.height);
    if (inPage) {
        style(viewer, [['position', 'absolute'], ['left', `${page.left + (window.scrollX || 0)}px`],
            ['top', `${page.top + (window.scrollY || 0)}px`], ['right', 'auto'], ['bottom', 'auto'],
            ['width', `${page.width}px`], ['height', `${page.height}px`], ['--lightbox-bleed', '0px'], ['overflow', 'visible']]);
        style(stage, [['overflow', 'visible'], ['contain', 'size layout style']]);
    }
    // The feed copy hides while the viewer's copy is in flight; letterbox bars
    // keep the feed's fill, so the frame looks the same before and after.
    const letterboxed = picture.rect.width < picture.box.width - .5 || picture.rect.height < picture.box.height - .5;
    if (letterboxed && picture.holder?.style && picture.background && !/^(transparent|rgba\(.*, 0\))$/.test(picture.background)) {
        style(picture.holder, [['background-color', picture.background], ['background-clip', 'padding-box']]);
    }
    if (picture.element?.style) style(picture.element, [['visibility', 'hidden']]);
    const feedInsets = videoFeedInsets(source, raised, !inPage);
    const crop = rect => {
        const scale = Math.max(rect.width / fitted.width, rect.height / fitted.height);
        // Exact-aspect targets leave float dust (e.g. 6e-14) that CSS can't parse.
        const cropX = Math.max(0, Math.round((fitted.width - rect.width / scale) / 2 * 1e4) / 1e4);
        const cropY = Math.max(0, Math.round((fitted.height - rect.height / scale) / 2 * 1e4) / 1e4);
        return {scale, cropX, cropY};
    };
    const cropped = Object.values(feedInsets).some(value => value > .5) || [source, startBounds || fitted].some(rect => {
        const {cropX, cropY} = crop(rect);
        return cropX > .5 || cropY > .5;
    });
    const bounds = (rect, radii, insets = {}) => {
        const {scale, cropX, cropY} = crop(rect);
        const corners = [insets.top || insets.left, insets.top || insets.right, insets.bottom || insets.right, insets.bottom || insets.left]
            .map((clipped, index) => `${clipped ? 0 : radii[index] / scale}px`).join(' ');
        return {
            transform: `translate3d(${rect.left - fitted.left - cropX * scale}px, ${rect.top - fitted.top - cropY * scale}px, 0) scale(${scale})`,
            borderRadius: radii.map(radius => `${radius / scale}px`).join(' '),
            // Ordinary videos need only a rounded, overflow-hidden surface.
            // Grid/quoted crops use a clip on that surface, never on the video.
            ...(cropped ? {clipPath: `inset(${cropY + (insets.top || 0) / scale}px ${cropX + (insets.right || 0) / scale}px ${cropY + (insets.bottom || 0) / scale}px ${cropX + (insets.left || 0) / scale}px round ${corners})`} : {}),
        };
    };
    const feed = bounds(source, feedMediaRadii(origin, source), feedInsets);
    const fullscreen = bounds(startBounds || fitted, Array(4).fill(startBounds?.radius || 0));
    if (ownSurface) {
        const parent = surface.parentElement.getBoundingClientRect();
        // Fullscreen photos fill the available box, which can letterbox them.
        // Move their fitted picture, keeping the transform origin on its edge.
        style(surface, [['position', 'absolute'], ['left', `${fitted.left - parent.left}px`], ['top', `${fitted.top - parent.top}px`], ['width', `${fitted.width}px`], ['height', `${fitted.height}px`], ['max-width', 'none'], ['max-height', 'none'], ['transform-origin', '0 0'], ['will-change', 'transform']]);
    } else {
        const parent = surface.parentElement.getBoundingClientRect();
        // fitted already includes the resting video's individual CSS translate.
        // Applying that lift again shifts the whole flight and its last frame.
        style(surface, [['position', 'absolute'], ['left', `${fitted.left - parent.left}px`], ['top', `${fitted.top - parent.top}px`], ['width', `${fitted.width}px`], ['height', `${fitted.height}px`], ['max-width', 'none'], ['aspect-ratio', 'auto'], ['overflow', 'hidden'], ['border', '0'], ['background', 'transparent'], ['translate', 'none'], ['transform-origin', '0 0'], ['will-change', 'transform']]);
        style(media, [['position', 'absolute'], ['left', '0'], ['top', '0'], ['width', '100%'], ['height', '100%'], ['max-width', 'none'], ['max-height', 'none'], ['object-fit', 'contain']]);
    }
    const options = { duration, easing: opening ? 'cubic-bezier(0.22, 0.75, 0.2, 1)' : 'cubic-bezier(0.22, 0.65, 0.3, 1)', fill: 'both' };
    const animation = surface.animate(opening ? [feed, fullscreen] : [fullscreen, feed], options);
    // Open to the shade's resting opacity, which its reactive style keeps afterwards.
    const fade = [opening ? 0 : startBounds?.backdropOpacity ?? shadeOpacity, opening ? shadeOpacity : 0];
    // The backdrop settles well before the picture lands, in either direction.
    const fadeOptions = {...options, duration: Math.min(duration, opening ? 150 : 120), easing: 'ease-out'};
    // Opacity runs on the compositor; a color fade would repaint the shade,
    // three screens tall, on every frame and stall the motion's first frames.
    const backdrop = shade?.animate?.(fade.map(opacity => ({opacity})), fadeOptions);
    // Raised headers left the backdrop early, so they fade in with the page.
    const headerFades = raised.map(header => header.animate?.([{opacity: 1 - fade[0]}, {opacity: 1}], fadeOptions));
    stepAnimationClock([animation, backdrop, ...headerFades].filter(Boolean), duration);
    let cleaned = false;
    const cleanup = () => {
        if (cleaned) return;
        cleaned = true;
        animation.cancel();
        backdrop?.cancel();
        headerFades.forEach(headerFade => headerFade?.cancel());
        for (const [element, name, value] of saved) {
            if (value) element.style.setProperty(name, value);
            else element.style.removeProperty(name);
        }
    };
    return {
        cancel: cleanup,
        bounds() {
            const rect = surface.getBoundingClientRect();
            const computed = getComputedStyle(surface);
            const inset = computed.clipPath?.match(/^inset\(([\d.]+)px ([\d.]+)px(?: ([\d.]+)px ([\d.]+)px)?/);
            const scale = rect.width / fitted.width;
            const x = Number(inset?.[4] || inset?.[2] || 0) * scale;
            const y = Number(inset?.[1] || 0) * scale;
            return {left: rect.left + x, top: rect.top + y,
                width: rect.width - x - Number(inset?.[2] || 0) * scale,
                height: rect.height - y - Number(inset?.[3] || inset?.[1] || 0) * scale,
                radius: (parseFloat(computed.borderRadius) || 0) * scale};
        },
        // Keep the last frame until the caller has handed the surface back.
        finished: animation.finished.then(() => true, () => { cleanup(); return false; }),
    };
}

// Media lists travel in an HTML-escaped data attribute, so no field value is ever
// parsed as script inside the inline handler.
function tweetNookOpenLightbox(element, index) {
    const holder = element?.closest?.('[data-lightbox-media]');
    // Ignore Safari's compatibility click that follows a dismissal gesture.
    if (!holder || Date.now() < videoTapBlockedUntil) return;
    window.dispatchEvent(new CustomEvent('open-lightbox', {
        detail: { media: JSON.parse(holder.dataset.lightboxMedia), index, origin: element },
    }));
}

// GIFs play in place on desktop; a phone opens them in the viewer like photos.
function tweetNookOpenGif(event, video, index) {
    if (!isPhoneLayout()) return;
    event?.stopPropagation?.();
    tweetNookOpenLightbox(video, index);
}

window.tweetNookOpenLightbox = tweetNookOpenLightbox;
window.tweetNookOpenGif = tweetNookOpenGif;
