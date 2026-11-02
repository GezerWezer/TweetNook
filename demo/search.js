/* Search and ordering for the static demo's fixture. */
(function installDemoSearch(global) {
    'use strict';

    const structuredKeys = new Set([
        'from', 'to', 'mentions', 'since', 'until', 'since_time', 'until_time',
        'since_id', 'max_id', 'has', 'is', 'filter', 'min_retweets', 'min_faves',
        'min_replies', 'conversation_id', 'quoted_tweet_id', 'url', 'source',
        'card_name', 'tag', 'description', 'hashtag',
    ]);


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


    global.TweetNookDemoSearch = {parseQuery, searchableText, matchesQuery, sortedTweets};
})(window);
