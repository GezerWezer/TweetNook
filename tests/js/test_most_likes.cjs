'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../..');

async function main() {
    const scripts = ['themes.js', 'app.js'].map(file => fs.readFileSync(
        path.join(root, 'tweetnook/web/static/js', file), 'utf8')).join('\n');
    const app = vm.runInNewContext(scripts + '\ntweetApp()', {window: {}});
    for (const collection of ['all', 'likes', 'bookmarks', 'tweets']) {
        for (const query of ['', 'needle']) {
            app.collectionFilter = collection;
            app.searchQuery = query;
            const options = Array.from(app.feedOptions('sort'));
            const random = options.findIndex(option => option.value === 'random');
            assert.equal(options[random + 1].value, 'most_likes');
            assert.equal(options[random + 1].label, 'Most likes');
        }
    }

    const window = {document: {baseURI: 'https://fixture.test/'}, location: {href: 'https://fixture.test/'}};
    vm.runInNewContext(fs.readFileSync(path.join(root, 'demo/demo-api.js'), 'utf8'), {
        window, URL, Request, Response, JSON, Math, Date, Promise, setTimeout, clearTimeout,
    });
    const fixture = JSON.parse(fs.readFileSync(path.join(root, 'tests/fixtures/demo-synthetic.json'), 'utf8'));
    const store = window.TweetNookDemo.createStore(fixture);
    const response = await store.handle('https://fixture.test/api/tweets?sort=most_likes&limit=100');
    assert.equal(response.status, 200);
    const page = await response.json();
    assert.ok(page.tweets.length > 5);
    const compare = (left, right) =>
        Number(right.raw_json?.legacy?.favorite_count || 0) - Number(left.raw_json?.legacy?.favorite_count || 0)
        || Date.parse(right.created_at) - Date.parse(left.created_at)
        || String(right.tweet_id).localeCompare(String(left.tweet_id));
    assert.deepEqual(page.tweets.map(tweet => tweet.tweet_id),
        [...page.tweets].sort(compare).map(tweet => tweet.tweet_id));
    assert.ok(Number(page.tweets[0].raw_json.legacy.favorite_count) > 0);
    console.log('PASS: Most likes follows Random in every feed, and the demo sorts captured likes.');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
