/**
 * Feed video playback: which player autoplays as the reader scrolls, which one owns
 * audio, which nearby clips warm their decoders, and the posters shown until a
 * first frame paints. tweetApp() supplies the playback settings it reads
 * (videoPlayback, videoMuted, videoVolume) and the open-dialog flags that pause it.
 */

// Measure the picture the reader can actually see, excluding sticky headers.
// A manually paused candidate stays paused until it leaves the viewport.
// Start as the picture enters; retain its owner near the edge so small scrolls
// do not repeatedly stop playback or switch between neighboring videos.
const VIDEO_AUTOPLAY_START_RATIO = 0.25;
const VIDEO_AUTOPLAY_STOP_RATIO = 0.1;
// Follow the picture occupying more of the viewport in either scroll direction.
// A ten-point picture-area lead keeps almost-equal neighbors from flapping.
const VIDEO_AUTOPLAY_SWITCH_MARGIN = 0.1;
const autoplayVideos = new Set();
const heldAutoplayVideos = new WeakSet();
const automaticallyPausedVideos = new WeakSet();
const preparedMediaVideos = new WeakSet();
const failedAutoplayVideos = new WeakSet();
const pendingAutoplayRequests = new WeakMap();
const playingVideoPlayers = new Set();
// Players muted only because the browser refused audible autoplay.
const forcedMuteVideos = new WeakSet();
// Expanded players have their own audio controls until they return to the feed.
const expandedVideoAudio = new WeakMap();
const videoPosterStates = new WeakMap();
const preparingVideoPosters = new WeakMap();
// Bounded, page-local previews also cover repeated media and freshly mounted
// cards before their decoders have loaded again.
const generatedVideoPosters = new Map();
// Lazy images start loading this far outside the viewport, ahead of the browser's
// own lazy-load distance, which on mobile is too short to hide the load.
const MEDIA_PREFETCH_MARGIN_PX = 1600;
// Only the two closest upcoming clips prepare playback data. Browsers control
// actual transfer sizes; stop asking for more after two seconds or four seconds
// of wall time, rather than enabling auto buffering across the whole feed.
const VIDEO_WARMUP_LIMIT = 2;
const VIDEO_WARMUP_SECONDS = 2;
const VIDEO_WARMUP_TIMEOUT_MS = 4000;

function feedVideoMethods() {
    let syncingVideoAudio = false;
    let videoVisibilityFrame = null;
    let playingVideoVisibilityFrame = null;
    let videoTouchSample = null;
    let videoScrollIntent = false;
    let videoScrollIntentTimer = null;
    let autoplayRequest = null;
    let autoplayTarget = null;
    let audibleFeedVideo = null;
    const audibleFeedDecoders = new Set();
    const releasedVideoSources = new WeakMap();
    let manualAutoplay = null;
    const videoWarmupCandidates = new Set();
    const warmedVideoSources = new WeakMap();
    const carouselWarmupPositions = new WeakMap();
    let videoScrollY = 0;
    let videoScrollDirection = 1;

    return {
        // Feed players follow these observers and document events from startup.
        initVideoPlayback() {
            this.videoObserver = new IntersectionObserver((entries) => {
                entries.forEach(e => {
                    if (e.target.tagName === 'VIDEO') {
                        if (preparingVideoPosters.has(e.target)) return;
                        // The lightbox owns playback; observer callbacks arriving
                        // after dismissal must never restart its hidden player.
                        if (e.target.closest?.('.lightbox-stage')) {
                            if (!this.lightboxOpen) e.target.pause();
                            return;
                        }
                        // Feed autoplay has its own viewport/header-aware stop
                        // boundary. The generic observer uses raw element area,
                        // which would cut tall or horizontally clipped videos off early.
                        if (this.videoPlayback === 'autoplay' && isFeedVideoPlayer(e.target)) return;
                        if (!e.isIntersecting) {
                            e.target.pause();
                        } else if (e.target.hasAttribute('autoplay')) {
                            e.target.play().catch(() => {});
                        }
                    }
                });
            }, { threshold: 0.1 });

            this.videoAutoplayObserver = new IntersectionObserver((entries) => {
                // Apply a whole observer batch before selecting a player. Choosing
                // per entry makes playback depend on callback/feed insertion order.
                entries.forEach(e => this.updateAutoplayVisibility(e.target, e.isIntersecting, false));
                this.autoplayNextVideo();
            }, { rootMargin: '200px 0px', threshold: 0 });
            this.videoWarmupObserver = new IntersectionObserver(entries => {
                entries.forEach(({target, isIntersecting}) => {
                    if (isIntersecting) {
                        this.restoreVideoResource(target);
                        videoWarmupCandidates.add(target);
                    } else {
                        videoWarmupCandidates.delete(target);
                        preparingVideoPosters.get(target)?.();
                        this.releaseVideoResource(target);
                    }
                });
                this.updateVideoWarmup();
            }, { rootMargin: `${MEDIA_PREFETCH_MARGIN_PX}px 0px`, threshold: 0 });
            // IntersectionObserver ignores sticky occluders and cannot report a
            // visibility change when their edge crosses a playing picture. Check
            // the small near-viewport candidate set on the next scroll frame.
            const queueVideoVisibility = () => this.queueVideoVisibility();
            document.addEventListener('scroll', queueVideoVisibility, { capture: true, passive: true });
            window.addEventListener('resize', queueVideoVisibility, { passive: true });
            window.visualViewport?.addEventListener('resize', queueVideoVisibility, { passive: true });
            document.addEventListener('touchmove', event => this.trackVideoScrollTouch(event), {capture: true, passive: true});
            document.addEventListener('touchend', () => { videoTouchSample = null; }, {capture: true, passive: true});
            document.addEventListener('touchcancel', () => {
                videoTouchSample = null;
                this.releaseVideoScrollIntent();
            }, {capture: true, passive: true});
            // Media events do not bubble, so players are followed in the capture phase.
            document.addEventListener('play', event => this.handleVideoPlay(event.target), true);
            document.addEventListener('pause', event => this.handleVideoPause(event.target), true);
            document.addEventListener('volumechange', event => this.handleVideoVolumeChange(event.target), true);
            window.addEventListener('video-audio-intent', event => {
                const {video, muted, volume} = event.detail;
                this.setSharedVideoAudio(video, muted, volume, true);
            });
            window.addEventListener('video-expanded-audio', event => {
                this.setExpandedVideoAudio(event.detail.video, event.detail.expanded);
            });

            this.mediaPrefetchObserver = new IntersectionObserver((entries) => {
                entries.forEach(e => {
                    // Lay out and paint a bounded ring of cards ahead of scrolling.
                    // Otherwise content-visibility can reveal their media a frame
                    // after the card itself, even when the bytes are already cached.
                    if (e.target.classList?.contains('feed-card')) {
                        if (e.isIntersecting) e.target.style.contentVisibility = 'visible';
                    }
                    if (e.isIntersecting) this.mediaPrefetchObserver.unobserve(e.target);
                    if (!e.isIntersecting) return;
                    this.prefetchMedia(e.target);
                });
            }, { rootMargin: `${MEDIA_PREFETCH_MARGIN_PX}px 0px` });
        },

        // A player removed from the page leaves every observer and candidate set.
        forgetVideo(target) {
            this.videoObserver.unobserve(target);
            this.videoAutoplayObserver.unobserve(target);
            this.videoWarmupObserver.unobserve(target);
            autoplayVideos.delete(target);
            videoWarmupCandidates.delete(target);
            preparingVideoPosters.get(target)?.();
            this.pauseAutoplayVideo(target);
        },

        // A video closed from the viewer while playing keeps the feed's playback, as if
        // the reader had started it there.
        resumeAutoplayAt(origin, track) {
            autoplayTarget = origin;
            heldAutoplayVideos.delete(origin);
            manualAutoplay = {video: origin, scrollY: window.scrollY || 0, scrollLeft: track?.scrollLeft || 0};
        },

        // Off-screen feed cards skip layout (content-visibility), so the images inside
        // have no box to observe; watch the card instead and load all of its images.
        observeMediaPrefetch(node) {
            const images = node.tagName === 'IMG' ? [node] : node.querySelectorAll('img[loading="lazy"]');
            images.forEach(image => {
                if (image.loading !== 'lazy') return;
                this.mediaPrefetchObserver.observe(image.closest('.feed-card') || image);
            });
            const videos = node.tagName === 'VIDEO' ? [node] : node.querySelectorAll?.('video') || [];
            videos.forEach(video => {
                if (video.tagName !== 'VIDEO') return;
                this.mediaPrefetchObserver.observe(video.closest('.feed-card') || video);
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
            const videos = target.tagName === 'VIDEO' ? [target] : target.querySelectorAll?.('video') || [];
            videos.forEach(video => {
                if (video.tagName !== 'VIDEO' || preparedMediaVideos.has(video)) return;
                this.restoreVideoResource(video);
                preparedMediaVideos.add(video);
                // All nearby clips get headers; only the closest two get a
                // bounded playback warmup, including clips with saved posters.
                if (video.preload === 'none') video.preload = 'metadata';
                // The poster property resolves poster="" to the document URL.
                // Read the raw attribute to distinguish an absent preview.
                if (isFeedVideoPlayer(video) && video.play && video.addEventListener) {
                    const cached = generatedVideoPosters.get(video.src);
                    if (!video.getAttribute?.('poster') && cached) {
                        video.poster = cached;
                        const image = tweetNookVideoPlayer(video)?.querySelector('[data-video-poster]');
                        if (image) { image.src = cached; image.hidden = false; }
                    }
                    videoWarmupCandidates.add(video);
                }
            });
            this.updateVideoWarmup();
        },

        updateVideoWarmup() {
            const y = window.scrollY || 0;
            if (y !== videoScrollY) videoScrollDirection = y > videoScrollY ? 1 : -1;
            videoScrollY = y;
            const blocked = this.videoPlayback !== 'autoplay' || document.hidden || this.lightboxOpen
                || this.showSettingsModal || this.showStatsModal || this.showSetupModal
                || window.navigator?.connection?.saveData;
            const height = window.innerHeight || 800;
            const nearby = [];
            const activeGallery = autoplayTarget?.closest?.('.media-carousel-track');
            const activeRect = activeGallery && autoplayTarget.getBoundingClientRect();
            videoWarmupCandidates.forEach(video => {
                if (!video.isConnected) { videoWarmupCandidates.delete(video); return; }
                const rect = video.getBoundingClientRect();
                const bottom = rect.top + rect.height;
                const distance = videoScrollDirection > 0 ? rect.top - height : -bottom;
                const gallery = video.closest?.('.media-carousel-track');
                let horizontalDistance = rect.left;
                if (gallery && gallery === activeGallery) {
                    const previous = carouselWarmupPositions.get(gallery);
                    const direction = previous && gallery.scrollLeft !== previous.left
                        ? Math.sign(gallery.scrollLeft - previous.left) : previous?.direction || 1;
                    carouselWarmupPositions.set(gallery, {left: gallery.scrollLeft, direction});
                    horizontalDistance = direction * (rect.left - activeRect.left);
                    // Do not spend a warmup slot on clips behind the swipe.
                    if (horizontalDistance <= 0) return;
                }
                if (!blocked && isFeedVideoPlayer(video) && video !== autoplayTarget
                    && !heldAutoplayVideos.has(video) && (video.paused || preparingVideoPosters.has(video))
                    && distance >= -height && distance < MEDIA_PREFETCH_MARGIN_PX) nearby.push({video, distance, horizontalDistance});
            });
            const selected = new Set(nearby.sort((a, b) => a.distance - b.distance || a.horizontalDistance - b.horizontalDistance)
                .slice(0, VIDEO_WARMUP_LIMIT).map(({video}) => video));
            // Cancel before starting replacements, including on fast reversals,
            // hidden tabs, overlays and switching to click-to-play.
            videoWarmupCandidates.forEach(video => {
                if (!selected.has(video)) preparingVideoPosters.get(video)?.();
            });
            [...selected].forEach(video => {
                let decoder = video;
                if (!this.videoMuted && isPhoneLayout() && audibleFeedDecoders.size > 1 && !audibleFeedDecoders.has(video)) {
                    const spare = [...audibleFeedDecoders].find(node => node.isConnected && isFeedVideoPlayer(node)
                        && node !== audibleFeedVideo && node !== autoplayTarget && node.paused && !selected.has(node));
                    if (spare) {
                        decoder = this.reuseAudibleFeedVideo(video, spare);
                        selected.delete(video); selected.add(decoder);
                        videoWarmupCandidates.add(decoder);
                        if (autoplayVideos.has(video)) autoplayVideos.add(decoder);
                    }
                }
                if (warmedVideoSources.get(decoder) === decoder.src || preparingVideoPosters.has(decoder)) return;
                warmedVideoSources.set(decoder, decoder.src);
                this.prepareVideoPoster(decoder, true);
            });
        },

        // Paused media still retains its decoder and buffered bytes. Bound those
        // resources to the same nearby window, including GIFs, on long feeds.
        releaseVideoResource(video) {
            if (!video.getAttribute?.('src') || video.closest?.('.lightbox-stage')) return;
            this.pauseAutoplayVideo(video);
            preparingVideoPosters.get(video)?.();
            // Emptying a gesture-authorized decoder can clear Safari's permission.
            // Keep the bounded three-element pool; ordinary distant media unloads.
            if (audibleFeedDecoders.has(video)) return;
            releasedVideoSources.set(video, {src: video.getAttribute('src'), time: video.currentTime || 0});
            warmedVideoSources.delete(video);
            preparedMediaVideos.delete(video);
            video.removeAttribute('src');
            video.load?.();
        },

        restoreVideoResource(video) {
            const resource = releasedVideoSources.get(video);
            if (!resource) return;
            releasedVideoSources.delete(video);
            video.setAttribute('src', resource.src);
            if (resource.time) video.currentTime = resource.time;
            // On a fast jump the viewport observer may try the GIF before the
            // wider preparation observer restores its source. Retry that start.
            if (video.hasAttribute?.('autoplay') && feedVideoVisibility(video).ratio >= 0.1) video.play()?.catch?.(() => {});
        },

        prepareVideoPoster(video, bufferPlayback = false) {
            if (preparingVideoPosters.has(video) || document.hidden) return;
            const position = video.currentTime || 0;
            const capturePoster = !video.getAttribute?.('poster');
            let timer;
            const finish = (capture = false, cancelled = false) => {
                // A canceled play promise can settle after this decoder starts
                // another warmup. Only this preparation may finish its work.
                if (preparingVideoPosters.get(video) !== cancel) return;
                preparingVideoPosters.delete(video);
                if (cancelled) warmedVideoSources.delete(video);
                clearTimeout(timer);
                video.removeEventListener('loadeddata', ready);
                video.removeEventListener('progress', ready);
                video.removeEventListener('error', failed);
                if (bufferPlayback) video.preload = 'metadata';
                if (capture && capturePoster && video.videoWidth && video.videoHeight) {
                    try {
                        const canvas = document.createElement('canvas');
                        canvas.width = Math.min(640, video.videoWidth);
                        canvas.height = Math.round(canvas.width * video.videoHeight / video.videoWidth);
                        canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
                        video.poster = canvas.toDataURL('image/jpeg', 0.85);
                        generatedVideoPosters.delete(video.src);
                        generatedVideoPosters.set(video.src, video.poster);
                        if (generatedVideoPosters.size > 48) generatedVideoPosters.delete(generatedVideoPosters.keys().next().value);
                        const image = tweetNookVideoPlayer(video)?.querySelector('[data-video-poster]');
                        if (image) { image.src = video.poster; image.hidden = false; }
                    } catch { /* A remote demo video may not allow canvas capture. */ }
                }
                // Only the selected player can retain playback after preparation.
                const selected = !cancelled && autoplayTarget === video && this.videoPlayback === 'autoplay'
                    && !document.hidden && !this.lightboxOpen && !this.showSettingsModal
                    && !this.showStatsModal && !this.showSetupModal;
                if (!selected) this.pauseAutoplayVideo(video);
                // Silent preparation must not consume the beginning of a clip.
                if (bufferPlayback && video.currentTime !== position) video.currentTime = position;
                this.applyVideoAudio(video);
                if (selected && !video.paused) this.handleVideoPlay(video);
                if (!cancelled) this.autoplayNextVideo();
            };
            const ready = () => {
                // Offscreen decoders have data but do not present frames, so a
                // presentation callback can wait forever here. loadeddata is
                // enough to copy their decoded picture into a canvas.
                if (preparingVideoPosters.get(video) !== cancel || video.readyState < 2) return;
                if (bufferPlayback) {
                    // Once the first picture decodes, keep filling while paused.
                    // This works on iOS where preload=auto alone is only a hint.
                    this.pauseAutoplayVideo(video);
                    const end = Math.min(position + VIDEO_WARMUP_SECONDS, video.duration || Infinity);
                    let buffered = false;
                    for (let i = 0; i < (video.buffered?.length || 0); i++) {
                        if (video.buffered.start(i) <= position && video.buffered.end(i) >= end) buffered = true;
                    }
                    if (!buffered) return;
                }
                finish(true);
            };
            const failed = () => finish();
            const cancel = () => finish(false, true);
            preparingVideoPosters.set(video, cancel);
            // Silent, bounded first-frame decoding is necessary on iOS: metadata
            // alone does not paint a preview for a file without an archived poster.
            video.muted = true;
            if (bufferPlayback) video.preload = 'auto';
            video.addEventListener('loadeddata', ready);
            video.addEventListener('progress', ready);
            video.addEventListener('error', failed);
            timer = setTimeout(() => finish(video.readyState >= 2), VIDEO_WARMUP_TIMEOUT_MS);
            video.play()?.then?.(ready, error => {
                // Pausing on loadeddata can abort the still-pending play promise.
                // Keep its paused buffer request alive until progress/timeout.
                if (bufferPlayback && error?.name === 'AbortError' && video.readyState >= 2) return;
                finish();
            });
        },

        observeVideo(video) {
            tweetNookWatchVideoPoster(video);
            if (isVideoPlayer(video)) {
                this.applyVideoAudio(video);
                tweetNookInitVideoPlayer(video);
            }
            this.videoObserver.observe(video);
            this.videoWarmupObserver?.observe(video);
            if (!isFeedVideoPlayer(video)) return;
            this.videoAutoplayObserver.observe(video);
        },

        applyVideoAudio(video) {
            const previous = syncingVideoAudio;
            syncingVideoAudio = true;
            try {
                const audio = expandedVideoAudio.get(video) || {muted: this.videoMuted, volume: this.videoVolume};
                if (video.volume !== audio.volume) video.volume = audio.volume;
                const muted = preparingVideoPosters.has(video) || forcedMuteVideos.has(video) || audio.muted;
                if (video.muted !== muted) video.muted = muted;
            } finally {
                syncingVideoAudio = previous;
            }
        },

        setExpandedVideoAudio(video, expanded) {
            if (!isVideoPlayer(video)) return;
            if (expanded) {
                if (expandedVideoAudio.has(video)) return;
                preparingVideoPosters.get(video)?.();
                expandedVideoAudio.set(video, {muted: false, volume: video.volume || 1, forcedMute: forcedMuteVideos.has(video)});
                forcedMuteVideos.delete(video);
                const resume = !video.paused;
                this.applyVideoAudio(video);
                // Keep the original tap's activation when Safari pauses on unmute.
                if (resume) video.play()?.catch?.(() => {});
            } else {
                const audio = expandedVideoAudio.get(video);
                if (!audio) return;
                expandedVideoAudio.delete(video);
                if (audio.forcedMute) forcedMuteVideos.add(video);
                this.applyVideoAudio(video);
            }
        },

        setSharedVideoAudio(video, muted, volume, fromGesture = false) {
            const resume = !video.paused;
            if (fromGesture) preparingVideoPosters.get(video)?.();
            const expanded = expandedVideoAudio.get(video);
            if (expanded) {
                expanded.muted = muted;
                if (Number.isFinite(volume)) expanded.volume = volume;
                forcedMuteVideos.delete(video);
                this.applyVideoAudio(video);
                if (fromGesture && resume) video.play()?.catch?.(() => {});
                return;
            }
            this.videoMuted = muted;
            if (Number.isFinite(volume)) this.videoVolume = volume;
            document.querySelectorAll(VIDEO_PLAYER_SELECTOR).forEach(other => {
                forcedMuteVideos.delete(other);
                this.applyVideoAudio(other);
            });
            if (!muted && (isFeedVideoPlayer(video) || this.isInlineLightboxPlayer(tweetNookVideoPlayer(video)))) {
                audibleFeedVideo = video;
                // Explicit play() in the unmute gesture also unlocks this decoder
                // when Safari paused it while its muted property changed.
                if (fromGesture && resume) video.play()?.catch?.(() => {});
                if (fromGesture && isPhoneLayout() && this.videoPlayback === 'autoplay' && !window.navigator?.connection?.saveData) {
                    audibleFeedDecoders.forEach(node => { if (!node.isConnected) audibleFeedDecoders.delete(node); });
                    if (!audibleFeedDecoders.has(video) && audibleFeedDecoders.size >= VIDEO_WARMUP_LIMIT + 1) {
                        const spare = [...audibleFeedDecoders].find(node => node !== autoplayTarget);
                        if (spare) { preparingVideoPosters.get(spare)?.(); audibleFeedDecoders.delete(spare); }
                    }
                    audibleFeedDecoders.add(video);
                    const nearby = [...videoWarmupCandidates].filter(node => node !== video && node.isConnected && isFeedVideoPlayer(node))
                        .sort((a, b) => Math.abs(a.getBoundingClientRect().top - video.getBoundingClientRect().top)
                            - Math.abs(b.getBoundingClientRect().top - video.getBoundingClientRect().top));
                    for (const node of nearby) {
                        if (audibleFeedDecoders.size >= VIDEO_WARMUP_LIMIT + 1) break;
                        audibleFeedDecoders.add(node);
                        // Starting these silent spares in the original tap grants
                        // their decoders the same playback activation as the owner.
                        preparingVideoPosters.get(node)?.();
                        warmedVideoSources.set(node, node.src);
                        this.prepareVideoPoster(node, true);
                    }
                }
            }
        },

        // iOS audible permission belongs to this exact video element. Reuse the
        // reader-unmuted decoder as the feed advances, including newly appended
        // pages; leave an ordinary paused decoder and poster in the previous tile.
        reuseAudibleFeedVideo(next, video = audibleFeedVideo) {
            if (this.videoMuted || !isPhoneLayout() || !video || video === next || !next
                || video.closest?.('.lightbox-stage') || !video.parentElement || !next.parentElement) return next;
            this.restoreVideoResource(video);
            this.restoreVideoResource(next);
            preparingVideoPosters.get(next)?.();
            preparingVideoPosters.get(video)?.();
            this.pauseAutoplayVideo(video);
            this.pauseAutoplayVideo(next);
            const attributes = node => [...node.attributes].map(attribute => [attribute.name, attribute.value]);
            const previous = attributes(video), incoming = attributes(next);
            const time = video.currentTime;
            const nextTime = next.currentTime;
            const oldPlayer = video.parentElement, newPlayer = next.parentElement;
            // A single video sizes its tile. Between the two moves one tile is
            // empty, and iOS re-anchors the scroll to that collapse, jumping the
            // feed and stopping its momentum; hold both heights until both are filled.
            const players = [oldPlayer, newPlayer];
            const heights = players.map(player => player.style.height);
            const locked = players.map(player => player.offsetHeight);
            players.forEach((player, index) => { if (locked[index]) player.style.height = `${locked[index]}px`; });
            oldPlayer.replaceChild(next, video);
            newPlayer.prepend(video);
            const restore = (node, attrs, position) => {
                const src = attrs.find(([name]) => name === 'src')?.[1] || '';
                const sameSource = node.getAttribute('src') === src;
                // Keep the live resource when repeated tiles use the same clip.
                // Removing and restoring src also resets a fully warm decoder.
                for (const attribute of [...node.attributes]) if (attribute.name !== 'src') node.removeAttribute(attribute.name);
                for (const [name, value] of attrs) if (name !== 'src' && name !== 'preload') node.setAttribute(name, value);
                node.preload = node === video ? 'auto' : 'none';
                // Mask a previously presented frame synchronously, before src is
                // changed; loadstart/emptied arrive in a later event task.
                videoPosterStates.get(node)?.reset();
                if (!sameSource) node.src = src;
                if (position > 0 || sameSource) node.currentTime = position;
                heldAutoplayVideos.delete(node);
                failedAutoplayVideos.delete(node);
                forcedMuteVideos.delete(node);
                tweetNookSyncVideoDuration(node);
                tweetNookSyncVideoUi(node);
            };
            restore(next, previous, time);
            restore(video, incoming, nextTime);
            players.forEach((player, index) => { player.style.height = heights[index]; });
            return video;
        },

        setVideoPlayback(mode) {
            autoplayRequest = null;
            autoplayTarget = null;
            manualAutoplay = null;
            this.videoPlayback = mode === 'autoplay' ? 'autoplay' : 'paused';
            localStorage.setItem('tvx-video-playback', this.videoPlayback);
            const autoplay = this.videoPlayback === 'autoplay';
            this.videoMuted = autoplay;
            document.querySelectorAll(VIDEO_PLAYER_SELECTOR).forEach(video => {
                forcedMuteVideos.delete(video);
                this.applyVideoAudio(video);
                if (!autoplay && isFeedVideoPlayer(video)) video.pause();
            });
            this.autoplayNextVideo();
        },

        updateAutoplayVisibility(video, visible, select = true) {
            if (!isFeedVideoPlayer(video)) {
                autoplayVideos.delete(video);
                return;
            }
            if (visible) {
                autoplayVideos.add(video);
            } else {
                autoplayVideos.delete(video);
                heldAutoplayVideos.delete(video);
                failedAutoplayVideos.delete(video);
                if (this.videoPlayback === 'autoplay' && !preparingVideoPosters.has(video)) this.pauseAutoplayVideo(video);
            }
            if (select) this.autoplayNextVideo();
        },

        pauseAutoplayVideo(video) {
            if (video.paused) { playingVideoPlayers.delete(video); return; }
            automaticallyPausedVideos.add(video);
            video.pause();
            playingVideoPlayers.delete(video);
        },

        trackVideoScrollTouch(event) {
            const touch = event?.touches?.length === 1 && event.touches[0];
            if (!touch || this.lightboxOpen || event.target?.closest?.('[data-video-seek]')) {
                videoTouchSample = null;
                return;
            }
            const sample = {x: touch.clientX, y: touch.clientY, time: event.timeStamp};
            const previous = videoTouchSample;
            sample.startX = event.type === 'touchstart' ? sample.x : previous?.startX ?? sample.x;
            sample.startY = event.type === 'touchstart' ? sample.y : previous?.startY ?? sample.y;
            sample.stopped = event.type === 'touchstart' ? false : previous?.stopped || false;
            videoTouchSample = sample;
            if (event.type !== 'touchmove' || !previous || sample.stopped || this.videoPlayback !== 'autoplay') return;
            const dx = sample.x - previous.x, dy = sample.y - previous.y;
            const carousel = event.target?.closest?.('.media-carousel-track');
            const distance = Math.max(Math.abs(sample.y - sample.startY), carousel ? Math.abs(sample.x - sample.startX) : 0);
            const speed = Math.max(Math.abs(dy), carousel ? Math.abs(dx) : 0) / Math.max(1, sample.time - previous.time);
            if (distance < 48 || speed < 1) return;
            // One preemptive stop per gesture; subsequent touch samples must
            // not repeatedly interrupt the new visible owner during a drag.
            sample.stopped = true;
            // Safari's compositor can fling many screens before JS receives a
            // scroll event or frame. Stop the old owner in the input task, and
            // wait only for fresh scroll geometry, never for scrolling to end.
            videoScrollIntent = true;
            clearTimeout(videoScrollIntentTimer);
            videoScrollIntentTimer = setTimeout(() => this.releaseVideoScrollIntent(), 120);
            new Set([...playingVideoPlayers, autoplayTarget].filter(Boolean)).forEach(video => {
                if (isFeedVideoPlayer(video) && !preparingVideoPosters.has(video)) this.pauseAutoplayVideo(video);
            });
        },

        releaseVideoScrollIntent() {
            if (!videoScrollIntent) return;
            videoScrollIntent = false;
            clearTimeout(videoScrollIntentTimer);
            videoScrollIntentTimer = null;
            this.queueVideoVisibility();
        },

        queueVideoVisibility() {
            // Scroll delivery establishes the new position after a fast swipe.
            videoScrollIntent = false;
            clearTimeout(videoScrollIntentTimer);
            videoScrollIntentTimer = null;
            // Stop outgoing audio in the scroll event, then select using fresh
            // geometry on the next frame, even while the reader keeps scrolling.
            this.stopOffscreenVideoPlayers();
            if (videoVisibilityFrame !== null) return;
            videoVisibilityFrame = requestAnimationFrame(() => {
                videoVisibilityFrame = null;
                this.autoplayNextVideo();
            });
        },

        watchPlayingVideoVisibility() {
            if (playingVideoVisibilityFrame !== null || typeof requestAnimationFrame !== 'function') return;
            playingVideoVisibilityFrame = requestAnimationFrame(() => {
                playingVideoVisibilityFrame = null;
                // iOS can move the picture before delivering its scroll event.
                // Check running players before each paint as well as on scroll.
                this.stopOffscreenVideoPlayers();
                if (this.videoPlayback === 'autoplay' && [...playingVideoPlayers].some(video =>
                    !video.paused && isFeedVideoPlayer(video) && !preparingVideoPosters.has(video))) {
                    this.watchPlayingVideoVisibility();
                }
            });
        },

        stopOffscreenVideoPlayers() {
            if (this.videoPlayback !== 'autoplay') return;
            new Set([...playingVideoPlayers, autoplayTarget].filter(Boolean)).forEach(video => {
                if (isFeedVideoPlayer(video) && !preparingVideoPosters.has(video)
                    && (!video.isConnected || feedVideoVisibility(video).ratio < VIDEO_AUTOPLAY_STOP_RATIO)) this.pauseAutoplayVideo(video);
            });
        },

        // Pause before starting the next player, including during fast scrolling.
        autoplayNextVideo() {
            if (this.videoPlayback !== 'autoplay') { this.updateVideoWarmup(); return; }
            autoplayVideos.forEach(video => {
                // Mobile viewing moves the live inline decoder into the viewer.
                // Its playback belongs to that viewer until the handoff returns it.
                if (!video.isConnected || !isFeedVideoPlayer(video)) autoplayVideos.delete(video);
            });
            const players = [...autoplayVideos];
            const blocked = videoScrollIntent || document.hidden || this.lightboxOpen || this.showSettingsModal || this.showStatsModal || this.showSetupModal;
            const visible = players.map(video => ({video, ...feedVideoVisibility(video), gallery: video.closest?.('.media-carousel-track')}));
            visible.forEach(({video, ratio}) => {
                if (ratio <= 0) {
                    heldAutoplayVideos.delete(video);
                    failedAutoplayVideos.delete(video);
                }
            });
            const candidates = visible.filter(({video}) => !heldAutoplayVideos.has(video) && !failedAutoplayVideos.has(video));
            const current = candidates.find(({video, ratio}) => video === autoplayTarget && ratio >= VIDEO_AUTOPLAY_STOP_RATIO);
            if (manualAutoplay && (!current || current.video !== manualAutoplay.video
                || Math.abs((window.scrollY || 0) - manualAutoplay.scrollY) > 1
                || (current.gallery && Math.abs(current.gallery.scrollLeft - manualAutoplay.scrollLeft) > 1))) manualAutoplay = null;
            let selected = current || candidates
                .filter(({ratio}) => ratio >= VIDEO_AUTOPLAY_START_RATIO)
                .sort((a, b) => a.top - b.top || (a.left || 0) - (b.left || 0))[0];
            if (!manualAutoplay) {
                const prominent = candidates.filter(candidate => candidate.ratio >= VIDEO_AUTOPLAY_START_RATIO)
                    .sort((a, b) => (b.visibleArea || 0) - (a.visibleArea || 0) || a.top - b.top || (a.left || 0) - (b.left || 0))[0];
                if (prominent && (!current || prominent.visibleArea > current.visibleArea
                    + VIDEO_AUTOPLAY_SWITCH_MARGIN * Math.min(prominent.pictureArea, current.pictureArea))) selected = prominent;
            }
            const next = blocked ? null : selected?.video;
            autoplayTarget = next;
            this.restoreVideoResource(next);
            // A selected player never waits for speculative buffering to finish.
            preparingVideoPosters.get(next)?.();
            new Set([...players, ...playingVideoPlayers]).forEach(video => {
                if (video !== next && isFeedVideoPlayer(video) && !preparingVideoPosters.has(video)) this.pauseAutoplayVideo(video);
            });
            this.updateVideoWarmup();
            if (autoplayRequest?.video !== next) autoplayRequest = null;
            if (!next || preparingVideoPosters.has(next) || !next.paused || autoplayRequest?.video === next) return;
            const request = { video: next };
            autoplayRequest = request;
            pendingAutoplayRequests.set(next, request);
            this.applyVideoAudio(next);
            const stillSelected = () => autoplayTarget === next && next.isConnected && !heldAutoplayVideos.has(next)
                && this.videoPlayback === 'autoplay' && !videoScrollIntent && !document.hidden
                && !this.lightboxOpen && !this.showSettingsModal && !this.showStatsModal && !this.showSetupModal
                && feedVideoVisibility(next).ratio >= VIDEO_AUTOPLAY_STOP_RATIO;
            const activeRequest = () => autoplayRequest === request && stillSelected();
            const finish = failed => {
                if (pendingAutoplayRequests.get(next) === request) pendingAutoplayRequests.delete(next);
                if (autoplayRequest !== request) return;
                autoplayRequest = null;
                if (failed) failedAutoplayVideos.add(next);
            };
            const played = () => {
                // A newer request for the same video owns any resumed playback.
                const viewerOwnsPlayback = this.lightboxOpen && next.closest?.('.lightbox-stage');
                if (!stillSelected() && !viewerOwnsPlayback) this.pauseAutoplayVideo(next);
                if (stillSelected() && !next.muted && isPhoneLayout()) audibleFeedVideo = next;
                finish(false);
                this.updateVideoWarmup();
            };
            const result = next.play();
            result?.then?.(played, error => {
                if (!activeRequest()) { finish(false); return; }
                // Source changes and interrupted loads reject with AbortError.
                // They say nothing about audible permission and must not mute
                // a decoder the reader already authorized.
                if (error?.name !== 'NotAllowedError') { finish(error?.name !== 'AbortError'); return; }
                if (next.muted) { finish(true); return; }
                // Try the already-prepared element first. Some mobile browsers
                // allow it after interaction; resetting its source would throw
                // away the warm decoder. Safari's per-element permission can
                // still require moving the reader-authorized player instead.
                const reused = this.reuseAudibleFeedVideo(next);
                if (reused !== next) {
                    finish(false);
                    autoplayVideos.add(reused);
                    autoplayTarget = reused;
                    this.autoplayNextVideo();
                    return;
                }
                // Browsers can refuse audible autoplay before the page has had a gesture.
                forcedMuteVideos.add(next);
                next.muted = true;
                next.play()?.then?.(played, () => finish(activeRequest()));
            });
        },

        // Only one player runs at a time, however it was started.
        handleVideoPlay(video) {
            if (!isVideoPlayer(video) || video.paused) return;
            playingVideoPlayers.add(video);
            if (preparingVideoPosters.has(video)) return;
            if (this.videoPlayback === 'autoplay' && isFeedVideoPlayer(video)
                && (videoScrollIntent || document.hidden || this.lightboxOpen || this.showSettingsModal || this.showStatsModal || this.showSetupModal
                    || feedVideoVisibility(video).ratio < VIDEO_AUTOPLAY_STOP_RATIO)) {
                this.pauseAutoplayVideo(video);
                return;
            }
            // A late play event from a superseded decoder must not interrupt
            // the player now selected by the scroll/observer pass.
            const pending = pendingAutoplayRequests.get(video);
            if (pending && this.videoPlayback === 'autoplay' && isFeedVideoPlayer(video) && autoplayTarget !== video) {
                this.pauseAutoplayVideo(video);
                return;
            }
            if (this.videoPlayback === 'autoplay' && isFeedVideoPlayer(video)) this.watchPlayingVideoVisibility();
            document.querySelectorAll(VIDEO_PLAYER_SELECTOR).forEach(other => {
                if (other !== video && !other.paused && !preparingVideoPosters.has(other)) this.pauseAutoplayVideo(other);
            });
            this.applyVideoAudio(video);
            if (this.videoPlayback !== 'autoplay') return;
            if (!pending && isFeedVideoPlayer(video)) {
                // An explicit play owns its tile until the reader scrolls again.
                // Background preparation and autoplay promises never enter here.
                autoplayTarget = video;
                autoplayVideos.add(video);
                manualAutoplay = {video, scrollY: window.scrollY || 0, scrollLeft: video.closest?.('.media-carousel-track')?.scrollLeft || 0};
            }
            heldAutoplayVideos.delete(video);
            failedAutoplayVideos.delete(video);
        },

        handleVideoPause(video) {
            if (video.paused) playingVideoPlayers.delete(video);
            // Media events are queued. A pause from the previous source can be
            // delivered after this decoder has already resumed in another tile.
            if (automaticallyPausedVideos.delete(video) || !video.paused) return;
            if (this.videoPlayback === 'autoplay' && autoplayVideos.has(video)) heldAutoplayVideos.add(video);
        },

        handleVideoVolumeChange(video) {
            if (syncingVideoAudio || preparingVideoPosters.has(video) || !isVideoPlayer(video)) return;
            const expanded = expandedVideoAudio.get(video);
            if (expanded) {
                expanded.muted = video.muted;
                expanded.volume = video.volume;
                return;
            }
            // A browser-imposed mute must not silence the other players, but
            // volume adjustments on that player still belong to the reader.
            const muted = forcedMuteVideos.has(video) && video.muted ? this.videoMuted : video.muted;
            if (!video.muted) forcedMuteVideos.delete(video);
            if (muted === this.videoMuted && video.volume === this.videoVolume) return;
            this.setSharedVideoAudio(video, muted, video.volume);
        },

        // Leaving a view stops playback and rewinds what played. Untouched players
        // are left alone: a seek on each of a long feed's videos rewrote every
        // player's time labels and forced the whole feed to lay out again.
        pauseAllVideos() {
            document.querySelectorAll('video').forEach(v => {
                if (!v.paused) v.pause();
                if (v.currentTime > 0) v.currentTime = 0;
            });
        },
    };
}
