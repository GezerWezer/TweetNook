/**
 * What surrounds a post's content: dates, counts, Community Notes and the action bar.
 */

function tweetMetaMethods() {
    return {
        formatSyncDate(tweet) {
            let synced = tweet.synced_at || (tweet.collection ? tweet.collection.synced_at : null);
            if (!synced) return 'Archived';
            let dS = new Date(synced);
            return `Synced ${dS.toLocaleDateString('en-US', {month:'short', day:'numeric'})}`;
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

        // X's apps: age in s/m/h/d inside a week, then a numeric date (10/9/26).
        formatAppDate(dateStr, includeTime = false) {
            if (!dateStr) return '';
            const d = new Date(dateStr);
            if (Number.isNaN(d.getTime())) return '';
            const numeric = d.toLocaleDateString('en-US', { month: 'numeric', day: 'numeric', year: '2-digit' });
            if (includeTime) return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }) + ' · ' + numeric;
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
                ? html`<button type="button" class="community-note-history" data-note-history="${noteTweetId}" aria-label="Community note history" title="Community note history"><svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2.5a9.5 9.5 0 1 1-9.43 10.62l1.98-.24A7.5 7.5 0 1 0 6.41 7H9.5v2h-6V3h2v2.07A9.5 9.5 0 0 1 12 2.5"/><path d="m13 11.59 3.2 3.2-1.4 1.42-3.8-3.8V8h2z"/></svg></button>`
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
                html`<svg viewBox="0 0 24 24" class="w-[18.5px] h-[18.5px]" style="fill: var(--danger-color)"><path d="M20.884 13.19c-1.351 2.48-4.001 5.12-8.379 7.67l-.503.3-.504-.3C7.121 18.31 4.471 15.67 3.119 13.19 1.928 10.99 1.898 8.48 2.921 6.45 3.864 4.56 5.8 3.32 8.016 3.42c1.474.07 2.812.8 3.486 2.08l.498.94.498-.94c.674-1.28 2.012-2.01 3.486-2.08 2.216-.1 4.152 1.14 5.095 3.03 1.023 2.03.993 4.54-.195 6.74z"></path></svg>` : 
                html`<svg viewBox="0 0 24 24" class="w-[18.5px] h-[18.5px] fill-current group-hover:fill-[var(--danger-color)]"><path d="M16.697 5.5c-1.222-.06-2.679.51-3.89 2.16l-.805 1.09-.806-1.09C9.984 6.01 8.526 5.44 7.304 5.5c-1.243.07-2.349.78-2.91 1.91-.552 1.12-.633 2.78.479 4.82 1.074 1.97 3.257 4.27 7.129 6.61 3.87-2.34 6.052-4.64 7.126-6.61 1.111-2.04 1.03-3.7.477-4.82-.561-1.13-1.666-1.84-2.908-1.91zm4.187 7.69c-1.351 2.48-4.001 5.12-8.379 7.67l-.503.3-.504-.3c-4.379-2.55-7.029-5.19-8.382-7.67-1.36-2.5-1.41-4.86-.514-6.67.887-1.79 2.647-2.91 4.601-3.01 1.651-.09 3.368.56 4.798 2.01 1.429-1.45 3.146-2.1 4.796-2.01 1.954.1 3.714 1.22 4.601 3.01.896 1.81.846 4.17-.514 6.67z"></path></svg>`;

            const bmkIcon = isBookmarked ? 
                html`<svg viewBox="0 0 24 24" class="w-[18.5px] h-[18.5px] fill-[var(--accent-color)]"><path d="M4 4.5C4 3.12 5.119 2 6.5 2h11C18.881 2 20 3.12 20 4.5v18.44l-8-5.71-8 5.71V4.5z"></path></svg>` : 
                html`<svg viewBox="0 0 24 24" class="w-[18.5px] h-[18.5px] fill-current"><path d="M4 4.5C4 3.12 5.119 2 6.5 2h11C18.881 2 20 3.12 20 4.5v18.44l-8-5.71-8 5.71V4.5zM6.5 4c-.276 0-.5.22-.5.5v14.56l6-4.29 6 4.29V4.5c0-.28-.224-.5-.5-.5H6.5z"></path></svg>`;

            const margin = isMain ? 'tweet-actions-main border-t border-[var(--border-color)]' : 'mt-3';
            
            return String(html`
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
                ${isMain ? '' : html`<div class="flex items-center tweet-action-views">
                    <svg viewBox="0 0 24 24" class="w-[18.5px] h-[18.5px] fill-current"><path d="M8.75 21V3h2v18h-2zM18 21V8.5h2V21h-2zM4 21l.004-10h2L6 21H4zm9.248 0v-7h2v7h-2z"></path></svg>
                    <span class="tweet-action-count">${formatNum(viewCount)}</span>
                </div>`}
                <div class="flex items-center group cursor-pointer ${isBookmarked ? 'text-[var(--accent-color)]' : 'hover:text-[var(--accent-color)]'}" ${!isMain ? html`title="${bookmarkLabel}" aria-label="${bookmarkLabel}"` : ''}>
                    ${bmkIcon}
                    ${isMain ? html`<span class="tweet-action-count">${formatNum(bmkCount)}</span>` : ''}
                </div>
                <div class="flex items-center">
                    <a href="https://x.com/${tweet.author?.username || 'i'}/status/${tweet.tweet_id}" target="_blank" @click.stop class="flex items-center" title="Open on Twitter/X">
                        <svg viewBox="0 0 24 24" class="w-[18.5px] h-[18.5px] fill-current blue-hover transition" fill="currentColor"><g transform="translate(24 0) scale(-1 1)"><path d="M16.2 6q1.23-.01 2.05.04a4 4 0 0 1 1.57.4 4 4 0 0 1 1.74 1.74c.25.49.35 1 .4 1.57q.05.82.04 2.05v4.4q.01 1.23-.04 2.05a4 4 0 0 1-.4 1.57 4 4 0 0 1-1.74 1.74c-.49.25-1 .35-1.57.4q-.82.05-2.05.04h-4.4q-1.23.01-2.05-.04a4 4 0 0 1-1.57-.4 4 4 0 0 1-1.74-1.74 4 4 0 0 1-.4-1.57Q6 17.43 6 16.2V14h2v2.2c0 .86 0 1.44.04 1.89.03.44.1.66.18.82q.3.57.87.87c.16.08.38.15.82.18.45.04 1.03.04 1.89.04h4.4c.86 0 1.44 0 1.89-.04.44-.03.66-.1.82-.18a2 2 0 0 0 .87-.87c.08-.16.15-.38.18-.82.04-.45.04-1.03.04-1.89v-4.4c0-.86 0-1.44-.04-1.89-.03-.44-.1-.66-.18-.82a2 2 0 0 0-.87-.87 2 2 0 0 0-.82-.18C17.64 8 17.06 8 16.2 8H14V6z" /><path d="M11 5H6.41l5.55 5.54-1.42 1.42L5 6.4V11H3V3h8z" /></g></svg>
                    </a>
                </div>
            </div>`);
        }
    };
}
