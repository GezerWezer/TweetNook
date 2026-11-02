'use strict';

const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../..');

function demoAPI() {
    const window = {};
    const context = vm.createContext({window, URL, Request, Response, Date, Math, JSON, setTimeout, clearTimeout});
    for (const file of ['jobs-replay.js', 'search-unicode.js', 'search.js', 'demo-api.js']) {
        vm.runInContext(fs.readFileSync(path.join(root, 'demo', file), 'utf8'), context, {filename: file});
    }
    return window.TweetNookDemo;
}

async function run({fixture, requests}) {
    const store = demoAPI().createStore(fixture);
    const results = [];
    for (const params of requests) {
        const response = await store.handle('https://demo.test/api/tweets?' + new URLSearchParams(params));
        const body = await response.json();
        const count = await store.handle('https://demo.test/api/tweets/count?' + new URLSearchParams(params));
        results.push({status: response.status, body: {...body, tweets: body.tweets?.map(tweet => tweet.tweet_id)},
            count_status: count.status, count: await count.json()});
    }
    return results;
}

module.exports = {demoAPI, run};
if (require.main === module) {
    run(JSON.parse(fs.readFileSync(0, 'utf8'))).then(result => process.stdout.write(JSON.stringify(result)))
        .catch(error => { console.error(error); process.exitCode = 1; });
}
