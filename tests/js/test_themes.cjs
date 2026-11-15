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
        // Button labels on their own faces; a transparent face shows the page.
        check(key, 'primary button label', theme['--button-primary-text'], theme['--button-primary-bg'], 4.5);
        const buttonFace = theme['--button-bg'] === 'transparent' ? theme['--bg-primary'] : theme['--button-bg'];
        check(key, 'button label', theme['--button-text'], buttonFace, 4.5);
        // Secondary text and like/danger marks: AA for large text and UI parts (3:1).
        check(key, 'muted text on bg', theme['--text-secondary'], theme['--bg-primary'], 3);
        check(key, 'danger on bg', theme['--danger-color'], theme['--bg-primary'], 3);
    }
    assert.deepEqual(failures, []);
});

test('themes keep X shapes unless a seed asks for its own corners and edge', () => {
    const all = themes();
    for (const [key, theme] of Object.entries(all)) {
        assert.ok(['flat', 'bevel', 'gloss'].includes(theme._edge), key);
        if (theme._edge !== 'flat') continue;
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

    const win = all['windows-95'];
    for (const token of ['--radius-full', '--radius-xl', '--radius-lg', '--radius-md']) assert.equal(win[token], '2px');
    assert.equal(win._edge, 'bevel');
    assert.match(win['--font-ui'], /^"Microsoft Sans Serif",/);
    // Grey faces around white wells, with the two-pixel bevel's inner ring.
    assert.equal(win['--bg-secondary'], '#c0c0c0');
    assert.equal(win['--field-bg'], '#ffffff');
    assert.notEqual(win['--bevel-raised-inner'], '0 0 #0000');
    assert.notEqual(win['--bevel-sunken-inner'], '0 0 #0000');
    const aero = all['frutiger-aero'];
    assert.equal(aero._edge, 'gloss');
    assert.match(aero['--font-ui'], /^Frutiger, "Frutiger Linotype",/);
    // Gel capsules want the rounded shape's generous radii.
    assert.equal(aero['--radius-full'], '9999px');
    assert.deepEqual([aero['--radius-xl'], aero['--radius-lg'], aero['--radius-md']], ['16px', '12px', '8px']);
    assert.equal(aero['--heading-color'], '#0b3a5c');

    // Only bevel themes that ask for one draw a second ring.
    for (const [key, theme] of Object.entries(all)) {
        if (key === 'windows-95') continue;
        assert.equal(theme['--bevel-raised-inner'], '0 0 #0000', key);
        assert.equal(theme['--bevel-sunken-inner'], '0 0 #0000', key);
    }
});

test('a solid menu highlight keeps its row text legible', () => {
    for (const [key, theme] of Object.entries(themes())) {
        if (!isHex(theme['--option-active-bg'])) continue;
        for (const role of ['--option-active-text', '--option-active-label']) {
            assert.ok(contrast(theme[role], theme['--option-active-bg']) >= 4.5, `${key} ${role}`);
        }
    }
    // Rows on a translucent highlight keep the theme's own colours.
    const steam = themes()['steam-green'];
    assert.equal(steam['--option-active-text'], steam['--text-primary']);
    assert.equal(steam['--option-active-label'], steam['--selected-text']);
});

test('new colour roles follow the variable they split from, after its override', () => {
    const all = themes();
    // Classic Light hand-picks its secondary surface; fields keep using it.
    assert.equal(all['classic-light']['--bg-secondary'], '#f7f9f9');
    assert.equal(all['classic-light']['--field-bg'], '#f7f9f9');
    for (const [key, theme] of Object.entries(all)) {
        if (theme._edge !== 'flat') continue;
        assert.equal(theme['--field-bg'], theme['--bg-secondary'], key);
        assert.equal(theme['--heading-color'], theme['--text-primary'], key);
        assert.equal(theme['--selected-text'], theme['--text-primary'], key);
        // The primary button is the inverted fill unless a theme says otherwise.
        assert.equal(theme['--button-primary-bg'], theme['--text-primary'], key);
        assert.equal(theme['--button-primary-text'], theme['--bg-primary'], key);
        assert.equal(theme['--button-border'], theme['--input-border'], key);
        assert.equal(theme['--button-bg'], 'transparent', key);
    }
    // Classic Light's hand-picked input border carries through to buttons.
    assert.equal(all['classic-light']['--button-border'], '#cfd9de');
    assert.equal(all['steam-green']['--button-primary-bg'], '#4c5844');
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
    assert.equal(root.dataset.theme, 'steam-green');
    assert.equal(css.get('--radius-xl'), '2px');
    assert.equal(css.get('--selected-text'), '#dccd62');
    app.applyTheme('frutiger-aero');
    assert.equal(root.dataset.edge, 'gloss');
    assert.equal(css.get('--radius-xl'), '16px');
    app.applyTheme('dracula');
    assert.equal(root.dataset.edge, 'flat');
    assert.equal(root.dataset.theme, 'dracula');
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

test('Settings lists the recreated interfaces under Special, after light and dark', () => {
    const context = browserContext();
    const { nookThemeGroups } = loadScripts(context, appScriptsWith('settings.js'), '({nookThemeGroups})');
    // Copied out of the page's realm so deepEqual compares plain arrays.
    const groups = Object.fromEntries(Array.from(nookThemeGroups(), group => [group.label, Array.from(group.themes, item => item.key)]));
    assert.deepEqual(Object.keys(groups), ['Light', 'Dark', 'Special']);
    assert.deepEqual(groups.Special, ['steam-green', 'windows-95', 'frutiger-aero']);
    assert.ok(groups.Light.includes('classic-light') && groups.Dark.includes('classic-dark'));
    for (const removed of ['vesper', 'steam-modern']) {
        assert.ok(!Object.values(groups).flat().includes(removed), removed);
    }
});

test('theme samples in Settings use each theme\'s own corners', () => {
    const context = browserContext();
    const { tweetApp, nookThemeTileStyle } = loadScripts(context, appScriptsWith('settings.js'), '({tweetApp, nookThemeTileStyle})');
    const app = immediateComponent(tweetApp());
    app.applyTheme('steam-green');
    assert.match(nookThemeTileStyle('dracula', app.THEMES.dracula, app), /--radius-lg:12px;.*--radius-full:9999px/);
    assert.match(nookThemeTileStyle('steam-green', app.THEMES['steam-green'], app), /--radius-lg:2px;.*--radius-full:2px/);
});

test('shared controls load before the feature stylesheets and theme structure after them', () => {
    const shell = fs.readFileSync(path.join(ROOT, 'tweetnook', 'web', 'index.html'), 'utf8');
    const sheets = [...shell.matchAll(/<link rel="stylesheet" href="\/static\/css\/([^"]+)">/g)].map(m => m[1]);
    assert.deepEqual(sheets.slice(0, 3), ['tailwind.css', 'styles.css', 'controls.css']);
    assert.deepEqual(sheets.slice(-3), ['theme-structure.css', 'theme-windows-95.css', 'theme-gloss.css']);
});

run();
