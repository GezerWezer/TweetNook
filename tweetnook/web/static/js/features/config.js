/**
 * Settings → Configuration: loading the server's settings and their schema, which
 * field types, changed values, and the shared save request.
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

        
        getFieldType(section, key) {
            if (!this.configSchema || !this.configData) return 'text';
            const fullKey = `${section}.${key}`;
            if (this.configSchema.types && this.configSchema.types[fullKey]) return this.configSchema.types[fullKey];
            
            const val = this.configData[section][key];
            if (typeof val === 'boolean') return 'boolean';
            if (typeof val === 'number') return 'number';
            return 'text';
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
        


    };
}
