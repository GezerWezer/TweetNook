/**
 * A post's overflow menu (one shared menu for feed cards, one per post elsewhere), the
 * History and Community Notes history dialogs, Copy raw, hiding a post, and the long
 * press that opens a post's tags.
 */

function tweetMenuMethods() {
    // Lists and threads send abbreviated raw JSON. Copy raw reads the stored document,
    // prefetched when a tweet menu opens so the copy usually stays in the click gesture.
    const rawTweetRequests = new Map();
    const rawTweetValues = new Map();
    // The card button currently covered by the shared list menu.
    let listMenuButton = null;

    return {
        tweetMenuOpen: null,
        // Feed cards share one overflow menu: the opening card's key and display
        // tweet, and where its button sits within the list area.
        listMenu: { key: 'list:', tweet: null, hidden: false, top: 0, right: 0 },
        tweetMenuPressTimer: null,
        tweetMenuClickBlockedUntil: 0,

        historyOpen: false,
        historyTweetId: null,
        historyData: null,
        historyLoading: false,
        historyError: '',
        notesOpen: false,
        notesTweetId: null,
        notesData: null,
        notesLoading: false,
        notesError: '',

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
            if (event?.ctrlKey || (event?.pointerType === 'mouse' && event.button !== 0)) return;
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

        async openTweetHistory(tweetId) {
            this.closeTweetMenu();
            const id = String(tweetId || '');
            if (!/^\d+$/.test(id)) return;
            this.historyTweetId = id;
            this.historyData = null;
            this.historyError = '';
            this.historyOpen = true;
            if (this.isDemo || typeof globalThis.fetch !== 'function') {
                this.historyError = 'History is not available in the demo.';
                return;
            }
            this.historyLoading = true;
            try {
                const response = await globalThis.fetch(`/api/tweets/${id}/history`);
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                const data = await response.json();
                if (this.historyTweetId === id) this.historyData = data;
            } catch {
                if (this.historyTweetId === id) this.historyError = "Couldn't load this tweet's history.";
            } finally {
                if (this.historyTweetId === id) this.historyLoading = false;
            }
        },

        closeTweetHistory() {
            this.historyOpen = false;
        },

        // Offered only on tweets that have, or once had, a community note.
        hasNoteHistory(tweet) {
            const raw = tweet?.raw_json;
            return Boolean(tweet?.note_history || (raw && typeof raw === 'object' && raw.birdwatch_pivot));
        },

        async openNoteHistory(tweetId) {
            this.closeTweetMenu();
            const id = String(tweetId || '');
            if (!/^\d+$/.test(id)) return;
            this.notesTweetId = id;
            this.notesData = null;
            this.notesError = '';
            this.notesOpen = true;
            if (this.isDemo || typeof globalThis.fetch !== 'function') {
                this.notesError = 'Community note history is not available in the demo.';
                return;
            }
            this.notesLoading = true;
            try {
                const response = await globalThis.fetch(`/api/tweets/${id}/notes`);
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                const data = await response.json();
                if (this.notesTweetId === id) this.notesData = data;
            } catch {
                if (this.notesTweetId === id) this.notesError = "Couldn't load this tweet's community notes.";
            } finally {
                if (this.notesTweetId === id) this.notesLoading = false;
            }
        },

        closeNoteHistory() {
            this.notesOpen = false;
        },

        notesCurrent() {
            return this.notesData?.versions?.find(version => version.current) || null;
        },

        notesEarlier() {
            return (this.notesData?.versions || []).filter(version => !version.current);
        },

        noteVersionDates(version) {
            const day = value => this.historyTime(value);
            const saved = version.source === 'Saved with the tweet' ? ' · saved with the tweet' : '';
            if (!version.first_seen_at) return `Shown at an unknown time${saved}`;
            if (version.removed_at) return `Shown ${day(version.first_seen_at)} – ${day(version.removed_at)}${saved}`;
            return `Shown since ${day(version.first_seen_at)}${saved}`;
        },

        historyLegend() {
            const shown = new Set(this.historyTimeline().map(event => event.segment));
            return [['on', 'On X'], ['missing', 'Missing'], ['protected', 'Protected']]
                .filter(([segment]) => shown.has(segment));
        },

        historyStatusMeta() {
            const status = this.historyData?.status || {};
            const parts = [status.last_checked_at ? `Last checked ${this.historyTime(status.last_checked_at)}` : 'Not checked yet'];
            if (status.next_login_check_at) parts.push(`next login check ${this.historyTime(status.next_login_check_at)}`);
            return parts.join(' · ');
        },

        // Newest first. Each event's segment is the tweet's state from the older event
        // below it up to this one, so the rail reads as stretches on X, missing, or private.
        historyTimeline() {
            const events = this.historyData?.events || [];
            const stateOf = { available: 'on', missing: 'missing', protected: 'protected' };
            const states = new Array(events.length);
            let state = 'unknown';
            for (let index = events.length - 1; index >= 0; index--) {
                const event = events[index];
                // Being saved to the archive means the tweet was on X at the time.
                const saved = event.source === 'Archive' && state === 'unknown';
                state = stateOf[event.status] || (saved ? 'on' : state);
                states[index] = state;
            }
            return events.map((event, index) => ({
                ...event,
                segment: index + 1 < events.length ? states[index + 1] : 'none',
            }));
        },

        historyTime(value) {
            const date = value ? new Date(value) : null;
            if (!date || Number.isNaN(date.getTime())) return '—';
            return date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
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
            this.discardCachedPages();
            this.updateLoadedTweet(tweetId, tweet => { tweet.hidden = hidden; });
            if (hidden !== this.searchShowsHidden()) this.removeFeedTweet(tweetId);
        },
    };
}
