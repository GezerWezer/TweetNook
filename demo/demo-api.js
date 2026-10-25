/* TweetNook's static demo backend. All mutable state lives in this page session. */
(function installTweetNookDemo(global) {
    'use strict';

    const nativeFetch = typeof global.fetch === 'function' ? global.fetch.bind(global) : null;
    const structuredKeys = new Set([
        'from', 'to', 'mentions', 'since', 'until', 'since_time', 'until_time',
        'since_id', 'max_id', 'has', 'is', 'filter', 'min_retweets', 'min_faves',
        'min_replies', 'conversation_id', 'quoted_tweet_id', 'url', 'source',
        'card_name', 'tag', 'description', 'hashtag',
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

    function textResponse(text, headers = {}) {
        return new Response(text, {status: 200, headers: {'Content-Type': 'text/plain; charset=utf-8', ...headers}});
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
            if (value === 'note') return [tweet, tweet.quoted_tweet].some(post =>
                post?.note_history || Boolean(post?.raw_json?.birdwatch_pivot?.subtitle?.text));
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
            if (normalized === 'hidden') return Boolean(tweet.hidden);
            if (normalized === 'uncaptured') return Boolean(tweet.uncaptured);
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
        if (key === 'description') {
            // Every word must appear in the description; a trailing * matches a prefix.
            const words = String(tweet.media_tags?.description || '').toLowerCase().match(/[\p{L}\p{N}_]+/gu) || [];
            return value.split(/\s+/).filter(term => term.replace(/\*+$/, '')).every(term => {
                const word = term.replace(/\*+$/, '');
                return words.some(candidate => (term.endsWith('*') ? candidate.startsWith(word) : candidate === word));
            });
        }
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
                api_mode: 'paid',
                model: fixture.demo_metadata?.tagging?.model || 'gemini-3.6-flash',
                thinking_level: 'low',
                google_search: true,
                free_batch_size: 6,
                free_rpm: 10,
                free_rpd: 20,
                processing_tier: 'standard',
                daily_spend_limit_usd: null,
                unlimited_spend: false,
                search_safety_reserve: 100,
                tagging_context: ['local archives', 'software'],
                additional_instructions: null,
                max_media_size_mb: 100,
            },
            // Configuration page: the server's schema and defaults (demo/config.json), saved in this session.
            config: options.config ? {
                schema: clone(options.config.schema),
                defaults: clone(options.config.defaults),
                values: clone(options.config.defaults),
                explicit: [],
            } : null,
            notices: [
                {
                    id: 'demo-activity-replay',
                    severity: 'info',
                    action: 'schedule',
                    dismissed: false,
                    created_at: state.generated_at,
                    title: 'Demo activity replay',
                    message: 'Sync and activity history replay this local demo archive. No Twitter/X or model requests are sent.',
                },
            ],
        };

        // Quotes attach two levels deep: a quote and its own quote, as X nests them.
        function hydrate(source, quoteLevels = 2) {
            if (!source) return null;
            const author = authors.get(String(source.author_id));
            const engagement = source.engagement || {};
            const raw = {
                rest_id: String(source.tweet_id),
                source: source.source || 'Twitter Web App',
                core: {user_results: {result: authorRaw(author)}},
                legacy: {
                    full_text: source.text,
                    ...(Array.isArray(source.display_text_range)
                        ? {display_text_range: clone(source.display_text_range)} : {}),
                    created_at: source.created_at,
                    conversation_id_str: source.conversation_id,
                    in_reply_to_status_id_str: source.reply_to_id,
                    in_reply_to_screen_name: source.reply_to_username,
                    quoted_status_id_str: source.quoted_tweet_id,
                    is_quote_status: Boolean(source.quoted_tweet_id),
                    quoted_status_permalink: source.quote_short_url ? {url: source.quote_short_url} : undefined,
                    reply_count: engagement.replies || 0,
                    retweet_count: engagement.retweets || 0,
                    quote_count: source.quoted_tweet_id ? 1 : 0,
                    favorite_count: engagement.likes || 0,
                    bookmark_count: engagement.bookmarks || 0,
                    entities: {
                        media: (source.media_short_urls || []).map(url => ({url})),
                        hashtags: (source.hashtags || []).map(text => ({text})),
                        user_mentions: (source.mentions || []).map(screen_name => ({screen_name})),
                        urls: (source.urls || []).map(url => ({
                            url: url.short_url || url.url || url.expanded_url,
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
                    title: 'Readers added context they thought people might want to know',
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
                article: clone(source.article || null),
                media_tags: source.tags?.length || source.description
                    ? {tags: [...(source.tags || [])], description: source.description || ''}
                    : null,
                qt_media_tags: null,
                hidden: Boolean(source.hidden),
                uncaptured: Boolean(source.uncaptured),
                note_history: Boolean(source.note_history),
                raw_json: raw,
                _demo: {
                    liked_order: source.liked_order,
                    verified: Boolean(author.verified),
                    engagement: clone(engagement),
                    hashtags: [...(source.hashtags || [])],
                    mentions: [...(source.mentions || [])],
                },
            };
            if (quoteLevels > 0 && source.quoted_tweet_id) {
                const quoted = hydrate(sourceTweets.get(String(source.quoted_tweet_id)), quoteLevels - 1);
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
            if (sort === 'most_likes') {
                return copy.sort((left, right) =>
                    Number(right.raw_json?.legacy?.favorite_count || 0) - Number(left.raw_json?.legacy?.favorite_count || 0)
                    || fixtureDate(right.created_at) - fixtureDate(left.created_at)
                    || String(right.tweet_id).localeCompare(String(left.tweet_id)));
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
            const fragments = query.toLowerCase().split(/\s+/).filter(Boolean);
            const counts = new Map();
            for (const tweet of state.tweets) {
                for (const tag of tweet.tags || []) {
                    const existing = [...counts.keys()].find(value => value.toLowerCase() === tag.toLowerCase()) || tag;
                    counts.set(existing, (counts.get(existing) || 0) + 1);
                }
            }
            return [...counts.entries()]
                .filter(([tag]) => fragments.every(fragment => tag.toLowerCase().includes(fragment)))
                .map(([tag, count]) => ({tag, count}))
                .sort((left, right) => right.count - left.count || left.tag.localeCompare(right.tag));
        }

        // A compressed, local replay of the published archive, using the production
        // pipeline's phase/log shapes. These are playback times, not captured run timings.
        const syncDurationSeconds = 180;
        const replayNotice = 'DEMO REPLAY: local archive only; no Twitter/X or model requests. Timings are playback timings.';

        function replayInventory() {
            const rows = state.tweets;
            const liked = rows.filter(tweet => tweet.collections.includes('like'));
            const assets = new Set();
            const links = new Set();
            for (const tweet of rows) {
                for (const media of [...(tweet.media || []), ...(tweet.article?.media || [])]) {
                    for (const url of [media.url, media.thumbnail_url, media.poster_url]) {
                        if (url) assets.add(url);
                    }
                }
                for (const url of tweet.urls || []) links.add(url.expanded_url || url.url);
            }
            return {
                bookmarks: rows.filter(tweet => tweet.collections.includes('bookmark')).length,
                likes: liked.length,
                context: rows.filter(tweet => !tweet.collections.length).length,
                articles: rows.filter(tweet => tweet.article).length,
                media: assets.size,
                urls: links.size,
                taggedLikes: liked.filter(tweet => tweet.tags?.length).length,
            };
        }

        function replayPhases(counts) {
            const phase = (key, title, start, end, total, unit, activity, summary, metrics = {}, extra = {}) => ({
                key, title, start, end, total, unit,
                summary: total === 0 ? `skipped due to no saved ${unit} to replay` : summary,
                metrics,
                detail: 'Demo replay · saved local data · no network requests',
                showRate: false,
                showEta: false,
                skip: total === 0,
                reason: total === 0 ? `no saved ${unit} to replay` : '',
                resolveScene(stepElapsed) {
                    const ratio = Math.max(0, Math.min(1, stepElapsed / (end - start || 1)));
                    const completed = Math.min(total, Math.floor(total * ratio));
                    return {completed, activity, counters: `${completed}/${total} ${unit} reviewed`};
                },
                ...extra,
            });
            return [
                phase('preflight:bookmarks,likes', 'Prepare', 0, 3, 1, 'archive',
                    'Opening the local demo archive', 'Demo archive ready'),
                phase('sync:bookmarks:head', 'Bookmarks', 3, 8, counts.bookmarks, 'bookmarks',
                    'Reading saved bookmarks', `${counts.bookmarks} saved bookmarks`,
                    {new_tweets: 0, tweets_seen: counts.bookmarks, pages: 0}),
                phase('sync:likes:head', 'Likes', 8, 13, counts.likes, 'likes',
                    'Reading saved Likes', `${counts.likes} saved Likes`,
                    {new_tweets: 0, tweets_seen: counts.likes, pages: 0}),
                phase('threads', 'Threads', 13, 75, counts.context, 'context tweets',
                    'Reviewing saved conversation context', `${counts.context} saved context tweets`,
                    {expanded: 0, already_known: counts.context, failed: 0}),
                phase('resurrection', 'Unavailable tweets', 75, 75, 0, 'unavailable tweets', '',
                    'skipped due to no unavailable tweets in the demo', {},
                    {reason: 'no unavailable tweets in the demo'}),
                phase('articles', 'Articles', 75, 95, counts.articles, 'articles',
                    'Reading saved article content', `${counts.articles} saved articles`,
                    {updated: 0, already_saved: counts.articles}),
                phase('media', 'Media', 95, 160, counts.media, 'items',
                    'Reviewing cached attachments and posters', `${counts.media} cached items · 0 downloads`,
                    {downloaded: 0, downloaded_bytes: 0, already_saved: counts.media, failed: 0}),
                phase('urls', 'Link previews', 160, 170, counts.urls, 'URLs',
                    'Reading saved URL records', `${counts.urls} saved URLs · 0 requests`,
                    {updated: 0, already_saved: counts.urls, failed: 0}),
                phase('tagging', 'Automated tags', 170, 180, counts.taggedLikes, 'tagged Likes',
                    'Reading cached tags for liked tweets', `${counts.taggedLikes} liked tweets have tags · 0 requests`,
                    {tagged: 0, already_tagged: counts.taggedLikes, requests: 0}),
            ];
        }

        function replaySummary(counts) {
            return `Demo replay · ${counts.bookmarks} bookmarks · ${counts.likes} likes · ${counts.context} context tweets`;
        }

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
            const steps = runtime.activity.phases.map(phase => {
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
                    reason: stateName === 'skipped'
                        ? scheduledSkip ? phase.reason || '' : 'an earlier failure stopped the command'
                        : null,
                    paused: false,
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
                origin: 'demo',
                pid: 1,
                title: 'tweetnook sync',
                running: !complete,
                success: complete ? !stopped : null,
                stopped,
                summary: stopped
                    ? 'Stopped by user'
                    : complete
                        ? replaySummary(runtime.activity.counts)
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
                    ? [{level: 'error', message: 'Stopped by user', count: 1, latest: null, step: steps.find(step => step.state === 'failed')?.key || null}]
                    : [],
            };
            if (complete) {
                runtime.lastActivity = snapshot;
                runtime.activity = null;
            }
            return snapshot;
        }

        const WEEKDAY_PLURALS = ['Mondays', 'Tuesdays', 'Wednesdays', 'Thursdays', 'Fridays', 'Saturdays', 'Sundays'];
        function ordinal(n) {
            const suffix = ['th', 'st', 'nd', 'rd'], v = n % 100;
            return n + (suffix[(v - 20) % 10] || suffix[v] || suffix[0]);
        }
        // Same wording as scheduler.schedule_label() and schedule_description().
        function scheduleLabel(config) {
            if (config.cadence === 'hours') return Number(config.every_hours) === 1 ? 'Every hour' : `Every ${config.every_hours} hours`;
            const [hour, minute] = String(config.time || '03:00').split(':').map(Number);
            const clock = `${hour % 12 || 12}:${String(minute).padStart(2, '0')} ${hour >= 12 ? 'PM' : 'AM'}`;
            if (config.cadence === 'daily') return `Daily at ${clock}`;
            if (config.cadence === 'weekly') return `${WEEKDAY_PLURALS[Number(config.weekday) || 0]} at ${clock}`;
            return `Monthly on the ${ordinal(Number(config.day_of_month) || 1)} at ${clock}`;
        }
        function scheduleDescription(config) {
            if (config.cadence === 'hours') return `Every ${config.every_hours} ${Number(config.every_hours) === 1 ? 'hour' : 'hours'}`;
            const base = {daily: 'Every day', weekly: 'Every week'}[config.cadence] || 'Every month';
            return config.randomize_time ? `${base} · randomized delay` : base;
        }
        // The next run in the browser's time zone, before any random delay.
        function nextScheduledRun(config) {
            const now = new Date(nowSeconds() * 1000);
            if (config.cadence === 'hours') return new Date(now.getTime() + Number(config.every_hours || 6) * 3600000);
            const [hour, minute] = String(config.time || '03:00').split(':').map(Number);
            const next = new Date(now);
            next.setHours(hour, minute, 0, 0);
            const lastDay = date => new Date(date.getFullYear(), date.getMonth() + 1, 0).getDate();
            const matches = () => config.cadence === 'daily'
                || (config.cadence === 'weekly' && (next.getDay() + 6) % 7 === Number(config.weekday))
                || (config.cadence === 'monthly' && next.getDate() === Math.min(Number(config.day_of_month), lastDay(next)));
            for (let guard = 0; guard < 400 && (next <= now || !matches()); guard++) next.setDate(next.getDate() + 1);
            return next;
        }

        function scheduleStatus() {
            const config = clone(runtime.schedule);
            if (!config.enabled) {
                return {
                    configured: false,
                    enabled: false,
                    relative: 'Not configured',
                    date: 'Set up scheduling in Settings',
                    description: 'Scheduled syncs are off',
                    label: scheduleLabel(config),
                    next_run_at: null,
                    config,
                };
            }
            const next = nextScheduledRun(config);
            const hours = Math.max(0, Math.round((next.getTime() / 1000 - nowSeconds()) / 3600));
            return {
                configured: true,
                enabled: true,
                relative: hours < 1 ? 'in less than an hour' : hours < 48 ? `in ${hours} hour${hours === 1 ? '' : 's'}` : `in ${Math.round(hours / 24)} days`,
                date: `${next.toLocaleDateString('en-US', {weekday: 'short', month: 'short', day: 'numeric'})} at ${next.toLocaleTimeString('en-US', {hour: 'numeric', minute: '2-digit'})}`,
                description: scheduleDescription(config),
                label: scheduleLabel(config),
                next_run_at: next.getTime() / 1000,
                config,
            };
        }

        // These histories replay the current fixture, not a private server/account.
        // The log grammar follows production activity_history; origin and the first
        // line make the local replay and its artificial playback duration explicit.
        function sampleRun(runId, title, seconds, summary, lines) {
            const startedAt = fixtureDate(state.generated_at) || Math.floor(nowSeconds());
            return {
                run: {
                    run_id: runId,
                    title,
                    origin: 'demo',
                    status: 'completed',
                    started_at: startedAt,
                    completed_at: startedAt + seconds,
                    exit_code: 0,
                    summary,
                },
                log: [replayNotice, ...lines].join('\n') + '\n',
            };
        }
        const initialReplayCounts = replayInventory();
        const sampleRuns = [
            sampleRun('demo-archive-replay', 'tweetnook sync', syncDurationSeconds,
                replaySummary(initialReplayCounts), [
                    'tweetnook sync: started',
                    ...replayPhases(initialReplayCounts).flatMap(phase => phase.skip
                        ? [`${phase.title}: ${phase.summary.startsWith('skipped') ? phase.summary : `skipped due to ${phase.reason}`}`]
                        : [
                            `${phase.title}: starting · total=${phase.total} ${phase.unit} · ${phase.detail}`,
                            `${phase.title}: complete · ${phase.summary}`,
                        ]),
                    `tweetnook sync: complete · ${replaySummary(initialReplayCounts)}`,
                ]),
            sampleRun('demo-tags-replay', 'tweetnook tag', 0,
                `Demo replay · ${initialReplayCounts.taggedLikes}/${initialReplayCounts.likes} liked tweets have cached tags`, [
                    'tweetnook tag: started',
                    `Automated tags: complete · ${initialReplayCounts.taggedLikes}/${initialReplayCounts.likes} liked tweets have cached tags · 0 model requests`,
                    'tweetnook tag: complete · Demo replay · cached results only',
                ]),
        ];

        // The simulated sync (running or last finished) as an Activity logs run.
        function liveRun() {
            const snapshot = activitySnapshot() || runtime.lastActivity;
            if (!snapshot?.run_id) return null;
            const status = snapshot.running ? 'running' : snapshot.stopped ? 'stopped' : snapshot.success === false ? 'failed' : 'completed';
            return {
                snapshot,
                run: {
                    run_id: snapshot.run_id,
                    title: 'tweetnook sync',
                    origin: snapshot.origin || 'web',
                    status,
                    started_at: snapshot.started_at,
                    completed_at: snapshot.running ? null : snapshot.completed_at,
                    exit_code: snapshot.running ? null : status === 'completed' ? 0 : 1,
                    summary: snapshot.summary || '',
                },
            };
        }
        // The console.log lines the pipeline would have written so far, from the run's steps. Like a real
        // log it only grows: each read appends the lines that are new since the last one.
        const liveLogs = new Map();
        function liveLog(snapshot) {
            const log = liveLogs.get(snapshot.run_id) || {lines: [], seen: new Set()};
            liveLogs.set(snapshot.run_id, log);
            for (const line of liveLogLines(snapshot)) {
                if (log.seen.has(line)) continue;
                log.seen.add(line);
                log.lines.push(line);
            }
            return log.lines.join('\n') + '\n';
        }
        function liveLogLines(snapshot) {
            const lines = [replayNotice, 'tweetnook sync: started'];
            for (const step of snapshot.steps || []) {
                if (step.state === 'pending') continue;
                if (step.state === 'skipped') {
                    lines.push(`${step.title}: ${step.summary || 'skipped'}`);
                    continue;
                }
                lines.push(`${step.title}: starting${step.total ? ` · total=${step.total} ${step.unit}` : ''}${step.detail ? ` · ${step.detail}` : ''}`);
                if (step.total && step.state === 'active') {
                    lines.push(`${step.title}: progress · ${step.completed}/${step.total} ${step.unit}${step.activity ? ` · ${step.activity}` : ''}${step.counters ? ` · ${step.counters}` : ''}`);
                }
                if (step.state === 'complete') lines.push(`${step.title}: complete · ${step.summary || 'done'}`);
                if (step.state === 'failed') lines.push(`${step.title}: failed · ${step.summary || 'stopped'}`);
            }
            for (const issue of snapshot.issues || []) {
                lines.push(`${issue.level === 'error' ? 'ERROR' : 'WARNING'}: ${issue.message}${issue.count > 1 ? ` · occurrences=${issue.count}` : ''}`);
            }
            if (!snapshot.running) {
                const outcome = snapshot.stopped ? 'stopped' : snapshot.success === false ? 'failed' : 'complete';
                lines.push(`tweetnook sync: ${outcome}${snapshot.summary ? ` · ${snapshot.summary}` : ''}`);
            }
            return lines;
        }
        function findRun(runId) {
            const live = liveRun();
            if (live?.run.run_id === runId) return {run: live.run, log: liveLog(live.snapshot)};
            return sampleRuns.find(item => item.run.run_id === runId) || null;
        }
        // Complete lines after a byte offset, like activity_history.read_run_log_since().
        function logSince(text, offset) {
            const bytes = new TextEncoder().encode(text);
            const start = offset > bytes.length ? 0 : offset;
            let end = bytes.length;
            while (end > start && bytes[end - 1] !== 10) end -= 1;
            return {text: new TextDecoder().decode(bytes.slice(start, end)), offset: end};
        }

        // Config values the page edits, in the dotted-path form the server's POST /api/config takes.
        function applyConfigChanges(changes) {
            const config = runtime.config;
            for (const [path, value] of Object.entries(changes || {})) {
                const [section, ...rest] = path.split('.');
                const key = rest.join('.');
                if (!config.values[section] || !(key in config.values[section])) throw new Error(`Unknown setting ${path}`);
                if (path === 'sync.collections') {
                    const order = ['bookmarks', 'likes', 'tweets'];
                    const picked = value === null ? config.defaults.sync.collections : value;
                    if (!Array.isArray(picked) || !picked.length) throw new Error('Choose at least one collection to sync');
                    config.values.sync.collections = order.filter(item => picked.includes(item));
                } else {
                    config.values[section][key] = value === null ? clone(config.defaults[section][key]) : value;
                }
                const explicit = new Set(config.explicit);
                if (value === null) explicit.delete(path);
                else explicit.add(path);
                config.explicit = [...explicit].sort();
            }
        }

        function accounting() {
            const tagged = fixture.demo_metadata?.tagging;
            const recorded = tagged?.accounting;
            const day = tagged?.completed_at?.slice(0, 10);
            const today = new Date(nowSeconds() * 1000).toISOString().slice(0, 10);
            const thisMonth = Boolean(day && day.slice(0, 7) === today.slice(0, 7));
            const isToday = day === today;
            const requests = recorded?.requests || 0;
            const cost = recorded?.estimated_cost_usd || 0;
            const history = day ? [{date: day, requests, estimated_cost_usd: cost}] : [];
            return {
                scope: 'Liked demo posts only. Batch token estimates include retries; Search charges and account-wide usage are unknown.',
                pricing_note: 'This demo uses Gemini-reported tokens and official Gemini 3.6 Flash Batch rates: $0.375 per million input tokens and $1.875 per million output tokens, including thinking. Search cost is excluded because the provider did not report an authoritative call count. This is an estimate, not an invoice.',
                requests, tweets: recorded?.unique_tagged_tweets || 0, estimated_cost_usd: cost,
                today_requests: isToday ? requests : 0, today_estimated_cost_usd: isToday ? cost : 0,
                month_requests: thisMonth ? requests : 0, month_estimated_cost_usd: thisMonth ? cost : 0,
                lifetime_requests: requests, lifetime_estimated_cost_usd: cost,
                search_queries_this_month: thisMonth ? recorded?.reported_search_queries || 0 : 0,
                search_allowance: 5000, search_call_count_known: false,
                spend_history: history.filter(row => (Date.parse(today) - Date.parse(row.date)) / 86400000 < 365),
                lifetime_spend_history: history,
                models: recorded?.models || {}, tweet_types: recorded?.tweet_types || {},
            };
        }

        function statsSnapshot() {
            const tweets = currentTweets();
            const metadata = fixture.demo_metadata || {};
            const dateLabel = values => {
                const dates = values.map(fixtureDate).filter(value => value !== null);
                return dates.length ? new Date(Math.min(...dates) * 1000).toLocaleDateString('en-US', {
                    month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC',
                }) : '-';
            };
            const latestLabel = values => dateLabel(values.filter(value => fixtureDate(value) !== null)
                .sort((a, b) => fixtureDate(b) - fixtureDate(a)).slice(0, 1));
            const uniqueAuthors = new Set(tweets.map(tweet => tweet.author.id));
            const allMedia = tweets.flatMap(tweet => [...tweet.media, ...(tweet.article?.media || [])]);
            const eligible = state.tweets.filter(tweet => tweet.collections.includes('like'));
            const tagged = eligible.filter(tweet => tweet.tags?.length);
            const counts = new Map();
            for (const tweet of eligible) for (const tag of new Set(tweet.tags || [])) {
                counts.set(tag, (counts.get(tag) || 0) + 1);
            }
            const tags = [...counts].map(([tag, count]) => ({tag, count}))
                .sort((a, b) => b.count - a.count || a.tag.localeCompare(b.tag));
            const instances = eligible.reduce((total, tweet) => total + (tweet.tags || []).length, 0);
            const collectionRows = Object.entries({bookmark: 'Bookmarks', like: 'Likes', tweet: 'Authored Tweets'})
                .map(([collection, label]) => {
                    const matches = tweets.filter(tweet => tweet.collections.includes(collection));
                    return {
                        collection: label, count: matches.length,
                        oldest: dateLabel(matches.map(tweet => tweet.created_at)),
                        newest: latestLabel(matches.map(tweet => tweet.created_at)),
                        last_synced: latestLabel(matches.map(tweet => tweet.synced_at)),
                        backfill_status: 'Demo selection',
                    };
                });
            const assetBytes = metadata.asset_bytes || {};
            const groups = {core_media: new Set(), context_media: new Set(), supplementary_media: new Set(), avatars: new Set()};
            const savedOrQuoted = new Set(tweets.filter(tweet => tweet.collections.length).map(tweet => tweet.tweet_id));
            for (const tweet of state.tweets) if (tweet.quoted_tweet_id) savedOrQuoted.add(tweet.quoted_tweet_id);
            for (const tweet of tweets) {
                const target = savedOrQuoted.has(tweet.tweet_id) ? groups.core_media : groups.context_media;
                for (const media of [...tweet.media, ...(tweet.article?.media || [])]) {
                    if (media.url) target.add(media.url);
                    for (const field of ['thumbnail_url', 'poster_url']) if (media[field]) groups.supplementary_media.add(media[field]);
                }
                const avatar = authors.get(String(tweet.author.id))?.avatar_url;
                if (avatar) groups.avatars.add(avatar);
            }
            // Shared assets count once, under their first applicable category.
            const counted = new Set();
            const definitions = [
                ['core_media', 'Saved & Quoted Media', 'Attachments and article images on saved or quoted posts.'],
                ['context_media', 'Conversation Media', 'Attachments and article images on other conversation posts.'],
                ['supplementary_media', 'Video Posters & Thumbnails', 'Supporting images for GIFs and videos.'],
                ['avatars', 'Author Avatars', 'Profile images used by the curated archive.'],
            ];
            const segments = definitions.map(([id, name, description]) => {
                const paths = [...groups[id]].filter(path => !counted.has(path));
                for (const path of paths) counted.add(path);
                return {id, name, description, group: 'media', bytes: paths.reduce((sum, path) => sum + (assetBytes[path] || 0), 0),
                    count: paths.length, unit: 'files', formatted_count: `${paths.length} files`};
            });
            const dataBytes = metadata.fixture_bytes ?? new TextEncoder().encode(JSON.stringify(fixture)).length;
            segments.push({id: 'core_db', name: 'Demo Data (JSON)', group: 'database',
                description: 'The published fixture, including posts, profiles, tags and article text.',
                bytes: dataBytes, count: tweets.length, unit: 'records', formatted_count: `${tweets.length} archive records`});
            const totalBytes = segments.reduce((sum, segment) => sum + segment.bytes, 0);
            const sizeLabel = bytes => bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MiB`
                : bytes >= 1024 ? `${(bytes / 1024).toFixed(1)} KiB` : `${bytes} B`;
            const formatSegment = segment => ({...segment, formatted_size: sizeLabel(segment.bytes),
                percent: totalBytes ? Number((segment.bytes / totalBytes * 100).toFixed(2)) : 0});
            const mediaBytes = totalBytes - dataBytes;
            return {
                generated_at: new Date().toISOString(), age_seconds: 0, stale: false, refreshing: false, refresh_failed: false,
                summary: {
                    owner_user_id: fixture.owner_user_id, unique_posts: tweets.length, archive_tweets: tweets.length,
                    media_rows: allMedia.length, articles: tweets.filter(tweet => tweet.article).length,
                    urls: new Set(tweets.flatMap(tweet => tweet.urls.map(url => url.expanded_url || url.url || url.short_url)).filter(Boolean)).size,
                    profiles: uniqueAuthors.size, oldest_post: dateLabel(tweets.map(tweet => tweet.created_at)),
                    newest_post: latestLabel(tweets.map(tweet => tweet.created_at)), latest_sync: latestLabel(tweets.map(tweet => tweet.synced_at)),
                    missing_archive_tweets: 0, missing_archive_pct: 0,
                },
                collections: collectionRows,
                health: {
                    threads_expanded: (metadata.expanded_thread_ids || []).filter(id => sourceTweets.has(String(id))).length,
                    enrichment: {details_description: 'Captured posts and demo examples', available: tweets.length, done: tweets.length, incomplete: 0, pending: 0, transient: 0,
                        resurrected: 0, terminal: 0, unavailable: {total: 0, percent_of_archive: 0, retryable: 0, due: 0,
                            delayed: 0, permanent: 0, reasons: []}},
                },
                storage: {
                    total_bytes: totalBytes, formatted_total: sizeLabel(totalBytes), database_component_bytes_estimated: false,
                    media_sizes_measured: Boolean(metadata.asset_bytes), segments: segments.map(formatSegment),
                    simplified_segments: [
                        formatSegment({id: 'media', name: 'Media Files & Avatars', bytes: mediaBytes, count: counted.size,
                            formatted_count: `${counted.size} files`, description: 'Referenced publication media and avatars.'}),
                        formatSegment({id: 'database', name: 'Demo Data (JSON)', bytes: dataBytes, count: tweets.length,
                            formatted_count: `${tweets.length} archive records`, description: 'Published JSON fixture.'}),
                    ],
                },
                tags: {
                    scope: 'Liked demo posts', eligible_tweets: eligible.length, tagged_tweets: tagged.length,
                    untagged_eligible: eligible.length - tagged.length, unique_tags: tags.length, total_tag_instances: instances,
                    coverage_pct: eligible.length ? Number((tagged.length / eligible.length * 100).toFixed(1)) : 0,
                    avg_tags_per_tweet: tagged.length ? Number((instances / tagged.length).toFixed(1)) : 0,
                    top_tags: tags.slice(0, 20), generation: metadata.tagging || null,
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

            if ((path.endsWith('/api/tweets') || path.endsWith('/api/tweets/count')) && method === 'GET') {
                const collectionMap = {likes: 'like', bookmarks: 'bookmark', tweets: 'tweet'};
                const collection = collectionMap[url.searchParams.get('collection')] || null;
                const groups = parseQuery(url.searchParams.get('q') || '');
                const page = Math.max(1, Number(url.searchParams.get('page') || 1));
                const limit = Math.max(1, Math.min(100, Number(url.searchParams.get('limit') || 20)));
                let tweets = currentTweets().filter(tweet => collection
                    ? tweet.collections.includes(collection)
                    : tweet.collections.length > 0);
                // Hidden tweets stay out of results unless the query mentions is:hidden.
                const namesHidden = groups.some(group => group.some(token => (
                    token.kind === 'filter' && token.key === 'is' && token.value.toLowerCase() === 'hidden'
                )));
                const namesUncaptured = groups.some(group => group.some(token => (
                    token.kind === 'filter' && token.key === 'is' && token.value.toLowerCase() === 'uncaptured'
                )));
                tweets = tweets.filter(tweet => (namesHidden || !tweet.hidden)
                    && (namesUncaptured || !tweet.uncaptured) && matchesQuery(tweet, groups));
                if (path.endsWith('/count')) return jsonResponse({total: tweets.length});
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
                    highlight_terms: groups.flat().filter(token => !token.negated && (
                        token.kind === 'text' || token.key === 'hashtag'
                    )).map(token => token.kind === 'text'
                        ? (token.phrase ? `"${token.value}"` : token.value)
                        : `#${token.value}`),
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

            if (path.endsWith('/api/authors/profile') && method === 'GET') {
                const username = (url.searchParams.get('username') || '').replace(/^@/, '').toLowerCase();
                const author = authors.get(url.searchParams.get('author_id') || '')
                    || state.authors.find(candidate => username && candidate.username.toLowerCase() === username);
                if (!author) return jsonResponse({author_id: null, snapshots: []});
                return jsonResponse({
                    author_id: author.id,
                    snapshots: author.profile_snapshots?.length ? clone(author.profile_snapshots) : [{
                        display_name: author.display_name,
                        username: author.username,
                        description: author.description || '',
                        followers_count: author.followers_count || 0,
                        following_count: author.following_count || 0,
                        fetched_at: state.generated_at,
                    }],
                });
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

            const hiddenMatch = path.match(/\/api\/tweets\/(\d+)\/hidden$/);
            if (hiddenMatch && ['PUT', 'DELETE'].includes(method)) {
                const tweet = findSource(hiddenMatch[1]);
                if (!tweet) return errorResponse('Tweet not found', 404);
                tweet.hidden = method === 'PUT';
                return jsonResponse({hidden: tweet.hidden});
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

            if (path.endsWith('/api/stats/latest-sync')) return jsonResponse({latest_sync: statsSnapshot().summary.latest_sync});
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
                const counts = replayInventory();
                runtime.activity = {
                    counts,
                    phases: replayPhases(counts),
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

            if (path.endsWith('/api/activity/runs') && method === 'GET') {
                const live = liveRun();
                return jsonResponse({runs: [...(live ? [live.run] : []), ...sampleRuns.map(item => clone(item.run))]});
            }
            const runMatch = path.match(/\/api\/activity\/runs\/([^/]+)(\/log)?$/);
            if (runMatch && method === 'GET') {
                const found = findRun(decodeURIComponent(runMatch[1]));
                if (!found) return errorResponse('Run not found.', 404);
                if (!runMatch[2]) return jsonResponse(clone(found.run));
                const offset = url.searchParams.get('offset');
                if (offset === null) return textResponse(found.log);
                const since = logSince(found.log, Math.max(0, Number(offset) || 0));
                return textResponse(since.text, {'X-Log-Offset': String(since.offset)});
            }

            if (path.endsWith('/api/config/schema') && method === 'GET' && runtime.config) return jsonResponse(clone(runtime.config.schema));
            if (path.endsWith('/api/config/defaults') && method === 'GET' && runtime.config) return jsonResponse(clone(runtime.config.defaults));
            if (path.endsWith('/api/config') && ['GET', 'POST'].includes(method) && runtime.config) {
                if (method === 'POST') {
                    try {
                        applyConfigChanges(body.changes);
                    } catch (error) {
                        return errorResponse(error.message, 400);
                    }
                }
                return jsonResponse({values: clone(runtime.config.values), explicit: clone(runtime.config.explicit)});
            }

            if (path.endsWith('/api/notices') && method === 'GET') return jsonResponse({notices: clone(runtime.notices)});
            if (path.endsWith('/api/notices/dismiss-all') && method === 'POST') {
                const unread = runtime.notices.filter(item => !item.dismissed);
                unread.forEach(item => { item.dismissed = true; });
                return jsonResponse({dismissed: unread.length});
            }
            if (path.endsWith('/api/notices/dismissed') && method === 'DELETE') {
                const before = runtime.notices.length;
                runtime.notices = runtime.notices.filter(item => !item.dismissed);
                return jsonResponse({cleared: before - runtime.notices.length});
            }
            const dismissMatch = path.match(/\/api\/notices\/([^/]+)\/dismiss$/);
            if (dismissMatch && method === 'POST') {
                const notice = runtime.notices.find(item => item.id === decodeURIComponent(dismissMatch[1]));
                if (notice) notice.dismissed = true;
                return jsonResponse({dismissed: true});
            }

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
                counts: initialReplayCounts,
                phases: replayPhases(initialReplayCounts),
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
        const base = global.document?.baseURI || global.location.href;
        const load = (fileUrl, label) => nativeFetch(new URL(fileUrl, base)).then(response => {
            if (!response.ok) throw new Error(`Could not load demo ${label} (${response.status})`);
            return response.json();
        });
        api.ready = Promise.all([
            load(global.TWEETNOOK_DEMO_DATA_URL || 'demo/data.json', 'fixture'),
            load(global.TWEETNOOK_DEMO_CONFIG_URL || 'demo/config.json', 'configuration'),
        ]).then(([data, config]) => {
            api.store = createStore(data, {seedLastActivity: true, config});
            return api.store;
        });
        global.fetch = async function demoFetch(input, init = {}) {
            const url = new URL(input instanceof Request ? input.url : String(input), global.document?.baseURI || global.location.href);
            if (url.pathname.includes('/api/')) return api.handle(input, init);
            return nativeFetch(input, init);
        };
    }
})(window);
