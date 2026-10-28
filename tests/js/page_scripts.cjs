'use strict';

// The browser loads the app as classic scripts in index.html order. Harnesses read that order
// here instead of naming files, so a script split out of app.js is loaded by every harness as
// soon as its <script> tag is added to the page.

const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..', '..');
const WEB_ROOT = path.join(ROOT, 'tweetnook', 'web');
const JS_DIR = path.join(WEB_ROOT, 'static', 'js');
const INCLUDE = /^[ \t]*<!-- include ([\w/-]+\.html) -->\n$/;

// The page as the server sends it before versioning asset URLs: the index.html shell with
// each include line replaced by its fragment, as tweetnook/web/page.py assembles it. Each
// line comes with the file and line it was written in.
function pageLines(webRoot = WEB_ROOT) {
    const lines = [];
    const expand = (file, including) => {
        const name = path.relative(webRoot, file);
        if (including.includes(file)) throw new Error(`${name} includes itself`);
        const text = fs.readFileSync(file, 'utf8');
        if (including.length && !text.endsWith('\n')) throw new Error(`${name} must end with a newline`);
        text.split(/(?<=\n)/).forEach((line, index) => {
            const include = INCLUDE.exec(line);
            if (include) expand(path.join(webRoot, 'fragments', include[1]), [...including, file]);
            else lines.push({ text: line, where: `${name}:${index + 1}` });
        });
    };
    expand(path.join(webRoot, 'index.html'), []);
    return lines;
}

function pageHtml(webRoot = WEB_ROOT) {
    return pageLines(webRoot).map(line => line.text).join('');
}

function pageScripts() {
    const html = pageHtml();
    const names = [...html.matchAll(/<script\b[^>]*\bsrc="\/static\/js\/([^"?]+)"/g)].map(match => match[1]);
    if (!names.includes('app.js')) throw new Error('index.html no longer loads app.js');
    return names;
}

// Everything tweetApp() depends on: the page's scripts up to and including app.js. Scripts after
// it (header, settings, ...) attach document listeners at load time and are tested on their own.
function appScripts() {
    const names = pageScripts();
    return names.slice(0, names.indexOf('app.js') + 1);
}

// appScripts() plus later page scripts a test needs, kept in page order.
function appScriptsWith(...extra) {
    const wanted = new Set([...appScripts(), ...extra]);
    const names = pageScripts().filter(name => wanted.has(name));
    if (names.length !== wanted.size) throw new Error(`index.html does not load ${extra.join(', ')}`);
    return names;
}

function readScripts(names) {
    return names.map(name => fs.readFileSync(path.join(JS_DIR, name), 'utf8')).join('\n');
}

module.exports = { JS_DIR, WEB_ROOT, pageLines, pageHtml, pageScripts, appScripts, appScriptsWith, readScripts };
