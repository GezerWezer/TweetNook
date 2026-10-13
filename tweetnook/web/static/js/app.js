/**
 * Main Alpine.js application for the TweetNook Web UI.
 */

const statsSuspendedVideos = new Set();
// Autoplay mode: feed players at least this visible are candidates; the topmost one
// plays. A candidate paused while on screen stays paused until it scrolls away.
const VIDEO_AUTOPLAY_VISIBLE_RATIO = 0.5;
const autoplayVideos = new Set();
const heldAutoplayVideos = new WeakSet();
// Players muted only because the browser refused audible autoplay.
const forcedMuteVideos = new WeakSet();
const AVATAR_URL_VERSION = 3;
// Video controls fade this long after the last pointer/touch/key activity
// during playback, and almost immediately once the pointer leaves the video.
const VIDEO_UI_IDLE_MS = 3000;
const VIDEO_UI_LEAVE_MS = 300;
// Replies (including nested ones) rendered per batch in a thread; a saved reply tree
// can hold 250 tweets, and rendering them all at once delays the first paint.
const THREAD_REPLY_BATCH = 30;
// First feed pages stay reusable this long; finished jobs clear them sooner.
const FEED_CACHE_MS = 5 * 60 * 1000;
const FEED_CACHE_LIMIT = 12;
// Load the next page while the end of the list is still this far below the viewport.
const FEED_PREFETCH_MARGIN_PX = 1200;
// Lazy images start loading this far outside the viewport, ahead of the browser's
// own lazy-load distance, which on mobile is too short to hide the load.
const MEDIA_PREFETCH_MARGIN_PX = 1600;
// Hovering a tweet this long starts loading its conversation.
const THREAD_HOVER_PREFETCH_MS = 150;
const DEFAULT_FEEDS = [['likes', 'liked_latest'], ['all', 'newest'], ['bookmarks', 'newest'], ['tweets', 'newest']];
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

function tweetNookVideoPlayer(video) {
    return video?.closest?.('.media-video-player') || video?.parentElement;
}

function isMobileFullscreenVideo(video) {
    return Boolean(video?.closest?.('.lightbox-stage') && window.matchMedia?.('(max-width: 640px), (pointer: coarse)')?.matches);
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
            || (video.closest?.('.lightbox-stage') && window.matchMedia?.('(max-width: 640px), (pointer: coarse)')?.matches);
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
    const mobileViewer = video.closest?.('.lightbox-stage') && window.matchMedia?.('(max-width: 640px), (pointer: coarse)')?.matches;
    const fullscreenLabel = document.fullscreenElement === player ? 'Exit full screen' : mobileViewer ? (filled ? 'Fit video' : 'Fill screen') : 'Full screen';
    if (fullscreen.getAttribute('aria-label') !== fullscreenLabel) {
        fullscreen.setAttribute('aria-label', fullscreenLabel);
        fullscreen.title = fullscreenLabel;
        fullscreen.innerHTML = videoIcon(document.fullscreenElement === player || filled ? 'exitFullscreen' : 'fullscreen');
    }
    if (video.videoWidth && video.videoHeight) player.style.setProperty('--video-aspect', video.videoWidth / video.videoHeight);
}

function isMobileInlineVideo(video) {
    return Boolean(window.matchMedia?.('(max-width: 640px), (pointer: coarse)')?.matches)
        && !video.closest?.('.lightbox-stage') && document.fullscreenElement !== tweetNookVideoPlayer(video);
}

function tweetNookOpenMobileVideo(video) {
    if (Date.now() < videoTapBlockedUntil) return;
    window.dispatchEvent(new CustomEvent('open-lightbox', {
        detail: { media: [{ type: 'video', src: video.currentSrc || video.src, isGif: false }], index: 0, originVideo: video },
    }));
}

function tweetNookPlayerAction(video, action) {
    if (action === 'play') {
        if (video.paused || video.ended) video.play()?.catch?.(() => tweetNookSyncVideoUi(video));
        else video.pause();
    } else if (action === 'mute') {
        if (video.volume === 0) { video.volume = 1; video.muted = false; }
        else video.muted = !video.muted;
    } else if (action === 'fullscreen') {
        if (document.fullscreenElement) document.exitFullscreen()?.catch?.(() => {});
        else if (isMobileInlineVideo(video)) tweetNookOpenMobileVideo(video);
        else if (video.closest?.('.lightbox-stage') && window.matchMedia?.('(max-width: 640px), (pointer: coarse)')?.matches) {
            tweetNookVideoPlayer(video).classList.toggle('is-video-fill');
        }
        else if (tweetNookVideoPlayer(video).requestFullscreen) tweetNookVideoPlayer(video).requestFullscreen()?.catch?.(() => {});
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
        event.preventDefault();
        event.stopPropagation();
        if (isMobileInlineVideo(video) && Date.now() < videoTapBlockedUntil) return;
        const button = event.target.closest('[data-video-action]');
        const surface = event.target === video || event.target === player;
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
        if (event.target.hasAttribute('data-video-seek') && Number.isFinite(video.duration)) {
            const state = videoScrubStates.get(video);
            if (state?.pointerControlled) return;
            video.currentTime = Math.max(0, Math.min(video.duration, Number(event.target.value) / 100 * video.duration));
            if (state) state.targetTime = video.currentTime;
        } else if (event.target.hasAttribute('data-video-volume')) {
            video.volume = Math.max(0, Math.min(1, Number(event.target.value)));
            video.muted = video.volume === 0;
        }
        tweetNookShowVideoUi(video);
        tweetNookSyncVideoDuration(video);
    });
    player.addEventListener('pointerdown', event => {
        if (event.target.hasAttribute('data-video-seek') && !(event.button > 0)) tweetNookStartVideoScrub(video, event);
    });
    player.addEventListener('keydown', event => {
        if (event.target.closest('input, button')) {
            if (event.key !== 'Escape') event.stopPropagation();
            return;
        }
        const action = { ' ': 'play', k: 'play', m: 'mute', f: 'fullscreen' }[event.key.toLowerCase()];
        if (action) { event.preventDefault(); event.stopPropagation(); tweetNookPlayerAction(video, action); }
        else if (['ArrowLeft', 'ArrowRight'].includes(event.key) && Number.isFinite(video.duration)) {
            event.preventDefault(); event.stopPropagation();
            video.currentTime = Math.max(0, Math.min(video.duration, video.currentTime + (event.key === 'ArrowRight' ? 5 : -5)));
            tweetNookShowVideoUi(video);
            tweetNookSyncVideoDuration(video);
        }
    });
    player.tabIndex = 0;
    player.setAttribute('role', 'group');
    player.setAttribute('aria-label', 'Video player');
    for (const name of ['pointermove', 'pointerenter']) player.addEventListener(name, event => {
        if (event.pointerType !== 'touch' && !isMobileFullscreenVideo(video)) tweetNookShowVideoUi(video);
    });
    player.addEventListener('focusin', event => {
        if (event.target.matches(':focus-visible')) tweetNookShowVideoUi(video);
    });
    player.addEventListener('pointerleave', event => {
        if (event.pointerType !== 'touch' && !isMobileFullscreenVideo(video)) tweetNookLeaveVideoUi(video);
    });
    player.addEventListener('focusout', () => tweetNookShowVideoUi(video, isMobileFullscreenVideo(video) ? VIDEO_UI_IDLE_MS : VIDEO_UI_LEAVE_MS));
    for (const name of ['loadedmetadata', 'durationchange', 'timeupdate', 'volumechange']) video.addEventListener(name, () => tweetNookSyncVideoDuration(video));
    video.addEventListener('play', () => tweetNookShowVideoUi(video));
    for (const name of ['pause', 'ended']) video.addEventListener(name, () => tweetNookSyncVideoUi(video));
    player.addEventListener('fullscreenchange', () => tweetNookShowVideoUi(video));
    tweetNookSyncVideoDuration(video);
    tweetNookSyncVideoUi(video);
}

// Keep the video decoder's surface at one size and move/crop it on the compositor.
function fittedVideoRect(video, includeCrop = false) {
    const frame = video.getBoundingClientRect();
    const cover = getComputedStyle(video).objectFit === 'cover';
    if (cover && !includeCrop) return frame;
    const ratio = video.videoWidth / video.videoHeight || Number(video.dataset?.videoAspect) || frame.width / frame.height;
    const width = cover ? Math.max(frame.width, frame.height * ratio) : Math.min(frame.width, frame.height * ratio);
    const height = width / ratio;
    return { left: frame.left + (frame.width - width) / 2, top: frame.top + (frame.height - height) / 2, width, height };
}

function videoFeedInsets(source, clipBottom = true) {
    const viewport = window.visualViewport;
    const left = viewport?.offsetLeft || 0;
    let top = viewport?.offsetTop || 0;
    const right = left + (viewport?.width || window.innerWidth || Infinity);
    const bottom = top + (viewport?.height || window.innerHeight || Infinity);
    document.querySelectorAll('[data-video-occluder]').forEach(header => {
        const rect = header.getBoundingClientRect();
        if (rect.width && rect.height && rect.left < source.left + source.width && rect.left + rect.width > source.left
            && rect.top <= Math.max(top, source.top) && rect.top + rect.height > top) top = Math.max(top, rect.top + rect.height);
    });
    return {top: Math.min(source.height, Math.max(0, top - source.top)),
        bottom: clipBottom ? Math.min(source.height, Math.max(0, source.top + source.height - bottom)) : 0,
        left: Math.min(source.width, Math.max(0, left - source.left)),
        right: Math.min(source.width, Math.max(0, source.left + source.width - right))};
}

function videoBackdropOpacity(backdrop) {
    const style = getComputedStyle(backdrop);
    const color = style.backgroundColor;
    if (!color) return Number(style.opacity);
    if (color === 'transparent') return 0;
    return color?.startsWith('rgba(') ? Number(color.split(',')[3].replace(')', '')) : 1;
}

function animateMobileVideoBounds(video, origin, opening, duration, startBounds = null) {
    const surface = video?.parentElement;
    if (!surface?.animate || !origin?.isConnected || !duration) return null;
    const source = origin.getBoundingClientRect();
    const frame = video.getBoundingClientRect();
    const parent = surface.parentElement.getBoundingClientRect();
    if (!source.width || !source.height || !frame.width || !frame.height) return null;
    const fitted = fittedVideoRect(video, true);
    // The page-positioned return must paint the same video pixels behind Safari's
    // URL bar that the inline player will occupy after handoff.
    const feedInsets = videoFeedInsets(source, opening);
    const radius = parseFloat(getComputedStyle(origin.tagName === 'VIDEO' ? origin.parentElement : origin).borderRadius) || 0;
    const crop = rect => {
        const scale = Math.max(rect.width / fitted.width, rect.height / fitted.height);
        const cropX = Math.max(0, (fitted.width - rect.width / scale) / 2);
        const cropY = Math.max(0, (fitted.height - rect.height / scale) / 2);
        return {scale, cropX, cropY};
    };
    const cropped = Object.values(feedInsets).some(value => value > .5) || [source, startBounds || fitted].some(rect => {
        const {cropX, cropY} = crop(rect);
        return cropX > .5 || cropY > .5;
    });
    const bounds = (rect, borderRadius, insets = {}) => {
        const {scale, cropX, cropY} = crop(rect);
        const corner = borderRadius / scale;
        const corners = [insets.top || insets.left, insets.top || insets.right, insets.bottom || insets.right, insets.bottom || insets.left]
            .map(clipped => `${clipped ? 0 : corner}px`).join(' ');
        return {
            transform: `translate3d(${rect.left - fitted.left - cropX * scale}px, ${rect.top - fitted.top - cropY * scale}px, 0) scale(${scale})`,
            borderRadius: `${borderRadius / scale}px`,
            // Ordinary videos need only a rounded, overflow-hidden surface.
            // Grid/quoted crops use a clip on that surface, never on the video.
            ...(cropped ? {clipPath: `inset(${cropY + (insets.top || 0) / scale}px ${cropX + (insets.right || 0) / scale}px ${cropY + (insets.bottom || 0) / scale}px ${cropX + (insets.left || 0) / scale}px round ${corners})`} : {}),
        };
    };
    const feed = bounds(source, radius, feedInsets);
    const fullscreen = bounds(startBounds || fitted, startBounds?.radius || 0);
    const saved = [];
    const style = (element, values) => {
        for (const [name, value] of values) {
            saved.push([element, name, element.style.getPropertyValue(name)]);
            element.style.setProperty(name, value);
        }
    };
    const viewer = video.closest('.lightbox-viewer');
    let pageReturn = false;
    if (!opening && viewer?.getBoundingClientRect) {
        const rect = viewer.getBoundingClientRect();
        // Release Safari's fixed-edge fill when dismissal starts. Keeping the
        // viewer fixed until handoff delays the browser toolbar's own fade by
        // the entire return animation. Keep the same screen bounds in the page.
        if (rect.width && rect.height) {
            pageReturn = true;
            viewer.classList?.add('is-video-closing');
            style(viewer, [['position', 'absolute'], ['left', `${rect.left + (window.scrollX || 0)}px`],
                ['top', `${rect.top + (window.scrollY || 0)}px`], ['right', 'auto'], ['bottom', 'auto'],
                ['width', `${rect.width}px`], ['height', `${rect.height}px`]]);
        }
    }
    style(surface, [['position', 'absolute'], ['left', `${fitted.left - parent.left}px`], ['top', `${fitted.top - parent.top}px`], ['width', `${fitted.width}px`], ['height', `${fitted.height}px`], ['max-width', 'none'], ['aspect-ratio', 'auto'], ['overflow', 'hidden'], ['border', '0'], ['background', 'transparent'], ['transform-origin', '0 0'], ['will-change', 'transform']]);
    style(video, [['position', 'absolute'], ['left', '0'], ['top', '0'], ['width', '100%'], ['height', '100%'], ['max-width', 'none'], ['max-height', 'none'], ['object-fit', 'contain']]);
    const options = { duration, easing: opening ? 'cubic-bezier(0.22, 0.75, 0.2, 1)' : 'cubic-bezier(0.22, 0.65, 0.3, 1)', fill: 'both' };
    const animation = surface.animate(opening ? [feed, fullscreen] : [fullscreen, feed], options);
    const fade = [opening ? 0 : startBounds?.backdropOpacity ?? 1, opening ? 1 : 0];
    // The opening viewer owns the fixed color and its fade. A separate absolute
    // sibling behind a transparent fixed viewer misses Safari's toolbar sampling.
    const backdrop = viewer?.animate?.(fade.map(opacity => ({backgroundColor: `rgba(0, 0, 0, ${opacity})`})), {
        ...options, duration: opening ? duration : Math.min(duration, 160), easing: opening ? 'linear' : 'ease-out',
    });
    let cleaned = false;
    const cleanup = () => {
        if (cleaned) return;
        cleaned = true;
        animation.cancel();
        backdrop?.cancel();
        if (pageReturn) viewer.classList?.remove('is-video-closing');
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
    if (!holder) return;
    window.dispatchEvent(new CustomEvent('open-lightbox', {
        detail: { media: JSON.parse(holder.dataset.lightboxMedia), index },
    }));
}

window.tweetNookOpenLightbox = tweetNookOpenLightbox;

// Feed cards are plain markup handled by delegated listeners. Writing them while
// Alpine's observer is paused keeps Alpine from walking (and binding) every card.
function outsideAlpine(update) {
    return window.Alpine?.mutateDom ? window.Alpine.mutateDom(update) : update();
}

// Status responses are JSON trees. Retain unchanged reactive objects so polling
// doesn't invalidate every binding in the mounted (including hidden) tray pages.
function reconcileActivityValue(current, next) {
    if (current === next) return current;
    if (!current || !next || typeof current !== 'object' || typeof next !== 'object'
        || Array.isArray(current) !== Array.isArray(next)) return next;
    if (Array.isArray(next) && (current.length !== next.length
        || next.some((item, index) => item?.key !== current[index]?.key))) return next;

    for (const key of Object.keys(current)) {
        if (!Object.prototype.hasOwnProperty.call(next, key)) delete current[key];
    }
    for (const key of Object.keys(next)) {
        if (key === '__proto__' || key === 'constructor' || key === 'prototype') continue;
        const value = reconcileActivityValue(current[key], next[key]);
        if (current[key] !== value) current[key] = value;
    }
    return current;
}

function tweetApp() {
    // Transport state is deliberately outside Alpine's reactive component.
    let activityRequest = null;
    let activityPollingEnabled = false;
    let activityClockTimer = null;
    let activityVisibilityHandler = null;
    let lightboxMotionTimer = null;
    // Mobile video viewing hands playback back to the original inline player.
    let lightboxOriginVideo = null;
    let lightboxInlineMount = null;
    let lightboxVideoMotion = null;
    let lightboxVideoReadyCancel = null;
    // Lists and threads send abbreviated raw JSON. Copy raw reads the stored document,
    // prefetched when a tweet menu opens so the copy usually stays in the click gesture.
    const rawTweetRequests = new Map();
    const rawTweetValues = new Map();
    // First pages by URL ({at, speculative, result, promise}) and the URL on screen.
    const feedPages = new Map();
    let feedListUrl = null;
    let feedPrefetchScheduled = false;
    // The list element whose delegated listeners are bound, and the card button
    // currently covered by the shared list menu.
    let boundFeedList = null;
    let listMenuButton = null;

    return {
        isDemo: window.TWEETNOOK_DEMO === true,
        viewMode: 'list',
        activeRoute: { viewMode: 'list' },
        pendingRoute: null,
        pendingRouteState: null,
        navigationDepth: 0,
        routeUsesPanel: false,
        tweets: [],
        loading: true,
        loadingMore: false,
        page: 1,
        totalPages: 1,
        total: 0,
        hasMore: false,
        nextCursor: null,
        feedRequestId: 0,
        feedGeneration: 0,
        countController: null,
        randomSeed: null,
        collectionFilter: 'likes',
        sortOrder: 'liked_latest',
        feedMenu: null,
        feedMenuTrigger: null,

        feedOptions(kind) {
            if (kind === 'collection') return [
                { value: 'all', label: 'All Tweets' },
                { value: 'likes', label: 'Likes' },
                { value: 'bookmarks', label: 'Bookmarks' },
                { value: 'tweets', label: 'Your Tweets' },
            ];
            return [
                ...(this.searchQuery.trim() ? [{ value: 'default', label: 'Relevance' }] : []),
                ...(this.collectionFilter === 'likes' ? [
                    { value: 'liked_latest', label: 'Recently liked' },
                    { value: 'liked_earliest', label: 'Earliest liked' },
                ] : []),
                { value: 'newest', label: 'Latest' },
                { value: 'oldest', label: 'Oldest' },
                { value: 'random', label: 'Random' },
            ];
        },
        feedValue(kind) {
            return kind === 'collection' ? this.collectionFilter : this.sortOrder;
        },
        feedLabel(kind) {
            return this.feedOptions(kind).find(option => option.value === this.feedValue(kind))?.label || 'Relevance';
        },
        resultCountLabel() {
            if (this.searchQuery.trim() && this.total === null) {
                return this.countError ? 'Count unavailable · Results' : 'Counting… Results';
            }
            const count = Number(this.total) || 0;
            const noun = this.searchQuery.trim()
                ? 'Results'
                : (this.collectionFilter === 'all'
                    ? 'Tweets'
                    : this.collectionFilter.charAt(0).toUpperCase() + this.collectionFilter.slice(1));
            return `${count.toLocaleString()} ${noun}`;
        },
        openFeedMenu(kind, trigger) {
            this.feedMenu = kind;
            this.feedMenuTrigger = trigger;
            const selected = this.feedOptions(kind).findIndex(option => option.value === this.feedValue(kind));
            this.$nextTick(() => this.focusFeedOption(kind, Math.max(0, selected)));
        },
        closeFeedMenu(restoreFocus = false) {
            this.feedMenu = null;
            if (restoreFocus) this.feedMenuTrigger?.focus();
        },
        focusFeedOption(kind, index) {
            const buttons = this.$refs.feedMenus.querySelectorAll(`[data-feed-menu="${kind}"] [role="menuitemradio"]`);
            buttons[(index + buttons.length) % buttons.length]?.focus();
        },
        selectFeedOption(kind, value) {
            if (!this.feedOptions(kind).some(option => option.value === value)) return;
            const changed = value !== this.feedValue(kind);
            if (kind === 'collection') {
                this.collectionFilter = value;
                if (changed) this.sortOrder = value === 'likes' ? 'liked_latest' : 'newest';
            } else {
                this.sortOrder = value;
            }
            this.closeFeedMenu(true);
            if (changed) this.search();
        },
        searchQuery: '',
        searchHighlightTerms: [],
        countError: false,
        error: null,
        showScrollTop: false,

        threadData: null,
        threadReplyLimit: THREAD_REPLY_BATCH,
        threadOtherReplies: false,
        loadingThread: false,
        threadCache: new Map(),
        threadCacheTtlMs: 60000,
        
        quotesTweetId: null,
        quotesList: [],
        quotesPage: 1,
        quotesTotalPages: 1,
        quotesLoading: false,
        quotesLoadingMore: false,
        
        lightboxOpen: false,
        lightboxMedia: [],
        lightboxIndex: 0,
        lightboxGestureStart: null,
        lightboxSuppressClickUntil: 0,
        lightboxGestureAxis: null,
        lightboxGestureOffsetX: 0,
        lightboxGestureOffsetY: 0,
        lightboxGestureTransition: 'none',
        lightboxGestureAnimating: false,
        lightboxDismissPending: false,
        lightboxVideoMotionPhase: '',
        videoObserver: null,
        videoAutoplayObserver: null,
        mediaPrefetchObserver: null,

        // Split panel state
        splitPanel: false,
        panelStack: [],
        panelMode: null,        // 'thread' | 'quotes' | null
        panelThreadData: null,
        panelReplyLimit: THREAD_REPLY_BATCH,
        panelOtherReplies: false,
        panelLoadingThread: false,
        panelQuotesTweetId: null,
        panelQuotesList: [],
        panelQuotesPage: 1,
        panelQuotesTotalPages: 1,
        panelQuotesLoading: false,
        panelQuotesLoadingMore: false,

        tweetMenuOpen: null,
        // Feed cards share one overflow menu: the opening card's key and display
        // tweet, and where its button sits within the list area.
        listMenu: { key: 'list:', tweet: null, hidden: false, top: 0, right: 0 },
        tweetMenuPressTimer: null,
        tweetMenuClickBlockedUntil: 0,

        tagModalOpen: false,
        tagModalData: null,
        tagModalTweetId: null,
        
        isEditingTags: false,
        editableTags: [],
        editableDescription: '',
        tagSearchQuery: '',
        tagAutocompleteOptions: [],
        tagSearchDropdown: false,
        
        showKeyboardShortcuts: false,
        profileCard: null,
        profileCardRequest: 0,
        activity: null,
        lastActivity: null,
        activitySchedule: { configured: false, relative: 'Not configured', date: 'Use cron or a service timer' },
        activityStartPending: false,
        activityStartingKind: null,
        activityError: null,
        activityDrawerOpen: false,
        activityPageKey: null,
        activityPageDirection: 'forward',
        activityNowSeconds: Math.floor(Date.now() / 1000),
        showLogModal: false,

        get displayActivity() {
            return this.activity || this.lastActivity;
        },

        async fetchActivityStatus(force = false) {
            if (activityRequest && !force) return activityRequest.promise;
            activityRequest?.controller.abort();
            clearTimeout(this.activityPollTimer);
            this.activityPollTimer = null;
            const request = { controller: new AbortController(), promise: null };
            activityRequest = request;
            request.promise = (async () => {
                let timedOut = false;
                const timeout = setTimeout(() => {
                    timedOut = true;
                    request.controller.abort();
                }, 10000);
                try {
                    const previousActiveStepKey = this.activity?.active_step
                        || this.activity?.steps?.find(step => step.state === 'active')?.key
                        || null;
                    const response = await fetch('/api/activity/status', { signal: request.controller.signal });
                    if (!response.ok) throw new Error('Could not load activity status');
                    const data = await response.json();
                    // An action/focus refresh can supersede a slow request, even if
                    // its response body finishes after cancellation.
                    if (activityRequest !== request || request.controller.signal.aborted) return;
                    this.activityNowSeconds = Math.floor(Date.now() / 1000);
                    this.activitySchedule = reconcileActivityValue(this.activitySchedule, data.schedule || this.activitySchedule);
                    this.activityError = null;
                    if (data.active) {
                        if (this.displayActivity?.run_id !== data.snapshot?.run_id) {
                            this.activityPageKey = null;
                            this.activityPageDirection = 'forward';
                        }
                        this.activity = this.activity?.run_id === data.snapshot?.run_id
                            ? reconcileActivityValue(this.activity, data.snapshot) : data.snapshot;
                        this.activityStartPending = false;
                        this.activityStartingKind = null;
                    } else {
                        // A finished job can change any list or conversation.
                        if (this.activity) {
                            feedPages.clear();
                            this.threadCache.clear();
                        }
                        const lastActivity = data.last_snapshot || this.activity || this.lastActivity;
                        this.lastActivity = this.lastActivity?.run_id === lastActivity?.run_id
                            ? reconcileActivityValue(this.lastActivity, lastActivity) : lastActivity;
                        this.activity = null;
                        if (lastActivity?.stopped && !this.activityPageKey && previousActiveStepKey) {
                            this.activityPageKey = previousActiveStepKey;
                        }
                    }
                } catch (error) {
                    if (activityRequest === request) {
                        this.activityError = timedOut ? 'Activity status request timed out' : error.message;
                    }
                } finally {
                    clearTimeout(timeout);
                    if (activityRequest === request) {
                        activityRequest = null;
                        this.scheduleActivityPoll();
                    }
                }
            })();
            return request.promise;
        },

        scheduleActivityPoll() {
            clearTimeout(this.activityPollTimer);
            this.activityPollTimer = null;
            if (!activityPollingEnabled || document.hidden) return;
            const delay = this.activity || this.activityStartPending ? 1000 : 5000;
            this.activityPollTimer = setTimeout(() => this.fetchActivityStatus(), delay);
        },

        startActivityPolling() {
            if (activityPollingEnabled) return;
            activityPollingEnabled = true;
            activityVisibilityHandler = () => {
                if (document.hidden) {
                    clearTimeout(this.activityPollTimer);
                    this.activityPollTimer = null;
                } else {
                    this.activityNowSeconds = Math.floor(Date.now() / 1000);
                    this.fetchActivityStatus(true);
                }
            };
            document.addEventListener('visibilitychange', activityVisibilityHandler);
            window.addEventListener('focus', activityVisibilityHandler);
            activityClockTimer = setInterval(() => {
                if (this.activity && !document.hidden) {
                    this.activityNowSeconds = Math.floor(Date.now() / 1000);
                }
            }, 1000);
            if (!document.hidden) this.fetchActivityStatus();
        },

        stopActivityPolling() {
            activityPollingEnabled = false;
            clearTimeout(this.activityPollTimer);
            this.activityPollTimer = null;
            clearInterval(activityClockTimer);
            if (activityVisibilityHandler) {
                document.removeEventListener('visibilitychange', activityVisibilityHandler);
                window.removeEventListener('focus', activityVisibilityHandler);
                activityVisibilityHandler = null;
            }
            activityRequest?.controller.abort();
            activityRequest = null;
        },

        destroy() {
            this.stopActivityPolling();
            clearTimeout(lightboxMotionTimer);
        },

        async fetchScheduleSettings() {
            this.scheduleError = '';
            try {
                const response = await fetch('/api/activity/schedule');
                if (!response.ok) throw new Error('Could not load schedule');
                const data = await response.json();
                this.scheduleSettings = data;
                this.scheduleForm = this.normalizeScheduleConfig(data.config);
                this.syncScheduleTimePicker();
                this.scheduleOriginal = JSON.parse(JSON.stringify(this.scheduleForm));
                this.scheduleSaved = false;
            } catch (error) {
                this.scheduleError = error.message;
                this.scheduleSettings = null;
            }
        },

        normalizeScheduleConfig(config = {}) {
            return {
                enabled: !!config.enabled,
                cadence: ['hours', 'daily', 'weekly', 'monthly'].includes(config.cadence)
                    ? config.cadence
                    : 'daily',
                every_hours: Number(config.every_hours ?? 6),
                time: config.time || '03:00',
                randomize_time: !!(config.randomize_time ?? true),
                random_offset_min_hours: Number(config.random_offset_min_hours ?? 0),
                random_offset_max_hours: Number(config.random_offset_max_hours ?? 2),
                weekday: Number(config.weekday ?? 0),
                day_of_month: Number(config.day_of_month ?? 1),
                timezone: config.timezone || 'local',
            };
        },

        scheduleTimezoneOptions() {
            const detected = Intl.DateTimeFormat().resolvedOptions().timeZone;
            const current = this.scheduleForm?.timezone;
            const values = [
                { value: 'local', label: 'System local time' },
                { value: 'UTC', label: 'UTC' },
            ];
            if (detected && !values.some(option => option.value === detected)) {
                values.push({ value: detected, label: `${detected} (browser)` });
            }
            const supported = typeof Intl.supportedValuesOf === 'function'
                ? Intl.supportedValuesOf('timeZone')
                : [];
            for (const timezone of supported) {
                if (!values.some(option => option.value === timezone)) {
                    values.push({ value: timezone, label: timezone });
                }
            }
            if (current && !values.some(option => option.value === current)) {
                values.push({ value: current, label: current });
            }
            return values;
        },

        scheduleFieldChanged() {
            this.scheduleSaved = false;
            this.scheduleError = '';
        },

        syncScheduleTimePicker() {
            const [hourValue, minuteValue] = (this.scheduleForm?.time || '03:00').split(':');
            const hour24 = Number(hourValue);
            this.scheduleTimeHour = String(hour24 % 12 || 12);
            this.scheduleTimeMinute = String(Number(minuteValue || 0)).padStart(2, '0');
            this.scheduleTimePeriod = hour24 >= 12 ? 'PM' : 'AM';
        },

        setScheduleTimePart(part, value) {
            if (!this.scheduleForm) return;
            if (part === 'hour') this.scheduleTimeHour = String(value);
            if (part === 'minute') this.scheduleTimeMinute = String(value).padStart(2, '0');
            if (part === 'period') this.scheduleTimePeriod = String(value);
            let hour = Number(this.scheduleTimeHour) % 12;
            if (this.scheduleTimePeriod === 'PM') hour += 12;
            this.scheduleForm.time = `${String(hour).padStart(2, '0')}:${this.scheduleTimeMinute}`;
            this.scheduleFieldChanged();
        },

        scheduleRandomWindowValid() {
            if (
                !this.scheduleForm?.randomize_time
                || this.scheduleForm.cadence === 'hours'
            ) return true;
            const minimum = Number(this.scheduleForm.random_offset_min_hours);
            const maximum = Number(this.scheduleForm.random_offset_max_hours);
            return Number.isFinite(minimum)
                && Number.isFinite(maximum)
                && minimum >= 0
                && maximum >= 0
                && minimum <= 24
                && maximum <= 24
                && minimum <= maximum;
        },

        scheduleHasChanges() {
            return !!this.scheduleForm
                && JSON.stringify(this.scheduleForm) !== JSON.stringify(this.scheduleOriginal);
        },

        async saveScheduleSettings() {
            if (!this.scheduleForm || this.scheduleSaving || !this.scheduleRandomWindowValid()) return;
            this.scheduleSaving = true;
            this.scheduleSaved = false;
            this.scheduleError = '';
            const values = this.normalizeScheduleConfig(this.scheduleForm);
            try {
                const response = await fetch('/api/activity/schedule', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(values),
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(
                        typeof data.detail === 'string'
                            ? data.detail
                            : 'Could not save schedule',
                    );
                }
                this.scheduleSettings = data;
                this.activitySchedule = data;
                this.scheduleForm = this.normalizeScheduleConfig(data.config);
                this.syncScheduleTimePicker();
                this.scheduleOriginal = JSON.parse(JSON.stringify(this.scheduleForm));
                this.scheduleSaved = true;
            } catch (error) {
                this.scheduleError = error.message;
            } finally {
                this.scheduleSaving = false;
            }
        },

        async fetchActivityRuns() {
            this.activityRunsLoading = true;
            try {
                const response = await fetch('/api/activity/runs?limit=100');
                if (!response.ok) throw new Error('Could not load activity logs');
                this.activityRuns = (await response.json()).runs || [];
            } catch (error) {
                this.activityError = error.message;
            } finally {
                this.activityRunsLoading = false;
            }
        },

        async openActivityRun(run) {
            this.selectedActivityRun = run;
            this.selectedActivityLog = 'Loading log…';
            this.showLogModal = true;
            try {
                const [detailsResponse, logResponse] = await Promise.all([
                    fetch(`/api/activity/runs/${encodeURIComponent(run.run_id)}`),
                    fetch(`/api/activity/runs/${encodeURIComponent(run.run_id)}/log`),
                ]);
                if (!detailsResponse.ok || !logResponse.ok) throw new Error('Could not load run log');
                this.selectedActivityRun = await detailsResponse.json();
                this.selectedActivityLog = await logResponse.text();
            } catch (error) {
                this.selectedActivityLog = error.message;
            }
        },

        formatActivityTimestamp(value) {
            if (!value) return '—';
            return new Date(Number(value) * 1000).toLocaleString();
        },

        activityScheduleDayText() {
            const schedule = this.activitySchedule || {};
            if (!schedule.enabled) return schedule.relative || 'Not scheduled';

            const nextRunAt = Number(schedule.next_run_at);
            if (Number.isFinite(nextRunAt) && nextRunAt > 0) {
                const now = new Date();
                const next = new Date(nextRunAt * 1000);
                const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
                const nextDay = new Date(next.getFullYear(), next.getMonth(), next.getDate());
                const dayOffset = Math.round((nextDay - today) / 86400000);
                if (dayOffset === 0) return 'Today';
                if (dayOffset === 1) return 'Tomorrow';
                return next.toLocaleDateString('en-US', {
                    weekday: 'short',
                    month: 'short',
                    day: 'numeric',
                });
            }

            const dateLabel = String(schedule.date || '').split('·')[0].trim();
            return dateLabel || schedule.relative || 'Scheduled';
        },

        activityScheduleTimeText() {
            const schedule = this.activitySchedule || {};
            if (!schedule.enabled) return '';

            const configuredTime = String(schedule.config?.time || '');
            const configuredMatch = configuredTime.match(/^(\d{2}):(\d{2})$/);
            if (configuredMatch && schedule.config?.cadence !== 'hours') {
                const hour = Number(configuredMatch[1]);
                const minute = configuredMatch[2];
                const period = hour >= 12 ? 'PM' : 'AM';
                const displayHour = hour % 12 || 12;
                const prefix = schedule.config?.randomize_time ? 'around' : 'at';
                return `${prefix} ${displayHour}:${minute} ${period}`;
            }

            const displayedTime = String(schedule.date || schedule.relative || '')
                .match(/\b\d{1,2}:\d{2}\s*[AP]M\b/i)?.[0];
            return displayedTime ? `at ${displayedTime.toUpperCase()}` : '';
        },

        formatLogText(text) {
            if (!text) return 'No log output.';
            return text.replace(/[<>]/g, c => c === '<' ? '&lt;' : '&gt;').split('\n').map(line => {
                if (line.includes('ERROR:')) return `<span style="color: var(--danger-color)">${line}</span>`;
                if (line.includes('WARNING:')) return `<span style="color: var(--accent-color)">${line}</span>`;
                const failureText = line.replace(/\b0+\s+failed\b/gi, '');
                if (/\bfailed\b/i.test(failureText)) {
                    return `<span style="color: var(--danger-color)">${line}</span>`;
                }
                if (line.includes('completed')) return `<span style="color: var(--accent-color)">${line}</span>`;
                const parts = line.split(/Z (.*)/);
                if (parts.length === 3) {
                    return `<span style="color: var(--text-secondary)">${parts[0]}Z</span> ${parts[1]}`;
                }
                return line;
            }).join('\n');
        },

        async startActivity(kind) {
            if (this.archiveReady === false || this.setupForced) return;
            if (this.activity || this.activityStartPending) return;
            this.activityStartPending = true;
            this.activityStartingKind = kind;
            this.activityPageKey = null;
            this.activityPageDirection = 'forward';
            this.activityError = null;
            try {
                const response = await fetch(`/api/activity/${kind}`, { method: 'POST' });
                if (!response.ok) {
                    const data = await response.json().catch(() => ({}));
                    throw new Error(data.detail || 'Could not start sync');
                }
                await new Promise(resolve => setTimeout(resolve, 250));
                await this.fetchActivityStatus(true);
                if (kind === 'enrich') await this.fetchArchiveEnrichmentStatus();
            } catch (error) {
                this.activityError = error.message;
                this.activityStartPending = false;
                this.activityStartingKind = null;
            }
        },

        async stopActivity() {
            if (!this.activity && !this.activityStartPending) return;
            this.activityError = null;
            try {
                const response = await fetch('/api/activity/stop', { method: 'POST' });
                if (!response.ok) {
                    const data = await response.json().catch(() => ({}));
                    throw new Error(data.detail || 'Could not stop task');
                }
                await new Promise(resolve => setTimeout(resolve, 100));
                await this.fetchActivityStatus(true);
            } catch (error) {
                this.activityError = error.message;
            }
        },

        formatActivityDuration(seconds) {
            const whole = Math.max(Math.round(Number(seconds) || 0), 0);
            const hours = Math.floor(whole / 3600);
            const minutes = Math.floor((whole % 3600) / 60);
            const remainder = whole % 60;
            return hours
                ? `${hours}:${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`
                : `${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`;
        },

        formatActivityBytes(value) {
            const bytes = Math.max(Number(value) || 0, 0);
            if (bytes < 1024) return `${bytes.toLocaleString()} B`;
            const units = ['KiB', 'MiB', 'GiB', 'TiB'];
            let amount = bytes / 1024;
            let index = 0;
            while (amount >= 1024 && index < units.length - 1) {
                amount /= 1024;
                index += 1;
            }
            return `${amount.toFixed(amount >= 10 ? 1 : 2)} ${units[index]}`;
        },

        activityPercent(step) {
            if (!step || !step.total) return 0;
            return Math.max(0, Math.min(100, step.completed / step.total * 100));
        },

        activitySegmentPercent(page) {
            if (!page) return 0;
            if (page.is_summary) return this.activity ? 0 : 100;
            if (page.state === 'complete' || page.state === 'skipped') return 100;
            return this.activityPercent(page);
        },

        activitySegmentState(page) {
            if (this.displayActivity?.stopped && page?.state === 'failed') return 'cancelled';
            return page?.state || 'pending';
        },

        activityPageStateLabel(page) {
            const labels = {
                active: 'In progress',
                complete: 'Complete',
                failed: 'Failed',
                cancelled: 'Canceled',
                skipped: 'Skipped',
                pending: 'Waiting',
            };
            return labels[this.activitySegmentState(page)] || 'Waiting';
        },

        activityPageUpdateText(page) {
            const counters = String(page?.counters || '').trim();
            if (counters) return `Latest update · ${counters}`;
            const labels = {
                active: 'Waiting for first result',
                complete: 'Stage complete',
                failed: 'Stopped with an error',
                cancelled: 'Canceled',
                skipped: 'No work needed',
                pending: 'Waiting to start',
            };
            return `Latest update · ${labels[this.activitySegmentState(page)] || 'Waiting to start'}`;
        },

        activityPageTimingText(page) {
            const state = this.activitySegmentState(page);
            const eta = Number(page?.eta_seconds);
            if (state === 'active' && page?.eta_seconds != null && Number.isFinite(eta)) {
                return `Timing · About ${this.formatActivityDuration(eta)} remaining`;
            }
            const labels = {
                active: 'Estimating remaining time',
                complete: 'Completed',
                failed: 'Stopped',
                cancelled: 'Canceled',
                skipped: 'No runtime needed',
                pending: 'Available when this stage starts',
            };
            return `Timing · ${labels[state] || 'Available when this stage starts'}`;
        },

        activitySteps() {
            return this.displayActivity?.steps || [];
        },

        activityActiveStep() {
            return this.activitySteps().find(step => step.state === 'active') || null;
        },

        activityCommandLabel() {
            const pending = this.activityStartingKind || 'sync';
            const rawTitle = String(
                this.activityStartPending && this.activityStartingKind
                    ? `tweetnook ${pending}`
                    : this.displayActivity?.title || `tweetnook ${pending}`
            );
            const command = rawTitle.replace(/^tweetnook\s+/i, '').trim().toLowerCase();
            const labels = {
                sync: 'Archive Sync',
                enrich: 'Archive Enrichment',
                import: 'Archive Import',
                'import enrich': 'Archive Enrichment',
                'import x-archive': 'Archive Import',
                'threads expand': 'Thread Expansion',
                'articles refresh': 'Article Refresh',
                'media download': 'Media Download',
                unfurl: 'Link Preview Refresh',
                tag: 'Automated Tagging',
                migrate: 'Archive Migration',
            };
            return labels[command] || rawTitle.replace(/^tweetnook\s+/i, '') || 'Activity';
        },

        activityIssues() {
            return Array.isArray(this.displayActivity?.issues) ? this.displayActivity.issues : [];
        },

        activityIssueCount() {
            return this.activityIssues().reduce(
                (total, issue) => total + Math.max(Number(issue.count) || 1, 1),
                0,
            );
        },

        activityRunState() {
            if (this.activity) return 'active';
            if (this.displayActivity?.success === true) return 'complete';
            if (this.displayActivity?.stopped || this.displayActivity?.success === false) {
                return 'failed';
            }
            return 'pending';
        },

        activityRunStateLabel() {
            if (this.activity) return 'In progress';
            if (this.displayActivity?.stopped) return 'Canceled';
            if (this.displayActivity?.success === false) return 'Failed';
            if (this.displayActivity?.success === true) return 'Complete';
            return 'Waiting';
        },

        activityRunStatusTitle() {
            if (this.activity) return 'Sync in progress';
            if (this.displayActivity?.stopped) return 'Last run stopped';
            if (this.displayActivity?.success === false) return 'Last run failed';
            if (this.displayActivity?.success === true) return 'Last run completed';
            return 'Run summary';
        },

        activityRunCounts() {
            const steps = this.activitySteps();
            const complete = steps.filter(step => step.state === 'complete').length;
            const skipped = steps.filter(step => step.state === 'skipped').length;
            const failed = steps.filter(step => step.state === 'failed').length;
            return [
                `${steps.length} stage${steps.length === 1 ? '' : 's'}`,
                complete ? `${complete} completed` : '',
                skipped ? `${skipped} skipped` : '',
                failed ? `${failed} ${this.displayActivity?.stopped ? 'canceled' : 'failed'}` : '',
            ].filter(Boolean).join(' · ');
        },

        activitySummaryMetrics() {
            const steps = this.activitySteps();
            const cards = [];
            const stepFor = key => steps.find(step => step.key === key || step.key.startsWith(`${key}:`));
            const metric = (step, key) => {
                if (!step?.metrics || !Object.prototype.hasOwnProperty.call(step.metrics, key)) {
                    return null;
                }
                const value = Number(step.metrics[key]);
                return Number.isFinite(value) ? Math.max(value, 0) : null;
            };
            const count = value => Math.round(value).toLocaleString();
            const add = (key, label, value, subtitle = '') => {
                const wide = String(label).length > 20
                    || String(value).length > 12
                    || String(subtitle).length > 25;
                cards.push({ key, label, value, subtitle, wide });
            };
            const unavailableSubtitle = step => {
                const state = this.activitySegmentState(step);
                if (state === 'skipped') return 'Stage did not run';
                if (state === 'failed' || state === 'cancelled') return 'Not reported before stop';
                return this.activity ? 'Available after this stage' : 'Not reported';
            };
            const sumMetrics = (matchingSteps, key) => {
                const values = matchingSteps
                    .map(step => metric(step, key))
                    .filter(value => value !== null);
                return values.length ? values.reduce((total, value) => total + value, 0) : null;
            };
            const syncCard = (key, label) => {
                const step = stepFor(key);
                if (!step) return;
                const newTweets = metric(step, 'new_tweets');
                const tweetsSeen = metric(step, 'tweets_seen');
                const pages = metric(step, 'pages');
                const subtitle = newTweets === null
                    ? unavailableSubtitle(step)
                    : [
                        tweetsSeen === null ? '' : `${count(tweetsSeen)} checked`,
                        pages === null ? '' : `${count(pages)} page${pages === 1 ? '' : 's'}`,
                    ].filter(Boolean).join(' · ');
                add(key, label, newTweets === null ? '—' : count(newTweets), subtitle);
            };

            syncCard('sync:bookmarks', 'New bookmarks');
            syncCard('sync:likes', 'New liked tweets');

            const syncSteps = steps.filter(step => step.key?.startsWith('sync:'));
            if (syncSteps.length) {
                const tweetsSeen = sumMetrics(syncSteps, 'tweets_seen');
                const pages = sumMetrics(syncSteps, 'pages');
                add(
                    'tweets-checked',
                    'Tweets checked',
                    tweetsSeen === null ? '—' : count(tweetsSeen),
                    tweetsSeen === null ? 'Available after stages' : 'All collections',
                );
                add(
                    'pages-fetched',
                    'Pages fetched',
                    pages === null ? '—' : count(pages),
                    pages === null ? 'Available after stages' : 'All collections',
                );
            }

            const media = stepFor('media');
            if (media) {
                const downloaded = metric(media, 'downloaded');
                const downloadedBytes = metric(media, 'downloaded_bytes');
                const alreadySaved = metric(media, 'already_saved');
                const failed = metric(media, 'failed');
                add(
                    'media-downloaded',
                    'Media downloaded',
                    downloaded === null ? '—' : count(downloaded),
                    downloaded === null
                        ? unavailableSubtitle(media)
                        : failed ? `${count(failed)} failed` : 'Saved locally',
                );
                add(
                    'media-size',
                    'Media size',
                    downloadedBytes === null ? '—' : this.formatActivityBytes(downloadedBytes),
                    downloadedBytes === null ? unavailableSubtitle(media) : 'Added this run',
                );
                add(
                    'media-saved',
                    'Media already saved',
                    alreadySaved === null ? '—' : count(alreadySaved),
                    alreadySaved === null ? unavailableSubtitle(media) : 'No download needed',
                );
                add(
                    'media-failed',
                    'Media failures',
                    failed === null ? '—' : count(failed),
                    failed === null ? unavailableSubtitle(media) : failed ? 'Review sync issues' : 'No media failures',
                );
            }

            const threads = stepFor('threads');
            if (threads) {
                const expanded = metric(threads, 'expanded');
                const known = metric(threads, 'already_known');
                add(
                    'threads',
                    'Threads expanded',
                    expanded === null ? '—' : count(expanded),
                    expanded === null ? unavailableSubtitle(threads) : 'New context saved',
                );
                add(
                    'threads-known',
                    'Threads already known',
                    known === null ? '—' : count(known),
                    known === null ? unavailableSubtitle(threads) : 'Skipped safely',
                );
                const failed = metric(threads, 'failed');
                add(
                    'threads-failed',
                    'Thread failures',
                    failed === null ? '—' : count(failed),
                    failed === null ? unavailableSubtitle(threads) : failed ? 'Review sync issues' : 'No thread failures',
                );
            }

            const resurrection = stepFor('resurrection');
            if (resurrection) {
                const restored = metric(resurrection, 'restored');
                const unavailable = metric(resurrection, 'still_unavailable');
                const retryLater = metric(resurrection, 'retry_later');
                const remainingDue = metric(resurrection, 'remaining_due');
                add(
                    'restored',
                    'Tweets resurrected',
                    restored === null ? '—' : count(restored),
                    restored === null ? unavailableSubtitle(resurrection) : 'Returned to the archive',
                );
                add(
                    'still-unavailable',
                    'Still unavailable',
                    unavailable === null ? '—' : count(unavailable),
                    unavailable === null
                        ? unavailableSubtitle(resurrection)
                        : [
                            retryLater ? `${count(retryLater)} retry later` : '',
                            remainingDue ? `${count(remainingDue)} still due` : '',
                        ].filter(Boolean).join(' · ') || 'Checked this run',
                );
                add(
                    'recovery-retries',
                    'Recovery retries',
                    retryLater === null ? '—' : count(retryLater),
                    retryLater === null ? unavailableSubtitle(resurrection) : 'Transient failures',
                );
                add(
                    'recovery-queue',
                    'Recovery queue',
                    remainingDue === null ? '—' : count(remainingDue),
                    remainingDue === null ? unavailableSubtitle(resurrection) : 'Still due',
                );
            }

            const urls = stepFor('urls');
            if (urls) {
                const updatedUrls = metric(urls, 'updated');
                const failedUrls = metric(urls, 'failed');
                add(
                    'urls',
                    'Link previews updated',
                    updatedUrls === null ? '—' : count(updatedUrls),
                    updatedUrls === null ? unavailableSubtitle(urls) : 'Metadata updated',
                );
                add(
                    'urls-failed',
                    'Preview failures',
                    failedUrls === null ? '—' : count(failedUrls),
                    failedUrls === null ? unavailableSubtitle(urls) : failedUrls ? 'Review sync issues' : 'No preview failures',
                );
            }

            const articles = stepFor('articles');
            if (articles) {
                const refreshedArticles = metric(articles, 'refreshed')
                    ?? (articles.state === 'skipped' ? 0 : null);
                const failedArticles = metric(articles, 'failed')
                    ?? (articles.state === 'skipped' ? 0 : null);
                add(
                    'articles',
                    'Articles refreshed',
                    refreshedArticles === null ? '—' : count(refreshedArticles),
                    refreshedArticles === null
                        ? unavailableSubtitle(articles)
                        : articles.state === 'skipped' ? 'No refresh needed' : 'Content updated',
                );
                add(
                    'articles-failed',
                    'Article failures',
                    failedArticles === null ? '—' : count(failedArticles),
                    failedArticles === null
                        ? unavailableSubtitle(articles)
                        : failedArticles ? 'Review sync issues' : 'No article failures',
                );
            }

            const tagging = stepFor('tagging');
            if (tagging) {
                const tagged = metric(tagging, 'tagged');
                const requests = metric(tagging, 'requests');
                add(
                    'tagging',
                    'Tweets tagged',
                    tagged === null ? '—' : count(tagged),
                    tagged === null
                        ? unavailableSubtitle(tagging)
                        : requests === null ? '' : `${count(requests)} request${requests === 1 ? '' : 's'}`,
                );
                add(
                    'tagging-requests',
                    'Tagging requests',
                    requests === null ? '—' : count(requests),
                    requests === null ? unavailableSubtitle(tagging) : 'Model requests',
                );
            }

            add(
                'duration',
                'Duration',
                this.formatActivityDuration(this.displayActivity?.elapsed_seconds),
                'Total runtime',
            );
            const issueCount = this.activityIssueCount();
            add('issues', 'Issues', count(issueCount), issueCount ? 'Review below' : 'No issues');
            return cards;
        },

        activityPages() {
            if (!this.displayActivity) return [];
            return [
                ...this.activitySteps(),
                {
                    key: '__summary__',
                    title: 'Summary',
                    state: this.activity ? 'pending' : this.activityRunState(),
                    is_summary: true,
                },
            ];
        },

        activitySelectedSegmentGrow() {
            const pageCount = this.activityPages().length;
            if (pageCount <= 2) return 2;
            return (2 * (pageCount - 1)) / (pageCount - 2);
        },

        activitySelectedPageKey() {
            const pages = this.activityPages();
            if (this.activityPageKey && pages.some(page => page.key === this.activityPageKey)) {
                return this.activityPageKey;
            }
            return this.activityActiveStep()?.key || '__summary__';
        },

        activitySelectedPage() {
            const key = this.activitySelectedPageKey();
            return this.activityPages().find(page => page.key === key) || null;
        },

        activitySelectedPageIndex() {
            return Math.max(
                this.activityPages().findIndex(page => page.key === this.activitySelectedPageKey()),
                0,
            );
        },

        selectActivityPage(key) {
            const pages = this.activityPages();
            const nextIndex = pages.findIndex(page => page.key === key);
            const currentIndex = this.activitySelectedPageIndex();
            if (nextIndex < 0 || nextIndex === currentIndex) return;
            this.activityPageDirection = nextIndex < currentIndex ? 'back' : 'forward';
            this.activityPageKey = key;
        },

        moveActivityPage(offset) {
            const pages = this.activityPages();
            const nextIndex = Math.max(
                0,
                Math.min(this.activitySelectedPageIndex() + offset, pages.length - 1),
            );
            if (pages[nextIndex]) this.selectActivityPage(pages[nextIndex].key);
        },

        openActivityDrawer() {
            this.activityPageKey = null;
            this.activityPageDirection = 'forward';
            this.activityDrawerOpen = true;
            if (activityPollingEnabled) this.fetchActivityStatus();
        },

        toggleActivityDrawer() {
            if (this.activityDrawerOpen) {
                this.activityDrawerOpen = false;
                return;
            }
            this.openActivityDrawer();
        },

        openUsernameProfileCard(event, username) {
            if (!username) return;
            this.openProfileCard(
                event,
                {
                    author: {
                        display_name: username,
                        username,
                        id: 'unknown',
                    },
                },
                false,
                true,
            );
        },

        openProfileCard(event, tweet, isQt = false, useClickedAnchor = false) {
            let anchor = event.currentTarget;
            if (!useClickedAnchor && !anchor.classList.contains('w-10')) {
                // Attempt to find the pfp avatar container which is usually a previous sibling or in the parent flex row
                let container = anchor.closest('.flex.space-x-2') || anchor.closest('.flex.relative');
                if (!container && anchor.parentElement && anchor.parentElement.parentElement) {
                    container = anchor.parentElement.parentElement;
                }
                if (container) {
                    const pfp = container.querySelector('.w-10.h-10.rounded-full');
                    if (pfp) anchor = pfp;
                }
            }

            const rect = anchor.getBoundingClientRect();

            let name, username, id;
            let raw;
            if (isQt) {
                raw = tweet.raw_json || tweet;
                const authorInfo = this.getQuoteAuthor(tweet);
                name = authorInfo.name;
                username = authorInfo.screen_name;
                id = authorInfo.id;
            } else {
                raw = tweet.raw_json || {};
                name = tweet.author?.display_name || 'Unknown';
                username = tweet.author?.username || 'unknown';
                id = tweet.author?.id || 'unknown';
            }
            
            const core = raw.core || {};
            const userResult = core.user_results?.result || {};
            const legacy = userResult.legacy || raw.user || {};
            
            this.profileCard = {
                x: rect.left,
                y: rect.bottom + 8,
                anchorRect: { left: rect.left, top: rect.top, bottom: rect.bottom },
                request: ++this.profileCardRequest,
                name,
                username,
                id,
                initial: name.charAt(0).toUpperCase(),
                description: legacy.description || '',
                followersCount: legacy.followers_count || 0,
                followingCount: legacy.friends_count || 0,
                syncDate: tweet.synced_at || (tweet.collection ? tweet.collection.synced_at : null) || raw.synced_at || null,
                snapshots: [],
                snapshotIndex: 0,
            };
            this.positionProfileCard();
            this.loadProfileSnapshots();

            if (!this._profileMouseMoveHandler) {
                this._profileMouseMoveHandler = (e) => {
                    if (!this.profileCard) return;
                    const card = document.getElementById('profile-card-modal');
                    if (!card) return;
                    const cardRect = card.getBoundingClientRect();
                    const bufferX = 30;
                    const bufferY = 80;
                    if (
                        e.clientX < cardRect.left - bufferX ||
                        e.clientX > cardRect.right + bufferX ||
                        e.clientY < cardRect.top - bufferY ||
                        e.clientY > cardRect.bottom + bufferY
                    ) {
                        this.closeProfileCard();
                    }
                };
            }
            window.addEventListener('mousemove', this._profileMouseMoveHandler);
        },
        
        // The clicked tweet carries the profile as it was when that tweet was
        // fetched. Replace it with the most recently fetched capture of the
        // author, keeping older distinct captures to cycle through.
        async loadProfileSnapshots() {
            const card = this.profileCard;
            if (!card) return;
            const request = card.request;
            let query;
            if (card.id && card.id !== 'unknown') {
                query = `author_id=${encodeURIComponent(card.id)}`;
            } else if (card.username && card.username !== 'unknown') {
                query = `username=${encodeURIComponent(card.username)}`;
            } else {
                return;
            }
            try {
                const res = await fetch(`/api/authors/profile?${query}`);
                if (!res.ok) return;
                const data = await res.json();
                // The card may have closed or opened for someone else meanwhile.
                if (this.profileCard?.request !== request) return;
                if (data.author_id) this.profileCard.id = data.author_id;
                if (!data.snapshots?.length) return;
                this.profileCard.snapshots = data.snapshots;
                this.showProfileSnapshot(0, false);
            } catch (e) {
                console.error('Failed to load profile snapshots:', e);
            }
        },

        // Index 0 is the most recent capture; higher indexes are older. Cycling
        // keeps the card's top fixed so the arrows stay under the pointer.
        showProfileSnapshot(index, keepTop = true) {
            const card = this.profileCard;
            const snapshot = card?.snapshots?.[index];
            if (!snapshot) return;
            card.snapshotIndex = index;
            card.name = snapshot.display_name || card.name;
            card.username = snapshot.username || card.username;
            card.initial = card.name.charAt(0).toUpperCase();
            card.description = snapshot.description || '';
            card.followersCount = snapshot.followers_count || 0;
            card.followingCount = snapshot.following_count || 0;
            card.syncDate = snapshot.fetched_at || card.syncDate;
            this.positionProfileCard(keepTop);
        },

        // Places the card below its anchor, or above it when it would overflow.
        // With keepTop, the card stays put unless it would leave the viewport.
        positionProfileCard(keepTop = false) {
            this.$nextTick(() => {
                const card = document.getElementById('profile-card-modal');
                if (!card || !this.profileCard) return;
                const anchor = this.profileCard.anchorRect;
                const cardRect = card.getBoundingClientRect();
                let x = anchor.left;
                let y = keepTop ? this.profileCard.y : anchor.bottom + 8;
                if (x + cardRect.width > window.innerWidth) {
                    x = window.innerWidth - cardRect.width - 16;
                }
                if (y + cardRect.height > window.innerHeight) {
                    y = keepTop
                        ? Math.max(8, window.innerHeight - cardRect.height - 8)
                        : anchor.top - cardRect.height - 8;
                }
                this.profileCard.x = x;
                this.profileCard.y = y;
            });
        },

        closeProfileCard() {
            this.profileCard = null;
            if (this._profileMouseMoveHandler) {
                window.removeEventListener('mousemove', this._profileMouseMoveHandler);
            }
        },
        
        showSettingsModal: false,
        showSetupModal: false,
        settingsBodyOverflow: null,
        settingsTab: 'appearance',
        globalTags: [],
        globalTagsLoading: false,
        tagSearchTerm: '',
        
        configSchema: null,
        configData: null,
        configOriginal: null,
        configDefaults: null,
        configExplicit: [],
        configSaving: false,
        showAdvancedConfig: false,
        automatedTaggingInstalled: false,
        automatedTagging: null,
        automatedTaggingOriginal: null,
        automatedTaggingContext: '',
        automatedTaggingContextItems: [],
        automatedTaggingContextInput: '',
        automatedTaggingInstructionItems: [],
        automatedTaggingInstructionInput: '',
        automatedTaggingModels: [],
        automatedTaggingModelCache: null,
        automatedTaggingModelCacheKey: '',
        automatedTaggingModelCacheExpiresAt: 0,
        automatedTaggingAccounting: null,
        automatedTaggingSaving: false,
        automatedTaggingLoadingModels: false,
        automatedTaggingSpendAmount: null,
        automatedTaggingSpendPeriod: 'day',
        showAutomatedTaggingSpendModal: false,
        automatedTaggingSpendRange: '30d',
        automatedTaggingError: '',
        automatedTaggingTweetId: '',
        automatedTaggingTweetLookingUp: false,
        automatedTaggingTweetLookupError: '',
        automatedTaggingSelectedTweet: null,
        automatedTaggingTesting: false,
        automatedTaggingTestResult: null,
        automatedTaggingTestError: '',
        scheduleSettings: null,
        scheduleForm: null,
        scheduleOriginal: null,
        scheduleSaving: false,
        scheduleSaved: false,
        scheduleError: '',
        scheduleTimeHour: '3',
        scheduleTimeMinute: '00',
        scheduleTimePeriod: 'AM',
        activityRuns: [],
        activityRunsLoading: false,
        selectedActivityRun: null,
        selectedActivityLog: '',
        setupData: null,
        archiveReady: null,
        archiveResourcesLoaded: false,
        setupForced: false,
        setupRerun: false,
        setupFlowStep: 1,
        setupDirection: 'forward',
        setupSkipAuth: false,
        setupPassword: '',
        setupPasswordConfirm: '',
        setupFinishing: false,
        setupError: '',
        setupPolling: false,
        notices: [],
        setupAuth: null,
        setupAuthSaved: false,
        setupSavingAuth: false,
        setupAuthSuccessPending: false,
        setupTestingAuth: false,
        setupAuthResult: null,
        setupUploading: false,
        setupStartingImport: false,
        
        mergePrimaryTag: '',
        mergeTagsList: [],
        mergeSearchTerm: '',
        mergePrimarySearchTerm: '',
        mergePrimaryDropdown: false,
        mergeTagsDropdown: false,
        mergeSelectedIndex: 0,
        primarySelectedIndex: 0,
        
        lastSyncFormatted: '',
        darkMode: true,
        
        showStatsModal: false,
        statsSummary: null,
        loadingStatsSummary: false,
        statsCollections: [],
        loadingStatsCollections: false,
        statsHealth: null,
        loadingStatsHealth: false,
        showEmptyUnavailableReasons: false,
        hoveredUnavailableReason: null,
        archiveEnrichmentIncomplete: 0,
        statsTags: null,
        loadingStatsTags: false,
        loadingStatsSnapshot: false,
        statsGeneratedAt: null,
        statsRefreshing: false,
        statsRefreshFailed: false,
        statsRefreshPollTimer: null,
        statsRefreshPollAttempts: 0,
        statsAgeNow: Date.now(),

        activityPollTimer: null,

        storageData: null,
        storageLoading: false,
        storageDetailedView: false,
        storageGapsCollapsed: false,
        animatingStorageToggle: false,
        hoveredStorageId: null,
        expandedStorageId: null,
        
        THEMES: THEMES,
        currentTheme: 'classic-dark',
        currentAccent: null,

        fontFamily: 'system',
        installedFonts: [],
        fontDiscoveryBusy: false,
        fontDiscoveryMessage: '',
        fontPickerOpen: false,
        fontSize: 'default',
        videoPlayback: 'autoplay', // 'autoplay' | 'paused'
        // Shared by every player in autoplay mode; each page load starts muted.
        videoMuted: true,

        FONT_OPTIONS: [
            {key: 'system', label: 'System Default', family: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif'},
            {key: 'serif', label: 'System Serif', family: 'serif'},
            {key: 'mono', label: 'System Monospace', family: 'monospace'},
            {key: 'inter', label: 'Inter', family: '"Inter", sans-serif'},
            {key: 'roboto', label: 'Roboto', family: '"Roboto", sans-serif'},
            {key: 'jakarta', label: 'Plus Jakarta Sans', family: '"Plus Jakarta Sans", sans-serif'},
            {key: 'outfit', label: 'Outfit', family: '"Outfit", sans-serif'},
            {key: 'nunito', label: 'Nunito', family: '"Nunito", sans-serif'},
            {key: 'jetbrains', label: 'JetBrains Mono', family: '"JetBrains Mono", monospace'}
        ],

        initApp() {
            if ('scrollRestoration' in history) {
                history.scrollRestoration = 'manual';
            }

            const savedTheme = localStorage.getItem('tvx-theme') || localStorage.getItem('theme');
            if (savedTheme && THEMES[savedTheme]) {
                this.applyTheme(savedTheme);
            } else if (savedTheme === 'light') {
                this.applyTheme('classic-light');
            }
            
            const savedFont = localStorage.getItem('tvx-font');
            const savedFontFamily = localStorage.getItem('tvx-font-family');
            if (savedFont && savedFontFamily) {
                this.fontFamily = savedFont;
                document.body.style.fontFamily = savedFontFamily;
            }

            window.addEventListener('scroll', () => {
                this.showScrollTop = window.scrollY > 300;
                this.placeListMenu();
            });
            
            const savedFontSize = localStorage.getItem('tvx-font-size');
            const savedFontSizePx = localStorage.getItem('tvx-font-size-px');
            if (savedFontSize && savedFontSizePx) {
                this.fontSize = savedFontSize;
                document.documentElement.style.setProperty('--font-size-base', savedFontSizePx);
            }
            
            this.videoPlayback = localStorage.getItem('tvx-video-playback') === 'paused' ? 'paused' : 'autoplay';

            // Restore split panel preference
            this.splitPanel = localStorage.getItem('tvx-split-panel') === 'true';
            this.routeUsesPanel = this.shouldUseSplitPanel();

            const initialRoute = this.parseRoute(window.location.pathname);
            if (initialRoute) {
                const initialState = this.historyStateForRoute(initialRoute, {
                    depth: 0,
                    scrollY: initialRoute.viewMode === 'list' ? window.scrollY : undefined,
                });
                this.activeRoute = initialRoute;
                this.pendingRoute = initialRoute;
                this.pendingRouteState = initialState;
                history.replaceState(initialState, '', this.routeUrl(initialRoute));
            }

            window.addEventListener('popstate', (event) => {
                this.pauseAllVideos();
                const route = this.routeFromHistoryState(event.state)
                    || this.parseRoute(window.location.pathname);
                if (route) return this.applyRoute(route, { fromPopState: true, state: event.state });
            });
            window.addEventListener('resize', () => {
                this.placeListMenu();
                const usePanel = this.shouldUseSplitPanel();
                if (usePanel === this.routeUsesPanel) return;
                this.routeUsesPanel = usePanel;
                if (this.activeRoute.viewMode !== 'list' && this.archiveResourcesLoaded) {
                    this.applyRoute(this.activeRoute, { state: history.state });
                }
            });

            this.fetchNotices();
            if (this.isDemo) {
                this.setupData = {
                    completed: true,
                    required: false,
                    auth: {configured: false, verified: false},
                    archive: {ready: true, enrichment_state: 'complete', pending_enrichment: 0},
                    web: {password_configured: true},
                };
                this.archiveReady = true;
                this.setupForced = false;
                this.archiveResourcesLoaded = true;
                Promise.all([
                    this.fetchTweets(),
                    this.fetchStats(),
                    this.fetchArchiveEnrichmentStatus(),
                    this.fetchAutomatedTagging(),
                ]).then(() => {
                    if (this.pendingRoute) {
                        const route = this.pendingRoute;
                        const state = this.pendingRouteState;
                        this.pendingRoute = null;
                        this.pendingRouteState = null;
                        this.applyRoute(route, {state});
                    }
                });
            } else {
                // Ask for the first page and any linked conversation alongside setup
                // status instead of after it; fetchTweets() reuses the request.
                this.requestFeedPage(`${this.feedUrl(1)}&cursor=`, { speculative: true });
                if (initialRoute?.viewMode === 'thread') this.prefetchThread(initialRoute.tweetId);
                this.fetchSetup();
            }
            // Start loading a conversation on press or a short hover, so it is often
            // ready by the time the click opens it.
            let hoverTimer = null;
            const threadTarget = event => event.target?.closest?.('[data-thread-id]')?.dataset.threadId;
            document.addEventListener('pointerdown', event => this.prefetchThread(threadTarget(event)), { passive: true });
            document.addEventListener('mouseover', event => {
                clearTimeout(hoverTimer);
                const tweetId = threadTarget(event);
                if (tweetId) hoverTimer = setTimeout(() => this.prefetchThread(tweetId), THREAD_HOVER_PREFETCH_MS);
            }, { passive: true });
            // Background polling pauses while the tab is hidden and catches up on return.
            const pollStatus = () => {
                if (!this.isDemo) this.fetchSetup();
                this.fetchNotices();
            };
            document.addEventListener('visibilitychange', () => {
                if (!document.hidden) pollStatus();
            });
            setInterval(() => {
                if (!document.hidden) pollStatus();
            }, 3000);
            setInterval(() => {
                if (!document.hidden && this.archiveResourcesLoaded) this.fetchArchiveEnrichmentStatus();
            }, 60000);
            setInterval(() => { this.statsAgeNow = Date.now(); }, 30000);
            this.startActivityPolling();

            this.$watch('showStatsModal', val => {
                if (val) this.lockModalScroll();
                else if (!this.showSettingsModal && !this.showSetupModal && !this.lightboxOpen) this.unlockModalScroll();
            });
            this.$watch('showSettingsModal', val => {
                if (val) {
                    this.lockModalScroll();
                    if (!this.isDemo) this.fetchSetup();
                    if (!this.setupForced) {
                        if (!this.isDemo) this.fetchConfig();
                        if (this.archiveReady) this.fetchScheduleSettings();
                        if (!this.isDemo) this.fetchActivityRuns();
                        this.fetchAutomatedTagging();
                        if (this.settingsTab === 'tags') this.fetchGlobalTags();
                    }
                } else {
                    this.showAutomatedTaggingSpendModal = false;
                    if (!this.showSetupModal && !this.lightboxOpen) this.unlockModalScroll();
                    this.autoplayNextVideo();
                }
            });
            // The full tag list is only shown in Tag Management; load it there.
            this.$watch('settingsTab', tab => {
                if (tab === 'tags' && this.showSettingsModal) this.fetchGlobalTags();
            });
            this.$watch('showSetupModal', val => {
                if (val) this.lockModalScroll();
                else if (!this.showSettingsModal && !this.lightboxOpen) this.unlockModalScroll();
            });
            this.$watch('lightboxOpen', val => {
                // The mobile viewer blocks pan gestures itself. Changing body
                // overflow makes Safari stop painting beneath its floating bar.
                if (val) { if (!this.isMobileVideoViewer()) this.lockModalScroll(); }
                else if (!this.showStatsModal && !this.showSettingsModal && !this.showSetupModal) this.unlockModalScroll();
            });

            window.addEventListener('open-lightbox', (e) => {
                this.openLightbox(e.detail.media, e.detail.index, e.detail.originVideo);
            });
            
            this.videoObserver = new IntersectionObserver((entries) => {
                entries.forEach(e => {
                    if (e.target.tagName === 'VIDEO') {
                        // The lightbox owns playback; observer callbacks arriving
                        // after dismissal must never restart its hidden player.
                        if (e.target.closest?.('.lightbox-stage')) {
                            if (!this.lightboxOpen) e.target.pause();
                            return;
                        }
                        if (!e.isIntersecting) {
                            e.target.pause();
                        } else if (e.target.hasAttribute('autoplay')) {
                            e.target.play().catch(() => {});
                        }
                    }
                });
            }, { threshold: 0.1 });

            this.videoAutoplayObserver = new IntersectionObserver((entries) => {
                entries.forEach(e => this.updateAutoplayVisibility(
                    e.target,
                    e.isIntersecting && e.intersectionRatio >= VIDEO_AUTOPLAY_VISIBLE_RATIO,
                ));
            }, { threshold: VIDEO_AUTOPLAY_VISIBLE_RATIO });
            // Media events do not bubble, so players are followed in the capture phase.
            document.addEventListener('play', event => this.handleVideoPlay(event.target), true);
            document.addEventListener('pause', event => this.handleVideoPause(event.target), true);
            document.addEventListener('volumechange', event => this.handleVideoVolumeChange(event.target), true);

            this.mediaPrefetchObserver = new IntersectionObserver((entries) => {
                entries.forEach(e => {
                    if (!e.isIntersecting) return;
                    this.mediaPrefetchObserver.unobserve(e.target);
                    this.prefetchMedia(e.target);
                });
            }, { rootMargin: `${MEDIA_PREFETCH_MARGIN_PX}px 0px` });

            const domObserver = new MutationObserver((mutations) => {
                mutations.forEach(m => {
                    m.addedNodes.forEach(node => {
                        if (node.nodeType === 1) {
                            if (node.tagName === 'VIDEO') this.observeVideo(node);
                            node.querySelectorAll('video').forEach(v => this.observeVideo(v));
                            const player = node.closest?.('.media-video-player');
                            const video = player?.querySelector('video');
                            if (video && isVideoPlayer(video)) tweetNookInitVideoPlayer(video);
                            this.observeMediaPrefetch(node);
                        }
                    });
                });
            });
            domObserver.observe(document.body, { childList: true, subtree: true });
            
        },

        renderVideoControls,

        // Off-screen feed cards skip layout (content-visibility), so the images inside
        // have no box to observe; watch the card instead and load all of its images.
        observeMediaPrefetch(node) {
            const images = node.tagName === 'IMG' ? [node] : node.querySelectorAll('img[loading="lazy"]');
            images.forEach(image => {
                if (image.loading !== 'lazy') return;
                this.mediaPrefetchObserver.observe(image.closest('.feed-card') || image);
            });
        },

        // Load and decode ahead of time, so the image paints with its card instead
        // of a frame or more after it.
        prefetchMedia(target) {
            const images = target.tagName === 'IMG' ? [target] : target.querySelectorAll('img[loading="lazy"]');
            images.forEach(image => {
                image.loading = 'eager';
                image.decode?.().catch(() => {});
            });
        },

        observeVideo(video) {
            if (isVideoPlayer(video)) tweetNookInitVideoPlayer(video);
            this.videoObserver.observe(video);
            if (!isFeedVideoPlayer(video)) return;
            if (this.videoPlayback === 'autoplay') video.muted = this.videoMuted;
            this.videoAutoplayObserver.observe(video);
        },

        setVideoPlayback(mode) {
            this.videoPlayback = mode === 'autoplay' ? 'autoplay' : 'paused';
            localStorage.setItem('tvx-video-playback', this.videoPlayback);
            const autoplay = this.videoPlayback === 'autoplay';
            this.videoMuted = true;
            document.querySelectorAll(VIDEO_PLAYER_SELECTOR).forEach(video => {
                video.muted = autoplay;
                if (!autoplay && isFeedVideoPlayer(video)) video.pause();
            });
            this.autoplayNextVideo();
        },

        updateAutoplayVisibility(video, visible) {
            if (!isFeedVideoPlayer(video)) return;
            if (visible) {
                autoplayVideos.add(video);
            } else {
                autoplayVideos.delete(video);
                heldAutoplayVideos.delete(video);
                if (this.videoPlayback === 'autoplay' && !video.paused) video.pause();
            }
            this.autoplayNextVideo();
        },

        // Starts the topmost visible player unless one is already playing, the
        // viewer paused it, or an overlay covers the feed.
        autoplayNextVideo() {
            if (this.videoPlayback !== 'autoplay') return;
            if (this.lightboxOpen || this.showSettingsModal || this.showStatsModal) return;
            autoplayVideos.forEach(video => {
                if (!video.isConnected) autoplayVideos.delete(video);
            });
            const players = [...autoplayVideos];
            if (players.some(video => !video.paused)) return;
            const next = players
                .filter(video => !heldAutoplayVideos.has(video))
                .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top)[0];
            if (!next) return;
            next.muted = this.videoMuted;
            next.play()?.catch?.(() => {
                if (next.muted) return;
                // Browsers can refuse audible autoplay before the page has had a gesture.
                forcedMuteVideos.add(next);
                next.muted = true;
                next.play()?.catch?.(() => {});
            });
        },

        // Only one player runs at a time, however it was started.
        handleVideoPlay(video) {
            if (!isVideoPlayer(video)) return;
            document.querySelectorAll(VIDEO_PLAYER_SELECTOR).forEach(other => {
                if (other !== video && !other.paused) other.pause();
            });
            if (this.videoPlayback !== 'autoplay') return;
            heldAutoplayVideos.delete(video);
            if (!forcedMuteVideos.has(video)) video.muted = this.videoMuted;
        },

        handleVideoPause(video) {
            if (this.videoPlayback === 'autoplay' && autoplayVideos.has(video)) heldAutoplayVideos.add(video);
        },

        handleVideoVolumeChange(video) {
            if (this.videoPlayback !== 'autoplay' || !isVideoPlayer(video)) return;
            if (forcedMuteVideos.has(video)) {
                if (video.muted) return;
                forcedMuteVideos.delete(video);
            }
            if (video.muted === this.videoMuted) return;
            this.videoMuted = video.muted;
            document.querySelectorAll(VIDEO_PLAYER_SELECTOR).forEach(other => {
                other.muted = this.videoMuted;
            });
        },

        pauseAllVideos() {
            document.querySelectorAll('video').forEach(v => {
                v.pause();
                v.currentTime = 0;
            });
        },

        lightboxMotionDuration(duration) {
            return window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches ? 0 : duration;
        },

        queueLightboxMotion(callback, delay) {
            clearTimeout(lightboxMotionTimer);
            lightboxMotionTimer = setTimeout(callback, this.lightboxMotionDuration(delay));
            lightboxMotionTimer?.unref?.();
        },

        resetLightboxMotion() {
            lightboxVideoReadyCancel?.();
            lightboxVideoReadyCancel = null;
            lightboxVideoMotion?.cancel();
            lightboxVideoMotion = null;
            this.lightboxVideoMotionPhase = '';
            clearTimeout(lightboxMotionTimer);
            lightboxMotionTimer = null;
            this.lightboxGestureStart = null;
            this.lightboxGestureAxis = null;
            this.lightboxGestureOffsetX = 0;
            this.lightboxGestureOffsetY = 0;
            this.lightboxGestureTransition = 'none';
            this.lightboxGestureAnimating = false;
            this.lightboxDismissPending = false;
        },

        openLightbox(media, index = 0, originVideo = null) {
            if (this.lightboxOpen && lightboxOriginVideo) return;
            this.resetLightboxMotion();
            this.lightboxMedia = Array.isArray(media) ? media : [];
            const lastIndex = Math.max(0, this.lightboxMedia.length - 1);
            this.lightboxIndex = Math.min(lastIndex, Math.max(0, Number(index) || 0));
            lightboxOriginVideo = originVideo;
            if (originVideo && this.lightboxMedia[this.lightboxIndex]) {
                const inlinePlayer = this.isMobileVideoViewer() && Boolean(tweetNookVideoPlayer(originVideo)?.cloneNode);
                this.lightboxMedia = this.lightboxMedia.map((item, itemIndex) => itemIndex === this.lightboxIndex
                    ? { ...item, currentTime: originVideo.currentTime, muted: originVideo.muted, volume: originVideo.volume, loop: originVideo.loop, inlinePlayer }
                    : item);
                if (!inlinePlayer) originVideo.pause();
            }
            this.lightboxOpen = this.lightboxMedia.length > 0;
            const animate = originVideo && this.isMobileVideoViewer() && this.lightboxMotionDuration(300) && originVideo.getBoundingClientRect;
            if (animate) this.lightboxVideoMotionPhase = 'preparing';
            // Reparent before play(), within the original tap's user activation.
            // iOS must not be asked to unlock an audible player in a later tick.
            this.mountInlineLightboxVideo();
            // Alpine's overlay enter lifecycle otherwise hides the live surface
            // for two animation frames. Install the first frame in the tap itself.
            const inlineMotion = animate && lightboxInlineMount && this.$refs.lightboxViewer
                && this.startLightboxVideoMotion(true);
            if (this.lightboxMedia[this.lightboxIndex]?.inlinePlayer) {
                tweetNookShowVideoUi(originVideo);
                originVideo.play()?.catch?.(() => {});
            }
            if (this.lightboxOpen) this.$nextTick(() => {
                if (!this.lightboxOpen) return;
                this.mountInlineLightboxVideo();
                this.syncLightboxPlayback();
                if (animate && !inlineMotion) this.prepareLightboxVideoMotion();
            });
        },

        closeLightbox() {
            if (this.lightboxVideoMotionPhase === 'closing') return;
            videoTapBlockedUntil = Date.now() + 600;
            const preparing = this.lightboxVideoMotionPhase === 'preparing';
            const active = this.activeLightboxVideo();
            if (active) tweetNookFinishVideoScrub(active);
            const animate = !preparing && lightboxOriginVideo && this.isMobileVideoViewer() && active?.parentElement?.animate;
            const backdrop = this.$refs.lightboxViewer;
            const startBounds = animate ? {
                ...(lightboxVideoMotion?.bounds() || {...fittedVideoRect(active), radius: 0}),
                backdropOpacity: backdrop ? videoBackdropOpacity(backdrop) : Number(this.lightboxBackdropStyle()['--lightbox-backdrop-opacity']),
            } : null;
            this.resetLightboxMotion();
            if (lightboxInlineMount && this.$refs.inlineVideoSlot) {
                // The reactive drag transform updates later. Neutralize it now
                // before measuring the destination for the return animation.
                this.$refs.inlineVideoSlot.style.transform = 'translate3d(0, 0, 0)';
                this.$refs.inlineVideoSlot.style.transition = 'none';
            }
            if (animate && this.lightboxOpen && this.startLightboxVideoMotion(false, startBounds)) return;
            this.finishCloseLightbox();
        },

        activeLightboxVideo() {
            return this.$refs.lightboxStage?.querySelector?.('.lightbox-slide.is-active video[data-video-player]');
        },

        mountInlineLightboxVideo() {
            if (!this.lightboxMedia[this.lightboxIndex]?.inlinePlayer || lightboxInlineMount) return;
            const slot = this.$refs.inlineVideoSlot || this.$refs.lightboxStage?.querySelector?.('.lightbox-slide.is-active [data-inline-video-slot]');
            const player = tweetNookVideoPlayer(lightboxOriginVideo);
            if (!slot || !player) return;
            const placeholder = player.cloneNode(false);
            const frame = player.getBoundingClientRect();
            placeholder.style.setProperty('aspect-ratio', `${frame.width} / ${frame.height}`);
            placeholder.setAttribute('aria-hidden', 'true');
            placeholder.removeAttribute('tabindex');
            player.parentElement.insertBefore(placeholder, player);
            // Only the picture travels between frames; controls stay anchored to
            // the fullscreen player instead of shrinking/clipping with the video.
            const surface = document.createElement('div');
            surface.setAttribute('class', 'video-motion-surface');
            player.insertBefore(surface, lightboxOriginVideo);
            surface.appendChild(lightboxOriginVideo);
            lightboxInlineMount = { player, placeholder, surface };
            player.classList.add('lightbox-video-player');
            slot.appendChild(player);
            if (this.$refs.lightboxViewer) {
                // Display/class changes must land before measuring the motion's
                // destination, rather than waiting on x-show's enter frames.
                const viewer = this.$refs.lightboxViewer;
                viewer.classList.remove('is-video-closed');
                viewer.classList.add('is-video-viewer');
                if (this.lightboxVideoMotionPhase) viewer.classList.add('is-video-motion');
                viewer.style.display = 'flex';
                slot.style.display = '';
                slot.classList.add('is-active');
            }
        },

        restoreInlineLightboxVideo() {
            if (!lightboxInlineMount) return;
            const {player, placeholder, surface} = lightboxInlineMount;
            surface.replaceWith(surface.querySelector('video'));
            player.classList.remove('lightbox-video-player', 'is-video-fill');
            placeholder.replaceWith(player);
            lightboxInlineMount = null;
        },

        isMobileVideoViewer() {
            const media = this.lightboxMedia[this.lightboxIndex];
            return media?.type === 'video' && !media.isGif
                && Boolean(window.matchMedia?.('(max-width: 640px), (pointer: coarse)')?.matches);
        },

        prepareLightboxVideoMotion() {
            const video = this.activeLightboxVideo();
            if (!video?.parentElement?.animate) { this.lightboxVideoMotionPhase = ''; return; }
            // The portaled video already has its feed picture/poster. Expand it
            // immediately, including when a first tap is still loading metadata.
            if (lightboxInlineMount) {
                if (!this.startLightboxVideoMotion(true)) this.lightboxVideoMotionPhase = '';
                return;
            }
            // Wait for the restored playback frame so the expanding surface never
            // replaces the feed picture with a black, newly loaded video.
            let timeout;
            const cleanup = () => {
                clearTimeout(timeout);
                video.removeEventListener('loadeddata', ready);
                video.removeEventListener('seeked', ready);
            };
            const begin = () => {
                cleanup();
                lightboxVideoReadyCancel = null;
                if (!this.startLightboxVideoMotion(true)) this.lightboxVideoMotionPhase = '';
            };
            const ready = () => { if (video.readyState >= 2 && !video.seeking) begin(); };
            lightboxVideoReadyCancel = cleanup;
            video.addEventListener('loadeddata', ready);
            video.addEventListener('seeked', ready);
            timeout = setTimeout(begin, 500);
            ready();
        },

        startLightboxVideoMotion(opening, startBounds = null) {
            const motion = animateMobileVideoBounds(this.activeLightboxVideo(), lightboxInlineMount?.placeholder || lightboxOriginVideo, opening,
                this.lightboxMotionDuration(opening ? 300 : 280), startBounds);
            if (!motion) return false;
            lightboxVideoMotion = motion;
            this.lightboxVideoMotionPhase = opening ? 'opening' : 'closing';
            motion.finished.then(completed => {
                if (lightboxVideoMotion !== motion) return;
                if (completed && !opening) { this.finishCloseLightbox(); return; }
                motion.cancel();
                lightboxVideoMotion = null;
                this.lightboxVideoMotionPhase = '';
                this.$refs.lightboxViewer?.classList.remove('is-video-motion');
                if (completed && opening) {
                    tweetNookShowVideoUi(this.activeLightboxVideo());
                }
            });
            return true;
        },

        finishCloseLightbox() {
            const active = this.$refs.lightboxStage?.querySelector?.('.lightbox-slide.is-active video[data-video-player]');
            const origin = lightboxOriginVideo;
            const resume = Boolean(origin && active && !active.paused && !active.ended);
            if (origin && active && origin !== active) {
                origin.currentTime = active.currentTime;
                origin.muted = active.muted;
                origin.volume = active.volume;
            }
            if (origin && !resume) heldAutoplayVideos.add(origin);
            this.lightboxOpen = false;
            videoTapBlockedUntil = Date.now() + 500;
            if (lightboxInlineMount && this.$refs.lightboxViewer) {
                // Hide the viewer and restore the inline surface in one handoff;
                // never expose a fullscreen frame while Alpine schedules leave.
                this.$refs.lightboxViewer.classList.add('is-video-closed');
                this.$refs.lightboxViewer.classList.remove('is-video-controls-hidden');
                this.$refs.lightboxViewer.style.display = 'none';
            }
            this.restoreInlineLightboxVideo();
            lightboxOriginVideo = null;
            this.pauseLightboxPlayback();
            this.resetLightboxMotion();
            if (resume && origin.isConnected && origin.paused) origin.play()?.catch?.(() => {});
            this.autoplayNextVideo();
        },

        pauseLightboxPlayback() {
            this.$refs.lightboxStage?.querySelectorAll?.('video').forEach(video => video.pause());
        },

        syncLightboxPlayback() {
            const stage = this.$refs.lightboxStage;
            if (!stage) return;
            if (!this.lightboxOpen) { this.pauseLightboxPlayback(); return; }
            if (this.lightboxVideoMotionPhase === 'closing') return;
            stage.querySelectorAll('video').forEach(video => {
                const active = video.closest('.lightbox-slide')?.classList.contains('is-active');
                if (!active) video.pause();
                else {
                    const media = this.lightboxMedia[Number(video.dataset?.lightboxIndex)];
                    if (media && !media.inlinePlayer && Number.isFinite(media.currentTime) && !video.hasAttribute('data-playback-restored')) {
                        video.setAttribute('data-playback-restored', '');
                        video.muted = media.muted;
                        video.volume = media.volume;
                        const restore = () => {
                            video.currentTime = media.currentTime;
                        };
                        if (video.readyState >= 1) restore();
                        else video.addEventListener('loadedmetadata', restore, { once: true });
                    }
                    if ((video.hasAttribute('autoplay') || isVideoPlayer(video)) && video.paused !== false) video.play().catch(() => {});
                }
            });
        },

        lightboxVisibleIndices() {
            if (this.lightboxMedia[this.lightboxIndex]?.inlinePlayer) return [];
            const first = Math.max(0, this.lightboxIndex - 1);
            const last = Math.min(this.lightboxMedia.length - 1, this.lightboxIndex + 1);
            const indices = [];
            for (let index = first; index <= last; index += 1) indices.push(index);
            return indices;
        },

        lightboxSlideStyle(mediaIndex) {
            const position = (mediaIndex - this.lightboxIndex) * 100;
            const verticalProgress = Math.max(0, this.lightboxGestureOffsetY)
                / Math.max(1, window.innerHeight || 800);
            const scale = this.lightboxGestureAxis === 'vertical'
                ? Math.max(0.9, 1 - verticalProgress * 0.13)
                : 1;
            return {
                transform: `translate3d(calc(${position}% + ${this.lightboxGestureOffsetX}px), ${this.lightboxGestureOffsetY}px, 0) scale(${scale})`,
                transition: this.lightboxGestureTransition,
                zIndex: mediaIndex === this.lightboxIndex ? 2 : 1,
                pointerEvents: mediaIndex === this.lightboxIndex && !this.lightboxGestureAnimating ? 'auto' : 'none',
            };
        },

        lightboxBackdropStyle() {
            const height = Math.max(1, window.innerHeight || 800);
            const progress = Math.min(1, Math.max(0, this.lightboxGestureOffsetY) / (height * 0.7));
            const opacity = this.lightboxVideoMotionPhase === 'preparing' || this.lightboxDismissPending ? 0
                : (this.isMobileVideoViewer() ? 1 : 0.95) * (1 - progress * 0.72);
            const transitionDuration = this.lightboxDismissPending
                ? this.lightboxMotionDuration(180)
                : (this.lightboxGestureAnimating ? this.lightboxMotionDuration(260) : 0);
            return {
                backgroundColor: `rgba(0, 0, 0, ${opacity})`,
                '--lightbox-backdrop-opacity': String(opacity),
                transition: transitionDuration ? `background-color ${transitionDuration}ms linear` : 'none',
            };
        },

        lightboxChromeStyle() {
            if (this.lightboxGestureAnimating || this.lightboxVideoMotionPhase) return { opacity: '0' };
            const distance = Math.hypot(this.lightboxGestureOffsetX, this.lightboxGestureOffsetY);
            return { opacity: String(Math.max(0, 1 - distance / 180)) };
        },

        prevLightbox() {
            if (this.lightboxOpen && this.lightboxGestureAnimating) return;
            if (this.lightboxMedia.length < 2 || this.lightboxIndex <= 0) return;
            if (this.lightboxOpen) this.commitLightboxNavigation(-1);
            else this.lightboxIndex -= 1;
        },

        nextLightbox() {
            if (this.lightboxOpen && this.lightboxGestureAnimating) return;
            if (this.lightboxMedia.length < 2 || this.lightboxIndex >= this.lightboxMedia.length - 1) return;
            if (this.lightboxOpen) this.commitLightboxNavigation(1);
            else this.lightboxIndex += 1;
        },

        startLightboxGesture(event) {
            if (!this.lightboxOpen || this.lightboxGestureAnimating || this.lightboxVideoMotionPhase) return;
            this.lightboxSuppressClickUntil = 0;
            if (event.touches?.length > 1) {
                this.cancelLightboxGesture();
                return;
            }

            const target = event.target;
            if (target?.closest?.('button, .video-control-bar, video[controls], audio, input, textarea, select, a')) return;

            const touch = event.touches?.[0] || event.changedTouches?.[0];
            if (!touch) return;

            this.lightboxGestureStart = {
                x: touch.clientX,
                y: touch.clientY,
                lastX: touch.clientX,
                lastY: touch.clientY,
                lastTime: Number(event.timeStamp) || Date.now(),
                velocityX: 0,
                velocityY: 0,
                moved: false,
                closesOnClick: !target?.closest?.('img, video, button'),
            };
            this.lightboxGestureAxis = null;
            this.lightboxGestureOffsetX = 0;
            this.lightboxGestureOffsetY = 0;
            this.lightboxGestureTransition = 'none';
        },

        updateLightboxGesture(touch, timeStamp, trackVelocity = true) {
            const start = this.lightboxGestureStart;
            if (!start || !touch) return null;

            const deltaX = touch.clientX - start.x;
            const deltaY = touch.clientY - start.y;
            const absX = Math.abs(deltaX);
            const absY = Math.abs(deltaY);
            if (!this.lightboxGestureAxis && Math.max(absX, absY) >= 8) {
                if (absX > absY * 1.08 && this.lightboxMedia.length > 1) {
                    this.lightboxGestureAxis = 'horizontal';
                } else if (absY > absX * 1.08) {
                    this.lightboxGestureAxis = 'vertical';
                }
            }

            const now = Number(timeStamp) || Date.now();
            if (trackVelocity) {
                const elapsed = Math.max(8, now - start.lastTime);
                const instantX = (touch.clientX - start.lastX) / elapsed;
                const instantY = (touch.clientY - start.lastY) / elapsed;
                start.velocityX = start.moved ? start.velocityX * 0.35 + instantX * 0.65 : instantX;
                start.velocityY = start.moved ? start.velocityY * 0.35 + instantY * 0.65 : instantY;
                start.lastX = touch.clientX;
                start.lastY = touch.clientY;
                start.lastTime = now;
                start.moved = true;
            }

            if (this.lightboxGestureAxis === 'horizontal') {
                const movingPastStart = this.lightboxIndex === 0 && deltaX > 0;
                const movingPastEnd = this.lightboxIndex === this.lightboxMedia.length - 1 && deltaX < 0;
                this.lightboxGestureOffsetX = deltaX * (movingPastStart || movingPastEnd ? 0.22 : 1);
                this.lightboxGestureOffsetY = 0;
            } else if (this.lightboxGestureAxis === 'vertical') {
                this.lightboxGestureOffsetX = 0;
                this.lightboxGestureOffsetY = deltaY >= 0 ? deltaY : deltaY * 0.16;
            }

            return { deltaX, deltaY, absX, absY };
        },

        moveLightboxGesture(event) {
            if (this.lightboxOpen && this.isMobileVideoViewer() && event.touches?.length === 1) event.preventDefault?.();
            if (!this.lightboxGestureStart) return;
            if (event.touches?.length !== 1) {
                this.cancelLightboxGesture();
                return;
            }
            const movement = this.updateLightboxGesture(event.touches[0], event.timeStamp, true);
            if (movement && this.lightboxGestureAxis) event.preventDefault?.();
        },

        endLightboxGesture(event) {
            const start = this.lightboxGestureStart;
            if (!start || !this.lightboxOpen) return;

            const touch = event.changedTouches?.[0];
            if (!touch) {
                this.cancelLightboxGesture();
                return;
            }

            const movement = this.updateLightboxGesture(touch, event.timeStamp, start.moved);
            this.lightboxGestureStart = null;
            if (!movement) return;

            const { deltaX, deltaY } = movement;
            const distance = Math.hypot(deltaX, deltaY);
            if (distance >= 8) {
                videoTapBlockedUntil = Date.now() + 600;
                this.lightboxSuppressClickUntil = Date.now() + 500;
            }

            if (this.lightboxGestureAxis === 'horizontal') {
                const direction = deltaX < 0 ? 1 : -1;
                const canNavigate = direction > 0
                    ? this.lightboxIndex < this.lightboxMedia.length - 1
                    : this.lightboxIndex > 0;
                const width = Math.max(1, window.innerWidth || 320);
                const projectedDistance = Math.abs(this.lightboxGestureOffsetX + start.velocityX * 150);
                const threshold = Math.min(68, Math.max(50, width * 0.16));
                if (canNavigate && (projectedDistance >= threshold
                    || (Math.abs(start.velocityX) >= 0.45 && Math.abs(deltaX) >= 24))) {
                    this.commitLightboxNavigation(direction);
                    return;
                }
            } else if (this.lightboxGestureAxis === 'vertical') {
                const height = Math.max(1, window.innerHeight || 800);
                const threshold = Math.min(84, Math.max(68, height * 0.1));
                if (deltaY > 0 && (this.lightboxGestureOffsetY >= threshold
                    || (start.velocityY >= 0.5 && deltaY >= 30))) {
                    this.animateLightboxDismiss();
                    return;
                }
            }

            this.snapBackLightbox();
        },

        commitLightboxNavigation(direction) {
            const duration = this.lightboxMotionDuration(220);
            const width = Math.max(1, window.innerWidth || 320);
            const retainedOffset = this.lightboxGestureOffsetX + direction * width;
            this.lightboxGestureAnimating = true;
            this.lightboxGestureAxis = 'horizontal';
            this.lightboxGestureTransition = 'none';
            this.lightboxIndex += direction;
            this.lightboxGestureOffsetX = retainedOffset;
            this.$nextTick(() => {
                this.lightboxGestureTransition = duration
                    ? `transform ${duration}ms cubic-bezier(0.22, 0.72, 0, 1)`
                    : 'none';
                this.lightboxGestureOffsetX = 0;
                this.syncLightboxPlayback();
            });
            this.queueLightboxMotion(() => {
                this.lightboxGestureTransition = 'none';
                this.lightboxGestureAxis = null;
                this.lightboxGestureAnimating = false;
            }, 220);
        },

        animateLightboxDismiss() {
            if (lightboxOriginVideo && this.isMobileVideoViewer()) { this.closeLightbox(); return; }
            const duration = this.lightboxMotionDuration(180);
            this.lightboxGestureAnimating = true;
            this.lightboxDismissPending = true;
            this.lightboxGestureTransition = duration
                ? `transform ${duration}ms cubic-bezier(0.4, 0, 1, 1)`
                : 'none';
            this.lightboxGestureOffsetY = Math.max(1, window.innerHeight || 800) * 1.05;
            this.queueLightboxMotion(() => this.finishLightboxDismiss(), 180);
        },

        finishLightboxDismiss() {
            clearTimeout(lightboxMotionTimer);
            lightboxMotionTimer = null;
            this.pauseLightboxPlayback();
            this.lightboxOpen = false;
            this.queueLightboxMotion(() => this.resetLightboxMotion(), 180);
        },

        snapBackLightbox() {
            const duration = this.lightboxMotionDuration(260);
            this.lightboxGestureAnimating = true;
            this.lightboxGestureTransition = duration
                ? `transform ${duration}ms cubic-bezier(0.22, 0.72, 0, 1)`
                : 'none';
            this.lightboxGestureOffsetX = 0;
            this.lightboxGestureOffsetY = 0;
            this.queueLightboxMotion(() => {
                this.lightboxGestureTransition = 'none';
                this.lightboxGestureAxis = null;
                this.lightboxGestureAnimating = false;
            }, 260);
        },

        cancelLightboxGesture() {
            this.lightboxGestureStart = null;
            if (this.lightboxGestureOffsetX || this.lightboxGestureOffsetY) this.snapBackLightbox();
            else this.lightboxGestureAxis = null;
        },

        handleLightboxBackdropClick(event) {
            if (this.lightboxVideoMotionPhase) return;
            if (Date.now() < this.lightboxSuppressClickUntil) {
                this.lightboxSuppressClickUntil = 0;
                event.stopPropagation();
                return;
            }
            this.closeLightbox();
        },

        scrollToTop() {
            window.scrollTo({ top: 0, behavior: 'smooth' });
        },

        feedUrl(page) {
            let url = `/api/tweets?collection=${this.collectionFilter}&sort=${this.sortOrder}&page=${page}`;
            if (this.sortOrder === 'random') {
                if (this.randomSeed === null) this.randomSeed = Math.floor(Math.random() * 2147483648);
                url += `&random_seed=${this.randomSeed}`;
            }
            if (this.searchQuery.trim()) {
                url += `&q=${encodeURIComponent(this.searchQuery)}`;
            }
            return url;
        },

        // First pages are kept briefly by URL, so returning to a recent list, or opening
        // one requested at startup or while idle, renders without waiting.
        requestFeedPage(url, { speculative = false } = {}) {
            const cached = feedPages.get(url);
            if (cached && Date.now() - cached.at < FEED_CACHE_MS) return cached;
            if (typeof globalThis.fetch !== 'function') return null;
            const entry = { at: Date.now(), speculative, result: null, promise: null };
            const forget = () => {
                if (feedPages.get(url) === entry) feedPages.delete(url);
            };
            entry.promise = globalThis.fetch(url).then(async response => {
                let data;
                try {
                    data = await response.json();
                } catch (parseError) {
                    throw new Error(`Invalid server response (${response.status}): Please check server logs.`);
                }
                const result = { ok: response.ok, status: response.status, data };
                if (result.ok) entry.result = result;
                else forget();
                return result;
            }, error => {
                forget();
                throw error;
            });
            entry.promise.catch(() => {});
            feedPages.set(url, entry);
            if (feedPages.size > FEED_CACHE_LIMIT) feedPages.delete(feedPages.keys().next().value);
            return entry;
        },

        scheduleFeedPrefetch() {
            if (feedPrefetchScheduled || typeof globalThis.fetch !== 'function') return;
            feedPrefetchScheduled = true;
            const prefetch = () => {
                for (const [collection, sort] of DEFAULT_FEEDS) {
                    this.requestFeedPage(`/api/tweets?collection=${collection}&sort=${sort}&page=1&cursor=`);
                }
            };
            if (typeof globalThis.requestIdleCallback === 'function') globalThis.requestIdleCallback(prefetch, { timeout: 3000 });
            else setTimeout(prefetch, 1500);
        },

        async fetchFeedPage(url) {
            const response = await fetch(url);
            let data;
            try {
                data = await response.json();
            } catch (parseError) {
                throw new Error(`Invalid server response (${response.status}): Please check server logs.`);
            }
            return { ok: response.ok, status: response.status, data };
        },

        async fetchResultCount(listUrl, generation) {
            const params = new URL(listUrl, window.location.origin).searchParams;
            const url = `/api/tweets/count?collection=${encodeURIComponent(params.get('collection'))}&q=${encodeURIComponent(params.get('q') || '')}`;
            const controller = new AbortController();
            this.countController = controller;
            try {
                const response = await fetch(url, { signal: controller.signal });
                const data = await response.json();
                if (!response.ok || !Number.isInteger(data.total) || data.total < 0) throw new Error('Count unavailable');
                if (generation === this.feedGeneration) this.total = data.total;
            } catch (error) {
                if (generation === this.feedGeneration && !controller.signal.aborted) this.countError = true;
            } finally {
                if (this.countController === controller) this.countController = null;
            }
        },

        // An appended page can leave the trigger inside the prefetch margin, where the
        // observer does not fire again; keep loading until it is out of reach.
        continueFeedPrefetch() {
            const sentinel = this.$refs?.feedSentinel;
            if (!sentinel || sentinel.offsetParent === null || !this.hasMore || this.loading || this.loadingMore) return;
            if (sentinel.getBoundingClientRect().top < window.innerHeight + FEED_PREFETCH_MARGIN_PX) this.loadMore();
        },

        async fetchTweets(append = false) {
            if (this.archiveReady === false || this.setupForced) return;
            const requestId = ++this.feedRequestId;
            const requestedPage = append ? this.page + 1 : 1;
            let url = this.feedUrl(requestedPage);
            if (!append) url += '&cursor=';
            else if (this.nextCursor !== null) url += `&cursor=${encodeURIComponent(this.nextCursor)}`;

            let entry = null;
            if (!append) {
                this.feedGeneration += 1;
                this.countController?.abort();
                this.countError = false;
                this.total = null;
                this.hasMore = false;
                this.nextCursor = null;
                window.scrollTo(0, 0);
                // Reloading the list already on screen (retry, repeated search) asks again.
                if (url === feedListUrl) feedPages.delete(url);
                feedListUrl = url;
                entry = this.requestFeedPage(url);
                // Keep the previous list visible (dimmed) until the new one arrives.
                if (!entry?.result) this.loading = true;
            } else {
                this.loadingMore = true;
            }

            try {
                this.error = null;
                let result;
                if (append || !entry) {
                    result = await this.fetchFeedPage(url);
                } else {
                    result = entry.result || await entry.promise;
                    if (!result.ok && entry.speculative) {
                        // The startup request can race setup; ask again now that it is ready.
                        result = await this.fetchFeedPage(url);
                    }
                }
                const data = result.data;

                if (requestId !== this.feedRequestId) return;
                if (result.status === 409 && append && this.nextCursor) {
                    // A new sync changed derived like order. Start a fresh list.
                    feedPages.clear();
                    return await this.fetchTweets();
                }
                if (!result.ok) {
                    throw new Error(data.detail || `Server error: ${result.status}`);
                }

                if (append) {
                    const seen = new Set(this.tweets.map(tweet => tweet.tweet_id));
                    const added = data.tweets.filter(tweet => {
                        if (seen.has(tweet.tweet_id)) return false;
                        seen.add(tweet.tweet_id);
                        return true;
                    });
                    this.tweets = [...this.tweets, ...added];
                    this.renderFeedList(added, { append: true });
                } else {
                    this.tweets = data.tweets;
                    this.searchHighlightTerms = data.highlight_terms || [];
                    this.renderFeedList(data.tweets);
                    this.scheduleFeedPrefetch();
                }
                this.page = data.page;
                this.totalPages = data.pages;
                if (!append || data.total !== null) this.total = data.total;
                this.hasMore = data.has_more === true;
                this.nextCursor = data.next_cursor || null;
                if (!append && data.total === null && new URL(url, window.location.origin).searchParams.get('q')) {
                    if (!data.has_more) this.total = this.tweets.length;
                    else this.fetchResultCount(url, this.feedGeneration);
                }
            } catch (e) {
                if (requestId !== this.feedRequestId) return;
                console.error(e);
                this.error = e.message;
                if (!append) {
                    this.tweets = [];
                    this.renderFeedList([]);
                }
            } finally {
                if (requestId === this.feedRequestId) {
                    this.loading = false;
                    this.loadingMore = false;
                    this.$nextTick(() => this.continueFeedPrefetch());
                }
            }
        },

        // The feed list renders outside Alpine: each card is one HTML string built by
        // renderFeedCard(), a page is a single DOM write, and listeners on the list
        // handle every card. this.tweets remains the data source.
        feedListElement() {
            // A profile search outlives the x-if card that initiated it, so its
            // Alpine magic refs may disappear before the response renders.
            const list = this.$refs?.feedList || boundFeedList;
            if (list && list !== boundFeedList) {
                this.bindFeedList(list);
                boundFeedList = list;
            }
            return list || null;
        },

        renderFeedList(tweets, { append = false } = {}) {
            const list = this.feedListElement();
            if (!list) return;
            // A new list closes the menu first, so no new card renders as its opener.
            if (!append && this.tweetMenuOpen === this.listMenu.key) this.closeTweetMenu(this.listMenu.key);
            const html = tweets.map(tweet => this.renderFeedCard(tweet)).join('');
            outsideAlpine(() => {
                if (append) list.insertAdjacentHTML('beforeend', html);
                else list.innerHTML = html;
            });
        },

        bindFeedList(list) {
            const menuButton = event => event.target?.closest?.('[data-action="menu"]') || null;
            list.addEventListener('click', event => this.handleFeedClick(event, list));
            // Holding the overflow button opens Tags; pressing it must not prefetch the
            // conversation (the document-level pointerdown listener).
            list.addEventListener('pointerdown', event => {
                const button = menuButton(event);
                if (!button) return;
                event.stopPropagation();
                const tweet = this.feedCardTweet(button);
                if (tweet) this.startTweetMenuPress(event, tweet.tweet_id, tweet.media_tags);
            });
            for (const type of ['pointerup', 'pointercancel']) {
                list.addEventListener(type, event => {
                    if (!menuButton(event)) return;
                    event.stopPropagation();
                    this.cancelTweetMenuPress();
                });
            }
            list.addEventListener('contextmenu', event => {
                if (menuButton(event)) event.preventDefault();
            });
            // pointerleave, load, and error do not bubble; catch them on the way down.
            list.addEventListener('pointerleave', event => {
                if (event.target?.dataset?.action === 'menu') this.cancelTweetMenuPress();
            }, true);
            list.addEventListener('load', event => {
                if (event.target?.classList?.contains('avatar-image')) this.avatarLoaded(event.target);
            }, true);
            list.addEventListener('error', event => {
                if (event.target?.classList?.contains('avatar-image')) this.avatarFailed(event.target);
            }, true);
        },

        // Card regions declare their click behavior with data-action. The nearest one
        // wins, and every action except opening the card's thread stops propagation,
        // as the per-card template's @click.stop handlers did.
        handleFeedClick(event, list) {
            for (let element = event.target; element && element !== list; element = element.parentElement) {
                // Links (tweet text, link cards, Open on X) navigate on their own.
                if (element.tagName === 'A') {
                    event.stopPropagation();
                    return;
                }
                const action = element.dataset?.action;
                if (!action) continue;
                if (action === 'text') {
                    if (this.handleTextClick(event, element)) return;
                    continue;
                }
                if (action === 'thread') {
                    this.openThread(element.dataset.threadId);
                    return;
                }
                event.stopPropagation();
                if (action === 'quote') {
                    this.openThread(element.dataset.threadId);
                } else if (action === 'profile') {
                    const tweet = this.feedCardTweet(element);
                    if (tweet) this.openProfileCard({ currentTarget: element }, tweet);
                } else if (action === 'reply-profile') {
                    this.openUsernameProfileCard({ currentTarget: element }, element.dataset.username);
                } else if (action === 'menu') {
                    this.toggleListTweetMenu(element);
                }
                return;
            }
        },

        // Thread ancestors use the same quote markup and data-action map as feed
        // cards. Their expanded quote text lives outside the feed list, so finish
        // the Show more update in place after the shared click handling.
        handleThreadParentQuoteClick(event, parent) {
            const expanded = event.target?.closest?.('.expand-tweet');
            const text = expanded?.closest?.('[data-action="text"]');
            this.handleFeedClick(event, event.currentTarget);
            if (!text || !text.contains(expanded)) return;
            const quote = this.getQuoteTweet(parent);
            if (quote) text.innerHTML = this.formatText(quote, false, true);
        },

        feedDisplayTweet(tweetObj) {
            const retweet = this.getRetweet(tweetObj);
            return retweet ? this.formatRetweetToTweet(tweetObj, retweet) : tweetObj;
        },

        feedCard(tweetId) {
            for (const card of this.feedListElement()?.children || []) {
                if (card.dataset.tweetId === tweetId) return card;
            }
            return null;
        },

        // The displayed tweet (a retweet's original) of the card containing an element.
        feedCardTweet(element) {
            const tweetId = element?.closest?.('.feed-card')?.dataset.tweetId;
            const tweetObj = tweetId && this.tweets.find(tweet => String(tweet.tweet_id) === tweetId);
            return tweetObj ? this.feedDisplayTweet(tweetObj) : null;
        },

        // Show more expands text in place, as x-html did, leaving media playing.
        expandFeedText(tweetId) {
            const list = this.feedListElement();
            if (!list || !tweetId) return;
            for (const toggle of list.querySelectorAll('.expand-tweet')) {
                if (toggle.dataset.id !== tweetId) continue;
                const text = toggle.closest('[data-action="text"]');
                const tweet = this.feedCardTweet(text);
                if (!text || !tweet) continue;
                const source = text.closest('[data-action="quote"]') ? this.getQuoteTweet(tweet) : tweet;
                outsideAlpine(() => { text.innerHTML = this.formatText(source, false, true); });
            }
            this.placeListMenu();
        },

        // Render the quoted post consistently in feed cards and thread ancestors.
        // The owner tweet supplies the normalized quote, media, and compact-layout
        // context; render helpers only read these values.
        renderQuoteCard(tweet, { highlightSearch = true } = {}) {
            const part = (html, attributes = '') => (html ? `<div${attributes}>${html}</div>` : '');
            const quote = this.getQuoteTweet(tweet);
            if (!quote) return '';
            if (this.isTombstone(quote)) return part(this.renderQuotePlaceholder(quote), ' data-action="stop"');

            const escape = value => this.escapeHTML(value);
            const quoteId = this.getTweetId(quote);
            const quoteReplyTo = this.getReplyRecipients({ raw_json: quote });
            // A compact quote's text is clamped by CSS, so it is rendered without Show more.
            const compact = this.isMediaQuotingMedia(tweet);
            return `<div class="quote-card mt-3 border border-[var(--border-color)] rounded-xl p-3 hover-bg transition cursor-pointer${compact ? ' quote-compact' : ''}"${quoteId ? ` data-thread-id="${escape(quoteId)}"` : ''} data-action="quote">`
                + this.renderQuoteByline(quote)
                + (quoteReplyTo.length ? this.renderReplyRecipients({ raw_json: quote }, 'quote-reply ') : '')
                + `<div class="quote-text text-[15px] whitespace-pre-wrap leading-normal break-words" data-action="text">${this.formatText(quote, compact, highlightSearch)}</div>`
                + (compact
                    ? part(this.renderQuoteThumbnail(tweet.qt_media), ' class="quote-media"')
                    : part(this.renderMediaGrid(tweet.qt_media, 'quote'), ' class="quote-media" data-action="stop"'))
                + part(this.renderCard({ raw_json: quote }), ' data-action="stop"')
                + this.renderNestedQuote(quote, highlightSearch)
                + part(this.renderCommunityNote(quote, true, false, Boolean(tweet.qt_media?.length)), ' data-action="stop"')
                + '</div>';
        },

        // One feed card, using the helpers the thread views render with. Values the
        // helpers do not escape go through escapeHTML, as x-text did for the template.
        renderFeedCard(tweetObj) {
            const escape = value => this.escapeHTML(value);
            const part = (html, attributes = '') => (html ? `<div${attributes}>${html}</div>` : '');
            const replyLine = (source, className = '') => this.renderReplyRecipients(source, className);
            const retweet = this.getRetweet(tweetObj);
            const tweet = retweet ? this.formatRetweetToTweet(tweetObj, retweet) : tweetObj;
            const author = tweet.author || {};
            const replyTo = this.getReplyRecipients(tweet);
            const menuOpen = this.tweetMenuOpen === `list:${tweetObj.tweet_id}`;

            const header = retweet
                ? '<div class="tweet-repost-context text-[13px] text-[var(--text-secondary)] font-bold mb-1 flex items-center space-x-2 ml-8">'
                    + '<svg viewBox="0 0 24 24" class="w-4 h-4 fill-current"><path d="M4.5 3.88l4.432 4.14-1.364 1.46L5.5 7.55V16c0 1.1.896 2 2 2H13v2H7.5c-2.209 0-4-1.79-4-4V7.55L1.432 9.48.068 8.02 4.5 3.88zM16.5 6H11V4h5.5c2.209 0 4 1.79 4 4v8.45l2.068-1.93 1.364 1.46-4.432 4.14-4.432-4.14 1.364-1.46 2.068 1.93V8c0-1.1-.896-2-2-2z"></path></svg>'
                    + `<span>${escape(`${tweetObj.author?.display_name || 'Unknown'} Reposted`)}</span></div>`
                : '';
            const avatar = '<div class="avatar w-10 h-10 rounded-full flex-shrink-0 relative bg-[var(--gray-700)] flex items-center justify-center font-bold text-[var(--white-icon)] uppercase overflow-hidden" data-action="profile">'
                + `<span class="avatar-initial">${escape(this.getInitial(tweet))}</span>`
                + `<img src="${escape(this.avatarUrl(author.id))}" class="avatar-image absolute inset-0 w-full h-full object-cover z-10" loading="lazy"></div>`;
            const byline = '<div class="tweet-byline tweet-byline-feed flex items-start text-[15px]"><div class="flex items-center space-x-1 min-w-0">'
                + `<span class="font-bold text-[var(--text-primary)] truncate hover:underline tweet-byline-name" data-action="profile">${escape(author.display_name || 'Unknown')}</span>`
                + `<span class="text-[var(--text-secondary)] tweet-byline-detail tweet-byline-handle">${escape(`@${author.username || 'unknown'}`)}</span>`
                + '<span class="text-[var(--text-secondary)]">·</span>'
                + '<div class="roll-wrapper text-[var(--text-secondary)] tweet-byline-detail" data-action="stop"><div class="roll-inner">'
                + `<span class="roll-item hover:underline">${escape(this.formatDate(tweet.created_at))}</span>`
                + `<span class="roll-item text-[var(--accent-color)] text-[13px] font-medium">${escape(this.formatSyncDate(tweet))}</span>`
                + '</div></div></div>'
                + '<div class="tweet-menu-trigger">'
                + `<button type="button" class="w-9 h-9 rounded-full flex items-center justify-center text-[var(--text-secondary)] hover:text-[var(--accent-color)] hover:bg-[color-mix(in_srgb,var(--accent-color)_10%,transparent)] select-none touch-manipulation${menuOpen ? ' opacity-0' : ''}" aria-label="More actions" aria-haspopup="menu" aria-expanded="${menuOpen}" title="More actions (hold for Tags)" data-action="menu">`
                + '<svg viewBox="0 0 24 24" class="w-[18px] h-[18px] fill-current" aria-hidden="true"><circle cx="5" cy="12" r="1.8"></circle><circle cx="12" cy="12" r="1.8"></circle><circle cx="19" cy="12" r="1.8"></circle></svg>'
                + '</button></div></div>';

            const quoteBlock = this.renderQuoteCard(tweet);

            return `<div class="px-4 pt-3 pb-3 tweet-border feed-card hover-bg transition duration-200 cursor-pointer" data-tweet-id="${escape(tweetObj.tweet_id)}"`
                + `${tweet.tweet_id ? ` data-thread-id="${escape(tweet.tweet_id)}"` : ''} data-action="thread">`
                + header
                + '<div class="flex space-x-2">'
                + avatar
                + '<div class="flex-1 min-w-0">'
                + byline
                + (replyTo.length ? replyLine(tweet) : '')
                + `<div class="tweet-text text-[15px] whitespace-pre-wrap break-words" data-action="text">${this.formatText(tweet, false, true)}</div>`
                + part(this.renderMediaGrid(tweet.media), ' data-action="stop"')
                + part(this.renderCard(tweet), ' data-action="stop"')
                + quoteBlock
                + part(this.renderCommunityNote(tweet.raw_json), ' data-action="stop"')
                + part(this.renderActionBar(tweet))
                + '</div></div></div>';
        },

        applyTheme(key) {
            const theme = THEMES[key];
            if (!theme) return;
            this.currentTheme = key;
            const root = document.documentElement;
            for (const [prop, val] of Object.entries(theme)) {
                if (prop.startsWith('--')) root.style.setProperty(prop, val);
            }
            const bgHex = theme['--bg-primary'] || '#000000';
            const r = parseInt(bgHex.slice(1,3),16), g = parseInt(bgHex.slice(3,5),16), b = parseInt(bgHex.slice(5,7),16);
            this.darkMode = (r*0.299 + g*0.587 + b*0.114) < 128;
            if (this.darkMode) { root.classList.remove('light'); } else { root.classList.add('light'); }

            // Restore saved accent for this theme (if it has selectable accents)
            if (theme._accents) {
                const saved = localStorage.getItem('tvx-accent-' + key);
                const accent = saved ? theme._accents.find(a => a.color === saved) : null;
                if (accent) {
                    this.currentAccent = accent.color;
                    root.style.setProperty('--accent-color', accent.color);
                    root.style.setProperty('--accent-hover', accent.hover);
                    root.style.setProperty('--accent-text', accent.text);
                } else {
                    this.currentAccent = theme['--accent-color'];
                }
            } else {
                this.currentAccent = theme['--accent-color'];
            }

            localStorage.setItem('tvx-theme', key);
        },

        setAccent(accent) {
            this.currentAccent = accent.color;
            const root = document.documentElement;
            root.style.setProperty('--accent-color', accent.color);
            root.style.setProperty('--accent-hover', accent.hover);
            root.style.setProperty('--accent-text', accent.text);
            localStorage.setItem('tvx-accent-' + this.currentTheme, accent.color);
        },

        canDiscoverFonts() {
            return window.isSecureContext && typeof window.queryLocalFonts === 'function';
        },

        fontChoices() {
            const choices = this.FONT_OPTIONS.map(font => ({
                ...font,
                label: ['system', 'serif', 'mono'].includes(font.key)
                    ? font.label : `${font.label} (if installed)`,
            }));
            const families = new Set(this.installedFonts);
            if (this.fontFamily.startsWith('local:')) families.add(this.fontFamily.slice(6));
            for (const family of [...families].sort((a, b) => a.localeCompare(b))) {
                choices.push({ key: `local:${family}`, label: family, family: this.localFontFamily(family) });
            }
            return choices;
        },

        localFontFamily(name) {
            // Escape a CSS string, keeping arbitrary font names out of CSS syntax.
            const escaped = String(name).replace(/[\\"\n\r\f]/g, character =>
                `\\${character.charCodeAt(0).toString(16)} `);
            return `"${escaped}", sans-serif`;
        },

        async discoverFonts() {
            if (this.fontDiscoveryBusy) return;
            if (!this.canDiscoverFonts()) {
                this.fontDiscoveryMessage = 'Font discovery is unavailable here. Choose one of the local fallback fonts.';
                return;
            }
            this.fontDiscoveryBusy = true;
            this.fontDiscoveryMessage = '';
            try {
                const fonts = await window.queryLocalFonts();
                this.installedFonts = [...new Set(fonts.map(font => font.family).filter(Boolean))];
                this.fontDiscoveryMessage = `${this.installedFonts.length} font families available on this device.`;
            } catch (error) {
                this.fontDiscoveryMessage = error.name === 'NotAllowedError'
                    ? 'Font access was not granted. The local fallback fonts are still available.'
                    : 'Could not list installed fonts. The local fallback fonts are still available.';
            } finally {
                this.fontDiscoveryBusy = false;
            }
        },

        selectFont(key) {
            const font = this.fontChoices().find(font => font.key === key);
            if (font) this.setFont(font.key, font.family);
            this.fontPickerOpen = false;
            this.$refs?.fontTrigger?.focus();
        },

        selectedFontChoice() {
            return this.fontChoices().find(font => font.key === this.fontFamily) || this.FONT_OPTIONS[0];
        },

        openFontPicker(edge = 'selected') {
            this.fontPickerOpen = true;
            this.$nextTick(() => this.focusFontOption(edge));
        },

        focusFontOption(direction) {
            const options = [...this.$refs.fontOptions.querySelectorAll('[role="option"]')];
            if (!options.length) return;
            const current = options.indexOf(document.activeElement);
            let index;
            if (direction === 'first') index = 0;
            else if (direction === 'last') index = options.length - 1;
            else if (direction === 'selected') index = this.fontChoices().findIndex(font => font.key === this.fontFamily);
            else index = (current + direction + options.length) % options.length;
            options[Math.max(0, index)].focus();
        },

        setFont(key, family) {
            this.fontFamily = key;
            document.body.style.fontFamily = family;
            localStorage.setItem('tvx-font', key);
            localStorage.setItem('tvx-font-family', family);
        },

        setFontSize(key, px) {
            this.fontSize = key;
            document.documentElement.style.setProperty('--font-size-base', px);
            localStorage.setItem('tvx-font-size', key);
            localStorage.setItem('tvx-font-size-px', px);
        },

        parseRoute(pathname) {
            let routePath = pathname;
            const basePath = this.demoBasePath();
            if (this.isDemo && basePath !== '/' && routePath.startsWith(basePath)) {
                routePath = '/' + routePath.slice(basePath.length);
            }
            if (routePath === '/' || routePath === '') return { viewMode: 'list' };
            const match = routePath.match(/^\/post\/(\d+)(?:\/(quotes))?\/?$/);
            if (!match) return null;
            return match[2]
                ? { viewMode: 'quotes', quotesTweetId: match[1] }
                : { viewMode: 'thread', tweetId: match[1] };
        },

        routeUrl(route) {
            const basePath = this.demoBasePath();
            if (route?.viewMode === 'list') return basePath;
            if (route?.viewMode === 'thread' && typeof route.tweetId === 'string' && /^\d+$/.test(route.tweetId)) {
                return `${basePath}post/${encodeURIComponent(route.tweetId)}`;
            }
            if (route?.viewMode === 'quotes' && typeof route.quotesTweetId === 'string' && /^\d+$/.test(route.quotesTweetId)) {
                return `${basePath}post/${encodeURIComponent(route.quotesTweetId)}/quotes`;
            }
            throw new Error('Invalid TweetNook route');
        },

        demoBasePath() {
            if (!this.isDemo) return '/';
            const configured = String(window.TWEETNOOK_DEMO_BASE || '/');
            return `/${configured.replace(/^\/+|\/+$/g, '')}${configured === '/' ? '' : '/'}`;
        },

        routeFromHistoryState(state) {
            if (!state || typeof state !== 'object') return null;
            if (state.viewMode === 'list') return { viewMode: 'list' };
            if (state.viewMode === 'thread' && typeof state.tweetId === 'string' && /^\d+$/.test(state.tweetId)) {
                return { viewMode: 'thread', tweetId: state.tweetId };
            }
            if (state.viewMode === 'quotes' && typeof state.quotesTweetId === 'string' && /^\d+$/.test(state.quotesTweetId)) {
                return { viewMode: 'quotes', quotesTweetId: state.quotesTweetId };
            }
            return null;
        },

        historyStateForRoute(route, { depth = this.navigationDepth, scrollY } = {}) {
            const state = { ...route, tweetNookDepth: depth };
            if (scrollY !== undefined) state.scrollY = scrollY;
            return state;
        },

        currentRoute() {
            return { ...this.activeRoute };
        },

        routeKey(route) {
            if (route.viewMode === 'thread') return `thread:${route.tweetId}`;
            if (route.viewMode === 'quotes') return `quotes:${route.quotesTweetId}`;
            return 'list';
        },

        shouldUseSplitPanel() {
            return this.splitPanel && window.innerWidth >= 1024;
        },

        pushRoute(route) {
            const state = this.historyStateForRoute(route, { depth: this.navigationDepth + 1 });
            history.pushState(state, '', this.routeUrl(route));
            this.navigationDepth = state.tweetNookDepth;
            this.activeRoute = { ...route };
            return state;
        },

        replaceRoute(route, { scrollY, depth = this.navigationDepth } = {}) {
            const state = this.historyStateForRoute(route, { depth, scrollY });
            history.replaceState(state, '', this.routeUrl(route));
            this.navigationDepth = depth;
            this.activeRoute = { ...route };
            return state;
        },

        savePanelSnapshot() {
            const top = this.panelStack[this.panelStack.length - 1];
            if (!top) return;
            top.scrollY = this.$refs.detailPanel?.scrollTop || 0;
            if (this.panelMode === 'thread') top.data = this.panelThreadData?.partial ? null : this.panelThreadData;
            else if (this.panelMode === 'quotes') top.data = this.panelQuotesList;
        },

        saveCurrentRouteState() {
            const route = this.currentRoute();
            if (route.viewMode === 'list') {
                return this.replaceRoute(route, { scrollY: window.scrollY });
            }
            if (this.shouldUseSplitPanel()) this.savePanelSnapshot();
            return this.replaceRoute(route, { scrollY: window.scrollY });
        },

        async applyRoute(route, { fromPopState = false, state = null } = {}) {
            if (!route) return false;
            const routeState = state || this.historyStateForRoute(route);
            if (state && Number.isInteger(routeState.tweetNookDepth) && routeState.tweetNookDepth >= 0) {
                this.navigationDepth = routeState.tweetNookDepth;
            } else if (fromPopState) {
                this.navigationDepth = 0;
            }
            this.activeRoute = { ...route };

            if (!this.archiveResourcesLoaded) {
                this.pendingRoute = { ...route };
                this.pendingRouteState = routeState;
                return false;
            }

            this.pendingRoute = null;
            this.pendingRouteState = null;
            this.routeUsesPanel = this.shouldUseSplitPanel();
            if (route.viewMode === 'list') {
                this.closePanel(false);
                this.viewMode = 'list';
                if (routeState?.scrollY !== undefined) {
                    const scrollY = routeState.scrollY;
                    window.scrollTo(0, scrollY);
                    this.$nextTick(() => {
                        window.scrollTo(0, scrollY);
                        setTimeout(() => window.scrollTo(0, scrollY), 20);
                    });
                }
                return true;
            }

            if (this.shouldUseSplitPanel()) {
                this.viewMode = 'list';
                await this.applyPanelRoute(route, routeState);
                return true;
            }

            this.closePanel(false);
            if (route.viewMode === 'thread') await this.showFullThread(route.tweetId);
            else await this.showFullQuotes(route.quotesTweetId);
            return true;
        },

        async applyPanelRoute(route, state) {
            this.savePanelSnapshot();
            const key = this.routeKey(route);
            let matchIndex = -1;
            for (let index = this.panelStack.length - 1; index >= 0; index--) {
                const entry = this.panelStack[index];
                if (entry.routeKey !== key) continue;
                if (Number.isInteger(state?.tweetNookDepth) && entry.historyDepth !== state.tweetNookDepth) continue;
                matchIndex = index;
                break;
            }
            if (matchIndex >= 0) {
                this.panelStack = this.panelStack.slice(0, matchIndex + 1);
            } else {
                if (state?.tweetNookDepth === 0) this.panelStack = [];
                this.panelStack.push({
                    type: route.viewMode,
                    tweetId: route.viewMode === 'thread' ? route.tweetId : route.quotesTweetId,
                    routeKey: key,
                    historyDepth: state?.tweetNookDepth,
                });
            }

            const entry = this.panelStack[this.panelStack.length - 1];
            this.panelMode = route.viewMode;
            this.$nextTick(() => {
                if (this.$refs.detailPanel) this.$refs.detailPanel.scrollTop = entry.scrollY || 0;
            });
            if (route.viewMode === 'thread') {
                this.panelQuotesList = [];
                if (entry.data) {
                    this.panelThreadData = entry.data;
                    this.panelReplyLimit = THREAD_REPLY_BATCH;
                    this.panelOtherReplies = false;
                    this.panelLoadingThread = false;
                    return;
                }
                const seed = this.threadSeed(route.tweetId);
                this.panelThreadData = seed;
                this.panelReplyLimit = THREAD_REPLY_BATCH;
                this.panelOtherReplies = false;
                this.panelLoadingThread = !seed;
                try {
                    const data = await this.loadThread(route.tweetId);
                    entry.data = data;
                    if (this.panelStack.at(-1) === entry) {
                        this.keepThreadMainInPlace(true, () => { this.panelThreadData = data; });
                    }
                } catch (error) {
                    console.error(error);
                    if (this.panelStack.at(-1) === entry) this.goBack();
                } finally {
                    if (this.panelStack.at(-1) === entry) this.panelLoadingThread = false;
                }
                return;
            }

            this.panelThreadData = null;
            this.panelQuotesTweetId = route.quotesTweetId;
            this.panelQuotesPage = 1;
            if (entry.data) {
                this.panelQuotesList = entry.data;
                this.panelQuotesLoading = false;
                return;
            }
            this.panelQuotesList = [];
            await this.fetchPanelQuotes();
        },

        // Render a tweet the page already shows while its conversation loads.
        threadSeed(tweetId) {
            const known = [];
            const add = tweet => {
                if (!tweet || typeof tweet !== 'object') return;
                known.push(tweet);
                if (tweet.quoted_tweet) known.push(tweet.quoted_tweet);
                if (tweet.retweeted_tweet) known.push(tweet.retweeted_tweet);
            };
            this.tweets.forEach(add);
            for (const data of [this.threadData, this.panelThreadData]) {
                if (!data) continue;
                add(data.main);
                (data.parents || []).forEach(add);
                for (const child of [...(data.children || []), ...(data.other_children || [])]) {
                    add(child);
                    (child.op_replies || []).forEach(add);
                }
            }
            [...this.quotesList, ...this.panelQuotesList].forEach(add);
            const match = known.find(tweet => tweet.tweet_id === tweetId && tweet.author && !this.isPlaceholderTweet(tweet));
            if (!match) return null;
            const { op_replies, thread_depth, parent_tweet_id, ...main } = match;
            return { main, parents: [], children: [], reply_tree_truncated: false, partial: true };
        },

        prefetchThread(tweetId) {
            if (typeof tweetId === 'string' && /^\d+$/.test(tweetId) && typeof globalThis.fetch === 'function') {
                this.loadThread(tweetId).catch(() => {});
            }
        },

        // Parents arrive above the tweet already on screen; keep it where it was.
        keepThreadMainInPlace(panel, update) {
            const root = panel ? this.$refs?.detailPanel : globalThis.document;
            const main = () => root?.querySelector?.(`[data-thread-main="${panel ? 'panel' : 'page'}"]`);
            const visibleTop = () => {
                const element = main();
                if (!element || !element.getClientRects?.().length) return undefined;
                const rect = element.getBoundingClientRect?.();
                if (!rect || rect.width <= 0 || rect.height <= 0) return undefined;
                return rect.top;
            };
            const before = visibleTop();
            update();
            if (before === undefined) return;
            this.$nextTick(() => {
                const after = visibleTop();
                if (after === undefined || after === before) return;
                if (panel) root.scrollTop += after - before;
                else window.scrollBy(0, after - before);
            });
        },

        async showFullThread(tweetId) {
            this.viewMode = 'thread';
            const seed = this.threadSeed(tweetId);
            this.threadData = seed;
            this.threadReplyLimit = THREAD_REPLY_BATCH;
            this.threadOtherReplies = false;
            this.loadingThread = !seed;
            window.scrollTo(0, 0);
            try {
                const data = await this.loadThread(tweetId);
                if (this.routeKey(this.activeRoute) === `thread:${tweetId}` && !this.shouldUseSplitPanel()) {
                    this.keepThreadMainInPlace(false, () => { this.threadData = data; });
                }
            } catch (error) {
                console.error(error);
                if (this.routeKey(this.activeRoute) === `thread:${tweetId}`) this.goBack();
            } finally {
                this.loadingThread = false;
            }
        },

        async showFullQuotes(tweetId) {
            this.viewMode = 'quotes';
            this.quotesTweetId = tweetId;
            this.quotesPage = 1;
            this.quotesList = [];
            this.quotesLoading = true;
            window.scrollTo(0, 0);
            await this.fetchQuotes();
        },

        toggleSplitPanel(val) {
            const route = this.currentRoute();
            this.splitPanel = val;
            this.routeUsesPanel = this.shouldUseSplitPanel();
            localStorage.setItem('tvx-split-panel', val);
            if (route.viewMode === 'list') this.closePanel(false);
            else if (this.archiveResourcesLoaded) return this.applyRoute(route, { state: history.state });
        },

        closePanel(syncRoute = true) {
            this.panelStack = [];
            this.panelMode = null;
            this.panelThreadData = null;
            this.panelQuotesList = [];
            if (syncRoute && this.activeRoute.viewMode !== 'list') {
                const route = { viewMode: 'list' };
                const state = this.replaceRoute(route, { scrollY: window.scrollY });
                this.applyRoute(route, { state });
            }
        },

        panelGoBack() {
            if (this.navigationDepth > 0) this.goBack();
        },

        async fetchPanelQuotes(append = false) {
            if (append) this.panelQuotesLoadingMore = true;
            else this.panelQuotesLoading = true;
            try {
                const res = await fetch(`/api/tweets/${this.panelQuotesTweetId}/quotes?page=${this.panelQuotesPage}`);
                const data = await res.json();
                if (append) this.panelQuotesList = [...this.panelQuotesList, ...data.tweets];
                else this.panelQuotesList = data.tweets;
                this.panelQuotesTotalPages = Math.ceil(data.total / data.limit);
                // Cache on stack
                const top = this.panelStack[this.panelStack.length - 1];
                if (top) top.data = this.panelQuotesList;
            } catch (e) {
                console.error('Failed to fetch panel quotes', e);
            } finally {
                this.panelQuotesLoading = false;
                this.panelQuotesLoadingMore = false;
            }
        },
        
        loadMore() {
            if (this.loadingMore || this.loading || !this.hasMore) return;
            return this.fetchTweets(true);
        },

        visibleReplies(children, limit) {
            const visible = [];
            let shown = 0;
            for (const child of children || []) {
                const size = 1 + (child.op_replies?.length || 0);
                if (visible.length && shown + size > limit) break;
                visible.push(child);
                shown += size;
            }
            return visible;
        },

        hasHiddenReplies(children, limit) {
            return this.visibleReplies(children, limit).length < (children?.length || 0);
        },

        showMoreReplies(panel = false) {
            if (panel) this.panelReplyLimit += THREAD_REPLY_BATCH;
            else this.threadReplyLimit += THREAD_REPLY_BATCH;
        },

        // Mention-only replies stay behind "Show other replies" until requested.
        threadReplies(data, showOther) {
            const children = data?.children || [];
            return showOther ? [...children, ...(data?.other_children || [])] : children;
        },

        // Like X's "Show probable spam", the button waits until every regular reply is shown.
        hasOtherReplies(data, limit, shown) {
            return !shown && Boolean(data?.other_children?.length) && !this.hasHiddenReplies(data.children, limit);
        },

        // The revealed section starts with an "Other replies" heading, like X's "Probable spam".
        firstOtherReplyId(data, shown) {
            return shown ? data?.other_children?.[0]?.tweet_id ?? null : null;
        },

        showOtherReplies(panel = false) {
            if (panel) this.panelOtherReplies = true;
            else this.threadOtherReplies = true;
            this.showMoreReplies(panel);
        },

        async loadThread(tweetId) {
            const cached = this.threadCache.get(tweetId);
            if (cached && Date.now() - cached.loadedAt < this.threadCacheTtlMs) {
                return cached.request;
            }

            const request = (async () => {
                const res = await fetch(`/api/tweets/${tweetId}`);
                if (!res.ok) throw new Error('Failed to load thread');
                return res.json();
            })();
            const cacheEntry = { request, loadedAt: Date.now() };
            this.threadCache.set(tweetId, cacheEntry);
            if (this.threadCache.size > 25) {
                this.threadCache.delete(this.threadCache.keys().next().value);
            }
            try {
                return await request;
            } catch (error) {
                if (this.threadCache.get(tweetId) === cacheEntry) {
                    this.threadCache.delete(tweetId);
                }
                throw error;
            }
        },

        async openThread(tweetId, fromPopState = false, fromPanel = false) {
            void fromPanel;
            if (typeof tweetId !== 'string' || !/^\d+$/.test(tweetId)) return;
            const route = { viewMode: 'thread', tweetId };
            if (fromPopState) return this.applyRoute(route, { fromPopState: true });
            this.saveCurrentRouteState();
            const state = this.pushRoute(route);
            return this.applyRoute(route, { state });
        },
        
        async openQuotes(tweetId, fromPanel = false) {
            void fromPanel;
            if (typeof tweetId !== 'string' || !/^\d+$/.test(tweetId)) return;
            const route = { viewMode: 'quotes', quotesTweetId: tweetId };
            this.saveCurrentRouteState();
            const state = this.pushRoute(route);
            return this.applyRoute(route, { state });
        },
        
        async fetchQuotes(append = false) {
            if (append) this.quotesLoadingMore = true;
            else this.quotesLoading = true;
            
            try {
                const res = await fetch(`/api/tweets/${this.quotesTweetId}/quotes?page=${this.quotesPage}`);
                const data = await res.json();
                
                if (append) this.quotesList = [...this.quotesList, ...data.tweets];
                else this.quotesList = data.tweets;
                
                this.quotesTotalPages = Math.ceil(data.total / data.limit);
            } catch (e) {
                console.error(e);
            } finally {
                this.quotesLoading = false;
                this.quotesLoadingMore = false;
            }
        },
        
        goBack() {
            this.pauseAllVideos();
            this.savePanelSnapshot();
            if (this.navigationDepth > 0) {
                history.back();
                return;
            }
            const route = { viewMode: 'list' };
            const state = this.replaceRoute(route, { scrollY: window.scrollY, depth: 0 });
            this.applyRoute(route, { state });
        },

        async fetchStats() {
            try {
                const res = await fetch('/api/stats/latest-sync');
                const data = await res.json();
                if (data.latest_sync) {
                    this.lastSyncFormatted = data.latest_sync;
                }
            } catch (e) {
                console.error('Failed to fetch stats', e);
            }
        },

        async openStatsModal() {
            this.suspendMediaForStats();
            this.showStatsModal = true;
            await this.fetchStatsSnapshot();
        },

        closeStatsModal() {
            this.showStatsModal = false;
            if (this.statsRefreshPollTimer) {
                clearTimeout(this.statsRefreshPollTimer);
                this.statsRefreshPollTimer = null;
            }
            this.resumeMediaAfterStats();
        },

        suspendMediaForStats() {
            statsSuspendedVideos.clear();
            document.querySelectorAll('video').forEach(video => {
                if (!video.paused) {
                    statsSuspendedVideos.add(video);
                    video.pause();
                }
            });
        },

        resumeMediaAfterStats() {
            statsSuspendedVideos.forEach(video => {
                if (video.isConnected !== false) {
                    const playResult = video.play();
                    if (playResult?.catch) playResult.catch(() => {});
                }
            });
            statsSuspendedVideos.clear();
        },

        setStatsLoading(loading) {
            this.loadingStatsSnapshot = loading;
            this.loadingStatsSummary = loading;
            this.loadingStatsCollections = loading;
            this.loadingStatsHealth = loading;
            this.loadingStatsTags = loading;
            this.storageLoading = loading;
        },

        applyStatsSnapshot(snapshot) {
            this.statsSummary = snapshot.summary;
            this.statsCollections = snapshot.collections || [];
            this.statsHealth = snapshot.health;
            this.storageData = snapshot.storage;
            this.statsTags = snapshot.tags;
            this.statsGeneratedAt = snapshot.generated_at;
            this.statsRefreshing = Boolean(snapshot.refreshing);
            this.statsRefreshFailed = Boolean(snapshot.refresh_failed);
            this.statsAgeNow = Date.now();
            if (snapshot.summary?.latest_sync) {
                this.lastSyncFormatted = snapshot.summary.latest_sync;
            }
        },

        applyStatsStatus(status) {
            this.statsRefreshing = Boolean(status.refreshing);
            this.statsRefreshFailed = Boolean(status.refresh_failed);
            this.statsAgeNow = Date.now();
        },

        async fetchStatsSnapshot() {
            const showSkeletons = !this.statsGeneratedAt;
            if (showSkeletons) this.setStatsLoading(true);
            try {
                const res = await fetch('/api/stats/snapshot');
                if (!res.ok) throw new Error(`Statistics request failed (${res.status})`);
                this.applyStatsSnapshot(await res.json());
                if (this.statsRefreshing) {
                    this.statsRefreshPollAttempts = 0;
                    this.scheduleStatsRefreshPoll();
                }
            } catch (e) {
                console.error('Failed to fetch statistics', e);
                this.statsRefreshFailed = true;
            } finally {
                if (showSkeletons) this.setStatsLoading(false);
            }
        },

        async refreshStats() {
            if (this.statsRefreshing || this.loadingStatsSnapshot) return;
            this.statsRefreshing = true;
            this.statsRefreshFailed = false;
            try {
                const res = await fetch('/api/stats/refresh', { method: 'POST' });
                if (!res.ok) throw new Error(`Statistics refresh failed (${res.status})`);
                const snapshot = await res.json();
                if (snapshot.generated_at !== this.statsGeneratedAt) {
                    this.applyStatsSnapshot(snapshot);
                } else {
                    this.applyStatsStatus(snapshot);
                }
                if (this.statsRefreshing) {
                    this.statsRefreshPollAttempts = 0;
                    this.scheduleStatsRefreshPoll();
                }
            } catch (e) {
                console.error('Failed to refresh statistics', e);
                this.statsRefreshing = false;
                this.statsRefreshFailed = true;
            }
        },

        scheduleStatsRefreshPoll() {
            if (this.statsRefreshPollTimer) clearTimeout(this.statsRefreshPollTimer);
            if (!this.statsRefreshing || !this.showStatsModal) return;
            this.statsRefreshPollAttempts += 1;
            if (this.statsRefreshPollAttempts > 300) {
                this.statsRefreshing = false;
                this.statsRefreshFailed = true;
                return;
            }
            this.statsRefreshPollTimer = setTimeout(() => this.pollStatsRefresh(), 1000);
        },

        async pollStatsRefresh() {
            this.statsRefreshPollTimer = null;
            if (!this.showStatsModal) return;
            try {
                const res = await fetch('/api/stats/status');
                if (!res.ok) throw new Error(`Statistics refresh poll failed (${res.status})`);
                const status = await res.json();
                if (status.generated_at !== this.statsGeneratedAt) {
                    await this.fetchStatsSnapshot();
                    return;
                }
                this.applyStatsStatus(status);
            } catch (e) {
                console.error('Failed to check statistics refresh', e);
            }
            if (this.statsRefreshing) {
                this.scheduleStatsRefreshPoll();
            } else {
                this.statsRefreshPollAttempts = 0;
            }
        },

        statsAgeLabel() {
            if (!this.statsGeneratedAt) return '';
            // Reading the timer-backed value keeps Alpine's relative label current.
            void this.statsAgeNow;
            const relative = this.formatRelativeDate(this.statsGeneratedAt);
            if (this.statsRefreshFailed) return `Refresh failed · updated ${relative}`;
            if (this.statsRefreshing) return `Refreshing · updated ${relative}`;
            return `Updated ${relative}`;
        },

        sortUnavailableReasons(reasons) {
            return [...reasons].sort((left, right) => (right.count || 0) - (left.count || 0));
        },

        getUnavailableReasons() {
            const reasons = this.statsHealth?.enrichment?.unavailable?.reasons || [];
            const visibleReasons = this.showEmptyUnavailableReasons
                ? reasons
                : reasons.filter(reason => (reason.count || 0) > 0);
            return this.sortUnavailableReasons(visibleReasons);
        },

        getUnavailableBarReasons() {
            const reasons = this.statsHealth?.enrichment?.unavailable?.reasons || [];
            return this.sortUnavailableReasons(reasons.filter(reason => (reason.count || 0) > 0));
        },

        getUnavailableSegmentWidth(reason) {
            const reasons = this.getUnavailableBarReasons();
            if (!reasons.length) return 0;

            const minFloor = 1.2;
            const logWeights = reasons.map(item => Math.log10(Math.max(10, item.count || 0)));
            const totalLog = logWeights.reduce((total, weight) => total + weight, 0);
            const rawWeights = reasons.map((item, index) => {
                const linearPct = item.percent_of_missing || 0;
                const logPct = totalLog > 0 ? (logWeights[index] / totalLog) * 100 : 0;
                return Math.max(minFloor, (linearPct * 0.82) + (logPct * 0.18));
            });
            const totalWeight = rawWeights.reduce((total, weight) => total + weight, 0);
            const index = reasons.findIndex(item => item.reason === reason.reason);
            return index === -1 ? 0 : Math.max(minFloor, (rawWeights[index] / totalWeight) * 100);
        },

        getUnavailableReasonStatus(reason) {
            const parts = [];
            if ((reason.due || 0) > 0) parts.push(`${reason.due.toLocaleString()} due now`);
            if ((reason.delayed || 0) > 0) parts.push(`${reason.delayed.toLocaleString()} scheduled`);
            if ((reason.permanent || 0) > 0) parts.push(`${reason.permanent.toLocaleString()} permanent`);
            if (parts.length) return parts.join(' · ');
            return (reason.count || 0) > 0 ? 'Availability recorded' : 'No unavailable tweets';
        },

        fetchArchiveEnrichmentStatus() {
            fetch('/api/stats/enrichment-incomplete')
                .then(r => r.json())
                .then(d => {
                    this.archiveEnrichmentIncomplete = d.incomplete || 0;
                })
                .catch(e => console.error('Failed to fetch archive enrichment status', e));
        },

        setHoveredStorage(id) {
            this.hoveredStorageId = id;
        },
        clearHoveredStorage() {
            this.hoveredStorageId = null;
        },
        getActiveStorageSegments() {
            if (!this.storageData) return [];
            const raw = this.storageDetailedView ? this.storageData.segments : this.storageData.simplified_segments;
            return (raw || []).filter(s => (s.bytes || 0) > 0);
        },
        getSegmentWidth(seg) {
            const segments = this.getActiveStorageSegments();
            if (!segments || !segments.length) return 2;
            
            const minFloor = 1.2;
            const logWeights = segments.map(s => Math.log10(Math.max(10, s.bytes || 0)));
            const totalLog = logWeights.reduce((a, b) => a + b, 0);
            
            const rawWeights = segments.map((s, idx) => {
                const linearPct = s.percent || 0;
                const logPct = totalLog > 0 ? (logWeights[idx] / totalLog) * 100 : 0;
                return Math.max(minFloor, (linearPct * 0.82) + (logPct * 0.18));
            });

            const totalWeight = rawWeights.reduce((a, b) => a + b, 0);
            const myIndex = segments.findIndex(s => s.id === seg.id);
            if (myIndex === -1) return minFloor;
            
            const normalizedWidth = (rawWeights[myIndex] / totalWeight) * 100;
            return Math.max(minFloor, normalizedWidth);
        },
        toggleStorageDetailView() {
            if (this.animatingStorageToggle) return;
            this.animatingStorageToggle = true;

            // Phase 1: Collapse all segment gaps so the bar becomes solid.
            this.storageGapsCollapsed = true;

            // Phase 2: After the bar becomes solid, flip state and reopen the gaps.
            setTimeout(() => {
                this.storageDetailedView = !this.storageDetailedView;
                this.expandedStorageId = null;

                this.$nextTick(() => {
                    this.storageGapsCollapsed = false;
                    setTimeout(() => {
                        this.animatingStorageToggle = false;
                    }, 600);
                });
            }, 250);
        },
        toggleExpandStorage(id) {
            if (!this.storageDetailedView) return; // Only detailed view allows row expansion
            this.expandedStorageId = this.expandedStorageId === id ? null : id;
        },

        search() { 
            if (this.collectionFilter !== 'likes' && this.sortOrder.startsWith('liked_')) this.sortOrder = 'newest';
            if (!this.searchQuery.trim() && this.sortOrder === 'default') this.sortOrder = this.collectionFilter === 'likes' ? 'liked_latest' : 'newest';
            this.page = 1;
            this.randomSeed = this.sortOrder === 'random' ? Math.floor(Math.random() * 2147483648) : null;
            const route = { viewMode: 'list' };
            if (this.activeRoute.viewMode !== 'list') this.replaceRoute(route, { scrollY: window.scrollY });
            this.activeRoute = route;
            this.closePanel(false);
            this.viewMode = 'list';
            this.fetchTweets();
        },
        searchFrom(username) {
            if(!username) return;
            this.searchQuery = `from:${username}`;
            this.search();
        },
        
        getInitial(tweet) {
            if (!tweet || !tweet.author) return '?';
            if (tweet.author.display_name) return tweet.author.display_name.charAt(0);
            if (tweet.author.username) return tweet.author.username.charAt(0);
            return '?';
        },

        toggleTweetMenu(menuKey) {
            if (Date.now() < this.tweetMenuClickBlockedUntil) return;
            this.tweetMenuOpen = this.tweetMenuOpen === menuKey ? null : menuKey;
            if (this.tweetMenuOpen) this.fullRawTweet({ tweet_id: menuKey.slice(menuKey.indexOf(':') + 1) });
            this.syncListMenuButton();
        },

        // Feed cards share one menu, keyed by the card and placed over the button that
        // opened it, where each card's own menu used to open.
        toggleListTweetMenu(button) {
            if (Date.now() < this.tweetMenuClickBlockedUntil) return;
            const card = button.closest('.feed-card');
            const tweet = this.feedCardTweet(button);
            if (!card || !tweet) return;
            const key = `list:${card.dataset.tweetId}`;
            if (this.tweetMenuOpen === key) {
                this.closeTweetMenu(key);
                return;
            }
            const hidden = Boolean(this.tweets.find(item => String(item.tweet_id) === card.dataset.tweetId)?.hidden);
            this.listMenu = { key, tweet, hidden, ...this.listMenuPosition(button) };
            this.tweetMenuOpen = key;
            this.fullRawTweet(tweet);
            this.syncListMenuButton();
        },

        listMenuPosition(button) {
            const area = this.$refs?.feedArea?.getBoundingClientRect();
            const rect = button.getBoundingClientRect();
            return area ? { top: rect.top - area.top, right: area.right - rect.right } : { top: 0, right: 0 };
        },

        // Keep the open list menu on its button when cards above it change height.
        placeListMenu() {
            if (!listMenuButton?.isConnected || this.tweetMenuOpen !== this.listMenu.key) return;
            const { top, right } = this.listMenuPosition(listMenuButton);
            if (top !== this.listMenu.top) this.listMenu.top = top;
            if (right !== this.listMenu.right) this.listMenu.right = right;
        },

        // The open menu covers its button, which is hidden and marked expanded meanwhile.
        syncListMenuButton() {
            const open = this.tweetMenuOpen && this.tweetMenuOpen === this.listMenu.key
                ? this.feedCard(this.tweetMenuOpen.slice('list:'.length))?.querySelector('[data-action="menu"]') || null
                : null;
            if (open === listMenuButton) return;
            outsideAlpine(() => {
                listMenuButton?.classList.remove('opacity-0');
                listMenuButton?.setAttribute('aria-expanded', 'false');
                open?.classList.add('opacity-0');
                open?.setAttribute('aria-expanded', 'true');
            });
            listMenuButton = open;
        },

        fullRawTweet(tweet) {
            const tweetId = this.getTweetId(tweet);
            if (this.isDemo || typeof globalThis.fetch !== 'function' || !/^\d+$/.test(tweetId || '')) {
                return Promise.resolve(null);
            }
            if (!rawTweetRequests.has(tweetId)) {
                rawTweetRequests.set(tweetId, globalThis.fetch(`/api/tweets/${tweetId}/raw`)
                    .then(response => (response.ok ? response.json() : null))
                    .then(data => data?.raw_json || null)
                    .catch(() => null)
                    .then(raw => {
                        if (raw) rawTweetValues.set(tweetId, raw);
                        else rawTweetRequests.delete(tweetId);
                        return raw;
                    }));
                if (rawTweetRequests.size > 50) {
                    const oldest = rawTweetRequests.keys().next().value;
                    rawTweetRequests.delete(oldest);
                    rawTweetValues.delete(oldest);
                }
            }
            return rawTweetRequests.get(tweetId);
        },

        closeTweetMenu(menuKey = null) {
            if (!menuKey || this.tweetMenuOpen === menuKey) this.tweetMenuOpen = null;
            this.syncListMenuButton();
        },

        startTweetMenuPress(event, tweetId, tagsData) {
            if (event?.pointerType === 'mouse' && event.button !== 0) return;
            this.cancelTweetMenuPress();
            this.tweetMenuPressTimer = setTimeout(() => {
                this.tweetMenuPressTimer = null;
                this.tweetMenuClickBlockedUntil = Date.now() + 750;
                this.openTagModal(tweetId, tagsData);
            }, 1000);
        },

        cancelTweetMenuPress() {
            if (this.tweetMenuPressTimer !== null) {
                clearTimeout(this.tweetMenuPressTimer);
                this.tweetMenuPressTimer = null;
            }
        },

        openTagsFromTweetMenu(tweetId, tagsData) {
            this.closeTweetMenu();
            this.openTagModal(tweetId, tagsData);
        },

        copyTextWithLegacyFallback(text) {
            const textarea = document.createElement('textarea');
            textarea.value = text;
            textarea.setAttribute('readonly', '');
            textarea.style.position = 'fixed';
            textarea.style.top = '0';
            textarea.style.left = '-9999px';
            textarea.style.opacity = '0';
            document.body.appendChild(textarea);
            try {
                textarea.focus();
                textarea.select();
                if (!document.execCommand('copy')) {
                    throw new Error('Legacy clipboard command failed');
                }
            } finally {
                document.body.removeChild(textarea);
            }
        },

        async copyRawTweet(tweet) {
            this.closeTweetMenu();
            const tweetId = this.getTweetId(tweet);
            const raw = rawTweetValues.get(tweetId) || (await this.fullRawTweet(tweet)) || tweet?.raw_json;
            if (!raw) {
                alert('No raw tweet data is available.');
                return;
            }
            const rawText = JSON.stringify(raw, null, 2);
            try {
                if (globalThis.navigator?.clipboard?.writeText) {
                    try {
                        await globalThis.navigator.clipboard.writeText(rawText);
                        return;
                    } catch (error) {
                        console.warn('Clipboard API copy failed; trying fallback', error);
                    }
                }
                this.copyTextWithLegacyFallback(rawText);
            } catch (error) {
                console.error('Could not copy raw tweet data', error);
                alert('Could not copy raw tweet data.');
            }
        },

        openTagModal(tweetId, tagsData) {
            const normalizedData = {
                description: typeof tagsData?.description === 'string' ? tagsData.description : '',
                tags: Array.isArray(tagsData?.tags) ? [...tagsData.tags] : [],
            };
            if (this._tagModalCloseTimer) clearTimeout(this._tagModalCloseTimer);
            this.closeTweetMenu();
            this.tagModalTweetId = tweetId;
            this.tagModalData = normalizedData;
            this.isEditingTags = false;
            this.editableTags = [...normalizedData.tags];
            this.editableDescription = normalizedData.description;
            this.tagModalOpen = true;
            document.body.style.overflow = 'hidden';
        },
        
        closeTagModal() {
            this.tagModalOpen = false;
            this._tagModalCloseTimer = setTimeout(() => {
                this.tagModalData = null;
                this.tagModalTweetId = null;
                this.isEditingTags = false;
                this.editableTags = [];
                this.editableDescription = '';
                this.tagSearchDropdown = false;
                document.body.style.overflow = '';
                this._tagModalCloseTimer = null;
            }, 300);
        },

        // Calls update for every loaded copy of a tweet: the feed, quote lists, and threads.
        updateLoadedTweet(tweetId, update) {
            const assign = tweet => {
                if (tweet && tweet.tweet_id === tweetId) update(tweet);
            };
            const visitThread = thread => {
                if (!thread) return;
                assign(thread.main);
                for (const tweet of thread.parents || []) assign(tweet);
                for (const tweet of [...(thread.children || []), ...(thread.other_children || [])]) {
                    assign(tweet);
                    for (const reply of tweet.op_replies || []) assign(reply);
                }
            };

            for (const tweet of this.tweets) assign(tweet);
            for (const tweet of this.quotesList) assign(tweet);
            for (const tweet of this.panelQuotesList) assign(tweet);
            visitThread(this.threadData);
            visitThread(this.panelThreadData);
            for (const entry of this.panelStack) {
                if (entry.type === 'thread') visitThread(entry.data);
                else if (entry.type === 'quotes') {
                    for (const tweet of entry.data || []) assign(tweet);
                }
            }
        },

        updateTweetTagData(tweetId, tagData) {
            this.updateLoadedTweet(tweetId, tweet => {
                tweet.media_tags = tagData ? {
                    description: tagData.description,
                    tags: [...tagData.tags],
                } : null;
            });
        },

        // A query that names is:hidden (not -is:hidden) lists hidden tweets.
        searchShowsHidden() {
            return /(^|\s)is:hidden(?=\s|$)/i.test(this.searchQuery);
        },

        async setTweetHidden(tweetId, hidden) {
            this.closeTweetMenu();
            if (!tweetId) return;
            try {
                const res = await fetch(`/api/tweets/${encodeURIComponent(tweetId)}/hidden`, {
                    method: hidden ? 'PUT' : 'DELETE',
                });
                if (!res.ok) throw new Error(`Server error: ${res.status}`);
            } catch (e) {
                alert(`Could not ${hidden ? 'hide' : 'unhide'} the tweet: ${e.message}`);
                return;
            }
            // Cached pages and threads predate the change.
            feedPages.clear();
            this.threadCache.clear();
            this.updateLoadedTweet(tweetId, tweet => { tweet.hidden = hidden; });
            if (hidden !== this.searchShowsHidden()) this.removeFeedTweet(tweetId);
        },

        // Drop a card from the current list without reloading it.
        removeFeedTweet(tweetId) {
            if (!this.tweets.some(tweet => tweet.tweet_id === tweetId)) return;
            this.tweets = this.tweets.filter(tweet => tweet.tweet_id !== tweetId);
            const card = this.feedCard(tweetId);
            if (card) outsideAlpine(() => card.remove());
            if (this.total) this.total -= 1;
        },
        
        async deleteTags() {
            if (!this.tagModalTweetId || !confirm("Are you sure you want to delete the tags for this tweet?")) return;
            try {
                const res = await fetch(`/api/tags/${this.tagModalTweetId}`, { method: 'DELETE' });
                if (res.ok) {
                    this.updateTweetTagData(this.tagModalTweetId, null);
                    this.closeTagModal();
                } else {
                    alert("Failed to delete tags.");
                }
            } catch (e) {
                alert("Error: " + e.message);
            }
        },
        
        startEditingTags() {
            this.isEditingTags = true;
            this.editableTags = [...(this.tagModalData?.tags || [])];
            this.editableDescription = this.tagModalData?.description || '';
        },
        async fetchTagAutocomplete(query) {
            try {
                const res = await fetch(`/api/tags/autocomplete?q=${encodeURIComponent(query)}`);
                if(res.ok) {
                    const data = await res.json();
                    this.tagAutocompleteOptions = data.tags;
                }
            } catch(e) {}
        },
        addEditableTag(tag) {
            const trimmed = tag.trim();
            if(trimmed && !this.editableTags.find(t => t.toLowerCase() === trimmed.toLowerCase())) {
                this.editableTags.push(trimmed);
            }
            this.tagSearchQuery = '';
        },
        async saveTags() {
            try {
                const description = this.editableDescription.trim();
                const res = await fetch(`/api/tags/${this.tagModalTweetId}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ tags: this.editableTags, description })
                });
                if (res.ok) {
                    const savedData = this.editableTags.length || description ? {
                        description,
                        tags: [...this.editableTags],
                    } : null;
                    this.updateTweetTagData(this.tagModalTweetId, savedData);
                    if (savedData) {
                        this.tagModalData = savedData;
                        this.isEditingTags = false;
                    } else {
                        this.closeTagModal();
                    }
                } else {
                    alert("Failed to save tags.");
                }
            } catch(e) {
                alert("Error saving tags: " + e.message);
            }
        },
        
        async fetchGlobalTags() {
            this.globalTagsLoading = true;
            try {
                const res = await fetch('/api/tags/stats');
                if (res.ok) {
                    const data = await res.json();
                    this.globalTags = data.tags;
                }
            } catch(e) { console.error(e); }
            finally { this.globalTagsLoading = false; }
        },
        
        get filteredGlobalTags() {
            if(!this.tagSearchTerm) return this.globalTags;
            const term = this.tagSearchTerm.toLowerCase();
            return this.globalTags.filter(t => t.tag.toLowerCase().includes(term));
        },
        
        async deleteGlobalTag(tag) {
            if(!confirm(`Are you sure you want to permanently delete the tag "${tag}" from all tweets?`)) return;
            try {
                const res = await fetch(`/api/tags/global/${encodeURIComponent(tag)}`, { method: 'DELETE' });
                if(res.ok) {
                    this.globalTags = this.globalTags.filter(t => t.tag !== tag);
                } else {
                    alert("Failed to delete global tag");
                }
            } catch(e) {
                alert("Error: " + e.message);
            }
        },
        
        get filteredMergeOptions() {
            let opts = this.globalTags.filter(t => !this.mergeTagsList.includes(t.tag) && t.tag !== this.mergePrimaryTag);
            if(this.mergeSearchTerm) {
                const q = this.mergeSearchTerm.toLowerCase();
                opts = opts.filter(t => t.tag.toLowerCase().includes(q));
            }
            return opts.slice(0, 10);
        },
        
        get filteredPrimaryOptions() {
            let opts = this.globalTags.filter(t => !this.mergeTagsList.includes(t.tag));
            if(this.mergePrimarySearchTerm) {
                const q = this.mergePrimarySearchTerm.toLowerCase();
                opts = opts.filter(t => t.tag.toLowerCase().includes(q));
            }
            return opts.slice(0, 10);
        },
        
        mergeMoveUp() {
            if (this.mergeSelectedIndex > 0) this.mergeSelectedIndex--;
        },
        
        mergeMoveDown() {
            if (this.mergeSelectedIndex < this.filteredMergeOptions.length - 1) this.mergeSelectedIndex++;
        },
        
        primaryMoveUp() {
            if (this.primarySelectedIndex > 0) this.primarySelectedIndex--;
        },
        
        primaryMoveDown() {
            if (this.primarySelectedIndex < this.filteredPrimaryOptions.length - 1) this.primarySelectedIndex++;
        },

        addMergeTag(tag) {
            if(!this.mergeTagsList.includes(tag)) {
                this.mergeTagsList.push(tag);
            }
            this.$nextTick(() => { this.mergeTagsDropdown = true; });
        },
        
        addMergeTagFromInput() {
            if(!this.mergeSearchTerm || this.filteredMergeOptions.length === 0) return;
            const tag = this.filteredMergeOptions[this.mergeSelectedIndex]?.tag || this.filteredMergeOptions[0].tag;
            this.addMergeTag(tag);
            this.mergeTagsDropdown = true;
            this.mergeSelectedIndex = 0;
        },
        
        setPrimaryTagFromInput() {
            if(!this.mergePrimarySearchTerm || this.filteredPrimaryOptions.length === 0) return;
            const tag = this.filteredPrimaryOptions[this.primarySelectedIndex]?.tag || this.filteredPrimaryOptions[0].tag;
            this.mergePrimaryTag = tag;
            this.mergePrimarySearchTerm = '';
            this.mergePrimaryDropdown = false;
        },
        
        removeMergeTag(tag) {
            this.mergeTagsList = this.mergeTagsList.filter(t => t !== tag);
        },
        
        getMergePath(index, total) {
            if (total === 1) return 'M 0 50 L 100 50';
            const startY = ((index + 0.5) / total) * 100;
            return `M 0 ${startY} C 35 ${startY}, 35 50, 75 50 L 100 50`;
        },
        
        async submitMerge() {
            if(!this.mergePrimaryTag || this.mergeTagsList.length === 0) return;
            if(!confirm(`Merge ${this.mergeTagsList.length} tags into "${this.mergePrimaryTag}"? This cannot be undone.`)) return;
            
            try {
                const res = await fetch('/api/tags/merge', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ primary_tag: this.mergePrimaryTag, merge_tags: this.mergeTagsList })
                });
                if (res.ok) {
                    alert("Tags merged successfully!");
                    this.mergeTagsList = [];
                    this.mergePrimaryTag = '';
                    this.mergePrimarySearchTerm = '';
                    this.fetchGlobalTags();
                } else {
                    alert("Failed to merge tags.");
                }
            } catch(e) {
                alert("Error merging tags: " + e.message);
            }
        },

        getReplyTo(tweet) {
            if (!tweet || !tweet.raw_json) return null;
            let raw = tweet.raw_json.raw_json || tweet.raw_json;
            if (raw.legacy && raw.legacy.in_reply_to_screen_name) return raw.legacy.in_reply_to_screen_name;
            if (raw.in_reply_to_screen_name) return raw.in_reply_to_screen_name;
            return null;
        },

        getReplyRecipients(tweet) {
            if (!tweet) return [];
            const raw = tweet.raw_json?.raw_json || tweet.raw_json || tweet;
            const legacy = raw?.legacy || raw || {};
            const primary = this.getReplyTo({ raw_json: raw });
            const text = legacy.full_text;
            const range = legacy.display_text_range;
            const validRange = Array.isArray(range) && range.length === 2
                && Number.isInteger(range[0]) && Number.isInteger(range[1])
                && range[0] >= 0 && range[1] >= range[0]
                && typeof text === 'string' && range[1] <= text.length;
            const prefixEnd = validRange ? range[0] : null;
            const knownReply = Boolean(primary || legacy.in_reply_to_status_id_str || raw?.in_reply_to_status_id_str);
            const recipients = [];
            const seen = new Set();
            const add = name => {
                if (typeof name !== 'string' || !name) return;
                const key = name.toLowerCase();
                if (seen.has(key)) return;
                seen.add(key);
                recipients.push(name);
            };
            if (knownReply && validRange && prefixEnd > 0) {
                const mentionNamesByStart = new Map();
                const mentions = legacy.entities?.user_mentions;
                if (Array.isArray(mentions)) {
                    for (const mention of [...mentions].sort((a, b) => (a?.indices?.[0] ?? 0) - (b?.indices?.[0] ?? 0))) {
                        const [start, end] = Array.isArray(mention?.indices) ? mention.indices : [];
                        if (Number.isInteger(start) && Number.isInteger(end)
                            && start >= 0 && end <= prefixEnd && end > start
                            && text.slice(start, end).startsWith('@') && typeof mention.screen_name === 'string') {
                            mentionNamesByStart.set(start, mention.screen_name);
                        }
                    }
                }
                // The captured prefix is the boundary; entities refine handle
                // casing when present, while this also covers missing indices.
                for (const match of text.slice(0, prefixEnd).matchAll(/@([\w]+)/g)) {
                    add(mentionNamesByStart.get(match.index) || match[1]);
                }
            } else if (knownReply && !validRange && typeof text === 'string') {
                // Older archives lack display_text_range; only a known reply may
                // use the legacy leading-mention fallback.
                for (const match of text.matchAll(/^(@([\w]+)\s+)+/g)) {
                    for (const handle of match[0].matchAll(/@([\w]+)/g)) add(handle[1]);
                }
            }
            if (primary) add(primary);
            return recipients;
        },

        renderReplyRecipients(tweet, className = '') {
            const recipients = this.getReplyRecipients(tweet);
            if (!recipients.length) return '';
            return `<div class="${className}tweet-reply-context text-[15px] text-[var(--text-secondary)] mb-1">${this.renderReplyRecipientContent(tweet)}</div>`;
        },

        renderReplyRecipientContent(tweet) {
            const recipients = this.getReplyRecipients(tweet);
            if (!recipients.length) return '';
            const escape = value => this.escapeHTML(value);
            const links = recipients.map(username =>
                `<span class="text-[var(--accent-color)] hover:underline cursor-pointer" data-action="reply-profile" data-username="${escape(username)}">${escape(`@${username}`)}</span>`
            );
            let names = links[0] || '';
            if (links.length === 2) names += ` and ${links[1]}`;
            else if (links.length > 2) names = `${links.slice(0, -1).join(' ')} and ${links.at(-1)}`;
            return `Replying to ${names}`;
        },

        handleReplyRecipientClick(event) {
            const target = event.target.closest?.('[data-action="reply-profile"][data-username]');
            if (!target) return;
            event.stopPropagation();
            this.openUsernameProfileCard({ currentTarget: target }, target.dataset.username);
        },

        nestedReplyIndent(tweet) {
            const depth = Number(tweet?.thread_depth);
            if (!Number.isFinite(depth)) return 0;
            return Math.min(Math.max(depth - 2, 0), 6) * 12;
        },

        formatDate(dateStr, includeTime = false) {
            if (!dateStr) return '';
            const d = new Date(dateStr);
            if (includeTime) {
                return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }) + ' · ' + 
                       d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
            }
            const now = new Date();
            const diff = (now - d) / 1000;
            if (diff < 60) return 'Just now';
            if (diff < 3600) return Math.floor(diff/60) + 'm';
            if (diff < 86400) return Math.floor(diff/3600) + 'h';
            const options = { month: 'short', day: 'numeric' };
            if (d.getFullYear() !== now.getFullYear()) options.year = 'numeric';
            return d.toLocaleDateString('en-US', options);
        },
        
        formatRelativeDate(dateStr) {
            if (!dateStr) return '';
            const d = new Date(dateStr);
            const now = new Date();
            const diff = (now - d) / 1000;
            if (diff < 60) return 'just now';
            if (diff < 3600) {
                const m = Math.floor(diff/60);
                return m === 1 ? '1 min ago' : m + ' mins ago';
            }
            if (diff < 86400) {
                const h = Math.floor(diff/3600);
                return h === 1 ? '1 hr ago' : h + ' hrs ago';
            }
            const days = Math.floor(diff/86400);
            if (days < 30) return days === 1 ? '1 day ago' : days + ' days ago';
            if (days < 365) {
                const mo = Math.floor(days/30);
                return mo === 1 ? '1 mo ago' : mo + ' mos ago';
            }
            const y = Math.floor(days/365);
            return y === 1 ? '1 yr ago' : y + ' yrs ago';
        },

        automatedTaggingSpendPeriodDays(period) {
            return {
                day: 1,
                week: 7,
                month: 30,
                year: 365,
            }[period] || 1;
        },

        syncAutomatedTaggingSpendState(resetPeriod = true) {
            if (!this.automatedTagging) return;
            if (resetPeriod || !this.automatedTaggingSpendPeriod) {
                this.automatedTaggingSpendPeriod = 'day';
            }
            const daily = Number(this.automatedTagging.daily_spend_limit_usd);
            this.automatedTaggingSpendAmount = Number.isFinite(daily) && daily > 0
                ? Number((daily * this.automatedTaggingSpendPeriodDays(this.automatedTaggingSpendPeriod)).toFixed(6))
                : null;
        },

        setAutomatedTaggingSpendPeriod(period) {
            if (!this.automatedTagging) return;
            this.automatedTaggingSpendPeriod = period;
            const daily = Number(this.automatedTagging.daily_spend_limit_usd);
            this.automatedTaggingSpendAmount = Number.isFinite(daily) && daily > 0
                ? Number((daily * this.automatedTaggingSpendPeriodDays(period)).toFixed(6))
                : null;
        },

        setAutomatedTaggingSpendAmount(rawAmount) {
            if (!this.automatedTagging) return;
            if (rawAmount === '') {
                this.automatedTaggingSpendAmount = null;
                this.automatedTagging.daily_spend_limit_usd = null;
                return;
            }
            const amount = Number(rawAmount);
            this.automatedTaggingSpendAmount = Number.isFinite(amount) ? amount : null;
            this.automatedTagging.daily_spend_limit_usd = Number.isFinite(amount) && amount > 0
                ? Number((amount / this.automatedTaggingSpendPeriodDays(this.automatedTaggingSpendPeriod)).toFixed(6))
                : null;
        },

        openAutomatedTaggingSpendHistory() {
            this.automatedTaggingSpendRange = '30d';
            this.showAutomatedTaggingSpendModal = true;
            this.$nextTick(() => this.$refs.automatedTaggingSpendClose?.focus());
        },

        closeAutomatedTaggingSpendHistory() {
            this.showAutomatedTaggingSpendModal = false;
            this.$nextTick(() => this.$refs.automatedTaggingSpendButton?.focus());
        },

        automatedTaggingSpendRangeDays() {
            if (this.automatedTaggingSpendRange === 'lifetime') return null;
            return {
                '7d': 7,
                '30d': 30,
                '3m': 90,
                '1y': 365,
            }[this.automatedTaggingSpendRange] || 30;
        },

        automatedTaggingSpendHistory() {
            if (this.automatedTaggingSpendRange === 'lifetime') {
                return this.automatedTaggingAccounting?.lifetime_spend_history || [];
            }
            const history = this.automatedTaggingAccounting?.spend_history || [];
            return history.slice(-this.automatedTaggingSpendRangeDays());
        },

        automatedTaggingSpendChartData() {
            const history = this.automatedTaggingSpendHistory();
            const groupSize = history.length > 60 ? Math.ceil(history.length / 52) : 1;
            const grouped = [];
            for (let index = 0; index < history.length; index += groupSize) {
                const items = history.slice(index, index + groupSize);
                grouped.push({
                    date: items[0]?.date,
                    endDate: items.at(-1)?.date,
                    interval: items[0]?.interval || 'day',
                    estimated_cost_usd: items.reduce(
                        (sum, item) => sum + Number(item.estimated_cost_usd || 0),
                        0,
                    ),
                    requests: items.reduce((sum, item) => sum + Number(item.requests || 0), 0),
                });
            }
            return grouped;
        },

        automatedTaggingSpendRangeTotal() {
            const total = this.automatedTaggingSpendHistory().reduce(
                (sum, item) => sum + Number(item.estimated_cost_usd || 0),
                0,
            );
            return Number(total.toFixed(12));
        },

        automatedTaggingSpendRangeRequests() {
            return this.automatedTaggingSpendHistory().reduce(
                (sum, item) => sum + Number(item.requests || 0),
                0,
            );
        },

        automatedTaggingSpendBarHeight(item) {
            const maximum = Math.max(
                0,
                ...this.automatedTaggingSpendChartData().map(point => Number(point.estimated_cost_usd || 0)),
            );
            const value = Number(item?.estimated_cost_usd || 0);
            if (maximum <= 0 || value <= 0) return '0%';
            return `${Math.max((value / maximum) * 100, 3)}%`;
        },

        automatedTaggingSpendModels() {
            return Object.entries(this.automatedTaggingAccounting?.models || {})
                .map(([model, values]) => ({
                    model,
                    requests: Number(values.requests || 0),
                    estimated_cost_usd: Number(
                        values.estimated_cost_usd ?? values.cost_usd ?? 0,
                    ),
                }))
                .sort((left, right) => (
                    right.estimated_cost_usd - left.estimated_cost_usd
                    || right.requests - left.requests
                    || left.model.localeCompare(right.model)
                ));
        },

        automatedTaggingSpendTweetTypes() {
            const labels = {
                text_only: 'Text only',
                image: 'Image',
                video: 'Video',
                gif: 'GIF',
                mixed_media: 'Mixed media',
                other_media: 'Other media',
            };
            const order = Object.keys(labels);
            return Object.entries(this.automatedTaggingAccounting?.tweet_types || {})
                .map(([type, values]) => {
                    const cost = Number(values.estimated_cost_usd ?? values.cost_usd ?? 0);
                    const pricedTweets = Number(values.priced_tweets || 0);
                    const pricedRequests = Number(values.priced_requests || 0);
                    return {
                        type,
                        label: labels[type] || type,
                        requests: Number(values.requests || 0),
                        tweets: Number(values.tweets || 0),
                        media_items: Number(values.media_items || 0),
                        image_items: Number(values.image_items || 0),
                        video_items: Number(values.video_items || 0),
                        gif_items: Number(values.gif_items || 0),
                        estimated_cost_usd: cost,
                        average_tweet_usd: pricedTweets ? cost / pricedTweets : 0,
                        average_request_usd: pricedRequests ? cost / pricedRequests : 0,
                    };
                })
                .sort((left, right) => {
                    const leftIndex = order.indexOf(left.type);
                    const rightIndex = order.indexOf(right.type);
                    return (leftIndex < 0 ? order.length : leftIndex)
                        - (rightIndex < 0 ? order.length : rightIndex);
                });
        },

        automatedTaggingTweetTypeMediaLabel(row) {
            const parts = [
                [row?.image_items, 'image'],
                [row?.video_items, 'video'],
                [row?.gif_items, 'GIF'],
            ]
                .filter(([count]) => Number(count || 0) > 0)
                .map(([count, label]) => `${Number(count).toLocaleString()} ${label}${Number(count) === 1 ? '' : 's'}`);
            return parts.join(' · ') || (row?.media_items ? 'Unclassified media' : 'No media');
        },

        formatAutomatedTaggingUsd(value) {
            const amount = Number(value || 0);
            if (!Number.isFinite(amount) || amount === 0) return '$0.00';
            if (amount < 0.01) return `$${amount.toFixed(4)}`;
            return `$${amount.toFixed(2)}`;
        },

        formatAutomatedTaggingSpendDate(value, includeYear = false, monthOnly = false) {
            if (!value) return '';
            return new Date(`${value}T00:00:00Z`).toLocaleDateString('en-US', {
                month: 'short',
                day: monthOnly ? undefined : 'numeric',
                year: includeYear ? 'numeric' : undefined,
                timeZone: 'UTC',
            });
        },

        automatedTaggingSpendBarLabel(item) {
            const monthOnly = item?.interval === 'month';
            const first = this.formatAutomatedTaggingSpendDate(item?.date, monthOnly, monthOnly);
            const last = this.formatAutomatedTaggingSpendDate(item?.endDate, monthOnly, monthOnly);
            const dates = first === last ? first : `${first}–${last}`;
            return `${dates}: ${this.formatAutomatedTaggingUsd(item?.estimated_cost_usd)} across ${item?.requests || 0} requests`;
        },
        
        async fetchAutomatedTagging() {
            try {
                const response = await fetch('/api/automated-tagging');
                if (!response.ok) return;
                const data = await response.json();
                this.automatedTaggingInstalled = !!data.installed;
                this.automatedTagging = JSON.parse(JSON.stringify(data.values));
                this.automatedTaggingOriginal = JSON.parse(JSON.stringify(data.values));
                this.syncAutomatedTaggingSpendState();
                this.setAutomatedTaggingContextItems(data.values.tagging_context || []);
                this.setAutomatedTaggingInstructionItems(data.values.additional_instructions || '');
                if (this.automatedTagging.api_mode === 'free') {
                    this.automatedTagging.google_search = false;
                }
                this.automatedTaggingAccounting = data.accounting;
                if (!this.automatedTagging.api_key) {
                    this.automatedTaggingModels = [];
                    this.automatedTaggingModelCache = null;
                    this.automatedTaggingModelCacheKey = '';
                    this.automatedTaggingModelCacheExpiresAt = 0;
                }
            } catch (error) {
                console.error('Error fetching automated tagging settings', error);
            }
        },

        async loadAutomatedTaggingModels(forceRefresh = false) {
            if (!this.automatedTagging || this.automatedTaggingLoadingModels) return;
            const cacheKey = [
                this.automatedTagging.api_key || '',
                this.automatedTagging.api_mode,
                this.automatedTagging.processing_tier,
            ].join('|');
            if (
                !forceRefresh
                && this.automatedTaggingModelCacheKey === cacheKey
                && Date.now() < this.automatedTaggingModelCacheExpiresAt
            ) {
                this.automatedTaggingModels = [...(this.automatedTaggingModelCache || [])];
                return;
            }
            this.automatedTaggingLoadingModels = true;
            this.automatedTaggingError = '';
            try {
                const response = await fetch('/api/automated-tagging/models', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        api_key: this.automatedTagging.api_key,
                        api_mode: this.automatedTagging.api_mode,
                        processing_tier: this.automatedTagging.processing_tier,
                    }),
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.detail || 'Could not load Gemini models');
                this.automatedTaggingModels = data.models || [];
                this.automatedTaggingModelCache = [...this.automatedTaggingModels];
                this.automatedTaggingModelCacheKey = cacheKey;
                this.automatedTaggingModelCacheExpiresAt = Date.now() + 15 * 60 * 1000;
                const modelIds = this.automatedTaggingModels.map(model => model.id);
                if (!modelIds.includes(this.automatedTagging.model) && modelIds.length) {
                    this.automatedTagging.model = modelIds[0];
                }
            } catch (error) {
                this.automatedTaggingError = error.message;
            } finally {
                this.automatedTaggingLoadingModels = false;
            }
        },

        async saveAutomatedTagging() {
            if (!this.automatedTagging || this.automatedTaggingSaving) return;
            this.addAutomatedTaggingContext();
            this.addAutomatedTaggingInstruction();
            this.automatedTaggingSaving = true;
            this.automatedTaggingError = '';
            const values = JSON.parse(JSON.stringify(this.automatedTagging));
            values.tagging_context = [...this.automatedTaggingContextItems];
            values.additional_instructions = this.automatedTaggingInstructionItems.join('\n') || null;
            try {
                const response = await fetch('/api/automated-tagging', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ values }),
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.detail || 'Could not save settings');
                this.automatedTagging = JSON.parse(JSON.stringify(data.values));
                this.automatedTaggingOriginal = JSON.parse(JSON.stringify(data.values));
                this.syncAutomatedTaggingSpendState(false);
                this.setAutomatedTaggingContextItems(data.values.tagging_context || []);
                this.setAutomatedTaggingInstructionItems(data.values.additional_instructions || '');
                if (data.accounting) this.automatedTaggingAccounting = data.accounting;
            } catch (error) {
                this.automatedTaggingError = error.message;
            } finally {
                this.automatedTaggingSaving = false;
            }
        },

        setAutomatedTaggingContextItems(items) {
            const seen = new Set();
            this.automatedTaggingContextItems = (items || [])
                .map(value => String(value).trim())
                .filter(value => {
                    const folded = value.toLowerCase();
                    if (!value || seen.has(folded)) return false;
                    seen.add(folded);
                    return true;
                });
            this.automatedTaggingContext = this.automatedTaggingContextItems.join('\n');
            if (this.automatedTagging) {
                this.automatedTagging.tagging_context = [...this.automatedTaggingContextItems];
            }
        },

        setAutomatedTaggingApiMode(mode) {
            if (!this.automatedTagging) return;
            this.automatedTagging.api_mode = mode;
            this.automatedTagging.google_search = mode === 'paid';
        },

        addAutomatedTaggingContext() {
            const values = this.automatedTaggingContextInput
                .split(/[,\n]/)
                .map(value => value.trim())
                .filter(Boolean);
            if (!values.length) return;
            this.setAutomatedTaggingContextItems([
                ...this.automatedTaggingContextItems,
                ...values,
            ]);
            this.automatedTaggingContextInput = '';
        },

        handleAutomatedTaggingContextKeydown(event) {
            if (!event) return;
            if (event.key === 'Enter' || event.key === ',') {
                event.preventDefault();
                this.addAutomatedTaggingContext();
            } else if (event.key === 'Tab') {
                // Commit before the browser moves focus to the next control.
                this.addAutomatedTaggingContext();
            }
        },

        removeAutomatedTaggingContext(item) {
            this.setAutomatedTaggingContextItems(
                this.automatedTaggingContextItems.filter(value => value !== item)
            );
        },

        setAutomatedTaggingInstructionItems(value) {
            const source = Array.isArray(value)
                ? value
                : String(value || '').split(/\r?\n/);
            const seen = new Set();
            this.automatedTaggingInstructionItems = source
                .map(item => String(item).trim())
                .filter(item => {
                    const folded = item.toLowerCase();
                    if (!item || seen.has(folded)) return false;
                    seen.add(folded);
                    return true;
                });
            if (this.automatedTagging) {
                this.automatedTagging.additional_instructions = this.automatedTaggingInstructionItems.join('\n') || null;
            }
        },

        addAutomatedTaggingInstruction() {
            const values = this.automatedTaggingInstructionInput
                .split(/\r?\n/)
                .map(value => value.trim())
                .filter(Boolean);
            if (!values.length) return;
            this.setAutomatedTaggingInstructionItems([
                ...this.automatedTaggingInstructionItems,
                ...values,
            ]);
            this.automatedTaggingInstructionInput = '';
        },

        removeAutomatedTaggingInstruction(item) {
            this.setAutomatedTaggingInstructionItems(
                this.automatedTaggingInstructionItems.filter(value => value !== item)
            );
        },

        async lookupAutomatedTaggingTweet() {
            const tweetId = this.automatedTaggingTweetId.trim();
            this.automatedTaggingSelectedTweet = null;
            this.automatedTaggingTestResult = null;
            this.automatedTaggingTweetLookupError = '';
            if (!tweetId) {
                return;
            }
            if (!/^\d+$/.test(tweetId)) {
                this.automatedTaggingTweetLookupError = 'Enter a numeric archived tweet ID.';
                return;
            }
            this.automatedTaggingTweetLookingUp = true;
            try {
                const response = await fetch(`/api/automated-tagging/tweets?q=${encodeURIComponent(tweetId)}`);
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.detail || 'Could not load archived tweet');
                if (tweetId === this.automatedTaggingTweetId.trim()) {
                    const tweet = Array.isArray(data.tweets) ? data.tweets[0] : null;
                    if (tweet) {
                        this.automatedTaggingSelectedTweet = tweet;
                    } else {
                        this.automatedTaggingTweetLookupError = 'No archived tweet was found with that ID.';
                    }
                }
            } catch (error) {
                if (tweetId === this.automatedTaggingTweetId.trim()) {
                    this.automatedTaggingTweetLookupError = error.message;
                }
            } finally {
                if (tweetId === this.automatedTaggingTweetId.trim()) {
                    this.automatedTaggingTweetLookingUp = false;
                }
            }
        },

        async runAutomatedTaggingTest() {
            if (!this.automatedTagging || !this.automatedTaggingSelectedTweet || this.automatedTaggingTesting) return;
            this.addAutomatedTaggingContext();
            this.addAutomatedTaggingInstruction();
            this.automatedTaggingTesting = true;
            this.automatedTaggingTestResult = null;
            this.automatedTaggingTestError = '';
            const values = JSON.parse(JSON.stringify(this.automatedTagging));
            values.tagging_context = [...this.automatedTaggingContextItems];
            values.additional_instructions = this.automatedTaggingInstructionItems.join('\n') || null;
            try {
                const response = await fetch('/api/automated-tagging/test', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        tweet_id: this.automatedTaggingSelectedTweet.tweet_id,
                        values,
                    }),
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.detail || 'Test run failed');
                this.automatedTaggingTestResult = data;
                if (data.accounting) this.automatedTaggingAccounting = data.accounting;
                if (!data.success) {
                    this.automatedTaggingTestError = data.output || 'Gemini did not return a valid tagging result';
                }
            } catch (error) {
                this.automatedTaggingTestError = error.message;
            } finally {
                this.automatedTaggingTesting = false;
            }
        },

        async fetchConfig() {
            if (this.isDemo) return;
            try {
                const [resSchema, resConfig, resDefaults] = await Promise.all([
                    fetch('/api/config/schema'),
                    fetch('/api/config'),
                    fetch('/api/config/defaults')
                ]);
                if (resSchema.ok && resConfig.ok && resDefaults.ok) {
                    this.configSchema = await resSchema.json();
                    const config = await resConfig.json();
                    this.configDefaults = await resDefaults.json();
                    this.configData = JSON.parse(JSON.stringify(config.values));
                    this.configOriginal = JSON.parse(JSON.stringify(config.values));
                    this.configExplicit = config.explicit;
                }
            } catch (e) {
                console.error("Error fetching config", e);
            }
        },

        closeSettings() {
            this.showSettingsModal = false;
        },

        openSettings() {
            this.showSetupModal = false;
            this.showSettingsModal = true;
        },

        lockModalScroll() {
            if (this.settingsBodyOverflow === null) this.settingsBodyOverflow = document.body.style.overflow;
            document.body.style.overflow = 'hidden';
        },

        unlockModalScroll() {
            if (this.settingsBodyOverflow === null) return;
            document.body.style.overflow = this.settingsBodyOverflow;
            this.settingsBodyOverflow = null;
        },

        closeSetup() {
            if (this.setupForced) return;
            this.setupRerun = false;
            this.showSetupModal = false;
        },

        openSetup() {
            if (this.isDemo) return;
            if (this.setupData?.completed) this.setupRerun = true;
            this.showSetupModal = true;
            this.showSettingsModal = false;
            this.fetchSetup();
        },

        goToSetupStep(step) {
            const next = Number(step);
            if (![1, 2, 3].includes(next) || next === this.setupFlowStep) return;
            this.setupDirection = next < this.setupFlowStep ? 'back' : 'forward';
            this.setupFlowStep = next;
        },

        rerunSetup() {
            if (this.isDemo) return;
            this.setupFlowStep = 1;
            this.setupDirection = 'forward';
            this.setupRerun = true;
            this.settingsTab = 'setup';
            this.openSetup();
        },

        get setupProgressMessage() {
            const archive = this.setupData?.archive;
            if (!archive) return '';
            if (archive.initialization_state === 'running') return this.activity?.summary || 'Importing your archive locally in the background…';
            if (['failed', 'interrupted'].includes(archive.initialization_state)) return 'Archive setup needs attention. Retry the import or migration.';
            if (archive.enrichment_state === 'running') return 'Archive enrichment is running in the background.';
            if (archive.enrichment_state === 'waiting_for_auth') return 'Local import complete. Connect Twitter/X to start enrichment.';
            if (archive.enrichment_state === 'waiting_for_worker') return 'Enrichment will start when the active task finishes.';
            if (archive.ready) return 'Your archive is ready to browse.';
            return 'Start by importing your official Twitter/X archive.';
        },

        async fetchSetup() {
            if (this.isDemo) return;
            if (this.setupPolling || this.setupFinishing) return;
            this.setupPolling = true;
            try {
                const response = await fetch('/api/setup');
                if (!response.ok) throw new Error('Could not load setup');
                const data = await response.json();
                this.setupData = data;
                if (!this.setupAuth) this.setupAuth = JSON.parse(JSON.stringify(data.auth.values));
                this.archiveReady = !!data.archive.ready;
                this.setupForced = !!data.required;
                if (this.setupForced) {
                    this.showSetupModal = true;
                    this.showSettingsModal = false;
                }
                if (this.archiveReady && !this.setupForced && !this.archiveResourcesLoaded) {
                    this.archiveResourcesLoaded = true;
                    await Promise.all([this.fetchTweets(), this.fetchStats(),
                        this.fetchArchiveEnrichmentStatus(), this.fetchAutomatedTagging()]);
                    const pendingRoute = this.pendingRoute;
                    const pendingRouteState = this.pendingRouteState;
                    if (pendingRoute) {
                        this.pendingRoute = null;
                        this.pendingRouteState = null;
                        await this.applyRoute(pendingRoute, { state: pendingRouteState });
                    }
                }
            } catch (error) {
                this.setupError = error.message;
            } finally {
                this.setupPolling = false;
            }
        },

        async initializeSetupArchive(mode) {
            if (mode === 'empty' && !confirm('Importing your official Twitter/X archive first is recommended. A live sync may not recover all of your historical data. Create an empty archive?')) return;
            this.setupError = '';
            try {
                const response = await fetch(mode === 'empty' ? '/api/setup/archive/empty' : '/api/setup/migrate', { method: 'POST' });
                const data = await response.json();
                if (!response.ok) throw new Error(data.detail?.message || data.detail || 'Could not initialize archive');
                this.goToSetupStep(2);
                await this.fetchSetup();
                await this.fetchActivityStatus();
            } catch (error) { this.setupError = error.message; }
        },

        async finishSetup() {
            if (this.setupFinishing) return;
            this.setupFinishing = true;
            this.setupError = '';
            try {
                const body = { skip_auth: this.setupSkipAuth };
                if (!this.setupData?.web?.password_configured || this.setupPassword) {
                    body.password = this.setupPassword;
                    body.confirm_password = this.setupPasswordConfirm;
                }
                const response = await fetch('/api/setup/complete', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
                });
                const data = await response.json();
                if (!response.ok) throw new Error(data.detail?.message || data.detail || 'Could not finish Setup');
                this.setupPassword = '';
                this.setupPasswordConfirm = '';
                this.setupForced = false;
                this.setupRerun = false;
                if (data.reload) window.location.reload();
                else this.closeSetup();
            } catch (error) { this.setupError = error.message; }
            finally { this.setupFinishing = false; }
        },

        get noticeCount() {
            return this.notices.filter(n => !n.dismissed).length;
        },

        async fetchNotices() {
            try {
                const response = await fetch('/api/notices');
                if (response.ok) this.notices = (await response.json()).notices || [];
            } catch (_) { /* Retry on the next status poll. */ }
        },

        async dismissNotice(id) {
            const response = await fetch(`/api/notices/${encodeURIComponent(id)}/dismiss`, { method: 'POST' });
            if (response.ok) this.notices = this.notices.map(n => n.id === id ? {...n, dismissed: true} : n);
        },

        openNotice(notice) {
            if (notice.action === 'activity') {
                this.activityDrawerOpen = true;
                return;
            }
            if (notice.action === 'schedule') {
                this.settingsTab = 'schedule';
                this.fetchScheduleSettings();
            } else {
                this.openSetup();
                return;
            }
            this.openSettings();
        },

        async saveSetupAuth() {
            if (!this.setupAuth || this.setupSavingAuth || this.setupAuthSuccessPending) return false;
            this.setupSavingAuth = true;
            this.setupAuthSuccessPending = false;
            this.setupAuthResult = null;

            try {
                // The server validates these candidate values before committing them.
                const saveResponse = await fetch('/api/setup/auth', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(this.setupAuth),
                });

                if (!saveResponse.ok) {
                    const data = await saveResponse.json().catch(() => ({}));
                    throw new Error(data.detail || 'Could not save authentication');
                }

                const auth = await saveResponse.json();
                this.setupData.auth = auth;
                this.setupAuth = JSON.parse(JSON.stringify(auth.values));
                this.setupAuthSaved = !!auth.verified;
                this.setupSkipAuth = false;
                await this.fetchSetup();
                if (this.setupData.auth.verified) {
                    this.setupAuthResult = {
                        success: true,
                        message: this.setupData.archive.enrichment_state === 'running'
                            ? 'Connected successfully. Archive enrichment has started.'
                            : 'Connected successfully. Archive enrichment will start when the local import finishes.',
                    };
                    this.setupSavingAuth = false;
                    this.setupAuthSuccessPending = true;
                    await new Promise(resolve => setTimeout(resolve, 1000));
                    this.setupAuthSuccessPending = false;
                    this.goToSetupStep(3);
                    return true;
                }
                throw new Error('Authentication was not verified');

            } catch (error) {
                this.setupAuthResult = { success: false, message: 'Authentication failed, verify cookies', output: '' };
                return false;
            } finally {
                this.setupSavingAuth = false;
            }
        },

        async uploadSetupArchive(event) {
            const file = event.target.files?.[0];
            event.target.value = '';
            if (!file) return;
            if (!file.name.toLowerCase().endsWith('.zip')) {
                alert('Choose an archive.zip file.');
                return;
            }
            this.setupUploading = true;
            try {
                const response = await fetch('/api/setup/archive', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/zip' },
                    body: file,
                });
                if (!response.ok) {
                    const data = await response.json().catch(() => ({}));
                    throw new Error(data.detail || 'Could not upload archive');
                }
                this.setupData.archive = await response.json();
            } catch (error) {
                alert(error.message);
            } finally {
                this.setupUploading = false;
            }
        },

        async clearSetupArchive() {
            if (!confirm('Clear the uploaded archive.zip? Imported tweets will not be deleted.')) return;
            try {
                const response = await fetch('/api/setup/archive', { method: 'DELETE' });
                if (!response.ok) {
                    const data = await response.json().catch(() => ({}));
                    throw new Error(data.detail || 'Could not clear archive');
                }
                this.setupData.archive = await response.json();
            } catch (error) {
                alert(error.message);
            }
        },

        async importSetupArchive() {
            if (!this.setupData?.archive?.uploaded || this.setupStartingImport) return;
            this.setupStartingImport = true;
            this.activityError = null;
            try {
                const response = await fetch('/api/setup/archive/import', { method: 'POST' });
                if (!response.ok) {
                    const data = await response.json().catch(() => ({}));
                    throw new Error(data.detail || 'Could not start archive import');
                }
                this.goToSetupStep(2);
                await this.fetchSetup();
                await this.fetchActivityStatus();
            } catch (error) {
                this.activityError = error.message;
                alert(error.message);
            } finally {
                this.setupStartingImport = false;
            }
        },

        formatSetupBytes(value) {
            return this.formatActivityBytes(value);
        },
        
        isFieldVisible(section, key) {
            const fullKey = `${section}.${key}`;
            if (!this.configSchema || !this.configData) return false;
            if (this.configSchema.blacklist.includes(fullKey)) return false;
            if (this.configSchema.whitelist.includes(fullKey)) return true;
            return this.showAdvancedConfig;
        },
        
        hasVisibleFields(section) {
            if (!this.configSchema || !this.configData || !this.configData[section]) return false;
            return Object.keys(this.configData[section]).some(k => this.isFieldVisible(section, k));
        },
        
        getFieldType(section, key) {
            if (!this.configSchema || !this.configData) return 'text';
            const fullKey = `${section}.${key}`;
            if (this.configSchema.types && this.configSchema.types[fullKey]) return this.configSchema.types[fullKey];
            
            const val = this.configData[section][key];
            if (typeof val === 'boolean') return 'boolean';
            if (typeof val === 'number') return 'number';
            return 'text';
        },
        
        isFieldFullWidth(section, key) {
            const fullKey = `${section}.${key}`;
            if (this.configSchema && this.configSchema.full_width && this.configSchema.full_width.includes(fullKey)) return true;
            return false;
        },

        isConfigExplicit(section, key) {
            return this.configExplicit.includes(`${section}.${key}`);
        },

        changedConfigValues() {
            const changes = {};
            if (!this.configData || !this.configOriginal) return changes;
            Object.entries(this.configData).forEach(([section, fields]) => {
                Object.entries(fields).forEach(([key, value]) => {
                    const original = this.configOriginal[section][key];
                    if (JSON.stringify(value) !== JSON.stringify(original)) {
                        changes[`${section}.${key}`] = value;
                    }
                });
            });
            return changes;
        },

        async submitConfigChanges(changes) {
            if (Object.keys(changes).length === 0) return true;
            this.configSaving = true;
            try {
                const res = await fetch('/api/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ changes })
                });
                if (!res.ok) {
                    alert("Failed to save config: " + await res.text());
                    return false;
                }
                const config = await res.json();
                this.configData = JSON.parse(JSON.stringify(config.values));
                this.configOriginal = JSON.parse(JSON.stringify(config.values));
                this.configExplicit = config.explicit;
                return true;
            } catch (e) {
                console.error("Error saving config", e);
                alert("Error saving config.");
                return false;
            } finally {
                this.configSaving = false;
            }
        },
        
        async saveConfig() {
            const changes = this.changedConfigValues();
            if (Object.keys(changes).length === 0) return;
            if (await this.submitConfigChanges(changes)) {
                const btn = document.getElementById('save-config-btn');
                if (btn) {
                    const old = btn.innerHTML;
                    btn.innerHTML = "Saved!";
                    btn.classList.replace('bg-[var(--accent-color)]', 'bg-green-600');
                    setTimeout(() => {
                        btn.innerHTML = old;
                        btn.classList.replace('bg-green-600', 'bg-[var(--accent-color)]');
                    }, 2000);
                }
            }
        },

        async resetConfigField(section, key) {
            await this.submitConfigChanges({ [`${section}.${key}`]: null });
        },

        async restoreConfigDefaults() {
            if(!confirm("Are you sure you want to restore default configuration settings? Your credentials and keys will be preserved.")) return;
            const preserved = new Set(['tagging.api_key', 'web.password_hash']);
            const resettable = this.configExplicit.filter(path => (
                !path.startsWith('auth.') && !preserved.has(path)
            ));
            const changes = Object.fromEntries(resettable.map(path => [path, null]));
            await this.submitConfigChanges(changes);
        },

        formatSyncDate(tweet) {
            let synced = tweet.synced_at || (tweet.collection ? tweet.collection.synced_at : null);
            if (!synced) return 'Archived';
            let dS = new Date(synced);
            return `Synced ${dS.toLocaleDateString('en-US', {month:'short', day:'numeric'})}`;
        },

        getQuoteTweet(tweet) {
            if (!tweet || this.isPlaceholderTweet(tweet)) return null;
            if (tweet.quoted_tweet) return tweet.quoted_tweet;
            if (!tweet.raw_json) return null;
            const quote = tweet.raw_json.quoted_status_result?.result;
            if (quote?.__typename === 'TweetWithVisibilityResults') {
                return quote.birdwatch_pivot && quote.tweet
                    ? { ...quote.tweet, birdwatch_pivot: quote.birdwatch_pivot }
                    : quote.tweet;
            }
            if (quote?.__typename === 'Tweet') return quote;
            if (quote?.__typename === 'TweetTombstone' || quote?.__typename === 'TweetUnavailable') return quote;
            if (tweet.raw_json.quoted_status) return tweet.raw_json.quoted_status;
            return null;
        },

        isTombstone(qt) {
            if (!qt) return false;
            return this.isPlaceholderTweet(qt) || qt.__typename === 'TweetTombstone' || qt.__typename === 'TweetUnavailable' || qt.__tombstone__ === true;
        },

        isPlaceholderTweet(tweet) {
            const typename = tweet?.__typename || tweet?.__typename__;
            return Boolean(tweet?.availability?.placeholder)
                || typename === 'TweetTombstone'
                || typename === 'TweetUnavailable'
                || tweet?.__tombstone__ === true;
        },

        isTextPlaceholderTweet(tweet) {
            if (!tweet || this.isPlaceholderTweet(tweet)) return false;
            const raw = tweet.raw_json?.raw_json || tweet.raw_json || tweet;
            const text = tweet.text || raw?.legacy?.full_text || raw?.full_text || '';
            return Boolean(this.getTweetId(tweet)) && !String(text).trim();
        },

        renderTweetSkeleton(tweet, { compact = false, contentOnly = false } = {}) {
            const reasonKey = tweet?.availability?.reason
                || (this.isTombstone(tweet) ? 'unavailable_unknown' : 'details_not_archived');
            const fallbackMessages = {
                protected_account: 'This tweet is from a protected account.',
                suspended_account: 'This tweet is from a suspended account.',
                account_missing: 'This tweet is from an account that no longer exists.',
                deleted_by_author: 'This tweet was deleted by its author.',
                archive_deleted: 'This tweet was deleted from Twitter/X.',
                withheld: 'This tweet is unavailable in your location.',
                not_found: 'This tweet could not be found.',
                not_archived: 'This tweet was not captured in the local archive.',
                details_not_archived: 'Tweet details have not been archived yet.',
                text_not_archived: 'Tweet text was not captured in the local archive.',
                unavailable_unknown: 'This tweet is unavailable.',
            };
            const tombstoneMessage = this.isTombstone(tweet)
                ? tweet?.tombstone?.text?.text || tweet?.text
                : '';
            const message = this.escapeHTML(
                tweet?.availability?.message
                    || tombstoneMessage
                    || fallbackMessages[reasonKey]
                    || 'This tweet is unavailable.',
            );
            const reason = this.escapeHTML(
                reasonKey,
            );
            const tweetId = this.escapeHTML(this.getTweetId(tweet) || '');
            const label = compact ? 'Quoted tweet unavailable' : 'Tweet unavailable';
            const rootClasses = [
                'tweet-skeleton',
                compact ? 'tweet-skeleton-compact' : '',
                contentOnly ? 'tweet-skeleton-content' : '',
            ].filter(Boolean).join(' ');
            const meta = contentOnly ? '' : `
                <div class="tweet-skeleton-meta" aria-hidden="true">
                    <span class="tweet-skeleton-bar tweet-skeleton-name"></span>
                    <span class="tweet-skeleton-bar tweet-skeleton-handle"></span>
                    <span class="tweet-skeleton-bar tweet-skeleton-date"></span>
                </div>`;
            const lineClasses = compact
                ? ['tweet-skeleton-line-wide', 'tweet-skeleton-line-medium']
                : ['tweet-skeleton-line-wide', 'tweet-skeleton-line-medium', 'tweet-skeleton-line-short'];
            const lines = lineClasses.map(className => (
                `<span class="tweet-skeleton-bar tweet-skeleton-line ${className}"></span>`
            )).join('');
            const body = `
                <div class="tweet-skeleton-message">${message}</div>
                <div class="tweet-skeleton-lines" aria-hidden="true">${lines}</div>
                <div class="tweet-skeleton-actions" aria-hidden="true">
                    <span class="tweet-skeleton-bar tweet-skeleton-action"></span>
                    <span class="tweet-skeleton-bar tweet-skeleton-action"></span>
                    <span class="tweet-skeleton-bar tweet-skeleton-action"></span>
                    <span class="tweet-skeleton-bar tweet-skeleton-action"></span>
                </div>`;
            const avatar = contentOnly
                ? ''
                : '<span class="tweet-skeleton-avatar" aria-hidden="true"></span>';
            return `<div class="${rootClasses}" role="status" aria-label="${label}" data-tweet-skeleton="true" data-availability-reason="${reason}" data-tweet-id="${tweetId}"><span class="sr-only">${label}</span>${avatar}<div class="tweet-skeleton-main">${meta}${body}</div></div>`;
        },

        renderContentPlaceholder(message, reason = 'details_not_archived') {
            return this.renderTweetSkeleton(
                { availability: { placeholder: true, message, reason } },
                { contentOnly: true },
            );
        },

        renderTweetPlaceholder(tweet) {
            return this.renderTweetSkeleton(tweet, { contentOnly: true });
        },

        renderQuotePlaceholder(tweet) {
            return this.renderTweetSkeleton(tweet, { compact: true });
        },

        getRetweet(tweet) {
            if (!tweet || this.isPlaceholderTweet(tweet)) return null;
            if (tweet.retweeted_tweet) return tweet.retweeted_tweet;
            if (!tweet.raw_json) return null;
            const rt = tweet.raw_json.legacy?.retweeted_status_result?.result;
            if (rt?.__typename === 'TweetWithVisibilityResults') {
                return rt.birdwatch_pivot && rt.tweet
                    ? { ...rt.tweet, birdwatch_pivot: rt.birdwatch_pivot }
                    : rt.tweet;
            }
            if (rt?.__typename === 'Tweet') return rt;
            if (tweet.raw_json.retweeted_status) return tweet.raw_json.retweeted_status;
            return null;
        },

        formatRetweetToTweet(originalTweet, rt) {
            if (rt.author) {
                return {
                    ...rt,
                    synced_at: originalTweet.synced_at,
                    collection: originalTweet.collection,
                    collections: originalTweet.collections,
                    media_tags: rt.media_tags || originalTweet.media_tags,
                };
            }
            const author = this.getQuoteAuthor(rt);
            return {
                tweet_id: rt.rest_id || rt.id_str || originalTweet.tweet_id,
                author: { display_name: author.name, username: author.screen_name, id: author.id },
                text: this.getQuoteText(rt),
                created_at: rt.legacy?.created_at || rt.created_at || originalTweet.created_at,
                synced_at: originalTweet.synced_at,
                collection: originalTweet.collection,
                raw_json: rt,
                media: originalTweet.media,
                qt_media: originalTweet.qt_media
            };
        },

        getQuoteAuthor(qt) {
            if (!qt) return { name: 'Unknown', screen_name: 'unknown', id: 'x', initial: '?' };
            if (qt.author) {
                const name = qt.author.display_name || 'Unknown';
                return {
                    name,
                    screen_name: qt.author.username || 'unknown',
                    id: qt.author.id || 'x',
                    initial: name.charAt(0),
                };
            }
            const userResult = qt.core?.user_results?.result;
            let name = userResult?.legacy?.name || userResult?.core?.name || qt.user?.name || 'Unknown';
            let screen_name = userResult?.legacy?.screen_name || userResult?.core?.screen_name || qt.user?.screen_name || 'unknown';
            let id = userResult?.rest_id || qt.user?.id_str || 'x';
            return { name, screen_name, id, initial: name.charAt(0) };
        },

        getQuoteText(qt) {
            if (!qt) return '';
            if (this.isTombstone(qt)) {
                return qt.availability?.message || qt.tombstone?.text?.text || qt.text || "This tweet is unavailable.";
            }
            return qt.text || qt.legacy?.full_text || qt.full_text || '';
        },

        getTweetId(tweet) {
            return tweet?.tweet_id || tweet?.rest_id || tweet?.id_str || null;
        },

        decodeTweetText(text) {
            // Captured tweet text can contain entity escapes. Decode one layer
            // before HTML escaping; never parse tweet content as markup.
            const entities = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'" };
            return String(text).replace(/&(amp|lt|gt|quot|apos);/g, (_, name) => entities[name]);
        },

        searchMatchRanges(text) {
            const normalize = value => value.normalize('NFD').replace(/\p{M}/gu, '').toLowerCase();
            const words = value => Array.from(value.matchAll(/[\p{L}\p{N}\p{M}]+/gu), match => ({
                value: normalize(match[0]), start: match.index, end: match.index + match[0].length,
            }));
            const tokens = words(text);
            const ranges = [];
            for (const term of this.searchHighlightTerms) {
                const parts = words(term);
                if (!parts.length) continue;
                const prefix = !term.startsWith('"') && term.endsWith('*') && parts.length === 1;
                for (let i = 0; i <= tokens.length - parts.length; i++) {
                    if (!parts.every((part, j) => prefix
                        ? tokens[i + j].value.startsWith(part.value)
                        : tokens[i + j].value === part.value)) continue;
                    let start = tokens[i].start;
                    if (term.startsWith('#')) {
                        if (text[start - 1] !== '#') continue;
                        start -= 1;
                    }
                    ranges.push([start, tokens[i + parts.length - 1].end]);
                }
            }
            ranges.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
            const merged = [];
            for (const range of ranges) {
                const previous = merged[merged.length - 1];
                if (previous && range[0] <= previous[1]) previous[1] = Math.max(previous[1], range[1]);
                else merged.push(range.slice());
            }
            return merged;
        },

        highlightSearchText(text, highlight = true, ranges = null, baseOffset = 0) {
            text = String(text || '');
            const escape = value => value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
            if (!highlight || !this.searchHighlightTerms.length) return escape(text);
            ranges = ranges || this.searchMatchRanges(text);
            let html = '', offset = 0;
            for (const range of ranges) {
                const start = Math.max(0, range[0] - baseOffset);
                const end = Math.min(text.length, range[1] - baseOffset);
                if (start >= end) continue;
                html += escape(text.slice(offset, start)) + '<strong>' + escape(text.slice(start, end)) + '</strong>';
                offset = end;
            }
            return html + escape(text.slice(offset));
        },

        formatText(tweet, forceFull = false, highlightSearch = false) {
            if (!tweet) return '';
            if (this.isPlaceholderTweet(tweet)) return this.renderTweetPlaceholder(tweet);
            const raw = tweet.raw_json?.raw_json || tweet.raw_json || tweet;
            const rawLegacy = raw?.legacy || raw || {};
            let sourceText = tweet.text || rawLegacy.full_text || '';
            const rawSourceText = rawLegacy.full_text;
            const displayRange = rawLegacy.display_text_range;
            const validDisplayRange = Array.isArray(displayRange) && displayRange.length === 2
                && Number.isInteger(displayRange[0]) && Number.isInteger(displayRange[1])
                && displayRange[0] >= 0 && displayRange[1] >= displayRange[0]
                && typeof rawSourceText === 'string' && displayRange[1] <= rawSourceText.length;
            const isReply = Boolean(
                this.getReplyTo({ raw_json: raw })
                || rawLegacy.in_reply_to_status_id_str
                || raw?.in_reply_to_status_id_str
            );
            if (isReply && validDisplayRange) {
                if (sourceText.startsWith(rawSourceText.slice(0, displayRange[0])) && displayRange[0] > 0) {
                    sourceText = sourceText.slice(displayRange[0]);
                }
            } else if (isReply) {
                // Keep the historical behavior for archives without valid bounds.
                sourceText = sourceText.replace(/^(@\w+\s+)+/, '');
            }
            let text = this.decodeTweetText(sourceText);
            if (this.isTextPlaceholderTweet(tweet)) {
                return this.renderContentPlaceholder(
                    'Tweet text was not captured in the local archive.',
                    'text_not_archived',
                );
            }
            
            let isTruncated = false;
            let tweetId = tweet.tweet_id || (raw && raw.rest_id);
            
            if (!forceFull && text.length > 280 && tweetId && !this.expandedTweets[tweetId]) {
                let truncated = text.substring(0, 280);
                let lastSpace = truncated.lastIndexOf(' ');
                if (lastSpace > 200) {
                    truncated = truncated.substring(0, lastSpace);
                }
                text = truncated + '...';
                isTruncated = true;
            }
            
            let urlMap = Object.create(null);
            if (raw && raw.legacy) {
                const urlsToRemove = [];
                if (raw.legacy.entities?.media) urlsToRemove.push(...raw.legacy.entities.media.map(m => m.url));
                if (raw.legacy.extended_entities?.media) urlsToRemove.push(...raw.legacy.extended_entities.media.map(m => m.url));
                if (raw.legacy.quoted_status_permalink?.url) urlsToRemove.push(raw.legacy.quoted_status_permalink.url);
                
                if (raw.card && raw.card.legacy && raw.card.legacy.url) urlsToRemove.push(raw.card.legacy.url);
                if (raw.card && raw.card.url) urlsToRemove.push(raw.card.url);
                
                urlsToRemove.filter(Boolean).forEach(u => {
                    text = text.split(u).join('');
                });
                
                if (raw.legacy.entities?.urls) {
                    raw.legacy.entities.urls.forEach(u => {
                        if (u.url && !urlsToRemove.includes(u.url)) {
                            const expandedUrl = this.safeURL(u.expanded_url);
                            const displayUrl = this.highlightSearchText(u.display_url || u.expanded_url || '', highlightSearch);
                            urlMap[u.url] = `<a href="${expandedUrl}" target="_blank" rel="noopener noreferrer" class="text-[var(--accent-color)] hover:underline" @click.stop>${displayUrl}</a>`;
                        }
                    });
                }
            }
            
            // Render source text once. Query matches never touch generated markup,
            // link destinations, data attributes, or escaped entity spellings.
            const urlTokens = Object.keys(urlMap).map(value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
            const pattern = new RegExp([...urlTokens, '@\\w+', '#\\w+'].join('|'), 'g');
            const ranges = highlightSearch ? this.searchMatchRanges(text) : [];
            let html = '', offset = 0;
            for (const match of text.matchAll(pattern)) {
                html += this.highlightSearchText(text.slice(offset, match.index), highlightSearch, ranges, offset);
                const token = match[0];
                if (Object.hasOwn(urlMap, token)) html += urlMap[token];
                else {
                    const mention = token.startsWith('@');
                    html += `<span class="${mention ? 'mention' : 'hashtag'} text-[var(--accent-color)] hover:underline cursor-pointer" data-${mention ? 'user' : 'tag'}="${token.slice(1)}">${this.highlightSearchText(token, highlightSearch, ranges, match.index)}</span>`;
                }
                offset = match.index + token.length;
            }
            text = html + this.highlightSearchText(text.slice(offset), highlightSearch, ranges, offset);
            
            if (isTruncated) {
                text += `<span class="text-[var(--accent-color)] hover:underline cursor-pointer block mt-1 expand-tweet" data-id="${this.escapeHTML(tweetId)}">Show more</span>`;
            }
            
            return text.trim();
        },


        expandedTweets: {},

        // Returns whether the click hit a mention, hashtag, or Show more. Delegated
        // callers pass the text element, which anchors the profile card as the
        // handler's currentTarget does for an element's own listener.
        handleTextClick(e, textElement = e.currentTarget) {
            const target = e.target.closest?.('span.mention, span.hashtag, span.expand-tweet') || e.target;
            if (target.tagName !== 'SPAN' || !textElement?.contains(target)) return false;
            if (target.classList.contains('mention')) {
                e.stopPropagation();
                const username = target.dataset.user;
                const dummyTweet = {
                    author: { username: username, display_name: username, id: 'unknown' }
                };
                this.openProfileCard({ currentTarget: textElement }, dummyTweet);
            } else if (target.classList.contains('hashtag')) {
                e.stopPropagation();
                this.searchQuery = '#' + target.dataset.tag;
                this.search();
            } else if (target.classList.contains('expand-tweet')) {
                e.stopPropagation();
                const tid = target.dataset.id;
                if (tid) {
                    this.expandedTweets[tid] = true;
                    // Feed cards render outside Alpine; expand their copies directly.
                    this.expandFeedText(tid);
                }
            } else {
                return false;
            }
            return true;
        },
        
        renderCard(tweet) {
            if (this.isPlaceholderTweet(tweet)) return '';
            if (!tweet || !tweet.raw_json) return '';
            const raw = tweet.raw_json.raw_json || tweet.raw_json;
            const card = raw.card;
            if (!card) return '';
            const name = card.name || card.legacy?.name || '';
            const bindingArray = card.binding_values || card.legacy?.binding_values || [];
            const binding = {};
            if (Array.isArray(bindingArray)) {
                bindingArray.forEach(item => {
                    binding[item.key] = item.value;
                });
            } else {
                Object.assign(binding, bindingArray);
            }
            let html = '';

            if (name.startsWith('poll')) {
                const choices = [];
                for (let i = 1; i <= 4; i++) {
                    const choice = binding[`choice${i}_label`];
                    const count = binding[`choice${i}_count`];
                    if (choice && choice.string_value) {
                        choices.push({
                            label: choice.string_value,
                            count: count ? parseInt(count.string_value) : 0
                        });
                    }
                }
                if (choices.length > 0) {
                    const total = choices.reduce((s, c) => s + c.count, 0) || 1;
                    const trueTotal = choices.reduce((s, c) => s + c.count, 0);
                    const maxCount = Math.max(...choices.map(c => c.count));
                    const isFinal = binding.counts_are_final?.boolean_value === true;
                    
                    html += `<div class="mt-3 flex flex-col space-y-[6px]">`;
                    choices.forEach(c => {
                        const pct = Math.round((c.count / total) * 100);
                        const isWinner = c.count === maxCount && maxCount > 0;
                        const weightClass = isWinner ? 'font-bold' : 'font-normal';
                        const barColor = isWinner ? 'bg-[var(--accent-color)] opacity-[0.4]' : 'bg-[var(--border-color)] opacity-[0.6]';
                        const label = this.escapeHTML(c.label);
                        
                        html += `<div class="relative w-full h-[32px] rounded flex items-center overflow-hidden">
                                    <div class="absolute left-0 top-0 bottom-0 ${barColor} rounded" style="width: ${pct}%"></div>
                                    <span class="relative z-10 text-[15px] ${weightClass} text-[var(--text-primary)] w-full flex justify-between px-3">
                                        <span class="truncate pr-4" title="${label}">${label}</span>
                                        <span>${pct}%</span>
                                    </span>
                                 </div>`;
                    });
                    
                    const statusText = isFinal ? 'Final results' : '';
                    const dot = isFinal ? ' · ' : '';
                    html += `<div class="text-[14px] text-[var(--text-secondary)] mt-2">${trueTotal} votes${dot}${statusText}</div></div>`;
                }
            } else if (name === 'summary' || name === 'summary_large_image') {
                const title = this.escapeHTML(binding.title?.string_value || '');
                const desc = this.escapeHTML(binding.description?.string_value || '');
                const vanityUrl = this.escapeHTML(binding.vanity_url?.string_value || '');
                const expandedUrl = this.safeURL(
                    binding.card_url?.string_value || binding.vanity_url?.string_value || '#'
                );
                let imageHtml = '';
                
                if (name === 'summary_large_image') {
                    const imgUrl = binding.thumbnail_image_original?.image_value?.url || 
                                   binding.summary_photo_image_original?.image_value?.url || 
                                   binding.thumbnail_image_x_large?.image_value?.url ||
                                   binding.thumbnail_image?.image_value?.url || 
                                   binding.photo_image_full_size?.image_value?.url || 
                                   binding.summary_photo_image?.image_value?.url;
                    const localImage = this.localCardImage(tweet, imgUrl);
                    if (localImage) {
                        const safeImgUrl = this.escapeHTML(localImage);
                        imageHtml = `<div class="w-full aspect-[1.91/1] bg-[var(--bg-tertiary)] overflow-hidden border-b border-[var(--border-color)]">
                                        <img src="${safeImgUrl}" class="w-full h-full object-cover" loading="lazy" onerror="this.parentElement.hidden=true">
                                     </div>`;
                    }
                }
                
                if (title || desc || imageHtml) {
                    html += `<a href="${expandedUrl}" target="_blank" rel="noopener noreferrer" class="mt-3 block border border-[var(--border-color)] rounded-xl overflow-hidden hover-bg transition cursor-pointer">
                                ${imageHtml}
                                <div class="p-3">
                                    <div class="text-[13px] text-[var(--text-secondary)] mb-1">${vanityUrl}</div>
                                    <div class="text-[15px] text-[var(--text-primary)] font-bold leading-tight mb-1" style="display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;">${title}</div>
                                    <div class="text-[14px] text-[var(--text-secondary)]" style="display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;">${desc}</div>
                                </div>
                             </a>`;
                }
            }
            return html;
        },

        localCardImage(tweet, remoteUrl) {
            if (!remoteUrl) return null;
            // Use the same archived media/poster files as the media grid.
            const media = tweet.media || [];
            for (const item of media) {
                const download = item.download || {};
                const path = item.thumbnail_url === remoteUrl && download.thumbnail_local_path
                    ? download.thumbnail_local_path
                    : (item.url === remoteUrl || item.media_url === remoteUrl) && download.local_path;
                if (typeof path === 'string' && path.startsWith('media/') && !path.includes('..') && !path.includes('\\')) {
                    return '/' + path.split('/').map(encodeURIComponent).join('/');
                }
            }
            return null;
        },

        avatarUrl(userId) {
            if (this.isDemo) return window.TweetNookDemo?.avatarUrl(userId) || '';
            const encodedUserId = encodeURIComponent(userId || 'unknown');
            return '/api/avatar/' + encodedUserId + '?v=' + AVATAR_URL_VERSION;
        },

        avatarLoaded(image) {
            const valid = image.naturalWidth > 1 || image.naturalHeight > 1;
            image.classList.toggle('avatar-image-loaded', valid);
            image.parentElement?.classList.toggle('avatar-loaded', valid);
        },

        avatarFailed(image) {
            image.classList.remove('avatar-image-loaded');
            image.parentElement?.classList.remove('avatar-loaded');
        },

        formatMediaDuration(durationMillis) {
            return formatMediaDuration(durationMillis);
        },

        renderMediaPlaybackIndicator(item) {
            if (item.isGif) {
                return `<button type="button" class="media-gif-indicator" data-gif-toggle aria-label="Pause GIF" title="Pause GIF" onclick="window.tweetNookToggleGif(event, this)">
                            <span class="media-gif-action" aria-hidden="true">
                                <svg class="media-gif-pause-icon" viewBox="0 0 24 24"><path d="M6 4h4v16H6zm8 0h4v16h-4z"></path></svg>
                                <svg class="media-gif-play-icon" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"></path></svg>
                            </span>
                            <span class="media-gif-label">GIF</span>
                        </button>`;
            }

            const duration = this.formatMediaDuration(item.m.duration_millis);
            const visibility = duration
                ? `aria-label="Remaining video duration ${duration}"`
                : 'hidden aria-hidden="true"';
            return `<span class="media-video-duration" data-video-duration ${visibility}>${duration}</span>`;
        },

        // Like X, a quote is compact when both it and the tweet quoting it have media.
        isMediaQuotingMedia(tweet) {
            return Boolean(tweet?.media?.length && tweet?.qt_media?.length);
        },

        // X shows a quote's avatar at 24px and a nested quote's at 20px.
        renderQuoteByline(quote, avatarSize = 'w-6 h-6') {
            const escape = value => this.escapeHTML(value);
            const author = this.getQuoteAuthor(quote);
            return '<div class="quote-byline flex items-center space-x-1 text-[15px] mb-1">'
                + `<div class="avatar ${avatarSize} rounded-full flex-shrink-0 relative bg-[var(--gray-700)] flex items-center justify-center font-bold text-[var(--white-icon)] uppercase text-xs overflow-hidden">`
                + `<span class="avatar-initial">${escape(author.initial)}</span>`
                + `<img src="${escape(this.avatarUrl(author.id))}" class="avatar-image absolute inset-0 w-full h-full object-cover z-10" loading="lazy"></div>`
                + `<span class="font-bold text-[var(--text-primary)] truncate tweet-byline-name">${escape(author.name)}</span>`
                + `<span class="text-[var(--text-secondary)] tweet-byline-detail tweet-byline-handle">${escape(`@${author.screen_name}`)}</span>`
                + '<span class="text-[var(--text-secondary)]">·</span>'
                + `<span class="text-[var(--text-secondary)] tweet-byline-detail">${escape(this.formatDate(quote.legacy?.created_at || quote.created_at))}</span>`
                + '</div>';
        },

        // A quote's own quote, nested inside it as X shows it: a bar beside the byline
        // and two lines of text, with any media as a small thumbnail beside the text.
        // Unavailable or uncaptured nested quotes are left out.
        renderNestedQuote(quote, highlightSearch = false) {
            const nested = this.getQuoteTweet(quote);
            if (!nested || this.isTombstone(nested)) return '';
            const id = this.getTweetId(nested);
            const media = quote.qt_media || nested.media || [];
            return `<div class="quote-nested${media.length ? ' quote-nested-media' : ''}"${id ? ` data-thread-id="${this.escapeHTML(id)}"` : ''} data-action="quote">`
                + this.renderQuoteByline(nested, 'w-5 h-5')
                + (media.length ? `<div class="quote-media">${this.renderQuoteThumbnail(media)}</div>` : '')
                + `<div class="quote-text text-[15px] whitespace-pre-wrap leading-normal break-words" data-action="text">${this.formatText(nested, true, highlightSearch)}</div>`
                + '</div>';
        },

        // Thread views render nested quotes with x-html; a click inside one opens it
        // instead of the quote around it.
        openNestedQuote(event) {
            const nested = event.target.closest?.('.quote-nested');
            if (!nested) return;
            event.stopPropagation();
            if (event.target.closest('a')) return;
            const text = event.target.closest('[data-action="text"]');
            if (text && this.handleTextClick(event, text)) return;
            this.openThread(nested.dataset.threadId);
        },

        // Avatar load and error events do not bubble; x-html content catches them on
        // the way down.
        avatarEvent(event) {
            const image = event.target;
            if (!image?.classList?.contains('avatar-image')) return;
            if (event.type === 'load') this.avatarLoaded(image);
            else this.avatarFailed(image);
        },

        mediaSource(m) {
            if (m.download?.local_path) return `/${m.download.local_path}`;
            return this.isDemo ? (m.url || m.media_url || null) : null;
        },

        mediaPoster(m) {
            if (m.download?.thumbnail_local_path) return `/${m.download.thumbnail_local_path}`;
            return this.isDemo ? (m.thumbnail_url || null) : null;
        },

        mediaFrameLayout(media, context = 'feed') {
            const positiveNumber = value => {
                if (!['number', 'string'].includes(typeof value)) return 0;
                const number = Number(value);
                return Number.isFinite(number) && number > 0 ? number : 0;
            };
            const video = media.type === 'video' || media.type === 'animated_gif';
            const gif = media.type === 'animated_gif';
            const expandedDetailQuote = context === 'quote-detail-expanded';
            const quoted = context === 'quote' || context === 'quote-detail' || expandedDetailQuote;
            const width = positiveNumber(media.width);
            const height = positiveNumber(media.height);
            const playback = video && Array.isArray(media.aspect_ratio) && media.aspect_ratio.length === 2
                ? media.aspect_ratio.map(positiveNumber) : [];
            const measured = playback[0] && playback[1] ? playback[0] / playback[1]
                : width && height ? width / height : 0;
            const sourceAspect = Number.isFinite(measured) && measured > 0
                ? measured : gif ? 1 : 16 / 9;
            const portraitPhoto = !video && sourceAspect < 1;
            return {
                sourceAspect,
                frameAspect: video ? quoted ? 16 / 9 : gif ? Math.max(sourceAspect, 1) : sourceAspect
                    : Math.min(sourceAspect, 5),
                maxWidth: !quoted && sourceAspect < 1 && !gif ? 510 * sourceAspect : null,
                photoCapWidth: portraitPhoto && !expandedDetailQuote ? 510 * sourceAspect : null,
                portraitPhoto: portraitPhoto && !expandedDetailQuote,
                quoted,
            };
        },

        // A compact quote's media as stills, laid out like the full grid. Only a single
        // item gets X's GIF or duration badge. Stills have no controls, so clicks fall
        // through and open the quoted tweet.
        renderQuoteThumbnail(mediaList) {
            const items = (mediaList || []).slice(0, 4);
            if (!items.length) return '';
            const stills = items.map(m => {
                const src = this.mediaSource(m);
                const still = m.type === 'video' || m.type === 'animated_gif' ? this.mediaPoster(m) : src;
                if (still) return `<img src="${this.escapeHTML(still)}" loading="lazy" decoding="async" alt="">`;
                if (src) {
                    // Without a poster, a video shows its first frame once metadata loads.
                    const frame = src.includes('#') ? src : `${src}#t=0.1`;
                    return `<video src="${this.escapeHTML(frame)}" preload="metadata" muted playsinline></video>`;
                }
                return '<svg fill="none" stroke="currentColor" viewBox="0 0 24 24" role="img" aria-label="Media not downloaded"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"></path></svg>';
            });
            if (items.length > 1) {
                const cells = stills.map(still => `<div class="quote-thumbnail-cell">${still}</div>`).join('');
                return `<div class="quote-thumbnail quote-thumbnail-grid">${cells}</div>`;
            }
            const m = items[0];
            const label = m.type === 'animated_gif' ? 'GIF' : (m.type === 'video' ? this.formatMediaDuration(m.duration_millis) : '');
            const badge = label ? `<span class="quote-thumbnail-badge${m.type === 'video' ? ' quote-thumbnail-duration' : ''}">${label}</span>` : '';
            return `<div class="quote-thumbnail">${stills[0]}${badge}</div>`;
        },

        renderMediaGrid(mediaList, context = 'feed') {
            if (!mediaList || mediaList.length === 0) return '';

            const getSrc = m => this.mediaSource(m);
            const getPoster = m => this.mediaPoster(m);

            const allMedia = mediaList.map(m => {
                const type = (m.type === 'video' || m.type === 'animated_gif') ? 'video' : 'photo';
                const isGif = m.type === 'animated_gif'; 
                const duration = m.duration_millis;
                const isShort = duration && duration <= 60000;
                return { m, src: getSrc(m), poster: getPoster(m), type, isGif, isShort };
            });
            
            if (allMedia.length === 0) return '';
            
            const downloadedMedia = allMedia.filter(x => x.src);
            const mediaAttr = `data-lightbox-media="${this.escapeHTML(JSON.stringify(downloadedMedia))}"`;

            const placeholderHtml = `
                <div class="absolute inset-0 w-full h-full flex flex-col items-center justify-center bg-[var(--bg-secondary)] text-[var(--text-secondary)]">
                    <svg class="w-8 h-8 mb-2 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"></path></svg>
                    <span class="text-xs font-medium">Media not downloaded</span>
                </div>
            `;

            if (allMedia.length === 1) {
                const item = allMedia[0];
                const { src, poster, type, isGif, isShort } = item;
                const layout = this.mediaFrameLayout(item.m, context);
                const sizing = `--media-frame-aspect: ${layout.frameAspect};`
                    + (layout.maxWidth === null ? '' : ` --media-max-width: ${layout.maxWidth}px;`)
                    + (layout.photoCapWidth === null ? '' : ` --media-photo-cap-width: ${layout.photoCapWidth}px;`);
                const frameClass = `media-frame media-frame-${context}${isGif ? ' media-frame-gif' : ''}`
                    + (layout.portraitPhoto ? ' media-frame-portrait-photo' : '');
                // The query container measures the available column, independently of
                // the narrower bordered portrait frame. Playback stays a direct child
                // of the frame so its existing controls and badge handlers keep working.
                const wrap = html => `<div class="media-single mt-3" style="${sizing}">${html}</div>`;

                if (!src) {
                    return wrap(`<div class="relative max-w-full rounded-2xl border border-[var(--border-color)] overflow-hidden block ${frameClass}" @click.stop>
                                <div class="media-frame-placeholder relative">${placeholderHtml}</div>
                            </div>`);
                } else if (type === 'photo') {
                    return wrap(`<div class="relative max-w-full rounded-2xl border border-[var(--border-color)] overflow-hidden block media-photo ${frameClass}" ${mediaAttr} @click.stop>
                                <img src="${src}" loading="lazy" decoding="async" onclick="window.tweetNookOpenLightbox(this, 0)" class="media-frame-image object-cover cursor-pointer block">
                            </div>`);
                } else {
                    const loopAttr = (isGif || isShort) ? 'loop' : '';
                    const autoplayAttr = isGif ? 'autoplay muted playsinline' : 'playsinline';
                    const controlsAttr = isGif ? '' : `data-video-custom data-video-aspect="${layout.sourceAspect}"`;
                    const gifEvents = isGif
                        ? 'data-animated-gif onplay="window.tweetNookSyncGifIndicator(this)" onpause="window.tweetNookSyncGifIndicator(this)" onloadeddata="window.tweetNookSyncGifIndicator(this)"'
                        : '';
                    const indicator = this.renderMediaPlaybackIndicator(item);

                    const videoEvents = isGif ? '' : VIDEO_PLAYER_EVENTS;

                    return wrap(`<div class="relative max-w-full rounded-2xl border border-[var(--border-color)] overflow-hidden block ${isGif ? '' : 'media-video-player'} ${frameClass}" @click.stop>
                                <video src="${src}" poster="${poster || ''}" preload="${poster ? 'none' : 'metadata'}" ${autoplayAttr} ${loopAttr} ${controlsAttr} ${gifEvents} ${videoEvents} class="${isGif ? 'media-frame-gif-video' : 'media-frame-video'} outline-none block"></video>
                                ${indicator}
                                ${isGif ? '' : renderVideoControls()}
                            </div>`);
                }
            }
            
            let gridClass = allMedia.length === 2 ? 'grid-cols-2' : 'grid-cols-2 grid-rows-2';
            let html = `<div class="mt-3 grid gap-[2px] rounded-2xl overflow-hidden border border-[var(--border-color)] aspect-[16/9] ${gridClass}" ${mediaAttr} @click.stop>`;
            
            let downloadedIdx = 0;
            allMedia.forEach((item, idx) => {
                const { src, poster, type, isGif, isShort } = item;
                let itemClass = (allMedia.length === 3 && idx === 0) ? 'row-span-2 col-span-1' : (allMedia.length === 3 ? 'col-span-1' : '');
                
                if (!src) {
                    html += `<div class="relative w-full h-full bg-[var(--border-color)] ${itemClass}">
                                ${placeholderHtml}
                            </div>`;
                } else {
                    const currentDlIdx = downloadedIdx++;
                    if (type === 'photo') {
                        html += `<div class="relative w-full h-full bg-[var(--border-color)] ${itemClass} media-photo">
                                    <img src="${src}" loading="lazy" decoding="async" onclick="window.tweetNookOpenLightbox(this, ${currentDlIdx})" class="absolute inset-0 w-full h-full object-cover cursor-pointer">
                                </div>`;
                    } else {
                        const loopAttr = (isGif || isShort) ? 'loop' : '';
                        const autoplayAttr = isGif ? 'autoplay muted playsinline' : 'playsinline';
                        const controlsAttr = isGif ? '' : `data-video-custom data-video-aspect="${this.mediaFrameLayout(item.m, context).sourceAspect}"`;
                        const gifEvents = isGif
                            ? 'data-animated-gif onplay="window.tweetNookSyncGifIndicator(this)" onpause="window.tweetNookSyncGifIndicator(this)" onloadeddata="window.tweetNookSyncGifIndicator(this)"'
                            : '';
                        const videoEvents = isGif ? '' : VIDEO_PLAYER_EVENTS;
                        const indicator = this.renderMediaPlaybackIndicator(item);
                        html += `<div class="relative w-full h-full bg-[var(--border-color)] ${itemClass} ${isGif ? '' : 'media-video-player'}">
                                    <video src="${src}" poster="${poster || ''}" preload="${poster ? 'none' : 'metadata'}" ${autoplayAttr} ${loopAttr} ${controlsAttr} ${gifEvents} ${videoEvents} class="absolute inset-0 w-full h-full object-cover outline-none"></video>
                                    ${indicator}
                                    ${isGif ? '' : renderVideoControls()}
                                </div>`;
                    }
                }
            });
            
            html += `</div>`;
            return html;
        },

        escapeHTML(str) {
            if (!str) return '';
            return String(str).replace(/&/g, '&amp;')
                      .replace(/</g, '&lt;')
                      .replace(/>/g, '&gt;')
                      .replace(/"/g, '&quot;')
                      .replace(/'/g, '&#039;');
        },

        safeURL(value) {
            if (!value || value === '#') return '#';
            try {
                const parsed = new URL(value, window.location?.origin || 'http://localhost');
                return ['http:', 'https:'].includes(parsed.protocol)
                    ? this.escapeHTML(parsed.href)
                    : '#';
            } catch (_) {
                return '#';
            }
        },

        formatCommunityNoteText(content) {
            let text = content?.text || '';
            if (!text) return '';
            let entities = content?.entities || [];
            
            entities = [...entities].sort((a, b) => a.fromIndex - b.fromIndex);
            
            let html = '';
            let lastIndex = 0;
            
            for (const ent of entities) {
                if (ent.ref?.urlType === 'ExternalUrl' && ent.ref?.url) {
                    html += this.escapeHTML(text.substring(lastIndex, ent.fromIndex));
                    const linkText = this.escapeHTML(text.substring(ent.fromIndex, ent.toIndex));
                    const href = this.safeURL(ent.ref.url);
                    html += `<a href="${href}" target="_blank" rel="noopener noreferrer" class="text-[var(--accent-color)] hover:underline" @click.stop>${linkText}</a>`;
                    lastIndex = ent.toIndex;
                }
            }
            html += this.escapeHTML(text.substring(lastIndex));
            return html;
        },

        renderCommunityNote(raw_json, isQuote = false, isDetail = false, quoteHasMedia = false) {
            raw_json = raw_json?.raw_json || raw_json;
            if (!raw_json || !raw_json.birdwatch_pivot) return '';
            const bw = raw_json.birdwatch_pivot;
            if (!bw.subtitle?.text) return '';
            
            const textHtml = this.formatCommunityNoteText(bw.subtitle);
            const title = isDetail ? (bw.title || bw.shorttitle || 'Readers added context') : (bw.shorttitle || 'Readers added context');
            const containerClass = isQuote
                ? `community-note community-note-quote${quoteHasMedia ? ' community-note-quote-media' : ' community-note-quote-spaced'}`
                : isDetail
                    ? 'community-note community-note-detail'
                    : 'community-note community-note-feed';

            const iconSvg = `<svg viewBox="0 0 24 24" fill="var(--accent-color)" class="w-[18px] h-[18px]"><path fill-rule="evenodd" d="M8.25 6.75a3.75 3.75 0 117.5 0 3.75 3.75 0 01-7.5 0zM15.75 9.75a3 3 0 116 0 3 3 0 01-6 0zM2.25 9.75a3 3 0 116 0 3 3 0 01-6 0zM6.31 15.117A6.745 6.745 0 0112 12a6.745 6.745 0 016.709 7.498.75.75 0 01-.372.568A12.696 12.696 0 0112 21.75c-2.305 0-4.47-.612-6.337-1.684a.75.75 0 01-.372-.568 6.787 6.787 0 011.019-4.38z" clip-rule="evenodd" /><path d="M5.082 14.254a8.287 8.287 0 00-1.308 5.135 9.687 9.687 0 01-1.764-.44l-.115-.04a.563.563 0 01-.373-.487l-.01-.121a3.75 3.75 0 016.576-3.036c.32.338.608.708.857 1.103a6.732 6.732 0 00-3.863 1.341 6.772 6.772 0 01-.004-3.456z" /><path d="M18.918 14.254a8.287 8.287 0 011.308 5.135 9.687 9.687 0 001.764-.44l.115-.04a.563.563 0 00.373-.487l-.01-.121a3.75 3.75 0 00-6.576-3.036c-.32.338-.608.708-.857 1.103a6.732 6.732 0 013.863 1.341 6.772 6.772 0 00.004-3.456z" /></svg>`;
            
            return `
            <div class="${containerClass}" ${!isQuote ? 'onclick="event.stopPropagation()"' : ''}>
                <div class="community-note-header">
                    ${iconSvg}
                    <span>${this.escapeHTML(title)}</span>
                </div>
                <div class="community-note-body">${textHtml}</div>
            </div>`;
        },

        formatCount(num, wholeLargeUnits = false) {
            let amount = Number(num);
            if (!Number.isFinite(amount) || amount <= 0) return '';
            const units = ['', 'K', 'M', 'B', 'T'];
            let unit = 0;
            while (unit < units.length - 1 && amount >= 1000) {
                amount /= 1000;
                unit++;
            }
            amount = wholeLargeUnits && unit && amount >= 10
                ? Math.floor(amount)
                : Math.floor(amount * 10) / 10;
            return `${Number(amount.toFixed(1))}${units[unit]}`;
        },

        formatCompactCount(num) {
            return this.formatCount(num);
        },

        formatActionCount(num) {
            return this.formatCount(num, true);
        },

        renderActionBar(tweet, isMain = false) {
            if (this.isPlaceholderTweet(tweet) || this.isTextPlaceholderTweet(tweet)) return '';
            const legacy = tweet.raw_json?.legacy || {};
            const replyCount = legacy.reply_count || 0;
            const retweetCount = (legacy.retweet_count || 0) + (legacy.quote_count || 0);
            const likeCount = legacy.favorite_count || 0;
            const viewCount = tweet.raw_json?.views?.count || 0;
            const bmkCount = legacy.bookmark_count || 0;
            const bookmarkLabel = `${this.formatCompactCount(bmkCount) || '0'} bookmarks`;
            
            const formatNum = this.formatActionCount.bind(this);
            
            const collections = [...(tweet.collections || []), ...(tweet.collection?.type ? [tweet.collection.type] : [])];
            const isLiked = collections.includes('like');
            const isBookmarked = collections.includes('bookmark');
            
            const likeIcon = isLiked ? 
                `<svg viewBox="0 0 24 24" class="w-[18.5px] h-[18.5px]" style="fill: var(--danger-color)"><path d="M20.884 13.19c-1.351 2.48-4.001 5.12-8.379 7.67l-.503.3-.504-.3C7.121 18.31 4.471 15.67 3.119 13.19 1.928 10.99 1.898 8.48 2.921 6.45 3.864 4.56 5.8 3.32 8.016 3.42c1.474.07 2.812.8 3.486 2.08l.498.94.498-.94c.674-1.28 2.012-2.01 3.486-2.08 2.216-.1 4.152 1.14 5.095 3.03 1.023 2.03.993 4.54-.195 6.74z"></path></svg>` : 
                `<svg viewBox="0 0 24 24" class="w-[18.5px] h-[18.5px] fill-current group-hover:fill-[var(--danger-color)]"><path d="M16.697 5.5c-1.222-.06-2.679.51-3.89 2.16l-.805 1.09-.806-1.09C9.984 6.01 8.526 5.44 7.304 5.5c-1.243.07-2.349.78-2.91 1.91-.552 1.12-.633 2.78.479 4.82 1.074 1.97 3.257 4.27 7.129 6.61 3.87-2.34 6.052-4.64 7.126-6.61 1.111-2.04 1.03-3.7.477-4.82-.561-1.13-1.666-1.84-2.908-1.91zm4.187 7.69c-1.351 2.48-4.001 5.12-8.379 7.67l-.503.3-.504-.3c-4.379-2.55-7.029-5.19-8.382-7.67-1.36-2.5-1.41-4.86-.514-6.67.887-1.79 2.647-2.91 4.601-3.01 1.651-.09 3.368.56 4.798 2.01 1.429-1.45 3.146-2.1 4.796-2.01 1.954.1 3.714 1.22 4.601 3.01.896 1.81.846 4.17-.514 6.67z"></path></svg>`;

            const bmkIcon = isBookmarked ? 
                `<svg viewBox="0 0 24 24" class="w-[18.5px] h-[18.5px] fill-[var(--accent-color)]"><path d="M4 4.5C4 3.12 5.119 2 6.5 2h11C18.881 2 20 3.12 20 4.5v18.44l-8-5.71-8 5.71V4.5z"></path></svg>` : 
                `<svg viewBox="0 0 24 24" class="w-[18.5px] h-[18.5px] fill-current"><path d="M4 4.5C4 3.12 5.119 2 6.5 2h11C18.881 2 20 3.12 20 4.5v18.44l-8-5.71-8 5.71V4.5zM6.5 4c-.276 0-.5.22-.5.5v14.56l6-4.29 6 4.29V4.5c0-.28-.224-.5-.5-.5H6.5z"></path></svg>`;

            const margin = isMain ? 'tweet-actions-main border-t border-[var(--border-color)]' : 'mt-3';
            
            return `
            <div class="tweet-actions flex items-center justify-between text-[var(--text-secondary)] w-full ${margin}">
                <div class="flex items-center">
                    <svg viewBox="0 0 24 24" class="w-[18.5px] h-[18.5px] fill-current"><path d="M1.751 10c0-4.42 3.584-8 8.005-8h4.366c4.49 0 8.129 3.64 8.129 8.13 0 2.96-1.607 5.68-4.196 7.11l-8.054 4.46v-3.69h-.067c-4.49.1-8.183-3.51-8.183-8.01zm8.005-6c-3.317 0-6.005 2.69-6.005 6 0 3.37 2.77 6.08 6.138 6.01l.351-.01h1.761v2.3l5.087-2.81c1.951-1.08 3.163-3.13 3.163-5.36 0-3.39-2.744-6.13-6.129-6.13H9.756z"></path></svg>
                    <span class="tweet-action-count">${formatNum(replyCount)}</span>
                </div>
                <div class="flex items-center">
                    <svg viewBox="0 0 24 24" class="w-[18.5px] h-[18.5px] fill-current"><path d="M4.5 3.88l4.432 4.14-1.364 1.46L5.5 7.55V16c0 1.1.896 2 2 2H13v2H7.5c-2.209 0-4-1.79-4-4V7.55L1.432 9.48.068 8.02 4.5 3.88zM16.5 6H11V4h5.5c2.209 0 4 1.79 4 4v8.45l2.068-1.93 1.364 1.46-4.432 4.14-4.432-4.14 1.364-1.46 2.068 1.93V8c0-1.1-.896-2-2-2z"></path></svg>
                    <span class="tweet-action-count">${formatNum(retweetCount)}</span>
                </div>
                <div class="flex items-center group cursor-pointer ${isLiked ? 'text-[var(--danger-color)]' : 'hover:text-[var(--danger-color)]'}">
                    ${likeIcon}
                    <span class="tweet-action-count">${formatNum(likeCount)}</span>
                </div>
                ${isMain ? '' : `<div class="flex items-center">
                    <svg viewBox="0 0 24 24" class="w-[18.5px] h-[18.5px] fill-current"><path d="M8.75 21V3h2v18h-2zM18 21V8.5h2V21h-2zM4 21l.004-10h2L6 21H4zm9.248 0v-7h2v7h-2z"></path></svg>
                    <span class="tweet-action-count">${formatNum(viewCount)}</span>
                </div>`}
                <div class="flex items-center group cursor-pointer ${isBookmarked ? 'text-[var(--accent-color)]' : 'hover:text-[var(--accent-color)]'}" ${!isMain ? `title="${this.escapeHTML(bookmarkLabel)}" aria-label="${this.escapeHTML(bookmarkLabel)}"` : ''}>
                    ${bmkIcon}
                    ${isMain ? `<span class="tweet-action-count">${formatNum(bmkCount)}</span>` : ''}
                </div>
                <div class="flex items-center">
                    <a href="https://x.com/${this.escapeHTML(tweet.author?.username || 'i')}/status/${this.escapeHTML(tweet.tweet_id)}" target="_blank" @click.stop class="flex items-center" title="Open on Twitter/X">
                        <svg viewBox="0 0 24 24" class="w-[18.5px] h-[18.5px] fill-current blue-hover transition"><path d="M18 19H6c-.55 0-1-.45-1-1V6c0-.55.45-1 1-1h5c.55 0 1-.45 1-1s-.45-1-1-1H6c-1.65 0-3 1.35-3 3v12c0 1.65 1.35 3 3 3h12c1.65 0 3-1.35 3-3v-5c0-.55-.45-1-1-1s-1 .45-1 1v5c0 .55-.45 1-1 1zM14 4c0 .55.45 1 1 1h2.59l-9.13 9.13c-.39.39-.39 1.02 0 1.41.19.19.45.29.71.29s.51-.1.71-.29L19 6.41V9c0 .55.45 1 1 1s1-.45 1-1V4c0-.55-.45-1-1-1h-5c-.55 0-1 .45-1 1z"></path></svg>
                    </a>
                </div>
            </div>`;
        }
    };
}
