/**
 * Post text: entity links, mentions and hashtags, search highlighting, Article
 * previews, and the escaping helpers every renderer uses.
 */

function tweetTextMethods() {
    return {
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
            let markup = '', offset = 0;
            for (const range of ranges) {
                const start = Math.max(0, range[0] - baseOffset);
                const end = Math.min(text.length, range[1] - baseOffset);
                if (start >= end) continue;
                markup += escape(text.slice(offset, start)) + '<strong>' + escape(text.slice(start, end)) + '</strong>';
                offset = end;
            }
            return markup + escape(text.slice(offset));
        },

        articleForTweet(tweet) {
            return window.TweetNookArticles?.data(tweet) || null;
        },

        renderArticle(tweet, full = false) {
            return window.TweetNookArticles?.render(this, tweet, full) || '';
        },

        openArticleFocus(tweet) {
            window.TweetNookArticles?.focus(this, tweet);
        },

        // appClamp: list cards on phones clamp long posts to 9 lines, as X's apps do,
        // instead of cutting at 280 characters.
        formatText(tweet, forceFull = false, highlightSearch = false, appClamp = false) {
            if (!tweet || this.articleForTweet(tweet)) return '';
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
            const clampLines = appClamp && !forceFull && text.length > 280 && tweetId
                && !this.expandedTweets[tweetId] && this.isPhoneLayout();

            if (!clampLines && !forceFull && text.length > 280 && tweetId && !this.expandedTweets[tweetId]) {
                let truncated = text.substring(0, 280);
                let lastSpace = truncated.lastIndexOf(' ');
                if (lastSpace > 200) {
                    truncated = truncated.substring(0, lastSpace);
                }
                text = truncated;
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
                            const displayUrl = rawHTML(this.highlightSearchText(u.display_url || u.expanded_url || '', highlightSearch));
                            urlMap[u.url] = html`<a href="${httpURL(u.expanded_url)}" target="_blank" rel="noopener noreferrer" class="text-[var(--accent-color)] hover:underline" @click.stop>${displayUrl}</a>`;
                        }
                    });
                }
            }
            
            // Render source text once. Query matches never touch generated markup,
            // link destinations, data attributes, or escaped entity spellings.
            const urlTokens = Object.keys(urlMap).map(value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
            const pattern = new RegExp([...urlTokens, '@\\w+', '#\\w+'].join('|'), 'g');
            const ranges = highlightSearch ? this.searchMatchRanges(text) : [];
            // highlightSearchText escapes the text it returns.
            const highlighted = (value, at) => rawHTML(this.highlightSearchText(value, highlightSearch, ranges, at));
            const parts = [];
            let offset = 0;
            for (const match of text.matchAll(pattern)) {
                parts.push(highlighted(text.slice(offset, match.index), offset));
                const token = match[0];
                if (Object.hasOwn(urlMap, token)) parts.push(urlMap[token]);
                else {
                    const mention = token.startsWith('@');
                    parts.push(html`<span class="${mention ? 'mention' : 'hashtag'} text-[var(--accent-color)] hover:underline cursor-pointer" data-${mention ? 'user' : 'tag'}="${token.slice(1)}">${highlighted(token, match.index)}</span>`);
                }
                offset = match.index + token.length;
            }
            text = String(html`${parts}${highlighted(text.slice(offset), offset)}`);
            
            if (isTruncated) {
                text += String(html`<span class="text-[var(--accent-color)] hover:underline cursor-pointer block expand-tweet" data-id="${tweetId}">Show more</span>`);
            }
            if (clampLines) {
                // Show more covers the end of the ninth line, once measuring finds overflow.
                return String(html`<span class="tweet-text-clamp">${rawHTML(text.trim())}</span>`
                    + html`<span class="tweet-text-more expand-tweet" data-id="${tweetId}">Show more</span>`);
            }

            return text.trim();
        },

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

        // Card links point at t.co; the tweet's URL entities carry the destination.
        expandShortURL(raw, url) {
            if (!url) return url;
            const note = raw?.note_tweet?.note_tweet_results?.result?.entity_set?.urls;
            const entities = [...(raw?.legacy?.entities?.urls || []), ...(Array.isArray(note) ? note : [])];
            return entities.find(entity => entity?.url === url && entity.expanded_url)?.expanded_url || url;
        },

        escapeHTML(str) {
            return escapeHTML(str);
        },

        safeURL(value) {
            return escapeHTML(httpURL(value));
        },
    };
}
