/**
 * What surrounds a post's content: dates, counts, Community Notes and the action bar.
 */

// toLocaleDateString with options builds a new ICU formatter on every call, which
// is most of the cost of a card's dates. Formatters for fixed options are reused.
const dateFormats = new Map();
function formatDateParts(date, options) {
    const key = JSON.stringify(options);
    let format = dateFormats.get(key);
    if (!format) dateFormats.set(key, format = new Intl.DateTimeFormat('en-US', options));
    return format.format(date);
}
const SHORT_DAY = { month: 'short', day: 'numeric' };
const SHORT_DAY_YEAR = { month: 'short', day: 'numeric', year: 'numeric' };
const NUMERIC_DAY = { month: 'numeric', day: 'numeric', year: '2-digit' };
const CLOCK_TIME = { hour: 'numeric', minute: '2-digit' };

function tweetMetaMethods() {
    return {
        formatSyncDate(tweet) {
            let synced = tweet.synced_at || (tweet.collection ? tweet.collection.synced_at : null);
            if (!synced) return 'Archived';
            let dS = new Date(synced);
            return `Synced ${formatDateParts(dS, SHORT_DAY)}`;
        },

        formatDate(dateStr, includeTime = false) {
            if (!dateStr) return '';
            const d = new Date(dateStr);
            if (includeTime) {
                return formatDateParts(d, CLOCK_TIME) + ' · ' + formatDateParts(d, SHORT_DAY_YEAR);
            }
            const now = new Date();
            const diff = (now - d) / 1000;
            if (diff < 60) return 'Just now';
            if (diff < 3600) return Math.floor(diff/60) + 'm';
            if (diff < 86400) return Math.floor(diff/3600) + 'h';
            return formatDateParts(d, d.getFullYear() !== now.getFullYear() ? SHORT_DAY_YEAR : SHORT_DAY);
        },

        // X's apps: age in s/m/h/d inside a week, then a numeric date (10/9/26).
        formatAppDate(dateStr, includeTime = false) {
            if (!dateStr) return '';
            const d = new Date(dateStr);
            if (Number.isNaN(d.getTime())) return '';
            const numeric = formatDateParts(d, NUMERIC_DAY);
            if (includeTime) return formatDateParts(d, CLOCK_TIME) + ' · ' + numeric;
            const diff = Math.max(0, (new Date() - d) / 1000);
            if (diff < 60) return Math.floor(diff) + 's';
            if (diff < 3600) return Math.floor(diff / 60) + 'm';
            if (diff < 86400) return Math.floor(diff / 3600) + 'h';
            if (diff < 7 * 86400) return Math.floor(diff / 86400) + 'd';
            return numeric;
        },

        // Both date styles, escaped; CSS shows the app one at phone widths.
        dateLabel(dateStr, includeTime = false) {
            const web = this.formatDate(dateStr, includeTime);
            const app = this.formatAppDate(dateStr, includeTime);
            if (!app || app === web) return this.escapeHTML(web);
            return String(html`<span class="date-web">${web}</span><span class="date-app">${app}</span>`);
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

        formatCommunityNoteText(content) {
            let text = content?.text || '';
            if (!text) return '';
            let entities = content?.entities || [];
            
            entities = [...entities].sort((a, b) => a.fromIndex - b.fromIndex);
            
            const parts = [];
            let lastIndex = 0;
            
            for (const ent of entities) {
                if (ent.ref?.urlType === 'ExternalUrl' && ent.ref?.url) {
                    const linkText = text.substring(ent.fromIndex, ent.toIndex);
                    parts.push(html`${text.substring(lastIndex, ent.fromIndex)}<a href="${httpURL(ent.ref.url)}" target="_blank" rel="noopener noreferrer" class="text-[var(--accent-color)] hover:underline" @click.stop>${linkText}</a>`);
                    lastIndex = ent.toIndex;
                }
            }
            return String(html`${parts}${text.substring(lastIndex)}`);
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

            const iconSvg = html`<svg viewBox="0 0 24 24" fill="var(--accent-color)" class="w-[18px] h-[18px]"><path d="M12 12a6 6 0 0 1 6 6v2H6v-2a6 6 0 0 1 6-6m-6.24 1A8 8 0 0 0 4 18v1H0v-1a5 5 0 0 1 5-5zM19 13a5 5 0 0 1 5 5v1h-4v-1c0-1.9-.66-3.63-1.76-5zM4.25 5.5a3 3 0 1 1 0 6 3 3 0 0 1 0-6m15.5 0a3 3 0 1 1 0 6 3 3 0 0 1 0-6M12 3.5a3.5 3.5 0 1 1 0 7 3.5 3.5 0 0 1 0-7" /></svg>`;
            
            // Notes with recorded versions get a history button that opens the Community notes
            // dialog (see the capture listener in init).
            const noteTweetId = String(raw_json.rest_id || raw_json.legacy?.id_str || '');
            const historyButton = bw.note_history && /^\d+$/.test(noteTweetId) && !this.isDemo
                ? html`<button type="button" class="nook-icon-button is-xs is-action community-note-history" data-note-history="${noteTweetId}" aria-label="Community note history" title="Community note history"><svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2.5a9.5 9.5 0 1 1-9.43 10.62l1.98-.24A7.5 7.5 0 1 0 6.41 7H9.5v2h-6V3h2v2.07A9.5 9.5 0 0 1 12 2.5"/><path d="m13 11.59 3.2 3.2-1.4 1.42-3.8-3.8V8h2z"/></svg></button>`
                : '';
            return String(html`
            <div class="${containerClass}" ${!isQuote ? html`onclick="event.stopPropagation()"` : ''}>
                <div class="community-note-header">
                    ${iconSvg}
                    <span>${title}</span>
                    ${historyButton}
                </div>
                <div class="community-note-body">${rawHTML(textHtml)}</div>
            </div>`);
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
                html`<span class="tweet-icon tweet-icon-liked" aria-hidden="true"></span>` : 
                html`<span class="tweet-icon tweet-icon-like" aria-hidden="true"></span>`;

            const bmkIcon = isBookmarked ? 
                html`<span class="tweet-icon tweet-icon-bookmarked" aria-hidden="true"></span>` : 
                html`<span class="tweet-icon tweet-icon-bookmark" aria-hidden="true"></span>`;

            const margin = isMain ? 'tweet-actions-main border-t border-[var(--border-color)]' : 'mt-3';
            
            return String(html`
            <div class="tweet-actions flex items-center justify-between text-[var(--text-secondary)] w-full ${margin}">
                <div class="flex items-center">
                    <span class="tweet-icon tweet-icon-reply" aria-hidden="true"></span>
                    <span class="tweet-action-count">${formatNum(replyCount)}</span>
                </div>
                <div class="flex items-center">
                    <span class="tweet-icon tweet-icon-repost" aria-hidden="true"></span>
                    <span class="tweet-action-count">${formatNum(retweetCount)}</span>
                </div>
                <div class="flex items-center group cursor-pointer ${isLiked ? 'text-[var(--danger-color)]' : 'hover:text-[var(--danger-color)]'}">
                    ${likeIcon}
                    <span class="tweet-action-count">${formatNum(likeCount)}</span>
                </div>
                ${isMain ? '' : html`<div class="flex items-center tweet-action-views">
                    <span class="tweet-icon tweet-icon-views" aria-hidden="true"></span>
                    <span class="tweet-action-count">${formatNum(viewCount)}</span>
                </div>`}
                <div class="flex items-center group cursor-pointer ${isBookmarked ? 'text-[var(--accent-color)]' : 'hover:text-[var(--accent-color)]'}" ${!isMain ? html`title="${bookmarkLabel}" aria-label="${bookmarkLabel}"` : ''}>
                    ${bmkIcon}
                    ${isMain ? html`<span class="tweet-action-count">${formatNum(bmkCount)}</span>` : ''}
                </div>
                <div class="flex items-center">
                    <a href="https://x.com/${tweet.author?.username || 'i'}/status/${tweet.tweet_id}" target="_blank" @click.stop class="flex items-center" title="Open on Twitter/X">
                        <span class="tweet-icon tweet-icon-open blue-hover transition" aria-hidden="true"></span>
                    </a>
                </div>
            </div>`);
        }
    };
}
