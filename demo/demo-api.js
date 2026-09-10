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

    // Captured from a production stats response and intentionally projected onto the
    // small synthetic archive below. Keep the response's category mix and labels, but
    // do not expose the real account ID or production-scale counts in the public demo.
    const STATS_REFERENCE = {
        archiveTweets: 636770,
        collections: {
            bookmark: {label: 'Bookmarks', backfill_status: 'none saved'},
            like: {label: 'Likes', backfill_status: 'none saved'},
            tweet: {label: 'Authored Tweets', backfill_status: 'none saved'},
        },
        enrichment: {
            threadsExpanded: 33402,
            resurrected: 42,
            unavailable: {
                total: 2823,
                reasons: [
                    {reason: 'protected_account', label: 'Protected account', count: 293, due: 114, delayed: 179, permanent: 0},
                    {reason: 'suspended_account', label: 'Suspended account', count: 839, due: 0, delayed: 839, permanent: 0},
                    {reason: 'account_missing', label: 'Missing account', count: 29, due: 0, delayed: 29, permanent: 0},
                    {reason: 'withheld', label: 'Withheld', count: 3, due: 0, delayed: 3, permanent: 0},
                    {reason: 'not_found', label: 'Not found', count: 0, due: 0, delayed: 0, permanent: 0},
                    {reason: 'unavailable_unknown', label: 'Unknown availability', count: 1659, due: 261, delayed: 1398, permanent: 0},
                    {reason: 'deleted_by_author', label: 'Deleted by author', count: 0, due: 0, delayed: 0, permanent: 0},
                    {reason: 'archive_deleted', label: 'Deleted in archive', count: 0, due: 0, delayed: 0, permanent: 0},
                ],
            },
        },
        storage: {
            totalBytes: 79447340695,
            detailed: [
                {id: 'context_media', group: 'media', name: 'Context Media (Thread Extensions)', bytes: 48900386028, percent: 61.55, description: 'Downloaded media from surrounding thread context.'},
                {id: 'core_media', group: 'media', name: 'Core Media (Bookmarked & Quoted)', bytes: 18449101264, percent: 23.22, description: 'Downloaded media attached to saved and quoted tweets.'},
                {id: 'search_index', group: 'database', name: 'Search Index & DB Overhead', bytes: 7617931201, percent: 9.59, description: 'SQLite indexes, FTS data, structural overhead, and active sidecars.'},
                {id: 'threads', group: 'database', name: 'Thread Extensions & Context', bytes: 2709334002, percent: 3.41, description: 'Estimated payload size for fetched conversation context.'},
                {id: 'supplementary_media', group: 'media', name: 'Supplementary Media Files', bytes: 1535370967, percent: 1.93, description: 'Video posters, thumbnails, and other derived supporting files.'},
                {id: 'avatars', group: 'media', name: 'Avatar Image Cache', bytes: 152418180, percent: 0.19, description: 'Locally cached author profile images.'},
                {id: 'core_db', group: 'database', name: 'Core Tweet Database', bytes: 80261422, percent: 0.1, description: 'Estimated payload size for saved tweet records and raw data.'},
                {id: 'tags', group: 'database', name: 'Tagging & Topic Metadata', bytes: 2207853, percent: 0.0, description: 'Estimated payload size for search tags and topic metadata.'},
                {id: 'user_profiles', group: 'database', name: 'User Profiles & Handles', bytes: 253911, percent: 0.0, description: 'Estimated payload size for stored author names and handles.'},
                {id: 'articles', group: 'database', name: 'Article Content & Cards', bytes: 75867, percent: 0.0, description: 'Estimated payload size for article bodies, previews, and cards.'},
            ],
            simplified: [
                {id: 'media', name: 'Media Files & Avatars', bytes: 69037276439, percent: 86.9, description: 'Downloaded tweet media, thread attachments, and cached avatars.'},
                {id: 'database', name: 'Database & Indexes', bytes: 10410064256, percent: 13.1, description: 'Primary SQLite archive, full-text indexes, and active sidecars.'},
            ],
        },
        tags: {
            eligible: 11790,
            coverage: 72.7,
            unique: 7105,
            totalInstances: 20091,
            average: 2.3,
        },
    };

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
        const initialTaggedTweetCount = state.tweets.filter(tweet => tweet.tags?.length).length;
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
                detail: 'sign-in · archive owner · 2 collection checks',
                rateUnit: 'checks/s',
                summary: 'Connected · 2/2 collections ready',
                scenes: [
                    {at: 0, completed: 0, activity: 'Connecting to Twitter/X', counters: ''},
                    {at: 0.2, completed: 1, activity: 'Checking archive ownership', counters: 'Signed in'},
                    {at: 0.4, completed: 2, activity: 'Preparing Twitter/X requests', counters: 'Signed in · archive owner confirmed'},
                    {at: 0.6, completed: 3, activity: 'Checking Bookmarks', counters: '2 request types ready'},
                    {at: 0.8, completed: 4, activity: 'Checking Likes', counters: '1/2 collections ready'},
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
                showProgress: false,
                summary: '1 page · 20 tweets · archive is up to date',
                metrics: {new_tweets: 12, tweets_seen: 20, pages: 1},
                scenes: [
                    {at: 0, completed: 0, activity: 'Fetching page 1', counters: '0 pages · 0 tweets saved'},
                    {at: 0.45, completed: 0, activity: 'Saving 20 tweets from page 1', counters: '0 pages · 0 tweets saved'},
                    {at: 0.8, completed: 1, activity: 'Archive is up to date', counters: '1 page · 20 tweets saved'},
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
                showProgress: false,
                summary: '1 page · 20 tweets · archive is up to date',
                metrics: {new_tweets: 8, tweets_seen: 20, pages: 1},
                scenes: [
                    {at: 0, completed: 0, activity: 'Fetching page 1', counters: '0 pages · 0 tweets saved'},
                    {at: 0.45, completed: 0, activity: 'Saving 20 tweets from page 1', counters: '0 pages · 0 tweets saved'},
                    {at: 0.8, completed: 1, activity: 'Archive is up to date', counters: '1 page · 20 tweets saved'},
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
                summary: '6 expanded · 148 already known',
                metrics: {expanded: 6, already_known: 148, failed: 0},
                scenes: [
                    {at: 0, completed: 0, activity: 'Selecting candidates', counters: ''},
                    {at: 0.02, completed: 0, activity: 'Loading prior expansions', counters: ''},
                    {at: 0.12, completed: 0, activity: 'Prior expansions loaded', counters: ''},
                    {at: 0.14, completed: 0, activity: 'Reading saved tweets', counters: ''},
                    {at: 0.2, completed: 0, activity: 'Membership pass over 126 archived tweets', counters: '120 already expanded'},
                    {at: 0.22, completed: 0, activity: 'Reading saved tweet IDs', counters: ''},
                    {at: 0.28, completed: 0, activity: 'Saved tweet IDs loaded', counters: ''},
                    {at: 0.3, completed: 0, activity: 'Reading URL references', counters: ''},
                    {at: 0.47, completed: 0, activity: 'Preparing conversation lookups', counters: '5 membership · 1 quoted'},
                    {at: 0.5, completed: 0, activity: 'Preparing conversation lookups', counters: '5 membership · 1 quoted'},
                    {at: 0.59, completed: 1, activity: 'Fetching thread context', counters: '1 expanded · 148 already known'},
                    {at: 0.68, completed: 2, activity: 'Fetching thread context', counters: '2 expanded · 148 already known'},
                    {at: 0.77, completed: 3, activity: 'Fetching thread context', counters: '3 expanded · 148 already known'},
                    {at: 0.86, completed: 4, activity: 'Fetching thread context', counters: '4 expanded · 148 already known'},
                    {at: 0.94, completed: 5, activity: 'Fetching thread context', counters: '5 expanded · 148 already known'},
                    {at: 0.985, completed: 6, activity: 'Saving conversation relationships', counters: '6 expanded · 148 already known'},
                ],
            },
            {
                key: 'resurrection',
                title: 'Unavailable tweets',
                start: 75,
                end: 171,
                total: 16,
                unit: 'tweets',
                detail: '16 due selected · 100-request ceiling',
                rateUnit: 'tweets/s',
                summary: '1 restored · 15 still unavailable',
                metrics: {restored: 1, still_unavailable: 15, retry_later: 2, remaining_due: 3},
                resolveScene(stepElapsed) {
                    if (stepElapsed < 1) {
                        return {
                            completed: 0,
                            activity: 'Preparing recovery checks',
                            counters: '',
                        };
                    }
                    const completed = Math.min(15, Math.floor(stepElapsed / 6));
                    const returned = completed >= 4 ? 1 : 0;
                    const unavailable = completed - returned;
                    if (stepElapsed < 6 || completed === 15) {
                        return {
                            completed,
                            activity: 'Rate-limit pacing · one request every 6s',
                            counters: [returned ? `${returned} restored` : '', unavailable ? `${unavailable} still unavailable` : ''].filter(Boolean).join(' · '),
                        };
                    }
                    return {
                        completed,
                        activity: 'Checking an unavailable tweet',
                        counters: [returned ? `${returned} restored` : '', unavailable ? `${unavailable} still unavailable` : ''].filter(Boolean).join(' · '),
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
                summary: '28 downloaded · 18.6 MiB',
                metrics: {downloaded: 28, downloaded_bytes: 19503514, already_saved: 4, failed: 0},
                scenes: [
                    {at: 0, completed: 0, activity: 'Downloading animated GIF', counters: ''},
                    {at: 0.2, completed: 5, activity: 'Downloading photo', counters: '5 downloaded · 3.4 MiB'},
                    {at: 0.4, completed: 11, activity: 'Downloading video', counters: '11 downloaded · 7.2 MiB'},
                    {at: 0.6, completed: 17, activity: 'Downloading video', counters: '17 downloaded · 11.3 MiB'},
                    {at: 0.8, completed: 23, activity: 'Downloading photo', counters: '23 downloaded · 15.1 MiB'},
                    {at: 0.96, completed: 28, activity: 'Saving download metadata', counters: '28 downloaded · 18.6 MiB'},
                ],
            },
            {
                key: 'urls',
                title: 'Link previews',
                start: 176,
                end: 180,
                total: 2,
                unit: 'URLs',
                detail: 'saved URLs · redirects followed · canonical metadata persisted',
                rateUnit: 'URLs/s',
                summary: '2 updated',
                metrics: {updated: 2, failed: 0},
                scenes: [
                    {at: 0, completed: 0, activity: 'Fetching example.com', counters: ''},
                    {at: 0.45, completed: 1, activity: 'Fetching docs.example.com', counters: '1 updated'},
                    {at: 0.9, completed: 2, activity: 'Saving link previews', counters: '2 updated'},
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
                    metrics: stateName === 'complete' ? clone(phase.metrics || {}) : {},
                    completed,
                    total: phase.total,
                    unit: phase.unit,
                    counters: stateName === 'active' ? scene.counters : '',
                    detail: phase.detail,
                    elapsed_seconds: stepElapsed,
                    rate,
                    rate_unit: phase.rateUnit || `${phase.unit}/s`,
                    eta_seconds: stateName === 'active' ? eta : null,
                    show_progress: phase.showProgress !== false,
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
            const archiveTweetCount = tweets.length;
            const mediaCount = tweets.reduce((total, tweet) => total + tweet.media.length, 0);
            const currentTaggedTweetCount = state.tweets.filter(tweet => tweet.tags?.length).length;
            const collectionRows = Object.entries(STATS_REFERENCE.collections).map(([collection, metadata]) => {
                const matches = tweets.filter(tweet => tweet.collections.includes(collection));
                return {
                    collection: metadata.label,
                    count: matches.length,
                    oldest: matches.length ? new Date(Math.min(...matches.map(tweet => Date.parse(tweet.created_at)))).toLocaleDateString('en-US', {month: 'short', day: 'numeric', year: 'numeric'}) : '-',
                    newest: matches.length ? new Date(Math.max(...matches.map(tweet => Date.parse(tweet.created_at)))).toLocaleDateString('en-US', {month: 'short', day: 'numeric', year: 'numeric'}) : '-',
                    last_synced: 'Feb 15, 2026 12:10 pm',
                    backfill_status: metadata.backfill_status,
                };
            });
            const unavailableTotal = Math.min(archiveTweetCount, 5);
            const sourceUnavailableReasons = STATS_REFERENCE.enrichment.unavailable.reasons;
            const sourceUnavailableTotal = STATS_REFERENCE.enrichment.unavailable.total;
            const scaledReasonCounts = new Map(sourceUnavailableReasons.map(reason => [
                reason.reason,
                Math.floor(unavailableTotal * reason.count / sourceUnavailableTotal),
            ]));
            let unallocatedReasons = unavailableTotal - [...scaledReasonCounts.values()].reduce((total, count) => total + count, 0);
            const largestReasonRemainders = sourceUnavailableReasons
                .filter(reason => reason.count > 0)
                .map(reason => ({
                    reason: reason.reason,
                    remainder: unavailableTotal * reason.count / sourceUnavailableTotal
                        - Math.floor(unavailableTotal * reason.count / sourceUnavailableTotal),
                }))
                .sort((left, right) => right.remainder - left.remainder);
            for (const item of largestReasonRemainders) {
                if (unallocatedReasons <= 0) break;
                scaledReasonCounts.set(item.reason, scaledReasonCounts.get(item.reason) + 1);
                unallocatedReasons -= 1;
            }
            const unavailableReasons = STATS_REFERENCE.enrichment.unavailable.reasons.map(reason => {
                const count = scaledReasonCounts.get(reason.reason) || 0;
                const permanent = count && reason.permanent > 0 ? 1 : 0;
                const retryable = count - permanent;
                const due = retryable > 0 && reason.due > 0
                    ? Math.min(retryable, Math.max(1, Math.round(retryable * reason.due / reason.count)))
                    : 0;
                return {
                    ...reason,
                    count,
                    percent_of_missing: unavailableTotal ? Number((count / unavailableTotal * 100).toFixed(1)) : 0,
                    percent_of_archive: archiveTweetCount ? Number((count / archiveTweetCount * 100).toFixed(1)) : 0,
                    retryable,
                    due,
                    delayed: retryable - due,
                    permanent,
                };
            });
            const resurrected = archiveTweetCount > unavailableTotal ? 1 : 0;
            const enriched = Math.max(0, archiveTweetCount - unavailableTotal - resurrected);
            const storageTotalBytes = 400 * 1024;
            const formatDemoStorageSize = bytes => {
                if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
                if (bytes >= 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KiB`;
                return `${bytes} B`;
            };
            const scaleStorageSegments = (sourceSegments, counts, countLabels) => {
                const sourceTotalBytes = sourceSegments.reduce((total, segment) => total + segment.bytes, 0);
                const bytes = sourceSegments.map(segment => Math.max(1, Math.round(
                    storageTotalBytes * segment.bytes / sourceTotalBytes,
                )));
                bytes[0] += storageTotalBytes - bytes.reduce((total, value) => total + value, 0);
                return sourceSegments.map((segment, index) => ({
                    ...segment,
                    bytes: bytes[index],
                    count: counts[segment.id] ?? 0,
                    unit: segment.group === 'media' ? 'files' : 'records',
                    formatted_count: countLabels[segment.id] || `${(counts[segment.id] ?? 0).toLocaleString()} ${segment.group === 'media' ? 'files' : 'records'}`,
                    formatted_size: formatDemoStorageSize(bytes[index]),
                    percent: Number((bytes[index] / storageTotalBytes * 100).toFixed(2)),
                }));
            };
            const storageCounts = {
                context_media: 17,
                core_media: 6,
                search_index: 1,
                threads: archiveTweetCount,
                supplementary_media: 4,
                avatars: uniqueAuthors.size,
                core_db: archiveTweetCount,
                tags: Math.max(1, tags.length),
                user_profiles: uniqueAuthors.size,
                articles: 1,
            };
            const storageCountLabels = {
                context_media: '10 photos · 7 videos/gifs',
                core_media: '3 photos · 3 videos/gifs',
                search_index: '1 active index',
                threads: `${archiveTweetCount.toLocaleString()} context objects`,
                supplementary_media: '4 supporting files',
                avatars: `${uniqueAuthors.size} cached avatars`,
                core_db: `${archiveTweetCount.toLocaleString()} archive tweets`,
                tags: `${Math.max(1, tags.length)} topic tags`,
                user_profiles: `${uniqueAuthors.size} demo profiles`,
                articles: '1 article card',
            };
            const storageSegments = scaleStorageSegments(
                STATS_REFERENCE.storage.detailed,
                storageCounts,
                storageCountLabels,
            );
            const simplifiedStorageSegments = scaleStorageSegments(
                STATS_REFERENCE.storage.simplified,
                {media: mediaCount + uniqueAuthors.size, database: archiveTweetCount},
                {media: `${mediaCount} media files · ${uniqueAuthors.size} avatars`, database: `${archiveTweetCount.toLocaleString()} archive records · SQLite index`},
            );
            const tagEligibleTweets = Math.max(1, archiveTweetCount - 1);
            const baselineTaggedTweets = Math.round(tagEligibleTweets * STATS_REFERENCE.tags.coverage / 100);
            const tagDelta = currentTaggedTweetCount - initialTaggedTweetCount;
            const taggedTweets = Math.max(0, Math.min(tagEligibleTweets, baselineTaggedTweets + tagDelta));
            return {
                generated_at: new Date().toISOString(),
                age_seconds: 0,
                stale: false,
                refreshing: false,
                refresh_failed: false,
                summary: {
                    owner_user_id: 'demo_you',
                    unique_posts: archiveTweetCount,
                    media_rows: mediaCount,
                    articles: 1,
                    urls: 5,
                    profiles: uniqueAuthors.size,
                    oldest_post: 'Jan 28, 2026',
                    newest_post: 'Feb 15, 2026',
                    latest_sync: 'Feb 15, 2026',
                    missing_archive_tweets: unavailableTotal,
                    missing_archive_pct: archiveTweetCount ? Number((unavailableTotal / archiveTweetCount * 100).toFixed(1)) : 0,
                    archive_tweets: archiveTweetCount,
                },
                collections: collectionRows,
                health: {
                    threads_expanded: Math.max(1, Math.round(
                        STATS_REFERENCE.enrichment.threadsExpanded / STATS_REFERENCE.archiveTweets * archiveTweetCount,
                    )),
                    enrichment: {
                        available: enriched + resurrected,
                        done: enriched,
                        incomplete: 0,
                        pending: 0,
                        transient: 0,
                        resurrected,
                        terminal: unavailableTotal,
                        unavailable: {
                            total: unavailableTotal,
                            percent_of_archive: archiveTweetCount ? Number((unavailableTotal / archiveTweetCount * 100).toFixed(1)) : 0,
                            retryable: unavailableTotal,
                            due: unavailableReasons.reduce((total, reason) => total + reason.due, 0),
                            delayed: unavailableReasons.reduce((total, reason) => total + reason.delayed, 0),
                            permanent: unavailableReasons.reduce((total, reason) => total + reason.permanent, 0),
                            reasons: unavailableReasons,
                        },
                    },
                },
                storage: {
                    total_bytes: storageTotalBytes,
                    formatted_total: '400 KiB',
                    database_component_bytes_estimated: true,
                    segments: storageSegments,
                    simplified_segments: simplifiedStorageSegments,
                },
                tags: {
                    eligible_tweets: tagEligibleTweets,
                    tagged_tweets: taggedTweets,
                    untagged_eligible: tagEligibleTweets - taggedTweets,
                    unique_tags: Math.max(
                        tags.length,
                        Math.round(STATS_REFERENCE.tags.unique / STATS_REFERENCE.tags.eligible * tagEligibleTweets),
                    ),
                    total_tag_instances: Math.round(
                        STATS_REFERENCE.tags.totalInstances / STATS_REFERENCE.tags.eligible * tagEligibleTweets,
                    ),
                    coverage_pct: STATS_REFERENCE.tags.coverage,
                    avg_tags_per_tweet: STATS_REFERENCE.tags.average,
                    top_tags: tags.slice(0, 20),
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

        if (options.seedLastActivity) {
            const finishedAt = nowSeconds() - 600;
            runtime.activity = {
                runId: 'demo-last-sync',
                startedAt: finishedAt - syncDurationSeconds,
                stopped: false,
                stoppedAt: null,
            };
            activitySnapshot();
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
                api.store = createStore(data, {seedLastActivity: true});
                return api.store;
            });
        global.fetch = async function demoFetch(input, init = {}) {
            const url = new URL(input instanceof Request ? input.url : String(input), global.document?.baseURI || global.location.href);
            if (url.pathname.includes('/api/')) return api.handle(input, init);
            return nativeFetch(input, init);
        };
    }
})(window);
