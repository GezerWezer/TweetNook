/**
 * Search autocomplete component for the TweetNook Web UI.
 */

function searchAutocomplete() {
    return {
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
                if (this.$refs.searchInput && !this.isComposing) {
                    const currentText = this.$refs.searchInput.textContent.replace(/\u00A0/g, ' ');
                    if (currentText !== (val || '')) {
                        this.formatRichText(val || '');
                    }
                }
            });
            this.$nextTick(() => {
                if (this.$refs.searchInput) {
                    this.formatRichText(this.searchQuery || '');
                }
            });
        },
        
        allFilters: [
            { prefix: 'from:', desc: 'Sent from a specific user' },
            { prefix: 'to:', desc: 'Replying to a specific user' },
            { prefix: 'has:', desc: 'Includes specific attached content' },
            { prefix: 'is:', desc: 'Matches a tweet type or state' },
            { prefix: 'since:', desc: 'After a specific date (YYYY-MM-DD)' },
            { prefix: 'until:', desc: 'Before a specific date (YYYY-MM-DD)' },
            { prefix: 'url:', desc: 'Contains a specific URL' },
            { prefix: 'tag:', desc: 'Matches media tag' }
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
            { prefix: 'is:', value: 'resurrected', desc: 'Previously unavailable and recovered' }
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
        
        handleInput() {
            const input = this.$refs.searchInput;
            
            // If it's the rich input, we must sync the contenteditable text to Alpine state
            if (input.isContentEditable) {
                // Get caret offset relative to textContent
                let caretOffset = 0;
                const sel = window.getSelection();
                if (sel.rangeCount > 0) {
                    const range = sel.getRangeAt(0);
                    const preCaretRange = range.cloneRange();
                    preCaretRange.selectNodeContents(input);
                    preCaretRange.setEnd(range.endContainer, range.endOffset);
                    caretOffset = preCaretRange.toString().length;
                }
                
                const text = input.textContent.replace(/\u00A0/g, ' '); // Normalize non-breaking spaces
                this.searchQuery = text;
                this.cursorPos = caretOffset;
                
                if (!this.isComposing) {
                    this.formatRichText(text);
                    this.restoreCaret(caretOffset);
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
                        fetch(`/api/authors/search?q=${encodeURIComponent(val)}`)
                            .then(res => res.ok ? res.json() : { authors: [] })
                            .then(data => {
                                const authorsList = data && data.authors ? data.authors : [];
                                if (!this.knownAuthors) this.knownAuthors = new Set();
                                authorsList.forEach(a => this.knownAuthors.add(a.username.toLowerCase()));
                                
                                this.options = authorsList.map(a => ({
                                    isAuthor: true,
                                    prefix: rawPrefix,
                                    value: a.username,
                                    desc: a.display_name,
                                    id: a.id,
                                    query: val
                                }));
                                if (this.options.length === 0) {
                                    this.showDropdown = false;
                                }
                            })
                            .catch(err => {
                                console.error(err);
                                this.options = [];
                                this.showDropdown = false;
                            });
                    } else {
                        this.options = [];
                        this.showDropdown = false;
                    }
                } else if (basePrefix === 'tag:') {
                    fetch(`/api/tags/autocomplete?q=${encodeURIComponent(val)}`)
                        .then(res => res.ok ? res.json() : { tags: [] })
                        .then(data => {
                            const tagsList = data && data.tags ? data.tags : [];
                            this.options = tagsList.map(t => ({
                                isTag: true,
                                prefix: rawPrefix,
                                value: t.tag,
                                count: t.count.toLocaleString(),
                                query: val
                            }));
                            if (this.options.length === 0) {
                                this.showDropdown = false;
                            }
                        })
                        .catch(err => {
                            console.error(err);
                            this.options = [];
                            this.showDropdown = false;
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
            if (!this.showDropdown || this.options.length === 0) return;
            
            const opt = this.options[this.selectedIndex];
            if (opt.isDatePicker) return; // Prevent enter key from closing if it's the date picker itself
            
            const textToCursor = this.searchQuery.substring(0, this.cursorPos);
            const textAfterCursor = this.searchQuery.substring(this.cursorPos);
            const tokenStart = this.currentSearchTokenStart(textToCursor);
            
            let valueToInsert = opt.value;
            if (valueToInsert && valueToInsert.includes(' ')) {
                valueToInsert = `"${valueToInsert}"`;
            }
            
            let insertion = opt.prefix + (valueToInsert ? valueToInsert + ' ' : '');
            
            const newTextToCursor = textToCursor.substring(0, tokenStart) + insertion;
            this.searchQuery = newTextToCursor + textAfterCursor;
            
            this.showDropdown = false;
            
            this.$nextTick(() => {
                const input = this.$refs.searchInput;
                input.focus();
                
                if (input.isContentEditable) {
                    this.formatRichText(this.searchQuery);
                    this.restoreCaret(newTextToCursor.length);
                } else {
                    input.setSelectionRange(newTextToCursor.length, newTextToCursor.length);
                }
                
                if (opt.value || opt.prefix.endsWith(':')) {
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

        formatRichText(text) {
            const input = this.$refs.searchInput;
            if (!input) return;
            
            if (text.length === 0) {
                input.innerHTML = '';
                return;
            }

            let html = '';
            const validBooleanOffsets = this.validBooleanOperatorOffsets(text);
            
            // Regex to parse operators vs normal text, preserving quotes and whitespace
            const regex = /(\s+)|(?:(-?(?:from|to|has|is|filter|since|until|url|tag):)("[^"]*(?:"|$)|[^\s]*))|([^\s]+)/gi;
            let match;
            
            while ((match = regex.exec(text)) !== null) {
                if (match[1]) {
                    // whitespace (preserve spaces for caret using non-breaking space)
                    html += match[1].replace(/ /g, '&nbsp;');
                } else if (match[2]) {
                    const prefix = match[2];
                    const value = match[3] || '';
                    
                    if (value) {
                        let isValid = true;
                        let cleanValue = value;
                        if (value.startsWith('"') && value.endsWith('"') && value.length >= 2) {
                            cleanValue = value.substring(1, value.length - 1);
                        }

                        if (prefix.toLowerCase().endsWith('has:')) {
                            isValid = ['media', 'image', 'video', 'links', 'article'].includes(cleanValue.toLowerCase());
                        } else if (prefix.toLowerCase().endsWith('is:')) {
                            isValid = ['reply', 'quote', 'retweet', 'thread', 'verified', 'resurrected'].includes(cleanValue.toLowerCase());
                        } else if (prefix.toLowerCase().endsWith('filter:')) {
                            isValid = ['articles', 'media', 'images', 'videos', 'native_video', 'links', 'replies', 'quote', 'nativeretweets', 'self_threads', 'threads', 'verified'].includes(cleanValue.toLowerCase());
                        } else if (prefix.toLowerCase().endsWith('tag:')) {
                            if (Array.isArray(this.globalTags)) {
                                isValid = this.globalTags.some(t => t.tag.toLowerCase() === cleanValue.toLowerCase());
                            }
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
                            if (value.startsWith('"') && value.endsWith('"')) {
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
                } else if (match[4]) {
                    if (validBooleanOffsets.has(match.index)) {
                        html += `<span class="search-capsule boolean-capsule">${this.escapeHtml(match[4])}</span>`;
                    } else {
                        html += this.escapeHtml(match[4]);
                    }
                }
            }
            
            input.innerHTML = html;
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
                        this.formatRichText(this.searchQuery);
                    }
                })
                .catch(() => {})
                .finally(() => {
                    this.pendingAuthorValidations.delete(lower);
                });
        },

        restoreCaret(targetOffset) {
            const input = this.$refs.searchInput;
            if (!input) return;
            
            const sel = window.getSelection();
            const range = document.createRange();
            
            let currentOffset = 0;
            let nodeFound = false;
            
            function traverseNodes(node) {
                if (nodeFound) return;
                
                if (node.nodeType === 3) { // Text node
                    const nextOffset = currentOffset + node.length;
                    if (targetOffset <= nextOffset) {
                        range.setStart(node, targetOffset - currentOffset);
                        range.collapse(true);
                        nodeFound = true;
                    } else {
                        currentOffset = nextOffset;
                    }
                } else {
                    for (let i = 0; i < node.childNodes.length; i++) {
                        traverseNodes(node.childNodes[i]);
                    }
                }
            }
            
            traverseNodes(input);
            
            if (!nodeFound) {
                // If target offset is beyond content, focus at end
                range.selectNodeContents(input);
                range.collapse(false);
            }
            
            sel.removeAllRanges();
            sel.addRange(range);
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
