/**
 * Settings → Configuration: loading the server's settings and their schema, which
 * fields show, and saving, resetting and restoring them.
 */

function configMethods() {
    return {
        nookConfigSaved: false,
        nookConfigResets: [],

        configSchema: null,
        configData: null,
        configOriginal: null,
        configDefaults: null,
        configExplicit: [],
        configSaving: false,
        showAdvancedConfig: false,

        async fetchConfig() {
            try {
                const [resSchema, resConfig, resDefaults] = await Promise.all([
                    fetch('/api/config/schema'),
                    fetch('/api/config'),
                    fetch('/api/config/defaults')
                ]);
                if (resSchema.ok && resConfig.ok && resDefaults.ok) {
                    this.configSchema = await resSchema.json();
                    const config = await resConfig.json();
                    this.configDefaults = await resDefaults.json();
                    this.configData = JSON.parse(JSON.stringify(config.values));
                    this.configOriginal = JSON.parse(JSON.stringify(config.values));
                    this.configExplicit = config.explicit;
                }
            } catch (e) {
                console.error("Error fetching config", e);
            }
        },

        isFieldVisible(section, key) {
            const fullKey = `${section}.${key}`;
            if (!this.configSchema || !this.configData) return false;
            if (this.configSchema.blacklist.includes(fullKey)) return false;
            if (this.configSchema.whitelist.includes(fullKey)) return true;
            return this.showAdvancedConfig;
        },
        
        getFieldType(section, key) {
            if (!this.configSchema || !this.configData) return 'text';
            const fullKey = `${section}.${key}`;
            if (this.configSchema.types && this.configSchema.types[fullKey]) return this.configSchema.types[fullKey];
            
            const val = this.configData[section][key];
            if (typeof val === 'boolean') return 'boolean';
            if (typeof val === 'number') return 'number';
            return 'text';
        },
        
        isFieldFullWidth(section, key) {
            const fullKey = `${section}.${key}`;
            if (this.configSchema && this.configSchema.full_width && this.configSchema.full_width.includes(fullKey)) return true;
            return false;
        },

        isConfigExplicit(section, key) {
            return this.configExplicit.includes(`${section}.${key}`);
        },

        changedConfigValues() {
            const changes = {};
            if (!this.configData || !this.configOriginal) return changes;
            Object.entries(this.configData).forEach(([section, fields]) => {
                Object.entries(fields).forEach(([key, value]) => {
                    const original = this.configOriginal[section][key];
                    if (JSON.stringify(value) !== JSON.stringify(original)) {
                        changes[`${section}.${key}`] = value;
                    }
                });
            });
            return changes;
        },

        async submitConfigChanges(changes) {
            if (Object.keys(changes).length === 0) return true;
            this.configSaving = true;
            try {
                const res = await fetch('/api/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ changes })
                });
                if (!res.ok) {
                    alert("Failed to save config: " + await res.text());
                    return false;
                }
                const config = await res.json();
                if ('web.fetch_avatars' in changes) {
                    avatarRevision = Date.now().toString(36);
                    try { localStorage.setItem(AVATAR_REVISION_KEY, avatarRevision); } catch (_) { /* Storage can be disabled. */ }
                }
                this.configData = JSON.parse(JSON.stringify(config.values));
                this.configOriginal = JSON.parse(JSON.stringify(config.values));
                this.configExplicit = config.explicit;
                return true;
            } catch (e) {
                console.error("Error saving config", e);
                alert("Error saving config.");
                return false;
            } finally {
                this.configSaving = false;
            }
        },
        
        async saveConfig() {
            const changes = this.changedConfigValues();
            if (Object.keys(changes).length === 0) return;
            if (await this.submitConfigChanges(changes)) {
                const btn = document.getElementById('save-config-btn');
                if (btn) {
                    const old = btn.innerHTML;
                    btn.innerHTML = "Saved!";
                    btn.classList.replace('bg-[var(--accent-color)]', 'bg-green-600');
                    setTimeout(() => {
                        btn.innerHTML = old;
                        btn.classList.replace('bg-green-600', 'bg-[var(--accent-color)]');
                    }, 2000);
                }
            }
        },

        async resetConfigField(section, key) {
            await this.submitConfigChanges({ [`${section}.${key}`]: null });
        },

        async restoreConfigDefaults() {
            if(!confirm("Are you sure you want to restore default configuration settings? Your credentials and keys will be preserved.")) return;
            const preserved = new Set(['tagging.api_key', 'web.password_hash']);
            const resettable = this.configExplicit.filter(path => (
                !path.startsWith('auth.') && !preserved.has(path)
            ));
            const changes = Object.fromEntries(resettable.map(path => [path, null]));
            await this.submitConfigChanges(changes);
        },
    };
}
