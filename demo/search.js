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

    const filterValues = {
        has: ['article', 'media', 'image', 'video', 'links', 'note'],
        is: ['hidden', 'missing', 'quote', 'reply', 'resurrected', 'retweet', 'thread', 'uncaptured', 'verified'],
        filter: ['articles', 'images', 'links', 'media', 'native_video', 'nativeretweets',
            'quote', 'replies', 'self_threads', 'threads', 'verified', 'videos'],
    };
    const numericKeys = new Set(['since_time', 'until_time', 'since_id', 'max_id',
        'min_retweets', 'min_faves', 'min_replies']);
    const integerPattern = /^[+-]?\d+$/;
    const floatPattern = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i;

    function numericValue(value) { return value.replace(/(?<=\d)_(?=\d)/g, ''); }

    class SearchError extends Error {
        constructor(message, status = 400) { super(message); this.status = status; }
    }

    function dateBoundary(value) {
        if (!/^\d{4}-\d{2}-\d{2}(?:_\d{2}:\d{2}:\d{2})?$/.test(value)) return null;
        const timestamp = fixtureDate(value.replace('_', 'T') + (value.includes('_') ? 'Z' : 'T00:00:00Z'));
        if (timestamp === null) return null;
        const normalized = new Date(timestamp * 1000).toISOString();
        return normalized.slice(0, 10) === value.slice(0, 10) ? timestamp : null;
    }

    function validateFilter(key, value) {
        if (!structuredKeys.has(key)) throw new SearchError(`Unsupported search filter: ${key}:`);
        if (!value) throw new SearchError(`Search filter ${key}: requires a value.`);
        if (filterValues[key] && !filterValues[key].includes(value)) {
            throw new SearchError(`Unsupported ${key}: value '${value}'. Expected one of: ${[...filterValues[key]].sort().join(', ')}.`);
        }
        if (key === 'description' && !value.split(/\s+/).some(word => word.replace(/\*+$/, ''))) {
            throw new SearchError('Search filter description: requires a word to match.');
        }
        if (['since', 'until'].includes(key) && dateBoundary(value) === null) {
            throw new SearchError(`Search filter ${key}: expects YYYY-MM-DD.`);
        }
        if (numericKeys.has(key)) {
            const numeric = numericValue(value);
            if (key.endsWith('_time')) {
                if (!floatPattern.test(numeric) || !Number.isFinite(Number(numeric))) {
                    throw new SearchError(`Search filter ${key}: expects a finite timestamp.`);
                }
            } else {
                if (!integerPattern.test(numeric)) throw new SearchError(`Search filter ${key}: expects a number.`);
                const number = BigInt(numeric);
                if (number < -(1n << 63n) || number >= (1n << 63n)) {
                    throw new SearchError(`Search filter ${key}: expects a signed 64-bit integer.`);
                }
            }
        }
    }

    function parseQuery(query) {
        const groups = [];
        let joinWithOr = false, negateNext = false, expectingClause = false;
        const tokens = String(query || '').match(/-?[\p{L}\p{N}_]+:(?:"[^"]*"|[^\s]+)|-?"[^"]*"|[^\s]+/gu) || [];
        for (let token of tokens) {
            if (token === 'OR' || token === 'AND') {
                if (!groups.length || joinWithOr || expectingClause) {
                    throw new SearchError(`${token} must appear between two search clauses.`);
                }
                joinWithOr = token === 'OR';
                expectingClause = true;
                continue;
            }
            if (token === 'NOT') {
                if (negateNext) throw new SearchError('NOT must be followed by a search clause.');
                negateNext = true;
                expectingClause = true;
                continue;
            }
            const negated = token.startsWith('-') !== negateNext;
            if (token.startsWith('-')) token = token.slice(1);
            negateNext = false;
            const filter = token.match(/^([\p{L}\p{N}_]+):("[^"]*"|[^\s]+)$/u);
            let clause;
            if (filter) {
                const key = filter[1].toLowerCase();
                const value = filter[2].replace(/^"(.*)"$/, '$1').toLowerCase();
                validateFilter(key, value);
                clause = {kind: 'filter', key, value, negated};
            } else if (token.startsWith('#') && token.length > 1) {
                clause = {kind: 'filter', key: 'hashtag', value: token.slice(1).toLowerCase(), negated};
            } else {
                if (!token.replace(/^"(.*)"$/, '$1').trim()) throw new SearchError('Search terms cannot be empty.');
                clause = {kind: 'text', value: token, negated};
            }
            if (joinWithOr) groups.at(-1).push(clause);
            else groups.push([clause]);
            joinWithOr = false;
            expectingClause = false;
        }
        if (joinWithOr || negateNext || expectingClause) throw new SearchError('Search query cannot end with an operator.');
        return groups;
    }

    function inRanges(point, ranges) {
        let low = 0, high = ranges.length - 1;
        while (low <= high) {
            const middle = (low + high) >>> 1;
            const [start, end] = ranges[middle];
            if (point < start) high = middle - 1;
            else if (point > end) low = middle + 1;
            else return true;
        }
        return false;
    }

    // The generated tables use SQLite's own unicode61 folds, including its legacy
    // diacritic exceptions. Browser Unicode versions and normalization differ.
    function normalizedWords(value) {
        const unicode = global.TweetNookDemoUnicode;
        const words = [];
        let word = '';
        for (const char of String(value || '')) {
            const point = char.codePointAt(0);
            if (inRanges(point, unicode.separators)) {
                if (word) words.push(word);
                word = '';
            } else if (!inRanges(point, unicode.ignored)) word += unicode.folds[char] ?? char;
        }
        if (word) words.push(word);
        return words;
    }

    function textColumns(tweet) {
        return [tweet.author?.username, tweet.author?.display_name, tweet.text, tweet._demo?.note_tweet_text]
            .map(normalizedWords);
    }

    function searchableText(tweet) {
        return [tweet.text, tweet.author?.display_name, tweet.author?.username].filter(Boolean).join(' ').toLowerCase();
    }

    function phraseSpec(value) {
        const quoted = value.startsWith('"') && value.endsWith('"');
        const prefix = !quoted && /^[\p{L}\p{N}_]+\*$/u.test(value);
        return {words: normalizedWords(quoted ? value.slice(1, -1) : value), prefix};
    }

    function phraseFrequency(columns, spec) {
        if (!spec.words.length) return 0;
        let frequency = 0;
        for (const words of columns) {
            for (let i = 0; i <= words.length - spec.words.length; i++) {
                if (spec.words.every((word, j) => spec.prefix && j === spec.words.length - 1
                    ? words[i + j].startsWith(word) : words[i + j] === word)) frequency++;
            }
        }
        return frequency;
    }

    function matchesText(tweet, token) {
        return phraseFrequency(textColumns(tweet), phraseSpec(token.value)) > 0;
    }

    function casefold(value) {
        return Array.from(String(value || ''), char => global.TweetNookDemoUnicode.casefolds[char] ?? char).join('');
    }

    function asciiLower(value) {
        return String(value || '').replace(/[A-Z]/g, char => char.toLowerCase());
    }

    function like(value, pattern) {
        const escaped = pattern.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replaceAll('%', '.*').replaceAll('_', '.');
        return new RegExp(escaped, 'su').test(asciiLower(value));
    }

    function boolValue(value) {
        return Boolean(value && value !== '0' && value !== 'false');
    }

    function matchesFilter(tweet, key, rawValue) {
        const value = String(rawValue || '').toLowerCase();
        const legacy = tweet.raw_json?.legacy || {};
        const engagement = tweet._demo?.engagement || {};
        if (key === 'from') return asciiLower(tweet.author?.username) === value.replaceAll('@', '');
        if (key === 'to') return asciiLower(legacy.in_reply_to_screen_name) === value.replaceAll('@', '');
        if (key === 'mentions') return (tweet._demo?.mentions || []).some(item => asciiLower(item) === value.replaceAll('@', ''));
        if (key === 'since' || key === 'until') {
            const boundary = dateBoundary(rawValue);
            const created = fixtureDate(tweet.created_at);
            if (boundary === null || created === null) return false;
            return key === 'since' ? created >= boundary : created < boundary;
        }
        if (key === 'since_time' || key === 'until_time') {
            const boundary = Number(numericValue(rawValue));
            const created = fixtureDate(tweet.created_at);
            if (!Number.isFinite(boundary) || created === null) return false;
            return key === 'since_time' ? created >= boundary : created < boundary;
        }
        if (key === 'since_id' || key === 'max_id') {
            try {
                const id = BigInt(tweet.tweet_id);
                const boundary = BigInt(numericValue(rawValue));
                return key === 'since_id' ? id > boundary : id <= boundary;
            } catch (_) {
                return false;
            }
        }
        if (key === 'has') {
            if (value === 'note') return [tweet, tweet.quoted_tweet].some(post =>
                post?.note_history || Boolean(post?.raw_json?.birdwatch_pivot?.subtitle?.text));
            if (value === 'article') return Boolean(tweet.article);
            if (value === 'media') return tweet.media?.some(item => item.source !== 'link_card') || false;
            if (value === 'image') return tweet.media?.some(item => item.type === 'photo' && item.source !== 'link_card') || false;
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
            if (normalized === 'media') return tweet.media?.some(item => item.source !== 'link_card') || false;
            if (normalized === 'image') return tweet.media?.some(item => item.type === 'photo' && item.source !== 'link_card') || false;
            if (normalized === 'video') return tweet.media?.some(item => ['video', 'animated_gif'].includes(item.type)) || false;
            if (normalized === 'links') return Boolean(tweet.urls?.length);
            if (normalized === 'reply') return Boolean(legacy.in_reply_to_status_id_str);
            if (normalized === 'quote') return boolValue(legacy.is_quote_status);
            if (normalized === 'retweet') return Boolean(legacy.retweeted_status_id_str);
            if (normalized === 'thread') {
                return Boolean(legacy.in_reply_to_screen_name)
                    && asciiLower(legacy.in_reply_to_screen_name) === asciiLower(tweet.author?.username);
            }
            if (normalized === 'verified') return Boolean(tweet._demo?.verified);
            if (normalized === 'missing') return tweet.enrichment_state === 'terminal_unavailable';
            if (normalized === 'resurrected') return tweet.enrichment_state === 'resurrected';
            if (normalized === 'hidden') return Boolean(tweet.hidden);
            if (normalized === 'uncaptured') return Boolean(tweet.uncaptured);
        }
        if (key === 'min_retweets') return BigInt(engagement.retweets || 0) >= BigInt(numericValue(rawValue));
        if (key === 'min_faves') return BigInt(engagement.likes || 0) >= BigInt(numericValue(rawValue));
        if (key === 'min_replies') return BigInt(engagement.replies || 0) >= BigInt(numericValue(rawValue));
        if (key === 'conversation_id') return String(legacy.conversation_id_str || '') === String(rawValue);
        if (key === 'quoted_tweet_id') return String(legacy.quoted_status_id_str || '') === String(rawValue);
        if (key === 'url') {
            return (tweet.urls || []).some(url =>
                like(asciiLower(url.expanded_url), value)
                || like(asciiLower(url.display_url), value));
        }
        if (key === 'source') return like(tweet.raw_json?.source, value.replaceAll('_', ' '));
        if (key === 'card_name') return String(tweet._demo?.card_name || '') === value;
        if (key === 'tag') return [tweet.media_tags, tweet.qt_media_tags].some(metadata =>
            (metadata?.tags || []).some(tag => casefold(tag.trim()) === casefold(value.trim())));
        if (key === 'description') {
            return [tweet.media_tags, tweet.qt_media_tags].some(metadata => {
                const columns = [normalizedWords(metadata?.description)];
                return value.split(/\s+/).filter(term => term.replace(/\*+$/, '')).every(term => {
                    const spec = {words: normalizedWords(term.replace(/\*+$/, '')), prefix: term.endsWith('*')};
                    return phraseFrequency(columns, spec) > 0;
                });
            });
        }
        if (key === 'hashtag') return (tweet._demo?.hashtags || []).some(tag =>
            casefold(tag) === casefold(value.replaceAll('#', '')));
        return false;
    }

    function matchesQuery(tweet, groups) {
        return groups.every(group => group.some(token => {
            const matched = token.kind === 'filter'
                ? matchesFilter(tweet, token.key, token.value) : matchesText(tweet, token);
            return token.negated ? !matched : matched;
        }));
    }

    function relevanceScores(corpus, groups) {
        // SQLite indexes one document per membership, including hidden entries;
        // filtering before computing corpus statistics changes BM25 result order.
        const documents = corpus.filter(tweet => tweet.collections.length).map(tweet => ({
            tweet, columns: textColumns(tweet), weight: tweet.collections.length,
        }));
        const size = documents.reduce((sum, doc) => sum + doc.weight, 0);
        const average = documents.reduce((sum, doc) => sum + doc.weight * doc.columns.flat().length, 0) / (size || 1);
        const scores = new Map(corpus.map(tweet => [tweet.tweet_id, 0]));
        for (const group of groups) {
            const alternatives = new Map();
            for (const token of group.filter(token => token.kind === 'text' && !token.negated)) {
                const spec = phraseSpec(token.value);
                const frequencies = documents.map(doc => phraseFrequency(doc.columns, spec));
                const hits = frequencies.reduce((sum, frequency, i) => sum + (frequency ? documents[i].weight : 0), 0);
                const log = Math.log((size - hits + 0.5) / (hits + 0.5));
                const idf = log <= 0 ? 0.000001 : log;
                documents.forEach((doc, i) => {
                    const frequency = frequencies[i];
                    const score = frequency ? idf * frequency * 2.2
                        / (frequency + 1.2 * (0.25 + 0.75 * doc.columns.flat().length / average)) : 0;
                    alternatives.set(doc.tweet.tweet_id, Math.max(alternatives.get(doc.tweet.tweet_id) || 0, score));
                });
            }
            for (const [id, score] of alternatives) scores.set(id, scores.get(id) + score);
        }
        return scores;
    }

    function compare(left, right) { return left < right ? -1 : left > right ? 1 : 0; }
    function timelineKey(tweet) { return BigInt(tweet.collection?.sort_index || tweet.tweet_id); }

    function chronological(left, right, sort) {
        const a = fixtureDate(left.created_at), b = fixtureDate(right.created_at);
        if (a === null || b === null) {
            if (a !== b) return a === null ? 1 : -1;
        }
        return (sort === 'oldest' ? 1 : -1) * (compare(a, b)
            || compare(timelineKey(left), timelineKey(right)) || compare(left.tweet_id, right.tweet_id));
    }

    function effectiveSort(sort, groups) {
        if (['liked_latest', 'liked_earliest', 'oldest', 'random', 'most_likes'].includes(sort)) return sort;
        const positiveText = groups.flat().some(token => token.kind === 'text' && !token.negated);
        return ['default', 'relevance'].includes(sort) && positiveText ? 'relevance' : 'newest';
    }

    function likePositions(corpus) {
        const ordered = corpus.filter(tweet => tweet.collections.includes('like')).sort((a, b) =>
            compare(BigInt(b._demo.liked_order || 0), BigInt(a._demo.liked_order || 0))
            || compare(b.tweet_id, a.tweet_id));
        return new Map(ordered.map((tweet, index) => [tweet.tweet_id, index]));
    }

    function sortedTweets(tweets, sort, groups, randomSeed = 0, corpus = tweets) {
        const mode = effectiveSort(sort, groups);
        const copy = [...tweets];
        if (mode === 'random') {
            const keys = new Map(copy.map(tweet => [tweet.tweet_id, randomKey(tweet.tweet_id, randomSeed)]));
            return copy.sort((a, b) => compare(keys.get(a.tweet_id), keys.get(b.tweet_id)) || compare(a.tweet_id, b.tweet_id));
        }
        if (mode === 'liked_latest' || mode === 'liked_earliest') {
            const positions = likePositions(corpus);
            return copy.sort((a, b) => (mode === 'liked_latest' ? 1 : -1)
                * compare(positions.get(a.tweet_id), positions.get(b.tweet_id)));
        }
        if (mode === 'most_likes') {
            return copy.sort((a, b) => compare(BigInt(b._demo.engagement.likes || 0), BigInt(a._demo.engagement.likes || 0))
                || chronological(a, b, 'newest'));
        }
        if (mode === 'relevance') {
            const scores = relevanceScores(corpus, groups);
            return copy.sort((a, b) => compare(scores.get(b.tweet_id), scores.get(a.tweet_id)) || compare(b.tweet_id, a.tweet_id));
        }
        return copy.sort((a, b) => chronological(a, b, mode));
    }

    // BLAKE2b with an eight-byte digest, matching tweetnook_random_key in SQLite.
    function randomKey(tweetId, seed) {
        const iv = [0x6a09e667f3bcc908n, 0xbb67ae8584caa73bn, 0x3c6ef372fe94f82bn, 0xa54ff53a5f1d36f1n,
            0x510e527fade682d1n, 0x9b05688c2b3e6c1fn, 0x1f83d9abfb41bd6bn, 0x5be0cd19137e2179n];
        const sigma = [
            [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15], [14,10,4,8,9,15,13,6,1,12,0,2,11,7,5,3],
            [11,8,12,0,5,2,15,13,10,14,3,6,7,1,9,4], [7,9,3,1,13,12,11,14,2,6,5,10,4,0,15,8],
            [9,0,5,7,2,4,10,15,14,1,11,12,6,8,3,13], [2,12,6,10,0,11,8,3,4,13,7,5,15,14,1,9],
            [12,5,1,15,14,13,4,10,0,7,6,3,9,2,8,11], [13,11,7,14,12,1,3,9,5,0,15,4,8,6,2,10],
            [6,15,14,9,11,3,0,8,12,2,13,7,1,4,10,5], [10,2,8,4,7,6,1,5,15,11,9,14,3,12,13,0],
        ];
        const mask = (1n << 64n) - 1n;
        const rotate = (x, n) => ((x >> n) | (x << (64n - n))) & mask;
        const bytes = Array.from(`${Number(seed)}:${tweetId}`, char => char.charCodeAt(0));
        const h = [...iv];
        h[0] ^= 0x01010008n;
        for (let offset = 0; offset < bytes.length; offset += 128) {
            const block = bytes.slice(offset, offset + 128);
            const m = Array.from({length: 16}, (_, i) => {
                let word = 0n;
                for (let j = 0; j < 8; j++) word |= BigInt(block[i * 8 + j] || 0) << BigInt(j * 8);
                return word;
            });
            const v = [...h, ...iv];
            v[12] ^= BigInt(offset + block.length);
            if (offset + 128 >= bytes.length) v[14] ^= mask;
            const mix = (a, b, c, d, x, y) => {
                v[a] = (v[a] + v[b] + x) & mask; v[d] = rotate(v[d] ^ v[a], 32n);
                v[c] = (v[c] + v[d]) & mask; v[b] = rotate(v[b] ^ v[c], 24n);
                v[a] = (v[a] + v[b] + y) & mask; v[d] = rotate(v[d] ^ v[a], 16n);
                v[c] = (v[c] + v[d]) & mask; v[b] = rotate(v[b] ^ v[c], 63n);
            };
            for (let round = 0; round < 12; round++) {
                const s = sigma[round % 10];
                mix(0,4,8,12,m[s[0]],m[s[1]]); mix(1,5,9,13,m[s[2]],m[s[3]]);
                mix(2,6,10,14,m[s[4]],m[s[5]]); mix(3,7,11,15,m[s[6]],m[s[7]]);
                mix(0,5,10,15,m[s[8]],m[s[9]]); mix(1,6,11,12,m[s[10]],m[s[11]]);
                mix(2,7,8,13,m[s[12]],m[s[13]]); mix(3,4,9,14,m[s[14]],m[s[15]]);
            }
            for (let i = 0; i < 8; i++) h[i] ^= v[i] ^ v[i + 8];
        }
        let digest = 0n;
        for (let i = 0; i < 8; i++) digest = (digest << 8n) | ((h[0] >> BigInt(i * 8)) & 255n);
        return digest & ((1n << 63n) - 1n);
    }

    function fingerprint(value) {
        let hash = 14695981039346656037n;
        for (const char of value) hash = BigInt.asUintN(64, (hash ^ BigInt(char.codePointAt(0))) * 1099511628211n);
        return hash.toString(16);
    }

    function cursorContext(groups, collection, sort, limit, seed) {
        return fingerprint(JSON.stringify([groups, collection, sort, limit, seed]));
    }

    function readCursor(token, context, mode) {
        try {
            if (token.length > 4096) throw new Error();
            const value = JSON.parse(decodeURIComponent(token));
            if (value?.v !== 1 || value.context !== context || value.mode !== mode
                || !Number.isInteger(value.page) || value.page < 1 || value.page >= 2 ** 31
                || !Number.isInteger(value.offset) || value.offset < 0 || value.offset >= 2 ** 31
                || !value.after || typeof value.after !== 'object' || Array.isArray(value.after)) throw new Error();
            if (mode !== 'offset' && (typeof value.after.id !== 'string' || !value.after.id
                || value.after.id.length > 256 || !Number.isSafeInteger(value.after.position)
                || value.after.position < 0)) throw new Error();
            if (mode === 'feed' && (value.after.timestamp !== null && !Number.isFinite(value.after.timestamp)
                || value.after.timestamp !== null && Math.abs(value.after.timestamp) > 8640000000000
                || typeof value.after.index !== 'string' || !integerPattern.test(value.after.index))) throw new Error();
            if (mode === 'like' && typeof value.after.revision !== 'string') throw new Error();
            return value;
        } catch (_) { throw new SearchError('Invalid pagination cursor for this search.'); }
    }

    function search(tweets, params, countOnly = false) {
        const groups = parseQuery(params.get('q') || '');
        const aliases = {like: 'like', likes: 'like', bookmark: 'bookmark', bookmarks: 'bookmark', tweet: 'tweet', tweets: 'tweet'};
        const collection = aliases[(params.get('collection') || 'all').trim().toLowerCase()] || null;
        const names = value => groups.flat().some(token => token.key === 'is' && token.value === value);
        let matches = tweets.filter(tweet => (collection ? tweet.collections.includes(collection) : tweet.collections.length)
            && (names('hidden') || !tweet.hidden) && (names('uncaptured') || !tweet.uncaptured)
            && matchesQuery(tweet, groups));
        if (countOnly) return {total: matches.length};
        const numberParam = (key, fallback, min, max) => {
            const raw = params.get(key);
            const value = raw === null ? fallback : Number(raw);
            if (raw !== null && !/^[+-]?\d+$/.test(raw) || !Number.isSafeInteger(value) || value < min || value > max) {
                throw new SearchError(`Invalid ${key}.`, 422);
            }
            return value;
        };
        let page = numberParam('page', 1, 1, Number.MAX_SAFE_INTEGER);
        const limit = numberParam('limit', 20, 1, 100);
        const seed = numberParam('random_seed', 0, 0, 2147483647);
        const sort = effectiveSort(params.get('sort') || 'default', groups);
        if (sort.startsWith('liked_') && collection !== 'like') {
            throw new SearchError('Like order is available only for the Likes collection.');
        }
        matches = sortedTweets(matches, sort, groups, seed, tweets);
        const hasText = groups.flat().some(token => token.kind === 'text');
        const total = hasText ? null : matches.length;
        const mode = sort.startsWith('liked_') ? 'like' : !groups.length && ['newest', 'oldest'].includes(sort) ? 'feed' : 'offset';
        const context = cursorContext(groups, collection, sort, limit, params.has('random_seed') ? seed : null);
        const revision = fingerprint(JSON.stringify(tweets.filter(tweet => tweet.collections.includes('like'))
            .map(tweet => [tweet.tweet_id, tweet._demo.liked_order]).sort()));
        const positions = mode === 'like' ? likePositions(tweets) : null;
        let start = (page - 1) * limit;
        if (params.has('cursor')) {
            page = 1; start = 0;
            if (params.get('cursor')) {
                const cursor = readCursor(params.get('cursor'), context, mode);
                page = cursor.page;
                start = cursor.offset;
                if (mode === 'like') {
                    if (cursor.after.revision !== revision) throw new SearchError('Like order changed. Reload the list to continue.', 409);
                    matches = matches.filter(tweet => (sort === 'liked_latest' ? 1 : -1)
                        * compare(positions.get(tweet.tweet_id), cursor.after.position) > 0);
                    start = 0;
                } else if (mode === 'feed') {
                    const boundary = {tweet_id: cursor.after.id,
                        created_at: cursor.after.timestamp === null ? null : new Date(cursor.after.timestamp * 1000).toISOString(),
                        collection: {sort_index: cursor.after.index}};
                    matches = matches.filter(tweet => chronological(tweet, boundary, sort) > 0);
                    start = 0;
                }
            }
        }
        const rows = matches.slice(start, start + limit);
        const hasMore = start + limit < matches.length;
        let nextCursor = null;
        if (params.has('cursor') && hasMore && rows.length) {
            const last = rows.at(-1);
            const after = mode === 'offset' ? {} : {id: last.tweet_id, position: positions?.get(last.tweet_id) || 0};
            if (mode === 'like') after.revision = revision;
            if (mode === 'feed') Object.assign(after, {timestamp: fixtureDate(last.created_at), index: String(timelineKey(last))});
            nextCursor = encodeURIComponent(JSON.stringify({v: 1, context, mode, after, offset: start + limit, page: page + 1}));
        }
        return {tweets: rows, total, page, pages: total === null ? null : Math.max(1, Math.ceil(total / limit)),
            has_more: hasMore, next_cursor: nextCursor, truncated: false,
            highlight_terms: [...new Set(groups.flat().filter(token => !token.negated
                && (token.kind === 'text' || token.key === 'hashtag'))
                .map(token => token.kind === 'text' ? token.value : `#${token.value}`))]};
    }

    global.TweetNookDemoSearch = {parseQuery, searchableText, matchesQuery, search, randomKey};
})(window);
