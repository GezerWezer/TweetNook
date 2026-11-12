/**
 * Thread replies, rendered as one HTML string outside Alpine like feed cards. A batch of
 * 30 replies bound through x-for held hundreds of reactive effects; one x-effect now
 * renders the visible replies, and clicks reach them through the feed's data-action map.
 */

function threadReplyMethods() {
    // Each rendered container's replies by id, for actions that need the tweet itself.
    const repliesByContainer = new WeakMap();

    return {
        renderThreadReplyRow(tweet, { nested = false, connectorDown = false, data = null, panel = false } = {}) {
            const part = (markup, attributes = '') => (markup ? html`<div${rawHTML(attributes)}>${rawHTML(markup)}</div>` : '');
            const author = tweet.author || {};
            const recipients = this.getReplyRecipients(tweet);
            const replyTo = this.getReplyTo(tweet);
            const showRecipients = nested
                ? recipients.length > 0
                : recipients.length > 1 || (replyTo && replyTo !== data?.main?.author?.username);
            // The split panel's rows skip the page's phone-width thread-row layout.
            const rowClass = `${panel ? '' : 'thread-row '}${nested
                ? 'thread-nested-row px-4 pb-3 flex relative cursor-pointer hover-bg'
                : 'px-4 py-3 flex relative cursor-pointer hover-bg'}`;
            const indent = nested ? ` style="margin-left: ${Number(this.nestedReplyIndent(tweet)) || 0}px"` : '';
            return String(html`${[
                html`<div class="${rowClass}"${rawHTML(indent)} data-thread-id="${tweet.tweet_id}" data-reply-id="${tweet.tweet_id}" data-action="thread">`,
                nested ? html`<div class="thread-connector-up w-0.5 bg-[var(--thread-line)] absolute top-0 left-[36px] -translate-x-1/2 z-0"></div>` : '',
                connectorDown ? html`<div class="thread-connector-down w-0.5 bg-[var(--thread-line)] absolute bottom-0 left-[36px] -translate-x-1/2 z-0"></div>` : '',
                html`<div class="avatar w-10 h-10 rounded-full flex-shrink-0 relative bg-[var(--gray-700)] flex items-center justify-center font-bold text-[var(--white-icon)] uppercase overflow-hidden z-10 mr-2" data-action="profile">`,
                html`<span class="avatar-initial">${this.getInitial(tweet)}</span>`,
                html`<img src="${this.avatarUrl(author.id)}" class="avatar-image absolute inset-0 w-full h-full object-cover z-10" loading="lazy"></div>`,
                html`<div class="flex-1 min-w-0 z-10">`,
                html`<div class="tweet-byline flex items-center space-x-1 text-[15px]">`,
                html`<span class="font-bold text-[var(--text-primary)] truncate hover:underline tweet-byline-name" data-action="profile">${author.display_name || 'Unknown'}</span>`,
                html`<span class="text-[var(--text-secondary)] tweet-byline-detail tweet-byline-handle">${`@${author.username || 'unknown'}`}</span>`,
                html`<span class="text-[var(--text-secondary)]">·</span>`,
                html`<div class="roll-wrapper text-[var(--text-secondary)] tweet-byline-detail" data-action="stop"><div class="roll-inner">`,
                html`<span class="roll-item hover:underline">${rawHTML(this.dateLabel(tweet.created_at))}</span>`,
                html`<span class="roll-item text-[var(--accent-color)] text-[13px] font-medium">${this.formatSyncDate(tweet)}</span>`,
                html`</div></div></div>`,
                showRecipients ? rawHTML(this.renderReplyRecipients(tweet)) : '',
                html`<div class="tweet-text text-[15px] whitespace-pre-wrap break-words" data-action="text">${rawHTML(this.formatText(tweet))}</div>`,
                part(this.renderCard(tweet), ' data-action="stop"'),
                part(this.renderMediaGrid(tweet.media, 'feed', tweet), ' data-action="media"'),
                rawHTML(this.renderQuoteCard(tweet, { highlightSearch: false })),
                part(this.renderCommunityNote(tweet.raw_json), ' data-action="stop"'),
                part(this.renderActionBar(tweet)),
                html`</div></div>`,
            ]}`);
        },

        // x-effect calls this with the visible replies; reading them there subscribes the
        // container to the thread, its reply limit, and the other-replies switch.
        renderThreadReplies(container, children, data, showOther) {
            const otherId = this.firstOtherReplyId(data, showOther);
            const panel = container.dataset?.panel !== undefined;
            const byId = new Map();
            const markup = children.map(child => {
                byId.set(String(child.tweet_id), child);
                const replies = child.op_replies || [];
                for (const reply of replies) byId.set(String(reply.tweet_id), reply);
                return String(html`${[
                    html`<div class="tweet-border">`,
                    child.tweet_id === otherId ? html`<h2 class="px-4 pt-4 pb-1 text-xl font-extrabold text-[var(--text-primary)]">Other replies</h2>` : '',
                    rawHTML(this.renderThreadReplyRow(child, { connectorDown: replies.length > 0, data, panel })),
                    replies.length ? html`<div>${replies.map((reply, index) => rawHTML(this.renderThreadReplyRow(reply, { nested: true, connectorDown: index < replies.length - 1, data, panel })))}</div>` : '',
                    html`</div>`,
                ]}`);
            }).join('');
            repliesByContainer.set(container, byId);
            outsideAlpine(() => { container.innerHTML = markup; });
        },

        // Replies use the feed's click map; only a profile needs the reply's own tweet.
        handleThreadReplyClick(event) {
            const container = event.currentTarget;
            const profile = event.target?.closest?.('[data-action="profile"]');
            if (profile && container.contains(profile)) {
                event.stopPropagation();
                const id = profile.closest('[data-reply-id]')?.dataset.replyId;
                const tweet = repliesByContainer.get(container)?.get(id);
                if (tweet) this.openProfileCard({ currentTarget: profile }, tweet);
                return;
            }
            // The split panel opens threads in place rather than navigating.
            const panel = container.dataset.panel !== undefined;
            this.handleFeedClick(event, container, id => (panel ? this.openThread(id, false, true) : this.openThread(id)));
        },
    };
}
