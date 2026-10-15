/* Archived X Articles. Render only known rich-text structures, never saved HTML. */
(function (global) {
    'use strict';
    const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[char]);
    const httpURL = value => {
        try { const url = new URL(String(value)); return /^https?:$/.test(url.protocol) ? url.href : ''; }
        catch { return ''; }
    };
    const X_MARK = 'M21.742 21.75l-7.563-11.179 7.056-8.321h-2.456l-5.691 6.714-4.54-6.714H2.359l7.29 10.776L2.25 21.75h2.456l6.035-7.118 4.818 7.118h6.191-.008zM7.739 3.818L18.81 20.182h-2.447L5.29 3.818h2.447z';
    const icon = path => `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${path}"></path></svg>`;
    const badge = `<span class="article-badge">${icon(X_MARK)}Article</span>`;

    function data(tweet) {
        for (let node = tweet, depth = 0; node && depth < 4; node = node.raw_json, depth++) {
            if (node.availability?.placeholder) return null;
            const article = node.article;
            if (article && typeof article === 'object' && !article.article_results) return article;
        }
        return null;
    }

    function mediaSource(app, media) {
        const value = app.mediaSource(media);
        if (typeof value !== 'string') return '';
        // Preserve the archive's offline-only media policy and reject active URL schemes.
        return (/^\/(?!\/)/.test(value) || /^demo\/media\//.test(value) || httpURL(value)) ? value : '';
    }

    function image(app, media, cover = false, interactive = true) {
        if (!media) return '';
        const src = mediaSource(app, media);
        if (!src) return `<div class="article-image-missing" role="img" aria-label="${cover ? 'Article cover' : 'Article image'} not downloaded">${cover ? 'Cover' : 'Image'} not downloaded</div>`;
        const alt = escape(media.alt_text || (cover ? 'Article cover' : 'Article image'));
        const pixels = `<img src="${escape(src)}" alt="${alt}" loading="lazy" decoding="async">`;
        if (!interactive) return pixels;
        const item = {m: media, src, poster: null, type: 'photo', isGif: false, isShort: false};
        return `<button type="button" class="article-image-button" aria-label="Open ${alt}" data-lightbox-media="${escape(JSON.stringify([item]))}" onclick="event.stopPropagation(); window.tweetNookOpenLightbox(this.querySelector('img'), 0)">${pixels}</button>`;
    }

    function entityMap(state) {
        const result = new Map();
        if (Array.isArray(state?.entityMap)) {
            for (const item of state.entityMap) if (item && item.value) result.set(String(item.key), item.value);
        } else if (state?.entityMap && typeof state.entityMap === 'object') {
            for (const [key, value] of Object.entries(state.entityMap)) result.set(key, value);
        }
        return result;
    }

    function inline(block, entities) {
        const text = typeof block.text === 'string' ? block.text : '';
        const styles = Array.isArray(block.inlineStyleRanges) ? block.inlineStyleRanges : [];
        const links = Array.isArray(block.entityRanges) ? block.entityRanges : [];
        const valid = range => Number.isInteger(range?.offset) && Number.isInteger(range?.length)
            && range.offset >= 0 && range.length > 0 && range.offset < text.length;
        const ranges = [...styles, ...links].filter(valid);
        const points = [...new Set([0, text.length, ...ranges.flatMap(r => [r.offset, Math.min(text.length, r.offset + r.length)])])].sort((a, b) => a - b);
        const tags = {bold: 'strong', italic: 'em', underline: 'u', strikethrough: 's', code: 'code'};
        let html = '';
        for (let i = 1; i < points.length; i++) {
            const start = points[i - 1], end = points[i];
            let part = escape(text.slice(start, end));
            for (const range of styles.filter(valid)) {
                const tag = tags[String(range.style).toLowerCase()];
                if (tag && range.offset <= start && range.offset + range.length >= end) part = `<${tag}>${part}</${tag}>`;
            }
            const linkRange = links.filter(valid).find(r => r.offset <= start && r.offset + r.length >= end);
            const entity = linkRange && entities.get(String(linkRange.key));
            const href = entity?.type?.toUpperCase() === 'LINK' ? httpURL(entity.data?.url) : '';
            if (href) part = `<a href="${escape(href)}" target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation()">${part}</a>`;
            html += part;
        }
        return html || '<br>';
    }

    function body(app, article, interactive = true) {
        const state = article.content_state;
        const media = Array.isArray(article.media) ? article.media : [];
        const entities = entityMap(state);
        const used = new Set();
        const blocks = Array.isArray(state?.blocks) ? state.blocks : [];
        const meaningful = blocks.some(b => typeof b?.text === 'string' && b.text.trim() && b.type !== 'atomic');
        let html = '', list = '';
        const closeList = () => { if (list) { html += `</${list}>`; list = ''; } };
        const figure = item => `<figure class="article-figure">${image(app, item, false, interactive)}</figure>`;
        if (meaningful) {
            for (const block of blocks.slice(0, 10000)) {
                if (!block || typeof block !== 'object') continue;
                const listType = block.type === 'unordered-list-item' ? 'ul' : block.type === 'ordered-list-item' ? 'ol' : '';
                if (listType !== list) { closeList(); if (listType) { list = listType; html += `<${list}>`; } }
                if (block.type === 'atomic') {
                    closeList();
                    for (const range of Array.isArray(block.entityRanges) ? block.entityRanges : []) {
                        const entity = entities.get(String(range.key));
                        const details = entity?.data || {};
                        if (entity?.type?.toUpperCase() === 'MEDIA') {
                            for (const entry of Array.isArray(details.mediaItems) ? details.mediaItems : []) {
                                const item = media.find(m => m.source !== 'article_cover'
                                    && (String(m.media_id || '') === String(entry.mediaId)
                                    || String(m.media_key || '').endsWith('_' + entry.mediaId)));
                                if (item) { used.add(item); html += figure(item); }
                                else html += '<p class="article-notice">Embedded media was not captured.</p>';
                            }
                        } else {
                            const id = String(details.tweetId || details.tweet_id || details.id || '');
                            const href = httpURL(details.url) || (/^\d+$/.test(id) ? `https://x.com/i/status/${id}` : '');
                            html += href ? `<a class="article-embed-link" href="${escape(href)}" target="_blank" rel="noopener noreferrer">View embedded post or content on X</a>`
                                : '<p class="article-notice">Embedded content was not captured.</p>';
                        }
                    }
                    continue;
                }
                const tag = listType ? 'li' : ({'header-one': 'h2', 'header-two': 'h3', 'header-three': 'h4', blockquote: 'blockquote', 'code-block': 'pre'})[block.type] || 'p';
                html += `<${tag}>${inline(block, entities)}</${tag}>`;
            }
            closeList();
        } else {
            const content = article.content_text || article.summary_text || '';
            html = String(content).split(/\n\s*\n/).map(p => `<p>${escape(p)}</p>`).join('');
        }
        for (const item of media) if (item.source !== 'article_cover' && !used.has(item)) html += figure(item);
        if (!article.content_text && !meaningful) html += '<p class="article-notice">Only a preview is saved. The full article body has not been archived.</p>';
        return html;
    }

    function render(app, tweet, full = false, interactive = true, focusMode = false) {
        const article = data(tweet);
        if (!article) return '';
        const media = Array.isArray(article.media) ? article.media : [];
        const cover = media.find(m => m.source === 'article_cover');
        const title = article.title || 'Untitled article';
        if (!full) {
            const id = tweet?.tweet_id || tweet?.raw_json?.tweet_id || tweet?.raw_json?.rest_id || '';
            const coverHTML = cover ? `<div class="article-preview-cover">${image(app, cover, true, false)}${badge}</div>` : `<div class="article-preview-label">${badge}</div>`;
            return `<button type="button" class="article-preview" data-article-id="${escape(id)}" onclick="window.tweetNookOpenArticle(event, this)" aria-label="Read article: ${escape(title)}">${coverHTML}<div class="article-preview-copy"><div class="article-preview-title">${escape(title)}</div><div class="article-preview-excerpt">${escape(article.summary_text || article.content_text || '')}</div></div></button>`;
        }
        const author = tweet.author || {};
        const byline = focusMode ? `<div class="article-focus-author"><strong>${escape(author.display_name || 'Unknown')}</strong><span>@${escape(author.username || 'unknown')} · ${escape(app.formatDate?.(tweet.created_at) || '')}</span></div>` : '';
        const actions = typeof app.renderActionBar === 'function' ? app.renderActionBar(tweet) : '';
        return `<section class="article-detail">${cover ? `<div class="article-cover">${image(app, cover, true, interactive)}</div>` : ''}<h2 class="article-title">${escape(title)}</h2>${byline}${actions}<div class="article-body">${body(app, article, interactive)}</div></section>`;
    }

    function focus(app, tweet) {
        if (!data(tweet)) return;
        const dialog = document.createElement('dialog');
        dialog.className = 'article-focus';
        dialog.setAttribute('aria-label', 'Article focus mode');
        dialog.innerHTML = `<div class="article-focus-toolbar"><button type="button" autofocus aria-label="Exit article focus mode">${icon('M16.697 5.89l1.414 1.42L13.414 12l4.697 4.69-1.414 1.42L12 13.41l-4.697 4.7-1.414-1.42L10.586 12 5.89 7.31l1.414-1.42L12 10.59l4.697-4.7z')}</button></div><div class="article-focus-content">${render(app, tweet, true, false, true)}</div>`;
        const savedOverflow = document.body.style.overflow;
        dialog.addEventListener('close', () => { document.body.style.overflow = savedOverflow; dialog.remove(); }, {once: true});
        dialog.addEventListener('keydown', event => event.stopPropagation());
        dialog.querySelector('button').addEventListener('click', () => dialog.close());
        document.body.appendChild(dialog);
        dialog.showModal();
        document.body.style.overflow = 'hidden';
    }

    global.tweetNookOpenArticle = (event, element) => {
        event.preventDefault(); event.stopPropagation();
        global.dispatchEvent(new CustomEvent('open-article', {detail: {tweetId: element.dataset.articleId}}));
    };
    global.TweetNookArticles = {data, render, body, focus, inline, entityMap};
})(window);
