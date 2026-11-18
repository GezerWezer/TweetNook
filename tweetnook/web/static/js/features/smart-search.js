/**
 * Local CLIP/SigLIP Smart Search settings and indexing trigger.
 */

function smartSearchMethods() {
    return {
        smartSearchInstalled: false,
        smartSearch: null,
        smartSearchStats: null,
        smartSearchStatsLoading: false,
        smartSearchModels: [],
        smartSearchSaving: false,
        smartSearchError: '',

        async fetchSmartSearchStats() {
            this.smartSearchStatsLoading = true;
            try {
                const res = await fetch('/api/smart-search/stats');
                const data = await res.json().catch(() => ({}));
                if (res.ok && data.stats) {
                    this.smartSearchStats = data.stats;
                }
            } catch (_) {
                // Keep previous stats if background count fails.
            } finally {
                this.smartSearchStatsLoading = false;
            }
        },

        async fetchSmartSearch() {
            this.smartSearchError = '';
            try {
                const res = await fetch('/api/smart-search');
                const data = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(data.detail || 'Failed to load Smart Search settings');
                this.smartSearchInstalled = !!data.installed;
                this.smartSearch = data.values;
                this.smartSearchModels = Array.isArray(data.models) ? data.models : [];
                this.fetchSmartSearchStats();
            } catch (error) {
                this.smartSearchError = error.message || 'Failed to load Smart Search settings';
            }
        },

        async saveSmartSearch() {
            if (!this.smartSearch || this.smartSearchSaving) return;
            this.smartSearchSaving = true;
            this.smartSearchError = '';
            try {
                const res = await fetch('/api/smart-search', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ values: this.smartSearch }),
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(data.detail || 'Failed to save Smart Search settings');
                this.smartSearchInstalled = !!data.installed;
                this.smartSearch = data.values;
                this.smartSearchModels = Array.isArray(data.models) ? data.models : this.smartSearchModels;
                this.fetchSmartSearchStats();
            } catch (error) {
                this.smartSearchError = error.message || 'Failed to save Smart Search settings';
            } finally {
                this.smartSearchSaving = false;
            }
        },
    };
}
