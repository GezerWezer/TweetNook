'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '..', '..');
const JS_DIR = path.join(ROOT, 'tweetnook', 'web', 'static', 'js');

function browserContext(pathname = '/') {
    const stored = new Map();
    const sessionStored = new Map();
    const cssProperties = new Map();
    const rootClasses = new Set();
    const events = new Map();
    const historyCalls = [];
    const scrollCalls = [];
    const appendedLinks = [];
    const videos = [];
    const location = { origin: 'http://localhost', pathname };
    let historyState = null;

    const context = {
        console: {
            log() {},
            error() {},
            warn() {},
        },
        setTimeout,
        clearTimeout,
        setInterval: () => 0,
        clearInterval() {},
        URL,
        URLSearchParams,
        Date,
        Math,
        JSON,
        Promise,
        AbortController,
        encodeURIComponent,
        decodeURIComponent,
        confirm: () => true,
        alert() {},
        CustomEvent: class CustomEvent {
            constructor(type, options = {}) {
                this.type = type;
                this.detail = options.detail;
            }
        },
        localStorage: {
            getItem(key) {
                return stored.has(key) ? stored.get(key) : null;
            },
            setItem(key, value) {
                stored.set(key, String(value));
            },
            removeItem(key) {
                stored.delete(key);
            },
        },
        sessionStorage: {
            getItem(key) { return sessionStored.get(key) ?? null; },
            setItem(key, value) { sessionStored.set(key, String(value)); },
            removeItem(key) { sessionStored.delete(key); },
        },
        history: {
            scrollRestoration: 'auto',
            get state() {
                return historyState;
            },
            replaceState(...args) {
                historyCalls.push(['replace', ...args]);
                historyState = args[0];
                if (args[2]) location.pathname = args[2];
            },
            pushState(...args) {
                historyCalls.push(['push', ...args]);
                historyState = args[0];
                if (args[2]) location.pathname = args[2];
            },
            back() {
                historyCalls.push(['back']);
            },
        },
        document: {
            addEventListener(name, callback) {
                events.set(`document:${name}`, callback);
            },
            removeEventListener(name) {
                events.delete(`document:${name}`);
            },
            documentElement: {
                style: {
                    setProperty(key, value) {
                        cssProperties.set(key, value);
                    },
                },
                classList: {
                    add(value) {
                        rootClasses.add(value);
                    },
                    remove(value) {
                        rootClasses.delete(value);
                    },
                },
            },
            body: {
                style: {},
                querySelectorAll() {
                    return [];
                },
            },
            head: {
                appendChild(node) {
                    appendedLinks.push(node);
                },
            },
            querySelector(selector) {
                if (selector.startsWith('link[data-font=')) {
                    return appendedLinks.find(link => selector.includes(link.dataset.font)) || null;
                }
                return null;
            },
            querySelectorAll(selector) {
                if (selector === 'video') return videos;
                if (selector === 'video[controls], video[data-video-custom], video[data-video-player]') return videos.filter(video => video.hasAttribute?.('controls') || video.hasAttribute?.('data-video-custom') || video.hasAttribute?.('data-video-player'));
                return [];
            },
            getElementById() {
                return null;
            },
            createElement(tag) {
                return { tagName: tag.toUpperCase(), dataset: {}, style: {} };
            },
            createRange() {
                return {
                    cloneRange() {
                        return this;
                    },
                    selectNodeContents() {},
                    setEnd() {},
                    setStart() {},
                    collapse() {},
                    toString() {
                        return '';
                    },
                };
            },
        },
        IntersectionObserver: class IntersectionObserver {
            constructor(callback) {
                this.callback = callback;
                this.observed = [];
            }
            observe(node) {
                this.observed.push(node);
            }
        },
        MutationObserver: class MutationObserver {
            constructor(callback) {
                this.callback = callback;
            }
            observe() {}
        },
    };

    context.window = {
        innerWidth: 1280,
        scrollY: 0,
        location,
        matchMedia: query => ({ matches: query.includes('min-width') }),
        addEventListener(name, callback) {
            events.set(name, callback);
        },
        removeEventListener(name) {
            events.delete(name);
        },
        dispatchEvent(event) {
            const callback = events.get(event.type);
            if (callback) return callback(event);
        },
        scrollTo(...args) {
            scrollCalls.push(args);
        },
        getSelection() {
            return {
                rangeCount: 0,
                removeAllRanges() {},
                addRange() {},
            };
        },
    };
    context.location = location;
    context.globalThis = context;
    context.__state = {
        stored,
        cssProperties,
        rootClasses,
        events,
        historyCalls,
        scrollCalls,
        appendedLinks,
        videos,
    };
    vm.createContext(context);
    return context;
}

function loadScripts(context, names, exportExpression) {
    const source = names
        .map(name => fs.readFileSync(path.join(JS_DIR, name), 'utf8'))
        .join('\n');
    vm.runInContext(`${source}\nglobalThis.__exports = ${exportExpression};`, context, {
        filename: names.join('+'),
    });
    return context.__exports;
}

function immediateComponent(component) {
    component.$nextTick = callback => callback();
    component.$watch = () => {};
    component.$refs = {};
    return component;
}

// A minimal DOM for feed-list tests: parses the renderer's markup and supports the
// element APIs the list code uses (tag, class, and attribute selectors, descendant
// combinators, and selector lists).
const VOID_TAGS = new Set(['img', 'input', 'br', 'hr', 'source', 'meta', 'link']);
const decodeEntities = text => text.replace(/&(amp|lt|gt|quot|#0?39);/g, (_, name) => (
    { amp: '&', lt: '<', gt: '>', quot: '"', '#039': "'", '#39': "'" }[name]
));

function splitSelector(source, separator) {
    const parts = [''];
    let quoted = false;
    for (const character of source) {
        if (character === '"') quoted = !quoted;
        if (character === separator && !quoted) parts.push('');
        else parts[parts.length - 1] += character;
    }
    return parts.map(part => part.trim()).filter(Boolean);
}

function compoundMatcher(source) {
    const tests = [];
    const pattern = /([a-zA-Z][\w-]*)|\.([\w-]+)|\[([^\]=]+)(?:="([^"]*)")?\]/g;
    let match;
    let consumed = '';
    while ((match = pattern.exec(source))) {
        consumed += match[0];
        const [, tag, className, attribute, value] = match;
        if (tag) tests.push(element => element.tagName === tag.toUpperCase());
        else if (className) tests.push(element => element.classList.contains(className));
        else if (value === undefined) tests.push(element => element.hasAttribute(attribute));
        else tests.push(element => element.getAttribute(attribute) === value);
    }
    assert.equal(consumed, source, `unsupported selector ${source}`);
    return element => tests.every(test => test(element));
}

function selectorMatcher(selector) {
    const alternatives = splitSelector(selector, ',').map(part => {
        const compounds = splitSelector(part, ' ').map(compoundMatcher);
        return element => {
            if (!compounds.at(-1)(element)) return false;
            let index = compounds.length - 2;
            for (let current = element.parentElement; current && index >= 0; current = current.parentElement) {
                if (compounds[index](current)) index -= 1;
            }
            return index < 0;
        };
    });
    return element => alternatives.some(matches => matches(element));
}

class FakeElement {
    constructor(tagName, attributes = []) {
        this.tagName = tagName.toUpperCase();
        this.attributeMap = new Map(attributes);
        this.childNodes = [];
        this.parentElement = null;
        this.listeners = [];
        this.rect = null;
        this.connected = false;
    }

    get children() { return this.childNodes.filter(node => node instanceof FakeElement); }
    get firstElementChild() { return this.children[0] || null; }
    get previousElementSibling() {
        const siblings = this.parentElement?.children || [];
        return siblings[siblings.indexOf(this) - 1] || null;
    }
    get isConnected() {
        let node = this;
        while (node.parentElement) node = node.parentElement;
        return node.connected;
    }
    get dataset() {
        const data = {};
        for (const [name, value] of this.attributeMap) {
            if (name.startsWith('data-')) data[name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = value;
        }
        return data;
    }
    get classList() {
        const read = () => (this.getAttribute('class') || '').split(/\s+/).filter(Boolean);
        const write = names => this.setAttribute('class', names.join(' '));
        return {
            contains: name => read().includes(name),
            add: name => { if (!read().includes(name)) write([...read(), name]); },
            remove: (...names) => write(read().filter(value => !names.includes(value))),
            toggle: (name, force) => {
                const enable = force ?? !read().includes(name);
                if (enable) write([...new Set([...read(), name])]);
                else write(read().filter(value => value !== name));
                return enable;
            },
        };
    }
    get textContent() {
        return this.childNodes.map(node => (node instanceof FakeElement ? node.textContent : node.text)).join('');
    }
    set textContent(value) { this.childNodes = [{ text: String(value) }]; }
    set innerHTML(html) {
        this.childNodes.forEach(node => { node.parentElement = null; });
        this.childNodes = [];
        this.append(parseFakeHTML(html));
    }

    getAttribute(name) { return this.attributeMap.has(name) ? this.attributeMap.get(name) : null; }
    setAttribute(name, value) { this.attributeMap.set(name, String(value)); }
    hasAttribute(name) { return this.attributeMap.has(name); }
    removeAttribute(name) { this.attributeMap.delete(name); }
    append(nodes, index = this.childNodes.length) {
        nodes.forEach(node => { node.parentElement = this; });
        this.childNodes.splice(index, 0, ...nodes);
    }
    insertAdjacentHTML(position, html) {
        if (position === 'beforeend') return this.append(parseFakeHTML(html));
        assert.equal(position, 'beforebegin');
        this.parentElement.append(parseFakeHTML(html), this.parentElement.childNodes.indexOf(this));
    }
    remove() {
        if (!this.parentElement) return;
        this.parentElement.childNodes.splice(this.parentElement.childNodes.indexOf(this), 1);
        this.parentElement = null;
    }
    cloneNode() {
        const clone = new FakeElement(this.tagName, [...this.attributeMap]);
        clone.rect = this.rect;
        const values = new Map();
        clone.style = {setProperty: (name, value) => values.set(name, value), getPropertyValue: name => values.get(name) || '', removeProperty: name => values.delete(name)};
        return clone;
    }
    appendChild(node) { node.remove(); this.append([node]); return node; }
    insertBefore(node, before) { node.remove(); this.append([node], this.childNodes.indexOf(before)); }
    replaceWith(node) { const parent = this.parentElement; parent.insertBefore(node, this); this.remove(); }
    contains(node) {
        for (let current = node; current; current = current.parentElement) if (current === this) return true;
        return false;
    }
    matches(selector) { return selectorMatcher(selector)(this); }
    closest(selector) {
        const matches = selectorMatcher(selector);
        for (let current = this; current; current = current.parentElement) if (matches(current)) return current;
        return null;
    }
    querySelectorAll(selector) {
        const matches = selectorMatcher(selector);
        const found = [];
        const visit = element => element.children.forEach(child => {
            if (matches(child)) found.push(child);
            visit(child);
        });
        visit(this);
        return found;
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    addEventListener(type, listener, capture = false) { this.listeners.push({ type, listener, capture }); }
    removeEventListener(type, listener) { this.listeners = this.listeners.filter(entry => entry.type !== type || entry.listener !== listener); }
    getBoundingClientRect() { return this.rect || { top: 0, right: 0, bottom: 0, left: 0, width: 0, height: 0 }; }
}

function parseFakeHTML(html) {
    const root = new FakeElement('root');
    const stack = [root];
    const token = /<!--[\s\S]*?-->|<\/([a-zA-Z][\w-]*)\s*>|<([a-zA-Z][\w-]*)((?:\s+[^\s=>\/]+(?:="[^"]*")?)*)\s*(\/?)>|([^<]+)/g;
    let match;
    while ((match = token.exec(html))) {
        const [, closing, tag, attributeSource, selfClosing, text] = match;
        const parent = stack[stack.length - 1];
        if (text !== undefined) {
            parent.childNodes.push({ text: decodeEntities(text) });
        } else if (tag) {
            const attributes = [...attributeSource.matchAll(/([^\s=>\/]+)(?:="([^"]*)")?/g)]
                .map(([, name, value]) => [name, decodeEntities(value ?? '')]);
            const element = new FakeElement(tag, attributes);
            parent.append([element]);
            if (!selfClosing && !VOID_TAGS.has(tag.toLowerCase())) stack.push(element);
        } else if (closing) {
            const index = stack.map(element => element.tagName).lastIndexOf(closing.toUpperCase());
            if (index > 0) stack.length = index;
        }
    }
    const nodes = root.childNodes;
    nodes.forEach(node => { node.parentElement = null; });
    return nodes;
}

// Dispatches to listeners on the list itself, as a delegated event arrives there.
function dispatchFeed(list, type, target, init = {}) {
    const event = {
        type, target, currentTarget: list, defaultPrevented: false, propagationStopped: false,
        stopPropagation() { this.propagationStopped = true; },
        preventDefault() { this.defaultPrevented = true; },
        ...init,
    };
    list.listeners.filter(entry => entry.type === type).forEach(entry => entry.listener(event));
    return event;
}

function feedListApp(context = browserContext()) {
    const app = immediateComponent(loadScripts(context, ['themes.js', 'app.js'], 'tweetApp')());
    const list = new FakeElement('div');
    list.connected = true;
    const area = new FakeElement('div');
    area.rect = { top: 100, right: 700, bottom: 5000, left: 100, width: 600, height: 4900 };
    app.$refs = { feedList: list, feedArea: area };
    return { app, list, context };
}

const WEB_FIXTURE = JSON.parse(fs.readFileSync(
    path.join(ROOT, 'tests', 'fixtures', 'web_raw_tweets.json'),
    'utf8',
));

const tests = [];
function test(name, fn) {
    tests.push({ name, fn });
}

test('tweet counts use compact detail precision and whole feed units', () => {
    const context = browserContext();
    const app = loadScripts(context, ['themes.js', 'app.js'], 'tweetApp')();
    const cases = [
        [0, ''], [null, ''], [undefined, ''], [-1, ''], ['invalid', ''],
        [999, '999'], [1000, '1K'], [1250, '1.2K'], [999949, '999K'],
        [999950, '999K'], [1000000, '1M'], [1250000, '1.2M'],
        [999949999, '999M'], [999950000, '999M'], [1000000000, '1B'],
        [1250000000, '1.2B'], [999950000000, '999B'], [1000000000000, '1T'],
        ['1000000', '1M'],
    ];
    for (const [count, expected] of cases) {
        for (const isMain of [false, true]) {
            const html = app.renderActionBar({
                tweet_id: '123',
                text: 'Archived tweet',
                raw_json: {
                    legacy: {
                        reply_count: count, retweet_count: Number(count) || 0, quote_count: 0,
                        favorite_count: count, bookmark_count: count,
                    },
                    views: { count },
                },
            }, isMain);
            const labels = [...html.matchAll(/<span class="tweet-action-count">([^<]*)<\/span>/g)]
                .map(match => match[1]);
            assert.deepEqual(labels, Array(4).fill(expected), `count ${count}, main ${isMain}`);
            assert.equal([...html.matchAll(/<div class="flex items-center/g)].length, isMain ? 5 : 6, `action groups, main ${isMain}`);
        }
    }
    assert.equal(app.formatActionCount(11753), '11K');
    assert.equal(app.formatActionCount(33119), '33K');
    assert.equal(app.formatActionCount(57501), '57K');
    assert.equal(app.formatActionCount(915225), '915K');
    assert.equal(app.formatActionCount(87610), '87K');
    assert.equal(app.formatActionCount(7177), '7.1K');
    assert.equal(app.formatActionCount(20656), '20K');
    assert.equal(app.formatActionCount(1139), '1.1K');
    assert.equal(app.formatActionCount(999950), '999K');
    assert.equal(app.formatActionCount(1000000), '1M');
    assert.equal(app.formatCompactCount(999500), '999.5K');
    assert.equal(app.formatCompactCount(999950), '999.9K');
    assert.equal(app.formatCompactCount(853200), '853.2K');
    assert.equal(app.formatCompactCount(859320), '859.3K');
    assert.equal(app.formatCompactCount(0), '');
    const html = app.renderActionBar({
        tweet_id: '123',
        text: 'Archived tweet',
        raw_json: { legacy: { retweet_count: 600000, quote_count: 400000 } },
    });
    assert.match(html, /tweet-action-count">1M<\/span>/);
    const feed = app.renderActionBar({
        tweet_id: '123', text: 'Archived tweet',
        raw_json: { legacy: { bookmark_count: 12345 }, views: { count: 692000 } },
    });
    assert.match(feed, /title="12\.3K bookmarks" aria-label="12\.3K bookmarks"/);
    assert.doesNotMatch(feed, /12K<\/span>/);
    assert.match(feed, /tweet-action-count">692K<\/span>/);
    for (const views of [undefined, { count: 0 }]) {
        const detail = app.renderActionBar({ tweet_id: '123', text: 'Archived tweet', raw_json: { legacy: {}, ...(views ? { views } : {}) } }, true);
        assert.equal([...detail.matchAll(/class="tweet-action-count"/g)].length, 4);
        assert.doesNotMatch(detail, /tweet-action-count">(?:0|undefined)/);
    }
    const page = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'index.html'), 'utf8');
    for (const prefix of ['threadData.main', 'panelThreadData.main']) {
        const at = page.indexOf(`class="tweet-timestamp`, page.indexOf(`renderCommunityNote(${prefix}.raw_json, false, true)`));
        const end = page.indexOf(`renderActionBar(${prefix}, true)`, at);
        const timestamp = page.slice(at, end);
        assert.match(timestamp, new RegExp(`formatCompactCount\\(${prefix}\\.raw_json\\.views\\.count\\)`));
        assert.match(timestamp, /<span>Views<\/span>/);
        assert.match(timestamp, /tweet-timestamp-meta[\s\S]*?roll-wrapper[\s\S]*?tweet-detail-views/);
        assert.match(timestamp, /tweet-detail-quotes/);
    }
});

test('theme color helpers and catalog generate complete deterministic themes', () => {
    const context = browserContext();
    const exported = loadScripts(
        context,
        ['themes.js'],
        '({_hexToRgb, _rgbToHex, _rgbToHsl, _hslToRgb, _adjustL, _mix, _rgba, _surface, _generateTheme, THEME_SEEDS, THEMES})',
    );

    assert.deepEqual(Array.from(exported._hexToRgb('#1d9bf0')), [29, 155, 240]);
    assert.equal(exported._rgbToHex(29, 155, 240), '#1d9bf0');
    const hsl = exported._rgbToHsl(29, 155, 240);
    const rgb = exported._hslToRgb(...hsl);
    assert.equal(exported._rgbToHex(...rgb), '#1d9bf0');
    assert.equal(exported._mix('#000000', '#ffffff', 0.5), '#808080');
    assert.equal(exported._rgba('#010203', 0.5), 'rgba(1,2,3,0.5)');
    assert.match(exported._adjustL('#000000', 10), /^#[0-9a-f]{6}$/);
    assert.match(exported._surface('#000000', '#71767b', 8), /^#[0-9a-f]{6}$/);

    const themeKeys = Object.keys(exported.THEMES);
    assert.ok(themeKeys.length >= 15);
    assert.deepEqual(themeKeys, Object.keys(exported.THEME_SEEDS));
    for (const theme of Object.values(exported.THEMES)) {
        for (const key of [
            '--bg-primary',
            '--bg-secondary',
            '--text-primary',
            '--text-secondary',
            '--border-color',
            '--accent-color',
            '--danger-color',
            '--destructive-color',
        ]) {
            assert.ok(theme[key], `missing ${key}`);
        }
    }
    assert.ok(exported.THEMES['classic-dark']._accents.length >= 6);
    // Classic danger is the like pink, so Delete uses X's red; other themes reuse their danger red.
    assert.equal(exported.THEMES['classic-dark']['--destructive-color'], '#f4212e');
    assert.equal(exported.THEMES['classic-light']['--destructive-color'], '#f4212e');
    assert.equal(exported.THEMES['classic-light']['--danger-color'], '#f91880');
    assert.equal(exported.THEMES.dracula['--destructive-color'], exported.THEMES.dracula['--danger-color']);
});

test('demo follows system appearance until an explicit theme is chosen', () => {
    for (const dark of [false, true]) {
        const context = browserContext();
        context.window.TWEETNOOK_DEMO = true;
        let onChange;
        const scheme = {
            matches: dark,
            addEventListener(name, callback) {
                assert.equal(name, 'change');
                onChange = callback;
            },
        };
        context.window.matchMedia = query => {
            assert.equal(query, '(prefers-color-scheme: dark)');
            return scheme;
        };
        const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
        const start = () => {
            const app = immediateComponent(tweetApp());
            app.fetchSetup = async () => {};
            app.fetchNotices = async () => {};
            app.fetchActivityStatus = async () => {};
            app.fetchTweets = async () => {};
            app.fetchStats = async () => {};
            app.fetchArchiveEnrichmentStatus = async () => {};
            app.fetchAutomatedTagging = async () => {};
            app.initApp();
            return app;
        };
        const app = start();
        assert.equal(app.currentTheme, dark ? 'classic-dark' : 'classic-light');
        assert.equal(app.darkMode, dark);
        assert.equal(context.__state.rootClasses.has('light'), !dark);
        assert.equal(context.localStorage.getItem('tvx-theme'), null);
        assert.equal(app.splitPanel, true);
        assert.equal(app.shouldUseSplitPanel(), true);
        context.window.innerWidth = 390;
        assert.equal(app.shouldUseSplitPanel(), false);

        scheme.matches = !dark;
        onChange();
        assert.equal(app.currentTheme, dark ? 'classic-light' : 'classic-dark');
        assert.equal(context.localStorage.getItem('tvx-theme'), null);
        assert.equal(start().currentTheme, app.currentTheme);

        app.applyTheme('classic-light');
        assert.equal(context.localStorage.getItem('tvx-theme'), 'classic-light');
        scheme.matches = true;
        onChange();
        assert.equal(app.currentTheme, 'classic-light');
        assert.equal(start().currentTheme, 'classic-light');
    }
});

test('desktop split-panel defaults respect saved layout and theme choices in demos and local archives', () => {
    for (const isDemo of [false, true]) {
        for (const savedSplit of [null, 'false', 'true']) {
            const context = browserContext();
            context.window.TWEETNOOK_DEMO = isDemo;
            context.localStorage.setItem('tvx-theme', 'classic-dark');
            if (savedSplit !== null) context.localStorage.setItem('tvx-split-panel', savedSplit);
            const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
            const app = immediateComponent(tweetApp());
            app.fetchSetup = async () => {};
            app.fetchNotices = async () => {};
            app.fetchActivityStatus = async () => {};
            app.fetchTweets = async () => {};
            app.fetchStats = async () => {};
            app.fetchArchiveEnrichmentStatus = async () => {};
            app.fetchAutomatedTagging = async () => {};
            app.initApp();
            assert.equal(app.currentTheme, 'classic-dark');
            assert.equal(app.splitPanel, savedSplit === null ? true : savedSplit === 'true');
        }
    }
});

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
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook/web/index.html'), 'utf8');
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
    assert.ok(component.isOptions.some(option => option.value === 'resurrected'));
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

    // Fast typing sends one request, and a late response never replaces newer input.
    calls.length = 0;
    let release;
    context.fetch = url => {
        calls.push(url);
        return new Promise(resolve => {
            release = () => resolve({ ok: true, json: async () => ({ tags: [{ tag: 'Stale', count: 1 }] }) });
        });
    };
    for (const query of ['tag:n', 'tag:ni', 'tag:nig']) {
        component.searchQuery = query;
        component.cursorPos = query.length;
        component.handleInput();
    }
    await new Promise(resolve => setTimeout(resolve, 150));
    assert.deepEqual(calls, ['/api/tags/autocomplete?q=nig']);
    component.searchQuery = 'has:';
    component.cursorPos = component.searchQuery.length;
    component.handleInput();
    release();
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.equal(component.options[0].value, 'media');
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

test('tweet app starts with coherent list, panel, modal, and theme state', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    assert.equal(app.viewMode, 'list');
    assert.equal(app.page, 1);
    assert.equal(app.collectionFilter, 'likes');
    assert.equal(app.sortOrder, 'liked_latest');
    assert.equal(app.panelMode, null);
    assert.deepEqual(Array.from(app.panelStack), []);
    assert.equal(app.tagModalOpen, false);
    assert.equal(app.currentTheme, 'classic-light');
    assert.equal(app.showEmptyUnavailableReasons, false);
    assert.equal(app.statsGeneratedAt, null);
    assert.equal(app.statsRefreshing, false);
    assert.ok(Object.keys(app.THEMES).length >= 15);
    assert.equal(app.avatarUrl('user/42'), '/api/avatar/user%2F42?v=3');
});

test('lazy feed images are observed per card and loaded and decoded ahead of scrolling', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    const observed = [];
    app.mediaPrefetchObserver = { observe: node => observed.push(node) };
    const image = card => {
        const img = { tagName: 'IMG', loading: 'lazy', decoded: 0, closest: () => card };
        img.decode = () => { img.decoded += 1; return Promise.resolve(); };
        return img;
    };
    const card = { tagName: 'DIV' };
    const images = [image(card), image(card)];
    card.querySelectorAll = () => images;
    const threadImage = image(null);

    app.observeMediaPrefetch(card);
    app.observeMediaPrefetch(threadImage);
    assert.deepEqual(observed, [card, card, threadImage]);

    app.prefetchMedia(card);
    app.prefetchMedia(threadImage);
    for (const img of [...images, threadImage]) {
        assert.equal(img.loading, 'eager');
        assert.equal(img.decoded, 1);
    }
    observed.length = 0;
    app.observeMediaPrefetch(threadImage);
    assert.deepEqual(observed, []);
});

test('avatar load and error state hides initials only for real images and recovers on reuse', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    const classes = () => {
        const values = new Set();
        return {
            contains: value => values.has(value),
            toggle(value, enabled) { enabled ? values.add(value) : values.delete(value); },
            remove(value) { values.delete(value); },
        };
    };
    const parent = { classList: classes() };
    const image = { naturalWidth: 128, naturalHeight: 128, classList: classes(), parentElement: parent };

    app.avatarLoaded(image); // A real alpha image must suppress the fallback.
    assert.equal(parent.classList.contains('avatar-loaded'), true);
    assert.equal(image.classList.contains('avatar-image-loaded'), true);

    image.naturalWidth = image.naturalHeight = 1;
    app.avatarLoaded(image); // HTTP 200 transparent placeholders do not trigger error.
    assert.equal(parent.classList.contains('avatar-loaded'), false);
    assert.equal(image.classList.contains('avatar-image-loaded'), false);

    image.naturalWidth = image.naturalHeight = 48;
    app.avatarLoaded(image);
    app.avatarFailed(image);
    assert.equal(parent.classList.contains('avatar-loaded'), false);
    assert.equal(image.classList.contains('avatar-image-loaded'), false);
    app.avatarLoaded(image); // Reusing the same element must recover after an error.
    assert.equal(parent.classList.contains('avatar-loaded'), true);
    assert.equal(image.classList.contains('avatar-image-loaded'), true);
});

test('avatar initials preserve emoji and combining graphemes across author surfaces', () => {
    const context = browserContext();
    const { tweetApp, avatarInitial } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp, avatarInitial})');
    const app = immediateComponent(tweetApp());
    app.positionProfileCard = () => {};
    for (const initial of ['😀', '👩🏽‍💻', '🇨🇦', 'e\u0301', 'A']) {
        const name = initial + ' Author';
        assert.equal(avatarInitial(name), initial);
        assert.equal(app.getInitial({author: {display_name: name}}), initial);
        assert.equal(app.getInitial({author: {username: name}}), initial);
        assert.equal(app.getQuoteAuthor({author: {display_name: name}}).initial, initial);
        assert.equal(app.getQuoteAuthor({core: {user_results: {result: {legacy: {name}}}}}).initial, initial);
        app.profileCard = { name: 'Original', snapshots: [{display_name: name}] };
        app.showProfileSnapshot(0);
        assert.equal(app.profileCard.initial, initial.toUpperCase());
    }
    assert.equal(app.getInitial({author: {}}), '?');
    assert.equal(avatarInitial(''), '?');
    assert.equal(avatarInitial(null), '?');
    // Older browsers still preserve a complete supplementary-plane code point.
    const fallbackContext = browserContext();
    vm.runInContext('Intl.Segmenter = undefined', fallbackContext);
    const fallback = loadScripts(fallbackContext, ['themes.js', 'app.js'], 'avatarInitial');
    assert.equal(fallback('😀 Author'), '😀');
    assert.equal(fallback('Alice'), 'A');
});

test('all avatar templates expose load and error handling, including reply and profile initials', () => {
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook/web/index.html'), 'utf8');
    const images = html.match(/<img\b[^>]*:src="avatarUrl\([^>]*>/g) || [];
    assert.equal(images.length, 20);
    for (const image of images) {
        assert.ok(image.includes('@load="avatarLoaded($el)"'));
        assert.ok(image.includes('@error="avatarFailed($el)"'));
        assert.ok(image.includes('class="avatar-image '));
    }
    const initialPairs = html.match(/<span[^>]*x-text="(?:getInitial\([^\"]*|getQuoteAuthor\([^\"]*\.initial|profileCard\?\.initial|avatarInitial\([^\"]*)"[^>]*><\/span>\s*<img[^>]*:src="avatarUrl/g) || [];
    assert.equal(initialPairs.length, 16);
    for (const pair of initialPairs) assert.ok(pair.includes('class="avatar-initial"'));
    // Feed cards (main and quote avatars) render in app.js; capture listeners on the
    // list handle their load and error events.
    const { app } = feedListApp();
    const card = parseFakeHTML(app.renderFeedCard(JSON.parse(JSON.stringify(WEB_FIXTURE.slim[0]))))[0];
    const avatars = card.querySelectorAll('img.avatar-image');
    assert.equal(avatars.length, 2);
    for (const avatar of avatars) assert.ok(avatar.parentElement.firstElementChild.matches('span.avatar-initial'));
});

test('lightbox navigation moves through media and clamps at both ends', () => {
    const context = browserContext();
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook/web/index.html'), 'utf8');
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    app.lightboxMedia = [{type: 'photo'}, {type: 'photo'}, {type: 'photo'}];
    app.lightboxIndex = 0;

    app.prevLightbox();
    assert.equal(app.lightboxIndex, 0);
    app.nextLightbox();
    assert.equal(app.lightboxIndex, 1);
    app.nextLightbox();
    app.nextLightbox();
    assert.equal(app.lightboxIndex, 2);
    app.prevLightbox();
    assert.equal(app.lightboxIndex, 1);

    app.lightboxMedia = [{type: 'photo'}];
    app.lightboxIndex = 0;
    app.nextLightbox();
    app.prevLightbox();
    assert.equal(app.lightboxIndex, 0);
    assert.match(html, /@click\.stop="prevLightbox\(\)"/);
    assert.match(html, /@click\.stop="nextLightbox\(\)"/);
    assert.match(html, /@click\.stop="closeLightbox\(\)"/);
    assert.match(html, /x-transition:enter="lightbox-overlay-enter"/);
    assert.match(html, /lightbox-position/);
});

test('lightbox touch drags track the finger, navigate, and protect the following click', () => {
    const context = browserContext();
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook/web/index.html'), 'utf8');
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    const imageTarget = { closest: (selector) => selector === 'img, video, button' ? {} : null };
    const backdropTarget = { closest: () => null };
    const touchEvent = (x, y, touches = [{clientX: x, clientY: y}], target = backdropTarget, timeStamp = 0) => ({
        target,
        touches,
        changedTouches: [{clientX: x, clientY: y}],
        timeStamp,
        preventDefault() { this.prevented = true; },
        stopPropagation() { this.stopped = true; },
    });

    app.lightboxOpen = true;
    app.lightboxMedia = [{type: 'photo'}, {type: 'photo'}, {type: 'photo'}];
    app.startLightboxGesture({...touchEvent(220, 180, undefined, imageTarget, 100), target: imageTarget});
    const move = touchEvent(160, 184, undefined, imageTarget, 150);
    app.moveLightboxGesture(move);
    assert.equal(move.prevented, true);
    assert.equal(app.lightboxGestureAxis, 'horizontal');
    assert.equal(app.lightboxGestureOffsetX, -60);
    assert.match(app.lightboxSlideStyle(1).transform, /calc\(100% \+ -60px\)/);
    app.endLightboxGesture({...touchEvent(145, 184, undefined, imageTarget, 175), target: imageTarget});
    assert.equal(app.lightboxIndex, 1);
    assert.equal(app.lightboxOpen, true);
    app.resetLightboxMotion();

    app.startLightboxGesture({...touchEvent(220, 180), target: backdropTarget});
    app.endLightboxGesture({...touchEvent(145, 184), target: backdropTarget});
    assert.equal(app.lightboxIndex, 2);
    const swipeClick = touchEvent(145, 184);
    app.handleLightboxBackdropClick(swipeClick);
    assert.equal(swipeClick.stopped, true);
    assert.equal(app.lightboxOpen, true);
    app.resetLightboxMotion();

    app.startLightboxGesture({...touchEvent(100, 180), target: backdropTarget});
    app.endLightboxGesture({...touchEvent(170, 175), target: backdropTarget});
    assert.equal(app.lightboxIndex, 1);
    assert.match(html, /@touchstart\.passive="startLightboxGesture\(\$event\)"/);
    assert.match(html, /@touchmove="moveLightboxGesture\(\$event\)"/);
    assert.match(html, /@touchend\.passive="endLightboxGesture\(\$event\)"/);
    assert.match(html, /lightboxSlideStyle\(mediaIndex\)/);
    assert.match(html, /lightboxVisibleIndices\(\)/);
    const css = fs.readFileSync(path.join(ROOT, 'tweetnook/web/static/css/styles.css'), 'utf8');
    assert.match(css, /\.lightbox-gesture-surface\s*\{[^}]*touch-action:\s*pinch-zoom;/s);
    assert.match(css, /\.lightbox-slide\s*\{[^}]*will-change:\s*transform;/s);
});

test('lightbox drag-to-dismiss fades and scales before closing, while invalid drags snap back', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    const touchEvent = (target, x, y, touches = [{clientX: x, clientY: y}], timeStamp = 0) => ({
        target,
        touches,
        changedTouches: [{clientX: x, clientY: y}],
        timeStamp,
        preventDefault() { this.prevented = true; },
    });

    app.lightboxOpen = true;
    app.lightboxMedia = [{type: 'photo'}];
    const imageTarget = {closest: (selector) => selector === 'img, video, button' ? {} : null};
    app.startLightboxGesture(touchEvent(imageTarget, 100, 100, undefined, 100));
    const dismissMove = touchEvent(imageTarget, 105, 170, undefined, 170);
    app.moveLightboxGesture(dismissMove);
    assert.equal(dismissMove.prevented, true);
    assert.equal(app.lightboxGestureOffsetY, 70);
    assert.match(app.lightboxSlideStyle(0).transform, /scale\(0\.9\d+/);
    assert.match(app.lightboxBackdropStyle().backgroundColor, /rgba\(0, 0, 0, 0\.[0-8]/);
    assert.equal(app.lightboxShadeStyle().opacity, app.lightboxBackdropStyle()['--lightbox-backdrop-opacity']); // The page shade follows the drag.
    app.endLightboxGesture(touchEvent(imageTarget, 105, 185, undefined, 190));
    assert.equal(app.lightboxOpen, true);
    assert.equal(app.lightboxDismissPending, true);
    assert.ok(app.lightboxGestureOffsetY >= 800);
    app.finishLightboxDismiss();
    assert.equal(app.lightboxOpen, false);
    app.resetLightboxMotion();

    app.lightboxOpen = true;
    app.startLightboxGesture(touchEvent({closest: () => ({})}, 100, 100));
    app.endLightboxGesture(touchEvent({closest: () => ({})}, 100, 190));
    assert.equal(app.lightboxOpen, true);

    app.startLightboxGesture(touchEvent({closest: () => null}, 100, 100));
    app.endLightboxGesture(touchEvent({closest: () => null}, 100, 150));
    assert.equal(app.lightboxOpen, true);
    assert.equal(app.lightboxGestureOffsetY, 0);
    app.resetLightboxMotion();

    app.startLightboxGesture(touchEvent({closest: () => null}, 100, 100, [
        {clientX: 100, clientY: 100},
        {clientX: 105, clientY: 105},
    ]));
    app.endLightboxGesture(touchEvent({closest: () => null}, 100, 190));
    assert.equal(app.lightboxOpen, true);
});

test('lightbox gestures use edge resistance and velocity-aware flick navigation', () => {
    const context = browserContext();
    context.window.innerWidth = 390;
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    const target = {closest: () => null};
    const event = (x, y, timeStamp) => ({
        target,
        timeStamp,
        touches: [{clientX: x, clientY: y}],
        changedTouches: [{clientX: x, clientY: y}],
        preventDefault() {},
    });

    app.lightboxOpen = true;
    app.lightboxMedia = [{type: 'photo'}, {type: 'photo'}];
    app.startLightboxGesture(event(100, 100, 100));
    app.moveLightboxGesture(event(160, 102, 180));
    assert.equal(app.lightboxGestureOffsetX, 13.2);
    app.endLightboxGesture(event(160, 102, 200));
    assert.equal(app.lightboxIndex, 0);
    app.resetLightboxMotion();

    app.startLightboxGesture(event(200, 100, 300));
    app.moveLightboxGesture(event(174, 101, 320));
    app.endLightboxGesture(event(170, 101, 330));
    assert.equal(app.lightboxIndex, 1);
    assert.equal(app.lightboxGestureAnimating, true);
});

test('lightbox playback follows the active animated slide and pauses on close', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    const counts = {activePlay: 0, activePause: 0, neighborPlay: 0, neighborPause: 0};
    const video = (active, prefix) => ({
        closest() {
            return {classList: {contains: () => active}};
        },
        hasAttribute(name) {
            return name === 'autoplay';
        },
        pause() {
            counts[`${prefix}Pause`] += 1;
        },
        play() {
            counts[`${prefix}Play`] += 1;
            return Promise.resolve();
        },
    });
    const active = video(true, 'active');
    const neighbor = video(false, 'neighbor');
    app.$refs.lightboxStage = {querySelectorAll: () => [active, neighbor]};
    app.lightboxOpen = true;

    app.syncLightboxPlayback();
    assert.deepEqual(counts, {
        activePlay: 1,
        activePause: 0,
        neighborPlay: 0,
        neighborPause: 1,
    });

    app.closeLightbox();
    assert.equal(app.lightboxOpen, false);
    assert.equal(counts.activePause, 1);
    assert.equal(counts.neighborPause, 2);
});

test('reply-recipient links open an anchored profile card without searching', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    let searches = 0;
    app.searchFrom = () => { searches += 1; };
    const anchor = {
        getBoundingClientRect() {
            return { left: 40, right: 100, top: 20, bottom: 50 };
        },
    };

    app.openUsernameProfileCard({ currentTarget: anchor }, 'reply_target');

    assert.equal(searches, 0);
    assert.equal(app.profileCard.username, 'reply_target');
    assert.equal(app.profileCard.name, 'reply_target');
    assert.equal(app.profileCard.id, 'unknown');
    assert.deepEqual([app.profileCard.x, app.profileCard.y], [40, 58]);
});

test('profile cards show the newest captured profile and cycle through older ones', async () => {
    const context = browserContext();
    const requests = [];
    const pending = [];
    const snapshots = [
        { display_name: 'Alice Renamed', username: 'alice', description: 'New bio', followers_count: 20, following_count: 6, fetched_at: '2026-06-01T00:00:00Z' },
        { display_name: 'Alice', username: 'alice', description: 'Old bio', followers_count: 10, following_count: 5, fetched_at: '2026-01-01T00:00:00Z' },
    ];
    context.fetch = url => {
        requests.push(url);
        return new Promise(resolve => pending.push(() => resolve({
            ok: true,
            async json() { return { author_id: '42', snapshots }; },
        })));
    };
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    const anchor = { getBoundingClientRect: () => ({ left: 0, right: 10, top: 0, bottom: 10 }) };
    const settle = async () => { pending.shift()(); await new Promise(resolve => setTimeout(resolve, 0)); };
    const tweet = {
        author: { id: '42', username: 'alice', display_name: 'Alice' },
        synced_at: '2025-12-01T00:00:00Z',
        raw_json: { core: { user_results: { result: { legacy: { description: 'Tweet bio', followers_count: 1 } } } } },
    };

    app.openProfileCard({ currentTarget: anchor }, tweet, false, true);
    assert.equal(app.profileCard.description, 'Tweet bio');
    await settle();
    assert.equal(requests[0], '/api/authors/profile?author_id=42');
    assert.equal(app.profileCard.name, 'Alice Renamed');
    assert.equal(app.profileCard.description, 'New bio');
    assert.equal(app.profileCard.followersCount, 20);
    assert.equal(app.profileCard.syncDate, '2026-06-01T00:00:00Z');

    app.showProfileSnapshot(1);
    assert.equal(app.profileCard.snapshotIndex, 1);
    assert.equal(app.profileCard.description, 'Old bio');
    assert.equal(app.profileCard.followingCount, 5);
    assert.equal(app.profileCard.syncDate, '2026-01-01T00:00:00Z');
    app.showProfileSnapshot(2);
    assert.equal(app.profileCard.snapshotIndex, 1);

    // Mentions resolve their handle and pick up the author's avatar ID.
    app.openUsernameProfileCard({ currentTarget: anchor }, 'alice');
    await settle();
    assert.equal(requests[1], '/api/authors/profile?username=alice');
    assert.equal(app.profileCard.id, '42');
    assert.equal(app.profileCard.description, 'New bio');

    // A response for a card that was replaced or closed is ignored.
    app.openProfileCard({ currentTarget: anchor }, tweet, false, true);
    app.closeProfileCard();
    await settle();
    assert.equal(app.profileCard, null);

    // Profile cards near the bottom open above their anchor, then keep their
    // top while cycling so the arrows stay put, unless they would overflow.
    let cardHeight = 200;
    context.window.innerHeight = 600;
    context.document.getElementById = id => (id === 'profile-card-modal'
        ? { getBoundingClientRect: () => ({ width: 300, height: cardHeight }) }
        : null);
    const lowAnchor = { getBoundingClientRect: () => ({ left: 0, right: 10, top: 500, bottom: 520 }) };
    app.openProfileCard({ currentTarget: lowAnchor }, tweet, false, true);
    assert.equal(app.profileCard.y, 292);
    await settle();
    assert.equal(app.profileCard.y, 292);
    cardHeight = 150;
    app.showProfileSnapshot(1);
    assert.equal(app.profileCard.y, 292);
    cardHeight = 400;
    app.showProfileSnapshot(0);
    assert.equal(app.profileCard.y, 192);

    const html = fs.readFileSync(path.join(ROOT, 'tweetnook/web/index.html'), 'utf8');
    assert.ok(html.includes('@click="showProfileSnapshot(profileCard?.snapshotIndex + 1)"'));
    assert.ok(html.includes('@click="showProfileSnapshot(profileCard?.snapshotIndex - 1)"'));
    assert.ok(html.includes('aria-label="Older profile capture"'));
});

test('analytics uses one cached snapshot and retains it during manual refresh', async () => {
    const context = browserContext();
    const calls = [];
    const generatedAt = new Date().toISOString();
    const snapshot = {
        generated_at: generatedAt,
        age_seconds: 0,
        refreshing: false,
        refresh_failed: false,
        summary: { unique_posts: 12, latest_sync: 'Aug 10, 2026' },
        collections: [{ collection: 'Bookmarks', count: 12 }],
        health: { enrichment: { unavailable: { reasons: [] } } },
        storage: { total_bytes: 42 },
        tags: { unique_tags: 3 },
    };
    context.fetch = async (url, options = {}) => {
        calls.push([url, options]);
        return {
            ok: true,
            status: 200,
            async json() {
                return url === '/api/stats/refresh'
                    ? { ...snapshot, refreshing: true }
                    : snapshot;
            },
        };
    };
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    let polls = 0;
    app.scheduleStatsRefreshPoll = () => { polls += 1; };

    await app.openStatsModal();

    assert.equal(calls.length, 1);
    assert.equal(calls[0][0], '/api/stats/snapshot');
    assert.equal(app.statsSummary.unique_posts, 12);
    assert.equal(app.statsCollections[0].collection, 'Bookmarks');
    assert.equal(app.storageData.total_bytes, 42);
    assert.equal(app.statsTags.unique_tags, 3);
    assert.equal(app.loadingStatsSnapshot, false);
    assert.equal(app.statsAgeLabel(), 'Updated just now');

    await app.refreshStats();

    assert.equal(calls[1][0], '/api/stats/refresh');
    assert.equal(calls[1][1].method, 'POST');
    assert.equal(app.statsSummary.unique_posts, 12);
    assert.equal(app.statsRefreshing, true);
    assert.equal(app.statsAgeLabel(), 'Refreshing · updated just now');
    assert.equal(polls, 1);
});

test('analytics refresh polling does not replace an unchanged report', async () => {
    const context = browserContext();
    const calls = [];
    context.fetch = async url => {
        calls.push(url);
        return {
            ok: true,
            async json() {
                return {
                    generated_at: '2026-08-10T00:00:00+00:00',
                    refreshing: true,
                    refresh_failed: false,
                };
            },
        };
    };
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    const originalSummary = { unique_posts: 12 };
    app.showStatsModal = true;
    app.statsGeneratedAt = '2026-08-10T00:00:00+00:00';
    app.statsSummary = originalSummary;
    app.scheduleStatsRefreshPoll = () => {};

    await app.pollStatsRefresh();

    assert.deepEqual(calls, ['/api/stats/status']);
    assert.equal(app.statsSummary, originalSummary);
    assert.equal(app.statsRefreshing, true);
});

test('analytics suspends only playing videos and resumes them on close', async () => {
    const context = browserContext();
    const playing = {
        paused: false,
        isConnected: true,
        pauseCalls: 0,
        playCalls: 0,
        pause() { this.paused = true; this.pauseCalls += 1; },
        play() { this.paused = false; this.playCalls += 1; return Promise.resolve(); },
    };
    const alreadyPaused = {
        paused: true,
        isConnected: true,
        pauseCalls: 0,
        playCalls: 0,
        pause() { this.pauseCalls += 1; },
        play() { this.playCalls += 1; return Promise.resolve(); },
    };
    context.__state.videos.push(playing, alreadyPaused);
    context.fetch = async () => ({
        ok: true,
        async json() {
            return {
                generated_at: '2026-08-10T00:00:00+00:00',
                refreshing: false,
                refresh_failed: false,
                summary: {}, collections: [], health: {}, storage: {}, tags: {},
            };
        },
    });
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());

    await app.openStatsModal();
    assert.equal(playing.pauseCalls, 1);
    assert.equal(alreadyPaused.pauseCalls, 0);

    app.closeStatsModal();
    assert.equal(playing.playCalls, 1);
    assert.equal(alreadyPaused.playCalls, 0);
});

test('archive status filters empty reasons and summarizes retry state', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    app.statsHealth = {
        enrichment: {
            unavailable: {
                reasons: [
                    {
                        reason: 'deleted_by_author',
                        count: 1,
                        percent_of_missing: 33.3,
                        due: 0,
                        delayed: 0,
                        permanent: 1,
                    },
                    {
                        reason: 'unavailable_unknown',
                        count: 2,
                        percent_of_missing: 66.7,
                        due: 1,
                        delayed: 1,
                        permanent: 0,
                    },
                    {
                        reason: 'withheld',
                        count: 0,
                        percent_of_missing: 0,
                        due: 0,
                        delayed: 0,
                        permanent: 0,
                    },
                ],
            },
        },
    };

    assert.deepEqual(
        Array.from(app.getUnavailableReasons(), item => item.reason),
        ['unavailable_unknown', 'deleted_by_author'],
    );
    assert.deepEqual(
        Array.from(app.getUnavailableBarReasons(), item => item.reason),
        ['unavailable_unknown', 'deleted_by_author'],
    );
    assert.equal(
        app.getUnavailableReasonStatus(app.statsHealth.enrichment.unavailable.reasons[1]),
        '1 due now · 1 scheduled',
    );
    assert.equal(
        app.getUnavailableReasonStatus(app.statsHealth.enrichment.unavailable.reasons[0]),
        '1 permanent',
    );
    assert.ok(
        app.getUnavailableSegmentWidth(app.statsHealth.enrichment.unavailable.reasons[1])
        > app.getUnavailableSegmentWidth(app.statsHealth.enrichment.unavailable.reasons[0]),
    );

    app.showEmptyUnavailableReasons = true;
    assert.equal(app.getUnavailableReasons().length, 3);
    assert.deepEqual(
        Array.from(app.getUnavailableReasons(), item => item.reason),
        ['unavailable_unknown', 'deleted_by_author', 'withheld'],
    );
    assert.equal(app.getUnavailableBarReasons().length, 2);
    assert.equal(
        app.getUnavailableReasonStatus(app.statsHealth.enrichment.unavailable.reasons[2]),
        'No unavailable tweets',
    );
});

test('analytics markup exposes the Archive status cards and reason breakdown', () => {
    const html = fs.readFileSync(
        path.join(ROOT, 'tweetnook', 'web', 'index.html'),
        'utf8',
    );
    const appJs = fs.readFileSync(path.join(JS_DIR, 'app.js'), 'utf8');
    const styles = fs.readFileSync(
        path.join(ROOT, 'tweetnook', 'web', 'static', 'css', 'styles.css'),
        'utf8',
    );

    assert.match(html, /role="switch"[^>]*:aria-checked="showEmptyUnavailableReasons" @click="showEmptyUnavailableReasons = !showEmptyUnavailableReasons"/);
    assert.match(html, /@click="refreshStats\(\)"/);
    assert.match(html, /@keydown\.window\.escape="closeStatsModal\(\)"/);
    assert.match(html, /@click\.self="closeStatsModal\(\)"/);
    assert.match(html, /@click="closeStatsModal\(\)"/);
    assert.match(html, /x-text="statsAgeLabel\(\)"/);
    assert.match(html, /x-text="statsRefreshing \? 'Refreshing' : 'Refresh'"/);
    assert.match(html, /x-show="statsRefreshing"/);
    assert.match(html, /class="stats-modal-scroll /);
    assert.match(styles, /\.stats-modal-scroll\s*\{[^}]*overscroll-behavior: contain;/s);
    const statsWatcherStart = appJs.indexOf("this.$watch('showStatsModal'");
    const settingsWatcherStart = appJs.indexOf("this.$watch('showSettingsModal'");
    assert.ok(statsWatcherStart >= 0 && statsWatcherStart < settingsWatcherStart);
    const statsWatcher = appJs.slice(statsWatcherStart, settingsWatcherStart);
    assert.match(statsWatcher, /if \(val\) this\.lockModalScroll\(\);/);
    assert.match(statsWatcher, /!this\.showSettingsModal && !this\.showSetupModal/);
    assert.match(statsWatcher, /this\.unlockModalScroll\(\);/);
});

test('tweet fetching encodes search state, hydrates pagination, appends, and reports errors', async () => {
    const context = browserContext();
    const calls = [];
    let responseData = {
        tweets: [{ tweet_id: '1' }],
        page: 1,
        pages: 3,
        total: 5,
        has_more: true,
    };
    context.fetch = async url => {
        calls.push(url);
        return {
            ok: true,
            status: 200,
            async json() {
                return responseData;
            },
        };
    };
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    app.collectionFilter = 'likes';
    app.sortOrder = 'relevance';
    app.searchQuery = 'from:alice night sky';

    await app.fetchTweets();
    assert.equal(
        calls[0],
        '/api/tweets?collection=likes&sort=relevance&page=1&q=from%3Aalice%20night%20sky&cursor=',
    );
    assert.deepEqual(Array.from(app.tweets, tweet => tweet.tweet_id), ['1']);
    assert.equal(app.totalPages, 3);
    assert.equal(app.total, 5);
    assert.equal(app.hasMore, true);
    assert.equal(app.loading, false);
    app.total = null;
    assert.equal(app.resultCountLabel(), 'Counting… Results');
    app.total = 3482;
    assert.equal(app.resultCountLabel(), '3,482 Results');

    responseData = { tweets: [{ tweet_id: '2' }], page: 2, pages: 3, total: 5, has_more: false };
    await app.fetchTweets(true);
    assert.deepEqual(Array.from(app.tweets, tweet => tweet.tweet_id), ['1', '2']);
    assert.equal(app.hasMore, false);
    assert.equal(app.loadingMore, false);

    context.fetch = async () => ({
        ok: false,
        status: 503,
        async json() {
            return { detail: 'temporarily unavailable' };
        },
    });
    await app.fetchTweets();
    assert.equal(app.error, 'temporarily unavailable');
    assert.deepEqual(Array.from(app.tweets), []);
});

test('text counts arrive independently, survive append, and ignore an older search', async () => {
    const context = browserContext();
    const counts = [];
    context.fetch = async (url, options) => {
        if (url.startsWith('/api/tweets/count?')) {
            return new Promise(resolve => counts.push({url, options, resolve}));
        }
        return {ok: true, status: 200, json: async () => ({
            tweets: [{tweet_id: url.includes('page=2') ? '2' : '1'}],
            page: url.includes('page=2') ? 2 : 1, total: null, pages: null, has_more: true,
        })};
    };
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.searchQuery = 'gif is:reply';
    await app.fetchTweets();
    assert.equal(app.loading, false);
    assert.equal(app.tweets.length, 1);
    assert.equal(app.total, null);
    assert.equal(counts[0].url, '/api/tweets/count?collection=likes&q=gif%20is%3Areply');
    counts[0].resolve({ok: true, json: async () => ({total: 42})});
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(app.resultCountLabel(), '42 Results');
    await app.fetchTweets(true);
    assert.equal(app.total, 42);
    assert.equal(counts.length, 1);

    await app.fetchTweets();
    app.searchQuery = 'cats';
    await app.fetchTweets();
    assert.equal(counts[1].options.signal.aborted, true);
    counts[2].resolve({ok: true, json: async () => ({total: 8})});
    counts[1].resolve({ok: true, json: async () => ({total: 99})});
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(app.total, 8);

    await app.fetchTweets();
    counts[3].resolve({ok: false, json: async () => ({detail: 'failed'})});
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(app.resultCountLabel(), 'Count unavailable · Results');
    assert.equal(app.tweets.length, 1);
});

test('a complete first text page supplies its own count, including zero', async () => {
    const context = browserContext();
    let tweets = [{tweet_id: '1'}];
    const calls = [];
    context.fetch = async url => {
        calls.push(url);
        return {ok: true, status: 200, json: async () => ({tweets, page: 1, total: null, pages: null, has_more: false})};
    };
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.searchQuery = 'gif is:reply';
    await app.fetchTweets();
    assert.equal(app.resultCountLabel(), '1 Results');
    tweets = [];
    await app.fetchTweets();
    assert.equal(app.resultCountLabel(), '0 Results');
    assert.equal(calls.length, 2);
});

test('feed cursors survive retries and ignore duplicate membership results', async () => {
    const context = browserContext();
    const calls = [];
    const response = data => ({ok: true, status: 200, json: async () => data});
    let fail = false;
    context.fetch = async url => {
        calls.push(url);
        if (fail) throw new Error('offline');
        return response(calls.length === 1
            ? {tweets: [{tweet_id: '1'}], page: 1, pages: null, total: null, has_more: true, next_cursor: 'cursor/+='}
            : {tweets: [{tweet_id: '1'}, {tweet_id: '2'}], page: 2, pages: null, total: null, has_more: false, next_cursor: null});
    };
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    await app.fetchTweets();
    fail = true;
    await app.loadMore();
    assert.equal(app.page, 1);
    assert.equal(app.nextCursor, 'cursor/+=');
    assert.equal(app.hasMore, true);
    assert.equal(app.loadingMore, false);
    fail = false;
    await app.loadMore();
    assert.equal(calls[1], calls[2]);
    assert.match(calls[2], /page=2&cursor=cursor%2F%2B%3D$/);
    assert.equal(app.page, 2);
    assert.deepEqual(Array.from(app.tweets, tweet => tweet.tweet_id), ['1', '2']);
});

test('feed ignores stale responses and pages without a cursor', async () => {
    const context = browserContext();
    const pending = [];
    context.fetch = url => new Promise(resolve => pending.push({url, resolve}));
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    const first = app.fetchTweets();
    app.searchQuery = 'new query';
    const second = app.fetchTweets();
    const resolve = (index, data) => pending[index].resolve({ok: true, status: 200, json: async () => data});
    resolve(0, {tweets: [{tweet_id: 'old'}], page: 1, pages: 1, total: 1, has_more: false});
    await first;
    assert.equal(app.loading, true);
    assert.equal(app.tweets.length, 0);
    resolve(1, {tweets: [{tweet_id: 'new'}], page: 1, pages: 2, total: 2, has_more: true});
    await second;
    assert.equal(app.loading, false);
    assert.equal(app.hasMore, true);
    assert.deepEqual(Array.from(app.tweets, tweet => tweet.tweet_id), ['new']);
    const more = app.loadMore();
    assert.match(pending[2].url, /page=2/);
    assert.doesNotMatch(pending[2].url, /cursor=/);
    resolve(2, {tweets: [{tweet_id: 'next'}], page: 2, pages: 2, total: 2, has_more: false});
    await more;
    assert.equal(app.hasMore, false);
});

test('expired like cursor reloads the list once with a fresh cursor', async () => {
    const context = browserContext();
    const calls = [];
    context.fetch = async url => {
        calls.push(url);
        const expired = calls.length === 2;
        return {ok: !expired, status: expired ? 409 : 200, json: async () => expired
            ? {detail: 'Like order changed.'}
            : {tweets: [{tweet_id: String(calls.length)}], page: 1, pages: 2, total: 30, has_more: true, next_cursor: 'next'}};
    };
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    await app.fetchTweets();
    await app.loadMore();
    assert.equal(calls.length, 3);
    assert.match(calls[2], /page=1&cursor=$/);
    assert.equal(app.page, 1);
    assert.equal(app.error, null);
    assert.equal(app.loading, false);
    assert.equal(app.loadingMore, false);
    assert.deepEqual(Array.from(app.tweets, tweet => tweet.tweet_id), ['3']);
});

test('random feeds keep one seed across pages and rotate it for a new search', async () => {
    const context = browserContext();
    context.Math = Object.create(Math);
    context.Math.random = () => 0.5;
    const calls = [];
    context.fetch = async url => {
        calls.push(url);
        return {
            ok: true,
            status: 200,
            async json() {
                return { tweets: [], page: 1, pages: 1, total: 0, has_more: false };
            },
        };
    };
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.sortOrder = 'random';
    app.randomSeed = 12345;

    await app.fetchTweets();
    app.page = 2;
    await app.fetchTweets(true);
    assert.match(calls[0], /random_seed=12345/);
    assert.match(calls[1], /random_seed=12345/);

    app.fetchTweets = () => {};
    app.search();
    assert.notEqual(app.randomSeed, 12345);
});

test('like order is exposed for Likes, preserved during search, and reset on collection change', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    const requests = [];
    app.fetchTweets = () => requests.push([app.collectionFilter, app.sortOrder, app.page]);
    for (const sort of ['liked_latest', 'liked_earliest']) {
        app.collectionFilter = 'likes';
        app.sortOrder = sort;
        app.searchQuery = 'needle';
        app.page = 5;
        app.search();
        assert.deepEqual(requests.at(-1), ['likes', sort, 1]);
        app.collectionFilter = 'bookmarks';
        app.search();
        assert.deepEqual(requests.at(-1), ['bookmarks', 'newest', 1]);
    }
    app.collectionFilter = 'likes';
    app.searchQuery = '';
    assert.equal(
        JSON.stringify(app.feedOptions('sort').slice(0, 2).map(option => [option.value, option.label])),
        JSON.stringify([
            ['liked_latest', 'Recently liked'],
            ['liked_earliest', 'Earliest liked'],
        ]),
    );
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'index.html'), 'utf8');
    assert.match(html, /x-for="option in feedOptions\('collection'\)"/);
    assert.match(html, /x-for="\(option, index\) in feedOptions\('sort'\)"/);
});

test('feed menus retain null focus transitions and close when focus moves outside', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'index.html'), 'utf8');
    const expression = html.match(/@focusout="([^"]+)"/)[1];
    const handleFocusOut = new Function('$el', '$event', 'closeFeedMenu', expression);
    const inside = {};
    const outside = {};
    const container = { contains: target => target === inside };
    app.$refs = { feedMenus: { querySelectorAll: () => [] } };
    for (const kind of ['collection', 'sort']) {
        app.openFeedMenu(kind, { focus() {} });
        for (const relatedTarget of [null, inside]) {
            handleFocusOut(container, { relatedTarget }, () => app.closeFeedMenu());
            assert.equal(app.feedMenu, kind);
        }
        handleFocusOut(container, { relatedTarget: outside }, () => app.closeFeedMenu());
        assert.equal(app.feedMenu, null);
    }
});

test('list searching, incremental loading, and back-to-top obey state guards', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    let fetches = 0;
    app.fetchTweets = append => {
        fetches += append ? 10 : 1;
    };

    app.searchQuery = '';
    app.sortOrder = 'default';
    app.search();
    assert.equal(app.sortOrder, 'liked_latest');
    assert.equal(fetches, 1);

    app.loading = false;
    app.loadingMore = false;
    app.page = 1;
    app.totalPages = 2;
    app.hasMore = true;
    app.loadMore();
    assert.equal(app.page, 1); // Only a successful fetch advances the committed page.
    assert.equal(fetches, 11);
    app.hasMore = false;
    app.loadMore();
    assert.equal(fetches, 11);

    const html = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'index.html'), 'utf8');
    assert.match(html, /x-show="!loading && hasMore"/);

    app.scrollToTop();
    const scrollOptions = context.__state.scrollCalls.at(-1)[0];
    assert.equal(scrollOptions.top, 0);
    assert.equal(scrollOptions.behavior, 'smooth');

    context.window.matchMedia = query => ({ matches: query === '(prefers-reduced-motion: reduce)' });
    app.scrollToTop();
    const reducedScroll = context.__state.scrollCalls.at(-1)[0];
    assert.equal(reducedScroll.top, 0);
    assert.equal(reducedScroll.behavior, 'instant');
});

test('themes, accents, fonts, sizes, and split panel persist preferences', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());

    app.applyTheme('classic-light');
    assert.equal(app.currentTheme, 'classic-light');
    assert.equal(context.__state.cssProperties.get('--bg-primary'), '#ffffff');
    assert.ok(context.__state.rootClasses.has('light'));
    assert.equal(context.localStorage.getItem('tvx-theme'), 'classic-light');

    const accent = app.THEMES['classic-light']._accents[1];
    app.setAccent(accent);
    assert.equal(context.__state.cssProperties.get('--accent-color'), accent.color);
    app.setFont('inter', '"Inter", sans-serif');
    assert.equal(context.__state.appendedLinks.length, 0);
    app.setFontSize('large', '18px');
    assert.equal(context.__state.cssProperties.get('--font-size-base'), '18px');
    assert.equal(app.setTweetDensity, undefined);

    app.panelStack = [{ type: 'thread' }];
    app.panelMode = 'thread';
    app.toggleSplitPanel(false);
    assert.deepEqual(Array.from(app.panelStack), []);
    assert.equal(app.panelMode, null);
});

test('detail routes parse and generate canonical string tweet IDs', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    const largeId = '12345678901234567890';

    assert.deepEqual({ ...app.parseRoute('/') }, { viewMode: 'list' });
    assert.deepEqual({ ...app.parseRoute(`/post/${largeId}`) }, { viewMode: 'thread', tweetId: largeId });
    assert.deepEqual(
        { ...app.parseRoute(`/post/${largeId}/quotes`) },
        { viewMode: 'quotes', quotesTweetId: largeId },
    );
    assert.equal(typeof app.parseRoute(`/post/${largeId}`).tweetId, 'string');
    assert.equal(app.routeUrl({ viewMode: 'thread', tweetId: largeId }), `/post/${largeId}`);
    assert.equal(app.routeUrl({ viewMode: 'quotes', quotesTweetId: largeId }), `/post/${largeId}/quotes`);
    assert.throws(() => app.routeUrl({ viewMode: 'thread', tweetId: Number(largeId) }), /Invalid TweetNook route/);
    assert.equal(app.parseRoute('/post/not-a-number'), null);
    assert.equal(app.parseRoute('/post/123/other'), null);
});

test('single-column thread quote count tolerates route teardown', () => {
    const html = fs.readFileSync(
        path.join(ROOT, 'tweetnook', 'web', 'index.html'),
        'utf8',
    );
    assert.match(html, /threadData\?\.main\?\.local_quote_count > 0/);
});

test('initialization preserves direct detail paths without adding history entries', () => {
    for (const [pathname, viewMode] of [['/post/123', 'thread'], ['/post/123/quotes', 'quotes']]) {
        const context = browserContext(pathname);
        const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
        const app = immediateComponent(tweetApp());
        app.fetchSetup = async () => {};
        app.fetchNotices = async () => {};
        app.fetchActivityStatus = async () => {};

        app.initApp();

        assert.equal(app.pendingRoute.viewMode, viewMode);
        assert.equal(context.window.location.pathname, pathname);
        assert.equal(context.__state.historyCalls.filter(call => call[0] === 'replace').length, 1);
        assert.equal(context.__state.historyCalls.filter(call => call[0] === 'push').length, 0);
        assert.equal(context.history.state.tweetNookDepth, 0);
    }
});

test('popstate applies URL routes without pushing and restores list scroll', async () => {
    const context = browserContext();
    context.localStorage.setItem('tvx-split-panel', 'false');
    context.fetch = async url => ({
        ok: true,
        async json() {
            if (url.includes('/quotes')) return { tweets: [], total: 0, limit: 20 };
            return { main: { tweet_id: url.split('/').at(-1) }, parents: [], children: [] };
        },
    });
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.fetchSetup = async () => {};
    app.fetchNotices = async () => {};
    app.fetchActivityStatus = async () => {};
    app.initApp();
    app.archiveResourcesLoaded = true;
    context.__state.historyCalls.length = 0;

    await context.window.dispatchEvent({
        type: 'popstate',
        state: { viewMode: 'thread', tweetId: '123', tweetNookDepth: 1 },
    });
    assert.equal(app.viewMode, 'thread');
    assert.equal(app.threadData.main.tweet_id, '123');

    context.window.location.pathname = '/post/123/quotes';
    await context.window.dispatchEvent({ type: 'popstate', state: null });
    assert.equal(app.viewMode, 'quotes');
    assert.equal(app.quotesTweetId, '123');

    await context.window.dispatchEvent({
        type: 'popstate',
        state: { viewMode: 'list', scrollY: 321, tweetNookDepth: 0 },
    });
    assert.equal(app.viewMode, 'list');
    assert.ok(context.__state.scrollCalls.some(call => call[0] === 0 && call[1] === 321));
    assert.equal(context.__state.historyCalls.filter(call => call[0] === 'push').length, 0);
});

test('Back and Forward dismiss article focus before restoring routes and scroll in both layouts', async () => {
    for (const splitPanel of [false, true]) {
        const context = browserContext('/post/123');
        const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
        const app = immediateComponent(tweetApp());
        app.fetchSetup = async () => {};
        app.fetchNotices = async () => {};
        app.fetchActivityStatus = async () => {};
        app.initApp();
        app.archiveResourcesLoaded = true;
        app.splitPanel = splitPanel;
        app.$refs.detailPanel = {scrollTop: 0};
        let focusOpen = true;
        let closes = 0;
        context.document.body.style.overflow = 'hidden';
        context.window.TweetNookArticles = {closeFocus(options) {
            assert.equal(options.restoreFocus, false, 'route changes must not refocus the old article');
            focusOpen = false;
            context.document.body.style.overflow = '';
            closes++;
        }};
        const originalScrollTo = context.window.scrollTo;
        context.window.scrollTo = (...args) => {
            assert.equal(focusOpen, false, 'focus cleanup must finish before route scroll restoration');
            originalScrollTo(...args);
        };
        app.showFullThread = async id => {
            assert.equal(focusOpen, false);
            app.viewMode = 'thread';
            app.threadData = {main: {tweet_id: id}};
        };
        app.applyPanelRoute = async route => {
            assert.equal(focusOpen, false);
            app.panelMode = route.viewMode;
            app.panelThreadData = {main: {tweet_id: route.tweetId}};
        };
        await context.window.dispatchEvent({type: 'popstate', state: {viewMode: 'list', scrollY: 321, tweetNookDepth: 0}});
        assert.equal(focusOpen, false);
        assert.equal(context.document.body.style.overflow, '');
        assert.equal(app.viewMode, 'list');
        assert.ok(context.__state.scrollCalls.some(call => call[1] === 321));
        await context.window.dispatchEvent({type: 'popstate', state: {viewMode: 'thread', tweetId: '123', tweetNookDepth: 1}});
        assert.equal(splitPanel ? app.panelThreadData.main.tweet_id : app.threadData.main.tweet_id, '123');
        assert.equal(focusOpen, false, 'Forward must not reopen the dismissed dialog');
        focusOpen = true;
        context.document.body.style.overflow = 'hidden';
        await app.applyRoute({viewMode: 'list'}, {state: {viewMode: 'list', scrollY: 22}});
        assert.equal(focusOpen, false, 'programmatic navigation uses the same cleanup');
        assert.equal(closes, 3);
    }
});

test('split-panel routes follow browser history and rebuild cached content', async () => {
    const context = browserContext();
    const requests = [];
    context.fetch = async url => {
        requests.push(url);
        return {
            ok: true,
            async json() {
                if (url.includes('/quotes')) return { tweets: [{ tweet_id: 'quote' }], total: 1, limit: 20 };
                return { main: { tweet_id: url.split('/').at(-1) }, parents: [], children: [] };
            },
        };
    };
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.archiveResourcesLoaded = true;
    app.splitPanel = true;
    app.$refs.detailPanel = { scrollTop: 0 };

    await app.openThread('123');
    await app.toggleSplitPanel(false);
    assert.equal(app.viewMode, 'thread');
    assert.equal(app.panelMode, null);
    assert.equal(context.window.location.pathname, '/post/123');
    await app.toggleSplitPanel(true);
    assert.equal(app.panelMode, 'thread');
    await app.openThread('456', false, true);
    await app.openQuotes('456', true);
    assert.deepEqual(
        context.__state.historyCalls.filter(call => call[0] === 'push').map(call => call[3]),
        ['/post/123', '/post/456', '/post/456/quotes'],
    );
    assert.equal(app.panelStack.length, 3);

    app.panelGoBack();
    assert.equal(context.__state.historyCalls.at(-1)[0], 'back');
    await app.applyRoute(
        { viewMode: 'thread', tweetId: '456' },
        { fromPopState: true, state: { viewMode: 'thread', tweetId: '456', tweetNookDepth: 2 } },
    );
    assert.equal(app.panelMode, 'thread');
    assert.equal(app.panelThreadData.main.tweet_id, '456');
    assert.equal(app.panelStack.length, 2);

    await app.applyRoute(
        { viewMode: 'thread', tweetId: '123' },
        { fromPopState: true, state: { viewMode: 'thread', tweetId: '123', tweetNookDepth: 1 } },
    );
    assert.equal(app.panelStack.length, 1);
    assert.equal(app.panelThreadData.main.tweet_id, '123');
    await app.applyRoute(
        { viewMode: 'list' },
        { fromPopState: true, state: { viewMode: 'list', scrollY: 0, tweetNookDepth: 0 } },
    );
    assert.equal(app.panelMode, null);
    assert.equal(app.panelStack.length, 0);

    await app.applyRoute(
        { viewMode: 'thread', tweetId: '456' },
        { fromPopState: true, state: { viewMode: 'thread', tweetId: '456', tweetNookDepth: 2 } },
    );
    assert.equal(app.panelThreadData.main.tweet_id, '456');
    assert.equal(requests.filter(url => url === '/api/tweets/456').length, 1);
});

test('direct detail Back and detail searches stay inside the archive route', async () => {
    const context = browserContext('/post/123');
    context.fetch = async url => ({
        ok: true,
        async json() {
            if (url.startsWith('/api/tweets?')) return { tweets: [], page: 1, total_pages: 1, total: 0 };
            return { main: { tweet_id: '123' }, parents: [], children: [] };
        },
    });
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.archiveResourcesLoaded = true;
    app.activeRoute = { viewMode: 'thread', tweetId: '123' };
    app.navigationDepth = 0;
    await app.applyRoute(app.activeRoute, {
        state: { viewMode: 'thread', tweetId: '123', tweetNookDepth: 0 },
    });

    app.goBack();
    assert.equal(context.window.location.pathname, '/');
    assert.notEqual(context.__state.historyCalls.at(-1)[0], 'back');

    app.activeRoute = { viewMode: 'thread', tweetId: '123' };
    app.viewMode = 'thread';
    app.searchQuery = 'from:alice';
    app.search();
    assert.equal(context.window.location.pathname, '/');
    assert.equal(app.viewMode, 'list');
});

test('thread and quote navigation update history, panel state, and network state', async () => {
    const context = browserContext();
    let threadFetches = 0;
    context.fetch = async url => ({
        ok: true,
        async json() {
            if (url.includes('/quotes')) {
                return { tweets: [{ tweet_id: 'q1' }], total: 21, limit: 20 };
            }
            threadFetches++;
            return { main: { tweet_id: '42' }, parents: [], children: [] };
        },
    });
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    app.archiveResourcesLoaded = true;
    app.$refs.detailPanel = { scrollTop: 99 };
    app.splitPanel = false;

    await app.openThread('42');
    assert.equal(app.viewMode, 'thread');
    assert.equal(app.threadData.main.tweet_id, '42');
    assert.equal(app.loadingThread, false);
    assert.ok(
        context.__state.historyCalls.some(
            call => call[0] === 'push' && call[1].tweetId === '42' && call[3] === '/post/42',
        ),
    );

    await app.openQuotes('42');
    assert.equal(app.viewMode, 'quotes');
    assert.deepEqual(Array.from(app.quotesList, tweet => tweet.tweet_id), ['q1']);
    assert.equal(app.quotesTotalPages, 2);
    assert.equal(context.__state.historyCalls.at(-1)[3], '/post/42/quotes');

    app.splitPanel = true;
    context.window.innerWidth = 1280;
    await app.openThread('42');
    assert.equal(app.panelMode, 'thread');
    assert.equal(app.panelThreadData.main.tweet_id, '42');
    assert.equal(threadFetches, 1);
    await app.openQuotes('42', true);
    assert.equal(app.panelMode, 'quotes');
    assert.equal(app.panelStack.length, 2);
    app.panelGoBack();
    assert.equal(context.__state.historyCalls.at(-1)[0], 'back');
    app.closePanel();
    assert.equal(app.panelMode, null);
    assert.equal(context.window.location.pathname, '/');
});

test('thread cache reuses successes across modes but retries failures', async () => {
    const context = browserContext();
    let fetches = 0;
    let fail = true;
    context.fetch = async url => {
        fetches++;
        if (fail) return { ok: false };
        return {
            ok: true,
            async json() {
                return { main: { tweet_id: url.split('/').at(-1) }, parents: [], children: [] };
            },
        };
    };
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());

    await assert.rejects(app.loadThread('failed'), /Failed to load thread/);
    fail = false;
    const loaded = await app.loadThread('failed');
    const cached = await app.loadThread('failed');

    assert.equal(loaded.main.tweet_id, 'failed');
    assert.equal(cached, loaded);
    assert.equal(fetches, 2);

    app.threadCache.set('stale', {
        loadedAt: Date.now() - app.threadCacheTtlMs - 1,
        request: Promise.resolve({ main: { tweet_id: 'old' } }),
    });
    const refreshed = await app.loadThread('stale');
    assert.equal(refreshed.main.tweet_id, 'stale');
    assert.equal(fetches, 3);
});

test('goBack tears down playing videos before navigating history', () => {
    const context = browserContext();
    const video = {
        paused: false,
        currentTime: 30,
        pause() {
            this.paused = true;
        },
    };
    context.__state.videos.push(video);
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    app.navigationDepth = 1;
    app.goBack();
    assert.equal(video.paused, true);
    assert.equal(video.currentTime, 0);
    assert.equal(context.__state.historyCalls.at(-1)[0], 'back');
});

test('tag settings bound rendered rows and reuse archive-wide counts for large catalogs', () => {
    const { nookTagsPage } = loadScripts(browserContext(), ['settings.js'], '({nookTagsPage})');
    const page = nookTagsPage();
    let reads = 0;
    page.globalTags = Array.from({ length: 20000 }, (_, index) => ({
        tag: `Tag ${String(index).padStart(5, '0')}`,
        get count() { reads++; return index + 1; },
    }));
    assert.equal(page.rows().length, 100);
    assert.equal(page.rows()[0].tag, 'Tag 19999');
    assert.equal(page.pageCount(), 200);
    assert.equal(page.pageLabel(), '1–100 of 20,000 tags');
    assert.equal(page.share(20000), 100);
    assert.equal(page.share(1), 4);
    assert.equal(page.countOf('Tag 19999'), 20000);
    assert.match(page.summary(), /200,010,000 times/);
    const warmedReads = reads;
    const seen = new Set();
    for (let index = 1; index <= page.pageCount(); index++) {
        page.page = index;
        for (const tag of page.rows()) {
            seen.add(tag.tag);
            page.share(index);
            page.countOf(tag.tag);
        }
        page.summary();
    }
    assert.equal(seen.size, 20000);
    assert.equal(reads, warmedReads, 'paging and row bars must not rescan the catalog');
    assert.equal(page.globalTags[0].tag, 'Tag 00000', 'sorting must preserve the source array');

    // More tags than can be spread into Math.max() arguments.
    page.globalTags = Array.from({ length: 150000 }, (_, index) => ({ tag: `New ${index}`, count: index + 1 }));
    assert.equal(page.share(150000), 100);
    assert.equal(page.countOf('Tag 19999'), 0);
    assert.equal(page.countOf('New 149999'), 150000);
});

test('tag settings search all pages and preserve merge selection through paging and refreshes', async () => {
    const context = browserContext();
    const requests = [];
    context.fetch = async (url, options) => { requests.push([url, options]); return { ok: true }; };
    const { nookTagsPage } = loadScripts(context, ['settings.js'], '({nookTagsPage})');
    const watchers = new Map();
    const page = Object.assign(nookTagsPage(), {
        globalTags: Array.from({ length: 205 }, (_, index) => ({ tag: `Tag ${String(index).padStart(3, '0')}`, count: index + 1 })),
        $watch: (key, callback) => watchers.set(key, callback),
        $el: { scrollTop: 500 },
        $refs: { tagListHeading: { focus() {} } },
        $nextTick: callback => callback(),
    });
    page.init();
    page.toggleSelect();
    page.toggle('Tag 204');
    page.changePage(1);
    assert.equal(page.currentPage(), 2);
    assert.equal(page.$el.scrollTop, 0);
    page.toggle(page.rows()[0].tag);
    page.changePage(1);
    assert.equal(page.rows().length, 5);
    assert.equal(page.pageLabel(), '201–205 of 205 tags');
    page.query = 'TAG 000';
    watchers.get('query')();
    assert.equal(page.currentPage(), 1);
    assert.equal(page.rows().length, 1);
    assert.equal(page.rows()[0].tag, 'Tag 000');
    assert.equal(page.selected.length, 2);
    page.query = '';
    watchers.get('query')();
    page.sort = 'name';
    watchers.get('sort')();
    assert.equal(page.rows()[0].tag, 'Tag 000');
    page.changePage(1);
    page.changePage(1);
    page.globalTags = page.globalTags.slice(0, 100);
    assert.equal(page.rows().length, 100, 'a shrinking result set must not leave a blank page');
    watchers.get('globalTags')();
    assert.equal(page.page, 1);
    page.globalTags = [{ tag: 'Tag 204', count: 205 }, { tag: 'Tag 104', count: 105 }];
    watchers.get('globalTags')();
    page.keep = 'Tag 204';
    page.fetchGlobalTags = async () => { page.globalTags = [{ tag: 'Tag 204', count: 310 }]; };
    await page.submitMerge();
    assert.equal(requests[0][0], '/api/tags/merge');
    assert.deepEqual(JSON.parse(requests[0][1].body), { primary_tag: 'Tag 204', merge_tags: ['Tag 104'] });
    assert.equal(page.countOf('Tag 204'), 310);
    assert.equal(page.selected.length, 0);
    page.confirmDelete = 'Tag 204';
    await page.deleteTag();
    assert.equal(page.rows().length, 0);
    assert.equal(page.countOf('Tag 204'), 0);
    assert.match(page.summary(), /Tag a tweet/);
});

test('tag settings only mount when open and expose accessible bounded page controls', () => {
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook/web/index.html'), 'utf8');
    assert.match(html, /<template x-if="showSettingsModal && settingsTab === 'tags'">/);
    assert.match(html, /<template x-for="tag in rows\(\)"/);
    assert.match(html, /aria-label="Tag pages" x-show="filteredRows\(\).length > pageSize"/);
    assert.match(html, /:disabled="currentPage\(\) <= 1" @click="changePage\(-1\)"/);
    assert.match(html, /:disabled="currentPage\(\) >= pageCount\(\)" @click="changePage\(1\)"/);
});

test('tag editing deduplicates case-insensitively and persists response state', async () => {
    const context = browserContext();
    const requests = [];
    context.fetch = async (url, options = {}) => {
        requests.push([url, options]);
        return {
            ok: true,
            async json() {
                return { tags: [{ tag: 'Night', count: 2 }] };
            },
        };
    };
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    app.tweets = [{ tweet_id: '1', media_tags: { description: 'Old description', tags: ['Old'] } }];
    app.panelThreadData = {
        main: { tweet_id: '1', media_tags: { description: 'Old description', tags: ['Old'] } },
        parents: [],
        children: [],
    };
    app.tagModalTweetId = '1';
    app.tagModalData = app.tweets[0].media_tags;
    app.editableTags = ['Night'];
    app.editableDescription = '  A moonlit view  ';
    app.addEditableTag(' night ');
    app.addEditableTag('Sky');
    assert.deepEqual(Array.from(app.editableTags), ['Night', 'Sky']);

    await app.saveTags();
    assert.deepEqual(Array.from(app.tweets[0].media_tags.tags), ['Night', 'Sky']);
    assert.equal(app.tweets[0].media_tags.description, 'A moonlit view');
    assert.equal(app.panelThreadData.main.media_tags.description, 'A moonlit view');
    assert.equal(requests[0][1].method, 'PUT');
    assert.deepEqual(JSON.parse(requests[0][1].body), {
        tags: ['Night', 'Sky'],
        description: 'A moonlit view',
    });

    await app.fetchGlobalTags();
    assert.equal(app.globalTags[0].tag, 'Night');
    assert.equal(app.globalTagsLoading, false);
});

test('tweet overflow supports copying raw data, untagged tweets, and a one-second Tags shortcut', async () => {
    const context = browserContext();
    let pressCallback = null;
    let clearedTimer = null;
    let copiedText = null;
    context.navigator = {
        clipboard: {
            async writeText(value) {
                copiedText = value;
            },
        },
    };
    context.setTimeout = (callback, delay) => {
        assert.equal(delay, 1000);
        pressCallback = callback;
        return 41;
    };
    context.clearTimeout = timer => { clearedTimer = timer; };
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());

    app.toggleTweetMenu('list:2');
    assert.equal(app.tweetMenuOpen, 'list:2');
    await app.copyRawTweet({ raw_json: { rest_id: '2', legacy: { full_text: 'Hello' } } });
    assert.equal(app.tweetMenuOpen, null);
    assert.equal(copiedText, '{\n  "rest_id": "2",\n  "legacy": {\n    "full_text": "Hello"\n  }\n}');

    app.toggleTweetMenu('list:2');
    app.openTagsFromTweetMenu('2', null);
    assert.equal(app.tweetMenuOpen, null);
    assert.equal(app.tagModalOpen, true);
    // Nothing saved yet, so the dialog opens in the editor.
    assert.equal(app.isEditingTags, true);
    assert.deepEqual(Array.from(app.tagModalData.tags), []);
    assert.equal(app.tagModalData.description, '');
    assert.deepEqual(Array.from(app.editableTags), []);

    app.startTweetMenuPress({ pointerType: 'touch', button: 0 }, '3', {
        description: 'Existing',
        tags: ['Bird'],
    });
    assert.equal(app.tweetMenuPressTimer, 41);
    pressCallback();
    assert.equal(app.tagModalTweetId, '3');
    assert.equal(app.tagModalData.description, 'Existing');
    assert.equal(app.isEditingTags, false);
    assert.equal(app.tweetMenuPressTimer, null);

    app.startTweetMenuPress({ pointerType: 'mouse', button: 0 }, '4', null);
    app.cancelTweetMenuPress();
    assert.equal(clearedTimer, 41);
    assert.equal(app.tweetMenuPressTimer, null);
});

test('tweet overflow copies raw data when the Clipboard API is unavailable', async () => {
    const context = browserContext();
    let copiedText = null;
    const textarea = {
        value: '',
        style: {},
        setAttribute() {},
        focus() {},
        select() {
            copiedText = this.value;
        },
    };
    context.navigator = {};
    context.document.createElement = tag => {
        assert.equal(tag, 'textarea');
        return textarea;
    };
    context.document.body.appendChild = node => assert.equal(node, textarea);
    context.document.body.removeChild = node => assert.equal(node, textarea);
    context.document.execCommand = command => {
        assert.equal(command, 'copy');
        return true;
    };
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());

    await app.copyRawTweet({ raw_json: { rest_id: '3', legacy: { full_text: 'Fallback' } } });
    assert.equal(copiedText, '{\n  "rest_id": "3",\n  "legacy": {\n    "full_text": "Fallback"\n  }\n}');
});

test('tweet overflow menus are wired on list and detail surfaces', () => {
    const html = fs.readFileSync(
        path.join(ROOT, 'tweetnook', 'web', 'index.html'),
        'utf8',
    );

    // Detail surfaces keep per-tweet menus; feed cards render their button in app.js
    // and share one Alpine menu.
    assert.equal((html.match(/aria-label="More actions"/g) || []).length, 2);
    assert.match(html, /copyRawTweet\(listMenu\.tweet\)/);
    assert.match(html, /copyRawTweet\(threadData\.main\)/);
    assert.match(html, /copyRawTweet\(panelThreadData\.main\)/);
    assert.match(html, /setTweetHidden\(listMenu\.key\.slice\('list:'\.length\), !listMenu\.hidden\)/);
    assert.match(html, /setTweetHidden\(threadData\.main\.tweet_id, !threadData\.main\.hidden\)/);
    assert.match(html, /setTweetHidden\(panelThreadData\.main\.tweet_id, !panelThreadData\.main\.hidden\)/);
    assert.match(html, /startTweetMenuPress\(\$event, threadData\.main\.tweet_id/);
    assert.match(html, /startTweetMenuPress\(\$event, panelThreadData\.main\.tweet_id/);
    assert.equal((html.match(/renderQuotePlaceholder\(getQuoteTweet/g) || []).length, 6);
    assert.match(html, /x-model="editableDescription"/);
    const { app } = feedListApp();
    const card = app.renderFeedCard({ tweet_id: '1', text: 'x', author: { id: '2', username: 'a', display_name: 'A' } });
    assert.match(card, /aria-label="More actions" aria-haspopup="menu" aria-expanded="false" title="More actions \(hold for Tags\)" data-action="menu"/);
});

test('thread reply surfaces render quoted tweets in both presentations', () => {
    const html = fs.readFileSync(
        path.join(ROOT, 'tweetnook', 'web', 'index.html'),
        'utf8',
    );

    assert.equal((html.match(/<template x-if="getQuoteTweet\(child\)">/g) || []).length, 2);
    assert.equal((html.match(/<template x-if="getQuoteTweet\(reply\)">/g) || []).length, 2);
    assert.equal((html.match(/renderQuotePlaceholder\(getQuoteTweet\(child\)\)/g) || []).length, 2);
    assert.equal((html.match(/renderQuotePlaceholder\(getQuoteTweet\(reply\)\)/g) || []).length, 2);
    assert.equal((html.match(/formatText\(getQuoteTweet\(child\), isMediaQuotingMedia\(child\)\)/g) || []).length, 2);
    assert.equal((html.match(/formatText\(getQuoteTweet\(reply\), isMediaQuotingMedia\(reply\)\)/g) || []).length, 2);
    assert.equal((html.match(/renderMediaGrid\(child\.qt_media, 'quote'\)/g) || []).length, 2);
    assert.equal((html.match(/renderMediaGrid\(reply\.qt_media, 'quote'\)/g) || []).length, 2);
    assert.match(html, /openThread\(getTweetId\(getQuoteTweet\(child\)\)\)/);
    assert.match(html, /openThread\(getTweetId\(getQuoteTweet\(child\)\), false, true\)/);
    assert.match(html, /openThread\(getTweetId\(getQuoteTweet\(reply\)\)\)/);
    assert.match(html, /openThread\(getTweetId\(getQuoteTweet\(reply\)\), false, true\)/);
});

test('recursive thread replies indent by bounded depth and disclose truncation', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    const html = fs.readFileSync(
        path.join(ROOT, 'tweetnook', 'web', 'index.html'),
        'utf8',
    );

    assert.equal(app.nestedReplyIndent({ thread_depth: 2 }), 0);
    assert.equal(app.nestedReplyIndent({ thread_depth: 3 }), 12);
    assert.equal(app.nestedReplyIndent({ thread_depth: 20 }), 72);
    assert.equal(app.nestedReplyIndent({}), 0);
    assert.equal((html.match(/nestedReplyIndent\(reply\)/g) || []).length, 2);
    assert.equal((html.match(/reply_tree_truncated/g) || []).length, 2);
    assert.equal((html.match(/more saved replies than can be shown at once/g) || []).length, 2);
});

test('reply-recipient markup opens profile cards on every tweet surface', () => {
    const html = fs.readFileSync(
        path.join(ROOT, 'tweetnook', 'web', 'index.html'),
        'utf8',
    );

    assert.equal((html.match(/renderReplyRecipientContent\(/g) || []).length, 16);
    assert.equal((html.match(/getReplyRecipients\(child\)\.length > 1 \|\|/g) || []).length, 2);
    assert.equal((html.match(/threadData\.parents\.length === 0/g) || []).length, 1);
    assert.equal((html.match(/panelThreadData\.parents\.length === 0/g) || []).length, 1);
    // Feed cards and their quotes delegate reply targets (see the feed click test).
    const { app } = feedListApp();
    const card = app.renderFeedCard(JSON.parse(JSON.stringify(WEB_FIXTURE.slim[4])));
    assert.match(card, /data-action="reply-profile" data-username="frank">@frank</);
});

test('demo reply metadata keeps body mentions out of the automatic recipient label', () => {
    const context = browserContext();
    const app = immediateComponent(loadScripts(context, ['themes.js', 'app.js'], 'tweetApp')());
    context.window.TWEETNOOK_DEMO = false;
    vm.runInContext(fs.readFileSync(path.join(ROOT, 'demo', 'demo-api.js'), 'utf8'), context);
    const fixture = JSON.parse(fs.readFileSync(path.join(ROOT, 'demo', 'data.json'), 'utf8'));
    const store = context.window.TweetNookDemo.createStore(fixture);
    const reply = store.hydrate('1795129713683255380');
    assert.deepEqual(Array.from(reply.raw_json.legacy.display_text_range), [13, 42]);
    assert.equal(app.getReplyTo(reply), 'aleksliving');
    assert.deepEqual(Array.from(app.getReplyRecipients(reply)), ['aleksliving']);
    const body = app.formatText(reply);
    assert.match(body, /@RPhantom00<\/span> it/);
    assert.doesNotMatch(body, /@aleksliving/);
});

test('reply recipients use only the captured automatic prefix and retain safe labels', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    const makeReply = (text, prefix, primary, mentions = null) => ({
        text,
        raw_json: { legacy: {
            full_text: text,
            in_reply_to_status_id_str: 'parent',
            in_reply_to_screen_name: primary,
            ...(prefix === null ? {} : { display_text_range: [prefix, text.length] }),
            ...(mentions === null ? {} : { entities: { user_mentions: mentions } }),
        } },
    });
    const mention = (text, name) => {
        const start = text.indexOf(`@${name}`);
        return { screen_name: name, indices: [start, start + name.length + 1] };
    };
    const twoText = '@first @second body @bodymention';
    const two = makeReply(twoText, '@first @second '.length, 'first', [mention(twoText, 'bodymention'), mention(twoText, 'second'), mention(twoText, 'first')]);
    assert.deepEqual(Array.from(app.getReplyRecipients(two)), ['first', 'second']);
    assert.match(app.renderReplyRecipients(two), /Replying to .*@first<\/span> and .*@second<\/span>/);
    assert.match(app.formatText(two), /^body <span[^>]+>@bodymention<\/span>$/);
    const malformedIndices = makeReply(twoText, '@first @second '.length, 'first', [
        { screen_name: 'first', indices: { start: 0, end: 6 } },
        { screen_name: 'second', indices: 7 },
    ]);
    assert.deepEqual(Array.from(app.getReplyRecipients(malformedIndices)), ['first', 'second']);

    const threeText = '@first @second @third body';
    const three = makeReply(threeText, '@first @second @third '.length, 'first', [
        mention(threeText, 'first'), mention(threeText, 'second'), mention(threeText, 'third'),
    ]);
    assert.deepEqual(Array.from(app.getReplyRecipients(three)), ['first', 'second', 'third']);
    assert.match(app.renderReplyRecipients(three), /@first<\/span> .*@second<\/span> and .*@third<\/span>/);

    const duplicateText = '@FIRST @second @first body';
    const duplicates = makeReply(duplicateText, '@FIRST @second @first '.length, 'SECOND', [
        mention(duplicateText, 'FIRST'), mention(duplicateText, 'second'), mention(duplicateText, 'first'),
    ]);
    assert.deepEqual(Array.from(app.getReplyRecipients(duplicates)), ['FIRST', 'second']);
    assert.equal(app.getReplyTo(duplicates), 'SECOND', 'primary reply target compatibility');

    const bodyLeading = makeReply('@body is intentional', 0, 'primary', []);
    assert.deepEqual(Array.from(app.getReplyRecipients(bodyLeading)), ['primary']);
    assert.match(app.formatText(bodyLeading), /^<span[^>]+>@body<\/span> is intentional$/);
    assert.deepEqual(Array.from(app.getReplyRecipients({ text: '@ordinary mention', raw_json: { legacy: { full_text: '@ordinary mention' } } })), []);
    const nonReply = {
        text: '@ordinary @body',
        raw_json: { legacy: {
            full_text: '@ordinary @body', display_text_range: ['@ordinary '.length, 14],
            entities: { user_mentions: [mention('@ordinary @body', 'ordinary')] },
        } },
    };
    assert.deepEqual(Array.from(app.getReplyRecipients(nonReply)), []);

    const legacy = makeReply('@old @primary body @notrecipient', null, 'primary');
    assert.deepEqual(Array.from(app.getReplyRecipients(legacy)), ['old', 'primary']);
    assert.match(app.formatText(legacy), /^body <span[^>]+>@notrecipient<\/span>$/);
    const nameOnlyFlat = {
        text: '@old @primary body',
        raw_json: { full_text: '@old @primary body', in_reply_to_screen_name: 'primary' },
    };
    assert.equal(app.formatText(nameOnlyFlat), 'body', 'screen-name-only flat replies use the legacy prefix fallback');
    const normalizedSource = {
        text: '@body has an intentional leading mention',
        raw_json: { legacy: {
            full_text: '@first @second @body has an intentional leading mention',
            display_text_range: ['@first @second '.length, '@first @second @body has an intentional leading mention'.length],
            in_reply_to_status_id_str: 'parent',
            in_reply_to_screen_name: 'first',
        } },
    };
    assert.match(app.formatText(normalizedSource), /^<span[^>]+>@body<\/span> has an intentional leading mention$/,
        'valid raw offsets do not strip a normalized source that has a different prefix');
    assert.deepEqual(Array.from(app.getReplyRecipients({ legacy: three.raw_json.legacy })), ['first', 'second', 'third'], 'flat raw shape');
    assert.deepEqual(Array.from(app.getReplyRecipients({
        full_text: threeText,
        display_text_range: three.raw_json.legacy.display_text_range,
        in_reply_to_status_id_str: 'parent',
        in_reply_to_screen_name: 'first',
    })), ['first', 'second', 'third'], 'flat raw with missing mention entities');

    const hostileText = '@<img src=x onerror=alert(1)> body';
    const hostile = makeReply(hostileText, '@<img src=x onerror=alert(1)> '.length, '<img src=x onerror=alert(1)>', []);
    const hostileHtml = app.renderReplyRecipients(hostile);
    assert.doesNotMatch(hostileHtml, /<img/);
    assert.match(hostileHtml, /&lt;img src=x onerror=alert\(1\)&gt;/);

    let opened = null;
    app.openUsernameProfileCard = (event, username) => { opened = { event, username }; };
    const second = { dataset: { username: 'second' } };
    const event = { target: { closest: selector => {
        assert.equal(selector, '[data-action="reply-profile"][data-username]');
        return second;
    } }, stopPropagation() { this.stopped = true; } };
    app.handleReplyRecipientClick(event);
    assert.equal(opened.username, 'second');
    assert.equal(opened.event.currentTarget, second);
    assert.equal(event.stopped, true);
});

test('config and stats requests update their matching UI state', async () => {
    const context = browserContext();
    context.fetch = async url => ({
        ok: true,
        async json() {
            if (url === '/api/config/schema') {
                return {
                    whitelist: ['web.host'],
                    blacklist: ['auth.auth_token'],
                    full_width: ['web.host'],
                    types: { 'web.port': 'number' },
                };
            }
            if (url === '/api/config') {
                return {
                    values: {
                        auth: { auth_token: 'masked' },
                        sync: { page_delay: 2 },
                        web: { host: '127.0.0.1', port: 8000 },
                    },
                    explicit: ['web.host'],
                };
            }
            if (url === '/api/config/defaults') return { web: { port: 8000 } };
            if (url === '/api/stats/latest-sync') return { latest_sync: '2026-07-30T00:00:00Z' };
            return {};
        },
    });
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());

    await app.fetchConfig();
    assert.equal(app.configData.web.port, 8000);
    assert.equal(app.configOriginal.web.port, 8000);
    assert.equal(app.configDefaults.web.port, 8000);
    assert.deepEqual(Array.from(app.configExplicit), ['web.host']);
    assert.equal(app.isFieldVisible('web', 'host'), true);
    assert.equal(app.isFieldVisible('auth', 'auth_token'), false);
    assert.equal(app.isFieldVisible('sync', 'page_delay'), false);
    app.showAdvancedConfig = true;
    assert.equal(app.isFieldVisible('sync', 'page_delay'), true);
    assert.equal(app.isFieldVisible('auth', 'auth_token'), false);
    assert.equal(app.getFieldType('web', 'port'), 'number');
    assert.equal(app.isFieldFullWidth('web', 'host'), true);
    assert.equal(app.isConfigExplicit('web', 'host'), true);

    await app.fetchStats();
    assert.equal(app.lastSyncFormatted, '2026-07-30T00:00:00Z');
});

test('setup validates before saving auth then uploads, imports, and confirms archive clearing', async () => {
    const context = browserContext();
    const requests = [];
    let authVerified = false;
    let archive = {
        uploaded: false,
        filename: null,
        size: null,
        imported: false,
        enriched: false,
        pending_enrichment: 0,
        warnings: ['A Twitter/X archive has not been imported yet.'],
    };
    context.fetch = async (url, options = {}) => {
        requests.push([url, options.method || 'GET']);
        if (url === '/api/setup' && !options.method) {
            return { ok: true, async json() { return {
                auth: { configured: true, verified: authVerified, values: { auth_token: '********', ct0: '********', user_id: '42' } },
                archive,
            }; } };
        }
        if (url === '/api/setup/auth') {
            authVerified = true;
            return { ok: true, async json() { return {
                configured: true,
                verified: true,
                values: { auth_token: '********', ct0: '********', user_id: '42' },
            }; } };
        }
        if (url === '/api/setup/archive' && options.method === 'DELETE') {
            archive = { ...archive, uploaded: false, filename: null, size: null };
            return { ok: true, async json() { return archive; } };
        }
        if (url === '/api/setup/archive/import') {
            return { ok: true, async json() { return { started: true }; } };
        }
        if (url === '/api/activity/status') {
            return { ok: true, async json() { return { active: false, schedule: {} }; } };
        }
        throw new Error(`unexpected request: ${url}`);
    };
    // Archive uploads go through XHR so the file row can report progress.
    context.XMLHttpRequest = class {
        constructor() { this.upload = {}; }
        open(method, url) { requests.push([url, method]); }
        setRequestHeader() {}
        send(file) {
            this.upload.onprogress({ loaded: file.size / 2, total: file.size });
            archive = { ...archive, uploaded: true, filename: 'archive.zip', size: file.size };
            this.status = 200;
            this.responseText = JSON.stringify(archive);
            this.onload();
        }
    };
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());

    await app.fetchSetup();
    assert.equal(app.setupAuth.auth_token, '********');
    assert.equal(app.setupArchiveStatus, 'none');
    assert.equal(app.setupArchiveAction.label, 'Start import');
    assert.equal(app.setupArchiveAction.disabled, true);
    app.setupFlowStep = 2;
    assert.equal(app.setupAuthNeedsTest, true);
    const authDelays = [];
    context.setTimeout = (callback, delay) => {
        authDelays.push(delay);
        callback();
        return 0;
    };
    await app.saveSetupAuth();
    assert.equal(app.setupAuthResult.success, true);
    assert.deepEqual(authDelays, [1000]);
    assert.equal(app.setupFlowStep, 3);
    assert.equal(requests.filter(([url]) => url === '/api/setup/auth').length, 1);
    // A verified, unedited connection continues without another probe.
    app.goToSetupStep(2);
    assert.equal(app.setupAuthNeedsTest, false);
    await app.saveSetupAuth();
    assert.equal(app.setupFlowStep, 3);
    assert.equal(requests.filter(([url]) => url === '/api/setup/auth').length, 1);
    app.editSetupAuth();
    assert.equal(app.setupAuthNeedsTest, true);

    await app.uploadSetupArchive({ target: { files: [{ name: 'notes.txt', size: 4 }], value: 'notes.txt' } });
    assert.match(app.setupArchiveError, /not a \.zip file/);
    assert.equal(requests.some(([url, method]) => url === '/api/setup/archive' && method === 'PUT'), false);

    await app.uploadSetupArchive({
        target: { files: [{ name: 'archive.zip', size: 2048 }], value: 'archive.zip' },
    });
    assert.equal(app.setupData.archive.uploaded, true);
    assert.equal(app.setupArchiveError, '');
    assert.equal(app.setupUploading, false);
    assert.equal(app.setupArchiveStatus, 'staged');
    assert.equal(app.setupArchiveAction.label, 'Start import');
    assert.equal(app.setupArchiveAction.disabled, false);
    assert.equal(app.formatSetupBytes(2048), '2.00 KiB');

    app.showSetupModal = true;
    app.setupFlowStep = 1;
    await app.importSetupArchive();
    assert.equal(app.activityDrawerOpen, false);
    assert.equal(app.showSetupModal, true);
    assert.equal(app.setupFlowStep, 2);
    assert.equal(app.setupDirection, 'forward');
    app.goToSetupStep(1);
    assert.equal(app.setupDirection, 'back');
    app.goToSetupStep(2);

    const confirmations = [];
    app.askSetupConfirm = async options => { confirmations.push(options); return false; };
    await app.clearSetupArchive();
    assert.equal(app.setupData.archive.uploaded, true);
    assert.equal(confirmations[0].destructive, true);
    app.askSetupConfirm = async () => true;
    await app.clearSetupArchive();
    assert.equal(app.setupData.archive.uploaded, false);
    assert.ok(requests.some(([url, method]) => url === '/api/setup/archive' && method === 'DELETE'));
});

test('setup archive choices stay reversible until Setup finishes', async () => {
    const context = browserContext();
    const requests = [];
    let archive = { ready: false, database_exists: false, uploaded: false, initialization_mode: null, initialization_state: 'missing', legacy_migration_available: true };
    context.fetch = async (url, options = {}) => {
        requests.push([url, options.method || 'GET']);
        if (url === '/api/setup/archive/empty') {
            archive = { ...archive, ready: true, database_exists: true, initialization_mode: 'empty', initialization_state: 'complete' };
            return { ok: true, async json() { return {}; } };
        }
        if (url === '/api/setup/complete') return { ok: true, async json() { return { completed: true, reload: false }; } };
        throw new Error(`unexpected request: ${url}`);
    };
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.setupData = { archive, auth: { verified: false }, web: { password_configured: true } };

    assert.equal(app.setupArchiveStatus, 'none');
    assert.equal(app.setupArchiveAction.disabled, true);

    // Choosing an empty archive only selects it; nothing is created yet.
    app.chooseSetupArchive('empty');
    assert.equal(app.setupArchiveStatus, 'empty-selected');
    assert.equal(app.setupArchiveAction.label, 'Continue');
    assert.equal(app.setupArchiveStatusText, '', 'selecting adds no line, so the layout stays put');
    // Continue confirms the choice; declining stays on step 1.
    const confirms = [];
    app.askSetupConfirm = async options => { confirms.push(options.title); return confirms.length > 1; };
    await app.setupArchiveAction.run();
    assert.equal(app.setupFlowStep, 1);
    await app.setupArchiveAction.run();
    assert.equal(app.setupFlowStep, 2);
    assert.deepEqual(confirms, ['Start with an empty archive?', 'Start with an empty archive?']);
    assert.equal(requests.length, 0);

    // Going back, the choice can be switched or undone.
    app.goToSetupStep(1);
    app.chooseSetupArchive('migration');
    assert.equal(app.setupArchiveAction.label, 'Start migration');
    app.chooseSetupArchive('migration');
    assert.equal(app.setupArchiveStatus, 'none');

    // The empty archive is created as part of finishing.
    app.chooseSetupArchive('empty');
    app.setupSkipAuth = true;
    await app.finishSetup();
    assert.deepEqual(requests.map(([url]) => url), ['/api/setup/archive/empty', '/api/setup/complete']);
    assert.equal(app.setupArchiveChoice, null);
});

test('first-run setup resumes at the first unfinished step after a reload', async () => {
    let auth = { verified: false, configured: false, values: { auth_token: '', ct0: '', user_id: '' } };
    let archive = { ready: false, uploaded: false, initialization_mode: null };
    const load = async () => {
        const context = browserContext();
        context.fetch = async () => ({ ok: true, async json() { return { required: true, completed: false, web: {}, auth, archive }; } });
        const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
        const app = immediateComponent(tweetApp());
        await app.fetchSetup();
        return app;
    };
    assert.equal((await load()).setupFlowStep, 1);
    archive = { ...archive, ready: true, initialization_mode: 'import' };
    assert.equal((await load()).setupFlowStep, 2);
    auth = { ...auth, verified: true };
    const app = await load();
    assert.equal(app.setupFlowStep, 3);
    // Later polls do not move someone who went back.
    app.goToSetupStep(1);
    await app.fetchSetup();
    assert.equal(app.setupFlowStep, 1);
});

test('setup schedule step saves through Setup before the scheduler starts', async () => {
    const context = browserContext();
    const requests = [];
    context.fetch = async (url, options = {}) => {
        requests.push([url, options.method || 'GET', options.body ? JSON.parse(options.body) : null]);
        if (url === '/api/setup/schedule' && !options.method) {
            return { ok: true, async json() { return { config: { enabled: false, cadence: 'daily', time: '03:00' }, collections: ['bookmarks'] }; } };
        }
        if (url === '/api/setup/schedule') {
            const body = JSON.parse(options.body);
            return { ok: true, async json() { return { config: body.schedule, collections: body.collections }; } };
        }
        throw new Error(`unexpected request: ${url}`);
    };
    const { tweetApp, nookToggleSyncCollection, nookScheduleHeadline } = loadScripts(
        context, ['themes.js', 'app.js', 'header.js', 'settings.js'], '({tweetApp, nookToggleSyncCollection, nookScheduleHeadline})');
    const app = immediateComponent(tweetApp());
    app.setupData = { completed: false, archive: {}, auth: { verified: false }, web: {} };
    app.showSetupModal = true;
    app.setupFlowStep = 3;

    await app.loadSetupSchedule();
    assert.equal(app.scheduleForm.enabled, false);
    assert.deepEqual([...app.nookSyncCollections], ['bookmarks']);
    // The simple step states the default without the random start window.
    assert.equal(app.setupScheduleSummary, 'Off. When on, syncs every day at 3:00 AM.');

    // What to sync stays local until Continue; the gated config API is not called.
    await nookToggleSyncCollection(app, 'tweets');
    assert.deepEqual([...app.nookSyncCollections], ['bookmarks', 'tweets']);
    app.scheduleForm.enabled = true;
    app.scheduleForm.randomize_time = true;
    app.scheduleForm.random_offset_max_hours = 2;
    assert.equal(app.setupScheduleSummary, 'Syncs every day at 3:00 AM.');
    assert.match(nookScheduleHeadline(app), /later/, 'Settings still describes the random start');

    await app.saveSetupSchedule();
    const saved = requests.at(-1);
    assert.deepEqual([saved[0], saved[1]], ['/api/setup/schedule', 'PUT']);
    assert.equal(saved[2].schedule.enabled, true);
    assert.deepEqual(saved[2].collections, ['bookmarks', 'tweets']);
    assert.equal(requests.some(([url]) => url === '/api/config'), false);
    assert.equal(app.setupFlowStep, 4);
    assert.equal(app.scheduleHasChanges(), false);

    // Setup has five steps.
    app.goToSetupStep(5);
    assert.equal(app.setupFlowStep, 5);
    app.goToSetupStep(6);
    assert.equal(app.setupFlowStep, 5);
});

test('setup replay source connects, toggles placeholder media, and disconnects', async () => {
    const context = browserContext();
    const requests = [];
    let skipQuotes = false, skipPreviews = false;
    const replay = (connected, placeholder = true) => ({
        tools: true,
        replay: connected
            ? { connected: true, url: 'http://192.168.1.20:8000', password: '********', placeholder_media: placeholder, skip_quote_expansion: skipQuotes, skip_link_previews: skipPreviews,
                status: { bookmarks_pages: 3, likes_pages: 2, thread_captures: 9 } }
            : { connected: false, url: null, password: null, placeholder_media: true, status: null },
    });
    let fail = true;
    context.fetch = async (url, options = {}) => {
        const body = options.body ? JSON.parse(options.body) : undefined;
        requests.push([url, options.method, body]);
        if (url === '/api/setup/replay' && options.method === 'PUT') {
            if (fail) return { ok: false, async json() { return { detail: 'The replay source rejected the password (its Web password).' }; } };
            skipQuotes = body.skip_quote_expansion;
            skipPreviews = body.skip_link_previews;
            return { ok: true, async json() { return replay(true, body.placeholder_media); } };
        }
        if (url === '/api/setup/replay/placeholder-media') return { ok: true, async json() { return replay(true, body.placeholder_media); } };
        if (url === '/api/setup/replay/skip-link-previews') {
            skipPreviews = body.skip_link_previews;
            return {ok: true, json: async () => replay(true)};
        }
        if (url === '/api/setup/replay/skip-quote-expansion') {
            skipQuotes = body.skip_quote_expansion;
            return {ok: true, async json() { return replay(true); }};
        }
        if (url === '/api/setup/replay' && options.method === 'DELETE') return { ok: true, async json() { return replay(false); } };
        throw new Error(`unexpected request: ${url}`);
    };
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.setupData = { dev: replay(false), archive: {}, auth: {} };

    assert.equal(app.setupReplayConnected, false);
    await app.connectSetupReplay();
    assert.equal(requests.length, 0, 'an empty address sends nothing');

    // Before connecting, the checkbox only changes what will be sent.
    await app.setSetupReplayPlaceholder(false);
    assert.equal(app.setupReplayPlaceholder, false);
    assert.equal(requests.length, 0);

    assert.equal(app.setupReplaySkipPreviews, false);
    await app.setSetupReplaySkipPreviews(true);
    assert.equal(app.setupReplaySkipPreviews, true);
    assert.equal(app.setupReplaySkipQuotes, false);
    await app.setSetupReplaySkipQuotes(true);
    assert.equal(app.setupReplaySkipQuotes, true);
    assert.equal(requests.length, 0);

    app.setupReplayForm.url = ' 192.168.1.20:8000 ';
    app.setupReplayForm.password = 'secret';
    await app.connectSetupReplay();
    assert.match(app.setupReplayError, /rejected the password/);
    assert.equal(app.setupReplayConnected, false);
    assert.equal(app.setupReplayForm.password, 'secret', 'a failed test keeps the typed password');

    fail = false;
    await app.connectSetupReplay();
    assert.deepEqual(requests.at(-1), ['/api/setup/replay', 'PUT', { url: '192.168.1.20:8000', password: 'secret', placeholder_media: false, skip_quote_expansion: true, skip_link_previews: true }]);
    assert.equal(app.setupReplayConnected, true);
    assert.equal(app.setupReplayError, '');
    assert.equal(app.setupReplayForm.password, '');
    assert.equal(app.setupReplaySummary, 'Connected to http://192.168.1.20:8000. It can replay 3 bookmark pages, 2 like pages, 9 threads.');

    // Once connected, the checkbox saves straight away.
    await app.setSetupReplayPlaceholder(true);
    assert.deepEqual(requests.at(-1), ['/api/setup/replay/placeholder-media', 'PUT', { placeholder_media: true }]);
    assert.equal(app.setupReplayPlaceholder, true);
    assert.equal(app.setupReplaySkipQuotes, true);
    await app.setSetupReplaySkipQuotes(false);
    assert.deepEqual(requests.at(-1), ['/api/setup/replay/skip-quote-expansion', 'PUT', {skip_quote_expansion: false}]);
    assert.equal(app.setupReplaySkipPreviews, true);
    await app.setSetupReplaySkipPreviews(false);
    assert.deepEqual(requests.at(-1), ['/api/setup/replay/skip-link-previews', 'PUT', {skip_link_previews: false}]);
    assert.equal(app.setupReplaySkipPreviews, false);
    assert.equal(app.setupReplaySkipQuotes, false);
    const markup = fs.readFileSync(path.join(ROOT, 'tweetnook/web/index.html'), 'utf8');
    assert.match(markup, /Use placeholder media[\s\S]*Skip quote expansion during sync[\s\S]*setSetupReplaySkipQuotes/);

    await app.disconnectSetupReplay();
    assert.equal(app.setupReplayConnected, false);
    assert.equal(app.setupReplayForm.url, 'http://192.168.1.20:8000', 'the address is kept for reconnecting');
});

test('setup password step validates matching passwords before finishing', async () => {
    const context = browserContext();
    const bodies = [];
    context.fetch = async (url, options = {}) => {
        assert.equal(url, '/api/setup/complete');
        bodies.push(JSON.parse(options.body));
        return { ok: true, async json() { return { completed: true, reload: false }; } };
    };
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.setupData = { web: { password_configured: false }, archive: {}, auth: {} };

    assert.equal(app.setupCanFinish, false);
    app.setupPassword = 'secret-1';
    app.setupPasswordConfirm = 'secret';
    assert.equal(app.setupPasswordMismatch, false, 'a prefix still being typed is not an error');
    assert.equal(app.setupCanFinish, false);
    app.setupPasswordConfirm = 'secret-2';
    assert.equal(app.setupPasswordMismatch, true);
    await app.finishSetup();
    assert.equal(bodies.length, 0);

    app.setupPasswordConfirm = 'secret-1';
    assert.equal(app.setupCanFinish, true);
    await app.finishSetup();
    assert.deepEqual(bodies, [{ skip_auth: false, password: 'secret-1', confirm_password: 'secret-1' }]);

    // An existing password may be kept by leaving both fields empty.
    app.setupData.web.password_configured = true;
    assert.equal(app.setupCanFinish, true);

    // No password is an explicit choice; typed passwords are not sent with it.
    app.setupData.web = { password_configured: false };
    app.setupPassword = 'secret-1';
    app.setupPasswordConfirm = 'other';
    app.setupNoPassword = true;
    assert.equal(app.setupCanFinish, true);
    assert.match(app.setupNoPasswordWarning, /other devices on your network/);
    app.setupData.web = { password_configured: true };
    assert.match(app.setupNoPasswordWarning, /other devices on your network.*current password is removed/);
    // Finishing without a password asks first; declining sends nothing.
    const sent = bodies.length;
    app.askSetupConfirm = async options => { assert.equal(options.body, app.setupNoPasswordWarning); return false; };
    await app.finishSetup();
    assert.equal(bodies.length, sent);
    app.askSetupConfirm = async () => true;
    await app.finishSetup();
    assert.deepEqual(bodies.at(-1), { skip_auth: false, no_password: true });
});

test('setup cookie help loads the generated guide and opens on the current browser', async () => {
    const context = browserContext();
    const help = JSON.parse(fs.readFileSync(path.join(ROOT, 'tweetnook/web/static/setup-help.json'), 'utf8'));
    let loads = 0;
    context.fetch = async url => {
        assert.equal(url, '/static/setup-help.json');
        loads++;
        return { ok: true, async json() { return help; } };
    };
    context.navigator = { ...(context.navigator || {}), userAgent: 'Mozilla/5.0 (Macintosh) AppleWebKit/605.1.15 Version/18.0 Safari/605.1.15' };
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());

    await app.openSetupHelp();
    assert.equal(app.setupHelpOpen, true);
    assert.equal(app.setupHelp.browsers[app.setupHelpBrowser].name, 'Safari');
    assert.equal(app.setupHelpDefaultBrowser('Mozilla/5.0 Firefox/131.0'), help.browsers.findIndex(b => b.name === 'Firefox'));
    assert.equal(app.setupHelpDefaultBrowser('Mozilla/5.0 Chrome/129.0 Safari/537.36'), 0);
    assert.equal(app.setupHelpTabLabel(help.browsers[0]), 'Chrome');
    assert.match(app.setupHelpAlsoFor(help.browsers[0]), /^Also for Chromium, Brave/);
    assert.equal(app.setupHelpAlsoFor({ name: 'Safari' }), '');

    // Escape closes the help sheet before it would close Setup.
    app.showSetupModal = true;
    app.closeSetup();
    assert.equal(app.setupHelpOpen, false);
    assert.equal(app.showSetupModal, true);
    await app.openSetupHelp();
    assert.equal(loads, 1, 'the guide is fetched once');
});

test('setup uses a dedicated responsive X-style modal shell', () => {
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook/web/index.html'), 'utf8');
    const css = fs.readFileSync(path.join(ROOT, 'tweetnook/web/static/css/styles.css'), 'utf8');
    const js = fs.readFileSync(path.join(JS_DIR, 'app.js'), 'utf8');
    const settingsMarkup = html.slice(html.indexOf('<!-- Settings Modal'), html.indexOf('<!-- Setup Modal -->'));
    const settingsJs = fs.readFileSync(path.join(JS_DIR, 'settings.js'), 'utf8');
    const setupMarkup = html.slice(html.indexOf('<!-- Setup Modal -->'), html.indexOf('<!-- Automated Tagging Spend History Modal -->'));

    assert.match(settingsMarkup, /x-show="showSettingsModal"/);
    assert.match(settingsMarkup, /@click="nookSettingsNavigate\(\$data, 'setup'\)"/);
    assert.match(settingsJs, /if \(key === 'setup' && !app\.isDemo\) app\.fetchSetup\(\);/);
    assert.match(settingsMarkup, /@click="rerunSetup\(\)"/);
    assert.match(setupMarkup, /x-show="showSetupModal"/);
    assert.match(setupMarkup, /class="setup-desktop-required" role="note"/);
    assert.match(setupMarkup, /@click="clearSetupArchive\(\)"/);
    assert.match(setupMarkup, /@click="goToSetupStep\(setupFlowStep - 1\)"/);
    assert.match(setupMarkup, /class="setup-modal-footer"/);
    // The replay source section only appears with developer tools, and replay mode is always labelled.
    assert.match(setupMarkup, /<section class="setup-options setup-replay" x-show="setupData\.dev\?\.tools"/);
    assert.match(setupMarkup, /x-show="setupReplayConnected"[^>]*class="nook-demo-badge setup-replay-badge">Replay mode</);
    assert.match(html, /x-show="setupReplayConnected" x-cloak class="nook-demo-badge"[^>]*>Replay mode</);
    // Going back to step 1 after an earlier choice still offers an archive upload.
    assert.match(setupMarkup, /class="setup-upload-zone" x-show="!setupUploading && !setupData\.archive\.uploaded && setupArchiveStatus !== 'importing'"/);
    assert.doesNotMatch(setupMarkup, /<details/);
    assert.doesNotMatch(js, /alert\(error\.message\)|confirm\('Clear the uploaded/);
    assert.match(setupMarkup, /@click="if \(!setupAuthSuccessPending\) saveSetupAuth\(\)" :disabled="setupSavingAuth"/);
    assert.match(css, /@media \(max-width: 767px\)[\s\S]*?\.setup-modal-progress,[\s\S]*?\.setup-modal-content\s*\{ display: none !important; \}/);
    assert.match(js, /openSetup\(\)\s*\{[\s\S]*?this\.showSetupModal = true;[\s\S]*?this\.showSettingsModal = false;/);
    assert.match(js, /openSettings\(\)\s*\{[\s\S]*?this\.showSetupModal = false;[\s\S]*?this\.showSettingsModal = true;/);
});

test('config saves only diffs and resets only eligible explicit fields', async () => {
    const context = browserContext();
    const requests = [];
    const plain = value => JSON.parse(JSON.stringify(value));
    let failNext = false;
    const effective = {
        auth: { auth_token: '********', ct0: null, user_id: '123' },
        sync: { page_delay: 2 },
        web: { host: '127.0.0.1', port: 8000, fetch_avatars: true },
        database: { cache_size_kb: 524288, mmap_size_bytes: 1073741824 },
        tagging: { api_key: '********', enabled: false },
    };
    let explicit = ['auth.auth_token', 'sync.page_delay', 'database.cache_size_kb', 'tagging.api_key'];
    context.fetch = async (url, options = {}) => {
        if (url === '/api/config/schema') return { ok: true, async json() { return { whitelist: [], blacklist: [], full_width: [], types: {} }; } };
        if (url === '/api/config/defaults') return { ok: true, async json() { return JSON.parse(JSON.stringify(effective)); } };
        if (url === '/api/config' && !options.method) {
            return { ok: true, async json() { return { values: effective, explicit }; } };
        }
        requests.push(JSON.parse(options.body));
        if (failNext) {
            failNext = false;
            return { ok: false, async text() { return 'invalid'; } };
        }
        const changes = requests.at(-1).changes;
        for (const [field, value] of Object.entries(changes)) {
            const [section, key] = field.split('.');
            if (value === null) {
                explicit = explicit.filter(item => item !== field);
            } else {
                effective[section][key] = value;
                if (!explicit.includes(field)) explicit.push(field);
            }
        }
        return { ok: true, async json() { return { values: effective, explicit }; } };
    };
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());

    await app.fetchConfig();
    app.showAdvancedConfig = true;
    assert.deepEqual(plain(app.changedConfigValues()), {});
    await app.saveConfig();
    assert.equal(requests.length, 0);

    app.configData.web.port = 9000;
    await app.saveConfig();
    assert.deepEqual(plain(requests.at(-1)), { changes: { 'web.port': 9000 } });
    assert.equal(app.configOriginal.web.port, 9000);

    app.configData.database.mmap_size_bytes = 536870912;
    await app.saveConfig();
    assert.deepEqual(plain(requests.at(-1)), { changes: { 'database.mmap_size_bytes': 536870912 } });
    assert.equal('database.cache_size_kb' in requests.at(-1).changes, false);

    await app.resetConfigField('database', 'cache_size_kb');
    assert.deepEqual(plain(requests.at(-1)), { changes: { 'database.cache_size_kb': null } });

    await app.restoreConfigDefaults();
    assert.deepEqual(plain(requests.at(-1)), {
        changes: {
            'sync.page_delay': null,
            'database.mmap_size_bytes': null,
            'web.port': null,
        },
    });
    assert.equal('auth.auth_token' in requests.at(-1).changes, false);
    assert.equal('tagging.api_key' in requests.at(-1).changes, false);

    app.configData.web.port = 7000;
    failNext = true;
    await app.saveConfig();
    assert.equal(app.configData.web.port, 7000);
    assert.notEqual(app.configOriginal.web.port, 7000);
});

test('config boolean controls bind actual booleans', () => {
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'index.html'), 'utf8');
    assert.match(html, /role="switch"[^>]*:aria-checked="nookConfigOn\(\$data, field\)"[^>]*@click="nookConfigToggle\(\$data, field\)"/);
    assert.doesNotMatch(html, /<option value="(?:true|false)">/);
    const { nookConfigToggle } = loadScripts(browserContext(), ['settings.js'], '({nookConfigToggle})');
    const app = { configData: { web: { fetch_avatars: true }, sync: { reply_linked_depth: 0 } } };
    nookConfigToggle(app, { path: 'web.fetch_avatars', section: 'web', key: 'fetch_avatars', type: 'boolean' });
    assert.equal(app.configData.web.fetch_avatars, false);
    nookConfigToggle(app, { path: 'web.fetch_avatars', section: 'web', key: 'fetch_avatars', type: 'boolean' });
    assert.equal(app.configData.web.fetch_avatars, true);
    // Number fields shown as a switch keep their numeric type.
    nookConfigToggle(app, { path: 'sync.reply_linked_depth', section: 'sync', key: 'reply_linked_depth', type: 'toggle01' });
    assert.equal(app.configData.sync.reply_linked_depth, 1);
});

test('tweet text decodes one entity layer before safe rendering and length checks', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    const text = 'Fish &amp; chips &lt;3 &quot;yes&quot; &apos;ok&apos;';
    const expected = 'Fish &amp; chips &lt;3 "yes" \'ok\'';
    assert.equal(app.decodeTweetText(text), 'Fish & chips <3 "yes" \'ok\'');
    assert.equal(app.formatText({ text }), expected);
    assert.equal(app.formatText({ raw_json: { legacy: { full_text: text } } }), expected);
    assert.equal(app.formatText({ legacy: { full_text: text } }), expected); // Raw quote/reply object.
    assert.equal(app.formatText({ text: 'Already & decoded <3' }), 'Already &amp; decoded &lt;3');
    assert.equal(app.formatText({ text: '&amp;lt; literal entity' }), '&amp;lt; literal entity');
    assert.equal(app.formatText({ text: '&unknown;' }), '&amp;unknown;');
    const attack = app.formatText({ text: '&lt;img src=x onerror=alert(1)&gt; @alice #topic' });
    assert.match(attack, /^&lt;img src=x onerror=alert\(1\)&gt;/);
    assert.doesNotMatch(attack, /<img/);
    assert.match(attack, /class="mention/);
    assert.match(attack, /class="hashtag/);
    const shortDecoded = app.formatText({ tweet_id: 'entity-length', text: 'A &amp; '.repeat(40) });
    assert.doesNotMatch(shortDecoded, /Show more/);
    assert.equal(shortDecoded, 'A &amp; '.repeat(40).trim());
});

test('result text bolds words, phrases, prefixes and hashtags safely', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.searchHighlightTerms = ['gif', '"night sky"', 'archiv*', '#python', 'cafe'];
    const tweet = {text: 'GIF gift gif night sky night blue sky Archives #Python python café Cafe\u0301 <gif> &amp; gif'};
    const html = app.formatText(tweet, false, true);
    assert.match(html, /^<strong>GIF<\/strong> gift <strong>gif<\/strong> <strong>night sky<\/strong> night blue sky <strong>Archives<\/strong>/);
    assert.match(html, /data-tag="Python"><strong>#Python<\/strong><\/span> python <strong>café<\/strong> <strong>Cafe\u0301<\/strong>/);
    assert.match(html, /&lt;<strong>gif<\/strong>&gt; &amp; <strong>gif<\/strong>/);
    assert.doesNotMatch(html, /<gif>|<strong>gift|<strong>night blue sky/);
    assert.doesNotMatch(app.formatText(tweet), /<strong>/); // Detail text stays plain.
    app.searchHighlightTerms = ['gif', 'gif*'];
    assert.equal(app.highlightSearchText('gif GIFS'), '<strong>gif</strong> <strong>GIFS</strong>');
    app.searchHighlightTerms = ['"gif*"'];
    assert.equal(app.highlightSearchText('gif gifts'), '<strong>gif</strong> gifts');
    app.searchHighlightTerms = ['"hello alice"'];
    assert.match(app.formatText({text: 'hello @alice'}, false, true), /<strong>hello <\/strong><span[^>]+><strong>@alice<\/strong>/);
});

test('result bolding preserves link destinations, attributes, entities and placeholders', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.searchHighlightTerms = ['gif', 'amp', 'class', 'script'];
    const html = app.formatText({text: 'gif &amp; https://t.co/x', raw_json: {legacy: {entities: {urls: [{
        url: 'https://t.co/x', expanded_url: 'https://example.com/gif?q=class&x=amp', display_url: 'example.com/gif<script>',
    }]}}}}, false, true);
    assert.match(html, /href="https:\/\/example.com\/gif\?q=class&amp;x=amp"/);
    assert.match(html, />example.com\/<strong>gif<\/strong>&lt;<strong>script<\/strong>&gt;<\/a>/);
    assert.doesNotMatch(html, /<script|&<strong>amp|class="<strong>|href="[^\"]*<strong>/);
    assert.doesNotMatch(app.formatText({availability: {state: 'not_archived', message: 'gif not archived'}}, false, true), /<strong>/);
});

test('feed result bolding applies to quotes and Show more and keeps mention/hashtag clicks', () => {
    const { app, list } = feedListApp();
    app.searchHighlightTerms = ['long', 'alice', '#topic'];
    app.tweets = [{tweet_id: '1', text: 'long @alice #topic ' + 'long '.repeat(80),
        author: {id: '2', username: 'bob', display_name: 'Bob'},
        quoted_tweet: {tweet_id: '3', text: 'long quote', author: {username: 'q', display_name: 'Q'}},
    }];
    app.renderFeedList(app.tweets);
    const card = list.children[0];
    assert.ok(card.querySelector('[data-action="quote"] strong'));
    let profile;
    app.openProfileCard = (event, tweet) => { profile = tweet.author.username; };
    dispatchFeed(list, 'click', card.querySelector('.mention strong'));
    assert.equal(profile, 'alice');
    let searched;
    app.search = () => { searched = app.searchQuery; };
    dispatchFeed(list, 'click', card.querySelector('.hashtag strong'));
    assert.equal(searched, '#topic');
    const text = card.querySelector('[data-action="text"]');
    dispatchFeed(list, 'click', text.querySelector('.expand-tweet'));
    assert.equal(text.querySelector('.expand-tweet'), null);
    assert.equal(text.querySelectorAll('strong').length, 83);
    assert.equal(list.children[0], card);
});

test('media quoting media is compact in lists and flush in the detail view', () => {
    const { app, list } = feedListApp();
    const author = { id: '2', username: 'a', display_name: 'A' };
    const photo = { type: 'photo', download: { local_path: 'media/outer.jpg' } };
    const video = {
        type: 'video',
        duration_millis: 21000,
        download: { local_path: 'media/q.mp4', thumbnail_local_path: 'media/q.jpg' },
    };
    const quoted_tweet = {
        tweet_id: '3',
        text: `quote ${'word '.repeat(80)}`,
        author: { username: 'q', display_name: 'Q' },
        raw_json: { in_reply_to_screen_name: 'frank' },
    };
    const doubleMedia = { tweet_id: '1', text: 'x', author, media: [photo], quoted_tweet, qt_media: [video] };

    assert.equal(app.isMediaQuotingMedia(doubleMedia), true);
    assert.equal(app.isMediaQuotingMedia({ media: [], qt_media: [video] }), false);
    assert.equal(app.isMediaQuotingMedia({ media: [photo] }), false);
    assert.equal(app.isMediaQuotingMedia(null), false);

    // Lists: a thumbnail with the duration badge beside the reply line and clamped
    // text, never Show more.
    const compact = app.renderFeedCard(doubleMedia);
    assert.ok(compact.includes(app.renderQuoteCard(doubleMedia)), 'feed quote markup comes from the shared renderer');
    assert.match(compact, /cursor-pointer quote-compact" data-thread-id="3" data-action="quote"/);
    assert.match(compact, /<div class="quote-reply tweet-reply-context text-\[15px\][^"]*">Replying to <span[^>]*data-username="frank">@frank<\/span>/);
    assert.match(compact, /<div class="quote-media"><div class="quote-thumbnail"><img src="\/media\/q\.jpg" loading="lazy" decoding="async" alt=""><span class="quote-thumbnail-badge quote-thumbnail-duration">0:21<\/span><\/div><\/div>/);
    assert.match(compact, /class="quote-text text-\[15px\]/);
    assert.doesNotMatch(compact, /media-video-player|expand-tweet/);

    // A quote under a tweet without media keeps the full media grid.
    const regular = app.renderFeedCard({ ...doubleMedia, tweet_id: '4', media: [] });
    assert.doesNotMatch(regular, /quote-compact|quote-thumbnail/);
    assert.match(regular, /media-video-player/);
    assert.match(regular, /expand-tweet/);

    // The thumbnail has no controls, so a click opens the quoted tweet.
    app.tweets = [doubleMedia];
    app.renderFeedList(app.tweets);
    let opened;
    app.openThread = id => { opened = id; };
    dispatchFeed(list, 'click', list.children[0].querySelector('.quote-thumbnail img'));
    assert.equal(opened, '3');

    // Several items tile in a small grid of up to four, without badges.
    const pair = app.renderQuoteThumbnail([{ type: 'photo', download: { local_path: 'media/p.jpg' } }, video]);
    assert.match(
        pair,
        /^<div class="quote-thumbnail quote-thumbnail-grid"><div class="quote-thumbnail-cell"><img src="\/media\/p\.jpg"[^>]*><\/div><div class="quote-thumbnail-cell"><img src="\/media\/q\.jpg"[^>]*><\/div><\/div>$/,
    );
    const videos = app.renderQuoteThumbnail(Array(5).fill(video));
    assert.equal((videos.match(/class="quote-thumbnail-cell"/g) || []).length, 4);
    assert.doesNotMatch(videos, /quote-thumbnail-badge/);
    const gif = app.renderQuoteThumbnail([{ type: 'animated_gif', download: { local_path: 'media/g.mp4' } }]);
    assert.match(gif, /<video src="\/media\/g\.mp4#t=0\.1" preload="metadata" muted playsinline><\/video><span class="quote-thumbnail-badge">GIF<\/span>/);
    assert.doesNotMatch(gif, /autoplay|controls/);
    assert.match(app.renderQuoteThumbnail([{ type: 'video' }]), /aria-label="Media not downloaded"/);
    assert.equal(app.renderQuoteThumbnail([]), '');

    const html = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'index.html'), 'utf8');
    for (const name of ['child', 'reply']) {
        assert.equal(html.split(`:class="{ 'quote-compact': isMediaQuotingMedia(${name}) }"`).length - 1, 2);
        assert.equal(html.split(`isMediaQuotingMedia(${name}) ? renderQuoteThumbnail(${name}.qt_media) : renderMediaGrid(${name}.qt_media, 'quote')`).length - 1, 2);
        assert.equal(html.split(`@click="if (!isMediaQuotingMedia(${name})) $event.stopPropagation()"`).length - 1, 2);
        const replyLines = html.split(`<template x-if="getReplyRecipients({raw_json: getQuoteTweet(${name})}).length">`).slice(1);
        assert.equal(replyLines.length, 2);
        for (const line of replyLines) {
            assert.ok(line.includes('class="quote-reply tweet-reply-context'));
            assert.ok(line.includes(`renderReplyRecipientContent({raw_json: getQuoteTweet(${name})})`));
        }
    }
    for (const main of ['threadData?.main', 'panelThreadData?.main']) {
        const mainQuote = html.split(`:data-thread-id="getTweetId(getQuoteTweet(${main}))"`)[0].split('\n').at(-1);
        assert.match(mainQuote, /class="quote-card quote-card-detail /);
        const mediaOwner = main === 'threadData?.main' ? 'threadData' : 'panelThreadData';
        assert.ok(html.includes(`class="main-tweet-quote" :class="{ 'main-tweet-quote-after-single-media': (${mediaOwner}.main.media || []).length === 1 }"`));
        assert.ok(html.includes(`<div class="quote-media" x-html="renderMediaGrid(${main}?.qt_media, (${mediaOwner}.main.media || []).length ? 'quote-detail-expanded' : 'quote-detail')" @click.stop></div>`));
    }
    assert.equal((html.match(/renderMediaGrid\((?:threadData|panelThreadData)\.main\.media, 'detail'\)/g) || []).length, 2);
    const css = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'static', 'css', 'styles.css'), 'utf8');
    assert.match(css, /\.quote-compact > \.quote-text \{[^}]*-webkit-line-clamp: 4;/);
    assert.match(css, /\.quote-compact \{[^}]*grid-template-columns: calc\(\(100% \+ 12px\) \/ 5\) minmax\(0, 1fr\);/);
    assert.match(css, /\.quote-compact > \.quote-media \{[^}]*margin-top: 4px;/);
    assert.match(css, /\.quote-compact:not\(:has\(> \.quote-reply\)\) > \.quote-text \{[^}]*margin-top: 8px;/);
    assert.match(css, /\.quote-compact \.quote-thumbnail \{[^}]*width: 100%;[^}]*aspect-ratio: 1;[^}]*border-radius: 16px;/);
    assert.match(css, /\.quote-compact > \.quote-reply \{[^}]*grid-row: 2;/);
    assert.match(css, /\.quote-thumbnail-cell:first-child:nth-last-child\(3\) \{[^}]*grid-row: span 2;/);
    assert.match(css, /\.quote-card:not\(\.quote-compact\) > \.quote-media:not\(:empty\) \{[^}]*margin: 16px -12px 0;/);
});

test('thread parents render shared quotes with the right layout, interactions, and source data intact', () => {
    const { app } = feedListApp();
    const video = {
        type: 'video',
        duration_millis: 21000,
        download: { local_path: 'media/parent-quote.mp4', thumbnail_local_path: 'media/parent-quote.jpg' },
    };
    const quote = {
        tweet_id: 'quoted-parent',
        text: `A quoted post ${'word '.repeat(80)}`,
        author: { id: 'quote-author', username: 'q', display_name: 'Quote Author' },
        raw_json: {
            in_reply_to_screen_name: 'frank',
            birdwatch_pivot: {
                shorttitle: 'Quoted note',
                subtitle: { text: 'Context from the note', entities: [] },
            },
        },
    };
    const standaloneParent = {
        tweet_id: 'parent-regular', text: 'Parent text', author: { username: 'p' },
        media: [], quoted_tweet: quote, qt_media: [video],
    };
    const mediaParent = {
        ...standaloneParent,
        tweet_id: 'parent-media',
        media: [{ type: 'photo', download: { local_path: 'media/parent.jpg' } }],
    };
    const originalPayloads = JSON.parse(JSON.stringify([standaloneParent, mediaParent]));

    const regular = app.renderQuoteCard(standaloneParent);
    assert.match(regular, /class="quote-card[^\"]*"[^>]*data-thread-id="quoted-parent" data-action="quote"/);
    assert.doesNotMatch(regular, /quote-compact|quote-thumbnail/);
    assert.match(regular, /media-video-player/);
    assert.match(regular, /expand-tweet/);
    assert.match(regular, /community-note-quote/);
    assert.ok(regular.indexOf('class="quote-media"') < regular.indexOf('community-note-quote'));
    const regularRoot = parseFakeHTML(regular)[0];
    assert.equal(regularRoot.querySelector('.quote-media').parentElement, regularRoot);
    assert.equal(regularRoot.querySelector('.quote-reply').parentElement, regularRoot);
    const regularWrapper = parseFakeHTML(`<div>${regular}</div>`)[0];
    app.handleThreadParentQuoteClick({
        target: regularWrapper.querySelector('.expand-tweet'),
        currentTarget: regularWrapper,
        stopPropagation() {},
    }, standaloneParent);
    assert.equal(regularWrapper.querySelector('.expand-tweet'), null);
    assert.equal(regularWrapper.querySelector('.quote-text').querySelectorAll('strong').length, 0);

    const compact = app.renderQuoteCard(mediaParent);
    assert.match(compact, /quote-card[^\"]*quote-compact/);
    assert.match(compact, /class="quote-reply tweet-reply-context[^\"]*"[^>]*>Replying to/);
    assert.match(compact, /class="quote-media"><div class="quote-thumbnail"><img src="\/media\/parent-quote.jpg"/);
    assert.match(compact, /community-note-quote-media/);
    assert.ok(compact.indexOf('class="quote-media"') < compact.indexOf('community-note-quote-media'));
    const compactRoot = parseFakeHTML(compact)[0];
    assert.equal(compactRoot.querySelector('.quote-media').parentElement, compactRoot);
    assert.equal(compactRoot.querySelector('.quote-reply').parentElement, compactRoot);

    // The same data-action delegation used by feed quotes opens a parent quote and
    // routes reply mentions to profile cards.
    let opened;
    app.openThread = id => { opened = id; };
    const wrapper = parseFakeHTML(`<div>${compact}</div>`)[0];
    app.handleThreadParentQuoteClick({
        target: wrapper.querySelector('.quote-thumbnail img'), currentTarget: wrapper, stopPropagation() {},
    }, mediaParent);
    assert.equal(opened, 'quoted-parent');
    let profile;
    app.openUsernameProfileCard = (_event, username) => { profile = username; };
    let stopped = false;
    app.handleThreadParentQuoteClick({
        target: wrapper.querySelector('[data-action="reply-profile"]'), currentTarget: wrapper,
        stopPropagation() { stopped = true; },
    }, mediaParent);
    assert.equal(profile, 'frank');
    assert.equal(stopped, true);

    assert.deepEqual([standaloneParent, mediaParent], originalPayloads);
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'index.html'), 'utf8');
    const parentRenderer = 'x-html="renderQuoteCard(parent)" @click.stop="handleThreadParentQuoteClick($event, parent)" @load.capture="avatarEvent($event)" @error.capture="avatarEvent($event)"';
    assert.equal(html.split(parentRenderer).length - 1, 2);
});

test('a quote shows its own quote nested inside it, as X does', () => {
    const { app, list } = feedListApp();
    const photo = { type: 'photo', download: { local_path: 'media/n.jpg' } };
    const deepest = {
        tweet_id: '9',
        text: `nested ${'word '.repeat(80)}`,
        author: { id: '7', username: 'n', display_name: 'N' },
        created_at: 'Fri Oct 09 18:25:33 +0000 2026',
        raw_json: { birdwatch_pivot: { subtitle: { text: 'nested note' } } },
        quoted_tweet: { tweet_id: '10', text: 'too deep', author: { username: 'd' } },
    };
    const quote = {
        tweet_id: '3',
        text: 'middle',
        author: { username: 'q', display_name: 'Q' },
        raw_json: { birdwatch_pivot: { subtitle: { text: 'middle note' } } },
        quoted_tweet: deepest,
        qt_media: [photo],
    };
    const tweet = { tweet_id: '1', text: 'outer', author: { username: 'a' }, quoted_tweet: quote, qt_media: [] };

    // A bar-marked block after the quote's content and before its note, with a
    // small thumbnail beside clamped text; nothing deeper and no note of its own.
    const html = app.renderFeedCard(tweet);
    assert.match(html, /<div class="quote-nested quote-nested-media" data-thread-id="9" data-action="quote">/);
    assert.match(html, /<div class="quote-nested[^>]*>.*@n<\/span>.*<div class="quote-media"><div class="quote-thumbnail"><img src="\/media\/n\.jpg"/);
    assert.ok(html.indexOf('quote-nested') < html.indexOf('middle note'));
    assert.doesNotMatch(html, /too deep|nested note|expand-tweet/);
    assert.equal((html.match(/class="quote-nested/g) || []).length, 1);
    // Like X, the quote's avatar is 24px and the nested quote's 20px.
    assert.deepEqual(html.match(/class="avatar w-\d h-\d/g), ['class="avatar w-6 h-6', 'class="avatar w-5 h-5']);

    // Without media it is text only; unavailable nested quotes are left out.
    const textOnly = app.renderNestedQuote({ ...quote, qt_media: [] });
    assert.match(textOnly, /^<div class="quote-nested" data-thread-id="9" data-action="quote">/);
    assert.doesNotMatch(textOnly, /quote-thumbnail/);
    const missing = { tweet_id: '9', availability: { placeholder: true } };
    assert.equal(app.renderNestedQuote({ ...quote, quoted_tweet: missing }), '');
    assert.equal(app.renderNestedQuote(null), '');

    // In lists a click on the nested quote opens it, and elsewhere opens the quote.
    app.tweets = [tweet];
    app.renderFeedList(app.tweets);
    let opened;
    app.openThread = id => { opened = id; };
    const card = list.children[0];
    const [quoteText, nestedText] = card.querySelectorAll('.quote-text');
    dispatchFeed(list, 'click', nestedText);
    assert.equal(opened, '9');
    dispatchFeed(list, 'click', quoteText);
    assert.equal(opened, '3');

    // Thread templates render it with x-html; their click handler opens it too.
    opened = null;
    const event = { target: nestedText, propagationStopped: false, stopPropagation() { this.propagationStopped = true; } };
    app.openNestedQuote(event);
    assert.equal(opened, '9');
    assert.equal(event.propagationStopped, true);
    app.openNestedQuote({ target: quoteText, stopPropagation() { throw new Error('outside'); } });

    const template = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'index.html'), 'utf8');
    for (const name of ['threadData?.main', 'panelThreadData?.main', 'child', 'reply']) {
        const slot = `<div x-html="renderNestedQuote(getQuoteTweet(${name}))" @click="openNestedQuote($event)" @load.capture="avatarEvent($event)" @error.capture="avatarEvent($event)"></div>`;
        const hasMedia = name.endsWith('main') ? name.replace('?.main', '?.main?.qt_media?.length') : `${name}?.qt_media?.length`;
        assert.equal(template.split(slot).length - 1, name.endsWith('main') ? 1 : 2);
        assert.ok(template.includes(`${slot}\n${' '.repeat(template.split(slot)[0].split('\n').at(-1).length)}<div x-html="renderCommunityNote(getQuoteTweet(${name}), true, false, Boolean(${hasMedia}))"`));
    }
    const css = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'static', 'css', 'styles.css'), 'utf8');
    assert.match(css, /\.quote-nested \{[^}]*margin-top: 12px;[^}]*padding: 8px 0 8px 15px;/);
    assert.match(css, /\.quote-text \+ \.quote-nested,\n\.quote-text \+ :empty \+ \.quote-media:empty \+ \* > \.quote-nested \{[^}]*margin-top: 6px;/);
    assert.match(css, /\.quote-nested > \.quote-text \{[^}]*-webkit-line-clamp: 2;/);
    assert.match(css, /\.quote-nested \.quote-thumbnail \{[^}]*width: 64px;/);
});

test('result highlights follow the returned query rather than edits to the search box', async () => {
    const { app, list, context } = feedListApp();
    context.fetch = async () => ({ok: true, status: 200, json: async () => ({
        tweets: [{tweet_id: '1', text: 'GIF cats', author: {username: 'a'}}],
        page: 1, total: 1, pages: 1, has_more: false, highlight_terms: ['gif'],
    })});
    app.searchQuery = 'gif is:reply';
    await app.fetchTweets();
    assert.equal(list.children[0].querySelector('strong').textContent, 'GIF');
    app.searchQuery = 'cats';
    assert.match(app.formatText(app.tweets[0], false, true), /^<strong>GIF<\/strong> cats$/);
    context.fetch = async () => ({ok: true, status: 200, json: async () => ({
        tweets: app.tweets, page: 1, total: 1, pages: 1, has_more: false, highlight_terms: [],
    })});
    await app.fetchTweets();
    assert.equal(list.children[0].querySelector('strong'), null);
});

test('demo hides captured media and quote links while preserving ordinary links', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    vm.runInContext(fs.readFileSync(path.join(ROOT, 'demo', 'demo-api.js'), 'utf8'), context);
    const fixture = JSON.parse(fs.readFileSync(path.join(ROOT, 'demo', 'data.json'), 'utf8'));
    const store = context.window.TweetNookDemo.createStore(fixture);
    const app = immediateComponent(tweetApp());
    const mediaOnly = store.hydrate('2087274031749927040');
    assert.equal(app.formatText(mediaOnly, true), '');
    assert.equal(mediaOnly.text, 'https://t.co/iJmE5Nu84I');
    assert.equal(mediaOnly.media.length, 1);
    for (const source of fixture.tweets) {
        const html = app.formatText(store.hydrate(source.tweet_id), true);
        for (const url of [...(source.media_short_urls || []), source.quote_short_url].filter(Boolean)) {
            assert.ok(!html.includes(url), `${source.tweet_id} displays attachment URL ${url}`);
        }
    }
    const mixed = fixture.tweets.find(tweet => tweet.tweet_id === '2087274031749927040');
    mixed.text = 'Read https://t.co/story https://t.co/iJmE5Nu84I';
    mixed.urls = [{url: 'https://t.co/story', expanded_url: 'https://example.com/story', display_url: 'example.com/story'}];
    const regular = app.formatText(context.window.TweetNookDemo.createStore(fixture).hydrate(mixed.tweet_id), true);
    assert.match(regular, /href="https:\/\/example.com\/story"/);
    assert.match(regular, />example.com\/story<\/a>/);
    assert.ok(!regular.includes('https://t.co/iJmE5Nu84I'));
    mixed.text = 'A quote https://t.co/quote';
    mixed.quote_short_url = 'https://t.co/quote';
    assert.equal(app.formatText(context.window.TweetNookDemo.createStore(fixture).hydrate(mixed.tweet_id), true), 'A quote');
});

test('text and card renderers escape HTML and reject active URL schemes', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    const formatted = app.formatText({
        tweet_id: '1',
        text: '<img src=x onerror=alert(1)> @alice #topic https://t.co/x',
        raw_json: {
            legacy: {
                entities: {
                    urls: [
                        {
                            url: 'https://t.co/x',
                            expanded_url: 'javascript:alert(1)',
                            display_url: '<svg onload=alert(1)>',
                        },
                    ],
                },
            },
        },
    });
    assert.match(formatted, /&lt;img/);
    assert.match(formatted, /class="mention/);
    assert.match(formatted, /class="hashtag/);
    assert.match(formatted, /href="#"/);
    assert.match(formatted, /&lt;svg/);
    assert.doesNotMatch(formatted, /<img src=x/);

    const card = app.renderCard({
        raw_json: {
            card: {
                name: 'summary_large_image',
                binding_values: [
                    { key: 'title', value: { string_value: '<img onerror=x>' } },
                    { key: 'description', value: { string_value: '<script>x</script>' } },
                    { key: 'card_url', value: { string_value: 'javascript:x' } },
                    {
                        key: 'thumbnail_image_original',
                        value: { image_value: { url: 'data:text/html,x' } },
                    },
                ],
            },
        },
    });
    assert.match(card, /href="#"/);
    assert.doesNotMatch(card, /<img src=/);
    assert.match(card, /&lt;img onerror=x&gt;/);
    assert.doesNotMatch(card, /<script>/);
});

test('availability placeholders replace content and support normalized attachments', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    const unavailable = {
        tweet_id: 'missing',
        text: 'stale private text',
        raw_json: { card: { name: 'summary' } },
        availability: {
            placeholder: true,
            reason: 'protected_account',
            message: 'This tweet is from a protected account. <unsafe>',
        },
    };

    const placeholder = app.formatText(unavailable);
    assert.match(placeholder, /tweet-skeleton/);
    assert.match(placeholder, /tweet-skeleton-content/);
    assert.match(placeholder, /data-tweet-skeleton="true"/);
    assert.match(placeholder, /data-availability-reason="protected_account"/);
    assert.match(placeholder, /This tweet is from a protected account\. &lt;unsafe&gt;/);
    assert.ok(placeholder.indexOf('tweet-skeleton-message') < placeholder.indexOf('tweet-skeleton-lines'));
    assert.doesNotMatch(placeholder, /<unsafe>/);
    assert.doesNotMatch(placeholder, /stale private text/);
    assert.equal(app.renderCard(unavailable), '');
    assert.equal(app.renderActionBar(unavailable), '');
    assert.equal(app.getQuoteTweet(unavailable), null);

    const wrapper = {
        quoted_tweet: unavailable,
        retweeted_tweet: unavailable,
        raw_json: {},
    };
    assert.equal(app.getQuoteTweet(wrapper), unavailable);
    assert.equal(app.getQuoteText(app.getQuoteTweet(wrapper)), unavailable.availability.message);
    assert.equal(app.getRetweet(wrapper), unavailable);
    assert.equal(app.getTweetId(unavailable), 'missing');

    const quotePlaceholder = app.renderQuotePlaceholder(unavailable);
    assert.match(quotePlaceholder, /tweet-skeleton tweet-skeleton-compact/);
    assert.match(quotePlaceholder, /data-tweet-skeleton="true"/);
    assert.match(quotePlaceholder, /data-availability-reason="protected_account"/);
    assert.match(quotePlaceholder, /aria-label="Quoted tweet unavailable"/);
    assert.match(quotePlaceholder, /This tweet is from a protected account\. &lt;unsafe&gt;/);
    assert.doesNotMatch(quotePlaceholder, /stale private text/);

    const missingText = app.formatText({
        tweet_id: 'textless',
        text: '',
        raw_json: { rest_id: 'textless', legacy: {} },
    });
    assert.match(missingText, /data-availability-reason="text_not_archived"/);
    assert.match(missingText, /tweet-skeleton-content/);
    assert.match(missingText, /Tweet text was not captured in the local archive\./);
    assert.equal(app.renderActionBar({
        tweet_id: 'textless',
        text: '',
        raw_json: { rest_id: 'textless', legacy: {} },
    }), '');
    assert.match(
        app.formatText({ rest_id: 'quoted-textless', legacy: {} }),
        /tweet-skeleton[\s\S]*data-availability-reason="text_not_archived"/,
    );
});

test('missing tweet skeletons cover terminal, relation, tombstone, and textless variants', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    const reasons = [
        'protected_account',
        'suspended_account',
        'account_missing',
        'deleted_by_author',
        'archive_deleted',
        'withheld',
        'not_found',
        'unavailable_unknown',
    ];

    for (const reason of reasons) {
        const missing = {
            tweet_id: `missing-${reason}`,
            availability: { placeholder: true, reason, message: `hidden ${reason}` },
        };
        const direct = app.renderTweetPlaceholder(missing);
        const quoted = app.renderQuotePlaceholder(missing);
        assert.match(direct, new RegExp(`data-availability-reason="${reason}"`));
        assert.match(quoted, new RegExp(`data-availability-reason="${reason}"`));
        assert.match(direct, /tweet-skeleton-content/);
        assert.match(quoted, /tweet-skeleton-compact/);
        assert.match(direct, new RegExp(`hidden ${reason}`));
        assert.match(quoted, new RegExp(`hidden ${reason}`));
        assert.ok(direct.indexOf('tweet-skeleton-message') < direct.indexOf('tweet-skeleton-lines'));
        assert.ok(quoted.indexOf('tweet-skeleton-message') < quoted.indexOf('tweet-skeleton-lines'));
    }

    const relationOnly = {
        tweet_id: 'missing-quoted',
        availability: { placeholder: true, reason: 'not_archived', message: 'not captured' },
    };
    const wrapper = { quoted_tweet: relationOnly, retweeted_tweet: relationOnly, raw_json: {} };
    assert.equal(app.getQuoteTweet(wrapper), relationOnly);
    assert.equal(app.getRetweet(wrapper), relationOnly);
    assert.match(app.renderQuotePlaceholder(app.getQuoteTweet(wrapper)), /tweet-skeleton-compact/);
    assert.match(app.renderTweetPlaceholder(app.getRetweet(wrapper)), /tweet-skeleton-content/);
    assert.match(app.renderQuotePlaceholder(relationOnly), /not captured/);

    const tombstone = { __typename: 'TweetTombstone', rest_id: 'tombstone-1' };
    assert.equal(app.isPlaceholderTweet(tombstone), true);
    assert.match(app.formatText(tombstone), /data-tweet-skeleton="true"/);
    assert.match(app.formatText(tombstone), /This tweet is unavailable\./);
    assert.match(app.renderQuotePlaceholder(tombstone), /data-availability-reason="unavailable_unknown"/);

    const css = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'static', 'css', 'styles.css'), 'utf8');
    assert.match(css, /\.tweet-skeleton/);
    assert.match(css, /\.tweet-skeleton-main \{[\s\S]*font-size: 0;[\s\S]*line-height: 0;/);
});

test('single media layout distinguishes portraits, square boundaries, GIFs and quotes', () => {
    const { app } = feedListApp();
    const layout = (type, width, height, context = 'feed', extra = {}) =>
        app.mediaFrameLayout({ type, width, height, ...extra }, context);

    // Captured square and landscape photos must fill detail columns, even when
    // their width/ratio would otherwise produce a height above 510px.
    for (const context of ['feed', 'detail', 'quote', 'quote-detail']) {
        assert.equal(layout('photo', 2400, 2400, context).maxWidth, null);
        assert.equal(layout('photo', 1200, 1168, context).photoCapWidth, null);
        assert.equal(layout('photo', 1200, 1168, context).maxWidth, null);
        assert.equal(layout('video', 1080, 1080, context).maxWidth, null);
    }
    const portrait = layout('photo', '1028', '1275', 'detail');
    assert.ok(Math.abs(portrait.maxWidth - 411.2) < 0.00001);
    assert.equal(portrait.photoCapWidth, portrait.maxWidth);
    assert.equal(layout('photo', 999, 1000, 'detail').maxWidth, 509.49);
    assert.equal(layout('photo', 1000, 1000, 'detail').maxWidth, null);
    assert.equal(layout('photo', 1001, 1000, 'detail').maxWidth, null);

    // Quoted photo previews crop very tall sources to 3:4. Only list previews
    // retain the 510px cap; a focal quote fills its available detail column.
    for (const context of ['quote', 'quote-detail']) {
        const quoted = layout('photo', 1536, 2048, context);
        assert.equal(quoted.maxWidth, null);
        assert.equal(quoted.photoCapWidth, context === 'quote' ? 382.5 : null);
        assert.equal(quoted.frameAspect, 0.75);
        assert.equal(quoted.portraitPhoto, context === 'quote');
        assert.equal(layout('video', 720, 1280, context).frameAspect, 16 / 9);
        assert.equal(layout('animated_gif', 498, 402, context).frameAspect, 16 / 9);
    }
    for (const [width, height] of [[360, 640], [600, 900], [750, 1000], [800, 1000]]) {
        const previewAspect = Math.max(width / height, 3 / 4);
        for (const context of ['quote', 'quote-detail', 'quote-detail-expanded']) {
            const photo = layout('photo', width, height, context);
            assert.equal(photo.sourceAspect, width / height);
            assert.equal(photo.frameAspect, previewAspect);
            assert.equal(photo.photoCapWidth, context === 'quote' ? 510 * previewAspect : null);
        }
        assert.equal(layout('photo', width, height).frameAspect, width / height);
    }
    const expandedQuote = layout('photo', 2816, 3072, 'quote-detail-expanded');
    assert.equal(expandedQuote.frameAspect, 2816 / 3072);
    assert.equal(expandedQuote.maxWidth, null);
    assert.equal(expandedQuote.photoCapWidth, null);
    assert.equal(expandedQuote.portraitPhoto, false);
    assert.equal(layout('video', 1639, 2048, 'quote-detail-expanded').frameAspect, 16 / 9);
    assert.equal(layout('animated_gif', 1639, 2048, 'quote-detail-expanded').frameAspect, 16 / 9);
    assert.ok(Math.abs(layout('video', 720, 742).maxWidth - 494.8787062) < 0.00001);
    assert.equal(layout('video', 480, 472).maxWidth, null);
    assert.equal(layout('animated_gif', 404, 512).frameAspect, 1);
    assert.equal(layout('animated_gif', 404, 512).maxWidth, null);
    assert.equal(layout('animated_gif', 746, 640).frameAspect, 1.165625);
    assert.equal(layout('photo', 637, 130).frameAspect, 4.9);
    assert.equal(layout('photo', 1634, 67).frameAspect, 5);

    // Playback metadata can differ from a poster; missing or invalid dimensions
    // must not leave a collapsed frame or produce invalid CSS values.
    const playback = layout('video', 1600, 900, 'detail', { aspect_ratio: [3, 4] });
    assert.equal(playback.sourceAspect, 0.75);
    assert.equal(playback.maxWidth, 382.5);
    assert.equal(layout('video', null, null, 'feed', { aspect_ratio: [3, 4] }).frameAspect, 0.75);
    for (const dimensions of [[null, null], [0, 100], [-2, 100], ['bad', 100], [Infinity, 2]]) {
        assert.equal(layout('photo', ...dimensions).frameAspect, 16 / 9);
        assert.equal(layout('animated_gif', ...dimensions).frameAspect, 1);
    }
    for (const aspect_ratio of [[true, 1], [0, 1], [1, -1], [Infinity, 1], [1], '3:4']) {
        assert.equal(layout('video', 16, 9, 'feed', { aspect_ratio }).frameAspect, 16 / 9);
    }
});

test('media renderer preserves direct playback parents, placeholders and existing galleries', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());

    const photo = app.renderMediaGrid([
        {
            type: 'photo',
            width: 1200,
            height: 600,
            download: { local_path: 'media/a.jpg' },
        },
    ]);
    assert.match(photo, /media-frame-feed/);
    assert.match(photo, /<img src="\/media\/a.jpg"/);

    const ordinaryQuotedPortrait = app.renderMediaGrid([
        { type: 'photo', width: 360, height: 640, download: { local_path: 'media/quoted.jpg' } },
    ], 'quote-detail');
    assert.match(ordinaryQuotedPortrait, /--media-frame-aspect: 0\.75;/);
    assert.doesNotMatch(ordinaryQuotedPortrait, /--media-photo-cap-width|media-frame-portrait-photo/);
    const originalPhoto = JSON.parse(parseFakeHTML(ordinaryQuotedPortrait)[0]
        .querySelector('[data-lightbox-media]').getAttribute('data-lightbox-media'))[0].m;
    assert.equal(originalPhoto.width, 360);
    assert.equal(originalPhoto.height, 640);

    const quotedListPhoto = app.renderMediaGrid([
        { type: 'photo', width: 360, height: 640, download: { local_path: 'media/quoted.jpg' } },
    ], 'quote');
    assert.match(quotedListPhoto, /--media-photo-cap-width: 382\.5px;/);
    assert.match(quotedListPhoto, /media-frame-portrait-photo/);

    const expandedQuotedPortrait = app.renderMediaGrid([
        { type: 'photo', width: 2816, height: 3072, download: { local_path: 'media/expanded-quoted.jpg' } },
    ], 'quote-detail-expanded');
    assert.match(expandedQuotedPortrait, /--media-frame-aspect: 0\.9166666666666666;/);
    assert.match(expandedQuotedPortrait, /class="[^"]*media-frame-quote-detail-expanded[^"]*"/);
    assert.doesNotMatch(expandedQuotedPortrait, /--media-photo-cap-width|media-frame-portrait-photo/);

    const video = app.renderMediaGrid([
        {
            type: 'video',
            duration_millis: 120000,
            download: {
                local_path: 'media/a.mp4',
                thumbnail_local_path: 'media/a.jpg',
            },
        },
    ]);
    assert.match(video, /controls/);
    assert.doesNotMatch(video, /autoplay muted/);
    assert.match(video, /class="[^"]*media-video-player[^"]*"/);
    assert.match(video, /class="media-video-duration" data-video-duration/);
    assert.match(video, /media-frame-video/);
    const parsedVideo = parseFakeHTML(video)[0];
    const player = parsedVideo.querySelector('video').parentElement;
    assert.ok(player.classList.contains('media-video-player'));
    assert.ok(player.querySelector('[data-video-duration]'));
    assert.match(video, /aria-label="Remaining video duration 2:00">2:00<\/span>/);
    assert.match(video, /onloadedmetadata="window\.tweetNookSyncVideoDuration\(this\)"/);
    assert.match(video, /ontimeupdate="window\.tweetNookSyncVideoDuration\(this\)"/);
    assert.match(video, /onended="window\.tweetNookSyncVideoDuration\(this\); window\.tweetNookSyncVideoUi\(this\)"/);
    assert.match(video, /onplay="window\.tweetNookShowVideoUi\(this\)"/);
    assert.match(video, /onpause="window\.tweetNookSyncVideoUi\(this\)"/);
    assert.doesNotMatch(video, /onfocus=|onblur=/);
    assert.equal(app.formatMediaDuration(3000), '0:03');
    assert.equal(app.formatMediaDuration(3723000), '1:02:03');
    assert.equal(app.formatMediaDuration(null), '');

    const gif = app.renderMediaGrid([
        {
            type: 'animated_gif',
            download: { local_path: 'media/a.mp4' },
        },
    ]);
    assert.match(gif, /autoplay muted playsinline/);
    assert.match(gif, /loop/);
    assert.match(gif, /data-animated-gif/);
    assert.match(gif, /data-gif-toggle aria-label="Pause GIF"/);
    assert.match(gif, /media-gif-pause-icon/);
    assert.match(gif, /media-gif-play-icon/);
    assert.match(gif, /media-frame-gif-video/);
    const gifPlayer = parseFakeHTML(gif)[0].querySelector('video').parentElement;
    assert.ok(gifPlayer.querySelector('[data-gif-toggle]'));
    assert.doesNotMatch(gif, /media-video-duration/);

    const missing = app.renderMediaGrid([{ type: 'photo', width: 4, height: 3 }]);
    assert.match(missing, /Media not downloaded/);
    assert.ok(parseFakeHTML(missing)[0].querySelector('.media-frame-placeholder'));

    const grid = app.renderMediaGrid([
        { type: 'photo', download: { local_path: 'media/1.jpg' } },
        { type: 'photo', download: { local_path: 'media/2.jpg' } },
        { type: 'photo', download: { local_path: 'media/3.jpg' } },
    ]);
    assert.match(grid, /grid-rows-2/);
    assert.match(grid, /row-span-2/);

    const gridVideo = app.renderMediaGrid([
        { type: 'photo', download: { local_path: 'media/1.jpg' } },
        {
            type: 'video',
            duration_millis: 3000,
            download: { local_path: 'media/2.mp4' },
        },
    ]);
    assert.match(gridVideo, /media-video-player/);
    assert.match(gridVideo, /aria-label="Remaining video duration 0:03">0:03<\/span>/);
    for (const context of ['feed', 'detail', 'quote', 'quote-detail']) {
        const gallery = app.renderMediaGrid([
            { type: 'photo', width: 640, height: 360, download: { local_path: 'media/photo.jpg' } },
            { type: 'video', width: 360, height: 640, download: { local_path: 'media/video.mp4' } },
        ], context);
        const parsed = parseFakeHTML(gallery)[0];
        assert.ok(parsed.querySelector('img').classList.contains('object-cover'));
        assert.ok(parsed.querySelector('video').classList.contains('media-grid-video'));
        assert.ok(!parsed.querySelector('video').classList.contains('object-cover'));
    }

    const videoWithoutArchivedDuration = app.renderMediaGrid([
        { type: 'video', download: { local_path: 'media/no-duration.mp4' } },
    ]);
    assert.match(
        videoWithoutArchivedDuration,
        /class="media-video-duration" data-video-duration hidden aria-hidden="true"><\/span>/,
    );

    const css = fs.readFileSync(
        path.join(ROOT, 'tweetnook', 'web', 'static', 'css', 'styles.css'),
        'utf8',
    );
    assert.match(css, /\.media-video-duration,/);
    assert.match(css, /\.media-video-duration\s*\{[^}]*display: inline-flex;[^}]*align-items: center;/s);
    assert.match(css, /\.media-video-player\.is-native-ui-hidden \.media-video-duration \{/);
    assert.doesNotMatch(css, /\.media-video-player:(hover|focus-within) \.media-video-duration/);
    assert.match(css, /\.media-gif-indicator \{/);
    assert.match(css, /\.media-gif-indicator\.is-paused \.media-gif-play-icon/);
    assert.match(css, /\.quote-thumbnail-badge\s*\{[^}]*display: inline-flex;[^}]*align-items: center;/s);
    assert.match(css, /\.quote-thumbnail-duration\s*\{[^}]*background: rgba\(0, 0, 0, 0\.77\);/s);
    assert.match(css, /\.media-frame-video,[\s\S]*object-fit: contain;[\s\S]*background: #000;/);
    // Photo previews crop; playing videos contain their pixels in every gallery.
    assert.match(css, /\.quote-card:not\(\.quote-compact\) > \.quote-media > \.media-single img \{\s*object-fit: cover;/);
    assert.match(css, /\.media-grid-video\s*\{[^}]*object-fit: contain;[^}]*background: #000;/);
    assert.match(css, /@media \(max-width: 675px\) \{\s*\.media-frame-feed,\s*\.media-frame-detail,\s*\.media-frame-quote \{\s*--media-max-width: 100%;\s*--media-photo-cap-width: 100%;/);
    assert.match(css, /\.quote-card:not\(\.quote-compact\) > \.quote-media > \.media-single video \{\s*object-fit: contain;/);
    assert.doesNotMatch(css, /\.quote-card:not\(\.quote-compact\) > \.quote-media (img|video) \{/);
});

test('video duration uses loaded metadata and shows only while the control bar is hidden', () => {
    const context = browserContext();
    const timers = new Map();
    let nextTimerId = 0;
    context.setTimeout = (callback, delay) => {
        const id = ++nextTimerId;
        timers.set(id, {
            callback: () => {
                timers.delete(id);
                callback();
            },
            delay,
        });
        return id;
    };
    context.clearTimeout = id => timers.delete(id);
    loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const classes = new Set();
    const attributes = new Map([['aria-hidden', 'true']]);
    const indicator = {
        hidden: true,
        textContent: '',
        removeAttribute(name) {
            attributes.delete(name);
        },
        setAttribute(name, value) {
            attributes.set(name, value);
        },
    };
    const frame = {
        classList: {
            toggle(name, enabled) {
                if (enabled) classes.add(name);
                else classes.delete(name);
            },
        },
        querySelector: () => indicator,
    };
    const video = { duration: 125.9, currentTime: 30.2, parentElement: frame };

    context.window.tweetNookSyncVideoDuration(video);
    assert.equal(indicator.textContent, '1:35');
    assert.equal(indicator.hidden, false);
    assert.equal(attributes.get('aria-label'), 'Remaining video duration 1:35');
    assert.equal(attributes.has('aria-hidden'), false);

    video.currentTime = video.duration;
    context.window.tweetNookSyncVideoDuration(video);
    assert.equal(indicator.textContent, '0:00');
    assert.equal(attributes.get('aria-label'), 'Remaining video duration 0:00');

    const hidden = 'is-native-ui-hidden';
    const fireTimers = () => [...timers.values()].forEach(timer => timer.callback());

    // Native controls stay up while paused, so pointer activity alone never shows the badge.
    video.paused = true;
    context.window.tweetNookShowVideoUi(video);
    fireTimers();
    assert.ok(!classes.has(hidden));

    // Playback starts with the controls up, then they fade after the idle delay.
    video.paused = false;
    context.window.tweetNookShowVideoUi(video);
    assert.ok(!classes.has(hidden));
    assert.equal(timers.size, 1);
    assert.equal(timers.values().next().value.delay, 3000);
    fireTimers();
    assert.ok(classes.has(hidden));

    // Moving the pointer again brings the controls back and restarts the idle delay.
    context.window.tweetNookShowVideoUi(video);
    const canceledTimerId = timers.keys().next().value;
    assert.ok(!classes.has(hidden));
    context.window.tweetNookShowVideoUi(video);
    assert.ok(!timers.has(canceledTimerId));
    assert.equal(timers.size, 1);

    // Leaving the player hides the controls almost immediately.
    context.window.tweetNookLeaveVideoUi(video);
    assert.equal(timers.size, 1);
    assert.equal(timers.values().next().value.delay, 300);
    fireTimers();
    assert.ok(classes.has(hidden));

    // Pausing or ending brings the native controls back without pointer activity.
    video.paused = true;
    context.window.tweetNookSyncVideoUi(video);
    assert.ok(!classes.has(hidden));
    video.paused = false;
    video.ended = true;
    context.window.tweetNookSyncVideoUi(video);
    assert.ok(!classes.has(hidden));
});

test('custom players retain native fallback, seek, toggle sound, and isolate keyboard shortcuts', () => {
    const context = browserContext();
    context.setTimeout = () => 1;
    context.clearTimeout = () => {};
    const { tweetApp, tweetNookInitVideoPlayer, tweetNookSyncVideoDuration, isVideoPlayer } = loadScripts(
        context, ['themes.js', 'app.js'],
        '({tweetApp, tweetNookInitVideoPlayer, tweetNookSyncVideoDuration, isVideoPlayer})',
    );
    const app = immediateComponent(tweetApp());
    const player = parseFakeHTML(app.renderMediaGrid([{type: 'video', download: {local_path: 'media/v.mp4'}}]))[0].querySelector('video').parentElement;
    const video = player.querySelector('video');
    const seek = player.querySelector('[data-video-seek]');
    const volume = player.querySelector('[data-video-volume]');
    for (const element of [player, seek]) element.style = { setProperty() {} };
    Object.assign(video, { paused: true, ended: false, duration: NaN, currentTime: 0, volume: 1, muted: false,
        play() { this.paused = false; return Promise.resolve(); }, pause() { this.paused = true; },
    });
    assert.equal(player.querySelector('[data-video-action="pip"]'), null);
    assert.equal(player.querySelector('[data-video-action="settings"]'), null);
    assert.ok(!video.hasAttribute('controls'));
    assert.ok(video.hasAttribute('playsinline'));
    assert.ok(video.hasAttribute('data-video-custom'));
    tweetNookInitVideoPlayer(video);
    assert.ok(!video.hasAttribute('controls'));
    assert.ok(isVideoPlayer(video));
    assert.equal(seek.disabled, true);
    const listeners = player.listeners.length;
    tweetNookInitVideoPlayer(video);
    assert.equal(player.listeners.length, listeners);

    for (const modifiers of [{button: 2}, {button: 0, ctrlKey: true}]) {
        const click = dispatchFeed(player, 'click', video, modifiers);
        assert.equal(click.defaultPrevented, false, 'native context-menu clicks keep their default action');
        assert.equal(video.paused, true, 'native context-menu clicks do not toggle playback');
    }

    video.duration = 120;
    tweetNookSyncVideoDuration(video);
    assert.equal(seek.disabled, false);
    seek.value = 25;
    dispatchFeed(player, 'input', seek);
    assert.equal(video.currentTime, 30);
    assert.equal(player.querySelector('[data-video-time]').textContent, '0:30 / 2:00');
    assert.equal(seek.getAttribute('aria-valuetext'), '0:30 of 2:00');
    seek.value = 150;
    dispatchFeed(player, 'input', seek);
    assert.equal(video.currentTime, 120);
    dispatchFeed(player, 'keydown', player, {key: 'ArrowLeft'});
    assert.equal(video.currentTime, 115);
    const space = dispatchFeed(player, 'keydown', player, {key: ' '});
    assert.ok(!video.paused && space.defaultPrevented && space.propagationStopped);
    assert.equal(player.querySelector('[data-video-action="play"]').getAttribute('aria-label'), 'Pause video');
    const sliderKey = dispatchFeed(player, 'keydown', seek, {key: 'ArrowLeft'});
    assert.equal(video.currentTime, 110); // The slider seeks by seconds, not its 0.1% native step.
    assert.ok(sliderKey.propagationStopped && sliderKey.defaultPrevented); // Do not navigate lightbox slides.
    const find = dispatchFeed(player, 'keydown', player, {key: 'f', metaKey: true});
    assert.ok(!find.defaultPrevented && !find.propagationStopped); // Browser shortcuts such as Cmd+F still work.
    const mute = player.querySelector('[data-video-action="mute"]');
    assert.ok(dispatchFeed(player, 'click', mute).propagationStopped);
    assert.equal(video.muted, true);
    assert.equal(mute.getAttribute('aria-label'), 'Unmute');
    volume.value = 0.3;
    dispatchFeed(player, 'input', volume);
    assert.equal(video.volume, 0.3);
    assert.equal(video.muted, false);
    let fullscreen = 0;
    player.requestFullscreen = () => { fullscreen++; return Promise.resolve(); };
    dispatchFeed(player, 'keydown', player, {key: 'f'});
    assert.equal(fullscreen, 1);
    context.document.fullscreenElement = player;
    context.document.exitFullscreen = () => { fullscreen--; return Promise.resolve(); };
    dispatchFeed(player, 'click', player.querySelector('[data-video-action="fullscreen"]'));
    assert.equal(fullscreen, 0);
    // The seek thumb follows playback even after keyboard focus stays on it.
    context.document.activeElement = seek;
    video.currentTime = 60;
    tweetNookSyncVideoDuration(video);
    assert.equal(seek.value, 50);

    const nativeVideo = new FakeElement('video', [['controls', '']]);
    tweetNookInitVideoPlayer(nativeVideo);
    assert.ok(nativeVideo.hasAttribute('controls'));
});

test('mobile feed taps open a video viewer, sound stays inline, and desktop clicks pause', () => {
    const context = browserContext();
    const timers = [];
    context.setTimeout = callback => { timers.push(callback); return timers.length; };
    context.clearTimeout = () => {};
    context.window.matchMedia = () => ({matches: true});
    const opened = [];
    context.window.dispatchEvent = event => opened.push(event);
    const { tweetApp, tweetNookInitVideoPlayer } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp, tweetNookInitVideoPlayer})');
    const player = parseFakeHTML(tweetApp().renderMediaGrid([{type: 'video', download: {local_path: 'media/v.mp4'}}]))[0].querySelector('video').parentElement;
    const video = player.querySelector('video');
    for (const element of [player, player.querySelector('[data-video-seek]')]) element.style = {setProperty() {}};
    Object.assign(video, {src: '/media/v.mp4', paused: false, duration: 10, currentTime: 3, volume: 1, muted: true, pause() {this.paused = true;}});
    tweetNookInitVideoPlayer(video);
    assert.ok(player.classList.contains('is-native-ui-hidden'));
    dispatchFeed(player, 'pointerenter', player, {pointerType: 'touch'});
    dispatchFeed(player, 'focusin', {matches: () => false});
    assert.ok(player.classList.contains('is-native-ui-hidden'));
    dispatchFeed(player, 'click', video);
    assert.equal(opened.length, 1);
    assert.equal(opened[0].type, 'open-lightbox');
    assert.equal(opened[0].detail.originVideo, video);
    assert.equal(opened[0].detail.media[0].src, '/media/v.mp4');
    assert.equal(player.querySelector('[data-video-remaining-time]').textContent, '-0:07');
    const sound = player.querySelector('.video-idle-mute');
    assert.equal(sound.getAttribute('data-video-icon'), 'mobileMuted');
    assert.equal(sound.querySelectorAll('path')[1].getAttribute('d'), 'm17 10 4 4m0-4-4 4');
    dispatchFeed(player, 'click', sound);
    assert.equal(video.muted, false);
    assert.equal(opened.filter(event => event.type === 'open-lightbox').length, 1);
    assert.equal(opened.at(-1).type, 'video-audio-intent');
    assert.equal(opened.at(-1).detail.muted, false);
    // Taps inside the viewer still reveal/dismiss chrome without pausing.
    timers.at(-1)();
    video.closest = selector => selector === '.lightbox-stage' ? {} : null;
    dispatchFeed(player, 'click', video);
    dispatchFeed(player, 'pointerleave', player, {pointerType: 'touch'});
    assert.ok(!player.classList.contains('is-native-ui-hidden'));
    assert.equal(video.paused, false);
    dispatchFeed(player, 'click', video);
    assert.ok(player.classList.contains('is-native-ui-hidden'));
    assert.equal(video.paused, false);
    context.window.matchMedia = () => ({matches: false});
    dispatchFeed(player, 'click', video);
    assert.equal(video.paused, true);
});

test('mobile viewer transfers playback position and sound back to the originating video', () => {
    for (const readyState of [0, 1]) {
        const context = browserContext();
        const {tweetApp} = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
        const app = immediateComponent(tweetApp());
        const origin = {currentTime: 3, muted: true, volume: .6, loop: true, isConnected: true,
            pause() {this.paused = true;}, play() {this.paused = false; return Promise.resolve();}};
        const active = new FakeElement('video', [['autoplay', ''], ['data-lightbox-index', '0']]);
        Object.assign(active, {readyState, paused: true, ended: false, currentTime: 0, muted: false, volume: 1,
            closest: () => ({classList: {contains: () => true}}),
            pause() {this.paused = true;}, play() {this.paused = false; return Promise.resolve();}});
        app.$refs.lightboxStage = {querySelectorAll: () => [active], querySelector: () => active};
        app.openLightbox([{type: 'video', src: '/media/v.mp4', isGif: false}], 0, origin);
        assert.equal(origin.paused, true);
        assert.equal(app.lightboxMedia[0].loop, true);
        if (!readyState) dispatchFeed(active, 'loadedmetadata', active);
        assert.equal(active.currentTime, 3);
        assert.equal(active.muted, true);
        assert.equal(active.volume, .6);
        active.currentTime = 7;
        active.muted = false;
        app.syncLightboxPlayback();
        assert.equal(active.currentTime, 7); // Repeated syncing never resets progress.
        if (!readyState) active.pause();
        app.closeLightbox();
        assert.equal(app.lightboxOpen, false);
        assert.equal(active.paused, true);
        assert.equal(origin.currentTime, 7);
        assert.equal(origin.muted, false);
        assert.equal(origin.paused, !readyState);
    }
});

test('scrubbing holds playback, tracks preview time, resumes on release and restores on cancellation', () => {
    const context = browserContext();
    const {tweetApp, tweetNookInitVideoPlayer} = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp, tweetNookInitVideoPlayer})');
    const player = parseFakeHTML(tweetApp().renderMediaGrid([{type: 'video', download: {local_path: 'media/v.mp4'}}]))[0].querySelector('video').parentElement;
    const video = player.querySelector('video');
    const seek = player.querySelector('[data-video-seek]');
    for (const element of [player, seek]) element.style = {setProperty() {}};
    let plays = 0;
    let root = player;
    while (root.parentElement) root = root.parentElement;
    root.connected = true;
    Object.assign(video, {paused: false, duration: 10, currentTime: 3, volume: 1, muted: true,
        pause() {this.paused = true;}, play() {plays++; this.paused = false; return Promise.resolve();}});
    tweetNookInitVideoPlayer(video);
    dispatchFeed(player, 'pointerdown', seek, {button: 0});
    assert.equal(video.paused, true);
    assert.ok(player.classList.contains('is-scrubbing'));
    seek.value = 75;
    dispatchFeed(player, 'input', seek);
    assert.equal(video.currentTime, 7.5);
    assert.equal(player.querySelector('[data-video-scrub-time]').textContent, '0:07 / 0:10');
    context.__state.events.get('document:pointerup')();
    assert.equal(video.paused, false);
    assert.equal(plays, 1);
    assert.ok(!player.classList.contains('is-scrubbing'));
    assert.ok(!context.__state.events.has('document:pointercancel'));
    dispatchFeed(player, 'pointerdown', seek, {button: 0});
    seek.value = 20;
    dispatchFeed(player, 'input', seek);
    context.__state.events.get('document:pointercancel')();
    assert.equal(video.currentTime, 7.5);
    assert.equal(video.paused, false);
    video.pause();
    dispatchFeed(player, 'pointerdown', seek, {button: 0});
    context.__state.events.get('document:pointerup')();
    assert.equal(video.paused, true); // A paused video remains paused after seeking.
    assert.equal(plays, 2);
});

test('mobile video bounds wait for a playback frame, return to the feed, and respect reduced motion', async () => {
    for (const reduced of [false, true]) {
        const context = browserContext();
        context.window.matchMedia = query => ({matches: query.includes('prefers-reduced-motion') ? reduced : true});
        context.getComputedStyle = () => ({borderRadius: '16px', objectFit: 'contain'});
        const {tweetApp} = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
        const app = immediateComponent(tweetApp());
        const origin = {currentTime: 3, muted: true, volume: .6, loop: true, isConnected: true,
            parentElement: {}, getBoundingClientRect: () => ({left: 60, top: 200, width: 300, height: 533}),
            pause() {this.paused = true;}, play() {this.paused = false; return Promise.resolve();}};
        const values = new Map();
        const surfaceValues = new Map();
        const motions = [];
        let backdropCancels = 0;
        const viewer = {};
        app.$refs.lightboxShade = {style: {}, animate: () => ({cancel() {backdropCancels++;}})};
        const active = new FakeElement('video', [['autoplay', ''], ['data-lightbox-index', '0']]);
        Object.assign(active, {readyState: 0, paused: true, ended: false, currentTime: 0, muted: false, volume: 1,
            videoWidth: 720, videoHeight: 1280,
            style: {getPropertyValue: name => values.get(name) || '', setProperty: (name, value) => values.set(name, value), removeProperty: name => values.delete(name)},
            getBoundingClientRect: () => ({left: 0, top: 0, width: 390, height: 772}),
            parentElement: {parentElement: {getBoundingClientRect: () => ({left: 0, top: 0})},
                style: {getPropertyValue: name => surfaceValues.get(name) || '', setProperty: (name, value) => surfaceValues.set(name, value), removeProperty: name => surfaceValues.delete(name)}},
            closest: selector => selector === '.lightbox-viewer' ? viewer : selector === '.lightbox-slide' ? {classList: {contains: () => true}} : null,
            pause() {this.paused = true;}, play() {this.paused = false; return Promise.resolve();},
            animate(frames, options) {
                let complete, reject;
                const finished = new Promise((resolve, fail) => {complete = resolve; reject = fail;});
                const motion = {frames, options, complete, finished, cancel() {reject(new Error('cancelled'));}};
                motions.push(motion);
                return motion;
            }});
        active.parentElement.animate = active.animate;
        app.$refs.lightboxStage = {querySelectorAll: () => [active], querySelector: () => active};
        app.openLightbox([{type: 'video', src: '/media/v.mp4', isGif: false}], 0, origin);
        assert.equal(motions.length, 0); // Do not grow a black unloaded frame.
        active.readyState = 2;
        dispatchFeed(active, 'loadedmetadata', active);
        dispatchFeed(active, 'loadeddata', active);
        if (!reduced) {
            assert.equal(app.lightboxVideoMotionPhase, 'opening');
            assert.match(motions[0].frames[0].transform, /translate3d\(60\.09375px,/); // The contained feed picture, not its frame.
            assert.ok(parseFloat(motions[0].frames[0].borderRadius) > 0);
            assert.equal(Object.keys(motions[0].frames[1]).sort().join(','), 'borderRadius,transform');
            motions[0].complete();
            await Promise.resolve(); await Promise.resolve();
            assert.equal(app.lightboxVideoMotionPhase, '');
            assert.equal(values.size, 0); // Temporary sizing/crop styles are restored.
            assert.equal(surfaceValues.size, 0);
        }
        active.currentTime = 7;
        app.startLightboxGesture({target: active, touches: [{clientX: 100, clientY: 200}], timeStamp: 100});
        assert.ok(app.lightboxGestureStart); // The enhanced video surface accepts dismissal drags.
        app.endLightboxGesture({changedTouches: [{clientX: 100, clientY: 300}], timeStamp: 200});
        if (!reduced) {
            assert.equal(app.lightboxOpen, true);
            assert.equal(app.lightboxVideoMotionPhase, 'closing');
            app.closeLightbox(); // Repeated close requests do not replace the return animation.
            assert.equal(motions.length, 2);
            assert.match(motions[1].frames[1].transform, /translate3d\(60\.09375px,/);
            assert.equal(origin.paused, true);
            motions[1].complete();
            await Promise.resolve(); await Promise.resolve();
            assert.equal(backdropCancels, 2);
        }
        assert.equal(app.lightboxOpen, false);
        assert.equal(origin.currentTime, 7);
        assert.equal(origin.paused, false);
        assert.equal(values.size, 0);
        assert.equal(surfaceValues.size, 0);
        assert.equal(motions.length, reduced ? 0 : 2);
    }
});

test('video motion holds its final surface for handoff and preserves cropped picture geometry', async () => {
    for (const mode of ['quote', 'grid', 'fill', 'occluded']) {
        const fill = mode === 'fill', occluded = mode === 'occluded', quote = mode === 'quote', grid = mode === 'grid';
        const context = browserContext();
        context.window.innerWidth = 390;
        context.window.innerHeight = 844;
        if (occluded) context.document.querySelectorAll = selector => selector === '[data-video-occluder]'
            ? [{getBoundingClientRect: () => ({left: 0, top: 0, width: 390, height: 90})}] : [];
        const surfaceValues = new Map(), videoValues = new Map();
        const style = values => ({getPropertyValue: name => values.get(name) || '', setProperty: (name, value) => values.set(name, value), removeProperty: name => values.delete(name)});
        let computedRadius = '0px', computedCrop = 'none';
        const surface = {style: style(surfaceValues), parentElement: {getBoundingClientRect: () => ({left: 0, top: 0})},
            getBoundingClientRect: () => ({left: 20, top: 80, width: 195, height: 347})};
        const video = {parentElement: surface, style: style(videoValues), videoWidth: 720, videoHeight: 1280,
            getBoundingClientRect: () => ({left: 0, top: 0, width: 390, height: 772}), closest: () => null,
            animate() {throw new Error('Video animation would disturb the decoder surface.');}};
        context.getComputedStyle = element => element === video
            ? {objectFit: videoValues.get('object-fit') || (fill ? 'cover' : 'contain')}
            : element === surface ? {borderRadius: computedRadius, clipPath: computedCrop} : {borderRadius: '16px'};
        let complete, frames;
        surface.animate = keyframes => {
            frames = keyframes;
            return {finished: new Promise(resolve => {complete = resolve;}), cancel() {}};
        };
        const {animateMobileMediaBounds} = loadScripts(context, ['app.js'], '({animateMobileMediaBounds})');
        const originValues = new Map();
        const origin = {isConnected: true, style: style(originValues),
            // The inline player's placeholder records how the feed drew its picture.
            getAttribute: name => ({'data-feed-fit': grid ? 'cover' : 'contain', 'data-feed-background': 'rgb(0, 0, 0)'})[name],
            getBoundingClientRect: () => ({left: 60, top: occluded ? -120 : 200, width: 300, height: fill || occluded ? 533 : 168.75})};
        const motion = animateMobileMediaBounds(video, origin, false, 280,
            {left: 0, top: fill ? 0 : (772 - 390 * 1280 / 720) / 2, width: 390, height: fill ? 772 : 390 * 1280 / 720});
        assert.ok(motion);
        assert.equal(videoValues.get('width'), '100%');
        assert.equal(videoValues.get('height'), '100%');
        assert.equal(videoValues.get('object-fit'), 'contain');
        assert.ok(Math.abs(parseFloat(surfaceValues.get('width')) / parseFloat(surfaceValues.get('height')) - 720 / 1280) < .00001);
        if (!quote) assert.ok(frames.some(frame => frame.clipPath)); // A grid crop or fill-screen crop belongs to the surface.
        if (fill) assert.match(frames[0].clipPath, /inset\(0px 22\.125px/); // Keep the current fill crop at dismissal start.
        else if (grid) assert.match(frames[1].clipPath, /inset\(236\.9/); // Return to the wide cropped grid cell without stretching.
        else if (quote) {
            // A quoted portrait video is pillarboxed in its 16:9 frame: land on that picture, uncropped.
            assert.ok(frames.every(frame => !frame.clipPath));
            const [, x, , scale] = frames[1].transform.match(/translate3d\(([-\d.]+)px, ([-\d.]+)px, 0\) scale\(([-\d.]+)\)/).map(Number);
            const width = 390 * scale;
            assert.ok(Math.abs(width - 168.75 * 720 / 1280) < .001);
            assert.ok(Math.abs(x - (60 + (300 - width) / 2)) < .001);
            assert.equal(originValues.get('background-color'), 'rgb(0, 0, 0)'); // Its bars stay black during flight.
        }
        else {
            const transform = frames[1].transform.match(/translate3d\(([-\d.]+)px, ([-\d.]+)px, 0\) scale\(([-\d.]+)\)/);
            const clip = frames[1].clipPath.match(/inset\(([\d.]+)px ([\d.]+)px ([\d.]+)px ([\d.]+)px/);
            const visibleTop = parseFloat(surfaceValues.get('top')) + Number(transform[2]) + Number(clip[1]) * Number(transform[3]);
            assert.ok(Math.abs(visibleTop - 90) < .001); // Last frame matches the sticky header's occlusion, before DOM handoff.
            assert.match(frames[1].clipPath, /round 0px 0px /); // The header cuts a straight edge, not new rounded corners.
            const visibleBottom = parseFloat(surfaceValues.get('top')) + Number(transform[2])
                + (parseFloat(surfaceValues.get('height')) - Number(clip[3])) * Number(transform[3]);
            assert.ok(Math.abs(visibleBottom - 413) < .001); // The header clip must not recenter the picture.
        }
        computedRadius = '24px'; computedCrop = 'inset(20px 10px round 24px)';
        const interrupted = motion.bounds();
        const scale = 195 / parseFloat(surfaceValues.get('width'));
        assert.equal(interrupted.left, 20 + 10 * scale);
        assert.equal(interrupted.top, 80 + 20 * scale);
        assert.equal(interrupted.radius, 24 * scale); // A mid-flight reversal preserves its visible corners.
        complete();
        assert.equal(await motion.finished, true);
        assert.ok(surfaceValues.size > 0); // Don't reveal fullscreen before the caller restores the feed.
        motion.cancel();
        assert.equal(surfaceValues.size, 0);
        assert.equal(videoValues.size, 0);
        assert.equal(originValues.size, 0); // The placeholder's own look returns at handoff.
    }
});

test('dismissal fades the page shade and lifts covering headers above the returning picture', async () => {
    for (const raisable of [true, false]) {
        const context = browserContext();
        context.window.visualViewport = {offsetLeft: 0, offsetTop: 0, width: 390, height: 844};
        const style = entries => ({getPropertyValue: name => entries.get(name) || '',
            setProperty: (name, value) => entries.set(name, value), removeProperty: name => entries.delete(name)});
        const viewer = {};
        const headerValues = new Map([['z-index', '20']]);
        const fades = [];
        const animate = target => (frames, options) => {
            const fade = {target, frames, options, cancelled: false, cancel() {this.cancelled = true;}};
            fades.push(fade); return fade;
        };
        const title = {};
        const header = {style: style(headerValues), contains: node => node === title, animate: animate('header'),
            getBoundingClientRect: () => ({left: 0, top: 0, width: 390, height: 90})};
        let hitPoint;
        context.document.querySelectorAll = selector => selector === '[data-video-occluder]' ? [header] : [];
        context.document.elementFromPoint = (x, y) => {
            hitPoint = [x, y];
            return raisable && headerValues.get('z-index') === '101' ? title : viewer;
        };
        context.getComputedStyle = element => element === viewer ? {zIndex: '100'} : {objectFit: 'contain', borderRadius: '16px'};
        const shade = {animate: animate('shade')};
        let frames, complete;
        const surface = {style: style(new Map()), parentElement: {getBoundingClientRect: () => ({left: 0, top: 0})},
            animate: keyframes => {frames = keyframes; return {finished: new Promise(resolve => {complete = resolve;}), cancel() {}};}};
        const video = {parentElement: surface, style: style(new Map()), videoWidth: 720, videoHeight: 1280,
            getBoundingClientRect: () => ({left: 0, top: 0, width: 390, height: 772}), closest: () => viewer};
        const origin = {isConnected: true, getBoundingClientRect: () => ({left: 60, top: 40, width: 300, height: 300 * 1280 / 720})};
        const {animateMobileMediaBounds} = loadScripts(context, ['app.js'], '({animateMobileMediaBounds})');
        const motion = animateMobileMediaBounds(video, origin, false, 280, null, shade);
        assert.deepEqual(hitPoint, [210, 65]); // Probe where the header covers the returning picture.
        const shadeFade = fades.find(fade => fade.target === 'shade');
        assert.equal(JSON.stringify(shadeFade.frames), '[{"opacity":1},{"opacity":0}]'); // Compositor opacity, not a repainted color.
        assert.equal(shadeFade.options.duration, 120); // The page returns before the picture lands.
        assert.equal(shadeFade.options.easing, 'ease-out');
        const headerFade = fades.find(fade => fade.target === 'header');
        if (raisable) {
            assert.equal(headerValues.get('z-index'), '101'); // The picture travels beneath the header, as it will rest.
            assert.equal(frames[1].clipPath, undefined); // No header crop that would reappear behind its glass.
            assert.equal(JSON.stringify(headerFade.frames), '[{"opacity":0},{"opacity":1}]'); // It fades in with the rest of the page.
            assert.equal(headerFade.options, shadeFade.options);
        } else {
            assert.equal(headerValues.get('z-index'), '20'); // A header in another stacking context isn't moved.
            assert.equal(headerFade, undefined);
            const top = frames[1].clipPath.match(/^inset\(([\d.]+)px/)[1];
            assert.ok(Math.abs(Number(top) * 300 / 390 - 50) < .001); // It keeps the crop at its 90px edge instead.
        }
        complete();
        assert.equal(await motion.finished, true);
        motion.cancel();
        assert.deepEqual([...headerValues], [['z-index', '20']]);
        assert.ok(fades.every(fade => fade.cancelled));
    }
});

test('phone media viewer stays outside Safari toolbar sampling and backs itself with page content', () => {
    const css = fs.readFileSync(path.join(ROOT, 'tweetnook/web/static/css/styles.css'), 'utf8');
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook/web/index.html'), 'utf8');
    const mobile = css.slice(css.indexOf('/* The phone feed uses a sound badge'));
    const viewer = mobile.match(/\n    \.lightbox-viewer \{([^}]*)\}/)[1];
    const bleed = Number(viewer.match(/--lightbox-bleed:\s*(\d+)px/)[1]);
    assert.ok(2 * bleed > .05 * 1366); // Over 105% of even a tall viewport, on every side.
    assert.match(viewer, /inset:\s*calc\(-1 \* var\(--lightbox-bleed\)\)/);
    assert.match(viewer, /background-color:\s*transparent !important/); // The fixed layer adds no sampled color.
    assert.match(mobile, /\.lightbox-viewer > \.lightbox-stage,\s*\.lightbox-viewer > \.lightbox-chrome \{ inset: var\(--lightbox-bleed\); \}/);
    assert.match(mobile, /\.lightbox-viewer \.lightbox-close-button \{\s*top: calc\(var\(--lightbox-bleed\) \+/);
    assert.match(mobile, /\.lightbox-viewer:not\(\.is-video-closed\) \+ \.lightbox-shade \{ display: block; \}/);
    // Photos and GIFs move or fade themselves on a phone, never with Alpine's overlay fade.
    assert.match(mobile, /\.lightbox-viewer\.lightbox-overlay-enter,\s*\.lightbox-viewer\.lightbox-overlay-leave \{ transition: none; \}/);
    assert.match(mobile, /\.is-video-preparing \.lightbox-slide\.is-active \.lightbox-media \{ visibility: hidden; \}/);
    assert.match(css, /\.lightbox-shade \{[^}]*display: none;[^}]*position: absolute;[^}]*z-index: 99;[^}]*pointer-events: none;/s);
    assert.match(html, /<\/div>\s*<div x-ref="lightboxShade" class="lightbox-shade" :style="lightboxShadeStyle\(\)" aria-hidden="true"><\/div>\s*<!-- Tag Modal -->/);
    assert.match(css, /\.lightbox-shade \{[^}]*background: #000;/s);
    assert.doesNotMatch(css, /is-video-closing/);
});

test('video motion crops its feed frame at the visible viewport in both directions', async () => {
    for (const opening of [true, false]) {
        const context = browserContext();
        context.window.visualViewport = {offsetLeft: 0, offsetTop: 42, width: 390, height: 730};
        context.getComputedStyle = () => ({objectFit: 'contain', borderRadius: '16px'});
        const style = () => ({getPropertyValue: () => '', setProperty() {}, removeProperty() {}});
        let frames, complete;
        const surface = {style: style(), parentElement: {getBoundingClientRect: () => ({left: 0, top: 42})},
            animate: keyframes => {frames = keyframes; return {finished: new Promise(resolve => {complete = resolve;}), cancel() {}};}};
        const video = {parentElement: surface, style: style(), videoWidth: 720, videoHeight: 1280,
            getBoundingClientRect: () => ({left: 0, top: 42, width: 390, height: 650}), closest: () => null};
        const origin = {isConnected: true, getBoundingClientRect: () => ({left: 60, top: 500, width: 300, height: 300 * 1280 / 720})};
        const {animateMobileMediaBounds} = loadScripts(context, ['app.js'], '({animateMobileMediaBounds})');
        const motion = animateMobileMediaBounds(video, origin, opening, 280);
        const insets = frames[opening ? 0 : 1].clipPath.match(/^inset\((.*?) round/)[1].split(' ').map(parseFloat);
        assert.ok(insets[2] > 0); // Safari never draws a fixed layer beneath its toolbar, so cut a straight edge there.
        assert.ok(Math.abs(insets[0]) < .001 && Math.abs(insets[1]) < .001 && Math.abs(insets[3]) < .001);
        complete();
        assert.equal(await motion.finished, true);
        motion.cancel();
    }
});

function mediaMotionFixture(context) {
    const styleOf = values => ({getPropertyValue: name => values.get(name) || '',
        setProperty: (name, value) => values.set(name, value), removeProperty: name => values.delete(name)});
    const motions = [];
    const record = target => (frames, options) => {
        let complete;
        const motion = {target, frames, options, cancelled: false, complete: () => complete(),
            finished: new Promise(resolve => {complete = resolve;}), cancel() {this.cancelled = true;}};
        motions.push(motion);
        return motion;
    };
    const viewer = new FakeElement('div', [['class', 'lightbox-viewer']]);
    viewer.connected = true;
    viewer.style = {};
    viewer.animate = record('viewer');
    const stage = new FakeElement('div', [['class', 'lightbox-stage']]);
    viewer.appendChild(stage);
    const shade = {style: {}, animate: record('shade')};
    return {styleOf, motions, record, viewer, stage, shade};
}

test('in-flight media joins the page so Safari draws it beneath its toolbars', async () => {
    for (const opening of [true, false]) {
        const context = browserContext();
        context.window.scrollX = 0;
        context.window.scrollY = 1200;
        context.window.visualViewport = {offsetLeft: 0, offsetTop: 0, width: 390, height: 714};
        const styleOf = values => ({getPropertyValue: name => values.get(name) || '',
            setProperty: (name, value) => values.set(name, value), removeProperty: name => values.delete(name)});
        const viewerValues = new Map(), stageValues = new Map();
        const stage = {style: styleOf(stageValues), getBoundingClientRect: () => ({left: 0, top: 0, width: 390, height: 714})};
        const viewer = {style: styleOf(viewerValues), querySelector: selector => selector === '.lightbox-stage' ? stage : null};
        context.getComputedStyle = element => element === viewer ? {zIndex: '100'} : {objectFit: 'contain', borderRadius: '0px'};
        let frames, complete;
        const surface = {style: styleOf(new Map()), parentElement: {getBoundingClientRect: () => ({left: 0, top: 0})},
            animate: keyframes => {frames = keyframes; return {finished: new Promise(resolve => {complete = resolve;}), cancel() {}};}};
        const video = {parentElement: surface, style: styleOf(new Map()), videoWidth: 720, videoHeight: 1280,
            getBoundingClientRect: () => ({left: 0, top: 0, width: 390, height: 650}), closest: () => viewer};
        // The feed video's lower part sits beneath Safari's toolbar, past the 714px viewport.
        const origin = {isConnected: true, getBoundingClientRect: () => ({left: 60, top: 500, width: 300, height: 300 * 1280 / 720})};
        const {animateMobileMediaBounds} = loadScripts(context, ['app.js'], '({animateMobileMediaBounds})');
        const motion = animateMobileMediaBounds(video, origin, opening, 280);
        assert.equal(viewerValues.get('position'), 'absolute'); // In the page at the same screen position.
        assert.equal(viewerValues.get('top'), '1200px');
        assert.equal(viewerValues.get('left'), '0px');
        assert.equal(viewerValues.get('width'), '390px');
        assert.equal(viewerValues.get('height'), '714px');
        assert.equal(viewerValues.get('--lightbox-bleed'), '0px');
        assert.equal(stageValues.get('overflow'), 'visible');
        assert.doesNotMatch(stageValues.get('contain'), /paint|strict/); // Paint containment would clip it again.
        assert.equal(frames[opening ? 0 : 1].clipPath, undefined); // No crop at the viewport's bottom edge.
        complete();
        assert.equal(await motion.finished, true);
        motion.cancel();
        assert.equal(viewerValues.size, 0); // Back to the screen-fixed viewer once the motion ends.
        assert.equal(stageValues.size, 0);
    }
});

test('phone photos expand from their tile and return to the one on screen, or fade when it is gone', async () => {
    const context = browserContext();
    context.window.matchMedia = query => ({matches: !query.includes('prefers-reduced-motion')});
    context.window.innerHeight = 844;
    const opened = [];
    context.window.dispatchEvent = event => opened.push(event.detail);
    const {tweetApp, tweetNookOpenLightbox} = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp, tweetNookOpenLightbox})');
    const app = immediateComponent(tweetApp());
    const {styleOf, motions, record, viewer, stage, shade} = mediaMotionFixture(context);
    const photo = name => ({type: 'photo', download: {local_path: `media/${name}.jpg`}});
    const feed = parseFakeHTML(app.renderMediaGrid([photo('a'), photo('b')]))[0];
    feed.connected = true;
    feed.rect = {left: 50, top: 300, width: 302, height: 170};
    const tiles = feed.querySelectorAll('img');
    // A video poster in an earlier gallery cell is decoration, not another slide.
    tiles[0].parentElement.appendChild(new FakeElement('img', [['data-video-poster', '']]));
    tiles[0].rect = {left: 51, top: 301, width: 149, height: 168};
    tiles[1].rect = {left: 202, top: 301, width: 149, height: 168};
    const tileValues = tiles.map(tile => { const values = new Map(); tile.style = styleOf(values); return values; });
    const images = [0, 1].map(() => {
        const slide = new FakeElement('div', [['class', 'lightbox-slide']]);
        slide.style = {};
        const image = new FakeElement('img', [['class', 'lightbox-media']]);
        Object.assign(image, {complete: true, naturalWidth: 1200, naturalHeight: 1600, style: styleOf(new Map()),
            rect: {left: 0, top: 162, width: 390, height: 520}, animate: record('image')});
        slide.appendChild(image);
        stage.appendChild(slide);
        return image;
    });
    const show = index => stage.children.forEach((slide, slideIndex) => slide.classList.toggle('is-active', slideIndex === index));
    context.getComputedStyle = element => element === feed ? {borderTopLeftRadius: '16px', borderTopWidth: '1px'}
        : images.includes(element) ? {objectFit: 'contain', borderRadius: '0px'}
        : element === viewer ? {zIndex: '100'}
        : element === shade ? {opacity: '0.95'} : {objectFit: 'cover'};
    Object.assign(app.$refs, {lightboxViewer: viewer, lightboxStage: stage, lightboxShade: shade});

    tweetNookOpenLightbox(tiles[1], 1);
    assert.equal(opened[0].origin, tiles[1]); // The tapped tile is where the photo expands from.
    show(1);
    app.openLightbox(opened[0].media, opened[0].index, null, opened[0].origin);
    const expand = motions[0];
    assert.equal(expand.target, 'image'); // The photo moves itself; it has no player wrapper.
    const darken = motions.find(motion => motion.target === 'shade');
    // Ends at the photo backdrop's resting opacity, so nothing snaps when the motion is released.
    assert.equal(JSON.stringify(darken.frames), '[{"opacity":0},{"opacity":0.95}]');
    assert.equal(app.lightboxRestingOpacity(), 0.95);
    assert.equal(images[1].style.getPropertyValue('transform-origin'), '0 0');
    assert.equal(images[1].style.getPropertyValue('position'), ''); // Its layout box is already the fitted picture.
    assert.match(expand.frames[0].clipPath, /^inset\([\d.]+px 0px [\d.]+px 0px round 0px [1-9][\d.]*px [1-9][\d.]*px 0px\)$/);
    assert.equal(app.lightboxVideoMotionPhase, 'opening');
    assert.equal(tileValues[1].get('visibility'), 'hidden'); // One copy of the photo on screen at a time.
    expand.complete(); await Promise.resolve(); await Promise.resolve();
    assert.equal(app.lightboxVideoMotionPhase, '');
    assert.equal(tileValues[1].size, 0);

    app.lightboxIndex = 0;
    show(0);
    app.closeLightbox();
    const land = motions.find(motion => motion.target === 'image' && motion !== expand);
    assert.ok(land, 'the photo now on screen returns');
    assert.equal(tileValues[0].get('visibility'), 'hidden'); // Its own tile, not the one it opened from.
    assert.equal(tileValues[1].size, 0);
    // The left tile meets the grid's rounded corners on its left only.
    assert.match(land.frames[1].clipPath, /round [1-9][\d.]*px 0px 0px [1-9][\d.]*px\)$/);
    const backdrop = motions.find(motion => motion.target === 'shade' && motion.options.duration === 120);
    assert.equal(JSON.stringify(backdrop.frames), '[{"opacity":0.95},{"opacity":0}]');
    land.complete(); await Promise.resolve(); await Promise.resolve();
    assert.equal(app.lightboxOpen, false);
    assert.equal(tileValues[0].size, 0); // Shown again in the same frame the viewer hides.
    assert.equal(viewer.style.display, 'none'); // Hidden at handoff, with no Alpine fade over the feed.
    tweetNookOpenLightbox(tiles[0], 0);
    assert.equal(opened.length, 1); // Safari's compatibility click after dismissal doesn't reopen it.

    show(1);
    app.openLightbox(opened[0].media, 1, null, tiles[1]);
    motions.at(-1).complete(); await Promise.resolve(); await Promise.resolve();
    tiles.forEach(tile => { tile.rect = {...tile.rect, top: -400}; });
    const before = motions.length;
    app.closeLightbox();
    const fades = motions.slice(before);
    assert.deepEqual(fades.map(motion => motion.target).sort(), ['shade', 'viewer']); // Nothing to land on: fade.
    assert.equal(JSON.stringify(fades[0].frames), '[{"opacity":1},{"opacity":0}]');
    assert.equal(app.lightboxVideoMotionPhase, 'closing');
    fades.forEach(fade => fade.complete());
    await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    assert.equal(app.lightboxOpen, false);
});

test('phone GIF taps open the viewer, which continues from the feed frame', async () => {
    const context = browserContext();
    let phone = false;
    context.window.matchMedia = query => ({matches: phone && !query.includes('prefers-reduced-motion')});
    const opened = [];
    context.window.dispatchEvent = event => opened.push(event.detail);
    const {tweetApp} = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    const {styleOf, motions, record, viewer, stage, shade} = mediaMotionFixture(context);
    const feed = parseFakeHTML(app.renderMediaGrid([{type: 'animated_gif', download: {local_path: 'media/g.mp4'}}]))[0];
    feed.connected = true;
    const gif = feed.querySelector('video');
    assert.match(gif.getAttribute('onclick'), /window\.tweetNookOpenGif\(event, this, 0\)/);
    const click = {stopPropagation() {this.stopped = true;}};
    context.window.tweetNookOpenGif(click, gif, 0);
    assert.equal(opened.length, 0); // Desktop GIFs keep playing in place.
    assert.ok(!click.stopped);
    phone = true;
    context.window.tweetNookOpenGif(click, gif, 0);
    assert.ok(click.stopped); // The card behind it doesn't open the thread.
    assert.equal(opened[0].origin, gif);
    assert.equal(opened[0].media[0].isGif, true);

    Object.assign(gif, {currentTime: 5.5, rect: {left: 60, top: 200, width: 300, height: 270}});
    const slide = new FakeElement('div', [['class', 'lightbox-slide is-active']]);
    slide.style = {};
    const loop = new FakeElement('video', [['class', 'lightbox-media'], ['autoplay', ''], ['loop', '']]);
    let time = 0;
    Object.defineProperty(loop, 'currentTime', {get: () => time, set: value => { time = value; loop.seeking = true; }});
    Object.assign(loop, {readyState: 1, duration: 4, seeking: false, paused: true, style: styleOf(new Map()),
        rect: {left: 0, top: 233, width: 390, height: 351}, animate: record('gif'),
        play() { this.paused = false; return Promise.resolve(); }, pause() { this.paused = true; }});
    slide.appendChild(loop);
    stage.appendChild(slide);
    context.getComputedStyle = element => element === loop ? {objectFit: 'contain'} : element === viewer ? {zIndex: '100'} : {borderRadius: '16px'};
    Object.assign(app.$refs, {lightboxViewer: viewer, lightboxStage: stage, lightboxShade: shade});
    app.openLightbox(opened[0].media, 0, null, gif);
    assert.equal(loop.currentTime, 1.5); // 5.5s into a 4s loop.
    assert.equal(motions.length, 0); // Wait for the seeked frame rather than expanding frame 0.
    Object.assign(loop, {readyState: 2, seeking: false});
    dispatchFeed(loop, 'seeked', loop);
    assert.equal(motions[0].target, 'gif');
    assert.match(motions[0].frames[0].transform, /translate3d\(60px, -33px, 0\) scale\(0\.769/); // From the feed GIF's box.
    assert.equal(loop.paused, false);
});

test('Home Screen apps are told apart from Safari tabs for bottom spacing', () => {
    for (const standalone of [true, false]) {
        const context = browserContext();
        context.navigator = {standalone};
        loadScripts(context, ['app.js'], 'null');
        assert.equal(context.__state.rootClasses.has('ios-home-screen'), standalone);
    }
    const css = fs.readFileSync(path.join(ROOT, 'tweetnook/web/static/css/styles.css'), 'utf8');
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook/web/index.html'), 'utf8');
    // Safari tabs sit on the toolbar's reserved strip; Home Screen apps reach the screen edge.
    assert.match(css, /\.lightbox-video-player \{[^}]*--video-controls-bottom: 0px;/s);
    assert.match(css, /\.ios-home-screen \.lightbox-video-player \{ --video-controls-bottom: max\(8px, env\(safe-area-inset-bottom\)\); \}/);
    assert.match(css, /:root:not\(\.ios-home-screen\) \.scroll-to-top \{ bottom: 12px; \}/);
    assert.match(html, /class="scroll-to-top"/);
    assert.match(css, /\.scroll-to-top \{[^}]*right: max\(24px, env\(safe-area-inset-right\)\);/s);
    assert.match(css, /\.scroll-to-top \{[^}]*bottom: max\(24px, env\(safe-area-inset-bottom\)\);/s);
});

test('phone media viewers keep the page paintable under Safari chrome while blocking one-finger page pans', () => {
    const context = browserContext();
    let phone = true;
    context.window.matchMedia = () => ({matches: phone});
    context.window.scrollY = 500;
    const {tweetApp} = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    const watchers = {};
    app.$watch = (name, callback) => {watchers[name] = callback;};
    app.fetchSetup = async () => {};
    app.fetchNotices = async () => {};
    app.fetchActivityStatus = async () => {};
    app.initApp();
    app.lightboxMedia = [{type: 'video', isGif: false}];
    app.lightboxOpen = true;
    context.document.body.style.overflow = 'auto';
    watchers.lightboxOpen(true);
    assert.equal(context.document.body.style.overflow, 'auto'); // Don't make Safari repaint its under-toolbar region on close.
    let prevented = 0;
    app.moveLightboxGesture({touches: [{}], preventDefault() {prevented++;}});
    assert.equal(prevented, 1); // Block background pan even if the touch began on controls.
    app.moveLightboxGesture({touches: [{}, {}], preventDefault() {prevented++;}});
    assert.equal(prevented, 1); // Native pinch zoom remains available.
    assert.equal(context.window.scrollY, 500);
    app.lightboxOpen = false;
    watchers.lightboxOpen(false);
    assert.equal(context.document.body.style.overflow, 'auto');
    app.lightboxMedia = [{type: 'photo'}];
    app.lightboxOpen = true;
    watchers.lightboxOpen(true);
    assert.equal(context.document.body.style.overflow, 'auto'); // Photos and GIFs use the same phone viewer.
    app.moveLightboxGesture({touches: [{}], preventDefault() {prevented++;}});
    assert.equal(prevented, 2);
    watchers.lightboxOpen(false);
    phone = false;
    watchers.lightboxOpen(true);
    assert.equal(context.document.body.style.overflow, 'hidden'); // Desktop modal locking is retained.
    watchers.lightboxOpen(false);
    assert.equal(context.document.body.style.overflow, 'auto');
});

test('ordinary feed and grid videos start inline without a native controls mount race', () => {
    const context = browserContext();
    const {tweetApp, tweetNookInitVideoPlayer} = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp, tweetNookInitVideoPlayer})');
    const media = {type: 'video', width: 720, height: 1280, download: {local_path: 'media/v.mp4'}};
    for (const list of [[media], [media, media]]) {
        const root = parseFakeHTML(tweetApp().renderMediaGrid(list))[0];
        for (const video of root.querySelectorAll('video')) {
            assert.ok(video.hasAttribute('playsinline'));
            assert.ok(video.hasAttribute('data-video-custom'));
            assert.equal(Number(video.dataset.videoAspect), 720 / 1280);
            assert.ok(!video.hasAttribute('controls'));
            const player = video.parentElement;
            for (const element of [player, player.querySelector('[data-video-seek]')]) element.style = {setProperty() {}};
            Object.assign(video, {paused: true, volume: 1, muted: true, currentTime: 0, duration: 10});
            tweetNookInitVideoPlayer(video);
            assert.equal(video.playsInline, true);
            assert.ok(video.hasAttribute('webkit-playsinline'));
            assert.ok(!video.hasAttribute('controls'));
        }
    }
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook/web/index.html'), 'utf8');
    assert.doesNotMatch(html, /<video[^>]*\scontrols(?:\s|>)/);
});

test('mobile viewer reuses its inline player and rejects dismissal click-through without native fullscreen', async () => {
    const context = browserContext();
    context.window.matchMedia = query => ({matches: !query.includes('prefers-reduced-motion')});
    context.getComputedStyle = () => ({borderRadius: '16px', objectFit: 'contain'});
    let now = 1000;
    context.Date = class extends Date { static now() {return now;} };
    const uiTimers = [];
    context.setTimeout = (callback, delay) => {uiTimers.push({callback, delay}); return uiTimers.length;};
    context.clearTimeout = () => {};
    const opened = [];
    context.window.dispatchEvent = event => opened.push(event);
    const {tweetApp, tweetNookInitVideoPlayer} = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp, tweetNookInitVideoPlayer})');
    const app = immediateComponent(tweetApp());
    const pendingTicks = [];
    app.$nextTick = callback => pendingTicks.push(callback);
    const feed = parseFakeHTML(app.renderMediaGrid([{type: 'video', download: {local_path: 'media/v.mp4'}}]))[0];
    feed.connected = true;
    const video = feed.querySelector('video'), player = video.parentElement;
    const stage = new FakeElement('div', [['class', 'lightbox-stage']]);
    stage.connected = true;
    const slide = new FakeElement('div', [['class', 'lightbox-slide is-active']]);
    const slot = new FakeElement('div', [['data-inline-video-slot', '']]);
    slide.appendChild(slot); stage.appendChild(slide);
    app.$refs.lightboxStage = stage;
    const viewer = new FakeElement('div', [['class', 'lightbox-viewer is-video-closed']]);
    viewer.connected = true;
    viewer.appendChild(stage);
    const viewerValues = new Map();
    viewer.style = {display: 'none', setProperty: (name, value) => viewerValues.set(name, value),
        getPropertyValue: name => viewerValues.get(name) || '', removeProperty: name => viewerValues.delete(name)};
    viewer.rect = {left: 0, top: 0, width: 390, height: 844};
    context.window.innerHeight = 844;
    context.window.scrollY = 240;
    app.$refs.lightboxViewer = viewer;
    const shade = {style: {}};
    app.$refs.lightboxShade = shade;
    const fades = [];
    shade.animate = (frames, options) => {
        const fade = {frames, options, cancelled: false, cancel() {this.cancelled = true;}};
        fades.push(fade); return fade;
    };
    context.getComputedStyle = element => element === shade
        ? {opacity: '0.6'} : {borderRadius: '16px', objectFit: 'contain'};
    app.$refs.inlineVideoSlot = slot;
    slot.style = {display: 'none'};
    const originalParent = player.parentElement;
    player.rect = {left: 60, top: 200, width: 300, height: 533};
    player.getBoundingClientRect = () => player.closest('.lightbox-stage') ? {left: 0, top: 0, width: 390, height: 844} : player.rect;
    video.getBoundingClientRect = () => video.closest('.lightbox-stage') ? {left: 0, top: slot.style.transform?.includes('180') ? 180 : 0, width: 390, height: 772} : player.rect;
    for (const element of [video, player, player.querySelector('[data-video-seek]')]) {
        const values = new Map();
        element.style = {setProperty: (name, value) => values.set(name, value), getPropertyValue: name => values.get(name) || '', removeProperty: name => values.delete(name)};
    }
    const motions = [];
    let nativeFullscreen = 0;
    let playParent;
    Object.assign(video, {readyState: 0, paused: false, ended: false, currentTime: 3, volume: .6, muted: true,
        videoWidth: 720, videoHeight: 1280, loop: true,
        pause() {this.paused = true;}, play() {playParent = this.closest('.lightbox-stage'); this.paused = false; return Promise.resolve();},
        webkitEnterFullscreen() {nativeFullscreen++;},
        animate(frames) {
            let complete;
            const finished = new Promise(resolve => {complete = resolve;});
            const motion = {frames, finished, complete, cancel() {}};
            motions.push(motion); return motion;
        }});
    player.animate = video.animate;
    context.document.createElement = tag => {
        const surface = new FakeElement(tag).cloneNode(false);
        surface.animate = player.animate;
        surface.getBoundingClientRect = video.getBoundingClientRect;
        return surface;
    };
    video.animate = () => {throw new Error('The decoder surface must not be animated.');};
    player.requestFullscreen = () => {nativeFullscreen++; return Promise.resolve();};
    tweetNookInitVideoPlayer(video);
    app.openLightbox([{type: 'video', src: '/media/v.mp4', isGif: false}], 0, video);
    assert.equal(playParent, stage); // Mount and play run in the original trusted tap.
    assert.equal(app.activeLightboxVideo(), video);
    assert.equal(feed.querySelectorAll('video').length, 0);
    assert.equal(stage.querySelectorAll('video').length, 1);
    assert.equal(video.currentTime, 3);
    assert.equal(viewer.style.display, 'flex');
    assert.equal(context.window.scrollY, 240); // Opening doesn't move the document.
    assert.equal(shade.style.top, '-604px'); // The page shade spans a viewport above and below the visible page.
    assert.equal(shade.style.height, '2532px');
    context.window.scrollY = 300;
    context.__state.events.get('scroll')();
    assert.equal(shade.style.top, '-544px'); // It follows any scroll while the viewer is open.
    context.window.scrollY = 240;
    context.__state.events.get('scroll')();
    assert.equal(JSON.stringify(fades[0].frames), '[{"opacity":0},{"opacity":1}]');
    assert.equal(fades[0].options.duration, 150); // The backdrop is dark well before the 300ms expansion lands.
    assert.equal(fades[0].options.easing, 'ease-out');
    assert.equal(slot.style.display, '');
    assert.equal(app.lightboxVideoMotionPhase, 'opening'); // First frame is ready before Alpine's next tick.
    assert.equal(motions.length, 1);
    assert.ok(video.parentElement.classList.contains('video-motion-surface'));
    assert.equal(player.querySelector('[data-video-controls]').parentElement, player); // Controls remain outside the animated picture.
    assert.equal(player.style.getPropertyValue('height'), ''); // The fullscreen control anchor never resizes.
    pendingTicks.shift()();
    assert.equal(motions.length, 1); // The deferred overlay mount must not restart motion.
    assert.equal(app.lightboxVideoMotionPhase, 'opening'); // No metadata/decoder wait for the existing poster.
    motions[0].complete(); await Promise.resolve(); await Promise.resolve();
    uiTimers.at(-1).callback();
    assert.ok(player.classList.contains('is-native-ui-hidden'));
    assert.ok(viewer.classList.contains('is-video-controls-hidden'));
    dispatchFeed(player, 'pointermove', player, {pointerType: 'mouse'});
    assert.ok(player.classList.contains('is-native-ui-hidden')); // Mobile tap visibility must not fight hover events.
    dispatchFeed(player, 'click', player);
    assert.ok(!player.classList.contains('is-native-ui-hidden'));
    assert.ok(!viewer.classList.contains('is-video-controls-hidden'));
    dispatchFeed(player, 'focusout', player);
    assert.equal(uiTimers.at(-1).delay, 3000); // Touch focus changes keep the normal reading interval.
    dispatchFeed(player, 'click', player.querySelector('[data-video-action="fullscreen"]'));
    assert.ok(player.classList.contains('is-video-fill'));
    assert.equal(nativeFullscreen, 0);
    video.currentTime = 7;
    slot.style.transform = 'translate3d(0, 180px, 0)';
    app.lightboxGestureOffsetY = 180;
    app.closeLightbox();
    assert.equal(fades[0].cancelled, true);
    assert.equal(JSON.stringify(fades[1].frames), '[{"opacity":0.6},{"opacity":0}]'); // Continue from the dragged shade.
    assert.equal(slot.style.transform, 'translate3d(0, 0, 0)');
    assert.equal(viewerValues.size, 0); // The viewer stays fixed in place; only the page shade fades.
    assert.equal(fades[1].options.duration, 120);
    assert.match(motions[1].frames[0].transform, /translate3d\(0px, 180px, 0\)/); // Continue from the finger, with no doubled/stale drag transform.
    motions[1].complete(); await Promise.resolve(); await Promise.resolve();
    assert.equal(app.lightboxOpen, false);
    assert.equal(viewer.style.display, 'none');
    assert.equal(viewerValues.size, 0);
    assert.equal(context.window.scrollY, 240);
    assert.equal(fades[1].cancelled, true);
    app.followLightboxShade(false);
    assert.ok(!context.__state.events.has('scroll')); // Closing stops following the page.
    assert.ok(!context.__state.events.has('resize'));
    assert.equal(player.parentElement, originalParent);
    assert.equal(feed.querySelector('video'), video);
    assert.equal(video.parentElement, player);
    assert.equal(stage.querySelectorAll('video').length, 0);
    assert.equal(video.currentTime, 7);
    assert.equal(video.paused, false);
    assert.ok(!player.classList.contains('is-video-fill'));
    dispatchFeed(player, 'click', video); // Safari's compatibility click following dismissal.
    assert.equal(opened.length, 0);
    now += 1000;
    dispatchFeed(player, 'click', video);
    assert.equal(opened.length, 1); // A subsequent intentional tap still opens the viewer.
    assert.equal(nativeFullscreen, 0);
});

test('touch scrub owns its pointer, coalesces decoder seeks and retains the pending preview', () => {
    const context = browserContext();
    const frames = new Map();
    let nextFrame = 0;
    context.window.requestAnimationFrame = callback => {frames.set(++nextFrame, callback); return nextFrame;};
    context.window.cancelAnimationFrame = id => frames.delete(id);
    const {tweetApp, tweetNookInitVideoPlayer, tweetNookSyncVideoDuration} = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp, tweetNookInitVideoPlayer, tweetNookSyncVideoDuration})');
    const root = parseFakeHTML(tweetApp().renderMediaGrid([{type: 'video', download: {local_path: 'media/v.mp4'}}]))[0];
    root.connected = true;
    const video = root.querySelector('video'), player = video.parentElement, seek = player.querySelector('[data-video-seek]');
    const picture = new FakeElement('div', [['class', 'video-motion-surface']]);
    player.insertBefore(picture, video);
    picture.appendChild(video); // Fullscreen scrubbing still belongs to the outer control anchor.
    for (const element of [player, seek]) element.style = {setProperty() {}};
    seek.rect = {left: 10, width: 100};
    let captured, released, time = 2, seeks = 0;
    seek.setPointerCapture = id => {captured = id;};
    seek.hasPointerCapture = id => captured === id;
    seek.releasePointerCapture = id => {released = id; captured = null;};
    Object.assign(video, {paused: false, duration: 10, muted: true, volume: 1, seeking: false,
        pause() {this.paused = true;}, play() {this.paused = false; return Promise.resolve();}});
    Object.defineProperty(video, 'currentTime', {get: () => time, set: value => {time = value; seeks++;}});
    tweetNookInitVideoPlayer(video);
    const down = dispatchFeed(player, 'pointerdown', seek, {button: 0, pointerType: 'touch', pointerId: 7, clientX: 40});
    assert.ok(down.defaultPrevented);
    assert.equal(captured, 7);
    const move = context.__state.events.get('document:pointermove');
    move({pointerId: 8, clientX: 100}); // A second finger must not steal the scrub.
    assert.equal(seek.value, 30);
    move({pointerId: 7, clientX: 90});
    move({pointerId: 7, clientX: 80});
    assert.equal(frames.size, 1);
    assert.equal(seeks, 0);
    frames.values().next().value(); frames.clear();
    assert.equal(time, 7);
    assert.equal(seeks, 1);
    dispatchFeed(video, 'seeked', video);
    frames.values().next().value(); frames.clear();
    assert.equal(seeks, 1); // A settled finger must not create a decoder seek loop.
    video.seeking = true;
    move({pointerId: 7, clientX: 100});
    tweetNookSyncVideoDuration(video); // An older decoded frame must not pull the thumb back.
    assert.equal(seek.value, 90);
    assert.equal(player.querySelector('[data-video-scrub-time]').textContent, '0:09 / 0:10');
    assert.equal(frames.size, 0);
    context.__state.events.get('document:pointerup')({pointerId: 7, clientX: 95});
    assert.equal(time, 8.5); // Final position is exact, even if the last decode is pending.
    assert.equal(video.paused, false);
    assert.equal(released, 7);
    assert.ok(!context.__state.events.has('document:pointermove'));
});

test('late lightbox observer and playback callbacks cannot restart a dismissed player', () => {
    const context = browserContext();
    const {tweetApp} = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.fetchSetup = async () => {};
    app.fetchNotices = async () => {};
    app.fetchActivityStatus = async () => {};
    app.initApp();
    let plays = 0;
    const video = {tagName: 'VIDEO', paused: true, hasAttribute: name => name === 'autoplay',
        closest: selector => selector === '.lightbox-stage' ? {} : {classList: {contains: () => true}},
        pause() {this.paused = true;}, play() {plays++; this.paused = false; return Promise.resolve();}};
    app.$refs.lightboxStage = {querySelectorAll: () => [video], querySelector: () => video};
    app.lightboxOpen = true;
    app.syncLightboxPlayback();
    assert.equal(plays, 1);
    app.closeLightbox();
    app.videoObserver.callback([{target: video, isIntersecting: true}]);
    app.syncLightboxPlayback();
    assert.equal(plays, 1);
    assert.equal(video.paused, true);
});

test('GIF indicator tracks playback state and toggles the archived animation', async () => {
    const context = browserContext();
    loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const classes = new Set();
    const attributes = new Map();
    let playCalls = 0;
    let pauseCalls = 0;
    const indicator = {
        classList: {
            toggle(name, enabled) {
                if (enabled) classes.add(name);
                else classes.delete(name);
            },
        },
        setAttribute(name, value) {
            attributes.set(name, value);
        },
    };
    const video = {
        paused: false,
        ended: false,
        parentElement: { querySelector: () => indicator },
        play() {
            playCalls += 1;
            this.paused = false;
            context.window.tweetNookSyncGifIndicator(this);
            return Promise.resolve();
        },
        pause() {
            pauseCalls += 1;
            this.paused = true;
            context.window.tweetNookSyncGifIndicator(this);
        },
    };
    indicator.parentElement = { querySelector: () => video };
    const event = {
        prevented: false,
        stopped: false,
        preventDefault() { this.prevented = true; },
        stopPropagation() { this.stopped = true; },
    };

    context.window.tweetNookSyncGifIndicator(video);
    assert.equal(attributes.get('aria-label'), 'Pause GIF');
    context.window.tweetNookToggleGif(event, indicator);
    assert.equal(pauseCalls, 1);
    assert.ok(classes.has('is-paused'));
    assert.equal(attributes.get('title'), 'Play GIF');
    context.window.tweetNookToggleGif(event, indicator);
    await Promise.resolve();
    assert.equal(playCalls, 1);
    assert.ok(!classes.has('is-paused'));
    assert.equal(event.prevented, true);
    assert.equal(event.stopped, true);
});

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

test('volume and mute are shared in both playback modes and inherited by new players', () => {
    for (const mode of ['autoplay', 'paused']) {
        const context = browserContext();
        const { tweetApp, forcedMuteVideos } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp, forcedMuteVideos})');
        const app = immediateComponent(tweetApp());
        app.videoPlayback = mode;
        app.videoObserver = app.videoAutoplayObserver = { observe() {} };
        const player = (lightbox = false) => {
            const video = new FakeElement('video', [['controls', '']]);
            let volume = 1, muted = false;
            Object.defineProperties(video, {
                volume: { get: () => volume, set(value) { volume = value; app.handleVideoVolumeChange(video); } },
                muted: { get: () => muted, set(value) { muted = value; app.handleVideoVolumeChange(video); } },
            });
            const closest = video.closest.bind(video);
            video.closest = selector => lightbox && selector === '.lightbox-stage' ? {} : closest(selector);
            context.__state.videos.push(video);
            app.observeVideo(video);
            return video;
        };
        const feed = player(), panel = player(), lightbox = player(true);
        feed.volume = .3;
        feed.muted = false;
        assert.equal(app.videoVolume, .3);
        for (const video of [panel, lightbox, player()]) {
            assert.equal(video.volume, .3);
            assert.equal(video.muted, false);
        }
        forcedMuteVideos.add(feed);
        feed.muted = true;
        feed.volume = .2;
        assert.equal(panel.volume, .2);
        assert.equal(panel.muted, false, 'browser-imposed mute stays local');
        feed.muted = false;
        assert.equal(forcedMuteVideos.has(feed), false);
        lightbox.volume = .7;
        lightbox.muted = true;
        assert.equal(feed.volume, .7);
        assert.equal(panel.muted, true);
        // GIF playback has its own permanently silent policy.
        const gif = new FakeElement('video', [['data-animated-gif', '']]);
        gif.volume = .1;
        gif.muted = true;
        app.handleVideoVolumeChange(gif);
        assert.equal(app.videoVolume, .7);
    }
});

test('autoplay setting plays the topmost visible video muted and shares unmuting', async () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    // Fake players dispatch their media events synchronously to the app handlers.
    const player = (top, { lightbox = false, custom = false } = {}) => {
        const video = {
            tagName: 'VIDEO',
            paused: true,
            isConnected: true,
            playCalls: 0,
            _muted: false,
            hasAttribute: name => name === (custom ? 'data-video-player' : 'controls'),
            closest: selector => (lightbox && selector === '.lightbox-stage' ? {} : null),
            getBoundingClientRect: () => ({ top }),
            get muted() { return this._muted; },
            set muted(value) {
                const changed = this._muted !== value;
                this._muted = value;
                if (changed) app.handleVideoVolumeChange(this);
            },
            play() {
                this.playCalls += 1;
                this.paused = false;
                app.handleVideoPlay(this);
                return Promise.resolve();
            },
            pause() {
                if (this.paused) return;
                this.paused = true;
                app.handleVideoPause(this);
            },
        };
        context.__state.videos.push(video);
        return video;
    };
    const lower = player(400, {custom: true});
    const upper = player(100);
    const lightbox = player(0, { lightbox: true });

    // Autoplay is the default; Click to Play never autoplays.
    assert.equal(app.videoPlayback, 'autoplay');
    app.setVideoPlayback('paused');
    app.updateAutoplayVisibility(lower, true);
    app.updateAutoplayVisibility(upper, true);
    assert.equal(upper.playCalls + lower.playCalls, 0);

    app.setVideoPlayback('autoplay');
    assert.equal(context.localStorage.getItem('tvx-video-playback'), 'autoplay');
    assert.equal(upper.playCalls, 1);
    assert.equal(lower.playCalls, 0);
    assert.ok(upper.muted && lower.muted && lightbox.muted);

    // Unmuting one player unmutes every player.
    upper.muted = false;
    assert.equal(app.videoMuted, false);
    assert.ok(!lower.muted && !lightbox.muted);

    // A video the viewer pauses stays paused; the next visible one takes over with sound.
    upper.pause();
    app.autoplayNextVideo();
    assert.equal(upper.playCalls, 1);
    assert.equal(lower.playCalls, 1);
    assert.equal(lower.muted, false);

    // Playing another video pauses the current one, and muting is shared again.
    lightbox.play();
    assert.ok(lower.paused);
    lightbox.muted = true;
    assert.ok(upper.muted && lower.muted);
    lightbox.paused = true;

    // Scrolling away clears a hold, but overlays keep autoplay back.
    app.updateAutoplayVisibility(upper, false);
    app.updateAutoplayVisibility(lower, false);
    app.lightboxOpen = true;
    app.updateAutoplayVisibility(lower, true);
    assert.equal(lower.playCalls, 1);
    app.lightboxOpen = false;
    app.autoplayNextVideo();
    assert.equal(lower.playCalls, 2);
    assert.equal(lower.muted, true);

    // Switching back pauses feed players and restores normal sound.
    app.setVideoPlayback('paused');
    assert.ok(lower.paused);
    assert.ok(!upper.muted && !lower.muted && !lightbox.muted);

    // Click to Play also keeps manual playback to one video at a time.
    upper.play();
    lower.play();
    assert.ok(upper.paused && !lower.paused);
    assert.equal(lower.muted, false);
    lower.pause();

    const restoredPlayback = () => {
        const restored = immediateComponent(tweetApp());
        restored.fetchSetup = async () => {};
        restored.fetchNotices = async () => {};
        restored.fetchActivityStatus = async () => {};
        restored.initApp();
        return restored.videoPlayback;
    };
    assert.equal(restoredPlayback(), 'paused');
    context.localStorage.removeItem('tvx-video-playback');
    assert.equal(restoredPlayback(), 'autoplay');

    const html = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'index.html'), 'utf8');
    assert.match(html, /role="switch" aria-label="Autoplay videos" :aria-checked="videoPlayback === 'autoplay'" @click="setVideoPlayback\(videoPlayback === 'autoplay' \? 'paused' : 'autoplay'\)"/);
    assert.match(html, /Play videos muted as they scroll into view\./);
});

test('activity drawer loads any pipeline and starts production jobs', async () => {
    const context = browserContext();
    const calls = [];
    let active = true;
    let stopped = false;
    context.fetch = async (url, options = {}) => {
        calls.push([url, options.method || 'GET']);
        if (url === '/api/activity/import') {
            active = true;
            stopped = false;
            return { ok: true, json: async () => ({ started: true, kind: 'import', run_id: 'run' }) };
        }
        if (url === '/api/activity/stop') {
            active = false;
            stopped = true;
            return { ok: true, json: async () => ({ stopping: true }) };
        }
        return {
            ok: true,
            json: async () => active ? {
                active: true,
                snapshot: {
                    run_id: 'active-run',
                    title: 'tweetnook import enrich',
                    started_at: Date.now() / 1000,
                    active_step: 'enrich',
                    steps: [{ key: 'enrich', state: 'active', completed: 5, total: 10 }],
                    issues: [],
                },
                schedule: { configured: false, relative: 'Not configured', date: 'Use cron or a service timer' },
            } : {
                active: false,
                snapshot: null,
                last_snapshot: {
                    run_id: 'active-run',
                    title: 'tweetnook import enrich',
                    completed_at: Date.now() / 1000,
                    stopped,
                    success: !stopped,
                    steps: [{
                        key: 'enrich',
                        state: stopped ? 'failed' : 'complete',
                        completed: stopped ? 5 : 10,
                        total: 10,
                    }],
                    issues: [],
                },
                schedule: { configured: false, relative: 'Not configured', date: 'Use cron or a service timer' },
            },
        };
    };
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());

    app.activityPageKey = 'old-run-stage';
    await app.fetchActivityStatus();
    assert.equal(app.activity.title, 'tweetnook import enrich');
    assert.equal(app.activityPercent(app.activity.steps[0]), 50);
    assert.equal(app.activityPageKey, null);

    active = false;
    await app.fetchActivityStatus();
    assert.equal(app.activity, null);
    assert.equal(app.lastActivity.title, 'tweetnook import enrich');
    assert.equal(app.activitySchedule.relative, 'Not configured');
    app.activityPageKey = '__summary__';
    await app.startActivity('import');
    assert.ok(calls.some(([url, method]) => url === '/api/activity/import' && method === 'POST'));
    assert.equal(app.activityStartPending, false);
    assert.equal(app.activityStartingKind, null);
    assert.equal(app.activityPageKey, null);
    assert.equal(app.activity.title, 'tweetnook import enrich');
    await app.stopActivity();
    assert.ok(calls.some(([url, method]) => url === '/api/activity/stop' && method === 'POST'));
    assert.equal(app.activity, null);
    assert.equal(app.activityPageKey, 'enrich');
    assert.equal(app.activitySelectedPageKey(), 'enrich');
    assert.equal(app.activitySegmentState(app.activitySelectedPage()), 'cancelled');
    assert.equal(app.activitySegmentPercent(app.activitySelectedPage()), 50);
});

test('activity updates retain unchanged rows and handle removal, cancellation, and new runs', async () => {
    const context = browserContext();
    let data = {
        active: true,
        schedule: {enabled: true, relative: 'Tomorrow'},
        snapshot: {
            run_id: 'one', active_step: 'media',
            steps: [
                {key: 'media', state: 'active', completed: 1, total: 10, counters: 'old', metrics: {downloaded: 1, failed: 1}},
                {key: 'articles', state: 'pending', completed: 0, total: 10},
            ],
            issues: [{level: 'warning', message: 'Retry', count: 1}],
        },
    };
    context.fetch = async () => ({ok: true, json: async () => JSON.parse(JSON.stringify(data))});
    const {tweetApp} = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    await app.fetchActivityStatus();
    const original = app.activity;
    const steps = original.steps;
    const media = steps[0];
    const articles = steps[1];
    const metrics = media.metrics;
    const issues = original.issues;
    const schedule = app.activitySchedule;
    await app.fetchActivityStatus();
    assert.equal(app.activity, original);
    assert.equal(app.activity.steps, steps);
    assert.equal(app.activity.steps[0], media);
    assert.equal(app.activity.issues, issues);
    assert.equal(app.activitySchedule, schedule);

    data.snapshot.steps[0].completed = 2;
    data.snapshot.steps[0].metrics.downloaded = 2;
    delete data.snapshot.steps[0].metrics.failed;
    delete data.snapshot.steps[0].counters;
    data.snapshot.issues[0].count = 2;
    await app.fetchActivityStatus();
    assert.equal(app.activity.steps[0], media);
    assert.equal(app.activity.steps[1], articles);
    assert.equal(media.completed, 2);
    assert.equal(media.metrics, metrics);
    assert.equal(metrics.downloaded, 2);
    assert.equal(Object.hasOwn(metrics, 'failed'), false);
    assert.equal(Object.hasOwn(media, 'counters'), false);
    assert.equal(issues[0].count, 2);

    data.snapshot.steps[0].metrics = null;
    await app.fetchActivityStatus();
    assert.equal(media.metrics, null);
    data.snapshot.steps.reverse();
    await app.fetchActivityStatus();
    assert.deepEqual(Array.from(app.activity.steps, step => step.key), ['articles', 'media']);
    data.snapshot.steps.push({key: 'tag', state: 'pending'});
    await app.fetchActivityStatus();
    assert.equal(app.activity.steps.length, 3);

    data = {...data, active: false, last_snapshot: {...data.snapshot, stopped: true, success: false}, snapshot: null};
    data.last_snapshot.steps[1].state = 'failed';
    app.activityError = 'Temporary connection failure';
    await app.fetchActivityStatus();
    assert.equal(app.activity, null);
    assert.equal(app.activityError, null);
    assert.equal(app.activityPageKey, 'media');
    assert.equal(app.activitySegmentState(app.activitySelectedPage()), 'cancelled');
    const finished = app.lastActivity;
    await app.fetchActivityStatus();
    assert.equal(app.lastActivity, finished);

    data = {...data, active: true, snapshot: {run_id: 'two', steps: [{key: 'prepare', state: 'active'}]}};
    await app.fetchActivityStatus();
    assert.equal(app.activity.run_id, 'two');
    assert.equal(app.activityPageKey, null);
    assert.equal(app.activitySelectedPageKey(), 'prepare');
    assert.equal(Object.hasOwn(app.activity, 'stopped'), false);
});

test('activity requests coalesce and superseded responses cannot revive a finished run', async () => {
    const context = browserContext();
    const requests = [];
    context.fetch = (_url, {signal}) => new Promise(resolve => requests.push({resolve, signal}));
    const {tweetApp} = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    const older = app.fetchActivityStatus();
    const shared = app.fetchActivityStatus();
    assert.equal(requests.length, 1);
    const newer = app.fetchActivityStatus(true);
    assert.equal(requests.length, 2);
    assert.equal(requests[0].signal.aborted, true);
    requests[1].resolve({ok: true, json: async () => ({active: false, last_snapshot: {run_id: 'one', success: true, steps: []}})});
    await newer;
    const finished = app.lastActivity;
    // Ignore abort on purpose: late response/body completion must still be harmless.
    requests[0].resolve({ok: true, json: async () => ({active: true, snapshot: {run_id: 'one', steps: [{key: 'media', state: 'active', completed: 100}]}})});
    await Promise.all([older, shared]);
    assert.equal(app.activity, null);
    assert.equal(app.lastActivity, finished);
    assert.equal(app.activityError, null);
});

test('activity request timeout releases the request and a successful retry clears its error', async () => {
    const context = browserContext();
    let timeout;
    context.setTimeout = (callback, delay) => { assert.equal(delay, 10000); timeout = callback; return 1; };
    context.clearTimeout = () => {};
    context.fetch = (_url, {signal}) => new Promise((_resolve, reject) => {
        signal.addEventListener('abort', () => reject(new Error('aborted')), {once: true});
    });
    const {tweetApp} = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    const request = app.fetchActivityStatus();
    timeout();
    await request;
    assert.equal(app.activityError, 'Activity status request timed out');
    context.fetch = async () => ({ok: true, json: async () => ({active: false})});
    await app.fetchActivityStatus();
    assert.equal(app.activityError, null);
});

test('activity polling adapts to idle and visibility while its elapsed clock stays independent', async () => {
    const context = browserContext();
    const timers = new Map();
    const intervals = new Map();
    let nextId = 0;
    let now = 100000;
    context.Date = class extends Date { static now() { return now; } };
    context.setTimeout = (callback, delay) => { const id = ++nextId; timers.set(id, {callback, delay}); return id; };
    context.clearTimeout = id => timers.delete(id);
    context.setInterval = (callback, delay) => { const id = ++nextId; intervals.set(id, {callback, delay}); return id; };
    context.clearInterval = id => intervals.delete(id);
    let requests = 0;
    let data = {active: true, snapshot: {run_id: 'one', started_at: 90, steps: []}};
    context.fetch = async url => {
        if (url === '/api/stats/enrichment-incomplete') return {ok: true, json: async () => ({incomplete: 0})};
        requests++;
        return {ok: true, json: async () => JSON.parse(JSON.stringify(data))};
    };
    const {tweetApp} = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.startActivityPolling();
    app.startActivityPolling();
    await app.fetchActivityStatus();
    assert.equal(requests, 1);
    assert.equal(intervals.size, 1);
    assert.deepEqual([...timers.values()].map(timer => timer.delay), [1000]);
    const original = app.activity;
    now = 101000;
    [...intervals.values()][0].callback();
    assert.equal(app.activityNowSeconds, 101);
    assert.equal(app.activity, original);
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook/web/index.html'), 'utf8');
    const headerJs = fs.readFileSync(path.join(JS_DIR, 'header.js'), 'utf8');
    assert.match(headerJs, /this\.formatActivityDuration\(this\.activityNowSeconds - this\.activity\.started_at\)/);

    data = {active: false, last_snapshot: {run_id: 'one', success: true, steps: []}};
    await app.fetchActivityStatus();
    assert.deepEqual([...timers.values()].map(timer => timer.delay), [5000]);
    context.document.hidden = true;
    context.__state.events.get('document:visibilitychange')();
    assert.equal(timers.size, 0);
    now = 102000;
    [...intervals.values()][0].callback();
    assert.equal(app.activityNowSeconds, 101);
    context.document.hidden = false;
    context.__state.events.get('document:visibilitychange')();
    await app.fetchActivityStatus();
    assert.equal(requests, 3);
    assert.equal(app.activityNowSeconds, 102);
    app.destroy();
    assert.equal(timers.size, 0);
    assert.equal(intervals.size, 0);
    assert.equal(context.__state.events.has('document:visibilitychange'), false);
    assert.equal(context.__state.events.has('focus'), false);
});

test('incomplete enrichment starts the fixed Web action and refreshes status', async () => {
    const context = browserContext();
    const requests = [];
    context.fetch = async (url, options = {}) => {
        requests.push([url, options.method || 'GET']);
        if (url === '/api/activity/enrich') {
            return {ok: true, json: async () => ({started: true, kind: 'enrich', run_id: 'run'})};
        }
        throw new Error(`unexpected request: ${url}`);
    };
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.archiveReady = true;
    let activityRefreshes = 0, enrichmentRefreshes = 0;
    app.fetchActivityStatus = async force => { assert.equal(force, true); activityRefreshes++; app.activityStartPending = false; };
    app.fetchArchiveEnrichmentStatus = async () => { enrichmentRefreshes++; };

    await app.startActivity('enrich');

    assert.deepEqual(requests, [['/api/activity/enrich', 'POST']]);
    assert.equal(activityRefreshes, 1);
    assert.equal(enrichmentRefreshes, 1);
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook/web/index.html'), 'utf8');
    assert.match(html, /Continue enrichment/);
});

test('enrichment completion refreshes pending work before showing a reminder', async () => {
    const context = browserContext();
    let finishCount;
    context.fetch = async url => {
        if (url === '/api/stats/enrichment-incomplete') {
            return {ok: true, json: () => new Promise(resolve => { finishCount = resolve; })};
        }
        assert.equal(url, '/api/activity/status');
        return {ok: true, json: async () => ({active: false})};
    };
    const {tweetApp} = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.setupForced = false;
    app.archiveEnrichmentIncomplete = 10;
    app.activity = {run_id: 'enrichment', title: 'tweetnook import enrich'};
    assert.equal(app.showArchiveEnrichmentReminder, false);
    await app.fetchActivityStatus();
    assert.equal(app.activity, null);
    assert.equal(app.showArchiveEnrichmentReminder, false);
    finishCount({incomplete: 0});
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(app.archiveEnrichmentIncomplete, 0);
    assert.equal(app.showArchiveEnrichmentReminder, false);
    app.archiveEnrichmentIncomplete = 2;
    assert.equal(app.showArchiveEnrichmentReminder, true);
    app.activityStartPending = true;
    assert.equal(app.showArchiveEnrichmentReminder, false);
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook/web/index.html'), 'utf8');
    assert.match(html, /x-show="showArchiveEnrichmentReminder"/);
    assert.doesNotMatch(html, /role="status">Archive enrichment is running in the background/);
});

test('a slow enrichment count cannot overwrite the completion refresh', async () => {
    const context = browserContext();
    const counts = [];
    context.fetch = async () => ({ok: true, json: () => new Promise(resolve => counts.push(resolve))});
    const {tweetApp} = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    const older = app.fetchArchiveEnrichmentStatus();
    const newer = app.fetchArchiveEnrichmentStatus();
    await new Promise(resolve => setImmediate(resolve));
    counts[1]({incomplete: 0});
    await newer;
    counts[0]({incomplete: 50});
    await older;
    assert.equal(app.archiveEnrichmentIncomplete, 0);
    assert.equal(app.archiveEnrichmentRefreshing, false);
});

test('schedule settings load, normalize, and save through the activity API', async () => {
    const context = browserContext();
    const requests = [];
    let schedule = {
        configured: true,
        enabled: true,
        description: 'Every week',
        relative: 'in 3 days',
        date: 'Mon, Aug 24 at 9:15 AM',
        config: {
            enabled: true,
            cadence: 'weekly',
            every_hours: 6,
            time: '09:15',
            randomize_time: true,
            random_offset_min_hours: 0,
            random_offset_max_hours: 2,
            weekday: 0,
            day_of_month: 1,
            timezone: 'UTC',
        },
    };
    context.fetch = async (url, options = {}) => {
        assert.equal(url, '/api/activity/schedule');
        requests.push(options);
        if (options.method === 'PUT') {
            const config = JSON.parse(options.body);
            schedule = {
                ...schedule,
                configured: config.enabled,
                enabled: config.enabled,
                description: 'Every month',
                relative: 'in 12 days',
                date: 'Tue, Sep 1 at 4:30 AM',
                config,
            };
        }
        return { ok: true, async json() { return schedule; } };
    };
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());

    await app.fetchScheduleSettings();
    assert.equal(app.scheduleForm.cadence, 'weekly');
    assert.equal(app.scheduleForm.weekday, 0);
    assert.equal(app.scheduleTimeHour, '9');
    assert.equal(app.scheduleTimeMinute, '15');
    assert.equal(app.scheduleTimePeriod, 'AM');
    assert.equal(app.scheduleForm.randomize_time, true);
    assert.equal(app.normalizeScheduleConfig({}).randomize_time, true);
    assert.equal(app.normalizeScheduleConfig({ randomize_time: false }).randomize_time, false);
    assert.equal(app.scheduleForm.random_offset_min_hours, 0);
    assert.equal(app.scheduleForm.random_offset_max_hours, 2);
    assert.equal(app.normalizeScheduleConfig({}).random_offset_min_hours, 0);
    assert.equal(app.normalizeScheduleConfig({}).random_offset_max_hours, 2);
    assert.equal(app.scheduleHasChanges(), false);
    assert.ok(app.scheduleTimezoneOptions().some(option => option.value === 'UTC'));
    assert.ok(app.scheduleTimezoneOptions().some(option => option.value === 'America/Detroit'));

    app.setScheduleTimePart('period', 'PM');
    app.setScheduleTimePart('minute', '45');
    assert.equal(app.scheduleForm.time, '21:45');
    app.scheduleForm.cadence = 'monthly';
    app.scheduleForm.day_of_month = 31;
    app.scheduleForm.randomize_time = true;
    app.scheduleForm.random_offset_min_hours = 0.5;
    app.scheduleForm.random_offset_max_hours = 2;
    app.scheduleFieldChanged();
    assert.equal(app.scheduleHasChanges(), true);
    await app.saveScheduleSettings();

    assert.equal(requests.at(-1).method, 'PUT');
    assert.equal(JSON.parse(requests.at(-1).body).day_of_month, 31);
    assert.equal(JSON.parse(requests.at(-1).body).time, '21:45');
    assert.equal(JSON.parse(requests.at(-1).body).random_offset_min_hours, 0.5);
    assert.equal(app.scheduleSettings.description, 'Every month');
    assert.equal(app.activitySchedule.description, 'Every month');
    assert.equal(app.scheduleSaved, true);
    assert.equal(app.scheduleHasChanges(), false);
});

test('schedule settings bind every control to the schedule form', () => {
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'index.html'), 'utf8');
    const start = html.indexOf('<!-- Scheduled sync settings -->');
    const end = html.indexOf('<!-- Activity logs -->');
    const scheduleHtml = html.slice(start, end);

    assert.match(html, /id="save-schedule-btn"[^>]*@click="saveScheduleSettings\(\)"/);
    assert.match(scheduleHtml, /aria-label="Sync automatically" :aria-checked="scheduleForm\.enabled" @click="scheduleForm\.enabled = !scheduleForm\.enabled; scheduleFieldChanged\(\)"/);
    assert.match(scheduleHtml, /<fieldset[^>]*:disabled="!scheduleForm\.enabled"/);
    assert.match(scheduleHtml, /scheduleForm\.cadence = cadence\.key/);
    // Dropdowns are custom listboxes (nookSelect) bound to the same form fields.
    const select = field => new RegExp(`value: \\(\\) => ${field.replace('.', '\\.')}, choose: value => \\{`);
    for (const field of ['scheduleForm.every_hours', 'scheduleTimeHour', 'scheduleTimeMinute', 'scheduleForm.day_of_month', 'scheduleForm.timezone', 'scheduleForm.random_offset_min_hours', 'scheduleForm.random_offset_max_hours']) {
        assert.match(scheduleHtml, select(field), field);
    }
    assert.match(scheduleHtml, /@click="setScheduleTimePart\('period', 'AM'\)"/);
    assert.match(scheduleHtml, /@click="scheduleForm\.weekday = index; scheduleFieldChanged\(\)"/);
    assert.match(scheduleHtml, /aria-label="Vary the start time" :aria-checked="scheduleForm\.randomize_time"/);
    // What to sync mirrors the sync panel's choice (sync.collections).
    for (const key of ['bookmarks', 'likes', 'tweets']) {
        assert.match(scheduleHtml, new RegExp(`:aria-checked="nookSyncCollections\\.includes\\('${key}'\\)" @click="nookToggleSyncCollection\\(\\$data, '${key}'\\)"`));
    }
});

test('unchecked square checkboxes use thinner outlines while checked states retain weight', () => {
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'index.html'), 'utf8');
    assert.equal(
        (html.match(/class="flex h-6 w-6 items-center justify-center rounded-lg border-2 transition"/g) || []).length,
        1,
    );
    assert.equal((html.match(/text-white border-\[3px\]'/g) || []).length, 1);
});

test('activity stages use focused pages and finish on a run summary', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.activity = {
        title: 'tweetnook sync',
        steps: [
            { key: 'one', title: 'First step', state: 'complete', summary: 'done' },
            { key: 'two', title: 'Second step', state: 'skipped', summary: 'not needed' },
            { key: 'three', title: 'Current step', state: 'active', completed: 2, total: 4 },
            { key: 'four', title: 'Later step', state: 'pending' },
        ],
        issues: [
            { level: 'warning', message: 'Retrying one item', count: 2 },
            { level: 'error', message: 'One item failed', count: 1 },
        ],
    };

    assert.equal(app.activityActiveStep().key, 'three');
    assert.equal(app.activityIssues().length, 2);
    assert.equal(app.activityIssueCount(), 3);
    assert.equal(app.activityRunState(), 'active');
    assert.equal(app.activityRunStatusTitle(), 'Sync in progress');
    assert.equal(app.activityRunCounts(), '4 stages · 1 completed · 1 skipped');
    assert.deepEqual(
        Array.from(app.activityPages(), page => page.key),
        ['one', 'two', 'three', 'four', '__summary__'],
    );
    assert.equal(app.activityPages().at(-1).state, 'pending');
    assert.equal(app.activitySelectedSegmentGrow(), 8 / 3);
    assert.equal(app.activitySegmentPercent(app.activity.steps[0]), 100);
    assert.equal(app.activitySegmentPercent(app.activity.steps[1]), 100);
    assert.equal(app.activitySegmentPercent(app.activity.steps[2]), 50);
    assert.equal(app.activitySegmentPercent(app.activity.steps[3]), 0);
    assert.equal(app.activitySegmentPercent(app.activityPages().at(-1)), 0);
    assert.equal(app.activitySegmentState(app.activity.steps[2]), 'active');
    assert.equal(app.activityPageStateLabel(app.activity.steps[0]), 'Complete');
    assert.equal(app.activityPageStateLabel(app.activity.steps[1]), 'Skipped');
    assert.equal(app.activityPageStateLabel(app.activity.steps[2]), 'In progress');
    assert.equal(app.activityPageUpdateText(app.activity.steps[2]), 'Latest update · Waiting for first result');
    assert.equal(app.activityPageTimingText(app.activity.steps[2]), 'Timing · Estimating remaining time');
    app.activity.steps[2].counters = '2 processed';
    app.activity.steps[2].eta_seconds = 30;
    assert.equal(app.activityPageUpdateText(app.activity.steps[2]), 'Latest update · 2 processed');
    assert.equal(app.activityPageTimingText(app.activity.steps[2]), 'Timing · About 00:30 remaining');
    assert.equal(app.activityRunStateLabel(), 'In progress');
    assert.equal(app.activitySelectedPageKey(), 'three');
    assert.equal(app.activitySelectedPageIndex(), 2);
    assert.equal(app.activityPageDirection, 'forward');

    app.selectActivityPage('one');
    assert.equal(app.activitySelectedPageKey(), 'one');
    assert.equal(app.activityPageDirection, 'back');
    app.moveActivityPage(1);
    assert.equal(app.activitySelectedPageKey(), 'two');
    assert.equal(app.activityPageDirection, 'forward');
    app.moveActivityPage(-10);
    assert.equal(app.activitySelectedPageKey(), 'one');
    assert.equal(app.activityPageDirection, 'back');
    app.openActivityDrawer();
    assert.equal(app.activityDrawerOpen, true);
    assert.equal(app.activityPageKey, null);
    assert.equal(app.activityPageDirection, 'forward');
    assert.equal(app.activitySelectedPageKey(), 'three');
    app.toggleActivityDrawer();
    assert.equal(app.activityDrawerOpen, false);
    assert.equal(app.activityCommandLabel(), 'Archive Sync');

    app.activity.title = 'tweetnook import enrich';
    assert.equal(app.activityCommandLabel(), 'Archive Preparation');
    app.activity.title = 'tweetnook media download';
    assert.equal(app.activityCommandLabel(), 'Media Download');
    app.activityStartPending = true;
    app.activityStartingKind = 'enrich';
    assert.equal(app.activityCommandLabel(), 'Archive Preparation');
    app.activityStartPending = false;
    app.activityStartingKind = null;
    app.activity.title = 'tweetnook sync';

    app.lastActivity = {
        ...app.activity,
        running: false,
        success: true,
        steps: app.activity.steps.map(step => ({ ...step, state: 'complete' })),
    };
    app.activity = null;
    app.activityPageKey = null;
    assert.equal(app.activityRunState(), 'complete');
    assert.equal(app.activityRunStateLabel(), 'Complete');
    assert.equal(app.activityRunStatusTitle(), 'Last run completed');
    assert.equal(app.activitySelectedPageKey(), '__summary__');
    assert.equal(app.activitySelectedPage().is_summary, true);
    assert.equal(app.activityPages().at(-1).state, 'complete');
    assert.equal(app.activitySegmentPercent(app.activityPages().at(-1)), 100);
    assert.equal(app.activityRunCounts(), '4 stages · 4 completed');

    app.lastActivity = {
        ...app.lastActivity,
        success: false,
        stopped: true,
        steps: [
            { key: 'one', title: 'First step', state: 'complete', completed: 1, total: 1 },
            { key: 'three', title: 'Stopped step', state: 'failed', completed: 2, total: 4 },
            { key: 'four', title: 'Later step', state: 'skipped', completed: 0, total: 1 },
        ],
    };
    const stoppedPage = app.activityPages().find(page => page.key === 'three');
    assert.equal(app.activitySegmentState(stoppedPage), 'cancelled');
    assert.equal(app.activityPageStateLabel(stoppedPage), 'Canceled');
    assert.equal(app.activityRunStateLabel(), 'Canceled');
    assert.equal(app.activitySegmentPercent(stoppedPage), 50);
    assert.equal(app.activitySegmentState(app.activityPages().at(-1)), 'cancelled');
    assert.equal(app.activitySegmentPercent(app.activityPages().at(-1)), 100);
    assert.equal(app.activityRunCounts(), '3 stages · 1 completed · 1 skipped · 1 canceled');
});

test('inactive unknown results are interrupted across the drawer and run history', () => {
    const context = browserContext();
    const { tweetApp, nookSyncPanel, nookLogsPage } = loadScripts(context,
        ['themes.js', 'app.js', 'header.js', 'settings.js'], '({tweetApp, nookSyncPanel, nookLogsPage})');
    const app = immediateComponent(tweetApp());
    app.lastActivity = {run_id: 'unknown', running: false, success: null, steps: []};
    const panel = Object.assign(Object.create(app), nookSyncPanel());
    assert.equal(panel.headTitle(), 'Sync interrupted');
    assert.equal(app.activityRunStateLabel(), 'Interrupted');
    assert.equal(app.activityRunStatusTitle(), 'Last run interrupted');
    assert.equal(app.activityRunState(), 'failed');
    app.lastSyncFormatted = 'Oct 17, 2026';
    assert.equal(app.activityHeaderText(), 'Sync interrupted — open Activity');
    const logs = nookLogsPage();
    assert.equal(logs.statusLabel({status: 'interrupted'}), 'Interrupted');
    assert.equal(logs.statusKind({status: 'interrupted'}), 'failed');
    app.lastActivity.success = false;
    assert.equal(panel.headTitle(), 'Sync failed');
    assert.equal(app.activityHeaderText(), 'Sync failed — open Activity');
    app.lastActivity.success = true;
    assert.equal(panel.headTitle(), 'Synced');
    assert.equal(app.activityHeaderText(), 'Synced Oct 17, 2026');
});

test('activity schedule footer separates the day from the emphasized run time', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());

    app.activitySchedule = {
        enabled: true,
        relative: 'daily around 3:00 AM',
        date: 'Tomorrow · browser local time',
        config: { cadence: 'daily', time: '03:00', randomize_time: true },
    };
    assert.equal(app.activityScheduleDayText(), 'Tomorrow');
    assert.equal(app.activityScheduleTimeText(), 'around 3:00 AM');

    app.activitySchedule = {
        enabled: false,
        relative: 'Not scheduled',
        date: 'Enable scheduling in Settings',
        config: {},
    };
    assert.equal(app.activityScheduleDayText(), 'Not scheduled');
    assert.equal(app.activityScheduleTimeText(), '');
});

test('activity run summary turns structured outcomes into analytics-style cards', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.lastActivity = {
        running: false,
        success: true,
        elapsed_seconds: 180,
        issues: [{ level: 'warning', message: 'One retry', count: 2 }],
        steps: [
            { key: 'sync:bookmarks:head', metrics: { new_tweets: 12, tweets_seen: 20, pages: 1 } },
            { key: 'sync:likes:head', metrics: { new_tweets: 8, tweets_seen: 20, pages: 1 } },
            { key: 'threads', metrics: { expanded: 6, already_known: 148, failed: 0 } },
            { key: 'resurrection', metrics: { restored: 1, still_unavailable: 15, retry_later: 2, remaining_due: 3 } },
            { key: 'media', metrics: { downloaded: 28, downloaded_bytes: 19503514, already_saved: 4, failed: 0 } },
            { key: 'urls', metrics: { updated: 2, failed: 0 } },
            { key: 'articles', state: 'skipped', metrics: {} },
        ],
    };

    const cards = Object.fromEntries(
        app.activitySummaryMetrics().map(card => [card.key, card]),
    );
    assert.equal(cards['sync:bookmarks'].value, '12');
    assert.equal(cards['sync:bookmarks'].subtitle, '20 checked · 1 page');
    assert.equal(cards['sync:likes'].label, 'New liked tweets');
    assert.equal(cards['sync:likes'].value, '8');
    assert.equal(cards['tweets-checked'].value, '40');
    assert.equal(cards['pages-fetched'].value, '2');
    assert.equal(cards['media-downloaded'].value, '28');
    assert.equal(cards['media-size'].label, 'Media size');
    assert.equal(cards['media-size'].value, '18.6 MiB');
    assert.equal(cards['media-saved'].value, '4');
    assert.equal(cards['media-failed'].value, '0');
    assert.equal(cards.threads.subtitle, 'New context saved');
    assert.equal(cards['threads-known'].value, '148');
    assert.equal(cards['threads-known'].wide, true);
    assert.equal(cards['threads-failed'].value, '0');
    assert.equal(cards.restored.label, 'Tweets resurrected');
    assert.equal(cards.restored.value, '1');
    assert.equal(cards['still-unavailable'].value, '15');
    assert.equal(cards['still-unavailable'].subtitle, '2 retry later · 3 still due');
    assert.equal(cards['still-unavailable'].wide, true);
    assert.equal(cards['recovery-retries'].value, '2');
    assert.equal(cards['recovery-queue'].value, '3');
    assert.equal(cards.urls.value, '2');
    assert.equal(cards.urls.label, 'Link previews updated');
    assert.equal(cards.urls.wide, true);
    assert.equal(cards['urls-failed'].value, '0');
    assert.equal(cards.articles.subtitle, 'No refresh needed');
    assert.equal(cards['articles-failed'].value, '0');
    assert.equal(cards.duration.value, '03:00');
    assert.equal(cards.issues.value, '2');
    assert.equal(Object.keys(cards).length, 21);

    app.activity = {
        running: true,
        elapsed_seconds: 0,
        steps: [{ key: 'media', state: 'pending', metrics: {} }],
        issues: [],
    };
    app.lastActivity = null;
    const plannedCards = Object.fromEntries(
        app.activitySummaryMetrics().map(card => [card.key, card]),
    );
    assert.equal(plannedCards['media-downloaded'].value, '—');
    assert.equal(plannedCards['media-size'].value, '—');
    assert.equal(plannedCards['media-saved'].subtitle, 'Available after this stage');
});

test('community notes escape headings, text, labels, and external links', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(
        context,
        ['themes.js', 'app.js'],
        '({tweetApp})',
    );
    const app = immediateComponent(tweetApp());
    const note = app.renderCommunityNote({
        birdwatch_pivot: {
            shorttitle: '<img onerror=x>',
            subtitle: {
                text: 'Read source',
                entities: [
                    {
                        fromIndex: 5,
                        toIndex: 11,
                        ref: { urlType: 'ExternalUrl', url: '" onmouseover="x' },
                    },
                ],
            },
        },
    });
    assert.match(note, /&lt;img onerror=x&gt;/);
    // Note links pass safeURL: no attribute breakout, and only http(s) targets.
    assert.doesNotMatch(note, /onmouseover="/);
    assert.match(note, /href="http:\/\/localhost\/%22%20onmouseover=%22x"/);
    assert.doesNotMatch(note, /<img onerror/);
});

test('community note detail uses captured title and omits footers, while feed and quote retain compact title', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    const raw = { birdwatch_pivot: {
        title: 'Full <b>title</b>',
        shorttitle: 'Short title',
        subtitle: { text: 'Note body\nsecond line', entities: [] },
        footer: { text: 'Footer information', entities: [] },
    } };
    const feed = app.renderCommunityNote(raw);
    const quote = app.renderCommunityNote(raw, true);
    const detail = app.renderCommunityNote(raw, false, true);
    const quoteWithMedia = app.renderCommunityNote(raw, true, false, true);
    assert.match(feed, /Short title/);
    assert.doesNotMatch(feed, /Footer information/);
    assert.match(quote, /community-note-quote/);
    assert.match(quote, /community-note-quote-spaced/);
    assert.match(quoteWithMedia, /community-note-quote-media/);
    assert.match(quote, /Short title/);
    assert.match(detail, /Full &lt;b&gt;title&lt;\/b&gt;/);
    assert.ok(detail.includes('Note body\nsecond line'));
    assert.doesNotMatch(detail, /Footer information|community-note-footer/);
    assert.doesNotMatch(quote, /Footer information/);
    assert.doesNotMatch(detail, /<b>title<\/b>/);

    const missingText = app.renderCommunityNote({ birdwatch_pivot: { shorttitle: 'No note text' } }, false, true);
    assert.equal(missingText, '');

    const html = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'index.html'), 'utf8');
    for (const prefix of ['threadData.main', 'panelThreadData.main']) {
        const detailCall = `renderCommunityNote(${prefix}.raw_json, false, true)`;
        const detailIndex = html.indexOf(detailCall);
        const timestampIndex = html.indexOf('class="tweet-timestamp', detailIndex);
        assert.ok(detailIndex >= 0 && timestampIndex > detailIndex, `${prefix} detail note precedes timestamp`);
    }
});

test('automated tagging has a dedicated conditional Settings page', () => {
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'index.html'), 'utf8');
    assert.match(html, /x-show="automatedTaggingInstalled"/);
    assert.match(html, /id="nook-nav-automated-tagging"[^>]*x-show="automatedTaggingInstalled"|x-show="automatedTaggingInstalled"[^>]*id="nook-nav-automated-tagging"/);
    assert.match(html, /@click="nookSettingsNavigate\(\$data, 'automated-tagging'\)"/);
    assert.match(html, /x-show="settingsTab === 'automated-tagging' && automatedTaggingInstalled"/);
    assert.match(html, /automatedTaggingContextItems/);
    assert.match(html, /handleAutomatedTaggingContextKeydown\(\$event\)/);
    const automatedTaggingStart = html.indexOf('<!-- Optional direct-Gemini automated tagging -->');
    const automatedTaggingEnd = html.indexOf('<!-- Config Section -->');
    const automatedTaggingHtml = html.slice(automatedTaggingStart, automatedTaggingEnd);
    assert.match(automatedTaggingHtml, /<fieldset[^>]*:disabled="!automatedTagging\.enabled"/);
    assert.match(automatedTaggingHtml, /<input type="checkbox" role="switch" aria-label="Enable automated tagging" x-model="automatedTagging\.enabled"/);
    assert.match(automatedTaggingHtml, /<input type="checkbox" x-model="automatedTagging\.google_search"/);
    assert.match(automatedTaggingHtml, /<select[^>]*x-model="automatedTagging\.model"/);
    assert.match(automatedTaggingHtml, /<option[^>]*:value="model\.id"[^>]*x-text="model\.name"/);
    assert.match(automatedTaggingHtml, /loadAutomatedTaggingModels\(true\)/);
    assert.match(automatedTaggingHtml, /automatedTagging\.thinking_level = level\.key/);
    assert.match(automatedTaggingHtml, /automatedTagging\.processing_tier = 'standard'/);
    assert.match(automatedTaggingHtml, /automatedTagging\.processing_tier = 'flex'/);
    assert.match(automatedTaggingHtml, /x-model="automatedTaggingSpendAmount"/);
    assert.match(automatedTaggingHtml, /<select[^>]*x-model="automatedTaggingSpendPeriod"/);
    assert.match(html, /x-model\.number="automatedTagging\.free_batch_size"/);
    assert.match(html, /x-model="automatedTaggingInstructionInput"/);
    assert.match(html, /addAutomatedTaggingInstruction\(\)/);
    assert.match(html, /removeAutomatedTaggingInstruction\(item\)/);
    assert.match(html, /x-model="automatedTaggingTweetId"/);
    assert.match(html, /lookupAutomatedTaggingTweet\(\)/);
    assert.match(html, /@click="openAutomatedTaggingSpendHistory\(\)"/);
    assert.match(html, /automatedTaggingSpendChartData\(\)/);
    assert.match(html, /automatedTaggingSpendModels\(\)/);
});

test('Settings hidden checkboxes stay anchored inside the modal', () => {
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'index.html'), 'utf8');
    const settingsHtml = html.slice(
        html.indexOf('<!-- Settings Modal'),
        html.indexOf('<!-- Setup Modal -->'),
    );
    // Other Settings switches are buttons (role="switch"); these two still wrap a hidden checkbox.
    for (const model of [
        'automatedTagging.enabled',
        'automatedTagging.google_search',
    ]) {
        const inputAt = settingsHtml.indexOf(`x-model="${model}"`);
        assert.notEqual(inputAt, -1, `missing Settings checkbox for ${model}`);
        const beforeInput = settingsHtml.slice(Math.max(0, inputAt - 300), inputAt);
        const anchorAt = Math.max(beforeInput.lastIndexOf('<label'), beforeInput.lastIndexOf('<span'), beforeInput.lastIndexOf('<div'));
        const anchor = beforeInput.slice(anchorAt);
        assert.match(anchor, /class="[^"]*\brelative\b[^"]*"/, `${model} needs a positioned ancestor`);
    }
});

test('automated tagging spend periods normalize to the stored daily limit', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.automatedTagging = { daily_spend_limit_usd: 2 };

    app.syncAutomatedTaggingSpendState();
    assert.equal(app.automatedTaggingSpendAmount, 2);
    app.setAutomatedTaggingSpendPeriod('week');
    assert.equal(app.automatedTaggingSpendAmount, 14);

    app.setAutomatedTaggingSpendAmount('21');
    assert.equal(app.automatedTaggingSpendAmount, 21);
    assert.equal(app.automatedTagging.daily_spend_limit_usd, 3);
    app.setAutomatedTaggingSpendPeriod('month');
    assert.equal(app.automatedTaggingSpendAmount, 90);
});

test('automated tagging spend history summarizes ranges, chart groups, and models', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.automatedTaggingAccounting = {
        spend_history: [
            { date: '2026-08-14', estimated_cost_usd: '0.001', requests: 1 },
            { date: '2026-08-15', estimated_cost_usd: '0.029', requests: 2 },
        ],
        lifetime_spend_history: [
            { date: '2026-07-01', interval: 'month', estimated_cost_usd: '0.20', requests: 5 },
            { date: '2026-08-01', interval: 'month', estimated_cost_usd: '0.30', requests: 6 },
        ],
        models: {
            'gemini-flash': { estimated_cost_usd: '0.01', requests: 4 },
            'gemini-pro': { estimated_cost_usd: '0.02', requests: 1 },
        },
        tweet_types: {
            text_only: {
                estimated_cost_usd: '0.01', requests: 2, priced_requests: 2,
                tweets: 4, priced_tweets: 4, media_items: 0,
            },
            video: {
                estimated_cost_usd: '0.02', requests: 1, priced_requests: 1,
                tweets: 1, priced_tweets: 1, media_items: 2, video_items: 2,
            },
        },
    };
    app.automatedTaggingSpendRange = '7d';

    assert.equal(app.automatedTaggingSpendRangeDays(), 7);
    assert.equal(app.automatedTaggingSpendRangeTotal(), 0.03);
    assert.equal(app.automatedTaggingSpendRangeRequests(), 3);
    assert.equal(app.automatedTaggingSpendChartData().length, 2);
    assert.equal(app.automatedTaggingSpendBarHeight(app.automatedTaggingSpendChartData()[1]), '100%');
    assert.equal(app.automatedTaggingSpendModels()[0].model, 'gemini-pro');
    assert.equal(app.automatedTaggingSpendTweetTypes()[0].label, 'Text only');
    assert.equal(app.automatedTaggingSpendTweetTypes()[0].average_tweet_usd, 0.0025);
    assert.equal(app.automatedTaggingSpendTweetTypes()[1].average_request_usd, 0.02);
    assert.equal(app.automatedTaggingTweetTypeMediaLabel(app.automatedTaggingSpendTweetTypes()[1]), '2 videos');
    assert.equal(app.formatAutomatedTaggingUsd(0.001), '$0.0010');
    assert.equal(app.formatAutomatedTaggingSpendDate('2026-08-15'), 'Aug 15');
    assert.match(app.automatedTaggingSpendBarLabel(app.automatedTaggingSpendChartData()[0]), /\$0\.0010 across 1 requests/);

    app.automatedTaggingSpendRange = 'lifetime';
    assert.equal(app.automatedTaggingSpendRangeDays(), null);
    assert.equal(app.automatedTaggingSpendRangeTotal(), 0.5);
    assert.equal(app.automatedTaggingSpendRangeRequests(), 11);
    assert.equal(app.automatedTaggingSpendChartData()[0].interval, 'month');
    assert.equal(app.formatAutomatedTaggingSpendDate('2026-08-01', true, true), 'Aug 2026');
    assert.match(app.automatedTaggingSpendBarLabel(app.automatedTaggingSpendChartData()[0]), /Jul 2026/);

    let closeFocused = false;
    let buttonFocused = false;
    app.$refs = {
        automatedTaggingSpendClose: { focus() { closeFocused = true; } },
        automatedTaggingSpendButton: { focus() { buttonFocused = true; } },
    };
    app.openAutomatedTaggingSpendHistory();
    assert.equal(app.showAutomatedTaggingSpendModal, true);
    assert.equal(app.automatedTaggingSpendRange, '30d');
    assert.equal(closeFocused, true);
    app.closeAutomatedTaggingSpendHistory();
    assert.equal(app.showAutomatedTaggingSpendModal, false);
    assert.equal(buttonFocused, true);
});

test('automated tagging model refresh caches until forced', async () => {
    const context = browserContext();
    let requests = 0;
    context.fetch = async () => {
        requests += 1;
        return {
            ok: true,
            async json() {
                return { models: [{ id: 'gemini-3.6-flash', name: 'Gemini 3.6 Flash' }] };
            },
        };
    };
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.automatedTagging = {
        api_key: '********',
        api_mode: 'free',
        processing_tier: 'standard',
        model: 'gemini-3.6-flash',
    };

    await app.loadAutomatedTaggingModels();
    await app.loadAutomatedTaggingModels();
    assert.equal(requests, 1);
    await app.loadAutomatedTaggingModels(true);
    assert.equal(requests, 2);
});

test('automated tagging context commits on Enter, comma, and Tab', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.automatedTaggingContextInput = 'Deadlock';
    let prevented = false;
    app.handleAutomatedTaggingContextKeydown({
        key: 'Enter',
        preventDefault() {
            prevented = true;
        },
    });
    assert.equal(prevented, true);
    assert.deepEqual(Array.from(app.automatedTaggingContextItems), ['Deadlock']);
    assert.equal(app.automatedTaggingContextInput, '');

    app.automatedTaggingContextInput = 'Guilty Gear';
    prevented = false;
    app.handleAutomatedTaggingContextKeydown({
        key: ',',
        preventDefault() {
            prevented = true;
        },
    });
    assert.equal(prevented, true);
    assert.deepEqual(Array.from(app.automatedTaggingContextItems), ['Deadlock', 'Guilty Gear']);

    app.automatedTaggingContextInput = 'Rust';
    let tabPrevented = false;
    app.handleAutomatedTaggingContextKeydown({
        key: 'Tab',
        preventDefault() {
            tabPrevented = true;
        },
    });
    assert.equal(tabPrevented, false);
    assert.deepEqual(Array.from(app.automatedTaggingContextItems), ['Deadlock', 'Guilty Gear', 'Rust']);
});

test('saving automated tagging includes a context hint still in the input', async () => {
    const context = browserContext();
    const requests = [];
    context.fetch = async (url, options = {}) => {
        requests.push([url, JSON.parse(options.body)]);
        return {
            ok: true,
            async json() {
                return {
                    values: {
                        enabled: false,
                        api_key: '********',
                        tagging_context: ['Deadlock'],
                    },
                };
            },
        };
    };
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.automatedTagging = { enabled: false, api_key: '********', tagging_context: [] };
    app.automatedTaggingContextInput = 'Deadlock';

    await app.saveAutomatedTagging();

    assert.equal(requests.length, 1);
    assert.deepEqual(requests[0][1].values.tagging_context, ['Deadlock']);
    assert.deepEqual(Array.from(app.automatedTaggingContextItems), ['Deadlock']);
});

test('offline fonts use local families, permission-based discovery, and no stylesheets', async () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({ tweetApp })');
    const component = immediateComponent(tweetApp());
    await component.discoverFonts();
    assert.match(component.fontDiscoveryMessage, /unavailable/);
    context.window.isSecureContext = true;
    context.window.queryLocalFonts = async () => [
        { family: 'Zed' }, { family: 'Alpha' }, { family: 'Alpha' },
    ];
    await component.discoverFonts();
    assert.equal(component.installedFonts.length, 2);
    assert.equal(component.fontDiscoveryBusy, false);
    component.selectFont('local:Alpha');
    assert.equal(context.document.body.style.fontFamily, '"Alpha", sans-serif');
    assert.equal(context.__state.stored.get('tvx-font'), 'local:Alpha');
    component.setFont('local:My "Font"', component.localFontFamily('My "Font"'));
    assert.equal(component.fontFamily, 'local:My "Font"');
    assert.match(context.document.body.style.fontFamily, /\\22 /);
    assert.ok(component.fontChoices().some(font => font.key === component.fontFamily));
    context.window.queryLocalFonts = async () => { throw { name: 'NotAllowedError' }; };
    await component.discoverFonts();
    assert.match(component.fontDiscoveryMessage, /not granted/);
    assert.equal(component.fontDiscoveryBusy, false);
    assert.equal(context.__state.appendedLinks.length, 0);
});

test('card thumbnails only render matching archived images or posters', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({ tweetApp })');
    const component = immediateComponent(tweetApp());
    const remote = 'https://example.com/card.jpg';
    assert.equal(component.localCardImage({}, remote), null);
    assert.equal(component.localCardImage({ media: [{ url: remote, download: {
        local_path: 'media/100/card image.jpg',
    } }] }, remote), '/media/100/card%20image.jpg');
    assert.equal(component.localCardImage({ media: [{ thumbnail_url: remote, download: {
        thumbnail_local_path: 'media/100/poster.jpg',
    } }] }, remote), '/media/100/poster.jpg');
    for (const local_path of ['https://example.com/a.jpg', '//example.com/a.jpg', 'media/../a.jpg']) {
        assert.equal(component.localCardImage({ media: [{ url: remote, download: { local_path } }] }, remote), null);
    }
});

test('link cards show archived large images and summary thumbnails in normalized quotes', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({ tweetApp })');
    const app = immediateComponent(tweetApp());
    const remote = 'https://example.com/preview.jpg';
    for (const name of ['summary', 'summary_large_image']) {
        const tweet = {
            raw_json: { card: { legacy: { name, binding_values: [
                { key: 'title', value: { string_value: 'A saved story' } },
                { key: 'thumbnail_image_original', value: { image_value: { url: remote } } },
            ] } } },
            card_media: [{ url: remote, download: { local_path: 'media/1/card image.jpg' } }],
            media: [],
        };
        for (const input of [tweet, { raw_json: tweet }]) {
            const html = app.renderCard(input);
            assert.match(html, /<img src="\/media\/1\/card%20image.jpg" alt=""/);
            assert.match(html, /A saved story/);
            assert.equal(html.includes('link-card-summary'), name === 'summary');
            assert.doesNotMatch(html, /src="https?:/);
        }
        tweet.card_media = [];
        assert.doesNotMatch(app.renderCard(tweet), /<img/);
        assert.match(app.renderCard(tweet), /A saved story/);
        tweet.card_media = [{ url: remote, download: { local_path: 'https://host/remote.jpg' } }];
        assert.doesNotMatch(app.renderCard(tweet), /<img/);
    }
});

test('custom font dropdown supports keyboard focus and selection', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({ tweetApp })');
    const component = immediateComponent(tweetApp());
    const choices = component.fontChoices();
    const options = choices.map(() => ({ focus() { context.document.activeElement = this; } }));
    const trigger = { focus() { context.document.activeElement = this; } };
    component.$refs = { fontOptions: { querySelectorAll: () => options }, fontTrigger: trigger };
    component.openFontPicker();
    assert.equal(component.fontPickerOpen, true);
    assert.equal(context.document.activeElement, options[0]);
    component.focusFontOption(-1);
    assert.equal(context.document.activeElement, options.at(-1));
    component.focusFontOption(1);
    assert.equal(context.document.activeElement, options[0]);
    component.focusFontOption('last');
    assert.equal(context.document.activeElement, options.at(-1));
    component.selectFont(choices.at(-1).key);
    assert.equal(component.fontPickerOpen, false);
    assert.equal(context.document.activeElement, trigger);
    assert.equal(component.selectedFontChoice().key, choices.at(-1).key);
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook/web/index.html'), 'utf8');
    assert.match(html, /Load local fonts/);
    assert.match(html, /:style="\{ fontFamily: f.family \}"/);
});

test('loading tagging settings never discovers provider models implicitly', async () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({ tweetApp })');
    const component = immediateComponent(tweetApp());
    let requests = 0;
    context.fetch = async url => {
        assert.equal(url, '/api/automated-tagging');
        requests++;
        return { ok: true, json: async () => ({ installed: true, values: {
            ...component.automatedTagging, api_key: 'test-key',
        }, accounting: {} }) };
    };
    let modelRequests = 0;
    component.loadAutomatedTaggingModels = async () => { modelRequests++; };
    await component.fetchAutomatedTagging();
    assert.equal(requests, 1);
    assert.equal(modelRequests, 0);
});

test('packaged page loads only same-origin runtime assets', () => {
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook/web/index.html'), 'utf8');
    const head = html.split('</head>')[0];
    assert.doesNotMatch(head, /(?:src|href)=["']https?:/);
    for (const match of head.matchAll(/(?:src|href)=["'](\/static\/[^"']+)/g)) {
        assert.ok(fs.existsSync(path.join(ROOT, 'tweetnook/web', match[1])), match[1]);
    }
    assert.doesNotMatch(fs.readFileSync(path.join(JS_DIR, 'themes.js'), 'utf8'), /fonts\.googleapis/);
});

test('first-run gating loads archive resources only after completion and preserves edited cookies during polling', async () => {
    const context = browserContext();
    let required = true, ready = false;
    context.fetch = async (url) => {
        assert.equal(url, '/api/setup');
        return { ok: true, async json() { return {
            required, completed: !required, web: {password_configured: !required},
            auth: {configured: false, verified: false, values: {auth_token: '', ct0: '', user_id: ''}},
            archive: {ready, uploaded: false},
        }; } };
    };
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    const calls = [];
    for (const method of ['fetchTweets', 'fetchStats', 'fetchGlobalTags', 'fetchArchiveEnrichmentStatus', 'fetchAutomatedTagging']) app[method] = async () => calls.push(method);
    let appliedDeepLinks = 0;
    app.splitPanel = false;
    app.pendingRoute = { viewMode: 'thread', tweetId: '123' };
    app.pendingRouteState = { viewMode: 'thread', tweetId: '123', tweetNookDepth: 0 };
    app.showFullThread = async tweetId => {
        assert.equal(tweetId, '123');
        appliedDeepLinks++;
    };
    await app.fetchSetup();
    assert.equal(app.setupForced, true);
    assert.equal(app.showSetupModal, true);
    assert.equal(app.showSettingsModal, false);
    app.closeSetup();
    assert.equal(app.showSetupModal, true);
    app.setupAuth.auth_token = 'edited';
    ready = true;
    await app.fetchSetup();
    assert.equal(app.setupAuth.auth_token, 'edited');
    assert.deepEqual(calls, []);
    required = false;
    await app.fetchSetup();
    assert.deepEqual(calls, ['fetchTweets', 'fetchStats', 'fetchArchiveEnrichmentStatus', 'fetchAutomatedTagging']);
    assert.equal(appliedDeepLinks, 1);
    await app.fetchSetup();
    assert.equal(calls.length, 4);
    assert.equal(appliedDeepLinks, 1);
    const before = JSON.stringify(app.setupData);
    app.rerunSetup();
    assert.equal(app.setupFlowStep, 1);
    assert.equal(app.setupRerun, true);
    assert.equal(app.settingsTab, 'setup');
    assert.equal(app.showSettingsModal, false);
    assert.equal(app.showSetupModal, true);
    assert.equal(JSON.stringify(app.setupData), before);
    app.closeSetup();
    assert.equal(app.showSetupModal, false);
});

test('a linked conversation opens without waiting for the feed and counters; a list waits for its feed', async () => {
    const setup = { required: false, completed: true, web: {password_configured: true},
        auth: {configured: true, verified: true, values: {auth_token: '', ct0: '', user_id: ''}},
        archive: {ready: true, uploaded: false} };
    const load = route => {
        const context = browserContext();
        context.fetch = async () => ({ ok: true, json: async () => setup });
        const app = immediateComponent(loadScripts(context, ['themes.js', 'app.js'], 'tweetApp')());
        const pending = {};
        for (const method of ['fetchTweets', 'fetchStats', 'fetchArchiveEnrichmentStatus', 'fetchAutomatedTagging']) {
            app[method] = () => new Promise(resolve => { pending[method] = resolve; });
        }
        const applied = [];
        app.applyRoute = async next => { applied.push(app.routeKey(next)); };
        app.activeRoute = route;
        app.pendingRoute = route;
        app.pendingRouteState = { ...route, tweetNookDepth: 0 };
        return { app, pending, applied, setupDone: app.fetchSetup() };
    };
    const flush = () => new Promise(resolve => setImmediate(resolve));

    const thread = load({ viewMode: 'thread', tweetId: '123' });
    await flush();
    assert.deepEqual(thread.applied, ['thread:123']);
    assert.equal(thread.app.pendingRoute, null);
    Object.values(thread.pending).forEach(resolve => resolve());
    await thread.setupDone;
    assert.deepEqual(thread.applied, ['thread:123']);

    const list = load({ viewMode: 'list' });
    await flush();
    assert.deepEqual(list.applied, [], 'the list restores scroll only once its tweets are in');
    list.pending.fetchTweets();
    await flush();
    assert.deepEqual(list.applied, ['list']);
    Object.values(list.pending).forEach(resolve => resolve());
    await list.setupDone;

    const moved = load({ viewMode: 'list' });
    await flush();
    moved.app.activeRoute = { viewMode: 'thread', tweetId: '9' };
    moved.pending.fetchTweets();
    Object.values(moved.pending).forEach(resolve => resolve());
    await moved.setupDone;
    assert.deepEqual(moved.applied, [], 'a stale list route never replaces one opened meanwhile');
});

test('notification badge counts every unread item and actions use Settings destinations', async () => {
    const context = browserContext();
    const requests = [];
    context.fetch = async (url, options) => { requests.push([url, options.method]); return {ok: true}; };
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js', 'settings.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.notices = [
        {id: 'a', severity: 'error', dismissed: false, action: 'setup'},
        {id: 'b', severity: 'warning', dismissed: false},
        {id: 'c', severity: 'info', dismissed: false},
        {id: 'd', severity: 'error', dismissed: true},
    ];
    assert.equal(app.noticeCount, 3);
    await app.dismissNotice('a');
    assert.equal(app.noticeCount, 2);
    assert.deepEqual(requests, [['/api/notices/a/dismiss', 'POST']]);
    app.openNotice(app.notices[0]);
    assert.equal(app.showSetupModal, true);
    assert.equal(app.showSettingsModal, false);
    app.fetchScheduleSettings = () => { requests.push(['schedule-refresh', 'GET']); };
    app.openNotice({action: 'schedule'});
    assert.equal(app.settingsTab, 'schedule');
    app.openNotice({action: 'activity'});
    assert.equal(app.activityDrawerOpen, true);
});

test('notices link to their run log and support bulk dismiss and clearing history', async () => {
    const context = browserContext();
    const requests = [];
    context.fetch = async (url, options) => { requests.push([url, options?.method]); return {ok: true, json: async () => ({runs: []})}; };
    const { tweetApp, nookNoticeWhen } = loadScripts(context, ['themes.js', 'app.js', 'settings.js'], '({tweetApp, nookNoticeWhen})');
    const app = immediateComponent(tweetApp());
    app.openSettings();
    app.notices = [
        {id: 'a', dismissed: false, action: 'activity', run_id: 'run-a'},
        {id: 'b', dismissed: false, action: 'setup', run_id: 'run-b'},
        {id: 'c', dismissed: true, action: 'activity'},
    ];

    app.openNotice(app.notices[0]);
    assert.equal(app.settingsTab, 'logs');
    assert.equal(app.nookLogOpenRunId, 'run-a');
    assert.equal(app.activityDrawerOpen, false);
    app.openNoticeLog(app.notices[1]);
    assert.equal(app.nookLogOpenRunId, 'run-b');

    await app.dismissAllNotices();
    assert.equal(app.noticeCount, 0);
    await app.clearDismissedNotices();
    assert.deepEqual(app.notices, []);
    assert.deepEqual(requests.filter(([url]) => url.startsWith('/api/notices')), [
        ['/api/notices/dismiss-all', 'POST'],
        ['/api/notices/dismissed', 'DELETE'],
    ]);

    assert.equal(nookNoticeWhen({}), '');
    assert.match(nookNoticeWhen({created_at: new Date().toISOString()}), /^Today, /);
});

test('notification list shows times, run logs, and bulk actions', () => {
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook/web/index.html'), 'utf8');
    assert.match(html, /x-show="noticeCount > 1" @click="dismissAllNotices\(\)">Dismiss all</);
    assert.match(html, /@click="clearDismissedNotices\(\)">Clear history</);
    assert.match(html, /class="notice-time" x-show="nookNoticeWhen\(notice\)"/);
    assert.match(html, /x-show="notice.action !== 'activity' && notice.run_id" @click="openNoticeLog\(notice\)">View log</);
});

test('log formatting does not mark zero-failure summaries as errors', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const component = immediateComponent(tweetApp());

    const formatted = component.formatLogText(
        'media: 30 downloaded, 0 failed\nmedia: 29 downloaded, 1 failed\nsync follow-up tagging failed',
    );

    assert.doesNotMatch(formatted.split('\n')[0], /var\(--destructive-color\)/);
    assert.match(formatted.split('\n')[1], /var\(--destructive-color\)/);
    assert.match(formatted.split('\n')[2], /var\(--destructive-color\)/);
});

test('notifications live in Settings with unread counts', () => {
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook/web/index.html'), 'utf8');
    const settingsJs = fs.readFileSync(path.join(JS_DIR, 'settings.js'), 'utf8');
    assert.match(html, /id="nook-nav-notifications" :aria-label="noticeCount \? 'Notifications, ' \+ noticeCount \+ ' unread' : 'Notifications'"/);
    assert.match(html, /@click="nookSettingsNavigate\(\$data, 'notifications'\)"/);
    assert.match(settingsJs, /if \(key === 'notifications'\) app\.fetchNotices\(\);/);
    assert.match(settingsJs, /notifications:'Notifications'/);
    assert.match(html, /Settings, \$\{noticeCount\} unread notifications/);
    assert.match(html, /class="nook-nav-count" aria-hidden="true" x-text="noticeCount"/);
    assert.match(html, /class="nook-notice-count" aria-hidden="true" x-text="noticeCount > 9 \? '9\+' : noticeCount"/);
});

test('Settings and Setup stay above an expanded activity drawer', () => {
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook/web/index.html'), 'utf8');
    const headerCss = fs.readFileSync(path.join(ROOT, 'tweetnook/web/static/css/header.css'), 'utf8');
    const settingsCss = fs.readFileSync(path.join(ROOT, 'tweetnook/web/static/css/settings.css'), 'utf8');
    const zIndex = (css, selector) => Number(css.match(new RegExp(`${selector.replace('.', '\\.')}\\s*\\{[^}]*z-index:\\s*(\\d+)`))?.[1]);

    assert.match(html, /class="settings-modal-backdrop nook-settings-backdrop"/);
    assert.match(html, /class="settings-modal-backdrop fixed inset-0 z-\[100\]/);
    assert.equal(zIndex(settingsCss, '.nook-settings-backdrop'), 100);
    assert.ok(zIndex(headerCss, '.nook-sync-panel') < 100);
});

test('activity drawer animation isolates layout and transitions only geometry', () => {
    const css = fs.readFileSync(path.join(ROOT, 'tweetnook/web/static/css/styles.css'), 'utf8');
    const drawerRule = css.match(/\.activity-drawer\s*\{([^}]*)\}/)?.[1] || '';
    const readyRule = css.match(/\.activity-drawer\.activity-drawer-ready\s*\{([^}]*)\}/)?.[1] || '';

    assert.match(drawerRule, /contain: layout paint style;/);
    assert.match(readyRule, /will-change: width, height, right, bottom, border-radius;/);
    assert.doesNotMatch(readyRule, /transition:\s*all\b/);
    for (const property of ['width', 'height', 'right', 'bottom', 'border-radius']) {
        assert.match(readyRule, new RegExp(`(?:transition:|,)\\s*${property} 0\\.35s`));
    }
});

test('tweet bylines size dates from the visible label and truncate handles when needed', () => {
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook/web/index.html'), 'utf8');
    const css = fs.readFileSync(path.join(ROOT, 'tweetnook/web/static/css/styles.css'), 'utf8');

    assert.match(css, /\.tweet-byline-name\s*\{[^}]*min-width:\s*0;[^}]*overflow:\s*hidden;[^}]*text-overflow:\s*ellipsis;[^}]*white-space:\s*nowrap;/s);
    assert.match(css, /\.tweet-byline-detail\s*\{[^}]*flex-shrink:\s*0;[^}]*white-space:\s*nowrap;/s);
    assert.match(css, /\.tweet-byline \.roll-item:nth-child\(2\)\s*\{[^}]*position:\s*absolute;[^}]*top:\s*20px;/s);
    assert.match(css, /\.tweet-byline \.roll-wrapper:hover \.roll-item:nth-child\(2\)\s*\{[^}]*position:\s*static;/s);
    assert.match(css, /\.tweet-byline \.roll-wrapper:hover\s*\{[^}]*max-width:\s*100%;/s);
    assert.match(css, /\.tweet-byline-handle\s*\{[^}]*flex-shrink:\s*1;[^}]*min-width:\s*0;[^}]*overflow:\s*hidden;[^}]*text-overflow:\s*ellipsis;/s);
    assert.match(html, /class="[^"]*tweet-byline-name[^"]*"[^>]*x-text="parent\.author\.display_name/);
    assert.match(html, /tweet-byline-detail tweet-byline-handle" x-text="'@' \+ \(parent\.author\.username/);
    assert.match(html, /class="roll-wrapper[^"]*tweet-byline-detail[^"]*"/);
    assert.match(html, /class="[^"]*tweet-byline-detail[^"]*" x-text="formatDate\(threadData\.main\.created_at\)"/);
    const { app } = feedListApp();
    const card = parseFakeHTML(app.renderFeedCard({ tweet_id: '1', text: 'x', author: { id: '2', username: 'a', display_name: 'A' } }))[0];
    assert.equal(card.querySelector('.tweet-byline-name').textContent, 'A');
    assert.equal(card.querySelector('.tweet-byline-detail').textContent, '@a');
    assert.ok(card.querySelector('.tweet-byline-handle'));
    assert.ok(card.querySelector('.roll-wrapper').classList.contains('tweet-byline-detail'));
});

test('sync, analytics, and settings live in the top bar on every screen size', () => {
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook/web/index.html'), 'utf8');
    const utilities = html.slice(html.indexOf('<div class="nook-utilities">'), html.indexOf('<nav class="nook-tabs"'));

    assert.match(utilities, /class="nook-icon-button nook-activity-button" @click="nookToggleActivity\(\$data, \$el\)" aria-controls="nook-activity-drawer"/);
    assert.match(utilities, /@click="openStatsModal\(\)" aria-label="Analytics"/);
    assert.match(utilities, /@click="openSettings\(\)"/);
    assert.doesNotMatch(utilities, /hidden sm:flex/);
    assert.doesNotMatch(html, /<!-- Stats Button -->|<!-- Settings Button -->/);
    assert.match(html, /<section id="nook-activity-drawer" class="nook-sync-panel"/);
});

test('Settings tabs use the requested order and Appearance is the default', () => {
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook/web/index.html'), 'utf8');
    const js = fs.readFileSync(path.join(JS_DIR, 'app.js'), 'utf8');
    const keys = ['appearance', 'schedule', 'tags', 'automated-tagging', 'notifications', 'config', 'logs', 'setup'];
    const positions = keys.map(key => html.indexOf(`id="nook-nav-${key}"`));
    assert.ok(positions.every(index => index >= 0));
    assert.deepEqual(positions, [...positions].sort((a, b) => a - b));
    for (const key of keys) assert.match(html, new RegExp(`@click="nookSettingsNavigate\\(\\$data, '${key}'\\)"`));
    assert.match(js, /settingsTab: 'appearance'/);
    assert.match(js, /rerunSetup\(\)[\s\S]*?this\.settingsTab = 'setup'/);
});

test('setup password finalization reloads with a one-time completion marker after success', async () => {
    const context = browserContext();
    let reloaded = false;
    context.window.location = {reload() { reloaded = true; }};
    context.fetch = async (url, options) => {
        assert.equal(url, '/api/setup/complete');
        assert.deepEqual(JSON.parse(options.body), {password: 'test-secret', confirm_password: 'test-secret', skip_auth: true});
        return {ok: true, async json() {return {completed: true, reload: true};}};
    };
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.setupForced = true;
    app.setupSkipAuth = true;
    app.setupPassword = app.setupPasswordConfirm = 'test-secret';
    app.setupData = {web: {password_configured: false}};
    await app.finishSetup();
    assert.equal(reloaded, true);
    assert.equal(app.setupForced, false);
    assert.equal(app.setupPassword, '');
    assert.equal(context.sessionStorage.getItem('tweetnook.setup-completion'), 'pending');
    assert.equal(app.setupCompletionVisible, true);
    assert.equal(app.setupFlowStep, 1, 'the explanation is separate from the counted steps');
});

test('setup completion survives the password reload once and supports both real handoffs', async () => {
    const context = browserContext();
    const data = { completed: true, required: false, auth: { verified: false, values: {} }, archive: { ready: true, initialization_mode: 'empty' } };
    context.fetch = async () => ({ ok: true, json: async () => data });
    const { tweetApp } = loadScripts(context, ['themes.js', 'setup-completion.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.archiveResourcesLoaded = true;
    context.sessionStorage.setItem('tweetnook.setup-completion', 'pending');
    await app.fetchSetup();
    assert.equal(app.setupCompletionVisible, true);
    assert.equal(app.showSetupModal, true);
    assert.equal(app.setupForced, false);
    assert.equal(context.sessionStorage.getItem('tweetnook.setup-completion'), null);
    app.setupCompletionAction();
    assert.equal(app.setupCompletionVisible, false);
    assert.equal(app.setupFlowStep, 2);
    assert.equal(app.setupRerun, true);
    app.closeSetup();
    await app.fetchSetup();
    assert.equal(app.showSetupModal, false, 'polling must not reopen a dismissed completion page');
    data.auth.verified = true;
    app.showSetupCompletion();
    app.setupCompletionAction();
    assert.equal(app.activityDrawerOpen, true);
    assert.equal(app.showSetupModal, false);
    assert.equal(app.setupCompletionVisible, false);
});

test('failed setup completion neither reloads nor queues the explanation', async () => {
    const context = browserContext();
    context.fetch = async () => ({ ok: false, json: async () => ({ detail: 'Cannot finish' }) });
    let reloads = 0;
    context.window.location.reload = () => reloads++;
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.setupData = { web: { password_configured: true } };
    app.setupForced = true;
    await app.finishSetup();
    assert.equal(app.setupError, 'Cannot finish');
    assert.equal(app.setupCompletionVisible, false);
    assert.equal(app.setupForced, true);
    assert.equal(reloads, 0);
    assert.equal(context.sessionStorage.getItem('tweetnook.setup-completion'), null);
});

test('setup still shows completion before reloading when browser storage is disabled', async () => {
    const context = browserContext();
    context.sessionStorage.setItem = () => { throw new Error('Storage disabled'); };
    context.fetch = async () => ({ ok: true, json: async () => ({ completed: true, reload: true }) });
    let reloads = 0;
    context.window.location.reload = () => reloads++;
    const { tweetApp } = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.setupData = { web: { password_configured: true } };
    await app.finishSetup();
    assert.equal(app.setupCompletionVisible, true);
    assert.equal(reloads, 0);
    app.closeSetup();
    assert.equal(reloads, 1);
});

test('setup completion explains actual import, enrichment, sync and skipped steps', () => {
    const context = browserContext();
    const { nookSetupCompletion } = loadScripts(context, ['setup-completion.js'], '({nookSetupCompletion})');
    const describe = (archive, extra = {}) => nookSetupCompletion({ setupData: {
        archive, auth: { verified: true }, schedule: { enabled: false, collections: ['bookmarks', 'likes'] },
    }, ...extra });
    const pending = { initialization_mode: 'import', initialization_state: 'complete', imported: true, enrichment_state: 'running' };
    const running = describe(pending);
    assert.equal(running.stages.map(stage => stage.state).join(','), 'done,active,queued');
    assert.match(running.note, /sync follows automatically/);
    assert.match(running.time, /hours or potentially days.*limits/);
    assert.match(running.usability, /until the first sync finishes/);
    assert.match(running.stages[1].icon, /data-icon="search"/);
    const syncing = describe(pending, { activity: { kind: 'enrich', steps: [{ key: 'archive-enrich', state: 'complete' }] } });
    assert.equal(syncing.stages.map(stage => stage.state).join(','), 'done,done,active');
    const failed = describe(pending, { lastActivity: { kind: 'enrich', success: false, steps: [{ key: 'archive-enrich', state: 'complete' }] } });
    assert.equal(failed.stages.at(-1).state, 'attention');
    assert.doesNotMatch(failed.note, /is complete/);
    const done = describe({ ...pending, enrichment_state: 'complete', enriched: true }, { lastActivity: { kind: 'enrich', success: true } });
    assert.equal(done.stages.at(-1).state, 'done');
    const detailsOnly = describe({ ...pending, enriched: false, details_complete: true,
        followup_state: 'failed', enrichment_state: 'failed', warnings: ['Storage is full. Free disk space, then resume.'],
    }, { lastActivity: {title: 'tweetnook import enrich', success: false} });
    assert.equal(detailsOnly.stages[1].state, 'done');
    assert.equal(detailsOnly.stages[2].state, 'attention');
    assert.match(detailsOnly.note, /Storage is full/);
    const importing = describe({ ...pending, initialization_state: 'running', enrichment_state: 'waiting_for_import' }, { lastActivity: { kind: 'enrich', success: true, steps: [{ key: 'archive-enrich', state: 'complete' }] } });
    assert.equal(importing.stages.map(stage => stage.state).join(','), 'active,queued,queued', 'an older success must not complete a new import');
    const importFailed = describe({ ...pending, initialization_state: 'failed' });
    assert.equal(importFailed.stages[0].state, 'attention');
    const offline = describe({}, { setupData: { auth: { verified: false }, archive: pending } });
    assert.equal(offline.needsConnection, true);
    assert.equal(offline.stages[1].state, 'connection');
    const manual = describe({ initialization_mode: 'empty' });
    assert.equal(manual.stages.length, 1);
    assert.equal(manual.stages[0].label, 'Not started');
    assert.match(manual.note, /Nothing is running/);
    const scheduled = describe({}, { setupData: { auth: { verified: true }, archive: { initialization_mode: 'empty' }, schedule: { enabled: true, collections: ['tweets'] } } });
    assert.equal(scheduled.stages[0].label, 'Scheduled');
    assert.match(scheduled.stages[0].description, /authored posts/);
    assert.doesNotMatch(scheduled.stages[0].description, /bookmarks/);
    const migration = describe({ initialization_mode: 'migration', initialization_state: 'complete' });
    assert.equal(migration.stages.map(stage => stage.title).join(','), 'Migration,First sync');
});

test('automated tagging saves uncertainty policies and exposes their controls', async () => {
    const context = browserContext();
    const values = {
        enabled: true, api_mode: 'paid', api_key: '********', tagging_context: [],
        daily_spend_limit_usd: 0.1, unknown_billing_policy: 'stop',
        unknown_request_reserve_usd: 0.03, max_unknown_requests_per_day: 2,
        search_unknown_policy: 'reserve', max_unknown_search_requests: 4,
    };
    let submitted;
    context.fetch = async (_url, options) => {
        submitted = JSON.parse(options.body).values;
        return {ok: true, async json() {return {values: submitted};}};
    };
    const {tweetApp} = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.automatedTagging = {...values};
    await app.saveAutomatedTagging();
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook/web/index.html'), 'utf8');
    for (const field of [
        'unknown_billing_policy', 'unknown_request_reserve_usd',
        'max_unknown_requests_per_day', 'search_unknown_policy', 'max_unknown_search_requests',
    ]) {
        assert.equal(submitted[field], values[field]);
        assert.equal(app.automatedTagging[field], values[field]);
        assert.ok(html.includes(`automatedTagging.${field}"`));
    }
    assert.ok(html.includes('actual charges may differ'));
    assert.ok(html.includes('Five is not a provider limit'));
});

test('abbreviated raw tweet JSON renders exactly like the stored document', () => {
    const fixture = JSON.parse(fs.readFileSync(
        path.join(ROOT, 'tests', 'fixtures', 'web_raw_tweets.json'),
        'utf8',
    ));
    const app = immediateComponent(loadScripts(browserContext(), ['themes.js', 'app.js'], 'tweetApp')());
    const event = () => ({
        currentTarget: {
            classList: { contains: () => true },
            closest: () => null,
            getBoundingClientRect: () => ({ left: 0, top: 0, bottom: 0 }),
        },
    });
    const profile = (tweet, isQuote) => {
        app.openProfileCard(event(), tweet, isQuote);
        const { x, y, request, ...card } = app.profileCard;
        app.closeProfileCard();
        return card;
    };
    const render = source => {
        const tweetObj = JSON.parse(JSON.stringify(source));
        const retweet = app.getRetweet(tweetObj);
        const tweet = retweet ? app.formatRetweetToTweet(tweetObj, retweet) : tweetObj;
        const quote = app.getQuoteTweet(tweet);
        const copy = () => JSON.parse(JSON.stringify(tweet));
        return JSON.stringify([
            Boolean(retweet), app.getTweetId(tweet), app.formatText(tweet), app.formatText(tweet, true),
            app.renderMediaGrid(tweet.media), app.renderCard(tweet), app.getReplyTo(tweet),
            app.renderCommunityNote(tweet.raw_json), app.renderActionBar(copy()),
            app.renderActionBar(copy(), true), app.getInitial(tweet), app.formatDate(tweet.created_at),
            app.isTextPlaceholderTweet(tweet), profile(tweet, false),
            quote && [
                app.isTombstone(quote), app.renderQuotePlaceholder(quote), app.getQuoteAuthor(quote),
                app.getTweetId(quote), app.formatDate(quote?.legacy?.created_at || quote?.created_at),
                app.getReplyTo({ raw_json: quote }), app.formatText(quote),
                app.renderCard({ raw_json: quote }), app.renderCommunityNote(quote, true),
                profile(quote, true),
            ],
        ]);
    };

    assert.equal(fixture.full.length, fixture.slim.length);
    fixture.full.forEach((tweet, index) => {
        assert.ok(JSON.stringify(fixture.slim[index]).length < JSON.stringify(tweet).length);
        assert.equal(render(fixture.slim[index]), render(tweet), `tweet ${index}`);
    });
});

test('copy raw prefers the stored document prefetched when the menu opens', async () => {
    const context = browserContext();
    const requests = [];
    let copiedText = null;
    context.fetch = async url => {
        requests.push(url);
        return { ok: true, json: async () => ({ raw_json: { rest_id: '7', full: true } }) };
    };
    context.navigator = { clipboard: { async writeText(value) { copiedText = value; } } };
    const app = immediateComponent(loadScripts(context, ['themes.js', 'app.js'], 'tweetApp')());

    app.toggleTweetMenu('list:7');
    await app.fullRawTweet({ tweet_id: '7' });
    await app.copyRawTweet({ tweet_id: '7', raw_json: { rest_id: '7' } });

    assert.deepEqual(requests, ['/api/tweets/7/raw']);
    assert.equal(JSON.parse(copiedText).full, true);

    context.fetch = async () => ({ ok: false, json: async () => ({}) });
    await app.copyRawTweet({ tweet_id: '8', raw_json: { rest_id: '8', abbreviated: true } });
    assert.equal(JSON.parse(copiedText).abbreviated, true);
});

test('large threads render replies in batches of nested tweets', async () => {
    const context = browserContext('/post/1');
    const app = immediateComponent(loadScripts(context, ['themes.js', 'app.js'], 'tweetApp')());
    const child = (id, nested) => ({
        tweet_id: id,
        op_replies: Array.from({ length: nested }, (_, index) => ({ tweet_id: `${id}-${index}` })),
    });
    const children = [child('a', 20), child('b', 5), child('c', 0), child('d', 40)];

    assert.deepEqual(Array.from(app.visibleReplies(children, 30), item => item.tweet_id), ['a', 'b', 'c']);
    assert.deepEqual(Array.from(app.visibleReplies([child('big', 99)], 30), item => item.tweet_id), ['big']);
    assert.equal(app.hasHiddenReplies(children, 30), true);
    app.showMoreReplies();
    assert.equal(app.hasHiddenReplies(children, app.threadReplyLimit), true);
    app.showMoreReplies();
    assert.equal(app.hasHiddenReplies(children, app.threadReplyLimit), false);
    app.showMoreReplies(true);
    assert.equal(app.panelReplyLimit, 60);

    app.activeRoute = { viewMode: 'thread', tweetId: '1' };
    app.loadThread = async () => ({ main: { tweet_id: '1' }, parents: [], children });
    await app.showFullThread('1');
    assert.equal(app.threadReplyLimit, 30);

    const html = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'index.html'), 'utf8');
    assert.ok(html.includes('x-for="child in visibleReplies(threadReplies(threadData, threadOtherReplies), threadReplyLimit)"'));
    assert.ok(html.includes('x-for="child in visibleReplies(threadReplies(panelThreadData, panelOtherReplies), panelReplyLimit)"'));
    assert.ok(html.includes('x-intersect="showMoreReplies()"'));
    assert.ok(html.includes('x-intersect="showMoreReplies(true)"'));
});

test('mention-only replies wait behind Show other replies until regular replies are shown', async () => {
    const context = browserContext('/post/1');
    const app = immediateComponent(loadScripts(context, ['themes.js', 'app.js'], 'tweetApp')());
    const reply = id => ({ tweet_id: id, op_replies: [] });
    const data = {
        main: { tweet_id: '1' },
        parents: [],
        children: Array.from({ length: 31 }, (_, index) => reply(`r${index}`)),
        other_children: [reply('spam1'), reply('spam2')],
    };
    const ids = list => Array.from(list, item => item.tweet_id);

    assert.deepEqual(ids(app.threadReplies(data, false)), ids(data.children));
    assert.equal(app.firstOtherReplyId(data, false), null);
    assert.equal(app.firstOtherReplyId(data, true), 'spam1');
    assert.equal(app.hasOtherReplies(data, app.threadReplyLimit, app.threadOtherReplies), false);
    app.showMoreReplies();
    assert.equal(app.hasOtherReplies(data, app.threadReplyLimit, app.threadOtherReplies), true);

    app.showOtherReplies();
    assert.equal(app.threadOtherReplies, true);
    assert.equal(app.hasOtherReplies(data, app.threadReplyLimit, app.threadOtherReplies), false);
    assert.deepEqual(
        ids(app.visibleReplies(app.threadReplies(data, app.threadOtherReplies), app.threadReplyLimit)).slice(-2),
        ['spam1', 'spam2'],
    );
    assert.equal(app.hasOtherReplies({ children: [], other_children: [] }, 30, false), false);
    assert.deepEqual(ids(app.threadReplies({ children: [reply('a')] }, true)), ['a']);

    app.activeRoute = { viewMode: 'thread', tweetId: '1' };
    app.loadThread = async () => data;
    await app.showFullThread('1');
    assert.equal(app.threadOtherReplies, false);

    app.showOtherReplies(true);
    assert.equal(app.panelOtherReplies, true);
    assert.equal(app.panelReplyLimit, 60);

    const html = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'index.html'), 'utf8');
    assert.ok(html.includes('x-show="hasOtherReplies(threadData, threadReplyLimit, threadOtherReplies)"'));
    assert.ok(html.includes('x-show="hasOtherReplies(panelThreadData, panelReplyLimit, panelOtherReplies)"'));
    assert.equal(html.split('>Show other replies</button>').length - 1, 2);
    assert.equal(html.split('font-extrabold text-[var(--text-primary)]">Other replies</h2>').length - 1, 2);
    assert.ok(html.includes('x-if="child.tweet_id === firstOtherReplyId(threadData, threadOtherReplies)"'));
});

test('the full tag list loads only when Tag Management opens', () => {
    const context = browserContext();
    const app = immediateComponent(loadScripts(context, ['themes.js', 'app.js'], 'tweetApp')());
    const watchers = {};
    let loads = 0;
    app.$watch = (key, callback) => { watchers[key] = callback; };
    for (const method of ['fetchSetup', 'fetchNotices', 'fetchActivityStatus', 'fetchConfig', 'fetchScheduleSettings', 'fetchActivityRuns', 'fetchAutomatedTagging']) {
        app[method] = async () => {};
    }
    app.fetchGlobalTags = async () => { loads++; };
    app.initApp();

    watchers.settingsTab('tags');
    assert.equal(loads, 0);
    app.showSettingsModal = true;
    watchers.settingsTab('appearance');
    watchers.settingsTab('tags');
    assert.equal(loads, 1);
    app.settingsTab = 'tags';
    watchers.showSettingsModal(true);
    assert.equal(loads, 2);
});

test('threads open on the tweet already shown while the conversation loads', async () => {
    const context = browserContext();
    const pending = [];
    context.fetch = url => new Promise(resolve => pending.push({ url, resolve }));
    const app = immediateComponent(loadScripts(context, ['themes.js', 'app.js'], 'tweetApp')());
    app.splitPanel = false;
    const feedTweet = {
        tweet_id: '5', text: 'Shown in the feed', author: { id: '1', username: 'a', display_name: 'A' },
        quoted_tweet: { tweet_id: '6', text: 'Quoted', author: { id: '2', username: 'b', display_name: 'B' } },
    };
    app.tweets = [feedTweet];
    app.activeRoute = { viewMode: 'thread', tweetId: '5' };

    const opening = app.showFullThread('5');
    assert.equal(app.loadingThread, false);
    assert.equal(app.threadData.partial, true);
    assert.equal(app.threadData.main.text, 'Shown in the feed');
    assert.equal(pending[0].url, '/api/tweets/5');
    pending[0].resolve({ ok: true, json: async () => ({ main: { tweet_id: '5', text: 'Full' }, parents: [], children: [] }) });
    await opening;
    assert.equal(app.threadData.partial, undefined);
    assert.equal(app.threadData.main.text, 'Full');

    assert.equal(app.threadSeed('6').main.text, 'Quoted');
    assert.equal(app.threadSeed('404'), null);
    app.panelMode = 'thread';
    app.panelStack = [{ routeKey: 'thread:6' }];
    app.panelThreadData = app.threadSeed('6');
    app.savePanelSnapshot();
    assert.equal(app.panelStack[0].data, null);
});

test('thread main anchoring ignores hidden content and preserves visible content on page and panel', () => {
    const context = browserContext();
    const pageScrolls = [];
    context.window.scrollBy = (...args) => pageScrolls.push(args);
    const app = immediateComponent(loadScripts(context, ['themes.js', 'app.js'], 'tweetApp')());

    for (const panel of [false, true]) {
        let visible = false;
        let top = 0;
        const main = {
            getClientRects: () => visible ? [{}] : [],
            getBoundingClientRect: () => ({ top, width: visible ? 598 : 0, height: visible ? 373 : 0 }),
        };
        const root = { scrollTop: 0, querySelector: () => main };
        if (panel) app.$refs.detailPanel = root;
        else context.document.querySelector = () => main;

        app.keepThreadMainInPlace(panel, () => {
            visible = true;
            top = 69;
        });
        assert.equal(panel ? root.scrollTop : pageScrolls.length, 0, 'newly rendered content must not cause an anchor adjustment');

        top = 100;
        app.keepThreadMainInPlace(panel, () => { top = 160; });
        if (panel) assert.equal(root.scrollTop, 60);
        else assert.deepEqual(pageScrolls, [[0, 60]]);
    }
});

test('pressing or hovering a tweet starts loading its conversation once', () => {
    const context = browserContext();
    const requests = [];
    const timers = [];
    context.fetch = url => {
        requests.push(url);
        return new Promise(() => {});
    };
    context.setTimeout = (callback, delay) => timers.push({ callback, delay });
    context.clearTimeout = () => {};
    const app = immediateComponent(loadScripts(context, ['themes.js', 'app.js'], 'tweetApp')());
    app.fetchSetup = async () => {};
    app.fetchNotices = async () => {};
    app.fetchActivityStatus = async () => {};
    app.initApp();
    const target = id => ({ target: { closest: selector => (selector === '[data-thread-id]' ? { dataset: { threadId: id } } : null) } });

    context.__state.events.get('document:pointerdown')(target('7'));
    context.__state.events.get('document:mouseover')(target('7'));
    timers.filter(timer => timer.delay === 150).forEach(timer => timer.callback());
    context.__state.events.get('document:pointerdown')(target('not-a-tweet'));

    assert.deepEqual(requests.filter(url => url.startsWith('/api/tweets/')), ['/api/tweets/7']);
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'index.html'), 'utf8');
    assert.equal((html.match(/:data-thread-id="/g) || []).length, (html.match(/="openThread\(/g) || []).length);
    assert.ok(html.includes('data-thread-main="page"') && html.includes('data-thread-main="panel"'));
});

test('the page requests the first feed page and a linked conversation before its scripts load', async () => {
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'index.html'), 'utf8');
    const startup = /<script>([\s\S]*?)<\/script>/.exec(html);
    assert.ok(startup && startup.index < html.indexOf('<link rel="stylesheet"'), 'runs before any stylesheet or script');
    const run = (pathname, demo = false) => {
        const context = browserContext(pathname);
        const requests = [];
        context.fetch = url => { requests.push(url); return new Promise(() => {}); };
        context.window.TWEETNOOK_DEMO = demo;
        vm.runInContext(startup[1], context);
        return { context, requests };
    };
    const { context, requests } = run('/post/42');
    const app = immediateComponent(loadScripts(context, ['themes.js', 'app.js'], 'tweetApp')());
    assert.deepEqual(requests, [`${app.feedUrl(1)}&cursor=`, '/api/tweets/42'], 'matches the app\'s first page');
    assert.deepEqual(run('/post/42/quotes').requests, [`${app.feedUrl(1)}&cursor=`]);
    assert.deepEqual(run('/', true).requests, [], 'the static demo has no API');

    // The app adopts each early request once instead of asking again.
    app.requestFeedPage(`${app.feedUrl(1)}&cursor=`, { speculative: true });
    app.loadThread('42').catch(() => {});
    assert.equal(requests.length, 2);
    assert.deepEqual(Object.keys(context.window.TWEETNOOK_STARTUP), []);
    app.threadCache.clear();
    app.loadThread('42').catch(() => {});
    assert.equal(requests.at(-1), '/api/tweets/42');
    assert.equal(requests.length, 3);
});

test('a first feed page is shown without waiting for Setup status', async () => {
    const start = async status => {
        const context = browserContext();
        context.fetch = url => (url.startsWith('/api/tweets?')
            ? Promise.resolve({ ok: status === 200, status, json: async () => ({ tweets: [] }) })
            : new Promise(() => {}));
        const app = immediateComponent(loadScripts(context, ['themes.js', 'app.js'], 'tweetApp')());
        app.fetchNotices = async () => {};
        app.fetchActivityStatus = async () => {};
        let loads = 0;
        app.loadArchiveResources = async () => { loads++; };
        app.initApp();
        await new Promise(resolve => setImmediate(resolve));
        return { app, loads };
    };
    const ready = await start(200);
    assert.equal(ready.app.archiveReady, true);
    assert.equal(ready.app.setupForced, false);
    assert.equal(ready.loads, 1);
    const gated = await start(409);
    assert.notEqual(gated.app.archiveReady, true, 'Setup status still decides when the server refuses');
    assert.equal(gated.loads, 0);
});

test('feeds reuse recent first pages and keep the old list while loading', async () => {
    const context = browserContext();
    const requests = [];
    const pending = new Map();
    context.fetch = url => {
        requests.push(url);
        return new Promise(resolve => pending.set(url, data => resolve({ ok: true, status: 200, json: async () => data })));
    };
    let idle = null;
    context.requestIdleCallback = callback => { idle = callback; };
    const app = immediateComponent(loadScripts(context, ['themes.js', 'app.js'], 'tweetApp')());
    const page = ids => ({ tweets: ids.map(tweet_id => ({ tweet_id })), page: 1, pages: 1, total: ids.length, has_more: false });
    const likes = '/api/tweets?collection=likes&sort=liked_latest&page=1&cursor=';
    const all = '/api/tweets?collection=all&sort=newest&page=1&cursor=';

    // A startup request made alongside setup is reused by the first load.
    app.requestFeedPage(likes, { speculative: true });
    const first = app.fetchTweets();
    pending.get(likes)(page(['L1']));
    await first;
    assert.deepEqual(requests, [likes]);
    assert.deepEqual(Array.from(app.tweets, tweet => tweet.tweet_id), ['L1']);

    // Idle time fetches the other collections' first pages once.
    idle();
    assert.deepEqual(requests.slice(1), [all,
        '/api/tweets?collection=bookmarks&sort=newest&page=1&cursor=',
        '/api/tweets?collection=tweets&sort=newest&page=1&cursor=']);

    app.collectionFilter = 'all';
    app.sortOrder = 'newest';
    const switching = app.fetchTweets();
    assert.equal(app.loading, true);
    assert.deepEqual(Array.from(app.tweets, tweet => tweet.tweet_id), ['L1']);
    pending.get(all)(page(['A1']));
    await switching;
    assert.deepEqual(Array.from(app.tweets, tweet => tweet.tweet_id), ['A1']);

    // Returning to a recent list renders it at once, without a request or loading state.
    app.collectionFilter = 'likes';
    app.sortOrder = 'liked_latest';
    const back = app.fetchTweets();
    assert.equal(app.loading, false);
    await back;
    assert.equal(requests.length, 4);
    assert.deepEqual(Array.from(app.tweets, tweet => tweet.tweet_id), ['L1']);

    // A finished job invalidates cached lists.
    app.activity = { run_id: 'sync' };
    context.fetch = async url => {
        requests.push(url);
        return { ok: true, status: 200, json: async () => (url.includes('activity') ? { active: false } : page(['A2'])) };
    };
    app.scheduleActivityPoll = () => {};
    await app.fetchActivityStatus(true);
    app.collectionFilter = 'all';
    app.sortOrder = 'newest';
    await app.fetchTweets();
    assert.equal(requests.at(-1), all);
    assert.deepEqual(Array.from(app.tweets, tweet => tweet.tweet_id), ['A2']);
});

test('feed media loads lazily and tweets render without mutating their data', () => {
    const app = immediateComponent(loadScripts(browserContext(), ['themes.js', 'app.js'], 'tweetApp')());
    const photo = { type: 'photo', width: 10, height: 10, download: { local_path: 'media/1/p.jpg' } };
    const video = { type: 'video', download: { local_path: 'media/1/v.mp4', thumbnail_local_path: 'media/1/v.jpg' } };
    const bare = { type: 'video', download: { local_path: 'media/1/w.mp4' } };
    assert.match(app.renderMediaGrid([photo]), /<img src="\/media\/1\/p\.jpg" loading="lazy" decoding="async"/);
    assert.match(app.renderMediaGrid([photo, video]), /loading="lazy" decoding="async"/);
    assert.match(app.renderMediaGrid([video]), /preload="none"/);
    assert.match(app.renderMediaGrid([bare]), /preload="metadata"/);

    const quoted = { rest_id: '2', legacy: { full_text: 'q' } };
    const tweet = {
        tweet_id: '1', collections: ['bookmark'], collection: { type: 'like' },
        raw_json: { quoted_status_result: { result: { __typename: 'TweetWithVisibilityResults', birdwatch_pivot: { shorttitle: 'n' }, tweet: quoted } } },
    };
    assert.equal(app.getQuoteTweet(tweet).birdwatch_pivot.shorttitle, 'n');
    assert.equal('birdwatch_pivot' in quoted, false);
    app.renderActionBar(tweet);
    app.renderActionBar(tweet);
    assert.deepEqual(Array.from(tweet.collections), ['bookmark']);
});

test('feed layout hooks load early and skip off-screen work', () => {
    const html = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'index.html'), 'utf8');
    const css = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'static', 'css', 'styles.css'), 'utf8');
    assert.ok(html.includes('x-ref="feedSentinel" x-intersect.margin.1200px="loadMore()"'));
    assert.equal((html.match(/x-intersect\.margin\.600px="if\(!(panelQ|q)uotesLoadingMore/g) || []).length, 2);
    assert.ok(html.includes(`<div x-ref="feedList" :class="{ 'feed-refreshing': loading && tweets.length > 0 }"></div>`));
    // The shared menu sits outside the cards, so every card can skip off-screen work.
    assert.match(css, /\n\.feed-card \{\s*content-visibility: auto;/);
    assert.doesNotMatch(css + html, /feed-card-active/);
});

test('status polling pauses in hidden tabs and refreshes on return', () => {
    const context = browserContext();
    const intervals = [];
    context.setInterval = (callback, delay) => intervals.push({ callback, delay });
    context.document.hidden = true;
    const app = immediateComponent(loadScripts(context, ['themes.js', 'app.js'], 'tweetApp')());
    let polls = 0;
    app.fetchSetup = async () => { polls++; };
    app.fetchNotices = async () => {};
    app.fetchActivityStatus = async () => {};
    app.archiveResourcesLoaded = true;
    app.fetchArchiveEnrichmentStatus = () => { polls += 100; };
    // The harness keeps one listener per event; the activity poller has its own.
    app.startActivityPolling = () => {};
    app.initApp();
    const before = polls;
    intervals.filter(interval => [3000, 60000].includes(interval.delay)).forEach(interval => interval.callback());
    assert.equal(polls, before);
    context.document.hidden = false;
    context.__state.events.get('document:visibilitychange')();
    assert.equal(polls, before + 1);
});

test('feed cards render each fixture tweet shape as plain markup', () => {
    const { app } = feedListApp();
    const render = tweet => parseFakeHTML(app.renderFeedCard(JSON.parse(JSON.stringify(tweet))))[0];
    WEB_FIXTURE.slim.forEach((tweet, index) => {
        // Abbreviated list JSON renders exactly like the stored document.
        assert.equal(
            app.renderFeedCard(JSON.parse(JSON.stringify(tweet))),
            app.renderFeedCard(JSON.parse(JSON.stringify(WEB_FIXTURE.full[index]))),
            `tweet ${index}`,
        );
    });
    const [quoted, rawQuoted, retweet, rawRetweet, legacy, tombstoneQuote] = WEB_FIXTURE.slim.map(render);

    for (const card of [quoted, rawQuoted, retweet, rawRetweet, legacy, tombstoneQuote]) {
        assert.ok(card.classList.contains('feed-card'));
        assert.equal(card.dataset.action, 'thread');
        assert.equal(card.querySelectorAll('[data-action="profile"]').length, 2);
        assert.equal(card.querySelector('[data-action="menu"]').getAttribute('aria-expanded'), 'false');
        assert.equal(card.querySelector('[data-action="menu"]').getAttribute('aria-label'), 'More actions');
        assert.ok(card.querySelector('.roll-wrapper').matches('[data-action="stop"]'));
        assert.equal(card.querySelectorAll('img.avatar-image').length, card.querySelectorAll('.avatar-initial').length);
    }

    // Quote with a visibility wrapper: reply line, Show more, link card, notes on both tweets.
    assert.equal(quoted.dataset.tweetId, '100');
    assert.equal(quoted.dataset.threadId, '100');
    assert.equal(quoted.querySelector('[data-action="reply-profile"]').dataset.username, 'Dave');
    assert.equal(quoted.querySelector('[data-action="reply-profile"]').textContent, '@Dave');
    const quote = quoted.querySelector('[data-action="quote"]');
    assert.equal(quote.dataset.threadId, '200');
    assert.equal(quote.querySelector('.tweet-byline-name').textContent, 'Carol');
    assert.equal(quote.querySelector('.tweet-byline-detail').textContent, '@carol');
    assert.equal(quote.querySelector('.avatar-initial').textContent, 'C');
    assert.equal(quote.querySelector('img.avatar-image').getAttribute('src'), '/api/avatar/12?v=3');
    assert.equal(quoted.querySelectorAll('.expand-tweet').length, 1);
    assert.equal(quoted.querySelector('.expand-tweet').closest('[data-action="text"]').closest('[data-action="quote"]'), null);
    assert.ok(quoted.querySelector('.hashtag') && quoted.querySelector('.mention'));
    assert.match(quoted.querySelector('[data-action="text"] a').getAttribute('href'), /^https:\/\/example\.test\/story/);
    assert.ok(quoted.querySelector('a.mt-3').closest('[data-action="stop"]'), 'link card');
    assert.equal(quoted.textContent.match(/Readers added context/g).length, 2);
    assert.ok(quote.querySelector('.community-note-quote').closest('[data-action="stop"]'), 'quote note');
    assert.equal(quoted.querySelector('[data-action="text"]').getAttribute('class'), 'tweet-text text-[15px] whitespace-pre-wrap break-words');

    // Retweets show the original under a repost header and keep the list item id.
    for (const card of [retweet, rawRetweet]) {
        assert.equal(card.dataset.tweetId, '301');
        assert.equal(card.dataset.threadId, '300');
        assert.match(card.firstElementChild.textContent, /^Alice Reposted$/);
        assert.equal(card.querySelector('.tweet-byline-name').textContent, 'Erin');
        assert.match(card.textContent, /40 votes · Final results/);
    }
    assert.equal(quoted.textContent.includes('Reposted'), false);

    // Legacy v1 tweets resolve their quote and reply target from the raw shape.
    assert.equal(legacy.querySelector('[data-action="reply-profile"]').dataset.username, 'frank');
    assert.equal(legacy.querySelector('[data-action="quote"]').dataset.threadId, '401');

    // A tombstone quote renders the compact skeleton, which does not open a thread.
    assert.equal(tombstoneQuote.querySelector('[data-action="quote"]'), null);
    const skeleton = tombstoneQuote.querySelector('.tweet-skeleton-compact');
    assert.ok(skeleton.closest('[data-action="stop"]'));
    assert.equal(skeleton.closest('.feed-card'), tombstoneQuote);

    // A placeholder list item keeps the card frame with a content skeleton and no actions.
    const placeholder = render({
        tweet_id: '900', text: 'stale', author: { id: null, username: null, display_name: null },
        raw_json: null, media: [], availability: { placeholder: true, reason: 'deleted_by_author', message: 'Gone.' },
    });
    assert.equal(placeholder.dataset.threadId, '900');
    assert.equal(placeholder.querySelector('.tweet-byline-name').textContent, 'Unknown');
    assert.equal(placeholder.querySelector('.avatar-initial').textContent, '?');
    assert.ok(placeholder.querySelector('[data-action="text"] .tweet-skeleton-content'));
    assert.equal(placeholder.textContent.includes('stale'), false);
    assert.equal(placeholder.querySelector('a[title="Open on Twitter/X"]'), null);
});

test('feed cards escape hostile names, handles, reply targets, dates, sync labels, and ids', () => {
    const { app } = feedListApp();
    const hostile = '<script>alert(1)</script>"\'&x';
    app.formatDate = () => `<img src=x onerror=alert(1)>"&`;
    app.formatSyncDate = () => `Synced <b>"&'`;
    const tweet = {
        tweet_id: '1"><script>x</script>',
        text: 'Hello',
        author: { id: '"><img>', username: hostile, display_name: hostile },
        collection: { synced_at: '2026-01-01T00:00:00Z' },
        raw_json: { legacy: { in_reply_to_screen_name: hostile, full_text: 'Hello' } },
        quoted_tweet: {
            tweet_id: '2"><b>', text: 'Quoted',
            author: { id: '3', username: hostile, display_name: hostile },
            raw_json: { legacy: { in_reply_to_screen_name: hostile } },
        },
        retweeted_tweet: null,
    };
    const html = app.renderFeedCard(tweet);
    assert.doesNotMatch(html, /<script>|<img src=x|<b>|"><img>/);
    assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;&quot;&#039;&amp;x/);

    const card = parseFakeHTML(html)[0];
    assert.equal(card.dataset.tweetId, tweet.tweet_id);
    assert.equal(card.querySelector('.tweet-byline-name').textContent, hostile);
    assert.equal(card.querySelector('.tweet-byline-detail').textContent, `@${hostile}`);
    assert.equal(card.querySelector('.avatar-initial').textContent, '<');
    assert.equal(card.querySelector('img.avatar-image').getAttribute('src'), '/api/avatar/%22%3E%3Cimg%3E?v=3');
    assert.equal(card.querySelector('.roll-item').textContent, '<img src=x onerror=alert(1)>"&');
    assert.equal(card.querySelectorAll('.roll-item')[1].textContent, `Synced <b>"&'`);
    const replies = card.querySelectorAll('[data-action="reply-profile"]');
    assert.equal(replies.length, 2);
    for (const reply of replies) {
        assert.equal(reply.dataset.username, hostile);
        assert.equal(reply.textContent, `@${hostile}`);
    }
    const quote = card.querySelector('[data-action="quote"]');
    assert.equal(quote.dataset.threadId, '2"><b>');
    assert.equal(quote.querySelector('.tweet-byline-name').textContent, hostile);
    // The Open on X link escapes its path segments too.
    assert.match(html, /href="https:\/\/x\.com\/&lt;script&gt;alert\(1\)&lt;\/script&gt;&quot;&#039;&amp;x\/status\/1&quot;&gt;&lt;script&gt;x&lt;\/script&gt;"/);

    // Retweet headers escape the reposting account's name.
    const repost = app.renderFeedCard({
        tweet_id: '5', author: { display_name: hostile },
        retweeted_tweet: { tweet_id: '6', text: 'Original', author: { id: '7', username: 'o', display_name: 'O' } },
    });
    assert.equal(parseFakeHTML(repost)[0].firstElementChild.textContent, `${hostile} Reposted`);
    assert.doesNotMatch(repost, /<script>/);
});

test('feed list replaces pages, appends only new tweets, and ignores stale responses', async () => {
    const context = browserContext();
    const pending = [];
    context.fetch = url => new Promise(resolve => pending.push({ url, resolve }));
    const { app, list } = feedListApp(context);
    const tweet = id => ({ tweet_id: id, text: `Tweet ${id}`, author: { id: '1', username: 'a', display_name: 'A' } });
    const respond = (index, data) => pending[index].resolve({ ok: true, status: 200, json: async () => data });
    const ids = () => list.children.map(card => card.dataset.tweetId);

    const first = app.fetchTweets();
    respond(0, { tweets: [tweet('1'), tweet('2')], page: 1, pages: 2, total: 4, has_more: true, next_cursor: 'c' });
    await first;
    assert.deepEqual(ids(), ['1', '2']);
    const firstCard = list.children[0];

    const more = app.loadMore();
    respond(1, { tweets: [tweet('2'), tweet('3')], page: 2, pages: 2, total: 4, has_more: false });
    await more;
    assert.deepEqual(ids(), ['1', '2', '3']);
    assert.equal(list.children[0], firstCard, 'appending keeps existing cards');
    assert.deepEqual(Array.from(app.tweets, item => item.tweet_id), ids());

    // A superseded request renders nothing; the newer list replaces the old one.
    const stale = app.fetchTweets();
    app.searchQuery = 'new';
    const fresh = app.fetchTweets();
    assert.deepEqual(ids(), ['1', '2', '3'], 'the previous list stays while loading');
    respond(2, { tweets: [tweet('old')], page: 1, pages: 1, total: 1, has_more: false });
    await stale;
    assert.deepEqual(ids(), ['1', '2', '3']);
    respond(3, { tweets: [tweet('9')], page: 1, pages: 1, total: 1, has_more: false });
    await fresh;
    assert.deepEqual(ids(), ['9']);

    // A failed list clears the cards along with the data.
    context.fetch = async () => ({ ok: false, status: 500, json: async () => ({ detail: 'down' }) });
    app.searchQuery = 'broken';
    await app.fetchTweets();
    assert.equal(app.error, 'down');
    assert.deepEqual(ids(), []);
});

test('profile search replaces feed cards after its Alpine template closes', async () => {
    const context = browserContext();
    const pending = [];
    context.fetch = url => new Promise(resolve => pending.push({ url, resolve }));
    const { app, list } = feedListApp(context);
    const tweet = (id, username) => ({
        tweet_id: id, text: `Tweet ${id}`,
        author: { id: username, username, display_name: username },
    });
    const original = [tweet('1', 'alice'), tweet('2', 'bob')];
    app.tweets = original;
    app.renderFeedList(original);
    app.profileCard = { username: 'bob' };
    let searched;
    const fetchTweets = app.fetchTweets;
    app.fetchTweets = () => (searched = fetchTweets.call(app));

    app.searchFrom(app.profileCard.username);
    app.closeProfileCard();
    // Alpine resolves magic refs through the initiating element. Removing the
    // profile's x-if template leaves the async search with an empty refs scope.
    app.$refs = {};
    assert.match(pending[0].url, /q=from%3Abob/);
    pending[0].resolve({ ok: true, status: 200, json: async () => ({
        tweets: [tweet('2', 'bob')], page: 1, pages: 1, total: 1, has_more: false,
    }) });
    await searched;

    assert.equal(app.profileCard, null);
    assert.equal(app.searchQuery, 'from:bob');
    assert.equal(app.total, 1);
    assert.deepEqual(list.children.map(card => card.dataset.tweetId), ['2']);
    assert.deepEqual(Array.from(app.tweets, item => item.tweet_id), ['2']);
});

test('media whitespace opens another post in split view while media surfaces retain their actions', () => {
    const { app, list } = feedListApp();
    app.panelStack = [{ tweetId: '999' }];
    app.panelMode = 'thread';
    const opened = [];
    app.openThread = id => opened.push(id);
    const tweet = JSON.parse(JSON.stringify(WEB_FIXTURE.slim[0]));
    app.renderFeedList([tweet]);
    const wrapper = list.querySelector('[data-action="media"]');
    assert.ok(wrapper);
    dispatchFeed(list, 'click', wrapper.querySelector('.media-single') || wrapper);
    assert.deepEqual(opened, [tweet.tweet_id]);
    const image = wrapper.querySelector('img');
    assert.ok(image);
    const event = dispatchFeed(list, 'click', image);
    assert.equal(event.propagationStopped, true);
    assert.deepEqual(opened, [tweet.tweet_id]);
});

test('feed clicks are delegated by data-action like the per-card handlers', () => {
    const { app, list } = feedListApp();
    const opened = [];
    const profiles = [];
    app.openThread = id => opened.push(id);
    app.openProfileCard = (event, tweet, isQt, useClickedAnchor) => profiles.push({ anchor: event.currentTarget, tweet, useClickedAnchor });
    app.tweets = WEB_FIXTURE.slim.map(tweet => JSON.parse(JSON.stringify(tweet)));
    app.tweets.splice(1, 2);  // one copy of each shape; list ids must be unique
    app.renderFeedList(app.tweets);
    const [quoted, retweet, legacy, tombstoneQuote] = list.children;
    const click = target => dispatchFeed(list, 'click', target);

    // The card body opens the displayed tweet and lets the click reach the document.
    let event = click(quoted.querySelector('[data-action="text"]'));
    assert.deepEqual(opened, ['100']);
    assert.equal(event.propagationStopped, false);
    click(retweet.querySelector('.tweet-byline-detail'));
    assert.equal(opened.at(-1), '300');
    click(quoted.querySelector('.group svg'));  // action bar icons open the thread as before
    assert.equal(opened.at(-1), '100');

    // The quote opens the quoted thread, including from its text.
    event = click(quoted.querySelector('[data-action="quote"] .tweet-byline-name'));
    assert.equal(opened.at(-1), '200');
    assert.equal(event.propagationStopped, true);
    click(legacy.querySelector('[data-action="quote"] [data-action="text"]'));
    assert.equal(opened.at(-1), '401');
    const openedCount = opened.length;

    // Links, media, link cards, notes, dates, and tombstone quotes never open a thread.
    for (const target of [
        quoted.querySelector('[data-action="text"] a'),
        quoted.querySelector('a.mt-3 div'),
        quoted.querySelector('img.cursor-pointer'),
        quoted.querySelector('.roll-item'),
        quoted.querySelector('[data-action="quote"] .community-note-quote span'),
        tombstoneQuote.querySelector('.tweet-skeleton-message'),
        retweet.querySelector('[data-action="stop"] .truncate'),
        quoted.querySelector('a[title="Open on Twitter/X"] svg'),
    ]) {
        assert.ok(target, 'target exists');
        event = click(target);
        assert.equal(event.propagationStopped, true);
    }
    assert.equal(opened.length, openedCount);

    // Avatar and name open the profile card for the displayed tweet.
    event = click(retweet.querySelector('.avatar-initial'));
    assert.equal(profiles.at(-1).anchor, retweet.querySelector('.avatar'));
    assert.equal(profiles.at(-1).tweet.author.username, 'erin');
    assert.equal(event.propagationStopped, true);
    click(quoted.querySelector('.tweet-byline-name'));
    assert.equal(profiles.at(-1).anchor, quoted.querySelector('.tweet-byline-name'));
    assert.equal(profiles.at(-1).tweet.tweet_id, '100');

    // Reply targets anchor the card at the clicked name.
    click(legacy.querySelector('[data-action="reply-profile"]'));
    assert.equal(profiles.at(-1).anchor, legacy.querySelector('[data-action="reply-profile"]'));
    assert.equal(profiles.at(-1).tweet.author.username, 'frank');
    assert.equal(profiles.at(-1).useClickedAnchor, true);

    // Mentions anchor at the card avatar through the text element; hashtags search.
    const textElement = quoted.querySelector('[data-action="text"]');
    event = click(quoted.querySelector('.mention'));
    assert.equal(profiles.at(-1).anchor, textElement);
    assert.equal(profiles.at(-1).tweet.author.username, 'bob');
    assert.equal(event.propagationStopped, true);
    let searched = null;
    app.search = () => { searched = app.searchQuery; };
    click(quoted.querySelector('.hashtag'));
    assert.equal(searched, '#tag');
    assert.equal(opened.length, openedCount);
});

test('feed Show more expands text in place and tag edits keep the card', () => {
    const { app, list } = feedListApp();
    app.tweets = [JSON.parse(JSON.stringify(WEB_FIXTURE.slim[0])), {
        tweet_id: '700', text: 'Quoting a long tweet',
        author: { id: '1', username: 'q', display_name: 'Q' },
        quoted_tweet: { tweet_id: '100', text: 'long '.repeat(80), author: { id: '10', username: 'alice', display_name: 'Alice' } },
    }];
    app.renderFeedList(app.tweets);
    const [card, quoting] = list.children;
    const text = card.querySelector('[data-action="text"]');
    const quotedText = quoting.querySelector('[data-action="quote"] [data-action="text"]');
    const media = card.querySelector('img.cursor-pointer');
    const [shortText, shortQuote] = [text.textContent.length, quotedText.textContent.length];
    assert.ok(text.querySelector('.expand-tweet'));
    assert.ok(quotedText.querySelector('.expand-tweet'));

    const event = dispatchFeed(list, 'click', text.querySelector('.expand-tweet'));
    assert.equal(event.propagationStopped, true);
    assert.equal(app.expandedTweets['100'], true);
    assert.equal(list.children[0], card, 'the card and its media stay in place');
    assert.equal(card.querySelector('img.cursor-pointer'), media);
    assert.equal(card.querySelector('[data-action="text"]'), text);
    assert.equal(text.querySelector('.expand-tweet'), null);
    assert.ok(text.textContent.length > shortText);
    // Every copy of the tweet in the list expands, as the reactive template did.
    assert.equal(quotedText.querySelector('.expand-tweet'), null);
    assert.ok(quotedText.textContent.length > shortQuote);

    // Cards do not display tags: an edit updates the data the menu and long press
    // read, and leaves the card (and any playing media) in place.
    app.updateTweetTagData('100', { description: 'Edited', tags: ['Night'] });
    assert.equal(list.children[0], card);
    assert.equal(card.querySelector('img.cursor-pointer'), media);
    assert.equal(app.tweets[0].media_tags.description, 'Edited');
    assert.equal(app.feedCardTweet(card.querySelector('[data-action="menu"]')).media_tags.description, 'Edited');
    assert.equal(list.children.length, 2);
});

test('feed cards share one overflow menu placed over the opening button', async () => {
    const context = browserContext();
    const requests = [];
    context.fetch = async url => { requests.push(url); return { ok: true, json: async () => ({ raw_json: { rest_id: '300', full: true } }) }; };
    const { app, list } = feedListApp(context);
    app.tweets = WEB_FIXTURE.slim.map(tweet => JSON.parse(JSON.stringify(tweet))).filter((_, index) => index === 0 || index === 2);
    app.renderFeedList(app.tweets);
    const [quoted, retweet] = list.children;
    const button = retweet.querySelector('[data-action="menu"]');
    button.rect = { top: 420, right: 664, bottom: 456, left: 628, width: 36, height: 36 };

    let event = dispatchFeed(list, 'click', button.querySelector('circle'));
    assert.equal(event.propagationStopped, true, 'the opening click must not count as outside');
    assert.equal(app.tweetMenuOpen, 'list:301');
    assert.equal(app.listMenu.key, 'list:301');
    assert.equal(app.listMenu.tweet.tweet_id, '300');
    assert.deepEqual([app.listMenu.top, app.listMenu.right], [320, 36]);
    assert.equal(button.getAttribute('aria-expanded'), 'true');
    assert.ok(button.classList.contains('opacity-0'));
    assert.deepEqual(requests, ['/api/tweets/300/raw'], 'menus prefetch the displayed tweet');

    // The menu follows its button when content above it changes height.
    button.rect = { ...button.rect, top: 520 };
    app.placeListMenu();
    assert.equal(app.listMenu.top, 420);

    // Another card moves the menu; the first button is restored.
    const other = quoted.querySelector('[data-action="menu"]');
    other.rect = { top: 150, right: 664, bottom: 186, left: 628, width: 36, height: 36 };
    dispatchFeed(list, 'click', other);
    assert.equal(app.tweetMenuOpen, 'list:100');
    assert.equal(app.listMenu.top, 50);
    assert.equal(button.getAttribute('aria-expanded'), 'false');
    assert.equal(button.classList.contains('opacity-0'), false);
    assert.equal(other.getAttribute('aria-expanded'), 'true');

    // Detail menus and unrelated keys leave it open; its own key and the same button close it.
    app.closeTweetMenu('detail:100');
    assert.equal(app.tweetMenuOpen, 'list:100');
    dispatchFeed(list, 'click', other);
    assert.equal(app.tweetMenuOpen, null);
    assert.equal(other.getAttribute('aria-expanded'), 'false');
    dispatchFeed(list, 'click', other);
    app.closeTweetMenu(app.listMenu.key);
    assert.equal(app.tweetMenuOpen, null);
    assert.equal(other.classList.contains('opacity-0'), false);

    // Tag edits leave the card, and the open menu's button state, untouched.
    dispatchFeed(list, 'click', button);
    app.updateTweetTagData('300', null);
    assert.equal(list.children[1].querySelector('[data-action="menu"]'), button);
    assert.equal(button.getAttribute('aria-expanded'), 'true');
    assert.ok(button.classList.contains('opacity-0'));

    // Menu items act on the stored display tweet; a new list closes the menu.
    let tags = null;
    app.openTagModal = (id, data) => { tags = [id, data]; app.closeTweetMenu(); };
    app.openTagsFromTweetMenu(app.listMenu.tweet.tweet_id, app.listMenu.tweet.media_tags);
    assert.deepEqual(tags, ['300', null]);
    dispatchFeed(list, 'click', list.children[1].querySelector('[data-action="menu"]'));
    assert.equal(app.tweetMenuOpen, 'list:301');
    app.renderFeedList(app.tweets);
    assert.equal(app.tweetMenuOpen, null);
    const reloaded = list.children[1].querySelector('[data-action="menu"]');
    assert.equal(reloaded.getAttribute('aria-expanded'), 'false');
    assert.equal(reloaded.classList.contains('opacity-0'), false);

    const html = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'index.html'), 'utf8');
    const menu = html.slice(html.indexOf('x-show="tweetMenuOpen === listMenu.key"'));
    assert.match(menu, /^x-show="tweetMenuOpen === listMenu\.key" x-cloak/);
    assert.match(menu, /@click\.outside="closeTweetMenu\(listMenu\.key\)" @keydown\.escape\.window="closeTweetMenu\(listMenu\.key\)"/);
    assert.match(menu, /:style="\{ top: listMenu\.top \+ 'px', right: listMenu\.right \+ 'px' \}"/);
    assert.match(menu, /x-transition:enter="transition ease-out duration-100"/);
    assert.match(menu, /@click\.stop="openTagsFromTweetMenu\(listMenu\.tweet\.tweet_id, listMenu\.tweet\.media_tags\)"/);
    assert.match(menu, /@click\.stop="copyRawTweet\(listMenu\.tweet\)"/);
});

test('hiding a tweet removes its card unless the search lists hidden tweets', async () => {
    const context = browserContext();
    const requests = [];
    let ok = true;
    context.fetch = async (url, options = {}) => {
        requests.push([options.method || 'GET', url]);
        return { ok, status: ok ? 200 : 500, json: async () => ({}) };
    };
    const alerts = [];
    context.alert = message => alerts.push(message);
    const { app, list } = feedListApp(context);
    app.tweets = WEB_FIXTURE.slim.map(tweet => JSON.parse(JSON.stringify(tweet))).filter((_, index) => index === 0 || index === 2);
    app.total = 2;
    app.renderFeedList(app.tweets);
    const [quoted, retweet] = list.children;

    // The shared menu reads the card's hidden state and hides the card, not its display tweet.
    dispatchFeed(list, 'click', retweet.querySelector('[data-action="menu"]'));
    assert.equal(app.listMenu.hidden, false);
    await app.setTweetHidden(app.listMenu.key.slice('list:'.length), !app.listMenu.hidden);
    assert.deepEqual(requests.at(-1), ['PUT', '/api/tweets/301/hidden']);
    assert.equal(app.tweetMenuOpen, null);
    assert.deepEqual(app.tweets.map(tweet => tweet.tweet_id), ['100']);
    assert.deepEqual(list.children, [quoted]);
    assert.equal(app.total, 1);

    // An is:hidden list keeps a newly hidden card and drops an unhidden one.
    app.searchQuery = 'needle is:hidden';
    await app.setTweetHidden('100', true);
    assert.equal(app.tweets[0].hidden, true);
    assert.deepEqual(list.children, [quoted]);
    await app.setTweetHidden('100', false);
    assert.deepEqual(requests.at(-1), ['DELETE', '/api/tweets/100/hidden']);
    assert.deepEqual(app.tweets, []);
    assert.equal(app.total, 0);

    // -is:hidden does not list hidden tweets; a failed request changes nothing.
    app.searchQuery = '-is:hidden';
    assert.equal(app.searchShowsHidden(), false);
    app.tweets = [JSON.parse(JSON.stringify(WEB_FIXTURE.slim[0]))];
    app.renderFeedList(app.tweets);
    ok = false;
    await app.setTweetHidden('100', true);
    assert.equal(alerts.length, 1);
    assert.equal(app.tweets.length, 1);
    assert.equal(app.tweets[0].hidden, undefined);
});

test('feed overflow buttons keep the long-press Tags shortcut and avatars their load state', () => {
    const context = browserContext();
    const timers = [];
    context.setTimeout = (callback, delay) => { timers.push({ callback, delay }); return timers.length; };
    context.clearTimeout = id => { if (timers[id - 1]) timers[id - 1].cleared = true; };
    const { app, list } = feedListApp(context);
    app.tweets = [JSON.parse(JSON.stringify(WEB_FIXTURE.slim[2]))];
    app.tweets[0].retweeted_tweet.media_tags = { description: 'Kept', tags: ['Poll'] };
    app.renderFeedList(app.tweets);
    const card = list.children[0];
    const button = card.querySelector('[data-action="menu"]');
    const icon = button.querySelector('svg');

    const timerCount = timers.length;
    for (const modifiers of [{button: 2}, {button: 0, ctrlKey: true}]) {
        dispatchFeed(list, 'pointerdown', icon, {pointerType: 'mouse', ...modifiers});
        assert.equal(timers.length, timerCount, 'native menu gestures do not start the Tags hold timer');
    }

    const down = dispatchFeed(list, 'pointerdown', icon, { pointerType: 'touch', button: 0 });
    assert.equal(down.propagationStopped, true, 'pressing the button does not prefetch the thread');
    const press = timers.at(-1);
    assert.equal(press.delay, 1000);
    press.callback();
    assert.equal(app.tagModalOpen, true);
    assert.equal(app.tagModalTweetId, '300');
    assert.equal(app.tagModalData.description, 'Kept');
    // The click that ends a long press does not open the menu.
    dispatchFeed(list, 'click', button);
    assert.equal(app.tweetMenuOpen, null);

    for (const type of ['pointerup', 'pointercancel']) {
        dispatchFeed(list, 'pointerdown', button, { pointerType: 'mouse', button: 0 });
        const event = dispatchFeed(list, type, icon);
        assert.equal(event.propagationStopped, true);
        assert.equal(timers.at(-1).cleared, true, type);
    }
    dispatchFeed(list, 'pointerdown', button, { pointerType: 'touch', button: 0 });
    dispatchFeed(list, 'pointerleave', icon);
    assert.equal(timers.at(-1).cleared, undefined, 'leaving a child does not cancel');
    dispatchFeed(list, 'pointerleave', button);
    assert.equal(timers.at(-1).cleared, true);
    assert.equal(dispatchFeed(list, 'contextmenu', icon).defaultPrevented, false);
    assert.equal(dispatchFeed(list, 'contextmenu', button).defaultPrevented, false);
    assert.equal(dispatchFeed(list, 'contextmenu', card).defaultPrevented, false);
    assert.equal(dispatchFeed(list, 'pointerdown', card, { pointerType: 'mouse', button: 0 }).propagationStopped, false);
    assert.ok(list.listeners.some(entry => entry.type === 'pointerleave' && entry.capture));

    // Image load and error events do not bubble; capture listeners handle avatars only.
    const avatar = card.querySelector('img.avatar-image');
    avatar.naturalWidth = 48;
    dispatchFeed(list, 'load', avatar);
    assert.ok(avatar.classList.contains('avatar-image-loaded'));
    assert.ok(avatar.parentElement.classList.contains('avatar-loaded'));
    dispatchFeed(list, 'error', avatar);
    assert.equal(avatar.parentElement.classList.contains('avatar-loaded'), false);
    assert.ok(['load', 'error'].every(type => list.listeners.find(entry => entry.type === type).capture));
});

test('media lightbox data and community note links cannot inject script', () => {
    const context = browserContext();
    const events = [];
    context.window.dispatchEvent = event => events.push(event);
    const app = immediateComponent(loadScripts(context, ['themes.js', 'app.js'], 'tweetApp')());
    const hostile = "x'),alert(1),('\"<b>";
    const photo = { type: 'photo', url: hostile, download: { local_path: 'media/1/p.jpg' } };
    for (const html of [app.renderMediaGrid([photo]), app.renderMediaGrid([photo, photo])]) {
        assert.doesNotMatch(html, /JSON\.parse\('/);
        assert.match(html, /onclick="window\.tweetNookOpenLightbox\(this, \d\)"/);
        const attribute = html.match(/data-lightbox-media="([^"]*)"/)[1];
        assert.doesNotMatch(attribute, /['<>]/);
        const decoded = attribute.replace(/&quot;/g, '"').replace(/&#039;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
        const holder = { dataset: { lightboxMedia: decoded } };
        context.tweetNookOpenLightbox({ closest: () => holder }, 1);
        assert.equal(events.at(-1).detail.media[0].m.url, hostile);
        assert.equal(events.at(-1).detail.index, 1);
    }

    const note = app.renderCommunityNote({ birdwatch_pivot: { subtitle: {
        text: 'bad good',
        entities: [
            { fromIndex: 0, toIndex: 3, ref: { urlType: 'ExternalUrl', url: 'javascript:alert(1)' } },
            { fromIndex: 4, toIndex: 8, ref: { urlType: 'ExternalUrl', url: 'https://example.test/a?b=1&c=2' } },
        ],
    } } });
    assert.doesNotMatch(note, /javascript:/);
    assert.match(note, /<a href="#"[^>]*>bad<\/a>/);
    assert.match(note, /<a href="https:\/\/example\.test\/a\?b=1&amp;c=2"/);
});

test('what to sync is the server sync.collections setting, shared by the sync panel and Schedule', async () => {
    const context = browserContext();
    const requests = [];
    let saved = ['likes'];
    let failNext = false;
    context.fetch = async (url, options = {}) => {
        requests.push([url, options.method || 'GET', options.body ? JSON.parse(options.body) : null]);
        if (options.method === 'POST') {
            if (failNext) { failNext = false; return { ok: false, async json() { return {}; } }; }
            saved = JSON.parse(options.body).changes['sync.collections'];
        }
        return { ok: true, async json() { return { values: { sync: { collections: saved } }, explicit: ['sync.collections'] }; } };
    };
    const { nookLoadSyncCollections, nookToggleSyncCollection, nookSyncCollectionsLabel } = loadScripts(
        context, ['header.js'], '({nookLoadSyncCollections, nookToggleSyncCollection, nookSyncCollectionsLabel})',
    );
    const app = { nookSyncCollections: ['bookmarks', 'likes'], configData: { sync: { collections: ['likes'] } }, configOriginal: { sync: { collections: ['likes'] } } };

    await nookLoadSyncCollections(app);
    assert.deepEqual([...app.nookSyncCollections], ['likes']);
    assert.equal(nookSyncCollectionsLabel(app), 'Likes');

    // Choices save right away, in the canonical order, and keep a loaded Configuration page in step.
    await nookToggleSyncCollection(app, 'tweets');
    await nookToggleSyncCollection(app, 'bookmarks');
    assert.deepEqual(requests.at(-1), ['/api/config', 'POST', { changes: { 'sync.collections': ['bookmarks', 'likes', 'tweets'] } }]);
    assert.deepEqual([...app.nookSyncCollections], ['bookmarks', 'likes', 'tweets']);
    assert.deepEqual([...app.configData.sync.collections], ['bookmarks', 'likes', 'tweets']);
    assert.deepEqual([...app.configOriginal.sync.collections], ['bookmarks', 'likes', 'tweets']);
    assert.equal(nookSyncCollectionsLabel(app), 'Bookmarks, likes and your tweets');

    // A failed save puts the previous choice back.
    failNext = true;
    await nookToggleSyncCollection(app, 'likes');
    assert.deepEqual([...app.nookSyncCollections], ['bookmarks', 'likes', 'tweets']);

    // The last collection can't be turned off.
    app.nookSyncCollections = ['likes'];
    const before = requests.length;
    await nookToggleSyncCollection(app, 'likes');
    assert.equal(requests.length, before);
    assert.deepEqual([...app.nookSyncCollections], ['likes']);
});

test('sync panel shows paused and skipped reasons, new tweets, and the server schedule label', () => {
    const context = browserContext();
    const { tweetApp, nookSyncPanel } = loadScripts(context, ['themes.js', 'app.js', 'header.js'], '({tweetApp, nookSyncPanel})');
    const app = immediateComponent(tweetApp());
    const panel = Object.assign(Object.create(app), nookSyncPanel());

    const paused = { key: 'tagging', title: 'Automated tags', state: 'failed', paused: true, reason: 'daily spend limit reached', summary: 'Stopped at the spend limit' };
    assert.equal(panel.stepState(paused), 'paused');
    assert.equal(panel.stepStateLabel(paused), 'paused');
    assert.equal(panel.describe(paused).left, 'Tag saved posts');
    assert.equal(panel.describe(paused).right, 'Paused');
    assert.equal(panel.describe(paused).status, 'daily spend limit reached');

    const failed = { key: 'media', title: 'Media', state: 'failed', summary: 'Disk full', reason: null };
    assert.equal(panel.describe(failed).danger, true);
    assert.equal(panel.describe(failed).status, 'Disk full');

    const skipped = { key: 'articles', title: 'Articles', state: 'skipped', reason: 'no article rows requiring refresh', summary: 'skipped due to no article rows requiring refresh' };
    assert.equal(panel.describe(skipped).status, 'No article rows requiring refresh');

    app.activity = null;
    app.lastActivity = {
        run_id: 'one', success: true, completed_at: 100,
        steps: [
            { key: 'sync:bookmarks:head', title: 'Bookmarks', state: 'complete', metrics: { new_tweets: 2 } },
            { key: 'sync:tweets:head', title: 'Your tweets', state: 'complete', metrics: { new_tweets: 1 } },
        ],
        issues: [],
    };
    assert.equal(panel.caption().left, '2 bookmarks and 1 tweet added');

    app.activitySchedule = { configured: true, label: 'Daily at 3:00 AM', description: 'Every day · randomized delay' };
    assert.equal(panel.scheduleLabel(), 'Daily at 3:00 AM');
    app.activitySchedule = { configured: false, label: 'Daily at 3:00 AM' };
    assert.equal(panel.scheduleLabel(), 'Scheduled syncs off');
});

test('sync panel labels unknown progress without fabricated fractions and animates active work', () => {
    const context = browserContext();
    const { tweetApp, nookSyncPanel, nookSyncBreakdown } = loadScripts(context, ['themes.js', 'app.js', 'header.js'], '({tweetApp, nookSyncPanel, nookSyncBreakdown})');
    const panel = Object.assign(Object.create(immediateComponent(tweetApp())), nookSyncPanel());
    const step = { state: 'active', title: 'Bookmarks', activity: 'Fetching page 3', completed: 3, total: 4, show_progress: false };

    // Timeline page totals are placeholders even when positive.
    assert.equal(panel.indeterminate(step), true);
    assert.equal(panel.describe(step).right, 'Page 3');
    assert.equal(panel.describe(step).status, 'Fetching page 3');

    step.activity = 'Downloading media';
    step.show_progress = true;
    assert.equal(panel.indeterminate(step), false);
    assert.equal(panel.describe(step).right, '3 / 4');
    for (const total of [undefined, null, 0, -1, 'unknown', Infinity]) {
        step.total = total;
        assert.equal(panel.indeterminate(step), true);
        assert.equal(panel.describe(step).right, 'Working');
    }

    step.show_progress = false;
    for (const state of ['pending', 'complete', 'skipped', 'failed', 'cancelled']) {
        step.state = state;
        assert.equal(panel.indeterminate(step), false);
        const breakdown = nookSyncBreakdown({ state, stats: [], total: 0 });
        assert.ok(breakdown.bars.every(bar => !bar.kind.includes('is-indeterminate')));
    }
    step.state = 'failed';
    step.paused = true;
    assert.equal(panel.indeterminate(step), false);
    assert.equal(panel.describe(step).right, 'Paused');

    const active = nookSyncBreakdown({ state: 'active', stats: [], total: 0 });
    assert.deepEqual(Array.from(active.bars, bar => ({ ...bar })), [{ kind: 'done is-indeterminate', width: 30 }]);

    const html = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'index.html'), 'utf8');
    const css = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'static', 'css', 'header.css'), 'utf8');
    assert.match(html, /class="cs-step-tab"[^\n]*stepState\(steps\(\)\[index\]\)/);
    assert.match(html, /class="bd-part" :class="'is-' \+ part.kind" :style="`width:\$\{part.width\}%`"/);
    assert.match(css, /\.bd-part\.is-indeterminate \{[^}]*animation: nook-sync-bounce[^}]*infinite alternate/);
    assert.match(css, /@keyframes nook-sync-bounce \{[^\n]*translateX\(233\.333333%\)/);
    assert.match(css, /@media \(prefers-reduced-motion: reduce\) \{[^\n]*\.bd-part\.is-indeterminate \{ animation: none;/);
});

test('activity log view follows a running log by byte offset', async () => {
    const context = browserContext();
    const chunks = {
        0: ['tweetnook sync: started\nPrepare: complete · Connected\n', 52],
        52: ['Bookmarks: complete · 1 pages · 3 tweets\n', 94],
        94: ['tweetnook sync: started\n', 24],
    };
    const requests = [];
    context.fetch = async url => {
        requests.push(url);
        const offset = Number(new URL(url, 'http://localhost').searchParams.get('offset'));
        const [text, next] = chunks[offset];
        return { ok: true, async text() { return text; }, headers: { get: name => (name === 'X-Log-Offset' ? String(next) : null) } };
    };
    const { nookLogsPage, nookRunTitle } = loadScripts(context, ['settings.js'], '({nookLogsPage, nookRunTitle})');
    const page = Object.assign(nookLogsPage(), { nookLogLines: null });

    page.nookLogLines = await page.readLog('run/1', 0);
    assert.equal(requests[0], '/api/activity/runs/run%2F1/log?offset=0');
    assert.equal(page.logOffset, 52);
    assert.equal(page.nookLogLines.length, 2);

    page.nookLogLines = await page.readLog('run/1', page.logOffset);
    assert.equal(page.logOffset, 94);
    assert.deepEqual([...page.nookLogLines.map(line => line.index)], [0, 1, 2]);
    assert.match(page.nookLogLines[2].text || JSON.stringify(page.nookLogLines[2]), /Bookmarks/);

    // A log that starts over (the server's offset went backwards) replaces what was shown.
    chunks[94] = ['tweetnook sync: started\n', 24];
    page.nookLogLines = await page.readLog('run/1', page.logOffset);
    assert.equal(page.logOffset, 24);
    assert.equal(page.nookLogLines.length, 1);

    assert.equal(nookRunTitle('tweetnook sync'), 'Archive sync');
    assert.equal(nookRunTitle('tweetnook import x-archive'), 'Archive import');
    assert.equal(nookRunTitle('tweetnook export'), 'Export');
});

test('timestamped worker logs show local times and retain UTC timestamps in exports', async () => {
    const context = browserContext();
    const { nookLogParse, nookLogsPage } = loadScripts(context, ['settings.js'], '({nookLogParse, nookLogsPage})');
    const stamp = '2026-10-17T21:01:16.123456Z';
    const raw = `${stamp} Media: progress · 47 files\n${stamp} ERROR: Storage is full.\n`;
    const lines = nookLogParse(raw);
    const localTime = new Date(stamp).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'});
    assert.equal(lines[0].time, localTime);
    assert.equal(lines[0].text, 'Media: progress · 47 files');
    assert.equal(lines[0].quiet, true);
    assert.equal(lines[1].tone, 'error');
    assert.equal(lines[1].time, localTime);
    assert.equal(nookLogParse('Older log without a timestamp\n')[0].time, '');
    const page = nookLogsPage();
    page.nookLogLines = lines;
    assert.equal(page.rawText(), raw); // Copy and Download both use rawText().
    let copied = null;
    context.navigator = {clipboard: {writeText: async value => { copied = value; }}};
    context.setTimeout = () => 0;
    await page.copy();
    assert.equal(copied, raw);
    assert.equal(page.copied, true);
});

test('combined archive preparation keeps its name through enrichment, sync, media and history', () => {
    const context = browserContext();
    const { tweetApp, nookRunTitle } = loadScripts(context, ['themes.js', 'app.js', 'settings.js'], '({tweetApp, nookRunTitle})');
    const app = immediateComponent(tweetApp());
    for (const key of ['archive-enrich', 'sync:likes:head', 'media']) {
        app.activity = { title: 'tweetnook import enrich', steps: [{key, state: 'active'}] };
        assert.equal(app.activityHeaderText(), 'Archive Preparation in progress…');
        assert.equal(nookRunTitle(app.activity.title), 'Archive preparation');
    }
    app.activity = null;
    app.activityStartPending = true;
    app.activityStartingKind = 'enrich';
    assert.equal(app.activityCommandLabel(), 'Archive Preparation');
    assert.equal(nookRunTitle('tweetnook enrich'), 'Archive preparation');
    assert.equal(nookRunTitle('tweetnook rehydrate'), 'Archive enrichment');
    assert.equal(nookRunTitle('tweetnook sync'), 'Archive sync');
});

test('restored Settings logs catch up, resume a single poll and show results finished while closed', async () => {
    for (const status of ['failed', 'completed']) {
        const context = browserContext();
        const timers = new Map(), watchers = {};
        let nextTimer = 0, output = 'Likes: progress · 61 pages\n', result = {status: 'running'};
        const offsets = [];
        context.setTimeout = callback => { timers.set(++nextTimer, callback); return nextTimer; };
        context.clearTimeout = timer => timers.delete(timer);
        context.fetch = async url => {
            if (!url.includes('/log?')) return {ok: true, json: async () => ({...result})};
            const offset = Number(new URL(url, 'http://localhost').searchParams.get('offset'));
            offsets.push(offset);
            return {ok: true, text: async () => output.slice(offset), headers: {get: () => String(output.length)}};
        };
        const page = immediateComponent(loadScripts(context, ['settings.js'], 'nookLogsPage')());
        Object.assign(page, {showSettingsModal: true, settingsTab: 'logs', activityRuns: []});
        page.$watch = (key, callback) => { watchers[key] = callback; };
        page.init();
        await page.open({run_id: 'first-sync', status: 'running'});
        assert.equal(timers.size, 1);
        assert.equal(page.nookLogLines.length, 1);
        page.openFolds = [0];
        page.showSettingsModal = false;
        await watchers.showSettingsModal();
        assert.equal(timers.size, 0);
        output += 'Likes: progress · 173 pages\n';
        page.showSettingsModal = true;
        await watchers.showSettingsModal();
        assert.equal(page.nookLogLines.length, 2);
        assert.deepEqual([...page.openFolds], [0]);
        assert.equal(timers.size, 1);
        assert.ok(offsets[1] > 0); // Resume by offset without duplicating the restored log.
        const readsBeforeSameRun = offsets.length;
        await watchers['nookLogRun?.run_id']('first-sync', 'first-sync');
        assert.equal(offsets.length, readsBeforeSameRun); // Replacing metadata must not trigger a polling loop.
        assert.equal(timers.size, 1);
        const [timer, poll] = [...timers][0];
        timers.delete(timer);
        output += 'Media: progress · 10 files\n';
        await poll();
        assert.equal(page.nookLogLines.length, 3);
        assert.equal(timers.size, 1);
        page.showSettingsModal = false;
        await watchers.showSettingsModal();
        result = {status, completed_at: 1000, exit_code: status === 'failed' ? 2 : 0,
            summary: status === 'failed' ? 'Storage is full. Free disk space, then resume.' : 'Complete'};
        output += result.summary + '\n';
        page.showSettingsModal = true;
        await watchers.showSettingsModal();
        assert.equal(page.nookLogRun.status, status);
        assert.equal(page.nookLogRun.exit_code, result.exit_code);
        assert.equal(page.nookLogRun.completed_at, 1000);
        assert.equal(page.nookLogRun.summary, result.summary);
        assert.match(page.nookLogLines.at(-1).raw, new RegExp(result.summary));
        assert.equal(timers.size, 0);
        // Returning to the log tab also refreshes the restored view.
        page.settingsTab = 'appearance';
        await watchers.settingsTab();
        const reads = offsets.length;
        page.settingsTab = 'logs';
        await watchers.settingsTab();
        assert.equal(offsets.length, reads + 1);
        assert.equal(timers.size, 0);
    }
});

test('log restoration shares in-flight reads and ignores responses from a replaced log', async () => {
    const context = browserContext(), pending = [], timers = new Map(), watchers = {};
    let nextTimer = 0;
    context.setTimeout = callback => { timers.set(++nextTimer, callback); return nextTimer; };
    context.clearTimeout = timer => timers.delete(timer);
    context.fetch = url => new Promise(resolve => pending.push({url, resolve}));
    const page = immediateComponent(loadScripts(context, ['settings.js'], 'nookLogsPage')());
    Object.assign(page, {showSettingsModal: true, settingsTab: 'logs', activityRuns: []});
    page.$watch = (key, callback) => { watchers[key] = callback; };
    page.init();
    const opening = page.open({run_id: 'old', status: 'running'});
    page.showSettingsModal = false;
    await watchers.showSettingsModal();
    page.showSettingsModal = true;
    const restoring = watchers.showSettingsModal();
    assert.equal(pending.length, 2); // No competing poll while the first request is in flight.
    const switching = page.open({run_id: 'new', status: 'starting'});
    const resolve = (start, status, text) => {
        pending[start].resolve({ok: true, json: async () => ({status})});
        pending[start + 1].resolve({ok: true, text: async () => text, headers: {get: () => String(text.length)}});
    };
    resolve(2, 'running', 'New run\n');
    await switching;
    resolve(0, 'failed', 'Old run failed with a much longer log\n');
    await Promise.all([opening, restoring]);
    assert.equal(page.nookLogRun.run_id, 'new');
    assert.equal(page.nookLogRun.status, 'running');
    assert.equal(page.logOffset, 8);
    assert.equal(page.nookLogLines.length, 1);
    assert.equal(page.nookLogLines[0].raw, 'New run');
    assert.equal(timers.size, 1);
    const polling = page.refreshLive('new');
    page.showSettingsModal = false;
    await watchers.showSettingsModal();
    resolve(4, 'running', '');
    await polling;
    assert.equal(timers.size, 0);
});

test('Configuration page groups and formats fields from the server schema', () => {
    const context = browserContext();
    const { nookConfigSections, nookConfigFormat, nookConfigDisabled } = loadScripts(
        context, ['settings.js'], '({nookConfigSections, nookConfigFormat, nookConfigDisabled})',
    );
    const { schema, defaults } = JSON.parse(fs.readFileSync(path.join(ROOT, 'demo', 'config.json'), 'utf8'));
    const app = {
        configSchema: schema,
        configData: JSON.parse(JSON.stringify(defaults)),
        showAdvancedConfig: false,
        configSaving: false,
        getFieldType(section, key) {
            const value = this.configData[section][key];
            return typeof value === 'boolean' ? 'boolean' : typeof value === 'number' ? 'number' : 'text';
        },
    };
    const basic = nookConfigSections(app, 'basic');
    assert.deepEqual([...basic.map(group => group.title)], ['Web server', 'Avatars', 'Quoted and linked posts']);
    assert.ok(basic.flatMap(group => group.fields).some(field => field.path === 'sync.reply_linked_depth'));
    assert.equal(basic[0].note, 'Restart TweetNook after changing these.');
    assert.equal(nookConfigSections(app, 'advanced').length, 0);

    app.showAdvancedConfig = true;
    const advanced = nookConfigSections(app, 'advanced');
    const fields = [...basic, ...advanced].flatMap(group => group.fields);
    const paths = fields.map(field => field.path);
    // Every visible field outside the blacklist appears exactly once.
    const expected = Object.entries(defaults).flatMap(([section, values]) => Object.keys(values).map(key => `${section}.${key}`))
        .filter(item => !schema.blacklist.includes(item) && item !== 'sync.reply_linked_limit');
    assert.deepEqual([...paths].sort(), [...expected].sort());
    assert.ok(!paths.includes('sync.collections'));

    const field = name => fields.find(item => item.path === name);
    assert.equal(field('web.analytics_refresh_time').type, 'time');
    assert.equal(field('sync.reply_linked_depth').type, 'toggle01');
    assert.ok(!paths.includes('sync.reply_linked_limit'));
    app.configData.sync.reply_linked_depth = 1;
    const replyLimit = nookConfigSections(app, 'advanced').flatMap(group => group.fields)
        .find(item => item.path === 'sync.reply_linked_limit');
    assert.equal(replyLimit.type, 'number');
    assert.equal(replyLimit.number.min, 1);
    assert.equal(replyLimit.number.step, 1);
    app.configData.sync.reply_linked_limit = 3;
    app.configData.sync.reply_linked_depth = 0;
    assert.ok(!nookConfigSections(app, 'advanced').flatMap(group => group.fields)
        .some(item => item.path === 'sync.reply_linked_limit'));
    assert.equal(app.configData.sync.reply_linked_limit, 3, 'hiding the limit preserves the saved choice');
    assert.equal(nookConfigFormat(field('database.cache_size_kb'), 524288), '512 MiB');
    assert.equal(nookConfigFormat(field('sync.page_delay'), 2), `2 ${schema.units['sync.page_delay']}`);
    // Avatar cache limits depend on fetching avatars.
    app.configData.web.fetch_avatars = false;
    assert.equal(nookConfigDisabled(app, field('web.avatar_cache_limit_mb')), true);
    app.configData.web.fetch_avatars = true;
    assert.equal(nookConfigDisabled(app, field('web.avatar_cache_limit_mb')), false);
});

test('setup polls fill an unset archive ID without replacing a user entry', async () => {
    const context = browserContext();
    context.fetch = async () => ({ok: true, json: async () => ({
        required: true, auth: {values: {user_id: '4242'}}, archive: {ready: true},
    })});
    const {tweetApp} = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.setupAuth = {auth_token: 'synthetic', ct0: 'synthetic', user_id: ''};
    await app.fetchSetup();
    assert.equal(app.setupAuth.user_id, '4242');
    app.setupAuth.user_id = '5151';
    await app.fetchSetup();
    assert.equal(app.setupAuth.user_id, '5151');
    const markup = fs.readFileSync(path.join(ROOT, 'tweetnook/web/index.html'), 'utf8');
    assert.match(markup, /Settings<\/h2><span x-show="isDemo"/);
    assert.doesNotMatch(markup, /Filled from your imported archive when available/);
});

test('enrichment status waits for setup and tolerates the archive attachment race', async () => {
    const context = browserContext();
    let calls = 0, errors = 0;
    context.console.error = () => { errors++; };
    context.fetch = async () => { calls++; return {
        ok: false, status: 503, json: async () => ({detail: {code: 'archive_not_ready'}}),
    }; };
    const {tweetApp} = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.archiveReady = false;
    await app.fetchArchiveEnrichmentStatus();
    assert.equal(calls, 0);
    app.archiveReady = true;
    app.setupForced = true;
    await app.fetchArchiveEnrichmentStatus();
    assert.equal(calls, 0);
    app.setupForced = false;
    await app.fetchArchiveEnrichmentStatus();
    assert.equal(calls, 1);
    assert.equal(errors, 0);
    assert.equal(app.archiveEnrichmentRefreshing, false);
});

test('historical activity on first load does not announce fresh content in demos or archives', async () => {
    for (const isDemo of [true, false]) {
        for (const feedLoaded of [true, false]) {
            const context = browserContext();
            context.window.TWEETNOOK_DEMO = isDemo;
            const history = {run_id: 'saved-sync', running: false, success: true,
                steps: [{key: 'sync:likes:head', state: 'complete'}, {key: 'media', state: 'complete'}]};
            let snapshot = history;
            context.fetch = async () => ({ok: true, json: async () => ({active: false, last_snapshot: snapshot})});
            const {tweetApp} = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
            const app = immediateComponent(tweetApp());
            app.archiveReady = true;
            app.tweets = feedLoaded ? [{tweet_id: 'reading'}] : [];
            let feeds = 0;
            app.fetchTweets = async () => { feeds++; };
            await app.fetchActivityStatus();
            assert.equal(app.lastActivity.run_id, 'saved-sync');
            assert.equal(app.feedUpdateAvailable, false, `initial history: demo=${isDemo}, feed=${feedLoaded}`);
            assert.equal(app.feedContentRevision, 0);
            assert.equal(feeds, 0);
            await app.fetchActivityStatus();
            assert.equal(app.feedUpdateAvailable, false);
            // A different completed run detected later still updates the feed.
            snapshot = {...history, run_id: 'new-sync'};
            await app.fetchActivityStatus();
            assert.equal(app.feedUpdateAvailable, true);
            assert.equal(app.feedContentRevision, 1);
            assert.equal(feeds, feedLoaded ? 0 : 1);
        }
    }
});

test('a run finishing between initial status polls still announces new content', async () => {
    const context = browserContext();
    let snapshot = null;
    context.fetch = async () => ({ok: true, json: async () => ({active: false, last_snapshot: snapshot})});
    const app = immediateComponent(loadScripts(context, ['themes.js', 'app.js'], 'tweetApp')());
    app.archiveReady = true;
    app.tweets = [{tweet_id: 'reading'}];
    await app.fetchActivityStatus();
    assert.equal(app.feedUpdateAvailable, false);
    snapshot = {run_id: 'new-sync', steps: [{key: 'sync:likes:head', state: 'complete'}]};
    await app.fetchActivityStatus();
    assert.equal(app.feedUpdateAvailable, true);
    assert.equal(app.feedContentRevision, 1);
});

test('a task started before the first status response still announces its saved content', async () => {
    const context = browserContext();
    context.fetch = async () => ({ok: true, json: async () => ({active: false,
        last_snapshot: {run_id: 'requested-sync', steps: [{key: 'sync:likes:head', state: 'complete'}]},
    })});
    const app = immediateComponent(loadScripts(context, ['themes.js', 'app.js'], 'tweetApp')());
    app.archiveReady = true;
    app.tweets = [{tweet_id: 'reading'}];
    app.activityStartPending = true;
    await app.fetchActivityStatus();
    assert.equal(app.feedUpdateAvailable, true);
    assert.equal(app.feedContentRevision, 1);
});

test('committed collection stages refresh an empty feed and its count while media still runs', async () => {
    const context = browserContext();
    let committed = false, feeds = 0;
    context.fetch = async url => {
        if (url === '/api/activity/status') return {ok: true, json: async () => ({
            active: true, snapshot: {run_id: 'first-sync', steps: [
                {key: 'sync:bookmarks:head', state: 'complete'},
                {key: 'media', state: 'active'},
            ]},
        })};
        assert.match(url, /^\/api\/tweets\?/);
        feeds++;
        return {ok: true, json: async () => ({
            tweets: committed ? [{tweet_id: 'saved'}] : [], total: committed ? 227 : 0,
            page: 1, pages: 1, has_more: false,
        })};
    };
    const {tweetApp} = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.archiveReady = true;
    app.collectionFilter = 'bookmarks';
    app.renderFeedList = () => {};
    app.scheduleFeedPrefetch = () => {};
    await app.fetchTweets();
    assert.equal(app.total, 0);
    app.activity = {run_id: 'first-sync', steps: [{key: 'sync:bookmarks:head', state: 'active'}]};
    committed = true;
    await app.fetchActivityStatus();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(app.total, 227);
    assert.equal(app.tweets[0].tweet_id, 'saved');
    assert.equal(app.activity.steps[1].state, 'active');
    assert.equal(app.feedUpdateAvailable, false);
    assert.equal(feeds, 2);
    await app.fetchActivityStatus();
    assert.equal(feeds, 2); // Identical polls do not keep refreshing the feed.
});

test('committed content offers a refresh without disturbing a populated feed', async () => {
    const context = browserContext();
    context.fetch = async () => ({ok: true, json: async () => ({active: true,
        snapshot: {run_id: 'sync', steps: [{key: 'sync:likes:head', state: 'complete'}]},
    })});
    const {tweetApp} = loadScripts(context, ['themes.js', 'app.js'], '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.archiveReady = true;
    app.tweets = [{tweet_id: 'reading'}];
    let feeds = 0;
    app.fetchTweets = async () => { feeds++; };
    await app.fetchActivityStatus();
    assert.equal(app.feedUpdateAvailable, true);
    assert.equal(feeds, 0);
    assert.equal(app.tweets[0].tweet_id, 'reading');
    const markup = fs.readFileSync(path.join(ROOT, 'tweetnook/web/index.html'), 'utf8');
    assert.match(markup, /x-show="feedUpdateAvailable"[^>]*@click="fetchTweets\(\)"/);
});

async function main() {
    let failures = 0;
    for (const { name, fn } of tests) {
        try {
            await fn();
            process.stdout.write(`ok - ${name}\n`);
        } catch (error) {
            failures += 1;
            process.stderr.write(`not ok - ${name}\n${error.stack}\n`);
        }
    }
    process.stdout.write(`\n${tests.length - failures}/${tests.length} browser asset tests passed\n`);
    if (failures) process.exitCode = 1;
}

main();
