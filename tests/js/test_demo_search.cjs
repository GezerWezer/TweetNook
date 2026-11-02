'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {demoAPI} = require('./demo_search_runner.cjs');
const root = path.resolve(__dirname, '../..');
const fixture = JSON.parse(fs.readFileSync(path.join(root, 'tests/fixtures/demo-synthetic.json'), 'utf8'));
for (const tweet of fixture.tweets.slice(0, 7)) tweet.created_at = null;
fixture.tweets[1].liked_order = fixture.tweets[0].liked_order;

async function request(store, params) {
    const response = await store.handle('https://demo.test/api/tweets?' + new URLSearchParams(params));
    return {status: response.status, body: await response.json()};
}

async function main() {
    const api = demoAPI();
    const store = api.createStore(fixture);
    for (const params of [
        {sort: 'newest'}, {sort: 'oldest'}, {sort: 'random', random_seed: '42'},
        {sort: 'most_likes'}, {q: 'has:media'}, {q: 'is:quote OR has:media'},
        {q: 'media OR quote', sort: 'relevance'}, {sort: 'liked_latest', collection: 'likes'},
        {sort: 'liked_earliest', collection: 'like'},
    ]) {
        const all = await request(store, {...params, limit: '100'});
        const expected = all.body.tweets.map(tweet => tweet.tweet_id);
        const visited = [];
        let cursor = '', page = 1;
        do {
            const result = await request(store, {...params, cursor, limit: '3', page: '987'});
            assert.equal(result.status, 200, JSON.stringify(params));
            assert.equal(result.body.page, page++);
            visited.push(...result.body.tweets.map(tweet => tweet.tweet_id));
            assert.equal(result.body.has_more, result.body.next_cursor !== null);
            cursor = result.body.next_cursor;
            assert.ok(page < 100, 'cursor traversal terminates');
        } while (cursor !== null);
        assert.deepEqual(visited, expected, JSON.stringify(params));
        assert.equal(new Set(visited).size, visited.length);
        const offset = await request(store, {...params, page: '2', limit: '3'});
        assert.deepEqual(offset.body.tweets.map(tweet => tweet.tweet_id), expected.slice(3, 6));
        assert.equal(offset.body.next_cursor, null);
    }

    const first = await request(store, {cursor: '', limit: '3'});
    assert.ok(first.body.next_cursor);
    for (const params of [{q: 'cats'}, {sort: 'oldest'}, {collection: 'bookmarks'}, {limit: '4'}, {random_seed: '2'}]) {
        assert.equal((await request(store, {limit: '3', cursor: first.body.next_cursor, ...params})).status, 400);
    }
    for (const cursor of ['garbage', '%', 'null', '%7B%7D', 'x'.repeat(4097)]) {
        assert.equal((await request(store, {cursor, limit: '3'})).status, 400);
    }
    const malformed = JSON.parse(decodeURIComponent(first.body.next_cursor));
    for (const change of [{page: 0}, {offset: -1}, {after: null}, {after: {}}, {v: 2}]) {
        assert.equal((await request(store, {limit: '3', cursor: encodeURIComponent(JSON.stringify({...malformed, ...change}))})).status, 400);
    }
    for (const params of [
        {page: '0'}, {page: 'nope'}, {limit: '0'}, {limit: '101'}, {limit: '1.5'},
        {random_seed: '-1'}, {random_seed: '2147483648'},
    ]) assert.equal((await request(store, params)).status, 422);
    for (const collection of ['all', 'bookmarks', 'tweets']) {
        assert.equal((await request(store, {collection, sort: 'liked_latest'})).status, 400);
    }

    // A feed cursor continues from its keys even when its boundary row is hidden.
    const initial = await request(store, {sort: 'newest', cursor: '', limit: '3'});
    const boundary = initial.body.tweets.at(-1).tweet_id;
    await store.handle(`https://demo.test/api/tweets/${boundary}/hide`, {method: 'POST'});
    const continuation = await request(store, {sort: 'newest', cursor: initial.body.next_cursor, limit: '3'});
    const original = await request(api.createStore(fixture), {sort: 'newest', limit: '100'});
    assert.deepEqual(continuation.body.tweets.map(tweet => tweet.tweet_id), original.body.tweets.slice(3, 6).map(tweet => tweet.tweet_id));

    const likes = await request(store, {collection: 'likes', sort: 'liked_latest', limit: '3', cursor: ''});
    const changed = structuredClone(fixture);
    changed.tweets.find(tweet => tweet.collections.includes('like')).liked_order += 1000;
    const expired = await request(api.createStore(changed), {
        collection: 'likes', sort: 'liked_latest', limit: '3', cursor: likes.body.next_cursor,
    });
    assert.equal(expired.status, 409);

    const highlights = await request(store, {q: 'gif gif OR "night sky" #python -dogs NOT "bad phrase"'});
    assert.deepEqual(highlights.body.highlight_terms, ['gif', '"night sky"', '#python']);
    console.log('demo search cursor and validation tests passed');
}
main().catch(error => {console.error(error); process.exitCode = 1;});
