/**
 * Media and link previews in cards: photo and video grids, carousels, quote
 * thumbnails, playback badges, link cards and avatars.
 */

function tweetMediaMethods() {
    return {
        renderCard(tweet, context = 'preview') {
            if (this.isPlaceholderTweet(tweet)) return '';
            if (this.articleForTweet(tweet)) return this.renderArticle(tweet, context === 'detail');
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
            const parts = [];

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
                    
                    parts.push(html`<div class="mt-3 flex flex-col space-y-[6px]">`);
                    choices.forEach(c => {
                        const pct = Math.round((c.count / total) * 100);
                        const isWinner = c.count === maxCount && maxCount > 0;
                        const weightClass = isWinner ? 'font-bold' : 'font-normal';
                        const barColor = isWinner ? 'bg-[var(--accent-color)] opacity-[0.4]' : 'bg-[var(--border-color)] opacity-[0.6]';
                        const label = c.label;
                        
                        parts.push(html`<div class="relative w-full h-[32px] rounded flex items-center overflow-hidden">
                                    <div class="absolute left-0 top-0 bottom-0 ${barColor} rounded" style="width: ${pct}%"></div>
                                    <span class="relative z-10 text-[15px] ${weightClass} text-[var(--text-primary)] w-full flex justify-between px-3">
                                        <span class="truncate pr-4" title="${label}">${label}</span>
                                        <span>${pct}%</span>
                                    </span>
                                 </div>`);
                    });
                    
                    const statusText = isFinal ? 'Final results' : '';
                    const dot = isFinal ? ' · ' : '';
                    parts.push(html`<div class="text-[14px] text-[var(--text-secondary)] mt-2">${trueTotal} votes${dot}${statusText}</div></div>`);
                }
            } else if (name === 'summary' || name === 'summary_large_image') {
                const title = binding.title?.string_value || '';
                const desc = binding.description?.string_value || '';
                const vanityUrl = binding.vanity_url?.string_value || '';
                const expandedUrl = httpURL(this.expandShortURL(
                    raw, binding.card_url?.string_value || card.legacy?.url || card.url
                ) || binding.vanity_url?.string_value || '#');
                let image = null;
                
                const imageKeys = [
                    'thumbnail_image_original', 'summary_photo_image_original',
                    'thumbnail_image_x_large', 'thumbnail_image_large', 'thumbnail_image',
                    'photo_image_full_size', 'summary_photo_image',
                ];
                for (const key of imageKeys) {
                    const localImage = this.localCardImage(tweet, binding[key]?.image_value?.url);
                    if (!localImage) continue;
                    image = localImage;
                    break;
                }
                
                if (title || desc || image) {
                    const large = name === 'summary_large_image' && image;
                    // A large card's title sits over its picture.
                    const overlay = large && title ? html`<span class="link-card-overlay-title">${title}</span>` : '';
                    const imageHtml = image ? html`<div class="link-card-image bg-[var(--bg-tertiary)] overflow-hidden">
                                    <img src="${image}" alt="" class="w-full h-full object-cover" loading="lazy" onerror="this.parentElement.hidden=true">
                                 ${overlay}</div>` : '';
                    parts.push(html`<a href="${expandedUrl}" target="_blank" rel="noopener noreferrer" class="link-card${name === 'summary' ? ' link-card-summary' : ''}${large ? ' link-card-large' : ''} mt-3 block border border-[var(--border-color)] rounded-xl overflow-hidden hover-bg transition cursor-pointer">
                                ${imageHtml}
                                <div class="link-card-text p-3">
                                    <div class="text-[13px] text-[var(--text-secondary)] mb-1">${vanityUrl}</div>
                                    <div class="text-[15px] text-[var(--text-primary)] font-bold leading-tight mb-1" style="display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;">${title}</div>
                                    <div class="text-[14px] text-[var(--text-secondary)]" style="display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;">${desc}</div>
                                </div>
                                ${large && vanityUrl ? html`<div class="link-card-from">From ${vanityUrl}</div>` : ''}
                             </a>`);
                }
            }
            return String(html`${parts}`);
        },

        localCardImage(tweet, remoteUrl) {
            if (!remoteUrl) return null;
            // Quote renderers wrap a normalized tweet in raw_json.
            const source = tweet.raw_json?.raw_json ? tweet.raw_json : tweet;
            const media = [...(source.card_media || []), ...(source.media || [])];
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
            const revision = avatarRevision ? '&r=' + encodeURIComponent(avatarRevision) : '';
            return '/api/avatar/' + encodedUserId + '?v=' + AVATAR_URL_VERSION + revision;
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

        // Avatar load and error events do not bubble; x-html content catches them on
        // the way down.
        avatarEvent(event) {
            const image = event.target;
            if (!image?.classList?.contains('avatar-image')) return;
            if (event.type === 'load') this.avatarLoaded(image);
            else this.avatarFailed(image);
        },

        formatMediaDuration(durationMillis) {
            return formatMediaDuration(durationMillis);
        },

        renderMediaPlaybackIndicator(item) {
            if (item.isGif) {
                return String(html`<button type="button" class="media-gif-indicator" data-gif-toggle aria-label="Pause GIF" title="Pause GIF" onclick="window.tweetNookToggleGif(event, this)">
                            <span class="media-gif-action" aria-hidden="true">
                                <svg class="media-gif-pause-icon" viewBox="0 0 24 24"><path d="M6 4h4v16H6zm8 0h4v16h-4z"></path></svg>
                                <svg class="media-gif-play-icon" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"></path></svg>
                            </span>
                            <span class="media-gif-label">GIF</span>
                        </button>`);
            }

            const duration = this.formatMediaDuration(item.m.duration_millis);
            const visibility = duration
                ? html`aria-label="Remaining video duration ${duration}"`
                : html`hidden aria-hidden="true"`;
            return String(html`<span class="media-video-duration" data-video-duration ${visibility}>${duration}</span>`);
        },

        // Like X, a quote is compact when both it and the tweet quoting it have media.
        // Whether lists show the quote compactly (thumbnail beside its text). On phones
        // X's app also does this for a quote with media whose own quote shows as a card.
        isMediaQuotingMedia(tweet) {
            const quote = this.getQuoteTweet(tweet);
            if (this.articleForTweet(quote) || !tweet?.qt_media?.length) return false;
            if (tweet?.media?.length) return true;
            const nested = quote && this.getQuoteTweet(quote);
            return Boolean(nested && !this.isTombstone(nested) && this.isPhoneLayout());
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
            const detailQuote = context === 'quote-detail' || context === 'quote-detail-expanded';
            const quoted = context === 'quote' || detailQuote;
            const width = positiveNumber(media.width);
            const height = positiveNumber(media.height);
            const playback = video && Array.isArray(media.aspect_ratio) && media.aspect_ratio.length === 2
                ? media.aspect_ratio.map(positiveNumber) : [];
            const measured = playback[0] && playback[1] ? playback[0] / playback[1]
                : width && height ? width / height : 0;
            const sourceAspect = Number.isFinite(measured) && measured > 0
                ? measured : gif ? 1 : 16 / 9;
            const portraitPhoto = !video && sourceAspect < 1;
            const photoAspect = Math.min(quoted ? Math.max(sourceAspect, 3 / 4) : sourceAspect, 5);
            return {
                sourceAspect,
                frameAspect: video ? quoted ? 16 / 9 : gif ? Math.max(sourceAspect, 1) : sourceAspect
                    : photoAspect,
                maxWidth: !quoted && sourceAspect < 1 && !gif ? 510 * sourceAspect : null,
                photoCapWidth: portraitPhoto && !detailQuote ? 510 * photoAspect : null,
                portraitPhoto: portraitPhoto && !detailQuote,
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
                if (still) return html`<img src="${still}" loading="lazy" decoding="async" alt="">`;
                if (src) {
                    // Without a poster, a video shows its first frame once metadata loads.
                    const frame = src.includes('#') ? src : `${src}#t=0.1`;
                    return html`<video src="${frame}" preload="metadata" muted playsinline></video>`;
                }
                return html`<svg fill="currentColor" viewBox="0 0 24 24" role="img" aria-label="Media not downloaded"><path d="M15 7a2 2 0 1 1 0 4 2 2 0 0 1 0-4" /><path d="M14.1 2.5q1.65-.02 2.7.06c.74.06 1.38.18 1.97.48a5 5 0 0 1 2.19 2.19c.3.6.42 1.23.48 1.96q.08 1.06.06 2.71v4.2q.02 1.65-.06 2.7a5 5 0 0 1-.48 1.97 5 5 0 0 1-2.19 2.19c-.6.3-1.23.42-1.96.48q-1.06.08-2.71.06H9.9q-1.65.02-2.7-.06a5 5 0 0 1-1.97-.48 5 5 0 0 1-2.19-2.19c-.3-.6-.42-1.23-.48-1.96q-.07-1.06-.06-2.71V9.9q-.02-1.65.06-2.7c.06-.74.18-1.38.48-1.97a5 5 0 0 1 2.19-2.19c.6-.3 1.23-.42 1.96-.48q1.06-.07 2.71-.06zM4.5 15.44q.01.7.05 1.2c.05.61.14.96.28 1.22a3 3 0 0 0 1.3 1.31c.27.14.62.23 1.22.28.62.05 1.41.05 2.55.05h5.18l-7.12-7.13zM9.9 4.5c-1.14 0-1.93 0-2.55.05-.6.05-.95.14-1.21.28a3 3 0 0 0-1.31 1.3 3 3 0 0 0-.28 1.22c-.05.62-.05 1.41-.05 2.55v2.87l3.54-3.14 9.63 9.63.2-.09a3 3 0 0 0 1.3-1.3c.14-.27.23-.62.28-1.22.05-.62.05-1.41.05-2.55V9.9c0-1.14 0-1.93-.05-2.55a3 3 0 0 0-.28-1.21 3 3 0 0 0-1.3-1.31 3 3 0 0 0-1.22-.28c-.62-.05-1.41-.05-2.55-.05z" /></svg>`;
            });
            if (items.length > 1) {
                const cells = stills.map(still => html`<div class="quote-thumbnail-cell">${still}</div>`);
                return String(html`<div class="quote-thumbnail quote-thumbnail-grid">${cells}</div>`);
            }
            const m = items[0];
            const label = m.type === 'animated_gif' ? 'GIF' : (m.type === 'video' ? this.formatMediaDuration(m.duration_millis) : '');
            const badge = label ? html`<span class="quote-thumbnail-badge${m.type === 'video' ? ' quote-thumbnail-duration' : ''}">${label}</span>` : '';
            return String(html`<div class="quote-thumbnail">${stills[0]}${badge}</div>`);
        },

        // indicator: the post has no text, so the X app shows a page indicator above its strip.
        renderMediaGrid(mediaList, context = 'feed', tweet = null, { indicator = false } = {}) {
            if (!mediaList || mediaList.length === 0) return '';

            const getSrc = m => this.mediaSource(m);
            const getPoster = m => this.mediaPoster(m);
            const posterImage = poster => html`<img ${poster ? html`src="${poster}"` : html`hidden`} data-video-poster loading="lazy" decoding="async" alt="" aria-hidden="true">`;

            const allMedia = mediaList.map(m => {
                const type = (m.type === 'video' || m.type === 'animated_gif') ? 'video' : 'photo';
                const isGif = m.type === 'animated_gif'; 
                const duration = m.duration_millis;
                const isShort = duration && duration <= 60000;
                return { m, src: getSrc(m), poster: getPoster(m), type, isGif, isShort };
            });
            
            if (allMedia.length === 0) return '';
            
            const downloadedMedia = allMedia.filter(x => x.src);
            const mediaAttr = html`data-lightbox-media="${JSON.stringify(downloadedMedia)}"`;

            const placeholderHtml = html`
                <div class="absolute inset-0 w-full h-full flex flex-col items-center justify-center bg-[var(--bg-secondary)] text-[var(--text-secondary)]">
                    <svg class="w-8 h-8 mb-2 opacity-50" fill="currentColor" viewBox="0 0 24 24"><path d="M15 7a2 2 0 1 1 0 4 2 2 0 0 1 0-4" /><path d="M14.1 2.5q1.65-.02 2.7.06c.74.06 1.38.18 1.97.48a5 5 0 0 1 2.19 2.19c.3.6.42 1.23.48 1.96q.08 1.06.06 2.71v4.2q.02 1.65-.06 2.7a5 5 0 0 1-.48 1.97 5 5 0 0 1-2.19 2.19c-.6.3-1.23.42-1.96.48q-1.06.08-2.71.06H9.9q-1.65.02-2.7-.06a5 5 0 0 1-1.97-.48 5 5 0 0 1-2.19-2.19c-.3-.6-.42-1.23-.48-1.96q-.07-1.06-.06-2.71V9.9q-.02-1.65.06-2.7c.06-.74.18-1.38.48-1.97a5 5 0 0 1 2.19-2.19c.6-.3 1.23-.42 1.96-.48q1.06-.07 2.71-.06zM4.5 15.44q.01.7.05 1.2c.05.61.14.96.28 1.22a3 3 0 0 0 1.3 1.31c.27.14.62.23 1.22.28.62.05 1.41.05 2.55.05h5.18l-7.12-7.13zM9.9 4.5c-1.14 0-1.93 0-2.55.05-.6.05-.95.14-1.21.28a3 3 0 0 0-1.31 1.3 3 3 0 0 0-.28 1.22c-.05.62-.05 1.41-.05 2.55v2.87l3.54-3.14 9.63 9.63.2-.09a3 3 0 0 0 1.3-1.3c.14-.27.23-.62.28-1.22.05-.62.05-1.41.05-2.55V9.9c0-1.14 0-1.93-.05-2.55a3 3 0 0 0-.28-1.21 3 3 0 0 0-1.3-1.31 3 3 0 0 0-1.22-.28c-.62-.05-1.41-.05-2.55-.05z" /></svg>
                    <span class="text-xs font-medium">Media not downloaded</span>
                </div>
            `;

            if (allMedia.length === 1) {
                const item = allMedia[0];
                const { src, poster, type, isGif, isShort } = item;
                const layout = this.mediaFrameLayout(item.m, context);
                const sizing = `--media-frame-aspect: ${layout.frameAspect}; --media-source-aspect: ${layout.sourceAspect};`
                    + (layout.maxWidth === null ? '' : ` --media-max-width: ${layout.maxWidth}px;`)
                    + (layout.photoCapWidth === null ? '' : ` --media-photo-cap-width: ${layout.photoCapWidth}px;`);
                const frameClass = `media-frame media-frame-${context}${isGif ? ' media-frame-gif' : ''}`
                    + (layout.portraitPhoto ? ' media-frame-portrait-photo' : '');
                // The query container measures the available column, independently of
                // the narrower bordered portrait frame. Playback stays a direct child
                // of the frame so its existing controls and badge handlers keep working.
                const wrap = frame => String(html`<div class="media-single mt-3" style="${sizing}">${frame}</div>`);

                if (!src) {
                    return wrap(html`<div class="relative max-w-full rounded-2xl border border-[var(--border-color)] overflow-hidden block ${frameClass}" @click.stop>
                                <div class="media-frame-placeholder relative">${placeholderHtml}</div>
                            </div>`);
                } else if (type === 'photo') {
                    return wrap(html`<div class="relative max-w-full rounded-2xl border border-[var(--border-color)] overflow-hidden block media-photo ${frameClass}" ${mediaAttr} @click.stop>
                                <img src="${src}" loading="lazy" decoding="async" onclick="window.tweetNookOpenLightbox(this, 0)" class="media-frame-image object-cover cursor-pointer block">
                            </div>`);
                } else {
                    const loopAttr = (isGif || isShort) ? html`loop` : '';
                    const autoplayAttr = isGif ? html`autoplay muted playsinline` : html`playsinline`;
                    const controlsAttr = isGif ? '' : html`data-video-custom data-video-aspect="${layout.sourceAspect}"`;
                    const gifEvents = isGif
                        ? html`data-animated-gif onplay="window.tweetNookSyncGifIndicator(this)" onpause="window.tweetNookSyncGifIndicator(this)" onloadeddata="window.tweetNookSyncGifIndicator(this)"`
                        : '';
                    const indicator = rawHTML(this.renderMediaPlaybackIndicator(item));

                    const videoEvents = isGif ? '' : rawHTML(VIDEO_PLAYER_EVENTS);

                    return wrap(html`<div class="relative max-w-full rounded-2xl border border-[var(--border-color)] overflow-hidden block ${isGif ? '' : 'media-video-player'} ${frameClass}" ${mediaAttr} @click.stop>
                                <video data-media-index="0" src="${src}" poster="${poster || ''}" preload="${poster ? 'none' : 'metadata'}" ${autoplayAttr} ${loopAttr} ${controlsAttr} ${gifEvents} ${videoEvents} ${isGif ? html`onclick="window.tweetNookOpenGif(event, this, 0)"` : ''} class="${isGif ? 'media-frame-gif-video' : 'media-frame-video'} outline-none block"></video>
                                ${posterImage(poster)}
                                ${indicator}
                                ${isGif ? '' : rawHTML(renderVideoControls())}
                            </div>`);
                }
            }
            
            const carousel = this.useCarouselForTweet(tweet);
            const gallery = carousel ? this.mediaCarouselLayout(allMedia.map(item => item.m), context) : null;
            const gridClass = allMedia.length === 2 ? 'grid-cols-2' : 'grid-cols-2 grid-rows-2';
            const arrow = (direction, label) => html`<button type="button" class="media-carousel-arrow media-carousel-${direction}" aria-label="${label}" disabled onclick="event.stopPropagation(); window.tweetNookScrollGallery(this, ${direction === 'previous' ? -1 : 1})"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="${direction === 'previous' ? 'M7.414 13l5.043 5.04-1.414 1.42L3.586 12l7.457-7.46 1.414 1.42L7.414 11H21v2H7.414z' : 'M12.957 4.54L20.414 12l-7.457 7.46-1.414-1.42L16.586 13H3v-2h13.586l-5.043-5.04 1.414-1.42z'}"/></svg></button>`;
            const parts = [carousel
                ? html`<div class="media-carousel media-carousel-${context} mt-3" style="--gallery-height: ${gallery.height}; --gallery-max-aspect: ${gallery.maxAspect}${gallery.appHeight ? `; --gallery-height-app: ${gallery.appHeight}` : ''}" ${mediaAttr} data-action="stop">${indicator && context === 'feed' ? html`<div class="media-carousel-dots" aria-hidden="true">${allMedia.map((_, idx) => html`<span style="--dot-active: ${idx ? 0 : 1}"></span>`)}</div>` : ''}<div class="media-carousel-nav">${arrow('previous', 'Previous')}${arrow('next', 'Next')}<div class="media-carousel-track" role="group" aria-label="Tweet media" tabindex="0" onscroll="window.tweetNookGalleryScroll(this)" onkeydown="window.tweetNookGalleryKey(event, this)">`
                : html`<div class="mt-3 grid gap-[2px] rounded-2xl overflow-hidden border border-[var(--border-color)] aspect-[16/9] ${gridClass}" ${mediaAttr} @click.stop>`];
            
            let downloadedIdx = 0;
            allMedia.forEach((item, idx) => {
                const { src, poster, type, isGif, isShort } = item;
                let itemClass = carousel ? 'media-carousel-tile' : (allMedia.length === 3 && idx === 0) ? 'row-span-2 col-span-1' : (allMedia.length === 3 ? 'col-span-1' : '');
                if (carousel) parts.push(html`<div class="media-carousel-item" role="group" aria-label="Media ${idx + 1} of ${allMedia.length}" style="--gallery-item-aspect: ${gallery.aspects[idx]}">`);
                
                if (!src) {
                    parts.push(html`<div class="relative w-full h-full bg-[var(--border-color)] ${itemClass}">
                                ${placeholderHtml}
                            </div>`);
                } else {
                    const currentDlIdx = downloadedIdx++;
                    if (type === 'photo') {
                        parts.push(html`<div class="relative w-full h-full bg-[var(--border-color)] ${itemClass} media-photo">
                                    <img src="${src}" loading="lazy" decoding="async" ${carousel ? html`alt="Image ${idx + 1}" tabindex="0" role="button" onkeydown="if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); event.stopPropagation(); window.tweetNookOpenLightbox(this, ${currentDlIdx}); }"` : ''} onclick="window.tweetNookOpenLightbox(this, ${currentDlIdx})" class="absolute inset-0 w-full h-full object-cover cursor-pointer">
                                </div>`);
                    } else {
                        const loopAttr = (isGif || isShort) ? html`loop` : '';
                        const autoplayAttr = isGif ? html`autoplay muted playsinline` : html`playsinline`;
                        const controlsAttr = isGif ? '' : html`data-video-custom data-video-aspect="${this.mediaFrameLayout(item.m, context).sourceAspect}"`;
                        const gifEvents = isGif
                            ? html`data-animated-gif onplay="window.tweetNookSyncGifIndicator(this)" onpause="window.tweetNookSyncGifIndicator(this)" onloadeddata="window.tweetNookSyncGifIndicator(this)" onclick="window.tweetNookOpenGif(event, this, ${currentDlIdx})"`
                            : '';
                        const videoEvents = isGif ? '' : rawHTML(VIDEO_PLAYER_EVENTS);
                        const indicator = rawHTML(this.renderMediaPlaybackIndicator(item));
                        parts.push(html`<div class="relative w-full h-full bg-[var(--border-color)] ${itemClass} ${isGif ? '' : 'media-video-player'}">
                                    <video data-media-index="${currentDlIdx}" src="${src}" poster="${poster || ''}" preload="${poster ? 'none' : 'metadata'}" ${autoplayAttr} ${loopAttr} ${controlsAttr} ${gifEvents} ${videoEvents} class="absolute inset-0 w-full h-full media-grid-video outline-none"></video>
                                    ${posterImage(poster)}
                                    ${indicator}
                                    ${isGif ? '' : rawHTML(renderVideoControls())}
                                </div>`);
                    }
                }
                if (carousel) parts.push(html`</div>`);
            });
            
            parts.push(carousel ? html`</div></div></div>` : html`</div>`);
            return String(html`${parts}`);
        },

        mediaCarouselLayout(media, context = 'feed') {
            const sourceAspects = media.map(item => this.mediaFrameLayout(item).sourceAspect);
            const aspects = sourceAspects.map(aspect => Math.min(9 / 4, aspect));
            const quoted = context.startsWith('quote');
            const min = Math.min(...aspects), max = Math.max(...aspects);
            // Native X packs portrait/square pairs at their source proportions.
            // Other strips use a shared height and crop wide photos to 80% of
            // the content column. Quotes reduce the common height by 15%.
            const packed = media.length === 2 && (min < 1 || max === 1);
            if (packed) {
                const sum = aspects.reduce((a, b) => a + b, 0);
                // A capped panoramic pair leaves a 16px trailing inset in X.
                const inset = Math.max(...sourceAspects) > 9 / 4 ? 20 : 4;
                const height = `calc((100cqw - ${inset}px) / ${sum})`;
                return {aspects, height: quoted ? `min(66cqw, ${height})` : height, maxAspect: max,
                    appHeight: quoted ? null : height};
            }
            const factor = max <= 1 && min < 1 ? 0.8 : 0.68;
            const percent = Number((factor * (quoted ? 0.85 : 1) * 100).toFixed(6));
            // Keep roughly 1.67 of the widest tiles visible when that needs
            // more height than the standard strip (notably quoted squares).
            const height = `max(${percent}cqw, calc((100cqw - 4px) / ${1.67 * max}))`;
            // X's web and apps size inline strips from their first item instead: wide
            // enough to show ~59.5% of the column, between 68% and 100% of it tall.
            // The shared height above remains for quoted strips.
            return {aspects, height: quoted ? `min(66cqw, ${height})` : height,
                maxAspect: 0.8 / (factor * (quoted ? 0.85 : 1)),
                appHeight: quoted ? null : `clamp(68cqw, calc(59.5cqw / ${aspects[0]}), 100cqw)`};
        },
    };
}
