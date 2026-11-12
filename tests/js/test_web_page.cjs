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

test('unchecked square checkboxes use thinner outlines while checked states retain weight', () => {
    const html = pageHtml();
    assert.equal(
        (html.match(/class="flex h-6 w-6 items-center justify-center rounded-lg border-2 transition"/g) || []).length,
        1,
    );
    assert.equal((html.match(/text-white border-\[3px\]'/g) || []).length, 1);
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
