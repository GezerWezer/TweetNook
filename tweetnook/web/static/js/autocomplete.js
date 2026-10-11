/**
 * Search autocomplete component for the TweetNook Web UI.
 */

// Suggestions are requested once typing pauses this long.
const SUGGESTION_DELAY_MS = 100;

// Filters whose value is edited inside its capsule, so spaces stay part of the
// value without typing quotes.
const BOX_FILTER_KEYS = ['description', 'tag'];

// Private-use markers for the caret and capsule edges while the editor is
// read back and re-rendered.
const SEARCH_CARET = '\uE000';
const BOX_START = '\uE001';
const BOX_END = '\uE002';

function searchAutocomplete() {
    return {
        suggestionRequest: 0,
        suggestionTimer: null,
        showDropdown: false,
        selectedIndex: 0,
        options: [],
        cursorPos: 0,
        isComposing: false,
        dpYear: new Date().getFullYear(),
        dpMonth: new Date().getMonth(),
        dpHoverDate: null,
        
        init() {
            this.$watch('searchQuery', (val) => {
                const input = this.$refs.searchInput;
                if (input && !this.isComposing && this.editorSearchQuery(input) !== (val || '')) {
                    this.formatRichText(val || '');
                }
            });
            this.$nextTick(() => {
                if (this.$refs.searchInput) {
                    this.formatRichText(this.searchQuery || '');
                }
            });
            // Arrow keys and clicks move the caret without input events.
            document.addEventListener('selectionchange', () => this.syncEditingState());
        },
        
        allFilters: [
            { prefix: 'from:', desc: 'Sent from a specific user' },
            { prefix: 'to:', desc: 'Replying to a specific user' },
            { prefix: 'has:', desc: 'Includes specific attached content' },
            { prefix: 'is:', desc: 'Matches a tweet type or state' },
            { prefix: 'since:', desc: 'After a specific date (YYYY-MM-DD)' },
            { prefix: 'until:', desc: 'Before a specific date (YYYY-MM-DD)' },
            { prefix: 'url:', desc: 'Contains a specific URL' },
            { prefix: 'tag:', desc: 'Matches media tag' },
            { prefix: 'description:', desc: 'Words in a tag description' }
        ],
        
        hasOptions: [
            { prefix: 'has:', value: 'media', desc: 'Images, videos, or GIFs' },
            { prefix: 'has:', value: 'image', desc: 'Only images' },
            { prefix: 'has:', value: 'video', desc: 'Videos or GIFs' },
            { prefix: 'has:', value: 'links', desc: 'External links' },
            { prefix: 'has:', value: 'article', desc: 'Attached archived article' }
        ],
        
        isOptions: [
            { prefix: 'is:', value: 'reply', desc: 'Replies to other tweets' },
            { prefix: 'is:', value: 'quote', desc: 'Quote tweets' },
            { prefix: 'is:', value: 'retweet', desc: 'Native reposts' },
            { prefix: 'is:', value: 'thread', desc: 'Self-reply threads' },
            { prefix: 'is:', value: 'verified', desc: 'From verified users' },
            { prefix: 'is:', value: 'resurrected', desc: 'Previously unavailable and recovered' },
            { prefix: 'is:', value: 'hidden', desc: 'Tweets you hid from lists' }
        ],

        currentSearchTokenStart(text) {
            let tokenStart = 0;
            let inQuotes = false;
            for (let index = 0; index < text.length; index += 1) {
                if (text[index] === '"') {
                    inQuotes = !inQuotes;
                } else if (/\s/.test(text[index]) && !inQuotes) {
                    tokenStart = index + 1;
                }
            }
            return tokenStart;
        },

        // A picked filter value replaces the rest of the value being edited:
        // up to the closing quote, or to the end of an unquoted value.
        currentSearchTokenEnd(text, cursor, tokenStart) {
            const token = text.substring(tokenStart, cursor);
            if (!token.includes(':')) return cursor;
            if ((token.match(/"/g) || []).length % 2 === 1) {
                const closingQuote = text.indexOf('"', cursor);
                return closingQuote === -1 ? cursor : closingQuote + 1;
            }
            return cursor + text.substring(cursor).match(/^[^\s"]*/)[0].length;
        },

        // A response that arrives after newer input is discarded instead of
        // replacing the current suggestions.
        fetchSuggestions(url, toOptions) {
            const request = ++this.suggestionRequest;
            clearTimeout(this.suggestionTimer);
            this.suggestionTimer = setTimeout(() => {
                fetch(url)
                    .then(res => res.ok ? res.json() : {})
                    .then(data => {
                        if (request !== this.suggestionRequest) return;
                        this.options = toOptions(data || {});
                        if (this.options.length === 0) {
                            this.showDropdown = false;
                        }
                    })
                    .catch(err => {
                        if (request !== this.suggestionRequest) return;
                        console.error(err);
                        this.options = [];
                        this.showDropdown = false;
                    });
            }, SUGGESTION_DELAY_MS);
        },

        handleInput() {
            this.suggestionRequest++;
            clearTimeout(this.suggestionTimer);
            const input = this.$refs.searchInput;
            
            // The rich input is read back with its caret marked, so the query and
            // caret position stay correct when capsules hide their quotes.
            if (input.isContentEditable) {
                const parts = this.parseSearchParts(this.extractMarkedSearch(input));
                const { query, caret } = this.serializeSearchParts(parts);
                this.searchQuery = query;
                this.cursorPos = caret === -1 ? query.length : caret;

                if (!this.isComposing) {
                    this.renderSearchParts(parts);
                }
            } else {
                this.cursorPos = input.selectionStart;
            }
            
            const textToCursor = this.searchQuery.substring(0, this.cursorPos);
            const tokenStart = this.currentSearchTokenStart(textToCursor);
            const currentWord = textToCursor.substring(tokenStart);
            
            this.showDropdown = true;
            this.selectedIndex = 0;
            
            if (currentWord.includes(':')) {
                const parts = currentWord.split(':');
                const rawPrefix = parts[0] + ':';
                const basePrefix = rawPrefix.startsWith('-') ? rawPrefix.substring(1) : rawPrefix;
                const val = parts.slice(1).join(':').replace(/^"/, '').replace(/"$/, '').toLowerCase();
                
                if (basePrefix === 'has:') {
                    this.options = this.hasOptions.filter(o => o.value.startsWith(val)).map(o => ({...o, prefix: rawPrefix}));
                } else if (basePrefix === 'is:') {
                    this.options = this.isOptions.filter(o => o.value.startsWith(val)).map(o => ({...o, prefix: rawPrefix}));
                } else if (basePrefix === 'since:' || basePrefix === 'until:') {
                    this.initDatePicker(val);
                    this.options = [{
                        isDatePicker: true,
                        prefix: rawPrefix,
                        val: val
                    }];
                } else if (basePrefix === 'from:' || basePrefix === 'to:') {
                    if (val.length > 0) {
                        this.fetchSuggestions(`/api/authors/search?q=${encodeURIComponent(val)}`, data => {
                            const authorsList = data.authors || [];
                            if (!this.knownAuthors) this.knownAuthors = new Set();
                            authorsList.forEach(a => this.knownAuthors.add(a.username.toLowerCase()));
                            return authorsList.map(a => ({
                                isAuthor: true,
                                prefix: rawPrefix,
                                value: a.username,
                                desc: a.display_name,
                                id: a.id,
                                query: val
                            }));
                        });
                    } else {
                        this.options = [];
                        this.showDropdown = false;
                    }
                } else if (basePrefix === 'tag:') {
                    this.fetchSuggestions(`/api/tags/autocomplete?q=${encodeURIComponent(val)}`, data => {
                        const tags = data.tags || [];
                        if (!this.knownTags) this.knownTags = new Set();
                        tags.forEach(t => this.knownTags.add(t.tag.toLowerCase()));
                        return tags.map(t => ({
                            isTag: true,
                            prefix: rawPrefix,
                            value: t.tag,
                            count: t.count.toLocaleString(),
                            query: val
                        }));
                    });
                } else {
                    this.options = [];
                    this.showDropdown = false;
                }
            } else {
                if (currentWord.startsWith('-')) {
                    const cleanWord = currentWord.substring(1).toLowerCase();
                    this.options = this.allFilters.filter(o => o.prefix.startsWith(cleanWord)).map(o => ({...o, prefix: '-' + o.prefix}));
                } else {
                    const lowerWord = currentWord.toLowerCase();
                    this.options = this.allFilters.filter(o => o.prefix.startsWith(lowerWord));
                }
            }
        },
        
        moveDown() {
            if (!this.showDropdown || this.options.length === 0) return;
            this.selectedIndex = (this.selectedIndex + 1) % this.options.length;
            this.scrollToSelected();
        },
        
        moveUp() {
            if (!this.showDropdown || this.options.length === 0) return;
            this.selectedIndex = (this.selectedIndex - 1 + this.options.length) % this.options.length;
            this.scrollToSelected();
        },

        scrollToSelected() {
            this.$nextTick(() => {
                const dropdown = this.$refs.dropdownMenu;
                if (!dropdown) return;
                const items = dropdown.querySelectorAll(':scope > div');
                if (items && items[this.selectedIndex]) {
                    items[this.selectedIndex].scrollIntoView({ block: 'nearest' });
                }
            });
        },
        
        selectOptionIndex(idx) {
            this.selectedIndex = idx;
            this.selectOption();
        },
        
        selectOption() {
            if (!this.showDropdown || this.options.length === 0) {
                // With nothing to pick, Tab finishes the capsule being edited.
                const current = this.caretFilterBox();
                if (current && current.inValue) this.leaveFilterBox(current.box);
                return;
            }

            const opt = this.options[this.selectedIndex];
            if (opt.isDatePicker) return; // Prevent enter key from closing if it's the date picker itself

            const textToCursor = this.searchQuery.substring(0, this.cursorPos);
            const tokenStart = this.currentSearchTokenStart(textToCursor);
            const textAfterCursor = this.searchQuery.substring(this.currentSearchTokenEnd(this.searchQuery, this.cursorPos, tokenStart));
            
            let valueToInsert = opt.value;
            if (valueToInsert && valueToInsert.includes(' ')) {
                valueToInsert = `"${valueToInsert}"`;
            }
            
            let insertion = opt.prefix + (valueToInsert ? valueToInsert + ' ' : '');
            
            const newTextToCursor = textToCursor.substring(0, tokenStart) + insertion;
            this.searchQuery = newTextToCursor + textAfterCursor;
            
            this.showDropdown = false;
            const refreshSuggestions = Boolean(opt.value || opt.prefix.endsWith(':'));

            const input = this.$refs.searchInput;
            if (input && input.isContentEditable) {
                // Rendered now, before the searchQuery watcher runs, so keys typed
                // right after picking an option land at the caret.
                input.focus();
                this.formatRichText(newTextToCursor + SEARCH_CARET + textAfterCursor);
                if (refreshSuggestions) this.handleInput();
                return;
            }

            this.$nextTick(() => {
                const input = this.$refs.searchInput;
                input.focus();
                input.setSelectionRange(newTextToCursor.length, newTextToCursor.length);

                if (refreshSuggestions) {
                    this.handleInput();
                }
            });
        },
        
        highlightMatch(text, query) {
            if (!text) return '';
            if (!query) return this.escapeHtml(text);
            const cleanQuery = query.startsWith('@') ? query.substring(1) : query;
            if (!cleanQuery) return this.escapeHtml(text);
            const idx = text.toLowerCase().indexOf(cleanQuery.toLowerCase());
            if (idx === -1) return this.escapeHtml(text);
            return this.escapeHtml(text.substring(0, idx))
                + '<b>' + this.escapeHtml(text.substring(idx, idx + cleanQuery.length)) + '</b>'
                + this.escapeHtml(text.substring(idx + cleanQuery.length));
        },

        highlightTagMatch(text, query) {
            if (!text) return '';
            const folded = text.toLowerCase();
            const ranges = (query || '').toLowerCase().split(/\s+/).filter(Boolean)
                .map(fragment => [folded.indexOf(fragment), fragment.length])
                .filter(([start]) => start !== -1)
                .map(([start, length]) => [start, start + length])
                .sort((a, b) => a[0] - b[0]);
            // Merge overlapping fragments before adding markup, and escape
            // every slice of the original label.
            const merged = [];
            for (const [start, end] of ranges) {
                const previous = merged[merged.length - 1];
                if (previous && start <= previous[1]) previous[1] = Math.max(previous[1], end);
                else merged.push([start, end]);
            }
            let html = '';
            let cursor = 0;
            for (const [start, end] of merged) {
                html += this.escapeHtml(text.substring(cursor, start))
                    + '<b>' + this.escapeHtml(text.substring(start, end)) + '</b>';
                cursor = end;
            }
            return html + this.escapeHtml(text.substring(cursor));
        },

        escapeHtml(unsafe) {
            return (unsafe || '').replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
        },

        validBooleanOperatorOffsets(text) {
            const tokens = [];
            const tokenRegex = /-?[\w_]+:(?:"[^"]*"|[^\s]+)|-?"[^"]*"|[^\s]+/g;
            let tokenMatch;
            while ((tokenMatch = tokenRegex.exec(text)) !== null) {
                tokens.push({ value: tokenMatch[0], offset: tokenMatch.index });
            }

            const hasFollowingClause = index => {
                let next = index + 1;
                if (tokens[next] && tokens[next].value === 'NOT') next += 1;
                return Boolean(tokens[next] && !['AND', 'OR', 'NOT'].includes(tokens[next].value));
            };
            const validOffsets = new Set();
            let hasClause = false;
            let expectingClause = false;
            let joinWithOr = false;
            let negateNext = false;

            tokens.forEach((token, index) => {
                if (token.value === 'OR') {
                    if (hasClause && !joinWithOr && !expectingClause && hasFollowingClause(index)) {
                        validOffsets.add(token.offset);
                        joinWithOr = true;
                        expectingClause = true;
                    }
                    return;
                }
                if (token.value === 'AND') {
                    if (hasClause && !joinWithOr && !expectingClause && hasFollowingClause(index)) {
                        validOffsets.add(token.offset);
                        expectingClause = true;
                    }
                    return;
                }
                if (token.value === 'NOT') {
                    if (!negateNext) {
                        negateNext = true;
                        expectingClause = true;
                    }
                    return;
                }

                hasClause = true;
                expectingClause = false;
                joinWithOr = false;
                negateNext = false;
            });
            return validOffsets;
        },

        // Renders query text. Text containing SEARCH_CARET also gets the caret there.
        formatRichText(text) {
            const input = this.$refs.searchInput;
            if (!input) return;

            if (text.length === 0) {
                input.innerHTML = '';
                return;
            }

            input.innerHTML = this.searchPartsHtml(this.parseSearchParts(text));
            if (text.includes(SEARCH_CARET)) this.placeMarkedCaret(input);
        },

        // Re-renders the editor from its own content, keeping the caret.
        refreshEditor() {
            const input = this.$refs.searchInput;
            if (input && input.isContentEditable && document.activeElement === input) {
                this.renderSearchParts(this.parseSearchParts(this.extractMarkedSearch(input)));
            } else {
                this.formatRichText(this.searchQuery || '');
            }
        },

        renderSearchParts(parts) {
            const input = this.$refs.searchInput;
            input.innerHTML = this.searchPartsHtml(parts);
            this.placeMarkedCaret(input);
            this.syncEditingState();
        },

        // Editor content as text: capsules wrapped in BOX_START/BOX_END and the
        // caret marked, ready for parseSearchParts.
        extractMarkedSearch(input, withCaret = true) {
            const sel = withCaret ? window.getSelection() : null;
            const anchor = sel && sel.rangeCount && input.contains(sel.anchorNode)
                ? { node: sel.anchorNode, offset: sel.anchorOffset } : null;
            const walk = node => {
                if (node.nodeType === 3) {
                    let data = node.data;
                    if (anchor && anchor.node === node) {
                        data = data.slice(0, anchor.offset) + SEARCH_CARET + data.slice(anchor.offset);
                    }
                    return data.replace(/\u00A0/g, ' ');
                }
                if (node.nodeType !== 1) return '';
                let text = '';
                node.childNodes.forEach((child, index) => {
                    if (anchor && anchor.node === node && anchor.offset === index) text += SEARCH_CARET;
                    text += walk(child);
                });
                if (anchor && anchor.node === node && anchor.offset === node.childNodes.length) text += SEARCH_CARET;
                return node !== input && node.classList.contains('filter-box') ? BOX_START + text + BOX_END : text;
            };
            return walk(input);
        },

        // The query the editor currently shows.
        editorSearchQuery(input) {
            if (!input.isContentEditable) return (input.textContent || '').replace(/\u00A0/g, ' ');
            return this.serializeSearchParts(this.parseSearchParts(this.extractMarkedSearch(input, false))).query;
        },

        // Splits query text (optionally marked) into text parts and capsules
        // ({ neg, key, value, caret }), where caret is an offset in value or -1.
        parseSearchParts(marked) {
            const keys = BOX_FILTER_KEYS.join('|');
            const raw = [];
            for (let index = 0; index < marked.length;) {
                if (marked[index] === BOX_START) {
                    let end = marked.indexOf(BOX_END, index);
                    if (end === -1) end = marked.length;
                    raw.push({ box: marked.slice(index + 1, end) });
                    index = end + 1;
                } else {
                    let end = marked.indexOf(BOX_START, index);
                    if (end === -1) end = marked.length;
                    raw.push({ text: marked.slice(index, end).split(BOX_END).join('') });
                    index = end;
                }
            }

            // A capsule whose key was edited away goes back to plain text.
            const merged = [];
            const keyPattern = new RegExp(`^(-?)(${keys}):`, 'i');
            for (const part of raw) {
                let box = null;
                if (part.box !== undefined) {
                    const plain = part.box.replace(SEARCH_CARET, '');
                    const match = plain.match(keyPattern);
                    if (match) {
                        const caretAt = part.box.indexOf(SEARCH_CARET);
                        const keyLength = match[0].length;
                        let rawValue = plain.slice(keyLength);
                        if (caretAt >= keyLength) {
                            rawValue = rawValue.slice(0, caretAt - keyLength) + SEARCH_CARET + rawValue.slice(caretAt - keyLength);
                        }
                        box = this.filterBoxPart(match[1], match[2], rawValue);
                        // A caret inside the key (from arrowing or clicking) stays there.
                        if (caretAt !== -1 && caretAt < keyLength) box.keyCaret = caretAt;
                    }
                }
                const last = merged[merged.length - 1];
                if (box) merged.push(box);
                else if (last && last.text !== undefined) last.text += part.text ?? part.box;
                else merged.push({ text: part.text ?? part.box });
            }

            // Typed, pasted, or loaded filters in plain text become capsules. A caret
            // right after the colon opens the new capsule for editing.
            const parts = [];
            const tokenPattern = new RegExp(`(^|\\s|${SEARCH_CARET})(-?)(${keys}):("[^"]*(?:"|$)|[^\\s"]*)`, 'gi');
            for (const part of merged) {
                if (part.text === undefined) {
                    parts.push(part);
                    continue;
                }
                let last = 0;
                let match;
                tokenPattern.lastIndex = 0;
                while ((match = tokenPattern.exec(part.text)) !== null) {
                    const start = match.index + match[1].length;
                    parts.push({ text: part.text.slice(last, start) });
                    parts.push(this.filterBoxPart(match[2], match[3], match[4]));
                    last = match.index + match[0].length;
                }
                parts.push({ text: part.text.slice(last) });
            }
            return this.joinGluedSearchText(parts.filter(part => part.text !== ''));
        },

        // A capsule from its raw value, which may hold quotes and the caret marker.
        filterBoxPart(neg, key, rawValue) {
            const caretAt = rawValue.indexOf(SEARCH_CARET);
            return {
                neg,
                key: key.toLowerCase(),
                value: rawValue.replace(SEARCH_CARET, '').replace(/"/g, ''),
                caret: caretAt === -1 ? -1 : rawValue.slice(0, caretAt).replace(/"/g, '').length,
                keyCaret: -1,
            };
        },

        // A capsule's key and value as marked text, keeping its caret.
        filterBoxText(part) {
            let key = `${part.neg}${part.key}:`;
            if (part.keyCaret !== -1) key = key.slice(0, part.keyCaret) + SEARCH_CARET + key.slice(part.keyCaret);
            const value = part.caret === -1
                ? part.value
                : part.value.slice(0, part.caret) + SEARCH_CARET + part.value.slice(part.caret);
            return { key, value };
        },

        // Capsules follow the same rules as plain query text. Text typed directly
        // against the end of a capsule is part of its value (so a caret on that edge
        // belongs to the capsule), and text directly against its start means it is
        // no longer a filter.
        joinGluedSearchText(parts) {
            for (let index = 0; index < parts.length; index += 1) {
                const box = parts[index];
                if (box.text !== undefined) continue;
                const next = parts[index + 1];
                const glued = next && next.text !== undefined ? next.text.match(/^\S+/) : null;
                if (glued) {
                    const caretAt = glued[0].indexOf(SEARCH_CARET);
                    if (caretAt !== -1) {
                        box.caret = box.value.length + glued[0].slice(0, caretAt).replace(/"/g, '').length;
                    }
                    box.value += glued[0].replace(SEARCH_CARET, '').replace(/"/g, '');
                    next.text = next.text.slice(glued[0].length);
                }
                const prev = parts[index - 1];
                if (prev && prev.text !== undefined && /\S$/.test(prev.text.replace(SEARCH_CARET, ''))) {
                    const { key, value } = this.filterBoxText(box);
                    prev.text += key + value;
                    parts.splice(index, 1);
                    index -= 1;
                }
            }
            return parts.reduce((joined, part) => {
                const last = joined[joined.length - 1];
                if (part.text !== undefined && last && last.text !== undefined) last.text += part.text;
                else if (part.text !== '') joined.push(part);
                return joined;
            }, []);
        },

        // A capsule's query token: key:word or key:"several words". Values are
        // trimmed here, so spaces still being typed never reach the server.
        filterBoxToken(part) {
            const prefix = `${part.neg}${part.key}:`;
            const value = part.value.replace(/\s+/g, ' ').trim();
            const quoted = value.includes(' ');
            let caret = part.keyCaret ?? -1;
            if (part.caret >= 0) {
                const before = part.value.slice(0, part.caret).replace(/\s+/g, ' ').trimStart();
                caret = prefix.length + (quoted ? 1 : 0) + Math.min(before.length, value.length);
            }
            return { token: prefix + (quoted ? `"${value}"` : value), caret };
        },

        // Query text for parts, with the caret's offset in it (-1 if none) and
        // where each part starts.
        serializeSearchParts(parts) {
            let query = '';
            let caret = -1;
            const starts = [];
            parts.forEach(part => {
                starts.push(query.length);
                if (part.text !== undefined) {
                    const caretAt = part.text.indexOf(SEARCH_CARET);
                    if (caretAt !== -1) caret = query.length + caretAt;
                    query += part.text.replace(SEARCH_CARET, '');
                } else {
                    const token = this.filterBoxToken(part);
                    if (token.caret !== -1) caret = query.length + token.caret;
                    query += token.token;
                }
            });
            return { query, caret, starts };
        },

        searchPartsHtml(parts) {
            const { query, starts } = this.serializeSearchParts(parts);
            const validBooleanOffsets = this.validBooleanOperatorOffsets(query);
            return parts.map((part, index) => (
                part.text !== undefined
                    ? this.plainSearchHtml(part.text, starts[index], validBooleanOffsets)
                    : this.filterBoxHtml(part)
            )).join('');
        },

        // A description: or tag: capsule. Its value is plain text, so the caret moves
        // through it like any other text. Quotes are left out of the display.
        filterBoxHtml(part) {
            const { key, value } = this.filterBoxText(part);
            let classes = 'filter-box';
            if (part.neg) classes += ' negative';
            if (!part.value.trim()) {
                classes += ' is-empty';
            } else if (part.key === 'tag' && !this.isKnownTag(part.value)) {
                classes += ' is-unknown';
                this.validateTagAsync(part.value.trim());
            }
            // An empty value has no text node, so its caret goes right after the colon.
            const keyCaret = part.caret !== -1 && !part.value ? SEARCH_CARET : '';
            return `<span class="${classes}"><span class="capsule-key">${key}${keyCaret}</span>`
                + `<span class="capsule-value">${part.value ? this.escapeHtml(value).replace(/ /g, '&nbsp;') : ''}</span></span>`;
        },

        // Plain query text starting at `start` in the full query, with capsules for
        // the other filters and valid boolean operators.
        plainSearchHtml(text, start, validBooleanOffsets) {
            let html = '';

            // Regex to parse operators vs normal text, preserving quotes and whitespace
            const regex = /(\s+)|(?:(-?(?:from|to|has|is|filter|since|until|url):)("[^"]*(?:"|$)|[^\s]*))|([^\s]+)/gi;
            let match;

            while ((match = regex.exec(text)) !== null) {
                if (match[1]) {
                    // whitespace (preserve spaces for caret using non-breaking space)
                    html += match[1].replace(/ /g, '&nbsp;');
                } else if (match[2]) {
                    const prefix = match[2];
                    let value = match[3] || '';
                    // A caret at the end of the value stays after the capsule.
                    let trailingCaret = '';
                    if (value.endsWith(SEARCH_CARET)) {
                        value = value.slice(0, -1);
                        trailingCaret = SEARCH_CARET;
                    }

                    if (value) {
                        let isValid = true;
                        let cleanValue = value.replace(SEARCH_CARET, '');
                        if (cleanValue.startsWith('"') && cleanValue.endsWith('"') && cleanValue.length >= 2) {
                            cleanValue = cleanValue.substring(1, cleanValue.length - 1);
                        }

                        if (prefix.toLowerCase().endsWith('has:')) {
                            isValid = ['media', 'image', 'video', 'links', 'article'].includes(cleanValue.toLowerCase());
                        } else if (prefix.toLowerCase().endsWith('is:')) {
                            isValid = this.isOptions.some(option => option.value === cleanValue.toLowerCase());
                        } else if (prefix.toLowerCase().endsWith('filter:')) {
                            isValid = ['articles', 'media', 'images', 'videos', 'native_video', 'links', 'replies', 'quote', 'nativeretweets', 'self_threads', 'threads', 'verified'].includes(cleanValue.toLowerCase());
                        } else if (prefix.toLowerCase().endsWith('from:') || prefix.toLowerCase().endsWith('to:')) {
                            if (this.knownAuthors && this.knownAuthors.has(cleanValue.toLowerCase())) {
                                isValid = true;
                            } else {
                                isValid = false;
                                if (cleanValue.length > 0) {
                                    this.validateAuthorAsync(cleanValue);
                                }
                            }
                        } else if (prefix.toLowerCase().endsWith('since:') || prefix.toLowerCase().endsWith('until:')) {
                            isValid = /^\d{4}-\d{2}-\d{2}$/.test(cleanValue);
                        }
                        // url, etc are dynamic, so assume valid if there is a value

                        const isNegative = prefix.startsWith('-');
                        const negativeClass = isNegative ? ' negative-capsule' : '';

                        if (isValid) {
                            if (value.startsWith('"') && value.endsWith('"') && value.length >= 2) {
                                const innerValue = value.substring(1, value.length - 1);
                                html += `<span class="search-capsule valid-capsule${negativeClass}"><span class="capsule-key">${prefix}</span><span class="hidden-quote">"</span><span class="capsule-value">${this.escapeHtml(innerValue).replace(/ /g, '&nbsp;')}</span><span class="hidden-quote">"</span></span>`;
                            } else {
                                html += `<span class="search-capsule valid-capsule${negativeClass}"><span class="capsule-key">${prefix}</span><span class="capsule-value">${this.escapeHtml(value)}</span></span>`;
                            }
                        } else {
                            html += `<span class="search-capsule invalid-capsule${negativeClass}"><span class="capsule-key">${prefix}</span></span><span class="capsule-invalid-value">${this.escapeHtml(value)}</span>`;
                        }
                    } else {
                        const isNegative = prefix.startsWith('-');
                        const negativeClass = isNegative ? ' negative-capsule' : '';
                        html += `<span class="search-capsule incomplete-capsule${negativeClass}"><span class="capsule-key">${prefix}</span></span>`;
                    }
                    html += trailingCaret;
                } else if (match[4]) {
                    const caretBefore = text.slice(0, match.index).includes(SEARCH_CARET) ? 1 : 0;
                    if (validBooleanOffsets.has(start + match.index - caretBefore)) {
                        html += `<span class="search-capsule boolean-capsule">${this.escapeHtml(match[4])}</span>`;
                    } else {
                        html += this.escapeHtml(match[4]);
                    }
                }
            }
            return html;
        },

        // Moves the caret to the SEARCH_CARET marker left in the rendered editor.
        placeMarkedCaret(input) {
            const walker = document.createTreeWalker(input, NodeFilter.SHOW_TEXT);
            let node;
            while ((node = walker.nextNode())) {
                const index = node.data.indexOf(SEARCH_CARET);
                if (index === -1) continue;
                node.data = node.data.slice(0, index) + node.data.slice(index + 1);
                const range = document.createRange();
                range.setStart(node, index);
                range.collapse(true);
                const sel = window.getSelection();
                sel.removeAllRanges();
                sel.addRange(range);
                return;
            }
        },

        // The capsule holding the caret, whether the caret is in its value (after
        // the colon), and whether it is at the end of the value.
        caretFilterBox() {
            const input = this.$refs.searchInput;
            if (!input || !input.isContentEditable) return null;
            const sel = window.getSelection();
            if (!sel || !sel.rangeCount || !input.contains(sel.anchorNode)) return null;
            const element = sel.anchorNode.nodeType === 1 ? sel.anchorNode : sel.anchorNode.parentElement;
            const box = element && element.closest('.filter-box');
            if (!box || !input.contains(box)) return null;
            const before = document.createRange();
            before.selectNodeContents(box);
            before.setEnd(sel.anchorNode, sel.anchorOffset);
            const keyLength = (box.querySelector('.capsule-key')?.textContent || '').length;
            const offset = before.toString().length - keyLength;
            const valueLength = (box.querySelector('.capsule-value')?.textContent || '').length;
            return { box, inValue: offset >= 0, atEnd: sel.isCollapsed && offset === valueLength };
        },

        // Finishing a capsule moves the caret past the space after it, adding one
        // if needed. An empty capsule stays, like an unfinished from:.
        leaveFilterBox(box) {
            let next = box.nextSibling;
            if (!next || next.nodeType !== 3 || !/^\s/.test(next.data)) {
                next = document.createTextNode('\u00A0');
                box.after(next);
            }
            const range = document.createRange();
            range.setStart(next, 1);
            range.collapse(true);
            const sel = window.getSelection();
            sel.removeAllRanges();
            sel.addRange(range);
            this.handleInput();
        },

        // A capsule shows its editing style while the caret is in its value.
        syncEditingState() {
            const input = this.$refs.searchInput;
            if (!input || !input.isContentEditable) return;
            const current = document.activeElement === input ? this.caretFilterBox() : null;
            input.querySelectorAll('.filter-box').forEach(box => {
                box.classList.toggle('is-editing', Boolean(current && current.inValue && box === current.box));
            });
        },

        handleEscape() {
            const current = this.caretFilterBox();
            if (current && current.inValue) this.leaveFilterBox(current.box);
            else this.showDropdown = false;
        },

        // At the very end of the search, → has nowhere to go, so it finishes the capsule.
        handleArrowRight(event) {
            const current = this.caretFilterBox();
            if (!current || !current.inValue || !current.atEnd) return;
            const next = current.box.nextSibling;
            if (next && next.textContent.replace(/\u00A0/g, ' ').length > 0) return;
            event.preventDefault();
            this.leaveFilterBox(current.box);
        },

        // Quotes would end a capsule's value early, so they are not typed into one.
        handleBeforeInput(event) {
            if (!event.data || !event.data.includes('"')) return;
            const current = this.caretFilterBox();
            if (!current || !current.inValue) return;
            event.preventDefault();
            const text = event.data.replace(/"/g, '');
            if (text) document.execCommand('insertText', false, text);
        },

        // Copying gives query text, so capsules with spaces come out quoted.
        handleCopy(event) {
            const sel = window.getSelection();
            if (!sel.rangeCount || sel.isCollapsed) return;
            event.preventDefault();
            event.clipboardData.setData('text/plain', this.fragmentSearchQuery(sel.getRangeAt(0).cloneContents()).trim());
            if (event.type === 'cut') document.execCommand('delete');
        },

        fragmentSearchQuery(root) {
            let text = '';
            root.childNodes.forEach(node => {
                if (node.nodeType === 3) {
                    text += node.data.replace(/\u00A0/g, ' ');
                } else if (node.nodeType === 1 && node.classList.contains('filter-box') && node.querySelector('.capsule-key')) {
                    text += this.parseSearchParts(node.textContent.replace(/\u00A0/g, ' '))
                        .map(part => (part.text !== undefined ? part.text : this.filterBoxToken(part).token))
                        .join('');
                } else if (node.nodeType === 1) {
                    text += this.fragmentSearchQuery(node);
                }
            });
            return text;
        },

        // Tags are known from the loaded tag list or from tag suggestions.
        isKnownTag(value) {
            const tag = value.trim().toLowerCase();
            if (this.knownTags && this.knownTags.has(tag)) return true;
            return Array.isArray(this.globalTags) && this.globalTags.some(t => t.tag.toLowerCase() === tag);
        },

        validateTagAsync(tag) {
            if (!this.knownTags) this.knownTags = new Set();
            if (!this.pendingTagValidations) this.pendingTagValidations = new Set();

            const lower = tag.toLowerCase();
            if (this.pendingTagValidations.has(lower)) return;
            this.pendingTagValidations.add(lower);

            fetch(`/api/tags/autocomplete?q=${encodeURIComponent(tag)}`)
                .then(res => res.ok ? res.json() : null)
                .then(data => {
                    const tags = (data && data.tags) || [];
                    tags.forEach(t => this.knownTags.add(t.tag.toLowerCase()));
                    if (this.knownTags.has(lower)) this.refreshEditor();
                })
                .catch(() => {})
                .finally(() => {
                    this.pendingTagValidations.delete(lower);
                });
        },

        validateAuthorAsync(username) {
            if (!this.knownAuthors) this.knownAuthors = new Set();
            if (!this.pendingAuthorValidations) this.pendingAuthorValidations = new Set();

            const lower = username.toLowerCase();
            if (this.pendingAuthorValidations.has(lower)) return;
            this.pendingAuthorValidations.add(lower);

            fetch(`/api/authors/search?q=${encodeURIComponent(username)}`)
                .then(res => res.ok ? res.json() : null)
                .then(data => {
                    let found = false;
                    if (data && data.authors) {
                        data.authors.forEach(a => {
                            this.knownAuthors.add(a.username.toLowerCase());
                            if (a.username.toLowerCase() === lower) found = true;
                        });
                    }
                    if (found) {
                        this.refreshEditor();
                    }
                })
                .catch(() => {})
                .finally(() => {
                    this.pendingAuthorValidations.delete(lower);
                });
        },

        initDatePicker(val) {
            let d = new Date();
            if (val && /^\d{4}-\d{2}-\d{2}$/.test(val)) {
                const parsed = new Date(val + 'T00:00:00');
                if (!isNaN(parsed.getTime())) d = parsed;
            } else if (val) {
                const parts = val.split('-');
                if (parts[0] && parts[0].length === 4) {
                    const y = parseInt(parts[0], 10);
                    if (!isNaN(y)) d.setFullYear(y);
                    if (parts[1] && parts[1].length === 2) {
                        const m = parseInt(parts[1], 10) - 1;
                        if (!isNaN(m) && m >= 0 && m <= 11) {
                            d.setMonth(m);
                        }
                    }
                }
            }
            this.dpYear = d.getFullYear();
            this.dpMonth = d.getMonth();
        },
        
        dpPrevMonth() {
            if (this.dpMonth === 0) {
                this.dpMonth = 11;
                this.dpYear--;
            } else {
                this.dpMonth--;
            }
        },
        
        dpNextMonth() {
            if (this.dpMonth === 11) {
                this.dpMonth = 0;
                this.dpYear++;
            } else {
                this.dpMonth++;
            }
        },
        
        dpGetDays() {
            const days = [];
            const firstDay = new Date(this.dpYear, this.dpMonth, 1).getDay();
            const daysInMonth = new Date(this.dpYear, this.dpMonth + 1, 0).getDate();
            const prevMonthDays = new Date(this.dpYear, this.dpMonth, 0).getDate();
            const now = new Date();
            const todayStr = this.formatDateStr(now.getFullYear(), now.getMonth() + 1, now.getDate());
            
            // Previous month
            for (let i = firstDay - 1; i >= 0; i--) {
                const dateStr = this.formatDateStr(this.dpMonth === 0 ? this.dpYear - 1 : this.dpYear, this.dpMonth === 0 ? 12 : this.dpMonth, prevMonthDays - i);
                days.push({ 
                    day: prevMonthDays - i, 
                    isCurrentMonth: false, 
                    dateStr: dateStr,
                    isToday: dateStr === todayStr
                });
            }
            // Current month
            for (let i = 1; i <= daysInMonth; i++) {
                const dateStr = this.formatDateStr(this.dpYear, this.dpMonth + 1, i);
                days.push({ 
                    day: i, 
                    isCurrentMonth: true, 
                    dateStr: dateStr,
                    isToday: dateStr === todayStr
                });
            }
            // Next month
            const remaining = 42 - days.length;
            for (let i = 1; i <= remaining; i++) {
                const dateStr = this.formatDateStr(this.dpMonth === 11 ? this.dpYear + 1 : this.dpYear, this.dpMonth === 11 ? 1 : this.dpMonth + 2, i);
                days.push({ 
                    day: i, 
                    isCurrentMonth: false, 
                    dateStr: dateStr,
                    isToday: dateStr === todayStr
                });
            }
            return days;
        },
        
        formatDateStr(y, m, d) {
            return `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
        },
        
        dpSelectDate(dateStr, opt) {
            opt.value = dateStr;
            this.selectedIndex = 0;
            const originalIsDatePicker = opt.isDatePicker;
            opt.isDatePicker = false; // Temporarily trick selectOption into submitting
            this.selectOption();
            opt.isDatePicker = originalIsDatePicker;
        }
    };
}
