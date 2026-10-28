'use strict';

// core/html.js is loaded on its own, with only the browser's URL: the template helper has no
// dependency on the app.

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { JS_DIR } = require('./page_scripts.cjs');

function load() {
    const context = { URL };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(JS_DIR, 'core', 'html.js'), 'utf8'), context);
    return vm.runInContext('({ html, rawHTML, escapeHTML, httpURL })', context);
}

const tests = [];
function test(name, fn) {
    tests.push({ name, fn });
}

const { html, rawHTML, escapeHTML, httpURL } = load();

test('interpolated values are escaped in text and attributes', () => {
    const name = `<img src=x onerror="alert('x')">&`;
    assert.equal(
        String(html`<a title="${name}">${name}</a>`),
        '<a title="&lt;img src=x onerror=&quot;alert(&#039;x&#039;)&quot;&gt;&amp;">'
            + '&lt;img src=x onerror=&quot;alert(&#039;x&#039;)&quot;&gt;&amp;</a>',
    );
});

test('nested templates and rawHTML pass through unescaped', () => {
    const icon = rawHTML('<svg viewBox="0 0 1 1"></svg>');
    const inner = html`<b>${'a&b'}</b>`;
    assert.equal(String(html`<span>${icon}${inner}</span>`), '<span><svg viewBox="0 0 1 1"></svg><b>a&amp;b</b></span>');
    assert.equal(rawHTML(inner), inner);
    assert.equal(String(rawHTML(null)), '');
});

test('arrays join their items, each escaped on its own', () => {
    const items = ['<1>', html`<i>2</i>`, ['3', '<4>']];
    assert.equal(String(html`<p>${items}</p>`), '<p>&lt;1&gt;<i>2</i>3&lt;4&gt;</p>');
});

test('null, undefined and false render nothing; 0 and true render as text', () => {
    assert.equal(String(html`[${null}][${undefined}][${false}][${0}][${true}][${''}]`), '[][][][0][true][]');
});

test('a template result works where markup strings are concatenated', () => {
    const part = html`<em>${'x<y'}</em>`;
    assert.equal('<p>' + part + '</p>', '<p><em>x&lt;y</em></p>');
    assert.equal(`${part}`, '<em>x&lt;y</em>');
});

test('escapeHTML keeps the app method\'s handling of empty values', () => {
    assert.equal(escapeHTML(`"'<>&`), '&quot;&#039;&lt;&gt;&amp;');
    assert.equal(escapeHTML(0), '');
    assert.equal(escapeHTML(null), '');
    assert.equal(escapeHTML(42), '42');
});

test('httpURL keeps http(s) links and turns everything else into #', () => {
    assert.equal(httpURL('https://e.test/a?b=1&c="2"'), 'https://e.test/a?b=1&c=%222%22');
    assert.equal(httpURL('/relative'), 'http://localhost/relative');
    for (const value of ['javascript:alert(1)', ' JavaScript:alert(1)', 'data:text/html,x', 'http://[', '', null, '#']) {
        assert.equal(httpURL(value), '#', String(value));
    }
    assert.equal(String(html`<a href="${httpURL('https://e.test/?a=1&b=2')}">`), '<a href="https://e.test/?a=1&amp;b=2">');
});

let failures = 0;
for (const { name, fn } of tests) {
    try {
        fn();
        process.stdout.write(`ok - ${name}\n`);
    } catch (error) {
        failures += 1;
        process.stderr.write(`not ok - ${name}\n${error.stack}\n`);
    }
}
process.stdout.write(`\n${tests.length - failures}/${tests.length} html template tests passed\n`);
if (failures) process.exitCode = 1;
