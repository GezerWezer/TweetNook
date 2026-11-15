'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { appScripts, appScriptsWith } = require('./page_scripts.cjs');
const { ROOT, browserContext, immediateComponent, loadScripts, test, run } = require('./web_harness.cjs');

const SYSTEM_STACK = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif';

// WCAG 2 relative luminance and contrast ratio, written out here rather than
// borrowed from themes.js so the check doesn't share the generator's maths.
function luminance(hex) {
    const channel = value => {
        const c = value / 255;
        return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    };
    const [r, g, b] = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16));
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}
function contrast(a, b) {
    const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
}
const isHex = value => /^#[0-9a-fA-F]{6}$/.test(value);

function themes() {
    return loadScripts(browserContext(), ['themes.js'], '({THEMES})').THEMES;
}

function startedApp(context) {
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    for (const name of ['fetchSetup', 'fetchNotices', 'fetchActivityStatus', 'fetchTweets', 'fetchStats',
        'fetchArchiveEnrichmentStatus', 'fetchAutomatedTagging']) {
        app[name] = async () => {};
    }
    app.initApp();
    return app;
}

test('every theme keeps text legible on the surfaces it is drawn on', () => {
    const failures = [];
    const check = (key, what, fg, bg, minimum) => {
        assert.ok(isHex(fg) && isHex(bg), `${key} ${what}: expected hex colours, got ${fg} on ${bg}`);
        const ratio = contrast(fg, bg);
        if (ratio < minimum) failures.push(`${key} ${what}: ${ratio.toFixed(2)} < ${minimum}`);
    };
    for (const [key, theme] of Object.entries(themes())) {
        // Body text (AA, 4.5:1) on every opaque surface it appears on.
        for (const surface of ['--bg-primary', '--bg-secondary', '--field-bg', '--dropdown-bg']) {
            check(key, `text on ${surface}`, theme['--text-primary'], theme[surface], 4.5);
        }
        check(key, 'heading on bg', theme['--heading-color'], theme['--bg-primary'], 4.5);
        // The selected tab sits on a raised panel in a bevelled theme, and on
        // the header (or the page through a translucent header) otherwise.
        const tabSurface = theme._edge === 'bevel' ? theme['--bg-secondary']
            : isHex(theme['--bg-header']) ? theme['--bg-header'] : theme['--bg-primary'];
        check(key, 'selected tab text', theme['--selected-text'], tabSurface, 4.5);
        // Secondary text and like/danger marks: AA for large text and UI parts (3:1).
        check(key, 'muted text on bg', theme['--text-secondary'], theme['--bg-primary'], 3);
        check(key, 'danger on bg', theme['--danger-color'], theme['--bg-primary'], 3);
    }
    assert.deepEqual(failures, []);
});

test('themes keep X shapes unless a seed asks for square corners and bevels', () => {
    const all = themes();
    for (const [key, theme] of Object.entries(all)) {
        if (key === 'steam-green') continue;
        assert.equal(theme['--radius-full'], '9999px', key);
        assert.equal(theme['--radius-xl'], '16px', key);
        assert.equal(theme._edge, 'flat', key);
        assert.equal(theme['--font-ui'], SYSTEM_STACK, key);
    }
    const steam = all['steam-green'];
    for (const token of ['--radius-full', '--radius-xl', '--radius-lg', '--radius-md']) assert.equal(steam[token], '2px');
    assert.equal(steam._edge, 'bevel');
    assert.match(steam['--font-ui'], /^Tahoma,/);
    // Steam's chrome is the lighter olive around darker content, not a dark bar.
    assert.ok(luminance(steam['--bg-header']) > luminance(steam['--bg-primary']));
    assert.ok(luminance(steam['--edge-light']) > luminance(steam['--bg-secondary']));
    assert.ok(luminance(steam['--edge-dark']) < luminance(steam['--bg-primary']));
});

test('new colour roles follow the variable they split from, after its override', () => {
    const all = themes();
    // Classic Light hand-picks its secondary surface; fields keep using it.
    assert.equal(all['classic-light']['--bg-secondary'], '#f7f9f9');
    assert.equal(all['classic-light']['--field-bg'], '#f7f9f9');
    for (const [key, theme] of Object.entries(all)) {
        if (key === 'steam-green') continue;
        assert.equal(theme['--field-bg'], theme['--bg-secondary'], key);
        assert.equal(theme['--heading-color'], theme['--text-primary'], key);
        assert.equal(theme['--selected-text'], theme['--text-primary'], key);
    }
    assert.equal(all['steam-green']['--heading-color'], '#dccd62');
    assert.equal(all['steam-green']['--field-bg'], '#3e4637');
});

test('applying a theme sets its edge style and shape, and switching away restores them', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    const root = context.document.documentElement;
    const css = context.__state.cssProperties;

    app.applyTheme('steam-green');
    assert.equal(root.dataset.edge, 'bevel');
    assert.equal(css.get('--radius-xl'), '2px');
    assert.equal(css.get('--selected-text'), '#dccd62');
    app.applyTheme('dracula');
    assert.equal(root.dataset.edge, 'flat');
    assert.equal(css.get('--radius-xl'), '16px');
    assert.equal(css.get('--selected-text'), '#f8f8f2');
    assert.equal(css.get('--font-ui'), SYSTEM_STACK);
});

test('Theme Default font follows the theme, including a default saved as the system stack', () => {
    const context = browserContext();
    context.localStorage.setItem('tvx-font', 'system');
    context.localStorage.setItem('tvx-font-family', SYSTEM_STACK);
    const app = startedApp(context);
    assert.equal(app.fontFamily, 'system');
    assert.equal(context.document.body.style.fontFamily, 'var(--font-ui)');
    assert.equal(app.selectedFontChoice().label, 'Theme Default');

    // Another saved family is restored as it was stored.
    const other = browserContext();
    other.localStorage.setItem('tvx-font', 'local:Fira Sans');
    other.localStorage.setItem('tvx-font-family', '"Fira Sans", sans-serif');
    startedApp(other);
    assert.equal(other.document.body.style.fontFamily, '"Fira Sans", sans-serif');
});

test('theme samples in Settings use each theme\'s own corners', () => {
    const context = browserContext();
    const { tweetApp, nookThemeTileStyle } = loadScripts(context, appScriptsWith('settings.js'), '({tweetApp, nookThemeTileStyle})');
    const app = immediateComponent(tweetApp());
    app.applyTheme('steam-green');
    assert.match(nookThemeTileStyle('dracula', app.THEMES.dracula, app), /--radius-lg:12px;.*--radius-full:9999px/);
    assert.match(nookThemeTileStyle('steam-green', app.THEMES['steam-green'], app), /--radius-lg:2px;.*--radius-full:2px/);
});

test('the theme structure stylesheet loads after every feature stylesheet', () => {
    const shell = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'index.html'), 'utf8');
    const sheets = [...shell.matchAll(/<link rel="stylesheet" href="\/static\/css\/([^"]+)">/g)].map(m => m[1]);
    assert.equal(sheets.at(-1), 'theme-structure.css');
});

run();
