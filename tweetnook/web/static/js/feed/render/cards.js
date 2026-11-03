/**
 * Feed and quote cards: the card shell, quoted posts, retweets, reply recipients,
 * placeholders for posts that weren't archived, and the missing-from-X badge.
 */

// warning-fill from icons.md: marks an archived tweet that is no longer on X.
// checkmark-circle from icons.md: the History dialog's "On X" status.
const AVAILABLE_ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="m16.28 9.62-5.41 6.84-3.54-3.22 1.34-1.48 1.96 1.78 4.09-5.16z"/><path d="M12 2a10 10 0 1 1 0 20 10 10 0 0 1 0-20m0 2a8 8 0 1 0 0 16 8 8 0 0 0 0-16"/></svg>';
const MISSING_ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M9.4 3.5a3 3 0 0 1 5.2 0L22.4 17a3 3 0 0 1-2.6 4.5H4.2A3 3 0 0 1 1.6 17zM12 15.2a1.2 1.2 0 1 0 0 2.4 1.2 1.2 0 0 0 0-2.4m0-6.95c-.8 0-1.4.7-1.3 1.49l.55 4.01h1.5l.55-4.01A1.3 1.3 0 0 0 12 8.25"/></svg>';

function feedCardMethods() {
    return {
        // Render the quoted post consistently in feed cards and thread ancestors.
        // The owner tweet supplies the normalized quote, media, and compact-layout
        // context; render helpers only read these values.
        renderQuoteCard(tweet, { highlightSearch = true } = {}) {
            // Attributes are literals written below; the markup comes from the renderers.
            const part = (markup, attributes = '') => (markup ? html`<div${rawHTML(attributes)}>${rawHTML(markup)}</div>` : '');
            const quote = this.getQuoteTweet(tweet);
            if (!quote) return '';
            if (this.isTombstone(quote)) return String(part(this.renderQuotePlaceholder(quote), ' data-action="stop"'));

            const quoteId = this.getTweetId(quote);
            const quoteReplyTo = this.getReplyRecipients({ raw_json: quote });
            // A compact quote's text is clamped by CSS, so it is rendered without Show more.
            const compact = this.isMediaQuotingMedia(tweet);
            return String(html`${[
                html`<div class="quote-card mt-3 border border-[var(--border-color)] rounded-xl p-3 hover-bg transition cursor-pointer${compact ? ' quote-compact' : ''}"${quoteId ? html` data-thread-id="${quoteId}"` : ''} data-action="quote">`,
                rawHTML(this.renderQuoteByline(quote)),
                quoteReplyTo.length ? rawHTML(this.renderReplyRecipients({ raw_json: quote }, 'quote-reply ')) : '',
                html`<div class="quote-text text-[15px] whitespace-pre-wrap leading-normal break-words" data-action="text">${rawHTML(this.formatText(quote, compact, highlightSearch))}</div>`,
                compact
                    ? part(this.renderQuoteThumbnail(tweet.qt_media), ' class="quote-media"')
                    : part(this.renderMediaGrid(tweet.qt_media, 'quote', quote), ' class="quote-media" data-action="stop"'),
                part(this.renderCard({ raw_json: quote }), ' data-action="stop"'),
                rawHTML(this.renderNestedQuote(quote, highlightSearch)),
                part(this.renderCommunityNote(quote, true, false, Boolean(tweet.qt_media?.length)), ' data-action="stop"'),
                html`</div>`,
            ]}`);
        },

        // One feed card, using the helpers the thread views render with. html`` escapes
        // archived values, as x-text did for the template; the helpers' markup is rawHTML.
        renderFeedCard(tweetObj) {
            // Attributes are literals written below; the markup comes from the renderers.
            const part = (markup, attributes = '') => (markup ? html`<div${rawHTML(attributes)}>${rawHTML(markup)}</div>` : '');
            const replyLine = (source, className = '') => rawHTML(this.renderReplyRecipients(source, className));
            const retweet = this.getRetweet(tweetObj);
            const tweet = retweet ? this.formatRetweetToTweet(tweetObj, retweet) : tweetObj;
            const author = tweet.author || {};
            const replyTo = this.getReplyRecipients(tweet);
            const menuOpen = this.tweetMenuOpen === `list:${tweetObj.tweet_id}`;

            const header = retweet
                ? html`${[
                    html`<div class="tweet-repost-context text-[13px] text-[var(--text-secondary)] font-bold mb-1 flex items-center space-x-2 ml-8">`,
                    html`<svg viewBox="0 0 24 24" class="w-4 h-4 fill-current" fill="currentColor"><path d="M13.1 4q1.65-.01 2.71.06c.73.06 1.37.18 1.96.48a5 5 0 0 1 2.19 2.19 5 5 0 0 1 .48 1.96q.08 1.06.06 2.71v5.05l2.07-1.93 1.36 1.46-4.43 4.14-4.43-4.14 1.36-1.46 2.07 1.93V11.4c0-1.14 0-1.93-.05-2.55a3 3 0 0 0-.27-1.21 3 3 0 0 0-1.32-1.31 3 3 0 0 0-1.21-.28C15.03 6 14.24 6 13.1 6H11V4zM8.93 8.02 7.57 9.48 5.5 7.55v5.05c0 1.14 0 1.93.05 2.55.05.6.15.95.28 1.21a3 3 0 0 0 1.31 1.31c.26.14.61.23 1.22.28s1.4.05 2.54.05H13v2h-2.1q-1.64.02-2.7-.06a5 5 0 0 1-1.97-.48 5 5 0 0 1-2.18-2.19 5 5 0 0 1-.49-1.96q-.08-1.06-.06-2.7V7.54L1.43 9.48.07 8.02 4.5 3.88z" /></svg>`,
                    html`<span>${`${tweetObj.author?.display_name || 'Unknown'} Reposted`}</span></div>`,
                ]}`
                : '';
            const avatar = html`${[
                html`<div class="avatar w-10 h-10 rounded-full flex-shrink-0 relative bg-[var(--gray-700)] flex items-center justify-center font-bold text-[var(--white-icon)] uppercase overflow-hidden" data-action="profile">`,
                html`<span class="avatar-initial">${this.getInitial(tweet)}</span>`,
                html`<img src="${this.avatarUrl(author.id)}" class="avatar-image absolute inset-0 w-full h-full object-cover z-10" loading="lazy"></div>`,
            ]}`;
            // The missing marker sits at the right, beside the overflow button.
            const missing = this.missingBadge(tweet);
            const byline = html`${[
                html`<div class="tweet-byline tweet-byline-feed flex items-start text-[15px]${missing ? ' has-missing' : ''}"><div class="flex items-center space-x-1 min-w-0">`,
                html`<span class="font-bold text-[var(--text-primary)] truncate hover:underline tweet-byline-name" data-action="profile">${author.display_name || 'Unknown'}</span>`,
                html`<span class="text-[var(--text-secondary)] tweet-byline-detail tweet-byline-handle">${`@${author.username || 'unknown'}`}</span>`,
                html`<span class="text-[var(--text-secondary)]">·</span>`,
                html`<div class="roll-wrapper text-[var(--text-secondary)] tweet-byline-detail" data-action="stop"><div class="roll-inner">`,
                html`<span class="roll-item hover:underline">${rawHTML(this.dateLabel(tweet.created_at))}</span>`,
                html`<span class="roll-item text-[var(--accent-color)] text-[13px] font-medium">${this.formatSyncDate(tweet)}</span>`,
                html`</div></div>`,
                html`</div>`,
                html`<div class="tweet-menu-trigger">`,
                rawHTML(missing),
                html`<button type="button" class="w-9 h-9 rounded-full flex items-center justify-center text-[var(--text-secondary)] hover:text-[var(--accent-color)] hover:bg-[color-mix(in_srgb,var(--accent-color)_10%,transparent)] select-none touch-manipulation${menuOpen ? ' opacity-0' : ''}" aria-label="More actions" aria-haspopup="menu" aria-expanded="${String(menuOpen)}" title="More actions (hold for Tags)" data-action="menu">`,
                html`<svg viewBox="0 0 24 24" class="w-[18px] h-[18px] fill-current" aria-hidden="true" fill="currentColor"><circle cx="5" cy="12" r="2" /><circle cx="12" cy="12" r="2" /><circle cx="19" cy="12" r="2" /></svg>`,
                html`</button></div></div>`,
            ]}`;

            const quoteBlock = this.renderQuoteCard(tweet);
            const text = this.formatText(tweet, false, true, true);

            return String(html`${[
                html`<div class="px-4 pt-3 pb-3 tweet-border feed-card hover-bg transition duration-200 cursor-pointer" data-tweet-id="${tweetObj.tweet_id}"`,
                html`${tweet.tweet_id ? html` data-thread-id="${tweet.tweet_id}"` : ''} data-action="thread">`,
                header,
                html`<div class="flex space-x-2">`,
                avatar,
                html`<div class="flex-1 min-w-0">`,
                byline,
                replyTo.length ? replyLine(tweet) : '',
                html`<div class="tweet-text text-[15px] whitespace-pre-wrap break-words" data-action="text">${rawHTML(text)}</div>`,
                part(this.renderMediaGrid(tweet.media, 'feed', retweet || tweet, { indicator: !text.replace(/<[^>]*>/g, '').trim() }), ' data-action="media"'),
                part(this.renderCard(tweet), ' data-action="stop"'),
                rawHTML(quoteBlock),
                part(this.renderCommunityNote(tweet.raw_json), ' data-action="stop"'),
                part(this.renderActionBar(tweet)),
                html`</div></div></div>`,
            ]}`);
        },

        // A red warning after the date: the tweet is archived but no longer on X.
        missingBadge(tweet) {
            const availability = tweet?.availability;
            if (!availability || availability.state !== 'unavailable' || availability.placeholder) return '';
            const label = `Missing from X. ${availability.message || 'This tweet is unavailable.'}`;
            return String(html`${[
                html`<button type="button" class="tweet-missing-badge" aria-label="${`${label} Show history`}"`,
                html` title="${label}" data-action="history" data-history-id="${tweet.tweet_id}">`,
                html`${rawHTML(MISSING_ICON)}</button>`,
            ]}`);
        },

        missingIcon() {
            return MISSING_ICON;
        },

        availableIcon() {
            return AVAILABLE_ICON;
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
            return String(html`<div class="${className}tweet-reply-context text-[15px] text-[var(--text-secondary)] mb-1">${rawHTML(this.renderReplyRecipientContent(tweet))}</div>`);
        },

        renderReplyRecipientContent(tweet) {
            const recipients = this.getReplyRecipients(tweet);
            if (!recipients.length) return '';
            const links = recipients.map(username =>
                html`<span class="text-[var(--accent-color)] hover:underline cursor-pointer" data-action="reply-profile" data-username="${username}">${`@${username}`}</span>`
            );
            let names = links[0] || '';
            if (links.length === 2) names = html`${names} and ${links[1]}`;
            else if (links.length > 2) names = html`${links.slice(0, -1).map(link => html`${link} `)}and ${links.at(-1)}`;
            return String(html`Replying to ${names}`);
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
            if (tweet.media?.length || tweet.card_media?.length || this.articleForTweet(tweet)) return false;
            const raw = tweet.raw_json?.raw_json || tweet.raw_json || tweet;
            if (raw?.legacy?.extended_entities?.media?.length || raw?.legacy?.entities?.media?.length) return false;
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
            const message = tweet?.availability?.message
                || tombstoneMessage
                || fallbackMessages[reasonKey]
                || 'This tweet is unavailable.';
            const tweetId = this.getTweetId(tweet) || '';
            const label = compact ? 'Quoted tweet unavailable' : 'Tweet unavailable';
            const rootClasses = [
                'tweet-skeleton',
                compact ? 'tweet-skeleton-compact' : '',
                contentOnly ? 'tweet-skeleton-content' : '',
            ].filter(Boolean).join(' ');
            const meta = contentOnly ? '' : html`
                <div class="tweet-skeleton-meta" aria-hidden="true">
                    <span class="tweet-skeleton-bar tweet-skeleton-name"></span>
                    <span class="tweet-skeleton-bar tweet-skeleton-handle"></span>
                    <span class="tweet-skeleton-bar tweet-skeleton-date"></span>
                </div>`;
            const lineClasses = compact
                ? ['tweet-skeleton-line-wide', 'tweet-skeleton-line-medium']
                : ['tweet-skeleton-line-wide', 'tweet-skeleton-line-medium', 'tweet-skeleton-line-short'];
            const lines = lineClasses.map(className => (
                html`<span class="tweet-skeleton-bar tweet-skeleton-line ${className}"></span>`
            ));
            const body = html`
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
                : html`<span class="tweet-skeleton-avatar" aria-hidden="true"></span>`;
            return String(html`<div class="${rootClasses}" role="status" aria-label="${label}" data-tweet-skeleton="true" data-availability-reason="${reasonKey}" data-tweet-id="${tweetId}"><span class="sr-only">${label}</span>${avatar}<div class="tweet-skeleton-main">${meta}${body}</div></div>`);
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
                    initial: avatarInitial(name),
                };
            }
            const userResult = qt.core?.user_results?.result;
            let name = userResult?.legacy?.name || userResult?.core?.name || qt.user?.name || 'Unknown';
            let screen_name = userResult?.legacy?.screen_name || userResult?.core?.screen_name || qt.user?.screen_name || 'unknown';
            let id = userResult?.rest_id || qt.user?.id_str || 'x';
            return { name, screen_name, id, initial: avatarInitial(name) };
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

        // X shows a quote's avatar at 24px and a nested quote's at 20px.
        renderQuoteByline(quote, avatarSize = 'w-6 h-6') {
            const author = this.getQuoteAuthor(quote);
            return String(html`${[
                html`<div class="quote-byline flex items-center space-x-1 text-[15px] mb-1">`,
                html`<div class="avatar ${avatarSize} rounded-full flex-shrink-0 relative bg-[var(--gray-700)] flex items-center justify-center font-bold text-[var(--white-icon)] uppercase text-xs overflow-hidden">`,
                html`<span class="avatar-initial">${author.initial}</span>`,
                html`<img src="${this.avatarUrl(author.id)}" class="avatar-image absolute inset-0 w-full h-full object-cover z-10" loading="lazy"></div>`,
                html`<span class="font-bold text-[var(--text-primary)] truncate tweet-byline-name">${author.name}</span>`,
                html`<span class="text-[var(--text-secondary)] tweet-byline-detail tweet-byline-handle">${`@${author.screen_name}`}</span>`,
                html`<span class="text-[var(--text-secondary)]">·</span>`,
                html`<span class="text-[var(--text-secondary)] tweet-byline-detail">${rawHTML(this.dateLabel(quote.legacy?.created_at || quote.created_at))}</span>`,
                rawHTML(this.missingBadge(quote)),
                html`</div>`,
            ]}`);
        },

        // A quote's own quote, nested inside it as X shows it: a bar beside the byline
        // and two lines of text, with any media as a small thumbnail beside the text.
        // Unavailable or uncaptured nested quotes are left out.
        renderNestedQuote(quote, highlightSearch = false) {
            const nested = this.getQuoteTweet(quote);
            if (!nested || this.isTombstone(nested)) return '';
            const id = this.getTweetId(nested);
            const media = quote.qt_media || nested.media || [];
            return String(html`${[
                html`<div class="quote-nested${media.length ? ' quote-nested-media' : ''}"${id ? html` data-thread-id="${id}"` : ''} data-action="quote">`,
                rawHTML(this.renderQuoteByline(nested, 'w-5 h-5')),
                rawHTML(this.renderArticle(nested)),
                media.length ? html`<div class="quote-media">${rawHTML(this.renderQuoteThumbnail(media))}</div>` : '',
                html`<div class="quote-text text-[15px] whitespace-pre-wrap leading-normal break-words" data-action="text">${rawHTML(this.formatText(nested, true, highlightSearch))}</div>`,
                html`</div>`,
            ]}`);
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
    };
}
