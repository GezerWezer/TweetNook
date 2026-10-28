/**
 * The media viewer: opening and closing it from a post's photos, GIFs and videos, its
 * open and close motion, swipe gestures to page and dismiss, and on phones handing a
 * video between its inline player and the viewer.
 */

function lightboxMethods() {
    let lightboxMotionTimer = null;
    let lightboxSpringFrame = null;
    // Mobile video viewing hands playback back to the original inline player.
    let lightboxOriginVideo = null;
    // Photos and GIFs return to their tile in the post that opened them.
    let lightboxOriginHolder = null;
    let lightboxInlineMount = null;
    const lightboxInlineMounts = new Map();
    const lightboxOriginVideos = new Map();
    let lightboxViewportContent = null;
    let lightboxVideoMotion = null;
    let lightboxVideoReadyCancel = null;
    // The mobile video backdrop is page content rather than a fixed layer, so
    // Safari paints it beneath its toolbars too. It spans a viewport above and
    // below the visible page without extending the document.
    let lightboxShade = null;
    const placeLightboxShade = () => {
        const height = window.innerHeight || 800;
        const top = (window.scrollY || 0) - height;
        const bottom = Math.min(document.documentElement?.scrollHeight || Infinity, (window.scrollY || 0) + 2 * height);
        lightboxShade.style.top = `${top}px`;
        lightboxShade.style.height = `${Math.max(0, bottom - top)}px`;
    };

    return {
        lightboxOpen: false,
        lightboxMedia: [],
        lightboxInlineIndex: null,
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
        lightboxChromeHidden: false,
        lightboxSettledIndex: 0,
        lightboxTweet: null,

        lightboxMotionDuration(duration) {
            return window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches ? 0 : duration;
        },

        queueLightboxMotion(callback, delay) {
            clearTimeout(lightboxMotionTimer);
            lightboxMotionTimer = setTimeout(callback, this.lightboxMotionDuration(delay));
            lightboxMotionTimer?.unref?.();
        },

        cancelQueuedLightboxMotion() {
            clearTimeout(lightboxMotionTimer);
        },

        resetLightboxMotion() {
            lightboxVideoReadyCancel?.();
            lightboxVideoReadyCancel = null;
            lightboxVideoMotion?.cancel();
            lightboxVideoMotion = null;
            this.lightboxVideoMotionPhase = '';
            clearTimeout(lightboxMotionTimer);
            lightboxMotionTimer = null;
            if (lightboxSpringFrame !== null) cancelAnimationFrame(lightboxSpringFrame);
            lightboxSpringFrame = null;
            this.lightboxGestureStart = null;
            this.lightboxGestureAxis = null;
            this.lightboxGestureOffsetX = 0;
            this.lightboxGestureOffsetY = 0;
            this.lightboxGestureTransition = 'none';
            this.lightboxGestureAnimating = false;
            this.lightboxDismissPending = false;
        },

        openLightbox(media, index = 0, originVideo = null, origin = null) {
            if (this.lightboxOpen && lightboxOriginVideo) return;
            this.resetLightboxMotion();
            this.lightboxMedia = Array.isArray(media) ? media : [];
            const lastIndex = Math.max(0, this.lightboxMedia.length - 1);
            this.lightboxIndex = Math.min(lastIndex, Math.max(0, Number(index) || 0));
            this.lightboxSettledIndex = this.lightboxIndex;
            lightboxOriginVideo = originVideo;
            lightboxOriginHolder = (origin || originVideo)?.closest?.('[data-lightbox-media]') || null;
            lightboxOriginVideos.clear();
            this.lightboxInlineIndex = null;
            this.lightboxChromeHidden = false;
            this.lightboxTweet = this.lightboxTweetFor(origin || originVideo);
            if (originVideo && this.lightboxMedia[this.lightboxIndex]) {
                preparingVideoPosters.get(originVideo)?.();
                const inlinePlayer = this.isMobileVideoViewer() && Boolean(tweetNookVideoPlayer(originVideo)?.cloneNode);
                lightboxOriginVideos.set(this.lightboxIndex, originVideo);
                if (inlinePlayer) {
                    this.lightboxInlineIndex = this.lightboxIndex;
                    lightboxOriginHolder?.querySelectorAll?.('video[data-media-index]').forEach(video => {
                        if (isVideoPlayer(video)) lightboxOriginVideos.set(Number(video.dataset.mediaIndex), video);
                    });
                }
                this.lightboxMedia = this.lightboxMedia.map((item, itemIndex) => {
                    const video = lightboxOriginVideos.get(itemIndex);
                    if (!video) return item;
                    this.restoreVideoResource(video);
                    preparingVideoPosters.get(video)?.();
                    return {...item, currentTime: video.currentTime, muted: video.muted, volume: video.volume, loop: video.loop, inlinePlayer};
                });
                if (!inlinePlayer) originVideo.pause();
            }
            this.lightboxOpen = this.lightboxMedia.length > 0;
            if (this.isMobileVideoViewer()) this.setLightboxViewport(true);
            this.followLightboxShade(this.lightboxOpen);
            const animate = this.isMobileViewer() && this.lightboxMotionDuration(300) && this.lightboxMotionOrigin()?.getBoundingClientRect;
            if (animate) this.lightboxVideoMotionPhase = 'preparing';
            // Reparent before play(), within the original tap's user activation.
            // iOS must not be asked to unlock an audible player in a later tick.
            this.mountInlineLightboxVideo();
            if (lightboxInlineMount) this.setExpandedVideoAudio(originVideo, true);
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
            // Only touch dismissals are followed by Safari's late compatibility click.
            if (this.isMobileViewer()) videoTapBlockedUntil = Date.now() + 600;
            const preparing = this.lightboxVideoMotionPhase === 'preparing';
            const active = this.activeLightboxVideo();
            if (active) tweetNookFinishVideoScrub(active);
            this.revealLightboxOrigin();
            const media = this.activeLightboxMedia();
            // A phone close returns the picture to its feed position when that is
            // on screen, and otherwise fades; it never hands off to Alpine's fade.
            const mobile = !preparing && this.lightboxOpen && this.isMobileViewer() && this.lightboxMotionDuration(280);
            const animate = mobile && media && this.lightboxReturnOrigin();
            const shade = this.$refs.lightboxShade;
            const startBounds = animate ? {
                ...(lightboxVideoMotion?.bounds() || {...fittedMediaRect(media), radius: 0}),
                backdropOpacity: shade ? Number(getComputedStyle(shade).opacity) : Number(this.lightboxBackdropStyle()['--lightbox-backdrop-opacity']),
            } : null;
            this.resetLightboxMotion();
            const slide = lightboxInlineMount?.player.closest?.('.lightbox-slide') || media?.closest?.('.lightbox-slide');
            if (lightboxInlineMount?.player.parentElement?.style) {
                lightboxInlineMount.player.parentElement.style.transform = 'translate3d(0, 0, 0)';
                lightboxInlineMount.player.parentElement.style.transition = 'none';
            }
            if (slide?.style && (animate || lightboxInlineMount)) {
                // The reactive drag transform updates later. Neutralize it now
                // before measuring the destination for the return animation.
                slide.style.transform = 'translate3d(0, 0, 0)';
                slide.style.transition = 'none';
            }
            if (animate && this.startLightboxVideoMotion(false, startBounds)) return;
            if (mobile && this.fadeLightbox(false)) return;
            this.finishCloseLightbox();
        },

        activeLightboxVideo() {
            return lightboxInlineMounts.get(this.lightboxIndex)?.surface.querySelector('video')
                || this.$refs.lightboxStage?.querySelector?.('.lightbox-slide.is-active video[data-video-player]');
        },

        activeLightboxMedia() {
            return this.activeLightboxVideo() || this.$refs.lightboxStage?.querySelector?.('.lightbox-slide.is-active .lightbox-media');
        },

        // The feed element the current picture expands from and returns to.
        lightboxMotionOrigin() {
            if (lightboxInlineMount) return lightboxInlineMount.placeholder;
            if (lightboxOriginVideo) return lightboxOriginVideo;
            const pictures = [...(lightboxOriginHolder?.querySelectorAll?.('img, video') || [])]
                .filter(element => !element.hasAttribute?.('data-video-poster'));
            const element = pictures[this.lightboxIndex];
            return element?.isConnected ? element : null;
        },

        revealLightboxOrigin(origin = this.lightboxMotionOrigin()) {
            const track = origin?.closest?.('.media-carousel-track');
            const tile = origin?.closest?.('.media-carousel-item');
            if (!track || !tile) return;
            const frame = tile.getBoundingClientRect(), clip = track.getBoundingClientRect();
            track.scrollLeft += frame.left - clip.left - Math.max(0, (clip.width - frame.width) / 2);
        },

        lightboxReturnOrigin() {
            const origin = this.lightboxMotionOrigin();
            const rect = origin?.getBoundingClientRect?.();
            return rect?.width && rect.height && rect.top < (window.innerHeight || Infinity) && rect.top + rect.height > 0 ? origin : null;
        },

        followLightboxShade(follow) {
            if (lightboxShade) {
                window.removeEventListener('scroll', placeLightboxShade);
                window.removeEventListener('resize', placeLightboxShade);
            }
            lightboxShade = follow ? this.$refs.lightboxShade : null;
            if (!lightboxShade) return;
            placeLightboxShade();
            window.addEventListener('scroll', placeLightboxShade, {passive: true});
            window.addEventListener('resize', placeLightboxShade);
        },

        mountInlineLightboxVideo() {
            const stage = this.$refs.lightboxStage;
            lightboxOriginVideos.forEach((video, index) => {
                if (!this.lightboxMedia[index]?.inlinePlayer || lightboxInlineMounts.has(index)) return;
                const slot = index === this.lightboxInlineIndex
                    ? this.$refs.inlineVideoSlot || stage?.querySelector?.('[data-inline-video-slot]')
                    : stage?.querySelector?.(`[data-inline-video-index="${index}"]`);
                const player = tweetNookVideoPlayer(video);
                if (!slot || !player) return;
                const placeholder = player.cloneNode(false);
                const picture = getComputedStyle(video);
                placeholder.setAttribute('data-feed-fit', picture.objectFit || 'contain');
                if (picture.backgroundColor) placeholder.setAttribute('data-feed-background', picture.backgroundColor);
                const frame = player.getBoundingClientRect();
                placeholder.style.setProperty('aspect-ratio', `${frame.width} / ${frame.height}`);
                placeholder.setAttribute('aria-hidden', 'true');
                placeholder.removeAttribute('tabindex');
                player.parentElement.insertBefore(placeholder, player);
                const surface = document.createElement('div');
                surface.setAttribute('class', 'video-motion-surface');
                player.insertBefore(surface, video);
                surface.appendChild(video);
                lightboxInlineMounts.set(index, {player, placeholder, surface, preload: video.preload});
                player.classList.add('lightbox-video-player');
                // Fill portrait footage without changing its aspect ratio; the
                // existing fit control can reveal the uncropped picture.
                const aspect = video.videoWidth / video.videoHeight || Number(video.dataset?.videoAspect);
                player.classList.toggle('is-video-fill', aspect > 0 && aspect < 1);
                video.setAttribute('data-viewer-fit-ready', '');
                video.setAttribute('data-lightbox-index', String(index));
                video.preload = 'auto';
                slot.appendChild(player);
                if (index !== this.lightboxIndex || !this.$refs.lightboxViewer) return;
                const viewer = this.$refs.lightboxViewer;
                viewer.classList.remove('is-video-closed');
                viewer.classList.add('is-video-viewer');
                if (this.lightboxVideoMotionPhase) viewer.classList.add('is-video-motion');
                viewer.style.display = 'flex';
                viewer.style.setProperty?.('--lightbox-video-lift', `${this.lightboxVideoLift()}px`);
                slot.style.display = '';
                // On reopening a later gallery item Alpine has not yet moved
                // this persistent slot from its previous index. Measure the
                // opening flight in its active position, within this tap.
                slot.style.transform = 'translate3d(0, 0, 0)';
                slot.style.transition = 'none';
                slot.classList.add('is-active');
            });
            lightboxInlineMount = lightboxInlineMounts.get(this.lightboxIndex) || null;
        },

        restoreInlineLightboxVideo() {
            lightboxInlineMounts.forEach(({player, placeholder, surface, preload}) => {
                const video = surface.querySelector('video');
                surface.replaceWith(video);
                video.removeAttribute('data-lightbox-index');
                video.removeAttribute('data-viewer-fit-ready');
                video.preload = preload;
                player.classList.remove('lightbox-video-player', 'is-video-fill');
                if (placeholder.isConnected) placeholder.replaceWith(player);
                else { video.pause(); player.remove(); }
            });
            lightboxInlineMounts.clear();
            lightboxInlineMount = null;
            this.lightboxInlineIndex = null;
        },

        isMobileViewer() {
            return Boolean(this.lightboxMedia[this.lightboxIndex])
                && isPhoneLayout();
        },

        isMobileVideoViewer() {
            const media = this.lightboxMedia[this.lightboxIndex];
            return media?.type === 'video' && !media.isGif && this.isMobileViewer();
        },

        setLightboxViewport(expanded) {
            const meta = document.querySelector?.('meta[name="viewport"]');
            if (!meta) return;
            if (expanded && lightboxViewportContent === null) {
                lightboxViewportContent = meta.getAttribute('content') || '';
                const content = lightboxViewportContent.replace(/(?:^|,)\s*viewport-fit\s*=\s*[^,]*/gi, '');
                meta.setAttribute('content', `${content}, viewport-fit=cover`);
            } else if (!expanded && lightboxViewportContent !== null) {
                meta.setAttribute('content', lightboxViewportContent);
                lightboxViewportContent = null;
            }
        },

        prepareLightboxVideoMotion() {
            const media = this.activeLightboxMedia();
            if (!media) { this.lightboxVideoMotionPhase = ''; return; }
            // The portaled video already has its feed picture/poster. Expand it
            // immediately, including when a first tap is still loading metadata.
            if (lightboxInlineMount) {
                if (!this.startLightboxVideoMotion(true)) this.lightboxVideoMotionPhase = '';
                return;
            }
            // Wait for the image or the restored playback frame so the expanding
            // surface never replaces the feed picture with an empty or black frame.
            const origin = this.lightboxMotionOrigin();
            // A GIF carries on from the frame the feed was showing.
            const gif = media.tagName === 'VIDEO' && !isVideoPlayer(media) && origin?.tagName === 'VIDEO';
            const follow = () => {
                if (gif && Number.isFinite(media.duration) && media.duration > 0) media.currentTime = (origin.currentTime || 0) % media.duration;
            };
            const events = ['load', 'loadeddata', 'seeked'];
            let timeout;
            const cleanup = () => {
                clearTimeout(timeout);
                media.removeEventListener('loadedmetadata', follow);
                events.forEach(name => media.removeEventListener(name, ready));
            };
            const begin = () => {
                cleanup();
                lightboxVideoReadyCancel = null;
                if (!this.startLightboxVideoMotion(true) && !this.fadeLightbox(true)) this.lightboxVideoMotionPhase = '';
            };
            const ready = () => {
                if (media.tagName === 'IMG' ? media.complete && media.naturalWidth : media.readyState >= 2 && !media.seeking) begin();
            };
            lightboxVideoReadyCancel = cleanup;
            events.forEach(name => media.addEventListener(name, ready));
            if (gif && media.readyState >= 1) follow();
            else if (gif) media.addEventListener('loadedmetadata', follow, {once: true});
            timeout = setTimeout(begin, 500);
            ready();
        },

        startLightboxVideoMotion(opening, startBounds = null) {
            const motion = animateMobileMediaBounds(this.activeLightboxMedia(), this.lightboxMotionOrigin(), opening,
                this.lightboxMotionDuration(opening ? 300 : 280), startBounds, this.$refs.lightboxShade, this.lightboxRestingOpacity());
            if (!motion) return false;
            this.runLightboxMotion(motion, opening);
            return true;
        },

        // A phone viewer without a feed picture to move to fades in or out instead.
        fadeLightbox(opening) {
            const options = {duration: this.lightboxMotionDuration(180), easing: 'ease-out', fill: 'both'};
            const fades = [this.$refs.lightboxViewer, this.$refs.lightboxShade].filter(element => element?.animate).map(element => {
                // Closing continues from the shade's dragged opacity.
                const current = opening ? 0 : Number(getComputedStyle(element).opacity ?? 1);
                // The shade opens to the resting opacity its reactive style keeps.
                const target = !opening ? 0 : element === this.$refs.lightboxShade ? this.lightboxRestingOpacity() : 1;
                return element.animate([{opacity: Number.isFinite(current) ? current : 1}, {opacity: target}], options);
            });
            if (!fades.length || !options.duration) return false;
            this.runLightboxMotion({
                cancel: () => fades.forEach(fade => fade.cancel()),
                bounds: () => null,
                finished: Promise.all(fades.map(fade => fade.finished)).then(() => true, () => false),
            }, opening);
            return true;
        },

        runLightboxMotion(motion, opening) {
            lightboxVideoMotion = motion;
            this.lightboxVideoMotionPhase = opening ? 'opening' : 'closing';
            motion.finished.then(completed => {
                if (lightboxVideoMotion !== motion) return;
                if (completed && !opening) { this.finishCloseLightbox(); return; }
                motion.cancel();
                lightboxVideoMotion = null;
                this.lightboxVideoMotionPhase = '';
                this.$refs.lightboxViewer?.classList.remove('is-video-motion');
                const video = this.activeLightboxVideo();
                if (completed && opening && video) tweetNookShowVideoUi(video);
            });
        },

        // Whether the viewer is showing this feed player in place, borrowed from its card.
        isInlineLightboxPlayer(player) {
            return lightboxInlineMount?.player === player;
        },

        finishCloseLightbox() {
            const active = this.activeLightboxVideo();
            const origin = lightboxOriginVideo;
            const resume = Boolean(origin && active && !active.paused && !active.ended);
            if (origin && active && origin !== active) {
                origin.currentTime = active.currentTime;
            }
            this.$refs.lightboxStage?.querySelectorAll?.('video').forEach(video => this.setExpandedVideoAudio(video, false));
            if (origin) this.setExpandedVideoAudio(origin, false);
            if (origin && !resume) heldAutoplayVideos.add(origin);
            this.lightboxOpen = false;
            if (this.isMobileViewer()) videoTapBlockedUntil = Date.now() + 500;
            if ((lightboxInlineMount || this.isMobileViewer()) && this.$refs.lightboxViewer) {
                // Hide the viewer and restore the feed in one handoff; never
                // expose a fullscreen frame while Alpine schedules leave.
                this.$refs.lightboxViewer.classList.add('is-video-closed');
                this.$refs.lightboxViewer.classList.remove('is-video-controls-hidden');
                this.$refs.lightboxViewer.style.display = 'none';
            }
            this.restoreInlineLightboxVideo();
            this.setLightboxViewport(false);
            if (origin?.isConnected && isFeedVideoPlayer(origin)) {
                this.revealLightboxOrigin(origin);
                const track = origin.closest?.('.media-carousel-track');
                if (resume && feedVideoVisibility(origin).ratio >= VIDEO_AUTOPLAY_STOP_RATIO) this.resumeAutoplayAt(origin, track);
                // Rejoin even when portaling happened within the observer's
                // near-viewport margin and produced no new intersection entry.
                this.updateAutoplayVisibility(origin, true, false);
            }
            lightboxOriginVideo = null;
            lightboxOriginVideos.clear();
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
            this.mountInlineLightboxVideo();
            const videos = [...stage.querySelectorAll('video')];
            const isActive = video => {
                const indexed = video.getAttribute?.('data-lightbox-index');
                return indexed !== null && indexed !== undefined
                    ? Number(indexed) === this.lightboxIndex : video.closest('.lightbox-slide')?.classList.contains('is-active');
            };
            videos.forEach(video => { if (!isActive(video)) video.pause(); });
            videos.forEach(video => {
                if (!isActive(video)) return;
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
                    this.setExpandedVideoAudio(video, true);
                    if (this.isMobileVideoViewer() && !video.hasAttribute('data-viewer-fit-ready')) {
                        const player = tweetNookVideoPlayer(video);
                        const aspect = video.videoWidth / video.videoHeight || this.mediaFrameLayout(media?.m || {}, 'detail').sourceAspect;
                        player?.classList?.toggle('is-video-fill', aspect < 1);
                        video.setAttribute('data-viewer-fit-ready', '');
                    }
                    if ((video.hasAttribute('autoplay') || isVideoPlayer(video)) && video.paused !== false) video.play().catch(() => {});
                }
            });
        },

        lightboxVisibleIndices() {
            // While a page settles, the next neighbour mounts only once it stops:
            // mounting it mid-motion stalled a frame on the phone.
            const center = this.lightboxGestureAnimating ? this.lightboxSettledIndex : this.lightboxIndex;
            const first = Math.max(0, Math.min(this.lightboxIndex, center - 1));
            const last = Math.min(this.lightboxMedia.length - 1, Math.max(this.lightboxIndex, center + 1));
            const indices = [];
            for (let index = first; index <= last; index += 1) if (index !== this.lightboxInlineIndex) indices.push(index);
            // A post has at most four attachments. Keep its live video decoders
            // mounted so swiping back preserves both progress and permission.
            this.lightboxMedia.forEach((media, index) => {
                if (media.inlinePlayer && index !== this.lightboxInlineIndex && !indices.includes(index)) indices.push(index);
            });
            return indices.sort((a, b) => a - b);
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

        // Desktop photos keep a hint of the page behind them; phones (as in X) and
        // video get a solid backdrop.
        lightboxRestingOpacity() {
            return this.isMobileViewer() ? 1 : 0.95;
        },

        lightboxBackdropStyle() {
            const height = Math.max(1, window.innerHeight || 800);
            const progress = Math.min(1, Math.max(0, this.lightboxGestureOffsetY) / (height * 0.7));
            const opacity = this.lightboxVideoMotionPhase === 'preparing' || this.lightboxDismissPending ? 0
                : this.lightboxRestingOpacity() * (1 - progress * 0.72);
            // The spring sets the drag offset every frame, so only a dismissal fades.
            const transitionDuration = this.lightboxDismissPending ? this.lightboxMotionDuration(180) : 0;
            return {
                backgroundColor: `rgba(0, 0, 0, ${opacity})`,
                '--lightbox-backdrop-opacity': String(opacity),
                '--lightbox-video-lift': `${this.lightboxVideoLift()}px`,
                transition: transitionDuration ? `background-color ${transitionDuration}ms linear` : 'none',
            };
        },

        // X centres a phone video between the top of the screen and its playback
        // bar, 29.5pt above the middle of the space this page has under the status
        // bar. Lift it that far, or as far as the space above the picture allows.
        lightboxVideoLift() {
            const item = this.lightboxMedia[this.lightboxIndex];
            if (!this.isMobileVideoViewer() || !item) return 0;
            const width = window.innerWidth || 0;
            const box = (window.innerHeight || 0) - 80;
            const aspect = this.mediaFrameLayout(item.m || {}, 'detail').sourceAspect;
            if (aspect < 1) return 0;
            const height = Math.min(box, width / aspect);
            return Math.round(Math.max(0, Math.min(29.5, (box - height) / 2)) * 10) / 10;
        },

        // The phone backdrop is a black page layer whose opacity follows the drag.
        lightboxShadeStyle() {
            const {transition, '--lightbox-backdrop-opacity': opacity} = this.lightboxBackdropStyle();
            return {opacity, transition: transition.replace('background-color', 'opacity')};
        },

        lightboxChromeStyle() {
            // X fades a photo's controls in while the photo flies open.
            const photoOpening = this.lightboxVideoMotionPhase === 'opening' && this.isMobileViewer() && !this.isMobileVideoViewer();
            if ((this.lightboxVideoMotionPhase && !photoOpening) || this.lightboxDismissPending) return { opacity: '0' };
            // Phone controls stay put while photos page, as in X; a drag down fades them.
            const phonePaging = this.isMobileViewer() && this.lightboxGestureAxis === 'horizontal';
            if (this.lightboxGestureAnimating && !phonePaging) return { opacity: '0' };
            const distance = phonePaging ? Math.abs(this.lightboxGestureOffsetY) : Math.hypot(this.lightboxGestureOffsetX, this.lightboxGestureOffsetY);
            return { opacity: String(Math.max(0, 1 - distance / 180)) };
        },

        // The post whose media is open, for the viewer's counts and X link.
        lightboxTweetFor(origin) {
            const id = origin?.closest?.('[data-thread-id]')?.dataset?.threadId;
            if (id) return this.threadSeed(id)?.main || null;
            const main = origin?.closest?.('[data-thread-main]')?.dataset?.threadMain;
            if (main) return (main === 'panel' ? this.panelThreadData : this.threadData)?.main || null;
            const card = origin?.closest?.('[data-tweet-id]')?.dataset?.tweetId;
            return card ? this.threadSeed(card)?.main || null : null;
        },

        // A phone photo viewer shows or hides its controls on a tap, as X does;
        // elsewhere a tap beside the media closes it.
        lightboxTap(event, onMedia = false) {
            if (this.lightboxVideoMotionPhase) return;
            if (Date.now() < this.lightboxSuppressClickUntil) {
                this.lightboxSuppressClickUntil = 0;
                event?.stopPropagation?.();
                return;
            }
            if (this.isMobileViewer() && !this.isMobileVideoViewer()) {
                this.lightboxChromeHidden = !this.lightboxChromeHidden;
                return;
            }
            if (!onMedia) this.closeLightbox();
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
            if (this.lightboxOpen && this.isMobileViewer() && event.touches?.length === 1) event.preventDefault?.();
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

            // A lift right where the last move ended keeps that move's speed (Safari
            // reports the lift a few ms later); one after a pause still slows it.
            const still = touch.clientX === start.lastX && touch.clientY === start.lastY;
            const recent = (Number(event.timeStamp) || Date.now()) - start.lastTime < 50;
            const movement = this.updateLightboxGesture(touch, event.timeStamp, start.moved && !(still && recent));
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
                // A slow release past half the width, or a flick that way, pages.
                const flick = -start.velocityX * direction >= LIGHTBOX_FLICK && Math.abs(deltaX) >= 24;
                if (canNavigate && (Math.abs(this.lightboxGestureOffsetX) > width / 2 || flick)) {
                    this.commitLightboxNavigation(direction, start.velocityX);
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

            this.snapBackLightbox(start.velocityX, start.velocityY);
        },

        commitLightboxNavigation(direction, velocity = 0) {
            const width = Math.max(1, window.innerWidth || 320);
            this.lightboxGestureAxis = 'horizontal';
            this.lightboxIndex += direction;
            if (this.isMobileVideoViewer()) this.setLightboxViewport(true);
            lightboxOriginVideo = lightboxOriginVideos.get(this.lightboxIndex) || null;
            lightboxInlineMount = lightboxInlineMounts.get(this.lightboxIndex) || null;
            // Neighbors are mounted before the swipe. Start in this touch's
            // activation, then let Alpine update the slide classes.
            this.syncLightboxPlayback();
            this.lightboxGestureOffsetX += direction * width;
            this.$nextTick(() => this.syncLightboxPlayback());
            this.springLightbox(velocity, 0);
        },

        // Settle the drag offsets at rest with X's spring, a display frame at a
        // time so an iOS stall pauses the motion instead of skipping it.
        springLightbox(velocityX = 0, velocityY = 0) {
            if (lightboxSpringFrame !== null) cancelAnimationFrame(lightboxSpringFrame);
            lightboxSpringFrame = null;
            const fromX = this.lightboxGestureOffsetX, fromY = this.lightboxGestureOffsetY;
            this.lightboxGestureAnimating = true;
            this.lightboxGestureTransition = 'none';
            const settle = () => {
                lightboxSpringFrame = null;
                this.lightboxSettledIndex = this.lightboxIndex;
                this.lightboxGestureOffsetX = 0;
                this.lightboxGestureOffsetY = 0;
                this.lightboxGestureAxis = null;
                this.lightboxGestureAnimating = false;
                this.$nextTick(() => this.mountInlineLightboxVideo());
            };
            if (!this.lightboxMotionDuration(1) || typeof requestAnimationFrame !== 'function') { settle(); return; }
            let clock = 0, last = null;
            const step = now => {
                clock += last === null ? 0 : Math.min(now - last, 1000 / 60);
                last = now;
                const x = lightboxSpring(fromX, velocityX, clock), y = lightboxSpring(fromY, velocityY, clock);
                if (clock > 0 && Math.abs(x) < 0.5 && Math.abs(y) < 0.5) { settle(); return; }
                this.lightboxGestureOffsetX = x;
                this.lightboxGestureOffsetY = y;
                lightboxSpringFrame = requestAnimationFrame(step);
            };
            step(performance.now());
        },

        animateLightboxDismiss() {
            if (lightboxOriginVideo || (this.isMobileViewer() && this.lightboxReturnOrigin())) { this.closeLightbox(); return; }
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
            this.$refs.lightboxStage?.querySelectorAll?.('video').forEach(video => this.setExpandedVideoAudio(video, false));
            this.pauseLightboxPlayback();
            this.lightboxOpen = false;
            this.queueLightboxMotion(() => this.resetLightboxMotion(), 180);
        },

        snapBackLightbox(velocityX = 0, velocityY = 0) {
            this.springLightbox(velocityX, velocityY);
        },

        cancelLightboxGesture() {
            this.lightboxGestureStart = null;
            if (this.lightboxGestureOffsetX || this.lightboxGestureOffsetY) this.snapBackLightbox();
            else this.lightboxGestureAxis = null;
        },

        handleLightboxBackdropClick(event) {
            this.lightboxTap(event);
        },
    };
}
