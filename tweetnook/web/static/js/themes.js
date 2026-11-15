/**
 * Theme definitions and constants for the TweetNook Web UI.
 *
 * Each theme is defined as a compact seed (5 colors + optional overrides)
 * and expanded into full CSS variable sets at load time.
 *
 * To add a new theme, add a seed with:
 *   bg     – background color
 *   text   – primary text color
 *   muted  – secondary/muted text color
 *   accent – accent/link color
 *   danger – danger/like color
 *
 * Optional: destructive – Delete and other destructive actions; defaults to
 * danger, so set it when danger is a like color rather than a red.
 * Optional: overrides object for any CSS variable that needs a hand-picked value.
 *
 * Optional structure, for themes modelled on a UI that isn't X's:
 *   shape – 'rounded' (default) or 'square' corners (SHAPES below)
 *   edge  – 'flat' (default), 'bevel' or 'gloss'. Bevel draws raised
 *           controls and sunken fields from --edge-light and --edge-dark by
 *           the [data-edge] rules in theme-structure.css. A bevel theme can
 *           set --bevel-raised and --bevel-sunken (border colours) and
 *           --bevel-raised-inner and --bevel-sunken-inner (inset shadows)
 *           for a two-pixel edge. Gloss is Frutiger Aero's gel controls and
 *           glass, drawn by theme-gloss.css.
 *   font  – font stack for "Theme Default" in the font picker
 *   special – listed under Special in Settings rather than Light or Dark
 *
 * Painting also sets data-theme on <html> to the theme's key, for details
 * specific to one theme that no token expresses.
 */

// ── Color utilities (private) ─────────────────────────────────────────

function _hexToRgb(hex) {
    const h = hex.replace('#', '');
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}

function _rgbToHex(r, g, b) {
    return '#' + [r, g, b].map(x => Math.round(Math.min(255, Math.max(0, x))).toString(16).padStart(2, '0')).join('');
}

function _rgbToHsl(r, g, b) {
    r /= 255; g /= 255; b /= 255;
    const max = Math.max(r, g, b), min = Math.min(r, g, b);
    let h = 0, s = 0, l = (max + min) / 2;
    if (max !== min) {
        const d = max - min;
        s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
        if (max === r) h = ((g - b) / d + (g < b ? 6 : 0)) / 6;
        else if (max === g) h = ((b - r) / d + 2) / 6;
        else h = ((r - g) / d + 4) / 6;
    }
    return [h * 360, s * 100, l * 100];
}

function _hslToRgb(h, s, l) {
    h /= 360; s /= 100; l /= 100;
    if (s === 0) return [l * 255, l * 255, l * 255];
    const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
    const p = 2 * l - q;
    const hue2rgb = (t) => {
        if (t < 0) t++; if (t > 1) t--;
        if (t < 1 / 6) return p + (q - p) * 6 * t;
        if (t < 1 / 2) return q;
        if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
        return p;
    };
    return [hue2rgb(h + 1 / 3) * 255, hue2rgb(h) * 255, hue2rgb(h - 1 / 3) * 255];
}

function _luminance(hex) {
    const [r, g, b] = _hexToRgb(hex);
    return r * 0.299 + g * 0.587 + b * 0.114;
}

/** Shift lightness of a hex color by `amount` percentage points. */
function _adjustL(hex, amount) {
    const [r, g, b] = _hexToRgb(hex);
    let [h, s, l] = _rgbToHsl(r, g, b);
    l = Math.min(100, Math.max(0, l + amount));
    const [nr, ng, nb] = _hslToRgb(h, s, l);
    return _rgbToHex(nr, ng, nb);
}

/** Linear interpolation between two hex colors. w=0 → hex1, w=1 → hex2. */
function _mix(hex1, hex2, w) {
    const [r1, g1, b1] = _hexToRgb(hex1);
    const [r2, g2, b2] = _hexToRgb(hex2);
    return _rgbToHex(r1 + (r2 - r1) * w, g1 + (g2 - g1) * w, b1 + (b2 - b1) * w);
}

/** Hex color to rgba() string. */
function _rgba(hex, a) {
    const [r, g, b] = _hexToRgb(hex);
    return `rgba(${r},${g},${b},${a})`;
}

/**
 * Derive a surface/border color from bg by shifting lightness.
 * If bg is nearly achromatic (e.g. pure black/white), borrows the hue
 * from the muted color so derived surfaces pick up the theme's tint.
 */
function _surface(bg, muted, delta) {
    const [r, g, b] = _hexToRgb(bg);
    let [h, s, l] = _rgbToHsl(r, g, b);
    if (s < 5) {
        const [mr, mg, mb] = _hexToRgb(muted);
        const [mh, ms] = _rgbToHsl(mr, mg, mb);
        h = mh;
        s = Math.min(ms, 8);
    }
    l = Math.min(100, Math.max(0, l + delta));
    const [nr, ng, nb] = _hslToRgb(h, s, l);
    return _rgbToHex(nr, ng, nb);
}

// ── Theme generator ───────────────────────────────────────────────────

const SYSTEM_FONT = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif';

// Corner radii by element size (styles.css). Square keeps a hairline radius,
// as the classic desktop toolkits did, rather than a hard 0.
const SHAPES = {
    rounded: { '--radius-full': '9999px', '--radius-xl': '16px', '--radius-lg': '12px', '--radius-md': '8px' },
    square:  { '--radius-full': '2px', '--radius-xl': '2px', '--radius-lg': '2px', '--radius-md': '2px' },
};

function _generateTheme(seed) {
    const { bg, text, muted, accent, danger } = seed;
    const destructive = seed.destructive || danger;
    const dark = _luminance(bg) < 128;

    const vars = {
        '--bg-primary':            bg,
        '--bg-secondary':          _surface(bg, muted, dark ? 8 : -5),
        '--bg-tertiary':           _rgba(text, dark ? 0.08 : 0.06),
        '--bg-header':             _rgba(bg, dark ? 0.9 : 0.85),
        '--text-primary':          text,
        '--text-secondary':        muted,
        '--border-color':          _surface(bg, muted, dark ? 12 : -12),
        '--hover-bg':              _rgba(dark ? '#ffffff' : '#000000', 0.03),
        '--scrollbar-bg':          bg,
        '--scrollbar-thumb':       _mix(bg, muted, 0.35),
        '--scrollbar-thumb-hover': _mix(bg, muted, 0.5),
        '--thread-line':           _surface(bg, muted, dark ? 14 : -14),
        '--input-border':          _surface(bg, muted, dark ? 12 : -12),
        '--white-icon':            text,
        '--dropdown-bg':           bg,
        '--gray-700':              _mix(bg, muted, 0.5),
        '--accent-color':          accent,
        '--accent-hover':          _adjustL(accent, -8),
        '--accent-text':           _luminance(accent) > 150 ? '#000000' : '#ffffff',
        '--danger-color':          danger,
        '--danger-hover':          _adjustL(danger, -8),
        '--danger-text':           _luminance(danger) > 150 ? '#000000' : '#ffffff',
        '--destructive-color':     destructive,
        '--destructive-hover':     _adjustL(destructive, -8),
        '--destructive-text':      _luminance(destructive) > 150 ? '#000000' : '#ffffff',
        // Only drawn when the theme sets edge: 'bevel'.
        '--edge-light':            _surface(bg, muted, dark ? 16 : 8),
        '--edge-dark':             _surface(bg, muted, dark ? -8 : -24),
        // The bevel's border colours (top right bottom left), resolved
        // against the edge colours on <html>, and an optional second ring
        // inside them; a single-pixel bevel leaves the ring empty.
        '--bevel-raised':          'var(--edge-light) var(--edge-dark) var(--edge-dark) var(--edge-light)',
        '--bevel-sunken':          'var(--edge-dark) var(--edge-light) var(--edge-light) var(--edge-dark)',
        '--bevel-raised-inner':    '0 0 #0000',
        '--bevel-sunken-inner':    '0 0 #0000',
        // Keyboard focus on bevelled controls, and the size of the blocks a
        // bevelled meter is built from.
        '--bevel-focus-style':     'solid',
        '--bevel-focus-offset':    '0px',
        '--meter-block-width':     '5px',
        '--meter-block-gap':       '3px',
        // Menu rows in a bevelled theme; X's tall touch rows by default.
        '--bevel-option-height':   '44px',
        '--bevel-option-pad':      '10px 16px',
        '--bevel-option-weight':   '700',
        // Set on every theme so switching away from one that overrides it
        // doesn't leave its value on <html>.
        '--divider-light':         _surface(bg, muted, dark ? 16 : 8),
        '--font-ui':               seed.font || SYSTEM_FONT,
        // Buttons (controls.css). Secondary buttons are outlined on whatever
        // they sit on; the primary fill derives from the theme's own text.
        '--button-bg':             'transparent',
        '--button-primary-hover-bg': 'color-mix(in srgb, var(--button-primary-bg) 88%, var(--bg-primary))',
        // Menus and listboxes (controls.css): X's soft halo.
        '--popover-shadow':        '0 0 15px 0 rgba(101, 119, 134, .2), 0 0 3px 1px rgba(101, 119, 134, .15)',
        // Dialogs sit on a dimmed page with no shadow of their own, as X's do.
        '--backdrop-bg':           'rgba(91, 112, 131, .4)',
        // A real shadow rather than none, so a bevel can list its inner ring
        // in front of it.
        '--dialog-shadow':         '0 0 #0000',
        ...SHAPES[seed.shape || 'rounded'],
    };
    // Plain properties, not CSS variables; paintTheme sets the attribute.
    vars._edge = seed.edge || 'flat';

    if (seed.overrides) Object.assign(vars, seed.overrides);

    // Roles a theme can separate from broader ones. Unless overridden, each
    // follows the variable its elements used before they had their own role,
    // after that variable's own override.
    // The primary button is X's inverted fill; a theme that wants an accent
    // button sets --button-primary-bg and --button-primary-text.
    const roles = {
        '--field-bg': '--bg-secondary', '--heading-color': '--text-primary', '--selected-text': '--text-primary',
        '--button-primary-bg': '--text-primary', '--button-primary-text': '--bg-primary',
        '--button-text': '--text-primary', '--button-border': '--input-border', '--button-hover-bg': '--bg-tertiary',
        '--popover-bg': '--dropdown-bg', '--option-active-bg': '--bg-tertiary', '--dialog-bg': '--bg-primary',
        '--option-active-text': '--text-primary', '--option-active-label': '--selected-text',
        '--scrollbar-track': '--scrollbar-bg', '--scrollbar-arrow': '--text-secondary',
        '--bevel-meter-bg': '--field-bg',
    };
    for (const [role, base] of Object.entries(roles)) {
        if (!(seed.overrides && role in seed.overrides)) vars[role] = vars[base];
    }
    // The light half of an engraved (disabled) bevelled label.
    if (!(seed.overrides && '--disabled-shadow' in seed.overrides)) {
        vars['--disabled-shadow'] = `color-mix(in srgb, ${vars['--text-secondary']} 60%, ${vars['--bg-secondary']})`;
    }

    // Pre-compute accent variants for themes with selectable accents
    if (seed.accents) {
        vars._accents = seed.accents.map(a => ({
            name: a.name,
            color: a.color,
            hover: _adjustL(a.color, -8),
            text: _luminance(a.color) > 150 ? '#000000' : '#ffffff',
        }));
    }

    return vars;
}

// ── Theme seeds (5 colors each + optional overrides) ──────────────────

const THEME_SEEDS = {
    'classic-dark': {
        _name: 'Classic Dark',
        bg: '#000000', text: '#e7e9ea', muted: '#71767b',
        accent: '#1d9bf0', danger: '#f91880', destructive: '#f4212e',
        accents: [
            { name: 'Blue', color: '#1d9bf0' }, { name: 'Yellow', color: '#ffd400' },
            { name: 'Pink', color: '#f91880' }, { name: 'Purple', color: '#7856ff' },
            { name: 'Orange', color: '#ff7a00' }, { name: 'Green', color: '#00ba7c' }
        ]
    },
    'classic-light': {
        _name: 'Classic Light',
        bg: '#ffffff', text: '#0f1419', muted: '#536471',
        accent: '#1d9bf0', danger: '#f91880', destructive: '#f4212e',
        // The measured X light palette (design.md).
        overrides: {
            '--bg-secondary': '#f7f9f9', '--bg-header': 'rgba(255,255,255,.92)',
            '--border-color': '#eff3f4', '--input-border': '#cfd9de',
        },
        accents: [
            { name: 'Blue', color: '#1d9bf0' }, { name: 'Yellow', color: '#ffd400' },
            { name: 'Pink', color: '#f91880' }, { name: 'Purple', color: '#7856ff' },
            { name: 'Orange', color: '#ff7a00' }, { name: 'Green', color: '#00ba7c' }
        ]
    },
    'dracula': {
        _name: 'Dracula',
        bg: '#282a36', text: '#f8f8f2', muted: '#6272a4',
        accent: '#bd93f9', danger: '#ff5555',
        accents: [
            { name: 'Purple', color: '#bd93f9' }, { name: 'Cyan', color: '#8be9fd' },
            { name: 'Green', color: '#50fa7b' }, { name: 'Orange', color: '#ffb86c' },
            { name: 'Pink', color: '#ff79c6' }, { name: 'Yellow', color: '#f1fa8c' }
        ]
    },
    'flexoki-dark': {
        _name: 'Flexoki Dark',
        bg: '#100F0F', text: '#CECDC3', muted: '#878580',
        accent: '#3AA99F', danger: '#D14D41',
        accents: [
            { name: 'Teal', color: '#3AA99F' }, { name: 'Magenta', color: '#CE5D97' },
            { name: 'Yellow', color: '#D0A215' }, { name: 'Blue', color: '#4385BE' },
            { name: 'Red', color: '#D14D41' }, { name: 'Purple', color: '#8B7EC8' },
            { name: 'Green', color: '#879A39' }, { name: 'Orange', color: '#DA702C' }
        ]
    },
    'flexoki-light': {
        _name: 'Flexoki Light',
        bg: '#FFFCF0', text: '#100F0F', muted: '#6F6E69',
        accent: '#24837B', danger: '#AF3029',
        accents: [
            { name: 'Teal', color: '#24837B' }, { name: 'Magenta', color: '#A02F6F' },
            { name: 'Yellow', color: '#AD8301' }, { name: 'Blue', color: '#205EA6' },
            { name: 'Red', color: '#AF3029' }, { name: 'Purple', color: '#5E409D' },
            { name: 'Green', color: '#66800B' }, { name: 'Orange', color: '#BC5215' }
        ]
    },
    'rose-pine': {
        _name: 'Rosé Pine',
        bg: '#191724', text: '#e0def4', muted: '#908caa',
        accent: '#c4a7e7', danger: '#eb6f92',
        accents: [
            { name: 'Iris', color: '#c4a7e7' }, { name: 'Love', color: '#eb6f92' },
            { name: 'Gold', color: '#f6c177' }, { name: 'Rose', color: '#ebbcba' },
            { name: 'Pine', color: '#31748f' }, { name: 'Foam', color: '#9ccfd8' }
        ]
    },
    'gruvbox': {
        _name: 'Gruvbox Dark',
        bg: '#282828', text: '#ebdbb2', muted: '#a89984',
        accent: '#fe8019', danger: '#fb4934',
        accents: [
            { name: 'Orange', color: '#fe8019' }, { name: 'Yellow', color: '#d79921' },
            { name: 'Green', color: '#98971a' }, { name: 'Blue', color: '#458588' },
            { name: 'Purple', color: '#b16286' }, { name: 'Aqua', color: '#689d6a' }, 
            { name: 'Red', color: '#cc241d' }
        ]
    },
    'gruvbox-light': {
        _name: 'Gruvbox Light',
        bg: '#f2e5bc', text: '#3c3836', muted: '#7c6f64',
        accent: '#d65d0e', danger: '#9d0006',
        accents: [
            { name: 'Orange', color: '#d65d0e' }, { name: 'Yellow', color: '#b57614' },
            { name: 'Green', color: '#79740e' }, { name: 'Blue', color: '#076678' },
            { name: 'Purple', color: '#8f3f71' }, { name: 'Aqua', color: '#427b58' }
        ]
    },
    'aura-dark': {
        _name: 'Aura Dark',
        bg: '#15141b', text: '#ede0d4', muted: '#a2a2a2',
        accent: '#82e2ff', danger: '#ff7373'
    },
    'steam-green': {
        _name: 'Steam Green',
        special: true,
        // The 2003-era Steam client: olive panels around darker content
        // wells, bevelled controls, Tahoma, and yellow reserved for selected
        // and heading text.
        bg: '#3e4637', text: '#d8ded3', muted: '#a0aa95',
        accent: '#c4b550', danger: '#de7a68',
        shape: 'square', edge: 'bevel',
        font: 'Tahoma, Verdana, "Segoe UI", sans-serif',
        overrides: {
            // Window chrome and raised controls; content sits in the darker bg.
            '--bg-secondary': '#4c5844',
            '--bg-header': '#4c5844',
            '--dropdown-bg': '#4c5844',
            '--divider-light': '#5a6a50',
            '--field-bg': '#3e4637',
            // Lines inside the dark wells are the skin's shadow colour; a
            // softer olive barely shows against the well.
            '--border-color': '#282e22',
            '--thread-line': '#5a6a50',
            '--input-border': '#282e22',
            // The skin's BorderBright is a neutral grey, not a lighter olive.
            '--edge-light': '#808080',
            '--edge-dark': '#282e22',
            '--heading-color': '#dccd62',
            '--selected-text': '#dccd62',
            // Buttons are raised panels, not fills; the bevel draws the edge
            // and hover lights the label instead of the face.
            '--button-primary-bg': '#4c5844',
            '--button-primary-text': '#ffffff',
            '--button-text': '#ffffff',
            '--button-primary-hover-bg': '#4c5844',
            '--button-bg': '#4c5844',
            '--button-hover-bg': '#4c5844',
            // Menus are raised windows with a hard drop, not a soft halo.
            '--popover-shadow': '0 2px 0 rgba(0, 0, 0, .25)',
            '--dialog-shadow': '0 2px 0 rgba(0, 0, 0, .25)',
            // X's blue-grey dimming turns the olive cold; dim it neutrally.
            '--backdrop-bg': 'rgba(0, 0, 0, .45)',
            // Yellow darkened toward the bg loses contrast; hover brightens it.
            '--accent-hover': '#d9ca66',
            '--accent-text': '#282e22',
            '--hover-bg': 'rgba(216, 222, 211, 0.04)',
            // Raised panel-coloured thumb in a lighter gutter.
            '--scrollbar-bg': '#5a6a50',
            '--scrollbar-thumb': '#4c5844',
            '--scrollbar-thumb-hover': '#4c5844',
            '--gray-700': '#5a6a50',
        },
    },
    'windows-95': {
        _name: 'Windows 95',
        special: true,
        // The classic Windows desktop: grey 3D window faces with two-pixel
        // bevels around white content and fields, black text, and navy
        // reserved for selection, menus' highlighted row and progress.
        bg: '#ffffff', text: '#000000', muted: '#505050',
        accent: '#000080', danger: '#a00000',
        shape: 'square', edge: 'bevel',
        font: '"Microsoft Sans Serif", "MS Sans Serif", Tahoma, Arial, sans-serif',
        overrides: {
            // The window face; lists and fields are white wells set into it.
            '--bg-secondary': '#c0c0c0',
            '--bg-header': '#c0c0c0',
            '--dropdown-bg': '#c0c0c0',
            '--dialog-bg': '#c0c0c0',
            '--field-bg': '#ffffff',
            '--divider-light': '#ffffff',
            '--border-color': '#808080',
            '--thread-line': '#c0c0c0',
            '--input-border': '#808080',
            '--gray-700': '#808080',
            // Single-pixel lines are the classic button shadow over white;
            // controls add the inner ring of the two-pixel bevel, with black
            // outside a raised edge and inside a sunken one. Windows 95's
            // 3D light colour is the face grey itself (98 lightened it to
            // #dfdfdf), so the inner ring shows only its shadow side.
            '--edge-light': '#ffffff',
            '--edge-dark': '#808080',
            '--bevel-raised': '#ffffff #000000 #000000 #ffffff',
            '--bevel-raised-inner': 'inset 1px 1px 0 #c0c0c0, inset -1px -1px 0 #808080',
            '--bevel-sunken': '#808080 #ffffff #ffffff #808080',
            '--bevel-sunken-inner': 'inset 1px 1px 0 #000000, inset -1px -1px 0 #c0c0c0',
            '--bevel-focus-style': 'dotted',
            '--bevel-focus-offset': '-4px',
            '--disabled-shadow': '#ffffff',
            '--heading-color': '#000000',
            '--selected-text': '#000000',
            '--button-primary-bg': '#c0c0c0',
            '--button-primary-text': '#000000',
            '--button-primary-hover-bg': '#c0c0c0',
            '--button-text': '#000000',
            '--button-bg': '#c0c0c0',
            '--button-hover-bg': '#c0c0c0',
            // Menus are flat-shaded windows; the highlighted row is navy.
            '--popover-shadow': '0 0 #0000',
            '--dialog-shadow': '0 0 #0000',
            '--option-active-bg': '#000080',
            '--option-active-text': '#ffffff',
            '--option-active-label': '#ffffff',
            // Menu rows are short and set in the regular weight.
            '--bevel-option-height': '24px',
            '--bevel-option-pad': '3px 20px',
            '--bevel-option-weight': '400',
            '--backdrop-bg': 'rgba(0, 0, 0, .25)',
            '--accent-hover': '#1084d0',
            '--hover-bg': 'rgba(0, 0, 0, .04)',
            // The gutter is the classic white-and-grey dither, behind grey
            // buttons with black arrows.
            '--scrollbar-bg': '#c0c0c0',
            '--scrollbar-track': 'repeating-conic-gradient(#c0c0c0 0 25%, #ffffff 0 50%) 0 0 / 2px 2px',
            '--scrollbar-thumb': '#c0c0c0',
            '--scrollbar-thumb-hover': '#c0c0c0',
            '--scrollbar-arrow': '#000000',
            // Progress bars fill the grey face with navy chunks about two
            // thirds as wide as the bar is tall inside, as the 95 progress
            // control draws them.
            '--meter-block-width': '7px',
            '--meter-block-gap': '2px',
            '--bevel-meter-bg': '#c0c0c0',
        },
    },
    'frutiger-aero': {
        _name: 'Frutiger Aero',
        special: true,
        // The glossy, aquatic optimism of mid-2000s consumer tech: white
        // glass, gel capsules in aqua and lime, ocean-ink text. The page stays
        // a plain white reading surface; theme-gloss.css draws the gel.
        bg: '#ffffff', text: '#0f2a3d', muted: '#557286',
        accent: '#0a7cc1', danger: '#e0335f', destructive: '#c93420',
        shape: 'rounded', edge: 'gloss',
        // Frutiger, then the humanist faces of the period's devices: Myriad
        // on Apple's, Segoe UI on Windows, Lucida Grande on older Macs.
        font: 'Frutiger, "Frutiger Linotype", "Myriad Pro", "Segoe UI", "Lucida Grande", "Lucida Sans Unicode", "Helvetica Neue", Arial, sans-serif',
        overrides: {
            '--bg-secondary': '#eef8fd',
            '--bg-header': 'rgba(255, 255, 255, .9)',
            '--field-bg': '#ffffff',
            '--dropdown-bg': '#ffffff',
            '--border-color': '#d6eaf4',
            '--thread-line': '#a9d9f0',
            '--input-border': '#a9d0e5',
            '--gray-700': '#a3c3d4',
            '--divider-light': '#ffffff',
            '--heading-color': '#0b3a5c',
            '--selected-text': '#0b3a5c',
            // Solid faces for anything that can't take a gradient; the gloss
            // rules paint the gel over them.
            '--button-bg': '#ecf7fc',
            '--button-hover-bg': '#ddf3fd',
            '--button-text': '#0b3a5c',
            '--button-border': '#8fc3df',
            '--button-primary-bg': '#0877b8',
            '--button-primary-hover-bg': '#0a7cc1',
            '--button-primary-text': '#ffffff',
            '--option-active-bg': 'linear-gradient(#f4fbfe, #d6f0fb)',
            '--popover-shadow': '0 2px 4px rgba(10, 90, 140, .08), 0 10px 28px rgba(10, 90, 140, .18)',
            '--dialog-shadow': '0 0 0 4px rgba(255, 255, 255, .6), 0 4px 10px rgba(10, 90, 140, .12), 0 24px 60px rgba(10, 90, 140, .28)',
            '--backdrop-bg': 'rgba(8, 60, 95, .28)',
            '--hover-bg': 'rgba(30, 166, 228, .05)',
            '--accent-hover': '#0a6aa6',
            '--scrollbar-bg': '#f2f9fc',
            '--scrollbar-thumb': '#c4e3f3',
            '--scrollbar-thumb-hover': '#2db6f0',
        },
    },
    'monokai': {
        _name: 'Monokai',
        bg: '#272822', text: '#f8f8f2', muted: '#75715e',
        accent: '#a6e22e', danger: '#f92672',
        accents: [
            { name: 'Green', color: '#a6e22e' }, { name: 'Orange', color: '#fd971f' },
            { name: 'Blue', color: '#66d9ef' }, { name: 'Purple', color: '#ae81ff' }
        ]
    },
    'cyberpunk': {
        _name: 'Cyberpunk',
        bg: '#0e0b16', text: '#e0e0e0', muted: '#a0a0a0',
        accent: '#00ffcc', danger: '#ff003c',
        overrides: {
            '--border-color': '#4717f6', '--thread-line': '#4717f6',
            '--input-border': '#4717f6', '--scrollbar-thumb': '#4717f6',
            '--scrollbar-thumb-hover': '#a239ca'
        },
        accents: [
            { name: 'Teal', color: '#00ffcc' }, { name: 'Purple', color: '#a239ca' },
            { name: 'Pink', color: '#ff003c' }, { name: 'Yellow', color: '#fcee09' }
        ]
    },
    'synthwave': {
        _name: "Synthwave '84",
        bg: '#262335', text: '#ffffff', muted: '#8b8b99',
        accent: '#f92aad', danger: '#ff3366',
        accents: [
            { name: 'Pink', color: '#f92aad' }, { name: 'Cyan', color: '#36f9f6' },
            { name: 'Yellow', color: '#fede5d' }, { name: 'Orange', color: '#ff8b39' }
        ]
    }
};

// ── Build full themes from seeds ──────────────────────────────────────

const THEMES = {};
for (const [key, seed] of Object.entries(THEME_SEEDS)) {
    THEMES[key] = { _name: seed._name, ..._generateTheme(seed) };
}

// ── Paint the theme onto the root element ─────────────────────────────

// Sets the theme's variables, the saved accent and the light/dark scheme on
// <html>. Returns what the app component tracks about the painted theme.
function paintTheme(key) {
    const theme = THEMES[key];
    const root = document.documentElement;
    for (const [prop, val] of Object.entries(theme)) {
        if (prop.startsWith('--')) root.style.setProperty(prop, val);
    }
    const bgHex = theme['--bg-primary'] || '#000000';
    const r = parseInt(bgHex.slice(1, 3), 16), g = parseInt(bgHex.slice(3, 5), 16), b = parseInt(bgHex.slice(5, 7), 16);
    const darkMode = (r * 0.299 + g * 0.587 + b * 0.114) < 128;
    if (darkMode) root.classList.remove('light'); else root.classList.add('light');
    root.dataset.edge = theme._edge;
    // For the few details a theme draws that no token expresses, such as
    // Windows 95's title bars (theme-windows-95.css).
    root.dataset.theme = key;
    root.style.setProperty('color-scheme', darkMode ? 'dark' : 'light');

    let accent = theme['--accent-color'];
    if (theme._accents) {
        const saved = localStorage.getItem('tvx-accent-' + key);
        const match = saved ? theme._accents.find(a => a.color === saved) : null;
        if (match) {
            accent = match.color;
            root.style.setProperty('--accent-color', match.color);
            root.style.setProperty('--accent-hover', match.hover);
            root.style.setProperty('--accent-text', match.text);
        }
    }
    return { darkMode, accent };
}

// The theme initApp settles on: the saved one, else the system scheme in the
// demo, else classic light.
function startupThemeKey() {
    const saved = localStorage.getItem('tvx-theme') || localStorage.getItem('theme');
    if (saved && THEMES[saved]) return saved;
    if (window.TWEETNOOK_DEMO === true) {
        return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'classic-dark' : 'classic-light';
    }
    return 'classic-light';
}

// This script runs in <head> before the body is parsed, while the app only
// applies the theme once Alpine starts after the deferred scripts. Painting
// here keeps the stylesheet's black defaults off the first frames of a load.
try {
    const key = startupThemeKey();
    paintTheme(key);
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', THEMES[key]['--bg-primary']);
} catch { /* Storage can be blocked; initApp still applies the theme. */ }
