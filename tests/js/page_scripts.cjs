'use strict';

// The browser loads the app as classic scripts in index.html order. Harnesses read that order
// here instead of naming files, so a script split out of app.js is loaded by every harness as
// soon as its <script> tag is added to the page.

const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..', '..');
const JS_DIR = path.join(ROOT, 'tweetnook', 'web', 'static', 'js');
const INDEX = path.join(ROOT, 'tweetnook', 'web', 'index.html');

function pageScripts() {
    const html = fs.readFileSync(INDEX, 'utf8');
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

module.exports = { JS_DIR, pageScripts, appScripts, appScriptsWith, readScripts };
