'use strict';

const assert = require('node:assert/strict');
const { pageHtml } = require('./page_scripts.cjs');
const {
    browserContext,
    immediateComponent,
    loadScripts,
    test,
    run,
} = require('./web_harness.cjs');

test('autocomplete formats valid, invalid, negative, quoted, and escaped capsules', () => {
    const context = browserContext();
    const { searchAutocomplete } = loadScripts(
        context,
        ['autocomplete.js'],
        '({searchAutocomplete})',
    );
    const input = { innerHTML: '' };
    const component = immediateComponent(searchAutocomplete());
    component.$refs.searchInput = input;
    component.globalTags = [{ tag: 'Night Sky' }];
    component.knownAuthors = new Set(['alice']);

    component.formatRichText(
        'from:alice -has:video url:"a b" tag:"Night Sky" filter:nope <img src=x>',
    );

    assert.match(input.innerHTML, /valid-capsule/);
    assert.match(input.innerHTML, /negative-capsule/);
    assert.match(input.innerHTML, /hidden-quote/);
    assert.match(input.innerHTML, /<span class="filter-box"><span class="capsule-key">tag:<\/span><span class="capsule-value">Night&nbsp;Sky<\/span><\/span>/);
    assert.match(input.innerHTML, /invalid-capsule/);
    assert.match(input.innerHTML, /&lt;img/);
    assert.doesNotMatch(input.innerHTML, /<img src=x>/);
});

test('autocomplete formats hidden search filters as valid capsules', () => {
    const context = browserContext();
    const { searchAutocomplete } = loadScripts(
        context,
        ['autocomplete.js'],
        '({searchAutocomplete})',
    );
    const input = { innerHTML: '' };
    const component = immediateComponent(searchAutocomplete());
    component.$refs.searchInput = input;

    for (const query of ['is:hidden', '-is:hidden', 'is:"hidden"', 'IS:HIDDEN']) {
        component.formatRichText(query);
        assert.match(input.innerHTML, /search-capsule valid-capsule/, query);
        assert.match(input.innerHTML, /capsule-value">(?:hidden|HIDDEN)<\/span>/, query);
        assert.doesNotMatch(input.innerHTML, /invalid-capsule|capsule-invalid-value/, query);
        assert.equal(/negative-capsule/.test(input.innerHTML), query.startsWith('-'), query);
    }

    component.formatRichText('is:hidde');
    assert.match(input.innerHTML, /invalid-capsule/);
});

test('autocomplete renders only valid uppercase boolean operators as neutral capsules', () => {
    const context = browserContext();
    const { searchAutocomplete } = loadScripts(
        context,
        ['autocomplete.js'],
        '({searchAutocomplete})',
    );
    const input = { innerHTML: '' };
    const component = immediateComponent(searchAutocomplete());
    component.$refs.searchInput = input;

    component.formatRichText('cats AND dogs OR birds and fish OR');

    assert.equal((input.innerHTML.match(/boolean-capsule/g) || []).length, 2);
    assert.match(input.innerHTML, /boolean-capsule">AND<\/span>/);
    assert.match(input.innerHTML, /boolean-capsule">OR<\/span>/);
    assert.match(input.innerHTML, /birds&nbsp;and&nbsp;fish&nbsp;OR$/);
});

test('autocomplete highlighting escapes API-provided labels', () => {
    const context = browserContext();
    const { searchAutocomplete } = loadScripts(
        context,
        ['autocomplete.js'],
        '({searchAutocomplete})',
    );
    const component = searchAutocomplete();
    const highlighted = component.highlightMatch('<img onerror=x> Alice', 'ali');
    assert.equal(highlighted, '&lt;img onerror=x&gt; <b>Ali</b>ce');
    assert.equal(component.highlightMatch('<script>', ''), '&lt;script&gt;');
});

test('tag autocomplete highlights partial fragments and escapes labels', () => {
    const { searchAutocomplete } = loadScripts(
        browserContext(), ['autocomplete.js'], '({searchAutocomplete})',
    );
    const component = searchAutocomplete();
    assert.equal(component.highlightTagMatch('REM (Deadlock)', 'rem D'), '<b>REM</b> (<b>D</b>eadlock)');
    assert.equal(component.highlightTagMatch('REM (Deadlock)', ' D\t rem '), '<b>REM</b> (<b>D</b>eadlock)');
    assert.equal(component.highlightTagMatch('REM REM', 'em re'), '<b>REM</b> REM');
    assert.equal(component.highlightTagMatch('<img> 100%_ [tag]', '%_ ['), '&lt;img&gt; 100<b>%_</b> <b>[</b>tag]');
    assert.equal(component.highlightTagMatch('<script>', ' \t '), '&lt;script&gt;');
    assert.equal(component.highlightTagMatch('<img>', 'missing'), '&lt;img&gt;');
    const html = pageHtml();
    assert.ok(html.includes('x-html="highlightTagMatch(opt.value, opt.query)"'));
});

test('autocomplete requests partial tag fragments together and selects the complete tag', async () => {
    const context = browserContext();
    const calls = [];
    context.fetch = async url => {
        calls.push(url);
        return { ok: true, json: async () => ({ tags: [{ tag: 'REM (Deadlock)', count: 2 }] }) };
    };
    const { searchAutocomplete, SEARCH_CARET, BOX_START, BOX_END } = loadScripts(
        context, ['autocomplete.js'], '({searchAutocomplete, SEARCH_CARET, BOX_START, BOX_END})',
    );
    const component = immediateComponent(searchAutocomplete());
    const typed = component.serializeSearchParts(component.parseSearchParts(
        `${BOX_START}tag:rem D${SEARCH_CARET}${BOX_END}`,
    ));
    component.searchQuery = typed.query;
    component.$refs.searchInput = {
        isContentEditable: false, selectionStart: typed.caret,
        focus() {}, setSelectionRange() {},
    };
    component.handleInput();
    await new Promise(resolve => setTimeout(resolve, 150));
    assert.deepEqual(calls, ['/api/tags/autocomplete?q=rem%20d']);
    assert.equal(component.options[0].value, 'REM (Deadlock)');
    assert.equal(component.options[0].query, 'rem d');
    component.handleInput = () => {};
    component.selectOption(0);
    assert.equal(component.searchQuery, 'tag:"REM (Deadlock)" ');
});

test('autocomplete separates attached content from post types and states', () => {
    const context = browserContext();
    const { searchAutocomplete } = loadScripts(
        context,
        ['autocomplete.js'],
        '({searchAutocomplete})',
    );
    const component = searchAutocomplete();
    assert.ok(component.hasOptions.some(option => option.value === 'article'));
    assert.ok(component.hasOptions.some(option => option.value === 'note'));
    assert.ok(!component.hasOptions.some(option => option.value === 'cn'));
    assert.ok(component.isOptions.some(option => option.value === 'resurrected'));
    assert.ok(component.isOptions.some(option => option.value === 'uncaptured'));
    const options = [...component.allFilters, ...component.hasOptions, ...component.isOptions];
    for (const [value, glyph] of [['note', 'community-notes-outline'], ['uncaptured', 'square-dashed']]) {
        assert.equal(options.find(option => option.value === value).glyph, glyph);
        assert.equal(options.filter(option => option.glyph === glyph).length, 1);
    }
    assert.ok(!component.plainSearchHtml('has:note is:uncaptured', 0, new Set()).includes('invalid'));
    assert.ok(component.allFilters.some(option => option.prefix === 'is:'));
    assert.ok(!component.allFilters.some(option => option.prefix === 'filter:'));
});

test('autocomplete describes excluded filters and values by what they hide', () => {
    const { searchAutocomplete } = loadScripts(
        browserContext(), ['autocomplete.js'], '({searchAutocomplete})',
    );
    const component = immediateComponent(searchAutocomplete());
    component.$refs.searchInput = { isContentEditable: false, selectionStart: 0 };
    const suggest = query => {
        component.searchQuery = query;
        component.$refs.searchInput.selectionStart = query.length;
        component.handleInput();
        return component.options;
    };

    const plain = suggest('fr')[0];
    assert.equal(plain.desc, 'Sent from a specific user');
    assert.ok(!plain.negated);
    const excluded = suggest('-')[0];
    assert.equal(excluded.prefix, '-from:');
    assert.equal(excluded.desc, 'Hide tweets from a specific user');
    assert.ok(excluded.negated);
    assert.equal(suggest('-sin')[0].desc, 'Hide tweets on or after a date (YYYY-MM-DD)');

    const value = suggest('-is:rep')[0];
    assert.equal(value.prefix, '-is:');
    assert.equal(value.desc, 'Hide replies to other tweets');
    assert.equal(suggest('is:rep')[0].desc, 'Replies to other tweets');
    for (const option of [...component.allFilters, ...component.hasOptions, ...component.isOptions]) {
        assert.ok(option.excludeDesc, option.prefix + (option.value || ''));
        assert.match(component.suggestionGlyph(option.glyph), /^<svg [^>]*viewBox=/, option.glyph);
    }
});

test('autocomplete keeps the menu open with a note when a lookup finds nothing', async () => {
    const context = browserContext();
    context.fetch = async () => ({ ok: true, json: async () => ({ authors: [], tags: [] }) });
    const { searchAutocomplete } = loadScripts(context, ['autocomplete.js'], '({searchAutocomplete})');
    const component = immediateComponent(searchAutocomplete());
    component.$refs.searchInput = { isContentEditable: false, selectionStart: 0 };

    component.searchQuery = 'from:zzq';
    component.$refs.searchInput.selectionStart = component.searchQuery.length;
    component.handleInput();
    await new Promise(resolve => setTimeout(resolve, 150));
    assert.equal(component.options.length, 0);
    assert.equal(component.showDropdown, true);
    assert.deepEqual({ ...component.emptyNote }, { noun: 'people', query: 'zzq' });

    component.searchQuery = 'has:';
    component.$refs.searchInput.selectionStart = component.searchQuery.length;
    component.handleInput();
    assert.equal(component.emptyNote, null);
    assert.ok(component.options.length > 0);
});

test('autocomplete keyboard navigation wraps and scrolls selected option', () => {
    const context = browserContext();
    const { searchAutocomplete } = loadScripts(
        context,
        ['autocomplete.js'],
        '({searchAutocomplete})',
    );
    let scrolled = 0;
    const component = immediateComponent(searchAutocomplete());
    component.showDropdown = true;
    component.options = [{}, {}, {}];
    component.$refs.dropdownMenu = {
        querySelectorAll() {
            return component.options.map(() => ({
                scrollIntoView() {
                    scrolled += 1;
                },
            }));
        },
    };

    component.selectedIndex = 2;
    component.moveDown();
    assert.equal(component.selectedIndex, 0);
    component.moveUp();
    assert.equal(component.selectedIndex, 2);
    assert.equal(scrolled, 2);
});

test('autocomplete quotes selected multi-word values and keeps caret position', () => {
    const context = browserContext();
    const { searchAutocomplete } = loadScripts(
        context,
        ['autocomplete.js'],
        '({searchAutocomplete})',
    );
    let selection = null;
    const component = immediateComponent(searchAutocomplete());
    component.searchQuery = 'before tag:ni after';
    component.cursorPos = 'before tag:ni'.length;
    component.showDropdown = true;
    component.options = [{ prefix: 'tag:', value: 'Night Sky' }];
    component.$refs.searchInput = {
        isContentEditable: false,
        focus() {},
        setSelectionRange(start, end) {
            selection = [start, end];
        },
    };
    component.handleInput = () => {};

    component.selectOption();

    assert.equal(component.searchQuery, 'before tag:"Night Sky"  after');
    assert.deepEqual(selection, [23, 23]);
});

test('autocomplete keeps unfinished quoted values together across spaces', async () => {
    const context = browserContext();
    const calls = [];
    context.fetch = async url => {
        calls.push(url);
        return {
            ok: true,
            async json() {
                return { tags: [{ tag: 'Word 1', count: 2 }] };
            },
        };
    };
    const { searchAutocomplete } = loadScripts(
        context,
        ['autocomplete.js'],
        '({searchAutocomplete})',
    );
    const component = immediateComponent(searchAutocomplete());
    component.searchQuery = 'before tag:"word 1';
    component.$refs.searchInput = {
        isContentEditable: false,
        selectionStart: component.searchQuery.length,
        focus() {},
        setSelectionRange() {},
    };

    component.handleInput();
    await new Promise(resolve => setTimeout(resolve, 150));

    assert.equal(calls[0], '/api/tags/autocomplete?q=word%201');
    assert.equal(component.options[0].value, 'Word 1');

    component.selectOption();
    assert.equal(component.searchQuery, 'before tag:"Word 1" ');

    component.formatRichText('tag:"word 1');
    assert.equal((component.$refs.searchInput.innerHTML.match(/capsule-key/g) || []).length, 1);
    assert.match(component.$refs.searchInput.innerHTML, /capsule-value">word&nbsp;1</);
});

test('autocomplete keeps description and tag values in capsules without quotes', async () => {
    const context = browserContext();
    const tagLookups = [];
    context.fetch = async url => {
        tagLookups.push(url);
        return { ok: true, json: async () => ({ tags: [{ tag: 'Known Later', count: 1 }] }) };
    };
    const { searchAutocomplete, SEARCH_CARET, BOX_START, BOX_END } = loadScripts(
        context,
        ['autocomplete.js'],
        '({searchAutocomplete, SEARCH_CARET, BOX_START, BOX_END})',
    );
    const component = immediateComponent(searchAutocomplete());
    const read = marked => component.serializeSearchParts(component.parseSearchParts(marked));
    const box = text => BOX_START + text + BOX_END;

    assert.ok(component.allFilters.some(option => option.prefix === 'description:'));

    // Typing the colon opens an empty capsule with the caret inside.
    const opened = component.parseSearchParts(`cat tag:${SEARCH_CARET}`);
    assert.equal(opened[1].key, 'tag');
    assert.equal(opened[1].value, '');
    assert.equal(opened[1].caret, 0);
    assert.equal(read(`cat tag:${SEARCH_CARET}`).query, 'cat tag:');
    assert.equal(read(`cat tag:${SEARCH_CARET}`).caret, 8);

    // Spaces stay in the value; the query gets quotes and trimmed values.
    assert.equal(read(`${box(`description:red car${SEARCH_CARET}`)} after`).query, 'description:"red car" after');
    assert.equal(read(`${box(`description:red car${SEARCH_CARET}`)}`).caret, 'description:"red car'.length);
    assert.equal(read(box(`tag:cars ${SEARCH_CARET}`)).query, 'tag:cars');
    assert.equal(read(box('-tag:  street   photography ')).query, '-tag:"street photography"');

    // Text typed against the end of a capsule joins its value, so the caret
    // there stays inside; text against its start means it is not a filter.
    const glued = component.parseSearchParts(`${box('tag:car')}s${SEARCH_CARET} dog`);
    assert.equal(glued[0].value, 'cars');
    assert.equal(glued[0].caret, 4);
    assert.equal(read(`x${box('tag:cars')}`).query, 'xtag:cars');

    // Deleting the colon turns the capsule back into plain text.
    const unwrapped = component.parseSearchParts(box(`tag${SEARCH_CARET}cars`));
    assert.equal(unwrapped.length, 1);
    assert.equal(unwrapped[0].text, `tag${SEARCH_CARET}cars`);

    // A caret inside the key stays there.
    assert.equal(read(box(`ta${SEARCH_CARET}g:cars`)).caret, 2);

    // Loaded queries render as capsules without visible quotes.
    const input = { innerHTML: '' };
    component.$refs.searchInput = input;
    component.globalTags = [{ tag: 'cars' }];
    component.formatRichText('tag:cars OR tag:"no such" -description:"red car" tag: x');
    assert.match(input.innerHTML, /<span class="filter-box"><span class="capsule-key">tag:<\/span><span class="capsule-value">cars<\/span>/);
    assert.match(input.innerHTML, /boolean-capsule">OR</);
    assert.match(input.innerHTML, /filter-box is-unknown"><span class="capsule-key">tag:<\/span><span class="capsule-value">no&nbsp;such</);
    assert.match(input.innerHTML, /filter-box negative"><span class="capsule-key">-description:<\/span><span class="capsule-value">red&nbsp;car</);
    assert.match(input.innerHTML, /filter-box is-empty"><span class="capsule-key">tag:<\/span><span class="capsule-value"><\/span>/);
    assert.doesNotMatch(input.innerHTML, /&quot;/);

    // Unknown tags are looked up once; a tag the archive has turns blue.
    assert.deepEqual(tagLookups, ['/api/tags/autocomplete?q=no%20such']);
    component.formatRichText('tag:"known later"');
    await new Promise(resolve => setTimeout(resolve, 0));
    component.formatRichText('tag:"known later"');
    assert.match(input.innerHTML, /<span class="filter-box"><span class="capsule-key">tag:/);
});

test('autocomplete replaces the whole capsule value when a tag is picked', () => {
    const context = browserContext();
    const { searchAutocomplete } = loadScripts(
        context,
        ['autocomplete.js'],
        '({searchAutocomplete})',
    );
    const component = immediateComponent(searchAutocomplete());
    component.searchQuery = 'tag:"street ph" after';
    component.cursorPos = 'tag:"street'.length;
    component.showDropdown = true;
    component.options = [{ prefix: 'tag:', value: 'Street Photography' }];
    component.$refs.searchInput = {
        isContentEditable: false,
        focus() {},
        setSelectionRange() {},
    };
    component.handleInput = () => {};

    component.selectOption();

    assert.equal(component.searchQuery, 'tag:"Street Photography"  after');
});

test('autocomplete fetches author and tag suggestions with encoded queries', async () => {
    const context = browserContext();
    const calls = [];
    context.fetch = async url => {
        calls.push(url);
        if (url.startsWith('/api/authors')) {
            return {
                ok: true,
                async json() {
                    return {
                        authors: [
                            { id: '1', username: 'Alice', display_name: 'Alice A' },
                        ],
                    };
                },
            };
        }
        return {
            ok: true,
            async json() {
                return { tags: [{ tag: 'Night Sky', count: 1200 }] };
            },
        };
    };
    const { searchAutocomplete } = loadScripts(
        context,
        ['autocomplete.js'],
        '({searchAutocomplete})',
    );
    const component = immediateComponent(searchAutocomplete());
    component.$refs.searchInput = {
        isContentEditable: false,
        selectionStart: 10,
    };

    component.searchQuery = 'from:a b';
    component.cursorPos = component.searchQuery.length;
    component.handleInput();
    component.searchQuery = 'tag:night sky';
    component.cursorPos = component.searchQuery.length;
    component.handleInput();
    await new Promise(resolve => setTimeout(resolve, 150));

    assert.deepEqual(calls, []);

    component.searchQuery = 'from:ali';
    component.cursorPos = component.searchQuery.length;
    component.handleInput();
    await new Promise(resolve => setTimeout(resolve, 150));
    assert.equal(component.options[0].value, 'Alice');
    assert.ok(component.knownAuthors.has('alice'));
    assert.match(calls[0], /q=ali$/);

    component.searchQuery = 'tag:night';
    component.cursorPos = component.searchQuery.length;
    component.handleInput();
    await new Promise(resolve => setTimeout(resolve, 150));
    assert.equal(component.options[0].value, 'Night Sky');
    assert.equal(component.options[0].count, '1,200');

    // The first keystroke asks at once and the rest of a burst sends one request
    // after typing pauses. Only the reply for the newest input is shown, whatever
    // order the replies arrive in.
    calls.length = 0;
    const releases = [];
    context.fetch = url => {
        calls.push(url);
        const tag = decodeURIComponent(url.split('q=')[1]);
        return new Promise(resolve => {
            releases.push(() => resolve({ ok: true, json: async () => ({ tags: [{ tag: `Reply ${tag}`, count: 1 }] }) }));
        });
    };
    for (const query of ['tag:n', 'tag:ni', 'tag:nig']) {
        component.searchQuery = query;
        component.cursorPos = query.length;
        component.handleInput();
    }
    assert.deepEqual(calls, ['/api/tags/autocomplete?q=n']);
    await new Promise(resolve => setTimeout(resolve, 150));
    assert.deepEqual(calls, ['/api/tags/autocomplete?q=n', '/api/tags/autocomplete?q=nig']);
    releases[1]();
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.equal(component.options[0].value, 'Reply nig');
    releases[0]();
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.equal(component.options[0].value, 'Reply nig');

    // Once typing has paused, the next keystroke asks at once again.
    calls.length = 0;
    releases.length = 0;
    component.searchQuery = 'tag:nigh';
    component.cursorPos = component.searchQuery.length;
    component.handleInput();
    assert.deepEqual(calls, ['/api/tags/autocomplete?q=nigh']);
    releases[0]();
    await new Promise(resolve => setTimeout(resolve, 150));

    // A reply that arrives after the input moved to a local filter is dropped too.
    calls.length = 0;
    releases.length = 0;
    component.searchQuery = 'tag:nights';
    component.cursorPos = component.searchQuery.length;
    component.handleInput();
    component.searchQuery = 'has:';
    component.cursorPos = component.searchQuery.length;
    component.handleInput();
    releases[0]();
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.equal(component.options[0].value, 'media');
    await new Promise(resolve => setTimeout(resolve, 150));

    // Capsule validation of the typed handle shares the suggestion request.
    calls.length = 0;
    releases.length = 0;
    context.fetch = url => {
        calls.push(url);
        return new Promise(resolve => {
            releases.push(() => resolve({ ok: true, json: async () => ({ authors: [{ username: 'Alice', display_name: 'Alice A', id: '1' }] }) }));
        });
    };
    component.refreshEditor = () => { component.refreshed = true; };
    component.validateAuthorAsync('ALICE');
    component.searchQuery = 'from:alice';
    component.cursorPos = component.searchQuery.length;
    component.handleInput();
    assert.deepEqual(calls, ['/api/authors/search?q=alice']);
    releases[0]();
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.equal(component.options[0].value, 'Alice');
    assert.ok(component.refreshed);
});

test('autocomplete calendar handles leap years and month boundaries', () => {
    const context = browserContext();
    const { searchAutocomplete } = loadScripts(
        context,
        ['autocomplete.js'],
        '({searchAutocomplete})',
    );
    const component = searchAutocomplete();
    component.dpYear = 2024;
    component.dpMonth = 1;
    const days = component.dpGetDays();
    assert.equal(days.length, 42);
    assert.ok(days.some(day => day.isCurrentMonth && day.dateStr === '2024-02-29'));

    component.dpMonth = 0;
    component.dpPrevMonth();
    assert.deepEqual([component.dpYear, component.dpMonth], [2023, 11]);
    component.dpNextMonth();
    assert.deepEqual([component.dpYear, component.dpMonth], [2024, 0]);

    component.initDatePicker('2026-07-30');
    assert.deepEqual([component.dpYear, component.dpMonth], [2026, 6]);
    assert.equal(component.formatDateStr(2026, 7, 3), '2026-07-03');
});

test('autocomplete calendar inserts the picked date without mutating the picker option', () => {
    const context = browserContext();
    const { searchAutocomplete } = loadScripts(
        context,
        ['autocomplete.js'],
        '({searchAutocomplete})',
    );
    const component = immediateComponent(searchAutocomplete());
    component.searchQuery = 'since:';
    component.cursorPos = component.searchQuery.length;
    component.showDropdown = true;
    const picker = { isDatePicker: true, prefix: 'since:', val: '' };
    component.options = [picker];
    component.$refs.searchInput = { isContentEditable: false, focus() {}, setSelectionRange() {} };
    component.handleInput = () => {};

    component.dpSelectDate('2026-10-13', picker);

    assert.equal(component.searchQuery, 'since:2026-10-13 ');
    assert.deepEqual({ ...picker }, { isDatePicker: true, prefix: 'since:', val: '' });
});

test('autocomplete calendar follows caret moves into and out of date filters', () => {
    const context = browserContext();
    const { searchAutocomplete } = loadScripts(
        context,
        ['autocomplete.js'],
        '({searchAutocomplete})',
    );
    const component = immediateComponent(searchAutocomplete());
    const input = { isContentEditable: true };
    component.$refs.searchInput = input;
    context.document.activeElement = input;
    context.window.getSelection = () => ({ isCollapsed: true });
    component.searchQuery = 'cats since:2026-10-13 dogs';
    component.cursorPos = component.searchQuery.length;
    let caret = component.cursorPos;
    let refreshes = 0;
    component.readEditorCaret = () => ({ query: component.searchQuery, caret });
    component.handleInput = () => {
        refreshes++;
        component.cursorPos = caret;
    };

    caret = 'cats since:2026'.length;
    component.syncDatePickerToCaret();
    assert.equal(refreshes, 1);

    component.syncDatePickerToCaret();
    assert.equal(refreshes, 1, 'an unmoved caret does not refresh again');

    caret = 2;
    component.syncDatePickerToCaret();
    assert.equal(refreshes, 1, 'plain words leave a closed dropdown alone');

    component.showDropdown = true;
    component.options = [{ isDatePicker: true, prefix: 'since:', val: '' }];
    caret = component.searchQuery.length;
    component.syncDatePickerToCaret();
    assert.equal(refreshes, 2, 'leaving an open calendar refreshes the suggestions');
});

test('autocomplete calendar shows the whole date when the caret is inside it', () => {
    const context = browserContext();
    const { searchAutocomplete } = loadScripts(
        context,
        ['autocomplete.js'],
        '({searchAutocomplete})',
    );
    const component = immediateComponent(searchAutocomplete());
    component.$refs.searchInput = { isContentEditable: false, selectionStart: 0 };
    component.searchQuery = 'cats since:2025-03-14 dogs';
    for (const caret of ['cats since:'.length, 'cats since:2025'.length, 'cats since:2025-03-1'.length]) {
        component.$refs.searchInput.selectionStart = caret;
        component.handleInput();
        assert.equal(component.options[0].val, '2025-03-14');
        assert.deepEqual([component.dpYear, component.dpMonth], [2025, 2]);
    }
});

run();
