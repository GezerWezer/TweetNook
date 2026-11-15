'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { pageHtml } = require('./page_scripts.cjs');
const {
    FakeElement,
    JS_DIR,
    ROOT,
    browserContext,
    loadScripts,
    test,
    run,
} = require('./web_harness.cjs');

test('header collapses after pointer tab selection and keeps keyboard-focused tabs visible', () => {
    const context = browserContext();
    const header = new FakeElement('header');
    const tab = new FakeElement('button');
    header.appendChild(tab);
    header.offsetHeight = 110;
    let keyboardFocus = false;
    tab.matches = selector => selector === ':focus-visible' && keyboardFocus;
    context.document.activeElement = tab;
    context.document.querySelector = () => header;
    context.requestAnimationFrame = callback => callback();
    const autoHide = loadScripts(context, ['header.js'], 'nookHeaderAutoHide');
    autoHide();
    const scroll = y => { context.window.scrollY = y; context.__state.events.get('scroll')(); };
    scroll(300);
    assert.equal(header.classList.contains('tabs-hidden'), true);
    keyboardFocus = true;
    scroll(400);
    assert.equal(header.classList.contains('tabs-hidden'), false);
    scroll(500);
    assert.equal(header.classList.contains('tabs-hidden'), false);
    keyboardFocus = false;
    scroll(600);
    assert.equal(header.classList.contains('tabs-hidden'), true);
    scroll(550);
    assert.equal(header.classList.contains('tabs-hidden'), false);
    scroll(20);
    assert.equal(header.classList.contains('tabs-hidden'), false);
});

test('header tabs ignore overscroll and a page shrinking under a reader at the end', () => {
    const context = browserContext();
    const header = new FakeElement('header');
    header.offsetHeight = 110;
    context.document.querySelector = () => header;
    context.document.documentElement.scrollHeight = 5000;
    context.window.innerHeight = 800;
    context.requestAnimationFrame = callback => callback();
    loadScripts(context, ['header.js'], 'nookHeaderAutoHide')();
    const scroll = y => { context.window.scrollY = y; context.__state.events.get('scroll')(); };
    const hidden = () => header.classList.contains('tabs-hidden');
    scroll(3000);
    assert.equal(hidden(), true);
    // Rubber-banding past the end (max 4200) and settling back is not scrolling up.
    scroll(4290);
    scroll(4200);
    assert.equal(hidden(), true);
    // The page loses 49px under a reader at the end; the position follows the new end.
    context.document.documentElement.scrollHeight = 4951;
    scroll(4151);
    assert.equal(hidden(), true);
    // A real upward scroll still brings the tabs back.
    scroll(4100);
    assert.equal(hidden(), false);
});

test('checkboxes use the shared drawn box over a native input', () => {
    const html = pageHtml();
    // Automated tagging's Google Search and Setup's three replay options.
    const boxes = html.match(/<span class="nook-checkbox"><input type="checkbox"[^>]*><svg viewBox="0 0 24 24" aria-hidden="true">/g) || [];
    assert.equal(boxes.length, 4);
    assert.doesNotMatch(html, /<input type="checkbox"(?![^>]*\bsr-only\b)(?![^>]*role="switch")[^>]*>(?!<svg)/);
    const controls = fs.readFileSync(path.join(ROOT, 'tweetnook/web/static/css/controls.css'), 'utf8');
    assert.match(controls, /\.nook-checkbox:has\(input:checked\),\n\[aria-checked="true"\] > \.nook-checkbox \{[^}]*background: var\(--accent-color\);/);
    // A row button with role="checkbox" carries the mark without an input.
    assert.match(html, /:role="selecting \? 'checkbox' : null"[^>]*>\s*<span class="nook-checkbox" x-show="selecting" aria-hidden="true">/);
});

test('choice rows mark the chosen option with the shared radio', () => {
    const html = pageHtml();
    // Desktop layout's two rows and the merge sheet's tag-to-keep rows.
    const rows = html.match(/role="radio" class="nook-choice-row"[\s\S]*?<\/button>/g) || [];
    assert.equal(rows.length, 3);
    for (const row of rows) assert.match(row, /<span class="nook-radio" aria-hidden="true"><\/span>\s*<\/button>$/);
    const controls = fs.readFileSync(path.join(ROOT, 'tweetnook/web/static/css/controls.css'), 'utf8');
    assert.match(controls, /\[aria-checked="true"\] > \.nook-radio::after,\ninput:checked \+ \.nook-radio::after \{\n    transform: scale\(1\);/);
});

test('typed values sit in a shared field box', () => {
    // Walk the page's tags with a stack of the open elements' classes, so each
    // text input can be checked against its ancestors.
    const html = pageHtml().replace(/<!--[\s\S]*?-->/g, '');
    const VOID = new Set(['input', 'img', 'br', 'hr', 'meta', 'link', 'source', 'track', 'wbr', 'col', 'area', 'base', 'embed', 'param']);
    const BOXES = /\bnook-(?:field|input|search)\b/;
    const NOT_TYPED = /\btype="(?:checkbox|radio|range|file|hidden|color)"/;
    const stack = [];
    const loose = [];
    for (const match of html.matchAll(/<(\/?)([a-zA-Z][\w-]*)\b((?:[^>"']|"[^"]*"|'[^']*')*)>/g)) {
        const [tag, closing, rawName, attrs] = match;
        const name = rawName.toLowerCase();
        if (closing) {
            const at = stack.map(entry => entry.name).lastIndexOf(name);
            if (at >= 0) stack.length = at;
            continue;
        }
        const typed = (name === 'input' && !NOT_TYPED.test(attrs) && !/\bsr-only\b/.test(attrs))
            || name === 'textarea' || /\bcontenteditable="true"/.test(attrs);
        if (typed && !stack.some(entry => BOXES.test(entry.cls))) loose.push(tag.slice(0, 120));
        if (!VOID.has(name) && !tag.endsWith('/>')) stack.push({ name, cls: (/\bclass="([^"]*)"/.exec(attrs) || [])[1] || '' });
    }
    assert.deepEqual(loose, []);
});

test('tab strips use the shared tab with its label span', () => {
    const html = pageHtml();
    const tabs = html.match(/<button[^>]*role="tab"[\s\S]*?<\/button>/g) || [];
    // Feed collections, Setup's browser help, the jobs panel's steps, and Logs' Activity and Server.
    assert.equal(tabs.length, 5);
    for (const tab of tabs) {
        assert.match(tab, /class="nook-tab[ "]/);
        assert.match(tab, /<span class="nook-tab-label"/);
    }
    for (const list of html.match(/<[a-z]+ [^>]*role="tablist"[^>]*>/g) || []) assert.match(list, /class="nook-tabs[ "]/);
});

test('packaged page loads only same-origin runtime assets', () => {
    const html = pageHtml();
    const head = html.split('</head>')[0];
    assert.doesNotMatch(head, /(?:src|href)=["']https?:/);
    for (const match of head.matchAll(/(?:src|href)=["'](\/static\/[^"']+)/g)) {
        assert.ok(fs.existsSync(path.join(ROOT, 'tweetnook/web', match[1])), match[1]);
    }
    assert.doesNotMatch(fs.readFileSync(path.join(JS_DIR, 'themes.js'), 'utf8'), /fonts\.googleapis/);
});

test('Tailwind scans every browser script, including nested modules', () => {
    // A script outside the content globs loses its utility classes on the next
    // asset build, while the shipped CSS still hides the loss.
    const webDir = path.join(ROOT, 'dev/web');
    const { content } = require(path.join(webDir, 'tailwind.config.cjs'));
    const scripts = fs.readdirSync(JS_DIR, { recursive: true }).filter(file => file.endsWith('.js'));
    assert.ok(scripts.some(file => file.includes(path.sep)), 'expected nested script modules');
    const missed = scripts.filter(file => !content.files.some(glob =>
        path.matchesGlob(path.relative(webDir, path.join(JS_DIR, file)), glob)));
    assert.deepEqual(missed, []);
});

run();
