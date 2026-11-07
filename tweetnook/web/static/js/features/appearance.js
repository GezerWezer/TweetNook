/**
 * Appearance: the theme, accent color, browser theme color and favicon, the font
 * picker (including fonts installed on this device) and text size, and the media
 * gallery style.
 */

function appearanceMethods() {
    return {
        darkMode: true,
        
        THEMES: THEMES,
        currentTheme: 'classic-light',
        currentAccent: null,

        fontFamily: 'system',
        installedFonts: [],
        fontDiscoveryBusy: false,
        fontDiscoveryMessage: '',
        fontPickerOpen: false,
        fontSize: 'default',

        mediaGallery: 'date', // 'grid' | 'carousel' | 'date'; browser-local appearance

        FONT_OPTIONS: [
            {key: 'system', label: 'System Default', family: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif'},
            {key: 'serif', label: 'System Serif', family: 'serif'},
            {key: 'mono', label: 'System Monospace', family: 'monospace'},
            {key: 'inter', label: 'Inter', family: '"Inter", sans-serif'},
            {key: 'roboto', label: 'Roboto', family: '"Roboto", sans-serif'},
            {key: 'jakarta', label: 'Plus Jakarta Sans', family: '"Plus Jakarta Sans", sans-serif'},
            {key: 'outfit', label: 'Outfit', family: '"Outfit", sans-serif'},
            {key: 'nunito', label: 'Nunito', family: '"Nunito", sans-serif'},
            {key: 'jetbrains', label: 'JetBrains Mono', family: '"JetBrains Mono", monospace'}
        ],

        setMediaGallery(style) {
            const next = ['grid', 'carousel', 'date'].includes(style) ? style : 'date';
            if (next === this.mediaGallery) return;
            this.mediaGallery = next;
            localStorage.setItem('tvx-media-gallery', next);
            // Detail/reply templates react to the preference. The feed is rendered
            // outside Alpine, so refresh its cards explicitly as well.
            this.renderFeedList(this.tweets);
        },

        useCarouselForTweet(tweet) {
            if (this.mediaGallery !== 'date') return this.mediaGallery === 'carousel';
            const source = this.getRetweet(tweet) || tweet;
            const raw = source?.raw_json || source;
            const created = raw?.legacy?.created_at || raw?.created_at || source?.created_at;
            // Unknown dates keep the grid; collection and archive dates never
            // tell us when the author composed the attachment layout.
            return typeof created === 'string' && Date.parse(created) >= MEDIA_CAROUSEL_INTRODUCED_AT;
        },

        applyTheme(key, persist = true) {
            const theme = THEMES[key];
            if (!theme) return;
            this.currentTheme = key;
            const painted = paintTheme(key);
            this.darkMode = painted.darkMode;
            this.currentAccent = painted.accent;
            this.updateBrowserTheme();

            if (persist) localStorage.setItem('tvx-theme', key);
            this.updateFavicon();
        },

        updateBrowserTheme(progress = this.activityShadeProgress) {
            progress = Math.max(0, Math.min(1, Number(progress) || 0));
            this.activityShadeProgress = progress;
            const background = THEMES[this.currentTheme]?.['--bg-primary'] || '#000000';
            // Match the Jobs scrim composited over the selected theme's background.
            const tint = [91, 112, 131];
            const alpha = .4 * progress;
            const color = progress ? '#' + tint.map((channel, index) => {
                const base = parseInt(background.slice(1 + index * 2, 3 + index * 2), 16);
                return Math.round(channel * alpha + base * (1 - alpha)).toString(16).padStart(2, '0');
            }).join('') : background;
            // Keep frame-by-frame paint values on their surfaces. Inherited root
            // variables force Safari to restyle the entire archive on every tick.
            const scrim = document.querySelector('.nook-sync-scrim');
            const edge = document.querySelector('.nook-sync-edge');
            if (scrim) scrim.style.opacity = String(progress);
            if (edge) edge.style.backgroundColor = color;
            document.documentElement.style.backgroundColor = progress ? color : '';
            document.body.style.backgroundColor = progress ? color : '';
            document.querySelector('meta[name="theme-color"]')?.setAttribute('content', color);
        },

        setAccent(accent) {
            this.currentAccent = accent.color;
            const root = document.documentElement;
            root.style.setProperty('--accent-color', accent.color);
            root.style.setProperty('--accent-hover', accent.hover);
            root.style.setProperty('--accent-text', accent.text);
            localStorage.setItem('tvx-accent-' + this.currentTheme, accent.color);
            this.updateFavicon();
        },

        // Redraws the tab icon in the active theme: corner in the accent,
        // bookmark in the text color, on a tile of the page background.
        updateFavicon() {
            const link = document.querySelector('link[rel="icon"]');
            if (!link) return;
            const style = document.documentElement.style;
            const bg = style.getPropertyValue('--bg-primary') || '#ffffff';
            const text = style.getPropertyValue('--text-primary') || '#0f1419';
            const accent = style.getPropertyValue('--accent-color') || '#1d9bf0';
            const svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
                + `<rect width="32" height="32" rx="7.5" fill="${bg}"/>`
                + `<path d="M8 8v12a4 4 0 0 0 4 4h12" fill="none" stroke="${accent}" stroke-width="4" stroke-linecap="round"/>`
                + `<path d="M14 7.5A1.5 1.5 0 0 1 15.5 6h7A1.5 1.5 0 0 1 24 7.5V18l-5-3-5 3z" fill="${text}"/>`
                + '</svg>';
            link.href = 'data:image/svg+xml,' + encodeURIComponent(svg);
        },

        canDiscoverFonts() {
            return window.isSecureContext && typeof window.queryLocalFonts === 'function';
        },

        fontChoices() {
            const choices = this.FONT_OPTIONS.map(font => ({
                ...font,
                label: ['system', 'serif', 'mono'].includes(font.key)
                    ? font.label : `${font.label} (if installed)`,
            }));
            const families = new Set(this.installedFonts);
            if (this.fontFamily.startsWith('local:')) families.add(this.fontFamily.slice(6));
            for (const family of [...families].sort((a, b) => a.localeCompare(b))) {
                choices.push({ key: `local:${family}`, label: family, family: this.localFontFamily(family) });
            }
            return choices;
        },

        localFontFamily(name) {
            // Escape a CSS string, keeping arbitrary font names out of CSS syntax.
            const escaped = String(name).replace(/[\\"\n\r\f]/g, character =>
                `\\${character.charCodeAt(0).toString(16)} `);
            return `"${escaped}", sans-serif`;
        },

        async discoverFonts() {
            if (this.fontDiscoveryBusy) return;
            if (!this.canDiscoverFonts()) {
                this.fontDiscoveryMessage = 'Font discovery is unavailable here. Choose one of the local fallback fonts.';
                return;
            }
            this.fontDiscoveryBusy = true;
            this.fontDiscoveryMessage = '';
            try {
                const fonts = await window.queryLocalFonts();
                this.installedFonts = [...new Set(fonts.map(font => font.family).filter(Boolean))];
                this.fontDiscoveryMessage = `${this.installedFonts.length} font families available on this device.`;
            } catch (error) {
                this.fontDiscoveryMessage = error.name === 'NotAllowedError'
                    ? 'Font access was not granted. The local fallback fonts are still available.'
                    : 'Could not list installed fonts. The local fallback fonts are still available.';
            } finally {
                this.fontDiscoveryBusy = false;
            }
        },

        selectFont(key) {
            const font = this.fontChoices().find(font => font.key === key);
            if (font) this.setFont(font.key, font.family);
            this.fontPickerOpen = false;
            this.$refs?.fontTrigger?.focus();
        },

        selectedFontChoice() {
            return this.fontChoices().find(font => font.key === this.fontFamily) || this.FONT_OPTIONS[0];
        },

        openFontPicker(edge = 'selected') {
            this.fontPickerOpen = true;
            this.$nextTick(() => this.focusFontOption(edge));
        },

        focusFontOption(direction) {
            const options = [...this.$refs.fontOptions.querySelectorAll('[role="option"]')];
            if (!options.length) return;
            const current = options.indexOf(document.activeElement);
            let index;
            if (direction === 'first') index = 0;
            else if (direction === 'last') index = options.length - 1;
            else if (direction === 'selected') index = this.fontChoices().findIndex(font => font.key === this.fontFamily);
            else index = (current + direction + options.length) % options.length;
            options[Math.max(0, index)].focus();
        },

        setFont(key, family) {
            this.fontFamily = key;
            document.body.style.fontFamily = family;
            localStorage.setItem('tvx-font', key);
            localStorage.setItem('tvx-font-family', family);
        },

        setFontSize(key, px) {
            this.fontSize = key;
            document.documentElement.style.setProperty('--font-size-base', px);
            localStorage.setItem('tvx-font-size', key);
            localStorage.setItem('tvx-font-size-px', px);
        },
    };
}
