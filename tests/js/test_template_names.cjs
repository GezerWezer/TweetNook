'use strict';

// Every name an Alpine expression in the page reads must exist in the scope that evaluates it:
// an x-for alias, an inline x-data object, a component's state or methods, a page-script global
// or a browser global. Moving methods between components, or out of app.js, breaks this check
// instead of leaving a silently undefined name in the markup.

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { JS_DIR, pageLines, pageScripts } = require('./page_scripts.cjs');

const KEYWORDS = new Set(('true false null undefined typeof instanceof new in of this let const var '
    + 'return if else function await async void delete NaN Infinity').split(' '));
const ALPINE_MAGICS = new Set(['$el', '$refs', '$store', '$watch', '$dispatch', '$nextTick', '$root',
    '$data', '$id', '$event']);
// Browser globals the vm context doesn't provide.
const BROWSER_GLOBALS = new Set(['window', 'document', 'navigator', 'location', 'history', 'localStorage',
    'sessionStorage', 'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame', 'alert', 'confirm',
    'fetch', 'matchMedia', 'innerWidth', 'innerHeight', 'scrollY', 'scrollTo', 'Intl', 'Event', 'CustomEvent',
    'DataTransfer']);
// Directives whose value is not an expression.
const NOT_EXPRESSIONS = /^x-(ref|cloak|ignore|teleport|collapse|transition)(?=$|[.:])/;

// --- Page scripts and component instances ---------------------------------------------------

// Load-time code in the page scripts only touches the DOM to attach listeners and flags, so a
// stand-in that accepts any property or call is enough to let every script define its globals.
function anything() {
    const target = function () {};
    const proxy = new Proxy(target, {
        get: (_, key) => (key === Symbol.toPrimitive ? () => '' : key === 'then' ? undefined : proxy),
        apply: () => proxy,
        construct: () => proxy,
    });
    return proxy;
}

function pageContext() {
    const stub = anything();
    const context = {
        console, URL, URLSearchParams, Intl,
        document: stub, navigator: {}, history: stub,
        location: { pathname: '/', search: '', hash: '', href: 'http://localhost/', origin: 'http://localhost' },
        localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
        sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
        matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
        getComputedStyle: () => stub,
        requestAnimationFrame: () => 0, cancelAnimationFrame() {},
        setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
        addEventListener() {}, removeEventListener() {},
        fetch: () => new Promise(() => {}),
        innerWidth: 1280, innerHeight: 900,
    };
    context.window = context;
    vm.createContext(context);
    for (const name of pageScripts()) {
        vm.runInContext(fs.readFileSync(path.join(JS_DIR, name), 'utf8'), context, { filename: name });
    }
    return context;
}

function isGlobal(context, name) {
    if (BROWSER_GLOBALS.has(name)) return true;
    // Top-level const/let/class declarations are visible to other scripts but are not properties
    // of the global object, so ask the script scope itself.
    return vm.runInContext(`typeof ${name} !== 'undefined'`, context);
}

// x-data="nookSelect({...})" and friends: build the component with stand-in arguments, since its
// option callbacks read the parent scope only when called.
function componentScope(context, expression) {
    const call = /^\s*([A-Za-z_$][\w$]*)\s*\(/.exec(expression);
    if (call) {
        const factory = vm.runInContext(call[1], context);
        const stub = anything();
        const instance = factory(new Proxy({}, { get: (_, key) => (key === 'searchable' ? false : stub) }));
        return { label: `${call[1]}()`, has: name => name in instance };
    }
    const keys = objectKeys(expression);
    return { label: `x-data ${expression.trim()}`, has: name => keys.has(name) };
}

// --- Expression scanning ---------------------------------------------------------------------

function lex(source, start = 0, untilBrace = false) {
    const tokens = [];
    let i = start;
    let depth = 0;
    while (i < source.length) {
        const c = source[i];
        if (/\s/.test(c)) { i++; continue; }
        if (c === '"' || c === "'") {
            i++;
            while (i < source.length && source[i] !== c) i += source[i] === '\\' ? 2 : 1;
            i++;
            tokens.push({ type: 'string' });
            continue;
        }
        if (c === '`') {
            i++;
            while (i < source.length && source[i] !== '`') {
                if (source[i] === '\\') { i += 2; continue; }
                if (source[i] === '$' && source[i + 1] === '{') {
                    const inner = lex(source, i + 2, true);
                    tokens.push({ type: '(', value: '(' }, ...inner.tokens, { type: ')', value: ')' });
                    i = inner.end + 1;
                    continue;
                }
                i++;
            }
            i++;
            tokens.push({ type: 'string' });
            continue;
        }
        const word = /^[A-Za-z_$][\w$]*/.exec(source.slice(i, i + 200));
        if (word) { tokens.push({ type: 'name', value: word[0], at: i }); i += word[0].length; continue; }
        const number = /^\d[\w.]*/.exec(source.slice(i, i + 50));
        if (number) { tokens.push({ type: 'number' }); i += number[0].length; continue; }
        const punct = ['?.', '=>', '...'].find(op => source.startsWith(op, i)) || c;
        if (untilBrace && punct === '{') depth++;
        if (untilBrace && punct === '}') {
            if (depth === 0) return { tokens, end: i };
            depth--;
        }
        tokens.push({ type: punct, value: punct });
        i += punct.length;
    }
    return { tokens, end: i };
}

function locals(tokens) {
    const names = new Set();
    tokens.forEach((token, index) => {
        if (token.type === 'name' && ['let', 'const', 'var'].includes(token.value)) {
            const next = tokens[index + 1];
            if (next?.type === 'name') names.add(next.value);
        }
        if (token.type !== '=>') return;
        const before = tokens[index - 1];
        if (before?.type === 'name') { names.add(before.value); return; }
        if (before?.type !== ')') return;
        let depth = 0;
        for (let j = index - 1; j >= 0; j--) {
            if (tokens[j].type === ')') depth++;
            else if (tokens[j].type === '(' && --depth === 0) break;
            else if (tokens[j].type === 'name') names.add(tokens[j].value);
        }
    });
    return names;
}

// The free names an expression reads: not properties, object keys, keywords or its own locals.
function freeNames(expression) {
    const { tokens } = lex(expression);
    const own = locals(tokens);
    const names = [];
    tokens.forEach((token, index) => {
        if (token.type !== 'name' || KEYWORDS.has(token.value) || own.has(token.value)) return;
        const before = tokens[index - 1]?.type;
        const after = tokens[index + 1]?.type;
        if (before === '.' || before === '?.') return;
        if (after === ':' && (before === '{' || before === ',')) return;
        names.push(token.value);
    });
    return names;
}

function objectKeys(expression) {
    const { tokens } = lex(expression);
    const keys = new Set();
    let depth = 0;
    tokens.forEach((token, index) => {
        if (token.type === '{' || token.type === '(' || token.type === '[') depth++;
        if (token.type === '}' || token.type === ')' || token.type === ']') depth--;
        const before = tokens[index - 1]?.type;
        if (depth === 1 && token.type === 'name' && (before === '{' || before === ',')
            && ['(', ':', ',', '}'].includes(tokens[index + 1]?.type)) keys.add(token.value);
    });
    return keys;
}

function forAliases(expression) {
    const match = /^\s*(?:\(([^)]*)\)|\[([^\]]*)\]|([A-Za-z_$][\w$]*))\s+(?:in|of)\s+([\s\S]+)$/.exec(expression);
    assert.ok(match, `unrecognised x-for: ${expression}`);
    const aliases = (match[1] ?? match[2] ?? match[3]).split(',').map(name => name.trim()).filter(Boolean);
    return { aliases: new Set(aliases), iterable: match[4] };
}

// --- Markup walk -----------------------------------------------------------------------------

const VOID = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source',
    'track', 'wbr', 'path', 'circle', 'rect', 'line', 'polyline', 'polygon', 'stop', 'use', 'ellipse']);
const ENTITIES = { '&amp;': '&', '&quot;': '"', '&#39;': "'", '&lt;': '<', '&gt;': '>' };

function elements(html) {
    const found = [];
    const tag = /<!--[\s\S]*?-->|<(script|style)\b[^>]*>[\s\S]*?<\/\1>|<\/([\w-]+)\s*>|<([\w-]+)((?:\s+[^\s=>/"']+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>"']+))?)*)\s*(\/?)>/g;
    let match;
    while ((match = tag.exec(html))) {
        if (match[2]) { found.push({ close: match[2].toLowerCase() }); continue; }
        if (!match[3]) continue;
        const attributes = [];
        const attribute = /([^\s=>/"']+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>"']+)))?/g;
        let part;
        while ((part = attribute.exec(match[4]))) {
            const value = (part[2] ?? part[3] ?? part[4] ?? '').replace(/&(amp|quot|#39|lt|gt);/g, e => ENTITIES[e]);
            attributes.push({ name: part[1], value });
        }
        const name = match[3].toLowerCase();
        const line = html.slice(0, match.index).split('\n').length;
        found.push({ open: name, attributes, line, selfClosing: Boolean(match[5]) || VOID.has(name) });
    }
    return found;
}

function isExpression(name) {
    if (name === 'x-data' || name === 'x-for') return false;
    if (name.startsWith('@') || name.startsWith(':')) return true;
    return name.startsWith('x-') && !NOT_EXPRESSIONS.test(name);
}

// origin(line) names the file and line a line of html came from.
function checkTemplate(html, context, origin = line => `index.html:${line}`) {
    const problems = [];
    const stack = [{ scopes: [] }];
    const check = (scopes, expression, where) => {
        for (const name of freeNames(expression)) {
            const resolved = name.startsWith('$')
                ? ALPINE_MAGICS.has(name)
                : scopes.some(scope => scope.has(name)) || isGlobal(context, name);
            if (!resolved) {
                const chain = scopes.map(scope => scope.label).reverse().join(' > ') || 'page';
                problems.push(`${origin(where.line)} ${where.attribute}: '${name}' is not defined in ${chain}`);
            }
        }
    };
    for (const element of elements(html)) {
        if (element.close) {
            if (stack.length > 1 && stack.at(-1).tag === element.close) stack.pop();
            continue;
        }
        let scopes = stack.at(-1).scopes;
        const attribute = name => element.attributes.find(entry => entry.name === name);
        const where = name => ({ line: element.line, attribute: name });
        const data = attribute('x-data');
        if (data) {
            check(scopes, data.value, where('x-data'));
            scopes = [...scopes, componentScope(context, data.value)];
        }
        const loop = attribute('x-for');
        if (loop) {
            const { aliases, iterable } = forAliases(loop.value);
            check(scopes, iterable, where('x-for'));
            scopes = [...scopes, { label: `x-for(${[...aliases].join(', ')})`, has: name => aliases.has(name) }];
        }
        for (const entry of element.attributes) {
            if (isExpression(entry.name) && entry.value.trim()) check(scopes, entry.value, where(entry.name));
        }
        if (!element.selfClosing) stack.push({ tag: element.open, scopes });
    }
    return problems;
}

// --- Tests -----------------------------------------------------------------------------------

const context = pageContext();
const lines = pageLines();
const page = lines.map(line => line.text).join('');
let failures = 0;
function test(name, body) {
    try { body(); console.log(`ok - ${name}`); } catch (error) { failures++; console.error(`not ok - ${name}\n${error.message}`); }
}

test('the scanner skips properties, keys, keywords, strings and locals', () => {
    assert.deepEqual(freeNames("a.b?.c + d({ e: f, g }) || `x${h}` + 'i' + (j => j + k)(l)"),
        ['a', 'd', 'f', 'g', 'h', 'k', 'l']);
    assert.deepEqual(freeNames('open ? close(true) : show = { up: false }'), ['open', 'close', 'show']);
    assert.deepEqual(freeNames('let n = items.length; n > 0 && pick(n)'), ['items', 'pick']);
    assert.deepEqual([...objectKeys("{ shown: false, toggle() { this.shown = !this.shown }, label }")],
        ['shown', 'toggle', 'label']);
});

test('names resolve through x-for aliases, nested components and page globals', () => {
    const markup = `<div x-data="tweetApp()"><template x-for="(tweet, index) in tweets" :key="tweet.tweet_id">
        <div x-data="{ shown: false }" @click="shown = !shown; openThread(tweet.tweet_id, index)" x-text="THEMES"></div>
        </template><span x-show="tweet || missingName || $nope"></span></div>`;
    assert.deepEqual(checkTemplate(markup, context), [
        "index.html:3 x-show: 'tweet' is not defined in tweetApp()",
        "index.html:3 x-show: 'missingName' is not defined in tweetApp()",
        "index.html:3 x-show: '$nope' is not defined in tweetApp()",
    ]);
});

test('every name the page reads resolves in the scope that evaluates it', () => {
    const problems = checkTemplate(page, context, line => lines[line - 1].where);
    assert.deepEqual(problems, [], `${problems.length} unresolved names:\n${problems.join('\n')}`);
});

if (failures) process.exitCode = 1;
