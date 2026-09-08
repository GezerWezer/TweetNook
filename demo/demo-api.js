/* TweetNook's static demo backend. All mutable state lives in this page session. */
(function installTweetNookDemo(global) {
    'use strict';

    const nativeFetch = typeof global.fetch === 'function' ? global.fetch.bind(global) : null;
    const structuredKeys = new Set([
        'from', 'to', 'mentions', 'since', 'until', 'since_time', 'until_time',
        'since_id', 'max_id', 'has', 'is', 'filter', 'min_retweets', 'min_faves',
        'min_replies', 'conversation_id', 'quoted_tweet_id', 'url', 'source',
        'card_name', 'tag', 'hashtag',
    ]);

    function clone(value) {
        return JSON.parse(JSON.stringify(value));
    }

    function jsonResponse(value, status = 200) {
        return new Response(JSON.stringify(value), {
            status,
            headers: {'Content-Type': 'application/json; charset=utf-8'},
        });
    }

    function errorResponse(detail, status = 400) {
        return jsonResponse({detail}, status);
    }

    function fixtureDate(value) {
        const timestamp = Date.parse(value);
        return Number.isFinite(timestamp) ? timestamp / 1000 : null;
    }

    function tokenize(query) {
        const tokens = [];
        const pattern = /(-?)([a-z_]+):(?:"([^"]*)"|(\S+))|"([^"]*)"|(\S+)/gi;
        let match;
        while ((match = pattern.exec(query)) !== null) {
            if (match[2] && structuredKeys.has(match[2].toLowerCase())) {
                tokens.push({
                    kind: 'filter',
                    key: match[2].toLowerCase(),
                    value: match[3] ?? match[4] ?? '',
                    negated: match[1] === '-',
                });
            } else if (match[5] !== undefined) {
                tokens.push({kind: 'text', value: match[5], phrase: true, negated: false});
            } else {
                const raw = match[6] || '';
                const upper = raw.toUpperCase();
                if (['AND', 'OR', 'NOT'].includes(upper)) {
                    tokens.push({kind: 'operator', value: upper});
                } else if (raw.startsWith('#') && raw.length > 1) {
                    tokens.push({kind: 'filter', key: 'hashtag', value: raw.slice(1), negated: false});
                } else {
                    tokens.push({
                        kind: 'text',
                        value: raw.startsWith('-') ? raw.slice(1) : raw,
                        phrase: false,
                        negated: raw.startsWith('-'),
                    });
                }
            }
        }
        return tokens;
    }

    function parseQuery(query) {
        const groups = [[]];
        let negateNext = false;
        for (const token of tokenize(query || '')) {
            if (token.kind === 'operator') {
                if (token.value === 'OR') groups.push([]);
                if (token.value === 'NOT') negateNext = true;
                continue;
            }
            groups.at(-1).push({...token, negated: Boolean(token.negated) !== negateNext});
            negateNext = false;
        }
        return groups.filter(group => group.length);
    }

    function normalizedWords(value) {
        return String(value || '').toLowerCase().match(/[\p{L}\p{N}_]+/gu) || [];
    }

    function searchableText(tweet) {
        return [
            tweet.text,
            tweet.author?.display_name,
            tweet.author?.username,
            ...(tweet.media_tags?.tags || []),
            ...(tweet.urls || []).flatMap(url => [url.expanded_url, url.display_url]),
        ].filter(Boolean).join(' ').toLowerCase();
    }

    function matchesText(tweet, token) {
        const haystack = searchableText(tweet);
        const term = String(token.value || '').toLowerCase();
        if (!term) return false;
        if (token.phrase) return haystack.includes(term);
        if (term.endsWith('*')) {
            const prefix = term.slice(0, -1);
            return normalizedWords(haystack).some(word => word.startsWith(prefix));
        }
        return normalizedWords(haystack).includes(term);
    }

    function boolValue(value) {
        return Boolean(value && value !== '0' && value !== 'false');
    }

    function matchesFilter(tweet, key, rawValue) {
        const value = String(rawValue || '').toLowerCase().replace(/^[@#]/, '');
        const legacy = tweet.raw_json?.legacy || {};
        const engagement = tweet._demo?.engagement || {};
        if (key === 'from') return (tweet.author?.username || '').toLowerCase() === value;
        if (key === 'to') return (legacy.in_reply_to_screen_name || '').toLowerCase() === value;
        if (key === 'mentions') return (tweet._demo?.mentions || []).some(item => item.toLowerCase() === value);
        if (key === 'since' || key === 'until') {
            const boundary = fixtureDate(rawValue);
            const created = fixtureDate(tweet.created_at);
            if (boundary === null || created === null) return false;
            return key === 'since' ? created >= boundary : created < boundary;
        }
        if (key === 'since_time' || key === 'until_time') {
            const boundary = Number(rawValue);
            const created = fixtureDate(tweet.created_at);
            if (!Number.isFinite(boundary) || created === null) return false;
            return key === 'since_time' ? created >= boundary : created < boundary;
        }
        if (key === 'since_id' || key === 'max_id') {
            try {
                const id = BigInt(tweet.tweet_id);
                const boundary = BigInt(rawValue);
                return key === 'since_id' ? id > boundary : id <= boundary;
            } catch (_) {
                return false;
            }
        }
        if (key === 'has') {
            if (value === 'article') return Boolean(tweet.article);
            if (value === 'media') return Boolean(tweet.media?.length);
            if (value === 'image') return tweet.media?.some(item => item.type === 'photo') || false;
            if (value === 'video') return tweet.media?.some(item => ['video', 'animated_gif'].includes(item.type)) || false;
            if (value === 'links') return Boolean(tweet.urls?.length);
        }
        if (key === 'is' || key === 'filter') {
            const normalized = value === 'images' ? 'image'
                : ['videos', 'native_video'].includes(value) ? 'video'
                    : value === 'replies' ? 'reply'
                        : value === 'threads' || value === 'self_threads' ? 'thread'
                            : value === 'nativeretweets' ? 'retweet' : value;
            if (normalized === 'article' || normalized === 'articles') return Boolean(tweet.article);
            if (normalized === 'media') return Boolean(tweet.media?.length);
            if (normalized === 'image') return tweet.media?.some(item => item.type === 'photo') || false;
            if (normalized === 'video') return tweet.media?.some(item => ['video', 'animated_gif'].includes(item.type)) || false;
            if (normalized === 'links') return Boolean(tweet.urls?.length);
            if (normalized === 'reply') return Boolean(legacy.in_reply_to_status_id_str);
            if (normalized === 'quote') return boolValue(legacy.is_quote_status);
            if (normalized === 'retweet') return Boolean(legacy.retweeted_status_id_str);
            if (normalized === 'thread') {
                return Boolean(legacy.in_reply_to_screen_name)
                    && legacy.in_reply_to_screen_name.toLowerCase() === (tweet.author?.username || '').toLowerCase();
            }
            if (normalized === 'verified') return Boolean(tweet._demo?.verified);
            if (normalized === 'resurrected') return tweet.enrichment_state === 'resurrected';
        }
        if (key === 'min_retweets') return Number(engagement.retweets || 0) >= Number(rawValue);
        if (key === 'min_faves') return Number(engagement.likes || 0) >= Number(rawValue);
        if (key === 'min_replies') return Number(engagement.replies || 0) >= Number(rawValue);
        if (key === 'conversation_id') return String(legacy.conversation_id_str || '') === String(rawValue);
        if (key === 'quoted_tweet_id') return String(legacy.quoted_status_id_str || '') === String(rawValue);
        if (key === 'url') {
            return (tweet.urls || []).some(url =>
                String(url.expanded_url || '').toLowerCase().includes(value)
                || String(url.display_url || '').toLowerCase().includes(value));
        }
        if (key === 'source') return String(tweet.raw_json?.source || '').toLowerCase().includes(value.replaceAll('_', ' '));
        if (key === 'card_name') return String(tweet.raw_json?.card?.name || '').toLowerCase() === value;
        if (key === 'tag') return (tweet.media_tags?.tags || []).some(tag => tag.toLowerCase() === value);
        if (key === 'hashtag') return (tweet._demo?.hashtags || []).some(tag => tag.toLowerCase() === value);
        return false;
    }

    function matchesQuery(tweet, groups) {
        if (!groups.length) return true;
        return groups.some(group => group.every(token => {
            const matched = token.kind === 'filter'
                ? matchesFilter(tweet, token.key, token.value)
                : matchesText(tweet, token);
            return token.negated ? !matched : matched;
        }));
    }

    function relevanceScore(tweet, groups) {
        return groups.reduce((best, group) => {
            const score = group.reduce((total, token) => {
                if (token.negated) return total;
                if (token.kind === 'filter') return total + (matchesFilter(tweet, token.key, token.value) ? 1 : 0);
                return total + (matchesText(tweet, token) ? 2 : 0);
            }, 0);
            return Math.max(best, score);
        }, 0);
    }

    function authorRaw(author) {
        return {
            is_blue_verified: Boolean(author.verified),
            legacy: {
                verified: Boolean(author.verified),
                name: author.display_name,
                screen_name: author.username,
                description: author.description || '',
                followers_count: author.followers_count || 0,
                friends_count: author.following_count || 0,
            },
            rest_id: author.id,
        };
    }

    function cardRaw(card, cardName) {
        if (!card || !cardName) return null;
        return {
            name: cardName,
            binding_values: {
                title: {string_value: card.title},
                description: {string_value: card.description},
                vanity_url: {string_value: card.vanity_url},
                card_url: {string_value: card.card_url},
            },
        };
    }

    function createStore(fixture, options = {}) {
        const state = clone(fixture);
        const authors = new Map(state.authors.map(author => [String(author.id), author]));
        const sourceTweets = new Map(state.tweets.map(tweet => [String(tweet.tweet_id), tweet]));
        const nowSeconds = typeof options.now === 'function'
            ? options.now
            : () => Date.now() / 1000;
        const runtime = {
            activity: null,
            lastActivity: null,
            schedule: {
                enabled: true,
                cadence: 'daily',
                every_hours: 6,
                time: '03:00',
                randomize_time: true,
                random_offset_min_hours: 0,
                random_offset_max_hours: 2,
                weekday: 0,
                day_of_month: 1,
                timezone: 'local',
            },
            automatedTagging: {
                enabled: true,
                api_key: 'demo-only',
                api_mode: 'free',
                model: 'gemini-demo',
                thinking_level: 'low',
                google_search: false,
                free_batch_size: 6,
                free_rpm: 10,
                free_rpd: 20,
                processing_tier: 'flex',
                daily_spend_limit_usd: null,
                unlimited_spend: false,
                search_safety_reserve: 100,
                tagging_context: ['local archives', 'software'],
                additional_instructions: null,
                max_media_size_mb: 100,
            },
        };

        function hydrate(source, attachQuote = true) {
            if (!source) return null;
            const author = authors.get(String(source.author_id));
            const engagement = source.engagement || {};
            const raw = {
                rest_id: String(source.tweet_id),
                source: source.source || 'Twitter Web App',
                core: {user_results: {result: authorRaw(author)}},
                legacy: {
                    full_text: source.text,
                    created_at: source.created_at,
                    conversation_id_str: source.conversation_id,
                    in_reply_to_status_id_str: source.reply_to_id,
                    in_reply_to_screen_name: source.reply_to_username,
                    quoted_status_id_str: source.quoted_tweet_id,
                    is_quote_status: Boolean(source.quoted_tweet_id),
                    reply_count: engagement.replies || 0,
                    retweet_count: engagement.retweets || 0,
                    quote_count: source.quoted_tweet_id ? 1 : 0,
                    favorite_count: engagement.likes || 0,
                    bookmark_count: engagement.bookmarks || 0,
                    entities: {
                        hashtags: (source.hashtags || []).map(text => ({text})),
                        user_mentions: (source.mentions || []).map(screen_name => ({screen_name})),
                        urls: (source.urls || []).map(url => ({
                            url: url.short_url || url.expanded_url,
                            expanded_url: url.expanded_url,
                            display_url: url.display_url,
                        })),
                    },
                },
                views: {count: engagement.views || 0},
                card: cardRaw(source.card, source.card_name),
            };
            if (source.community_note) {
                raw.birdwatch_pivot = {
                    shorttitle: 'Readers added context',
                    subtitle: {
                        text: String(source.community_note),
                        entities: [],
                    },
                };
            }
            const tweet = {
                tweet_id: String(source.tweet_id),
                text: source.text,
                author: {
                    id: author.id,
                    username: author.username,
                    display_name: author.display_name,
                },
                created_at: source.created_at,
                synced_at: source.synced_at,
                collection: {
                    type: source.collections[0] || null,
                    folder_id: null,
                    sort_index: String(source.liked_order || source.tweet_id),
                    added_at: source.created_at,
                    synced_at: source.synced_at,
                },
                collections: [...source.collections],
                media: clone(source.media || []),
                urls: clone(source.urls || []),
                article: null,
                media_tags: source.tags?.length || source.description
                    ? {tags: [...(source.tags || [])], description: source.description || ''}
                    : null,
                qt_media_tags: null,
                raw_json: raw,
                _demo: {
                    liked_order: source.liked_order,
                    verified: Boolean(author.verified),
                    engagement: clone(engagement),
                    hashtags: [...(source.hashtags || [])],
                    mentions: [...(source.mentions || [])],
                },
            };
            if (attachQuote && source.quoted_tweet_id) {
                const quoted = hydrate(sourceTweets.get(String(source.quoted_tweet_id)), false);
                tweet.quoted_tweet = quoted;
                tweet.qt_media = clone(quoted?.media || []);
                tweet.qt_media_tags = clone(quoted?.media_tags || null);
            } else {
                tweet.qt_media = [];
            }
            tweet.local_quote_count = state.tweets.filter(item => item.quoted_tweet_id === source.tweet_id).length;
            return tweet;
        }

        function currentTweets() {
            return state.tweets.map(tweet => hydrate(tweet));
        }

        function findSource(tweetId) {
            return sourceTweets.get(String(tweetId));
        }

        function sortedTweets(tweets, sort, groups, randomSeed = 0) {
            const copy = [...tweets];
            if (sort === 'random') {
                const key = tweet => {
                    let hash = Number(randomSeed) >>> 0;
                    for (const character of String(tweet.tweet_id)) {
                        hash = Math.imul(hash ^ character.charCodeAt(0), 16777619) >>> 0;
                    }
                    return hash;
                };
                return copy.sort((left, right) => key(left) - key(right) || String(left.tweet_id).localeCompare(String(right.tweet_id)));
            }
            if (sort === 'liked_latest' || sort === 'liked_earliest') {
                const direction = sort === 'liked_latest' ? -1 : 1;
                return copy.sort((left, right) => direction * (Number(left._demo.liked_order || 0) - Number(right._demo.liked_order || 0)));
            }
            if (sort === 'default' && groups.length) {
                return copy.sort((left, right) =>
                    relevanceScore(right, groups) - relevanceScore(left, groups)
                    || fixtureDate(right.created_at) - fixtureDate(left.created_at));
            }
            const direction = sort === 'oldest' ? 1 : -1;
            return copy.sort((left, right) => direction * (fixtureDate(left.created_at) - fixtureDate(right.created_at)));
        }

        function tagCounts(query = '') {
            const folded = query.toLowerCase();
            const counts = new Map();
            for (const tweet of state.tweets) {
                for (const tag of tweet.tags || []) {
                    const existing = [...counts.keys()].find(value => value.toLowerCase() === tag.toLowerCase()) || tag;
                    counts.set(existing, (counts.get(existing) || 0) + 1);
                }
            }
            return [...counts.entries()]
                .filter(([tag]) => tag.toLowerCase().includes(folded))
                .map(([tag, count]) => ({tag, count}))
                .sort((left, right) => right.count - left.count || left.tag.localeCompare(right.tag));
        }

        const syncDurationSeconds = 180;
        const syncPhases = [
            {
                key: 'preflight:bookmarks,likes',
                title: 'Prepare',
                start: 0,
                end: 3,
                total: 5,
                unit: 'checks',
                detail: 'authentication · archive owner · 2 remote endpoint probes',
                rateUnit: 'checks/s',
                summary: 'authentication ready · 2/2 endpoints available',
                scenes: [
                    {at: 0, completed: 0, activity: 'Resolving Twitter/X authentication', counters: '0/5 checks'},
                    {at: 0.2, completed: 1, activity: 'Checking local archive ownership', counters: '1/5 checks · authentication resolved'},
                    {at: 0.4, completed: 2, activity: 'Resolving GraphQL operation IDs', counters: '2/5 checks · authentication ready · archive owner accepted'},
                    {at: 0.6, completed: 3, activity: 'Probing Bookmarks endpoint', counters: '3/5 checks · 2 operation IDs ready'},
                    {at: 0.8, completed: 4, activity: 'Probing Likes endpoint', counters: '4/5 checks · 1/2 endpoints ready'},
                ],
            },
            {
                key: 'sync:bookmarks:head',
                title: 'Bookmarks',
                start: 3,
                end: 5,
                total: 1,
                unit: 'current page',
                detail: 'head pass · each page is committed with its resume cursor',
                showRate: false,
                showEta: false,
                summary: '1 page · 20 tweets · reached saved archive history',
                scenes: [
                    {at: 0, completed: 0, activity: 'Fetching newest bookmarks · page 1', counters: '0 pages · 0 tweets'},
                    {at: 0.45, completed: 0, activity: 'Committing 20 tweets and the page 1 resume cursor', counters: '0 pages · 0 tweets'},
                    {at: 0.8, completed: 1, activity: 'Reached saved archive history', counters: '1 page · 20 tweets · 20 on this page · reached saved archive history'},
                ],
            },
            {
                key: 'sync:likes:head',
                title: 'Likes',
                start: 5,
                end: 7,
                total: 1,
                unit: 'current page',
                detail: 'head pass · each page is committed with its resume cursor',
                showRate: false,
                showEta: false,
                summary: '1 page · 20 tweets · reached saved archive history',
                scenes: [
                    {at: 0, completed: 0, activity: 'Fetching newest likes · page 1', counters: '0 pages · 0 tweets'},
                    {at: 0.45, completed: 0, activity: 'Committing 20 tweets and the page 1 resume cursor', counters: '0 pages · 0 tweets'},
                    {at: 0.8, completed: 1, activity: 'Reached saved archive history', counters: '1 page · 20 tweets · 20 on this page · reached saved archive history'},
                ],
            },
            {
                key: 'threads',
                title: 'Threads',
                start: 7,
                end: 75,
                total: 6,
                unit: 'candidates',
                detail: 'membership and reachable linked-status candidates',
                rateUnit: 'candidates/s',
                summary: '6 fetched · 6 expanded · 148 already known · 0 failed',
                scenes: [
                    {at: 0, completed: 0, activity: 'Selecting thread candidates from local archive state', counters: '0/6 candidates'},
                    {at: 0.02, completed: 0, activity: 'Loading archived thread expansion state…', counters: '0/6 candidates'},
                    {at: 0.12, completed: 0, activity: 'Loaded 382 previously expanded thread targets', counters: '0/6 candidates'},
                    {at: 0.14, completed: 0, activity: 'Loading archived membership tweets…', counters: '0/6 candidates'},
                    {at: 0.2, completed: 0, activity: 'Membership pass over 126 archived tweets', counters: '120 already expanded'},
                    {at: 0.22, completed: 0, activity: 'Loading known tweet IDs for linked-status pass…', counters: '0/6 candidates'},
                    {at: 0.28, completed: 0, activity: 'Loaded 6,411 known tweet IDs for linked-status dedupe', counters: '0/6 candidates'},
                    {at: 0.3, completed: 0, activity: 'Loading archived URL references…', counters: '0/6 candidates'},
                    {at: 0.47, completed: 0, activity: 'Related-status pass over 24 quote relations and 3 reachable URL references', counters: '0/6 candidates · 5 membership · 1 quoted · 0 linked'},
                    {at: 0.5, completed: 0, activity: 'Resolving the TweetDetail operation ID', counters: '0/6 candidates · 5 membership · 1 quoted · 0 linked'},
                    {at: 0.59, completed: 1, activity: 'Fetching thread context for 202602140001', counters: '1/6 candidates · 1 fetched · 1 expanded · 148 already known · 0 failed'},
                    {at: 0.68, completed: 2, activity: 'Fetching thread context for 202602130004', counters: '2/6 candidates · 2 fetched · 2 expanded · 148 already known · 0 failed'},
                    {at: 0.77, completed: 3, activity: 'Fetching thread context for 202602120007', counters: '3/6 candidates · 3 fetched · 3 expanded · 148 already known · 0 failed'},
                    {at: 0.86, completed: 4, activity: 'Fetching thread context for 202602110010', counters: '4/6 candidates · 4 fetched · 4 expanded · 148 already known · 0 failed'},
                    {at: 0.94, completed: 5, activity: 'Fetching thread context for 202602100013', counters: '5/6 candidates · 5 fetched · 5 expanded · 148 already known · 0 failed'},
                    {at: 0.985, completed: 6, activity: 'Persisting expanded conversation relationships', counters: '6/6 candidates · 6 fetched · 6 expanded · 148 already known · 0 failed'},
                ],
            },
            {
                key: 'resurrection',
                title: 'Resurrection',
                start: 75,
                end: 171,
                total: 16,
                unit: 'tweets',
                detail: '16 due selected · 100-request ceiling',
                rateUnit: 'tweets/s',
                summary: '16 checked · 1 returned · 15 unavailable · 0 transient · 0 still due',
                resolveScene(stepElapsed) {
                    if (stepElapsed < 1) {
                        return {
                            completed: 0,
                            activity: 'Resolving the TweetDetail operation ID',
                            counters: '0/16 tweets · 0 checked · 0 returned · 0 unavailable · 0 transient',
                        };
                    }
                    const completed = Math.min(15, Math.floor(stepElapsed / 6));
                    const returned = completed >= 4 ? 1 : 0;
                    const unavailable = completed - returned;
                    if (stepElapsed < 6 || completed === 15) {
                        return {
                            completed,
                            activity: 'Rate-limit pacing active, using 6.0s/request',
                            counters: `${completed}/16 tweets · ${completed} checked · ${returned} returned · ${unavailable} unavailable · 0 transient`,
                        };
                    }
                    const nextId = `2026000000${String(completed + 1).padStart(2, '0')}`;
                    return {
                        completed,
                        activity: `Rechecking unavailable tweet ${nextId}`,
                        counters: `${completed}/16 tweets · ${completed} checked · ${returned} returned · ${unavailable} unavailable · 0 transient · 0 account probes`,
                    };
                },
            },
            {
                key: 'articles',
                title: 'Articles',
                start: 171,
                end: 171,
                total: 0,
                unit: 'tweets',
                detail: 'article rows requiring refresh',
                skip: true,
                summary: 'skipped due to no article rows requiring refresh',
                scenes: [{at: 0, completed: 0, activity: '', counters: ''}],
            },
            {
                key: 'media',
                title: 'Media',
                start: 171,
                end: 176,
                total: 28,
                unit: 'files',
                detail: 'pending media rows',
                rateUnit: 'files/s',
                summary: '28 processed · 28 downloaded · 0 skipped · 0 failed · 18.6 MiB transferred',
                scenes: [
                    {at: 0, completed: 0, activity: 'Downloading animated GIF for tweet 202602100013', counters: '0/28 files · 0 processed · 0 downloaded · 0 skipped · 0 failed'},
                    {at: 0.2, completed: 5, activity: 'Downloading photo for tweet 202602110010', counters: '5/28 files · 5 processed · 5 downloaded · 0 skipped · 0 failed'},
                    {at: 0.4, completed: 11, activity: 'Downloading video for tweet 202602090016', counters: '11/28 files · 11 processed · 11 downloaded · 0 skipped · 0 failed'},
                    {at: 0.6, completed: 17, activity: 'Verifying video metadata for tweet 202602090016', counters: '17/28 files · 17 processed · 17 downloaded · 0 skipped · 0 failed'},
                    {at: 0.8, completed: 23, activity: 'Downloading photo for tweet 202602070022', counters: '23/28 files · 23 processed · 23 downloaded · 0 skipped · 0 failed'},
                    {at: 0.96, completed: 28, activity: 'Persisting media download metadata', counters: '28/28 files · 28 processed · 28 downloaded · 0 skipped · 0 failed'},
                ],
            },
            {
                key: 'urls',
                title: 'URLs',
                start: 176,
                end: 180,
                total: 2,
                unit: 'URLs',
                detail: 'saved URLs · redirects followed · canonical metadata persisted',
                rateUnit: 'URLs/s',
                summary: '2 processed · 2 updated · 0 failed',
                scenes: [
                    {at: 0, completed: 0, activity: 'Fetching metadata from example.com', counters: '0/2 URLs · 0 processed · 0 updated · 0 failed'},
                    {at: 0.45, completed: 1, activity: 'Fetching metadata from docs.example.com', counters: '1/2 URLs · 1 processed · 1 updated · 0 failed'},
                    {at: 0.9, completed: 2, activity: 'Persisting canonical URL metadata', counters: '2/2 URLs · 2 processed · 2 updated · 0 failed'},
                ],
            },
        ];

        function progressScene(phase, ratio, stepElapsed) {
            if (phase.resolveScene) return phase.resolveScene(stepElapsed);
            return phase.scenes.reduce(
                (selected, scene) => ratio >= scene.at ? scene : selected,
                phase.scenes[0],
            );
        }

        function activitySnapshot() {
            if (!runtime.activity) return null;
            const now = nowSeconds();
            const elapsed = Math.max(0, now - runtime.activity.startedAt);
            const stopped = runtime.activity.stopped;
            const complete = elapsed >= syncDurationSeconds || stopped;
            const displayElapsed = Math.min(elapsed, syncDurationSeconds);
            const steps = syncPhases.map(phase => {
                const scheduledSkip = Boolean(phase.skip) && elapsed >= phase.start;
                let stateName = phase.skip
                    ? scheduledSkip ? 'skipped' : 'pending'
                    : elapsed >= phase.end ? 'complete' : elapsed >= phase.start ? 'active' : 'pending';
                if (stopped && stateName === 'active') stateName = 'failed';
                if (stopped && stateName === 'pending') stateName = 'skipped';
                const phaseDuration = phase.end - phase.start;
                const ratio = phaseDuration <= 0
                    ? 1
                    : Math.max(0, Math.min(1, (elapsed - phase.start) / phaseDuration));
                const stepElapsed = Math.max(0, Math.min(phaseDuration, elapsed - phase.start));
                const scene = progressScene(phase, ratio, stepElapsed);
                const completed = stateName === 'complete' ? phase.total : stateName === 'skipped' ? 0 : scene.completed;
                const rate = phase.showRate === false || completed <= 0 || stepElapsed <= 0
                    ? null
                    : completed / stepElapsed;
                const eta = phase.showEta === false || rate === null || completed >= phase.total
                    ? null
                    : (phase.total - completed) / rate;
                return {
                    key: phase.key,
                    title: phase.title,
                    state: stateName,
                    activity: stateName === 'active' ? scene.activity : '',
                    summary: stateName === 'complete'
                        ? phase.summary
                        : stateName === 'failed'
                            ? 'Stopped by user'
                            : stateName === 'skipped'
                                ? scheduledSkip
                                    ? phase.summary
                                    : 'skipped due to an earlier failure stopped the command'
                                : '',
                    completed,
                    total: phase.total,
                    unit: phase.unit,
                    counters: stateName === 'active' ? scene.counters : '',
                    detail: phase.detail,
                    elapsed_seconds: stepElapsed,
                    rate,
                    rate_unit: phase.rateUnit || `${phase.unit}/s`,
                    eta_seconds: stateName === 'active' ? eta : null,
                };
            });
            const activeStep = steps.find(step => step.state === 'active');
            const snapshot = {
                version: 1,
                run_id: runtime.activity.runId,
                origin: 'web',
                pid: 1,
                title: 'tweetnook sync',
                running: !complete,
                success: complete ? !stopped : null,
                stopped,
                summary: stopped
                    ? 'Stopped by user'
                    : complete
                        ? 'bookmarks: 1 pages, 20 tweets; likes: 1 pages, 20 tweets'
                        : '',
                started_at: runtime.activity.startedAt,
                updated_at: now,
                completed_at: complete
                    ? (runtime.activity.stoppedAt || runtime.activity.startedAt + syncDurationSeconds)
                    : null,
                elapsed_seconds: displayElapsed,
                active_step: activeStep?.key || null,
                steps,
                issues: stopped
                    ? [{level: 'error', message: 'Stopped by user', count: 1, latest: null}]
                    : [],
            };
            if (complete) {
                runtime.lastActivity = snapshot;
                runtime.activity = null;
            }
            return snapshot;
        }

        function scheduleStatus() {
            const config = clone(runtime.schedule);
            return {
                configured: true,
                enabled: config.enabled,
                relative: config.enabled ? 'daily around 3:00 AM' : 'Not scheduled',
                date: config.enabled ? 'Tomorrow · browser local time' : 'Enable scheduling in Settings',
                description: config.enabled ? 'Daily at 3:00 AM with up to 2 hours random delay' : 'Scheduled syncs are off',
                config,
            };
        }

        function accounting() {
            return {
                requests: 0,
                tweets: 0,
                estimated_cost_usd: 0,
                search_queries_this_month: 0,
                search_allowance: 5000,
                spend_history: [],
                lifetime_spend_history: [],
                models: {},
                tweet_types: {},
            };
        }

        function statsSnapshot() {
            const tweets = currentTweets();
            const uniqueAuthors = new Set(tweets.map(tweet => tweet.author.id));
            const tags = tagCounts();
            const collectionRows = [
                ['Bookmarks', 'bookmark'],
                ['Likes', 'like'],
                ['Tweets', 'tweet'],
            ].map(([label, collection]) => {
                const matches = tweets.filter(tweet => tweet.collections.includes(collection));
                return {
                    collection: label,
                    count: matches.length,
                    oldest: matches.length ? new Date(Math.min(...matches.map(tweet => Date.parse(tweet.created_at)))).toLocaleDateString('en-US', {month: 'short', day: 'numeric', year: 'numeric'}) : '-',
                    newest: matches.length ? new Date(Math.max(...matches.map(tweet => Date.parse(tweet.created_at)))).toLocaleDateString('en-US', {month: 'short', day: 'numeric', year: 'numeric'}) : '-',
                    last_synced: 'Feb 15, 2026 12:10 pm',
                    backfill_status: 'Complete',
                };
            });
            const mediaCount = tweets.reduce((total, tweet) => total + tweet.media.length, 0);
            const taggedTweets = tweets.filter(tweet => tweet.media_tags?.tags?.length).length;
            const segments = [
                {id: 'database', name: 'Archive database', bytes: 245760, count: tweets.length, unit: 'tweets', formatted_count: `${tweets.length} synthetic tweets`, formatted_size: '240 KiB', percent: 60, description: 'Synthetic archive records used for this browser session.'},
                {id: 'media', name: 'Demo media', bytes: 122880, count: mediaCount, unit: 'items', formatted_count: `${mediaCount} local media references`, formatted_size: '120 KiB', percent: 30, description: 'References to the public image, GIF, and video files included with this demo.'},
                {id: 'search', name: 'Search index', bytes: 40960, count: tweets.length, unit: 'tweets', formatted_count: `${tweets.length} searchable tweets`, formatted_size: '40 KiB', percent: 10, description: 'In-memory search data derived from the synthetic fixture.'},
            ];
            return {
                generated_at: new Date().toISOString(),
                age_seconds: 0,
                stale: false,
                refreshing: false,
                refresh_failed: false,
                summary: {
                    owner_user_id: state.owner_user_id,
                    unique_posts: tweets.length,
                    media_rows: mediaCount,
                    articles: 1,
                    urls: tweets.reduce((total, tweet) => total + tweet.urls.length, 0),
                    profiles: uniqueAuthors.size,
                    oldest_post: 'Jan 28, 2026',
                    newest_post: 'Feb 15, 2026',
                    latest_sync: 'Feb 15, 2026',
                    missing_archive_tweets: 0,
                    missing_archive_pct: 0,
                    archive_tweets: tweets.length,
                },
                collections: collectionRows,
                health: {
                    threads_expanded: 1,
                    enrichment: {
                        done: tweets.length,
                        incomplete: 0,
                        pending: 0,
                        transient: 0,
                        resurrected: 0,
                        unavailable: {total: 0, percent_of_archive: 0, reasons: []},
                    },
                },
                storage: {formatted_total: '400 KiB', segments, simplified_segments: segments},
                tags: {
                    eligible_tweets: tweets.length,
                    tagged_tweets: taggedTweets,
                    coverage_pct: Math.round(taggedTweets / tweets.length * 100),
                    unique_tags: tags.length,
                    top_tags: tags.slice(0, 10),
                },
            };
        }

        async function handle(input, init = {}) {
            const requestUrl = input instanceof Request ? input.url : String(input);
            const url = new URL(requestUrl, global.document?.baseURI || global.location?.href || 'http://localhost/');
            const method = String(init.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
            const path = url.pathname.replace(/\/+$/, '') || '/';
            const bodyText = init.body instanceof String ? String(init.body) : init.body;
            let body = {};
            if (typeof bodyText === 'string' && bodyText) {
                try { body = JSON.parse(bodyText); } catch (_) { body = {}; }
            }

            if (path.endsWith('/api/tweets') && method === 'GET') {
                const collectionMap = {likes: 'like', bookmarks: 'bookmark', tweets: 'tweet'};
                const collection = collectionMap[url.searchParams.get('collection')] || null;
                const groups = parseQuery(url.searchParams.get('q') || '');
                const page = Math.max(1, Number(url.searchParams.get('page') || 1));
                const limit = Math.max(1, Math.min(100, Number(url.searchParams.get('limit') || 20)));
                let tweets = currentTweets().filter(tweet => collection
                    ? tweet.collections.includes(collection)
                    : tweet.collections.length > 0);
                tweets = tweets.filter(tweet => matchesQuery(tweet, groups));
                tweets = sortedTweets(
                    tweets,
                    url.searchParams.get('sort') || 'default',
                    groups,
                    url.searchParams.get('random_seed') || 0,
                );
                const total = tweets.length;
                const start = (page - 1) * limit;
                return jsonResponse({
                    tweets: tweets.slice(start, start + limit),
                    total,
                    page,
                    pages: Math.max(1, Math.ceil(total / limit)),
                    has_more: start + limit < total,
                    truncated: false,
                });
            }

            const quotesMatch = path.match(/\/api\/tweets\/(\d+)\/quotes$/);
            if (quotesMatch && method === 'GET') {
                const page = Math.max(1, Number(url.searchParams.get('page') || 1));
                const limit = Math.max(1, Math.min(100, Number(url.searchParams.get('limit') || 20)));
                const tweets = state.tweets
                    .filter(tweet => String(tweet.quoted_tweet_id) === quotesMatch[1])
                    .map(tweet => hydrate(tweet));
                return jsonResponse({tweets: tweets.slice((page - 1) * limit, page * limit), total: tweets.length, page, limit});
            }

            const detailMatch = path.match(/\/api\/tweets\/(\d+)$/);
            if (detailMatch && method === 'GET') {
                const source = findSource(detailMatch[1]);
                if (!source) return errorResponse('Tweet not found', 404);
                const parents = [];
                const seen = new Set();
                let parentId = source.reply_to_id;
                while (parentId && !seen.has(parentId)) {
                    seen.add(parentId);
                    const parent = findSource(parentId);
                    if (!parent) break;
                    parents.unshift(hydrate(parent));
                    parentId = parent.reply_to_id;
                }
                const mainAuthorId = String(source.author_id);
                const children = state.tweets
                    .filter(tweet => String(tweet.reply_to_id || '') === detailMatch[1])
                    .map(childSource => {
                        const opReplies = state.tweets
                            .filter(reply => (
                                String(reply.reply_to_id || '') === String(childSource.tweet_id)
                                && String(reply.author_id) === mainAuthorId
                            ))
                            .map(reply => hydrate(reply));
                        return {...hydrate(childSource), op_replies: opReplies};
                    })
                    .sort((left, right) => (
                        Number(Boolean(right.op_replies.length)) - Number(Boolean(left.op_replies.length))
                        || Number(right._demo.engagement.likes || 0) - Number(left._demo.engagement.likes || 0)
                    ));
                return jsonResponse({main: hydrate(source), parents, children});
            }

            if (path.endsWith('/api/authors/search') && method === 'GET') {
                const query = (url.searchParams.get('q') || '').replace(/^@/, '').toLowerCase();
                const rows = state.authors
                    .filter(author => author.username.toLowerCase().includes(query) || author.display_name.toLowerCase().includes(query))
                    .slice(0, 10)
                    .map(author => ({id: author.id, username: author.username, display_name: author.display_name}));
                return jsonResponse({authors: rows});
            }

            if ((path.endsWith('/api/tags/autocomplete') || path.endsWith('/api/tags/stats')) && method === 'GET') {
                return jsonResponse({tags: tagCounts(url.searchParams.get('q') || '')});
            }

            const globalTagMatch = path.match(/\/api\/tags\/global\/(.+)$/);
            if (globalTagMatch && method === 'DELETE') {
                const tag = decodeURIComponent(globalTagMatch[1]).toLowerCase();
                for (const tweet of state.tweets) tweet.tags = (tweet.tags || []).filter(item => item.toLowerCase() !== tag);
                return jsonResponse({success: true});
            }

            const tagMatch = path.match(/\/api\/tags\/(\d+)$/);
            if (tagMatch && ['PUT', 'DELETE'].includes(method)) {
                const tweet = findSource(tagMatch[1]);
                if (!tweet) return errorResponse('Tweet not found', 404);
                if (method === 'DELETE') {
                    tweet.tags = [];
                    tweet.description = '';
                } else {
                    tweet.tags = Array.isArray(body.tags) ? body.tags.map(tag => String(tag).trim()).filter(Boolean) : [];
                    tweet.description = String(body.description || '').trim();
                }
                return jsonResponse({success: true});
            }

            if (path.endsWith('/api/tags/merge') && method === 'POST') {
                const primary = String(body.primary_tag || '').trim();
                const merged = new Set((body.merge_tags || []).map(tag => String(tag).toLowerCase()));
                for (const tweet of state.tweets) {
                    if (!(tweet.tags || []).some(tag => merged.has(tag.toLowerCase()))) continue;
                    tweet.tags = (tweet.tags || []).filter(tag => !merged.has(tag.toLowerCase()));
                    if (!tweet.tags.some(tag => tag.toLowerCase() === primary.toLowerCase())) tweet.tags.push(primary);
                }
                return jsonResponse({success: true});
            }

            if (path.endsWith('/api/stats/latest-sync')) return jsonResponse({latest_sync: 'Feb 15, 2026'});
            if (path.endsWith('/api/stats/enrichment-incomplete')) return jsonResponse({incomplete: 0});
            if (path.endsWith('/api/stats/snapshot')) return jsonResponse(statsSnapshot());
            if (path.endsWith('/api/stats/status')) {
                const snapshot = statsSnapshot();
                return jsonResponse({generated_at: snapshot.generated_at, age_seconds: 0, stale: false, refreshing: false, refresh_failed: false});
            }
            if (path.endsWith('/api/stats/refresh') && method === 'POST') return jsonResponse(statsSnapshot());

            if (path.endsWith('/api/activity/status') && method === 'GET') {
                const snapshot = activitySnapshot();
                return jsonResponse({active: Boolean(snapshot?.running), snapshot: snapshot?.running ? snapshot : null, last_snapshot: snapshot?.running ? null : (snapshot || runtime.lastActivity), schedule: scheduleStatus()});
            }
            if (path.endsWith('/api/activity/sync') && method === 'POST') {
                if (runtime.activity) return errorResponse('A demo sync is already running', 409);
                const startedAt = nowSeconds();
                runtime.activity = {
                    runId: `demo-${Math.floor(startedAt * 1000)}`,
                    startedAt,
                    stopped: false,
                    stoppedAt: null,
                };
                runtime.lastActivity = null;
                return jsonResponse({started: true, kind: 'sync', run_id: runtime.activity.runId}, 202);
            }
            if (path.endsWith('/api/activity/stop') && method === 'POST') {
                if (!runtime.activity) return errorResponse('No task is running', 409);
                runtime.activity.stopped = true;
                runtime.activity.stoppedAt = nowSeconds();
                activitySnapshot();
                return jsonResponse({stopping: true, run_id: runtime.lastActivity?.run_id}, 202);
            }
            if (path.endsWith('/api/activity/schedule') && method === 'GET') return jsonResponse(scheduleStatus());
            if (path.endsWith('/api/activity/schedule') && method === 'PUT') {
                runtime.schedule = {...runtime.schedule, ...body};
                return jsonResponse(scheduleStatus());
            }

            if (path.endsWith('/api/notices') && method === 'GET') return jsonResponse({notices: []});

            if (path.endsWith('/api/automated-tagging') && method === 'GET') {
                return jsonResponse({installed: true, values: clone(runtime.automatedTagging), accounting: accounting()});
            }
            if (path.endsWith('/api/automated-tagging') && method === 'PUT') {
                runtime.automatedTagging = {...runtime.automatedTagging, ...(body.values || {})};
                return jsonResponse({installed: true, values: clone(runtime.automatedTagging), accounting: accounting()});
            }
            if (path.endsWith('/api/automated-tagging/models') && method === 'POST') {
                return jsonResponse({models: [{id: 'gemini-demo', display_name: 'Gemini Demo', description: 'Deterministic browser-only model'}]});
            }
            if (path.endsWith('/api/automated-tagging/tweets') && method === 'GET') {
                const query = (url.searchParams.get('q') || '').trim();
                const tweets = currentTweets()
                    .filter(tweet => !query || tweet.tweet_id === query || searchableText(tweet).includes(query.toLowerCase()))
                    .slice(0, 8)
                    .map(tweet => ({tweet_id: tweet.tweet_id, author_display_name: tweet.author.display_name, author_username: tweet.author.username, text: tweet.text, has_media: Boolean(tweet.media.length)}));
                return jsonResponse({tweets});
            }
            if (path.endsWith('/api/automated-tagging/test') && method === 'POST') {
                const tweet = hydrate(findSource(String(body.tweet_id || '')));
                if (!tweet) return errorResponse('Tweet not found', 404);
                const mediaType = tweet.media[0]?.type || 'text_only';
                return jsonResponse({
                    success: true,
                    result: {
                        tweet_id: tweet.tweet_id,
                        model: 'gemini-demo',
                        content_type: mediaType,
                        description: 'A synthetic result describing local archive tooling.',
                        tags: ['demo', 'archive', mediaType === 'text_only' ? 'tooling' : mediaType],
                    },
                    output: 'Browser-only demo result. No external model was called.',
                    accounting: accounting(),
                    notice: 'Settings and generated tags were not saved outside this browser session.',
                });
            }

            return errorResponse(`The static demo does not implement ${method} ${path}`, 404);
        }

        return {
            handle,
            parseQuery,
            matchesQuery: (tweet, query) => matchesQuery(tweet, parseQuery(query)),
            hydrate: tweetId => hydrate(findSource(tweetId)),
            avatarUrl: userId => authors.get(String(userId))?.avatar_url || '',
            snapshot: () => clone(state),
        };
    }

    const api = {
        createStore,
        parseQuery,
        store: null,
        ready: Promise.resolve(null),
        avatarUrl(userId) {
            return api.store?.avatarUrl(userId) || '';
        },
        async handle(input, init) {
            await api.ready;
            return api.store.handle(input, init);
        },
        installFromData(data) {
            api.store = createStore(data);
            api.ready = Promise.resolve(api.store);
            return api.store;
        },
    };

    global.TweetNookDemo = api;
    if (global.TWEETNOOK_DEMO === true) {
        if (!nativeFetch) throw new Error('TweetNook demo requires the Fetch API');
        const fixtureUrl = global.TWEETNOOK_DEMO_DATA_URL || 'demo/data.json';
        api.ready = nativeFetch(new URL(fixtureUrl, global.document?.baseURI || global.location.href))
            .then(response => {
                if (!response.ok) throw new Error(`Could not load demo fixture (${response.status})`);
                return response.json();
            })
            .then(data => {
                api.store = createStore(data);
                return api.store;
            });
        global.fetch = async function demoFetch(input, init = {}) {
            const url = new URL(input instanceof Request ? input.url : String(input), global.document?.baseURI || global.location.href);
            if (url.pathname.includes('/api/')) return api.handle(input, init);
            return nativeFetch(input, init);
        };
    }
})(window);
