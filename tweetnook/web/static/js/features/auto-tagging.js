/**
 * Automated tagging settings: the Gemini connection and model, context and instructions,
 * the spending limit and its history, and a test run against one archived post.
 */

function automatedTaggingMethods() {
    return {
        automatedTaggingInstalled: false,
        automatedTagging: null,
        automatedTaggingOriginal: null,
        automatedTaggingContext: '',
        automatedTaggingContextItems: [],
        automatedTaggingContextInput: '',
        automatedTaggingInstructionItems: [],
        automatedTaggingInstructionInput: '',
        automatedTaggingModels: [],
        automatedTaggingModelCache: null,
        automatedTaggingModelCacheKey: '',
        automatedTaggingModelCacheExpiresAt: 0,
        automatedTaggingAccounting: null,
        automatedTaggingSaving: false,
        automatedTaggingLoadingModels: false,
        automatedTaggingSpendAmount: null,
        automatedTaggingSpendPeriod: 'day',
        showAutomatedTaggingSpendModal: false,
        automatedTaggingSpendRange: '30d',
        automatedTaggingError: '',
        automatedTaggingTweetId: '',
        automatedTaggingTweetLookingUp: false,
        automatedTaggingTweetLookupError: '',
        automatedTaggingSelectedTweet: null,
        automatedTaggingTesting: false,
        automatedTaggingTestResult: null,
        automatedTaggingTestError: '',

        automatedTaggingSpendPeriodDays(period) {
            return {
                day: 1,
                week: 7,
                month: 30,
                year: 365,
            }[period] || 1;
        },

        syncAutomatedTaggingSpendState(resetPeriod = true) {
            if (!this.automatedTagging) return;
            if (resetPeriod || !this.automatedTaggingSpendPeriod) {
                this.automatedTaggingSpendPeriod = 'day';
            }
            const daily = Number(this.automatedTagging.daily_spend_limit_usd);
            this.automatedTaggingSpendAmount = Number.isFinite(daily) && daily > 0
                ? Number((daily * this.automatedTaggingSpendPeriodDays(this.automatedTaggingSpendPeriod)).toFixed(6))
                : null;
        },

        setAutomatedTaggingSpendPeriod(period) {
            if (!this.automatedTagging) return;
            this.automatedTaggingSpendPeriod = period;
            const daily = Number(this.automatedTagging.daily_spend_limit_usd);
            this.automatedTaggingSpendAmount = Number.isFinite(daily) && daily > 0
                ? Number((daily * this.automatedTaggingSpendPeriodDays(period)).toFixed(6))
                : null;
        },

        setAutomatedTaggingSpendAmount(rawAmount) {
            if (!this.automatedTagging) return;
            if (rawAmount === '') {
                this.automatedTaggingSpendAmount = null;
                this.automatedTagging.daily_spend_limit_usd = null;
                return;
            }
            const amount = Number(rawAmount);
            this.automatedTaggingSpendAmount = Number.isFinite(amount) ? amount : null;
            this.automatedTagging.daily_spend_limit_usd = Number.isFinite(amount) && amount > 0
                ? Number((amount / this.automatedTaggingSpendPeriodDays(this.automatedTaggingSpendPeriod)).toFixed(6))
                : null;
        },

        openAutomatedTaggingSpendHistory() {
            this.automatedTaggingSpendRange = '30d';
            this.showAutomatedTaggingSpendModal = true;
            this.$nextTick(() => this.$refs.automatedTaggingSpendClose?.focus());
        },

        closeAutomatedTaggingSpendHistory() {
            this.showAutomatedTaggingSpendModal = false;
            this.$nextTick(() => this.$refs.automatedTaggingSpendButton?.focus());
        },

        automatedTaggingSpendRangeDays() {
            if (this.automatedTaggingSpendRange === 'lifetime') return null;
            return {
                '7d': 7,
                '30d': 30,
                '3m': 90,
                '1y': 365,
            }[this.automatedTaggingSpendRange] || 30;
        },

        automatedTaggingSpendHistory() {
            if (this.automatedTaggingSpendRange === 'lifetime') {
                return this.automatedTaggingAccounting?.lifetime_spend_history || [];
            }
            const history = this.automatedTaggingAccounting?.spend_history || [];
            return history.slice(-this.automatedTaggingSpendRangeDays());
        },

        automatedTaggingSpendChartData() {
            const history = this.automatedTaggingSpendHistory();
            const groupSize = history.length > 60 ? Math.ceil(history.length / 52) : 1;
            const grouped = [];
            for (let index = 0; index < history.length; index += groupSize) {
                const items = history.slice(index, index + groupSize);
                grouped.push({
                    date: items[0]?.date,
                    endDate: items.at(-1)?.date,
                    interval: items[0]?.interval || 'day',
                    estimated_cost_usd: items.reduce(
                        (sum, item) => sum + Number(item.estimated_cost_usd || 0),
                        0,
                    ),
                    requests: items.reduce((sum, item) => sum + Number(item.requests || 0), 0),
                });
            }
            return grouped;
        },

        automatedTaggingSpendRangeTotal() {
            const total = this.automatedTaggingSpendHistory().reduce(
                (sum, item) => sum + Number(item.estimated_cost_usd || 0),
                0,
            );
            return Number(total.toFixed(12));
        },

        automatedTaggingSpendRangeRequests() {
            return this.automatedTaggingSpendHistory().reduce(
                (sum, item) => sum + Number(item.requests || 0),
                0,
            );
        },

        automatedTaggingSpendBarHeight(item) {
            const maximum = Math.max(
                0,
                ...this.automatedTaggingSpendChartData().map(point => Number(point.estimated_cost_usd || 0)),
            );
            const value = Number(item?.estimated_cost_usd || 0);
            if (maximum <= 0 || value <= 0) return '0%';
            return `${Math.max((value / maximum) * 100, 3)}%`;
        },

        automatedTaggingSpendModels() {
            return Object.entries(this.automatedTaggingAccounting?.models || {})
                .map(([model, values]) => ({
                    model,
                    requests: Number(values.requests || 0),
                    estimated_cost_usd: Number(
                        values.estimated_cost_usd ?? values.cost_usd ?? 0,
                    ),
                }))
                .sort((left, right) => (
                    right.estimated_cost_usd - left.estimated_cost_usd
                    || right.requests - left.requests
                    || left.model.localeCompare(right.model)
                ));
        },

        automatedTaggingSpendTweetTypes() {
            const labels = {
                text_only: 'Text only',
                image: 'Image',
                video: 'Video',
                gif: 'GIF',
                mixed_media: 'Mixed media',
                other_media: 'Other media',
            };
            const order = Object.keys(labels);
            return Object.entries(this.automatedTaggingAccounting?.tweet_types || {})
                .map(([type, values]) => {
                    const cost = Number(values.estimated_cost_usd ?? values.cost_usd ?? 0);
                    const pricedTweets = Number(values.priced_tweets || 0);
                    const pricedRequests = Number(values.priced_requests || 0);
                    return {
                        type,
                        label: labels[type] || type,
                        requests: Number(values.requests || 0),
                        tweets: Number(values.tweets || 0),
                        media_items: Number(values.media_items || 0),
                        image_items: Number(values.image_items || 0),
                        video_items: Number(values.video_items || 0),
                        gif_items: Number(values.gif_items || 0),
                        estimated_cost_usd: cost,
                        average_tweet_usd: pricedTweets ? cost / pricedTweets : 0,
                        average_request_usd: pricedRequests ? cost / pricedRequests : 0,
                    };
                })
                .sort((left, right) => {
                    const leftIndex = order.indexOf(left.type);
                    const rightIndex = order.indexOf(right.type);
                    return (leftIndex < 0 ? order.length : leftIndex)
                        - (rightIndex < 0 ? order.length : rightIndex);
                });
        },

        automatedTaggingTweetTypeMediaLabel(row) {
            const parts = [
                [row?.image_items, 'image'],
                [row?.video_items, 'video'],
                [row?.gif_items, 'GIF'],
            ]
                .filter(([count]) => Number(count || 0) > 0)
                .map(([count, label]) => `${Number(count).toLocaleString()} ${label}${Number(count) === 1 ? '' : 's'}`);
            return parts.join(' · ') || (row?.media_items ? 'Unclassified media' : 'No media');
        },

        formatAutomatedTaggingUsd(value) {
            const amount = Number(value || 0);
            if (!Number.isFinite(amount) || amount === 0) return '$0.00';
            if (amount < 0.01) return `$${amount.toFixed(4)}`;
            return `$${amount.toFixed(2)}`;
        },

        formatAutomatedTaggingSpendDate(value, includeYear = false, monthOnly = false) {
            if (!value) return '';
            return new Date(`${value}T00:00:00Z`).toLocaleDateString('en-US', {
                month: 'short',
                day: monthOnly ? undefined : 'numeric',
                year: includeYear ? 'numeric' : undefined,
                timeZone: 'UTC',
            });
        },

        automatedTaggingSpendBarLabel(item) {
            const monthOnly = item?.interval === 'month';
            const first = this.formatAutomatedTaggingSpendDate(item?.date, monthOnly, monthOnly);
            const last = this.formatAutomatedTaggingSpendDate(item?.endDate, monthOnly, monthOnly);
            const dates = first === last ? first : `${first}–${last}`;
            return `${dates}: ${this.formatAutomatedTaggingUsd(item?.estimated_cost_usd)} across ${item?.requests || 0} requests`;
        },
        
        async fetchAutomatedTagging() {
            try {
                const response = await fetch('/api/automated-tagging');
                if (!response.ok) return;
                const data = await response.json();
                this.automatedTaggingInstalled = !!data.installed;
                this.automatedTagging = JSON.parse(JSON.stringify(data.values));
                this.automatedTaggingOriginal = JSON.parse(JSON.stringify(data.values));
                this.syncAutomatedTaggingSpendState();
                this.setAutomatedTaggingContextItems(data.values.tagging_context || []);
                this.setAutomatedTaggingInstructionItems(data.values.additional_instructions || '');
                if (this.automatedTagging.api_mode === 'free') {
                    this.automatedTagging.google_search = false;
                }
                this.automatedTaggingAccounting = data.accounting;
                if (!this.automatedTagging.api_key) {
                    this.automatedTaggingModels = [];
                    this.automatedTaggingModelCache = null;
                    this.automatedTaggingModelCacheKey = '';
                    this.automatedTaggingModelCacheExpiresAt = 0;
                }
            } catch (error) {
                console.error('Error fetching automated tagging settings', error);
            }
        },

        async loadAutomatedTaggingModels(forceRefresh = false) {
            if (!this.automatedTagging || this.automatedTaggingLoadingModels) return;
            const cacheKey = [
                this.automatedTagging.api_key || '',
                this.automatedTagging.api_mode,
                this.automatedTagging.processing_tier,
            ].join('|');
            if (
                !forceRefresh
                && this.automatedTaggingModelCacheKey === cacheKey
                && Date.now() < this.automatedTaggingModelCacheExpiresAt
            ) {
                this.automatedTaggingModels = [...(this.automatedTaggingModelCache || [])];
                return;
            }
            this.automatedTaggingLoadingModels = true;
            this.automatedTaggingError = '';
            try {
                const response = await fetch('/api/automated-tagging/models', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        api_key: this.automatedTagging.api_key,
                        api_mode: this.automatedTagging.api_mode,
                        processing_tier: this.automatedTagging.processing_tier,
                    }),
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.detail || 'Could not load Gemini models');
                this.automatedTaggingModels = data.models || [];
                this.automatedTaggingModelCache = [...this.automatedTaggingModels];
                this.automatedTaggingModelCacheKey = cacheKey;
                this.automatedTaggingModelCacheExpiresAt = Date.now() + 15 * 60 * 1000;
                const modelIds = this.automatedTaggingModels.map(model => model.id);
                if (!modelIds.includes(this.automatedTagging.model) && modelIds.length) {
                    this.automatedTagging.model = modelIds[0];
                }
            } catch (error) {
                this.automatedTaggingError = error.message;
            } finally {
                this.automatedTaggingLoadingModels = false;
            }
        },

        async saveAutomatedTagging() {
            if (!this.automatedTagging || this.automatedTaggingSaving) return;
            this.addAutomatedTaggingContext();
            this.addAutomatedTaggingInstruction();
            this.automatedTaggingSaving = true;
            this.automatedTaggingError = '';
            const values = JSON.parse(JSON.stringify(this.automatedTagging));
            values.tagging_context = [...this.automatedTaggingContextItems];
            values.additional_instructions = this.automatedTaggingInstructionItems.join('\n') || null;
            try {
                const response = await fetch('/api/automated-tagging', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ values }),
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.detail || 'Could not save settings');
                this.automatedTagging = JSON.parse(JSON.stringify(data.values));
                this.automatedTaggingOriginal = JSON.parse(JSON.stringify(data.values));
                this.syncAutomatedTaggingSpendState(false);
                this.setAutomatedTaggingContextItems(data.values.tagging_context || []);
                this.setAutomatedTaggingInstructionItems(data.values.additional_instructions || '');
                if (data.accounting) this.automatedTaggingAccounting = data.accounting;
            } catch (error) {
                this.automatedTaggingError = error.message;
            } finally {
                this.automatedTaggingSaving = false;
            }
        },

        setAutomatedTaggingContextItems(items) {
            const seen = new Set();
            this.automatedTaggingContextItems = (items || [])
                .map(value => String(value).trim())
                .filter(value => {
                    const folded = value.toLowerCase();
                    if (!value || seen.has(folded)) return false;
                    seen.add(folded);
                    return true;
                });
            this.automatedTaggingContext = this.automatedTaggingContextItems.join('\n');
            if (this.automatedTagging) {
                this.automatedTagging.tagging_context = [...this.automatedTaggingContextItems];
            }
        },

        setAutomatedTaggingApiMode(mode) {
            if (!this.automatedTagging) return;
            this.automatedTagging.api_mode = mode;
            this.automatedTagging.google_search = mode === 'paid';
        },

        addAutomatedTaggingContext() {
            const values = this.automatedTaggingContextInput
                .split(/[,\n]/)
                .map(value => value.trim())
                .filter(Boolean);
            if (!values.length) return;
            this.setAutomatedTaggingContextItems([
                ...this.automatedTaggingContextItems,
                ...values,
            ]);
            this.automatedTaggingContextInput = '';
        },

        handleAutomatedTaggingContextKeydown(event) {
            if (!event) return;
            if (event.key === 'Enter' || event.key === ',') {
                event.preventDefault();
                this.addAutomatedTaggingContext();
            } else if (event.key === 'Tab') {
                // Commit before the browser moves focus to the next control.
                this.addAutomatedTaggingContext();
            }
        },

        removeAutomatedTaggingContext(item) {
            this.setAutomatedTaggingContextItems(
                this.automatedTaggingContextItems.filter(value => value !== item)
            );
        },

        setAutomatedTaggingInstructionItems(value) {
            const source = Array.isArray(value)
                ? value
                : String(value || '').split(/\r?\n/);
            const seen = new Set();
            this.automatedTaggingInstructionItems = source
                .map(item => String(item).trim())
                .filter(item => {
                    const folded = item.toLowerCase();
                    if (!item || seen.has(folded)) return false;
                    seen.add(folded);
                    return true;
                });
            if (this.automatedTagging) {
                this.automatedTagging.additional_instructions = this.automatedTaggingInstructionItems.join('\n') || null;
            }
        },

        addAutomatedTaggingInstruction() {
            const values = this.automatedTaggingInstructionInput
                .split(/\r?\n/)
                .map(value => value.trim())
                .filter(Boolean);
            if (!values.length) return;
            this.setAutomatedTaggingInstructionItems([
                ...this.automatedTaggingInstructionItems,
                ...values,
            ]);
            this.automatedTaggingInstructionInput = '';
        },

        removeAutomatedTaggingInstruction(item) {
            this.setAutomatedTaggingInstructionItems(
                this.automatedTaggingInstructionItems.filter(value => value !== item)
            );
        },

        async lookupAutomatedTaggingTweet() {
            const tweetId = this.automatedTaggingTweetId.trim();
            this.automatedTaggingSelectedTweet = null;
            this.automatedTaggingTestResult = null;
            this.automatedTaggingTweetLookupError = '';
            if (!tweetId) {
                return;
            }
            if (!/^\d+$/.test(tweetId)) {
                this.automatedTaggingTweetLookupError = 'Enter a numeric archived tweet ID.';
                return;
            }
            this.automatedTaggingTweetLookingUp = true;
            try {
                const response = await fetch(`/api/automated-tagging/tweets?q=${encodeURIComponent(tweetId)}`);
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.detail || 'Could not load archived tweet');
                if (tweetId === this.automatedTaggingTweetId.trim()) {
                    const tweet = Array.isArray(data.tweets) ? data.tweets[0] : null;
                    if (tweet) {
                        this.automatedTaggingSelectedTweet = tweet;
                    } else {
                        this.automatedTaggingTweetLookupError = 'No archived tweet was found with that ID.';
                    }
                }
            } catch (error) {
                if (tweetId === this.automatedTaggingTweetId.trim()) {
                    this.automatedTaggingTweetLookupError = error.message;
                }
            } finally {
                if (tweetId === this.automatedTaggingTweetId.trim()) {
                    this.automatedTaggingTweetLookingUp = false;
                }
            }
        },

        async runAutomatedTaggingTest() {
            if (!this.automatedTagging || !this.automatedTaggingSelectedTweet || this.automatedTaggingTesting) return;
            this.addAutomatedTaggingContext();
            this.addAutomatedTaggingInstruction();
            this.automatedTaggingTesting = true;
            this.automatedTaggingTestResult = null;
            this.automatedTaggingTestError = '';
            const values = JSON.parse(JSON.stringify(this.automatedTagging));
            values.tagging_context = [...this.automatedTaggingContextItems];
            values.additional_instructions = this.automatedTaggingInstructionItems.join('\n') || null;
            try {
                const response = await fetch('/api/automated-tagging/test', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        tweet_id: this.automatedTaggingSelectedTweet.tweet_id,
                        values,
                    }),
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.detail || 'Test run failed');
                this.automatedTaggingTestResult = data;
                if (data.accounting) this.automatedTaggingAccounting = data.accounting;
                if (!data.success) {
                    this.automatedTaggingTestError = data.output || 'Gemini did not return a valid tagging result';
                }
            } catch (error) {
                this.automatedTaggingTestError = error.message;
            } finally {
                this.automatedTaggingTesting = false;
            }
        },
    };
}
