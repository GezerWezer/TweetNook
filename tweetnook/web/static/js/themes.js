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
 * Optional: overrides object for any CSS variable that needs a hand-picked value.
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

function _generateTheme(seed) {
    const { bg, text, muted, accent, danger } = seed;
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
    };

    if (seed.overrides) Object.assign(vars, seed.overrides);

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
        accent: '#1d9bf0', danger: '#f91880',
        accents: [
            { name: 'Blue', color: '#1d9bf0' }, { name: 'Yellow', color: '#ffd400' },
            { name: 'Pink', color: '#f91880' }, { name: 'Purple', color: '#7856ff' },
            { name: 'Orange', color: '#ff7a00' }, { name: 'Green', color: '#00ba7c' }
        ]
    },
    'classic-light': {
        _name: 'Classic Light',
        bg: '#ffffff', text: '#0f1419', muted: '#536471',
        accent: '#1d9bf0', danger: '#f91880',
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
        bg: '#3e4637',         
        text: '#d8ded3',      
        muted: '#a0aa95',     
        accent: '#c4b550',     
        danger: '#b24b4b',    
        
        overrides: {
            // Background
            '--bg-primary': '#3e4637',         
            '--bg-secondary': '#4c5844',         
            '--bg-tertiary': '#5a6a50',         
            '--bg-header': '#282e22',         
            '--dropdown-bg': '#4c5844',        

            // Text & Icons
            '--text-primary': '#d8ded3',   
            '--text-secondary': '#a0aa95', 
            '--white-icon': '#d8ded3',  

            // Borders & Dividers
            '--border-color': '#282e22',        
            '--thread-line': '#282e22',         
            '--input-border': '#808080',    

            // Accents & Interactive
            '--accent-color': '#c4b550',         
            '--accent-hover': '#91863c',       
            '--accent-text': '#282e22',      
            '--hover-bg': 'rgba(90, 106, 80, 0.4)',

            // Danger
            '--danger-color': '#b24b4b',
            '--danger-hover': '#8c3b3b', 
            '--danger-text': '#d8ded3',

            // Scrollbars
            '--scrollbar-bg': '#282e22',        
            '--scrollbar-thumb': '#4c5844',       
            '--scrollbar-thumb-hover': '#5a6a50', 

            // Misc / Muted elements
            '--gray-700': '#75806f'           
        },
    },
    'steam-modern': {
        _name: 'Steam Modern',
        bg: '#1B2838',         
        text: '#DCDEDF',     
        muted: '#8B929A',    
        accent: '#1A9FFF',  
        danger: '#D94126',  
        
        overrides: {
            // Background Tiers
            '--bg-primary': '#1B2838',        
            '--bg-secondary': '#2A475E',      
            '--bg-tertiary': '#3D4450',       
            '--bg-header': '#171D25',         
            '--dropdown-bg': '#3D4450',       

            // Text & Icons
            '--text-primary': '#DCDEDF',      
            '--text-secondary': '#8B929A',    
            '--white-icon': '#DCDEDF',        

            // Borders & Dividers
            '--border-color': '#3D4450',      
            '--thread-line': '#2A475E',       
            '--input-border': '#4e697d',      

            // Accents & Interactive
            '--accent-color': '#1A9FFF',      
            '--accent-hover': '#00BBFF',      
            '--accent-text': '#FFFFFF',           
            
            '--hover-bg': 'rgba(103, 193, 245, 0.2)', 

            // Scrollbars
            '--scrollbar-bg': 'rgba(0, 0, 0, 0.2)', 
            '--scrollbar-thumb': '#2e5470',       
            '--scrollbar-thumb-hover': '#3d6c8d', 

            // Danger/Errors
            '--danger-color': '#D94126',
            '--danger-hover': '#EE563B',
            '--danger-text': '#FFFFFF',

            // Misc
            '--gray-700': '#67707B'     
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
    },
    'vesper': {
        _name: 'Vesper',
        bg: '#101010', text: '#cccccc', muted: '#777777',
        accent: '#ffc799', danger: '#ff5f5f'
    }
};

// ── Build full themes from seeds ──────────────────────────────────────

const THEMES = {};
for (const [key, seed] of Object.entries(THEME_SEEDS)) {
    THEMES[key] = { _name: seed._name, ..._generateTheme(seed) };
}
