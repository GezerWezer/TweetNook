/**
 * Tags: the archive's tag list for the Tags settings page, and the dialog that shows
 * and edits one post's tags and description (tagDialog(), its own component).
 */

function tagMethods() {
    return {
        globalTags: [],
        globalTagsLoading: false,

        openTagsFromTweetMenu(tweetId, tagsData) {
            this.closeTweetMenu();
            this.openTagModal(tweetId, tagsData);
        },

        // The app can't reach into the dialog's component, so it asks with an event.
        openTagModal(tweetId, tagsData) {
            this.closeTweetMenu();
            window.dispatchEvent(new CustomEvent('open-tag-dialog', { detail: { tweetId, tagsData } }));
        },

        updateTweetTagData(tweetId, tagData) {
            this.updateLoadedTweet(tweetId, tweet => {
                tweet.media_tags = tagData ? {
                    description: tagData.description,
                    tags: [...tagData.tags],
                } : null;
            });
        },

        async fetchGlobalTags() {
            this.globalTagsLoading = true;
            try {
                const res = await fetch('/api/tags/stats');
                if (res.ok) {
                    const data = await res.json();
                    this.globalTags = data.tags;
                }
            } catch(e) { console.error(e); }
            finally { this.globalTagsLoading = false; }
        },
    };
}

// The tag dialog (x-data="tagDialog()"), opened by the app's open-tag-dialog event.
function tagDialog() {
    let closeTimer = null;
    let autocompleteRequest = 0;
    let dialogGeneration = 0;
    return {
        tagModalOpen: false,
        tagModalData: null,
        tagModalTweetId: null,
        
        isEditingTags: false,
        editableTags: [],
        editableDescription: '',
        tagSearchQuery: '',
        tagAutocompleteOptions: [],
        tagSearchDropdown: false,
        tagOptionIndex: -1,
        tagDeleteConfirm: false,
        tagSaving: false,
        tagError: '',
        tagConfirmError: '',
        tagFocusTarget: 'tags',

        init() {
            window.addEventListener('open-tag-dialog', event => {
                this.showTagDialog(event.detail.tweetId, event.detail.tagsData);
            });
        },

        showTagDialog(tweetId, tagsData) {
            dialogGeneration++;
            const normalizedData = {
                description: typeof tagsData?.description === 'string' ? tagsData.description : '',
                tags: Array.isArray(tagsData?.tags) ? [...tagsData.tags] : [],
            };
            if (closeTimer) clearTimeout(closeTimer);
            this.tagModalTweetId = tweetId;
            this.tagModalData = normalizedData;
            this.isEditingTags = false;
            this.editableTags = [...normalizedData.tags];
            this.editableDescription = normalizedData.description;
            this.tagSearchQuery = '';
            this.tagSearchDropdown = false;
            this.tagOptionIndex = -1;
            this.tagDeleteConfirm = false;
            this.tagSaving = false;
            this.tagError = '';
            this.tagConfirmError = '';
            // A tweet with nothing saved has nothing to view, so it opens in the editor.
            if (!this.tagModalHasContent()) this.startEditingTags('tags');
            this.tagModalOpen = true;
            document.body.style.overflow = 'hidden';
        },

        closeTagModal() {
            dialogGeneration++;
            this.tagModalOpen = false;
            this.tagSaving = false;
            this.tagDeleteConfirm = false;
            closeTimer = setTimeout(() => {
                this.tagModalData = null;
                this.tagModalTweetId = null;
                this.isEditingTags = false;
                this.editableTags = [];
                this.editableDescription = '';
                this.tagSearchDropdown = false;
                this.tagSearchQuery = '';
                document.body.style.overflow = '';
                closeTimer = null;
            }, 300);
        },

        tagModalHasContent() {
            return Boolean(this.tagModalData?.tags?.length || this.tagModalData?.description);
        },

        // The loaded tweet the dialog is tagging, shown as a quoted card; null when it is not loaded.
        tagModalTweet() {
            if (!this.tagModalTweetId) return null;
            let found = null;
            this.updateLoadedTweet(this.tagModalTweetId, tweet => { found = found || tweet; });
            const retweet = found && this.getRetweet(found);
            return retweet ? this.formatRetweetToTweet(found, retweet) : found;
        },

        tagIsDirty() {
            const saved = this.tagModalData || { tags: [], description: '' };
            return this.editableTags.length !== saved.tags.length
                || this.editableTags.some((tag, i) => tag !== saved.tags[i])
                || this.editableDescription !== saved.description
                || Boolean(this.tagSearchQuery.trim());
        },

        // Focuses a dialog control once x-show has made it visible.
        focusTagDialogControl(id, tries = 12) {
            const el = document.getElementById(id);
            if (el && el.getClientRects().length) {
                el.focus();
                if (id === 'nook-td-desc') el.setSelectionRange(el.value.length, el.value.length);
            } else if (tries > 0) {
                setTimeout(() => this.focusTagDialogControl(id, tries - 1), 16);
            }
        },

        startEditingTags(focusTarget = 'tags') {
            this.tagFocusTarget = focusTarget;
            this.isEditingTags = true;
            this.editableTags = [...(this.tagModalData?.tags || [])];
            this.editableDescription = this.tagModalData?.description || '';
            this.tagSearchQuery = '';
            this.tagSearchDropdown = false;
            this.tagOptionIndex = -1;
            this.tagError = '';
        },

        // Cancel discards edits; a tweet without tags has no view to return to.
        cancelEditingTags() {
            if (this.tagSaving) return;
            if (!this.tagModalHasContent()) {
                this.closeTagModal();
                return;
            }
            this.isEditingTags = false;
            this.tagSearchDropdown = false;
            this.tagSearchQuery = '';
            this.tagOptionIndex = -1;
            this.tagError = '';
        },

        // Unsaved edits keep the dialog open when the backdrop is clicked.
        tagBackdropClick() {
            if (this.tagSaving || (this.isEditingTags && this.tagIsDirty())) return;
            this.closeTagModal();
        },

        // Escape closes the innermost layer first; Cmd/Ctrl+Enter saves the editor.
        tagDialogKeydown(event) {
            if (event.key === 'Escape') {
                event.preventDefault();
                this.tagDialogEscape();
            } else if (event.key === 'Enter' && (event.metaKey || event.ctrlKey) && this.isEditingTags && !this.tagDeleteConfirm) {
                event.preventDefault();
                this.saveTags();
            }
        },

        tagDialogEscape() {
            if (this.tagDeleteConfirm) this.closeDeleteConfirm();
            else if (this.tagSearchDropdown && this.tagSuggestions().length) {
                this.tagSearchDropdown = false;
                this.tagOptionIndex = -1;
            } else if (!this.tagSaving) this.closeTagModal();
        },

        tagDeleteLabel() {
            const hasTags = Boolean(this.tagModalData?.tags?.length);
            const hasDescription = Boolean(this.tagModalData?.description);
            if (hasTags && hasDescription) return 'Delete tags and description';
            return hasTags ? 'Delete tags' : 'Delete description';
        },

        tagDeleteCopy() {
            const count = this.tagModalData?.tags?.length || 0;
            const parts = [];
            if (count) parts.push(count === 1 ? '1 tag' : `${count} tags`);
            if (this.tagModalData?.description) parts.push('the description');
            return `This removes ${parts.join(' and ')} from this tweet. Other tweets keep their tags. This can’t be undone.`;
        },

        requestDeleteTags() {
            this.tagConfirmError = '';
            this.tagDeleteConfirm = true;
            this.focusTagDialogControl('nook-td-confirm-cancel');
        },

        closeDeleteConfirm() {
            if (this.tagSaving) return;
            this.tagDeleteConfirm = false;
            this.focusTagDialogControl('nook-td-delete');
        },

        searchForTag(tag) {
            this.closeTagModal();
            this.searchQuery = 'tag:' + (/\s/.test(tag) ? `"${tag}"` : tag);
            this.search();
        },

        tagCountLabel(count) {
            return count === 1 ? '1 tweet' : `${Number(count || 0).toLocaleString()} tweets`;
        },

        // Suggestion label with the typed text in bold.
        tagOptionHtml(tag) {
            const query = this.tagSearchQuery.trim();
            const at = query ? tag.toLowerCase().indexOf(query.toLowerCase()) : -1;
            if (at < 0) return this.escapeHTML(tag);
            return this.escapeHTML(tag.slice(0, at))
                + '<b>' + this.escapeHTML(tag.slice(at, at + query.length)) + '</b>'
                + this.escapeHTML(tag.slice(at + query.length));
        },

        // Existing tags not already on the tweet, then an "Add new tag" row for unknown text.
        tagSuggestions() {
            const query = this.tagSearchQuery.trim().replace(/\s+/g, ' ');
            const folded = query.toLowerCase();
            const added = new Set(this.editableTags.map(tag => tag.toLowerCase()));
            const known = this.tagAutocompleteOptions || [];
            const options = known
                .filter(opt => !added.has(opt.tag.toLowerCase()) && (!folded || opt.tag.toLowerCase().includes(folded)))
                .map(opt => ({ tag: opt.tag, count: opt.count, isNew: false }));
            if (query && !known.some(opt => opt.tag.toLowerCase() === folded) && !added.has(folded)) {
                options.push({ tag: query, count: 0, isNew: true });
            }
            return options;
        },

        openTagSuggestions() {
            this.tagSearchDropdown = true;
            this.tagOptionIndex = -1;
            this.fetchTagAutocomplete(this.tagSearchQuery);
        },

        // Keeps the suggestion menu and its active row inside the dialog's scroll area.
        revealTagOptions() {
            const list = this.$refs?.tagOptions;
            if (!list || !list.getClientRects().length) return;
            const active = this.tagOptionIndex >= 0 && document.getElementById('nook-td-option-' + this.tagOptionIndex);
            (active || list).scrollIntoView({ block: 'nearest' });
        },

        pickTagSuggestion(opt) {
            this.addEditableTag(opt.tag);
            this.tagSearchDropdown = false;
            this.tagOptionIndex = -1;
            this.$refs?.tagSearchInput?.focus();
        },

        // Returns the removed tag's position so focus can move to its neighbour.
        removeEditableTag(tag) {
            const index = this.editableTags.indexOf(tag);
            if (index >= 0) this.editableTags = this.editableTags.filter(t => t !== tag);
            return index;
        },

        tagInputKeydown(event) {
            if (event.isComposing || event.keyCode === 229) return;
            const options = this.tagSuggestions();
            const open = this.tagSearchDropdown && options.length > 0;
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault();
                const down = event.key === 'ArrowDown';
                if (!this.tagSearchDropdown) {
                    this.openTagSuggestions();
                    this.tagOptionIndex = down ? 0 : -1;
                    return;
                }
                if (!options.length) return;
                if (down) this.tagOptionIndex = (this.tagOptionIndex + 1) % options.length;
                else this.tagOptionIndex = this.tagOptionIndex <= 0 ? options.length - 1 : this.tagOptionIndex - 1;
                this.$nextTick?.(() => this.revealTagOptions());
            } else if (event.key === 'Enter') {
                if (event.metaKey || event.ctrlKey) return;
                event.preventDefault();
                if (open && this.tagOptionIndex >= 0) this.pickTagSuggestion(options[this.tagOptionIndex]);
                else if (this.tagSearchQuery.trim()) this.pickTagSuggestion({ tag: this.tagSearchQuery });
            } else if (event.key === 'Backspace' && !this.tagSearchQuery && this.editableTags.length) {
                event.preventDefault();
                this.editableTags = this.editableTags.slice(0, -1);
            } else if (event.key === 'Escape' && open) {
                event.preventDefault();
                event.stopPropagation();
                this.tagSearchDropdown = false;
                this.tagOptionIndex = -1;
            } else if (event.key === 'Tab') {
                this.tagSearchDropdown = false;
            }
        },

        async fetchTagAutocomplete(query) {
            const request = ++autocompleteRequest;
            try {
                const res = await fetch(`/api/tags/autocomplete?q=${encodeURIComponent(query.trim())}`);
                if (res.ok && request === autocompleteRequest) {
                    const data = await res.json();
                    this.tagAutocompleteOptions = data.tags;
                    if (this.tagOptionIndex >= this.tagSuggestions().length) this.tagOptionIndex = -1;
                }
            } catch(e) {}
        },

        addEditableTag(tag) {
            const trimmed = tag.trim().replace(/\s+/g, ' ');
            if(trimmed && !this.editableTags.find(t => t.toLowerCase() === trimmed.toLowerCase())) {
                this.editableTags.push(trimmed);
            }
            this.tagSearchQuery = '';
        },

        async saveTags() {
            if (this.tagSaving || !this.tagIsDirty()) return;
            // Text left in the tag field counts as a tag.
            if (this.tagSearchQuery.trim()) this.addEditableTag(this.tagSearchQuery);
            const generation = dialogGeneration;
            const tweetId = this.tagModalTweetId;
            const tags = [...this.editableTags];
            const description = this.editableDescription.trim();
            this.tagSaving = true;
            this.tagSearchDropdown = false;
            this.tagError = '';
            try {
                const res = await fetch(`/api/tags/${tweetId}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ tags, description })
                });
                if (!res.ok) throw new Error(`Server error: ${res.status}`);
                const savedData = tags.length || description ? {
                    description,
                    tags,
                } : null;
                this.updateTweetTagData(tweetId, savedData);
                // The request still updates its post, but a reopened editor owns
                // its own data, errors and pending state, even for the same post.
                if (generation !== dialogGeneration) return;
                if (savedData) {
                    this.tagModalData = savedData;
                    this.isEditingTags = false;
                } else {
                    this.closeTagModal();
                }
            } catch(e) {
                if (generation === dialogGeneration) {
                    this.tagError = 'Couldn’t save your changes. They’re still here, so you can try again.';
                }
            } finally {
                if (generation === dialogGeneration) this.tagSaving = false;
            }
        },

        // Runs from the dialog's Delete confirmation.
        async deleteTags() {
            if (!this.tagModalTweetId || this.tagSaving) return;
            const generation = dialogGeneration;
            const tweetId = this.tagModalTweetId;
            this.tagSaving = true;
            this.tagConfirmError = '';
            try {
                const res = await fetch(`/api/tags/${tweetId}`, { method: 'DELETE' });
                if (!res.ok) throw new Error(`Server error: ${res.status}`);
                this.updateTweetTagData(tweetId, null);
                if (generation !== dialogGeneration) return;
                this.tagSaving = false;
                this.closeTagModal();
            } catch (e) {
                if (generation === dialogGeneration) {
                    this.tagConfirmError = 'Couldn’t delete. Nothing was changed. Try again.';
                }
            } finally {
                if (generation === dialogGeneration) this.tagSaving = false;
            }
        },
    };
}
