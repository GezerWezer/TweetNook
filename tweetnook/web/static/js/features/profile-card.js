/**
 * The profile card opened from an author or @mention: the account's archived profile
 * snapshots, and placing the card below (or above) what opened it.
 */

function profileCardMethods() {
    return {
        profileCard: null,
        profileCardRequest: 0,

        openUsernameProfileCard(event, username) {
            if (!username) return;
            this.openProfileCard(
                event,
                {
                    author: {
                        display_name: username,
                        username,
                        id: 'unknown',
                    },
                },
                false,
                true,
            );
        },

        openProfileCard(event, tweet, isQt = false, useClickedAnchor = false) {
            let anchor = event.currentTarget;
            if (!useClickedAnchor && !anchor.classList.contains('w-10')) {
                // Attempt to find the pfp avatar container which is usually a previous sibling or in the parent flex row
                let container = anchor.closest('.flex.space-x-2') || anchor.closest('.flex.relative');
                if (!container && anchor.parentElement && anchor.parentElement.parentElement) {
                    container = anchor.parentElement.parentElement;
                }
                if (container) {
                    const pfp = container.querySelector('.w-10.h-10.rounded-full');
                    if (pfp) anchor = pfp;
                }
            }

            const rect = anchor.getBoundingClientRect();

            let name, username, id;
            let raw;
            if (isQt) {
                raw = tweet.raw_json || tweet;
                const authorInfo = this.getQuoteAuthor(tweet);
                name = authorInfo.name;
                username = authorInfo.screen_name;
                id = authorInfo.id;
            } else {
                raw = tweet.raw_json || {};
                name = tweet.author?.display_name || 'Unknown';
                username = tweet.author?.username || 'unknown';
                id = tweet.author?.id || 'unknown';
            }
            
            const core = raw.core || {};
            const userResult = core.user_results?.result || {};
            const legacy = userResult.legacy || raw.user || {};
            
            this.profileCard = {
                x: rect.left,
                y: rect.bottom + 8,
                anchorRect: { left: rect.left, top: rect.top, bottom: rect.bottom },
                request: ++this.profileCardRequest,
                name,
                username,
                id,
                initial: avatarInitial(name).toUpperCase(),
                description: legacy.description || '',
                followersCount: legacy.followers_count || 0,
                followingCount: legacy.friends_count || 0,
                syncDate: tweet.synced_at || (tweet.collection ? tweet.collection.synced_at : null) || raw.synced_at || null,
                snapshots: [],
                snapshotIndex: 0,
            };
            this.positionProfileCard();
            this.loadProfileSnapshots();

            if (!this._profileMouseMoveHandler) {
                this._profileMouseMoveHandler = (e) => {
                    if (!this.profileCard) return;
                    const card = document.getElementById('profile-card-modal');
                    if (!card) return;
                    const cardRect = card.getBoundingClientRect();
                    const bufferX = 30;
                    const bufferY = 80;
                    if (
                        e.clientX < cardRect.left - bufferX ||
                        e.clientX > cardRect.right + bufferX ||
                        e.clientY < cardRect.top - bufferY ||
                        e.clientY > cardRect.bottom + bufferY
                    ) {
                        this.closeProfileCard();
                    }
                };
            }
            window.addEventListener('mousemove', this._profileMouseMoveHandler);
        },
        
        // The clicked tweet carries the profile as it was when that tweet was
        // fetched. Replace it with the most recently fetched capture of the
        // author, keeping older distinct captures to cycle through.
        async loadProfileSnapshots() {
            const card = this.profileCard;
            if (!card) return;
            const request = card.request;
            let query;
            if (card.id && card.id !== 'unknown') {
                query = `author_id=${encodeURIComponent(card.id)}`;
            } else if (card.username && card.username !== 'unknown') {
                query = `username=${encodeURIComponent(card.username)}`;
            } else {
                return;
            }
            try {
                const res = await fetch(`/api/authors/profile?${query}`);
                if (!res.ok) return;
                const data = await res.json();
                // The card may have closed or opened for someone else meanwhile.
                if (this.profileCard?.request !== request) return;
                if (data.author_id) this.profileCard.id = data.author_id;
                if (!data.snapshots?.length) return;
                this.profileCard.snapshots = data.snapshots;
                this.showProfileSnapshot(0, false);
            } catch (e) {
                console.error('Failed to load profile snapshots:', e);
            }
        },

        // Index 0 is the most recent capture; higher indexes are older. Cycling
        // keeps the card's top fixed so the arrows stay under the pointer.
        showProfileSnapshot(index, keepTop = true) {
            const card = this.profileCard;
            const snapshot = card?.snapshots?.[index];
            if (!snapshot) return;
            card.snapshotIndex = index;
            card.name = snapshot.display_name || card.name;
            card.username = snapshot.username || card.username;
            card.initial = avatarInitial(card.name).toUpperCase();
            card.description = snapshot.description || '';
            card.followersCount = snapshot.followers_count || 0;
            card.followingCount = snapshot.following_count || 0;
            card.syncDate = snapshot.fetched_at || card.syncDate;
            this.positionProfileCard(keepTop);
        },

        // Places the card below its anchor, or above it when it would overflow.
        // With keepTop, the card stays put unless it would leave the viewport.
        positionProfileCard(keepTop = false) {
            this.$nextTick(() => {
                const card = document.getElementById('profile-card-modal');
                if (!card || !this.profileCard) return;
                const anchor = this.profileCard.anchorRect;
                const cardRect = card.getBoundingClientRect();
                let x = anchor.left;
                let y = keepTop ? this.profileCard.y : anchor.bottom + 8;
                if (x + cardRect.width > window.innerWidth) {
                    x = window.innerWidth - cardRect.width - 16;
                }
                if (y + cardRect.height > window.innerHeight) {
                    y = keepTop
                        ? Math.max(8, window.innerHeight - cardRect.height - 8)
                        : anchor.top - cardRect.height - 8;
                }
                this.profileCard.x = x;
                this.profileCard.y = y;
            });
        },

        closeProfileCard() {
            this.profileCard = null;
            if (this._profileMouseMoveHandler) {
                window.removeEventListener('mousemove', this._profileMouseMoveHandler);
            }
        },
    };
}
