'use strict';

// Shared fixture for the browser-asset harnesses (tests/js/test_web_*.cjs): a fake
// browser context, script loading in page order, Alpine scope merging, a small HTML
// parser and the test runner. Each test file registers tests with test() and calls run().

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { appScripts, appScriptsWith, pageHtml, pageScripts, readScripts } = require('./page_scripts.cjs');

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
    const layoutStyles = { feed: { maxWidth: '600px' }, panel: { width: '600px' } };
    const themeColor = {
        content: '#000000',
        setAttribute(name, value) { this[name] = value; },
    };
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
        getComputedStyle: element => element.computedStyle || {},
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
                    removeProperty(key) {
                        cssProperties.delete(key);
                    },
                },
                classList: {
                    contains(value) {
                        return rootClasses.has(value);
                    },
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
                if (selector === '.app-column') return { computedStyle: layoutStyles.feed };
                if (selector === '.nook-detail-panel') return { computedStyle: layoutStyles.panel };
                if (selector === 'meta[name="theme-color"]') return themeColor;
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
            constructor(callback, options = {}) {
                this.callback = callback;
                this.options = options;
                this.observed = [];
                context.intersectionObservers.push(this);
            }
            observe(node) {
                if (!this.observed.includes(node)) this.observed.push(node);
            }
            unobserve(node) {
                this.observed = this.observed.filter(observed => observed !== node);
            }
            disconnect() {
                this.observed = [];
            }
        },
        intersectionObservers: [],
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
        themeColor,
        layoutStyles,
    };
    vm.createContext(context);
    return context;
}

// The page's own stylesheets (not Tailwind's build), concatenated in the order the
// page links them, so a test sees the rules wherever they live and in cascade order.
function pageCss() {
    const shell = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'index.html'), 'utf8');
    return [...shell.matchAll(/<link rel="stylesheet" href="\/static\/css\/([\w-]+\.css)">/g)]
        .filter(([, name]) => name !== 'tailwind.css')
        .map(([, name]) => fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'static', 'css', name), 'utf8'))
        .join('\n');
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

// Like Alpine's nested x-data scopes: a read finds the nearest component that has the
// name, a write goes to the component that owns it (the outer one for a new name), and
// methods and getters run with the merged scope as `this`.
function nookMergeScope(parent, child) {
    const owner = name => [child, parent].find(object => name in object);
    const scope = new Proxy({}, {
        has: (_, name) => Boolean(owner(name)),
        get: (_, name) => {
            const target = owner(name);
            return target && Reflect.get(target, name, scope);
        },
        set: (_, name, value) => {
            const target = owner(name) || parent;
            const setter = Object.getOwnPropertyDescriptor(target, name)?.set;
            if (!setter) return Reflect.set(target, name, value);
            setter.call(scope, value);
            return true;
        },
    });
    return scope;
}

// The tag dialog as the page mounts it: inside the app's scope, opened by its event.
function mountTagDialog(context, app) {
    const dialog = nookMergeScope(app, vm.runInContext('tagDialog()', context));
    dialog.init();
    return dialog;
}

// The app as the setup dialog sees it: the dialog's own state over the app's.
function withSetupDialog(context, app) {
    return nookMergeScope(app, vm.runInContext('setupDialog()', context));
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
        const values = new Map();
        this.style = {setProperty: (name, value) => values.set(name, value), getPropertyValue: name => values.get(name) || '', removeProperty: name => values.delete(name)};
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
    const app = immediateComponent(loadScripts(context, appScripts(), 'tweetApp')());
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

// Runs the tests a file registered, in order, and reports them like one suite.
async function run() {
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

module.exports = {
    pageCss,
    FakeElement,
    JS_DIR,
    ROOT,
    VOID_TAGS,
    WEB_FIXTURE,
    browserContext,
    compoundMatcher,
    decodeEntities,
    dispatchFeed,
    feedListApp,
    immediateComponent,
    loadScripts,
    mountTagDialog,
    nookMergeScope,
    parseFakeHTML,
    selectorMatcher,
    splitSelector,
    withSetupDialog,
    test,
    run,
};
